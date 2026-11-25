"""CLI 公共部分：把项目根目录加入 sys.path、统一参数、统一 JSON 输出与错误处理。"""
from __future__ import annotations

import argparse
import sys
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ren9 import store  # noqa: E402
from ren9.common import emit, setup_stdio  # noqa: E402
from ren9.pipeline import Ctx, run  # noqa: E402


def base_parser(desc: str, strategy: bool = False) -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=desc)
    p.add_argument("--date", default="today", help="today / yesterday / tomorrow / YYYY-MM-DD（北京时间）")
    p.add_argument("--game-type", default="ren9", choices=["ren9", "jczq"], help="ren9=传统足彩任九；jczq=竞彩足球")
    p.add_argument("--period", help="传统足彩期号（如 26169）；缺省按日期自动选择在售期")
    p.add_argument("--manual", help="手动赛单文件路径（官方赛单不可用时使用）")
    p.add_argument("--refresh", action="store_true", help="忽略缓存，重新抓取并重算全部阶段")
    p.add_argument("--matches", help="只重新分析指定场次，逗号分隔，如 3,8")
    p.add_argument("--full", action="store_true", help="输出完整 JSON（默认输出摘要，完整结果在 output_file）")
    if strategy:
        p.add_argument("--strategy", choices=["conservative", "balanced", "aggressive"], help="策略，默认 balanced")
        p.add_argument("--all-strategies", action="store_true", help="同时生成保守/均衡/激进三套方案")
        p.add_argument("--budget", type=float, help="预算上限（元），覆盖策略默认注数上限")
    return p


def make_ctx(args) -> Ctx:
    seqs = [int(x) for x in args.matches.split(",") if x.strip()] if getattr(args, "matches", None) else None
    return Ctx(
        date=args.date, game_type=args.game_type, period=args.period,
        strategy=getattr(args, "strategy", None), all_strategies=getattr(args, "all_strategies", False),
        budget=getattr(args, "budget", None), refresh=args.refresh, manual=args.manual, seqs=seqs,
    )


def stage_file(ctx: Ctx, stage: str) -> str | None:
    period = (ctx.stages.get("schedule") or {}).get("period")
    return str(store.run_dir(ctx.game_type, period) / f"{stage}.json") if period else None


def schedule_problem(ctx: Ctx) -> dict | None:
    """赛程不可用时返回统一的错误输出（提示用户手动提供赛单）。"""
    sch = ctx.stages.get("schedule") or {}
    if sch.get("matches"):
        return None
    return {"status": "no_schedule", "date": sch.get("date"), "game_type": sch.get("game_type"),
            "complete": False, "issues": sch.get("issues", []), "notes": sch.get("notes", []),
            "action_required": sch.get("action_required")}


def main(fn) -> None:
    """运行脚本主体；任何异常都以 JSON 形式输出，退出码 1。"""
    setup_stdio()
    try:
        result = fn()
        if result is not None:
            emit(result)
        if isinstance(result, dict) and result.get("status") in ("error", "no_schedule"):
            sys.exit(2)
    except SystemExit:
        raise
    except Exception as exc:  # noqa: BLE001
        emit({"status": "error", "error": type(exc).__name__, "message": str(exc),
              "trace": traceback.format_exc().splitlines()[-3:]})
        sys.exit(1)


__all__ = ["base_parser", "make_ctx", "stage_file", "schedule_problem", "main", "run", "Ctx", "store"]
