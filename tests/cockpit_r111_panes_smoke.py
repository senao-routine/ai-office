#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""R111-B4: 端末のペイン（03 端末の窓を最大 4 つに分ける）の門・偽の端末で速く。

本人の依頼（2026-10-07）「端末を 2 つに分割して開ける・境目をドラッグしたら広がる」を、B2 の配線（ui/cockpit/panes.js・
term.js・index.js・ui/platform/api.js の termStreams・ui/core/termpanes.js）の**外から**機械に見させる
（計画 scalable-cooking-bentley の B4・決めたこと 5〜8・リスク ④⑦⑨⑪⑫⑬）。

起動と偽の端末は tests/cockpit_r110_ui_smoke.py を流用する（固定時刻の open_page＋term_routes はそのまま使う）。live の
ページは偽の端末を 4 本（MAX_TERMS）一覧に置く上書きの route を自分で持つ（r110 のファイルは触らない）:
/api/term/list＝4 本・/api/term/streams＝ids ごとに hello（from を受けたら reset しない続き）＋out（`<id> <b64>`）・
/api/term/input の POST は数えて中身を控える・ほかの端末の POST は {ok:true}。/api/office は固定ワールドを返す
（live のポーリングで world が差し替わらない）。ページは live（`?ui=cockpit&life=0`）の 1920×1080（左右に分けられる）と
1440×900（既定の端末の列 574px＝左右は入らない）・dark。

