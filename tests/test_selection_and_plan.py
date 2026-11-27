"""预测输出、风险分级、14 选 9、组合优化。"""
import itertools

import pytest

from conftest import PROBS_14, make_pred, make_schedule
from ren9 import optimizer, predict, risk, selector


# ---------- 预测 ----------

def _sched_match(seq=1):
    return make_schedule()["matches"][seq - 1]


def test_market_only_prediction_sums_to_one():
    od = {"market": {"fair_probability": {"home": 0.5038, "draw": 0.2741, "away": 0.2221},
                     "sources_used": ["sporttery_jc_had"]}, "sources": [{}]}
    r = predict.predict_match(_sched_match(), None, od, True)
    assert r["status"] == "ok" and r["models_used"] == ["market"]
    assert sum(r["probabilities"].values()) == pytest.approx(1.0, abs=1e-9)
    assert r["prediction"] == "WIN"
    assert 0 <= r["model_confidence"] <= 1 and 0 <= r["data_quality"] <= 1
    assert r["agreement"] is None                       # 单模型时一致性未知
    assert set(r["models_unavailable"]) == {"elo", "poisson", "dixon_coles"}


def test_no_inputs_means_no_prediction_not_a_guess():
    r = predict.predict_match(_sched_match(), None, None, True)
    assert r["status"] == "no_prediction"
    assert r["probabilities"] is None and r["prediction"] is None


def test_round_probs_always_sums_to_one():
    for p in ({"win": 1 / 3, "draw": 1 / 3, "loss": 1 / 3}, {"win": 0.33335, "draw": 0.33335, "loss": 0.3333}):
        assert sum(predict.round_probs(p).values()) == pytest.approx(1.0, abs=1e-12)


# ---------- 风险 ----------

def test_risk_uses_more_than_max_probability():
    a = risk.assess_match(make_pred(1, (0.80, 0.12, 0.08)), None, None)       # PRD 比赛 A
    b = risk.assess_match(make_pred(2, (0.42, 0.30, 0.28)), None, None)       # PRD 比赛 B
    assert a["risk_score"] < b["risk_score"]
    assert b["risk_level"] == "HIGH"
    assert a["risk_level"] in ("LOW", "MEDIUM")
    assert set(a["factors"]) == set(risk.FACTOR_ZH)
    # 同样的概率，模型分歧大时风险更高
    calm = risk.assess_match(make_pred(3, (0.55, 0.25, 0.20), agreement=0.95), None, None)
    split = risk.assess_match(make_pred(3, (0.55, 0.25, 0.20), agreement=0.20), None, None)
    assert split["risk_score"] > calm["risk_score"]


def test_risk_unknown_when_data_insufficient():
    none = risk.assess_match(make_pred(1, None, status="no_prediction"), None, None)
    assert none["risk_level"] == "UNKNOWN" and none["risk_score"] is None
    poor = risk.assess_match(make_pred(2, (0.6, 0.25, 0.15), quality=0.2), None, None)
    assert poor["risk_level"] == "UNKNOWN"
    # 缺失的因子标记为未知，而不是当作 0
    ok = risk.assess_match(make_pred(3, (0.6, 0.25, 0.15)), None, None)
    assert "伤停/首发不确定" in ok["unknown_factors"]
    assert ok["factors"]["lineup"]["known"] is False and ok["factors"]["lineup"]["value"] is None


# ---------- 筛选 ----------

def test_selects_exactly_nine_and_excludes_five(analysis):
    sch, preds, rk = analysis
    for strategy in ("conservative", "balanced", "aggressive"):
        sel = selector.select(sch, preds, rk, None, strategy)
        assert sel["status"] == "ok"
        assert len(sel["selected"]) == 9 and len(sel["excluded"]) == 5
        assert len(sel["matches"]) == 14                                # 14 场全部展示
        assert sorted(sel["selected"] + [e["seq"] for e in sel["excluded"]]) == list(range(1, 15))
        assert all(e["reason"] for e in sel["excluded"])
    sel = selector.select(sch, preds, rk, None, "balanced")
    assert 12 in sel["selected"] and 1 in sel["selected"]               # 概率最明确的比赛入选
    assert 14 in [e["seq"] for e in sel["excluded"]]                    # 三项接近的比赛被排除


def test_stops_when_fewer_than_nine_valid(analysis):
    sch, preds, rk = analysis
    for i in range(6):                                   # 6 场没有概率 → 只剩 8 场有效
        preds["matches"][i] = make_pred(i + 1, None, status="no_prediction")
    rk = risk.assess(preds, None, None)
    sel = selector.select(sch, preds, rk, None, "balanced")
    assert sel["status"] == "insufficient" and sel["selected"] == []
    assert sel["eligible_count"] == 8 and "停止" in sel["message"]
    assert len(sel["matches"]) == 14


