#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""R98: 様式 pixel（フロア帯 × 台帳）のスモーク。1 ブラウザで台帳の契約を確かめる。

「行が描けた」ではなく、❗の行が最上段に来て **数字キー 1 で office_inbox に実ファイルが書かれる**
ところまで（配達経路の入口が iso と同じ本物であることの機械証明）。

検査（W1 時点）:
  1. 行数 = fixture のプロジェクト数・横スクロール無し
  2. ❗の行が index 0（class attn）・#attn トレイが見える・「1」→ inbox 投函
  3. 行クリック → #sheet が同じセッションで開く・シートがヘッダーと❗帯を覆わない
  4. __office.debug.agentPoint(id) の座標クリック → その行が .sel
  5. 形状 3 つ（1440×900 / 1280×800 / 1024×768）で scrollWidth ≤ innerWidth かつ行数不変
  5b. 1024px・⤢ でもシートは表の上に重なり、畳んだ帯（#stage）は現れない（3D 用 sheet-open 則を当てない）
  6. ? でヘルプが開く
  7. 留守中ダイジェストのカードは台帳の面（.pxbody）に置かれる（live ページ・構造のみ）
  8. 管理 7 ボタンはアイコン（≤32px）・title に文言
  9. 証拠列: committed / tested / failed（fixture）・記録の無い行は — ＋理由の title
  10. リプレイが台帳で進み、行が**過去の状態**になる（live の値で上書きしない）・終わると live に戻る
  11. console.error / pageerror = 0
W2 で足す: 帯の canvas（fillText を throw に差し替えて mount が通る）・行↔帯ホバー・data-pose・無変化 poll の DOM 書き込み上限。

