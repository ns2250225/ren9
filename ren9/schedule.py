"""赛程获取与校验。

传统足彩任九：只接受体彩网官方 14 场赛单或用户手动提供的赛单；
任何情况下都不会用竞彩比赛或其他联赛的比赛去补齐 14 场。
"""
from __future__ import annotations

import csv
import io
import json
import re
from datetime import date
from pathlib import Path

from ren9 import store
from ren9.common import now, now_iso, parse_dt, settings
from providers import sporttery
from providers.base import ProviderError

MANUAL_HINT = (
    "未能获取官方 14 场赛单。请手动提供本期 14 场比赛后重试："
    "python scripts/fetch_schedule.py --manual <文件>（JSON 列表，或每行"
    "『序号,主队,客队,赛事,比赛日期』的 CSV/文本），并用 --period 指定期号。"
)


# ---------- 校验 ----------

def validate_schedule(matches: list[dict], expected: int | None = 14) -> list[dict]:
    """返回问题列表；level=error 的问题会使赛单被判定为不完整。"""
    issues: list[dict] = []

    def err(code: str, msg: str, seqs: list | None = None) -> None:
        issues.append({"level": "error", "code": code, "message": msg, "seqs": seqs or []})

    if expected is not None and len(matches) != expected:
        err("count", f"比赛数量为 {len(matches)}，应为 {expected}")
    seqs = [m.get("seq") for m in matches]
    dup = sorted({s for s in seqs if seqs.count(s) > 1}, key=lambda x: (x is None, x))
    if dup:
        err("duplicate_seq", f"比赛编号重复: {dup}", dup)
    if expected is not None:
        missing = [i for i in range(1, expected + 1) if i not in seqs]
        if missing:
            err("missing_seq", f"缺少比赛编号: {missing}", missing)
    seen_fix: dict[tuple, int] = {}
    seen_team: dict[str, int] = {}
    for m in matches:
        s = m.get("seq")
        if not m.get("home_team") or not m.get("away_team"):
            err("team_missing", f"第 {s} 场球队信息不完整", [s])
            continue
        if m["home_team"] == m["away_team"]:
            err("same_team", f"第 {s} 场主客队相同", [s])
        key = (m["home_team"], m["away_team"])
        if key in seen_fix:
            err("duplicate_fixture", f"第 {seen_fix[key]} 场与第 {s} 场对阵重复", [seen_fix[key], s])
        seen_fix[key] = s
        for t in key:
            if t in seen_team and seen_team[t] != s:
                issues.append({"level": "warning", "code": "team_twice",
                               "message": f"{t} 同时出现在第 {seen_team[t]} 场和第 {s} 场", "seqs": [seen_team[t], s]})
            seen_team[t] = s
        if not m.get("competition"):
            issues.append({"level": "warning", "code": "competition_missing", "message": f"第 {s} 场缺少赛事名称", "seqs": [s]})
        if not m.get("date"):
            issues.append({"level": "warning", "code": "date_missing", "message": f"第 {s} 场缺少比赛日期", "seqs": [s]})
        if m.get("status") in ("cancelled", "postponed"):
            issues.append({"level": "warning", "code": "not_playable", "message": f"第 {s} 场状态为 {m['status']}", "seqs": [s]})
    return issues


def diff_schedule(old: list[dict], new: list[dict]) -> list[dict]:
    """与上一次保存的同期赛单比较，找出变更。"""
    changes = []
    prev = {m.get("seq"): m for m in old}
    for m in new:
        o = prev.get(m.get("seq"))
        if o is None:
            changes.append({"seq": m.get("seq"), "field": "match", "old": None, "new": f"{m['home_team']} vs {m['away_team']}"})
            continue
        for f in ("home_team", "away_team", "competition", "date", "kickoff", "status"):
            if o.get(f) != m.get(f) and not (f == "kickoff" and m.get(f) is None):
                changes.append({"seq": m.get("seq"), "field": f, "old": o.get(f), "new": m.get(f)})
    for s in set(prev) - {m.get("seq") for m in new}:
        changes.append({"seq": s, "field": "match", "old": f"{prev[s]['home_team']} vs {prev[s]['away_team']}", "new": None})
    return changes


# ---------- 手动赛单 ----------

