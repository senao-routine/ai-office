#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""README ヒーローGIFの決定論レンダラ（R50提案2d）。R106後: v3.0の既定画面（cockpit）も撮れる。

fixture 基盤（ui_shot の free_port/start_server）＋ ?demo=1（同梱英語world）で
実セッションに依存せず撮る。時刻は clock.setTime → probe.inject の再描画で進める
（frozen ロードのままフレームを刻む＝毎回同じGIFになる）。

--style iso（既定・今の出力を変えない）: 3D。従来どおり、同じ demo world を毎フレーム注入し直して
  clock を進める（iso 側に「時刻だけ進める」専用口が無いため）。
--style cockpit（R99〜R106・v3.0 の既定画面）: 俯瞰ドット絵×台帳×端末。cockpit は
  window.__office.debug.step(t) で「時刻だけ進めて描き直す」専用口を持つ（tools/demo_clip.py と同じ仕掛け）。
  デモ world の「確認待ち」2 件のうち 1 件目（demo-0003）を、途中の 1 コマで答えた後の状態へ書き換えて
  注入する＝「あなた待ちに答えると次へ進む」（❗ 2→1・机へ歩いて戻る）をデモ world の範囲内で見せる
  （答えるボタン自体は DEMO では黒塗りなので、本物の送信は動かさない＝world の書き換えで見せる）。

