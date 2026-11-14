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
# R90-S1: 製品の既定スタイル iso（方向C）を検査する。
STYLE = os.environ.get("UI_STYLE", "iso")



def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def exercise_provider(page, fake_mode):
    """R103-5b: 実サーバーの fake 応答と保存前後のリクエストを検査する。"""
    ng = 0

    def check(label, passed):
        nonlocal ng
        print(f"  {'✓' if passed else '✗'} R103-5b {fake_mode}: {label}")
        ng += 0 if passed else 1

    origin = page.evaluate("location.origin")

    def provider_status():
        body = page.request.get(origin + "/api/keys/status", headers={"X-Office-Local": "1"}).json()
        return next((p for p in body.get("providers", []) if p.get("id") == "anthropic"), {})

    def provider_response(response, with_value):
        if "/api/providers/test" not in response.url or response.request.method != "POST":
            return False
        body = response.request.post_data_json
        return (isinstance(body, dict) and body.get("id") == "anthropic"
                and ("value" in body) == with_value)

    wizard = '.modal .mkeywizard[data-provider="anthropic"]'
    row = '.modal .mkeygrp[data-key-group="api"] .mkeyrow[data-provider="anthropic"]'
    test_key = "sk-ant-admin01-office-smoke-" + fake_mode + "a" * 24
    try:
        before = provider_status()
        page.click(".modal .mkeyadd")
        page.wait_for_selector('.modal .mkeychoice[data-provider="anthropic"]', timeout=8000)
        choices = page.eval_on_selector(".modal .mkeychoices", "el => el.textContent")
        check("未接続一覧に Anthropic・OpenAI・Mistral と測れるものを表示",
              all(name in choices for name in ("Anthropic", "OpenAI", "Mistral"))
              and all(ja in choices or en in choices for ja, en in (
                  ("消費", "Spend"), ("上限", "Limit"), ("残高", "Balance"),
                  ("接続の確認だけ", "Connection check only"))))
        page.click('.modal .mkeychoice[data-provider="anthropic"]')
        page.wait_for_selector(wizard + " .mkeyin", timeout=8000)
        guide = page.eval_on_selector(wizard, "el => el.textContent")
        check("正本の hint・発行場所を表示し、発行場所をリンクにしない",
              before.get("hint", "") in guide and before.get("getFrom", "") in guide
              and page.locator(wizard + " a").count() == 0)
        input_meta = page.eval_on_selector(wizard + " .mkeyin", "el => ({type: el.type, ph: el.placeholder})")
        untested_text = page.eval_on_selector(wizard + " .mkeyresult", "el => el.textContent")
        check("鍵の欄は password・正本の placeholder・保存前は無効",
              input_meta["type"] == "password" and input_meta["ph"] == before.get("ph")
              and page.locator(wizard + " .mkeysave").is_disabled())
        page.fill(wizard + " .mkeyin", test_key)

        with page.expect_response(lambda r: provider_response(r, True), timeout=10000) as tested:
            page.click(wizard + " .mkeytest")
        response = tested.value
        result = response.json().get("result", {})
        expected_ok = fake_mode == "ok"
        state_class = ".ok" if expected_ok else ".fail"
        page.wait_for_selector(wizard + " .mkeyresult" + state_class, timeout=8000)
        result_text = page.eval_on_selector(wizard + " .mkeyresult", "el => el.textContent")
        check("保存前に入力した鍵を JSON でテストし、サーバーの言葉をそのまま表示",
              response.request.post_data_json == {"id": "anthropic", "value": test_key}
              and response.request.headers.get("x-office-local") == "1"
              and "application/json" in response.request.headers.get("content-type", "")
              and result.get("ok") is expected_ok and result_text == result.get("message")
              and not provider_status().get("connected"))
        check("成功後だけ通常の保存を押せる",
              page.locator(wizard + " .mkeysave").is_disabled() == (not expected_ok))

        if expected_ok:
            # テストを済ませた鍵と異なる値へ変更したら、古い結果で保存できない。
            page.fill(wizard + " .mkeyin", test_key + "b")
            check("鍵を編集すると古い結果と保存許可が消える",
                  page.locator(wizard + " .mkeysave").is_disabled()
                  and page.eval_on_selector(wizard + " .mkeyresult", "el => el.textContent") == untested_text
                  and page.locator(wizard + " .mkeyresult.ok, " + wizard + " .mkeyresult.fail").count() == 0)
            page.fill(wizard + " .mkeyin", test_key)
            page.wait_for_timeout(3200)    # 同一プロバイダの 3 秒制限を守る
            with page.expect_response(lambda r: provider_response(r, True), timeout=10000) as retested:
                page.click(wizard + " .mkeytest")
            result = retested.value.json().get("result", {})
            page.wait_for_selector(wizard + " .mkeyresult.ok", timeout=8000)
            check("編集後も再テストの言葉をそのまま表示",
                  result.get("ok") is True
                  and page.eval_on_selector(wizard + " .mkeyresult", "el => el.textContent") == result.get("message"))
            save_selector = wizard + " .mkeysave"
        else:
            page.click(wizard + " .mkeyforce")
            page.wait_for_selector(wizard + " .mkeyforceconfirm", timeout=8000)
            check("失敗時の『それでも保存』は 1 クリックでは保存しない",
                  not provider_status().get("connected")
                  and page.locator(wizard + " .mkeysave").is_disabled())
            save_selector = wizard + " .mkeyforceconfirm"

        with page.expect_response(lambda r: provider_response(r, False), timeout=12000) as saved_test:
            with page.expect_response(
                    lambda r: "/api/keys/set" in r.url and r.request.method == "POST", timeout=10000) as saved:
                page.click(save_selector)
        saved_result = saved_test.value.json().get("result", {})
        page.wait_for_selector(row + " .mkeyresult" + state_class, timeout=12000)
        check("keyName で保存し、保存済みの鍵を value 無しで 1 回テスト",
              saved.value.request.post_data_json == {"name": before.get("keyName"), "value": test_key}
              and saved_test.value.request.post_data_json == {"id": "anthropic"}
              and saved_result.get("ok") is expected_ok and provider_status().get("connected") is True)
        check("接続行に状態の点・最後の言葉・手動予算の案内を表示し、入力欄を空にする",
              page.locator(row + (" .mkeydot.on" if expected_ok else " .mkeydot.fail")).count() == 1
              and page.eval_on_selector(row + " .mkeyresult", "el => el.textContent") == saved_result.get("message")
              and ("上限は ⚡ の予算で" in page.eval_on_selector(row, "el => el.textContent")
                   or "Set the limit in the ⚡ budget section" in page.eval_on_selector(row, "el => el.textContent"))
              and page.evaluate("() => [...document.querySelectorAll('.modal .mkeyin')].every(el => el.value === '')")
              and test_key not in page.eval_on_selector(".modal", "el => el.textContent"))
        page.click(".modal .mkeyadd")
        page.wait_for_selector(".modal .mkeychoices", timeout=8000)
        check("接続済みの Anthropic は『＋ つなぐ』の一覧から消える",
              page.locator('.modal .mkeychoice[data-provider="anthropic"]').count() == 0)
        page.click(".modal .mkeycancel")

        page.wait_for_timeout(3200)
        with page.expect_response(lambda r: provider_response(r, False), timeout=10000) as row_test:
            page.click(row + " .mkeyretest")
        row_result = row_test.value.json().get("result", {})
        page.wait_for_selector(row + " .mkeyresult" + state_class, timeout=8000)
        check("再テストも value 無しで呼び、行にサーバーの言葉をそのまま表示",
              row_test.value.request.post_data_json == {"id": "anthropic"}
              and row_result.get("ok") is expected_ok
              and page.eval_on_selector(row + " .mkeyresult", "el => el.textContent") == row_result.get("message"))
        page.click(row + " .mkeyrevoke")
        check("外す 1 クリック目はアームだけ", provider_status().get("connected") is True)
        page.click(row + " .mkeyrevoke")
        page.wait_for_selector(row, state="detached", timeout=10000)
        check("外す 2 クリック目で接続行と保存状態が消える", not provider_status().get("connected"))
    except Exception as exc:                                        # noqa: BLE001
        # 操作エラーの文字列に入力値が含まれる可能性があるため型名だけを報告する。
        check(f"接続ウィザードの操作を完了（{type(exc).__name__}）", False)
    return ng


