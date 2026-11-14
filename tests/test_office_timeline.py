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
from statistics import median
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

    def test_evidence_for_last_24h_committed_tested_failed_and_cache(self):
        """R98: 台帳の証拠列。セッションごとの**最後の**証拠（committed/tested/failed）だけ・24h 窓・
        PreToolUse は数えない・記録の無い sid はキーごと無し・5 秒キャッシュ・壊れていても空 dict。"""
        t = self.timeline
        self.append(self.hook(ts=self.now - 3 * 86400, sid="old", tool="Bash", kind="git:commit"),
                    self.hook("PreToolUse", ts=self.now - 100, sid="pre", tool="Bash", kind="git:commit"),
                    self.hook(ts=self.now - 90, sid="a", tool="Bash", kind="test"),
                    self.hook(ts=self.now - 60, sid="a", tool="Bash", kind="git:commit"),
                    self.hook(ts=self.now - 50, sid="b", tool="Bash", kind="git:commit"),
                    self.hook("PostToolUseFailure", ts=self.now - 40, sid="b", tool="Bash", kind="test", ok=False),
                    self.hook(ts=self.now - 30, sid="c", tool="Bash", kind="test"),
                    self.hook(ts=self.now - 20, sid="d", tool="Bash", kind=""))
        t.start(self.home)
        # hook 行は seq を持つ（sid ごとの合成 hire 行は seq 無し＝数えない）
        self.wait_for(lambda: self.query("SELECT COUNT(*) FROM events WHERE seq IS NOT NULL")[0][0] == 8)
        out = t.evidence_for(self.home, ["a", "b", "c", "d", "old", "pre", "nobody"], self.now)
        self.assertEqual({k: v["kind"] for k, v in out.items()}, {"a": "committed", "b": "failed", "c": "tested"})
        self.assertEqual(out["a"]["ago"], 60)
        self.assertEqual(set(out["a"]), {"kind", "ago"})          # 本文・tool 名・パスは持たない
        # 5 秒キャッシュ: 新しい行が来ても TTL 内は前の答え・TTL を過ぎたら更新
        self.append(self.hook(ts=self.now - 5, sid="c", tool="Bash", kind="git:commit"))
        self.wait_for(lambda: self.query("SELECT COUNT(*) FROM events WHERE seq IS NOT NULL")[0][0] == 9)
        self.assertEqual(t.evidence_for(self.home, ["c"], self.now + 1)["c"]["kind"], "tested")
        self.assertEqual(t.evidence_for(self.home, ["c"], self.now + 6)["c"]["kind"], "committed")
        # 読めない DB でも落ちない
        self.assertEqual(t.evidence_for(self.home / "missing", ["a"], self.now + 20), {})

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
            exposed = office.office_json()["growth"]
        # R123-C: 全体のレベル・連続日数はそのまま、byProject はいま居る人の分だけ（ここでは誰も居ない）
        self.assertEqual({k: v for k, v in exposed.items() if k not in ("byProject", "maxSeen")},
                         {k: v for k, v in growth.items() if k not in ("byProject", "maxSeen")})
        self.assertEqual(exposed["byProject"], {})

    def test_office_json_growth_keeps_only_present_projects(self):
        """R123-C: byProject は timeline の全件ではなく、名簿に居るプロジェクトの分だけ（使うほど太らない）。
        growth_json のキャッシュの dict は書き換えない。"""
        office = load_module("office_timeline_present", "office_server.py")
        cached = {"byProject": {"p-here": {"xp": 10, "level": 0}, "p-gone": {"xp": 990, "level": 3},
                                "s-here": {"xp": 5, "level": 0}},
                  "office": {"xp": 1005, "level": 3, "nextAt": 1600}, "streak15": 2}
        roster = [{"projectId": "p-here", "session": "s-x", "sessions": []}]
        employees = [{"session": "s-here"}]
        out = office.present_growth(cached, roster, employees)
        self.assertEqual(sorted(out["byProject"]), ["p-here", "s-here"])
        self.assertEqual(out["office"], cached["office"])
        self.assertEqual(out["streak15"], 2)
        self.assertEqual(sorted(cached["byProject"]), ["p-gone", "p-here", "s-here"], "キャッシュの dict を書き換えた")
        self.assertEqual(office.present_growth({"office": {}}, [], [])["byProject"], {})

    # ---- R115: 読み手は必要な行だけを読む（全行を読んだ R115 前と同じ結果） ----

    def legacy_read(self, now):
        """R115 前の `_snapshot`（全行 → facts・キャッシュ無し）の写し。新しい読み手の正解。"""
        t = self.timeline
        with closing(t._reader(self.home)) as db:
            sessions = {row["sid"]: dict(row) for row in db.execute("SELECT * FROM sessions")}
            events = [dict(row) for row in db.execute("SELECT * FROM events ORDER BY ts,id")]
            spans = [dict(row) for row in db.execute("SELECT * FROM spans")]
            answers = {row["spanId"]: dict(row) for row in db.execute("SELECT * FROM answers")}
            resolved = {row["k"] for row in db.execute("SELECT k FROM meta WHERE k LIKE 'resolved:%'")}
        facts = [dict(e, projectId=t._project(sessions.get(e["sid"], {}).get("projectId"))) for e in events]
        answered = {(s["sid"], s["start"]) for s in spans if s["id"] in answers}
        last = {(s["sid"], s["start"]): s["id"] for s in spans if s["kind"] == "ask"}
        for s in spans:
            if s["sid"] not in sessions or s["start"] is None:
                continue
            session = sessions[s["sid"]]
            base = {"sid": s["sid"], "vendor": session["vendor"], "projectId": t._project(session["projectId"])}
            if s["kind"] == "state":
                start = s["start"]
                end = min(now, s["end"] if s["end"] is not None else session["lastSeen"])
                while start < end:
                    day = datetime.fromtimestamp(start).date()
                    stop = min(end, datetime.combine(day + timedelta(days=1), datetime.min.time()).timestamp())
                    working = s["label"] == "working"
                    facts.append(dict(base, ev="active" if working else "state", state=s["label"], start=start,
                                      ts=stop, day=day.isoformat(), activeMin=(stop - start) / 60 if working else 0))
                    start = stop
            elif s["kind"] == "ask":
                answer = answers.get(s["id"])
                key = "resolved:" + hashlib.sha256(json.dumps([s["sid"], s["start"]]).encode()).hexdigest()
                if (answer is None and s["end"] is not None and (s["sid"], s["start"]) not in answered
                        and last.get((s["sid"], s["start"])) == s["id"] and key in resolved):
                    answer = {"ts": s["end"], "waitedSec": s["end"] - s["start"]}
                if answer is not None:
                    facts.append(dict(base, ev="askResolved", ts=answer["ts"], day=t._day(answer["ts"]),
                                      waitedSec=answer["waitedSec"]))
        return sessions, facts

    def legacy_growth(self, now):
        facts = [row for row in self.legacy_read(now)[1] if row["ts"] <= now]
        return self.timeline.growth_from_events(facts, self.timeline._day(now))

    def legacy_digest(self, day, since, now):
        t = self.timeline
        parsed = date.fromisoformat(day)
        start = max(since, datetime.combine(parsed, datetime.min.time()).timestamp())
        end = min(now, datetime.combine(parsed + timedelta(days=1), datetime.min.time()).timestamp())
        metadata, all_facts = self.legacy_read(now)
        facts = []
        for row in all_facts:
            if row["ev"] in ("active", "state"):
                left, right = max(start, row["start"]), min(end, row["ts"])
                if left < right:
                    facts.append(dict(row, start=left, ts=right,
                                      activeMin=(right - left) / 60 if row["ev"] == "active" else 0))
            elif start <= row["ts"] < end:
                facts.append(row)
        growth = t.growth_from_events(facts, day)
        growth["streak15"] = t._streak([row for row in all_facts if row["ts"] <= end], parsed)

        def metrics(rows):
            counts, tools, waits = t._measure(rows)
            return {**{key: round(value, 9) for key, value in counts.items()}, "tools": tools,
                    "waitingMin": round(sum((r["ts"] - r["start"]) / 60 for r in rows
                                            if r.get("state") == "waiting"), 9),
                    "avgWaitSec": sum(waits) / len(waits) if waits else 0,
                    "medianWaitSec": median(waits) if waits else 0}

        sessions = [{"sid": sid, "vendor": metadata.get(sid, {}).get("vendor", ""),
                     "name": metadata.get(sid, {}).get("name", ""),
                     "projectId": t._project(metadata.get(sid, {}).get("projectId")),
                     **metrics([r for r in facts if r["sid"] == sid])}
                    for sid in sorted({r["sid"] for r in facts})]
        totals = metrics(facts)
        totals.update(xp=growth["office"]["xp"], level=growth["office"]["level"])
        return {"day": day, "since": since, "sessions": sessions, "totals": totals, "hires": totals["hires"],
                "streak15": growth["streak15"], "growth": growth,
                "available": any(r.get("day") == day for r in all_facts)}

    def edge_case_database(self):
        """同じ project の 2 セッション（作業が日を跨いで交互）・待つだけの project・Codex の turn・
        掃除で hire だけ消えた古いセッション・sessions に無い sid・不正な projectId・未来の event・
        answers の無い旧い解消キー・数えない PostToolUse を 1 つの DB に揃える。"""
        t = self.timeline
        midnight = datetime.combine(date.today(), datetime.min.time()).timestamp()
        d1, d2 = midnight - 86400, midnight - 2 * 86400
        writer = t._Timeline(self.home)

        def row(sid, state, project, vendor="claude", attention=False):
            return {"sid": sid, "vendor": vendor, "cwdh": "c" + sid, "projectId": project, "name": "N" + sid,
                    "state": state, "attention": attention}

        a = lambda state, **kw: row("a1", state, "shared", **kw)
        b = lambda state, **kw: row("a2", state, "shared", **kw)
        w = lambda state: row("w", state, "waitonly")
        cx = lambda state: row("cx", state, "cxp", "codex")
        bad = lambda state: row("bad", state, "/private/path")
        asked = d1 + 10 * 3600
        with closing(t._connect(self.path)) as db:
            writer.batch(db, [
                ("states", ([a("working"), b("waiting"), w("waiting"), cx("working"), bad("working")], d2 + 22 * 3600)),
                ("states", ([a("working"), b("working"), w("resting"), cx("waiting"), bad("waiting")], d1 + 3600)),
                ("states", ([a("waiting", attention=True), b("working", attention=True), w("waiting"),
                             cx("working")], asked)),
                ("states", ([a("waiting"), b("working"), w("waiting"), cx("working")], asked + 400)),
                ("resolved", ("shared", "a1", 400, asked + 400)),
                ("resolved", ("shared", "a2", 1000, asked + 400)),
                ("states", ([a("working"), b("waiting"), w("resting"), cx("working")], midnight + 7200)),
            ])
            hooks = [("a1", "TaskCompleted", d1 + 7200, {"task": "t1"}), ("a1", "TaskCompleted", d1 + 7300, {"task": "t1"}),
                     ("a1", "TaskCompleted", d1 + 7400, {}), ("a1", "TaskCompleted", d1 + 7500, {"task": "t2", "ok": False}),
                     ("a2", "PostToolUse", d1 + 8000, {"tool": "Bash", "kind": "git:commit"}),
                     ("a2", "PostToolUse", d1 + 8100, {"tool": "Bash", "kind": "git:commit", "ok": False}),
                     ("a2", "PostToolUseFailure", d1 + 8200, {"tool": "Bash", "kind": "git:commit", "ok": False}),
                     ("a2", "PostToolUse", d1 + 8300, {"tool": "Bash", "kind": "git:push"}),
                     ("a2", "PostToolUse", midnight + 600, {"tool": "Bash", "kind": "test"}),
                     ("a2", "PostToolUse", midnight + 700, {"tool": "Read"}),
                     ("a2", "PreToolUse", midnight + 800, {"tool": "Bash", "kind": "git:commit"}),
                     ("old", "PreToolUse", d1 + 9000, {"tool": "Read"}),
                     ("a1", "TaskCompleted", midnight + 4 * 3600, {"task": "future"})]
            with db:
                for n, (sid, ev, ts, extra) in enumerate(hooks):
                    writer.hook(db, self.hook(ev, ts=ts, sid=sid, **extra), f"edge:{n}")
                db.execute("DELETE FROM events WHERE ev='hire' AND sid IN ('old','w')")
                db.execute("DELETE FROM answers WHERE spanId IN (SELECT id FROM spans WHERE sid='a2' AND kind='ask')")
                t._event(db, {"sid": "ghost", "ts": d1 + 5 * 3600, "ev": "TaskCompleted", "task": "g"})
                t._event(db, {"sid": "ghost2", "ts": d1 + 6 * 3600, "ev": "PreToolUse"})
                # 別の UTC オフセットで書かれた行: 保存した day（昨日）と時計の日付（今日 00:30）が違う
                t._event(db, {"sid": "a1", "ts": midnight + 1800, "ev": "TaskCompleted", "task": "tz"})
                db.execute("UPDATE events SET day=? WHERE task='tz'", (t._day(d1),))
        return midnight, d1

    def test_readers_equal_reading_every_row_on_edge_cases(self):
        """R115: 必要な行だけ読む growth_json／digest_json が、全行を読んでいた R115 前の結果と JSON（型まで）で一致する。"""
        t = self.timeline
        midnight, d1 = self.edge_case_database()
        now = midnight + 3 * 3600
        for at in (now, d1 + 12 * 3600, midnight - 0.000001):
            with self.subTest(growth_at=at):
                t._READ_CACHE.clear()
                self.assertEqual(json.dumps(t.growth_json(self.home, at), sort_keys=True),
                                 json.dumps(self.legacy_growth(at), sort_keys=True))
        for day in [(date.today() - timedelta(days=n)).isoformat() for n in range(4)]:
            for since in (0.0, d1 + 9 * 3600):
                with self.subTest(day=day, since=since):
                    t._READ_CACHE.clear()
                    self.assertEqual(json.dumps(t.digest_json(self.home, day=day, since=since, now=now), sort_keys=True),
                                     json.dumps(self.legacy_digest(day, since, now), sort_keys=True))
        # 一致が偶然でない＝端の形が実際に結果へ出ている
        t._READ_CACHE.clear()
        growth = t.growth_json(self.home, now)["byProject"]
        self.assertEqual(growth["waitonly"]["xp"], 0)                       # 待つだけ（hire も無い）でも残る
        self.assertEqual(growth["123456789abc"]["xp"], 0)                   # hire の消えた古いセッション
        self.assertEqual(growth["unknown"]["breakdown"]["tasksDone"]["count"], 1)   # sessions に無い sid
        self.assertEqual(growth["cxp"]["breakdown"]["turnsCompleted"]["count"], 1)
        self.assertEqual(growth["shared"]["breakdown"]["asksAnswered"]["count"], 2)  # answers の無い旧いキーも
        self.assertEqual(growth["shared"]["breakdown"]["commits"]["count"], 1)

    def bare_home(self):
        tmp = tempfile.TemporaryDirectory(prefix="office_timeline_r115_")
        self.addCleanup(tmp.cleanup)
        home = Path(tmp.name)
        (home / self.timeline.DB_FILE).parent.mkdir(parents=True)
        db = self.timeline._connect(home / self.timeline.DB_FILE)
        self.addCleanup(db.close)
        return home, db

    def test_streak_counts_from_a_day_seen_only_in_events_spans_or_answers(self):
        """streak の「最初に観測した日」は、数えない event だけ／待つだけの span だけ／回答だけの日でも同じに立つ。"""
        t = self.timeline
        midnight = datetime.combine(date.today(), datetime.min.time()).timestamp()
        first = midnight - 2 * 86400 + 3600          # 2 日前 01:00 が唯一の活動
        now = midnight + 3600
        session = "INSERT INTO sessions VALUES ('s','claude','c','p1','N',?,?)"
        cases = {
            "event": [("INSERT INTO events(ts,day,sid,vendor,ev,tool,tgt,kind,nt,sub,task,ok) "
                       "VALUES (?,?,'s','claude','PreToolUse','Read','','','','','',1)", (first, t._day(first)))],
            "span": [(session, (first, first + 600)),
                     ("INSERT INTO spans(sid,kind,label,start,end,day) VALUES ('s','state','waiting',?,?,?)",
                      (first, first + 600, t._day(first)))],
            "answer": [(session, (first - 60, first)),
                       ("INSERT INTO spans(id,sid,kind,label,start,end,day) VALUES (7,'s','ask','attention',?,?,?)",
                        (first - 60, first, t._day(first - 60))),
                       ("INSERT INTO answers VALUES (7,?,60)", (first,))],
            "slow answer": [(session, (first - 1000, first)),
                            ("INSERT INTO spans(id,sid,kind,label,start,end,day) VALUES (7,'s','ask','attention',?,?,?)",
                             (first - 1000, first, t._day(first - 1000))),
                            ("INSERT INTO answers VALUES (7,?,1000)", (first,))],
        }
        expected = {"event": 3, "span": 3, "answer": 3, "slow answer": 2}   # 遅い回答の日（中央値 ≥15 分）で切れる
        for name, statements in cases.items():
            with self.subTest(only=name):
                home, db = self.bare_home()
                with db:
                    for sql, args in statements:
                        db.execute(sql, args)
                with closing(t._reader(home)) as reader:
                    self.assertEqual(reader.execute("SELECT COUNT(*) FROM events WHERE ev IN "
                                                    "('hire','TaskCompleted','turnCompleted')").fetchone()[0], 0)
                self.assertEqual(t.growth_json(home, now)["streak15"], expected[name])
                self.assertEqual(t.digest_json(home, since=0, now=now)["streak15"], expected[name])
                # 1 日前のダイジェストは「その日まで」の streak（今日は数えない）
                yesterday = (date.today() - timedelta(days=1)).isoformat()
                self.assertEqual(t.digest_json(home, day=yesterday, since=0, now=now)["streak15"],
                                 expected[name] - 1)

    def test_xp_reads_only_successful_git_commit_post_tool_use_rows(self):
        """XP に読む PostToolUse は git:commit だけ（他の kind・失敗・PreToolUse は読まない）＝全行を読んだ結果と同じ。"""
        t = self.timeline
        writer = t._Timeline(self.home)
        base = datetime.combine(date.today(), datetime.min.time()).timestamp() + 3600
        now = base + 600
        rows = [("PostToolUse", {"kind": "git:commit"}), ("PostToolUse", {"kind": "git:commit", "ok": False}),
                ("PostToolUseFailure", {"kind": "git:commit", "ok": False}), ("PreToolUse", {"kind": "git:commit"}),
                ("PostToolUse", {"kind": "git:push"}), ("PostToolUse", {"kind": "test"}),
                ("PostToolUse", {"kind": "", "tool": "Read"})]
        with closing(t._connect(self.path)) as db, db:
            for n, (ev, extra) in enumerate(rows):
                writer.hook(db, self.hook(ev, ts=base + n, **dict({"tool": "Bash"}, **extra)), f"x:{n}")
        facts = t._snapshot(self.home, now)[1]
        self.assertEqual(sorted((f["ev"], f["kind"]) for f in facts if f["ev"] != "hire"),
                         [("PostToolUse", "git:commit"), ("PostToolUse", "git:commit")])
        growth = t.growth_json(self.home, now)
        self.assertEqual(growth["byProject"]["123456789abc"]["breakdown"]["commits"]["count"], 1)
        self.assertEqual(json.dumps(growth, sort_keys=True), json.dumps(self.legacy_growth(now), sort_keys=True))
        digest = t.digest_json(self.home, since=0, now=now)
        self.assertEqual(digest["totals"]["commits"], 1)
        self.assertEqual(digest["totals"]["tools"], {"Bash": 3, "Read": 1})   # ダイジェストは数えない行も読む

    def test_event_sid_without_session_row_counts_for_unknown_project(self):
        """sessions に行の無い sid の event は projectId 'unknown'（数えない行だけでも byProject に残る）。"""
        t = self.timeline
        base = datetime.combine(date.today(), datetime.min.time()).timestamp() + 3600
        now = base + 600
        with closing(t._connect(self.path)) as db, db:
            t._event(db, {"sid": "ghost2", "ts": base, "ev": "PreToolUse"})
        growth = t.growth_json(self.home, now)
        self.assertEqual(list(growth["byProject"]), ["unknown"])
        self.assertEqual(growth["byProject"]["unknown"]["xp"], 0)
        with closing(t._connect(self.path)) as db, db:
            t._event(db, {"sid": "ghost", "ts": base + 10, "ev": "TaskCompleted", "task": "g"})
        t._READ_CACHE.clear()
        self.assertEqual(t.growth_json(self.home, now)["byProject"]["unknown"]["xp"], 10)
        digest = t.digest_json(self.home, since=0, now=now)
        self.assertEqual([(s["sid"], s["projectId"], s["vendor"], s["name"]) for s in digest["sessions"]],
                         [("ghost", "unknown", "", ""), ("ghost2", "unknown", "", "")])
        self.assertEqual(list(digest["growth"]["byProject"]), ["unknown"])

    def test_digest_available_from_an_event_a_reaching_span_or_an_answer(self):
        """available は「その日の event」「その日に掛かる state の span」「その日の回答」のどれか 1 つで立つ。"""
        t = self.timeline
        midnight = datetime.combine(date.today(), datetime.min.time()).timestamp()
        d1 = midnight - 86400
        now = midnight + 3600
        session = "INSERT INTO sessions VALUES ('s','claude','c','p1','N',?,?)"
        cases = {
            "event": [("INSERT INTO events(ts,day,sid,vendor,ev,tool,tgt,kind,nt,sub,task,ok) "
                       "VALUES (?,?,'s','claude','PreToolUse','Read','','','','','',1)", (d1 + 43200, t._day(d1 + 43200)))],
            # 2 日前 23:30 に始まり昨日 00:30 に終わる span＝昨日に掛かる
            "span": [(session, (d1 - 1800, d1 + 1800)),
                     ("INSERT INTO spans(sid,kind,label,start,end,day) VALUES ('s','state','working',?,?,?)",
                      (d1 - 1800, d1 + 1800, t._day(d1 - 1800)))],
            # 待ちは 2 日前に始まり、回答は昨日＝回答の日だけが立つ
            "answer": [(session, (d1 - 600, d1 + 300)),
                       ("INSERT INTO spans(id,sid,kind,label,start,end,day) VALUES (7,'s','ask','attention',?,?,?)",
                        (d1 - 600, d1 + 300, t._day(d1 - 600))),
                       ("INSERT INTO answers VALUES (7,?,900)", (d1 + 300,))],
        }
        days = {n: (date.today() - timedelta(days=n)).isoformat() for n in range(4)}
        expected = {"event": {1}, "span": {1, 2}, "answer": {1}}
        for name, statements in cases.items():
            with self.subTest(only=name):
                home, db = self.bare_home()
                with db:
                    for sql, args in statements:
                        db.execute(sql, args)
                self.assertEqual({n for n, day in days.items()
                                  if t.digest_json(home, day=day, since=0, now=now)["available"]}, expected[name])

    def test_cached_results_show_a_stored_row_once_now_reaches_its_time(self):
        """R115 反証: 結果の 60 秒キャッシュでも、DB に既にある「その時の now より後の ts」の行は、now が追い越した次の
        呼び出しで数える（R115 前＝生の行をキャッシュして呼び出しごとに ts<=now で絞った、と同じ）。数える event・回答・
        解消キーだけの回答・数えない event・window の終わりちょうどの行・events_kind_ts の無い DB。来る行が無い間はキャッシュ。"""
        t = self.timeline
        today = date.today().isoformat()
        T = datetime.combine(date.fromisoformat(today), datetime.min.time()).timestamp() + 12 * 3600

        def database(statements=(), rows=(), indexed=True):
            for suffix in ("", "-wal", "-shm"):
                Path(str(self.path) + suffix).unlink(missing_ok=True)
            t._READ_CACHE.clear()
            with closing(t._connect(self.path)) as db, db:
                if not indexed:
                    db.execute("DROP INDEX events_kind_ts")
                db.execute("INSERT INTO sessions VALUES ('s','claude','c','p1','N',?,?)", (T - 600, T - 600))
                for sql, args in statements:
                    db.execute(sql, args)
                for row in rows:
                    t._event(db, dict(row, sid="s"))

        def same(results, legacy):
            self.assertEqual([json.dumps(result, sort_keys=True) for result, _ in results],
                             [json.dumps(legacy(now), sort_keys=True) for _, now in results])

        ask = ("INSERT INTO spans(id,sid,kind,label,start,end,day) VALUES (7,'s','ask','attention',?,?,?)",
               (T - 100, T + 10, t._day(T - 100)))
        resolved = "resolved:" + hashlib.sha256(json.dumps(["s", T - 100]).encode()).hexdigest()
        cases = {
            "task": ((), [{"ts": T + 10, "ev": "TaskCompleted", "task": "later"}], "tasksDone"),
            "answer": ((ask, ("INSERT INTO answers VALUES (7,?,110)", (T + 10,))), (), "asksAnswered"),
            "resolved key": ((ask, ("INSERT INTO meta VALUES (?,?)", (resolved, today))), (), "asksAnswered"),
            "uncounted": ((), [{"ts": T + 10, "ev": "PostToolUse", "tool": "Read"}], None),
        }
        for name, (statements, rows, key) in cases.items():
            for indexed in (True, False):
                with self.subTest(row=name, events_kind_ts=indexed):
                    database(statements, rows, indexed)
                    growth, digest = [], []
                    for now in (T, T + 10.5):     # 同じ日のキー・TTL の内側で now だけ進める
                        growth.append((t.growth_json(self.home, now), now))
                        digest.append((t.digest_json(self.home, since=0, now=now), now))
                    same(growth, self.legacy_growth)
                    same(digest, lambda now: self.legacy_digest(today, 0.0, now))
                    if key:
                        self.assertEqual([g["office"]["breakdown"][key]["count"] for g, _ in growth], [0, 1])
                        self.assertEqual([d["totals"][key] for d, _ in digest], [0, 1])
                    else:
                        self.assertEqual([(list(g["byProject"]), g["streak15"]) for g, _ in growth],
                                         [([], 0), (["p1"], 1)])
                        self.assertEqual([d["totals"]["tools"] for d, _ in digest], [{}, {"Read": 1}])
        with self.subTest(row="at the window end"):
            # growth は ts<=now で数え、digest の window [start, now) は now を過ぎた次から数える
            database(rows=[{"ts": T, "ev": "TaskCompleted", "task": "edge"}])
            self.assertEqual(t.growth_json(self.home, T)["office"]["breakdown"]["tasksDone"]["count"], 1)
            digest = [(t.digest_json(self.home, since=0, now=now), now) for now in (T, T + 1)]
            same(digest, lambda now: self.legacy_digest(today, 0.0, now))
            self.assertEqual([d["totals"]["tasksDone"] for d, _ in digest], [0, 1])
        with self.subTest(row="none comes due"):
            # 読んだ後に書いた行は、来る行が無ければ TTL まで見えない（R115 前のスナップショットと同じ＝キャッシュは生きている）
            database(rows=[{"ts": T - 10, "ev": "TaskCompleted", "task": "one"}])
            self.assertEqual(t.growth_json(self.home, T)["office"]["breakdown"]["tasksDone"]["count"], 1)
            self.assertEqual(t.digest_json(self.home, since=0, now=T)["totals"]["tasksDone"], 1)
            with closing(sqlite3.connect(self.path)) as db, db:
                t._event(db, {"sid": "s", "ts": T - 5, "ev": "TaskCompleted", "task": "two"})
            self.assertEqual(t.growth_json(self.home, T + 30)["office"]["breakdown"]["tasksDone"]["count"], 1)
            self.assertEqual(t.digest_json(self.home, since=0, now=T + 30)["totals"]["tasksDone"], 1)
            self.assertEqual(t.growth_json(self.home, T + 60)["office"]["breakdown"]["tasksDone"]["count"], 2)


if __name__ == "__main__":
    unittest.main()
