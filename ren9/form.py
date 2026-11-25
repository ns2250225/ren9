"""球队近况状态和战意分析模块。

分析球队的近期表现状态、连胜/连败势头、战意强度，
结合积分榜位置（争冠、保级、洲际赛事出线形势）评估双方战意差异，
并据此对集成概率做有上限的小幅修正（adjust_probabilities）。
"""
from __future__ import annotations

import math

from ren9.common import OUTCOMES, data_sources, model_weights, now_iso

# 分析逻辑版本：变化后流水线会丢弃已保存的 form_analysis 并重算
VERSION = 2

RESULT_SCORE = {"W": 1.0, "D": 1 / 3, "L": 0.0}          # 按积分折算（胜 3 / 平 1 / 负 0）
RECENCY_WEIGHTS = (1.0, 0.9, 0.8, 0.7, 0.6)              # 新→旧
MIN_RESULTS = 3                                          # 少于该场数不评估状态


def analyze_streak(results: list[str]) -> dict:
    """分析连胜/连败/连平势头。results：新→旧，每项为 'W'/'D'/'L'。"""
    if not results:
        return {"current_streak": None, "momentum_score": None, "stability": None}
    streak_type, streak_count = results[0], 1
    for r in results[1:]:
        if r != streak_type:
            break
        streak_count += 1
    weights = [1.0, 0.85, 0.7, 0.55, 0.4, 0.3, 0.2, 0.15, 0.1, 0.05][:len(results)]
    scores = {"W": 1.0, "D": 0.5, "L": 0.0}
    momentum = round(sum(w * scores[r] for w, r in zip(weights, results)) / sum(weights), 4)
    changes = sum(1 for a, b in zip(results, results[1:]) if a != b)
    stability = round(1 - changes / max(1, len(results) - 1), 4)
    return {"current_streak": {"type": streak_type, "count": streak_count},
            "momentum_score": momentum, "stability": stability}


def game_goals(game: dict) -> tuple[int, int] | None:
    """从一场近期比赛中取出（进球, 失球）。

    优先用 goals_for / goals_against；否则解析 score 字符串。ESPN 近 5 场的比分
    不保证是"本队-对手"的顺序（常为胜方在前），所以按赛果分配大小比分，与顺序无关。
    """
    if game.get("goals_for") is not None and game.get("goals_against") is not None:
        return int(game["goals_for"]), int(game["goals_against"])
    try:
        a, b = (int(x) for x in str(game.get("score") or "").replace(":", "-").split("-")[:2])
    except (ValueError, TypeError):
        return None
    hi, lo = max(a, b), min(a, b)
    res = game.get("result")
    if res == "W":
        return hi, lo
    if res == "L":
        return lo, hi
    if res == "D":
        return lo, lo                    # 点球决胜等情况下比分不相等，按常规时间平局处理
    return None


def form_score(results: list[str], goals: list[tuple[int, int]] | None = None) -> dict:
    """状态质量 0~1。results：新→旧；goals：对应的（进球, 失球），可缺省。

    回测（backtest.form_features）与实时分析共用这一函数，保证校准出的系数可直接使用。
    """
    results = [r for r in results if r in RESULT_SCORE][:len(RECENCY_WEIGHTS)]
    if len(results) < MIN_RESULTS:
        return {"results_form": None, "offensive_form": None, "defensive_form": None,
                "overall_form": None, "basis": None}
    w = RECENCY_WEIGHTS[:len(results)]
    res_form = sum(wi * RESULT_SCORE[r] for wi, r in zip(w, results)) / sum(w)
    goals = [g for g in (goals or []) if g][:len(RECENCY_WEIGHTS)]
    if len(goals) >= MIN_RESULTS:
        offensive = min(1.0, sum(g[0] for g in goals) / len(goals) / 2.5)
        defensive = max(0.0, 1 - sum(g[1] for g in goals) / len(goals) / 2.5)
        overall, basis = 0.6 * res_form + 0.2 * offensive + 0.2 * defensive, "赛果+进失球"
    else:
        offensive = defensive = None
        overall, basis = res_form, "仅赛果"
    r4 = lambda x: None if x is None else round(x, 4)   # noqa: E731
    return {"results_form": r4(res_form), "offensive_form": r4(offensive), "defensive_form": r4(defensive),
            "overall_form": r4(overall), "basis": basis}