使い方: <playwright入りpython> tools/demo_gif.py [--style iso|cockpit] [--out docs/demo.gif]
"""
import argparse
import io
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
from ui_shot import SWIFTSHADER, free_port, start_server  # noqa: E402

# ── iso（既定・今の出力を変えない）─────────────────────────────────────────
ISO_VIEW = {"width": 1280, "height": 800}
ISO_T0, ISO_STEP, ISO_FRAMES = 3.2, 0.45, 24
ISO_OUT_W = 880
ISO_DELAY_MS = 150
ISO_SIZE_LIMIT_KB = 4500

# ── cockpit（R99〜R106・v3.0 の既定画面）────────────────────────────────────
# 1440×900 で撮る（R105: 3カラム＝部屋・台帳・端末が同時に出る幅）。GIF は README向けに縮める。
COCKPIT_VIEW = {"width": 1440, "height": 900}
# 0.0〜8.4秒（世界時間）: 0.0に❗2件（受付で2人待ち）→1.0で1件目が答えた体に書き換え（机へ歩いて戻る・
# 歩行の実測＝約6秒）→8.4で机に着いて作業を再開した絵のまま数コマ静止。再生は30コマ×300ms＝9.0秒。
COCKPIT_T0, COCKPIT_STEP, COCKPIT_FRAMES = 0.0, 0.29, 30
COCKPIT_RESOLVE_AT = 1.0
COCKPIT_RESOLVE_SESSION = "demo-0003"
COCKPIT_OUT_W = 950
COCKPIT_DELAY_MS = 300
COCKPIT_SIZE_LIMIT_KB = 3072

DEFAULT_OUT_NAME = {"iso": "demo.gif", "cockpit": "demo-cockpit.gif"}


def _resolve_demo_world(world, session):
    """cockpit用: デモworldの中の1件（`session`）を「答えた後」の状態へ書き換える（デモworldの範囲内）。

    roster・roster[].sessions[]・employees の3箇所を揺らさないと行に届かない
    （tools/demo_clip.py の scenario()/variant() と同じ事情＝ui/hud/board.js の重なり順）。
    """
    doc = json.loads(json.dumps(world))
    answered = {
        "state": "working", "attention": False, "approvalMin": 0, "question": "",
        "verb": "Editing", "target": "the compact layout", "kind": "tool",
        "detail": "the compact layout",
        "work": {"now": ["the compact layout"], "next": [], "done": []},
        "feed": ["Editing the compact layout"],
    }
    for r in doc.get("roster", []):
        if r.get("session") != session:
            continue
        r.update(answered)
        for s in r.get("sessions", []):
            if s.get("session") == session:
                s.update(state="working", attention=False)
    for e in doc.get("employees", []):
        if e.get("session") != session:
            continue
        e.update(answered)
    return doc


def _capture_iso(browser, port):
    page = browser.new_page(viewport=ISO_VIEW, device_scale_factor=1)
    page.goto(f"http://127.0.0.1:{port}/?ui=iso&demo=1&t={ISO_T0}&seed=11")
    page.wait_for_function("window.__office && window.__office.ready", timeout=60000)
    page.wait_for_timeout(400)
    from PIL import Image
    frames = []
    for i in range(ISO_FRAMES):
        t = ISO_T0 + i * ISO_STEP
        # clock.setTime → probe.inject（同じworld）で t 時点の絵を再描画する
        page.evaluate(
            """async (t) => {
                const clock = await import('/ui/platform/clock.js');
                clock.setTime(t);
                window.__office.inject(window.__demoWorld
                  || (window.__demoWorld = await (await fetch('/ui/demo/world.json',
                       {headers: {'X-Office-Local': '1'}})).json()));
            }""", t)
        page.wait_for_timeout(120)
        png = page.screenshot()
        im = Image.open(io.BytesIO(png)).convert("RGB")
        im = im.resize((ISO_OUT_W, int(im.height * ISO_OUT_W / im.width)), Image.LANCZOS)
        frames.append(im.quantize(colors=256, dither=Image.FLOYDSTEINBERG))
    return frames


def _capture_cockpit(browser, port):
    page = browser.new_page(viewport=COCKPIT_VIEW, device_scale_factor=1)
    page.goto(f"http://127.0.0.1:{port}/?ui=cockpit&demo=1&t={COCKPIT_T0}&seed=11")
    page.wait_for_function("window.__office && window.__office.ready", timeout=60000)
    page.wait_for_timeout(400)
    from PIL import Image
    world = json.loads((ROOT / "ui" / "demo" / "world.json").read_text(encoding="utf-8"))
    resolved = _resolve_demo_world(world, COCKPIT_RESOLVE_SESSION)
    injected = False
    frames = []
    for i in range(COCKPIT_FRAMES):
        t = COCKPIT_T0 + i * COCKPIT_STEP
        # 先に時計を送る（tools/demo_clip.py と同じ順序: step(t)→inject は「t に届いたworld」として
        # 歩行をtから始める）。注入は「答えた」コマだけ＝デモworldの範囲内の書き換え。
        page.evaluate("(t) => window.__office.debug.step(t)", t)
        if not injected and t >= COCKPIT_RESOLVE_AT:
            page.evaluate("(w) => window.__office.inject(w)", resolved)
            page.evaluate("(t) => window.__office.debug.step(t)", t)
            injected = True
        png = page.screenshot()
        im = Image.open(io.BytesIO(png)).convert("RGB")
        im = im.resize((COCKPIT_OUT_W, int(im.height * COCKPIT_OUT_W / im.width)), Image.LANCZOS)
        # ドット絵＋文字のUI＝ディザを掛けない（ノイズが文字の縁を潰し、GIFも重くなる）
        frames.append(im.quantize(colors=256, dither=Image.Dither.NONE))
    return frames


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--style", choices=("iso", "cockpit"), default="iso",
                     help="iso=3D（既定・今の出力を変えない）／cockpit=v3.0の既定画面（俯瞰ドット絵×台帳×端末）")
    ap.add_argument("--out", default=None, help="既定: iso=docs/demo.gif／cockpit=docs/demo-cockpit.gif")
    ap.add_argument("--gpu", action="store_true",
                    help="実GPUで撮る（見栄え優先・ビット再現は保証しない）")
    args = ap.parse_args()
    out = pathlib.Path(args.out) if args.out else ROOT / "docs" / DEFAULT_OUT_NAME[args.style]
    from playwright.sync_api import sync_playwright

    port = free_port()
    proc = start_server(port)
    try:
        with sync_playwright() as pw:
            launch_args = ["--use-angle=metal"] if args.gpu else SWIFTSHADER
            browser = pw.chromium.launch(args=launch_args)
            frames = _capture_iso(browser, port) if args.style == "iso" else _capture_cockpit(browser, port)
            browser.close()
    finally:
        proc.terminate()

    out.parent.mkdir(parents=True, exist_ok=True)
    delay_ms = ISO_DELAY_MS if args.style == "iso" else COCKPIT_DELAY_MS
    frames[0].save(out, save_all=True, append_images=frames[1:],
                   duration=delay_ms, loop=0, optimize=True)
    kb = out.stat().st_size // 1024
    limit_kb = ISO_SIZE_LIMIT_KB if args.style == "iso" else COCKPIT_SIZE_LIMIT_KB
    seconds = len(frames) * delay_ms / 1000
    print(f"✓ {out} ({len(frames)}コマ・{delay_ms}ms/コマ・{seconds:.1f}秒・{kb}KB・style={args.style})")
    if kb > limit_kb:
        print(f"⚠ {limit_kb / 1024:.2f}MB超（GitHub READMEでの読み込みが重い）— FRAMES/OUT_W を下げる")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
