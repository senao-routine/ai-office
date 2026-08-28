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
        # ★形は必ず [{label, desc}]（素の文字列だとスマホの選択肢ボタンが1つも出ない＝実機で踏んだ）
        self.assertEqual(e["questionOptions"], [{"label": "A案", "desc": ""},
                                                {"label": "B案", "desc": ""}])

    def test_question_options_are_always_labelled_objects(self):
        """選択肢の形の契約。producer が増えてもここを通らないと本番のスマホで消える。"""
        for opts in (["A", "B"], ["日本語の選択肢"], []):
            sess = "sess-shape%04d" % len(opts)
            proj = Path(self.home) / ".claude/projects/-tmp-demo"
            proj.mkdir(parents=True, exist_ok=True)
            f = proj / f"{sess}.jsonl"
            f.write_text(json.dumps({"type": "assistant", "cwd": "/tmp/demo", "message": {
                "role": "assistant", "content": [{"type": "text", "text": "x"}]}}) + "\n",
                encoding="utf-8")
            publish(self.home, sess, kind="question", tool="AskUserQuestion",
                    ts=time.time() - 5, title="どっち?", options=opts)
            e = self.office.parse_session(f, time.time())
            for o in (e.get("questionOptions") or []):
                self.assertIsInstance(o, dict, "選択肢が素の文字列のまま載っている")
                self.assertTrue(o.get("label"))

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


class InboxLitterTest(unittest.TestCase):
    """指示ポストの置き場が永久に太らないこと（実測: pidfile 262個・最古35日前）。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = self.tmp.name
        self.office = _reload(self.home)
        self.inbox = Path(self.home) / ".claude" / "office_inbox"
        self.inbox.mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        self.tmp.cleanup()
        os.environ.pop("OFFICE_HOME", None)

    def _aged(self, name, age, body="{}"):
        p = self.inbox / name
        p.write_text(body, encoding="utf-8")
        os.utime(p, (time.time() - age, time.time() - age))
        return p

    def test_prunes_only_the_dead(self):
        old_pid = self._aged(".sess-old.pid", 3 * 86400, "1234")
        live_pid = self._aged(".sess-live.pid", 5, "5678")
        old_msg = self._aged("sess-old.json", 30 * 86400, '{"text":"x"}')
        new_msg = self._aged("sess-new.json", 60, '{"text":"y"}')
        hist = self._aged("_history.json", 90 * 86400, "[]")

        self.office._LAST_PRUNE[0] = 0
        n = self.office.prune_inbox_litter()
        self.assertEqual(n, 2, "消す数が違う（生きているものまで消していないか）")
        self.assertFalse(old_pid.exists())
        self.assertFalse(old_msg.exists())
        self.assertTrue(live_pid.exists(), "心拍している受信待機を殺した")
        self.assertTrue(new_msg.exists(), "まだ届く指示を捨てた")
        self.assertTrue(hist.exists(), "送信履歴を消した")

    def test_runs_at_most_hourly(self):
        self.office._LAST_PRUNE[0] = 0
        self.office.prune_inbox_litter()
        self._aged(".sess-x.pid", 3 * 86400, "1")
        self.assertEqual(self.office.prune_inbox_litter(), 0, "毎スキャン走ってディスクを叩いている")


class DeviceLedgerTest(unittest.TestCase):
    """端末台帳が太り続けないこと・どれが生きている鍵か分かること（実測57件/有効29件）。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = self.tmp.name
        self.office = _reload(self.home)

    def tearDown(self):
        self.tmp.cleanup()
        os.environ.pop("OFFICE_HOME", None)

    def test_prunes_dead_keys_only(self):
        now = int(time.time())
        d = {"version": 1, "devices": {
            "d_live": {"secret": "a", "label": "スマホ", "created": now,
                       "expires": now + 86400, "revoked": False, "last_used": 0},
            "d_rev": {"secret": "b", "label": "旧", "created": now,
                      "expires": now + 86400, "revoked": True, "last_used": 0,
                      "revoked_at": now - 2 * 3600},
            "d_justrev": {"secret": "d", "label": "いま失効", "created": now,
                          "expires": now + 86400, "revoked": True, "last_used": 0,
                          "revoked_at": now - 5},
            "d_old": {"secret": "c", "label": "期限切れ", "created": now - 99 * 86400,
                      "expires": now - 30 * 86400, "revoked": False, "last_used": 0},
            "d_justexp": {"secret": "e", "label": "先ほど期限切れ", "created": now - 31 * 86400,
                          "expires": now - 60, "revoked": False, "last_used": 0},
        }}
        self.office.save_devices(d)
        self.assertEqual(self.office.prune_devices(), 2)     # revoked と 7日超の期限切れ
        left = self.office.load_devices()["devices"]
        self.assertIn("d_live", left)
        self.assertIn("d_justexp", left, "切れたばかりの端末まで即消しにしている")
        self.assertIn("d_justrev", left, "失効を押した直後に一覧から消えると操作の結果が見えない")
        self.assertNotIn("d_rev", left)
        self.assertNotIn("d_old", left)

    def test_list_marks_state_and_puts_active_first(self):
        now = int(time.time())
        self.office.save_devices({"version": 1, "devices": {
            "d_exp": {"secret": "b", "label": "古い", "created": now - 100,
                      "expires": now - 10, "revoked": False, "last_used": 0},
            "d_live": {"secret": "a", "label": "スマホ", "created": now - 200,
                       "expires": now + 5 * 86400, "revoked": False, "last_used": 0},
        }})
        rows = self.office.list_devices()
        self.assertEqual(rows[0]["device_id"], "d_live", "有効な端末が先頭に来ない")
        self.assertEqual(rows[0]["state"], "active")
        self.assertEqual(rows[0]["daysLeft"], 5)
        self.assertEqual(rows[1]["state"], "expired")
        self.assertNotIn("secret", json.dumps(rows), "secret が一覧に漏れている")


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