def analyze_form_quality(form_data: dict | None) -> dict:
    """综合评估球队状态质量（输入为 teamdata 的 form 字段）。"""
    last5 = (form_data or {}).get("last5")
    if not isinstance(last5, dict):
        return form_score([])
    games = [g for g in last5.get("games") or [] if isinstance(g, dict)]
    return form_score(last5.get("results") or [], [game_goals(g) for g in games])


def _level(score: float | None) -> str:
    if score is None:
        return "UNKNOWN"
    return "HIGH" if score >= 0.75 else "MEDIUM" if score >= 0.55 else "LOW"


def _motivation(score: float | None, factor: str) -> dict:
    return {"motivation_level": _level(score), "factors": [factor],
            "score": None if score is None else round(score, 4)}


def league_motivation(rank: int, total: int, played: int) -> tuple[float, str]:
    """联赛战意：争冠 / 欧战 / 保级区在赛季中后段战意高，中游球队赛季末战意低。"""
    rounds = 2 * (total - 1) if total > 1 else 38
    progress = played / rounds if rounds else 0
    if rank <= 2:
        return (0.9, f"争冠区（第{rank}名，赛季进度{progress:.0%}）") if progress >= 0.6 else (0.75, f"排名靠前（第{rank}名）")
    if rank <= 6 and progress >= 0.5:
        return 0.7, f"欧战区（第{rank}名）"
    if rank > total - 3:
        return (0.85, f"保级区（第{rank}名，赛季进度{progress:.0%}）") if progress >= 0.5 else (0.65, f"排名靠后（第{rank}名）")
    if progress < 0.3:
        return 0.6, "赛季初期，排名未定"
    if progress > 0.8:
        return 0.4, "赛季末期，中游无欲无求"
    return 0.5, "中游位置"


def league_phase_motivation(rank: int, played: int, phase: dict) -> tuple[float, str]:
    """洲际赛事联赛阶段战意：按直接晋级线 / 附加赛线的距离和赛程进度判断。

    phase: {rounds, direct, playoff}，来自 data_sources.yaml 中该赛事的 league_phase 配置。
    """
    rounds, direct, playoff = phase["rounds"], phase["direct"], phase["playoff"]
    progress = played / rounds if rounds else 0
    if played >= rounds:
        return 0.8, "联赛阶段已结束，进入淘汰赛"
    if progress < 0.5:
        return 0.7, f"联赛阶段前半程（{played}/{rounds} 轮），排名未定"
    if rank <= direct // 2:
        return 0.65, f"第{rank}名，直接晋级形势主动（可能轮换）"
    if rank <= direct:
        return 0.8, f"第{rank}名，力保前{direct}直接晋级"
    if rank <= direct + 4:
        return 0.85, f"第{rank}名，冲击前{direct}直接晋级"
    if rank <= playoff - 3:
        return 0.75, f"第{rank}名，处于附加赛区"
    if rank <= playoff + 5:
        return 0.9, f"第{rank}名，在出线线（前{playoff}）附近，必须抢分"
    if progress >= 0.75:
        return 0.45, f"第{rank}名，出线希望渺茫"
    return 0.7, f"第{rank}名，落后出线区较多但仍有机会"


