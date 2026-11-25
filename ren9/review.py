"""赛后复盘：匹配官方开奖结果，评估概率质量、方案命中与真实 ROI。"""
from __future__ import annotations

from datetime import timedelta, timezone

from models import ensemble, metrics
from ren9 import store
from ren9.common import (CODE_OUTCOME, OUTCOME_ZH, OUTCOMES, data_dir, model_weights, now_iso, parse_dt,
                         save_learned, write_json)
from providers import espn, sporttery
from providers.base import ProviderError

TAX_THRESHOLD = 10000   # 单注奖金超过 1 万元需缴 20% 个人所得税
TAX_RATE = 0.20


def _probs(row: dict) -> dict | None:
    if row.get("p_win") is None:
        return None
    return {"win": row["p_win"], "draw": row["p_draw"], "loss": row["p_loss"]}


def _evaluate_plan(plan: dict, outcomes: dict[int, str | None], void: set[int], prize: dict | None) -> dict:
    sel_out, all_hit, tickets = [], True, 1
    for s in plan.get("selections", []):
        seq = s["seq"]
        if seq in void:
            hit, actual = True, "*"
            tickets *= len(s["picks"])        # 无效场次任意选项都算中，复式中的每个选项各成一注
        else:
            actual = outcomes.get(seq)
            hit = actual in s["picks"] if actual else False
        all_hit = all_hit and hit
        sel_out.append({"seq": seq, "match": s.get("match"), "picks": s["picks_zh"], "type": s["type"],
                        "actual": "*" if actual == "*" else OUTCOME_ZH.get(actual, "?"), "hit": hit})
    cost = plan.get("total_cost", 0)
    out = {
        "strategy": plan["strategy"], "bet_count": plan.get("bet_count"), "cost": cost,
        "selections": sel_out, "hits": sum(1 for s in sel_out if s["hit"]), "total": len(sel_out),
        "single_hits": sum(1 for s in sel_out if s["type"] == "单选" and s["hit"]),
        "single_total": sum(1 for s in sel_out if s["type"] == "单选"),
        "multi_covered": sum(1 for s in sel_out if s["type"] != "单选" and s["hit"]),
        "multi_total": sum(1 for s in sel_out if s["type"] != "单选"),
        "all_nine_hit": all_hit and len(sel_out) == 9,
        "predicted_hit_probability": plan.get("hit_probability"),
    }
    # ROI 只用官方公布的真实奖金与真实投注成本计算
    if prize and prize.get("amount") is not None:
        amount = prize["amount"]
        win_tickets = tickets if out["all_nine_hit"] else 0
        gross = win_tickets * amount
        net_per = amount * (1 - TAX_RATE) if amount > TAX_THRESHOLD else amount
        out.update(
            prize_per_bet=amount, winning_bets=win_tickets, payout_pre_tax=gross,
            payout_after_tax=round(win_tickets * net_per, 2),
            roi=round((win_tickets * net_per - cost) / cost, 4) if cost else None,
        )
    else:
        out.update(prize_per_bet=None, winning_bets=None, payout_pre_tax=None, payout_after_tax=None, roi=None,
                   roi_note="官方尚未公布任九奖金，无法计算 ROI")
    return out


