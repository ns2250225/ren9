"""赔率去水与量化模型的数学性质。"""
import math
from datetime import date

import numpy as np
import pytest

from conftest import synthetic_league
from models import elo, ensemble, metrics
from models.dixon_coles import DixonColesModel
from models.poisson import PoissonModel, calibrate_matrix, handicap_probs, outcome_probs, score_matrix
from ren9 import odds

REF = date(2026, 11, 2)


# ---------- 赔率 ----------

def test_fair_probability_matches_prd_example():
    r = odds.analyze({"home": 1.85, "draw": 3.40, "away": 4.20})
    assert r["implied_probability"] == {"home": 0.5405, "draw": 0.2941, "away": 0.2381}
    # PRD 示例用四舍五入后的隐含概率相加得到 0.0727 / 0.2221；精确计算为 0.0728 / 0.2219
    assert r["overround"] == pytest.approx(0.0727, abs=3e-4)
    assert r["fair_probability"]["home"] == pytest.approx(0.5038, abs=3e-4)
    assert r["fair_probability"]["draw"] == pytest.approx(0.2741, abs=3e-4)
    assert r["fair_probability"]["away"] == pytest.approx(0.2221, abs=3e-4)
    assert abs(sum(r["fair_probability"].values()) - 1) < 1e-3


def test_invalid_odds_rejected():
    with pytest.raises(ValueError):
        odds.analyze({"home": 1.0, "draw": 3.0, "away": 4.0})
    with pytest.raises(ValueError):
        odds.analyze({"home": 2.0, "draw": None, "away": 4.0})


def test_official_and_commercial_sources_kept_separate(zc_text, fake_http, jc_text):
    from ren9 import schedule

    fake_http({"getFootBallMatchV1": zc_text, "getMatchCalculatorV1": jc_text})
    sch = schedule.get_schedule("2026-11-24", "ren9")
    td = {"matches": [{"match_id": sch["matches"][0]["match_id"], "resolution": {"status": "resolved"},
                       "espn_odds": [{"provider": "DraftKings", "current": {"home": 1.48, "draw": 5.25, "away": 5.5},
                                      "open": {"home": 1.51, "draw": 4.9, "away": 5.0}, "fetched_at": "t"}]}]}
    out = odds.collect(sch, td)
    m1 = out["matches"][0]
    kinds = {s["source"]: s["kind"] for s in m1["sources"]}
    assert kinds == {"sporttery_jc_had": "official", "espn_draftkings": "commercial"}
    assert all(s["fetched_at"] for s in m1["sources"])
    assert m1["market"]["official_sources"] == 1 and m1["market"]["commercial_sources"] == 1
    assert abs(sum(m1["market"]["fair_probability"].values()) - 1) < 1e-3
    assert m1["movement"]["known"] and m1["movement"]["max_change"] > 0
    assert m1["jc_handicap"]["line"] == -1.0
    # 没有任何赔率的比赛：market 为 None，而不是 0
    m14 = out["matches"][13]
    assert m14["available"] is False and m14["market"] is None


# ---------- Elo ----------

def test_elo_probabilities():
    p = elo.davidson_probs(1750, 1620, 65, 0.78)
    assert abs(sum(p.values()) - 1) < 1e-12
    assert p["win"] > p["draw"] > p["loss"]
    even = elo.davidson_probs(1500, 1500, 0, 0.78)
    assert abs(even["win"] - even["loss"]) < 1e-12
    assert elo.davidson_probs(1900, 1500)["win"] > elo.davidson_probs(1700, 1500)["win"]
    r = elo.predict(1750, 1620)
    assert r["elo_difference"] == 130


def test_elo_ratings_follow_results():
    rows = synthetic_league()
    er = elo.EloRatings().fit(rows)
    assert er.get("T00") > er.get("T11") + 100
    assert abs(sum(er.ratings.values()) / len(er.ratings) - 1500) < 1e-6     # 零和更新


# ---------- Poisson ----------

