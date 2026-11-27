"""多票组合优化：同样的注数，用几张互不重叠的复式票代替一张复式票，提高 9 场全中的概率。

一张复式票覆盖的是"矩形"区域（每场的选项相乘），其中包含不少概率极低的组合（例如所有双选场
同时打出次选）。把同样的注数拆成几张票——主票覆盖各场首选附近的组合，副票只在最不确定的
一两场上换成次选——可以把这些低概率组合换成概率更高的组合。

做法：把 9 场按不确定程度排序（首选与次选最接近的排最前），做"分叉树"动态规划：
    F(i, b, t) = 只用第 i 场及之后的比赛、不超过 b 注、不超过 t 张票时能覆盖的最大概率
每场可以不分叉（所有票在这一场选同一组结果，注数相乘），也可以分叉（不同的票在这一场选
互不相交的结果，注数与票数在各分支之间分配）。分叉保证各票覆盖的组合互不重叠，
所以总覆盖概率就是各票概率之和。t = 1 时退化为普通复式的最优解。
"""
from __future__ import annotations

import numpy as np

from ren9.common import OUTCOMES

# 分叉方式：每个分支是一组"按概率排名"的结果下标（0 = 首选，1 = 次选，2 = 第三选）
_SPLITS = (((0,), (1,)), ((0, 1), (2,)), ((0,), (1, 2)), ((0,), (1,), (2,)))
_NO_SPLIT = ((0,), (0, 1), (0, 1, 2))
MAX_BETS = 2048           # 动态规划的注数上限（更大的预算退回单张复式）


