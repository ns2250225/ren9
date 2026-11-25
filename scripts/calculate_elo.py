#!/usr/bin/env python
"""Elo 工具：由评级计算胜平负概率，或用某联赛历史赛果自算 Elo 排名。

示例：
    python scripts/calculate_elo.py --home-elo 1750 --away-elo 1620
    python scripts/calculate_elo.py --league E0 --top 20
    python scripts/calculate_elo.py --league E0 --home Arsenal --away Chelsea
"""
import argparse
from datetime import date

from _cli import main

from models import elo
from ren9.common import model_weights, resolve_date


def body():
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--home-elo", type=float)
    p.add_argument("--away-elo", type=float)
    p.add_argument("--league", help="football-data.co.uk 联赛代码，如 E0 / D1 / SP1")
    p.add_argument("--home", help="主队（数据集中的英文名）")
    p.add_argument("--away", help="客队（数据集中的英文名）")
    p.add_argument("--date", default="today")
    p.add_argument("--top", type=int, default=30)
    args = p.parse_args()
    ec = model_weights().get("elo", {})
    hfa, nu = ec.get("home_advantage", 65), ec.get("draw_nu", 0.78)
    if args.home_elo is not None and args.away_elo is not None:
        r = elo.predict(args.home_elo, args.away_elo, hfa, nu)
        r["probabilities"] = {k: round(v, 4) for k, v in r["probabilities"].items()}
        return {**r, "home_advantage": hfa, "draw_nu": nu}
    if not args.league:
        raise ValueError("请提供 --home-elo/--away-elo，或 --league")
    from ren9.backtest import load_league
    from ren9.teams import best_match

    ref = date.fromisoformat(resolve_date(args.date))
    rows = load_league(args.league, ref, 3)
    if not rows:
        raise ValueError(f"没有取到联赛 {args.league} 的历史数据")
    er = elo.EloRatings(ec.get("k_factor", 20), hfa, ec.get("initial_rating", 1500)).fit(rows)
    out = {"league": args.league, "matches_used": len(rows), "data_until": rows[-1]["date"],
           "source": "football-data.co.uk 历史赛果自算（仅同联赛内可比）"}
    if args.home and args.away:
        teams = list(er.ratings)
        h, a = best_match([args.home], teams), best_match([args.away], teams)
        if not h or not a:
            raise ValueError(f"数据集中找不到球队：{args.home if not h else args.away}；可选 {sorted(teams)}")
        r = elo.predict(er.get(h), er.get(a), hfa, nu)
        r["probabilities"] = {k: round(v, 4) for k, v in r["probabilities"].items()}
        return {**out, "home_team": h, "away_team": a, **r}
    table = sorted(er.ratings.items(), key=lambda x: -x[1])[: args.top]
    out["ratings"] = [{"rank": i + 1, "team": t, "elo": round(v, 1), "games": er.games[t]}
                      for i, (t, v) in enumerate(table)]
    return out


if __name__ == "__main__":
    main(body)
