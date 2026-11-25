---
name: football-odds
description: >
  Collect and analyze football odds (China Jingcai official 胜平负 / 让球胜平负, bookmaker 1X2,
  Asian handicap, over/under): implied probability, overround, de-vigged fair probability and odds movement.
  Also computes fair probabilities for any odds triple the user provides.
license: MIT
compatibility: Python 3.10+
metadata:
  version: "1.0.0"
  parent: ren9-analyst
---

# football-odds：赔率分析

## 执行

```bash
python ../scripts/fetch_odds.py --date today
python ../scripts/fetch_odds.py --date today --full
python ../scripts/fetch_odds.py --odds 1.85,3.40,4.20      # 只计算，不联网
```

## 计算

```text
隐含概率  P(胜) = 1/O(胜)，P(平) = 1/O(平)，P(负) = 1/O(负)
水位      overround = P(胜)+P(平)+P(负) − 1
去水概率  P'(x) = P(x) / (P(胜)+P(平)+P(负))
```

## 输出（每个来源一条）

```json
{
  "match_id": "match_001",
  "odds": {"home": 1.85, "draw": 3.40, "away": 4.20},
  "implied_probability": {"home": 0.5405, "draw": 0.2941, "away": 0.2381},
  "overround": 0.0728,
  "fair_probability": {"home": 0.5039, "draw": 0.2742, "away": 0.2219}
}
```

整期输出中每场包含：

| 字段 | 含义 |
|---|---|
| `sources[]` | 胜平负（欧洲赔率）各来源：`source`、`kind`（official / commercial）、赔率、隐含概率、水位、去水概率、`fetched_at` |
| `jc_handicap` | 中国竞彩让球胜平负（让球数 `line` + 三项赔率与去水概率） |
| `asian_handicap` | 亚洲让球（商业机构，盘口与两项赔率） |
| `totals` | 大小球（盘口与两项赔率） |
| `market` | 市场共识：各来源去水概率的平均，注明用了几个官方 / 商业来源 |
| `movement` | 赔率波动：开盘 → 当前，或首次记录 → 当前的去水概率最大变动 |

## 数据源

| 来源 key | 类型 | 内容 |
|---|---|---|
| `sporttery_jc_had` | official | 竞彩官方胜平负 |
| `sporttery_jc_hhad` | official | 竞彩官方让球胜平负 |
| `sporttery_zc_ref` | official | 体彩网传统足彩赛单页的参考赔率（没有竞彩 HAD 时使用） |
| `espn_<机构>` | commercial | ESPN 提供的商业机构胜平负 / 让球 / 大小球（含开盘价） |

每次抓取的赔率变化都记入本地 `odds_snapshots` 表，用于波动分析。

## 规则与错误处理

- 赔率必须带来源和抓取时间；官方与商业来源分开保存，不合并成一个来源。
- 没有赔率的比赛：`available: false`、`market: null`，预测阶段改由其余模型独立预测。
- **去水概率不是最终预测概率**，只是集成模型的输入之一。
- 赔率 ≤ 1 或缺项时报错 `无效赔率`，不做猜测。
- 竞彩接口失败时在 `notes` 中说明，流程继续。
