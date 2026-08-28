# -*- coding: utf-8 -*-
"""R86-H: 承認・質問の受け答え経路（サーバー側）の機械検証。

守っている性質:
  1. ❗は「推測」ではなく掲示（フックの publish）を一次情報にする＝聞かれた瞬間に立ち、
     人間がターミナルで答えれば掲示が消えて❗も消える
  2. 掲示が無いセッションへは「答える」経路を開かない（押せるのに届かない嘘を作らない）
  3. 中継（スマホ）経由の回答は **allow にならない**（src が構造的に "relay"）
  4. 聞かれている**中身**（Bashコマンド全文）は中継に出ない
"""
import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "server"))


def _reload(home):
    os.environ["OFFICE_HOME"] = str(home)
    for m in ("office_server", "relay_agent"):
        sys.modules.pop(m, None)
    import office_server
    return office_server


def publish(home, session, **kw):
    d = Path(home) / ".claude" / "office_approvals"
    d.mkdir(parents=True, exist_ok=True)
    rec = {"session": session, "tool": "Bash", "kind": "permission",
           "title": "rm -rf /Users/someone/secret", "options": [],
           "cwd": "/tmp", "ts": time.time(), "deadline": time.time() + 600, "pid": 1}
    rec.update(kw)
    (d / f"{session}.json").write_text(json.dumps(rec, ensure_ascii=False), encoding="utf-8")
    return d / f"{session}.json"


class PendingApprovalTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = self.tmp.name
        self.office = _reload(self.home)

    def tearDown(self):
        self.tmp.cleanup()
        os.environ.pop("OFFICE_HOME", None)

    def test_reads_live_posting(self):
        publish(self.home, "s1")
        a = self.office.pending_approval("s1")
        self.assertEqual(a["kind"], "permission")
        self.assertEqual(a["tool"], "Bash")
        self.assertIn("rm -rf", a["title"])

    def test_fresh_posting_needs_a_moment_before_it_counts_as_attention(self):
        """一瞬で消える掲示（そのまま許可される操作）で❗を光らせない＝誤プッシュの門番。"""
        publish(self.home, "s1")
        self.assertIsNone(self.office.pending_approval("s1", grace=self.office.ASK_GRACE))
        self.assertIsNotNone(self.office.pending_approval("s1"))   # 回答APIは即座に開く
        publish(self.home, "s1", ts=time.time() - 10)
        self.assertIsNotNone(self.office.pending_approval("s1", grace=self.office.ASK_GRACE))

    def test_expired_posting_is_a_ghost(self):
        publish(self.home, "s1", deadline=time.time() - 1)
        self.assertIsNone(self.office.pending_approval("s1"))

    def test_mismatched_or_broken_posting_is_ignored(self):
        d = Path(self.home) / ".claude/office_approvals"
        d.mkdir(parents=True, exist_ok=True)
        (d / "s1.json").write_text(json.dumps(
            {"session": "someone-else", "deadline": time.time() + 60}), encoding="utf-8")
        self.assertIsNone(self.office.pending_approval("s1"))
        (Path(self.home) / ".claude/office_approvals/s2.json").write_text("{oops", encoding="utf-8")
        self.assertIsNone(self.office.pending_approval("s2"))
        self.assertIsNone(self.office.pending_approval("../../etc/passwd"))
        self.assertIsNone(self.office.pending_approval(""))

    def test_reply_is_written_0600_and_atomically(self):
        publish(self.home, "s1")
        rec = self.office.write_approval_reply("s1", "allow", "", src="local")
        p = Path(self.home) / ".claude/office_approvals/s1.reply.json"
        self.assertEqual(oct(os.stat(p).st_mode)[-3:], "600")
        self.assertEqual(json.loads(p.read_text())["behavior"], "allow")
        self.assertEqual(rec["src"], "local")
        self.assertFalse(list(p.parent.glob(".*tmp")), "一時ファイルが残っている")

    def test_reply_rejects_bad_session(self):
        with self.assertRaises(ValueError):
            self.office.write_approval_reply("../evil", "allow", "")

    def test_attention_is_immediate_when_asked(self):
        """フックが掲示していれば 75秒ヒューリスティックを待たずに❗が立つ。"""
        sess = "sess-live1111"
        proj = Path(self.home) / ".claude/projects/-tmp-demo"
        proj.mkdir(parents=True)
        f = proj / f"{sess}.jsonl"
        f.write_text(json.dumps({"type": "assistant", "cwd": "/tmp/demo", "message": {
            "role": "assistant", "content": [{"type": "tool_use", "name": "Bash",
                                              "input": {"command": "git push"}}]}}) + "\n",
            encoding="utf-8")
        publish(self.home, sess, ts=time.time() - 5)
        e = self.office.parse_session(f, time.time())
        self.assertGreaterEqual(e["approvalMin"], 1, "掲示があるのに❗が立たない")
        self.assertEqual(e["ask"]["kind"], "permission")

    def test_question_posting_fills_options(self):
        sess = "sess-live2222"
        proj = Path(self.home) / ".claude/projects/-tmp-demo"
        proj.mkdir(parents=True, exist_ok=True)
        f = proj / f"{sess}.jsonl"
        f.write_text(json.dumps({"type": "assistant", "cwd": "/tmp/demo", "message": {
            "role": "assistant", "content": [{"type": "text", "text": "考え中"}]}}) + "\n",
            encoding="utf-8")
        publish(self.home, sess, kind="question", tool="AskUserQuestion", ts=time.time() - 5,
                title="A案とB案どちらにしますか?", options=["A案", "B案"])
        e = self.office.parse_session(f, time.time())
        self.assertEqual(e["question"], "A案とB案どちらにしますか?")
        self.assertEqual(e["questionOptions"], ["A案", "B案"])

    def test_blocked_session_stays_on_the_floor(self):
        """★止まっている＝イベントが出ない＝古く見える。素直に3時間窓で切ると
        **承認まちが画面から消える**（実測: 3時間45分ブロックされた works が非表示だった）。"""
        sess = "sess-stuck0001"
        proj = Path(self.home) / ".claude/projects/-tmp-demo"
        proj.mkdir(parents=True, exist_ok=True)
        f = proj / f"{sess}.jsonl"
        old_ts = time.strftime("%Y-%m-%dT%H:%M:%S.000Z",
                               time.gmtime(time.time() - 5 * 3600))
        f.write_text(json.dumps({"type": "assistant", "cwd": "/tmp/demo", "timestamp": old_ts,
                                 "message": {"role": "assistant", "content": [
                                     {"type": "tool_use", "name": "Bash",
                                      "input": {"command": "git add ."}}]}}) + "\n",
                     encoding="utf-8")
        publish(self.home, sess, ts=time.time() - 5 * 3600, deadline=time.time() + 600)
        emps = self.office.scan_office()["employees"]
        me = [e for e in emps if e["session"] == sess]
        self.assertTrue(me, "承認まちのセッションが出勤窓から落ちて画面から消えた")
        self.assertEqual(me[0]["state"], "waiting", "人間待ちなのに休憩中に見える")
        # 掲示が無ければ従来どおり退勤扱い（幽霊社員の対策は生きている）
        (Path(self.home) / ".claude/office_approvals" / f"{sess}.json").unlink()
        emps2 = self.office.scan_office()["employees"]
        self.assertFalse([e for e in emps2 if e["session"] == sess],
                         "掲示が無い古いセッションまで居座らせている")

    def test_no_posting_keeps_old_heuristic(self):
        """掲示が無い（フック未配線の旧セッション）ときは従来どおり推測に委ねる＝暗転しない。"""
        sess = "sess-live3333"
        proj = Path(self.home) / ".claude/projects/-tmp-demo"
        proj.mkdir(parents=True, exist_ok=True)
        f = proj / f"{sess}.jsonl"
        f.write_text(json.dumps({"type": "assistant", "cwd": "/tmp/demo", "message": {
            "role": "assistant", "content": [{"type": "tool_use", "name": "ExitPlanMode",
                                              "input": {"plan": "x"}}]}}) + "\n",
            encoding="utf-8")
        os.utime(f, (time.time() - 300, time.time() - 300))
        e = self.office.parse_session(f, time.time())
        self.assertGreaterEqual(e["approvalMin"], 1)
        self.assertIsNone(e.get("ask"))


