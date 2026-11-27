# Ren9 Analyst — 足彩任九智能推荐分析 Skill

面向中国体育彩票**传统足彩任选 9 场**的通用 Agent Skill：自动获取官方 14 场赛单，采集公开数据，
用 Elo / Poisson / Dixon-Coles / 市场去水概率融合计算胜平负概率，评估风险，14 选 9，
优化单选 / 双选组合并拆成多张互不重叠的复式票（同样注数下全中概率更高），生成报告并在开奖后复盘。
扩展支持竞彩足球胜平负、让球胜平负。

- 本地运行，无需后端、无需 GPU、无需付费 API 或 API Key
- 标准 `SKILL.md` + Python CLI，不依赖任何 Agent 平台的专属接口
- 所有数字都由 Python 计算，每项数据带来源与抓取时间；缺失的数据不会被编造或当作 0

> **声明**：本项目只做数据分析。概率是模型估计，不保证命中或盈利，不构成投注建议；
> 不代购彩票、不登录体彩网站、不涉及支付。

## 快速开始

```bash
python -m venv .venv
# Windows:  .venv\Scripts\Activate.ps1      Linux / macOS:  source .venv/bin/activate
pip install -r requirements.txt

python -m pytest tests/                 # 离线测试（不联网）
python scripts/check_sources.py         # 实测各数据源是否可用
python scripts/generate_report.py --date today --all-strategies --print
```

需要 Python 3.10+。

## 安装为 Agent Skill

把项目根目录作为一个 Skill 安装到 Agent 的 Skills 目录即可，子 Skill 随主 Skill 一起安装。

```bash
python install.py --agent claude                 # ~/.claude/skills/ren9-analyst
python install.py --agent claude --project .     # <项目>/.claude/skills/ren9-analyst
python install.py --agent codex                  # ~/.agents/skills/ren9-analyst
python install.py --target /path/to/skills       # 其他 Agent：直接指定 Skills 目录
python install.py --list                         # 查看各 Agent 的默认目录
```

可加 `--deps`（同时安装依赖）、`--mode link`（符号链接）、`--force`（覆盖，保留 `data/`）。
OpenClaw、Hermes 的 Skills 目录随版本变化，默认路径不对时用 `--target` 指定。

安装后对 Agent 说：

```text
帮我分析今天的任九，自动推荐一套方案。
推荐今天的任九，保守策略。
分析本期14场比赛，生成三套方案。
重新分析第3场和第8场。
复盘昨天的任九预测。
```

## CLI

所有脚本输出 UTF-8 JSON 到 stdout，可在任意目录执行。

```bash
python scripts/fetch_schedule.py --date today           # 官方 14 场赛单 + 校验
python scripts/fetch_team_data.py --date today          # 球队 / 比赛数据
python scripts/fetch_odds.py --date today               # 赔率、隐含概率、去水概率
python scripts/calculate_probability.py --date today    # 胜平负概率
python scripts/calculate_risk.py --date today           # 风险分级
python scripts/select_matches.py --date today           # 14 选 9
python scripts/optimize_bet.py --strategy balanced      # 单选 / 双选组合
python scripts/generate_report.py --date today          # 完整报告（自动补跑上游阶段）
python scripts/review_results.py --date yesterday       # 赛后复盘

python scripts/backtest.py                              # 时间切分回测 + 学习集成参数（约 3 分钟）
python scripts/check_sources.py                         # 数据源实测
python scripts/calculate_elo.py --home-elo 1750 --away-elo 1620
python scripts/dixon_coles.py --league E0 --home Arsenal --away Chelsea
python scripts/fetch_odds.py --odds 1.85,3.40,4.20      # 只算去水概率
python scripts/optimize_bet.py --count "3;31;10;3;310;0;3;31;0"   # 只算注数
```

通用参数：