def review_ren9(period: str, refresh: bool = False, allow_post_deadline: bool = False) -> dict:
    base = {"game_type": "ren9", "period": period, "reviewed_at": now_iso()}
    try:
        draw = sporttery.fetch_ren9_draw(period, refresh=refresh)
    except ProviderError as exc:
        return {**base, "status": "error", "message": f"无法获取官方开奖结果: {exc}"}
    if not draw["complete"]:
        return {**base, "status": "pending", "message": f"第 {period} 期尚未开奖或赛果不完整，稍后再复盘"}
    preds = store.latest_predictions("ren9", period, allow_post_deadline)
    if not preds:
        msg = f"第 {period} 期没有在销售截止前保存的预测记录"
        if not allow_post_deadline and store.latest_predictions("ren9", period, True):
            msg += "（只有截止后生成的预测；为避免数据泄漏默认不纳入复盘，可用 --allow-post-deadline 查看）"
        return {**base, "status": "no_prediction", "message": msg, "draw_result": draw["draw_result"]}

    results = {r["seq"]: r for r in draw["results"]}
    store.save_results("ren9", period, draw["results"])
    void = {s for s, r in results.items() if r["result"] == "*"}
    outcomes = {s: CODE_OUTCOME.get(r["result"]) for s, r in results.items() if r["result"] != "*"}

    rows, records, model_records, warnings = [], [], {}, []
    for p in preds:
        r = results.get(p["seq"])
        if not r:
            warnings.append(f"第 {p['seq']} 场没有对应的开奖结果")
            continue
        if (r["home_team"], r["away_team"]) != (p["home"], p["away"]):
            warnings.append(f"第 {p['seq']} 场对阵与开奖数据不一致：预测 {p['home']} vs {p['away']}，"
                            f"开奖 {r['home_team']} vs {r['away_team']}")
        probs = _probs(p)
        actual = outcomes.get(p["seq"])
        row = {"seq": p["seq"], "match": f"{p['home']} vs {p['away']}", "score": r["score"],
               "result_code": r["result"], "actual": "*" if p["seq"] in void else actual,
               "probabilities": probs, "prediction": p["prediction"], "risk_level": p["risk_level"],
               "data_quality": p["data_quality"], "models": p["models"]}
        if probs and actual:
            row["top_pick_hit"] = p["prediction"] == actual.upper()
            row["probability_of_actual"] = round(probs[actual], 4)
            row["brier"] = round(metrics.brier(probs, actual), 4)
            records.append((probs, actual))
            for name, mp in (p["models"] or {}).items():
                model_records.setdefault(name, []).append((mp, actual))
        rows.append(row)

    plans = [_evaluate_plan(pl, outcomes, void, draw.get("ren9_prize"))
             for pl in store.latest_plans("ren9", period, allow_post_deadline) if pl.get("status") == "ok"]
    # 模型偏差：平均预测概率 − 实际发生频率（正值 = 高估）
    bias = None
    if records:
        n = len(records)
        bias = {k: round(sum(p[k] for p, _ in records) / n - sum(1 for _, o in records if o == k) / n, 4)
                for k in OUTCOMES}
    review = {
        **base, "status": "ok", "analysis_date": preds[0]["analysis_date"], "predicted_at": preds[0]["created_at"],
        "post_deadline_prediction": bool(preds[0]["post_deadline"]),
        "draw_result": draw["draw_result"], "draw_date": draw["draw_date"],
        "ren9_prize": draw.get("ren9_prize"), "void_matches": sorted(void),
        "matches": rows,
        "metrics": metrics.summarize(records),
        "model_metrics": {k: metrics.summarize(v) for k, v in model_records.items()},
        "outcome_bias": bias,
        "plans": plans,
        "data_quality": {
            "mean": round(sum(p["data_quality"] or 0 for p in preds) / len(preds), 4),
            "no_prediction": sum(1 for p in preds if _probs(p) is None),
            "model_coverage": {k: len(v) for k, v in model_records.items()},
        },
        "records": [{"p": p, "o": o} for p, o in records],
        "model_records": {k: [{"p": p, "o": o} for p, o in v] for k, v in model_records.items()},
        "warnings": warnings,
        "source": draw["source"],
    }
    store.save_review("ren9", period, review)
    write_json(data_dir() / "results" / f"ren9_{period}_review.json", review)
    return review


def review_jczq(day: str, refresh: bool = False) -> dict:
    """竞彩复盘：官方赛果接口不可用（403），用 ESPN 终场比分匹配已对应上的比赛。"""
    base = {"game_type": "jczq", "period": day, "reviewed_at": now_iso()}
    preds = store.latest_predictions("jczq", day, True)
    td = store.load_stage("jczq", day, "team_data") or {}
    if not preds:
        return {**base, "status": "no_prediction", "message": f"{day} 没有保存的竞彩预测"}
    tds = {x["match_id"]: x for x in td.get("matches", [])}
    rows, records, pending = [], [], 0
    cache: dict[tuple, list] = {}
    for p in preds:
        t = tds.get(p["match_id"]) or {}
        res = t.get("resolution") or {}
        ko = parse_dt((t.get("kickoff") or {}).get("espn"))
        actual = score = None
        if res.get("status") == "resolved" and res.get("league") and ko:
            for d in (ko, ko - timedelta(days=1)):
                key = (res["league"], d.astimezone(timezone.utc).strftime("%Y%m%d"))
                if key not in cache:
                    try:
                        cache[key] = espn.scoreboard(key[0], key[1], ttl=300, refresh=refresh)[0]
                    except ProviderError:
                        cache[key] = []
                ev = next((e for e in cache[key] if e["event_id"] == res["espn_event_id"]), None)
                if ev and ev["completed"] and ev["home"]["score"] is not None:
                    hg, ag = ev["home"]["score"], ev["away"]["score"]
                    score = f"{hg}:{ag}"
                    actual = "win" if hg > ag else "draw" if hg == ag else "loss"
                    break
        probs = _probs(p)
        row = {"seq": p["seq"], "match": f"{p['home']} vs {p['away']}", "score": score, "actual": actual,
               "probabilities": probs, "prediction": p["prediction"]}
        made = parse_dt(p["created_at"])
        if ko and made and made >= ko:          # 开球后才生成的预测不计入指标（防止数据泄漏）
            row["predicted_after_kickoff"] = True
            rows.append(row)
            continue
        if actual and probs:
            row["top_pick_hit"] = p["prediction"] == actual.upper()
            records.append((probs, actual))
        elif not actual:
            pending += 1
        rows.append(row)
    review = {**base, "status": "ok" if records else "pending", "matches": rows,
              "metrics": metrics.summarize(records), "unmatched_or_unfinished": pending,
              "records": [{"p": p, "o": o} for p, o in records],
              "note": "赛果来自 ESPN 终场比分（90 分钟含补时）；未对应到 ESPN 的比赛无法复盘"}
    if records:
        store.save_review("jczq", day, review)
        write_json(data_dir() / "results" / f"jczq_{day}_review.json", review)
    return review