def parse_manual(text: str, period: str) -> list[dict]:
    """解析用户提供的赛单。支持 JSON（列表或 {matches: [...]})、CSV、"主队 vs 客队" 文本。"""
    text = text.strip().lstrip("﻿")
    rows: list[dict] = []
    if text.startswith("[") or text.startswith("{"):
        data = json.loads(text)
        items = data.get("matches") if isinstance(data, dict) else data
        for i, it in enumerate(items or [], 1):
            rows.append(
                {
                    "seq": int(it.get("seq") or it.get("matchNum") or i),
                    "home_team": str(it.get("home_team") or it.get("home") or it.get("主队") or "").strip(),
                    "away_team": str(it.get("away_team") or it.get("away") or it.get("客队") or "").strip(),
                    "competition": str(it.get("competition") or it.get("league") or it.get("赛事") or "").strip(),
                    "date": (str(it.get("date") or it.get("kickoff") or it.get("日期") or "")[:10]) or None,
                    "kickoff": it.get("kickoff") or None,
                }
            )
    else:
        lines = [ln for ln in text.splitlines() if ln.strip() and not ln.strip().startswith("#")]
        auto = 0
        for ln in lines:
            parts = [p.strip() for p in next(csv.reader(io.StringIO(ln)))]
            if len(parts) == 1:
                parts = [p.strip() for p in re.split(r"[\t|]", ln)]
            if any(h in parts[0] for h in ("序号", "seq", "场次")):
                continue
            seq = None
            if parts and re.fullmatch(r"\d{1,2}", parts[0]):
                seq = int(parts.pop(0))
            if parts and re.search(r"\s+(vs|VS|v|对)\s+|(?<=\S)(VS|vs)(?=\S)", parts[0]):
                h, a = re.split(r"\s*(?:vs|VS|对)\s*|\s+v\s+", parts[0], maxsplit=1)
                rest = parts[1:]
            elif len(parts) >= 2:
                h, a, rest = parts[0], parts[1], parts[2:]
            else:
                continue
            auto += 1
            comp = rest[0] if rest else ""
            dt = rest[1] if len(rest) > 1 else ""
            rows.append(
                {
                    "seq": seq or auto,
                    "home_team": h.strip(),
                    "away_team": a.strip(),
                    "competition": comp,
                    "date": dt[:10] or None,
                    "kickoff": dt if len(dt) > 10 else None,
                }
            )
    src = {"name": "用户手动提供", "url": None, "fetched_at": now_iso(), "from_cache": False, "stale": False}
    out = []
    for r in rows:
        out.append(
            {
                "match_id": f"ren9_{period}_{r['seq']:02d}",
                "period": period,
                "seq": r["seq"],
                "home_team": "".join(r["home_team"].split()),
                "away_team": "".join(r["away_team"].split()),
                "home_short": None,
                "away_short": None,
                "competition": r["competition"],
                "date": r["date"],
                "kickoff": r["kickoff"],
                "status": "scheduled",
                "official_ref_odds": None,
                "jc_match_id": None,
                "result": None,
                "score": None,
                "sources": [src],
            }
        )
    return out


# ---------- 传统足彩 ----------

def _in_window(s: dict, day: str) -> bool:
    b, e = (s.get("sale_begin") or "")[:10], (s.get("sale_end") or "")[:10]
    return bool(b and e and b <= day <= e)


def _pick_period(day: str, refresh: bool) -> tuple[dict | None, list[dict], list[str]]:
    """按日期选期：在售窗口包含该日期且最先截止的一期。返回 (选中期, 其他同日在售期, 降级记录)。"""
    notes: list[str] = []
    try:
        current = sporttery.fetch_ren9_schedule(None, refresh)
    except ProviderError as exc:
        notes.append(f"官方赛单接口失败: {exc}")
        current = None
    cands = [current] if current else []
    for p in (current or {}).get("upcoming_periods", []):
        if current and p == current["period"]:
            continue
        try:
            cands.append(sporttery.fetch_ren9_schedule(p, refresh))
        except ProviderError as exc:
            notes.append(f"第 {p} 期赛单暂不可用: {exc}")
    if not cands:
        # 备用官方接口：用最新开奖期号推算下一期，再按期号查询赛单
        try:
            from providers.sporttery import fetch_ren9_history

            last = fetch_ren9_history(1, 1)
            if last:
                nxt = str(int(last[0]["period"]) + 1)
                cands.append(sporttery.fetch_ren9_schedule(nxt, refresh))
                notes.append(f"主接口不可用，已通过备用官方接口按期号 {nxt} 获取")
        except (ProviderError, ValueError) as exc:
            notes.append(f"备用官方接口失败: {exc}")
    open_ = sorted((c for c in cands if _in_window(c, day)), key=lambda c: c.get("sale_end") or "")
    if open_:
        return open_[0], open_[1:], notes
    return None, [], notes