| 参数 | 说明 |
|---|---|
| `--date` | today / yesterday / tomorrow / YYYY-MM-DD（北京时间） |
| `--period` | 传统足彩期号；缺省时选"销售窗口包含该日期且最先截止"的一期 |
| `--game-type` | `ren9`（默认）或 `jczq`（竞彩足球） |
| `--strategy` | conservative / balanced / aggressive；`--all-strategies` 同时生成三套 |
| `--budget` | 预算上限（元） |
| `--matches 3,8` | 只重新分析指定场次（配合 `--refresh`） |
| `--refresh` | 忽略缓存，重新抓取并重算 |
| `--manual 文件` | 手动赛单（官方接口不可用时） |
| `--full` | 输出完整 JSON；`--print` 直接输出 Markdown 报告 |

各阶段结果保存在 `data/cache/runs/<玩法>_<期号>/`，后续阶段自动复用；上游变化
（赛程变更、`--refresh`、模型权重变化）会触发下游重算。

## 工作流程

```text
官方 14 场赛单 → 校验（数量 / 重复 / 缺失 / 变更）
   → 球队与比赛数据（每项带来源与时间）
   → 赔率（官方与商业来源分开）→ 隐含概率 → 去水概率 → 按水位加权的市场共识
   → Elo / Poisson / Dixon-Coles / 市场 → 集成概率（回测学习的对数线性池化）
   → 风险分级 LOW / MEDIUM / HIGH / UNKNOWN
   → 数据过滤 → MatchScore → 选 9 排 5
   → 单选 / 双选 / 全选优化（预算约束）→ 拆成多张互不重叠的复式票（购买清单）
   → Markdown + JSON 报告 → 保存预测与数据快照
   → 开奖后复盘（Accuracy / Brier / Log Loss / RPS / 校准 / 真实 ROI）
```

停止条件：没有官方赛单、赛单不是完整的 14 场、有效比赛不足 9 场时，不生成正式推荐，
并说明原因；任何情况下都不会用竞彩比赛或其他联赛比赛补齐 14 场。

## 数据源

`scripts/check_sources.py` 会实测每个接口并把结果写入 `data/cache/source_status.json`。
下表是 2026-11-27 的实测结果：

| 数据源 | 内容 | 实测 | 不可用时 |
|---|---|---|---|
| 中国体彩网（传统足彩） | 期号、14 场赛单、截止时间、开奖结果、任九奖金 | 可用 | 备用官方接口 → 同期缓存 → 手动赛单 |
| 中国体育彩票（竞彩） | 竞彩比赛、胜平负 / 让球胜平负官方赔率 | 可用（官方赛果接口返回 403，未使用） | 只用其余模型预测 |
| 500 彩票网（任九选号页） | 当期 14 场的多家机构平均欧赔，补齐竞彩 / ESPN 没有赔率的比赛（次级联赛常见） | 可用（只支持当前在售期） | 只用竞彩官方与 ESPN 的赔率 |
| ESPN 公开接口 | 赛程对应、开球时间、积分榜、近 5 场、首发、商业赔率、洲际赛事历史 | 可用 | 该场球队数据缺失并提示 |
| football-data.co.uk | 国内联赛历史赛果、射门、xG、赛前赔率 | 可用 | 改用 ESPN 历史赛果 |
| ClubElo | 跨联赛 Elo | 本机网络下连接失败 | 由历史赛果自算 Elo（仅同赛事内可比） |
| Understat | xG / xGA | 返回维护页 | 使用 football-data.co.uk 的 xG 列 |

主力缺阵在官方首发公布后（开球前约 1 小时）由"本场首发 vs 近期首发"推断，并小幅修正概率和风险；
在此之前为"未知"，重新运行并加 `--refresh` 即可更新。
没有已验证的免费来源的字段——伤停 / 停赛名单、预计首发、主教练、战术——固定为"未知"，
并在报告的"数据缺失提醒"中列出。只有 ESPN 已发布完整 11 人首发才记为官方首发。

赛事与数据源的映射在 `config/data_sources.yaml`，球队中英文名在 `config/team_aliases.yaml`。
表里没有的球队会通过"同一赛事同一比赛日的对阵配对"自动学习并记入
`data/cache/learned_aliases.json`；新增赛事需要在 `competitions` 里加一行映射。

## 模型

