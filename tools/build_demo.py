#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""R97-C: インストール不要で踏める**公開デモ**を組み立てる（静的ファイルだけ）。

なぜ要るか: GitHub で README を読んでいる人は `http://localhost:4780/?demo=1` を踏めない。
競合は `npx` や VS Code 拡張の 1 手で入るので、こちらの入口も「リンク 1 クリック」にする。

なぜ静的で足りるか（2026-09-17 の実測）:
  `?demo=1` は `/ui/demo/world.json` を 1 回読むだけで、`/api/office` のポーリングも SSE も張らない
  （ui/iso/index.js）。コスト面の `/api/status_board` は失敗すると黙ってゲージを畳む（ui/hud/gauges.js）。
  つまりサーバーが無くても 3D は成立する。

**ルート（/）で配ること**が条件: ui/ の import は `/ui/...` の絶対指定なので、サブパス配下
（例 GitHub Pages の /ai-office/）では解決できない。Cloudflare Pages / Workers の
ルート配信を前提にする（import map で回避する案は、PWA と共有する ui/iso/scene3d.js に
影響が及ぶので採らない）。

使い方:
    python3 tools/build_demo.py            # dist/demo-site/ を作る
    python3 tools/build_demo.py --check    # ソースとの差分だけ見る（verify 用・書かない）
"""
import hashlib
import json
import re
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "dist" / "demo-site"
# boot.html が `/ui/${style}/index.js` で読む先。R98: pixel（台帳）も同梱＝⚙「画面」で切り替えても起動する
# （片方だけ同梱すると、選んだ様式が localStorage に残って再読み込みでも起動できない＝別モデルレビュー）。
ENTRIES = ["/ui/iso/index.js", "/ui/pixel/index.js"]
EXTRA_DIRS = ["ui/iso/tex", "ui/vendor/three"]      # import では辿れない（URL 直読み・相対 import）
EXTRA_FILES = ["ui/hud/hud.css", "ui/pixel/style.css", "ui/demo/world.json", "ui/boot.html"]
_ABS = re.compile(r'from\s*"(/ui/[^"]+)"')
_REL = re.compile(r'from\s*"(\./[^"]+)"')


def collect():
    """入口から import を辿って {url: 本文} を作る（PWA のスタブ差し替えはしない＝実物を配る）。"""
    seen, stack = {}, list(ENTRIES)
    while stack:
        url = stack.pop()
        if url in seen:
            continue
        path = ROOT / url.lstrip("/")
        if not path.is_file():
            raise SystemExit(f"✗ 依存が見つからない: {url}")
        src = path.read_text(encoding="utf-8")
        seen[url] = src
        base = url.rsplit("/", 1)[0]
        for dep in _ABS.findall(src):
            stack.append(dep)
        for dep in _REL.findall(src):
            stack.append(f"{base}/{dep[2:]}")
    return seen


def plan():
    """{出力パス: バイト列} を決める（--check と本番で同じ計算を使う）。"""
    files = {}
    for url, src in collect().items():
        files[url.lstrip("/")] = src.encode("utf-8")
    for rel in EXTRA_FILES:
        files[rel] = (ROOT / rel).read_bytes()
    for d in EXTRA_DIRS:
        for f in sorted((ROOT / d).rglob("*")):
            if f.is_file() and not f.name.startswith("."):
                files[str(f.relative_to(ROOT))] = f.read_bytes()
    # ルートの index.html ＝ boot.html そのまま（`/` で開けるように）。
    # `?demo=1` を付け忘れてもデモに入れるよう、meta で印を付ける（ui/iso/index.js が読む）。
    boot = (ROOT / "ui" / "boot.html").read_text(encoding="utf-8")
    assert "<head>" in boot, "boot.html の <head> が見つからない"
    boot = boot.replace("<head>", '<head>\n<meta name="office-demo" content="1">', 1)
    files["index.html"] = boot.encode("utf-8")
    return files


def main():
    check = "--check" in sys.argv
    files = plan()
    total = sum(len(v) for v in files.values())
    if check:
        drift = []
        for rel, data in files.items():
            cur = OUT / rel
            if not cur.is_file() or cur.read_bytes() != data:
                drift.append(rel)
        stale = [str(f.relative_to(OUT)) for f in OUT.rglob("*")
                 if f.is_file() and str(f.relative_to(OUT)) not in files] if OUT.is_dir() else []
        if drift or stale:
            print(f"✗ デモサイトがソースとずれている（更新 {len(drift)} / 余分 {len(stale)}）"
                  f" → python3 tools/build_demo.py")
            for rel in (drift[:5] + stale[:5]):
                print(f"    {rel}")
            return 1
        print(f"✓ デモサイト: {len(files)} ファイル / {total // 1024}KB（ソースと一致）")
        return 0
    if OUT.is_dir():
        shutil.rmtree(OUT)
    for rel, data in files.items():
        dest = OUT / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)
    digest = hashlib.sha256(json.dumps(
        {k: hashlib.sha256(v).hexdigest() for k, v in sorted(files.items())}).encode()).hexdigest()[:12]
    print(f"✓ dist/demo-site: {len(files)} ファイル / {total // 1024}KB（内容ハッシュ {digest}）")
    print("  ローカル確認: python3 -m http.server -d dist/demo-site 8788  → http://127.0.0.1:8788/")
    return 0


if __name__ == "__main__":
    sys.exit(main())
