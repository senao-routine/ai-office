# -*- coding: utf-8 -*-
"""状態推定のゴールデンテスト（合成jsonlフィクスチャ・mtime注入）"""
import importlib.util
import json
import os
import time
from datetime import datetime
import re
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

TESTS = Path(__file__).resolve().parent
ROOT = TESTS.parent
FX = TESTS / "fixtures"

os.environ.setdefault("OFFICE_HOME", tempfile.mkdtemp(prefix="office_home_"))
spec = importlib.util.spec_from_file_location(
    "office_server", ROOT / "server" / "office_server.py")
office = importlib.util.module_from_spec(spec)
spec.loader.exec_module(office)


class ParseSessionTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="office_fx_"))
        office._SKILL_MEMORY.clear()
        office._TITLE_MEMORY.clear()
        office._AI_TITLE_MEMORY.clear()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)
        office._SKILL_MEMORY.clear()
        office._TITLE_MEMORY.clear()
        office._AI_TITLE_MEMORY.clear()

    def load(self, fixture, age, name="sess-aaaa1111.jsonl"):
        """フィクスチャをコピーし、mtime=now-age で parse_session を呼ぶ"""
        p = self.tmp / name
        shutil.copy(FX / fixture, p)
        mtime = 1_800_000_000.0
        os.utime(p, (mtime, mtime))
        return office.parse_session(p, now=mtime + age)

    def test_working_tool(self):
        e = self.load("working_tool.jsonl", age=10)
        self.assertEqual(e["state"], "working")
        self.assertEqual(e["verb"], "実行中")
        self.assertEqual(e["tool"], "Bash")            # R110: 工具名そのもの（部屋の用事の根拠・言語に依らない）
        self.assertIn("push", e["target"])
        self.assertEqual(e["approvalMin"], 0)
        self.assertEqual(e["branch"], "main")
        self.assertEqual(e["cwd"], "/Users/test/demo-project")

    def test_approval_stuck(self):
        """最後のイベントがtool_useのまま75秒超 → 承認待ち推定"""
        e = self.load("working_tool.jsonl", age=200)
        self.assertEqual(e["state"], "working")
        self.assertEqual(e["approvalMin"], 3)
        self.assertIn("実行中", e["stuckTool"])

    def test_ask_question(self):
        e = self.load("ask_question.jsonl", age=100)
        self.assertIn("本番に公開しますか", e["question"])
        self.assertIn("公開する", e["question"])   # 選択肢ラベルの連結
        self.assertEqual(e["approvalMin"], 0)      # 質問はapprovalではなくquestion側

    def test_stale_tool_session_stops_approval_alert(self):
        """R23.5: tool止まりでもresting帯(1800s+)はクラッシュ/放置残骸＝❗承認待ちを出さない"""
        e = self.load("working_tool.jsonl", age=2000)
        self.assertEqual(e["state"], "resting")
        self.assertEqual(e["approvalMin"], 0)

    def test_stale_ask_question_keeps_question(self):
        """R23.5: 未回答のAskUserQuestionはresting帯でも質問として残す（ユーザーの回答待ち）"""
        e = self.load("ask_question.jsonl", age=2000)
        self.assertIn("本番に公開しますか", e["question"])
        self.assertEqual(e["approvalMin"], 0)

    def test_pending_ask_question_exposes_capped_options(self):
        e = self.load("ask_question_pending_options.jsonl", age=100)
        self.assertIn("どの構成で進めますか", e["question"])
        self.assertEqual(e["questionOptions"], [
            {"label": "プロジェクト部屋型 (Recommended)", "desc": "部署ごとに部屋を分けて表示します"},
            {"label": "一覧型", "desc": "社員を一覧で見やすく表示します"},
            {"label": "混在型", "desc": "重要な社員だけ部屋に配置します"},
        ])
        self.assertEqual(len(e["questionOptions"]), 3)
        self.assertLessEqual(max(map(lambda option: len(option["label"]), e["questionOptions"])), 60)
        self.assertLessEqual(max(map(lambda option: len(option["desc"]), e["questionOptions"])), 120)

    def test_answered_ask_question_has_no_question_options(self):
        e = self.load("ask_question_answered_options.jsonl", age=100)
        self.assertEqual(e["question"], "")
        self.assertNotIn("questionOptions", e)

    def test_waiting_said(self):
        e = self.load("waiting_said.jsonl", age=600)
        self.assertEqual(e["state"], "waiting")
        self.assertEqual(e["verb"], "指示待ち")
        self.assertIn("完了しました", e["lastSaid"])
        self.assertIn("まとめて", e["lastOrder"])

    def test_api_error_line_says_why_it_stopped(self):
        """R102-4: 合成のエラー行（isApiErrorMessage）で止まったセッションは「指示待ち／報告中」でなく、止まった理由の種類を持つ。
        本文（API の応答）は err に入れない。直後（age 5 秒）でも作業中と言わない。"""
        for fx, kind, verb in (("api_error_limit.jsonl", "limit", "利用上限で停止"),
                               ("api_error_overloaded.jsonl", "api", "API エラーで停止"),
                               ("api_error_context.jsonl", "ctx", "会話の長さの上限で停止")):
            with self.subTest(fx=fx):
                e = self.load(fx, age=5)
                self.assertEqual(e["err"], {"k": kind, "ago": 5})
                self.assertEqual((e["state"], e["verb"], e["target"]), ("waiting", verb, ""))
                self.assertEqual(e["approvalMin"], 0)
        e = self.load("api_error_limit.jsonl", age=3600)
        self.assertEqual((e["state"], e["err"]["k"]), ("resting", "limit"))
        self.assertEqual(e["permMode"], "plan")

    def test_interrupt_closes_the_turn(self):
        """R102-4: Esc の中断（Stop フックは走らない）は interruptedAt（ローカル専用）で「閉じたターン」を知らせる。
        中断した道具を ❗（止まっている?）にしない。"""
        e = self.load("interrupted_tool.jsonl", age=200)
        self.assertEqual((e["state"], e["verb"], e["approvalMin"]), ("waiting", "中断しました", 0))
        self.assertGreater(e["interruptedAt"], 0)
        self.assertNotIn("err", e)
        self.assertEqual(e["permMode"], "bypassPermissions")
        e = self.load("waiting_said.jsonl", age=600, name="sess-bbbb2222.jsonl")   # 権限モードは同じパスで覚える＝別の名前で
        self.assertNotIn("interruptedAt", e)
        self.assertNotIn("err", e)
        self.assertNotIn("permMode", e)

    def test_resting(self):
        e = self.load("waiting_said.jsonl", age=3600)
        self.assertEqual(e["state"], "resting")
        self.assertEqual(e["verb"], "休憩中")

    def test_broken_lines_are_skipped(self):
        e = self.load("broken_lines.jsonl", age=10)
        self.assertEqual(e["state"], "working")
        self.assertEqual(e["verb"], "編集中")
        self.assertEqual(e["target"], "app.py")

    def test_tail_truncation(self):
        """TAIL_BYTES 超のファイルは先頭の欠け行を捨てて末尾だけ読む"""
        src = (FX / "working_tool.jsonl").read_text()
        big = self.tmp / "big.jsonl"
        big.write_text("x" * (office.TAIL_BYTES + 5000) + "\n" + src)
        mtime = 1_800_000_000.0
        os.utime(big, (mtime, mtime))
        e = office.parse_session(big, now=mtime + 10)
        self.assertIsNotNone(e)
        self.assertEqual(e["verb"], "実行中")

    def test_minions_counted(self):
        p = self.tmp / "sess-bbbb2222.jsonl"
        shutil.copy(FX / "working_tool.jsonl", p)
        sub = self.tmp / "sess-bbbb2222" / "subagents" / "wf_x"
        sub.mkdir(parents=True)
        agent = sub / "agent-1.jsonl"
        agent.write_text("{}")
        mtime = 1_800_000_000.0
        for f in (p, agent):
            os.utime(f, (mtime, mtime))
        e = office.parse_session(p, now=mtime + 10)
        self.assertEqual(e["minions"], 1)

    def test_minions_live_counts_only_children_written_in_the_last_minute(self):
        """minions は終わった子も 15 分数える（スマホが指示待ちのセッションを 15 分「作業中」と出していた）。
        minionsLive は直近 60 秒に書いた子だけ＝作業中の根拠はこちら。"""
        p = self.tmp / "sess-bbbb3333.jsonl"
        shutil.copy(FX / "waiting_said.jsonl", p)
        sub = self.tmp / "sess-bbbb3333" / "subagents"
        sub.mkdir(parents=True)
        now = 1_800_000_000.0
        for name, ago in (("agent-live.jsonl", 10), ("agent-done.jsonl", 300), ("agent-old.jsonl", 1000)):
            (sub / name).write_text("{}")
            os.utime(sub / name, (now - ago, now - ago))
        os.utime(p, (now - 30, now - 30))
        e = office.parse_session(p, now=now)
        self.assertEqual((e["minions"], e["minionsLive"]), (2, 1))
        os.utime(sub / "agent-live.jsonl", (now - 61, now - 61))
        self.assertEqual(office.parse_session(p, now=now)["minionsLive"], 0)

    def _ask(self, now, **kw):
        return dict({"tool": "AskUserQuestion", "kind": "question", "title": "どの順で直しますか？",
                     "options": ["server から", "PWA から"], "ts": now - 130, "askId": "a" * 32,
                     "remote": 0, "askCount": 1}, **kw)

    def test_question_ask_carries_minutes_count_and_multi_select(self):
        """質問の掲示は approvalMin が立たない＝スマホが 40 分前の質問を「いま」と出していた → ask.min。
        2 問以上・複数選択の質問を 1 問目の 1 ラベルで答えさせない → ask.qn・ask.multi（整数だけ）。"""
        p = self.tmp / "sess-qqqq1111.jsonl"
        shutil.copy(FX / "ask_question_multi.jsonl", p)
        now = 1_800_000_000.0
        os.utime(p, (now - 130, now - 130))
        with mock.patch.object(office, "pending_approval", return_value=self._ask(now)):
            e = office.parse_session(p, now=now)
        self.assertEqual(e["approvalMin"], 0)
        self.assertEqual({k: e["ask"][k] for k in ("min", "qn", "multi")}, {"min": 2, "qn": 2, "multi": 1})
        # 掲示の 1 問目と照合できない（サブエージェントの質問など）＝形は言わない（スマホは従来どおり）
        with mock.patch.object(office, "pending_approval", return_value=self._ask(now, title="別の質問")):
            e = office.parse_session(p, now=now)
        self.assertEqual(e["ask"]["min"], 2)
        self.assertNotIn("qn", e["ask"])
        self.assertNotIn("multi", e["ask"])
        # 1 問・単一選択
        q1 = self.tmp / "sess-qqqq2222.jsonl"
        shutil.copy(FX / "ask_question_pending_options.jsonl", q1)
        os.utime(q1, (now - 20, now - 20))
        with mock.patch.object(office, "pending_approval",
                               return_value=self._ask(now, title="どの構成で進めますか？", ts=now - 20)):
            e = office.parse_session(q1, now=now)
        self.assertEqual({k: e["ask"][k] for k in ("min", "qn", "multi")}, {"min": 0, "qn": 1, "multi": 0})

    def test_permission_and_plan_asks_carry_minutes_only(self):
        p = self.tmp / "sess-pppp1111.jsonl"
        shutil.copy(FX / "working_tool.jsonl", p)
        now = 1_800_000_000.0
        os.utime(p, (now - 30, now - 30))
        for ask in (self._ask(now, tool="Bash", kind="permission", title="git push", ts=now - 190),
                    self._ask(now, tool="ExitPlanMode", kind="question", title="プランの承認", ts=now - 190)):
            with mock.patch.object(office, "pending_approval", return_value=ask):
                e = office.parse_session(p, now=now)
            self.assertEqual(e["ask"]["min"], 3, ask["tool"])
            self.assertNotIn("qn", e["ask"])

    def test_skills_extract_tool_and_command_with_window_order_limit_and_safety(self):
        e = self.load("skills_transcript.jsonl", age=10)
        self.assertEqual(e["skills"], [
            "x-post", "blogwrite", "ABC", "video-edit", "A" * 64,
        ])
        self.assertTrue(all(re.fullmatch(r"[A-Za-z0-9_:-]{1,64}", name)
                            for name in e["skills"]))
        self.assertNotIn("too-old", e["skills"])
        self.assertNotIn("researchdeep", e["skills"])
        self.assertNotIn("memo", e["skills"])

    def test_skills_outside_window_and_absent_are_empty(self):
        self.assertEqual(self.load("skills_transcript.jsonl", age=2000)["skills"], [])
        self.assertEqual(self.load("waiting_said.jsonl", age=10)["skills"], [])

    def test_skills_survive_tail_scroll_via_memory_until_window_expires(self):
        # tail窓(80KB)からスキル行が流れても、同一セッションなら30分はメモリで保持する
        p = self.tmp / "sess-aaaa1111.jsonl"
        shutil.copy(FX / "skills_transcript.jsonl", p)
        mtime = 1_800_000_000.0
        os.utime(p, (mtime, mtime))
        first = office.parse_session(p, now=mtime + 10)
        self.assertIn("x-post", first["skills"])
        # スキル行が窓から消えた状態（スキル無しfixtureへ差し替え）でも保持される
        shutil.copy(FX / "waiting_said.jsonl", p)
        os.utime(p, (mtime + 60, mtime + 60))
        second = office.parse_session(p, now=mtime + 70)
        self.assertIn("x-post", second["skills"])
        # 30分経過で失効する
        third = office.parse_session(p, now=mtime + 31 * 60)
        self.assertEqual(third["skills"], [])


