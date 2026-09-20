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
  4b. ホバー: プロジェクト行は全員・セッション行はその 1 体だけ帯が光る
  5. 形状 6 つ（1440/1280/1024/834×1112/890×626/390×844）で横スクロール無し・行数不変・
     帯は切れずに全部入るか、スマホでは畳まれている
  5b. 1024px・⤢ でもシートは表の上だけに重なり、帯は覆われない（3D 用 sheet-open 則を当てない）
  5b'. 390px・⤢ でも帯は畳まれたまま
  5c. 帯は論理幅×72 の整数倍・pixelated・実際に人が居る
  5d. canvas に文字を描かない（fillText を throw に差し替えても mount が通る）
  5e. 帯の移動は歩く（瞬間移動しない・奥列へは縦も動く）・窓幅の変化は移動に化けない（live ページ）
  6. ? でヘルプが開く
  6b. 固定時刻（?t=）では補間しない＝注入した配置がその場で描かれる
  7. 留守中ダイジェストのカードは台帳の面（.pxbody）に置かれる（live ページ・構造のみ）
  8. 管理 7 ボタンはアイコン（≤32px）・title に文言
  9. 証拠列: committed / tested / failed（fixture）・記録の無い行は — ＋理由の title
  10. リプレイが台帳で進み、行が**過去の状態**になる（live の値で上書きしない）・止めると帯も止まる・終わると live に戻る
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
# 形状 6 つ。後ろ 3 つはプラン W3 の対象（iPad 縦・折りたたみ展開・スマホ）。
# スマホは帯を畳む（部屋の最小幅 400 が入らず右端が切れる＝休憩中の人が「居ない」ように見える）。
SHAPES = [(1440, 900), (1280, 800), (1024, 768), (834, 1112), (890, 626), (390, 844)]
BAND_HIDDEN_BELOW = 480


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
    multi = next(p for p in world["roster"] if len(p.get("sessions") or []) > 1)
    multi_id = multi.get("projectId") or multi["session"]      # 内訳を持つプロジェクト（ホバーの検査用）

    def world_with(**fields):
        """target のプロジェクトだけ状態を差し替えた office_json を作る。

        行は roster → sessions[] → employees の順に重ねて組まれる（ui/hud/board.js）。
        employees が最後に勝つので、**3 箇所とも**書かないと注入が効かない（実測で踏んだ）。
        """
        doc = json.loads(payload)
        sids = set()
        for entry in doc["roster"]:
            if entry.get("projectId") != target_id and entry.get("session") != target_id:
                continue
            entry.update(fields)
            sids.add(entry.get("session"))
            for sub in entry.get("sessions", []):
                sub.update(fields)
                sids.add(sub.get("session"))
        for emp in doc.get("employees", []):
            if emp.get("session") in sids:
                emp.update(fields)
        return doc


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
                    "    tall: rows.filter(r => r.getBoundingClientRect().height > 45).length,"
                    "    band: (() => { const st = document.querySelector('#stage');"
                    "      return st && getComputedStyle(st).display !== 'none'"
                    "        ? Math.round(st.getBoundingClientRect().width) : 0; })(),"
                    "    canvas: (() => { const c = document.querySelector('#stage canvas');"
                    "      return c && c.offsetParent ? c.width : 0; })(),"
                    "    room: (window.__office.stats() || {}).logicalW,"
                    "    scale: (window.__office.stats() || {}).scale }; }")
                # 帯は「畳む」か「切れずに全部入る」のどちらか。中途半端に切れると、右端の
                # ラウンジとサーバーが見えず**休憩中の人が居ないように見える**＝帯が嘘をつく。
                band_ok = (m["band"] == 0) if w <= BAND_HIDDEN_BELOW else (
                    m["band"] > 0 and m["canvas"] <= m["band"] and m["room"] >= 400)
                # 見えるセル数が見出しと同じ＝列が次の段へ流れていない（別モデルレビュー: 詳細度で非表示が負けていた）
                if (m["rows"] == projects and m["sw"] <= m["iw"] and m["cells"] == [m["head"]]
                        and m["tall"] == 0 and band_ok):
                    band = "帯なし" if m["band"] == 0 else f"帯 論理{m['room']}×{m['scale']}"
                    print(f"  ✓ {w}×{h}: 行 {m['rows']}・列 {m['head']}・{band}・横スクロール無し")
                else:
                    print(f"  ✗ {w}×{h}: {m}")
                    ng += 1
            page.set_viewport_size({"width": VIEWPORT["width"], "height": VIEWPORT["height"]})

            # (5b) hud.css の 3D 用 sheet-open 則（≤1024 でシートを relative に縦積み・wide で #stage を
            #      grid に）が台帳に当たると、帯が潰れてシートが表の下に落ちる。
            page.set_viewport_size({"width": 1024, "height": 768})
            page.click(f'#agents .pxrow.proj[data-project="{target_id}"]')
            page.wait_for_selector("#sheet:not([hidden])", timeout=3000)
            page.click("#sheetwide")
            page.wait_for_timeout(200)
            lay = page.evaluate(
                "() => { const s = document.querySelector('#sheet'); const st = document.querySelector('#stage');"
                "  const tbl = document.querySelector('.pxtable').getBoundingClientRect();"
                "  const band = st.getBoundingClientRect(); const r = s.getBoundingClientRect();"
                "  return { pos: getComputedStyle(s).position, stage: getComputedStyle(st).display,"
                "    wide: s.classList.contains('wide'), overTable: r.top < tbl.bottom && r.bottom > tbl.top,"
                "    overBand: r.top < band.bottom, bandH: Math.round(band.height),"
                "    sw: document.documentElement.scrollWidth, iw: window.innerWidth }; }")
            if (lay["pos"] == "absolute" and lay["stage"] == "block" and lay["wide"]
                    and lay["overTable"] and not lay["overBand"] and lay["bandH"] > 0
                    and lay["sw"] <= lay["iw"]):
                print(f"  ✓ 1024px・⤢ でもシートは表の上だけに重なり、帯（{lay['bandH']}px）は覆われない")
            else:
                print(f"  ✗ 3D 用の sheet-open 則が台帳に当たっている: {lay}")
                ng += 1
            page.click("#sheetwide")
            page.keyboard.press("Escape")
            # (5b') スマホ幅では ⤢ でも帯は戻らない。`:has(.sheet.wide)` の則は詳細度が高いので、
            #       同じ形で打ち消さないと 390px に 400 論理の部屋が出て右端が切れる（別モデルレビュー）。
            page.set_viewport_size({"width": 390, "height": 844})
            page.wait_for_timeout(200)
            page.click(f'#agents .pxrow.proj[data-project="{target_id}"]')
            page.wait_for_selector("#sheet:not([hidden])", timeout=3000)
            page.click("#sheetwide")
            page.wait_for_timeout(200)
            phone = page.evaluate(
                "() => ({ band: getComputedStyle(document.querySelector('#stage')).display,"
                "  wide: document.querySelector('#sheet').classList.contains('wide'),"
                "  sw: document.documentElement.scrollWidth, iw: window.innerWidth })")
            if phone["band"] == "none" and phone["wide"] and phone["sw"] <= phone["iw"]:
                print("  ✓ 390px・⤢ でも帯は畳まれたまま（右端が切れた部屋を出さない）")
            else:
                print(f"  ✗ スマホ幅で帯が戻っている: {phone}")
                ng += 1
            page.click("#sheetwide")
            page.keyboard.press("Escape")
            page.set_viewport_size({"width": VIEWPORT["width"], "height": VIEWPORT["height"]})

            # (5c) 帯: 論理 72 の整数倍で、canvas に**文字を描いていない**（掟の機械証明）
            band = page.evaluate(
                "() => { const c = document.querySelector('#stage canvas');"
                "  const st = window.__office.stats() || {};"
                "  return { w: c.width, h: c.height, scale: st.scale, logicalW: st.logicalW,"
                "    actors: st.actors, draw: st.drawCalls, css: getComputedStyle(c).imageRendering }; }")
            if (band["h"] == 72 * band["scale"] and band["w"] == band["logicalW"] * band["scale"]
                    and band["scale"] in (1, 2, 3) and band["actors"] > 0
                    and band["css"] == "pixelated"):
                print(f"  ✓ 帯: 論理 {band['logicalW']}×72 の ×{band['scale']}・{band['actors']} 体・pixelated")
            else:
                print(f"  ✗ 帯の寸法か倍率が違う: {band}")
                ng += 1

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

            # (6b) 固定時刻（`?t=`）では補間しない＝注入した配置が**その場で**描かれる。
            #      時計が進まない以上、補間すると旧位置で歩行姿勢のまま永久に止まる
            #      （別モデルレビュー medium・golden と E2E がそこを踏む）。
            frozen_from = page.evaluate("(id) => window.__office.debug.bandPoint(id)", target_id)
            page.evaluate("(w) => window.__office.inject(w)",
                          world_with(question="これで進めていいですか？", approvalMin=4,
                                     attention=True, state="waiting"))
            page.wait_for_timeout(250)
            frozen_to = page.evaluate(
                "(id) => ({ p: window.__office.debug.bandPoint(id),"
                "  walking: (window.__office.stats() || {}).walking })", target_id)
            if (frozen_from and frozen_to["p"] and frozen_to["walking"] == 0
                    and frozen_from["left"] - frozen_to["p"]["left"] > 100):
                print(f"  ✓ 固定時刻では注入した配置がその場で描かれる"
                      f"（{frozen_from['left']:.0f} → {frozen_to['p']['left']:.0f}px・歩行 0）")
            else:
                print(f"  ✗ 固定時刻で帯が止まったまま: {frozen_from} → {frozen_to}")
                ng += 1
            page.evaluate("(w) => window.__office.inject(w)", world)

            # (5d) canvas に**文字を描かない**掟の機械証明: fillText / strokeText を throw に
            #      差し替えたページでも mount が通り、帯が描けること。
            text_page = browser.new_page(viewport=VIEWPORT, device_scale_factor=1)
            text_errs = []
            text_page.on("pageerror", lambda e: text_errs.append(str(e)))
            text_page.add_init_script(
                "for (const m of ['fillText', 'strokeText']) {"
                "  CanvasRenderingContext2D.prototype[m] = function () {"
                "    throw new Error('帯の canvas に文字を描いてはいけない: ' + m); }; }")
            text_page.route("**/api/office*", lambda route: route.fulfill(
                status=200, content_type="application/json; charset=utf-8", body=payload))
            text_page.route("**/api/status_board*", lambda route: route.fulfill(
                status=200, content_type="application/json; charset=utf-8", body=sb_payload))
            text_page.goto(f"http://127.0.0.1:{port}/?ui=pixel&t=3.2&seed=11")
            text_page.wait_for_function("window.__office && window.__office.ready", timeout=30000)
            text_page.wait_for_timeout(400)
            drew = text_page.evaluate("() => (window.__office.stats() || {}).actors || 0")
            if drew > 0 and not text_errs:
                print(f"  ✓ canvas に文字を描いていない（fillText を throw にしても {drew} 体が描けた）")
            else:
                print(f"  ✗ canvas に文字を描いている: {text_errs[:2]} actors={drew}")
                ng += 1
            text_page.close()

            # (7) 留守中ダイジェストの置き場（frozen では作られないので live ページで構造だけ見る）:
            #     pixel は #stage を畳んでいる＝そこに出すと inertOthers で台帳ごと操作不能（別モデルレビュー high）。
            live = browser.new_page(viewport=VIEWPORT, device_scale_factor=1)
            live.on("pageerror", lambda e: errors.append(f"pageerror(live): {e}"))
            # 本文は差し替え可能にする（(5e) で世界を動かしたあと、ポーリングが元へ巻き戻さないように）
            live_body = [payload]
            live.route("**/api/office*", lambda route: route.fulfill(
                status=200, content_type="application/json; charset=utf-8", body=live_body[0]))
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
            # (4b) 行 ↔ 帯のホバー: プロジェクト行は全員・**セッション行はその 1 体だけ**光る
            #      （board は内訳の全員に同じ project id を振るので、id で引くと行と対応しない）。
            #      固定時刻のページは loop が 1 回しか描かない（clock.loop）ので live で見る。
            live.click(f'#agents .pxrow.proj[data-project="{multi_id}"]')       # 選択で内訳が開く
            live.wait_for_selector(f'#agents .pxrow.sub[data-project="{multi_id}"]', timeout=3000)
            live.hover(f'#agents .pxrow.proj[data-project="{multi_id}"]')
            live.wait_for_timeout(200)
            hov_proj = live.evaluate("() => (window.__office.stats() || {}).hovered")
            live.hover(f'#agents .pxrow.sub[data-project="{multi_id}"]')
            live.wait_for_timeout(200)
            hov_sess = live.evaluate("() => (window.__office.stats() || {}).hovered")
            if hov_proj > 1 and hov_sess == 1:
                print(f"  ✓ ホバー: プロジェクト行は {hov_proj} 体・セッション行は 1 体だけ光る")
            else:
                print(f"  ✗ ホバーが行と対応していない: プロジェクト {hov_proj} / セッション {hov_sess}")
                ng += 1
            live.mouse.move(2, 2)
            live.keyboard.press("Escape")       # 選択を戻す（内訳は畳む）
            live.wait_for_timeout(150)
            # 既定の avatarMode=session では、同じフォルダの 2 セッションは**別々の id** を持ち
            # departmentBoard が cwd でまとめる。代表の id で引くと本人しか光らない（同レビュー）。
            shared = json.loads(payload)
            cwd = next(p["cwd"] for p in shared["roster"] if p["disp"] == "制作本部(works)")
            for entry in shared["roster"]:
                if entry["disp"] == "ブログ編集部":
                    entry["cwd"] = cwd                      # 同じフォルダの 2 本目にする
            live_body[0] = json.dumps(shared, ensure_ascii=False)
            live.evaluate("(w) => window.__office.inject(w)", shared)
            live.wait_for_timeout(250)
            live.hover(f'#agents .pxrow.proj[data-group="{cwd}"]')   # 行はグループ（cwd）で引く
            live.wait_for_timeout(200)
            hov_group = live.evaluate("() => (window.__office.stats() || {}).hovered")
            if hov_group == 2:
                print("  ✓ ホバー: 同じフォルダの別セッションも一緒に光る（id でなくグループで引く）")
            else:
                print(f"  ✗ グループのホバーが代表だけ: hovered={hov_group}")
                ng += 1
            live.mouse.move(2, 2)
            live_body[0] = payload
            live.evaluate("(w) => window.__office.inject(w)", world)
            live.wait_for_timeout(250)

            # (5e) 帯の移動: 目的地へ**瞬間移動しない**（別モデルレビュー medium・2026-09-20）。
            #      指摘まで、配置が変わった瞬間に着いてしまい歩行コマは 1 フレームだけ出ていた
            #      ＝同じ world・同じ t の連続描画で絵が変わる（決定論も崩れる）。
            #      ラウンジに居る「制作本部(works)」に質問を持たせて受付へ呼ぶ＝帯を横断させる。
            start = live.evaluate("(id) => window.__office.debug.bandPoint(id)", target_id)
            moved = world_with(question="これで進めていいですか？", approvalMin=4,
                               attention=True, state="waiting")   # fixture は attention を明示 False で固定
            live_body[0] = json.dumps(moved, ensure_ascii=False)
            live.evaluate("(w) => window.__office.inject(w)", moved)
            live.wait_for_timeout(400)
            mid = live.evaluate(
                "(id) => ({ p: window.__office.debug.bandPoint(id),"
                "  walking: (window.__office.stats() || {}).walking })", target_id)
            live.wait_for_function(
                "() => (window.__office.stats() || {}).walking === 0", timeout=15000)
            end_pt = live.evaluate("(id) => window.__office.debug.bandPoint(id)", target_id)
            if start and mid["p"] and end_pt:
                travel = start["left"] - end_pt["left"]          # ラウンジ（右）→ 受付（左）
                sofar = start["left"] - mid["p"]["left"]
                if (travel > 100 and mid["walking"] >= 1 and 0 < sofar < travel * 0.6):
                    print(f"  ✓ 帯は歩いて移動する（400ms で {sofar/travel:.0%}・着地まで {travel:.0f}px）")
                else:
                    print(f"  ✗ 帯が瞬間移動している: 進んだ {sofar:.0f} / 全体 {travel:.0f}"
                          f" walking={mid['walking']}")
                    ng += 1
            else:
                print(f"  ✗ bandPoint が取れない: {start} {mid} {end_pt}")
                ng += 1
            # 奥列（会議室）へ移るときは**縦も一緒に動く**（y を補間しないと 30px 飛ぶ・同レビュー medium）
            # minions>0 かつ working ＝ 会議室（ui/core/world.js の zoneOf）
            up = world_with(minions=2, state="working", attention=False, question="", approvalMin=0)
            live_body[0] = json.dumps(up, ensure_ascii=False)
            live.evaluate("(w) => window.__office.inject(w)", up)
            live.wait_for_timeout(400)
            mid_up = live.evaluate("(id) => window.__office.debug.bandPoint(id)", target_id)
            live.wait_for_function("() => (window.__office.stats() || {}).walking === 0", timeout=15000)
            top_up = live.evaluate("(id) => window.__office.debug.bandPoint(id)", target_id)
            if mid_up and top_up and end_pt["top"] > top_up["top"]:
                if end_pt["top"] > mid_up["top"] > top_up["top"]:
                    print(f"  ✓ 会議室へは縦も一緒に動く（{end_pt['top']:.0f} → {mid_up['top']:.0f} → {top_up['top']:.0f}px）")
                else:
                    print(f"  ✗ 縦が飛んでいる: {end_pt['top']} → {mid_up['top']} → {top_up['top']}")
                    ng += 1
            else:
                print(f"  ✗ 会議室へ移れていない: {mid_up} {top_up}")
                ng += 1

            # 歩いている最中のリサイズで**目的地へ飛ばない**（部屋の伸縮と移動を混ぜない・同レビュー）
            again = world_with(question="もう一度どうぞ", approvalMin=2,
                               attention=True, state="waiting", minions=0)
            live_body[0] = json.dumps(again, ensure_ascii=False)   # ポーリングが巻き戻さないように
            live.evaluate("(w) => window.__office.inject(w)", again)
            live.wait_for_function("() => (window.__office.stats() || {}).walking >= 1", timeout=8000)
            live.wait_for_timeout(200)
            before_rs = live.evaluate(
                "(id) => ({ p: window.__office.debug.bandPoint(id),"
                "  w: (window.__office.stats() || {}).walking })", target_id)
            live.set_viewport_size({"width": 1438, "height": VIEWPORT["height"]})
            live.wait_for_timeout(200)
            after_rs = live.evaluate(
                "(id) => ({ p: window.__office.debug.bandPoint(id),"
                "  w: (window.__office.stats() || {}).walking })", target_id)
            if (before_rs["p"] and after_rs["p"] and before_rs["w"] >= 1 and after_rs["w"] >= 1
                    and abs(after_rs["p"]["left"] - before_rs["p"]["left"]) < 120):
                print("  ✓ 歩いている最中に窓の幅が変わっても、目的地へ飛ばずに歩き続ける")
            else:
                print(f"  ✗ リサイズで歩行が打ち切られた: {before_rs} → {after_rs}")
                ng += 1
            # 倍率ごと変わる幅（1438 → 834）でも、歩いている人が canvas の外へ消えない
            live.set_viewport_size({"width": 834, "height": 1112})
            live.wait_for_timeout(200)
            narrow = live.evaluate(
                "(id) => { const p = window.__office.debug.bandPoint(id);"
                "  const c = document.querySelector('#stage canvas').getBoundingClientRect();"
                "  return { inside: p ? (p.left >= c.left - 1 && p.left <= c.right + 1) : null,"
                "    left: p && Math.round(p.left), c0: Math.round(c.left), c1: Math.round(c.right),"
                "    room: (window.__office.stats() || {}).logicalW }; }", target_id)
            if narrow["inside"]:
                print(f"  ✓ 倍率の変わる幅（→834）でも歩いている人が帯の中に居る（論理 {narrow['room']}）")
            else:
                print(f"  ✗ 歩いている人が canvas の外へ消えた: {narrow}")
                ng += 1
            live.set_viewport_size(VIEWPORT)
            live.wait_for_function("() => (window.__office.stats() || {}).walking === 0", timeout=15000)

            # 窓の幅が変わっただけで歩き出さない（中央寄せの ox を移動に混ぜない・別モデルレビュー medium）
            live.set_viewport_size({"width": 1246, "height": VIEWPORT["height"]})
            live.wait_for_timeout(300)
            moved_by_resize = live.evaluate("() => (window.__office.stats() || {}).walking")
            if moved_by_resize == 0:
                print("  ✓ 窓の幅を変えても誰も歩き出さない（帯の中央寄せは移動ではない）")
            else:
                print(f"  ✗ リサイズが移動に化けている: walking={moved_by_resize}")
                ng += 1
            live.set_viewport_size(VIEWPORT)
            live_body[0] = payload
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
            # 再生を止めたら**帯も止まる**（live の時計を渡すと帯だけ歩き続ける・別モデルレビュー medium）
            rp.click("#replay-pause")
            rp.wait_for_timeout(200)
            band_t0 = rp.evaluate("() => [(window.__office.stats() || {}).t, window.__office.t()]")
            rp.wait_for_timeout(700)
            band_t1 = rp.evaluate("() => [(window.__office.stats() || {}).t, window.__office.t()]")
            if band_t0[0] == band_t1[0] and band_t1[1] > band_t0[1]:
                print("  ✓ 再生を止めると帯の時計も止まる（live の時計で動き続けない）")
            else:
                print(f"  ✗ 再生を止めても帯が動いている: 帯 {band_t0[0]}→{band_t1[0]} live {band_t0[1]}→{band_t1[1]}")
                ng += 1
            rp.click("#replay-pause")
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
    print("✓ pixel スモーク: 台帳・❗→inbox・シート・照準・形状 6 つ・帯（歩行/再生/ホバー）・エラー 0")
    return 0


if __name__ == "__main__":
    sys.exit(main())
