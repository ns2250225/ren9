"""本地存储：分阶段运行目录（JSON）+ 历史数据库（SQLite）。"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from ren9.common import data_dir, now_iso, read_json, write_json

STAGES = ("schedule", "team_data", "odds", "probabilities", "risk", "selection", "plans", "report")


# ---------- 运行目录 ----------

def run_dir(game_type: str, period: str) -> Path:
    d = data_dir() / "cache" / "runs" / f"{game_type}_{period}"
    d.mkdir(parents=True, exist_ok=True)
    return d


def save_stage(game_type: str, period: str, stage: str, obj: dict) -> Path:
    return write_json(run_dir(game_type, period) / f"{stage}.json", obj)


def load_stage(game_type: str, period: str, stage: str) -> dict | None:
    return read_json(run_dir(game_type, period) / f"{stage}.json")


def _index_path() -> Path:
    return data_dir() / "cache" / "runs" / "index.json"


def remember_period(game_type: str, day: str, period: str) -> None:
    idx = read_json(_index_path(), {}) or {}
    idx[f"{game_type}:{day}"] = period
    write_json(_index_path(), idx)


def recall_period(game_type: str, day: str) -> str | None:
    return (read_json(_index_path(), {}) or {}).get(f"{game_type}:{day}")


# ---------- 历史数据库 ----------

def db() -> sqlite3.Connection:
    conn = sqlite3.connect(data_dir() / "history" / "ren9.db", timeout=30)
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS predictions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            game_type TEXT, period TEXT, analysis_date TEXT, created_at TEXT,
            match_id TEXT, seq INTEGER, home TEXT, away TEXT, competition TEXT,
            p_win REAL, p_draw REAL, p_loss REAL,
            models_json TEXT, prediction TEXT, risk_level TEXT,
            data_quality REAL, confidence REAL, post_deadline INTEGER DEFAULT 0, snapshot TEXT
        );
        CREATE INDEX IF NOT EXISTS ix_pred_period ON predictions(game_type, period, created_at);
        CREATE TABLE IF NOT EXISTS plans (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            game_type TEXT, period TEXT, analysis_date TEXT, created_at TEXT,
            strategy TEXT, plan_json TEXT, post_deadline INTEGER DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS results (
            game_type TEXT, period TEXT, seq INTEGER, result TEXT, score TEXT, fetched_at TEXT,
            PRIMARY KEY (game_type, period, seq)
        );
        CREATE TABLE IF NOT EXISTS reviews (
            game_type TEXT, period TEXT, reviewed_at TEXT, review_json TEXT,
            PRIMARY KEY (game_type, period)
        );
        CREATE TABLE IF NOT EXISTS odds_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            match_id TEXT, source TEXT, fetched_at TEXT, home REAL, draw REAL, away REAL
        );
        CREATE INDEX IF NOT EXISTS ix_odds_match ON odds_snapshots(match_id, source, id);
        """
    )
    return conn


def record_odds(match_id: str, source: str, odds: dict, fetched_at: str) -> None:
    """赔率有变化时追加一条快照（用于赔率波动分析）。"""
    conn = db()
    try:
        last = conn.execute(
            "SELECT home, draw, away FROM odds_snapshots WHERE match_id=? AND source=? ORDER BY id DESC LIMIT 1",
            (match_id, source),
        ).fetchone()
        cur = (odds["home"], odds["draw"], odds["away"])
        if last is None or tuple(last) != cur:
            conn.execute(
                "INSERT INTO odds_snapshots(match_id, source, fetched_at, home, draw, away) VALUES (?,?,?,?,?,?)",
                (match_id, source, fetched_at, *cur),
            )
            conn.commit()
    finally:
        conn.close()


def odds_history(match_id: str, source: str) -> list[dict]:
    conn = db()
    try:
        rows = conn.execute(
            "SELECT fetched_at, home, draw, away FROM odds_snapshots WHERE match_id=? AND source=? ORDER BY id",
            (match_id, source),
        ).fetchall()
    finally:
        conn.close()
    return [dict(r) for r in rows]


