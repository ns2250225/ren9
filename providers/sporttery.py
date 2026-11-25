"""中国体彩网（sporttery.cn）适配器。

- 传统足彩（胜负彩 14 场 / 任选 9 场）：期号、14 场赛单、销售截止时间、开奖结果与真实奖金。
- 竞彩足球：比赛列表、胜平负（HAD）与让球胜平负（HHAD）官方赔率。

两种玩法的数据结构互相独立：竞彩比赛列表永远不会被用来拼凑传统足彩 14 场赛单。
"""
from __future__ import annotations

import json

from ren9.common import data_sources, settings, to_float
from providers.base import Fetched, ProviderError, http_get

HEADERS = {"Referer": "https://www.sporttery.cn/", "Origin": "https://www.sporttery.cn"}


def _cfg(key: str) -> dict:
    return data_sources()["sources"][key]


def _get_json(url: str, ttl: float, refresh: bool) -> tuple[dict, Fetched]:
    def ok(text: str) -> bool:
        return text.lstrip().startswith("{")

    res = http_get(url, ttl=ttl, headers=HEADERS, refresh=refresh, validate=ok)
    try:
        payload = json.loads(res.text)
    except json.JSONDecodeError as exc:
        raise ProviderError(f"sporttery 返回非 JSON: {url}") from exc
    if not payload.get("success") or payload.get("value") is None:
        raise ProviderError(f"sporttery 返回错误: {payload.get('errorMessage')} ({url})")
    return payload["value"], res


def _clean(name: str | None) -> str:
    return "".join((name or "").split())


def _ref_odds(m: dict) -> dict | None:
    h, d, a = to_float(m.get("h")), to_float(m.get("d")), to_float(m.get("a"))
    if h and d and a and min(h, d, a) > 1.0:
        return {"home": h, "draw": d, "away": a}
    return None


def parse_ren9_schedule(value: dict, source: dict) -> dict:
    """把官方接口的 sfcMatch 结构转成统一赛单结构（纯解析，不补数据）。"""
    sfc = value.get("sfcMatch") or {}
    period = str(sfc.get("lotteryDrawNum") or "")
    matches = []
    for m in sfc.get("matchList") or []:
        seq = int(m.get("matchNum") or 0)
        matches.append(
            {
                "match_id": f"ren9_{period}_{seq:02d}",
                "period": period,
                "seq": seq,
                "home_team": _clean(m.get("masterTeamAllName") or m.get("masterTeamName")),
                "away_team": _clean(m.get("guestTeamAllName") or m.get("guestTeamName")),
                "home_short": _clean(m.get("masterTeamName")),
                "away_short": _clean(m.get("guestTeamName")),
                "competition": _clean(m.get("matchName")),
                "date": (m.get("startTime") or "")[:10] or None,
                "kickoff": None,           # 官方赛单只给比赛日期，开球时间由竞彩/ESPN 交叉补充
                "status": "finished" if (m.get("result") or "").strip() else "scheduled",
                "official_ref_odds": _ref_odds(m),
                "jc_match_id": int(m.get("infohubMatchId") or 0) or None,
                "result": (m.get("result") or "").strip() or None,
                "score": (m.get("czScore") or "").strip() or None,
                "sources": [source],
            }
        )
    return {
        "period": period,
        "game_type": "ren9",
        "game_name": "任选9场（与胜负游戏14场共用赛单）",
        "sale_begin": sfc.get("lotterySaleBegintime") or None,
        "sale_end": sfc.get("lotterySaleEndtime") or None,
        "draw_time": sfc.get("lotteryDrawTime") or None,
        "upcoming_periods": [str(p) for p in (value.get("sfclist") or [])],
        "matches": matches,
        "source": [source],
    }


def fetch_ren9_schedule(period: str | None = None, refresh: bool = False) -> dict:
    """获取传统足彩赛单；period 为空时返回当前在售期。"""
    cfg = _cfg("sporttery_zc")
    ep = cfg["endpoints"]["schedule_by_period" if period else "schedule"]
    url = cfg["base"] + ep.format(period=period)
    value, res = _get_json(url, settings()["data"].get("schedule_ttl", 600), refresh)
    sched = parse_ren9_schedule(value, res.source("中国体彩网-传统足彩赛单"))
    if not sched["period"] or not sched["matches"]:
        raise ProviderError(f"官方接口未返回第 {period or '当前'} 期赛单")
    return sched


