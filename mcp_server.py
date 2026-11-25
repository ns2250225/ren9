#!/usr/bin/env python
"""可选的 MCP Server（模式 B）。不是必需依赖：不支持 MCP 的 Agent 用 scripts/ 下的 CLI 即可。

启动（stdio）：
    pip install mcp
    python mcp_server.py

暴露的工具与 CLI 一一对应，返回值都是 JSON 可序列化的字典。
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

try:
    from mcp.server.fastmcp import FastMCP
except ImportError:  # pragma: no cover
    sys.stderr.write("未安装 mcp：pip install mcp（或直接使用 scripts/ 下的 CLI，功能相同）\n")
    raise SystemExit(1)

from ren9 import report as report_mod  # noqa: E402
from ren9 import review  # noqa: E402
from ren9.pipeline import Ctx, run  # noqa: E402

mcp = FastMCP("ren9-analyst")


def _ctx(date: str, game_type: str, period: str | None, refresh: bool, matches: list[int] | None = None,
         strategy: str | None = None, all_strategies: bool = False, budget: float | None = None) -> Ctx:
    return Ctx(date=date, game_type=game_type, period=period or None, refresh=refresh, seqs=matches or None,
               strategy=strategy, all_strategies=all_strategies, budget=budget)


def _guard(ctx: Ctx, stage: str) -> dict:
    try:
        st = run(ctx, stage)
    except Exception as exc:  # noqa: BLE001
        return {"status": "error", "error": type(exc).__name__, "message": str(exc)}
    sch = st.get("schedule") or {}
    if not sch.get("matches"):
        return {"status": "no_schedule", "issues": sch.get("issues", []), "notes": sch.get("notes", []),
                "action_required": sch.get("action_required")}
    return st


@mcp.tool()
def get_ren9_schedule(date: str = "today", period: str = "", game_type: str = "ren9", refresh: bool = False) -> dict:
    """获取传统足彩任九的官方 14 场赛单并校验（game_type=jczq 时返回竞彩在售比赛）。"""
    st = _guard(_ctx(date, game_type, period, refresh), "schedule")
    return st.get("schedule", st)


@mcp.tool()
def get_match_data(date: str = "today", period: str = "", game_type: str = "ren9", refresh: bool = False,
                   matches: list[int] | None = None) -> dict:
    """采集球队与比赛数据（排名、近况、攻防、xG、主客场、交锋、赛程、首发、Elo），每项带来源与时间。"""
    st = _guard(_ctx(date, game_type, period, refresh, matches), "team_data")
    return st.get("team_data", st)


@mcp.tool()
def get_odds(date: str = "today", period: str = "", game_type: str = "ren9", refresh: bool = False) -> dict:
    """采集赔率并计算隐含概率、水位、去水概率与赔率波动（官方与商业来源分开）。"""
    st = _guard(_ctx(date, game_type, period, refresh), "odds")
    return st.get("odds", st)


@mcp.tool()
def predict_match(date: str = "today", period: str = "", game_type: str = "ren9", refresh: bool = False,
                  matches: list[int] | None = None) -> dict:
    """计算胜平负概率（Elo + Poisson + Dixon-Coles + 市场去水 → 集成）。matches 只重算指定场次。"""
    st = _guard(_ctx(date, game_type, period, refresh, matches), "probabilities")
    return st.get("probabilities", st)


@mcp.tool()
def analyze_risk(date: str = "today", period: str = "", game_type: str = "ren9", refresh: bool = False) -> dict:
    """比赛风险分析，分级 LOW / MEDIUM / HIGH / UNKNOWN 并给出原因。"""
    st = _guard(_ctx(date, game_type, period, refresh), "risk")
    return st.get("risk", st)


@mcp.tool()
def select_nine_matches(date: str = "today", period: str = "", strategy: str = "balanced",
                        refresh: bool = False) -> dict:
    """从 14 场中严格选出 9 场、排除 5 场，返回全部 14 场的评分与理由。"""
    ctx = _ctx(date, "ren9", period, refresh, strategy=strategy)
    st = _guard(ctx, "selection")
    return st["selection"]["strategies"][ctx.strategy] if "selection" in st else st


@mcp.tool()
def optimize_bet(date: str = "today", period: str = "", strategy: str = "balanced", budget: float = 0,
                 all_strategies: bool = False, refresh: bool = False) -> dict:
    """为选中的 9 场分配单选 / 双选 / 全选，计算注数、成本与模型估计的全中概率。budget 为预算上限（元）。"""
    ctx = _ctx(date, "ren9", period, refresh, strategy=strategy, all_strategies=all_strategies,
               budget=budget or None)
    st = _guard(ctx, "plans")
    if "plans" not in st:
        return st
    plans = {s: st["plans"]["strategies"][s] for s in ctx.strategies}
    return plans[ctx.strategy] if len(plans) == 1 else {"status": "ok", "plans": plans}


@mcp.tool()
def generate_report(date: str = "today", period: str = "", game_type: str = "ren9", strategy: str = "balanced",
                    all_strategies: bool = False, budget: float = 0, refresh: bool = False,
                    match: int = 0) -> dict:
    """生成完整分析报告（Markdown + JSON）并保存预测记录。match>0 时只生成该场的详细报告。"""
    ctx = _ctx(date, game_type, period, refresh, strategy=strategy, all_strategies=all_strategies,
               budget=budget or None)
    if match:
        st = _guard(ctx, "risk")
        if "risk" not in st:
            return st
        return {"status": "ok", "markdown": report_mod.build_match(st, match)}
    st = _guard(ctx, "report")
    if "report" not in st:
        return st
    out = dict(st["report"])
    out["markdown"] = Path(out["markdown_path"]).read_text(encoding="utf-8")
    return out


@mcp.tool()
def review_predictions(period: str = "", summary: bool = False, allow_post_deadline: bool = False) -> dict:
    """赛后复盘某一期（官方开奖结果、概率指标、方案命中、真实 ROI）；summary=True 返回历史累计表现。"""
    if summary or not period:
        return review.aggregate("ren9")
    r = review.review_ren9(period, allow_post_deadline=allow_post_deadline)
    if r.get("status") == "ok":
        md = report_mod.build_review(r, review.aggregate("ren9"))
        r = {k: v for k, v in r.items() if k not in ("records", "model_records")}
        r["markdown"] = md
    return r


if __name__ == "__main__":
    mcp.run()
