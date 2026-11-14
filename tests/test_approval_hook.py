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
import re
import signal
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
    # R86-H2: 自分のプロセスグループで起動する＝killpg で bash と python をまとめて殺せる
    # （Claude Code が TUI の回答時にやるのと同じ形。片方だけ殺すと python 子が孤児になる）。
    p = subprocess.Popen(["bash", str(HOOK)], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                         stderr=subprocess.PIPE, text=True, env=env, start_new_session=True)
    p.stdin.write(json.dumps(payload))
    p.stdin.close()
    p.stdin = None          # communicate() が閉じた stdin を触らないように
    return p


def kill_group(p, sig):
    try:
        os.killpg(os.getpgid(p.pid), sig)
    except ProcessLookupError:
        pass


_OFFICE = []


def _office(home):
    """office_server（掲示の questions の正本 clean_ask_questions を持つ）。sys.modules の office_server は
    ほかのテストが OFFICE_HOME ごと読み直すので、別名で 1 回だけ読む（純関数しか使わない・読む間は home を HOME に）。"""
    if not _OFFICE:
        import importlib.util
        prev = os.environ.get("OFFICE_HOME")
        os.environ["OFFICE_HOME"] = str(home)          # 本物の HOME を読ませない
        try:
            spec = importlib.util.spec_from_file_location("office_server_r112h", ROOT / "server" / "office_server.py")
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
        finally:
            if prev is None:
                os.environ.pop("OFFICE_HOME", None)
            else:
                os.environ["OFFICE_HOME"] = prev
        _OFFICE.append(mod)
    return _OFFICE[0]


def approvals(home):
    return Path(home) / ".claude" / "office_approvals"


def wait_for(path, timeout=8.0):
    end = time.time() + timeout
    while time.time() < end:
        if os.path.exists(path):
            return True
        time.sleep(0.05)
    return False


ASK_NAME = re.compile(r"(?P<sid>[A-Za-z0-9-]+)\.(?P<ask>[0-9a-f]{32})\.json")


def postings(home, session="sess-aaaa1111"):
    """R100-0: 掲示は <session>.<askId>.json（1 件ごと）。"""
    d = approvals(home)
    if not d.is_dir():
        return []
    return sorted(f for f in d.iterdir()
                  if (m := ASK_NAME.fullmatch(f.name)) and m.group("sid") == session)


def wait_posting(home, session="sess-aaaa1111", timeout=8.0, besides=()):
    """掲示が出るまで待って、その Path を返す（besides にある掲示は除く＝2 本目を待つ）。"""
    end = time.time() + timeout
    while time.time() < end:
        fresh = [f for f in postings(home, session) if f not in besides]
        if fresh:
            return fresh[0]
        time.sleep(0.05)
    return None


def ask_id_of(pend):
    return ASK_NAME.fullmatch(Path(pend).name).group("ask")


