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
from ren9 import form, optimizer
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


def form_features(rows: list[dict]) -> dict[tuple, dict]:
    """为单个联赛的每场历史比赛计算赛前的状态差 / 战意差（主 − 客）。

    只使用该比赛日之前的比赛（同一天的比赛互不可见）；状态用 form.form_score、战意用
    form.league_motivation，与实时分析同一套公式。返回 {(date, home, away): {form_diff, motivation_gap}}。
    """
    season_of = lambda d: _season_start(date.fromisoformat(d))          # noqa: E731
    season_teams: dict[int, set] = {}
    for r in rows:
        season_teams.setdefault(season_of(r["date"]), set()).update((r["home"], r["away"]))
    recent: dict[str, list] = {}                 # 球队 -> [(赛果, 进球, 失球)]，新→旧，跨赛季延续
    tables: dict[int, dict[str, list]] = {}      # 赛季 -> 球队 -> [积分, 净胜球, 已赛]
    out: dict[tuple, dict] = {}
    i = 0
    while i < len(rows):
        j = i
        while j < len(rows) and rows[j]["date"] == rows[i]["date"]:
            j += 1
        season = season_of(rows[i]["date"])
        tab = tables.setdefault(season, {})
        ranked = sorted(tab, key=lambda t: (-tab[t][0], -tab[t][1]))
        total = len(season_teams[season])

        def side(team):
            games = recent.get(team, [])[:5]
            q = form.form_score([g[0] for g in games], [(g[1], g[2]) for g in games])["overall_form"]
            mot = form.league_motivation(ranked.index(team) + 1, total, tab[team][2])[0] if team in tab else None
            return q, mot

        for r in rows[i:j]:
            (hq, hm), (aq, am) = side(r["home"]), side(r["away"])
            out[(r["date"], r["home"], r["away"])] = {
                "form_diff": hq - aq if hq is not None and aq is not None else None,
                "motivation_gap": hm - am if hm is not None and am is not None else None}
        for r in rows[i:j]:
            for team, gf, ga in ((r["home"], r["hg"], r["ag"]), (r["away"], r["ag"], r["hg"])):
                res = "W" if gf > ga else "D" if gf == ga else "L"
                recent.setdefault(team, []).insert(0, (res, gf, ga))
                row = tab.setdefault(team, [0, 0, 0])
                row[0] += 3 if res == "W" else 1 if res == "D" else 0
                row[1] += gf - ga
                row[2] += 1
        i = j
    return out


def fit_form_adjust(samples: list[tuple[dict, float | None, float | None, str]]) -> dict | None:
    """在拟合集上最小化 Log Loss，求状态差 / 战意差的修正系数（约束为非负）。

    samples: [(集成概率, 状态差, 战意差, 实际结果)]；缺失的差值按 0 处理。
    """
    if len(samples) < 200:
        return None
    P = np.array([[p[k] for k in ensemble.ORDER] for p, *_ in samples])
    F = np.array([s[1] or 0.0 for s in samples])
    M = np.array([s[2] or 0.0 for s in samples])
    Y = np.array([ensemble.ORDER.index(s[3]) for s in samples])

    def loss(x):
        sig = x[0] * F + x[1] * M
        adj = P * np.stack([np.exp(sig), np.ones_like(sig), np.exp(-sig)], axis=1)
        adj = adj / adj.sum(axis=1, keepdims=True)
        return float(-np.mean(np.log(np.clip(adj[np.arange(len(Y)), Y], 1e-12, 1))))

    res = minimize(loss, np.array([0.1, 0.1]), method="L-BFGS-B", bounds=[(0.0, 1.0), (0.0, 1.0)])
    return {"form_coef": round(float(res.x[0]), 4), "motivation_coef": round(float(res.x[1]), 4),
            "fit_log_loss_before": round(loss(np.zeros(2)), 5), "fit_log_loss_after": round(float(res.fun), 5),
            "samples": len(samples)}


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
        feats = form_features(rows)
        for r in recs:
            r["league"] = code
            r.update(feats.get((r["date"], r["home"], r["away"]), {}))
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

    # 状态 / 战意修正系数：在拟合集上学习，在留出集上对比修正前后的 Log Loss
    def base(r):
        return ens(r, fit["weights"], fit["temperature"]) if fit else ens(r, initial, 1.0)

    form_fit = fit_form_adjust([(base(r), r.get("form_diff"), r.get("motivation_gap"), r["outcome"]) for r in fit_part])
    if form_fit:
        cfg = {"enabled": True, "form_coef": form_fit["form_coef"], "motivation_coef": form_fit["motivation_coef"],
               "max_shift": (mw.get("form_adjust") or {}).get("max_shift", 0.04), "source": "backtest"}
        adjusted = [(form.adjust_probabilities(base(r), r, cfg)[0], r["outcome"]) for r in hold]
        before = metrics.summarize([(base(r), r["outcome"]) for r in hold])
        after = metrics.summarize(adjusted)
        form_fit.update(holdout_before=before, holdout_after=after,
                        improves_holdout=after["log_loss"] < before["log_loss"])
        if not form_fit["improves_holdout"]:
            # 留出集上没有改善：不让未经验证的修正影响概率
            form_fit.update(form_coef=0.0, motivation_coef=0.0,
                            note="留出集 Log Loss 未改善，系数置 0（状态/战意只进入风险因子，不修正概率）")

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
        "learned": fit, "elo_draw_nu": nu, "selector_learned": sel_fit, "form_adjust_learned": form_fit,
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
    if apply and form_fit:
        save_learned({"form_adjust": {**form_fit, "fitted_at": now_iso(), "source": "backtest:" + ",".join(used)}})
    write_json(data_dir() / "history" / "backtest_summary.json", summary)
    return summary
