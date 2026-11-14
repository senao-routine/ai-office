# -*- coding: utf-8 -*-
"""R86-H: 承認・質問の受け答え経路（サーバー側）の機械検証。

守っている性質:
  1. ❗は「推測」ではなく掲示（フックの publish）を一次情報にする＝聞かれた瞬間に立ち、
     人間がターミナルで答えれば掲示が消えて❗も消える
  2. 掲示が無いセッションへは「答える」経路を開かない（押せるのに届かない嘘を作らない）
  3. 中継（スマホ）経由の回答は **allow にならない**（src が構造的に "relay"）
  4. 聞かれている**中身**（Bashコマンド全文）は中継に出ない
"""
import hashlib
import io
import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "server"))


def _reload(home):
    os.environ["OFFICE_HOME"] = str(home)
    for m in ("office_server", "relay_agent"):
        sys.modules.pop(m, None)
    import office_server
    return office_server


def aid(session, n=0):
    """テスト用の決まった askId（32 桁の 16 進）。"""
    return hashlib.md5(f"{session}#{n}".encode()).hexdigest()


def publish(home, session, ask_id="auto", **kw):
    """R100-0: 掲示は <session>.<askId>.json（hook と同じ形）。ask_id=None は旧名 <session>.json（移行窓）。"""
    d = Path(home) / ".claude" / "office_approvals"
    d.mkdir(parents=True, exist_ok=True)
    if ask_id == "auto":
        ask_id = aid(session)
    rec = {"session": session, "tool": "Bash", "kind": "permission",
           "title": "rm -rf /Users/someone/secret", "options": [],
           "cwd": "/tmp", "ts": time.time(), "deadline": time.time() + 600, "pid": 1}
    if ask_id:
        rec["askId"] = ask_id
    rec.update(kw)
    f = d / (f"{session}.{ask_id}.json" if ask_id else f"{session}.json")
    f.write_text(json.dumps(rec, ensure_ascii=False), encoding="utf-8")
    return f


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

    # ── R86-H2: 心拍（mtime）が生死の一次情報 ──────────────────────────
    def test_stale_heartbeat_is_a_ghost(self):
        """deadline が未来でも、心拍（mtime）が ASK_STALE より古い掲示は幽霊。
        人間が TUI で答えると Claude Code は hook を kill し finally は走らない＝掲示だけが残る（実測 3 件）。"""
        p = publish(self.home, "s1", ts=time.time() - 30)
        now = time.time()
        os.utime(p, (now - 30, now - 30))
        self.assertIsNone(self.office.pending_approval("s1"), "心拍が止まった掲示を生きていると言った")
        os.utime(p, None)
        self.assertIsNotNone(self.office.pending_approval("s1"), "心拍が戻ったのに幽霊のまま")

    def test_ask_stale_is_a_few_seconds(self):
        """❗が消えるまでの上限（心拍 10 周＋スリープ復帰のジッタ）。広げるなら理由を書く。"""
        self.assertGreaterEqual(self.office.ASK_STALE, 5)
        self.assertLessEqual(self.office.ASK_STALE, 15)

    def test_ghost_posting_does_not_raise_attention(self):
        sess = "sess-ghost001"
        proj = Path(self.home) / ".claude/projects/-tmp-demo"
        proj.mkdir(parents=True, exist_ok=True)
        f = proj / f"{sess}.jsonl"
        f.write_text(json.dumps({"type": "assistant", "cwd": "/tmp/demo", "message": {
            "role": "assistant", "content": [{"type": "text", "text": "done"}]}}) + "\n",
            encoding="utf-8")
        p = publish(self.home, sess, kind="question", tool="AskUserQuestion", ts=time.time() - 40,
                    title="幽霊?", options=["A", "B"])
        os.utime(p, (time.time() - 40, time.time() - 40))
        e = self.office.parse_session(f, time.time())
        self.assertIsNone(e.get("ask"), "幽霊の掲示で ask を立てた")
        self.assertEqual(e.get("approvalMin", 0), 0)
        self.assertNotEqual(e.get("state"), "waiting")

    def test_killed_hook_becomes_ghost_after_ask_stale(self):
        """実 hook を SIGKILL（Claude Code が TUI 回答時にやる形）→ 掲示は残るが ASK_STALE 後に幽霊。"""
        import signal, subprocess
        hook = Path(__file__).resolve().parents[1] / "hooks" / "office-approval-wait.sh"
        sess = "sess-killme001"
        env = dict(os.environ, OFFICE_HOME=self.home, OFFICE_APPROVAL_WAIT="30", OFFICE_APPROVAL_POLL="0.1")
        p = subprocess.Popen(["bash", str(hook)], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE, text=True, env=env, start_new_session=True)
        p.stdin.write(json.dumps({"session_id": sess, "cwd": "/tmp/demo", "hook_event_name": "PermissionRequest",
                                  "tool_name": "Bash", "tool_input": {"command": "git push"},
                                  "transcript_path": "/nonexistent"})); p.stdin.close(); p.stdin = None
        d = Path(self.home) / ".claude/office_approvals"
        end = time.time() + 8
        pend = None
        while time.time() < end and pend is None:
            pend = next((f for f in (d.glob(f"{sess}.*.json") if d.is_dir() else [])
                         if ".reply." not in f.name), None)
            time.sleep(0.05)
        self.assertTrue(pend and pend.exists(), "掲示が出ない")
        try:
            self.assertIsNotNone(self.office.pending_approval(sess), "生きている hook の掲示が幽霊扱い")
            os.killpg(os.getpgid(p.pid), signal.SIGKILL); p.wait(timeout=5)
            time.sleep(0.3)
            self.assertTrue(pend.exists())
            self.assertIsNone(self.office.pending_approval(sess, now=time.time() + self.office.ASK_STALE + 1),
                              "kill された hook の掲示が ASK_STALE 後も生きている")
        finally:
            try:
                os.killpg(os.getpgid(p.pid), signal.SIGKILL)
            except Exception:
                pass

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
        rec = self.office.write_approval_reply("s1", "allow", "", src="local", ask_id=aid("s1"))
        p = Path(self.home) / f".claude/office_approvals/s1.{aid('s1')}.reply.json"
        self.assertEqual(oct(os.stat(p).st_mode)[-3:], "600")
        self.assertEqual(json.loads(p.read_text())["behavior"], "allow")
        self.assertEqual(json.loads(p.read_text())["askId"], aid("s1"))
        self.assertEqual(rec["src"], "local")
        self.assertFalse(list(p.parent.glob(".*tmp")), "一時ファイルが残っている")

    def test_reply_rejects_bad_session(self):
        with self.assertRaises(ValueError):
            self.office.write_approval_reply("../evil", "allow", "", src="local", ask_id=aid("x"))

    # ── R100-0: 1 件ごとの掲示と回答 ────────────────────────────────
    def test_reply_needs_src_and_ask_id_by_keyword(self):
        """既定値が local だと、呼び忘れた経路が黙って『Mac の前の人間』になる＝既定値を持たない。"""
        for call in (lambda: self.office.write_approval_reply("s1", "allow", ""),
                     lambda: self.office.write_approval_reply("s1", "allow", "", src="local"),
                     lambda: self.office.write_approval_reply("s1", "allow", "", ask_id=aid("s1")),
                     lambda: self.office.write_approval_reply("s1", "allow", "", "local", aid("s1"))):
            with self.assertRaises(TypeError):
                call()

    def test_reply_rejects_bad_ask_id_and_remote_without_ask_id(self):
        for bad in ("", "../x", "A" * 32, aid("s1")[:31], aid("s1") + "0", 123):
            with self.assertRaises(ValueError, msg=repr(bad)):
                self.office.write_approval_reply("s1", "deny", "x", src="local", ask_id=bad)
        with self.assertRaises(ValueError):
            self.office.write_approval_reply("s1", "allow", "", src="remote", ask_id=None)

    def test_legacy_reply_name_only_without_ask_id(self):
        """移行窓: 配備の瞬間に待っていた旧 hook（<sid>.json）宛ては旧名 <sid>.reply.json に書く。"""
        publish(self.home, "s1", ask_id=None)
        self.office.write_approval_reply("s1", "deny", "x", src="relay", ask_id=None)
        d = Path(self.home) / ".claude/office_approvals"
        self.assertTrue((d / "s1.reply.json").exists())
        self.assertNotIn("askId", json.loads((d / "s1.reply.json").read_text()))

    def test_parallel_postings_are_listed_oldest_first(self):
        now = time.time()
        publish(self.home, "s1", ask_id=aid("s1", 2), ts=now - 1, title="後")
        publish(self.home, "s1", ask_id=aid("s1", 1), ts=now - 5, title="先")
        publish(self.home, "s1", ask_id=None, ts=now - 9, title="旧")
        asks = self.office.pending_approvals("s1")
        self.assertEqual([a["title"] for a in asks], ["旧", "先", "後"])
        self.assertEqual([a["askId"] for a in asks], [None, aid("s1", 1), aid("s1", 2)])
        one = self.office.pending_approval("s1")
        self.assertEqual((one["title"], one["askCount"]), ("旧", 3))
        publish(self.home, "s10", title="別セッション")      # 前方一致の別セッションを混ぜない
        self.assertEqual(len(self.office.pending_approvals("s1")), 3)

    def test_posting_whose_ask_id_disagrees_with_its_name_is_ignored(self):
        publish(self.home, "s1", askId=aid("s1", 9))
        self.assertIsNone(self.office.pending_approval("s1"))
        publish(self.home, "s2", ask_id=None, askId=aid("s2"))   # 旧名なのに askId を名乗る
        self.assertIsNone(self.office.pending_approval("s2"))

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

    GOOD_QS = [{"q": "どの順で直しますか?", "options": ["server から", "PWA から"], "multi": 0},
               {"q": "どれを含めますか?", "options": ["hook", "server", "relay"], "multi": 1},
               {"q": "選択肢の無い問い", "options": [], "multi": 0}]

    def test_question_posting_carries_every_question(self):
        """R112-H: 掲示の questions（全部の問いと選択肢）を office_json の ask へ写す（1 問目の互換はそのまま）。"""
        publish(self.home, "s1", kind="question", tool="AskUserQuestion", title="どの順で直しますか?",
                options=["server から", "PWA から"], qn=3, multi=1,
                questions=[dict(q, extra="写さない") for q in self.GOOD_QS])
        a = self.office.pending_approval("s1")
        self.assertEqual(a["questions"], self.GOOD_QS)                  # 知らないキー（extra）は写さない
        self.assertEqual((a["title"], a["options"], a["qn"], a["multi"]),
                         ("どの順で直しますか?", ["server から", "PWA から"], 3, 1))

    def test_broken_questions_add_nothing(self):
        """形が違う questions は何も足さない（1 件でも違えば全部落とす＝番号のずれた一部を見せない）。"""
        good = self.GOOD_QS[0]
        broken = [
            "文字列",                                                    # list でない
            [],                                                          # 空
            ["文字列の問い"],                                            # 要素が dict でない
            [dict(good, options=[{"label": "A"}])],                      # options が dict の list
            [dict(good, options={"label": "A"})],                        # options が dict
            [dict(good, q="長" * 300)],                                  # q が 300 字
            [dict(good, q="")],                                          # 空の問い
            [dict(good, q=12)],                                          # q が文字列でない
            [dict(good, options=["選" * 41])],                           # 選択肢が 40 字超
            [dict(good, options=["a", "b", "c", "d", "e"])],             # 選択肢が 5 個
            [dict(good, options=["a", ""])],                             # 空の選択肢
            [dict(good, multi=True)],                                    # multi が bool
            [dict(good, multi=2)],                                       # multi が 0/1 でない
            [{"q": "multi が無い", "options": []}],
            [good] * 9,                                                  # 9 件
            [good, "壊れた 2 問目"],                                     # 一部だけ壊れている
        ]
        for i, qs in enumerate(broken):
            sess = "sb%02d" % i
            publish(self.home, sess, kind="question", tool="AskUserQuestion", title="x", questions=qs)
            a = self.office.pending_approval(sess)
            self.assertIsNotNone(a, qs)
            self.assertNotIn("questions", a, f"壊れた questions が通った: {qs!r}")
        self.assertEqual(self.office.clean_ask_questions([good] * 8), [good] * 8)   # 8 件は通る
        self.assertEqual(self.office.clean_ask_questions([dict(good, q="長" * 200)])[0]["q"], "長" * 200)

    def test_questions_only_for_ask_user_question(self):
        """許可（Bash）と計画の承認（ExitPlanMode）の掲示に questions があっても写さない（本文を広げない）。"""
        publish(self.home, "s1", kind="permission", tool="Bash", questions=self.GOOD_QS)
        publish(self.home, "s2", kind="question", tool="ExitPlanMode", title="プランの承認", questions=self.GOOD_QS)
        for s in ("s1", "s2"):
            self.assertNotIn("questions", self.office.pending_approval(s), s)

    def test_shown_question_is_the_one_being_answered(self):
        """R100-0（Codex レビュー）: 質問 A（最古の掲示）と B（transcript の最新）が並ぶと、答える先は A の askId なのに
        画面には B が出ていた。掲示がある間は、表示する質問も掲示の最古の 1 件に揃える。"""
        sess = "sess-live3333"
        proj = Path(self.home) / ".claude/projects/-tmp-demo"
        proj.mkdir(parents=True, exist_ok=True)
        f = proj / f"{sess}.jsonl"
        old_ts = time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime(time.time() - 120))
        f.write_text(json.dumps({"type": "assistant", "cwd": "/tmp/demo", "timestamp": old_ts, "message": {
            "role": "assistant", "content": [
                {"type": "tool_use", "name": "AskUserQuestion", "input": {"questions": [
                    {"question": "B の質問（あとから来た）", "options": [{"label": "B1"}, {"label": "B2"}]}]}}]}}) + "\n",
            encoding="utf-8")
        os.utime(f, (time.time() - 120, time.time() - 120))          # 75 秒を超えて止まっている＝transcript の質問が立つ
        publish(self.home, sess, ask_id=aid(sess, 1), kind="question", tool="AskUserQuestion", ts=time.time() - 9,
                title="A の質問（先に来た）", options=["A1", "A2"])
        publish(self.home, sess, ask_id=aid(sess, 2), kind="question", tool="AskUserQuestion", ts=time.time() - 4,
                title="B の質問（あとから来た）", options=["B1", "B2"])
        e = self.office.parse_session(f, time.time())
        self.assertEqual(e["ask"]["askId"], aid(sess, 1))
        self.assertEqual(e["question"], "A の質問（先に来た）", "答える先（A）と違う質問（B）を見せた")
        self.assertEqual([o["label"] for o in e["questionOptions"]], ["A1", "A2"])

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
        pend = publish(self.home, sess, ts=time.time() - 5 * 3600, deadline=time.time() + 600)
        emps = self.office.scan_office()["employees"]
        me = [e for e in emps if e["session"] == sess]
        self.assertTrue(me, "承認まちのセッションが出勤窓から落ちて画面から消えた")
        self.assertEqual(me[0]["state"], "waiting", "人間待ちなのに休憩中に見える")
        # 掲示が無ければ従来どおり退勤扱い（幽霊社員の対策は生きている）
        pend.unlink()
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

    def test_prunes_dead_postings_and_replies_only(self):
        """R86-H2: 承認の掲示板。死んだ掲示（心拍 >1h）と古い回答/claim/tmp（>10min）だけ消し、
        生きた掲示・新しい回答・`_` 始まり（_e2e.log）は残す。"""
        d = Path(self.home) / ".claude" / "office_approvals"
        d.mkdir(parents=True, exist_ok=True)
        def aged(name, age, body="{}"):
            p = d / name
            p.write_text(body, encoding="utf-8")
            os.utime(p, (time.time() - age, time.time() - age))
            return p
        dead = aged("s-dead.json", 2 * 3600)
        live = aged("s-live.json", 5)
        old_reply = aged("s-old.reply.json", 20 * 60)
        fresh_reply = aged("s-fresh.reply.json", 30)
        tmp = aged(".s-x.reply.tmp", 20 * 60)
        claim = aged("s-y.reply.json.taken.1", 20 * 60)
        log = aged("_e2e.log", 30 * 86400, "x")
        # R100-0: 1 件ごとの名前（<sid>.<askId>.json・.reply.json・hook と daemon の一時ファイル）も同じ掟で消す
        a = "0123456789abcdef" * 2
        dead2 = aged(f"s-dead.{a}.json", 2 * 3600)
        live2 = aged(f"s-live.{a}.json", 5)
        old_reply2 = aged(f"s-old.{a}.reply.json", 20 * 60)
        fresh_reply2 = aged(f"s-fresh.{a}.reply.json", 30)
        tmp2 = aged(f".s-x.{a}.reply.json.tmp", 20 * 60)
        tmp3 = aged(f"s-x.{a}.json.tmp", 20 * 60)
        self.office._LAST_PRUNE[0] = 0
        n = self.office.prune_inbox_litter()
        for f in (dead2, old_reply2, tmp2, tmp3):
            self.assertFalse(f.exists(), f"{f.name} が残った")
        for f in (live2, fresh_reply2):
            self.assertTrue(f.exists(), f"{f.name} を消した")
        self.assertEqual(n, 8, "消す数が違う")
        for p in (dead, old_reply, tmp, claim):
            self.assertFalse(p.exists(), p.name)
        for p in (live, fresh_reply, log):
            self.assertTrue(p.exists(), p.name)

    def test_prunes_approvals_even_without_an_inbox(self):
        """初回の PermissionRequest だけが起きた環境: office_inbox が無くても掲示板は掃除する。"""
        import shutil
        shutil.rmtree(self.inbox)
        d = Path(self.home) / ".claude" / "office_approvals"
        d.mkdir(parents=True, exist_ok=True)
        dead = d / "s-dead.json"
        dead.write_text("{}", encoding="utf-8")
        os.utime(dead, (time.time() - 2 * 3600, time.time() - 2 * 3600))
        self.office._LAST_PRUNE[0] = 0
        self.assertEqual(self.office.prune_inbox_litter(), 1)
        self.assertFalse(dead.exists())

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
                                  "options": [], "askId": aid("s1"), "askCount": 2}}
        self.ra._redact_entry_for_relay(e)
        # R100-1b: remote（0/1）だけ足した＝全文は登録した端末へ封書で（意図した更新）
        # R102-11: 掲示の識別 id8（askId の先頭 8 桁＝hook の乱数）だけは載る（スマホが答え先を指す）。本文は載らない
        self.assertEqual(e["ask"], {"tool": "Bash", "kind": "permission", "remote": 0, "id8": e["ask"].get("id8")})
        self.assertRegex(e["ask"]["id8"], r"^[0-9a-f]{8}$")
        self.assertNotIn("secret", json.dumps(e))

    def test_relay_answer_is_never_local(self):
        """スマホ経由の回答は src="relay"。フックはこれを allow に昇格させない。"""
        rec = self.office.write_approval_reply("s1", "allow", "はい", src="relay", ask_id=aid("s1"))
        self.assertEqual(rec["src"], "relay")

    def test_permission_answer_explains_it_did_not_run(self):
        msg = self.ra._answer_text({"kind": "permission"}, "はい、承認します")
        self.assertIn("はい、承認します", msg)
        self.assertIn("ターミナル", msg)
        # 質問への回答は素の文言のまま（余計な但し書きで選択肢の判定を壊さない）
        self.assertEqual(self.ra._answer_text({"kind": "question"}, "B案"), "B案")


