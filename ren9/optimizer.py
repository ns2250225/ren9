"""投注组合优化：为选出的 9 场分配单选 / 双选 / 全选。

目标：在注数上限（预算）与双选/全选场次上限内，最大化
    Π_i f_i(S_i) / N^λ ，N = Π_i |S_i|
其中 f_i(S) = Σ_{o∈S} p_i(o)·q_i(o)^(-α)，p 为模型概率，q 为市场概率（无赔率时取 p）。
α = 0 时 f 就是该场的覆盖概率，目标即 9 场全中的概率；α > 0 时偏向市场低估的结果（冷门价值）。
λ 为成本厌恶：只有覆盖率提升超过 2^λ 倍才值得把单选升为双选。
9 场 × 3 种选择数共 3^9 = 19683 种配置，直接穷举求精确最优。
"""
from __future__ import annotations

import itertools
import math

from ren9.common import OUTCOMES, OUTCOME_ZH, now_iso, picks_zh, settings


def bet_count(selections: list[list[str]]) -> int:
    """复式注数 = 各场选择数的乘积。"""
    n = 1
    for s in selections:
        if not 1 <= len(s) <= 3 or len(set(s)) != len(s):
            raise ValueError(f"每场必须选择 1~3 个不重复的结果: {s}")
        n *= len(s)
    return n


def preset(strategy: str, budget: float | None = None) -> dict:
    cfg = settings()
    presets = cfg.get("strategy", {}).get("presets", {})
    if strategy not in presets:
        raise ValueError(f"未知策略 {strategy}，可选: {sorted(presets)}")
    p = dict(presets[strategy])
    unit = cfg["lottery"]["unit_price"]
    if budget is not None:
        if budget < unit:
            raise ValueError(f"预算 {budget} 元不足一注（{unit} 元）")
        p["max_bets"] = int(budget // unit)
        p["budget_source"] = "用户指定"
    else:
        p["budget_source"] = "策略默认"
    p["unit_price"] = unit
    return p


def _options(p: dict, q: dict | None, alpha: float) -> list[tuple[list[str], float, float]]:
    """某一场 k=1,2,3 时的最优选项集合：[(选项, 目标值 f, 覆盖概率)]。"""
    q = q or p
    value = {o: p[o] * max(q[o], 1e-6) ** (-alpha) for o in OUTCOMES}
    order = sorted(OUTCOMES, key=lambda o: (-value[o], OUTCOMES.index(o)))
    out = []
    for k in (1, 2, 3):
        pick = [o for o in OUTCOMES if o in order[:k]]
        out.append((pick, sum(value[o] for o in pick), sum(p[o] for o in pick)))
    return out


def optimize(matches: list[dict], strategy: str = "balanced", budget: float | None = None) -> dict:
    """matches: 选中的 9 场，每项含 seq / home_team / away_team / probabilities / market(可无) / risk_level。"""
    ps = preset(strategy, budget)
    max_bets, max_d, max_t = ps["max_bets"], ps.get("max_doubles", 9), ps.get("max_triples", 9)
    lam, alpha = ps.get("cost_aversion", 0.0), ps.get("cold_alpha", 0.0)
    opts = [_options(m["probabilities"], m.get("market"), alpha) for m in matches]
    n = len(matches)
    best, best_key = None, None
    for ks in itertools.product((0, 1, 2), repeat=n):
        doubles, triples = ks.count(1), ks.count(2)
        if doubles > max_d or triples > max_t:
            continue
        bets = (2 ** doubles) * (3 ** triples)
        if bets > max_bets:
            continue
        obj = sum(math.log(max(opts[i][k][1], 1e-12)) for i, k in enumerate(ks)) - lam * math.log(bets)
        key = (round(obj, 10), -bets)
        if best_key is None or key > best_key:
            best, best_key = ks, key
    if best is None:
        raise ValueError("预算不足以生成任何方案")

    selections, hit = [], 1.0
    for m, o, k in zip(matches, opts, best):
        picks, _, cover = o[k]
        hit *= cover
        top = max(OUTCOMES, key=lambda x: m["probabilities"][x])
        selections.append(
            {
                "seq": m["seq"], "match_id": m.get("match_id"),
                "match": f"{m['home_team']} vs {m['away_team']}",
                "picks": picks, "picks_zh": picks_zh(picks),
                "type": ("单选", "双选", "全选")[k],
                "coverage": round(cover, 4),
                "includes_cold": any(p != top and m["probabilities"][p] < 0.30 for p in picks) and k < 2,
                "risk_level": m.get("risk_level"),
            }
        )
    bets = bet_count([s["picks"] for s in selections])
    singles_hit = 1.0
    for m in matches:
        singles_hit *= max(m["probabilities"].values())
    unit = ps["unit_price"]
    return {
        "strategy": strategy,
        "generated_at": now_iso(),
        "selections": selections,
        "singles": sum(1 for s in selections if s["type"] == "单选"),
        "doubles": sum(1 for s in selections if s["type"] == "双选"),
        "triples": sum(1 for s in selections if s["type"] == "全选"),
        "bet_count": bets,
        "unit_price": unit,
        "total_cost": bets * unit,
        "max_bets": max_bets,
        "budget": max_bets * unit,
        "budget_source": ps["budget_source"],
        # 模型估计的 9 场全中概率（假设各场独立）；是估计值，不是保证
        "hit_probability": round(hit, 6),
        "all_singles_hit_probability": round(singles_hit, 6),
        "params": {"cost_aversion": lam, "cold_alpha": alpha, "max_doubles": max_d, "max_triples": max_t},
        "note": "多选会提高组合覆盖率，但成本同比例增加，并不代表一定提高最终收益。",
    }


def build_plan(selection: dict, predictions: dict, risk: dict, odds: dict | None, strategy: str,
               budget: float | None = None) -> dict:
    """由筛选结果生成一套完整任九方案（PRD 8.3 结构）。"""
    base = {"date": selection.get("date"), "period": selection.get("period"), "strategy": strategy,
            "generated_at": now_iso()}
    if selection.get("status") != "ok":
        return {**base, "status": selection.get("status"), "selected_matches": [], "excluded_matches": [],
                "selections": [], "bet_count": 0, "unit_price": settings()["lottery"]["unit_price"],
                "total_cost": 0, "message": selection.get("message")}
    preds = {x["match_id"]: x for x in predictions.get("matches", [])}
    risks = {x["match_id"]: x for x in risk.get("matches", [])}
    od = {x["match_id"]: x for x in (odds or {}).get("matches", [])}
    chosen = []
    for row in selection["matches"]:
        if not row["selected"]:
            continue
        mk = (od.get(row["match_id"]) or {}).get("market")
        chosen.append(
            {
                "seq": row["seq"], "match_id": row["match_id"], "home_team": row["home_team"],
                "away_team": row["away_team"], "probabilities": preds[row["match_id"]]["probabilities"],
                "market": ({"win": mk["fair_probability"]["home"], "draw": mk["fair_probability"]["draw"],
                            "loss": mk["fair_probability"]["away"]} if mk else None),
                "risk_level": risks.get(row["match_id"], {}).get("risk_level"),
            }
        )
    plan = optimize(chosen, strategy, budget)
    return {**base, "status": "ok", "selected_matches": selection["selected"],
            "excluded_matches": [e["seq"] for e in selection["excluded"]], **plan}


def describe(plan: dict) -> str:
    return " / ".join(f"{s['seq']}:{s['picks_zh']}" for s in plan.get("selections", []))


__all__ = ["bet_count", "optimize", "build_plan", "preset", "describe", "OUTCOME_ZH"]
