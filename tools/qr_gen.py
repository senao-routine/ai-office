#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""pair_url を QRコードSVGにする（P6）。matrix生成は tools/vendor/segno（信頼ライブラリ・pure-python・
同梱＝pip不要）＝手書きエンコーダの「壊れQR」リスクを回避。SVGレンダリングだけ自前。

office_server.py が subprocess で呼ぶ（office_server の stdlib純度を保つため import しない）。
payload は **stdin** で受け取る（argv だと ps で secret が一瞬見える）。出力は SVG 文字列を stdout へ。
使い方:  echo -n '<pair_url>' | python3 qr_gen.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "vendor"))
from segno import encoder   # noqa: E402  vendored（tools/=deps可の領域）

QUIET = 4   # 静止ゾーン（4モジュール＝規格の推奨・スキャン成功率を担保）


def svg(payload, error="m"):
    code = encoder.encode(payload, error=error)   # ECC-M・版は自動選定
    m = code.matrix
    dim = len(m) + QUIET * 2
    # 黒モジュールを1本の path に畳む（要素数を抑え軽量）
    d = "".join(f"M{x + QUIET} {y + QUIET}h1v1h-1z"
                for y, row in enumerate(m) for x, c in enumerate(row) if c)
    return ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 %d %d" '
            'shape-rendering="crispEdges" role="img" aria-label="pairing QR">'
            '<rect width="%d" height="%d" fill="#fff"/>'
            '<path d="%s" fill="#000"/></svg>' % (dim, dim, dim, dim, d))


def main():
    payload = sys.stdin.read().strip()
    if not payload:
        print("usage: echo -n '<pair_url>' | qr_gen.py", file=sys.stderr)
        sys.exit(2)
    sys.stdout.write(svg(payload))


if __name__ == "__main__":
    main()
