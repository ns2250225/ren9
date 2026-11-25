#!/usr/bin/env python
"""获取赛程：传统足彩任九 14 场官方赛单（或竞彩足球在售比赛），并做完整性校验。

示例：
    python scripts/fetch_schedule.py --date today
    python scripts/fetch_schedule.py --period 26169
    python scripts/fetch_schedule.py --manual my_matches.csv --period 26169
    python scripts/fetch_schedule.py --game-type jczq
"""
from _cli import base_parser, main, make_ctx, run, stage_file


def body():
    args = base_parser(__doc__.splitlines()[0]).parse_args()
    ctx = make_ctx(args)
    sch = run(ctx, "schedule")["schedule"]
    out = dict(sch)
    out["output_file"] = stage_file(ctx, "schedule")
    if not sch.get("matches"):
        out["status"] = "no_schedule"
    elif not sch.get("complete"):
        out["status"] = "invalid_schedule"
    else:
        out["status"] = "ok"
    return out


if __name__ == "__main__":
    main(body)
