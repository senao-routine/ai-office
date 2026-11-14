#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""R99-P4: 証拠列の分類の見張り（tools/evidence_watch.py）の番人。

なぜ要るか: 9/21〜9/23 は Bash の PostToolUse が 1 日 1,400〜3,000 件あったのに、hook の分類（kind＝git:commit／test）は
0 件のまま誰も気づかなかった（この repo だけでもその 3 日で 16 コミット）。台帳の証拠列（committed／tested）は
この kind だけを根拠にするので、分類が死ぬと列は黙って「—」になる。配備後に「分類件数 > 0」を見る手段が無かった。

見るもの（本番の ~/.claude には触らない＝全部一時 HOME）:
  ① Bash が十分（20 件以上）あるのに分類 0 件 → ⚠（終了 4）
  ② 分類が 1 件でもあれば ✓（終了 0）・Bash が少ない／記録が無いときは黙って省略（終了 3）
  ③ 窓は直近 24 時間（昨日のファイルの窓内は数える・窓の外の分類で ⚠ を消さない）
  ④ 壊れた行・途中まで書かれた行で落ちない。出力にパスを出さない
  ⑤ 本物の hook（hooks/office-event.sh）が書いた行を読める（hook の形式が変わったら見張りも赤）
"""
import datetime
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TOOL = ROOT / "tools" / "evidence_watch.py"
HOOK = ROOT / "hooks" / "office-event.sh"
sys.path.insert(0, str(ROOT / "tools"))
import evidence_watch  # noqa: E402

NOW = 1790000000.0      # 固定の「今」（窓の計算を時計に依存させない）


def rec(ts, ev="PostToolUse", tool="Bash", kind=""):
    return {"v": 1, "ts": ts, "ev": ev, "sid": "s-1", "cwdh": "0" * 12, "tool": tool, "tgt": "",
            "kind": kind, "nt": "", "sub": "", "task": "", "ok": ev != "PostToolUseFailure", "src": "", "plen": 0}


def write_day(home, day, records, tail=""):
    d = Path(home) / ".claude" / "office_events"
    d.mkdir(parents=True, exist_ok=True)
    with (d / f"{day.isoformat()}.jsonl").open("a", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r, separators=(",", ":")) + "\n")
        f.write(tail)


def run_cli(home, now=NOW):
    env = {"OFFICE_HOME": str(home), "HOME": str(home), "PATH": os.environ.get("PATH", "/usr/bin:/bin")}
    return subprocess.run([sys.executable, str(TOOL), "--now", str(now)], capture_output=True, text=True, env=env)


class AssessTest(unittest.TestCase):
    """純関数（ファイルに触らない）"""

    def test_Bashが十分で分類0件なら警告(self):
        r = evidence_watch.assess([rec(NOW - 60 * i) for i in range(25)], NOW)
        self.assertEqual((r["status"], r["bash"], r["kinds"]), ("warn", 25, {}))

    def test_分類が1件でもあれば正常(self):
        rs = [rec(NOW - 60 * i) for i in range(25)] + [rec(NOW - 5, kind="git:commit")]
        r = evidence_watch.assess(rs, NOW)
        self.assertEqual((r["status"], r["bash"], r["kinds"]), ("ok", 26, {"git:commit": 1}))

    def test_失敗したBashのtest分類も数える(self):
        rs = [rec(NOW - 60 * i) for i in range(25)] + [rec(NOW - 5, ev="PostToolUseFailure", kind="test")]
        self.assertEqual(evidence_watch.assess(rs, NOW)["status"], "ok")

    def test_Bashが少なければ省略(self):
        r = evidence_watch.assess([rec(NOW - 60 * i) for i in range(19)], NOW)
        self.assertEqual((r["status"], r["bash"]), ("skip", 19))

    def test_Bash以外とPostToolUse以外は数えない(self):
        rs = ([rec(NOW - i, tool="Edit") for i in range(30)] + [rec(NOW - i, ev="PreToolUse") for i in range(30)]
              + [rec(NOW - i, ev="PreToolUse", kind="git:commit") for i in range(3)])
        r = evidence_watch.assess(rs, NOW)
        self.assertEqual((r["status"], r["bash"], r["kinds"]), ("skip", 0, {}))

    def test_窓の外の分類で警告を消さない(self):
        rs = [rec(NOW - 60 * i) for i in range(25)] + [rec(NOW - 25 * 3600, kind="git:commit")]
        self.assertEqual(evidence_watch.assess(rs, NOW)["status"], "warn")

    def test_窓の外のBashは数えない(self):
        rs = [rec(NOW - 25 * 3600 - i) for i in range(40)]
        self.assertEqual(evidence_watch.assess(rs, NOW)["bash"], 0)

    def test_壊れた値で落ちない(self):
        rs = [None, "x", {"ts": "late", "tool": "Bash", "ev": "PostToolUse"}, {"ts": True},
              {**rec(NOW), "kind": 7}] + [rec(NOW - i) for i in range(20)]
        r = evidence_watch.assess(rs, NOW)
        self.assertEqual((r["status"], r["bash"], r["kinds"]), ("warn", 21, {}))


class CliTest(unittest.TestCase):
    """一時 HOME のファイルを読む CLI（verify ▶8 と同じ呼び方）"""

    def setUp(self):
        self.home = Path(tempfile.mkdtemp(prefix="evidence_watch_"))
        self.addCleanup(shutil.rmtree, self.home, ignore_errors=True)
        self.today = datetime.date.fromtimestamp(NOW)
        self.yesterday = self.today - datetime.timedelta(days=1)

    def test_記録が無ければ省略(self):
        r = run_cli(self.home)
        self.assertEqual(r.returncode, 3, r.stdout + r.stderr)

    def test_分類0件なら終了4で警告し件数を言う(self):
        write_day(self.home, self.today, [rec(NOW - 30 * i) for i in range(30)])
        r = run_cli(self.home)
        self.assertEqual(r.returncode, 4, r.stdout + r.stderr)
        self.assertIn("30", r.stdout)
        self.assertIn("0 件", r.stdout)

    def test_昨日のファイルの窓内の分類を数える(self):
        # 今日の 0 時を跨いだ 24 時間の窓＝昨日のファイルも読む（今日のファイルだけだと朝は毎日 ⚠ になる）
        midnight = datetime.datetime.combine(self.today, datetime.time()).timestamp()
        now = midnight + 3600                              # 今日の 01:00
        write_day(self.home, self.today, [rec(now - 60 * i) for i in range(25)])
        write_day(self.home, self.yesterday, [rec(midnight - 7200, kind="test")])
        r = run_cli(self.home, now)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("test", r.stdout)

    def test_途中まで書かれた行で落ちない(self):
        write_day(self.home, self.today, [rec(NOW - 30 * i) for i in range(25)] + [rec(NOW - 1, kind="git:commit")],
                  tail='{"v":1,"tool":"Bash","ts":17900')          # hook が書いている最中の行
        write_day(self.home, self.yesterday, [], tail='"Bash" not json\n\x00"Bash"\n[1,"Bash"]\n')
        r = run_cli(self.home)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_出力にパスを出さない(self):
        write_day(self.home, self.today, [rec(NOW - 30 * i) for i in range(30)])
        for now in (NOW, NOW + 400 * 86400):               # 警告・省略の両方の文言
            r = run_cli(self.home, now)
            self.assertNotIn(str(self.home), r.stdout + r.stderr)
            self.assertNotIn("office_events/", r.stdout + r.stderr)

    def test_本物のhookが書いた分類を読める(self):
        """hook（hooks/office-event.sh）の出力形式と見張りの読み方がずれたら赤（片方だけ変えると気づけない）"""
        env = {"OFFICE_HOME": str(self.home), "HOME": str(self.home),
               "PATH": "/usr/bin:/bin:/usr/sbin:/opt/homebrew/bin"}

        def fire(command):
            payload = {"session_id": "sess-watch-0001", "hook_event_name": "PostToolUse", "cwd": "/w",
                       "tool_name": "Bash", "tool_input": {"command": command}}
            r = subprocess.run(["bash", str(HOOK)], input=json.dumps(payload), capture_output=True,
                               text=True, env=env)
            self.assertEqual(r.returncode, 0)

        for i in range(20):
            fire(f"ls -la /w/{i}")
        now = time.time() + 1
        self.assertEqual(run_cli(self.home, now).returncode, 4, "hook が書いた Bash 20 件を数えていない")
        fire("git commit -m 'watch'")
        r = run_cli(self.home, time.time() + 1)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("git:commit", r.stdout)


if __name__ == "__main__":
    unittest.main()
