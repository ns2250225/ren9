#!/usr/bin/env python
"""Dixon-Coles 工具：用某联赛历史赛果拟合模型，查看参数或预测一场比赛（含比分概率）。

示例：
    python scripts/dixon_coles.py --league E0
    python scripts/dixon_coles.py --league E0 --home Arsenal --away Chelsea
"""
import argparse
from datetime import date

from _cli import main

from models.dixon_coles import DixonColesModel
from models.poisson import PoissonModel
from ren9.common import model_weights, resolve_date


def body():
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--league", required=True, help="football-data.co.uk 联赛代码，如 E0 / D1 / SP1")
    p.add_argument("--home")
    p.add_argument("--away")
    p.add_argument("--date", default="today", help="只使用该日期之前的比赛")
    args = p.parse_args()
    from ren9.backtest import load_league
    from ren9.teams import best_match

    ref = date.fromisoformat(resolve_date(args.date))
    rows = load_league(args.league, ref, 2)
    if not rows:
        raise ValueError(f"没有取到联赛 {args.league} 的历史数据")
    mw = model_weights()
    dc = mw.get("dixon_coles", {})
    model = DixonColesModel(dc.get("half_life_days", 240), dc.get("ridge", 0.6), dc.get("max_goals", 10)).fit(rows, ref)
    if not model.fitted:
        raise ValueError("样本不足，模型未能拟合")
    out = {"league": args.league, "matches_used": len(rows), "data_until": rows[-1]["date"],
           "home_advantage": round(model.home, 4), "rho": round(model.rho, 4), "converged": model.converged,
           "half_life_days": model.half_life}
    if args.home and args.away:
        h, a = best_match([args.home], model.teams), best_match([args.away], model.teams)
        if not h or not a:
            raise ValueError(f"数据集中找不到球队；可选 {model.teams}")
        r = model.predict(h, a)
        m = r.pop("matrix")
        r["probabilities"] = {k: round(v, 4) for k, v in r["probabilities"].items()}
        scores = sorted(((float(m[i, j]), f"{i}:{j}") for i in range(6) for j in range(6)), reverse=True)[:8]
        pc = mw.get("poisson", {})
        pm = PoissonModel(pc.get("half_life_days", 240), pc.get("shrink_matches", 4), pc.get("xg_blend", 0.4)).fit(rows, ref)
        pp = pm.predict(h, a)
        return {**out, "home_team": h, "away_team": a, **r,
                "top_scores": [{"score": s, "probability": round(p, 4)} for p, s in scores],
                "poisson_reference": {"home_expected_goals": pp["home_expected_goals"],
                                      "away_expected_goals": pp["away_expected_goals"],
                                      "probabilities": {k: round(v, 4) for k, v in pp["probabilities"].items()}}}
    out["teams"] = sorted(
        ({"team": t, "attack": round(float(model.att[i]), 3), "defence_weakness": round(float(model.dfn[i]), 3),
          "matches": model.counts[t]} for t, i in model.idx.items()),
        key=lambda x: x["defence_weakness"] - x["attack"])
    return out


if __name__ == "__main__":
    main(body)