class HelpersTest(unittest.TestCase):
    def test_describe_tool_mcp(self):
        verb, target = office.describe_tool("mcp__brightData__search_engine", {})
        self.assertEqual(verb, "外部ツール操作中")
        self.assertEqual(target, "search_engine")

    def test_project_label_nfc_and_first_match(self):
        cfg = {"projects": {"demo-project": {"name": "デモ部", "role": "検証"}}}
        name, role = office.project_label("/Users/test/demo-project", "-x", cfg)
        self.assertEqual((name, role), ("デモ部", "検証"))
        name, _ = office.project_label("/Users/test/unknown", "-x", {"projects": {}})
        self.assertEqual(name, "unknown")


class CustomTitleTest(unittest.TestCase):
    """R85-1: /rename のセッション名（custom-title 行 → employee['title']）。"""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="office_title_"))
        office._SKILL_MEMORY.clear()
        office._TITLE_MEMORY.clear()
        office._AI_TITLE_MEMORY.clear()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)
        office._SKILL_MEMORY.clear()
        office._TITLE_MEMORY.clear()
        office._AI_TITLE_MEMORY.clear()

    def load(self, fixture, age, name="sess-aaaa1111.jsonl"):
        p = self.tmp / name
        shutil.copy(FX / fixture, p)
        mtime = 1_800_000_000.0
        os.utime(p, (mtime, mtime))
        return office.parse_session(p, now=mtime + age)

    def test_last_custom_title_wins(self):
        """複数の custom-title 行は最後の1件が現在値。ai-title/agent-name は採用しない。"""
        e = self.load("custom_title.jsonl", age=10)
        self.assertEqual(e["title"], "決済チーム")

    def test_cleared_title_is_empty(self):
        """customTitle が空文字＝リネーム解除。"""
        e = self.load("custom_title_cleared.jsonl", age=10)
        self.assertEqual(e["title"], "")

    def test_no_custom_title_is_empty(self):
        e = self.load("working_tool.jsonl", age=10)
        self.assertEqual(e["title"], "")

    def test_clamp_and_nonstring_ignored(self):
        """30字クランプ（short と同じ …省略）・非文字列は無視して直前の値を保つ。"""
        src = (FX / "working_tool.jsonl").read_text()
        p = self.tmp / "sess-cccc3333.jsonl"
        p.write_text(
            json.dumps({"type": "custom-title", "customTitle": "あ" * 40},
                       ensure_ascii=False) + "\n" +
            json.dumps({"type": "custom-title", "customTitle": 123}) + "\n" + src)
        mtime = 1_800_000_000.0
        os.utime(p, (mtime, mtime))
        e = office.parse_session(p, now=mtime + 10)
        self.assertEqual(len(e["title"]), 30)
        self.assertTrue(e["title"].endswith("…"))

    def test_title_sticky_when_scrolled_out_of_tail(self):
        """custom-title 行が tail 窓から流れても記憶で表示を保つ（名前チラつき根絶）。
        明示の解除（空文字）は記憶ごと消す。"""
        p = self.tmp / "sess-dddd4444.jsonl"
        mtime = 1_800_000_000.0
        shutil.copy(FX / "custom_title.jsonl", p)
        os.utime(p, (mtime, mtime))
        e1 = office.parse_session(p, now=mtime + 10)
        self.assertEqual(e1["title"], "決済チーム")
        # 窓から流れた状態を再現（custom-title 行の無い内容へ差し替え）
        shutil.copy(FX / "working_tool.jsonl", p)
        os.utime(p, (mtime, mtime))
        e2 = office.parse_session(p, now=mtime + 20)
        self.assertEqual(e2["title"], "決済チーム", "記憶が効いていない（チラつき再発）")
        # 明示の解除
        shutil.copy(FX / "custom_title_cleared.jsonl", p)
        os.utime(p, (mtime, mtime))
        e3 = office.parse_session(p, now=mtime + 30)
        self.assertEqual(e3["title"], "")