def test_score_matrix_and_outcomes():
    m = score_matrix(1.65, 0.92)
    assert abs(m.sum() - 1) < 1e-12
    p = outcome_probs(m)
    assert abs(sum(p.values()) - 1) < 1e-12 and p["win"] > p["loss"]
    h = handicap_probs(m, -1)
    assert abs(sum(h.values()) - 1) < 1e-12 and h["win"] < p["win"]
    assert abs(handicap_probs(m, 0)["draw"] - p["draw"]) < 1e-12


def test_calibrated_matrix_keeps_handicap_consistent_with_final_probs():
    target = {"win": 0.67, "draw": 0.18, "loss": 0.15}
    m = calibrate_matrix(score_matrix(1.1, 1.4), target)          # 进球模型本身看好客队
    assert outcome_probs(m) == pytest.approx(target, abs=1e-9)
    minus1, plus1 = handicap_probs(m, -1), handicap_probs(m, 1)
    assert minus1["loss"] == pytest.approx(1 - target["win"], abs=1e-9)     # 让 1 球输盘 = 没赢
    assert plus1["win"] == pytest.approx(target["win"] + target["draw"], abs=1e-9)
    assert minus1["win"] < target["win"]


def test_dixon_coles_low_score_adjustment():
    base, adj = score_matrix(1.3, 1.1), score_matrix(1.3, 1.1, rho=-0.1)
    assert adj[0, 0] > base[0, 0] and adj[1, 1] > base[1, 1]        # ρ<0 提高 0:0 与 1:1
    assert adj[1, 0] < base[1, 0] and adj[0, 1] < base[0, 1]
    assert abs(adj.sum() - 1) < 1e-12


def test_poisson_model_orders_teams():
    rows = synthetic_league()
    pm = PoissonModel().fit(rows, REF)
    strong, weak = pm.predict("T00", "T11"), pm.predict("T11", "T00")
    assert strong["probabilities"]["win"] > 0.6 > weak["probabilities"]["win"]
    assert strong["home_expected_goals"] > strong["away_expected_goals"]
    assert pm.predict("T00", "不存在") is None


def test_time_weight_excludes_future_matches():
    rows = synthetic_league()
    cutoff = date(2026, 6, 1)
    pm = PoissonModel().fit(rows, cutoff)
    past = sum(1 for r in rows if r["date"] <= cutoff.isoformat() and "T00" in (r["home"], r["away"]))
    assert pm.matches_of("T00") == past            # ref 之后的比赛权重为 0，不参与拟合


# ---------- Dixon-Coles ----------

def test_dixon_coles_fit_and_predict():
    rows = synthetic_league()
    dc = DixonColesModel().fit(rows, REF)
    assert dc.fitted and dc.converged
    assert dc.home > 0                                    # 学到了主场优势
    assert -0.2 <= dc.rho <= 0.2
    i0, i11 = dc.idx["T00"], dc.idx["T11"]
    assert dc.att[i0] > dc.att[i11] and dc.dfn[i0] < dc.dfn[i11]
    r = dc.predict("T00", "T11")
    assert abs(sum(r["probabilities"].values()) - 1) < 1e-9
    assert r["probabilities"]["win"] > 0.6
    assert set(r["low_scores"]) == {"0:0", "1:0", "0:1", "1:1"}


def test_dixon_coles_needs_enough_data():
    assert DixonColesModel().fit(synthetic_league()[:10], REF).fitted is False


def test_dixon_coles_analytic_gradient_matches_numeric():
    """用有限差分验证似然函数的解析梯度。"""
    from scipy.optimize import approx_fprime

    captured = {}
    import models.dixon_coles as mod

    orig = mod.minimize

    def spy(fun, x0, **kw):
        captured["fun"], captured["x0"] = fun, x0
        return orig(fun, x0, **kw)

    mod.minimize = spy
    try:
        DixonColesModel().fit(synthetic_league(n_teams=6, rounds=2), REF)
    finally:
        mod.minimize = orig
    fun = captured["fun"]
    rng = np.random.default_rng(0)
    x = captured["x0"] + rng.normal(0, 0.1, len(captured["x0"]))
    x[-1] = -0.05
    _, g = fun(x)
    num = approx_fprime(x, lambda p: fun(p)[0], 1e-6)
    assert np.allclose(g, num, rtol=1e-3, atol=1e-3)


