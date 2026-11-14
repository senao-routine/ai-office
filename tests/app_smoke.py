#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""R122: 明るい 3 列の画面の操作・実投函・部屋・4 形状を検査する。

固定ワールドを配り、描画の確認と実サーバーへの inbox / reply 投函を分けて確かめる。
Playwright を使用するため verify ▶7 / dev.sh --app-smoke から実行する。
"""
import json
import os
import pathlib
import re
import socket
import subprocess
import sys
import tempfile
import urllib.request
import time
from urllib.parse import parse_qs, urlparse

ROOT = pathlib.Path(__file__).resolve().parent.parent
TMP = ROOT / "tests" / "artifacts" / ".tmp"
sys.path.insert(0, str(ROOT / "tools"))
from ui_shot import SWIFTSHADER, VIEWPORT, free_port  # noqa: E402

WORLD = ROOT / "tests" / "fixtures" / "world" / "basic.json"
VIEWPORTS = ((1440, 900), (1280, 800), (1024, 768), (390, 844))
ACTIVITY = [
    {"k": "u", "t": "スマートフォンでも読める画面にしてください", "ts": 1753799800},
    {"k": "a", "t": "画面の幅を確かめて、文章が折り返すように直しました。", "ts": 1753799810},
    {"k": "tool", "id": "app-smoke-read", "n": "Read", "t": "ui/example.js", "ts": 1753799820},
    {"k": "res", "for": "app-smoke-read", "rl": 12, "ts": 1753799821},
]
# R123-B: 会話の末尾に質問（選択肢つき）があるセッションの行。セッションごとに差し替える（既定は ACTIVITY）
ACTIVITY_BY_SESSION = {}


def start_server(port, home):
    """既存 smoke と同じサーバーを、ほかのテストと共有しない一時 home で起動する。"""
    proc = subprocess.Popen(
        [sys.executable, str(ROOT / "server" / "office_server.py"), "--port", str(port)],
        cwd=str(ROOT), env={**os.environ, "OFFICE_HOME": str(home)},
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(80):
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=.2):
                return proc
        except OSError:
            time.sleep(.1)
    proc.terminate()
    proc.wait(timeout=5)
    raise RuntimeError("スモーク用サーバーが起動しませんでした")


def wait_file(path, timeout=6):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if path.is_file():
            return json.loads(path.read_text(encoding="utf-8"))
        time.sleep(.1)
    raise AssertionError(f"実投函ファイルが作られない: {path.name}")


def selected(page):
    return page.evaluate("""() => {
      const v = window.__office.debug.selected;
      return typeof v === 'function' ? v() : v;
    }""")


def room_mode(page):
    return page.evaluate("""() => {
      const v = window.__office.debug.appRoomMode;
      return typeof v === 'function' ? v() : v;
    }""")


def guard_room_storage(page):
    """既定と逆の保存値を置き、固定時刻・デモでの読み書きも検出する。"""
    page.add_init_script("""
      (() => {
        const keys = ['aioffice.app.room', 'aioffice.app.pip'];
        const get = Storage.prototype.getItem, set = Storage.prototype.setItem;
        set.call(localStorage, keys[0], 'pixel');
        set.call(localStorage, keys[1], '0');
        window.__appSmokeStorageCalls = [];
        window.__appSmokeStorageSnapshot = () => keys.map(k => get.call(localStorage, k));
        Storage.prototype.getItem = function(k) {
          if (this === localStorage && keys.includes(k)) window.__appSmokeStorageCalls.push(['get', k]);
          return get.call(this, k);
        };
        Storage.prototype.setItem = function(k, v) {
          if (this === localStorage && keys.includes(k)) window.__appSmokeStorageCalls.push(['set', k]);
          return set.call(this, k, v);
        };
      })();
    """)


def assert_room_storage_untouched(page, label):
    assert page.evaluate("window.__appSmokeStorageSnapshot()") == ["pixel", "0"], \
        f"{label}で部屋・小窓の保存値を上書きした"
    assert page.evaluate("window.__appSmokeStorageCalls") == [], \
        f"{label}で部屋・小窓の保存値を読み書きした"


def configure(page, payload, errors, activity_calls):
    page.on("pageerror", lambda err: errors.append(f"pageerror: {err}"))
    page.on("console", lambda msg: errors.append(f"console.error: {msg.text}")
            if msg.type == "error" else None)
    page.route("**/api/office*", lambda route: route.fulfill(
        status=200, content_type="application/json", body=json.dumps(payload, ensure_ascii=False)))
    status = (ROOT / "tests" / "fixtures" / "status_board" / "basic.json").read_text(encoding="utf-8")
    page.route("**/api/status_board*", lambda route: route.fulfill(
        status=200, content_type="application/json", body=status))

    def activity_route(route):
        activity_calls.append(route.request.url)
        sid = parse_qs(urlparse(route.request.url).query).get("session", [""])[0]
        if sid.startswith("bot-"):
            # R128: Bot の記録は本物の daemon の bot- の枝（口の記録 <id>.log.jsonl）から読む＝差し替えない
            return route.continue_()
        route.fulfill(status=200, content_type="application/json", body=json.dumps({
            "ok": True, "rows": ACTIVITY_BY_SESSION.get(sid, ACTIVITY), "cur": {"off": 4, "h": "app-smoke"},
            "beg": {"off": 0, "h": "app-smoke", "i": 0},
            "top": True, "gap": False, "reset": True}))

    page.route("**/api/session/activity*", activity_route)


def computed_design(page):
    """表示中の実スタイルを検査し、CSS のコメントだけで通る門にしない。"""
    result = page.evaluate("""() => {
      const root = document.querySelector('.ui-app');
      const visible = (e) => e.getClientRects().length && getComputedStyle(e).visibility !== 'hidden';
      const bad = [];
      const neutral = new Set(['rgb(228, 224, 216)', 'rgb(238, 235, 229)',
        'rgb(217, 212, 203)', 'rgba(0, 0, 0, 0)', 'transparent']);
      for (const e of [root, ...root.querySelectorAll('*')]) {
        if (!visible(e)) continue;
        const s = getComputedStyle(e);
        if (/gradient/i.test(s.backgroundImage)) bad.push('gradient:' + e.className);
        if (s.boxShadow !== 'none') bad.push('shadow:' + e.className);
        const uniformBorder = ['Top', 'Right', 'Bottom'].every((side) =>
          s['border' + side + 'Width'] === s.borderLeftWidth &&
          s['border' + side + 'Color'] === s.borderLeftColor &&
          s['border' + side + 'Style'] === s.borderLeftStyle);
        // 四辺が同じボタン枠は帯ではない。左だけ違う色/幅の罫を見張る。
        if (!uniformBorder && (parseFloat(s.borderLeftWidth) > 1 ||
            (parseFloat(s.borderLeftWidth) > 0 && !neutral.has(s.borderLeftColor)))) {
          bad.push('left-band:' + e.className);
        }
        for (const pseudo of ['::before', '::after']) {
          const p = getComputedStyle(e, pseudo), width = parseFloat(p.width);
          if (p.content !== 'none' && p.content !== 'normal' && width > 0 && width <= 6 &&
              parseFloat(p.height) > 16 && !neutral.has(p.backgroundColor)) bad.push('pseudo-band:' + e.className);
        }
      }
      const cards = [...root.querySelectorAll('.app-card')].filter(visible).map((e) => {
        const s = getComputedStyle(e);
        return { radius: parseFloat(s.borderTopLeftRadius), bg: s.backgroundColor, border: s.borderTopColor };
      });
      return { bad, cards, bg: getComputedStyle(root).getPropertyValue('--a-bg').trim(),
        ink: getComputedStyle(root).getPropertyValue('--a-ink').trim() };
    }""")
    assert not result["bad"], f"禁止された見た目: {result['bad'][:8]}"
    assert result["cards"], "白い状況カードがない"
    for card in result["cards"]:
        assert 14 <= card["radius"] <= 16, f"カードの角丸が見本から外れた: {card}"
        assert card["bg"] == "rgb(255, 255, 255)", f"カードが白でない: {card}"
        assert card["border"] == "rgb(228, 224, 216)", f"カードの罫が見本と異なる: {card}"
    assert result["bg"].lower() == "#f7f6f3", result
    assert result["ink"].lower() == "#1c1b19", result


def live_bot_rows(port, sessions, pred, timeout=20.0):
    """本物の daemon の /api/office から Bot の行（employees・roster）を取る。走査（SWR・キャッシュ 2 秒）が口のファイルの変化を拾うまで取り直す。"""
    end = time.monotonic() + timeout
    while True:
        req = urllib.request.Request(f"http://127.0.0.1:{port}/api/office", headers={"X-Office-Local": "1"})
        live = json.loads(urllib.request.urlopen(req, timeout=5).read())
        emps = [e for e in live.get("employees", []) if e.get("session") in sessions]
        if len(emps) == len(sessions) and all(pred(e) for e in emps):
            return emps, [r for r in live.get("roster", []) if r.get("session") in sessions]
        assert time.monotonic() < end, f"/api/office に Bot の行が出ない: {emps}"
        time.sleep(0.25)


def check_bot_panel(page, home, port, world, errors):
    """R128 #4: ⚙ の Bot パネル（本物の daemon の /api/door/*・この一時 home の口）。断り（口の文が欄の下に残る）→ 追加 → 一覧
    → 登録コマンドのコピー → 消音（画面の通知を出さない・あなたの番には出る）→ 削除（2 回押し・状態と記録も消える）。
    Bot の名前は文字のまま（HTML にしない）・コピーするコマンドは valid_id を通った id だけ・禁止の見た目（左の帯・影・グラデーション）が無い。"""
    import shlex
    if str(ROOT / "server") not in sys.path:
        sys.path.insert(0, str(ROOT / "server"))
    import office_door
    ledger = lambda: (json.loads(office_door.ledger_path(home).read_text(encoding="utf-8")).get("bots", {})  # noqa: E731
                      if office_door.ledger_path(home).exists() else {})
    poison = "<img src=x onerror=p=1>"
    page.evaluate("""() => { window.__copied = [];
      Object.defineProperty(navigator, 'clipboard', { configurable: true,
        value: { writeText: async (t) => { window.__copied.push(String(t)); } } }); }""")
    page.locator("#btn-settings").click()
    page.wait_for_selector("#modal .mbots .mbotform", timeout=10000)
    page.wait_for_function("() => (document.querySelector('#modal .mbots > .mnote')?.textContent || '').startsWith('Bot はまだいません')",
                           timeout=10000)
    head = page.evaluate("() => document.querySelector('#modal .mbots .msub')?.textContent")
    assert head == "Bot", f"設定に Bot の節の見出しが無い: {head!r}"
    computed_design(page)                                           # 禁止の見た目（左の帯・影・グラデーション）が節に無い
    form = page.locator("#modal .mbotform")
    inputs = form.locator("input")
    assert inputs.count() == 3, "追加の欄が id・名前・役の 3 つでない"
    # 口の断り（形の違う id）: サーバーが 400 と口の文を返し、文が欄の下に残る。ブラウザはこの 400 を console.error に 1 行出す
    # ＝この 1 回だけを URL で確かめて数えない（それ以外の console.error は今までどおり最後に赤）
    before, urls = len(errors), []
    on_console = lambda m: urls.append((m.location or {}).get("url", "")) if m.type == "error" else None  # noqa: E731
    page.on("console", on_console)
    inputs.nth(0).fill("Panel Bot")
    inputs.nth(1).fill("x")
    form.locator("button[type=submit]").click()
    page.wait_for_function("""() => { const e = document.querySelector('#modal .mboterr');
        return e && !e.hidden && e.textContent.includes('英小文字'); }""", timeout=10000)
    end = time.monotonic() + 5
    while len(errors) == before and time.monotonic() < end:
        page.wait_for_timeout(100)
    page.remove_listener("console", on_console)
    expected = errors[before:]
    assert len(expected) == 1 and "status of 400" in expected[0] and urls and all(u.endswith("/api/door/bot/add") for u in urls), \
        f"断りの 400 以外の console.error が出た: {expected} {urls}"
    del errors[before:]
    assert "Panel Bot" not in json.dumps(ledger(), ensure_ascii=False), "形の違う id を台帳に書いた"
    inputs.nth(0).fill("panel-bot")
    inputs.nth(1).fill(poison)
    inputs.nth(2).fill("見張り")
    form.locator("button[type=submit]").click()
    row = page.locator('#modal .mbot[data-bot="panel-bot"]')
    row.wait_for(timeout=10000)
    drawn = page.evaluate("""() => { const b = document.querySelector('#modal .mbots'), r = b.querySelector('.mbot[data-bot="panel-bot"]');
        return { name: r.querySelector('.mdevname').textContent, meta: r.querySelector('.mdevsub').textContent,
                 cmd: r.querySelector('.mbotcmd').textContent, html: b.querySelectorAll('img, script').length,
                 pwned: window.p === 1, err: document.querySelector('#modal .mboterr').hidden,
                 inputs: [...document.querySelectorAll('#modal .mbotform input')].map((i) => i.value) }; }""")
    assert drawn["name"] == poison and drawn["html"] == 0 and not drawn["pwned"], f"Bot の名前が文字のまま描かれていない: {drawn}"
    assert drawn["err"] and drawn["inputs"] == ["", "", ""], f"登録の後に欄と断りの文が片付かない: {drawn}"
    assert "panel-bot" in drawn["meta"] and "まだ出勤していません" in drawn["meta"], f"一覧の行の id と出勤の印が違う: {drawn}"
    assert ledger().get("panel-bot", {}).get("name") == poison and ledger()["panel-bot"].get("muted") is False, ledger()
    row.locator(".mbotcopy").click()
    page.wait_for_function("() => window.__copied.length === 1", timeout=5000)
    copied = page.evaluate("() => window.__copied[0]")
    argv = shlex.split(copied)
    assert copied == drawn["cmd"], f"コピーしたコマンドが行に見えているものと違う: {copied!r}"
    assert argv[:5] == ["claude", "mcp", "add", "aioffice-door-panel-bot", "--"] and argv[-2:] == ["--bot", "panel-bot"] \
        and argv[6].endswith("/server/mcp_office.py") and len(argv) == 9, f"登録コマンドの形が違う: {argv}"
    assert row.locator(".mbotcopy").inner_text() == "コピーしました", "コピーしたことが分からない"
    print("  ✓ R128 #4 ⚙ の Bot の節: 空の案内 → 形の違う id は口の文で断る → 追加 → 一覧（名前の毒は文字のまま）→ 登録コマンドのコピー（valid_id の id・9 語）")

    row.locator(".mbotmute").click()
    page.wait_for_function("""() => { const r = document.querySelector('#modal .mbot[data-bot="panel-bot"]');
        return r && r.querySelector('.mbotmuted') && r.querySelector('.mbotmute').textContent === '消音を解く'; }""", timeout=10000)
    assert ledger()["panel-bot"]["muted"] is True, f"消音が台帳に無い: {ledger()}"
    page.keyboard.press("Escape")
    # 消音でない Bot と並べて相談を出す → 画面の通知（tray）は消音でない Bot だけ・あなたの番には両方出る（鳴らさないだけ）
    assert office_door.add_bot("loud-bot", "見回り係", home=home)["ok"]
    for bot_id, question in (("panel-bot", "静かな相談？"), ("loud-bot", "鳴る相談？")):
        assert office_door.checkin(bot_id, "見回り中", home=home)["ok"]
        assert office_door.ask(bot_id, question, home=home)["ok"]
    sessions = {"bot-panel-bot", "bot-loud-bot"}
    emps, roster_rows = live_bot_rows(port, sessions, lambda e: bool(e.get("ask")))
    assert {e["session"]: e["muted"] for e in emps} == {"bot-panel-bot": True, "bot-loud-bot": False}, emps
    page.evaluate("""() => { window.__notes = []; window.__hid = true;
      Object.defineProperty(document, 'hidden', { configurable: true, get: () => window.__hid === true });
      localStorage.setItem('aioffice.iso.notify', '1');
      window.Notification = class { static permission = 'granted';
        constructor(title, opts) { window.__notes.push(opts && opts.tag); } close() {} }; }""")
    bot_world = json.loads(json.dumps(world))
    bot_world["employees"] = [*bot_world.get("employees", []), *emps]
    bot_world["roster"] = [*bot_world["roster"], *roster_rows]
    page.evaluate("w => window.__office.inject(w)", bot_world)
    seen = page.evaluate("""() => { const out = { notes: window.__notes.slice(),
        waiting: [...document.querySelectorAll('#app-attention-list .app-session-row')].map((r) => r.dataset.appSession) };
      window.__hid = false; localStorage.removeItem('aioffice.iso.notify'); return out; }""")
    assert "aioffice-attn-bot-loud-bot" in seen["notes"], f"消音でない Bot の相談で画面の通知が出ない（偽の Notification が効いていない）: {seen}"
    assert "aioffice-attn-bot-panel-bot" not in seen["notes"], f"消音の Bot の相談で画面の通知が出た: {seen}"
    assert {"bot-panel-bot", "bot-loud-bot"} <= set(seen["waiting"]), f"消音の Bot を「あなたの番」から隠した: {seen}"
    print("  ✓ R128 #4 消音: 台帳に残る → 消音の Bot の相談は画面の通知を出さない・消音でない Bot は出る・どちらもあなたの番に並ぶ")

    assert office_door.report("panel-bot", "済み", home=home)["ok"]
    assert office_door.log_path("panel-bot", home).exists()
    page.locator("#btn-settings").click()
    row = page.locator('#modal .mbot[data-bot="panel-bot"]')
    row.wait_for(timeout=10000)
    row.locator(".mbotremove").click()
    assert row.locator(".mbotremove").inner_text() == "もう一度押すと削除（状態と記録も消えます）", "削除が 1 回押しで進む（確認の段が無い）"
    time.sleep(0.3)
    assert "panel-bot" in ledger(), "1 回目の押しで削除した"
    row.locator(".mbotremove").click()
    page.wait_for_function("() => !document.querySelector('#modal .mbot[data-bot=\"panel-bot\"]')", timeout=10000)
    assert "panel-bot" not in ledger(), "2 回目の押しで台帳から外れない"
    assert not office_door.state_path("panel-bot", home).exists() and not office_door.log_path("panel-bot", home).exists(), \
        "削除で状態と記録が消えない"
    page.keyboard.press("Escape")
    assert office_door.remove_bot("loud-bot", home=home)["ok"]
    page.evaluate("w => window.__office.inject(w)", world)
    print("  ✓ R128 #4 削除: 1 回目は「もう一度押すと削除」だけ（台帳は変わらない）→ 2 回目で台帳・状態・記録が消える")


def check_today_card(page):
    """R129 今日の 1 枚: 左の足元のボタン → 本物の daemon の /api/digest（この一時 home＝記録なし）と部屋のドット絵の写し →
    窓に 1200×675 の PNG・保存名は日付だけ・保存のリンクに手が置かれる → Esc で閉じてボタンへ戻る・object URL を返す。
    名前が入らないことは構造で守る（ui/core/today.js は名前の鍵を返さない＝today.test.js・部屋の canvas は文字を描かない）。"""
    page.evaluate("""() => { window.__revoked = []; const r = URL.revokeObjectURL.bind(URL);
      URL.revokeObjectURL = (u) => { window.__revoked.push(u); r(u); }; }""")
    button = page.locator("#app-today")
    assert button.is_visible() and button.inner_text() == "今日の 1 枚", f"左の足元に「今日の 1 枚」が無い: {button.inner_text()!r}"
    button.click()
    page.wait_for_selector("#app-today-card img", timeout=30000)
    page.wait_for_function("() => document.querySelector('#app-today-card img').complete", timeout=10000)
    got = page.evaluate("""() => { const img = document.querySelector('#app-today-card img'), a = document.querySelector('#app-today-card a');
        return { w: img.naturalWidth, h: img.naturalHeight, src: img.src, name: a.download, href: a.href === img.src,
                 focus: document.activeElement === a, role: document.querySelector('#app-today-card').getAttribute('role') }; }""")
    assert (got["w"], got["h"]) == (1200, 675) and got["src"].startswith("blob:"), f"今日の 1 枚の絵が 1200×675 の PNG でない: {got}"
    assert re.fullmatch(r"ai-office-\d{4}-\d{2}-\d{2}\.png", got["name"]) and got["href"] and got["focus"] and got["role"] == "dialog", \
        f"保存のリンク・名前・手の置き場が違う: {got}"
    page.keyboard.press("Escape")
    page.wait_for_function("() => !document.querySelector('#app-today-card')", timeout=5000)
    after = page.evaluate("() => ({ focus: document.activeElement === document.querySelector('#app-today'), revoked: window.__revoked })")
    assert after["focus"] and got["src"] in after["revoked"], f"閉じた後にボタンへ戻らない・object URL を返していない: {after}"
    print("  ✓ R129 今日の 1 枚: 左の足元 → 1200×675 の PNG を窓で見せる・保存名は日付だけ・Esc で閉じてボタンへ戻る")


def main():
    from playwright.sync_api import sync_playwright

    world = json.loads(WORLD.read_text(encoding="utf-8"))
    roster = world["roster"]
    expected_sessions = {
        brief["session"]
        for project in roster
        for brief in (project.get("sessions") or [{"session": project["session"]}])
    }
    target = next(p for p in roster if p["state"] == "working" and not p.get("attention"))
    session = target["session"]
    # attention の搬送値で数える。question がなくても attention な許可まちは含める。
    attention = [p for p in roster if p.get("attention") or p.get("approvalMin", 0) > 0
                 or p.get("question") or p.get("ask")]
    errors, activity_calls = [], []
    TMP.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="app-smoke-", dir=TMP) as temp:
        home = pathlib.Path(temp)
        port = free_port()
        proc = start_server(port, home)
        url = f"http://127.0.0.1:{port}/?ui=app&seed=11"
        try:
            with sync_playwright() as pw:
                browser = pw.chromium.launch(args=SWIFTSHADER)
                context = browser.new_context(viewport=VIEWPORT, device_scale_factor=1)
                fixed = context.new_page()
                configure(fixed, world, errors, activity_calls)
                guard_room_storage(fixed)
                fixed.goto(url + "&t=3.2")
                fixed.wait_for_function("window.__office && window.__office.ready", timeout=30000)
                assert fixed.locator(".ui-app").count() == 1, "app が mount されていない"
                assert fixed.locator('#app-room > .app-room-layer[data-room="3d"] canvas:visible').count() == 1, "3D の部屋に canvas がない"
                assert room_mode(fixed) == "3d", "部屋の既定が 3D でない"
                assert fixed.locator("#app-room[data-room-pip='1'] .app-room-pip").is_visible(), "小窓が既定で出ない"
                assert fixed.locator('.app-room-pip-view[data-room="pixel"] canvas:visible').count() == 1, "3D の小窓がドット絵でない"
                assert_room_storage_untouched(fixed, "固定時刻の初期表示")
                rows = fixed.locator(".app-session-row[data-app-session]")
                assert rows.count() >= len(expected_sessions), "左の一覧にセッションが足りない"
                sessions = set(rows.evaluate_all("rows => rows.map(r => r.dataset.appSession)"))
                assert sessions == expected_sessions, "セッション一覧が固定ワールドと異なる"
                assert int(fixed.locator("#app-your-count").inner_text()) == len(attention), "あなたの番の数が違う"
                displayed_name = fixed.locator(f'.app-session-row[data-app-session="{session}"] .app-row-name').first.inner_text()
                fixed.locator("#app-search").fill(displayed_name)
                assert fixed.locator(f'.app-session-row[data-app-session="{session}"]:visible').count() > 0
                fixed.locator("#app-search").fill("")
                assert not activity_calls, "固定時刻の画面が activity を取得した"
                print("  ✓ app の mount・左の一覧・あなたの番・検索・主の 3D とドット絵の小窓・固定時刻の取得抑止")

                # R124: 一覧の選択を主の札と小窓の寄りにそろえる。キーによる小窓の交換では選択を変えない。
                fixed.locator(f'.app-session-row[data-app-session="{session}"]').first.click()
                assert selected(fixed) == session, "固定時刻の一覧で人を選べない"
                selected_label = fixed.locator(f'.app-room-label.is-selected[data-agent="{session}"]')
                assert selected_label.is_visible(), "選んだ人の名札が主の絵にない"
                assert fixed.locator(".app-room-label.is-attention").count() > 0, "あなたの番の名札に朱の class がない"
                assert fixed.locator(".app-room-label").count() <= 4, "名札が 4 枚を超えた"
                assert selected_label.evaluate("e => getComputedStyle(e).pointerEvents") == "none", "名札がクリックを遮る"
                pip_position = fixed.locator('.app-room-pip-view[data-room="pixel"] canvas').evaluate("e => [e.style.left, e.style.top]")
                another_session = next(s for s in sorted(expected_sessions) if s != session)
                fixed.locator(f'.app-session-row[data-app-session="{another_session}"]').first.click()
                assert fixed.locator(f'.app-room-label.is-selected[data-agent="{another_session}"]').is_visible(), "一覧の再選択が名札に反映されない"
                assert fixed.locator('.app-room-pip-view[data-room="pixel"] canvas').evaluate("e => [e.style.left, e.style.top]") != pip_position, \
                    "一覧の再選択でドット絵の小窓の寄りが替わらない"
                fixed.locator(".app-room-pip-swap").press("Enter")
                assert room_mode(fixed) == "pixel", "小窓を押しても主がドット絵にならない"
                assert fixed.locator('.app-room-pip-view[data-room="3d"] canvas:visible').count() == 1, "小窓が 3D に入れ替わらない"
                assert fixed.locator("#app-room-pixel").get_attribute("aria-pressed") == "true", "小窓の交換と切替ボタンの aria-pressed がずれた"
                assert fixed.locator("#app-room-3d").get_attribute("aria-pressed") == "false", "交換後も 3D の切替が選択中"
                assert selected(fixed) == another_session, "小窓のキー操作が主の人選択に伝わった"
                assert fixed.locator(f'.app-room-label.is-selected[data-agent="{another_session}"]').is_visible(), "主の入替後に選んだ人の札が消えた"
                pixel_points = fixed.evaluate("""(ids) => {
                  const host = document.querySelector('#app-room').getBoundingClientRect();
                  const canvas = document.querySelector('#app-room > .app-room-layer[data-room="pixel"] canvas').getBoundingClientRect();
                  return ids.flatMap(id => {
                    const p = window.__office.debug.agentPoint(id);
                    return p ? [{id, x: (p.left + host.left - canvas.left) / canvas.width,
                      y: (p.top + host.top - canvas.top) / canvas.height}] : [];
                  });
                }""", sorted(expected_sessions))
                fixed.locator(".app-room-pip-hide").click()
                assert fixed.locator("#app-room").get_attribute("data-room-pip") == "0", "小窓を隠せない"
                assert not fixed.locator(".app-room-pip").is_visible(), "畳んだ小窓が残った"
                assert fixed.locator(".app-room-pip-show").is_visible(), "畳んだ位置に小窓ボタンがない"
                fixed.locator(".app-room-pip-show").click()
                assert fixed.locator(".app-room-pip").is_visible(), "小窓ボタンで戻せない"
                fixed.locator(".app-room-pip-swap").press("Enter")
                assert room_mode(fixed) == "3d", "小窓を押して主を 3D に戻せない"
                # 主のドット絵で得た照準を同じ canvas の小窓へ写し、選択中以外の人をクリックする。
                pip_target = fixed.evaluate("""({points, selected}) => {
                  const view = document.querySelector('.app-room-pip-view').getBoundingClientRect();
                  const canvas = document.querySelector('.app-room-pip-view canvas').getBoundingClientRect();
                  const hide = document.querySelector('.app-room-pip-hide').getBoundingClientRect();
                  return points.filter(p => p.id !== selected).map(p => ({id: p.id,
                    x: canvas.left + p.x * canvas.width, y: canvas.top + p.y * canvas.height}))
                    .find(p => p.x > view.left + 4 && p.x < view.right - 4 &&
                      p.y > view.top + 4 && p.y < view.bottom - 4 &&
                      !(p.x >= hide.left && p.x <= hide.right && p.y >= hide.top && p.y <= hide.bottom));
                }""", {"points": pixel_points, "selected": another_session})
                assert pip_target, "小窓で選択を検査する別の人が見つからない"
                fixed.mouse.click(pip_target["x"], pip_target["y"])
                assert room_mode(fixed) == "pixel", "小窓の人を押しても主がドット絵にならない"
                assert selected(fixed) == pip_target["id"], "小窓で押した人と選択がそろわない"
                assert fixed.locator(f'.app-room-label.is-selected[data-agent="{pip_target["id"]}"]').is_visible(), "小窓で選んだ人の名札がない"
                assert fixed.locator(f'.app-session-row.is-selected[data-app-session="{pip_target["id"]}"]').count() > 0, "小窓の選択が一覧に反映されない"
                fixed.locator("#app-room-3d").click()
                fixed.locator(".app-room-pip-hide").click()
                main_target = session if selected(fixed) != session else another_session
                main_point = fixed.evaluate("""id => {
                  const p = window.__office.debug.agentPoint(id);
                  const r = document.querySelector('#app-room').getBoundingClientRect();
                  return p && {x: r.left + p.left, y: r.top + p.top};
                }""", main_target)
                assert main_point, "主の 3D の人の照準がない"
                fixed.mouse.click(main_point["x"], main_point["y"])
                assert selected(fixed) == main_target, "主の 3D で押した人と選択がそろわない"
                assert fixed.locator(f'.app-room-label.is-selected[data-agent="{main_target}"]').is_visible(), "主で選んだ人の名札がない"
                assert fixed.locator(f'.app-session-row.is-selected[data-app-session="{main_target}"]').count() > 0, "主の選択が一覧に反映されない"
                fixed.locator(".app-room-pip-show").click()
                assert_room_storage_untouched(fixed, "固定時刻の小窓操作")

                # 最大数は 5 人以上が待つ実データを注入して検査する。
                crowded = json.loads(json.dumps(world))
                for project in crowded["roster"]:
                    project["attention"] = True
                    for brief in project.get("sessions", []):
                        brief["attention"] = True
                for employee in crowded.get("employees", []):
                    employee["attention"] = True
                fixed.evaluate("w => window.__office.inject(w)", crowded)
                assert fixed.locator(".app-room-label").count() == 4, "待つ人が多いときの名札が 4 枚でない"
                assert fixed.locator(".app-room-label.is-attention").count() == 4, "待つ人の名札から朱の class が抜けた"
                # R124 検収: 隣の机の人どうしの名札が重なって、選んだ人の札が隠れていた（本番の実データで 6px 差）＝見えている札は互いに重ならない
                overlap = fixed.evaluate("""() => {
                    const r = [...document.querySelectorAll('#app-room .app-room-label')].filter((l) => !l.hidden).map((l) => l.getBoundingClientRect());
                    for (let i = 0; i < r.length; i++) for (let j = i + 1; j < r.length; j++) {
                        const a = r[i], b = r[j];
                        if (a.left < b.right && b.left < a.right && a.top < b.bottom && b.top < a.bottom) return [i, j];
                    }
                    return null;
                }""")
                assert overlap is None, f"名札どうしが重なった: {overlap}"
                fixed.evaluate("w => window.__office.inject(w)", world)
                print("  ✓ 小窓の交換・aria-pressed 同期・隠す/戻す・一覧/主/小窓の選択同期・選択の名札と寄り・あなたの番の朱・名札は最大 4 で互いに重ならない・固定時刻の保存読み書き 0")

                for width, height in VIEWPORTS:
                    fixed.set_viewport_size({"width": width, "height": height})
                    fixed.wait_for_timeout(150)
                    overflow = fixed.evaluate("""() => Math.max(document.documentElement.scrollWidth,
                        document.body.scrollWidth) - innerWidth""")
                    assert overflow <= 0, f"{width}×{height} で横スクロール {overflow}px"
                    if width < 720:
                        fixed.locator("#app-menu").click()
                        assert fixed.locator("#app-search").is_visible(), "小さい画面で左の一覧が開かない"
                        fixed.locator("#app-sidebar-close").click()
                    if width < 1000:
                        fixed.locator("#app-details-toggle").click()
                    computed_design(fixed)
                    if width < 1000:
                        fixed.locator("#app-details-close").click()
                print("  ✓ 1440×900 / 1280×800 / 1024×768 / 390×844 の横スクロール 0・引き出し・計算後の色と角丸")

                fixed.set_viewport_size(VIEWPORT)
                fixed.evaluate("window.__appSmokeCanvas = [...document.querySelectorAll('#app-room > .app-room-layer canvas')].find(c => c.getClientRects().length)")
                fixed.locator("#app-room-pixel").click()
                fixed.wait_for_function("window.__appSmokeCanvas !== [...document.querySelectorAll('#app-room > .app-room-layer canvas')].find(c => c.getClientRects().length)")
                assert_room_storage_untouched(fixed, "固定時刻の切替ボタン")
                fixed.close()

                page = context.new_page()
                configure(page, world, errors, activity_calls)
                # 3D の部屋は SwiftShader（headless）では 1 コマ数秒（実測 rAF 3/s・evaluate 最長 11 秒）＝クリックが待ちで落ちる。
                # live の操作は保存したドット絵＋小窓を畳んだ状態で回す。小窓の 3D は作らない。
                page.add_init_script("try { localStorage.setItem('aioffice.app.room', 'pixel'); localStorage.setItem('aioffice.app.pip', '0'); } catch (e) {}")
                page.goto(url)
                page.wait_for_function("window.__office && window.__office.ready", timeout=30000)
                assert room_mode(page) == "pixel", "保存したドット絵の部屋を読み込まない"
                assert page.locator("#app-room").get_attribute("data-room-pip") == "0", "保存した小窓の畳みを読み込まない"
                assert page.locator("#app-room canvas").count() == 1, "畳んだ小窓の 3D canvas が作られた"
                assert page.locator('#app-room canvas[data-room="3d"]').count() == 0, "畳んだ小窓に 3D が作られた"
                assert page.locator(".app-room-pip-show").is_visible(), "保存で畳んだ小窓を戻すボタンがない"
                print("  ✓ live は保存したドット絵・畳んだ小窓を読み込み、3D canvas を作らない")
                page.locator(f'.app-session-row[data-app-session="{session}"]').first.click()
                page.wait_for_function("""(s) => {
                    const v = window.__office.debug.selected;
                    return (typeof v === 'function' ? v() : v) === s;
                }""", arg=session)
                page.wait_for_selector("#app-activity [data-act-key]", timeout=10000)
                page.wait_for_function("() => (document.querySelector('#app-activity')?.innerText || '').includes('文章が折り返す')", timeout=15000)
                assert "文章が折り返す" in page.locator("#app-activity").inner_text(), "中央の作業ビューが選択した会話でない"
                print("  ✓ 左の一覧から選択すると中央の作業ビューに activity の行が出る")

                # R122 レビュー（medium）: 同じデータの再取得で行を作り直さない＝フォーカスと押しかけのクリックを失わない
                page.evaluate("""(s) => {
                    window.__appRow = document.querySelector(`#app-people .app-session-row[data-app-session="${s}"]`);
                    window.__appRow.focus();
                }""", session)
                page.evaluate("w => { window.__office.inject(w); window.__office.inject(w); }", world)
                assert page.evaluate("() => window.__appRow.isConnected && document.activeElement === window.__appRow"), \
                    "同じデータの再描画で一覧の行が作り直され、フォーカスが外れた"
                print("  ✓ 同じデータの再描画で一覧の行を使い回す（フォーカスが残る）")

                # 2026-11-14: 中央の記録も、同じデータなら描き直さない（毎秒の /api/office で丸ごと作り直すと、中央の文を選んでも 1 秒で
                # 外れた＝本番で確かめた）。中身が替わったら描き直す（同じ節を使い回し続けない）。
                page.evaluate("""() => { const n = [...document.querySelectorAll('#app-activity [data-act-key]')]
                    .find((x) => (x.textContent || '').includes('文章が折り返す'));
                  window.__actNode = n; getSelection().selectAllChildren(n); }""")
                chosen = page.evaluate("() => getSelection().toString()")
                page.evaluate("w => { window.__office.inject(w); window.__office.inject(w); }", world)
                kept = page.evaluate("() => ({ alive: window.__actNode.isConnected, sel: getSelection().toString() })")
                assert "文章が折り返す" in chosen and kept["alive"] and kept["sel"] == chosen, \
                    f"同じデータの再描画で中央の記録が作り直され、選んだ文が外れた: {kept}"
                changed = json.loads(json.dumps(world))
                for row in [*changed.get("employees", []), *changed.get("roster", []),
                            *(b for r in changed.get("roster", []) for b in (r.get("sessions") or []))]:
                    if row.get("session") == session:
                        row["state"] = "idle"
                page.evaluate("w => window.__office.inject(w)", changed)
                assert not page.evaluate("() => window.__actNode.isConnected"), "状態が替わっても中央の記録を描き直さない"
                page.evaluate("w => window.__office.inject(w)", world)
                page.evaluate("() => getSelection().removeAllRanges()")
                print("  ✓ 同じデータの再描画で中央の記録を作り直さない（選んだ文が残る）・状態が替われば描き直す")

                message = "アプリ画面からのスモーク用の指示です"
                page.locator("#app-input").fill(message)
                page.locator("#app-input").press("Shift+Enter")
                assert "\n" in page.locator("#app-input").input_value(), "Shift+Enter で改行できない"
                assert not (home / ".claude" / "office_inbox" / f"{session}.json").exists(), "Shift+Enter で送信された"
                page.locator("#app-input").press("Enter")
                inbox = wait_file(home / ".claude" / "office_inbox" / f"{session}.json")
                assert message in json.dumps(inbox, ensure_ascii=False), "inbox に書かれた指示が違う"
                print("  ✓ 入力欄の Shift+Enter は改行・Enter は実 inbox ファイルに投函")

                approvals = home / ".claude" / "office_approvals"
                approvals.mkdir(parents=True, exist_ok=True)

                def asked_world(aid):
                    (approvals / f"{session}.{aid}.json").write_text(json.dumps({
                        "session": session, "askId": aid, "tool": "Bash", "kind": "permission",
                        "title": "npm test", "options": [], "ts": time.time(),
                        "deadline": time.time() + 600, "pid": 1}), encoding="utf-8")
                    asked = json.loads(json.dumps(world))
                    ask_fields = {"attention": True, "approvalMin": 2, "listening": False, "question": "",
                                  "ask": {"tool": "Bash", "kind": "permission", "title": "npm test", "askId": aid}}
                    next(p for p in asked["roster"] if p["session"] == session).update(ask_fields)
                    # departmentBoard は個別 employee を優先する。集約の代表と brief も同じ掲示にそろえる。
                    for employee in asked.get("employees", []):
                        if employee.get("session") == session:
                            employee.update(ask_fields)
                    for project in asked["roster"]:
                        for brief in project.get("sessions", []):
                            if brief.get("session") == session:
                                brief.update(ask_fields)
                    return asked

                ask_id = "c0ffee22" * 4
                asked = asked_world(ask_id)
                page.evaluate("w => window.__office.inject(w)", asked)
                page.wait_for_selector("#app-answer button[data-answer]", timeout=5000)
                page.locator("#app-answer button[data-answer]").filter(has_text="許可").first.click()
                reply = wait_file(approvals / f"{session}.{ask_id}.reply.json")
                assert reply["behavior"] == "allow" and reply["src"] == "local", f"許可の reply が違う: {reply}"
                print("  ✓ 中央の許可カードから実 reply ファイル（allow / local）に回答")

                # R122 レビュー（high）: 下書きは書き始めた時点の要求に縛る（R100-0）。書いている間に要求が A→B に替わったら
                # 送らずに知らせ、いまの要求を見せた後の 2 回目だけが B へ届く（A への文章が黙って B の答えにならない）。
                first, second = "a1b2c3d4" * 4, "d4c3b2a1" * 4
                page.evaluate("w => window.__office.inject(w)", asked_world(first))
                draft = "要求 A への返事として書いた文章"
                page.locator("#app-input").fill(draft)
                page.evaluate("w => window.__office.inject(w)", asked_world(second))
                page.locator("#app-input").press("Enter")
                page.wait_for_function("() => (document.querySelector('#toast')?.innerText || '').includes('表示の後で要求が替わりました')",
                                       timeout=5000)
                time.sleep(1.0)
                assert not (approvals / f"{session}.{first}.reply.json").exists(), "替わる前の要求 A へ答えた"
                assert not (approvals / f"{session}.{second}.reply.json").exists(), "A への下書きが黙って B へ届いた"
                assert page.locator("#app-input").input_value() == draft, "止めた下書きが消えた"
                page.locator("#app-input").press("Enter")
                reply = wait_file(approvals / f"{session}.{second}.reply.json")
                assert reply["behavior"] == "deny" and reply.get("message") == draft, f"2 回目の送信が B に届かない: {reply}"
                print("  ✓ 下書きの途中で要求が替わると送らずに知らせ、2 回目だけがいまの要求へ届く")

                # R123-B: 質問はサーバーが「（選択肢 / …）」を連結して運ぶ。会話の末尾に同じ質問が選択肢つきで見えているときは、
                # 答えのカードは種類と待ち時間とボタンだけ（題を繰り返さない）・(Recommended) は札・一覧の行にも連結を出さない
                q_session = next(p["session"] for p in roster if p.get("question") and p.get("questionOptions"))
                # 違いは 31 字目より後ろ（旧実装は先頭 30 字だけで同じと見なしていた）
                q_text = "この設計で確定していいですか？いまの案のまま進めます。設定は検証環境のものを使います。"
                q_opts = [{"label": "案A で進める (Recommended)", "description": ""}, {"label": "案B", "description": ""},
                          {"label": "自分で書く", "description": ""}]
                ACTIVITY_BY_SESSION[q_session] = ACTIVITY + [
                    {"k": "q", "t": q_text, "o": [o["label"] for o in q_opts], "ts": 1753799830}]
                q_world = json.loads(json.dumps(world))
                for row in [*q_world["roster"], *q_world.get("employees", [])]:
                    if row.get("session") == q_session:
                        row.update({"question": q_text + "（" + " / ".join(o["label"] for o in q_opts) + "）",
                                    "questionOptions": q_opts, "attention": True})
                page.evaluate("w => window.__office.inject(w)", q_world)
                page.locator(f'#app-attention-list .app-session-row[data-app-session="{q_session}"]').first.click()
                page.wait_for_selector("#app-answer.is-follow", timeout=15000)
                kind = page.locator("#app-answer .app-question-kind").inner_text()
                assert kind.startswith("質問") and q_text not in kind, f"会話に見えている質問をカードが繰り返した: {kind!r}"
                labels = page.locator("#app-answer button[data-answer]").all_inner_texts()
                assert not any("Recommended" in t for t in labels), f"(Recommended) が文のまま出た: {labels}"
                assert page.locator("#app-answer button[data-answer] .app-rec").count() == 1, "おすすめの札が無い"
                row_line = page.locator(f'.app-session-row[data-app-session="{q_session}"] .app-row-line').first.inner_text()
                assert "（案A" not in row_line and q_text in row_line, f"一覧の行に選択肢の連結が出た: {row_line!r}"
                # 別モデルレビュー（medium）: 状態が会話より先に替わり、先頭が同じで末尾だけ違う新しい質問が来たら、題を省かない
                # （古い質問を見ながら新しい要求に答えさせない）。会話の行は古い質問のまま
                q_new = q_text.replace("検証環境", "本番環境")
                for row in [*q_world["roster"], *q_world.get("employees", [])]:
                    if row.get("session") == q_session:
                        row["question"] = q_new + "（" + " / ".join(o["label"] for o in q_opts) + "）"
                page.evaluate("w => window.__office.inject(w)", q_world)
                page.wait_for_function("(t) => { const a = document.querySelector('#app-answer'); return a && !a.classList.contains('is-follow') && a.innerText.includes(t); }",
                                       arg=q_new, timeout=10000)
                print("  ✓ 会話の末尾と同じ質問はカードで繰り返さない・おすすめは札・一覧の行に選択肢の連結を出さない・末尾だけ違う新しい質問は題を出す")

                # R127: 出勤の口の Bot の相談に Mac から答える。本物の口（この一時 home）に Bot を登録して相談を出し、本物の daemon が
                # 作った行（employees・roster）を画面へ流す → 「あなたの番」→ 答えのカード → 選択肢 → 本物の /api/approval/reply の
                # bot- の枝 → 口の inbox に答えと choice。掲示板（office_approvals）には何も書かれない（Bot は許可を持たない＝不変条件 7）。
                # Bot が書いた文（名前・問い・選択肢）は文字として描く（HTML の要素にならない＝手元の画面から何も動かせない）。
                if str(ROOT / "server") not in sys.path:
                    sys.path.insert(0, str(ROOT / "server"))
                import office_door
                bot_name = "<b>郵便受け</b>"
                poison = '<img src=x onerror="window.__r127_pwned=1">'
                assert office_door.add_bot("mailbox", bot_name, "受信箱", home=home)["ok"]
                assert office_door.checkin("mailbox", "依頼を読みました", home=home)["ok"]
                asked_bot = office_door.ask("mailbox", "請求書を送ってよい？" + poison, ["送る<i>今</i>", "明日にする"], home=home)
                assert asked_bot["ok"], asked_bot
                bot_session = "bot-mailbox"
                live = None
                for _ in range(40):                                  # daemon の走査（キャッシュ 2 秒）に Bot の行が出るまで
                    req = urllib.request.Request(f"http://127.0.0.1:{port}/api/office", headers={"X-Office-Local": "1"})
                    live = json.loads(urllib.request.urlopen(req, timeout=5).read())
                    if any(e.get("session") == bot_session and e.get("ask") for e in live.get("employees", [])):
                        break
                    time.sleep(0.25)
                bot_emps = [e for e in live.get("employees", []) if e.get("session") == bot_session]
                bot_roster = [r for r in live.get("roster", []) if r.get("session") == bot_session]
                assert bot_emps and bot_roster and bot_emps[0].get("vendor") == "bot" \
                    and bot_emps[0]["ask"]["askId"] == asked_bot["askId"], f"daemon の office に Bot の相談の行が無い: {bot_emps}"
                bot_world = json.loads(json.dumps(world))
                bot_world["employees"] = [*bot_world.get("employees", []), *bot_emps]
                bot_world["roster"] = [*bot_world["roster"], *bot_roster]
                page.evaluate("w => window.__office.inject(w)", bot_world)
                bot_row = page.locator(f'#app-attention-list .app-session-row[data-app-session="{bot_session}"]')
                bot_row.first.wait_for(timeout=10000)
                bot_row.first.click()
                page.wait_for_function("""() => [...document.querySelectorAll('#app-answer button[data-answer]')]
                    .some(b => (b.innerText || '').includes('明日にする'))""", timeout=10000)
                drawn = page.evaluate("""() => ({ imgs: document.querySelectorAll('#app-answer img, #app-attention-list img, #app-people img, .app-main-head img').length,
                    tags: document.querySelectorAll('#app-answer b, #app-answer i, #app-attention-list b, #app-attention-list i').length,
                    pwned: window.__r127_pwned === 1, card: document.querySelector('#app-answer').innerText,
                    row: document.querySelector('#app-attention-list .app-session-row[data-app-session="bot-mailbox"]').innerText })""")
                assert drawn["imgs"] == 0 and drawn["tags"] == 0 and not drawn["pwned"], f"Bot の文が HTML として描かれた: {drawn}"
                assert "<i>今</i>" in drawn["card"] and bot_name in drawn["row"], f"Bot の文が文字のまま見えていない: {drawn}"
                allow_like = [t for t in page.locator("#app-answer button[data-answer]").all_inner_texts() if "許可" in t]
                assert not allow_like, f"Bot の相談のカードに許可のボタンがある: {allow_like}"
                # 押している間に次のデータが来ても答えが消えない（2026-11-14 本番で見つけた: 中央はデータのたびに作り直す＝押した
                # ボタンが離す前に外れるとブラウザはクリックを出さない → ui/hud/press_hold.js が押している間の描き直しを離した後へ回す）。
                # 人の押し方＝押す → （データが来る）→ 同じボタンのまま → 離す → 答えが届く。離した後は描き直しが走る（ためた 1 回）
                target = page.locator("#app-answer button[data-answer]").filter(has_text="明日にする").first
                box = target.bounding_box()
                target.evaluate("(b) => { window.__pressed = b; }")
                page.mouse.move(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
                page.mouse.down()
                page.evaluate("w => window.__office.inject(w)", bot_world)
                held = page.evaluate("() => window.__pressed.isConnected")
                page.mouse.up()
                assert held, "押している間に次のデータで中央が作り直され、押したボタンが外れた（離しても答えが届かない）"
                page.wait_for_function("() => !window.__pressed.isConnected", timeout=5000)   # ためた描き直しは離した後に走る
                got = None
                for _ in range(40):
                    got = office_door.inbox("mailbox", home=home)
                    if any(it.get("kind") == "answer" for it in got.get("items", [])):
                        break
                    time.sleep(0.15)
                answers = [it for it in got.get("items", []) if it.get("kind") == "answer"]
                assert len(answers) == 1 and answers[0].get("askId") == asked_bot["askId"] \
                    and (answers[0].get("choice") or {}).get("label") == "明日にする" and answers[0]["choice"]["index"] == 1, \
                    f"Bot の inbox に答え（choice つき）が届いていない: {got}"
                assert not list(approvals.glob("bot-*")), "Bot の相談・答えが承認の掲示板に置かれた（不変条件 7）"
                print("  ✓ R127 Bot の相談: Mac の答えのカード → 選択肢 → 口の inbox に答えと choice・掲示板は使わない・Bot の文は文字として描く")
                print("  ✓ 押している間に次のデータが来ても押したボタンは外れず、離すと答えが届く・ためた描き直しは離した後に走る")

                # R128 #3: Bot を選ぶと中央にその記録（本物の daemon の GET /api/session/activity の bot- の枝＝口の記録）が出る。
                # 誰が（Bot の 1 行・人の吹き出し）・いつ（日付つきの時刻）・何を（出勤・相談と選択肢・答えの札と選んだ番号）。
                # 選択肢の無い相談にセッション向けの 3 本を出さない・「ターミナルを前へ」を出さない・Bot の文は文字のまま。
                def live_bot_world():
                    for _ in range(40):
                        req = urllib.request.Request(f"http://127.0.0.1:{port}/api/office", headers={"X-Office-Local": "1"})
                        live = json.loads(urllib.request.urlopen(req, timeout=5).read())
                        rows = [e for e in live.get("employees", []) if e.get("session") == bot_session]
                        if rows:
                            break
                        time.sleep(0.25)
                    w = json.loads(json.dumps(world))
                    w["employees"] = [*w.get("employees", []), *rows]
                    w["roster"] = [*w["roster"], *[r for r in live.get("roster", []) if r.get("session") == bot_session]]
                    return w, rows[0]

                def wait_bot(pred, what, timeout=20.0):
                    """daemon の走査（SWR・キャッシュ 2 秒）が口のファイルの変化を拾うまで、取り直しては画面へ流す。"""
                    end = time.monotonic() + timeout
                    while True:
                        w, row = live_bot_world()
                        if pred(row):
                            page.evaluate("w => window.__office.inject(w)", w)
                            return row
                        assert time.monotonic() < end, f"/api/office に {what} が出ない: {row}"
                        time.sleep(0.25)

                wait_bot(lambda r: not r.get("ask"), "答えた後の Bot の行")
                page.wait_for_function("""() => [...document.querySelectorAll('#app-activity .act-said')]
                    .some(n => n.textContent === '相談への答え（選択肢 2）')""", timeout=15000)
                rec = page.evaluate("""() => { const a = document.querySelector('#app-activity');
                    return { events: [...a.querySelectorAll('.act-event')].map(n => n.textContent),
                             q: [...a.querySelectorAll('.act-question')].map(n => n.getAttribute('data-qtext')),
                             opts: [...a.querySelectorAll('.act-question .act-option-label')].map(n => n.textContent),
                             said: [...a.querySelectorAll('.act-said')].map(n => n.textContent),
                             times: [...a.querySelectorAll('.act-time')].map(n => n.textContent),
                             imgs: a.querySelectorAll('img, b, i').length, live: a.querySelectorAll('.act-live').length,
                             pwned: window.__r127_pwned === 1,
                             jump: document.querySelector('#app-jump').hidden,
                             delivery: document.querySelector('#app-delivery').textContent,
                             term: document.querySelector('#app-terminal-toggle').hidden }; }""")
                assert rec["events"] and rec["events"][0].startswith("出勤") and "依頼を読みました" in rec["events"][0], \
                    f"中央に Bot の出勤が出ない: {rec}"
                assert rec["q"] == ["請求書を送ってよい？" + poison] and rec["opts"] == ["送る<i>今</i>", "明日にする"], \
                    f"中央に Bot の相談と選択肢が文字のまま出ない: {rec}"
                assert rec["said"] == ["相談への答え（選択肢 2）"], f"答えの札（選んだ選択肢の番号）が出ない: {rec}"
                assert rec["times"] and "/" in rec["times"][0], f"記録の最初の行に日付つきの時刻が無い: {rec}"
                assert rec["imgs"] == 0 and not rec["pwned"] and rec["live"] == 0, f"Bot の文が HTML になった／考え中を出した: {rec}"
                assert rec["jump"] and rec["term"], f"Bot に「ターミナルを前へ」／ターミナルの切替を出した: {rec}"
                assert rec["delivery"] == "Bot が次に出勤したときに読みます", f"Bot への届き方の案内が違う: {rec}"
                print("  ✓ R128 Bot の記録: 中央に出勤・相談と選択肢・答えの札（選択肢 2）・日付つきの時刻・Bot の文は文字のまま・ターミナルへの移動なし")

                # 選択肢の無い相談: セッション向けの 3 本（承認して進める・いったん停止・進捗を報告）を出さない＝自由な文だけ
                asked2 = office_door.ask("mailbox", "棚の件はどうする？", home=home)
                assert asked2["ok"], asked2
                wait_bot(lambda r: (r.get("ask") or {}).get("askId") == asked2["askId"], "2 つ目の相談")
                page.wait_for_function("() => document.querySelectorAll('#app-answer button[data-answer]').length > 0", timeout=15000)
                labels = page.locator("#app-answer button[data-answer]").all_inner_texts()
                assert labels == ["自由に書く"], f"選択肢の無い Bot の相談にセッション向けの定型が出た: {labels}"
                assert page.locator("#app-input").get_attribute("placeholder") == "相談への答えを書く…"
                page.locator("#app-input").fill("棚は来週にしてください")
                page.locator("#app-input").press("Enter")
                got = None
                for _ in range(60):
                    got = office_door.inbox("mailbox", home=home)
                    if any(it.get("kind") == "answer" and it.get("askId") == asked2["askId"] for it in got.get("items", [])):
                        break
                    time.sleep(0.15)
                free = [it for it in got.get("items", []) if it.get("askId") == asked2["askId"]]
                assert free and free[0]["text"] == "棚は来週にしてください" and "choice" not in free[0], f"自由な文の答えが届かない: {got}"
                wait_bot(lambda r: not r.get("ask"), "2 つ目の相談が閉じた Bot の行")
                page.wait_for_function("""() => [...document.querySelectorAll('#app-activity .act-said')]
                    .map(n => n.textContent).join('|') === '相談への答え（選択肢 2）|相談への答え'""", timeout=15000)
                # 相談が閉じた後の言葉は指示（/api/instruct）＝記録に「指示」の札（未読がある Bot でも送れたら取り直す）
                page.locator("#app-input").fill("棚も見て")
                page.locator("#app-input").press("Enter")
                page.wait_for_function("""() => [...document.querySelectorAll('#app-activity .act-user')]
                    .some(n => n.textContent.includes('棚も見て') && n.textContent.includes('指示'))""", timeout=15000)
                assert any(it.get("kind") == "instruct" and it.get("text") == "棚も見て"
                           for it in office_door.inbox("mailbox", home=home)["items"]), "Mac の指示が Bot の inbox に無い"
                assert not list(approvals.glob("bot-*")), "Bot の答え・指示が承認の掲示板に置かれた（不変条件 7）"
                print("  ✓ R128 選択肢の無い Bot の相談は自由な文だけ（3 本を出さない）→ 答えの札・相談が閉じた後の言葉は「指示」の札")

                assert office_door.remove_bot("mailbox", home=home)["ok"]
                page.evaluate("w => window.__office.inject(w)", world)
                check_bot_panel(page, home, port, world, errors)
                check_today_card(page)

                assert page.locator("#app-room canvas").count() == 1, "live の操作中に畳んだ小窓の canvas が増えた"
                page.evaluate("window.__appSmokeCanvas = [...document.querySelectorAll('#app-room > .app-room-layer canvas')].find(c => c.getClientRects().length)")
                page.locator("#app-room-3d").click(timeout=60000)
                page.wait_for_function("() => localStorage.getItem('aioffice.app.room') === '3d'", timeout=60000)
                page.wait_for_function("window.__appSmokeCanvas !== [...document.querySelectorAll('#app-room > .app-room-layer canvas')].find(c => c.getClientRects().length)",
                                       timeout=60000)
                assert room_mode(page) == "3d", "3D の部屋へ切り替わらない"
                page.locator("#app-room-pixel").click(timeout=60000)
                page.wait_for_function("() => localStorage.getItem('aioffice.app.room') === 'pixel'", timeout=60000)
                assert room_mode(page) == "pixel", "ドット絵の部屋へ戻らない"
                print("  ✓ 保存したドット絵の部屋を読み込み・3D へ canvas を交換して保存・ドット絵へ戻して保存")
                for suffix, label in (("&t=3.2", "固定時刻"), ("&demo=1", "デモ")):
                    protected_page = context.new_page()
                    # デモも静止させて SwiftShader の連続描画による操作待ちを避ける。
                    protected_page.emulate_media(reduced_motion="reduce")
                    protected_calls = []
                    configure(protected_page, world, errors, protected_calls)
                    guard_room_storage(protected_page)
                    protected_page.goto(url + suffix)
                    protected_page.wait_for_function("window.__office && window.__office.ready", timeout=30000)
                    assert room_mode(protected_page) == "3d", f"{label}で保存済みの部屋を読み込んだ"
                    assert protected_page.locator(".app-room-pip").is_visible(), f"{label}で保存済みの小窓の畳みを読み込んだ"
                    protected_page.locator(".app-room-pip-swap").click()
                    assert room_mode(protected_page) == "pixel", f"{label}でドット絵へ切り替わらない"
                    protected_page.locator(".app-room-pip-hide").click()
                    protected_page.locator(".app-room-pip-show").click()
                    protected_page.locator("#app-room-3d").click()
                    assert_room_storage_untouched(protected_page, label)
                    assert not protected_calls, f"{label}で activity を取得した"
                    if label == "デモ":
                        assert protected_page.locator("#app-today").is_hidden(), "デモに「今日の 1 枚」が出た（デモには記録が無い）"
                    protected_page.close()
                print("  ✓ 固定時刻とデモは部屋・小窓の保存を読み書きしない・切替/交換/隠す/戻す・activity の取得 0")
                browser.close()
            assert not errors, "ブラウザのエラー: " + " | ".join(errors[:8])
            print("  ✓ console.error / pageerror 0")
        finally:
            proc.terminate()
            proc.wait(timeout=5)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (AssertionError, RuntimeError) as exc:
        print(f"  ✗ app スモーク: {exc}")
        raise SystemExit(1)