class AiTitleTest(unittest.TestCase):
    """R123-A: Claude Code の自動タイトル（ai-title 行 → employee['aiTitle']）。title（/rename）には混ぜない。"""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="office_aititle_"))
        office._SKILL_MEMORY.clear()
        office._TITLE_MEMORY.clear()
        office._AI_TITLE_MEMORY.clear()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)
        office._SKILL_MEMORY.clear()
        office._TITLE_MEMORY.clear()
        office._AI_TITLE_MEMORY.clear()

    def parse(self, head_lines, name="sess-ai000001.jsonl", age=10, fixture="working_tool.jsonl"):
        p = self.tmp / name
        src = (FX / fixture).read_text()
        p.write_text("".join(json.dumps(x, ensure_ascii=False) + "\n" for x in head_lines) + src)
        mtime = 1_800_000_000.0
        os.utime(p, (mtime, mtime))
        return office.parse_session(p, now=mtime + age)

    def test_custom_title_fixture_keeps_both_apart(self):
        """custom-title と ai-title が両方あるとき、title は /rename・aiTitle は自動タイトル（別の欄）。"""
        p = self.tmp / "sess-aaaa1111.jsonl"
        shutil.copy(FX / "custom_title.jsonl", p)
        mtime = 1_800_000_000.0
        os.utime(p, (mtime, mtime))
        e = office.parse_session(p, now=mtime + 10)
        self.assertEqual(e["title"], "決済チーム")
        self.assertEqual(e["aiTitle"], "自動生成の別タイトル（採用しない）")

    def test_last_ai_title_wins_and_whitespace_folds(self):
        e = self.parse([{"type": "ai-title", "aiTitle": "最初の題"},
                        {"type": "ai-title", "aiTitle": "  前回作業の\n続き  "},
                        {"type": "ai-title", "aiTitle": 42}])
        self.assertEqual(e["aiTitle"], "前回作業の 続き")
        self.assertEqual(e["title"], "")

    def test_no_ai_title_is_empty_and_clamped(self):
        self.assertEqual(self.parse([])["aiTitle"], "")
        e = self.parse([{"type": "ai-title", "aiTitle": "あ" * 60}], name="sess-ai000002.jsonl")
        self.assertEqual(len(e["aiTitle"]), 40)
        self.assertTrue(e["aiTitle"].endswith("…"))

    def test_ai_title_sticky_when_scrolled_out_of_tail(self):
        """ai-title 行が tail 窓から流れても記憶で表示を保つ（/rename の名前と同じ流儀）。"""
        p = self.tmp / "sess-ai000003.jsonl"
        mtime = 1_800_000_000.0
        p.write_text(json.dumps({"type": "ai-title", "aiTitle": "検証フォルダの整理"}, ensure_ascii=False) + "\n"
                     + (FX / "working_tool.jsonl").read_text())
        os.utime(p, (mtime, mtime))
        self.assertEqual(office.parse_session(p, now=mtime + 10)["aiTitle"], "検証フォルダの整理")
        shutil.copy(FX / "working_tool.jsonl", p)
        os.utime(p, (mtime, mtime))
        self.assertEqual(office.parse_session(p, now=mtime + 20)["aiTitle"], "検証フォルダの整理")


