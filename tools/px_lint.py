#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""R98-W2: フロア帯のドット絵（文字グリッド）の番人。

なぜ文字グリッドか: 本番のロボは **PNG を持たない**。`ui/pixel/px/*.js` に 16×24 の文字グリッドを
置き、描画時にパレットを当てる。理由は 3 つ。
  ① diff が読める（1 ドット変えた差分がレビューできる）
  ② golden が決定論（画像デコーダの差が入らない）
  ③ 生成画像をそのまま貼らない（本人裁定: 生成画像は**ムードボードと量子化の入力にだけ**使う）

検査するもの:
  - セルは 16×24 ちょうど・文字は凡例の中だけ
  - 1 セルの色数 ≤8（パレットの表現力を超えない）
  - 輪郭が枠からはみ出していない（右端・左端の列が透明でないセルを弾く＝隣の机と重なる）
  - 3 ベンダー × 14 コマ = 42 セルが揃っている（`ui/core/pxpose.js` の CELLS が正本）
  - 歩行 4 コマは隣り合うコマ同士で ≥6 ドット違う（「歩いていない歩行」を弾く）
  - `ui/pixel/**` に PNG が 0 個・文字グリッドの合計 ≤80KB

使い方:
    python3 tools/px_lint.py            # 検査（verify ▶3b から呼ぶ）
    python3 tools/px_lint.py --list     # 見つけたセルの一覧
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PX_DIR = ROOT / "ui" / "pixel"
GRID_DIR = PX_DIR / "px"
WIDTH, HEIGHT = 16, 24
MAX_COLORS = 8
MAX_BYTES = 80 * 1024
# 凡例（ui/pixel/px/palette.js と 1:1）。`.` は透明。
LEGEND = set(".osfeahAB")


def cells_from(source):
    """`name: ["....", ...]` の並びを {name: [行, ...]} に読む（JS を実行しない）。"""
    out = {}
    for match in re.finditer(r"(\w+)\s*:\s*\[([^\]]*)\]", source, re.S):
        rows = re.findall(r'"([^"]*)"', match.group(2))
        if rows:
            out[match.group(1)] = rows
    return out


def load_cells():
    grids = {}
    if not GRID_DIR.is_dir():
        return grids
    for path in sorted(GRID_DIR.glob("*.js")):
        if path.name == "palette.js":
            continue
        grids[path.stem] = cells_from(path.read_text(encoding="utf-8"))
    return grids


def expected_cells():
    """コマ表の正本は ui/core/pxpose.js（二重管理を作らない）。"""
    source = (ROOT / "ui" / "core" / "pxpose.js").read_text(encoding="utf-8")
    cells = re.search(r"export const CELLS = Object\.freeze\(\[(.*?)\]\)", source, re.S)
    vendors = re.search(r"export const VENDORS = Object\.freeze\(\[(.*?)\]\)", source, re.S)
    if not cells or not vendors:
        raise SystemExit("✗ ui/core/pxpose.js から CELLS / VENDORS を読めない")
    return re.findall(r'"([^"]+)"', cells.group(1)), re.findall(r'"([^"]+)"', vendors.group(1))


def diff_dots(a, b):
    return sum(1 for ra, rb in zip(a, b) for ca, cb in zip(ra, rb) if ca != cb)


def main():
    names, vendors = expected_cells()
    grids = load_cells()
    bad = []
    total = 0

    pngs = [p for p in PX_DIR.rglob("*") if p.suffix.lower() in (".png", ".webp", ".jpg", ".gif")]
    if pngs:
        bad.append(f"ui/pixel に画像ファイルがある（文字グリッドだけで作る掟）: "
                   + ", ".join(p.relative_to(ROOT).as_posix() for p in pngs[:5]))

    if not grids:
        print("  ・ 文字グリッドはまだ無い（ui/pixel/px/*.js）＝検査するものが無い")
        return 1 if bad else 0

    for path in sorted(GRID_DIR.glob("*.js")):
        total += len(path.read_bytes())
    if total > MAX_BYTES:
        bad.append(f"文字グリッドの合計が {total // 1024}KB（上限 {MAX_BYTES // 1024}KB）")

    # 完全性（42 セル）は「帯を実際に描き始めたら」必須にする。作りかけの基準セルだけの間は
    # 形・色・枠の検査はするが「足りない」では落とさない（門を先に置く流儀を保ったまま赤を作らない）。
    wired = (PX_DIR / "strip.js").is_file()
    complete_required = wired or all(v in grids for v in vendors)
    made = 0
    for vendor in vendors:
        cells = grids.get(vendor)
        if cells is None:
            if complete_required:
                bad.append(f"ベンダー {vendor} の文字グリッド（ui/pixel/px/{vendor}.js）が無い")
            continue
        made += len(cells)
        missing = [n for n in names if n not in cells]
        if missing and complete_required:
            bad.append(f"{vendor}: コマが足りない {' '.join(missing)}")
        for name, rows in cells.items():
            where = f"{vendor}.{name}"
            if len(rows) != HEIGHT or any(len(r) != WIDTH for r in rows):
                bad.append(f"{where}: {WIDTH}×{HEIGHT} でない（{len(rows)} 行）")
                continue
            used = {c for r in rows for c in r}
            unknown = used - LEGEND
            if unknown:
                bad.append(f"{where}: 凡例に無い文字 {''.join(sorted(unknown))}")
            if len(used - {"."}) > MAX_COLORS:
                bad.append(f"{where}: 色数 {len(used - {'.'})}（上限 {MAX_COLORS}）")
            if any(r[0] != "." or r[-1] != "." for r in rows):
                bad.append(f"{where}: 輪郭が左右の枠に触れている（隣の机と重なる）")
        extra = [n for n in cells if n not in names]
        if extra:
            bad.append(f"{vendor}: コマ表に無い名前 {' '.join(extra)}（正本= ui/core/pxpose.js）")
        walk = [cells[n] for n in names if n.startswith("walk") and n in cells]
        for i in range(len(walk) - 1):
            if diff_dots(walk[i], walk[i + 1]) < 6:
                bad.append(f"{vendor}: 歩行コマ {i}→{i + 1} の差が小さい（歩いて見えない）")

    if "--list" in sys.argv:
        for vendor, cells in sorted(grids.items()):
            print(f"  {vendor}: {len(cells)} コマ（{' '.join(sorted(cells))}）")

    if bad:
        print(f"✗ ドット絵 lint: {len(bad)} 件")
        for line in bad[:12]:
            print("   " + line)
        return 1
    want = len(vendors) * len(names)
    state = "" if made >= want else f"（作りかけ {made}/{want}・帯の配線で完全性が必須になる）"
    print(f"✓ ドット絵 lint: {made} セル / {total // 1024}KB・PNG 0 個{state}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
