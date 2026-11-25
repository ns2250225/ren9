#!/usr/bin/env python
"""把 Skill 打包成上传用的 zip：只包含 根目录/二级目录/文件 两级结构。

运行时数据（data/）、缓存、虚拟环境等不进包；data/ 会在首次运行时自动创建。

示例：
    python package.py                    # 生成 dist/ren9-analyst.zip（SKILL.md 位于压缩包根部）
    python package.py --wrap             # 压缩包内再套一层 ren9-analyst/ 目录
    python package.py --check            # 只检查并列出清单，不生成文件
"""
from __future__ import annotations

import argparse
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
NAME = "ren9-analyst"
SKIP_DIRS = {"data", "dist", ".git", ".venv", "venv", "__pycache__", ".pytest_cache", ".idea", ".vscode", ".claude"}
SKIP_FILES = {"prd.md", ".gitignore"}
SKIP_SUFFIX = {".pyc", ".pyo", ".zip", ".sqlite", ".db"}
MAX_DEPTH = 1          # 相对路径里最多一个 "/"：根目录/二级目录/文件


def collect() -> list[str]:
    """返回要打包的相对路径（POSIX 写法）。出现超过两级的文件时报错，而不是悄悄丢掉。"""
    files, too_deep = [], []
    for p in sorted(ROOT.rglob("*")):
        rel = p.relative_to(ROOT)
        if any(part in SKIP_DIRS for part in rel.parts[:-1]) or (p.is_dir() and p.name in SKIP_DIRS):
            continue
        if not p.is_file() or p.name in SKIP_FILES or p.suffix in SKIP_SUFFIX:
            continue
        posix = rel.as_posix()
        (too_deep if posix.count("/") > MAX_DEPTH else files).append(posix)
    if too_deep:
        raise SystemExit("以下文件超过两级目录，请先移到二级目录内：\n  " + "\n  ".join(too_deep))
    return files


def main() -> None:
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8")
        except Exception:
            pass
    ap = argparse.ArgumentParser(description="打包 Ren9 Analyst Skill（两级目录）")
    ap.add_argument("--wrap", action="store_true", help=f"压缩包内再套一层 {NAME}/ 目录")
    ap.add_argument("--check", action="store_true", help="只检查，不生成 zip")
    ap.add_argument("--out", default=str(ROOT / "dist" / f"{NAME}.zip"))
    args = ap.parse_args()
    files = collect()
    dirs = sorted({f.split("/")[0] for f in files if "/" in f})
    print(f"{len(files)} 个文件，二级目录：{', '.join(dirs)}")
    if args.check:
        print("\n".join(files))
        return
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for f in files:
            z.write(ROOT / f, f"{NAME}/{f}" if args.wrap else f)
    print(f"已生成：{out}（{out.stat().st_size / 1024:.0f} KB）")


if __name__ == "__main__":
    main()
