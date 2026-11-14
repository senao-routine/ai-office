#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""R106 cockpit-logic: PC版5視点dogfoodで確認済みの不具合（担当=claude）の回帰ピン。

起動・fixture・route の書き方は tests/cockpit_smoke.py を写す（verify.sh ▶7 相当の Playwright スモーク）。

検査（番号は dogfood の確認済みID・報告書と同じ）:
   1. ❗トレイの長いコマンドは既定で畳まれ（省略の印）、普通の <button>（キーボードの Tab→Enter でも）で全文が開く
      （real-data-readonly-01）
   2. 1440×900・空オフィスで「詳しく」を展開した直後、部屋の絵がコピー欄を覆わない＝elementFromPoint が自分を返す
      （first-run-01・#cp-setup-slot 自身もResizeObserverの監視対象にした）
   4. 名簿（R110 で一覧を統合した 02）が空のとき cp_roster_empty の文言が実際に（可視で）描かれる（copy-consistency-03・
      名簿の空の案内は #cp-roster の .pxempty）
   6. ?demo=1 はブラウザの言語（ja-JP）に従って開く＝world.json の固定値 lang:"en" を敷かない（first-run-04）
   7. デモの「送信N件」と「最近の指示」の件数が矛盾しない（0件のまま、ではない）（first-run-05）
   8. ❗に数字キーで答えると、確認表示の後に自動で次の❗へ進む＝Jキー無しで複数件を数字キーだけで捌ける
      （power-user-01・二重送信防止＝回答済みは一度だけ届くことも見る）
  10. ロボ/タブ/一覧で選択セッションが変わると、下書きが無ければ開いている指示シートが新しい相手へ追従し、
      下書きが有れば黙って流用せず正直に閉じて知らせる（power-user-03）
  14. 接続断から NO CARRIER までの時間が、SSE接続後の遅いポーリング間隔でも閾値内（旧: 最大30秒）
      （copy-consistency-04）
  15. 設定の「画面」を押した直後に、言語に依らない印（待機カーソル・タブ見出し）が出る（first-run-06）
  16. ❗が0件のとき 04 受付に INBOX ZERO の安心の合図が出る（power-user-06・R110 で一覧の見出しから受付の窓へ）
  21. 台帳の「いま」に、分類できない英語の生テキストがそのまま出ない＝既知の定型へ倒す。日本語の生テキストは
      （分類できなくても）そのまま残す＝実データの情報を無言で消さない（real-data-readonly-04）

