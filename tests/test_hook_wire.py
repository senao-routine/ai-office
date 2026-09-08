#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""R50提案2d: hooks/install.sh --wire の自動配線テスト（隔離HOME・冪等・既存hooks温存）。

グローバル ~/.claude/settings.json への書込は他プロジェクトのhookを壊すと事故が大きいので、
バックアップ作成・冪等（2回実行で重複しない）・既存Stop hookの温存を機械でピンする。
"""
import json
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "hooks" / "install.sh"


def run_install(home, *args):
    return subprocess.run(
        ["bash", str(SCRIPT), *args], capture_output=True, text=True,
        env={"HOME": str(home), "PATH": "/usr/bin:/bin:/usr/sbin:/opt/homebrew/bin"})


class HookWireTest(unittest.TestCase):
    def setUp(self):
        self.home = Path(tempfile.mkdtemp(prefix="hookwire_"))
        self.settings = self.home / ".claude" / "settings.json"

    def _stops(self):
        return json.loads(self.settings.read_text(encoding="utf-8"))["hooks"]["Stop"]

    def _wired_count(self):
        return sum(1 for grp in self._stops()
                   for h in grp.get("hooks", [])
                   if "office-inbox-wait" in h.get("command", ""))

    def _inbox_hook(self):
        """R90-D5 以降、Stop にはイベント記録 group も並ぶので並び順でなくコマンド名で引く。"""
        return next(h for grp in self._stops() for h in grp.get("hooks", [])
                    if "office-inbox-wait" in h.get("command", ""))

    def test_default_prints_snippet_without_writing(self):
        r = run_install(self.home)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("--wire", r.stdout)                 # 自動配線の案内
        self.assertIn("office-inbox-wait.sh", r.stdout)   # 手動スニペット
        self.assertFalse(self.settings.exists())          # 既定では書かない
        self.assertTrue((self.home / ".claude" / "hooks" / "office-inbox-wait.sh").exists())

    def test_wire_creates_settings_and_is_idempotent(self):
        r1 = run_install(self.home, "--wire")
        self.assertEqual(r1.returncode, 0, r1.stderr)
        self.assertEqual(self._wired_count(), 1)
        hook = self._inbox_hook()
        self.assertTrue(hook["asyncRewake"])
        # R86-D 不変条件: timeout は待機ループ（LOOPS×INTERVAL）より必ず長い。
        # 逆転すると Claude Code が hook を kill して**出力を破棄**するため、
        # 「inboxを読んで消したが渡せない」窓が生まれて指示が恒久ロストする。
        script = (ROOT / "hooks" / "office-inbox-wait.sh").read_text(encoding="utf-8")
        loops = int(re.search(r"OFFICE_WAIT_LOOPS:-(\d+)", script).group(1))
        interval = int(re.search(r"OFFICE_WAIT_INTERVAL:-(\d+)", script).group(1))
        self.assertGreater(hook["timeout"], loops * interval,
                           "hook timeout <= 待機ループ長＝指示ロストの窓ができる")
        self.assertGreaterEqual(loops * interval, 12 * 3600,
                                "受信待機が12時間を下回った（アイドル中に届かなくなる）")
        r2 = run_install(self.home, "--wire")             # 2回目=冪等
        self.assertIn("配線を確認", r2.stdout)
        self.assertEqual(self._wired_count(), 1)

    def test_wire_repairs_short_timeout(self):
        """R86-D: 旧 timeout(7300=2時間)のまま配線済みの環境を、再実行で自己修復する。"""
        self.settings.parent.mkdir(parents=True)
        old = {"hooks": {"Stop": [{"hooks": [{
            "type": "command",
            "command": 'bash "$HOME/.claude/hooks/office-inbox-wait.sh"',
            "timeout": 7300, "asyncRewake": True}]}]}}
        self.settings.write_text(json.dumps(old), encoding="utf-8")
        r = run_install(self.home, "--wire")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self._wired_count(), 1, "重複配線してはいけない")
        hook = self._inbox_hook()
        self.assertGreater(hook["timeout"], 12 * 3600)
        self.assertIn("timeout", r.stdout)

    def test_wire_preserves_existing_hooks_and_backs_up(self):
        self.settings.parent.mkdir(parents=True)
        existing = {"model": "opusplan",
                    "hooks": {"Stop": [{"hooks": [{"type": "command",
                                                   "command": "echo other-hook"}]}]}}
        self.settings.write_text(json.dumps(existing), encoding="utf-8")
        r = run_install(self.home, "--wire")
        self.assertEqual(r.returncode, 0, r.stderr)
        data = json.loads(self.settings.read_text(encoding="utf-8"))
        self.assertEqual(data["model"], "opusplan")       # 他キー温存
        cmds = [h["command"] for grp in data["hooks"]["Stop"] for h in grp["hooks"]]
        self.assertIn("echo other-hook", cmds)            # 既存hook温存
        self.assertEqual(self._wired_count(), 1)
        backups = list(self.settings.parent.glob("settings.json.bak-*"))
        self.assertEqual(len(backups), 1)                 # バックアップ作成

    def test_broken_settings_refuses_to_write(self):
        self.settings.parent.mkdir(parents=True)
        self.settings.write_text("{broken", encoding="utf-8")
        r = run_install(self.home, "--wire")
        self.assertNotEqual(r.returncode, 0)
        self.assertEqual(self.settings.read_text(encoding="utf-8"), "{broken")  # 壊れた正本に触らない


class EventWireTest(unittest.TestCase):
    """R90-D5: イベント記録 hook（office-event.sh）を 17 イベントへ async で配線する。
    既存 group（他プロジェクトの hook・inbox-wait）には触らず、2回実行で重複しない。"""

    EVENTS = ["SessionStart", "UserPromptSubmit", "PreToolUse", "PostToolUse", "PostToolUseFailure",
              "PermissionRequest", "PermissionDenied", "Stop", "StopFailure", "SubagentStart",
              "SubagentStop", "TaskCreated", "TaskCompleted", "Notification", "SessionEnd",
              "PreCompact", "PostCompact"]

    def setUp(self):
        self.home = Path(tempfile.mkdtemp(prefix="evwire_"))
        self.settings = self.home / ".claude" / "settings.json"

    def _hooks(self):
        return json.loads(self.settings.read_text(encoding="utf-8"))["hooks"]

    def _event_entries(self, ev):
        return [h for grp in self._hooks().get(ev, []) for h in grp.get("hooks", [])
                if h.get("command") == 'bash "$HOME/.claude/hooks/office-event.sh"']

    def test_wire_all_events_async_and_idempotent(self):
        r1 = run_install(self.home, "--wire")
        self.assertEqual(r1.returncode, 0, r1.stderr)
        self.assertTrue((self.home / ".claude" / "hooks" / "office-event.sh").exists())
        for ev in self.EVENTS:
            entries = self._event_entries(ev)
            self.assertEqual(len(entries), 1, ev)
            self.assertIs(entries[0]["async"], True, ev)
            self.assertLessEqual(entries[0]["timeout"], 30, ev)
        # Stop は inbox-wait と別 group（asyncRewake 判定に干渉しない）
        stop_groups = self._hooks()["Stop"]
        inbox = [g for g in stop_groups if any("office-inbox-wait" in h["command"] for h in g["hooks"])]
        events = [g for g in stop_groups if any("office-event.sh" in h["command"] for h in g["hooks"])]
        self.assertTrue(inbox and events and inbox[0] is not events[0])
        self.assertFalse(any("office-event.sh" in h["command"] for h in inbox[0]["hooks"]))
        r2 = run_install(self.home, "--wire")
        self.assertIn("配線を確認", r2.stdout)
        for ev in self.EVENTS:
            self.assertEqual(len(self._event_entries(ev)), 1, ev)

    def test_wire_preserves_existing_groups_and_heals_async(self):
        self.settings.parent.mkdir(parents=True)
        existing = {"hooks": {
            "Stop": [{"hooks": [{"type": "command", "command": "bash other-stop.sh", "timeout": 20}]}],
            "PostToolUse": [{"matcher": "Edit|Write", "hooks": [{"type": "command", "command": "bash quick.sh"}]},
                            {"hooks": [{"type": "command", "command": 'bash "$HOME/.claude/hooks/office-event.sh"',
                                        "timeout": 10}]}]}}
        self.settings.write_text(json.dumps(existing), encoding="utf-8")
        r = run_install(self.home, "--wire")
        self.assertEqual(r.returncode, 0, r.stderr)
        hooks = self._hooks()
        self.assertEqual(hooks["PostToolUse"][0]["matcher"], "Edit|Write")            # 既存 group 無改変
        self.assertEqual(hooks["PostToolUse"][0]["hooks"][0]["command"], "bash quick.sh")
        self.assertEqual(len(self._event_entries("PostToolUse")), 1)                  # 既配線は重複しない
        self.assertIs(self._event_entries("PostToolUse")[0]["async"], True)           # async 欠落を自己修復
        self.assertIn("bash other-stop.sh", [h["command"] for g in hooks["Stop"] for h in g["hooks"]])
        self.assertEqual(len(self._event_entries("Stop")), 1)

    def test_other_projects_same_named_hook_is_left_alone(self):
        """他プロジェクトの office-event.sh（別パス）は自分の配線とみなさない＝触らず、自分の hook を別に足す。"""
        self.settings.parent.mkdir(parents=True)
        other = {"type": "command", "command": "bash /other-project/hooks/office-event.sh", "timeout": 60}
        self.settings.write_text(json.dumps({"hooks": {"Stop": [{"hooks": [dict(other)]}]}}), encoding="utf-8")
        r = run_install(self.home, "--wire")
        self.assertEqual(r.returncode, 0, r.stderr)
        stop = self._hooks()["Stop"]
        others = [h for g in stop for h in g["hooks"] if h["command"] == other["command"]]
        self.assertEqual(others, [other])                       # 無改変（async を足していない）
        self.assertEqual(len(self._event_entries("Stop")), 1)   # 自分の配線は別 entry として存在

    def test_restricted_matcher_group_does_not_count_as_wired(self):
        """matcher 付き group に自分のコマンドがあっても、イベント全体の配線とは数えず無制限 group を足す。"""
        self.settings.parent.mkdir(parents=True)
        self.settings.write_text(json.dumps({"hooks": {"PostToolUse": [
            {"matcher": "Edit", "hooks": [{"type": "command",
                                            "command": 'bash "$HOME/.claude/hooks/office-event.sh"', "timeout": 10}]}]}}),
            encoding="utf-8")
        r = run_install(self.home, "--wire")
        self.assertEqual(r.returncode, 0, r.stderr)
        groups = self._hooks()["PostToolUse"]
        self.assertEqual(groups[0]["matcher"], "Edit")          # 既存 group は無改変
        unrestricted = [g for g in groups if not g.get("matcher")
                        and any("office-event.sh" in h["command"] for h in g["hooks"])]
        self.assertEqual(len(unrestricted), 1)

    def test_default_mode_does_not_write_events(self):
        r = run_install(self.home)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("イベント記録", r.stdout)
        self.assertFalse(self.settings.exists())


if __name__ == "__main__":
    unittest.main()


class StatuslineWireTest(unittest.TestCase):
    """R61: --statusline 配線（capture ラッパー・既存コマンドのファイル退避・冪等）。"""

    def setUp(self):
        self.home = Path(tempfile.mkdtemp(prefix="slwire_"))
        self.settings = self.home / ".claude" / "settings.json"
        self.passthrough = self.home / ".claude" / "office_usage" / "passthrough.cmd"

    def _cmd(self):
        return json.loads(self.settings.read_text(encoding="utf-8"))["statusLine"]["command"]

    def test_wire_creates_and_is_idempotent(self):
        r1 = run_install(self.home, "--statusline")
        self.assertEqual(r1.returncode, 0, r1.stderr)
        self.assertIn("office-statusline-capture", self._cmd())
        self.assertFalse(self.passthrough.exists())      # 既存コマンド無し=退避も無し
        # capture スクリプト本体も配布されている
        self.assertTrue((self.home / ".claude" / "hooks" /
                         "office-statusline-capture.sh").exists())
        r2 = run_install(self.home, "--statusline")
        self.assertIn("配線済み", r2.stdout)             # 冪等
        self.assertIn("office-statusline-capture", self._cmd())

    def test_existing_command_is_preserved_as_passthrough(self):
        self.settings.parent.mkdir(parents=True)
        prev = "bash -c 'echo my fancy statusline'"
        self.settings.write_text(json.dumps({
            "model": "opusplan",
            "statusLine": {"type": "command", "command": prev}}), encoding="utf-8")
        r = run_install(self.home, "--statusline")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.passthrough.read_text(encoding="utf-8"), prev)   # 退避
        self.assertEqual(self.passthrough.stat().st_mode & 0o777, 0o600)
        self.assertIn("office-statusline-capture", self._cmd())               # 差し替え
        data = json.loads(self.settings.read_text(encoding="utf-8"))
        self.assertEqual(data["model"], "opusplan")                            # 他キー温存
        backups = list(self.settings.parent.glob("settings.json.bak-*"))
        self.assertEqual(len(backups), 1)

    def test_broken_settings_refuses(self):
        self.settings.parent.mkdir(parents=True)
        self.settings.write_text("{broken", encoding="utf-8")
        r = run_install(self.home, "--statusline")
        self.assertNotEqual(r.returncode, 0)
        self.assertEqual(self.settings.read_text(encoding="utf-8"), "{broken")
