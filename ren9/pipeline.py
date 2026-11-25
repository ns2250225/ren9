"""流程编排：赛程 → 数据 → 赔率 → 概率 → 风险 → 筛选 → 优化 → 报告。

每个阶段的输出保存在 data/cache/runs/<玩法>_<期号>/<阶段>.json，后续阶段直接复用；
任一上游阶段重新计算后，下游阶段都会重新计算。
"""
from __future__ import annotations

from dataclasses import dataclass, field

from ren9 import odds as odds_mod
from ren9 import optimizer, predict, report, risk, schedule, selector, store, teamdata
from ren9.common import data_dir, now, now_iso, parse_dt, resolve_date, settings, write_json

ORDER = ("schedule", "team_data", "odds", "probabilities", "risk", "selection", "plans", "report")
STRATEGIES = ("conservative", "balanced", "aggressive")


@dataclass
class Ctx:
    date: str = "today"
    game_type: str = "ren9"
    period: str | None = None
    strategy: str | None = None
    all_strategies: bool = False
    budget: float | None = None
    refresh: bool = False
    manual: str | None = None
    seqs: list[int] | None = None          # 只重新分析指定场次
    save: bool = True                      # 报告阶段是否写入历史库
    stages: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.date = resolve_date(self.date)
        self.game_type = self.game_type or settings()["lottery"]["game_type"]
        self.strategy = self.strategy or settings()["strategy"]["default"]
        if self.strategy not in STRATEGIES:
            raise ValueError(f"未知策略 {self.strategy}，可选: {', '.join(STRATEGIES)}")

    @property
    def strategies(self) -> list[str]:
        return list(STRATEGIES) if self.all_strategies else [self.strategy]


def _schedule(ctx: Ctx) -> tuple[dict, bool]:
    """返回 (赛程, 是否需要重算下游)。"""
    if not ctx.manual:
        known = ctx.period or store.recall_period(ctx.game_type, ctx.date)
        saved = store.load_stage(ctx.game_type, known, "schedule") if known else None
        if saved and not saved.get("official", True) and (ctx.period or saved.get("complete")):
            return saved, False                  # 之前手动提供的赛单：继续沿用，不被官方接口覆盖
    sch = schedule.get_schedule(ctx.date, ctx.game_type, ctx.period, ctx.manual, ctx.refresh)
    return sch, bool(sch.get("changed")) or bool(ctx.manual)


def run(ctx: Ctx, upto: str = "report") -> dict:
    """运行到指定阶段，返回各阶段结果（ctx.stages）。"""
    if upto not in ORDER:
        raise ValueError(f"未知阶段 {upto}")
    target = ORDER.index(upto)
    sch, dirty = _schedule(ctx)
    ctx.stages["schedule"] = sch
    period = sch.get("period")
    gt = ctx.game_type
    if target == 0 or not period or not sch.get("matches"):
        return ctx.stages
    dirty = dirty or ctx.refresh

    def stage(name: str, compute, partial: bool = False):
        """复用已保存的阶段结果，除非：是目标阶段、上游已重算、要求刷新、或指定了重算场次。"""
        nonlocal dirty
        idx = ORDER.index(name)
        if idx > target:
            return None
        saved = store.load_stage(gt, period, name)
        if name == "probabilities" and saved and saved.get("config_fingerprint") != predict.config_fingerprint():
            dirty = True                       # 模型参数 / 权重已变化（如刚跑完回测），概率需要重算
        if saved is not None and not dirty and idx != target and not (ctx.seqs and partial):
            ctx.stages[name] = saved
            return saved
        result = compute(saved)
        store.save_stage(gt, period, name, result)
        ctx.stages[name] = result
        dirty = True
        return result

    stage("team_data", lambda prev: teamdata.collect(sch, ctx.refresh, ctx.seqs, prev), partial=True)
    stage("odds", lambda prev: odds_mod.collect(sch, ctx.stages["team_data"], ctx.refresh, ctx.seqs, prev), partial=True)
    stage("probabilities", lambda prev: predict.predict(sch, ctx.stages["team_data"], ctx.stages["odds"],
                                                        ctx.seqs, prev), partial=True)
    stage("risk", lambda prev: risk.assess(ctx.stages["probabilities"], ctx.stages["team_data"], ctx.stages["odds"]))

    if gt == "ren9":
        def do_select(prev):
            out = dict((prev or {}).get("strategies", {})) if not dirty else {}
            for s in ctx.strategies:
                out[s] = selector.select(sch, ctx.stages["probabilities"], ctx.stages["risk"],
                                         ctx.stages["team_data"], s)
            return {"period": period, "generated_at": now_iso(), "strategies": out}

        def do_plans(prev):
            out = dict((prev or {}).get("strategies", {})) if not dirty else {}
            for s in ctx.strategies:
                sel = ctx.stages["selection"]["strategies"][s]
                out[s] = optimizer.build_plan(sel, ctx.stages["probabilities"], ctx.stages["risk"],
                                              ctx.stages["odds"], s, ctx.budget)
            return {"period": period, "generated_at": now_iso(), "strategies": out}

        if target >= ORDER.index("selection"):
            saved = store.load_stage(gt, period, "selection")
            have = saved and not dirty and all(s in saved.get("strategies", {}) for s in ctx.strategies)
            if have and target != ORDER.index("selection"):
                ctx.stages["selection"] = saved
            else:
                ctx.stages["selection"] = do_select(saved)
                store.save_stage(gt, period, "selection", ctx.stages["selection"])
                dirty = True
        if target >= ORDER.index("plans"):
            saved = store.load_stage(gt, period, "plans")
            have = (saved and not dirty and ctx.budget is None
                    and all(s in saved.get("strategies", {}) for s in ctx.strategies))
            if have and target != ORDER.index("plans"):
                ctx.stages["plans"] = saved
            else:
                ctx.stages["plans"] = do_plans(saved)
                store.save_stage(gt, period, "plans", ctx.stages["plans"])

    if target >= ORDER.index("report"):
        ctx.stages["report"] = _report(ctx)
        store.save_stage(gt, period, "report", ctx.stages["report"])
    return ctx.stages


