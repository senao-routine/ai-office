#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""スマホPWAスモーク（Playwright必須）
使い方: python pwa_smoke.py <base_url> <device_id> <secret> <token> <out.png>
"""
import json
import re
import sys
import urllib.request
from pathlib import Path

try:
    from playwright.sync_api import sync_playwright
except ImportError as exc:
    print(f"✗ PWAスモーク: Playwright import失敗: {exc}")
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


def main(argv):
    if len(argv) != 6:
        print("使い方: pwa_smoke.py <base_url> <device_id> <secret> <token> <out.png>")
        return 2

    base_url, device_id, secret, token, out = argv[1:]
    cred = {"d": device_id, "s": secret, "t": token, "e": 0}
    # R77: 3D退避の発火条件として /ui/** を意図的に落とすので、その読込失敗だけは想定内。
    # それ以外の console error は従来どおり1件でも不合格（嘘greenを作らない）。
    _EXPECTED = ("net::ERR_FAILED", "Failed to load resource")

    def _unexpected(msgs):
        return [m for m in msgs if not any(x in m for x in _EXPECTED)]

    console_errors = []
    page_errors = []
    instruct_requests = []

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch()
            try:
                page = browser.new_page(viewport={"width": 390, "height": 844})
                page.on(
                    "console",
                    lambda message: console_errors.append(message.text)
                    if message.type == "error"
                    else None,
                )
                page.on("pageerror", lambda error: page_errors.append(str(error)))

                def on_request(request):
                    if request.method == "POST" and request.url.split("?", 1)[0].endswith("/instruct"):
                        instruct_requests.append(request)

                page.on("request", on_request)

                # worker.js の saveCred と同じJSONを、アプリのbootより前に注入する。
                cred_json = json.dumps(cred, ensure_ascii=False, separators=(",", ":"))
                page.add_init_script(
                    "localStorage.setItem('aioffice.cred', "
                    + json.dumps(cred_json, ensure_ascii=False)
                    + ");"
                )
                # R122-P3: 3D・ドット絵の両方の部品が読めない端末でも、ホームの会話と許可は使える。
                # 3Dだけの失敗→ドット絵への退避は pwa3d_smoke.py が検査する。
                page.route("**/ui/**", lambda route: route.abort())
                page.route("**/u/**", lambda route: route.abort())   # R102-10: 版つきの道 /u/<BUILD>/ も
                diag_posts = []
                page.on("request", lambda rq: diag_posts.append(rq.post_data or "")
                        if rq.url.endswith("/diag") and rq.method == "POST" else None)
                page.goto(base_url.rstrip("/") + "/app", wait_until="domcontentloaded")
                page.wait_for_timeout(4000)

                assert not _unexpected(console_errors), (
                    "console error: " + "; ".join(console_errors[:5])
                )
                assert not page_errors, "page error: " + "; ".join(page_errors[:5])
                assert page.locator(".tabbar").is_visible(), ".tabbar が非表示"
                assert page.locator("#tabbar > button").evaluate_all(
                    "nodes => nodes.map(n => n.id)"
                ) == ["tb_wait", "tb_office", "tb_set"], "会話・オフィス・設定の3タブが不正"
                # R122-P2/P3: 初期表示は会話ホーム。部屋を読めなくても最近は使える。
                assert page.locator("#list").is_visible(), "部屋を読めなくても最近(#list)が非表示"


                audio_initial_probe = page.evaluate(
                    """() => ({
                        ctx:window.__pwaAudio ? __pwaAudio.ctx : 'missing',
                        enabled:window.__pwaAudio ? __pwaAudio.enabled : null,
                        stored:localStorage.getItem('aioffice.sound')
                    })"""
                )
                assert audio_initial_probe == {
                    "ctx": None,
                    "enabled": False,
                    "stored": None,
                }, "ロード直後の音声既定OFF/AudioContext生成ピン不正: " + repr(audio_initial_probe)

                page.locator("#tb_set").click()
                page.locator("#setwrap.open").wait_for(state="visible", timeout=5000)
                sound_rows = page.locator("#setwrap .setrow").filter(has_text="効果音")
                assert sound_rows.count() == 1, "設定に効果音トグル行がありません"
                page.locator("#sg_sd_on").click()
                page.wait_for_function(
                    "() => !!window.__pwaAudio && __pwaAudio.ctx !== null",
                    timeout=3000,
                )
                audio_on_probe = page.evaluate(
                    """() => ({
                        ctx:__pwaAudio.ctx !== null,
                        enabled:__pwaAudio.enabled,
                        stored:localStorage.getItem('aioffice.sound'),
                        played:__pwaAudio.played
                    })"""
                )
                assert (
                    audio_on_probe["ctx"] is True
                    and audio_on_probe["enabled"] is True
                    and audio_on_probe["stored"] == "1"
                    and audio_on_probe["played"] >= 1
                ), "効果音ONのctx生成・保存・確認音ピン不正: " + repr(audio_on_probe)
                # B2（遊びの掟）: 鳴らすのは あなた待ち・送った・許可できた の 3 種類だけ・全体で 3 秒に 1 回まで（タップのたびに鳴らない）
                burst = page.evaluate(
                    "() => { const p0 = __pwaAudio.played; for (const k of ['select', 'cursor', 'talk', 'send', 'send', 'ok', 'attn']) playSE(k);"
                    " return __pwaAudio.played - p0; }")
                assert burst <= 1, "効果音が連打で鳴り続ける（3 秒に 1 回まで）: " + repr(burst)
                page.locator("#sg_sd_off").click()
                page.wait_for_function(
                    "() => !__pwaAudio.enabled && localStorage.getItem('aioffice.sound') === null",
                    timeout=1000,
                )
                audio_off_probe = page.evaluate(
                    """() => ({
                        enabled:__pwaAudio.enabled,
                        stored:localStorage.getItem('aioffice.sound')
                    })"""
                )
                assert audio_off_probe == {"enabled": False, "stored": None}, (
                    "効果音OFFのlocalStorage掃除ピン不正: " + repr(audio_off_probe)
                )
                page.locator("#st_close").click()

                # R122-P3: 両方の部品が読めなくてもホームを保ち、最近から会話を開ける。
                # 旧2DのDOM（#map/#mapframe/.openclaw）は作らない。
                page.wait_for_function(
                    "() => typeof SCENE3D !== 'undefined' && SCENE3D === 'failed'",
                    timeout=15000)
                fb = page.evaluate(
                    """() => ({
                        view: VIEW,
                        twoD: document.querySelectorAll('#map,#mapframe,.openclaw').length,
                        cards: document.querySelectorAll('#list .recentrow').length,
                        lastOffice: !!localStorage.getItem('aioffice.lastOffice'),
                    })""")
                assert fb["view"] == "wait", f"部屋を読めないときホームから移動した: {fb}"
                # R102-8（別モデルレビュー）: リストへ自動で退避したまま（オフィスへ戻らなくても）理由は 1 回送る
                assert len(diag_posts) == 1 and '"k":"load"' in diag_posts[0], f"退避の時点で /diag が送られていない: {diag_posts}"
                assert fb["twoD"] == 0, f"2DのDOMが残っている: {fb}"
                seed_count = page.evaluate(
                    """() => {
                        const raw = localStorage.getItem('aioffice.lastOffice');
                        const office = raw ? JSON.parse(raw) : {};
                        return recentAgents(office).length;
                    }""")
                assert fb["cards"] == seed_count, (
                    f"リストのカード数({fb['cards']}) != 社員数({seed_count})")
                assert fb["lastOffice"], "aioffice.lastOffice が未保存"
                # R79: 2D撤去後は OpenClaw 帯そのものが無い。外部(OpenClaw)社員は
                # リストに出る（「3Dに居るものは必ず一覧にも居る」の退避側の相方）。
                # 退避で既にリスト表示なのでタブ操作は不要（viewを動かさない＝後続検査の前提を守る）
                list_texts = page.locator("#list .recentrow").all_inner_texts()
                assert any("OpenClaw" in s for s in list_texts), (
                    "外部(OpenClaw)社員がリストに出ていません: " + repr(list_texts[:5])
                )
                # officeタブへ戻ると3D不可の告知が出る（白画面にしない）
                page.locator("#tb_office").click()
                page.wait_for_timeout(300)
                assert page.locator("#office_notice").is_visible(), "部屋を読めない告知(#office_notice)が出ていない"
                # R102-8: 起動できなかった理由を画面に出し、中継のログへ 1 回だけ送る（本人の iPhone でだけ起きた失敗を掴むため）
                why = page.locator("#office_notice").inner_text()
                assert "読み込めませんでした" in why, f"起動できなかった理由が出ていない: {why!r}"
                assert len(diag_posts) == 1 and '"k":"load"' in diag_posts[0], f"/diag が 1 回送られていない: {diag_posts}"
                # R122-P2/P3: 部屋を読めなくてもホームの「あなたの番」と最近へ届く。
                page.locator("#tb_wait").click()
                page.wait_for_timeout(200)
                home = page.evaluate("""() => ({ view:VIEW,
                  preview:document.getElementById('homeoffice').getBoundingClientRect().height,
                  cards:document.querySelectorAll('#attncards .attncard,#attncards .apvrow').length,
                  recent:document.querySelectorAll('#list .recentrow').length })""")
                assert home["view"] == "wait" and abs(home["preview"] - 230) <= 1 and home["cards"] and home["recent"], (
                    "部屋を読めないときホームの操作が消える: " + repr(home))

                # R103-2a: あなた待ちは専用タブの縦一覧。部屋の読込失敗中も操作できる。
                page.locator("#tb_wait").click()
                page.wait_for_timeout(400)
                assert page.locator("#wait").is_visible(), "あなた待ち(#wait)が非表示"
                assert page.locator("#room #attncards").count() == 0, "部屋にあなた待ちを重ねている"
                attn_seed = page.evaluate(
                    """() => {
                        const raw = localStorage.getItem('aioffice.lastOffice');
                        const office = raw ? JSON.parse(raw) : {};
                        return (office.employees || []).filter(employee =>
                            !!employee.question || Number(employee.approvalMin || 0) > 0
                        );
                    }"""
                )
                # R86-H: seed は「質問(選択肢つき)」と「許可要求」の2件。前者が先頭カード。
                assert len(attn_seed) == 2, "❗seed社員が2名ではありません: " + repr(attn_seed)
                attn_seed = [e for e in attn_seed if e.get("question")]
                assert len(attn_seed) == 1, "質問付きseedが1名ではありません: " + repr(attn_seed)
                # R116-S1: 許可待ちはあなた待ちの先頭の決裁カード（#apvslot_wait）・2 件目からは 1 行（.apvrow）
                listed = page.evaluate("""() => document.querySelectorAll('#attncards .attncard, #attncards .apvrow').length
                  + (document.querySelector('#apvslot_wait > #apvwrap.open') ? 1 : 0)""")
                assert listed == 2, "あなた待ちが全件表示でない"
                attn_card = page.locator("#attncards .attncard[data-kind='question']").first
                option_buttons = attn_card.locator(".attnoptions .attnoption")
                assert option_buttons.count() == 3, (
                    "質問カードの選択肢ボタンが3個ではありません: "
                    + str(option_buttons.count())
                )
                # R102-1: 推奨は絵文字（⭐）でなく語の札（.qopt-rec「推奨」）・ラベルは (Recommended) を外した全文
                first_opt = option_buttons.first
                assert (first_opt.locator(".qopt-label").inner_text() == "案A"
                        and first_opt.locator(".qopt-rec").inner_text() == "推奨"
                        and "⭐" not in first_opt.inner_text()), (
                    "Recommended表示が不正: " + repr(first_opt.inner_text())
                )
                assert "(Recommended)" not in option_buttons.first.inner_text(), (
                    "Recommended原文が表示に残っています"
                )
                # R86-H: 選択肢があるときは汎用の承認/停止/報告を出さない（PCと同じ規則）
                # ＝選択肢3件＋「ほかの答えを書く…」の4個ちょうど（R102-1: 名前のボタン＝セッションを開く は数えない）。
                acts = attn_card.locator(".attnoptions button, .attnactions button")
                assert acts.count() == 4, (
                    "選択肢と汎用ボタンが二重に出ている/足りない: " + str(acts.count())
                )
                option_buttons.first.click()             # R102-1: 1 タップ → 2 秒の取り消しの後に送る
                for _ in range(50):
                    if instruct_requests:
                        break
                    page.wait_for_timeout(100)
                assert instruct_requests, "/instruct POSTが観測できません"
                post_data = json.loads(instruct_requests[-1].post_data or "{}")
                assert post_data.get("session") == attn_seed[0].get("session"), (
                    "質問カードの送信先sessionが不正: " + repr(post_data)
                )
                assert post_data.get("sig") and post_data.get("text"), (
                    "質問カードの署名付きPOST封筒が不正: " + repr(post_data)
                )
                assert post_data.get("text") == "選択肢「案A (Recommended)」でお願いします。", (
                    "選択肢の送信文言が不正: " + repr(post_data)
                )
                page.wait_for_function(
                    "() => !!document.querySelector('#attncards .attnsent') && "
                    "document.querySelector('#attncards .attnsent').textContent.includes('送信しました')",
                    timeout=5000,
                )
                # R86-H: 許可要求のカードは断定の「承認が必要です」を出さない
                # （スマホからは実行の許可を渡せない＝言えるのは事実だけ）
                perm_txt = page.evaluate(
                    "() => [...document.querySelectorAll('#attncards .attncard, #attncards .apvrow, #apvslot_wait > #apvwrap')]"
                    "  .map(n => n.textContent || '').join(' | ')")
                assert "承認が必要です" not in perm_txt, (
                    "断定の『承認が必要です』が残っています: " + perm_txt[:160])
                # R112-P→R116-S1: ここで許可できないときは理由を 1 行で正確に（P7 の許可待ちは ask.remote の無い旧い形＝読み切れない→
                # 「この操作は Mac で許可してください」）・［許可］は出さず［拒否］［拒否して指示…］は残す。部屋を出せない端末（この経路）でも同じカード
                why = page.evaluate(
                    """() => { const c = document.querySelector('#apvslot_wait > #apvwrap.open'); if (!c) return null;
                      const a = document.getElementById('apv_allow');
                      return { state: c.getAttribute('data-state'), why: (document.getElementById('apvwhy') || {}).textContent || '',
                               allow: !!a && !a.hidden, deny: (document.getElementById('apvdeny') || {}).textContent || '',
                               free: (document.getElementById('apvdenywrite') || {}).textContent || '' }; }""")
                assert (why and why["state"] == "mac-only" and why["why"].startswith("この操作は Mac で許可してください")
                        and not why["allow"] and why["deny"] == "拒否" and why["free"] == "拒否して指示…"), (
                    "R112-P 許可できない理由と導線が違う: " + repr(why))
                assert not _unexpected(console_errors), (
                    "console error: " + "; ".join(console_errors[:5])
                )
                assert not page_errors, "page error: " + "; ".join(page_errors[:5])

                # R103-4: シート本文は待たずに表示し、返信欄は本文スクロールから分けて固定する。
                page.locator("#tb_wait").click()
                page.wait_for_timeout(300)
                recent_session = page.locator("#list .recentrow").first.get_attribute("data-sess")
                page.locator("#list .recentrow").first.click()
                page.locator("#termwrap.open").wait_for(state="visible", timeout=5000)
                assert page.evaluate("(s) => !!TERM && TERM.session === s", recent_session), "最近から本人の会話を開けない"
                assert page.locator("#term_close").get_attribute("aria-label") == "戻る", "会話の戻る操作が無い"
                page.locator("#term_close").click()
                # シート固有の既存契約は、最近の会話導線と分けて明示的に開く。
                page.evaluate("(s) => openSheet(sessionAgent(s))", recent_session)
                page.locator("#sheetwrap.open").wait_for(state="visible", timeout=5000)
                # 開いた直後は下から出る途中（transform）＝位置は動き終わってから測る（もう一度 openSheet すると出直しになる）
                page.wait_for_function("""() => { const s = document.querySelector('#sheetwrap .sheet');
                    if (!s) return false; const a = s.getBoundingClientRect().top; return a + s.offsetHeight <= innerHeight + 1; }""",
                                       timeout=5000)
                page.wait_for_timeout(250)
                sheet_now = page.evaluate("""() => {
                    const sc = document.getElementById('shscroll'), ft = document.getElementById('freetext');
                    const before = ft.getBoundingClientRect(), pad = document.createElement('div');
                    pad.style.height = '900px'; sc.appendChild(pad); sc.scrollTop = sc.scrollHeight;
                    const after = ft.getBoundingClientRect(), send = document.getElementById('sh_send').getBoundingClientRect();
                    const out = { who: document.getElementById('shwho').textContent, typing: !!SHSAY_IV,
                        scroll: sc.scrollTop, top: before.top, afterTop: after.top,
                        visible: after.height >= 44 && after.top >= 0 && after.bottom <= innerHeight
                            && send.height >= 44 && send.bottom <= innerHeight,
                        outside: !sc.contains(ft),
                        entries: [...document.querySelectorAll('#sheetwrap #sh_term,#sheetwrap #sh_dlg')].map(n => n.id),
                        termEligible: canTermFor(SEL) };
                    pad.remove(); return out;
                }""")
                assert sheet_now["who"].strip() and not sheet_now["typing"], (
                    "シートの状態が即時表示されない／タイプライターが残る: " + repr(sheet_now))
                assert sheet_now["scroll"] > 0 and sheet_now["outside"] and sheet_now["visible"] \
                    and abs(sheet_now["top"] - sheet_now["afterTop"]) <= 1, (
                    "本文を下げるとシートの返信欄が見えない: " + repr(sheet_now))
                assert len(sheet_now["entries"]) <= 1 and (not sheet_now["termEligible"] or sheet_now["entries"] == ["sh_term"]), (
                    "R104: 同じセッションに端末と会話の入口が二重に出る／端末の入口が無い: " + repr(sheet_now))
                assert not _unexpected(console_errors), (
                    "console error: " + "; ".join(console_errors[:5])
                )
                assert not page_errors, "page error: " + "; ".join(page_errors[:5])

                Path(out).parent.mkdir(parents=True, exist_ok=True)
                page.screenshot(path=out)

                # ---- R42.2d-2: EN検査パス（office.lang=en でUIチロームが英語化される） ----
                # 既存seedと同型のofficeへ lang:"en" を付けて再push→reload→UI文字列に日本語が
                # 無いことを検査する。ユーザーデータ面（disp/dept/verb/question/選択肢/feed本文/
                # 吹き出し）は対象外＝dept由来のroompill・#shsay・.attnq等はassertに含めない。
                office_seed = page.evaluate(
                    "() => JSON.parse(localStorage.getItem('aioffice.lastOffice')||'{}')"
                )
                assert office_seed.get("employees"), "EN検査: seed officeが空"
                office_seed["lang"] = "en"
                en_req = urllib.request.Request(
                    base_url.rstrip("/") + "/status",
                    data=json.dumps(
                        {"office": office_seed}, ensure_ascii=False
                    ).encode("utf-8"),
                    headers={
                        "Authorization": "Bearer " + token,
                        "Content-Type": "application/json",
                        "User-Agent": "aioffice-pwa-smoke",
                    },
                    method="POST",
                )
                with urllib.request.urlopen(en_req) as en_resp:
                    assert en_resp.status == 200, (
                        f"EN検査: POST /status = {en_resp.status}"
                    )
                page.reload(wait_until="domcontentloaded")
                # reload後はリスト表示のまま＝シーン起動はofficeタブを見た時に始まる（節約）。
                # 先にofficeへ切り替えて❗カードを塗らせ（分岐前で必ず塗る）、failed確定を待つ
                # （自動退避でリストへ戻るが、textContent の検査は表示状態に依存しない）
                page.locator("#tb_office").click()
                page.wait_for_function(
                    "() => typeof SCENE3D !== 'undefined' && SCENE3D === 'failed'",
                    timeout=15000)
                page.wait_for_timeout(300)
                page.wait_for_function("() => window.LANG === 'en'", timeout=15000)
                assert page.evaluate(
                    "() => localStorage.getItem('aioffice.lang')"
                ) == "en", "EN検査: aioffice.lang が保存されていません"
                assert page.evaluate(
                    "() => document.documentElement.lang"
                ) == "en", "EN検査: <html lang> が en になっていません"
                page.locator("#tb_office").click()
                jp_re = re.compile(r"[ぁ-んァ-ヶ一-龥]")
                chrome_texts = page.evaluate(
                    """() => {
                        const grab = (sel) => [...document.querySelectorAll(sel)]
                            .map(n => (n.textContent || '').trim()).filter(Boolean);
                        return {
                            tabbar: grab('#tabbar button'),
                            header: grab('#hstats .ichip'),   // R102-1: 状態の語のチップ（Allow / Ask / Idle）
                            attncards: grab('#attncards .attnactions button')
                                .concat(grab('#wait_title'), grab('#attncards .ktag'), grab('#attncards .attnage'))
                                .concat(grab('#attncards .attngo'),
                                        grab('#attncards .attnmore'),
                                        grab('#attncards .attnsent')),
                            no3d: grab('#office_notice'),
                            banner: grab('#banner'),
                        };
                    }"""
                )
                # R122-P2: 会話（ホーム）・オフィス・設定の3タブ。
                assert len(chrome_texts["tabbar"]) == 3, (
                    "EN検査: tabbarが3ボタンではありません: " + repr(chrome_texts["tabbar"])
                )
                assert any("Conversations" in s for s in chrome_texts["tabbar"]) and any(
                    "Office" in s for s in chrome_texts["tabbar"]
                ) and any("Settings" in s for s in chrome_texts["tabbar"]), (
                    "EN検査: 会話／オフィス／設定タブが英訳されていません: " + repr(chrome_texts["tabbar"])
                )
                assert page.locator("#tb_run").count() == 0, "EN検査: 実行タブが残っています"
                page.locator("#tb_set").click()
                page.locator("#setwrap.open").wait_for(state="visible", timeout=5000)
                assert page.locator("#st_run_btn").count() == 1 and page.locator("#st_ln_btn").count() == 1, (
                    "EN検査: 設定の実行／プロジェクト起動がありません"
                )
                assert not jp_re.search(page.locator("#st_run_btn").inner_text()), "EN検査: 設定の実行が英語でない"
                page.locator("#st_close").click()
                assert len(chrome_texts["header"]) >= 2 and any("Allow" in s for s in chrome_texts["header"]), (
                    "EN検査: ヘッダーの状態の語のチップがありません／英語でない: " + repr(chrome_texts["header"])
                )
                assert chrome_texts["attncards"], "EN検査: ❗即答カードのボタンが空です"
                for area, values in chrome_texts.items():
                    bad = [v for v in values if jp_re.search(v)]
                    assert not bad, f"EN検査: {area} に日本語が残存: {bad!r}"
                page.locator("#tb_wait").click()
                page.wait_for_timeout(300)
                # R99-P4-7: 選択肢のある質問のシートには汎用の承認・停止・報告を出さない（❗ドックの R86-H と同じ規則）。
                # 固定 QUICK の英語表示は、選択肢の無いカードで見る。選択肢のあるカードでは固定 QUICK が 0 個であることを課す。
                cards = page.locator("#list .recentrow")
                picked, saw_opts = None, False
                for ci in range(cards.count()):
                    page.evaluate("(s) => openSheet(sessionAgent(s))", cards.nth(ci).get_attribute("data-sess"))
                    page.locator("#sheetwrap.open").wait_for(state="visible", timeout=5000)
                    has_opts = page.evaluate("() => !!document.querySelector('#quickbtns .quickoptions .qopt')")
                    if has_opts:
                        saw_opts = True
                        fixed_on_opts = page.evaluate(
                            "() => [...document.querySelectorAll('#quickbtns > button')]"
                            ".map(n => (n.textContent || '').trim()).filter(t => t && !t.startsWith('»'))"
                            ".filter(t => /Approve|Stop|progress report|Report/i.test(t))")
                        assert not fixed_on_opts, (
                            "EN検査: 選択肢のある質問のシートに汎用の承認・停止・報告が出ている: " + repr(fixed_on_opts))
                        page.evaluate("() => closeSheet()")
                        page.wait_for_timeout(150)
                        continue
                    picked = ci
                    break
                assert saw_opts, "EN検査: 選択肢のある質問のカードが無い（新しい規則のピンが空振り）"
                assert picked is not None, "EN検査: 選択肢の無いカードが無い（固定 QUICK の英語表示を見られない）"
                sheet_texts = page.evaluate(
                    """() => {
                        const grab = (sel) => [...document.querySelectorAll(sel)]
                            .map(n => (n.textContent || '').trim()).filter(Boolean);
                        const ft = document.getElementById('freetext');
                        return {
                            secs: grab('#sheetwrap .sec'),
                            quick: grab('#quickbtns > button:not(#sh_dlg)'),
                            entries: grab('#sheetwrap #sh_term,#sheetwrap #sh_dlg'),
                            mac: grab('#apvslot_sheet > #apvwrap.open #apvwhy, #apvslot_sheet > #apvwrap.open #apvmeta,'
                                      + ' #apvslot_sheet > #apvwrap.open #apvdeny, #apvslot_sheet > #apvwrap.open #apvdenywrite'),
                            buttons: grab('#sh_send, #sh_close'),
                            placeholder: ft ? [ft.placeholder] : [],
                            kind: [sessKind(SEL)],
                        };
                    }"""
                )
                # R82: ✳定型チップはユーザー本文＝翻訳しないのが正。固定QUICKだけを数える
                # B1: 承認は質問（選択肢なし）のときだけ・「許可は Mac で」はボタンでなく文字の行
                # R116-S0（意図した反転）: 選択肢の無い最初のカード（P7 の許可待ち）には「停止」「報告」の定型を出さない（許可待ちへの定型は
                # 黙った拒否として届いた）＝固定 QUICK 0 本＋文字の行 1 つ・送信口は「Deny & send」
                fixed_quick = [s for s in sheet_texts["quick"] if not s.startswith("»")]
                assert sheet_texts["kind"] == ["perm"] and fixed_quick == [] and sheet_texts["buttons"][:1] == ["Deny & send"], (
                    "EN検査: 許可待ちのシートに定型が出ている／送信口が「Deny & send」でない: "
                    + repr((sheet_texts["kind"], sheet_texts["quick"], sheet_texts["buttons"]))
                )
                sheet_texts.pop("kind")
                # R116-S1: 許可待ちのシートには決裁カード（#apvslot_sheet）＝読み切れない形は「Mac で許可」の 1 行・［拒否］［拒否して指示…］
                assert len(sheet_texts["mac"]) == 4 and sheet_texts["mac"][1].startswith("Allow this one on the Mac"), (
                    "EN検査: 許可待ちのシートに決裁カード（Mac で許可の 1 行・拒否・拒否して指示）がありません: " + repr(sheet_texts["mac"])
                )
                assert len(sheet_texts["entries"]) <= 1 and all(
                    s in ("Open session", "Show conversation") for s in sheet_texts["entries"]), (
                    "R104 EN検査: 会話の入口が二重／英語のラベルが違う: " + repr(sheet_texts["entries"]))
                sheet_texts["quick"] = fixed_quick
                for area, values in sheet_texts.items():
                    bad = [v for v in values if jp_re.search(v)]
                    assert not bad, f"EN検査: シート{area} に日本語が残存: {bad!r}"
                # 固定 QUICK の英語表示は、許可待ちでも質問でもない人（P7 の作業中）のシートで見る（許可待ちには出さなくなった＝R116-S0）
                busy = page.evaluate("""() => { closeSheet(); const e = officeAgents(LAST_OFFICE).find(x => x.session === 'e2e-busy-0002');
                    if (!e) return null; openSheet(e);
                    return { kind: sessKind(e), send: document.getElementById('sh_send').textContent,
                             quick: [...document.querySelectorAll('#quickbtns > button:not(#sh_dlg):not(#sh_term)')]
                               .map(n => (n.textContent || '').trim()).filter(t => t && !t.startsWith('»')) }; }""")
                assert (busy and busy["kind"] == "working" and len(busy["quick"]) == 2 and not any(s.startswith("Approve") for s in busy["quick"])
                        and busy["send"] == "Send this instruction" and not any(jp_re.search(v) for v in busy["quick"] + [busy["send"]])), (
                    "EN検査: 作業中のシートの固定 QUICK（停止・報告の 2 本）／送信口が英語でない: " + repr(busy))
                page.evaluate("() => closeSheet()")
                assert not _unexpected(console_errors), (
                    "EN検査 console error: " + "; ".join(console_errors[:5])
                )
                assert not page_errors, (
                    "EN検査 page error: " + "; ".join(page_errors[:5])
                )
            finally:
                browser.close()
    except Exception as exc:
        reason = re.sub(r"\s+", " ", str(exc)).strip() or exc.__class__.__name__
        print(f"✗ PWAスモーク: {reason}")
        return 1

    print(f"✓ PWAスモーク: {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