class RelayApprovalTest(unittest.TestCase):
    """中継側: 回答は届くが、実行許可は渡らない・中身は出ない。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = self.tmp.name
        self.office = _reload(self.home)
        import relay_agent
        self.ra = relay_agent

    def tearDown(self):
        self.tmp.cleanup()
        os.environ.pop("OFFICE_HOME", None)

    def test_ask_body_never_leaves_the_mac(self):
        e = {"disp": "A", "ask": {"tool": "Bash", "kind": "permission",
                                  "title": "curl https://example.com/secret -H 'Authorization: x'",
                                  "options": []}}
        self.ra._redact_entry_for_relay(e)
        self.assertEqual(e["ask"], {"tool": "Bash", "kind": "permission"})
        self.assertNotIn("secret", json.dumps(e))

    def test_relay_answer_is_never_local(self):
        """スマホ経由の回答は src="relay"。フックはこれを allow に昇格させない。"""
        rec = self.office.write_approval_reply("s1", "allow", "はい", src="relay")
        self.assertEqual(rec["src"], "relay")

    def test_permission_answer_explains_it_did_not_run(self):
        msg = self.ra._answer_text({"kind": "permission"}, "はい、承認します")
        self.assertIn("はい、承認します", msg)
        self.assertIn("ターミナル", msg)
        # 質問への回答は素の文言のまま（余計な但し書きで選択肢の判定を壊さない）
        self.assertEqual(self.ra._answer_text({"kind": "question"}, "B案"), "B案")


if __name__ == "__main__":
    unittest.main()
