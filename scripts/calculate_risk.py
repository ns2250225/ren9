#!/usr/bin/env python
"""比赛风险分析：概率接近、模型分歧、数据不足、赔率波动、首发/伤停、赛程压力、赛事性质。

示例：
    python scripts/calculate_risk.py --date today
"""
from _cli import base_parser, main, make_ctx, run, schedule_problem, stage_file


def body():
    args = base_parser(__doc__.splitlines()[0]).parse_args()
    ctx = make_ctx(args)
    st = run(ctx, "risk")
    bad = schedule_problem(ctx)
    if bad:
        return bad
    rk = st["risk"]
    out = {"status": "ok", "date": rk["date"], "period": rk["period"], "generated_at": rk["generated_at"],
           "counts": rk["counts"], "levels": "LOW 低风险 / MEDIUM 中风险 / HIGH 高风险 / UNKNOWN 数据不足",
           "output_file": stage_file(ctx, "risk")}
    if args.full:
        out["matches"] = rk["matches"]
    else:
        out["matches"] = [{"seq": m["seq"], "match": f"{m['home_team']} vs {m['away_team']}",
                           "risk_level": m["risk_level"], "risk_score": m["risk_score"], "reasons": m["reasons"],
                           "unknown_factors": m["unknown_factors"]} for m in rk["matches"]]
    return out


if __name__ == "__main__":
    main(body)
