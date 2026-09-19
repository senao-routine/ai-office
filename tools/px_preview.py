#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""R98-W2: 文字グリッドのコンタクトシート（本人が 1 回だけ見る唯一の主観ゲート）。

42 セル（3 ベンダー × 14 コマ）を ×6 に拡大して 1 枚に並べる。これを本人が見て OK が出るまで
帯は配線しない（プラン §2「品質 3 段」の ①）。ここで落ちたら差し戻し、2 回で通らなければ
発注か自作へ退避する（契約＝16×24・コマ表は変えない）。

使い方:
    python3 tools/px_preview.py                     # dist/px_contact.png を作る
    python3 tools/px_preview.py --ascii             # 端末に出す（画像を作らない）
    python3 tools/px_preview.py --cell claude.idle0 # 1 セルだけ大きく見る
"""
import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
GRID_DIR = ROOT / "ui" / "pixel" / "px"
OUT = ROOT / "dist" / "px_contact.png"
SCALE = 6
PAD = 8
LABEL_H = 10


def palette():
    """凡例 → RGB。正本は ui/pixel/px/palette.js（色を二重に持たない）。"""
    source = (GRID_DIR / "palette.js").read_text(encoding="utf-8")
    block = re.search(r"export const PALETTE = Object\.freeze\(\{(.*?)\}\)", source, re.S)
    if not block:
        raise SystemExit("✗ palette.js の PALETTE を読めない")
    out = {}
    for key, hexval in re.findall(r"(\w+):\s*\"#([0-9a-fA-F]{6})\"", block.group(1)):
        out[key] = tuple(int(hexval[i:i + 2], 16) for i in (0, 2, 4))
    shell = re.search(r"export const SHELL = Object\.freeze\(\{(.*?)\}\)", source, re.S)
    shells = {k: tuple(int(v[i:i + 2], 16) for i in (0, 2, 4))
              for k, v in re.findall(r"(\w+):\s*\"#([0-9a-fA-F]{6})\"", shell.group(1))} if shell else {}
    return out, shells


def cells():
    out = {}
    if not GRID_DIR.is_dir():
        return out
    for path in sorted(GRID_DIR.glob("*.js")):
        if path.name == "palette.js":
            continue
        source = path.read_text(encoding="utf-8")
        for match in re.finditer(r"(\w+)\s*:\s*\[([^\]]*)\]", source, re.S):
            rows = re.findall(r'"([^"]*)"', match.group(2))
            if rows:
                out[f"{path.stem}.{match.group(1)}"] = rows
    return out


def order(names):
    """コマ表の順（ui/core/pxpose.js）で並べる。見比べるときに順番が揺れない。"""
    source = (ROOT / "ui" / "core" / "pxpose.js").read_text(encoding="utf-8")
    cell_names = re.findall(r'"([^"]+)"',
                            re.search(r"export const CELLS = Object\.freeze\(\[(.*?)\]\)", source, re.S).group(1))
    vendors = re.findall(r'"([^"]+)"',
                         re.search(r"export const VENDORS = Object\.freeze\(\[(.*?)\]\)", source, re.S).group(1))
    rank = {f"{v}.{c}": (vi, ci) for vi, v in enumerate(vendors) for ci, c in enumerate(cell_names)}
    return sorted(names, key=lambda n: rank.get(n, (99, 99, n)))


def main():
    ap = argparse.ArgumentParser(description="文字グリッドのコンタクトシート")
    ap.add_argument("--ascii", action="store_true")
    ap.add_argument("--cell")
    ap.add_argument("--scale", type=int, default=SCALE)
    args = ap.parse_args()

    grids = cells()
    if not grids:
        print("  ・ 文字グリッドがまだ無い（ui/pixel/px/*.js）")
        return 0
    names = order([args.cell] if args.cell else list(grids))
    if args.cell and args.cell not in grids:
        raise SystemExit(f"✗ そのセルは無い: {args.cell}（ある: {' '.join(sorted(grids))}）")

    if args.ascii or args.cell:
        for name in names:
            print(f"  {name}")
            for row in grids[name]:
                print("    " + row.replace(".", "·"))
        return 0

    try:
        from PIL import Image, ImageDraw
    except ImportError:
        print("  ・ Pillow が無いので ASCII で出す（pip install Pillow で画像になる）")
        for name in names:
            print(f"  {name}")
            for row in grids[name]:
                print("    " + row.replace(".", "·"))
        return 0

    pal, shells = palette()
    w, h = len(grids[names[0]][0]), len(grids[names[0]])
    cols = 14                                   # 1 行 = 1 ベンダーの 14 コマ
    cw, ch = w * args.scale + PAD, h * args.scale + PAD + LABEL_H
    rows_n = (len(names) + cols - 1) // cols
    img = Image.new("RGB", (cols * cw, rows_n * ch), (244, 244, 251))
    draw = ImageDraw.Draw(img)
    for i, name in enumerate(names):
        vendor = name.split(".")[0]
        ox, oy = (i % cols) * cw + PAD // 2, (i // cols) * ch + PAD // 2
        draw.text((ox, oy), name.split(".")[1], fill=(108, 104, 144))
        for y, row in enumerate(grids[name]):
            for x, chch in enumerate(row):
                if chch == ".":
                    continue
                color = shells.get(vendor, pal.get("s")) if chch == "s" else pal.get(chch, (255, 0, 255))
                draw.rectangle([ox + x * args.scale, oy + LABEL_H + y * args.scale,
                                ox + (x + 1) * args.scale - 1, oy + LABEL_H + (y + 1) * args.scale - 1],
                               fill=color)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    img.save(OUT)
    print(f"✓ コンタクトシート: {OUT.relative_to(ROOT)}（{len(names)} セル・×{args.scale}）")
    print("  本人がこれを 1 回見て OK が出るまで帯は配線しない（差し戻しは 2 回まで）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
