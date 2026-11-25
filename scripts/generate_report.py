#!/usr/bin/env python
"""生成完整分析报告（Markdown + JSON），并保存预测记录与预测时点数据快照。

缺少的上游阶段会自动运行，所以这一条命令就能完成"分析今天的任九"的全部流程。

示例：
    python scripts/generate_report.py --date today
    python scripts/generate_report.py --date today --strategy conservative
    python scripts/generate_report.py --date today --all-strategies          # 三套方案
    python scripts/generate_report.py --date today --matches 3,8 --refresh   # 重新分析第 3、8 场后更新报告
    python scripts/generate_report.py --date today --match 3                 # 单场详细报告
    python scripts/generate_report.py --game-type jczq                       # 竞彩足球
"""
from pathlib import Path

from _cli import base_parser, main, make_ctx, run, schedule_problem


def body():
    p = base_parser(__doc__.splitlines()[0], strategy=True)
    p.add_argument("--match", type=int, help="只生成某一场的详细报告（场次序号）")
    p.add_argument("--no-save", action="store_true", help="不写入历史预测库（试运行）")
    p.add_argument("--print", dest="print_md", action="store_true", help="直接输出 Markdown 报告而不是 JSON 摘要")
    args = p.parse_args()
    ctx = make_ctx(args)
    ctx.save = not args.no_save
    if args.match:
        from ren9 import report, store
        from ren9.common import data_dir

        st = run(ctx, "risk")
        bad = schedule_problem(ctx)
        if bad:
            return bad
        md = report.build_match(st, args.match)
        period = st["schedule"]["period"]
        path = data_dir() / "predictions" / f"{ctx.game_type}_{period}" / f"match_{args.match:02d}_{ctx.date}.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(md, encoding="utf-8")
        if args.print_md:
            print(md)
            return None
        return {"status": "ok", "period": period, "match": args.match, "markdown_path": str(path)}
    st = run(ctx, "report")
    bad = schedule_problem(ctx)
    if bad:
        return bad
    rep = st["report"]
    if args.print_md:
        print(Path(rep["markdown_path"]).read_text(encoding="utf-8"))
        return None
    return rep


if __name__ == "__main__":
    main(body)
