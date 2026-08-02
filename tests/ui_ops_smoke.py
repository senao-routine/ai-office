#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""R50 P6: 新UIの操作系スモーク（❗回答・指示コンポーズが実際に投函されるか）。

「ボタンが押せた」ではなく「office_inbox に実ファイルが書かれた」まで検証する
（配達経路の入口が本物であることの機械証明。UIだけ動いて投函されない嘘greenを防ぐ）。

使い方: python3 tests/ui_ops_smoke.py   （verify.sh ▶7 から呼ぶ・Playwright必要）
"""
import json
import pathlib
import shutil
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
from ui_shot import SWIFTSHADER, VIEWPORT, free_port, start_server  # noqa: E402

WORLD = ROOT / "tests" / "fixtures" / "world" / "basic.json"
INBOX = ROOT / ".ui_shot_home" / ".claude" / "office_inbox"


def wait_file(path, timeout=6.0):
    end = time.time() + timeout
    while time.time() < end:
        if path.is_file():
            return True
        time.sleep(0.1)
    return False


def main():
    from playwright.sync_api import sync_playwright

    world = json.loads(WORLD.read_text(encoding="utf-8"))
    payload = json.dumps(world, ensure_ascii=False)
    roster = {p["disp"]: p["session"] for p in world["roster"]}
    attn_session = roster["議事録アプリ"]        # 質問持ち（トレイの最優先）
    target_session = roster["制作本部(works)"]   # コンポーズ送信のターゲット

    shutil.rmtree(INBOX, ignore_errors=True)
    port = free_port()
    focus_marker = ROOT / ".ui_shot_home" / "focus.marker"
    focus_marker.unlink(missing_ok=True)
    proc = start_server(port, extra_env={"OFFICE_FAKE_FOCUS": str(focus_marker)})
    ng = 0
    errors = []
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(args=SWIFTSHADER)
            page = browser.new_page(viewport=VIEWPORT, device_scale_factor=1)
            page.on("pageerror", lambda e: errors.append(f"pageerror: {e}"))
            page.on("console", lambda m: errors.append(f"console.error: {m.text}")
                    if m.type == "error" else None)
            page.route("**/api/office*", lambda route: route.fulfill(
                status=200, content_type="application/json; charset=utf-8", body=payload))
            sb_payload = (ROOT / "tests" / "fixtures" / "status_board" / "basic.json"
                          ).read_text(encoding="utf-8")
            page.route("**/api/status_board*", lambda route: route.fulfill(
                status=200, content_type="application/json; charset=utf-8", body=sb_payload))
            page.goto(f"http://127.0.0.1:{port}/?ui=iso&t=3.2&seed=11")
            page.wait_for_function("window.__office && window.__office.ready", timeout=30000)
            page.wait_for_timeout(300)

            # (1) ❗トレイ: 数字キー1で選択肢を回答 → inbox に実ファイル
            page.keyboard.press("1")
            f1 = INBOX / f"{attn_session}.json"
            if wait_file(f1) and "選択肢「案A で進める」でお願いします。" in f1.read_text(encoding="utf-8"):
                print("  ✓ ❗回答: 数字キー1 → inbox 投函（選択肢の定型文）")
            else:
                print(f"  ✗ ❗回答が inbox に届かない: {f1}")
                ng += 1
            if page.eval_on_selector("#toast", "el => !el.hidden && el.textContent.includes('配達')"):
                print("  ✓ 送信トースト表示")
            else:
                print("  ✗ 送信トーストが出ない")
                ng += 1

            # (1b) 二重送信ガード: 回答済みトレイは「反映待ち」表示になり数字キーが無効
            if page.eval_on_selector("#attn", "el => el.textContent.includes('回答済み')"):
                print("  ✓ 回答済みトレイ=反映待ち表示（楽観状態）")
            else:
                print("  ✗ 回答後もトレイのボタンが残っている")
                ng += 1
            before1 = f1.stat().st_mtime
            page.keyboard.press("1")
            page.wait_for_timeout(500)
            if f1.stat().st_mtime == before1:
                print("  ✓ 回答済み❗への再打鍵は投函されない（二重送信ガード）")
            else:
                print("  ✗ 同じ❗へ二重送信された")
                ng += 1

            # (2) エージェント行クリック → コンポーズ → Enter で投函
            page.click(f'.arow[data-session="{target_session}"]')
            page.wait_for_selector("#sheet:not([hidden])", timeout=3000)
            page.fill("#composeinput", "操作系スモークのテスト指示です")
            page.keyboard.press("Enter")
            f2 = INBOX / f"{target_session}.json"
            if wait_file(f2) and "操作系スモークのテスト指示です" in f2.read_text(encoding="utf-8"):
                print("  ✓ コンポーズ: 行クリック→入力→Enter → inbox 投函")
            else:
                print(f"  ✗ コンポーズ投函が inbox に届かない: {f2}")
                ng += 1
            if page.eval_on_selector("#sheet", "el => el.hidden"):
                print("  ✓ 送信後にシートが閉じる")
            else:
                print("  ✗ 送信後もシートが開いたまま")
                ng += 1

            # (2b) ×N集約の内訳: 2号セッションへ宛先を切替えて投函 → 非代表の inbox へ届く
            ai_office = next(p for p in world["roster"] if p["disp"] == "ai-office")
            second = ai_office["sessions"][1]["session"]
            page.click(f'.arow[data-session="{ai_office["session"]}"]')
            page.wait_for_selector("#sheet:not([hidden])", timeout=3000)
            page.wait_for_selector(".crewlist .crewrow", timeout=3000)
            page.click(f'.crewrow[data-session="{second}"]')
            tgt = page.eval_on_selector("#sheettarget", "el => el.hidden ? '' : el.textContent")
            page.fill("#composeinput", "2号機への個別指示です")
            page.keyboard.press("Enter")
            f2b = INBOX / f"{second}.json"
            if wait_file(f2b) and "2号機への個別指示です" in f2b.read_text(encoding="utf-8"):
                print(f"  ✓ 内訳の宛先切替 → 非代表セッションへ投函（{tgt}）")
            else:
                print(f"  ✗ 宛先切替の投函が届かない: {f2b}")
                ng += 1

            # (3) 入力中は数字キーが暴発しない（コンポーズを開いて '1' を打つ）
            page.click(f'.arow[data-session="{target_session}"]')
            page.wait_for_selector("#sheet:not([hidden])", timeout=3000)
            before = f1.stat().st_mtime
            page.type("#composeinput", "1")
            page.wait_for_timeout(400)
            if f1.stat().st_mtime == before:
                print("  ✓ 入力中の数字キーはトレイ回答に化けない")
            else:
                print("  ✗ 入力中の数字キーで誤投函")
                ng += 1
            page.keyboard.press("Escape")
            page.wait_for_timeout(200)

            # (3a2) R53 🖥ターミナルジャンプ: シートのボタン → /api/terminal/focus（FAKEマーカー実証明）
            page.click(f'.arow[data-session="{target_session}"]')
            page.wait_for_selector("#sheet:not([hidden])", timeout=3000)
            page.click("#sheetterm")
            if wait_file(focus_marker) and target_session in focus_marker.read_text(encoding="utf-8"):
                print("  ✓ 🖥 ターミナルジャンプ: シートボタン → focus API（マーカー実証明）")
            else:
                print(f"  ✗ 🖥 focus API が呼ばれない: {focus_marker}")
                ng += 1
            page.keyboard.press("Escape")
            page.wait_for_timeout(200)

            # (3b) ❗トレイの複数件トリアージ: (1/2)表示＋Jで次の❗へ巡回（Kで戻る）
            tray_txt = page.eval_on_selector("#attn", "el => el.textContent")
            if "（1/2）" in tray_txt or "(1/2)" in tray_txt:
                print("  ✓ トレイに残数表示（1/2）")
            else:
                print(f"  ✗ トレイの残数表示が無い: {tray_txt[:60]}")
                ng += 1
            page.keyboard.press("j")
            page.wait_for_timeout(200)
            tray2 = page.eval_on_selector("#attn", "el => el.textContent")
            if "xpost製品化" in tray2 and ("（2/2）" in tray2 or "(2/2)" in tray2):
                print("  ✓ J で次の❗（承認まち）へ巡回")
            else:
                print(f"  ✗ J 巡回が効かない: {tray2[:60]}")
                ng += 1
            page.keyboard.press("k")
            page.wait_for_timeout(200)

            # (3c) 3Dロボットのクリック → シートが開く（座標は probe.debug から取る＝暗算しない）。
            #      足元チップが胴に被るとチップ側が先に拾う（それは別経路で検証済み）ので、
            #      ロボット経路そのものを見るためにチップは透過にする
            dump = page.evaluate("window.__office.dumpWorld()")
            blog = next(a for a in dump["agents"] if a["disp"] == "ブログ編集部")
            pt = page.evaluate("(id) => window.__office.debug.agentPoint(id)", blog["id"])
            vp = page.eval_on_selector("#viewport", "el => el.getBoundingClientRect()")
            page.eval_on_selector("#labels", "el => { el.style.display = 'none'; }")
            page.mouse.click(vp["x"] + pt["left"], vp["y"] + pt["top"])
            page.eval_on_selector("#labels", "el => { el.style.display = ''; }")
            page.wait_for_timeout(300)
            sheet_open = page.eval_on_selector("#sheet", "el => !el.hidden")
            sheet_name = page.eval_on_selector("#sheetname", "el => el.textContent")
            if sheet_open and "ブログ編集部" in sheet_name:
                print("  ✓ 3Dロボットのクリックでシートが開く")
            else:
                print(f"  ✗ ロボットクリックが効かない: open={sheet_open} name={sheet_name!r}")
                ng += 1
            page.keyboard.press("Escape")
            page.wait_for_timeout(200)

            # (3d) ↻再送は2クリック制（1回目=アーム表示のみ・2回目で投函）
            hist_session = world["history"][0]["session"]
            fh = INBOX / f"{hist_session}.json"
            page.click(".hresend")
            page.wait_for_timeout(300)
            armed = page.eval_on_selector(".hresend", "el => el.classList.contains('arm')")
            if armed and not fh.exists():
                print("  ✓ 再送1クリック目はアームのみ（投函されない）")
            else:
                print(f"  ✗ 再送1クリック目の挙動が不正: armed={armed} sent={fh.exists()}")
                ng += 1
            page.click(".hresend")
            if wait_file(fh) and world["history"][0]["text"] in fh.read_text(encoding="utf-8"):
                print("  ✓ 再送2クリック目で inbox 投函")
            else:
                print("  ✗ 再送2クリック目が投函されない")
                ng += 1

            # (3e) R55 リッチゲージ: fixture status_board で Codex 2段バー・リセット残・
            #      planチップ・Claude tok行・Gemini接続行が描画される（SwiftShaderは遅い＝長めに待つ）
            page.wait_for_function(
                "document.querySelectorAll('#gcreditbody .gprov').length >= 4", timeout=60000)
            gtxt = page.eval_on_selector("#gcreditbody", "el => el.innerText")
            gbars = page.eval_on_selector_all("#gcreditbody .gbar", "els => els.length")
            # "PRO" は .gplan の text-transform:uppercase 後の innerText
            checks = [("Codex", "Codex"), ("PRO", "PRO"), ("3%", "3%"),
                      ("リセット残", "リセットまであと"), ("Claude tok", "tok"),
                      ("Gemini接続", "Gemini"),
                      # R61: 実測subscriptionが新鮮なら実測チップ+5h/週の2本バー・
                      #      推定ペースは隠れる（fixtureはpaceも持つ＝優先の証明）
                      ("Claude実測チップ", "実測"), ("実測5h枠", "5時間枠"),
                      ("実測週枠", "週間枠"), ("実測5h%", "42%"), ("実測週%", "61%"),
                      ("アカウントチップ", "main-dev"), ("2アカウント行", "sub-dev"),
                      ("別アカ前回確認", "前回確認"),
                      # R63: 上限ありは金額バー・上限なしは「未設定」を明示（嘘の%を作らない）
                      ("APIプロバイダ名", "OpenRouter"), ("上限ありの金額", "$3.42 / $10.00"),
                      ("上限なしの注記", "上限が設定されていません")]
            miss = [name for name, frag in checks if frag not in gtxt]
            if "ペース" in gtxt:
                miss.append("推定ペースが実測と二重表示")
            if not miss and gbars >= 7:
                print(f"  ✓ R55/R61/R63 リッチゲージ（bar {gbars}本・実測5h+週/tok/アカウント別/API上限）")
            else:
                print(f"  ✗ R55 ゲージ描画不足: miss={miss} bars={gbars} txt={gtxt[:120]!r}")
                ng += 1
            # (3e2) R55.1 レイアウト収まり: ゲージが伸びてもサイドバーが画面からはみ出さず、
            #       最下部の管理ボタンが可視のまま（実際に⚡が見切れた回帰＝要素スクショでは
            #       検出できない種類の崩れなので、矩形とscrollHeightで機械検査する）
            fit = page.evaluate(
                "() => { const side = document.querySelector('.side');"
                " const lic = document.querySelector('#btn-license').getBoundingClientRect();"
                " return { over: side.scrollHeight - side.clientHeight,"
                "   licOk: lic.height > 0 && lic.bottom <= window.innerHeight }; }")
            if fit["over"] <= 1 and fit["licOk"]:
                print("  ✓ R55.1 サイドバー収まり（はみ出しゼロ・管理ボタン可視）")
            else:
                print(f"  ✗ サイドバーがはみ出している: {fit}")
                ng += 1

            # (3f) R67 送信失敗で本文が残る（従来: 失敗トーストの裏で入力全喪失の実バグ）
            page.route("**/api/instruct", lambda route: route.fulfill(
                status=500, content_type="application/json; charset=utf-8",
                body='{"ok": false, "error": "synthetic failure"}'))
            page.click(f'.arow[data-session="{target_session}"]')
            page.wait_for_selector("#sheet:not([hidden])", timeout=3000)
            page.fill("#composeinput", "失敗しても消えない本文")
            page.keyboard.press("Enter")
            page.wait_for_timeout(600)
            kept = page.eval_on_selector("#composeinput", "el => el.value")
            sheet_open2 = page.eval_on_selector("#sheet", "el => !el.hidden")
            if kept == "失敗しても消えない本文" and sheet_open2:
                print("  ✓ R67 送信失敗: 本文が残りシートも開いたまま（再送できる）")
            else:
                print(f"  ✗ 失敗で本文が消えた: kept={kept!r} open={sheet_open2}")
                ng += 1
            page.unroute("**/api/instruct")

            # (3g) R67 送信中UI: 飛行中は入力とボタンが塞がる（無反応800msの可視化）
            # 応答を保留したまま検査し、後から fulfill（route内sleepはsyncスレッドを
            # 塞ぎ、検査時点で送信が完了してしまう＝保留方式が正）
            held = []
            page.route("**/api/instruct", lambda route: held.append(route))
            page.fill("#composeinput", "送信中UIの検証")
            page.keyboard.press("Enter")
            page.wait_for_timeout(250)
            busy = page.evaluate(
                "() => ({ dis: document.querySelector('#composeinput').disabled,"
                "  ph: document.querySelector('#composeinput').placeholder })")
            if busy["dis"] and "送信中" in busy["ph"]:
                print("  ✓ R67 送信中: 入力disabled＋「送信中…」表示")
            else:
                print(f"  ✗ 送信中UIが出ない: {busy}")
                ng += 1
            for r in held:
                r.fulfill(status=200, content_type="application/json; charset=utf-8",
                          body='{"ok": true}')
            page.wait_for_timeout(400)
            page.unroute("**/api/instruct")
            page.keyboard.press("Escape")
            page.wait_for_timeout(200)

            real = [e for e in errors if "Failed to load resource" not in e]
            if real:
                print(f"  ✗ JSエラー: {real[:3]}")
                ng += 1
            else:
                print("  ✓ console error 0")

            # (3h) R67 差分更新: live mode で3秒ポーリングをまたいでも .arow が
            #      同一ノードのまま（従来: 毎回 replaceChildren でクリックが detach 空振り）
            page3 = browser.new_page(viewport=VIEWPORT, device_scale_factor=1)
            page3.route("**/api/office*", lambda route: route.fulfill(
                status=200, content_type="application/json; charset=utf-8", body=payload))
            page3.goto(f"http://127.0.0.1:{port}/?ui=iso&seed=11")
            page3.wait_for_function("window.__office && window.__office.ready", timeout=60000)
            page3.wait_for_selector(".arow", timeout=10000)
            handle = page3.query_selector(f'.arow[data-session="{target_session}"]')
            page3.wait_for_timeout(3600)          # ポーリング1周以上またぐ
            alive = handle.evaluate("el => el.isConnected") if handle else False
            if alive:
                print("  ✓ R67 差分更新: ポーリング後も .arow が同一ノード（detach空振りの根絶）")
            else:
                print("  ✗ .arow がポーリングで作り直されている")
                ng += 1
            page3.close()

            # (4) オフライン表示: /api/office が2回連続で落ちたら .ui-iso.offline＋バナー
            #     （クラス付与先とCSSセレクタの食い違いでサイレント故障していた回帰ピン。
            #      ?t=固定はポーリング自体を止めるので、このページだけ非frozenで開く）
            page2 = browser.new_page(viewport=VIEWPORT, device_scale_factor=1)
            calls = {"n": 0}
            # あわせて 403ノイズ根絶ピン: costDash=false の world では
            # /api/status_board への fetch 自体が発生しないこと（R42.2の教訓の新UI適用）
            world2 = dict(world)
            world2["edition"] = {"id": "claude",
                                 "features": {**world["edition"]["features"], "costDash": False}}
            payload2 = json.dumps(world2, ensure_ascii=False)
            sb_calls = []
            page2.on("request", lambda r: sb_calls.append(r.url)
                     if "/api/status_board" in r.url else None)

            def office_route(route):
                calls["n"] += 1
                if calls["n"] <= 1:
                    route.fulfill(status=200,
                                  content_type="application/json; charset=utf-8", body=payload2)
                else:
                    route.abort()
            page2.route("**/api/office*", office_route)
            page2.goto(f"http://127.0.0.1:{port}/?ui=iso&seed=11")
            page2.wait_for_function("window.__office && window.__office.ready", timeout=30000)
            try:
                # SwiftShader は rAF が重くポーリング周期が実時間で数倍に伸びる（実測: 単体で
                # 2回失敗到達まで30秒強・フルverifyの並行負荷ではさらに伸びる）ので大きく待つ
                page2.wait_for_function(
                    "document.querySelector('#app').classList.contains('offline')",
                    timeout=150000)
                offbar_ok = page2.eval_on_selector(
                    "#offbar", "el => !el.hidden && el.textContent.includes('最終更新')")
                if offbar_ok:
                    print("  ✓ オフライン: .offline クラス＋鮮度バナー表示")
                else:
                    print("  ✗ オフラインバナーが出ない/文言不正")
                    ng += 1
            except Exception:
                print("  ✗ オフライン検知（.offline クラス）が発火しない")
                ng += 1
            if not sb_calls:
                print("  ✓ Pro未解錠(costDash=false)では status_board を fetch しない（403ノイズ0）")
            else:
                print(f"  ✗ 未解錠なのに status_board へ fetch: {len(sb_calls)}回")
                ng += 1
            page2.close()
            browser.close()
    finally:
        proc.terminate()
        shutil.rmtree(INBOX, ignore_errors=True)
    if ng:
        print(f"操作系スモーク: {ng} 件失敗")
    else:
        print("✓ 操作系スモーク合格（❗回答/コンポーズ投函/誤爆ガード/console 0）")
    return 1 if ng else 0


if __name__ == "__main__":
    sys.exit(main())
