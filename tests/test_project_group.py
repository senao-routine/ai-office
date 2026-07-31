# -*- coding: utf-8 -*-
"""R50-P1: プロジェクト集約（1アバター＝1プロジェクト）のテスト。

掟（CLAUDE.md フィクスチャ第一則）に従い、実装より先に書いた。

設計の要点:
  - employees[] は**一切変えない**（旧UIの挙動と既存テストを守る＝併走の条件）
  - 集約結果は新しい roster[] として並置する。新UIはこちらだけを読む
  - session は代表セッションID（❗持ち優先→最新）＝投函/hook/MCP/署名は無改造で動く
  - external(OpenClaw) は集約しない（別Macの稼働体・専用区画）
"""
import importlib.util
import os
import shutil
import tempfile
import time
import unittest
from pathlib import Path

TESTS = Path(__file__).resolve().parent
ROOT = TESTS.parent
FX = TESTS / "fixtures"

_home = Path(tempfile.mkdtemp(prefix="office_group_home_"))
os.environ["OFFICE_HOME"] = str(_home)
spec = importlib.util.spec_from_file_location(
    "office_server_group", ROOT / "server" / "office_server.py")
office = importlib.util.module_from_spec(spec)
spec.loader.exec_module(office)


def put(proj, name, fixture, age):
    d = _home / ".claude" / "projects" / proj
    d.mkdir(parents=True, exist_ok=True)
    p = d / name
    shutil.copy(FX / fixture, p)
    t = time.time() - age
    os.utime(p, (t, t))
    return p


def projects_of(data):
    return {p["name"]: p for p in data["roster"]}


