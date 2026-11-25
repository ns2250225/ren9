"""预测引擎：Elo + Poisson + Dixon-Coles + 市场去水概率 → 集成概率。

所有概率均由这里的 Python 计算得出；任何模型缺少输入时直接不参与集成（权重重新归一化），
全部模型都不可用时该场不给出概率。
"""
from __future__ import annotations

from datetime import date

from models import elo as elo_model
from models import ensemble
from models.dixon_coles import DixonColesModel
from models.poisson import PoissonModel, calibrate_matrix, handicap_probs
from ren9 import history
from ren9.common import OUTCOMES, data_sources, model_weights, now_iso, rnd, settings

_fit_memo: dict[tuple, dict] = {}


def ensemble_config() -> tuple[dict, float, str]:
    """返回 (权重, 校准温度, 来源说明)。存在回测学习结果且启用时优先使用。"""
    mw = model_weights()
    ens = mw.get("ensemble", {})
    learned = ens.get("learned")
    if ens.get("use_learned", True) and learned and learned.get("weights"):
        return dict(learned["weights"]), float(learned.get("temperature", 1.0)), (
            f"回测学习（{learned.get('source', 'backtest')}，样本 {learned.get('samples')}，{learned.get('fitted_at')}）"
        )
    return dict(ens.get("initial", {})), float(mw.get("calibration", {}).get("temperature", 1.0)), "初始配置（尚未回测校准）"


def fitted_models(ds: dict, ref: date) -> dict:
    key = (ds["kind"], ds["key"], ref.isoformat())
    if key not in _fit_memo:
        mw = model_weights()
        pc, dc, ec = mw.get("poisson", {}), mw.get("dixon_coles", {}), mw.get("elo", {})
        _fit_memo[key] = {
            "poisson": PoissonModel(pc.get("half_life_days", 240), pc.get("shrink_matches", 4),
                                    pc.get("xg_blend", 0.4), pc.get("max_goals", 10)).fit(ds["rows"], ref),
            "dixon_coles": DixonColesModel(dc.get("half_life_days", 240), dc.get("ridge", 0.6),
                                           dc.get("max_goals", 10)).fit(ds["rows"], ref),
            "elo": elo_model.EloRatings(ec.get("k_factor", 20), ec.get("home_advantage", 65),
                                        ec.get("initial_rating", 1500)).fit(ds["rows"]),
        }
    return _fit_memo[key]


def round_probs(p: dict, n: int = 4) -> dict:
    """四舍五入并保证三项之和严格等于 1。"""
    r = {k: round(p[k], n) for k in OUTCOMES}
    top = max(OUTCOMES, key=lambda k: r[k])
    r[top] = round(r[top] + (1.0 - sum(r.values())), n)
    return r


def _quality(sched_official: bool, td: dict | None, od: dict | None, elo_src: str | None) -> tuple[float, dict]:
    parts: dict[str, float] = {}
    parts["schedule"] = 1.0 if sched_official else 0.6
    parts["resolution"] = (td or {}).get("resolution", {}).get("confidence", 0.0) if td else 0.0
    h = (td or {}).get("history")
    parts["history"] = min(1.0, min(h["home_matches"], h["away_matches"]) / 12.0) if h else 0.0
    n_src = len((od or {}).get("sources") or [])
    parts["market"] = 1.0 if n_src >= 2 else 0.8 if n_src == 1 else 0.0
    parts["elo"] = 1.0 if elo_src == "ClubElo" else 0.6 if elo_src else 0.0
    teams = (td or {}).get("teams") or {}
    parts["standings"] = sum(0.5 for s in ("home", "away") if (teams.get(s) or {}).get("standing"))
    parts["form"] = sum(0.5 for s in ("home", "away") if ((teams.get(s) or {}).get("form") or {}).get("last5"))
    ko = (td or {}).get("kickoff") or {}
    parts["kickoff"] = 1.0 if ko.get("cross_validated") else 0.7 if ko.get("value") else 0.0
    parts["lineup"] = sum(0.5 for s in ("home", "away")
                          if ((teams.get(s) or {}).get("lineup") or {}).get("official_lineup_published"))
    w = {"schedule": 0.10, "resolution": 0.15, "history": 0.25, "market": 0.20, "elo": 0.10,
         "standings": 0.05, "form": 0.05, "kickoff": 0.05, "lineup": 0.05}
    score = sum(w[k] * parts[k] for k in w)
    return round(score, 4), {k: round(v, 3) for k, v in parts.items()}