class ListeningTest(unittest.TestCase):
    """R86-D: 受信待機（Stop hookの心拍）の判定。届かない相手に「届く」と嘘をつかない。"""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="office_listen_"))
        self.orig = office.INBOX
        office.INBOX = self.tmp

    def tearDown(self):
        office.INBOX = self.orig
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _pid(self, session, age):
        p = self.tmp / f".{session}.pid"
        p.write_text("12345\n", encoding="utf-8")
        t = time.time() - age
        os.utime(p, (t, t))
        return p

    def test_fresh_heartbeat_is_listening(self):
        self._pid("s-live", age=3)
        self.assertTrue(office.session_listening("s-live"))

    def test_stale_heartbeat_is_not_listening(self):
        """心拍が止まった＝待機プロセスが死んでいる。PIDが生きていても関係ない
        （実測でPID再利用により7件中4件がGoogle Drive等を「待機中」と誤報していた）。"""
        self._pid("s-dead", age=600)
        self.assertFalse(office.session_listening("s-dead"))

    def test_missing_pidfile_is_not_listening(self):
        self.assertFalse(office.session_listening("s-none"))

    def test_pid_content_is_irrelevant(self):
        """判定は mtime の鮮度のみ＝中身が壊れていても心拍が新しければ待機中。"""
        p = self._pid("s-odd", age=1)
        p.write_text("not-a-pid", encoding="utf-8")
        t = time.time() - 1
        os.utime(p, (t, t))
        self.assertTrue(office.session_listening("s-odd"))


