"""500 彩票网适配器：任九/胜负彩 14 场的多家机构平均欧赔（公开网页，无需 API key）。

官方竞彩与 ESPN 的赔率只覆盖部分比赛（次级联赛常常没有），这里的选号页对当期 14 场都给出
平均欧赔，用来补齐市场概率。页面按期号 + 场次编号对应比赛；只支持当前在售期。
页面改版或不可用时抛出 ProviderError，调用方跳过该来源，不会用猜测值代替。
"""
from __future__ import annotations

import re

from ren9.common import data_sources, settings, to_float
from providers.base import ProviderError, http_get

_ROW = re.compile(r'<tr[^>]*data-vs="([^"]*)"[^>]*>(.*?)</tr>', re.S)
_CELL = re.compile(r"<td[^>]*>(.*?)</td>", re.S)
_EURO = re.compile(r'<td[^>]*id="index_0_\d+"[^>]*>(.*?)</td>', re.S)
_NUM = re.compile(r"\d+(?:\.\d+)?")
_PERIOD = re.compile(r'id="expect"[^>]*value="(\d+)"|value="(\d+)"[^>]*id="expect"')


def _text(html: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html)).strip()


def parse_ren9(text: str) -> dict:
    """解析选号页：返回 {period, matches: {场次编号: {home_team, away_team, competition, odds}}}。"""
    m = _PERIOD.search(text)
    period = (m.group(1) or m.group(2)) if m else None
    matches: dict[int, dict] = {}
    for vs, body in _ROW.findall(text):
        cells = _CELL.findall(body)
        names = [x.strip() for x in vs.split(",")]
        if len(cells) < 6 or len(names) != 2 or not all(names):
            continue
        seq = _text(cells[0])
        euro = _EURO.search(body)
        nums = [to_float(x) for x in _NUM.findall(_text(euro.group(1)))] if euro else []
        if not seq.isdigit() or len(nums) != 3 or min(nums) <= 1.0:
            continue
        matches[int(seq)] = {"home_team": names[0], "away_team": names[1], "competition": _text(cells[1]),
                             "odds": {"home": nums[0], "draw": nums[1], "away": nums[2]}}
    return {"period": period, "matches": matches}


def fetch_ren9_odds(period: str, refresh: bool = False) -> tuple[dict[int, dict], dict]:
    """返回 ({场次编号: {...}}, 来源信息)。页面期号与 period 不一致时视为不可用。"""
    cfg = data_sources()["sources"]["c500"]
    url = cfg["base"] + cfg["endpoints"]["ren9"].format(period=period)
    res = http_get(url, ttl=settings()["data"].get("odds_ttl", 600), refresh=refresh,
                   validate=lambda t: "data-vs=" in t, retries=1, timeout=12)
    parsed = parse_ren9(res.text)
    if parsed["period"] != str(period):
        raise ProviderError(f"500 彩票网返回的是第 {parsed['period']} 期，不是第 {period} 期（只支持当前在售期）")
    if not parsed["matches"]:
        raise ProviderError("500 彩票网页面中没有解析到赔率")
    return parsed["matches"], res.source("500彩票网-任九平均欧赔")
