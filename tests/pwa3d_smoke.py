#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""R77: スマホPWAの部屋のスモーク（Playwright必須）。名前は 3D 時代のまま（relay_e2e・配線 lint・記録が参照する）。

R122-P3: 既定の3D、保存したドット絵、切替、3Dだけの読込失敗→ドット絵を検査する。
既存のドット絵の幾何・操作・安全検査はドット絵を明示的に選んで継続する。
「canvasが出た」ではなく **ロボットが実際に立ち、1タップのミニカードから回答・端末を開ける** まで見る
（絵だけ出て操作が死んでいる嘘greenを防ぐ）。両方の部品が読めない経路は pwa_smoke.py が担当。

使い方: python pwa3d_smoke.py <base_url> <device_id> <secret> <token> <out.png>
"""
import importlib.util
import inspect
import json
import re
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

try:
    from playwright.sync_api import TimeoutError as PWTimeout
    from playwright.sync_api import sync_playwright
except ImportError as exc:  # pragma: no cover
    print(f"✗ 3Dスモーク: Playwright import失敗: {exc}")
    sys.exit(1)


# R122-P3: スマホの部屋の既定は 3D（別の束）。SwiftShader（headless）の 3D は 1 コマ数秒（Mac の新しい画面で実測 rAF 3/s・evaluate 最長 11 秒）
# ＝クリックや状態の待ちが時間切れで落ちる。部屋の 3D そのものを見る節（_r122_office_modes）以外は、開く前に「ドット絵」を保存した状態で回す
# （既に値を入れている節はその値のまま＝空のときだけ入れる）。実機の GPU では問題ない。
PIXEL_PREF = "try { if (localStorage.getItem('aioffice.officeMode') === null) localStorage.setItem('aioffice.officeMode', 'pixel'); } catch (e) {}"
PIXEL_DEFAULT = [True]


def _install_pixel_default():
    from playwright.sync_api import Browser
    if getattr(Browser, "_r122_pixel", False):
        return
    orig_ctx, orig_page = Browser.new_context, Browser.new_page

    def new_context(self, *a, **k):
        c = orig_ctx(self, *a, **k)
        if PIXEL_DEFAULT[0]:
            c.add_init_script(PIXEL_PREF)
        return c

    def new_page(self, *a, **k):
        pg = orig_page(self, *a, **k)
        if PIXEL_DEFAULT[0]:
            pg.add_init_script(PIXEL_PREF)
        return pg
    Browser.new_context, Browser.new_page, Browser._r122_pixel = new_context, new_page, True


_install_pixel_default()



def _r87_dialog_pins(page):
    """R87-S5: 「💬 会話を見る」の 3 ピン（caps 無しで非表示／固定封書を注入すると本文が描かれる／
    err:denied は理由文言・開いた会話は残す）。暗号は本番の ui/pwa/dlg.js → ui/core/dialog_open.js を
    実ブラウザで踏む（両側 KAT と同じ固定ベクタ。期限 e だけ今へ寄せる＝AAD 外）。"""
    ng = 0
    K = json.loads((Path(__file__).parent / "fixtures" / "dialog_seal_kat.json").read_text(encoding="utf-8"))
    absent = page.evaluate("() => !document.getElementById('sh_dlg') && !(actionsView().caps || {}).dialog")
    if absent:
        print("  ✓ R87: caps.dialog 無し → 「💬 会話を見る」は出ない")
    else:
        print("  ✗ R87: caps.dialog が無いのにボタンが出ている")
        ng += 1
    got = page.evaluate("""async (K) => {
        // 固定ベクタの iat は過去なので、端末の時計を iat+5 秒に固定する（openBlob の iat+90 秒検査は本番のまま踏む）
        const saved = { canDlg: window.canDlg, canTermFor: window.canTermFor, getCred: window.getCred, SEL: window.SEL, now: Date.now };
        Date.now = () => (K.bundle.i + 5) * 1000;
        const wait = (ms) => new Promise((r) => setTimeout(r, ms));
        try {
          window.canDlg = () => true;
          window.canTermFor = () => false;             // R104: 互換の会話入口は端末が対象外の人だけ
          window.getCred = () => ({ d: K.deviceId, s: K.secretHex, t: "smoke" });
          window.SEL = Object.assign({}, window.SEL || {}, { session: K.targetSession });
          dlgButton(document.getElementById('quickbtns'), window.SEL);
          const button = !!document.getElementById('sh_dlg');
          const now = Date.now(), id2 = "deadbeef" + K.bundle.id.slice(8);
          const pend = {}; pend[K.bundle.id] = { session: K.targetSession, at: now }; pend[id2] = { session: K.targetSession, at: now };
          sessionStorage.setItem(DLG_KEY, JSON.stringify(pend));
          onDlg(JSON.stringify({ v: 1, items: [K.bundle] }));   // e も固定ベクタのまま（時計を合わせたので生きている）
          for (let i = 0; i < 150 && !document.querySelector('#shdlg .dlgm'); i++) await wait(100);
          const rows = [...document.querySelectorAll('#shdlg .dlgm')].map((n) => n.textContent);
          onDlg(JSON.stringify({ v: 1, items: [{ v: 1, id: id2, e: K.bundle.e, err: "denied" }] }));
          await wait(80);
          const errText = (document.querySelector('#shdlg .dlgerr') || {}).textContent || "";
          return { button, rows, errText, stillRows: document.querySelectorAll('#shdlg .dlgm').length,
                   pending: Object.keys(dlgPending()).length };
        } finally {
          window.canDlg = saved.canDlg; window.canTermFor = saved.canTermFor;
          window.getCred = saved.getCred; window.SEL = saved.SEL; Date.now = saved.now;
          sessionStorage.removeItem(DLG_KEY); Object.keys(DLG_PAGES).forEach((k) => delete DLG_PAGES[k]);
          const box = document.getElementById('shdlg'); if (box) { box.hidden = true; box.innerHTML = ""; }
          const b = document.getElementById('sh_dlg'); if (b) b.remove();
        }
      }""", K)
    msgs = K["page"]["messages"]
    if got["button"] and len(got["rows"]) == len(msgs) and msgs[0]["text"] in got["rows"][0]:
        print(f"  ✓ R87: 固定封書を端末秘密で開いて {len(msgs)} 件を描画（本番 openBlob・実ブラウザ）")
    else:
        print(f"  ✗ R87: 封書の描画が違う: button={got['button']} rows={got['rows'][:2]}")
        ng += 1
    if "OFF" in got["errText"] and got["stillRows"] == len(msgs) and got["pending"] == 0:
        print("  ✓ R87: err:denied は理由文言＋開いた会話は残す＋要求は消える")
    else:
        print(f"  ✗ R87: denied の扱いが違う: err={got['errText'][:40]!r} rows={got['stillRows']} pending={got['pending']}")
        ng += 1
    return ng

def _r103_terminal_pins(page, room_height):
    """R122-P2: 会話は全面の作業ビュー。見出し・本文・入力欄・戻る操作が画面内にある。"""
    ng = 0
    page.wait_for_timeout(250)
    frame = page.evaluate("""() => {
      const r = id => { const n = document.getElementById(id), b = n.getBoundingClientRect();
        return { l:b.left, r:b.right, t:b.top, b:b.bottom, h:b.height, w:b.width }; };
      const close = document.getElementById('term_close'), cr = close.getBoundingClientRect();
      const hit = document.elementFromPoint(cr.left + cr.width / 2, cr.top + cr.height / 2);
      return { term:r('termwrap'), head:r('termhead'), body:r('termbody'), foot:r('termfoot'), field:r('termsay'),
        close:{ h:cr.height, w:cr.width, name:close.getAttribute('aria-label'), hit:!!hit && (hit === close || close.contains(hit)) },
        name:document.getElementById('termname').textContent, state:document.getElementById('termstate').textContent,
        split:splitOffice(), session:TERM && TERM.session, expected:TERM && dispCrew(TERM.e),
        returnView:TERM && TERM.returnView, iw:innerWidth, ih:innerHeight };
    }""")
    good = (not frame["split"] and frame["term"]["l"] >= -1 and frame["term"]["r"] <= frame["iw"] + 1
            and frame["term"]["t"] >= -1 and frame["term"]["b"] <= frame["ih"] + 1
            and frame["head"]["b"] <= frame["body"]["t"] + 1 and frame["body"]["b"] <= frame["foot"]["t"] + 1
            and frame["field"]["h"] >= 44 and frame["field"]["b"] <= frame["ih"] + 1
            and frame["close"]["h"] >= 44 and frame["close"]["w"] >= 44 and frame["close"]["hit"]
            and frame["close"]["name"] in ("戻る", "Back") and frame["name"] and frame["state"])
    if good:
        print(f"  ✓ R122-P2 会話の名前・状態・戻る・本文・44pxの入力欄が画面内（{frame['session']}）")
    else:
        print(f"  ✗ R122-P2 会話の見出し・本文・入力欄の位置が違う: {frame}")
        ng += 1
    # 送る仕組みと履歴は既存の term 検査が引き続き確認する。
    page.locator("#term_close").click()
    page.wait_for_timeout(250)
    restored = page.evaluate("""() => ({ view:VIEW, term:!!TERM,
      open:document.getElementById('termwrap').classList.contains('open'), split:splitOffice() })""")
    if restored["view"] == frame["returnView"] and not restored["term"] and not restored["open"] and not restored["split"]:
        print("  ✓ R122-P2 戻ると会話を閉じ、開く前の画面へ戻る")
    else:
        print(f"  ✗ R122-P2 会話から戻った画面が違う: {restored}")
        ng += 1
    return ng


def _r100_term_pins(page, device_id, secret):
    """R100-1a: ターミナル画面の 4 ピン。Mac が封じる形（server/dialog_seal.seal・kind:"term"）をこの場で作り、
    実ブラウザの onDlg → 本番 openBlob → termPaint まで踏む。/instruct は route で受け止めて外へ出さない。
      ① 開くと kind:"term"・after:null の封筒が 1 通（dlg-<reqId> 宛・署名は本物の端末秘密）
      ② 行は textContent（`<img onerror>` が文字のまま・img 要素が生えない）・末尾へ自動スクロール
      ③ 上へ戻して差分が来たら自動スクロールせず「↓ 新着 N」・次の要求は after＝前のカーソル
      ④ 入力欄の送信は既存 send()（宛先はセッションそのもの＝言葉を届けるだけ）"""
    import time as _t
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "server"))
    import dialog_seal  # noqa: E402
    ng = 0
    if not dialog_seal.AVAILABLE:
        print("  · R100-1a: 封緘バックエンドが無い環境＝ターミナル画面のピンは省略")
        return 0
    sent = []

    def on_instruct(route):
        try:
            sent.append(json.loads(route.request.post_data or "{}"))
        except ValueError:
            sent.append({})
        route.fulfill(status=200, content_type="application/json", body='{"ok":true}')

    page.route("**/instruct", on_instruct)
    session = "sess-e2e-term1"
    try:
        page.evaluate("""(s) => {
            window.__termSaved = { canDlg: window.canDlg, activity: window.__activity };
            // R121: 橋の未到着時も空にしない旧描画の経路を、このピンでは明示して検査する。
            window.__activity = null;
            window.canDlg = () => true;
            openTerm({ session: s, disp: "端末テスト", state: "working", kind: "tool", feed: [] });
        }""", session)
        for _ in range(50):
            if sent:
                break
            page.wait_for_timeout(100)
        env = sent[0] if sent else {}
        act = json.loads(env.get("text") or "{}") if env else {}
        rid = act.get("reqId", "")
        if (len(sent) == 1 and env.get("session") == f"dlg-{rid}" and act.get("kind") == "term"
                and act.get("session") == session and act.get("after") is None):
            print("  ✓ R100-1a ターミナルを開くと kind:term・after:null の封筒が 1 通（dlg- 宛）")
        else:
            print(f"  ✗ R100-1a 開いたときの要求が違う: n={len(sent)} env={str(env)[:120]} act={act}")
            return ng + 1

        def seal_and_inject(req_id, page_obj):
            iat = int(_t.time())
            b = dialog_seal.seal(secret, device_id, req_id, session, page_obj, iat=iat)
            page.evaluate("(p) => onDlg(JSON.stringify({ v: 1, items: [p] }))", b)

        evil = '<img src=x onerror="window.__pwned=1">'
        rows = [{"k": "u", "t": "npm test を回して"}, {"k": "a", "t": "回します"},
                {"k": "tool", "n": "Bash", "t": "npm test"}, {"k": "res", "t": "ok 1\nok 2\nok 3\nok 4\nok 5", "nl": 5},
                {"k": "a", "t": evil}] + [{"k": "a", "t": f"行 {i}"} for i in range(60)]
        cur1 = {"off": 1000, "h": "0123456789abcdef"}
        seal_and_inject(rid, {"kind": "term", "session": session, "reqId": rid, "iat": int(_t.time()),
                              "rows": rows, "cur": cur1, "reset": True, "gap": False})
        page.wait_for_function("() => document.querySelectorAll('#termbody .tl').length >= 60", timeout=8000)
        got = page.evaluate("""() => { const b = document.getElementById('termbody');
            return { n: b.querySelectorAll('.tl').length, img: b.querySelectorAll('img').length,
              pwned: !!window.__pwned, evil: [...b.querySelectorAll('.tl')].some(n => n.textContent.includes('<img')),
              bottom: b.scrollHeight - b.scrollTop - b.clientHeight < 24, pill: document.getElementById('termnew').hidden,
              fold: !!b.querySelector('.tl.res .tmore') }; }""")
        if got["img"] == 0 and not got["pwned"] and got["evil"] and got["bottom"] and got["pill"] and got["fold"]:
            print(f"  ✓ R100-1a 行は textContent（<img onerror> は文字のまま）・{got['n']} 行・末尾へ自動スクロール・結果は畳む")
        else:
            print(f"  ✗ R100-1a 描画が違う: {got}")
            ng += 1
        # ③ 上へ戻す → 次の要求（after＝前のカーソル）→ 差分 → ピル
        page.evaluate("() => { const b = document.getElementById('termbody'); b.scrollTop = 0; termScrolled(); termSchedule(0); }")
        for _ in range(50):
            if len(sent) >= 2:
                break
            page.wait_for_timeout(100)
        act2 = json.loads(sent[1].get("text") or "{}") if len(sent) >= 2 else {}
        if act2.get("after") == cur1 and act2.get("kind") == "term":
            print("  ✓ R100-1a 次の要求は after＝前のカーソル（差分だけ取りに行く）")
        else:
            print(f"  ✗ R100-1a 2 通目の after が違う: {act2}")
            ng += 1
        if act2.get("reqId"):
            seal_and_inject(act2["reqId"], {"kind": "term", "session": session, "reqId": act2["reqId"],
                                            "iat": int(_t.time()), "rows": [{"k": "a", "t": "新しい行"}],
                                            "cur": {"off": 1200, "h": "fedcba9876543210"}, "reset": False, "gap": False})
            page.wait_for_function("() => !document.getElementById('termnew').hidden", timeout=8000)
            pill = page.evaluate("""() => ({ text: document.getElementById('termnew').textContent,
                top: document.getElementById('termbody').scrollTop,
                last: [...document.querySelectorAll('#termbody .tl')].pop().textContent })""")
            if "1" in pill["text"] and pill["top"] < 50 and "新しい行" in pill["last"]:
                print(f"  ✓ R100-1a 上へ戻している間は自動スクロールしない・「{pill['text']}」が出る")
            else:
                print(f"  ✗ R100-1a 新着の扱いが違う: {pill}")
                ng += 1
        # ⑤ R102-3（本人「Claude Code のセッションみたいに」）: v2 の行＝道具の中に結果（点が緑）・差分は行番号と ± の地・Todo・
        # 1 ターンの所要・2 分以上あいたら時刻の区切り・最下行の「作業中…」・行頭の記号に絵文字系を使わない
        t0 = int(_t.time()) - 600
        v2 = [{"k": "u", "t": "直して", "ts": t0},
              {"k": "tool", "n": "Edit", "t": "office_term.py", "id": "a1b2c3d4", "ts": t0 + 1},
              {"k": "res", "for": "a1b2c3d4", "f": "office_term.py", "add": 2, "rem": 1, "t": "office_term.py を更新（+2 −1）",
               "d": [[" ", 21, "KINDS = (1,)"], ["-", 22, "ROWS_MAX = 80"], ["+", 22, "ROWS_MAX = 150"], ["+", 23, "X = 1"]],
               "ts": t0 + 2},
              {"k": "tool", "n": "Bash", "t": "npm test", "id": "e5f6a7b8", "ts": t0 + 300},
              {"k": "res", "for": "e5f6a7b8", "err": 1, "t": "2 failed", "nl": 1, "ts": t0 + 301},
              {"k": "todo", "i": [["c", "直す"], ["i", "試す"], ["p", "報告"]], "t": "Todo を更新", "ts": t0 + 302},
              {"k": "turn", "ms": 192000, "t": "192秒 作業", "ts": t0 + 303},
              {"k": "tool", "n": "Bash", "t": "sleep 100", "id": "c9d0e1f2", "ts": t0 + 304}]
        page.evaluate("() => { closeTerm(true); }")
        n5 = len(sent)
        page.evaluate("""(s) => openTerm({ session: s, disp: "端末テスト", state: "working", kind: "tool", verb: "テスト中",
                                            age: 42, feed: [], permMode: "plan",
                                            work: { now: [], next: [], done: [],
                                                    counts: { pending: 1, in_progress: 1, completed: 2 } } })""", session)
        for _ in range(50):
            if len(sent) > n5:
                break
            page.wait_for_timeout(100)
        act3 = json.loads(sent[n5].get("text") or "{}") if len(sent) > n5 else {}
        if act3.get("reqId"):
            seal_and_inject(act3["reqId"], {"kind": "term", "v": 2, "session": session, "reqId": act3["reqId"],
                                            "iat": int(_t.time()), "rows": v2, "cur": {"off": 1400, "h": "0123456789abcdef"},
                                            "reset": True, "gap": False})
            page.wait_for_function("() => !!document.querySelector('#termbody .tdiff')", timeout=8000)
            v = page.evaluate("""() => { const b = document.getElementById('termbody');
                const edit = b.querySelector('[data-tid="a1b2c3d4"]'), bash = b.querySelector('[data-tid="e5f6a7b8"]'),
                      run = b.querySelector('[data-tid="c9d0e1f2"]');
                const marks = [...b.querySelectorAll('.tm')].map(n => n.textContent);
                return { editOk: !!edit && edit.classList.contains('ok') && !!edit.querySelector('.tdiff'),
                  adds: b.querySelectorAll('.tdiff .dl.add').length, dels: b.querySelectorAll('.tdiff .dl.del').length,
                  nums: [...b.querySelectorAll('.tdiff .dn')].map(n => n.textContent),
                  bashBad: !!bash && bash.classList.contains('bad'), running: !!run && run.classList.contains('run'),
                  todo: [...b.querySelectorAll('.ttodo .tc')].map(n => n.textContent).join(''),
                  sep: [...b.querySelectorAll('.tl.tsep')].map(n => n.textContent),
                  turn: (b.querySelector('.tl.turn') || {}).textContent || '',
                  head: (edit && edit.querySelector('.th') || {}).textContent || '',
                  live: (document.getElementById('termlive') || {}).textContent || '',
                  cnt: (document.querySelector('#termstate .tcnt') || {}).textContent || '',
                  mode: (document.querySelector('#termstate .tmode') || {}).textContent || '',
                  gapDisp: (() => { const g = document.createElement('div'); g.className = 'tl gap'; g.textContent = '案内';
                                    b.appendChild(g); const d = getComputedStyle(g).display; g.remove(); return d; })(),
                  emoji: marks.filter(m => /\\p{Extended_Pictographic}/u.test(m)) }; }""")
            ok = (v["editOk"] and v["adds"] == 2 and v["dels"] == 1 and v["nums"][:4] == ["21", "22", "22", "23"]
                  and v["bashBad"] and v["running"] and v["todo"] == "⊠▣□" and len(v["sep"]) == 1
                  and "3分12秒" in v["turn"] and v["head"].startswith("Update(office_term.py)")
                  and "テスト中" in v["live"] and not v["emoji"]
                  and v["cnt"] == "Todo 2/4" and v["gapDisp"] == "block" and v["mode"] == "計画モード")
            if ok:
                print(f"  ✓ R102-3 Claude Code の文法: {v['head']}＋差分（+2 −1・行番号）・失敗は朱・実行中・Todo {v['todo']}・"
                      f"区切り {v['sep'][0]}・{v['turn']}・最下行「{v['live'].strip()[:24]}」・絵文字の記号なし・"
                      f"見出し {v['cnt']}（work.counts の実際の形）・権限モード「{v['mode']}」・案内の行は格子にしない")
            else:
                print(f"  ✗ R102-3 v2 の行の描画が違う: {v}")
                ng += 1
        else:
            print("  ✗ R102-3 開き直したときの要求が来ない")
            ng += 1
        # ④ 入力欄 → 既存 send()（宛先はセッションそのもの）
        n0 = len(sent)
        page.fill("#termsay", "テストを回して結果を教えて")
        page.click("#term_send")
        for _ in range(50):
            if len(sent) > n0 and any(s.get("session") == session for s in sent[n0:]):
                break
            page.wait_for_timeout(100)
        words = [s for s in sent[n0:] if s.get("session") == session]
        if words and words[0].get("text") == "テストを回して結果を教えて":
            print("  ✓ R100-1a 入力欄の送信は既存の send()（セッションへ言葉を届ける）")
        else:
            print(f"  ✗ R100-1a 入力欄の送信が違う: {[str(s)[:80] for s in sent[n0:]]}")
            ng += 1
        # ⑥ R102-3「最近の動き」: 古い順＝最新が一番下。開いた直後にシート（実際にスクロールする箱）が末尾にいる
        lg = page.evaluate("""async () => { closeTerm(true);
            const savedCanDlg = window.canDlg; window.canDlg = () => false;
            try {
            SEL = { session: 'log-pin', disp: 'ログ', state: 'working', age: 3,   // feed は新しい順（feed[0]＝最新＝step40）
                    feed: Array.from({ length: 40 }, (_, i) => '実行中 step' + (40 - i)) };
            openLog(); await new Promise(r => setTimeout(r, 150));
            const sh = document.querySelector('#logwrap .sheet');
            const lines = [...document.querySelectorAll('#lgbody .tlg .tb')].map(n => n.textContent);
            const r = { top: sh.scrollTop, max: sh.scrollHeight - sh.clientHeight, first: lines[0], last: lines[lines.length - 1] };
            closeLog(); SEL = null; return r;
            } finally { window.canDlg = savedCanDlg; } }""")
        if lg["max"] > 0 and lg["top"] >= lg["max"] - 2 and lg["last"] == "実行中 step40" and lg["first"] == "実行中 step1":
            print(f"  ✓ R102-3 最近の動きは古い順・開いた直後に最新が見える（シートを末尾へ {lg['top']}/{lg['max']}）")
        else:
            print(f"  ✗ R102-3 最近の動きの位置が違う（最新が画面外）: {lg}")
            ng += 1
    finally:
        page.evaluate("""() => { try { closeTerm(true); } catch (_) {}
            if (window.__termSaved) { window.canDlg = window.__termSaved.canDlg;
              window.__activity = window.__termSaved.activity; delete window.__termSaved; }
            sessionStorage.removeItem(DLG_KEY); }""")
        page.unroute("**/instruct", on_instruct)
    return ng


def _r103_term_action_pins(page, device_id, secret):
    """R103-4: q行の選択肢／自由入力／許可と、署名済み送信行の3段・失敗・再送を実操作で見る。"""
    ng = 0

    def bad(message):
        nonlocal ng
        print("  ✗ R103-4 端末: " + message)
        ng += 1

    page.evaluate("""() => {
      window.__r103Term = { fetch: window.fetch, poll: window.poll, termRequest: window.termRequest,
        canDlg: window.canDlg, apvView: window.apvView, office: LAST_OFFICE, stale: STALE, offline: OFFLINE,
        prev: PEND_PREV, delivered: DELIVERED, lastStatus: LAST_ST, activity: window.__activity,
        posts: [], releases: [], fail: false };
      // R121: 旧行描画での送信・再送・遡りも橋が未到着の間は引き続き動く。
      closeTerm(true); window.__activity = null;
      window.poll = () => {}; window.termRequest = () => {}; window.canDlg = () => true;
      window.apvView = () => ({ caps: true, on: true, fps: [] });
      STALE = false; OFFLINE = false; PEND_PREV = {}; DELIVERED = {};
      LAST_ST = { ts: Date.now(), at: Date.now(), ago: 0, on: true };
      window.fetch = function(url, init) {
        if (String(url).split('?')[0] !== '/instruct') return __r103Term.fetch.apply(this, arguments);
        const env = JSON.parse(init.body); __r103Term.posts.push(env);
        if (env.session.startsWith('dlg-')) return Promise.resolve(new Response('{"ok":true}'));
        if (__r103Term.fail) { __r103Term.fail = false; return Promise.reject(new Error('smoke-send-failed')); }
        return new Promise(resolve => __r103Term.releases.push(() => resolve(new Response('{"ok":true}'))));
      };
    }""")
    q = {"session": "e2e-r103-q", "projectId": "e2e-r103-q", "disp": "質問テスト", "state": "waiting",
         "listening": True, "question": "どの案で進めますか?", "questionOptions": [{"label": "案A"}, {"label": "案B"}],
         "ask": {"tool": "AskUserQuestion", "kind": "question", "id8": "a1b2c3d4", "qn": 1}}

    def open_q(employee):
        page.evaluate("""e => {
          closeApprove(true); LAST_OFFICE = { employees: [e] }; openTerm(e);
          termPaint({ reset: true, rows: [{ k: 'q', t: e.question || '実行の確認', o: ['案A', '案B'] }] });
        }""", employee)

    def words():
        return page.evaluate("() => __r103Term.posts.filter(e => !e.session.startsWith('dlg-'))")

    def last_row():
        return page.evaluate("""() => {
          const n = [...document.querySelectorAll('#termbody .tl.tsend')].pop();
          return n ? { id: n.dataset.sendId, nonce: n.dataset.sendNonce, state: n.dataset.sendState,
            text: (n.querySelector('.tsendtext') || {}).textContent || '',
            status: (n.querySelector('.tsendstate') || {}).textContent || '',
            retry: !!n.querySelector('.tsendretry'), open: !!TERM && document.getElementById('termwrap').classList.contains('open'),
            sheet: document.getElementById('sheetwrap').classList.contains('open') } : null;
        }""")

    def release():
        page.evaluate("() => __r103Term.releases.splice(0).forEach(f => f())")
        page.wait_for_function("() => !SENDING", timeout=5000)

    try:
        open_q(q)
        opts = page.locator("#termbody .qactions button.qopt.quickoption")
        if opts.count() != 2:
            bad(f"質問のq行に選択肢2個がない: {opts.count()}")
        else:
            opts.nth(1).click()
            page.wait_for_function("() => __r103Term.posts.some(e => e.session === 'e2e-r103-q')", timeout=5000)
            env = words()[-1]
            ok, why, session, text = _verify(json.dumps(env), device_id, secret)
            try:
                answer = json.loads(text)
            except ValueError:
                answer = {}
            row = last_row()
            if not (ok and session == q["session"] and answer == {"aioffice": 1, "kind": "answer", "ask": "a1b2c3d4",
                    "text": "選択肢「案B」でお願いします。"} and row and row["open"] and not row["sheet"]
                    and row["state"] == "sending" and "送信中" in row["status"] and row["nonce"] == env.get("nonce")):
                bad(f"選択肢の宛先・答えの印・送信中行が違う: verify={ok}({why}) session={session} answer={answer} row={row}")
            else:
                print("  ✓ R103-4 q行の選択肢から署名付きid8回答を1通・端末を開いたまま本文に送信中の1行")
            page.evaluate("""() => termPaint({ reset: true,
              rows: [{ k:'u', t:'選択肢「案B」でお願いします。', ts:Math.floor(Date.now()/1000)-1 }] })""")
            historical = last_row()
            if not historical or historical["state"] != "sending":
                bad(f"送信前の同じ文で配達済みにしてしまう: {historical}")
            release()
            relay_row = last_row()
            page.evaluate("""() => {
              const e = Object.assign({}, TERM.e, { pending: true });
              LAST_OFFICE = { employees: [e] }; termOnStatus();
            }""")
            old_status = last_row()
            page.evaluate("""() => {
              const e = Object.assign({}, TERM.e, { pending: true });
              LAST_ST.ts += 1; LAST_ST.at = Date.now();
              LAST_OFFICE = { employees: [e] }; checkDelivery([e]); termOnStatus();
            }""")
            accepted = last_row()
            page.evaluate("""() => {
              const e = Object.assign({}, TERM.e, { pending: false });
              LAST_ST.ts += 1; LAST_ST.at = Date.now();
              LAST_OFFICE = { employees: [e] }; checkDelivery([e]); termOnStatus();
            }""")
            page.wait_for_function("() => !!document.querySelector('#termbody .tsend[data-send-state=delivered]')", timeout=5000)
            delivered = last_row()
            if not (relay_row and relay_row["state"] == "sending" and old_status and old_status["state"] == "sending"
                    and accepted and accepted["state"] == "accepted"
                    and "Mac が受け付けた" in accepted["status"] and delivered and delivered["state"] == "delivered"
                    and "届いた" in delivered["status"] and "✓" in delivered["status"]
                    and accepted["id"] == delivered["id"] == row["id"]
                    and accepted["nonce"] == delivered["nonce"] == env.get("nonce")):
                bad(f"中継ok→Mac受理→配達の3段が違う: relay={relay_row} old={old_status} accepted={accepted} delivered={delivered}")
            else:
                print("  ✓ R103-4 中継okでは送信中のまま・Macのpendingで受け付けた→解除で届いた✓（同じ1行）")
            page.evaluate("() => termPaint({ reset: true, rows: [{ k:'a', t:'Macの新しい本文' }] })")
            reset_row = last_row()
            if not (reset_row and reset_row["id"] == row["id"] and reset_row["state"] == "delivered"
                    and page.locator("#termbody .tl.tsend").count() == 1):
                bad(f"Macの本文resetで送信行が消える／重複する: {reset_row}")
            # 検収（反証つきレビュー）: 描き直した本文に同じ言葉の ❯ 行があれば、届いた送信行は戻さない（二重にしない）
            page.evaluate("""() => termPaint({ reset: true, rows: [{ k:'u', t:'選択肢「案B」でお願いします。' }, { k:'a', t:'了解' }] })""")
            dup = page.evaluate("""() => ({ sends: document.querySelectorAll('#termbody .tl.tsend').length,
              users: [...document.querySelectorAll('#termbody .tl.u:not(.tsend) .tb')].map(n => n.textContent.trim()) })""")
            if dup["sends"] != 0 or dup["users"].count("選択肢「案B」でお願いします。") != 1:
                bad(f"届いた送信行が本文の ❯ 行と二重になる: {dup}")
            else:
                print("  ✓ R103-4 届いた送信行は、本文に同じ ❯ 行が載ったら描き直しで戻さない（二重にしない）")
            page.locator("#termbody .qactions .qfree").click()
            page.locator("#termbody .qactions .tanswer").fill("自由入力の下書きを保持してください")
            draft = page.evaluate("""() => {
              termPaint({ reset: true, rows: [{ k:'q', t:TERM.e.question, o:['案A', '案B'] }] });
              const n = document.querySelector('#termbody .qactions .tanswer');
              return { text:n.value, visible:n.offsetParent!==null };
            }""")
            if draft != {"text": "自由入力の下書きを保持してください", "visible": True}:
                bad(f"本文更新でq行の自由入力が消える／畳まれる: {draft}")
            else:
                print("  ✓ R103-4 q行の自由入力は本文resetでも下書きと開いた状態を保持")
            # R121（別モデルレビュー medium）: 打っている回答欄は、同じ要求の状態更新（termOnStatus）でフォーカスと打った位置を失わない
            kept_focus = page.evaluate("""() => {
              const n = document.querySelector('#termbody .qactions .tanswer');
              n.focus(); n.setSelectionRange(3, 3);
              LAST_OFFICE = { employees: [TERM.e] }; termOnStatus(); termOnStatus();
              const a = document.activeElement;
              return { same: a === n, pos: a && typeof a.selectionStart === 'number' ? a.selectionStart : -1 };
            }""")
            if kept_focus != {"same": True, "pos": 3}:
                bad(f"同じ要求の状態更新で回答欄のフォーカス／打った位置が外れる: {kept_focus}")
            else:
                print("  ✓ R121 打っている回答欄は、同じ要求の状態更新でもフォーカスと打った位置を保つ")
            # 検収（反証つきレビュー）: 書いている途中で相手の要求が変わっても、書きかけは消さず新しい欄へ移してそう言う（自動では送らない）
            n_words = len(words())
            moved = page.evaluate("""() => {
              TERM.e = Object.assign({}, TERM.e, { question: '別の質問になりました', ask: Object.assign({}, TERM.e.ask, { id8: 'feedbeef' }) });
              termPaint({ reset: true, rows: [{ k:'q', t:'別の質問になりました', o:['案C'] }] });
              const n = document.querySelector('#termbody .qactions .tanswer'), m = document.querySelector('#termbody .qactions .tanswer-moved');
              return { text: n && n.value, visible: !!n && n.offsetParent !== null, note: m ? m.textContent : '' };
            }""")
            if not (moved["text"] == "自由入力の下書きを保持してください" and moved["visible"] and "要求が変わりました" in moved["note"]
                    and len(words()) == n_words):
                bad(f"要求が変わると書きかけが消える／黙って送る: {moved}")
            else:
                print("  ✓ R103-4 書いている途中で要求が変わっても書きかけを残し「要求が変わりました」と言う（送らない）")

        # 複数問・複数選択は既存と同じMacの案内＋読取専用の選択肢、自由入力だけ送れる。
        multi = dict(q, session="e2e-r103-multi", projectId="e2e-r103-multi", ask=dict(q["ask"], qn=2, multi=1, id8="b1c2d3e4"))
        open_q(multi)
        multi_ui = page.evaluate("""() => ({ note: !!document.querySelector('#termbody .qactions .qmulti'),
          readonly: document.querySelectorAll('#termbody .qactions .qopt.ro').length,
          buttons: document.querySelectorAll('#termbody .qactions button.qopt').length })""")
        if not (multi_ui["note"] and multi_ui["readonly"] == 2 and multi_ui["buttons"] == 0):
            bad(f"複数質問の既存の作法が違う: {multi_ui}")
        free = page.locator("#termbody .qactions .qfree")
        if free.count() and free.is_visible():
            free.click()
        page.locator("#termbody .qactions .tanswer").fill("1問目は案A、2問目は案Bで進めてください")
        before = len(words())
        page.locator("#termbody .qactions .tanswer-send").click()
        page.wait_for_function("n => __r103Term.posts.filter(e => !e.session.startsWith('dlg-')).length > n", arg=before, timeout=5000)
        env = words()[-1]
        ok, why, session, text = _verify(json.dumps(env), device_id, secret)
        try:
            answer = json.loads(text)
        except ValueError:
            answer = {}
        if not (ok and session == multi["session"] and answer.get("ask") == "b1c2d3e4"
                and answer.get("text") == "1問目は案A、2問目は案Bで進めてください" and len(words()) == before + 1
                and last_row() and last_row()["open"]):
            bad(f"複数質問の自由入力が違う: verify={ok}({why}) session={session} answer={answer}")
        else:
            print("  ✓ R103-4 複数質問のq行は既存のMac案内・読取専用選択肢・端末を閉じず自由入力で回答")
        release()
        # 別モデルレビュー: 別のセッションの端末へ切り替えて戻ると、q 行の書きかけが消えた（端末を開くたびに初期化）＝セッションごとにメモリで持つ
        open_q(q)
        back = page.evaluate("() => { const n = document.querySelector('#termbody .qactions .tanswer'); return n ? n.value : null; }")
        if back != "自由入力の下書きを保持してください":
            bad(f"別の端末へ切り替えて戻るとq行の書きかけが消える: {back!r}")
        else:
            print("  ✓ R103-4 別のセッションの端末へ切り替えて戻っても、q 行の書きかけは残る（メモリだけ）")

        # R104-2（洗い出し backlog-plan-text-missing）: 計画の本文の行（封書の中）＝先頭 8 行・「続きを読む」で全文
        plan_text = "\n".join("%d. 計画の手順 %d" % (i, i) for i in range(1, 21))
        plan_ui = page.evaluate("""(t) => { termPaint({ reset: true, rows: [{ k: 'plan', t }] });
          const body = document.querySelector('#termbody .tplan-body'), more = document.querySelector('#termbody .tplan-more');
          const head = body ? body.textContent.split('\\n').length : 0; if (more) more.click();
          return { head, full: body ? body.textContent === t : false, more: !!more,
            gone: !document.querySelector('#termbody .tplan-more') }; }""", plan_text)
        if plan_ui != {"head": 8, "full": True, "more": True, "gone": True}:
            bad(f"計画の本文の行が違う: {plan_ui}")
        else:
            print("  ✓ R104-2 計画の本文は端末の行に先頭 8 行・「… +N 行（タップで開く）」で全文（封書の中だけ・R113-B で言い回しを統一）")
        # R104-2（洗い出し backlog-term-depth）: いちばん上まで巻き戻すと before＝先頭の行の位置で遡りを頼み、届いた行を上に足す（読む位置は動かさない）
        n_posts = page.evaluate("() => __r103Term.posts.length")
        page.evaluate("""() => { termPaint({ reset: true, rows: Array.from({ length: 40 }, (_, i) => ({ k: 'a', t: '新しい行 ' + i })) });
          TERM.beg = { off: 4096, h: '0123456789abcdef' }; TERM.begLost = false; TERM.req = null;
          window.__canTermSaved = window.canTermFor; window.canTermFor = () => true;   // この検査の部屋は「会話を見せる」が OFF
          window.termRequest = __r103Term.termRequest;                                // 上の準備で止めた本物の要求に戻す
          const b = document.getElementById('termbody'); b.scrollTop = 0; termScrolled(); }""")
        try:
            page.wait_for_function("n => __r103Term.posts.length > n", arg=n_posts, timeout=8000)
        except Exception:  # noqa: BLE001  待ちが切れたら理由（状態）を赤に書く＝ブラウザの途中死として数えない
            pass
        dbg = page.evaluate("""() => ({ req: TERM && TERM.req, beg: TERM && TERM.beg, can: canTermFor(TERM.e), hidden: document.hidden,
          throttled: termThrottled(), top: document.getElementById('termbody').scrollTop, posts: __r103Term.posts.length })""")
        env = page.evaluate("n => __r103Term.posts[n] || {}", n_posts)
        ok, why, session, text = _verify(json.dumps(env), device_id, secret) if env else (False, "no post", "", "")
        try:
            req = json.loads(text)
        except (ValueError, TypeError):
            req = {}
        loading = page.evaluate("() => !!document.querySelector('#termbody .tolder')")
        older = page.evaluate("""() => { const b = document.getElementById('termbody'); b.scrollTop = 0;
          const anchor = [...b.querySelectorAll('.tl.a')][0], top0 = anchor.getBoundingClientRect().top;
          TERM.req = null; TERM.beg = null; TERM.begTop = true;                // 先頭まで来た頁（top:true）
          termPrepend([{ k: 'u', t: '古い指示' }, { k: 'tool', n: 'Bash', t: 'ls', id: 'aa11' }, { k: 'res', for: 'aa11', t: 'a.txt' }]);
          const rows = [...b.querySelectorAll('.tl')].map(n => n.className.split(' ').slice(1).join(' ') + ':' + n.textContent.slice(0, 12));
          return { rows: rows.slice(0, 4), moved: Math.round(anchor.getBoundingClientRect().top - top0),
            loaded: TERM.olderLoaded === true, running: b.querySelectorAll('.tl.tool.run').length }; }""")
        if not (ok and session.startswith("dlg-") and req.get("kind") == "term" and req.get("before") == {"off": 4096, "h": "0123456789abcdef"}
                and req.get("after") is None and loading and older["moved"] == 0 and older["loaded"]
                and "会話のはじまり" in older["rows"][0] and "古い指示" in older["rows"][1] and older["running"] == 0):
            bad(f"端末の遡りが違う: req={req} loading={loading} older={older} state={dbg}")
        else:
            print("  ✓ R104-2 いちばん上まで巻き戻すと before で遡り、届いた行を上に足す（読む位置は動かない・会話のはじまりの印）")
        # R104-3（別モデルレビュー）: ①遡りの再試行は同じ向き（before）で出し直す ②読んでいる間は削らない・削るときは遡った頁を丸ごと外してカーソルを戻す
        n_posts = page.evaluate("() => __r103Term.posts.length")
        page.evaluate("""() => { TERM.req = 'r-old'; TERM.reqAt = Date.now() - 30000; TERM.reqOlder = true;
          TERM.reqBefore = { off: 2048, h: '00112233445566aa', i: 1 }; TERM.beg = null; termRetry(); }""")
        try:
            page.wait_for_function("n => __r103Term.posts.length > n", arg=n_posts, timeout=8000)
        except Exception:  # noqa: BLE001
            pass
        env = page.evaluate("n => __r103Term.posts[n] || {}", n_posts)
        ok, why, session, text = _verify(json.dumps(env), device_id, secret) if env else (False, "no post", "", "")
        try:
            retry_req = json.loads(text)
        except (ValueError, TypeError):
            retry_req = {}
        trim = page.evaluate("""() => { const b = document.getElementById('termbody'), saved = TERM_ROWS_MAX;
          const savedE = TERM.e; TERM.e = Object.assign({}, TERM.e, { question: '', ask: null, state: 'working', approvalMin: 0 });   // 問いの行を足さない
          TERM.req = null; TERM.sends = []; termPaint({ reset: true, rows: Array.from({ length: 40 }, (_, i) => ({ k: 'a', t: '描き直し ' + i })) });
          TERM.beg = { off: 50, h: '0123456789abcdef', i: 0 };
          termPrepend(Array.from({ length: 5 }, (_, i) => ({ k: 'u', t: '遡った ' + i })), { off: 777, h: 'fedcba9876543210', i: 0 });
          TERM_ROWS_MAX = 24;                                                   // 上限 72（遡った後は 3 倍）
          b.scrollTop = 0;
          termPaint({ rows: Array.from({ length: 30 }, (_, i) => ({ k: 'a', t: '追加 ' + i })) });   // 75 行・読んでいる途中
          const reading = { n: b.childNodes.length, old: [...b.querySelectorAll('.tl.u')].filter(n => n.textContent.includes('遡った')).length };
          b.scrollTop = b.scrollHeight;
          termPaint({ rows: [{ k: 'a', t: '最後' }] });                          // 76 行・いちばん下＝遡った頁を丸ごと外す
          const out = { reading, n: b.childNodes.length, old: [...b.querySelectorAll('.tl.u')].filter(n => n.textContent.includes('遡った')).length,
            beg: TERM.beg, lost: TERM.begLost, first: b.firstChild && b.firstChild.textContent.slice(0, 8) };
          TERM_ROWS_MAX = saved; TERM.e = savedE; return out; }""")
        if not (ok and retry_req.get("before") == {"off": 2048, "h": "00112233445566aa", "i": 1} and retry_req.get("after") is None
                and trim["reading"] == {"n": 75, "old": 5} and trim["old"] == 0 and trim["n"] == 71
                and trim["beg"] == {"off": 777, "h": "fedcba9876543210", "i": 0} and trim["lost"] is False and "描き直し" in (trim["first"] or "")):
            bad(f"遡りの再試行・上限での削り方が違う: retry={retry_req} trim={trim}")
        else:
            print("  ✓ R104-3 遡りの再試行は同じ before・読んでいる間は削らない・削るときは遡った頁を丸ごと外してカーソルを戻す")
        page.evaluate("() => { if (window.__canTermSaved) window.canTermFor = window.__canTermSaved; window.termRequest = () => {}; TERM.req = null; }")
        release()                                                              # 握っていた遡りの要求を返す（後のピンへ持ち越さない）

        perm = dict(q, session="e2e-r103-perm-q", projectId="e2e-r103-perm-q", question="", questionOptions=[],
                    ask={"tool": "Bash", "kind": "permission", "remote": 1, "id8": "c1d2e3f4"})
        open_q(perm)
        page.wait_for_timeout(300)
        opened = page.evaluate("""() => { const c = document.getElementById('apvwrap');
          return { go: [...document.querySelectorAll('#termbody .qactions button')].filter(n => n.textContent.indexOf('許可の画面') >= 0).length,
                   session: APV && APV.session, terminal: TERM && TERM.session, open: !!c && c.classList.contains('open'),
                   slot: c && c.parentNode && c.parentNode.id }; }""")
        if opened != {"go": 0, "session": perm["session"], "terminal": perm["session"], "open": True, "slot": "apvslot_term"}:
            bad(f"端末の許可待ちに端末の下のカードが出ない（q 行の「許可の画面を開く」は撤去）: {opened}")
        else:
            print("  ✓ R116-S1 端末の許可待ちは端末の下に決裁カード（q 行の「許可の画面を開く」は無い・端末を閉じない）")

        # 通信失敗を本文に残し、もう一度から同じ言葉を1通だけ送る。
        worker = {"session": "e2e-r103-retry", "projectId": "e2e-r103-retry", "disp": "送信テスト", "state": "working", "feed": []}
        open_q(worker)
        page.evaluate("() => { __r103Term.fail = true; document.getElementById('termsay').value = 'テストを回して結果を教えて'; }")
        before = len(words())
        page.click("#term_send")
        page.wait_for_function("() => !SENDING && !!document.querySelector('#termbody .tsendretry')", timeout=5000)
        failed = last_row()
        if not (failed and failed["state"] == "failed" and "!" in failed["status"] and "送れませんでした" in failed["status"]
                and failed["retry"] and failed["text"] == "テストを回して結果を教えて"):
            bad(f"失敗した言葉ともう一度が行に残らない: {failed}")
        page.locator("#termbody .tsendretry").click()
        page.wait_for_function("n => __r103Term.posts.filter(e => !e.session.startsWith('dlg-')).length >= n + 2", arg=before, timeout=5000)
        retry = last_row()
        env = words()[-1]
        ok, why, session, text = _verify(json.dumps(env), device_id, secret)
        if not (ok and session == worker["session"] and text == "テストを回して結果を教えて" and len(words()) == before + 2
                and retry and retry["state"] == "sending" and retry["nonce"] == env.get("nonce") and retry["id"] == failed["id"]
                and page.locator("#termbody .tl.tsend").count() == 1):
            bad(f"もう一度の再送が違う: verify={ok}({why}) session={session} text={text!r} row={retry}")
        else:
            print("  ✓ R103-4 失敗は本文に!送れませんでした＋もう一度・同じ言葉を同じ1行から再送")
        release()
    finally:
        page.evaluate("""() => {
          __r103Term.releases.splice(0).forEach(f => f()); closeApprove(true); closeTerm(true);
          const s = __r103Term; window.fetch = s.fetch; window.poll = s.poll; window.termRequest = s.termRequest;
          window.canDlg = s.canDlg; window.apvView = s.apvView; window.__activity = s.activity;
          LAST_OFFICE = s.office; STALE = s.stale; OFFLINE = s.offline; PEND_PREV = s.prev; DELIVERED = s.delivered;
          LAST_ST = s.lastStatus;
          delete window.__r103Term;
        }""")
    return ng


def _r103_approval_action_pins(page, employee, fields, device_id, secret, sent, dialog_seal):
    """R103-4: 先読みは鍵＋ONの先頭要求に1回、開いた瞬間の全文と拒否の2秒取り消しを固定する。"""
    ng = 0

    def bad(message):
        nonlocal ng
        print("  ✗ R103-4 許可: " + message)
        ng += 1

    e = dict(employee, ask=dict(employee["ask"], id8=fields["askId"][:8]))
    key = e["session"] + "\n" + e["ask"]["id8"]
    page.evaluate("""e => {
      window.__r103Apv = { office: LAST_OFFICE, view: window.apvView, poll: window.poll, prefetch: APV_PREFETCH,
        employee: e, on: false };
      const fps = apvView().fps.slice(); APV_PREFETCH = {};
      window.poll = () => {}; window.apvView = () => ({ caps: true, on: __r103Apv.on, fps });
      LAST_OFFICE = { employees: [e], actions: { caps: { approve: 1 }, approve: { on: 0, until: Math.floor(Date.now()/1000)+3600, fps } } };
      closeApprove(true);
    }""", e)

    def asks(start):
        found = []
        for envelope in sent[start:]:
            try:
                inner = json.loads(envelope.get("text") or "{}")
            except ValueError:
                continue
            if inner.get("kind") == "ask":
                found.append((envelope, inner))
        return found

    def answers(start):
        return [env for env in sent[start:] if env.get("session") == e["session"]]

    try:
        before = len(sent)
        page.evaluate("() => { apvPrefetch(); apvPrefetch(); }")
        page.wait_for_timeout(100)
        off = len(asks(before))
        # R118（本人裁定 2026-10-13）: 許可鍵は要らない＝先読みを止めるのは OFF だけ（ON の後の先読みは下で 1 通だけ）
        page.evaluate("() => { __r103Apv.on = true; LAST_OFFICE.actions.approve.on = 1; }")
        if off:
            bad(f"OFFで先読みした: {off}")
        else:
            print("  ✓ R103-4 許可全文の先読みはOFFでは0通（R118: 許可鍵の有無は見ない）")
        first = dict(e, session="e2e-r103-first-off", projectId="e2e-r103-first-off", approvalMin=60,
                     ask=dict(e["ask"], remote=0, id8="deadbeef"))
        second = dict(e, approvalMin=1)
        order = page.evaluate("""([first, second]) => {
          LAST_OFFICE.employees = [first, second];
          apvPrefetch(); apvPrefetch(); return officeNeed(LAST_OFFICE.employees)[0].session;
        }""", [first, second])
        page.wait_for_timeout(100)
        if order != first["session"] or len(asks(before)) != 1:
            bad(f"先頭が Mac だけ（remote=0）のとき次の許可を 1 通だけ先読みしない: first={order} asks={len(asks(before))}")
        else:
            print("  ✓ R116-S1 先読みは焦点の件と次の件: 先頭が Mac だけ（remote=0）でも、次のスマホで許可できる件を 1 通だけ先読み")
        page.evaluate("""() => {
          LAST_OFFICE.employees = [__r103Apv.employee]; apvPrefetch(); apvPrefetch(); apvPrefetch(); }""")
        for _ in range(50):
            if asks(before):
                break
            page.wait_for_timeout(100)
        requests = asks(before)
        if len(requests) != 1:
            bad(f"同じaskIdの先読みが1通でない: {len(requests)}")
        else:
            envelope, request = requests[0]
            ok, why, target, text = _verify(json.dumps(envelope), device_id, secret)
            if not (ok and target == "dlg-" + request.get("reqId", "") and request.get("session") == e["session"]
                    and set(request) == {"aioffice", "kind", "reqId", "session"}):
                bad(f"先読みの封筒が本文を載せない既存ask経路でない: verify={ok}({why}) request={request}")
            iat = int(time.time())
            body = dict(fields, kind="ask", session=e["session"], reqId=request["reqId"], iat=iat)
            bundle = dialog_seal.seal(secret, device_id, request["reqId"], e["session"], body, iat=iat)
            page.evaluate("p => onDlg(JSON.stringify({ v: 1, items: [p] }))", bundle)
            page.wait_for_function("k => !!(APV_PREFETCH[k] && APV_PREFETCH[k].page)", arg=key, timeout=8000)
            page.wait_for_timeout(1400)  # 先読みから1.2秒以上経っても、開いた画面の猶予は消費しない。
            opened = page.evaluate("""e => { openApprove(e); return { text: document.getElementById('apvcanon').textContent,
              disabled: document.getElementById('apv_allow').disabled, img: document.querySelectorAll('#apvwrap img').length }; }""", e)
            page.wait_for_timeout(100)
            once = len(asks(before))
            page.wait_for_timeout(1300)
            ready = page.evaluate("() => !document.getElementById('apv_allow').disabled")
            if not (opened["text"] == fields["inputCanon"] and opened["disabled"] and opened["img"] == 0 and ready and once == 1):
                bad(f"先読みの全文・開いた画面の1.2秒・再要求なしが違う: immediate={opened} ready={ready} asks={once}")
            else:
                print("  ✓ R103-4 鍵＋ONの先頭要求だけ1回署名ask→本番復号・開いた瞬間に全文・許可はさらに1.2秒後")
            page.evaluate("() => { closeApprove(true); apvPrefetch(); apvPrefetch(); }")
            page.wait_for_timeout(100)
            if len(asks(before)) != 1:
                bad("開き閉じ／同じstatusで同じaskIdを先読みし直した")
            gone = page.evaluate("""() => { LAST_OFFICE.employees = [{ session: LAST_OFFICE.employees[0].session, state: 'working' }];
              apvPrefetch(); return Object.keys(APV_PREFETCH).length; }""")
            if gone:
                bad(f"要求が終わっても先読み全文を捨てない: {gone}件")
            else:
                print("  ✓ R103-4 同じaskIdの再先読みなし・要求終了で先読み全文を破棄")

        page.evaluate("""e => { LAST_OFFICE.employees = [e]; openApprove(e); }""", e)
        before = len(sent)
        page.click("#apvdeny")
        pending = page.evaluate("() => ({ shown: !document.getElementById('apvdenyundo').hidden, text: document.getElementById('apvdenycount').textContent })")
        page.click("#apvdenycancel")
        page.wait_for_timeout(2300)
        if not (pending["shown"] and "送信まで" in pending["text"] and "2" in pending["text"] and not answers(before)):
            bad(f"拒否1タップの2秒取り消しが違う: pending={pending} answers={len(answers(before))}")
        else:
            print("  ✓ R103-4 拒否1タップで送信まで2…・取り消すと回答0通")
        before = len(sent)
        page.click("#apvdeny")
        for _ in range(50):
            if answers(before):
                break
            page.wait_for_timeout(100)
        emitted = answers(before)
        ok, why, target, text = _verify(json.dumps(emitted[0]), device_id, secret) if emitted else (False, "no-request", "", "")
        try:
            answer = json.loads(text)
        except ValueError:
            answer = {}
        if not (ok and target == e["session"] and len(emitted) == 1 and answer.get("kind") == "answer"
                and answer.get("ask") == e["ask"]["id8"] and isinstance(answer.get("text"), str) and 0 < len(answer["text"]) <= 80
                and "decision" not in answer and not any(env.get("session", "").startswith("apv-") for env in sent[before:])):
            bad(f"拒否が既存send/id8の短い回答1通でない: verify={ok}({why}) target={target} answer={answer}")
        else:
            print("  ✓ R103-4 拒否は2秒後に既存sendの署名付きid8回答1通（allow の封筒を送らない）")

        page.evaluate("e => openApprove(e)", e)
        before = len(sent)
        page.click("#apvdenywrite")
        one_line = page.evaluate("() => { const n = document.getElementById('apvdenytext'); return n.tagName === 'INPUT' && n.type === 'text' && !document.getElementById('apvdenybox').hidden; }")
        page.fill("#apvdenytext", "削除はせず、差分を先に見せてください")
        page.click("#apvdenysend")
        undo = page.evaluate("() => !document.getElementById('apvdenyundo').hidden && document.getElementById('apvdenycount').textContent.includes('2')")
        for _ in range(50):
            if answers(before):
                break
            page.wait_for_timeout(100)
        emitted = answers(before)
        ok, why, target, text = _verify(json.dumps(emitted[0]), device_id, secret) if emitted else (False, "no-request", "", "")
        try:
            answer = json.loads(text)
        except ValueError:
            answer = {}
        if not (one_line and undo and ok and target == e["session"] and len(emitted) == 1 and answer == {"aioffice": 1, "kind": "answer",
                    "ask": e["ask"]["id8"], "text": "削除はせず、差分を先に見せてください"}
                and not any(env.get("session", "").startswith("apv-") for env in sent[before:])):
            bad(f"指示を書いて拒否の言葉・答えの印が違う: verify={ok}({why}) target={target} answer={answer}")
        else:
            print("  ✓ R103-4 指示を書いて拒否は1行入力→書いた言葉そのものを既存send/id8で1通")
    finally:
        page.evaluate("""() => { closeApprove(true); const s = __r103Apv;
          LAST_OFFICE = s.office; window.apvView = s.view; window.poll = s.poll; APV_PREFETCH = s.prefetch;
          delete window.__r103Apv;
        }""")
    return ng


def _r100_apv_pins(p, base_url, device_id, secret, token):
    """R100-1b スマホから許可: 実ブラウザで本番の app.js を踏み、出てきた封筒を **Mac 側の本物の検証**
    （server/office_approve.py の remote_set / approve）に通す＝JS と Python の食い違いを実機と同じ形の値で止める。
    R118（本人裁定 2026-10-13「Claude のスマホアプリと同じく、サクッと許可」「常に ON」）: 許可鍵の登録は無い・パスキーは 1 回も呼ばない。
      ① OFF の間はカードの状態が need-on（許可鍵の登録の段＝need-setup は無い）
      ② ON（apvRemoteSet・封筒だけ）→ rst- 封筒 → office_approve.remote_set が granted・期限なし（終わりに OFF も）
      ③ 封書 kind:"ask" を注入 → 全キーが textContent（<img onerror> が文字のまま）・押せるのは 1.2 秒後
      ④ 封書の inputSha が画面の文字列と違う → 送らない
      ⑤ 押す → apv- のタップの封筒 → office_approve.approve が granted（hook に書く回答が 1 通）
      ⑥ level:"mac" も全文が届いていれば危険の朱の 1 行＋1 タップ・判定できない形（macOnly=1）は押せない
    navigator.credentials（パスキー）の get／create は数える＝最後まで 0 回。localhost で開く（Mac の rp と同じ）。"""
    import secrets as _s
    import tempfile as _tf
    import time as _t
    from urllib.parse import urlsplit
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "server"))
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    home = _tf.mkdtemp(prefix="apv-smoke-")
    old_home = os.environ.get("OFFICE_HOME")
    os.environ["OFFICE_HOME"] = home
    try:
        import dialog_seal  # noqa: E402
        import office_approve as oa  # noqa: E402
        import approval_policy as ap  # noqa: E402
        from fake_authn import FakeAuthenticator  # noqa: F401,E402  (形の参照のみ)
        import importlib.util as _iu
        spec = _iu.spec_from_file_location("o_for_apv", str(Path(__file__).resolve().parents[1] / "server" / "office_server.py"))
        osv = _iu.module_from_spec(spec)
        spec.loader.exec_module(osv)
    except Exception as e:  # noqa: BLE001
        print(f"  ✗ R100-1b: 検証側の部品が読めない: {e}")
        return 1
    if not dialog_seal.AVAILABLE:
        print("  · R100-1b: 封緘バックエンドが無い環境＝許可ビューのピンは省略")
        return 0
    u = urlsplit(base_url)
    lbase = f"{u.scheme}://localhost:{u.port}"
    rp = ("localhost", lbase)
    now = int(_t.time())
    devices = {"version": 1, "devices": {device_id: {"secret": secret, "label": "smoke", "created": now,
                                                     "expires": now + 86400, "revoked": False}}}
    cred = json.dumps({"d": device_id, "s": secret, "t": token, "e": 0}, separators=(",", ":"))
    ng = 0
    browser = p.chromium.launch()
    sent = []

    def on_instruct(route):
        try:
            sent.append(json.loads(route.request.post_data or "{}"))
        except ValueError:
            sent.append({})
        route.fulfill(status=200, content_type="application/json", body='{"ok":true}')

    try:
        ctx = browser.new_context(viewport={"width": 390, "height": 844}, has_touch=True, is_mobile=True)
        page = ctx.new_page()
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.add_init_script("localStorage.setItem('aioffice.cred', " + json.dumps(cred) + ");")
        page.route("**/instruct", on_instruct)
        page.goto(lbase + "/app/", wait_until="domcontentloaded")
        page.wait_for_function("() => typeof apvRemoteSet === 'function' && typeof openApprove === 'function'", timeout=30000)
        # R118: パスキー（navigator.credentials）の呼び出しは数える＝最後まで 0 回
        page.evaluate("""() => { window.__credGets = 0; window.__credCreates = 0; const c = navigator.credentials;
            const g = c.get.bind(c), m = c.create.bind(c);
            c.get = (o) => { window.__credGets++; return g(o); }; c.create = (o) => { window.__credCreates++; return m(o); }; }""")
        # ① 条件がそろわない間は出ない
        # 中継の P7（relay_e2e が push 済み）の許可待ちの人＝定期取得のたびに「要求が終わった」扱いにならない
        e = {"session": "e2e-perm-0003", "disp": "E2E許可部", "state": "waiting", "kind": "tool",
             "ask": {"tool": "Bash", "kind": "permission", "remote": 1}}
        gh = page.evaluate("(e) => apvState(e)", e)
        if gh == "need-on":
            print("  ✓ R118 OFF の間はカードの状態が need-on（［ON にして確認］・許可鍵の登録の段＝need-setup は無い）")
        else:
            print(f"  ✗ R118 OFF なのに許可できる状態になる／登録の段が残る: {gh}")
            ng += 1
        def next_env(prefix, k):
            for _ in range(100):
                got = [x for x in sent if str(x.get("session", "")).startswith(prefix)]
                if len(got) >= k:
                    return got[k - 1]
                page.wait_for_timeout(100)
            return None
        # ② R118（本人裁定 2026-10-13「常に ON」）: ON もスマホから封筒だけ（rst-・パスキーも Face ID も無し）→ Mac 側の remote_set が granted・
        # 期限なし（until＝2100-01-01）・本文は aioffice・kind・reqId・on だけ（hours と WebAuthn の 4 項目は無い）
        n_rst = len([x for x in sent if str(x.get("session", "")).startswith("rst-")])
        page.evaluate("() => apvRemoteSet(true, 0)")
        on_env = next_env("rst-", n_rst + 1)
        on_res = (oa.remote_set(on_env, devices=devices, verify=osv.verify_envelope, rp=rp)
                  if on_env else ("none", "no envelope", {}))
        until = int(oa.remote_until())
        on_inner = json.loads((on_env or {}).get("text") or "{}")
        # 同じ封筒をもう一度送っても通らない（封筒の nonce の台帳＝replay）
        again = oa.remote_set(on_env, devices=devices, verify=osv.verify_envelope, rp=rp) if on_env else ("none", "", {})
        paint = page.evaluate("""(u) => { LAST_OFFICE.actions = Object.assign({}, LAST_OFFICE.actions || {},
              { approve: Object.assign({}, (LAST_OFFICE.actions || {}).approve || {}, { on: 1, until: u }) });
            apvBusyEnd(); apvPaintMain(); return { now: document.getElementById('st_apv_now').textContent,
              go: document.getElementById('st_apv_go').textContent, buttons: document.querySelectorAll('#st_apv_main button').length }; }""", until)
        # 別モデルレビュー: ok は中継が預かっただけ＝Mac の拒否（10 秒に 1 回）は結果を追って理由と今の状態を出す
        rate = page.evaluate("""() => { APV_RST_REQ = { reqId: 'r-rst-rate', on: false, at: Date.now() };
            LAST_OFFICE.actions = Object.assign({}, LAST_OFFICE.actions || {},
              { results: [{ reqId: 'r-rst-rate', kind: 'remote_set', state: 'refused', output: 'rate' }] }); apvOnStatus();
            return { hint: document.getElementById('st_apv_hint').textContent, left: APV_RST_REQ }; }""")
        if not ("少し待ってからもう一度" in rate["hint"] and rate["left"] is None):
            print(f"  ✗ R103-1 ON/OFF の拒否（rate）を画面に出さない: {rate}")
            ng += 1
        if (on_res[0] == "granted" and list(on_inner) == ["aioffice", "kind", "reqId", "on"] and on_inner.get("on") is True
                and until == oa.REMOTE_ALWAYS and again[:2] == ("refused", "replay")
                and paint["now"] == "ON（期限なし）" and paint["go"] == "OFF にする" and paint["buttons"] == 1):
            print("  ✓ R118 スマホの ON（封筒だけ・パスキーなし）→ Mac が受理・期限なし（「ON（期限なし）」・設定のボタンは［OFF にする］の 1 つ）・"
                  "同じ封筒の再送は replay")
        else:
            print(f"  ✗ R118 スマホの ON が違う: {on_res[:2]} inner={on_inner} until={until} again={again[:2]} 画面={paint}")
            ng += 1
        # 中継の定期取得が LAST_OFFICE を上書きする＝Mac の状態（caps.approve）は関数ごと差し替える（R118: 照合コード＝許可鍵は無い）。
        # R114-P: ON の期限（apvOnNow）も LAST_OFFICE から読む＝検査の間は ON に固定する（Mac 側は上の rst- で本当に ON）
        page.evaluate("""() => { window.__apvSaved = window.apvView;
            window.apvView = () => ({ caps: true, on: true, fps: [], enr: [] }); window.apvOnNow = () => true; }""")
        tap_ready = '"tap"' in inspect.getsource(oa)     # R114-S（Mac がタップの封筒を受ける）がこの木に合流しているか
        page.evaluate("() => closeApprove(true)")
        gh2 = page.evaluate("(e) => apvState(e)", e)
        gh3 = page.evaluate("(e) => apvState(e)", dict(e, ask=dict(e["ask"], remote=0)))
        if gh2 == "loading" and gh3 == "mac-only":
            print("  ✓ R118 caps.approve＋ON＋remote=1 なら封書を取りに行く状態（loading・許可鍵は要らない）・remote=0 は Mac だけ")
        else:
            print(f"  ✗ R116-S1 カードの状態の条件が違う: 揃った={gh2} remote=0={gh3}")
            ng += 1

        def open_with(page_fields, tamper=None):
            n0 = len(sent)
            page.evaluate("(e) => openApprove(e)", e)
            for _ in range(50):
                if len(sent) > n0:
                    break
                page.wait_for_timeout(100)
            asks_ = [json.loads(x.get("text") or "{}") for x in sent[n0:] if str(x.get("session", "")).startswith("dlg-")]
            req = next((a for a in reversed(asks_) if a.get("kind") == "ask"), {})
            rid = req.get("reqId", "")
            if not rid:
                raise RuntimeError("開いても kind:ask が出ない: " + repr([str(x.get("session", ""))[:8] for x in sent[n0:]])
                                   + " APV=" + repr(page.evaluate("() => APV && { req: APV.req, pre: !!APV.prefetch, page: !!APV.page, st: apvState(APV.e) }")))
            body = dict(page_fields, kind="ask", session=e["session"], reqId=rid, iat=int(_t.time()))
            if tamper:
                body.update(tamper)
            b = dialog_seal.seal(secret, device_id, rid, e["session"], body, iat=body["iat"])
            page.evaluate("(p) => onDlg(JSON.stringify({ v: 1, items: [p] }))", b)
            try:
                page.wait_for_function("() => document.getElementById('apvcanon').textContent.length > 0", timeout=8000)
            except PWTimeout:
                raise RuntimeError("全文が描かれない: " + repr(page.evaluate("""() => APV && { req: APV.req, page: !!APV.page, st: apvState(APV.e),
                    lv: APV.page && APV.page.level, mo: APV.page && APV.page.macOnly, sess: APV.session, host: APV_HOST,
                    note: document.getElementById('apvnote').textContent }""")) + " rid=" + rid)
            oa.mark_seen(device_id, body["askId"], body["iat"])      # Mac が封じた事実（daemon の _ask_sealed と同じ）
            return req

        evil = '<img src=x onerror="window.__pwned=1">'
        ti = {"command": "npm test", "description": evil, "timeout": 120000}
        canon = ap.input_canon("Bash", ti)
        ask_id = _s.token_hex(16)
        fields = {"askId": ask_id, "tool": "Bash", "inputCanon": canon, "inputSha": ap.input_sha(canon),
                  "inputBytes": len(canon.encode()), "cwdBase": "proj", "askTs": now, "level": "remote",
                  "reasons": [], "agentId": "", "askCount": 1}
        ng += _r103_approval_action_pins(page, e, fields, device_id, secret, sent, dialog_seal)
        req = open_with(fields)
        if req.get("kind") == "ask" and set(req) == {"aioffice", "kind", "reqId", "session"}:
            print("  ✓ R100-1b 開くと kind:ask の封筒が 1 通（askId も全文も要求に載せない）")
        else:
            print(f"  ✗ R100-1b 開いたときの要求が違う: {req}")
            ng += 1
        view = page.evaluate("""() => ({ img: document.querySelectorAll('#apvwrap img').length, pwned: !!window.__pwned,
            keys: [...document.querySelectorAll('#apvview .apvrest .ak')].map(n => n.textContent),
            box: (document.getElementById('apvbox') || {}).textContent || '',
            evil: document.getElementById('apvview').textContent.includes('<img'),
            dis: document.getElementById('apv_allow').disabled, why: document.getElementById('apvwhy').textContent,
            view: apvView(), note: document.getElementById('apvnote').textContent })""")
        page.wait_for_timeout(1400)
        dis2 = page.evaluate("() => document.getElementById('apv_allow').disabled")
        if (view["img"] == 0 and not view["pwned"] and view["evil"] and view["box"] == "npm test"
                and view["keys"] == ["description", "timeout"] and view["dis"] and not dis2):
            print(f"  ✓ R100-1b 全キーを textContent で描く（箱に命令・［全文］に {view['keys']}）・押せるのは 1.2 秒後")
        else:
            print(f"  ✗ R100-1b 許可ビューの描画が違う: {view} 1.4 秒後 disabled={dis2}")
            ng += 1
        def press_apv():
            """許可ボタンを押して apv- の封筒（無ければ None）と、その間のパスキーの呼び出し回数を返す。"""
            n0, g0 = len(sent), page.evaluate("() => window.__credGets")
            page.click("#apv_allow")
            for _ in range(100):
                if any(str(x.get("session", "")).startswith("apv-") for x in sent[n0:]):
                    break
                page.wait_for_timeout(100)
            got = [x for x in sent[n0:] if str(x.get("session", "")).startswith("apv-")]
            return (got[0] if len(got) == 1 else None), page.evaluate("() => window.__credGets") - g0, len(got)

        def mac_approve(env, rec, aid, replies):
            return oa.approve(env, devices=devices, verify=osv.verify_envelope, rp=rp,
                              read_ask=lambda s, a: rec if a == aid else None,
                              write_reply=lambda s, a, x: replies.append((s, a, x)), project_of=lambda s, a: "0" * 12,
                              home=str(Path.home()), available=True) if env else ("none", "no envelope", {})

        # ⑤ R114-P（本人裁定 2026-10-08「ON の間はタップだけ」）: 押す → パスキーを呼ばずにタップの封筒（mode:"tap"）が 1 通・
        # 外側の HMAC は Mac と同じ verify_envelope が受理・本文は契約の形。Mac 側（R114-S）がこの木にあれば 14 段の受理まで見る
        env5, gets5, n5 = press_apv()
        ok5, why5, inner5 = _tap_env(env5, device_id, secret, ask_id, fields["inputSha"])
        if ok5 and gets5 == 0 and n5 == 1:
            print("  ✓ R114-P remote を押す → Face ID を呼ばずにタップの封筒 1 通（外側の HMAC は Mac の verify_envelope が受理・本文は契約の並び）")
        else:
            print(f"  ✗ R114-P remote のタップが違う: {why5} パスキー呼び出し={gets5} 封筒={n5} errors={errors[:2]}")
            ng += 1
        if tap_ready:
            replies = []
            res = mac_approve(env5, dict(fields, cwd=home, session=e["session"]), ask_id, replies)
            if res[0] == "granted" and len(replies) == 1 and replies[0][1] == ask_id:
                print("  ✓ R114-P タップの封筒を Mac（R114-S）の検証が受理（回答 1 通・askId 一致）")
            else:
                print(f"  ✗ R114-P タップの封筒が Mac 側で通らない: {res[:2]} replies={len(replies)}")
                ng += 1
        else:
            print("  · R114-P Mac 側のタップの受理（R114-S）はこの木に未合流＝封筒の形と外側の HMAC まで（合流後はここで 14 段まで見る）")
        # ④ 表示と封書の inputSha が違う → 送らない
        page.evaluate("() => closeApprove(true)")
        open_with(dict(fields, askId=_s.token_hex(16)), tamper={"inputSha": "0" * 64})
        page.wait_for_timeout(1400)
        n0 = len(sent)
        page.click("#apv_allow")
        page.wait_for_timeout(1500)
        leaked = [x for x in sent[n0:] if str(x.get("session", "")).startswith("apv-")]
        note = page.evaluate("() => document.getElementById('apvnote').textContent")
        if not leaked and note:
            print(f"  ✓ R100-1b 画面の文字列と封書の inputSha が違えば送らない（{note[:24]}…）")
        else:
            print(f"  ✗ R100-1b 一致しないのに送った: {len(leaked)} note={note}")
            ng += 1
        # ⑤' R116-S1（D3「5 秒の制限には端末が待ってから送る」）: 前の許可から 5 秒以内に押した 2 件目は、押し直し無しで
        # 5 秒を空けてから 1 通だけ送る（Mac の rate に当てない）。待っている間に画面を離れたら送らない（許可は失敗側に倒す）
        page.evaluate("() => closeApprove(true)")
        open_with(dict(fields, askId=_s.token_hex(16)))
        page.wait_for_timeout(1300)
        n0 = len(sent)
        page.evaluate("() => { APV_LAST_ALLOW = Date.now(); }")
        page.click("#apv_allow")
        page.wait_for_timeout(200)
        q_label = page.evaluate("() => document.getElementById('apv_allow').textContent")
        page.wait_for_timeout(2500)
        early = [x for x in sent[n0:] if str(x.get("session", "")).startswith("apv-")]
        page.wait_for_timeout(3200)
        late = [x for x in sent[n0:] if str(x.get("session", "")).startswith("apv-")]
        q_ok, q_why, q_inner = _tap_env(late[0] if len(late) == 1 else None, device_id, secret)
        if not early and len(late) == 1 and q_ok and q_label == "送信中…" and q_inner["ux"]["q"] >= 4000:
            print(f"  ✓ R116-S1 5 秒以内の 2 件目は押し直し無しで 5 秒を空けて 1 通（待ち {q_inner['ux']['q']} ms・その間は「送信中…」）")
        else:
            print(f"  ✗ R116-S1 5 秒以内の 2 件目: 早く送った={len(early)} 後で={len(late)} {q_why} ラベル={q_label!r}")
            ng += 1
        page.evaluate("() => closeApprove(true)")
        open_with(dict(fields, askId=_s.token_hex(16)))
        page.wait_for_timeout(1300)
        n0 = len(sent)
        page.evaluate("() => { APV_LAST_ALLOW = Date.now(); }")
        page.click("#apv_allow")
        page.wait_for_timeout(300)
        page.evaluate("() => { window.__onNow0 = window.apvOnNow; window.apvOnNow = () => false; }")
        page.wait_for_timeout(5600)
        off_sent = [x for x in sent[n0:] if str(x.get("session", "")).startswith("apv-")]
        page.evaluate("() => { window.apvOnNow = window.__onNow0; }")
        if not off_sent:
            print("  ✓ R116-S1（レビュー）5 秒の待ちの間に OFF になったら許可は送らない（送る直前に ON・鍵・段を検算）")
        else:
            print(f"  ✗ R116-S1（レビュー）待ちの間に OFF になったのに送った: {len(off_sent)}")
            ng += 1
        page.evaluate("() => closeApprove(true)")
        open_with(dict(fields, askId=_s.token_hex(16)))
        page.wait_for_timeout(1300)
        n0 = len(sent)
        page.evaluate("() => { APV_LAST_ALLOW = Date.now(); }")
        page.click("#apv_allow")
        page.wait_for_timeout(300)
        page.evaluate("""() => { Object.defineProperty(document, 'hidden', { value: true, configurable: true });
            document.dispatchEvent(new Event('visibilitychange')); }""")
        page.wait_for_timeout(5600)
        page.evaluate("""() => { delete document.hidden; document.dispatchEvent(new Event('visibilitychange')); }""")
        gone = [x for x in sent[n0:] if str(x.get("session", "")).startswith("apv-")]
        back_ok = page.evaluate("() => !!APV && !APV.queued && !APV.busy && !APV.sent")
        if not gone and back_ok:
            print("  ✓ R116-S1 5 秒の待ちの間に画面を離れたら許可は送らない（戻るとカードは押す前の状態）")
        else:
            print(f"  ✗ R116-S1 待ちの間に離れたのに送った／戻らない: {len(gone)} {back_ok}")
            ng += 1
        # ⑤'' R116-S1（D3「150 秒を過ぎてから押したとき: 取り直す。askId と inputSha が同じなら、押した意思のまま送る」）: 実物の封書で
        # 取り直しの要求（kind:ask）が 1 通出て、同じ中身の封書が届くと押し直し無しでタップの封筒 1 通。取り直しの間に画面を離れたら送らない
        def again_case(hide):
            page.evaluate("() => closeApprove(true)")
            aid2 = _s.token_hex(16)
            open_with(dict(fields, askId=aid2))
            page.wait_for_timeout(1300)
            page.evaluate("() => { APV.at = Date.now() - 151000; APV_LAST_ALLOW = 0; }")
            n0 = len(sent)
            page.click("#apv_allow")
            for _ in range(50):
                if any(str(x.get("session", "")).startswith("dlg-") for x in sent[n0:]):
                    break
                page.wait_for_timeout(100)
            asks2 = [json.loads(x.get("text") or "{}") for x in sent[n0:] if str(x.get("session", "")).startswith("dlg-")]
            rid2 = asks2[-1].get("reqId", "") if asks2 else ""
            label = page.evaluate("() => document.getElementById('apv_allow').textContent")
            if hide:
                page.evaluate("""() => { Object.defineProperty(document, 'hidden', { value: true, configurable: true });
                    document.dispatchEvent(new Event('visibilitychange')); }""")
            iat2 = int(_t.time())
            body2 = dict(fields, askId=aid2, kind="ask", session=e["session"], reqId=rid2, iat=iat2)
            if rid2:
                page.evaluate("(p) => onDlg(JSON.stringify({ v: 1, items: [p] }))", dialog_seal.seal(secret, device_id, rid2, e["session"], body2, iat=iat2))
            page.wait_for_timeout(1500)
            if hide:
                page.evaluate("() => { delete document.hidden; document.dispatchEvent(new Event('visibilitychange')); }")
            got2 = [x for x in sent[n0:] if str(x.get("session", "")).startswith("apv-")]
            ok2, why2, inner2 = _tap_env(got2[0] if len(got2) == 1 else None, device_id, secret, aid2, fields["inputSha"])
            return {"asks": len(asks2), "label": label, "sent": len(got2), "ok": ok2, "why": why2, "iat": inner2.get("iat")}, iat2
        a1, iat_new = again_case(False)
        a2, _ = again_case(True)
        if (a1["asks"] == 1 and a1["label"] == "送信中…" and a1["sent"] == 1 and a1["ok"] and a1["iat"] == iat_new
                and a2["asks"] == 1 and a2["sent"] == 0):
            print("  ✓ R116-S1 150 秒を過ぎてから押すと黙って取り直し（要求 1 通・「送信中…」）、同じ中身なら押し直し無しで新しい iat のタップの封筒 1 通・"
                  "取り直しの間に画面を離れたら送らない")
        else:
            print(f"  ✗ R116-S1 150 秒を過ぎてからの押下: 続けて={a1} 離れた={a2}")
            ng += 1
        # ⑦ R102-2 review（本人裁定「危険なもの以外は全部」）＋R114-P（「ON の間はタップだけ」）: 警告のタグを言葉で出し、要点（道具・入力の先頭）を先に・
        # 全文は「全文を見る」の折りたたみ。「内容を確認した」の印もスクロールの義務も無く、1.2 秒後に押せる → 押すとパスキーを呼ばずにタップの封筒 1 通
        page.evaluate("() => closeApprove(true)")
        rti = {"command": "bash verify.sh", "description": "\n".join("検証の手順 %d" % i for i in range(60))}
        rcanon = ap.input_canon("Bash", rti)
        rask = _s.token_hex(16)
        rfields = dict(fields, askId=rask, inputCanon=rcanon, inputSha=ap.input_sha(rcanon), inputBytes=len(rcanon.encode()),
                       level="review", reasons=["script"], tags=["script", "outside-cwd"])
        open_with(rfields)
        look_js = """() => { const b = document.getElementById('apv_allow'), lv = document.getElementById('apvlevel'), rest = document.getElementById('apvrest');
            return { lv: !lv.hidden, cls: lv.className, head: lv.textContent, dis: b.disabled, label: b.textContent, hidden: b.hidden,
              ack: !!(document.getElementById('apv_ack') || document.getElementById('apvack')),
              box: (document.getElementById('apvbox') || {}).textContent || '', rest: !!rest, restHidden: !!(rest && rest.hidden),
              restAll: !!(rest && (rest.textContent.indexOf('検証の手順 59') >= 0 || rest.textContent.indexOf('片付けの手順 59') >= 0)),
              state: document.getElementById('apvwrap').getAttribute('data-state') }; }"""
        r0 = page.evaluate(look_js)
        page.wait_for_timeout(1400)
        r2 = page.evaluate("() => document.getElementById('apv_allow').disabled")      # スクロールも印も無しで押せる
        # B2→R116-S1: 横向きの低い画面（844×390）でもカードの［許可］が画面の中・命令は単語の途中で割らない（word-break: normal）
        page.set_viewport_size({"width": 844, "height": 390})
        page.wait_for_timeout(400)
        ls = page.evaluate("""() => { const c = document.getElementById('apvwrap'), b = document.getElementById('apv_allow'), r = b.getBoundingClientRect(),
            box = document.getElementById('apvbox'); return { host: c.parentNode ? c.parentNode.id : '', inView: r.width > 0 && r.top >= 0 && r.bottom <= innerHeight + 1,
            wb: box ? getComputedStyle(box).wordBreak : null }; }""")
        page.set_viewport_size({"width": 390, "height": 844})
        page.wait_for_timeout(400)
        if ls["host"].startswith("apvslot_") and ls["inView"] and ls["wb"] == "normal":
            print(f"  ✓ R116-S1 844×390 でもカード（{ls['host']}）の［許可］が画面の中・命令は単語の途中で折らない")
        else:
            print(f"  ✗ R116-S1 844×390 でカードが見えない／命令が単語の途中で折れる: {ls}")
            ng += 1
        page.wait_for_timeout(1300)
        env7, gets7, n7 = press_apv()
        ok7, why7, _inner7 = _tap_env(env7, device_id, secret, rask, rfields["inputSha"])
        res7 = ("skip", "", {})
        if tap_ready:
            replies = []
            res7 = mac_approve(env7, dict(rfields, cwd=home, session=e["session"]), rask, replies)
        if (r0["lv"] and r0["cls"] == "lvwarn" and r0["head"] == "注意: 組み立てて実行・作業フォルダの外" and r0["dis"] and not r0["ack"]
                and r0["box"] == "bash verify.sh" and r0["rest"] and r0["restHidden"] and r0["restAll"] and r0["label"] == "許可"
                and r0["state"] == "ready" and not r2 and ok7 and gets7 == 0 and n7 == 1
                and (not tap_ready or (res7[0] == "granted" and res7[2].get("level") == "review"))):
            print(f"  ✓ R116-S1 review: 注意の 1 行（{r0['head']}）・箱に命令・説明は［全文］の中・印もスクロールも無く 1.2 秒後に［許可］"
                  "→ パスキーを呼ばずにタップの封筒 1 通" + ("・Mac（R114-S）が review のまま受理" if tap_ready else ""))
        else:
            print(f"  ✗ R116-S1 review のタップが違う: 初め={r0} 1.4 秒後 disabled={r2} 封筒={why7}/{n7} パスキー={gets7} Mac={res7[:2]}")
            ng += 1
        # ⑥ R118（本人裁定 2026-10-13「危険な種類も全部 1 タップ」）: level:"mac" も全文が届いていれば（macOnly=0）危険の朱の 1 行・要点を出し、
        # 全文は折りたたみ・印もスクロールも無く 1.2 秒後に［許可］→ パスキーを呼ばずにタップの封筒 1 通 → Mac の 14 段が判定を作り直して
        # mac のまま受理。判定できない形（macOnly=1・全文なし）は押せない
        page.evaluate("() => closeApprove(true)")
        mti = {"command": "rm -rf build", "description": "\n".join("片付けの手順 %d" % i for i in range(60))}
        mcanon = ap.input_canon("Bash", mti)
        mask = _s.token_hex(16)
        mfields = dict(fields, askId=mask, inputCanon=mcanon, inputSha=ap.input_sha(mcanon), inputBytes=len(mcanon.encode()),
                       level="mac", reasons=["delete"], tags=["delete"], macOnly=0)
        open_with(mfields)
        m0 = page.evaluate(look_js)
        page.wait_for_timeout(1400)
        m1 = page.evaluate("() => document.getElementById('apv_allow').disabled")
        envm, getsm, nm = press_apv()
        okm, whym, innerm = _tap_env(envm, device_id, secret, mask, mfields["inputSha"])
        replies = []
        mres = mac_approve(envm, dict(mfields, cwd=home, session=e["session"]), mask, replies)
        page.evaluate("() => closeApprove(true)")
        open_with(dict(fields, askId=_s.token_hex(16), level="mac", reasons=["dynamic"], macOnly=1))
        page.wait_for_timeout(1400)
        mac = page.evaluate("""() => ({ dis: document.getElementById('apv_allow').disabled, hidden: document.getElementById('apv_allow').hidden,
            why: document.getElementById('apvwhy').textContent, state: document.getElementById('apvwrap').getAttribute('data-state') })""")
        if (m0["lv"] and m0["dis"] and not m0["ack"] and m0["cls"] == "lvdanger" and m0["head"] == "危険: 削除" and m0["box"] == "rm -rf build"
                and m0["rest"] and m0["restHidden"] and m0["restAll"] and m0["label"] == "許可" and m0["state"] == "ready" and not m1
                and okm and getsm == 0 and nm == 1 and innerm["ux"]["s"] == "ready"
                and mres[0] == "granted" and mres[2].get("level") == "mac" and len(replies) == 1
                and mac["dis"] and mac["hidden"] and mac["state"] == "mac-only" and mac["why"] == "この操作は Mac で許可してください（組み立てて実行）"):
            print(f"  ✓ R118 level:mac は危険の朱の 1 行（{m0['head'][:14]}…）＋要点・全文は折りたたみ・1.2 秒後に［許可］→ パスキーを呼ばずに"
                  "タップの封筒 1 通 → Mac の 14 段が mac のまま受理・判定できない形（macOnly=1）は押せない")
        else:
            print(f"  ✗ R118 level:mac が違う: 初め={m0} 1.4 秒後 disabled={m1} パスキー={getsm} 封筒={nm}/{whym} "
                  f"Mac={mres[:2]} replies={len(replies)} 判定できない形={mac}")
            ng += 1
        # R103-1: OFF もスマホから（封筒の署名だけ・Face ID なし）→ Mac 側で OFF
        page.evaluate("() => closeApprove(true)")
        n_rst = len([x for x in sent if str(x.get("session", "")).startswith("rst-")])
        page.evaluate("() => apvRemoteSet(false)")
        off_env = next_env("rst-", n_rst + 1)
        off_res = (oa.remote_set(off_env, devices=devices, verify=osv.verify_envelope, rp=rp)
                   if off_env else ("none", "no envelope", {}))
        off_inner = json.loads((off_env or {}).get("text") or "{}")
        if off_res[0] == "granted" and off_inner.get("on") is False and "signature" not in off_inner and oa.remote_until() <= _t.time():
            print("  ✓ R103-1 スマホの OFF（封筒だけ）→ Mac 側で OFF")
        else:
            print(f"  ✗ R103-1 スマホの OFF が通らない: {off_res[:2]} inner={off_inner} until={oa.remote_until()}")
            ng += 1
        pk = page.evaluate("() => [window.__credGets, window.__credCreates]")
        if pk == [0, 0]:
            print("  ✓ R118 この検査の間（ON・remote・review・mac・OFF）、パスキー（navigator.credentials の get／create）を 1 回も呼ばない")
        else:
            print(f"  ✗ R118 パスキーを呼んだ: get={pk[0]} create={pk[1]}")
            ng += 1
        if errors:
            print(f"  ✗ R100-1b ページのエラー: {errors[:3]}")
            ng += 1
    except Exception as exc:  # noqa: BLE001  操作の待ちが切れた等＝赤として数える（後ろのピンを止めない）
        print(f"  ✗ R100-1b 許可のピンが途中で止まった: {type(exc).__name__}: {str(exc)[:160]}")
        ng += 1
    finally:
        browser.close()
        if old_home is None:
            os.environ.pop("OFFICE_HOME", None)
        else:
            os.environ["OFFICE_HOME"] = old_home
    return ng

def _reload_pins(p, base_url, device_id, secret, token):
    """R102-1（本人「スマホ版で AI オフィスがまだ表示されていません」・ホーム画面のアプリが古い版のまま動き続けている疑い）:
    Worker が status に載せる shell（今配っている /app の版）と自分の <html data-build> が違えば、安全な時に 1 回だけ読み直す。
    ①同じ版では読み直さない ②違う版でも設定が開いている間は待ち、閉じてから 1 回 ③同じ版へは 10 分に 1 回まで（配備直後の版ずれで
    行ったり来たりしない）。起動の回数は sessionStorage の数で数える（読み直しは同じタブ＝sessionStorage が残る）。"""
    ng = 0
    browser = p.chromium.launch()
    try:
        ctx = browser.new_context(viewport={"width": 390, "height": 844}, has_touch=True, is_mobile=True)
        page = ctx.new_page()
        cred = json.dumps({"d": device_id, "s": secret, "t": token, "e": 0}, separators=(",", ":"))
        page.add_init_script("localStorage.setItem('aioffice.cred', " + json.dumps(cred) + ");"
                             "sessionStorage.setItem('bootn', String(+(sessionStorage.getItem('bootn') || 0) + 1));")
        page.goto(base_url.rstrip("/") + "/app/", wait_until="domcontentloaded")
        page.wait_for_function("() => typeof maybeReload === 'function'", timeout=30000)
        page.wait_for_timeout(1500)                    # WS の最初の status（同じ版）を受けてから
        boots = lambda: int(page.evaluate("() => +(sessionStorage.getItem('bootn') || 0)"))
        ver = page.evaluate("() => document.documentElement.getAttribute('data-build')")
        same = boots()
        other = "0123456789ab" if ver != "0123456789ab" else "ba9876543210"
        page.evaluate("() => { openSettings(); }")
        page.wait_for_timeout(300)
        page.evaluate("(v) => maybeReload(v)", other)
        page.wait_for_timeout(1200)
        held = boots()
        with page.expect_navigation(timeout=8000):
            page.evaluate("() => { closeSettings(); maybeReload(); }")
        page.wait_for_function("() => typeof maybeReload === 'function'", timeout=30000)
        after = boots()
        page.evaluate("(v) => maybeReload(v)", other)
        page.wait_for_timeout(1500)
        again = boots()
        ok = bool(ver) and len(ver) == 12 and same == 1 and held == 1 and after == 2 and again == 2
        if ok:
            print(f"  ✓ R102-1 古い版で動き続けない: 版 {ver}・同じ版は読み直さない・設定が開いている間は待つ・閉じて 1 回・同じ版へ 2 回目は無い")
        else:
            print(f"  ✗ R102-1 版の食い違いの読み直しが違う: data-build={ver!r} 起動回数 同じ={same} 開いている間={held} 閉じた後={after} 2 回目={again}")
            ng += 1
        ctx.close()
    except Exception as exc:
        print(f"  ✗ R102-1 版の食い違いの読み直しの検査が落ちた: {type(exc).__name__}: {exc}")
        ng += 1
    finally:
        browser.close()
    return ng


def _room_error_pin(p, base_url, device_id, secret, token):
    """R102-8（本人の iPhone だけで「この端末では部屋を表示できません」・手元の WebKit では出る）: 部屋の部品が実行中に例外を
    投げたら、その文言とファイル・行を「表示できません」の下に出し、中継のログ（POST /diag）へ 1 回だけ送る＝推測で直さず、
    実機で何が起きたかを掴む。"""
    ng = 0
    browser = p.chromium.launch()
    try:
        ctx = browser.new_context(viewport={"width": 390, "height": 844}, has_touch=True, is_mobile=True)
        page = ctx.new_page()
        diag = []
        page.on("request", lambda rq: diag.append(rq.post_data or "") if rq.url.endswith("/diag") and rq.method == "POST" else None)
        page.route(re.compile(r"/(ui|u/[0-9a-f]{12})/core/life\.js(\?|$)"), lambda r: r.fulfill(status=200, content_type="text/javascript; charset=utf-8",
                                                               body="export function lifeTargets() { return {}; }\nthrow new Error('boom-life-e2e');\n"))
        cred = json.dumps({"d": device_id, "s": secret, "t": token, "e": 0}, separators=(",", ":"))
        page.add_init_script("localStorage.setItem('aioffice.officeMode','pixel');")
        page.add_init_script("localStorage.setItem('aioffice.cred', " + json.dumps(cred) + ");")
        page.goto(base_url.rstrip("/") + "/app/", wait_until="domcontentloaded")
        page.wait_for_function("() => typeof SCENE3D !== 'undefined' && SCENE3D === 'failed'", timeout=20000)
        page.evaluate("() => setView('office', true)")
        page.wait_for_timeout(600)
        why = page.evaluate("() => roomErrText()")
        if ("boom-life-e2e" in why and any(name in why for name in ("core/life.js", "pwa/bootpx.js"))
                and page.locator("#office_notice").is_visible() and len(diag) == 1 and "boom-life-e2e" in diag[0]):
            print(f"  ✓ R102-8 部屋の部品が例外を投げたら、理由を画面に出して中継へ 1 回送る（{why}）")
        else:
            print(f"  ✗ R102-8 部屋が起動しなかった理由が出ない／送られない: 画面={why!r} 送信={diag}")
            ng += 1
        ctx.close()
        # R122-P3: 遅れて失敗が届いても現在の読込の理由を1回だけ送る（見張りは15秒）。
        import time as _t
        ctx = browser.new_context(viewport={"width": 390, "height": 844}, has_touch=True, is_mobile=True)
        page = ctx.new_page()
        late = []
        page.on("request", lambda rq: late.append(rq.post_data or "") if rq.url.endswith("/diag") and rq.method == "POST" else None)
        page.route(re.compile(r"/(ui|u/[0-9a-f]{12})/core/life\.js(\?|$)"), lambda r: (_t.sleep(5.5), r.fulfill(status=200, content_type="text/javascript; charset=utf-8",
                   body="export function lifeTargets() { return {}; }\nthrow new Error('late-boom-e2e');\n")))
        page.add_init_script("localStorage.setItem('aioffice.officeMode','pixel');")
        page.add_init_script("localStorage.setItem('aioffice.cred', " + json.dumps(cred) + ");")
        page.goto(base_url.rstrip("/") + "/app/", wait_until="domcontentloaded")
        page.wait_for_function("() => typeof SCENE3D !== 'undefined' && SCENE3D === 'failed'", timeout=20000)
        page.evaluate("() => setView('office', true)")
        try:
            page.wait_for_function("() => roomErrText().indexOf('late-boom-e2e') >= 0",
                                   timeout=15000)
        except Exception:
            pass
        page.wait_for_timeout(500)
        why2 = page.evaluate("() => roomErrText()")
        if "late-boom-e2e" in why2 and len(late) == 1 and "late-boom-e2e" in late[0]:
            print(f"  ✓ R102-8 遅れて届いた失敗の理由を1回だけ送る（{why2}）")
        else:
            print(f"  ✗ R102-8 後から届いた理由が反映されない: 画面={why2!r} 送信={late}")
            ng += 1
        ctx.close()
    finally:
        browser.close()
    return ng


def _r112_pins(p, base_url, device_id, secret, token):
    """R112-P（本人 2026-10-07「スマホで『Bash の実行を許可するか待っている』の項目で指示が出せない。1、2、3 の選択肢がある
    パターンでも通常どおり指示を出せなかった」）:
    ① 2 問以上・複数選択（掲示の ask.questions）は問いごとに押せる button で選び、全部選ぶまで送れない・再描画で選択が消えない・
       送る文は「1. 問い: 答え\\n2. 問い: 答え, 答え\\n3. …」（シートからは返信欄の文を「補足: 」で添える）
    ② 問いの中身が無い古い Mac の qn=3 は注記＋「答えを書く…」
    ③ スマホから許可が OFF（caps.approve=0・on=0）の許可待ちはカードに「ON が切れています」と［ON にして確認］→ その場で ON の封筒（R118: 封筒だけ）
    ④ R118: この端末の許可鍵が無くても（ON は期限なし）カードは封書を取りに行く（loading）＝許可鍵の登録の導線は無い
    ⑤ アプリの JS エラー（kind:"app"）と送信の失敗（kind:"send"）が /diag へ 1 回（本文・session・askId は載せない）
    ⑥ 更新直後の 600ms に押した操作は黙って捨てずに「画面が更新されました」と言う
    R112-P2（別モデルレビューの medium 3 件）:
    P2-① 2100 字の補足（シートの返信欄）で送っても答えの本文は 2000 字（Mac の受け手の上限）以内・「補足を短くしました」と言う
    P2-② 束ねたカード（avatarMode=project・同じプロジェクトの 2 セッション）: 代表が替わっても非代表になった相手の選択は残り、
          シート（束の中のセッション）で選び足せ、新しい代表の箱には付かない・代表に戻れば同じ選択で 1 通（宛先と印はその相手）
    P2-③ 選んだ後の再読み込みで選択が戻る（もうあなた待ちに居ない相手の控えは戻さない）・送ったら控え（sessionStorage）が消える
    状態は page.route の /status で固定し、WebSocket は止める（中継の DO の状態で上書きされない）。/instruct と /diag も route で捕まえる。"""
    ng = 0
    fails = []

    def bad(msg):
        fails.append(msg)
        print(f"  ✗ R112-P {msg}")

    browser = p.chromium.launch()
    try:
        ctx = browser.new_context(viewport={"width": 390, "height": 844}, has_touch=True, is_mobile=True,
                                  service_workers="block")
        page = ctx.new_page()
        errors, sent, diag = [], [], []
        mode = {"instruct": "ok"}
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.on("dialog", lambda d: d.accept())

        def on_instruct(route, request):
            if request.method != "POST":
                route.continue_()
                return
            sent.append(request.post_data or "")
            if mode["instruct"] == "500":
                route.fulfill(status=500, content_type="application/json", body='{"ok":false,"error":"r112-boom"}')
            elif mode["instruct"] == "abort":
                route.abort()
            else:
                route.fulfill(status=200, content_type="application/json", body='{"ok":true}')

        def on_diag(route, request):
            diag.append(request.post_data or "")
            route.fulfill(status=204, body="")

        serve = {"status": None}

        def on_status(route, request):
            if request.method == "GET" and serve["status"] is not None:
                route.fulfill(status=200, content_type="application/json", body=serve["status"])
            else:
                route.continue_()
        page.route("**/instruct", on_instruct)
        page.route("**/diag", on_diag)
        page.route("**/status", on_status)

        def office(emps, actions):
            o = {"officeName": "E2E R112", "employees": emps, "actions": actions}
            serve["status"] = json.dumps({"ok": True, "ts": int(time.time() * 1000), "agentSeenAgo": 0, "agentOnline": True,
                                          "json": json.dumps(o, ensure_ascii=False)}, ensure_ascii=False)

        def reserve(emps, actions):
            office(emps, actions)
            page.evaluate("() => poll()")
            page.wait_for_function("(s) => officeAgents(LAST_OFFICE).map(e => e.session).join(',') === s",
                                   arg=",".join(e["session"] for e in emps), timeout=8000)
            page.wait_for_timeout(750)                     # B1: 人が替わった直後 600ms は送る操作を受けない

        def answers(start):
            out = []
            for raw in sent[start:]:
                ok, why, sess, text = _verify(raw, device_id, secret)
                try:
                    inner = json.loads(text)
                except ValueError:
                    inner = {"text": text}
                out.append({"ok": ok, "why": why, "session": sess, "ask": inner.get("ask"), "text": inner.get("text")})
            return out

        def tap(sel):
            b = _tap_center(page, sel)
            page.wait_for_timeout(80)
            return bool(b and b["hit"])

        q3 = {"session": "e2e-r112-q3", "projectId": "e2e-r112-q3", "disp": "E2E三問", "state": "waiting", "listening": True,
              "verb": "検討中", "question": "どの方式にしますか?",
              "questionOptions": [{"label": "方式A (Recommended)", "desc": ""}, {"label": "方式B", "desc": ""}],
              "ask": {"kind": "question", "tool": "AskUserQuestion", "qn": 3, "multi": 1, "id8": "a1b2c3d4",
                      "questions": [{"q": "どの方式にしますか?", "options": ["方式A (Recommended)", "方式B"], "multi": 0},
                                    {"q": "どの機能を入れますか?", "options": ["検索", "通知", "履歴"], "multi": 1},
                                    {"q": "いつ出しますか?", "options": ["今日", "来週"], "multi": 0}]}}
        qold = {"session": "e2e-r112-qold", "projectId": "e2e-r112-qold", "disp": "E2E旧三問", "state": "waiting", "listening": True,
                "verb": "検討中", "question": "どれにしますか?",
                "questionOptions": [{"label": "案A", "desc": ""}, {"label": "案B", "desc": ""}],
                "ask": {"kind": "question", "tool": "AskUserQuestion", "qn": 3, "multi": 0, "id8": "b1c2d3e4"}}
        perm = {"session": "e2e-r112-perm", "projectId": "e2e-r112-perm", "disp": "E2E許可", "state": "waiting", "listening": True,
                "verb": "実行中", "approvalMin": 2, "ask": {"kind": "permission", "tool": "Bash", "remote": 1, "id8": "c0ffee01"}}
        off_mine = {"caps": {"approve": 0}, "approve": {"on": 0, "until": 0, "fps": ["r112fp"], "enr": []}}
        cred = json.dumps({"d": device_id, "s": secret, "t": token, "e": 0}, separators=(",", ":"))
        page.add_init_script("localStorage.setItem('aioffice.cred', " + json.dumps(cred) + ");"
                             "localStorage.setItem('aioffice.apv', JSON.stringify({ fp: 'r112fp', d: " + json.dumps(device_id) + " }));"
                             "window.WebSocket = function () { throw new Error('r112: WS off'); };")
        office([q3, qold], off_mine)
        page.goto(base_url.rstrip("/") + "/app/", wait_until="domcontentloaded")
        page.wait_for_function("() => typeof updateAttnCards === 'function' && officeAgents(LAST_OFFICE).length === 2", timeout=30000)
        page.evaluate("() => { closeSheet(); setView('wait', true); }")
        page.wait_for_timeout(750)

        # ① 問いごとの選択（カード）
        C = '#attncards .attncard[data-attn-sess="e2e-r112-q3"]'
        probe = """(C) => { const c = document.querySelector(C); if (!c) return null; const b = c.querySelector('.qpsend');
          return { qs: [...c.querySelectorAll('.qpick .qpq')].map(q => ({ n: q.querySelectorAll('button.qpo').length, multi: !!q.querySelector('.qpq-m') })),
                   on: [...c.querySelectorAll('.qpo[aria-checked="true"]')].map(n => n.getAttribute('data-qi') + ':' + n.getAttribute('data-oi')),
                   ro: c.querySelectorAll('.qopt.ro, .qmulti').length, send: b ? { dis: b.disabled, t: b.textContent } : null,
                   left: (c.querySelector('.qpleft') || {}).textContent || '', free: (c.querySelector('.qpfree') || {}).textContent || '',
                   head: (c.querySelector('.attnq') || {}).textContent || '' }; }"""
        st0 = page.evaluate(probe, C)
        def held(st):                                       # 「この答えを送る」があって押せない
            return bool(st and st.get("send") and st["send"]["dis"])
        if not (st0 and [q["n"] for q in st0["qs"]] == [2, 3, 2] and [q["multi"] for q in st0["qs"]] == [False, True, False]
                and st0["ro"] == 0 and held(st0) and "あと 3 問" in st0["left"]
                and st0["free"] == "ほかの答えを書く…" and "3 問" in st0["head"]):
            bad(f"① 3 問（2 問目は複数選択）が問いごとの押せる選択肢にならない: {st0}")
        hits = [tap(C + f' .qpq[data-qi="{qi}"] .qpo[data-oi="{oi}"]') for qi, oi in ((0, 0), (1, 0), (1, 2))]
        st1 = page.evaluate(probe, C)
        n0 = len(sent)
        tap(C + " .qpsend")                                 # 押せない（1 問残っている）＝送らない
        page.wait_for_timeout(300)
        # 再描画（status の取り直し・カードの組み直し）でも選んだ状態が残る
        page.evaluate("() => { updateAttnCards(officeAgents(LAST_OFFICE)); poll(); }")
        page.wait_for_timeout(900)
        st2 = page.evaluate(probe, C)
        if not (all(hits) and held(st1) and "あと 1 問" in st1["left"] and sorted(st1["on"]) == ["0:0", "1:0", "1:2"]
                and len(sent) == n0 and held(st2) and sorted(st2["on"]) == ["0:0", "1:0", "1:2"]):
            bad(f"① 選んだ数・あと N 問・再描画での保持が違う: hits={hits} 選択後={st1} 再描画後={st2} 送信={len(sent) - n0}")
        tap(C + ' .qpq[data-qi="2"] .qpo[data-oi="1"]')
        st3 = page.evaluate(probe, C)
        tap(C + " .qpsend")
        _wait_sent(page, sent, n0 + 1)
        got = answers(n0)
        want = "1. どの方式にしますか?: 方式A (Recommended)\n2. どの機能を入れますか?: 検索, 履歴\n3. いつ出しますか?: 来週"
        if (st3 and st3.get("send") and not held(st3) and not st3["left"] and len(got) == 1 and got[0]["ok"] and got[0]["session"] == q3["session"]
                and got[0]["ask"] == "a1b2c3d4" and got[0]["text"] == want
                and re.fullmatch(r"1\. [^\n]+: [^\n]+\n2\. [^\n]+: [^\n,]+, [^\n]+\n3\. [^\n]+: [^\n]+", got[0]["text"] or "")):
            print("  ✓ R112-P ① 3 問（2 問目は複数選択）を問いごとに選んで 1 通（全部選ぶまで送れない・再描画で残る・"
                  "「1. 問い: 答え／2. …, …／3. …」・id8 つきの署名）")
        else:
            bad(f"① 送った答えが違う: 状態={st3} 送信={got}")
        # シートでも同じ選択が見え、返信欄の文を「補足: 」で添えて送る
        sh = page.evaluate("""() => { openSheet(officeAgents(LAST_OFFICE).find(e => e.session === 'e2e-r112-q3'));
          document.getElementById('freetext').value = '急ぎでお願いします';
          return { on: document.querySelectorAll('#quickbtns .qpick .qpo.on').length,
                   send: !!document.querySelector('#quickbtns .qpick .qpsend:not([disabled])') }; }""")
        n1 = len(sent)
        page.evaluate("() => document.querySelector('#quickbtns .qpick .qpsend')?.click()")
        _wait_sent(page, sent, n1 + 1)
        got = answers(n1)
        if sh["on"] == 4 and sh["send"] and len(got) == 1 and got[0]["text"] == want + "\n補足: 急ぎでお願いします":
            print("  ✓ R112-P ① シートにも同じ選択が残り、返信欄の文を「補足: 」として最後に添える")
        else:
            bad(f"① シートの選択／補足が違う: {sh} 送信={got}")
        page.evaluate("() => { closeSheet(); setView('wait', true); }")

        # ② 問いの中身（ask.questions）が無い古い Mac の qn=3: 注記（押せない）＋「答えを書く…」
        Q = '#attncards .attncard[data-attn-sess="e2e-r112-qold"]'
        old = page.evaluate("""(Q) => { const c = document.querySelector(Q); if (!c) return null;
          const r = { note: (c.querySelector('.qmulti') || {}).textContent || '', btn: c.querySelectorAll('.attnoptions button, .qpick').length,
                      write: [...c.querySelectorAll('.attnactions button')].map(b => b.textContent) };
          openSheet(officeAgents(LAST_OFFICE).find(e => e.session === 'e2e-r112-qold'));
          const w = document.querySelector('#quickbtns .quickoptions .qwrite');
          r.sheetNote = !!document.querySelector('#quickbtns .quickoptions .qmulti'); r.sheetWrite = w ? w.textContent : null;
          if (w) w.click(); r.focus = document.activeElement && document.activeElement.id; closeSheet(); return r; }""", Q)
        if (old and "このまま選べません" in old["note"] and "3 問" in old["note"] and "答えを書く…" in old["note"] and old["btn"] == 0
                and "答えを書く…" in old["write"] and old["sheetNote"] and old["sheetWrite"] == "答えを書く…" and old["focus"] == "freetext"):
            print(f"  ✓ R112-P ② 問いの中身が無い古い Mac の 3 問は注記（「{old['note'][:16]}…」）＋「答えを書く…」（カード・シート＝返信欄へ）")
        else:
            bad(f"② 古い Mac の複数の問いの注記／答えを書く…が違う: {old}")

        # R112-P2 ① 補足が長くても答えの本文は 2000 字（daemon の short・hook の clip が切る長さ）以内・削ったらそう言う
        page.evaluate("""() => { openSheet(officeAgents(LAST_OFFICE).find(e => e.session === 'e2e-r112-q3'));
          document.getElementById('freetext').value = '長'.repeat(2100); }""")
        n_l = len(sent)
        page.evaluate("() => document.querySelector('#quickbtns .qpick .qpsend')?.click()")
        _wait_sent(page, sent, n_l + 1)
        try:
            page.wait_for_function("() => ((document.getElementById('note') || {}).textContent || '').includes('補足を短くしました')", timeout=3000)
        except PWTimeout:
            pass
        lnote = page.evaluate("() => (document.getElementById('note') || {}).textContent || ''")
        got = answers(n_l)
        ltext = got[0]["text"] if len(got) == 1 else ""
        if (len(got) == 1 and got[0]["ok"] and got[0]["ask"] == "a1b2c3d4" and len(ltext or "") == 2000
                and (ltext or "").startswith(want + "\n補足: 長") and "補足を短くしました" in lnote):
            print(f"  ✓ R112-P2 ① 2100 字の補足は削って本文 {len(ltext)} 字（Mac の受け手の上限 2000 字）・「{lnote}」")
        else:
            bad(f"P2-① 長い補足で 2000 字を超える／削ったことを言わない: 送信={[(len(g['text'] or ''), (g['text'] or '')[:40]) for g in got]} 表示={lnote!r}")
        page.evaluate("() => { closeSheet(); setView('wait', true); }")

        # R112-P2 ② 束ねたカード（avatarMode=project）: 同じプロジェクトの 2 セッション（同じ問い・別の印）
        def two(sess, id8):
            return {"session": sess, "projectId": "e2e-r112-proj", "disp": "E2E束", "state": "waiting", "listening": True,
                    "verb": "検討中", "question": "どちらにしますか?",
                    "questionOptions": [{"label": "左", "desc": ""}, {"label": "右", "desc": ""}],
                    "ask": {"kind": "question", "tool": "AskUserQuestion", "qn": 2, "multi": 0, "id8": id8,
                            "questions": [{"q": "どちらにしますか?", "options": ["左", "右"], "multi": 0},
                                          {"q": "いつにしますか?", "options": ["今日", "明日"], "multi": 0}]}}
        pa, pb = two("e2e-r112-pa", "aa11aa11"), two("e2e-r112-pb", "bb22bb22")

        def serve_proj(lead):
            proj = dict(lead, crew=2, sessions=[{"session": m["session"], "vendor": "claude", "state": "waiting", "age": 5,
                                                 "attention": True, "minions": 0, "pending": False, "listening": True,
                                                 "ask": {"tool": "AskUserQuestion", "kind": "question"}} for m in (pa, pb)])
            o = {"officeName": "E2E R112", "avatarMode": "project", "roster": [proj], "employees": [pa, pb], "actions": off_mine}
            serve["status"] = json.dumps({"ok": True, "ts": int(time.time() * 1000), "agentSeenAgo": 0, "agentOnline": True,
                                          "json": json.dumps(o, ensure_ascii=False)}, ensure_ascii=False)
            page.evaluate("() => poll()")
            page.wait_for_function("(s) => officeAgents(LAST_OFFICE).length === 1 && officeAgents(LAST_OFFICE)[0].session === s",
                                   arg=lead["session"], timeout=8000)
            page.wait_for_timeout(750)                     # B1: 人が替わった直後 600ms は送る操作を受けない
        G = '#attncards .attncard[data-attn-sess="e2e-r112-proj"]'
        gp = """(C) => { const c = document.querySelector(C); if (!c) return null; const b = c.querySelector('.qpick'), go = c.querySelector('.qpsend');
          return { key: b ? b.getAttribute('data-qpick') : null, dis: go ? go.disabled : null,
                   on: [...c.querySelectorAll('.qpo[aria-checked="true"]')].map(n => n.getAttribute('data-qi') + ':' + n.getAttribute('data-oi')).sort() }; }"""
        serve_proj(pa)                                       # 代表＝A
        page.evaluate("() => setView('wait', true)")
        page.wait_for_timeout(200)
        hit_a = tap(G + ' .qpq[data-qi="0"] .qpo[data-oi="1"]')     # A の 1 問目「右」
        g1 = page.evaluate(gp, G)
        serve_proj(pb)                                       # 代表が B に替わる（A は束の中で待ったまま＝非代表）
        g2 = page.evaluate(gp, G)
        # 非代表になった A の要求に答える（束の中のセッション＝employees の A。端末の q 行と同じ選択の箱）
        sh2 = page.evaluate("""() => { openSheet((LAST_OFFICE.employees || []).find(e => e.session === 'e2e-r112-pa'));
          const b = document.querySelector('#quickbtns .qpick');
          const r = { key: b ? b.getAttribute('data-qpick') : null,
                      on: [...document.querySelectorAll('#quickbtns .qpick .qpo.on')].map(n => n.getAttribute('data-qi') + ':' + n.getAttribute('data-oi')).sort() };
          document.querySelector('#quickbtns .qpick .qpq[data-qi="1"] .qpo[data-oi="1"]')?.click(); closeSheet(); return r; }""")
        page.evaluate("() => poll()")                        # status の取り直し（代表は B のまま）＝刈り込みが走る
        page.wait_for_timeout(900)
        page.evaluate("() => updateAttnCards(officeAgents(LAST_OFFICE))")
        alive = page.evaluate("(k) => QPICK[k] ? JSON.stringify(QPICK[k].p) : null", "e2e-r112-pa\naa11aa11")
        g3 = page.evaluate(gp, G)
        serve_proj(pa)                                       # 代表が A に戻る
        g4 = page.evaluate(gp, G)
        n_g = len(sent)
        tap(G + " .qpsend")
        _wait_sent(page, sent, n_g + 1)
        got = answers(n_g)
        if (hit_a and g1 and g1["on"] == ["0:1"] and g2 and g2["on"] == [] and (g2["key"] or "").endswith("bb22bb22")
                and sh2["key"] == "e2e-r112-pa\naa11aa11" and sh2["on"] == ["0:1"] and alive == "[[1],[1]]"
                and g3 and g3["on"] == [] and g4 and g4["on"] == ["0:1", "1:1"] and g4["dis"] is False
                and len(got) == 1 and got[0]["ok"] and got[0]["session"] == "e2e-r112-pa" and got[0]["ask"] == "aa11aa11"
                and got[0]["text"] == "1. どちらにしますか?: 右\n2. いつにしますか?: 明日"):
            print("  ✓ R112-P2 ② 束ねたカード: 代表が替わっても非代表の A の選択は残り（シートで選び足せる）・代表 B の箱には付かない・"
                  "A に戻れば同じ選択で 1 通（宛先 A・印 aa11aa11）")
        else:
            bad(f"P2-② 束ねたカードの選択が消える／別の相手に付く: A={hit_a},{g1} B={g2} シートA={sh2} 控え={alive} "
                f"B(取り直し)={g3} A(戻り)={g4} 送信={got}")

        # R112-P2 ③ 再読み込み（版の更新・復帰）で未送信の選択が戻る・もうあなた待ちに居ない相手の控えは戻さない・送ったら控えが消える
        q3r = dict(q3, ask=dict(q3["ask"], id8="d4c3b2a1"))
        reserve([q3r], off_mine)
        page.evaluate("() => setView('wait', true)")
        page.wait_for_timeout(200)
        hits_r = [tap(C + f' .qpq[data-qi="{qi}"] .qpo[data-oi="{oi}"]') for qi, oi in ((0, 1), (1, 2))]
        ss0 = page.evaluate("""() => { const raw = sessionStorage.getItem('aioffice.qpick'), d = JSON.parse(raw || 'null');
          if (d) { d.m['e2e-r112-gone\\nfeedf00d'] = { p: [[0], [0], [0]], at: Date.now() }; sessionStorage.setItem('aioffice.qpick', JSON.stringify(d)); }
          return raw; }""")
        page.reload(wait_until="domcontentloaded")
        page.wait_for_function("() => typeof updateAttnCards === 'function' && QPICK_LIVE && officeAgents(LAST_OFFICE).length === 1", timeout=30000)
        page.evaluate("() => setView('wait', true)")
        page.wait_for_timeout(750)
        st_r = page.evaluate(probe, C)
        keys_r = page.evaluate("() => ({ mem: Object.keys(QPICK), ss: Object.keys((JSON.parse(sessionStorage.getItem('aioffice.qpick') || 'null') || { m: {} }).m) })")
        tap(C + ' .qpq[data-qi="2"] .qpo[data-oi="0"]')
        n_r = len(sent)
        tap(C + " .qpsend")
        _wait_sent(page, sent, n_r + 1)
        page.wait_for_timeout(300)
        ss_after = page.evaluate("() => sessionStorage.getItem('aioffice.qpick')")
        got = answers(n_r)
        want_r = "1. どの方式にしますか?: 方式B\n2. どの機能を入れますか?: 履歴\n3. いつ出しますか?: 今日"
        if (all(hits_r) and ss0 and "e2e-r112-q3\nd4c3b2a1" in json.loads(ss0)["m"] and "どの方式" not in ss0
                and st_r and sorted(st_r["on"]) == ["0:1", "1:2"] and "あと 1 問" in st_r["left"]
                and keys_r["mem"] == ["e2e-r112-q3\nd4c3b2a1"] and keys_r["ss"] == ["e2e-r112-q3\nd4c3b2a1"]
                and len(got) == 1 and got[0]["ok"] and got[0]["ask"] == "d4c3b2a1" and got[0]["text"] == want_r and ss_after is None):
            print("  ✓ R112-P2 ③ 選んだ後の再読み込みで選択が戻る（控えは鍵と番号だけ・居ない相手の控えは捨てる）・送ったら控えが消える")
        else:
            bad(f"P2-③ 再読み込みで選択が戻らない／控えが残る: 押下={hits_r} 控え={ss0} 戻った={st_r} 鍵={keys_r} 送信={got} 送った後の控え={ss_after!r}")

        # ③ R116-S1→R118: スマホから許可が OFF＝カードの中に「ON が切れています」と［ON にして確認］（設定へ飛ばさない）・押すとその場で
        # ON の封筒（rst-・パスキーも Face ID も呼ばない）・［拒否］［拒否して指示…］は残る・ホームの先頭の 1 件も同じカード
        reserve([perm], off_mine)
        page.evaluate("() => setView('office', true)")
        page.wait_for_timeout(300)
        card_js = """() => { const c = document.getElementById('apvwrap'), vis = (id) => { const n = document.getElementById(id); return !!n && !n.hidden && n.getClientRects().length > 0; };
          return { host: c.parentNode ? c.parentNode.id : '', state: c.getAttribute('data-state'), why: document.getElementById('apvwhy').textContent,
                   fix: vis('apv_fix') ? document.getElementById('apv_fix').textContent : '', allow: vis('apv_allow'),
                   deny: vis('apvdeny') ? document.getElementById('apvdeny').textContent : '', write: vis('apvdenywrite') ? document.getElementById('apvdenywrite').textContent : '' }; }"""
        g1 = page.evaluate(card_js)
        page.evaluate("""() => { window.__r112pk = 0; const c = navigator.credentials, no = () => { window.__r112pk++; return Promise.reject(new Error('r112-stub')); };
          if (c) { c.get = no; c.create = no; } }""")
        n_on = len(sent)
        fixed = tap("#apv_fix")
        rst = []
        for _ in range(30):
            rst = [(ok, json.loads(text or "{}")) for ok, _why, sess, text in (_verify(raw, device_id, secret) for raw in sent[n_on:])
                   if str(sess).startswith("rst-")]
            if rst:
                break
            page.wait_for_timeout(100)
        page.wait_for_timeout(200)
        nav = page.evaluate("""() => ({ view: VIEW, pk: window.__r112pk, fix: document.getElementById('apv_fix').textContent,
          state: (document.getElementById('st_approve_state') || {}).textContent || '' })""")
        page.evaluate("() => { APV_RST_REQ = null; apvBusyEnd(); apvRender(); }")      # Mac の結果は来ない（status は固定）＝送信中を解く
        if (g1["host"] == "apvslot_home" and g1["state"] == "need-on" and g1["why"] == "ON が切れています" and g1["fix"] == "ON にして確認"
                and not g1["allow"] and g1["deny"] == "拒否" and g1["write"] == "拒否して指示…" and fixed and nav["view"] == "office"
                and nav["pk"] == 0 and len(rst) == 1 and rst[0][0] and list(rst[0][1]) == ["aioffice", "kind", "reqId", "on"]
                and rst[0][1]["on"] is True and nav["fix"] == "送信中…" and nav["state"] == "スマホから許可: OFF"):
            print("  ✓ R116-S1→R118 ③ OFF の許可待ち＝ホームのカードに「ON が切れています」＋［ON にして確認］→ その場で ON の封筒 1 通"
                  "（パスキーも Face ID も呼ばない・設定へ飛ばない・Mac の受理までは「送信中…」）・拒否と拒否して指示…は残る")
        else:
            bad(f"③ OFF のカードが違う: カード={g1} ボタン={fixed} 押した後={nav} ON 封筒={rst}")
        # 期限切れ（Mac は期限後 until=0 で送る＝端末が見ていた終了時刻の控えから時刻を言う）
        exp = page.evaluate("""() => { localStorage.setItem('aioffice.apvUntil', String(Math.floor(Date.now() / 1000) - 3600));
          APV.sig = ''; apvRender(); const r = { why: document.getElementById('apvwhy').textContent };
          localStorage.removeItem('aioffice.apvUntil'); return r; }""")
        if re.search(r"^ON が切れています（\d\d:\d\d）$", exp["why"]):
            print(f"  ✓ R116-S1 ③ 期限が切れた OFF は時刻つき（「{exp['why']}」）")
        else:
            bad(f"③ 期限切れの時刻が出ない: {exp}")
        # ⑥ 更新直後の 600ms に押した操作（許可以外のカード＝質問の選択肢）は送らず、黙らずに言う。許可のカードは新着で動かない＝ガードは要らない
        qs1 = {"session": "e2e-r112-qs", "projectId": "e2e-r112-qs", "disp": "E2E一問", "state": "waiting", "listening": True,
               "verb": "検討中", "question": "どちらにしますか?", "questionOptions": [{"label": "案A", "desc": ""}, {"label": "案B", "desc": ""}],
               "ask": {"kind": "question", "tool": "AskUserQuestion", "id8": "5151aaaa"}}
        reserve([qs1], off_mine)
        page.evaluate("() => { closeSheet(); setView('wait', true); }")
        page.wait_for_timeout(300)
        n2 = len(sent)
        page.evaluate("() => { ATTN_GUARD = Date.now() + 5000; }")
        tap('#attncards .attncard[data-attn-sess="e2e-r112-qs"] .attnoptions .qopt')
        page.wait_for_timeout(300)
        held = page.evaluate("() => { const t = (document.getElementById('note') || {}).textContent || ''; ATTN_GUARD = 0; qpendCancel(true); closeSheet(); return t; }")
        if "画面が更新されました" in held and len(sent) == n2:
            print("  ✓ R112-P ⑥ 更新直後（600ms）に押した操作（質問の選択肢）は送らず「画面が更新されました。もう一度押してください」と言う"
                  "（R116-S1: 許可のカードは新着で動かない＝ガードは要らない）")
        else:
            bad(f"⑥ 更新直後に押した操作を黙って捨てる／送った: 表示={held!r} 送信={len(sent) - n2}")

        # ④ R118（本人裁定 2026-10-13「常に ON」）: この端末の許可鍵が無くても（Mac の鍵は他の端末のものだけ）、ON ならカードは封書を
        # 取りに行く（loading・dlg- の要求）＝許可鍵の登録の導線（need-setup・［このスマホで許可できるようにする］）は無い
        n_ask = len(sent)
        page.evaluate("() => { localStorage.removeItem('aioffice.apv'); closeSheet(); setView('wait', true); }")
        reserve([perm], {"caps": {"approve": 1}, "approve": {"on": 1, "until": 4102444800, "fps": ["otherfp"], "enr": []}})
        g2 = page.evaluate(card_js)
        page.wait_for_timeout(500)
        asks = [sess for _ok, _why, sess, _text in (_verify(raw, device_id, secret) for raw in sent[n_ask:]) if str(sess).startswith("dlg-")]
        page.evaluate("() => paintSettingsState()")
        nav2 = page.evaluate("""() => ({ view: VIEW, pk: window.__r112pk,
          state: (document.getElementById('st_approve_state') || {}).textContent || '' })""")
        if (g2["host"] == "apvslot_wait" and g2["state"] == "loading" and not g2["fix"] and not g2["why"] and g2["allow"]
                and g2["deny"] == "拒否" and len(asks) >= 1 and nav2["view"] == "wait" and nav2["pk"] == 0
                and nav2["state"] == "スマホから許可: ON（期限なし）"):
            print("  ✓ R118 ④ 許可鍵の無い端末でも ON ならタブのカードは封書を取りに行く（loading・要求が出る・登録の導線は無い）・"
                  "状態の行は「スマホから許可: ON（期限なし）」")
        else:
            bad(f"④ 許可鍵の無い端末のカードが違う: カード={g2} 要求={len(asks)} 後={nav2}")
        page.evaluate("() => { closeSettings(); setView('wait', true); }")

        # ⑤ アプリ本体の JS エラーは /diag に kind:"app" で 1 回（60 秒に 1 回まで）・送信の失敗は kind:"send"（本文・session・askId は無し）
        try:                                                 # 部屋の起動待ち（filename の無いエラーは部屋の理由として数える）が終わってから
            page.wait_for_function("() => SCENE3D !== 'pending'", timeout=60000)
        except PWTimeout:
            pass
        spurious = [d for d in diag if '"kind":"app"' in d or '"kind":"send"' in d]
        page.evaluate("""() => { setTimeout(() => { throw new Error('r112-app-probe'); }, 0);
          setTimeout(() => { throw new Error('r112-app-probe-2'); }, 30); }""")
        page.wait_for_timeout(600)
        apps = [json.loads(d) for d in diag if '"kind":"app"' in d]
        n3, d0 = len(sent), len(diag)
        mode["instruct"] = "500"
        page.evaluate("() => setView('office', true)")
        page.wait_for_timeout(200)
        tap("#apvdeny")
        _wait_sent(page, sent, n3 + 1, ms=6000)
        page.wait_for_timeout(600)
        toast = page.evaluate("() => (document.getElementById('note') || {}).textContent || ''")
        sends = [d for d in diag[d0:] if '"kind":"send"' in d]
        # 通信そのものの失敗（fetch の例外）も段の名前で（60 秒の間引きは種類ごと＝ここでは時計の控えを戻して 2 回目を見る）
        mode["instruct"] = "abort"
        page.evaluate("() => { if (window.DIAG_AT) DIAG_AT.send = 0; }")
        n4 = len(sent)
        page.evaluate("() => send('r112 の本文', officeAgents(LAST_OFFICE).find(e => e.session === 'e2e-r112-perm'))")
        _wait_sent(page, sent, n4 + 1)
        page.wait_for_timeout(600)
        sends2 = [d for d in diag[d0:] if '"kind":"send"' in d]
        mode["instruct"] = "ok"
        e_app = apps[0].get("err") if len(apps) == 1 else None
        e_send = json.loads(sends[0]).get("err") if len(sends) == 1 else None
        e_fetch = json.loads(sends2[-1]).get("err") if len(sends2) == 2 else None
        leaked = [d for d in diag if any(s in d for s in ("e2e-r112", "c0ffee01", "a1b2c3d4", "許可しません", "r112 の本文"))]
        if (not spurious and e_app and set(e_app) == {"k", "m", "f", "l", "c"} and e_app["k"] == "error" and "r112-app-probe" in e_app["m"]
                and e_send and e_send["k"] == "http" and "HTTP 500" in e_send["m"] and "r112-boom" in e_send["m"] and "r112-boom" in toast
                and e_fetch and e_fetch["k"] == "fetch" and not leaked):
            print(f"  ✓ R112-P ⑤ /diag: JS エラーは kind:app で 1 回（{e_app['m'][:30]}…）・送信の失敗は kind:send（{e_send['m']}／{e_fetch['k']}）"
                  "・本文/session/askId は載らない")
        else:
            bad(f"⑤ /diag の報告が違う: 先に出ていた={spurious} app={apps} send={sends} fetch={sends2[-1:] if sends2 else None} "
                f"トースト={toast!r} 漏れ={leaked}")
        rest = [e for e in errors if "r112-app-probe" not in e]
        if rest:
            bad(f"page error: {rest[:3]}")
        ctx.close()
    except Exception as exc:
        bad(f"途中で例外: {type(exc).__name__}: {exc}")
    finally:
        browser.close()
    ng = len(fails)
    return ng


def _r113_pins(p, base_url, device_id, secret, token):
    """R113-A（本人 2026-10-08「スマホの登録がめちゃくちゃ回りくどい」）→R118（本人裁定 2026-10-13「Claude のスマホアプリと同じく、
    サクッと許可」「常に ON」）: 許可鍵の登録は撤去＝設定の ┤ 許可 ├ は「ON にする／OFF にする」のボタン 1 つ・ON も OFF も封筒（rst-）だけ。
    出た封筒は Mac 側の本物の検証（office_approve.remote_set）に通す。navigator.credentials（パスキー）は数え、最後まで 0 回。
      ① 主ボタンの連打（と JS からの呼び直し）でも ON の封筒は 1 通・押した瞬間に止まって「送信中…」
      ② ON の封筒の本文は aioffice・kind・reqId・on だけ（hours と WebAuthn の 4 項目は無い）→ Mac 側の検証が受理・期限なし（2100-01-01）・
         設定は「ON（期限なし）」＋［OFF にする］・状態の行は「スマホから許可: ON（期限なし）」
      ③ OFF も同じボタン 1 回（封筒だけ）→ Mac 側で OFF・設定は「OFF」＋［ON にする］
      ④ 再ペアリングの説明は「通常は不要」・許可鍵に触れない
      ⑥ Mac の理由（rate）が失敗の 8 行の 1 行になり、ボタンは［ON にする］に戻る
      ⑤ 許可の結果は窓を閉じていても状態の行（R116-S0）・要求が先に終わっていたら「Mac で先に答えが出ました」・no-ask の言い方
    状態は page.route の /status で固定し WebSocket は止める（中継の DO の状態で上書きされない）。"""
    import tempfile as _tf
    from urllib.parse import urlsplit
    fails = []

    def bad(msg):
        fails.append(msg)
        print(f"  ✗ R113-A {msg}")

    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "server"))
    home = _tf.mkdtemp(prefix="r113-smoke-")
    old_home = os.environ.get("OFFICE_HOME")
    os.environ["OFFICE_HOME"] = home
    try:
        import office_approve as oa  # noqa: E402
    except Exception as exc:  # noqa: BLE001
        print(f"  ✗ R113-A 検証側の部品が読めない: {exc}")
        return 1
    u = urlsplit(base_url)
    lbase = f"{u.scheme}://localhost:{u.port}"
    rp = ("localhost", lbase)
    now0 = int(time.time())
    devices = {"version": 1, "devices": {device_id: {"secret": secret, "label": "r113", "created": now0,
                                                     "expires": now0 + 86400, "revoked": False}}}
    verify = _office_server().verify_envelope
    cred = json.dumps({"d": device_id, "s": secret, "t": token, "e": 0}, separators=(",", ":"))
    perm = {"session": "e2e-r113-perm", "projectId": "e2e-r113-perm", "disp": "E2E許可113", "state": "waiting", "listening": True,
            "verb": "実行中", "approvalMin": 1, "ask": {"kind": "permission", "tool": "Bash", "remote": 1, "id8": "1130beef"}}
    browser = p.chromium.launch()
    sent, serve = [], {"status": None}

    def office(approve, results=(), emps=()):
        o = {"officeName": "E2E R113", "employees": list(emps),
             "actions": {"caps": {"approve": 1 if approve.get("on") else 0}, "approve": approve, "results": list(results)}}
        serve["status"] = json.dumps({"ok": True, "ts": int(time.time() * 1000), "agentSeenAgo": 0, "agentOnline": True,
                                      "json": json.dumps(o, ensure_ascii=False)}, ensure_ascii=False)

    def appr(on=0, until=0):
        return {"on": on, "until": until, "fps": [], "enr": []}

    def on_instruct(route, request):
        if request.method != "POST":
            route.continue_()
            return
        try:
            sent.append(json.loads(request.post_data or "{}"))
        except ValueError:
            sent.append({})
        route.fulfill(status=200, content_type="application/json", body='{"ok":true}')

    def on_status(route, request):
        if request.method == "GET" and serve["status"] is not None:
            route.fulfill(status=200, content_type="application/json", body=serve["status"])
        else:
            route.continue_()

    def envs(prefix):
        return [x for x in sent if str(x.get("session", "")).startswith(prefix)]

    def wait_env(prefix, k, ms=10000):
        end = time.time() + ms / 1000
        while time.time() < end:
            got = envs(prefix)
            if len(got) >= k:
                return got[k - 1]
            page.wait_for_timeout(100)
        return None

    def repoll(cond, arg=None, ms=8000):
        page.evaluate("() => poll()")
        try:
            page.wait_for_function(cond, arg=arg, timeout=ms)
            return True
        except PWTimeout:
            return False

    main_js = """() => { const g = document.getElementById('st_apv_go'), h = document.getElementById('st_apv_hint');
      return { go: g ? g.textContent : null, act: g ? g.getAttribute('data-act') : null, dis: !!(g && g.disabled),
               hint: h ? h.textContent : '', now: (document.getElementById('st_apv_now') || {}).textContent || '',
               state: (document.getElementById('st_approve_state') || {}).textContent || '',
               buttons: [...document.querySelectorAll('#st_apv_main button')].map(b => b.id), busy: APV_BUSY ? APV_BUSY.kind : null }; }"""
    try:
        ctx = browser.new_context(viewport={"width": 390, "height": 844}, has_touch=True, is_mobile=True, service_workers="block")
        page = ctx.new_page()
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.on("dialog", lambda d: d.accept())
        page.add_init_script("localStorage.setItem('aioffice.cred', " + json.dumps(cred) + ");"
                             "window.WebSocket = function () { throw new Error('r113: WS off'); };"
                             "window.__pk = 0;"
                             "if (navigator.credentials) { const no = () => { window.__pk++; return Promise.reject(new Error('r118: no passkey')); };"
                             "  navigator.credentials.get = no; navigator.credentials.create = no; }")
        page.route("**/instruct", on_instruct)
        page.route("**/status", on_status)
        page.route("**/diag", lambda route: route.fulfill(status=204, body=""))
        office(appr())
        page.goto(lbase + "/app/", wait_until="domcontentloaded")
        page.wait_for_function("() => typeof apvRemoteSet === 'function' && typeof apvMainClick === 'function' && !!(LAST_OFFICE && LAST_OFFICE.actions)",
                               timeout=30000)
        page.evaluate("() => setView('settings')")
        page.wait_for_timeout(300)

        # ① 主ボタンの連打（＋JS からの呼び直し）でも ON の封筒は 1 通・押した瞬間に止まる
        repoll("() => !apvOnNow()")
        page.evaluate("() => { APV_MSG = null; apvEnrollState(); paintSettingsState(); }")
        g0 = page.evaluate(main_js)
        nr = len(envs("rst-"))
        burst = page.evaluate("""() => { const g = document.getElementById('st_apv_go'); g.click();
          const r = { label: g.textContent, dis: g.disabled, busy: APV_BUSY && APV_BUSY.kind };
          g.click(); g.click(); apvMainClick(); apvRemoteSet(true, 0, 'main'); apvRemoteSet(false, 0, 'main'); return r; }""")
        s1 = wait_env("rst-", nr + 1)
        page.wait_for_timeout(2500)
        n_on = len(envs("rst-")) - nr
        if (g0["go"] == "ON にする" and g0["act"] == "on" and g0["now"] == "OFF" and g0["buttons"] == ["st_apv_go"] and not g0["hint"]
                and g0["state"] == "スマホから許可: OFF" and burst["dis"] and burst["busy"] == "on" and burst["label"] == "送信中…"
                and s1 and n_on == 1):
            print("  ✓ R118 ① 設定の ┤ 許可 ├ は「OFF」＋［ON にする］の 1 つ・押した瞬間に止まる（送信中…）・連打と呼び直しでも ON の封筒は 1 通")
        else:
            bad(f"① 主ボタンが違う／連打で ON の封筒が {n_on} 通: 前={g0} 押した直後={burst}")

        # ② Mac 側の検証が受理・期限なし → 画面は「ON（期限なし）」＋［OFF にする］
        r2 = oa.remote_set(s1, devices=devices, verify=verify, rp=rp) if s1 else ("none", "no envelope", {})
        inner2 = json.loads((s1 or {}).get("text") or "{}")
        until2 = int((r2[2] or {}).get("until") or 0)
        rid2 = str((s1 or {}).get("session", ""))[4:]
        office(appr(on=1, until=until2),
               results=[{"reqId": rid2, "kind": "remote_set", "state": r2[0], "output": r2[1] or str(until2), "startedAt": now0 + 1}])
        done2 = repoll("() => (document.getElementById('st_apv_now').textContent || '') === 'ON（期限なし）' && !APV_BUSY")
        page.evaluate("() => paintSettingsState()")
        a2 = page.evaluate(main_js)
        if (r2[0] == "granted" and list(inner2) == ["aioffice", "kind", "reqId", "on"] and inner2.get("on") is True
                and inner2.get("reqId") == rid2 and until2 == oa.REMOTE_ALWAYS and done2 and not a2["hint"] and a2["go"] == "OFF にする"
                and a2["act"] == "off" and a2["state"] == "スマホから許可: ON（期限なし）" and a2["buttons"] == ["st_apv_go"]):
            print("  ✓ R118 ② ON の封筒（本文は aioffice・kind・reqId・on だけ＝hours も WebAuthn の 4 項目も無い）を Mac 側の検証が受理・"
                  "期限なし（2100-01-01）・設定は「ON（期限なし）」＋［OFF にする］・状態の行は「スマホから許可: ON（期限なし）」")
        else:
            bad(f"② ON が違う: Mac={r2[:2]} inner={inner2} until={until2} 反映={done2} 画面={a2}")

        # ③ OFF も同じボタン 1 回（封筒だけ）→ Mac 側で OFF（ON/OFF は端末ごと 10 秒に 1 回＝Mac の時計を進めて受ける）
        nr = len(envs("rst-"))
        page.click("#st_apv_go")
        s3 = wait_env("rst-", nr + 1)
        r3 = (oa.remote_set(s3, devices=devices, verify=verify, rp=rp, now=int(time.time()) + oa.RST_GAP + 1)
              if s3 else ("none", "no envelope", {}))
        inner3 = json.loads((s3 or {}).get("text") or "{}")
        rid3 = str((s3 or {}).get("session", ""))[4:]
        office(appr(), results=[{"reqId": rid3, "kind": "remote_set", "state": r3[0], "output": r3[1] or "0", "startedAt": now0 + 2}])
        done3 = repoll("() => (document.getElementById('st_apv_now').textContent || '') === 'OFF' && !APV_BUSY")
        page.evaluate("() => paintSettingsState()")
        a3 = page.evaluate(main_js)
        if (r3[0] == "granted" and list(inner3) == ["aioffice", "kind", "reqId", "on"] and inner3.get("on") is False
                and oa.remote_until() <= time.time() and done3 and a3["go"] == "ON にする" and a3["act"] == "on" and not a3["hint"]
                and a3["state"] == "スマホから許可: OFF"):
            print("  ✓ R118 ③ OFF も同じボタン 1 回（封筒だけ）→ Mac 側で OFF・設定は「OFF」＋［ON にする］")
        else:
            bad(f"③ OFF が違う: Mac={r3[:2]} inner={inner3} until={oa.remote_until()} 反映={done3} 画面={a3}")

        # ④ 再ペアリングの説明（R118: 許可鍵は無い＝触れない）
        rp_text = page.evaluate("() => document.getElementById('st_rp_hint').textContent")
        if rp_text == "通常は不要です。スマホを替えたときだけ使います（Mac の QR からペアリングをやり直します）":
            print("  ✓ R118 ④ 再ペアリングの説明は「通常は不要」で許可鍵に触れない（許可鍵の登録は無い）")
        else:
            bad(f"④ 再ペアリングの説明が違う: {rp_text!r}")

        # ⑥ ON の拒否（rate）は失敗の 8 行の 1 行・ボタンは［ON にする］に戻る
        page.evaluate("() => { APV_MSG = null; apvEnrollState(); }")
        nr = len(envs("rst-"))
        page.click("#st_apv_go")
        s6 = wait_env("rst-", nr + 1)
        rid6 = str((s6 or {}).get("session", ""))[4:]
        office(appr(), results=[{"reqId": rid6, "kind": "remote_set", "state": "refused", "output": "rate", "startedAt": now0 + 4}])
        got6 = repoll("() => (document.getElementById('st_apv_hint').textContent || '') === '! 少し待ってからもう一度'")
        a6 = page.evaluate(main_js)
        if s6 and got6 and a6["hint"] == "! 少し待ってからもう一度" and not a6["busy"] and a6["go"] == "ON にする":
            print("  ✓ R113-A→R118 ⑥ ON の拒否（rate）は失敗の 8 行の「少し待ってからもう一度」・ボタンは［ON にする］に戻る")
        else:
            bad(f"⑥ ON の拒否の言い方が違う: 封筒={bool(s6)} 反映={got6} 画面={a6}")

        # ⑤ R116-S1: 許可の結果は状態の行（「✓ 許可」）・見ていた要求が Mac で先に終わったら状態の行に「Mac で先に答えが出ました」・no-ask も同じ 1 行
        office(appr(on=1, until=oa.REMOTE_ALWAYS), emps=[perm])
        repoll("() => officeAgents(LAST_OFFICE).length === 1")
        page.evaluate("() => { setView('office', true); APV_SENT_LOG['r113-toast'] = Date.now(); }")
        office(appr(on=1, until=oa.REMOTE_ALWAYS), emps=[perm],
               results=[{"reqId": "r113-toast", "kind": "approve", "state": "granted", "output": "", "startedAt": now0 + 5}])
        toast = repoll("() => ((document.querySelector('#ticker .tk-login') || {}).textContent || '') === '✓ 許可'")
        page.evaluate("(e) => { openApprove(e); APV.page = { level: 'remote', askId: '0'.repeat(32), tool: 'Bash', inputSha: '0'.repeat(64), iat: Math.floor(Date.now() / 1000) }; }", perm)
        office(appr(on=1, until=oa.REMOTE_ALWAYS), emps=[dict(perm, state="working", ask=None, approvalMin=0)])
        ended = repoll("() => ((document.querySelector('#ticker .tk-login') || {}).textContent || '') === 'Mac で先に答えが出ました' && !APV")
        office(appr(on=1, until=oa.REMOTE_ALWAYS), emps=[perm])
        repoll("() => officeAgents(LAST_OFFICE).length === 1 && !!sessKind(officeAgents(LAST_OFFICE)[0])")
        page.evaluate("() => { APV_SENT_LOG['r113-noask'] = Date.now(); APV_SENT_WHO['r113-noask'] = 'E2E許可113'; }")
        office(appr(on=1, until=oa.REMOTE_ALWAYS), emps=[perm],
               results=[{"reqId": "r113-noask", "kind": "approve", "state": "refused", "output": "no-ask", "startedAt": now0 + 6}])
        noask = repoll("() => ((document.getElementById('note') || {}).textContent || '') === '! Mac で先に答えが出ました — E2E許可113'")
        page.evaluate("() => closeApprove(true)")
        if toast and ended and noask:
            print("  ✓ R113-A ⑤ 許可の結果は状態の行に「✓ 許可」・見ていた要求が Mac で先に終わったら「Mac で先に答えが出ました」（カードは次へ）・"
                  "no-ask も同じ 1 行（R116-S1: 失敗は 8 行）")
        else:
            bad(f"⑤ 届いた／終わっていたの言い方が違う: 状態の行={toast} 終わっていた={ended} no-ask={noask}")
        pk = page.evaluate("() => window.__pk")
        if pk:
            bad(f"パスキーを呼んだ: {pk} 回")
        else:
            print("  ✓ R118 設定の ON/OFF とカードの間、パスキー（navigator.credentials）を 1 回も呼ばない")
        if errors:
            bad(f"page error: {errors[:3]}")
        ctx.close()
    except Exception as exc:  # noqa: BLE001  操作の待ちが切れた等＝赤として数える（後ろのピンを止めない）
        bad(f"途中で例外: {type(exc).__name__}: {str(exc)[:200]}")
    finally:
        browser.close()
        if old_home is None:
            os.environ.pop("OFFICE_HOME", None)
        else:
            os.environ["OFFICE_HOME"] = old_home
    return len(fails)


def _r114_tap_pins(p, base_url, device_id, secret, token):
    """R114-P（本人裁定 2026-10-08「スマホで確認して許可すると Face ID を求められたり、QR コードをスキャンすることになったりする。全く許可
    できない」→ ON の間はタップだけ）→R118（本人裁定 2026-10-13「Claude のスマホアプリと同じく、サクッと許可」）: 段を問わず 1 タップ。
    navigator.credentials の get／create はスタブ（呼んだ回数を数える＝最後まで 0 回）。この端末に許可鍵は無い。状態は page.route の /status で
    固定し WebSocket は止める（中継の DO の状態で上書きされない）。/instruct と /diag も route で受ける。
      ① ON×review・ON×remote: パスキーを呼ばず /instruct にタップの封筒（mode:"tap"）が 1 通（外側の HMAC は Mac の verify_envelope が受理・
         本文は契約の並び）・結果 granted で窓が閉じ、状態の行に「✓ 許可 — <名前>」（R116-S0）
      ② OFF（caps も 0）: カードは「ON が切れています」と［ON にして確認］・許可ボタンは押せない・JS から押してもタップを送らない（/diag に off）。
         ON の期限が手元で切れた（Mac の caps は残っている）ときも同じで、［ON にして確認］はその場で ON の封筒（パスキーなし・期限なし）
      ③ ON×mac: 危険の朱の 1 行・要点（道具・入力の先頭）・［全文］の折りたたみ・印もスクロールの義務も無い・［許可］→ パスキーを呼ばずに
         タップの封筒 1 通（R118）
      ④ タップの結果 refused（dangerous＝掲示の後で段が上がった）: 理由がトーストに出て、/diag（kind:approve・k=tap:review）に 1 行
         （本文・session・askId は載せない）
      ⑤ 許可鍵の登録の導線は無い（カードは封書を取りに行く・設定のボタンは ON/OFF の 1 つ）・再ペアリングの説明は「通常は不要」で許可鍵に触れない
    localhost で開く（ほかのピンと同じ）。"""
    import secrets as _s
    from urllib.parse import urlsplit
    fails = []

    def bad(msg):
        fails.append(msg)
        print(f"  ✗ R114-P {msg}")

    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "server"))
    try:
        import approval_policy as ap  # noqa: E402
    except Exception as exc:  # noqa: BLE001
        print(f"  ✗ R114-P 検証側の部品が読めない: {exc}")
        return 1
    u = urlsplit(base_url)
    lbase = f"{u.scheme}://localhost:{u.port}"
    now0 = int(time.time())
    cred = json.dumps({"d": device_id, "s": secret, "t": token, "e": 0}, separators=(",", ":"))
    perm = {"session": "e2e-r114-perm", "projectId": "e2e-r114-perm", "disp": "E2E許可114", "state": "waiting", "listening": True,
            "verb": "実行中", "approvalMin": 1, "ask": {"kind": "permission", "tool": "Bash", "remote": 1, "id8": "114beef0"}}
    browser = p.chromium.launch()
    sent, diag, serve = [], [], {"status": None}

    def office(on=1, until=None, results=(), caps=None):
        approve = {"on": on, "until": 4102444800 if until is None else until, "fps": [], "enr": []}
        o = {"officeName": "E2E R114", "employees": [perm],
             "actions": {"caps": {"approve": (1 if on else 0) if caps is None else caps}, "approve": approve, "results": list(results)}}
        serve["status"] = json.dumps({"ok": True, "ts": int(time.time() * 1000), "agentSeenAgo": 0, "agentOnline": True,
                                      "json": json.dumps(o, ensure_ascii=False)}, ensure_ascii=False)

    def on_instruct(route, request):
        if request.method != "POST":
            route.continue_()
            return
        try:
            sent.append(json.loads(request.post_data or "{}"))
        except ValueError:
            sent.append({})
        route.fulfill(status=200, content_type="application/json", body='{"ok":true}')

    def on_status(route, request):
        if request.method == "GET" and serve["status"] is not None:
            route.fulfill(status=200, content_type="application/json", body=serve["status"])
        else:
            route.continue_()

    def on_diag(route, request):
        diag.append(request.post_data or "")
        route.fulfill(status=204, body="")

    def repoll(cond, arg=None, ms=8000):
        page.evaluate("() => poll()")
        try:
            page.wait_for_function(cond, arg=arg, timeout=ms)
            return True
        except PWTimeout:
            return False

    def open_page(level, inp, extra=None):
        """Mac が封じた全文（掲示 1 件）を受け取った状態を作る（封書の往復は _r100_apv_pins が実物で見る）。"""
        canon = ap.input_canon("Bash", inp)
        pg = {"kind": "ask", "session": perm["session"], "askId": _s.token_hex(16), "tool": "Bash", "inputCanon": canon,
              "inputSha": ap.input_sha(canon), "inputBytes": len(canon.encode()), "cwdBase": "proj", "askTs": now0, "level": level,
              "reasons": [], "tags": [], "macOnly": 0, "agentId": "", "askCount": 1, "iat": int(time.time())}
        pg.update(extra or {})
        page.evaluate("""([e, pg]) => { closeApprove(true); openApprove(e); APV.page = pg; APV.at = Date.now(); APV.sig = ''; apvRender(); }""",
                      [perm, pg])
        return pg

    def press():
        n0, g0 = len(sent), page.evaluate("() => window.__gets")
        page.click("#apv_allow")
        end = time.time() + 6
        while time.time() < end and not any(str(x.get("session", "")).startswith("apv-") for x in sent[n0:]):
            page.wait_for_timeout(100)
        got = [x for x in sent[n0:] if str(x.get("session", "")).startswith("apv-")]
        return (got[0] if len(got) == 1 else None), page.evaluate("() => window.__gets") - g0, len(got)

    def diags(start, kind):
        out = []
        for raw in diag[start:]:
            try:
                d = json.loads(raw)
            except ValueError:
                continue
            if d.get("kind") == kind:
                out.append((d, raw))
        return out

    look_js = """() => { const b = document.getElementById('apv_allow'), lv = document.getElementById('apvlevel'), rest = document.getElementById('apvrest'),
          f = document.getElementById('apv_fix');
        return { dis: b.disabled, label: b.textContent, hidden: b.hidden, ack: !!(document.getElementById('apv_ack') || document.getElementById('apvack')),
          lv: !lv.hidden, cls: lv.className, head: lv.textContent, box: (document.getElementById('apvbox') || {}).textContent || '',
          restHidden: !!(rest && rest.hidden), restText: rest ? rest.textContent : '', why: document.getElementById('apvwhy').textContent,
          fix: f && !f.hidden ? f.textContent : '', note: document.getElementById('apvnote').textContent,
          state: document.getElementById('apvwrap').getAttribute('data-state') }; }"""
    try:
        ctx = browser.new_context(viewport={"width": 390, "height": 844}, has_touch=True, is_mobile=True, service_workers="block")
        page = ctx.new_page()
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.on("dialog", lambda d: d.accept())
        page.add_init_script(
            "localStorage.setItem('aioffice.cred', " + json.dumps(cred) + ");"
            "window.WebSocket = function () { throw new Error('r114: WS off'); };"
            "window.__gets = 0;"
            "if (navigator.credentials) { const no = function () { window.__gets++; return Promise.reject(new Error('r118: no passkey')); };"
            "  navigator.credentials.get = no; navigator.credentials.create = no; }")
        page.route("**/instruct", on_instruct)
        page.route("**/status", on_status)
        page.route("**/diag", on_diag)
        office(on=1)
        page.goto(lbase + "/app/", wait_until="domcontentloaded")
        page.wait_for_function("() => typeof apvTapAllowed === 'function' && !!(LAST_OFFICE && LAST_OFFICE.actions) && apvOnNow()", timeout=30000)

        # ① ON×review → タップ（パスキーを呼ばない）→ granted で「タップで許可しました」
        rv = open_page("review", {"command": "bash verify.sh", "description": "\n".join("検証の手順 %d" % i for i in range(40))},
                       {"reasons": ["script"], "tags": ["script"]})
        a1 = page.evaluate(look_js)
        page.wait_for_timeout(1300)
        env1, gets1, n1 = press()
        ok1, why1, inner1 = _tap_env(env1, device_id, secret, rv["askId"], rv["inputSha"])
        office(on=1, results=[{"reqId": inner1.get("reqId", ""), "kind": "approve", "state": "granted", "output": "", "startedAt": now0}])
        # R116-S0（意図した反転）: granted の後は［閉じる］を押させずに窓を閉じ、状態の行に「✓ 許可 — <名前>（Mac が受け付けました）」
        # （窓の 1 行「✓ タップで許可しました」とトーストは出さない＝同じ知らせを 3 か所に出さない）
        got1 = repoll("() => !document.getElementById('apvwrap').classList.contains('open')"
                      " && ((document.querySelector('#ticker .tk-login') || {}).textContent || '') === '✓ 許可 — E2E許可114'")
        w1 = page.evaluate("() => ({ open: document.getElementById('apvwrap').classList.contains('open'),"
                           " line: (document.querySelector('#ticker .tk-login') || {}).textContent || '' })")
        if (ok1 and gets1 == 0 and n1 == 1 and a1["label"] == "許可" and a1["state"] == "ready" and not a1["ack"]
                and a1["box"] == "bash verify.sh" and a1["restHidden"] and "検証の手順 39" in a1["restText"]
                and not a1["note"] and got1 and not w1["open"]):
            print("  ✓ R114-P ① ON×review: ［許可］（R116-S1）・箱に命令・説明は［全文］の中・印なし → パスキーを呼ばずに"
                  "タップの封筒 1 通（外側の HMAC は Mac の verify_envelope が受理・本文は契約の並び＋ux）→ 中継が預かったらカードは次へ・"
                  "granted で状態の行に「✓ 許可 — E2E許可114」")
        else:
            bad(f"① ON×review のタップが違う: 封筒={why1}/{n1} パスキー={gets1} 画面={a1} 状態の行={got1} 窓={w1!r}")

        # ① ON×remote → タップ
        rm = open_page("remote", {"command": "npm test"})
        a1r = page.evaluate(look_js)
        page.wait_for_timeout(1300)
        env1r, gets1r, n1r = press()
        ok1r, why1r, _ = _tap_env(env1r, device_id, secret, rm["askId"], rm["inputSha"])
        if ok1r and gets1r == 0 and n1r == 1 and a1r["label"] == "許可" and a1r["box"] == "npm test" and not a1r["lv"]:
            print("  ✓ R114-P ① ON×remote: 1 タップ（パスキーを呼ばない）でタップの封筒 1 通")
        else:
            bad(f"① ON×remote のタップが違う: 封筒={why1r}/{n1r} パスキー={gets1r} 画面={a1r}")

        # ③ ON×mac → R118（本人裁定 2026-10-13「危険な種類も全部 1 タップ」）: 危険の朱の 1 行＋要点・［許可］→ パスキーを呼ばずにタップの封筒 1 通
        mc = open_page("mac", {"command": "git push --force origin main", "description": "\n".join("片付けの手順 %d" % i for i in range(40))},
                       {"reasons": ["git-push-force"], "tags": []})
        a3 = page.evaluate(look_js)
        page.wait_for_timeout(1300)
        d3 = page.evaluate("() => document.getElementById('apv_allow').disabled")
        env3, gets3, n3 = press()
        ok3, why3, _inner3 = _tap_env(env3, device_id, secret, mc["askId"], mc["inputSha"])
        if (a3["lv"] and a3["cls"] == "lvdanger" and a3["head"] == "危険: 外へ送る・公開" and a3["box"] == "git push --force origin main"
                and a3["restHidden"] and "片付けの手順 39" in a3["restText"] and not a3["ack"] and a3["label"] == "許可"
                and a3["state"] == "ready" and not d3 and gets3 == 0 and n3 == 1 and ok3):
            print("  ✓ R118 ③ ON×mac: 危険の朱の 1 行（外へ送る・公開）・箱に命令・説明は［全文］の中・印もスクロールも無し・"
                  "［許可］→ パスキーを呼ばずにタップの封筒 1 通（本人裁定「危険な種類も 1 タップ」）")
        else:
            bad(f"③ ON×mac が違う: 画面={a3} 1.3 秒後 disabled={d3} パスキー={gets3} 封筒={n3}/{why3}")

        # ④ タップの結果 refused（dangerous＝掲示の後で段が上がった・R118 で tap-mac は無い）→ 理由をトースト・/diag へ（本文・session・askId は載せない）
        page.evaluate("() => { DIAG_AT = {}; APV_SENT_LOG = {}; APV_SENT_STAGE = {}; }")
        d0 = len(diag)
        rv4 = open_page("review", {"command": "bash scripts/check.sh"}, {"reasons": ["script"], "tags": ["script"]})
        page.wait_for_timeout(1300)
        env4, gets4, n4 = press()
        ok4, why4, inner4 = _tap_env(env4, device_id, secret, rv4["askId"], rv4["inputSha"])
        office(on=1, results=[{"reqId": inner4.get("reqId", ""), "kind": "approve", "state": "refused", "output": "dangerous", "startedAt": now0 + 1}])
        got4 = repoll("() => ((document.getElementById('note') || {}).textContent || '') === '! この操作は Mac で許可してください — E2E許可114'")
        page.wait_for_timeout(300)
        t4 = page.evaluate("() => ({ note: (document.getElementById('note') || {}).textContent || '' })")
        dg4 = [d for d, raw in diags(d0, "approve") if (d.get("err") or {}).get("k") == "tap:review"
               and "dangerous" in str((d.get("err") or {}).get("m", "")) and rv4["askId"] not in raw and perm["session"] not in raw]
        if ok4 and gets4 == 0 and got4 and len(dg4) == 1:
            print("  ✓ R114-P→R118 ④ タップの拒否（dangerous）は 8 行の「この操作は Mac で許可してください — 名前」・/diag に 1 行（kind:approve・k=tap:review・"
                  "askId と session は載せない）")
        else:
            bad(f"④ タップの拒否の報告が違う: 封筒={why4} 窓={got4} {t4} /diag={diag[d0:]}")

        # ② OFF（caps も 0）: 理由と「設定で ON にする」・押せない・JS から押してもタップを送らない（/diag に off）・カードの理由も OFF
        office(on=0, until=0)
        repoll("() => !apvOnNow() && !apvView().caps")
        page.evaluate("() => { DIAG_AT = {}; APV_SENT_LOG = {}; APV_SENT_STAGE = {}; }")
        d0 = len(diag)
        open_page("review", {"command": "bash verify.sh"}, {"reasons": ["script"], "tags": ["script"]})
        a2 = page.evaluate(look_js)
        page.wait_for_timeout(1300)
        d2 = page.evaluate("() => document.getElementById('apv_allow').disabled")
        n0, g0 = len(sent), page.evaluate("() => window.__gets")
        page.evaluate("() => { const b = document.getElementById('apv_allow'); b.disabled = false; APV.readyAt = 0; apvAllow(); }")
        page.wait_for_timeout(800)
        leak2 = [x for x in sent[n0:] if str(x.get("session", "")).startswith("apv-")]
        g2 = page.evaluate("() => window.__gets") - g0
        dg2 = [d for d, _raw in diags(d0, "approve") if (d.get("err") or {}).get("m") == "off"]
        # ON の期限が手元で切れた（Mac の caps は残っている）＝同じく need-on・カードの［ON にして確認］でその場の ON（rst- の封筒だけ・R118）
        office(on=1, until=int(time.time()) - 5, caps=1)
        repoll("() => !!apvView().caps && !apvOnNow()")
        open_page("remote", {"command": "npm test"})
        a2x = page.evaluate(look_js)
        nr0 = len([x for x in sent if str(x.get("session", "")).startswith("rst-")])
        g20 = page.evaluate("() => window.__gets")
        page.click("#apv_fix")
        end = time.time() + 6
        while time.time() < end and len([x for x in sent if str(x.get("session", "")).startswith("rst-")]) <= nr0:
            page.wait_for_timeout(100)
        rst2 = [x for x in sent if str(x.get("session", "")).startswith("rst-")][nr0:]
        on_inner = json.loads((rst2[0] if rst2 else {}).get("text") or "{}")
        go2 = page.evaluate("() => ({ view: VIEW, gets: window.__gets })")
        if (a2["state"] == "need-on" and a2["hidden"] and a2["why"] == "ON が切れています" and a2["fix"] == "ON にして確認" and d2
                and not leak2 and g2 == 0 and len(dg2) == 1
                and a2x["state"] == "need-on" and a2x["fix"] == "ON にして確認" and a2x["hidden"]
                and len(rst2) == 1 and list(on_inner) == ["aioffice", "kind", "reqId", "on"] and on_inner.get("on") is True
                and go2["view"] == "office" and go2["gets"] - g20 == 0):
            print("  ✓ R116-S1→R118 ② OFF はカードに「ON が切れています」＋［ON にして確認］（［許可］は出さない）・JS から押してもタップもパスキーも無し（/diag に off）・"
                  "手元で期限が切れたときも同じで、押すとその場で ON の封筒（パスキーなし・期限なし）＝設定へ飛ばない")
        else:
            bad(f"② OFF の扱いが違う: 画面={a2} 1.3 秒後 disabled={d2} 漏れ={len(leak2)} パスキー={g2} /diag={diag[d0:]} 期限切れ={a2x} "
                f"ON 封筒={len(rst2)} {sorted(on_inner)} 移動={go2}")

        # ⑤ R118: 許可鍵の登録の導線は無い（カードは封書を取りに行く・設定のボタンは［OFF にする］の 1 つ）・再ペアリングの説明は「通常は不要」で
        # 許可鍵に触れない・再ペアリングへ飛ばさない。この検査の間、パスキーは 1 回も呼ばない
        office(on=1)
        repoll("() => apvOnNow()")
        r5 = page.evaluate("""(e) => { closeApprove(true); setView('office', true); apvBusyEnd(); APV_RST = false; APV_RST_REQ = null;
            window.__rep = 0; const href = location.href; window.repair = () => { window.__rep++; };
            openApprove(e); const st = apvState(e), fix = document.getElementById('apv_fix');
            const label = fix && !fix.hidden ? fix.textContent : '';
            setView('settings'); apvEnrollState(); const go = document.getElementById('st_apv_go');
            const r = { st, label, act: go && go.getAttribute('data-act'), go: go ? go.textContent : '', rep: window.__rep, same: location.href === href,
                        rp: document.getElementById('st_rp_hint').textContent, buttons: [...document.querySelectorAll('#st_apv_main button')].map(b => b.id),
                        gets: window.__gets };
            setView('office', true); return r; }""", perm)
        if (r5["st"] == "loading" and not r5["label"] and r5["act"] == "off" and r5["go"] == "OFF にする" and r5["rep"] == 0 and r5["same"]
                and r5["buttons"] == ["st_apv_go"] and r5["gets"] == 0
                and r5["rp"] == "通常は不要です。スマホを替えたときだけ使います（Mac の QR からペアリングをやり直します）"):
            print("  ✓ R118 ⑤ 許可鍵の登録の導線は無い（カードは封書を取りに行く・設定のボタンは［OFF にする］の 1 つ）・再ペアリングの説明は"
                  "「通常は不要」で許可鍵に触れない・この検査の間パスキーは 0 回")
        else:
            bad(f"⑤ 登録の導線／再ペアリングの説明が違う: {r5}")
        if errors:
            bad(f"page error: {errors[:3]}")
        ctx.close()
    except Exception as exc:  # noqa: BLE001  操作の待ちが切れた等＝赤として数える（後ろのピンを止めない）
        bad(f"途中で例外: {type(exc).__name__}: {str(exc)[:200]}")
    finally:
        browser.close()
    return len(fails)


def _r116_pins(p, base_url, device_id, secret, token):
    """R116-S1 決裁カード（本人 2026-10-09「bash の許可を与えるのもごちゃごちゃしすぎている」「回りくどい」・計画 ~/.claude/plans/r116-s1-card.md）。
    状態は page.route の /status で固定し WebSocket は止める（R114 と同じ型）。署名・apvAllow・隠した pre・1.2 秒・5 秒は変えない（押すのは本物の #apv_allow）。
      ① 通知の深リンク（#attn=・開いている PWA への postMessage）: 許可待ちはその件のカード（ホーム）に焦点・シートを挟まない・#apv_allow は DOM に 1 個。
         質問はシート。OFF の許可待ちもカード（need-on・［ON にして確認］）
      ② 許可を中継が預かったら［閉じる］無しでカードがその場で次の件へ・替わった直後（1.2 秒）の二度押しで次の件は許可されない・
         Mac の granted で状態の行に「✓ 許可 — 名前」・次が無ければカードはしまう・あなた待ちが空なら INBOX ZERO
      ②b 見ているカードは新着（上位の質問）で動かない・②c 中継の後で断られた（rate）件は 8 行の 1 行で知らせ、カードに戻る
      ①b この端末から拒否を送った件の次の要求（同じ人・新しい印）の通知はその要求のカード・質問の通知はシートを前に
      ⑤ 全部の面（ホーム・タブ・端末・シート）で同じ DOM・data-state は apvState と同じ・OFF にした status 1 回で全部の面が need-on
      ③ 許可待ちのシート・端末の送信口に拒否と書いていない口が 0（見えるボタンを全部押して send() を数える）・③b 開いた後で替わった相手で組み直す
    WebAuthn の API（PublicKeyCredential）は安全な文脈でだけ出る＝localhost で開く。"""
    import secrets as _s
    from urllib.parse import urlsplit
    fails = []

    def bad(msg):
        fails.append(msg)
        print(f"  ✗ R116-S0 {msg}")

    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "server"))
    try:
        import approval_policy as ap  # noqa: E402
    except Exception as exc:  # noqa: BLE001
        print(f"  ✗ R116-S0 検証側の部品が読めない: {exc}")
        return 1
    u = urlsplit(base_url)
    lbase = f"{u.scheme}://localhost:{u.port}"
    now0 = int(time.time())
    cred = json.dumps({"d": device_id, "s": secret, "t": token, "e": 0}, separators=(",", ":"))
    key = json.dumps({"fp": "r116fp", "credId": "AQID", "d": device_id}, separators=(",", ":"))

    def perm(tag, name):
        return {"session": f"e2e-r116-{tag}", "projectId": f"e2e-r116-{tag}", "disp": name, "state": "waiting", "listening": True,
                "verb": "実行中", "approvalMin": 1, "ask": {"kind": "permission", "tool": "Bash", "remote": 1, "id8": ("116" + tag * 5)[:8]}}
    pa, pb = perm("a", "E2E許可116A"), perm("b", "E2E許可116B")
    qu = {"session": "e2e-r116-q", "projectId": "e2e-r116-q", "disp": "E2E質問116", "state": "waiting", "listening": True,
          "verb": "質問中", "question": "どちらで進めますか?", "questionOptions": [{"label": "案A"}, {"label": "案B"}],
          "ask": {"kind": "question", "tool": "AskUserQuestion", "id8": "116c0de0"}}
    idle = {"session": "e2e-r116-i", "projectId": "e2e-r116-i", "disp": "E2E指示待ち116", "state": "waiting", "listening": True,
            "verb": "指示待ち", "kind": "said", "age": 30}
    tpls = [{"label": "定型A", "text": "定型の指示A"}, {"label": "報告して", "text": "いまの状況を報告して"}]
    browser = p.chromium.launch()
    sent, serve = [], {"status": None}

    def office(emps, on=1, results=(), templates=()):
        approve = {"on": on, "until": now0 + 3600 if on else 0, "fps": ["r116fp"], "enr": []}
        o = {"officeName": "E2E R116", "employees": list(emps), "templates": list(templates),
             "actions": {"caps": {"approve": 1 if on else 0}, "approve": approve, "results": list(results)}}
        serve["status"] = json.dumps({"ok": True, "ts": int(time.time() * 1000), "agentSeenAgo": 0, "agentOnline": True,
                                      "json": json.dumps(o, ensure_ascii=False)}, ensure_ascii=False)

    def on_instruct(route, request):
        if request.method != "POST":
            route.continue_()
            return
        try:
            sent.append(json.loads(request.post_data or "{}"))
        except ValueError:
            sent.append({})
        route.fulfill(status=200, content_type="application/json", body='{"ok":true}')

    def on_status(route, request):
        if request.method == "GET" and serve["status"] is not None:
            route.fulfill(status=200, content_type="application/json", body=serve["status"])
        else:
            route.continue_()

    def wait(cond, arg=None, ms=8000):
        try:
            page.wait_for_function(cond, arg=arg, timeout=ms)
            return True
        except PWTimeout:
            return False

    def repoll(cond, arg=None, ms=8000):
        page.evaluate("() => poll()")
        return wait(cond, arg, ms)

    def open_page(e, inp):
        """Mac が封じた全文（掲示 1 件・remote）を受け取った状態の許可の画面（封書の往復は _r100_apv_pins が実物で見る）。"""
        canon = ap.input_canon("Bash", inp)
        pg = {"kind": "ask", "session": e["session"], "askId": _s.token_hex(16), "tool": "Bash", "inputCanon": canon,
              "inputSha": ap.input_sha(canon), "inputBytes": len(canon.encode()), "cwdBase": "proj", "askTs": now0, "level": "remote",
              "reasons": [], "tags": [], "macOnly": 0, "agentId": "", "askCount": 1, "iat": int(time.time())}
        page.evaluate("""([e, pg]) => { closeSheet(); closeApprove(true); setView('office', true);
            openApprove(e); APV.page = pg; APV.at = Date.now(); APV.sig = ''; apvRender(); }""", [e, pg])
        return pg

    def press(e):
        """1.2 秒待って本物の #apv_allow を押す → (タップの封筒か, 理由, reqId)。"""
        pg = open_page(e, {"command": "npm test"})
        page.wait_for_timeout(1300)
        n0 = len(sent)
        page.click("#apv_allow")
        end = time.time() + 6
        while time.time() < end and not any(str(x.get("session", "")).startswith("apv-") for x in sent[n0:]):
            page.wait_for_timeout(100)
        got = [x for x in sent[n0:] if str(x.get("session", "")).startswith("apv-")]
        ok, why, inner = _tap_env(got[0] if len(got) == 1 else None, device_id, secret, pg["askId"], pg["inputSha"])
        return ok, why, inner.get("reqId", "")

    def grant(emps, rid):
        """Mac がその reqId を受け付けた（results の granted）status を返す。"""
        office(emps, results=[{"reqId": rid, "kind": "approve", "state": "granted", "output": "", "startedAt": now0}])
        page.evaluate("() => poll()")

    def press_granted(e, emps):
        ok, why, rid = press(e)
        grant(emps, rid)
        return ok, why

    land_js = """() => ({ apv: document.getElementById('apvwrap').classList.contains('open'), apvSess: APV ? APV.session : null,
        sheet: document.getElementById('sheetwrap').classList.contains('open'), sel: SEL ? SEL.session : null, hash: location.hash })"""
    apv_is = "(s) => document.getElementById('apvwrap').classList.contains('open') && !!APV && APV.session === s"
    sheet_is = "(s) => document.getElementById('sheetwrap').classList.contains('open') && !!SEL && SEL.session === s"
    line_js = "() => (document.querySelector('#ticker .tk-login') || {}).textContent || ''"
    try:
        ctx = browser.new_context(viewport={"width": 390, "height": 844}, has_touch=True, is_mobile=True, service_workers="block")
        page = ctx.new_page()
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.on("dialog", lambda d: d.accept())
        page.add_init_script(
            "localStorage.setItem('aioffice.cred', " + json.dumps(cred) + ");"
            "localStorage.setItem('aioffice.apv', " + json.dumps(key) + ");"
            "window.WebSocket = function () { throw new Error('r116: WS off'); };"
            "window.__gets = 0;"
            "if (navigator.credentials) navigator.credentials.get = function () { window.__gets++;"
            "  return Promise.reject(new Error('r116: passkey は呼ばない')); };")
        page.route("**/instruct", on_instruct)
        page.route("**/status", on_status)
        page.route("**/diag", lambda route: route.fulfill(status=204, body=""))

        def closed_now():
            return wait("() => !document.getElementById('apvwrap').classList.contains('open')", ms=6000)

        def stays(ms=3600):
            page.wait_for_timeout(ms)
            return page.evaluate(land_js)

        def status_of(emps, **kw):
            office(emps, **kw)
            return json.loads(serve["status"])

        uncatch = "() => { const c = document.getElementById('tapcatch'); if (c) c.hidden = true; }"
        # シートは 120ms で下から出る（transform）＝当たり判定はシートが止まってから
        sheet_still = ("() => { const s = document.querySelector('#sheetwrap .sheet'), r = s && s.getBoundingClientRect();"
                       " return !!r && document.getElementById('sheetwrap').classList.contains('open') && r.top >= 0 && r.bottom <= innerHeight + 1; }")

        card_js = """() => { const c = document.getElementById('apvwrap');
          return { open: c.classList.contains('open'), host: c.parentNode ? c.parentNode.id : '', state: c.getAttribute('data-state'), sess: APV ? APV.session : null,
                   allowN: document.querySelectorAll('#apv_allow').length, sheet: document.getElementById('sheetwrap').classList.contains('open'),
                   hash: location.hash, fix: (() => { const f = document.getElementById('apv_fix'); return f && !f.hidden ? f.textContent : ''; })() }; }"""
        line_is = "(w) => ((document.querySelector('#ticker .tk-login') || {}).textContent || '') === w"

        # ① 通知の深リンク: 初回の起動（#attn=）で許可待ちはその件のカード（ホーム）
        office([pa, qu])
        page.goto(lbase + "/app/#attn=" + pa["session"], wait_until="domcontentloaded")
        land1 = wait(apv_is, pa["session"], 20000)
        l1 = page.evaluate(card_js)
        page.evaluate("() => { closeSheet(); readAttnLink('e2e-r116-q'); poll(); }")
        land2 = wait(sheet_is, qu["session"])
        page.evaluate("() => { closeSheet(); readAttnLink('e2e-r116-a'); poll(); }")
        land3 = wait(apv_is, pa["session"])
        l3 = page.evaluate(card_js)
        office([pa, qu], on=0)
        repoll("() => !apvView().caps")
        page.evaluate("() => { closeApprove(true); closeSheet(); readAttnLink('e2e-r116-a'); poll(); }")
        land4 = wait("(s) => !!APV && APV.session === s && document.getElementById('apvwrap').getAttribute('data-state') === 'need-on'", pa["session"])
        l4 = page.evaluate(card_js)
        if (land1 and l1["host"] == "apvslot_home" and not l1["sheet"] and not l1["hash"] and l1["allowN"] == 1 and land2
                and land3 and not l3["sheet"] and l3["host"] == "apvslot_home" and land4 and not l4["sheet"] and l4["fix"] == "ON にして確認"):
            print("  ✓ R116-S1 ① 許可待ちの通知（#attn=・開いている PWA への postMessage）はシートを挟まずその件のカード（ホーム）・#apv_allow は 1 個・"
                  "質問はシート・OFF の許可待ちもカード（［ON にして確認］）")
        else:
            bad(f"① 通知の着地が違う: 起動={land1}{l1} 質問={land2} 開いている PWA={land3}{l3} OFF={land4}{l4}")

        # ② 許可を中継が預かったら、カードはその場で次の件（B）へ・替わった直後の二度押しで B は許可されない・granted で状態の行
        office([pa, pb])
        repoll("() => apvOnNow() && officeAgents(LAST_OFFICE).length === 2")
        ok2, why2, rid2 = press(pa)
        moved = wait(apv_is, pb["session"], 4000)
        canon_b = ap.input_canon("Bash", {"command": "npm run lint"})
        pg_b = {"kind": "ask", "session": pb["session"], "askId": _s.token_hex(16), "tool": "Bash", "inputCanon": canon_b,
                "inputSha": ap.input_sha(canon_b), "inputBytes": len(canon_b.encode()), "cwdBase": "proj", "askTs": now0, "level": "remote",
                "reasons": [], "tags": [], "macOnly": 0, "agentId": "", "askCount": 1, "iat": int(time.time())}
        n_b = len(sent)
        xy = page.evaluate("""(pg) => { APV.page = pg; APV.at = Date.now(); APV.sig = ''; apvRender();
            const r = document.getElementById('apv_allow').getBoundingClientRect(); return [r.left + r.width / 2, r.top + r.height / 2]; }""", pg_b)
        page.touchscreen.tap(xy[0], xy[1])
        page.wait_for_timeout(500)
        double = [x for x in sent[n_b:] if str(x.get("session", "")).startswith("apv-")]
        grant([pa, pb], rid2)
        line2 = wait(line_is, "✓ 許可 — E2E許可116A", 6000)
        page.wait_for_timeout(900)
        n_b2 = len(sent)
        page.touchscreen.tap(xy[0], xy[1])
        end = time.time() + 6
        while time.time() < end and not any(str(x.get("session", "")).startswith("apv-") for x in sent[n_b2:]):
            page.wait_for_timeout(100)
        sent_b = [x for x in sent[n_b2:] if str(x.get("session", "")).startswith("apv-")]
        ok_b, why_b, _ = _tap_env(sent_b[0] if len(sent_b) == 1 else None, device_id, secret, pg_b["askId"], pg_b["inputSha"])
        parked = wait("() => !document.getElementById('apvwrap').classList.contains('open') && !APV", ms=4000)
        office([idle])
        zero = repoll("() => !document.getElementById('inboxzero').hidden")
        if (ok2 and moved and not double and line2 and ok_b and parked and zero and page.evaluate("() => window.__gets") == 0):
            print("  ✓ R116-S1 ② 中継が預かったら［閉じる］無しでカードがその場で B へ・替わった直後の二度押し（本物のタップ）で B は許可されない・"
                  "granted で状態の行に「✓ 許可 — E2E許可116A」・B も 1 タップ・次が無ければカードはしまう・空なら INBOX ZERO")
        else:
            bad(f"② 次へ進む・二度押しが違う: A={why2} 次へ={moved} 二度押しで送った={len(double)} 状態の行={line2} B={why_b}/{len(sent_b)} "
                f"しまう={parked} INBOX ZERO={zero}")

        # ②b 見ているカードは新着で動かない（上位に並ぶ質問が来ても A のまま）・②c 中継の後で断られた件はカードに戻る
        office([pa])
        repoll("() => officeAgents(LAST_OFFICE).length === 1")
        open_page(pa, {"command": "npm test"})
        hi = dict(qu, approvalMin=30)
        office([hi, pa])
        repoll("() => officeAgents(LAST_OFFICE).length === 2")
        page.wait_for_timeout(300)
        stay = page.evaluate("() => ({ sess: APV ? APV.session : null, first: officeNeed(officeAgents(LAST_OFFICE))[0].session })")
        office([pa])
        repoll("() => officeAgents(LAST_OFFICE).length === 1")
        ok_r, why_r, rid_r = press(pa)
        parked_r = wait("() => !document.getElementById('apvwrap').classList.contains('open')", ms=4000)
        office([pa], results=[{"reqId": rid_r, "kind": "approve", "state": "refused", "output": "rate", "startedAt": now0}])
        back = repoll("(s) => !!APV && APV.session === s && document.getElementById('apvwrap').classList.contains('open')", pa["session"])
        note_r = page.evaluate("() => (document.getElementById('note') || {}).textContent || ''")
        if (stay["sess"] == pa["session"] and stay["first"] == hi["session"] and ok_r and parked_r and back
                and note_r == "! 少し待ってからもう一度 — E2E許可116A"):
            print("  ✓ R116-S1 ②b 見ているカードは上位の新着（質問）が来ても動かない・②c 中継の後で断られた（rate）件は「少し待ってからもう一度 — 名前」でカードに戻る")
        else:
            bad(f"②b/②c が違う: 新着={stay} 押した={why_r} しまった={parked_r} 戻った={back} 1 行={note_r!r}")

        # ①b この端末から拒否を送った件の次の要求（同じ人・新しい印）の通知はその要求のカード・質問の通知はシートを前に
        a2 = dict(pa, ask=dict(pa["ask"], id8="116a2222"))
        a3 = dict(pa, ask=dict(pa["ask"], id8="116a3333"))
        office([pa, qu])
        repoll("() => officeAgents(LAST_OFFICE).length === 2 && apvOnNow()")
        open_page(pa, {"command": "npm test"})
        n0 = len(sent)
        page.click("#apvdeny")
        denied = wait("() => !APV || APV.session !== 'e2e-r116-a'", ms=6000)
        deny_sent = [x for x in sent[n0:] if x.get("session") == pa["session"]]
        office([a2, qu])
        page.evaluate("() => { readAttnLink('e2e-r116-a'); poll(); }")
        new2 = wait("() => !!APV && APV.session === 'e2e-r116-a' && APV.e.ask.id8 === '116a2222' && !APV.denied"
                    " && document.getElementById('apvwrap').classList.contains('open')")
        office([a3, qu])
        page.evaluate("() => { readAttnLink('e2e-r116-a'); poll(); }")
        new3 = wait("() => !!APV && APV.e.ask.id8 === '116a3333'")
        page.evaluate("() => { readAttnLink('e2e-r116-q'); poll(); }")
        qland = wait(sheet_is, qu["session"]) and wait("""() => { const s = document.querySelector('#sheetwrap .sheet'), r = s && s.getBoundingClientRect();
            return !!r && r.top >= 0 && r.bottom <= innerHeight + 1; }""")
        qtop = page.evaluate("""() => { const s = document.querySelector('#sheetwrap .sheet'), r = s.getBoundingClientRect();
            const h = document.elementFromPoint(r.left + r.width / 2, r.top + Math.min(r.height / 2, 120)); return { top: !!(h && s.contains(h)) }; }""")
        page.evaluate("() => closeSheet()")
        if denied and len(deny_sent) == 1 and new2 and new3 and qland and qtop["top"]:
            print("  ✓ R116-S1 ①b 拒否を送った件の次の要求（新しい印）の通知はその要求のカード・その次も同じ・質問の通知はシートを前に")
        else:
            bad(f"①b 通知の着地が違う: 拒否={denied}/{len(deny_sent)} 次の要求={new2} その次={new3} 質問={qland}{qtop}")

        # ⑤ 全部の面で同じ DOM（#apv_allow は 1 個）・data-state は apvState と同じ・OFF の status 1 回で need-on
        office([pa, qu])
        repoll("() => officeAgents(LAST_OFFICE).length === 2 && apvOnNow()")
        surf_js = """async (step) => { const e = () => sessionAgent('e2e-r116-a');
          closeApprove(true); closeSheet(); if (TERM) closeTerm(true, true); setView('office', true);
          if (step === 'home') openApprove(e());
          if (step === 'tab') { setView('wait', true); openApprove(e()); }
          if (step === 'term') openTerm(e());
          if (step === 'sheet') openSheet(e());
          await new Promise(r => setTimeout(r, 150));
          const c = document.getElementById('apvwrap');
          return { host: c.parentNode ? c.parentNode.id : '', state: c.getAttribute('data-state'), want: apvState(e()),
                   n: document.querySelectorAll('#apv_allow').length, open: c.classList.contains('open') }; }"""
        surf = {}
        for step in ("home", "tab", "term", "sheet"):
            office([pa, qu])
            repoll("() => apvOnNow()")
            on_ = page.evaluate(surf_js, step)
            office([pa, qu], on=0)
            off_ = repoll("() => document.getElementById('apvwrap').getAttribute('data-state') === 'need-on' && !apvOnNow()")
            surf[step] = (on_, off_)
        page.evaluate("() => { closeSheet(); if (TERM) closeTerm(true, true); setView('office', true); }")
        want_host = {"home": "apvslot_home", "tab": "apvslot_wait", "term": "apvslot_term", "sheet": "apvslot_sheet"}
        if all(v[0]["host"] == want_host[k] and v[0]["open"] and v[0]["n"] == 1 and v[0]["state"] == v[0]["want"] == "loading" and v[1]
               for k, v in surf.items()):
            print("  ✓ R116-S1 ⑤ ホーム・タブ・端末・シートのどこでも同じカード（#apv_allow は 1 個）・data-state は apvState と同じ・OFF の status 1 回で need-on")
        else:
            bad(f"⑤ 面ごとの状態が違う: {surf}")

        # ③（レビュー）横取りした先読みが捨てられたら、カードは「読み込み中」で止まらず取り直す
        office([pa])
        repoll("() => officeAgents(LAST_OFFICE).length === 1 && apvOnNow()")
        stuck = page.evaluate("""() => { closeApprove(true); APV_PREFETCH = {}; setView('office', true); apvPrefetch();
            const k = apvPrefetchKey(sessionAgent('e2e-r116-a')), rec = APV_PREFETCH[k];
            openApprove(sessionAgent('e2e-r116-a')); const adopted = !!APV && APV.prefetch === rec && APV.req === (rec && rec.req);
            if (rec) apvPrefetchFail(rec);
            const after = { req: APV && APV.req, inflight: APV && APV.inflight, retry: !!(APV && APV.retryAt > Date.now()) };
            APV.retryAt = 0; const n = performance.now(); apvSync();
            return { adopted, after, again: !!(APV && APV.req && APV.inflight), state: document.getElementById('apvwrap').getAttribute('data-state') }; }""")
        if stuck["adopted"] and stuck["after"]["req"] is None and stuck["after"]["inflight"] is None and stuck["after"]["retry"] and stuck["again"]:
            print("  ✓ R116-S1（レビュー）横取りした先読みが捨てられたらカードの要求も外れ、15 秒後に取り直す（読み込み中で止まらない）")
        else:
            bad(f"③（レビュー）横取りした先読みの失敗でカードが止まる: {stuck}")
        # ④（レビュー）/instruct の応答より先に status で「断られた」が届いても、その件を隠さず（2 分の印を付けず）カードに残す
        office([pa])
        repoll("() => officeAgents(LAST_OFFICE).length === 1 && apvOnNow()")
        pg_e = open_page(pa, {"command": "npm test"})
        page.wait_for_timeout(1300)
        page.evaluate("""() => { window.__f0 = window.fetch; window.fetch = (u, init) => {
            if (String(u).endsWith('/instruct') && init && String(init.body || '').indexOf('"apv-') >= 0) { window.__apvBody = String(init.body);
              return new Promise(res => { window.__release = () => res(new Response('{"ok":true}', { headers: { 'Content-Type': 'application/json' } })); }); }
            return window.__f0(u, init); }; }""")
        page.click("#apv_allow")
        page.wait_for_function("() => !!window.__apvBody", timeout=5000)
        rid_e = page.evaluate("() => (JSON.parse(window.__apvBody).session || '').slice(4)")
        early = page.evaluate("""([d, sig]) => { applyStatus(d); window.__release(); return sig; }""",
                              [status_of([pa], results=[{"reqId": rid_e, "kind": "approve", "state": "refused", "output": "rate", "startedAt": now0}]), ""])
        page.wait_for_timeout(600)
        e_after = page.evaluate("""() => { window.fetch = window.__f0; const e = sessionAgent('e2e-r116-a');
            return { sess: APV ? APV.session : null, done: !!APV_DONE[attnSig(e)], sent: !!(APV && APV.sent),
                     note: (document.getElementById('note') || {}).textContent || '' }; }""")
        if e_after["sess"] == pa["session"] and not e_after["done"] and not e_after["sent"] and "少し待ってからもう一度" in e_after["note"]:
            print("  ✓ R116-S1（レビュー）応答より先に「断られた」が届いても、その件はカードに残る（2 分の印を付けない・押し直せる）")
        else:
            bad(f"④（レビュー）先に届いた拒否の後で件を隠した: {e_after}")
        # ⑤（レビュー）端末の中で［全文］を開いても［許可］［拒否］は画面の中で押せる（長い命令）
        office([pa])
        repoll("() => officeAgents(LAST_OFFICE).length === 1")
        long_cmd = "\n".join("echo line %d" % i for i in range(60))
        canon_l = ap.input_canon("Bash", {"command": long_cmd})
        pg_l = {"kind": "ask", "session": pa["session"], "askId": _s.token_hex(16), "tool": "Bash", "inputCanon": canon_l,
                "inputSha": ap.input_sha(canon_l), "inputBytes": len(canon_l.encode()), "cwdBase": "proj", "askTs": now0, "level": "remote",
                "reasons": [], "tags": [], "macOnly": 0, "agentId": "", "askCount": 1, "iat": int(time.time())}
        tfull = page.evaluate("""(pg) => { closeApprove(true); setView('office', true); openTerm(sessionAgent('e2e-r116-a'));
            APV.page = pg; APV.at = Date.now(); APV.sig = ''; apvRender(); return !!document.getElementById('apvfullbtn'); }""", pg_l)
        page.wait_for_timeout(300)
        if tfull:
            page.click("#apvfullbtn")
        page.wait_for_timeout(1300)
        reach = page.evaluate("""() => ['apv_allow', 'apvdeny'].map(id => { const b = document.getElementById(id), r = b.getBoundingClientRect(),
            h = document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2);
            return r.bottom <= innerHeight + 1 && r.top >= 0 && !!h && (h === b || b.contains(h)); })""")
        lay = page.evaluate("""() => { const f = document.getElementById('termfoot'), b = document.getElementById('termbody'),
            w = document.getElementById('termwrap').getBoundingClientRect(), s = document.getElementById('apvslot_term').getBoundingClientRect();
          return { foot: getComputedStyle(f).display, body: Math.round(b.getBoundingClientRect().height), want: Math.floor(Math.min(96, innerHeight * 0.18)) - 1,
                   inside: s.bottom <= w.bottom + 1 }; }""")
        page.evaluate("() => { if (TERM) closeTerm(true, true); }")
        if tfull and all(reach) and lay["foot"] == "none" and lay["body"] >= lay["want"] and lay["inside"]:
            print(f"  ✓ R116-S1（レビュー）端末の中で 60 行の命令の［全文］を開いても［許可］［拒否］は画面の中で押せる・"
                  f"カードの間は返信欄を畳み本文 {lay['body']}px を残す・引き出しの下端を越えない")
        else:
            bad(f"⑤（レビュー）端末の中で全文を開くとボタンが画面の外／本文が潰れる: 全文ボタン={tfull} 押せる={reach} 形={lay}")

        # ⑥（コミット後のレビュー）拒否の書きかけ: 同じ人の要求が替わっても書きかけを残して「相手の要求が変わりました」・別の人のカードへは持ち込まない
        pc = perm("c", "E2E許可116C")                                   # B は前の検査で答えた印（2 分）つき＝カードに出ない
        office([pa, pc])
        repoll("() => officeAgents(LAST_OFFICE).length === 2")
        d1 = page.evaluate("""() => { closeSheet(); closeApprove(true); if (TERM) closeTerm(true, true); setView('office', true);
            openApprove(sessionAgent('e2e-r116-a')); approveDenyWrite(); document.getElementById('apvdenytext').value = '別の方法でお願いします';
            return { sess: APV && APV.session, open: !document.getElementById('apvdenybox').hidden }; }""")
        office([dict(pa, ask=dict(pa["ask"], id8="116aaaa2")), pc])
        repoll("() => { const e = sessionAgent('e2e-r116-a'); return !!e && e.ask.id8 === '116aaaa2' && !!APV && APV.e.ask.id8 === '116aaaa2'; }")
        d2 = page.evaluate("""() => ({ sess: APV && APV.session, id8: APV && APV.e.ask.id8, open: !document.getElementById('apvdenybox').hidden,
            text: document.getElementById('apvdenytext').value, note: document.getElementById('apvnote').textContent })""")
        office([pc])
        repoll("() => officeAgents(LAST_OFFICE).length === 1 && !!APV && APV.session === 'e2e-r116-c'")
        d3 = page.evaluate("""() => ({ sess: APV && APV.session, open: !document.getElementById('apvdenybox').hidden,
            text: document.getElementById('apvdenytext').value })""")
        if (d1["sess"] == pa["session"] and d1["open"] and d2["sess"] == pa["session"] and d2["id8"] == "116aaaa2" and d2["open"]
                and d2["text"] == "別の方法でお願いします" and d2["note"].startswith("相手の要求が変わりました")
                and d3["sess"] == pc["session"] and not d3["open"] and d3["text"] == ""):
            print("  ✓ R116-S1（コミット後のレビュー）拒否の書きかけは同じ人の次の要求のカードに残る（「相手の要求が変わりました」）・別の人のカードへは持ち込まない")
        else:
            bad(f"⑥ 拒否の書きかけ: 開いた={d1} 要求が替わった後={d2} 別の人={d3}")

        # R122-P2 ⑦: 横向きも全面の会話。相手を切り替えても同じ人の許可カードになり、
        # 60行の命令の［全文］を開いても［許可］［拒否］に届く。
        office([pa, pc])
        repoll("() => officeAgents(LAST_OFFICE).length === 2")
        page.set_viewport_size({"width": 844, "height": 390})
        page.wait_for_timeout(600)
        sp = page.evaluate("""() => { closeSheet(); closeApprove(true); if (TERM) closeTerm(true, true); setView('office', true);
            openTerm(sessionAgent('e2e-r116-a'), true);
            const rows = [...document.querySelectorAll('#splitwaitlist .splitwaititem')].map(n => n.getAttribute('data-sess'));
            return { split: splitOffice(), rail: !!document.querySelector('#splitwait #apvwrap'), rows,
                     card: APV && APV.session, host: APV_HOST }; }""")
        page.evaluate("() => openTerm(sessionAgent('e2e-r116-c'))")
        page.wait_for_timeout(500)
        canon_c = ap.input_canon("Bash", {"command": long_cmd})
        pg_c = dict(pg_l, session=pc["session"], askId=_s.token_hex(16), inputCanon=canon_c, inputSha=ap.input_sha(canon_c))
        sw = page.evaluate("""(pg) => { const r = { term: TERM && TERM.session, card: APV && APV.session, host: APV_HOST,
              rail: !!document.querySelector('#splitwait #apvwrap') };
            if (APV && APV.session === pg.session) { APV.page = pg; APV.at = Date.now(); APV.sig = ''; apvRender(); }
            r.full = !!document.getElementById('apvfullbtn'); return r; }""", pg_c)
        page.wait_for_timeout(300)
        if sw["full"]:
            page.click("#apvfullbtn")
        page.wait_for_timeout(1300)
        reach2 = page.evaluate("""() => ['apv_allow', 'apvdeny'].map(id => { const b = document.getElementById(id), r = b.getBoundingClientRect(),
            h = document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2);
            return r.bottom <= innerHeight + 1 && r.top >= 0 && !!h && (h === b || b.contains(h)); })""")
        page.evaluate("() => { if (TERM) closeTerm(true, true); }")
        page.set_viewport_size({"width": 390, "height": 844})
        page.wait_for_timeout(400)
        page.evaluate("() => setView('office', true)")
        if (not sp["split"] and not sp["rail"] and not sp["rows"] and sp["card"] == pa["session"] and sp["host"] == "term"
                and sw["term"] == pc["session"] and sw["card"] == pc["session"] and sw["host"] == "term" and not sw["rail"] and sw["full"]
                and all(reach2)):
            print("  ✓ R122-P2 横向き844×390も全面の会話・相手を替えると本人の許可カード・［全文］を開いても［許可］［拒否］に届く")
        else:
            bad(f"⑦ 横向きの全面会話のカード: 最初={sp} 行を押した後={sw} 押せる={reach2}")

        # ⑨（2 巡目のレビュー）横向き（844×390）でセッション一覧から通知で来ても（先に見ていたのは別の人の質問）、その許可の人の端末とカード
        pd, pe, pf = perm("d", "E2E許可116D"), perm("e", "E2E許可116E"), perm("f", "E2E許可116F")
        office([qu, pd])
        repoll("() => officeAgents(LAST_OFFICE).length === 2 && !!sessionAgent('e2e-r116-d') && !!sessionAgent('e2e-r116-q')")
        page.set_viewport_size({"width": 844, "height": 390})
        page.wait_for_timeout(600)
        land = page.evaluate("""() => { closeSheet(); closeApprove(true); if (TERM) closeTerm(true, true); setView('list', true);
            SEL = sessionAgent('e2e-r116-q'); openApprove(sessionAgent('e2e-r116-d'), 'notif');
            const c = document.getElementById('apvwrap');
            return { view: VIEW, split: splitOffice(), term: TERM && TERM.session, card: APV && APV.session,
                     slot: c.parentNode && c.parentNode.id, open: c.classList.contains('open') }; }""")
        # ⑩（2 巡目のレビュー）横分割で決裁（拒否）したら、真ん中の端末が次の許可の人へ替わりカードが出る
        office([pd, pe])
        repoll("() => officeAgents(LAST_OFFICE).length === 2 && !!sessionAgent('e2e-r116-e')")
        page.evaluate("""() => { closeSheet(); closeApprove(true); if (TERM) closeTerm(true, true); setView('wait', true);
            openApprove(sessionAgent('e2e-r116-d')); approveDeny(); }""")
        page.wait_for_timeout(3200)
        nxt = page.evaluate("""() => { const c = document.getElementById('apvwrap');
            return { term: TERM && TERM.session, card: APV && APV.session, slot: c.parentNode && c.parentNode.id,
                     doneD: apvAnswered(sessionAgent('e2e-r116-d')) }; }""")
        page.evaluate("() => { if (TERM) closeTerm(true, true); }")
        page.set_viewport_size({"width": 390, "height": 844})
        page.wait_for_timeout(400)
        page.evaluate("() => setView('office', true)")
        if land == {"view": "wait", "split": False, "term": None, "card": pd["session"], "slot": "apvslot_wait", "open": True}:
            print("  ✓ R116-S1（2 巡目のレビュー）横向きでセッション一覧から通知で来ても、ホームにその許可の人のカード（先に見ていた質問の人にしない）")
        else:
            bad(f"⑨ 横向きの一覧から通知で来たときのカード: {land}")
        if nxt == {"term": None, "card": pe["session"], "slot": "apvslot_wait", "doneD": True}:
            print("  ✓ R116-S1（2 巡目のレビュー）ホームで決裁したら、次の許可の人のカードが出る（次の件へ）")
        else:
            bad(f"⑩ ホームの決裁の後に次の許可へ進まない: {nxt}")

        # ⑪（2・3 巡目のレビュー）拒否の文を送った後（応答の前）に別のカードへ移っても、送った文を書きかけとして控えない・答えた印は付く
        office([pf, pc])
        repoll("() => officeAgents(LAST_OFFICE).length === 2 && !!sessionAgent('e2e-r116-f') && !!sessionAgent('e2e-r116-c')")
        page.evaluate("""() => { closeSheet(); closeApprove(true); if (TERM) closeTerm(true, true); setView('office', true);
            openApprove(sessionAgent('e2e-r116-f')); approveDenyWrite(); document.getElementById('apvdenytext').value = '送った指示';
            window.__release2 = null; window.__f1 = window.fetch; window.fetch = (u, init) => {
              if (String(u).endsWith('/instruct') && String((init && init.body) || '').indexOf('"session":"e2e-r116-f"') >= 0)
                return new Promise(res => { window.__release2 = () => res(new Response('{"ok":true}', { headers: { 'Content-Type': 'application/json' } })); });
              return window.__f1(u, init); };
            approveDenySend(); }""")
        held = wait("() => !!window.__release2", ms=6000)
        moved = page.evaluate("""() => { openApprove(sessionAgent('e2e-r116-c')); const had = !!APV_DRAFT['e2e-r116-f'];
            if (window.__release2) window.__release2(); return { had, card: APV && APV.session }; }""")
        page.wait_for_timeout(600)
        after = page.evaluate("""() => { window.fetch = window.__f1; const e = sessionAgent('e2e-r116-f');
            return { draft: !!APV_DRAFT['e2e-r116-f'], done: apvAnswered(e), card: APV && APV.session }; }""")
        if held and not moved["had"] and moved["card"] == pc["session"] and not after["draft"] and after["done"] and after["card"] == pc["session"]:
            print("  ✓ R116-S1（2・3 巡目のレビュー）拒否の文を送った後に別のカードへ移っても、送った文を書きかけとして控えない・答えた印は付く")
        else:
            bad(f"⑪ 送った拒否の文が書きかけに残る: 止めた={held} 移った={moved} 応答の後={after}")

        # ⑫（3 巡目のレビュー）拒否の応答を待つ間に同じ人の次の要求で書いた新しい書きかけは、遅れて届いた古い応答で消さない
        ph = perm("ab", "E2E許可116AB")
        ph2 = dict(ph, ask=dict(ph["ask"], id8="116abab2"))
        office([ph, pc])
        repoll("() => officeAgents(LAST_OFFICE).length === 2 && !!sessionAgent('e2e-r116-ab') && !!sessionAgent('e2e-r116-c')")
        page.evaluate("""() => { closeSheet(); closeApprove(true); if (TERM) closeTerm(true, true); setView('office', true);
            openApprove(sessionAgent('e2e-r116-ab')); approveDenyWrite(); document.getElementById('apvdenytext').value = '古い要求への指示';
            window.__release3 = null; window.__f2 = window.fetch; window.fetch = (u, init) => {
              if (String(u).endsWith('/instruct') && String((init && init.body) || '').indexOf('"session":"e2e-r116-ab"') >= 0)
                return new Promise(res => { window.__release3 = () => res(new Response('{"ok":true}', { headers: { 'Content-Type': 'application/json' } })); });
              return window.__f2(u, init); };
            approveDenySend(); }""")
        held3 = wait("() => !!window.__release3", ms=6000)
        office([ph2, pc])
        repoll("() => { const e = sessionAgent('e2e-r116-ab'); return !!e && e.ask.id8 === '116abab2'; }")
        page.evaluate("""() => { openApprove(sessionAgent('e2e-r116-ab')); approveDenyWrite();
            document.getElementById('apvdenytext').value = '新しい要求への書きかけ'; openApprove(sessionAgent('e2e-r116-c')); window.__release3(); }""")
        page.wait_for_timeout(600)
        kept = page.evaluate("""() => { window.fetch = window.__f2; const d = APV_DRAFT['e2e-r116-ab'];
            openApprove(sessionAgent('e2e-r116-ab'));
            return { draft: d ? d.t : null, box: document.getElementById('apvdenytext').value, open: !document.getElementById('apvdenybox').hidden,
                     card: APV && APV.session }; }""")
        if held3 and kept == {"draft": "新しい要求への書きかけ", "box": "新しい要求への書きかけ", "open": True, "card": ph["session"]}:
            print("  ✓ R116-S1（3 巡目のレビュー）拒否の応答を待つ間に同じ人の次の要求で書いた書きかけは、遅れて届いた古い応答で消さない")
        else:
            bad(f"⑫ 古い応答が新しい書きかけを消した: 止めた={held3} {kept}")

        # ⑬（4 巡目のレビュー）自分で書いた拒否の文の送信中に離れて同じ件へ戻り、その後に届かなかったら、未編集の欄へ文を戻す（「届いていません」）・
        #    その後に離れて戻っても文は残る
        pk, pl = perm("ac", "E2E許可116AC"), perm("ad", "E2E許可116AD")
        hold_js = """([sess, key, text]) => { closeSheet(); closeApprove(true); if (TERM) closeTerm(true, true); setView('office', true);
            openApprove(sessionAgent(sess)); approveDenyWrite(); document.getElementById('apvdenytext').value = text;
            window[key] = null; window.__f4 = window.__f4 || window.fetch; window.fetch = (u, init) => {
              if (String(u).endsWith('/instruct') && String((init && init.body) || '').indexOf('"session":"' + sess + '"') >= 0)
                return new Promise(res => { window[key] = () => res(new Response('{"ok":false,"error":"down"}', { headers: { 'Content-Type': 'application/json' } })); });
              return window.__f4(u, init); };
            approveDenySend(); }"""
        office([pk, pc])
        repoll("() => officeAgents(LAST_OFFICE).length === 2 && !!sessionAgent('e2e-r116-ac') && !!sessionAgent('e2e-r116-c')")
        page.evaluate(hold_js, [pk["session"], "__release4", "届かない指示"])
        held4 = wait("() => !!window.__release4", ms=6000)
        back = page.evaluate("""() => { openApprove(sessionAgent('e2e-r116-c')); openApprove(sessionAgent('e2e-r116-ac'));
            const before = document.getElementById('apvdenytext').value; window.__release4(); return before; }""")
        page.wait_for_timeout(600)
        r13 = page.evaluate("""() => ({ box: document.getElementById('apvdenytext').value, open: !document.getElementById('apvdenybox').hidden,
            note: document.getElementById('apvnote').textContent, card: APV && APV.session })""")
        r13b = page.evaluate("""() => { openApprove(sessionAgent('e2e-r116-c')); openApprove(sessionAgent('e2e-r116-ac'));
            return document.getElementById('apvdenytext').value; }""")
        if (held4 and back == "" and r13["box"] == "届かない指示" and r13["open"] and r13["note"].startswith("Mac に届いていません")
                and r13["card"] == pk["session"] and r13b == "届かない指示"):
            print("  ✓ R116-S1（4 巡目のレビュー）送信中に離れて同じ件へ戻った後に届かなかったら、未編集の欄へ文を戻す（「Mac に届いていません」）・離れて戻っても残る")
        else:
            bad(f"⑬ 届かなかった文が戻らない: 止めた={held4} 戻った直後={back!r} 失敗の後={r13} 離れて戻った後={r13b!r}")
        # ⑭（4 巡目のレビュー）送信中に離れて同じ件へ戻り、書き直してまた離れた後に届かなくても、書き直した文を古い文で上書きしない
        office([pl, pc])
        repoll("() => officeAgents(LAST_OFFICE).length === 2 && !!sessionAgent('e2e-r116-ad') && !!sessionAgent('e2e-r116-c')")
        page.evaluate(hold_js, [pl["session"], "__release5", "古い文"])
        held5 = wait("() => !!window.__release5", ms=6000)
        stored = page.evaluate("""() => { openApprove(sessionAgent('e2e-r116-c')); openApprove(sessionAgent('e2e-r116-ad')); approveDenyWrite();
            document.getElementById('apvdenytext').value = '書き直した文'; openApprove(sessionAgent('e2e-r116-c'));
            const d = APV_DRAFT['e2e-r116-ad']; window.__release5(); return d ? d.t : null; }""")
        page.wait_for_timeout(600)
        r14 = page.evaluate("""() => { window.fetch = window.__f4; window.__f4 = null; const d = APV_DRAFT['e2e-r116-ad'];
            openApprove(sessionAgent('e2e-r116-ad')); return { draft: d ? d.t : null, box: document.getElementById('apvdenytext').value }; }""")
        if held5 and stored == "書き直した文" and r14 == {"draft": "書き直した文", "box": "書き直した文"}:
            print("  ✓ R116-S1（4 巡目のレビュー）送信中に同じ件で書き直した文は、その後に届かなかった古い文で上書きしない")
        else:
            bad(f"⑭ 書き直した文が古い文で上書きされた: 止めた={held5} 離れたときの控え={stored!r} 失敗の後={r14}")

        # ③ の前: 端末（セッションの画面）の送信口も許可待ちには「拒否して送る」
        office([pa, idle], templates=tpls)
        repoll("() => officeAgents(LAST_OFFICE).length === 2 && !!sessionAgent('e2e-r116-a') && !!sessionAgent('e2e-r116-i')")
        tsend = page.evaluate("""() => { closeSheet(); setView('office', true); openTerm(sessionAgent('e2e-r116-a'));
            const a = document.getElementById('term_send').textContent; openTerm(sessionAgent('e2e-r116-i'));
            const b = document.getElementById('term_send').textContent; closeTerm(true, true); return [a, b]; }""")
        if tsend == ["拒否して送る", "送信"]:
            print("  ✓ R116-S1 端末の送信口: 許可待ちは「拒否して送る」・指示待ちは「送信」")
        else:
            bad(f"端末の送信口の言葉が違う: {tsend}")

        # ③ 許可待ちのシート: 見えるボタンを全部押して send() を数える＝拒否と書いていない送信口が 0
        office([pa, idle], templates=tpls)
        repoll("() => officeAgents(LAST_OFFICE).length === 2 && (LAST_OFFICE.templates || []).length === 2")
        probe_js = """async (sess) => {
          const find = () => officeAgents(LAST_OFFICE).find(x => x.session === sess);
          const reset = () => { closeApprove(true); if (TERM) closeTerm(true, true); closeSheet(); closeLog(); closeMini(); setView('office', true); };
          const vis = (n) => { const r = n.getBoundingClientRect(); return r.width > 0 && r.height > 0; };
          const shown = () => [...document.querySelectorAll('#sheetwrap button')].filter(vis);
          reset(); openSheet(find());
          const out = { labels: shown().map(b => (b.textContent || '').trim()), send: document.getElementById('sh_send').textContent,
            tpl: document.querySelectorAll('#sheettemplates button').length,
            quick: [...document.querySelectorAll('#quickbtns > button')].map(b => (b.textContent || '').trim()), hits: [] };
          const saved = window.send;
          try {
            for (let i = 0; i < out.labels.length; i++) {
              reset(); openSheet(find()); document.getElementById('freetext').value = 'r116 のことば';
              const b = shown()[i]; if (!b) continue;
              const calls = [], lab = (b.textContent || '').trim(); window.send = (text) => { calls.push(String(text)); return Promise.resolve(true); };
              b.click(); await new Promise(r => setTimeout(r, 80)); window.send = saved;
              if (calls.length) out.hits.push({ label: lab, n: calls.length });
            }
          } finally { window.send = saved; reset(); }
          return out; }"""
        sp = page.evaluate(probe_js, pa["session"])
        si = page.evaluate(probe_js, idle["session"])
        silent = [h for h in sp["hits"] if "拒否" not in h["label"]]
        # 開いた後で許可待ちになったら、status で送信口の言葉が変わる（いまの状態で決める）
        page.evaluate("(s) => { setView('office', true); openSheet(officeAgents(LAST_OFFICE).find(e => e.session === s)); }", idle["session"])
        before = page.evaluate("() => document.getElementById('sh_send').textContent")
        office([pa, dict(idle, ask={"kind": "permission", "tool": "Bash", "remote": 1, "id8": "116d0d0d"}, approvalMin=1)],
               templates=tpls)
        live = repoll("() => document.getElementById('sh_send').textContent === '拒否して送る'")
        page.evaluate("() => closeSheet()")
        if (not silent and sp["hits"] == [{"label": "拒否して送る", "n": 1}] and sp["send"] == "拒否して送る" and sp["tpl"] == 0
                and not any(("停止" in q or "報告" in q) for q in sp["quick"])
                and si["send"] == "この指示を送る" and si["tpl"] == 2 and any("停止" in q for q in si["quick"])
                and before == "この指示を送る" and live):
            print(f"  ✓ R116-S0 ③ 許可待ちのシートの送信口は「拒否して送る」の 1 つだけ（見える {len(sp['labels'])} 個を全部押して send() は"
                  " 1 回）・定型と定型チップ無し・指示待ちは「この指示を送る」＋定型のまま・開いた後で許可待ちになったら status で「拒否して送る」")
        else:
            bad(f"③ 拒否と書いていない送信口がある: 許可待ち={sp} 指示待ち={si} 黙った送信口={silent} 切替={before!r}→{live}")

        # ③b 開いたシートの相手が status で許可待ちになったら、いまの相手で組み直す（開いた時点の写しのまま定型が黙った拒否になった）
        idle_perm = dict(idle, ask={"kind": "permission", "tool": "Bash", "remote": 1, "id8": "116d0d0d"}, approvalMin=1)
        st_idle = status_of([pa, idle], templates=tpls)
        st_perm = status_of([pa, idle_perm], templates=tpls)
        office([pa, idle], templates=tpls)
        repoll("() => sessKind(sessionAgent('e2e-r116-i')) === 'idle' && (LAST_OFFICE.templates || []).length === 2")
        probe2 = """async ([sess, before, after]) => {
          const vis = (n) => { const r = n.getBoundingClientRect(); return r.width > 0 && r.height > 0; };
          const shown = () => [...document.querySelectorAll('#sheetwrap button')].filter(vis);
          const reset = () => { applyStatus(before); closeApprove(true); if (TERM) closeTerm(true, true); closeSheet(); closeLog(); closeMini();
            setView('office', true); document.getElementById('tapcatch').hidden = true; };
          const stale = () => { reset(); openSheet(sessionAgent(sess)); document.getElementById('freetext').value = 'r116 のことば'; applyStatus(after); };
          stale();
          const out = { send: document.getElementById('sh_send').textContent, who: document.getElementById('shwho').textContent,
            selId8: askId8(SEL), liveId8: askId8(sessionAgent(sess)), draft: document.getElementById('freetext').value,
            tpl: document.querySelectorAll('#sheettemplates button').length, catcher: !document.getElementById('tapcatch').hidden,
            quick: [...document.querySelectorAll('#quickbtns > button')].filter(vis).map(b => (b.textContent || '').trim()),
            labels: shown().map(b => (b.textContent || '').trim()), hits: [] };
          const saved = window.send;
          try {
            for (let i = 0; i < out.labels.length; i++) {
              stale(); const b = shown()[i]; if (!b) continue;
              const calls = [], lab = (b.textContent || '').trim(); window.send = (text, target) => { calls.push({ text: String(text), id8: askId8(target || SEL) }); return Promise.resolve(true); };
              b.click(); await new Promise(r => setTimeout(r, 80)); window.send = saved;
              if (calls.length) out.hits.push({ label: lab, n: calls.length, id8: calls[0].id8 });
            }
          } finally { window.send = saved; reset(); }
          return out; }"""
        sb = page.evaluate(probe2, [idle["session"], st_idle, st_perm])
        # #1→#2: 送る印もいまの要求（以前は #1 の印のまま「拒否して送る」＝一致する掲示が無く指示として届き、#2 は拒否されなかった）
        st_x = status_of([pa, idle], templates=tpls)
        st_y = status_of([dict(pa, ask=dict(pa["ask"], id8="116a9999")), idle], templates=tpls)
        sxy = page.evaluate("""async ([before, after]) => {
            applyStatus(before); closeApprove(true); closeSheet(); setView('office', true);
            openSheet(sessionAgent('e2e-r116-a')); document.getElementById('freetext').value = 'r116 の 1 件目への言葉';
            applyStatus(after);
            const saved = window.send, calls = [];
            window.send = (text, target) => { calls.push({ text: String(text), id8: askId8(target || SEL) }); return Promise.resolve(true); };
            try { document.getElementById('sh_send').click(); await new Promise(r => setTimeout(r, 80)); } finally { window.send = saved; }
            const r = { send: document.getElementById('sh_send').textContent, selId8: askId8(SEL), who: document.getElementById('shwho').textContent, calls };
            closeSheet(); document.getElementById('tapcatch').hidden = true; applyStatus(before); return r; }""", [st_x, st_y])
        # 替わった直後の本物のタップは受け皿が受ける（送らない・「画面が更新されました」と言う）
        page.evaluate("""([sess, before]) => { applyStatus(before); closeApprove(true); closeSheet(); setView('office', true);
            openSheet(sessionAgent(sess)); document.getElementById('freetext').value = 'r116 のことば'; }""", [idle["session"], st_idle])
        still = wait(sheet_still)
        cr = page.evaluate("""([sess, before, after]) => { window.__guard = ATTN_GUARD_MS; ATTN_GUARD_MS = 4000;
            window.__calls = 0; window.__send0 = window.send; window.send = () => { window.__calls++; return Promise.resolve(true); };
            applyStatus(after); const b = document.getElementById('sh_send').getBoundingClientRect();
            const x = b.left + b.width / 2, y = b.top + b.height / 2, h = document.elementFromPoint(x, y);
            return { x, y, hit: h ? h.id : '' }; }""", [idle["session"], st_idle, st_perm])
        page.touchscreen.tap(cr["x"], cr["y"])
        page.wait_for_timeout(300)
        ctap = page.evaluate("""(before) => { const r = { calls: window.__calls, note: (document.getElementById('note') || {}).textContent || '' };
            window.send = window.__send0; ATTN_GUARD_MS = window.__guard; document.getElementById('tapcatch').hidden = true; closeSheet();
            applyStatus(before); return r; }""", st_idle)
        # 押した時点で相手が替わっていれば送らない（status を通さずに相手だけ替えた＝組み直しの前に押された）
        sf = page.evaluate("""async ([sess, before]) => { applyStatus(before); closeApprove(true); closeSheet(); setView('office', true);
            openSheet(sessionAgent(sess));
            LAST_OFFICE = JSON.parse(JSON.stringify(LAST_OFFICE));
            const e2 = officeAgents(LAST_OFFICE).find(x => x.session === sess);
            e2.ask = { kind: 'permission', tool: 'Bash', remote: 1, id8: '116f0f0f' }; e2.approvalMin = 1;
            const chip = [...document.querySelectorAll('#sheettemplates button')].find(b => (b.textContent || '').indexOf('報告して') >= 0);
            const saved = window.send, calls = []; window.send = (t) => { calls.push(String(t)); return Promise.resolve(true); };
            const savedPoll = window.poll; window.poll = () => {}; let r0 = {};
            try { if (chip) chip.click(); r0 = { note: (document.getElementById('note') || {}).textContent || '',
                    tpl: document.querySelectorAll('#sheettemplates button').length, selId8: askId8(SEL) };
                  await new Promise(r => setTimeout(r, 80)); } finally { window.send = saved; window.poll = savedPoll; }
            const r = { chip: !!chip, calls, note: r0.note || '', tpl: r0.tpl, selId8: r0.selId8 };
            closeSheet(); document.getElementById('tapcatch').hidden = true; applyStatus(before); return r; }""", [idle["session"], st_idle])
        office([pa, idle], templates=tpls)
        silent2 = [h for h in sb["hits"] if "拒否" not in h["label"]]
        if (not silent2 and sb["hits"] == [{"label": "拒否して送る", "n": 1, "id8": "116d0d0d"}] and sb["send"] == "拒否して送る"
                and sb["selId8"] == sb["liveId8"] == "116d0d0d" and sb["who"].startswith("Bash の許可") and sb["draft"] == "r116 のことば"
                and sb["tpl"] == 0 and not any(("停止" in q or "報告" in q) for q in sb["quick"]) and sb["catcher"]
                and sxy["send"] == "拒否して送る" and sxy["selId8"] == "116a9999"
                and sxy["calls"] == [{"text": "r116 の 1 件目への言葉", "id8": "116a9999"}]
                and still and cr["hit"] == "tapcatch" and ctap["calls"] == 0 and "画面が更新されました" in ctap["note"]
                and sf["chip"] and sf["calls"] == [] and "状態が変わったので送りませんでした" in sf["note"] and sf["tpl"] == 0 and sf["selId8"] == "116f0f0f"):
            print(f"  ✓ R116-S0b ③b 開いた後で許可待ちになったシートはいまの相手で組み直す（見える {len(sb['labels'])} 個を全部押して send() は「拒否して送る」の"
                  " 1 回・印はいまの要求・書きかけは残る）・#1→#2 の印も #2・替わった直後の本物のタップは受け皿が受ける・押した時点で相手が替わっていれば送らない")
        else:
            bad(f"③b 開いた後で替わった相手へ黙った拒否・違う印: 組み直し={sb} 黙った送信口={silent2} #1→#2={sxy} 受け皿={cr}{ctap} 押した時点={sf}")
        if errors:
            bad(f"page error: {errors[:3]}")
        ctx.close()
    except Exception as exc:  # noqa: BLE001  操作の待ちが切れた等＝赤として数える（後ろのピンを止めない）
        bad(f"途中で例外: {type(exc).__name__}: {str(exc)[:200]}")
    finally:
        browser.close()
    return len(fails)


def _r116s1_budget_pin(p, base_url, device_id, secret, token):
    """R116-S1 門③（D3 の予算）: 許可のカードは状態ごとに 45 字以下（人の名前・道具・命令の箱は数えない）・押せる物 3 つ
    （命令が箱に入りきらないときだけ［全文］で 4 つ）。390×844・375×667（小さい iPhone）・文字「大きめ」でも、ボタンは全部画面の中で押せる。
    状態は JS で直接作る（封書の往復・判定は _r100_apv_pins と _r114_tap_pins が実物で見る）。"""
    from urllib.parse import urlsplit
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "server"))
    import approval_policy as ap  # noqa: E402
    u = urlsplit(base_url)
    lbase = f"{u.scheme}://localhost:{u.port}"
    cred = json.dumps({"d": device_id, "s": secret, "t": token, "e": 0}, separators=(",", ":"))
    e = {"session": "e2e-r116b-perm", "projectId": "e2e-r116b-perm", "disp": "web-app", "state": "waiting", "listening": True,
         "approvalMin": 3, "ask": {"kind": "permission", "tool": "Bash", "remote": 1, "id8": "b0d9e7a1", "min": 3}}
    office = {"officeName": "E2E 予算", "employees": [e],
              "actions": {"caps": {"approve": 1}, "approve": {"on": 1, "until": 4102444800, "fps": [], "enr": []}}}
    status = json.dumps({"ok": True, "ts": int(time.time() * 1000), "agentSeenAgo": 0, "agentOnline": True,
                         "json": json.dumps(office, ensure_ascii=False)}, ensure_ascii=False)

    def page_of(cmd, level="remote", reasons=(), tags=()):
        canon = ap.input_canon("Bash", {"command": cmd, "description": "テストを回す", "timeout": 120000})
        return {"kind": "ask", "session": e["session"], "askId": "b0d9e7a1" + "0" * 24, "tool": "Bash", "inputCanon": canon,
                "inputSha": ap.input_sha(canon), "level": level, "macOnly": 0 if level != "unjudged" else 1, "reasons": list(reasons),
                "tags": list(tags), "cwdBase": "web-app", "iat": int(time.time())}
    measure = """() => { const c = document.getElementById('apvwrap');
        const skip = (n) => n.closest('#apvname, #apvbox, .apvtool, #apvrest, #apvdenybox, #apvdenyundo');
        let text = ''; const walk = (n) => { for (const ch of n.childNodes) { if (ch.nodeType === 3) { if (!skip(ch.parentElement)) {
            const s = getComputedStyle(ch.parentElement); if (s.display !== 'none' && s.visibility !== 'hidden') text += ch.textContent; } }
          else if (ch.nodeType === 1 && !ch.hidden && getComputedStyle(ch).display !== 'none') walk(ch); } };
        walk(c);
        const btns = [...c.querySelectorAll('button')].filter(b => !b.hidden && b.getClientRects().length && getComputedStyle(b).display !== 'none'
          && !b.closest('#apvdenybox, #apvdenyundo, [hidden]'));
        const hit = btns.map(b => { b.scrollIntoView({ block: 'nearest' }); const r = b.getBoundingClientRect(),
            h = document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2);
          return { t: b.textContent.trim(), ok: r.left >= 0 && r.right <= innerWidth + 1 && r.height >= 44 && !!h && (h === b || b.contains(h)) }; });
        return { chars: text.replace(/\\s+/g, '').length, text: text.replace(/\\s+/g, ' ').trim(), ops: btns.length, hit,
                 state: c.getAttribute('data-state'), full: !!document.getElementById('apvfullbtn') }; }"""
    cases = [
        ("ready", None, page_of("npm test 2>&1 | tail -20")),
        ("ready(mac)", None, page_of("rm -rf .cache/tmp", "mac", ["delete"])),
        ("ready(review)", None, page_of("cd ../other && make", "review", [], ["outside-cwd", "script"])),
        ("ready(long)", None, page_of("\n".join("echo line %d" % i for i in range(9)))),
        ("need-on", "window.apvOnNow = () => false; localStorage.setItem('aioffice.apvUntil', String(Math.floor(Date.now() / 1000) - 600));", None),
        ("mac-only", None, dict(page_of("eval $(cat x)", "mac", ["dynamic"]), macOnly=1)),
    ]
    fails, seen = [], {}
    browser = p.chromium.launch()
    try:
        for shape, vw, vh, big in (("390×844", 390, 844, False), ("375×667", 375, 667, False), ("390×844 大きめ", 390, 844, True)):
            ctx = browser.new_context(viewport={"width": vw, "height": vh}, has_touch=True, is_mobile=True, service_workers="block")
            page = ctx.new_page()
            page.add_init_script("localStorage.setItem('aioffice.cred', " + json.dumps(cred) + ");"
                                 + ("localStorage.setItem('aioffice.big', '1');" if big else "")
                                 + "window.WebSocket = function () { throw new Error('budget: WS off'); };")
            page.route("**/status", lambda r: r.fulfill(status=200, content_type="application/json", body=status)
                       if r.request.method == "GET" else r.continue_())
            page.route("**/instruct", lambda r: r.fulfill(status=200, content_type="application/json", body='{"ok":true}'))
            page.goto(lbase + "/app/", wait_until="domcontentloaded")
            page.wait_for_function("() => typeof apvSync === 'function' && officeAgents(LAST_OFFICE).length === 1", timeout=30000)
            if big:
                page.evaluate("() => { if (typeof setBig === 'function') setBig(true); }")
            for name, js, pg in cases:
                page.evaluate("""([js, e, pg]) => { window.apvOnNow = () => true; localStorage.removeItem('aioffice.apvUntil');
                    if (js) (0, eval)(js); closeApprove(true); setView('office', true); openApprove(e);
                    if (pg) { APV.page = pg; APV.at = Date.now(); } APV.sig = ''; apvRender(); }""", [js, e, pg])
                page.wait_for_timeout(1400)
                m = page.evaluate(measure)
                limit_ops = 4 if m["full"] else 3
                bad_hit = [h["t"] for h in m["hit"] if not h["ok"]]
                seen.setdefault(name, []).append((m["chars"], m["ops"], m["state"]))
                if m["chars"] > 45 or m["ops"] > limit_ops or bad_hit or m["chars"] < 5 or m["state"] != name.split("(")[0]:
                    fails.append(f"{shape} {name}: {m['chars']} 字・押せる物 {m['ops']}（上限 {limit_ops}）・押せない {bad_hit}・「{m['text']}」")
            ctx.close()
    finally:
        browser.close()
    if fails:
        for f in fails:
            print(f"  ✗ R116-S1 ③ 予算: {f}")
        return len(fails)
    mx = "・".join(f"{k} {max(c for c, _o, _s in v)} 字/{max(o for _c, o, _s in v)}" for k, v in seen.items())
    print(f"  ✓ R116-S1 ③ 予算: {len(cases)} 状態（R118: mac も 1 タップ・登録の段は無い）× 390×844・375×667・大きめで 45 字以下・押せる物 3 つ（折りたたみ時だけ［全文］で 4）・全部画面の中で押せる（{mx}）")
    return 0


def _has_pictograph(s):
    """絵文字（❗・🏢 など）を含むか。記号の「·」「—」「（）」は含めない。"""
    return any(0x1F000 <= ord(c) <= 0x1FAFF or 0x2600 <= ord(c) <= 0x27BF or 0x2B00 <= ord(c) <= 0x2BFF or ord(c) == 0xFE0F
               for c in str(s))


def _r116_push_pin():
    """R116-S0 ④: 通知（Web Push）の題は「AI Office」・本文は「<名前> · <何を>（<いつから>）」＝絵文字と「（0分）」が無い・命令や質問の
    本文は載せない（不変条件 8）。worker.js の sendAttnPushes を単体で走らせる（test_notify_report と同じ切り出し・Web Push の実物は手動 E2E）。"""
    import subprocess
    src = (Path(__file__).resolve().parents[1] / "relay" / "src" / "worker.js").read_text(encoding="utf-8")
    try:
        targets = src[src.index("function pushTargets("):src.index("// R5_PUSH_TARGETS_END")]
        push = src[src.index("async function sendAttnPushes("):src.index("// P7: /status push")]
    except ValueError as exc:
        print(f"  ✗ R116-S0 ④ worker.js の sendAttnPushes が読めない: {exc}")
        return 1
    script = targets + "\n" + push + "\n" + r"""
const sent = [];
async function sendWebPush(sub, payload) { sent.push(payload); return 201; }
(async () => {
  const out = [];
  for (const entry of JSON.parse(process.argv[1])) {
    const room = { getStatus: async () => ({ json: JSON.stringify({ roster: [entry] }) }) };
    const n0 = sent.length;
    await sendAttnPushes(room, { VAPID_JWK: '{}' }, ['p1'], { p1: { disp: entry.disp, dept: 'demo' } },
      [{ k: 'push:t', v: '{}' }], 'https://x.test');
    out.push(sent.length > n0 ? sent.at(-1) : null);
  }
  console.log(JSON.stringify(out));
})().catch((e) => { console.error(e); process.exitCode = 1; });
"""
    base = {"projectId": "p1", "disp": "Demo", "state": "waiting"}
    cases = [
        dict(base, ask={"kind": "permission", "tool": "Bash", "min": 0, "title": "PRIVATE_rm -rf /"}),
        dict(base, ask={"kind": "permission", "tool": "Bash", "min": 3}),
        dict(base, question="PRIVATE_QUESTION", ask={"kind": "question", "tool": "AskUserQuestion", "min": 0}),
        dict(base, err={"k": "api", "ago": 30}, approvalMin=0),
    ]
    want = ["Demo · Bash の許可（いま）", "Demo · Bash の許可（3分）", "Demo · 質問（いま）", "Demo · API エラーで止まっています"]
    try:
        res = subprocess.run(["node", "-e", script, json.dumps(cases, ensure_ascii=False)], capture_output=True, text=True, timeout=20)
        got = json.loads(res.stdout or "null") if res.returncode == 0 else None
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        print(f"  ✗ R116-S0 ④ node で sendAttnPushes を走らせられない: {exc}")
        return 1
    if not got or len(got) != len(cases) or not all(got):
        print(f"  ✗ R116-S0 ④ 通知が出ない: rc={res.returncode} {res.stderr[:200]} {res.stdout[:200]}")
        return 1
    bodies = [g.get("body", "") for g in got]
    titles = [g.get("title", "") for g in got]
    text = json.dumps(got, ensure_ascii=False)
    if (bodies == want and titles == ["AI Office"] * len(cases) and not any(_has_pictograph(t) for t in bodies + titles)
            and "（0分）" not in text and "PRIVATE_" not in text):
        print(f"  ✓ R116-S0 ④ 通知は題「AI Office」・本文「{bodies[0]}」の形（絵文字と「（0分）」無し・命令と質問の本文は載せない）")
        return 0
    print(f"  ✗ R116-S0 ④ 通知の題・本文が違う: 題={titles} 本文={bodies}")
    return 1


def _r114_nocred_pins(p, base_url, device_id, secret, token):
    """R114-P（本人「QR コードをスキャンすることになったりする」）: iOS はホーム画面のアプリと Safari で保存（localStorage）が別＝通知や
    リンクから Safari で開くと資格情報が無い。そこで Mac のペアリング（QR・リンク）へ誘導すると、再ペアリング（新しい端末 id）の最長ルートに落ちる。
      ① standalone でない（ブラウザ）: 「このブラウザにはペアリングがありません。ホーム画面の AI Office のアイコンから開いてください」を最優先に出し、
         Mac のペアリングの案内（スマホ連携・リンクの貼り付け）は「初めて使う」を押すまで出さない
      ② standalone（ホーム画面のアプリ＝navigator.standalone）: 従来のペアリングの案内
      ③ R115-S4（R114-P の別モデルレビュー medium）: どちらも /diag を 1 件も送らない（Bearer が無い＝Worker は 401 で捨てる＝届かない経路を
         叩かない）。旧 ③ は「/diag に kind:"nocred"（Bearer なし）」だった。/diag は route で受けて数える"""
    fails = []

    def bad(msg):
        fails.append(msg)
        print(f"  ✗ R114-P nocred {msg}")

    browser = p.chromium.launch()
    try:
        for standalone in (False, True):
            ctx = browser.new_context(viewport={"width": 390, "height": 844}, has_touch=True, is_mobile=True, service_workers="block")
            page = ctx.new_page()
            errors, diag = [], []
            page.on("pageerror", lambda e: errors.append(str(e)))

            def on_diag(route, request, box=diag):
                box.append({"body": request.post_data or "", "auth": request.headers.get("authorization")})
                route.fulfill(status=204, body="")
            page.route("**/diag", on_diag)
            if standalone:
                page.add_init_script("Object.defineProperty(navigator, 'standalone', { get: () => true });")
            page.goto(base_url.rstrip("/") + "/app/", wait_until="domcontentloaded")
            page.wait_for_function("() => typeof showSetup === 'function' && !document.getElementById('setup').classList.contains('hidden')",
                                   timeout=30000)
            page.wait_for_timeout(300)
            look = """() => { const vis = (id) => { const n = document.getElementById(id); return !!n && n.getClientRects().length > 0; };
                return { home: vis('su_home'), pair: vis('su_pair'), paste: vis('pastebox'), more: vis('su_home_pair'),
                         text: document.getElementById('setup').innerText, cred: localStorage.getItem('aioffice.cred') }; }"""
            v = page.evaluate(look)
            diag_ok = not diag                              # R115-S4: Bearer の無い文脈は kind に依らず送らない
            if not standalone:
                page.click("#su_home_pair")
                page.wait_for_timeout(200)
                v2 = page.evaluate(look)
                if (v["home"] and not v["pair"] and not v["paste"] and v["more"] and v["cred"] is None
                        and "ホーム画面の AI Office のアイコンから開いてください" in v["text"] and "スマホ連携" not in v["text"]
                        and v2["pair"] and v2["paste"] and not v2["more"] and diag_ok and not diag):
                    print("  ✓ R114-P nocred ① ブラウザ（standalone でない）は「ホーム画面の AI Office のアイコンから開いてください」を最優先・"
                          "Mac のペアリングの案内は「初めて使う」を押すまで出さない・/diag は送らない（R115-S4・Bearer なし）")
                else:
                    bad(f"① ブラウザで開いたときの案内が違う: 初め={v} 押した後={v2} /diag={diag}")
            else:
                if v["pair"] and v["paste"] and not v["home"] and "スマホ連携" in v["text"] and diag_ok:
                    print("  ✓ R114-P nocred ② ホーム画面のアプリ（standalone）は従来のペアリングの案内・/diag は送らない（R115-S4）")
                else:
                    bad(f"② standalone の案内が違う: {v} /diag={diag}")
            if errors:
                bad(f"page error（standalone={standalone}）: {errors[:3]}")
            ctx.close()
    except Exception as exc:  # noqa: BLE001
        bad(f"途中で例外: {type(exc).__name__}: {str(exc)[:200]}")
    finally:
        browser.close()
    return len(fails)


R113B_SHAPE = (393, 793, "iPhone 393×793")


def _r113b_rows(now):
    """R113-B の端末の行のフィクスチャ（server/office_term.py の R113-B の契約の形・上限いっぱい）。id と for は 16 進（termPaint の結び方）。"""
    t0 = now - 300
    one = "python3 -c '" + "x" * 187 + "'"                                  # 200 字の 1 行（空白の無い長い語）
    forty = "\n".join(f"echo step-{i:02d} " + ("abcdefghij" * 9)[:86] for i in range(40))   # 40 行・3,999 字（入力の上限 4000 字/40 行）
    diff = [[" ", 9990, "def handler(event):"], [" ", 9991, "    pass"]]
    diff += [["+", 9992 + i, (f"    added_{i:02d} = compute({i}) " + "y" * 200)[:200]] for i in range(58)]   # 差分の上限 100 行×200 字
    diff += [["-", 9992 + i, (f"    removed_{i:02d} = old({i}) " + "z" * 200)[:200]] for i in range(40)]
    head = [f"FAIL test_{i:03d} " + "e" * 50 for i in range(48)]                    # 失敗の出力＝先頭 48 行＋中略＋末尾 71 行（120 行・約 7,600 字）
    tail = [f"  at frame {i:03d} " + "t" * 45 for i in range(71)]
    # パスはプロジェクトからの相対（200 字まで・空白なし）・Grep の検索語・WebFetch の scheme://host/path（server の B-S の契約）
    path = ("src/" + "/".join(f"very_long_directory_name_{i:02d}" for i in range(8)))[:192] + "/main.py"
    grep = 'pattern: "' + "needle_" * 16 + '", path: "src/app", glob: "*.py"'
    url = "https://docs.example.com/" + "/".join(f"section-{i:02d}" for i in range(14))
    kids = []
    for i in range(5):
        tid = f"c{i:07d}"
        kids += [{"k": "a", "t": f"子の文 {i}"}, {"k": "th", "n": 1, "chars": 40 + i},
                 {"k": "tool", "n": "Bash", "t": f"ls src/{i}", "id": tid}, {"k": "res", "for": tid, "t": f"file{i}.py", "nl": 1}]
    rows = [
        {"k": "u", "t": "テストを直して", "ts": t0},
        {"k": "th", "n": 3, "chars": 1240, "ts": t0 + 1},
        {"k": "th", "n": 1, "ts": t0 + 2},
        {"k": "tool", "n": "Bash", "t": one, "id": "b0000001", "ts": t0 + 3},
        {"k": "res", "for": "b0000001", "t": "a\nb", "nl": 10, "ts": t0 + 4},
        {"k": "tool", "n": "Bash", "t": forty, "id": "b0000002", "ts": t0 + 5},
        {"k": "res", "for": "b0000002", "t": "\n".join(f"ok {i:03d} " + "o" * 58 for i in range(120)), "nl": 500, "ts": t0 + 6},
        {"k": "tool", "n": "Bash", "t": "npm test", "id": "b0000003", "ts": t0 + 7},
        {"k": "res", "for": "b0000003", "err": 1, "t": "\n".join(head + ["…（中略 381 行）…"] + tail), "nl": 500, "ts": t0 + 8},
        {"k": "tool", "n": "Read", "t": path, "id": "d0000001", "ts": t0 + 9},
        {"k": "res", "for": "d0000001", "rl": 120, "t": "120 行を読み込み", "ts": t0 + 9},
        {"k": "tool", "n": "Grep", "t": grep, "id": "d0000002", "ts": t0 + 9},
        {"k": "res", "for": "d0000002", "nf": 3, "t": "3 件", "ts": t0 + 9},
        {"k": "tool", "n": "WebFetch", "t": url, "id": "d0000003", "ts": t0 + 9},
        {"k": "res", "for": "d0000003", "t": "取得 200 · 12 KB", "code": 200, "ts": t0 + 9},
        {"k": "tool", "n": "Edit", "t": path, "id": "e0000001", "ts": t0 + 9},
        {"k": "res", "for": "e0000001", "f": path, "add": 130, "rem": 50, "d": diff,
         "t": path + " を更新（+130 −50）", "ts": t0 + 10},
        {"k": "tool", "n": "Task", "t": "テストの失敗を調べる", "sub": "Explore", "id": "aa11bb22", "ts": t0 + 11},
        {"k": "res", "for": "aa11bb22", "t": "完了（12 回 · 3.4k トークン · 40秒）", "tu": 12, "kids": kids, "ts": t0 + 12},
        {"k": "tool", "n": "Agent", "t": "別の調査", "id": "cc33dd44", "ts": t0 + 13},
        {"k": "res", "for": "cc33dd44", "t": "完了", "kx": 12, "ts": t0 + 14},
        {"k": "a", "t": "直しました", "ts": t0 + 15},
    ]
    return rows


def _r113b_pins(p, base_url, device_id, secret, token):
    """R113-B（本人裁定 2026-10-08「それぞれのターミナルのセッション画面がちゃんと表示される方がいい。Claude Code や Codex とかのやり取りが、
    そのまま表示される設計に」）: スマホのセッション画面。393×793（iPhone の Safari の見える高さ）で本番の app.js を踏む。
      ① Codex（cx-）のシートに「セッションを開く」→ 押すと kind:"term" の封書の要求（dlg- 宛・署名は本物の端末秘密）→ Mac が封じた形
         （server/dialog_seal.seal）を onDlg へ → 端末に描かれる。送る言葉の案内は Codex のまま（見えると打てるは別）
      ②〜⑤ R121: 共有作業ビューは実行を1行にまとめ、groupとstepの展開時だけ詳細DOMを作る。
         命令8行・出力3行・差分16行で畳む。展開後の残数・差分の符号・子の末尾も検査する。
      ⑥ 「スマホに会話を見せる」が OFF の「最近の動き」の履歴（openLog）にも端末と同じ理由の文・OpenClaw の説明から Codex が消えた
      ⑦ 一覧・シート・端末の見出しに道具の印（.vendor-dot.vendor-codex）
      ⑧ R113-B-S3: 届くまでの初期表示は .tinit。差分の再描画でも開いた詳細を保持する。
         R121の最新の質問カードから従来の署名付き回答へつながる。
      ＋ 393 幅で #termbody の配下に横へはみ出す要素が 0（畳んだまま・全部開いた後の両方）
    状態は page.route の /status で固定し WebSocket は止める（中継の DO の状態で上書きされない）。"""
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "server"))
    import dialog_seal  # noqa: E402
    if not dialog_seal.AVAILABLE:
        print("  · R113-B: 封緘バックエンドが無い環境＝セッション画面のピンは省略")
        return 0
    fails = []

    def bad(msg):
        fails.append(msg)
        print(f"  ✗ R113-B {msg}")

    w, h, label = R113B_SHAPE
    cx = {"session": "cx-r113b-0001", "projectId": "r113b-codex", "disp": "E2E Codex", "state": "working", "vendor": "codex",
          "verb": "実行中", "listening": False, "feed": ["実行中 npm test"]}
    cc = {"session": "e2e-r113b-claude", "projectId": "r113b-claude", "disp": "E2E Claude", "state": "waiting", "vendor": "claude",
          "verb": "待機中", "listening": True, "feed": ["編集 handler.py", "実行中 npm test"]}
    oc = {"session": "oc-r113b", "disp": "E2E OpenClaw", "dept": "OpenClaw", "state": "working", "verb": "replying",
          "external": "openclaw", "vendor": "openclaw"}
    office = {"officeName": "E2E R113B", "avatarMode": "session", "employees": [cx, cc, oc], "actions": {"caps": {"dialog": 1}}}
    status = json.dumps({"ok": True, "ts": int(time.time() * 1000), "agentSeenAgo": 0, "agentOnline": True,
                         "json": json.dumps(office, ensure_ascii=False)}, ensure_ascii=False)
    sent = []

    def on_instruct(route, request):
        if request.method != "POST":
            route.continue_()
            return
        sent.append(request.post_data or "")
        route.fulfill(status=200, content_type="application/json", body='{"ok":true}')

    def on_status(route, request):
        if request.method == "GET":
            route.fulfill(status=200, content_type="application/json", body=status)
        else:
            route.continue_()

    cred = json.dumps({"d": device_id, "s": secret, "t": token, "e": 0}, separators=(",", ":"))
    overflow_js = """() => { const b = document.getElementById('termbody');
      const wide = [...b.querySelectorAll('*')].filter(n => getComputedStyle(n).display !== 'inline' && n.scrollWidth > n.clientWidth + 1)
        .map(n => (n.className || n.tagName) + ' ' + n.scrollWidth + '>' + n.clientWidth);
      return { wide: wide.slice(0, 6), n: wide.length, body: b.scrollWidth - b.clientWidth, page: document.documentElement.scrollWidth - innerWidth,
               w: Math.round(b.getBoundingClientRect().width) }; }"""
    browser = p.chromium.launch()
    try:
        ctx = browser.new_context(viewport={"width": w, "height": h}, has_touch=True, is_mobile=True, device_scale_factor=3,
                                  service_workers="block")
        page = ctx.new_page()
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.on("dialog", lambda d: d.accept())
        page.add_init_script("localStorage.setItem('aioffice.cred', " + json.dumps(cred) + ");"
                             "window.WebSocket = function () { throw new Error('r113b: WS off'); };")
        page.route("**/instruct", on_instruct)
        page.route("**/status", on_status)
        page.route("**/diag", lambda route: route.fulfill(status=204, body=""))
        page.goto(base_url.rstrip("/") + "/app/", wait_until="domcontentloaded")
        page.wait_for_function("() => typeof openTerm === 'function' && window.__activity && officeAgents(LAST_OFFICE).length === 3 && canDlg()", timeout=30000)

        # ⑦（一覧）＋①（シートの入口）
        v7 = page.evaluate("""() => { setView('list', true);
          const card = document.querySelector('#list .recentrow[data-sess="cx-r113b-0001"]'), claude = document.querySelector('#list .recentrow[data-sess="e2e-r113b-claude"]');
          const out = { list: !!(card && card.querySelector('.recentline') && card.querySelector('.recentline').textContent === recentLine(sessionAgent('cx-r113b-0001'))), claude: !!(claude && claude.querySelector('.recentline') && claude.querySelector('.recentline').textContent === recentLine(sessionAgent('e2e-r113b-claude'))) };
          openSheet(officeAgents(LAST_OFFICE).find(e => e.session === 'cx-r113b-0001'));
          const sn = document.getElementById('shname');
          out.sheet = !!sn.querySelector('.vendor-dot.vendor-codex'); out.name = sn.textContent; out.term = !!document.getElementById('sh_term');
          out.can = canTermFor(SEL); out.dlg = canDlgFor(SEL); return out; }""")
        if not (v7["list"] and v7["claude"] and v7["sheet"] and v7["name"] == "E2E Codex"):
            bad(f"⑦ 最近の文章1行／シートの道具の印が違う: {v7}")
        if not (v7["term"] and v7["can"] and not v7["dlg"]):
            bad(f"① Codex のシートに「セッションを開く」が無い（canTermFor={v7['can']}・会話の入口 canDlgFor={v7['dlg']}）")
        n0 = len(sent)
        page.locator("#sh_term").click()
        _wait_sent(page, sent, n0 + 1, ms=8000)
        ok, why, sess, text = _verify(sent[n0], device_id, secret) if len(sent) > n0 else (False, "no request", "", "")
        act = json.loads(text) if ok else {}
        rid = act.get("reqId", "")
        if not (ok and sess == f"dlg-{rid}" and act.get("kind") == "term" and act.get("session") == cx["session"] and act.get("after") is None):
            bad(f"① 開いても kind:term の封書の要求が飛ばない: verify={ok}({why}) session={sess} act={act}")
            raise RuntimeError("要求が無い＝以降の描画を検査できない")
        # ⑧-1 R113-B-S3: 届くまでの初期表示（経過秒の行＋手元の「最近の動き」）の印は tinit＝本文の pre ブロック（tpre）とは別。
        #   共有していると本文の罫（1px の枠）・折りたたみ・見切れ検査が初期表示にも効き、差分の頁で termPaint が本文の pre まで消した
        v8_js = """() => { const b = document.getElementById('termbody'), ini = [...b.querySelectorAll('.tinit')];
          const feed = ini.filter(n => !n.classList.contains('gap')), gap = b.querySelector('.tl.gap.tinit');
          return { pre: b.querySelectorAll('.tpre').length, init: ini.length, feed: feed.length, gap: gap ? gap.textContent : '',
                   border: feed[0] ? getComputedStyle(feed[0]).borderTopWidth : '', more: b.querySelectorAll('.tmore').length }; }"""
        for _ in range(30):                                  # 経過秒の行は setTimeout(0) の後に書かれる（クリック直後は空＝実測）
            v8 = page.evaluate(v8_js)
            if v8["gap"]:
                break
            page.wait_for_timeout(100)
        if (v8["pre"] == 0 and v8["init"] == 1 + len(cx["feed"]) and v8["feed"] == len(cx["feed"]) and "最近の動き" in v8["gap"]
                and v8["border"] == "0px" and v8["more"] == 0):
            print(f"  ✓ R113-B-S3 ⑧ 届くまでの初期表示は .tinit（{v8['init']} 行・経過秒の行が動く）＝本文の pre ブロック（.tpre）0・"
                  "折りたたみ（.tmore）0・pre の罫なし")
        else:
            bad(f"⑧ 初期表示が本文の pre ブロック（.tpre）と同じ印のまま／折りたたみ・罫の対象になっている: {v8}")
        now = int(time.time())
        rows = _r113b_rows(now)
        bundle = dialog_seal.seal(secret, device_id, rid, cx["session"],
                                  {"kind": "term", "v": 2, "session": cx["session"], "reqId": rid, "iat": now, "rows": rows,
                                   "cur": {"off": 4096, "h": "0123456789abcdef"}, "reset": True, "gap": False}, iat=now)
        page.evaluate("(b) => onDlg(JSON.stringify({ v: 1, items: [b] }))", bundle)
        page.wait_for_function("() => document.querySelectorAll('#termbody .act-group').length > 0", timeout=10000)
        page.evaluate("() => { if (TERM && TERM.timer) { clearTimeout(TERM.timer); TERM.timer = null; } }")
        head = page.evaluate("""() => ({ vendor: !!document.querySelector('#termhead #termvendor.vendor-dot.vendor-codex'),
          before: (() => { const v = document.getElementById('termvendor'), n = document.getElementById('termname'); return !!v && v.nextElementSibling === n; })(),
          note: document.getElementById('termnote').textContent,
          pre: document.querySelectorAll('#termbody .act-step-body pre').length,
          steps: document.querySelectorAll('#termbody .act-step').length,
          groups: document.querySelectorAll('#termbody .act-group').length,
          user: document.querySelector('#termbody .act-user .act-user-text')?.textContent || '',
          text: document.querySelector('#termbody .act-text')?.textContent || '' })""")
        if not (head["vendor"] and head["before"] and "Codex の実行中のセッションへ積みます" in head["note"]):
            bad(f"①⑦ 封書の送信案内／道具の印が違う: {head}")
        if not (head["groups"] == 1 and head["pre"] == 0 and head["steps"] == 0
                and head["user"] == "テストを直して" and head["text"] == "直しました"):
            bad(f"R121 既定の作業ビューで道具の中身を作ってしまう／本文がない: {head}")
        else:
            print("  ✓ R121 封書の行は共有作業ビューへ・人の吹き出し／AI本文／実行の1行・道具の詳細DOMは既定0")
        o1 = page.evaluate(overflow_js)
        page.locator("#termbody .act-group-head").click()
        step_state = page.evaluate("""() => ({ n: document.querySelectorAll('#termbody .act-step').length,
          pre: document.querySelectorAll('#termbody .act-step-body pre').length })""")
        if step_state != {"n": 9, "pre": 0}:
            bad(f"R121 groupを開くと1手ずつ、詳細は未作成のままにならない: {step_state}")
        page.locator("#termbody .act-step-head").evaluate_all("nodes => nodes.forEach(n => n.click())")

        details = page.evaluate("""() => { const b = document.getElementById('termbody');
          const step = id => b.querySelector('[data-step-id="' + id + '"]');
          const one = step('b0000001'), forty = step('b0000002'), edit = step('e0000001');
          return { one: one.querySelector('.act-command pre').textContent.split('\\n').length,
            forty: forty.querySelector('.act-command pre').textContent.split('\\n').length,
            more: forty.querySelector('.act-command .act-more').textContent,
            output: forty.querySelector('.act-output pre').textContent.split('\\n').length,
            diff: edit.querySelectorAll('.act-diffline').length,
            signs: [...edit.querySelectorAll('.act-sign')].filter(n => n.textContent === '+' || n.textContent === '−').slice(0, 2).map(n => n.textContent),
            kids: b.querySelectorAll('.act-kids-body > *').length }; }""")
        if not (details["one"] == 1 and details["forty"] == 8 and "+32" in details["more"]
                and details["output"] == 3 and details["diff"] == 16 and details["kids"] == 0):
            bad(f"R121 命令8行／出力3行／差分16行・子のlazy描画が違う: {details}")
        else:
            print("  ✓ R121 1手を開いたときだけ命令・出力・差分を作成（8／3／16行で畳む）・子の中身は未作成")
        page.locator("#termbody .act-command .act-more").evaluate_all("nodes => nodes.forEach(n => n.click())")
        page.locator("#termbody .act-output .act-more").evaluate_all("nodes => nodes.forEach(n => n.click())")
        page.locator("#termbody .act-diff > .act-more").evaluate_all("nodes => nodes.forEach(n => n.click())")
        page.locator("#termbody .act-kids > .act-more").click()
        opened = page.evaluate("""() => { const b = document.getElementById('termbody');
          const step = id => b.querySelector('[data-step-id="' + id + '"]');
          const add = b.querySelector('.act-diffline .act-sign.act-add'), rem = b.querySelector('.act-diffline .act-sign.act-rem');
          return { command: step('b0000002').querySelector('.act-command pre').textContent.split('\\n').length,
            output: step('b0000002').querySelector('.act-output pre').textContent.split('\\n').length,
            rest: step('b0000002').querySelector('.act-output').textContent,
            diff: b.querySelectorAll('.act-diffline').length,
            signs: [add?.textContent, rem?.textContent],
            colors: [getComputedStyle(add).color, getComputedStyle(rem).color],
            expected: ['--t-accent','--t-attn'].map(k => getComputedStyle(document.documentElement).getPropertyValue(k).trim()),
            kids: b.querySelectorAll('.act-kids-body .act-group').length,
            omitted: step('cc33dd44').textContent.includes('12 件'),
            middle: step('b0000003').textContent.includes('中略 381 行') }; }""")
        hexrgb = lambda hx: "rgb(%d, %d, %d)" % tuple(int(hx.lstrip("#")[i:i + 2], 16) for i in (0, 2, 4)) if hx.startswith("#") and len(hx) == 7 else hx
        if not (opened["command"] == 40 and opened["output"] == 120 and "380" in opened["rest"]
                and opened["diff"] == 100 and opened["signs"] == ["+", "−"]
                and opened["colors"] == [hexrgb(x) for x in opened["expected"]]
                and opened["kids"] == 5 and opened["omitted"] and opened["middle"]):
            bad(f"R121 開いた詳細／差分の記号の色／子の末尾が違う: {opened}")
        else:
            print("  ✓ R121 詳細を展開して命令40行・出力120行と残数・差分100行・子の末尾を確認")
        o2 = page.evaluate(overflow_js)
        if o1["n"] == 0 and o2["n"] == 0 and o1["body"] <= 1 and o2["body"] <= 1 and o1["page"] <= 0 and o2["page"] <= 0:
            print(f"  ✓ R121 {label}: 作業ビューは既定・展開後とも横はみ出し0")
        else:
            bad(f"R121 {label}: 横へはみ出す要素がある: 既定={o1} 展開後={o2}")

        # ⑧ 差分の再描画でも開いたgroup/stepと本文を保持する。
        count_js = "() => ({ pre: document.querySelectorAll('#termbody .act-step-body pre').length, init: document.querySelectorAll('#termbody .tinit').length })"
        b8 = page.evaluate(count_js)
        n1 = len(sent)
        page.evaluate("() => termRequest()")
        _wait_sent(page, sent, n1 + 1, ms=8000)
        ok1, why1, _s1, text1 = _verify(sent[n1], device_id, secret) if len(sent) > n1 else (False, "no request", "", "")
        act1 = json.loads(text1) if ok1 else {}
        if not (ok1 and act1.get("kind") == "term" and act1.get("after") == {"off": 4096, "h": "0123456789abcdef"}):
            bad(f"⑧ 差分の要求（after＝前の頁の cur）が飛ばない: verify={ok1}({why1}) act={act1}")
        else:
            rid1, now1 = act1.get("reqId", ""), int(time.time())
            delta = dialog_seal.seal(secret, device_id, rid1, cx["session"],
                                     {"kind": "term", "v": 2, "session": cx["session"], "reqId": rid1, "iat": now1,
                                      "rows": [{"k": "a", "t": "差分の頁の行（R113-B-S3）", "ts": now1}],
                                      "cur": {"off": 4200, "h": "fedcba9876543210"}, "reset": False, "gap": False}, iat=now1)
            page.evaluate("(b) => onDlg(JSON.stringify({ v: 1, items: [b] }))", delta)
            page.wait_for_function("() => document.getElementById('termbody').textContent.includes('差分の頁の行（R113-B-S3）')",
                                   timeout=10000)
            page.evaluate("() => { if (TERM && TERM.timer) { clearTimeout(TERM.timer); TERM.timer = null; } }")
            a8 = page.evaluate(count_js)
            if b8["pre"] >= 3 and a8["pre"] == b8["pre"] and b8["init"] == 0 and a8["init"] == 0:
                print(f"  ✓ R113-B-S3 ⑧ 差分の頁（reset:false）を描いても本文の pre ブロック {a8['pre']} 個は残る・初期表示（.tinit）は最初の頁で消えている")
            else:
                bad(f"⑧ 差分の頁で本文の pre ブロックが消えた／初期表示が残った: 前={b8} 後={a8}")

        # R121: 最新の質問カードだけに、従来の署名付き回答の操作を載せる。
        question = dict(cc, state="waiting", listening=True, question="どの案で進めますか?",
                        questionOptions=[{"label": "案A"}, {"label": "案B"}],
                        ask={"tool": "AskUserQuestion", "kind": "question", "id8": "a1b2c3d4", "qn": 1})
        page.evaluate("""e => { closeTerm(true); openTerm(e);
          termPaint({ reset: true, rows: [{ k: 'q', t: '以前の質問', o: ['古い案'] },
            { k: 'q', t: e.question, o: ['案A', '案B'] }] }); }""", question)
        qcards = page.evaluate("""() => { const q = [...document.querySelectorAll('#termbody .act-question')];
          return { n: q.length, actions: q.map(n => n.querySelectorAll('.qactions').length),
            choices: q.at(-1)?.querySelectorAll('button.qopt.quickoption').length }; }""")
        if qcards != {"n": 2, "actions": [0, 1], "choices": 2}:
            bad(f"R121 最新の質問カードだけに回答ボタンが載らない: {qcards}")
        else:
            before_answer = len(sent)
            page.locator("#termbody .act-question .qactions button.qopt.quickoption").nth(1).click()
            answers = []
            for _ in range(80):
                answers = [raw for raw in sent[before_answer:] if json.loads(raw).get("session") == question["session"]]
                if answers:
                    break
                page.wait_for_timeout(100)
            valid, why, target, text = _verify(answers[0], device_id, secret) if answers else (False, "no answer", "", "")
            try:
                answer = json.loads(text)
            except ValueError:
                answer = {}
            if not (valid and target == question["session"] and answer.get("ask") == "a1b2c3d4"
                    and answer.get("text") == "選択肢「案B」でお願いします。"):
                bad(f"R121 作業ビューの質問からの回答が違う: {valid}({why}) {target} {answer}")
            else:
                print("  ✓ R121 最新の質問カードのボタンから既存の署名付き回答を同じ宛先へ送る")

        # ⑥ 会話を見せるが OFF の「最近の動き」の履歴・OpenClaw の説明
        v6 = page.evaluate("""() => { closeTerm(true); const saved = window.canDlg; window.canDlg = () => false;
          try { SEL = officeAgents(LAST_OFFICE).find(e => e.session === 'e2e-r113b-claude'); openLog();
            const off = document.querySelector('#lgbody .lgoff'), r = { log: off ? off.textContent : '', last: !!off && !off.nextElementSibling };
            closeLog(); window.canDlg = saved;
            openTerm(officeAgents(LAST_OFFICE).find(e => e.session === 'oc-r113b'), true);
            r.oc = (document.querySelector('#termbody .toff .tob') || {}).textContent || ''; closeTerm(true); return r;
          } finally { window.canDlg = saved; } }""")
        want = "中身（指示・返答・コマンドの結果・差分）を見るには、Mac の AI Office →「スマホ連携」→「スマホに会話を見せる」を ON に"
        if want in v6["log"] and "いまは要約だけです" in v6["log"] and v6["last"] and "OpenClaw" in v6["oc"] and "Codex" not in v6["oc"]:
            print("  ✓ R113-B ⑥ 会話を見せるが OFF の「最近の動き」の履歴にも端末と同じ理由の文（末尾＝開いた直後に見える）・"
                  "OpenClaw の説明から Codex が消えた")
        else:
            bad(f"⑥ 履歴の理由の文／OpenClaw の説明が違う: {v6}")
        if errors:
            bad(f"page error: {errors[:3]}")
        ctx.close()
    except Exception as exc:  # noqa: BLE001  待ちが切れた等＝赤として数える（後ろのピンを止めない）
        bad(f"途中で例外: {type(exc).__name__}: {str(exc)[:200]}")
    finally:
        browser.close()
    return len(fails)


R128_POISON = '<img src=x onerror="window.__r128_pwned=1">'


def _r128_door_rows():
    """R128 #3: Mac が封じる Bot の記録の行＝本物の口（server/office_door.py が書く記録）を本物の変換（server/source_door_log.py）に
    通した形（手で組んだ行で緑にしない）。一時 HOME だけ（本物の ~/.claude には触らない）。→ (rows, 開いている相談)。"""
    import shutil
    import tempfile
    server = str(Path(__file__).resolve().parents[1] / "server")
    if server not in sys.path:
        sys.path.insert(0, server)
    import office_door
    import source_door_log
    from office_actions import scrub_output
    home = tempfile.mkdtemp(prefix="pwa3d_r128_")
    try:
        t, bot = time.time() - 900, "mailbox"
        assert office_door.add_bot(bot, "郵便受け", "受信箱", home=home)["ok"]
        assert office_door.checkin(bot, "依頼を読みました" + R128_POISON, home=home, now=t)["ok"]
        assert office_door.report(bot, "請求書を 3 通確認" + R128_POISON, "送り先は 3 社", kind="warn", home=home, now=t + 60)["ok"]
        a = office_door.ask(bot, "先週の分も送る？", ["送る", "あとで"], home=home, now=t + 70)
        assert office_door.answer(bot, a["askId"], "選択肢「あとで」でお願いします。", src="relay", home=home, now=t + 300)["ok"]
        assert office_door.instruct(bot, "棚も見て" + R128_POISON, src="relay", home=home, now=t + 360)["ok"]
        a2 = office_door.ask(bot, "次の確認は？", home=home, now=t + 420)
        assert office_door.withdraw(bot, a2["askId"], home=home, now=t + 480)["ok"]
        question, options = "請求書を送ってよい？" + R128_POISON, ["送る<i>今</i>", "明日にする"]
        a3 = office_door.ask(bot, question, options, home=home, now=t + 540)
        assert a3["ok"], a3
        page = source_door_log.term_page("bot-" + bot, None, scrub_output, home=home)
        return page["rows"], {"question": question, "options": options, "askId": a3["askId"]}
    finally:
        shutil.rmtree(home, ignore_errors=True)


def _r128_pins(p, base_url, device_id, secret, token):
    """R128 #3（Bot の記録の画面・正本= docs/door.md「記録の画面」）: 本番の app.js を 393×793 で踏む。
      ① 一覧の Bot の行 → 記録の画面（シートではない）→ kind:"term"・session "bot-…"・after null の封書の要求（署名は本物の端末秘密）
      ② Mac が封じた形（本物の口の記録を本物の変換に通した行）を onDlg へ → 作業ビュー: 出勤・報告の札（注意）と題・相談のカード・
         答えの札（選択肢 2）・指示の札・取り下げの 1 行・日付つきの時刻・考え中なし・Bot と人の文は文字のまま・393 幅で見切れ 0
      ③ 開いている相談のカードに答えの操作（選択肢）・セッション向けの 3 本は無い → 選ぶと id8 つきの答え（署名つき・R127 の形）
      ④ 案内（送った言葉の届き方・入力欄）・最下行に office_ask を出さない・記録が見つからない（古い Mac）の文
      ⑤ シートの入口は「記録を見る」（セッション・会話・起動・定型の入口なし）・「会話を見せる」が OFF なら Bot の記録の言葉で理由
      ⑥ 作業ビューが読めない旧い描き方でも記録の 1 行と答え／指示の印（文字のまま）"""
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "server"))
    import dialog_seal  # noqa: E402
    if not dialog_seal.AVAILABLE:
        print("  · R128: 封緘バックエンドが無い環境＝Bot の記録の画面のピンは省略")
        return 0
    fails = []

    def bad(msg):
        fails.append(msg)
        print(f"  ✗ R128 {msg}")

    rows, opened = _r128_door_rows()
    id8 = opened["askId"][:8]
    bot_q = {"session": "bot-mailbox", "vendor": "bot", "dept": "郵便受け", "disp": "郵便受け", "role": "受信箱",
             "state": "waiting", "kind": "said", "verb": "相談の答え待ち", "age": 30, "pending": True, "listening": True,
             "approvalMin": 0, "question": opened["question"],
             "questionOptions": [{"label": o, "desc": ""} for o in opened["options"]],
             "ask": {"tool": "office_ask", "kind": "question", "remote": 0, "min": 1, "id8": id8}}
    bot_rest = {"session": "bot-nippou", "vendor": "bot", "dept": "日報の係", "disp": "日報の係", "role": "夕方",
                "state": "resting", "kind": "idle", "verb": "休憩中", "age": 3600, "pending": False, "listening": True,
                "approvalMin": 0, "question": ""}
    office = {"officeName": "E2E R128", "avatarMode": "session", "employees": [bot_q, bot_rest], "actions": {"caps": {"dialog": 1}}}
    status = json.dumps({"ok": True, "ts": int(time.time() * 1000), "agentSeenAgo": 0, "agentOnline": True,
                         "json": json.dumps(office, ensure_ascii=False)}, ensure_ascii=False)
    sent = []

    def on_instruct(route, request):
        if request.method != "POST":
            route.continue_()
            return
        sent.append(request.post_data or "")
        route.fulfill(status=200, content_type="application/json", body='{"ok":true}')

    def on_status(route, request):
        if request.method == "GET":
            route.fulfill(status=200, content_type="application/json", body=status)
        else:
            route.continue_()

    cred = json.dumps({"d": device_id, "s": secret, "t": token, "e": 0}, separators=(",", ":"))
    overflow_js = """() => { const b = document.getElementById('termbody');
      const wide = [...b.querySelectorAll('*')].filter(n => getComputedStyle(n).display !== 'inline' && n.scrollWidth > n.clientWidth + 1)
        .map(n => (n.className || n.tagName) + ' ' + n.scrollWidth + '>' + n.clientWidth);
      return { wide: wide.slice(0, 6), n: wide.length, body: b.scrollWidth - b.clientWidth, page: document.documentElement.scrollWidth - innerWidth }; }"""
    browser = p.chromium.launch()
    try:
        ctx = browser.new_context(viewport={"width": 393, "height": 793}, has_touch=True, is_mobile=True, device_scale_factor=3,
                                  service_workers="block")
        page = ctx.new_page()
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.on("dialog", lambda d: d.accept())
        page.add_init_script("localStorage.setItem('aioffice.cred', " + json.dumps(cred) + ");"
                             "window.WebSocket = function () { throw new Error('r128: WS off'); };")
        page.route("**/instruct", on_instruct)
        page.route("**/status", on_status)
        page.route("**/diag", lambda route: route.fulfill(status=204, body=""))
        page.goto(base_url.rstrip("/") + "/app/", wait_until="domcontentloaded")
        page.wait_for_function("() => typeof openTerm === 'function' && window.__activity && officeAgents(LAST_OFFICE).length === 2 && canDlg()",
                               timeout=30000)
        # ① 一覧の Bot の行 → 記録の画面（端末の画面）＋封書の要求
        n0 = len(sent)
        v1 = page.evaluate("""() => { setView('list', true);
          const row = document.querySelector('#list .recentrow[data-sess="bot-mailbox"]'); if (row) row.click();
          // 届き方の 1 行（termnote）は要求中は「Mac に要求中…」になる＝頁が届いた後に出す文（termStateLine）を見る
          return { row: !!row, term: !!TERM && TERM.session === 'bot-mailbox', sheet: document.getElementById('sheetwrap').classList.contains('open'),
                   say: document.getElementById('termsay').placeholder, note: TERM ? termStateLine(TERM.e) : '',
                   live: document.getElementById('termlive').textContent }; }""")
        _wait_sent(page, sent, n0 + 1, ms=8000)
        ok, why, sess, text = _verify(sent[n0], device_id, secret) if len(sent) > n0 else (False, "no request", "", "")
        act = json.loads(text) if ok else {}
        rid = act.get("reqId", "")
        if not (v1["row"] and v1["term"] and not v1["sheet"]):
            bad(f"① 一覧の Bot の行が記録の画面を開かない（R127 まではシート）: {v1}")
        if not (ok and sess == f"dlg-{rid}" and act.get("kind") == "term" and act.get("session") == "bot-mailbox" and act.get("after") is None):
            bad(f"① 記録を開いても kind:term の封書の要求が飛ばない: verify={ok}({why}) session={sess} act={act}")
            raise RuntimeError("要求が無い＝以降の描画を検査できない")
        if not (v1["say"] == "相談への答えを書く" and v1["note"] == "送った言葉が、この相談への答えになります" and "office_ask" not in v1["live"]):
            bad(f"④ 相談中の Bot の入力欄・届き方・最下行の案内が違う: {v1}")
        # ② Mac が封じた記録の頁を開いて描く
        now = int(time.time())
        bundle = dialog_seal.seal(secret, device_id, rid, "bot-mailbox",
                                  {"kind": "term", "v": 2, "session": "bot-mailbox", "reqId": rid, "iat": now, "rows": rows,
                                   "cur": {"off": 4096, "h": "0123456789abcdef"}, "reset": True, "gap": False, "beg": None, "top": True},
                                  iat=now)
        page.evaluate("(b) => onDlg(JSON.stringify({ v: 1, items: [b] }))", bundle)
        page.wait_for_function("() => document.querySelectorAll('#termbody .act-event').length > 0", timeout=10000)
        page.evaluate("() => { if (TERM && TERM.timer) { clearTimeout(TERM.timer); TERM.timer = null; } }")
        v2 = page.evaluate("""(poison) => { const b = document.getElementById('termbody');
          const txt = (sel) => [...b.querySelectorAll(sel)].map(n => n.textContent);
          return { events: txt('.act-event'), kinds: txt('.act-report-kind'), titles: txt('.act-report-title'), said: txt('.act-said'),
            q: [...b.querySelectorAll('.act-question')].map(n => n.getAttribute('data-qtext')), times: txt('.act-time'),
            live: b.querySelectorAll('.act-live').length, imgs: b.querySelectorAll('img').length,
            poison: b.textContent.split(poison).length - 1, pwned: window.__r128_pwned === 1 }; }""", R128_POISON)
        want_events = ["出勤 依頼を読みました" + R128_POISON, "Bot が相談を取り下げました"]
        if not (v2["events"] == want_events and v2["kinds"] == ["注意"] and v2["titles"] == ["請求書を 3 通確認" + R128_POISON]
                and v2["said"] == ["相談への答え（選択肢 2）", "指示"]
                and v2["q"] == ["先週の分も送る？", "次の確認は？", opened["question"]]
                and v2["times"] and "/" in v2["times"][0] and v2["live"] == 0):
            bad(f"② 記録の行（出勤・報告の札と題・相談・答えと指示の札・取り下げ・日付つきの時刻・考え中なし）が違う: {v2}")
        else:
            print("  ✓ R128 ② 記録の画面: 出勤・報告の札（注意）と題・相談のカード・答えの札（選択肢 2）・指示の札・取り下げ・日付つきの時刻・考え中なし")
        if not (v2["imgs"] == 0 and not v2["pwned"] and v2["poison"] >= 4):
            bad(f"② Bot と人の文が HTML として描かれた（文字のままでない）: {v2}")
        o = page.evaluate(overflow_js)
        if o["n"] or o["body"] > 0 or o["page"] > 0:
            bad(f"② 393 幅で記録の画面が横へはみ出す: {o}")
        # ③ 開いている相談のカードに答えの操作 → 選ぶと id8 つきの答え（署名つき）
        v3 = page.evaluate("""() => { const card = [...document.querySelectorAll('#termbody .act-question')].at(-1);
          return { actions: card ? card.querySelectorAll('.qactions').length : 0,
            opts: card ? [...card.querySelectorAll('button.qopt.quickoption')].map(b => b.textContent) : [],
            quick: [...document.querySelectorAll('#termbody button')].map(b => b.textContent).filter(t => /承認して進める|いったん停止|進捗を/.test(t)) }; }""")
        if not (v3["actions"] == 1 and v3["opts"] == opened["options"] and not v3["quick"]):
            bad(f"③ 相談のカードに選択肢の答えが無い／セッション向けの定型が出た: {v3}")
        n1 = len(sent)
        page.locator("#termbody .act-question .qactions button.qopt.quickoption").nth(1).click()
        answers = []
        for _ in range(80):
            answers = [raw for raw in sent[n1:] if json.loads(raw).get("session") == "bot-mailbox"]
            if answers:
                break
            page.wait_for_timeout(100)
        ok, why, target, text = _verify(answers[0], device_id, secret) if answers else (False, "no answer", "", "")
        try:
            body = json.loads(text) if ok else {}
        except ValueError:
            body = {}
        if body != {"aioffice": 1, "kind": "answer", "ask": id8, "text": "選択肢「明日にする」でお願いします。"}:
            bad(f"③ 記録の画面の選択肢から id8 つきの答えが飛ばない: verify={ok}({why}) target={target} body={body or text}")
        else:
            print("  ✓ R128 ③ 記録の画面の相談のカードから選ぶと id8 つきの答え（署名つき）・セッション向けの定型は無い")
        # ④ 記録が見つからない（古い Mac は bot- に notfound）
        nf = page.evaluate("""() => { TERM.req = 'r128nf'; onTermItem({ id: 'r128nf', err: 'notfound' }, { session: 'bot-mailbox' }, null, 0);
          if (TERM.timer) { clearTimeout(TERM.timer); TERM.timer = null; } return document.getElementById('termnote').textContent; }""")
        if "この Bot の記録が見つかりません" not in nf:
            bad(f"④ 記録が見つからないときの文が Bot の言葉でない: {nf!r}")
        # ⑤ シートの入口は「記録を見る」・「会話を見せる」が OFF の理由
        v5 = page.evaluate("""async () => { const wait = (ms) => new Promise((r) => setTimeout(r, ms));
          closeTerm(true); openSheet(sessionAgent('bot-nippou')); await wait(300);
          const sheet = document.getElementById('sheetwrap');
          const labels = [...sheet.querySelectorAll('button')].filter(b => b.getClientRects().length).map(b => b.textContent.trim());
          const rec = document.getElementById('sh_term'), out = { labels, rec: rec ? rec.textContent : '' };
          out.send = document.getElementById('sh_send').textContent;
          openSheet(sessionAgent('bot-mailbox')); await wait(300); out.sendQ = document.getElementById('sh_send').textContent;
          openSheet(sessionAgent('bot-nippou')); await wait(300);
          const rec2 = document.getElementById('sh_term'); if (rec2) rec2.click(); await wait(300);
          out.to = (document.getElementById('termto') || {}).textContent || '';
          out.term = !!TERM && TERM.session === 'bot-nippou'; out.sheet = sheet.classList.contains('open');
          out.say = document.getElementById('termsay').placeholder; out.note = TERM ? termStateLine(TERM.e) : '';
          const saved = window.canDlg; window.canDlg = () => false;
          try { closeTerm(true); openTerm(sessionAgent('bot-nippou')); out.off = (document.querySelector('#termbody .toff') || {}).textContent || ''; }
          finally { window.canDlg = saved; closeTerm(true); }
          return out; }""")
        banned = ("セッションを開く", "会話を見る", "このプロジェクトを起動", "いったん停止して報告", "進捗を1行で報告", "承認して進める")
        if not (v5["rec"] == "記録を見る" and not any(w in lb for lb in v5["labels"] for w in banned) and v5["term"] and not v5["sheet"]
                and v5["say"] == "この Bot への言葉（次に出勤したときに読みます）" and v5["note"] == "Bot が次に出勤したときに読みます"
                and "Bot の記録（報告・相談・答え・指示）を見るには" in v5["off"]
                # 別モデルレビューの回の直し: Bot を「セッション」と呼ばない（宛先の行）・相談への答えを「指示」と呼ばない（送るボタン）
                and v5["send"] == "この言葉を送る" and v5["sendQ"] == "この答えを送る"
                and v5["to"].startswith("宛先: ") and "セッション" not in v5["to"]):
            bad(f"⑤ シートの「記録を見る」・休憩中の Bot の案内・会話を見せるが OFF の理由・送るボタンと宛先の言葉が違う: {v5}")
        else:
            print("  ✓ R128 ⑤ シートの入口は「記録を見る」（セッション・会話・起動・定型なし）・休憩中の Bot の案内・OFF の理由は Bot の記録の言葉・送るボタンは「この言葉／この答えを送る」・宛先に「セッション」と書かない")
        # ⑥ 作業ビューが読めない旧い描き方でも記録の 1 行と答え／指示の印
        v6 = page.evaluate("""(rows) => { const saved = { act: window.__activity, req: window.termRequest };
          window.__activity = null; window.termRequest = () => {};
          try { closeTerm(true); openTerm(sessionAgent('bot-mailbox')); termPaint({ reset: true, rows });
            const b = document.getElementById('termbody');
            return { text: b.textContent, imgs: b.querySelectorAll('img').length, pwned: window.__r128_pwned === 1 }; }
          finally { closeTerm(true); window.__activity = saved.act; window.termRequest = saved.req; } }""", rows)
        if not ("出勤 · 依頼を読みました" in v6["text"] and "Bot が相談を取り下げました" in v6["text"] and "（相談への答え）" in v6["text"]
                and "（指示）" in v6["text"] and v6["imgs"] == 0 and not v6["pwned"]):
            bad(f"⑥ 旧い描き方に記録の 1 行・答え／指示の印が無い（または HTML になった）: {v6['text'][:300]} imgs={v6['imgs']}")
        if errors:
            bad(f"page error: {errors[:3]}")
        if not fails:
            print("  ✓ R128 ①④⑥ 一覧の行は記録を開く（kind:term の封書の要求）・案内は Bot の言葉・見つからない文・旧い描き方にも記録の行")
        ctx.close()
    except Exception as exc:  # noqa: BLE001  待ちが切れた等＝赤として数える（後ろのピンを止めない）
        bad(f"途中で例外: {type(exc).__name__}: {str(exc)[:200]}")
    finally:
        browser.close()
    return len(fails)


class _StaleProxy:
    """R102-10 の検査用: 中継（wrangler dev）の前に立つ本物の HTTP サーバー。stale の間は /ui/core/world.js・/ui/platform/clock.js に
    8 月の Worker と同じ形（immutable・ETag 無し）で古い写しを返す。broken の間は今の版の道の world.js を、要る名前の無い写しにする（no-store）。
    /seed.html は 8 月の頃のように /ui/... を読み（古い写しを手元に置く）、/probe.html は /ui/core/world.js が手元の古い写しのままかを答える。
    ほかは全部そのまま中継へ回す。Playwright の route の差し替え応答は持続プロファイルのディスクキャッシュに載らない（実測）＝本物の HTTP で作る。"""
    STALE = ("/ui/core/world.js", "/ui/platform/clock.js")
    PAGES = {
        "/seed.html": '<!doctype html><script type=module>import "/ui/core/world.js"; import "/ui/platform/clock.js"; '
                      'window.SEEDED = 1;</script>',
        "/probe.html": '<!doctype html><script type=module>import * as m from "/ui/core/world.js"; '
                       'window.PROBE = typeof m.vendorOf;</script>',
    }

    def __init__(self, upstream):
        import http.server
        import threading
        self.upstream = upstream.rstrip("/")
        self.stale = True
        self.broken = False
        self.hits = []
        self.seen = []
        outer = self

        class H(http.server.BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *a):
                pass

            def _send(self, code, body, headers):
                self.send_response(code)
                for k, v in headers.items():
                    self.send_header(k, v)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                if self.command != "HEAD":
                    self.wfile.write(body)

            def _relay(self):
                path = self.path.split("?")[0]
                outer.seen.append(path)
                if self.command == "GET" and path in outer.PAGES:
                    return self._send(200, outer.PAGES[path].encode(), {"Content-Type": "text/html", "Cache-Control": "no-store"})
                if outer.stale and self.command == "GET" and path in outer.STALE:
                    outer.hits.append(path)
                    return self._send(200, b"export const stale = 1;\n", {"Content-Type": "text/javascript; charset=utf-8",
                                                                             "Cache-Control": "public, max-age=31536000, immutable"})
                if outer.broken and self.command == "GET" and path.startswith("/u/") and path.endswith("/core/world.js"):
                    return self._send(200, b"export function buildWorld() { return {}; }\n",
                                      {"Content-Type": "text/javascript; charset=utf-8", "Cache-Control": "no-store"})
                n = int(self.headers.get("Content-Length") or 0)
                data = self.rfile.read(n) if n else None
                hdr = {k: v for k, v in self.headers.items() if k.lower() not in ("host", "accept-encoding", "connection", "content-length")}
                req = urllib.request.Request(outer.upstream + self.path, data=data, method=self.command, headers=hdr)
                try:
                    r = urllib.request.urlopen(req, timeout=30)
                    code, rh, body = r.status, r.headers, r.read()
                except urllib.error.HTTPError as e:
                    code, rh, body = e.code, e.headers, e.read()
                except Exception:
                    code, rh, body = 502, {}, b"proxy"
                keep = {k: v for k, v in (rh.items() if hasattr(rh, "items") else [])
                        if k.lower() not in ("transfer-encoding", "connection", "content-length", "content-encoding")}
                self._send(code, body, keep)
            do_GET = do_POST = do_PUT = do_DELETE = do_HEAD = _relay

        self.httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.base = f"http://127.0.0.1:{self.httpd.server_address[1]}"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


def _stale_cache_pins(p, base_url, device_id, secret, token):
    """R102-10（2026-09-30〜10-01・本人の iPhone だけで「この端末では部屋を表示できません」）: 本人の端末は 8 月（R77〜R79-0）の
    1 年キャッシュ（immutable）の頃の /ui/core/world.js・/ui/platform/clock.js を手元から使い続け、新しい部品と結合できなかった
    （実機のログ「Importing binding name 'vendorOf' is not found.」・cache:"reload" で取り直しても置き換わらなかった）。
    部屋は版つきの道 /u/<BUILD>/ で読む。Chromium と WebKit（iPhone の中身）の持続プロファイル（ディスクキャッシュあり）で:
    ①8 月の頃のように /ui/... の古い写しを immutable で手元に置く ②手元の写しが本当に古いまま（再現できている）
    ③今の版の world.js が壊れていたら起動せず、足りない名前を画面と /diag に出す（WebKit は filename 空で報告する）
    ④アプリは版の無い /ui/*.js を 1 本も取りに行かず、部屋が起動する ⑤開き直しても起動する。"""
    import re as _re
    import tempfile as _tf
    ng = 0
    cred = json.dumps({"d": device_id, "s": secret, "t": token, "e": 0}, separators=(",", ":"))
    # 中継が実際に返す Cache-Control: 今の版の道だけ immutable・古い版の道と版の無い /ui/ は毎回検証・閉包の外は 404
    def head(path):
        rq = urllib.request.Request(base_url.rstrip("/") + path, headers={"User-Agent": "Mozilla/5.0"})
        try:
            r = urllib.request.urlopen(rq, timeout=15)
            return r.status, r.headers.get("Cache-Control") or ""
        except urllib.error.HTTPError as e:
            return e.code, e.headers.get("Cache-Control") or ""
    html = urllib.request.urlopen(urllib.request.Request(base_url.rstrip("/") + "/app/", headers={"User-Agent": "Mozilla/5.0"})).read().decode("utf-8")
    m = _re.search(r'<script type=application/json id=uiparts>(\{.*?\})</script>', html)
    build = json.loads(m.group(1)).get("b", "") if m else ""
    other = "000000000000" if build != "000000000000" else "111111111111"
    got = {"now": head(f"/u/{build}/core/world.js"), "old": head(f"/u/{other}/core/world.js"), "bare": head("/ui/core/world.js"),
           "miss": head(f"/u/{build}/core/nope.js"), "short": head("/u/abc/core/world.js")}
    if (_re.fullmatch(r"[0-9a-f]{12}", build) and got["now"] == (200, "public, max-age=31536000, immutable")
            and got["old"] == (200, "public, max-age=0, must-revalidate") and got["bare"] == (200, "public, max-age=0, must-revalidate")
            and got["miss"][0] == 404 and got["short"][0] == 404):
        print(f"  ✓ R102-10 中継の Cache-Control: 今の版の道 /u/{build}/ だけ immutable・古い版と版の無い /ui/ は毎回検証・閉包の外は 404")
    else:
        print(f"  ✗ R102-10 中継の Cache-Control が違う: 版={build!r} {got}")
        ng += 1
    for engine in ("chromium", "webkit"):
        proxy = _StaleProxy(base_url)
        app = proxy.base + "/app/"
        prof = _tf.TemporaryDirectory(prefix="pwa-stale-")
        dev = dict(p.devices["iPhone 13"])
        dev.pop("default_browser_type", None)
        ctx = getattr(p, engine).launch_persistent_context(prof.name, **dev)
        try:
            page = ctx.pages[0] if ctx.pages else ctx.new_page()
            diag = []
            page.on("request", lambda rq: diag.append(rq.post_data or "") if rq.url.endswith("/diag") and rq.method == "POST" else None)
            page.add_init_script("localStorage.setItem('aioffice.officeMode','pixel');")
            page.add_init_script("localStorage.setItem('aioffice.cred', " + json.dumps(cred) + ");")

            def load(url):
                page.goto(url, wait_until="domcontentloaded")
                try:
                    page.wait_for_function("() => typeof SCENE3D !== 'undefined' && SCENE3D !== 'pending'", polling=200, timeout=25000)
                except Exception:
                    pass
                return page.evaluate("() => typeof SCENE3D === 'undefined' ? '' : SCENE3D")

            def probe(url, expr):
                page.goto(url, wait_until="domcontentloaded")
                try:
                    page.wait_for_function(f"() => {expr} !== undefined", polling=200, timeout=10000)
                except Exception:
                    pass
                return page.evaluate(f"() => {expr}")
            probe(proxy.base + "/seed.html", "window.SEEDED")          # ① 8 月の頃の古い写しを immutable で手元に置く
            proxy.stale = False
            seeded = len(proxy.hits)
            kept = probe(proxy.base + "/probe.html", "window.PROBE")   # ② 中継は新しい本文を返すのに、手元は古いまま（'undefined'）
            proxy.broken = True
            proxy.seen.clear()
            diag.clear()
            c = load(app)                                               # ③ 今の版の world.js が壊れている → 理由を出して止まる
            page.evaluate("() => setView('office', true)")
            # 結合の失敗は <script> の error にならない＝止まるのは 4 秒の見張り。重い機械では理由が見張りの後に届く
            # （app.js は後から届いた理由で表示を差し替え、/diag を 1 回だけ送り直す＝R102-8）＝理由が出るまで待つ
            try:
                page.wait_for_function("() => /vendorOf|needsAttention|zoneOf/.test(roomErrText())",
                                       polling=200, timeout=15000)
            except Exception:
                pass
            page.wait_for_timeout(300)
            why = page.evaluate("() => roomErrText()")
            named = any(n in why for n in ("vendorOf", "needsAttention", "zoneOf"))
            proxy.broken = False
            d = load(app)                                               # ④ 版つきの道だけで読んで起動する（③④ を通して数える＝
            #                                                             今の版の道は immutable＝④ では手元から読まれて要求に出ない）
            bare = sorted({x for x in proxy.seen if x.startswith("/ui/") and x.endswith(".js")})
            versioned = [x for x in proxy.seen if _re.match(r"^/u/[0-9a-f]{12}/", x)]
            e = load(app)                                               # ⑤ 開き直しても起動する
            if (seeded == 2 and kept == "undefined" and c == "failed" and named and any('"err":{' in x for x in diag)
                    and d == "ready" and not bare and len(versioned) >= 10 and e == "ready"):
                print(f"  ✓ R102-10 {engine}: 8 月の immutable の古い写しを手元に置いても、部屋は版つきの道（{len(versioned)} 本）で起動する"
                      f"・壊れた部品の理由も出る（{why[:60]}）")
            else:
                print(f"  ✗ R102-10 {engine}: 仕込み={seeded} 手元の写し={kept!r}（'undefined' が再現） 壊れた版={c} 理由={why!r} 送信={diag[:1]} "
                      f"起動={d} 版の無い要求={bare} 版つき={len(versioned)} 開き直し={e}")
                ng += 1
        finally:
            ctx.close()
            prof.cleanup()
            proxy.close()
    return ng


def _r116s1b_rekey_pins(p, base_url, device_id, secret, token):
    """R116-S1b つながりの延長（本人裁定 2026-10-26「延長の仕組みを作る」）。Mac と同じ verify_envelope で封筒を検算する。
    ①office の actions.approve.rk に自分の端末があれば、今の秘密で署名した rky- の求め（step req）が出る
    ②Mac が今の秘密で封じた新しい秘密を開き、新しい秘密で署名した受領（step ack）が出る・cred は新しい秘密と期限へ（古い秘密は sp）
    ③受領待ち（ka）は Mac の「済み」の封書（新しい秘密で封じた {done:1}）を開けたときだけ終える（別モデルレビュー 3 巡目＝状態や時刻から推さない）:
      それまでは Mac が最近見えているとき（HTTP でも agentSeenAgo が新しい）だけ受領を送り直し、眠った Mac には送らない・受領待ちの間は spu が
      切れていても sp で古い封書を開ける・別の鍵は開けない・「済み」で ka を終え spu は 2 分後（古い秘密の窓を狭く）・期限は「済み」の e へ
    ④前回の受領待ちが残ったまま次の［延長］が来ても、送り直した受領への「済み」（rk=1）で終えてすぐ新しい求めに進む
    ⑤スマホの時計が 45 秒進んでいても終える ⑥rk に他の端末しか無いときは求めない ⑦中身が壊れた封書では cred を替えず受領も出さない。"""
    import secrets as _sec
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "server"))
    import dialog_seal  # noqa: E402
    if not dialog_seal.AVAILABLE:
        print("  ⚠ R116-S1b 延長: 封緘バックエンドが無い環境＝省略")
        return 0
    ng = 0
    now = int(time.time())
    cred0 = {"d": device_id, "s": secret, "t": token, "e": now + 2 * 86400}
    cred_js = "() => JSON.parse(localStorage.getItem('aioffice.cred') || 'null')"
    short = lambda c: {k: (v[:6] + "…" if isinstance(v, str) and len(v) == 64 else v) for k, v in (c or {}).items()}

    def status_for(st):
        office = {"officeName": "E2E 延長", "employees": [],
                  "actions": {"caps": {"approve": 0}, "approve": {"on": 0, "until": 0, "fps": [], "enr": [], "rk": st["rk"]}}}
        d = {"ok": True, "ts": int(time.time() * 1000), "agentSeenAgo": st.get("ago", 0), "agentOnline": bool(st.get("online", True)),
             "json": json.dumps(office, ensure_ascii=False)}
        if st.get("items"):
            d["dlg"] = json.dumps({"v": 1, "items": st["items"]})
        return json.dumps(d, ensure_ascii=False)

    def open_page(browser, state, sent, skew_ms=0):
        ctx = browser.new_context(viewport={"width": 390, "height": 844}, has_touch=True, is_mobile=True,
                                  service_workers="block")
        page = ctx.new_page()
        page.add_init_script("localStorage.setItem('aioffice.cred', " + json.dumps(json.dumps(cred0, separators=(",", ":")))
                             + ");window.WebSocket = function () { throw new Error('rekey: WS off'); };"
                             + (f"(function () {{ const n = Date.now.bind(Date); Date.now = () => n() + {int(skew_ms)}; }})();"
                                if skew_ms else ""))
        page.route("**/status", lambda r: r.fulfill(status=200, content_type="application/json", body=status_for(state))
                   if r.request.method == "GET" else r.continue_())
        page.route("**/instruct", lambda r: (sent.append(r.request.post_data),
                                             r.fulfill(status=200, content_type="application/json", body='{"ok":true}')))
        page.goto(base_url.rstrip("/") + "/app/", wait_until="domcontentloaded")
        page.wait_for_function("() => typeof rekeyOnStatus === 'function' && typeof openSealed === 'function'", timeout=30000)
        return ctx, page

    def steps(sent, key, step):
        out = []
        for body in list(sent):
            ok, _r, sess, text = _verify(body, device_id, key)
            if ok and sess.startswith("rky-"):
                try:
                    t = json.loads(text)
                except ValueError:
                    continue
                if t.get("kind") == "rekey" and t.get("reqId") == sess[4:] and t.get("step") == step:
                    out.append(sess[4:])
        return out

    def wait_for(page, fn, sec=25):
        end = time.time() + sec
        while time.time() < end:
            v = fn()
            if v:
                return v
            page.wait_for_timeout(300)
        return None

    def done_for(key, rid, e, rk=0):
        return dialog_seal.seal(key, device_id, rid, "pair-rekey", {"done": 1, "e": e, "rk": rk})

    browser = p.chromium.launch()
    try:
        # ①〜④ 本流
        new_s, new_e = _sec.token_hex(32), now + 30 * 86400
        state, sent = {"rk": [device_id], "items": None}, []
        ctx, page = open_page(browser, state, sent)
        req = wait_for(page, lambda: steps(sent, secret, "req"))
        fails = []
        if not req:
            fails.append(f"rk に自分の端末があるのに今の秘密の求め（rky- req）が出ない（/instruct {len(sent)} 通）")
        else:
            state["items"] = [dialog_seal.seal(secret, device_id, req[0], "pair-rekey", {"s": new_s, "e": new_e})]
            acks = wait_for(page, lambda: steps(sent, new_s, "ack"))
            page.wait_for_timeout(1500)
            c = page.evaluate(cred_js)
            if not acks:
                fails.append("新しい秘密を受け取ったのに、新しい秘密で署名した受領（rky- ack）が出ない")
            elif steps(sent, secret, "ack"):
                fails.append("受領を今の秘密で署名した（新しい秘密を持つ証明にならない）")
            elif not c or c.get("s") != new_s or c.get("e") != new_e or c.get("sp") != secret or not c.get("ka"):
                fails.append(f"cred が新しい秘密と期限へ替わっていない・古い秘密が sp に無い・受領待ち（ka）が無い: {short(c)}")
            else:
                # 「済み」が届くまで ka は消えない（中継が預かっても・状態が何度届いても）
                page.evaluate("() => { poll(); }")
                page.wait_for_timeout(2000)
                if not (page.evaluate(cred_js) or {}).get("ka"):
                    fails.append("「済み」の封書を受け取る前に受領待ち（ka）を終えた（Mac が昇格したとは限らない）")
                # HTTP でつながっている Mac（agentOnline は false でも agentSeenAgo が新しい）には送り直す
                state["online"], state["ago"] = False, 5
                n1 = len(steps(sent, new_s, "ack"))
                page.evaluate("() => { REKEY.ackAt = 0; poll(); }")
                if not wait_for(page, lambda: len(steps(sent, new_s, "ack")) > n1, 10):
                    fails.append("HTTP でつながっている Mac（agentSeenAgo が新しい）に受領を送り直さない")
                # 眠った Mac（agentSeenAgo が古い）には送らない
                state["ago"] = 600
                n2 = len(steps(sent, new_s, "ack"))
                page.evaluate("() => { REKEY.ackAt = 0; poll(); }")
                page.wait_for_timeout(3000)
                if len(steps(sent, new_s, "ack")) != n2:
                    fails.append("眠った Mac（agentSeenAgo が古い）にも受領を送り直した")
                state["online"], state["ago"] = True, 0
                # 受領待ちの間は spu が切れていても sp で古い封書を開ける・別の鍵は開けない
                page.evaluate("""() => { const c = JSON.parse(localStorage.getItem('aioffice.cred'));
                    c.spu = Math.floor(Date.now() / 1000) - 10; localStorage.setItem('aioffice.cred', JSON.stringify(c)); }""")
                probe = dialog_seal.seal(secret, device_id, _sec.token_hex(16), "pair-rekey", {"probe": 1})
                other = dialog_seal.seal(_sec.token_hex(32), device_id, _sec.token_hex(16), "pair-rekey", {"probe": 2})
                got = page.evaluate("""async ([a, b]) => { await ensureDlg(); const c = getCred(), n = Math.floor(Date.now() / 1000);
                    const one = await openSealed(a, c, 'pair-rekey', n).then(x => x && x.probe, e => 'err:' + e.message);
                    const two = await openSealed(b, c, 'pair-rekey', n).then(x => 'opened', e => 'err');
                    return { one, two }; }""", [probe, other])
                if got.get("one") != 1 or got.get("two") != "err":
                    fails.append(f"受領待ちの間に古い秘密の封書を開けない（spu 切れ）／別の鍵の封書を開けた: {got}")
                # 別モデルレビュー 4 巡目: 古い秘密（sp）で封じた偽の「済み」では ka を終えない（今の秘密だけで開く）
                last = steps(sent, new_s, "ack")[-1]
                state["items"] = [done_for(secret, last, new_e + 999)]
                page.evaluate("() => { poll(); }")
                page.wait_for_timeout(3000)
                if not (page.evaluate(cred_js) or {}).get("ka"):
                    fails.append("古い秘密（sp）で封じた偽の「済み」で受領待ちを終えた（古い QR の秘密を持つ者が差し込める）")
                # Mac の「済み」（新しい秘密で封じた・受領の reqId に縛る）で ka を終える
                last = steps(sent, new_s, "ack")[-1]
                state["rk"], state["items"] = [], [done_for(new_s, last, new_e + 100)]
                page.evaluate("() => { poll(); }")
                fin = wait_for(page, lambda: (lambda c: c if c and not c.get("ka") else None)(page.evaluate(cred_js)), 15)
                if not fin or fin.get("e") != new_e + 100 or not (int(time.time()) + 60 < int(fin.get("spu") or 0) <= int(time.time()) + 180):
                    fails.append(f"「済み」の封書で受領待ちを終えない・期限が「済み」の e でない・spu が 2 分後でない（古い秘密の窓を狭く）: {short(fin)}")
                else:
                    # ④ 前回の受領待ちが残ったまま次の［延長］: 送り直した受領への「済み」（rk=1）で終え、すぐ新しい求めへ
                    page.evaluate("""() => { const c = JSON.parse(localStorage.getItem('aioffice.cred')); c.ka = 1;
                        localStorage.setItem('aioffice.cred', JSON.stringify(c)); REKEY.ackAt = 0; REKEY.done = 0; REKEY.req = null; }""")
                    state["rk"], state["items"] = [device_id], None
                    n3 = len(steps(sent, new_s, "ack"))
                    page.evaluate("() => { poll(); }")
                    more = wait_for(page, lambda: steps(sent, new_s, "ack")[n3:], 10)
                    if not more:
                        fails.append("前回の受領待ちが残ったまま次の［延長］が来ても受領を送り直さない")
                    else:
                        n_req = len(steps(sent, new_s, "req"))
                        state["items"] = [done_for(new_s, more[-1], new_e + 100, rk=1)]
                        page.evaluate("() => { poll(); }")
                        nxt_req = wait_for(page, lambda: len(steps(sent, new_s, "req")) > n_req, 15)
                        if not nxt_req or (page.evaluate(cred_js) or {}).get("ka"):
                            fails.append("前回の受領待ちの「済み」（rk=1）の後に、新しい窓の求めへ進まない（行き止まり）")
                        else:
                            # 別モデルレビュー 4 巡目: 進行中の求めに、古い秘密（sp）で封じた偽の新しい秘密を差し込んでも切り替わらない
                            rid_n = steps(sent, new_s, "req")[-1]
                            forged, new_s3 = _sec.token_hex(32), _sec.token_hex(32)
                            state["items"] = [dialog_seal.seal(secret, device_id, rid_n, "pair-rekey", {"s": forged, "e": new_e + 500})]
                            page.evaluate("() => { poll(); }")
                            page.wait_for_timeout(3000)
                            if (page.evaluate(cred_js) or {}).get("s") != new_s:
                                fails.append("古い秘密（sp）で封じた偽の新しい秘密に切り替わった（Mac の知らない鍵へ）")
                            else:
                                state["items"] = [dialog_seal.seal(new_s, device_id, rid_n, "pair-rekey", {"s": new_s3, "e": new_e + 500})]
                                page.evaluate("() => { poll(); }")
                                if not wait_for(page, lambda: steps(sent, new_s3, "ack"), 15):
                                    fails.append("今の秘密で封じた本物の新しい秘密に切り替わらない（2 回目の延長）")
        ctx.close()
        for f in fails:
            print(f"  ✗ R116-S1b 延長: {f}")
        ng += len(fails)
        if req and not fails:
            print("  ✓ R116-S1b 延長: rk に自分の端末 → 今の秘密で求め → 封書の新しい秘密を開き、新しい秘密で受領 → cred は新しい秘密と期限へ・"
                  "受領待ちは Mac の「済み」の封書でだけ終える（HTTP の Mac にも送り直し、眠った Mac には送らない・その間は spu 切れでも sp で開ける・"
                  "別の鍵は開けない）→ 終えたら spu は 2 分後（古い秘密の窓を狭く）・期限は「済み」の e・前回の受領待ちが残っても次の［延長］へ進む・"
                  "古い秘密で封じた偽の「済み」と偽の新しい秘密は受けない（延長の答えは今の秘密だけで開く）")
        # ⑤ スマホの時計が 45 秒進んでいても終える
        new_s2 = _sec.token_hex(32)
        state, sent = {"rk": [device_id], "items": None}, []
        ctx, page = open_page(browser, state, sent, skew_ms=45000)
        req = wait_for(page, lambda: steps(sent, secret, "req"))
        acks = None
        if req:
            state["items"] = [dialog_seal.seal(secret, device_id, req[0], "pair-rekey", {"s": new_s2, "e": now + 30 * 86400})]
            acks = wait_for(page, lambda: steps(sent, new_s2, "ack"))
        if acks:
            state["rk"], state["items"] = [], [done_for(new_s2, acks[-1], now + 30 * 86400)]
            page.evaluate("() => { poll(); }")
        fin = acks and wait_for(page, lambda: (lambda c: c if c and c.get("s") == new_s2 and not c.get("ka") else None)(
            page.evaluate(cred_js)), 10)
        if not fin:
            print(f"  ✗ R116-S1b 延長: スマホの時計が 45 秒進んでいると延長を終えられない（求め {bool(req)}・受領 {bool(acks)}）")
            ng += 1
        else:
            print("  ✓ R116-S1b 延長: スマホの時計が 45 秒進んでいても「済み」で終える（時計の違う 2 つを比べない）")
        ctx.close()
        # ⑥ 他の端末の延長では求めない
        state, sent = {"rk": ["d_000000000000"], "items": None}, []
        ctx, page = open_page(browser, state, sent)
        page.wait_for_timeout(6000)
        if steps(sent, secret, "req"):
            print("  ✗ R116-S1b 延長: rk に他の端末しか無いのに求めた")
            ng += 1
        else:
            print("  ✓ R116-S1b 延長: rk に他の端末しか無いときは求めない")
        ctx.close()
        # ⑦ 壊れた中身では替えない
        state, sent = {"rk": [device_id], "items": None}, []
        ctx, page = open_page(browser, state, sent)
        req = wait_for(page, lambda: steps(sent, secret, "req"))
        if req:
            state["items"] = [dialog_seal.seal(secret, device_id, req[0], "pair-rekey", {"s": "zz", "e": now + 30 * 86400})]
            page.wait_for_timeout(6000)
            c = page.evaluate(cred_js)
            if not c or c.get("s") != secret or c.get("sp") or steps(sent, secret, "ack"):
                print(f"  ✗ R116-S1b 延長: 壊れた中身（秘密の形が違う）で cred を替えた／受領を出した: {short(c)}")
                ng += 1
            else:
                print("  ✓ R116-S1b 延長: 壊れた中身の封書では cred を替えず、受領も出さない")
        else:
            print("  ✗ R116-S1b 延長: ⑦ の前提（求め）が出ない")
            ng += 1
        ctx.close()
    finally:
        browser.close()
    return ng


def _cred_expiry_pins(p, base_url, device_id, secret, token):
    """本人（2026-09-29）「スマホから送っても Mac のセッションに届かない」: 端末の登録（30 日）が切れると Mac の配達員は
    封筒を「期限切れ」で捨てる。スマホは手元の期限（登録リンクの e）で①上に案内を出す ②送る操作を止めて理由を出す
    （/instruct を 1 通も出さない＝「送信しました」と嘘をつかない）③3 日前から予告する ④遠い期限・期限無し（旧い登録）は出さない。"""
    import time as _t
    ng = 0
    browser = p.chromium.launch()
    try:
        for label, e, want in (("切れた", int(_t.time()) - 3600, "expired"), ("あと 1 日", int(_t.time()) + 86400, "soon"),
                               ("遠い", int(_t.time()) + 20 * 86400, None), ("期限無し", 0, None)):
            ctx = browser.new_context(viewport={"width": 390, "height": 844}, has_touch=True, is_mobile=True)
            page = ctx.new_page()
            sent = []
            page.route("**/instruct", lambda r: (sent.append(1), r.fulfill(status=200, content_type="application/json",
                                                                             body='{"ok":true}')))
            cred = json.dumps({"d": device_id, "s": secret, "t": token, "e": e}, separators=(",", ":"))
            page.add_init_script("localStorage.setItem('aioffice.cred', " + json.dumps(cred) + ");")
            page.goto(base_url.rstrip("/") + "/app/", wait_until="domcontentloaded")
            page.wait_for_function("() => typeof send === 'function'", timeout=30000)
            page.wait_for_timeout(800)
            got = page.evaluate("""() => { const n = document.getElementById('credwarn');
                send('届くか確かめる', { session: 'e2e-attn-0001' });
                return { hidden: n.hidden, cls: n.className, text: n.textContent, note: (document.getElementById('note') || {}).textContent || '' }; }""")
            page.wait_for_timeout(1500)
            if want == "expired":
                ok = (not got["hidden"] and got["cls"] == "expired" and "期限切れ" in got["text"] and not sent
                      and "期限切れ" in got["note"])
            elif want == "soon":
                ok = not got["hidden"] and got["cls"] == "soon" and "あと" in got["text"]
            else:
                ok = got["hidden"]
            if ok:
                print(f"  ✓ 端末の登録の期限（{label}）: 案内={'出る' if want else '出ない'}" + ("・送る操作は止めて理由を出す（/instruct 0 通）" if want == "expired" else ""))
            else:
                print(f"  ✗ 端末の登録の期限（{label}）の扱いが違う: {got} /instruct={len(sent)}")
                ng += 1
            ctx.close()
    finally:
        browser.close()
    return ng

# ── R102-1 オフィスのホーム（本人裁定 2026-09-30）: 形状ごとの門 ──────────────────────────────────────────
# 本人「スマホ版で AI オフィスがまだ表示されていません」「右上のびっくりマークだけだと単なる記号」→ 裁定「部屋を主役に残し、
# あなた待ちは部屋に重ねず上下に分ける。狭い画面では部屋を小さく（×1）描いて全員を入れる」「質問は 1 タップ＋2 秒の取り消し」。
# 見ること（全 SHAPES）:
#   (a) 部屋の窓はヘッダー・ロスター帯・ドック・タブのどれとも交差しない（_pane_clear）
#   (b) 居るロボ全員の矩形が窓の中にあり、上に別の面が重なっていない（_room_all_visible・7 人＋❗ でも）
#   (c) 右上に ❗ の記号が無く、状態の語（質問・許可）がある
#   (d) あなた待ちのカード: 質問の選択肢 3 件が全部、ドックの中で見えて当たる（見えない分はドックをスクロールして届く）
#   (e) 選択肢の 1 タップ → 「送信まで 2…［取り消す］」→ 取り消せば 0 通／待てば署名済みの /instruct がちょうど 1 通
#   (f) 計画の確認（ExitPlanMode）に承認ボタンを出さず「承認は Mac で」と事実の行・言葉の指示は届く
#   (g) 許可待ちのカード: 状態の語「許可待ち」・「許可は Mac で」（押せない印）と「拒否」→ 拒否の言葉が 1 通
# 退役（意図した更新・コミットに表）: ❗ドックを窓に重ねていた頃の門（_room_band・_calls_seen・_DOCK_CLEAR_JS・_ATTN_TAIL_JS・
# 詰める段 .attn-tight・「選択肢 N 件を開く」の畳み・_long_label_set/WORST_SETS・_attn_walk_pin・_infodock_*）。
# R104 退役表: R80.9 の「1回目はトースト・2回目でシート」は R103-2a の1タップミニカードへ、
# R80.5/R102-1 の人数・倍率によるピン化は全員テキスト名札へ、R81-4 の人物ティッカー＋独立ゲージ帯は1行の状態へ置換。

SHAPES = [(390, 844, "スマホ縦 390×844"), (375, 667, "スマホ縦 375×667"), (360, 780, "スマホ縦 360×780"),
          (390, 664, "iPhone Safari 390×664"), (393, 793, "iPhone 393×793"),
          (834, 1112, "iPad 縦 834×1112"), (890, 626, "Duo 開いて横 890×626"), (1112, 834, "iPad 横 1112×834"),
          # iPhone Duo（2026-10-23 発売・Apple の画素 1878×2670／1398×2034 を 3 で割った寸法）の残りの形。R134: 開いた形と iPad は
          # PC 版の並び（WIDE_SHAPES・_r134_wide_pin）、閉じた形は今までどおりスマホの 3 タブ
          (626, 890, "Duo 開いて縦 626×890"), (466, 678, "Duo 閉じて縦 466×678"), (678, 466, "Duo 閉じて横 678×466"),
          (844, 390, "スマホ横 844×390"), (667, 375, "スマホ横 667×375"),
          (568, 320, "スマホ横 568×320"), (667, 325, "スマホ横 667×325"),
          (568, 340, "スマホ横 568×340"), (852, 343, "スマホ横 852×343")]


_OFFICE = {}

# 長いラベルの組（AskUserQuestion の選択肢は承認フックが 40 字で切り・transcript 経路は 60 字まで）。前半が共通で末尾の語だけが違う組を含む
# （「…旧環境を一か月そのまま残す／消す」）＝末尾が隠れたまま 1 タップで送られない（全文を折り返す）。
LONG_OPTION_SETS = [
    ("e2e-long-0006", "データベースの移行をどう進めますか?", [
        {"label": "データベースの移行は週末の夜間に段階的に進めて旧環境を一か月そのまま残す",
         "desc": "切り戻しに備える・容量を食う"},
        {"label": "データベースの移行は週末の夜間に段階的に進めて旧環境を一か月そのまま消す",
         "desc": "容量をすぐ空ける・切り戻せない"},
        {"label": "データベースの移行は週末の夜間に段階的に進めて新旧を並走させ旧は読み取りで残す",
         "desc": "並走中は書き込みを新へ寄せる"},
        {"label": "データベースの移行は週末の夜間に段階的に進めて新旧を並走させ旧は読み取りで消す",
         "desc": "並走の後に旧を片付ける"},
    ]),
    ("e2e-long-0007", "設定の変更をいつ反映しますか?", [
        {"label": "設定ファイルの読み込みは起動時の一回だけにして変更は再起動したときに反映する",
         "desc": "いちばん単純・再起動が要る"},
        {"label": "設定ファイルの読み込みは起動時の一回だけにして変更は画面を開き直すと反映する",
         "desc": "画面単位で読み直す"},
        {"label": "設定ファイルの読み込みは起動時の一回だけにして変更は保存した瞬間に反映する",
         "desc": "ファイルの監視が要る"},
    ]),
    ("e2e-long-0008", "今夜のデプロイはどうしますか?", [
        {"label": "本番環境へのデプロイは今夜の定期メンテナンスの時間帯に全部まとめて実施する",
         "desc": "停止は 1 回で済む"},
        {"label": "本番環境へのデプロイは今夜の定期メンテナンスの時間帯に全部まとめて見送る",
         "desc": "次の枠まで待つ"},
    ]),
    # 上限の組: サーバーはラベルを 60 字まで通す（server/office_server.py の _question_options＝short(…, 60)・
    # transcript 経路は承認フックの 40 字の clip を通らない）。全角 60 字 × 4 件・前半 54 字が共通。
    ("e2e-long-0009", "旧環境をどうしますか?", [
        {"label": "データベースの移行は週末の夜間に段階的に進めて新旧の環境をしばらく並走させたうえで"
                  "旧環境の中身を最低限一か月そのまま残す", "desc": "切り戻しに備える・容量を食う"},
        {"label": "データベースの移行は週末の夜間に段階的に進めて新旧の環境をしばらく並走させたうえで"
                  "旧環境の中身を最低限一か月そのまま消す", "desc": "容量をすぐ空ける・切り戻せない"},
        {"label": "データベースの移行は週末の夜間に段階的に進めて新旧の環境をしばらく並走させたうえで"
                  "旧環境の中身を最低限一か月まるごと残す", "desc": "読み取り専用で置いておく"},
        {"label": "データベースの移行は週末の夜間に段階的に進めて新旧の環境をしばらく並走させたうえで"
                  "旧環境の中身を最低限一か月まるごと消す", "desc": "バックアップも消す"},
    ]),
    # 最悪の組＝60 字 × 4 件＋長い質問。R102-1 ではドックの中をスクロールして全部に届く（_long_label_pin）。
    ("e2e-long-0010", "データベースの移行を週末の夜間に段階的に進めたあと、新旧を並走させた旧環境の中身を"
                      "どれくらいの期間どういう形で残しますか?", [
        {"label": "データベースの移行は週末の夜間に段階的に進めて新旧の環境をしばらく並走させたうえで"
                  "旧環境の中身を最低限一か月そのまま残す", "desc": "切り戻しに備える・容量を食う"},
        {"label": "データベースの移行は週末の夜間に段階的に進めて新旧の環境をしばらく並走させたうえで"
                  "旧環境の中身を最低限一か月そのまま消す", "desc": "容量をすぐ空ける・切り戻せない"},
        {"label": "データベースの移行は週末の夜間に段階的に進めて新旧の環境をしばらく並走させたうえで"
                  "旧環境の中身を最低限一か月まるごと残す", "desc": "読み取り専用で置いておく"},
        {"label": "データベースの移行は週末の夜間に段階的に進めて新旧の環境をしばらく並走させたうえで"
                  "旧環境の中身を最低限一か月まるごと消す", "desc": "バックアップも消す"},
    ]),
    # 統括の裁定（R99-P4-9 の続き）: 幅 850 でも 2 行に折り返す質問（81 字）と短い 3 件。質問が 2 行になると、高さ 340 以上の横向き
    # （852×343 等）で「選択肢 3 件を開く」「✍️ 自由に」の行が丸ごと箱の下に隠れた（固定のしきい値＝高さ 340 未満の外・別モデルレビュー
    # の実測）。ほかの組はどれも質問が短いか、幅 800 級では 1 行に収まる＝この形を誰も見ていなかった。
    ("e2e-long-0011", "本番環境へのデプロイを今夜の定期メンテナンスの時間帯に進める前に、切り戻しの手順と容量の見積もりと"
                      "関係者への事前の連絡を、どの順番で済ませてから作業を始めますか?", [
        {"label": "案A: 手順→容量→連絡", "desc": "切り戻しを先に固める"},
        {"label": "案B: 連絡→手順→容量", "desc": "関係者の都合を先に押さえる"},
        {"label": "案C: 容量→手順→連絡", "desc": "空きが足りないなら延期"},
    ]),
]


def _tails(labels):
    """各ラベルの「末尾の違う語」＝他のラベルとの最長の共通前半より後ろ（ここが見えないと選択肢を見分けられない）。"""
    out = []
    for i, s in enumerate(labels):
        k = max((len(os.path.commonprefix([s, o])) for j, o in enumerate(labels) if j != i), default=0)
        out.append(s[k:])
    return out


def _office_server():
    """署名の検算は relay_agent と同じ正本（server/office_server.verify_envelope）で行う。"""
    if "m" not in _OFFICE:
        spec = importlib.util.spec_from_file_location(
            "office_server_pwa3d", Path(__file__).resolve().parents[1] / "server" / "office_server.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        _OFFICE["m"] = mod
    return _OFFICE["m"]


def _verify(body, device_id, secret):
    """PWA が送った封筒を Mac 側と同じ検証に通す → (ok, reason, session, text)。"""
    try:
        env = json.loads(body or "")
    except ValueError:
        return False, "not-json", "", ""
    now = int(time.time())
    devices = {"devices": {device_id: {"secret": secret, "expires": now + 3600, "revoked": False}}}
    return _office_server().verify_envelope(env, devices, now, 300)


# R114-P: タップの本文（Mac の R114-S と同じ形・並び＝tests/approve_kat.mjs の固定値と同じ）。WebAuthn の 4 項目は持たない
TAP_KEYS = ["aioffice", "kind", "mode", "reqId", "session", "askId", "tool", "inputSha", "iat", "decision"]
WEBAUTHN_KEYS = ("credId", "authData", "clientDataJSON", "signature")


UX_ENTRIES = ("home", "split", "tab", "term", "sheet", "notif")


def _ux_ok(ux):
    """R116-S1: 許可の本文の ux＝入口・状態・見えてから押すまで（ms）・5 秒の待ち（ms）だけ（本文・命令・session は載せない）。"""
    if not isinstance(ux, dict) or set(ux) != {"e", "s", "ms", "q"}:
        return False, "鍵の組"
    if ux["e"] not in UX_ENTRIES or ux["s"] != "ready":
        return False, "入口か状態"
    if not all(isinstance(ux[k], int) and not isinstance(ux[k], bool) and 0 <= ux[k] <= 3600000 for k in ("ms", "q")):
        return False, "ms か q"
    return True, ""


def _tap_env(env, device_id, secret, ask_id=None, sha=None):
    """apv- の封筒がタップの形か → (ok, 理由, 内側)。外側は Mac と同じ verify_envelope（canonical は不変）。"""
    if not env:
        return False, "封筒なし", {}
    ok, why, target, text = _verify(json.dumps(env), device_id, secret)
    try:
        inner = json.loads(text or "{}")
    except ValueError:
        return False, "本文が JSON でない", {}
    if not ok:
        return False, f"外側の HMAC が通らない（{why}）", inner
    if target != "apv-" + str(inner.get("reqId", "")):
        return False, f"宛先が apv-<reqId> でない: {target}", inner
    if list(inner) != TAP_KEYS + ["ux"]:
        return False, f"キーの並びが違う: {list(inner)}", inner
    ok_ux, why_ux = _ux_ok(inner.get("ux"))
    if not ok_ux:
        return False, f"ux が監査の形でない（{why_ux}）: {inner.get('ux')}", inner
    if inner.get("mode") != "tap" or inner.get("kind") != "approve" or inner.get("decision") != "allow":
        return False, f"mode/kind/decision が違う: {inner}", inner
    if any(k in inner for k in WEBAUTHN_KEYS):
        return False, "WebAuthn の項目が載っている", inner
    if ask_id is not None and inner.get("askId") != ask_id:
        return False, "askId が掲示と違う", inner
    if sha is not None and inner.get("inputSha") != sha:
        return False, "inputSha が掲示と違う", inner
    return True, "", inner


def _wait_sent(page, sent, n, ms=5000):
    end = time.time() + ms / 1000
    while len(sent) < n and time.time() < end:
        page.wait_for_timeout(100)
    return len(sent) >= n


def _shot(page, name):
    """赤の回だけ画面を控える（SwiftShader の 3D 込みの撮影は 1 枚数秒かかる＝緑の回には撮らない）→ リポジトリからの相対パス。"""
    shot = Path(__file__).resolve().parent / "artifacts" / name
    try:
        page.screenshot(path=str(shot))
        return str(shot.relative_to(Path(__file__).resolve().parents[1]))
    except Exception:
        return None                                   # 撮れなくても赤の理由は出す


_PANE_CLEAR_JS = """(() => {
  const r = (n) => { if (!n) return null; const b = n.getBoundingClientRect(); return b.width > 0 && b.height > 0 ? b : null; };
  const w = r(document.getElementById('officewin'));
  if (!w) return { ok: false, over: ['窓が無い'], clip: false, win: null };
  // R103-2b: 部屋は独立した窓。下のあなた待ち1件・次の一手と横分割の右列も重ねない。
  const others = { header: r(document.querySelector('.topbar')), roster: r(document.getElementById('topdock')),
    mini: r(document.getElementById('minicard')), officewait: r(document.getElementById('officewait')),
    nextbar: r(document.getElementById('nextbar')),
    splitwait: r(document.getElementById('splitwait')), dock: r(document.getElementById('dock')),
    tabs: r(document.getElementById('tabbar')) };
  const over = [];
  for (const [k, b] of Object.entries(others)) {
    if (!b) continue;
    const ix = Math.min(w.right, b.right) - Math.max(w.left, b.left), iy = Math.min(w.bottom, b.bottom) - Math.max(w.top, b.top);
    if (ix > 0.5 && iy > 0.5) over.push(k + ':' + Math.round(ix) + 'x' + Math.round(iy));
  }
  const wrap = document.getElementById('scene3dwrap'), cs = wrap ? getComputedStyle(wrap) : null;
  const clip = !!cs && cs.overflowX === 'hidden' && cs.overflowY === 'hidden';
  return { ok: over.length === 0 && clip, over, clip,
           win: [Math.round(w.left), Math.round(w.top), Math.round(w.width), Math.round(w.height)] };
})()"""

# 居るロボ全員（退場中を除く）: 描いた矩形（足元の anchor から頭まで＝ROBOT_H 16 論理 px）が窓（#scene3dwrap）の中に全部あり、
# 胴の中心に当たる面が窓の中（canvas か名札）＝上に別の面が重なっていない。倍率は bootpx の state（×1〜×4）。
_ROOM_ALL_JS = """() => {
  const s3 = window.__scene3d, w = document.getElementById('scene3dwrap'), host = document.getElementById('scene3d');
  if (!s3 || !w || !host || !s3.view) return null;
  const wr = w.getBoundingClientRect(), hr = host.getBoundingClientRect();
  const S = s3.view.state().roomScale;
  const ags = s3.agents() || [];
  const bad = [], ok = [];
  for (const a of ags) {
    const p = s3.anchor(a.id);
    if (!p) { bad.push({ id: a.id, why: 'no-anchor' }); continue; }
    const cx = hr.left + p.left, feet = hr.top + p.top - 1, head = feet - 16 * S, half = 6 * S;
    const inside = cx - half >= wr.left - 1 && cx + half <= wr.right + 1 && head >= wr.top - 1 && feet <= wr.bottom + 1;
    const hit = document.elementFromPoint(cx, (head + feet) / 2);
    const covered = !!hit && !w.contains(hit);
    (inside && !covered ? ok : bad).push({ id: a.id, y: [Math.round(head), Math.round(feet)], x: Math.round(cx), inside, covered,
      at: covered ? (hit.id || String(hit.className) || hit.tagName) : null });
  }
  return { n: ags.length, S, win: [Math.round(wr.top), Math.round(wr.bottom)], ok: ok.length, bad };
}"""


def _pane_clear(page, label):
    """(a) 部屋の窓はヘッダー・ロスター帯・ドック・タブのどれとも交差しない・部屋は窓の中でだけクリップしてパン。赤の件数を返す。"""
    pc = page.evaluate(_PANE_CLEAR_JS)
    if pc["ok"]:
        print(f"  ✓ R102-1 {label}: 部屋の窓 {pc['win']} は他の面（ドックも）と交差しない（窓の中でクリップ）")
        return 0
    print(f"  ✗ R102-1 {label}: 部屋の窓が他の面と重なる／クリップしない: {pc}")
    return 1


def _room_all_visible(page, label):
    """(b) 居るロボ全員が部屋の窓の中に見えている（上に別の面が重なっていない）。赤の件数を返す。"""
    rv = page.evaluate(_ROOM_ALL_JS)
    if not rv or not rv["n"]:
        print(f"  ✗ R102-1 {label}: 部屋にロボが居ない／橋渡しが無い: {rv}")
        return 1
    if rv["bad"]:
        # R122: 何が窓の丈を食っているかを並べる（部屋の子と #app の格子の子の箱）
        boxes = page.evaluate("""() => { const box = (n) => { const r = n.getBoundingClientRect();
            return (n.id ? '#' + n.id : n.tagName.toLowerCase() + '.' + String(n.className || '').split(' ')[0]) + '=' + [Math.round(r.top), Math.round(r.bottom), Math.round(r.left), Math.round(r.right)].join(','); };
          const room = document.getElementById('room'), app = document.getElementById('app');
          return { room: room ? [...room.children].filter((n) => n.getClientRects().length).map(box) : [],
            app: app ? [...app.children].filter((n) => n.getClientRects().length).map(box) : [], rows: app ? getComputedStyle(app).gridTemplateRows : '' }; }""")
        print(f"  ✗ R102-1 {label}: 部屋の窓で見えないロボが居る（{rv['ok']}/{rv['n']} 体・×{rv['S']}・窓 y={rv['win']}）: "
              f"{rv['bad'][:4]} → bootpx.js の fitScale（×2 が入らなければ ×1）・箱={boxes}")
        return 1
    print(f"  ✓ R102-1 {label}: 部屋の窓に全員 {rv['n']}/{rv['n']} 体が見える（×{rv['S']}・窓 y={rv['win']}）")
    return 0


_CHIP_JS = """() => {
  const hs = document.getElementById('hstats'), tb = document.querySelector('.topbar');
  const chips = [...document.querySelectorAll('#hstats .ichip')].map(n => {
    const r = n.getBoundingClientRect(), x = r.left + r.width / 2, y = r.top + r.height / 2, hit = document.elementFromPoint(x, y);
    return { kind: n.getAttribute('data-kind'), text: n.textContent.trim(), aria: n.getAttribute('aria-label') || '',
             h: Math.round(r.height), x, y, hit: !!hit && (hit === n || n.contains(hit)),
             color: getComputedStyle(n.querySelector('.ib')).color };
  });
  return { chips, bang: (tb ? tb.textContent : '').indexOf('\\u2757') >= 0, aria: hs ? hs.getAttribute('aria-label') || '' : '',
           calm: !!document.querySelector('#hstats .calm') };
}"""


def _chip_pin(page, label):
    """(c) 右上: ❗ の記号でなく状態の語。P7（質問 1・許可待ち 1）では「質問」「許可」の 2 つ、当たり 44 以上。"""
    c = page.evaluate(_CHIP_JS)
    kinds = sorted(x["kind"] for x in c["chips"])
    bad = []
    if c["bang"]:
        bad.append("ヘッダーに ❗ の記号が残っている")
    if "perm" not in kinds or "question" not in kinds:
        bad.append(f"許可・質問のチップが無い（{kinds}）")
    for x in c["chips"]:
        if x["h"] < 44 or not x["hit"]:
            bad.append(f"チップ {x['kind']} の当たりが 44 未満／覆われている（{x['h']}px・hit={x['hit']}）")
    if "あなた" not in c["aria"] and "質問" not in c["aria"]:
        bad.append(f"aria-label に状態の文が無い（{c['aria']!r}）")
    if bad:
        print(f"  ✗ R102-1 {label}: 右上の状態の語: " + "／".join(bad) + f"（{[x['text'] for x in c['chips']]}）")
        return 1
    print(f"  ✓ R102-1 {label}: 右上は言葉のチップ {[x['text'] for x in c['chips']]}（❗ の記号なし・当たり 44 以上）")
    return 0


# あなた待ちのカードの選択肢: 専用タブの一覧をスクロール（scrollIntoView・block nearest）→ 一覧の箱の中・画面の中にあり、
# 中心が当たる。全件の縦一覧なので、1件目を含め必要な箇所へスクロールして届くことを検査する。
_OPTS_JS = """() => {
  const dock = document.getElementById('home'), card = document.querySelector('#attncards .attncard');
  if (!dock || !card) return { sess: null, opts: [] };
  dock.scrollTop = 0;
  const probe = (n) => {
    const d = dock.getBoundingClientRect(), r = n.getBoundingClientRect();
    const x = r.left + r.width / 2, y = r.top + r.height / 2, hit = document.elementFromPoint(x, y);
    const inBox = r.top >= Math.max(d.top, 0) - 1 && r.bottom <= Math.min(d.bottom, innerHeight) + 1 && r.left >= d.left - 1 && r.right <= d.right + 1;
    const inside = r.top >= -1 && r.bottom <= innerHeight + 1;
    return { inBox, inside, hit: !!hit && (hit === n || n.contains(hit)), y: [Math.round(r.top), Math.round(r.bottom)],
             at: hit ? (hit.id || String(hit.className) || hit.tagName) : null };
  };
  const nodes = [...card.querySelectorAll('.attnoptions .qopt')];
  if (nodes[0]) nodes[0].scrollIntoView({ block: 'nearest' });
  const first = nodes[0] ? probe(nodes[0]) : null;
  const opts = nodes.map(n => { n.scrollIntoView({ block: 'nearest' }); const p = probe(n);
    const lab = n.querySelector('.qopt-label'); const cs = lab ? getComputedStyle(lab) : null;
    return Object.assign(p, { raw: n.getAttribute('data-raw'), label: lab ? lab.textContent : '',
      wraps: !!cs && cs.whiteSpace !== 'nowrap' && cs.textOverflow !== 'ellipsis', clip: n.scrollHeight > n.clientHeight + 1,
      ok: p.inBox && p.inside && p.hit }); });
  dock.scrollTop = 0;
  const tag = card.querySelector('.ktag');
  return { sess: card.getAttribute('data-attn-sess'), kind: card.getAttribute('data-kind'), tag: tag ? tag.textContent : '',
           nav: (document.getElementById('wait_title') || {}).textContent || '', first, opts,
           dchip: (card.querySelector('.dchip') || {}).textContent || '' };
}"""


def _tap_center(page, sel):
    b = page.evaluate("""(sel) => { const n = document.querySelector(sel); if (!n) return null; n.scrollIntoView({ block: 'nearest' });
      const r = n.getBoundingClientRect(); const x = r.left + r.width / 2, y = r.top + r.height / 2, hit = document.elementFromPoint(x, y);
      return { x, y, hit: !!hit && (hit === n || n.contains(hit)), at: hit ? (hit.id || String(hit.className)) : null }; }""", sel)
    if b and b["hit"]:
        page.touchscreen.tap(b["x"], b["y"])
    return b


def _r122_home_pin(page, label):
    """R122-P2: 230pxのオフィス→あなたの番→全員の文章1行。最近を押すと本人の会話。
    R134: 広い画面（開いた Duo・iPad）はホームの部屋のカードを出さない（オフィスは右の列か重ねる窓）・最近を押すと真ん中の会話がその人に替わる（戻るは無い）。"""
    before = page.evaluate("() => VIEW")
    if page.evaluate("() => WIDE"):
        return _r134_home_pin(page, label, before)
    page.evaluate("() => { if (TERM) closeTerm(true, true); closeSheet(); closeMini(); setView('wait', true); }")
    page.wait_for_timeout(200)
    got = page.evaluate("""() => {
      const home=document.getElementById('home'), office=document.getElementById('homeoffice'),
        wait=document.getElementById('wait'), recent=document.getElementById('recent');
      const rows=[...document.querySelectorAll('#list .recentrow')];
      return { view:VIEW, height:office.getBoundingClientRect().height,
        order:!!(office.compareDocumentPosition(wait)&Node.DOCUMENT_POSITION_FOLLOWING)
          && !!(wait.compareDocumentPosition(recent)&Node.DOCUMENT_POSITION_FOLLOWING),
        inside:home.contains(office)&&home.contains(wait)&&home.contains(recent),
        rows:rows.map(n=>({ sess:n.getAttribute('data-sess'), name:(n.querySelector('.recentname')||{}).textContent||'',
          line:(n.querySelector('.recentline')||{}).textContent||'', age:!!n.querySelector('.recenttime'), dot:!!n.querySelector('.recentdot'),
          expected:recentLine(sessionAgent(n.getAttribute('data-sess'))), nested:n.querySelectorAll('button').length })),
        wanted:recentAgents(LAST_OFFICE).filter(e=>e&&e.session).map(e=>e.session),
        feed:document.querySelectorAll('#list .feed,#list .feedline').length,
        theme:document.documentElement.getAttribute('data-theme') };
    }""")
    good = (got["view"] == "wait" and abs(got["height"] - 230) <= 1 and got["order"] and got["inside"]
            and sorted(r["sess"] for r in got["rows"]) == sorted(got["wanted"]) and not got["feed"]
            and all(r["name"] and r["line"] == r["expected"] and r["age"] and r["dot"] and not r["nested"] for r in got["rows"]))
    route = True
    if got["rows"]:
        session=got["rows"][0]["sess"]
        page.locator("#list .recentrow").first.click()
        page.wait_for_timeout(150)
        route = page.evaluate("""s => !!TERM && TERM.session===s && document.getElementById('termwrap').classList.contains('open')
          && document.getElementById('termname').textContent && document.getElementById('termstate').textContent""", session)
        page.locator("#term_close").click()
        route = route and page.evaluate("() => !TERM && VIEW==='wait'")
    page.locator("#homeoffice").click()
    office_route = page.evaluate("() => VIEW==='office'")
    page.evaluate("v => setView(v, true)", before)
    if good and route and office_route:
        print(f"  ✓ R122-P2 {label}: ホームの230pxオフィス→あなたの番→全員{len(got['rows'])}件の文章1行・最近から本人の会話・戻る")
        return 0
    print(f"  ✗ R122-P2 {label}: ホームの構成・文章1行・会話導線が違う: {got} 会話={route} オフィス={office_route}")
    return 1


def _r134_home_pin(page, label, before):
    """R134 の広い画面のホーム（一覧の列）: あなたの番→最近の全員（文章 1 行）・ホームの部屋のカードは無い・最近を押すと真ん中の会話がその人へ
    （一覧の印も移る）・会話は閉じない。"""
    page.evaluate("() => { closeSheet(); closeMini(); setView('wait', true); }")
    page.wait_for_timeout(200)
    got = page.evaluate("""() => {
      const home=document.getElementById('home'), office=document.getElementById('homeoffice'),
        wait=document.getElementById('wait'), recent=document.getElementById('recent');
      const rows=[...document.querySelectorAll('#list .recentrow')];
      return { view:VIEW, homeHidden:home.classList.contains('hidden'), office:getComputedStyle(office).display,
        order:!!(wait.compareDocumentPosition(recent)&Node.DOCUMENT_POSITION_FOLLOWING), inside:home.contains(wait)&&home.contains(recent),
        rows:rows.map(n=>({ sess:n.getAttribute('data-sess'), line:(n.querySelector('.recentline')||{}).textContent||'',
          expected:recentLine(sessionAgent(n.getAttribute('data-sess'))) })),
        wanted:recentAgents(LAST_OFFICE).filter(e=>e&&e.session).map(e=>e.session) };
    }""")
    good = (got["view"] == "wait" and not got["homeHidden"] and got["office"] == "none" and got["order"] and got["inside"]
            and sorted(r["sess"] for r in got["rows"]) == sorted(got["wanted"]) and all(r["line"] == r["expected"] for r in got["rows"]))
    route = None
    if got["rows"]:
        # 今の会話でない行を押す（同じ人だと替わったことが見えない）
        target = page.evaluate("""() => { const rows = [...document.querySelectorAll('#list .recentrow')];
          const n = rows.find(r => !TERM || r.getAttribute('data-sess') !== TERM.session) || rows[0]; return n.getAttribute('data-sess'); }""")
        page.locator(f'#list .recentrow[data-sess="{target}"]').click()
        page.wait_for_timeout(200)
        route = page.evaluate("""s => ({ term: TERM && TERM.session, open: document.getElementById('termwrap').classList.contains('open'),
          name: document.getElementById('termname').textContent, view: VIEW,
          sel: [...document.querySelectorAll('#list .recentrow.is-selected')].map(n => n.getAttribute('data-sess')),
          bodyOpen: document.body.classList.contains('term-open') })""", target)
        good = good and route["term"] == target and route["open"] and route["name"] and route["view"] == "wait"             and route["sel"] == [target] and not route["bodyOpen"]
    page.evaluate("v => setView(v, true)", before)
    if good:
        print(f"  ✓ R134 {label}: 一覧の列（あなたの番→全員{len(got['rows'])}件の文章1行・ホームの部屋のカード無し）・最近を押すと真ん中の会話がその人へ（一覧の印も移る）")
        return 0
    print(f"  ✗ R134 {label}: 広い画面の一覧の列・会話の切り替えが違う: {got} 会話={route}")
    return 1


def _r103_split_pin(page, label):
    """R122-P2→R134: スマホ（閉じた Duo・普通のスマホ・低い横向き）は分割せず、同じ 3 タブ。広い画面（開いた Duo・iPad）は _r134_wide_pin が見る。"""
    got = page.evaluate("""() => ({
      split:splitOffice() || document.body.classList.contains('split-office'), wide:WIDE,
      rail:document.getElementById('splitwait').getClientRects().length > 0,
      tabs:[...document.querySelectorAll('#tabbar > button')].map(n => n.id),
      overflow:document.scrollingElement.scrollWidth > innerWidth + 1,
      iw:innerWidth, ih:innerHeight })""")
    if got["wide"]:
        return 0
    good = (not got["split"] and not got["rail"] and not got["overflow"]
            and got["tabs"] == ["tb_wait", "tb_office", "tb_set"])
    if good:
        print(f"  ✓ R122-P2 {label}: スマホの形は分割せず会話・オフィス・設定の3タブ（横はみ出しなし）")
        return 0
    print(f"  ✗ R122-P2 {label}: 画面分割・タブ・横はみ出しが違う: {got}")
    return 1


# ── R134 広い画面（開いた Duo・iPad）＝ PC 版（Mac の明るい 3 列）と同じ並び（本人 2026-11-14「iPhone Duo を開いたらパソコン版が
# 表示されてほしい」「配色も PC と同じに」）。形の正本は app.css の --wide（0／2／3）・app.js は読むだけ。
#   3 列（890×626・1112×834）: 左＝一覧・中央＝会話・右＝オフィス（部屋と人の一覧）が同時に・重ならず・画面の中・下のタブ無し・足元に［設定］
#   2 列（626×890・834×1112）: 左＝一覧・右＝会話・下のタブ無し。会話の見出しの［オフィス］で部屋と人の一覧が右の列に重なって開き、［閉じる］で戻る
#   それ以外（閉じた Duo・普通のスマホ・低い横向き）: 今までどおり下の 3 タブ
#   すべての形: 端末が暗いモード（＋前の版が保存したテーマ「ダーク」）でも地は明るい（Mac の --a-* 系）
#   390×844・890×626: 封書の会話が無いときの要約の行（取り寄せ中・要約だけ）が等幅でない（作業ビューの作法）
# 旧 R119（3 列の左を広く）の門はこの門に置き換えた（R122 から横分割そのものが無かった）。
WIDE_SHAPES = {(890, 626): 3, (1112, 834): 3, (626, 890): 2, (834, 1112): 2}

_WIDE_JS = """() => {
  const box = (n) => { if (!n) return null; const cs = getComputedStyle(n); if (cs.display === 'none' || cs.visibility === 'hidden') return null;
    const r = n.getBoundingClientRect(); return r.width > 0 && r.height > 0 ? [Math.round(r.left), Math.round(r.top), Math.round(r.right), Math.round(r.bottom)] : null; };
  const tabs = document.getElementById('tabbar');
  return { wide: WIDE, cls: ['wide', 'wide2', 'wide3'].filter((c) => document.body.classList.contains(c)),
    css: getComputedStyle(document.documentElement).getPropertyValue('--wide').trim(), iw: innerWidth, ih: innerHeight,
    view: VIEW, term: TERM ? TERM.session : null, termOpen: document.getElementById('termwrap').classList.contains('open'),
    role: document.getElementById('termwrap').getAttribute('role'),
    list: box(document.getElementById('home')), talk: box(document.getElementById('termwrap')), room: box(document.getElementById('room')),
    win: box(document.getElementById('officewin')), people: box(document.getElementById('topdock')), homeoffice: box(document.getElementById('homeoffice')),
    tabs: box(tabs), tabButtons: tabs ? [...tabs.querySelectorAll('button')].filter((b) => box(b)).length : 0,
    office: box(document.getElementById('term_office')), close: box(document.getElementById('office_close')),
    settings: box(document.getElementById('dock_set')), back: box(document.getElementById('term_close')),
    selected: [...document.querySelectorAll('#list .recentrow.is-selected')].map((n) => n.getAttribute('data-sess')),
    overflow: document.scrollingElement.scrollWidth > innerWidth + 1 };
}"""

# 端末が暗いモードでも主な面の地が明るいか（輝度 0.8 以上＝#F1EFEA〜#FFFFFF）。見えていない面は測らない
_LIGHT_SURFACES_JS = """() => {
  const lum = (c) => { const m = String(c).match(/rgba?\\(([^)]+)\\)/); if (!m) return null; const v = m[1].split(',').map(parseFloat);
    if (v.length > 3 && v[3] < 1) return null;
    const [r, g, b] = v.slice(0, 3).map((x) => { x /= 255; return x <= 0.03928 ? x / 12.92 : ((x + 0.055) / 1.055) ** 2.4; });
    return 0.2126 * r + 0.7152 * g + 0.0722 * b; };
  const out = {};
  for (const q of ['body', '.topbar', '#home', '#termwrap.open', '#room', '#tabbar', '#dock', '#list .recentrow']) {
    const n = document.querySelector(q); if (!n) continue; const cs = getComputedStyle(n);
    if (cs.display === 'none' || cs.visibility === 'hidden') continue;
    const r = n.getBoundingClientRect(); if (q !== 'body' && !(r.width > 0 && r.height > 0)) continue;
    const l = lum(cs.backgroundColor); if (l === null) continue; out[q] = Math.round(l * 1000) / 1000; }
  return { dark: matchMedia('(prefers-color-scheme: dark)').matches, attr: document.documentElement.getAttribute('data-theme'),
           scheme: getComputedStyle(document.documentElement).colorScheme, saved: localStorage.getItem('aioffice.theme'), out };
}"""

# 要約の行（取り寄せ中＝tinit・要約だけ＝toff）の字。canTermFor を一時だけ真にし termRequest を止める＝/instruct を 1 通も出さない
_SUMMARY_FONT_JS = """() => {
  const e = { session: 'e2e-r134-sum', projectId: 'e2e-r134-sum', disp: 'E2E要約部', state: 'working', listening: true, verb: '実行中',
              feed: ['実行中 npm test', '編集中 ui/pwa/app.css'] };
  const prev = TERM ? TERM.session : null, c0 = window.canTermFor, r0 = window.termRequest;
  const read = () => [...document.querySelectorAll('#termbody .tl.tsum, #termbody .tl.tsum .tb, #termbody .tl.gap.tinit, #termbody .toff, #termbody .toff .tob')]
    .map((n) => ({ cls: String(n.className), font: getComputedStyle(n).fontFamily }));
  let fetching = [], off = [];
  try { window.canTermFor = () => true; window.termRequest = () => {}; openTerm(e, true); fetching = read(); }
  finally { window.canTermFor = c0; window.termRequest = r0; }
  openTerm(e, true); off = read();
  if (prev && prev !== e.session) openTerm(sessionAgent(prev) || { session: prev }, true); else closeTerm(true, true);
  return { fetching, off };
}"""


def _r134_wide_pin(page, label):
    """R134: 形ごとの並び（3 列・2 列・スマホ）と、暗いモードでも明るい地。390×844・890×626 では要約の行の字も見る。赤の件数を返す。"""
    vw = page.viewport_size or {}
    want = WIDE_SHAPES.get((vw.get("width"), vw.get("height")), 0)
    before = page.evaluate("() => VIEW")
    if want:
        page.evaluate("() => setView('wait', true)")
        try:
            page.wait_for_function("() => !!TERM", timeout=8000)          # 広い画面は会話の列をいつも埋める（開いていなければ自動で 1 人）
        except PWTimeout:
            pass
        page.wait_for_timeout(200)
    g = page.evaluate(_WIDE_JS)

    def inside(b):
        return bool(b) and b[0] >= -1 and b[1] >= -1 and b[2] <= g["iw"] + 1 and b[3] <= g["ih"] + 1

    def apart(a, b):
        return bool(a and b) and (min(a[2], b[2]) - max(a[0], b[0]) <= 0.5 or min(a[3], b[3]) - max(a[1], b[1]) <= 0.5)

    bad = []
    if g["wide"] != want or str(g["css"]) != str(want):
        bad.append(f"形が違う（WIDE={g['wide']}・--wide={g['css']!r}・期待 {want}）")
    if want:
        if g["tabs"] or g["tabButtons"]:
            bad.append(f"下のタブが出ている（{g['tabs']}・{g['tabButtons']} 個）")
        if g["cls"] != ["wide", f"wide{want}"]:
            bad.append(f"body の印が違う（{g['cls']}）")
        if not (inside(g["list"]) and inside(g["talk"])):
            bad.append(f"一覧と会話が画面の中に無い（一覧 {g['list']}・会話 {g['talk']}）")
        elif not (apart(g["list"], g["talk"]) and g["list"][2] <= g["talk"][0] + 1):
            bad.append(f"一覧と会話が左右に並ばない／重なる（{g['list']}・{g['talk']}）")
        if not (g["termOpen"] and g["term"] and g["role"] == "region" and g["selected"] == [g["term"]]):
            bad.append(f"会話の列が開いていない／一覧の印が違う（term={g['term']}・role={g['role']}・印 {g['selected']}）")
        if g["homeoffice"] or g["back"] or not g["settings"] or g["overflow"]:
            bad.append(f"ホームの部屋のカード／戻る／足元の［設定］／横はみ出しが違う（{g['homeoffice']}・{g['back']}・{g['settings']}・{g['overflow']}）")
    if want == 3:
        if not (inside(g["room"]) and inside(g["win"])):
            bad.append(f"オフィスの列が画面の中に無い（{g['room']}・窓 {g['win']}）")
        elif not (apart(g["talk"], g["room"]) and apart(g["list"], g["room"]) and g["talk"][2] <= g["room"][0] + 1):
            bad.append(f"会話とオフィスが左右に並ばない／重なる（{g['talk']}・{g['room']}）")
        if not (g["people"] and g["win"] and g["people"][1] >= g["win"][3] - 1 and g["people"][0] >= g["room"][0] - 1):
            bad.append(f"人の一覧が部屋のすぐ下に無い（{g['people']}・窓 {g['win']}）")
        if g["office"]:
            bad.append("3 列なのに［オフィス］のボタンが出ている")
    elif want == 2:
        if g["room"] or not g["office"] or not g["talk"] or g["office"][2] < g["talk"][2] - 120:
            bad.append(f"2 列でオフィスが重なっている／見出しの右上に［オフィス］が無い（部屋 {g['room']}・ボタン {g['office']}）")
        else:
            page.locator("#term_office").click()
            page.wait_for_timeout(400)
            o = page.evaluate(_WIDE_JS)
            if not (o["view"] == "office" and inside(o["room"]) and inside(o["win"]) and o["people"] and o["close"]
                    and o["room"][0] >= o["list"][2] - 1 and o["term"] == g["term"]):
                bad.append(f"［オフィス］で部屋と人の一覧が右の列に重ならない（{o['view']}・部屋 {o['room']}・窓 {o['win']}・一覧 {o['list']}・閉じる {o['close']}）")
            page.locator("#office_close").click()
            page.wait_for_timeout(300)
            c = page.evaluate(_WIDE_JS)
            if not (c["view"] == "wait" and not c["room"] and c["term"] == g["term"] and c["termOpen"]):
                bad.append(f"［閉じる］で会話に戻らない（{c['view']}・部屋 {c['room']}・会話 {c['term']}）")
    elif g["cls"] or not g["tabs"] or g["tabButtons"] != 3:
        bad.append(f"スマホの形なのに広い画面の印／下の 3 タブが無い（{g['cls']}・{g['tabs']}・{g['tabButtons']} 個）")
    # 暗いモード（＋前の版が保存した「ダーク」＝_shape_pins の init script）でも地は明るい
    try:
        page.emulate_media(color_scheme="dark")
        lt = page.evaluate(_LIGHT_SURFACES_JS)
    finally:
        page.emulate_media(color_scheme=None)
    dim = {k: v for k, v in lt["out"].items() if v < 0.8}
    if not lt["dark"] or lt["attr"] is not None or lt["saved"] is not None or lt["scheme"] != "light" or dim or "body" not in lt["out"]:
        bad.append(f"暗いモードで地が暗い／テーマの印が残る（{lt}）")
    fonts = (vw.get("width"), vw.get("height")) in ((390, 844), (890, 626))
    if fonts:
        f = page.evaluate(_SUMMARY_FONT_JS)
        mono = [x for x in f["fetching"] + f["off"] if re.search(r"mono|menlo|consolas|courier", x["font"], re.I)]
        kinds = {"tinit": any("tinit" in x["cls"] and "gap" in x["cls"] for x in f["fetching"]),
                 "fetch": sum(1 for x in f["fetching"] if x["cls"].startswith("tl ") and "tsum" in x["cls"]) == 2,
                 "toff": any(x["cls"] == "toff" for x in f["off"]),
                 "off": sum(1 for x in f["off"] if x["cls"].startswith("tl ") and "tsum" in x["cls"]) == 2}
        if mono or not all(kinds.values()):
            bad.append(f"要約の行が等幅のまま／行が無い（等幅 {[x['cls'] for x in mono]}・{kinds}）")
        page.evaluate("(v) => setView(v, true)", "wait" if want else before)
    if bad:
        at = _shot(page, f"pwa3d_r134_{vw.get('width')}x{vw.get('height')}.png")
        print(f"  ✗ R134 {label}: " + "／".join(bad) + (f"（画面= {at}）" if at else ""))
        return 1
    shape = {3: "3 列（一覧・会話・オフィス）", 2: "2 列（一覧・会話＋［オフィス］で重ねて開閉）", 0: "スマホ（下の 3 タブ）"}[want]
    print(f"  ✓ R134 {label}: {shape}・暗いモードでも明るい地" + ("・要約の行は等幅でない" if fonts else ""))
    return 0


def _r103_sheet_pins(page, label):
    """R103-4: 本文は即時に1回だけ、長い本文をスクロールしても返信欄と44pxの操作が見える。"""
    got = page.evaluate("""async () => {
      const question = 'R103入力欄はどの案で進めますか?';
      const e = { session:'e2e-r103-sheet', disp:'返信テスト', state:'waiting', listening:true, question,
        questionOptions:[{ label:'案A' }, { label:'案B' }], feed:Array.from({ length:16 }, (_, i) => '最近の動き ' + i) };
      openSheet(e);
      const immediate = (document.querySelector('#shdetail .saidq') || {}).textContent || '';
      const text = document.querySelector('#sheetwrap .sheet').textContent;
      const count = text.split(question).length - 1;
      const scroll = document.getElementById('shscroll');
      const pad = document.createElement('div'); pad.style.height = '900px'; scroll.appendChild(pad);
      const inspect = () => {
        const field = document.getElementById('freetext'), send = document.getElementById('sh_send');
        const fr = field.getBoundingClientRect(), br = send.getBoundingClientRect(), sr = document.querySelector('#sheetwrap .sheet').getBoundingClientRect();
        const hit = document.elementFromPoint(fr.left + fr.width/2, fr.top + fr.height/2);
        return { top:fr.top, bottom:fr.bottom, fieldH:fr.height, buttonH:br.height,
          visible:fr.height>0 && fr.top >= sr.top && fr.bottom <= Math.min(sr.bottom, innerHeight)+1
            && br.top >= sr.top && br.bottom <= Math.min(sr.bottom, innerHeight)+1,
          hit:hit===field || field.contains(hit) };
      };
      await new Promise(r => setTimeout(r, 180)); const first = inspect();
      scroll.scrollTop = scroll.scrollHeight; await new Promise(r => setTimeout(r, 80)); const last = inspect();
      const result = { immediate, typing:!!SHSAY_IV, count, first, last, scroll:scroll.scrollTop,
        sheetScroll:document.querySelector('#sheetwrap .sheet').scrollTop };
      pad.remove(); closeSheet(); return result;
    }""")
    good = (got["immediate"] == "R103入力欄はどの案で進めますか?" and not got["typing"] and got["count"] == 1 and got["scroll"] > 0 and got["sheetScroll"] == 0
            and all(got[k]["visible"] and got[k]["hit"] and got[k]["fieldH"] >= 44 and got[k]["buttonH"] >= 44
                    for k in ("first", "last")) and abs(got["first"]["top"] - got["last"]["top"]) <= 1)
    if good:
        print(f"  ✓ R103-4 {label}: シート本文は即時・質問の重複なし・本文を{round(got['scroll'])}px下げても返信欄と44pxの送信が見える")
        return 0
    print(f"  ✗ R103-4 {label}: シートの即時本文・重複・固定返信欄が違う: {got}")
    return 1


# R116-S1: あなた待ちタブの全件＝その他のカード（.attncard）＋焦点でない許可の 1 行（.apvrow）＋先頭の決裁カード（#apvslot_wait）。
_WAIT_LISTED_JS = """() => { const out = [...document.querySelectorAll('#attncards .attncard, #attncards .apvrow')].map(n => n.getAttribute('data-attn-sess'));
  if (document.querySelector('#apvslot_wait > #apvwrap.open') && APV) out.push(attnKey(sessionAgent(APV.session) || APV.e));
  return out; }"""


def _shape_once(page, label, sent, serve, device_id, secret):
    """1 形状ぶんの検査（R102-1）。赤の件数を返す（例外はブラウザ死として呼び出し側が 1 回だけ再試行）。"""
    ng = 0
    page.wait_for_timeout(600)
    ng += _r103_split_pin(page, label)
    ng += _r134_wide_pin(page, label)
    ng += _r122_home_pin(page, label)
    page.evaluate("() => setView('office', true)")
    page.wait_for_timeout(200)
    ng += _pane_clear(page, label)
    ng += _room_all_visible(page, label)
    ng += _chip_pin(page, label)
    # R122-P2: 下のタブ「会話」は横向きの小さい形でも押せる位置にある（ほかの面が覆わない）。覆っていたら何かを言ってから赤（クリックの 30 秒待ちで原因が消えない）
    # R134: 広い画面（開いた Duo・iPad）には下のタブが無い＝一覧の列はいつも見えている（2 列の重ねたオフィスは閉じる）
    wide = page.evaluate("() => WIDE")
    tab_hit = {"inside": True} if wide else page.evaluate("""() => { const b = document.querySelector('#tb_wait'); if (!b) return { found: false };
      const r = b.getBoundingClientRect(), x = r.left + r.width / 2, y = r.top + r.height / 2, e = document.elementFromPoint(x, y);
      const who = (n) => n ? (n.id ? '#' + n.id : n.tagName.toLowerCase() + '.' + String(n.className || '').split(' ').join('.')) : null;
      return { found: true, rect: [Math.round(r.left), Math.round(r.top), Math.round(r.width), Math.round(r.height)], vw: innerWidth, vh: innerHeight,
        hit: who(e), up: e && e.parentElement ? who(e.parentElement) : null, inside: !!(e && (e === b || b.contains(e))) }; }""")
    if not tab_hit.get("inside"):
        print(f"  ✗ R122-P2 {label}: 下のタブ「会話」が押せない（覆っている面・位置）: {tab_hit}")
        return 1
    if wide:
        page.evaluate("() => setView('wait', true)")
    else:
        page.locator("#tb_wait").click()
    page.wait_for_timeout(200)
    # R103-2b: 横分割の自動端末が送った dlg-要求は回答の通数に含めない。
    # あなた待ちへ移った時点で端末は閉じるので、ここからの署名付き回答だけを数える。
    sent.clear()
    # (d) 質問のカード
    qo = page.evaluate(_OPTS_JS)
    opts = qo["opts"]
    bad = []
    if qo["sess"] != "e2e-attn-0001" or qo["kind"] != "question" or "質問" not in qo["tag"]:
        bad.append(f"先頭のカードが質問の e2e-attn-0001 でない（sess={qo['sess']}・kind={qo.get('kind')}・tag={qo.get('tag')!r}）")
    if "あなたの番 " not in qo.get("nav", ""):
        bad.append(f"ホームの題が「あなたの番 N」でない（{qo.get('nav')!r}）")
    listed = page.evaluate(_WAIT_LISTED_JS)
    wanted = page.evaluate("() => officeAgents(LAST_OFFICE).filter(needsAttn).map(attnKey)")
    if sorted(listed) != sorted(wanted):
        bad.append(f"あなた待ちが全件表示でない（{listed}/{wanted}）")
    wait_tab = page.evaluate("""() => ({ count: document.getElementById('tb_wait_count').textContent,
      colored: document.getElementById('tb_wait').classList.contains('needs-you') })""")
    if wait_tab["count"] != str(len(wanted)) or not wait_tab["colored"]:
        bad.append(f"あなた待ちタブの数字1つ／意味色が違う（{wait_tab}）")
    if len(opts) != 3 or not all(o["ok"] for o in opts):
        bad.append(f"選択肢 3 件がドックの中で見えて当たらない（{[(o['label'], o['y'], o['inBox'], o['hit'], o['at']) for o in opts]}）")
    if not all(o["wraps"] for o in opts):
        bad.append("選択肢のラベルが 1 行で切られている（全文を折り返す）")
    if not (qo["first"] and qo["first"]["inBox"] and qo["first"]["hit"]):
        bad.append(f"1 件目へスクロールしても見えない（{qo['first']}）")
    if "保存済み" not in qo.get("dchip", ""):
        bad.append(f"受信待機なしの本人の配達状態がカードに出ない（{qo.get('dchip')!r}）")
    if bad:
        at = _shot(page, f"pwa3d_attn_{(page.viewport_size or {}).get('width')}x{(page.viewport_size or {}).get('height')}.png")
        print(f"  ✗ R102-1 {label}: あなた待ちのカード: " + "／".join(bad) + (f"（画面= {at}）" if at else ""))
        ng += 1
    else:
        print(f"  ✓ R102-1 {label}: あなた待ちのカード（{qo['nav']}・{qo['tag']}）の選択肢 3 件がドックの中で見えて当たる・"
              f"配達状態「{qo['dchip']}」")
    # (e) 1 タップ＋2 秒の取り消し: 取り消せば 0 通・待てば 1 通
    if len(opts) >= 2:
        b = _tap_center(page, "#attncards .attncard .attnoptions .qopt")
        try:
            page.wait_for_selector("#attncards .qpend .qundo", timeout=3000)
            pend_txt = page.evaluate("() => (document.querySelector('#attncards .qpend .qpt') || {}).textContent || ''")
            _tap_center(page, "#attncards .qpend .qundo")
            page.wait_for_timeout(2600)
            undone = len(sent) == 0 and page.evaluate("() => document.querySelectorAll('#attncards .attnoptions .qopt').length") == 3
        except PWTimeout:
            pend_txt, undone = "", False
        if undone and "送信まで" in pend_txt:
            print(f"  ✓ R102-1 {label}: 選択肢の 1 タップ →「{pend_txt}」→ 取り消し → 送らない（/instruct 0 通）")
        else:
            print(f"  ✗ R102-1 {label}: 取り消せない（tap={b}・表示={pend_txt!r}・/instruct {len(sent)} 通）")
            ng += 1
        raw = opts[1]["raw"]
        _tap_center(page, "#attncards .attncard .attnoptions .qopt:nth-child(2)")
        got = _wait_sent(page, sent, 1, ms=5000)
        ok, why, sess, text = _verify(sent[0], device_id, secret) if got else (False, "no-request", "", "")
        page.wait_for_timeout(400)                                   # 二重送信（タッチ→click の合成が 2 回）を拾う猶予
        if ok and len(sent) == 1 and sess == "e2e-attn-0001" and text == f"選択肢「{raw}」でお願いします。":
            print(f"  ✓ R102-1 {label}: 選択肢の 1 タップ → 2 秒で署名済みの /instruct がちょうど 1 通（{text}）")
        else:
            print(f"  ✗ R102-1 {label}: 2 秒後に答えが飛ばない／違う: ok={ok}({why}) 通数={len(sent)} sess={sess} text={text!r}")
            ng += 1
    ng += _plan_pin(page, label, sent, serve, device_id, secret)
    ng += _perm_pin(page, label, sent, serve, device_id, secret)
    ng += _long_label_pin(page, label, sent, serve, device_id, secret)
    ng += _err_pin(page, label, sent, serve, device_id, secret)
    ng += _play_pin(page, label, serve)
    return ng


_B2_HEAD_JS = """() => {
  const nm = document.querySelector('.hdr2 .tname'), hs = document.getElementById('hstats');
  const chips = [...document.querySelectorAll('#hstats .ichip')].filter((n) => !n.hidden && n.getBoundingClientRect().width > 0);
  const words = chips.map((n) => { const w = n.querySelector('.w'); return w && getComputedStyle(w).display !== 'none' ? w.textContent.trim() : ''; });
  const hr = document.querySelector('.hdr2').getBoundingClientRect();
  return { nm: nm ? Math.round(nm.clientWidth) : 0, need: nm ? Math.round(nm.scrollWidth) : 0, text: nm ? nm.textContent : '',
           words, over: hs ? hs.scrollWidth > hs.clientWidth + 1 : true, right: Math.round(Math.max(0, ...chips.map((n) => n.getBoundingClientRect().right))),
           hdr: Math.round(hr.right) };
}"""

_B2_EMOJI_JS = """(ids) => {
  const emoji = /\\p{Extended_Pictographic}/u, out = [];
  for (const q of ids) for (const n of document.querySelectorAll(q)) {
    const t = n.innerText || ''; const m = t.match(emoji); if (m) out.push(q + ':' + t.slice(Math.max(0, t.indexOf(m[0]) - 12), t.indexOf(m[0]) + 14).replace(/\\s+/g, ' '));
  }
  return out;
}"""

_B2_PLATES_JS = """() => {
  const w = document.getElementById('scene3dwrap'), cv = document.querySelector('#scene3d canvas');
  if (!w || !cv) return null;
  const a = w.getBoundingClientRect(), c = cv.getBoundingClientRect();
  const box = { l: Math.max(a.left, c.left), r: Math.min(a.right, c.right), t: Math.max(a.top, c.top), b: Math.min(a.bottom, c.bottom) };
  const out = [...document.querySelectorAll('#plates .plate')].map((n) => { const r = n.getBoundingClientRect();
    return { nm: (n.querySelector('.nm') || {}).textContent || '', l: Math.round(r.left), r: Math.round(r.right), t: Math.round(r.top), b: Math.round(r.bottom),
             cut: !!n.querySelector('.nm') && n.querySelector('.nm').scrollWidth > n.querySelector('.nm').clientWidth + 1,
             ok: r.left >= box.l - 1 && r.right <= box.r + 1 && r.top >= box.t - 1 && r.bottom <= box.b + 1 }; });
  return { box: [Math.round(box.l), Math.round(box.t), Math.round(box.r), Math.round(box.b)], plates: out };
}"""


def _b2_pins(page, label, serve, full):
    """B2（スマホの実写の洗い出し・2026-10-01）: (a) 右上はあなた待ちが 4 種類でもオフィス名が潰れず（「s…」にしない）、見えているチップは
    全部「語＋数」（記号だけのチップを作らない）(b) ヘッダー・タブ・ロスター帯・ドック・リスト・シート・設定・リソースの見える文字に
    カラー絵文字が無い (c) 名札は部屋の canvas（∩窓）の中 (d) full の形（390×844）だけ: あなた待ちタブを往復しても部屋の倍率が
    変わらない（×2 のまま）・シートは開き直すと先頭から（前の人のスクロール位置を持ち越さない）。"""
    ng = 0

    def bad(msg):
        nonlocal ng
        print(f"  ✗ B2 {label}: {msg}")
        ng += 1

    q = {"session": "e2e-b2-q", "projectId": "e2e-b2-q", "disp": "senao-qa-long", "state": "waiting", "listening": True,
         "verb": "検討中", "question": "どの案で進めますか?",
         "questionOptions": [{"label": "案A (Recommended)", "desc": "推奨案"}, {"label": "案B", "desc": "別案"}, {"label": "案C", "desc": "保留案"}],
         "work": {"now": ["📋 R102 の監査"], "counts": {"total": 4, "done": 1}}}
    p = {"session": "e2e-b2-p", "projectId": "e2e-b2-p", "disp": "infra-ops", "state": "waiting", "listening": False,
         "verb": "実行中", "approvalMin": 2, "ask": {"tool": "Bash", "kind": "permission"}}
    er = {"session": "e2e-b2-e", "projectId": "e2e-b2-e", "disp": "docs-site", "state": "waiting", "listening": False,
          "verb": "利用上限で停止", "err": {"k": "limit", "ago": 120}}
    idle = {"session": "e2e-b2-i", "projectId": "e2e-b2-i", "disp": "blog-auto", "state": "waiting", "listening": True, "verb": "指示待ち",
            "kind": "said", "age": 300}
    work = {"session": "e2e-b2-w", "projectId": "e2e-b2-w", "disp": "sakutto-editor", "state": "working", "listening": False,
            "verb": "実行中", "target": "bash verify.sh", "kind": "tool", "age": 4,
            "sessions": [{"session": "e2e-b2-w", "state": "working", "age": 4, "attention": False, "minions": 1, "pending": False},
                         {"session": "e2e-b2-w2", "state": "waiting", "age": 90, "attention": False, "minions": 0, "pending": True}]}
    rest = {"session": "e2e-b2-r", "projectId": "e2e-b2-r", "disp": "voice-agent", "state": "resting", "listening": False, "verb": "休憩中"}
    _serve_many(page, serve, [q, p, er, idle, work, rest])
    page.evaluate("() => setView('office', true)")
    page.evaluate("() => { const o = LAST_OFFICE; o.officeName = 'senao-lab'; buildHeader(officeAgents(o)); }")
    page.wait_for_timeout(300)
    ng += _pane_clear(page, label + "・あなた待ち4種類")
    h = page.evaluate(_B2_HEAD_JS)
    if h["nm"] + 1 < min(h["need"], 64) or not h["words"] or not all(h["words"]) or h["over"] or h["right"] > h["hdr"] + 1:
        bad(f"右上: オフィス名が潰れる／語の無いチップ／はみ出し: {h}")
    else:
        print(f"  ✓ B2 {label}: 右上（あなた待ち 4 種類）でもオフィス名「{h['text']}」{h['nm']}px・チップは全部語つき {h['words']}")
    pl = page.evaluate(_B2_PLATES_JS)
    if not pl or not pl["plates"] or not all(x["ok"] for x in pl["plates"]):
        bad(f"名札が部屋の canvas（∩窓）の外に出る: {pl}")
    else:
        print(f"  ✓ B2 {label}: 名札 {len(pl['plates'])} 枚が部屋の canvas の中（{[x['nm'] for x in pl['plates']]}）")
    ids = ['.hdr2', '#tabbar', '#roster', '#dock', '#officewin .wtitle']
    emo = page.evaluate(_B2_EMOJI_JS, ids)
    page.evaluate("() => setView('wait', true)")
    emo += page.evaluate(_B2_EMOJI_JS, ['#wait'])
    page.evaluate("() => setView('list', true)")
    page.wait_for_timeout(300)
    emo += page.evaluate(_B2_EMOJI_JS, ['#list'])
    lst = page.evaluate("""() => ({ gest: document.querySelectorAll('#list .gesture').length,
      saved: [...document.querySelectorAll('#list .recentrow')].map((c) => [(c.querySelector('.recentname') || {}).textContent, (c.querySelector('.dchip') || {}).textContent || '']) })""")
    page.evaluate("() => { const e = officeAgents(LAST_OFFICE).find((x) => x.session === 'e2e-b2-w'); openSheet(e); }")
    page.wait_for_timeout(200)
    emo += page.evaluate(_B2_EMOJI_JS, ['#sheetwrap'])
    rows = page.evaluate("() => [...document.querySelectorAll('#sheetwrap .sessrow')].map((n) => n.textContent)")
    page.evaluate("() => { closeSheet(); openSettings(); }")
    emo += page.evaluate(_B2_EMOJI_JS, ['#setwrap'])
    page.evaluate("() => { closeSettings(); openRes(); }")
    emo += page.evaluate(_B2_EMOJI_JS, ['#reswrap'])
    page.evaluate("() => { closeRes(); setView('office', true); }")
    page.wait_for_timeout(300)
    if emo:
        bad(f"見える文字にカラー絵文字が残る: {emo[:6]}")
    else:
        print(f"  ✓ B2 {label}: ヘッダー・タブ・ロスター帯・ドック・リスト・シート・設定・リソースにカラー絵文字なし")
    rest_chip = [s for n, s in lst["saved"] if n == "voice-agent"]
    if lst["gest"] or rest_chip != [""] or len(rows) != 2 or not any("作業中" in r for r in rows):
        bad(f"リストの右上の記号／休憩中の人の配達札／セッション内訳の行が違う: gesture={lst['gest']} 休憩中={rest_chip} 内訳={rows}")
    else:
        print(f"  ✓ B2 {label}: リストに右上の記号なし・休憩中の人に配達の札なし・セッション内訳は状態の語つき {rows}")
    ng += _r103_sheet_pins(page, label)
    if not full:
        return ng
    # (d) あなた待ちタブから戻っても、部屋の倍率が変わらない（×2 のまま）
    seq = []
    for i in range(4):
        page.wait_for_timeout(450)
        seq.append(page.evaluate("() => window.__scene3d.view.state().roomScale"))
        page.evaluate("() => { setView('wait', true); setView('office', true); }")
    if len(set(seq)) != 1 or seq[0] < 1:
        bad(f"会話ホームを往復すると部屋の整数倍率が変わる: {seq}")
    else:
        print(f"  ✓ B2 {label}: あなた待ちタブを 3 回往復しても部屋は ×{seq[0]} のまま（{seq}）")
    # (e) シートは開き直すと先頭から
    sc = page.evaluate("""async () => {
      const a = officeAgents(LAST_OFFICE).find((x) => x.session === 'e2e-b2-w'), b = officeAgents(LAST_OFFICE).find((x) => x.session === 'e2e-b2-i');
      openSheet(a); const s = document.getElementById('shscroll');
      const pad = document.createElement('div'); pad.style.height = '900px'; document.getElementById('shdetail').appendChild(pad);
      s.scrollTop = s.scrollHeight;
      await new Promise((r) => setTimeout(r, 150)); const was = s.scrollTop; closeSheet();
      await new Promise((r) => setTimeout(r, 150)); openSheet(b); await new Promise((r) => setTimeout(r, 200));
      const h = document.getElementById('shname').getBoundingClientRect(); const out = { was, top: s.scrollTop, name: document.getElementById('shname').textContent,
        visible: h.top >= 0 && h.bottom <= innerHeight && h.height > 0 }; closeSheet(); return out; }""")
    if sc["was"] > 0 and sc["top"] == 0 and sc["visible"] and sc["name"] == "blog-auto":
        print(f"  ✓ B2 {label}: シートは開き直すと先頭から（前は {sc['was']}px まで下げた → {sc['name']} は 0・名前が見える）")
    else:
        bad(f"シートが前の人のスクロール位置のまま開く: {sc}")
    return ng


def _serve_one(page, serve, emp):
    page.evaluate("() => setView('wait', true)")
    serve["status"] = json.dumps({"ok": True, "ts": int(time.time() * 1000), "agentSeenAgo": 0, "agentOnline": True,
                                  "json": page.evaluate("""(emp) => { const o = JSON.parse(JSON.stringify(LAST_OFFICE || {}));
          o.employees = [emp]; delete o.roster; return JSON.stringify(o); }""", emp)}, ensure_ascii=False)
    page.evaluate("() => { try { clearInterval(WS_ST); } catch (_) {} poll(); }")
    try:
        page.wait_for_function("(s) => { const c = document.querySelector('#attncards .attncard');"
                               " if (!!c && c.getAttribute('data-attn-sess') === s) return true;"
                               " return !!APV && APV.session === s && !!document.querySelector('#apvslot_wait > #apvwrap.open, #apvslot_term > #apvwrap.open'); }",
                               arg=emp["session"], timeout=8000)                 # R116-S1: 許可待ちは決裁カード
    except PWTimeout:
        pass
    page.wait_for_timeout(750)                                       # B1: 人が替わった直後 600ms は送る操作を受けない


def _plan_pin(page, label, sent, serve, device_id, secret):
    """(f) 計画の確認（ExitPlanMode）: スマホから承認はできない（中継の回答は deny＋言葉だけ）＝承認ボタンを出さず「承認は Mac で」と
    事実の行（カード・シート・リスト・ログで同じ）。言葉の指示（シートの「いったん停止して報告」）は実タッチで届く。"""
    before = len(sent)
    _serve_one(page, serve, {"session": "e2e-plan-0005", "projectId": "e2e-plan-0005", "disp": "E2E計画部", "state": "waiting",
                             "listening": False, "verb": "計画中", "question": "プランの承認",
                             "ask": {"tool": "ExitPlanMode", "kind": "question"}})
    plan = page.evaluate(
        """async () => {
          const card = document.querySelector('#attncards .attncard');
          const cardBtns = card ? [...card.querySelectorAll('button')].map(n => n.textContent.trim()) : [];
          // B1: 「承認は Mac で」は押せる形でなく文字の行（.attnghost の見出し b）
          const macRows = (root) => root ? [...root.querySelectorAll('.attnghost')].map(n => ((n.querySelector('b') || n).textContent || '').trim()) : [];
          const cardMac = macRows(card);
          const tag = card ? (card.querySelector('.ktag') || {}).textContent || '' : '';
          const q = card ? (card.querySelector('.attnq') || {}).textContent || '' : '';
          const e = officeAgents(LAST_OFFICE)[0];
          openSheet(e);
          const sheetLine = (document.querySelector('#shdetail .planwait') || {}).textContent || '';
          const sheetBtns = [...document.querySelectorAll('#quickbtns button')].map(n => n.textContent.trim());
          const sheetMac = macRows(document.getElementById('quickbtns'));
          const macBtn = !!document.querySelector('#quickbtns button.attnghost, #attncards button.attnghost');
          const savedCanDlg=window.canDlg; let logText=''; window.canDlg=() => false;
          try { openLog(); logText=(document.getElementById('lgbody') || {}).innerText || ''; closeLog(); }
          finally { window.canDlg=savedCanDlg; }
          closeSheet();
          renderList(LAST_OFFICE);
          const lc = [...document.querySelectorAll('#list .recentrow')].find(n => n.getAttribute('data-sess') === e.session);
          return { sess: card ? card.getAttribute('data-attn-sess') : null, cardBtns, sheetBtns, tag, q, sheetLine, logText, cardMac, sheetMac, macBtn,
                   listText: lc ? (lc.querySelector('.recentline') || {}).textContent || '' : '', expectedLine: recentLine(e) }; }""")
    approve = [t for t in plan["cardBtns"] + plan["sheetBtns"] if "✅" in t or t.startswith("承認して")]
    ghosts = plan["cardMac"] + plan["sheetMac"]
    fact_tail = "計画への修正の指示として届きます"
    faces = {"カード": plan["q"], "シート": plan["sheetLine"], "ログ": plan["logText"]}
    if plan["listText"] != plan["expectedLine"]:
        no_line = True
    else:
        no_line = False
    no_fact = [k for k, v in faces.items() if fact_tail not in v]
    ng = 0
    if (plan["sess"] == "e2e-plan-0005" and not approve and ghosts == ["承認は Mac で"] * 2 and not plan["macBtn"] and not no_fact and not no_line
            and "計画の確認" in plan["tag"]):
        print(f"  ✓ R102-1 {label}: 計画の確認に承認ボタンを出さない（「承認は Mac で」・カード/シート/ログが同じ事実・最近は共通規則の1行）")
    else:
        print(f"  ✗ R102-1 {label}: 計画の確認がスマホで承認に見える: 承認={approve} Mac={ghosts} 事実の無い面={no_fact} "
              f"押せる形={plan['macBtn']} tag={plan['tag']!r} sess={plan['sess']}")
        ng += 1
    page.wait_for_timeout(400)
    _tap_center(page, "#attncards .attncard .attnactions .free")
    try:
        page.wait_for_function("() => document.getElementById('sheetwrap').classList.contains('open')", timeout=4000)
    except PWTimeout:
        pass
    page.wait_for_timeout(450)
    b = page.evaluate("""() => { const b = [...document.querySelectorAll('#quickbtns button')].find(n => n.textContent.indexOf('停止') >= 0);
      if (!b) return null; b.scrollIntoView({ block: 'nearest' }); const r = b.getBoundingClientRect();
      const x = r.left + r.width / 2, y = r.top + r.height / 2, hit = document.elementFromPoint(x, y);
      return { x, y, hit: !!hit && (hit === b || b.contains(hit)) }; }""")
    if b and b["hit"]:
        page.touchscreen.tap(b["x"], b["y"])
    got = bool(b and b["hit"]) and _wait_sent(page, sent, before + 1)
    ok, why, sess, text = _verify(sent[before], device_id, secret) if got else (False, "no-request", "", "")
    if ok and sess == "e2e-plan-0005" and text.startswith("いったん作業を止めて"):
        print(f"  ✓ R102-1 {label}: 計画の確認にも言葉の指示（直す指示を書く → 停止）は実タッチで届く")
    else:
        print(f"  ✗ R102-1 {label}: 計画の確認へ言葉の指示が飛ばない: btn={b} ok={ok}({why}) sess={sess} text={text!r}")
        ng += 1
    return ng


def _perm_pin(page, label, sent, serve, device_id, secret):
    """(g) 許可待ち（R116-S1）: あなた待ちの先頭の決裁カード（#apvslot_wait）に名前・「許可待ち」・道具。読み切れない形（remote≠1）は
    「この操作は Mac で許可してください」の 1 行で［許可］も［ON にして確認］も出さない。［拒否］は 2 秒の取り消しの後に拒否の言葉を 1 通
    （Mac のフックが deny＋言葉にする）。"""
    before = len(sent)
    _serve_one(page, serve, {"session": "e2e-perm-0003", "projectId": "e2e-perm-0003", "disp": "E2E許可部", "state": "waiting",
                             "listening": False, "verb": "実行中", "approvalMin": 4, "ask": {"tool": "Bash", "kind": "permission"}})
    # R134: 広い画面で会話の列がその人なら、カードは会話の末尾（#apvslot_term）＝PC と同じ。それ以外は一覧（#apvslot_wait）
    c = page.evaluate("""() => { const card = document.querySelector('#apvslot_wait > #apvwrap.open')
        || (WIDE && TERM && APV && TERM.session === APV.session ? document.querySelector('#apvslot_term > #apvwrap.open') : null);
      if (!card || !APV) return null;
      const a = document.getElementById('apv_allow'), f = document.getElementById('apv_fix'), d = document.getElementById('apvdeny');
      return { sess: APV.session, state: card.getAttribute('data-state'), meta: (document.getElementById('apvmeta') || {}).textContent || '',
               tool: (card.querySelector('.apvtool') || {}).textContent || '', why: (document.getElementById('apvwhy') || {}).textContent || '',
               allow: !!a && !a.hidden, fix: !!f && !f.hidden, deny: d ? d.textContent : null }; }""")
    b = _tap_center(page, "#apvdeny")
    got = bool(b and b["hit"]) and _wait_sent(page, sent, before + 1, ms=6000)
    ok, why, sess, text = _verify(sent[before], device_id, secret) if got else (False, "no-request", "", "")
    if (c and c["sess"] == "e2e-perm-0003" and c["state"] == "mac-only" and "許可待ち" in c["meta"] and "Bash" in c["tool"]
            and c["why"].startswith("この操作は Mac で許可してください") and not c["allow"] and not c["fix"] and c["deny"] == "拒否"
            and ok and sess == "e2e-perm-0003" and text.startswith("この操作は許可しません")):
        print(f"  ✓ R102-1 {label}: 許可待ちのカード（{c['meta'].strip(' ·')}・{c['tool']}・「{c['why']}」・［許可］なし）→［拒否］で 2 秒後に拒否の言葉が 1 通")
        return 0
    print(f"  ✗ R102-1 {label}: 許可待ちのカードが違う: {c} tap={b} ok={ok}({why}) sess={sess} text={text!r}")
    return 1


def _err_pin(page, label, sent, serve, device_id, secret):
    """(h) R102-4 エラーで止まった（利用上限・API エラー）: 右上に「エラー」のチップ（! の印）・カードは「エラーで停止」と理由。
    受信待機していない（StopFailure の後は Stop フックが走らない＝実測）ときは「続けて」を出さず Mac での再開を案内、
    受信待機しているときは「続けて」が 1 通。作業中に戻ったら「あなた待ち」から消える。"""
    ng = 0
    # R102-5: ホーム画面のアイコンのバッジ（Badging API）を記録に差し替える＝あなた待ちの件数で呼ばれ、0 で消える
    page.evaluate("""() => { window.__badges = [];
      navigator.setAppBadge = (n) => { window.__badges.push(n); return Promise.resolve(); };
      navigator.clearAppBadge = () => { window.__badges.push(0); return Promise.resolve(); }; BADGE_N = -1; }""")
    # minions は「15 分以内に更新されたサブエージェントの数」（終わった子も数える）＝それでエラーを隠さない（別モデルレビュー）
    _serve_one(page, serve, {"session": "e2e-err-0006", "projectId": "e2e-err-0006", "disp": "E2E上限部", "state": "waiting",
                             "listening": False, "verb": "利用上限で停止", "err": {"k": "limit", "ago": 180}, "minions": 2})
    badge_err = page.evaluate("() => window.__badges.slice(-1)[0]")
    js = """() => { const card = document.querySelector('#attncards .attncard'); if (!card) return null;
      const chip = document.querySelector('#hstats .ichip[data-kind="error"]');
      return { sess: card.getAttribute('data-attn-sess'), kind: card.getAttribute('data-kind'),
               tag: (card.querySelector('.ktag') || {}).textContent || '', q: (card.querySelector('.attnq') || {}).textContent || '',
               head: (card.querySelector('.attnkind') || {}).textContent || '',
               btns: [...card.querySelectorAll('.attnactions button')].map(n => n.textContent.trim()),
               chip: chip ? chip.textContent.trim() : null, glyph: chip ? ((chip.querySelector('.px') || { getAttribute: () => null }).getAttribute('data-px')
                                || (chip.querySelector('.ig') || {}).textContent) : null }; }"""
    c = page.evaluate(js)
    if (c and c["sess"] == "e2e-err-0006" and c["kind"] == "error" and "エラーで停止" in c["tag"] and "利用上限" in c["q"]
            and "Mac のターミナルで「続けて」" in c["q"] and "続けて" not in c["btns"] and c["glyph"] == "bang"
            and c["chip"] and "エラー" in c["chip"] and c["head"] == "利用上限・3分前から"):
        print(f"  ✓ R102-4 {label}: 利用上限で止まったカード（{c['tag']}・{c['head']}）・右上「{c['chip']}」・受信待機なし＝「続けて」を出さず Mac での再開を案内")
    else:
        print(f"  ✗ R102-4 {label}: 止まった理由のカードが違う: {c}")
        ng += 1
    # 経過の分が変われば見出しも変わる（err.ago を再描画の判定に入れる・別モデルレビュー）
    _serve_one(page, serve, {"session": "e2e-err-0006", "projectId": "e2e-err-0006", "disp": "E2E上限部", "state": "waiting",
                             "listening": False, "verb": "利用上限で停止", "err": {"k": "limit", "ago": 660}, "minions": 2})
    later = page.evaluate("() => (document.querySelector('#attncards .attncard .attnkind') || {}).textContent || ''")
    if later == "利用上限・11分前から":
        print(f"  ✓ R102-4 {label}: 止まってからの時間が進めばカードの見出しも進む（{later}）")
    else:
        print(f"  ✗ R102-4 {label}: 止まってからの時間が描き直されない（{later!r}）")
        ng += 1
    before = len(sent)
    _serve_one(page, serve, {"session": "e2e-err-0007", "projectId": "e2e-err-0007", "disp": "E2E障害部", "state": "waiting",
                             "listening": True, "verb": "API エラーで停止", "err": {"k": "api", "ago": 20}})
    c = page.evaluate(js)
    b = _tap_center(page, "#attncards .attncard .attnactions .g")
    got = bool(b and b["hit"]) and _wait_sent(page, sent, before + 1)
    ok, why, sess, text = _verify(sent[before], device_id, secret) if got else (False, "no-request", "", "")
    if c and c["kind"] == "error" and "続けて" in c["btns"] and ok and sess == "e2e-err-0007" and text == "続けてください。":
        print(f"  ✓ R102-4 {label}: API エラーで止まった・受信待機あり →「続けて」で 1 通（{text}）")
    else:
        print(f"  ✗ R102-4 {label}: API エラーのカードの「続けて」が違う: {c} tap={b} ok={ok}({why}) sess={sess} text={text!r}")
        ng += 1
    _serve_one(page, serve, {"session": "e2e-err-0008", "projectId": "e2e-err-0008", "disp": "E2E復帰部", "state": "working",
                             "listening": False, "verb": "実行中", "err": {"k": "api", "ago": 20}})
    gone = page.evaluate("""() => ({ card: !!document.querySelector('#attncards .attncard'),
                                     chip: !!document.querySelector('#hstats .ichip[data-kind="error"]'),
                                     badge: window.__badges.slice(-1)[0] })""")
    if not gone["card"] and not gone["chip"]:
        print(f"  ✓ R102-4 {label}: 作業中に戻ったら「あなた待ち」とエラーのチップから消える")
    else:
        print(f"  ✗ R102-4 {label}: 作業中なのにエラーとして残る: {gone}")
        ng += 1
    # 裏で通知の SW がバッジを 1 にした後、画面に戻ったら付け直す（ページの記憶が 0 のまま消えなかった・別モデルレビュー）
    back = page.evaluate("""() => { navigator.setAppBadge(1); const n = window.__badges.length;
      document.dispatchEvent(new Event('visibilitychange')); return window.__badges.slice(n); }""")
    if badge_err == 1 and gone["badge"] == 0 and back == [0]:
        print(f"  ✓ R102-5 {label}: ホーム画面のバッジ＝あなた待ちの件数（1 → 作業中に戻って 0・SW が変えた後も画面に戻れば付け直す）")
    else:
        print(f"  ✗ R102-5 {label}: バッジの件数が違う（エラー 1 件のとき {badge_err}・作業中に戻って {gone['badge']}・戻ったとき {back}）")
        ng += 1
    return ng


def _play_pin(page, label, serve):
    """(i) R102-6 遊び心（端末の言い回し）: あなた待ちが空なら INBOX ZERO の 1 行（いまの様子の数）・15 分以上離れてから開いたら
    下の行に Last login（その間に終わった数・新しいあなた待ち）・中継に届かないときは NO CARRIER。"""
    ng = 0
    emps = [{"session": "e2e-play-0001", "projectId": "e2e-play-0001", "disp": "E2E稼働部", "state": "working", "listening": True,
             "verb": "実行中", "kind": "tool", "age": 3},
            {"session": "e2e-play-0002", "projectId": "e2e-play-0002", "disp": "E2E待機部", "state": "waiting", "listening": True,
             "verb": "指示待ち", "kind": "said", "age": 400}]
    serve["status"] = json.dumps({"ok": True, "ts": int(time.time() * 1000), "agentSeenAgo": 0, "agentOnline": True,
                                  "json": page.evaluate("""(emps) => { const o = JSON.parse(JSON.stringify(LAST_OFFICE || {}));
          o.employees = emps; delete o.roster; return JSON.stringify(o); }""", emps)}, ensure_ascii=False)
    page.evaluate("() => { try { clearInterval(WS_ST); } catch (_) {} poll(); }")
    try:
        page.wait_for_function("() => { const z = document.getElementById('inboxzero');"
                               " return !!z && !z.hidden && z.textContent.indexOf('指示待ち 1') >= 0; }", timeout=8000)
    except PWTimeout:
        pass
    # R122-P2: 空のあなたの番はホームの件数で分かり、最近の全員は残る。
    page.evaluate("() => setView('wait', true)")
    page.wait_for_timeout(250)
    z = page.evaluate("""() => ({
      title:document.getElementById('wait_title').textContent,
      cards:document.querySelectorAll('#attncards .attncard,#attncards .apvrow').length,
      recent:document.querySelectorAll('#list .recentrow').length,
      count:document.getElementById('tb_wait_count').textContent })""")
    if z['title']=='あなたの番 0' and z['cards']==0 and z['recent']==2 and z['count']=='0':
        print(f"  ✓ R122-P2 {label}: あなたの番0件でも最近の2人を表示")
    else:
        print(f"  ✗ R122-P2 {label}: あなたの番0件のホームが違う: {z}")
        ng+=1
    # Last login: 2 時間前に「e2e-play-0002 は作業中・e2e-play-0001 は指示待ち」だった → 終わった 1（0002）・新しいあなた待ち 0
    login = page.evaluate("""() => {
      localStorage.setItem('aioffice.lastSeen', JSON.stringify({ t: Date.now() - 2 * 3600 * 1000,
        k: { 'e2e-play-0002': 'working', 'e2e-play-0001': 'idle' } }));
      LOGIN.prev = loginRead();                                      // B1: 戻った時点で退避した lastSeen を読む
      LOGIN.checked = false; LOGIN.fresh = true; LOGIN.msg = null; paintTicker(false);
      const tk = document.getElementById('ticker'); const text = tk ? tk.textContent : '';
      tk.click(); const after = tk.hasAttribute('data-login'), resource = document.getElementById('reswrap').classList.contains('open');
      closeRes(); return { text, after, resource }; }""")
    if login["text"].startswith("Last login: ") and "終わった 1" in login["text"] and "あなた待ち +0" in login["text"] \
            and "Mac " in login["text"] and not login["after"] and login["resource"]:
        print(f"  ✓ R102-6/R104 {label}: Last loginは同期と同じ行（押すと消えてリソースが開く）「{login['text']}」")
    else:
        print(f"  ✗ R102-6 {label}: Last login の行が違う: {login}")
        ng += 1
    nc = page.evaluate("""() => { conn(false); const b = document.getElementById('banner'); const t = b ? b.textContent : '';
      conn(true); return t; }""")
    if nc.startswith("NO CARRIER") and "中継につながりません" in nc:
        print(f"  ✓ R102-6 {label}: 中継に届かないとき「{nc}」")
    else:
        print(f"  ✗ R102-6 {label}: 中継に届かないときの表示が違う: {nc!r}")
        ng += 1
    # B2: Mac が同期していない（中継には届く）ときも、何が・いつから・送った言葉がどうなるかを見える文字で（title の中だけにしない）
    st = page.evaluate("""() => { updateStale(Date.now(), 200, false); const b = document.getElementById('banner');
      const r = b ? b.getBoundingClientRect() : null; const t = b ? b.textContent : '';
      updateStale(Date.now(), 0, true); return { t, h: r ? Math.round(r.height) : 0 }; }""")
    if st["t"].startswith("NO CARRIER") and "Mac に届きません（3分前から）" in st["t"] and "Mac が戻ったら届きます" in st["t"] and st["h"] > 0:
        print(f"  ✓ B2 {label}: Mac が同期していないとき「{st['t'][:44]}…」")
    else:
        print(f"  ✗ B2 {label}: Mac が同期していないときの帯が違う: {st}")
        ng += 1
    return ng


def _long_label_pin(page, label, sent, serve, device_id, secret):
    """長いラベル（40 字 × 4・60 字 × 4＝前半が共通で末尾の語だけ違う組）: 全文を折り返して切らず、ドックの中で全部見えて当たる
    （見えない分はドックをスクロールして届く）。最後の 1 件の 1 タップ → 2 秒で、その全文の答えがちょうど 1 通。"""
    ng = 0
    for sess, q, opts in (LONG_OPTION_SETS[0], LONG_OPTION_SETS[4]):
        _serve_one(page, serve, {"session": sess, "projectId": sess, "disp": "E2E長文部", "state": "waiting", "listening": False,
                                 "verb": "検討中", "question": q, "questionOptions": opts})
        r = page.evaluate(_OPTS_JS)
        bad = []
        if r["sess"] is None or not r["sess"].startswith(sess) or len(r["opts"]) != len(opts):
            bad.append(f"対象・件数が違う（sess={r['sess']}・{len(r['opts'])}/{len(opts)} 件）")
        for o, want in zip(r["opts"], opts):
            if not o["ok"]:
                bad.append(f"見えない／当たらない「…{want['label'][-8:]}」y={o['y']} at={o['at']}")
            if o["label"] != want["label"] or not o["wraps"] or o.get("clip"):
                bad.append(f"ラベルが全文で出ない「…{want['label'][-8:]}」（表示「…{o['label'][-8:]}」・折り返し={o['wraps']}）")
        before = len(sent)
        _tap_center(page, "#attncards .attncard .attnoptions .qopt:last-child")
        got = _wait_sent(page, sent, before + 1, ms=5000)
        ok, why, s2, text = _verify(sent[before], device_id, secret) if got else (False, "no-request", "", "")
        page.wait_for_timeout(400)
        want_text = f"選択肢「{opts[-1]['label']}」でお願いします。"
        if not (ok and s2 == sess and text == want_text and len(sent) == before + 1):
            bad.append(f"最後の 1 件を押して 2 秒で全文の答えが 1 通でない（ok={ok}({why})・通数 {len(sent) - before}・{text[:30]!r}）")
        if bad:
            at = _shot(page, f"pwa3d_long_{(page.viewport_size or {}).get('width')}_{sess[-4:]}.png")
            print(f"  ✗ R102-1 {label}: 長いラベル {len(opts)} 件（{sess}）: " + "／".join(bad) + (f"（画面= {at}）" if at else ""))
            ng += 1
        else:
            print(f"  ✓ R102-1 {label}: 長いラベル {len(opts)} 件（{sess}）が全文でドックの中に見えて当たり、1 タップ → 2 秒で全文の答えが 1 通")
    return ng


# ── B1（スマホ監査の修正・2026-10-01）: 最初の形状でだけ見る ────────────────────────────────────────────────
#   (1) R103-2a: あなた待ちは全件の縦一覧。新しい質問が割り込んでも既存のカードを失わない
#   (2) 並びの更新後も、押したカード本人へ答える（先頭へ割り込んだ別の人へ送らない）
#   (3) 「✓ 送信しました」は要求ごと: 同じプロジェクトに次の許可（分が戻った）・別の質問が来たら持ち越さない
#   (4) 作業中・指示待ちのシートに「承認して進める」が 0 個・「停止」は塗らない（背景が透明）。minions だけでは作業中にしない
#   (5) 状態の語と文: 右上の札・ロスターのチップ・リストの札が同じ語、カードの見出し＝リストの 2 行目、吹き出しが質問を「止まっています」と言わない
#   (6) 2 問以上・複数選択の質問は 1 タップで送らない（選択肢は見せるだけ）
#   (7) 選択肢の 2 秒の取り消し中に画面が隠れたら送らない
#   (8) リスト表示でも Last login が同じ状態の行に出る（R104）
def _serve_many(page, serve, emps, want=None):
    page.evaluate("() => setView('wait', true)")
    serve["status"] = json.dumps({"ok": True, "ts": int(time.time() * 1000), "agentSeenAgo": 0, "agentOnline": True,
                                  "json": page.evaluate("""(emps) => { const o = JSON.parse(JSON.stringify(LAST_OFFICE || {}));
          o.employees = emps; delete o.roster; return JSON.stringify(o); }""", emps)}, ensure_ascii=False)
    page.evaluate("() => { try { clearInterval(WS_ST); } catch (_) {} poll(); }")
    try:
        page.wait_for_function("""(n) => officeAgents(LAST_OFFICE).length === n.n
            && (!n.want || [...document.querySelectorAll('#attncards .attncard, #attncards .apvrow')].some(c => c.getAttribute('data-attn-sess') === n.want)
                || (!!APV && APV.session === n.want && !!document.querySelector('#apvslot_wait > #apvwrap.open')))""",
                               arg={"n": len(emps), "want": want}, timeout=8000)
    except PWTimeout:
        pass
    page.wait_for_timeout(750)


def _r104_polish_pins(page, label, sent, serve, device_id, secret):
    """R104-1: 残り8件。手元の許可文脈・更新待ち・入口1つ・定型1列・×2・名札の語・個別返信・状態1行。
    /instruct は呼び出し側の route が受け止める。時刻は必要な小区間だけ進め、最後に必ず戻す。"""
    ng = 0

    def check(ok, subject, detail):
        nonlocal ng
        if ok:
            print(f"  ✓ R104-1 {label}: {subject}")
        else:
            print(f"  ✗ R104-1 {label}: {subject}: {detail}")
            ng += 1

    q = {"session": "e2e-r104-q", "projectId": "r104-q", "disp": "Question Desk", "state": "waiting",
         "listening": True, "question": "どちらの手順で進めますか?",
         "questionOptions": [{"label": "案A"}, {"label": "案B"}, {"label": "案C"}]}
    work = {"session": "e2e-r104-w", "projectId": "r104-w", "disp": "Work Desk", "state": "working",
            "kind": "tool", "listening": False, "age": 3, "mtime": 1000, "verb": "検証中"}
    idle = {"session": "e2e-r104-i", "projectId": "r104-i", "disp": "Idle Desk", "state": "waiting",
            "kind": "said", "listening": True, "age": 400, "verb": "指示待ち"}
    _serve_many(page, serve, [q, work, idle])
    page.evaluate("() => { closeSheet(); closeMini(); closeTerm(true); setView('office', true); fitAttnDock(); }")
    page.wait_for_timeout(250)
    room = page.evaluate(_ROOM_ALL_JS)
    clear = page.evaluate(_PANE_CLEAR_JS)
    fit = page.evaluate("""() => ({
      room:document.getElementById('officewin').getBoundingClientRect().bottom,
      roster:document.getElementById('topdock').getBoundingClientRect().top,
      cards:document.querySelectorAll('#room .attncard,#room #apvwrap').length })""")
    check(bool(room and room["S"] >= 1 and room["n"] == 3 and room["ok"] == 3 and not room["bad"]
               and clear["ok"] and fit["roster"] >= fit["room"] - 1 and fit["cards"] == 0),
          "390×844は全員が部屋に見える・下に人の一覧・許可と質問はホーム", (room, clear, fit))

    # R116-S1: 許可の先頭の「AI の直前の 1 行」（apvcontext）は撤去＝カードは名前・待ち時間・道具・命令だけ。端末の描画は許可の本文
    # （#apvcanon＝署名の本文）に混ざらず、画像も作らない（R104-1 の XSS の番は残す）。
    context = page.evaluate("""() => {
      const saved = { term:TERM, apv:APV, request:window.termRequest, canDlg:window.canDlg };
      const e = { session:'e2e-r104-context', disp:'許可文脈', state:'waiting', listening:true,
        ask:{ kind:'permission', tool:'Bash' }, lastSaid:'手元の要約です' };
      const canon = JSON.stringify({ input:{ command:'printf ok' } });
      try {
        window.termRequest = () => {}; window.canDlg = () => true; openTerm(e);
        termPaint({ reset:true, rows:[{ k:'a', t:'前の返答' }, { k:'u', t:'検証を進めて' },
          { k:'a', t:'最後の返答 <img src=x onerror="window.__r104Xss=1">\\n次の行' }, { k:'tool', n:'Bash', t:'printf ok' }] });
        const cached = TERM.rows.map(r => r.k);
        APV = { session:e.session, e, page:{ inputCanon:canon, tool:'Bash', level:'unavailable' } }; apvRender();
        const card = document.getElementById('apvwrap');
        return { cached, ctx:!!document.getElementById('apvcontext'), text:(card && card.textContent) || '',
          canon:document.getElementById('apvcanon').textContent, images:card ? card.querySelectorAll('img').length : -1,
          xss:!!window.__r104Xss, want:canon };
      } finally { closeApprove(true); closeTerm(true); TERM=saved.term; APV=saved.apv; window.termRequest=saved.request; window.canDlg=saved.canDlg; }
    }""")
    check(context["cached"] == ["a", "u", "a", "tool"] and not context["ctx"] and "最後の返答" not in context["text"]
          and "手元の要約です" not in context["text"] and context["canon"] == context["want"] and context["images"] == 0
          and not context["xss"],
          "許可のカードに AI の直前の行を出さない（R116-S1 で撤去）・本文は署名の本文のまま・画像を作らない", context)

    cadence = page.evaluate("""() => {
      const saved = { term:TERM, now:Date.now, timeout:window.setTimeout, office:LAST_OFFICE, canTermFor:window.canTermFor };
      const base = Date.now(), e = { session:'e2e-r104-cadence', state:'working', kind:'tool', age:3, mtime:1000 };
      const delays = [];
      try {
        TERM={ session:e.session, e, opAt:base, timer:null, requestSig:termSig(e), req:null };
        window.canTermFor=() => true;
        Date.now=() => base; const first=termDelay(); Date.now=() => base+6*60*1000;
        window.setTimeout=(fn, ms) => { delays.push(ms); return 0; }; const six=termDelay(); termSchedule();
        TERM.e=Object.assign({}, e, { state:'waiting', listening:true }); TERM.requestSig=termSig(TERM.e);
        const n=delays.length; termSchedule(); const stopped=delays.length===n;
        const old=termSig(TERM.e), changed=termSig(Object.assign({}, TERM.e, { mtime:1001 }));
        const aged=termSig(Object.assign({}, TERM.e, { age:15 }));
        LAST_OFFICE=Object.assign({}, LAST_OFFICE, { relay:{ level:1 } }); termRequest();
        return { first, six, working:delays[0], stopped, mtime:old!==changed, age:old!==aged,
          throttle:delays[delays.length-1], note:document.getElementById('termnote').textContent };
      } finally { TERM=saved.term; Date.now=saved.now; window.setTimeout=saved.timeout; LAST_OFFICE=saved.office; window.canTermFor=saved.canTermFor; }
    }""")
    wait = page.evaluate("""async () => {
      const saved = { now:Date.now, fetch:window.fetch, poll:window.poll, canDlg:window.canDlg, office:LAST_OFFICE };
      const requests=[], base=Date.now();
      try {
        window.canDlg=() => true; window.poll=() => {};
        LAST_OFFICE=Object.assign({}, LAST_OFFICE, { relay:{ level:0 } });
        window.fetch=(url, opts) => { if (url==='/instruct') { requests.push(JSON.parse(opts.body));
          return Promise.resolve({ json:() => Promise.resolve({ ok:true }) }); } return saved.fetch(url,opts); };
        openTerm({ session:'e2e-r104-wait', disp:'更新待ち', state:'working', kind:'tool', feed:[] });
        for(let i=0;i<100 && !requests.length;i++) await new Promise(r => setTimeout(r,10));
        const old=TERM.req, reqAt=TERM.reqAt; Date.now=() => reqAt+1000; termWaitTick(old);
        const one=document.getElementById('termnotetext').textContent;
        const hidden=document.getElementById('termretry').hidden;
        Date.now=() => reqAt+21000; termWaitTick(old);
        const twenty=document.getElementById('termnotetext').textContent;
        const shown=!document.getElementById('termretry').hidden;
        document.getElementById('termretry').click();
        for(let i=0;i<100 && requests.length<2;i++) await new Promise(r => setTimeout(r,10));
        const r={ one, hidden, twenty, shown, count:requests.length,
          replaced:!!TERM.req && TERM.req!==old, oldGone:!dlgPending()[old], kind:requests.map(x => JSON.parse(x.text).kind) };
        return r;
      } finally { closeTerm(true); Date.now=saved.now; window.fetch=saved.fetch; window.poll=saved.poll;
        window.canDlg=saved.canDlg; LAST_OFFICE=saved.office; }
    }""")
    check(cadence["first"] <= 5000 and cadence["six"] <= 5000 and cadence["working"] <= 5000
          and cadence["stopped"] and cadence["mtime"] and cadence["age"] and cadence["throttle"] >= 60000
          and "中継" in cadence["note"] and "1 秒" in wait["one"] and wait["hidden"] and "21 秒" in wait["twenty"]
          and wait["shown"] and wait["count"] == 2 and wait["replaced"] and wait["oldGone"] and wait["kind"] == ["term", "term"],
          "時計を6分進めても作業中は5秒・静止中は変化時だけ・mtime/ageを検知・要求の経過秒と20秒後の再試行", (cadence, wait))

    sheet = page.evaluate("""async () => {
      const saved={ canDlg:window.canDlg, canTermFor:window.canTermFor, office:LAST_OFFICE };
      const e={ session:'e2e-r104-sheet', disp:'定型テスト', state:'working', feed:['検証しています'] };
      const inspect=() => ({ term:document.querySelectorAll('#sheetwrap #sh_term').length,
        dialog:document.querySelectorAll('#sheetwrap #sh_dlg').length,
        label:(document.getElementById('sh_term')||document.getElementById('sh_dlg')||{}).textContent || '' });
      try {
        window.canDlg=() => true; window.canTermFor=() => true; openSheet(e); const on=inspect();
        const feedbox=document.querySelector('#shdetail .feedbox'), feed=(feedbox&&feedbox.previousElementSibling||{}).textContent || '';
        window.canTermFor=() => false; openSheet(e); const compat=inspect();
        window.canDlg=() => false; openSheet(e); const off=inspect();
        LAST_OFFICE=Object.assign({}, LAST_OFFICE, { templates:Array.from({ length:8 }, (_,i) => ({ label:'定型を確認 '+i, text:'検証 '+i })) });
        openSheet(e); await new Promise(r => setTimeout(r,180));
        const row=document.getElementById('sheettemplates'), nodes=[...row.querySelectorAll('button.tpl')], cs=getComputedStyle(row);
        row.scrollLeft=row.scrollWidth; nodes[nodes.length-1].scrollIntoView({ block:'nearest', inline:'nearest' });
        const r=row.getBoundingClientRect(), last=nodes[nodes.length-1].getBoundingClientRect();
        const hit=document.elementFromPoint(last.left+last.width/2,last.top+last.height/2);
        const sh=document.querySelector('#sheetwrap .sheet').getBoundingClientRect(), field=document.getElementById('freetext').getBoundingClientRect();
        return { on, compat, off, feed, count:nodes.length, nowrap:cs.flexWrap==='nowrap', overflow:cs.overflowX,
          wider:row.scrollWidth>row.clientWidth, heights:nodes.map(n => n.getBoundingClientRect().height),
          tops:nodes.map(n => n.getBoundingClientRect().top), last:last.left>=r.left-1&&last.right<=r.right+1,
          hit:hit===nodes[nodes.length-1]||nodes[nodes.length-1].contains(hit),
          stacked:document.querySelectorAll('#quickbtns > button.tpl').length,
          sheetH:sh.height, viewport:innerHeight, field:field.top>=sh.top&&field.bottom<=innerHeight+1 };
      } finally { closeSheet(); window.canDlg=saved.canDlg; window.canTermFor=saved.canTermFor; LAST_OFFICE=saved.office; }
    }""")
    check(sheet["on"]["term"] == 1 and sheet["on"]["dialog"] == 0 and sheet["on"]["label"] == "セッションを開く"
          and sheet["compat"]["term"] == 0 and sheet["compat"]["dialog"] == 1
          and sheet["off"]["term"] == 1 and sheet["off"]["dialog"] == 0 and "タップでターミナル" in sheet["feed"],
          "同じ人の会話入口は1つ・OFFでも要約のセッションを開ける", sheet)
    check(sheet["count"] == 8 and sheet["nowrap"] and sheet["overflow"] in ("auto", "scroll") and sheet["wider"]
          and sheet["stacked"] == 0 and all(h >= 44 for h in sheet["heights"])
          and max(sheet["tops"]) - min(sheet["tops"]) <= 1 and sheet["last"] and sheet["hit"]
          and sheet["sheetH"] <= sheet["viewport"] and sheet["field"],
          "定型8件は44pxの横1列・末尾まで届く・シートは画面内で返信欄が見える", sheet)

    perm = {"session": "e2e-r104-p", "projectId": "r104-p", "disp": "Permission Desk", "state": "waiting",
            "listening": True, "ask": {"kind": "permission", "tool": "Bash"}, "approvalMin": 5}
    _serve_many(page, serve, [q, perm, idle, work])
    page.evaluate("() => setView('office', true)")
    page.wait_for_timeout(300)
    plates = page.evaluate("""() => { paintPlates(); const st = window.__scene3d.stats(); return { robots:st.robots - (st.leaving || 0),   // 前の場面から出ていく途中のロボは名札の対象外
      pins:document.querySelectorAll('#plates .pin').length, rows:Object.entries(PLATE_NODES).map(([id,n]) => {
        const e=empOfAgent(id), k=e?sessKind(e):'', p=n.querySelector('.pkind'), w=KIND_S[k]||KIND_W[k]||KIND_W.rest;
        return { text:n.classList.contains('plate'), kind:p&&p.getAttribute('data-kind'), want:k,
          word:p&&p.textContent, expected:T(w[0],w[1]), name:(n.querySelector('.nm')||{}).textContent || '' }; }) }; }""")
    # R133: 名札は「選んだ人＋あなたの番」だけ・最大 4 枚（Mac の R124 と同じ決まり。全員ぶん置くと実データの 20 人で部屋が札に埋まった）。
    check(plates["pins"] == 0 and plates["robots"] == 4 and len(plates["rows"]) == 2
          and all(r["text"] and r["kind"] == r["want"] and r["word"] == r["expected"] and r["name"] for r in plates["rows"])
          and {r["word"] for r in plates["rows"]} == {"許可", "質問"},
          "名札はあなたの番の 2 人だけ（ロボは 4 体）・sessKind と同じ短い状態語（許可・質問）", plates)
    picked = page.evaluate("""() => { const names = () => Object.values(PLATE_NODES).map(n => (n.querySelector('.nm') || {}).textContent || '');
      const idle = officeAgents(LAST_OFFICE).find(e => e.session === 'e2e-r104-i') || officeAgents(LAST_OFFICE).find(e => !needsAttn(e));
      openMini(idle); paintPlates(); const sel = { n: names().length, has: !!document.querySelector('#plates .plate.sel'),
        selAttn: !!document.querySelector('#plates .plate.sel.attn') };
      closeMini(); paintPlates(); return { sel, after: names().length }; }""")
    check(picked["sel"]["n"] == 3 and picked["sel"]["has"] and not picked["sel"]["selAttn"] and picked["after"] == 2,
          "あなたの番でない人も、選ぶと名札が付く（朱ではない）・選択を外すと消える", picked)
    crowd = [dict(perm, session=f"e2e-r133-p{i}", projectId=f"r133-p{i}", disp=f"Permission Desk {i}", approvalMin=5 + i) for i in range(6)]
    _serve_many(page, serve, crowd + [idle, work])
    page.evaluate("() => setView('office', true)")
    page.wait_for_timeout(300)
    many = page.evaluate("""() => { paintPlates(); const r = [...document.querySelectorAll('#plates .plate')].map(n => n.getBoundingClientRect());
      let overlap = 0;
      for (let i = 0; i < r.length; i++) for (let j = i + 1; j < r.length; j++)
        if (!(r[i].right <= r[j].left || r[j].right <= r[i].left || r[i].bottom <= r[j].top || r[j].bottom <= r[i].top)) overlap++;
      return { plates: r.length, attn: officeAgents(LAST_OFFICE).filter(needsAttn).length, robots: window.__scene3d.stats().robots, overlap,
               allAttn: [...document.querySelectorAll('#plates .plate')].every(n => n.classList.contains('attn')) }; }""")
    check(many["plates"] == 4 and many["attn"] == 6 and many["allAttn"] and many["overlap"] == 0,
          "あなたの番が 6 人でも名札は 4 枚まで・互いに重ならない", many)
    # R127: 出勤の口の Bot（中継に載る形＝relay_agent の Bot 用の allowlist）。会話（dialog）・起動・定型の入口を出さない・相談には答えられる・
    # Bot が書いた文（名前・問い・選択肢）は文字として描く（HTML にしない）。
    # R128 #3 で約束を書き換えた: 記録（Mac が口の記録を端末の行にして封じる kind:"term"）は開ける＝一覧の行は記録の画面を開く・
    # シートの入口は「記録を見る」（セッションと呼ばない）。封書と描画の往復は _r128_pins。
    poison = '<img src=x onerror="window.__r127_pwned=1">'
    bot_q = {"session": "bot-mailbox", "vendor": "bot", "dept": "郵便受け" + poison, "disp": "郵便受け" + poison, "role": "受信箱",
             "state": "waiting", "kind": "said", "verb": "相談の答え待ち", "age": 400, "pending": False, "listening": True, "approvalMin": 0,
             "question": "請求書を送ってよい？" + poison, "questionOptions": [{"label": "送る" + poison, "desc": ""}, {"label": "明日にする", "desc": ""}],
             "ask": {"tool": "office_ask", "kind": "question", "remote": 0, "min": 6, "id8": "0013042d"}}
    bot_rest = {"session": "bot-nippou", "vendor": "bot", "dept": "日報の係", "disp": "日報の係", "role": "夕方", "projectId": "bot-nippou",
                "state": "resting", "kind": "idle", "verb": "休憩中", "age": 3600, "pending": False, "listening": True, "approvalMin": 0, "question": ""}
    _serve_many(page, serve, [bot_q, bot_rest, work], want="bot-mailbox")
    bots = page.evaluate("""async () => { const wait = (ms) => new Promise((r) => setTimeout(r, ms));
      const saved = { canDlg: window.canDlg, canAct: window.canAct, termRequest: window.termRequest };
      // 会話も遠隔の起動も使える端末で、Bot にだけ出ないことを見る（記録の封書の要求はここでは送らない＝往復は _r128_pins）
      window.canDlg = () => true; window.canAct = () => true; window.termRequest = () => {};
      try {
        const q = sessionAgent('bot-mailbox'), r = sessionAgent('bot-nippou');
        const out = { isBot: isBot(q) && isBot(r) && !isBot(sessionAgent('e2e-r104-w')), term: canTermFor(q) && canTermFor(r), dlg: canDlgFor(q) || canDlgFor(r),
                      quick: quickForSheet(q).length + quickForSheet(r).length + quickForCard(q).length };
        setView('wait', true); await wait(150);
        const row = document.querySelector('#list .recentrow[data-sess="bot-nippou"]'); if (row) row.click(); await wait(300);
        const sheet = document.getElementById('sheetwrap');
        out.rowOpensRecord = !!row && !sheet.classList.contains('open') && !!TERM && TERM.session === 'bot-nippou';
        closeTerm(true); openSheet(r); await wait(300);
        const labels = () => [...sheet.querySelectorAll('button')].filter((b) => b.getClientRects().length).map((b) => b.textContent.trim());
        out.rest = labels(); out.restPh = document.getElementById('freetext').placeholder;
        closeSheet(); openSheet(q); await wait(300);
        out.ask = labels(); out.askPh = document.getElementById('freetext').placeholder;
        out.name = (document.getElementById('shname') || {}).textContent || '';
        out.img = document.querySelectorAll('#sheetwrap img, #attncards img, #list img, #roster img').length;
        out.pwned = window.__r127_pwned === 1;
        out.card = (document.querySelector('#attncards .attncard[data-attn-sess] .attnq') || {}).textContent || '';
        closeSheet();
        // 別モデルレビュー（medium）: 長すぎる答えは印（id8）を外して送らずに止める（外すと相談中の Bot に断られて答えが失われる）
        const calls = [], realFetch = window.fetch;
        window.fetch = (u, o) => { if (String(u).indexOf('/instruct') >= 0) { calls.push(1); return Promise.resolve(new Response('{"ok":true}', { status: 200 })); } return realFetch(u, o); };
        try { out.longSent = await send('あ'.repeat(3990), q); out.longCalls = calls.length; out.longNote = (document.getElementById('note') || {}).textContent || ''; out.sending = SENDING; }
        finally { window.fetch = realFetch; }
        return out;
      } finally { closeTerm(true); window.canDlg = saved.canDlg; window.canAct = saved.canAct; window.termRequest = saved.termRequest; } }""")
    banned = ("セッションを開く", "会話を見る", "このプロジェクトを起動", "いったん停止して報告", "進捗を1行で報告", "承認して進める")
    check(bots["isBot"] and bots["term"] and not bots["dlg"] and bots["quick"] == 0 and bots["rowOpensRecord"]
          and not any(w in lb for lb in bots["rest"] + bots["ask"] for w in banned)
          and "記録を見る" in bots["rest"] and "記録を見る" in bots["ask"]
          and any(lb.startswith("送る") for lb in bots["ask"]) and any(lb.startswith("明日にする") for lb in bots["ask"])
          and "Bot" in bots["restPh"] and bots["askPh"] == "答えを書く",
          "R127→R128 Bot: 一覧の行は記録の画面を開く・シートの入口は「記録を見る」・セッション／会話／起動／定型の入口は出ない・相談の選択肢は出る", bots)
    check(bots["longSent"] is False and bots["longCalls"] == 0 and "長すぎ" in bots["longNote"] and not bots["sending"],
          "R127 Bot への長すぎる答えは、印を外して送らずに止める（書いた文は残る・送信は 0 回）", bots)
    check(bots["img"] == 0 and not bots["pwned"] and poison in bots["name"] and (not bots["card"] or poison in bots["card"]),
          "R127 Bot が書いた文（名前・問い・選択肢）は文字として描く（HTML の要素にならない）", bots)
    _serve_many(page, serve, [q, perm, idle, work])
    page.evaluate("() => setView('office', true)")
    page.wait_for_timeout(300)

    workers = [dict(work, session=f"e2e-r104-w{i:02d}", projectId=f"r104-w{i:02d}", disp=f"Work Desk {i}") for i in range(10)]
    _serve_many(page, serve, workers + [idle])
    page.evaluate("() => { setView('office', true); document.querySelector('#hstats .ichip[data-kind=idle]').click(); }")
    page.wait_for_timeout(200)
    inline = page.evaluate("""() => { const n=document.querySelector('#list .recentrow[data-kind=idle]');
      n.scrollIntoView({block:'center'}); const r=n.getBoundingClientRect();
      return { view:VIEW, sess:n.getAttribute('data-sess'), visible:r.top>=0&&r.bottom<=innerHeight,
        line:(n.querySelector('.recentline') || {}).textContent,
        expected:recentLine(sessionAgent(n.getAttribute('data-sess'))), nested:n.querySelectorAll('button').length }; }""")
    # 最近の行は会話を直接開く。署名付き「続けて」の確認・取消は既存の次の一手で保つ。
    _tap_center(page, "#list .recentrow[data-kind=idle]")
    page.wait_for_timeout(150)
    reply = page.evaluate("() => ({ open:document.getElementById('termwrap').classList.contains('open'), sess:TERM&&TERM.session })")
    page.evaluate("() => { closeTerm(true); setView('office', true); openNext(); nextChoose(NEXT_ROWS.findIndex(r=>r.kind==='continue')); }")
    before = len(sent)
    confirm = page.evaluate("() => ({ rows:document.querySelectorAll('#nextconfirm #nextsend').length, text:document.getElementById('nextconfirm').textContent })")
    unsent = len(sent) == before
    page.evaluate("()=>document.getElementById('nextcancel').click()")
    cancelled = len(sent) == before
    page.evaluate("() => nextChoose(NEXT_ROWS.findIndex(r=>r.kind==='continue'))")
    page.evaluate("()=>nextSend()")
    got = _wait_sent(page, sent, before + 1)
    ok, why, session, text = _verify(sent[before], device_id, secret) if got else (False, "no-request", "", "")
    if got:
        page.wait_for_function("() => !SENDING && !NEXT_BUSY", timeout=3000)
    check(inline["view"] == "wait" and inline["sess"] == idle["session"] and inline["visible"]
          and inline["line"] == inline["expected"] and inline["nested"] == 0
          and confirm["rows"] == 1 and "続けて" in confirm["text"] and unsent and cancelled
          and ok and session == idle["session"] and text == "続けてください" and len(sent) == before + 1
          and reply["open"] and reply["sess"] == idle["session"],
          "指示待ちの文章1行から本人の会話へ・続けては確認と取消→署名付き1通", (inline, confirm, why, text, reply))
    page.evaluate("() => { closeSheet(); closeNext(false); setView('office', true); }")

    # R122で構成が変わった。下情報は新しい末尾の可視面で測る。
    # 同期・同じ3枠の数・1行・15秒の知らせを守り、末尾44pxを実タップしてリソースへ届くことを見る。
    page.evaluate("()=>closeRes()")
    status = page.evaluate("""() => {
      const saved={ office:LAST_OFFICE, now:Date.now, login:Object.assign({}, LOGIN), sync:LAST_ST,
        pin:localStorage.getItem('aioffice.gaugePin') };
      const base=Date.now();
      try {
        LAST_OFFICE=Object.assign({}, LAST_OFFICE, { relay:{ pct:8, level:0 }, res:{ providers:[
          { id:'claude', label:'Claude Code', bars:[{ k:'5h', pct:38 }, { k:'7d', pct:19 }] }] } });
        localStorage.setItem('aioffice.gaugePin','claude'); LOGIN.msg=null; LOGIN.checked=true;
        LAST_ST={ at:base, ago:3, ts:base }; paintGauges();
        const tk=document.getElementById('ticker'), first=tk.textContent, row=tk.getBoundingClientRect(),
          quotas=[...tk.querySelectorAll('#gaugebar .gg')].map(n => ({ key:n.getAttribute('data-quota'), text:n.textContent,
            top:n.getBoundingClientRect().top }));
        paintTicker(true); const rotation=tk.textContent===first;
        LOGIN.msg='Last login: 11:02（2時間前）· 終わった 1 · あなた待ち +0'; LOGIN.until=base+15000;
        paintTicker(false); const login=tk.textContent;
        Date.now=() => base+14999; paintTicker(false); const fifteen=!!tk.querySelector('.tk-login');
        Date.now=() => base+15001; paintTicker(false); const expired=!tk.querySelector('.tk-login');
        Date.now=saved.now;
        const tabs=document.getElementById('tabbar').getBoundingClientRect(),
          header=document.querySelector('.topbar').getBoundingClientRect(),
          tap={x:row.right-22,y:row.top+row.height/2},hit=document.elementFromPoint(tap.x,tap.y);
        return { first, quotas, rotation, height:row.height,width:row.width,tap,
          hit:!!hit&&(hit===tk||tk.contains(hit)),
          visible:row.width>=44&&row.height>=44&&row.left>=-1&&row.right<=innerWidth+1
            &&row.top>=header.bottom-1&&row.bottom<=Math.min(tabs.top,innerHeight)+1,
          buttons:document.querySelectorAll('#infodock button').length,
          nested:tk.contains(document.getElementById('gaugebar')), names:tk.querySelectorAll('.tk-nm,.tk-act,.mono').length,
          nowrap:getComputedStyle(tk).whiteSpace==='nowrap', login, fifteen, expired };
      } finally { Date.now=saved.now; LAST_OFFICE=saved.office; LOGIN=saved.login; LAST_ST=saved.sync;
        if(saved.pin===null) localStorage.removeItem('aioffice.gaugePin'); else localStorage.setItem('aioffice.gaugePin',saved.pin); paintGauges(); }
    }""")
    if status["visible"] and status["hit"]:
        page.touchscreen.tap(status["tap"]["x"], status["tap"]["y"])
    page.wait_for_timeout(150)
    status["resources"] = page.locator("#reswrap.open").is_visible()
    page.evaluate("()=>closeRes()")
    check("Mac 3秒前" in status["first"] and [r["key"] for r in status["quotas"]] == ["5h", "7d", "中継"]
          and all(w in status["first"] for w in ("残り62%", "残り81%", "残り92%"))
          and status["rotation"] and status["buttons"] == 1 and status["nested"] and status["names"] == 0
          and status["height"] >= 44 and status["width"] >= 44 and status["visible"] and status["hit"]
          and max(r["top"] for r in status["quotas"]) - min(r["top"] for r in status["quotas"]) <= 2
          and status["nowrap"] and status["resources"]
          and "Last login:" in status["login"] and "Mac 3秒前" in status["login"] and status["fifteen"] and status["expired"],
          "下の情報はMac同期と5h/7d/中継残りの1行・人物回転なし・末尾44pxの実タップでリソース・Last loginは同じ行へ15秒", status)
    return ng


def _r103_next_pins(page, label, sent, serve, device_id, secret):
    """R103-2b: 部屋外のあなた待ち1件と、次の一手の候補・あいまい検索・キー操作・個別の続けて確認。"""
    ng = 0

    def bad(msg):
        nonlocal ng
        print(f"  ✗ R103-2b {label}: {msg}")
        ng += 1

    q = {"session": "e2e-next-q", "projectId": "question-project", "disp": "Alpha Command Matrix", "state": "waiting",
         "listening": True, "question": "どちらの案で進めますか?", "kind": "said"}
    p = {"session": "e2e-next-p", "projectId": "permission-project", "disp": "Permission Desk", "state": "waiting",
         "listening": True, "approvalMin": 20, "ask": {"tool": "Bash", "kind": "permission", "id8": "a1b2c3d4"}}
    plan = {"session": "e2e-next-plan", "projectId": "planning-project", "disp": "Planning Desk", "state": "waiting",
            "listening": True, "ask": {"tool": "ExitPlanMode", "kind": "question", "id8": "b1c2d3e4"}}
    idle = {"session": "e2e-next-i", "projectId": "idle-project", "disp": "Idle Desk", "state": "waiting",
            "listening": True, "verb": "指示待ち", "kind": "said"}
    work = {"session": "e2e-next-w", "projectId": "route-build-planet", "disp": "Build Desk", "state": "working",
            "listening": True, "verb": "検証中", "kind": "tool", "age": 4}
    emps = [q, p, plan, idle, work]
    _serve_many(page, serve, emps)
    page.evaluate("() => { closeSheet(); closeMini(); setView('wait', true); }")
    page.wait_for_timeout(250)
    listed=page.evaluate(_WAIT_LISTED_JS)
    if sorted(listed) != sorted([q['projectId'],p['projectId'],plan['projectId']]):
        bad(f"ホームのあなたの番が全3件でない: {listed}")
    ng += _r122_home_pin(page,label)
    logic=page.evaluate("""() => ({
      kinds:nextCandidates('').map(r=>r.kind),
      names:nextCandidates('acm').map(r=>r.e.session),
      projects:nextCandidates('rbp').map(r=>r.e.session) })""")
    # R134: 「テーマを変える」の候補はテーマの選択ごと撤去（いつも明るい）
    if (logic['kinds']!=['attn','attn','attn','continue','terminal','notify','resources']
            or logic['names']!=[q['session']] or logic['projects']!=[work['session']]):
        bad(f"既存の候補生成・名前検索の規則が変わった: {logic}")
    choose="""() => { setView('office',true); NEXT_OPEN=true;
      NEXT_ROWS=nextCandidates('');nextChoose(NEXT_ROWS.findIndex(r=>r.kind==='continue'));
      return document.getElementById('nextconfirm').textContent; }"""
    n=len(sent)
    confirmation=page.evaluate(choose)
    page.wait_for_timeout(350)
    if len(sent)!=n or 'Idle Desk' not in confirmation or '続けてください' not in confirmation:
        bad(f"続けてが確認前に送られる／相手が不明: {confirmation!r}")
    page.evaluate("()=>{NEXT_CONFIRM=null;paintNext()}")
    page.wait_for_timeout(350)
    if len(sent)!=n:
        bad('続けての取消で送信された')
    page.evaluate(choose)
    page.evaluate("""()=>{const e=sessionAgent('e2e-next-i');e.state='working';e.kind='tool';
      LAST_SIG='';dispatch();nextSend()}""")
    page.wait_for_timeout(350)
    if len(sent)!=n:
        bad('確認中に指示待ちが終わった人へ続けてを送信した')
    _serve_many(page,serve,emps)
    page.evaluate(choose)
    n=len(sent)
    page.evaluate("()=>nextSend()")
    got=_wait_sent(page,sent,n+1)
    ok,why,sess,text=_verify(sent[n],device_id,secret) if got else (False,'no-request','','')
    page.wait_for_timeout(350)
    if not (ok and len(sent)==n+1 and sess==idle['session'] and text=='続けてください'):
        bad(f"続けての署名／宛先／本文／通数が違う: ok={ok}({why}) session={sess} text={text!r} 通数={len(sent)-n}")
    else:
        print(f"  ✓ R122-P2 {label}: 続けての既存安全処理（確認前・取消・状態変更は0通、本人へ署名付き1通）")
    page.evaluate("()=>{closeNext(false);closeMini();closeSheet();setView('wait',true)}")
    _serve_many(page,serve,[])
    empty=page.evaluate("()=>document.querySelectorAll('#attncards .attncard,#attncards .apvrow').length===0")
    _serve_many(page,serve,emps)
    returned=page.evaluate(_WAIT_LISTED_JS)
    if not empty or sorted(returned)!=sorted([q['projectId'],p['projectId'],plan['projectId']]):
        bad(f"あなたの番0件から同じ要求が戻るとカードを失う: {returned}")
    return ng


_B1_CARD_JS = """() => { const c = document.querySelector('#attncards .attncard'); if (!c) return null;
  return { sess: c.getAttribute('data-attn-sess'), nav: (document.getElementById('wait_title') || {}).textContent || '',
           sent: !!c.querySelector('.attnsent'), settling: c.classList.contains('settling') }; }"""


# R116-S1: 許可は焦点の 1 件がカード・答えた件（中継が預かった 2 分）は 1 行に「✓ 送信しました」。
_B1_PERM_JS = """(s) => { const r = document.querySelector('#attncards .apvrow[data-attn-sess="' + s + '"]');
  return { row: !!r, sent: !!r && !!r.querySelector('.attnsent'),
           card: !!document.querySelector('#apvslot_wait > #apvwrap.open') && !!APV && APV.session === s }; }"""


def _b1_pins(page, label, sent, serve, device_id, secret):
    ng = 0

    def bad(msg):
        nonlocal ng
        print(f"  ✗ B1 {label}: {msg}")
        ng += 1

    qa = {"session": "e2e-b1-qa", "projectId": "e2e-b1-qa", "disp": "E2E質問A", "state": "waiting", "listening": True,
          "verb": "検討中", "question": "A の質問ですか?"}
    pb = {"session": "e2e-b1-pb", "projectId": "e2e-b1-pb", "disp": "E2E許可B", "state": "waiting", "listening": True,
          "verb": "実行中", "approvalMin": 2, "ask": {"tool": "Bash", "kind": "permission"}}
    qc = {"session": "e2e-b1-qc", "projectId": "e2e-b1-qc", "disp": "E2E質問C", "state": "waiting", "listening": True,
          "verb": "検討中", "question": "C の質問ですか?"}
    # (1) 新しい質問が割り込んでも全員が一度ずつ表示される
    _serve_many(page, serve, [qa, pb], want="e2e-b1-qa")
    c1 = page.evaluate(_WAIT_LISTED_JS)
    _serve_many(page, serve, [qc, qa, pb])
    c2 = page.evaluate(_WAIT_LISTED_JS)
    if sorted(c1) == ["e2e-b1-pb", "e2e-b1-qa"] and sorted(c2) == ["e2e-b1-pb", "e2e-b1-qa", "e2e-b1-qc"]:
        print(f"  ✓ R103-2a {label}: 新しい質問が割り込んでもあなた待ちを全件表示（{len(c1)} → {len(c2)} 件・重複なし）")
    else:
        bad(f"割り込みでカードが消えた／重複した: {c1} → {c2}")
    # (2) 新しい先頭の C ではなく、押した A のカード本人へ送る
    n0 = len(sent)
    b = _tap_center(page, "#attncards .attncard[data-attn-sess='e2e-b1-qa'] .attnactions .g")
    got = bool(b and b["hit"]) and _wait_sent(page, sent, n0 + 1)
    ok, why, sess, text = _verify(sent[n0], device_id, secret) if got else (False, "no-request", "", "")
    if ok and len(sent) == n0 + 1 and sess == "e2e-b1-qa" \
            and text == "はい、そのまま進めてください。":
        print(f"  ✓ R103-2a {label}: 並びの更新後も押したカード本人へ答えが 1 通（{sess}）")
    else:
        bad(f"並びの更新後に答えの宛先が違う: tap={b} ok={ok}({why}) sess={sess} text={text!r}")
    # (3) ✓ は要求ごと: 拒否 → ✓ → 次の許可（分が戻った）には ✓ が無い／質問 → ✓ → 別の質問には ✓ が無い
    p1 = {"session": "e2e-b1-pp", "projectId": "e2e-b1-pp", "disp": "E2E許可P", "state": "waiting", "listening": True,
          "verb": "実行中", "approvalMin": 3, "ask": {"tool": "Bash", "kind": "permission", "min": 3}}
    _serve_many(page, serve, [p1], want="e2e-b1-pp")
    n0 = len(sent)
    _tap_center(page, "#apvdeny")                                    # R116-S1: 許可はカードの［拒否］→ 答えた件は 1 行に ✓
    _wait_sent(page, sent, n0 + 1, ms=6000)
    try:
        page.wait_for_selector("#attncards .apvrow .attnsent", timeout=4000)
    except PWTimeout:
        pass
    s1 = page.evaluate(_B1_PERM_JS, "e2e-b1-pp")
    _serve_many(page, serve, [dict(p1, approvalMin=1, ask={"tool": "Bash", "kind": "permission", "min": 0})], want="e2e-b1-pp")
    s2 = page.evaluate(_B1_PERM_JS, "e2e-b1-pp")
    q1 = dict(qc, session="e2e-b1-qq", projectId="e2e-b1-qq", question="最初の質問ですか?")
    _serve_many(page, serve, [q1], want="e2e-b1-qq")
    n0 = len(sent)
    _tap_center(page, "#attncards .attncard .attnactions .g")
    _wait_sent(page, sent, n0 + 1)
    try:
        page.wait_for_selector("#attncards .attnsent", timeout=4000)
    except PWTimeout:
        pass
    s3 = page.evaluate(_B1_CARD_JS)
    _serve_many(page, serve, [dict(q1, question="次の別の質問ですか?")], want="e2e-b1-qq")
    s4 = page.evaluate(_B1_CARD_JS)
    if (s1 and s1["sent"] and not s1["card"] and s2 and s2["card"] and not s2["sent"] and s3 and s3["sent"]
            and s4 and not s4["sent"]):
        print(f"  ✓ B1 {label}: 「✓ 送信しました」は要求ごと（次の許可・別の質問へ持ち越さない）")
    else:
        bad(f"✓ が次の要求へ持ち越される: 拒否後={s1} 次の許可={s2} 回答後={s3} 別の質問={s4}")
    # (4) 承認ボタンは質問だけ・停止は塗らない・minions だけでは作業中にしない
    wk = {"session": "e2e-b1-wk", "projectId": "e2e-b1-wk", "disp": "E2E稼働W", "state": "working", "listening": False,
          "verb": "実行中", "kind": "tool", "age": 3}
    idl = {"session": "e2e-b1-id", "projectId": "e2e-b1-id", "disp": "E2E待機I", "state": "waiting", "listening": True,
           "verb": "指示待ち", "kind": "said", "age": 400, "minions": 3}
    _serve_many(page, serve, [wk, idl, qc])
    sh = page.evaluate("""() => { const out = {};
      for (const e of officeAgents(LAST_OFFICE)) { openSheet(e);
        const bs = [...document.querySelectorAll('#quickbtns > button')];
        const stop = bs.find(n => n.textContent.indexOf('停止') >= 0);
        out[e.session] = { approve: bs.filter(n => n.textContent.trim().startsWith('承認')).length,
          stopBg: stop ? getComputedStyle(stop).backgroundColor : null, kind: sessKind(e) };
        closeSheet(); }
      const chip = [...document.querySelectorAll('#roster .rchip')].find(n => n.getAttribute('data-sess') === 'e2e-b1-id');
      out.chip = chip ? [(chip.querySelector('.skw') || {}).textContent, (chip.querySelector('.gl') || {}).textContent] : null;
      out.live = sessKind(Object.assign({}, officeAgents(LAST_OFFICE).find(e => e.session === 'e2e-b1-id'), { minionsLive: 1 }));
      return out; }""")
    clear = lambda v: v in ("rgba(0, 0, 0, 0)", "transparent")   # noqa: E731
    if (sh["e2e-b1-wk"]["approve"] == 0 and sh["e2e-b1-id"]["approve"] == 0 and sh["e2e-b1-qc"]["approve"] == 1
            and clear(sh["e2e-b1-wk"]["stopBg"]) and clear(sh["e2e-b1-id"]["stopBg"])
            and sh["e2e-b1-id"]["kind"] == "idle" and sh["live"] == "working" and sh["chip"] == ["指示待ち", "指示待ち"]):
        print(f"  ✓ B1 {label}: 作業中・指示待ちのシートに承認 0 個（質問だけ 1 個）・停止は塗らない・minions だけでは作業中にしない"
              f"（チップ {sh['chip']}）")
    else:
        bad(f"シートの承認・停止・作業中の判定が違う: {sh}")
    # (5) 状態の語と文
    qw = dict(qc, ask={"kind": "question", "tool": "AskUserQuestion", "min": 5})
    _serve_many(page, serve, [qw, pb], want="e2e-b1-qc")
    wd = page.evaluate("""() => { const c = document.querySelector('#attncards .attncard'), e = officeAgents(LAST_OFFICE).find(x => x.session === 'e2e-b1-qc');
      const chip = [...document.querySelectorAll('#roster .rchip')].find(n => n.getAttribute('data-sess') === 'e2e-b1-qc');
      renderList(LAST_OFFICE);
      const lc = [...document.querySelectorAll('#list .recentrow')].find(n => n.getAttribute('data-sess') === 'e2e-b1-qc');
      const pe = officeAgents(LAST_OFFICE).find(x => x.session === 'e2e-b1-pb');
      return { tag: (c.querySelector('.ktag .kw') || {}).textContent, head: (c.querySelector('.attnkind') || {}).textContent,
        age: (c.querySelector('.attnage') || {}).textContent, chip: chip ? (chip.querySelector('.skw') || {}).textContent : null,
        listSt: lc ? (lc.querySelector('.recentline') || {}).textContent.trim() : null, expectedLine:recentLine(e),
        say: sayText(e), sayPerm: sayText(pe), permHead: askSummary(pe) }; }""")
    if (wd["tag"] == wd["chip"] == "質問" and wd["listSt"] == wd["expectedLine"] and wd["head"] == "質問への回答・5分" and wd["age"] == "5分"
            and wd["head"] in wd["say"] and "止まっています" not in wd["say"] and "次の指示を待っています" not in wd["say"]
            and wd["permHead"] in wd["sayPerm"]):
        print(f"  ✓ B1 {label}: 同じ人の札・チップが「{wd['tag']}」・見出しと共通規則の最近の行「{wd['head']}」・吹き出しも同じ語")
    else:
        bad(f"状態の語と文が食い違う: {wd}")
    # (6) 2 問以上・複数選択は 1 タップで送らない
    mq = {"session": "e2e-b1-mq", "projectId": "e2e-b1-mq", "disp": "E2E複数M", "state": "waiting", "listening": True,
          "verb": "検討中", "question": "どれにしますか?", "questionOptions": [{"label": "案A", "desc": ""}, {"label": "案B", "desc": ""}],
          "ask": {"kind": "question", "tool": "AskUserQuestion", "qn": 2, "multi": 0}}
    _serve_many(page, serve, [mq], want="e2e-b1-mq")
    n0 = len(sent)
    mt = page.evaluate("""() => { const c = document.querySelector('#attncards .attncard');
      return { note: (c.querySelector('.qmulti') || {}).textContent || '', ro: c.querySelectorAll('.attnoptions .qopt.ro').length,
               btn: c.querySelectorAll('.attnoptions button').length, yes: !!c.querySelector('.attnactions .g') }; }""")
    _tap_center(page, "#attncards .attncard .attnoptions .qopt")
    page.wait_for_timeout(2600)
    if "2 問" in mt["note"] and mt["ro"] == 2 and mt["btn"] == 0 and not mt["yes"] and len(sent) == n0:
        print(f"  ✓ B1 {label}: 2 問の質問は選択肢を見せるだけ・1 タップで送らない（「{mt['note'][:24]}…」）")
    else:
        bad(f"複数の問いを 1 タップで送れる: {mt} /instruct {len(sent) - n0} 通")
    # (7) 2 秒の取り消し中に画面が隠れたら送らない
    oq = dict(qc, questionOptions=[{"label": "はい", "desc": ""}, {"label": "いいえ", "desc": ""}])
    _serve_many(page, serve, [oq], want="e2e-b1-qc")
    n0 = len(sent)
    _tap_center(page, "#attncards .attncard .attnoptions .qopt")
    hid = page.evaluate("""() => { const had = !!QPEND;
      Object.defineProperty(document, 'hidden', { configurable: true, get: () => true });
      document.dispatchEvent(new Event('visibilitychange'));
      const dropped = !QPEND; delete document.hidden;
      document.dispatchEvent(new Event('visibilitychange'));
      return { had, dropped, note: (document.getElementById('note') || {}).textContent || '' }; }""")
    page.wait_for_timeout(2600)
    if hid["had"] and hid["dropped"] and "送っていません" in hid["note"] and len(sent) == n0:
        print(f"  ✓ B1 {label}: 2 秒の取り消し中に画面が隠れたら送らない（戻ると「{hid['note'][:20]}…」）")
    else:
        bad(f"隠れた間の選択肢が送られる／言わない: {hid} /instruct {len(sent) - n0} 通")
    # (8) リスト表示でも Last login
    lg = page.evaluate("""() => { setView('list');
      localStorage.setItem('aioffice.lastSeen', JSON.stringify({ t: Date.now() - 3 * 3600 * 1000, k: { 'e2e-b1-qc': 'working' } }));
      LOGIN.prev = loginRead(); LOGIN.checked = false; LOGIN.fresh = true; LOGIN.msg = null; loginShow();
      const t = (document.getElementById('ticker') || {}).textContent || ''; setView('office'); return t; }""")
    if lg.startswith("Last login: ") and "Mac " in lg:
        print(f"  ✓ B1/R104 {label}: リスト表示でも Last login が同期と同じ状態の行に出る（{lg[:60]}…）")
    else:
        bad(f"リスト表示で Last login が出ない: {lg!r}")
    # (9) R102-11: 要求へ答えるときは表示していた要求の印（ask.id8）を添えた本文を署名する（Mac は一致する掲示にだけ答える）。
    #     同じ道具の次の許可（id8 が別）には「✓ 送信しました」を持ち越さない。印の無い要求（旧い Mac）は従来どおり言葉だけ
    pa = {"session": "e2e-b1-pa", "projectId": "e2e-b1-pa", "disp": "E2E許可ID", "state": "waiting", "listening": True,
          "verb": "実行中", "approvalMin": 1, "ask": {"tool": "Bash", "kind": "permission", "min": 0, "id8": "0a1b2c3d"}}
    _serve_many(page, serve, [pa], want="e2e-b1-pa")
    page.wait_for_timeout(700)
    n0 = len(sent)
    _tap_center(page, "#apvdeny")
    got = _wait_sent(page, sent, n0 + 1, ms=6000)
    ok, why, sess, text = _verify(sent[n0], device_id, secret) if got else (False, "no-request", "", "")
    try:
        body = json.loads(text)
    except (ValueError, TypeError):
        body = None
    try:
        page.wait_for_selector("#attncards .apvrow .attnsent", timeout=4000)
    except PWTimeout:
        pass
    t1 = page.evaluate(_B1_PERM_JS, "e2e-b1-pa")
    _serve_many(page, serve, [dict(pa, ask=dict(pa["ask"], id8="9f8e7d6c"))], want="e2e-b1-pa")
    t2 = page.evaluate(_B1_PERM_JS, "e2e-b1-pa")
    if (ok and sess == "e2e-b1-pa" and isinstance(body, dict) and body.get("aioffice") == 1 and body.get("kind") == "answer"
            and body.get("ask") == "0a1b2c3d" and isinstance(body.get("text"), str) and body["text"]
            and t1 and t1["sent"] and not t1["card"] and t2 and t2["card"] and not t2["sent"]):
        print(f"  ✓ R102-11 {label}: 答えは表示していた要求の印つき（ask={body['ask']}）・同じ道具の次の許可へ ✓ を持ち越さない")
    else:
        bad(f"答えに要求の印が付かない／✓ を持ち越す: ok={ok}({why}) sess={sess} text={text!r} 送信後={t1} 次の許可={t2}")
    return ng


def _refresh_relay(base_url, token):
    """R99-P4-7: 形状ごとに、中継に置いてある status（relay_e2e の P7）を Mac と同じ道で刻み直す＝POST /status に同じ office を
    push し直し、POST /sync（office=null）で Mac の生存（agentseen）も刻む。形状を 6 つに増やして pwa3d_smoke が長くなり、後ろの
    形状（1112）は P7 の push から 180 秒を超えた＝PWA が同期切れ（app.js の updateStale）として body.off（opacity 0.62）になり、
    (1b) の配達チップの濃さが赤になった（偽の赤・実測）。status は WS でも届くので /status の route の書き換えでは足りない。
    同じ ❗ の集合なので通知の遷移は起きない。戻り値= 失敗の理由（成功なら空）。"""
    def call(method, path, body=None):
        req = urllib.request.Request(base_url.rstrip("/") + path, method=method,
                                     data=None if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8"),
                                     headers={"Authorization": "Bearer " + token, "Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.loads(r.read().decode("utf-8") or "{}")
    try:
        cur = call("GET", "/status")
        office = json.loads(cur.get("json") or "null")
        if not isinstance(office, dict):
            return f"中継に status が無い（{str(cur)[:80]}）"
        pushed = call("POST", "/status", {"office": office})
        synced = call("POST", "/sync", {"office": None, "ackIds": [], "wantOpenclaw": False})
        return "" if pushed.get("ok") and synced.get("ok") else f"push={str(pushed)[:60]} sync={str(synced)[:60]}"
    except Exception as exc:                          # 刻み直せなくても形状の検査は回す（同期切れなら下の検査が赤を出す）
        return f"{type(exc).__name__}: {exc}"


_SW_READY_JS = """async () => {
  const out = { path: location.pathname, hash: location.hash, sw: 'serviceWorker' in navigator,
                cred: localStorage.getItem('aioffice.cred') };
  if (!out.sw) return out;
  // ready は「このページを範囲に含む登録」が active になった時にだけ解決する＝範囲外のページでは永久に待つ
  const reg = await Promise.race([navigator.serviceWorker.ready,
                                  new Promise(r => setTimeout(() => r(null), 20000))]);
  out.ready = !!reg; out.scope = reg ? reg.scope : null; out.active = !!(reg && reg.active);
  const link = document.querySelector('link[rel=manifest]');
  if (link) {
    try {
      const m = await (await fetch(link.href, { cache: 'no-store' })).json();
      out.start = new URL(m.start_url || '', link.href).href;
      out.id = new URL(m.id || m.start_url || '', link.href).href;
      out.mscope = new URL(m.scope || './', link.href).href;
    } catch (err) { out.manifestError = String(err); }
  }
  return out; }"""

_THEME_JS = """() => {
  const hex = (c) => { const m = String(c || '').match(/rgba?\\(([^)]+)\\)/); if (!m) return String(c || '').toLowerCase();
    const v = m[1].split(',').map(s => parseFloat(s));
    if (v.length > 3 && v[3] < 1) return 'translucent:' + c;
    return '#' + v.slice(0, 3).map(n => Math.round(n).toString(16).padStart(2, '0')).join(''); };
  const metas = [...document.querySelectorAll('meta[name="theme-color"]')];
  const eff = metas.find(m => !m.media || matchMedia(m.media).matches);
  const hdr = document.querySelector('.hdr2'), bar = document.querySelector('.topbar');
  return { dark: matchMedia('(prefers-color-scheme: dark)').matches,
           metas: metas.map(m => [m.media || '', (m.content || '').toLowerCase()]),
           eff: eff ? (eff.content || '').toLowerCase() : null,
           hdr: hdr ? hex(getComputedStyle(hdr).backgroundColor) : null,
           bar: bar ? hex(getComputedStyle(bar).backgroundColor) : null }; }"""


_THEME_PIN_JS = """() => {
  const lum = (c) => { const m = String(c).match(/rgba?\\(([^)]+)\\)/); if (!m) return null;
    const [r, g, b] = m[1].split(',').slice(0, 3).map((x) => { x = parseFloat(x) / 255; return x <= 0.03928 ? x / 12.92 : ((x + 0.055) / 1.055) ** 2.4; });
    return 0.2126 * r + 0.7152 * g + 0.0722 * b; };
  const bgOf = (n) => { for (let e = n; e; e = e.parentElement) { const c = getComputedStyle(e).backgroundColor;
    const m = c.match(/rgba?\\(([^)]+)\\)/); if (m && (m[1].split(',').length < 4 || parseFloat(m[1].split(',')[3]) >= 1)) return c; }
    return getComputedStyle(document.body).backgroundColor; };
  const cr = (n) => { const a = lum(getComputedStyle(n).color), b = lum(bgOf(n)); if (a == null || b == null) return 0;
    return (Math.max(a, b) + 0.05) / (Math.min(a, b) + 0.05); };
  const d = document.documentElement, meta = [...document.getElementsByName('theme-color')].map((m) => m.content.toLowerCase());
  const hex = (c) => '#' + (c.match(/\\d+/g) || []).slice(0, 3).map((x) => (+x).toString(16).padStart(2, '0')).join('');
  // B2: 空の部屋の案内（窓は常に暗い＝見出しも窓の中の色で解く）をその場で出して測り、すぐ消す
  const hadHint = !!document.getElementById('emptyhint');
  if (!hadHint) paintEmptyHint(0);
  const body = ['.hdr2 .tname', '#roster .nm', '.setrow .lb', '#emptyhint .eh-t'].map((q) => document.querySelector(q)).filter(Boolean);
  const faint = ['.setrow .hint', '#roster .gl', '#emptyhint .eh-s', '#st_rp_btn'].map((q) => document.querySelector(q)).filter(Boolean);
  const emoji = /\\p{Extended_Pictographic}/u;
  const chrome = ['.hdr2', '#tabbar', '#officewin .wtitle'].map((q) => document.querySelector(q)).filter(Boolean);
  return { dark: matchMedia('(prefers-color-scheme: dark)').matches, attr: d.getAttribute('data-theme'), auto: d.hasAttribute('data-theme-auto'), meta,
           hdr: hex(getComputedStyle(document.querySelector('.hdr2')).backgroundColor),
           page: hex(getComputedStyle(document.body).backgroundColor),
           stage: hex(getComputedStyle(document.getElementById('scene3dwrap')).backgroundColor),
           body: body.map(cr), faint: faint.map(cr), saved: localStorage.getItem('aioffice.theme'),
           hint: ((r) => { if (!hadHint) paintEmptyHint(1); return r; })(body.length),
           emoji: chrome.filter((n) => emoji.test(n.textContent)).map((n) => n.className || n.id),
           seg: ['sg_th_auto', 'sg_th_dark', 'sg_th_light', 'st_th_lb'].filter((i) => document.getElementById(i)),
           setTheme: typeof window.setTheme === 'function' };
}"""


_SETTINGS_STATE_JS = """(cases) => {
  const saved = { office: LAST_OFFICE, st: LAST_ST, stale: STALE, staleSec: STALE_SEC, offline: OFFLINE,
    nt: NTOG_STATE, cred: window.getCred, supported: window.pushSupported,
    sent: ATTN_SENT, lang: LANG, now: Date.now, fetch: window.fetch };
  const now = Math.floor(saved.now() / 1000) * 1000, sec = now / 1000;
  const byId = (id) => document.getElementById(id), text = (id) => (byId(id)?.textContent || '').trim();
  const visible = (id) => { const n = byId(id); return !!n && n.getClientRects().length > 0; };
  const ids = ['st_sync_state', 'st_approve_state', 'st_notify_state', 'st_cred_state'];
  let network = 0;
  try {
    Date.now = () => now;
    window.fetch = () => { network++; throw new Error('Settings paint must only read existing state'); };
    APV_MSG = null; APV_BUSY = null;   // R113-A: 前の操作の案内・処理中を持ち込まない
    try { localStorage.removeItem('aioffice.apvUntil'); } catch (_) {}   // R112-P: 端末の ON の控え（期限切れの日時）を持ち込まない
    LANG = 'ja'; applyLangChrome();
    const status = byId('st_status'), diag = byId('st_diag_title')?.closest('.setgroup');
    const shape = {
      title: text('st_status_title'),
      rows: status ? [...status.querySelectorAll('[id$="_state"]')].map(n => n.id) : [],
      interactive: status ? status.querySelectorAll('button,a,input,select,textarea,summary,[role="button"],[tabindex]').length : -1,
      selfInteractive: !!status && (status.tabIndex >= 0 || !!status.onclick || status.getAttribute('role') === 'button'),
      // R122で構成が変わった。本文と同じsansにし、状態4行と非対話は守る。
      font: status ? getComputedStyle(status).fontFamily : '',
      bodyFont: getComputedStyle(document.body).fontFamily,
      groupIds: [...document.querySelectorAll('#setwrap .setgroup > h3')].map(n => n.id),
      groups: [...document.querySelectorAll('#setwrap .setgroup > h3')].map(n => n.textContent.trim()),
      resourceAfter: status?.nextElementSibling?.id === 'st_res_row',
      resourceAction: byId('st_res_btn')?.getAttribute('onclick'),
      notifyGroup: byId('st_nt_row')?.closest('.setgroup')?.querySelector('h3')?.id,
      notifyAction: byId('st_nt_btn')?.getAttribute('onclick'),
      testInRow: !!byId('st_nt_row')?.contains(byId('st_pt_btn')),
      testAction: byId('st_pt_btn')?.getAttribute('onclick'),
      cryptoClosed: byId('st_cs_details')?.tagName === 'DETAILS' && !byId('st_cs_details').open,
      cryptoInDiag: !!diag?.contains(byId('st_cs_note')),
      cryptoLast: diag?.lastElementChild?.id === 'st_cs_details',
      codeHidden: !byId('apvcode'),                                  // R116-S1: 登録の受付の欄は撤去
      dotEmpty: text('tb_set_dot') === '',
      soundRows: [...document.querySelectorAll('#setwrap .setrow')].filter(n => n.textContent.includes('効果音')).length,
    };
    const probes = cases.map(c => {
      const e = { session: 'settings-pin', state: 'waiting', kind: 'tool',
        ask: { kind: 'permission', tool: 'Bash', id8: '01234567', remote: 1 } };
      let agents = [];
      if (c.wait === 'permission') agents = [e];
      if (c.wait === 'question') agents = [{ ...e, ask: { kind: 'question', id8: '01234567' } }];
      if (c.wait === 'text') agents = [{ session: e.session, state: 'waiting', question: 'Which one?' }];
      if (c.wait === 'legacy') agents = [{ session: e.session, state: 'waiting', approvalMin: 1 }];
      if (c.wait === 'error') agents = [{ session: e.session, state: 'working', err: { kind: 'failure' } }];
      LAST_OFFICE = { employees: agents, actions: { caps: { approve: 1 },
        approve: { on: c.on || 0, until: c.always ? 4102444800 : sec + (c.until || 0), fps: [] } } };   // R118: 許可鍵は見ない
      LAST_ST = c.noSync ? null : { ago: c.fallback ? null : (c.ago ?? 3),
        ts: now - (c.ts || 3) * 1000, at: now - (c.elapsed || 0) * 1000, on: !c.stale };
      STALE = !!c.stale; STALE_SEC = 0; OFFLINE = !!c.offline; NTOG_STATE = c.notify !== false;
      window.getCred = () => ({ e: c.expiry == null ? 0 : sec + c.expiry, d: 'd_settings' });
      window.pushSupported = () => c.available !== false;
      ATTN_SENT = {};
      if (c.sent && agents.length) {
        ATTN_SENT[attnKey(agents[0])] = { t: now, sig: attnSig(agents[0]), m: askMinOf(agents[0]) };
        if (c.changed) agents[0].ask.id8 = '89abcdef';
      }
      apvEnrollState(); paintSettingsState();
      const d = new Date((sec + (c.until || 0)) * 1000);
      return { key: c.key, lines: ids.map(text), dot: byId('tb_set_dot')?.hidden === false,
        attn: ids.filter(id => byId(id)?.classList.contains('attn')),
        now: text('st_apv_now'), hintVisible: visible('st_apv_hint'), button: text('st_apv_go'),
        act: byId('st_apv_go')?.getAttribute('data-act') || '',
        extra: [...document.querySelectorAll('#st_apv_main button')].map(n => n.id).filter(id => id !== 'st_apv_go'),
        hhmm: String(d.getHours()).padStart(2, '0') + ':' + String(d.getMinutes()).padStart(2, '0') };
    });
    LANG = 'en'; applyLangChrome(); paintSettingsState();
    const en = { title: text('st_status_title'),
      groups: [...document.querySelectorAll('#setwrap .setgroup > h3')].map(n => n.textContent.trim()),
      lines: ids.map(text), button: text('st_apv_go'), now: text('st_apv_now') };
    return { shape, probes, en, network };
  } finally {
    LAST_OFFICE = saved.office; LAST_ST = saved.st; STALE = saved.stale; STALE_SEC = saved.staleSec; OFFLINE = saved.offline;
    NTOG_STATE = saved.nt; window.getCred = saved.cred; window.pushSupported = saved.supported;
    ATTN_SENT = saved.sent; LANG = saved.lang; Date.now = saved.now; window.fetch = saved.fetch;
    applyLangChrome(); apvEnrollState(); paintSettingsState();
  }
}"""


def _r103_settings_pins(page):
    """R103-3: 状態は既存値だけを読む。点の3条件・境界・理由の朱・許可の1行（R118: ON／OFF だけ・期限なし）を固定する。"""
    cases = [
        {"key": "unregistered"},
        {"key": "registered_off", "mine": True},
        {"key": "approve_short", "mine": True, "on": 1, "until": 1799},
        {"key": "approve_30m", "mine": True, "on": 1, "until": 1800},
        {"key": "approve_expired", "mine": True, "on": 1, "until": -1},
        {"key": "approve_other", "on": 1, "until": 20},
        {"key": "approve_always", "on": 1, "always": True},
        {"key": "cred_short", "expiry": 3 * 86400 - 1},
        {"key": "cred_3d", "expiry": 3 * 86400},
        {"key": "cred_expired", "expiry": -1},
        {"key": "notify_permission", "notify": False, "wait": "permission"},
        {"key": "notify_question", "notify": False, "wait": "question"},
        {"key": "notify_text", "notify": False, "wait": "text"},
        {"key": "notify_legacy", "notify": False, "wait": "legacy"},
        {"key": "notify_idle", "notify": False},
        {"key": "notify_answered", "notify": False, "wait": "permission", "sent": True},
        {"key": "notify_changed", "notify": False, "wait": "permission", "sent": True, "changed": True},
        {"key": "notify_error", "notify": False, "wait": "error"},
        {"key": "notify_unsupported", "notify": False, "wait": "permission", "available": False},
        {"key": "notify_on", "wait": "permission"},
        {"key": "sync_stale", "stale": True, "ago": 243},
        {"key": "sync_offline", "offline": True, "ago": 243},
        {"key": "sync_elapsed", "elapsed": 2},
        {"key": "sync_fallback", "fallback": True, "ts": 7},
        {"key": "sync_unknown", "noSync": True},
        {"key": "all", "mine": True, "on": 1, "until": 1799,
         "expiry": 86400, "notify": False, "wait": "question"},
    ]
    old_view = page.evaluate("VIEW")
    page.locator("#tb_set").click()
    try:
        got = page.evaluate(_SETTINGS_STATE_JS, cases)
    finally:
        page.evaluate("(view) => setView(view)", old_view)
    why = []
    shape, probes = got["shape"], {p["key"]: p for p in got["probes"]}
    ids = ["st_sync_state", "st_approve_state", "st_notify_state", "st_cred_state"]
    titles = ["許可", "通知", "表示", "実行", "つながり", "診断"]      # R133: 端末風の飾り（┤ ├）は外した
    group_ids = ["st_apv_title", "st_nt_title", "st_display_title", "st_run_title", "st_connect_title", "st_diag_title"]
    if (shape["title"] != "いまの状態" or shape["rows"] != ids or shape["interactive"] != 0
            or shape["selfInteractive"] or "sans-serif" not in shape["font"].lower()
            or shape["font"] != shape["bodyFont"]):
        why.append(f"状態の4行・非対話・本文と同じsansが不正: {shape}")
    if shape["groups"] != titles or shape["groupIds"] != group_ids:
        why.append(f"6グループの題と順が不正: {shape['groups']}")
    if not shape["resourceAfter"] or shape["resourceAction"] != "openRes()":
        why.append("状態の直下にリソースを見る入口が無い")
    if (shape["notifyGroup"] != "st_nt_title" or shape["notifyAction"] != "togglePush()"
            or not shape["testInRow"] or shape["testAction"] != "sendTestPush()"):
        why.append("通知ON/OFFと右のテスト送信が同じ通知行に無い")
    if not (shape["cryptoClosed"] and shape["cryptoInDiag"] and shape["cryptoLast"]):
        why.append("暗号の長い説明が診断末尾の閉じたdetailsに無い")
    if not shape["codeHidden"] or not shape["dotEmpty"] or shape["soundRows"] != 1:
        why.append("照合欄hidden・数字なしの点・効果音1行の既存IDが不正")
    expected = {"approve_short": [ids[1]], "approve_other": [ids[1]], "cred_short": [ids[3]], "cred_expired": [ids[3]],
                **{k: [ids[2]] for k in ("notify_permission", "notify_question", "notify_text",
                                        "notify_legacy", "notify_changed")}, "all": ids[1:]}
    # R112-P（本人 2026-10-07・意図して変更）: 「スマホから許可」の行は OFF・期限切れでも朱（ここから許可できない状態）。
    # 設定タブの点は従来の 3 条件のまま（行の朱と点は ON の残り 30 分未満でだけ一致する）。R118: 許可鍵の段（未登録）は無い＝ON なら許可できる
    approve_on = {"approve_short", "approve_30m", "all", "approve_other", "approve_always"}
    for key, p in probes.items():
        want = expected.get(key, [])
        rows = [i for i in ids[1:] if i in want or (i == ids[1] and key not in approve_on)]
        # 同期切れの行の朱は点の3条件と独立した状態表示。
        reasons = [i for i in p["attn"] if i != ids[0]]
        if p["dot"] != bool(want) or reasons != rows:
            why.append(f"点／理由の朱が不正 {key}: dot={p['dot']} attn={p['attn']} expected={rows}")
        if len(p["lines"]) != 4 or not all(p["lines"]):
            why.append(f"状態の短文4行が空 {key}: {p['lines']}")
    if "3 秒前" not in probes["unregistered"]["lines"][0] or "5 秒前" not in probes["sync_elapsed"]["lines"][0] \
            or "7 秒前" not in probes["sync_fallback"]["lines"][0]:
        why.append("Mac最終同期がheartbeat・経過・timestampから読めない")
    for key in ("sync_stale", "sync_offline"):
        if "4 分前から届いていません" not in probes[key]["lines"][0]:
            why.append(f"NO CARRIERの経過が不正 {key}: {probes[key]['lines'][0]}")
    # R118（本人裁定 2026-10-13）: 許可鍵の登録は無い＝状態は ON／OFF だけ（「未登録」と言わない）・常に ON は「ON（期限なし）」
    if (probes["unregistered"]["lines"][1] != "スマホから許可: OFF" or "未登録" in probes["approve_other"]["lines"][1]
            or probes["approve_always"]["lines"][1] != "スマホから許可: ON（期限なし）"):
        why.append(f"許可の状態が ON／OFF だけでない: {probes['unregistered']['lines'][1]}／{probes['approve_other']['lines'][1]}"
                   f"／{probes['approve_always']['lines'][1]}")
    # R112-P: 期限が過ぎた ON は「期限切れ（M/D HH:MM）」（Mac は期限後 until=0 で送る＝その形は registered_off の OFF）
    if ("OFF" not in probes["registered_off"]["lines"][1]
            or not re.search(r"期限切れ（\d+/\d+ \d\d:\d\d）", probes["approve_expired"]["lines"][1])):
        why.append(f"許可OFF／期限切れを読めない: {probes['registered_off']['lines'][1]}／{probes['approve_expired']['lines'][1]}")
    p = probes["approve_short"]
    if f"ON（{p['hhmm']} まで・あと 30 分）" not in p["lines"][1]:              # R112-P: 終了時刻と残り
        why.append(f"許可ONの終了時刻が不正: {p['lines'][1]}")
    if "購読中" not in probes["notify_on"]["lines"][2] or "OFF" not in probes["notify_idle"]["lines"][2] \
            or "この端末では使えない" not in probes["notify_unsupported"]["lines"][2]:
        why.append("通知の購読中／OFF／非対応を読めない")
    if "1 日" not in probes["all"]["lines"][3] or "期限切れ" not in probes["cred_expired"]["lines"][3]:
        why.append("端末登録の残り／期限切れを読めない")
    # R116-S1→R118: ┤ 許可 ├ は 1 行（状態＋ボタン 1 つ）。OFF／期限切れ=［ON にする］・ON=［OFF にする］（1h・4h・登録・登録し直すは無い）。
    # ON は期限なし（期限つきの古い ON だけ「〜まで」）。案内（#st_apv_hint）は操作の結果があるときだけ
    want_go = {**{k: ("ON にする", "on", "OFF") for k in ("unregistered", "registered_off", "approve_expired")},
               "approve_always": ("OFF にする", "off", "ON（期限なし）"),
               **{k: ("OFF にする", "off", None) for k in ("approve_short", "approve_30m", "all", "approve_other")}}
    for key, (button, act, now) in want_go.items():
        p = probes[key]
        if (p["button"] != button or p["act"] != act or p["extra"] or p["hintVisible"]
                or p["now"] != (now if now is not None else f"ON · {p['hhmm']} まで")):
            why.append(f"許可の 1 行が状態と合わない {key}: {p}")
    if got["network"]:
        why.append(f"設定の描画で新しい通信を始めた: {got['network']}件")
    en_text = " ".join([got["en"]["title"], *got["en"]["groups"], *got["en"]["lines"], got["en"]["button"], got["en"]["now"]])
    if re.search(r"[\u3040-\u30ff\u3400-\u9fff]", en_text):
        why.append(f"英語の設定に日本語が残る: {en_text}")
    if why:
        print("  ✗ R103-3 設定: " + "／".join(why))
        return 1
    print("  ✓ R103-3 設定: 非対話の状態4行・6グループ・許可/登録/通知の点3条件と境界・許可は1行＋ボタン1つ（ON にする／OFF にする・期限なし）・英語")
    return 0


def _theme_pins(page):
    """R134（本人 2026-11-14「配色も PC と同じ明るい色に」）: R101 のテーマの 3 択は撤去＝いつも明るい。端末が暗いモードでも
    属性（data-theme）は付かず、ページとヘッダーの地は Mac の明るい画面の --a-bg・meta theme-color は 2 本ともその地・設定にテーマの行が無い・
    本文 ≥7:1・薄い文字 ≥4.5:1・部屋の窓（.t-dark）の地は暗いまま・ヘッダー／タブ／窓の題名に絵文字が無い。"""
    ng = 0
    got = {}
    try:
        for scheme in ("light", "dark"):
            page.emulate_media(color_scheme=scheme)
            got[scheme] = page.evaluate(_THEME_PIN_JS)
    finally:
        page.emulate_media(color_scheme=None)
    lt, dk = got["light"], got["dark"]
    light_bg = "#f7f6f3"
    ok = (dk["dark"] and not lt["dark"] and lt["attr"] is None and dk["attr"] is None and not lt["auto"] and not dk["auto"]
          and lt["hdr"] == dk["hdr"] == lt["page"] == dk["page"] == light_bg and set(dk["meta"]) == set(lt["meta"]) == {light_bg}
          and not dk["seg"] and not dk["setTheme"] and dk["saved"] is None and lt["stage"] == dk["stage"] != light_bg)
    if ok:
        print(f"  ✓ R134 いつも明るい（端末が暗いモードでも地 {dk['page']}・ヘッダー {dk['hdr']}・meta {sorted(dk['meta'])}・テーマの行なし・部屋の窓は {dk['stage']} のまま）")
    else:
        print(f"  ✗ R134 暗いモードで地・meta・テーマの行・部屋の窓が違う: {got}")
        ng += 1
    low = [(v, "本文", c) for v in ("dark", "light") for c in got[v]["body"] if c < 7] \
        + [(v, "薄い文字", c) for v in ("dark", "light") for c in got[v]["faint"] if c < 4.5]
    if low or not dk["body"] or dk["hint"] < 4 or lt["hint"] < 4:
        print(f"  ✗ R101 コントラストが足りない: {low or '測る要素が無い'}")
        ng += 1
    else:
        print(f"  ✓ R101 明るい配色のコントラスト（本文 ≥7:1・薄い文字 ≥4.5:1・{len(dk['body']) + len(dk['faint'])} 要素・端末の明暗の両方で同じ）")
    emo = dk["emoji"] + lt["emoji"]
    if emo:
        print(f"  ✗ R101 ヘッダー／タブ／窓の題名に絵文字が残っている: {emo}")
        ng += 1
    else:
        print("  ✓ R101 ヘッダー・タブ・窓の題名に絵文字が無い（ドット絵のアイコン）")
    return ng


def _install_pins(p, base_url, device_id, secret, token):
    """R99-P4-7: ホーム画面のアプリとして名乗れること（ブラウザの中で見る分）。
    (a) 古いペアリングのリンク（/app#v=…&d=…&s=…&t=…＝office_server.pair_url の形）が /app/ に着き、資格情報を読む
    (b) navigator.serviceWorker.ready が解決し、登録の範囲が /app/ を含み、マニフェストの start_url・scope も範囲の中。
        id（統括の裁定＝"/app"・旧 WebAPK と同じアプリのまま）は start_url と同じオリジン（範囲の中は求めない）
    (c) meta theme-color（明・暗の両方）がヘッダーの実際の地と同じ色。R134: 暗いモードでも明るい地（暗の media 付きの meta も同じ色へ）
    旧: start_url=/app・ページ=/app は SW の範囲 /app/ の外で ready が永久に解決せず、🔔 を押しても何も起きなかった。"""
    ng = 0
    link = (base_url.rstrip("/") + "/app#v=1&d=" + device_id + "&s=" + secret + "&t=" + token + "&e=0")
    for attempt in (1, 2):
        browser = p.chromium.launch()
        try:
            ctx = browser.new_context(viewport={"width": 390, "height": 844}, has_touch=True, is_mobile=True)
            page = ctx.new_page()
            errors = []
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.goto(link, wait_until="domcontentloaded")
            try:
                page.wait_for_function("() => !!localStorage.getItem('aioffice.cred')", timeout=20000)
            except PWTimeout:
                pass
            sw = page.evaluate(_SW_READY_JS)
            try:
                got = json.loads(sw.get("cred") or "null") or {}
            except ValueError:
                got = {}
            if sw["path"] == "/app/" and got.get("d") == device_id and got.get("t") == token and not sw["hash"]:
                print("  ✓ R99-P4-7 古いペアリングのリンク（/app#…）が /app/ に着き、資格情報を読んだ（フラグメントは消えた）")
            else:
                print(f"  ✗ R99-P4-7 古いペアリングのリンクが /app/ に着かない／資格情報を読まない: "
                      f"path={sw['path']} hash={sw['hash'][:12]!r} cred={'有' if got else '無'}")
                ng += 1
            scope_path = (sw.get("scope") or "").split("://", 1)[-1].partition("/")[2]
            scope_path = "/" + scope_path
            in_scope = bool(sw.get("ready")) and "/app/".startswith(scope_path) and bool(sw.get("active"))
            man_in = bool(sw.get("scope")) and all(str(sw.get(k) or "").startswith(sw["scope"])
                                                   for k in ("start", "mscope"))
            origin = lambda u: str(u or "").split("/", 3)[:3]   # noqa: E731  scheme://host[:port]
            man_in = man_in and bool(sw.get("id")) and origin(sw.get("id")) == origin(sw.get("start"))
            if in_scope and man_in:
                print(f"  ✓ R99-P4-7 navigator.serviceWorker.ready が解決・登録の範囲 {scope_path}・"
                      f"マニフェストの start_url・scope も範囲の中・id {sw.get('id')} は start_url と同じオリジン")
            else:
                print(f"  ✗ R99-P4-7 SW の範囲にページかマニフェストが入らない（ready={sw.get('ready')} "
                      f"scope={sw.get('scope')} start={sw.get('start')} id={sw.get('id')} "
                      f"mscope={sw.get('mscope')} {sw.get('manifestError', '')}）＝🔔 の購読が永久に待つ")
                ng += 1
            # R134: いつも明るい＝端末が暗いモードでも、効いている meta（暗の media 付きの 1 本）までヘッダーの地（明るい --a-bg）
            themes = []
            for scheme in ("light", "dark"):
                page.emulate_media(color_scheme=scheme)
                themes.append(page.evaluate(_THEME_JS))
            page.emulate_media(color_scheme=None)
            dark_meta = any("prefers-color-scheme: dark" in m[0] for m in themes[0]["metas"])
            same = all(t["eff"] and t["eff"] == t["hdr"] == t["bar"] == "#f7f6f3" for t in themes) \
                and all(m[1] == "#f7f6f3" for t in themes for m in t["metas"])
            if same and dark_meta and themes[1]["dark"]:
                print(f"  ✓ R99-P4-7→R134 meta theme-color はヘッダーの地と同じ・端末が暗いモードでも明るい地のまま（{themes[1]['eff']}・"
                      "暗の media 付きの meta も同じ色へ）")
            else:
                print(f"  ✗ R99-P4-7→R134 meta theme-color とヘッダーの地が食い違う／暗いモードで暗くなる／暗の meta が無い: "
                      f"{[(t['dark'], t['eff'], t['hdr'], t['bar']) for t in themes]} metas={themes[0]['metas']}")
                ng += 1
            if errors:
                print(f"  ✗ R99-P4-7 page error: {errors[:3]}")
                ng += 1
            return ng
        except Exception as exc:
            if attempt == 1:
                print(f"  - R99-P4-7 ブラウザが途中で死んだ → 1回だけ再試行: {type(exc).__name__}")
                ng = 0
                continue
            print(f"  ✗ R99-P4-7 ブラウザが2回連続で途中死: {type(exc).__name__}: {exc}")
            return ng + 1
        finally:
            browser.close()
    return ng


def _shape_pins(p, base_url, cred, device_id, secret, only=None):
    ng = 0
    token = json.loads(cred).get("t", "")
    for w, h, label in SHAPES:
        if only and not any(o in label for o in only):
            continue
        for attempt in (1, 2):
            why = _refresh_relay(base_url, token)
            if why:
                print(f"  - {label}: 中継の status を刻み直せない（{why}）→ 同期切れの赤が出たら P7 の push からの経過を疑う")
            browser = p.chromium.launch()
            try:
                ctx = browser.new_context(viewport={"width": w, "height": h}, has_touch=True,
                                          is_mobile=True, device_scale_factor=2)
                page = ctx.new_page()
                errors, sent = [], []
                page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
                page.on("pageerror", lambda e: errors.append(str(e)))
                page.on("dialog", lambda d: d.accept())       # 同期切れの確認が出ても送る（送れること自体を見る）

                def on_instruct(route, request):
                    if request.method == "POST":
                        sent.append(request.post_data or "")
                        route.fulfill(status=200, content_type="application/json", body='{"ok":true}')
                    else:
                        route.continue_()
                page.route("**/instruct", on_instruct)
                serve = {"status": None}

                def on_status(route, request):
                    if request.method == "GET" and serve["status"] is not None:
                        route.fulfill(status=200, content_type="application/json", body=serve["status"])
                    else:
                        route.continue_()
                page.route("**/status", on_status)
                page.add_init_script("localStorage.setItem('aioffice.cred', " + json.dumps(cred) + ");")
                page.add_init_script("localStorage.setItem('aioffice.officeMode','pixel');")
                # R134: 前の版で「ダーク」を選んでいた端末（保存は残っている）でも明るい＝_r134_wide_pin が暗いモードで測る
                page.add_init_script("localStorage.setItem('aioffice.theme','dark');")
                page.goto(base_url.rstrip("/") + "/app", wait_until="domcontentloaded")
                try:
                    # R134: 2 列（開いて縦・iPad 縦）は部屋を［オフィス］で重ねて開くまで隠す＝canvas は付いていれば足りる（開いて見えるかは _r134_wide_pin）
                    page.wait_for_selector("#scene3d canvas", state="attached" if WIDE_SHAPES.get((w, h)) == 2 else "visible", timeout=60000)
                    # 描かれているかは (1) が理由つきで見る（選択肢を「選択肢 N 件を開く」へ差し替えた形でも「出ない」にしない）
                    page.wait_for_selector("#attncards .attncard .qopt",
                                           state="attached", timeout=20000)
                except PWTimeout:
                    if attempt == 1:
                        print(f"  - {label}: 3D か ❗ドックが出ない → SwiftShader稀死とみなし1回だけ再試行")
                        continue
                    print(f"  ✗ R99-P4-5 {label}: 3D か ❗ドックが 2 回連続で出ない（まず uptime の load を確認）")
                    ng += 1
                    break
                ng += _shape_once(page, label, sent, serve, device_id, secret)
                if (w, h, label) == SHAPES[0]:
                    ng += _r103_next_pins(page, label, sent, serve, device_id, secret)
                    ng += _b1_pins(page, label, sent, serve, device_id, secret)
                    ng += _r104_polish_pins(page, label, sent, serve, device_id, secret)
                if (w, h) in ((390, 844), (375, 667), (844, 390)):
                    ng += _b2_pins(page, label, serve, (w, h) == (390, 844))
                if errors:
                    print(f"  ✗ R99-P4-5 {label}: console/page error: {errors[:3]}")
                    ng += 1
                break
            except Exception as exc:
                if attempt == 1:
                    print(f"  - {label}: ブラウザが途中で死んだ → 1回だけ再試行: {type(exc).__name__}")
                    continue
                detail = " / ".join(x.strip() for x in str(exc).splitlines()[:8] if x.strip())   # R122: 待っていた要素（Call log）まで出す
                print(f"  ✗ R99-P4-5 {label}: ブラウザが2回連続で途中死: {type(exc).__name__}: {detail}")
                ng += 1
                break
            finally:
                browser.close()
    return ng


# 英語（office.lang=en）の P7 を 1 形だけ通す（R99-P4-9 の形・R102-1 で中身を更新）: 画面が英語になる・右上のチップが英語の語・
# あなた待ちのカードの選択肢がドックの中で見えて当たる・部屋の窓に全員が見える・窓が他の面と交差しない。
# 状態は中継に置く（POST /status に office.lang="en" を足した P7）＝本番と同じ道（WS と /status の両方）で受ける。終わったら元へ戻す。
EN_SHAPE = (740, 345, "英語 スマホ横 740×345")


def _en_pin(p, base_url, cred, token):
    w, h, label = EN_SHAPE

    def call(method, path, body=None):
        req = urllib.request.Request(base_url.rstrip("/") + path, method=method,
                                     data=None if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8"),
                                     headers={"Authorization": "Bearer " + token, "Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.loads(r.read().decode("utf-8") or "{}")

    def push(office):
        pushed = call("POST", "/status", {"office": office})
        synced = call("POST", "/sync", {"office": None, "ackIds": [], "wantOpenclaw": False})
        return bool(pushed.get("ok") and synced.get("ok"))

    try:
        office = json.loads(call("GET", "/status").get("json") or "null")
        if not isinstance(office, dict) or not push(dict(office, lang="en")):
            print(f"  ✗ R99-P4-9 {label}: 中継に英語の P7 を置けない（status={str(office)[:60]}）")
            return 1
    except Exception as exc:
        print(f"  ✗ R99-P4-9 {label}: 中継に英語の P7 を置けない: {type(exc).__name__}: {exc}")
        return 1
    try:
        for attempt in (1, 2):
            browser = p.chromium.launch()
            try:
                ctx = browser.new_context(viewport={"width": w, "height": h}, has_touch=True,
                                          is_mobile=True, device_scale_factor=2)
                page = ctx.new_page()
                errors = []
                page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
                page.on("pageerror", lambda e: errors.append(str(e)))
                page.add_init_script("localStorage.setItem('aioffice.cred', " + json.dumps(cred) + ");")
                page.add_init_script("localStorage.setItem('aioffice.officeMode','pixel');")
                page.goto(base_url.rstrip("/") + "/app/", wait_until="domcontentloaded")
                try:
                    page.wait_for_selector("#scene3d canvas", timeout=60000)
                    page.wait_for_function(
                        "() => document.documentElement.lang === 'en' && !!document.querySelector("
                        "'#attncards.on .attncard .qopt')", timeout=20000)
                except PWTimeout:
                    if attempt == 1:
                        print(f"  - {label}: 3D か ❗ドックか英語の画面が出ない → SwiftShader稀死とみなし1回だけ再試行")
                        continue
                    print(f"  ✗ R99-P4-9 {label}: 3D か ❗ドックか英語の画面が 2 回連続で出ない（lang="
                          f"{page.evaluate('document.documentElement.lang')}・まず uptime の load を確認）")
                    return 1
                page.wait_for_timeout(600)
                page.locator("#tb_wait").click()
                page.wait_for_timeout(200)
                qo = page.evaluate(_OPTS_JS)
                chips = page.evaluate(_CHIP_JS)
                why = []
                if qo["sess"] != "e2e-attn-0001" or len(qo["opts"]) != 3 or not all(o["ok"] for o in qo["opts"]):
                    why.append(f"カードの選択肢が見えない／当たらない（sess={qo['sess']}・{[(o['label'], o['ok']) for o in qo['opts']]}）")
                if "Your turn " not in qo.get("nav", ""):
                    why.append(f"カードの題が英語でない（{qo.get('nav')!r}）")
                words = [x["text"] for x in chips["chips"]]
                if not any("Allow" in t for t in words) or not any("Ask" in t for t in words) or chips["bang"]:
                    why.append(f"右上のチップが英語の語でない（{words}）")
                page.locator("#tb_office").click()
                page.wait_for_timeout(200)
                if page.evaluate(_ROOM_ALL_JS)["bad"]:
                    why.append("部屋の窓で見えないロボが居る")
                pc = page.evaluate(_PANE_CLEAR_JS)
                if not pc["ok"]:
                    why.append(f"部屋の窓が他の面と重なる（{pc['over']}）")
                if errors:
                    why.append(f"console/page error: {errors[:3]}")
                if not why:
                    print(f"  ✓ R102-1 {label}: 英語でもあなた待ちのカード（{qo['nav']}）の選択肢 3 件が見えて当たり、右上は {words}・部屋に全員")
                    return 0
                at = _shot(page, f"pwa3d_en_{w}x{h}.png")
                print(f"  ✗ R102-1 {label}: 英語の形: " + "／".join(why)
                      + (f"（画面= {at}）" if at else ""))
                return 1
            except Exception as exc:
                if attempt == 1:
                    print(f"  - {label}: ブラウザが途中で死んだ → 1回だけ再試行: {type(exc).__name__}")
                    continue
                print(f"  ✗ R99-P4-9 {label}: ブラウザが2回連続で途中死: {type(exc).__name__}: {exc}")
                return 1
            finally:
                browser.close()
        return 1
    finally:
        try:                                          # 後ろの検査（pwa_smoke）へ英語を持ち越さない
            push(office)
        except Exception as exc:
            print(f"  - {label}: 中継の status を日本語へ戻せない（{type(exc).__name__}）")


def _r122_office_modes(p, base_url, cred):
    """R122-P3: 別束の遅延読込・3D既定・保存・破損値・失敗時のドット絵を実配信で検査する。"""
    PIXEL_DEFAULT[0] = False        # この節だけは保存なし＝既定の 3D を見る
    try:
        return _r122_office_modes_body(p, base_url, cred)
    finally:
        PIXEL_DEFAULT[0] = True


def _r122_office_modes_body(p, base_url, cred):
    ng = 0
    browser = p.chromium.launch()
    bridge_js = """() => {
      const s=window.__scene3d, names=['apply','pick','project','anchor','agents','resize','focus'];
      const methods=!!s&&names.every(k=>typeof s[k]==='function');
      if (!methods) return {methods:false};
      const world=s.apply(LAST_OFFICE), agents=s.agents(); s.resize(); s.focus(null);
      const finite=p=>!!p&&Number.isFinite(p.left)&&Number.isFinite(p.top);
      const projected=agents.filter(a=>finite(s.project(a.id))&&finite(s.anchor(a.id)));
      const picked=projected.some(a=>{const q=s.project(a.id);return s.pick(q.left,q.top)===a.id});
      return {methods, mode:s.mode, ready:s.ready, n:agents.length, projected:projected.length, picked,
        applied:!!world&&Array.isArray(world.agents), view:VIEW,
        selected:['3d','pixel'].filter(m=>document.getElementById('office_mode_'+m).getAttribute('aria-pressed')==='true'),
        theme:document.documentElement.getAttribute('data-theme')};
    }"""
    try:
        for case in ('default', 'corrupt', 'saved-pixel', 'load-failure', 'no-webgl'):
            ctx=browser.new_context(viewport={"width":390,"height":844},has_touch=True,is_mobile=True,service_workers='block')
            try:
                page=ctx.new_page(); requests=[]
                page.on('request',lambda rq:requests.append(rq.url))
                page.add_init_script("localStorage.setItem('aioffice.cred', "+json.dumps(cred)+");")
                if case in ('corrupt','saved-pixel'):
                    value='broken-value' if case=='corrupt' else 'pixel'
                    page.add_init_script("localStorage.setItem('aioffice.officeMode',"+json.dumps(value)+");")
                if case=='load-failure':
                    page.route('**/pwa/boot3d.js',lambda route:route.abort())
                if case=='no-webgl':
                    page.add_init_script("""const oldContext=HTMLCanvasElement.prototype.getContext;
                      HTMLCanvasElement.prototype.getContext=function(kind,...args){
                        return /^webgl/.test(kind)?null:oldContext.call(this,kind,...args);};""")
                page.goto(base_url.rstrip('/')+'/app/',wait_until='domcontentloaded')
                expected='pixel' if case in ('saved-pixel','load-failure','no-webgl') else '3d'
                page.wait_for_function("m=>SCENE3D==='ready'&&window.__scene3d&&window.__scene3d.mode===m",arg=expected,timeout=60000)
                # R133: この待ちは描画のコマ（rAF）に乗せない。SwiftShader の 3D は負荷が高いと 1 コマに 10 秒以上かかり、条件はとうに真なのに
                # 20 秒の rAF 待ちが時間切れになった（負荷 200 超・変更前のコードでも同じ赤＝2026-11-13 に切り分け）。時間で見に行く。
                page.wait_for_function("()=>officeAgents(LAST_OFFICE).length>0&&window.__scene3d.agents().length>0",timeout=60000,polling=250)
                got=page.evaluate(bridge_js)
                parts=page.evaluate("()=>JSON.parse(document.getElementById('uiparts').textContent)")
                requested_3d=any('/pwa/boot3d.js' in u for u in requests)
                versioned=any('/u/'+parts.get('b3','')+'/pwa/boot3d.js' in u for u in requests)
                # R134: いつも明るい＝テーマの属性は付けない（data-theme が無い）
                good=(got['methods'] and got['ready'] and got['mode']==expected and got['selected']==[expected]
                      and got['applied'] and got['n']>0 and got['projected']==got['n'] and got['picked']
                      and got['view']=='wait' and got['theme'] is None)
                if case=='saved-pixel':
                    good=good and not requested_3d and not any('/vendor/three/' in u or '/iso/' in u for u in requests)
                    # 初回は3Dを1本も読まず、明示的に選んだ時だけ別束のURLを読む。
                    page.evaluate("()=>setOfficeMode('3d')")
                    page.wait_for_function("()=>SCENE3D==='ready'&&window.__scene3d.mode==='3d'",timeout=60000)
                    switched=page.evaluate(bridge_js)
                    good=good and switched['mode']=='3d' and switched['selected']==['3d'] and switched['applied']
                    good=good and page.evaluate("()=>localStorage.getItem('aioffice.officeMode')==='3d'")
                    good=good and any('/u/'+parts.get('b3','')+'/pwa/boot3d.js' in u for u in requests)
                elif case in ('default','corrupt'):
                    good=good and requested_3d and versioned
                    page.evaluate("()=>setOfficeMode('pixel')")
                    page.wait_for_function("()=>SCENE3D==='ready'&&window.__scene3d.mode==='pixel'",timeout=30000)
                    good=good and page.evaluate("()=>localStorage.getItem('aioffice.officeMode')==='pixel'")
                    if case=='default':
                        # 初期化スクリプトで値を上書きせず、手動で選んだ保存値が再読込でも使われることを見る。
                        requests.clear();page.reload(wait_until='domcontentloaded')
                        page.wait_for_function("()=>SCENE3D==='ready'&&window.__scene3d.mode==='pixel'",timeout=30000)
                        good=good and not any('/pwa/boot3d.js' in u for u in requests)
                else:
                    notice=page.locator('#office_notice')
                    good=good and notice.is_visible() and bool(notice.inner_text().strip())
                    good=good and page.evaluate("()=>VIEW==='wait'&&document.getElementById('office_notice').getAttribute('role')==='status'")
                if good:
                    print(f"  ✓ R122-P3 {case}: {expected}・橋の適用/投影/選択・別束の遅延読込・ホームを維持")
                else:
                    print(f"  ✗ R122-P3 {case}: 表示モード・橋・別束の読込が違う: {got} 3D要求={requested_3d} 版つき={versioned}")
                    ng+=1
            except Exception as exc:
                print(f"  ✗ R122-P3 {case}: {type(exc).__name__}: {str(exc)[:200]}")
                ng+=1
            finally:
                ctx.close()
    finally:
        browser.close()
    return ng


def main(argv):
    if len(argv) != 6:
        print("使い方: pwa3d_smoke.py <base_url> <device_id> <secret> <token> <out.png>")
        return 2
    base_url, device_id, secret, token, out = argv[1:]
    cred = json.dumps({"d": device_id, "s": secret, "t": token, "e": 0},
                      ensure_ascii=False, separators=(",", ":"))

    with sync_playwright() as p:
        # R122: 形の検査だけを回す近道（直すときの往復を短くする・既定の verify は使わない）。例 PWA3D_ONLY_SHAPES="568×320,667×325"
        only = [x.strip() for x in os.environ.get("PWA3D_ONLY_SHAPES", "").split(",") if x.strip()]
        if only:
            return 1 if _shape_pins(p, base_url, cred, device_id, secret, only=only) else 0
        modes_ng = _r122_office_modes(p, base_url, cred)
        # SwiftShader はヘッドレスの3D初期化で稀にブラウザごと死ぬ（ui_shot の3回リトライと
        # 同じ既知事象・R79-5で実測）。canvas が一度も出ない場合だけ1回再試行する＝
        # アサーション失敗（実回帰）はリトライで隠さない。
        for attempt in (1, 2):
            errors = []
            ng = modes_ng
            browser = p.chromium.launch()
            try:
                page = browser.new_page(viewport={"width": 390, "height": 844},
                                        has_touch=True)
                page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
                page.on("pageerror", lambda e: errors.append(str(e)))
                page.add_init_script(
                    "localStorage.setItem('aioffice.cred', " + json.dumps(cred) + ");")
                # ドット絵固有の既存の幾何・挨拶・名札の門を継続する。
                page.add_init_script("localStorage.setItem('aioffice.officeMode','pixel');")
                page.goto(base_url.rstrip("/") + "/app", wait_until="domcontentloaded")

                try:
                    # 60秒: 高負荷時はSwiftShaderのシェーダコンパイルが30秒を超える
                    # （初期化中もホームの操作は保ち、完了を待つ）
                    page.wait_for_selector("#scene3d canvas", timeout=60000)
                except PWTimeout:
                    if attempt == 1:
                        print("  - 部屋の canvas 未出現 → ブラウザの稀死とみなし1回だけ再試行")
                        continue
                    print("  ✗ 部屋の canvas が2回連続で出ない（起動失敗。マシン過負荷でも"
                          "起きる＝uptime のload averageを確認。実測: load 200超で4秒watchdogが"
                          "初期化が遅れることがある）")
                    return 1
                print("  ✓ 部屋の canvas が描画された（Worker配信のESM・ドット絵）")

                # R103-2a: 「送っても即座には届かない」相手をオフィスのロスター帯とあなた待ち一覧で見分けられること。
                # 併せて **working には出さない**（心拍は待機ループ中しか打たないので working は
                # 必ず listening:false になる＝そこに出すと忙しい社員全員に誤警告）。
                page.wait_for_selector("#attncards .attncard", state="attached", timeout=20000)
                page.wait_for_timeout(600)
                # R122で構成が変わった。保存の札はホームの本人の最近行で見せる。
                # あなたの番の札、次の操作で届く意味、稼働中への誤警告と休憩中の札を出さない条件は守る。
                page.locator("#tb_wait").click()
                page.wait_for_timeout(200)
                mute = page.evaluate("""() => {
                  const home=document.getElementById('home'), hr=home.getBoundingClientRect(),
                    tabs=document.getElementById('tabbar').getBoundingClientRect(), before=home.scrollTop;
                  const visible=n=>{if(!n)return false;n.scrollIntoView({block:'nearest'});
                    const r=n.getBoundingClientRect(), h=document.elementFromPoint(r.left+r.width/2,r.top+r.height/2);
                    return r.width>0&&r.height>0&&r.top>=hr.top-1&&r.bottom<=Math.min(hr.bottom,tabs.top)+1
                      &&r.left>=0&&r.right<=innerWidth+1&&!!h&&(h===n||n.contains(h));};
                  const rows=[...document.querySelectorAll('#list .recentrow[data-sess]')];
                  const rowOf=e=>rows.find(n=>n.getAttribute('data-sess')===e.session);
                  const agents=recentAgents(LAST_OFFICE), saved=agents.filter(e=>e.state!=='resting'&&deliveryState(e,{offline:OFFLINE,stalled:STALE})==='saved');
                  const chips=[...document.querySelectorAll('#attncards .dchip.mute')];
                  const inAttn=chips.some(visible);
                  const savedRows=saved.map(e=>{const row=rowOf(e),chip=row&&row.querySelector('.dchip.dstate-saved.mute');
                    return {session:e.session,rowSession:row&&row.getAttribute('data-sess'),visible:visible(chip),
                      text:chip&&chip.textContent,title:chip&&chip.title};});
                  const busy=agents.filter(e=>e.state==='working'&&e.listening===false&&!needsAttn(e));
                  const busySeen=busy.filter(rowOf).length;
                  const busyMuted=busy.some(e=>{const row=rowOf(e);return !!row&&!!row.querySelector('.dchip.mute');});
                  home.scrollTop=before;
                  return {chips:chips.length,inAttn,savedRows,savedExpected:saved.length,
                    inRecent:savedRows.length>0&&savedRows.every(r=>r.session===r.rowSession&&r.visible
                      &&/保存|Saved/.test(r.text||'')&&/保存|saved/i.test(r.title||'')&&/次.*操作.*届|next.*action/i.test(r.title||'')),
                    busySeen,busyMuted};
                }""")
                # R86-H: 選択肢は**全部一覧内で押せる**こと（横スクロールだと3択の3つ目が
                # 画面外へ出て押せない＝実機390pxで実測 right=531px）。加えて、選択肢がある
                # ときに汎用の承認/停止/報告を並べない（PC側と同じ規則＝何を押すか迷わせない）。
                qo = page.evaluate(
                    """() => {
                      const card = document.querySelector('#attncards .attncard[data-kind="question"]');
                      const opts = [...(card ? card.querySelectorAll('.qopt') : [])].map(n => {
                        n.scrollIntoView({ block: 'nearest' });
                        const r = n.getBoundingClientRect();
                        const hit = document.elementFromPoint(r.x + r.width / 2, r.y + r.height / 2);
                        return { label: n.textContent.trim().slice(0, 12),
                                 inside: r.right <= innerWidth + 1 && r.left >= -1,
                                 tappable: !!(hit && (hit === n || n.contains(hit))) };
                      });
                      return { opts,
                        acts: [...(card ? card.querySelectorAll('.attnactions button') : [])]
                                .map(n => n.textContent.trim()),
                        clipped: [...document.querySelectorAll('#gaugebar .lb')]
                                   .filter(n => n.scrollWidth > n.clientWidth + 1)
                                   .map(n => n.textContent.trim()) }; }""")
                if len(qo["opts"]) == 3 and all(o["inside"] and o["tappable"] for o in qo["opts"]):
                    print("  ✓ R86-H 選択肢3件が全て画面内で押せる（横スクロールで隠さない）")
                else:
                    print(f"  ✗ 選択肢が画面外/押せない: {qo['opts']}")
                    ng += 1
                if qo["acts"] == ["ほかの答えを書く…"]:
                    print("  ✓ R86-H 選択肢があるときは汎用ボタンを出さない（PCと同じ規則）")
                else:
                    print(f"  ✗ 選択肢と汎用ボタンが二重に出ている: {qo['acts']}")
                    ng += 1
                if not qo["clipped"]:
                    print("  ✓ R86-H ゲージのラベルが切れていない")
                else:
                    print(f"  ✗ ゲージのラベルが切れて読めない: {qo['clipped']}")
                    ng += 1

                # R86-I: 通知が未購読なら、❗が出ているときに1行だけ案内が出る
                # （ヘッダーの🔕が小さすぎて誰も押さず、実測で購読0台＝❗が一生届かなかった）。
                hint = page.evaluate(
                    """() => { const n = document.getElementById('pushhint');
                      if (!n) return null;
                      const r = n.getBoundingClientRect();
                      return { text: (n.innerText || '').slice(0, 60),
                               visible: r.width > 0 && r.height > 0, h: Math.round(r.height),
                               buttons: [...n.querySelectorAll('button')].map(b => b.textContent) }; }""")
                if hint and hint["visible"] and hint.get("h", 99) <= 56 and any("あとで" in b or "Later" in b
                                                                             for b in hint["buttons"]):
                    print(f"  ✓ R86-I 通知の案内が❗と一緒に出る（{hint['buttons']}・R102-6 で 1 行＝高さ {hint.get('h')}px）")
                else:
                    print(f"  ✗ 通知が未購読なのに案内が出ない（❗が一生届かない）: {hint}")
                    ng += 1
                page.locator("#tb_office").click()
                page.wait_for_timeout(200)

                # R86-I: 「どのロボが誰か」＝名札とバッジが**全員ぶん一意**であること。
                # 実データで詰んだ形: 同一プロジェクトの並走セッションは名前の頭が全部同じで
                # 1文字モノグラムが「制」だらけ、名札は12文字で切られて区別がつく部分だけが消える。
                ident = page.evaluate(
                    r"""() => {
                      const plates = [...document.querySelectorAll('#plates .plate .nm')]
                        .map(n => n.textContent.replace(/^❗\s*/, ''));
                      const badges = [...document.querySelectorAll('#roster .rchip .mono')]
                        .map(n => n.textContent);
                      const r = [...document.querySelectorAll('#plates .plate')]
                        .map(n => n.getBoundingClientRect());
                      let overlap = 0;
                      for (let i = 0; i < r.length; i++) for (let j = i + 1; j < r.length; j++)
                        if (!(r[i].right < r[j].left || r[j].right < r[i].left ||
                              r[i].bottom < r[j].top || r[j].bottom < r[i].top)) overlap++;
                      return { plates, badges, overlap }; }""")
                dup_p = len(ident["plates"]) != len(set(ident["plates"]))
                dup_b = len(ident["badges"]) != len(set(ident["badges"]))
                if ident["plates"] and not dup_p and not dup_b:
                    print(f"  ✓ R86-I 名札とバッジが全員一意（名札{len(ident['plates'])}枚・"
                          f"重なり{ident['overlap']}）")
                else:
                    print(f"  ✗ 誰がどれか分からない（名札/バッジが重複）: {ident}")
                    ng += 1

                # R86-H: 許可要求の相手には「承認」を出さない（スマホからは実行を通せない）。
                # R103-2a→R116-S1: あなた待ちの全件（決裁カード＋1 行＋その他のカード）から文言を集める。読み切れない形
                # （remote≠1）のカードは「この操作は Mac で許可してください」。
                page.locator("#tb_wait").click()
                page.wait_for_timeout(300)
                perm = page.evaluate(
                    """() => {
                      const cards = [...document.querySelectorAll('#attncards .attncard, #attncards .apvrow')];
                      const c = document.querySelector('#apvslot_wait > #apvwrap.open'); if (c) cards.push(c);
                      const all = cards.map(n => n.textContent || '').join(' ');
                      return { cards: cards.length,
                               hasPermLine: /この操作は Mac で許可してください/.test(all),
                               liesApprovalNeeded: /承認が必要です/.test(all) }; }""")
                if perm["hasPermLine"] and not perm["liesApprovalNeeded"]:
                    print("  ✓ R86-H 許可要求は事実の文言（『承認が必要です』と断定しない）")
                else:
                    print(f"  ✗ 許可要求の文言が事実と違う: {perm}")
                    ng += 1

                if (mute["inAttn"] and mute["inRecent"] and mute["savedExpected"] == len(mute["savedRows"])):
                    print(f"  ✓ R103-2a ホームの本人の最近行とあなたの番で保存が分かる（"
                          f"最近{mute['savedExpected']}件・次の操作で届く意味を維持）")
                else:
                    print(f"  ✗ 本人の最近行かあなたの番に保存の札が出ない／意味が欠ける: {mute}")
                    ng += 1
                if mute["busySeen"] and not mute["busyMuted"]:
                    print("  ✓ R86-E 稼働中(working)には📴を出さない（誤警告なし）")
                elif not mute["busySeen"]:
                    print(f"  ✗ 稼働中セッションが本人の最近行に出ていない: {mute}")
                    ng += 1
                else:
                    print(f"  ✗ 稼働中に誤って📴を出している: {mute}")
                    ng += 1
                page.locator("#tb_office").click()
                page.wait_for_timeout(200)

                stats = None
                for _ in range(60):
                    stats = page.evaluate(
                        "() => window.__scene3d && window.__scene3d.stats"
                        " ? window.__scene3d.stats() : null")
                    if stats and stats.get("robots"):
                        break
                    page.wait_for_timeout(500)
                if not (stats and stats.get("robots", 0) >= 1):
                    print(f"  ✗ ロボットが1体も立っていない: {stats}")
                    ng += 1
                else:
                    print(f"  ✓ ロボット {stats['robots']}体（drawCalls {stats.get('drawCalls')}）")
                if stats and stats.get("drawCalls", 999) >= 300:
                    print(f"  ✗ drawCalls 上限超過: {stats}")
                    ng += 1

                # R122-P3: ここからは選んだドット絵の既存の門。整数倍の実寸で置き、引き伸ばさない。
                # 既定3Dと両者の切替は _r122_office_modes で別に検査する。
                px = page.evaluate(
                    """() => {
                        const c = document.querySelector('#scene3d canvas');
                        if (!c) return null;
                        const r = c.getBoundingClientRect();
                        const st = window.__scene3d.stats();
                        return {w: c.width, h: c.height, cssW: Math.round(r.width), cssH: Math.round(r.height),
                                scale: st.scale, gl: !!(c.getContext('webgl2') || c.getContext('webgl')),
                                render: getComputedStyle(c).imageRendering};
                    }""")
                if not px:
                    print("  ✗ 部屋の canvas が無い")
                    ng += 1
                elif not (px["scale"] in (1, 2, 3, 4) and px["w"] == 184 * px["scale"] and px["h"] == 160 * px["scale"]
                          and px["cssW"] == px["w"] and px["cssH"] == px["h"] and not px["gl"]
                          and px["render"] == "pixelated"):
                    print(f"  ✗ 部屋が整数倍の実寸のドット絵でない（引き伸ばし・WebGL・にじみ）: {px}")
                    ng += 1
                else:
                    print(f"  ✓ 部屋はドット絵（×{px['scale']}・{px['w']}×{px['h']} を実寸・pixelated・WebGL 無し）")

                # R99-P4-L1（本人「しっかりアバターが動くように」）: 「ロボの動き」の 3 択と OS の「視差効果を減らす」が
                # 部屋の止め絵／動きを本当に切り替える（iPhone で視差効果を減らすが ON だと丸ごと止まり、外す口が無かった）。
                # タップの挨拶は、動いているときだけ手を振る（greeting ≥ 1）・止めているときは振らない。
                mo = page.evaluate("""async () => {
                    const s3 = window.__scene3d, r = {};
                    // 値だけでなく描き直しまで見る（止める → 時刻 0 の止め絵が描かれる・動かす → 時刻が進む）
                    const frames = (n) => new Promise((ok) => { let k = 0; const f = () => (++k >= n ? ok() : requestAnimationFrame(f)); requestAnimationFrame(f); });
                    const wait = (ms) => new Promise((ok) => setTimeout(ok, ms));
                    r.buttons = ['sg_mo_auto', 'sg_mo_on', 'sg_mo_off'].every(id => !!document.getElementById(id));
                    r.life = s3.stats().life;
                    r.auto = s3.stats().still;
                    setMotion('off'); r.off = s3.stats().still;
                    const f0 = s3.stats().frames; await frames(3); await wait(200);
                    r.offDrawn = s3.stats().frames > f0 && s3.stats().t === 0;
                    setMotion('on'); r.on = s3.stats().still;
                    await frames(3); await wait(400);
                    r.onDrawn = s3.stats().t > 0;
                    setMotion('auto'); r.back = s3.stats().still;
                    return r; }""")
                page.emulate_media(reduced_motion="reduce")
                mo_r = page.evaluate("""() => {
                    const s3 = window.__scene3d, r = {};
                    r.auto = s3.stats().still;
                    setMotion('on'); r.on = s3.stats().still;
                    setMotion('auto'); r.back = s3.stats().still;
                    return r; }""")
                page.emulate_media(reduced_motion="no-preference")
                if not (mo["buttons"] and mo["life"] is True and mo["auto"] is False and mo["off"] is True and mo["on"] is False and mo["back"] is False
                        and mo["offDrawn"] and mo["onDrawn"]
                        and mo_r["auto"] is True and mo_r["on"] is False and mo_r["back"] is True):
                    print(f"  ✗ 「ロボの動き」の設定が止め絵／動きを切り替えない: 通常 {mo} / 視差効果を減らす {mo_r}")
                    ng += 1
                else:
                    print("  ✓ 「ロボの動き」: 端末の設定（視差効果を減らすで止まる）／動かす（それでも動く）／止める が効く・散歩（暮らし）は既定で入る")
                greet = page.evaluate("""async () => {
                    const s3 = window.__scene3d, ags = s3.agents() || [];
                    const a = ags.find(x => !x.attention) || ags[0];
                    if (!a) return null;
                    s3.greet(a.id);
                    const wait = (ms) => new Promise(r => setTimeout(r, ms));
                    let on = 0;
                    for (let i = 0; i < 20 && !on; i++) { await wait(50); on = s3.stats().greeting; }
                    await wait(1300);
                    return { on, after: s3.stats().greeting }; }""")
                if not greet or greet["on"] < 1 or greet["after"] != 0:
                    print(f"  ✗ タップの挨拶（手を振る 0.9 秒）が出ない／終わらない: {greet}")
                    ng += 1
                else:
                    print("  ✓ タップの挨拶: 手を振って 0.9 秒で終わる")

                # R102-1（本人「スマホ版で AI オフィスがまだ表示されていません」）: ❗ の間も居るロボ全員が部屋の窓に見える
                ng += _room_all_visible(page, "既定の形")
                # R104: 細い残りバーはリソースへ移動。状態の行は残りの数だけ。
                rem = page.evaluate("""() => { openRes(); const gb = document.getElementById('rs_body'); if (!gb) return null;
                    const rows = [...gb.querySelectorAll('.rs-bar')].filter(g => g.querySelector('.tr')).map(g => ({
                      text: (g.querySelector('b') || {}).textContent || '',
                      w: parseFloat((g.querySelector('.tr i') || {style: {}}).style.width) })); closeRes(); return rows; }""")
                import re as _re
                bad_rem = [r for r in (rem or []) if not _re.match(r"^(残り\d+%|\d+% left)$", r["text"])
                           or abs(r["w"] - max(2, int(_re.sub(r"\D", "", r["text"]) or 0))) > 0.5]
                if not rem or bad_rem:
                    print(f"  ✗ リソースが「残り N%」でない／バーの長さが残りと違う: {rem}")
                    ng += 1
                else:
                    print(f"  ✓ リソースは残り表示（{' / '.join(r['text'] for r in rem)}・バーの長さも残り）")

                # 部屋の表示中は旧2Dマップを出さない（二重描画＝どちらが正か分からなくなる）
                if page.locator("#mapframe").count() and page.locator("#mapframe").is_visible():
                    print("  ✗ 部屋の表示中に旧2Dマップが残っている")
                    ng += 1
                else:
                    print("  ✓ 旧2Dマップは非表示（部屋の canvas が唯一の絵）")

                # R79-0: 部屋の経路の回帰ピン。ここが崩れるとスマホの主目的（❗即応）が静かに死ぬ。
                two_d = page.evaluate(
                    "() => document.querySelectorAll('#map,#mapframe,.openclaw').length")
                if two_d:
                    print(f"  ✗ 部屋の経路なのに旧2DのDOMが作られている: {two_d}件")
                    ng += 1
                else:
                    print("  ✓ 2DのDOMを一度も作らない（初回フレームのPNG実DLも無い）")
                # R79-2: 2Dコードは撤去済み＝タイマーどころかシンボル自体が存在しないのが正
                legacy = page.evaluate(
                    "() => [typeof LEGACY_ON, typeof WALK_IV, typeof mapShell,"
                    " typeof updateMapScene, typeof paintOpenclaw]")
                if any(x != "undefined" for x in legacy):
                    print(f"  ✗ 2Dコードの残骸が定義されている: {legacy}")
                    ng += 1
                else:
                    print("  ✓ 2Dコードはシンボルごと存在しない（完全撤去）")

                # R104→R133: 名札は DOM のテキスト（印の pin は使わない）と状態の短い語。置くのは選んだ人＋あなたの番だけ・最大 4 枚。
                page.wait_for_timeout(700)   # paintPlatesは描画ループ内＝1フレーム以上待つ
                marks = page.evaluate(
                    """() => ({
                        pins: [...document.querySelectorAll('#plates .pin')]
                            .map(n => n.textContent),
                        plates: [...document.querySelectorAll('#plates .plate')]
                            .map(n => Math.round(n.getBoundingClientRect().height)),
                        robots: window.__scene3d.stats().robots,
                        want: Math.min(PLATE_MAX, officeAgents(LAST_OFFICE).filter(needsAttn).length
                            + (SEL && !needsAttn(sessionAgent(SEL.session) || SEL) ? 1 : 0)),
                        words: [...document.querySelectorAll('#plates .plate .pkind')].map(n => n.textContent),
                        rosterH: (() => { const c = document.querySelector('#roster .rchip');
                            return c ? Math.round(c.getBoundingClientRect().height) : 0; })(),
                        attnOn: !!document.querySelector('#attncards.on'),
                        S: window.__scene3d.view.state().roomScale,
                    })""")
                if marks["pins"] or not marks["want"] or len(marks["plates"]) != marks["want"] or len(marks["words"]) != marks["want"] \
                        or marks["want"] > marks["robots"] or not all(marks["words"]):
                    print(f"  ✗ 名札が「選んだ人＋あなたの番（最大 4）」と状態の語になっていない: {marks}")
                    ng += 1
                elif any(h > 20 for h in marks["plates"]):
                    print(f"  ✗ テキスト名札の高さが20px超: {marks}")
                    ng += 1
                else:
                    print(f"  ✓ R133 名札は選んだ人＋あなたの番だけ（{len(marks['plates'])} 枚・ロボ{marks['robots']}体・状態の語・≤20px）")
                # R122-P2: オフィスは部屋の下に人の一覧。あなたの番はホームに置く。
                layout=page.evaluate("""() => ({
                  rosterTop:document.getElementById('topdock').getBoundingClientRect().top,
                  roomBottom:document.getElementById('officewin').getBoundingClientRect().bottom,
                  cardsInRoom:document.querySelectorAll('#room .attncard,#room #apvwrap').length,
                  homeHidden:document.getElementById('home').classList.contains('hidden'),
                  clear:"""+_PANE_CLEAR_JS+""" })""")
                if (layout['rosterTop']<layout['roomBottom']-1 or layout['cardsInRoom']
                        or not layout['homeHidden'] or not layout['clear']['ok']):
                    print(f"  ✗ R122-P2 部屋・下の人の一覧・ホームのあなたの番の配置が違う: {layout}")
                    ng+=1
                else:
                    print("  ✓ R122-P2 部屋の下に人の一覧・あなたの番はホーム・部屋に操作を重ねない")

                # R80.5: ロスターは情報カード＝各チップに述語（activityGloss）が入っている
                # （圧縮中はCSSで隠れるが内容は常に供給＝タップ不要で「誰が・何を」）
                glosses = page.evaluate(
                    "() => [...document.querySelectorAll('#roster .rchip .gl')]"
                    ".map(n => n.textContent)")
                if not glosses or not any(g.strip() for g in glosses):
                    print(f"  ✗ ロスターに述語（.gl）が無い: {glosses}")
                    ng += 1
                else:
                    print(f"  ✓ ロスター情報カード（述語 {sum(1 for g in glosses if g.strip())}/{len(glosses)} 枚）")

                # R104: 人物の回転はロスターへ集約、状態の1行からリソースへ。
                live = page.evaluate(
                    """() => ({
                        ticker: (document.getElementById('ticker')||{}).textContent || '',
                        quotas: [...document.querySelectorAll('#ticker #gaugebar .gg')].map(n => n.textContent),
                        people: document.querySelectorAll('#ticker .tk-nm,#ticker .tk-act,#ticker .mono').length,
                    })""")
                if "Mac " not in live["ticker"] or not live["quotas"] or live["people"]:
                    print(f"  ✗ 状態1行にMac同期・残り枠がない／人物回転が残る: {live}")
                    ng += 1
                else:
                    print(f"  ✓ 状態1行（{live['ticker'][:60]}）")
                page.locator("#tb_set").click()
                page.locator("#st_res_btn").click()
                page.wait_for_timeout(400)
                res_sheet = page.evaluate(
                    """() => ({
                        open: document.getElementById('reswrap').classList.contains('open'),
                        text: (document.getElementById('rs_body')||{}).textContent || '',
                    })""")
                if not res_sheet["open"] or "中継" not in res_sheet["text"] \
                        or "ライセンス" in res_sheet["text"]:
                    print(f"  ✗ ⚡リソースシートが開かない/説明が無い/🧾が復活: {res_sheet['text'][:60]}")
                    ng += 1
                else:
                    print("  ✓ ⚡リソースシート（中継の説明・🧾撤去の維持）")
                page.evaluate("() => { closeRes(); closeSettings(); }")
                page.wait_for_timeout(300)

                # R122-P2: 骨格ピン＝部屋は独立した窓・officeタブはスクロールしない・会話・オフィス・設定の3タブ。
                # ロスターは上部帯、次の一手はタブの上、あなた待ちは先頭1件＋専用の縦一覧。
                skel = page.evaluate(
                    """() => {
                        const c = document.querySelector('#scene3d');
                        const d = document.getElementById('dock');
                        const next = document.getElementById('nextbar'), tabs = document.getElementById('tabbar');
                        const r = c ? c.getBoundingClientRect() : null;
                        const dr = d ? d.getBoundingClientRect() : null;
                        return {
                            cw: r ? Math.round(r.width) : 0,
                            ch: r ? Math.round(r.height) : 0,
                            iw: innerWidth, ih: innerHeight,
                            scrollable: document.scrollingElement.scrollHeight > innerHeight + 1,
                            tabs: document.querySelectorAll('#tabbar button').length,
                            dockBottom: dr ? Math.round(dr.bottom) : -1,
                            nextTop: next ? Math.round(next.getBoundingClientRect().top) : -1,
                            nextBottom: next ? Math.round(next.getBoundingClientRect().bottom) : -1,
                            tabsTop: tabs ? Math.round(tabs.getBoundingClientRect().top) : -1,
                            statbar: !!document.getElementById('statbar'),
                            hstats: document.querySelectorAll('#hstats .ichip').length,
                        };
                    }""")
                # R101: 部屋は窓（残りを全部）＝窓がロスター帯の下からドックの上まで・横幅いっぱい
                ng += _pane_clear(page, "既定の形")
                if not (skel["cw"] >= skel["iw"] - 28 and skel["ch"] >= 132):
                    print(f"  ✗ 部屋の窓が小さすぎる（左右12pxの余白・最小132pxの丈）: {skel}")
                    ng += 1
                else:
                    print(f"  ✓ 部屋の窓（{skel['cw']}x{skel['ch']} / 画面 {skel['iw']}x{skel['ih']}）")
                if skel["scrollable"]:
                    print(f"  ✗ officeタブがスクロール可能: {skel}")
                    ng += 1
                else:
                    print("  ✓ officeタブは一切スクロールしない")
                # R103-2a: 会話／オフィス／設定の3タブを固定する。
                if skel["tabs"] != 3 or skel["statbar"] or skel["hstats"] < 2:
                    print(f"  ✗ ヘッダー統合/タブ3つの骨格が不正: {skel}")
                    ng += 1
                else:
                    print(f"  ✓ タブ3つ＋ヘッダー統計{skel['hstats']}チップ")
                r82 = page.evaluate(
                    """() => ({
                        ttl: (document.querySelector('.hdr2 .ttl')||{}).textContent || '',
                        tabIds: [...document.querySelectorAll('#tabbar > button')].map(n => n.id),
                        runTab: !!document.getElementById('tb_run'),
                        runSetting: !!document.querySelector('#setwrap #st_run_btn'),
                        launchSetting: !!document.querySelector('#setwrap #st_ln_btn'),
                        provider: (document.getElementById('gaugebar')||{}).title || '',
                    })""")
                if "E2Eオフィス" not in r82["ttl"]:
                    print(f"  ✗ ヘッダーにオフィス名が出ない: {r82['ttl']}")
                    ng += 1
                elif (r82["runTab"] or r82["tabIds"] != ["tb_wait", "tb_office", "tb_set"]
                      or not r82["runSetting"] or not r82["launchSetting"]):
                    print(f"  ✗ 会話・オフィス・設定の3タブ／設定の実行・起動が不正: {r82}")
                    ng += 1
                elif "Claude" not in r82["provider"]:
                    print(f"  ✗ 状態の残り枠がピン留めプロバイダ(Claude)でない: {r82['provider'][:40]}")
                    ng += 1
                else:
                    print("  ✓ R103-2a/R104骨格（会話・オフィス・設定の3タブ・設定に実行／プロジェクト起動・状態の残り枠はClaude）")
                # プロバイダ切替: リソースシートのチップでCodexへピン→状態の残り値が追随。
                page.evaluate("openRes()")
                page.wait_for_timeout(400)
                switched = page.evaluate(
                    """() => {
                        const chips = [...document.querySelectorAll('#rs_body .rs-chip')];
                        const codex = chips.find(c => c.textContent === 'Codex');
                        if (!codex) return {ok: false, chips: chips.map(c => c.textContent)};
                        codex.click();
                        const gauge = document.getElementById('gaugebar'), gb = gauge.textContent || '';
                        const selected = document.querySelector('#rs_body .rs-chip.on');
                        const left = [...gauge.querySelectorAll('.gg b')].map(n => parseInt(n.textContent.replace(/[^0-9]/g,''),10));
                        const pv = pinnedProvider(), expected = pv.bars.slice(0,2).map(b => 100-Math.max(0,Math.min(100,Math.round(b.pct))));
                        const pin = localStorage.getItem('aioffice.gaugePin');
                        closeRes();
                        return {ok: gauge.title.indexOf('Codex') >= 0 && pin === 'codex' && selected && selected.textContent==='Codex'
                          && expected.every((v,i) => v===left[i]), gb: gb.slice(0, 40), pin, left, expected};
                    }""")
                if not switched["ok"]:
                    print(f"  ✗ プロバイダ切替が効かない: {switched}")
                    ng += 1
                else:
                    print("  ✓ 状態の残り枠のプロバイダ切替（リソースのチップ→残り値反映＋永続）")
                # S1/R104: プロバイダlabelはリソースで表示。HTMLを入れても実行しない。
                xss = page.evaluate(
                    """() => {
                        window.__xss = 0;
                        const o = JSON.parse(JSON.stringify(LAST_OFFICE || {}));
                        o.res = o.res || {};
                        o.res.providers = [{id:'claude', label:'<img src=x onerror=\"window.__xss=1\">',
                            bars:[{k:'<b>5h</b>', pct:20}]}];
                        localStorage.setItem('aioffice.gaugePin','claude');
                        LAST_OFFICE = o; paintGauges(); paintRes();
                        const gb = document.getElementById('rs_body');
                        return {fired: window.__xss, imgs: gb.querySelectorAll('img').length,
                                litem: gb.textContent.indexOf('<img') >= 0,
                                statusImgs: document.getElementById('ticker').querySelectorAll('img').length};
                    }""")
                if xss["fired"] or xss["imgs"] > 0 or xss["statusImgs"] > 0 or not xss["litem"]:
                    print(f"  ✗ リソースか状態の行にHTMLインジェクション（XSS掟の破れ）: {xss}")
                    ng += 1
                else:
                    print("  ✓ リソースlabelはtextContent（<img>は文字列・onerror不発・状態の行にもimgなし）")
                page.evaluate("localStorage.setItem('aioffice.gaugePin','claude')")
                page.evaluate("localStorage.setItem('aioffice.gaugePin','claude');paintGauges()")
                # R122-P2: ホームは全セッションを共通の文章1行で見せる。
                ng += _r122_home_pin(page, "既定の形")
                page.evaluate("() => setView('office', true)")
                tabs=page.evaluate("""()=>{const r=document.getElementById('tabbar').getBoundingClientRect();
                  return {top:r.top,bottom:r.bottom,h:r.height,ih:innerHeight}}""")
                if tabs['h']<52 or tabs['bottom']>tabs['ih']+1 or tabs['top']<0:
                    print(f"  ✗ R122-P2 下の3タブが画面内に無い: {tabs}")
                    ng+=1
                else:
                    print("  ✓ R122-P2 下の3タブが画面内にある")

                # R79-7: WS常時接続がライブで確立し、HTTPポーリングが止まっていること
                # （WS不通ならポーリングへ自動退避する設計＝ここが落ちたらWS経路の回帰）
                try:
                    page.wait_for_function(
                        "() => window.__office_ws && window.__office_ws.on === true",
                        timeout=8000)
                    polling_stopped = page.evaluate("() => POLL_IV === null")
                    if polling_stopped:
                        print("  ✓ WebSocket接続（ライブ）＋HTTPポーリング停止")
                    else:
                        print("  ✗ WS接続中なのにポーリングが回り続けている")
                        ng += 1
                except Exception:
                    print("  ✗ WebSocketが接続できない（wrangler dev でWS経路が死んでいる）")
                    ng += 1

                # R80-C1: 再接続バックオフの機械ピン。**スマホ1台で無料枠を割った欠陥**の再発防止。
                # 「繋がるが即切れる」を人為的に起こし、①リトライ回数が進む（＝間隔が伸びる）
                # ②1分あたりの接続試行が上限を超えない、を実測する。
                backoff = page.evaluate(
                    """() => {
                        // 短命な接続を10回ぶん模擬（実接続はせず、公開APIの状態だけを見る）
                        const before = window.__office_ws.tries;
                        for (let i = 0; i < 10; i++) {
                            // 予算スタンプを積んで上限判定を踏ませる
                            window.__office_ws.budgetDelay();
                        }
                        return {tries: window.__office_ws.tries, before,
                                attempts: window.__office_ws.attempts,
                                delay: window.__office_ws.budgetDelay()};
                    }""")
                if backoff.get("attempts", 99) > 6:
                    print(f"  ✗ 1分あたりの接続試行が上限を超えている: {backoff}")
                    ng += 1
                else:
                    print(f"  ✓ 再接続の予算ガード（直近60秒の試行 {backoff['attempts']}/6）")

                page.screenshot(path=out)   # 目視用はタップ前＝シーンが写る絵にする

                # R103-2a: ロボを1回押すと部屋の外のミニカード。主操作から回答か端末を開く。
                room_height = page.locator("#officewin").bounding_box()["height"]
                point = page.evaluate(
                    """() => {
                        const host = document.getElementById('scene3d'), r = host.getBoundingClientRect();
                        const ags = window.__scene3d ? window.__scene3d.agents() : [];
                        const candidates = ags.slice().sort((a, b) => Number(!!a.attention) - Number(!!b.attention));
                        for (const a of candidates) {
                            const p = window.__scene3d.project(a.id); if (!p) continue;
                            const x = r.left + p.left, y = r.top + p.top + 30, hit = document.elementFromPoint(x, y);
                            const wrap = document.getElementById('scene3dwrap');
                            if (hit && wrap && wrap.contains(hit)) return { id: a.id, session: a.session, x, y };
                        }
                        return null;
                    }""")
                if not point:
                    print("  ✗ 部屋のロボットに当たるタップ点が無い（ミニカードの検証不能）")
                    ng += 1
                else:
                    page.mouse.click(point["x"], point["y"])
                    page.wait_for_timeout(250)
                    # R122で構成が変わった。ミニカードは部屋の上下左右の別面を許す。
                    # 部屋と重ならず、画面内で本人の内容と44pxの主操作に届く条件は守る。
                    first = page.evaluate("""wanted => {
                      const m=document.getElementById('minicard'),e=MINI,win=document.getElementById('officewin'),
                        mr=m.getBoundingClientRect(),wr=win.getBoundingClientRect(),
                        header=document.querySelector('.topbar').getBoundingClientRect(),tabs=document.getElementById('tabbar').getBoundingClientRect(),
                        primary=document.getElementById('mini_open'),pr=primary.getBoundingClientRect(),
                        hit=document.elementFromPoint(pr.left+pr.width/2,pr.top+pr.height/2);
                      const outside=mr.bottom<=wr.top+1||mr.top>=wr.bottom-1||mr.right<=wr.left+1||mr.left>=wr.right-1;
                      return {visible:!m.hidden&&mr.width>0&&mr.height>0,
                        sheet:document.getElementById('sheetwrap').classList.contains('open'),term:!!TERM,
                        session:e&&e.session,wanted,name:document.getElementById('mininame').textContent,
                        state:document.getElementById('ministate').textContent,work:document.getElementById('miniactivity').textContent,
                        primary:primary.textContent,outsideRoom:outside,
                        separate:outside&&!win.contains(m)&&mr.top>=header.bottom-1&&mr.bottom<=tabs.top+1,
                        inside:mr.left>=-1&&mr.right<=innerWidth+1&&mr.top>=0&&mr.bottom<=innerHeight+1,
                        primaryH:pr.height,primaryW:pr.width,primaryHit:!!hit&&(hit===primary||primary.contains(hit)),
                        wantName:e?dispCrew(e):'',wantWork:e?(detailOf(e)||glossPlain(e,LANG)||kindWord(sessKind(e))):'',
                        wantState:e?kindWord(sessKind(e)):'',wantPrimary:e&&needsAttn(e)?'答える':'端末を開く'};
                    }""", point["session"])
                    if (first["visible"] and not first["sheet"] and not first["term"]
                            and first["session"] == first["wanted"] and first["name"] == first["wantName"]
                            and first["work"] == first["wantWork"] and first["name"] and first["work"]
                            and first["outsideRoom"] and first["separate"] and first["inside"]
                            and first["primaryH"] >= 44 and first["primaryW"] >= 44 and first["primaryHit"]
                            and first["state"] == first["wantState"] and first["primary"] == first["wantPrimary"]):
                        print(f"  ✓ R103-2a ロボ1タップで本人の別面のミニカード（{first['state']}・{first['work'][:24]}・{first['primary']}）・44px主操作が画面内で当たる")
                    else:
                        print(f"  ✗ R103-2a 1タップのミニカードの本人・別面の位置・主操作が違う: {first}")
                        ng += 1
                    if first["visible"] and first["primary"] == "端末を開く":
                        page.locator("#mini_open").click()
                        page.locator("#termwrap.open").wait_for(state="visible", timeout=4000)
                        ng += _r103_terminal_pins(page, room_height)
                    elif first["visible"]:
                        page.locator("#mini_open").click()
                        page.locator("#sheetwrap.open").wait_for(state="visible", timeout=4000)
                        page.evaluate("() => closeSheet()")

                    # 実タッチでも1回で開く。再度ロボを押す必要はない。
                    page.evaluate("() => { closeMini(); window.__scene3d.view.reset(); window.__scene3d.focus(null); }")
                    page.wait_for_timeout(300)
                    tp = page.evaluate("""(id) => {
                      const host = document.getElementById('scene3d'), r = host.getBoundingClientRect(), p = window.__scene3d.project(id);
                      return p ? { x: r.left + p.left, y: r.top + p.top + 30 } : null;
                    }""", point["id"])
                    if tp:
                        page.touchscreen.tap(tp["x"], tp["y"])
                        try:
                            page.locator("#minicard").wait_for(state="visible", timeout=4000)
                            touch = page.evaluate("() => ({ mini: !!MINI, scale: window.__scene3d.view.state().scale })")
                            if touch["mini"] and touch["scale"] == 1:
                                print("  ✓ R103-2a 実タッチも1回でミニカードを開く（全景の倍率を保持）")
                            else:
                                print(f"  ✗ R103-2a 実タッチでミニカード／倍率が違う: {touch}")
                                ng += 1
                        except PWTimeout:
                            print("  ✗ R103-2a 実タッチ1回でミニカードが開かない")
                            ng += 1
                    else:
                        print("  ✗ R103-2a 実タッチ検査用の投影点が無い")
                        ng += 1
                    page.evaluate("() => closeMini()")

                    # あなた待ちの主操作は「答える」になり、既存の回答シートへ進む。
                    answer = page.evaluate("""() => {
                      const e = officeAgents(LAST_OFFICE).find(needsAttn); if (!e) return null;
                      openMini(e); return { sess: e.session, primary: document.getElementById('mini_open').textContent };
                    }""")
                    if answer and answer["primary"] == "答える":
                        page.locator("#mini_open").click()
                        page.locator("#sheetwrap.open").wait_for(state="visible", timeout=4000)
                        same = page.evaluate("(s) => !!SEL && SEL.session === s", answer["sess"])
                        if same:
                            print("  ✓ R103-2a あなた待ちのミニカードは「答える」→その人の回答シート")
                        else:
                            print("  ✗ R103-2a ミニカードの「答える」が別の人を開く")
                            ng += 1
                        page.evaluate("() => closeSheet()")
                    else:
                        print(f"  ✗ R103-2a あなた待ちの主操作が違う: {answer}")
                        ng += 1
                    # 回答シート・定型文・会話の封書・端末の行は既存経路で検査を続ける。
                    page.evaluate("""(s) => {
                      const e = officeAgents(LAST_OFFICE).find(x => x.session === s); if (e) openSheet(e);
                    }""", point["session"])
                    tpl = page.evaluate("() => [...document.querySelectorAll('#sheettemplates button.tpl')].map(b => b.textContent)")
                    if not any("ビルド確認" in s for s in tpl):
                        print(f"  ✗ 定型文チップが回答シートに出ない: {tpl}")
                        ng += 1
                    else:
                        print("  ✓ 定型文チップ（Mac保存→スマホ同期）")
                        ng += _r87_dialog_pins(page)
                        ng += _r100_term_pins(page, device_id, secret)
                        ng += _r103_term_action_pins(page, device_id, secret)

                # R80.6: ピンチズーム/パンのAPIが生きている（タッチ実ジェスチャの代わりに
                # 公開APIで実測＝ズームでviewStateのscaleが変わり、リセットで戻る）
                view = page.evaluate(
                    """() => {
                        const v = window.__scene3d && window.__scene3d.view;
                        if (!v) return null;
                        v.reset();
                        const s0 = v.state().scale;
                        v.zoomBy(1.6, 195, 300);
                        const s1 = v.state().scale;
                        v.panBy(40, -30);
                        const p1 = v.state();
                        v.reset();
                        const s2 = v.state().scale;
                        return {s0, s1, panX: p1.panX, panY: p1.panY, s2};
                    }""")
                has_greet = page.evaluate(
                    "() => !!(window.__scene3d && window.__scene3d.greet)")
                if not has_greet:
                    print("  ✗ タップ挨拶API（__scene3d.greet）が無い")
                    ng += 1
                else:
                    print("  ✓ タップ挨拶API（greet）")
                if not view:
                    print("  ✗ ズーム/パンAPI（__scene3d.view）が無い")
                    ng += 1
                elif not (view["s0"] == 1 and view["s1"] > 1 and view["s2"] == 1
                          and (view["panX"] != 0 or view["panY"] != 0)):
                    # R104: あなた待ちのある390×844も全景×2。拡大時は横か縦の少なくとも一方でパンする。
                    # R99-P4-6: 部屋はドット絵＝拡大は整数倍を 1 段ずつ（scale は全景からの拡大率 S/fit）。
                    # 3D の scale は「見える範囲」で、寄ると 1 未満だった（向きが逆）
                    print(f"  ✗ ズーム/パンが効いていない: {view}")
                    ng += 1
                else:
                    print(f"  ✓ ピンチズーム/パン（全景 1→{view['s1']:.2f}＝整数倍を 1 段→リセット 1・パン可）")

                # R79-6 の B10 再発ガード（本文色が hairline アルファ .08〜.22 に落ちる一括置換事故）。
                # R96-B Tier 3 で PWA のテーマ切替（setTheme・呼び出し元ゼロ・th-dark の CSS も無い死んだ行）を
                # 撤去したので、ダークへ切り替えてから測る形はもう成り立たない（実測: setTheme is not defined で
                # スモークごと落ちていた＝RUN_RELAY=1 でしか走らない経路なので通常の verify では見えなかった）。
                # 守りたいのは「本文が透ける置換事故を見つける」ことなので、いまの唯一のテーマで同じ検査をする。
                text_alpha = page.evaluate(
                    """() => {
                        const sels = ['.sheet h3', '#shsay', '.sheet .sec',
                                      '.sheet button.sub', '.sheet .feedbox .feedline'];
                        const out = [];
                        for (const s of sels) {
                            const n = document.querySelector(s);
                            if (!n) continue;
                            const c = getComputedStyle(n).color;
                            const m = c.match(/rgba?\\(([^)]+)\\)/);
                            const p = m ? m[1].split(',').map(parseFloat) : [];
                            out.push([s, c, p.length > 3 ? p[3] : 1]);
                        }
                        return { out, lightOnly: typeof window.setTheme === 'undefined' && typeof window.__applyTheme === 'function'
                                 && !document.documentElement.hasAttribute('data-theme') && !document.documentElement.hasAttribute('data-theme-auto') };
                    }""")
                bad = [r for r in text_alpha["out"] if r[2] < 0.75]
                if bad or not text_alpha["out"]:
                    print(f"  ✗ 本文が透けている(B10再発): {bad or 'シート要素なし'}")
                    ng += 1
                elif not text_alpha["lightOnly"]:
                    # R134（本人 2026-11-14「配色も PC と同じ明るい色に」）: R101 のテーマの 3 択は撤去＝setTheme も data-theme も無い
                    print("  ✗ R134 テーマの切替が残っている（いつも明るいはず: setTheme・data-theme）")
                    ng += 1
                else:
                    print(f"  ✓ 本文の不透明度OK（{len(text_alpha['out'])}要素・B10ガード）")
                ng += _theme_pins(page)
                ng += _r103_settings_pins(page)

                if errors:
                    print(f"  ✗ console/page error: {errors[:3]}")
                    ng += 1
                else:
                    print("  ✓ console error 0")
                break
            except Exception as exc:
                # SwiftShader稀死は canvas 待ちだけでなく**実行途中でもブラウザごと落ちる**
                # （load20超のスクショ中に実測）。試行全体を1回だけ再試行の対象にする。
                # アサーション失敗（ng加算）は例外ではないのでリトライで隠れない。
                if attempt == 1:
                    print(f"  - ブラウザが途中で死んだ → SwiftShader稀死とみなし1回だけ再試行: "
                          f"{type(exc).__name__}")
                    continue
                print(f"  ✗ ブラウザが2回連続で途中死: {type(exc).__name__}: {exc}")
                return 1
            finally:
                browser.close()
        # R99-P4-7: ホーム画面のアプリとして名乗れること（古いリンク→/app/・SW の ready と範囲・theme-color＝ヘッダーの地）
        ng += _install_pins(p, base_url, device_id, secret, token)
        # R99-P4-5: スマホ縦（is_mobile）・iPad 縦・折りたたみ・iPad 横（主経路が緑でも赤でも回す＝形の穴を別に報告する）
        ng += _shape_pins(p, base_url, cred, device_id, secret)
        # 統括の裁定（R99-P4-9 の続き）: 英語（office.lang=en）の P7 を 1 形だけ（長い配達状態・「Open 3 options」）
        ng += _en_pin(p, base_url, cred, token)
        # R100-1b: スマホから許可（実ブラウザのパスキー → Mac 側の本物の検証 enroll/approve）
        ng += _r100_apv_pins(p, base_url, device_id, secret, token)
        # 本人（2026-09-29）: 端末の登録が切れたら、スマホが自分で気づいて言う（Mac に捨てられたのに「送信しました」と言わない）
        ng += _cred_expiry_pins(p, base_url, device_id, secret, token)
        # R116-S1b（本人裁定 2026-10-26「延長の仕組みを作る」）: 同じ端末のまま新しい秘密を封書で受け取って延長する（URL に載せない）
        ng += _r116s1b_rekey_pins(p, base_url, device_id, secret, token)
        ng += _reload_pins(p, base_url, device_id, secret, token)
        # R102-8: 部屋が起動しなかった理由を画面と中継のログへ（本人の iPhone だけで起きた失敗を掴む）
        ng += _room_error_pin(p, base_url, device_id, secret, token)
        # R112-P: 複数の問い・複数選択に答える／許可できない理由と導線／アプリの JS エラーと送信の失敗を /diag へ（本人 2026-10-07「ポンコツ」）
        ng += _r112_pins(p, base_url, device_id, secret, token)
        # R113-A→R118: 設定の ON/OFF（封筒だけ・期限なし・連打で 1 通・Mac の理由・再ペアリングの説明・届いた表示）
        ng += _r113_pins(p, base_url, device_id, secret, token)
        # R113-B: セッション画面（Codex を開く入口・考えの印・Bash 全文の pre・差分と続きの案内・サブエージェントの折りたたみ・393 幅の見切れ 0）
        ng += _r113b_pins(p, base_url, device_id, secret, token)
        # R128 #3: Bot の記録の画面（一覧の行・シートの「記録を見る」→ kind:term の封書 → 口の記録の行を作業ビューで・相談に答える）
        ng += _r128_pins(p, base_url, device_id, secret, token)
        # R114-P→R118: ON の間はタップだけ（段を問わず 1 タップ・mac は危険の朱の 1 行・失敗を /diag に）・許可鍵の登録の導線は無い・
        # ペアリングの無いブラウザ（standalone でない）は「ホーム画面から開いて」を最優先（本人 2026-10-08「全く許可できない」）
        ng += _r114_tap_pins(p, base_url, device_id, secret, token)
        ng += _r114_nocred_pins(p, base_url, device_id, secret, token)
        # R116-S0（本人 2026-10-09「bash の許可を与えるのもごちゃごちゃしすぎている」「回りくどい」）: 通知の着地は許可の画面・受け付けられたら
        # ［閉じる］なしで閉じて次へ・許可待ちのシートに黙った拒否の送信口を置かない・通知の本文に絵文字と「（0分）」を出さない
        ng += _r116_pins(p, base_url, device_id, secret, token)
        ng += _r116_push_pin()
        # R116-S1 門③: 許可のカードの予算（45 字・押せる物 3 つ・小さい iPhone と「大きめ」）
        ng += _r116s1_budget_pin(p, base_url, device_id, secret, token)
        # R102-10: 8 月の 1 年キャッシュの古い写しに当たらない（版つきの道 /u/<BUILD>/）・WebKit の結合の失敗も理由として拾う
        ng += _stale_cache_pins(p, base_url, device_id, secret, token)

    if ng:
        print(f"✗ PWA 3Dスモーク {ng}件失敗")
        return 1
    print(f"✓ PWA 3Dスモーク合格: {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
