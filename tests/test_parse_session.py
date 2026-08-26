# -*- coding: utf-8 -*-
"""状態推定のゴールデンテスト（合成jsonlフィクスチャ・mtime注入）"""
import importlib.util
import json
import os
import re
import shutil
import tempfile
import unittest
from pathlib import Path

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

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)
        office._SKILL_MEMORY.clear()
        office._TITLE_MEMORY.clear()

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

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)
        office._SKILL_MEMORY.clear()
        office._TITLE_MEMORY.clear()

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


if __name__ == "__main__":
    unittest.main()
