"""赛程：读取 14 场官方赛单、校验缺失与重复、手动赛单、降级规则。"""
import json

from providers import sporttery
from providers.base import ProviderError
from ren9 import schedule


def _parsed(zc_text):
    return sporttery.parse_ren9_schedule(json.loads(zc_text)["value"], {"name": "fixture", "fetched_at": "t"})


def test_parse_official_coupon_has_14_real_matches(zc_text):
    s = _parsed(zc_text)
    assert s["period"] == "26169"
    assert len(s["matches"]) == 14
    assert [m["seq"] for m in s["matches"]] == list(range(1, 15))
    assert s["matches"][0]["home_team"] == "博德闪耀" and s["matches"][0]["away_team"] == "LASK林茨"
    assert s["sale_end"] == "2026-11-24 22:00:00"
    assert all(m["home_team"] and m["away_team"] and m["competition"] for m in s["matches"])
    assert schedule.validate_schedule(s["matches"], 14) == []


def test_missing_odds_are_none_not_zero(zc_text):
    s = _parsed(zc_text)
    assert s["matches"][0]["official_ref_odds"] == {"home": 1.29, "draw": 5.3, "away": 5.7}
    assert s["matches"][13]["official_ref_odds"] is None


def test_validation_detects_missing_and_duplicates(zc_text):
    ms = _parsed(zc_text)["matches"]
    short = ms[:13]
    codes = {i["code"] for i in schedule.validate_schedule(short, 14)}
    assert {"count", "missing_seq"} <= codes

    dup = [dict(m) for m in ms]
    dup[5]["seq"] = 3
    codes = {i["code"] for i in schedule.validate_schedule(dup, 14)}
    assert {"duplicate_seq", "missing_seq"} <= codes

    same = [dict(m) for m in ms]
    same[1]["home_team"], same[1]["away_team"] = same[0]["home_team"], same[0]["away_team"]
    assert "duplicate_fixture" in {i["code"] for i in schedule.validate_schedule(same, 14)}

    blank = [dict(m) for m in ms]
    blank[2]["away_team"] = ""
    assert "team_missing" in {i["code"] for i in schedule.validate_schedule(blank, 14)}


def test_official_schedule_via_api(fake_http, zc_text, jc_text):
    fake_http({"getFootBallMatchV1": zc_text, "getMatchCalculatorV1": jc_text})
    s = schedule.get_schedule("2026-11-24", "ren9")
    assert s["complete"] and s["official"] and s["period"] == "26169"
    assert len(s["matches"]) == s["expected_matches"] == 14
    assert s["source"][0]["fetched_at"]
    # 开球时间来自竞彩官方数据的交叉补充，且只补充已在赛单中的比赛
    assert s["matches"][0]["kickoff"] == "2026-11-25T01:45:00+08:00"
    assert s["matches"][13]["kickoff"] is None


def test_no_official_schedule_never_padded_with_jczq(fake_http, jc_text):
    """官方赛单不可用时：即使竞彩有 21 场比赛，也不能拼出 14 场。"""
    fake_http({"getFootBallMatchV1": ProviderError("down"), "getHistoryPageListV1": ProviderError("down"),
               "getMatchCalculatorV1": jc_text})
    s = schedule.get_schedule("2026-11-24", "ren9")
    assert s["complete"] is False
    assert s["matches"] == []
    assert "手动提供" in s["action_required"]


def test_date_outside_sale_window_gives_no_schedule(fake_http, zc_text, jc_text):
    fake_http({"getFootBallMatchV1": zc_text, "getMatchCalculatorV1": jc_text})
    s = schedule.get_schedule("2026-12-30", "ren9")
    assert s["complete"] is False and s["matches"] == []


def test_manual_schedule_formats():
    csv_text = "\n".join(f"{i},主队{i},客队{i},英超,2026-11-25" for i in range(1, 15))
    ms = schedule.parse_manual(csv_text, "M1")
    assert len(ms) == 14 and ms[2]["home_team"] == "主队3" and ms[2]["competition"] == "英超"
    assert ms[0]["sources"][0]["name"] == "用户手动提供"

    vs_text = "\n".join(f"甲{i} vs 乙{i}" for i in range(1, 15))
    ms = schedule.parse_manual(vs_text, "M2")
    assert [m["seq"] for m in ms] == list(range(1, 15)) and ms[13]["away_team"] == "乙14"

    js = json.dumps([{"home": f"H{i}", "away": f"A{i}", "league": "德甲"} for i in range(1, 15)])
    ms = schedule.parse_manual(js, "M3")
    assert len(ms) == 14 and ms[4]["competition"] == "德甲"


def test_manual_schedule_is_flagged_unofficial_and_validated():
    text = "\n".join(f"{i},主{i},客{i},英超,2026-11-25" for i in range(1, 14))   # 只有 13 场
    s = schedule.get_schedule("2026-11-24", "ren9", period="X1", manual=text)
    assert s["official"] is False and s["complete"] is False
    assert any(i["code"] == "count" for i in s["issues"])


def test_change_detection(fake_http, zc_text, jc_text):
    fake_http({"getFootBallMatchV1": zc_text, "getMatchCalculatorV1": jc_text})
    first = schedule.get_schedule("2026-11-24", "ren9")
    assert first["changed"] is False
    data = json.loads(zc_text)
    data["value"]["sfcMatch"]["matchList"][2]["guestTeamAllName"] = "拜仁慕尼黑"
    fake_http({"getFootBallMatchV1": json.dumps(data, ensure_ascii=False), "getMatchCalculatorV1": jc_text})
    second = schedule.get_schedule("2026-11-24", "ren9", refresh=True)
    assert second["changed"] is True
    assert second["changes"][0] == {"seq": 3, "field": "away_team", "old": "多特蒙德", "new": "拜仁慕尼黑"}


def test_jczq_is_a_separate_structure(fake_http, jc_text):
    fake_http({"getMatchCalculatorV1": jc_text})
    s = schedule.get_schedule("2026-11-24", "jczq")
    assert s["game_type"] == "jczq" and s["expected_matches"] is None
    assert s["matches"] and all(m["match_id"].startswith("jczq_") for m in s["matches"])
    assert all(m["business_date"] == "2026-11-24" for m in s["matches"])
    assert s["matches"][0]["had"]["home"] == 8.0 and s["matches"][0]["hhad"]["handicap"] == 1.0
