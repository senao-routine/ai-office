#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""R97-C: 公開デモ（静的ホスト）が**サーバー無しで**成立することを機械で確かめる。

なぜ要るか: GitHub で README を読んでいる人は `http://localhost:4780/?demo=1` を踏めない。
競合は `npx` や拡張の 1 手で入るので、こちらの入口は「リンク 1 クリック」にする。
その入口は「静的ファイルだけで 3D が出る・/api/ を 1 本も叩かない」が成立条件なので、ここで固定する。

R99-P4-4 で足した門（様式が 3 つになった＝「選んだ様式が同梱されていない」で詰む型が現実になった）:
  - `?ui=cockpit` が静的ホストで立つ（台帳の行 ≥6・部屋の人 ≥6・＋端末は出さない・/api 0・console 0）
  - 同梱した vendor（three・xterm）は全部 LICENSE 付き（MIT 再配布の義務）
  - **STYLES の全値を保存値にして起動できる**（⚙「画面」で選んだ様式が localStorage に残っても詰まない）。
    様式の一覧はデモに配った ui/platform/style.js をブラウザで import して取る＝様式を足すと門も自動で広がる
  - 保存値の様式が読めないとき（import 失敗）は、保存値を捨てて既定で 1 回だけ起動し直す。
    `?ui=` で明示した様式の失敗は黙って既定へすり替えない（失敗を見せる・boot.html 自身の文に様式名・文言に WebGL を書かない。
    <code> のブラウザのエラー本文には URL の様式名が入る＝そこで様式名を見ると空振り）。
    保存値の様式も既定も読めないときは、保存値を捨てて失敗の文言を出す（各 1 回だけ試す）
  - 保存値を捨てるのは、失敗した様式が**保存値から来たときだけ**（統括の裁定）。`?ui=` で明示した様式や
    `?stream=1`（iso を強制）の失敗では、保存値が同じ様式でも消さない（頼まれた URL の失敗で普段の設定を失わない）
  - 保存値を書くのは **mount が成功した後**（統括の裁定）。import は通っても mount が落ちる様式を `?ui=` で開いても、
    保存値（普段の様式）を壊れた様式で上書きしない。成功した mount の後には保存する（上書きしないだけで保存が消えたら赤・
    既定以外の全様式で見る）
  - R99-P4 第 C 波: mount が落ちても**失敗の文言が画面に見える**。3 様式の mount は先頭で root.replaceChildren() する
    ＝起動画面（#bootstate）が DOM から外れ、文言を書いても白い画面になっていた（R50 からの穴）。偽の mount も実物と同じく
    root を空にし・様式の class を付け・画面を覆う殻を置いてから投げる。見るのは: 起動画面が DOM に在る・文言が画面内で
    真ん中に当たる・見出しが「起動しています…」でない・マークが脈打たない・#app に様式の class が残らない。
    保存値から来た様式の mount の失敗では保存値も捨てる（再読み込みで既定が立つ）。?ui= と ?stream=1 の失敗では消さない
  - `?ui=legacy` のような無効な ?ui= は明示ではない（保存値から来た様式として扱う＝読めなければ捨てて既定へ）。
    日本語の画面でも boot.html の文に様式名が入る（ja-JP の context で見る）
  - デモには端末が無い（統括の裁定: 端末を出すかの軸は「デモかどうか」だけ）: 端末の画面（#cp-xterm・タブ列）を畳み、
    足元（#cp-term-note）に理由（strings.js の cp_term_demo）を出す。足元はこの画面の ＋ を案内しない。
    許可待ちの表示（#cp-work・#cp-dlg）は畳まない

