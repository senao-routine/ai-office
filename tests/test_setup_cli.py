#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""R97: setup.sh の入口（引数の受け取りと --demo）を固定する。

なぜ要るか（2026-09-17 の棚卸し）:
  - 未知の引数が**フルインストール扱い**だった（`bash setup.sh --help` のつもりが常駐まで入る）。
  - 「まず見たい」人の経路が無く、デモを見るにもフック配線と常駐登録が要った。
  --demo は「何も入れない・何も残さない」が契約なので、~/.claude を 1 バイトも触らないことまで見る。
"""
import os
import re
import signal
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SETUP = ROOT / "setup.sh"


def run(args, env=None, timeout=20):
    return subprocess.run(["bash", str(SETUP), *args], capture_output=True, text=True,
                          timeout=timeout, env={**os.environ, **(env or {})}, cwd=str(ROOT))


class SetupCliTest(unittest.TestCase):
    def test_help_lists_every_mode(self):
        r = run(["--help"])
        self.assertEqual(r.returncode, 0, r.stderr)
        for mode in ("--demo", "--no-daemon", "--check"):
            self.assertIn(mode, r.stdout)

    def test_unknown_argument_does_not_install(self):
        """未知の引数は 2 で断る（従来はここからフルインストールが走っていた）。"""
        r = run(["--not-a-real-flag"])
        self.assertEqual(r.returncode, 2, r.stdout)
        # 使い方を出して終わる＝環境を確認する段（1.）にすら進まない
        self.assertNotIn("環境を確認します", r.stdout)
        self.assertNotIn("配線", r.stdout.split("知らない指定")[0].replace("配線 →", ""))

    def test_demo_serves_and_touches_nothing(self):
        """--demo は一時領域だけで動き、~/.claude にも LaunchAgent にも触らない。"""
        home = Path(tempfile.mkdtemp())
        (home / ".claude").mkdir()
        marker = home / ".claude" / "settings.json"
        marker.write_text('{"hooks": {}}', encoding="utf-8")
        # 契約は「配線しない・常駐しない」。Claude Code 自身が作る ~/.claude.json や
        # そのバックアップは、セッション検出のために `claude` が動いた副産物なので数えない。
        before = marker.read_bytes()
        plists = home / "Library" / "LaunchAgents"
        tmp_before = {d for d in os.listdir("/tmp") if d.startswith("aioffice-demo.")}
        proc = subprocess.Popen(["bash", str(SETUP), "--demo"], stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, text=True, cwd=str(ROOT),
                                env={**os.environ, "HOME": str(home), "OFFICE_HOME": str(home)},
                                start_new_session=True)
        url, out = None, ""
        try:
            deadline = time.time() + 30
            while time.time() < deadline:
                line = proc.stdout.readline()
                if not line:
                    break
                out += line
                m = re.search(r"http://127\.0\.0\.1:\d+/\?demo=1", line)
                if m:
                    url = m.group(0)
                    break
            self.assertIsNotNone(url, f"デモの URL が出ない: {out}")
            import json
            import urllib.request
            with urllib.request.urlopen(url, timeout=10) as res:
                self.assertEqual(res.status, 200)
            port = int(re.search(r":(\d+)/", url).group(1))
            req = urllib.request.Request(f"http://127.0.0.1:{port}/api/office",
                                         headers={"X-Office-Local": "1"})
            with urllib.request.urlopen(req, timeout=10) as res:
                office = json.loads(res.read())
            # デモは**実セッションを読まない**（子に HOME を継がせると `claude` が実の ~/.claude を見る）
            self.assertEqual(office.get("employees"), [], "デモが実セッションを読んでいる")
        finally:
            # Ctrl-C と同じ経路で止める。非対話 bash の子は SIGINT を無視して生き残るので、
            # setup.sh 側で明示的に止めていないとポートを掴んだまま残る（実際に残っていた）
            os.killpg(proc.pid, signal.SIGINT)
            try:
                proc.wait(timeout=15)
            except subprocess.TimeoutExpired:
                os.killpg(proc.pid, signal.SIGKILL)
            time.sleep(2.0)
            held = subprocess.run(["lsof", "-ti", f"tcp:{port}", "-sTCP:LISTEN"],
                                  capture_output=True, text=True).stdout.strip()
            self.assertEqual(held, "", "Ctrl-C のあともデモのサーバーが残っている")
            # 「何も残さない」の実測: この実行で作った一時領域が消えていること。
            # PID だけ止めると `claude`（セッション検出）が孤児になり、消したはずの領域を作り直す。
            left = {d for d in os.listdir("/tmp") if d.startswith("aioffice-demo.")} - tmp_before
            self.assertEqual(left, set(), f"デモの一時領域が残っている: {left}")
        self.assertEqual(before, marker.read_bytes(), "--demo が settings.json を書き換えている")
        self.assertFalse(plists.exists() and list(plists.glob("com.senao.aioffice*")),
                         "--demo が LaunchAgent を作っている")
        self.assertFalse((home / ".claude" / "hooks").exists(), "--demo が hook を配っている")


if __name__ == "__main__":
    unittest.main()
