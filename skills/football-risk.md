---
name: football-risk
description: >
  Upset and risk analysis for football matches: probability closeness, model disagreement, missing data,
  odds movement, lineup/injury uncertainty, fixture congestion and competition context.
  Grades each match LOW / MEDIUM / HIGH / UNKNOWN with reasons.
license: MIT
compatibility: Python 3.10+
metadata:
  version: "1.0.0"
  parent: ren9-analyst
---

# football-risk：比赛风险分析

识别高风险比赛。风险评分是多因子的，**不只看最大概率**。

## 执行

```bash
python ../scripts/calculate_risk.py --date today
python ../scripts/calculate_risk.py --date today --full     # 含每个因子的取值
```

输入：通用参数（`--date` / `--period` / `--game-type` / `--refresh`）。依赖预测阶段的结果，缺少时自动运行。

## 风险因子

| 因子 | 含义 | 数据来源 |
|---|---|---|
| `closeness` 概率接近 | 首选概率低、与次选差距小 | 集成概率 |
| `disagreement` 模型分歧 | 各模型概率分布差异、首选不一致 | 各模型概率 |
| `data_gap` 数据不足 | 1 − 数据质量 | football-data |
| `odds_move` 赔率波动 | 去水概率的最大变动 | football-odds |
| `lineup` 伤停 / 首发 | 官方首发是否已公布 | ESPN |
| `fatigue` 赛程压力 | 休息天数、近 14 天比赛数 | 近期比赛日期 |
| `context` 赛事性质 | 杯赛、洲际赛事、保级、争冠 | 赛事类型 + 积分榜 |

每个因子取值 0–1。**采集不到的因子标记为未知**（`known: false`），按中性值计入总分并列在
`unknown_factors` 中，不会被当作"没有风险"。权重与阈值在 `config/model_weights.yaml` 的 `risk`。

## 分级

```text
LOW       低风险
MEDIUM    中风险
HIGH      高风险
UNKNOWN   数据不足（无法计算概率，或数据质量低于 safety.min_data_quality）
```

概率接近度对等级有硬约束：三项概率接近的比赛直接判为 HIGH；首选优势不明显的比赛不会判为 LOW。
例如 胜 80% / 平 12% / 负 8% 的比赛单选更明确（但不代表一定命中）；
胜 42% / 平 30% / 负 28% 的比赛为 HIGH，应重点考虑双选或排除。

## 输出

```json
{
  "counts": {"LOW": 7, "MEDIUM": 3, "HIGH": 4, "UNKNOWN": 0},
  "matches": [
    {
      "seq": 2, "match": "加拉塔萨雷 vs 阿斯顿维拉",
      "risk_level": "HIGH", "risk_score": 0.61,
      "reasons": ["概率接近：首选 43%，次选 33%", "模型分歧：模型首选不一致（2 种结果）"],
      "unknown_factors": ["伤停/首发不确定"]
    }
  ]
}
```

## 规则与错误处理

- 向用户说明风险时，`unknown_factors` 要如实转述（例如"伤停信息未知"），不要说成"无伤停"。
- LOW 不等于稳赢；HIGH 的比赛建议双选或排除，由 ren9-selector / ren9-optimizer 处理。
- 预测缺失的比赛一律 `UNKNOWN`，`risk_score: null`。