使い方: <playwright入りpython> tests/demo_site_smoke.py   （verify.sh ▶7 から呼ぶ）
"""
import functools
import http.server
import pathlib
import socket
import subprocess
import sys
import threading

ROOT = pathlib.Path(__file__).resolve().parent.parent
SITE = ROOT / "dist" / "demo-site"
READY = "window.__office && window.__office.ready"
# 様式の一覧・保存キー・既定（保存値も ?ui= も無いときの様式）をデモに配った style.js から取る
STYLE_META = """async () => {
  const m = await import('/ui/platform/style.js');
  const key = m.STYLE_KEY;
  let saved = null;
  try { saved = localStorage.getItem(key); localStorage.removeItem(key); } catch {}
  const dflt = m.resolveStyle('');
  try { if (saved !== null) localStorage.setItem(key, saved); } catch {}
  // stream＝配信 URL（?stream=1）が強制する様式（既定と同じとは限らない＝既定を切り替えても iso のまま）
  return { styles: Object.values(m.STYLES), key, dflt, stream: m.resolveStyle('?stream=1') };
}"""
# 端末の面（統括の裁定: 端末を出すかの軸は「デモかどうか」だけ）: デモでは端末の画面（#cp-xterm とタブ列）を畳み、
# 足元（#cp-term-note）に理由を書く。許可待ちの表示（#cp-work・#cp-dlg）は畳まない。
# reason＝理由の文言（strings.js の cp_term_demo・今の言語）。キーが無いと T はキー名を返す＝理由が無いと分かる。
# forbid＝この画面の ＋ を案内する文（読み取り専用・打てます）の日英＝デモでは嘘になる文
COCKPIT = """async () => {
  const plus = document.querySelector('#cp-term-new');
  const { T, dictStrings } = await import('/ui/hud/strings.js');
  const box = (sel) => {
    const e = document.querySelector(sel);
    if (!e) return null;
    const r = e.getBoundingClientRect(), cs = getComputedStyle(e);
    return { rects: e.getClientRects().length, w: Math.round(r.width), h: Math.round(r.height),
      display: cs.display, visibility: cs.visibility, kids: e.childElementCount,
      text: (e.textContent || '').trim().slice(0, 160) };
  };
  const d = dictStrings();
  return {
    style: window.__office.style,
    rows: document.querySelectorAll('#agents .pxrow').length,
    people: (window.__office.debug && window.__office.debug.roomActors
      ? window.__office.debug.roomActors().length : -1),
    plus: plus ? { hidden: plus.hidden, rects: plus.getClientRects().length } : null,
    xterm: box('#cp-xterm'), tabbar: box('.cptabbar'), note: box('#cp-term-note'),
    work: box('#cp-work'), dlg: box('#cp-dlg'),
    // 畳んだ後の面の形: 足元の帯が列の底にあり（細いまま）、本体（#cp-term）が残りの丈を持つ
    center: (() => { const r = document.querySelector('.cpcenter').getBoundingClientRect();
      return { bottom: Math.round(r.bottom), h: Math.round(r.height) }; })(),
    foot: (() => { const r = document.querySelector('.cptermfoot').getBoundingClientRect();
      return { bottom: Math.round(r.bottom), h: Math.round(r.height) }; })(),
    termH: Math.round(document.querySelector('#cp-term').getBoundingClientRect().height),
    reason: T('cp_term_demo'),
    forbid: [d.ja.cp_term_readonly, d.en.cp_term_readonly, d.ja.cp_term_live, d.en.cp_term_live],
  };
}"""


def seen(b):
    """box() の結果が画面に描かれているか（在る・display が none でない・丈と幅がある・visibility が visible）"""
    return bool(b) and b["rects"] > 0 and b["w"] > 0 and b["h"] > 0 and b["visibility"] == "visible"


class StaticServer(http.server.ThreadingHTTPServer):
    """公開デモを配る静的サーバー（ホスト先＝Cloudflare の代わり）。

    listen キュー（backlog）は 128。既定の 5 では、macOS が溢れた接続に即座に RST を返す。ブラウザは様式 1 つ分
    （約 100 本）の module を HTTP/1.0（1 要求 1 接続）で一斉に取りに来るので、accept が遅れた瞬間に
    ERR_CONNECTION_RESET → 動的 import が落ちる → window.__office が立たず、最初の起動待ちが 60 秒で止まっていた
    （master の版で 4/8 回。同じ木で列だけ 128 にすると 8/8。2026-09-26 実測）。番人= tests/test_listen_backlog.py
    """
    daemon_threads = True
    request_queue_size = 128


def _serve():
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    http.server.SimpleHTTPRequestHandler.log_message = lambda *a, **k: None
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(SITE))
    srv = StaticServer(("127.0.0.1", port), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, port


def _watch(page, api_hits, errors):
    page.on("request", lambda r: api_hits.append(r.url) if "/api/" in r.url else None)
    page.on("pageerror", lambda e: errors.append(f"pageerror: {e}"))
    page.on("console", lambda m: errors.append(f"console.error: {m.text}")
            if m.type == "error" else None)


# 起動の失敗の文言。lead＝boot.html 自身が書いた文（<br>・<code> より前の文字）。<code> にはブラウザのエラー本文
# （import に失敗した URL /ui/<様式>/index.js を含む）が入る＝全体の textContent で様式名を探すと、boot.html が
# 様式名を書かなくても緑になる。
# connected＝起動画面が DOM に在る（getElementById は DOM から外れた要素を返さない）。shown＝文言が画面内に丈を持ち、
# 見えていて、真ん中に当たる（途中まで組まれた様式の殻に覆われていない）。anim/grad＝マークの動きと背景
BOOT_FAIL = """() => { const m = document.getElementById('bootmsg'), s = document.getElementById('bootstate');
  const t = document.getElementById('boottitle'), app = document.getElementById('app');
  const out = { office: Boolean(window.__office), connected: Boolean(m && s), boot: m ? m.textContent : '',
    lead: '', code: false, detail: '', shown: false, title: t ? t.textContent.trim() : '',
    appClass: app ? app.className : null, anim: null, grad: null };
  if (!m || !s) return out;
  let lead = '';
  for (const n of m.childNodes) { if (n.nodeName === 'BR' || n.nodeName === 'CODE') break; lead += n.textContent; }
  out.lead = lead.trim();
  const c = m.querySelector('code'); out.code = Boolean(c); out.detail = c ? c.textContent : '';
  const r = m.getBoundingClientRect();
  const hit = document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2);
  out.shown = r.width > 0 && r.height > 0 && r.left >= 0 && r.top >= 0 && r.right <= innerWidth
    && r.bottom <= innerHeight && m.checkVisibility({ opacityProperty: true, visibilityProperty: true })
    && Boolean(hit) && m.contains(hit);
  const k = s.querySelector('.mark');
  if (k) { const cs = getComputedStyle(k); out.anim = cs.animationName; out.grad = cs.backgroundImage; }
  return out; }"""
# 起動中の見出し（boot.html の日英）。失敗の後もこれが残ると、読めなかったと書いた文の真上で「起動しています…」と言う
STARTING = ("AI Office is starting…", "AIオフィス を起動しています…")
# import は通るが mount が落ちる様式（壊れた配布物・実行時の例外の再現）。detail にこの文が出れば mount まで届いた証拠。
# 実物の 3 様式（ui/{iso,pixel,cockpit}/index.js）と同じく、先頭で root を空にして様式の class を付け、殻を組み始めてから
# 投げる（殻は画面を覆う）。root を消さない偽物だと、起動画面が DOM から外れて白くなる穴が門に写らない（第 B 波の検収）
BROKEN_MOUNT = "mount broken by demo_site_smoke"
BROKEN_MODULE = (
    "export async function mount(root) {\n"
    "  root.replaceChildren();\n"
    "  root.className = 'hud ui-broken';\n"
    "  const shell = document.createElement('div');\n"
    "  shell.style.cssText = 'position:fixed;inset:0;background:#f4f4fa';\n"
    "  root.append(shell);\n"
    f"  throw new Error({BROKEN_MOUNT!r});\n"
    "}\n")
# boot.html が mount の失敗を拾った印（console.error の先頭）。これを待ってから画面を読む
# （#bootmsg を待つと、文言が DOM から外れたときに 60 秒の timeout になって「何が見えていたか」が残らない）
BOOT_STARTFAIL = "[boot] failed to start the UI"


def screen_faults(r):
    """失敗の画面が人に届いていない理由の列（空＝届いている）。白い画面・覆われた文言・「起動しています…」の見出し・
    脈打ち続けるマーク・#app に残った様式の class（暗い様式の背景の上に暗い文字が載る）・マークのグラデーション"""
    f = []
    if not r.get("connected"):
        f.append("起動画面が DOM から外れたまま（白い画面）")
    elif not r.get("shown"):
        f.append("失敗の文言が画面内に見えていない（覆われている・はみ出している）")
    if not r.get("title") or r["title"] in STARTING:
        f.append(f"見出しが「{r.get('title')}」のまま")
    if r.get("anim") != "none":
        f.append(f"マークが脈打ち続ける（animation {r.get('anim')}）")
    if r.get("appClass") != "":
        f.append(f"#app に様式の class が残る（{r.get('appClass')!r}）")
    if "gradient" in (r.get("grad") or ""):
        f.append("起動画面のマークがグラデーション（デザイン方針: 紫系のグラデーション禁止）")
    return f


