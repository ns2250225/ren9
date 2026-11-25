---
name: ren9-review
description: >
  Post-match review of saved Ren9 predictions against official draw results: accuracy, Brier score,
  log loss, RPS, calibration, single/double coverage, nine-match hit and ROI from real prize money.
  Also summarizes historical performance and refits ensemble weights.
license: MIT
compatibility: Python 3.10+
metadata:
  version: "1.0.0"
  parent: ren9-analyst
---

# ren9-review：赛后复盘

自动获取官方开奖结果，对已保存的预测进行评估。

## 执行

```bash
python ../scripts/review_results.py --date yesterday     # 复盘昨天分析的期次
python ../scripts/review_results.py --period 26168
python ../scripts/review_results.py                       # 所有已开奖但未复盘的期次
python ../scripts/review_results.py --period 26168 --print   # 输出 Markdown 复盘报告
python ../scripts/review_results.py --summary             # 历史累计表现
python ../scripts/review_results.py --optimize-weights    # 用复盘数据重新拟合集成权重
python ../scripts/review_results.py --game-type jczq --date yesterday
```

| 参数 | 说明 |
|---|---|
| `--date` | 复盘该日期所分析的期次 |
| `--period` | 直接指定期号 |
| `--allow-post-deadline` | 允许查看销售截止后才生成的预测（默认排除） |
| `--summary` | 只输出累计指标 |
| `--optimize-weights` | 已复盘比赛 ≥ 50 场时重新拟合集成权重与校准温度 |

## 记录与指标

每期记录：原始预测概率、各模型概率、最终推荐、实际赛果与比分、单选命中、双选 / 全选覆盖、
9 场整体结果、模型偏差（平均预测概率 − 实际频率）、数据源质量（平均数据质量、各模型覆盖场数）。

| 指标 | 说明 |
|---|---|
| Accuracy | 首选结果命中率 |
| Brier Score | 概率平方误差（均匀猜测 0.667，越低越好） |
| Log Loss | 概率对数损失（均匀猜测 1.099） |
| RPS | 考虑胜-平-负顺序的概率误差 |
| Calibration | 概率分箱：平均预测概率 vs 实际频率（`--summary`） |
| ROI | （税后奖金 − 成本）/ 成本 |
| Hit Rate | 9 场全中的期数占比 |

**ROI 只用真实数据计算**：奖金取官方公布的该期任九单注奖金（单注超过 1 万元按 20% 扣税），
成本取方案的真实注数 × 2 元。官方未公布奖金时 `roi: null`，不做估算。

## 输出

```json
{
  "status": "ok",
  "reviews": [
    {
      "period": "26168", "status": "ok", "draw_result": "3 3 3 1 0 3 1 3 3 3 0 3 1 0",
      "metrics": {"n": 14, "accuracy": 0.57, "brier": 0.58, "log_loss": 0.98, "rps": 0.20},
      "plans": [{"strategy": "balanced", "hits": 7, "total": 9, "all_nine_hit": false,
                 "cost": 48, "prize_per_bet": 3660.0, "payout_after_tax": 0, "roi": -1.0}],
      "markdown_path": "data/results/ren9_26168_review.md"
    }
  ],
  "summary": {"period_count": 1, "metrics": {}, "strategies": {}, "calibration": []}
}
```

完整复盘结果保存在 `data/results/ren9_<期号>_review.json`（含逐场明细）和 `.md`。

## 数据源

- 中国体彩网传统足彩开奖接口：14 场赛果（3 / 1 / 0，`*` 表示比赛无效）、比分、任九单注奖金与中奖注数。
- 竞彩复盘：官方赛果接口不可用，使用 ESPN 终场比分匹配已对应上的比赛。

## 规则与错误处理

| `status` | 含义 |
|---|---|
| `pending` | 尚未开奖或赛果不完整，稍后再试 |
| `no_prediction` | 该期没有在销售截止前保存的预测 |
| `nothing_to_review` | 没有待复盘的期次 |
| `error` | 官方开奖接口不可用 |

- **防泄漏**：只评估销售截止前生成的最后一次预测；截止后生成的预测带 `post_deadline` 标记，默认不参与。
- 无效比赛（`*`）：任意选项都算中，不计入概率指标；复式中该场的每个选项各算一注中奖。
- 对阵与开奖数据不一致时在 `warnings` 中提示。
- 不要用一期的结果判断模型好坏；样本少时指标统计意义有限，向用户说明这一点。
