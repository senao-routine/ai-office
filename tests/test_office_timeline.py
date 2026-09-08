"""Timeline persistence, restart recovery, retention and fail-soft wiring."""
from contextlib import closing
from datetime import date, datetime, timedelta
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import sqlite3
import stat
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parent.parent


def load_module(name, filename):
    spec = importlib.util.spec_from_file_location(name, ROOT / "server" / filename)
    module = importlib.util.module_from_spec(spec)
    with patch.object(sys, "path", [str(ROOT / "server"), *sys.path]):
        spec.loader.exec_module(module)
    return module


class OfficeTimelineTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix="office_timeline_")
        self.addCleanup(tmp.cleanup)
        self.home = Path(tmp.name)
        self.env = patch.dict(os.environ, {"OFFICE_HOME": str(self.home), "OFFICE_TIMELINE": "1"})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.events = load_module("events_timeline_test", "office_events.py")
        self.timeline = self.load_timeline()
        self.addCleanup(lambda: self.timeline.stop())
        self.addCleanup(lambda: self.events.stop())
        self.directory = self.home / self.events.EVENTS_DIR
        self.directory.mkdir(parents=True)
        self.path = self.home / self.timeline.DB_FILE
        self.now = time.time()

    def load_timeline(self):
        with patch.dict(sys.modules, {"office_events": self.events}):
            timeline = load_module("timeline_test", "office_timeline.py")
        timeline.BATCH_SECONDS = 0.02
        return timeline

    def employee(self, **values):
        return dict({"session": "session-a", "vendor": "claude", "cwd": "/demo/project",
                     "projectId": "project-a", "disp": "Demo", "state": "working"}, **values)

    def hook(self, ev="PostToolUse", ts=None, **values):
        return dict({"v": 1, "ts": self.now if ts is None else ts,
                     "sid": "session-a", "ev": ev, "cwdh": "123456789abc",
                     "tool": "", "tgt": "", "kind": "", "nt": "", "sub": "",
                     "task": "", "ok": True}, **values)

    def append(self, *rows, day=None):
        path = self.directory / f"{(day or date.today()).isoformat()}.jsonl"
        with path.open("a", encoding="utf-8") as stream:
            for row in rows:
                stream.write(json.dumps(row) + "\n")
        self.events._poll(self.home)

    def query(self, sql, args=()):
        with closing(sqlite3.connect(self.path.as_uri() + "?mode=ro", uri=True)) as db:
            return db.execute(sql, args).fetchall()

    def wait_for(self, predicate):
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            try:
                if predicate():
                    return
            except sqlite3.Error:
                pass
            time.sleep(0.01)
        self.fail("writer did not reach expected state")

    def test_schema_private_wal_single_writer_and_one_second_batches(self):
        self.timeline.BATCH_SECONDS = 1.0
        self.timeline.start(self.home)
        writer = self.timeline._WRITER
        self.timeline.start(self.home)
        self.assertIs(writer, self.timeline._WRITER)
        self.assertTrue(writer.thread.daemon)
        self.assertEqual(writer.thread.name, "office-timeline")
        self.timeline.record_states([self.employee()], self.now)
        time.sleep(0.05)
        self.assertFalse(self.path.exists())
        self.wait_for(lambda: self.query("SELECT COUNT(*) FROM sessions")[0][0] == 1)
        self.assertEqual(stat.S_IMODE(self.path.stat().st_mode), 0o600)
        self.assertEqual(self.query("PRAGMA journal_mode"), [("wal",)])
        self.assertEqual(self.query("SELECT v FROM meta WHERE k='version'"), [("1",)])
        self.assertEqual([r[1] for r in self.query("PRAGMA table_info(events)")],
                         ["id", "ts", "day", "sid", "vendor", "ev", "tool", "tgt", "kind",
                          "nt", "sub", "task", "ok", "seq"])
        for suffix in ("-wal", "-shm"):
            self.assertEqual(stat.S_IMODE(Path(str(self.path) + suffix).stat().st_mode), 0o600)

    def test_hook_rows_task_spans_git_counts_and_private_fields(self):
        self.timeline.start(self.home)
        self.append(self.hook("TaskCreated", task="task-1", tsub="PRIVATE_SUBJECT"),
                    self.hook("TaskCreated", ts=self.now + 1, task="task-2"),
                    self.hook("PreToolUse", ts=self.now + 2, tool="Bash", kind="git:commit"),
                    self.hook(ts=self.now + 3, tool="Bash", kind="git:commit",
                              body="PRIVATE_BODY", cwd="/PRIVATE_PATH"),
                    self.hook("PostToolUseFailure", ts=self.now + 4, kind="git:push", ok=False),
                    self.hook(ts=self.now + 5, kind="git:push"),
                    self.hook("TaskCompleted", ts=self.now + 6, task="task-1"))
        self.timeline.stop()
        self.assertEqual(self.query("SELECT COUNT(*) FROM events WHERE seq IS NOT NULL"), [(7,)])
        self.assertEqual(self.query("SELECT COUNT(*) FROM events WHERE ev='hire'"), [(1,)])
        self.assertEqual(self.query("SELECT label,start,end FROM spans WHERE kind='task' ORDER BY label"),
                         [("task-1", self.now, self.now + 6), ("task-2", self.now + 1, None)])
        stats = json.loads(self.query("SELECT json FROM daily")[0][0])
        self.assertEqual(stats["git:commit"], 1)
        self.assertEqual(stats["git:push"], 1)
        self.assertEqual(stats["taskCompleted"], 1)
        with closing(sqlite3.connect(self.path)) as db:
            dumped = "\n".join(db.iterdump())
        for private in ("PRIVATE_SUBJECT", "PRIVATE_BODY", "PRIVATE_PATH", "tsub"):
            self.assertNotIn(private, dumped)

    def test_state_spans_codex_turn_diff_disappearance_and_reappearance(self):
        self.timeline.start(self.home)
        a = self.employee()
        cx = self.employee(session="cx-demo", vendor="codex")
        for offset, employees in [(0, [a, cx]), (1, [a, cx]),
                                  (2, [dict(a, state="waiting"), dict(cx, state="waiting")]),
                                  (3, []), (4, [a])]:
            self.timeline.record_states(employees, self.now + offset)
        self.timeline.stop()
        self.assertEqual(self.query("SELECT COUNT(*) FROM spans WHERE kind='state'"), [(5,)])
        self.assertEqual(self.query("SELECT COUNT(*) FROM spans WHERE kind='state' AND end IS NULL"), [(1,)])
        self.assertEqual(self.query("SELECT COUNT(*) FROM spans WHERE end <= start"), [(0,)])
        self.assertEqual(self.query("SELECT start,end FROM spans WHERE kind='turn'"), [(self.now, self.now + 2)])
        self.assertEqual(self.query("SELECT COUNT(*) FROM events WHERE ev='turnCompleted'"), [(1,)])
        self.assertEqual(self.query("SELECT COUNT(*) FROM events WHERE ev='hire'"), [(2,)])
        self.assertEqual(self.query("SELECT cwdh,projectId,name FROM sessions WHERE sid='session-a'"),
                         [(hashlib.sha1(b"/demo/project").hexdigest()[:12], "project-a", "Demo")])

    def test_equal_and_out_of_order_scans_do_not_create_invalid_spans(self):
        self.timeline.start(self.home)
        for offset, state in [(0, "working"), (0, "waiting"), (2, "working"), (1, "resting")]:
            self.timeline.record_states([self.employee(state=state)], self.now + offset)
        self.timeline.stop()
        self.assertEqual(self.query("SELECT label FROM spans WHERE kind='state' AND end IS NULL"), [("working",)])
        self.assertEqual(self.query("SELECT label FROM spans WHERE kind='state' ORDER BY start"),
                         [("working",), ("waiting",), ("working",)])
        self.assertEqual(self.query("SELECT COUNT(*) FROM spans WHERE end <= start"), [(0,)])
        spans = self.query("SELECT start,end FROM spans WHERE kind='state' ORDER BY start")
        self.assertTrue(all(a[1] <= b[0] for a, b in zip(spans, spans[1:])))

    def test_restart_resumes_seq_without_duplicate_hooks_or_states(self):
        first = self.hook("TaskCreated", task="task-1")
        self.timeline.start(self.home)
        self.append(first)
        self.timeline.record_states([self.employee()], self.now)
        self.timeline.stop()
        self.assertEqual(self.query("SELECT v FROM meta WHERE k='last_seq'"), [("1",)])
        self.append(self.hook("TaskCompleted", ts=self.now + 1, task="task-1"))
        self.timeline.start(self.home)
        self.timeline.record_states([self.employee()], self.now + 2)
        self.timeline.stop()
        self.assertEqual(self.query("SELECT seq FROM events WHERE seq IS NOT NULL ORDER BY seq"), [(1,), (2,)])
        self.assertEqual(self.query("SELECT COUNT(*) FROM spans WHERE kind='state'"), [(1,)])
        self.assertEqual(self.query("SELECT end FROM spans WHERE kind='task'"), [(self.now + 1,)])

    def test_full_process_restart_replays_with_reset_seq_and_changed_day_window(self):
        yesterday = date.today() - timedelta(days=1)
        self.timeline.start(self.home)
        self.append(self.hook(ts=self.now - 86400), day=yesterday)
        self.append(self.hook("TaskCreated", ts=self.now + 1, task="task-1"))
        self.timeline.stop()
        # A new D6 process starts at seq=0 and no longer reads the old window.
        self.events = load_module("events_timeline_restart", "office_events.py")
        with patch.object(self.events, "_today", return_value=date.today() + timedelta(days=1)):
            self.events._poll(self.home)
        self.assertEqual(self.events.seq(), 1)
        self.timeline = self.load_timeline()
        self.timeline.start(self.home)
        self.append(self.hook("TaskCompleted", ts=self.now + 2, task="task-1"))
        self.timeline.stop()
        self.assertEqual(self.query("SELECT COUNT(*),COUNT(DISTINCT seq) FROM events WHERE seq IS NOT NULL"), [(3, 3)])
        self.assertEqual(self.query("SELECT seq FROM events WHERE ev='TaskCompleted'"), [(3,)])
        self.assertEqual(self.query("SELECT end FROM spans WHERE kind='task'"), [(self.now + 2,)])

    def test_file_cursor_recovers_1200_hooks_before_and_after_start(self):
        # The first 500 queue entries and last 500 ring entries miss the middle.
        subscriber = self.events.subscribe()
        self.addCleanup(lambda: self.events.unsubscribe(subscriber))
        self.append(*(self.hook(ts=self.now + n / 1000) for n in range(1200)))
        self.assertEqual(subscriber.qsize(), self.events.RING)
        self.assertEqual(len(self.events.recent(limit=2000)), self.events.RING)
        with patch.object(self.events, "recent", side_effect=AssertionError("ring is not durable")), \
                patch.object(self.events, "subscribe", side_effect=AssertionError("no internal SSE slot")):
            self.timeline.start(self.home)
            self.timeline.stop()
        self.assertEqual(self.query("SELECT seq FROM events WHERE seq IS NOT NULL ORDER BY seq"),
                         [(n,) for n in range(1, 1201)])
        self.assertEqual(self.query("SELECT v FROM meta WHERE k='last_seq'"), [("1200",)])
        cursor = json.loads(self.query("SELECT v FROM meta WHERE k=?", ("cursor:" + date.today().isoformat(),))[0][0])
        path = self.directory / f"{date.today().isoformat()}.jsonl"
        self.assertEqual(cursor["offset"], path.stat().st_size)
        self.assertEqual(cursor["line"], 1200)
        self.timeline.start(self.home)
        self.append(*(self.hook(ts=self.now + n / 1000) for n in range(1200, 2400)))
        self.timeline.stop()
        self.assertEqual(self.query("SELECT seq FROM events WHERE seq IS NOT NULL ORDER BY seq"),
                         [(n,) for n in range(1, 2401)])

    def test_identical_hook_lines_are_distinct_but_replay_is_not(self):
        self.append(*([self.hook()] * 1200))
        self.timeline.start(self.home)
        self.timeline.stop()
        self.timeline = self.load_timeline()
        self.timeline.start(self.home)
        self.timeline.stop()
        self.assertEqual(self.query("SELECT COUNT(*),MIN(seq),MAX(seq) FROM events WHERE seq IS NOT NULL"),
                         [(1200, 1, 1200)])

    def test_cursor_preserves_partial_lines_and_resumes_after_replacement(self):
        path = self.directory / f"{date.today().isoformat()}.jsonl"
        first = self.hook(ts=self.now)
        second = self.hook(ts=self.now + 1)
        path.write_text(json.dumps(first) + "\n" + json.dumps(second), encoding="utf-8")
        self.timeline.start(self.home)
        self.timeline.stop()
        self.assertEqual(self.query("SELECT seq FROM events WHERE seq IS NOT NULL"), [(1,)])
        with path.open("a", encoding="utf-8") as stream:
            stream.write("\n")
        self.timeline.start(self.home)
        self.timeline.stop()
        replacement = path.with_suffix(".replacement")
        replacement.write_text(json.dumps(first) + "\n" + json.dumps(second) + "\n"
                               + json.dumps(self.hook(ts=self.now + 2)) + "\n", encoding="utf-8")
        replacement.replace(path)
        self.timeline.start(self.home)
        self.timeline.stop()
        self.assertEqual(self.query("SELECT seq FROM events WHERE seq IS NOT NULL ORDER BY seq"), [(1,), (2,), (3,)])

    def test_timeline_leaves_all_eight_sse_subscriptions_available(self):
        self.timeline.start(self.home)
        subscribers = [self.events.subscribe() for _ in range(8)]
        for subscriber in subscribers:
            self.addCleanup(lambda q=subscriber: self.events.unsubscribe(q))
        self.assertTrue(all(q is not None for q in subscribers))
        self.assertIsNone(self.events.subscribe())
        self.append(self.hook())
        self.timeline.stop()
        self.assertEqual(self.query("SELECT COUNT(*) FROM events WHERE seq IS NOT NULL"), [(1,)])

    def test_ask_open_close_resolution_waited_seconds_and_missing_roster(self):
        self.timeline.start(self.home)
        self.append(self.hook("PermissionRequest"))
        self.timeline.record_states([self.employee(question="PRIVATE_QUESTION")], self.now + 1)
        self.timeline.record_states([], self.now + 10)
        self.timeline.record_ask_resolved("project-a", "", 9, self.now + 10)
        self.timeline.record_ask_resolved("project-a", "", 9, self.now + 10)
        self.timeline.stop()
        self.assertEqual(self.query("SELECT start,end FROM spans WHERE kind='ask'"), [(self.now, self.now + 10)])
        stats = json.loads(self.query("SELECT json FROM daily")[0][0])
        self.assertEqual(stats["askResolved"], 1)
        self.assertEqual(stats["waitedSec"], 9)

    def test_prune_boundaries_startup_and_day_rollover(self):
        self.timeline.start(self.home)
        self.timeline.stop()
        today = date.today()
        with closing(sqlite3.connect(self.path)) as db, db:
            for table, days in (("events", 90), ("spans", 180), ("daily", 400)):
                for age in (days, days + 1):
                    day = (today - timedelta(days=age)).isoformat()
                    if table == "daily":
                        db.execute("INSERT INTO daily VALUES (?,'{}',0)", (day,))
                    else:
                        db.execute(f"INSERT INTO {table}(day) VALUES (?)", (day,))
        self.timeline.start(self.home)
        self.wait_for(lambda: self.query("SELECT COUNT(*) FROM events") == [(1,)])
        self.assertEqual(self.query("SELECT COUNT(*) FROM spans"), [(1,)])
        self.assertEqual(self.query("SELECT COUNT(*) FROM daily"), [(1,)])
        with patch.object(self.timeline, "date") as clock:
            clock.today.return_value = today + timedelta(days=1)
            self.wait_for(lambda: self.query("SELECT COUNT(*) FROM daily") == [(0,)])
            self.assertEqual(self.query("SELECT COUNT(*) FROM events"), [(0,)])
            self.assertEqual(self.query("SELECT COUNT(*) FROM spans"), [(0,)])
            self.timeline.stop()

    def test_zero_byte_and_corrupt_database_recreated(self):
        for contents in (b"", b"not a SQLite database"):
            with self.subTest(contents=contents):
                self.path.write_bytes(contents)
                self.path.chmod(0o644)
                self.timeline.start(self.home)
                self.timeline.record_states([self.employee()], self.now)
                self.timeline.stop()
                self.assertEqual(self.query("PRAGMA integrity_check"), [("ok",)])
                self.assertEqual(self.query("SELECT COUNT(*) FROM sessions"), [(1,)])
                self.assertEqual(stat.S_IMODE(self.path.stat().st_mode), 0o600)

    def test_disabled_or_missing_events_directory_does_not_create_database(self):
        with patch.dict(os.environ, {"OFFICE_TIMELINE": "0"}):
            self.timeline.start(self.home)
            self.timeline.record_states([self.employee()], self.now)
            self.timeline.record_ask_resolved("project-a", "session-a", 1, self.now)
        self.assertIsNone(self.timeline._WRITER)
        self.assertFalse(self.path.exists())
        missing = self.home / "missing"
        self.timeline.start(missing)
        self.assertFalse(missing.exists())
        self.assertTrue(self.timeline._WRITER.thread.is_alive())
        self.timeline.stop()
        self.assertFalse(missing.exists())

    def test_writer_waits_without_writing_then_starts_when_events_appear(self):
        missing = self.home / "late"
        self.timeline.start(missing)
        writer = self.timeline._WRITER
        self.timeline.record_states([self.employee()], self.now)
        time.sleep(0.06)
        self.assertFalse(missing.exists())
        self.assertIsNone(writer.last_error)
        directory = missing / self.events.EVENTS_DIR
        directory.mkdir(parents=True)
        (directory / f"{date.today().isoformat()}.jsonl").write_text(json.dumps(self.hook()) + "\n", encoding="utf-8")
        self.path = missing / self.timeline.DB_FILE
        self.wait_for(lambda: self.query("SELECT COUNT(*) FROM events WHERE seq IS NOT NULL") == [(1,)])
        self.assertIs(writer, self.timeline._WRITER)
        self.assertEqual(self.query("SELECT label FROM spans WHERE kind='state'"), [("working",)])

    def test_connection_failure_bounds_queue_and_keeps_latest_state(self):
        with patch.object(self.timeline, "_connect", side_effect=OSError("offline")):
            self.timeline.start(self.home)
            self.wait_for(lambda: self.timeline._WRITER.last_error == "OSError")
            writer = self.timeline._WRITER
            for n in range(1000):
                self.timeline.record_states([self.employee(state="working")], self.now + n)
            self.assertLessEqual(writer.commands.qsize(), self.timeline.MAX_COMMANDS)
            for n in range(1000, self.timeline.MAX_COMMANDS * 3):
                self.timeline.record_states([self.employee(state="waiting")], self.now + n)
            self.assertLessEqual(writer.commands.qsize(), self.timeline.MAX_COMMANDS)
            self.assertFalse(self.path.exists())
        self.wait_for(lambda: self.query("SELECT lastSeen FROM sessions") == [(self.now + self.timeline.MAX_COMMANDS * 3 - 1,)])
        self.assertEqual(self.query("SELECT label FROM spans WHERE kind='state' AND end IS NULL"), [("waiting",)])

    def test_overflow_recovery_does_not_count_missing_wait_as_active_xp(self):
        writer = self.timeline._Timeline(self.home)
        base = self.now - 7000
        row = {"sid": "session-a", "vendor": "codex", "cwdh": "project-hash",
               "projectId": "session-avatar-id", "name": "Demo", "state": "working", "attention": False}
        with closing(self.timeline._connect(self.path)) as db:
            writer.batch(db, [("states", ([row], base))])
            writer.commands.put_nowait(("states", ([dict(row, state="waiting")], base + 1)))
            failed = writer.commands.drain()
            with patch.object(writer, "file_hooks", side_effect=sqlite3.OperationalError("offline")):
                with self.assertRaises(sqlite3.OperationalError):
                    writer.batch(db, failed)
            writer.commands.restore(failed)
            # Overflow more than once, including a working -> waiting -> working
            # interval that the former latest-only compaction silently extended.
            for n in range(2, 6002):
                writer.commands.put_nowait(("states", ([dict(row, state="waiting")], base + n)))
            writer.commands.put_nowait(("states", ([row], base + 6002)))
            self.assertLessEqual(writer.commands.qsize(), self.timeline.MAX_COMMANDS)
            writer.batch(db, writer.commands.drain())
        facts = self.timeline._snapshot(self.home, self.now)[1]
        active = sum(f.get("activeMin", 0) for f in facts)
        self.assertAlmostEqual(active, 1 / 60)
        self.assertEqual(sum(f["ev"] == "turnCompleted" for f in facts), 1)
        growth = self.timeline.growth_from_events(facts)
        self.assertAlmostEqual(growth["office"]["breakdown"]["activeMin"]["xp"], 1 / 300)

    def test_overflow_keeps_answer_session_metadata_with_or_without_hooks(self):
        for hooked in (False, True):
            with self.subTest(hooked=hooked), tempfile.TemporaryDirectory() as tmp:
                home = Path(tmp)
                writer = self.timeline._Timeline(home)
                writer.directory.mkdir(parents=True)
                base = self.now - 4000
                other = {"sid": "other", "vendor": "claude", "projectId": "other-project",
                         "state": "working", "attention": False}
                row = dict(other, sid="departed", projectId="session-avatar-id",
                           cwdh="project-hash", name="Demo", state="waiting", attention=True)
                writer.commands.put_nowait(("states", ([other], base)))
                writer.commands.put_nowait(("states", ([other, row], base + 1)))
                writer.commands.put_nowait(("resolved", ("session-avatar-id", "departed", 60, base + 61)))
                for n in range(62, 3062):
                    writer.commands.put_nowait(("states", ([other], base + n)))
                self.assertLessEqual(writer.commands.qsize(), self.timeline.MAX_COMMANDS)
                with closing(self.timeline._connect(writer.path)) as db:
                    if hooked:
                        writer.hook(db, self.hook("SessionEnd", ts=base + 62,
                                                 sid="departed", cwdh="project-hash"), "1")
                        db.commit()
                    writer.batch(db, writer.commands.drain())
                    self.assertEqual(db.execute("SELECT projectId FROM sessions WHERE sid='departed'").fetchone(),
                                     ("session-avatar-id",))
                    self.assertEqual(db.execute("SELECT COUNT(*) FROM spans WHERE sid='departed' AND end IS NULL").fetchone(), (0,))
                facts = self.timeline._snapshot(home, self.now)[1]
                answers = [f for f in facts if f["ev"] == "askResolved"]
                self.assertEqual([(f["sid"], f["projectId"], f["waitedSec"]) for f in answers],
                                 [("departed", "session-avatar-id", 60)])

    def test_locked_database_bounds_retries_and_recovers_all_file_events(self):
        self.timeline.start(self.home)
        self.timeline.stop()
        with closing(sqlite3.connect(self.path)) as blocker:
            blocker.execute("BEGIN IMMEDIATE")
            self.timeline.start(self.home)
            self.wait_for(lambda: self.timeline._WRITER.last_error == "OperationalError")
            for n in range(self.timeline.MAX_COMMANDS * 2):
                self.timeline.record_states([self.employee()], self.now + n)
            self.append(*(self.hook(ts=self.now + n / 1000) for n in range(1200)))
            self.assertLessEqual(self.timeline._WRITER.commands.qsize(), self.timeline.MAX_COMMANDS)
            blocker.rollback()
        self.wait_for(lambda: self.query("SELECT COUNT(*) FROM events WHERE seq IS NOT NULL") == [(1200,)])
        self.assertEqual(self.query("SELECT MIN(seq),MAX(seq),COUNT(DISTINCT seq) FROM events WHERE seq IS NOT NULL"), [(1, 1200, 1200)])

    def test_writer_exception_rolls_back_and_retries_without_losing_batch(self):
        entered = threading.Event()
        original = self.timeline._Timeline.file_hooks
        attempts = [True]

        def fail_after_writes(writer, db):
            original(writer, db)
            if attempts:
                attempts.pop()
                entered.set()
                raise sqlite3.OperationalError("simulated writer failure")

        with patch.object(self.timeline._Timeline, "file_hooks", fail_after_writes):
            self.timeline.start(self.home)
            self.append(self.hook())
            self.assertTrue(entered.wait(2))
            self.wait_for(lambda: self.query("SELECT COUNT(*) FROM events WHERE seq IS NOT NULL") == [(1,)])
        self.timeline.stop()
        self.assertEqual(self.query("SELECT COUNT(*) FROM events WHERE ev='hire'"), [(1,)])
        self.assertEqual(self.query("SELECT v FROM meta WHERE k='last_seq'"), [("1",)])

    def test_writer_failure_does_not_break_office_json_and_wiring_is_local(self):
        office = load_module("office_timeline_server", "office_server.py")
        with patch.dict(sys.modules, {"office_timeline": self.timeline}), \
                patch.object(office, "office_events", self.events), \
                patch.object(self.timeline, "_connect", side_effect=OSError("unavailable")):
            self.timeline.start(self.home)
            self.wait_for(lambda: self.timeline._WRITER.last_error == "OSError")
            data = office.office_json()
            self.assertIsInstance(data["employees"], list)
            self.assertNotIn("timeline", data)
            self.assertFalse(self.path.exists())
            self.timeline.stop()
        with patch.dict(sys.modules, {"office_timeline": self.timeline}), \
                patch.object(self.timeline, "record_states", side_effect=RuntimeError("queue failed")):
            self.assertIsInstance(office.scan_office(), dict)

    def test_server_calls_record_states_and_ask_resolved(self):
        office = load_module("office_timeline_wiring", "office_server.py")
        recorder = Mock()
        with patch.dict(sys.modules, {"office_timeline": recorder}), \
                patch.object(office.source_codex, "codex_employees", return_value=([], {})):
            office.scan_office()
            recorder.record_states.assert_called_once()
            seen, resolved = office._attn_track({"project-a": self.now},
                [{"projectId": "project-a", "session": "session-a"}], self.now + 8)
            self.assertEqual((seen, resolved), ({}, [8]))
            recorder.record_ask_resolved.assert_called_once_with("project-a", "", 8, self.now + 8)

    def test_project_ask_resolution_belongs_to_original_session_after_lead_changes(self):
        office = load_module("office_timeline_ask_owner", "office_server.py")
        a = self.employee(question="private", age=0)
        b = self.employee(session="session-b", state="waiting", age=1)
        self.timeline.start(self.home)
        with patch.dict(sys.modules, {"office_timeline": self.timeline}):
            roster = office.group_by_project([a, b], mode="project")
            pid = roster[0]["projectId"]
            self.assertEqual(roster[0]["session"], a["session"])
            self.timeline.record_states([dict(a, projectId=pid), dict(b, projectId=pid)], self.now)
            seen, resolved = office._attn_track({}, roster, self.now)
            self.assertEqual(resolved, [])
            # Keep the first owner even if another session asks in the same project.
            waiting = office.group_by_project([dict(a, question="", age=3), dict(b, question="private")], mode="project")
            seen, _ = office._attn_track(seen, waiting, self.now + 5)
            self.assertEqual(seen[pid]["session"], a["session"])
            a = dict(a, question="", age=3)
            roster = office.group_by_project([a, b], mode="project")
            self.assertEqual(roster[0]["session"], b["session"])
            self.timeline.record_states([dict(a, projectId=pid), dict(b, projectId=pid)], self.now + 10)
            seen, resolved = office._attn_track(seen, roster, self.now + 10)
        self.timeline.stop()
        self.assertEqual((seen, resolved), ({}, [10]))
        self.assertEqual(self.query("SELECT sid,start,end FROM spans WHERE kind='ask'"),
                         [(a["session"], self.now, self.now + 10)])
        self.assertEqual(self.query("SELECT s.sid,a.waitedSec FROM answers a JOIN spans s ON s.id=a.spanId"),
                         [(a["session"], 10)])
        digest = self.timeline.digest_json(self.home, since=0, now=self.now + 11)
        self.assertEqual(digest["totals"]["asksAnswered"], 1)

    def test_dump_and_disabled_main_skip_timeline_start(self):
        office = load_module("office_timeline_main", "office_server.py")
        with patch.object(sys, "argv", ["office_server.py", "--dump"]), \
                patch.object(office, "scan_office", return_value={}), \
                patch("builtins.print"), patch.object(office.importlib, "import_module") as importer:
            office.main()
            importer.assert_not_called()
        with patch.object(sys, "argv", ["office_server.py"]), \
                patch.dict(os.environ, {"OFFICE_TIMELINE": "0", "OFFICE_EVENTS": "0"}), \
                patch.object(office, "_install_ts_logging"), patch.object(office, "_OfficeHTTPServer"), \
                patch.object(office, "start_watcher"), patch("builtins.print"), \
                patch.object(office.importlib, "import_module") as importer:
            office.main()
            importer.assert_not_called()

    def golden(self):
        base = datetime.combine(date.today(), datetime.min.time()).timestamp() + 3600
        self.timeline.start(self.home)
        self.append(self.hook("SessionStart", ts=base),
                    *(self.hook("TaskCompleted", ts=base + n, task=f"task-{n}") for n in range(1, 10)),
                    self.hook(ts=base + 400, tool="Bash", kind="git:commit"),
                    self.hook("PreToolUse", ts=base + 401, tool="Bash", kind="git:commit"),
                    self.hook("PostToolUseFailure", ts=base + 402, tool="Bash", kind="git:commit", ok=False),
                    self.hook("PermissionRequest", ts=base + 600))
        a = self.employee()
        cx = self.employee(session="cx-demo", vendor="codex")
        self.timeline.record_states([a, cx], base)
        self.timeline.record_states([a, dict(cx, state="waiting")], base + 300)
        self.timeline.record_states([dict(a, state="waiting", question="private")], base + 600)
        self.timeline.record_states([], base + 900)
        self.timeline.record_ask_resolved("project-a", "session-a", 300, base + 900)
        self.timeline.record_ask_resolved("project-a", "session-a", 300, base + 900)
        self.timeline.stop()
        return base

    def test_digest_golden_observed_tasks_commits_minutes_xp_level(self):
        base = self.golden()
        before = self.path.read_bytes()
        digest = self.timeline.digest_json(self.home, since=0, now=base + 901)
        self.assertTrue(digest["available"])
        totals = digest["totals"]
        self.assertEqual({key: totals[key] for key in (
            "activeMin", "waitingMin", "tasksDone", "commits", "asksAnswered", "hires", "xp", "level")},
            {"activeMin": 15, "waitingMin": 10, "tasksDone": 9, "commits": 1,
             "asksAnswered": 1, "hires": 2, "xp": 114, "level": 1})
        self.assertEqual(totals["tools"], {"Bash": 1})
        self.assertEqual((totals["avgWaitSec"], totals["medianWaitSec"]), (300, 300))
        self.assertEqual([s["activeMin"] for s in digest["sessions"]], [5, 10])
        growth = self.timeline.growth_json(self.home, now=base + 901)
        self.assertEqual(growth["office"]["xp"], 114)
        self.assertEqual(growth["office"]["nextAt"], 400)
        self.assertEqual(growth["byProject"]["project-a"]["level"], 1)
        self.assertEqual(sum(v["xp"] for v in growth["office"]["breakdown"].values()), 114)
        self.assertEqual(before, self.path.read_bytes())
        self.assertNotIn("private", json.dumps(digest))

    def test_growth_zero_boundaries_tokens_fast_answers_and_purity(self):
        growth = self.timeline.growth_from_events
        zero = growth([])["office"]
        self.assertEqual((zero["xp"], zero["level"], zero["nextAt"]), (0, 0, 100))
        self.assertEqual(set(zero["breakdown"]), {"tasksDone", "commits", "asksAnswered", "fastAnswers",
                                                "activeMin", "hires", "turnsCompleted"})
        self.assertTrue(all(part == {"count": 0, "xp": 0} for part in zero["breakdown"].values()))
        self.assertEqual(growth([])["streak15"], 0)
        for xp, level in ((99, 0), (100, 1), (400, 2)):
            rows = [{"ev": "active", "activeMin": xp * 5, "projectId": "p1"}]
            self.assertEqual((growth(rows)["office"]["xp"], growth(rows)["office"]["level"]), (xp, level))
        rows = [{"sid": "s1", "ev": "askResolved", "waitedSec": waited, "projectId": "p1"}
                for waited in (0, 300, 301)]
        original = json.dumps(rows)
        with patch.object(self.timeline.time, "time", side_effect=AssertionError("clock used")), \
                patch.object(self.timeline, "_reader", side_effect=AssertionError("I/O used")):
            result = growth(rows)
            self.assertEqual(result["office"]["xp"], 8)
            self.assertEqual(result, growth([dict(row, tokens=10**12, tokens_used=10**15) for row in rows]))
        self.assertEqual(original, json.dumps(rows))
        self.assertEqual(result["office"]["breakdown"]["fastAnswers"]["count"], 2)

    def test_growth_project_sum_and_no_path_output(self):
        rows = [{"ev": "TaskCompleted", "sid": "a", "task": "one", "projectId": "p1"},
                {"ev": "hire", "sid": "b", "projectId": "p2"},
                {"ev": "hire", "sid": "c", "projectId": "/private/path"}]
        result = self.timeline.growth_from_events(rows + [rows[0]])
        self.assertEqual(result["office"]["xp"], 20)
        self.assertEqual(sum(p["xp"] for p in result["byProject"].values()), 20)
        self.assertNotIn("/private/path", json.dumps(result))

    def test_streak_median_boundary_empty_days_and_today(self):
        rows = [{"ev": "askResolved", "waitedSec": waited, "day": day}
                for day, waited in (("2026-09-01", 901), ("2026-09-03", 100),
                                    ("2026-09-03", 200), ("2026-09-03", 2000))]
        self.assertEqual(self.timeline.growth_from_events(rows, "2026-09-05")["streak15"], 4)
        rows.append({"ev": "askResolved", "waitedSec": 900, "day": "2026-09-05"})
        self.assertEqual(self.timeline.growth_from_events(rows, "2026-09-05")["streak15"], 0)

    def test_unanswered_closed_spans_do_not_receive_answer_xp(self):
        self.timeline.start(self.home)
        self.timeline.record_states([self.employee(question="private")], self.now)
        self.timeline.record_states([], self.now + 300)
        self.timeline.stop()
        digest = self.timeline.digest_json(self.home, since=0, now=self.now + 301)
        self.assertEqual(digest["totals"]["asksAnswered"], 0)

    def test_digest_since_seen_default_clips_spans_and_cache_expires(self):
        base = self.golden()
        self.timeline.mark_seen(self.home, base + 450)
        digest = self.timeline.digest_json(self.home, now=base + 901)
        self.assertEqual(digest["since"], base + 450)
        self.assertEqual((digest["totals"]["activeMin"], digest["totals"]["xp"]), (2.5, 3.5))
        full = self.timeline.digest_json(self.home, since=0, now=base + 901)
        full["sessions"].clear()
        with closing(sqlite3.connect(self.path)) as db, db:
            self.timeline._event(db, {"ts": base + 902, "ev": "TaskCompleted", "sid": "session-a", "task": "new"})
        cached = self.timeline.digest_json(self.home, since=0, now=base + 959)
        fresh = self.timeline.digest_json(self.home, since=0, now=base + 961)
        self.assertEqual(len(cached["sessions"]), 2)
        self.assertEqual((cached["totals"]["tasksDone"], fresh["totals"]["tasksDone"]), (9, 10))

    def test_day_boundary_and_open_span_only_until_last_observation(self):
        midnight = datetime.combine(date.today(), datetime.min.time()).timestamp()
        self.timeline.start(self.home)
        self.timeline.record_states([self.employee()], midnight - 300)
        self.timeline.record_states([self.employee()], midnight + 300)
        self.timeline.stop()
        yesterday = (date.today() - timedelta(days=1)).isoformat()
        self.assertEqual(self.timeline.digest_json(self.home, day=yesterday, since=0,
                         now=midnight + 3600)["totals"]["activeMin"], 5)
        self.assertEqual(self.timeline.digest_json(self.home, since=0,
                         now=midnight + 3600)["totals"]["activeMin"], 5)

    def test_timeline_since_sid_limit_and_missing_disabled_database(self):
        empty = self.timeline.digest_json(self.home, since=0, now=self.now)
        self.assertFalse(empty["available"])
        self.assertEqual(empty["totals"]["xp"], 0)
        self.assertFalse(self.path.exists())
        base = self.golden()
        result = self.timeline.timeline_json(self.home, since=base + 399, sid="session-a", limit=2)
        self.assertEqual([row["ts"] for row in result["events"]], [base + 402, base + 600])
        with patch.dict(os.environ, {"OFFICE_TIMELINE": "0"}):
            self.assertFalse(self.timeline.digest_json(self.home, since=0)["available"])
            self.assertEqual(self.timeline.timeline_json(self.home)["events"], [])

    def request(self, office, method, path, headers=None, peer="127.0.0.1"):
        handler = office.Handler.__new__(office.Handler)
        handler.path = path
        handler.headers = {"Host": "127.0.0.1", **(headers or {})}
        handler.client_address = (peer, 12345)
        handler.rfile = io.BytesIO(b"")
        handler._send = Mock()
        getattr(handler, "do_" + method)()
        code, body, _ = handler._send.call_args.args
        return code, json.loads(body)

    def test_api_auth_validation_seen_rmw_and_server_time(self):
        office = load_module("office_timeline_api", "office_server.py")
        local = {"X-Office-Local": "1"}
        for method, path in (("GET", "/api/digest"), ("GET", "/api/timeline"), ("POST", "/api/seen")):
            self.assertEqual(self.request(office, method, path)[0], 403)
            self.assertEqual(self.request(office, method, path, {**local, "Origin": "https://example.invalid"})[0], 403)
            self.assertEqual(self.request(office, method, path, {**local, "Host": "example.invalid"})[0], 403)
            self.assertEqual(self.request(office, method, path, local, peer="192.0.2.1")[0], 403)
            self.assertEqual(self.request(office, method, path, local)[0], 200)
        for path in ("/api/timeline?limit=0", "/api/timeline?limit=501", "/api/timeline?sid=../a",
                     "/api/digest?day=2026-02-30", "/api/digest?since=nan", "/api/digest?since=-1",
                     "/api/digest?since=1&since=2", "/api/timeline?since=", "/api/timeline?since=inf"):
            self.assertEqual(self.request(office, "GET", path, local)[0], 400, path)
        path = self.home / self.timeline.SEEN_FILE
        path.write_text(json.dumps({"seenAt": 1, "other": {"preserved": True}}))
        with patch.object(office.time, "time", return_value=self.now):
            code, response = self.request(office, "POST", "/api/seen", local)
        self.assertEqual((code, response["seenAt"]), (200, self.now))
        self.timeline.mark_seen(self.home, self.now - 10)
        self.assertEqual(json.loads(path.read_text()), {"seenAt": self.now, "other": {"preserved": True}})
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)

    def test_seen_parallel_rmw_keeps_latest_timestamp(self):
        threads = [threading.Thread(target=self.timeline.mark_seen, args=(self.home, self.now + i))
                   for i in range(10)]
        for thread in reversed(threads):
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(self.timeline._read_seen(self.home)["seenAt"], self.now + 9)

    def test_daily_report_prefers_digest_median_and_office_exposes_growth(self):
        base = self.golden()
        office = load_module("office_timeline_report", "office_server.py")
        digest = self.timeline.digest_json(self.home, since=0, now=base + 901)
        with patch.object(office.office_timeline, "digest_json", return_value=digest), \
                patch.object(office, "_HOME", self.home):
            _title, body, md = office.build_daily_report({"tasks": {}, "roster": []}, date.today().isoformat())
        self.assertIn("❗1件に答えた", body)   # D13 通知本文 v2
        self.assertIn("待たせ中央値 5分", md)
        growth = self.timeline.growth_json(self.home, now=base + 901)
        with patch.object(office.office_timeline, "growth_json", return_value=growth):
            self.assertEqual(office.office_json()["growth"], growth)


if __name__ == "__main__":
    unittest.main()
