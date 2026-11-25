# Ren9 Analyst — 通用足彩任九推荐分析 Agent Skill 产品需求文档（PRD）

> 产品名称：Ren9 Analyst  
> 中文名称：足彩任九智能推荐分析 Skill  
> 产品类型：通用 Agent Skill / 足球数据分析工具  
> 版本：V1.0  
> 运行环境：OpenClaw、Hermes、Codex、Claude Code、Cursor 及其他兼容 Agent Skills 开放格式的 Agent  
> 核心玩法：传统中国体育彩票任选9场（胜平负）  
> 扩展玩法：竞彩足球胜平负、让球胜平负  
> 部署方式：本地运行、无需独立后端  
> 数据要求：优先免费公开数据源，不依赖付费 API  
> 核心语言：Python + Markdown + JSON

---

# 一、项目概述

## 1.1 项目背景

目前大部分足球预测 Agent Skills 主要针对国际足球比赛，提供球队分析、赔率预测、比分预测和比赛胜平负概率。

但中国足彩任选9场具有独特的玩法规则：

- 每期从14场指定比赛中选择9场。
- 每场比赛选择胜、平、负中的一个或多个结果。
- 可以采用单选、双选、全选。
- 最终根据9场比赛的开奖结果计算中奖情况。
- 需要综合考虑比赛概率、冷门风险、数据完整性和投注组合数量。

单一足球预测 Skill 无法完整解决以下问题：

1. 自动获取传统足彩14场赛单。
2. 自动采集球队数据、历史数据和赔率。
3. 使用量化模型计算胜平负概率。
4. 评估14场比赛的预测难度。
5. 自动筛选9场比赛。
6. 对选中的比赛进行单选、双选配置。
7. 生成多套任九推荐方案。
8. 记录推荐并进行历史回测。

因此，本项目将多个专业 Skill 和独立 Python 分析模块组合，构建一套通用的任九分析系统。

## 1.2 产品目标

构建一个可以被不同 Agent 调用的足球任九推荐 Skill。

用户只需要输入：

```text
帮我分析今天的任九，自动推荐一套方案。
```

系统自动完成：

```text
获取当天赛程
    ↓
识别传统足彩14场比赛
    ↓
采集球队和历史比赛数据
    ↓
采集可用赔率数据
    ↓
计算胜平负概率
    ↓
评估比赛风险
    ↓
筛选9场比赛
    ↓
优化单选和双选
    ↓
生成任九推荐报告
    ↓
保存预测结果
    ↓
赛后自动复盘
```

## 1.3 非目标

V1.0 不实现：

- 自动购买彩票。
- 自动登录体育彩票网站。
- 自动支付或资金管理。
- 保证中奖。
- 虚构比赛、赔率和球队数据。
- 使用未经验证的模型结果冒充真实概率。
- 强制依赖付费足球数据 API。
- 强制依赖某一个大模型或 Agent 平台。

---

# 二、总体架构

## 2.1 系统架构

采用模块化 Skill 架构。

```text
Ren9 Analyst
│
├── SKILL.md
│   └── 主入口与 Agent 调度规则
│
├── skills/
│   ├── ren9-schedule/
│   │   └── 14场赛程获取
│   │
│   ├── football-data/
│   │   └── 足球数据采集
│   │
│   ├── football-odds/
│   │   └── 赔率分析
│   │
│   ├── football-prediction/
│   │   └── 比赛概率预测
│   │
│   ├── football-risk/
│   │   └── 冷门与风险分析
│   │
│   ├── ren9-selector/
│   │   └── 任九9场筛选
│   │
│   ├── ren9-optimizer/
│   │   └── 单选、双选组合优化
│   │
│   └── ren9-review/
│       └── 赛后复盘
│
├── scripts/
│   ├── fetch_schedule.py
│   ├── fetch_team_data.py
│   ├── fetch_odds.py
│   ├── calculate_probability.py
│   ├── calculate_elo.py
│   ├── dixon_coles.py
│   ├── calculate_risk.py
│   ├── select_matches.py
│   ├── optimize_bet.py
│   ├── generate_report.py
│   └── review_results.py
│
├── providers/
│   ├── sporttery.py
│   ├── clubelo.py
│   ├── football_data.py
│   ├── understat.py
│   └── espn.py
│
├── models/
│   ├── elo.py
│   ├── poisson.py
│   ├── dixon_coles.py
│   └── ensemble.py
│
├── config/
│   ├── settings.yaml
│   ├── data_sources.yaml
│   └── model_weights.yaml
│
├── templates/
│   ├── daily_report.md
│   └── match_report.md
│
├── data/
│   ├── cache/
│   ├── history/
│   ├── predictions/
│   └── results/
│
├── tests/
│
├── requirements.txt
├── LICENSE
└── README.md
```

