#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""R97-C: 公開デモ（静的ホスト）が**サーバー無しで**成立することを機械で確かめる。

なぜ要るか: GitHub で README を読んでいる人は `http://localhost:4780/?demo=1` を踏めない。
競合は `npx` や拡張の 1 手で入るので、こちらの入口は「リンク 1 クリック」にする。
その入口は「静的ファイルだけで 3D が出る・/api/ を 1 本も叩かない」が成立条件なので、ここで固定する。

使い方: <playwright入りpython> tests/demo_site_smoke.py   （verify.sh ▶7 から呼ぶ）
"""
import functools
import http.server
import pathlib
import socket
import subprocess
import sys
import threading

ROOT = pathlib.Path(__file__).resolve().parent.parent
SITE = ROOT / "dist" / "demo-site"


def main():
    from playwright.sync_api import sync_playwright

    # 生成物はコミットしない（dist/ は gitignore）。検査の直前に必ず作り直す＝ソースが正本
    subprocess.run([sys.executable, str(ROOT / "tools" / "build_demo.py")],
                   check=True, capture_output=True)
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    http.server.SimpleHTTPRequestHandler.log_message = lambda *a, **k: None
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(SITE))

    class Server(http.server.ThreadingHTTPServer):
        daemon_threads = True

    srv = Server(("127.0.0.1", port), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    api_hits, errors, ng = [], [], 0
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(args=["--use-gl=swiftshader", "--disable-gpu"])
            page = browser.new_page(viewport={"width": 1440, "height": 900}, device_scale_factor=1)
            page.on("request", lambda r: api_hits.append(r.url) if "/api/" in r.url else None)
            page.on("pageerror", lambda e: errors.append(f"pageerror: {e}"))
            page.on("console", lambda m: errors.append(f"console.error: {m.text}")
                    if m.type == "error" else None)
            # ?demo=1 を付けない＝ページに焼いた <meta name="office-demo"> だけでデモに入ること
            page.goto(f"http://127.0.0.1:{port}/")
            page.wait_for_function("window.__office && window.__office.ready", timeout=60000)
            page.wait_for_timeout(400)
            dump = page.evaluate("window.__office.dumpWorld()")
            stats = page.evaluate("window.__office.stats && window.__office.stats()") or {}
            # R98: 台帳（?ui=pixel）も同梱されている＝⚙「画面」で切り替えた人が空振りしない
            page.goto(f"http://127.0.0.1:{port}/?ui=pixel")
            page.wait_for_function("window.__office && window.__office.ready", timeout=60000)
            page.wait_for_timeout(300)
            px = page.evaluate("() => ({ style: window.__office.style,"
                               "  rows: document.querySelectorAll('#agents .pxrow').length })")
            browser.close()
    finally:
        srv.shutdown()

    agents = len(dump.get("agents") or [])
    if agents >= 6:
        print(f"  ✓ 静的デモ: サーバー無しで {agents} 体が出勤（drawCalls {stats.get('drawCalls')}）")
    else:
        print(f"  ✗ 静的デモの出勤が少なすぎる: {agents} 体")
        ng += 1
    if px.get("style") == "pixel" and px.get("rows", 0) >= 6:
        print(f"  ✓ 静的デモ: 台帳（?ui=pixel）も {px['rows']} 行で立つ")
    else:
        print(f"  ✗ 静的デモの台帳が立たない: {px}")
        ng += 1
    if api_hits:
        print(f"  ✗ 静的デモが /api/ を叩いている（ホスト先には存在しない）: {api_hits[:3]}")
        ng += 1
    else:
        print("  ✓ 静的デモ: /api/ へのリクエスト 0 本")
    if errors:
        print(f"  ✗ console/page error: {errors[:3]}")
        ng += 1
    else:
        print("  ✓ 静的デモ: console error 0")
    # three.js は MIT。再配布物にライセンス原文を必ず同梱する
    if (SITE / "ui" / "vendor" / "three" / "LICENSE").is_file():
        print("  ✓ 静的デモ: three.js の LICENSE を同梱")
    else:
        print("  ✗ 静的デモに three.js の LICENSE が無い（MIT 再配布の義務）")
        ng += 1
    if ng:
        print(f"✗ 公開デモ {ng}件失敗")
        return 1
    print("✓ 公開デモ（静的ホスト）成立")
    return 0


if __name__ == "__main__":
    sys.exit(main())
