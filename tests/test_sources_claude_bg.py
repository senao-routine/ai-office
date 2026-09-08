# -*- coding: utf-8 -*-
"""公式 Claude 状態アダプタ: golden、3キー突合、本文非参照、overlay の回帰。"""
import copy
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

TESTS = Path(__file__).resolve().parent
ROOT = TESTS.parent
FX = TESTS / "fixtures" / "claude_bg"
NOW = 1700000030.0
_spec = importlib.util.spec_from_file_location(
    "source_claude_bg_t", ROOT / "server" / "source_claude_bg.py")
source = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(source)


def fixture(name):
    return json.loads((FX / name).read_text(encoding="utf-8"))


class NoBody(dict):
    """本文キーを参照した時点で失敗する、読み取り済み JSON の代役。"""

    def __getitem__(self, key):
        if key in ("intent", "label", "output", "dispatch", "respawnFlags") or key.startswith("bridge"):
            raise AssertionError("body field was accessed")
        return super().__getitem__(key)

    def get(self, key, default=None):
        return self[key] if key in self else default


class ParseJobTest(unittest.TestCase):
    def golden(self, number, state, detail, tempo, tasks, queued, kinds, fan, tokens, name, name_source):
        expected = {
            "id": f"b000000{number}", "state": state, "detail": detail, "tempo": tempo,
            "inFlight": {"tasks": tasks, "queued": queued, "kinds": kinds},
            "fan": fan, "tokens": tokens, "name": name, "nameSource": name_source,
            "sessionId": f"session-launch-{number}", "resumeSessionId": f"session-resume-{number}",
            "transcriptStem": f"transcript-{number}", "cwd": "/fixture/project", "template": "bg",
            "createdAt": 1699999400.0, "updatedAt": 1700000000.0 - (number - 1) * 10,
            "age": 30.0 + (number - 1) * 10,
        }
        raw = fixture(f"state_{state}.json")
        self.assertEqual(source.parse_job_state(raw, NOW), expected)
        self.assertEqual(source.parse_job_state(json.dumps(raw), NOW), expected)

    def test_golden_working(self):
        self.golden(1, "working", "合成テストを実行中", "busy", 2, 1, ["local_bash", "todo"],
                    {"shell": 2, "todo": 3, "running": 1, "done": 2}, 1200, "fixture-bot", "auto")

    def test_golden_blocked(self):
        self.golden(2, "blocked", "入力を待っています", "idle", 0, 1, ["todo"],
                    {"shell": 0, "todo": 1, "running": 0, "done": 0}, 1500, "fixture-waiter", "user")

    def test_golden_done(self):
        self.golden(3, "done", "合成テストが完了しました", "idle", 0, 0, [],
                    {"shell": 1, "todo": 1, "running": 0, "done": 2}, 1800, "fixture-finisher", "auto")

    def test_body_is_absent_and_never_accessed(self):
        for state in ("working", "blocked", "done"):
            raw = NoBody(fixture(f"state_{state}.json"))
            raw["fan"] = [NoBody(entry) for entry in raw["fan"]]
            result = source.parse_job_state(raw, NOW)
            dumped = json.dumps(result)
            for forbidden in ("intent", "label", "output", "result", "respawnFlags", "bridge", "dummy"):
                self.assertNotIn(forbidden, dumped)

    def test_invalid_job_returns_none(self):
        good = fixture("state_working.json")
        for raw in ("{broken", "[]", "null", "1", [], None, 1, {},
                    dict(good, state="unknown"), dict(good, state={}),
                    dict(good, sessionId=""), dict(good, sessionId=None), dict(good, sessionId="  ")):
            with self.subTest(raw=raw):
                self.assertIsNone(source.parse_job_state(raw, NOW))

    def test_failed_and_stopped_are_valid(self):
        for state in ("failed", "stopped"):
            raw = dict(fixture("state_done.json"), state=state)
            self.assertEqual(source.parse_job_state(raw, NOW)["state"], state)

    def test_detail_has_200_character_limit(self):
        raw = dict(fixture("state_working.json"), detail="あ" * 201)
        self.assertEqual(source.parse_job_state(raw, NOW)["detail"], "あ" * 200)

    def test_malformed_optional_fields_do_not_leak_or_raise(self):
        raw = dict(fixture("state_working.json"), detail={"intent": "dummy"},
                   name=["dummy"], tokens=float("inf"), createdAt=False, updatedAt="invalid",
                   linkScanPath=None, inFlight={"tasks": [], "queued": -1, "kinds": ["todo", {"label": "dummy"}]},
                   fan=[None, "dummy", {"kind": {}}, {"kind": "todo", "startedAt": 1000, "doneAt": 0}])
        result = source.parse_job_state(raw, NOW)
        self.assertEqual(result["inFlight"], {"tasks": 0, "queued": 0, "kinds": ["todo"]})
        self.assertEqual(result["fan"], {"shell": 0, "todo": 1, "running": 1, "done": 0})
        self.assertEqual((result["detail"], result["name"], result["tokens"], result["transcriptStem"]), ("", "", 0, ""))
        self.assertEqual((result["createdAt"], result["updatedAt"]), (0.0, 0.0))
        self.assertNotIn("dummy", json.dumps(result))
        raw.update(inFlight=[], fan={}, updatedAt=float("nan"))
        self.assertEqual(source.parse_job_state(raw, NOW)["fan"]["todo"], 0)

    def test_done_counts_all_fan_kinds_but_running_only_todo(self):
        raw = dict(fixture("state_working.json"), fan=[
            {"kind": "shell", "startedAt": 1000},
            {"kind": "monitor", "startedAt": 1000, "doneAt": 2000},
            {"kind": "todo", "startedAt": 1000, "doneAt": 2000},
        ])
        self.assertEqual(source.parse_job_state(raw, NOW)["fan"],
                         {"shell": 1, "todo": 1, "running": 0, "done": 2})

    def test_epoch_seconds_milliseconds_and_iso_offset(self):
        raw = fixture("state_working.json")
        for timestamp in (1700000000, 1700000000000, "2023-11-15T07:13:20+09:00"):
            result = source.parse_job_state(dict(raw, updatedAt=timestamp), NOW)
            self.assertEqual(result["updatedAt"], 1700000000.0)
            self.assertEqual(result["age"], 30.0)