## 2.2 核心设计原则

### 原则一：通用 Agent Skills 标准

所有子 Skill 使用标准 `SKILL.md` 文件组织。

不使用以下平台专属功能作为核心依赖：

- OpenClaw 专属工具调用。
- Hermes 专属 API。
- Codex 专属工作流。
- Claude Code 专属命令。
- Cursor 专属插件接口。

Agent 只负责理解任务、调度 Skill、解释分析结果。

Python 负责所有确定性的计算工作。

### 原则二：子 Skill 独立运行

每个子 Skill 都应该有：

- 独立的 SKILL.md。
- 明确的输入参数。
- 明确的输出格式。
- 独立的执行脚本。
- 数据源配置。
- 错误处理规则。

主 Skill 负责协调各个子 Skill。

### 原则三：计算与语言模型分离

比赛概率、赔率计算、组合数量、筛选结果必须由 Python 计算。

LLM 不允许凭空生成比赛赔率和预测概率。

---

# 三、通用 Agent 兼容性

## 3.1 兼容目标

| Agent | 兼容方式 | 优先级 |
|---|---|---|
| OpenClaw | 通用 SKILL.md + Python | P0 |
| Hermes | 通用 SKILL.md + Python | P0 |
| Codex | 通用 SKILL.md + Python | P0 |
| Claude Code | 通用 SKILL.md + Python | P0 |
| Cursor | 通用 SKILL.md + Python | P1 |
| Gemini CLI | 通用 SKILL.md + Python | P1 |
| 其他 Agent | 标准 SKILL.md + 脚本 | P1 |

兼容性以各 Agent 对开放 Skill 规范的实际支持情况为准。

## 3.2 标准 SKILL.md

主入口必须遵循开放 Agent Skills 规范。

示例：

```yaml
---
name: ren9-analyst
description: >
  Automatically collect football fixtures and public data,
  calculate match probabilities, analyze risks, select
  nine matches from a valid 14-match Ren9 coupon,
  optimize selections and generate prediction reports.
license: MIT
compatibility: Python 3.10+
metadata:
  version: "1.0.0"
  category: football-analysis
---
```

主 Skill 的功能：

1. 理解用户的任九分析请求。
2. 判断分析日期。
3. 检查赛程数据是否完整。
4. 调用对应数据采集模块。
5. 调用概率模型。
6. 调用任九筛选算法。
7. 生成报告。
8. 保存预测记录。

## 3.3 Skill 加载目录

推荐采用标准 Skill 目录。

Codex：

```text
~/.agents/skills/ren9-analyst/
```

Claude Code：

```text
.claude/skills/ren9-analyst/
```

OpenClaw、Hermes：

根据各自版本的 Skill 加载目录配置安装。

项目也应提供一份安装脚本，支持用户指定目标 Agent 的 Skills 目录。

---

# 四、子 Skill 功能设计

## 4.1 ren9-schedule：14场赛程获取

功能：

自动获取目标日期的传统足彩任选9场对应14场比赛。

输入：

```json
{
  "date": "2026-11-24",
  "game_type": "ren9"
}
```

输出：

```json
{
  "date": "2026-11-24",
  "game_type": "ren9",
  "expected_matches": 14,
  "matches": [],
  "complete": false,
  "source": [],
  "last_updated": null
}
```

