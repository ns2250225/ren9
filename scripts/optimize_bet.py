#!/usr/bin/env python
"""投注组合优化：为选中的 9 场分配单选/双选/全选，计算注数、成本与模型估计的全中概率。

示例：
    python scripts/optimize_bet.py --strategy balanced
    python scripts/optimize_bet.py --all-strategies
    python scripts/optimize_bet.py --strategy aggressive --budget 200
    python scripts/optimize_bet.py --count "3;31;10;3;310;0;3;31;0"   # 只计算注数与金额，不联网
"""
from _cli import base_parser, main, make_ctx, run, schedule_problem, stage_file

CODE = {"3": "win", "1": "draw", "0": "loss"}


def body():
    p = base_parser(__doc__.splitlines()[0], strategy=True)
    p.add_argument("--count", help="用开奖代码(3/1/0)描述每场选择，分号分隔，只计算注数与金额")
    args = p.parse_args()
    if args.count:
        from ren9.common import settings
        from ren9.optimizer import bet_count

        sel = [[CODE[c] for c in part.strip()] for part in args.count.split(";") if part.strip()]
        n = bet_count(sel)
        unit = settings()["lottery"]["unit_price"]
        return {"matches": len(sel), "valid_ren9": len(sel) == 9,
                "singles": sum(len(s) == 1 for s in sel), "doubles": sum(len(s) == 2 for s in sel),
                "triples": sum(len(s) == 3 for s in sel), "bet_count": n, "unit_price": unit, "total_cost": n * unit}
    if args.game_type != "ren9":
        raise ValueError("组合优化只适用于传统足彩任九（--game-type ren9）")
    ctx = make_ctx(args)
    st = run(ctx, "plans")
    bad = schedule_problem(ctx)
    if bad:
        return bad
    plans = {s: st["plans"]["strategies"][s] for s in ctx.strategies}
    result = plans[ctx.strategy] if len(plans) == 1 else {"status": "ok", "plans": plans}
    result = dict(result)
    result["output_file"] = stage_file(ctx, "plans")
    return result


if __name__ == "__main__":
    main(body)
