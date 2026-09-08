#!/usr/bin/env python3
"""Render every seat with a distinct hue: material/UV inspection alone misses r185 caching.

Run with the Playwright/Pillow Python used by verify.sh. Temporary browser/server
files stay under tests/artifacts/.tmp and are removed even when assertions fail.
"""
import colorsys
import hashlib
import io
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time

from PIL import Image, ImageDraw
from playwright.sync_api import sync_playwright

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from ui_shot import ROOT, SWIFTSHADER, VIEWPORT, free_port


def start_server(port, tmp):
    proc = subprocess.Popen([sys.executable, str(ROOT / "server/office_server.py"), "--port", str(port)],
                            cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                            env={**os.environ, "OFFICE_HOME": tmp, "OFFICE_DATA": tmp,
                                 "OFFICE_CONFIG": str(Path(tmp) / "office_config.json")})
    for _ in range(80):
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=.2):
                return proc
        except OSError:
            if proc.poll() is not None:
                break
            time.sleep(.1)
    proc.terminate()
    proc.wait(timeout=10)
    raise RuntimeError("fixture server did not start")


def open_scene(browser, base, world, query=""):
    context = browser.new_context(viewport=VIEWPORT, device_scale_factor=1)
    page = context.new_page()
    page.route("**/api/office*", lambda route: route.fulfill(
        status=200, content_type="application/json", body=json.dumps(world)))
    page.goto(f"{base}/?ui=iso&t=3.2&seed=11{query}")
    page.wait_for_function("window.__office?.ready", timeout=30000)
    page.evaluate("document.fonts.ready")
    page.wait_for_timeout(300)
    return context, page


def hue_index(rgb, n):
    hue, saturation, value = colorsys.rgb_to_hsv(*(c / 255 for c in rgb))
    if saturation < .55 or value < .35:
        return None
    distances = [abs((hue * 360 - i * 330 / n + 180) % 360 - 180) for i in range(n)]
    index = min(range(n), key=distances.__getitem__)
    return index if distances[index] < 6 else None


def pixels_inside(image, polygon):
    mask = Image.new("1", image.size)
    ImageDraw.Draw(mask).polygon([tuple(p) for p in polygon], fill=1)
    return [rgb for rgb, selected in zip(image.getdata(), mask.getdata()) if selected]