class GhostSessionTest(unittest.TestCase):
    """R86-D: Claude Code はアイドルな transcript も1時間ごとに touch する。
    mtime を鮮度にすると数十時間前に終わったセッションが「出勤中」に居座る（実測53%）。"""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="office_ghost_"))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_age_uses_last_event_timestamp_not_mtime(self):
        p = self.tmp / "sess-ghost001.jsonl"
        old = "2026-08-20T10:00:00.000Z"
        p.write_text("\n".join([
            json.dumps({"type": "user", "message": {"role": "user", "content": "やって"},
                        "cwd": "/w/x", "timestamp": old}),
            json.dumps({"type": "assistant", "timestamp": old,
                        "message": {"role": "assistant",
                                    "content": [{"type": "text", "text": "できました"}]}}),
        ]), encoding="utf-8")
        now = datetime.fromisoformat("2026-08-27T10:00:00+00:00").timestamp()
        os.utime(p, (now - 60, now - 60))          # mtime は1分前（＝touchされた幽霊）
        e = office.parse_session(p, now=now)
        self.assertGreater(e["age"], 6 * 24 * 3600, "mtimeを信じて幽霊を出勤させている")

    def test_age_falls_back_to_mtime_without_timestamps(self):
        """timestamp を持たない合成フィクスチャは従来どおり mtime 基準（既存テスト互換）。"""
        p = self.tmp / "sess-nots0001.jsonl"
        shutil.copy(FX / "working_tool.jsonl", p)
        mtime = 1_800_000_000.0
        os.utime(p, (mtime, mtime))
        e = office.parse_session(p, now=mtime + 42)
        self.assertEqual(e["age"], 42)