使い方: python3 tests/cockpit_r106_logic_smoke.py
"""
import json
import pathlib
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
from ui_shot import SWIFTSHADER, VIEWPORT, free_port, start_server  # noqa: E402

WORLD = ROOT / "tests" / "fixtures" / "world" / "basic.json"
FIXTURES = ROOT / "tests" / "fixtures" / "world"

# real-data-readonly-01 の実機（複数行の Bash コマンド）を模した、省略なしでは絶対に収まらない長さの1行。
# R110: 受付は左の列（×3 で 570px・×4 で 754px）へ移り、本文は 14px＝1 行に入る字数が増えた。約 260 字では 1440 の受付で
# 6.5em の箱をわずか 1 行しか越えず（91px の箱に 5 行）、窓の幅が少し広がるだけで「はみ出していない＝展開ボタンが付かない」
# に化ける＝×4 の幅（1 行約 88 字）でも 6.5em を越える長さ（367 字＝×3 で 6 行・×4 で 5 行）にする
LONG_CMD = ("bash -lc \"set -e; cd /Users/test/works/server && "
            "python3 tools/publish_snapshot.sh --check && "
            "rsync -av --delete dist/ user@staging.example.com:/var/www/release_candidate_long_path/ && "
            "ssh deploy@staging.example.com 'sudo systemctl restart release-candidate.service && journalctl -n 50' && "
            "curl -sf https://status.example.com/api/v1/deploys/notify --data env=staging\"")

# 別モデルレビュー（高・R106-1）の実機repro相当: 多段デプロイ/CIスクリプトのような `&&` 連結45個。
# LONG_CMD（1 行で足りる短さ）では展開後の丈がビューポートを超えず、ボタン押し出しの退行を検出できない。
EXTREME_LONG_CMD = "bash -lc \"" + " && ".join(
    f"echo step{i} && do_something_long_enough_for_a_real_deploy_script_{i}" for i in range(45)) + "\""


def copy(world):
    return json.loads(json.dumps(world))


def with_fields(world, session, **fields):
    """session の状態を書き換えた world の写し（roster の代表・内訳の3フィールドだけ・tests/cockpit_smoke.py と同じ型）"""
    w = copy(world)
    for r in w.get("roster", []):
        if r.get("session") == session:
            r.update(fields)
        for brief in r.get("sessions") or []:
            if isinstance(brief, dict) and brief.get("session") == session:
                brief.update({k: v for k, v in fields.items() if k in ("state", "age", "attention", "minions")})
    return w


def open_live(browser, port, world, errors, label, locale=None, life=False):
    """live ページ（?t= 無し）を開く。/api/office は body[0] を返す（tests/cockpit_smoke.py の open_live と同じ型）。"""
    body = [json.dumps(world, ensure_ascii=False)]
    page = browser.new_page(viewport=VIEWPORT, device_scale_factor=1, locale=locale)
    page.on("pageerror", lambda e: errors.append(f"pageerror({label}): {e}"))
    page.on("console",
            lambda m: errors.append(f"console.error({label}): {m.text}") if m.type == "error" else None)
    page.route("**/api/office*", lambda route: route.fulfill(
        status=200, content_type="application/json; charset=utf-8", body=body[0]))
    page.goto(f"http://127.0.0.1:{port}/?ui=cockpit" + ("" if life else "&life=0"), wait_until="domcontentloaded")
    page.bring_to_front()
    page.wait_for_function("() => window.__office && window.__office.inject", timeout=20000)
    page.wait_for_selector(".cproom", timeout=20000)

    def inject(w):
        body[0] = json.dumps(w, ensure_ascii=False)
        page.evaluate("w => window.__office.inject(w)", w)

    inject(world)
    page.wait_for_timeout(150)
    return page, inject


def open_fixed(browser, port, world, errors, label):
    """固定時刻のページ（?t=・golden と同じ描き方。動かないので計測が安定する）。"""
    page = browser.new_page(viewport=VIEWPORT, device_scale_factor=1)
    page.on("pageerror", lambda e: errors.append(f"pageerror({label}): {e}"))
    page.on("console",
            lambda m: errors.append(f"console.error({label}): {m.text}") if m.type == "error" else None)
    page.goto(f"http://127.0.0.1:{port}/?ui=cockpit&t=3.2", wait_until="networkidle")
    page.wait_for_function("() => window.__office && window.__office.inject", timeout=20000)
    page.evaluate("w => window.__office.inject(w)", world)
    page.wait_for_selector(".cproom", timeout=20000)
    page.wait_for_timeout(150)
    return page


def check_1_tray_clamp(browser, port, basic, errors, check):
    """1: 長いコマンドは既定で畳まれ（.trayexpand が在る）、キーボード操作（フォーカス→Enter）で全文が開く。
    はみ出していない短い❗（別のセッション）には余計なボタンが付かない＝回帰ガード。"""
    # sess-fixture-0006 は既定で attention あり（短い質問）＝先頭に出ると長文側を見られない。消しておく
    world = with_fields(basic, "sess-fixture-0006", attention=False, question="")
    world = with_fields(world, "sess-fixture-0005", attention=True, question="",
                         ask={"tool": "Bash", "kind": "permission", "title": LONG_CMD, "options": []})
    page = open_fixed(browser, port, world, errors, "r106-1")
    try:
        m = page.evaluate("""() => {
          const span = document.querySelector('#attn .trayline');
          const btn = document.querySelector('#attn .trayexpand');
          if (!span || !btn) return { found: false };
          const before = { h: Math.round(span.getBoundingClientRect().height),
            overflow: getComputedStyle(span).overflow, ariaExpanded: btn.getAttribute('aria-expanded'),
            isButton: btn.tagName === 'BUTTON', cut: span.scrollHeight > span.clientHeight + 1 };
          btn.focus();
          const focused = document.activeElement === btn;
          return { found: true, before, focused, full: span.textContent };
        }""")
        check(m.get("found"), "1: ❗トレイの長いコマンドに展開ボタンが付く（real-data-readonly-01）",
              json.dumps(m, ensure_ascii=False))
        if m.get("found"):
            # R110 S1: cockpit の受付のトレイは grid で、本文（.trayline）は丈を 6.5em に絞って中でスクロールする
            # （overflow auto・ボタンは本文の下の行に常に見える）。畳み＝丈が絞られて全文が箱に入りきっていない（cut）こと
            check(m["before"]["isButton"] and m["before"]["overflow"] in ("hidden", "auto") and m["before"]["cut"]
                  and m["before"]["h"] < 120 and m["before"]["ariaExpanded"] == "false",
                  "1a: 既定は畳まれている（<button>・丈を絞って全文は箱に入りきらない・overflow hidden か auto・aria-expanded=false）",
                  json.dumps(m["before"], ensure_ascii=False))
            check(LONG_CMD in m.get("full", ""),
                  "1b: 畳んでいる間も全文はDOMに残っている（テキストを切り落とさない）", m.get("full", "")[:80])
            check(m.get("focused"), "1c: 展開ボタンはキーボードでフォーカスできる", json.dumps(m, ensure_ascii=False))
            page.keyboard.press("Enter")
            after = page.evaluate("""() => {
              const span = document.querySelector('#attn .trayline');
              const btn = document.querySelector('#attn .trayexpand');
              return { h: Math.round(span.getBoundingClientRect().height),
                overflow: getComputedStyle(span).overflow, ariaExpanded: btn.getAttribute('aria-expanded') };
            }""")
            # 展開後は overflow:auto（span 自身の中でスクロール）＝visible にはしない。
            # visible のまま丈を無制限にすると、長いコマンドで答えるボタンが画面外へ押し出される（別モデルレビュー・高）。
            check(after["ariaExpanded"] == "true" and after["overflow"] == "auto"
                  and after["h"] > m["before"]["h"] + 20,
                  "1d: キーボード（Enter）で全文を開ける（シートの「もっと見る」と同じ文法・span 自身がスクロール）",
                  json.dumps({"before": m["before"], "after": after}, ensure_ascii=False))
        # 短い❗（cockpit.json・はみ出さない）には展開ボタンが付かない＝常時出す退行をしていないこと
        short = page.evaluate("""() => document.querySelectorAll('#attn .trayexpand').length""")
    finally:
        page.close()
    short_world = copy(basic)
    page2 = open_fixed(browser, port, short_world, errors, "r106-1-short")
    try:
        n = page2.evaluate("() => document.querySelectorAll('#attn .trayexpand').length")
        check(n == 0, "1e: はみ出していない短い❗には展開ボタンを付けない（過剰な常時表示の回帰ガード）", f"n={n}")
    finally:
        page2.close()

    # 1f: 別モデルレビュー（高・R106-1）の実機repro＝多段デプロイ相当の `&&` 連結45個を展開しても、
    # 許可/拒否などの答えるボタンがビューポートの外へ押し出されず、実際にクリックで届く（マウス操作でリカバリ可能）。
    extreme_world = with_fields(basic, "sess-fixture-0006", attention=False, question="")
    extreme_world = with_fields(extreme_world, "sess-fixture-0005", attention=True, question="",
                                 ask={"tool": "Bash", "kind": "permission", "title": EXTREME_LONG_CMD, "options": []})
    page3 = browser.new_page(viewport={"width": 1440, "height": 900}, device_scale_factor=1)
    page3.on("pageerror", lambda e: errors.append(f"pageerror(r106-1f): {e}"))
    page3.route("**/api/office*", lambda route: route.fulfill(
        status=200, content_type="application/json; charset=utf-8",
        body=json.dumps(extreme_world, ensure_ascii=False)))
    try:
        page3.goto(f"http://127.0.0.1:{port}/?ui=cockpit&t=3.2", wait_until="networkidle")
        page3.wait_for_function("() => window.__office && window.__office.inject", timeout=20000)
        page3.evaluate("w => window.__office.inject(w)", extreme_world)
        page3.wait_for_selector(".cproom", timeout=20000)
        page3.wait_for_timeout(150)
        page3.click("#attn .trayexpand")
        page3.wait_for_timeout(150)
        # R110 S1: トレイは受付の窓（#win-reception・overflow hidden）の中の grid＝本文だけが中でスクロールし、ボタンは本文の
        # 下の行。画面の内でも窓に切られたボタンは押せない＝中心が窓の見えている矩形の内で、elementFromPoint がそのボタンに当たる
        hit = page3.evaluate("""() => {
          const vh = window.innerHeight;
          const win = document.querySelector('#attn').closest('.cpwin');
          const w = win ? win.getBoundingClientRect() : { top: 0, bottom: vh, left: 0, right: innerWidth };
          const btns = [...document.querySelectorAll('#attn button')].filter((b) => !b.hidden).map((b) => {
            const r = b.getBoundingClientRect(), cx = r.left + r.width / 2, cy = r.top + r.height / 2;
            const at = document.elementFromPoint(cx, cy);
            return { cls: b.className, bottom: Math.round(r.bottom),
              visible: r.width > 0 && r.bottom <= vh && cy >= w.top && cy <= w.bottom && cx >= w.left && cx <= w.right
                && !!at && (at === b || b.contains(at)),
              at: at ? (at.id || at.className || at.tagName) : null };
          });
          return { vh, win: win?.id || null, btns, allVisible: btns.length > 0 && btns.every((b) => b.visible) };
        }""")
        check(hit.get("allVisible"),
              "1f: 展開後も答えるボタン（許可/拒否/自由に書く 等）が全て受付の窓の内で中心が当たる＝マウスで押せる"
              "（45個連結の実運用相当・別モデルレビュー高・R110 の grid）",
              json.dumps(hit, ensure_ascii=False))
    finally:
        page3.close()


def check_2_setup_resize(browser, port, errors, check):
    """2: 1440×900・空オフィスで「詳しく」展開直後、コピー欄が部屋の絵の下に隠れて押せない（first-run-01）。
    #cp-setup-slot 自身もResizeObserverの対象にして、展開直後に部屋を縮め直す。"""
    empty = {
        "_note": "r106-2", "v": 2, "officeName": "r106", "lang": "ja", "generatedAt": 1790000000,
        "setup": {"hookInstalled": False, "eventsWired": False, "approvalWired": False},
        "avatarMode": "session", "employees": [], "roster": [], "history": [],
        "today": {"sent": 0, "answered": 0}, "counts": {}, "tasks": {},
    }
    page = browser.new_page(viewport={"width": 1440, "height": 900}, device_scale_factor=1)
    page.on("pageerror", lambda e: errors.append(f"pageerror(r106-2): {e}"))
    page.on("console",
            lambda m: errors.append(f"console.error(r106-2): {m.text}") if m.type == "error" else None)
    body = [json.dumps(empty, ensure_ascii=False)]
    page.route("**/api/office*", lambda route: route.fulfill(
        status=200, content_type="application/json; charset=utf-8", body=body[0]))
    try:
        page.goto(f"http://127.0.0.1:{port}/?ui=cockpit&life=0", wait_until="domcontentloaded")
        page.wait_for_function("() => window.__office && window.__office.inject", timeout=20000)
        page.wait_for_selector(".cproom", timeout=20000)
        page.wait_for_selector("#cp-setup-slot .sb-more", timeout=5000)
        page.click("#cp-setup-slot .sb-more")
        page.wait_for_timeout(500)  # ResizeObserver の rAF 待ち
        hit = page.evaluate("""() => {
          const copyBtn = document.querySelector('#cp-setup-slot .sb-copy');
          if (!copyBtn) return { found: false };
          const r = copyBtn.getBoundingClientRect();
          const top = document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2);
          return { found: true, hit: !!(top && (top === copyBtn || copyBtn.contains(top))),
            at: top ? (top.id || top.className || top.tagName) : null,
            rect: { w: Math.round(r.width), h: Math.round(r.height) } };
        }""")
        check(hit.get("found") and hit.get("hit"),
              "2: 1440×900 空オフィスで『詳しく』展開後、コピー欄が部屋の絵に隠れず当たる（first-run-01）",
              json.dumps(hit, ensure_ascii=False))
    finally:
        page.close()


