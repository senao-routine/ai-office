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
    """R50-P1 の集約（1アバター=1プロジェクト）。R86-A で実既定は session になったため、
    このクラスは avatarMode=project を明示して集約仕様をピンし続ける。"""

    def setUp(self):
        proj_root = _home / ".claude" / "projects"
        if proj_root.exists():
            shutil.rmtree(proj_root)
        office._cache["t"] = 0.0
        os.environ["OFFICE_AVATAR_MODE"] = "project"
        self.addCleanup(os.environ.pop, "OFFICE_AVATAR_MODE", None)

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


class R86SessionModeTest(unittest.TestCase):
    """R86-A: 1アバター=1セッション（mode="session"・scan_office の実既定＝ユーザー裁定2026-08-26）。
    group_by_project 直呼び。関数既定は "project" のまま＝上のクラス群が集約仕様をピンし続ける。"""

    _emp = staticmethod(R69GroupTest._emp)

    def test_every_session_gets_own_entry(self):
        """同一cwdの3セッション → roster 3件・crew=1・session=自分・titleは自分のもの。"""
        emps = [self._emp("sess-a1", "/w/x", "制作本部", age=5, title="PollAI"),
                self._emp("sess-a2", "/w/x", "制作本部", age=50),
                self._emp("sess-a3", "/w/x", "制作本部", age=99, title="AKOOL")]
        roster = office.group_by_project(emps, mode="session")
        self.assertEqual(len(roster), 3)
        by_sess = {p["session"]: p for p in roster}
        self.assertEqual(set(by_sess), {"sess-a1", "sess-a2", "sess-a3"})
        for p in roster:
            self.assertEqual(p["crew"], 1)
            self.assertEqual(len(p["sessions"]), 1)
            self.assertEqual(p["sessions"][0]["session"], p["session"])
        self.assertEqual(by_sess["sess-a1"]["title"], "PollAI")
        self.assertEqual(by_sess["sess-a2"]["title"], "")
        self.assertEqual(by_sess["sess-a3"]["title"], "AKOOL")

    def test_derived_projectid_unique_stable_and_opaque(self):
        """派生ID: 12hex・同cwd内でも一意・呼び直しで不変・cwd/セッションID平文を含まない・
        projectモードのcwdハッシュとも衝突しない。"""
        emps = [self._emp("sess-a1", "/w/x", "開発", age=5),
                self._emp("sess-a2", "/w/x", "開発", age=50)]
        r1 = office.group_by_project(emps, mode="session")
        r2 = office.group_by_project(emps, mode="session")
        ids = [p["projectId"] for p in r1]
        self.assertEqual(len(set(ids)), 2, "同一cwd内で派生IDが衝突")
        for pid in ids:
            self.assertRegex(pid, r"^[0-9a-f]{12}$")
            self.assertNotIn("sess-a", pid)
            self.assertNotIn("/w/x", pid)
        self.assertEqual(ids, [p["projectId"] for p in r2], "派生IDが呼び直しで変わった")
        proj_id = office.group_by_project(emps, mode="project")[0]["projectId"]
        self.assertNotIn(proj_id, ids, "projectモードのIDと衝突")

    def test_attention_lands_on_own_entry(self):
        """❗は当該セッションのエントリだけに付く（他セッションへ波及しない）。"""
        emps = [self._emp("sess-a1", "/w/x", "開発", age=5),
                self._emp("sess-a2", "/w/x", "開発", age=50,
                          approvalMin=4, question="進めていい?")]
        roster = office.group_by_project(emps, mode="session")
        by_sess = {p["session"]: p for p in roster}
        self.assertTrue(by_sess["sess-a2"]["attention"])
        self.assertEqual(by_sess["sess-a2"]["question"], "進めていい?")
        self.assertFalse(by_sess["sess-a1"]["attention"])

    def test_name_keeps_dept_for_push_filter_and_numbering(self):
        """name=dept 維持（PWA購読フィルタ/deptbar担保）・同名の採番は安定。"""
        emps = [self._emp("sess-a1", "/w/x", "制作本部", age=5),
                self._emp("sess-a2", "/w/x", "制作本部", age=50)]
        r1 = office.group_by_project(emps, mode="session")
        for p in r1:
            self.assertEqual(p["name"], "制作本部")
        d1 = sorted(p["disp"] for p in r1)
        self.assertEqual(d1, ["制作本部", "制作本部 2号"])
        d2 = sorted(p["disp"] for p in office.group_by_project(emps[::-1], mode="session"))
        self.assertEqual(d1, d2, "並び順で採番が回転した")

    def test_external_keeps_ext_style(self):
        """external は従来どおり ext:（projectId=session）＝OpenClaw区画の挙動不変。"""
        emps = [self._emp("sess-a1", "/w/x", "開発", age=5),
                self._emp("oc-123", "", "OpenClaw", age=5, external={"site": "macmini"})]
        roster = office.group_by_project(emps, mode="session")
        ext = [p for p in roster if p.get("external")]
        self.assertEqual(len(ext), 1)
        self.assertEqual(ext[0]["projectId"], "oc-123")

    def test_launch_target_fallback_for_derived_id(self):
        """R86-A: 派生projectId の遠隔▶起動＝rosterから cwd を引くが、projects_index の
        cwd 集合に無いパスは絶対に採用しない（遠隔から任意パス起動不能の不変条件）。"""
        orig_pj, orig_oj = office.projects_index.projects_json, office.office_json
        try:
            office.projects_index.projects_json = lambda: {
                "projects": [{"cwd": "/w/known", "name": "既知PJ"}]}
            derived = office.project_id_for("/w/known\nsess-a1")
            evil = office.project_id_for("/w/evil\nsess-a2")
            office.office_json = lambda: {"roster": [
                {"projectId": derived, "cwd": "/w/known", "title": "決済チーム", "disp": "既知PJ"},
                {"projectId": evil, "cwd": "/w/evil", "disp": "怪しいPJ"},
            ]}
            # 従来経路（cwd直一致）は不変
            self.assertEqual(office._launch_target_for(office.project_id_for("/w/known")),
                             ("/w/known", "既知PJ"))
            # 派生ID → roster経由で cwd 引き当て（labelはtitle優先）
            self.assertEqual(office._launch_target_for(derived), ("/w/known", "決済チーム"))
            # roster に在っても index の cwd 集合外は拒否
            self.assertEqual(office._launch_target_for(evil), ("", ""))
            self.assertEqual(office._launch_target_for("ffffffffffff"), ("", ""))
        finally:
            office.projects_index.projects_json = orig_pj
            office.office_json = orig_oj

    def test_scan_office_default_is_session_mode(self):
        """scan_office の実既定＝session（env/config 未指定）。同cwd2セッションが2アバターになる。"""
        os.environ.pop("OFFICE_AVATAR_MODE", None)
        proj_root = _home / ".claude" / "projects"
        if proj_root.exists():
            shutil.rmtree(proj_root)
        office._cache["t"] = 0.0
        put("-Users-test-demo-project", "sess-sm000001.jsonl", "working_tool.jsonl", age=5)
        put("-Users-test-demo-project", "sess-sm000002.jsonl", "waiting_said.jsonl", age=60)
        data = office.scan_office()
        self.assertEqual(data["avatarMode"], "session")
        self.assertEqual(len(data["roster"]), 2)
        self.assertTrue(all(p["crew"] == 1 for p in data["roster"]))