def _mount_fail(page, url, key):
    """mount が落ちるページを開き、boot.html が失敗を拾った後の起動画面と保存値を読む"""
    with page.expect_console_message(lambda m: m.type == "error" and BOOT_STARTFAIL in m.text, timeout=60000):
        page.goto(url)
    page.wait_for_timeout(300)
    r = page.evaluate(BOOT_FAIL)
    r["kept"] = _kept(page, key)
    return r


def _kept(page, key):
    return page.evaluate(f"() => localStorage.getItem({key!r})")


def _boot_text(page):
    return page.evaluate("() => (document.querySelector('#bootmsg') || {}).textContent || ''")


def _fresh(browser, saved=None, key=None, block=None, broken=None, locale="en-US"):
    """新しい context（localStorage が空）で 1 枚開く。saved を指定したら起動前に保存値を入れる。
    block を指定したら /ui/<block>/index.js を 404 にする（import 失敗の再現・様式 1 つか様式の列）。
    叩かれた回数を数える（blocked＝全部の合計・様式ごとは hits[様式]）。
    broken を指定したら /ui/<broken>/index.js を「mount が投げる」module にすり替える（回数は hits['broken']）。
    locale＝navigator.language（boot.html は ja で始まれば日本語の文を書く）"""
    ctx = browser.new_context(viewport={"width": 1440, "height": 900}, device_scale_factor=1, locale=locale)
    if saved is not None:
        ctx.add_init_script(f"try {{ localStorage.setItem({key!r}, {saved!r}); }} catch (e) {{}}")
    hits = {"blocked": 0}
    def denier(one):                  # route の handler は (route, request) で呼ばれる＝様式は閉包で持つ
        def deny(route):
            hits["blocked"] += 1
            hits[one] = hits.get(one, 0) + 1
            route.fulfill(status=404, body="blocked by demo_site_smoke")
        return deny
    for one in ([block] if isinstance(block, str) else block or []):
        ctx.route(f"**/ui/{one}/index.js", denier(one))
    if broken:
        def serve_broken(route):
            hits["broken"] = hits.get("broken", 0) + 1
            route.fulfill(status=200, content_type="text/javascript; charset=utf-8", body=BROKEN_MODULE)
        ctx.route(f"**/ui/{broken}/index.js", serve_broken)
    page = ctx.new_page()
    navs = []
    page.on("framenavigated", lambda f: navs.append(f.url) if f == page.main_frame else None)
    return ctx, page, hits, navs