| 模型 | 说明 |
|---|---|
| Elo | 评级差 + 主场优势，Davidson 模型给出平局概率 |
| Poisson | 主客场攻防强度，时间衰减，向联赛均值收缩，可混合 xG |
| Dixon-Coles | 极大似然估计攻击 / 防守 / 主场优势，ρ 修正 0:0、1:0、0:1、1:1，时间衰减，L2 正则 |
| 市场 | 各赔率来源去水概率的加权平均：商业机构用幂次法去水、竞彩官方用比例法；水位越低的来源权重越高 |
| 集成 | 回测学习的对数线性池化（有赔率 / 无赔率的比赛各一套参数）；缺失模型自动剔除；历史样本少的模型按样本量降权 |

`model_confidence` 是模型一致性 / 覆盖度 / 结果明确度的组合指标，不是命中概率。

### 回测结果

`python scripts/backtest.py` 对每个联赛按周滚动拟合（只用被预测比赛之前的数据，市场概率只用
赛前赔率而非收盘赔率），在时间更早的 60% 预测上拟合集成参数，在更晚的 40% 上评估。
2026-11-27 运行的结果（16 个欧洲联赛 × 6 个赛季；留出集 9787 场）：

| 模型 | 准确率 | Brier | Log Loss | RPS |
|---|---:|---:|---:|---:|
| Elo | 49.8% | 0.604 | 1.009 | 0.208 |
| Poisson | 49.0% | 0.614 | 1.024 | 0.212 |
| Dixon-Coles | 49.9% | 0.607 | 1.015 | 0.209 |
| 市场去水 | 51.7% | 0.591 | 0.9904 | 0.202 |
| 集成（初始权重） | 50.9% | 0.599 | 1.003 | 0.206 |
| 集成（无赔率时：只用 Elo / Poisson / Dixon-Coles） | 49.9% | 0.604 | 1.009 | 0.208 |
| 集成（回测学习） | 51.7% | 0.591 | 0.9899 | 0.202 |

如实说明：

- **市场赔率是最强的单一信号，自建模型没有带来额外信息。** 学习到的参数里 Elo / Poisson / Dixon-Coles
  的指数都是 0，有赔率的比赛最终概率就是校准后的市场概率（指数 1.09，平局略上调）。
  之前的版本把市场权重限制在 0.60 以内，结果集成比纯市场概率更差；现在不再限制。
  自建模型只在没有赔率的比赛里起作用，那时准确率约低 2 个百分点。
- **单场准确率的上限就在 52% 左右**，任何模型都很难超过。所以提高任九命中率主要靠三件事：
  尽量让每场都有赔率（新增 500 彩票网的平均欧赔）、临近截止再跑一次（收盘赔率比早盘更准，
  `--refresh`）、以及下面的多票组合。
- 回测只覆盖有完整赔率历史的欧洲联赛；任九模拟是把留出集按时间每 14 场拼成一组，不是真实赛单。

学习到的参数写入 `config/model_weights.learned.yaml`，仅当它在留出集上不差于初始权重时才写入；
删除该文件即恢复初始权重（Elo 0.15 / Poisson 0.20 / Dixon-Coles 0.35 / 市场 0.30，线性池化）。
完整结果（含校准分箱、任九组合模拟）在 `data/history/backtest_summary.json`。

## 策略与多票组合

| 策略 | 默认注数上限 | 最多几张票 | 成本厌恶 λ | 特点 |
|---|---:|---:|---:|---|
| conservative | 8 注（16 元） | 3 | 0.30 | 明确场次为主，控制成本 |
| balanced | 24 注（48 元） | 4 | 0.25 | 兼顾概率与成本 |
| aggressive | 128 注（256 元） | 6 | 0.05 | 允许保留部分冷门，扩大覆盖 |

在 `config/settings.yaml` 的 `strategy.presets` 中调整，或用 `--budget` 临时指定预算。

**多票组合**：一张复式票覆盖的是各场选项相乘的"矩形"，其中不少组合概率极低（例如所有双选场同时
打出次选）。同样的注数拆成几张互不重叠的复式票——主票覆盖各场首选附近的组合，副票只在最不确定的
一两场换成次选——全中概率更高。报告"推荐 9 场"下面的**购买清单**逐票列出 9 场选择、注数、金额和
全中概率，并给出"同样预算只买一张复式"的对照；每张票单独购买，任意一张 9 场全中即中奖。
把某个策略的 `max_tickets` 设为 1 即恢复为只出一张复式。

