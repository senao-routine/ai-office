#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""R77: スマホPWAの3Dシーン用に ui/ のESMを Worker へ同梱する（sprites_data.js と同じ型）。

なぜ必要か: PWAは Cloudflare Worker が配信する＝Macの 127.0.0.1 には届かない。
デスクトップと同じ three.js シーンをスマホで動かすには、モジュール一式を Worker が
`/ui/...` の**同じパス**で返す必要がある（import 指定子を書き換えないための条件）。

生成物: relay/src/modules_data.js（git追跡必須。未追跡だとクリーンcloneのdeployが壊れる）
使い方:
    python3 tools/gen_pwa_modules.py           # 生成
    python3 tools/gen_pwa_modules.py --check   # ui/ との一致（ドリフト検知・verify用）
"""
import base64
import hashlib
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "relay" / "src" / "modules_data.js"

# 入口＝3Dシーンとワールド構築。ここから import を辿って閉包を作る
ENTRIES = ["/ui/pwa/boot3d.js"]   # ここから import を辿れば scene3d/world/clock/three が揃う
# JSからURLで読む静的アセット（importでは辿れない）。3Dシーンのテクスチャ一式。
ASSET_DIRS = ["ui/iso/tex"]
ASSET_MIME = {".webp": "image/webp", ".png": "image/png", ".jpg": "image/jpeg"}

# import 指定子: 絶対(/ui/...) と 相対(./x.js) の両方
_ABS = re.compile(r'from\s*"(/ui/[^"]+)"')
_REL = re.compile(r'from\s*"(\./[^"]+)"')


def _read(url):
    path = ROOT / url.lstrip("/")
    if not path.is_file():
        raise SystemExit(f"✗ 依存が見つからない: {url}")
    return path.read_text(encoding="utf-8")


def collect():
    """入口から import を辿って {url: source} を作る（テストは含めない）。"""
    seen = {}
    stack = list(ENTRIES)
    while stack:
        url = stack.pop()
        if url in seen:
            continue
        src = _read(url)
        seen[url] = src
        base = url.rsplit("/", 1)[0]
        for dep in _ABS.findall(src):
            stack.append(dep)
        for dep in _REL.findall(src):
            stack.append(f"{base}/{dep[2:]}")
    return dict(sorted(seen.items()))


def collect_assets():
    """URL読みのテクスチャを base64 で同梱（importで辿れないので明示収集）。"""
    out = {}
    for d in ASSET_DIRS:
        for f in sorted((ROOT / d).glob("*")):
            if f.is_file() and f.suffix in ASSET_MIME:
                out[f"/{d}/{f.name}"] = (ASSET_MIME[f.suffix],
                                         base64.b64encode(f.read_bytes()).decode("ascii"))
    return out


def build_id(mods, assets):
    """内容から決まる版ID。Worker の ETag に使う＝内容が変われば必ず新版になる。
    実行時に計算すると1.2MBを毎コールドスタートで舐めるので、生成時に確定させる。"""
    h = hashlib.sha256()
    for url, src in mods.items():
        h.update(url.encode("utf-8")); h.update(b"\x00")
        h.update(src.encode("utf-8")); h.update(b"\x00")
    for url, (mime, b64) in assets.items():
        h.update(url.encode("utf-8")); h.update(b"\x00")
        h.update(b64.encode("ascii")); h.update(b"\x00")
    return h.hexdigest()[:12]


def render(mods, assets):
    lines = [
        "// 自動生成: tools/gen_pwa_modules.py（手で編集しない）",
        "// PWAの3Dシーン用ESM。Worker が /ui/... の同じパスで返す＝import指定子を書き換えない。",
        "export const MODULES = Object.assign(Object.create(null), {",
    ]
    lines.insert(2, f"export const BUILD = {json.dumps(build_id(mods, assets))};   // 内容ハッシュ＝ETagの版")
    lines.insert(3, "")
    for url, src in mods.items():
        lines.append(f"  {json.dumps(url)}: {json.dumps(src)},")
    lines.append("});")
    lines.append("")
    lines.append("// テクスチャ（base64）。Worker が /ui/iso/tex/... で返す")
    lines.append("export const ASSETS = Object.assign(Object.create(null), {")
    for url, (mime, b64) in assets.items():
        lines.append(f"  {json.dumps(url)}: [{json.dumps(mime)}, {json.dumps(b64)}],")
    lines.append("});")
    lines.append("")
    return "\n".join(lines)


def main(argv):
    mods = collect()
    assets = collect_assets()
    body = render(mods, assets)
    kb = len(body.encode("utf-8")) // 1024
    if "--check" in argv:
        if not OUT.is_file():
            print("✗ modules_data.js が無い（python3 tools/gen_pwa_modules.py で生成）")
            return 1
        if OUT.read_text(encoding="utf-8") != body:
            print("✗ modules_data.js が ui/ と不一致（再生成してコミット）")
            return 1
        print(f"✓ relay/src/modules_data.js は最新（JS {len(mods)}本＋tex {len(assets)}枚・{kb}KB）")
        return 0
    OUT.write_text(body, encoding="utf-8")
    print(f"✓ 生成: relay/src/modules_data.js（JS {len(mods)}本＋tex {len(assets)}枚・{kb}KB）")
    for url in mods:
        print(f"    {url}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
