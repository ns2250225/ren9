"""端到端（离线）：流程编排、报告、预测快照、赛后复盘、防泄漏。"""
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from conftest import PROBS_14, make_pred
from providers import sporttery
from ren9 import pipeline, report, review, risk, selector, optimizer, store
from ren9.common import data_dir


def _with_full_odds(zc_text: str, future_sale_end: bool = True) -> str:
    """测试用：给 14 场都补上参考赔率，使离线流程可以走到出方案（仅测试数据）。"""
    data = json.loads(zc_text)
    sfc = data["value"]["sfcMatch"]
    for i, m in enumerate(sfc["matchList"]):
        if not m.get("h"):
            m["h"], m["d"], m["a"] = ["1.60", "3.80", "5.20"] if i % 2 else ["2.60", "3.20", "2.70"]
    end = datetime.now(timezone(timedelta(hours=8))) + timedelta(hours=6 if future_sale_end else -6)
    sfc["lotterySaleBegintime"] = (end - timedelta(days=2)).strftime("%Y-%m-%d %H:%M:%S")
    sfc["lotterySaleEndtime"] = end.strftime("%Y-%m-%d %H:%M:%S")
    data["value"]["sfclist"] = [sfc["lotteryDrawNum"]]
    return json.dumps(data, ensure_ascii=False)


@pytest.fixture
def odds_only_ok(monkeypatch):
    """离线测试里每场只有官方赔率一种数据（数据质量约 0.26~0.30）。
    把"数据严重缺失"阈值调低，才能在不联网的情况下走完整条出方案的流程。"""
    from ren9 import common

    cfg = common.load_yaml("settings.yaml")
    cfg["safety"]["min_data_quality"] = 0.2
    monkeypatch.setitem(common._CONFIG_CACHE, str(common.CONFIG_DIR / "settings.yaml"), cfg)


def test_pipeline_offline_stops_without_enough_data(fake_http, zc_text, jc_text):
    """真实赛单里只有 8 场带官方参考赔率、其余数据源全部不可用：不足 9 场，不出正式推荐。"""
    fake_http({"getFootBallMatchV1": zc_text, "getMatchCalculatorV1": jc_text})
    ctx = pipeline.Ctx(date="2026-11-24", period="26169")
    st = pipeline.run(ctx, "report")
    assert st["probabilities"]["predicted"] == 8
    sel = st["selection"]["strategies"]["balanced"]
    assert sel["status"] == "insufficient"
    assert st["plans"]["strategies"]["balanced"]["bet_count"] == 0
    assert st["report"]["status"] == "insufficient"
    md = Path(st["report"]["markdown_path"]).read_text(encoding="utf-8")
    assert "未生成正式推荐" in md
    for m in st["schedule"]["matches"]:                      # 仍然展示全部 14 场
        assert m["home_team"] in md
    td = st["team_data"]["matches"][0]
    assert td["resolution"]["status"] == "unresolved" and td["teams"]["home"]["standing"] is None
    assert td["teams"]["home"]["lineup"]["injuries"] is None          # 缺失 ≠ 0