回测模拟（699 组，每组 14 场，2026-11-27 运行）：

| 策略 | 平均注数 | 多票方案 9 场全中 | 同样预算只买一张复式 |
|---|---:|---:|---:|
| conservative | 8 | 22 / 699（3.1%） | 17 / 699（2.4%） |
| balanced | 24 | 47 / 699（6.7%） | 39 / 699（5.6%） |
| aggressive | 127（单张复式平均 110） | 139 / 699（19.9%） | 101 / 699（14.4%） |

这些是每期全中的概率，不是收益：大多数期不会中，命中热门结果时奖金也低。
多选提高覆盖率的同时成本同比例增加，并不代表提高最终收益。

## 复盘与防泄漏

- 每次生成报告都会把当时的全部输入与输出存为快照（`data/predictions/<期>/snapshot_*.json`），
  并写入历史库（`data/history/ren9.db`）。
- 复盘只评估**销售截止前**生成的最后一次预测；截止后生成的预测带 `post_deadline` 标记，默认不参与。
- ROI 使用官方公布的该期任九单注奖金（超过 1 万元按 20% 扣税）和方案真实成本；
  官方未公布奖金时不计算。
- `python scripts/review_results.py --summary` 查看累计指标；已复盘比赛达到 50 场后可用
  `--optimize-weights` 用实盘记录重新拟合权重。

## 定时运行

Skill 本身不内置定时器，由 Agent 平台或系统调度（cron / 任务计划程序）执行，例如：
上午 `generate_report.py --date today`；销售截止前 `generate_report.py --date today --refresh`
（更新赔率与首发）；开奖后 `review_results.py`。

## 可选：MCP 模式

```bash
pip install mcp
python mcp_server.py      # stdio
```

工具：`get_ren9_schedule`、`get_match_data`、`get_odds`、`predict_match`、`analyze_risk`、
`select_nine_matches`、`optimize_bet`、`generate_report`、`review_predictions`。
MCP 不是必需依赖，CLI 提供同样的功能。

## 目录结构

```text
SKILL.md            主 Skill 入口与调度规则
skills/             8 个子 Skill（skills/<名称>.md）
scripts/            CLI 入口
ren9/               核心库：schedule / teamdata / odds / predict / risk / selector / optimizer /
                    portfolio（多票组合）/ report / review / backtest / pipeline / store
providers/          数据源适配器：sporttery / espn / football_data / c500 / clubelo / understat
models/             elo / poisson / dixon_coles / ensemble / metrics
config/             settings / data_sources / model_weights / team_aliases
templates/          daily_report.md / match_report.md
data/               cache / history / predictions / results（运行时生成）
tests/              离线测试（pytest）
install.py          通用安装器
package.py          打包为上传用的 zip（两级目录；回测学习到的参数随包发布）
mcp_server.py       可选 MCP Server
```

## 多 Agent 兼容性

设计上只要求 Agent 能读取 `SKILL.md`、执行 Python、读取 stdout 的 JSON，核心代码不 import
任何平台 SDK（有测试检查）。已在 Claude Code 环境中用真实数据跑通全流程；
OpenClaw、Hermes、Codex、Cursor、Gemini CLI 尚未逐一实机验证，实际兼容性取决于各 Agent
对开放 Skill 规范的支持情况。

## 上游项目与许可

PRD 列出的参考项目（football-lottery-advisor、JetQiao/football-prediction-skill、
machina-sports/sports-skills、SportteryAPI、saowes/football-prediction-skill）在本版本中
**没有复制任何代码，也不是运行依赖**；Elo、Poisson、Dixon-Coles、赔率计算与数据适配器均为
依据公开算法文献与公开接口的独立实现。如果以后要适配或引入其中的代码，需要先逐个核实其许可证。

本项目以 MIT 许可发布（见 [LICENSE](LICENSE)）。数据来自第三方公开接口，
使用时请遵守各数据源的服务条款并控制请求频率（默认带本地缓存）。