def _attach_jc_kickoff(matches: list[dict], notes: list[str]) -> None:
    """用竞彩官方数据交叉补充开球时间（仅匹配同一场比赛，不新增比赛）。"""
    try:
        jc = sporttery.fetch_jczq_matches()
    except ProviderError as exc:
        notes.append(f"竞彩接口不可用，开球时间待 ESPN 补充: {exc}")
        return
    by_id = {m["jc_match_id"]: m for m in jc}
    by_names = {(m["home_team"], m["away_team"]): m for m in jc}
    for m in matches:
        j = by_id.get(m.get("jc_match_id")) or by_names.get((m["home_team"], m["away_team"]))
        if j and j.get("kickoff"):
            m["kickoff"] = j["kickoff"]
            m["jc_match_id"] = j["jc_match_id"]
            m["sources"].append({**j["sources"][0], "field": "kickoff"})


def get_ren9_schedule(day: str, period: str | None = None, manual: str | None = None,
                      refresh: bool = False) -> dict:
    cfg = settings()
    expected = cfg["lottery"]["expected_matches"]
    notes: list[str] = []
    sched: dict | None = None
    others: list[dict] = []
    explicit_period = bool(period)

    if manual:
        if not cfg["data"].get("allow_manual_schedule", True):
            raise ValueError("配置禁止使用手动赛单 (data.allow_manual_schedule=false)")
        text = Path(manual).read_text(encoding="utf-8") if Path(manual).exists() else manual
        period = period or f"manual-{day}"
        sched = {
            "period": period, "game_type": "ren9", "game_name": "任选9场（手动赛单）",
            "sale_begin": None, "sale_end": None, "draw_time": None, "upcoming_periods": [],
            "matches": parse_manual(text, period),
            "source": [{"name": "用户手动提供", "url": None, "fetched_at": now_iso()}],
        }
        notes.append("赛单由用户手动提供，未经官方接口验证")
    elif period:
        try:
            sched = sporttery.fetch_ren9_schedule(period, refresh)
        except ProviderError as exc:
            notes.append(f"官方接口未返回第 {period} 期在售赛单: {exc}")
            try:   # 已开奖的往期：从开奖接口还原赛单
                draw = sporttery.fetch_ren9_draw(period)
                if draw["results"]:
                    sched = {
                        "period": period, "game_type": "ren9", "game_name": "任选9场（往期）",
                        "sale_begin": None, "sale_end": None, "draw_time": draw.get("draw_date"),
                        "upcoming_periods": [],
                        "matches": [
                            {
                                "match_id": f"ren9_{period}_{r['seq']:02d}", "period": period, "seq": r["seq"],
                                "home_team": r["home_team"], "away_team": r["away_team"], "home_short": None,
                                "away_short": None, "competition": r["competition"], "date": None, "kickoff": None,
                                "status": "finished" if r["result"] else "scheduled", "official_ref_odds": None,
                                "jc_match_id": None, "result": r["result"], "score": r["score"],
                                "sources": draw["source"],
                            }
                            for r in draw["results"]
                        ],
                        "source": draw["source"],
                    }
                    notes.append("该期已停售，赛单取自官方开奖接口")
            except ProviderError as exc2:
                notes.append(f"官方开奖接口失败: {exc2}")
    else:
        sched, others, n2 = _pick_period(day, refresh)
        notes.extend(n2)
        if sched is None:
            known = store.recall_period("ren9", day)
            cached = store.load_stage("ren9", known, "schedule") if known else None
            if cached and cached.get("complete"):
                # 只有同一期且尚未截止的缓存才可信
                end = parse_dt(cached.get("sale_end"))
                if end and end >= now():
                    notes.append(f"官方接口不可用，使用本地缓存的第 {known} 期赛单（仍在销售期内）")
                    cached["notes"] = notes
                    cached["from_local_cache"] = True
                    return cached
                notes.append(f"本地缓存的第 {known} 期赛单已过销售截止时间，不再使用")

    if sched is None:
        return {
            "date": day, "game_type": "ren9", "period": None, "expected_matches": expected, "matches": [],
            "complete": False, "source": [], "last_updated": None, "issues": [
                {"level": "error", "code": "no_official_schedule", "message": f"{day} 没有在售的传统足彩期次或官方接口不可用", "seqs": []}
            ],
            "notes": notes, "action_required": MANUAL_HINT,
        }

    if not manual:
        _attach_jc_kickoff(sched["matches"], notes)
    issues = validate_schedule(sched["matches"], expected)
    stale = any(s.get("stale") for s in sched.get("source", []))
    if stale:
        end = parse_dt(sched.get("sale_end"))
        if not end or end < now():
            issues.append({"level": "error", "code": "stale_schedule", "message": "官方接口不可用且缓存赛单已过期", "seqs": []})
        else:
            notes.append("官方接口暂不可用，使用仍在销售期内的缓存赛单")

    prev = store.load_stage("ren9", sched["period"], "schedule")
    changes = diff_schedule(prev["matches"], sched["matches"]) if prev and prev.get("matches") else []
    end = parse_dt(sched.get("sale_end"))
    out = {
        "date": day,
        "game_type": "ren9",
        "period": sched["period"],
        "game_name": sched.get("game_name"),
        "expected_matches": expected,
        "sale_begin": sched.get("sale_begin"),
        "sale_end": sched.get("sale_end"),
        "sale_closed": bool(end and end < now()),
        "draw_time": sched.get("draw_time"),
        "matches": sorted(sched["matches"], key=lambda m: m["seq"]),
        "complete": not any(i["level"] == "error" for i in issues),
        "official": not manual,
        "issues": issues,
        "changed": bool(changes),
        "changes": changes,
        "other_open_periods": [
            {"period": o["period"], "sale_end": o.get("sale_end"), "matches": len(o["matches"])} for o in others
        ],
        "source": sched.get("source", []),
        "last_updated": max((s.get("fetched_at") or "" for s in sched.get("source", [])), default=None) or now_iso(),
        "notes": notes,
    }
    if not out["complete"]:
        out["action_required"] = "赛单未通过校验，已停止生成正式推荐。" + MANUAL_HINT
    store.save_stage("ren9", out["period"], "schedule", out)
    # 日期 → 期号的索引只记录"按日期自动选出的官方期次"或"通过校验的手动赛单"，
    # 显式指定的其他期次、未通过校验的手动赛单都不会改变当天默认分析的期次。
    if (manual and out["complete"]) or (not manual and not explicit_period):
        store.remember_period("ren9", day, out["period"])
    return out


