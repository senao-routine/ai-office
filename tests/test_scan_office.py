# -*- coding: utf-8 -*-
"""scan_office のテスト（OFFICE_HOME フィクスチャで社員一覧を検証）"""
import importlib.util
import json
import os
import shutil
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

TESTS = Path(__file__).resolve().parent
ROOT = TESTS.parent
FX = TESTS / "fixtures"

_home = Path(tempfile.mkdtemp(prefix="office_scan_home_"))
os.environ["OFFICE_HOME"] = str(_home)
spec = importlib.util.spec_from_file_location(
    "office_server_scan", ROOT / "server" / "office_server.py")
office = importlib.util.module_from_spec(spec)
spec.loader.exec_module(office)


def put_session(proj, name, fixture, age):
    d = _home / ".claude" / "projects" / proj
    d.mkdir(parents=True, exist_ok=True)
    p = d / name
    shutil.copy(FX / fixture, p)
    t = time.time() - age
    os.utime(p, (t, t))
    return p


class ScanOfficeTest(unittest.TestCase):
    def setUp(self):
        proj_root = _home / ".claude" / "projects"
        if proj_root.exists():
            shutil.rmtree(proj_root)
        office._cache["t"] = 0.0

    def test_scan_finds_and_numbers_employees(self):
        put_session("-Users-test-demo-project", "sess-aaaa0001.jsonl", "working_tool.jsonl", age=10)
        put_session("-Users-test-demo-project", "sess-aaaa0002.jsonl", "waiting_said.jsonl", age=600)
        data = office.scan_office()
        emps = data["employees"]
        self.assertEqual(len(emps), 2)
        disps = sorted(e["disp"] for e in emps)
        self.assertEqual(disps[0], "demo-project")
        self.assertTrue(disps[1].startswith("demo-project 2号"))
        self.assertEqual(data["counts"]["working"], 1)
        self.assertEqual(data["counts"]["waiting"], 1)

    def test_old_sessions_hidden(self):
        put_session("-Users-test-demo-project", "sess-old0001.jsonl", "waiting_said.jsonl",
                    age=office.SHOW_WINDOW + 100)
        data = office.scan_office()
        self.assertEqual(len(data["employees"]), 0)

    def test_r80_no_sprite_fields(self):
        """R80: スプライト生成パイプラインは撤去済み（3D/モノグラム化で誰も表示しない）。
        誤って sprite フィールドの供給が復活したらここで気づく（不可視の$0.4生成の再発防止）。"""
        put_session("-Users-test-demo-project", "sess-aaaa0003.jsonl", "working_tool.jsonl", age=10)
        data = office.scan_office()
        self.assertNotIn("sprite", data["employees"][0])
        for prj in data["roster"]:
            self.assertNotIn("sprite", prj)


