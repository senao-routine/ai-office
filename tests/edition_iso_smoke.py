#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""R42.6骨格: 新UIのエディション別表示スモーク（旧 edition_smoke.py の後継=R52で削除済み）。

OFFICE_EDITION 注入の実サーバー2本で検査する（/api/office は route 差し替えしない＝
edition/features はサーバー正本を使う。roster は空でよい＝表示ゲートの検査が目的）:
  openclaw版: .ed-openclaw ダーク化・OpenClaw Editionバッジ・🔑🅰にClaude行なし・②→③導線
  claude版:   .ed-openclaw なし・バッジ非表示・🔑🅰にClaude行あり

使い方: python3 tests/edition_iso_smoke.py   （verify.sh ▶7 から呼ぶ・Playwright必要）
"""
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
from ui_shot import SWIFTSHADER, VIEWPORT, free_port, start_server  # noqa: E402


def luma(rgb_text):
    """'rgb(18, 16, 28)' → 輝度。ダーク判定用。"""
    try:
        nums = [int(x) for x in rgb_text.replace("rgba", "").replace("rgb", "")
                .strip("() ").split(",")[:3]]
        return sum(nums) / 3
    except (ValueError, IndexError):
        return 255


def run_edition(pw, port, ng, errors):
    browser = pw.chromium.launch(args=SWIFTSHADER)
    page = browser.new_page(viewport=VIEWPORT, device_scale_factor=1)
    page.on("pageerror", lambda e: errors.append(f"pageerror: {e}"))
    page.on("console", lambda m: errors.append(f"console.error: {m.text}")
            if m.type == "error" else None)
    page.goto(f"http://127.0.0.1:{port}/?ui=iso&t=3.2&seed=11")
    page.wait_for_function("window.__office && window.__office.ready", timeout=30000)
    page.wait_for_timeout(400)
    return browser, page


def main():
    from playwright.sync_api import sync_playwright

    ng = 0
    errors = []
    with sync_playwright() as pw:
        # ── openclaw版 ──
        port = free_port()
        proc = start_server(port, extra_env={"OFFICE_EDITION": "openclaw"})
        try:
            browser, page = run_edition(pw, port, ng, errors)
            if page.eval_on_selector("#app", "el => el.classList.contains('ed-openclaw')"):
                print("  ✓ openclaw: .ed-openclaw 付与")
            else:
                print("  ✗ openclaw: .ed-openclaw が無い")
                ng += 1
            side_bg = page.eval_on_selector(".side", "el => getComputedStyle(el).backgroundColor")
            if luma(side_bg) < 100:
                print(f"  ✓ openclaw: ダーク基調（side={side_bg}）")
            else:
                print(f"  ✗ openclaw: 背景が明るいまま: {side_bg}")
                ng += 1
            badge = page.eval_on_selector("#edbadge",
                                          "el => el.hidden ? '' : el.textContent")
            if "OpenClaw" in badge:
                print("  ✓ openclaw: エディションバッジ表示")
            else:
                print(f"  ✗ openclaw: バッジが出ない: {badge!r}")
                ng += 1
            page.click("#btn-res")
            page.wait_for_selector(".modal .mkeyrow", timeout=15000)
            names = page.eval_on_selector_all(
                ".mkeys .mkeyname", "els => els.map(e => e.textContent)")
            if not any("Claude Code" in n for n in names):
                print("  ✓ openclaw: 🔑🅰に Claude Code 行なし")
            else:
                print(f"  ✗ openclaw: Claude Code 行が残っている: {names}")
                ng += 1
            page.keyboard.press("Escape")
            page.click("#btn-license")
            page.wait_for_selector("#mgo-license", timeout=8000)
            lic_txt = page.eval_on_selector(".modal", "el => el.textContent")
            if "Hybrid" in lic_txt and ("アップグレード" in lic_txt or "Upgrade" in lic_txt):
                print("  ✓ openclaw: ②→③アップグレード導線")
            else:
                print(f"  ✗ openclaw: 導線が出ない: {lic_txt[:100]!r}")
                ng += 1
            browser.close()
        finally:
            proc.terminate()

        # ── claude版（従来表示の無退行） ──
        port = free_port()
        proc = start_server(port, extra_env={"OFFICE_EDITION": "claude"})
        try:
            browser, page = run_edition(pw, port, ng, errors)
            if not page.eval_on_selector("#app", "el => el.classList.contains('ed-openclaw')"):
                print("  ✓ claude: .ed-openclaw なし（従来表示）")
            else:
                print("  ✗ claude: .ed-openclaw が誤付与")
                ng += 1
            if page.eval_on_selector("#edbadge", "el => el.hidden"):
                print("  ✓ claude: バッジ非表示")
            else:
                print("  ✗ claude: バッジが出ている")
                ng += 1
            page.click("#btn-res")
            page.wait_for_selector(".modal .mkeyrow", timeout=15000)
            names = page.eval_on_selector_all(
                ".mkeys .mkeyname", "els => els.map(e => e.textContent)")
            if any("Claude Code" in n for n in names):
                print("  ✓ claude: 🔑🅰に Claude Code 行あり")
            else:
                print(f"  ✗ claude: Claude Code 行が消えた: {names}")
                ng += 1
            browser.close()
        finally:
            proc.terminate()

    real = [e for e in errors if "Failed to load resource" not in e]
    if real:
        print(f"  ✗ JSエラー: {real[:3]}")
        ng += 1
    else:
        print("  ✓ console error 0")
    if ng:
        print(f"エディションUI(新)スモーク: {ng} 件失敗")
    else:
        print("✓ エディションUI(新)スモーク合格（openclawダーク/バッジ/Claude面ゲート/導線・claude無退行）")
    return 1 if ng else 0


if __name__ == "__main__":
    sys.exit(main())
