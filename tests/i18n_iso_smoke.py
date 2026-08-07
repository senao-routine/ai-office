#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""R50提案2c: 新UIの日本語文字カナリア（lang=en で日本語が1文字も出ないこと）。

R42.2d-1 の i18n_smoke.py（旧UI）と同思想。旧カナリアが新UIを検査対象外にしていた
穴を塞ぐ。デモワールド（/ui/demo/world.json・lang=en・内容も英語）を表示し、
DOMテキスト全体＋🧾/⚡モーダルを走査する。サーバーは OFFICE_LANG=en で立てる
（エラーメッセージ等のサーバー文字列も en になる＝R42.2d-1 の lang 対応）。

使い方: python3 tests/i18n_iso_smoke.py   （verify.sh ▶7 から呼ぶ・Playwright必要）
"""
import os
import pathlib
import re
import socket
import subprocess
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
from ui_shot import SWIFTSHADER, VIEWPORT, free_port  # noqa: E402

JA = re.compile(r"[ぁ-んァ-ヶ一-龯]")


def start_server_en(port):
    """OFFICE_LANG=en で密閉サーバーを立てる（ui_shot.start_server は env 固定のため自前）。"""
    env = {**os.environ, "OFFICE_HOME": str(ROOT / ".ui_shot_home"), "OFFICE_LANG": "en"}
    (ROOT / ".ui_shot_home").mkdir(exist_ok=True)
    proc = subprocess.Popen(
        [sys.executable, str(ROOT / "server" / "office_server.py"), "--port", str(port)],
        cwd=str(ROOT), env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(80):
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=.2):
                return proc
        except OSError:
            time.sleep(.1)
    proc.terminate()
    raise SystemExit("サーバーが起動しませんでした")


def find_ja(text):
    hits = []
    for line in (text or "").splitlines():
        if JA.search(line):
            hits.append(line.strip()[:80])
    return hits


def main():
    from playwright.sync_api import sync_playwright

    port = free_port()
    proc = start_server_en(port)
    ng = 0
    errors = []
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(args=SWIFTSHADER)
            page = browser.new_page(viewport=VIEWPORT, device_scale_factor=1)
            page.on("pageerror", lambda e: errors.append(f"pageerror: {e}"))
            page.on("console", lambda m: errors.append(f"console.error: {m.text}")
                    if m.type == "error" else None)
            page.goto(f"http://127.0.0.1:{port}/?ui=iso&demo=1&t=3.2&seed=11")
            page.wait_for_function("window.__office && window.__office.ready", timeout=30000)
            page.wait_for_timeout(400)

            hits = find_ja(page.evaluate("document.body.innerText"))
            if hits:
                print(f"  ✗ lang=en の画面に日本語: {hits[:5]}")
                ng += 1
            else:
                print("  ✓ 本体DOM: 日本語文字 0（デモ=enワールド）")

            # 🧾ライセンス・⚡リソースのモーダル（➕はフォルダダイアログ・📱は実API発行なので開かない）
            for btn, label in (("#btn-license", "🧾"), ("#btn-res", "⚡")):
                page.click(btn)
                page.wait_for_selector(".modal .mtitle", timeout=8000)
                if btn == "#btn-res":
                    # R66: 🔑セクションは非同期描画＝待たずにスキャンすると検査から漏れる
                    # （実際にサーバー由来の日本語hintが素通りしていた検査の穴）
                    page.wait_for_selector(".modal .mkeys .mkeyrow", timeout=8000)
                page.wait_for_timeout(600)          # 非同期の読み込み文言差し替えを待つ
                mtext = page.eval_on_selector(".modal", "el => el.innerText")
                mh = find_ja(mtext)
                if mh:
                    print(f"  ✗ {label}モーダルに日本語: {mh[:3]}")
                    ng += 1
                else:
                    print(f"  ✓ {label}モーダル: 日本語文字 0")
                page.keyboard.press("Escape")
                page.wait_for_timeout(150)

            # R80-A4: **用語の再混入を止める番人**。同じアバターが「社員/メンバー/
            # キャラクター/部署」など6通りで呼ばれていて初見に伝わらなかった（配布前監査）。
            # 画面の呼称は「プロジェクト/セッション/サブエージェント」の3語に統一したので、
            # 廃止した語がUI文字列（strings.js と PWA）へ戻ってきたらここで落とす。
            banned = ["社員", "メンバー", "キャラクター", "部署", "部下"]
            sources = [pathlib.Path("ui/iso/strings.js"), pathlib.Path("relay/src/worker.js")]
            leaks = []
            for src in sources:
                body = src.read_text(encoding="utf-8")
                for line in body.splitlines():
                    if line.lstrip().startswith("//") or line.lstrip().startswith("*"):
                        continue          # コメントは説明文なので対象外
                    for word in banned:
                        # UIに出る文字列リテラルの中だけを見る
                        if word in line and ('"' in line or "'" in line):
                            leaks.append(f"{src.name}: {word} — {line.strip()[:70]}")
            if leaks:
                print(f"  ✗ 廃止した用語がUI文字列に混入（{len(leaks)}件）:")
                for x in leaks[:5]:
                    print(f"      {x}")
                ng += 1
            else:
                print("  ✓ 用語統一（社員/メンバー/キャラクター/部署/部下 を画面に出さない）")

            real = [e for e in errors if "Failed to load resource" not in e]
            if real:
                print(f"  ✗ JSエラー: {real[:3]}")
                ng += 1
            else:
                print("  ✓ console error 0")
            browser.close()
    finally:
        proc.terminate()
    if ng:
        print(f"新UI i18nカナリア: {ng} 件失敗")
    else:
        print("✓ 新UI i18nカナリア合格（lang=en 全面英語＋console 0）")
    return 1 if ng else 0


if __name__ == "__main__":
    sys.exit(main())
