"""赔率分析：隐含概率、去水概率、赔率波动。

来源分开保存：official（中国竞彩官方）与 commercial（商业机构）不会合并成同一个来源；
市场概率只是集成模型的输入之一，不会被直接当作最终预测概率。
"""
from __future__ import annotations

from ren9 import store
from ren9.common import data_sources, now_iso, rnd
from providers import sporttery
from providers.base import ProviderError

KEYS = ("home", "draw", "away")


def implied(odds: dict) -> dict:
    """隐含概率 P = 1 / O。"""
    return {k: 1.0 / odds[k] for k in KEYS}


def analyze(odds: dict) -> dict:
    """由一组胜平负赔率计算隐含概率、水位（overround）与去水概率。"""
    for k in KEYS:
        if not odds.get(k) or odds[k] <= 1.0:
            raise ValueError(f"无效赔率: {odds}")
    imp = implied(odds)
    total = sum(imp.values())
    return {
        "odds": {k: odds[k] for k in KEYS},
        "implied_probability": {k: rnd(imp[k]) for k in KEYS},
        "overround": rnd(total - 1.0),
        "fair_probability": {k: rnd(imp[k] / total) for k in KEYS},
    }


def analyze_two_way(a: float, b: float) -> dict:
    ia, ib = 1.0 / a, 1.0 / b
    return {"overround": rnd(ia + ib - 1.0), "fair": [rnd(ia / (ia + ib)), rnd(ib / (ia + ib))]}


def _source_meta(key: str) -> dict:
    meta = data_sources().get("odds_sources", {}).get(key)
    if meta:
        return meta
    if key.startswith("espn_"):
        return {"kind": "commercial", "label": f"{key[5:]}（经 ESPN）"}
    return {"kind": "commercial", "label": key}


def _entry(key: str, odds: dict, fetched_at: str | None, updated_at: str | None = None,
           extra: dict | None = None) -> dict:
    meta = _source_meta(key)
    e = {"source": key, "kind": meta["kind"], "label": meta["label"], **analyze(odds),
         "fetched_at": fetched_at, "updated_at": updated_at}
    if extra:
        e.update(extra)
    return e


def _movement(match_id: str, sources: list[dict]) -> dict:
    """赔率波动：各来源的最早快照/开盘 → 当前，取去水概率的最大变化。"""
    details, max_change = [], None
    for s in sources:
        first, basis = None, None
        if s.get("open"):
            first, basis = analyze(s["open"])["fair_probability"], "开盘→当前"
        else:
            hist = store.odds_history(match_id, s["source"])
            if len(hist) >= 2:
                first = analyze(hist[0])["fair_probability"]
                basis = f"首次记录({hist[0]['fetched_at']})→当前"
        if first is None:
            continue
        cur = s["fair_probability"]
        change = max(abs(cur[k] - first[k]) for k in KEYS)
        details.append({"source": s["source"], "basis": basis, "max_change": rnd(change), "from": first, "to": cur})
        max_change = change if max_change is None else max(max_change, change)
    return {"max_change": rnd(max_change), "details": details, "known": max_change is not None}


def collect(schedule: dict, team_data: dict | None, refresh: bool = False,
            only_seqs: list[int] | None = None, previous: dict | None = None) -> dict:
    notes: list[str] = []
    jc_by_id, jc_by_names = {}, {}
    try:
        for j in sporttery.fetch_jczq_matches(refresh):
            jc_by_id[j["jc_match_id"]] = j
            jc_by_names[(j["home_team"], j["away_team"])] = j
    except ProviderError as exc:
        notes.append(f"竞彩官方赔率接口不可用: {exc}")
    td = {x["match_id"]: x for x in (team_data or {}).get("matches", [])}
    prev = {x["match_id"]: x for x in (previous or {}).get("matches", [])}
    out = []
    for m in schedule.get("matches") or []:
        mid = m["match_id"]
        if only_seqs and m.get("seq") not in only_seqs and mid in prev:
            out.append(prev[mid])
            continue
        sources: list[dict] = []
        handicap = totals = asian = None
        j = jc_by_id.get(m.get("jc_match_id")) or jc_by_names.get((m["home_team"], m["away_team"]))
        if j is None and mid.startswith("jczq_"):
            j = m                                   # 竞彩赛程自身携带官方赔率
        if j:
            fetched = j["sources"][0]["fetched_at"]
            if j.get("had"):
                sources.append(_entry("sporttery_jc_had", j["had"], fetched, j["had"].get("updated_at")))
            if j.get("hhad"):
                o = j["hhad"]
                handicap = {"source": "sporttery_jc_hhad", "kind": "official", "label": "竞彩官方让球胜平负",
                            "line": o.get("handicap"), **analyze(o), "fetched_at": fetched,
                            "updated_at": o.get("updated_at")}
        if not sources and m.get("official_ref_odds"):
            src = (m.get("sources") or [{}])[0]
            sources.append(_entry("sporttery_zc_ref", m["official_ref_odds"], src.get("fetched_at")))
        t = td.get(mid)
        for eo in (t or {}).get("espn_odds") or []:
            key = "espn_" + str(eo["provider"]).lower().replace(" ", "_")
            sources.append(_entry(key, eo["current"], eo.get("fetched_at"), None,
                                  {"open": eo["open"]} if eo.get("open") else None))
            if eo.get("handicap") and asian is None:
                h = eo["handicap"]
                asian = {"source": key, "kind": "commercial", "line": h["line"], "home": h["home"],
                         "away": h["away"], **analyze_two_way(h["home"], h["away"]),
                         "fetched_at": eo.get("fetched_at")}
            if eo.get("totals") and totals is None:
                tt = eo["totals"]
                totals = {"source": key, "kind": "commercial", "line": tt["line"], "over": tt["over"],
                          "under": tt["under"], **analyze_two_way(tt["over"], tt["under"]),
                          "fetched_at": eo.get("fetched_at")}
        for s in sources:
            store.record_odds(mid, s["source"], s["odds"], s.get("fetched_at") or now_iso())
        # 市场共识：各来源去水概率的简单平均（每个来源一票；官方/商业仍分开保存在 sources 里）
        market = None
        if sources:
            fair = {k: sum(s["fair_probability"][k] for s in sources) / len(sources) for k in KEYS}
            tot = sum(fair.values())
            market = {
                "fair_probability": {k: rnd(fair[k] / tot) for k in KEYS},
                "sources_used": [s["source"] for s in sources],
                "official_sources": sum(1 for s in sources if s["kind"] == "official"),
                "commercial_sources": sum(1 for s in sources if s["kind"] == "commercial"),
            }
        out.append(
            {
                "match_id": mid, "seq": m.get("seq"), "home_team": m["home_team"], "away_team": m["away_team"],
                "sources": sources,          # 欧洲赔率（1X2）：竞彩官方与商业机构分来源保存
                "jc_handicap": handicap,     # 中国竞彩让球胜平负
                "asian_handicap": asian,     # 亚洲让球（商业机构）
                "totals": totals,            # 大小球
                "market": market,
                "movement": _movement(mid, sources),
                "available": bool(sources),
            }
        )
    return {
        "date": schedule.get("date"), "game_type": schedule.get("game_type"), "period": schedule.get("period"),
        "generated_at": now_iso(), "matches": out,
        "with_odds": sum(1 for x in out if x["available"]), "total": len(out), "notes": notes,
    }