核心要求：

- 区分传统足彩任选9和竞彩足球。
- 获取真实期号。
- 获取官方比赛编号。
- 获取主队和客队。
- 获取比赛开始时间。
- 获取赛事名称。
- 获取销售截止时间。
- 获取官方赛程状态。
- 检查比赛数量是否为14。
- 检查比赛编号是否重复。
- 检查赛程是否发生变更。

数据源优先级：

1. 中国体育彩票官方公开数据。
2. 中国体彩网官方公开赛程。
3. 其他公开赛事信息作为交叉验证。

特别要求：

竞彩足球的比赛列表不能直接代替传统足彩14场赛单。

如果无法找到传统足彩官方赛单：

- 不得将竞彩足球比赛拼凑成14场。
- 不得自动生成虚假的14场比赛。
- 提示用户当前缺少官方赛单。
- 支持用户上传或手动提供14场比赛。

## 4.2 football-data：足球数据采集

负责获取比赛所需的基础数据。

采集字段：

| 类别 | 字段 |
|---|---|
| 球队 | 球队名称、国家、联赛 |
| 排名 | 联赛排名、积分 |
| 近期表现 | 最近5场、最近10场 |
| 进攻 | 场均进球、射门、xG |
| 防守 | 场均失球、xGA |
| 主客场 | 主场胜率、客场胜率 |
| 历史交锋 | 交锋次数、胜平负 |
| 阵容 | 伤停、停赛、预计首发 |
| 赛程 | 休息天数、连续比赛 |
| 其他 | 主教练、战术、赛事重要性 |

免费数据源：

| 数据源 | 数据用途 |
|---|---|
| ClubElo | 球队实力评级 |
| Understat | xG、xGA |
| ESPN | 赛程、比分、球队信息 |
| football-data.co.uk | 历史比赛和赔率 |
| SportteryAPI | 竞彩比赛及赔率补充 |

数据采集规则：

- 优先获取当天比赛相关数据。
- 缓存历史数据，避免重复请求。
- 每个字段记录来源。
- 每个字段记录更新时间。
- 允许部分数据缺失。
- 不将缺失数据当作零。
- 不将预计首发当成官方首发。
- 不将传闻当成已确认伤停。

## 4.3 football-odds：赔率分析

负责采集和分析赔率。

支持：

- 欧洲赔率。
- 亚洲让球赔率。
- 大小球赔率。
- 中国竞彩胜平负赔率。
- 中国竞彩让球胜平负赔率。

核心计算：

### 隐含概率

对于胜平负赔率：

```text
P(胜) = 1 / O(胜)
P(平) = 1 / O(平)
P(负) = 1 / O(负)
```

### 去水概率

```text
P'(胜) = P(胜) / (P(胜)+P(平)+P(负))

P'(平) = P(平) / (P(胜)+P(平)+P(负))

P'(负) = P(负) / (P(胜)+P(平)+P(负))
```

输出：

```json
{
  "match_id": "match_001",
  "odds": {
    "home": 1.85,
    "draw": 3.40,
    "away": 4.20
  },
  "implied_probability": {
    "home": 0.5405,
    "draw": 0.2941,
    "away": 0.2381
  },
  "overround": 0.0727,
  "fair_probability": {
    "home": 0.5038,
    "draw": 0.2741,
    "away": 0.2221
  }
}
```

注意：

- 赔率必须记录来源和抓取时间。
- 不同公司赔率不能直接混为一个来源。
- 赔率缺失时允许模型独立预测。
- 竞彩官方赔率与商业机构赔率分开保存。
- 不得将赔率概率直接当作最终预测概率。

## 4.4 football-prediction：足球预测引擎

这是整个系统的核心量化模块。

采用多个模型融合，不依赖单一大模型。

### 模型 A：Elo

计算球队实力差距。

考虑：

- 当前 Elo。
- 主场优势。
- 历史比赛。
- 联赛等级。
- 对手实力。

输出：

```json
{
  "home_elo": 1750,
  "away_elo": 1620,
  "elo_difference": 130
}
```

