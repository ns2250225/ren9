---
name: ren9-optimizer
description: >
  Allocate single / double / triple selections across the nine chosen Ren9 matches under a budget,
  and compute ticket count, cost and model-estimated coverage probability. Supports conservative,
  balanced and aggressive strategies, and plain ticket-count calculation.
license: MIT
compatibility: Python 3.10+
metadata:
  version: "1.0.0"
  parent: ren9-analyst
---

# ren9-optimizer：投注组合优化

为选出的 9 场比赛分配单选、双选、全选。

## 执行

```bash
python ../scripts/optimize_bet.py --strategy balanced
python ../scripts/optimize_bet.py --all-strategies              # 三套方案
python ../scripts/optimize_bet.py --strategy aggressive --budget 200
python ../scripts/optimize_bet.py --count "3;31;10;3;310;0;3;31;0"   # 只算注数，不联网
```

输入：通用参数 + `--strategy`、`--all-strategies`、`--budget`（元，覆盖策略默认上限）。
`--count` 用开奖代码描述每场选择（3=胜、1=平、0=负），分号分隔。

## 注数与金额

```text
N = 各场选择数的乘积
例：胜 | 胜平 | 平负 | 胜 | 胜平负 | 负 | 胜 | 胜平 | 负
N = 1×2×2×1×3×1×1×2×1 = 24 注；每注 2 元 → 48 元
```

## 优化方法

在注数上限（预算）和双选 / 全选场次上限内，对 3^9 = 19683 种配置穷举，最大化

```text
Π f_i(S_i) / N^λ        f_i(S) = Σ_{o∈S} p_i(o) · q_i(o)^(−α)
```

`p` 为模型概率，`q` 为市场概率。α = 0 时 f 就是该场的覆盖概率，目标即 9 场全中的概率；
λ 是成本厌恶——只有覆盖率提升超过 2^λ 倍才把单选升为双选；α > 0 时偏向市场低估的结果（冷门）。

| 策略 | 默认注数上限 | 双选 / 全选上限 | λ | α | 特点 |
|---|---:|---|---:|---:|---|
| conservative 保守 | 8 注（16 元） | 3 / 0 | 0.30 | 0 | 明确场次为主，控制成本 |
| balanced 均衡 | 32 注（64 元） | 5 / 1 | 0.15 | 0 | 兼顾概率与成本 |
| aggressive 激进 | 128 注（256 元） | 7 / 3 | 0.05 | 0.35 | 保留部分冷门，扩大覆盖 |

预设在 `config/settings.yaml` 的 `strategy.presets`，可修改。

## 输出

```json
{
  "date": "2026-11-24",
  "strategy": "balanced",
  "selected_matches": [1, 3, 4, 6, 7, 8, 10, 13, 14],
  "excluded_matches": [2, 5, 9, 11, 12],
  "selections": [
    {"seq": 1, "match": "博德闪耀 vs LASK林茨", "picks": ["win", "loss"], "picks_zh": "胜负",
     "type": "双选", "coverage": 0.811, "risk_level": "LOW"}
  ],
  "singles": 5, "doubles": 3, "triples": 1,
  "bet_count": 24,
  "unit_price": 2,
  "total_cost": 48,
  "budget": 64,
  "hit_probability": 0.1075,
  "all_singles_hit_probability": 0.0212,
  "note": "多选会提高组合覆盖率，但成本同比例增加，并不代表一定提高最终收益。"
}
```

## 规则与错误处理

- `hit_probability` 是假设各场独立时模型估计的 9 场全中概率，是估计值，不是保证。
- 必须向用户说明：多选增加覆盖的同时增加成本，并不代表一定提高最终收益。
- 筛选未成功（`insufficient` / `invalid_schedule`）时返回 `bet_count: 0` 和原因，不生成方案。
- 预算不足一注（< 2 元）报错。
- 本 Skill 只做计算，不购买彩票。
