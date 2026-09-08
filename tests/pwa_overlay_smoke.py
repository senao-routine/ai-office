#!/usr/bin/env python3
"""PWA実ページの骨格回帰。生成済みWorker配信物＋固定statusをPlaywrightで開く。

既存のPlaywright入りPythonで実行。本物の認証・中継には接続しない。
relay_e2e.shの代替ではなく、全画面/名札/ロスターと起動時例外を素早く検出する。
"""
import base64
import json
import subprocess
import time
from pathlib import Path
from urllib.parse import urlparse

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "output" / "playwright"


def fixture(count, attention=False):
    roster = [{"session": f"fixture-{i}", "projectId": f"project-{i}",
               "disp": f"検証室 {i + 1}号", "state": "working", "vendor": "codex",
               "detail": "表示を確認中", "verb": "編集中", "target": "layout.css",
               "age": 1, "listening": True, "level": i + 1} for i in range(count)]
    if attention:
        roster[-1].update(question="進め方を選んでください", approvalMin=3,
                          ask={"kind": "question", "tool": "AskUserQuestion"},
                          questionOptions=[{"label": label, "desc": "選択肢の説明"}
                                           for label in ["案A", "案B", "案C"]])
        roster[-2].update(approvalMin=2, ask={"kind": "permission", "tool": "Bash"})
    return {"officeName": "検証オフィス", "roster": roster, "employees": roster,
            "relay": {"pct": 20}, "actions": {"recipes": []}, "lang": "ja"}


def status(office):
    return {"json": json.dumps(office), "ts": int(time.time()),
            "agentSeenAgo": 0, "agentOnline": True}


GEOMETRY = """() => {
  const rect = s => document.querySelector(s).getBoundingClientRect();
  const c = rect('#scene3d canvas'), r = rect('#topdock #roster .rchip');
  const dock = rect('#dock'), header = rect('.topbar'), tabs = rect('#tabbar');
  return {canvas: [c.width, c.height], viewport: [innerWidth, innerHeight],
    scrollable: document.scrollingElement.scrollHeight > innerHeight + 1,
    rosterTop: r.top, rosterHeight: r.height, headerBottom: header.bottom,
    dockBottom: dock.bottom, tabsTop: tabs.top,
    plates: [...document.querySelectorAll('#plates .plate')].map(n => n.getBoundingClientRect().height),
    pins: document.querySelectorAll('#plates .pin').length,
    info: !!document.querySelector('#dock #infodock #ticker') &&
          !!document.querySelector('#dock #infodock #gaugebar')};
}"""


