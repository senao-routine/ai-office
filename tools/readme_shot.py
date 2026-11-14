#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""README の絵（docs/screen-app-ja.png・docs/screen-app-en.png）を撮り直す（R129・2026-11-14）。

  .venv/bin/python tools/readme_shot.py               # 日英の 2 枚を docs/ に書く
  .venv/bin/python tools/readme_shot.py --out-dir DIR  # 別の場所へ（見比べる用）

公開物に載る絵＝実データは使わない: 同梱のデモの世界（ui/demo/world.json・一般的な名前）を通常の画面（?ui=app）へ流し込み、
選んだ人（Design Review）の記録に短い会話を差し込む（デモの道 ?demo=1 は答えのボタンと会話を出さない作り＝絵として弱い）。
一時 HOME の daemon で撮る＝この Mac のセッションは読まない。3D は SwiftShader（golden と同じ）。
"""
import argparse
import json
import os
import pathlib
import socket
import subprocess
import sys
import tempfile
import time

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
from ui_shot import SWIFTSHADER, free_port  # noqa: E402

SESSION = "demo-0003"
NOW = 1794650000
TEXT = {
    "ja": {
        "rows": [("u", "設定画面を 1 画面に収めて。スマホでも読めるように"),
                 ("a", "設定の項目を 3 つの組に分け、スマホの幅で折り返すように直します。"),
                 ("a2", "テストは通りました（42 件）。並べ方は 2 通り作れます。")],
        "question": "詰めた並べ方にしますか？", "options": ["詰める", "今のまま"],
    },
    "en": {
        "rows": [("u", "Fit the settings screen on one page, and make it readable on a phone"),
                 ("a", "I'll split the settings into three groups and let them wrap at phone width."),
                 ("a2", "Tests pass (42). There are two ways to lay it out.")],
        "question": "Use the compact layout?", "options": ["Compact", "Standard"],
    },
}


def conversation(lang):
    (_, ask), (_, reply), (_, done) = TEXT[lang]["rows"]
    return [
        {"k": "u", "t": ask, "ts": NOW - 600},
        {"k": "a", "t": reply, "ts": NOW - 590},
        {"k": "tool", "id": "t1", "n": "Read", "t": "ui/settings.js", "ts": NOW - 580}, {"k": "res", "for": "t1", "rl": 120, "ts": NOW - 579},
        {"k": "tool", "id": "t2", "n": "Edit", "t": "ui/settings.css", "ts": NOW - 560}, {"k": "res", "for": "t2", "rl": 4, "ts": NOW - 559},
        {"k": "tool", "id": "t3", "n": "Bash", "t": "npm test", "ts": NOW - 520}, {"k": "res", "for": "t3", "rl": 18, "ts": NOW - 500},
        {"k": "a", "t": done, "ts": NOW - 320},
    ]


def world_for(lang):
    world = json.loads((ROOT / "ui" / "demo" / "world.json").read_text(encoding="utf-8"))
    world["lang"] = lang
    rows = [*world.get("employees", []), *world.get("roster", []),
            *(s for r in world.get("roster", []) for s in (r.get("sessions") or []))]
    for row in rows:
        if row.get("session") == SESSION:
            row["question"] = TEXT[lang]["question"]
            row["questionOptions"] = [{"label": o} for o in TEXT[lang]["options"]]
    return world


def shoot(out, lang):
    from playwright.sync_api import sync_playwright
    body = json.dumps(world_for(lang), ensure_ascii=False)
    activity = json.dumps({"ok": True, "rows": conversation(lang), "cur": {"off": 9, "h": "readme"},
                           "beg": {"off": 0, "h": "readme", "i": 0}, "top": True, "gap": False, "reset": True}, ensure_ascii=False)
    with tempfile.TemporaryDirectory(prefix="readme-shot-") as tmp:
        port = free_port()
        proc = subprocess.Popen([sys.executable, str(ROOT / "server" / "office_server.py"), "--port", str(port)], cwd=str(ROOT),
                                env={**os.environ, "HOME": tmp, "OFFICE_HOME": tmp, "OFFICE_DATA": tmp},
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            for _ in range(100):
                try:
                    socket.create_connection(("127.0.0.1", port), timeout=.2).close()
                    break
                except OSError:
                    time.sleep(.1)
            with sync_playwright() as pw:
                browser = pw.chromium.launch(args=SWIFTSHADER)
                try:
                    page = browser.new_page(viewport={"width": 1440, "height": 900}, device_scale_factor=1)
                    page.route("**/api/office*", lambda r: r.fulfill(status=200, content_type="application/json", body=body))
                    page.route("**/api/session/activity*", lambda r: r.fulfill(status=200, content_type="application/json", body=activity))
                    page.goto(f"http://127.0.0.1:{port}/?ui=app&seed=11", wait_until="domcontentloaded")
                    page.wait_for_function("window.__office && window.__office.ready", timeout=90000)
                    page.evaluate("(s) => window.__office.debug.select(s)", SESSION)
                    page.wait_for_selector("#app-answer button[data-answer]", timeout=20000)
                    page.wait_for_timeout(1500)
                    out.parent.mkdir(parents=True, exist_ok=True)
                    page.screenshot(path=str(out))
                    print(f"  ✓ {out}（ボタン: {' / '.join(page.locator('#app-answer button[data-answer]').all_inner_texts())}）")
                finally:
                    browser.close()
        finally:
            proc.terminate()
            proc.wait(timeout=10)


def main(argv=None):
    ap = argparse.ArgumentParser(description="README の絵を撮り直す")
    ap.add_argument("--out-dir", type=pathlib.Path, default=ROOT / "docs")
    args = ap.parse_args(argv)
    for lang in ("ja", "en"):
        shoot(args.out_dir / f"screen-app-{lang}.png", lang)
    return 0


if __name__ == "__main__":
    sys.exit(main())