def predict_match(m: dict, td: dict | None, od: dict | None, sched_official: bool) -> dict:
    cfg = settings()
    enabled = [k for k, v in cfg.get("models", {}).items() if v]
    min_hist = cfg["data"].get("min_history_matches", 4)
    mw = model_weights()
    ec = mw.get("elo", {})
    hfa, nu = ec.get("home_advantage", 65), ec.get("draw_nu", 0.78)
    probs: dict[str, dict] = {}
    details: dict[str, dict] = {}
    unavailable: dict[str, str] = {}
    matrix = None
    elo_src = None

    ds = None
    h = (td or {}).get("history")
    if h:
        comp = data_sources().get("competitions", {}).get(td.get("competition") or "")
        ref = date.fromisoformat(td["ref_date"])
        ds = next((d for d in history.load(comp, ref, kind=h["kind"]) if d["key"] == h["key"]), None)
    fits = fitted_models(ds, date.fromisoformat(td["ref_date"])) if ds else None

    # 模型 A：Elo
    if "elo" in enabled:
        teams = (td or {}).get("teams") or {}
        eh, ea = (teams.get("home") or {}).get("elo"), (teams.get("away") or {}).get("elo")
        if eh and ea:
            r = elo_model.predict(eh["value"], ea["value"], hfa, nu)
            elo_src = "ClubElo"
            details["elo"] = {**{k: r[k] for k in ("home_elo", "away_elo", "elo_difference")},
                              "home_advantage": hfa, "source": "ClubElo（跨联赛可比）"}
            probs["elo"] = r["probabilities"]
        elif fits and min(fits["elo"].games.get(h["home_name"], 0), fits["elo"].games.get(h["away_name"], 0)) >= min_hist:
            r = elo_model.predict(fits["elo"].get(h["home_name"]), fits["elo"].get(h["away_name"]), hfa, nu)
            elo_src = "self"
            details["elo"] = {**{k: r[k] for k in ("home_elo", "away_elo", "elo_difference")},
                              "home_advantage": hfa,
                              "source": f"由 {'/'.join(h['source'][:2])} 历史赛果自算（仅本赛事内可比）"}
            probs["elo"] = r["probabilities"]
        else:
            unavailable["elo"] = "缺少双方 Elo（ClubElo 不可用且历史样本不足）"

    # 模型 B / C：Poisson 与 Dixon-Coles
    for name in ("poisson", "dixon_coles"):
        if name not in enabled:
            continue
        if not fits:
            unavailable[name] = "没有双方共同的历史比赛数据集"
            continue
        model = fits[name]
        if not model.fitted:
            unavailable[name] = "历史样本不足，模型未能拟合"
            continue
        nh, na = model.matches_of(h["home_name"]), model.matches_of(h["away_name"])
        if min(nh, na) < min_hist:
            unavailable[name] = f"历史场次不足（主 {nh} / 客 {na}，至少需要 {min_hist}）"
            continue
        r = model.predict(h["home_name"], h["away_name"])
        if not r:
            unavailable[name] = "球队不在拟合数据中"
            continue
        if name == "dixon_coles" or matrix is None:
            matrix = r["matrix"]
        probs[name] = r["probabilities"]
        details[name] = {k: v for k, v in r.items() if k not in ("matrix", "probabilities")}
        details[name].update(sample={"home": nh, "away": na}, source="/".join(h["source"][:2]))

    # 模型 D：市场去水概率
    if "market" in enabled:
        mk = (od or {}).get("market")
        if mk:
            f = mk["fair_probability"]
            probs["market"] = {"win": f["home"], "draw": f["draw"], "loss": f["away"]}
            details["market"] = {"sources_used": mk["sources_used"]}
        else:
            unavailable["market"] = "没有可用赔率"

    quality, qparts = _quality(sched_official, td, od, elo_src)
    weights, temperature, wsrc = ensemble_config()
    # 小样本降权：基于历史赛果的模型（Poisson / Dixon-Coles / 自算 Elo）按样本量线性折减权重，
    # 样本达到 full_weight_matches 场才用满权重，避免几场比赛拟合出的结果主导集成。
    full = cfg["data"].get("full_weight_matches", 20)
    reliability = {}
    if h:
        rel = round(min(1.0, min(h["home_matches"], h["away_matches"]) / full), 3)
        for name in ("poisson", "dixon_coles") + (("elo",) if elo_src == "self" else ()):
            if name in probs:
                reliability[name] = rel
                weights[name] = weights.get(name, 0.0) * rel
    combined = ensemble.combine(probs, weights, temperature)
    out = {
        "match_id": m["match_id"], "seq": m.get("seq"), "home_team": m["home_team"], "away_team": m["away_team"],
        "competition": m.get("competition"),
        "kickoff": ((td or {}).get("kickoff") or {}).get("value") or m.get("kickoff"),
        "match_status": (td or {}).get("status") or m.get("status"),
        "data_quality": quality, "quality_breakdown": qparts,
        "models_used": [k for k in ("elo", "poisson", "dixon_coles", "market") if k in probs],
        "models_unavailable": unavailable,
        "model_probabilities": {k: round_probs(v) for k, v in probs.items()},
        "model_details": details,
        "ensemble_weights_source": wsrc,
        "calibration_temperature": temperature,
        "sample_reliability": reliability,
    }
    if not combined:
        out.update(status="no_prediction", probabilities=None, prediction=None, model_confidence=None,
                   agreement=None, weights_used={},
                   reason="所有模型都缺少输入数据，无法计算概率（不会用猜测值代替）")
        return out
    final = round_probs(combined["probabilities"])
    top = max(OUTCOMES, key=lambda k: final[k])
    out.update(
        status="ok",
        probabilities=final,
        prediction=top.upper(),
        # model_confidence 衡量模型间一致性/覆盖度/结果明确度，不是命中概率
        model_confidence=ensemble.confidence(probs, final, len(enabled)),
        agreement=ensemble.agreement(probs),
        weights_used=combined["weights_used"],
    )
    eg = details.get("dixon_coles") or details.get("poisson")
    if eg:
        out["expected_goals"] = {"home": eg["home_expected_goals"], "away": eg["away_expected_goals"]}
    # 让球胜平负（竞彩扩展玩法）
    jh = (od or {}).get("jc_handicap")
    if jh and jh.get("line") is not None:
        hc = {"line": jh["line"], "official_odds": jh["odds"],
              "market_fair": {"win": jh["fair_probability"]["home"], "draw": jh["fair_probability"]["draw"],
                              "loss": jh["fair_probability"]["away"]}}
        if matrix is not None:
            # 比分矩阵先校准到集成概率，保证让球概率与最终胜平负概率一致
            hc["model"] = round_probs(handicap_probs(calibrate_matrix(matrix, final), jh["line"]))
            hc["model_source"] = ("dixon_coles" if "dixon_coles" in probs else "poisson") + " 比分分布 + 集成概率校准"
        out["handicap"] = hc
    return out


