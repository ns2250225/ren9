"""时间切分回测（walk-forward）。

对每个联赛按周滚动：只用该周之前的比赛拟合模型，预测该周比赛；市场概率只用赛前赔率
（非收盘赔率）。全部预测按时间排序后，前 60% 用于拟合集成权重与校准温度，后 40% 作为
留出集评估。任何时候训练数据都严格早于被预测的比赛，避免数据泄漏。
"""
from __future__ import annotations

from datetime import date, timedelta

import numpy as np
from scipy.optimize import minimize

from models import elo as elo_model
from models import ensemble, metrics
from models.dixon_coles import DixonColesModel
from models.poisson import PoissonModel
from ren9 import optimizer
from ren9.common import data_dir, load_yaml, model_weights, now_iso, save_learned, write_json
from ren9.odds import analyze
from ren9.risk import closeness
from ren9.selector import probability_score
from providers import football_data
from providers.base import ProviderError

MODELS = ("elo", "poisson", "dixon_coles", "market")


def _season_start(ref: date) -> int:
    return ref.year if ref.month >= 7 else ref.year - 1


def load_league(code: str, ref: date, seasons: int) -> list[dict]:
    rows = []
    start = _season_start(ref)
    for i in range(seasons):
        y = start - i
        try:
            r, _ = football_data.league_season(code, f"{y % 100:02d}{(y + 1) % 100:02d}", current=(i == 0))
        except ProviderError:
            continue
        rows.extend(r)
    return sorted((r for r in rows if r["date"] < ref.isoformat()), key=lambda r: r["date"])


def walk_forward(rows: list[dict], test_from: str, step_days: int = 7, window_days: int = 730) -> list[dict]:
    """返回 [{date, home, away, outcome, probs: {模型: 概率}}]。"""
    mw = model_weights()
    pc, dc, ec = mw.get("poisson", {}), mw.get("dixon_coles", {}), mw.get("elo", {})
    hfa, nu = ec.get("home_advantage", 65), ec.get("draw_nu", 0.78)
    elo = elo_model.EloRatings(ec.get("k_factor", 20), hfa, ec.get("initial_rating", 1500))
    out: list[dict] = []
    i = 0
    while i < len(rows) and rows[i]["date"] < test_from:      # 预热期只更新 Elo
        elo.update(rows[i]["home"], rows[i]["away"], rows[i]["hg"], rows[i]["ag"])
        i += 1
    while i < len(rows):
        block_start = date.fromisoformat(rows[i]["date"])
        block_end = (block_start + timedelta(days=step_days)).isoformat()
        lo = (block_start - timedelta(days=window_days)).isoformat()
        train = [r for r in rows[:i] if r["date"] >= lo]
        pm = PoissonModel(pc.get("half_life_days", 240), pc.get("shrink_matches", 4), pc.get("xg_blend", 0.4)).fit(train, block_start)
        dm = DixonColesModel(dc.get("half_life_days", 240), dc.get("ridge", 0.6)).fit(train, block_start)
        j = i
        while j < len(rows) and rows[j]["date"] < block_end:
            r = rows[j]
            probs: dict[str, dict] = {}
            if min(elo.games.get(r["home"], 0), elo.games.get(r["away"], 0)) >= 4:
                probs["elo"] = elo_model.davidson_probs(elo.get(r["home"]), elo.get(r["away"]), hfa, nu)
            for name, model in (("poisson", pm), ("dixon_coles", dm)):
                if model.fitted and min(model.matches_of(r["home"]), model.matches_of(r["away"])) >= 4:
                    p = model.predict(r["home"], r["away"])
                    if p:
                        probs[name] = p["probabilities"]
            if r.get("odds_pre"):
                f = analyze(r["odds_pre"])["fair_probability"]
                probs["market"] = {"win": f["home"], "draw": f["draw"], "loss": f["away"]}
            outcome = "win" if r["hg"] > r["ag"] else "draw" if r["hg"] == r["ag"] else "loss"
            out.append({"date": r["date"], "home": r["home"], "away": r["away"], "outcome": outcome, "probs": probs,
                        "elo": (elo.get(r["home"]), elo.get(r["away"]))})
            j += 1
        for r in rows[i:j]:                                    # 本周比赛结束后才更新 Elo
            elo.update(r["home"], r["away"], r["hg"], r["ag"])
        i = j
    return out


