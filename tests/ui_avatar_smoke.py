#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""R91: アバターのカスタマイズを「実ファイルまで」検査するスモーク。

本人指摘への回答を機械で固定する:
  ① 名札クリックは **会話（シート）** を開く（帽子モーダルではない）
  ② 🎨 から開く画面で **1体だけ** 色/帽子を変えられる（隣の席は変わらない）
  ③ 「このフォルダ全員」を選べば従来どおりフォルダ設定に落ちる
  ④ リソースの残枠が **ドロワーを開かなくてもヘッダーに実バーで出ている**

`/api/office` はモックしない＝サーバーの実 roster と config を突き合わせる
（モックすると「保存できたつもり」で通ってしまう）。

使い方: python3 tests/ui_avatar_smoke.py   （verify.sh ▶7 から呼ぶ・Playwright必要）
"""
import json
import os
import pathlib
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parent.parent
STYLE = os.environ.get("UI_STYLE", "iso")


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def office(port):
    req = urllib.request.Request(f"http://127.0.0.1:{port}/api/office",
                                 headers={"X-Office-Local": "1"})
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read().decode("utf-8"))


def main():
    from playwright.sync_api import sync_playwright

    home = pathlib.Path(subprocess.run(
        [sys.executable, str(ROOT / "tests/make_home.py")],
        capture_output=True, text=True, check=True).stdout.strip())
    tmp = pathlib.Path(tempfile.mkdtemp(prefix="ui_avatar_"))
    config = tmp / "office_config.json"
    config.write_text('{"projects": {}}', encoding="utf-8")
    env = {**os.environ, "OFFICE_HOME": str(home), "OFFICE_CONFIG": str(config),
           "OFFICE_LANG": "ja", "OFFICE_AGENTS_CLI": "0"}
    port = free_port()
    proc = subprocess.Popen(
        [sys.executable, str(ROOT / "server" / "office_server.py"), "--port", str(port)],
        cwd=str(ROOT), env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(80):
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=.2):
                break
        except OSError:
            time.sleep(.1)

    ng = 0
    errors = []
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(args=["--use-gl=swiftshader", "--disable-gpu"])
            page = browser.new_page(viewport={"width": 1440, "height": 900},
                                    device_scale_factor=1)
            page.on("pageerror", lambda e: errors.append(f"pageerror: {e}"))
            page.on("console", lambda m: errors.append(f"console.error: {m.text}")
                    if m.type == "error" else None)
            # ④ ヘッダー常設ゲージ。status_board だけ差し替える（保存経路は実サーバーのまま）
            board = {"generatedAt": int(time.time()), "fx": {"jpyPerUsd": 155},
                     "providers": [
                         {"id": "claude", "kind": "tokens", "label": "Claude Code",
                          "billing": "subscription",
                          "subscription": {"staleSec": 40, "fiveHour": {"pct": 63},
                                           "sevenDay": {"pct": 41}},
                          "tokens": {"today": {"total": 100}, "last5h": {"total": 10},
                                     "byModel": {}}},
                         {"id": "codex", "kind": "gauge", "status": "ok", "label": "Codex",
                          "billing": "subscription", "usedPercent": 87,
                          "windowMinutes": 300}]}
            page.route("**/api/status_board*", lambda route: route.fulfill(
                status=200, content_type="application/json; charset=utf-8",
                body=json.dumps(board)))
            page.goto(f"http://127.0.0.1:{port}/?ui={STYLE}&t=3.2&seed=11")
            page.wait_for_function("window.__office && window.__office.ready", timeout=30000)
            page.wait_for_function("document.querySelectorAll('#labels .lbl').length >= 2",
                                   timeout=20000)

            page.wait_for_function(
                "document.querySelectorAll('#usage-summary .gpin').length >= 3", timeout=20000)
            pins = page.eval_on_selector_all("#usage-summary .gpin", """ns => ns.map(n => ({
              text: n.textContent.trim(),
              w: n.querySelector('.gpfill').style.width,
              warn: n.querySelector('.gpfill').classList.contains('warn')}))""")
            if [p["w"] for p in pins[:3]] == ["63%", "41%", "87%"] and pins[2]["warn"]:
                print("  ✓ ヘッダーに実バーの常設ゲージ（80%超は警告色）")
            else:
                print(f"  ✗ ヘッダーゲージがおかしい: {pins}")
                ng += 1
            page.click("#usage-summary")
            page.wait_for_timeout(300)
            if page.eval_on_selector(".usage", "el => el.classList.contains('open')"):
                print("  ✓ ゲージをクリックすると従来のドロワーが開く")
            else:
                print("  ✗ ドロワーが開かない（ピンがクリックを飲んでいる）")
                ng += 1
            page.keyboard.press("Escape")
            page.wait_for_timeout(200)

            # ① 名札クリック＝会話（旧: 帽子モーダル直行）
            picked = page.evaluate("""() => {
              const chip = document.querySelector('#labels .lbl');
              chip.click();
              return chip.dataset.project;
            }""")
            page.wait_for_selector("#sheet.show", timeout=8000)
            modal_open = not page.eval_on_selector("#modalwrap", "el => el.hidden")
            if modal_open:
                print("  ✗ 名札クリックで帽子モーダルが開いた（会話が開くべき）")
                ng += 1
            else:
                print("  ✓ 名札クリック → セッションのやり取り（シート）が開く")
            width = page.eval_on_selector("#sheet",
                                          "el => Math.round(el.getBoundingClientRect().width)")
            if width >= 480:
                print(f"  ✓ シート幅 {width}px（旧380pxより広い）")
            else:
                print(f"  ✗ シートが狭いまま: {width}px")
                ng += 1
            page.click("#sheetwide")
            page.wait_for_timeout(250)
            wide = page.eval_on_selector("#sheet",
                                         "el => Math.round(el.getBoundingClientRect().width)")
            if wide > width:
                print(f"  ✓ ⤢ でさらに広がる（{width}→{wide}px）")
            else:
                print(f"  ✗ ⤢ が効かない: {width}→{wide}px")
                ng += 1
            page.click("#sheetwide")

            # ② 🎨 → 1体だけ色を変える
            page.click("#sheetarch")
            page.wait_for_selector("#modal .color-choices", timeout=8000)
            page.eval_on_selector_all(
                "#modal .swatch", "ns => ns.find(n => n.title === 'セージ').click()")
            page.wait_for_timeout(1200)
            saved = json.loads(config.read_text(encoding="utf-8"))
            avatars = saved.get("avatars", {})
            if avatars.get(picked, {}).get("color") == "sage" and len(avatars) == 1:
                print("  ✓ 色は個体（avatars）に保存された")
            else:
                print(f"  ✗ 個体保存されていない: {avatars}")
                ng += 1
            if saved.get("projects") == {}:
                print("  ✓ フォルダ設定は書き換わっていない（隣の席を巻き込まない）")
            else:
                print(f"  ✗ フォルダ設定に漏れた: {saved.get('projects')}")
                ng += 1
            roster = {p["projectId"]: p for p in office(port)["roster"]}
            same_cwd = [p for pid, p in roster.items()
                        if pid != picked and p.get("cwd") == roster[picked].get("cwd")]
            if roster[picked].get("color") == "sage" and all(
                    not p.get("color") for p in same_cwd):
                print(f"  ✓ /api/office で1体だけ色が付く（同じフォルダの他 {len(same_cwd)} 体は無色）")
            else:
                print(f"  ✗ 配色の適用先がおかしい: "
                      f"{[(p['projectId'], p.get('color')) for p in roster.values()]}")
                ng += 1

            # ③ 「このフォルダ全員」＝従来の挙動も残っている
            page.click("#sheetarch")
            page.wait_for_selector("#modal .color-choices", timeout=8000)
            page.eval_on_selector_all(
                "#modal .mkeybtn",
                "ns => ns.find(n => n.textContent === 'このフォルダ全員').click()")
            page.wait_for_timeout(200)
            page.eval_on_selector_all(
                "#modal .mkeybtn", "ns => ns.find(n => n.textContent === 'ベレー帽').click()")
            page.wait_for_timeout(1200)
            saved = json.loads(config.read_text(encoding="utf-8"))
            arches = [m.get("arch") for m in saved.get("projects", {}).values()]
            if arches == ["beret"] and saved.get("avatars") == {}:
                print("  ✓ 「このフォルダ全員」はフォルダ設定へ落ち、個体の上書きを消す")
            else:
                print(f"  ✗ フォルダ適用がおかしい: projects={saved.get('projects')} "
                      f"avatars={saved.get('avatars')}")
                ng += 1

            hard = [e for e in errors if "favicon" not in e]
            if hard:
                print(f"  ✗ コンソールエラー: {hard[:3]}")
                ng += 1
            else:
                print("  ✓ コンソールエラーなし")
            browser.close()
    finally:
        proc.terminate()
        proc.wait(timeout=10)

    print("✅ アバタースモーク green" if ng == 0 else f"❌ アバタースモーク {ng}件失敗")
    return 1 if ng else 0


if __name__ == "__main__":
    sys.exit(main())
