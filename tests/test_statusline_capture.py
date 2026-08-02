#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""R61: hooks/office-statusline-capture.sh の単体テスト。

statusLine payload（公式docs形: rate_limits.five_hour/seven_day の used_percentage・
resets_at）を stdin 注入し、~/.claude/office_usage/ への実測記録と
「絶対にブロックしない」掟（壊れ入力でも exit 0）・パススルー動作を機械検証する。
"""
import json
import os
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "hooks" / "office-statusline-capture.sh"

# 公式 statusline docs の形に忠実な payload（rate_limits は Pro/Max サブスクのみ・
# セッション初回API応答後に出現・各窓は独立に欠落しうる）
PAYLOAD = {
    "hook_event_name": "Status",
    "session_id": "sess-cap-0001",
    "model": {"id": "claude-fable-5", "display_name": "Fable 5"},
    "workspace": {"current_dir": "/tmp/x", "project_dir": "/tmp/x"},
    "rate_limits": {
        "five_hour": {"used_percentage": 43.0, "resets_at": 1754007200},
        "seven_day": {"used_percentage": 61.0, "resets_at": 1754259200},
    },
}


def run_capture(home, stdin_text):
    return subprocess.run(
        ["bash", str(SCRIPT)], input=stdin_text, capture_output=True, text=True,
        env={"OFFICE_HOME": str(home), "HOME": str(home),
             "PATH": "/usr/bin:/bin:/usr/sbin:/opt/homebrew/bin"})


class StatuslineCaptureTest(unittest.TestCase):
    def setUp(self):
        self.home = Path(tempfile.mkdtemp(prefix="slcap_"))
        self.usage = self.home / ".claude" / "office_usage"

    def test_writes_current_json_600_and_default_line(self):
        r = run_capture(self.home, json.dumps(PAYLOAD))
        self.assertEqual(r.returncode, 0, r.stderr)
        cur = self.usage / "current.json"
        self.assertTrue(cur.is_file())
        self.assertEqual(cur.stat().st_mode & 0o777, 0o600)
        d = json.loads(cur.read_text(encoding="utf-8"))
        self.assertEqual(d["rateLimits"], PAYLOAD["rate_limits"])   # verbatim保存
        self.assertEqual(d["model"], "Fable 5")
        self.assertIsInstance(d["capturedAt"], int)
        self.assertLess(abs(d["capturedAt"] - time.time()), 60)
        # 自前の1行（モデル+両窓%）
        self.assertIn("Fable 5", r.stdout)
        self.assertIn("5h 43%", r.stdout)
        self.assertIn("wk 61%", r.stdout)

    def test_account_identity_written_when_available(self):
        (self.home / ".claude.json").write_text(json.dumps({
            "oauthAccount": {"accountUuid": "abcdef123456xxxx",
                             "emailAddress": "main@example.com"}}), encoding="utf-8")
        r = run_capture(self.home, json.dumps(PAYLOAD))
        self.assertEqual(r.returncode, 0, r.stderr)
        d = json.loads((self.usage / "current.json").read_text(encoding="utf-8"))
        self.assertEqual(d["account"], {"id": "abcdef123456xxxx",
                                        "email": "main@example.com"})
        acct = self.usage / "acct-abcdef123456.json"
        self.assertTrue(acct.is_file())
        self.assertEqual(acct.stat().st_mode & 0o777, 0o600)

    def test_missing_rate_limits_keeps_existing_record(self):
        # 先に有効な記録を作る
        run_capture(self.home, json.dumps(PAYLOAD))
        before = (self.usage / "current.json").read_text(encoding="utf-8")
        # rate_limits の無い payload（サブスク外/初回前）→ 既存を消さない・更新しない
        p2 = {k: v for k, v in PAYLOAD.items() if k != "rate_limits"}
        r = run_capture(self.home, json.dumps(p2))
        self.assertEqual(r.returncode, 0)
        self.assertEqual((self.usage / "current.json").read_text(encoding="utf-8"), before)
        self.assertIn("Fable 5", r.stdout)   # モデル名だけの1行は出す

    def test_passthrough_receives_same_stdin_and_owns_stdout(self):
        self.usage.mkdir(parents=True)
        child_dump = self.home / "child_stdin.json"
        (self.usage / "passthrough.cmd").write_text(
            f"cat > '{child_dump}'; echo CHILD-LINE", encoding="utf-8")
        r = run_capture(self.home, json.dumps(PAYLOAD))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.strip(), "CHILD-LINE")            # 表示は子が所有
        self.assertEqual(json.loads(child_dump.read_text(encoding="utf-8"))["session_id"],
                         "sess-cap-0001")                            # 同じstdinが届く
        self.assertTrue((self.usage / "current.json").is_file())     # 記録も両立

    def test_broken_stdin_never_blocks(self):
        for bad in ("not json at all", "", "{trunc"):
            r = run_capture(self.home, bad)
            self.assertEqual(r.returncode, 0, f"exit!=0 for {bad!r}: {r.stderr}")
            self.assertTrue(r.stdout.strip())                        # 何かは必ず出す
        self.assertFalse((self.usage / "current.json").exists())


if __name__ == "__main__":
    unittest.main()
