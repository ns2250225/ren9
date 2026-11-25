"""历史比赛数据集：优先 football-data.co.uk（含 xG 与赔率），否则 ESPN 赛事历史。"""
from __future__ import annotations

from datetime import date

from ren9.common import settings
from ren9.teams import best_match
from providers import espn, football_data

_memo: dict[tuple, dict] = {}


def load(comp: dict | None, ref: date, kind: str | None = None, refresh: bool = False) -> list[dict]:
    """返回该赛事可用的数据集列表（按优先级）。每个数据集：{kind, key, rows, sources, teams, has_xg}。

    只包含 ref 之前的比赛，避免把待预测比赛或之后的信息带入模型。
    """
    out = []
    if not comp:
        return out
    if kind in (None, "fd") and (comp.get("fd") or comp.get("fd_extra")):
        key = ("fd", comp.get("fd") or comp.get("fd_extra"), ref.isoformat())
        if key not in _memo or refresh:
            rows, sources = football_data.history(comp, ref, refresh=refresh)
            _memo[key] = _dataset("fd", key[1], rows, sources)
        if _memo[key]["rows"]:
            out.append(_memo[key])
    if kind in (None, "espn") and comp.get("espn"):
        key = ("espn", comp["espn"], ref.isoformat())
        if key not in _memo or refresh:
            months = settings()["data"].get("history_months", 15)
            rows, sources = espn.history(comp["espn"], ref, months)
            _memo[key] = _dataset("espn", comp["espn"], rows, sources)
        if _memo[key]["rows"]:
            out.append(_memo[key])
    return out


def _dataset(kind: str, key: str, rows: list[dict], sources: list[dict]) -> dict:
    teams = sorted({r["home"] for r in rows} | {r["away"] for r in rows})
    return {
        "kind": kind,
        "key": key,
        "rows": rows,
        "sources": sources,
        "teams": teams,
        "has_xg": sum(1 for r in rows if r.get("hxg") is not None) >= max(10, len(rows) // 4),
        "updated_at": max((s.get("fetched_at") or "" for s in sources), default=None),
    }


def locate(ds: dict, names: list[str]) -> str | None:
    """在数据集里找到某支球队的写法。"""
    return best_match([n for n in names if n], ds["teams"]) if names else None


def team_rows(ds: dict, name: str) -> list[dict]:
    return [r for r in ds["rows"] if r["home"] == name or r["away"] == name]


def team_stats(ds: dict, name: str) -> dict:
    """由历史比赛推导球队统计（只统计真实存在的比赛；没有的字段返回 None）。"""
    rows = sorted(team_rows(ds, name), key=lambda r: r["date"], reverse=True)
    if not rows:
        return {"matches": 0}

    def res(r):
        gf, ga = (r["hg"], r["ag"]) if r["home"] == name else (r["ag"], r["hg"])
        return "W" if gf > ga else "D" if gf == ga else "L"

    recent = rows[:20]
    gf = [r["hg"] if r["home"] == name else r["ag"] for r in recent]
    ga = [r["ag"] if r["home"] == name else r["hg"] for r in recent]
    xg = [(r["hxg"] if r["home"] == name else r["axg"]) for r in recent]
    xga = [(r["axg"] if r["home"] == name else r["hxg"]) for r in recent]
    xg = [v for v in xg if v is not None]
    xga = [v for v in xga if v is not None]
    shots = [(r.get("hs") if r["home"] == name else r.get("as_")) for r in recent]
    shots = [v for v in shots if v is not None]
    home = [r for r in rows if r["home"] == name][:20]
    away = [r for r in rows if r["away"] == name][:20]

    def avg(v):
        return round(sum(v) / len(v), 3) if v else None

    return {
        "matches": len(rows),
        "sample": len(recent),
        "last10": [res(r) for r in rows[:10]],
        "last5": [res(r) for r in rows[:5]],
        "goals_per_match": avg(gf),
        "conceded_per_match": avg(ga),
        "xg_per_match": avg(xg) if len(xg) >= 5 else None,
        "xga_per_match": avg(xga) if len(xga) >= 5 else None,
        "shots_per_match": avg(shots) if len(shots) >= 5 else None,
        "home_matches": len(home),
        "home_win_rate": round(sum(1 for r in home if r["hg"] > r["ag"]) / len(home), 3) if home else None,
        "away_matches": len(away),
        "away_win_rate": round(sum(1 for r in away if r["ag"] > r["hg"]) / len(away), 3) if away else None,
        "last_match_date": rows[0]["date"],
    }


def head_to_head(ds: dict, home: str, away: str) -> dict:
    """数据集窗口内的历史交锋（以本场主队视角统计胜平负）。"""
    rows = [r for r in ds["rows"] if {r["home"], r["away"]} == {home, away}]
    w = d = l = 0
    for r in rows:
        gf, ga = (r["hg"], r["ag"]) if r["home"] == home else (r["ag"], r["hg"])
        w += gf > ga
        d += gf == ga
        l += gf < ga
    return {
        "meetings": len(rows),
        "home_team_wins": w,
        "draws": d,
        "away_team_wins": l,
        "recent": [
            {"date": r["date"], "home": r["home"], "away": r["away"], "score": f"{r['hg']}:{r['ag']}"}
            for r in sorted(rows, key=lambda r: r["date"], reverse=True)[:5]
        ],
    }
