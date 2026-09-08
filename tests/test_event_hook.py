#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""R90-D4: hooks/office-event.sh（hook イベントの記録専用 hook）の番人。

守るもの:
  1. 本文を1バイトも書かない（ファイルパス・コマンド本文・tool_response・prompt 本文が行に存在しない）
  2. どんな入力・環境でも無出力 exit 0（壊れJSON・sid 不正・書けない dir）
  3. 1行 ≤1500 バイト・dir 0700 / file 0600・追記（複数イベントが同じ日付ファイルに並ぶ）
  4. 軽い（1発火 <150ms が受入条件。フレーク回避のためテストは 1.0s で判定し実測を表示）
"""
import json
import os
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HOOK = ROOT / "hooks" / "office-event.sh"


def run_hook(home, payload):
    raw = payload if isinstance(payload, (str, bytes)) else json.dumps(payload, ensure_ascii=False)
    t0 = time.time()
    r = subprocess.run(["bash", str(HOOK)], input=raw, capture_output=True, text=True,
                       env={"OFFICE_HOME": str(home), "HOME": str(home),
                            "PATH": "/usr/bin:/bin:/usr/sbin:/opt/homebrew/bin"})
    return r, time.time() - t0


def lines(home):
    d = Path(home) / ".claude" / "office_events"
    out = []
    for p in sorted(d.glob("*.jsonl")) if d.is_dir() else []:
        out.extend(p.read_text(encoding="utf-8").splitlines())
    return out


class EventHookTest(unittest.TestCase):
    def setUp(self):
        self.home = Path(tempfile.mkdtemp(prefix="office_event_"))

    def test_edit_records_basename_only(self):
        r, sec = run_hook(self.home, {
            "session_id": "sess-evt-0001", "hook_event_name": "PostToolUse", "cwd": "/Users/x/secret-project",
            "tool_name": "Edit", "tool_input": {"file_path": "/Users/x/secret-project/docs/report.md",
                                                "old_string": "PASSWORD=hunter2", "new_string": "x"},
            "tool_response": {"content": "very secret content"},
            "transcript_path": "/Users/x/.claude/projects/p/s.jsonl"})
        self.assertEqual((r.returncode, r.stdout, r.stderr), (0, "", ""))
        ls = lines(self.home)
        self.assertEqual(len(ls), 1)
        rec = json.loads(ls[0])
        self.assertEqual((rec["v"], rec["ev"], rec["sid"], rec["tool"], rec["tgt"]),
                         (1, "PostToolUse", "sess-evt-0001", "Edit", "report.md"))
        self.assertEqual(len(rec["cwdh"]), 12)
        for secret in ("secret-project", "hunter2", "very secret", "transcript", "/Users/x"):
            self.assertNotIn(secret, ls[0])
        self.assertLess(sec, 1.0, f"hook が遅い: {sec:.3f}s")
        print(f"\n  event hook 1発火 {sec * 1000:.0f}ms")

    def test_bash_git_commit_is_classified_without_command_text(self):
        r, _ = run_hook(self.home, {
            "session_id": "sess-evt-0002", "hook_event_name": "PostToolUse", "cwd": "/w",
            "tool_name": "Bash", "tool_input": {"command": "cd /w && git commit -m 'add secret token abc123'"}})
        self.assertEqual(r.returncode, 0)
        rec = json.loads(lines(self.home)[0])
        self.assertEqual((rec["tool"], rec["kind"], rec["tgt"]), ("Bash", "git:commit", ""))
        self.assertNotIn("abc123", lines(self.home)[0])
        self.assertNotIn("commit -m", lines(self.home)[0])
        run_hook(self.home, {"session_id": "sess-evt-0002", "hook_event_name": "PostToolUse", "cwd": "/w",
                             "tool_name": "Bash", "tool_input": {"command": "ls -la"}})
        self.assertEqual(json.loads(lines(self.home)[1])["kind"], "")

    def test_prompt_records_length_only(self):
        run_hook(self.home, {"session_id": "sess-evt-0003", "hook_event_name": "UserPromptSubmit",
                             "cwd": "/w", "prompt_text": "この本文は絶対に記録しない " * 3})
        rec = json.loads(lines(self.home)[0])
        self.assertGreater(rec["plen"], 0)
        self.assertNotIn("記録しない", lines(self.home)[0])

    def test_task_and_subagent_and_notification_fields(self):
        run_hook(self.home, {"session_id": "sess-evt-0004", "hook_event_name": "TaskCreated", "cwd": "/w",
                             "task_id": "t1", "task_input": {"subject": "x" * 200, "description": "long"}})
        run_hook(self.home, {"session_id": "sess-evt-0004", "hook_event_name": "SubagentStart", "cwd": "/w",
                             "agent_id": "a-1", "agent_type": "Explore"})
        run_hook(self.home, {"session_id": "sess-evt-0004", "hook_event_name": "Notification", "cwd": "/w",
                             "notification_type": "permission_prompt", "message": "Claude needs your permission"})
        run_hook(self.home, {"session_id": "sess-evt-0004", "hook_event_name": "PostToolUseFailure", "cwd": "/w",
                             "tool_name": "Bash", "tool_input": {"command": "false"}, "error": "boom"})
        ls = [json.loads(l) for l in lines(self.home)]
        self.assertEqual(ls[0]["task"], "t1")
        self.assertNotIn("tsub", ls[0])                      # 件名は自由記述＝記録しない（レビュー指摘）
        self.assertNotIn("xxxxx", lines(self.home)[0])
        self.assertEqual(ls[1]["sub"], "a-1")
        self.assertEqual(ls[2]["nt"], "permission_prompt")
        self.assertNotIn("needs your permission", lines(self.home)[2])
        self.assertFalse(ls[3]["ok"])
        for l in lines(self.home):
            self.assertLessEqual(len(l.encode("utf-8")), 1500)

    def test_bad_inputs_are_silent(self):
        for payload in ("", "{not json", json.dumps({"hook_event_name": "Stop"}),
                        json.dumps({"session_id": "../etc", "hook_event_name": "Stop"}),
                        json.dumps({"session_id": "ok-sid", "hook_event_name": "bad name"}),
                        json.dumps([1, 2, 3])):
            r, _ = run_hook(self.home, payload)
            self.assertEqual((r.returncode, r.stdout, r.stderr), (0, "", ""), payload)
        self.assertEqual(lines(self.home), [])
        self.assertFalse((self.home / ".claude" / "office_events").exists())

    def test_unwritable_dir_is_silent(self):
        base = self.home / ".claude"
        base.mkdir()
        os.chmod(base, 0o500)
        try:
            r, _ = run_hook(self.home, {"session_id": "sess-evt-0005", "hook_event_name": "Stop", "cwd": "/w"})
            self.assertEqual((r.returncode, r.stdout, r.stderr), (0, "", ""))
        finally:
            os.chmod(base, 0o700)

    def test_permissions_and_append(self):
        for i in range(3):
            run_hook(self.home, {"session_id": "sess-evt-0006", "hook_event_name": "Stop", "cwd": "/w"})
        d = self.home / ".claude" / "office_events"
        self.assertEqual(stat.S_IMODE(d.stat().st_mode), 0o700)
        files = list(d.glob("*.jsonl"))
        self.assertEqual(len(files), 1)
        self.assertEqual(stat.S_IMODE(files[0].stat().st_mode), 0o600)
        self.assertEqual(len(lines(self.home)), 3)

    def test_free_text_reason_is_not_recorded(self):
        run_hook(self.home, {"session_id": "sess-evt-0008", "hook_event_name": "SessionEnd", "cwd": "/w",
                             "reason": "/Users/example/private token=abc"})
        run_hook(self.home, {"session_id": "sess-evt-0008", "hook_event_name": "SessionStart", "cwd": "/w",
                             "source": "resume"})
        ls = lines(self.home)
        self.assertEqual(json.loads(ls[0])["src"], "")
        self.assertNotIn("private", ls[0])
        self.assertEqual(json.loads(ls[1])["src"], "resume")

    def test_huge_tool_response_still_records(self):
        payload = {"session_id": "sess-evt-0009", "hook_event_name": "PostToolUse", "cwd": "/w",
                   "tool_name": "Read", "tool_input": {"file_path": "/w/big.txt"},
                   "tool_response": {"content": "x" * 2_000_000}}
        r, sec = run_hook(self.home, payload)
        self.assertEqual((r.returncode, r.stdout, r.stderr), (0, "", ""))
        rec = json.loads(lines(self.home)[0])
        self.assertEqual((rec["tool"], rec["tgt"]), ("Read", "big.txt"))
        self.assertLess(sec, 2.0)

    def test_oversized_line_drops_optional_fields_before_giving_up(self):
        run_hook(self.home, {"session_id": "sess-evt-0007", "hook_event_name": "Skill", "cwd": "/w",
                             "tool_name": "Skill", "tool_input": {"skill": "s" * 5000}})
        ls = lines(self.home)
        self.assertEqual(len(ls), 1)
        self.assertLessEqual(len(ls[0].encode("utf-8")), 1500)


if __name__ == "__main__":
    unittest.main()