class ReplyApiTest(unittest.TestCase):
    """R100-0: POST /api/approval/reply は askId で 1 件を指す（表示と違う要求に効かせない）。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = self.tmp.name
        # 通知と物理確認の注入口は**元に戻す形で**差し替える（pop すると、import 時に同じ変数を置いた
        # 別のテスト（test_hire の入社通知）の設定まで消える＝verify で実際に踏んだ）。
        self.notify = Path(self.home) / "notify.txt"
        env = patch.dict(os.environ, {"OFFICE_FAKE_NOTIFY": str(self.notify)})
        env.start()
        self.addCleanup(env.stop)
        self.office = _reload(self.home)
        self.d = Path(self.home) / ".claude/office_approvals"

    def tearDown(self):
        self.tmp.cleanup()

    def post(self, body, headers=None):
        raw = json.dumps(body).encode()
        h = self.office.Handler.__new__(self.office.Handler)
        h.path = "/api/approval/reply"
        h.headers = {"Host": "127.0.0.1:4780", "X-Office-Local": "1", "Origin": "http://127.0.0.1:4780",
                     "Content-Length": str(len(raw)), **(headers or {})}
        h.client_address = ("127.0.0.1", 12345)
        h.rfile = io.BytesIO(raw)
        h._send = Mock()
        h.do_POST()
        code, out, _ = h._send.call_args.args
        return code, json.loads(out)

    def replies(self):
        return sorted(f.name for f in self.d.glob("*.reply.json"))

    def test_answer_goes_to_the_named_request_only(self):
        now = time.time()
        publish(self.home, "s1", ask_id=aid("s1", 1), ts=now - 5)
        publish(self.home, "s1", ask_id=aid("s1", 2), ts=now - 1)
        code, out = self.post({"session": "s1", "behavior": "allow", "askId": aid("s1", 2)})
        self.assertEqual(code, 200, out)
        self.assertEqual(out.get("askId"), aid("s1", 2))
        self.assertEqual(self.replies(), [f"s1.{aid('s1', 2)}.reply.json"])

    def test_missing_or_finished_request_is_refused(self):
        publish(self.home, "s1", ask_id=aid("s1", 1))
        code, out = self.post({"session": "s1", "behavior": "allow"})
        self.assertEqual(code, 400, "askId の無い allow が新しい掲示に効いた")
        code, out = self.post({"session": "s1", "behavior": "allow", "askId": aid("s1", 7)})
        self.assertEqual(code, 400, "終わった要求への allow が通った")
        self.assertIn("終わって", out.get("message", "") + out.get("msg", "") + json.dumps(out, ensure_ascii=False))
        self.assertEqual(self.replies(), [])

    def test_answering_requires_a_browser_origin(self):
        """R100-0: loopback＋X-Office-Local は同じユーザーの任意のプロセス（curl でも付く）。
        承認への回答は Origin 必須（loopback）＋ Sec-Fetch-Site が在れば same-origin。"""
        publish(self.home, "s1", ask_id=aid("s1", 1))
        body = {"session": "s1", "behavior": "allow", "askId": aid("s1", 1)}
        for headers, why in (({"Origin": ""}, "Origin 無し"),
                             ({"Origin": "https://evil.example"}, "別オリジン"),
                             ({"Sec-Fetch-Site": "cross-site"}, "cross-site"),
                             ({"Sec-Fetch-Site": "same-site"}, "same-site"),
                             ({"X-Office-Local": ""}, "X-Office-Local 無し")):
            code, _ = self.post(body, headers)
            self.assertEqual(code, 403, why)
        self.assertEqual(self.replies(), [], "拒否したのに回答を書いた")
        code, _ = self.post(body, {"Sec-Fetch-Site": "same-origin"})
        self.assertEqual(code, 200)

    def test_strict_gate_follows_the_prefix_routing(self):
        """振り分けは self.path の前方一致＝末尾に文字を足したパス・クエリ付きも Origin 無しは 403。"""
        for path in ("/api/pair/newX", "/api/pair/new/", "/api/pair/revoke2", "/api/recipes/set?x=1",
                     "/api/config/dialog_relay_", "/api/approval/reply?askId=1"):
            raw = b"{}"
            h = self.office.Handler.__new__(self.office.Handler)
            h.path = path
            h.headers = {"Host": "127.0.0.1:4780", "X-Office-Local": "1", "Content-Length": "2"}
            h.client_address = ("127.0.0.1", 12345)
            h.rfile = io.BytesIO(raw)
            h._send = Mock()
            h.do_POST()
            self.assertEqual(h._send.call_args.args[0], 403, path)

    def test_local_allow_notifies_the_mac(self):
        """R100-0: Mac での allow は必ず通知に残す（同じユーザーの別プロセスが黙って許可しても気づける）。"""
        publish(self.home, "s1", ask_id=aid("s1", 1), title="git push origin main")
        self.assertEqual(self.post({"session": "s1", "behavior": "allow", "askId": aid("s1", 1)})[0], 200)
        text = self.notify.read_text(encoding="utf-8")
        self.assertIn("git push origin main", text)
        self.assertIn("Bash", text)

    def test_pairing_needs_a_physical_confirm_and_notifies(self):
        """R100-0: スマホの登録（中継から指示を出せる端末を増やす）は Mac の前での確認が要る。キャンセルなら発行しない。"""
        devs = Path(self.home) / ".claude/office_devices.json"
        h = {"Origin": "http://127.0.0.1:4780"}
        for fake, want in (("cancel", False), ("ok", True)):
            with patch.dict(os.environ, {"OFFICE_FAKE_CONFIRM": fake}):
                raw = json.dumps({"label": "テスト端末"}).encode()
                hd = self.office.Handler.__new__(self.office.Handler)
                hd.path = "/api/pair/new"
                hd.headers = {"Host": "127.0.0.1:4780", "X-Office-Local": "1", "Content-Length": str(len(raw)), **h}
                hd.client_address = ("127.0.0.1", 12345)
                hd.rfile = io.BytesIO(raw)
                hd._send = Mock()
                hd.do_POST()
                code, out, _ = hd._send.call_args.args
            has = devs.exists() and json.loads(devs.read_text()).get("devices")
            self.assertEqual(bool(has), want, f"{fake}: 台帳の状態が違う")
            self.assertEqual(code == 200, want, f"{fake}: {out!r}")
        self.assertIn("テスト端末", self.notify.read_text(encoding="utf-8"))

    def test_legacy_posting_accepts_an_answer_without_ask_id(self):
        publish(self.home, "s1", ask_id=None)
        code, out = self.post({"session": "s1", "behavior": "deny", "message": "B で"})
        self.assertEqual(code, 200, out)
        self.assertEqual(self.replies(), ["s1.reply.json"])


if __name__ == "__main__":
    unittest.main()
