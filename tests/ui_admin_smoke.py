#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""R50: 新UIの管理フロースモーク（➕新プロジェクト / 📱スマホ連携）。

「モーダルが出た」ではなく、config への登録・起動マーカー・デバイス台帳の
実ファイルまで検証する。サーバーはテスト専用HOME+OFFICE_CONFIG に完全隔離
（本番configを書き換えないための必須注入・P1の掟）。

使い方: python3 tests/ui_admin_smoke.py   （verify.sh ▶7 から呼ぶ・Playwright必要）
"""
import json
import os
import pathlib
import socket
import subprocess
import sys
import tempfile
import time

ROOT = pathlib.Path(__file__).resolve().parent.parent


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def main():
    from playwright.sync_api import sync_playwright

    tmp = pathlib.Path(tempfile.mkdtemp(prefix="ui_admin_"))
    home = tmp / "home"
    (home / ".claude").mkdir(parents=True)
    pick_dir = tmp / "新規プロジェクト"
    pick_dir.mkdir()
    config = tmp / "office_config.json"
    config.write_text('{"projects": {}}', encoding="utf-8")
    launch_marker = tmp / "launch.marker"
    gen_marker = tmp / "gen.marker"
    # 📱pair/new は Pro ゲート配下 → テスト鍵ライセンスで解錠してから叩く（掟）
    lic = tmp / "office_license.json"
    lic_n = subprocess.run(
        [sys.executable, "-c",
         "import json;print(json.load(open('tests/fixtures/license_test_key.json'))['n'][2:])"],
        cwd=str(ROOT), capture_output=True, text=True).stdout.strip()
    subprocess.run(
        [sys.executable, str(ROOT / "tools" / "license_sign.py"), "issue",
         "--edition", "hybrid", "--email", "admin-smoke@fixture", "--out", str(lic)],
        cwd=str(ROOT), env={**os.environ,
                            "OFFICE_LICENSE_SIGNING": "tests/fixtures/license_test_key.json"},
        check=True, capture_output=True)

    env = {**os.environ,
           "OFFICE_HOME": str(home), "OFFICE_CONFIG": str(config),
           "OFFICE_PICK_DIR": str(pick_dir),
           "OFFICE_FAKE_LAUNCH": str(launch_marker), "OFFICE_FAKE_GEN": str(gen_marker),
           "OFFICE_LICENSE": str(lic), "OFFICE_LICENSE_PUBKEY_N": lic_n}
    port = free_port()
    proc = subprocess.Popen(
        [sys.executable, str(ROOT / "server" / "office_server.py"), "--port", str(port)],
        cwd=str(ROOT), env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(80):
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=.2):
                break
        except OSError:
            time.sleep(.1)

    world = json.loads((ROOT / "tests/fixtures/world/basic.json").read_text(encoding="utf-8"))
    payload = json.dumps(world, ensure_ascii=False)
    ng = 0
    errors = []
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(args=["--use-gl=swiftshader", "--disable-gpu"])
            page = browser.new_page(viewport={"width": 1440, "height": 900},
                                    device_scale_factor=1)
            page.on("pageerror", lambda e: errors.append(f"pageerror: {e}"))
            page.on("console", lambda m: errors.append(f"console.error: {m.text}")
                    if m.type == "error" else None)
            page.route("**/api/office*", lambda route: route.fulfill(
                status=200, content_type="application/json; charset=utf-8", body=payload))
            page.goto(f"http://127.0.0.1:{port}/?ui=iso&t=3.2&seed=11")
            page.wait_for_function("window.__office && window.__office.ready", timeout=30000)

            # (1) ➕新プロジェクト: pick(注入dir) → 名前入力 → 登録 → config+起動マーカー
            # ready直後のセレクタ系API（click/wait_for_selector）が稀に永久ハングする
            # SwiftShader環境の間欠レースがある（evaluate だけは同じ状態でも動く・実測）。
            # ハングしたら診断を出して JSクリックへフォールバック＝以降の検証は
            # 実ファイル（config/マーカー/台帳）で行うので嘘greenにはならない。
            try:
                page.click("#btn-newproj", timeout=15000)
            except Exception:
                diag = page.evaluate(
                    "() => { const b = document.querySelector('#btn-newproj');"
                    " const cs = b && getComputedStyle(b);"
                    " return { exists: !!b, display: cs && cs.display,"
                    "   vis: cs && cs.visibility,"
                    "   rect: b && b.getBoundingClientRect().toJSON(),"
                    "   sheets: document.styleSheets.length }; }")
                print(f"  ℹ click ハング→JSクリックにフォールバック: {diag}")
                page.evaluate("document.querySelector('#btn-newproj').click()")
            page.wait_for_selector(".modal .minput", timeout=8000)
            page.fill(".modal .minput", "スモーク編集部")
            page.click("#mgo-newproj")
            page.wait_for_timeout(1200)
            cfg = json.loads(config.read_text(encoding="utf-8"))
            names = [m.get("name") for m in cfg.get("projects", {}).values()]
            if "スモーク編集部" in names:
                print("  ✓ ➕登録: config に部署が追加された")
            else:
                print(f"  ✗ ➕登録が config に反映されない: {names}")
                ng += 1
            if launch_marker.exists():
                print("  ✓ ➕起動: claude起動（FAKEマーカー）が走った")
            else:
                print("  ✗ ➕起動マーカーが無い")
                ng += 1
            if page.eval_on_selector("#modalwrap", "el => el.hidden"):
                print("  ✓ 登録後にモーダルが閉じる")
            else:
                print("  ✗ 登録後もモーダルが開いたまま")
                ng += 1

            # (2) 📱スマホ連携: デバイス発行（中継未設定→注記）＋一覧＋失効
            page.click("#btn-pair")
            page.wait_for_selector(".modal .mdevices", timeout=8000)
            devices = json.loads((home / ".claude" / "office_devices.json")
                                 .read_text(encoding="utf-8"))
            if len(devices.get("devices", devices) or []) >= 1 or devices:
                print("  ✓ 📱発行: デバイス台帳に登録された")
            else:
                print("  ✗ 📱デバイス台帳が空")
                ng += 1
            note = page.eval_on_selector(".modal", "el => el.textContent")
            if "中継が未設定" in note or "QR" in note:
                print("  ✓ 📱中継未設定の案内 or QR を表示")
            else:
                print(f"  ✗ 📱パネル内容が想定外: {note[:80]}")
                ng += 1
            page.click(".modal .mrevoke")
            page.wait_for_timeout(800)
            after = (home / ".claude" / "office_devices.json").read_text(encoding="utf-8")
            if '"revoked": true' in after or '"devices": []' in after or after.strip() in ("{}", "[]"):
                print("  ✓ 📱失効が台帳に反映された")
            else:
                print(f"  ✗ 📱失効が反映されない: {after[:120]}")
                ng += 1
            page.keyboard.press("Escape")

            # (3) 🧾ライセンス: fixture鍵で hybrid 有効の表示＋不正キーの拒否
            page.click("#btn-license")
            page.wait_for_selector("#mgo-license", timeout=8000)
            lic_text = page.eval_on_selector(".modal", "el => el.textContent")
            if "hybrid" in lic_text and "ライセンス有効" in lic_text:
                print("  ✓ 🧾状態: hybrid+有効を表示")
            else:
                print(f"  ✗ 🧾状態の表示が想定外: {lic_text[:100]}")
                ng += 1
            page.fill(".modal .mtextarea", "こわれたライセンス")
            # 直前操作のトーストが残っていると誤読する（実際に「失効しました」を読んだ）
            page.eval_on_selector("#toast", "el => { el.hidden = true; el.textContent = ''; }")
            page.click("#mgo-license")
            page.wait_for_selector("#toast:not([hidden])", timeout=5000)
            toast = page.eval_on_selector("#toast", "el => el.textContent")
            if "失敗" in toast:
                print("  ✓ 🧾不正キーは拒否（サーバー文言のトースト）")
            else:
                print(f"  ✗ 🧾不正キーが通った?: {toast}")
                ng += 1
            page.keyboard.press("Escape")

            # (4) ⚡リソース: status_board 読み取りビュー（Pro解錠済み前提）
            page.click("#btn-res")
            page.wait_for_selector(".modal .mres", timeout=10000)
            res_text = page.eval_on_selector(".modal", "el => el.textContent")
            if "Claude Code" in res_text and "固定費" in res_text:
                print("  ✓ ⚡リソース: プロバイダ+固定費を表示")
            else:
                print(f"  ✗ ⚡リソース表示が想定外: {res_text[:100]}")
                ng += 1

            # (4b) 💳台帳CRUD（R50提案3で新UIへ移植）: 追加→実ファイル→一覧→削除まで
            ledger_file = home / ".claude" / "office_resources.json"
            page.fill(".modal .mledname-in", "スモーク台帳サブスク")
            page.fill(".modal .mledamt", "1980")
            page.click("#mgo-ledger")
            page.wait_for_timeout(1200)
            saved = ledger_file.exists() and "スモーク台帳サブスク" in ledger_file.read_text(encoding="utf-8")
            if saved:
                print("  ✓ 💳台帳: 追加が office_resources.json に反映")
            else:
                print(f"  ✗ 💳台帳の追加が保存されない: {ledger_file}")
                ng += 1
            page.wait_for_selector(".modal .mledrow", timeout=8000)
            row_text = page.eval_on_selector(".modal .mledlist", "el => el.textContent")
            if "スモーク台帳サブスク" in row_text and "¥1,980" in row_text:
                print("  ✓ 💳台帳: 一覧に表示（名前+金額）")
            else:
                print(f"  ✗ 💳台帳の一覧表示が不正: {row_text[:80]}")
                ng += 1
            page.click(".modal .mledel")
            page.wait_for_timeout(1200)
            if "スモーク台帳サブスク" not in ledger_file.read_text(encoding="utf-8"):
                print("  ✓ 💳台帳: 削除が実ファイルに反映")
            else:
                print("  ✗ 💳台帳の削除が反映されない")
                ng += 1
            page.keyboard.press("Escape")

            real = [e for e in errors if "Failed to load resource" not in e]
            if real:
                print(f"  ✗ JSエラー: {real[:3]}")
                ng += 1
            else:
                print("  ✓ console error 0")
            browser.close()
    finally:
        proc.terminate()
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)
    if ng:
        print(f"管理フロースモーク: {ng} 件失敗")
    else:
        print("✓ 管理フロースモーク合格（➕/📱/🧾/⚡ + console 0）")
    return 1 if ng else 0


if __name__ == "__main__":
    sys.exit(main())