def main():
    from playwright.sync_api import sync_playwright

    tmp_root = ROOT / "tests" / "artifacts" / ".tmp"
    tmp_root.mkdir(parents=True, exist_ok=True)
    tmp = pathlib.Path(tempfile.mkdtemp(prefix="ui_admin_", dir=tmp_root))
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
           "OFFICE_FAKE_LAUNCH": str(launch_marker), "OFFICE_FAKE_GEN": str(gen_marker),
           # R103-5b: 接続テストは外へ問い合わせず、成功・失敗の応答をサーバーで作る。
           "OFFICE_FAKE_PROVIDER_TEST": "ok", "PYTHONDONTWRITEBYTECODE": "1",
           # R100-0: スマホの登録は Mac の物理確認（osascript のダイアログ）と通知を出す＝テストでは注入口で置き換える
           "OFFICE_FAKE_CONFIRM": "ok", "OFFICE_FAKE_NOTIFY": str(home / "notify.txt")}
    port = free_port()
    proc = subprocess.Popen(
        [sys.executable, str(ROOT / "server" / "office_server.py"), "--port", str(port)],
        cwd=str(ROOT), env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    fail_proc = None
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
            page.goto(f"http://127.0.0.1:{port}/?ui={STYLE}&t=3.2&seed=11")
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
            # R100-0: 開いただけでは発行しない（発行は Mac の物理確認＝開くたびにダイアログを出さない）
            page.wait_for_selector("#pair-add", timeout=8000)
            if (home / ".claude" / "office_devices.json").exists():
                print("  ✗ R100-0 📱を開いただけで端末を発行した（Mac の確認が毎回出る）")
                ng += 1
            else:
                print("  ✓ R100-0 📱を開いただけでは発行しない（「＋ 新しいスマホを登録」で発行）")
            # b4-mac: 発行の前に名前を付けられる（既定「iPhone 月/日」）＝一覧が全部「スマホ」で見分けられなかった
            page.fill("#pair-name", "スモークの iPhone")
            page.click("#pair-add")
            page.wait_for_selector(".modal .mdevices", timeout=8000)
            devices = json.loads((home / ".claude" / "office_devices.json")
                                 .read_text(encoding="utf-8"))
            labels = [d.get("label") for d in (devices.get("devices") or {}).values()]
            row_meta = page.evaluate("""() => [...document.querySelectorAll('.modal .mdevices .mdev')].map((r) => ({
                name: (r.querySelector('.mdevname') || {}).textContent || '', sub: (r.querySelector('.mdevsub') || {}).textContent || '' }))""")
            if "スモークの iPhone" in labels and any(r["name"] == "スモークの iPhone" and ("登録" in r["sub"] or "Added" in r["sub"])
                                                  and ("未使用" in r["sub"] or "never used" in r["sub"]) for r in row_meta):
                print("  ✓ b4-mac 📱発行の名前が台帳と一覧に出る・一覧に登録日と「未使用」")
            else:
                print(f"  ✗ b4-mac 📱端末の名前・登録日・最後の使用が出ない: labels={labels} rows={row_meta}")
                ng += 1
            if len(devices.get("devices", devices) or []) >= 1 or devices:
                print("  ✓ 📱発行: デバイス台帳に登録された")
            else:
                print("  ✗ 📱デバイス台帳が空")
                ng += 1
            notified = (home / "notify.txt").read_text(encoding="utf-8") if (home / "notify.txt").exists() else ""
            if "スマホを登録しました" in notified or "Phone registered" in notified:
                print("  ✓ R100-0 📱登録は Mac に通知される（物理確認を通った後）")
            else:
                print(f"  ✗ R100-0 📱登録の通知が無い: {notified[:120]!r}")
                ng += 1
            note = page.eval_on_selector(".modal", "el => el.textContent")
            if "中継が未設定" in note or "QR" in note:
                print("  ✓ 📱中継未設定の案内 or QR を表示")
            else:
                print(f"  ✗ 📱パネル内容が想定外: {note[:80]}")
                ng += 1
            # R87-S6: 会話共有のトグル（別モデルレビュー: API だけあって UI が無かった）。
            # モーダルが開いているうち（失効→再描画→Escape の前）に、実ファイルまで往復する。
            try:
                page.wait_for_selector("#pair-dialog", state="attached", timeout=8000)
            except Exception:
                html = page.eval_on_selector(".modal", "el => el.innerHTML.slice(0, 500)")
                print(f"  ✗ #pair-dialog が出ない。modal={html!r} errors={errors[-4:]}")
                raise
            if page.eval_on_selector("#pair-dialog", "el => el.disabled"):
                # 封緘バックエンド（CommonCrypto）が無い環境では意図的に disabled＝verify ▶12 と同じく警告扱い
                print("  · 📱会話共有トグルは disabled（封緘バックエンド無し）＝往復は省略")
            else:
                page.click("#pair-dialog")
                page.wait_for_timeout(600)
                on = json.loads(config.read_text(encoding="utf-8")).get("dialogRelay")
                page.click("#pair-dialog")
                page.wait_for_timeout(600)
                off = json.loads(config.read_text(encoding="utf-8")).get("dialogRelay")
                if on is True and off is False:
                    print("  ✓ 📱「スマホに会話を見せる」トグル → office_config.json の dialogRelay が true/false で往復")
                else:
                    print(f"  ✗ 📱会話共有トグルが設定に届かない: on={on!r} off={off!r}")
                    ng += 1
            # R100-1b→R118（本人裁定 2026-10-13「常に ON」）: スマホから許可の欄（ON は期限なし＝ボタンは 1 つ・中継が無ければ押せない・
            # OFF と今日の件数・Face ID の言葉は出さない）。失効ボタン（.mrevoke）と同じ見た目のクラスを使わない＝下の「端末の失効」の押し間違いを生まない。
            apv = page.evaluate("""() => { const b = document.getElementById('pair-approve'); if (!b) return null;
                return { on: [...b.querySelectorAll('button[id^="apv-on"]')].map(n => [n.id, n.disabled]),
                         off: !!b.querySelector('#apv-off'), revokeLike: b.querySelectorAll('.mrevoke').length,
                         keyRows: document.querySelectorAll('.modal .apv-key-revoke').length, text: b.textContent }; }""")
            if (apv and [x[0] for x in apv["on"]] == ["apv-on"] and apv["off"] and apv["revokeLike"] == 0 and apv["keyRows"] == 0
                    and all(x[1] for x in apv["on"]) and "Face ID" not in apv["text"]):
                print("  ✓ R118 📱スマホから許可: ON のボタンは 1 つ（期限なし）・中継未設定では押せない・失効と別の見た目・許可鍵の行と Face ID の言葉なし")
            else:
                print(f"  ✗ R118 📱スマホから許可の欄が違う: {apv}")
                ng += 1
            # 押した結果をこの欄にその場で出す（描き直しを待たない）。中継未設定の木ではボタンが押せない＝ここでは押せる状態にしてから押す
            #（サーバーの ON は中継の有無を見ない）。R118: ON は期限なし（until＝2100-01-01）
            page.evaluate("() => { const b = document.getElementById('apv-on'); b.disabled = false; b.click(); }")
            try:
                page.wait_for_function("() => /^(ON（期限なし|On \\(no time limit)/.test((document.getElementById('apv-state') || {}).textContent || '')",
                                       timeout=8000)
            except Exception:
                pass
            st = page.evaluate("() => (document.getElementById('apv-state') || {}).textContent || ''")
            onf = home / ".claude" / "office_remote_approve.json"
            until = json.loads(onf.read_text(encoding="utf-8")).get("until", 0) if onf.exists() else 0
            if until == 4102444800 and st.startswith(("ON（期限なし", "On (no time limit")):
                print(f"  ✓ R118 📱ON を押すと、その場で「ON（期限なし）」が出る・設定に届く: {st[:40]}")
            else:
                print(f"  ✗ R118 📱ON の結果が欄に出ない／設定に届かない: state={st!r} until={until}")
                ng += 1
            page.evaluate("() => document.getElementById('apv-off').click()")
            page.wait_for_timeout(800)
            # Astra（4f880e4・medium）: R118 より前に 1/4/12 時間で ON にした値（until が 2100 年より前）は移さない＝その期限で OFF になる。
            # 欄もヘッダーの札も「期限なし」と言わず、実際の期限を出す（古い daemon が書いた形をそのまま置いて描き直す）
            legacy_until = int(time.time()) + 3600
            onf.write_text(json.dumps({"v": 1, "until": legacy_until, "set": int(time.time())}), encoding="utf-8")
            page.evaluate("() => document.getElementById('btn-pair').click()")
            try:
                page.wait_for_function("() => /まで|until/.test((document.getElementById('apv-state') || {}).textContent || '')", timeout=8000)
            except Exception:
                pass
            legacy = page.evaluate("""() => { const b = document.getElementById('apvbadge'), g = b && b.querySelector('.apvgo');
                return { state: (document.getElementById('apv-state') || {}).textContent || '', badge: b && !b.hidden ? g.textContent : '' }; }""")
            if (not legacy["state"].startswith(("ON（期限なし", "On (no time limit"))
                    and ("まで" in legacy["state"] or "until" in legacy["state"]) and ("まで" in legacy["badge"] or "until" in legacy["badge"])):
                print(f"  ✓ R118（レビュー）期限つきの古い ON は「期限なし」と言わず期限を出す（欄: {legacy['state'][:28]}…・札: {legacy['badge']}）")
            else:
                print(f"  ✗ R118（レビュー）期限つきの古い ON を期限なしと言う／期限が出ない: {legacy}")
                ng += 1
            page.evaluate("() => document.getElementById('apv-off').click()")
            page.wait_for_timeout(800)
            # b4-mac: 色の役割。主ボタンの塗り（.mgo）は「＋ 新しいスマホを登録」と「許可鍵を登録」だけ＝破壊操作（失効）にも
            # 中立の操作（時間・OFF）にも付けない。チェックボックスの行は .mcheck（.mdev の space-between に当てない）
            roles = page.evaluate("""() => { const m = document.querySelector('.modal');
                return { destructiveGo: m.querySelectorAll('.mgo.pair-revoke, .mgo.apv-key-revoke, .mgo.pair-stale').length,
                         neutralGo: m.querySelectorAll('#pair-approve .mgo').length,
                         revoke: m.querySelectorAll('.mrevoke.pair-revoke').length,
                         check: !!document.getElementById('pair-dialog')?.closest('label.mcheck') }; }""")
            if roles["destructiveGo"] == 0 and roles["neutralGo"] == 0 and roles["revoke"] >= 1 and roles["check"]:
                print("  ✓ b4-mac 📱破壊操作は .mrevoke・時間/OFF は副ボタン・主ボタンは登録の 2 つだけ・チェックの行は左寄せ")
            else:
                print(f"  ✗ b4-mac 📱ボタンの色の役割が違う: {roles}")
                ng += 1
            # b4-mac: 許可鍵の登録中、Mac の画面が段階と結果を言う（以前は長い 1 段落のまま何も変わらなかった）。
            # 状態（/api/approve/status）を差し替えて、許可鍵が付いた瞬間に「登録できました」が出ることを見る。
            fake = {"key": False}

            def status_route(route):
                resp = route.fetch()
                body = resp.json()
                if fake["key"]:
                    dev_id = next(iter((json.loads((home / ".claude" / "office_devices.json").read_text(encoding="utf-8"))
                                        .get("devices") or {}).keys()), "")
                    body["keys"] = {dev_id: {"fp": "AB12-CD34", "created": int(time.time()) + 5, "expires": int(time.time()) + 86400,
                                             "projects": "*", "lastUsed": 0, "active": True}}
                route.fulfill(response=resp, body=json.dumps(body), content_type="application/json; charset=utf-8")
            page.route("**/api/approve/status*", status_route)
            try:
                # R116-S0: Mac の「許可鍵を登録」（Mac 側の登録の受付）は画面から隠した（R103-1 以降は登録も ON もスマホだけ）。
                # 見えないことを確かめる。段階の画面（renderEnroll）は撤去（R116-S3）まで残すので、隠したボタンを JS で押して開き、下の検査を続ける
                vis = page.evaluate("""() => [...document.querySelectorAll('.modal .apv-key-add')]
                    .map((b) => b.getClientRects().length > 0 && getComputedStyle(b).visibility !== 'hidden')""")
                if vis and not any(vis):
                    print(f"  ✓ R116-S0 📱Mac の「許可鍵を登録」は見えない（{len(vis)} 台分・登録はスマホだけ）")
                else:
                    print(f"  ✗ R116-S0 📱Mac の「許可鍵を登録」が見えている／見つからない: {vis}")
                    ng += 1
                page.evaluate("() => document.querySelector('.modal .apv-key-add').click()")
                page.wait_for_selector("#apv-enroll-steps", timeout=8000)
                steps = page.eval_on_selector("#apv-enroll-steps", "el => [...el.children].map((li) => li.textContent)")
                fake["key"] = True
                page.wait_for_function("() => /登録できました|Registered/.test((document.getElementById('apv-enroll-result') || {}).textContent || '')",
                                       timeout=10000)
                res_text = page.eval_on_selector("#apv-enroll-result", "el => el.textContent")
                if len(steps) == 3 and ("受付中" in steps[0] or "Open" in steps[0]) and "AB12-CD34" in res_text:
                    print(f"  ✓ b4-mac 📱許可鍵の登録: 3 段の手順（{steps[0][:12]}…）→ 鍵が付くと「{res_text[:24]}」")
                else:
                    print(f"  ✗ b4-mac 📱許可鍵の登録の段階・結果が出ない: steps={steps} result={res_text!r}")
                    ng += 1
                # 3 秒後に一覧へ戻る
                page.wait_for_selector("#pair-add", timeout=10000)
            except Exception as exc:                                    # noqa: BLE001
                print(f"  ✗ b4-mac 📱許可鍵の登録の画面: {str(exc)[:160]}")
                ng += 1
            finally:
                page.unroute("**/api/approve/status*")
            page.wait_for_selector(".modal .pair-revoke", timeout=8000)
            page.click(".modal .pair-revoke")
            page.wait_for_timeout(800)
            after = (home / ".claude" / "office_devices.json").read_text(encoding="utf-8")
            if '"revoked": true' in after or '"devices": []' in after or after.strip() in ("{}", "[]"):
                print("  ✓ 📱失効が台帳に反映された")
            else:
                print(f"  ✗ 📱失効が反映されない: {after[:120]}")
                ng += 1
            # b4-mac: 失効済み・期限切れは畳み、ボタンを出さない（押しても何も起きないボタンを並べない）
            try:
                page.wait_for_selector(".modal .mdevdead", state="attached", timeout=8000)
            except Exception:                                           # noqa: BLE001
                pass
            dead = page.evaluate("""() => { const d = document.querySelector('.modal .mdevdead');
                return d ? { rows: d.querySelectorAll('.mdev').length, buttons: d.querySelectorAll('button').length,
                             summary: (d.querySelector('summary') || {}).textContent || '' } : null; }""")
            if dead and dead["rows"] >= 1 and dead["buttons"] == 0:
                print(f"  ✓ b4-mac 📱失効済みは「{dead['summary']}」に畳まれ、ボタンが無い")
            else:
                print(f"  ✗ b4-mac 📱失効済みの行にボタンがある／畳まれていない: {dead}")
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

            # (4b2) R103-5b 🔑グループ構造: 自動／接続済み AI サービス・API／その他。
            # 未接続 AI の入力は常設せず「＋ つなぐ」から 1 つずつ始める。
            page.wait_for_selector(".modal .mkeygrp", timeout=8000)
            grp = page.evaluate(
                "() => { const gs = [...document.querySelectorAll('.modal .mkeygrp')];"
                " const a = gs[0]; const b = gs[1]; const c = gs[2];"
                " return { n: gs.length,"
                "   aHasClaude: !!a && a.textContent.includes('Claude Code'),"
                "   aNoKeyBtn: !!a && !a.querySelector('.mkeybtn'),"
                "   aLoginGuide: !!a && a.textContent.includes('/login'),"
                "   bIsApi: !!b && b.dataset.keyGroup === 'api',"
                "   bHasAdd: !!b && !!b.querySelector('.mkeyadd'),"
                "   bNoRows: !!b && !b.querySelector('.mkeyrow[data-provider]'),"
                "   cIsOther: !!c && c.dataset.keyGroup === 'other',"
                "   cHasImage: !!c && !!c.querySelector('.mkeybtn[data-key=\"OPENAI_API_KEY\"]'),"
                "   cHasX: !!c && !!c.querySelector('.mkeybtn[data-key=\"X_BEARER_TOKEN\"]') }; }")
            if (grp["n"] == 3 and grp["aHasClaude"] and grp["aNoKeyBtn"]
                    and grp["aLoginGuide"] and grp["bIsApi"] and grp["bHasAdd"]
                    and grp["bNoRows"] and grp["cIsOther"] and grp["cHasImage"] and grp["cHasX"]):
                print("  ✓ 🔑グループ: 自動（/login ガイド）／AI（＋ つなぐ・接続済みだけ）／その他（画像・X）")
            else:
                print(f"  ✗ 🔑グループ構造が想定外: {grp}")
                ng += 1
            ng += exercise_provider(page, "ok")

            # fake は起動環境から読む。失敗用も別 HOME/config の実サーバーに通し、
            # API のブラウザー差し替えだけで失敗の保存ガードを検査したことにしない。
            fail_home = tmp / "fail_home"
            (fail_home / ".claude" / "projects").mkdir(parents=True)
            fail_config = tmp / "fail_config.json"
            fail_config.write_text('{"projects": {}}', encoding="utf-8")
            fail_env = {**env, "OFFICE_HOME": str(fail_home), "OFFICE_CONFIG": str(fail_config),
                        "OFFICE_FAKE_PROVIDER_TEST": "fail", "OFFICE_FAKE_NOTIFY": str(fail_home / "notify.txt")}
            fail_port = free_port()
            fail_proc = subprocess.Popen(
                [sys.executable, str(ROOT / "server" / "office_server.py"), "--port", str(fail_port)],
                cwd=str(ROOT), env=fail_env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            for _ in range(80):
                try:
                    with socket.create_connection(("127.0.0.1", fail_port), timeout=.2):
                        break
                except OSError:
                    time.sleep(.1)
            fail_page = browser.new_page(viewport={"width": 1440, "height": 900}, device_scale_factor=1)
            try:
                fail_page.on("pageerror", lambda e: errors.append(f"pageerror: {e}"))
                fail_page.on("console", lambda m: errors.append(f"console.error: {m.text}")
                             if m.type == "error" else None)
                fail_page.route("**/api/office*", lambda route: route.fulfill(
                    status=200, content_type="application/json; charset=utf-8", body=payload))
                fail_page.goto(f"http://127.0.0.1:{fail_port}/?ui={STYLE}&t=3.2&seed=11")
                fail_page.wait_for_function("window.__office && window.__office.ready", timeout=30000)
                fail_page.click("#btn-res")
                fail_page.wait_for_selector(".modal .mkeyadd", timeout=10000)
                ng += exercise_provider(fail_page, "fail")
            finally:
                fail_page.close()

            # (4c) 🔑アカウント連携（R54-F）: 接続→キー保存が office_secrets 実ファイルへ
            other_keys = '.modal .mkeygrp[data-key-group="other"]'
            image_row = other_keys + ' .mkeyrow:has(.mkeybtn[data-key="OPENAI_API_KEY"])'
            image_button = image_row + ' .mkeybtn[data-key="OPENAI_API_KEY"]'
            page.wait_for_selector(image_button, timeout=8000)
            page.click(image_button)
            page.wait_for_selector(image_row + " .mkeyin", timeout=4000)
            test_key = "sk-office-smoke-" + "a" * 24
            page.fill(image_row + " .mkeyin", test_key)
            page.click(image_row + " .mkeysave")
            page.wait_for_timeout(1200)
            secrets_file = home / ".claude" / "office_secrets"
            if (secrets_file.exists()
                    and f"OPENAI_API_KEY={test_key}" in secrets_file.read_text(encoding="utf-8")):
                print("  ✓ 🔑連携: キー保存が office_secrets(600) に反映")
            else:
                print(f"  ✗ 🔑キーが保存されない: {secrets_file}")
                ng += 1
            # 再描画後は connected（緑ドット+masked表示）へ
            page.wait_for_selector(other_keys, timeout=8000)
            dot_on = page.eval_on_selector_all(
                image_row,
                "els => els.some(r => r.querySelector('.mkeydot.on') &&"
                " r.textContent.includes('OpenAI API'))")
            if dot_on:
                print("  ✓ 🔑連携: 保存後に接続済み表示へ更新")
            else:
                print("  ✗ 🔑接続済み表示にならない")
                ng += 1

            # (4d) R65 🔑解除: 2クリック制（1クリック目=アームのみ）→実ファイルから行が消える
            row_sel = image_row + " .mkeyrevoke"
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
                print("  ✗ 🔑解除が反映されない（鍵の値は表示しない）")
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
        if fail_proc is not None:
            fail_proc.terminate()
            fail_proc.wait(timeout=5)
        proc.terminate()
        proc.wait(timeout=5)
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)
    if ng:
        print(f"管理フロースモーク: {ng} 件失敗")
    else:
        print("✓ 管理フロースモーク合格（➕/📱/🧾撤去維持/⚡ + console 0）")
    return 1 if ng else 0


if __name__ == "__main__":
    sys.exit(main())
