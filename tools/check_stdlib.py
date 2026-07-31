#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""stdlib番人: 引数のPythonファイルが標準ライブラリ以外をimportしていたら exit 1
（server/ の「クローンして即動く」価値を機械強制する）
※同じディレクトリにある .py（例: relay_agent.py → office_server）は同梱物なので許可する。"""
import ast
import sys
from pathlib import Path


def check(path):
    tree = ast.parse(open(path, encoding="utf-8").read())
    # 同ディレクトリのローカルモジュール名は「同梱物」＝外部依存ではない
    local = {p.stem for p in Path(path).resolve().parent.glob("*.py")}
    mods = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            mods |= {a.name.split(".")[0] for a in n.names}
        elif isinstance(n, ast.ImportFrom) and n.module and n.level == 0:
            mods.add(n.module.split(".")[0])
    return [m for m in sorted(mods)
            if m not in sys.stdlib_module_names and m not in local]


def main():
    # --allow name1,name2 : 別ディレクトリの同梱モジュールを許可（例: tools/office_send.py→office_server）
    allow = set()
    args = list(sys.argv[1:])
    if args and args[0] == "--allow":
        allow = set(args[1].split(","))
        args = args[2:]
    bad = False
    for path in args:
        extras = [m for m in check(path) if m not in allow]
        if extras:
            print(f"✗ {path}: 標準ライブラリ外のimport: {', '.join(extras)}")
            bad = True
    if bad:
        sys.exit(1)
    print("✓ stdlib番人: 全ファイル標準ライブラリのみ")


if __name__ == "__main__":
    main()
