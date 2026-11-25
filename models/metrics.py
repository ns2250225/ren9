"""概率预测评估指标：Accuracy、Brier、Log Loss、RPS、校准。"""
from __future__ import annotations

import math

ORDER = ("win", "draw", "loss")


def brier(p: dict, outcome: str) -> float:
    """多分类 Brier Score（三项平方误差之和，0 最好，随机 1/3 时为 0.667）。"""
    return sum((p[k] - (1.0 if k == outcome else 0.0)) ** 2 for k in ORDER)


def log_loss(p: dict, outcome: str) -> float:
    return -math.log(max(p[outcome], 1e-12))


def rps(p: dict, outcome: str) -> float:
    """Ranked Probability Score（考虑 胜-平-负 的顺序）。"""
    cum_p = cum_o = 0.0
    total = 0.0
    for k in ORDER[:-1]:
        cum_p += p[k]
        cum_o += 1.0 if k == outcome else 0.0
        total += (cum_p - cum_o) ** 2
    return total / (len(ORDER) - 1)


def top_pick(p: dict) -> str:
    return max(ORDER, key=lambda k: p[k])


def summarize(records: list[tuple[dict, str]]) -> dict | None:
    """records: [(概率, 实际结果)]。"""
    n = len(records)
    if n == 0:
        return None
    return {
        "n": n,
        "accuracy": round(sum(1 for p, o in records if top_pick(p) == o) / n, 4),
        "brier": round(sum(brier(p, o) for p, o in records) / n, 4),
        "log_loss": round(sum(log_loss(p, o) for p, o in records) / n, 4),
        "rps": round(sum(rps(p, o) for p, o in records) / n, 4),
    }


def calibration_bins(records: list[tuple[dict, str]], bins: int = 10) -> list[dict]:
    """校准曲线：把所有（预测概率, 是否发生）按概率分箱，比较平均预测概率与实际发生频率。"""
    acc = [[0, 0.0, 0] for _ in range(bins)]   # count, sum_p, hits
    for p, o in records:
        for k in ORDER:
            b = min(int(p[k] * bins), bins - 1)
            acc[b][0] += 1
            acc[b][1] += p[k]
            acc[b][2] += 1 if k == o else 0
    out = []
    for i, (c, sp, h) in enumerate(acc):
        if c:
            out.append(
                {
                    "bin": f"{i / bins:.1f}-{(i + 1) / bins:.1f}",
                    "count": c,
                    "mean_predicted": round(sp / c, 4),
                    "observed": round(h / c, 4),
                }
            )
    return out


def expected_calibration_error(records: list[tuple[dict, str]], bins: int = 10) -> float | None:
    rows = calibration_bins(records, bins)
    total = sum(r["count"] for r in rows)
    if not total:
        return None
    return round(sum(r["count"] * abs(r["mean_predicted"] - r["observed"]) for r in rows) / total, 4)


def top_pick_buckets(records: list[tuple[dict, str]]) -> list[dict]:
    """不同概率区间内，首选结果的实际命中率。"""
    edges = [(0.0, 0.40), (0.40, 0.50), (0.50, 0.60), (0.60, 0.70), (0.70, 1.01)]
    out = []
    for lo, hi in edges:
        sel = [(p, o) for p, o in records if lo <= max(p.values()) < hi]
        if sel:
            out.append(
                {
                    "range": f"{lo:.0%}-{min(hi, 1.0):.0%}",
                    "count": len(sel),
                    "mean_predicted": round(sum(max(p.values()) for p, _ in sel) / len(sel), 4),
                    "hit_rate": round(sum(1 for p, o in sel if top_pick(p) == o) / len(sel), 4),
                }
            )
    return out