def _bundle(ctx: Ctx) -> dict:
    st = dict(ctx.stages)
    st["selections"] = {s: v for s, v in (st.get("selection") or {}).get("strategies", {}).items()
                        if s in ctx.strategies}
    st["plans"] = {s: v for s, v in (st.get("plans") or {}).get("strategies", {}).items() if s in ctx.strategies}
    return st


def _report(ctx: Ctx) -> dict:
    sch = ctx.stages["schedule"]
    gt, period = ctx.game_type, sch["period"]
    st = _bundle(ctx)
    if gt == "jczq":
        md, js = report.build_jczq(st)
    else:
        md, js = report.build_daily(st, ctx.strategy)
    out_dir = data_dir() / "predictions" / f"{gt}_{period}"
    stamp = now().strftime("%Y%m%d_%H%M%S")
    md_path = out_dir / f"report_{ctx.date}.md"
    json_path = out_dir / f"report_{ctx.date}.json"
    out_dir.mkdir(parents=True, exist_ok=True)
    md_path.write_text(md, encoding="utf-8")
    write_json(json_path, js)

    # 预测时点数据快照：保存当时用到的全部输入与输出，供复盘与防泄漏审计
    end = parse_dt(sch.get("sale_end"))
    kicks = [parse_dt(p.get("kickoff")) for p in ctx.stages["probabilities"]["matches"]]
    first_kick = min((k for k in kicks if k), default=None)
    post_deadline = bool((end and end < now()) or (gt == "jczq" and first_kick and first_kick < now()))
    snap_path = out_dir / f"snapshot_{stamp}.json"
    write_json(snap_path, {"created_at": now_iso(), "post_deadline": post_deadline,
                           **{k: ctx.stages.get(k) for k in ORDER[:-1]}})
    saved = False
    if ctx.save:
        risks = {r["match_id"]: r for r in ctx.stages["risk"]["matches"]}
        rows = [{**p, "risk_level": risks.get(p["match_id"], {}).get("risk_level")}
                for p in ctx.stages["probabilities"]["matches"]]
        store.save_predictions(gt, period, ctx.date, rows, post_deadline, str(snap_path))
        for s, pl in st["plans"].items():
            if pl.get("status") == "ok":
                store.save_plan(gt, period, ctx.date, pl, post_deadline)
        saved = True
    plan = st["plans"].get(ctx.strategy) if gt == "ren9" else None
    return {
        "date": ctx.date, "game_type": gt, "period": period, "generated_at": now_iso(),
        "status": ("ok" if gt == "jczq" or (plan and plan.get("status") == "ok")
                   else (plan or {}).get("status") or "no_plan"),
        "message": (plan or {}).get("message"),
        "markdown_path": str(md_path), "json_path": str(json_path), "snapshot_path": str(snap_path),
        "saved_to_history": saved, "post_deadline": post_deadline,
        "strategy": ctx.strategy if gt == "ren9" else None,
        "plans": {s: {"status": p.get("status"), "picks": optimizer.describe(p), "bet_count": p.get("bet_count"),
                      "total_cost": p.get("total_cost"), "hit_probability": p.get("hit_probability")}
                  for s, p in st["plans"].items()},
    }