def check_4_roster_empty(browser, port, basic, errors, check):
    """4: 名簿（R110 で一覧を統合）が空のとき cp_roster_empty の文言が実際に（可視で）描かれる（copy-consistency-03・
    名簿の空の案内は ui/hud/table.js の .pxempty＝px_no_rows と同じ文）"""
    empty = copy(basic)
    empty["roster"] = []
    empty["employees"] = []
    page, _ = open_live(browser, port, empty, errors, "r106-4")
    try:
        want = page.evaluate("""async () => {
          const { T: rawT } = await import('/ui/hud/strings.js');
          const T = (await import('/ui/cockpit/plain.js')).plainT(rawT);
          return T('cp_roster_empty');
        }""")
        got = page.evaluate("""() => {
          const el = document.querySelector('#cp-roster .pxempty');
          if (!el) return { found: false };
          const r = el.getBoundingClientRect();
          const cs = getComputedStyle(el);
          return { found: true, text: el.textContent, visible: cs.display !== 'none' && r.width > 0 && r.height > 0 };
        }""")
        check(got.get("found") and got.get("visible") and got.get("text") == want,
              "4: 名簿が空のとき cp_roster_empty の文言が可視で描かれる（copy-consistency-03・以前は実装漏れ）",
              json.dumps({"want": want, "got": got}, ensure_ascii=False))
    finally:
        page.close()


