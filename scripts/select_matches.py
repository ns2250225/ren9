#!/usr/bin/env python
"""任九筛选：从 14 场中严格选出 9 场、排除 5 场，并给出全部 14 场的评分与理由。

示例：
    python scripts/select_matches.py --date today
    python scripts/select_matches.py --date today --strategy conservative
"""
from _cli import base_parser, main, make_ctx, run, schedule_problem, stage_file


def body():
    args = base_parser(__doc__.splitlines()[0], strategy=True).parse_args()
    if args.game_type != "ren9":
        raise ValueError("14 选 9 只适用于传统足彩任九（--game-type ren9）")
    ctx = make_ctx(args)
    st = run(ctx, "selection")
    bad = schedule_problem(ctx)
    if bad:
        return bad
    out = {}
    for s in ctx.strategies:
        sel = st["selection"]["strategies"][s]
        item = {k: sel.get(k) for k in ("status", "message", "strategy", "eligible_count", "selected", "excluded",
                                        "score_weights", "score_weights_source")}
        item["matches"] = sel["matches"] if args.full else [
            {"seq": r["seq"], "match": f"{r['home_team']} vs {r['away_team']}", "selected": r.get("selected", False),
             "match_score": r["match_score"], "risk_level": r["risk_level"], "reason": r.get("reason") or r.get("ineligible_reason")}
            for r in sel["matches"]]
        out[s] = item
    result = out[ctx.strategy] if len(out) == 1 else {"status": "ok", "strategies": out}
    result.update(date=ctx.date, period=st["schedule"]["period"], output_file=stage_file(ctx, "selection"))
    return result


if __name__ == "__main__":
    main(body)
