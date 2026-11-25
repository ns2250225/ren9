#!/usr/bin/env python
"""计算每场比赛的胜平负概率（Elo + Poisson + Dixon-Coles + 市场去水 → 集成）。

示例：
    python scripts/calculate_probability.py --date today
    python scripts/calculate_probability.py --date today --matches 3,8 --refresh   # 重新分析第 3、8 场
"""
from _cli import base_parser, main, make_ctx, run, schedule_problem, stage_file

KEEP = ("match_id", "seq", "home_team", "away_team", "competition", "kickoff", "status", "probabilities",
        "prediction", "model_confidence", "data_quality", "models_used", "models_unavailable",
        "model_probabilities", "weights_used", "expected_goals", "handicap", "reason")


def body():
    args = base_parser(__doc__.splitlines()[0]).parse_args()
    ctx = make_ctx(args)
    st = run(ctx, "probabilities")
    bad = schedule_problem(ctx)
    if bad:
        return bad
    pr = st["probabilities"]
    if args.full:
        return pr
    return {"status": "ok", "date": pr["date"], "period": pr["period"], "generated_at": pr["generated_at"],
            "predicted": pr["predicted"], "total": pr["total"],
            "note": "model_confidence 不是命中概率；probabilities 三项之和为 1",
            "matches": [{k: m[k] for k in KEEP if k in m} for m in pr["matches"]],
            "output_file": stage_file(ctx, "probabilities")}


if __name__ == "__main__":
    main(body)
