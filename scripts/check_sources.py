#!/usr/bin/env python
"""实测所有数据源接口是否可用（只有实测通过的数据源才标记为可用）。

结果写入 data/cache/source_status.json。

示例：
    python scripts/check_sources.py
"""
import time
from datetime import date

from _cli import main

from ren9.common import data_dir, now, now_iso, write_json


def probe(name: str, fn) -> dict:
    t0 = time.time()
    try:
        detail = fn()
        return {"source": name, "available": True, "detail": detail, "seconds": round(time.time() - t0, 2)}
    except Exception as exc:  # noqa: BLE001
        return {"source": name, "available": False, "detail": f"{type(exc).__name__}: {exc}"[:200],
                "seconds": round(time.time() - t0, 2)}


def body():
    from providers import clubelo, espn, football_data, sporttery, understat

    today = now().date()

    def zc():
        s = sporttery.fetch_ren9_schedule(None, refresh=True)
        return f"当前在售第 {s['period']} 期，{len(s['matches'])} 场，截止 {s['sale_end']}"

    def zc_draw():
        h = sporttery.fetch_ren9_history(1, 1, refresh=True)
        return f"最新开奖第 {h[0]['period']} 期，任九单注奖金 {(h[0]['ren9_prize'] or {}).get('amount')}"

    def jc():
        m = sporttery.fetch_jczq_matches(refresh=True)
        return f"{len(m)} 场在售比赛"

    def es():
        ev, _ = espn.scoreboard("eng.1", today.strftime("%Y%m"), refresh=True)
        return f"英超本月 {len(ev)} 场比赛"

    def fd():
        rows, _ = football_data.league_season("E0", football_data.season_code(today), refresh=True)
        xg = sum(1 for r in rows if r["hxg"] is not None)
        return f"英超本赛季 {len(rows)} 场，其中 {xg} 场带 xG"

    def ce():
        tab, _ = clubelo.ratings(today.isoformat(), refresh=True)
        return f"{len(tab)} 支球队"

    def us():
        tab, _ = understat.league_xg("EPL", date(today.year, today.month, today.day), refresh=True)
        return f"{len(tab)} 支球队"

    results = [
        probe("sporttery_zc（体彩网传统足彩赛单）", zc),
        probe("sporttery_zc（体彩网开奖与奖金）", zc_draw),
        probe("sporttery_jc（竞彩足球赔率）", jc),
        probe("espn（赛程/比分/积分榜）", es),
        probe("football_data（历史赛果/xG/赔率）", fd),
        probe("clubelo（Elo 评级）", ce),
        probe("understat（xG）", us),
    ]
    out = {"checked_at": now_iso(), "results": results,
           "available": sum(r["available"] for r in results), "total": len(results),
           "fallbacks": {"clubelo": "不可用时用历史赛果自算 Elo（仅同赛事内可比）",
                         "understat": "不可用时使用 football-data.co.uk 的 HxG/AxG 列"}}
    write_json(data_dir() / "cache" / "source_status.json", out)
    return out


if __name__ == "__main__":
    main(body)