def predict(schedule: dict, team_data: dict | None, odds: dict | None,
            only_seqs: list[int] | None = None, previous: dict | None = None) -> dict:
    td = {x["match_id"]: x for x in (team_data or {}).get("matches", [])}
    od = {x["match_id"]: x for x in (odds or {}).get("matches", [])}
    prev = {x["match_id"]: x for x in (previous or {}).get("matches", [])}
    official = bool(schedule.get("official", True))
    out = []
    for m in schedule.get("matches") or []:
        mid = m["match_id"]
        if only_seqs and m.get("seq") not in only_seqs and mid in prev:
            out.append(prev[mid])
        else:
            out.append(predict_match(m, td.get(mid), od.get(mid), official))
    return {
        "date": schedule.get("date"), "game_type": schedule.get("game_type"), "period": schedule.get("period"),
        "generated_at": now_iso(), "matches": out,
        "predicted": sum(1 for x in out if x["status"] == "ok"), "total": len(out),
        "config_fingerprint": config_fingerprint(),
    }


def config_fingerprint() -> str:
    """模型参数与相关设置的指纹：配置或学习到的权重变化后，已保存的概率需要重算。"""
    import hashlib
    import json

    cfg = settings()
    blob = json.dumps([model_weights(), cfg.get("models"), cfg.get("data", {}).get("min_history_matches"),
                       cfg.get("data", {}).get("full_weight_matches")], sort_keys=True, default=str)
    return hashlib.sha1(blob.encode("utf-8")).hexdigest()[:12]
