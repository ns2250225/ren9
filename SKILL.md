---
name: ren9-analyst
version: "1.0.0"
display_name: 足彩任九智能推荐分析
display_name_en: Ren9 Analyst
description_zh: >
  自动获取中国体彩传统足彩任选9场的官方14场赛单，采集公开数据，用 Elo / Poisson / Dixon-Coles /
  市场赔率融合计算胜平负概率，评估风险，从14场中筛选9场，优化单选与双选组合，生成分析报告并在开奖后复盘。
  同时支持竞彩足球胜平负、让球胜平负。仅做数据分析，不保证命中或盈利，不代购彩票。
description_en: >
  Fetches the official 14-match coupon of China Sports Lottery Ren9 (pick 9 of 14), collects free public
  football data, computes win/draw/loss probabilities with an Elo / Poisson / Dixon-Coles / market-odds
  ensemble, grades match risk, selects nine matches, optimizes single and double picks under a budget,
  generates reports and reviews predictions after the draw. Also covers Jingcai 1X2 and handicap 1X2.
  Analysis only: no guarantee of winning, no ticket purchasing.
description: >
  Automatically collect football fixtures and public data,
  calculate match probabilities, analyze risks, select
  nine matches from a valid 14-match Ren9 coupon,
  optimize selections and generate prediction reports.
  Use when the user asks to analyze / recommend / review 中国足彩任选9场（任九、胜负彩14场）
  or 竞彩足球胜平负 / 让球胜平负.
license: MIT
compatibility: Python 3.10+
metadata:
  version: "1.0.0"
  category: football-analysis
---

# Ren9 Analyst — 足彩任九智能推荐分析

面向中国体育彩票**传统足彩任选 9 场**的分析 Skill：自动获取官方 14 场赛单 → 采集公开数据 →
多模型计算胜平负概率 → 风险分析 → 14 选 9 → 单选/双选优化 → 生成报告 → 赛后复盘。
扩展支持竞彩足球胜平负、让球胜平负。

**分工**：你（Agent）负责理解用户意图、调用脚本、解释结果；所有概率、赔率、注数、筛选结果
都由 Python 脚本计算。不要自己估算或改写任何数字。

## 运行方式

所有脚本位于本 Skill 目录的 `scripts/` 下，可在任意工作目录执行，输出为 UTF-8 JSON（stdout）。
设 `SKILL_DIR` 为本文件所在目录：

```bash
python "$SKILL_DIR/scripts/generate_report.py" --date today
```

首次使用先安装依赖：`pip install -r "$SKILL_DIR/requirements.txt"`（Python 3.10+，无需 API Key）。

## 用户意图 → 命令

| 用户说 | 执行 |
|---|---|
| 分析今天的任九 / 帮我推荐一套方案 | `python scripts/generate_report.py --date today` |
| 推荐今天的任九，保守策略 | `python scripts/generate_report.py --date today --strategy conservative` |
| 分析本期 14 场，生成三套方案 | `python scripts/generate_report.py --date today --all-strategies` |
| 预算 100 元以内 | 追加 `--budget 100` |
| 重新分析第 3 场和第 8 场 | `python scripts/generate_report.py --date today --matches 3,8 --refresh` |
| 详细看看第 3 场 | `python scripts/generate_report.py --date today --match 3 --print` |
| 分析第 26170 期 | 追加 `--period 26170` |
| 临近开赛，更新一下推荐 | `python scripts/generate_report.py --date today --refresh` |
| 复盘昨天的任九预测 | `python scripts/review_results.py --date yesterday` |
| 模型历史表现怎么样 | `python scripts/review_results.py --summary` |
| 分析今天的竞彩 | `python scripts/generate_report.py --date today --game-type jczq` |
| 数据源还能用吗 | `python scripts/check_sources.py` |

`generate_report.py` 会自动运行缺少的上游阶段，所以一条命令即可完成整个流程。
策略：`conservative`（保守）/ `balanced`（均衡，默认）/ `aggressive`（激进）。

## 标准流程

1. **判断日期与玩法**：默认今天（北京时间）、传统足彩任九。同一天可能有多期在售，
   脚本默认选最先截止的一期，并在输出的 `other_open_periods` 里列出其余期次——如有，告诉用户。
2. **运行** `generate_report.py`，读取 stdout 的 JSON：
   - `status: "ok"` → 读取 `markdown_path` 指向的报告并呈现给用户。
   - `status: "no_schedule"` → 没有拿到官方赛单。把 `action_required` 转告用户，请其提供 14 场比赛
     （见下文"手动赛单"）。**不要**自己编比赛，也**不要**用竞彩比赛凑 14 场。
   - `status: "insufficient"` / `"invalid_schedule"` → 有效比赛不足 9 场或赛单未通过校验，
     不出正式推荐。报告里仍有全部比赛的分析，照实说明原因（`message`）。
