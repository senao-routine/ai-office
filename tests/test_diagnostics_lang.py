#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""plan.md #5: `/api/diagnostics` の本文が UI の表示言語を無視し、常に _LANG（サーバーの設定言語）
固定で生成されていた（デモの既定=英語でも本文だけ日本語など）。クライアントが今見ている言語を
`?lang=` で渡し、サーバーはそれを使う（不正値・無指定は既定の _LANG へ）。

HTTP 経由（実サーバー・:4811）で確かめる: office_diag.build() 自体は既に lang を受け取れたので、
穴は office_server.py のエンドポイント側だけ＝ユニットではなく実リクエストで確かめないと検出できない。
"""
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PORT = 4811


def _get(path):
    req = urllib.request.Request(f"http://127.0.0.1:{PORT}{path}", headers={"X-Office-Local": "1"})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


class DiagnosticsLangTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.home = Path(tempfile.mkdtemp(prefix="office_diag_lang_"))
        (cls.home / ".claude").mkdir(parents=True)
        (cls.home / "office_config.json").write_text(json.dumps({"projects": {}, "lang": "ja"}),
                                                      encoding="utf-8")
        env = dict(os.environ, OFFICE_HOME=str(cls.home),
                   OFFICE_CONFIG=str(cls.home / "office_config.json"))
        env.pop("OFFICE_LANG", None)                          # config の "ja" が効くことを確かめる
        cls.log = open(cls.home / "daemon.log", "w", encoding="utf-8")
        cls.proc = subprocess.Popen([sys.executable, str(ROOT / "server/office_server.py"),
                                     "--port", str(PORT)], env=env,
                                    stdout=cls.log, stderr=subprocess.STDOUT)
        for _ in range(100):
            try:
                if _get("/api/office")[0] == 200:
                    break
            except OSError:
                pass
            time.sleep(0.1)
        else:
            raise RuntimeError("test server did not come up")

    @classmethod
    def tearDownClass(cls):
        cls.proc.terminate()
        try:
            cls.proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            cls.proc.kill()
        cls.log.close()
        import shutil
        shutil.rmtree(cls.home, ignore_errors=True)

    def test_lang_query_param_picks_the_clients_display_language(self):
        status, body = _get("/api/diagnostics?lang=en")
        self.assertEqual(status, 200)
        self.assertIn("AI Office diagnostics", body["text"])
        self.assertNotIn("AI Office 診断情報", body["text"])

    def test_server_default_is_used_when_lang_is_missing_or_invalid(self):
        # config の lang="ja" が既定（env OFFICE_LANG は setUpClass で外した）。
        for qs in ("", "?lang=", "?lang=fr", "?lang=<script>"):
            status, body = _get(f"/api/diagnostics{qs}")
            self.assertEqual(status, 200, qs)
            self.assertIn("AI Office 診断情報", body["text"], qs)

    def test_ja_query_param_is_honored_too(self):
        status, body = _get("/api/diagnostics?lang=ja")
        self.assertEqual(status, 200)
        self.assertIn("AI Office 診断情報", body["text"])


if __name__ == "__main__":
    unittest.main()


class DiagnosticsDefaultLangFirstRequestTest(unittest.TestCase):
    """別モデルレビュー（2026-10-04・low）: 英語設定のサーバーで**最初の要求**が /api/diagnostics（lang 無し）だと、
    office_json() が設定を読む前の _LANG（初期値 ja）で既定を決めて日本語の診断になった。設定を読んでから決める。
    最初の要求であることが肝＝起動の待ちは /api/office を叩かずにソケットの接続だけで見る。"""
    PORT2 = 4814

    @classmethod
    def setUpClass(cls):
        import socket
        cls.home = Path(tempfile.mkdtemp(prefix="office_diag_lang_en_"))
        (cls.home / ".claude").mkdir(parents=True)
        (cls.home / "office_config.json").write_text(json.dumps({"projects": {}, "lang": "en"}), encoding="utf-8")
        env = dict(os.environ, OFFICE_HOME=str(cls.home), OFFICE_CONFIG=str(cls.home / "office_config.json"))
        env.pop("OFFICE_LANG", None)
        cls.log = open(cls.home / "daemon.log", "w", encoding="utf-8")
        cls.proc = subprocess.Popen([sys.executable, str(ROOT / "server/office_server.py"), "--port", str(cls.PORT2)],
                                    env=env, stdout=cls.log, stderr=subprocess.STDOUT)
        for _ in range(100):
            try:
                with socket.create_connection(("127.0.0.1", cls.PORT2), timeout=0.2):
                    break
            except OSError:
                time.sleep(0.1)
        else:
            raise RuntimeError("test server did not come up")

    @classmethod
    def tearDownClass(cls):
        cls.proc.terminate()
        try:
            cls.proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            cls.proc.kill()
        cls.log.close()
        import shutil
        shutil.rmtree(cls.home, ignore_errors=True)

    def test_first_request_without_lang_uses_the_configured_language(self):
        req = urllib.request.Request(f"http://127.0.0.1:{self.PORT2}/api/diagnostics", headers={"X-Office-Local": "1"})
        with urllib.request.urlopen(req, timeout=10) as r:
            body = json.loads(r.read())
        self.assertIn("AI Office diagnostics", body["text"])
        self.assertNotIn("AI Office 診断情報", body["text"])