def main():
    from playwright.sync_api import sync_playwright

    # 生成物はコミットしない（dist/ は gitignore）。検査の直前に必ず作り直す＝ソースが正本
    subprocess.run([sys.executable, str(ROOT / "tools" / "build_demo.py")],
                   check=True, capture_output=True)
    srv, port = _serve()
    base = f"http://127.0.0.1:{port}"
    api_hits, errors, ng = [], [], 0
    cp_api, cp_err = [], []
    saved_runs, fallback, explicit, both = [], {}, {}, {}
    streamrun, mountfail, persisted, legacy = {}, {}, [], {}
    mf_same, mf_stream, mf_saved = {}, {}, {}
    cp = {}
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(args=["--use-gl=swiftshader", "--disable-gpu"])
            page = browser.new_page(viewport={"width": 1440, "height": 900}, device_scale_factor=1)
            _watch(page, api_hits, errors)
            # ?demo=1 を付けない＝ページに焼いた <meta name="office-demo"> だけでデモに入ること
            page.goto(f"{base}/")
            page.wait_for_function(READY, timeout=60000)
            page.wait_for_timeout(400)
            dump = page.evaluate("window.__office.dumpWorld()")
            stats = page.evaluate("window.__office.stats && window.__office.stats()") or {}
            meta = page.evaluate(STYLE_META)
            # R98: 台帳（?ui=pixel）も同梱されている＝⚙「画面」で切り替えた人が空振りしない
            page.goto(f"{base}/?ui=pixel")
            page.wait_for_function(READY, timeout=60000)
            page.wait_for_timeout(300)
            px = page.evaluate("() => ({ style: window.__office.style,"
                               "  rows: document.querySelectorAll('#agents .pxrow').length })")
            page.close()

            # R99-P4-4: コックピット（?ui=cockpit）。別 context＝/api と console を様式ごとに数える
            ctx, page, _, _ = _fresh(browser)
            _watch(page, cp_api, cp_err)
            page.goto(f"{base}/?ui=cockpit")
            try:
                page.wait_for_function(READY, timeout=60000)
                page.wait_for_timeout(600)
                cp = page.evaluate(COCKPIT)
            except Exception as e:           # 立たなかった理由（起動画面の文言）を残す
                cp = {"error": f"{type(e).__name__}: {str(e)[:120]}", "boot": _boot_text(page)}
            ctx.close()

            # 保存値（⚙「画面」で選んだ様式）の全値で、?ui= 無しの再訪問が起動できること
            for style in meta["styles"]:
                ctx, page, _, _ = _fresh(browser, saved=style, key=meta["key"])
                errs = []
                _watch(page, [], errs)
                page.goto(f"{base}/")
                run = {"saved": style}
                try:
                    page.wait_for_function(READY, timeout=60000)
                    run.update(page.evaluate(f"() => ({{ style: window.__office.style,"
                                             f"  kept: localStorage.getItem({meta['key']!r}) }})"))
                except Exception as e:
                    run.update(error=f"{type(e).__name__}", boot=_boot_text(page))
                run["errors"] = errs[:2]
                saved_runs.append(run)
                ctx.close()

            # 保存値の様式が import できない（古い配布物・一部欠け）→ 保存値を捨てて既定で 1 回だけ起動し直す
            blocked = [s for s in meta["styles"] if s != meta["dflt"]][-1]
            ctx, page, hits, navs = _fresh(browser, saved=blocked, key=meta["key"], block=blocked)
            page.goto(f"{base}/")
            try:
                page.wait_for_function(READY, timeout=60000)
                page.wait_for_timeout(300)
                fallback = page.evaluate(f"() => ({{ style: window.__office.style,"
                                         f"  kept: localStorage.getItem({meta['key']!r}) }})")
            except Exception as e:
                fallback = {"error": f"{type(e).__name__}", "boot": _boot_text(page)}
            fallback.update(blocked=blocked, hits=hits["blocked"], navs=len(navs))
            ctx.close()

            # 無効な ?ui=（?ui=legacy＝R52 で削除した旧 UI）は明示ではない: resolveStyle が無視して保存値の様式を選ぶ＝
            # 読めなければ保存値を捨てて既定で 1 回だけ起動し直す（「?ui= が付いていれば明示」と判定すると詰む）
            ctx, page, hits, navs = _fresh(browser, saved=blocked, key=meta["key"], block=blocked)
            page.goto(f"{base}/?ui=legacy")
            try:
                page.wait_for_function(READY, timeout=60000)
                page.wait_for_timeout(300)
                legacy = page.evaluate(f"() => ({{ style: window.__office.style,"
                                       f"  kept: localStorage.getItem({meta['key']!r}) }})")
            except Exception as e:
                legacy = {"error": f"{type(e).__name__}", "boot": _boot_text(page)}
            legacy.update(hits=hits["blocked"], navs=len(navs))
            ctx.close()

            # ?ui=<様式> で明示した様式の失敗は、既定へすり替えずに失敗を見せる（文言に WebGL を書かない）。
            # 保存値も同じ様式にしておく＝「保存値と一致したら消す」だけの実装だと、明示の失敗で保存値が消える（統括の裁定: 消さない）
            ctx, page, hits, _ = _fresh(browser, saved=blocked, key=meta["key"], block=blocked)
            page.goto(f"{base}/?ui={blocked}")
            try:
                page.wait_for_function("document.querySelector('#bootmsg code')", timeout=60000)
                page.wait_for_timeout(300)
                explicit = page.evaluate(BOOT_FAIL)
                explicit["kept"] = _kept(page, meta["key"])
            except Exception as e:
                explicit = {"error": f"{type(e).__name__}", "boot": _boot_text(page)}
            explicit.update(hits=hits["blocked"])
            ctx.close()

            # 配信 URL（?stream=1＝iso を強制）の失敗も明示と同じ: 保存値が同じ様式でも消さない・ほかの様式へ退避しない。
            # 日本語の画面で開く＝import の失敗の日本語の文にも様式名が入ること（en だけだと日本語の文から様式名が落ちても緑）
            stream = meta["stream"]
            ctx, page, hits, _ = _fresh(browser, saved=stream, key=meta["key"], block=stream, locale="ja-JP")
            page.goto(f"{base}/?stream=1")
            try:
                page.wait_for_function("document.querySelector('#bootmsg code')", timeout=60000)
                page.wait_for_timeout(300)
                streamrun = page.evaluate(BOOT_FAIL)
                streamrun["kept"] = _kept(page, meta["key"])
            except Exception as e:
                streamrun = {"error": f"{type(e).__name__}", "boot": _boot_text(page)}
            streamrun.update(style=stream, hits={k: v for k, v in hits.items() if k != "blocked"})
            ctx.close()

            # import は通るが mount が落ちる様式を ?ui= で開く → 保存値（普段の様式＝既定）を壊れた様式で上書きしない
            # （persistStyle は mount が成功した後。統括の裁定）
            # 実物と同じく root を空にしてから投げる＝起動画面を戻さないと失敗の文言が白い画面の裏に消える
            def mount_case(saved, url, broken, locale="en-US"):
                ctx, page, hits, _ = _fresh(browser, saved=saved, key=meta["key"], broken=broken, locale=locale)
                try:
                    r = _mount_fail(page, f"{base}{url}", meta["key"])
                except Exception as e:
                    r = {"error": f"{type(e).__name__}: {str(e)[:120]}", "boot": _boot_text(page)}
                r.update(hits=hits.get("broken", 0))
                ctx.close()
                return r
            mountfail = mount_case(meta["dflt"], f"/?ui={blocked}", blocked)
            # 明示の mount の失敗では、保存値が同じ様式でも消さない（?ui= と ?stream=1 の両方）
            mf_same = mount_case(blocked, f"/?ui={blocked}", blocked)
            mf_stream = mount_case(stream, "/?stream=1", stream)
            # 保存値から来た様式の mount の失敗: 保存値を捨てる（捨てないと再読み込みしても同じ様式で落ち続ける）・
            # 起動画面に失敗を見せる・この場で別の様式を mount し直さない。日本語の画面で開く（mount の失敗の日本語の文）
            mf_saved = mount_case(blocked, "/", blocked, locale="ja-JP")

            # R105: ?ui= で開いただけでは保存しない（保存は ⚙「画面」で選んだときだけ）。以前は mount の成功のたびに書き、
            # 一度でも開いたブラウザは全部「選んだ」ことになって、既定を切り替えても誰の画面も変わらなかった。
            # 既定以外の全様式で見る（起動しきってから少し待っても保存値が無いこと）
            for chosen in [s for s in meta["styles"] if s != meta["dflt"]]:
                ctx, page, _, _ = _fresh(browser)
                page.goto(f"{base}/?ui={chosen}")
                run = {"style": chosen}
                try:
                    page.wait_for_function(READY, timeout=60000)
                    page.wait_for_timeout(500)
                except Exception as e:
                    run["error"] = f"{type(e).__name__}"
                try:
                    run["kept"] = _kept(page, meta["key"])
                except Exception:
                    run["kept"] = "?"
                persisted.append(run)
                ctx.close()

            # 保存値の様式も既定も読めない（配布物の欠け）→ 保存値は捨てる（次の訪問で同じ様式を読みに行って詰まない）・
            # 既定も 1 回だけ試して、失敗の文言を出す（黙って白い画面にしない）
            ctx, page, hits, _ = _fresh(browser, saved=blocked, key=meta["key"], block=[blocked, meta["dflt"]])
            page.goto(f"{base}/")
            try:
                page.wait_for_function("document.querySelector('#bootmsg code')", timeout=60000)
                page.wait_for_timeout(300)
                both = page.evaluate(BOOT_FAIL)
                both["kept"] = page.evaluate(f"() => localStorage.getItem({meta['key']!r})")
            except Exception as e:
                both = {"error": f"{type(e).__name__}", "boot": _boot_text(page)}
            both.update(hits={k: v for k, v in hits.items() if k != "blocked"})
            ctx.close()
            browser.close()
    finally:
        srv.shutdown()

    agents = len(dump.get("agents") or [])
    if agents >= 6:
        print(f"  ✓ 静的デモ: サーバー無しで {agents} 体が出勤（drawCalls {stats.get('drawCalls')}）")
    else:
        print(f"  ✗ 静的デモの出勤が少なすぎる: {agents} 体")
        ng += 1
    if px.get("style") == "pixel" and px.get("rows", 0) >= 6:
        print(f"  ✓ 静的デモ: 台帳（?ui=pixel）も {px['rows']} 行で立つ")
    else:
        print(f"  ✗ 静的デモの台帳が立たない: {px}")
        ng += 1
    if api_hits:
        print(f"  ✗ 静的デモが /api/ を叩いている（ホスト先には存在しない）: {api_hits[:3]}")
        ng += 1
    else:
        print("  ✓ 静的デモ: /api/ へのリクエスト 0 本")
    if errors:
        print(f"  ✗ console/page error: {errors[:3]}")
        ng += 1
    else:
        print("  ✓ 静的デモ: console error 0")

    # ── R99-P4-4: コックピット ─────────────────────────────────
    plus = cp.get("plus") or {}
    if (cp.get("style") == "cockpit" and cp.get("rows", 0) >= 6 and cp.get("people", 0) >= 6):
        print(f"  ✓ 静的デモ: コックピット（?ui=cockpit）が台帳 {cp['rows']} 行・部屋 {cp['people']} 人で立つ")
    else:
        print(f"  ✗ 静的デモのコックピットが立たない: {cp}")
        ng += 1
    if plus.get("hidden") is True and plus.get("rects") == 0:
        print("  ✓ 静的デモ: コックピットは ＋端末 を出さない（本物の端末は Mac の前でだけ）")
    else:
        print(f"  ✗ 静的デモのコックピットに ＋端末 が見えている（または見つからない）: {plus or None}")
        ng += 1
    # 端末の面: デモには端末が無い＝画面（#cp-xterm・タブ列）を畳んで理由を足元に書く（空の箱を黙って出さない）
    xterm, tabbar, note = cp.get("xterm"), cp.get("tabbar"), cp.get("note") or {}
    reason = cp.get("reason") or ""
    folded = (xterm is not None and xterm["rects"] == 0 and tabbar is not None and tabbar["rects"] == 0)
    if folded and seen(note) and reason and reason != "cp_term_demo" and note.get("text") == reason:
        print(f"  ✓ 静的デモ: コックピットは端末の画面（#cp-xterm・タブ列）を畳み、足元に理由「{reason}」を出す")
    else:
        print(f"  ✗ 静的デモのコックピットが端末の画面を畳んでいない（または理由が足元に無い）: "
              f"#cp-xterm {xterm} / タブ列 {tabbar} / #cp-term-note {note or None} / 理由の文言 {reason!r}")
        ng += 1
    # 畳んだ後も面の形が崩れない: タブ列を畳むと自動配置で本体と足元が繰り上がり、足元の帯が 1fr の行を取って
    # 列の真ん中に浮いた（実測 1440×900）。足元は列の底の細い帯・本体（#cp-term）が残りの丈を持つ
    # R110: 文字 14px＋32px の枠つきボタンで足元は 1 行なら 44〜48px・ボタンが 2 段目に折れると 54px 前後＝「細い帯」の上限は 72。
    # 窓（.cpwin）の 1px の枠の内側に足元が収まるので、列の底との差は 2px まで許す。
    foot, center, term_h = cp.get("foot") or {}, cp.get("center") or {}, cp.get("termH") or 0
    if foot and 0 < foot["h"] <= 72 and abs(foot["bottom"] - center.get("bottom", -99)) <= 2 and term_h >= center.get("h", 0) // 2:
        print(f"  ✓ 静的デモ: 端末を畳んでも面の形は崩れない（足元 {foot['h']}px が列の底・本体 {term_h}px）")
    else:
        print(f"  ✗ 静的デモで端末を畳んだ面の形が崩れている（足元の帯が列の底に無い・本体が潰れた）: "
              f"足元 {foot} / 列 {center} / 本体 {term_h}px")
        ng += 1
    # 足元の文がこの画面の ＋ を案内しない（＋ はデモで出さない＝「＋ から起こしたセッションだけ打てます」は嘘）。
    # ＋ に触れてよいのは「手元の Mac で」と場所を言うときだけ
    text = note.get("text") or ""
    guides = text in (cp.get("forbid") or []) or ("＋" in text and "Mac" not in text)
    if note and not guides:
        print("  ✓ 静的デモ: 端末の足元は ＋ を案内しない（読み取り専用・打てますの文を出さない）")
    else:
        print(f"  ✗ 静的デモの端末の足元が、デモに無い ＋ を案内している: 「{text}」")
        ng += 1
    # 許可待ちの表示（#cp-work＝いまの仕事と ❗ の問い・#cp-dlg）はデモでも畳まない
    work, dlg = cp.get("work"), cp.get("dlg")
    if seen(work) and work["kids"] > 0 and dlg is not None and dlg["display"] != "none":
        print(f"  ✓ 静的デモ: 端末の面の #cp-work（{work['kids']} 要素）と #cp-dlg は畳まずに在る")
    else:
        print(f"  ✗ 静的デモで #cp-work か #cp-dlg が消えている（許可待ちの表示はデモでも残す）: "
              f"#cp-work {work} / #cp-dlg {dlg}")
        ng += 1
    if cp_api:
        print(f"  ✗ 静的デモのコックピットが /api/ を叩いている: {cp_api[:3]}")
        ng += 1
    else:
        print("  ✓ 静的デモ: コックピットの /api/ へのリクエスト 0 本")
    if cp_err:
        print(f"  ✗ コックピットの console/page error: {cp_err[:3]}")
        ng += 1
    else:
        print("  ✓ 静的デモ: コックピットの console error 0")

    # 同梱した vendor は全部 LICENSE 付き（three も xterm も MIT＝再配布物に原文を必ず同梱する）
    vendor = SITE / "ui" / "vendor"
    pkgs = sorted(d.name for d in vendor.iterdir() if d.is_dir()) if vendor.is_dir() else []
    missing = [p for p in pkgs if not (vendor / p / "LICENSE").is_file()]
    if pkgs and not missing and {"three", "xterm"} <= set(pkgs):
        print(f"  ✓ 静的デモ: vendor {len(pkgs)} 個（{'・'.join(pkgs)}）すべて LICENSE を同梱")
    else:
        print(f"  ✗ 静的デモの vendor に LICENSE が無い（MIT 再配布の義務）: 同梱 {pkgs}・欠け {missing}")
        ng += 1

    # 保存値の全様式で起動できる（⚙「画面」で選んだまま再訪問しても詰まない）
    bad = [r for r in saved_runs if r.get("style") != r["saved"] or r.get("kept") != r["saved"] or r["errors"]]
    if saved_runs and not bad:
        print(f"  ✓ 静的デモ: 保存値 {len(saved_runs)} 様式（{'・'.join(r['saved'] for r in saved_runs)}）"
              "のどれでも ?ui= 無しで起動できる")
    else:
        print(f"  ✗ 保存値の様式で起動できない（同梱漏れ＝⚙で選んだ人が詰む）: {bad or saved_runs}")
        ng += 1

    # 保存値の様式が読めない → 保存値を捨てて既定で 1 回だけ再試行（ページの再読み込みはしない）
    fb_ok = (fallback.get("style") == meta["dflt"] and fallback.get("kept") != fallback.get("blocked")
             and fallback.get("hits") == 1 and fallback.get("navs") == 1)
    if fb_ok:
        print(f"  ✓ 静的デモ: 保存値 {fallback['blocked']} が読めなくても既定 {meta['dflt']} で起動し、"
              f"保存値を捨てる（再試行 1 回）")
    else:
        print(f"  ✗ 保存値の様式が読めないと詰む（既定へ 1 回だけ退避・保存値を捨てる）: {fallback}")
        ng += 1
    lg_ok = (legacy.get("style") == meta["dflt"] and legacy.get("kept") != blocked
             and legacy.get("hits") == 1 and legacy.get("navs") == 1)
    if lg_ok:
        print(f"  ✓ 静的デモ: 無効な ?ui=legacy は明示ではない＝保存値 {blocked} が読めなければ捨てて既定 {meta['dflt']} "
              "で起動（再試行 1 回）")
    else:
        print(f"  ✗ 無効な ?ui=legacy を明示として扱っている（保存値を捨てない・既定へ退避しない）: {legacy}")
        ng += 1
    ex_ok = (not explicit.get("error") and not explicit.get("office") and explicit.get("hits") == 1
             and "WebGL" not in (explicit.get("boot") or "") and blocked in (explicit.get("lead") or "")
             and not screen_faults(explicit))
    if ex_ok:
        print(f"  ✓ 静的デモ: ?ui={blocked} の失敗は既定へすり替えずに理由を出す（boot.html の文に様式名・"
              "文言に WebGL を含まない・見出しは失敗を言いマークは止まる）")
    else:
        print(f"  ✗ ?ui= で明示した様式の失敗の見せ方が違う（すり替え・WebGL の文言・boot.html の文に様式名が無い・"
              f"失敗の画面）: {screen_faults(explicit)} / {explicit}")
        ng += 1
    # 明示の失敗では保存値を消さない（失敗した様式が保存値から来たときだけ消す）
    if not explicit.get("error") and explicit.get("kept") == blocked:
        print(f"  ✓ 静的デモ: ?ui={blocked} の失敗では、同じ様式の保存値 {blocked} を消さない（明示は保存値から来ていない）")
    else:
        print(f"  ✗ ?ui= で明示した様式の失敗で保存値が消えた（消すのは保存値から来た様式の失敗だけ）: "
              f"保存値 {blocked!r} → {explicit.get('kept')!r} / {explicit}")
        ng += 1
    st = streamrun.get("style")
    st_ok = (not streamrun.get("error") and not streamrun.get("office") and streamrun.get("code")
             and streamrun.get("hits") == {st: 1} and streamrun.get("kept") == st
             and st in (streamrun.get("lead") or "") and "WebGL" not in (streamrun.get("boot") or "")
             and not screen_faults(streamrun))
    if st_ok:
        print(f"  ✓ 静的デモ: ?stream=1（{st} を強制）の失敗は保存値 {st} を消さず、ほかの様式へ退避しない（1 回だけ試す・"
              f"日本語の文にも様式名「{streamrun.get('lead')}」）")
    else:
        print(f"  ✗ ?stream=1 の失敗の扱いが違う（保存値が消えた・退避した・試す回数・日本語の失敗の文言・失敗の画面）: "
              f"{screen_faults(streamrun)} / {streamrun}")
        ng += 1
    # mount が落ちた様式を保存値に書かない（persistStyle は mount が成功した後）
    # mount の失敗は、起動画面を戻して文言を見せる（mount が root を空にしても白い画面にしない）
    def mount_seen(r, style):
        return (not r.get("error") and not r.get("office") and r.get("hits") == 1
                and BROKEN_MOUNT in (r.get("detail") or "") and style in (r.get("lead") or "")
                and "WebGL" not in (r.get("boot") or "") and not screen_faults(r))
    if mount_seen(mountfail, blocked) and mountfail.get("kept") == meta["dflt"]:
        print(f"  ✓ 静的デモ: mount が落ちる ?ui={blocked} は保存値 {meta['dflt']} を上書きしない"
              "（保存は mount の成功の後）・root を空にされても起動画面を戻して失敗の文言を画面に出す")
    else:
        print(f"  ✗ mount が落ちた様式が保存値に残った・失敗の文言が画面に見えない（白い画面）・mount まで届いていない: "
              f"保存値 {meta['dflt']!r} → {mountfail.get('kept')!r} / {screen_faults(mountfail)} / {mountfail}")
        ng += 1
    same_ok = mount_seen(mf_same, blocked) and mf_same.get("kept") == blocked
    stream_ok = mount_seen(mf_stream, st) and mf_stream.get("kept") == st
    if same_ok and stream_ok:
        print(f"  ✓ 静的デモ: 明示の mount の失敗（?ui={blocked}・?stream=1）では同じ様式の保存値を消さず、失敗を画面に出す")
    else:
        print(f"  ✗ 明示の mount の失敗で保存値が消えた（または失敗が画面に見えない）: "
              f"?ui={blocked} 保存値 {blocked!r} → {mf_same.get('kept')!r} {screen_faults(mf_same)} / "
              f"?stream=1 保存値 {st!r} → {mf_stream.get('kept')!r} {screen_faults(mf_stream)} / {mf_same} / {mf_stream}")
        ng += 1
    # 保存値から来た様式の mount の失敗: 保存値を捨てる・この場で別の様式を mount しない・次に何が起きるか（既定）を書く
    if (mount_seen(mf_saved, blocked) and mf_saved.get("kept") is None
            and meta["dflt"] in (mf_saved.get("lead") or "")):
        print(f"  ✓ 静的デモ: 保存値 {blocked} の mount が落ちたら、保存値を捨てて失敗を画面に出す"
              f"（再読み込みで既定 {meta['dflt']} が立つと書く・日本語の文「{mf_saved.get('lead')}」）")
    else:
        print(f"  ✗ 保存値から来た様式の mount の失敗で詰む（保存値が残る＝毎回同じ様式で落ちる・白い画面・既定の案内が無い）: "
              f"保存値 {blocked!r} → {mf_saved.get('kept')!r} / {screen_faults(mf_saved)} / {mf_saved}")
        ng += 1
    bad_p = [r for r in persisted if r.get("error") or r.get("kept") is not None]
    if persisted and not bad_p:
        print(f"  ✓ 静的デモ: ?ui=（{'・'.join(r['style'] for r in persisted)}）で開いただけでは保存しない（R105・保存は ⚙ で選んだときだけ）"
              "（次の訪問は保存値か既定）")
    else:
        print(f"  ✗ ?ui= で開いただけで保存された（または起動しなかった）: {bad_p or persisted}")
        ng += 1
    # 保存値の様式も既定も読めない: 保存値は捨てる・両方を 1 回ずつ試す・失敗の文言（boot.html の文）を出す
    both_ok = (not both.get("error") and not both.get("office") and both.get("code") and both.get("lead")
               and both.get("kept") is None and both.get("hits") == {blocked: 1, meta["dflt"]: 1}
               and "WebGL" not in (both.get("boot") or "") and not screen_faults(both))
    if both_ok:
        print(f"  ✓ 静的デモ: 保存値 {blocked} も既定 {meta['dflt']} も読めないとき、保存値を捨てて失敗を見せる"
              "（各 1 回だけ試す）")
    else:
        print(f"  ✗ 保存値の様式も既定も読めないときの退避が違う（保存値が残る・試す回数・失敗の文言・失敗の画面）: "
              f"{screen_faults(both)} / {both}")
        ng += 1

    if ng:
        print(f"✗ 公開デモ {ng}件失敗")
        return 1
    print("✓ 公開デモ（静的ホスト）成立")
    return 0


if __name__ == "__main__":
    sys.exit(main())
