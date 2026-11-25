#!/usr/bin/env python
"""赛后复盘：获取官方开奖结果，评估预测（Accuracy / Brier / Log Loss / RPS / 校准）、方案命中与真实 ROI。

示例：
    python scripts/review_results.py --date yesterday        # 复盘昨天分析的期次
    python scripts/review_results.py --period 26168
    python scripts/review_results.py                          # 复盘所有尚未复盘且已开奖的期次
    python scripts/review_results.py --summary                # 历史累计表现
    python scripts/review_results.py --optimize-weights       # 用已复盘数据重新拟合集成权重
"""
import argparse

from _cli import main

from ren9 import report, review, store
from ren9.common import data_dir, resolve_date


def body():
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--date", help="复盘该日期所分析的期次（today / yesterday / YYYY-MM-DD）")
    p.add_argument("--period", help="直接指定期号")
    p.add_argument("--game-type", default="ren9", choices=["ren9", "jczq"])
    p.add_argument("--refresh", action="store_true")
    p.add_argument("--allow-post-deadline", action="store_true", help="允许复盘销售截止后才生成的预测（仅供查看）")
    p.add_argument("--summary", action="store_true", help="只输出历史累计表现")
    p.add_argument("--optimize-weights", action="store_true", help="用已复盘记录重新拟合集成权重")
    p.add_argument("--print", dest="print_md", action="store_true", help="输出 Markdown 复盘报告")
    args = p.parse_args()
    gt = args.game_type
    if args.optimize_weights:
        return review.optimize_weights(gt)
    if args.summary:
        return review.aggregate(gt)

    if gt == "jczq":
        day = resolve_date(args.date or "yesterday")
        r = review.review_jczq(day, args.refresh)
        r.pop("records", None)
        return r

    if args.period:
        periods = [args.period]
    elif args.date:
        day = resolve_date(args.date)
        periods = store.periods_with_predictions(gt, day)
        if not periods:
            return {"status": "no_prediction", "date": day,
                    "message": f"{day} 没有保存过预测。可用 --period 指定期号，或不带参数复盘全部待复盘期次。",
                    "periods_with_predictions": store.periods_with_predictions(gt)}
    else:
        done = {r["period"] for r in store.all_reviews(gt) if r.get("status") == "ok"}
        periods = [x for x in store.periods_with_predictions(gt) if x not in done]
        if not periods:
            return {"status": "nothing_to_review", "message": "没有待复盘的期次", "summary": review.aggregate(gt)}

    out, mds = [], []
    for period in periods:
        r = review.review_ren9(period, args.refresh, args.allow_post_deadline)
        if r.get("status") == "ok":
            md = report.build_review(r, review.aggregate(gt))
            path = data_dir() / "results" / f"ren9_{period}_review.md"
            path.write_text(md, encoding="utf-8")
            mds.append(md)
            out.append({"period": period, "status": "ok", "metrics": r["metrics"], "draw_result": r["draw_result"],
                        "plans": [{k: pl.get(k) for k in ("strategy", "hits", "total", "all_nine_hit", "cost",
                                                          "prize_per_bet", "payout_after_tax", "roi")}
                                  for pl in r["plans"]],
                        "warnings": r["warnings"], "markdown_path": str(path)})
        else:
            out.append({"period": period, "status": r.get("status"), "message": r.get("message")})
    if args.print_md and mds:
        print("\n\n---\n\n".join(mds))
        return None
    return {"status": "ok" if any(o["status"] == "ok" for o in out) else out[0]["status"],
            "reviews": out, "summary": review.aggregate(gt)}


if __name__ == "__main__":
    main(body)