def test_invalid_schedule_stops_selection(analysis):
    _, preds, rk = analysis
    sch = make_schedule(13)
    sel = selector.select(sch, preds, rk, None, "balanced")
    assert sel["status"] == "invalid_schedule" and sel["selected"] == []


def test_cancelled_and_low_quality_matches_are_filtered(analysis):
    sch, preds, rk = analysis
    preds["matches"][11]["match_status"] = "postponed"          # 概率最高的一场延期
    preds["matches"][0]["data_quality"] = 0.1
    sel = selector.select(sch, preds, risk.assess(preds, None, None), None, "balanced")
    rows = {r["seq"]: r for r in sel["matches"]}
    assert not rows[12]["eligible"] and "延期" in rows[12]["ineligible_reason"]
    assert not rows[1]["eligible"] and "数据严重缺失" in rows[1]["ineligible_reason"]
    assert 12 not in sel["selected"] and 1 not in sel["selected"] and len(sel["selected"]) == 9


# ---------- 组合优化 ----------

def test_bet_count_matches_prd_example():
    w, d, l = "win", "draw", "loss"
    sel = [[w], [w, d], [d, l], [w], [w, d, l], [l], [w], [w, d], [l]]
    assert optimizer.bet_count(sel) == 24
    assert optimizer.bet_count(sel) * 2 == 48
    assert optimizer.bet_count([[w]] * 9) == 1
    with pytest.raises(ValueError):
        optimizer.bet_count([[w, w]])
    with pytest.raises(ValueError):
        optimizer.bet_count([[]])


def _nine():
    return [{"seq": i + 1, "home_team": f"H{i}", "away_team": f"A{i}",
             "probabilities": dict(zip(("win", "draw", "loss"), p))}
            for i, p in enumerate(PROBS_14[:9])]


@pytest.mark.parametrize("strategy", ["conservative", "balanced", "aggressive"])
def test_plan_respects_budget_and_limits(strategy):
    plan = optimizer.optimize(_nine(), strategy, max_tickets=1)          # 单张复式
    assert plan["mode"] == "compound" and len(plan["tickets"]) == 1
    assert len(plan["selections"]) == 9
    counts = [len(s["picks"]) for s in plan["selections"]]
    product = 1
    for c in counts:
        product *= c
    assert plan["bet_count"] == product <= plan["max_bets"]
    assert plan["total_cost"] == plan["bet_count"] * plan["unit_price"]
    assert plan["singles"] + plan["doubles"] + plan["triples"] == 9
    assert plan["doubles"] <= plan["params"]["max_doubles"] and plan["triples"] <= plan["params"]["max_triples"]
    cover = 1.0
    for s in plan["selections"]:
        cover *= s["coverage"]
    assert plan["hit_probability"] == pytest.approx(cover, abs=1e-4)
    assert plan["hit_probability"] >= plan["all_singles_hit_probability"] - 1e-9
    assert "成本" in plan["note"]


def test_user_budget_overrides_default():
    plan = optimizer.optimize(_nine(), "aggressive", budget=8)       # 8 元 = 4 注
    assert plan["bet_count"] <= 4 and plan["total_cost"] <= 8
    single = optimizer.optimize(_nine(), "balanced", budget=2)
    assert single["bet_count"] == 1 and single["singles"] == 9
    with pytest.raises(ValueError):
        optimizer.optimize(_nine(), "balanced", budget=1)


def test_optimizer_is_exactly_optimal_under_its_objective():
    """λ=0、α=0 时目标就是命中概率：结果应等于穷举得到的最优值。"""
    import math

    matches = _nine()
    plan = optimizer.optimize(matches, "balanced", budget=64, max_tickets=1)
    lam = plan["params"]["cost_aversion"]
    best = -1e9
    sorted_p = [sorted(m["probabilities"].values(), reverse=True) for m in matches]
    for ks in itertools.product((1, 2, 3), repeat=9):
        bets = math.prod(ks)
        if bets > 32 or ks.count(2) > plan["params"]["max_doubles"] or ks.count(3) > plan["params"]["max_triples"]:
            continue
        obj = sum(math.log(sum(sp[:k])) for sp, k in zip(sorted_p, ks)) - lam * math.log(bets)
        best = max(best, obj)
    got = math.log(plan["hit_probability"]) - lam * math.log(plan["bet_count"])
    assert got == pytest.approx(best, abs=1e-4)


