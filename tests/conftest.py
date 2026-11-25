"""测试公共夹具：离线运行（禁止联网）、临时数据目录、可注入的 HTTP 响应。"""
from __future__ import annotations

import json
import random
import sys
from datetime import date, timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

FIXTURES = Path(__file__).parent


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    """每个测试使用独立的数据目录；不读取学习到的权重；默认任何网络请求都失败。"""
    monkeypatch.setenv("REN9_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("REN9_IGNORE_LEARNED", "1")
    from providers import base
    from ren9 import history, predict

    def no_network(url, headers, timeout):
        raise base.ProviderError(f"network disabled in tests: {url}")

    base.set_fetcher(no_network)
    history._memo.clear()
    predict._fit_memo.clear()
    yield
    base.set_fetcher(None)


@pytest.fixture
def fake_http():
    """按 URL 子串注入响应：fake_http({'getFootBallMatchV1': text, ...})。"""
    from providers import base

    def install(routes: dict):
        def fetch(url, headers, timeout):
            for key, body in routes.items():
                if key in url:
                    if isinstance(body, Exception):
                        raise body
                    return body
            raise base.ProviderError(f"no fake route for {url}")

        base.set_fetcher(fetch)

    return install


def load_fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


@pytest.fixture
def zc_text():
    return load_fixture("zc_26169.json")


@pytest.fixture
def jc_text():
    return load_fixture("jc_20261124.json")


@pytest.fixture
def draw_text():
    return load_fixture("zc_draw_26168.json")


def synthetic_league(n_teams: int = 12, rounds: int = 3, seed: int = 7, end: date = date(2026, 11, 1)) -> list[dict]:
    """生成一个有明确强弱次序的模拟联赛（仅用于测试模型数学性质，不用于任何推荐）。"""
    import numpy as np

    rng = np.random.default_rng(seed)
    teams = [f"T{i:02d}" for i in range(n_teams)]
    strength = {t: 0.5 - i * (1.0 / (n_teams - 1)) for i, t in enumerate(teams)}   # T00 最强
    fixtures = [(h, a) for _ in range(rounds) for h in teams for a in teams if h != a]
    random.Random(seed).shuffle(fixtures)
    start = end - timedelta(days=len(fixtures) // 4 + 5)
    rows = []
    for i, (h, a) in enumerate(fixtures):
        lam = float(np.exp(0.25 + strength[h] - strength[a]))
        mu = float(np.exp(strength[a] - strength[h]))
        rows.append({"date": (start + timedelta(days=i // 4)).isoformat(), "home": h, "away": a,
                     "hg": int(rng.poisson(lam)), "ag": int(rng.poisson(mu)), "hxg": None, "axg": None})
    return rows


def make_pred(seq: int, probs: tuple, quality: float = 0.85, agreement: float | None = 0.8, status: str = "ok"):
    p = {"win": probs[0], "draw": probs[1], "loss": probs[2]} if probs else None
    top = max(p, key=p.get).upper() if p else None
    return {
        "match_id": f"ren9_T_{seq:02d}", "seq": seq, "home_team": f"主{seq}", "away_team": f"客{seq}",
        "competition": "测试联赛", "kickoff": None, "match_status": "scheduled", "status": status,
        "probabilities": p, "prediction": top, "model_confidence": 0.7 if p else None, "data_quality": quality,
        "agreement": agreement, "models_used": ["dixon_coles", "market"] if p else [],
        "model_probabilities": {"dixon_coles": p, "market": p} if p else {}, "models_unavailable": {},
        "weights_used": {"dixon_coles": 0.5, "market": 0.5} if p else {},
    }


def make_schedule(n: int = 14) -> dict:
    matches = [{"match_id": f"ren9_T_{i:02d}", "period": "T", "seq": i, "home_team": f"主{i}", "away_team": f"客{i}",
                "competition": "测试联赛", "date": "2026-11-25", "kickoff": None, "status": "scheduled",
                "official_ref_odds": None, "jc_match_id": None, "sources": []} for i in range(1, n + 1)]
    from ren9.schedule import validate_schedule

    issues = validate_schedule(matches, 14)
    return {"date": "2026-11-24", "game_type": "ren9", "period": "T", "expected_matches": 14, "matches": matches,
            "complete": not any(i["level"] == "error" for i in issues), "official": True, "issues": issues,
            "sale_end": None}


PROBS_14 = [
    (0.72, 0.18, 0.10), (0.35, 0.40, 0.25), (0.55, 0.25, 0.20), (0.28, 0.30, 0.42), (0.66, 0.20, 0.14),
    (0.20, 0.27, 0.53), (0.61, 0.22, 0.17), (0.48, 0.28, 0.24), (0.15, 0.20, 0.65), (0.37, 0.33, 0.30),
    (0.58, 0.24, 0.18), (0.80, 0.12, 0.08), (0.42, 0.30, 0.28), (0.34, 0.33, 0.33),
]


@pytest.fixture
def analysis():
    """14 场的赛程 + 预测 + 风险（纯内存构造，用于筛选/优化/报告测试）。"""
    from ren9 import risk

    sch = make_schedule()
    preds = {"date": sch["date"], "game_type": "ren9", "period": "T",
             "matches": [make_pred(i + 1, p) for i, p in enumerate(PROBS_14)]}
    rk = risk.assess(preds, None, None)
    return sch, preds, rk
