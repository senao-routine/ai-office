"""使い捨てHOMEの形式と、本文を取得しないfixture抽出の回帰テスト。"""
import datetime
import importlib.util
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("capture_fixture", ROOT / "tools/capture_fixture.py")
capture = importlib.util.module_from_spec(spec)
spec.loader.exec_module(capture)


class MakeHomeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix="make_home_test_")
        cls.addClassCleanup(cls.tmp.cleanup)
        cls.started = time.time()
        result = subprocess.run(
            [sys.executable, str(ROOT / "tests/make_home.py")],
            env=dict(os.environ, TMPDIR=cls.tmp.name),
            capture_output=True, text=True, check=True,
        )
        cls.stdout = result.stdout
        cls.home = Path(result.stdout)
        cls.job = cls.home / ".claude/jobs/j0000001"

    def test_files_and_existing_transcripts(self):
        self.assertNotIn("\n", self.stdout)
        self.assertEqual(self.home.parent.resolve(), Path(self.tmp.name).resolve())
        for relative in (
            ".claude/jobs/j0000001/state.json", ".claude/jobs/j0000001/timeline.jsonl",
            ".claude/jobs/agents.json", ".claude/daemon/roster.json",
            ".codex/state_5.sqlite", ".codex/thread_history_1.sqlite",
        ):
            with self.subTest(path=relative):
                self.assertTrue((self.home / relative).is_file())
        project = self.home / ".claude/projects/-Users-test-demo-project"
        for name, fixture in (("sess-verify0001", "working_tool"), ("sess-verify0002", "waiting_said")):
            self.assertEqual((project / f"{name}.jsonl").read_bytes(),
                             (ROOT / "tests/fixtures" / f"{fixture}.jsonl").read_bytes())
        rollouts = list((self.home / ".codex/sessions").glob("*/*/*/rollout-test.jsonl"))
        self.assertEqual(len(rollouts), 1)
        self.assertEqual(rollouts[0].read_bytes(), (ROOT / "tests/fixtures/codex_rollout.jsonl").read_bytes())

    def test_background_job(self):
        path = self.job / "state.json"
        state = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(state["state"], "working")
        self.assertEqual(state["sessionId"], "sess-verify0001")
        self.assertEqual(state["resumeSessionId"], state["sessionId"])
        self.assertEqual(state["cwd"], "/Users/test/demo/project")
        link = Path(state["linkScanPath"])
        self.assertTrue(link.is_absolute() and link.is_file())
        self.assertEqual(link.name, "sess-verify0001.jsonl")
        updated = datetime.datetime.fromisoformat(state["updatedAt"].replace("Z", "+00:00")).timestamp()
        self.assertGreaterEqual(updated, self.started - 1)
        self.assertLessEqual(updated, time.time())
        timeline = [json.loads(line) for line in (self.job / "timeline.jsonl").read_text().splitlines()]
        self.assertEqual(len(timeline), 2)
        self.assertTrue(all({"at", "state", "detail", "text"} <= row.keys() for row in timeline))
        roster = json.loads((self.home / ".claude/daemon/roster.json").read_text())
        self.assertEqual(roster["proto"], 1)
        self.assertEqual(list(roster["workers"]), ["j0000001"])
        self.assertEqual(roster["workers"]["j0000001"]["sessionId"], state["sessionId"])
        agents = json.loads((self.job.parent / "agents.json").read_text())
        self.assertEqual(len(agents), 2)
        self.assertEqual([agent["kind"] for agent in agents], ["background", "interactive"])
        self.assertEqual(agents[0]["sessionId"], state["sessionId"])

    def test_events(self):
        directory = self.home / ".claude/office_events"
        files = list(directory.glob("*.jsonl"))
        self.assertEqual(len(files), 1)
        self.assertEqual(directory.stat().st_mode & 0o777, 0o700)
        self.assertEqual(files[0].stat().st_mode & 0o777, 0o600)
        rows = [json.loads(line) for line in files[0].read_text().splitlines()]
        self.assertEqual(len(rows), 3)
        self.assertTrue(all(row["v"] == 1 and row["sid"] == "sess-verify0001" for row in rows))
        self.assertEqual([(row["ev"], row["tool"]) for row in rows],
                         [("PostToolUse", "Edit"), ("PostToolUse", "Bash"), ("Stop", "")])
        self.assertEqual(rows[0]["tgt"], "report.md")
        self.assertEqual(rows[1]["kind"], "git:commit")
        for row in rows:
            self.assertRegex(row["cwdh"], r"^[0-9a-f]{12}$")
            self.assertFalse({"prompt", "command", "tool_response"} & row.keys())

    def test_sqlite_threads_and_history(self):
        with closing(sqlite3.connect(self.home / ".codex/state_5.sqlite")) as db:
            db.row_factory = sqlite3.Row
            rows = {row["id"]: dict(row) for row in db.execute("SELECT * FROM threads")}
            self.assertEqual(len(rows), 3)
            self.assertEqual(rows["cx-parent-a"]["source"], "cli")
            self.assertEqual(rows["cx-parent-a"]["name"], "Codex A")
            self.assertEqual(rows["cx-parent-b"]["source"], "exec")
            self.assertEqual(rows["cx-parent-a"]["updated_at"] - rows["cx-parent-b"]["updated_at"], 600)
            source = json.loads(rows["cx-child-c"]["source"])
            self.assertEqual(source["subagent"]["thread_spawn"]["parent_thread_id"], "cx-parent-a")
            for row in rows.values():
                self.assertEqual([row[key] for key in ("title", "first_user_message", "preview")], ["", "", ""])
            edges = [tuple(row) for row in db.execute("SELECT * FROM thread_spawn_edges")]
            self.assertEqual(edges, [("cx-parent-a", "cx-child-c", "open")])
        with closing(sqlite3.connect(self.home / ".codex/thread_history_1.sqlite")) as db:
            db.row_factory = sqlite3.Row
            turns = {row["thread_id"]: dict(row) for row in db.execute("SELECT * FROM thread_turns")}
            self.assertEqual(len(turns), 3)
            self.assertEqual(turns["cx-parent-a"]["status"], "inProgress")
            self.assertIsNone(turns["cx-parent-a"]["completed_at"])
            self.assertEqual(turns["cx-parent-b"]["duration_ms"], 80000)
            self.assertEqual(turns["cx-child-c"]["status"], "completed")
            items = list(db.execute("SELECT item_type, item_json FROM thread_items"))
            self.assertEqual(len(items), 3)
            self.assertEqual({row["item_type"] for row in items}, {"commandExecution", "agentMessage", "reasoning"})
            for row in items:
                self.assertEqual(json.loads(row["item_json"]), {"type": row["item_type"]})

    def test_capture_codex_reads_only_metadata(self):
        connect = sqlite3.connect
        connections = []

        def metadata_only(database_uri, **kwargs):
            self.assertTrue(database_uri.endswith("?mode=ro"))
            self.assertTrue(kwargs["uri"])
            db = connect(database_uri, **kwargs)

            def authorize(action, table, column, database, trigger):
                if action == sqlite3.SQLITE_READ and column in {
                    "title", "first_user_message", "preview", "item_json", "error_json",
                }:
                    return sqlite3.SQLITE_DENY
                return sqlite3.SQLITE_OK

            db.set_authorizer(authorize)
            connections.append(database_uri)
            return db

        with patch.object(capture.sqlite3, "connect", side_effect=metadata_only):
            data = capture.capture_codex(self.home, 2)
        self.assertEqual(len(connections), 2)
        self.assertEqual([row["id"] for row in data["threads"]], ["cx-parent-a", "cx-child-c"])
        self.assertEqual(len(data["thread_spawn_edges"]), 1)
        self.assertEqual(len(data["thread_turns"]), 2)
        self.assertEqual(len(data["thread_items"]), 2)
        for item in data["thread_items"]:
            self.assertEqual(json.loads(item["item_json"]), {"type": item["item_type"]})
        for thread in data["threads"]:
            self.assertEqual(thread["cwd"], "/Users/test/demo/project")
            self.assertTrue(thread["rollout_path"].startswith("/tmp/redacted/"))
            self.assertFalse({"title", "first_user_message", "preview"} & thread.keys())

    def test_capture_jobs_and_event_redaction(self):
        job = capture.capture_jobs(self.job / "state.json")
        self.assertFalse({"intent", "output", "children"} & job.keys())
        self.assertTrue(job["detail"].startswith("REDACTED("))
        self.assertTrue(job["name"].startswith("REDACTED("))
        self.assertTrue(job["linkScanPath"].startswith("/tmp/redacted/"))
        for item in job["fan"]:
            self.assertFalse({"id", "label"} & item.keys())
        event_file = next((self.home / ".claude/office_events").glob("*.jsonl"))
        rows = [json.loads(line) for line in capture.capture_events(event_file, 2).splitlines()]
        self.assertEqual(len(rows), 2)
        self.assertEqual([row["sid"] for row in rows], ["sess-fixture-1", "sess-fixture-1"])
        self.assertEqual(rows[0]["kind"], "git:commit")


if __name__ == "__main__":
    unittest.main()
