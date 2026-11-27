"""报告生成：Markdown（模板在 templates/）+ JSON。报告中的所有数字都来自上游计算结果。"""
from __future__ import annotations

import re

from ren9.common import (OUTCOME_ZH, OUTCOMES, RISK_ZH, TEMPLATE_DIR, data_dir, now_iso, picks_zh, read_json)
from ren9.lineup import describe as describe_absences
from ren9.risk import FACTOR_ZH

STRATEGY_ZH ={"conservative": "保守", "balanced": "均衡", "aggressive": "激进"}
MODEL_ZH = {"elo": "Elo", "poisson": "Poisson", "dixon_coles": "Dixon-Coles", "market": "市场去水"}
FORM_ZH = {"W": "胜", "D": "平", "L": "负"}


def render(template: str, ctx: dict) -> str:
    text = (TEMPLATE_DIR / template).read_text(encoding="utf-8")
    text = re.sub(r"\{\{\s*(\w+)\s*\}\}", lambda m: str(ctx.get(m.group(1), "")), text)
    return re.sub(r"\n{3,}", "\n\n", text).strip() + "\n"


def pct(x: float | None, digits: int = 0) -> str:
    return "—" if x is None else f"{x * 100:.{digits}f}%"


def table(headers: list[str], rows: list[list], align: str | None = None) -> str:
    if not rows:
        return "（无）"
    al = align or "l" * len(headers)
    sep = ["---:" if a == "r" else ":---:" if a == "c" else "---" for a in al]
    out = ["| " + " | ".join(headers) + " |", "|" + "|".join(sep) + "|"]
    for r in rows:
        out.append("| " + " | ".join("—" if c is None else str(c).replace("|", "/") for c in r) + " |")
    return "\n".join(out)


def _hm(kickoff: str | None) -> str:
    return kickoff[5:16].replace("T", " ") if kickoff else "—"


def history_eval_md() -> str:
    """模型历史评估：真实预测的复盘汇总 + 时间切分回测结果。"""
    from ren9 import review

    parts = []
    agg = review.aggregate("ren9")
    if agg["period_count"] and agg["metrics"]:
        m = agg["metrics"]
        parts.append(f"**实盘复盘**（{agg['period_count']} 期，{m['n']} 场）：准确率 {pct(m['accuracy'], 1)}，"
                     f"Brier {m['brier']}，Log Loss {m['log_loss']}，RPS {m['rps']}。")
        rows = [[STRATEGY_ZH.get(k, k), s["periods"], s["all_nine_hits"], pct(s["selection_hit_rate"], 1),
                 f"{s['cost']:.0f}", f"{s['payout_after_tax']:.0f}", pct(s["roi"], 1) if s["roi"] is not None else "—"]
                for k, s in agg["strategies"].items()]
        if rows:
            parts.append(table(["策略", "期数", "9场全中", "单场覆盖率", "累计成本(元)", "累计税后奖金(元)", "ROI"],
                               rows, "lrrrrrr"))
        parts.append(f"> {agg['note']}")
    else:
        parts.append("**实盘复盘**：暂无已复盘的历史预测（开奖后运行 `python scripts/review_results.py`）。")
    bt = read_json(data_dir() / "history" / "backtest_summary.json")
    if bt and bt.get("holdout"):
        parts.append(f"**时间切分回测**（{', '.join(bt['leagues'])}；{bt['test_range'][0]} ~ {bt['test_range'][1]}；"
                     f"留出集 {bt['holdout']['ensemble']['n']} 场，训练数据始终早于被预测比赛）：")
        rows = []
        for k in ("elo", "poisson", "dixon_coles", "market", "ensemble_initial", "ensemble_no_market", "ensemble"):
            v = bt["holdout"].get(k)
            if v:
                name = {"ensemble": "集成（回测学习）", "ensemble_initial": "集成（初始权重）",
                        "ensemble_no_market": "集成（无赔率时）"}.get(k, MODEL_ZH.get(k, k))
                rows.append([name, v["n"], pct(v["accuracy"], 1), v["brier"], v["log_loss"], v["rps"]])
        parts.append(table(["模型", "场数", "准确率", "Brier", "Log Loss", "RPS"], rows, "lrrrrr"))
        if bt.get("top_pick_buckets"):
            parts.append("首选概率区间的实际命中率（留出集，集成模型）：")
            parts.append(table(["概率区间", "场数", "平均预测", "实际命中"],
                               [[b["range"], b["count"], pct(b["mean_predicted"], 1), pct(b["hit_rate"], 1)]
                                for b in bt["top_pick_buckets"]], "lrrr"))
        if bt.get("ren9_simulation"):
            s = bt["ren9_simulation"]
            parts.append(f"任九组合历史模拟（留出集按时间每 14 场组成一组，共 {s['coupons']} 组）："
                         + "；".join(f"{STRATEGY_ZH.get(k, k)} 9 场全中 {v['hits']}/{s['coupons']}"
                                    f"（平均 {v['avg_bets']:.1f} 注"
                                    + (f"、{v['avg_tickets']:.1f} 张票；同样预算只买一张复式为 {v['compound_hits']}/{s['coupons']}"
                                       if v.get("compound_hits") is not None else "") + "）"
                                    for k, v in s["strategies"].items())
                         + "。模拟组合不是官方赛单，没有真实奖金，因此不计算 ROI。")
    else:
        parts.append("**时间切分回测**：尚未运行（`python scripts/backtest.py`）。当前集成权重为初始配置，未经回测校准。")
    return "\n\n".join(parts)