class ParseRosterAgentsTest(unittest.TestCase):
    def test_roster_golden_and_dispatch_nonaccess(self):
        raw = fixture("roster.json")
        raw["workers"]["b0000001"] = NoBody(raw["workers"]["b0000001"])
        expected = {"b0000001": {"pid": 456, "sessionId": "session-launch-1",
                                  "cwd": "/fixture/project", "startedAt": 1699999400.0}}
        self.assertEqual(source.parse_roster(raw), expected)
        self.assertEqual(source.parse_roster(json.dumps(raw)), expected)
        for word in ("dispatch", "flagArgs", "dummy", "procStart"):
            self.assertNotIn(word, json.dumps(source.parse_roster(raw)))

    def test_roster_discards_poison_workers(self):
        raw = fixture("roster.json")
        good = dict(raw["workers"])
        for value in (None, [], "dummy", {}, {"pid": [], "sessionId": "bad"},
                      {"pid": -1, "sessionId": "bad"}, {"pid": 1, "sessionId": ""}):
            raw["workers"]["bad"] = value
            self.assertEqual(source.parse_roster(raw), source.parse_roster({"workers": good}))
        for raw in ("{bad", "[]", [], None, {}, {"workers": []}):
            self.assertEqual(source.parse_roster(raw), {})

    def test_agents_golden_includes_interactive_waiting(self):
        expected = {
            "session-launch-1": {"id": "b0000001", "kind": "background", "pid": 456,
                                 "name": "fixture-bot", "status": "running", "state": "working",
                                 "waiting": False, "cwd": "/fixture/project", "startedAt": 1699999400.0},
            "session-interactive-1": {"id": "i0000001", "kind": "interactive", "pid": 789,
                                      "name": "fixture-interactive", "status": "idle", "state": "blocked",
                                      "waiting": True, "cwd": "/fixture/interactive", "startedAt": 1699999500.0},
        }
        raw = fixture("agents.json")
        self.assertEqual(source.parse_agents_json(raw), expected)
        self.assertEqual(source.parse_agents_json(json.dumps(raw)), expected)
        self.assertNotIn("waitingFor", json.dumps(expected))

    def test_agents_waiting_status_without_waiting_for(self):
        raw = dict(fixture("agents.json")[0], status="waiting", waitingFor="")
        self.assertIs(source.parse_agents_json([raw])[raw["sessionId"]]["waiting"], True)
        raw.update(status="running", waitingFor=None)
        self.assertIs(source.parse_agents_json([raw])[raw["sessionId"]]["waiting"], False)

    def test_agents_discard_poison_rows(self):
        good = fixture("agents.json")
        for bad in (None, "dummy", [], {}, dict(good[0], sessionId=""), dict(good[0], kind="unknown"),
                    dict(good[0], state="unknown"), dict(good[0], pid=[]), dict(good[0], pid=True)):
            self.assertEqual(source.parse_agents_json(good + [bad]), source.parse_agents_json(good))
        for raw in (None, {}, "{bad", '"dummy"', "null"):
            self.assertEqual(source.parse_agents_json(raw), {})


