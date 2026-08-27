#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""R77: スマホPWAの3Dオフィス スモーク（Playwright必須）。

既定経路＝デスクトップと同じ IsoScene が Worker 配信のESMで動くこと。
「canvasが出た」ではなく **ロボットが実際に立ち、タップでシートが開く** まで見る
（絵だけ出て操作が死んでいる嘘greenを防ぐ）。2Dへ退避する経路は pwa_smoke.py が担当。

使い方: python pwa3d_smoke.py <base_url> <device_id> <secret> <token> <out.png>
"""
import json
import sys
from pathlib import Path

try:
    from playwright.sync_api import TimeoutError as PWTimeout
    from playwright.sync_api import sync_playwright
except ImportError as exc:  # pragma: no cover
    print(f"✗ 3Dスモーク: Playwright import失敗: {exc}")
    sys.exit(1)


def main(argv):
    if len(argv) != 6:
        print("使い方: pwa3d_smoke.py <base_url> <device_id> <secret> <token> <out.png>")
        return 2
    base_url, device_id, secret, token, out = argv[1:]
    cred = json.dumps({"d": device_id, "s": secret, "t": token, "e": 0},
                      ensure_ascii=False, separators=(",", ":"))

    with sync_playwright() as p:
        # SwiftShader はヘッドレスの3D初期化で稀にブラウザごと死ぬ（ui_shot の3回リトライと
        # 同じ既知事象・R79-5で実測）。canvas が一度も出ない場合だけ1回再試行する＝
        # アサーション失敗（実回帰）はリトライで隠さない。
        for attempt in (1, 2):
            errors = []
            ng = 0
            browser = p.chromium.launch()
            try:
                page = browser.new_page(viewport={"width": 390, "height": 844},
                                        has_touch=True)
                page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
                page.on("pageerror", lambda e: errors.append(str(e)))
                page.add_init_script(
                    "localStorage.setItem('aioffice.cred', " + json.dumps(cred) + ");")
                page.goto(base_url.rstrip("/") + "/app", wait_until="domcontentloaded")

                try:
                    # 60秒: 高負荷時はSwiftShaderのシェーダコンパイルが30秒を超える
                    # （4秒watchdog→リスト退避→ready到着で自動復帰、までを待つ）
                    page.wait_for_selector("#scene3d canvas", timeout=60000)
                except PWTimeout:
                    if attempt == 1:
                        print("  - 3D canvas未出現 → SwiftShader稀死とみなし1回だけ再試行")
                        continue
                    print("  ✗ 3D canvas が2回連続で出ない（WebGL初期化失敗。マシン過負荷でも"
                          "起きる＝uptime のload averageを確認。実測: load 200超で4秒watchdogが"
                          "リスト退避し canvas が hidden のまま＝コード回帰と誤診しやすい）")
                    return 1
                print("  ✓ 3D canvas が描画された（Worker配信のESM＋WebGL）")

                # R86-E: 「送っても即座には届かない」相手を**既定タブで**見分けられること。
                # 📴 をリストとシートにだけ入れていたため、既定の🏢オフィスタブを見ている
                # ユーザーには構造的に見えなかった（ユーザー報告「そんなボタンない」で発覚）。
                # 併せて **working には出さない**（心拍は待機ループ中しか打たないので working は
                # 必ず listening:false になる＝そこに出すと忙しい社員全員に誤警告）。
                page.wait_for_selector("#attncards .attncard", timeout=20000)
                page.wait_for_timeout(600)
                mute = page.evaluate(
                    """() => {
                      const vis = (n) => { const r = n.getBoundingClientRect();
                        return r.width > 0 && r.height > 0; };
                      const chips = [...document.querySelectorAll('.dchip.mute')].filter(vis);
                      const attn = document.getElementById('attncards');
                      const roster = document.getElementById('roster');
                      const inAttn = !!(attn && [...attn.querySelectorAll('.dchip.mute')].some(vis));
                      const inRoster = !!(roster && /📴/.test(roster.textContent || ''));
                      const busy = [...(roster ? roster.querySelectorAll('.rchip') : [])]
                        .filter(n => /E2E稼働部/.test(n.textContent || ''));
                      return { chips: chips.length, inAttn, inRoster,
                        busyMuted: busy.some(n => /📴/.test(n.textContent || '')),
                        busySeen: busy.length }; }""")
                if mute["inAttn"] and mute["inRoster"]:
                    print(f"  ✓ R86-E 既定タブで受信待機なしが分かる（❗ドック＋ロスター帯・"
                          f"チップ{mute['chips']}件）")
                else:
                    print(f"  ✗ 既定タブに📴が出ない（リスト/シートだけでは見えない）: {mute}")
                    ng += 1
                if mute["busySeen"] and not mute["busyMuted"]:
                    print("  ✓ R86-E 稼働中(working)には📴を出さない（誤警告なし）")
                elif not mute["busySeen"]:
                    print(f"  ✗ 稼働中セッションがロスター帯に出ていない: {mute}")
                    ng += 1
                else:
                    print(f"  ✗ 稼働中に誤って📴を出している: {mute}")
                    ng += 1

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

                # 3D時は旧2Dマップを出さない（二重描画＝どちらが正か分からなくなる）
                if page.locator("#mapframe").count() and page.locator("#mapframe").is_visible():
                    print("  ✗ 3D時に旧2Dマップが残っている")
                    ng += 1
                else:
                    print("  ✓ 旧2Dマップは非表示（3Dが唯一の絵）")

                # R79-0: 3D経路の回帰ピン。ここが崩れるとスマホの主目的（❗即応）が静かに死ぬ。
                two_d = page.evaluate(
                    "() => document.querySelectorAll('#map,#mapframe,.openclaw').length")
                if two_d:
                    print(f"  ✗ 3D経路なのに2DのDOMが作られている: {two_d}件")
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

                # R79-6→R80.5: 6体以下は**全員テキスト名札**（識別最優先・実FB2回目）。
                # 7体以上は二段marker（全員=モノグラムピン＋テキスト名札は選択/❗先頭の最大2枚）。
                # どちらでも pins+plates=ロボ全数・名札高さ≤20px。
                page.wait_for_timeout(700)   # paintPlatesは描画ループ内＝1フレーム以上待つ
                marks = page.evaluate(
                    """() => ({
                        pins: [...document.querySelectorAll('#plates .pin')]
                            .map(n => n.textContent),
                        plates: [...document.querySelectorAll('#plates .plate')]
                            .map(n => Math.round(n.getBoundingClientRect().height)),
                        robots: window.__scene3d.stats().robots,
                        rosterH: (() => { const c = document.querySelector('#roster .rchip');
                            return c ? Math.round(c.getBoundingClientRect().height) : 0; })(),
                        attnOn: !!document.querySelector('#attncards.on'),
                    })""")
                few = marks["robots"] <= 6
                if len(marks["pins"]) + len(marks["plates"]) != marks["robots"]:
                    print(f"  ✗ 全ロボットにマーカーが付いていない: {marks}")
                    ng += 1
                elif not all(len(t) == 1 for t in marks["pins"]):
                    print(f"  ✗ ピンが1文字モノグラムでない: {marks}")
                    ng += 1
                elif few and len(marks["plates"]) != marks["robots"]:
                    print(f"  ✗ 6体以下なのに全員に名札が付いていない: {marks}")
                    ng += 1
                elif (not few) and len(marks["plates"]) > 2:
                    print(f"  ✗ 7体以上でテキスト名札が2枚超: {marks}")
                    ng += 1
                elif any(h > 20 for h in marks["plates"]):
                    print(f"  ✗ テキスト名札の高さが20px超: {marks}")
                    ng += 1
                else:
                    print(f"  ✓ 名札（ピン{len(marks['pins'])}＋名札{len(marks['plates'])}"
                          f"＝ロボ{marks['robots']}体・{'全員名札' if few else '二段'}・≤20px）")
                # R80.7: ロスターは上部帯へ（ユーザーFB「スライドできる項目は上に」）。
                # ❗カードは下（親指圏）のまま＝両方が同時に見える。ゲージ帯は下ドック。
                layout = page.evaluate(
                    """() => ({
                        rosterTop: (() => { const c = document.querySelector('#topdock #roster .rchip');
                            return c ? Math.round(c.getBoundingClientRect().top) : -1; })(),
                        gaugebar: !!document.getElementById('gaugebar'),
                        attnBottom: (() => { const c = document.querySelector('#attncards.on');
                            return c ? Math.round(c.getBoundingClientRect().bottom) : -1; })(),
                    })""")
                ih = 844
                if layout["rosterTop"] < 40 or layout["rosterTop"] > 260:   # <40=ヘッダー裏に潜った
                    print(f"  ✗ ロスターが上部帯に居ない: {layout}")
                    ng += 1
                elif not layout["gaugebar"]:
                    print("  ✗ 下部ゲージ帯（#gaugebar）が無い")
                    ng += 1
                elif marks["attnOn"] and not (layout["attnBottom"] > ih / 2):
                    print(f"  ✗ ❗カードが下部に居ない: {layout}")
                    ng += 1
                else:
                    print(f"  ✓ レイアウト（ロスター上 top={layout['rosterTop']}・"
                          f"❗下 bottom={layout['attnBottom']}・ゲージ帯あり）")

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

                # R81-4: 殺風景対策の常設ピン＝ライブ活動ティッカー（誰が今なにを）と
                # ゲージタップ→⚡リソースシート（ゲージの意味が読める場所。🧾表示はR85-2撤去）
                live = page.evaluate(
                    """() => ({
                        ticker: (document.getElementById('ticker')||{}).textContent || '',
                        gauges: !!document.getElementById('gaugebar'),
                    })""")
                if not live["ticker"].strip():
                    print(f"  ✗ 活動ティッカーが空: {live}")
                    ng += 1
                else:
                    print(f"  ✓ 活動ティッカー（{live['ticker'][:28]}…）")
                page.evaluate("openRes()")
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
                page.evaluate("closeRes()")
                page.wait_for_timeout(300)

                # R79-5: 骨格ピン＝3Dはフルブリード背景・officeタブは一切スクロールしない・
                # タブ3つ・❗/ロスターは下部ドック（親指圏）・statbar/deptbar常設2段は廃止
                skel = page.evaluate(
                    """() => {
                        const c = document.querySelector('#scene3d');
                        const d = document.getElementById('dock');
                        const r = c ? c.getBoundingClientRect() : null;
                        const dr = d ? d.getBoundingClientRect() : null;
                        return {
                            cw: r ? Math.round(r.width) : 0,
                            ch: r ? Math.round(r.height) : 0,
                            iw: innerWidth, ih: innerHeight,
                            scrollable: document.scrollingElement.scrollHeight > innerHeight + 1,
                            tabs: document.querySelectorAll('#tabbar button').length,
                            dockBottom: dr ? Math.round(dr.bottom) : -1,
                            statbar: !!document.getElementById('statbar'),
                            hstats: document.querySelectorAll('#hstats .hstat').length,
                        };
                    }""")
                if not (skel["cw"] >= skel["iw"] and skel["ch"] >= int(skel["ih"] * 0.9)):
                    print(f"  ✗ 3Dがフルブリードでない: {skel}")
                    ng += 1
                else:
                    print(f"  ✓ 3Dフルブリード（{skel['cw']}x{skel['ch']} / 画面 {skel['iw']}x{skel['ih']}）")
                if skel["scrollable"]:
                    print(f"  ✗ officeタブがスクロール可能: {skel}")
                    ng += 1
                else:
                    print("  ✓ officeタブは一切スクロールしない")
                # R82: タブは4つ（▶実行を昇格）。ヘッダーはオフィス名入り
                if skel["tabs"] != 4 or skel["statbar"] or skel["hstats"] < 2:
                    print(f"  ✗ ヘッダー統合/タブ4つの骨格が不正: {skel}")
                    ng += 1
                else:
                    print(f"  ✓ タブ4つ＋ヘッダー統計{skel['hstats']}チップ")
                r82 = page.evaluate(
                    """() => ({
                        ttl: (document.querySelector('.hdr2 .ttl')||{}).textContent || '',
                        runTab: !!document.getElementById('tb_run'),
                        gauges: (document.getElementById('gaugebar')||{}).textContent || '',
                    })""")
                if "E2Eオフィス" not in r82["ttl"]:
                    print(f"  ✗ ヘッダーにオフィス名が出ない: {r82['ttl']}")
                    ng += 1
                elif not r82["runTab"]:
                    print("  ✗ ▶実行タブが無い")
                    ng += 1
                elif "Claude" not in r82["gauges"]:
                    print(f"  ✗ ゲージ帯にピン留めプロバイダ(Claude)が出ない: {r82['gauges'][:40]}")
                    ng += 1
                else:
                    print(f"  ✓ R82骨格（オフィス名ヘッダー・▶実行タブ・ゲージ帯にClaude）")
                # プロバイダ切替: リソースシートのチップでCodexへピン→ゲージ帯が追随
                page.evaluate("openRes()")
                page.wait_for_timeout(400)
                switched = page.evaluate(
                    """() => {
                        const chips = [...document.querySelectorAll('#rs_body .rs-chip')];
                        const codex = chips.find(c => c.textContent === 'Codex');
                        if (!codex) return {ok: false, chips: chips.map(c => c.textContent)};
                        codex.click();
                        const gb = (document.getElementById('gaugebar')||{}).textContent || '';
                        const pin = localStorage.getItem('aioffice.gaugePin');
                        closeRes();
                        return {ok: gb.indexOf('Codex') >= 0 && pin === 'codex', gb: gb.slice(0, 40), pin};
                    }""")
                if not switched["ok"]:
                    print(f"  ✗ プロバイダ切替が効かない: {switched}")
                    ng += 1
                else:
                    print("  ✓ ゲージのプロバイダ切替（チップ→バー反映＋永続）")
                # S1: ゲージのプロバイダlabelにHTMLを入れても実行されない（innerHTML→textContent化）
                xss = page.evaluate(
                    """() => {
                        window.__xss = 0;
                        const o = JSON.parse(JSON.stringify(LAST_OFFICE || {}));
                        o.res = o.res || {};
                        o.res.providers = [{id:'claude', label:'<img src=x onerror=\"window.__xss=1\">',
                            bars:[{k:'<b>5h</b>', pct:20}]}];
                        localStorage.setItem('aioffice.gaugePin','claude');
                        LAST_OFFICE = o; paintGauges();
                        const gb = document.getElementById('gaugebar');
                        return {fired: window.__xss, imgs: gb.querySelectorAll('img').length,
                                litem: gb.textContent.indexOf('<img') >= 0};
                    }""")
                if xss["fired"] or xss["imgs"] > 0 or not xss["litem"]:
                    print(f"  ✗ ゲージにHTMLインジェクション（XSS掟の破れ）: {xss}")
                    ng += 1
                else:
                    print("  ✓ ゲージlabelはtextContent化（<img>は文字列・onerror不発）")
                page.evaluate("localStorage.setItem('aioffice.gaugePin','claude')")
                page.evaluate("localStorage.setItem('aioffice.gaugePin','claude');paintGauges()")
                # 今日のまとめ: リストタブの最上部カード
                daysum = page.evaluate(
                    """() => { setView('list');
                        const c = document.querySelector('#list .daysum');
                        const txt = c ? c.textContent : '';
                        setView('office');
                        return txt; }""")
                if "タスク" not in daysum or "3" not in daysum:
                    print(f"  ✗ 今日のまとめカードが無い/タスク実数不一致: {daysum[:50]}")
                    ng += 1
                else:
                    print("  ✓ 今日のまとめカード（タスク実数入り）")
                if not (0 <= skel["dockBottom"] <= skel["ih"] - 56):
                    print(f"  ✗ 下部ドックがタブバー直上に無い: {skel}")
                    ng += 1
                else:
                    print(f"  ✓ 下部ドック（bottom={skel['dockBottom']} / 画面 {skel['ih']}）")

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

                # ロボットをタップ → 詳細シートが開く（操作の本流が生きている証明）。
                # R80.6: タップ点が下部ドック（❗カード/ロスター）に覆われているロボを選ぶと
                # カード側の click が走って2段タップを検証できない＝elementFromPoint で
                # **シーン領域（canvas/名札）に当たる**候補だけを使う（名札タップも同じ2段フロー）。
                point = page.evaluate(
                    """() => {
                        const host = document.getElementById('scene3d');
                        const r = host.getBoundingClientRect();
                        const ags = window.__scene3d ? window.__scene3d.agents() : [];
                        for (const a of ags) {
                            const p = window.__scene3d.project(a.id);
                            if (!p) continue;
                            const x = r.left + p.left, y = r.top + p.top + 30;
                            const hit = document.elementFromPoint(x, y);
                            const wrap = document.getElementById('scene3dwrap');
                            if (hit && wrap && wrap.contains(hit)) {
                                return {id: a.id, left: p.left, top: p.top};
                            }
                        }
                        return null;
                    }""")
                if not point:
                    print("  ✗ ドックに覆われていないロボットが1体も無い（タップ検証不能）")
                    ng += 1
                else:
                    # R80.6: タップは2段（1度目=フォーカス+「誰が・何を」/ 2度目=シート）。
                    # いきなりシートが開いたら「まず注目させる」段が消えた回帰。
                    box = page.locator("#scene3d").bounding_box()
                    px = box["x"] + point["left"]
                    py = box["y"] + point["top"] + 30
                    page.mouse.click(px, py)
                    page.wait_for_timeout(500)
                    first = page.evaluate(
                        "() => ({open: document.getElementById('sheetwrap')"
                        ".classList.contains('open'),"
                        " sel: !!document.querySelector('.rchip.sel'),"
                        " note: (document.getElementById('note')||{}).textContent||''})")
                    if first["open"]:
                        print("  ✗ 1度目のタップでいきなりシートが開いた（2段タップの回帰）")
                        ng += 1
                    elif not first["sel"]:
                        print(f"  ✗ 1度目のタップで選択されない: {first}")
                        ng += 1
                    else:
                        print(f"  ✓ 1度目のタップ=フォーカス＋選択（トースト: {first['note'][:24]}…）")
                    # 1度目のフォーカスでカメラが寄る＝ロボの画面位置が動く。
                    # 2度目は同じidの**新しい投影点**を取り直してからタップする
                    # （実ユーザーは目でロボを追うので同じ操作になる）。
                    page.wait_for_timeout(900)
                    p2 = page.evaluate(
                        """(id) => {
                            const host = document.getElementById('scene3d');
                            const r = host.getBoundingClientRect();
                            const p = window.__scene3d.project(id);
                            if (!p) return null;
                            return {x: r.left + p.left, y: r.top + p.top + 30};
                        }""", point["id"])
                    if not p2:
                        print("  ✗ フォーカス後のロボ位置が取れない")
                        ng += 1
                    else:
                        page.mouse.click(p2["x"], p2["y"])
                        page.wait_for_timeout(700)
                        opened = page.evaluate(
                            "() => { const n=document.getElementById('sheetwrap');"
                            " return !!n && n.classList.contains('open'); }")
                        if opened:
                            print("  ✓ 2度目のタップ → 詳細シートが開く")
                            tpl = page.evaluate(
                                "() => [...document.querySelectorAll('#quickbtns button')]"
                                ".map(b => b.textContent).filter(s => s.indexOf('✳') === 0)")
                            if not any("ビルド確認" in s for s in tpl):
                                print(f"  ✗ 定型文チップがシートに出ない: {tpl}")
                                ng += 1
                            else:
                                print("  ✓ 定型文チップ（Mac保存→スマホ同期）")
                        else:
                            print("  ✗ 2度目のタップでシートが開かない")
                            ng += 1

                    # R80.9: **実タッチ**の連続タップ（<320ms）。ダブルタップ=全景リセットが
                    # ロボ上でも発動すると2度目のclickが飲まれてシート動線が丸ごと死ぬ
                    # （実機で発覚＝マウスE2Eでは踏めない）。ロボ上ではリセットしないのが正。
                    page.evaluate(
                        """() => { closeSheet(); SEL = null;
                            window.__scene3d.view.reset(); window.__scene3d.focus(null);
                            // 失敗時診断: タップ経路のトレース（決定論バグと環境遅延の切り分け）
                            window.__tapdbg = [];
                            if (!window.__tapdbg_wired) {
                                window.__tapdbg_wired = true;
                                // hostTapはsceneShell3Dローカル＝外から包めない。documentのcapture
                                // リスナーで「clickの着弾座標とその瞬間のpick結果」を記録する
                                window.addEventListener("click", (ev) => {
                                    const host = document.getElementById("scene3d");
                                    if (!host) return;
                                    const r = host.getBoundingClientRect();
                                    const x = ev.clientX - r.left, y = ev.clientY - r.top;
                                    let pk = null;
                                    try { pk = window.__scene3d.pick(x, y); } catch (e) { pk = "err"; }
                                    window.__tapdbg.push(["click", Math.round(x), Math.round(y),
                                        pk, !!SEL, (ev.target.id || ev.target.tagName)]);
                                }, true);
                                const _ta = tapAgent;
                                tapAgent = function (id) {
                                    window.__tapdbg.push(["tapAgent", id, SEL && SEL.session]);
                                    return _ta(id);
                                };
                                const _os = openSheet;
                                openSheet = function (e) {
                                    window.__tapdbg.push(["openSheet", e && e.session]);
                                    return _os(e);
                                };
                                const _cs = closeSheet;
                                closeSheet = function () {
                                    window.__tapdbg.push(["closeSheet"]);
                                    return _cs();
                                };
                            } }""")
                    # タップ座標は**直前に毎回取り直す**（整定待ち方式はアニメ停止中の座標を
                    # 「安定」と誤認するレースがあった＝実測。取得→タップの間は~20ms＝
                    # カメラが動いていても pick半径54pxに対して十分小さい）
                    def fresh_tp():
                        return page.evaluate(
                            """(id) => {
                                const host = document.getElementById('scene3d');
                                const r = host.getBoundingClientRect();
                                const p = window.__scene3d.project(id);
                                return p ? {x: r.left + p.left, y: r.top + p.top + 30} : null;
                            }""", point["id"])
                    page.wait_for_timeout(800)
                    tp = fresh_tp()
                    if not tp:
                        print("  ✗ タッチ検証用のロボ位置が取れない")
                        ng += 1
                    else:
                        # 高負荷（load25超・別プロセスが多コア占有）ではclick合成自体が
                        # 救済窓4秒を超えて届くことがある＝環境遅延。コード退行（リセット化け/
                        # 空振り化け）は決定論なので、**一連の再試行1回**では隠れない。
                        opened2 = False
                        touch = None
                        for tap_try in (1, 2):
                            t1 = fresh_tp() or tp
                            page.touchscreen.tap(t1["x"], t1["y"])
                            page.wait_for_timeout(150)
                            t2 = fresh_tp() or t1
                            page.touchscreen.tap(t2["x"], t2["y"])
                            try:
                                page.wait_for_function(
                                    "() => document.getElementById('sheetwrap')"
                                    ".classList.contains('open')", timeout=4000)
                                opened2 = True
                            except PWTimeout:
                                opened2 = False
                            touch = page.evaluate(
                                "() => ({open: document.getElementById('sheetwrap')"
                                ".classList.contains('open'),"
                                " sel: !!SEL, note: (document.getElementById('note')||{})"
                                ".textContent || '',"
                                " scale: window.__scene3d.view.state().scale})")
                            if opened2:
                                break
                            trace = page.evaluate("() => window.__tapdbg || []")
                            print(f"  - 連続タップ試行{tap_try}が届かない: {touch} trace={trace}")
                            page.evaluate(
                                "() => { closeSheet(); SEL = null;"
                                " window.__scene3d.view.reset();"
                                " window.__scene3d.focus(null); }")
                            page.wait_for_timeout(1200)
                        if not opened2:
                            print(f"  ✗ 実タッチの連続タップでシートが開かない（リセット/空振り化け?）: {touch}")
                            ng += 1
                        elif touch["scale"] != 1:
                            print(f"  ✗ ロボ上の連続タップでズームが動いた: {touch}")
                            ng += 1
                        else:
                            print("  ✓ 実タッチの連続タップ（150ms間隔）→ シートが開く")

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
                        return {s0, s1, panX: p1.panX, s2};
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
                elif not (view["s0"] == 1 and view["s1"] < 1 and view["s2"] == 1
                          and view["panX"] != 0):
                    print(f"  ✗ ズーム/パンが効いていない: {view}")
                    ng += 1
                else:
                    print(f"  ✓ ピンチズーム/パン（scale 1→{view['s1']:.2f}→リセット1・パン可）")

                # R79-6: B10再発ガード＝ダークにすると本文色が hairline アルファ(.08〜.22)に
                # 落ちる一括置換事故の再発を、計算済みcolorの不透明度で機械検知する
                dark_alpha = page.evaluate(
                    """() => {
                        setTheme('dark');
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
                        setTheme('classic');
                        return out;
                    }""")
                bad = [r for r in dark_alpha if r[2] < 0.75]
                if bad or not dark_alpha:
                    print(f"  ✗ ダーク本文が透けている(B10再発): {bad or 'シート要素なし'}")
                    ng += 1
                else:
                    print(f"  ✓ ダーク本文の不透明度OK（{len(dark_alpha)}要素・B10ガード）")

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

    if ng:
        print(f"✗ PWA 3Dスモーク {ng}件失敗")
        return 1
    print(f"✓ PWA 3Dスモーク合格: {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