def _alerts(st: dict) -> str:
    sch = st["schedule"]
    out = []
    if not sch.get("official", True):
        out.append("> ⚠️ 赛单由用户手动提供，未经官方接口验证。")
    if sch.get("sale_closed"):
        out.append("> ⚠️ 本期已过销售截止时间；本报告为截止后生成，不计入复盘统计。")
    if sch.get("changed"):
        ch = "；".join(f"第{c['seq']}场 {c['field']}: {c['old']} → {c['new']}" for c in sch["changes"][:6])
        out.append(f"> ⚠️ 赛程较上次获取发生变更：{ch}")
    for i in sch.get("issues", []):
        out.append(f"> {'❌' if i['level'] == 'error' else '⚠️'} 赛程校验：{i['message']}")
    for n in sch.get("notes", []):
        out.append(f"> ℹ️ {n}")
    if sch.get("other_open_periods"):
        o = "、".join(f"{x['period']} 期（截止 {x['sale_end']}）" for x in sch["other_open_periods"])
        out.append(f"> ℹ️ 同日在售的其他期次：{o}。可用 `--period` 指定。")
    return "\n".join(out)


def _sources_table(st: dict) -> str:
    seen, rows = set(), []

    def add(name, when, note=""):
        if name and (name, note) not in seen:
            seen.add((name, note))
            rows.append([name, when or "—", note])

    for s in st["schedule"].get("source", []):
        add(s.get("name"), s.get("fetched_at"), "官方赛单" + ("（过期缓存）" if s.get("stale") else ""))
    for m in (st.get("team_data") or {}).get("matches", []):
        if m.get("history"):
            for n in m["history"]["source"]:
                add(n, m["history"]["updated_at"], "历史赛果 / 攻防统计")
        for side in ("home", "away"):
            t = m["teams"][side]
            for grp, note in (("standing", "积分榜"), ("elo", "Elo 评级")):
                if t.get(grp):
                    add(t[grp]["source"], t[grp]["updated_at"], note)
            f5 = (t.get("form") or {}).get("last5")
            if f5 and f5.get("scope") == "全部赛事":
                add(f5["source"], f5["updated_at"], "近 5 场 / 首发状态")
    for m in (st.get("odds") or {}).get("matches", []):
        for s in m["sources"]:
            add(s["label"], s.get("fetched_at"), "胜平负赔率（" + ("官方" if s["kind"] == "official" else "商业机构") + "）")
        if m.get("jc_handicap"):
            add(m["jc_handicap"]["label"], m["jc_handicap"].get("fetched_at"), "让球胜平负赔率（官方）")
    return table(["数据源", "抓取时间", "用途"], rows)


def _missing_md(st: dict) -> str:
    td = (st.get("team_data") or {}).get("matches", [])
    preds = {p["match_id"]: p for p in (st.get("probabilities") or {}).get("matches", [])}
    common = None
    for m in td:
        s = set(m.get("missing", []))
        common = s if common is None else common & s
    out = []
    if common:
        out.append("所有场次共同缺失：" + "；".join(sorted(common)) + "。")
    for m in td:
        own = [x for x in m.get("missing", []) if x not in (common or set())]
        p = preds.get(m["match_id"], {})
        for k, why in (p.get("models_unavailable") or {}).items():
            own.append(f"{MODEL_ZH.get(k, k)} 模型未参与：{why}")
        if own:
            out.append(f"- 第 {m['seq']} 场 {m['home_team']} vs {m['away_team']}：" + "；".join(own))
    for src in ("team_data", "odds"):
        for n in (st.get(src) or {}).get("notes", []):
            out.append(f"- 数据源提示：{n}")
    out.append("\n缺失的数据不会被当作 0 或用猜测值代替；对应模型不参与集成，对应风险因子标记为未知。")
    return "\n".join(out)


def _match_notes(st: dict, plan: dict | None, selection: dict | None) -> str:
    preds = {p["match_id"]: p for p in st["probabilities"]["matches"]}
    risks = {r["match_id"]: r for r in st["risk"]["matches"]}
    sel_rows = {r["match_id"]: r for r in (selection or {}).get("matches", [])}
    picks = {s["seq"]: s for s in (plan or {}).get("selections", [])}
    out = []
    for m in st["schedule"]["matches"]:
        p, r = preds.get(m["match_id"], {}), risks.get(m["match_id"], {})
        head = f"**第 {m['seq']} 场 {m['home_team']} vs {m['away_team']}**（{m.get('competition') or '赛事未知'}）"
        lines = [head]
        if p.get("status") != "ok":
            lines.append(f"- 无预测：{p.get('reason', '数据缺失')}")
        else:
            mp = "；".join(f"{MODEL_ZH[k]} {pct(v['win'])}/{pct(v['draw'])}/{pct(v['loss'])}"
                          for k, v in p["model_probabilities"].items())
            lines.append(f"- 各模型（胜/平/负）：{mp}")
            wu = "、".join(f"{MODEL_ZH[k]} {pct(v)}" for k, v in p.get("weights_used", {}).items())
            small = "（历史样本少的模型已降权）" if any(v < 1 for v in p.get("sample_reliability", {}).values()) else ""
            lines.append(f"- 集成权重：{wu}{small}")
            eg = p.get("expected_goals")
            extra = f"；预期进球 {eg['home']} : {eg['away']}" if eg else ""
            lines.append(f"- 模型置信度 {p['model_confidence']:.2f}，数据质量 {p['data_quality']:.2f}{extra}")
        lines.append(f"- 风险 {RISK_ZH.get(r.get('risk_level'), '—')}：" + "；".join(r.get("reasons", [])))
        if r.get("unknown_factors"):
            lines.append(f"- 未知风险因子：{'、'.join(r['unknown_factors'])}")
        sr = sel_rows.get(m["match_id"])
        if sr:
            tag = f"入选，{picks[m['seq']]['type']} {picks[m['seq']]['picks_zh']}" if m["seq"] in picks else "排除"
            lines.append(f"- 筛选：{tag}。{sr.get('reason') or ''}")
        out.append("\n".join(lines))
    return "\n\n".join(out)


