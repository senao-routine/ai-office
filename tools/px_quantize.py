#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""R98-W2: 画像 → 16×24 の**文字グリッド**（ui/pixel/px/*.js の素材）へ機械変換する。

本人裁定（2026-09-18）: 「gpt image でドット絵のキャラを作るなら、**コードから実際のドットの構成で**」。
つまり生成画像をそのまま製品に貼らない。生成画像は**ムードボード**（雰囲気・配色・シルエットの当たり）
として使い、そこから機械でドットを起こす。ここがその機械。

やること:
  1. 入力画像を最近傍で 16×24 へ縮小（ぼかさない＝ドットの角を残す）
  2. 透明・白背景を落とす（背景 → `.`）
  3. 残った色を k-means ではなく**固定パレット**（ui/pixel/px/palette.js の 8 色）へ最近傍で割り当て
     ＝生成画像の色をそのまま持ち込まない（art-direction v3 の寒色を外さない）
  4. 文字グリッド（凡例 1 文字 = 1 ドット）として出力

使い方:
    python3 tools/px_quantize.py <画像> [--name idle0] [--vendor claude] [--threshold 0.5]
    python3 tools/px_quantize.py <画像> --preview     # 端末に ASCII で出す（貼る前に見る）

依存: Pillow（tools/ 配下なので外部依存可。server/ は stdlib のみの掟は不変）。
"""
import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WIDTH, HEIGHT = 16, 24

# 凡例 → 基準色（art-direction v3 §2 の hex。palette.js と同じ順・同じ値）
# o=輪郭 s=殻 f=顔プレート e=目 a=アクセント h=影 A=強調1 B=強調2
LEGEND = [
    ("o", (0x23, 0x21, 0x3a)),      # ink
    ("s", (0xe9, 0xe0, 0xd5)),      # shell-claude
    ("f", (0x26, 0x26, 0x26)),      # face
    ("e", (0xdf, 0xe9, 0xff)),      # 画面の発光（目）
    ("a", (0x7c, 0x5c, 0xff)),      # accent
    ("h", (0x9a, 0x9a, 0xb4)),      # glass-rail（影・金属）
    ("A", (0x4f, 0x8d, 0xff)),      # accent-2
    ("B", (0x4f, 0xb3, 0xa3)),      # working（胸リング）
]


# ベンダー別の殻（ui/pixel/px/palette.js の SHELL と同じ値）。`s` は描画時に差し替わるので、
# 量子化のときも**そのベンダーの殻色**で近さを測らないと、Codex の灰色が影（h）に化ける。
SHELL = {"claude": (0xe9, 0xe0, 0xd5), "codex": (0x78, 0x76, 0x76), "openclaw": (0xbb, 0x48, 0x38)}


def nearest(rgb, vendor="claude"):
    best, dist = ".", None
    for ch, ref in LEGEND:
        if ch == "s":
            ref = SHELL.get(vendor, SHELL["claude"])
        d = sum((a - b) ** 2 for a, b in zip(rgb, ref))
        if dist is None or d < dist:
            best, dist = ch, d
    return best


def quantize(path, threshold=0.5, vendor="claude"):
    try:
        from PIL import Image
    except ImportError:
        raise SystemExit("✗ Pillow が要る: pip install Pillow（tools/ は外部依存可）")
    img = Image.open(path).convert("RGBA")
    # 正方形でない入力は中央を 2:3 で切ってから縮める（16×24 の比に合わせる）
    w, h = img.size
    want = WIDTH / HEIGHT
    if w / h > want:
        new_w = int(h * want)
        img = img.crop(((w - new_w) // 2, 0, (w + new_w) // 2, h))
    else:
        new_h = int(w / want)
        img = img.crop((0, (h - new_h) // 2, w, (h + new_h) // 2))
    small = img.resize((WIDTH, HEIGHT), Image.NEAREST)
    px = small.load()
    rows = []
    for y in range(HEIGHT):
        row = []
        for x in range(WIDTH):
            r, g, b, a = px[x, y]
            # 透明・ほぼ白は背景（生成画像の白地をそのまま殻にしない）
            if a < 255 * threshold or (r > 240 and g > 240 and b > 240):
                row.append(".")
            else:
                row.append(nearest((r, g, b), vendor))
        rows.append("".join(row))
    return rows


def main():
    ap = argparse.ArgumentParser(description="画像を 16×24 の文字グリッドへ量子化する")
    ap.add_argument("image")
    ap.add_argument("--name", default="idle0", help="コマ名（ui/core/pxpose.js の CELLS）")
    ap.add_argument("--vendor", default="claude")
    ap.add_argument("--threshold", type=float, default=0.5)
    ap.add_argument("--preview", action="store_true", help="端末に見える形で出す（貼らない）")
    args = ap.parse_args()

    rows = quantize(Path(args.image), args.threshold, args.vendor)
    if args.preview:
        for row in rows:
            print("  " + row.replace(".", "·"))
        used = {c for r in rows for c in r} - {"."}
        print(f"  使った色: {''.join(sorted(used))}（{len(used)} 色・上限 8）")
        return 0
    print(f"  // {args.vendor} / {args.name}（tools/px_quantize.py・入力 {Path(args.image).name}）")
    print(f"  {args.name}: [")
    for row in rows:
        print(f'    "{row}",')
    print("  ],")
    return 0


if __name__ == "__main__":
    sys.exit(main())