検査:
  1 既定: #cp-xterm[data-panes="1"]・.cppane-head 0・.cppane.sel 0 で、どの .cppane も outline 無し（none か幅 0）・
    streams() ≤ 1。1920・1440・固定時刻（?t=3.2・r110 の open_page）の 3 つで。
  2 キー（1920）: JIS の ¥（code IntlYen）＋⌘ で左右に分かれる・⌘\ → 葉 2・見出し 2・選んでいる葉が新しい葉で .empty／
    ⌘⇧\ → 3（上下）／⌘⇧↑←→ で選んでいる葉が「ui/core/termpanes.js の neighbor（描いた矩形の幾何）」の答えへ／
    ⌘⌥X → 閉じる／⌘⌥\（⌘⇧\ の別名）→ 上下。
  3 打鍵中のショートカット: xterm に「echo x」を打った後（POST が届いたのを確かめてから）⌘\ → 分割は起き、
    /api/term/input の POST は 1 件も増えない（打鍵として pty へ流れない）。
  4 タブは選んでいる葉へ: 2 ペインで cpFocusPane(b) → タブ（term:<B>）をクリック → b の sel だけ替わり a は不変・
    B の xterm は b の中・streams()==1・subs()＝見えている 2 本。
  5 同じ端末を 2 葉に出さない: a の端末（sess-cp-09＝A）のタブを b で選ぶ → A は b へ移り a は空・葉の term が重複しない・
    .cpxt[data-term=A] は 1 つ。
  6 上限: 4 葉（2×2・4 本の端末）で streams()==1・subs() 4 本 → cpSplit も ⌘\ も無変化（同じ木・エラー 0）・
    ＋端末のメニューの「右に／下に分割」は押せない。閉じる（⌘⌥X・見出しの ✕・cpClosePane）→ 兄弟が親の矩形を引き継ぐ。
    最後の 1 つは ✕（見出し）が無く ⌘⌥X も無変化。
  7 境目のドラッグ（本物の pointer・.cpsplit-bar[data-dir=h]）: ratio が変わる・両側の termDims(id).cols ≥ 40・
    fontSize === 14（設定の base＝分けている間は 80 桁の縮小をしない）・左端まで引いても 40 桁で止まる・Esc で取り消し・
    cpSetRatio(split, 0.05) → 木は 0.1 に clamp・描く位置は 40 桁の下限で止まる。
  8 入らない分割（1440×900）: ⌘\ → 分けずに #toast に cp_pane_no_room_h の文言・cpSplitTo(0, 386)（部屋を ×2＝端末の列を
    広げる）の後は分けられる・cpResetLayout() で配置もペインも既定へ。
  9 保存と復元: 2 ペインで再読み込み → 葉 2・葉の sel 一致・streams()==1。固定時刻（?t=3.2）のページは鍵を読まない
    （1 枚・鍵はそのまま）。cpResetPanes()・cpResetLayout() で 1 枚に戻り鍵 aioffice.cockpit.panes が消える。
    壊れた値（garbage・{"v":0}・葉 5）→ 1 枚・エラー 0・鍵は消える。
  10 会話ビュー: 端末の無いセッション（sess-cp-12）を選んでいる葉に入れる → その葉の body に #cp-work・#cp-dlg・
    他の葉の端末（termText に自分の印・subs に在る）は不変・端末の葉を選び直すと会話の組は #cp-dlg-park へ戻る。
  11 段と狭さ: 2 ペインのまま 1280×800（2 カラム）→ 見えている葉 1・streams()==1・subs() 1 本・1920 へ戻すと 2。
    ?demo=1（鍵が在っても）→ .cppane 0・⌘\ 無効・/api/term/* 0 回。
  12 console.error / pageerror / window の error（ResizeObserver loop を含む）= 0（全体を通して）。

使い方: python3 tests/cockpit_r111_panes_smoke.py   （verify.sh ▶7 の run_ui から呼ぶ・Playwright 必要）
"""
import base64
import json
import pathlib
import sys
import time
from urllib.parse import parse_qs, urlparse

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "tests"))
from ui_shot import SWIFTSHADER, free_port, start_server  # noqa: E402
from cockpit_r110_ui_smoke import open_page  # noqa: E402  （固定時刻のページ＋r110 の偽の端末 term_routes）

FIXTURES = ROOT / "tests" / "fixtures" / "world"
WIDE = {"width": 1920, "height": 1080}       # 本人の画面（部屋 ×4・端末の列 ≈ 800px＝左右に 40 桁×2 が入る）
NARROW = {"width": 1440, "height": 900}      # golden の形（部屋 ×3・端末の列 574px＝左右は入らない）
TWO_COL = {"width": 1280, "height": 800}     # 2 カラム（分割は選んでいる葉だけ見せる）
PANES_KEY = "aioffice.cockpit.panes"
LAYOUT_KEY = "aioffice.cockpit.layout"
MIN_COLS = 40                                # ペインの下限（panes.js の MIN_COLS）
BASE_FONT = 14                               # ⚙「文字の大きさ」の既定（term.js の FONT_DEFAULT）
ROOM_X2_LEFT = 184 * 2 + 18                  # 部屋 ×2 の左の列の幅（--cp-left-w = 184k + 18）

# 偽の端末 4 本（MAX_TERMS）。A は固定ワールドで最初に選ばれる ❗ の人（sess-cp-09）の claude・ほかは台帳に無い端末（タブは term:<id>）
TA, TB, TC, TD = "a1a1a1a1a1a1a1a1", "b2b2b2b2b2b2b2b2", "c3c3c3c3c3c3c3c3", "d4d4d4d4d4d4d4d4"
TERMS = [
    {"id": TA, "kind": "claude", "session": "sess-cp-09", "projectId": "2ffc7d9c6254"},
    {"id": TB, "kind": "shell", "session": None, "projectId": None},
    {"id": TC, "kind": "shell", "session": None, "projectId": None},
    {"id": TD, "kind": "codex", "session": None, "projectId": None},
]
for _i, _t in enumerate(TERMS):
    _t.update(cols=100, rows=30, alive=True, exitCode=None, startedAt=1790199000 + _i, viewers=0)
MARK = {t["id"]: f"r111 pane fake {t['id'][:2]}" for t in TERMS}   # 端末ごとの印（termText で自分の印だけを見る）
OUT = {tid: (m + "\r\n$ ").encode() for tid, m in MARK.items()}
SESSION_A = "sess-cp-09"                     # A の端末を持つセッション（タブの data-session）
SESSION_TALK = "sess-cp-12"                  # 端末の無いセッション（会話ビュー）
DEAD = "deadbeefdeadbeef"                    # 一覧に無い端末 id

# ── ページの中で読む ────────────────────────────────────────────────
STATE = r"""() => {
  const d = window.__office.debug;
  const root = document.querySelector('#cp-xterm');
  const p = d.panes();
  const panes = [...document.querySelectorAll('#cp-xterm > .cppane')].map((e) => {
    const cs = getComputedStyle(e);
    return { id: e.dataset.pane, hidden: e.hidden, body: e.querySelector(':scope > .cppane-body')?.className || '',
      outline: cs.outlineStyle, outlineW: parseFloat(cs.outlineWidth) || 0,
      x: e.offsetLeft, y: e.offsetTop, w: e.offsetWidth, h: e.offsetHeight,
      terms: [...e.querySelectorAll('.cpxt[data-term]')].filter((x) => !x.hidden).map((x) => x.dataset.term) };
  });
  return { p, attr: root ? (root.dataset.panes ?? null) : null, rootHidden: root ? root.hidden : null,
    heads: document.querySelectorAll('#cp-xterm .cppane-head').length,
    // 見えている見出し（畳んで隠した葉＝.cppane[hidden] の中に残った見出しは数えない＝画面に出ない）
    vheads: document.querySelectorAll('#cp-xterm > .cppane:not([hidden]) > .cppane-head').length,
    sels: document.querySelectorAll('#cp-xterm .cppane.sel').length,
    closes: [...document.querySelectorAll('#cp-xterm .cppane-head .cppane-close')].filter((b) => !b.hidden).length,
    panes, cpxt: [...document.querySelectorAll('.cpxt[data-term]')].map((x) => x.dataset.term),
    ghosts: document.querySelectorAll('.cpsplit-ghost').length };
}"""

# 描いた矩形（.cppane の left/top/width/height＝panes.js が layout() の rects を書いたもの）から neighbor の答えを出す
NEIGHBOR = r"""async ([leafId, dir]) => {
  const tp = await import('/ui/core/termpanes.js');
  const rects = new Map();
  for (const e of document.querySelectorAll('#cp-xterm > .cppane:not([hidden])')) {
    rects.set(e.dataset.pane, { x: parseFloat(e.style.left), y: parseFloat(e.style.top),
      w: parseFloat(e.style.width), h: parseFloat(e.style.height) });
  }
  return tp.neighbor({ rects }, leafId, dir);
}"""

ERRS = "() => (window.__r111errs || []).slice()"
TERMINAL_PREF = "try { localStorage.setItem('aioffice.cockpit.center', 'terminal'); } catch (e) {}"
INIT = r"""(() => {
  window.__r111errs = [];
  window.addEventListener('error', (e) => { window.__r111errs.push(String(e.message || e.error || 'error')); });
  window.addEventListener('unhandledrejection', (e) => { window.__r111errs.push('unhandledrejection: ' + String(e.reason)); });
})();"""


def fake_routes(ctx, world, posts, asked):
    """live のページの偽の API（端末 4 本・会話・office）。posts＝/api/term/input の (id, bytes)・asked＝端末 API の要求の控え"""
    office = json.dumps(world, ensure_ascii=False)
    term_list = json.dumps({"terms": TERMS})
    by_id = {t["id"]: t for t in TERMS}
    js = "application/json; charset=utf-8"

    def sse(*events):
        return "".join(f"event: {k}\ndata: {v}\n\n" for k, v in events)

    def handler(route):
        req = route.request
        u = urlparse(req.url)
        path = u.path
        if path.startswith("/api/term/"):
            asked.append((req.method, path + ("?" + u.query if u.query else "")))
        if path == "/api/office":
            return route.fulfill(status=200, content_type=js, body=office)
        if path == "/api/session/activity":
            return route.fulfill(status=200, content_type=js, body=json.dumps(
                {"ok": True, "rows": [], "cur": None, "beg": None, "top": True, "gap": False, "reset": False}))
        if path == "/api/session/dialog":
            return route.fulfill(status=200, content_type=js, body=json.dumps(
                {"ok": True, "messages": [], "depth": 0, "maxDepth": 0, "hasMore": False, "total": 0}))
        if not path.startswith("/api/term/"):
            return route.continue_()
        if req.method != "GET":
            if path == "/api/term/input":
                try:
                    b = json.loads(req.post_data or "{}")
                    posts.append((b.get("id"), base64.b64decode(b.get("data") or "")))
                except Exception:  # noqa: BLE001 — 形の崩れた POST も 1 件に数える
                    posts.append((None, b""))
            return route.fulfill(status=200, content_type=js, body='{"ok":true}')
        if path == "/api/term/list":
            return route.fulfill(status=200, content_type=js, body=term_list)
        if path == "/api/term/streams":
            qs = parse_qs(u.query)
            ids = [x for x in (qs.get("ids", [""])[0]).split(",") if x]
            at = {}
            for pair in (qs.get("from", [""])[0]).split(","):
                if ":" in pair:
                    k, v = pair.split(":", 1)
                    if v.isdigit():
                        at[k] = int(v)
            events = []
            for tid in ids:
                info = by_id.get(tid)
                if not info:
                    events.append(("exit", json.dumps({"id": tid, "code": None})))
                    continue
                n = at.get(tid)
                if n is not None and n >= len(OUT[tid]):
                    # 読み終えたところから＝続きだけ（reset しない）。偽の端末に続きは無い＝hello だけ
                    events.append(("hello", json.dumps({**info, "from": n, "reset": False}, separators=(",", ":"))))
                else:
                    events.append(("hello", json.dumps({**info, "from": 0, "reset": True}, separators=(",", ":"))))
                    events.append(("out", f"{tid} {base64.b64encode(OUT[tid]).decode()}"))
            return route.fulfill(status=200, content_type="text/event-stream", body=sse(*events))
        if path == "/api/term/stream":                 # 単一の購読（もう呼ばれないはず＝呼ばれたら asked に残る）
            return route.fulfill(status=404, content_type=js, body='{"error":"single stream is not used"}')
        return route.fulfill(status=404, content_type=js, body='{"error":"unknown"}')

    ctx.route(lambda url: urlparse(url).path.startswith("/api/"), handler)


def main():
    from playwright.sync_api import sync_playwright

    world = json.loads((FIXTURES / "cockpit.json").read_text(encoding="utf-8"))
    ng, errors, notes = 0, [], []
    t_start = time.monotonic()

    def check(ok, label, detail=""):
        nonlocal ng
        print(f"  {'✓' if ok else '✗'} {label}{'' if ok else f' — {detail}'}")
        if not ok:
            ng += 1

    def watch(page, label):
        page.on("pageerror", lambda e: errors.append(f"pageerror({label}): {e}"))
        page.on("console", lambda m: errors.append(
            f"console.error({label}): {m.text}（{(m.location or {}).get('url', '')}）") if m.type == "error" else None)

    def win_errors(page, label):
        try:
            for e in page.evaluate(ERRS):
                errors.append(f"window.error({label}): {e}")
        except Exception:  # noqa: BLE001 — 閉じたページ
            pass

    def wait_js(page, expr, arg=None, timeout=6000):
        try:
            page.wait_for_function(expr, arg=arg, timeout=timeout)
            return True
        except Exception:  # noqa: BLE001 — 待ちの失敗は呼び手が検査の赤にする
            return False

    def state(page):
        return page.evaluate(STATE)

    def frame(page, n=2):
        page.evaluate(f"() => new Promise((r) => {{ let k = {n}; const f = () => (--k <= 0 ? r() : requestAnimationFrame(f)); requestAnimationFrame(f); }})")

    def boot(page, url):
        page.goto(url, wait_until="domcontentloaded")
        page.wait_for_function("() => window.__office && window.__office.inject", timeout=30000)
        page.evaluate("w => window.__office.inject(w)", world)
        page.wait_for_selector(".cproom", timeout=20000)

    def wait_count(page, n, timeout=4000):
        return wait_js(page, "n => window.__office.debug.panes().count === n", n, timeout)

    def wait_shown_term(page, tid, timeout=6000):
        """単葉は［ターミナル］へ切り替えてから、xterm と桁を待つ。分割時の動きは従来どおり。"""
        wait_js(page, """id => document.querySelector(`#cp-xterm .cpxt[data-term="${id}"]:not([hidden])`)
            || !document.querySelector('#cp-activity-toggle').hidden""", tid, timeout)
        if not page.evaluate("() => document.querySelector('#cp-activity-toggle').hidden"):
            if not page.evaluate("() => document.querySelector('#cp-term').classList.contains('live')"):
                page.click("#cp-activity-toggle")
        return wait_js(page, """id => { const e = document.querySelector(`#cp-xterm .cppane .cpxt[data-term="${id}"]:not([hidden])`);
            return !!e && !!e.querySelector('.xterm-rows') && !!window.__office.debug.termDims(id); }""", tid, 6000)

    def leaf(st, lid):
        return next((x for x in st["p"]["leaves"] if x["id"] == lid), None)

    def blur(page):
        page.evaluate("() => document.activeElement?.blur?.()")

    def press(page, key):
        page.keyboard.press(key)
        frame(page)

    def click_tab(page, session):
        page.locator(f'#cp-tabs .cptab[data-session="{session}"]').click(timeout=5000)
        frame(page)

    def toast_has(page, key, timeout=2500):
        return wait_js(page, """async (k) => { const s = await import('/ui/hud/strings.js');
            return (document.querySelector('#toast')?.textContent || '').includes(s.T(k)); }""", key, timeout)

    def stored(page):
        return page.evaluate(f"() => localStorage.getItem('{PANES_KEY}')")

    def default_ok(page, tag, no="1"):
        """1 既定の形（1 ペイン・見出しも枠も無し）"""
        st = state(page)
        outlined = [x["id"] for x in st["panes"] if x["outline"] != "none" and x["outlineW"] > 0]
        ok = (st["attr"] == "1" and st["heads"] == 0 and st["sels"] == 0 and not outlined
              and st["p"]["count"] == 1 and st["p"]["streams"] <= 1 and len(st["panes"]) == 1)
        check(ok, f"{no} {tag}: 既定は 1 ペイン（data-panes=1・.cppane 1・見出し 0・選択の枠 0・outline 無し・streams ≤1）",
              json.dumps({"attr": st["attr"], "heads": st["heads"], "sels": st["sels"], "outlined": outlined,
                          "count": st["p"]["count"], "streams": st["p"]["streams"], "panes": len(st["panes"])},
                         ensure_ascii=False))

    port = free_port()
    proc = start_server(port)
    live_url = f"http://127.0.0.1:{port}/?ui=cockpit&life=0"
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(args=SWIFTSHADER)

            # ── 1（固定時刻）: r110 の open_page（?t=3.2&seed=11・偽の端末 1 本）＝golden と同じ形に見出しも枠も無い ──
            ctx0, page0 = open_page(browser, port, "dark", world, errors, "t=3.2")
            try:
                page0.set_viewport_size(NARROW)
                wait_js(page0, "() => !!document.querySelector('#cp-xterm .xterm-rows')", None, 8000)
                frame(page0, 3)
                default_ok(page0, "固定時刻 ?t=3.2 1440×900")
            finally:
                ctx0.close()

            # ── 1920×1080 の live ───────────────────────────────────────
            posts, asked = [], []
            ctx = browser.new_context(viewport=WIDE, device_scale_factor=1)
            ctx.add_init_script(INIT)
            ctx.add_init_script(TERMINAL_PREF)        # R121: この節は本物の端末のペインを測る＝「ターミナル派」の保存で開く
            fake_routes(ctx, world, posts, asked)
            page = ctx.new_page()
            watch(page, "1920")
            boot(page, live_url)
            got = wait_shown_term(page, TA)
            check(got, "前提: 偽の端末 A（sess-cp-09 の claude）が選ばれた葉に描かれる", json.dumps(state(page))[:400])
            frame(page, 3)
            default_ok(page, "live 1920×1080")

            # 2 キー（JIS の ¥ → ⌘\ → ⌘⇧\ → ⌘⇧↑←→ → ⌘⌥X → ⌘⌥\）
            blur(page)                        # 入力欄の外（body）で押す
            jis = "press"
            try:
                page.keyboard.press("Meta+IntlYen")
            except Exception:  # noqa: BLE001 — Playwright の US 配列に IntlYen が無い＝同じ code の keydown を投げる
                jis = "dispatch"
                page.evaluate("""() => document.body.dispatchEvent(new KeyboardEvent('keydown',
                    { code: 'IntlYen', key: '¥', metaKey: true, bubbles: true, cancelable: true }))""")
            frame(page)
            st = state(page)
            check(st["p"]["count"] == 2 and st["p"]["tree"]["root"].get("dir") == "h",
                  f"2 JIS の ¥（code IntlYen・{jis}）＋⌘ で左右に分かれる", json.dumps(st["p"]["tree"]))
            press(page, "Meta+Alt+KeyX")
            check(wait_count(page, 1), "2 ⌘⌥X で閉じて 1 枚へ", json.dumps(state(page)["p"]["tree"]))

            press(page, "Meta+Backslash")
            wait_count(page, 2)
            st = state(page)
            new = st["p"]["focused"]
            nb = next((x for x in st["panes"] if x["id"] == new), {})
            check(st["p"]["count"] == 2 and st["heads"] == 2 and st["attr"] == "2" and new != "p1"
                  and leaf(st, new) and leaf(st, new)["sel"] is None and "empty" in nb.get("body", "")
                  and st["sels"] == 1 and nb.get("outline") not in (None, "none") and nb.get("outlineW", 0) > 0,
                  "2 ⌘\\ → 葉 2・見出し 2・選んでいる葉は新しい空の葉（.empty・選択の枠 1）",
                  json.dumps({k: st[k] for k in ("attr", "heads", "sels")} | {"p": st["p"]["tree"], "focus": new,
                             "body": nb.get("body"), "outline": [nb.get("outline"), nb.get("outlineW")]}))
            press(page, "Meta+Shift+Backslash")
            wait_count(page, 3)
            st = state(page)
            p3 = st["p"]["focused"]
            vsplit = st["p"]["tree"]["root"].get("b", {})
            check(st["p"]["count"] == 3 and vsplit.get("dir") == "v" and p3 not in ("p1", new) and st["heads"] == 3,
                  "2 ⌘⇧\\ → 3（右の葉を上下に）", json.dumps(st["p"]["tree"]))
            moves = []
            cur = p3
            for key, d in (("Meta+Shift+ArrowUp", "up"), ("Meta+Shift+ArrowDown", "down"),
                           ("Meta+Shift+ArrowLeft", "left"), ("Meta+Shift+ArrowRight", "right")):
                want = page.evaluate(NEIGHBOR, [cur, d])
                press(page, key)
                got_focus = page.evaluate("() => window.__office.debug.panes().focused")
                moves.append((key, cur, want, got_focus))
                cur = got_focus
            check(all(w and w == g for _, _, w, g in moves) and moves[0][2] == new and moves[1][2] == p3
                  and moves[2][2] == "p1",
                  "2 ⌘⇧↑←→ で選んでいる葉が幾何の隣（termpanes.neighbor）へ",
                  json.dumps(moves))
            page.evaluate(f"() => window.__office.debug.cpFocusPane('{p3}')")
            press(page, "Meta+Alt+KeyX")
            st = state(page)
            check(wait_count(page, 2) and not leaf(state(page), p3), "2 ⌘⌥X → 選んでいる葉を閉じて 2",
                  json.dumps(st["p"]["tree"]))
            press(page, "Meta+Alt+Backslash")
            st = state(page)
            check(st["p"]["count"] == 3 and '"dir": "v"' in json.dumps(st["p"]["tree"]),
                  "2 ⌘⌥\\（⌘⇧\\ の別名）→ 上下に分かれる", json.dumps(st["p"]["tree"]))
            press(page, "Meta+Alt+KeyX")
            press(page, "Meta+Alt+KeyX")
            check(wait_count(page, 1), "2 ⌘⌥X を 2 回で 1 枚へ戻る", json.dumps(state(page)["p"]["tree"]))

            # 3 打鍵中のショートカット（xterm の中で ⌘\ を押しても pty へ何も流れない・分割は起きる）
            wait_shown_term(page, TA)
            page.locator(f'#cp-xterm .cpxt[data-term="{TA}"] .xterm').click()
            page.keyboard.type("echo x")
            t_end = time.monotonic() + 3
            while time.monotonic() < t_end and b"".join(b for _, b in posts).count(b"echo x") < 1:
                page.wait_for_timeout(50)
            typed = b"echo x" in b"".join(b for _, b in posts)
            page.wait_for_timeout(250)
            in_xterm = page.evaluate("() => !!document.activeElement?.classList.contains('xterm-helper-textarea')")
            n0 = len(posts)
            page.keyboard.press("Meta+Backslash")
            split_ok = wait_count(page, 2)
            page.wait_for_timeout(400)
            extra = posts[n0:]
            check(typed and in_xterm and split_ok and not extra,
                  "3 xterm に打った直後の ⌘\\ は分割になり /api/term/input の POST は増えない",
                  f"打鍵の POST 届いた={typed}・押した時の焦点が xterm={in_xterm}・分割={split_ok}・"
                  f"増えた POST {len(extra)} 件 {extra[:3]}")

            # 4 タブは選んでいる葉へ
            st = state(page)
            a_id = "p1"
            b_id = st["p"]["focused"]
            page.evaluate(f"() => window.__office.debug.cpFocusPane('{b_id}')")
            click_tab(page, f"term:{TB}")
            got_b = wait_shown_term(page, TB)
            st = state(page)
            bp = next((x for x in st["panes"] if x["id"] == b_id), {})
            check(got_b and leaf(st, b_id)["sel"] == f"term:{TB}" and leaf(st, a_id)["sel"] == SESSION_A
                  and TB in bp.get("terms", []) and st["p"]["streams"] == 1
                  and sorted(st["p"]["subs"]) == sorted([TA, TB]),
                  "4 タブは選んでいる葉へ入る（a は不変・B は b の中・streams 1・subs＝見えている 2 本）",
                  json.dumps({"leaves": st["p"]["leaves"], "streams": st["p"]["streams"], "subs": st["p"]["subs"]}))

            # 5 同じ端末を 2 葉に出さない（a の A を b で選ぶ＝移動）
            click_tab(page, SESSION_A)
            wait_js(page, "([b, s]) => window.__office.debug.panes().leaves.find((l) => l.id === b)?.sel === s",
                    [b_id, SESSION_A], 3000)
            frame(page, 3)
            st = state(page)
            terms = [x["term"] for x in st["p"]["leaves"] if x["term"]]
            bp = next((x for x in st["panes"] if x["id"] == b_id), {})
            check(leaf(st, b_id)["sel"] == SESSION_A and leaf(st, a_id)["sel"] is None and len(terms) == len(set(terms))
                  and st["cpxt"].count(TA) == 1 and TA in bp.get("terms", []),
                  "5 同じ端末は 1 葉だけ（別の葉で選ぶと移動・a は空・.cpxt[data-term=A] は 1 つ）",
                  json.dumps({"leaves": st["p"]["leaves"], "cpxt": st["cpxt"]}))
            page.evaluate(f"() => window.__office.debug.cpFocusPane('{a_id}')")
            click_tab(page, f"term:{TB}")
            wait_shown_term(page, TB)

            # 6 上限（2×2 に 4 本）と閉じる
            d = "window.__office.debug"
            page.evaluate(f"() => {d}.cpSplit('{a_id}', 'v')")
            a2 = page.evaluate(f"() => {d}.panes().focused")
            page.evaluate(f"() => {d}.cpAssign('{a2}', 'term:{TC}')")
            page.evaluate(f"() => {d}.cpSplit('{b_id}', 'v')")
            b2 = page.evaluate(f"() => {d}.panes().focused")
            page.evaluate(f"() => {d}.cpAssign('{b2}', 'term:{TD}')")
            four = all(wait_shown_term(page, t) for t in (TA, TB, TC, TD))
            wait_js(page, "() => window.__office.debug.panes().subs.length === 4", None, 3000)
            st = state(page)
            check(four and st["p"]["count"] == 4 and st["attr"] == "4" and st["p"]["streams"] == 1
                  and sorted(st["p"]["subs"]) == sorted([TA, TB, TC, TD]),
                  "6 2×2 に 4 本: 見えている 4 葉・購読は接続 1 本（streams 1）で 4 本（subs）",
                  json.dumps({"attr": st["attr"], "count": st["p"]["count"], "streams": st["p"]["streams"],
                              "subs": st["p"]["subs"]}))
            before = json.dumps(st["p"]["tree"], sort_keys=True)
            n_err = len(errors)
            r1 = page.evaluate(f"() => {d}.cpSplit('{a_id}', 'h')")
            r2 = page.evaluate(f"() => {d}.cpSplit('{b2}', 'v')")
            press(page, "Meta+Backslash")
            press(page, "Meta+Shift+Backslash")
            after = json.dumps(state(page)["p"]["tree"], sort_keys=True)
            page.click("#cp-term-new")
            menu = page.evaluate("""() => ['#cp-pane-split-h', '#cp-pane-split-v'].map((s) => {
                const b = document.querySelector(s); return { hidden: b.hidden, disabled: b.disabled }; })""")
            page.keyboard.press("Escape")
            check(r1 is False and r2 is False and before == after and len(errors) == n_err
                  and all(m["disabled"] and not m["hidden"] for m in menu),
                  "6 5 枚目の分割は無変化（cpSplit も ⌘\\ も同じ木・エラー 0・メニューの分割は押せない）",
                  json.dumps({"cpSplit": [r1, r2], "same": before == after, "menu": menu}))
            # 閉じる＝兄弟が親の矩形を引き継ぐ（⌘⌥X・見出しの ✕・cpClosePane）
            sib_before = next(x for x in state(page)["panes"] if x["id"] == b_id)
            page.evaluate(f"() => {d}.cpFocusPane('{b2}')")
            press(page, "Meta+Alt+KeyX")
            wait_count(page, 3)
            frame(page, 2)
            st = state(page)
            sib = next((x for x in st["panes"] if x["id"] == b_id), {})
            check(st["p"]["count"] == 3 and not leaf(st, b2) and sib.get("h", 0) > sib_before["h"] + 40
                  and sib.get("y") == min(x["y"] for x in st["panes"] if not x["hidden"]),
                  "6 ⌘⌥X で閉じると兄弟が親の矩形を引き継ぐ（合流）",
                  f"前 {sib_before['y']}+{sib_before['h']} → 後 {sib.get('y')}+{sib.get('h')}")
            page.locator(f'#cp-xterm .cppane[data-pane="{a2}"] .cppane-close').click()
            ok_x = wait_count(page, 2)
            page.evaluate(f"() => {d}.cpClosePane('{b_id}')")
            ok_last = wait_count(page, 1)
            frame(page, 2)
            st = state(page)
            before1 = json.dumps(st["p"]["tree"], sort_keys=True)
            press(page, "Meta+Alt+KeyX")
            r3 = page.evaluate(f"() => {d}.cpClosePane()")
            st2 = state(page)
            check(ok_x and ok_last and st2["p"]["count"] == 1 and st2["closes"] == 0 and st2["heads"] == 0
                  and json.dumps(st2["p"]["tree"], sort_keys=True) == before1 and r3 is False,
                  "6 見出しの ✕・cpClosePane で 1 枚へ・最後の 1 つは ✕ が無く ⌘⌥X も無変化",
                  json.dumps({"✕": ok_x, "last": ok_last, "count": st2["p"]["count"], "closes": st2["closes"],
                              "r3": r3}))

            # 7 境目のドラッグ（本物の pointer）
            last = st2["p"]["leaves"][0]["id"]
            page.evaluate(f"() => {d}.cpAssign('{last}', '{SESSION_A}')")
            wait_shown_term(page, TA)
            blur(page)
            press(page, "Meta+Backslash")
            wait_count(page, 2)
            right = page.evaluate(f"() => {d}.panes().focused")
            page.evaluate(f"() => {d}.cpAssign('{right}', 'term:{TB}')")
            wait_shown_term(page, TB)
            frame(page, 3)

            def pair_dims():
                return page.evaluate(f"() => [{d}.termDims('{TA}'), {d}.termDims('{TB}')]")

            def ratio():
                return page.evaluate(f"() => {d}.panes().tree.root.ratio")

            def wait_dims(pred_js, timeout=3000):
                return wait_js(page, f"""() => {{ const a = {d}.termDims('{TA}'), b = {d}.termDims('{TB}');
                    return !!a && !!b && ({pred_js}); }}""", None, timeout)

            def bar_box():
                return page.locator('#cp-xterm .cpsplit-bar[data-dir="h"]').bounding_box()

            r0 = ratio()
            dims0 = pair_dims()
            box = bar_box()
            cx, cy = box["x"] + box["width"] / 2, box["y"] + box["height"] / 2
            page.mouse.move(cx, cy)
            page.mouse.down()
            for i in range(1, 7):
                page.mouse.move(cx + 15 * i, cy)
            page.mouse.up()
            frame(page, 2)
            r1 = ratio()
            moved = wait_dims(f"a.cols !== {dims0[0]['cols'] if dims0[0] else -1}")
            dims1 = pair_dims()
            check(box and r1 != r0 and r1 > r0 and moved and all(x and x["cols"] >= MIN_COLS for x in dims1)
                  and all(x["fontSize"] == BASE_FONT for x in dims1),
                  f"7 境目を右へ 90px ドラッグ: ratio が変わり・両側 ≥{MIN_COLS} 桁・字は base {BASE_FONT}",
                  json.dumps({"ratio": [r0, r1], "dims": [dims0, dims1]}))
            box = bar_box()
            cx, cy = box["x"] + box["width"] / 2, box["y"] + box["height"] / 2
            page.mouse.move(cx, cy)
            page.mouse.down()
            for i in range(1, 7):
                page.mouse.move(max(2, cx - 150 * i), cy)
            page.mouse.up()
            frame(page, 2)
            wait_dims(f"a.cols <= {MIN_COLS + 2}", 2500)
            r2 = ratio()
            dims2 = pair_dims()
            check(r2 < r1 and all(x and x["cols"] >= MIN_COLS for x in dims2) and dims2[0]["cols"] <= MIN_COLS + 2
                  and all(x["fontSize"] == BASE_FONT for x in dims2),
                  f"7 左端まで引いても {MIN_COLS} 桁で止まる（字は小さくしない）",
                  json.dumps({"ratio": r2, "dims": dims2}))
            box = bar_box()
            cx, cy = box["x"] + box["width"] / 2, box["y"] + box["height"] / 2
            page.mouse.move(cx, cy)
            page.mouse.down()
            for i in range(1, 5):
                page.mouse.move(cx + 40 * i, cy)
            ghost_on = state(page)["ghosts"]
            page.keyboard.press("Escape")
            page.mouse.up()
            frame(page, 2)
            r3 = ratio()
            check(ghost_on == 1 and r3 == r2 and state(page)["ghosts"] == 0,
                  "7 Esc でドラッグを取り消す（ゴーストが消え ratio は元のまま）",
                  json.dumps({"ghost": ghost_on, "ratio": [r2, r3]}))
            split_id = page.evaluate(f"() => {d}.panes().tree.root.id")
            page.evaluate(f"() => {d}.cpSetRatio('{split_id}', 0.05)")
            frame(page, 3)
            wait_dims("true")
            r4 = ratio()
            dims4 = pair_dims()
            check(r4 == 0.1 and all(x and x["cols"] >= MIN_COLS for x in dims4),
                  "7 cpSetRatio(split, 0.05) → 木は 0.1 に clamp・描く位置は 40 桁の下限で止まる",
                  json.dumps({"ratio": r4, "dims": dims4}))

            # 9 保存と復元（2 ペインで再読み込み）
            page.evaluate(f"() => {d}.cpSetRatio('{split_id}', 0.5)")
            st = state(page)
            sels = [(x["id"], x["sel"]) for x in st["p"]["leaves"]]
            key_before = stored(page)
            boot(page, live_url)
            back = wait_count(page, 2, 8000) and wait_shown_term(page, TA) and wait_shown_term(page, TB)
            wait_js(page, "() => window.__office.debug.panes().subs.length === 2", None, 3000)
            st = state(page)
            check(back and key_before and [(x["id"], x["sel"]) for x in st["p"]["leaves"]] == sels
                  and st["p"]["streams"] == 1 and sorted(st["p"]["subs"]) == sorted([TA, TB]),
                  "9 再読み込みで木と葉の sel が戻る（streams 1・subs 2）",
                  json.dumps({"want": sels, "got": [(x["id"], x["sel"]) for x in st["p"]["leaves"]],
                              "streams": st["p"]["streams"], "subs": st["p"]["subs"], "key": key_before}))
            # 固定時刻のページは鍵を読まない（同じ context＝同じ localStorage）
            key_before = stored(page)
            fixed = ctx.new_page()
            watch(fixed, "t=3.2 鍵あり")
            boot(fixed, f"http://127.0.0.1:{port}/?ui=cockpit&t=3.2&seed=11")
            wait_shown_term(fixed, TA)
            frame(fixed, 3)
            default_ok(fixed, "固定時刻 ?t=3.2 は鍵を読まない（aioffice.cockpit.panes が 2 葉でも）", no="9")
            check(stored(fixed) == key_before, "9 固定時刻のページは鍵を読まず書かない（鍵はそのまま）",
                  f"{key_before} → {stored(fixed)}")
            win_errors(fixed, "t=3.2 鍵あり")
            fixed.close()

            # 10 会話ビュー（端末の無いセッションを選んでいる葉へ）
            right = st["p"]["leaves"][1]["id"]
            left = st["p"]["leaves"][0]["id"]
            text_a = page.evaluate(f"() => {d}.termText('{TA}')")
            page.evaluate(f"() => {d}.cpFocusPane('{right}')")
            click_tab(page, SESSION_TALK)
            talk = wait_js(page, """id => { const b = document.querySelector(`#cp-xterm .cppane[data-pane="${id}"] > .cppane-body`);
                return !!b && b.classList.contains('dialog') && b.contains(document.querySelector('#cp-work'))
                  && b.contains(document.querySelector('#cp-dlg')); }""", right, 4000)
            frame(page, 2)
            st = state(page)
            text_a2 = page.evaluate(f"() => {d}.termText('{TA}')")
            check(talk and leaf(st, right)["sel"] == SESSION_TALK and MARK[TA] in text_a2 and text_a2 == text_a
                  and MARK[TB] not in text_a2 and st["p"]["subs"] == [TA] and st["p"]["streams"] == 1,
                  "10 会話ビューは選んでいる葉に出る（#cp-work・#cp-dlg）・他の葉の端末（text・購読）は不変",
                  json.dumps({"talk": talk, "leaves": st["p"]["leaves"], "subs": st["p"]["subs"],
                              "textSame": text_a2 == text_a}))
            page.evaluate(f"() => {d}.cpFocusPane('{left}')")
            parked = wait_js(page, """() => { const p = document.querySelector('#cp-dlg-park');
                return !!p && p.hidden && p.contains(document.querySelector('#cp-work')) && p.contains(document.querySelector('#cp-dlg')); }""",
                             None, 3000)
            st = state(page)
            rp = next((x for x in st["panes"] if x["id"] == right), {})
            check(parked and "label" in rp.get("body", "") and st["p"]["subs"] == [TA],
                  "10 端末の葉を選び直すと会話の組は #cp-dlg-park へ戻り、会話の葉は .label（名前 1 行）",
                  json.dumps({"parked": parked, "body": rp.get("body"), "subs": st["p"]["subs"]}))

            # 11 段と狭さ（2 カラムでは選んでいる葉だけ・木は保持）
            page.evaluate(f"() => {d}.cpAssign('{right}', 'term:{TB}')")
            wait_shown_term(page, TB)
            page.set_viewport_size(TWO_COL)
            narrow1 = wait_js(page, "() => { const p = window.__office.debug.panes();"
                                    " return p.leaves.filter((l) => l.visible).length === 1 && p.subs.length === 1; }", None, 4000)
            st = state(page)
            check(narrow1 and st["p"]["count"] == 2 and st["p"]["streams"] == 1 and len(st["p"]["subs"]) == 1
                  and st["vheads"] == 0 and st["attr"] == "1",
                  "11 1280×800（2 カラム）: 見えている葉 1・streams 1・subs 1・見出し無し（木は 2 葉のまま）",
                  json.dumps({"leaves": st["p"]["leaves"], "subs": st["p"]["subs"], "vheads": st["vheads"],
                              "attr": st["attr"]}))
            page.set_viewport_size(WIDE)
            wide2 = wait_js(page, "() => { const p = window.__office.debug.panes();"
                                  " return p.leaves.filter((l) => l.visible).length === 2 && p.subs.length === 2; }", None, 4000)
            st = state(page)
            check(wide2 and st["p"]["streams"] == 1 and st["vheads"] == 2 and st["attr"] == "2",
                  "11 1920 へ戻すと 2 葉とも見え購読も 2 本（streams 1）",
                  json.dumps({"leaves": st["p"]["leaves"], "subs": st["p"]["subs"]}))
            # デモ（鍵が在っても読まない・端末 API に触らない）
            key_demo = stored(page)
            demo = ctx.new_page()
            watch(demo, "demo")
            demo_asked = []
            demo.on("request", lambda r: demo_asked.append(r.url) if "/api/term/" in r.url else None)
            demo.goto(f"http://127.0.0.1:{port}/?ui=cockpit&demo=1", wait_until="domcontentloaded")
            demo.wait_for_selector(".cproom", timeout=20000)
            demo.wait_for_timeout(600)
            blur(demo)
            demo.keyboard.press("Meta+Backslash")
            demo.wait_for_timeout(500)
            dst = demo.evaluate("() => ({ panes: document.querySelectorAll('.cppane').length,"
                                " heads: document.querySelectorAll('.cppane-head').length,"
                                " probe: window.__office.debug.panes ? window.__office.debug.panes().count : null })")
            check(key_demo and dst["panes"] == 0 and dst["heads"] == 0 and dst["probe"] in (1, None) and not demo_asked,
                  "11 デモ（?demo=1）: 鍵が在っても .cppane 0・⌘\\ 無効・/api/term/* 0 回",
                  json.dumps({"dom": dst, "term API": demo_asked[:3], "key": bool(key_demo)}))
            win_errors(demo, "demo")
            demo.close()

            # 9 初期化（cpResetPanes・cpResetLayout）と壊れた保存
            r_p = page.evaluate(f"() => {d}.cpResetPanes()")
            frame(page, 2)
            st = state(page)
            k1 = stored(page)
            page.evaluate(f"() => {d}.cpSplit(null, 'v')")
            wait_count(page, 2)
            k2 = stored(page)
            r_l = page.evaluate(f"() => {d}.cpResetLayout()")
            frame(page, 2)
            st2 = state(page)
            k3 = stored(page)
            check(r_p and st["p"]["count"] == 1 and k1 is None and k2 and r_l and st2["p"]["count"] == 1 and k3 is None
                  and page.evaluate(f"() => localStorage.getItem('{LAYOUT_KEY}')") is None,
                  "9 cpResetPanes()・cpResetLayout() で 1 枚に戻り鍵 aioffice.cockpit.panes が消える",
                  json.dumps({"resetPanes": r_p, "k1": k1, "k2": bool(k2), "resetLayout": r_l, "k3": k3,
                              "count": [st["p"]["count"], st2["p"]["count"]]}))
            five = {"v": 1, "focus": "p1", "root": {"id": "s2", "dir": "h", "ratio": 0.5,
                    "a": {"id": "s3", "dir": "v", "ratio": 0.5, "a": {"id": "p1", "sel": None}, "b": {"id": "p3", "sel": None}},
                    "b": {"id": "s4", "dir": "v", "ratio": 0.5, "a": {"id": "p2", "sel": None},
                          "b": {"id": "s5", "dir": "h", "ratio": 0.5, "a": {"id": "p4", "sel": None},
                                "b": {"id": "p5", "sel": None}}}}}
            bad_results = []
            for label, raw in (("garbage", "garbage"), ('{"v":0}', '{"v":0}'), ("葉 5", json.dumps(five))):
                n_err = len(errors)
                page.evaluate("([k, v]) => localStorage.setItem(k, v)", [PANES_KEY, raw])
                boot(page, live_url)
                wait_shown_term(page, TA)
                wait_js(page, f"() => localStorage.getItem('{PANES_KEY}') === null", None, 4000)
                frame(page, 2)
                st = state(page)
                win_errors(page, f"壊れた保存 {label}")
                bad_results.append({"label": label, "count": st["p"]["count"], "attr": st["attr"],
                                    "key": stored(page), "errors": len(errors) - n_err})
            check(all(x["count"] == 1 and x["attr"] == "1" and x["key"] is None and x["errors"] == 0 for x in bad_results),
                  "9 壊れた保存（garbage・{\"v\":0}・葉 5）→ 1 枚・エラー 0・鍵は消える",
                  json.dumps(bad_results, ensure_ascii=False))
            # 死んだ端末 id の葉は空（一覧の後で当てる）
            dead = {"v": 1, "focus": "p2", "root": {"id": "s2", "dir": "h", "ratio": 0.5,
                    "a": {"id": "p1", "sel": SESSION_A}, "b": {"id": "p2", "sel": f"term:{DEAD}"}}}
            page.evaluate("([k, v]) => localStorage.setItem(k, v)", [PANES_KEY, json.dumps(dead)])
            boot(page, live_url)
            wait_count(page, 2, 8000)
            wait_shown_term(page, TA)
            frame(page, 2)
            st = state(page)
            p2 = next((x for x in st["panes"] if x["id"] == "p2"), {})
            check(st["p"]["count"] == 2 and leaf(st, "p2") and leaf(st, "p2")["sel"] is None and "empty" in p2.get("body", "")
                  and leaf(st, "p1")["sel"] == SESSION_A and not any(DEAD in u for _, u in asked),
                  "9 一覧に無い端末 id の葉は空で戻る（その id を購読しに行かない）",
                  json.dumps({"leaves": st["p"]["leaves"], "body": p2.get("body")}))
            page.evaluate(f"() => {d}.cpResetLayout()")
            single_stream = [p for m, p in asked if p.split("?")[0] == "/api/term/stream"]
            check(not single_stream, "単一の購読 /api/term/stream は使わない（多重化 1 本だけ）",
                  f"{len(single_stream)} 回")
            win_errors(page, "1920")
            ctx.close()

            # ── 1440×900 の live（既定の端末の列 574px）──────────────────────────
            posts2, asked2 = [], []
            ctx2 = browser.new_context(viewport=NARROW, device_scale_factor=1)
            ctx2.add_init_script(INIT)
            fake_routes(ctx2, world, posts2, asked2)
            page2 = ctx2.new_page()
            watch(page2, "1440")
            boot(page2, live_url)
            # R121: 保存の無い既定は作業ビュー（本物の端末がある claude のセッションでも）・［ターミナル］で端末へ切り替わり、
            # 選んだ側を保存する（aioffice.cockpit.center＝表示の選び方だけ）。この節だけ保存を入れずに開いて、既定と切替と保存を見る
            wait_js(page2, "() => !document.querySelector('#cp-activity-toggle').hidden", None, 6000)
            pre = page2.evaluate("""() => ({ live: document.querySelector('#cp-term').classList.contains('live'),
              act: document.querySelector('#cp-dlg').classList.contains('act'),
              key: localStorage.getItem('aioffice.cockpit.center') })""")
            page2.click("#cp-activity-toggle")
            switched = wait_js(page2, "() => document.querySelector('#cp-term').classList.contains('live')", None, 4000)
            key = page2.evaluate("() => localStorage.getItem('aioffice.cockpit.center')")
            check(not pre["live"] and pre["act"] and pre["key"] is None and switched and key == "terminal",
                  "R121 保存の無い既定は作業ビュー・［ターミナル］で端末へ切り替わり、その選び方を保存する",
                  json.dumps({"pre": pre, "switched": switched, "key": key}, ensure_ascii=False))
            wait_shown_term(page2, TA)
            frame(page2, 3)
            default_ok(page2, "live 1440×900")
            blur(page2)
            page2.keyboard.press("Meta+Backslash")
            toast = toast_has(page2, "cp_pane_no_room_h")
            st = state(page2)
            check(toast and st["p"]["count"] == 1 and st["attr"] == "1",
                  "8 1440×900 の既定では左右に分けず、#toast に「左右に分けられません（40 桁）」",
                  json.dumps({"toast": page2.evaluate("() => document.querySelector('#toast')?.textContent"),
                              "count": st["p"]["count"]}, ensure_ascii=False))
            widened = page2.evaluate(f"() => window.__office.debug.cpSplitTo(0, {ROOM_X2_LEFT})")
            wait_js(page2, "() => window.__office.debug.cpLayout().k === 2", None, 3000)
            frame(page2, 3)
            page2.keyboard.press("Meta+Backslash")
            two = wait_count(page2, 2)
            k = page2.evaluate("() => window.__office.debug.cpLayout().k")
            check(widened and k == 2 and two,
                  f"8 cpSplitTo(0, {ROOM_X2_LEFT})（部屋 ×2＝端末の列を広げる）の後は ⌘\\ で左右に分けられる",
                  json.dumps({"splitTo": widened, "k": k, "panes": state(page2)["p"]["tree"]}))
            reset = page2.evaluate("() => window.__office.debug.cpResetLayout()")
            frame(page2, 3)
            st = state(page2)
            check(reset and st["p"]["count"] == 1 and stored(page2) is None
                  and page2.evaluate("() => window.__office.debug.cpLayout().isDefault"),
                  "8 cpResetLayout() で配置もペインも既定へ（鍵 2 つとも無し）",
                  json.dumps({"reset": reset, "count": st["p"]["count"], "key": stored(page2)}))
            win_errors(page2, "1440")
            ctx2.close()

            check(not errors, "12 console.error / pageerror / window の error（ResizeObserver loop を含む）= 0",
                  "; ".join(errors[:4]))
            browser.close()
    finally:
        proc.terminate()

    took = time.monotonic() - t_start
    print(f"  所要 {took:.1f} 秒" + ("".join(f"\n  ⚠ {n}" for n in notes)))
    print("✅ R111 コックピットのペイン green" if not ng else f"❌ R111 コックピットのペイン {ng} 件")
    return 1 if ng else 0


if __name__ == "__main__":
    sys.exit(main())
