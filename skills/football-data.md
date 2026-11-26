---
name: football-data
description: >
  Collect team and match data from free public sources (ESPN, football-data.co.uk, ClubElo, Understat)
  for the matches of a Ren9 coupon or Jingcai list: standings, recent form, attack/defence, xG,
  home/away record, head-to-head, rest days, official lineups, Elo. Every field carries its source and timestamp.
license: MIT
compatibility: Python 3.10+
metadata:
  version: "1.0.0"
  parent: ren9-analyst
---

# football-data：足球数据采集

把赛单中的每场比赛对应到公开数据源并采集模型所需的基础数据。

## 执行

```bash
python ../scripts/fetch_team_data.py --date today
python ../scripts/fetch_team_data.py --date today --matches 3,8 --refresh
python ../scripts/fetch_team_data.py --date today --full      # 完整 JSON
```

输入参数与 ren9-schedule 相同（`--date` / `--period` / `--game-type` / `--refresh` / `--matches`）。
赛程未获取时会先自动获取。

## 输出（`--full`，每场一条）

```json
{
  "match_id": "ren9_26169_09", "seq": 9,
  "home_team": "布里斯托尔城", "away_team": "雷克斯汉姆", "competition": "英冠",
  "resolution": {"status": "resolved", "method": "alias", "confidence": 1.0,
                 "home_en": "Bristol City", "away_en": "Wrexham", "espn_event_id": "..."},
  "kickoff": {"value": "2026-11-25T03:45:00+08:00", "official": null, "espn": "...", "cross_validated": null},
  "status": "scheduled",
  "history": {"kind": "fd", "key": "E1", "home_matches": 62, "away_matches": 62, "has_xg": false,
              "source": ["football-data.co.uk-E1-2627"], "updated_at": "..."},
  "teams": {
    "home": {
      "standing": {"rank": 8, "points": 27, "played": 17, "source": "ESPN-standings-eng.2", "updated_at": "..."},
      "form": {"last5": {"results": ["W","D","L","W","W"], "scope": "全部赛事", "source": "...", "updated_at": "..."},
               "last10": {"results": ["..."], "scope": "仅本赛事数据集"}},
      "attack": {"goals_per_match": 1.45, "shots_per_match": 12.1, "xg_per_match": null, "source": ["..."]},
      "defence": {"conceded_per_match": 1.2, "xga_per_match": null},
      "venue": {"home_win_rate": 0.45, "away_win_rate": 0.3},
      "schedule": {"rest_days": 3, "matches_last_14d": 4},
      "lineup": {"official_lineup_published": false, "starters": null, "expected_lineup": null,
                 "injuries": null, "suspensions": null},
      "elo": null, "coach": null, "tactics": null
    }
  },
  "h2h": {"meetings": 2, "home_team_wins": 1, "draws": 0, "away_team_wins": 1},
  "importance": {"label": "联赛", "notes": []},
  "missing": ["伤停/停赛名单（无已验证的免费来源，状态未知）", "..."]
}
```

默认（不带 `--full`）输出每场的摘要：是否对应成功、英文名、开球时间、历史样本量、缺失项数量。

## 数据源

| 数据源 | 用途 | 说明 |
|---|---|---|
| ESPN 公开接口 | 赛程对应、开球时间、积分榜、近 5 场、官方首发、洲际赛事历史赛果 | 实测可用 |
| football-data.co.uk | 国内联赛历史赛果、射门、xG（部分联赛）、赛前赔率 | 实测可用 |
| ClubElo | 跨联赛可比的 Elo | 不可用时降级为由历史赛果自算 Elo |
| Understat | xG / xGA | 不可用时使用 football-data.co.uk 的 xG 列 |

实测状态用 `python ../scripts/check_sources.py` 查看。赛事 → 数据源的映射在
`config/data_sources.yaml` 的 `competitions`；球队中英文名在 `config/team_aliases.yaml`。

**球队对应方法**（`resolution.method`）：`alias` 双方别名命中；`fixture-pairing` 一方命中，
另一方由同一场比赛的对手推出（并自动记入学习表）；`kickoff-pairing` 同赛事同开球时间唯一比赛。
对应不上时 `status: "unresolved"`，该场只保留赛单信息。

## 规则与错误处理

- 每组数据都有 `source` 与 `updated_at`；历史数据走本地缓存（SQLite），不重复请求。
- **缺失就是缺失**：采集不到的字段为 `null` 并写入 `missing`，绝不当作 0。
- **首发**：只有 ESPN 已发布完整 11 人首发才标记 `official_lineup_published: true`；
  `expected_lineup` 恒为 `null`（不采集预测首发）。
- **伤停**：没有已验证的免费来源，`injuries` / `suspensions` 恒为 `null`（未知）；不要把传闻当成已确认伤停。
- **主力缺阵**（`lineup.absences`）：官方首发公布后，对比该队最近几场（ESPN 近 5 场）的首发推断——
  近期常规首发这次不在大名单记为缺阵，坐替补席按轮换部分计入；`absence_score` 为 0~1 的缺阵指数。
  首发未公布或近期首发记录不足时 `known: false`。缺阵原因（伤病 / 停赛 / 轮换）未知，转述时不要说成"受伤"。
- 历史数据只取比赛日之前的比赛（`ref_date`），避免数据泄漏。
- 单个数据源失败不会中断流程，失败原因记录在输出的 `notes` 中。
