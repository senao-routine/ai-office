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
                # R77: PWAの既定は3Dオフィス。このスモークは**3Dが使えない端末**
                # （WebGL不可・モジュール取得失敗）で2Dマップへ退避する経路を守る担当。
                # /ui/** を落とすと boot3d が読めず renderScene が2D側へフォールバックする。
                page.route("**/ui/**", lambda route: route.abort())
                page.goto(base_url.rstrip("/") + "/app", wait_until="domcontentloaded")
                page.wait_for_timeout(4000)

                assert not _unexpected(console_errors), (
                    "console error: " + "; ".join(console_errors[:5])
                )
                assert not page_errors, "page error: " + "; ".join(page_errors[:5])
                assert page.locator(".tabbar").is_visible(), ".tabbar が非表示"
                # R79: 3D不可端末はリストへ自動退避済み＝初期表示は #list が正
                assert page.locator("#list").is_visible(), "退避先のリスト(#list)が非表示"


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
                page.locator("#setwrap .sheet button.sub").click()

                # R79: /ui/** 遮断＝3D不可端末。4秒タイムアウト後にリストへ自動退避し、
                # 2DのDOM（#map/#mapframe/.openclaw）は一切作られないことが新しい正。
                page.wait_for_function(
                    "() => typeof SCENE3D !== 'undefined' && SCENE3D === 'failed'",
                    timeout=15000)
                fb = page.evaluate(
                    """() => ({
                        view: VIEW,
                        twoD: document.querySelectorAll('#map,#mapframe,.openclaw').length,
                        cards: document.querySelectorAll('#list .card:not(.daysum)').length,
                        lastOffice: !!localStorage.getItem('aioffice.lastOffice'),
                    })""")
                assert fb["view"] == "list", f"3D不可でリストへ自動退避していない: {fb}"
                assert fb["twoD"] == 0, f"2DのDOMが残っている: {fb}"
                seed_count = page.evaluate(
                    """() => {
                        const raw = localStorage.getItem('aioffice.lastOffice');
                        const office = raw ? JSON.parse(raw) : {};
                        return (office.employees || []).filter((e) => e).length;
                    }""")
                assert fb["cards"] == seed_count, (
                    f"リストのカード数({fb['cards']}) != 社員数({seed_count})")
                assert fb["lastOffice"], "aioffice.lastOffice が未保存"
                # R79: 2D撤去後は OpenClaw 帯そのものが無い。外部(OpenClaw)社員は
                # リストに出る（「3Dに居るものは必ず一覧にも居る」の退避側の相方）。
                # 退避で既にリスト表示なのでタブ操作は不要（viewを動かさない＝後続検査の前提を守る）
                list_texts = page.locator("#list .card:not(.daysum)").all_inner_texts()
                assert any("OpenClaw" in s for s in list_texts), (
                    "外部(OpenClaw)社員がリストに出ていません: " + repr(list_texts[:5])
                )
                # officeタブへ戻ると3D不可の告知が出る（白画面にしない）
                page.locator("#tb_office").click()
                page.wait_for_timeout(300)
                assert page.locator("#no3d").is_visible(), "3D不可の告知(#no3d)が出ていない"
                page.locator("#tb_list").click()
                page.wait_for_timeout(200)

                # ❗カードは officeタブ（#room）内＝退避でリストに居るので戻してから検査。
                # failed分岐でも updateAttnCards が塗る（officeへ戻ったユーザーにも❗を見せる）ことの検査でもある
                page.locator("#tb_office").click()
                page.wait_for_timeout(400)
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
                attn_card = page.locator("#attncards .attncard").first
                option_buttons = attn_card.locator(".attnoptions .attnoption")
                assert option_buttons.count() == 3, (
                    "質問カードの選択肢ボタンが3個ではありません: "
                    + str(option_buttons.count())
                )
                assert option_buttons.first.inner_text().startswith("⭐ 案A"), (
                    "Recommended表示が不正: " + repr(option_buttons.first.inner_text())
                )
                assert "(Recommended)" not in option_buttons.first.inner_text(), (
                    "Recommended原文が表示に残っています"
                )
                # R86-H: 選択肢があるときは汎用の承認/停止/報告を出さない（PCと同じ規則）
                # ＝選択肢3件＋「✍️ 自由に」の4個ちょうど。
                assert attn_card.locator("button").count() == 4, (
                    "選択肢と汎用ボタンが二重に出ている/足りない: "
                    + str(attn_card.locator("button").count())
                )
                option_buttons.first.click()
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
                    "document.querySelector('#attncards .attnsent').textContent.includes('送信済み')",
                    timeout=5000,
                )
                # R86-H: 許可要求のカードは断定の「承認が必要です」を出さない
                # （スマホからは実行の許可を渡せない＝言えるのは事実だけ）
                perm_txt = page.evaluate(
                    "() => [...document.querySelectorAll('#attncards .attncard')]"
                    "  .map(n => n.textContent || '').join(' | ')")
                assert "承認が必要です" not in perm_txt, (
                    "断定の『承認が必要です』が残っています: " + perm_txt[:160])
                assert not _unexpected(console_errors), (
                    "console error: " + "; ".join(console_errors[:5])
                )
                assert not page_errors, "page error: " + "; ".join(page_errors[:5])

                # シートのタイプライター検査＝リストのカードから開く（2Dキャラは撤去済み）
                page.locator("#tb_list").click()
                page.wait_for_timeout(300)
                page.locator("#list .card:not(.daysum)").first.click()
                page.locator("#sheetwrap.open").wait_for(state="visible", timeout=5000)
                for _ in range(50):
                    if page.locator("#shsay").inner_text().strip():
                        break
                    page.wait_for_timeout(100)
                assert page.locator("#shsay").inner_text().strip(), "#shsay が空"
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
                jp_re = re.compile(r"[ぁ-んァ-ヶ一-龥]")
                chrome_texts = page.evaluate(
                    """() => {
                        const grab = (sel) => [...document.querySelectorAll(sel)]
                            .map(n => (n.textContent || '').trim()).filter(Boolean);
                        return {
                            tabbar: grab('#tabbar button'),
                            header: grab('#hstats .hstat'),
                            attncards: grab('#attncards .attnactions button')
                                .concat(grab('#attncards .attngo'),
                                        grab('#attncards .attnmore'),
                                        grab('#attncards .attnsent')),
                            no3d: grab('#no3d'),
                            banner: grab('#banner'),
                        };
                    }"""
                )
                # R82: タブは4つ（オフィス/リスト/▶実行/設定）。全ラベルがENに翻訳されること
                assert len(chrome_texts["tabbar"]) == 4, (
                    "EN検査: tabbarが4ボタンではありません: " + repr(chrome_texts["tabbar"])
                )
                assert any("Run" in s for s in chrome_texts["tabbar"]), (
                    "EN検査: ▶実行タブが英訳されていません: " + repr(chrome_texts["tabbar"])
                )
                assert len(chrome_texts["header"]) >= 2, (
                    "EN検査: ヘッダー統計チップがありません: " + repr(chrome_texts["header"])
                )
                assert chrome_texts["attncards"], "EN検査: ❗即答カードのボタンが空です"
                for area, values in chrome_texts.items():
                    bad = [v for v in values if jp_re.search(v)]
                    assert not bad, f"EN検査: {area} に日本語が残存: {bad!r}"
                page.locator("#tb_list").click()
                page.wait_for_timeout(300)
                page.locator("#list .card:not(.daysum)").first.click()
                page.locator("#sheetwrap.open").wait_for(state="visible", timeout=5000)
                sheet_texts = page.evaluate(
                    """() => {
                        const grab = (sel) => [...document.querySelectorAll(sel)]
                            .map(n => (n.textContent || '').trim()).filter(Boolean);
                        const ft = document.getElementById('freetext');
                        return {
                            secs: grab('#sheetwrap .sec'),
                            quick: grab('#quickbtns > button'),
                            buttons: grab('#sh_send, #sh_close'),
                            placeholder: ft ? [ft.placeholder] : [],
                        };
                    }"""
                )
                # R82: ✳定型チップはユーザー本文＝翻訳しないのが正。固定QUICKだけ3本を数える
                fixed_quick = [s for s in sheet_texts["quick"] if not s.startswith("✳")]
                assert len(fixed_quick) == 3, (
                    "EN検査: 固定QUICKが3個ではありません: " + repr(sheet_texts["quick"])
                )
                sheet_texts["quick"] = fixed_quick
                for area, values in sheet_texts.items():
                    bad = [v for v in values if jp_re.search(v)]
                    assert not bad, f"EN検査: シート{area} に日本語が残存: {bad!r}"
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
