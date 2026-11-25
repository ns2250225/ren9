"""任九筛选：从 14 场中严格选出 9 场、排除 5 场。

MatchScore = w_p·ProbabilityScore + w_a·ModelAgreementScore + w_q·DataQualityScore − w_r·RiskPenalty
四项都归一化到 0~1；权重来自策略预设，回测学习到权重后可覆盖。
"""
from __future__ import annotations

from ren9.common import OUTCOMES, model_weights, now_iso, settings


def score_weights(strategy: str) -> tuple[dict, str]:
    presets = settings().get("strategy", {}).get("presets", {})
    sel = model_weights().get("selector", {})
    if strategy in presets and presets[strategy].get("score_weights"):
        w = dict(presets[strategy]["score_weights"])
        src = f"策略预设（{strategy}）"
    else:
        w = dict(sel.get("initial", {"probability": 0.4, "agreement": 0.2, "data_quality": 0.15, "risk": 0.25}))
        src = "初始配置"
    learned = sel.get("learned")
    if learned and learned.get("weights") and strategy == "balanced":
        w = dict(learned["weights"])
        src = f"回测学习（样本 {learned.get('samples')}）"
    return w, src


def probability_score(p: dict) -> float:
    """首选结果的概率优势：1/3 → 0，80% 及以上 → 1。"""
    return max(0.0, min(1.0, (max(p[k] for k in OUTCOMES) - 1 / 3) / (0.80 - 1 / 3)))


def ineligible_reason(sched_m: dict, pred: dict, risk: dict, td: dict | None, bad_seqs: set) -> str | None:
    """第一阶段数据过滤：返回不能进入正常推荐的原因；可以进入则返回 None。"""
    min_q = settings().get("safety", {}).get("min_data_quality", 0.35)
    status = (pred.get("match_status") or sched_m.get("status") or "scheduled")
    if sched_m.get("seq") in bad_seqs:
        return "比赛编号重复或对阵信息异常"
    if not sched_m.get("home_team") or not sched_m.get("away_team"):
        return "球队信息不完整"
    if status in ("cancelled", "postponed"):
        return f"比赛状态为 {status}（取消/延期）"
    if pred.get("status") != "ok" or not pred.get("probabilities"):
        return "模型无法计算概率（数据缺失）"
    if (pred.get("data_quality") or 0) < min_q:
        return f"数据严重缺失（数据质量 {pred.get('data_quality')} < {min_q}）"
    return None


