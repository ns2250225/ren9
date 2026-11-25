"""公共工具：路径、配置、时间、JSON 输出。"""
from __future__ import annotations

import copy
import json
import os
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import yaml

ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = ROOT / "config"
TEMPLATE_DIR = ROOT / "templates"


def data_dir() -> Path:
    """数据目录，可用环境变量 REN9_DATA_DIR 覆盖（测试时使用临时目录）。"""
    d = Path(os.environ.get("REN9_DATA_DIR") or (ROOT / "data"))
    for sub in ("cache", "history", "predictions", "results"):
        (d / sub).mkdir(parents=True, exist_ok=True)
    return d


_CONFIG_CACHE: dict[str, Any] = {}


def load_yaml(name: str) -> dict:
    path = CONFIG_DIR / name
    key = str(path)
    if key not in _CONFIG_CACHE:
        with open(path, encoding="utf-8") as f:
            _CONFIG_CACHE[key] = yaml.safe_load(f) or {}
    return copy.deepcopy(_CONFIG_CACHE[key])


def save_yaml(name: str, data: dict) -> None:
    path = CONFIG_DIR / name
    with open(path, "w", encoding="utf-8") as f:
        yaml.safe_dump(data, f, allow_unicode=True, sort_keys=False)
    _CONFIG_CACHE.pop(str(path), None)


def settings() -> dict:
    return load_yaml("settings.yaml")


def data_sources() -> dict:
    return load_yaml("data_sources.yaml")


LEARNED_FILE = "model_weights.learned.yaml"


def model_weights() -> dict:
    """模型参数 = config/model_weights.yaml + 回测/复盘学习结果（model_weights.learned.yaml）。"""
    mw = load_yaml("model_weights.yaml")
    if (CONFIG_DIR / LEARNED_FILE).exists() and os.environ.get("REN9_IGNORE_LEARNED") != "1":
        learned = load_yaml(LEARNED_FILE)
        if learned.get("ensemble"):
            mw.setdefault("ensemble", {})["learned"] = learned["ensemble"]
        if learned.get("elo_draw_nu"):
            mw.setdefault("elo", {})["draw_nu"] = learned["elo_draw_nu"]
        if learned.get("selector"):
            mw.setdefault("selector", {})["learned"] = learned["selector"]
        if learned.get("form_adjust"):
            mw.setdefault("form_adjust", {})["learned"] = learned["form_adjust"]
    return mw


def save_learned(update: dict) -> None:
    """把学习到的参数写入 model_weights.learned.yaml（不改动带注释的主配置文件）。"""
    cur = load_yaml(LEARNED_FILE) if (CONFIG_DIR / LEARNED_FILE).exists() else {}
    cur.update(update)
    save_yaml(LEARNED_FILE, cur)


def tz() -> ZoneInfo:
    try:
        return ZoneInfo(settings().get("general", {}).get("timezone", "Asia/Shanghai"))
    except Exception:  # Windows 上缺少 tzdata 时退回固定偏移
        from datetime import timezone

        return timezone(timedelta(hours=8))  # type: ignore[return-value]


def now() -> datetime:
    return datetime.now(tz())


def now_iso() -> str:
    return now().isoformat(timespec="seconds")


def resolve_date(value: str | None) -> str:
    """把 today / yesterday / tomorrow / YYYY-MM-DD 解析为 YYYY-MM-DD（北京时间）。"""
    today = now().date()
    v = (value or "today").strip().lower()
    if v in ("today", "今天"):
        return today.isoformat()
    if v in ("yesterday", "昨天"):
        return (today - timedelta(days=1)).isoformat()
    if v in ("tomorrow", "明天"):
        return (today + timedelta(days=1)).isoformat()
    return date.fromisoformat(v.replace("/", "-")).isoformat()


def parse_dt(value: str | None) -> datetime | None:
    """解析 ISO 或 'YYYY-MM-DD HH:MM:SS'，无时区信息时按北京时间。"""
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=tz())
    return dt


def to_float(value: Any) -> float | None:
    """安全转 float；空值/非法值返回 None（缺失不等于 0）。"""
    if value is None:
        return None
    try:
        s = str(value).strip().replace(",", "")
        if s == "":
            return None
        f = float(s)
    except (TypeError, ValueError):
        return None
    if f != f:  # NaN
        return None
    return f


def read_json(path: Path, default: Any = None) -> Any:
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def write_json(path: Path, obj: Any) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2, default=str)
    os.replace(tmp, path)
    return path


def setup_stdio() -> None:
    """保证 Windows 控制台 / 管道下输出 UTF-8。"""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
        except Exception:
            pass


def emit(obj: Any) -> None:
    """向 stdout 输出 JSON（Agent 读取的标准输出格式）。"""
    setup_stdio()
    print(json.dumps(obj, ensure_ascii=False, indent=2, default=str))


def rnd(x: float | None, n: int = 4) -> float | None:
    return None if x is None else round(float(x), n)


OUTCOMES = ("win", "draw", "loss")           # 主队视角：胜 / 平 / 负
OUTCOME_ZH = {"win": "胜", "draw": "平", "loss": "负"}
OUTCOME_CODE = {"win": "3", "draw": "1", "loss": "0"}   # 传统足彩开奖代码
CODE_OUTCOME = {v: k for k, v in OUTCOME_CODE.items()}
RISK_ZH = {"LOW": "低", "MEDIUM": "中", "HIGH": "高", "UNKNOWN": "数据不足"}


def picks_zh(picks: list[str]) -> str:
    return "".join(OUTCOME_ZH[p] for p in OUTCOMES if p in picks)
