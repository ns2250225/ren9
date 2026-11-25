---
name: ren9-schedule
description: >
  Fetch and validate the official 14-match coupon of China Sports Lottery traditional football
  (任选9场 / 胜负彩) for a date or period, or the Jingcai (竞彩足球) match list.
  Use before any Ren9 analysis, or when the user asks which matches are in this period.
license: MIT
compatibility: Python 3.10+
metadata:
  version: "1.0.0"
  parent: ren9-analyst
---

# ren9-schedule：14 场赛程获取

获取目标日期的传统足彩任选 9 场对应的 14 场官方赛单并校验；也可获取竞彩足球在售比赛
（两种玩法的数据结构彼此独立）。

## 执行

```bash
python ../scripts/fetch_schedule.py --date today
python ../scripts/fetch_schedule.py --period 26169
python ../scripts/fetch_schedule.py --manual matches.csv --period 26169
python ../scripts/fetch_schedule.py --date today --game-type jczq
```

## 输入

```json
{"date": "2026-11-24", "game_type": "ren9"}
```

| 参数 | 说明 |
|---|---|
| `--date` | today / yesterday / tomorrow / YYYY-MM-DD（北京时间），默认 today |
| `--game-type` | `ren9`（默认）或 `jczq` |
| `--period` | 期号；缺省时选择"销售窗口包含该日期且最先截止"的一期 |
| `--manual` | 手动赛单文件（CSV / 文本 / JSON） |
| `--refresh` | 忽略缓存 |

## 输出

```json
{
  "status": "ok",
  "date": "2026-11-24",
  "game_type": "ren9",
  "period": "26169",
  "expected_matches": 14,
  "sale_end": "2026-11-24 22:00:00",
  "sale_closed": false,
  "matches": [
    {
      "match_id": "ren9_26169_01", "period": "26169", "seq": 1,
      "home_team": "博德闪耀", "away_team": "LASK林茨", "competition": "欧冠",
      "date": "2026-11-25", "kickoff": "2026-11-25T01:45:00+08:00", "status": "scheduled",
      "official_ref_odds": {"home": 1.29, "draw": 5.3, "away": 5.7},
      "sources": [{"name": "中国体彩网-传统足彩赛单", "url": "...", "fetched_at": "..."}]
    }
  ],
  "complete": true,
  "official": true,
  "issues": [],
  "changed": false,
  "changes": [],
  "other_open_periods": [{"period": "26170", "sale_end": "2026-11-25 17:30:00", "matches": 14}],
  "source": [],
  "last_updated": "2026-11-24T15:22:16+08:00"
}
```

- `complete`：正好 14 场、编号 1–14 无重复无缺失、无重复对阵、球队信息完整。
- `issues`：校验问题（`count` / `duplicate_seq` / `missing_seq` / `duplicate_fixture` / `team_missing` …）。
- `changed` / `changes`：与上次保存的同期赛单相比的变更（球队、日期、状态）。
- `kickoff`：官方赛单只有比赛日期；开球时间由竞彩官方数据交叉补充，补不到时为 `null`
  （后续由 football-data 用 ESPN 补充并交叉验证）。

## 数据源

1. 中国体彩网传统足彩接口（`sporttery_zc`）：期号、14 场对阵、销售截止时间。
2. 备用官方接口：按期号查询；主接口失败时由最新开奖期号推算。
3. 中国体育彩票竞彩接口（`sporttery_jc`）：只用于给已在赛单中的比赛补充开球时间。

配置见 `config/data_sources.yaml`。

## 降级与错误处理

官方接口不可用时依次：备用官方接口 → 本地缓存（必须是同一期且仍在销售期内）→ 要求手动提供。

| 情况 | 输出 | 处理 |
|---|---|---|
| 没有在售期次 / 接口不可用 | `status: "no_schedule"`，`matches: []`，带 `action_required` | 请用户手动提供 14 场 |
| 赛单未通过校验 | `status: "invalid_schedule"`，`complete: false` | 说明 `issues`，不生成正式推荐 |
| 赛程变更 | `changed: true` | 提醒用户，下游阶段自动重算 |

**禁止**：用竞彩比赛或其他联赛比赛拼凑 / 补齐 14 场；自动生成虚假赛单。