def analyze_motivation(standing: dict | None, competition_type: str | None,
                       league_phase: dict | None = None) -> dict:
    """分析球队战意强度，返回 {motivation_level, factors, score}（score 0~1，未知为 None）。"""
    if competition_type == "cup":
        return _motivation(0.85, "杯赛淘汰制，每场都重要")
    if competition_type == "international":
        return _motivation(0.6, "国家队赛事")
    has_rank = bool(standing) and standing.get("rank") is not None
    if competition_type == "continental":
        if has_rank and league_phase:
            return _motivation(*league_phase_motivation(standing["rank"], standing.get("played") or 0, league_phase))
        return _motivation(0.8, "洲际赛事（未配置赛制或缺少积分榜，双方按同等战意处理）")
    if not has_rank:
        return _motivation(None, "缺少积分榜信息")
    return _motivation(*league_motivation(standing["rank"], standing.get("teams_total") or 20,
                                          standing.get("played") or 0))


def adjust_config() -> dict:
    """概率修正参数：config/model_weights.yaml 的 form_adjust，回测学习到系数时优先使用。"""
    cfg = dict(model_weights().get("form_adjust") or {})
    learned = cfg.get("learned")
    out = {"enabled": cfg.get("enabled", True), "form_coef": float(cfg.get("form_coef", 0.0)),
           "motivation_coef": float(cfg.get("motivation_coef", 0.0)),
           "max_shift": float(cfg.get("max_shift", 0.04)), "source": "初始配置（尚未回测校准）"}
    if cfg.get("use_learned", True) and learned:
        out["form_coef"] = float(learned.get("form_coef", out["form_coef"]))
        out["motivation_coef"] = float(learned.get("motivation_coef", out["motivation_coef"]))
        out["source"] = f"回测学习（样本 {learned.get('samples')}，{learned.get('fitted_at')}）"
    return out


def shift_probs(p: dict, signal: float) -> dict:
    """按 signal（>0 利好主队）在对数几率上平移：主胜 ×e^s，客胜 ×e^-s，平局不变，再归一化。"""
    raw = {"win": p["win"] * math.exp(signal), "draw": p["draw"], "loss": p["loss"] * math.exp(-signal)}
    total = sum(raw.values())
    return {k: raw[k] / total for k in OUTCOMES}


def adjust_probabilities(p: dict, form_match: dict | None, cfg: dict | None = None) -> tuple[dict, dict]:
    """用状态差与战意差修正胜平负概率，返回（修正后概率, 修正说明）。

    任一项概率的变动不超过 max_shift；状态和战意都未知时不修正。
    """
    cfg = cfg or adjust_config()
    fd = (form_match or {}).get("form_diff")
    mg = (form_match or {}).get("motivation_gap")
    info = {"applied": False, "form_diff": fd, "motivation_gap": mg, "signal": 0.0,
            "form_coef": cfg["form_coef"], "motivation_coef": cfg["motivation_coef"],
            "max_shift": cfg["max_shift"], "source": cfg["source"]}
    if not cfg["enabled"]:
        return p, {**info, "reason": "已在配置中关闭"}
    if fd is None and mg is None:
        return p, {**info, "reason": "缺少近期状态和战意数据"}
    signal = cfg["form_coef"] * (fd or 0.0) + cfg["motivation_coef"] * (mg or 0.0)
    if abs(signal) < 1e-9:
        return p, {**info, "reason": "双方状态与战意相当，或修正系数为 0"}
    adj = shift_probs(p, signal)
    biggest = max(abs(adj[k] - p[k]) for k in OUTCOMES)
    capped = biggest > cfg["max_shift"]
    if capped:                              # 超过上限时按比例缩回（p 与 adj 的凸组合，仍是合法概率）
        t = cfg["max_shift"] / biggest
        adj = {k: p[k] + t * (adj[k] - p[k]) for k in OUTCOMES}
    return adj, {**info, "applied": True, "signal": round(signal, 4), "capped": capped,
                 "before": {k: round(p[k], 4) for k in OUTCOMES},
                 "delta": {k: round(adj[k] - p[k], 4) for k in OUTCOMES}}