def _tickets_md(plan: dict) -> str:
    """购买清单：每张复式票的 9 场选择、注数、金额与全中概率。"""
    tickets = plan.get("tickets") or []
    if not tickets:
        return ""
    rows = [[f"票 {t['no']}", t["picks"], t["bet_count"], f"{t['cost']:.0f}", f"{t['hit_probability'] * 100:.3f}%"]
            for t in tickets]
    head = ["票号", "9 场选择（场次:结果）", "注数", "金额(元)", "全中概率"]
    alt = plan.get("compound_alternative")
    if len(tickets) == 1 or not alt:
        return "\n\n".join([
            f"**购买清单（{len(tickets)} 张复式票，共 {plan['bet_count']} 注 {plan['total_cost']:.0f} 元）**",
            table(head, rows, "llrrr")])
    rows.append(["合计", f"{len(tickets)} 张票", plan["bet_count"], f"{plan['total_cost']:.0f}",
                 f"{plan['hit_probability'] * 100:.3f}%"])
    gain = plan["hit_probability"] / alt["hit_probability"] - 1 if alt["hit_probability"] else 0
    return "\n\n".join([
        "**购买清单**（两种买法二选一，不要同时买）",
        f"**方案 A：单张复式**（就是上表\"推荐\"一列）——{alt['bet_count']} 注 {alt['total_cost']:.0f} 元，"
        f"全中概率 {alt['hit_probability'] * 100:.3f}%",
        table(head, [["票 1", alt["picks"], alt["bet_count"], f"{alt['total_cost']:.0f}",
                      f"{alt['hit_probability'] * 100:.3f}%"]], "llrrr"),
        f"**方案 B：多票组合**（{len(tickets)} 张复式票，共 {plan['bet_count']} 注 {plan['total_cost']:.0f} 元）"
        f"——全中概率 {plan['hit_probability'] * 100:.3f}%，比方案 A 相对提高 {gain:.0%}",
        table(head, rows, "llrrr"),
        "> 方案 B 把方案 A 里概率最低的组合（例如几个多选场同时打出次选）换成概率更高的组合，所以个别场次的选择"
        "和方案 A 不同，见上表\"多票覆盖\"一列。每张票单独购买（都是普通复式票），各票覆盖的组合互不重叠，"
        "任意一张 9 场全中即中奖，所以总全中概率是各票之和。本报告的方案统计与赛后复盘按方案 B 计算。",
    ])


def _plan_stats(plan: dict) -> str:
    if not plan or plan.get("status") != "ok":
        return f"未生成正式方案：{(plan or {}).get('message', '')}"
    multi = len(plan.get("tickets") or []) > 1
    return "\n".join([
        "```text",
        f"选择场次：{len(plan['selections'])}场",
        f"单选：{plan['singles']}场",
        f"双选：{plan['doubles']}场",
        f"全选：{plan['triples']}场",
        *(["（以上为方案 A 单张复式的配置）", "",
           f"购买方式：方案 B 多票组合，{len(plan['tickets'])} 张复式票"] if multi else ["", "购买方式：1 张复式票"]),
        f"组合数量：{plan['bet_count']}注",
        f"每注金额：{plan['unit_price']}元",
        f"理论金额：{plan['total_cost']}元（预算上限 {plan['budget']} 元，{plan['budget_source']}）",
        "",
        f"模型估计 9 场全中概率：{plan['hit_probability'] * 100:.3f}%（全部单选时为 {plan['all_singles_hit_probability'] * 100:.3f}%）",
        "```",
        "",
        f"> {plan['note']} 全中概率是假设各场独立时的模型估计值，不是中奖保证。",
    ])