def check_6_7_demo(browser, port, errors, check):
    """6: ?demo=1 はブラウザの言語（ja-JP）に従って開く（world.json の固定値 lang:"en" を敷かない）。
    7: デモの「送信N件」と「最近の指示」の件数が矛盾しない。"""
    page = browser.new_page(viewport=VIEWPORT, device_scale_factor=1, locale="ja-JP")
    page.on("pageerror", lambda e: errors.append(f"pageerror(r106-6-7): {e}"))
    page.on("console",
            lambda m: errors.append(f"console.error(r106-6-7): {m.text}") if m.type == "error" else None)
    try:
        page.goto(f"http://127.0.0.1:{port}/?ui=cockpit&demo=1", wait_until="networkidle")
        page.wait_for_function("() => window.__office && window.__office.ready", timeout=20000)
        page.wait_for_timeout(300)
        state = page.evaluate("""() => {
          const w = window.__office.debug.world();
          return { lang: w?.lang, sent: w?.today?.sent ?? null, historyLen: (w?.history || []).length,
            histText: document.querySelector('#hist')?.textContent || '',
            pxToday: document.querySelector('#pxtoday')?.textContent || '' };
        }""")
        check(state.get("lang") == "ja", "6: ?demo=1 はブラウザ言語（ja-JP）に従って開く（first-run-04）",
              json.dumps(state, ensure_ascii=False))
        check((state.get("sent") or 0) > 0 and state.get("historyLen", 0) > 0,
              "7: デモの『送信N件』と『最近の指示』が矛盾しない（送信>0件なのに指示が0件、ではない・first-run-05）",
              json.dumps(state, ensure_ascii=False))
    finally:
        page.close()