def check_geometry(page, count):
    page.wait_for_function("n => document.querySelectorAll('#plates .plate,#plates .pin').length === n", arg=count)
    box = page.evaluate(GEOMETRY)
    assert box["canvas"] == box["viewport"], box
    assert not box["scrollable"], box
    assert 40 <= box["rosterTop"] <= 260, box
    assert box["rosterTop"] >= box["headerBottom"], box
    assert box["dockBottom"] <= box["tabsTop"], box
    assert all(height <= 20 for height in box["plates"]), box
    assert box["info"], box
    if count <= 6:
        assert len(box["plates"]) == count and box["pins"] == 0, box
    else:
        assert len(box["plates"]) <= 2, box
    return box


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(["node", "--input-type=module", "-e", """
        import {APP_HTML} from './relay/src/app_html.js';
        import {MODULES,ASSETS} from './relay/src/modules_data.js';
        process.stdout.write(JSON.stringify({APP_HTML,MODULES,ASSETS}));
    """], cwd=ROOT, capture_output=True, text=True, check=True)
    bundle = json.loads(result.stdout)
    office = fixture(6, attention=True)
    errors = []

    def serve(route):
        path = urlparse(route.request.url).path
        if path == "/app":
            route.fulfill(body=bundle["APP_HTML"], content_type="text/html")
        elif path == "/status":
            route.fulfill(json=status(office))
        elif path in bundle["MODULES"]:
            route.fulfill(body=bundle["MODULES"][path], content_type="application/javascript")
        elif path in bundle["ASSETS"]:
            mime, data = bundle["ASSETS"][path]
            route.fulfill(body=base64.b64decode(data), content_type=mime)
        elif path.endswith(".webmanifest"):
            route.fulfill(json={"name": "Fixture", "start_url": "/app", "display": "standalone"})
        else:
            route.fulfill(status=204)

    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        try:
            context = browser.new_context(viewport={"width": 390, "height": 844},
                                          has_touch=True, service_workers="block")
            context.route("**/*", serve)
            context.add_init_script("""
              localStorage.setItem('aioffice.cred', JSON.stringify({d:'fixture',s:'fixture',t:'fixture'}));
              // ローカルHTTP fixtureで既存のポーリング退避経路を使う。
              window.WebSocket = class { constructor() { throw new Error('Fixture polling'); } };
            """)
            page = context.new_page()
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.goto("http://127.0.0.1:4799/app?t=3.2&seed=11", wait_until="domcontentloaded")
            page.wait_for_function("() => window.__scene3d?.stats()?.robots === 6", timeout=60000)
            assert page.evaluate("typeof NTOG_STATE") == "boolean"
            assert page.evaluate("__scene3d.stats().quality") == "mobile"
            before = check_geometry(page, 6)
            assert before["rosterHeight"] == 44, before
            opts = page.locator("#attncards .qopt").evaluate_all("""nodes => nodes.map(n => {
              const r = n.getBoundingClientRect(), hit = document.elementFromPoint(r.x+r.width/2,r.y+r.height/2);
              return r.height >= 44 && !!hit && (hit === n || n.contains(hit)); })""")
            assert opts == [True, True, True], opts
            page.screenshot(path=OUT / "pwa-overlay-attention.png")
            print("✓ 390×844 全画面・scrollable:false・ロスター上部44px・名札6枚20px・選択肢3件が押せる")

            page.evaluate("d => applyStatus(d)", status(fixture(6)))
            after = check_geometry(page, 6)
            assert after["rosterHeight"] > before["rosterHeight"], after
            page.screenshot(path=OUT / "pwa-overlay-idle.png")
            print("✓ 対応終了でロスターを展開、ダイジェスト＋ゲージは下部に固定")

            page.evaluate("d => applyStatus(d)", status(fixture(7)))
            page.wait_for_function("() => document.querySelectorAll('#plates .pin').length === 7")
            check_geometry(page, 7)
            page.evaluate("tapAgent(__scene3d.agents()[0].id)")
            page.wait_for_function("() => document.querySelectorAll('#plates .plate').length === 1")
            page.evaluate("tapAgent(__scene3d.agents()[0].id)")
            page.locator("#sheetwrap.open").wait_for()
            page.locator("#sh_close").click()
            page.evaluate("d => applyStatus(d)", status(fixture(7, attention=True)))
            check_geometry(page, 7)
            print("✓ 7体以上は小型ピン＋選択/❗名札、2段タップで詳細シート")

            page.set_viewport_size({"width": 844, "height": 390})
            check_geometry(page, 7)
            page.set_viewport_size({"width": 390, "height": 844})
            page.evaluate("d => applyStatus(d)", status(fixture(20)))
            page.locator("#tb_list").click()
            page.evaluate("scrollTo(0, document.scrollingElement.scrollHeight)")
            assert page.evaluate("scrollY > 0"), "リストはスクロール可能であること"
            page.locator("#tb_office").click()
            check_geometry(page, 20)
            assert page.evaluate("scrollY") == 0
            print("✓ 回転とリスト往復後も全画面固定（リストのスクロールは維持）")

            fallback = context.new_page()
            fallback.on("pageerror", lambda error: errors.append(str(error)))
            fallback.route("**/ui/pwa/boot3d.js", lambda route: route.abort())
            fallback.goto("http://127.0.0.1:4799/app", wait_until="domcontentloaded")
            fallback.locator("#list:not(.hidden)").wait_for(timeout=10000)
            assert fallback.evaluate("typeof NTOG_STATE") == "boolean"
            assert not errors, errors
            print("✓ 3D取得失敗でリスト退避・全ケースpageerrorなし・NTOG_STATE保持")
        finally:
            browser.close()


if __name__ == "__main__":
    main()