def test_pipeline_end_to_end_and_report(fake_http, zc_text, jc_text, odds_only_ok):
    fake_http({"getFootBallMatchV1": _with_full_odds(zc_text), "getMatchCalculatorV1": jc_text})
    ctx = pipeline.Ctx(date="today", period="26169", all_strategies=True)
    st = pipeline.run(ctx, "report")
    rep = st["report"]
    assert rep["status"] == "ok" and rep["saved_to_history"] and rep["post_deadline"] is False
    assert set(rep["plans"]) == {"conservative", "balanced", "aggressive"}

    for p in st["probabilities"]["matches"]:
        assert sum(p["probabilities"].values()) == pytest.approx(1.0, abs=1e-9)
    plan = st["plans"]["strategies"]["balanced"]
    assert len(plan["selected_matches"]) == 9 and len(plan["excluded_matches"]) == 5

    md = Path(rep["markdown_path"]).read_text(encoding="utf-8")
    for section in ("14 场完整分析", "推荐 9 场", "排除 5 场", "方案统计", "数据来源与更新时间", "数据缺失提醒",
                    "模型说明与历史评估", "风险提示", "备选方案：保守策略", "备选方案：激进策略"):
        assert section in md
    for m in st["schedule"]["matches"]:
        assert m["home_team"] in md and m["away_team"] in md
    assert "26169" in md and "不保证命中或盈利" in md and "组合数量" in md
    # 多票方案：报告里有逐票的购买清单，注数与金额和方案一致
    assert "购买清单" in md and "票 1" in md
    for pl in st["plans"]["strategies"].values():
        assert pl["bet_count"] == sum(t["bet_count"] for t in pl["tickets"])
        if pl["mode"] == "tickets":
            assert f"{len(pl['tickets'])} 张复式票，共 {pl['bet_count']} 注" in md

    js = json.loads(Path(rep["json_path"]).read_text(encoding="utf-8"))
    assert len(js["matches"]) == 14
    assert sum(1 for m in js["matches"] if m["selected"]) == 9
    one = js["matches"][0]
    for key in ("match_id", "period", "home_team", "away_team", "competition", "status", "probabilities",
                "model_confidence", "data_quality", "risk_level", "recommendation", "sources"):
        assert key in one
    # 预测时点快照 + 历史库
    snap = json.loads(Path(rep["snapshot_path"]).read_text(encoding="utf-8"))
    assert snap["post_deadline"] is False and len(snap["schedule"]["matches"]) == 14
    assert len(store.latest_predictions("ren9", "26169")) == 14
    assert {p["strategy"] for p in store.latest_plans("ren9", "26169")} == {"conservative", "balanced", "aggressive"}


def test_stage_reuse_and_partial_reanalysis(fake_http, zc_text, jc_text, odds_only_ok):
    fake_http({"getFootBallMatchV1": _with_full_odds(zc_text), "getMatchCalculatorV1": jc_text})
    pipeline.run(pipeline.Ctx(date="today", period="26169"), "probabilities")
    first = store.load_stage("ren9", "26169", "team_data")
    ctx = pipeline.Ctx(date="today", period="26169", seqs=[3, 8])
    st = pipeline.run(ctx, "probabilities")
    by_seq = {m["seq"]: m for m in st["team_data"]["matches"]}
    old = {m["seq"]: m for m in first["matches"]}
    assert by_seq[1]["collected_at"] == old[1]["collected_at"]            # 未指定的场次沿用
    assert len(st["probabilities"]["matches"]) == 14


def test_single_match_report(fake_http, zc_text, jc_text, odds_only_ok):
    fake_http({"getFootBallMatchV1": _with_full_odds(zc_text), "getMatchCalculatorV1": jc_text})
    ctx = pipeline.Ctx(date="today", period="26169")
    st = pipeline.run(ctx, "risk")
    md = report.build_match(st, 3)
    assert "阿森纳" in md and "多特蒙德" in md and "风险因子" in md and "赔率分析" in md
    with pytest.raises(ValueError):
        report.build_match(st, 99)


def test_post_deadline_predictions_are_flagged(fake_http, zc_text, jc_text, odds_only_ok):
    """销售截止后才生成的预测要打标，默认不进入复盘统计（防止数据泄漏）。"""
    fake_http({"getFootBallMatchV1": _with_full_odds(zc_text, future_sale_end=False),
               "getMatchCalculatorV1": jc_text})
    st = pipeline.run(pipeline.Ctx(date="2026-11-24", period="26169"), "report")
    assert st["report"]["post_deadline"] is True
    assert store.latest_predictions("ren9", "26169") == []
    assert len(store.latest_predictions("ren9", "26169", allow_post_deadline=True)) == 14


def test_jczq_pipeline(fake_http, jc_text):
    fake_http({"getMatchCalculatorV1": jc_text})
    st = pipeline.run(pipeline.Ctx(date="2026-11-24", game_type="jczq"), "report")
    assert "selection" not in st and "plans" not in st            # 竞彩不做 14 选 9
    md = Path(st["report"]["markdown_path"]).read_text(encoding="utf-8")
    assert "竞彩足球分析报告" in md and "让球胜平负" in md
    pred = st["probabilities"]["matches"][0]
    assert pred["handicap"]["line"] == 1.0 and "market_fair" in pred["handicap"]


