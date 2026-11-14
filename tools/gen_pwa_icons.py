#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""R99-P4-7: スマホの PWA のホーム画面アイコン（PNG）を**コードで描く**（AI の画像生成は使わない・stdlib のみ）。

なぜ: 旧アイコンは絵文字 🏢 の SVG の data URI だった。iOS のホーム画面（apple-touch-icon）は PNG しか使わず、
Android のインストールにも 192・512 の PNG が要る。色もマニフェスト・meta・アイコンの地で 3 通りに割れていた。

正本（ここに色や絵を書き写さない）:
  - 絵      : ui/cockpit/cells.js の claude.idle0（12×16 の文字グリッド・コックピットのロボと同じ 1 コマ）
  - 体の色  : ui/cockpit/palette.js の PALETTE の凡例（→ RAMP.bot）と dimTint の係数
  - 地の色  : ui/cockpit/palette.js の TERM.dark.bg（R101・端末の地＝マニフェストの theme_color・background_color・暗の meta）
  - 個体色  : ui/cockpit/palette.js の TERM.dark.accent（燐光グリーン 1 色。目と耳あて＝a、胸と足＝c は dimTint）
  - 明の meta: TERM.light.bg（ライトの地・media 付き）
出力: relay/src/pwa_icons.js（ESM・git 追跡）。Worker が /app/icon-*.png を image/png と ETag で配り、
      マニフェストの色（PWA_THEME）とアイコンの一覧もここから読む（worker.js に色を書かない）。

大きさ（整数倍＝ドットがにじまない）:
  - icon-192 / icon-512（purpose any）・icon-180（apple-touch-icon）: ロボの高さが一辺の約 8 割
  - icon-512-maskable: ロボの外接矩形の対角の半分が一辺の 40%（maskable の安全域＝中心からの円）に収まる最大の倍率

使い方:
    python3 tools/gen_pwa_icons.py           # 生成（中身が同じなら書き換えない）
    python3 tools/gen_pwa_icons.py --check   # 生成物と正本の一致（画素・寸法・ETag・色）＋ app.html の meta と link

