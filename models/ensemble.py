"""集成模型：把各模型的概率融合成最终概率。

两种方式：
- linear：加权线性池化 + 温度校准（初始配置、以及样本很少的复盘调权使用）；
- log_linear：对数线性池化（回测学习使用）。指数与偏置由回测在历史数据上拟合，
  有市场概率和没有市场概率的比赛各拟合一套参数。
"""
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


def log_pool(model_probs: dict[str, dict], weights: dict[str, float], bias: dict | None = None,
             reliability: dict[str, float] | None = None) -> dict | None:
    """对数线性池化：p(k) ∝ exp(b_k) · Π_m p_m(k)^{w_m}。

    weights 是指数而不是占比：只有一个模型、指数为 1、偏置为 0 时原样返回该模型的概率；
    指数 > 1 锐化、< 1 压平，所以它同时完成了"融合"和"校准"。bias 修正系统性偏差（如平局偏少）。
    缺失的模型被剔除，其余指数等比例放大，保持总指数不变（整体锐度不变）；
    reliability（0~1）直接乘在对应模型的指数上——样本少的模型贡献变小，结果更接近均匀分布。
    """
    base = {m: w for m, w in weights.items() if w > 0}
    used = {m: w for m, w in base.items() if m in model_probs}
    if not used:
        return None
    scale = sum(base.values()) / sum(used.values())
    exps = {m: w * scale * (reliability or {}).get(m, 1.0) for m, w in used.items()}
    z = {k: (bias or {}).get(k, 0.0) + sum(e * math.log(max(model_probs[m][k], 1e-9)) for m, e in exps.items())
         for k in ORDER}
    top = max(z.values())
    total = sum(exps.values())
    return {
        "probabilities": normalize({k: math.exp(z[k] - top) for k in ORDER}),
        "weights_used": {m: round(e / total, 4) for m, e in exps.items()} if total > 0 else {},
        "exponents": {m: round(e, 4) for m, e in exps.items()},
    }


def pool(model_probs: dict[str, dict], cfg: dict, reliability: dict[str, float] | None = None) -> dict | None:
    """按集成配置融合各模型概率。

    cfg: {method: linear | log_linear, weights, temperature?, bias?, no_market?: {weights, bias}}
    log_linear 且本场没有市场概率时改用 no_market 那一套参数（只用历史类模型单独拟合出来的）。
    """
    if cfg.get("method") == "log_linear":
        part = cfg
        if "market" not in model_probs and cfg.get("no_market"):
            part = cfg["no_market"]
        return log_pool(model_probs, part.get("weights") or {}, part.get("bias"), reliability)
    weights = {m: w * (reliability or {}).get(m, 1.0) for m, w in (cfg.get("weights") or {}).items()}
    return combine(model_probs, weights, cfg.get("temperature", 1.0))


def fit_log_pool(samples: list[tuple[dict[str, dict], str]], models: list[str], max_exponent: float = 3.0) -> dict | None:
    """最小化 Log Loss，求对数线性池化的指数（非负）与胜/平偏置（负的偏置固定为 0）。

    samples: [({模型名: 概率}, 实际结果)]，只使用所有 models 都有预测的样本。
    """
    rows = [(mp, o) for mp, o in samples if all(m in mp for m in models)]
    if len(rows) < 200 or not models:
        return None
    L = np.log(np.clip(np.array([[[mp[m][k] for k in ORDER] for m in models] for mp, _ in rows]), 1e-9, 1))
    Y = np.array([ORDER.index(o) for _, o in rows])
    k = len(models)

    def loss(x):
        z = np.einsum("m,nmk->nk", x[:k], L) + np.array([x[k], x[k + 1], 0.0])
        z = z - z.max(axis=1, keepdims=True)
        lse = np.log(np.exp(z).sum(axis=1))
        return float(-np.mean(z[np.arange(len(Y)), Y] - lse))

    best = None
    for start in (np.full(k, 1.0 / k), np.eye(k)[-1], np.full(k, 0.5)):
        res = minimize(loss, np.concatenate([start, [0.0, 0.0]]), method="L-BFGS-B",
                       bounds=[(0.0, max_exponent)] * k + [(-1.0, 1.0)] * 2)
        if np.isfinite(res.fun) and (best is None or res.fun < best.fun):
            best = res
    if best is None:
        return None
    w = np.where(best.x[:k] < 1e-4, 0.0, best.x[:k])
    return {
        "method": "log_linear",
        "weights": {m: round(float(v), 4) for m, v in zip(models, w)},
        "bias": {"win": round(float(best.x[k]), 4), "draw": round(float(best.x[k + 1]), 4), "loss": 0.0},
        "log_loss": round(float(best.fun), 4),
        "samples": len(rows),
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