class FileSourceTest(unittest.TestCase):
    def setUp(self):
        # 合成 home もリポジトリの fixture 配下に限定する。
        self.tmp = tempfile.TemporaryDirectory(prefix="runtime-", dir=FX)
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name)
        self.env = mock.patch.dict(os.environ, {
            "OFFICE_HOME": str(self.home), "OFFICE_AGENTS_CLI": "0", "OFFICE_AGENTS_FIXTURE": "",
        })
        self.env.start()
        self.addCleanup(self.env.stop)
        self.cli = mock.patch.object(source.subprocess, "run", side_effect=AssertionError("real CLI forbidden"))
        self.cli.start()
        self.addCleanup(self.cli.stop)

    def write_job(self, short="b0000001", raw=None):
        path = self.home / ".claude" / "jobs" / short / "state.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(raw if raw is not None else fixture("state_working.json")), encoding="utf-8")
        return path

    def write_roster(self, raw=None):
        path = self.home / ".claude" / "daemon" / "roster.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(raw if raw is not None else fixture("roster.json")), encoding="utf-8")

    def test_three_aliases_resolve_to_same_record(self):
        self.write_job()
        self.write_roster()
        index = source.bg_index(os.environ["OFFICE_HOME"], NOW)
        record = index.lookup("session-launch-1")
        self.assertIs(record, index.lookup("session-resume-1"))
        self.assertIs(record, index.lookup("transcript-1"))
        self.assertIs(record, index.records[0])
        self.assertNotEqual(record["sessionId"], record["resumeSessionId"])
        self.assertIsNone(index.lookup("missing"))
        self.assertEqual(index.roster, source.parse_roster(fixture("roster.json")))
        self.assertEqual(record["roster"]["pid"], 456)
        self.assertEqual(source.overlay({"state": "resting"}, record, None, NOW)["pid"], 456)

    def test_show_window_uses_updated_time_not_file_mtime(self):
        raw = fixture("state_working.json")
        self.write_job("stale", dict(raw, updatedAt=NOW - 10801))
        self.write_job("edge", dict(raw, updatedAt=NOW - 10800, daemonShort="edge"))
        self.write_job("invalid", dict(raw, updatedAt="bad"))
        index = source.bg_index(self.home, NOW)
        self.assertEqual([record["id"] for record in index.records], ["edge"])
        self.assertEqual(source.bg_index(self.home, NOW, show_window=100).records, [])

    def test_newest_job_wins_shared_transcript(self):
        raw = fixture("state_working.json")
        self.write_job("a-new", dict(raw, daemonShort="new", updatedAt=NOW - 1))
        self.write_job("z-old", dict(raw, daemonShort="old", updatedAt=NOW - 60))
        index = source.bg_index(self.home, NOW)
        self.assertEqual(len(index.records), 2)
        for key in ("session-launch-1", "session-resume-1", "transcript-1"):
            self.assertEqual(index.lookup(key)["id"], "new")
        # 別の起動 ID 同士でも、共有する stem は新しい job を指す。
        self.write_job("a-new", dict(raw, daemonShort="new", sessionId="new-launch", updatedAt=NOW - 1))
        index = source.bg_index(self.home, NOW)
        self.assertIs(index.lookup("transcript-1"), index.lookup("new-launch"))

    def test_missing_corrupt_files_and_unread_timeline(self):
        empty = source.bg_index(self.home, NOW)
        self.assertEqual((empty.records, empty.roster), ([], {}))
        path = self.write_job()
        path.with_name("timeline.jsonl").write_text((FX / "timeline.jsonl").read_text(), encoding="utf-8")
        self.write_job("broken").write_text("{bad", encoding="utf-8")
        self.write_roster()
        (self.home / ".claude" / "daemon" / "roster.json").write_bytes(b"\xff")
        with mock.patch.object(source, "_read_json", wraps=source._read_json) as read:
            index = source.bg_index(self.home, NOW)
        self.assertEqual(len(index.records), 1)
        self.assertEqual(index.roster, {})
        self.assertEqual({call.args[0].name for call in read.call_args_list}, {"state.json", "roster.json"})

    def test_roster_from_other_session_does_not_supply_pid(self):
        self.write_job()
        roster = fixture("roster.json")
        roster["workers"]["b0000001"]["sessionId"] = "other-session"
        self.write_roster(roster)
        record = source.bg_index(self.home, NOW).records[0]
        self.assertNotIn("roster", record)

    def test_agents_fixture_takes_precedence_over_disabled_cli(self):
        with mock.patch.dict(os.environ, {"OFFICE_AGENTS_FIXTURE": str(FX / "agents.json")}):
            self.assertEqual(source.agents_cli(self.home, NOW), source.parse_agents_json(fixture("agents.json")))
        self.assertEqual(source.agents_cli(self.home, NOW), {})

    def test_agents_missing_and_broken_fixture_are_silent(self):
        path = self.home / "agents.json"
        with mock.patch.dict(os.environ, {"OFFICE_AGENTS_FIXTURE": str(path)}):
            self.assertEqual(source.agents_cli(self.home, NOW), {})
            path.write_text("{bad", encoding="utf-8")
            self.assertEqual(source.agents_cli(self.home, NOW), {})
            path.write_bytes(b"\xff")
            self.assertEqual(source.agents_cli(self.home, NOW), {})