### 模型 B：Poisson

根据主客场进攻和防守数据预测进球分布。

输出：

```json
{
  "home_expected_goals": 1.65,
  "away_expected_goals": 0.92,
  "win_probability": 0.56,
  "draw_probability": 0.25,
  "loss_probability": 0.19
}
```

### 模型 C：Dixon-Coles

在 Poisson 基础上调整低比分比赛的概率。

重点处理：

- 0:0
- 1:0
- 0:1
- 1:1

支持：

- 时间衰减。
- 球队攻击强度。
- 球队防守强度。
- 主场优势。

### 模型 D：赔率概率

使用多个可靠赔率来源计算市场隐含概率。

### 模型 E：集成模型

综合：

- Elo 模型。
- Poisson 模型。
- Dixon-Coles 模型。
- 市场去水概率。

示例权重：

```yaml
ensemble:
  elo: 0.15
  poisson: 0.20
  dixon_coles: 0.35
  market: 0.30
```

权重只作为初始配置，必须通过时间切分的历史回测和概率校准调整，不应长期固定。

最终输出：

```json
{
  "match_id": "match_001",
  "probabilities": {
    "win": 0.56,
    "draw": 0.25,
    "loss": 0.19
  },
  "prediction": "WIN",
  "model_confidence": 0.72,
  "data_quality": 0.88,
  "models_used": [
    "elo",
    "poisson",
    "dixon_coles",
    "market"
  ]
}
```

注意：

`model_confidence` 不是预测命中概率，必须与胜平负概率分别展示。

## 4.5 football-risk：比赛风险分析

负责识别高风险比赛。

分析因素：

| 风险因素 | 说明 |
|---|---|
| 概率接近 | 胜平负概率相近 |
| 赔率波动 | 赔率明显变化 |
| 数据不足 | 缺少近期数据 |
| 伤停不确定 | 核心球员状态不明 |
| 阵容轮换 | 预计轮换较大 |
| 赛程压力 | 连续比赛、休息不足 |
| 赛事性质 | 杯赛、保级、争冠 |
| 模型分歧 | 不同模型预测差异较大 |

风险分级：

```text
LOW       低风险
MEDIUM    中风险
HIGH      高风险
UNKNOWN   数据不足
```

风险评分不能只根据最大概率计算。

例如：

```text
比赛 A：胜 80%，平 12%，负 8%
比赛 B：胜 42%，平 30%，负 28%
```

比赛 A 的单选预测明显更明确，但不意味着一定命中。

比赛 B 应重点考虑双选或排除。

## 4.6 ren9-selector：任九筛选

负责从14场比赛中选择9场。

核心约束：

```text
比赛总数 = 14

选中比赛数 = 9

排除比赛数 = 5
```

### 第一阶段：数据过滤

以下比赛不能直接进入正常推荐：

- 赛程未确认。
- 比赛取消。
- 比赛编号重复。
- 球队信息不完整。
- 数据严重缺失。
- 模型无法计算概率。

如果有效比赛不足9场，停止生成正式推荐。

### 第二阶段：单场评分

综合以下指标：

```text
MatchScore =
    ProbabilityScore
  + ModelAgreementScore
  + DataQualityScore
  - RiskPenalty
```

其中：

- ProbabilityScore：预测结果的概率优势。
- ModelAgreementScore：多个模型的预测一致性。
- DataQualityScore：数据完整程度。
- RiskPenalty：伤停、阵容和赔率等风险。

所有指标需要归一化，并通过历史回测确定实际权重。

### 第三阶段：9场筛选

默认策略：

优先保留：

- 预测概率较高的比赛。
- 模型一致性较好的比赛。
- 数据质量较高的比赛。
- 比赛状态和阵容信息明确的比赛。

优先排除：

- 三项概率接近的比赛。
- 赔率异常波动的比赛。
- 数据质量差的比赛。
- 多模型预测严重分歧的比赛。

### 第四阶段：输出14场完整分析

即使只选择9场，也必须展示全部14场。