def parse_ren9_draw(value: dict, source: dict) -> dict:
    results = []
    for m in value.get("matchList") or []:
        results.append(
            {
                "seq": int(m.get("matchNum") or 0),
                "home_team": _clean(m.get("masterTeamAllName")),
                "away_team": _clean(m.get("guestTeamAllName")),
                "competition": _clean(m.get("matchName")),
                "result": (m.get("result") or "").strip() or None,   # 3 / 1 / 0 / *（* = 比赛无效，任意选项算中）
                "score": (m.get("czScore") or "").strip() or None,
                "half_score": (m.get("czHalfScore") or "").strip() or None,
            }
        )
    prizes = {}
    # 开奖详情接口把任九奖级放在 prizeLevelList 里，历史列表接口放在 prizeLevelListRj 里
    for p in (value.get("prizeLevelList") or []) + (value.get("prizeLevelListRj") or []):
        prizes[p.get("prizeLevel")] = {
            "amount": to_float(p.get("stakeAmount")),
            "winners": to_float(p.get("stakeCount")),
        }
    ren9_prize = next((v for k, v in prizes.items() if k and "任选" in k), None)
    return {
        "period": str(value.get("lotteryDrawNum") or ""),
        "draw_date": value.get("lotteryDrawTime") or None,
        "draw_result": (value.get("lotteryDrawResult") or "").strip() or None,
        "results": results,
        "ren9_prize": ren9_prize,              # 真实单注奖金（税前）与中奖注数
        "ren9_sales": to_float(value.get("totalSaleAmountRj")),
        "prizes": prizes,
        "complete": len(results) == 14 and all(r["result"] for r in results),
        "source": [source],
    }


def fetch_ren9_draw(period: str, refresh: bool = False) -> dict:
    """获取某一期的开奖结果（14 场赛果 + 任九真实奖金）。未开奖时 complete=False。"""
    cfg = _cfg("sporttery_zc")
    url = cfg["base"] + cfg["endpoints"]["draw_by_period"].format(period=period)
    value, res = _get_json(url, settings()["data"].get("schedule_ttl", 600), refresh)
    draw = parse_ren9_draw(value, res.source("中国体彩网-传统足彩开奖"))
    draw["period"] = draw["period"] or str(period)
    return draw


def fetch_ren9_history(page: int = 1, page_size: int = 30, refresh: bool = False) -> list[dict]:
    """历史开奖列表（含每期 14 场赛果与任九奖金），用于历史回测与 ROI。"""
    cfg = _cfg("sporttery_zc")
    url = cfg["base"] + cfg["endpoints"]["history"].format(page=page, page_size=page_size)
    value, res = _get_json(url, settings()["data"].get("history_ttl", 21600), refresh)
    src = res.source("中国体彩网-传统足彩历史开奖")
    return [parse_ren9_draw(item, src) for item in value.get("list") or []]


def parse_jczq(value: dict, source: dict) -> list[dict]:
    out = []
    for day in value.get("matchInfoList") or []:
        for m in day.get("subMatchList") or []:
            had, hhad = m.get("had") or {}, m.get("hhad") or {}

            def pool(p: dict) -> dict | None:
                h, d, a = to_float(p.get("h")), to_float(p.get("d")), to_float(p.get("a"))
                if not (h and d and a):
                    return None
                upd = f"{p.get('updateDate', '')} {p.get('updateTime', '')}".strip() or None
                return {"home": h, "draw": d, "away": a, "updated_at": upd}

            hh = pool(hhad)
            if hh:
                hh["handicap"] = to_float(hhad.get("goalLine"))
            kickoff = None
            if m.get("matchDate") and m.get("matchTime"):
                kickoff = f"{m['matchDate']}T{m['matchTime']}+08:00"
            out.append(
                {
                    "match_id": f"jczq_{m.get('matchId')}",
                    "jc_match_id": m.get("matchId"),
                    "match_num": m.get("matchNumStr"),
                    "business_date": m.get("businessDate"),
                    "home_team": _clean(m.get("homeTeamAllName")),
                    "away_team": _clean(m.get("awayTeamAllName")),
                    "home_short": _clean(m.get("homeTeamAbbName")),
                    "away_short": _clean(m.get("awayTeamAbbName")),
                    "competition": _clean(m.get("leagueAbbName")),
                    "competition_full": _clean(m.get("leagueAllName")),
                    "date": m.get("matchDate"),
                    "kickoff": kickoff,
                    "status": "scheduled" if m.get("matchStatus") == "Selling" else str(m.get("matchStatus") or "").lower(),
                    "had": pool(had),
                    "hhad": hh,
                    "sources": [source],
                }
            )
    return out


def fetch_jczq_matches(refresh: bool = False) -> list[dict]:
    """竞彩足球在售比赛与官方赔率（胜平负 + 让球胜平负）。"""
    cfg = _cfg("sporttery_jc")
    url = cfg["base"] + cfg["endpoints"]["matches"]
    value, res = _get_json(url, settings()["data"].get("odds_ttl", 600), refresh)
    return parse_jczq(value, res.source("中国体育彩票-竞彩足球"))
