#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""R106（cockpit-ui 組）: PC 版コックピットの5視点dogfoodで見つかった不具合のピン。

起動・fixture・route の書き方は tests/cockpit_smoke.py を写す（start_server・sync_playwright・
`window.__office.inject(w)`・`?ui=cockpit&t=<秒>` の固定時刻ページ）。見た目が変わる項目
（12 の絞り込み入力・11 の複数行化）は golden を自分では撮り直さない＝呼び手の仕事。

検査（plan.md の番号）:
  3  台帳が空のとき「セッションはまだありません」が出る（CSS の重複セレクタで常に display:none だった）
  9  台帳は roving tabindex（Tab 1 か所だけ・矢印キーで行をまたぐ）
 11  指示欄（#composeinput）は複数行のtextarea。Enter=送信・Shift+Enter=改行・送信後は空になる
 12  台帳の絞り込み（⌘K・/ で入る・あいまい一致・Esc で消す）
 17  台帳の列見出しは 2 回目クリックで降順・3 回目で既定へ戻る（従来は 2 回目で既定に戻っていた）
 18  「あなた待ち」の文言はヘッダーのチップだけ（台帳・一覧の見出しは重複しない）
 19  cockpit では #usage-summary への書き込み経路（paintPins）が止まっている（iso は変えない）
 20  端末タブの縁（溢れているとき）に mask-image のフェードが掛かる
 a11y 台帳の行に aria-label が付き、頭文字バッジ（.mono）は aria-hidden

R110 S1'（名簿のエンジン・r110_roster_pins）:
 a  名簿の行に .c-project 先頭の点（aria-hidden）と --session＝タブの点と同じ色
 b  証拠は短い語（✓ 10分前）・何の証拠か（commit / test / 失敗）は title の全文
 c  選択の印（.sel）は端末で選んでいる人の行だけ（タブで選び直すと固定時刻のページでも追う）
 d  端末タブの名前に title（全文）
 e  右の縁で途中まで切れたタブも「N ›」に数える
 f  ⚙ 文字の大きさ 12/13/14/16（既定 14・data-cp-font・保存 aioffice.cockpit.font は既定以外だけ・段に無い値は既定）

R110 S6（名札・眺めるモード・⚙ の表示の節・経過・r110_s6_pins＝固定ワールド cockpit.json の固定時刻のページ）:
 g  常時の名札: 部屋に貼った人（退場中を除く）全員に #cpnames の札・どれも部屋の canvas の内・互いに重ならない・頭上の印に
    掛かる札は薄く（.under）だけ。×3（1440×900）は名前だけ（8 字＋…まで）・×4（1920×1080）は状態の語つき
 h  眺めるモード: F で .cp-watch・部屋の倍率は画面いっぱい（1440×900＝×5）・端末の列の幅は変わらない・ティッカーは作業中の
    最初の人（固定時刻は 1 件目）・眺めている間の / は絞り込み欄へ行かない・Esc で戻ると ×3 と同じ部屋の画素
 i  ⚙: 「表示」の節に文字の大きさと環境音（＋注記）・「通知」の節は通知音（文字の大きさは無い）・選んでいる段は accent の枠
 j  「いま」の経過は固定時刻のページでは出さない（.since が 0）

別モデルレビュー（fix-needed・2件・本ファイルに追加のピン）:
 11-fix 空の #composeinput で Enter を押しても改行が入らない（preventDefault を送信条件の外へ出した）
 12-fix compose 入力中は ⌘K がフォーカスを奪わない（#sheet 内では cmdK も無視する）
