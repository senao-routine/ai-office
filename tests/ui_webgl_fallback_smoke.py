#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""R80-B6: WebGLが使えないMacでもデスクトップUIが使えることの回帰（Playwright必須）。

配布すると必ず一定数いる「古いGPU・仮想マシン・リモートデスクトップ」の環境で、
以前は `new IsoScene()` の例外が起動ごと落として**白画面＋英語の行き止まり**だった
（スマホPWAは同じ状況でリスト表示へ自動退避するのに、デスクトップだけ逆転していた）。

ここでは **WebGLを実際に無効化したブラウザ**で開き、
  ①起動が完走する（bootの失敗画面に落ちない）
  ②3D不可のお知らせが日本語/英語で出る
  ③右レールにプロジェクト行が並び、**クリックで詳細シートが開く**＝操作が生きている
を実測する。「絵が出ない」ことではなく「仕事ができる」ことを検査するのが要点。

使い方: python ui_webgl_fallback_smoke.py <base_url> <out.png>
"""
import sys
from pathlib import Path

try:
    from playwright.sync_api import sync_playwright
except ImportError as exc:  # pragma: no cover
    print(f"✗ WebGL退避スモーク: Playwright import失敗: {exc}")
    sys.exit(1)


def main(argv):
    if len(argv) != 3:
        print("使い方: ui_webgl_fallback_smoke.py <base_url> <out.png>")
        return 2
    base_url, out = argv[1:]
    ng = 0
    errors = []

    with sync_playwright() as p:
        # WebGLを本当に殺す（ANGLEもソフトウェア実装も使わせない）
        browser = p.chromium.launch(args=[
            "--disable-webgl", "--disable-webgl2",
            "--disable-3d-apis", "--disable-gpu",
        ])
        try:
            page = browser.new_page(viewport={"width": 1440, "height": 900})
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.goto(base_url.rstrip("/"), wait_until="domcontentloaded")

            # ① 起動が完走する（#bootstate の失敗画面のままにならない）
            try:
                page.wait_for_selector(".ui-iso", timeout=20000)
                print("  ✓ WebGL無効でもUIが起動する（白画面にならない）")
            except Exception:
                print("  ✗ WebGL無効で起動が完走しない（旧: boot失敗画面の行き止まり）")
                browser.close()
                return 1

            # ② 3D不可のお知らせ（次に何ができるかを書いてある）
            note = page.locator("#no3d")
            if note.count() and note.first.is_visible():
                text = note.first.inner_text().strip()
                if len(text) < 10:
                    print(f"  ✗ 3D不可の案内が短すぎる: {text!r}")
                    ng += 1
                else:
                    print(f"  ✓ 3D不可の案内が出る: {text[:34]}…")
            else:
                print("  ✗ 3D不可の案内(#no3d)が出ていない")
                ng += 1

            # ③ 操作が生きている: 右レールの行 → 詳細シートが開く
            page.wait_for_timeout(1500)
            rows = page.locator("#agents .arow")
            if not rows.count():
                print("  ✗ 右レールにプロジェクト行が無い（データ未到達 or 描画停止）")
                ng += 1
            else:
                rows.first.click()
                page.wait_for_timeout(600)
                opened = page.evaluate(
                    "() => { const n = document.querySelector('.ui-iso .sheet');"
                    " return !!n && getComputedStyle(n).display !== 'none'; }")
                if opened:
                    print(f"  ✓ 右レール{rows.count()}行・クリックで詳細シートが開く（操作は生きている）")
                else:
                    print("  ✗ 行をクリックしても詳細シートが開かない＝退避先で仕事ができない")
                    ng += 1

            Path(out).parent.mkdir(parents=True, exist_ok=True)
            page.screenshot(path=out)

            if errors:
                print(f"  ✗ page error: {errors[:3]}")
                ng += 1
            else:
                print("  ✓ page error 0")
        finally:
            browser.close()

    if ng:
        print(f"✗ WebGL退避スモーク {ng}件失敗")
        return 1
    print(f"✓ WebGL退避スモーク合格: {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
