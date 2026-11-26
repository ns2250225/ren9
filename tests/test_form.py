"""近况 / 战意分析，以及它对概率、风险的影响。"""
from __future__ import annotations

import pytest

from conftest import make_pred
from providers import espn
from ren9 import backtest, form, lineup, predict, risk

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


# ---------- 主力缺阵（由官方首发推断） ----------

LCFG = {"enabled": True, "coef": 0.5, "max_shift": 0.04, "regular_start_rate": 0.6, "min_games": 3,
        "bench_weight": 0.4, "source": "test"}


def xi(ids):
    return [{"id": str(i), "name": f"P{i}", "position": "M"} for i in ids]


def squad(starters, bench=()):
    return ([{"id": str(i), "name": f"P{i}", "position": "M", "starter": True} for i in starters]
            + [{"id": str(i), "name": f"P{i}", "position": "SUB", "starter": False} for i in bench])


HISTORY = [xi(range(1, 12))] * 4 + [xi(list(range(1, 10)) + [20, 21])]        # 1~9 五场全首发，10/11 四场


def test_regulars_come_from_recent_starts():
    regs = {r["id"]: r for r in lineup.regulars(HISTORY, LCFG)}
    assert set(regs) == {str(i) for i in range(1, 12)}                     # 20/21 只首发 1 场，不算主力
    assert regs["1"]["importance"] == 1.0 and regs["10"]["importance"] == 0.8
    assert lineup.regulars(HISTORY[:2], LCFG) == []                         # 记录不足


def test_absences_unknown_without_lineup_or_history():
    assert lineup.assess_team(None, HISTORY, LCFG)["known"] is False
    short = lineup.assess_team(squad(range(1, 12)), HISTORY[:2], LCFG)
    assert short["known"] is False and short["absence_score"] is None and "不足" in short["reason"]


def test_absence_score_weights_squad_omission_above_bench():
    full = lineup.assess_team(squad(range(1, 12)), HISTORY, LCFG)
    assert full["known"] and full["absence_score"] == 0.0 and full["missing"] == []
    out = lineup.assess_team(squad(list(range(3, 12)) + [30, 31]), HISTORY, LCFG)             # 1、2 不在大名单
    bench = lineup.assess_team(squad(list(range(3, 12)) + [30, 31], bench=[1, 2]), HISTORY, LCFG)
    assert out["absence_score"] > bench["absence_score"] > 0
    assert {m["status"] for m in out["missing"]} == {"not_in_squad"}
    assert {m["status"] for m in bench["missing"]} == {"bench"}
    assert out["absence_score"] == pytest.approx(2 / (9 + 2 * 0.8), abs=1e-4)
    assert "不在大名单" in lineup.describe(out) and "主力齐整" in lineup.describe(full)


def test_lineup_adjustment_favours_the_side_with_fewer_absences():
    weak = {"known": True, "absence_score": 0.3, "missing": [{}] * 3}
    fine = {"known": True, "absence_score": 0.0, "missing": []}
    adj, info = lineup.adjust_probabilities(P, weak, fine, LCFG)             # 主队缺阵多
    assert info["applied"] and adj["win"] < P["win"] and adj["loss"] > P["loss"]
    assert max(abs(adj[k] - P[k]) for k in P) <= 0.04 + 1e-9 and sum(adj.values()) == pytest.approx(1.0)
    rev, _ = lineup.adjust_probabilities(P, fine, weak, LCFG)
    assert rev["win"] > P["win"]
    for h, a, cfg in ((None, fine, LCFG), ({"known": False, "reason": "官方首发未公布"}, fine, LCFG),
                      (weak, weak, LCFG), (weak, fine, {**LCFG, "enabled": False})):
        same, info = lineup.adjust_probabilities(P, h, a, cfg)
        assert same == P and not info["applied"] and info["reason"]


def td_with(home_abs, away_abs):
    def side(a):
        return {"lineup": {"official_lineup_published": True, "absences": a}}
    return {"teams": {"home": side(home_abs), "away": side(away_abs)}}


def test_prediction_and_risk_use_absences():
    od = {"market": {"fair_probability": {"home": 0.45, "draw": 0.28, "away": 0.27}, "sources_used": ["x"]},
          "sources": ["x"]}
    m = {"match_id": "m1", "seq": 1, "home_team": "主", "away_team": "客"}
    hurt = {"known": True, "absence_score": 0.4, "missing": [{}] * 4}
    fine = {"known": True, "absence_score": 0.0, "missing": []}
    plain = predict.predict_match(m, None, od, True)
    weakened = predict.predict_match(m, td_with(hurt, fine), od, True)
    assert not plain["lineup_adjustment"]["applied"]
    assert weakened["lineup_adjustment"]["applied"]
    assert weakened["probabilities"]["win"] < plain["probabilities"]["win"]
    pred = make_pred(1, (0.55, 0.25, 0.20))
    r_fine = risk.assess_match(pred, td_with(fine, fine), None)
    r_hurt = risk.assess_match(pred, td_with(hurt, fine), None)             # 首选（主胜）一方主力缺阵
    r_opp = risk.assess_match(pred, td_with(fine, hurt), None)
    assert (r_hurt["factors"]["lineup"]["value"] > r_opp["factors"]["lineup"]["value"]
            > r_fine["factors"]["lineup"]["value"])
    assert risk.assess_match(pred, None, None)["factors"]["lineup"]["known"] is False


def test_espn_lineups_keep_full_matchday_squad():
    roster = [{"athlete": {"id": i, "displayName": f"P{i}"}, "starter": i <= 11,
               "position": {"abbreviation": "G" if i == 1 else "SUB" if i > 11 else "M"}} for i in range(1, 19)]
    lus = espn._lineups({"rosters": [{"team": {"id": 7}, "formation": "4-3-3", "roster": roster},
                                     {"team": {"id": 8}, "roster": roster[:5]}]})
    assert lus["7"]["official"] and len(lus["7"]["starters"]) == 11 and len(lus["7"]["players"]) == 18
    assert lus["7"]["players"][0] == {"id": "1", "name": "P1", "position": "G", "starter": True}
    assert not lus["8"]["official"] and lus["8"]["players"] == [] and lus["8"]["starters"] == []