# ---------- 竞彩足球 ----------

def get_jczq_schedule(day: str, refresh: bool = False) -> dict:
    notes: list[str] = []
    try:
        allm = sporttery.fetch_jczq_matches(refresh)
    except ProviderError as exc:
        return {
            "date": day, "game_type": "jczq", "period": day, "expected_matches": None, "matches": [],
            "complete": False, "source": [], "last_updated": None, "notes": [str(exc)],
            "issues": [{"level": "error", "code": "jc_unavailable", "message": f"竞彩官方接口不可用: {exc}", "seqs": []}],
        }
    matches = [m for m in allm if m.get("business_date") == day]
    if not matches and allm:
        notes.append(f"{day} 没有竞彩在售比赛；当前在售业务日期: {sorted({m['business_date'] for m in allm})}")
    for i, m in enumerate(matches, 1):
        m["seq"] = i
        m["period"] = day
    issues = validate_schedule(matches, None)
    if not matches:
        issues.append({"level": "error", "code": "no_matches", "message": f"{day} 没有竞彩在售比赛", "seqs": []})
    out = {
        "date": day, "game_type": "jczq", "period": day, "game_name": "竞彩足球（胜平负 / 让球胜平负）",
        "expected_matches": None, "sale_end": None, "sale_closed": False,
        "matches": matches, "complete": not any(i["level"] == "error" for i in issues), "official": True,
        "issues": issues, "changed": False, "changes": [],
        "source": matches[0]["sources"] if matches else [],
        "last_updated": matches[0]["sources"][0]["fetched_at"] if matches else None,
        "notes": notes,
    }
    store.save_stage("jczq", day, "schedule", out)
    store.remember_period("jczq", day, day)
    return out


def get_schedule(day: str, game_type: str = "ren9", period: str | None = None, manual: str | None = None,
                 refresh: bool = False) -> dict:
    date.fromisoformat(day)
    if game_type == "jczq":
        return get_jczq_schedule(day, refresh)
    if game_type != "ren9":
        raise ValueError(f"不支持的玩法: {game_type}（支持 ren9 / jczq）")
    return get_ren9_schedule(day, period, manual, refresh)