def aggregate(game_type: str = "ren9") -> dict:
    """全部已复盘期次的历史表现汇总（报告中的"模型历史评估"）。"""
    reviews = [r for r in store.all_reviews(game_type) if r.get("status") == "ok"]
    records = [(x["p"], x["o"]) for r in reviews for x in r.get("records", [])]
    model_records: dict[str, list] = {}
    for r in reviews:
        for k, v in (r.get("model_records") or {}).items():
            model_records.setdefault(k, []).extend((x["p"], x["o"]) for x in v)
    strategies: dict[str, dict] = {}
    for r in reviews:
        for pl in r.get("plans", []):
            s = strategies.setdefault(pl["strategy"], {"periods": 0, "all_nine_hits": 0, "cost": 0.0,
                                                       "payout_after_tax": 0.0, "roi_periods": 0,
                                                       "selection_hits": 0, "selections": 0})
            s["periods"] += 1
            s["all_nine_hits"] += 1 if pl["all_nine_hit"] else 0
            s["selection_hits"] += pl["hits"]
            s["selections"] += pl["total"]
            if pl.get("roi") is not None:
                s["roi_periods"] += 1
                s["cost"] += pl["cost"]
                s["payout_after_tax"] += pl["payout_after_tax"]
    for s in strategies.values():
        s["hit_rate"] = round(s["all_nine_hits"] / s["periods"], 4) if s["periods"] else None
        s["selection_hit_rate"] = round(s["selection_hits"] / s["selections"], 4) if s["selections"] else None
        s["roi"] = round((s["payout_after_tax"] - s["cost"]) / s["cost"], 4) if s["cost"] else None
    return {
        "game_type": game_type, "periods": [r["period"] for r in reviews], "period_count": len(reviews),
        "metrics": metrics.summarize(records),
        "model_metrics": {k: metrics.summarize(v) for k, v in model_records.items()},
        "calibration": metrics.calibration_bins(records) if records else [],
        "ece": metrics.expected_calibration_error(records) if records else None,
        "top_pick_buckets": metrics.top_pick_buckets(records) if records else [],
        "strategies": strategies,
        "note": "样本量很小时，这些指标的统计意义有限；不能用一期的结果判断模型质量。",
    }


def optimize_weights(game_type: str = "ren9") -> dict:
    """用已复盘的真实预测记录重新拟合集成权重与校准温度，写入 config/model_weights.yaml。"""
    reviews = [r for r in store.all_reviews(game_type) if r.get("status") == "ok"]
    per_match: list[tuple[dict, str]] = []
    for r in reviews:
        for m in r.get("matches", []):
            if m.get("models") and m.get("actual") in OUTCOMES:
                per_match.append((m["models"], m["actual"]))
    models = [k for k in ("elo", "poisson", "dixon_coles", "market")
              if sum(1 for mp, _ in per_match if k in mp) >= 50]
    ens_cfg = model_weights().get("ensemble", {})
    fit = (ensemble.fit_weights(per_match, models, {"market": ens_cfg.get("max_market_weight", 0.6)},
                                ens_cfg.get("min_model_weight", 0.05)) if len(models) >= 2 else None)
    if not fit:
        return {"status": "insufficient", "samples": len(per_match),
                "message": "已复盘且各模型都有预测的比赛不足 50 场，暂不调整权重（继续使用当前配置）"}
    save_learned({"ensemble": {**fit, "fitted_at": now_iso(), "source": "review"}})
    return {"status": "ok", **fit}