--check は PNG の**画素**（IHDR・PLTE・展開した IDAT）で比べる。圧縮後のバイト列は zlib の版で変わりうる
（公開クローンの Python が別の zlib でも赤にしない）。ETag は収録したバイト列の SHA-256 と一致するかを見る。
"""
import base64
import hashlib
import json
import math
import re
import struct
import sys
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CELLS = ROOT / "ui" / "cockpit" / "cells.js"
PALETTE = ROOT / "ui" / "cockpit" / "palette.js"
TOKENS = ROOT / "ui" / "hud-tokens.css"
APP_HTML = ROOT / "ui" / "pwa" / "app.html"
OUT = ROOT / "relay" / "src" / "pwa_icons.js"

VENDOR, FRAME = "claude", "idle0"
# (配信パス, 一辺, purpose)。purpose=apple は apple-touch-icon 用（マニフェストには載せない）
ICONS = [
    ("/app/icon-192.png", 192, "any"),
    ("/app/icon-512.png", 512, "any"),
    ("/app/icon-512-maskable.png", 512, "maskable"),
    ("/app/icon-180.png", 180, "apple"),
]
FILL = 0.8            # any・apple: ロボの高さ ≒ 一辺 × FILL（整数倍に切り下げ）
MASKABLE_SAFE = 0.4   # maskable の安全域の半径（一辺に対する比・W3C の規定）
HEX = re.compile(r"^#[0-9a-f]{6}$")


def _die(msg):
    raise SystemExit(f"✗ gen_pwa_icons: {msg}")


def read_sprite():
    """cells.js から claude.idle0 の行を取る（12×16・凡例の文字だけ）。"""
    src = CELLS.read_text(encoding="utf-8")
    w = int(re.search(r"export const PX_CELL_W = (\d+);", src).group(1))
    h = int(re.search(r"export const PX_CELL_H = (\d+);", src).group(1))
    m = re.search(VENDOR + r": Object\.freeze\(\{\s*" + FRAME + r": \[(.*?)\]", src, re.S)
    if not m:
        _die(f"{CELLS.relative_to(ROOT)} に {VENDOR}.{FRAME} が無い")
    rows = re.findall(r'"([^"]*)"', m.group(1))
    if len(rows) != h or any(len(r) != w for r in rows):
        _die(f"{VENDOR}.{FRAME} が {w}×{h} でない（{len(rows)} 行）")
    return rows


def read_term():
    """palette.js の TERM（厳密な JSON の書き方・tools/gen_pwa_modules.py と同じ読み方）。"""
    pal = PALETTE.read_text(encoding="utf-8")
    m = re.search(r"export const TERM = Object\.freeze\((\{.*?\})\);\n", pal, re.S)
    if not m:
        _die(f"{PALETTE.relative_to(ROOT)} に TERM が無い")
    try:
        term = json.loads(m.group(1))
    except ValueError as e:
        _die(f"TERM が JSON として読めない: {e}")
    for th in ("dark", "light"):
        if not HEX.match(str(term.get(th, {}).get("bg", "")).lower()):
            _die(f"TERM.{th}.bg が #rrggbb でない")
    return term


def read_colors():
    """palette.js と hud-tokens.css から、凡例 → #rrggbb と 地の色を作る。"""
    pal = PALETTE.read_text(encoding="utf-8")
    ramp_m = re.search(r"export const RAMP = \{(.*?)\n\};", pal, re.S)
    legend_m = re.search(r"export const PALETTE = \{(.*?)\n\};", pal, re.S)
    k_m = re.search(r"function dimTint\(hex, k = ([0-9.]+)\)", pal)
    if not (ramp_m and legend_m and k_m):
        _die(f"{PALETTE.relative_to(ROOT)} の RAMP・PALETTE・dimTint が読めない")
    ramp = {name: re.findall(r'"(#[0-9a-fA-F]{6})"', body)
            for name, body in re.findall(r"^\s*(\w+):\s*\[([^\]]*)\]", ramp_m.group(1), re.M)}
    term = read_term()
    paper, accent = term["dark"]["bg"].lower(), term["dark"]["accent"].lower()
    legend = {}
    for ch, rhs in re.findall(r"^\s*(\w):\s*([^,\n]+),", legend_m.group(1), re.M):
        r = re.match(r"RAMP\.(\w+)\[(\d)\]", rhs.strip())
        lit = re.match(r'"(#[0-9a-fA-F]{6})"', rhs.strip())   # R128: 階段を通さない色（常駐 Bot の殻 s）は hex をそのまま書く
        legend[ch] = ramp[r.group(1)][int(r.group(2))].lower() if r else lit.group(1).lower() if lit else None
    k = float(k_m.group(1))
    # palette.js の dimTint と同じ計算（JS の Math.round＝正の値は floor(x+0.5)）
    n = int(accent[1:], 16)
    dim = "#" + "".join(f"{math.floor(((n >> s) & 255) * k + 0.5):02x}" for s in (16, 8, 0))
    legend["a"], legend["c"] = accent, dim
    if any(v is None or not HEX.match(v) for v in legend.values()):
        _die(f"凡例の色が決まらない: {legend}")
    return paper, legend


def scale_for(size, purpose, w, h):
    if purpose == "maskable":
        return math.floor(MASKABLE_SAFE * size / math.hypot(w / 2, h / 2))
    return math.floor(FILL * size / h)


def render(size, purpose, rows, paper, legend):
    """(IHDR, PLTE, 展開した IDAT) を作る。パレット PNG（8 bit・不透明＝iOS は透明を黒にする）。"""
    h, w = len(rows), len(rows[0])
    s = scale_for(size, purpose, w, h)
    ox, oy = (size - w * s) // 2, (size - h * s) // 2
    used = sorted({legend[ch] for row in rows for ch in row if ch != "."})
    colors = [paper] + [c for c in used if c != paper]
    index = {c: i for i, c in enumerate(colors)}
    grid = [[0] * size for _ in range(size)]
    for y, row in enumerate(rows):
        for x, ch in enumerate(row):
            if ch == ".":
                continue
            v = index[legend[ch]]
            for yy in range(oy + y * s, oy + (y + 1) * s):
                line = grid[yy]
                for xx in range(ox + x * s, ox + (x + 1) * s):
                    line[xx] = v
            if purpose == "maskable":        # ドットの四隅が全部、安全域の円の中
                c = size / 2
                for cx, cy in ((ox + x * s, oy + y * s), (ox + (x + 1) * s, oy + (y + 1) * s),
                               (ox + x * s, oy + (y + 1) * s), (ox + (x + 1) * s, oy + y * s)):
                    if math.hypot(cx - c, cy - c) > MASKABLE_SAFE * size + 1e-9:
                        _die(f"maskable の安全域の外にドットがある（{cx},{cy}）")
    raw = b"".join(b"\x00" + bytes(line) for line in grid)
    ihdr = struct.pack(">IIBBBBB", size, size, 8, 3, 0, 0, 0)
    plte = b"".join(bytes.fromhex(c[1:]) for c in colors)
    return ihdr, plte, raw, s


