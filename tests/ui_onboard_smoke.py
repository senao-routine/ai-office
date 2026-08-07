#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""R50提案2b: 初回体験スモーク（空オフィスの導線・?demo=1・hook未設定バナー）。

「clone→起動→無人の美しいオフィスで放置」という初回体験の断線を防ぐ3点を機械検証する:
  (1) 空world → 右レールに onboarding カード＋デモ導線
  (2) setup.hookInstalled=false → 📮配達未設定バナー
  (3) ?demo=1 → 同梱 /ui/demo/world.json で8体出勤・投函はブロック（inbox実ファイル無し）

使い方: python3 tests/ui_onboard_smoke.py   （verify.sh ▶7 から呼ぶ・Playwright必要）
"""
import json
import pathlib
import shutil
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
from ui_shot import SWIFTSHADER, VIEWPORT, free_port, start_server  # noqa: E402

INBOX = ROOT / ".ui_shot_home" / ".claude" / "office_inbox"

EMPTY_WORLD = {
    "officeName": "AI Office", "lang": "ja", "generatedAt": 1753799999,
    "setup": {"hookInstalled": False},
    "edition": {"id": "claude",
                "features": {"claudeSessions": True, "openclaw": False,
                             "relayPwa": False, "push": False, "costDash": False}},
    "counts": {}, "history": [], "employees": [], "roster": [],
    "tasks": {"pending": 0, "inProgress": 0, "completed": 0},
}


def main():
    from playwright.sync_api import sync_playwright

    shutil.rmtree(INBOX, ignore_errors=True)
    port = free_port()
    proc = start_server(port)
    ng = 0
    errors = []
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(args=SWIFTSHADER)

            # (1)(2) 空world＋hook未設定
            page = browser.new_page(viewport=VIEWPORT, device_scale_factor=1)
            page.on("pageerror", lambda e: errors.append(f"pageerror: {e}"))
            page.on("console", lambda m: errors.append(f"console.error: {m.text}")
                    if m.type == "error" else None)
            page.route("**/api/office*", lambda route: route.fulfill(
                status=200, content_type="application/json; charset=utf-8",
                body=json.dumps(EMPTY_WORLD, ensure_ascii=False)))
            page.goto(f"http://127.0.0.1:{port}/?ui=iso&t=3.2&seed=11")
            page.wait_for_function("window.__office && window.__office.ready", timeout=30000)
            page.wait_for_timeout(300)
            ob = page.query_selector(".onboard")
            if ob and "claude を起動" in ob.text_content() and page.query_selector(".odemo"):
                print("  ✓ 空オフィス: onboardingカード＋デモ導線")
            else:
                print("  ✗ 空オフィスに onboarding カードが出ない")
                ng += 1
            # R80-A21: バナーは「文言＋コマンド＋コピーボタン」の3要素。
            # 回答が実セッションへ届かないという**致命的な前提条件**を伝える場所なので、
            # 読めるだけでなく**コピーして実行できる**ことまでを固定する。
            bar = page.query_selector("#setupbar")
            cmd = page.query_selector("#setupbar .sb-cmd")
            copy = page.query_selector("#setupbar .sb-copy")
            if bar and cmd and copy and "setup.sh" in cmd.text_content():
                print("  ✓ hook未設定バナー（文言＋コマンド＋コピーボタン）")
            else:
                print("  ✗ hook未設定バナーが出ない/コピーできない")
                ng += 1
            page.close()

            # (3) デモモード: 同梱worldで出勤・投函ブロック
            page = browser.new_page(viewport=VIEWPORT, device_scale_factor=1)
            page.on("pageerror", lambda e: errors.append(f"pageerror: {e}"))
            page.on("console", lambda m: errors.append(f"console.error: {m.text}")
                    if m.type == "error" else None)
            page.goto(f"http://127.0.0.1:{port}/?ui=iso&demo=1&t=3.2&seed=11")
            page.wait_for_function("window.__office && window.__office.ready", timeout=30000)
            page.wait_for_timeout(300)
            rows = page.query_selector_all(".arow")
            if len(rows) >= 6:
                print(f"  ✓ デモ: 同梱worldで {len(rows)} プロジェクトが出勤")
            else:
                print(f"  ✗ デモの出勤が少なすぎる: {len(rows)}体")
                ng += 1
            # 投函ブロック: ❗トレイの数字キー1 → inbox が生えないこと
            page.keyboard.press("1")
            page.wait_for_timeout(600)
            files = list(INBOX.glob("*.json")) if INBOX.is_dir() else []
            toast = page.eval_on_selector("#toast", "el => el.hidden ? '' : el.textContent")
            # デモworldは lang=en なので UI が英語へ切り替わる（提案2c）＝両言語を許容
            if not files and ("デモ" in toast or "Demo" in toast):
                print("  ✓ デモ: 投函はブロック（inbox実ファイル無し＋デモトースト）")
            else:
                print(f"  ✗ デモで投函された?: files={files} toast={toast!r}")
                ng += 1
            page.close()

            real = [e for e in errors if "Failed to load resource" not in e]
            if real:
                print(f"  ✗ JSエラー: {real[:3]}")
                ng += 1
            else:
                print("  ✓ console error 0")
            browser.close()
    finally:
        proc.terminate()
        shutil.rmtree(INBOX, ignore_errors=True)
    if ng:
        print(f"初回体験スモーク: {ng} 件失敗")
    else:
        print("✓ 初回体験スモーク合格（空オフィス導線/hookバナー/デモ出勤+投函ブロック/console 0）")
    return 1 if ng else 0


if __name__ == "__main__":
    sys.exit(main())
