"""Elo 模型：评级更新 + 由评级差计算胜平负概率（Davidson 平局模型）。"""
from __future__ import annotations

import math
from collections import defaultdict


def davidson_probs(home_elo: float, away_elo: float, home_advantage: float = 65.0, nu: float = 0.78) -> dict:
    """Davidson (1970) 带平局的配对比较模型。

    强度 π = 10^(Elo/400)；P(平) ∝ ν·sqrt(π_h·π_a)。ν 越大平局概率越高。
    """
    d = (home_elo + home_advantage - away_elo) / 400.0
    ph = 10 ** (d / 2.0)       # 以几何平均归一化，数值稳定
    pa = 10 ** (-d / 2.0)
    den = ph + pa + nu
    return {"win": ph / den, "draw": nu / den, "loss": pa / den}


def expected_score(home_elo: float, away_elo: float, home_advantage: float = 65.0) -> float:
    return 1.0 / (1.0 + 10 ** (-(home_elo + home_advantage - away_elo) / 400.0))


def _margin_mult(goal_diff: int) -> float:
    g = abs(goal_diff)
    if g <= 1:
        return 1.0
    if g == 2:
        return 1.5
    return (11.0 + g) / 8.0


class EloRatings:
    """按时间顺序用历史比赛自算 Elo（ClubElo 不可用或同联赛内比较时使用）。"""

    def __init__(self, k: float = 20.0, home_advantage: float = 65.0, initial: float = 1500.0) -> None:
        self.k = k
        self.hfa = home_advantage
        self.initial = initial
        self.ratings: dict[str, float] = defaultdict(lambda: self.initial)
        self.games: dict[str, int] = defaultdict(int)

    def get(self, team: str) -> float:
        return self.ratings[team]

    def update(self, home: str, away: str, hg: int, ag: int) -> None:
        rh, ra = self.ratings[home], self.ratings[away]
        exp = expected_score(rh, ra, self.hfa)
        act = 1.0 if hg > ag else 0.5 if hg == ag else 0.0
        delta = self.k * _margin_mult(hg - ag) * (act - exp)
        self.ratings[home] = rh + delta
        self.ratings[away] = ra - delta
        self.games[home] += 1
        self.games[away] += 1

    def fit(self, matches: list[dict]) -> "EloRatings":
        for m in sorted(matches, key=lambda x: x["date"]):
            self.update(m["home"], m["away"], int(m["hg"]), int(m["ag"]))
        return self


def predict(home_elo: float, away_elo: float, home_advantage: float = 65.0, nu: float = 0.78) -> dict:
    p = davidson_probs(home_elo, away_elo, home_advantage, nu)
    return {
        "home_elo": round(home_elo, 1),
        "away_elo": round(away_elo, 1),
        "elo_difference": round(home_elo - away_elo, 1),
        "probabilities": p,
    }


def fit_draw_nu(samples: list[tuple[float, float, str]], home_advantage: float) -> float:
    """用 (home_elo, away_elo, outcome) 样本按最大似然网格搜索 ν。"""
    best, best_ll = 0.78, -math.inf
    for i in range(40, 131, 2):
        nu = i / 100.0
        ll = 0.0
        for rh, ra, out in samples:
            ll += math.log(max(davidson_probs(rh, ra, home_advantage, nu)[out], 1e-12))
        if ll > best_ll:
            best, best_ll = nu, ll
    return best
