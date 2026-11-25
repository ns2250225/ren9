"""Dixon-Coles (1997) 模型：带时间衰减的极大似然估计 + 低比分（0:0 / 1:0 / 0:1 / 1:1）修正。

log λ = home + att[主] + dfn[客]      （dfn 为"防守弱度"，越大失球越多）
log μ =        att[客] + dfn[主]
似然 = Π τ(x, y; λ, μ, ρ) · Pois(x; λ) · Pois(y; μ)，按 0.5^(Δt/半衰期) 加权。
攻防参数带 L2 正则（ridge），样本少时自动向联赛平均收缩。
"""
from __future__ import annotations

from datetime import date

import numpy as np
from scipy.optimize import minimize

from models.poisson import outcome_probs, score_matrix, time_weight


class DixonColesModel:
    def __init__(self, half_life_days: float = 240, ridge: float = 0.6, max_goals: int = 10) -> None:
        self.half_life = half_life_days
        self.ridge = ridge
        self.max_goals = max_goals
        self.fitted = False

    def fit(self, matches: list[dict], ref: date) -> "DixonColesModel":
        rows = [(m, time_weight(m["date"], ref, self.half_life)) for m in matches]
        rows = [(m, w) for m, w in rows if w > 1e-4]
        teams = sorted({m["home"] for m, _ in rows} | {m["away"] for m, _ in rows})
        if len(rows) < 20 or len(teams) < 4:
            return self
        idx = {t: i for i, t in enumerate(teams)}
        n = len(teams)
        hi = np.array([idx[m["home"]] for m, _ in rows])
        ai = np.array([idx[m["away"]] for m, _ in rows])
        x = np.array([int(m["hg"]) for m, _ in rows], dtype=float)
        y = np.array([int(m["ag"]) for m, _ in rows], dtype=float)
        w = np.array([wt for _, wt in rows])
        w = w / w.mean()
        m00, m01, m10, m11 = (x == 0) & (y == 0), (x == 0) & (y == 1), (x == 1) & (y == 0), (x == 1) & (y == 1)
        # 样本稀疏（如洲际赛事每队只有几场）时按比例加大正则，防止攻防参数被个别大比分带偏
        per_team = 2.0 * len(rows) / n
        ridge = self.ridge * max(1.0, 20.0 / per_team)
        self.ridge_effective = ridge

        def unpack(p):
            return p[:n], p[n:2 * n], p[2 * n], p[2 * n + 1], p[2 * n + 2]

        def nll(p):
            att, dfn, home, base, rho = unpack(p)
            ll = base + home + att[hi] + dfn[ai]
            lm = base + att[ai] + dfn[hi]
            lam, mu = np.exp(ll), np.exp(lm)
            tau = np.ones_like(lam)
            tau[m00] = 1 - lam[m00] * mu[m00] * rho
            tau[m01] = 1 + lam[m01] * rho
            tau[m10] = 1 + mu[m10] * rho
            tau[m11] = 1 - rho
            tau = np.maximum(tau, 1e-9)
            f = -np.sum(w * (np.log(tau) + x * ll - lam + y * lm - mu))
            f += ridge * (np.sum(att ** 2) + np.sum(dfn ** 2))

            # 解析梯度
            dl = np.zeros_like(lam)     # ∂logτ/∂λ · λ
            dm = np.zeros_like(lam)
            dr = np.zeros_like(lam)
            dl[m00] = -lam[m00] * mu[m00] * rho / tau[m00]
            dm[m00] = dl[m00]
            dr[m00] = -lam[m00] * mu[m00] / tau[m00]
            dl[m01] = lam[m01] * rho / tau[m01]
            dr[m01] = lam[m01] / tau[m01]
            dm[m10] = mu[m10] * rho / tau[m10]
            dr[m10] = mu[m10] / tau[m10]
            dr[m11] = -1.0 / tau[m11]
            gl = w * (x - lam + dl)      # 对 log λ 的梯度
            gm = w * (y - mu + dm)
            g_att = np.zeros(n)
            g_dfn = np.zeros(n)
            np.add.at(g_att, hi, gl)
            np.add.at(g_att, ai, gm)
            np.add.at(g_dfn, ai, gl)
            np.add.at(g_dfn, hi, gm)
            grad = np.concatenate([
                -g_att + 2 * ridge * att,
                -g_dfn + 2 * ridge * dfn,
                [-gl.sum(), -(gl.sum() + gm.sum()), -np.sum(w * dr)],
            ])
            return f, grad

        avg = max((x.mean() + y.mean()) / 2, 0.2)
        p0 = np.concatenate([np.zeros(2 * n), [0.25, np.log(avg) - 0.125, -0.05]])
        bounds = [(-3, 3)] * (2 * n) + [(-1, 1.5), (-3, 2), (-0.2, 0.2)]
        res = minimize(nll, p0, jac=True, method="L-BFGS-B", bounds=bounds, options={"maxiter": 500})
        att, dfn, home, base, rho = unpack(res.x)
        self.teams = teams
        self.idx = idx
        self.att, self.dfn = att, dfn
        self.home, self.base, self.rho = float(home), float(base), float(rho)
        self.counts = {t: int(np.sum(hi == i) + np.sum(ai == i)) for t, i in idx.items()}
        self.converged = bool(res.success)
        self.fitted = True
        return self

    def matches_of(self, team: str) -> int:
        return self.counts.get(team, 0) if self.fitted else 0

    def predict(self, home: str, away: str) -> dict | None:
        if not self.fitted or home not in self.idx or away not in self.idx:
            return None
        h, a = self.idx[home], self.idx[away]
        lam = float(np.clip(np.exp(self.base + self.home + self.att[h] + self.dfn[a]), 0.15, 5.0))
        mu = float(np.clip(np.exp(self.base + self.att[a] + self.dfn[h]), 0.15, 5.0))
        m = score_matrix(lam, mu, self.max_goals, rho=self.rho)
        return {
            "home_expected_goals": round(lam, 3),
            "away_expected_goals": round(mu, 3),
            "rho": round(self.rho, 4),
            "home_advantage": round(self.home, 4),
            "attack": {"home": round(float(self.att[h]), 3), "away": round(float(self.att[a]), 3)},
            "defence_weakness": {"home": round(float(self.dfn[h]), 3), "away": round(float(self.dfn[a]), 3)},
            "low_scores": {
                "0:0": round(float(m[0, 0]), 4), "1:0": round(float(m[1, 0]), 4),
                "0:1": round(float(m[0, 1]), 4), "1:1": round(float(m[1, 1]), 4),
            },
            "probabilities": outcome_probs(m),
            "matrix": m,
        }