def save_predictions(game_type: str, period: str, analysis_date: str, matches: list[dict],
                     post_deadline: bool, snapshot: str) -> str:
    created = now_iso()
    conn = db()
    try:
        for m in matches:
            p = m.get("probabilities") or {}
            conn.execute(
                "INSERT INTO predictions(game_type, period, analysis_date, created_at, match_id, seq, home, away,"
                " competition, p_win, p_draw, p_loss, models_json, prediction, risk_level, data_quality, confidence,"
                " post_deadline, snapshot) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    game_type, period, analysis_date, created, m["match_id"], m.get("seq"), m["home_team"],
                    m["away_team"], m.get("competition"), p.get("win"), p.get("draw"), p.get("loss"),
                    json.dumps(m.get("model_probabilities") or {}, ensure_ascii=False), m.get("prediction"),
                    m.get("risk_level"), m.get("data_quality"), m.get("model_confidence"),
                    1 if post_deadline else 0, snapshot,
                ),
            )
        conn.commit()
    finally:
        conn.close()
    return created


def save_plan(game_type: str, period: str, analysis_date: str, plan: dict, post_deadline: bool) -> None:
    conn = db()
    try:
        conn.execute(
            "INSERT INTO plans(game_type, period, analysis_date, created_at, strategy, plan_json, post_deadline)"
            " VALUES (?,?,?,?,?,?,?)",
            (game_type, period, analysis_date, now_iso(), plan["strategy"],
             json.dumps(plan, ensure_ascii=False), 1 if post_deadline else 0),
        )
        conn.commit()
    finally:
        conn.close()


def latest_predictions(game_type: str, period: str, allow_post_deadline: bool = False) -> list[dict]:
    """某期最后一次（默认要求在截止时间之前生成的）预测。"""
    conn = db()
    try:
        cond = "" if allow_post_deadline else " AND post_deadline=0"
        row = conn.execute(
            f"SELECT MAX(created_at) AS c FROM predictions WHERE game_type=? AND period=?{cond}",
            (game_type, period),
        ).fetchone()
        if not row or not row["c"]:
            return []
        rows = conn.execute(
            "SELECT * FROM predictions WHERE game_type=? AND period=? AND created_at=? ORDER BY seq",
            (game_type, period, row["c"]),
        ).fetchall()
    finally:
        conn.close()
    out = []
    for r in rows:
        d = dict(r)
        d["models"] = json.loads(d.pop("models_json") or "{}")
        out.append(d)
    return out


def latest_plans(game_type: str, period: str, allow_post_deadline: bool = False) -> list[dict]:
    """某期每个策略最后一次保存的方案。"""
    conn = db()
    try:
        cond = "" if allow_post_deadline else " AND post_deadline=0"
        rows = conn.execute(
            f"SELECT strategy, plan_json, MAX(id) FROM plans WHERE game_type=? AND period=?{cond} GROUP BY strategy",
            (game_type, period),
        ).fetchall()
    finally:
        conn.close()
    return [json.loads(r["plan_json"]) for r in rows]


def periods_with_predictions(game_type: str, analysis_date: str | None = None) -> list[str]:
    conn = db()
    try:
        if analysis_date:
            rows = conn.execute(
                "SELECT DISTINCT period FROM predictions WHERE game_type=? AND analysis_date=? ORDER BY period",
                (game_type, analysis_date),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT DISTINCT period FROM predictions WHERE game_type=? ORDER BY period", (game_type,)
            ).fetchall()
    finally:
        conn.close()
    return [r["period"] for r in rows]


def save_results(game_type: str, period: str, results: list[dict]) -> None:
    conn = db()
    try:
        for r in results:
            conn.execute(
                "INSERT OR REPLACE INTO results(game_type, period, seq, result, score, fetched_at) VALUES (?,?,?,?,?,?)",
                (game_type, period, r["seq"], r.get("result"), r.get("score"), now_iso()),
            )
        conn.commit()
    finally:
        conn.close()


def save_review(game_type: str, period: str, review: dict) -> None:
    conn = db()
    try:
        conn.execute(
            "INSERT OR REPLACE INTO reviews(game_type, period, reviewed_at, review_json) VALUES (?,?,?,?)",
            (game_type, period, now_iso(), json.dumps(review, ensure_ascii=False)),
        )
        conn.commit()
    finally:
        conn.close()


def all_reviews(game_type: str = "ren9") -> list[dict]:
    conn = db()
    try:
        rows = conn.execute(
            "SELECT review_json FROM reviews WHERE game_type=? ORDER BY period", (game_type,)
        ).fetchall()
    finally:
        conn.close()
    return [json.loads(r["review_json"]) for r in rows]
