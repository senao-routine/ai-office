#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""CSS カスタムプロパティの「使っているのに定義が無い」を機械で落とす番人。

なぜ必要か（2026-09-08 実発生・R90-U3）:
  留守中ダイジェストのカードが `background: var(--paper)` を使っていたが、このプロジェクトの
  トークンは `--iso-glass` で `--paper` はどこにも定義されていなかった。CSS は未定義変数を
  **エラーにせず黙って無視する**ので、カードの背景だけが透明になり、3D の上に文字が直接
  重なって読めない状態のまま本番へ出た。テストもスモークも「DOM が在るか」しか見ていない。
  ＝目で見るまで分からない種類の壊れ方なので、機械で落とす。

  `var(--x, フォールバック)` は未定義でも意図どおり動くので対象外にする。

使い方: python3 tools/css_var_lint.py        （dev.sh --check と verify.sh ▶2b から呼ぶ）
"""
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
# 変数は :root だけでなく .th-dark や .ed-openclaw の上書きでも定義される＝ファイル全体を見る。
USE = re.compile(r"var\(\s*(--[A-Za-z0-9_-]+)\s*(,?)")
DEF = re.compile(r"(--[A-Za-z0-9_-]+)\s*:")


def check(path):
    css = path.read_text(encoding="utf-8")
    defined = set(DEF.findall(css))
    bad = {}
    for line_no, line in enumerate(css.splitlines(), 1):
        for name, fallback in USE.findall(line):
            if fallback or name in defined:
                continue                      # フォールバック付き／定義済みは問題ない
            bad.setdefault(name, []).append(line_no)
    return bad


def main():
    files = sorted(p for p in (ROOT / "ui").rglob("*.css") if "vendor" not in p.parts)
    ng = 0
    for path in files:
        bad = check(path)
        for name, lines in sorted(bad.items()):
            rel = path.relative_to(ROOT).as_posix()
            print(f"  ✗ {rel}:{lines[0]} 未定義のCSS変数 {name}"
                  f"（{len(lines)}箇所・フォールバックも無い＝黙って無視される）")
            ng += 1
    if ng:
        print(f"CSS変数lint: {ng} 件の未定義")
        return 1
    print(f"✓ CSS変数lint: {len(files)}ファイル 未定義なし")
    return 0


if __name__ == "__main__":
    sys.exit(main())
