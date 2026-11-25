"""football-data.co.uk 适配器：历史赛果、xG（部分联赛）、赛前赔率（多家平均）。"""
from __future__ import annotations

import csv
import io
from datetime import date, datetime

from ren9.common import data_sources, settings, to_float
from providers.base import ProviderError, http_get


def season_code(d: date) -> str:
    """欧洲跨年赛季代码，如 2026-11 → '2627'。"""
    start = d.year if d.month >= 7 else d.year - 1
    return f"{start % 100:02d}{(start + 1) % 100:02d}"


def _parse_date(s: str) -> str | None:
    s = (s or "").strip()
    for fmt in ("%d/%m/%Y", "%d/%m/%y"):
        try:
            return datetime.strptime(s, fmt).date().isoformat()
        except ValueError:
            continue
    return None


def _odds(row: dict, prefixes: tuple[str, ...]) -> dict | None:
    for p in prefixes:
        h, d, a = to_float(row.get(p + "H")), to_float(row.get(p + "D")), to_float(row.get(p + "A"))
        if h and d and a and min(h, d, a) > 1.0:
            return {"home": h, "draw": d, "away": a, "book": p}
    return None


def parse_csv(text: str) -> list[dict]:
    rows = []
    reader = csv.DictReader(io.StringIO(text.lstrip("﻿")))
    for r in reader:
        home = (r.get("HomeTeam") or r.get("Home") or "").strip()
        away = (r.get("AwayTeam") or r.get("Away") or "").strip()
        d = _parse_date(r.get("Date") or "")
        hg = to_float(r.get("FTHG") if "FTHG" in r else r.get("HG"))
        ag = to_float(r.get("FTAG") if "FTAG" in r else r.get("AG"))
        if not home or not away or not d or hg is None or ag is None:
            continue
        rows.append(
            {
                "date": d,
                "season": (r.get("Season") or "").strip() or None,
                "home": home,
                "away": away,
                "hg": int(hg),
                "ag": int(ag),
                "hxg": to_float(r.get("HxG")),
                "axg": to_float(r.get("AxG")),
                "hs": to_float(r.get("HS")),
                "as_": to_float(r.get("AS")),
                # 赛前赔率（非收盘）用于回测，避免使用临场之后的信息
                "odds_pre": _odds(r, ("Avg", "B365", "BW", "PS")),
                "odds_close": _odds(r, ("AvgC", "B365C", "PSC", "MaxC")),
            }
        )
    rows.sort(key=lambda x: x["date"])
    return rows


def _fetch(url: str, ttl: float, refresh: bool):
    return http_get(url, ttl=ttl, refresh=refresh, validate=lambda t: "Date" in t[:400] and "," in t[:400])


def league_season(code: str, season: str, current: bool = True, refresh: bool = False) -> tuple[list[dict], dict]:
    cfg = data_sources()["sources"]["football_data"]
    url = cfg["base"] + cfg["endpoints"]["main"].format(season=season, code=code)
    d = settings()["data"]
    res = _fetch(url, d.get("history_ttl", 21600) if current else d.get("archive_ttl", 2592000), refresh)
    return parse_csv(res.text), res.source(f"football-data.co.uk-{code}-{season}")


def league_extra(code: str, refresh: bool = False) -> tuple[list[dict], dict]:
    cfg = data_sources()["sources"]["football_data"]
    url = cfg["base"] + cfg["endpoints"]["extra"].format(code=code)
    res = _fetch(url, settings()["data"].get("history_ttl", 21600), refresh)
    return parse_csv(res.text), res.source(f"football-data.co.uk-{code}")


def history(comp: dict, ref: date, seasons: int = 2, refresh: bool = False) -> tuple[list[dict], list[dict]]:
    """返回 ref 之前的历史比赛（当季 + 之前若干赛季）。comp 为 competitions 配置项。"""
    rows: list[dict] = []
    sources: list[dict] = []
    if comp.get("fd"):
        start = ref.year if ref.month >= 7 else ref.year - 1
        for i in range(seasons):
            y = start - i
            code = f"{y % 100:02d}{(y + 1) % 100:02d}"
            try:
                r, src = league_season(comp["fd"], code, current=(i == 0), refresh=refresh and i == 0)
            except ProviderError:
                continue
            rows.extend(r)
            sources.append(src)
    elif comp.get("fd_extra"):
        try:
            r, src = league_extra(comp["fd_extra"], refresh=refresh)
            cutoff = date(ref.year - seasons, ref.month, 1).isoformat()
            rows.extend(x for x in r if x["date"] >= cutoff)
            sources.append(src)
        except ProviderError:
            pass
    rows = [x for x in rows if x["date"] < ref.isoformat()]
    rows.sort(key=lambda x: x["date"])
    return rows, sources
