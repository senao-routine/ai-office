#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""R99-P4: UI を配る HTTP サーバーの listen キュー（backlog）が、ブラウザの一斉接続で溢れないこと。

なぜ要るか（2026-09-26 の実測）:
  - macOS は accept 待ちの列（listen の backlog）が溢れると、次の接続に**即座に RST** を返す
    （ThreadingHTTPServer の既定 request_queue_size=5 で 40 本つなぐと 35 本が ConnectionResetError）。
  - ブラウザは UI の ES モジュール（約 100 本）を一斉に取りに来る。HTTP/1.0（1 要求 1 接続）なので接続も一斉に来る。
    負荷で accept のループが遅れると列が溢れ、1 本でも ERR_CONNECTION_RESET になると動的 import が落ちて
    起動画面が「Couldn't load the UI … Failed to fetch dynamically imported module」で止まる（再読み込みまで直らない）。
  - 製品の office_server（:4780）で、負荷の高い時にフィクスチャ HOME で 12 回開いて 3 回がリセット・2 回が起動失敗。
    列を 128 にすると同じ条件で 12/12・リセット 0。
  - tests/demo_site_smoke.py の「最初の起動待ちが 3 回に 1 回止まる」も同じ原因だった（master の版は列が 5 のまま。
    同じ木で列だけ 5→128 に変えると 4/8 → 8/8）。

見るもの: accept しない（＝一番遅れた）サーバーに 40 本つないで、1 本も落ちないこと。
"""
import json
import os
import socket
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tests"))
BURST = 40

PROBE = """
def burst(srv, n):
    import socket
    port = srv.server_address[1]
    ok, bad, socks = 0, 0, []
    try:
        for _ in range(n):
            c = socket.socket()
            c.settimeout(2)
            try:
                c.connect(("127.0.0.1", port))
                ok += 1
            except OSError:
                bad += 1
            socks.append(c)
    finally:
        for c in socks:
            c.close()
        srv.server_close()
    return ok, bad
"""
_ns = {}
exec(PROBE, _ns)                                     # noqa: S102 （子プロセスと同じ関数を使う）
burst = _ns["burst"]


class ListenBacklogTest(unittest.TestCase):
    def test_office_serverは一斉接続を落とさない(self):
        """製品の :4780。子プロセスで import する（モジュールの状態をほかのテストへ持ち込まない）"""
        with tempfile.TemporaryDirectory(prefix="office_backlog_") as home:
            code = (PROBE + "\nimport json, os, sys\n"
                    f"os.environ['OFFICE_HOME'] = {home!r}\n"
                    f"sys.path.insert(0, {str(ROOT / 'server')!r})\n"
                    "import office_server as o\n"
                    "srv = o._OfficeHTTPServer(('127.0.0.1', 0), o.Handler)\n"
                    f"print(json.dumps(dict(queue=srv.request_queue_size, r=burst(srv, {BURST}))))\n")
            r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=120,
                               env={**os.environ, "OFFICE_HOME": home})
            self.assertEqual(r.returncode, 0, r.stderr[-800:])
            got = json.loads(r.stdout.strip().splitlines()[-1])
            self.assertEqual(tuple(got["r"]), (BURST, 0),
                             f"office_server の listen キュー {got['queue']} が一斉接続で溢れる（落ちた接続＝ブラウザの "
                             "ERR_CONNECTION_RESET＝動的 import が落ちて UI が起動しない）")

    def test_公開デモのスモークの静的サーバーも落とさない(self):
        import demo_site_smoke
        srv = demo_site_smoke.StaticServer(("127.0.0.1", 0), demo_site_smoke.http.server.BaseHTTPRequestHandler)
        queue = srv.request_queue_size
        self.assertEqual(burst(srv, BURST), (BURST, 0),
                         f"demo_site_smoke の静的サーバーの listen キュー {queue} が一斉接続で溢れる"
                         "（最初の起動待ちが 60 秒止まるフレークの原因）")


if __name__ == "__main__":
    unittest.main()
