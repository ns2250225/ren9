"""足球数据采集：把官方赛单中的每场比赛对应到公开数据源，采集球队与比赛数据。

规则：每组数据都带 source 与 updated_at；采集不到的字段为 None 并记入 missing，
绝不用 0 或猜测值填充；只有 ESPN 已发布的完整首发才标记为官方首发。
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from ren9 import history
from ren9.common import data_sources, now_iso, parse_dt
from ren9.teams import TeamResolver, best_match, name_score
from providers import clubelo, espn, understat
from providers.base import ProviderError

ALWAYS_MISSING = [
    "伤停/停赛名单（无已验证的免费来源，状态未知）",
    "预计首发（不采集传闻；仅在官方首发公布后记录）",
    "主教练与战术信息（无已验证的免费来源）",
]


def _beijing_date(m: dict) -> str | None:
    if m.get("kickoff"):
        dt = parse_dt(m["kickoff"])
        if dt:
            return dt.astimezone(timezone(timedelta(hours=8))).date().isoformat()
    return m.get("date")


def ref_date(m: dict, espn_kickoff_utc: str | None = None) -> date:
    """模型使用的历史数据截止日（UTC 比赛日）：只用该日之前的比赛。"""
    for k in (espn_kickoff_utc, m.get("kickoff")):
        dt = parse_dt(k) if k else None
        if dt:
            return dt.astimezone(timezone.utc).date()
    d = m.get("date")
    if d:
        return date.fromisoformat(d) - timedelta(days=1)   # 北京时间凌晨开球 = UTC 前一天，保守处理
    return datetime.now(timezone.utc).date()


class Collector:
    def __init__(self, refresh: bool = False) -> None:
        self.refresh = refresh
        self.resolver = TeamResolver()
        self.comps = data_sources().get("competitions", {})
        self._events: dict[tuple, list] = {}
        self._event_src: dict[tuple, dict] = {}
        self._standings: dict[str, tuple | None] = {}
        self._elo: dict[str, tuple | None] = {}
        self._understat: dict[tuple, tuple | None] = {}
        self.notes: list[str] = []

    # ----- ESPN 赛程匹配 -----
    def events_for(self, slug: str, day: str) -> list[dict]:
        out = []
        d0 = date.fromisoformat(day)
        for d in (d0, d0 - timedelta(days=1)):       # 北京时间比赛日与其前一日（UTC/美东日期）
            key = (slug, d.strftime("%Y%m%d"))
            if key not in self._events:
                try:
                    ev, src = espn.scoreboard(slug, key[1], ttl=600, refresh=self.refresh)
                    if slug == "all":            # 未知赛事：没有联赛 slug，后续不取积分榜/详情
                        for e in ev:
                            e["league"] = None
                    self._events[key], self._event_src[key] = ev, src
                except ProviderError as exc:
                    self._events[key] = []
                    self.notes.append(f"ESPN 赛程不可用 ({slug} {key[1]}): {exc}")
            out.extend(self._events[key])
        seen, uniq = set(), []
        for e in out:
            if e["event_id"] not in seen:
                seen.add(e["event_id"])
                uniq.append(e)
        return uniq

    def _side_score(self, zh: str, team: dict) -> float:
        names = self.resolver.names_for(zh)
        if not names:
            return 0.0
        return max(name_score(n, x) for n in names for x in team["names"])

    def resolve(self, matches: list[dict]) -> dict[str, dict]:
        """赛单比赛 → ESPN 比赛。三级：双方别名命中 / 单方命中+对阵配对 / 同赛事同开球时间唯一。"""
        result: dict[str, dict] = {}
        claimed: set[str] = set()
        cands: dict[str, list] = {}
        for m in matches:
            comp = self.comps.get(m.get("competition") or "")
            day = _beijing_date(m)
            if not day:
                cands[m["match_id"]] = []
                continue
            slug = (comp or {}).get("espn")
            cands[m["match_id"]] = self.events_for(slug or "all", day)

        def accept(m, e, method, conf):
            claimed.add(e["event_id"])
            result[m["match_id"]] = {"event": e, "method": method, "confidence": conf}

        # 第 1 轮：双方别名都命中
        for m in matches:
            best = None
            for e in cands[m["match_id"]]:
                if e["event_id"] in claimed:
                    continue
                sh, sa = self._side_score(m["home_team"], e["home"]), self._side_score(m["away_team"], e["away"])
                if sh >= 0.8 and sa >= 0.8 and (best is None or sh + sa > best[0]):
                    best = (sh + sa, e)
            if best:
                accept(m, best[1], "alias", 1.0)
        # 第 2 轮：只有一方命中 → 同一场比赛的对手即另一方（仅在赛事已知时使用）
        for m in matches:
            if m["match_id"] in result or not (self.comps.get(m.get("competition") or "") or {}).get("espn"):
                continue
            best = None
            for e in cands[m["match_id"]]:
                if e["event_id"] in claimed:
                    continue
                sh, sa = self._side_score(m["home_team"], e["home"]), self._side_score(m["away_team"], e["away"])
                s = max(sh, sa)
                if s >= 0.9 and (best is None or s > best[0]):
                    best = (s, e, sh >= sa)
            if best:
                _, e, home_known = best
                accept(m, e, "fixture-pairing", 0.8)
                if home_known:
                    self.resolver.learn(m["away_team"], e["away"]["name"], "fixture-pairing")
                else:
                    self.resolver.learn(m["home_team"], e["home"]["name"], "fixture-pairing")
        # 第 3 轮：官方开球时间 + 同赛事内唯一未匹配比赛
        for m in matches:
            if m["match_id"] in result or not m.get("kickoff"):
                continue
            if not (self.comps.get(m.get("competition") or "") or {}).get("espn"):
                continue
            ko = parse_dt(m["kickoff"])
            same = []
            for e in cands[m["match_id"]]:
                eko = parse_dt(e.get("kickoff_utc"))
                if e["event_id"] not in claimed and ko and eko and abs((eko - ko).total_seconds()) <= 300:
                    same.append(e)
            # 同一开球时间的未匹配赛单比赛也必须只有这一场，否则无法区分
            rivals = [
                x for x in matches
                if x["match_id"] not in result and x.get("competition") == m.get("competition")
                and x.get("kickoff") == m.get("kickoff")
            ]
            if len(same) == 1 and len(rivals) == 1:
                e = same[0]
                accept(m, e, "kickoff-pairing", 0.6)
                self.resolver.learn(m["home_team"], e["home"]["name"], "kickoff-pairing")
                self.resolver.learn(m["away_team"], e["away"]["name"], "kickoff-pairing")
        return result

    # ----- 各类数据 -----
    def standings(self, slug: str):
        if slug not in self._standings:
            try:
                self._standings[slug] = espn.standings(slug, refresh=self.refresh)
            except ProviderError as exc:
                self._standings[slug] = None
                self.notes.append(f"ESPN 积分榜不可用 ({slug}): {exc}")
        return self._standings[slug]

    def clubelo(self, day: str):
        if day not in self._elo:
            try:
                self._elo[day] = clubelo.ratings(day)
            except ProviderError as exc:
                self._elo[day] = None
                self.notes.append(f"ClubElo 不可用，Elo 降级为由历史赛果自算: {exc}")
        return self._elo[day]

    def understat_xg(self, league: str, ref: date):
        key = (league, ref.isoformat())
        if key not in self._understat:
            try:
                self._understat[key] = understat.league_xg(league, ref)
            except ProviderError as exc:
                self._understat[key] = None
                self.notes.append(f"Understat 不可用 ({league})，xG 改用 football-data.co.uk: {exc}")
        return self._understat[key]

    # ----- 单场 -----
    def collect_match(self, m: dict, res: dict | None) -> dict:
        comp_name = m.get("competition") or ""
        comp = self.comps.get(comp_name)
        missing = list(ALWAYS_MISSING)
        ev = res["event"] if res else None
        ref = ref_date(m, ev["kickoff_utc"] if ev else None)
        out: dict = {
            "match_id": m["match_id"],
            "seq": m.get("seq"),
            "home_team": m["home_team"],
            "away_team": m["away_team"],
            "competition": comp_name,
            "competition_type": (comp or {}).get("type"),
            "ref_date": ref.isoformat(),
            "collected_at": now_iso(),
        }
        if comp is None:
            missing.append(f"赛事『{comp_name}』未配置数据源映射（config/data_sources.yaml）")

        # 对应关系与开球时间
        if ev:
            out["resolution"] = {
                "status": "resolved", "method": res["method"], "confidence": res["confidence"],
                "espn_event_id": ev["event_id"], "league": ev["league"],
                "home_en": ev["home"]["name"], "away_en": ev["away"]["name"],
                "source": "ESPN", "updated_at": now_iso(),
            }
            ko_espn = espn.kickoff_beijing(ev["kickoff_utc"])
            ko_off = m.get("kickoff")
            agree = None
            if ko_off and ko_espn:
                agree = abs((parse_dt(ko_off) - parse_dt(ko_espn)).total_seconds()) <= 900
            out["kickoff"] = {
                "value": ko_off or ko_espn,
                "official": ko_off, "espn": ko_espn,
                "cross_validated": agree,
                "source": "竞彩官方" if ko_off else "ESPN",
            }
            if agree is False:
                missing.append(f"开球时间不一致：官方 {ko_off}，ESPN {ko_espn}")
            st = ev.get("status") or ""
            out["status"] = (
                "postponed" if "POSTPONED" in st else "cancelled" if "CANCEL" in st
                else "finished" if ev.get("completed") else "live" if ev.get("state") == "in" else "scheduled"
            )
        else:
            out["resolution"] = {"status": "unresolved", "method": None, "confidence": 0.0,
                                 "home_en": None, "away_en": None}
            out["kickoff"] = {"value": m.get("kickoff"), "official": m.get("kickoff"), "espn": None,
                              "cross_validated": None, "source": "竞彩官方" if m.get("kickoff") else None}
            out["status"] = m.get("status") or "scheduled"
            missing.append("未能在公开数据源中找到对应比赛（球队名未映射），球队数据缺失")
            if not m.get("kickoff"):
                missing.append("开球时间未知（官方赛单只提供比赛日期）")

        names = {
            side: (self.resolver.names_for(m[f"{side}_team"]) + ([ev[side]["name"]] if ev else []))
            for side in ("home", "away")
        }

        # 历史数据集
        ds_used, loc = None, {}
        for ds in history.load(comp, ref, refresh=False):
            fd_names = {s: (self.resolver.names_for(m[f"{s}_team"], "fd") if ds["kind"] == "fd" else []) + names[s]
                        for s in ("home", "away")}
            h, a = history.locate(ds, fd_names["home"]), history.locate(ds, fd_names["away"])
            if h and a and h != a:
                ds_used, loc = ds, {"home": h, "away": a}
                break
        if ds_used:
            out["history"] = {
                "kind": ds_used["kind"], "key": ds_used["key"], "home_name": loc["home"], "away_name": loc["away"],
                "total_matches": len(ds_used["rows"]), "has_xg": ds_used["has_xg"],
                "home_matches": len(history.team_rows(ds_used, loc["home"])),
                "away_matches": len(history.team_rows(ds_used, loc["away"])),
                "source": list(dict.fromkeys(s["name"] for s in ds_used["sources"])),
                "updated_at": ds_used["updated_at"],
            }
            out["h2h"] = {**history.head_to_head(ds_used, loc["home"], loc["away"]),
                          "window": "数据集覆盖期内", "source": out["history"]["source"],
                          "updated_at": ds_used["updated_at"]}
        else:
            out["history"] = None
            out["h2h"] = None
            missing.append("两队没有共同的历史比赛数据集（Poisson / Dixon-Coles 不可用）")

        # 详情：近 5 场、首发
        summ, summ_src = None, None
        if ev and ev.get("league"):
            try:
                summ, summ_src = espn.summary(ev["league"], ev["event_id"], refresh=self.refresh)
            except ProviderError as exc:
                self.notes.append(f"ESPN 比赛详情不可用 ({m['home_team']} vs {m['away_team']}): {exc}")
        table = self.standings(ev["league"]) if ev and ev.get("league") and (comp or {}).get("type") in ("league", "continental") else None
        elo_tab = self.clubelo(ref.isoformat())
        us = self.understat_xg(comp["understat"], ref) if comp and comp.get("understat") else None
        ko = parse_dt(out["kickoff"]["value"])

        out["teams"] = {}
        for side in ("home", "away"):
            zh = m[f"{side}_team"]
            t: dict = {"name": zh, "name_en": ev[side]["name"] if ev else None,
                       "espn_id": ev[side]["id"] if ev else None, "league": comp_name or None,
                       "country": None, "coach": None, "tactics": None}
            # 积分榜
            t["standing"] = None
            if table and ev:
                tab, src = table
                key = ev[side]["name"] if ev[side]["name"] in tab else best_match(ev[side]["names"], list(tab))
                if key:
                    row = tab[key]
                    t["standing"] = {k: row[k] for k in ("rank", "points", "played", "wins", "draws", "losses", "gf", "ga", "teams_total")}
                    t["standing"].update(source=src["name"], updated_at=src["fetched_at"])
            # 近期表现
            st = history.team_stats(ds_used, loc[side]) if ds_used else {"matches": 0}
            last5 = None
            if summ and ev and summ["last_five"].get(ev[side]["id"]):
                games = [g for g in summ["last_five"][ev[side]["id"]] if g.get("result") in ("W", "D", "L")]
                last5 = {"results": [g["result"] for g in games[:5]], "games": games[:5],
                         "scope": "全部赛事", "source": summ_src["name"], "updated_at": summ_src["fetched_at"]}
            elif st.get("last5"):
                last5 = {"results": st["last5"], "scope": "仅本赛事数据集", "source": out["history"]["source"],
                         "updated_at": out["history"]["updated_at"]}
            t["form"] = {
                "last5": last5,
                "last10": ({"results": st["last10"], "scope": "仅本赛事数据集", "source": out["history"]["source"],
                            "updated_at": out["history"]["updated_at"]} if st.get("last10") else None),
            }
            # 攻防
            if st.get("matches"):
                t["attack"] = {"goals_per_match": st["goals_per_match"], "shots_per_match": st["shots_per_match"],
                               "xg_per_match": st["xg_per_match"], "sample": st["sample"],
                               "source": out["history"]["source"], "updated_at": out["history"]["updated_at"]}
                t["defence"] = {"conceded_per_match": st["conceded_per_match"], "xga_per_match": st["xga_per_match"],
                                "sample": st["sample"], "source": out["history"]["source"],
                                "updated_at": out["history"]["updated_at"]}
                t["venue"] = {"home_win_rate": st["home_win_rate"], "home_matches": st["home_matches"],
                              "away_win_rate": st["away_win_rate"], "away_matches": st["away_matches"],
                              "source": out["history"]["source"], "updated_at": out["history"]["updated_at"]}
            else:
                t["attack"] = t["defence"] = t["venue"] = None
            if us and t["name_en"]:
                tab_us, src_us = us
                k = best_match(names[side], list(tab_us))
                if k:
                    t["xg_understat"] = {**tab_us[k], "source": src_us["name"], "updated_at": src_us["fetched_at"]}
            # 赛程压力
            t["schedule"] = None
            dates = []
            if last5 and last5.get("games"):
                dates = [g["date"] for g in last5["games"] if g.get("date")]
            elif st.get("last_match_date"):
                dates = [st["last_match_date"]]
            if dates and ko:
                kd = ko.astimezone(timezone.utc).date()
                past = sorted((date.fromisoformat(d) for d in dates if date.fromisoformat(d) < kd), reverse=True)
                if past:
                    t["schedule"] = {
                        "last_match_date": past[0].isoformat(),
                        "rest_days": (kd - past[0]).days,
                        "matches_last_14d": (sum(1 for d in past if (kd - d).days <= 14) if last5 and last5.get("games") else None),
                        "scope": last5["scope"] if last5 else "仅本赛事数据集",
                        "source": (last5 or {}).get("source") or out["history"]["source"],
                        "updated_at": now_iso(),
                    }
            # 首发 / 伤停
            lu = (summ or {}).get("lineups", {}).get(ev[side]["id"]) if ev else None
            t["lineup"] = {
                "official_lineup_published": bool(lu and lu["official"]),
                "formation": lu["formation"] if lu and lu["official"] else None,
                "starters": lu["starters"] if lu and lu["official"] else None,
                "expected_lineup": None,
                "injuries": None,
                "suspensions": None,
                "source": summ_src["name"] if summ_src else None,
                "updated_at": summ_src["fetched_at"] if summ_src else None,
            }
            # Elo
            t["elo"] = None
            if elo_tab:
                tab_e, src_e = elo_tab
                k = best_match(self.resolver.names_for(zh, "elo") + names[side], list(tab_e), threshold=0.9)
                if k:
                    t["elo"] = {"value": tab_e[k]["elo"], "comparable": "cross-league", "source": src_e["name"],
                                "updated_at": src_e["fetched_at"]}
                    t["country"] = tab_e[k].get("country")
            # 缺失登记
            label = "主队" if side == "home" else "客队"
            if t["standing"] is None and (comp or {}).get("type") in ("league", "continental"):
                missing.append(f"{label}联赛排名/积分")
            if t["form"]["last5"] is None:
                missing.append(f"{label}近 5 场战绩")
            if t["form"]["last10"] is None:
                missing.append(f"{label}近 10 场战绩")
            if t["attack"] is None:
                missing.append(f"{label}进攻/防守/主客场统计")
            elif t["attack"]["xg_per_match"] is None and "xg_understat" not in t:
                missing.append(f"{label} xG / xGA")
            if t["schedule"] is None:
                missing.append(f"{label}休息天数")
            out["teams"][side] = t

        # ESPN 提供的商业机构赔率（原样保留，由赔率模块分析）
        eo, seen_prov = [], set()
        if ev:
            for o in (ev.get("odds") or []) + ((summ or {}).get("odds") or []):
                if o["provider"] not in seen_prov:
                    seen_prov.add(o["provider"])
                    eo.append({**o, "fetched_at": now_iso()})
        out["espn_odds"] = eo

        # 赛事重要性（由积分榜推导；推导不了则为 None）
        out["importance"] = _importance(out, comp)
        out["missing"] = missing
        return out


def _importance(td: dict, comp: dict | None) -> dict | None:
    ctype = (comp or {}).get("type")
    if ctype == "cup":
        return {"label": "杯赛", "notes": ["杯赛淘汰制，轮换与加时/点球增加不确定性"], "derived_from": "赛事类型"}
    if ctype == "continental":
        return {"label": "洲际赛事", "notes": ["洲际赛事，跨联赛实力对比不确定性较高"], "derived_from": "赛事类型"}
    if ctype == "international":
        return {"label": "国家队赛事", "notes": ["国家队比赛样本少，模型数据有限"], "derived_from": "赛事类型"}
    if ctype != "league":
        return None
    notes = []
    for side, zh in (("home", "主队"), ("away", "客队")):
        s = td["teams"][side].get("standing")
        if not s or not s.get("teams_total") or s.get("played") is None or s.get("rank") is None:
            continue
        rounds = 2 * (s["teams_total"] - 1)
        stage = s["played"] / rounds if rounds else 0
        if stage >= 0.6 and s["rank"] <= 2:
            notes.append(f"{zh}处于争冠区（第 {s['rank']} 名，赛季进度 {stage:.0%}）")
        elif stage >= 0.5 and s["rank"] > s["teams_total"] - 4:
            notes.append(f"{zh}处于保级区（第 {s['rank']} 名，赛季进度 {stage:.0%}）")
    if any(td["teams"][s].get("standing") for s in ("home", "away")):
        return {"label": "联赛", "notes": notes, "derived_from": "ESPN 积分榜"}
    return None


def collect(schedule: dict, refresh: bool = False, only_seqs: list[int] | None = None,
            previous: dict | None = None) -> dict:
    """采集整期比赛的数据。only_seqs 非空时只重新采集指定场次，其余沿用 previous。"""
    c = Collector(refresh=refresh)
    matches = schedule.get("matches") or []
    prev = {x["match_id"]: x for x in (previous or {}).get("matches", [])}
    todo = [m for m in matches if not only_seqs or m.get("seq") in only_seqs or m["match_id"] not in prev]
    resolved = c.resolve(todo)
    out_matches = []
    for m in matches:
        if m in todo:
            out_matches.append(c.collect_match(m, resolved.get(m["match_id"])))
        else:
            out_matches.append(prev[m["match_id"]])
    return {
        "date": schedule.get("date"),
        "game_type": schedule.get("game_type"),
        "period": schedule.get("period"),
        "generated_at": now_iso(),
        "matches": out_matches,
        "resolved": sum(1 for x in out_matches if x["resolution"]["status"] == "resolved"),
        "total": len(out_matches),
        "notes": sorted(set(c.notes)),
    }