class MakeHomeTest(unittest.TestCase):
    def test_make_home_job_and_interactive_agent(self):
        # make_home.py の生成物だけを OFFICE_HOME として使い、実 HOME を読まない。
        with tempfile.TemporaryDirectory(prefix="make-home-", dir=FX) as temp:
            env = dict(os.environ, TMPDIR=temp, OFFICE_AGENTS_CLI="0", OFFICE_AGENTS_FIXTURE="")
            completed = subprocess.run([sys.executable, str(TESTS / "make_home.py")],
                                       capture_output=True, text=True, check=True, env=env)
            home = Path(completed.stdout.strip())
            with mock.patch.dict(os.environ, {"OFFICE_HOME": str(home), "OFFICE_AGENTS_CLI": "0",
                                             "OFFICE_AGENTS_FIXTURE": str(home / ".claude" / "jobs" / "agents.json")}):
                index = source.bg_index(os.environ["OFFICE_HOME"], time.time())
                record = index.lookup("sess-verify0001")
                self.assertIsNotNone(record)
                self.assertEqual(record["id"], "j0000001")
                self.assertEqual(record["fan"], {"shell": 1, "todo": 1, "running": 0, "done": 0})
                self.assertEqual(record["roster"]["pid"], 456)
                agents = source.agents_cli(home, time.time())
                self.assertEqual(agents["sess-verify0002"]["kind"], "interactive")
                self.assertIs(agents["sess-verify0002"]["waiting"], True)