def check_8_auto_advance(browser, port, peak, errors, check):
    """8: ❗に数字キーで答えると、確認表示の後に自動で次の❗へ進む（Jキー無しで複数件を数字キーだけで捌ける・
    power-user-01）。二重送信防止（同じ❗へもう一度同じキーを押しても増えない）も見る。"""
    sent = []
    page, _ = open_live(browser, port, peak, errors, "r106-8", life=False)
    page.route("**/api/instruct", lambda route: (sent.append("instruct"), route.fulfill(
        status=200, content_type="application/json; charset=utf-8", body='{"ok": true}'))[1])
    page.route("**/api/approval/reply", lambda route: (sent.append("approval"), route.fulfill(
        status=200, content_type="application/json; charset=utf-8", body='{"ok": true}'))[1])
    try:
        heads = []
        for _ in range(5):
            head = page.evaluate("() => document.querySelector('#attn .trayhead')?.textContent || ''")
            heads.append(head)
            page.keyboard.press("1")
            page.wait_for_timeout(900)   # ANSWER_ADVANCE_MS(700ms) を超えて待つ＝自動で次へ進むはず
        n_after_first_round = len(sent)
        # 二重送信防止: 最後に答えた❗はもう acts が無い（回答済み）ので、もう一度 '1' を押しても増えない
        page.keyboard.press("1")
        page.wait_for_timeout(300)
        check(len(sent) == 5, "8: ❗5件を数字キー『1』だけ（Jキー無し）で全部答えられる（power-user-01）",
              f"sent={sent}")
        check(len(set(heads)) == 5, "8a: 回答のたびに自動で次の❗へ進む（見出しが毎回違う人になる）",
              f"heads={heads}")
        check(len(sent) == n_after_first_round, "8b: 二重送信防止は壊れていない（回答済みへの再押下は届かない）",
              f"sent={sent}")
    finally:
        page.close()