# ---------- 集成 ----------

def test_ensemble_renormalizes_missing_models():
    probs = {"dixon_coles": {"win": 0.6, "draw": 0.25, "loss": 0.15},
             "market": {"win": 0.5, "draw": 0.3, "loss": 0.2}}
    w = {"elo": 0.15, "poisson": 0.20, "dixon_coles": 0.35, "market": 0.30}
    r = ensemble.combine(probs, w)
    assert abs(sum(r["probabilities"].values()) - 1) < 1e-12
    assert r["weights_used"] == {"dixon_coles": 0.5385, "market": 0.4615}
    assert abs(r["probabilities"]["win"] - (0.35 * 0.6 + 0.30 * 0.5) / 0.65) < 1e-12
    assert ensemble.combine({}, w) is None


def test_temperature_calibration():
    p = {"win": 0.6, "draw": 0.25, "loss": 0.15}
    flat, sharp = ensemble.apply_temperature(p, 2.0), ensemble.apply_temperature(p, 0.5)
    assert flat["win"] < p["win"] < sharp["win"]
    assert abs(sum(flat.values()) - 1) < 1e-12
    assert ensemble.apply_temperature(p, 1.0) == pytest.approx(p)


def test_agreement_and_confidence():
    same = {"a": {"win": 0.6, "draw": 0.25, "loss": 0.15}, "b": {"win": 0.6, "draw": 0.25, "loss": 0.15}}
    diff = {"a": {"win": 0.7, "draw": 0.2, "loss": 0.1}, "b": {"win": 0.15, "draw": 0.25, "loss": 0.6}}
    assert ensemble.agreement(same) == 1.0
    assert ensemble.agreement(diff) < 0.3
    assert ensemble.agreement({"a": same["a"]}) is None       # 单模型：一致性未知
    c = ensemble.confidence(same, same["a"], 4)
    assert 0 <= c <= 1


def test_fit_weights_prefers_the_informative_model():
    rng = np.random.default_rng(1)
    samples = []
    for _ in range(600):
        true = rng.dirichlet([4, 2.5, 2.5])
        outcome = ("win", "draw", "loss")[rng.choice(3, p=true)]
        good = dict(zip(("win", "draw", "loss"), true))
        noise = dict(zip(("win", "draw", "loss"), rng.dirichlet([1, 1, 1])))
        samples.append(({"good": good, "noise": noise}, outcome))
    fit = ensemble.fit_weights(samples, ["good", "noise"])
    assert fit["weights"]["good"] > 0.8
    assert abs(sum(fit["weights"].values()) - 1) < 1e-3


# ---------- 指标 ----------

def test_metrics():
    p = {"win": 0.5, "draw": 0.3, "loss": 0.2}
    assert metrics.brier(p, "win") == pytest.approx(0.25 + 0.09 + 0.04)
    assert metrics.log_loss(p, "win") == pytest.approx(-math.log(0.5))
    assert metrics.rps(p, "win") == pytest.approx((0.5 ** 2 + 0.2 ** 2) / 2)
    assert metrics.rps(p, "loss") > metrics.rps(p, "draw") > 0     # 顺序敏感：预测越远惩罚越大
    perfect = {"win": 1.0, "draw": 0.0, "loss": 0.0}
    assert metrics.brier(perfect, "win") == 0 and metrics.rps(perfect, "win") == 0
    s = metrics.summarize([(p, "win"), (p, "loss")])
    assert s["n"] == 2 and s["accuracy"] == 0.5
    bins = metrics.calibration_bins([(p, "win")] * 10)
    assert sum(b["count"] for b in bins) == 30
    assert metrics.summarize([]) is None
