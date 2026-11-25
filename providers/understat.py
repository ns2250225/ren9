"""Understat 适配器：球队 xG / xGA（五大联赛）。

Understat 没有正式 API，这里读取站点自身使用的 JSON 接口；站点维护或改版时抛出
ProviderError，调用方改用 football-data.co.uk 的 HxG/AxG 列，不会用 0 填充。
"""
from __future__ import annotations

import json
import re
from datetime import date

from ren9.common import data_sources, settings, to_float
from providers.base import ProviderError, http_get

HEADERS = {"X-Requested-With": "XMLHttpRequest", "Referer": "https://understat.com/"}


def _extract_teams(text: str) -> dict:
    t = text.lstrip()
    if t.startswith("{"):
        data = json.loads(t)
        return data.get("teams") or {}
    # 旧版页面：var teamsData = JSON.parse('\x7B...')
    m = re.search(r"teamsData\s*=\s*JSON\.parse\('(.+?)'\)", text)
    if not m:
        raise ProviderError("Understat 页面中未找到 teamsData")
    return json.loads(m.group(1).encode("utf-8").decode("unicode_escape"))


def league_xg(league: str, ref: date, refresh: bool = False) -> tuple[dict[str, dict], dict]:
    """返回 {球队名: {matches, xg_for, xg_against}}（场均），只统计 ref 之前的比赛。"""
    season = ref.year if ref.month >= 7 else ref.year - 1
    cfg = data_sources()["sources"]["understat"]
    url = cfg["base"] + cfg["endpoints"]["league"].format(league=league, season=season)
    res = http_get(
        url,
        ttl=settings()["data"].get("history_ttl", 21600),
        headers=HEADERS,
        refresh=refresh,
        validate=lambda t: t.lstrip().startswith("{") or "teamsData" in t,
        retries=0,
        timeout=10,
    )
    try:
        teams = _extract_teams(res.text)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ProviderError("Understat 数据解析失败") from exc
    out: dict[str, dict] = {}
    for t in teams.values():
        hist = [h for h in t.get("history") or [] if (h.get("date") or "")[:10] < ref.isoformat()]
        xg = [to_float(h.get("xG")) for h in hist]
        xga = [to_float(h.get("xGA")) for h in hist]
        xg = [v for v in xg if v is not None]
        xga = [v for v in xga if v is not None]
        if t.get("title") and xg and xga:
            out[t["title"]] = {
                "matches": len(hist),
                "xg_for": round(sum(xg) / len(xg), 3),
                "xg_against": round(sum(xga) / len(xga), 3),
            }
    if not out:
        raise ProviderError("Understat 返回空数据")
    return out, res.source(f"Understat-{league}-{season}")