def _chunk(kind, body):
    return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body) & 0xFFFFFFFF)


def encode(ihdr, plte, raw):
    return (b"\x89PNG\r\n\x1a\n" + _chunk(b"IHDR", ihdr) + _chunk(b"PLTE", plte)
            + _chunk(b"IDAT", zlib.compress(raw, 9)) + _chunk(b"IEND", b""))


def decode(png):
    """encode の逆（比較用）。(IHDR, PLTE, 展開した IDAT)。"""
    if png[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError("PNG の署名ではない")
    pos, got, idat = 8, {}, b""
    while pos < len(png):
        n = struct.unpack(">I", png[pos:pos + 4])[0]
        kind, body = png[pos + 4:pos + 8], png[pos + 8:pos + 8 + n]
        if struct.unpack(">I", png[pos + 8 + n:pos + 12 + n])[0] != zlib.crc32(kind + body) & 0xFFFFFFFF:
            raise ValueError(f"{kind!r} の CRC が合わない")
        pos += 12 + n
        if kind == b"IDAT":
            idat += body
        else:
            got[kind] = body
    return got.get(b"IHDR"), got.get(b"PLTE"), zlib.decompress(idat)


def etag_of(png):
    return '"pi-' + hashlib.sha256(png).hexdigest()[:16] + '"'


def build():
    rows = read_sprite()
    paper, legend = read_colors()
    icons = {}
    for path, size, purpose in ICONS:
        ihdr, plte, raw, s = render(size, purpose, rows, paper, legend)
        icons[path] = {"size": size, "purpose": purpose, "scale": s, "parts": (ihdr, plte, raw)}
    return paper, icons


def module_text(paper, icons, pngs):
    body = {p: {"size": v["size"], "purpose": v["purpose"], "etag": etag_of(pngs[p]),
                "b64": base64.b64encode(pngs[p]).decode("ascii")} for p, v in icons.items()}
    return ("// 自動生成: tools/gen_pwa_icons.py（手で編集しない・直すときは正本を直して再生成）\n"
            f"// 絵= ui/cockpit/cells.js の {VENDOR}.{FRAME} ／ 体の色= ui/cockpit/palette.js ／ "
            "地と個体色= ui/hud-tokens.css の --paper と --accent\n"
            "// PWA_THEME はマニフェストの theme_color・background_color。ui/pwa/app.html の meta theme-color と"
            "同じ値であることを --check が見る。\n"
            f"export const PWA_THEME = {json.dumps(paper)};\n"
            f"export const PWA_ICONS = {json.dumps(body, indent=2)};\n")


def read_module():
    """収録済みの relay/src/pwa_icons.js を読む。(theme, {path: {..., png}}) か None。"""
    if not OUT.is_file():
        return None
    src = OUT.read_text(encoding="utf-8")
    t = re.search(r'^export const PWA_THEME = ("[^"]*");$', src, re.M)
    i = re.search(r"^export const PWA_ICONS = (\{.*\});\n\Z", src, re.M | re.S)
    if not (t and i):
        return None
    icons = json.loads(i.group(1))
    for v in icons.values():
        v["png"] = base64.b64decode(v["b64"])
    return json.loads(t.group(1)), icons


def read_app_bg():
    """R122: 明るい画面の地（Mac の ui/app/style.css の --a-bg が正本・スマホの APP_TOKENS 区画は同じ値の写し）。"""
    css = (ROOT / "ui" / "app" / "style.css").read_text(encoding="utf-8")
    m = re.search(r"--a-bg:\s*(#[0-9a-fA-F]{6})", css)
    return m.group(1).lower() if m else None


def check_html(paper):
    """app.html の meta theme-color（R101: 明の media＝ライトの地・暗の media＝ダークの地＝paper・両方必須）と link の向き先。
    R122: 明の地は明るい画面の --a-bg（ヘッダーの地と同じ＝pwa3d_smoke の R99-P4-7 と同じ物差し）。無ければ従来の TERM のライトの地。"""
    html = APP_HTML.read_text(encoding="utf-8")
    bad = []
    light = read_app_bg() or read_term()["light"]["bg"].lower()
    metas = re.findall(r"<meta name=theme-color([^>]*)>", html)
    if not metas:
        bad.append("app.html に meta theme-color が無い")
    for attrs in metas:
        c = re.search(r'content="?(#[0-9a-fA-F]{6})', attrs)
        want = light if "prefers-color-scheme: light" in attrs else paper
        if not c or c.group(1).lower() != want:
            bad.append(f"app.html の meta theme-color{attrs} が地の色 {want} でない")
    for mode in ("light", "dark"):
        if not any(f"prefers-color-scheme: {mode}" in a for a in metas):
            bad.append(f"app.html に {mode} の meta theme-color（media 付き）が無い")
    by_purpose = {p: path for path, _, p in ICONS}
    for rel, want in (("apple-touch-icon", by_purpose["apple"]), ("icon", "/app/icon-192.png")):
        hrefs = re.findall(r"<link rel=" + re.escape(rel) + r"\b[^>]*href=\"?([^\" >]+)", html)
        if hrefs != [want]:
            bad.append(f"app.html の <link rel={rel}> が {want} の 1 本でない（{hrefs}）")
    return bad


def main(argv):
    paper, icons = build()
    have = read_module()
    drift = []
    if have is None:
        drift.append(f"{OUT.relative_to(ROOT)} が無いか読めない")
    else:
        theme, got = have
        if theme != paper:
            drift.append(f"PWA_THEME {theme} ≠ --paper {paper}")
        if set(got) != set(icons):
            drift.append(f"アイコンの組が違う（収録 {sorted(got)}）")
        for path, v in icons.items():
            g = got.get(path)
            if not g:
                continue
            if (g.get("size"), g.get("purpose")) != (v["size"], v["purpose"]):
                drift.append(f"{path} の寸法・purpose が違う")
            try:
                same = decode(g["png"]) == v["parts"]
            except (ValueError, zlib.error, struct.error) as e:
                drift.append(f"{path} が PNG として読めない（{e}）")
                continue
            if not same:
                drift.append(f"{path} の画素が正本（cells.js・palette.js・hud-tokens.css）と違う")
            if g.get("etag") != etag_of(g["png"]):
                drift.append(f"{path} の ETag が中身の SHA-256 と違う")
    html_bad = check_html(paper)
    if "--check" in argv:
        for x in drift + html_bad:
            print(f"✗ {x}")
        if drift or html_bad:
            if drift:
                print("  → python3 tools/gen_pwa_icons.py で再生成して relay/src/pwa_icons.js をコミット")
            return 1
        print(f"✓ relay/src/pwa_icons.js は最新（{len(icons)} 枚・地 {paper}・"
              + "・".join(f"{Path(p).name}×{v['scale']}" for p, v in icons.items()) + "）")
        print("✓ app.html の meta theme-color と apple-touch-icon・icon が同じ正本を向く")
        return 0
    if drift:
        pngs = {p: encode(*v["parts"]) for p, v in icons.items()}
        OUT.write_text(module_text(paper, icons, pngs), encoding="utf-8")
        print(f"✓ 生成: {OUT.relative_to(ROOT)}（" + "・".join(f"{Path(p).name} {len(b)}B" for p, b in pngs.items()) + "）")
    else:
        print(f"✓ {OUT.relative_to(ROOT)} は最新（画素が同じなので書き換えない）")
    for x in html_bad:
        print(f"⚠ {x}（app.html は手で直す）")
    return 1 if html_bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
