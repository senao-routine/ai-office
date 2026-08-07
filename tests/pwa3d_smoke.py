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
                page = browser.new_page(viewport={"width": 390, "height": 844})
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

                # R79-6: 名札二段マーカー＝全ロボットに足元モノグラムピン＋テキスト名札は最大2枚
                # （選択中/❗先頭）・高さ≤20px。❗があるときロスターは隠れる（ドックの一等地は排他）
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
                if len(marks["pins"]) + len(marks["plates"]) != marks["robots"]:
                    print(f"  ✗ 全ロボットにマーカーが付いていない: {marks}")
                    ng += 1
                elif not all(len(t) == 1 for t in marks["pins"]):
                    print(f"  ✗ ピンが1文字モノグラムでない: {marks}")
                    ng += 1
                elif len(marks["plates"]) > 2 or any(h > 20 for h in marks["plates"]):
                    print(f"  ✗ テキスト名札が2枚超or高さ20px超: {marks}")
                    ng += 1
                else:
                    print(f"  ✓ 名札二段（ピン{len(marks['pins'])}＋名札{len(marks['plates'])}"
                          f"＝ロボ{marks['robots']}体・名札≤20px）")
                # R80-A16: ❗表示中もロスターは**消さず圧縮**する（一番「誰が何を」を知りたい
                # 瞬間に述語が全滅していたため）。ドックが3Dを覆わないよう高さは詰める。
                if marks["attnOn"] and marks["rosterH"] > 46:
                    print(f"  ✗ ❗表示中にロスターが圧縮されていない: {marks}")
                    ng += 1
                elif marks["attnOn"] and marks["rosterH"] == 0:
                    print("  ✗ ❗表示中にロスターが消えている（誰が何をしているかが全滅）")
                    ng += 1
                elif marks["attnOn"]:
                    print(f"  ✓ ❗表示中もロスターを圧縮して残す（{marks['rosterH']}px）")

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
                if skel["tabs"] != 3 or skel["statbar"] or skel["hstats"] < 2:
                    print(f"  ✗ ヘッダー統合/タブ3つの骨格が不正: {skel}")
                    ng += 1
                else:
                    print(f"  ✓ タブ3つ＋ヘッダー統計{skel['hstats']}チップ（statbar廃止）")
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

                # ロボットをタップ → 詳細シートが開く（操作の本流が生きている証明）
                point = page.evaluate(
                    """() => {
                        const ags = window.__scene3d ? window.__scene3d.agents() : [];
                        for (const a of ags) {
                            const p = window.__scene3d.project(a.id);
                            if (p) return {id: a.id, left: p.left, top: p.top};
                        }
                        return null;
                    }""")
                if not point:
                    print("  ✗ ロボットのスクリーン座標が取れない")
                    ng += 1
                else:
                    box = page.locator("#scene3d").bounding_box()
                    page.mouse.click(box["x"] + point["left"], box["y"] + point["top"] + 30)
                    page.wait_for_timeout(700)
                    opened = page.evaluate(
                        "() => { const n=document.getElementById('sheetwrap');"
                        " return !!n && n.classList.contains('open'); }")
                    if opened:
                        print("  ✓ ロボットのタップ → 詳細シートが開く")
                    else:
                        print("  ✗ ロボットをタップしてもシートが開かない")
                        ng += 1

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
            finally:
                browser.close()

    if ng:
        print(f"✗ PWA 3Dスモーク {ng}件失敗")
        return 1
    print(f"✓ PWA 3Dスモーク合格: {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
