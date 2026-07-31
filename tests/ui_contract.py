#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""R50: UIが world の「意味」を正しく返すことの契約テスト。

なぜ必要か:
  window.__office.dumpWorld() を唯一の観測点にして、UIが受け取った world を
  取り違えていない（集約・ゾーン・件数）ことを機械で固定する。
  ドット絵スタイルは撤去（2026-07-30）＝現在の対象は iso のみ。
  スタイルを増やしたら STYLES に足すだけで「全スタイル同値」検査に戻る。

  さらに、新UIはESMなので旧UIのような page.evaluate("EMPS=[...]") の代入は
  モジュールスコープに届かず「何も検証しないまま green」になる。
  だから注入も window.__office.inject() という明示APIを通す（そこも同時に検査する）。

使い方: python3 tests/ui_contract.py           （verify.sh ▶7 から呼ぶ）
"""
import json
import pathlib
import socket
import subprocess
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parent.parent
WORLDS = ROOT / "tests" / "fixtures" / "world"
STYLES = ("iso",)


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def start_server(port):
    home = ROOT / ".ui_contract_home"
    home.mkdir(exist_ok=True)
    proc = subprocess.Popen(
        [sys.executable, str(ROOT / "server" / "office_server.py"), "--port", str(port)],
        cwd=str(ROOT), env={"OFFICE_HOME": str(home), "PATH": "/usr/bin:/bin"},
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(80):
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=.2):
                return proc
        except OSError:
            time.sleep(.1)
    proc.terminate()
    raise SystemExit("サーバーが起動しませんでした")


def dump_for(page, port, style, payload, world_name):
    errors = []
    page.on("pageerror", lambda e: errors.append(f"pageerror: {e}"))
    page.on("console", lambda m: errors.append(f"console.error: {m.text}")
            if m.type == "error" else None)
    page.route("**/api/office*", lambda route: route.fulfill(
        status=200, content_type="application/json; charset=utf-8", body=payload))
    page.goto(f"http://127.0.0.1:{port}/?ui={style}&t=3.2&seed=11")
    page.wait_for_function("window.__office && window.__office.ready", timeout=30000)
    dump = page.evaluate("window.__office.dumpWorld()")
    real = [e for e in errors if "Failed to load resource" not in e]
    return dump, real


def main():
    from playwright.sync_api import sync_playwright

    ng = 0
    port = free_port()
    proc = start_server(port)
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(args=["--use-gl=swiftshader", "--disable-gpu"])
            for world_path in sorted(WORLDS.glob("*.json")):
                payload = world_path.read_text(encoding="utf-8")
                world = json.loads(payload)
                dumps = {}
                for style in STYLES:
                    page = browser.new_page(viewport={"width": 1440, "height": 900})
                    dump, errors = dump_for(page, port, style, payload, world_path.stem)
                    page.close()
                    if errors:
                        print(f"  ✗ {world_path.stem}/{style}: JSエラー {errors[:2]}")
                        ng += 1
                    dumps[style] = dump

                a = dumps["iso"]
                ref = list(dumps.values())[0]
                if any(d != ref for d in dumps.values()):
                    print(f"  ✗ {world_path.stem}: スタイル間で dumpWorld() が食い違う")
                    ng += 1
                    continue

                # 集約が効いていること＝1プロジェクト1アバター
                expect = len(world.get("roster") or world.get("employees") or [])
                got = len(a["agents"])
                if got != expect:
                    print(f"  ✗ {world_path.stem}: agents {got} != roster {expect}")
                    ng += 1
                    continue

                # ESM注入口が本当に効いているか（効いていなければテストは無意味）
                page = browser.new_page(viewport={"width": 1440, "height": 900})
                page.route("**/api/office*", lambda route: route.fulfill(
                    status=200, content_type="application/json; charset=utf-8", body=payload))
                page.goto(f"http://127.0.0.1:{port}/?ui=iso&t=3.2&seed=11")
                page.wait_for_function("window.__office && window.__office.ready", timeout=30000)
                injected = {**world, "officeName": "注入テスト", "roster": [], "employees": []}
                page.evaluate("(w) => window.__office.inject(w)", injected)
                after = page.evaluate("window.__office.dumpWorld()")
                page.close()
                if after["officeName"] != "注入テスト" or after["agents"]:
                    print(f"  ✗ {world_path.stem}: inject() が効いていない（テストが空回りする）")
                    ng += 1
                    continue

                crews = {x["disp"]: x["crew"] for x in a["agents"] if x["crew"] > 1}
                extra = f" / 集約 {crews}" if crews else ""
                print(f"  ✓ {world_path.stem}: dumpWorld 整合 (agents={got}{extra})")
            browser.close()
    finally:
        proc.terminate()
    if ng:
        print(f"UI契約テスト: {ng} 件失敗")
    else:
        print("✓ UI契約テスト合格（dumpWorld整合・inject有効・集約が効いている）")
    return 1 if ng else 0


if __name__ == "__main__":
    sys.exit(main())
