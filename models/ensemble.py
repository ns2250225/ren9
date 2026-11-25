"""集成模型：对可用模型的概率做加权线性池化，再做温度校准。"""
from __future__ import annotations

import math

import numpy as np
from scipy.optimize import minimize

ORDER = ("win", "draw", "loss")


def normalize(p: dict) -> dict:
    s = sum(max(p[k], 0.0) for k in ORDER)
    if s <= 0:
        raise ValueError("概率全为 0，无法归一化")
    return {k: max(p[k], 0.0) / s for k in ORDER}


def apply_temperature(p: dict, temperature: float) -> dict:
    """温度校准：p_i^(1/T) 再归一化。T>1 压平（降低过度自信），T<1 锐化。"""
    if not temperature or abs(temperature - 1.0) < 1e-9:
        return normalize(p)
    return normalize({k: max(p[k], 1e-12) ** (1.0 / temperature) for k in ORDER})


def combine(model_probs: dict[str, dict], weights: dict[str, float], temperature: float = 1.0) -> dict | None:
    """model_probs: {模型名: {win, draw, loss}}。缺失的模型自动剔除，其余权重重新归一化。"""
    used = {m: weights.get(m, 0.0) for m in model_probs if weights.get(m, 0.0) > 0}
    total = sum(used.values())
    if not used or total <= 0:
        return None
    pooled = {k: sum(used[m] * model_probs[m][k] for m in used) / total for k in ORDER}
    return {
        "probabilities": apply_temperature(pooled, temperature),
        "weights_used": {m: round(w / total, 4) for m, w in used.items()},
    }


def _js_divergence(p: dict, q: dict) -> float:
    def kl(a, b):
        return sum(a[k] * math.log(a[k] / b[k]) for k in ORDER if a[k] > 0)

    m = {k: 0.5 * (p[k] + q[k]) for k in ORDER}
    return 0.5 * kl(p, m) + 0.5 * kl(q, m)


def agreement(model_probs: dict[str, dict]) -> float | None:
    """模型一致性 0~1：1 − 归一化的平均两两 JS 散度；同时惩罚首选结果不一致。

    只有 1 个模型时无法衡量一致性，返回 None（未知，不当作完全一致）。
    """
    names = list(model_probs)
    if len(names) < 2:
        return None
    divs = []
    for i in range(len(names)):
        for j in range(i + 1, len(names)):
            divs.append(_js_divergence(model_probs[names[i]], model_probs[names[j]]))
    mean_js = sum(divs) / len(divs) / math.log(2)          # 归一化到 0~1
    base = max(0.0, 1.0 - min(1.0, mean_js * 8.0))          # JS=0.125 即视为完全分歧
    picks = [max(ORDER, key=lambda k: p[k]) for p in model_probs.values()]
    majority = max(picks.count(k) for k in ORDER) / len(picks)
    return round(base * (0.5 + 0.5 * majority), 4)


def entropy_norm(p: dict) -> float:
    """归一化熵 0~1（1 = 三项完全相等）。"""
    return -sum(p[k] * math.log(p[k]) for k in ORDER if p[k] > 0) / math.log(3)


def confidence(model_probs: dict[str, dict], final: dict, n_enabled: int) -> float:
    """模型置信度（不是命中概率）：模型一致性、模型覆盖度、结果明确度的组合。"""
    agr = agreement(model_probs)
    coverage = len(model_probs) / max(n_enabled, 1)
    clarity = 1.0 - entropy_norm(final)
    agr_v = 0.5 if agr is None else agr                    # 无法衡量一致性时取中性值
    return round(float(np.clip(0.45 * agr_v + 0.30 * coverage + 0.25 * min(1.0, clarity * 2.5), 0, 1)), 4)


def fit_weights(samples: list[tuple[dict[str, dict], str]], models: list[str],
                max_weight: dict[str, float] | None = None, min_weight: float = 0.0) -> dict | None:
    """在验证集上最小化 Log Loss，求单纯形上的集成权重与校准温度。

    samples: [({模型名: 概率}, 实际结果)]，只使用所有 models 都有预测的样本。
    max_weight: 个别模型的权重上限（如限制市场概率的权重，使其不能单独决定最终概率）。
    min_weight: 每个模型的权重下限（保证缺少赔率的比赛仍有可用的模型权重）。
    """
    rows = [(mp, o) for mp, o in samples if all(m in mp for m in models)]
    if len(rows) < 50 or len(models) < 2:
        return None
    P = np.array([[[mp[m][k] for k in ORDER] for m in models] for mp, _ in rows])   # N × M × 3
    Y = np.array([ORDER.index(o) for _, o in rows])
    k = len(models)
    upper = [(max_weight or {}).get(m, 1.0) for m in models]
    if min_weight * k > 1 or sum(upper) < 1:
        raise ValueError("权重上下限约束不可行")

    def loss(x):
        w, t = x[:k], x[k]
        pooled = np.clip(np.einsum("m,nmk->nk", w, P), 1e-12, 1)
        cal = pooled ** (1.0 / t)
        cal = cal / cal.sum(axis=1, keepdims=True)
        return -np.mean(np.log(cal[np.arange(len(Y)), Y]))

    bounds = [(min_weight, u) for u in upper] + [(0.5, 2.5)]
    cons = [{"type": "eq", "fun": lambda x: np.sum(x[:k]) - 1.0}]
    best = None
    for seed in range(5):
        rng = np.random.default_rng(seed)
        w0 = np.clip(rng.dirichlet(np.ones(k)), min_weight, upper)
        w0 = w0 / w0.sum()
        res = minimize(loss, np.concatenate([w0, [1.0]]), method="SLSQP", bounds=bounds, constraints=cons,
                       options={"maxiter": 300, "ftol": 1e-9})
        if np.isfinite(res.fun) and (best is None or res.fun < best.fun):
            best = res
    if best is None:
        return None
    w = np.clip(best.x[:k], min_weight, upper)
    w = w / w.sum()
    return {
        "weights": {m: round(float(v), 4) for m, v in zip(models, w)},
        "temperature": round(float(best.x[k]), 4),
        "log_loss": round(float(best.fun), 4),
        "samples": len(rows),
    }
