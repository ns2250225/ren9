#!/usr/bin/env python
"""时间切分回测：评估各模型与集成的 Brier / Log Loss / RPS / 准确率 / 校准，并学习集成权重。

训练数据始终早于被预测比赛；市场概率只用赛前赔率。学习到的权重只有在留出集上
不差于初始权重时才写入 config/model_weights.learned.yaml。

示例：
    python scripts/backtest.py
    python scripts/backtest.py --leagues E0,D1,SP1 --no-apply
"""
import argparse
from datetime import date

from _cli import main

from ren9.common import resolve_date


def body():
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--leagues", default="E0,E1,D1,I1,SP1,F1", help="football-data.co.uk 联赛代码，逗号分隔")
    p.add_argument("--date", default="today", help="只使用该日期之前的比赛")
    p.add_argument("--seasons", type=int, default=3, help="每个联赛加载的赛季数（最早一季只做预热）")
    p.add_argument("--step-days", type=int, default=7, help="滚动重拟合的步长（天）")
    p.add_argument("--no-apply", action="store_true", help="只评估，不写入学习到的权重")
    p.add_argument("--full", action="store_true")
    args = p.parse_args()
    from ren9 import backtest

    s = backtest.run([x.strip() for x in args.leagues.split(",") if x.strip()],
                     date.fromisoformat(resolve_date(args.date)), args.seasons, args.step_days, not args.no_apply)
    if args.full or s.get("status") != "ok":
        return s
    return {k: s[k] for k in ("status", "leagues", "samples", "fit_samples", "holdout_samples", "test_range",
                              "holdout_range", "learned", "elo_draw_nu", "selector_learned", "holdout", "ece",
                              "top_pick_buckets", "ren9_simulation", "applied", "leakage_controls")}


if __name__ == "__main__":
    main(body)
