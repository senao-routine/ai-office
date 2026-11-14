#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""R99 Phase 2: コックピットの**本物の端末**をブラウザから打つ E2E。

「端末が開いた」ではなく、**キーボードで打った文字が本物の pty に届き、出力が画面に返る**
ところまで確かめる。ここが壊れると、コックピットの芯（オフィスと端末が同じ画面にある）が消える。

検査:
  1. ＋ 端末 → シェルを開く → タブが増え、中央に xterm が出る（会話ビューは畳まれる）
  2. キーボードで打った `echo SMOKE_$((6*7))` の**出力** SMOKE_42 が画面に出る
     （打った行のエコーでは合わない形の印＝実行を待たずに合格しない）
  2b. **2 回目以降の打鍵も届く**（入力の Promise の鎖が循環して死ぬ実バグを踏んだ）
  3. **Ctrl+C が効く**（sleep 30 を止めて、次のコマンドがすぐ走る）
  4. 窓の大きさを変えると、サーバー側の端末の cols/rows も変わる
  5. **再読み込みしても端末は生きていて、前の出力が戻る**（backlog の再生）
  5b. **再描画で入力欄のフォーカスを端末へ奪わない**（奪うと指示の打鍵が本物のシェルで実行される）
  5c. 購読するのは見えている端末 1 本だけ（HTTP/1.1 の接続 6 本を食い潰さない）
  5c'. タブを往復しても画面モードと中身が残る（続きから読む＝reset しない）
  5d. 一覧から消えた端末は画面からも捨てる
  6. 閉じる → 一覧から消える（プロセスごと止まる）
  6b. デモ（?demo=1）では本物の端末に触らない
  7. console.error / pageerror = 0（全体を通して・最後に見る）
  R111-B4 ペイン（本物の pty・1920×1080＝左右に 40 桁×2 が入る形。サーバーとブラウザは上と同じ 1 本ずつ）:
  8. ＋シェル（1 ペイン）→ ⌘\（xterm の中で）→ ＋シェルが新しい葉に入る（葉 2・見出し 2・subs が別の 2 本・streams 1）
  9. 各ペインの xterm を押して別の印を打つ → 各 termText(id) に自分の印だけ（相手の印もその打鍵のエコーも無い）
  10. /api/term/list の cols は両方とも 1 ペインの時より小さく、rows は両方揃って 1 ペインより見出しの丈のぶんだけ少ない
      （resize が葉ごとに届いている・サーバーの値がブラウザの xterm の桁と一致するまで待つ）
  11. 再読み込み → 木と端末 id が戻る（daemon は生きている・streams 1）・見出しの ✕ で 1 枚に戻り subs 1（端末は止めない）・
      一覧に無い端末 id を保存に混ぜて開くとその葉は空（その id を購読しに行かない）
  12. 接続の実測（多重化が効いている証明）: 4 本のシェルを 2×2 に置き（MAX_TERMS）、/api/term/list を 4 本ずつ並べて 20 回・
      打鍵の往復（echo MARK_i → 出力が termText に届く）を 10 回。Resource Timing の requestStart − fetchStart（待ち行列）
      p95 < 300ms・往復 p95 < 600ms。負荷が高い（1 分の load average ＞ CPU 数）ときだけ閾値を 2 倍まで許して ⚠ を出す
      （嘘の赤も嘘の緑も出さない＝実測値は毎回表示する）。同じ窓の間、ブラウザの Network（CDP）で数えた長い接続の同時の
      最大が端末 1 本・/api/events と合わせて 2 本以下で、サーバーの viewers が端末ごとに 1 人（probe の streams()==1 だけでは
      「ペインごとに 1 本ずつ繋いで active を束ねた」形を見分けられない＝変異で確かめた）