def check_10_sheet_follow(browser, port, basic, errors, check):
    """10: 選択セッションが変わったとき、下書きが無ければ開いているシートが新しい相手へ追従し、
    下書きが有れば黙って流用せず正直に閉じて知らせる（power-user-03）。"""
    page, _ = open_live(browser, port, basic, errors, "r106-10")
    try:
        def select(session):
            page.evaluate("s => window.__office.debug.select(s, { focus: false })", session)

        def open_compose():
            page.click("#cp-compose")
            page.wait_for_selector("#sheet:not([hidden])", timeout=3000)

        def ensure_closed():
            """毎回同じ前提（シートは閉じている）から始める＝既に閉じていれば何もしない
            （#sheetclose は .sheet[hidden] の中なので、開いていないときにクリックするとタイムアウトする。
            [hidden] 属性の要素は Playwright の既定 state="visible" には永遠に合致しないため evaluate で見る）。"""
            if not page.evaluate("() => document.querySelector('#sheet').hidden"):
                page.click("#sheetclose")
                page.wait_for_function("() => document.querySelector('#sheet').hidden", timeout=3000)

        # (a) 下書きが有るとき: 新しい相手へ流用せず、閉じて知らせる
        select("sess-fixture-0001")
        open_compose()
        page.fill("#composeinput", "下書きのテスト本文")
        select("sess-fixture-0002")
        page.wait_for_timeout(250)
        state_a = page.evaluate("""() => {
          const sheet = document.querySelector('#sheet');
          const toast = [...document.querySelectorAll('#toast .tmsg')].map((t) => t.textContent);
          return { sheetHidden: sheet.hidden, draft: document.querySelector('#composeinput').value, toast };
        }""")
        warned = any(("下書き" in t) for t in state_a.get("toast", []))
        check(state_a.get("sheetHidden") and state_a.get("draft") == "" and warned,
              "10a: 下書きが有るときは新しい相手へ流用せず、正直に閉じて知らせる（power-user-03）",
              json.dumps(state_a, ensure_ascii=False))

        # (b) 下書きが無いとき: 開いているシートが新しい相手へ追従する
        select("sess-fixture-0001")
        open_compose()
        select("sess-fixture-0003")
        page.wait_for_timeout(250)
        state_b = page.evaluate("""() => ({
          sheetHidden: document.querySelector('#sheet').hidden,
          name: document.querySelector('#sheetname')?.textContent || '',
        })""")
        check(not state_b.get("sheetHidden") and "ブログ編集部" in state_b.get("name", ""),
              "10b: 下書きが無ければ、開いているシートが新しい相手へ追従する",
              json.dumps(state_b, ensure_ascii=False))

        # ★別モデルレビュー（中・R106-10）: (a)(b) は window.__office.debug.select を直接呼ぶだけで、
        # 本物の入口である台帳の行クリック（onPick）を経由しない＝その経路だけ select() の『閉じました』を
        # 無視してまた開き直す食い違いが残っていても検出できなかった。ここでは実際に .pxrow を
        # クリックして同じことを確かめる（page.click・DOM 経由）。(b) の後始末で開いたままのシートを閉じ、
        # 毎回同じ前提（シートは閉じている・選択は 0001）からやり直す。
        ensure_closed()
        select("sess-fixture-0001")
        # (c) 台帳の行クリック・下書きが有るとき: トーストどおりシートは閉じたまま（開き直さない）
        open_compose()
        page.fill("#composeinput", "下書きのテスト本文2")
        page.click('.pxrow[data-session="sess-fixture-0002"]')
        page.wait_for_timeout(250)
        state_c = page.evaluate("""() => {
          const sheet = document.querySelector('#sheet');
          const toast = [...document.querySelectorAll('#toast .tmsg')].map((t) => t.textContent);
          return { sheetHidden: sheet.hidden, draft: document.querySelector('#composeinput').value, toast };
        }""")
        warned_c = any(("下書き" in t) for t in state_c.get("toast", []))
        check(state_c.get("sheetHidden") and state_c.get("draft") == "" and warned_c,
              "10c: 台帳の行クリック（onPick）でも、下書きが有るときは閉じたまま＝トーストと実際のDOM状態が一致する"
              "（以前はここだけトースト『閉じました』の直後に無条件でまた開き直していた）",
              json.dumps(state_c, ensure_ascii=False))

        # (d) 台帳の行クリック・下書きが無いとき: シートが新しい相手へ追従する（従来どおり・退行していないこと）
        ensure_closed()
        select("sess-fixture-0001")
        open_compose()
        page.click('.pxrow[data-session="sess-fixture-0003"]')
        page.wait_for_timeout(250)
        state_d = page.evaluate("""() => ({
          sheetHidden: document.querySelector('#sheet').hidden,
          name: document.querySelector('#sheetname')?.textContent || '',
        })""")
        check(not state_d.get("sheetHidden") and "ブログ編集部" in state_d.get("name", ""),
              "10d: 台帳の行クリック（onPick）・下書きが無いときはシートが新しい相手へ追従する（退行ガード）",
              json.dumps(state_d, ensure_ascii=False))
    finally:
        page.close()


