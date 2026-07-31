# -*- coding: utf-8 -*-
"""指示ポスト（投函API）と履歴のテスト"""
import importlib.util
import json
import os
import tempfile
import unittest
from pathlib import Path

TESTS = Path(__file__).resolve().parent
ROOT = TESTS.parent

_home = tempfile.mkdtemp(prefix="office_inbox_home_")
os.environ["OFFICE_HOME"] = _home
spec = importlib.util.spec_from_file_location(
    "office_server_inbox", ROOT / "server" / "office_server.py")
office = importlib.util.module_from_spec(spec)
spec.loader.exec_module(office)


class InboxTest(unittest.TestCase):
    def test_invalid_session_rejected(self):
        ok, msg = office.post_instruction("../evil", "x")
        self.assertFalse(ok)
        ok, _ = office.post_instruction("short", "x")
        self.assertFalse(ok)

    def test_empty_and_too_long_rejected(self):
        ok, _ = office.post_instruction("valid-session-0001", "   ")
        self.assertFalse(ok)
        ok, _ = office.post_instruction("valid-session-0001", "あ" * 4001)
        self.assertFalse(ok)

    def test_post_creates_file_and_history(self):
        ok, _ = office.post_instruction("valid-session-0001", "テスト指示")
        self.assertTrue(ok)
        f = office.INBOX / "valid-session-0001.json"
        self.assertTrue(f.exists())
        self.assertEqual(json.loads(f.read_text())["text"], "テスト指示")
        hist = json.loads(office.HISTORY_FILE.read_text())
        self.assertEqual(hist[-1]["session"], "valid-session-0001")
        f.unlink()

    def test_history_trimmed_to_50(self):
        for i in range(60):
            office.post_instruction(f"trim-session-{i:04d}", f"指示{i}")
        hist = json.loads(office.HISTORY_FILE.read_text())
        self.assertLessEqual(len(hist), 50)


if __name__ == "__main__":
    unittest.main()