def write_reply(home, session, ask_id=None, name=None, fields=None, **kw):
    """回答を置く。ask_id を省くと、いま出ている唯一の掲示の askId に宛てる。"""
    if ask_id is None:
        found = postings(home, session)
        assert len(found) == 1, f"掲示が 1 件でない: {found}"
        ask_id = ask_id_of(found[0])
    rec = {"session": session, "askId": ask_id, "ts": time.time()}
    rec.update(kw)
    rec.update(fields or {})          # 中身だけ変える（名前はそのまま）＝偽の回答の検査用
    p = approvals(home) / (name or f"{session}.{ask_id}.reply.json")
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
        pend = wait_posting(self.home)
        self.assertTrue(pend, "掲示ファイルが出ない")
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
        pend = wait_posting(self.home)
        self.assertTrue(pend)
        rec = json.loads(pend.read_text())
        self.assertEqual(rec["kind"], "question")
        self.assertEqual(rec["title"], "どちらで進めますか?")
        self.assertEqual(rec["options"], ["A案", "B案"])
        self.assertEqual((rec["qn"], rec["multi"]), (1, 0))
        p.kill(); p.wait(timeout=10); p.stdout.close(); p.stderr.close()

    def test_question_payload_carries_count_and_multiselect(self):
        """R102-11: 2 問以上・複数選択はスマホで 1 タップにしない＝hook が掲示に問いの数と複数選択を書く（推測しない）。"""
        pl = dict(BASH_PAYLOAD, tool_name="AskUserQuestion", tool_input={"questions": [
            {"question": "どちらで進めますか?", "options": [{"label": "A案"}, {"label": "B案"}]},
            {"question": "どれを含めますか?", "multiSelect": True, "options": [{"label": "x"}, {"label": "y"}]}]})
        p = spawn_hook(pl, self.home)
        pend = wait_posting(self.home)
        self.assertTrue(pend)
        rec = json.loads(pend.read_text())
        self.assertEqual((rec["qn"], rec["multi"]), (2, 1))
        p.kill(); p.wait(timeout=10); p.stdout.close(); p.stderr.close()

    def _posting_of(self, payload):
        """hook を起動して掲示 1 件を読み、止めて返す（掲示の形だけを見るテスト用）。"""
        home = tempfile.mkdtemp(dir=self.home)
        p = spawn_hook(payload, home)
        try:
            pend = wait_posting(home)
            self.assertTrue(pend, "掲示が出ない（hook が例外で落ちた？）")
            return json.loads(pend.read_text())
        finally:
            kill_group(p, signal.SIGKILL)
            p.wait(timeout=10); p.stdout.close(); p.stderr.close()

    def test_question_payload_carries_every_question(self):
        """R112-H（本人の報告 2026-10-07「1、2、3 の選択肢でもスマホから答えられない」）: 掲示は 1 問目しか持たず、
        2〜4 問を束ねた質問はスマホで答えられなかった。全部の問いと選択肢を questions に書く（1 問目の互換は今のまま）。"""
        pl = dict(BASH_PAYLOAD, tool_name="AskUserQuestion", tool_input={"questions": [
            {"question": "どの順で直しますか?", "header": "順序",
             "options": [{"label": "server から", "description": "本文の説明（載せない）"}, {"label": "PWA から"}]},
            {"question": "どれを含めますか?", "multiSelect": True,
             "options": [{"label": "hook"}, {"label": "server"}, {"label": "relay"}, {"label": "docs"}, {"label": "5 個目"}]},
            {"header": "問いの文が無ければ header", "options": ["素の文字列", "  畳む\n空白  "]}]})
        rec = self._posting_of(pl)
        # 1 問目の互換（古いスマホ）はそのまま
        self.assertEqual((rec["title"], rec["options"], rec["qn"], rec["multi"]),
                         ("どの順で直しますか?", ["server から", "PWA から"], 3, 1))
        qs = rec["questions"]
        self.assertEqual(len(qs), 3)
        self.assertEqual([q["multi"] for q in qs], [0, 1, 0])
        self.assertEqual([q["q"] for q in qs], ["どの順で直しますか?", "どれを含めますか?", "問いの文が無ければ header"])
        self.assertEqual(qs[0]["options"], ["server から", "PWA から"])
        self.assertEqual(qs[1]["options"], ["hook", "server", "relay", "docs"])        # 4 個まで
        self.assertEqual(qs[2]["options"], ["素の文字列", "畳む 空白"])
        self.assertEqual(set(qs[0]), {"q", "options", "multi"})
        self.assertNotIn("本文の説明", json.dumps(rec, ensure_ascii=False), "description を掲示に載せた")
        # 正本の検証（office_server.clean_ask_questions）をそのまま通る形＝hook と daemon の契約
        self.assertEqual(_office(self.home).clean_ask_questions(qs), qs)

    def test_question_payload_caps_lengths_and_count(self):
        """9 問目以降は切る（qn は実数のまま）・問い 200 字・選択肢 40 字・空の問いは落とす・壊れた形で掲示ごと落とさない。"""
        many = [{"question": f"問い{i}", "options": [{"label": f"選択{i}"}]} for i in range(1, 10)]
        rec = self._posting_of(dict(BASH_PAYLOAD, tool_name="AskUserQuestion", tool_input={"questions": many}))
        self.assertEqual(rec["qn"], 9)
        self.assertEqual([q["q"] for q in rec["questions"]], [f"問い{i}" for i in range(1, 9)])
        odd = [{"question": "長" * 300, "options": [{"label": "選" * 60}, {"label": ""}, {"nolabel": 1}, 7]},
               {"question": "", "header": "", "options": [{"label": "空の問い"}]},
               "文字列の問い",
               {"question": "options が dict", "options": {"label": "x"}}]
        rec = self._posting_of(dict(BASH_PAYLOAD, tool_name="AskUserQuestion", tool_input={"questions": odd}))
        self.assertEqual(rec["qn"], 4)
        qs = rec["questions"]
        self.assertEqual(len(qs), 2, qs)                      # 空の問い・文字列の問いは落ちる
        self.assertEqual((len(qs[0]["q"]), qs[0]["q"][-1]), (200, "…"))
        self.assertEqual(qs[0]["options"], ["選" * 39 + "…"])
        self.assertEqual(qs[1], {"q": "options が dict", "options": [], "multi": 0})
        self.assertEqual(_office(self.home).clean_ask_questions(qs), qs)

    def test_questions_only_for_ask_user_question(self):
        """ExitPlanMode（プランの承認）と許可（Bash）には questions を付けない（許可の本文を広げない）。"""
        for pl in (dict(BASH_PAYLOAD, tool_name="ExitPlanMode", tool_input={"plan": "手順を3つに分けます"}),
                   BASH_PAYLOAD,
                   dict(BASH_PAYLOAD, tool_name="AskUserQuestion", tool_input={"questions": "壊れた形"})):
            rec = self._posting_of(pl)
            self.assertNotIn("questions", rec, pl["tool_name"])

    def test_write_tool_publishes_basename_only(self):
        pl = dict(BASH_PAYLOAD, tool_name="Write",
                  tool_input={"file_path": "/Users/someone/secret/plan.md"})
        p = spawn_hook(pl, self.home)
        pend = wait_posting(self.home)
        self.assertTrue(pend)
        rec = json.loads(pend.read_text())
        self.assertEqual(rec["title"], "plan.md")
        # R102-2: この Write は cwd の外への書き込み＝review（スマホで全文を読んで 2 段）＝全文（inputCanon）は 0600 の掲示に置く。
        # 題名など全文以外の欄には basename しか出さない（中継へ出るのは daemon が端末の鍵で封じた分だけ）
        rest = {k: v for k, v in rec.items() if k != "inputCanon"}
        self.assertNotIn("secret", json.dumps(rest, ensure_ascii=False))
        p.kill(); p.wait(timeout=10); p.stdout.close(); p.stderr.close()

    def test_exit_plan_mode_is_a_question(self):
        pl = dict(BASH_PAYLOAD, tool_name="ExitPlanMode", tool_input={"plan": "..."})
        p = spawn_hook(pl, self.home)
        pend = wait_posting(self.home)
        self.assertTrue(pend)
        self.assertEqual(json.loads(pend.read_text())["kind"], "question")
        p.kill(); p.wait(timeout=10); p.stdout.close(); p.stderr.close()

    # ── 回答の受理と拒否 ──────────────────────────────────────────────
    def test_local_allow_is_honored(self):
        p = spawn_hook(BASH_PAYLOAD, self.home)
        self.assertTrue(wait_posting(self.home))
        write_reply(self.home, "sess-aaaa1111", behavior="allow", src="local")
        out, _ = p.communicate(timeout=25)
        self.assertEqual(decision(out), {"behavior": "allow"})

    def test_relay_allow_is_downgraded_to_deny(self):
        """★安全境界: スマホからの『許可』は実行を通さない（言葉だけ届く）。"""
        p = spawn_hook(BASH_PAYLOAD, self.home)
        self.assertTrue(wait_posting(self.home))
        write_reply(self.home, "sess-aaaa1111", behavior="allow", src="relay",
                    message="スマホから承認しました")
        out, _ = p.communicate(timeout=25)
        d = decision(out)
        self.assertEqual(d["behavior"], "deny")
        self.assertIn("スマホから承認しました", d["message"])

    def test_missing_src_never_allows(self):
        p = spawn_hook(BASH_PAYLOAD, self.home)
        self.assertTrue(wait_posting(self.home))
        write_reply(self.home, "sess-aaaa1111", behavior="allow", message="ok")
        out, _ = p.communicate(timeout=25)
        self.assertEqual(decision(out)["behavior"], "deny")

    def test_deny_with_message_is_the_answer_channel(self):
        p = spawn_hook(BASH_PAYLOAD, self.home)
        self.assertTrue(wait_posting(self.home))
        write_reply(self.home, "sess-aaaa1111", behavior="deny", src="relay",
                    message="B案でお願いします")
        out, _ = p.communicate(timeout=25)
        self.assertEqual(decision(out), {"behavior": "deny", "message": "B案でお願いします"})

    def test_reply_is_consumed_exactly_once(self):
        """回答は原子的に取る＝同じ回答が二度効かない（取った後に回答ファイルが残らない）。"""
        p = spawn_hook(BASH_PAYLOAD, self.home, wait=4)
        pend = wait_posting(self.home)
        self.assertTrue(pend)
        r = write_reply(self.home, "sess-aaaa1111", behavior="deny", src="local", message="一度だけ")
        out, _ = p.communicate(timeout=25)
        self.assertEqual(decision(out), {"behavior": "deny", "message": "一度だけ"})
        self.assertFalse(r.exists(), "取った回答が残っている（二度目に効く）")
        self.assertEqual(list(approvals(self.home).glob("*.taken.*")), [], "取った回答の抜け殻が残った")

    def test_parallel_requests_never_swap_answers(self):
        """★R100-0（実測で再現した穴）: 同じセッションで 2 件並ぶと、単一枠の掲示は後の要求で上書きされ、
        Mac で『表示された方』に出した allow が表示されていない方に効いた。回答は askId の 1 件にしか効かない。"""
        pa = spawn_hook(dict(BASH_PAYLOAD, tool_input={"command": "echo A"}), self.home, wait=8)
        a = wait_posting(self.home)
        self.assertTrue(a)
        pb = spawn_hook(dict(BASH_PAYLOAD, tool_input={"command": "echo B"}), self.home, wait=8)
        b = wait_posting(self.home, besides=(a,))
        self.assertTrue(b, "2 件目の掲示が 1 件目を上書きした（単一枠）")
        self.assertTrue(a.exists(), "2 件目の掲示で 1 件目が消えた")
        self.assertEqual(json.loads(a.read_text())["title"], "echo A")
        self.assertEqual(json.loads(b.read_text())["title"], "echo B")
        self.assertEqual(json.loads(a.read_text())["askId"], ask_id_of(a))
        write_reply(self.home, "sess-aaaa1111", ask_id=ask_id_of(b), behavior="allow", src="local")
        ob, _ = pb.communicate(timeout=25)
        self.assertEqual(decision(ob), {"behavior": "allow"}, "B への allow が B に効かない")
        self.assertTrue(a.exists(), "B への回答で A が降りた")
        write_reply(self.home, "sess-aaaa1111", ask_id=ask_id_of(a), behavior="deny", src="local",
                    message="A はやめて")
        oa, _ = pa.communicate(timeout=25)
        self.assertEqual(decision(oa), {"behavior": "deny", "message": "A はやめて"})

    def test_reply_naming_another_request_is_ignored(self):
        """ファイル名は合っていても中身の askId が違う回答・askId の無い回答・旧名 <sid>.reply.json は効かない。"""
        for name_kind in ("wrong_id", "no_id", "legacy"):
            with self.subTest(name_kind):
                p = spawn_hook(BASH_PAYLOAD, self.home, wait=2.5)
                pend = wait_posting(self.home)
                self.assertTrue(pend)
                aid = ask_id_of(pend)
                if name_kind == "wrong_id":
                    write_reply(self.home, "sess-aaaa1111", ask_id=aid, behavior="allow", src="local",
                                askId="0" * 32)
                elif name_kind == "no_id":
                    write_reply(self.home, "sess-aaaa1111", ask_id=aid, behavior="allow", src="local",
                                askId=None)
                else:
                    write_reply(self.home, "sess-aaaa1111", ask_id=aid, behavior="allow", src="local",
                                name="sess-aaaa1111.reply.json")
                out, _ = p.communicate(timeout=25)
                self.assertEqual(out, "", f"{name_kind}: 別の 1 件宛ての回答が効いた")
                for f in approvals(self.home).glob("*reply*"):
                    f.unlink()

    # ── 異常系は全て「無出力 exit 0」＝素通し ─────────────────────────
    def test_stale_reply_is_ignored(self):
        p = spawn_hook(BASH_PAYLOAD, self.home, wait=3)
        self.assertTrue(wait_posting(self.home))
        write_reply(self.home, "sess-aaaa1111", behavior="allow", src="local", ts=time.time() - 3600)
        out, _ = p.communicate(timeout=25)
        self.assertEqual(out, "", "1時間前の回答が効いてしまう＝別の質問に化ける")

    def test_reply_for_another_session_is_ignored(self):
        p = spawn_hook(BASH_PAYLOAD, self.home, wait=3)
        self.assertTrue(wait_posting(self.home))
        write_reply(self.home, "sess-aaaa1111", behavior="allow", src="local", fields={"session": "someone-else"})
        out, _ = p.communicate(timeout=25)
        self.assertEqual(out, "")

    def test_tool_result_means_the_human_answered(self):
        """人間がターミナルで答えると tool_result が書かれる → 黙って降りる（二重回答の防止）。
        自分の tool_use_id が分からないとき（transcript に該当の tool_use が無い）は従来どおり何の結果でも降りる。"""
        tp = Path(self.home) / "t.jsonl"
        tp.write_text('{"type":"assistant"}\n', encoding="utf-8")
        p = spawn_hook(dict(BASH_PAYLOAD, transcript_path=str(tp)), self.home, wait=25)
        pend = wait_posting(self.home)
        self.assertTrue(pend)
        t0 = time.time()
        with tp.open("a") as f:
            f.write('{"type":"user","message":{"content":[{"type":"tool_result"}]}}\n')
        out, _ = p.communicate(timeout=30)
        self.assertEqual(out, "", "答え済みなのに決定を返した（二重回答）")
        self.assertLess(time.time() - t0, 12, "検知が遅すぎる（❗が居座る）")
        self.assertFalse(pend.exists())

    def _own_id_case(self, payload_extra, transcript_head):
        tp = Path(self.home) / "t.jsonl"
        tp.write_text(transcript_head, encoding="utf-8")
        p = spawn_hook(dict(BASH_PAYLOAD, transcript_path=str(tp), **payload_extra), self.home, wait=25)
        pend = wait_posting(self.home)
        self.assertTrue(pend)
        with tp.open("a") as f:           # 並列で先に終わった**別の**呼び出しの結果
            f.write(json.dumps({"type": "user", "message": {"content": [
                {"type": "tool_result", "tool_use_id": "toolu_OTHER", "content": "ok"}]}}) + "\n")
        time.sleep(1.0)
        self.assertTrue(pend.exists(), "他人の tool_result で降りた（まだ聞いている要求の❗が消える）")
        self.assertIsNone(p.poll(), "他人の tool_result で hook が終わった")
        t0 = time.time()
        with tp.open("a") as f:           # 自分の結果（行を 2 回に分けて書く＝行の途中では判定しない）
            line = json.dumps({"type": "user", "message": {"content": [
                {"type": "tool_result", "tool_use_id": "toolu_MINE", "content": "done"}]}}) + "\n"
            f.write(line[:20]); f.flush(); time.sleep(0.3); f.write(line[20:])
        out, _ = p.communicate(timeout=30)
        self.assertEqual(out, "")
        self.assertLess(time.time() - t0, 12)
        self.assertFalse(pend.exists())

    def _subagent_case(self, nested, make_sub=True):
        """実機の形: agent_id あり・tool_use_id なし・transcript_path は**親**。サブエージェントの呼び出しと結果は
        <sid>/subagents/[workflows/wf_x/]agent-<id>.jsonl にだけ書かれる。"""
        ti = BASH_PAYLOAD["tool_input"]
        proj = Path(self.home) / "proj"
        proj.mkdir()
        parent = proj / "sess-aaaa1111.jsonl"
        parent.write_text('{"type":"assistant"}\n', encoding="utf-8")
        sub_dir = proj / "sess-aaaa1111" / "subagents" / (Path("workflows") / "wf_x" if nested else Path("."))
        sub_dir.mkdir(parents=True)
        sub = sub_dir / "agent-a1b2c3.jsonl"
        if make_sub:
            sub.write_text(json.dumps({"type": "assistant", "message": {"content": [
                {"type": "tool_use", "id": "toolu_SUB", "name": "Bash", "input": ti}]}}) + "\n", encoding="utf-8")
        p = spawn_hook(dict(BASH_PAYLOAD, transcript_path=str(parent), agent_id="a1b2c3"), self.home, wait=25)
        pend = wait_posting(self.home)
        self.assertTrue(pend)
        self.assertEqual(json.loads(pend.read_text())["agentId"], "a1b2c3")
        res = lambda tid: json.dumps({"type": "user", "message": {"content": [
            {"type": "tool_result", "tool_use_id": tid, "content": "ok"}]}}) + "\n"
        with parent.open("a") as f:           # 並列の兄弟（別のサブエージェント・親）の結果は親に書かれる
            f.write(res("toolu_SIBLING"))
        if make_sub:
            with sub.open("a") as f:
                f.write(res("toolu_OTHER"))
        time.sleep(1.2)
        self.assertTrue(pend.exists(), "兄弟の tool_result で降りた（まだ聞いている要求の ❗ が消える）")
        self.assertIsNone(p.poll())
        return p, pend, sub, res

    def test_subagent_request_ignores_siblings_and_ends_on_its_own_result(self):
        """★R100-0（レビュー）: サブエージェントの許可は親の transcript の兄弟の結果で降りず、自分の結果で降りる。"""
        for nested in (False, True):
            with self.subTest(nested=nested):
                p, pend, sub, res = self._subagent_case(nested)
                with sub.open("a") as f:
                    f.write(res("toolu_SUB"))
                out, _ = p.communicate(timeout=30)
                self.assertEqual(out, "")
                self.assertFalse(pend.exists())
                import shutil
                shutil.rmtree(Path(self.home) / "proj")

    def test_subagent_without_its_transcript_keeps_waiting_for_an_answer(self):
        """自分の jsonl が見つからないサブエージェントは親の「何か来たら降りる」を使わない＝オフィスの回答が届く。"""
        p, pend, _sub, _res = self._subagent_case(False, make_sub=False)
        write_reply(self.home, "sess-aaaa1111", behavior="deny", src="relay", message="待ってから届いた")
        out, _ = p.communicate(timeout=30)
        self.assertEqual(decision(out), {"behavior": "deny", "message": "待ってから届いた"})

    def test_only_own_tool_result_ends_the_wait_given_tool_use_id(self):
        """入力に tool_use_id があれば、その id の結果だけで降りる（2.1.2xx の PermissionRequest は送らない＝将来の版への備え）。"""
        self._own_id_case({"tool_use_id": "toolu_MINE"}, '{"type":"assistant"}\n')

    def test_only_own_tool_result_ends_the_wait_found_in_transcript(self):
        """★R100-0: tool_use_id が無ければ transcript 末尾から name と input が一致し、まだ結果の無い
        tool_use を 1 件だけ見つける（同じコマンドの過去の実行＝結果が済んでいる物は数えない）。"""
        ti = BASH_PAYLOAD["tool_input"]
        head = "".join(json.dumps(r) + "\n" for r in [
            {"type": "assistant", "message": {"content": [
                {"type": "tool_use", "id": "toolu_OLD", "name": "Bash", "input": ti}]}},
            {"type": "user", "message": {"content": [
                {"type": "tool_result", "tool_use_id": "toolu_OLD", "content": "old"}]}},
            {"type": "assistant", "message": {"content": [
                {"type": "tool_use", "id": "toolu_OTHER", "name": "Bash", "input": {"command": "ls"}},
                {"type": "tool_use", "id": "toolu_MINE", "name": "Bash", "input": ti}]}},
        ])
        self._own_id_case({}, head)

    def test_noise_in_the_transcript_does_not_abandon_the_wait(self):
        """★実機で踏んだ穴: 質問が出ている最中にも attachment 等が普通に追記される。
        「サイズが増えたら降りる」にすると、オフィスからの回答が二度と間に合わない。"""
        tp = Path(self.home) / "t.jsonl"
        tp.write_text('{"type":"assistant"}\n', encoding="utf-8")
        p = spawn_hook(dict(BASH_PAYLOAD, transcript_path=str(tp)), self.home, wait=25)
        self.assertTrue(wait_posting(self.home))
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