def check_14_no_carrier(browser, port, basic, errors, check):
    """14: 接続断から NO CARRIER までの時間が、SSE接続後の遅いポーリング間隔でも閾値内（旧: 最大30秒・
    copy-consistency-04）。間隔が長いとき（>=10秒）は1回の失敗でオフライン扱いにする。"""
    page, _ = open_live(browser, port, basic, errors, "r106-14")
    try:
        page.wait_for_function("() => window.__office.debug.pollMs === 15000", timeout=15000)
        page.unroute("**/api/office*")
        page.route("**/api/office*", lambda route: route.abort())
        page.route("**/api/events*", lambda route: route.abort())
        t0 = time.monotonic()
        try:
            page.wait_for_function("() => document.querySelector('#offbar') "
                                    "&& !document.querySelector('#offbar').hidden", timeout=21000)
            elapsed = time.monotonic() - t0
            check(elapsed < 21,
                  "14: 接続断から NO CARRIER までが、SSE接続後の遅い間隔でも閾値内（旧: 最大30秒・copy-consistency-04）",
                  f"elapsed={elapsed:.1f}s")
        except Exception as exc:  # noqa: BLE001 - playwright の TimeoutError
            check(False,
                  "14: 接続断から NO CARRIER までが、SSE接続後の遅い間隔でも閾値内（旧: 最大30秒・copy-consistency-04）",
                  f"21秒待っても #offbar が出ない: {exc}")
    finally:
        page.close()


def check_15_view_switch_cue(browser, port, basic, errors, check):
    """15: 設定の「画面」を押した直後に、言語に依らない印（待機カーソル・タブ見出し）が出る（first-run-06）。
    switchStyle() は location.replace() を呼ぶ＝一度戻ってくると（ナビゲーション中の実行コンテキストで
    Chromium が evaluate を止めてしまうので）古い画面はもう覗けない（実測＝document 型のリクエストを route で
    保留している間に evaluate すると無期限に応答しない）。before/after は switchStyle を呼ぶその場（location.replace
    の直前・直後・同じ1ティックの中）で取る。"""
    page, _ = open_live(browser, port, basic, errors, "r106-15")
    try:
        title_before = page.evaluate("() => document.title")
        state = page.evaluate("""async () => {
          const { switchStyle } = await import('/ui/platform/style.js');
          const before = { cursor: document.documentElement.style.cursor, title: document.title };
          switchStyle('iso');
          const after = { cursor: document.documentElement.style.cursor, title: document.title };
          return { before, after };
        }""")
        before, after = state.get("before", {}), state.get("after", {})
        check(before.get("cursor") != "wait" and after.get("cursor") == "wait"
              and after.get("title") != before.get("title") and before.get("title") == title_before,
              "15: 「画面」の切替（switchStyle）を呼んだ直後に印（待機カーソル・タブ見出し）が出る（first-run-06）",
              json.dumps(state, ensure_ascii=False))
    finally:
        page.close()