"""
import copy
import json
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
from ui_shot import SWIFTSHADER, VIEWPORT, free_port, start_server  # noqa: E402

WORLD = ROOT / "tests" / "fixtures" / "world" / "basic.json"
PEAK = ROOT / "tests" / "fixtures" / "world" / "cockpit_peak.json"


def r110_roster_pins(page, peak, world, check):
    """R110 S1'（名簿のエンジン）: 行の個体色の点・短い証拠＋全文の title・選択の印＝端末で選んでいる人・
    タブ名の title・縁で途中まで切れたタブも「N ›」に数える・⚙ 文字の大きさ（保存は既定以外だけ）。
    固定時刻（?t=）のページ＝rAF は 1 回しか回らない＝選択の印は select の経路だけで追従できていることも見る。"""
    page.evaluate("w => window.__office.inject(w)", peak)
    page.wait_for_timeout(300)

    # ── R110-a: 名簿の行に個体色（--session）と .c-project 先頭の点。色はタブの点と同じ 1 本 ──
    dots = page.evaluate("""() => {
      const rows = [...document.querySelectorAll('#agents .pxrow')];
      const tab = new Map([...document.querySelectorAll('#cp-tabs .cptab[data-session]')]
        .map((b) => [b.dataset.session, b.style.getPropertyValue('--session')]));
      const bad = [];
      let compared = 0;
      for (const r of rows) {
        const proj = r.querySelector('.c-project');
        const dot = proj?.firstElementChild;
        const color = r.style.getPropertyValue('--session');
        const want = tab.get(r.dataset.session);
        if (want !== undefined) compared += 1;
        if (!dot || !dot.matches('i.dot') || dot.getAttribute('aria-hidden') !== 'true' || !color
            || (want !== undefined && want !== color)) bad.push({ key: r.dataset.key, dot: dot?.className, color, want });
      }
      return { rows: rows.length, compared, bad: bad.slice(0, 5) };
    }""")
    check(dots["rows"] > 1 and dots["compared"] > 1 and not dots["bad"],
          f"R110-a: 名簿 {dots['rows']} 行すべてに .c-project 先頭の点（aria-hidden）と --session・タブの点と同じ色（{dots['compared']} 行で照合）",
          json.dumps(dots, ensure_ascii=False))

    # ── R110-b: 証拠は短い語（✓/✗ と経過だけ）・何の証拠か（commit/test/失敗）は title の全文 ──
    evd = page.evaluate("""() => [...document.querySelectorAll('#agents .evd')]
      .filter((e) => /evd-(committed|tested|failed)/.test(e.className))
      .map((e) => ({ kind: e.className.match(/evd-(\\w+)/)[1], text: e.textContent, title: e.title }))""")
    word = {"committed": ("commit",), "tested": ("test",), "failed": ("失敗", "failed")}
    bad_evd = [e for e in evd if not (
        e["text"][:1] in ("✓", "✗") and e["title"][:1] == e["text"][:1]
        and not any(w in e["text"] for ws in word.values() for w in ws)
        and any(w in e["title"] for w in word.get(e["kind"], ("?",)))
        and e["title"].endswith(e["text"][1:].strip()))]
    check(len(evd) >= 2 and not bad_evd,
          f"R110-b: 証拠 {len(evd)} 件は短い語（✓ 10分前）で、title に全文（✓ test 10分前）",
          json.dumps(bad_evd[:4] or evd[:2], ensure_ascii=False))

    # ── R110-c: 選択の印（.sel）は端末で選んでいる人に付き、タブで選び直すと名簿の印も追う ──
    sel_js = """() => ({ sel: window.__office.debug.selected(),
      rows: [...document.querySelectorAll('#agents .pxrow.sel')].map((r) => ({ s: r.dataset.session, proj: r.classList.contains('proj') })) })"""
    before = page.evaluate(sel_js)
    other = page.evaluate("""(cur) => [...document.querySelectorAll('#agents .pxrow.proj')]
      .map((r) => r.dataset.session).find((s) => s && s !== cur) || null""", before["sel"])
    page.evaluate("s => window.__office.debug.select(s)", other)
    page.wait_for_timeout(150)
    after = page.evaluate(sel_js)

    def sel_ok(d):
        return bool(d["sel"]) and bool(d["rows"]) and all(r["s"] == d["sel"] for r in d["rows"]) \
            and any(r["proj"] for r in d["rows"])
    check(sel_ok(before) and other and after["sel"] == other and sel_ok(after),
          "R110-c: 名簿の .sel は端末で選んでいる人の行だけ（選び直すと固定時刻のページでも追う）",
          json.dumps({"before": before, "other": other, "after": after}, ensure_ascii=False))

    # ── R110-d: 端末タブの名前は title に全文（20em で切る唯一の省略） ──
    names = page.evaluate("""() => [...document.querySelectorAll('#cp-tabs .cptab .nm')]
      .map((n) => ({ text: n.textContent, title: n.title }))""")
    check(len(names) > 1 and all(n["text"] and n["title"] == n["text"] for n in names),
          f"R110-d: 端末タブ {len(names)} 本の名前に title（全文）",
          json.dumps([n for n in names if n["title"] != n["text"]][:4], ensure_ascii=False))

    # ── R110-e: 右の縁で途中まで切れたタブも「N ›」に数える（以前は丸ごと外のタブだけ＝1 本少なかった） ──
    page.evaluate("""() => {
      const host = document.querySelector('#cp-tabs');
      host.scrollLeft = 0;
      const box = host.getBoundingClientRect();
      const t = [...host.children].find((c) => c.getBoundingClientRect().right > box.right + 1);
      if (t) { const r = t.getBoundingClientRect(); host.scrollLeft += Math.round(r.left + r.width / 2 - box.right); }
      host.dispatchEvent(new Event('scroll'));
    }""")
    page.wait_for_timeout(150)
    edge = page.evaluate("""() => {
      const host = document.querySelector('#cp-tabs'), box = host.getBoundingClientRect();
      const rs = [...host.children].map((c) => c.getBoundingClientRect());
      const r = document.querySelector('#cp-tabs-r');
      return { straddle: rs.filter((x) => x.left < box.right - 1 && x.right > box.right + 1).length,
        right: rs.filter((x) => x.right > box.right + 1).length, text: r.hidden ? '' : r.textContent };
    }""")
    check(edge["straddle"] >= 1 and edge["text"] == f"{edge['right']} ›",
          f"R110-e: 縁で途中まで切れたタブ（{edge['straddle']} 本）も数えて「{edge['right']} ›」",
          json.dumps(edge, ensure_ascii=False))
    page.evaluate("() => { const h = document.querySelector('#cp-tabs'); h.scrollLeft = 0; h.dispatchEvent(new Event('scroll')); }")

    # ── R110-f: ⚙ 文字の大きさ（12/13/14/16・既定 14）＝data-cp-font・保存は既定以外だけ・開き直しても残る ──
    font_js = """() => ({ ds: document.querySelector('.hud.ui-cockpit').dataset.cpFont,
      ls: localStorage.getItem('aioffice.cockpit.font'),
      row: (() => { const r = [...document.querySelectorAll('#modal .mledform')]
          .find((x) => /文字の大きさ|Text size/.test(x.querySelector('.mledname')?.textContent || ''));
        return r ? [...r.querySelectorAll('button')].map((b) => b.textContent + (b.classList.contains('on') ? '*' : '')) : null; })() })"""
    page.click("#btn-settings")
    page.wait_for_selector("#modalwrap:not([hidden])", timeout=5000)
    f0 = page.evaluate(font_js)
    font_row = re.compile("文字の大きさ|Text size")
    page.locator("#modal .mledform").filter(has_text=font_row).locator("button", has_text="16px").click()
    page.wait_for_timeout(150)
    f16 = page.evaluate(font_js)
    page.locator("#modal .mledform").filter(has_text=font_row).locator("button", has_text="14px").click()
    page.wait_for_timeout(150)
    f14 = page.evaluate(font_js)
    page.click("#modalclose")
    check(f0["ds"] == "14" and f0["ls"] is None and f0["row"] == ["12px", "13px", "14px*", "16px"]
          and f16["ds"] == "16" and f16["ls"] == "16" and "16px*" in (f16["row"] or [])
          and f14["ds"] == "14" and f14["ls"] is None,
          "R110-f: ⚙ 文字の大きさ 12/13/14/16（既定 14）→ data-cp-font が替わり、保存は既定以外だけ（14 に戻すと鍵を消す）",
          json.dumps({"start": f0, "16": f16, "14": f14}, ensure_ascii=False))
    page.evaluate("() => localStorage.setItem('aioffice.cockpit.font', '13')")
    page.reload(wait_until="networkidle")
    page.wait_for_function("() => window.__office && window.__office.inject", timeout=20000)
    page.evaluate("w => window.__office.inject(w)", world)
    page.wait_for_selector(".cproom", timeout=20000)
    kept = page.evaluate("() => document.querySelector('.hud.ui-cockpit').dataset.cpFont")
    page.evaluate("() => localStorage.setItem('aioffice.cockpit.font', '15')")
    page.reload(wait_until="networkidle")
    page.wait_for_function("() => window.__office && window.__office.inject", timeout=20000)
    bogus = page.evaluate("() => document.querySelector('.hud.ui-cockpit').dataset.cpFont")
    page.evaluate("() => localStorage.removeItem('aioffice.cockpit.font')")
    check(kept == "13" and bogus == "14",
          "R110-f: 保存した 13 は開き直しても 13・段に無い値（15）は既定 14 で開く",
          json.dumps({"kept": kept, "bogus": bogus}))


def r110_s6_pins(browser, port, check, errors):
    """R110 S6: 常時の名札・眺めるモード（F）・⚙ の「表示」の節・固定時刻では経過を出さない。固定ワールドの固定時刻のページ
    （golden と同じ描き方＝描き直しは形の変化・キーの起点だけ）。"""
    world = json.loads((ROOT / "tests" / "fixtures" / "world" / "cockpit.json").read_text(encoding="utf-8"))
    page = browser.new_page(viewport=VIEWPORT, device_scale_factor=1)
    page.on("pageerror", lambda e: errors.append(f"pageerror: {e}"))
    page.on("console", lambda m: errors.append(f"console.error: {m.text}") if m.type == "error" else None)
    try:
        page.goto(f"http://127.0.0.1:{port}/?ui=cockpit&t=3.2", wait_until="networkidle")
        page.wait_for_function("() => window.__office && window.__office.inject", timeout=20000)
        page.evaluate("w => window.__office.inject(w)", world)
        page.wait_for_selector(".cproom", timeout=20000)
        page.wait_for_timeout(300)
        plates_js = """() => {
          const c = document.querySelector('#viewport canvas.cproom').getBoundingClientRect();
          const S = window.__office.stats().scale;
          const actors = window.__office.debug.roomActors().filter((a) => !a.leaving);
          const marks = window.__office.debug.roomMarks().filter((m) => m.markAt).map((m) => ({
            l: c.left + m.markAt.x * S, t: c.top + m.markAt.y * S, r: c.left + (m.markAt.x + m.markAt.w) * S,
            b: c.top + (m.markAt.y + m.markAt.h) * S }));
          const ps = [...document.querySelectorAll('#cpnames .cpname')].filter((n) => !n.hidden).map((n) => {
            const r = n.getBoundingClientRect();
            return { l: r.left, t: r.top, r: r.right, b: r.bottom, text: n.textContent, under: n.classList.contains('under'),
              nm: n.querySelector('.nm').textContent, st: n.querySelector('.st').textContent };
          });
          const hit = (a, b) => a.l < b.r - 0.5 && a.r > b.l + 0.5 && a.t < b.b - 0.5 && a.b > b.t + 0.5;
          let overlaps = 0;
          for (let i = 0; i < ps.length; i += 1) for (let j = i + 1; j < ps.length; j += 1) if (hit(ps[i], ps[j])) overlaps += 1;
          return { S, actors: actors.length, plates: ps.length, overlaps,
            outside: ps.filter((p) => p.l < c.left - 0.5 || p.r > c.right + 0.5 || p.t < c.top - 0.5 || p.b > c.bottom + 0.5).length,
            onMark: ps.filter((p) => !p.under && marks.some((m) => hit(p, m))).length, under: ps.filter((p) => p.under).length,
            longest: Math.max(...ps.map((p) => [...p.nm].length)), withState: ps.filter((p) => p.st).length };
        }"""
        g3 = page.evaluate(plates_js)
        check(g3["S"] == 3 and g3["actors"] > 10 and g3["plates"] == g3["actors"] and not g3["overlaps"] and not g3["outside"]
              and not g3["onMark"] and g3["longest"] <= 9 and g3["withState"] == 0,
              f"R110-g: ×3 で部屋の {g3['actors']} 人全員に名札（canvas の内・重なり 0・印に掛かる札は薄く {g3['under']}・名前だけ 8 字＋…まで）",
              json.dumps(g3, ensure_ascii=False))
        page.set_viewport_size({"width": 1920, "height": 1080})
        page.wait_for_function("() => window.__office.stats().scale === 4", timeout=8000)
        page.wait_for_timeout(300)
        g4 = page.evaluate(plates_js)
        check(g4["plates"] == g4["actors"] and not g4["overlaps"] and not g4["outside"] and not g4["onMark"]
              and g4["withState"] == g4["plates"],
              f"R110-g: ×4（1920×1080）は名札に状態の語（{g4['withState']}/{g4['plates']}）・重なり 0・canvas の内",
              json.dumps(g4, ensure_ascii=False))
        page.set_viewport_size(VIEWPORT)
        page.wait_for_function("() => window.__office.stats().scale === 3", timeout=8000)
        page.wait_for_timeout(300)

        # ── R110-h: 眺めるモード（F・Esc）──
        snap = "() => document.querySelector('#viewport canvas.cproom').toDataURL()"
        before = page.evaluate(snap)
        center_w = page.evaluate("() => document.querySelector('#cp-center').getBoundingClientRect().width")
        first = page.evaluate("""() => { const b = window.__office.debug.board();
          const m = b.flatMap((g) => g.sessions.map((s) => ({ ...s, projectName: g.name }))).find((s) => s.state === 'working' && !s.attention);
          return m ? (m.name || m.projectName) : ''; }""")
        page.mouse.click(5, 5)                       # 入力欄の外にフォーカスを置く
        page.keyboard.press("f")
        page.wait_for_function("() => window.__office.stats().scale === 5", timeout=8000)
        page.wait_for_timeout(200)
        page.keyboard.press("/")
        w = page.evaluate("""() => { const st = document.querySelector('#stage').getBoundingClientRect();
          const tk = document.querySelector('#cpticker');
          return { watch: document.querySelector('.hud.ui-cockpit').classList.contains('cp-watch'), scale: window.__office.stats().scale,
            stage: [st.left, st.top, st.width, st.height], vw: innerWidth, vh: innerHeight,
            center: document.querySelector('#cp-center').getBoundingClientRect().width,
            ticker: tk && !tk.hidden && getComputedStyle(tk).display !== 'none' ? tk.querySelector('.tk-who').textContent : null,
            exit: tk?.querySelector('.tk-exit')?.textContent || '',
            searchFocused: document.activeElement === document.querySelector('#cp-ledger-search'),
            shellHidden: getComputedStyle(document.querySelector('.cpshell')).visibility };
        }""")
        check(w["watch"] and w["scale"] == 5 and w["stage"] == [0, 0, w["vw"], w["vh"]] and w["center"] == center_w
              and w["ticker"] == first and first and w["exit"] and not w["searchFocused"] and w["shellHidden"] == "hidden",
              f"R110-h: F で眺めるモード（×5・部屋だけ画面いっぱい・端末の列の幅 {center_w}px のまま・ティッカーは 1 件目「{first}」・"
              "眺めている間の / は絞り込みへ行かない）", json.dumps(w, ensure_ascii=False))
        # 別モデルレビュー（R110）: 眺めている間に接続が切れても警告（#offbar）は部屋より前面に見える（部屋は最後の状態で動き続けるため）
        off = page.evaluate("""() => { const root = document.querySelector('.hud.ui-cockpit'); const bar = document.querySelector('#offbar');
          root.classList.add('offline'); bar.hidden = false; const r = bar.getBoundingClientRect();
          const hit = document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2);
          const out = { vis: getComputedStyle(bar).visibility, inView: r.width > 0 && r.bottom <= innerHeight && r.top >= 0,
            onTop: hit === bar || bar.contains(hit) };
          root.classList.remove('offline'); bar.hidden = true; return out; }""")
        check(off["vis"] == "visible" and off["inView"] and off["onTop"],
              "R110-h': 眺めている間の接続断の警告（#offbar）は部屋より前面に見える", json.dumps(off))
        page.keyboard.press("Escape")
        page.wait_for_function("() => window.__office.stats().scale === 3", timeout=8000)
        page.wait_for_timeout(200)
        back = page.evaluate("""() => ({ watch: document.querySelector('.hud.ui-cockpit').classList.contains('cp-watch'),
          ticker: getComputedStyle(document.querySelector('#cpticker')).display })""")
        after = page.evaluate(snap)
        check(not back["watch"] and back["ticker"] == "none" and after == before,
              "R110-h: Esc で戻ると .cp-watch が外れ、部屋は ×3 と同じ画素（倍率の往復で絵が変わらない）",
              json.dumps({**back, "same": after == before}, ensure_ascii=False))

        # ── R110-j: 固定時刻のページでは「いま」の経過を出さない ──
        since = page.evaluate("() => document.querySelectorAll('#agents .since').length")
        check(since == 0, "R110-j: 固定時刻のページでは名簿の「いま」に経過を出さない（.since 0 件）", f"since={since}")

        # ── R110-i: ⚙ の「表示」の節に文字の大きさと環境音・「通知」の節は通知音だけ・選んでいる段は accent の枠 ──
        page.click("#btn-settings")
        page.wait_for_selector("#modalwrap:not([hidden])")
        st = page.evaluate("""() => {
          const groups = [...document.querySelectorAll('#modal .cp-settings-group')];
          const names = (g) => [...g.querySelectorAll('.mledname')].map((n) => n.textContent);
          const on = [...document.querySelectorAll('#modal .mkeybtn.on')].map((b) => getComputedStyle(b).borderColor);
          const off = [...document.querySelectorAll('#modal .mkeybtn:not(.on)')].map((b) => getComputedStyle(b).borderColor);
          return { display: names(groups[0]), notes: [...groups[0].querySelectorAll('.mnote')].map((n) => n.textContent),
            notify: names(groups[1]), onColors: [...new Set(on)], offColors: [...new Set(off)] };
        }""")
        disp = st["display"]
        ok_i = (any("文字の大きさ" in n for n in disp) and any("環境音" in n for n in disp)
                and any("既定 OFF" in n for n in st["notes"])
                and not any("文字の大きさ" in n for n in st["notify"]) and any("通知音" in n for n in st["notify"])
                and st["onColors"] and not set(st["onColors"]) & set(st["offColors"]))
        check(ok_i, "R110-i: ⚙「表示」の節に文字の大きさと環境音（＋既定 OFF の注記）・「通知」の節は通知音・選んでいる段は別の枠の色",
              json.dumps(st, ensure_ascii=False))
        page.keyboard.press("Escape")
    finally:
        page.close()


def main():
    from playwright.sync_api import sync_playwright

    world = json.loads(WORLD.read_text(encoding="utf-8"))
    peak = json.loads(PEAK.read_text(encoding="utf-8"))
    ng = 0

    def check(ok, label, detail=""):
        nonlocal ng
        print(f"  {'✓' if ok else '✗'} {label}{'' if ok else f' — {detail}'}")
        if not ok:
            ng += 1

    port = free_port()
    proc = start_server(port)
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(args=SWIFTSHADER)
            page = browser.new_page(viewport=VIEWPORT, device_scale_factor=1)
            errors = []
            page.on("pageerror", lambda e: errors.append(f"pageerror: {e}"))
            page.on("console",
                    lambda m: errors.append(f"console.error: {m.text}") if m.type == "error" else None)
            url = f"http://127.0.0.1:{port}/?ui=cockpit&t=3.2"
            page.goto(url, wait_until="networkidle")
            page.wait_for_function("() => window.__office && window.__office.inject", timeout=20000)
            page.evaluate("w => window.__office.inject(w)", world)
            page.wait_for_selector(".cproom", timeout=20000)
            page.wait_for_timeout(400)

            # ── 3: 台帳が空のとき「セッションはまだありません」が見える（display:none で消えていないか） ──
            empty_world = copy.deepcopy(world)
            empty_world["employees"] = []
            empty_world["roster"] = []
            page.evaluate("w => window.__office.inject(w)", empty_world)
            page.wait_for_timeout(200)
            empty = page.evaluate("""() => {
              const e = document.querySelector('#agents .pxempty');
              if (!e) return { found: false };
              const cs = getComputedStyle(e);
              return { found: true, display: cs.display, text: e.textContent };
            }""")
            check(empty.get("found") and empty.get("display") != "none" and bool(empty.get("text")),
                  "3: 台帳が空のとき案内文が見える（display:none の重複セレクタで消えていない）",
                  json.dumps(empty, ensure_ascii=False))
            page.evaluate("w => window.__office.inject(w)", world)
            page.wait_for_timeout(200)

            # ── 9: 台帳は roving tabindex（Tab は 1 か所だけ・矢印キーで行をまたぐ） ──
            rove0 = page.evaluate("""() => {
              const rows = [...document.querySelectorAll('#agents .pxrow')];
              return { total: rows.length, zeros: rows.filter((r) => r.tabIndex === 0).length };
            }""")
            check(rove0["total"] > 1 and rove0["zeros"] == 1,
                  f"9: 台帳 {rove0['total']} 行のうち tabIndex=0 はちょうど 1 個（roving tabindex）",
                  json.dumps(rove0))
            page.evaluate("() => document.querySelector('#agents .pxrow[tabindex=\"0\"]').focus()")
            page.keyboard.press("ArrowDown")
            page.wait_for_timeout(80)
            rove1 = page.evaluate("""() => {
              const rows = [...document.querySelectorAll('#agents .pxrow')];
              return { zeros: rows.filter((r) => r.tabIndex === 0).length,
                activeIsRow: document.activeElement?.classList.contains('pxrow') || false };
            }""")
            check(rove1["zeros"] == 1 and rove1["activeIsRow"],
                  "9: ArrowDown で次の行へフォーカスが移り、tabIndex=0 もその行だけに移る",
                  json.dumps(rove1))

            # ── a11y: 行に aria-label・バッジ（.mono）は aria-hidden ──
            a11y = page.evaluate("""() => {
              const row = document.querySelector('#agents .pxrow');
              const mono = row?.querySelector('.mono');
              return { label: row?.getAttribute('aria-label') || '', title: row?.title || '',
                monoHidden: mono?.getAttribute('aria-hidden') };
            }""")
            check(bool(a11y["label"]) and a11y["label"] == a11y["title"] and a11y["monoHidden"] == "true",
                  "a11y: 台帳の行に aria-label（title と同文）・頭文字バッジは aria-hidden",
                  json.dumps(a11y, ensure_ascii=False))

            # ── 11: 指示欄（#composeinput）は複数行のtextarea。Shift+Enter=改行・Enter=送信 ──
            page.evaluate("() => document.querySelector('#agents .pxrow').click()")
            page.wait_for_selector("#sheet:not([hidden])", timeout=5000)
            tag = page.evaluate("() => document.querySelector('#composeinput').tagName")
            check(tag == "TEXTAREA", "11: #composeinput は textarea（1行の input から変更）", tag)
            # レビュー指摘(fix): 本文が空のまま Enter を押しても改行が入らない（textarea の既定を止める）
            page.click("#composeinput")
            page.keyboard.press("Enter")
            empty_enter_val = page.evaluate("() => document.querySelector('#composeinput').value")
            check(empty_enter_val == "",
                  "11-fix: 空の #composeinput で Enter を押しても改行が入らない（input の頃の no-op を維持）",
                  json.dumps(empty_enter_val, ensure_ascii=False))
            page.keyboard.type("1行目")
            page.keyboard.press("Shift+Enter")
            page.keyboard.type("2行目")
            val_before = page.evaluate("() => document.querySelector('#composeinput').value")
            check("\n" in val_before and "1行目" in val_before and "2行目" in val_before,
                  "11: Shift+Enter は改行を入れる（送信されない）", json.dumps(val_before, ensure_ascii=False))
            grew = page.evaluate("""() => {
              const t = document.querySelector('#composeinput');
              return t.scrollHeight > 20;
            }""")
            check(grew, "11: 複数行を入れると textarea が自動で伸びる（scrollHeight が育つ）")
            # 送信・二重送信防止（sheet.js/send.js）は壊していないことを確かめる＝複数行のまま普通の Enter で送れる
            posted = []
            page.route("**/api/instruct", lambda route: (
                posted.append(json.loads(route.request.post_data or "{}")),
                route.fulfill(status=200, content_type="application/json", body="{}")))
            page.keyboard.press("Enter")
            page.wait_for_function(
                "() => document.querySelector('#sheet')?.hidden === true", timeout=5000)
            after_val = page.evaluate("() => document.querySelector('#composeinput').value")
            check(len(posted) == 1 and "1行目" in posted[0].get("text", "") and "2行目" in posted[0].get("text", "")
                  and after_val == "",
                  "11: 複数行のまま Enter で送信され（/api/instruct に1件）・シートが閉じ・欄が空になる",
                  json.dumps({"posted": posted, "after": after_val}, ensure_ascii=False))
            page.unroute("**/api/instruct")

            # ── レビュー指摘(fix): compose 入力中は ⌘K（Ctrl+K）がフォーカスを奪わない ──
            page.evaluate("() => document.querySelector('#agents .pxrow').click()")
            page.wait_for_selector("#sheet:not([hidden])", timeout=5000)
            page.click("#composeinput")
            page.keyboard.type("draft text")
            page.keyboard.press("Control+k")
            page.wait_for_timeout(100)
            cmdk_during_compose = page.evaluate("""() => ({
              focus: document.activeElement?.id,
              val: document.querySelector('#composeinput').value,
            })""")
            check(cmdk_during_compose["focus"] == "composeinput" and cmdk_during_compose["val"] == "draft text",
                  "12-fix: compose 入力中に ⌘K を押してもフォーカスは #composeinput に残る（ドラフトを奪わない）",
                  json.dumps(cmdk_during_compose, ensure_ascii=False))
            page.click("#sheetclose")
            page.wait_for_timeout(100)

            # ── 17: 台帳の列見出しは 2 回目クリックで降順・3 回目で既定へ戻る ──
            def col_order():
                return page.evaluate("""() => [...document.querySelectorAll('#agents .pxrow.proj .pname')]
                  .map((n) => n.textContent)""")
            default_order = col_order()
            page.click("#col-project")
            page.wait_for_timeout(120)
            asc = col_order()
            asc_state = page.evaluate("""() => { const b = document.querySelector('#col-project');
              return { on: b.classList.contains('on'), dir: b.dataset.dir }; }""")
            page.click("#col-project")
            page.wait_for_timeout(120)
            desc = col_order()
            desc_state = page.evaluate("""() => { const b = document.querySelector('#col-project');
              return { on: b.classList.contains('on'), dir: b.dataset.dir }; }""")
            page.click("#col-project")
            page.wait_for_timeout(120)
            back = col_order()
            back_state = page.evaluate("() => document.querySelector('#col-project').classList.contains('on')")
            attn_names = {n for n in default_order if n not in asc or n not in desc}
            same_order = (asc == list(reversed(desc)) if not attn_names
                          else [n for n in asc if n not in attn_names] == list(reversed([n for n in desc if n not in attn_names])))
            check(asc_state["dir"] == "asc" and desc_state["dir"] == "desc" and not back_state
                  and asc != desc and back == default_order,
                  "17: 1回目=昇順(▴)・2回目=降順(▾)・3回目=既定順に戻る（従来は2回目で既定に戻っていた）",
                  json.dumps({"default": default_order, "asc": asc, "desc": desc, "back": back,
                              "asc_state": asc_state, "desc_state": desc_state}, ensure_ascii=False))

            # ── 18: 「あなた待ち」の文言はヘッダーのチップだけ ──
            page.evaluate("w => window.__office.inject(w)", peak)
            page.wait_for_timeout(300)
            dup = page.evaluate("""() => {
              const chip = document.querySelector('#cp-attn-chip')?.textContent || '';
              const ledger = document.querySelector('#cp-ledger-meta')?.textContent || '';
              const roster = document.querySelector('#cp-roster-meta')?.textContent || '';
              return { chip, ledger, roster,
                dup: [ledger, roster].filter((t) => t && t === chip).length };
            }""")
            check(bool(dup["chip"]) and dup["dup"] == 0,
                  "18: ヘッダーのチップと台帳・一覧の見出しが同一文言で重複しない",
                  json.dumps(dup, ensure_ascii=False))
            page.evaluate("w => window.__office.inject(w)", world)
            page.wait_for_timeout(200)

            # ── 20: 端末タブが溢れているとき #cp-tabs に縁のフェード（mask-image）が掛かる ──
            page.evaluate("w => window.__office.inject(w)", peak)
            page.wait_for_timeout(300)
            tabs = page.evaluate("""() => {
              const r = document.querySelector('#cp-tabs-r');
              const t = document.querySelector('#cp-tabs');
              const cs = getComputedStyle(t);
              const mask = cs.maskImage || cs.webkitMaskImage || cs.getPropertyValue('-webkit-mask-image');
              return { overflowing: !!r && !r.hidden, mask: mask || 'none' };
            }""")
            check(tabs["overflowing"] and tabs["mask"] != "none",
                  "20: タブが溢れている（#cp-tabs-r が見える）とき #cp-tabs に mask-image のフェードが掛かる",
                  json.dumps(tabs, ensure_ascii=False))
            page.evaluate("w => window.__office.inject(w)", world)
            page.wait_for_timeout(200)

            # ── 12: 台帳の絞り込み（⌘K・/ で入る・あいまい一致・Esc で消す） ──
            page.evaluate("w => window.__office.inject(w)", peak)
            page.wait_for_timeout(300)
            before_rows = page.evaluate("() => document.querySelectorAll('#agents .pxrow').length")
            page.evaluate("() => document.activeElement && document.activeElement.blur()")
            page.keyboard.press("/")
            focused = page.evaluate("() => document.activeElement?.id")
            check(focused == "cp-ledger-search", "12: / キーで台帳の絞り込み欄にフォーカスが入る", focused)
            name0 = page.evaluate("() => document.querySelector('#agents .pxrow.proj .pname')?.textContent || ''")
            query = name0[: max(2, len(name0) // 2)] if name0 else ""
            if query:
                page.keyboard.type(query)
                page.wait_for_timeout(150)
                after = page.evaluate("""(q) => [...document.querySelectorAll('#agents .pxrow.proj .pname')]
                  .every((n) => n.textContent.toLowerCase().includes(q.toLowerCase()))""", query)
                after_rows = page.evaluate("() => document.querySelectorAll('#agents .pxrow').length")
                check(after and after_rows < before_rows,
                      f"12: 名前の一部『{query}』で絞り込むと一致する行だけが残る（{before_rows}→{after_rows}）",
                      str(after))
            page.keyboard.press("Escape")
            page.wait_for_timeout(150)
            cleared = page.evaluate("() => document.querySelector('#cp-ledger-search').value")
            check(cleared == "", "12: Esc で絞り込みを消す", cleared)
            page.evaluate("w => window.__office.inject(w)", world)
            page.wait_for_timeout(200)

            # ── 19: cockpit では initUsage の paintPins が死んだ書き込みをしない（iso は変えない） ──
            usage_probe = page.evaluate("""async () => {
              const mod = await import('/ui/hud/shell.js');
              const shell = document.createElement('div');
              shell.className = 'hud ui-cockpit';
              shell.innerHTML = '<div class="usage"><button id="usage-summary"></button>'
                + '<div id="gauges" hidden></div></div>';
              document.body.appendChild(shell);
              const { paintPins } = mod.initUsage({ shell, T: (k) => k });
              const before = shell.querySelector('#usage-summary').textContent;
              paintPins([{ who: 'claude', label: 'Claude', pct: 43 }]);
              const after = shell.querySelector('#usage-summary').textContent;
              shell.remove();
              return { before, after };
            }""")
            check(usage_probe["before"] == "" and usage_probe["after"] == "",
                  "19: cockpit の initUsage は paintPins が呼ばれても #usage-summary に書き込まない（死んだ経路を停止）",
                  json.dumps(usage_probe, ensure_ascii=False))

            r110_roster_pins(page, peak, world, check)
            r110_s6_pins(browser, port, check, errors)

            check(not errors, "console.error / pageerror = 0", "; ".join(errors[:3]))
            browser.close()
    finally:
        proc.terminate()

    print("✅ R106 cockpit-ui スモーク green" if not ng else f"❌ R106 cockpit-ui スモーク {ng} 件")
    return 1 if ng else 0


if __name__ == "__main__":
    sys.exit(main())