不得只展示被选中的比赛而隐藏被排除比赛。

## 4.7 ren9-optimizer：投注组合优化

负责为选出的9场比赛分配单选、双选和全选。

例如：

```text
第1场：胜
第2场：胜平
第3场：平负
第4场：胜
第5场：胜平负
第6场：负
第7场：胜
第8场：胜平
第9场：负
```

组合数量：

```text
N = 1 × 2 × 2 × 1 × 3 × 1 × 1 × 2 × 1

N = 24 注
```

如果每注2元：

```text
总金额 = 24 × 2 = 48元
```

### 优化目标

在用户设定预算下：

- 优先覆盖概率较高的结果。
- 对不确定比赛增加双选。
- 控制组合数量。
- 计算覆盖概率。
- 计算单注数量。
- 计算理论成本。

必须说明：

多选会增加组合覆盖，但同时增加成本；并不代表一定提高最终收益。

### 可选策略

```yaml
strategy:
  - conservative
  - balanced
  - aggressive
```

保守：

优先选择明确场次，降低高风险比赛的数量。

均衡：

兼顾预测概率和组合成本。

激进：

允许保留部分冷门结果，增加覆盖范围。

## 4.8 ren9-review：赛后复盘

自动获取比赛结果，对预测进行评估。

记录：

- 原始预测概率。
- 最终推荐结果。
- 实际比赛结果。
- 单选命中情况。
- 双选覆盖情况。
- 9场整体结果。
- 模型预测偏差。
- 数据源质量。

评估指标：

| 指标 | 说明 |
|---|---|
| Accuracy | 胜平负预测准确率 |
| Brier Score | 概率预测误差 |
| Log Loss | 概率损失 |
| Calibration | 概率校准 |
| ROI | 假设投注回报 |
| Hit Rate | 任九组合命中情况 |

ROI 必须使用真实历史奖金和真实投注成本计算，不能简单用预测命中率代替。

---

# 五、免费数据源设计

## 5.1 数据源清单

| 数据源 | 数据内容 | 免费 | API Key |
|---|---|---|---|
| 中国体育彩票 | 官方竞彩赛程、赔率 | 公开读取 | 通常不需要 |
| 中国体彩网 | 传统足彩期次、赛程 | 公开读取 | 通常不需要 |
| ClubElo | 球队 Elo | 是 | 不需要 |
| Understat | xG、xGA | 公开数据 | 通常不需要 |
| ESPN | 赛程、比分、球队信息 | 公开接口 | 通常不需要 |
| football-data.co.uk | 历史比赛、赔率 | 是 | 不需要 |

所有数据源必须经过实际接口测试后才能标记为可用。

## 5.2 数据源优先级

传统任九：

```text
中国体彩网官方14场赛单
       ↓
官方赛程验证
       ↓
公开球队数据
       ↓
历史赔率与历史赛果
       ↓
模型计算
```

竞彩足球：

```text
中国体育彩票竞彩接口
       ↓
竞彩比赛数据
       ↓
竞彩赔率
       ↓
球队和历史数据
       ↓
模型计算
```

两种玩法的数据结构应当分离。

## 5.3 数据源降级

如果官方接口不可用：

1. 尝试备用官方页面。
2. 尝试缓存数据。
3. 检查历史赛单是否属于同一期。
4. 提示用户手动提供赛程。
5. 停止使用不可信数据生成正式推荐。

禁止自动把其他联赛的比赛补齐到14场。

---

# 六、自动化任务流程

## 6.1 每日自动分析

用户可以要求 Agent 每天定时运行。

推荐工作流：

```text
09:00
获取当天可用赛程
    ↓
验证期次和比赛数量
    ↓
采集球队历史数据
    ↓
采集赔率数据
    ↓
计算模型概率
    ↓
识别高风险比赛
    ↓
生成初版任九推荐
    ↓
保存报告
```

临近比赛时：

```text
比赛开始前
    ↓
更新赔率
    ↓
检查官方首发
    ↓
检查伤停
    ↓
重新计算概率
    ↓
更新推荐报告
```