使い方: python3 tests/pixel_smoke.py   （verify.sh ▶7 から呼ぶ・Playwright必要）
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
SHAPES = [(1440, 900), (1280, 800), (1024, 768)]


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
    projects = len({p.get("projectId") or p["session"] for p in world["roster"]})
    attn_session = roster["議事録アプリ"]        # 質問持ち（トレイの最優先）
    target = next(p for p in world["roster"] if p["disp"] == "制作本部(works)")
    target_id = target.get("projectId") or target["session"]

    shutil.rmtree(INBOX, ignore_errors=True)
    port = free_port()
    proc = start_server(port)
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
            page.route("**/api/session/dialog*", lambda route: route.fulfill(
                status=200, content_type="application/json; charset=utf-8",
                body='{"ok":true,"messages":[],"depth":0,"maxDepth":0,"hasMore":false,"total":0}'))
            page.route("**/api/digest*", lambda route: route.fulfill(
                status=200, content_type="application/json",
                body='{"available":false,"since":0,"totals":{"tasksDone":0}}'))
            page.goto(f"http://127.0.0.1:{port}/?ui=pixel&t=3.2&seed=11")
            page.wait_for_function("window.__office && window.__office.ready", timeout=30000)
            page.wait_for_timeout(300)
            if page.evaluate("() => window.__office.style") != "pixel":
                print("  ✗ probe.style が pixel でない")
                ng += 1

            # (1) 行数と横スクロール
            shape = page.evaluate(
                "() => ({ rows: document.querySelectorAll('#agents .pxrow.proj').length,"
                "  sw: document.documentElement.scrollWidth, iw: window.innerWidth,"
                "  bodySw: document.body.scrollWidth })")
            if shape["rows"] == projects and shape["sw"] <= shape["iw"] and shape["bodySw"] <= shape["iw"]:
                print(f"  ✓ 台帳: プロジェクト行 {shape['rows']} 本・横スクロール無し")
            else:
                print(f"  ✗ 台帳の形が違う: {shape}（期待 rows={projects}）")
                ng += 1

            # (2) ❗の行が最上段・トレイ・数字キー 1 → inbox
            top = page.evaluate(
                "(s) => { const r = document.querySelector('#agents .pxrow');"
                "  return { attn: r?.classList.contains('attn'), session: r?.dataset.session,"
                "    tray: !document.querySelector('#attn').hidden }; }", attn_session)
            if top["attn"] and top["session"] == attn_session and top["tray"]:
                print("  ✓ ❗の行が最上段・❗トレイが見える")
            else:
                print(f"  ✗ ❗の行が最上段でない/トレイが無い: {top}")
                ng += 1
            page.keyboard.press("1")
            f1 = INBOX / f"{attn_session}.json"
            if wait_file(f1) and "選択肢「案A で進める」でお願いします。" in f1.read_text(encoding="utf-8"):
                print("  ✓ ❗回答: 数字キー1 → inbox 投函（iso と同じ経路）")
            else:
                print(f"  ✗ ❗回答が inbox に届かない: {f1}")
                ng += 1

            # (3) 行クリック → シート（同じセッション・ヘッダーと❗帯を覆わない）
            page.click(f'#agents .pxrow.proj[data-project="{target_id}"]')
            page.wait_for_selector("#sheet:not([hidden])", timeout=3000)
            page.wait_for_timeout(200)
            sheet = page.evaluate(
                "(id) => { const s = document.querySelector('#sheet').getBoundingClientRect();"
                "  const h = document.querySelector('.pxhead').getBoundingClientRect();"
                "  const t = document.querySelector('#attn').getBoundingClientRect();"
                "  const row = document.querySelector(`#agents .pxrow.proj[data-project=\"${id}\"]`);"
                "  return { sel: row?.classList.contains('sel'), name: document.querySelector('#sheetname').textContent,"
                "    overHead: s.top < h.bottom, overTray: s.top < t.bottom }; }", target_id)
            if sheet["sel"] and sheet["name"] and not sheet["overHead"] and not sheet["overTray"]:
                print(f"  ✓ 行クリック → シート「{sheet['name']}」・行が選択色・ヘッダーと❗帯を覆わない")
            else:
                print(f"  ✗ シートの開き方が違う: {sheet}")
                ng += 1
            page.keyboard.press("Escape")
            page.wait_for_timeout(150)

            # (4) 照準 API → クリック → .sel
            pt = page.evaluate("(id) => window.__office.debug.agentPoint(id)", attn_session and target_id)
            if pt:
                page.mouse.click(pt["left"], pt["top"])
                page.wait_for_selector("#sheet:not([hidden])", timeout=3000)
                if page.evaluate("(id) => document.querySelector(`#agents .pxrow.proj[data-project=\"${id}\"]`)"
                                 ".classList.contains('sel')", target_id):
                    print("  ✓ debug.agentPoint の座標クリックで同じ行が開く")
                else:
                    print("  ✗ agentPoint の座標が行に当たらない")
                    ng += 1
                page.keyboard.press("Escape")
            else:
                print("  ✗ debug.agentPoint が null")
                ng += 1

            # (5) 形状
            for w, h in SHAPES:
                page.set_viewport_size({"width": w, "height": h})
                page.wait_for_timeout(150)
                m = page.evaluate(
                    "() => { const vis = (n) => [...n.children].filter(c => getComputedStyle(c).display !== 'none').length;"
                    "  const rows = [...document.querySelectorAll('#agents .pxrow')];"
                    "  return { rows: document.querySelectorAll('#agents .pxrow.proj').length,"
                    "    sw: document.documentElement.scrollWidth, iw: window.innerWidth,"
                    "    head: vis(document.querySelector('.pxhrow')),"
                    "    cells: [...new Set(rows.map(vis))],"
                    "    tall: rows.filter(r => r.getBoundingClientRect().height > 45).length }; }")
                # 見えるセル数が見出しと同じ＝列が次の段へ流れていない（別モデルレビュー: 詳細度で非表示が負けていた）
                if (m["rows"] == projects and m["sw"] <= m["iw"] and m["cells"] == [m["head"]]
                        and m["tall"] == 0):
                    print(f"  ✓ {w}×{h}: 行 {m['rows']}・列 {m['head']}・横スクロール無し")
                else:
                    print(f"  ✗ {w}×{h}: {m}")
                    ng += 1
            page.set_viewport_size({"width": VIEWPORT["width"], "height": VIEWPORT["height"]})

            # (5b) 別モデルレビュー: hud.css の 3D 用 sheet-open 則（≤1024 でシートを relative に縦積み・
            #      wide で #stage を grid に）が台帳に当たると、畳んだ帯が空で現れシートが表の下に落ちる。
            page.set_viewport_size({"width": 1024, "height": 768})
            page.click(f'#agents .pxrow.proj[data-project="{target_id}"]')
            page.wait_for_selector("#sheet:not([hidden])", timeout=3000)
            page.click("#sheetwide")
            page.wait_for_timeout(200)
            lay = page.evaluate(
                "() => { const s = document.querySelector('#sheet'); const st = document.querySelector('#stage');"
                "  const tbl = document.querySelector('.pxtable').getBoundingClientRect();"
                "  const r = s.getBoundingClientRect();"
                "  return { pos: getComputedStyle(s).position, stage: getComputedStyle(st).display,"
                "    wide: s.classList.contains('wide'), overTable: r.top < tbl.bottom && r.bottom > tbl.top,"
                "    sw: document.documentElement.scrollWidth, iw: window.innerWidth }; }")
            if (lay["pos"] == "absolute" and lay["stage"] == "none" and lay["wide"]
                    and lay["overTable"] and lay["sw"] <= lay["iw"]):
                print("  ✓ 1024px・⤢ でもシートは表の上に重なり、畳んだ帯は出ない")
            else:
                print(f"  ✗ 3D 用の sheet-open 則が台帳に当たっている: {lay}")
                ng += 1
            page.click("#sheetwide")
            page.keyboard.press("Escape")
            page.set_viewport_size({"width": VIEWPORT["width"], "height": VIEWPORT["height"]})

            # (6) ? → ヘルプ
            page.keyboard.press("?")
            page.wait_for_timeout(150)
            if page.evaluate("() => !document.querySelector('#modalwrap').hidden"
                             " && document.querySelector('#modal').dataset.kind === 'help'"):
                print("  ✓ ? でヘルプが開く")
            else:
                print("  ✗ ? でヘルプが開かない")
                ng += 1
            page.keyboard.press("Escape")

            # (7) 留守中ダイジェストの置き場（frozen では作られないので live ページで構造だけ見る）:
            #     pixel は #stage を畳んでいる＝そこに出すと inertOthers で台帳ごと操作不能（別モデルレビュー high）。
            live = browser.new_page(viewport=VIEWPORT, device_scale_factor=1)
            live.on("pageerror", lambda e: errors.append(f"pageerror(live): {e}"))
            live.route("**/api/office*", lambda route: route.fulfill(
                status=200, content_type="application/json; charset=utf-8", body=payload))
            live.route("**/api/status_board*", lambda route: route.fulfill(
                status=200, content_type="application/json; charset=utf-8", body=sb_payload))
            live.route("**/api/digest*", lambda route: route.fulfill(
                status=200, content_type="application/json",
                body='{"available":false,"since":0,"totals":{"tasksDone":0}}'))
            live.goto(f"http://127.0.0.1:{port}/?ui=pixel")
            live.wait_for_function("window.__office && window.__office.ready", timeout=60000)
            host = live.evaluate("() => document.querySelector('#digest-card')?.parentElement?.className || null")
            if host and "pxbody" in host:
                print("  ✓ ダイジェストのカードは台帳の面（.pxbody）に置かれる")
            else:
                print(f"  ✗ ダイジェストのカードの置き場が違う: {host}")
                ng += 1
            # (8) 管理 7 ボタンはアイコン（28px）＝幅を枠ゲージに返す。文言は title に残る（読み上げ・ホバー）
            icons = live.evaluate(
                "() => [...document.querySelectorAll('.admin .abtn:not([hidden])')].map(b => "
                "  ({ w: Math.round(b.getBoundingClientRect().width), title: b.title, glyph: b.dataset.glyph }))")
            if icons and all(i["w"] <= 32 and i["title"] and i["glyph"] for i in icons):
                print(f"  ✓ 管理ボタン {len(icons)} 個はアイコン（≤32px）・title に文言")
            else:
                print(f"  ✗ 管理ボタンの形が違う: {icons}")
                ng += 1
            # (9) 証拠列: fixture の committed / tested / failed が行に出る（Codex/OpenClaw は「—」＋hook 無しの title）
            evd = live.evaluate(
                "() => [...document.querySelectorAll('#agents .pxrow')].map(r => ({"
                "  p: r.querySelector('.pname').textContent, t: r.querySelector('.evd').textContent,"
                "  c: r.querySelector('.evd').className, title: r.querySelector('.evd').title }))")
            by = {e["p"]: e for e in evd}
            ok_evd = (
                by.get("制作本部(works)", {}).get("c") == "evd evd-committed" and "commit" in by["制作本部(works)"]["t"]
                and by.get("xpost製品化", {}).get("c") == "evd evd-failed"
                and by.get("ai-office", {}).get("c") == "evd evd-tested"
                and by.get("OpenClaw", {}).get("t") == "—" and "hook" in by.get("OpenClaw", {}).get("title", "")
                and by.get("広報チーム", {}).get("t") == "—" and "24" in by.get("広報チーム", {}).get("title", ""))
            if ok_evd:
                print("  ✓ 証拠列: committed / tested / failed が出て、記録の無い行は — ＋理由の title")
            else:
                print(f"  ✗ 証拠列が違う: {evd}")
                ng += 1
            live.close()

            # (10) リプレイ（留守中のまとめ → 過去の再生）が**台帳で進む**（別モデルレビュー: W1 は接続していなかった）
            since = int(time.time()) - 1800
            attn_s, tgt_s = attn_session, target["session"]
            rp = browser.new_page(viewport=VIEWPORT, device_scale_factor=1)
            rp.on("pageerror", lambda e: errors.append(f"pageerror(replay): {e}"))
            rp.route("**/api/office*", lambda route: route.fulfill(
                status=200, content_type="application/json; charset=utf-8", body=payload))
            rp.route("**/api/status_board*", lambda route: route.fulfill(
                status=200, content_type="application/json; charset=utf-8", body=sb_payload))
            rp.route("**/api/digest*", lambda route: route.fulfill(
                status=200, content_type="application/json",
                body=json.dumps({"available": True, "since": since, "totals": {"tasksDone": 2}})))
            rp.route("**/api/timeline*", lambda route: route.fulfill(
                status=200, content_type="application/json",
                body=json.dumps({"events": [
                    # 30 分 → 20 秒に畳まれる。最初の 2 件は再生開始から 1 秒以内に通過する位置へ置く
                    {"id": 1, "ts": since + 30, "sid": attn_s, "ev": "Stop", "tool": "", "kind": "", "ok": 1},
                    {"id": 2, "ts": since + 60, "sid": tgt_s, "ev": "PreToolUse", "tool": "Bash", "kind": "", "ok": 1},
                    {"id": 3, "ts": since + 1500, "sid": attn_s, "ev": "PermissionRequest", "tool": "Bash", "kind": "", "ok": 1},
                ]})))
            rp.route("**/api/seen*", lambda route: route.fulfill(status=200, content_type="application/json", body="{}"))
            rp.goto(f"http://127.0.0.1:{port}/?ui=pixel")
            rp.bring_to_front()
            rp.wait_for_function("window.__office && window.__office.ready", timeout=60000)
            rp.wait_for_function("() => !document.querySelector('#digest-strip')?.hidden"
                                 " || !document.querySelector('#digest-card')?.hidden", timeout=15000)
            if rp.evaluate("() => document.querySelector('#digest-card').hidden"):
                rp.click("#digest-strip")
            rp.wait_for_selector("#digest-card:not([hidden])", timeout=5000)
            rows_live = rp.evaluate("() => document.querySelectorAll('#agents .pxrow').length")
            rp.click("#digest-details")
            rp.wait_for_function("() => document.querySelector('.pxshell').classList.contains('replay-active')", timeout=8000)
            p1 = rp.evaluate("() => document.querySelector('#replay-progress').textContent")
            # 進捗は秒単位の表示＝1 秒未満では変わらない（900ms で見て「進まない」と誤判定した）
            rp.wait_for_function("(before) => document.querySelector('#replay-progress').textContent !== before",
                                 arg=p1, timeout=8000)
            p2 = rp.evaluate("() => document.querySelector('#replay-progress').textContent")
            probe_state = ("(s) => { const r = document.querySelector(`#agents .pxrow[data-session=\"${s[0]}\"]`);"
                           "  const a = document.querySelector(`#agents .pxrow[data-session=\"${s[1]}\"]`);"
                           "  return { inert: document.querySelector('#agents').closest('[inert]') !== null,"
                           "    st: r?.dataset.state, attn: a?.classList.contains('attn'),"
                           "    rows: document.querySelectorAll('#agents .pxrow').length }; }")
            state_mid = rp.evaluate(probe_state, [tgt_s, attn_s])
            rp.click("#replay-controls .digest-close")
            rp.wait_for_function("() => !document.querySelector('.pxshell').classList.contains('replay-active')", timeout=5000)
            state_live = rp.evaluate(probe_state, [tgt_s, attn_s])
            # 再生中の行は**過去の世界**から作る（live の roster で state が戻らない・別モデルレビュー）。
            # 制作本部(works) は live では休憩・再生では Bash 実行で作業。❗は Stop で消える。
            if (p1 != p2 and state_mid["inert"] and state_mid["st"] == "working"
                    and state_mid["attn"] is False and state_live["st"] == "resting"
                    and state_live["attn"] is True and state_live["rows"] == rows_live):
                print(f"  ✓ リプレイ: 時間が進み（{p1}→{p2}）・行は過去の状態・操作不能・終わると live に戻る")
            else:
                print(f"  ✗ リプレイが台帳に反映されない: p1={p1} p2={p2} mid={state_mid} live={state_live} rows={rows_live}")
                ng += 1
            rp.close()

            browser.close()
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except Exception:
            proc.kill()
    if errors:
        print("  ✗ ブラウザのエラー:")
        for e in errors[:8]:
            print("     " + e[:200])
        ng += 1
    if ng:
        print(f"✗ pixel スモーク: {ng} 件")
        return 1
    print("✓ pixel スモーク: 台帳・❗→inbox・シート・照準・形状 3 つ・ヘルプ・エラー 0")
    return 0


if __name__ == "__main__":
    sys.exit(main())
