#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""R77: スマホPWAの3Dオフィス スモーク（Playwright必須）。

既定経路＝デスクトップと同じ IsoScene が Worker 配信のESMで動くこと。
「canvasが出た」ではなく **ロボットが実際に立ち、タップでシートが開く** まで見る
（絵だけ出て操作が死んでいる嘘greenを防ぐ）。2Dへ退避する経路は pwa_smoke.py が担当。

使い方: python pwa3d_smoke.py <base_url> <device_id> <secret> <token> <out.png>
"""
import json
import sys
from pathlib import Path

try:
    from playwright.sync_api import sync_playwright
except ImportError as exc:  # pragma: no cover
    print(f"✗ 3Dスモーク: Playwright import失敗: {exc}")
    sys.exit(1)


def main(argv):
    if len(argv) != 6:
        print("使い方: pwa3d_smoke.py <base_url> <device_id> <secret> <token> <out.png>")
        return 2
    base_url, device_id, secret, token, out = argv[1:]
    cred = json.dumps({"d": device_id, "s": secret, "t": token, "e": 0},
                      ensure_ascii=False, separators=(",", ":"))
    errors = []
    ng = 0

    with sync_playwright() as p:
        browser = p.chromium.launch()
        try:
            page = browser.new_page(viewport={"width": 390, "height": 844})
            page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.add_init_script(
                "localStorage.setItem('aioffice.cred', " + json.dumps(cred) + ");")
            page.goto(base_url.rstrip("/") + "/app", wait_until="domcontentloaded")

            page.wait_for_selector("#scene3d canvas", timeout=30000)
            print("  ✓ 3D canvas が描画された（Worker配信のESM＋WebGL）")

            stats = None
            for _ in range(60):
                stats = page.evaluate(
                    "() => window.__scene3d && window.__scene3d.stats"
                    " ? window.__scene3d.stats() : null")
                if stats and stats.get("robots"):
                    break
                page.wait_for_timeout(500)
            if not (stats and stats.get("robots", 0) >= 1):
                print(f"  ✗ ロボットが1体も立っていない: {stats}")
                ng += 1
            else:
                print(f"  ✓ ロボット {stats['robots']}体（drawCalls {stats.get('drawCalls')}）")
            if stats and stats.get("drawCalls", 999) >= 300:
                print(f"  ✗ drawCalls 上限超過: {stats}")
                ng += 1

            # 3D時は旧2Dマップを出さない（二重描画＝どちらが正か分からなくなる）
            if page.locator("#mapframe").count() and page.locator("#mapframe").is_visible():
                print("  ✗ 3D時に旧2Dマップが残っている")
                ng += 1
            else:
                print("  ✓ 旧2Dマップは非表示（3Dが唯一の絵）")

            page.screenshot(path=out)   # 目視用はタップ前＝シーンが写る絵にする

            # ロボットをタップ → 詳細シートが開く（操作の本流が生きている証明）
            point = page.evaluate(
                """() => {
                    const ags = window.__scene3d ? window.__scene3d.agents() : [];
                    for (const a of ags) {
                        const p = window.__scene3d.project(a.id);
                        if (p) return {id: a.id, left: p.left, top: p.top};
                    }
                    return null;
                }""")
            if not point:
                print("  ✗ ロボットのスクリーン座標が取れない")
                ng += 1
            else:
                box = page.locator("#scene3d").bounding_box()
                page.mouse.click(box["x"] + point["left"], box["y"] + point["top"] + 30)
                page.wait_for_timeout(700)
                opened = page.evaluate(
                    "() => { const n=document.getElementById('sheetwrap');"
                    " return !!n && n.classList.contains('open'); }")
                if opened:
                    print("  ✓ ロボットのタップ → 詳細シートが開く")
                else:
                    print("  ✗ ロボットをタップしてもシートが開かない")
                    ng += 1

            if errors:
                print(f"  ✗ console/page error: {errors[:3]}")
                ng += 1
            else:
                print("  ✓ console error 0")
        finally:
            browser.close()

    if ng:
        print(f"✗ PWA 3Dスモーク {ng}件失敗")
        return 1
    print(f"✓ PWA 3Dスモーク合格: {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