def build_daily(st: dict, strategy: str) -> tuple[str, dict]:
    sch = st["schedule"]
    selections, plans = st.get("selections") or {}, st.get("plans") or {}
    sel, plan = selections.get(strategy), plans.get(strategy)
    preds = {p["match_id"]: p for p in (st.get("probabilities") or {}).get("matches", [])}
    risks = {r["match_id"]: r for r in (st.get("risk") or {}).get("matches", [])}
    forms = {f["match_id"]: f for f in (st.get("form_analysis") or {}).get("matches", [])}
    picks = {s["seq"]: s for s in (plan or {}).get("selections", [])}

    rows = []
    for m in sch.get("matches", []):
        p, r, f = preds.get(m["match_id"], {}), risks.get(m["match_id"], {}), forms.get(m["match_id"], {})
        pr = p.get("probabilities")
        rec = (picks[m["seq"]]["picks_zh"] if m["seq"] in picks
               else OUTCOME_ZH[p["prediction"].lower()] if p.get("prediction") else "—")

        # 状态优势简写
        form_adv = "—"
        if f.get("form_advantage"):
            adv_map = {"home": "主", "away": "客", "balanced": "均"}
            form_adv = adv_map.get(f["form_advantage"], "—")

        # 战意对比简写
        motivation_info = "—"
        if f.get("home_form") and f.get("away_form"):
            h_mot = f["home_form"].get("motivation", {})
            a_mot = f["away_form"].get("motivation", {})
            if h_mot.get("motivation_level") and a_mot.get("motivation_level"):
                h_level = h_mot["motivation_level"]
                a_level = a_mot["motivation_level"]
                motivation_info = f"{h_level}/{a_level}"

        rows.append([
            m["seq"], m.get("competition") or "—", _hm(p.get("kickoff") or m.get("kickoff")), m["home_team"],
            m["away_team"], pct(pr["win"]) if pr else "—", pct(pr["draw"]) if pr else "—",
            pct(pr["loss"]) if pr else "—", rec, RISK_ZH.get(r.get("risk_level"), "—"),
            f"{p['model_confidence']:.2f}" if p.get("model_confidence") is not None else "—",
            f"{p['data_quality']:.2f}" if p.get("data_quality") is not None else "—",
            form_adv, motivation_info,
            ("✅ 入选" if m["seq"] in picks else "排除") if plan and plan.get("status") == "ok" else "—",
        ])
    match_table = table(["场次", "赛事", "开球(北京)", "主队", "客队", "胜", "平", "负", "推荐", "风险", "置信度",
                         "数据质量", "状态优势", "战意(主/客)", "任九"], rows, "rllllrrrccrrccc")

    if plan and plan.get("status") == "ok":
        multi = len(plan.get("tickets") or []) > 1
        selected_table = table(
            ["场次", "对阵", "推荐", "类型", "覆盖概率", "风险"] + (["多票覆盖"] if multi else []),
            [[s["seq"], s["match"], s["picks_zh"], s["type"], pct(s["coverage"], 1), RISK_ZH.get(s["risk_level"], "—")]
             + ([s.get("tickets_picks_zh") or s["picks_zh"]] if multi else [])
             for s in plan["selections"]], "rllcrc" + ("c" if multi else ""))
        if multi:
            selected_table += ("\n\n> \"推荐 / 类型 / 覆盖概率\"是单张复式方案（方案 A）的选择；"
                               "\"多票覆盖\"是多票方案（方案 B）各票在该场选到的全部结果，不能照这一列买成一张复式。")
        selected_table += "\n\n" + _tickets_md(plan)
        excluded_table = table(["场次", "对阵", "排除理由"],
                               [[e["seq"], e["match"], e["reason"]] for e in sel["excluded"]], "rll")
    else:
        msg = (sel or {}).get("message") or sch.get("action_required") or "赛单或数据不满足生成正式推荐的条件"
        selected_table = f"**未生成正式推荐**：{msg}"
        excluded_table = "（未筛选）"

    others = []
    for name, pl in plans.items():
        if name == strategy or pl.get("status") != "ok":
            continue
        others.append(f"### 备选方案：{STRATEGY_ZH.get(name, name)}策略\n\n"
                      f"- 排除场次：{'、'.join(str(x) for x in pl['excluded_matches'])}\n"
                      f"- {pl['bet_count']} 注，{pl['total_cost']} 元；模型估计全中概率 {pl['hit_probability'] * 100:.3f}%\n\n"
                      + _tickets_md(pl))
    weights_src = next((p.get("ensemble_weights_source") for p in preds.values()), "—")
    used = next((p.get("weights_used") for p in preds.values() if p.get("weights_used")), {})
    model_info = (f"- 集成方式：Elo / Poisson / Dixon-Coles / 市场去水概率融合（回测学习后为对数线性池化，"
                  f"有赔率 / 无赔率的比赛各用一套参数）；缺失的模型自动剔除。\n"
                  f"- 权重来源：{weights_src}。\n"
                  f"- 14 选 9 评分权重：{(sel or {}).get('score_weights', '—')}（{(sel or {}).get('score_weights_source', '—')}）。")
    n_pred = sum(1 for p in preds.values() if p.get("status") == "ok")
    status = "真实数据" if sch.get("official", True) else "手动赛单"
    data_status = (f"{status}；{len(sch.get('matches', []))} 场中 {n_pred} 场完成概率计算；"
                   f"赛程最后更新 {sch.get('last_updated') or '—'}")
    ctx = {
        "title": "任九推荐分析报告", "date": sch.get("date"), "period": sch.get("period") or "未获取到官方期次",
        "sale_end": sch.get("sale_end") or "—", "data_status": data_status, "generated_at": now_iso(),
        "alerts": _alerts(st), "match_table": match_table, "strategy_zh": STRATEGY_ZH.get(strategy, strategy),
        "selected_table": selected_table, "excluded_table": excluded_table, "plan_stats": _plan_stats(plan),
        "other_plans": "\n\n".join(others),
        "match_notes": _match_notes(st, plan, sel) if preds else "（无）",
        "sources_table": _sources_table(st), "missing": _missing_md(st), "model_info": model_info,
        "history_eval": history_eval_md(),
    }
    md = render("daily_report.md", ctx)
    js = {
        "report_type": "ren9_daily", "date": sch.get("date"), "period": sch.get("period"),
        "generated_at": ctx["generated_at"], "strategy": strategy,
        "schedule": {k: sch.get(k) for k in ("complete", "official", "sale_end", "sale_closed", "issues", "changed",
                                             "changes", "source", "last_updated", "notes")},
        "matches": [
            {
                "match_id": m["match_id"], "seq": m["seq"], "period": sch.get("period"), "date": m.get("date"),
                "home_team": m["home_team"], "away_team": m["away_team"], "competition": m.get("competition"),
                "kickoff": preds.get(m["match_id"], {}).get("kickoff") or m.get("kickoff"),
                "status": preds.get(m["match_id"], {}).get("match_status") or m.get("status"),
                "probabilities": preds.get(m["match_id"], {}).get("probabilities"),
                "model_confidence": preds.get(m["match_id"], {}).get("model_confidence"),
                "data_quality": preds.get(m["match_id"], {}).get("data_quality"),
                "risk_level": (risks.get(m["match_id"], {}).get("risk_level") or "UNKNOWN").lower(),
                "recommendation": (picks[m["seq"]]["picks"] if m["seq"] in picks
                                   else [preds[m["match_id"]]["prediction"].lower()]
                                   if preds.get(m["match_id"], {}).get("prediction") else []),
                "selected": m["seq"] in picks,
                "models_used": preds.get(m["match_id"], {}).get("models_used", []),
                "model_probabilities": preds.get(m["match_id"], {}).get("model_probabilities", {}),
                "risk_reasons": risks.get(m["match_id"], {}).get("reasons", []),
                "sources": m.get("sources", []),
            }
            for m in sch.get("matches", [])
        ],
        "plan": plan, "plans": plans,
        "excluded": (sel or {}).get("excluded", []),
        "disclaimer": "概率为模型估计，不保证命中或盈利；不构成投注建议。",
    }
    return md, js