3. **呈现报告**：必须展示全部 14 场（含被排除的 5 场及理由）、推荐的 9 场、单选/双选配置、
   注数与金额、风险等级、数据缺失提醒、风险提示。可以概括，但数字必须与报告一致。
   方案是多票组合时（报告里的"购买清单"，JSON 里的 `tickets`），**必须逐票列出每张复式票的 9 场选择、
   注数和金额**——"推荐"一列只是各票选择的合并，不能当成一张复式票去买。
4. **赛后**：用户要求复盘时运行 `review_results.py`，开奖前会返回 `pending`。

需要分步执行或查看中间结果时，使用下面的子 Skill（每个都可独立运行）。

## 子 Skill

| 子 Skill | 作用 | 脚本 |
|---|---|---|
| [ren9-schedule](skills/ren9-schedule.md) | 官方 14 场赛单获取与校验 | `scripts/fetch_schedule.py` |
| [football-data](skills/football-data.md) | 球队与比赛数据采集 | `scripts/fetch_team_data.py` |
| [football-odds](skills/football-odds.md) | 赔率、隐含概率、去水概率 | `scripts/fetch_odds.py` |
| [football-prediction](skills/football-prediction.md) | Elo / Poisson / Dixon-Coles / 集成概率 | `scripts/calculate_probability.py`、`calculate_elo.py`、`dixon_coles.py`、`backtest.py` |
| [football-risk](skills/football-risk.md) | 冷门与风险分析 | `scripts/calculate_risk.py` |
| [ren9-selector](skills/ren9-selector.md) | 14 选 9 | `scripts/select_matches.py` |
| [ren9-optimizer](skills/ren9-optimizer.md) | 单选/双选组合优化 | `scripts/optimize_bet.py` |
| [ren9-review](skills/ren9-review.md) | 赛后复盘 | `scripts/review_results.py` |

通用参数：`--date`（today / yesterday / YYYY-MM-DD）、`--period`、`--game-type ren9|jczq`、
`--refresh`（忽略缓存重算）、`--matches 3,8`（只重算指定场次）、`--full`（输出完整 JSON）。
各阶段结果保存在 `data/cache/runs/<玩法>_<期号>/<阶段>.json`，后续阶段自动复用。

## 手动赛单

官方接口不可用时，请用户提供本期 14 场比赛，保存为文件后运行：

```bash
python scripts/fetch_schedule.py --manual matches.csv --period 26169
python scripts/generate_report.py --date today --period 26169
```

文件格式任选其一：每行 `序号,主队,客队,赛事,比赛日期`；每行 `主队 vs 客队`；
或 JSON 列表 `[{"home": "...", "away": "...", "competition": "...", "date": "..."}]`。
手动赛单会在报告中标注"未经官方接口验证"。必须正好 14 场且无重复。

## 必须遵守的规则

1. **不虚构数据**：比赛、赔率、球队数据、概率只能来自脚本输出。脚本没有给出的信息
   （如伤停、预计首发、主教练）就说"没有可靠数据"，不要凭记忆补充。
2. **传统足彩 ≠ 竞彩**：竞彩比赛列表不能代替传统足彩 14 场赛单，也不能用其他联赛比赛补齐。
3. **概率不是保证**：`probabilities` 是模型估计；`model_confidence` 是模型一致性指标，
   不是命中概率，两者要分开说；`hit_probability` 是假设各场独立的估计值。
4. **多选的代价**：说明双选/全选提高覆盖率的同时成本同比例增加，不代表提高最终收益。
5. **截止时间**：输出里 `post_deadline: true` 或报告提示已过销售截止时间时，明确告诉用户
   本期已停售，结果仅供参考、不计入复盘。
6. **不做的事**：不代购彩票、不登录体彩网站、不涉及支付、不承诺中奖或盈利。
7. **每次呈现推荐都附上风险提示**（报告末尾已包含）。

## 定时运行

本 Skill 不内置定时器。用户要求每天自动分析时，用你所在平台的调度能力定时执行：
上午运行 `generate_report.py --date today` 生成初版；临近销售截止再运行一次并加 `--refresh`
更新赔率与首发；开奖后运行 `review_results.py`。

## 其他

- 可选 MCP 模式：`python mcp_server.py`（需要 `pip install mcp`），暴露 `get_ren9_schedule`、
  `get_match_data`、`get_odds`、`predict_match`、`analyze_risk`、`select_nine_matches`、
  `optimize_bet`、`generate_report`、`review_predictions`。不支持 MCP 的 Agent 用上面的 CLI 即可，功能相同。
- 配置：`config/settings.yaml`（策略预算、缓存、阈值）、`config/model_weights.yaml`（模型权重）、
  `config/data_sources.yaml`（数据源与赛事映射）、`config/team_aliases.yaml`（球队中英文名）。
- 模型权重校准：`python scripts/backtest.py`（时间切分回测，自动学习集成权重）。
- 更多说明见 [README.md](README.md)。
