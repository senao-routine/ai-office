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
    # R66: projects在=Claude Code接続済み→🔑🅰の「/login で切替」ガイドが出る状態を作る
    (home / ".claude" / "projects").mkdir()
    pick_dir = tmp / "新規プロジェクト"
    pick_dir.mkdir()
    config = tmp / "office_config.json"
    config.write_text('{"projects": {}}', encoding="utf-8")
    launch_marker = tmp / "launch.marker"
    gen_marker = tmp / "gen.marker"
    # R85-2: 旧「テスト鍵ライセンスで解錠」はライセンス機構撤去で不要（全機能が素で開く）

    env = {**os.environ,
           "OFFICE_HOME": str(home), "OFFICE_CONFIG": str(config),
           "OFFICE_PICK_DIR": str(pick_dir),
           "OFFICE_FAKE_LAUNCH": str(launch_marker), "OFFICE_FAKE_GEN": str(gen_marker)}
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

            # (3) 🧾ライセンスは R85-2 撤去 → ボタン/パネルが**存在しない**ことをピン
            #     （復活させると R84 全機能無料化と矛盾＝廃止商品の宣伝が再発する）
            lic_btn = page.evaluate("() => !!document.querySelector('#btn-license')")
            if not lic_btn:
                print("  ✓ 🧾ライセンスUIが存在しない（R85-2撤去の維持）")
            else:
                print("  ✗ 🧾ライセンスボタンが復活している")
                ng += 1

            # (4) ⚡リソース: status_board 読み取りビュー
            page.click("#btn-res")
            page.wait_for_selector(".modal .mres", timeout=10000)
            res_text = page.eval_on_selector(".modal", "el => el.textContent")
            if "Claude Code" in res_text and "固定費" in res_text:
                print("  ✓ ⚡リソース: プロバイダ+固定費を表示")
            else:
                print(f"  ✗ ⚡リソース表示が想定外: {res_text[:100]}")
                ng += 1

            # (4a2) R67 モーダル切断の回帰ピン: 内容が縦に伸びても最下部要素へ
            #       スクロールで到達でき、実クリック標的になる（従来: overflow切断で🔑下部が操作不能）
            fit = page.evaluate(
                "() => { const m = document.querySelector('.modal');"
                "  const last = m.lastElementChild;"
                "  m.scrollTop = m.scrollHeight;"
                "  const r = last.getBoundingClientRect();"
                "  const mr = m.getBoundingClientRect();"
                "  return { inFrame: r.bottom <= mr.bottom + 2,"
                "    onScreen: r.bottom <= innerHeight && r.height > 0,"
                "    scrolled: m.scrollTop > 0 || m.scrollHeight <= m.clientHeight }; }")
            if fit["inFrame"] and fit["onScreen"] and fit["scrolled"]:
                print("  ✓ R67 ⚡モーダル: 内部スクロールで最下部まで到達（切断なし）")
            else:
                print(f"  ✗ モーダル下部が切断されている: {fit}")
                ng += 1
            page.evaluate("document.querySelector('.modal').scrollTop = 0")

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

            # (4b3) R85-3 💱為替編集: 保存が実ファイル(fx.jpyPerUsd)へ反映（UI呼び手ゼロだったAPIの接続ピン）
            page.wait_for_selector("#mgo-fx", timeout=8000)
            page.fill(".modal .mledform:has(#mgo-fx) .mledamt", "150.5")
            page.click("#mgo-fx")
            page.wait_for_timeout(1500)
            try:
                fx_saved = json.loads(ledger_file.read_text(encoding="utf-8")) \
                    .get("fx", {}).get("jpyPerUsd") == 150.5
            except (OSError, json.JSONDecodeError):
                fx_saved = False
            if fx_saved:
                print("  ✓ R85-3 💱為替: 保存が fx.jpyPerUsd に反映")
            else:
                print("  ✗ 💱為替の保存が反映されない")
                ng += 1
            page.wait_for_selector(".modal .mkeygrp", timeout=8000)   # 再描画完了を待つ

            # (4b2) R66 🔑グループ構造: 🅰自動（キー入力UIなし・Claudeの/loginガイド）／
            #       🅱APIキー（キー形式プレースホルダ・発行場所）＝「接続方法が分からない」FBのピン
            page.wait_for_selector(".modal .mkeygrp", timeout=8000)
            grp = page.evaluate(
                "() => { const gs = [...document.querySelectorAll('.modal .mkeygrp')];"
                " const a = gs[0]; const b = gs[1];"
                " return { n: gs.length,"
                "   aHasClaude: !!a && a.textContent.includes('Claude Code'),"
                "   aNoKeyBtn: !!a && !a.querySelector('.mkeybtn'),"
                "   aLoginGuide: !!a && a.textContent.includes('/login'),"
                "   bHasOpenrouter: !!b && b.textContent.includes('OpenRouter'),"
                "   bGetFrom: !!b && b.textContent.includes('openrouter.ai/settings/keys') }; }")
            if (grp["n"] == 2 and grp["aHasClaude"] and grp["aNoKeyBtn"]
                    and grp["aLoginGuide"] and grp["bHasOpenrouter"] and grp["bGetFrom"]):
                print("  ✓ 🔑グループ: 🅰自動（/loginガイド・キーUIなし）/🅱キー（発行場所）")
            else:
                print(f"  ✗ 🔑グループ構造が想定外: {grp}")
                ng += 1
            ph = page.evaluate(
                "() => { const btn = document.querySelector("
                "'.modal .mkeybtn[data-key=\"OPENROUTER_API_KEY\"]');"
                " if (!btn) return ''; btn.click();"
                " const inp = btn.closest('.mkeyrow').querySelector('.mkeyin');"
                " const v = inp ? inp.placeholder : ''; btn.click(); return v; }")
            if "sk-or-v1" in ph:
                print("  ✓ 🔑キー形式プレースホルダ（sk-or-v1-…）")
            else:
                print(f"  ✗ プレースホルダが出ない: {ph!r}")
                ng += 1

            # (4c) 🔑アカウント連携（R54-F）: 接続→キー保存が office_secrets 実ファイルへ
            page.wait_for_selector('.modal .mkeybtn[data-key="OPENAI_API_KEY"]', timeout=8000)
            page.click('.modal .mkeybtn[data-key="OPENAI_API_KEY"]')
            page.wait_for_selector(".modal .mkeyin", timeout=4000)
            test_key = "sk-office-smoke-" + "a" * 24
            page.fill(".modal .mkeyin", test_key)
            page.click(".modal .mkeysave")
            page.wait_for_timeout(1200)
            secrets_file = home / ".claude" / "office_secrets"
            if (secrets_file.exists()
                    and f"OPENAI_API_KEY={test_key}" in secrets_file.read_text(encoding="utf-8")):
                print("  ✓ 🔑連携: キー保存が office_secrets(600) に反映")
            else:
                print(f"  ✗ 🔑キーが保存されない: {secrets_file}")
                ng += 1
            # 再描画後は connected（緑ドット+masked表示）へ
            page.wait_for_selector(".modal .mkeys", timeout=8000)
            dot_on = page.eval_on_selector_all(
                ".modal .mkeyrow",
                "els => els.some(r => r.querySelector('.mkeydot.on') &&"
                " r.textContent.includes('OpenAI API'))")
            if dot_on:
                print("  ✓ 🔑連携: 保存後に接続済み表示へ更新")
            else:
                print("  ✗ 🔑接続済み表示にならない")
                ng += 1

            # (4d) R65 🔑解除: 2クリック制（1クリック目=アームのみ）→実ファイルから行が消える
            row_sel = ('.modal .mkeyrow:has(.mkeybtn[data-key="OPENAI_API_KEY"])'
                       ' .mkeyrevoke')
            page.wait_for_selector(row_sel, timeout=8000)
            page.click(row_sel)
            page.wait_for_timeout(300)
            if f"OPENAI_API_KEY={test_key}" in secrets_file.read_text(encoding="utf-8"):
                print("  ✓ 🔑解除: 1クリック目はアームのみ（誤爆ガード）")
            else:
                print("  ✗ 🔑解除が1クリックで発動した")
                ng += 1
            page.click(row_sel)
            page.wait_for_timeout(1200)
            body_txt = secrets_file.read_text(encoding="utf-8") if secrets_file.exists() else ""
            if f"OPENAI_API_KEY={test_key}" not in body_txt:
                print("  ✓ 🔑解除: 2クリック目で office_secrets から行が消えた")
            else:
                print(f"  ✗ 🔑解除が反映されない: {body_txt[:80]}")
                ng += 1
            page.keyboard.press("Escape")

            # (5) R85-3 ⚙設定: ダークテーマ切替（th-dark クラス＋実背景色の変化を実測）と復帰
            page.click("#btn-settings")
            page.wait_for_selector(".modal .mkeybtn", timeout=8000)
            light_bg = page.evaluate(
                "() => getComputedStyle(document.querySelector('.side')).backgroundColor")
            page.evaluate(
                "() => [...document.querySelectorAll('.modal .mkeybtn')]"
                ".find(b => b.textContent === 'ダーク').click()")
            page.wait_for_timeout(400)
            dark = page.evaluate(
                "() => ({ cls: document.querySelector('.ui-iso').classList.contains('th-dark'),"
                "  bg: getComputedStyle(document.querySelector('.side')).backgroundColor,"
                "  saved: localStorage.getItem('aioffice.iso.theme') })")
            if dark["cls"] and dark["bg"] != light_bg and dark["saved"] == "dark":
                print("  ✓ R85-3 ⚙ダーク: th-dark適用+実背景色変化+localStorage永続")
            else:
                print(f"  ✗ ダークテーマが効かない: {dark} light_bg={light_bg}")
                ng += 1
            page.evaluate(
                "() => [...document.querySelectorAll('.modal .mkeybtn')]"
                ".find(b => b.textContent === 'ライト').click()")
            page.wait_for_timeout(300)
            if not page.evaluate("() => document.querySelector('.ui-iso').classList.contains('th-dark')"):
                print("  ✓ R85-3 ⚙ライト復帰")
            else:
                print("  ✗ ライトへ戻らない")
                ng += 1
            page.keyboard.press("Escape")

            # (6) R85-3 ▶プロジェクト起動: モーダル到達（fixtureは launchable 空＝空状態文言）
            page.click("#btn-launch")
            page.wait_for_selector(".modal .mtitle", timeout=8000)
            launch_txt = page.eval_on_selector(".modal", "el => el.textContent")
            if "プロジェクト起動" in launch_txt and (
                    "まだありません" in launch_txt or "▶" in launch_txt):
                print("  ✓ R85-3 ▶起動モーダル到達（空状態/一覧）")
            else:
                print(f"  ✗ ▶起動モーダルが想定外: {launch_txt[:80]}")
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
        print("✓ 管理フロースモーク合格（➕/📱/🧾撤去維持/⚡ + console 0）")
    return 1 if ng else 0


if __name__ == "__main__":
    sys.exit(main())
