#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""server/ が **素の Mac の python3**（/usr/bin/python3 = 3.9.6）で import できることを守る番人。

なぜ要るか（2026-09-17 の実測）:
  `def state_of(sid, now) -> dict | None:` のような PEP 604 の注釈は、
  `from __future__ import annotations` が無いと **def の実行時に評価される**ので
  Python 3.9 では import 時に TypeError で落ちる。実測では office_events.py の 2 行だけで
  office_server / mcp_office / relay_agent / office_timeline が連鎖して死んでいた
  ＝ **Homebrew の python3 を入れていない Mac では AI Office が 1 秒も動かない**。
  開発機は 3.14 なので気づけず、verify ▶12 の「素の Mac の python3」検査は封書だけを見ていた。

検査するもの（AST・実行しない）:
  1. 注釈（引数・戻り値・変数）に `X | Y` を使っていない（`from __future__ import annotations` があれば許す）
  2. 3.10 以降でしか無い構文（match 文）を使っていない
  3. 3.9 に無い標準ライブラリ（tomllib / graphlib の一部）を import していない
"""
import ast
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
TARGETS = sorted((ROOT / "server").glob("*.py"))
NEWER_STDLIB = {"tomllib"}          # 3.11+
MIN = (3, 9)


def has_future_annotations(tree):
    for node in tree.body:
        if isinstance(node, ast.ImportFrom) and node.module == "__future__":
            if any(a.name == "annotations" for a in node.names):
                return True
    return False


def annotation_nodes(tree):
    """注釈として書かれた式だけを集める（実行される位置にあるもの）。"""
    out = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.returns is not None:
                out.append(node.returns)
            args = node.args
            for a in [*args.posonlyargs, *args.args, *args.kwonlyargs, args.vararg, args.kwarg]:
                if a is not None and a.annotation is not None:
                    out.append(a.annotation)
        elif isinstance(node, ast.AnnAssign) and node.annotation is not None:
            out.append(node.annotation)
    return out


def main():
    bad = []
    for path in TARGETS:
        src = path.read_text(encoding="utf-8")
        rel = path.relative_to(ROOT)
        try:
            tree = ast.parse(src, filename=str(path))
        except SyntaxError as exc:                      # 3.10+ の構文（match 等）はここで出る
            bad.append(f"{rel}:{exc.lineno} 構文が {MIN[0]}.{MIN[1]} で読めない: {exc.msg}")
            continue
        if not has_future_annotations(tree):
            for ann in annotation_nodes(tree):
                for node in ast.walk(ann):
                    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr):
                        bad.append(
                            f"{rel}:{node.lineno} 注釈の `X | Y` は 3.9 で落ちる"
                            "（`Optional[...]` か `from __future__ import annotations`）")
        for node in ast.walk(tree):
            if isinstance(node, ast.Match if hasattr(ast, "Match") else ()):
                bad.append(f"{rel}:{node.lineno} match 文は 3.10 以降")
            mod = None
            if isinstance(node, ast.Import):
                mod = [a.name.split(".")[0] for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                mod = [node.module.split(".")[0]]
            for m in mod or []:
                if m in NEWER_STDLIB:
                    bad.append(f"{rel}:{node.lineno} {m} は 3.9 に無い")
    if bad:
        print("✗ 素の Mac の python3（3.9）で落ちる書き方:")
        for b in bad:
            print(f"    {b}")
        return 1
    print(f"✓ py39 番人: server/ {len(TARGETS)} ファイル（素の Mac の python3 で import できる書き方）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