自动定时任务属于 Agent 平台调度能力，不写死在通用 Skill 中。

## 6.2 手动触发命令

支持自然语言：

```text
分析今天的任九
```

```text
推荐今天的任九，保守策略
```

```text
分析本期14场比赛，生成三套方案
```

```text
重新分析第3场和第8场
```

```text
复盘昨天的任九预测
```

---

# 七、推荐报告设计

## 7.1 报告内容

报告必须包括：

1. 期次和日期。
2. 数据来源。
3. 数据更新时间。
4. 14场比赛完整列表。
5. 每场胜平负概率。
6. 推荐结果。
7. 风险等级。
8. 推荐理由。
9. 9场选中名单。
10. 5场排除名单。
11. 单选、双选配置。
12. 组合数量和理论成本。
13. 数据缺失提醒。
14. 模型历史评估。

## 7.2 示例报告

# 任九推荐分析报告

日期：2026-11-24

期次：示例期次

数据状态：演示数据，不可用于实际投注

| 场次 | 主队 | 客队 | 胜 | 平 | 负 | 推荐 | 风险 |
|---|---|---|---:|---:|---:|---|---|
| 1 | A队 | B队 | 72% | 18% | 10% | 胜 | 低 |
| 2 | C队 | D队 | 35% | 40% | 25% | 平 | 高 |
| 3 | E队 | F队 | 55% | 25% | 20% | 胜 | 中 |
| 4 | G队 | H队 | 28% | 30% | 42% | 负 | 高 |

其余场次同样展示，直到14场全部完成。

### 推荐9场

| 场次 | 推荐 |
|---|---|
| 1 | 胜 |
| 2 | 胜平 |
| 3 | 胜 |
| 4 | 负 |
| 5 | 胜 |
| 6 | 平负 |
| 7 | 胜 |
| 8 | 胜 |
| 9 | 胜平 |

### 排除5场

展示每场比赛被排除的理由。

### 方案统计

```text
选择场次：9场
单选：6场
双选：3场
全选：0场

组合数量：8注
每注金额：2元
理论金额：16元
```

### 风险提示

- 概率为模型估计，不是确定性结果。
- 部分数据可能存在延迟。
- 伤停和首发信息可能变化。
- 不保证命中或盈利。

---

# 八、数据结构

## 8.1 比赛数据

统一采用 JSON：

```json
{
  "match_id": "ren9_001",
  "period": "example",
  "date": "2026-11-24",
  "home_team": "Team A",
  "away_team": "Team B",
  "competition": "League A",
  "kickoff": "2026-11-24T20:00:00+08:00",
  "status": "scheduled",
  "sources": []
}
```

## 8.2 预测数据

```json
{
  "match_id": "ren9_001",
  "probabilities": {
    "win": 0.55,
    "draw": 0.25,
    "loss": 0.20
  },
  "model_confidence": 0.75,
  "data_quality": 0.90,
  "risk_level": "medium",
  "recommendation": ["win"]
}
```

## 8.3 任九方案

```json
{
  "date": "2026-11-24",
  "strategy": "balanced",
  "selected_matches": [],
  "excluded_matches": [],
  "selections": [],
  "bet_count": 8,
  "unit_price": 2,
  "total_cost": 16
}
```

---

# 九、配置文件

settings.yaml：

```yaml
general:
  language: zh-CN
  timezone: Asia/Shanghai
  currency: CNY

lottery:
  game_type: ren9
  expected_matches: 14
  selected_matches: 9
  unit_price: 2

strategy:
  default: balanced

data:
  cache_enabled: true
  cache_ttl: 3600
  require_official_schedule: true
  allow_manual_schedule: true

models:
  elo: true
  poisson: true
  dixon_coles: true
  market: true

report:
  format:
    - markdown
    - json
  show_all_matches: true
  show_excluded_matches: true

safety:
  never_invent_data: true
  require_source_timestamp: true
  stop_on_invalid_schedule: true
```

---

# 十、技术选型

