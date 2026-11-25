"""比赛风险分析：多因子评分，不只看最大概率。

每个因子取值 0~1（越大风险越高）；采集不到数据的因子标记为 unknown，
按配置的中性值计入并在结果中明确提示，而不是当作"没有风险"。
"""
from __future__ import annotations

from ren9.common import OUTCOMES, model_weights, now_iso, settings

FACTOR_ZH = {
    "closeness": "概率接近",
    "disagreement": "模型分歧",
    "data_gap": "数据不足",
    "odds_move": "赔率波动",
    "lineup": "伤停/首发不确定",
    "fatigue": "赛程压力",
    "context": "赛事性质",
    "form_motivation": "状态/战意与推荐方向",
}


def clip(x: float) -> float:
    return max(0.0, min(1.0, x))


def closeness(p: dict) -> float:
    """概率接近程度：首选概率越低、与次选差距越小，风险越高。"""
    v = sorted((p[k] for k in OUTCOMES), reverse=True)
    a = clip(1 - (v[0] - 1 / 3) / (0.75 - 1 / 3))
    b = clip(1 - (v[0] - v[1]) / 0.35)
    return round(0.6 * a + 0.4 * b, 4)


def assess_match(pred: dict, td: dict | None, od: dict | None, form_data: dict | None = None) -> dict:
    mw = model_weights().get("risk", {})
    weights = mw.get("weights", {})
    unknown_val = mw.get("unknown_factor_value", 0.5)
    th = mw.get("thresholds", {"low": 0.33, "medium": 0.52})
    min_q = settings().get("safety", {}).get("min_data_quality", 0.35)
    base = {"match_id": pred["match_id"], "seq": pred.get("seq"), "home_team": pred["home_team"],
            "away_team": pred["away_team"]}

    if pred.get("status") != "ok" or not pred.get("probabilities"):
        return {**base, "risk_level": "UNKNOWN", "risk_score": None, "factors": {},
                "reasons": ["模型无法计算概率（缺少输入数据）"], "unknown_factors": list(FACTOR_ZH.values())}

    p = pred["probabilities"]
    factors: dict[str, dict] = {}

    def put(name: str, value: float | None, note: str) -> None:
        factors[name] = {"value": None if value is None else round(clip(value), 4),
                         "known": value is not None, "note": note}

    c = closeness(p)
    v = sorted(p.values(), reverse=True)
    put("closeness", c, f"首选 {v[0]:.0%}，次选 {v[1]:.0%}")

    agr = pred.get("agreement")
    if agr is None:
        put("disagreement", None, f"只有 {len(pred.get('models_used', []))} 个模型可用，无法衡量模型一致性")
    else:
        picks = {max(OUTCOMES, key=lambda k: mp[k]) for mp in pred["model_probabilities"].values()}
        put("disagreement", 1 - agr, "各模型首选一致" if len(picks) == 1 else f"模型首选不一致（{len(picks)} 种结果）")

    q = pred.get("data_quality") or 0.0
    put("data_gap", 1 - q, f"数据质量 {q:.2f}")

    mv = (od or {}).get("movement") or {}
    if mv.get("known"):
        put("odds_move", mv["max_change"] / 0.06, f"去水概率最大变动 {mv['max_change']:.1%}")
    else:
        put("odds_move", None, "没有可比较的历史赔率（首次抓取或无赔率）")

    teams = (td or {}).get("teams") or {}
    lineups = [((teams.get(s) or {}).get("lineup") or {}).get("official_lineup_published") for s in ("home", "away")]
    if td and all(lineups):
        put("lineup", 0.15, "双方官方首发已公布")
    else:
        put("lineup", None, "官方首发未公布，伤停信息无可靠来源")

    rests = [((teams.get(s) or {}).get("schedule") or {}) for s in ("home", "away")]
    if all(r.get("rest_days") is not None for r in rests):
        def one(r: dict) -> float:
            d = r["rest_days"]
            f = 1.0 if d <= 2 else 0.6 if d == 3 else 0.3 if d == 4 else 0.0
            if (r.get("matches_last_14d") or 0) >= 4:
                f = max(f, 0.6)
            return f

        fh, fa = one(rests[0]), one(rests[1])
        put("fatigue", max(fh, fa) * 0.7 + abs(fh - fa) * 0.3,
            f"休息天数 主 {rests[0]['rest_days']} / 客 {rests[1]['rest_days']}")
    else:
        put("fatigue", None, "缺少双方最近比赛日期")

    imp = (td or {}).get("importance")
    ctype = (td or {}).get("competition_type")
    if ctype == "cup":
        put("context", 0.7, "杯赛（轮换、加时风险）")
    elif ctype == "international":
        put("context", 0.6, "国家队赛事")
    elif ctype == "continental":
        put("context", 0.45, "洲际赛事")
    elif ctype == "league" and imp:
        put("context", 0.5 if imp.get("notes") else 0.15, "；".join(imp["notes"]) if imp.get("notes") else "常规联赛轮次")
    else:
        put("context", None, "赛事性质未知")

    # 状态 / 战意因子：近期状态和战意是否支持首选结果（支持则降风险，相悖则升风险）
    fd = (form_data or {}).get("form_diff")
    mg = (form_data or {}).get("motivation_gap")
    if fd is None and mg is None:
        put("form_motivation", None, "缺少近期状态和战意数据" if form_data else "未执行状态分析")
    else:
        lean = (fd or 0.0) + (mg or 0.0)                 # >0 利好主队
        top = max(OUTCOMES, key=lambda k: p[k])
        support = lean if top == "win" else -lean if top == "loss" else -abs(lean)
        bits = ([f"状态差 {fd:+.2f}"] if fd is not None else ["状态未知"]) +                ([f"战意差 {mg:+.2f}"] if mg is not None else ["战意未知"])
        verdict = "支持首选" if support >= 0.1 else "与首选相悖" if support <= -0.1 else "对首选影响不大"
        put("form_motivation", 0.5 - 0.8 * support, f"{'，'.join(bits)}（主 − 客），{verdict}")

    total_w = sum(weights.get(k, 0) for k in factors) or 1.0
    score = sum(weights.get(k, 0) * (f["value"] if f["known"] else unknown_val) for k, f in factors.items()) / total_w
    unknown = [FACTOR_ZH[k] for k, f in factors.items() if not f["known"]]

    if q < min_q:
        level = "UNKNOWN"
    elif score < th.get("low", 0.33):
        level = "LOW"
    elif score < th.get("medium", 0.52):
        level = "MEDIUM"
    else:
        level = "HIGH"
    # 概率接近度对等级有硬约束：首选优势不明显不能判为低风险；三项接近直接判为高风险
    if level != "UNKNOWN":
        if c >= 0.70:
            level = "HIGH"
        elif c >= 0.33 and level == "LOW":
            level = "MEDIUM"

    ranked = sorted(((weights.get(k, 0) * f["value"], k) for k, f in factors.items() if f["known"]), reverse=True)
    reasons = [f"{FACTOR_ZH[k]}：{factors[k]['note']}" for _, k in ranked[:3] if factors[k]["value"] >= 0.35]
    if level == "UNKNOWN":
        reasons.insert(0, f"数据质量 {q:.2f} 低于阈值 {min_q}，数据严重缺失")
    if not reasons:
        reasons = [f"{FACTOR_ZH['closeness']}：{factors['closeness']['note']}"]
    return {**base, "risk_level": level, "risk_score": round(score, 4), "factors": factors,
            "reasons": reasons, "unknown_factors": unknown}


def assess(predictions: dict, team_data: dict | None, odds: dict | None, form_analysis: dict | None = None) -> dict:
    td = {x["match_id"]: x for x in (team_data or {}).get("matches", [])}
    od = {x["match_id"]: x for x in (odds or {}).get("matches", [])}
    fd = {x["match_id"]: x for x in (form_analysis or {}).get("matches", [])} if form_analysis else {}
    out = [assess_match(p, td.get(p["match_id"]), od.get(p["match_id"]), fd.get(p["match_id"]))
           for p in predictions.get("matches", [])]
    counts = {lv: sum(1 for x in out if x["risk_level"] == lv) for lv in ("LOW", "MEDIUM", "HIGH", "UNKNOWN")}
    return {"date": predictions.get("date"), "game_type": predictions.get("game_type"),
            "period": predictions.get("period"), "generated_at": now_iso(), "matches": out, "counts": counts}
