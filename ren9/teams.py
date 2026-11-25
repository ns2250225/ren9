"""球队名解析：中文名 → 英文名，以及不同数据源之间的英文名匹配。"""
from __future__ import annotations

import re
import unicodedata
from difflib import SequenceMatcher

from ren9.common import data_dir, load_yaml, now_iso, read_json, write_json

_STOP = {
    "fc", "cf", "afc", "sc", "ac", "as", "ss", "ssc", "us", "cd", "ud", "rc", "rcd", "sv", "vfb", "vfl",
    "tsg", "fsv", "bk", "fk", "sk", "if", "ff", "club", "de", "the", "1", "05", "04", "1846", "98", "29",
    "calcio", "bsc", "aj", "sco", "ogc", "losc", "kv", "krc", "rsc",
}


def norm(name: str) -> str:
    s = unicodedata.normalize("NFKD", name or "")
    s = "".join(c for c in s if not unicodedata.combining(c)).lower()
    s = s.replace("ø", "o").replace("&", " and ").replace("'", "").replace("’", "")
    s = re.sub(r"[^a-z0-9]+", " ", s)
    toks = [t for t in s.split() if t not in _STOP and t != "and"]
    return " ".join(toks) if toks else s.strip()


def _subseq(short: str, long: str) -> bool:
    """short 是否为 long 的同首字母子序列（Man→Manchester, Weds→Wednesday）。"""
    if not short or not long or short[0] != long[0]:
        return False
    it = iter(long)
    return all(ch in it for ch in short)


def _abbrev(a: str, b: str) -> bool:
    """a 的每个词都能按顺序对应到 b 的某个词（相等或缩写）。"""
    ta, tb = a.split(), b.split()
    if not ta or len(ta) > len(tb):
        return False
    j = 0
    for t in ta:
        while j < len(tb) and not (t == tb[j] or (len(t) >= 3 and _subseq(t, tb[j]))):
            j += 1
        if j == len(tb):
            return False
        j += 1
    return True


def name_score(a: str, b: str) -> float:
    """两个英文队名的相似度：1.0 精确；0.9 缩写/子集；否则为字符相似度。"""
    na, nb = norm(a), norm(b)
    if not na or not nb:
        return 0.0
    if na == nb:
        return 1.0
    if _abbrev(na, nb) or _abbrev(nb, na):
        return 0.9
    return SequenceMatcher(None, na, nb).ratio() * 0.85


def best_match(names: list[str], candidates: list[str], threshold: float = 0.80) -> str | None:
    """在 candidates 中找与 names（同一球队的多种写法）最匹配且唯一的名字。"""
    scored = []
    for c in candidates:
        s = max((name_score(n, c) for n in names if n), default=0.0)
        scored.append((s, c))
    scored.sort(reverse=True)
    if not scored or scored[0][0] < threshold:
        return None
    if len(scored) > 1 and scored[1][0] == scored[0][0] and scored[0][0] < 1.0:
        return None   # 有歧义时宁可不匹配
    return scored[0][1]


class TeamResolver:
    """中文名 → {en: [...], fd, elo}。内置表 + 运行时学习表。"""

    def __init__(self) -> None:
        self._by_zh: dict[str, dict] = {}
        for t in load_yaml("team_aliases.yaml").get("teams") or []:
            entry = {
                "en": list(t.get("en") or []),
                "fd": t.get("fd"),
                "elo": t.get("elo"),
                "origin": "builtin",
            }
            for zh in t.get("zh") or []:
                self._by_zh[self._key(zh)] = entry
        self._learned_path = data_dir() / "cache" / "learned_aliases.json"
        self._learned: dict[str, dict] = read_json(self._learned_path, {}) or {}

    @staticmethod
    def _key(zh: str) -> str:
        return "".join((zh or "").split()).lower()

    def lookup(self, zh: str) -> dict | None:
        k = self._key(zh)
        if k in self._by_zh:
            return self._by_zh[k]
        if k in self._learned:
            e = self._learned[k]
            return {"en": [e["en"]], "fd": None, "elo": None, "origin": e.get("origin", "learned")}
        return None

    def learn(self, zh: str, en: str, origin: str) -> None:
        """记录由对阵配对推断出的映射（origin 说明推断依据）。"""
        k = self._key(zh)
        if k in self._by_zh or not en:
            return
        self._learned[k] = {"zh": zh, "en": en, "origin": origin, "learned_at": now_iso()}
        write_json(self._learned_path, self._learned)

    def names_for(self, zh: str, provider: str | None = None) -> list[str]:
        """某个数据源下可能的英文写法列表（最优先的在前）。"""
        e = self.lookup(zh)
        if not e:
            return []
        out = []
        if provider and e.get(provider):
            out.append(e[provider])
        out.extend(e["en"])
        return out
