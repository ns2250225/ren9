"""ESPN 公开接口适配器：赛程、比分、积分榜、近期战绩、首发名单、商业赔率。"""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone

from ren9.common import data_sources, settings, to_float
from providers.base import Fetched, ProviderError, http_get


def _url(endpoint: str, **kw) -> str:
    cfg = data_sources()["sources"]["espn"]
    return cfg["base"] + cfg["endpoints"][endpoint].format(**kw)


def _get(url: str, ttl: float, refresh: bool = False) -> tuple[dict, Fetched]:
    res = http_get(url, ttl=ttl, refresh=refresh, validate=lambda t: t.lstrip().startswith("{"))
    try:
        data = json.loads(res.text)
    except json.JSONDecodeError as exc:
        raise ProviderError(f"ESPN 返回非 JSON: {url}") from exc
    if isinstance(data, dict) and data.get("code") and data.get("message") and "events" not in data:
        raise ProviderError(f"ESPN 错误 {data.get('code')}: {data.get('message')}")
    return data, res


def american_to_decimal(ml) -> float | None:
    v = to_float(str(ml).replace("+", "")) if ml is not None else None
    if v is None or v == 0:
        return None
    if str(ml).strip().upper() == "EVEN":
        return 2.0
    return round(1 + (v / 100 if v > 0 else 100 / abs(v)), 4)


def _score(c: dict) -> int | None:
    s = c.get("score")
    if isinstance(s, dict):
        s = s.get("displayValue", s.get("value"))
    v = to_float(s)
    return None if v is None else int(v)


def _parse_odds(comp: dict) -> list[dict]:
    out = []
    for o in comp.get("odds") or []:
        if not isinstance(o, dict):
            continue
        prov = (o.get("provider") or {}).get("name") or "unknown"
        ml = o.get("moneyline") or {}

        def side(key: str, when: str):
            node = (ml.get(key) or {}).get(when) or {}
            return american_to_decimal(node.get("odds"))

        cur = {k: side(k, "close") for k in ("home", "draw", "away")}
        opn = {k: side(k, "open") for k in ("home", "draw", "away")}
        if not all(cur.values()):
            cur = {
                "home": american_to_decimal((o.get("homeTeamOdds") or {}).get("moneyLine")),
                "draw": american_to_decimal((o.get("drawOdds") or {}).get("moneyLine")),
                "away": american_to_decimal((o.get("awayTeamOdds") or {}).get("moneyLine")),
            }
        item = {"provider": prov, "over_under": to_float(o.get("overUnder")), "spread": to_float(o.get("spread"))}
        ps, tot = o.get("pointSpread") or {}, o.get("total") or {}

        def leg(node: dict, key: str):
            c = (node.get(key) or {}).get("close") or {}
            return c.get("line"), american_to_decimal(c.get("odds"))

        hl, ho = leg(ps, "home")
        _, ao = leg(ps, "away")
        if hl is not None and ho and ao:
            item["handicap"] = {"line": to_float(hl), "home": ho, "away": ao}
        ol, oo = leg(tot, "over")
        _, uo = leg(tot, "under")
        if ol is not None and oo and uo:
            item["totals"] = {"line": to_float(str(ol).lstrip("ou")), "over": oo, "under": uo}
        if all(cur.values()):
            item["current"] = cur
        if all(opn.values()):
            item["open"] = opn
        if "current" in item:
            out.append(item)
    return out


def parse_event(ev: dict, league: str | None = None) -> dict | None:
    comp = (ev.get("competitions") or [{}])[0]
    home = away = None
    for c in comp.get("competitors") or []:
        t = c.get("team") or {}
        info = {
            "id": str(t.get("id") or c.get("id") or ""),
            "name": t.get("displayName") or t.get("name"),
            "names": [n for n in {t.get("displayName"), t.get("shortDisplayName"), t.get("name"), t.get("location")} if n],
            "abbr": t.get("abbreviation"),
            "score": _score(c),
            "form": c.get("form") or None,
        }
        if c.get("homeAway") == "home":
            home = info
        else:
            away = info
    if not home or not away:
        return None
    st = ((comp.get("status") or ev.get("status") or {}).get("type")) or {}
    uid = ev.get("uid") or ""
    return {
        "event_id": str(ev.get("id")),
        "league": league,
        "league_uid": next((p[2:] for p in uid.split("~") if p.startswith("l:")), None),
        "kickoff_utc": ev.get("date") or comp.get("date"),
        "home": home,
        "away": away,
        "state": st.get("state"),              # pre / in / post
        "status": st.get("name"),
        "completed": bool(st.get("completed")),
        "odds": _parse_odds(comp),
    }


def scoreboard(league: str, dates: str, ttl: float | None = None, refresh: bool = False) -> tuple[list[dict], dict]:
    """league: ESPN 联赛 slug 或 'all'；dates: YYYYMMDD 或 YYYYMM（不支持区间）。"""
    if ttl is None:
        ttl = settings()["data"].get("cache_ttl", 3600)
    data, res = _get(_url("scoreboard", league=league, dates=dates), ttl, refresh)
    events = [e for e in (parse_event(ev, league) for ev in data.get("events") or []) if e]
    return events, res.source(f"ESPN-scoreboard-{league}")