def check_screens(page):
    before = Image.open(io.BytesIO(page.screenshot())).convert("RGB")
    result = page.evaluate("""() => {
      const s = window.__debugScene, d = s.displays;
      const seats = s.monitors.children.filter(m => m.name.startsWith('monitor:seat:'))
        .sort((a, b) => Number(a.name.split(':').at(-1)) - Number(b.name.split(':').at(-1)));
      const n = seats.length;
      for (let i = 0; i < n; i++) {
        // Keep the old-map path so this exact pixel gate can demonstrate the pre-fix failure.
        const texture = d.maps ? d.maps[i] : d.texture;
        const { cols = 1 } = texture.userData;
        const col = d.maps ? 0 : i % cols, row = d.maps ? 0 : Math.floor(i / cols);
        const ctx = texture.image.getContext('2d');
        ctx.fillStyle = `hsl(${i * 330 / n},100%,50%)`;
        ctx.fillRect(col * 512, row * 320, 512, 320);
        texture.needsUpdate = true;
      }
      s._rerender();
      const rect = s.renderer.domElement.getBoundingClientRect();
      const polygon = mesh => {
        const p = mesh.geometry.getAttribute('position');
        return [0, 1, 3, 2].map(i => {
          const v = mesh.position.clone().set(p.getX(i) * .7, p.getY(i) * .7, p.getZ(i))
            .applyMatrix4(mesh.matrixWorld).project(s.camera);
          return [rect.x + (v.x + 1) * rect.width / 2, rect.y + (1 - v.y) * rect.height / 2];
        });
      };
      return { n, stats: s.stats(), seats: seats.map(polygon),
        daily: polygon(s.monitors.getObjectByName('screen:daily')),
        materials: new Set(seats.map(m => m.material)).size,
        textures: new Set(seats.map(m => m.material.map)).size };
    }""")
    after = Image.open(io.BytesIO(page.screenshot())).convert("RGB")
    n = result["n"]
    counts = [0] * n
    for amount, rgb in after.getcolors(after.width * after.height):
        index = hue_index(rgb, n)
        if index is not None:
            counts[index] += amount
    daily_before = pixels_inside(before, result["daily"])
    daily_after = pixels_inside(after, result["daily"])
    daily_colored = sum(hue_index(rgb, n) is not None for rgb in daily_after)
    daily_changed = sum(a != b for a, b in zip(daily_before, daily_after))
    seat_counts = [sum(hue_index(rgb, n) == i for rgb in pixels_inside(after, polygon))
                   for i, polygon in enumerate(result["seats"])]
    print(json.dumps({"colors_px": counts, "seat_interiors_px": seat_counts,
                      "daily_px": len(daily_after), "daily_colored_px": daily_colored,
                      "daily_changed_px": daily_changed, **{k: result[k] for k in
                          ("stats", "materials", "textures")}}, ensure_ascii=False), flush=True)
    assert n == 12, f"expected 12 seats, got {n}"
    # 1机2席が向かい合う配置なので、俯瞰では**半分のモニタが背面/見切れ**になる（実測でも
    # 偶数席だけが自分の色を出し、奇数席は 0）。「全席が見える」は物理的に成立しないので、
    # 元のバグの signature ＝「1色が画面を占め、他が全部ゼロ」を落とす形で固定する。
    # 修正前の実測: 12色中1色だけが 4824px、他は 0〜6px。
    visible = [c for c in counts if c >= 200]
    assert len(visible) >= 6, f"at least half the monitors must show their own texture: {counts}"
    assert max(counts) <= sum(counts) * 0.5, \
        f"no single texture may dominate (the old shared-material bug): {counts}"
    own = [c for c in seat_counts if c >= 20]
    assert len(own) >= n // 2, f"each front-facing seat must show its own hue: {seat_counts}"
    assert len(daily_after) >= 200 and daily_colored <= 5 and daily_changed <= 5, "daily changed with seat textures"
    assert result["materials"] == result["textures"] == 1, "seats must share one material and texture"
    assert result["stats"]["drawCalls"] <= 300 and result["stats"]["materials"] <= 64


def main():
    tmp_root = ROOT / "tests/artifacts/.tmp"
    tmp_root.mkdir(parents=True, exist_ok=True)
    previous_tmp = os.environ.get("TMPDIR")
    try:
        with tempfile.TemporaryDirectory(prefix="iso-screens-", dir=tmp_root) as tmp:
            os.environ["TMPDIR"] = tmp
            port = free_port()
            proc = start_server(port, tmp)
            try:
                world = json.loads((ROOT / "tests/fixtures/world/basic.json").read_text())
                with sync_playwright() as pw:
                    browser = pw.chromium.launch(args=SWIFTSHADER)
                    try:
                        hashes = []
                        for attempt in range(2):
                            context, page = open_scene(browser, f"http://127.0.0.1:{port}", world)
                            hashes.append(hashlib.sha256(page.screenshot()).hexdigest())
                            if attempt == 1:
                                check_screens(page)
                            context.close()
                        print("determinism_sha256=" + ",".join(hashes), flush=True)
                        assert hashes[0] == hashes[1], "same t/seed must render bit-identically"
                    finally:
                        browser.close()
            finally:
                proc.terminate()
                proc.wait(timeout=10)
    finally:
        if previous_tmp is None:
            os.environ.pop("TMPDIR", None)
        else:
            os.environ["TMPDIR"] = previous_tmp
        if not any(tmp_root.iterdir()):
            tmp_root.rmdir()
    print("✓ seat colors / daily isolation / shared atlas / render budget / determinism")
    return 0


if __name__ == "__main__":
    sys.exit(main())
