"""主力缺阵分析：由官方首发推断。

没有可靠的免费足球伤停名单（ESPN 的足球伤停接口恒为空），所以不采集伤停传闻，
而是在官方首发公布后，把本场首发与该队最近几场的首发对比：
最近常规首发（主力）这次没有首发，就记为缺阵——不在大名单按完全缺阵计，
坐替补席按部分缺阵计（轮换，仍可能替补出场）。缺阵原因（伤病/停赛/轮换）未知。

官方首发一般在开球前约 1 小时公布，在此之前本维度为"未知"，不影响概率。
"""
from __future__ import annotations

from ren9 import form
from ren9.common import model_weights


def config() -> dict:
    """缺阵分析与概率修正参数（config/model_weights.yaml 的 lineup_adjust）。"""
    cfg = dict(model_weights().get("lineup_adjust") or {})
    return {"enabled": cfg.get("enabled", True), "coef": float(cfg.get("coef", 0.5)),
            "max_shift": float(cfg.get("max_shift", 0.04)),
            "regular_start_rate": float(cfg.get("regular_start_rate", 0.6)),
            "min_games": int(cfg.get("min_games", 3)), "bench_weight": float(cfg.get("bench_weight", 0.4)),
            "rotation_baseline": float(cfg.get("rotation_baseline", 0.15)),
            "source": "初始配置（没有历史首发数据可供回测校准）"}


def regulars(history: list[list[dict]], cfg: dict | None = None) -> list[dict]:
    """由最近几场的首发名单找出主力。

    history: 每场一个首发名单 [{id, name, position}]，新→旧。
    返回 [{id, name, position, starts, games, importance}]，importance = 首发率。
    """
    cfg = cfg or config()
    games = len(history)
    if games < cfg["min_games"]:
        return []
    seen: dict[str, dict] = {}
    for starters in history:
        for p in starters:
            row = seen.setdefault(p["id"], {"id": p["id"], "name": p.get("name"), "position": p.get("position"),
                                            "starts": 0, "games": games})
            row["starts"] += 1
    out = [{**r, "importance": round(r["starts"] / games, 4)} for r in seen.values()
           if r["starts"] / games >= cfg["regular_start_rate"]]
    return sorted(out, key=lambda r: (-r["starts"], r["name"] or ""))


def assess_team(players: list[dict] | None, history: list[list[dict]], cfg: dict | None = None) -> dict:
    """对比本场名单与近期首发，给出主力缺阵情况。

    players: 本场官方名单 [{id, name, position, starter}]；未公布时为 None。
    返回 {known, absence_score, missing, regulars, games_used, reason}；
    absence_score 0~1 = 缺阵主力的重要性占全部主力重要性的比例。
    """
    cfg = cfg or config()
    base = {"known": False, "absence_score": None, "missing": [], "regulars": [], "games_used": len(history),
            "method": "由官方首发对比近期首发推断，缺阵原因未知"}
    if not players:
        return {**base, "reason": "官方首发未公布"}
    regs = regulars(history, cfg)
    if not regs:
        return {**base, "reason": f"近期首发记录不足（{len(history)} 场，至少需要 {cfg['min_games']} 场）"}
    starting = {p["id"] for p in players if p.get("starter")}
    squad = {p["id"] for p in players}
    missing, lost = [], 0.0
    for r in regs:
        if r["id"] in starting:
            continue
        on_bench = r["id"] in squad
        weight = cfg["bench_weight"] if on_bench else 1.0
        lost += r["importance"] * weight
        missing.append({"id": r["id"], "name": r["name"], "position": r["position"], "starts": r["starts"],
                        "games": r["games"], "status": "bench" if on_bench else "not_in_squad"})
    total = sum(r["importance"] for r in regs)
    return {**base, "known": True, "absence_score": round(lost / total, 4) if total else 0.0, "missing": missing,
            "regulars": [{k: r[k] for k in ("name", "position", "starts", "games")} for r in regs], "reason": None}


def describe(absences: dict | None) -> str:
    """一句话说明，用于报告。"""
    if not absences or not absences.get("known"):
        return f"未知（{(absences or {}).get('reason') or '未采集'}）"
    if not absences["missing"]:
        return f"主力齐整（近 {absences['games_used']} 场常规首发 {len(absences['regulars'])} 人全部首发）"
    out = [m for m in absences["missing"] if m["status"] == "not_in_squad"]
    bench = [m for m in absences["missing"] if m["status"] == "bench"]
    bits = []
    if out:
        bits.append("不在大名单：" + "、".join(f"{m['name']}（{m['starts']}/{m['games']} 场首发）" for m in out))
    if bench:
        bits.append("替补席：" + "、".join(f"{m['name']}（{m['starts']}/{m['games']}）" for m in bench))
    return f"缺阵指数 {absences['absence_score']:.2f}；" + "；".join(bits)


def adjust_probabilities(p: dict, home: dict | None, away: dict | None, cfg: dict | None = None) -> tuple[dict, dict]:
    """用双方主力缺阵差修正胜平负概率，返回（修正后概率, 修正说明）。

    只有双方缺阵情况都已知时才修正；任一项概率的变动不超过 max_shift。
    """
    cfg = cfg or config()
    h = (home or {}).get("absence_score") if (home or {}).get("known") else None
    a = (away or {}).get("absence_score") if (away or {}).get("known") else None
    info = {"applied": False, "home_absence": h, "away_absence": a, "signal": 0.0, "coef": cfg["coef"],
            "max_shift": cfg["max_shift"], "source": cfg["source"]}
    if not cfg["enabled"]:
        return p, {**info, "reason": "已在配置中关闭"}
    if h is None or a is None:
        why = next(((x or {}).get("reason") for x in (home, away) if not (x or {}).get("known")), None)
        return p, {**info, "reason": why or "缺少双方首发数据"}
    signal = cfg["coef"] * (a - h)                      # 客队缺阵更多 → 利好主队
    if abs(signal) < 1e-9:
        return p, {**info, "reason": "双方主力缺阵程度相当，或修正系数为 0"}
    adj, capped = form.capped_shift(p, signal, cfg["max_shift"])
    return adj, {**info, "applied": True, "signal": round(signal, 4), "capped": capped,
                 "before": {k: round(p[k], 4) for k in p}, "delta": {k: round(adj[k] - p[k], 4) for k in p}}