# ---------- 复盘 ----------

def _seed_predictions(draw: dict, period: str, picks_all_hit: bool = True):
    """用官方第 26168 期的真实对阵写入一组预测与方案（概率为测试构造）。"""
    from ren9.common import CODE_OUTCOME

    rows = []
    for r, p in zip(draw["results"], PROBS_14):
        m = make_pred(r["seq"], p)
        m.update(match_id=f"ren9_{period}_{r['seq']:02d}", home_team=r["home_team"], away_team=r["away_team"],
                 risk_level="LOW")
        rows.append(m)
    store.save_predictions("ren9", period, "2026-11-22", rows, False, "snap.json")
    sels = []
    for r in draw["results"][:9]:
        actual = CODE_OUTCOME[r["result"]]
        picks = [actual] if picks_all_hit else ["draw" if actual != "draw" else "win"]
        if r["seq"] == 2:
            picks = sorted({actual, "draw"}, key=("win", "draw", "loss").index) if picks_all_hit else picks
        sels.append({"seq": r["seq"], "match": f"{r['home_team']} vs {r['away_team']}", "picks": picks,
                     "picks_zh": "".join({"win": "胜", "draw": "平", "loss": "负"}[x] for x in picks),
                     "type": "单选" if len(picks) == 1 else "双选", "coverage": 0.5})
    bets = optimizer.bet_count([s["picks"] for s in sels])
    store.save_plan("ren9", period, "2026-11-22",
                    {"status": "ok", "strategy": "balanced", "selections": sels, "bet_count": bets,
                     "total_cost": bets * 2, "hit_probability": 0.01}, False)


def test_review_matches_official_results_and_real_roi(fake_http, draw_text):
    fake_http({"getFootBallDrawInfoByDrawNumV1": draw_text})
    draw = sporttery.fetch_ren9_draw("26168")
    assert draw["complete"] and draw["draw_result"] == "3 3 3 1 0 3 1 3 3 3 0 3 1 0"
    assert draw["ren9_prize"] == {"amount": 3660.0, "winners": 3139.0}
    _seed_predictions(draw, "26168")
    r = review.review_ren9("26168")
    assert r["status"] == "ok" and len(r["matches"]) == 14 and not r["warnings"]
    m = r["metrics"]
    assert m["n"] == 14 and 0 <= m["accuracy"] <= 1 and m["brier"] > 0 and m["log_loss"] > 0 and m["rps"] > 0
    assert r["matches"][0]["actual"] == "win" and r["matches"][0]["score"] == "4:0"
    assert set(r["model_metrics"]) == {"dixon_coles", "market"}
    plan = r["plans"][0]
    assert plan["all_nine_hit"] and plan["hits"] == 9
    assert plan["cost"] == plan["bet_count"] * 2
    # ROI 用官方真实奖金（3660 元，低于 1 万元免税）与真实成本
    assert plan["prize_per_bet"] == 3660.0 and plan["winning_bets"] == 1
    assert plan["roi"] == pytest.approx((3660 - plan["cost"]) / plan["cost"], abs=1e-3)
    agg = review.aggregate("ren9")
    assert agg["period_count"] == 1 and agg["strategies"]["balanced"]["all_nine_hits"] == 1
    assert agg["calibration"] and agg["top_pick_buckets"]
    md = report.build_review(r, agg)
    assert "任九复盘报告" in md and "Brier" in md and "ROI" in md
    assert (data_dir() / "results" / "ren9_26168_review.json").exists()


def test_review_miss_has_negative_roi(fake_http, draw_text):
    fake_http({"getFootBallDrawInfoByDrawNumV1": draw_text})
    _seed_predictions(sporttery.fetch_ren9_draw("26168"), "26168", picks_all_hit=False)
    plan = review.review_ren9("26168")["plans"][0]
    assert not plan["all_nine_hit"] and plan["winning_bets"] == 0 and plan["roi"] == -1.0