def _conv(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """(max, +) 卷积：out[n, t] = max_{n1+n2=n, t1+t2=t} a[n1, t1] + b[n2, t2]。

    表格对注数、票数都是单调不减的阶梯函数，只需在 a 的值真正增大的位置（阶梯的起点）展开。
    """
    out = np.zeros_like(a)
    nb, nt = a.shape
    grew = np.zeros(a.shape, dtype=bool)
    grew[1:, 1:] = (a[1:, 1:] > a[:-1, 1:] + 1e-15) & (a[1:, 1:] > a[1:, :-1] + 1e-15)
    for n1, t1 in zip(*np.nonzero(grew)):
        np.maximum(out[n1:, t1:], a[n1, t1] + b[: nb - n1, : nt - t1], out=out[n1:, t1:])
    return out


def _branch(table: np.ndarray, prob: float, size: int) -> np.ndarray:
    """某个分支（在本场选 size 个结果、概率 prob）在各 (注数, 票数) 下的覆盖概率。"""
    idx = np.arange(table.shape[0]) // size
    return prob * table[idx, :]


def optimize(probs: list[dict], max_bets: int, max_tickets: int, values: list[dict] | None = None,
             cost_aversion: float = 0.0) -> dict:
    """probs: 选中的各场概率 {win, draw, loss}（顺序即场次顺序）。

    values: 可选，各场每个结果的"价值"（如 概率 × 市场概率^-α 的冷门偏好）；给出时按价值优化，
    返回的概率仍按 probs 计算。cost_aversion: 成本厌恶 λ，实际使用的注数 b 取
    log(覆盖) − λ·log(b) 最大者（λ = 0 时用满预算）。
    返回 {tickets: [{picks: [[结果...] 每场], bets, probability}], bets, hit_probability}。
    各票覆盖的组合互不重叠；tickets 按覆盖概率从高到低排列。
    """
    n = len(probs)
    if n == 0 or max_bets < 1 or max_tickets < 1:
        raise ValueError("至少需要 1 场比赛、1 注、1 张票")
    max_bets = min(int(max_bets), MAX_BETS)
    max_tickets = min(int(max_tickets), max_bets)
    vals = values or probs
    ranked = [sorted(OUTCOMES, key=lambda o: (-v[o], OUTCOMES.index(o))) for v in vals]
    q = [[v[o] for o in r] for v, r in zip(vals, ranked)]            # 优化用的价值（按排名）
    real = [[p[o] for o in r] for p, r in zip(probs, ranked)]        # 真实概率（按同一排名）
    # 最不确定的比赛排最前：分叉发生在这些比赛上
    order = sorted(range(n), key=lambda i: (q[i][0] - q[i][1], i))

    shape = (max_bets + 1, max_tickets + 1)
    tables = [None] * (n + 1)
    last = np.zeros(shape)
    last[1:, 1:] = 1.0
    tables[n] = last
    for pos in range(n - 1, -1, -1):
        qi, nxt = q[order[pos]], tables[pos + 1]
        best = np.zeros(shape)
        for grp in _NO_SPLIT:
            np.maximum(best, _branch(nxt, sum(qi[k] for k in grp), len(grp)), out=best)
        if max_tickets > 1:
            for split in _SPLITS:
                acc = _branch(nxt, sum(qi[k] for k in split[0]), len(split[0]))
                for grp in split[1:]:
                    acc = _conv(acc, _branch(nxt, sum(qi[k] for k in grp), len(grp)))
                np.maximum(best, acc, out=best)
        tables[pos] = best

    def build(pos: int, b: int, t: int) -> list[list[tuple]]:
        """回溯：返回若干张票，每张票是 pos 及之后各场的"排名下标组"列表。"""
        if pos == n:
            return [[]]
        qi, target = q[order[pos]], tables[pos][b, t]
        for grp in _NO_SPLIT:
            if abs(sum(qi[k] for k in grp) * tables[pos + 1][b // len(grp), t] - target) < 1e-12:
                return [[grp] + rest for rest in build(pos + 1, b // len(grp), t)]
        for split in _SPLITS:
            found = _find_split(split, qi, tables[pos + 1], b, t, target)
            if found:
                out = []
                for grp, (bb, tt) in zip(split, found):
                    if bb >= len(grp) and tt >= 1:
                        out += [[grp] + rest for rest in build(pos + 1, bb // len(grp), tt)]
                return out
        raise RuntimeError("多票组合回溯失败")          # 理论上不会发生

    b, t = max_bets, max_tickets
    if cost_aversion > 0:
        col = tables[0][1:, t]
        b = 1 + int(np.argmax(np.log(np.maximum(col, 1e-300)) - cost_aversion * np.log(np.arange(1, max_bets + 1))))
    tickets = []
    for tk in build(0, b, t):
        picks = [None] * n
        prob, bets = 1.0, 1
        for pos, grp in enumerate(tk):
            i = order[pos]
            chosen = {ranked[i][k] for k in grp}
            picks[i] = [o for o in OUTCOMES if o in chosen]
            prob *= sum(real[i][k] for k in grp)
            bets *= len(grp)
        tickets.append({"picks": picks, "bets": bets, "probability": prob})
    tickets.sort(key=lambda x: -x["probability"])
    return {"tickets": tickets, "bets": sum(x["bets"] for x in tickets),
            "hit_probability": float(sum(x["probability"] for x in tickets))}


def _find_split(split, qi, nxt, b: int, t: int, target: float):
    """找出一种分叉方式下各分支的 (注数, 票数) 分配，使总覆盖概率等于 target。"""
    val = lambda grp, bb, tt: sum(qi[k] for k in grp) * nxt[bb // len(grp), tt] if bb >= 1 and tt >= 1 else 0.0  # noqa: E731
    if len(split) == 2:
        for b1 in range(1, b):
            for t1 in range(1, t):
                if abs(val(split[0], b1, t1) + val(split[1], b - b1, t - t1) - target) < 1e-12:
                    return [(b1, t1), (b - b1, t - t1)]
        return None
    for b1 in range(1, b - 1):
        for t1 in range(1, t - 1):
            v1 = val(split[0], b1, t1)
            for b2 in range(1, b - b1):
                for t2 in range(1, t - t1):
                    if abs(v1 + val(split[1], b2, t2) + val(split[2], b - b1 - b2, t - t1 - t2) - target) < 1e-12:
                        return [(b1, t1), (b2, t2), (b - b1 - b2, t - t1 - t2)]
    return None
