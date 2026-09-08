#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""R90-D10: post_instruction の cx-（Codex CLI）分岐＝`codex queue --thread <id> --message=<text>` 配達の番人。

守るもの:
  1. argv は固定形（shell=False）。本文は `--message=` の結合形＝先頭が `-` でもオプションに化けない
  2. cx- 宛は office_inbox にファイルを**作らない**（Stop hook 経路は Claude 専用）
  3. rc≠0 / タイムアウト / codex 不在 は正直に False（「あとで届く」ふりをしない）
  4. OFFICE_FAKE_CODEX（テスト注入口）でマーカーに thread/text が残り、履歴に記録される
"""
import importlib.util
import json
import os
import stat
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

_home = Path(tempfile.mkdtemp(prefix="office_delivery_home_"))
os.environ["OFFICE_HOME"] = str(_home)
os.environ["OFFICE_CODEX_QUEUE_TIMEOUT"] = "1"
spec = importlib.util.spec_from_file_location("office_server_delivery", ROOT / "server" / "office_server.py")
office = importlib.util.module_from_spec(spec)
spec.loader.exec_module(office)


def fake_codex(body):
    """偽の codex 実行ファイルを作って OFFICE_CODEX_BIN に差す。"""
    d = Path(tempfile.mkdtemp(prefix="fake_codex_"))
    p = d / "codex"
    p.write_text("#!/bin/bash\n" + body + "\n", encoding="utf-8")
    p.chmod(p.stat().st_mode | stat.S_IXUSR)
    os.environ["OFFICE_CODEX_BIN"] = str(p)
    return d


class CodexDeliveryTest(unittest.TestCase):
    def setUp(self):
        for k in ("OFFICE_FAKE_CODEX", "OFFICE_CODEX_BIN"):
            os.environ.pop(k, None)
        for f in office.INBOX.glob("*.json"):
            f.unlink()

    def test_fake_marker_records_thread_and_text_without_inbox(self):
        marker = _home / "codex_queue.marker"
        os.environ["OFFICE_FAKE_CODEX"] = str(marker)
        ok, msg = office.post_instruction("cx-parent-a", "  テストを直して  ")
        self.assertTrue(ok, msg)
        rec = json.loads(marker.read_text(encoding="utf-8"))
        self.assertEqual(rec, {"thread": "parent-a", "text": "テストを直して"})
        self.assertEqual(list(office.INBOX.glob("cx-*.json")), [])      # inbox には書かない
        hist = json.loads(office.HISTORY_FILE.read_text(encoding="utf-8"))
        self.assertTrue(any(h.get("session") == "cx-parent-a" for h in (hist if isinstance(hist, list) else hist.get("items", []))))

    def test_argv_is_fixed_form_and_message_is_joined(self):
        d = fake_codex('printf "%s\\n" "$@" > "$(dirname "$0")/argv.txt"; exit 0')
        ok, _ = office.post_instruction("cx-thread-0001", "-rf / --danger")
        self.assertTrue(ok)
        argv = (d / "argv.txt").read_text(encoding="utf-8").splitlines()
        self.assertEqual(argv, ["queue", "--thread", "thread-0001", "--message=-rf / --danger"])
        self.assertEqual(list(office.INBOX.glob("cx-*.json")), [])

    def test_nonzero_exit_is_honest_failure(self):
        fake_codex("echo 'session not found' >&2; exit 1")
        ok, msg = office.post_instruction("cx-thread-0002", "hello")
        self.assertFalse(ok)
        self.assertIn("Codex", msg)
        self.assertEqual(list(office.INBOX.glob("cx-*.json")), [])

    def test_timeout_raises_transient_oserror_and_kills_grandchild(self):
        """タイムアウト＝一時障害。relay の「OSError は残置して再試行」に乗せるため例外で返す。
        npm ラッパーが spawn する実体（孫）もプロセスグループごと止める。"""
        d = fake_codex('bash -c "sleep 30" & echo $! > "$(dirname "$0")/child.pid"; sleep 30')
        with self.assertRaises(OSError) as cm:
            office.post_instruction("cx-thread-0003", "hello")
        self.assertIsInstance(cm.exception, office.CodexUnavailable)
        pid = int((d / "child.pid").read_text().strip())
        time.sleep(0.3)
        with self.assertRaises(ProcessLookupError):
            os.kill(pid, 0)
        self.assertEqual(list(office.INBOX.glob("cx-*.json")), [])

    def test_nul_in_text_is_rejected(self):
        os.environ["OFFICE_FAKE_CODEX"] = str(_home / "nul.marker")
        ok, msg = office.post_instruction("cx-thread-0006", "hello\x00world")
        self.assertFalse(ok)
        self.assertFalse((_home / "nul.marker").exists())

    def test_missing_codex_binary(self):
        os.environ["OFFICE_CODEX_BIN"] = str(_home / "no-such-codex")
        ok, msg = office.post_instruction("cx-thread-0004", "hello")
        self.assertFalse(ok)
        self.assertIn("codex", msg.lower())

    def test_validation_still_applies(self):
        os.environ["OFFICE_FAKE_CODEX"] = str(_home / "m.json")
        self.assertFalse(office.post_instruction("cx-a", "x")[0])            # 短すぎ（既存の 8..64 規則）
        self.assertFalse(office.post_instruction("cx-thread-0005", "   ")[0])  # 空
        self.assertFalse(office.post_instruction("cx-thread-0005", "x" * 4001)[0])
        self.assertFalse(office.post_instruction("cx-bad id", "x")[0])

    def test_claude_session_path_is_unchanged(self):
        os.environ["OFFICE_FAKE_CODEX"] = str(_home / "m2.json")
        ok, _ = office.post_instruction("sess-plain-0001", "hello")
        self.assertTrue(ok)
        self.assertTrue((office.INBOX / "sess-plain-0001.json").exists())   # Claude 宛は従来どおり inbox
        self.assertFalse((_home / "m2.json").exists())


if __name__ == "__main__":
    unittest.main()