class OverlayScanTest(unittest.TestCase):
    NOW = 1_800_000_000.0

    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix="office_overlay_")
        self.addCleanup(tmp.cleanup)
        self.home = Path(tmp.name)
        claude = self.home / ".claude"
        self.config = {"projects": {}}
        for name, value in {
            "_HOME": self.home, "PROJECTS": claude / "projects",
            "INBOX": claude / "office_inbox", "APPROVALS": claude / "office_approvals",
            "HISTORY_FILE": claude / "office_inbox/_history.json", "DATA": self.home,
            "_cache": {"t": 0.0, "data": None}, "office_events": None,
            "_LAST_PRUNE": [0.0],
        }.items():
            self._patch(patch.object(office, name, value))
        self._patch(patch.dict(os.environ, {
            "OFFICE_AGENTS_CLI": "0", "OFFICE_SOURCES_CODEX": "0",
            "OFFICE_LANG": "ja", "OFFICE_EDITION": "hybrid",
            "OFFICE_OPENCLAW_FIXTURE": str(self.home / "missing-openclaw.json"),
        }))
        self._patch(patch.object(office.time, "time", return_value=self.NOW))
        self._patch(patch.object(office, "load_config", return_value=self.config))

    def _patch(self, patcher):
        value = patcher.start()
        self.addCleanup(patcher.stop)
        return value

    def session(self, sid="sess-overlay001", age=10, fixture="working_tool.jsonl"):
        path = office.PROJECTS / "demo" / f"{sid}.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(FX / fixture, path)
        os.utime(path, (self.NOW - age, self.NOW - age))
        return path

    def job(self, state, sid="sess-overlay001", **overrides):
        path = self.home / ".claude/jobs" / sid / "state.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        raw = {"sessionId": sid, "daemonShort": "j0000001", "state": state,
               "updatedAt": self.NOW, "cwd": "/work/original"}
        raw.update(overrides)
        path.write_text(json.dumps(raw), encoding="utf-8")

    def test_blocked_job_makes_employee_waiting(self):
        self.session()
        self.job("blocked", detail="入力を待っています")
        data = office.scan_office()
        employee = data["employees"][0]
        self.assertEqual(employee["state"], "waiting")
        self.assertIs(employee["listening"], False)
        self.assertEqual(data["roster"][0]["detail"], "入力を待っています")

    def test_terminal_job_respects_transcript_freshness_boundary(self):
        for state in ("done", "failed", "stopped"):
            for age, expected in ((24, "working"), (25, "resting"), (600, "resting")):
                with self.subTest(state=state, age=age):
                    self.session(age=age)
                    self.job(state)
                    self.assertEqual(office.scan_office()["employees"][0]["state"], expected)

    def test_fresh_working_job_overrides_old_transcript_state(self):
        self.session(age=3000)
        for job_age, expected in ((239, "working"), (240, "resting")):
            with self.subTest(job_age=job_age):
                self.job("working", updatedAt=self.NOW - job_age)
                employee = office.scan_office()["employees"][0]
                self.assertEqual(employee["state"], expected)
                self.assertEqual(employee["age"], 3000)
                self.assertIs(employee["listening"], False)

    def test_disabled_sources_match_legacy_employee_json_golden(self):
        self.session("sess-golden001", 10)
        self.session("sess-golden002", 600, "waiting_said.jsonl")
        # R90-D3 適用前の fixture 出力を固定。vendor 以外は全キー・値・型を比較する。
        golden = json.loads('''[
          {"session":"sess-golden001","cwd":"/Users/test/demo-project","branch":"main",
           "title":"","age":10,"mtime":1799999990.0,"state":"working","kind":"tool",
           "verb":"実行中","target":"push to origin",
           "feed":["実行中 push to origin","💬 了解です。まずテストを回します。"],
           "skills":[],"minions":0,"pending":false,"listening":false,
           "lastSaid":"了解です。まずテストを回します。","lastOrder":"デプロイを進めて",
           "question":"","approvalMin":0,"stuckTool":"","dept":"demo-project","role":"",
           "disp":"demo-project"},
          {"session":"sess-golden002","cwd":"/Users/test/demo-project","branch":"main",
           "title":"","age":600,"mtime":1799999400.0,"state":"waiting","kind":"said",
           "verb":"指示待ち","target":"完了しました。次の指示をください。",
           "feed":["💬 完了しました。次の指示をください。"],
           "skills":[],"minions":0,"pending":false,"listening":false,
           "lastSaid":"完了しました。次の指示をください。","lastOrder":"まとめて",
           "question":"","approvalMin":0,"stuckTool":"","dept":"demo-project","role":"",
           "disp":"demo-project 2号"}
        ]''')
        for relative in (".claude/jobs", ".claude/office_events", ".codex"):
            self.assertFalse((self.home / relative).exists())
        employees = office.office_json()["employees"]
        self.assertTrue(all(e["vendor"] == "claude" for e in employees))
        actual = [{k: v for k, v in e.items() if k != "vendor"} for e in employees]
        self.assertEqual(json.dumps(actual, sort_keys=True), json.dumps(golden, sort_keys=True))

    def test_fresh_jobs_do_not_resurrect_expired_or_missing_transcripts(self):
        self.session("sess-expired001", office.SHOW_WINDOW + 100)
        path = self.session("sess-touched001")
        lines = [dict(json.loads(line), timestamp=self.NOW - office.SHOW_WINDOW - 100)
                 for line in path.read_text(encoding="utf-8").splitlines()]
        path.write_text("\n".join(json.dumps(line) for line in lines), encoding="utf-8")
        os.utime(path, (self.NOW, self.NOW))
        for sid in ("sess-expired001", "sess-touched001", "sess-missing001"):
            self.job("working", sid)
        data = office.scan_office()
        self.assertEqual(data["employees"], [])
        self.assertEqual(data["sources"]["claude"], {"fg": 0, "bg": 0, "agentsCli": False})

    def test_no_events_matches_full_json_without_event_adapter(self):
        from tests.test_office_events import load_events
        self.session("sess-golden001", 10)
        self.session("sess-golden002", 600, "waiting_said.jsonl")
        baseline = office.scan_office()
        events = load_events()
        events._poll(self.home)
        with patch.object(office, "office_events", events):
            actual = office.scan_office()
        self.assertEqual(json.dumps(actual, sort_keys=True), json.dumps(baseline, sort_keys=True))
        self.assertEqual(actual["events"], {"seq": 0, "wired": False, "lastTs": 0})
        self.assertFalse((self.home / events.EVENTS_DIR).exists())

    def test_session_end_hides_employee_and_reports_event_metadata(self):
        from tests.test_office_events import load_events
        self.session()
        events = load_events()
        events._ingest(json.dumps({"v": 1, "ts": self.NOW, "sid": "sess-overlay001", "ev": "SessionEnd"}))
        with patch.object(office, "office_events", events), patch.object(office, "events_wired", return_value=True):
            data = office.scan_office()
            self.assertEqual(data["employees"], [])
            self.assertEqual(data["events"], {"seq": 1, "wired": True, "lastTs": self.NOW})
            with patch.object(office, "pending_approval", return_value={"kind": "permission", "ts": self.NOW}):
                self.assertEqual(len(office.scan_office()["employees"]), 1)

    def test_event_prune_is_hourly_even_without_inbox(self):
        events = SimpleNamespace(prune=Mock())
        with patch.object(office, "office_events", events):
            office.prune_inbox_litter(self.NOW)
            office.prune_inbox_litter(self.NOW + 3599)
            events.prune.assert_called_once_with(self.home)
            office.prune_inbox_litter(self.NOW + 3600)
            self.assertEqual(events.prune.call_count, 2)

    def test_serve_starts_events_unless_disabled_and_dump_never_starts(self):
        import sys
        for disabled, dump in ((False, False), (True, False), (False, True)):
            with self.subTest(disabled=disabled, dump=dump):
                events = SimpleNamespace(start=Mock(), stop=Mock())
                server = Mock()
                server.serve_forever.side_effect = KeyboardInterrupt
                with patch.object(office, "office_events", events), \
                        patch.object(office, "_OfficeHTTPServer", return_value=server), \
                        patch.object(office, "_install_ts_logging"), patch.object(office, "start_watcher"), \
                        patch.object(office, "scan_office", return_value={}), patch("builtins.print"), \
                        patch.object(sys, "argv", ["office_server.py"] + (["--dump"] if dump else [])), \
                        patch.dict(os.environ, OFFICE_EVENTS="0" if disabled else "1"):
                    office.main()
                if disabled or dump:
                    events.start.assert_not_called()
                    events.stop.assert_not_called()
                else:
                    events.start.assert_called_once()
                    self.assertEqual(events.start.call_args.args, (self.home,))
                    office._cache["t"] = self.NOW
                    events.start.call_args.kwargs["on_change"]("Stop")
                    self.assertEqual(office._cache["t"], 0)
                    events.stop.assert_called_once()

    def test_agents_waiting_and_indexes_loaded_once_per_scan(self):
        self.session()
        self.session("sess-other001")
        self.job("working")
        path = self.home / "agents.json"
        path.write_text(json.dumps([{
            "sessionId": "sess-overlay001", "kind": "interactive", "state": "working",
            "pid": 123, "status": "waiting",
        }]), encoding="utf-8")
        with patch.dict(os.environ, OFFICE_AGENTS_CLI="1", OFFICE_AGENTS_FIXTURE=str(path)), \
                patch.object(office.source_claude_bg, "bg_index",
                             wraps=office.source_claude_bg.bg_index) as bg, \
                patch.object(office.source_claude_bg, "agents_cli",
                             wraps=office.source_claude_bg.agents_cli) as agents:
            data = office.scan_office()
            bg.assert_called_once_with(self.home, self.NOW)
            agents.assert_called_once_with(self.home, self.NOW)
            employee = next(e for e in data["employees"] if e["session"] == "sess-overlay001")
            self.assertEqual(employee["state"], "waiting")
            self.assertEqual(data["sources"]["claude"], {"fg": 1, "bg": 1, "agentsCli": True})
            with patch.dict(os.environ, OFFICE_AGENTS_CLI="0"):
                agents.reset_mock()
                data = office.scan_office()
                agents.assert_not_called()
                self.assertIs(data["sources"]["claude"]["agentsCli"], False)

    def test_avatar_style_beats_the_folder_wide_setting_for_one_robot(self):
        """R91: 個体の見た目はフォルダ設定より強い（隣の席を巻き込まない）。"""
        self.session(sid="sess-overlay001")
        self.session(sid="sess-overlay002")
        self.config["projects"] = {"demo": {"name": "開発", "arch": "cap", "color": "oak"}}
        base = {p["projectId"]: p for p in office.scan_office()["roster"]}
        self.assertEqual({(p["arch"], p["color"]) for p in base.values()}, {("cap", "oak")})
        target = sorted(base)[0]
        self.config["avatars"] = {target: {"arch": "beret", "color": "sage", "at": self.NOW}}
        office._cache["t"] = 0.0
        after = {p["projectId"]: p for p in office.scan_office()["roster"]}
        self.assertEqual((after[target]["arch"], after[target]["color"]), ("beret", "sage"))
        for pid, proj in after.items():
            if pid != target:
                self.assertEqual((proj["arch"], proj["color"]), ("cap", "oak"))

    def test_unknown_avatar_style_values_are_ignored_rather_than_served(self):
        self.session()
        self.config["projects"] = {"demo": {"name": "開発", "arch": "cap", "color": "sand"}}
        pid = office.scan_office()["roster"][0]["projectId"]
        self.config["avatars"] = {pid: {"arch": "sombrero", "color": "neon"}}
        office._cache["t"] = 0.0
        proj = office.scan_office()["roster"][0]
        self.assertEqual((proj["arch"], proj["color"]), ("cap", "sand"))

    def test_choosing_default_on_one_robot_clears_the_folder_wide_color(self):
        """Astra レビュー指摘: 「既定」を押してもフォルダの色が残り、押しても何も起きない。"""
        self.session()
        self.config["projects"] = {"demo": {"name": "開発", "arch": "cap", "color": "oak"}}
        pid = office.scan_office()["roster"][0]["projectId"]
        self.config["avatars"] = {pid: {"color": None, "arch": None}}
        office._cache["t"] = 0.0
        proj = office.scan_office()["roster"][0]
        self.assertNotIn("color", proj)
        self.assertIsNone(proj["arch"])

    def test_a_hand_broken_avatars_table_cannot_take_down_the_whole_api(self):
        """Astra レビュー指摘: [] を集合に照合すると TypeError で scan_office ごと落ちる。"""
        self.session()
        self.config["projects"] = {"demo": {"name": "開発", "arch": "cap"}}
        pid = office.scan_office()["roster"][0]["projectId"]
        for broken in ([], {}, 3, ["cap"]):
            self.config["avatars"] = {pid: {"arch": broken, "color": broken}}
            office._cache["t"] = 0.0
            proj = office.scan_office()["roster"][0]      # 例外を出さないこと自体が検査
            self.assertEqual(proj["arch"], "cap")
            self.assertNotIn("color", proj)
        self.config["avatars"] = {pid: "not-a-dict"}
        office._cache["t"] = 0.0
        self.assertEqual(office.scan_office()["roster"][0]["arch"], "cap")

    def test_worktree_uses_background_home_project_label_as_fallback(self):
        path = self.session()
        path.write_text(path.read_text(encoding="utf-8").replace(
            "/Users/test/demo-project", "/work/.claude/worktrees/isolated"), encoding="utf-8")
        os.utime(path, (self.NOW - 10, self.NOW - 10))
        self.job("working")
        self.config["projects"] = {"/work/original": {"name": "開発", "role": "実装"}}
        employee = office.scan_office()["employees"][0]
        self.assertEqual((employee["dept"], employee["role"]), ("開発", "実装"))
        self.assertEqual(employee["cwd"], "/work/.claude/worktrees/isolated")
        self.config["projects"]["isolated"] = {"name": "専用部署"}
        self.assertEqual(office.scan_office()["employees"][0]["dept"], "専用部署")
        self.assertEqual(office.project_label("/unmatched", "demo", self.config), ("unmatched", ""))

    def test_hook_overlay_priority_preserves_approval_and_listening(self):
        self.session()
        self.job("blocked")
        hook = Mock(side_effect=lambda info, home, now: dict(info, state="working", listening=True))
        events = SimpleNamespace(overlay=hook, seq=lambda: 0, prune=Mock())
        with patch.object(office, "office_events", events):
            employee = office.scan_office()["employees"][0]
            self.assertEqual(hook.call_args.args[0]["state"], "waiting")
            self.assertEqual(employee["state"], "working")
            self.assertIs(employee["listening"], False)
            ask = {"kind": "permission", "ts": self.NOW, "title": "確認", "options": []}
            with patch.object(office, "pending_approval", return_value=ask):
                hook.reset_mock()
                employee = office.scan_office()["employees"][0]
                hook.assert_not_called()
                self.assertEqual(employee["state"], "waiting")
                self.assertEqual(employee["ask"], ask)


if __name__ == "__main__":
    unittest.main()
