"""Poisson 模型：按主客场攻防强度预测进球分布。"""
from __future__ import annotations

from collections import defaultdict
from datetime import date

import numpy as np
from scipy.stats import poisson as _poisson


def time_weight(match_date: str, ref: date, half_life_days: float) -> float:
    days = (ref - date.fromisoformat(match_date)).days
    if days < 0:
        return 0.0
    return 0.5 ** (days / half_life_days) if half_life_days > 0 else 1.0


def score_matrix(lam: float, mu: float, max_goals: int = 10, rho: float = 0.0) -> np.ndarray:
    """比分概率矩阵 M[i, j] = P(主队 i 球, 客队 j 球)；rho≠0 时应用 Dixon-Coles 低比分修正。"""
    g = np.arange(max_goals + 1)
    m = np.outer(_poisson.pmf(g, lam), _poisson.pmf(g, mu))
    if rho:
        m[0, 0] *= max(1 - lam * mu * rho, 1e-9)
        m[0, 1] *= max(1 + lam * rho, 1e-9)
        m[1, 0] *= max(1 + mu * rho, 1e-9)
        m[1, 1] *= max(1 - rho, 1e-9)
    return m / m.sum()


def outcome_probs(m: np.ndarray) -> dict:
    return {
        "win": float(np.tril(m, -1).sum()),
        "draw": float(np.trace(m)),
        "loss": float(np.triu(m, 1).sum()),
    }


def handicap_probs(m: np.ndarray, line: float) -> dict:
    """让球胜平负：主队进球 + line 与客队进球比较（line 为整数，如 -1 / +1）。"""
    n = m.shape[0]
    diff = np.subtract.outer(np.arange(n), np.arange(n)) + line
    return {
        "win": float(m[diff > 0].sum()),
        "draw": float(m[diff == 0].sum()),
        "loss": float(m[diff < 0].sum()),
    }


class PoissonModel:
    """Maher 风格的攻防强度模型（时间衰减 + 向联赛均值收缩 + 可选 xG 混合）。"""

    def __init__(self, half_life_days: float = 240, shrink_matches: float = 4, xg_blend: float = 0.4,
                 max_goals: int = 10) -> None:
        self.half_life = half_life_days
        self.shrink = shrink_matches
        self.xg_blend = xg_blend
        self.max_goals = max_goals
        self.fitted = False

    def _goals(self, m: dict) -> tuple[float, float]:
        hg, ag = float(m["hg"]), float(m["ag"])
        if self.xg_blend > 0 and m.get("hxg") is not None and m.get("axg") is not None:
            b = self.xg_blend
            return (1 - b) * hg + b * float(m["hxg"]), (1 - b) * ag + b * float(m["axg"])
        return hg, ag

    def fit(self, matches: list[dict], ref: date) -> "PoissonModel":
        acc = defaultdict(lambda: defaultdict(float))
        tw = th = ta = 0.0
        for m in matches:
            w = time_weight(m["date"], ref, self.half_life)
            if w <= 0:
                continue
            hg, ag = self._goals(m)
            tw += w
            th += w * hg
            ta += w * ag
            h, a = acc[m["home"]], acc[m["away"]]
            h["hw"] += w; h["hgf"] += w * hg; h["hga"] += w * ag; h["n"] += 1
            a["aw"] += w; a["agf"] += w * ag; a["aga"] += w * hg; a["n"] += 1
        if tw <= 0 or th <= 0 or ta <= 0:
            return self
        self.league_home = th / tw
        self.league_away = ta / tw
        k = self.shrink
        self.teams: dict[str, dict] = {}
        for t, s in acc.items():
            self.teams[t] = {
                # 强度 = 收缩后的场均 / 联赛均值；样本越少越接近 1
                "att_home": ((s["hgf"] + k * self.league_home) / (s["hw"] + k)) / self.league_home,
                "def_home": ((s["hga"] + k * self.league_away) / (s["hw"] + k)) / self.league_away,
                "att_away": ((s["agf"] + k * self.league_away) / (s["aw"] + k)) / self.league_away,
                "def_away": ((s["aga"] + k * self.league_home) / (s["aw"] + k)) / self.league_home,
                "n": int(s["n"]),
            }
        self.fitted = True
        return self

    def matches_of(self, team: str) -> int:
        return self.teams.get(team, {}).get("n", 0) if self.fitted else 0

    def predict(self, home: str, away: str) -> dict | None:
        if not self.fitted or home not in self.teams or away not in self.teams:
            return None
        h, a = self.teams[home], self.teams[away]
        lam = float(np.clip(self.league_home * h["att_home"] * a["def_away"], 0.15, 5.0))
        mu = float(np.clip(self.league_away * a["att_away"] * h["def_home"], 0.15, 5.0))
        m = score_matrix(lam, mu, self.max_goals)
        return {
            "home_expected_goals": round(lam, 3),
            "away_expected_goals": round(mu, 3),
            "probabilities": outcome_probs(m),
            "matrix": m,
        }


def calibrate_matrix(m: np.ndarray, target: dict) -> np.ndarray:
    """把比分矩阵的胜/平/负三块分别缩放，使其边际概率等于 target（集成后的最终概率）。

    这样由矩阵派生的让球胜平负概率与最终胜平负概率保持一致，比分的相对分布仍来自进球模型。
    """
    cur = outcome_probs(m)
    out = m.copy()
    n = m.shape[0]
    diff = np.subtract.outer(np.arange(n), np.arange(n))
    for key, mask in (("win", diff > 0), ("draw", diff == 0), ("loss", diff < 0)):
        if cur[key] > 0:
            out[mask] *= target[key] / cur[key]
    return out / out.sum()