def select(schedule: dict, predictions: dict, risk: dict, team_data: dict | None = None,
           strategy: str = "balanced") -> dict:
    cfg = settings()["lottery"]
    n_expected, n_select = cfg["expected_matches"], cfg["selected_matches"]
    weights, wsrc = score_weights(strategy)
    preds = {x["match_id"]: x for x in predictions.get("matches", [])}
    risks = {x["match_id"]: x for x in risk.get("matches", [])}
    tds = {x["match_id"]: x for x in (team_data or {}).get("matches", [])}
    bad_seqs = {s for i in schedule.get("issues", []) if i["level"] == "error" for s in i.get("seqs", [])}

    base = {"date": schedule.get("date"), "game_type": "ren9", "period": schedule.get("period"),
            "strategy": strategy, "generated_at": now_iso(), "score_weights": weights,
            "score_weights_source": wsrc, "expected_matches": n_expected, "select_count": n_select}

    if not schedule.get("complete") or len(schedule.get("matches", [])) != n_expected:
        return {**base, "status": "invalid_schedule", "selected": [], "excluded": [], "matches": [],
                "message": "赛单不完整或未通过校验，停止筛选。" + (schedule.get("action_required") or "")}

    rows = []
    for m in schedule["matches"]:
        p, r = preds.get(m["match_id"], {}), risks.get(m["match_id"], {})
        why = ineligible_reason(m, p, r, tds.get(m["match_id"]), bad_seqs)
        row = {"match_id": m["match_id"], "seq": m["seq"], "home_team": m["home_team"],
               "away_team": m["away_team"], "competition": m.get("competition"),
               "eligible": why is None, "ineligible_reason": why,
               "risk_level": r.get("risk_level", "UNKNOWN"), "probabilities": p.get("probabilities")}
        if why is None:
            ps = probability_score(p["probabilities"])
            ag = p.get("agreement")
            comp = {
                "probability": round(ps, 4),
                "agreement": 0.5 if ag is None else ag,          # 无法衡量一致性时取中性值
                "data_quality": p.get("data_quality") or 0.0,
                "risk": r.get("risk_score") if r.get("risk_score") is not None else 0.5,
            }
            row["components"] = comp
            row["match_score"] = round(
                weights["probability"] * comp["probability"] + weights["agreement"] * comp["agreement"]
                + weights["data_quality"] * comp["data_quality"] - weights["risk"] * comp["risk"], 4)
        else:
            row["components"] = None
            row["match_score"] = None
        rows.append(row)

    eligible = sorted((r for r in rows if r["eligible"]), key=lambda r: (-r["match_score"], r["seq"]))
    if len(eligible) < n_select:
        for r in rows:
            r["selected"] = False
        return {**base, "status": "insufficient", "eligible_count": len(eligible), "selected": [], "excluded": [],
                "matches": rows,
                "message": f"有效比赛只有 {len(eligible)} 场（少于 {n_select} 场），停止生成正式推荐。"}

    chosen = {r["match_id"] for r in eligible[:n_select]}
    cutoff = eligible[n_select - 1]["match_score"]
    for rank, r in enumerate(eligible, 1):
        r["rank"] = rank
    for r in rows:
        r["selected"] = r["match_id"] in chosen
        if r["selected"]:
            r["reason"] = _keep_reason(r)
        else:
            r["reason"] = r["ineligible_reason"] or _drop_reason(r, risks.get(r["match_id"], {}), cutoff)
    selected = sorted((r["seq"] for r in rows if r["selected"]))
    excluded = [{"seq": r["seq"], "match": f"{r['home_team']} vs {r['away_team']}", "reason": r["reason"],
                 "match_score": r["match_score"]} for r in rows if not r["selected"]]
    assert len(selected) == n_select and len(excluded) == n_expected - n_select
    return {**base, "status": "ok", "eligible_count": len(eligible), "selected": selected,
            "excluded": excluded, "matches": rows}


def _keep_reason(r: dict) -> str:
    c, p = r["components"], r["probabilities"]
    bits = [f"首选概率 {max(p.values()):.0%}"]
    if c["agreement"] >= 0.7:
        bits.append("模型一致性好")
    if c["data_quality"] >= 0.7:
        bits.append("数据质量高")
    if r["risk_level"] == "LOW":
        bits.append("风险低")
    return f"综合评分 {r['match_score']:.3f}（第 {r['rank']} 位）：" + "，".join(bits)


def _drop_reason(r: dict, risk: dict, cutoff: float) -> str:
    c, p = r["components"], r["probabilities"]
    v = sorted(p.values(), reverse=True)
    bits = []
    if v[0] - v[1] < 0.12 or v[0] < 0.45:
        bits.append(f"三项概率接近（{v[0]:.0%}/{v[1]:.0%}/{v[2]:.0%}）")
    if c["agreement"] < 0.55:
        bits.append("模型预测分歧较大")
    if c["data_quality"] < 0.6:
        bits.append(f"数据质量偏低（{c['data_quality']:.2f}）")
    mv = (risk.get("factors") or {}).get("odds_move") or {}
    if mv.get("known") and (mv.get("value") or 0) >= 0.6:
        bits.append("赔率波动明显")
    if r["risk_level"] == "HIGH":
        bits.append("风险等级高")
    if not bits:
        bits.append("各项指标尚可，但综合评分低于入选线")
    return f"综合评分 {r['match_score']:.3f} 低于入选线 {cutoff:.3f}（第 {r['rank']} 位）：" + "，".join(bits)
