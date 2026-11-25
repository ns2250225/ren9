---
name: ren9-selector
description: >
  Select exactly nine matches (and exclude five) from a valid 14-match Ren9 coupon using a
  normalized MatchScore of probability edge, model agreement, data quality and risk penalty.
  Always reports all 14 matches with reasons.
license: MIT
compatibility: Python 3.10+
metadata:
  version: "1.0.0"
  parent: ren9-analyst
---

# ren9-selector：任九筛选

约束：比赛总数 = 14，选中 = 9，排除 = 5。

## 执行

```bash
python ../scripts/select_matches.py --date today
python ../scripts/select_matches.py --date today --strategy conservative
python ../scripts/select_matches.py --date today --all-strategies
```

输入：通用参数 + `--strategy conservative|balanced|aggressive`（默认 balanced）。仅适用于 `--game-type ren9`。

## 四个阶段

1. **数据过滤** —— 以下比赛不能进入正常推荐：赛程未确认 / 取消 / 延期、比赛编号重复、球队信息不完整、
   数据严重缺失（数据质量低于 `safety.min_data_quality`）、模型无法计算概率。
   有效比赛不足 9 场 → 停止，`status: "insufficient"`。
2. **单场评分**

   ```text
   MatchScore = w_p·ProbabilityScore + w_a·ModelAgreementScore + w_q·DataQualityScore − w_r·RiskPenalty
   ```

   四项都归一化到 0–1：ProbabilityScore 为首选概率优势（1/3 → 0，≥80% → 1）；
   ModelAgreementScore 为多模型一致性；DataQualityScore 为数据完整度；RiskPenalty 为风险评分。
   权重来自策略预设（`config/settings.yaml`）；运行回测后 balanced 策略改用回测学习到的权重。
3. **9 场筛选** —— 按 MatchScore 取前 9。优先保留概率高、模型一致、数据好的比赛；
   优先排除三项概率接近、赔率异常波动、数据差、模型严重分歧的比赛。
4. **输出 14 场完整分析** —— 被排除的 5 场同样展示，并给出排除理由。

## 输出

```json
{
  "status": "ok",
  "strategy": "balanced",
  "eligible_count": 14,
  "selected": [1, 3, 4, 6, 7, 8, 10, 13, 14],
  "excluded": [
    {"seq": 2, "match": "加拉塔萨雷 vs 阿斯顿维拉", "match_score": 0.173,
     "reason": "综合评分 0.173 低于入选线 0.385（第 14 位）：三项概率接近（43%/33%/23%），模型预测分歧较大"}
  ],
  "score_weights": {"probability": 0.4, "agreement": 0.2, "data_quality": 0.15, "risk": 0.25},
  "matches": [
    {"seq": 1, "match": "博德闪耀 vs LASK林茨", "selected": true, "match_score": 0.446,
     "risk_level": "LOW", "reason": "综合评分 0.446（第 7 位）：首选概率 60%，模型一致性好"}
  ]
}
```

## 错误处理

| `status` | 含义 | 处理 |
|---|---|---|
| `ok` | 严格 9 场入选、5 场排除 | 继续 ren9-optimizer |
| `insufficient` | 有效比赛少于 9 场 | 不出正式推荐；向用户说明每场不合格的原因（`ineligible_reason`） |
| `invalid_schedule` | 赛单不是完整的 14 场 | 回到 ren9-schedule，请用户提供正确赛单 |
| `no_schedule` | 没有赛单 | 同上 |

向用户展示时必须包含全部 14 场，不能只展示被选中的比赛。
