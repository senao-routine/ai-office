#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""README ヒーローGIFの決定論レンダラ（R50提案2d）。

fixture 基盤（ui_shot の free_port/start_server）＋ ?demo=1（同梱英語world）で
実セッションに依存せず撮る。時刻は clock.setTime → probe.inject の再描画で進める
（frozen ロードのままフレームを刻む＝毎回同じGIFになる）。

使い方: <playwright入りpython> tools/demo_gif.py [--out docs/demo.gif]
"""
import argparse
import io
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
from ui_shot import SWIFTSHADER, free_port, start_server  # noqa: E402

VIEW = {"width": 1280, "height": 800}
T0, STEP, FRAMES = 3.2, 0.45, 24
OUT_W = 880
DELAY_MS = 150


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(ROOT / "docs" / "demo.gif"))
    ap.add_argument("--gpu", action="store_true",
                    help="実GPUで撮る（見栄え優先・ビット再現は保証しない）")
    args = ap.parse_args()
    from PIL import Image
    from playwright.sync_api import sync_playwright

    port = free_port()
    proc = start_server(port)
    frames = []
    try:
        with sync_playwright() as pw:
            launch_args = ["--use-angle=metal"] if args.gpu else SWIFTSHADER
            browser = pw.chromium.launch(args=launch_args)
            page = browser.new_page(viewport=VIEW, device_scale_factor=1)
            page.goto(f"http://127.0.0.1:{port}/?ui=iso&demo=1&t={T0}&seed=11")
            page.wait_for_function("window.__office && window.__office.ready", timeout=60000)
            page.wait_for_timeout(400)
            for i in range(FRAMES):
                t = T0 + i * STEP
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
                im = im.resize((OUT_W, int(im.height * OUT_W / im.width)), Image.LANCZOS)
                frames.append(im.quantize(colors=256, dither=Image.FLOYDSTEINBERG))
            browser.close()
    finally:
        proc.terminate()

    out = pathlib.Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    frames[0].save(out, save_all=True, append_images=frames[1:],
                   duration=DELAY_MS, loop=0, optimize=True)
    kb = out.stat().st_size // 1024
    print(f"✓ {out} ({len(frames)}コマ・{kb}KB)")
    if kb > 4500:
        print("⚠ 4.5MB超（GitHub READMEでの読み込みが重い）— FRAMES/OUT_W を下げる")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