def build_jczq(st: dict) -> tuple[str, dict]:
    sch = st["schedule"]
    preds = {p["match_id"]: p for p in (st.get("probabilities") or {}).get("matches", [])}
    risks = {r["match_id"]: r for r in (st.get("risk") or {}).get("matches", [])}
    odds = {o["match_id"]: o for o in (st.get("odds") or {}).get("matches", [])}
    rows, hrows, items = [], [], []
    for m in sch.get("matches", []):
        p, r, o = preds.get(m["match_id"], {}), risks.get(m["match_id"], {}), odds.get(m["match_id"], {})
        pr = p.get("probabilities")
        had = next((s for s in o.get("sources", []) if s["source"] == "sporttery_jc_had"), None)
        ev = None
        if pr and had:   # 模型概率 × 官方赔率 − 1：>0 表示模型认为赔率偏高（仅供参考）
            ev = {k: round(pr[k] * had["odds"][{"win": "home", "draw": "draw", "loss": "away"}[k]] - 1, 3)
                  for k in OUTCOMES}
        rows.append([
            m.get("match_num") or m["seq"], m.get("competition"), _hm(m.get("kickoff")), m["home_team"],
            m["away_team"], pct(pr["win"]) if pr else "—", pct(pr["draw"]) if pr else "—",
            pct(pr["loss"]) if pr else "—",
            "/".join(f"{had['odds'][k]:.2f}" for k in ("home", "draw", "away")) if had else "未开售",
            OUTCOME_ZH[p["prediction"].lower()] if p.get("prediction") else "—",
            RISK_ZH.get(r.get("risk_level"), "—"),
        ])
        hc = p.get("handicap")
        if hc:
            mp = hc.get("model")
            hrows.append([
                m.get("match_num") or m["seq"], f"{m['home_team']} vs {m['away_team']}", f"{hc['line']:+.0f}",
                "/".join(pct(mp[k]) for k in OUTCOMES) if mp else "—（无进球模型）",
                "/".join(pct(hc["market_fair"][k]) for k in OUTCOMES),
                "/".join(f"{hc['official_odds'][k]:.2f}" for k in ("home", "draw", "away")),
            ])
        items.append({"match_id": m["match_id"], "match_num": m.get("match_num"), "home_team": m["home_team"],
                      "away_team": m["away_team"], "competition": m.get("competition"), "kickoff": m.get("kickoff"),
                      "probabilities": pr, "prediction": p.get("prediction"), "handicap": hc,
                      "official_had_odds": had["odds"] if had else None, "model_vs_odds": ev,
                      "risk_level": (r.get("risk_level") or "UNKNOWN").lower(),
                      "model_confidence": p.get("model_confidence"), "data_quality": p.get("data_quality")})
    md = "\n\n".join([
        "# 竞彩足球分析报告（胜平负 / 让球胜平负）",
        f"日期：{sch.get('date')}\n\n报告生成时间：{now_iso()}\n\n数据状态：真实数据；{len(rows)} 场竞彩在售比赛",
        _alerts(st),
        "## 胜平负", table(["编号", "赛事", "开球(北京)", "主队", "客队", "胜", "平", "负", "官方赔率(胜/平/负)",
                         "模型首选", "风险"], rows, "lllllrrrlcc"),
        "## 让球胜平负", table(["编号", "对阵", "让球", "模型(胜/平/负)", "官方去水(胜/平/负)", "官方赔率"], hrows),
        "> 让球胜平负的模型概率 = 进球模型（Dixon-Coles / Poisson）的比分分布，按集成后的胜平负概率校准；"
        "没有进球模型的比赛只展示官方去水概率。",
        "## 逐场说明", _match_notes(st, None, None) if preds else "（无）",
        "## 数据来源与更新时间", _sources_table(st),
        "## 数据缺失提醒", _missing_md(st),
        "## 模型历史评估", history_eval_md(),
        "## 风险提示\n\n- 概率为模型估计，不是确定性结果。\n- 官方赔率包含抽水，赔率概率不等于真实概率。\n"
        "- 不保证命中或盈利，不构成投注建议。",
    ])
    md = re.sub(r"\n{3,}", "\n\n", md).strip() + "\n"
    return md, {"report_type": "jczq_daily", "date": sch.get("date"), "generated_at": now_iso(), "matches": items}


