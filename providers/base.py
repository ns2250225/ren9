"""HTTP 访问与本地缓存（SQLite）。所有 provider 通过这里取数，保证每条数据带来源与抓取时间。"""
from __future__ import annotations

import os
import sqlite3
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Callable

from ren9.common import data_dir, now_iso, settings

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)


class ProviderError(Exception):
    """数据源不可用或返回内容无法解析。"""


@dataclass
class Fetched:
    url: str
    text: str
    fetched_at: str          # 实际从网络抓取的时间
    from_cache: bool = False
    stale: bool = False      # 网络失败后使用的过期缓存

    def source(self, name: str) -> dict:
        return {
            "name": name,
            "url": self.url,
            "fetched_at": self.fetched_at,
            "from_cache": self.from_cache,
            "stale": self.stale,
        }


# 测试可替换：fetcher(url, headers, timeout) -> str
_fetcher: Callable[[str, dict, float], str] | None = None


def set_fetcher(fn: Callable[[str, dict, float], str] | None) -> None:
    global _fetcher
    _fetcher = fn


def _network_get(url: str, headers: dict, timeout: float) -> str:
    if os.environ.get("REN9_OFFLINE") == "1":
        raise ProviderError(f"offline mode: {url}")
    import requests

    resp = requests.get(url, headers=headers, timeout=timeout)
    if resp.status_code != 200:
        raise ProviderError(f"HTTP {resp.status_code}: {url}")
    if resp.encoding is None or resp.encoding.lower() == "iso-8859-1":
        resp.encoding = "utf-8"
    return resp.text


def _db() -> sqlite3.Connection:
    conn = sqlite3.connect(data_dir() / "cache" / "http_cache.sqlite", timeout=30)
    conn.execute(
        "CREATE TABLE IF NOT EXISTS http_cache ("
        "url TEXT PRIMARY KEY, body TEXT NOT NULL, fetched_at TEXT NOT NULL, ts REAL NOT NULL)"
    )
    return conn


def cache_get(url: str) -> tuple[str, str, float] | None:
    conn = _db()
    try:
        row = conn.execute(
            "SELECT body, fetched_at, ts FROM http_cache WHERE url=?", (url,)
        ).fetchone()
    finally:
        conn.close()
    return row


def cache_put(url: str, body: str) -> str:
    fetched_at = now_iso()
    conn = _db()
    try:
        conn.execute(
            "INSERT OR REPLACE INTO http_cache(url, body, fetched_at, ts) VALUES (?,?,?,?)",
            (url, body, fetched_at, time.time()),
        )
        conn.commit()
    finally:
        conn.close()
    return fetched_at


def http_get(
    url: str,
    ttl: float | None = None,
    headers: dict | None = None,
    refresh: bool = False,
    allow_stale: bool = True,
    validate: Callable[[str], bool] | None = None,
    retries: int | None = None,
    timeout: float | None = None,
) -> Fetched:
    """GET 并缓存。

    ttl: 缓存有效期（秒）；refresh=True 时强制联网。
    网络失败且存在旧缓存时返回 stale=True 的结果（由调用方决定是否可信）。
    validate: 返回 False 表示响应内容无效（如维护页），不写入缓存。
    """
    cfg = settings().get("data", {})
    if ttl is None:
        ttl = cfg.get("cache_ttl", 3600)
    use_cache = cfg.get("cache_enabled", True)
    cached = cache_get(url) if use_cache else None
    if cached and not refresh and (time.time() - cached[2]) <= ttl:
        return Fetched(url, cached[0], cached[1], from_cache=True)

    hdrs = {"User-Agent": USER_AGENT, "Accept": "*/*"}
    hdrs.update(headers or {})
    if timeout is None:
        timeout = cfg.get("http_timeout", 20)
    if retries is None:
        retries = cfg.get("http_retries", 2)
    fetch = _fetcher or _network_get
    last_err: Exception | None = None
    for attempt in range(retries + 1):
        try:
            text = fetch(url, hdrs, timeout)
            if validate and not validate(text):
                raise ProviderError(f"invalid response content: {url}")
            fetched_at = cache_put(url, text) if use_cache else now_iso()
            return Fetched(url, text, fetched_at)
        except Exception as exc:  # noqa: BLE001 - 统一降级处理
            last_err = exc
            if _fetcher is not None or os.environ.get("REN9_OFFLINE") == "1":
                break
            if attempt < retries:
                time.sleep(0.8 * (attempt + 1))
    if cached and allow_stale:
        return Fetched(url, cached[0], cached[1], from_cache=True, stale=True)
    raise ProviderError(f"{type(last_err).__name__}: {last_err}")


def age_seconds(fetched_at: str) -> float:
    try:
        return (datetime.fromisoformat(now_iso()) - datetime.fromisoformat(fetched_at)).total_seconds()
    except ValueError:
        return float("inf")