def test_doubles_go_to_uncertain_matches():
    plan = optimizer.optimize(_nine(), "balanced")
    by_seq = {s["seq"]: s for s in plan["selections"]}
    assert by_seq[1]["type"] == "单选" and by_seq[1]["picks"] == ["win"]        # 72% 的比赛单选
    assert by_seq[2]["type"] != "单选"                                           # 35/40/25 的比赛多选
    assert by_seq[9]["picks"] == ["loss"]


def test_build_plan_structure(analysis):
    sch, preds, rk = analysis
    sel = selector.select(sch, preds, rk, None, "balanced")
    plan = optimizer.build_plan(sel, preds, rk, None, "balanced")
    for key in ("date", "strategy", "selected_matches", "excluded_matches", "selections", "bet_count",
                "unit_price", "total_cost"):
        assert key in plan
    assert len(plan["selected_matches"]) == 9 and len(plan["excluded_matches"]) == 5
    bad = optimizer.build_plan({"status": "insufficient", "message": "x"}, preds, rk, None, "balanced")
    assert bad["status"] == "insufficient" and bad["bet_count"] == 0 and bad["selections"] == []


# ---------- 多票组合 ----------

def _covered(plan):
    """方案覆盖的全部 9 场结果组合（逐票展开）。"""
    combos = []
    for t in plan["tickets"]:
        combos += list(itertools.product(*[s["picks"] for s in t["selections"]]))
    return combos


@pytest.mark.parametrize("strategy", ["conservative", "balanced", "aggressive"])
def test_multi_ticket_plan_is_disjoint_within_budget_and_beats_single_compound(strategy):
    import math

    matches = _nine()
    plan = optimizer.optimize(matches, strategy)
    single = optimizer.optimize(matches, strategy, max_tickets=1)
    assert plan["mode"] == "tickets" and 1 < len(plan["tickets"]) <= plan["params"]["max_tickets"]
    combos = _covered(plan)
    assert len(combos) == len(set(combos)) == plan["bet_count"] <= plan["max_bets"]     # 各票互不重叠
    assert plan["bet_count"] == sum(t["bet_count"] for t in plan["tickets"])
    assert plan["total_cost"] == plan["bet_count"] * plan["unit_price"] == sum(t["cost"] for t in plan["tickets"])
    prob = sum(math.prod(m["probabilities"][o] for m, o in zip(matches, c)) for c in combos)
    assert plan["hit_probability"] == pytest.approx(prob, abs=1e-5)
    assert plan["hit_probability"] > single["hit_probability"]
    assert plan["compound_alternative"]["hit_probability"] == single["hit_probability"]
    # 逐场推荐 = 单张复式方案（与对照一致）；多票覆盖到的结果另存在 tickets_picks
    assert [s["picks"] for s in plan["selections"]] == [s["picks"] for s in single["selections"]]
    assert optimizer.bet_count([s["picks"] for s in plan["selections"]]) == plan["compound_alternative"]["bet_count"]
    assert (plan["singles"], plan["doubles"], plan["triples"]) == (single["singles"], single["doubles"], single["triples"])
    assert plan["compound_alternative"]["picks"] == " / ".join(f"{s['seq']}:{s['picks_zh']}" for s in plan["selections"])
    for i, s in enumerate(plan["selections"]):
        assert set(s["tickets_picks"]) == {o for t in plan["tickets"] for o in t["selections"][i]["picks"]}
    assert "票1" in optimizer.describe(plan) and "｜" in optimizer.describe(plan)


def test_portfolio_never_beats_the_best_possible_single_tickets():
    """多票方案的覆盖概率不可能超过"概率最高的 N 个单式组合"之和；一张票时等于最优复式。"""
    import math

    from ren9 import portfolio

    probs = [m["probabilities"] for m in _nine()][:5]
    allc = sorted((math.prod(p[o] for p, o in zip(probs, c))
                   for c in itertools.product(("win", "draw", "loss"), repeat=5)), reverse=True)
    prev = 0.0
    for tickets in (1, 2, 4, 12):
        r = portfolio.optimize(probs, 12, tickets)
        assert r["bets"] <= 12 and len(r["tickets"]) <= tickets
        assert prev - 1e-12 <= r["hit_probability"] <= sum(allc[:12]) + 1e-9      # 票数越多不会越差
        prev = r["hit_probability"]
    assert prev == pytest.approx(sum(allc[:12]), abs=1e-9)                       # 票数够多时达到理论上限
    # 成本厌恶：覆盖率提升不够时少用注数
    assert portfolio.optimize(probs, 12, 4, cost_aversion=5.0)["bets"] == 1


def test_balanced_preset_is_tighter_than_before():
    ps = optimizer.preset("balanced")
    assert ps["max_bets"] == 24 and ps["cost_aversion"] == 0.25 and ps["max_tickets"] == 4