def fit_selector_weights(samples: list[tuple[float, float, float, int]]) -> dict | None:
    """逻辑回归：首选是否命中 ~ 概率优势 + 模型一致性 − 风险（系数约束为非负）。"""
    if len(samples) < 200:
        return None
    X = np.array([[s[0], s[1], -s[2]] for s in samples])
    y = np.array([s[3] for s in samples], dtype=float)

    def nll(b):
        z = b[0] + X @ b[1:]
        return float(np.mean(np.log1p(np.exp(-np.where(y == 1, z, -z)))) + 1e-3 * np.sum(b[1:] ** 2))

    res = minimize(nll, np.array([0.0, 1.0, 0.5, 0.5]), method="L-BFGS-B",
                   bounds=[(None, None), (0, None), (0, None), (0, None)])
    b = np.maximum(res.x[1:], 0)
    if b.sum() <= 0:
        return None
    q = 0.15                                   # 回测数据的数据质量恒定，无法学习，保留默认权重
    b = b / b.sum() * (1 - q)
    return {"weights": {"probability": round(float(b[0]), 4), "agreement": round(float(b[1]), 4),
                        "data_quality": q, "risk": round(float(b[2]), 4)}, "samples": len(samples)}


def simulate_ren9(records: list[dict], final_probs: list[dict], sel_weights: dict) -> dict:
    """把留出集按时间每 14 场组成一组，模拟 14 选 9 + 组合优化的命中情况。"""
    coupons = len(records) // 14
    res = {s: {"hits": 0, "bets": 0} for s in ("conservative", "balanced", "aggressive")}
    for c in range(coupons):
        chunk = list(range(c * 14, c * 14 + 14))
        scored = []
        for idx in chunk:
            p = final_probs[idx]
            agr = ensemble.agreement(records[idx]["probs"])
            score = (sel_weights["probability"] * probability_score(p)
                     + sel_weights["agreement"] * (0.5 if agr is None else agr)
                     - sel_weights["risk"] * closeness(p))
            scored.append((score, idx))
        chosen = sorted(idx for _, idx in sorted(scored, reverse=True)[:9])
        matches = [{"seq": k + 1, "home_team": records[idx]["home"], "away_team": records[idx]["away"],
                    "probabilities": final_probs[idx], "market": records[idx]["probs"].get("market")}
                   for k, idx in enumerate(chosen)]
        for s in res:
            plan = optimizer.optimize(matches, s)
            hit = all(records[idx]["outcome"] in sel["picks"] for idx, sel in zip(chosen, plan["selections"]))
            res[s]["hits"] += 1 if hit else 0
            res[s]["bets"] += plan["bet_count"]
    return {"coupons": coupons,
            "strategies": {s: {"hits": v["hits"], "avg_bets": v["bets"] / coupons if coupons else 0}
                           for s, v in res.items()}}


