#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""R99-P4-7: ホーム画面のアイコン（コードで描いた PNG）とマニフェストの形を、wrangler 無しで固定する（verify の unittest で毎回走る）。

中継の実配信（308・200・ETag・ready）は tests/pwa_install_smoke.py と pwa3d_smoke の _install_pins（relay_e2e）が見る。
ここは「生成物が正本（cells.js・palette.js・hud-tokens.css）と一致」「worker.js のマニフェストが色を書き写さず
SW の範囲 /app/ に揃っている」を見る。
"""
import contextlib
import importlib.util
import io
import math
import re
import struct
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("gen_pwa_icons_t", ROOT / "tools" / "gen_pwa_icons.py")
g = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(g)


def _run(argv):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = g.main(argv)
    return rc, buf.getvalue()


class PwaIconsTest(unittest.TestCase):
    def test_generated_module_matches_sources(self):
        rc, out = _run(["--check"])
        self.assertEqual(rc, 0, out)

    def test_icons_are_png_of_declared_size_on_the_theme(self):
        theme, icons = g.read_module()
        self.assertEqual(theme, g.read_colors()[0])                       # 地＝--paper＝ヘッダーの地
        self.assertEqual({(v["size"], v["purpose"]) for v in icons.values()},
                         {(192, "any"), (512, "any"), (512, "maskable"), (180, "apple")})
        for path, v in icons.items():
            with self.subTest(path=path):
                self.assertTrue(path.startswith("/app/icon-") and path.endswith(".png"))
                ihdr, plte, raw = g.decode(v["png"])
                w, h, depth, ctype = struct.unpack(">IIBB", ihdr[:10])
                self.assertEqual((w, h, depth, ctype), (v["size"], v["size"], 8, 3))   # 不透明のパレット PNG
                self.assertEqual("#" + plte[:3].hex(), theme)              # 添字 0 が地の色
                stride = w + 1
                for x, y in ((0, 0), (w - 1, 0), (0, h - 1), (w - 1, h - 1)):
                    self.assertEqual(raw[y * stride + 1 + x], 0)           # 四隅は地
                self.assertEqual(v["etag"], g.etag_of(v["png"]))

    def test_maskable_robot_is_inside_the_safe_zone(self):
        """maskable は中心からの半径 40% の円が安全域（端末が丸や角丸に切る）。ロボのドットが全部その中。"""
        _, icons = g.read_module()
        v = icons["/app/icon-512-maskable.png"]
        _, _, raw = g.decode(v["png"])
        n, c = v["size"], v["size"] / 2
        far = max(math.hypot(x + 0.5 - c, y + 0.5 - c)
                  for y in range(n) for x in range(n) if raw[y * (n + 1) + 1 + x] != 0)
        self.assertLessEqual(far, g.MASKABLE_SAFE * n)

    def test_scale_is_integer_and_uses_most_of_the_icon(self):
        """ドットがにじまない整数倍で、any・apple はロボの高さが一辺の 7 割以上（小さすぎる絵にしない）。"""
        rows = g.read_sprite()
        for _, size, purpose in g.ICONS:
            s = g.scale_for(size, purpose, len(rows[0]), len(rows))
            self.assertIsInstance(s, int)
            if purpose != "maskable":
                self.assertGreaterEqual(len(rows) * s, 0.7 * size, (size, purpose, s))

    def test_check_goes_red_when_pixels_drift(self):
        """変異: 収録した 192 の中身を 180 の PNG に差し替える → --check が赤（黙って古い絵を配らない）。"""
        src = g.OUT.read_text(encoding="utf-8")
        _, icons = g.read_module()
        b192 = re.search(r'"/app/icon-192\.png": \{.*?"b64": "([^"]+)"', src, re.S).group(1)
        b180 = re.search(r'"/app/icon-180\.png": \{.*?"b64": "([^"]+)"', src, re.S).group(1)
        self.assertTrue(icons)
        with tempfile.TemporaryDirectory() as td:
            bad = Path(td) / "pwa_icons.js"
            bad.write_text(src.replace(b192, b180, 1), encoding="utf-8")
            orig = g.OUT
            g.OUT = bad
            try:
                rc, out = _run(["--check"])
            finally:
                g.OUT = orig
        self.assertEqual(rc, 1)
        self.assertIn("icon-192.png", out)

    def test_check_goes_red_when_html_theme_drifts(self):
        """変異: app.html の暗の meta theme-color だけ別の色 → --check が赤（色を 2 つにしない）。"""
        html = g.APP_HTML.read_text(encoding="utf-8")
        dark = re.search(r'<meta name=theme-color media="\(prefers-color-scheme: dark\)" content="(#[0-9a-f]{6})">', html)
        self.assertIsNotNone(dark, "app.html に暗の meta theme-color（media 付き）が無い")
        with tempfile.TemporaryDirectory() as td:
            bad = Path(td) / "app.html"
            bad.write_text(html.replace(dark.group(0), dark.group(0).replace(dark.group(1), "#23213a")),
                           encoding="utf-8")
            orig = g.APP_HTML
            g.APP_HTML = bad
            try:
                rc, out = _run(["--check"])
            finally:
                g.APP_HTML = orig
        self.assertEqual(rc, 1)
        self.assertIn("theme-color", out)

    def test_worker_manifest_reads_the_generated_theme_and_app_scope(self):
        """worker.js のマニフェストは色を書き写さず PWA_THEME を読む。start_url・scope は SW の範囲 /app/・id は "/app"（統括の裁定）。
        名前は「AI Office」の 1 つ。SW は /app/sw.js（範囲 /app/）・通知のタップも /app/ へ。"""
        worker = (ROOT / "relay" / "src" / "worker.js").read_text(encoding="utf-8")
        m = re.search(r"const MANIFEST = JSON\.stringify\(\{(.*?)\n\}\);", worker, re.S)
        self.assertIsNotNone(m)
        man = m.group(1)
        self.assertIsNone(re.search(r"#[0-9a-fA-F]{3,6}\b", man), "マニフェストに色を直書きしている")
        self.assertNotIn("data:", man)
        for key in ("theme_color", "background_color"):
            self.assertRegex(man, key + r": PWA_THEME,")
        for key in ("start_url", "scope"):
            self.assertRegex(man, key + r': "/app/",')
        # id は "/app"（統括の裁定）: 旧マニフェスト（id 無し＝start_url の /app）で入れた Android の WebAPK と同じアプリのまま。
        # "/app/" にすると別アプリ扱い＝新しいアイコンと名前に更新されない。id は識別子で、範囲の中である必要は無い。
        self.assertRegex(man, r'\bid: "/app",')
        self.assertRegex(man, r'\bname: "AI Office",')
        self.assertRegex(man, r'short_name: "AI Office",')
        for must in ('import { PWA_ICONS, PWA_THEME } from "./pwa_icons.js";',
                     'if (method === "GET" && path === "/app") {',       # /app は 308 で /app/ へ
                     'Location: "/app/" + url.search',
                     'target = "/app/" +'):                              # 通知のタップも範囲の中へ
            self.assertTrue(must in worker, f"worker.js に {must!r} が無い")
        # 空の fetch ハンドラを戻さない（範囲 /app/ のページが全リクエストで SW を経由する）
        self.assertFalse('addEventListener("fetch"' in worker, "SW に fetch ハンドラが戻っている")
        app = (ROOT / "ui" / "pwa" / "app.js").read_text(encoding="utf-8")
        regs = re.findall(r'serviceWorker\.register\(([^)]*)\)', app)
        self.assertEqual(regs, ['"/app/sw.js"'])
        self.assertFalse('meta[name=theme-color]' in app, "app.js が theme-color を書き換えている（色の 3 つ目の写し）")
        html = g.APP_HTML.read_text(encoding="utf-8")
        self.assertIn('<title>AI Office</title>', html)
        self.assertIn('<meta name=apple-mobile-web-app-title content="AI Office">', html)


if __name__ == "__main__":
    unittest.main()
