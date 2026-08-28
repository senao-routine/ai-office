# -*- coding: utf-8 -*-
"""R86-H: PermissionRequest フック（hooks/office-approval-wait.sh）の機械検証。

実TUIでの挙動（2026-08-28実測）はこのテストの前提であって、ここで検証するのは
「フックが何を publish し、どの回答を通し、どの回答を**通さない**か」。

★このテストが守っている一番大事な性質:
  - 中継（スマホ）由来の allow は **絶対に allow にならない**（deny へ降格）。
    ここが破れると、中継トークン＋デバイス秘密の漏洩がそのまま任意コード実行になる。
  - 異常系（壊れた入力・古い回答・別セッション宛・タイムアウト）は全て
    **無出力 exit 0**＝ターミナルの通常動作を1ミリも変えない。
"""
import json
import os
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HOOK = ROOT / "hooks" / "office-approval-wait.sh"

BASH_PAYLOAD = {
    "session_id": "sess-aaaa1111", "cwd": "/tmp/demo", "permission_mode": "default",
    "hook_event_name": "PermissionRequest", "tool_name": "Bash",
    "tool_input": {"command": "git push --dry-run", "description": "push"},
    "transcript_path": "/nonexistent/transcript.jsonl",
}


def run_hook(payload, home, wait=3.0, poll=0.1, env_extra=None):
    env = dict(os.environ)
    env.update({"OFFICE_HOME": str(home), "OFFICE_APPROVAL_WAIT": str(wait),
                "OFFICE_APPROVAL_POLL": str(poll)})
    env.update(env_extra or {})
    return subprocess.run(["bash", str(HOOK)], input=json.dumps(payload),
                          capture_output=True, text=True, env=env, timeout=wait + 25)


def spawn_hook(payload, home, wait=6.0, poll=0.1):
    env = dict(os.environ)
    env.update({"OFFICE_HOME": str(home), "OFFICE_APPROVAL_WAIT": str(wait),
                "OFFICE_APPROVAL_POLL": str(poll)})
    p = subprocess.Popen(["bash", str(HOOK)], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                         stderr=subprocess.PIPE, text=True, env=env)
    p.stdin.write(json.dumps(payload))
    p.stdin.close()
    p.stdin = None          # communicate() が閉じた stdin を触らないように
    return p


def approvals(home):
    return Path(home) / ".claude" / "office_approvals"


def wait_for(path, timeout=8.0):
    end = time.time() + timeout
    while time.time() < end:
        if os.path.exists(path):
            return True
        time.sleep(0.05)
    return False


def write_reply(home, session, **kw):
    rec = {"session": session, "ts": time.time()}
    rec.update(kw)
    p = approvals(home) / (session + ".reply.json")
    p.write_text(json.dumps(rec, ensure_ascii=False), encoding="utf-8")
    return p


def decision(out):
    return json.loads(out)["hookSpecificOutput"]["decision"]


class ApprovalHookTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = self.tmp.name

    def tearDown(self):
        self.tmp.cleanup()

    # ── publish（AIオフィスの❗が読む「事実」） ───────────────────────────
    def test_publishes_pending_and_cleans_up(self):
        p = spawn_hook(BASH_PAYLOAD, self.home)
        pend = approvals(self.home) / "sess-aaaa1111.json"
        self.assertTrue(wait_for(pend), "掲示ファイルが出ない")
        rec = json.loads(pend.read_text())
        self.assertEqual(rec["tool"], "Bash")
        self.assertEqual(rec["kind"], "permission")
        self.assertEqual(rec["title"], "git push --dry-run")
        self.assertEqual(rec["session"], "sess-aaaa1111")
        self.assertGreater(rec["deadline"], rec["ts"])
        self.assertEqual(oct(os.stat(pend).st_mode)[-3:], "600")   # 本文を含むので0600
        p.wait(timeout=25)
        self.assertEqual(p.returncode, 0)
        self.assertEqual(p.stdout.read(), "")                       # タイムアウトは素通し
        p.stdout.close(); p.stderr.close()
        self.assertFalse(pend.exists(), "掲示が残ると❗が幽霊になる")

    def test_question_payload_carries_options(self):
        pl = dict(BASH_PAYLOAD, tool_name="AskUserQuestion", tool_input={"questions": [{
            "question": "どちらで進めますか?", "header": "方式",
            "options": [{"label": "A案"}, {"label": "B案"}]}]})
        p = spawn_hook(pl, self.home)
        pend = approvals(self.home) / "sess-aaaa1111.json"
        self.assertTrue(wait_for(pend))
        rec = json.loads(pend.read_text())
        self.assertEqual(rec["kind"], "question")
        self.assertEqual(rec["title"], "どちらで進めますか?")
        self.assertEqual(rec["options"], ["A案", "B案"])
        p.kill(); p.wait(timeout=10); p.stdout.close(); p.stderr.close()

    def test_write_tool_publishes_basename_only(self):
        pl = dict(BASH_PAYLOAD, tool_name="Write",
                  tool_input={"file_path": "/Users/someone/secret/plan.md"})
        p = spawn_hook(pl, self.home)
        pend = approvals(self.home) / "sess-aaaa1111.json"
        self.assertTrue(wait_for(pend))
        rec = json.loads(pend.read_text())
        self.assertEqual(rec["title"], "plan.md")
        self.assertNotIn("secret", pend.read_text())
        p.kill(); p.wait(timeout=10); p.stdout.close(); p.stderr.close()

    def test_exit_plan_mode_is_a_question(self):
        pl = dict(BASH_PAYLOAD, tool_name="ExitPlanMode", tool_input={"plan": "..."})
        p = spawn_hook(pl, self.home)
        pend = approvals(self.home) / "sess-aaaa1111.json"
        self.assertTrue(wait_for(pend))
        self.assertEqual(json.loads(pend.read_text())["kind"], "question")
        p.kill(); p.wait(timeout=10); p.stdout.close(); p.stderr.close()

    # ── 回答の受理と拒否 ──────────────────────────────────────────────
    def test_local_allow_is_honored(self):
        p = spawn_hook(BASH_PAYLOAD, self.home)
        self.assertTrue(wait_for(approvals(self.home) / "sess-aaaa1111.json"))
        write_reply(self.home, "sess-aaaa1111", behavior="allow", src="local")
        out, _ = p.communicate(timeout=25)
        self.assertEqual(decision(out), {"behavior": "allow"})

    def test_relay_allow_is_downgraded_to_deny(self):
        """★安全境界: スマホからの『許可』は実行を通さない（言葉だけ届く）。"""
        p = spawn_hook(BASH_PAYLOAD, self.home)
        self.assertTrue(wait_for(approvals(self.home) / "sess-aaaa1111.json"))
        write_reply(self.home, "sess-aaaa1111", behavior="allow", src="relay",
                    message="スマホから承認しました")
        out, _ = p.communicate(timeout=25)
        d = decision(out)
        self.assertEqual(d["behavior"], "deny")
        self.assertIn("スマホから承認しました", d["message"])

    def test_missing_src_never_allows(self):
        p = spawn_hook(BASH_PAYLOAD, self.home)
        self.assertTrue(wait_for(approvals(self.home) / "sess-aaaa1111.json"))
        write_reply(self.home, "sess-aaaa1111", behavior="allow", message="ok")
        out, _ = p.communicate(timeout=25)
        self.assertEqual(decision(out)["behavior"], "deny")

    def test_deny_with_message_is_the_answer_channel(self):
        p = spawn_hook(BASH_PAYLOAD, self.home)
        self.assertTrue(wait_for(approvals(self.home) / "sess-aaaa1111.json"))
        write_reply(self.home, "sess-aaaa1111", behavior="deny", src="relay",
                    message="B案でお願いします")
        out, _ = p.communicate(timeout=25)
        self.assertEqual(decision(out), {"behavior": "deny", "message": "B案でお願いします"})

    def test_reply_is_consumed_exactly_once(self):
        """並行して2つ質問が出ていても、1つの回答が二重に効かない（原子的に取る）。"""
        p1 = spawn_hook(BASH_PAYLOAD, self.home, wait=4)
        self.assertTrue(wait_for(approvals(self.home) / "sess-aaaa1111.json"))
        p2 = spawn_hook(BASH_PAYLOAD, self.home, wait=4)
        time.sleep(0.5)
        write_reply(self.home, "sess-aaaa1111", behavior="deny", src="local", message="一度だけ")
        o1, _ = p1.communicate(timeout=25)
        o2, _ = p2.communicate(timeout=25)
        self.assertEqual(len([o for o in (o1, o2) if o.strip()]), 1,
                         "回答が2つのフックに効いた（二重回答）")

    # ── 異常系は全て「無出力 exit 0」＝素通し ─────────────────────────
    def test_stale_reply_is_ignored(self):
        p = spawn_hook(BASH_PAYLOAD, self.home, wait=3)
        self.assertTrue(wait_for(approvals(self.home) / "sess-aaaa1111.json"))
        (approvals(self.home) / "sess-aaaa1111.reply.json").write_text(json.dumps(
            {"session": "sess-aaaa1111", "behavior": "allow", "src": "local",
             "ts": time.time() - 3600}), encoding="utf-8")
        out, _ = p.communicate(timeout=25)
        self.assertEqual(out, "", "1時間前の回答が効いてしまう＝別の質問に化ける")

    def test_reply_for_another_session_is_ignored(self):
        p = spawn_hook(BASH_PAYLOAD, self.home, wait=3)
        self.assertTrue(wait_for(approvals(self.home) / "sess-aaaa1111.json"))
        (approvals(self.home) / "sess-aaaa1111.reply.json").write_text(json.dumps(
            {"session": "someone-else", "behavior": "allow", "src": "local",
             "ts": time.time()}), encoding="utf-8")
        out, _ = p.communicate(timeout=25)
        self.assertEqual(out, "")

    def test_tool_result_means_the_human_answered(self):
        """人間がターミナルで答えると tool_result が書かれる → 黙って降りる（二重回答の防止）。"""
        tp = Path(self.home) / "t.jsonl"
        tp.write_text('{"type":"assistant"}\n', encoding="utf-8")
        p = spawn_hook(dict(BASH_PAYLOAD, transcript_path=str(tp)), self.home, wait=25)
        pend = approvals(self.home) / "sess-aaaa1111.json"
        self.assertTrue(wait_for(pend))
        t0 = time.time()
        with tp.open("a") as f:
            f.write('{"type":"user","message":{"content":[{"type":"tool_result"}]}}\n')
        out, _ = p.communicate(timeout=30)
        self.assertEqual(out, "", "答え済みなのに決定を返した（二重回答）")
        self.assertLess(time.time() - t0, 12, "検知が遅すぎる（❗が居座る）")
        self.assertFalse(pend.exists())

    def test_noise_in_the_transcript_does_not_abandon_the_wait(self):
        """★実機で踏んだ穴: 質問が出ている最中にも attachment 等が普通に追記される。
        「サイズが増えたら降りる」にすると、オフィスからの回答が二度と間に合わない。"""
        tp = Path(self.home) / "t.jsonl"
        tp.write_text('{"type":"assistant"}\n', encoding="utf-8")
        p = spawn_hook(dict(BASH_PAYLOAD, transcript_path=str(tp)), self.home, wait=25)
        self.assertTrue(wait_for(approvals(self.home) / "sess-aaaa1111.json"))
        with tp.open("a") as f:                      # 実機で観測された種類の追記
            f.write('{"type":"attachment","content":{"x":1}}\n')
            f.write('{"type":"file-history-delta"}\n')
        time.sleep(2.5)
        write_reply(self.home, "sess-aaaa1111", behavior="deny", src="local", message="まだ間に合う")
        out, _ = p.communicate(timeout=30)
        self.assertEqual(decision(out), {"behavior": "deny", "message": "まだ間に合う"},
                         "ノイズ追記で待機を降りてしまい、回答が届かない")

    def test_broken_input_is_passthrough(self):
        for bad in ("", "not json", "[]", '{"session_id":"../../etc/passwd"}',
                    '{"session_id":""}'):
            env = dict(os.environ, OFFICE_HOME=self.home, OFFICE_APPROVAL_WAIT="2",
                       OFFICE_APPROVAL_POLL="0.1")
            r = subprocess.run(["bash", str(HOOK)], input=bad, capture_output=True,
                               text=True, env=env, timeout=30)
            self.assertEqual(r.returncode, 0, bad)
            self.assertEqual(r.stdout, "", bad)
        self.assertFalse(approvals(self.home).exists(),
                         "不正なセッションIDでディレクトリを作らない")

    def test_timeout_is_silent(self):
        r = run_hook(BASH_PAYLOAD, self.home, wait=1.5)
        self.assertEqual(r.returncode, 0)
        self.assertEqual(r.stdout, "")


if __name__ == "__main__":
    unittest.main()