class RemoteApprovalTest(unittest.TestCase):
    """R100-1b: src="remote" の allow は、全部の条件が明示的に真のときだけ allow（1 つでも欠けたら deny）。
    条件＝回答の inputSha がこの hook の入力から作り直した値と一致・危険判定（approval_policy.py）が "remote"・
    Mac で ON（office_remote_approve.json の期限内）・回答が 120 秒以内。判定の部品が読めなければ remote は通さない。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = self.tmp.name
        self.cwd = tempfile.mkdtemp(prefix="apv-cwd-")
        self.payload = dict(BASH_PAYLOAD, cwd=self.cwd,
                            tool_input={"command": "npm test", "description": "テストを実行"})

    def tearDown(self):
        self.tmp.cleanup()

    def on(self, until=None):
        p = Path(self.home) / ".claude" / "office_remote_approve.json"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps({"v": 1, "until": int(until if until is not None else time.time() + 3600)}))

    def posted(self, payload=None, hook=None):
        p = spawn_hook(payload or self.payload, self.home) if hook is None else hook
        pend = wait_posting(self.home)
        self.assertTrue(pend)
        return p, pend, json.loads(Path(pend).read_text(encoding="utf-8"))

    def test_posting_carries_the_signed_string_and_the_level(self):
        p, pend, rec = self.posted()
        want = json.dumps({"tool": "Bash", "input": self.payload["tool_input"]}, sort_keys=True,
                          separators=(",", ":"), ensure_ascii=False)
        self.assertEqual(rec["inputCanon"], want)
        self.assertEqual(rec["inputSha"], __import__("hashlib").sha256(want.encode()).hexdigest())
        self.assertEqual((rec["level"], rec["reasons"]), ("remote", []))
        self.assertEqual(rec["inputBytes"], len(want.encode()))
        self.assertEqual(os.stat(pend).st_mode & 0o777, 0o600)
        write_reply(self.home, "sess-aaaa1111", behavior="deny", src="relay", message="x")
        p.communicate(timeout=25)

    def test_remote_allow_when_everything_matches(self):
        self.on()
        p, pend, rec = self.posted()
        write_reply(self.home, "sess-aaaa1111", behavior="allow", src="remote", inputSha=rec["inputSha"],
                    device="d_0123456789ab", reqId="f" * 32)
        out, _ = p.communicate(timeout=25)
        self.assertEqual(decision(out), {"behavior": "allow"})          # message も updatedInput も付かない
        audit = Path(self.home) / ".claude" / "office_approvals_hook.jsonl"
        rows = [json.loads(x) for x in audit.read_text().splitlines()]
        self.assertEqual([r["ev"] for r in rows], ["ask", "decide"])
        self.assertEqual(rows[-1]["src"], "remote")
        self.assertEqual(rows[-1]["decision"], "allow")
        self.assertEqual(rows[0]["askId"], ask_id_of(pend))
        self.assertNotIn("npm test", audit.read_text())                  # 本文は書かない
        self.assertEqual(os.stat(audit).st_mode & 0o777, 0o600)

    def test_review_is_posted_with_tags_and_allowed(self):
        """R102-2（本人裁定「危険なもの以外は全部」）: 拒否リストに当たらないが 1 段の許可リストにも無い形（review）は、
        全文と警告のタグを掲示に置き、条件が揃った remote の allow を通す（2 段の作法はスマホの画面・判定は同じ積）。"""
        self.on()
        payload = dict(self.payload, tool_input={"command": "bash verify.sh"})
        p, pend, rec = self.posted(payload)
        self.assertEqual((rec["level"], rec["reasons"]), ("review", ["script"]))
        self.assertIn("script", rec["tags"])
        self.assertTrue(rec.get("inputCanon"))
        write_reply(self.home, "sess-aaaa1111", behavior="allow", src="remote", inputSha=rec["inputSha"],
                    device="d_0123456789ab", reqId="e" * 32)
        out, _ = p.communicate(timeout=25)
        self.assertEqual(decision(out), {"behavior": "allow"})

    def test_unjudged_syntax_stays_mac_only(self):
        """別モデルレビュー（high）: 判定できない構文（case 文＝unreadable-syntax）は全文を掲示に置かない＝スマホからは許可できない。"""
        self.on()
        payload = dict(self.payload, tool_input={"command": "case x in x) echo ok;; esac"})
        p, pend, rec = self.posted(payload)
        self.assertEqual(rec["level"], "mac")
        self.assertNotIn("inputCanon", rec)
        want = json.dumps({"tool": "Bash", "input": payload["tool_input"]}, sort_keys=True, separators=(",", ":"),
                          ensure_ascii=False)
        write_reply(self.home, "sess-aaaa1111", behavior="allow", src="remote",
                    inputSha=__import__("hashlib").sha256(want.encode()).hexdigest())
        out, _ = p.communicate(timeout=25)
        d = decision(out)
        self.assertEqual(d["behavior"], "deny")
        self.assertIn("mac-only", d["message"])

    def test_mac_form_carries_the_full_text_but_a_wrong_sha_never_allows(self):
        """R103-1: 種類で mac になった形（削除）も全文を掲示に置く（スマホが全文を読んで判断する）。SHA が違えば deny のまま。"""
        self.on()
        payload = dict(self.payload, tool_input={"command": "rm -rf build"})
        p, pend, rec = self.posted(payload)
        self.assertEqual((rec["level"], rec["reasons"]), ("mac", ["delete"]))
        self.assertIn("inputCanon", rec)
        write_reply(self.home, "sess-aaaa1111", behavior="allow", src="remote", inputSha="0" * 64)
        out, _ = p.communicate(timeout=25)
        d = decision(out)
        self.assertEqual(d["behavior"], "deny")
        self.assertIn("input-mismatch", d["message"])

    def refused(self, reason, reply_fields=None, payload=None, hook=None, on=True):
        if on:
            self.on()
        p, pend, rec = self.posted(payload, hook)
        fields = {"behavior": "allow", "src": "remote", "inputSha": rec.get("inputSha") or "0" * 64}
        fields.update(reply_fields or {})
        write_reply(self.home, "sess-aaaa1111", **fields)
        out, _ = p.communicate(timeout=25)
        d = decision(out)
        self.assertEqual(d["behavior"], "deny", reason)
        self.assertIn(reason, d["message"])

    def test_off_is_refused(self):
        self.refused("off", on=False)

    def test_expired_on_is_refused(self):
        self.on(until=time.time() - 1)
        self.refused("off", on=False)

    def test_input_sha_mismatch_is_refused(self):
        self.refused("input-mismatch", {"inputSha": "0" * 64})

    def test_missing_input_sha_is_refused(self):
        self.refused("input-mismatch", {"inputSha": None})

    def test_mac_level_is_allowed_with_the_right_sha(self):
        """R103-1（本人裁定 2026-10-02「Mac だけの操作も全文確認＋Face ID でスマホから」）: 種類で mac になった形（削除）も、
        全文の SHA が一致し ON なら remote の allow が通る（スマホ側は全文を最後まで読む→「内容を確認した」→Face ID の 2 段）。"""
        self.on()
        rm = dict(self.payload, tool_input={"command": "rm -rf build"})
        p, pend, rec = self.posted(rm)
        self.assertEqual((rec["level"], rec["reasons"]), ("mac", ["delete"]))
        write_reply(self.home, "sess-aaaa1111", behavior="allow", src="remote", inputSha=rec["inputSha"])
        out, _ = p.communicate(timeout=25)
        self.assertEqual(decision(out)["behavior"], "allow")

    def test_decision_reclassifies_the_input_at_the_moment(self):
        """掲示の後に、読むはずの cwd 内のファイルが cwd の外へのリンクに差し替えられた（TOCTOU）→ 決定の瞬間の
        危険判定で mac＝正しい署名でも deny。掲示の時点の判定だけに頼らない。"""
        f = Path(self.cwd) / "notes.txt"
        f.write_text("x")
        payload = dict(self.payload, tool_input={"command": "cat notes.txt"})
        self.on()
        p, pend, rec = self.posted(payload)
        self.assertEqual(rec["level"], "remote")
        f.unlink()
        secret = Path(self.home) / ".ssh" / "id_rsa"          # R102-2: cwd の外でも秘密でなければ review＝秘密へ向ける
        secret.parent.mkdir(parents=True, exist_ok=True)
        secret.write_text("k")
        os.symlink(secret, f)
        write_reply(self.home, "sess-aaaa1111", behavior="allow", src="remote", inputSha=rec["inputSha"])
        out, _ = p.communicate(timeout=25)
        d = decision(out)
        self.assertEqual(d["behavior"], "deny")
        self.assertIn("dangerous", d["message"])

    def test_future_dated_reply_is_refused(self):
        self.refused("stale", {"ts": time.time() + 60})

    def test_without_the_policy_file_remote_never_allows(self):
        d = tempfile.mkdtemp(prefix="hook-only-")
        hook = Path(d) / "office-approval-wait.sh"
        hook.write_bytes(HOOK.read_bytes())
        env = dict(os.environ)
        env.update({"OFFICE_HOME": str(self.home), "OFFICE_APPROVAL_WAIT": "6", "OFFICE_APPROVAL_POLL": "0.1"})
        p = subprocess.Popen(["bash", str(hook)], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE, text=True, env=env, start_new_session=True)
        p.stdin.write(json.dumps(self.payload))
        p.stdin.close()
        p.stdin = None
        self.on()
        _p, pend, rec = self.posted(hook=p)
        self.assertEqual(rec["level"], "mac")
        self.assertNotIn("inputCanon", rec)
        want = json.dumps({"tool": "Bash", "input": self.payload["tool_input"]}, sort_keys=True,
                          separators=(",", ":"), ensure_ascii=False)
        write_reply(self.home, "sess-aaaa1111", behavior="allow", src="remote",
                    inputSha=__import__("hashlib").sha256(want.encode()).hexdigest())
        out, _ = p.communicate(timeout=25)
        self.assertEqual(decision(out)["behavior"], "deny")

    def test_without_the_review_file_remote_never_allows(self):
        """R102-2: 写しに approval_policy.py と字句はあるが拒否リスト approval_review.py が無い（配り漏れ）＝ fail closed。
        判定は mac（全文を掲示に置かない）・remote の allow は deny。"""
        d = tempfile.mkdtemp(prefix="hook-noreview-")
        for name, src in (("office-approval-wait.sh", HOOK), ("office_shell_lex.py", HOOK.parent / "office_shell_lex.py"),
                          ("approval_policy.py", HOOK.parent.parent / "server" / "approval_policy.py")):
            (Path(d) / name).write_bytes(Path(src).read_bytes())
        env = dict(os.environ)
        env.update({"OFFICE_HOME": str(self.home), "OFFICE_APPROVAL_WAIT": "6", "OFFICE_APPROVAL_POLL": "0.1"})
        p = subprocess.Popen(["bash", str(Path(d) / "office-approval-wait.sh")], stdin=subprocess.PIPE,
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env, start_new_session=True)
        p.stdin.write(json.dumps(self.payload))
        p.stdin.close()
        p.stdin = None
        self.on()
        _p, pend, rec = self.posted(hook=p)
        self.assertEqual((rec["level"], rec["reasons"]), ("mac", ["unreadable"]))
        self.assertNotIn("inputCanon", rec)
        want = json.dumps({"tool": "Bash", "input": self.payload["tool_input"]}, sort_keys=True,
                          separators=(",", ":"), ensure_ascii=False)
        write_reply(self.home, "sess-aaaa1111", behavior="allow", src="remote",
                    inputSha=__import__("hashlib").sha256(want.encode()).hexdigest())
        out, _ = p.communicate(timeout=25)
        self.assertEqual(decision(out)["behavior"], "deny")

    def decide_rows(self):
        audit = Path(self.home) / ".claude" / "office_approvals_hook.jsonl"
        return [r for r in (json.loads(x) for x in audit.read_text().splitlines()) if r.get("ev") == "decide"]

    def test_tap_mode_allows_remote_and_review(self):
        """R114（本人裁定 2026-10-08「ON の間はタップだけ」）: 回答の mode:"tap"（Face ID 無し）は、この hook の
        決定の瞬間の再計算が remote／review なら今までどおり allow。監査の decide 行に方式が残る。"""
        self.on()
        for inp, lv in ((self.payload["tool_input"], "remote"), ({"command": "bash verify.sh"}, "review")):
            p, pend, rec = self.posted(dict(self.payload, tool_input=inp))
            self.assertEqual(rec["level"], lv)
            write_reply(self.home, "sess-aaaa1111", behavior="allow", src="remote", inputSha=rec["inputSha"],
                        device="d_0123456789ab", reqId="a" * 32, mode="tap")
            out, _ = p.communicate(timeout=25)
            self.assertEqual(decision(out), {"behavior": "allow"}, lv)
        rows = self.decide_rows()
        self.assertEqual([(r["decision"], r["mode"]) for r in rows], [("allow", "tap"), ("allow", "tap")])

    def test_tap_mode_allows_every_level(self):
        """R118（本人裁定 2026-10-13「Claude のスマホアプリと同じく、サクッと許可」）: mac の段（削除）も、daemon がタップで通してきたら
        hook の再計算（全文の SHA-256・段が上がっていない・判定できる形・ON・120 秒）を通る限り allow（R114〜R116 の tap-mac は廃止）。
        方式（tap・sig・未知）は監査に残すだけ。"""
        self.on()
        rm = dict(self.payload, tool_input={"command": "rm -rf build"})
        for mode, want, label in (("tap", "allow", "tap"), ("TAP", "allow", "other"), (None, "allow", "sig"),
                                  ("sig", "allow", "sig")):
            p, pend, rec = self.posted(rm)
            self.assertEqual((rec["level"], rec["reasons"]), ("mac", ["delete"]))
            write_reply(self.home, "sess-aaaa1111", behavior="allow", src="remote", inputSha=rec["inputSha"],
                        device="d_0123456789ab", reqId="b" * 32, **({"mode": mode} if mode else {}))
            out, _ = p.communicate(timeout=25)
            d = decision(out)
            self.assertEqual(d["behavior"], want, mode)
            row = self.decide_rows()[-1]
            self.assertEqual((row["decision"], row["mode"], row["reason"]), (want, label, ""), mode)

    def test_explicit_null_mode_is_an_unknown_mode(self):
        """R114-S2（レビュー medium）: 回答の "mode": null（明示の null）は省略（無印＝R114 より前の daemon＝sig）と同じにしない（監査は other）。
        R118: 段では分けない＝mac の段も remote の段も通る。省略は今までどおり sig。"""
        self.on()
        rm = dict(self.payload, tool_input={"command": "rm -rf build"})
        cases = ((rm, {"mode": None}, "allow", "other", ""),
                 (rm, {}, "allow", "sig", ""),
                 (self.payload, {"mode": None}, "allow", "other", ""))
        for pl, extra, want, label, why in cases:
            p, pend, rec = self.posted(pl)
            write_reply(self.home, "sess-aaaa1111", behavior="allow", src="remote", inputSha=rec["inputSha"],
                        device="d_0123456789ab", reqId="c" * 32, **extra)       # {"mode": None} は JSON の null
            out, _ = p.communicate(timeout=25)
            d = decision(out)
            self.assertEqual(d["behavior"], want, (rec["level"], extra))
            if why:
                self.assertIn(why, d["message"])
            row = self.decide_rows()[-1]
            self.assertEqual((row["decision"], row["mode"], row["reason"]), (want, label, why), (rec["level"], extra))

    def test_tap_mode_with_the_level_lowered_after_posting(self):
        """R114→R118: 掲示の時点で mac（秘密へのリンク）と見せた 1 件が、決定の瞬間に remote へ下がった＝段は上がっていない＝
        tap でも allow（R114〜R116 は tap-mac で拒否していた）。上がった向き（すり替え）の拒否は test_*escalat* が固定。"""
        f = Path(self.cwd) / "notes.txt"
        secret = Path(self.home) / ".ssh" / "id_rsa"
        secret.parent.mkdir(parents=True, exist_ok=True)
        secret.write_text("k")
        os.symlink(secret, f)
        self.on()
        p, pend, rec = self.posted(dict(self.payload, tool_input={"command": "cat notes.txt"}))
        self.assertEqual((rec["level"], rec["reasons"]), ("mac", ["sensitive-path"]))
        f.unlink()
        f.write_text("x")                                     # 決定の瞬間の再計算は remote
        write_reply(self.home, "sess-aaaa1111", behavior="allow", src="remote", inputSha=rec["inputSha"], mode="tap")
        out, _ = p.communicate(timeout=25)
        self.assertEqual(decision(out), {"behavior": "allow"})

    def test_local_allow_is_unchanged_by_remote(self):
        p, pend, rec = self.posted()
        write_reply(self.home, "sess-aaaa1111", behavior="allow", src="local")
        out, _ = p.communicate(timeout=25)
        self.assertEqual(decision(out), {"behavior": "allow"})

    def test_question_never_carries_remote(self):
        q = dict(self.payload, tool_name="AskUserQuestion",
                 tool_input={"questions": [{"question": "どれ?", "options": [{"label": "A"}]}]})
        p, pend, rec = self.posted(q)
        self.assertEqual(rec["level"], "mac")
        self.assertNotIn("inputCanon", rec)
        write_reply(self.home, "sess-aaaa1111", behavior="deny", src="relay", message="A")
        p.communicate(timeout=25)

class HeartbeatTest(unittest.TestCase):
    """R86-H2: 人間が TUI で答えると Claude Code は hook をその場で kill する（finally は走らない）。
    掲示の生死は**心拍（mtime）**で判定する＝hook は毎周 utime だけ打ち、中身は書き換えない。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = self.tmp.name

    def tearDown(self):
        self.tmp.cleanup()

    def test_heartbeat_touches_posting_without_rewriting(self):
        p = spawn_hook(BASH_PAYLOAD, self.home)
        pend = wait_posting(self.home)
        self.assertTrue(pend, "掲示が出ない")
        try:
            body0 = pend.read_bytes(); st0 = pend.stat()
            time.sleep(0.6)
            st1 = pend.stat()
            self.assertGreater(st1.st_mtime, st0.st_mtime, "心拍（mtime）が進んでいない")
            self.assertEqual(pend.read_bytes(), body0, "心拍で中身を書き換えている（pid/ts は不変のはず）")
            self.assertEqual(st1.st_ino, st0.st_ino, "心拍でファイルを作り直している")
        finally:
            kill_group(p, signal.SIGKILL); p.wait(timeout=5)

    def test_sigterm_cleans_up_the_posting_immediately(self):
        p = spawn_hook(BASH_PAYLOAD, self.home)
        pend = wait_posting(self.home)
        self.assertTrue(pend)
        kill_group(p, signal.SIGTERM)
        end = time.time() + 2.0
        while time.time() < end and pend.exists():
            time.sleep(0.05)
        self.assertFalse(pend.exists(), "SIGTERM で掲示が消えない")
        out, _ = p.communicate(timeout=5)
        self.assertEqual(out, "", "kill 時に出力してはいけない（ターミナルを壊す）")

    def test_sigkill_freezes_the_heartbeat_but_keeps_the_posting(self):
        """SIGKILL は捕まえられない＝掲示は残り mtime が止まる。この失敗様式を daemon 側（ASK_STALE）が拾う。"""
        p = spawn_hook(BASH_PAYLOAD, self.home)
        pend = wait_posting(self.home)
        self.assertTrue(pend)
        kill_group(p, signal.SIGKILL); p.wait(timeout=5)
        m0 = pend.stat().st_mtime
        time.sleep(0.5)
        self.assertTrue(pend.exists(), "SIGKILL では掲示が残るはず（これが幽霊の正体）")
        self.assertEqual(pend.stat().st_mtime, m0, "死んだ hook の mtime が動いている")

    def test_republishes_when_the_posting_was_swept(self):
        p = spawn_hook(BASH_PAYLOAD, self.home)
        pend = wait_posting(self.home)
        self.assertTrue(pend)
        try:
            pid0 = json.loads(pend.read_text())["pid"]
            pend.unlink()
            self.assertTrue(wait_for(pend, 3.0), "掃除された掲示を再掲示しない（スリープ復帰で❗が消えたまま）")
            self.assertEqual(json.loads(pend.read_text())["pid"], pid0)
        finally:
            kill_group(p, signal.SIGKILL); p.wait(timeout=5)


if __name__ == "__main__":
    unittest.main()
