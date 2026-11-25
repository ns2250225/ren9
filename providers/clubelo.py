"""ClubElo 适配器：俱乐部 Elo 评级（跨联赛可比）。不可用时由调用方降级为自算 Elo。"""
from __future__ import annotations

import csv
import io

from ren9.common import data_sources, settings, to_float
from providers.base import ProviderError, http_get


def ratings(day: str, refresh: bool = False) -> tuple[dict[str, dict], dict]:
    """返回 {俱乐部名: {elo, country, level}} 与来源信息。day: YYYY-MM-DD。"""
    cfg = data_sources()["sources"]["clubelo"]
    url = cfg["base"] + cfg["endpoints"]["ratings"].format(date=day)
    res = http_get(
        url,
        ttl=settings()["data"].get("history_ttl", 21600),
        refresh=refresh,
        validate=lambda t: t.lstrip().startswith("Rank,Club"),
        retries=0,
        timeout=8,
    )
    out: dict[str, dict] = {}
    for r in csv.DictReader(io.StringIO(res.text)):
        elo = to_float(r.get("Elo"))
        if r.get("Club") and elo:
            out[r["Club"]] = {"elo": elo, "country": r.get("Country"), "level": r.get("Level")}
    if not out:
        raise ProviderError("ClubElo 返回空数据")
    return out, res.source("ClubElo")