| 模块 | 技术 |
|---|---|
| Agent Skill | Agent Skills 开放格式 |
| 数据采集 | Python requests / httpx |
| 数据解析 | Pydantic |
| 数值计算 | NumPy / SciPy |
| 数据处理 | Pandas |
| 足球模型 | 自研 Elo / Poisson / Dixon-Coles |
| 历史存储 | SQLite |
| 缓存 | SQLite / 本地 JSON |
| 报告 | Markdown / JSON |
| 自动测试 | Pytest |
| 配置 | YAML |

## 10.1 运行环境

最低要求：

- Python 3.10+。
- 2GB 可用内存。
- 网络连接。
- 无 GPU 要求。
- 无需独立服务器。
- 无需付费 API Key。

## 10.2 部署模式

支持两种模式：

### 模式 A：Skill + 本地脚本

Agent 读取 SKILL.md，调用 Python 脚本。

优点：

- 最容易兼容不同 Agent。
- 不依赖特定平台。
- 容易调试。
- 计算结果可复现。

### 模式 B：Skill + MCP

额外提供可选 MCP Server。

对外暴露：

```text
get_ren9_schedule
get_match_data
get_odds
predict_match
analyze_risk
select_nine_matches
optimize_bet
generate_report
review_predictions
```

MCP 不是必需依赖。没有 MCP 支持的 Agent 仍可通过 Python CLI 使用全部核心功能。

---

# 十一、安装与使用

## 11.1 通用安装

```bash
git clone https://github.com/yourname/ren9-analyst.git

cd ren9-analyst

python -m venv .venv
```

激活虚拟环境：

Windows：

```powershell
.venv\Scripts\Activate.ps1
```

Linux / macOS：

```bash
source .venv/bin/activate
```

安装依赖：

```bash
pip install -r requirements.txt
```

执行检查：

```bash
python -m pytest tests/
```

将项目根目录作为 Skill 安装到 Agent 对应的 Skills 目录。

## 11.2 CLI 命令

```bash
# 获取传统任九赛单
python scripts/fetch_schedule.py --date today

# 获取球队数据
python scripts/fetch_team_data.py --date today

# 获取赔率
python scripts/fetch_odds.py --date today

# 计算比赛概率
python scripts/calculate_probability.py --date today

# 筛选9场
python scripts/select_matches.py --date today

# 生成组合
python scripts/optimize_bet.py --strategy balanced

# 生成完整报告
python scripts/generate_report.py --date today

# 赛后复盘
python scripts/review_results.py --date yesterday
```

具体命令参数以最终实现的 CLI 为准。

---

# 十二、测试与验收

## 12.1 功能验收

| 测试项 | 验收标准 |
|---|---|
| 读取14场赛单 | 能识别14场真实比赛 |
| 赛程校验 | 能识别缺失和重复 |
| 数据采集 | 每项数据有来源 |
| 赔率分析 | 能正确计算去水概率 |
| 模型预测 | 三项概率总和为100% |
| 风险分析 | 能识别数据不足 |
| 任九筛选 | 严格选择9场 |
| 排除列表 | 严格排除5场 |
| 组合优化 | 组合数量计算正确 |
| 报告生成 | 展示14场完整分析 |
| 赛后复盘 | 能匹配比赛结果 |
| 多 Agent | 不依赖平台专属接口 |

## 12.2 预测质量验收

不能只用一次推荐结果判断模型质量。

应当建立至少一个完整赛季或足够数量的历史比赛数据集，进行时间切分回测。

核心指标：

- Brier Score。
- Log Loss。
- RPS。
- 概率校准曲线。
- 胜平负分类准确率。
- 不同概率区间的实际命中率。
- 任九组合历史命中情况。

测试必须避免数据泄漏：

- 不能使用比赛结束后的赔率预测赛前结果。
- 不能使用比赛结束后的伤停或首发信息。
- 训练数据与测试数据按时间切分。
- 每次推荐保留预测时点的数据快照。

## 12.3 多 Agent 验收

同一套代码分别在以下环境测试：

- OpenClaw。
- Hermes。
- Codex。
- Claude Code。