def run(leagues: list[str] | None = None, ref: date | None = None, seasons: int = 3, step_days: int = 7,
        apply: bool = True) -> dict:
    ref = ref or date.today()
    leagues = leagues or ["E0", "E1", "D1", "I1", "SP1", "F1"]
    records: list[dict] = []
    used = []
    for code in leagues:
        rows = load_league(code, ref, seasons)
        if len(rows) < 300:
            continue
        # 最早的一个赛季只做预热，之后的比赛才作为被预测对象
        test_from = date(_season_start(ref) - (seasons - 2), 7, 1).isoformat()
        recs = walk_forward(rows, test_from, step_days)
        for r in recs:
            r["league"] = code
        records.extend(recs)
        used.append(code)
    records = [r for r in records if all(m in r["probs"] for m in MODELS)]
    records.sort(key=lambda r: r["date"])
    if len(records) < 200:
        return {"status": "insufficient", "samples": len(records),
                "message": "可用于回测的历史比赛不足（数据源不可用或联赛过少）"}
    cut = int(len(records) * 0.6)
    fit_part, hold = records[:cut], records[cut:]
    mw = load_yaml("model_weights.yaml")
    initial = mw["ensemble"]["initial"]
    cap = {"market": mw["ensemble"].get("max_market_weight", 0.6)}
    floor = mw["ensemble"].get("min_model_weight", 0.05)
    fit = ensemble.fit_weights([(r["probs"], r["outcome"]) for r in fit_part], list(MODELS), cap, floor)
    if fit:
        fit["constraints"] = {"max_market_weight": cap["market"], "min_model_weight": floor}
    nu = elo_model.fit_draw_nu([(r["elo"][0], r["elo"][1], r["outcome"]) for r in fit_part],
                               mw["elo"].get("home_advantage", 65))

    def ens(r, weights, temp):
        return ensemble.combine(r["probs"], weights, temp)["probabilities"]

    hold_final = [ens(r, fit["weights"], fit["temperature"]) for r in hold] if fit else None
    holdout = {m: metrics.summarize([(r["probs"][m], r["outcome"]) for r in hold]) for m in MODELS}
    holdout["ensemble_initial"] = metrics.summarize([(ens(r, initial, 1.0), r["outcome"]) for r in hold])
    if fit:
        pairs = list(zip(hold_final, (r["outcome"] for r in hold)))
        holdout["ensemble"] = metrics.summarize(pairs)
    else:
        pairs = [(ens(r, initial, 1.0), r["outcome"]) for r in hold]
        holdout["ensemble"] = holdout["ensemble_initial"]

    sel_samples = []
    for r in fit_part:
        p = ens(r, fit["weights"], fit["temperature"]) if fit else ens(r, initial, 1.0)
        agr = ensemble.agreement(r["probs"])
        sel_samples.append((probability_score(p), 0.5 if agr is None else agr, closeness(p),
                            1 if metrics.top_pick(p) == r["outcome"] else 0))
    sel_fit = fit_selector_weights(sel_samples)
    sel_w = (sel_fit or {}).get("weights") or mw["selector"]["initial"]
    summary = {
        "status": "ok", "generated_at": now_iso(), "leagues": used, "samples": len(records),
        "fit_samples": len(fit_part), "holdout_samples": len(hold),
        "test_range": [records[0]["date"], records[-1]["date"]],
        "holdout_range": [hold[0]["date"], hold[-1]["date"]],
        "split": "按时间排序，前 60% 拟合集成权重与校准温度，后 40% 留出评估",
        "learned": fit, "elo_draw_nu": nu, "selector_learned": sel_fit,
        "holdout": holdout,
        "calibration": metrics.calibration_bins(pairs),
        "ece": metrics.expected_calibration_error(pairs),
        "top_pick_buckets": metrics.top_pick_buckets(pairs),
        "ren9_simulation": simulate_ren9(hold, [p for p, _ in pairs], sel_w),
        "leakage_controls": [
            "模型只用被预测比赛所在周之前的比赛拟合",
            "市场概率使用赛前赔率（非收盘赔率）",
            "Elo 在每周比赛全部预测完之后才更新",
            "集成权重/温度在时间更早的 60% 上拟合，在更晚的 40% 上评估",
        ],
    }
    # 只有学习到的集成在留出集上不差于初始权重时才写回配置
    better = bool(fit) and holdout["ensemble"]["log_loss"] <= holdout["ensemble_initial"]["log_loss"]
    summary["applied"] = bool(apply and better)
    if apply and better:
        learned = {"ensemble": {**fit, "fitted_at": now_iso(), "source": "backtest:" + ",".join(used)},
                   "elo_draw_nu": nu}
        if sel_fit:
            learned["selector"] = sel_fit
        save_learned(learned)
    write_json(data_dir() / "history" / "backtest_summary.json", summary)
    return summary
