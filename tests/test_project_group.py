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


class R69GroupTest(unittest.TestCase):
    """R69: 採番の安定性・work counts のプロジェクト内合算（group_by_project 直呼び）。"""

    @staticmethod
    def _emp(session, cwd, dept, age=10, **over):
        base = {"session": session, "cwd": cwd, "dept": dept, "age": age,
                "state": "working", "minions": 0, "pending": False}
        base.update(over)
        return base

    def test_numbering_is_stable_across_ordering(self):
        """同名dept×別cwdの「N号」は、employeesの並び順（=mtime変動）が変わっても回転しない。"""
        a = self._emp("sess-aaa", "/w/alpha", "制作本部", age=5)
        b = self._emp("sess-bbb", "/w/beta", "制作本部", age=50)
        d1 = {p["session"]: p["disp"] for p in office.group_by_project([a, b])}
        d2 = {p["session"]: p["disp"] for p in office.group_by_project([b, a])}
        self.assertEqual(d1, d2, "並び順で採番が回転した")
        self.assertEqual(sorted(d1.values()), ["制作本部", "制作本部 2号"])

    def test_numbering_survives_lead_change(self):
        """❗で代表が入れ替わっても各グループの disp は不変。"""
        a = self._emp("sess-aaa", "/w/alpha", "制作本部", age=5)
        b = self._emp("sess-bbb", "/w/beta", "制作本部", age=50)
        before = {p["cwd"]: p["disp"] for p in office.group_by_project([a, b])}
        b_attn = dict(b, approvalMin=4, age=1)     # betaが❗＋最新＝順序も変わる
        after = {p["cwd"]: p["disp"] for p in office.group_by_project([b_attn, a])}
        self.assertEqual(before, after)

    def test_work_counts_are_summed_across_sessions(self):
        """countsは全セッション合算・now/next/doneは代表のもの＝代表交代でドーナツが急変しない。"""
        lead = self._emp("sess-lead", "/w/x", "開発", age=1,
                         work={"now": ["A"], "next": [], "done": [],
                               "counts": {"pending": 2, "in_progress": 1, "completed": 0}})
        other = self._emp("sess-other", "/w/x", "開発", age=99,
                          work={"now": ["B"], "next": [], "done": [],
                                "counts": {"pending": 6, "in_progress": 0, "completed": 3}})
        p = office.group_by_project([lead, other])[0]
        self.assertEqual(p["work"]["counts"],
                         {"pending": 8, "in_progress": 1, "completed": 3})
        self.assertEqual(p["work"]["now"], ["A"])          # リストは代表のもの
        # 代表にworkが無くても合算countsは出る（ドーナツ急変の根本）
        lead2 = self._emp("sess-lead", "/w/x", "開発", age=1)
        p2 = office.group_by_project([lead2, other])[0]
        self.assertEqual(p2["work"]["counts"],
                         {"pending": 6, "in_progress": 0, "completed": 3})

    # ── R85-1: title（/renameのセッション名）の決定則 ──────────────
    def test_title_uses_smallest_session_id_not_lead(self):
        """title は「title持ちメンバの sessionId 昇順で最初」＝lead交代で名前がチラつかない。"""
        a = self._emp("sess-aaa", "/w/x", "開発", age=50, title="決済チーム")
        b = self._emp("sess-bbb", "/w/x", "開発", age=5, title="別名セッション")
        p = office.group_by_project([b, a])[0]
        self.assertEqual(p["title"], "決済チーム")
        # ❗で代表が b に入れ替わっても title は不変
        b_attn = dict(b, approvalMin=4, age=1)
        p2 = office.group_by_project([b_attn, a])[0]
        self.assertEqual(p2["session"], "sess-bbb", "前提: 代表は❗のbへ交代")
        self.assertEqual(p2["title"], "決済チーム", "lead交代でtitleがチラついた")

    def test_title_skips_untitled_and_defaults_empty(self):
        a = self._emp("sess-aaa", "/w/x", "開発", age=5)                       # title無し
        b = self._emp("sess-bbb", "/w/x", "開発", age=50, title="決済チーム")
        self.assertEqual(office.group_by_project([a, b])[0]["title"], "決済チーム")
        self.assertEqual(office.group_by_project([a])[0]["title"], "")

    def test_title_does_not_affect_disp_numbering(self):
        """title があっても disp・N号採番は1文字も変わらない（採番ピンの延長）。"""
        a = self._emp("sess-aaa", "/w/alpha", "制作本部", age=5, title="PollAI")
        b = self._emp("sess-bbb", "/w/beta", "制作本部", age=50)
        d = {p["cwd"]: p["disp"] for p in office.group_by_project([a, b])}
        self.assertEqual(sorted(d.values()), ["制作本部", "制作本部 2号"])