要求：

1. 能发现主 Skill。
2. 能读取 SKILL.md。
3. 能找到脚本目录。
4. 能执行 Python。
5. 能读取 JSON 输出。
6. 能生成 Markdown 报告。
7. 不需要修改核心代码。

---

# 十三、开发计划

## Phase 1：基础 Skill

- 建立项目目录。
- 编写主 SKILL.md。
- 编写标准输入输出。
- 实现 Python CLI。
- 实现14场赛程校验。
- 实现 Markdown 报告。

## Phase 2：数据采集

- 实现中国体彩接口适配。
- 实现传统足彩赛程适配。
- 实现 ClubElo。
- 实现 ESPN。
- 实现历史数据缓存。
- 实现数据质量检查。

## Phase 3：预测引擎

- 实现 Elo。
- 实现 Poisson。
- 实现 Dixon-Coles。
- 实现赔率去水。
- 实现集成预测。
- 实现概率校准。

## Phase 4：任九优化

- 实现14选9。
- 实现风险评分。
- 实现单选和双选。
- 实现预算约束。
- 实现多方案生成。

## Phase 5：自动复盘

- 保存每日预测。
- 自动获取比赛结果。
- 计算预测指标。
- 生成历史表现报告。
- 优化模型权重。

## Phase 6：多 Agent 兼容

- OpenClaw 测试。
- Hermes 测试。
- Codex 测试。
- Claude Code 测试。
- 编写通用安装器。

---

# 十四、开源项目整合计划

本项目优先复用以下开源项目的算法和实现思路，而不是盲目复制全部代码。

| 项目 | 复用内容 | 整合方式 |
|---|---|---|
| football-lottery-advisor | 中国足彩规则、赔率计算 | 任九规则模块参考 |
| JetQiao/football-prediction-skill | Dixon-Coles、Elo、模型回测 | 预测引擎参考或适配 |
| machina-sports/sports-skills | 体育数据采集 | 数据适配器 |
| SportteryAPI | 竞彩数据与赔率 | 可选数据适配器 |
| saowes/football-prediction-skill | 战术分析、风险分析 | 可选分析模块 |

参考项目：

1. https://github.com/stulove123/football-lottery-advisor
2. https://github.com/JetQiao/football-prediction-skill
3. https://github.com/machina-sports/sports-skills
4. https://github.com/Johnserf-Seed/SportteryAPI
5. https://github.com/saowes/football-prediction-skill

重要要求：

- 每个上游项目独立核实许可证。
- 不得将无许可证仓库的代码直接复制进 MIT 项目。
- 优先使用独立实现或通过命令行适配。
- 记录上游项目版本和来源。
- 不强制安装全部上游项目。
- 通过统一接口替换数据源和模型。

---

# 十五、V1.0 最终交付物

必须交付：

```text
ren9-analyst/
│
├── SKILL.md
├── README.md
├── LICENSE
├── requirements.txt
│
├── skills/
│   ├── ren9-schedule/
│   ├── football-data/
│   ├── football-odds/
│   ├── football-prediction/
│   ├── football-risk/
│   ├── ren9-selector/
│   ├── ren9-optimizer/
│   └── ren9-review/
│
├── scripts/
├── providers/
├── models/
├── config/
├── templates/
├── data/
└── tests/
```

最终用户只需要安装一次主 Skill，就能获得全部子功能。

---

# 十六、最终产品定位

Ren9 Analyst 不是简单的足球比分预测 Skill，而是一套面向中国足彩任选9场的自动化分析系统。

它具备：

- 通用 Agent Skills 开放格式。
- 多 Skill 协同。
- 免费公开数据源。
- 多模型概率预测。
- 传统足彩14场赛程识别。
- 任九9场自动筛选。
- 单选和双选优化。
- 多套推荐方案。
- 历史回测。
- 自动赛后复盘。
- 本地运行。
- 不依赖付费 API。
- 不依赖特定 Agent。

**核心产品原则：数据真实、概率可追溯、推荐可解释、计算可复现、Agent 可替换。**