def build_match(st: dict, seq: int) -> str:
    sch = st["schedule"]
    m = next((x for x in sch["matches"] if x["seq"] == seq), None)
    if not m:
        raise ValueError(f"本期没有第 {seq} 场")
    mid = m["match_id"]
    p = next((x for x in st["probabilities"]["matches"] if x["match_id"] == mid), {})
    r = next((x for x in st["risk"]["matches"] if x["match_id"] == mid), {})
    o = next((x for x in (st.get("odds") or {}).get("matches", []) if x["match_id"] == mid), {})
    t = next((x for x in (st.get("team_data") or {}).get("matches", []) if x["match_id"] == mid), None)
    f = next((x for x in (st.get("form_analysis") or {}).get("matches", []) if x["match_id"] == mid), None)
    pr = p.get("probabilities")
    prob_table = table(["胜", "平", "负"], [[pct(pr[k], 1) for k in OUTCOMES]], "ccc") if pr else f"无法计算：{p.get('reason')}"
    used = p.get("weights_used", {})
    model_table = table(["模型", "胜", "平", "负", "集成权重"],
                        [[MODEL_ZH[k], pct(v["win"], 1), pct(v["draw"], 1), pct(v["loss"], 1), pct(used.get(k), 0)]
                         for k, v in p.get("model_probabilities", {}).items()], "lrrrr")
    det = []
    d = p.get("model_details", {})
    if "elo" in d:
        det.append(f"- Elo：主 {d['elo']['home_elo']} / 客 {d['elo']['away_elo']}，差值 {d['elo']['elo_difference']}"
                   f"（主场优势 {d['elo']['home_advantage']}）；来源：{d['elo']['source']}")
    for k in ("poisson", "dixon_coles"):
        if k in d:
            s = f"- {MODEL_ZH[k]}：预期进球 {d[k]['home_expected_goals']} : {d[k]['away_expected_goals']}"
            if k == "dixon_coles":
                s += f"，ρ={d[k]['rho']}，低比分概率 " + "、".join(f"{a} {pct(b, 1)}" for a, b in d[k]["low_scores"].items())
            det.append(s + f"；样本 主 {d[k]['sample']['home']} 场 / 客 {d[k]['sample']['away']} 场（{d[k]['source']}）")
    for k, why in (p.get("models_unavailable") or {}).items():
        det.append(f"- {MODEL_ZH.get(k, k)}：未参与（{why}）")
    odds_table = table(["来源", "类型", "胜", "平", "负", "水位", "去水 胜/平/负", "抓取时间"],
                       [[s["label"], "官方" if s["kind"] == "official" else "商业", s["odds"]["home"], s["odds"]["draw"],
                         s["odds"]["away"], pct(s["overround"], 1),
                         "/".join(pct(s["fair_probability"][k], 1) for k in ("home", "draw", "away")),
                         s.get("fetched_at")] for s in o.get("sources", [])], "llrrrrll")
    extra = []
    if o.get("jc_handicap"):
        h = o["jc_handicap"]
        extra.append(f"- 竞彩让球胜平负（{h['line']:+.0f}）：{h['odds']['home']}/{h['odds']['draw']}/{h['odds']['away']}")
    if o.get("asian_handicap"):
        a = o["asian_handicap"]
        extra.append(f"- 亚洲让球（{a['source']}）：主 {a['line']:+g}，{a['home']} / {a['away']}")
    if o.get("totals"):
        tt = o["totals"]
        extra.append(f"- 大小球（{tt['source']}）：{tt['line']} 球，大 {tt['over']} / 小 {tt['under']}")
    mv = o.get("movement") or {}
    extra.append(f"- 赔率波动：{'去水概率最大变动 ' + pct(mv['max_change'], 1) if mv.get('known') else '暂无可比较的历史赔率'}")
    team_table, h2h = "（未能对应到公开数据源，球队数据缺失）", ""
    # 球队状态和战意分析，以及它对概率的修正
    form_lines = [f"- {x}" for x in (f or {}).get("notes", [])]
    adj = p.get("form_adjustment") or {}
    if adj.get("applied"):
        d, before = adj["delta"], adj["before"]
        form_lines.append(
            f"- 概率修正：胜 {pct(before['win'], 1)} → {pct(before['win'] + d['win'], 1)}，平 {pct(before['draw'], 1)} → "
            f"{pct(before['draw'] + d['draw'], 1)}，负 {pct(before['loss'], 1)} → {pct(before['loss'] + d['loss'], 1)}"
            f"（主胜 {d['win'] * 100:+.1f} 个百分点{'，已触及上限' if adj.get('capped') else ''}；系数来源：{adj['source']}）")
    elif adj:
        form_lines.append(f"- 概率未修正：{adj.get('reason')}（系数来源：{adj.get('source')}）")
    form_analysis_text = "\n".join(form_lines) if form_lines else "状态和战意分析数据不足"
    # 主力缺阵对概率的修正（官方首发公布后才有）
    lu_adj = p.get("lineup_adjustment") or {}
    if lu_adj.get("applied"):
        d = lu_adj["delta"]
        absence_text = (f"缺阵指数 主 {lu_adj['home_absence']:.2f} / 客 {lu_adj['away_absence']:.2f}，"
                        f"概率修正：胜 {d['win'] * 100:+.1f} / 平 {d['draw'] * 100:+.1f} / 负 {d['loss'] * 100:+.1f} 个百分点"
                        f"{'（已触及上限）' if lu_adj.get('capped') else ''}。系数来源：{lu_adj['source']}。"
                        "缺阵由官方首发对比近期首发推断，原因（伤病 / 停赛 / 轮换）未知。")
    else:
        absence_text = f"概率未按主力缺阵修正：{lu_adj.get('reason') or '未采集首发数据'}。"
    if t and t["resolution"]["status"] == "resolved":
        def row(label, fn):
            return [label, fn(t["teams"]["home"]), fn(t["teams"]["away"])]

        def form(x, key):
            f = (x.get("form") or {}).get(key)
            return "".join(FORM_ZH[c] for c in f["results"]) + f"（{f['scope']}）" if f else "—"

        def g(x, grp, key, fmt="{}"):
            v = (x.get(grp) or {}).get(key)
            return "—" if v is None else fmt.format(v)

        team_table = table(["项目", f"{m['home_team']}（主）", f"{m['away_team']}（客）"], [
            row("英文名", lambda x: x.get("name_en") or "—"),
            row("排名 / 积分", lambda x: f"{g(x, 'standing', 'rank')} / {g(x, 'standing', 'points')}"),
            row("近 5 场（新→旧）", lambda x: form(x, "last5")),
            row("近 10 场（新→旧）", lambda x: form(x, "last10")),
            row("场均进球 / 失球", lambda x: f"{g(x, 'attack', 'goals_per_match')} / {g(x, 'defence', 'conceded_per_match')}"),
            row("场均 xG / xGA", lambda x: f"{g(x, 'attack', 'xg_per_match')} / {g(x, 'defence', 'xga_per_match')}"),
            row("场均射门", lambda x: g(x, "attack", "shots_per_match")),
            row("主场胜率 / 客场胜率", lambda x: f"{g(x, 'venue', 'home_win_rate', '{:.0%}')} / {g(x, 'venue', 'away_win_rate', '{:.0%}')}"),
            row("休息天数", lambda x: g(x, "schedule", "rest_days")),
            row("近 14 天比赛数", lambda x: g(x, "schedule", "matches_last_14d")),
            row("Elo", lambda x: g(x, "elo", "value")),
            row("官方首发", lambda x: "已公布 " + (x["lineup"].get("formation") or "") if x["lineup"]["official_lineup_published"] else "未公布"),
            row("主力缺阵（由首发推断）", lambda x: describe_absences(x["lineup"].get("absences"))),
            row("伤停名单", lambda x: "未知（无可靠来源）"),
        ])

        if t.get("h2h") and t["h2h"]["meetings"]:
            hh = t["h2h"]
            h2h = (f"历史交锋（{hh['window']}，共 {hh['meetings']} 次）：{m['home_team']} {hh['home_team_wins']} 胜 "
                   f"{hh['draws']} 平 {hh['away_team_wins']} 负。最近：" +
                   "；".join(f"{x['date']} {x['home']} {x['score']} {x['away']}" for x in hh["recent"]))
        else:
            h2h = "历史交锋：数据集覆盖期内没有交锋记录。"
    risk_table = table(["因子", "取值(0-1)", "说明"],
                       [[FACTOR_ZH[k],
                         "未知" if not f["known"] else f"{f['value']:.2f}", f["note"]]
                        for k, f in (r.get("factors") or {}).items()], "lrl")
    srcs = [f"- {s.get('name')}（{s.get('fetched_at')}）" for s in m.get("sources", [])]
    if t and t.get("history"):
        srcs += [f"- {n}（{t['history']['updated_at']}）" for n in t["history"]["source"]]
    ctx = {
        "home": m["home_team"], "away": m["away_team"], "period": sch.get("period"), "seq": seq,
        "competition": m.get("competition") or "—", "kickoff": p.get("kickoff") or m.get("kickoff") or "未知",
        "status": p.get("match_status") or m.get("status"), "generated_at": now_iso(), "prob_table": prob_table,
        "prediction": OUTCOME_ZH[p["prediction"].lower()] if p.get("prediction") else "—",
        "confidence": f"{p['model_confidence']:.2f}" if p.get("model_confidence") is not None else "—",
        "data_quality": f"{p['data_quality']:.2f}" if p.get("data_quality") is not None else "—",
        "risk_level": f"{RISK_ZH.get(r.get('risk_level'), '—')}（评分 {r.get('risk_score')}）；" + "；".join(r.get("reasons", [])),
        "model_table": model_table, "model_details": "\n".join(det), "odds_table": odds_table,
        "odds_extra": "\n".join(extra), "team_table": team_table, "h2h": h2h,
        "form_analysis": form_analysis_text, "absence_analysis": absence_text, "risk_table": risk_table,
        "missing": "\n".join(f"- {x}" for x in (t or {}).get("missing", ["未采集球队数据"])),
        "sources": "\n".join(dict.fromkeys(srcs)),
    }
    return render("match_report.md", ctx)


