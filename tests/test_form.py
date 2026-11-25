"""近况 / 战意分析，以及它对概率、风险的影响。"""
from __future__ import annotations

import pytest

from conftest import make_pred
from ren9 import backtest, form, predict, risk

CFG = {"enabled": True, "form_coef": 0.15, "motivation_coef": 0.15, "max_shift": 0.04, "source": "test"}
P = {"win": 0.45, "draw": 0.28, "loss": 0.27}


def team(results, scores=None, standing=None):
    last5 = {"results": results, "scope": "全部赛事"}
    if scores:
        last5["games"] = [{"result": r, "score": s, "home": True} for r, s in zip(results, scores)]
    return {"name": "T", "form": {"last5": last5, "last10": None}, "standing": standing}


def match(home, away, ctype="league", competition="英超"):
    return {"match_id": "m1", "home_team": "主", "away_team": "客", "competition": competition,
            "competition_type": ctype, "teams": {"home": home, "away": away}}


def test_game_goals_follow_result_not_score_order():
    # ESPN 的比分常为胜方在前：主场 1-0 输球，本队进 0 失 1
    assert form.game_goals({"result": "L", "score": "1-0", "home": True}) == (0, 1)
    assert form.game_goals({"result": "W", "score": "3-1"}) == (3, 1)
    assert form.game_goals({"result": "D", "score": "2-2"}) == (2, 2)
    assert form.game_goals({"result": "W", "score": None}) is None
    assert form.game_goals({"goals_for": 2, "goals_against": 5, "result": "L"}) == (2, 5)


def test_form_score_uses_goals_and_needs_enough_games():
    assert form.form_score(["W", "L"])["overall_form"] is None
    only_results = form.form_score(["W", "W", "D", "L", "W"])
    assert only_results["basis"] == "仅赛果" and only_results["offensive_form"] is None
    strong = form.form_score(["W"] * 5, [(3, 0)] * 5)
    weak = form.form_score(["W"] * 5, [(1, 0)] * 5)
    assert strong["offensive_form"] > weak["offensive_form"] > 0          # 进球数确实被解析并计入
    assert strong["overall_form"] > weak["overall_form"]
    assert form.form_score(["L"] * 5, [(0, 3)] * 5)["overall_form"] < 0.1


def test_motivation_differs_by_table_position():
    title = form.analyze_motivation({"rank": 1, "teams_total": 20, "played": 34}, "league")
    mid = form.analyze_motivation({"rank": 10, "teams_total": 20, "played": 34}, "league")
    assert title["motivation_level"] == "HIGH" and mid["motivation_level"] == "LOW"
    assert form.analyze_motivation(None, "league")["score"] is None
    phase = {"rounds": 8, "direct": 8, "playoff": 24}
    bubble = form.analyze_motivation({"rank": 25, "played": 6}, "continental", phase)
    out = form.analyze_motivation({"rank": 35, "played": 6}, "continental", phase)
    assert bubble["score"] > out["score"]                                  # 洲际赛事不再固定为双方 HIGH
    assert form.analyze_motivation({"rank": 3, "played": 6}, "continental")["score"] == 0.8


def test_assess_match_form_gives_diff_and_gap():
    home = team(["W"] * 5, ["2-0"] * 5, {"rank": 1, "teams_total": 20, "played": 34})
    away = team(["L"] * 5, ["3-0"] * 5, {"rank": 10, "teams_total": 20, "played": 34})
    f = form.assess_match_form(match(home, away))
    assert f["form_advantage"] == "home" and f["form_diff"] > 0.5
    assert f["motivation_gap"] == pytest.approx(0.5)
    assert form.assess({"matches": [match(home, away)]})["version"] == form.VERSION
    unknown = form.assess_match_form(match({"name": "A"}, {"name": "B"}))
    assert unknown["form_diff"] is None and unknown["motivation_gap"] is None


def test_adjustment_moves_probabilities_toward_better_side_with_cap():
    adj, info = form.adjust_probabilities(P, {"form_diff": 0.4, "motivation_gap": 0.0}, CFG)
    assert info["applied"] and adj["win"] > P["win"] and adj["loss"] < P["loss"]
    assert sum(adj.values()) == pytest.approx(1.0)
    rev, _ = form.adjust_probabilities(P, {"form_diff": -0.4, "motivation_gap": 0.0}, CFG)
    assert rev["win"] < P["win"] and rev["loss"] > P["loss"]
    big, info = form.adjust_probabilities(P, {"form_diff": 1.0, "motivation_gap": 1.0}, {**CFG, "form_coef": 2.0})
    assert info["capped"] and max(abs(big[k] - P[k]) for k in P) == pytest.approx(0.04)
    assert sum(big.values()) == pytest.approx(1.0)


def test_adjustment_skipped_when_unknown_disabled_or_zero():
    for fm, cfg in ((None, CFG), ({"form_diff": None, "motivation_gap": None}, CFG),
                    ({"form_diff": 0.5, "motivation_gap": 0.2}, {**CFG, "enabled": False}),
                    ({"form_diff": 0.5, "motivation_gap": 0.2}, {**CFG, "form_coef": 0.0, "motivation_coef": 0.0})):
        adj, info = form.adjust_probabilities(P, fm, cfg)
        assert adj == P and not info["applied"] and info["reason"]


def test_prediction_changes_with_form(monkeypatch):
    od = {"market": {"fair_probability": {"home": 0.37, "draw": 0.27, "away": 0.36}, "sources_used": ["x"]},
          "sources": ["x"]}
    m = {"match_id": "m1", "seq": 1, "home_team": "主", "away_team": "客"}
    plain = predict.predict_match(m, None, od, True)
    away_hot = predict.predict_match(m, None, od, True, {"form_diff": -0.8, "motivation_gap": -0.3})
    assert plain["prediction"] == "WIN" and not plain["form_adjustment"]["applied"]
    assert away_hot["prediction"] == "LOSS"                               # 近况/战意确实能改变推荐方向
    assert away_hot["probabilities_before_form"] == plain["probabilities"]
    assert sum(away_hot["probabilities"].values()) == pytest.approx(1.0)


def test_risk_reflects_whether_form_supports_the_pick():
    pred = make_pred(1, (0.55, 0.25, 0.20))
    backs = risk.assess_match(pred, None, None, {"form_diff": 0.4, "motivation_gap": 0.2})
    against = risk.assess_match(pred, None, None, {"form_diff": -0.4, "motivation_gap": -0.2})
    unknown = risk.assess_match(pred, None, None, None)
    assert backs["risk_score"] < unknown["risk_score"] < against["risk_score"]
    assert unknown["factors"]["form_motivation"]["known"] is False


def test_backtest_form_features_only_use_earlier_matches():
    rows = [{"date": f"2025-08-{d:02d}", "home": h, "away": a, "hg": hg, "ag": ag}
            for d, h, a, hg, ag in [(1, "A", "B", 3, 0), (8, "B", "A", 0, 2), (15, "A", "B", 1, 0),
                                    (22, "B", "A", 0, 4), (29, "A", "B", 2, 2)]]
    feats = backtest.form_features(rows)
    assert feats[("2025-08-01", "A", "B")] == {"form_diff": None, "motivation_gap": None}   # 赛前没有任何数据
    assert feats[("2025-08-22", "B", "A")]["form_diff"] < 0                                  # B 三连败，A 三连胜
    assert feats[("2025-08-29", "A", "B")]["form_diff"] > 0
