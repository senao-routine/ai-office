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
                assert page.locator("#room").is_visible(), "officeビュー(#room) が非表示"

                # R76: OpenClaw室は実データ駆動（旧＝常に「未接続（拡張準備中）」の飾り）。
                # /status に external 社員が居るなら人数ピルと実メンバーが出ること。
                band = page.locator(".openclaw")
                assert band.count() == 1, "OpenClaw帯が無い"
                pill = page.locator("#ocz_pill").inner_text()
                members = page.locator(".openclaw .ocmem").count()
                assert members >= 1, f"OpenClaw実メンバーが描画されていない (pill={pill!r})"
                assert "接続中" in pill or "connected" in pill, f"人数ピルが未接続のまま: {pill!r}"
                names = page.locator(".openclaw .ocmem .ocname").all_inner_texts()
                assert any(n.strip() for n in names), "メンバー名が空"

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

                employee_nodes = page.locator("#map .mchar")
                # R76: 外部(OpenClaw)社員はマップではなく OpenClaw室の帯に出る＝マップの
                # 期待値は「外部を除いた社員数」。ここを全社員数にすると帯の実装が入った
                # 瞬間に嘘の不一致で落ちる。
                seed_count = page.evaluate(
                    """() => {
                        const raw = localStorage.getItem('aioffice.lastOffice');
                        const office = raw ? JSON.parse(raw) : {};
                        const all = Array.isArray(office.employees) ? office.employees : [];
                        return all.filter((e) => e && !e.external).length;
                    }"""
                )
                assert seed_count >= 1, "seed社員が取得できません"
                assert employee_nodes.count() == seed_count, (
                    f"#map .mchar 数({employee_nodes.count()}) != seed社員数({seed_count})"
                )
                # v5: walkbar廃止＝歩行フレームはマップのmcharで検証（walking時に_walk系ソースへ切替）
                walk_probe = page.evaluate(
                    """() => {
                        const rec = [...MCHARS.values()][0];
                        if(!rec) return {ok:false};
                        rec.walking = true;
                        const src = mcharSource(rec);
                        rec.walking = false;
                        return {ok:true, walkSrc:src.includes('_walk'),
                                idleSrc:mcharSource(rec).includes('_walk')};
                    }"""
                )
                assert walk_probe.get("ok"), "mchar歩行プローブ失敗"
                assert walk_probe["walkSrc"] and not walk_probe["idleSrc"], (
                    "mchar歩行フレーム切替が不正: " + repr(walk_probe)
                )
                assert page.evaluate("() => !document.getElementById('walkbar')"), (
                    "廃止済みのwalkbarがDOMに残っています"
                )

                labels = page.locator("#map .zonepill").all_inner_texts()
                assert any("会議" in label for label in labels), (
                    "会議コーナーpillがない: " + repr(labels)
                )

                # R23.5: ミニマップ吹き出し会話。6秒ローテを待たず決定論部を直接検査する
                # （msayLine=redaction後素材のみ・.msay要素実在・on付与で実描画・タップ透過）。
                bubble_probe = page.evaluate(
                    """() => {
                        const rec = [...MCHARS.values()][0];
                        if(!rec || !rec.say) return {ok:false, reason:'no rec/say'};
                        const flavorLine = msayLine({state:'waiting', session:'probe', feed:[]}, 0);
                        const logLine = msayLine({state:'working', age:10, session:'probe',
                                                  feed:['編集中 run.py']}, 2);   // (2+wHash('probe'))%3!==0=ログ行分岐
                        rec.say.textContent = flavorLine;
                        rec.say.classList.add('on');
                        const rect = rec.say.getBoundingClientRect();
                        const style = getComputedStyle(rec.say);
                        const result = {
                            ok: true,
                            flavorLine, logLine,
                            visible: rect.width > 0 && rect.height > 0,
                            passthrough: style.pointerEvents === 'none',
                        };
                        rec.say.classList.remove('on');
                        rec.say.textContent = '';
                        return result;
                    }"""
                )
                assert bubble_probe.get("ok"), "吹き出しプローブ失敗: " + repr(bubble_probe)
                assert bubble_probe["flavorLine"], "flavor台詞が空: " + repr(bubble_probe)
                assert bubble_probe["logLine"] == "編集中 run.py", (
                    "動作ログ行が吹き出しに出ていません: " + repr(bubble_probe)
                )
                assert bubble_probe["visible"], "吹き出し.onが実描画されません: " + repr(bubble_probe)
                assert bubble_probe["passthrough"], "吹き出しがタップを遮っています: " + repr(bubble_probe)

                scene_probe = page.evaluate(
                    """() => {
                        const room = document.querySelector('#room');
                        const map = document.querySelector('#map');
                        const mapStyle = map ? getComputedStyle(map) : null;
                        const styleText = [...document.querySelectorAll('style')]
                            .map(node => node.textContent || '').join('\\n');
                        return {
                            map: !!map,
                            mapScale: !!map && !!mapStyle && mapStyle.transform !== 'none' &&
                                map.getBoundingClientRect().width > 0,
                            mapLogicalWidth: !!map && map.offsetWidth === 374,
                            mchars: document.querySelectorAll('#map .mchar').length,
                            desks: document.querySelectorAll('#map .mdesk').length,
                            oldFurniture: document.querySelectorAll(
                                'img[src*="deskset"], img[src*="meetset"], img[src*="sofaset"]'
                            ).length,
                            oldWall: styleText.includes('wallstrip'),
                            oldElements: document.querySelectorAll(
                                '#room .officegrid, #room .meetroom, #room .loungestage'
                            ).length,
                            openclaw: !!room && [...room.querySelectorAll('.zonepill')]
                                .some(node => (node.textContent || '').includes('OpenClaw')),
                            openclawCrt: room ? room.querySelectorAll('.openclaw .crt').length : 0,
                            openclawServer: room ? room.querySelectorAll('.openclaw .server').length : 0,
                            openclawBots: room ? room.querySelectorAll('.openclaw .ocbot').length : 0,
                            walkfObject: typeof WALKF === 'object' && WALKF !== null && !Array.isArray(WALKF),
                            walkbarGone: !document.getElementById('walkbar'),
                            lastOffice: !!localStorage.getItem('aioffice.lastOffice'),
                        };
                    }"""
                )
                assert scene_probe["map"], "#map がありません"
                assert scene_probe["mapScale"], "#map にscale transformが適用されていません"
                assert scene_probe["mapLogicalWidth"], "#map の論理幅が374pxではありません"
                assert scene_probe["mchars"] == seed_count, (
                    "#map .mchar 数がseed社員数と一致しません: " + repr(scene_probe)
                )
                assert scene_probe["desks"] == 6, (
                    "マップ机(.mdesk)が6個ではありません: " + repr(scene_probe)
                )
                assert scene_probe["oldFurniture"] == 0, (
                    "旧家具スプライト参照が残っています: " + repr(scene_probe)
                )
                assert not scene_probe["oldWall"], "CSSに旧 wallstrip 参照が残っています"
                assert scene_probe["oldElements"] == 0, (
                    "旧縦積みゾーン要素が残っています: " + repr(scene_probe)
                )
                assert scene_probe["openclaw"] and scene_probe["openclawCrt"] == 3 and scene_probe["openclawServer"] == 1, (
                    "OpenClaw装飾バンドが不正です: " + repr(scene_probe)
                )
                assert scene_probe["openclawBots"] == 2, (
                    "OpenClawロボットが2体ではありません: " + repr(scene_probe)
                )
                assert scene_probe["walkfObject"] and scene_probe["walkbarGone"], (
                    "歩行フレームまたはWALKFが不正です: " + repr(scene_probe)
                )
                assert scene_probe["lastOffice"], "localStorage aioffice.lastOffice が未保存"

                attn_seed = page.evaluate(
                    """() => {
                        const raw = localStorage.getItem('aioffice.lastOffice');
                        const office = raw ? JSON.parse(raw) : {};
                        return (office.employees || []).filter(employee =>
                            !!employee.question || Number(employee.approvalMin || 0) > 0
                        );
                    }"""
                )
                assert len(attn_seed) == 1 and attn_seed[0].get("question"), (
                    "質問付きseed社員が1名ではありません: " + repr(attn_seed)
                )
                attn_card = page.locator("#attncards .attncard")
                assert attn_card.count() == 1, "#attncards の質問カードが1枚ではありません"
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
                option_buttons.first.click()
                assert attn_card.locator("button").count() >= 6, (
                    "質問カードの選択肢+即答ボタンが不足しています: "
                    + str(attn_card.locator("button").count())
                )
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
                assert not _unexpected(console_errors), (
                    "console error: " + "; ".join(console_errors[:5])
                )
                assert not page_errors, "page error: " + "; ".join(page_errors[:5])

                employee_nodes.first.click()
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
                            statbar: grab('#statbar .stat').concat(grab('#total')),
                            attncards: grab('#attncards .attnactions button')
                                .concat(grab('#attncards .attngo'),
                                        grab('#attncards .attnmore'),
                                        grab('#attncards .attnsent')),
                            zonepills: grab('#mainzone .zonepill')
                                .concat(grab('#loungezone .zonepill'),
                                        grab('#meetingzone .zonepill'),
                                        grab('#room .openclaw .zonepill'),
                                        grab('#room .ocpill')),
                            banner: grab('#banner'),
                        };
                    }"""
                )
                assert len(chrome_texts["tabbar"]) == 4, (
                    "EN検査: tabbarが4ボタンではありません: " + repr(chrome_texts["tabbar"])
                )
                assert chrome_texts["attncards"], "EN検査: ❗即答カードのボタンが空です"
                for area, values in chrome_texts.items():
                    bad = [v for v in values if jp_re.search(v)]
                    assert not bad, f"EN検査: {area} に日本語が残存: {bad!r}"
                page.locator("#map .mchar").first.click()
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
                assert len(sheet_texts["quick"]) == 3, (
                    "EN検査: QUICKボタンが3個ではありません: " + repr(sheet_texts["quick"])
                )
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