def history(league: str, ref: date, months: int) -> tuple[list[dict], list[dict]]:
    """按月抓取某赛事的历史完赛比赛（ref 之前，不含 ref 当天及以后的比赛，避免数据泄漏）。"""
    cfgd = settings()["data"]
    rows, sources = [], []
    y, m = ref.year, ref.month
    for i in range(months + 1):
        ym = f"{y:04d}{m:02d}"
        ttl = cfgd.get("history_ttl", 21600) if i == 0 else cfgd.get("archive_ttl", 2592000)
        try:
            events, src = scoreboard(league, ym, ttl=ttl)
        except ProviderError:
            events, src = [], None
        if src:
            sources.append(src)
        for e in events:
            if not e["completed"] or e["home"]["score"] is None or e["away"]["score"] is None:
                continue
            d = (e["kickoff_utc"] or "")[:10]
            if not d or d >= ref.isoformat():
                continue
            rows.append(
                {
                    "date": d,
                    "home": e["home"]["name"],
                    "away": e["away"]["name"],
                    "hg": e["home"]["score"],
                    "ag": e["away"]["score"],
                    "hxg": None,
                    "axg": None,
                }
            )
        m -= 1
        if m == 0:
            y, m = y - 1, 12
    seen, uniq = set(), []
    for r in sorted(rows, key=lambda r: r["date"]):
        k = (r["date"], r["home"], r["away"])
        if k not in seen:
            seen.add(k)
            uniq.append(r)
    return uniq, sources


def standings(league: str, refresh: bool = False) -> tuple[dict[str, dict], dict]:
    """返回 {球队名: {rank, points, played, wins, draws, losses, gf, ga, teams_total}}。"""
    data, res = _get(_url("standings", league=league), settings()["data"].get("history_ttl", 21600), refresh)
    table: dict[str, dict] = {}
    groups = data.get("children") or [data]
    for g in groups:
        entries = ((g.get("standings") or {}).get("entries")) or []
        for e in entries:
            stats = {s.get("name"): s.get("value") for s in e.get("stats") or []}
            team = e.get("team") or {}
            name = team.get("displayName") or team.get("name")
            if not name:
                continue

            def iv(k):
                v = to_float(stats.get(k))
                return None if v is None else int(v)

            table[name] = {
                "team_id": str(team.get("id") or ""),
                "rank": iv("rank"),
                "points": iv("points"),
                "played": iv("gamesPlayed"),
                "wins": iv("wins"),
                "draws": iv("ties"),
                "losses": iv("losses"),
                "gf": iv("pointsFor"),
                "ga": iv("pointsAgainst"),
                "teams_total": len(entries),
            }
    return table, res.source(f"ESPN-standings-{league}")


def _lineups(data: dict) -> dict[str, dict]:
    """比赛详情中的双方名单：{球队 id: {official, formation, starters, players}}。

    players 是完整的比赛日名单 [{id, name, position, starter}]（首发 + 替补）；
    只有 ESPN 已发布完整 11 人首发才算官方首发，否则 starters / players 为空。
    """
    out = {}
    for r in data.get("rosters") or []:
        tid = str((r.get("team") or {}).get("id") or "")
        players = []
        for p in r.get("roster") or []:
            a = p.get("athlete") or {}
            if a.get("id") and a.get("displayName"):
                players.append({"id": str(a["id"]), "name": a["displayName"],
                                "position": (p.get("position") or {}).get("abbreviation"),
                                "starter": bool(p.get("starter"))})
        official = sum(1 for p in players if p["starter"]) >= 11
        out[tid] = {
            "official": official,
            "formation": r.get("formation") or None,
            "starters": [p["name"] for p in players if p["starter"]] if official else [],
            "players": players if official else [],
        }
    return out


def match_lineups(league: str, event_id: str) -> tuple[dict, dict]:
    """已结束比赛的双方名单（内容不再变化，按归档有效期缓存）。"""
    data, res = _get(_url("summary", league=league, event=event_id), settings()["data"].get("archive_ttl", 2592000))
    return _lineups(data), res.source("ESPN-summary")


def summary(league: str, event_id: str, refresh: bool = False) -> tuple[dict, dict]:
    """单场详情：近 5 场（全部赛事）、官方首发是否公布、商业赔率。"""
    data, res = _get(_url("summary", league=league, event=event_id), settings()["data"].get("odds_ttl", 600), refresh)
    out: dict = {"last_five": {}, "lineups": {}, "odds": []}
    for block in data.get("lastFiveGames") or []:
        tid = str((block.get("team") or {}).get("id") or "")
        games = []
        for g in block.get("events") or []:
            games.append(
                {
                    "event_id": str(g.get("id") or "") or None,
                    "date": (g.get("gameDate") or "")[:10],
                    "result": g.get("gameResult"),        # W / D / L
                    "score": g.get("score"),
                    "home": str(g.get("homeTeamId")) == tid,
                    "opponent": (g.get("opponent") or {}).get("displayName"),
                    "competition": g.get("leagueName") or g.get("competitionName"),
                }
            )
        out["last_five"][tid] = sorted(games, key=lambda x: x["date"], reverse=True)
    out["lineups"] = _lineups(data)
    injuries = data.get("injuries")
    out["injuries"] = injuries if injuries else None   # 该接口通常不提供足球伤停，None = 未知
    for o in data.get("pickcenter") or data.get("odds") or []:
        cur = {
            "home": american_to_decimal((o.get("homeTeamOdds") or {}).get("moneyLine")),
            "draw": american_to_decimal((o.get("drawOdds") or {}).get("moneyLine")),
            "away": american_to_decimal((o.get("awayTeamOdds") or {}).get("moneyLine")),
        }
        if all(cur.values()):
            out["odds"].append(
                {
                    "provider": (o.get("provider") or {}).get("name") or "unknown",
                    "current": cur,
                    "over_under": to_float(o.get("overUnder")),
                    "spread": to_float(o.get("spread")),
                }
            )
    return out, res.source(f"ESPN-summary-{league}")


def kickoff_beijing(kickoff_utc: str | None) -> str | None:
    if not kickoff_utc:
        return None
    try:
        dt = datetime.fromisoformat(kickoff_utc.replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt.astimezone(timezone(timedelta(hours=8))).isoformat(timespec="seconds")
