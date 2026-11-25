#!/usr/bin/env python
"""采集球队与比赛数据（排名、近期战绩、攻防、xG、主客场、交锋、赛程压力、首发、Elo），每项带来源与时间。

示例：
    python scripts/fetch_team_data.py --date today
    python scripts/fetch_team_data.py --date today --matches 3,8 --refresh
"""
from _cli import base_parser, main, make_ctx, run, schedule_problem, stage_file


def summarize(m: dict) -> dict:
    t = m["teams"]
    return {
        "seq": m["seq"], "match": f"{m['home_team']} vs {m['away_team']}", "competition": m["competition"],
        "resolved": m["resolution"]["status"] == "resolved", "method": m["resolution"].get("method"),
        "home_en": m["resolution"].get("home_en"), "away_en": m["resolution"].get("away_en"),
        "kickoff": m["kickoff"]["value"], "status": m["status"],
        "history": (f"{m['history']['key']}: 主 {m['history']['home_matches']} 场 / 客 {m['history']['away_matches']} 场"
                    if m.get("history") else None),
        "has_standings": bool(t["home"].get("standing") and t["away"].get("standing")),
        "has_form": bool(t["home"]["form"]["last5"] and t["away"]["form"]["last5"]),
        "has_elo": bool(t["home"].get("elo") and t["away"].get("elo")),
        "missing_count": len(m["missing"]),
    }


def body():
    args = base_parser(__doc__.splitlines()[0]).parse_args()
    ctx = make_ctx(args)
    st = run(ctx, "team_data")
    bad = schedule_problem(ctx)
    if bad:
        return bad
    td = st["team_data"]
    if args.full:
        return td
    return {"status": "ok", "date": td["date"], "period": td["period"], "generated_at": td["generated_at"],
            "resolved": td["resolved"], "total": td["total"], "notes": td["notes"],
            "matches": [summarize(m) for m in td["matches"]], "output_file": stage_file(ctx, "team_data")}


if __name__ == "__main__":
    main(body)