def build_review(review: dict, agg: dict | None = None) -> str:
    if review.get("status") != "ok":
        return f"# 复盘：第 {review.get('period')} 期\n\n{review.get('message', '暂无法复盘')}\n"
    out = [f"# 任九复盘报告：第 {review['period']} 期",
           f"- 预测生成时间：{review.get('predicted_at')}（分析日期 {review.get('analysis_date')}）\n"
           f"- 官方开奖结果：{review.get('draw_result')}（开奖日期 {review.get('draw_date')}）\n"
           f"- 复盘时间：{review['reviewed_at']}"]
    if review.get("post_deadline_prediction"):
        out.append("> ⚠️ 该预测生成于销售截止之后，仅供查看，不代表真实的赛前预测能力。")
    rows = []
    for m in review["matches"]:
        pr = m.get("probabilities")
        act = m.get("actual")
        rows.append([m["seq"], m["match"], m.get("score") or "—",
                     "无效(*)" if act == "*" else OUTCOME_ZH.get(act, "—"),
                     pct(pr["win"]) if pr else "—", pct(pr["draw"]) if pr else "—", pct(pr["loss"]) if pr else "—",
                     OUTCOME_ZH.get((m.get("prediction") or "").lower(), "—"),
                     "—" if "top_pick_hit" not in m else ("✅" if m["top_pick_hit"] else "❌"),
                     pct(m.get("probability_of_actual"), 0) if m.get("probability_of_actual") is not None else "—"])
    out += ["## 逐场结果", table(["场次", "对阵", "比分", "赛果", "胜", "平", "负", "首选", "命中", "赛果的预测概率"],
                               rows, "rlccrrrccr")]
    if review.get("metrics"):
        mt = review["metrics"]
        out += ["## 概率质量",
                f"- 胜平负准确率（Accuracy）：{pct(mt['accuracy'], 1)}（{mt['n']} 场）\n- Brier Score：{mt['brier']}"
                f"（越低越好，均匀猜测为 0.667）\n- Log Loss：{mt['log_loss']}（均匀猜测为 1.099）\n- RPS：{mt['rps']}"]
        mm = [[MODEL_ZH.get(k, k), v["n"], pct(v["accuracy"], 1), v["brier"], v["log_loss"], v["rps"]]
              for k, v in review.get("model_metrics", {}).items() if v]
        if mm:
            out += ["各模型单独表现：", table(["模型", "场数", "准确率", "Brier", "Log Loss", "RPS"], mm, "lrrrrr")]
        if review.get("outcome_bias"):
            b = review["outcome_bias"]
            out.append("模型偏差（平均预测概率 − 实际频率，正值为高估）：" +
                       "，".join(f"{OUTCOME_ZH[k]} {b[k]:+.1%}" for k in OUTCOMES))
    out.append("## 方案结果")
    for pl in review.get("plans", []):
        out.append(f"### {STRATEGY_ZH.get(pl['strategy'], pl['strategy'])}策略")
        out.append(table(["场次", "对阵", "选择", "类型", "赛果", "命中"],
                         [[s["seq"], s["match"], s["picks"], s["type"], s["actual"], "✅" if s["hit"] else "❌"]
                          for s in pl["selections"]], "rlcccc"))
        roi = (f"单注奖金 {pl['prize_per_bet']:.0f} 元（官方公布，税前），中奖 {pl['winning_bets']} 注，"
               f"税后奖金 {pl['payout_after_tax']:.0f} 元，ROI {pl['roi']:+.1%}"
               if pl.get("roi") is not None else pl.get("roi_note", "ROI 不可用"))
        out.append(f"- 9 场命中 {pl['hits']}/{pl['total']}；单选命中 {pl['single_hits']}/{pl['single_total']}；"
                   f"双选/全选覆盖 {pl['multi_covered']}/{pl['multi_total']}；"
                   f"9 场全中：{'是' if pl['all_nine_hit'] else '否'}"
                   + (f"（共 {len(pl['tickets'])} 张票，命中最多的一张 {pl['best_ticket_hits']}/9；上表为单张复式方案的选择，是否全中按各票判定）"
                      if len(pl.get("tickets") or []) > 1 else "") + "\n"
                   f"- 成本 {pl['cost']} 元（{pl['bet_count']} 注）；{roi}")
    if not review.get("plans"):
        out.append("（该期没有保存的方案）")
    dq = review.get("data_quality", {})
    out += ["## 数据源质量", f"- 平均数据质量：{dq.get('mean')}；无法给出概率的场次：{dq.get('no_prediction')}\n"
            f"- 各模型覆盖场数：{dq.get('model_coverage')}"]
    if review.get("warnings"):
        out += ["## 提示", "\n".join(f"- {w}" for w in review["warnings"])]
    if agg and agg.get("period_count"):
        out += ["## 历史累计表现", history_eval_md()]
    return re.sub(r"\n{3,}", "\n\n", "\n\n".join(out)).strip() + "\n"
