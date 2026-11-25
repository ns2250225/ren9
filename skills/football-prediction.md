---
name: football-prediction
description: >
  Quantitative football match prediction engine: Elo, Poisson, Dixon-Coles and de-vigged market odds
  combined into calibrated win/draw/loss probabilities. Use to compute match probabilities,
  inspect individual models, or run a time-split backtest.
license: MIT
compatibility: Python 3.10+
metadata:
  version: "1.0.0"
  parent: ren9-analyst
---

# football-prediction：足球预测引擎

多模型融合，全部由 Python 计算，不依赖大模型判断。

## 执行

```bash
python ../scripts/calculate_probability.py --date today
python ../scripts/calculate_probability.py --date today --matches 3,8 --refresh

# 单模型工具
python ../scripts/calculate_elo.py --home-elo 1750 --away-elo 1620
python ../scripts/calculate_elo.py --league E0 --top 20
python ../scripts/dixon_coles.py --league E0 --home Arsenal --away Chelsea

# 时间切分回测 + 学习集成权重
python ../scripts/backtest.py
```

## 模型

| 模型 | 输入 | 说明 |
|---|---|---|
| A. Elo | ClubElo 评级；不可用时由历史赛果自算 | 评级差 + 主场优势，Davidson 模型给出平局概率 |
| B. Poisson | 历史赛果（可混合 xG） | 主客场攻防强度，时间衰减，向联赛均值收缩 |
| C. Dixon-Coles | 历史赛果 | 极大似然估计攻击 / 防守 / 主场优势，ρ 修正 0:0、1:0、0:1、1:1，时间衰减，L2 正则 |
| D. 市场 | football-odds 的去水概率 | 多来源平均 |
| E. 集成 | A–D | 加权线性池化 + 温度校准；缺失的模型剔除后权重重新归一化 |

初始权重 `elo 0.15 / poisson 0.20 / dixon_coles 0.35 / market 0.30`（`config/model_weights.yaml`）。
运行 `backtest.py` 后改用回测学习到的权重（写入 `config/model_weights.learned.yaml`），
学习时限制市场权重上限、保留各模型最低权重。历史样本少的球队（如洲际赛事）对应模型自动降权。

## 输出（每场）

```json
{
  "match_id": "match_001",
  "probabilities": {"win": 0.56, "draw": 0.25, "loss": 0.19},
  "prediction": "WIN",
  "model_confidence": 0.72,
  "data_quality": 0.88,
  "models_used": ["elo", "poisson", "dixon_coles", "market"],
  "models_unavailable": {},
  "model_probabilities": {"elo": {"win": 0.5, "draw": 0.27, "loss": 0.23}},
  "weights_used": {"elo": 0.3, "poisson": 0.05, "dixon_coles": 0.05, "market": 0.6},
  "expected_goals": {"home": 1.65, "away": 0.92},
  "handicap": {"line": -1, "model": {"win": 0.31, "draw": 0.25, "loss": 0.44}}
}
```

- `probabilities` 三项之和恒为 1（主队视角：win=主胜）。
- `model_confidence`：模型一致性 + 模型覆盖度 + 结果明确度的组合指标，**不是命中概率**，要和概率分开展示。
- `data_quality`：0–1，赛单、球队对应、历史样本、赔率、Elo、积分榜、近况、开球时间、首发的加权完整度。
- `handicap`：让球胜平负（竞彩扩展），模型概率来自比分矩阵。

## 回测输出要点

`backtest.py` 对每个联赛按周滚动拟合，输出留出集上各模型与集成的
Accuracy / Brier / Log Loss / RPS、校准分箱、不同概率区间的实际命中率、任九组合模拟命中情况。
防泄漏措施：模型只用被预测比赛之前的数据；市场概率只用赛前赔率（非收盘）；
权重在时间更早的 60% 上拟合、更晚的 40% 上评估。结果存 `data/history/backtest_summary.json`。

## 规则与错误处理

- 某模型缺输入 → 记入 `models_unavailable` 并说明原因，不参与集成。
- 所有模型都缺输入 → `status: "no_prediction"`，`probabilities: null`。不要替它猜一个概率。
- 单队历史场次少于 `data.min_history_matches`（默认 4）不运行 Poisson / Dixon-Coles。
- 不要用一期的结果评价模型质量；用回测与累计复盘指标。