class ProjectGroupTest(unittest.TestCase):
    def setUp(self):
        proj_root = _home / ".claude" / "projects"
        if proj_root.exists():
            shutil.rmtree(proj_root)
        office._cache["t"] = 0.0

    # ── 集約の本体 ────────────────────────────────────────────
    def test_same_cwd_sessions_collapse_into_one_project(self):
        """同じプロジェクトで3ターミナル → アバターは1体・crew=3。"""
        put("-Users-test-demo-project", "sess-grp00001.jsonl", "working_tool.jsonl", age=5)
        put("-Users-test-demo-project", "sess-grp00002.jsonl", "waiting_said.jsonl", age=400)
        put("-Users-test-demo-project", "sess-grp00003.jsonl", "working_tool.jsonl", age=60)
        data = office.scan_office()

        self.assertEqual(len(data["employees"]), 3, "employees[] は集約しない（旧UI互換）")
        self.assertEqual(len(data["roster"]), 1)
        proj = data["roster"][0]
        self.assertEqual(proj["crew"], 3)
        self.assertEqual(proj["name"], "demo-project")
        self.assertEqual(len(proj["sessions"]), 3)

    def test_different_cwd_stays_separate(self):
        put("-Users-test-demo-project", "sess-grp00010.jsonl", "working_tool.jsonl", age=5)
        put("-Users-test-other-project", "sess-grp00011.jsonl", "other_project.jsonl", age=5)
        data = office.scan_office()
        by_name = projects_of(data)
        self.assertEqual(len(data["roster"]), 2)
        self.assertEqual(by_name["demo-project"]["crew"], 1)
        self.assertEqual(by_name["other-project"]["crew"], 1)

    def test_project_id_is_stable_and_carries_no_path(self):
        """projectId は席の永続化キー。パスを含まないので中継にも載せられる。"""
        put("-Users-test-demo-project", "sess-grp00020.jsonl", "working_tool.jsonl", age=5)
        first = office.scan_office()["roster"][0]["projectId"]
        office._cache["t"] = 0.0
        put("-Users-test-demo-project", "sess-grp00021.jsonl", "waiting_said.jsonl", age=9)
        second = office.scan_office()["roster"][0]["projectId"]
        self.assertEqual(first, second, "セッションが増えても同じプロジェクトなら不変")
        self.assertRegex(first, r"^[0-9a-f]{12}$")
        self.assertNotIn("Users", first)
        self.assertNotIn("/", first)

    # ── 代表セッションの選び方 ────────────────────────────────
    def test_representative_prefers_attention_over_recency(self):
        """❗を出しているセッションが居れば、より新しいセッションより優先される。
        （指示の宛先＝返事を待っている本人になる）"""
        put("-Users-test-demo-project", "sess-attn00001.jsonl", "ask_question.jsonl", age=200)
        put("-Users-test-demo-project", "sess-fresh00001.jsonl", "working_tool.jsonl", age=3)
        proj = office.scan_office()["roster"][0]
        self.assertEqual(proj["session"], "sess-attn00001")
        self.assertTrue(proj["attention"])
        # 質問文には選択肢が併記される（_question_text の仕様）
        self.assertTrue(proj["question"].startswith("本番に公開しますか？"), proj["question"])

    def test_representative_falls_back_to_newest(self):
        put("-Users-test-demo-project", "sess-old000001.jsonl", "waiting_said.jsonl", age=900)
        put("-Users-test-demo-project", "sess-new000001.jsonl", "working_tool.jsonl", age=4)
        proj = office.scan_office()["roster"][0]
        self.assertEqual(proj["session"], "sess-new000001")
        self.assertFalse(proj["attention"])

    # ── 集約規則の各フィールド ────────────────────────────────
    def test_state_merges_by_priority_and_age_is_freshest(self):
        """1本でも動いていればプロジェクトは working。age は最も新しい活動。"""
        put("-Users-test-demo-project", "sess-rest000001.jsonl", "waiting_said.jsonl", age=2000)
        put("-Users-test-demo-project", "sess-work000001.jsonl", "working_tool.jsonl", age=6)
        proj = office.scan_office()["roster"][0]
        self.assertEqual(proj["state"], "working")
        self.assertLessEqual(proj["age"], 60)

    def test_minions_are_summed_and_pending_is_any(self):
        put("-Users-test-demo-project", "sess-min000001.jsonl", "working_tool.jsonl", age=5)
        put("-Users-test-demo-project", "sess-min000002.jsonl", "working_tool.jsonl", age=6)
        for name in ("sess-min000001", "sess-min000002"):
            sub = _home / ".claude" / "projects" / "-Users-test-demo-project" / name / "subagents"
            sub.mkdir(parents=True, exist_ok=True)
            for i in range(2):
                sp = sub / f"agent-{i}.jsonl"
                sp.write_text('{"type":"assistant"}\n', encoding="utf-8")
                t = time.time() - 20
                os.utime(sp, (t, t))
        inbox = _home / ".claude" / "office_inbox"
        inbox.mkdir(parents=True, exist_ok=True)
        (inbox / "sess-min000002.json").write_text('{"text":"x"}', encoding="utf-8")
        try:
            proj = office.scan_office()["roster"][0]
            self.assertEqual(proj["minions"], 4, "部下は全セッションの合計")
            self.assertTrue(proj["pending"], "1本でも未配達があれば pending")
        finally:
            shutil.rmtree(inbox, ignore_errors=True)

    def test_sessions_breakdown_carries_no_free_text(self):
        """sessions[] は内訳表示用。本文やパスを持たない（中継に載せても安全な形）。"""
        put("-Users-test-demo-project", "sess-brk000001.jsonl", "working_tool.jsonl", age=5)
        proj = office.scan_office()["roster"][0]
        entry = proj["sessions"][0]
        self.assertEqual(set(entry), {"session", "state", "age", "attention", "minions", "pending"})

    # ── external は集約しない ────────────────────────────────
    def test_external_is_not_grouped(self):
        # 契約v1は 600秒で stale 扱いになるので、生成時刻を「いま」にした写しを使う
        import json
        raw = json.loads((FX / "openclaw_status.json").read_text(encoding="utf-8"))
        raw["generatedAt"] = time.time()
        fixture = Path(tempfile.mkdtemp(prefix="oc_fx_")) / "openclaw_status.json"
        fixture.write_text(json.dumps(raw), encoding="utf-8")
        os.environ["OFFICE_OPENCLAW_FIXTURE"] = str(fixture)
        try:
            put("-Users-test-demo-project", "sess-ext000001.jsonl", "working_tool.jsonl", age=5)
            data = office.scan_office()
            ext = [p for p in data["roster"] if p.get("external")]
            claude = [p for p in data["roster"] if not p.get("external")]
            self.assertEqual(len(claude), 1)
            self.assertTrue(ext, "external社員が roster にも出ること")
            for p in ext:
                self.assertEqual(p["crew"], 1, "external はまとめない")
                self.assertEqual(p["cwd"], "")
        finally:
            os.environ.pop("OFFICE_OPENCLAW_FIXTURE", None)

    # ── kind（会議室・考え中の判定材料） ──────────────────────
    def test_kind_is_exposed_for_zone_decisions(self):
        put("-Users-test-demo-project", "sess-kind000001.jsonl", "working_tool.jsonl", age=5)
        data = office.scan_office()
        self.assertIn(data["employees"][0]["kind"], ("tool", "said", "think", "order", "idle"))
        self.assertIn(data["roster"][0]["kind"], ("tool", "said", "think", "order", "idle"))

    # ── 空でも落ちない ────────────────────────────────────────
    def test_empty_office(self):
        data = office.scan_office()
        self.assertEqual(data["roster"], [])


if __name__ == "__main__":
    unittest.main()
