#!/usr/bin/env python
"""通用安装器：把 Ren9 Analyst 安装到某个 Agent 的 Skills 目录。

示例：
    python install.py --agent claude                 # ~/.claude/skills/ren9-analyst
    python install.py --agent claude --project .     # <项目>/.claude/skills/ren9-analyst
    python install.py --agent codex                  # ~/.agents/skills/ren9-analyst
    python install.py --target /path/to/skills       # 任意 Agent：指定其 Skills 目录
    python install.py --agent hermes --mode link     # 用符号链接（便于开发时同步更新）
    python install.py --list

OpenClaw / Hermes 等 Agent 的 Skills 目录随版本不同而变化，默认路径不对时请用 --target 指定。
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
NAME = "ren9-analyst"

# 各 Agent 的用户级 Skills 目录（默认值，可用 --target 覆盖）与项目级目录
AGENTS = {
    "claude": {"user": "~/.claude/skills", "project": ".claude/skills"},
    "codex": {"user": "~/.agents/skills", "project": ".agents/skills"},
    "cursor": {"user": "~/.cursor/skills", "project": ".cursor/skills"},
    "gemini": {"user": "~/.gemini/skills", "project": ".gemini/skills"},
    "openclaw": {"user": "~/.openclaw/skills", "project": "skills"},
    "hermes": {"user": "~/.hermes/skills", "project": ".hermes/skills"},
    "generic": {"user": "~/.agents/skills", "project": ".agents/skills"},
}

IGNORE = shutil.ignore_patterns(
    "__pycache__", "*.pyc", ".git", ".venv", "venv", ".pytest_cache", ".idea", ".vscode",
    "cache", "history", "predictions", "results", "*.sqlite", "*.db", "dist",
)


def skills_dir(agent: str, project: str | None, target: str | None) -> Path:
    if target:
        return Path(target).expanduser().resolve()
    cfg = AGENTS[agent]
    if project:
        return (Path(project).expanduser().resolve() / cfg["project"])
    return Path(cfg["user"]).expanduser()


def install(dest_root: Path, mode: str, force: bool) -> Path:
    dest = dest_root / NAME
    if dest.resolve() == ROOT:
        print(f"已经位于目标目录：{dest}")
        return dest
    if dest.exists() or dest.is_symlink():
        if not force:
            raise SystemExit(f"目标已存在：{dest}\n如需覆盖请加 --force（会保留其中的 data/ 目录）")
        keep = None
        if dest.is_dir() and not dest.is_symlink() and (dest / "data").exists():
            keep = dest_root / f".{NAME}-data-backup"
            if keep.exists():
                shutil.rmtree(keep)
            shutil.move(str(dest / "data"), str(keep))
        if dest.is_symlink() or dest.is_file():
            dest.unlink()
        else:
            shutil.rmtree(dest)
    else:
        keep = None
    dest_root.mkdir(parents=True, exist_ok=True)
    if mode == "link":
        try:
            os.symlink(ROOT, dest, target_is_directory=True)
        except OSError as exc:
            raise SystemExit(f"创建符号链接失败（Windows 需要开发者模式或管理员权限）：{exc}\n请改用 --mode copy")
    else:
        shutil.copytree(ROOT, dest, ignore=IGNORE)
        for sub in ("cache", "history", "predictions", "results"):
            (dest / "data" / sub).mkdir(parents=True, exist_ok=True)
        if keep:
            shutil.rmtree(dest / "data")
            shutil.move(str(keep), str(dest / "data"))
    return dest


def main() -> None:
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8")
        except Exception:
            pass
    p = argparse.ArgumentParser(description="安装 Ren9 Analyst 到 Agent 的 Skills 目录")
    p.add_argument("--agent", choices=sorted(AGENTS), default="generic")
    p.add_argument("--project", help="安装到某个项目内（项目级 Skill），值为项目根目录")
    p.add_argument("--target", help="直接指定 Skills 目录（优先于 --agent / --project）")
    p.add_argument("--mode", choices=["copy", "link"], default="copy")
    p.add_argument("--force", action="store_true", help="覆盖已有安装（保留 data/）")
    p.add_argument("--deps", action="store_true", help="同时安装 Python 依赖（pip install -r requirements.txt）")
    p.add_argument("--list", action="store_true", help="列出各 Agent 的默认 Skills 目录")
    args = p.parse_args()
    if args.list:
        for a, cfg in AGENTS.items():
            print(f"{a:9s} 用户级 {cfg['user']:22s} 项目级 <项目>/{cfg['project']}")
        return
    if sys.version_info < (3, 10):
        raise SystemExit("需要 Python 3.10 及以上版本")
    dest = install(skills_dir(args.agent, args.project, args.target), args.mode, args.force)
    print(f"已安装：{dest}")
    if args.deps:
        subprocess.check_call([sys.executable, "-m", "pip", "install", "-r", str(dest / "requirements.txt")])
    else:
        print(f"安装依赖：{sys.executable} -m pip install -r \"{dest / 'requirements.txt'}\"")
    print(f"自检：{sys.executable} \"{dest / 'scripts' / 'check_sources.py'}\"")
    print("然后对 Agent 说：帮我分析今天的任九，自动推荐一套方案。")


if __name__ == "__main__":
    main()