def test_review_void_match_counts_as_hit_for_every_pick(fake_http, draw_text):
    data = json.loads(draw_text)
    data["value"]["matchList"][1]["result"] = "*"            # 第 2 场无效
    fake_http({"getFootBallDrawInfoByDrawNumV1": json.dumps(data, ensure_ascii=False)})
    clean = sporttery.parse_ren9_draw(json.loads(draw_text)["value"], {})
    _seed_predictions(clean, "26168")                        # 第 2 场为双选
    r = review.review_ren9("26168")
    plan = r["plans"][0]
    assert r["void_matches"] == [2] and r["metrics"]["n"] == 13
    assert plan["all_nine_hit"] and plan["winning_bets"] == 2          # 双选的两注都中


def test_review_pending_and_missing(fake_http, draw_text):
    empty = json.dumps({"success": True, "value": {"lotteryDrawNum": "26169", "matchList": []}})
    fake_http({"getFootBallDrawInfoByDrawNumV1": empty})
    assert review.review_ren9("26169")["status"] == "pending"
    fake_http({"getFootBallDrawInfoByDrawNumV1": draw_text})
    assert review.review_ren9("26168")["status"] == "no_prediction"
    assert review.optimize_weights("ren9")["status"] == "insufficient"


def test_review_multi_ticket_plan_wins_if_any_ticket_hits_all_nine():
    def sel(picks_by_seq):
        return [{"seq": i, "match": "", "picks": pk, "picks_zh": "", "type": "单选"} for i, pk in picks_by_seq.items()]

    main = {i: ["win"] for i in range(1, 10)}
    side = {**main, 9: ["draw"]}
    plan = {"strategy": "balanced", "mode": "tickets", "bet_count": 2, "total_cost": 4,
            "selections": sel({**main, 9: ["win", "draw"]}),
            "tickets": [{"no": 1, "bet_count": 1, "picks": "", "selections": sel(main)},
                        {"no": 2, "bet_count": 1, "picks": "", "selections": sel(side)}]}
    actual = {i: "win" for i in range(1, 9)}
    hit = review._evaluate_plan(plan, {**actual, 9: "draw"}, set(), {"amount": 1000.0})      # 副票全中
    assert hit["all_nine_hit"] and hit["winning_bets"] == 1 and hit["best_ticket_hits"] == 9
    assert [t["won"] for t in hit["tickets"]] == [False, True]
    assert hit["roi"] == pytest.approx((1000 - 4) / 4)
    miss = review._evaluate_plan(plan, {**actual, 9: "loss"}, set(), {"amount": 1000.0})
    assert not miss["all_nine_hit"] and miss["winning_bets"] == 0 and miss["best_ticket_hits"] == 8
    # 各票的并集覆盖了赛果，但没有任何一张票 9 场全中：不算中奖
    split = {"strategy": "balanced", "mode": "tickets", "bet_count": 2, "total_cost": 4,
             "selections": sel({**main, 8: ["win", "draw"], 9: ["win", "draw"]}),
             "tickets": [{"no": 1, "bet_count": 1, "picks": "", "selections": sel({**main, 8: ["draw"]})},
                         {"no": 2, "bet_count": 1, "picks": "", "selections": sel({**main, 9: ["draw"]})}]}
    both = review._evaluate_plan(split, {**actual, 8: "draw", 9: "draw"}, set(), None)
    assert both["hits"] == 9 and not both["all_nine_hit"]


def test_tax_applied_above_threshold():
    plan = {"strategy": "balanced", "bet_count": 1, "total_cost": 2,
            "selections": [{"seq": i, "match": "", "picks": ["win"], "picks_zh": "胜", "type": "单选"}
                           for i in range(1, 10)]}
    out = review._evaluate_plan(plan, {i: "win" for i in range(1, 10)}, set(), {"amount": 50000.0})
    assert out["payout_pre_tax"] == 50000.0 and out["payout_after_tax"] == 40000.0
    none = review._evaluate_plan(plan, {i: "win" for i in range(1, 10)}, set(), None)
    assert none["roi"] is None and "无法计算" in none["roi_note"]