def assess_match_form(team_data: dict) -> dict:
    """评估单场比赛的双方状态和战意对比（输入为 teamdata.collect_match 的结果）。"""
    teams = team_data.get("teams") or {}
    ctype = team_data.get("competition_type")
    comp = data_sources().get("competitions", {}).get(team_data.get("competition") or "") or {}
    phase = comp.get("league_phase")

    def analyze_team(side: str) -> dict:
        team = teams.get(side) or {}
        form_raw = team.get("form") or {}
        last5 = form_raw.get("last5") if isinstance(form_raw.get("last5"), dict) else {}
        results = [r for r in last5.get("results") or [] if r in RESULT_SCORE][:5]
        return {"team": team.get("name"), "side": side, "streak": analyze_streak(results),
                "motivation": analyze_motivation(team.get("standing"), ctype, phase),
                "form_quality": analyze_form_quality(form_raw), "recent_results": results,
                "scope": last5.get("scope")}

    home, away = analyze_team("home"), analyze_team("away")
    h_mot, a_mot = home["motivation"]["score"], away["motivation"]["score"]
    motivation_gap = round(h_mot - a_mot, 4) if h_mot is not None and a_mot is not None else None
    h_q, a_q = home["form_quality"]["overall_form"], away["form_quality"]["overall_form"]
    form_diff = round(h_q - a_q, 4) if h_q is not None and a_q is not None else None
    form_adv = ("unknown" if form_diff is None else "home" if form_diff >= 0.15
                else "away" if form_diff <= -0.15 else "balanced")

    notes = []
    for side_form, zh in ((home, "主队"), (away, "客队")):
        q = side_form["form_quality"]
        if q["overall_form"] is not None:
            notes.append(f"{zh}近 {len(side_form['recent_results'])} 场 {''.join(side_form['recent_results'])}"
                         f"（状态 {q['overall_form']:.2f}，{q['basis']}）")
        st = side_form["streak"].get("current_streak")
        if st and st["count"] >= 3:
            label = {"W": "连胜", "D": "连平", "L": "连败"}[st["type"]]
            mood = {"W": "强劲", "D": "平淡", "L": "低迷"}[st["type"]]
            notes.append(f"{side_form['team']}近期{label}{st['count']}场，势头{mood}")
    if form_diff is not None and form_adv != "balanced":
        notes.append(f"{'主队' if form_diff > 0 else '客队'}状态明显更好（差距 {abs(form_diff):.2f}）")
    for side_form, zh in ((home, "主队"), (away, "客队")):
        notes.append(f"{zh}战意 {side_form['motivation']['motivation_level']}：{'；'.join(side_form['motivation']['factors'])}")
    if motivation_gap is not None and abs(motivation_gap) >= 0.2:
        notes.append(f"{'主队' if motivation_gap > 0 else '客队'}战意明显更强（差距 {abs(motivation_gap):.2f}）")

    return {"match_id": team_data.get("match_id"), "home_team": team_data.get("home_team"),
            "away_team": team_data.get("away_team"), "home_form": home, "away_form": away,
            "form_diff": form_diff, "motivation_gap": motivation_gap, "form_advantage": form_adv,
            "notes": notes, "generated_at": now_iso()}


def assess(team_data: dict) -> dict:
    """批量评估所有比赛的状态和战意（输入为 teamdata.collect 的结果）。"""
    results = [assess_match_form(md) for md in (team_data or {}).get("matches", [])]
    return {
        "version": VERSION,
        "date": team_data.get("date"), "game_type": team_data.get("game_type"), "period": team_data.get("period"),
        "generated_at": now_iso(), "matches": results,
        "summary": {
            "total_matches": len(results),
            "form_gaps": sum(1 for r in results if r["form_advantage"] in ("home", "away")),
            "high_motivation_gaps": sum(1 for r in results
                                        if r["motivation_gap"] is not None and abs(r["motivation_gap"]) >= 0.2),
            "strong_streaks": sum(1 for r in results for side in ("home_form", "away_form")
                                  if (r[side]["streak"].get("current_streak") or {}).get("count", 0) >= 3),
        },
    }
