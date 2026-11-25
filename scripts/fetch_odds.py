#!/usr/bin/env python
"""采集并分析赔率：隐含概率、水位、去水概率、赔率波动。官方与商业来源分开保存。

示例：
    python scripts/fetch_odds.py --date today
    python scripts/fetch_odds.py --odds 1.85,3.40,4.20      # 只做计算，不联网
"""
from _cli import base_parser, main, make_ctx, run, schedule_problem, stage_file


def body():
    p = base_parser(__doc__.splitlines()[0])
    p.add_argument("--odds", help="直接计算一组胜,平,负赔率的隐含概率与去水概率")
    args = p.parse_args()
    if args.odds:
        from ren9.odds import analyze

        h, d, a = (float(x) for x in args.odds.split(","))
        return {"match_id": "manual", **analyze({"home": h, "draw": d, "away": a})}
    ctx = make_ctx(args)
    st = run(ctx, "odds")
    bad = schedule_problem(ctx)
    if bad:
        return bad
    od = st["odds"]
    if args.full:
        return od
    rows = []
    for m in od["matches"]:
        rows.append({
            "match_id": m["match_id"], "seq": m["seq"], "match": f"{m['home_team']} vs {m['away_team']}",
            "sources": [{"source": s["source"], "kind": s["kind"], "odds": s["odds"],
                         "implied_probability": s["implied_probability"], "overround": s["overround"],
                         "fair_probability": s["fair_probability"], "fetched_at": s["fetched_at"]}
                        for s in m["sources"]],
            "market_fair_probability": (m["market"] or {}).get("fair_probability"),
            "jc_handicap_line": (m["jc_handicap"] or {}).get("line"),
            "movement": m["movement"]["max_change"],
        })
    return {"status": "ok", "date": od["date"], "period": od["period"], "generated_at": od["generated_at"],
            "with_odds": od["with_odds"], "total": od["total"], "notes": od["notes"], "matches": rows,
            "output_file": stage_file(ctx, "odds")}


if __name__ == "__main__":
    main(body)
