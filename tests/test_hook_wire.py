#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""R50提案2d: hooks/install.sh --wire の自動配線テスト（隔離HOME・冪等・既存hooks温存）。

グローバル ~/.claude/settings.json への書込は他プロジェクトのhookを壊すと事故が大きいので、
バックアップ作成・冪等（2回実行で重複しない）・既存Stop hookの温存を機械でピンする。
"""
import json
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
        hook = self._stops()[-1]["hooks"][0]
        self.assertTrue(hook["asyncRewake"])
        self.assertEqual(hook["timeout"], 7300)
        r2 = run_install(self.home, "--wire")             # 2回目=冪等
        self.assertIn("配線を確認", r2.stdout)
        self.assertEqual(self._wired_count(), 1)

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