class CanPromptTest(unittest.TestCase):
    """R86-F: ❗は「承認まち」であって「ツールが走っている」ではない。
    実測で直近7日の❗159回中95回(60%)が誤報だった＝Push/macOS通知/トリアージ/日報の共通トリガ
    なので実害が大きい。can_prompt は「そのツールが人間に聞き得るか」の純関数。"""

    def setUp(self):
        office._ASK_CACHE.update(mtime=-1.0, rules=())
        self._orig = office._ask_rules
        office._ask_rules = lambda: ("git push", "firebase deploy")

    def tearDown(self):
        office._ask_rules = self._orig
        office._ASK_CACHE.update(mtime=-1.0, rules=())

    def test_truth_table(self):
        B = "bypassPermissions"
        cases = [
            (B, "Bash", {"command": "python3 run_check.py"}, False),   # 走っているだけ
            (B, "Bash", {"command": "git push origin main"}, True),    # askルール＝本物
            (B, "Bash", {"command": "cd /x && git push"}, True),       # 複合コマンド内
            (B, "Bash", {"command": "firebase deploy --only hosting"}, True),
            (B, "Bash", {"command": "echo git push"}, False),          # 引数に出るだけは対象外
            (B, "TaskOutput", {}, False),                              # bypass下でBash以外は聞かれない
            (B, "Read", {"file_path": "/x"}, False),
            (B, "ExitPlanMode", {}, True),                             # プラン承認は全モード
            ("plan", "Bash", {"command": "ls"}, True),                 # 従来どおり
            ("dontAsk", "Bash", {"command": "git push origin main"}, False),   # 聞かないモード＝走っているだけ
            ("dontAsk", "Read", {"file_path": "/x"}, False),
            ("dontAsk", "ExitPlanMode", {}, True),
            ("default", "Bash", {"command": "ls"}, True),
            ("", "Bash", {"command": "ls"}, True),                     # 不明＝安全側
            (None, "Bash", {"command": "ls"}, True),
        ]
        for mode, tool, inp, want in cases:
            self.assertEqual(office.can_prompt(mode, tool, inp), want,
                             f"{mode!r}/{tool}/{inp}")

    def test_no_ask_rules_means_bypass_never_prompts(self):
        office._ask_rules = lambda: ()
        self.assertFalse(office.can_prompt("bypassPermissions", "Bash",
                                           {"command": "git push origin main"}))
        self.assertTrue(office.can_prompt("bypassPermissions", "ExitPlanMode", {}))