使い方: python3 tests/cockpit_term_smoke.py   （verify.sh ▶7 から呼ぶ・Playwright 必要）
"""
import json
import math
import os
import pathlib
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
from ui_shot import SWIFTSHADER, free_port, start_server  # noqa: E402

WORLD = ROOT / "tests" / "fixtures" / "world" / "basic.json"
VIEW = {"width": 1440, "height": 900}
WIDE = {"width": 1920, "height": 1080}       # R111: 端末の列 ≈ 800px＝左右に 40 桁×2 が入る（1440×900 は入らない）
PANES_KEY = "aioffice.cockpit.panes"
DEAD_TERM = "deadbeefdeadbeef"                # 一覧に無い端末 id（保存に混ぜる）
WAIT_P95_MS = 300                             # 12: 要求の待ち行列（requestStart − fetchStart）の p95
ROUND_P95_MS = 600                            # 12: 打鍵の往復の p95
LIST_BATCH, LIST_ROUNDS = 4, 5                # 12: /api/term/list を 4 本ずつ並べて 5 回＝20 回
ROUND_TRIPS = 10


def p95(xs):
    """最近傍順位の 95 パーセンタイル（20 個なら大きい方から 2 番目・10 個なら最大）"""
    s = sorted(xs)
    return s[max(0, math.ceil(0.95 * len(s)) - 1)] if s else float("nan")


# 12: 打鍵の往復の時計（Enter を押す直前に起こし、出力の印が xterm のバッファに届いた時刻を控える）
START_ROUND_TRIP = r"""([id, needle]) => {
  window.__r111rt = null;
  const t0 = performance.now();
  const tick = () => {
    if (window.__office.debug.termText(id).includes(needle)) { window.__r111rt = performance.now() - t0; return; }
    if (performance.now() - t0 > 8000) { window.__r111rt = -1; return; }
    setTimeout(tick, 4);
  };
  tick();
}"""
# 12: /api/term/list を n 本ずつ並べて投げる（印の付いた URL＝ページ自身のポーリングと混ぜない）
LIST_BURST = r"""async ([n, r]) => {
  await Promise.all(Array.from({ length: n }, (_, i) =>
    fetch(`/api/term/list?r111=${r}-${i}`, { headers: { 'X-Office-Local': '1' } }).then((x) => x.json())));
}"""
LIST_TIMINGS = r"""() => performance.getEntriesByType('resource')
  .filter((e) => e.name.includes('/api/term/list?r111='))
  .map((e) => ({ wait: e.requestStart - e.fetchStart, total: e.responseEnd - e.startTime, rs: e.requestStart }))"""
# 11: 葉の body の種類（DOM）
PANE_BODY = r"""id => document.querySelector(`#cp-xterm > .cppane[data-pane="${id}"] > .cppane-body`)?.className || ''"""
VISIBLE_HEADS = r"""() => document.querySelectorAll('#cp-xterm > .cppane:not([hidden]) > .cppane-head').length"""


def show_terminal(page):
    """R121: 単葉の既定は作業。端末を打つ検査は本人の切替を明示してから進める。"""
    page.wait_for_function("""() => document.querySelector('#cp-term').classList.contains('live')
      || !document.querySelector('#cp-activity-toggle').hidden""", timeout=8000)
    if not page.evaluate("() => document.querySelector('#cp-term').classList.contains('live')"):
        page.click("#cp-activity-toggle")


def panes_section(page, check, terms, boot):
    """R111-B4 の節 8〜12（本物の pty のペイン）。上の節のサーバー・ブラウザ・ページをそのまま使う（新しいサーバーを立てない）"""
    d = "window.__office.debug"

    def js_wait(expr, arg=None, timeout=8000):
        try:
            page.wait_for_function(expr, arg=arg, timeout=timeout)
            return True
        except Exception:  # noqa: BLE001 — 待ちの失敗は検査の赤にする
            return False

    def panes():
        return page.evaluate(f"() => {d}.panes()")

    def alive_ids():
        return [t["id"] for t in terms(page) if t["alive"]]

    def text(tid):
        return page.evaluate(f"id => {d}.termText(id)", tid)

    def wait_shown(tid, timeout=8000):
        return js_wait("""id => { const e = document.querySelector(`#cp-xterm > .cppane .cpxt[data-term="${id}"]:not([hidden])`);
            return !!e && !!e.querySelector('.xterm-rows') && !!window.__office.debug.termDims(id); }""", tid, timeout)

    def wait_count(n, timeout=5000):
        return js_wait(f"n => {d}.panes().count === n", n, timeout)

    def wait_subs(n, timeout=5000):
        return js_wait(f"n => {d}.panes().subs.length === n && {d}.panes().streams === 1", n, timeout)

    def open_shell():
        """＋ 端末 → シェル（選んでいる葉に入る）。新しい端末の id（出来なければ None）"""
        before = set(alive_ids())
        page.click("#cp-term-new")
        page.click("#cp-new-shell")
        end = time.monotonic() + 8
        while time.monotonic() < end:
            new = [i for i in alive_ids() if i not in before]
            if new:
                show_terminal(page)
                return new[0] if wait_shown(new[0]) else None
            page.wait_for_timeout(100)
        return None

    def settled(tid, timeout=4.0):
        """サーバーの cols/rows がブラウザの xterm の桁・行と一致するまで待つ（resize が届いた）。一致した (cols, rows)"""
        end = time.monotonic() + timeout
        while True:
            got = page.evaluate("""async (id) => {
                const r = await fetch('/api/term/list', { headers: { 'X-Office-Local': '1' } });
                const t = (await r.json()).terms.find((x) => x.id === id);
                const dm = window.__office.debug.termDims(id);
                return t && dm && t.cols === dm.cols && t.rows === dm.rows ? [t.cols, t.rows, dm.fontSize] : null; }""", tid)
            if got or time.monotonic() > end:
                return got
            page.wait_for_timeout(150)

    # 長い接続（/api/events と /api/term/stream*）の同時の本数を数える（12 の窓の中の最大＝多重化なら端末は 1 本）。
    # ブラウザの Network（CDP）で数える。張り直しで止めた fetch は loadingFailed（net::ERR_ABORTED）が来るが、再読み込みで
    # 捨てた前の文書の要求には来ない（実測: Playwright の requestfailed も CDP も前の文書の 3 本を幽霊として残した・
    # サーバーの viewers は各 1）＝新しい文書（type=Document の要求）が来たら前の文書（loaderId 違い）の要求は数えから外す
    inflight = {}                     # requestId → (種類, loaderId)
    peak = {"on": False, "streams": 0, "long": 0, "opened": 0}
    cdp = page.context.new_cdp_session(page)

    def on_req(ev):
        if ev.get("type") == "Document":
            for rid in [r for r, (_, lid) in inflight.items() if lid != ev.get("loaderId")]:
                inflight.pop(rid, None)
            return
        url = (ev.get("request") or {}).get("url", "")
        kind = "streams" if "/api/term/stream" in url else "events" if "/api/events" in url else None
        if not kind or ev.get("redirectResponse"):
            return
        inflight[ev["requestId"]] = (kind, ev.get("loaderId"))
        if peak["on"]:
            peak["opened"] += kind == "streams"
            peak["streams"] = max(peak["streams"], sum(1 for k, _ in inflight.values() if k == "streams"))
            peak["long"] = max(peak["long"], len(inflight))

    def on_done(ev):
        inflight.pop(ev.get("requestId"), None)

    cdp.on("Network.requestWillBeSent", on_req)
    cdp.on("Network.loadingFinished", on_done)
    cdp.on("Network.loadingFailed", on_done)
    cdp.send("Network.enable")

    page.set_viewport_size(WIDE)
    page.wait_for_timeout(400)
    page.evaluate(f"() => {d}.cpResetPanes()")

    # 8. ＋シェル（1 ペイン）→ xterm の中で ⌘\ → ＋シェルが新しい葉へ
    s1 = open_shell()
    single = settled(s1) if s1 else None
    in_xterm = page.evaluate("() => !!document.activeElement?.classList.contains('xterm-helper-textarea')")
    page.keyboard.press("Meta+Backslash")
    split = wait_count(2)
    st = panes()
    fresh = st["focused"]
    fresh_empty = next((x["sel"] is None for x in st["leaves"] if x["id"] == fresh), False)
    s2 = open_shell()
    wait_subs(2)
    st = panes()
    lv = st["leaves"]
    check(bool(s1 and s2 and single) and in_xterm and split and fresh_empty and st["count"] == 2
          and page.evaluate(VISIBLE_HEADS) == 2 and [x["term"] for x in lv] == [s1, s2] and lv[1]["id"] == fresh
          and st["streams"] == 1 and sorted(st["subs"]) == sorted([s1, s2]),
          "R111-8 xterm の中の ⌘\\ で分かれ、＋シェルは新しい葉に入る（葉 2・見出し 2・subs 別の 2 本・streams 1）",
          json.dumps({"s1": s1, "s2": s2, "single": single, "inXterm": in_xterm, "split": split,
                      "freshEmpty": fresh_empty, "leaves": lv, "streams": st["streams"], "subs": st["subs"]}))
    if not (s1 and s2 and len(lv) == 2):
        check(False, "R111-9〜12 は 8 の 2 ペインが前提", "8 が成り立たないので先へ進めない")
        return
    la, lb = lv[0]["id"], lv[1]["id"]

    # 9. 各ペインに別の印を打つ → 自分の印だけ
    page.click(f'#cp-xterm .cppane[data-pane="{la}"] .xterm')
    page.keyboard.type("echo PANE_A_$((20+22))")
    page.keyboard.press("Enter")
    page.click(f'#cp-xterm .cppane[data-pane="{lb}"] .xterm')
    page.keyboard.type("echo PANE_B_$((30+3))")
    page.keyboard.press("Enter")
    ok_a = js_wait(f"([id, n]) => {d}.termText(id).includes(n)", [s1, "PANE_A_42"])
    ok_b = js_wait(f"([id, n]) => {d}.termText(id).includes(n)", [s2, "PANE_B_33"])
    ta, tb = text(s1), text(s2)
    check(ok_a and ok_b and "PANE_B" not in ta and "PANE_A" not in tb,
          "R111-9 各ペインの xterm に打った印は自分の端末にだけ出る（相手の印もエコーも無い）",
          f"A: {ta[-160:]!r} / B: {tb[-160:]!r}")

    # 10. resize が葉ごとに届く（cols は 1 ペインより小さく・rows は揃って見出しのぶん少ない）
    sa, sb = settled(s1), settled(s2)
    head_h = page.evaluate("() => document.querySelector('#cp-xterm > .cppane:not([hidden]) > .cppane-head')"
                           "?.getBoundingClientRect().height || 0")
    cell_h = (panes().get("cell") or {}).get("h") or 0
    lo = math.floor(head_h / cell_h) if cell_h else 1
    hi = math.ceil(head_h / cell_h) if cell_h else 99
    diff = single[1] - sa[1] if sa else None
    check(bool(sa and sb) and sa[0] < single[0] and sb[0] < single[0] and sa[1] == sb[1]
          and diff is not None and max(1, lo) <= diff <= hi,
          f"R111-10 サーバーの cols は両方とも 1 ペインより小さく、rows は揃って見出し（{head_h:.0f}px）のぶん少ない",
          json.dumps({"single": single, "a": sa, "b": sb, "rowsDiff": diff, "want": [lo, hi], "cellH": cell_h}))

    # 11. 再読み込み → 木と端末 id が戻る・✕ で 1 枚・死んだ端末 id の葉は空
    want = [(x["id"], x["term"]) for x in panes()["leaves"]]
    boot(page)
    back = wait_count(2) and wait_shown(s1) and wait_shown(s2) and wait_subs(2)
    st = panes()
    alive = alive_ids()
    check(back and [(x["id"], x["term"]) for x in st["leaves"]] == want and st["streams"] == 1
          and s1 in alive and s2 in alive,
          "R111-11 再読み込みで木と端末 id が戻る（daemon の端末は生きたまま・接続 1 本）",
          json.dumps({"want": want, "got": [(x["id"], x["term"]) for x in st["leaves"]], "streams": st["streams"],
                      "alive": alive}))
    page.click(f'#cp-xterm .cppane[data-pane="{lb}"] .cppane-close')
    one = wait_count(1) and wait_subs(1)
    st = panes()
    alive = alive_ids()
    check(one and st["subs"] == [s1] and st["leaves"][0]["term"] == s1 and s2 in alive
          and page.evaluate(VISIBLE_HEADS) == 0,
          "R111-11 見出しの ✕ で 1 枚に戻り subs 1 本（閉じたペインの端末は止めない）",
          json.dumps({"leaves": st["leaves"], "subs": st["subs"], "alive": alive}))
    dead = {"v": 1, "focus": "p2", "root": {"id": "s2", "dir": "h", "ratio": 0.5,
            "a": {"id": "p1", "sel": f"term:{s1}"}, "b": {"id": "p2", "sel": f"term:{DEAD_TERM}"}}}
    page.evaluate("([k, v]) => localStorage.setItem(k, v)", [PANES_KEY, json.dumps(dead)])
    streams_urls = []

    def on_request(r):
        if "/api/term/stream" in r.url:
            streams_urls.append(r.url)

    page.on("request", on_request)
    boot(page)
    restored = wait_count(2) and wait_shown(s1) and wait_subs(1)
    st = panes()
    p2 = next((x for x in st["leaves"] if x["id"] == "p2"), {})
    p1 = next((x for x in st["leaves"] if x["id"] == "p1"), {})
    body = page.evaluate(PANE_BODY, "p2")
    page.remove_listener("request", on_request)
    check(restored and p2.get("sel") is None and "empty" in body and p1.get("term") == s1
          and not any(DEAD_TERM in u for u in streams_urls),
          "R111-11 一覧に無い端末 id を保存に混ぜて開くとその葉は空（その id を購読しに行かない）",
          json.dumps({"leaves": st["leaves"], "body": body, "streams": streams_urls[-2:]}))

    # 12. 接続の実測: 4 本のシェルを 2×2 に置き、一覧 20 回と打鍵の往復 10 回
    page.evaluate(f"() => {d}.cpAssign('p2', 'term:{s2}')")
    wait_shown(s2)
    ok_v1 = page.evaluate(f"() => {d}.cpSplit('p1', 'v')")
    s3 = open_shell() if ok_v1 else None
    ok_v2 = page.evaluate(f"() => {d}.cpSplit('p2', 'v')")
    s4 = open_shell() if ok_v2 else None
    four = wait_subs(4)
    st = panes()
    lv = st["leaves"]
    terms4 = [x["term"] for x in lv]
    check(four and st["count"] == 4 and all(x["visible"] for x in lv) and sorted(terms4) == sorted([s1, s2, s3, s4])
          and None not in terms4 and st["streams"] == 1 and len(alive_ids()) == 4,
          "R111-12 2×2 に本物のシェル 4 本（MAX_TERMS）・購読は接続 1 本（streams 1）で 4 本",
          json.dumps({"split": [ok_v1, ok_v2], "leaves": lv, "streams": st["streams"], "subs": st["subs"]}))
    if not four or None in terms4:
        return
    page.wait_for_timeout(300)
    peak.update(on=True, streams=sum(1 for k, _ in inflight.values() if k == "streams"), long=len(inflight), opened=0)
    page.evaluate("() => { performance.setResourceTimingBufferSize(2000); performance.clearResourceTimings(); }")
    for r in range(LIST_ROUNDS):
        page.evaluate(LIST_BURST, [LIST_BATCH, r])
    timings = page.evaluate(LIST_TIMINGS)
    waits = [t["wait"] for t in timings if t["rs"] > 0]
    totals = [t["total"] for t in timings]
    rts = []
    for i in range(ROUND_TRIPS):
        lf = lv[i % len(lv)]
        page.click(f'#cp-xterm .cppane[data-pane="{lf["id"]}"] .xterm')
        page.keyboard.type(f"echo MARK_{i}_$((1000+{i}))")
        page.wait_for_timeout(120)          # 打った文字のエコーが落ち着いてから（往復の起点は Enter）
        page.evaluate(START_ROUND_TRIP, [lf["term"], f"MARK_{i}_{1000 + i}"])
        page.keyboard.press("Enter")
        js_wait("() => window.__r111rt !== null", None, 9000)
        rt = page.evaluate("() => window.__r111rt")
        rts.append(rt if isinstance(rt, (int, float)) else -1)
    still_one = panes()["streams"] == 1
    peak["on"] = False
    cdp.detach()
    # サーバーから見た購読者（端末ごとの viewers）も 1 人ずつ＝止めた古い購読が残っていない（切断はサーバーの ping＝3 秒で気づく）
    end = time.monotonic() + 8
    viewers = {}
    while True:
        viewers = {t["id"]: t["viewers"] for t in terms(page) if t["alive"]}
        if (len(viewers) == 4 and all(v == 1 for v in viewers.values())) or time.monotonic() > end:
            break
        page.wait_for_timeout(250)
    one_each = len(viewers) == 4 and all(v == 1 for v in viewers.values())
    load1 = os.getloadavg()[0]
    ncpu = os.cpu_count() or 1
    busy = load1 > ncpu
    k = 2 if busy else 1
    w95, r95 = p95(waits), p95([x for x in rts if x >= 0])
    print(f"  ・ R111-12 実測: /api/term/list の待ち行列（requestStart − fetchStart）p95 {w95:.1f}ms・最大 {max(waits or [0]):.1f}ms"
          f"（{len(waits)} 件・応答まで p95 {p95(totals):.1f}ms）／打鍵の往復 p95 {r95:.0f}ms・中央 {sorted(rts)[len(rts) // 2]:.0f}ms"
          f"（{len(rts)} 回）／長い接続の同時の最大 {peak['long']} 本（うち端末 {peak['streams']} 本・張り直し {peak['opened']} 回）"
          f"・サーバーの viewers {sorted(viewers.values())}"
          f"／load {load1:.1f}・CPU {ncpu}")
    if busy:
        print(f"  ⚠ R111-12 負荷が高い（load {load1:.1f} ＞ CPU {ncpu}）＝閾値を 2 倍（{WAIT_P95_MS * k}ms・{ROUND_P95_MS * k}ms）で見た")
    check(len(waits) == LIST_BATCH * LIST_ROUNDS and len(rts) == ROUND_TRIPS and all(x >= 0 for x in rts)
          and still_one and peak["streams"] == 1 and peak["long"] <= 2 and one_each
          and w95 < WAIT_P95_MS * k and r95 < ROUND_P95_MS * k,
          f"R111-12 接続の実測: 待ち行列 p95 {w95:.1f}ms < {WAIT_P95_MS * k}ms・往復 p95 {r95:.0f}ms < {ROUND_P95_MS * k}ms"
          f"（4 本の端末を出したまま・端末の接続は同時に 1 本・events と合わせて ≤2 本・サーバーの viewers は各 1）",
          json.dumps({"waits": [round(x, 1) for x in waits], "roundTrips": [round(x, 1) for x in rts],
                      "streams1": still_one, "peak": {n: v for n, v in peak.items() if n != "on"}, "viewers": viewers}))
    # 後片付け: 4 本とも止める（サーバーを止める前にシェルを残さない）
    for tid in alive_ids():
        page.evaluate("""id => fetch('/api/term/close', { method: 'POST',
          headers: { 'X-Office-Local': '1', 'Content-Type': 'application/json' }, body: JSON.stringify({ id }) })""", tid)
    page.wait_for_timeout(600)


def main():
    from playwright.sync_api import sync_playwright

    world = json.loads(WORLD.read_text(encoding="utf-8"))
    ng, errors = 0, []

    def check(ok, label, detail=""):
        nonlocal ng
        print(f"  {'✓' if ok else '✗'} {label}{'' if ok else f' — {detail}'}")
        if not ok:
            ng += 1

    # ログインシェルは本人の設定（プロンプト・プラグイン）で遅く・非決定になる＝素の sh で固定する
    port = free_port()
    proc = start_server(port, extra_env={"OFFICE_PTY_SHELL": "/bin/sh", "PS1": "$ "})
    url = f"http://127.0.0.1:{port}/?ui=cockpit"

    def boot(page):
        # networkidle は使わない。live（?t= 無し）では /api/events が繋ぎっぱなし＝永久に idle にならない
        # （固定時刻の cockpit_smoke が networkidle で通るのは SSE を張らないから）
        page.goto(url, wait_until="domcontentloaded")
        page.wait_for_function("() => window.__office && window.__office.inject", timeout=20000)
        page.evaluate("w => window.__office.inject(w)", world)
        page.wait_for_selector(".cproom", timeout=20000)
        page.wait_for_timeout(300)

    # 画面の中身は xterm のバッファから読む（DOM の行は見えている範囲しか描かれない＝取りこぼす）
    def screen_text(page):
        return page.evaluate("() => window.__office.debug.termText()")

    def wait_text(page, needle, timeout=6000):
        try:
            page.wait_for_function("n => window.__office.debug.termText().includes(n)",
                                   arg=needle, timeout=timeout)
            return True
        except Exception:  # noqa: BLE001
            return False

    def terms(page):
        return page.evaluate("""async () => {
          const r = await fetch('/api/term/list', { headers: { 'X-Office-Local': '1' } });
          return (await r.json()).terms;
        }""")

    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(args=SWIFTSHADER)
            page = browser.new_page(viewport=VIEW, device_scale_factor=1)
            page.on("pageerror", lambda e: errors.append(f"pageerror: {e}"))
            page.on("console",
                    lambda m: errors.append(f"console.error: {m.text}") if m.type == "error" else None)
            # 4xx/5xx は URL ごと記録する（console の「404」だけではどれか分からず直せない）
            page.on("response", lambda r: errors.append(
                f"HTTP {r.status} {r.request.method} {r.url.split(str(port), 1)[-1]}") if r.status >= 400 else None)
            boot(page)

            # 1. ＋ 端末 → シェルを開く
            page.click("#cp-term-new")
            page.click("#cp-new-shell")
            # R121: 素のシェルは会話の記録が無い＝作業ビューに出すものが無い→いつも端末・切替（作業⇄ターミナル）は出さない
            #（記録のある claude のセッションの既定＝作業ビューと切替と保存は cockpit_r111_panes_smoke の R121 が見る）
            page.wait_for_function("() => document.querySelector('#cp-term').classList.contains('live')", timeout=8000)
            shell_direct = page.evaluate("() => document.querySelector('#cp-activity-toggle').hidden")
            check(shell_direct, "＋ → シェルはすぐ本物の端末（記録が無い＝作業ビューへの切替は出さない）")
            show_terminal(page)
            try:
                page.wait_for_selector(".cpxt:not([hidden]) .xterm", timeout=8000)
                opened = True
            except Exception:  # noqa: BLE001
                opened = False
            state = page.evaluate("""() => ({
              live: document.querySelector('#cp-term').classList.contains('live'),
              tab: !!document.querySelector('.cptab.term.sel'),
              dlgHidden: getComputedStyle(document.querySelector('#cp-dlg')).display === 'none',
              note: document.querySelector('#cp-term-note').textContent })""")
            check(opened and state["live"] and state["tab"] and state["dlgHidden"],
                  "＋ → シェル: タブが増え、中央に本物の端末が出る（会話ビューは畳む）", json.dumps(state, ensure_ascii=False))

            # 2. キーボードで打つ → 出力が返る
            page.click(".cpxt:not([hidden]) .xterm")
            page.keyboard.type("echo SMOKE_$((6*7))")
            page.keyboard.press("Enter")
            check(wait_text(page, "SMOKE_42"), "打った文字が pty に届き、出力が画面に返る",
                  screen_text(page)[-300:])

            # 2b. 2 回目以降の打鍵も届く。入力は順番を守る Promise の鎖で送っていて、空振りの flush が
            #     自分自身を返すと鎖が循環して reject し、**最初の塊だけ届いて以降は永久に消えた**（実測）
            page.keyboard.type("echo SECOND_$((5+5))")
            page.keyboard.press("Enter")
            check(wait_text(page, "SECOND_10"), "2 回目以降の打鍵も届く（入力の鎖が途中で死なない）",
                  screen_text(page)[-300:])

            # 3. Ctrl+C
            page.keyboard.type("sleep 30")
            page.keyboard.press("Enter")
            page.wait_for_timeout(400)
            page.keyboard.press("Control+c")
            page.keyboard.type("echo AFTER_$((3+4))")
            page.keyboard.press("Enter")
            check(wait_text(page, "AFTER_7", timeout=4000), "Ctrl+C で前面のジョブが止まる",
                  screen_text(page)[-300:])

            # 4. 窓の大きさ → サーバー側の cols/rows
            before = terms(page)[0]
            page.set_viewport_size({"width": 1280, "height": 760})
            page.wait_for_timeout(700)
            after = terms(page)[0]
            check((after["cols"], after["rows"]) != (before["cols"], before["rows"]),
                  "窓の大きさがサーバー側の端末に届く",
                  f"{before['cols']}x{before['rows']} → {after['cols']}x{after['rows']}")
            page.set_viewport_size(VIEW)

            # 5. 再読み込みしても端末は生きていて、前の出力が戻る
            tid = terms(page)[0]["id"]
            boot(page)
            page.wait_for_selector(f'.cptab.term[data-session="term:{tid}"]', timeout=8000)
            page.click(f'.cptab.term[data-session="term:{tid}"]')
            show_terminal(page)
            check(wait_text(page, "SMOKE_42"), "再読み込み後も同じ端末に戻れ、前の出力が戻る（backlog）",
                  screen_text(page)[-300:])
            # 戻った出力が二重に積まれていない（hello で消してから backlog を書く）
            count = screen_text(page).count("SMOKE_42")
            check(count == 1, "再接続で出力が二重に積まれない", f"SMOKE_42 が {count} 回")

            # 5b. **再描画でフォーカスを奪わない**（別モデルレビュー high）。端末を見せたまま別の入力欄に
            #     打っている最中にポーリングの再描画が走っても、打鍵が本物のシェルへ流れない
            page.evaluate("""() => { const i = document.createElement('input'); i.id = 'probe-input';
              document.body.append(i); i.focus(); }""")
            for _ in range(3):
                page.evaluate("w => window.__office.inject(w)", world)
                page.wait_for_timeout(120)
            page.wait_for_timeout(300)
            holder = page.evaluate("() => document.activeElement && document.activeElement.id")
            check(holder == "probe-input", "再描画で入力欄のフォーカスを端末へ奪わない", f"フォーカスは {holder}")
            page.evaluate("() => document.getElementById('probe-input')?.remove()")

            # 5c. **購読は見えている 1 本だけ**（隠した端末も繋ぐと HTTP/1.1 の 6 本を食い潰す）
            page.click("#cp-term-new")
            page.click("#cp-new-shell")
            show_terminal(page)
            page.wait_for_timeout(800)
            streams = page.evaluate("() => window.__office.debug.termStreams()")
            check(streams == 1, "購読するのは見えている端末 1 本だけ", f"{streams} 本")
            second = [t for t in terms(page) if t["id"] != tid][0]["id"]

            # 5c'. **タブを往復しても、リングから押し出された過去がスクロールバックに残る**。
            #      ★見分けられるのはここだけ: 出力が少ないと、毎回 reset して頭から流し直しても同じ画面に
            #      なり、この検査は何も証明しない（実際に「毎回頭から」に戻しても通った）。
            #      128KB（サーバーのリング）を超えて流してから往復し、先頭の印がまだ画面に在るかを見る。
            page.click(".cpxt:not([hidden]) .xterm")
            page.keyboard.type("printf '\\033[?2004h\\033[?1h'; echo HEAD_$((50+50)); i=0; "
                               "while [ $i -lt 4300 ]; do echo fill-$i-xxxxxxxxxxxxxxxxxxxxxxxxxx; i=$((i+1)); "
                               "done; echo TAIL_$((7*7))")
            page.keyboard.press("Enter")
            wait_text(page, "TAIL_49", timeout=30000)
            page.wait_for_timeout(300)
            before_modes = page.evaluate("() => window.__office.debug.termModes()")
            had_head = "HEAD_100" in screen_text(page)
            page.click(f'.cptab.term[data-session="term:{tid}"]')
            page.wait_for_timeout(400)
            page.click(f'.cptab.term[data-session="term:{second}"]')
            page.wait_for_timeout(1200)
            after_modes = page.evaluate("() => window.__office.debug.termModes()")
            text = screen_text(page)
            check(had_head and "HEAD_100" in text and text.count("TAIL_49") == 1
                  and before_modes and before_modes["bracketedPaste"] and after_modes == before_modes,
                  "タブを往復しても続きから読む（リング外の過去が残る・画面モードも残る・二重に積まない）",
                  f"HEAD 前 {had_head}→後 {'HEAD_100' in text} / TAIL {text.count('TAIL_49')} 回 / "
                  f"モード {before_modes} → {after_modes}")

            # 5d. **一覧から消えた端末は画面からも捨てる**（別の画面から閉じた等）
            page.evaluate("""id => fetch('/api/term/close', { method: 'POST',
              headers: { 'X-Office-Local': '1', 'Content-Type': 'application/json' },
              body: JSON.stringify({ id }) })""", second)
            page.wait_for_timeout(3800)          # 一覧の取り直し（3 秒）を 1 回は跨ぐ
            left = page.evaluate(f"() => !!document.querySelector('.cpxt[data-term=\"{second}\"]')")
            check(not left, "一覧から消えた端末の xterm を捨てる（DOM とバッファを抱えない）")
            page.click(f'.cptab.term[data-session="term:{tid}"]')

            # 6. 閉じる（b4-mac: 生きている端末はプロセスごと止める＝「終了」の 2 回押し。1 回目では止めない）
            page.click("#cp-term-close")
            page.wait_for_timeout(600)
            armed = page.evaluate("() => { const b = document.getElementById('cp-term-close');"
                                  " return { text: b.textContent, armed: b.classList.contains('armed') }; }")
            still = [t for t in terms(page) if t["alive"] and t["id"] == tid]
            check(bool(still) and armed["armed"],
                  f"閉じる（生きている端末）は 1 回押しただけでは止めない（「{armed['text']}」の 3 秒）",
                  json.dumps({"armed": armed, "alive": still}, ensure_ascii=False))
            page.click("#cp-term-close")
            page.wait_for_timeout(600)
            alive = [t for t in terms(page) if t["alive"]]
            gone = not page.query_selector(f'.cptab.term[data-session="term:{tid}"]')
            check(not alive and gone, "閉じる → 端末が止まり、タブが消える", json.dumps(alive))

            # 7. デモ（?demo=1）では本物の端末に触らない（＋も出さず、一覧も取りに行かない）
            demo = browser.new_page(viewport=VIEW, device_scale_factor=1)
            asked = []
            demo.on("request", lambda r: asked.append(r.url) if "/api/term/" in r.url else None)
            demo.goto(f"http://127.0.0.1:{port}/?ui=cockpit&demo=1", wait_until="domcontentloaded")
            demo.wait_for_selector(".cproom", timeout=20000)
            demo.wait_for_timeout(1500)
            hidden_new = demo.evaluate("() => document.getElementById('cp-term-new').hidden")
            check(hidden_new and not asked, "デモでは本物の端末に触らない（＋無し・一覧を取らない）",
                  f"＋ hidden={hidden_new} / 端末 API {len(asked)} 回")
            demo.close()

            panes_section(page, check, terms, boot)

            check(not errors, "console.error / pageerror / HTTP エラー = 0", "; ".join(errors[:4]))
            browser.close()
    finally:
        proc.terminate()

    print("✅ cockpit 端末スモーク green" if not ng else f"❌ cockpit 端末スモーク {ng} 件")
    return 1 if ng else 0


if __name__ == "__main__":
    sys.exit(main())
