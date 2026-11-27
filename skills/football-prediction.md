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
| D. 市场 | football-odds 的去水概率 | 多来源加权平均（低水位来源权重更高） |
| E. 集成 | A–D | 回测学习后为对数线性池化 p(k) ∝ exp(b_k)·Π p_m(k)^{w_m}；缺失的模型自动剔除 |

初始权重 `elo 0.15 / poisson 0.20 / dixon_coles 0.35 / market 0.30`（线性池化，`config/model_weights.yaml`）。
运行 `backtest.py` 后改用回测学习到的参数（写入 `config/model_weights.learned.yaml`）：
**有赔率的比赛和没有赔率的比赛各拟合一套**。回测结果是赛前市场概率最准，有赔率时集成基本等于
校准后的市场概率（指数约 1.09，略微锐化，平局略上调）；没有赔率时用 Elo / Poisson / Dixon-Coles。
历史样本少的球队（如洲际赛事）对应模型自动降权。

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
  "weights_used": {"market": 1.0},
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
集成参数在时间更早的 60% 上拟合、更晚的 40% 上评估。默认 16 个欧洲联赛 × 6 个赛季，约 3 分钟。结果存 `data/history/backtest_summary.json`。

## 规则与错误处理

- 某模型缺输入 → 记入 `models_unavailable` 并说明原因，不参与集成。
- 所有模型都缺输入 → `status: "no_prediction"`，`probabilities: null`。不要替它猜一个概率。
- 单队历史场次少于 `data.min_history_matches`（默认 4）不运行 Poisson / Dixon-Coles。
- 不要用一期的结果评价模型质量；用回测与累计复盘指标。