class OverlayTest(unittest.TestCase):
    def setUp(self):
        self.info = {"state": "working", "age": 30, "name": "", "title": "transcript title",
                     "listening": True, "ask": None}
        self.bg = source.parse_job_state(fixture("state_working.json"), NOW)
        self.agent = source.parse_agents_json(fixture("agents.json"))["session-launch-1"]

    def test_ask_preserves_state_above_agent_and_job(self):
        for state in ("working", "waiting", "resting"):
            info = dict(self.info, state=state, ask={"tool": "approval"})
            result = source.overlay(info, dict(self.bg, state="blocked"), dict(self.agent, waiting=True), NOW)
            self.assertEqual(result["state"], state)
            self.assertIn("bg", result)

    def test_agent_waiting_overrides_job_done(self):
        result = source.overlay(self.info, dict(self.bg, state="done"), dict(self.agent, waiting=True), NOW)
        self.assertEqual(result["state"], "waiting")

    def test_job_blocked_maps_to_waiting(self):
        result = source.overlay(self.info, dict(self.bg, state="blocked"), self.agent, NOW)
        self.assertEqual(result["state"], "waiting")

    def test_fresh_transcript_working_survives_terminal_job(self):
        for state in ("done", "failed", "stopped"):
            result = source.overlay(dict(self.info, age=24.99), dict(self.bg, state=state), None, NOW)
            self.assertEqual(result["state"], "working")

    def test_terminal_job_rests_after_25_seconds(self):
        for state in ("done", "failed", "stopped"):
            for age in (25, 300):
                result = source.overlay(dict(self.info, age=age), dict(self.bg, state=state), None, NOW)
                self.assertEqual(result["state"], "resting")
        result = source.overlay(dict(self.info, state="waiting", age=1), dict(self.bg, state="done"), None, NOW)
        self.assertEqual(result["state"], "resting")

    def test_working_job_freshness_boundary_and_fallback(self):
        info = dict(self.info, state="resting")
        for age, expected in ((239.99, "working"), (240, "resting"), (300, "resting")):
            result = source.overlay(info, dict(self.bg, age=age), None, NOW)
            self.assertEqual(result["state"], expected)
        self.assertEqual(source.overlay(info, None, None, NOW), info)

    def test_overlay_preserves_inputs_title_and_listening(self):
        before = copy.deepcopy((self.info, self.bg, self.agent))
        result = source.overlay(self.info, self.bg, self.agent, NOW)
        self.assertIsNot(result, self.info)
        self.assertEqual((self.info, self.bg, self.agent), before)
        self.assertEqual(result["title"], "transcript title")
        self.assertIs(result["listening"], True)
        expected_keys = {"id", "name", "nameSource", "state", "tempo", "detail", "inFlight", "fan", "template"}
        self.assertEqual(result["bg"], {key: self.bg[key] for key in expected_keys})
        result["bg"]["inFlight"]["kinds"].append("new")
        result["bg"]["fan"]["todo"] = 999
        self.assertEqual(self.bg, before[1])
        self.assertNotIn("listening", source.overlay({"state": "resting"}, self.bg, None, NOW))
        self.assertIs(source.overlay(dict(self.info, listening=False), self.bg, self.agent, NOW)["listening"], False)

    def test_names_and_pid_precedence(self):
        bg = dict(self.bg, roster={"pid": 999})
        result = source.overlay(self.info, bg, self.agent, NOW)
        self.assertEqual((result["name"], result["pid"]), ("fixture-bot", 456))
        result = source.overlay(dict(self.info, name="existing"), bg, None, NOW)
        self.assertEqual((result["name"], result["pid"]), ("existing", 999))
        result = source.overlay(self.info, dict(bg, name=""), dict(self.agent, name="from-cli", pid=0), NOW)
        self.assertEqual((result["name"], result["pid"]), ("from-cli", 999))
        result = source.overlay(dict(self.info, pid=321), None, None, NOW)
        self.assertEqual(result["pid"], 321)

    def test_interactive_waiting_without_background_job(self):
        agent = source.parse_agents_json(fixture("agents.json"))["session-interactive-1"]
        result = source.overlay(self.info, None, agent, NOW)
        self.assertEqual((result["state"], result["name"], result["pid"]), ("waiting", "fixture-interactive", 789))
        self.assertNotIn("bg", result)


if __name__ == "__main__":
    unittest.main()
