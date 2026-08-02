#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""R53: ロボ→実ターミナルジャンプの単体テスト。

osascript/psは実行しない: FAKE注入（OFFICE_FAKE_FOCUS）とプロセス対応付けの
純関数（_match_proc）だけを検証する。実フォーカスは手動E2E（Automation TCC配下）。
"""
import importlib.util
import json
import os
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

spec = importlib.util.spec_from_file_location("office_focus_t", ROOT / "server" / "office_server.py")
office = importlib.util.module_from_spec(spec)
spec.loader.exec_module(office)


class MatchProcTest(unittest.TestCase):
    """cwd一致＋起動時刻の近さでプロセスを選ぶヒューリスティック（純関数）。"""

    PROCS = [
        {"pid": "100", "tty": "ttys000", "started": 1000.0, "cwd": "/Users/x/works"},
        {"pid": "200", "tty": "ttys001", "started": 5000.0, "cwd": "/Users/x/works"},
        {"pid": "300", "tty": "ttys002", "started": 3000.0, "cwd": "/Users/x/other"},
    ]

    def test_matches_by_cwd_and_closest_start(self):
        p = office._match_proc("/Users/x/works", 4800.0, self.PROCS)
        self.assertEqual(p["pid"], "200")
        p = office._match_proc("/Users/x/works", 1100.0, self.PROCS)
        self.assertEqual(p["pid"], "100")

    def test_without_first_ts_picks_newest(self):
        # --resume 等で時刻が使えないときは同cwdの最新プロセスへ縮退
        p = office._match_proc("/Users/x/works", None, self.PROCS)
        self.assertEqual(p["pid"], "200")

    def test_no_cwd_match_returns_none(self):
        self.assertIsNone(office._match_proc("/Users/x/nowhere", 1000.0, self.PROCS))
        self.assertIsNone(office._match_proc("", 1000.0, self.PROCS))
        self.assertIsNone(office._match_proc("/Users/x/works", 1000.0, []))

    def test_nfc_normalized_cwd_comparison(self):
        # macOSのNFD濁点パスでも一致する（パス比較はnfc揃え）
        import unicodedata
        nfd = unicodedata.normalize("NFD", "/Users/x/データ")
        self.assertNotEqual(nfd, unicodedata.normalize("NFC", nfd))  # 前提: 実際に別バイト列
        procs = [{"pid": "9", "tty": "ttys009", "started": 1.0, "cwd": nfd}]
        p = office._match_proc(unicodedata.normalize("NFC", "/Users/x/データ"), None, procs)
        self.assertIsNotNone(p)


class FakeFocusTest(unittest.TestCase):
    def test_fake_focus_writes_marker_and_skips_osascript(self):
        with tempfile.TemporaryDirectory() as tmp:
            marker = Path(tmp) / "focus.marker"
            os.environ["OFFICE_FAKE_FOCUS"] = str(marker)
            try:
                ok, msg = office.focus_terminal("sess-fixture-0001")
            finally:
                del os.environ["OFFICE_FAKE_FOCUS"]
            self.assertTrue(ok)
            self.assertEqual(json.loads(marker.read_text(encoding="utf-8"))["session"],
                             "sess-fixture-0001")


if __name__ == "__main__":
    unittest.main()