def check_16_inbox_zero(browser, port, basic, errors, check):
    """16: ❗が0件のとき INBOX ZERO の安心の合図が出る（power-user-06）。R110 骨格 A: 合図は 04 受付の窓（#cp-inbox-zero・
    ❗トレイの代わりに同じ場所）＝見えていて、トレイ（#attn）は hidden"""
    none_attn = copy(basic)
    for p in none_attn.get("roster", []):
        p["attention"] = False
        p["question"] = ""
        for s in p.get("sessions") or []:
            if isinstance(s, dict):
                s["attention"] = False
    page, _ = open_live(browser, port, none_attn, errors, "r106-16")
    try:
        got = page.evaluate("""() => {
          const z = document.querySelector('#cp-inbox-zero');
          const r = z ? z.getBoundingClientRect() : null;
          return { text: z?.textContent || '', shown: !!z && !z.hidden && r.width > 0 && r.height > 0,
            inReception: !!z?.closest('#win-reception'), trayHidden: !!document.querySelector('#attn')?.hidden };
        }""")
        check("INBOX ZERO" in got["text"] and got["shown"] and got["inReception"] and got["trayHidden"],
              "16: ❗が0件のとき受付（#win-reception）に INBOX ZERO の合図が見える・トレイは hidden（power-user-06）",
              json.dumps(got, ensure_ascii=False))
    finally:
        page.close()


def check_21_activity_fallback(browser, port, errors, check):
    """21: 台帳の『いま』に、分類できない英語の生テキストがそのまま出ない（既知の定型へ倒す）。
    日本語の生テキストは（分類できなくても）そのまま残す＝実データの情報を無言で消さない
    （real-data-readonly-04）。ui/core/world.js の純ロジックを直接見る。"""
    page = browser.new_page(viewport=VIEWPORT, device_scale_factor=1)
    page.on("pageerror", lambda e: errors.append(f"pageerror(r106-21): {e}"))
    try:
        page.goto(f"http://127.0.0.1:{port}/", wait_until="domcontentloaded")
        result = page.evaluate("""async () => {
          const { activityGloss } = await import('/ui/core/world.js');
          return {
            enRawKept: activityGloss({ verb: 'Polishing', target: 'the final slides', state: 'working' }, 'en'),
            jaRawSuppressed: activityGloss({ verb: 'Polishing', target: 'the final slides', state: 'working' }, 'ja'),
            jaRawKept: activityGloss({ verb: '確認中', target: 'パラメータ', state: 'working' }, 'ja'),
            jaWaitingFallback:
              activityGloss({ verb: 'Reviewing', target: 'the PR', state: 'waiting' }, 'ja'),
          };
        }""")
        check("Polishing" in result.get("enRawKept", ""),
              "21a: 英語UIでは分類できないverbの生テキストを残す（従来どおり・回帰ガード）",
              json.dumps(result, ensure_ascii=False))
        check("Polishing" not in result.get("jaRawSuppressed", "") and bool(result.get("jaRawSuppressed")),
              "21: 日本語UIで分類できない英語の生テキストが台帳にそのまま出ない（real-data-readonly-04）",
              json.dumps(result, ensure_ascii=False))
        check("確認中" in result.get("jaRawKept", ""),
              "21b: 分類できなくても日本語の生テキストはそのまま残す（実データの情報を無言で消さない）",
              json.dumps(result, ensure_ascii=False))
        check("Reviewing" not in result.get("jaWaitingFallback", ""),
              "21c: state=waiting でも英語の生テキストを残さない（作業中以外の経路も見る）",
              json.dumps(result, ensure_ascii=False))
    finally:
        page.close()


def main():
    from playwright.sync_api import sync_playwright

    basic = json.loads(WORLD.read_text(encoding="utf-8"))
    peak = json.loads((FIXTURES / "cockpit_peak.json").read_text(encoding="utf-8"))
    ng, errors = 0, []

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
            try:
                check_1_tray_clamp(browser, port, basic, errors, check)
                check_2_setup_resize(browser, port, errors, check)
                check_4_roster_empty(browser, port, basic, errors, check)
                check_6_7_demo(browser, port, errors, check)
                check_8_auto_advance(browser, port, peak, errors, check)
                check_10_sheet_follow(browser, port, basic, errors, check)
                check_14_no_carrier(browser, port, basic, errors, check)
                check_15_view_switch_cue(browser, port, basic, errors, check)
                check_16_inbox_zero(browser, port, basic, errors, check)
                check_21_activity_fallback(browser, port, errors, check)
            finally:
                browser.close()
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except Exception:  # noqa: BLE001
            proc.kill()

    real_errors = [e for e in errors if "Failed to load resource" not in e]
    check(not real_errors, "console.error / pageerror が無い", "; ".join(real_errors[:6]))

    print(f"\n{ng} 件 赤" if ng else "\nすべて green")
    return 0 if ng == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