class ApprovalGateTest(unittest.TestCase):
    """R86-F: parse_session の❗が permission-mode で正しく門を絞ること（実フィクスチャ）。"""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="office_perm_"))
        office._SKILL_MEMORY.clear()
        office._TITLE_MEMORY.clear()
        office._AI_TITLE_MEMORY.clear()
        office._PERM_MEMORY.clear()
        self._orig = office._ask_rules
        office._ask_rules = lambda: ("git push", "firebase deploy")

    def tearDown(self):
        office._ask_rules = self._orig
        office._PERM_MEMORY.clear()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def load(self, fixture, age, name="sess-perm0001.jsonl"):
        p = self.tmp / name
        shutil.copy(FX / fixture, p)
        mtime = 1_800_000_000.0
        os.utime(p, (mtime, mtime))
        return office.parse_session(p, now=mtime + age)

    def test_bypass_long_bash_is_not_approval(self):
        """走っているだけのBashを「承認まち」と言わない（今回の誤検知の本体）。"""
        e = self.load("bypass_bash_stall.jsonl", age=400)
        self.assertEqual(e["approvalMin"], 0)
        self.assertEqual(e["stuckTool"], "")

    def test_bypass_git_push_is_real_approval(self):
        """askルール該当は bypass でも聞かれる＝本物の❗を消さない。"""
        e = self.load("bypass_git_push.jsonl", age=400)
        self.assertEqual(e["approvalMin"], 6)
        self.assertIn("実行中", e["stuckTool"])

    def test_dont_ask_long_bash_is_not_approval(self):
        """dontAsk は聞かずに拒否するモード＝75 秒を越える Bash は走っているだけ（❗と通知の誤報だった）。"""
        e = self.load("dontask_bash_stall.jsonl", age=400)
        self.assertEqual((e["approvalMin"], e["stuckTool"]), (0, ""))

    def test_exit_plan_mode_is_real_approval(self):
        e = self.load("plan_exit_plan.jsonl", age=400)
        self.assertGreater(e["approvalMin"], 0)

    def test_unknown_mode_keeps_legacy_behavior(self):
        """permission-mode 行を持たない旧形式は従来どおり❗を出す（安全側）。
        working_tool.jsonl は `git push origin main` なので本物でもある。"""
        e = self.load("working_tool.jsonl", age=200, name="sess-legacy01.jsonl")
        self.assertEqual(e["approvalMin"], 3)

    def test_perm_mode_survives_tail_scroll(self):
        """permission-mode 行が窓から流れても記憶で保つ（落ちると誤検知が復活する）。"""
        p = self.tmp / "sess-perm0002.jsonl"
        mtime = 1_800_000_000.0
        shutil.copy(FX / "bypass_bash_stall.jsonl", p)
        os.utime(p, (mtime, mtime))
        self.assertEqual(office.parse_session(p, now=mtime + 400)["approvalMin"], 0)
        # permission-mode 行の無い内容へ差し替え（＝窓から流れた状態）
        body = (FX / "bypass_bash_stall.jsonl").read_text(encoding="utf-8").splitlines()
        p.write_text("\n".join(body[1:]), encoding="utf-8")
        os.utime(p, (mtime, mtime))
        self.assertEqual(office.parse_session(p, now=mtime + 400)["approvalMin"], 0,
                         "窓落ちで誤検知が復活した")


if __name__ == "__main__":
    unittest.main()
