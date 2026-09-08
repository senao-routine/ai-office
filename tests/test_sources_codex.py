# -*- coding: utf-8 -*-
"""Codex アダプタ: 合成 DB、寿命境界、WAL、ロック、本文非取得の回帰。"""
import copy
import importlib.util
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
from contextlib import closing
from pathlib import Path
from unittest import mock

TESTS = Path(__file__).resolve().parent
ROOT = TESTS.parent
FX = TESTS / "fixtures" / "codex"
_spec = importlib.util.spec_from_file_location(
    "source_codex_t", ROOT / "server" / "source_codex.py")
source = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(source)
PARENT_A = "cx-parent-a"
PARENT_B = "cx-parent-b"
CHILD_C = "cx-child-c"
NOW = 1700000000


class ParseTest(unittest.TestCase):
    def setUp(self):
        self.threads = [{
            "id": PARENT_A, "updated_at": NOW - 10, "source": "cli",
            "cwd": "/fixture/project", "git_branch": "main", "name": "Codex A",
            "archived": 0, "tokens_used": "1200", "agent_nickname": None,
        }]
        self.turns = {PARENT_A: {"status": "inProgress", "started_at": NOW - 60,
                                 "completed_at": None}}
        self.items = {PARENT_A: "commandExecution"}

    def parse(self, lang="ja", edges=()):
        return source.parse_codex(self.threads, edges, self.turns, self.items, NOW, lang)

    def test_employee_contract(self):
        employees, meta = self.parse()
        self.assertEqual(employees, [{
            "session": "cx-" + PARENT_A, "cwd": "/fixture/project", "branch": "main",
            "title": "Codex A", "age": 10, "mtime": NOW - 10, "state": "working",
            "kind": "tool", "verb": "実行中", "target": "", "feed": ["実行中"],
            "skills": [], "minions": 0, "pending": False, "listening": True,
            "lastSaid": "", "lastOrder": "", "question": "", "approvalMin": 0,
            "stuckTool": "", "dept": "Codex A", "role": "cli", "vendor": "codex",
            "tokens": 1200,
        }])
        self.assertEqual(meta, {"connected": True, "n": 1, "children": 0})

    def test_all_verbs_and_unknown_fallback(self):
        expected = {
            "commandExecution": ("実行中", "running"),
            "fileChange": ("編集中", "editing"),
            "agentMessage": ("報告中", "reporting"),
            "reasoning": ("考え中…", "thinking"),
            "mcpToolCall": ("スキル実行中", "using a tool"),
            "imageGeneration": ("画像を生成中", "generating image"),
            "userMessage": ("作業中", "working"),
            None: ("作業中", "working"),
        }
        for item, labels in expected.items():
            self.items[PARENT_A] = item
            for lang, label in zip(("ja", "en"), labels):
                with self.subTest(item=item, lang=lang):
                    employee = self.parse(lang)[0][0]
                    self.assertEqual(employee["verb"], label)
                    self.assertEqual(employee["feed"], [label])

    def test_turn_lifetime_boundary(self):
        for status, timestamp, active, kind in (
                ("inProgress", "started_at", "working", "tool"),
                ("completed", "completed_at", "waiting", "said")):
            for age in (source.TURN_ALIVE - 1, source.TURN_ALIVE, 7200):
                with self.subTest(status=status, age=age):
                    self.turns[PARENT_A] = {"status": status, timestamp: NOW - age}
                    employee = self.parse()[0][0]
                    alive = age < source.TURN_ALIVE
                    self.assertEqual(employee["state"], active if alive else "resting")
                    self.assertEqual(employee["kind"], kind if alive else "idle")
                    self.assertIs(employee["listening"], alive)

    def test_missing_turn_or_timestamp_is_resting(self):
        for turn in (None, {}, {"status": "inProgress"}, {"status": "completed"},
                     {"status": "failed", "started_at": NOW - 1}):
            with self.subTest(turn=turn):
                self.turns[PARENT_A] = turn
                self.assertEqual(self.parse()[0][0]["state"], "resting")

    def test_show_window_and_archived_filter(self):
        for age, archived, count in ((source.SHOW_WINDOW, 0, 1),
                                     (source.SHOW_WINDOW + 1, 0, 0), (0, 1, 0)):
            with self.subTest(age=age, archived=archived):
                self.threads[0].update(updated_at=NOW - age, archived=archived)
                self.assertEqual(len(self.parse()[0]), count)

    def test_finished_exec_is_resting_not_waiting(self):
        """`exec` はターンが終わるとプロセスごと消える＝指示は届かない。

        `waiting` にすると listening=True になり、オフィスが「ここに送れます」と言うが
        `codex queue` は失敗する。実測: 監査ワークフロー1本で終わった exec が52体
        「指示待ち」で並び、対話セッションが埋もれた（2026-09-08）。
        対話（cli/vscode）は人が座っているので waiting のままでよい。
        """
        self.turns[PARENT_A] = {"status": "completed", "completed_at": NOW - 10}
        for value, expected in (("exec", "resting"), ("cli", "waiting"), ("vscode", "waiting")):
            with self.subTest(source=value):
                self.threads[0].update(source=value, updated_at=NOW - 5)
                employee = self.parse()[0][0]
                self.assertEqual(employee["state"], expected)
                self.assertIs(employee["listening"], expected != "resting")

    def test_exec_threads_use_the_short_window(self):
        """`codex exec` は一発仕事＝終わったらオフィスに居ない。対話セッションは 3 時間のまま。

        実測（2026-09-07）: 直近3時間の thread 91 件のうち 77 件が exec で最終更新の中央値が
        86 分前だった。同じ窓で出すとオフィスが「終わった仕事の幽霊」で埋まる。
        """
        cases = ((("exec", source.EXEC_WINDOW - 1), 1), (("exec", source.EXEC_WINDOW + 1), 0),
                 (("cli", source.EXEC_WINDOW + 1), 1), (("cli", source.SHOW_WINDOW + 1), 0),
                 (("vscode", source.SHOW_WINDOW - 1), 1), (("", source.EXEC_WINDOW + 1), 1))
        for (value, age), count in cases:
            with self.subTest(source=value, age=age):
                self.threads[0].update(source=value, updated_at=NOW - age)
                self.assertEqual(len(self.parse()[0]), count)

    def test_only_parents_are_visible_and_only_open_children_count(self):
        for source_value in ('{"subagent":"review"}',
                             '{"subagent":{"thread_spawn":{"parent_thread_id":"cx-parent-a"}}}'):
            with self.subTest(source=source_value):
                self.threads = self.threads[:1] + [dict(self.threads[0], id=CHILD_C, source=source_value)]
                edge = {"parent_thread_id": PARENT_A, "child_thread_id": CHILD_C, "status": "open"}
                edges = [edge, dict(edge), dict(edge, child_thread_id="closed-child", status="closed"),
                         dict(edge, parent_thread_id="hidden-parent", child_thread_id="other-child")]
                employees, meta = self.parse(edges=edges)
                self.assertEqual(len(employees), 1)
                self.assertEqual(employees[0]["minions"], 1)
                self.assertEqual(meta["children"], 1)

    def test_session_satisfies_both_consumers_without_uuid_assumption(self):
        for thread_id in (PARENT_A, "01234567-89ab-cdef-0123-456789abcdef", "a" * 61):
            with self.subTest(thread_id=thread_id):
                self.threads[0]["id"] = thread_id
                session = self.parse()[0][0]["session"]
                self.assertEqual(session, "cx-" + thread_id)
                self.assertIsNotNone(re.fullmatch(r"[A-Za-z0-9_-]{1,64}", session))
                self.assertIsNotNone(re.fullmatch(r"[a-zA-Z0-9-]{8,64}", session))

    def test_invalid_session_ids_are_not_rewritten(self):
        for thread_id in ("", "a", "a" * 62, "bad_id", "../parent", "parent\n", None):
            with self.subTest(thread_id=thread_id):
                self.threads[0]["id"] = thread_id
                self.assertEqual(self.parse()[0], [])

    def test_defaults_and_age_use_latest_metadata_time(self):
        self.threads[0].update(name=None, agent_nickname="Fixture Bot", git_branch=None,
                               cwd=None, tokens_used=None, source="vscode")
        self.turns[PARENT_A]["started_at"] = NOW - 2
        employee = self.parse()[0][0]
        self.assertEqual((employee["dept"], employee["title"], employee["branch"],
                          employee["cwd"], employee["tokens"], employee["age"], employee["role"]),
                         ("Fixture Bot", "", "", "", 0, 2, "vscode"))
        self.threads[0]["agent_nickname"] = None
        self.turns[PARENT_A].update(status="completed", completed_at=NOW - 1)
        employee = self.parse()[0][0]
        self.assertEqual((employee["dept"], employee["age"]), ("Codex", 1))

    def test_body_fields_are_never_accessed_and_outputs_are_empty(self):
        class NoBody(dict):
            def __getitem__(self, key):
                if key in ("title", "first_user_message", "preview", "item_json"):
                    raise AssertionError("body accessed")
                return super().__getitem__(key)

            def get(self, key, default=None):
                return self[key] if key in self else default

        self.threads[0] = NoBody(dict(self.threads[0], title="private", first_user_message="private",
                                      preview="private", item_json="private"))
        employee = self.parse()[0][0]
        for field in ("lastSaid", "lastOrder", "target", "question", "stuckTool"):
            self.assertEqual(employee[field], "")
        self.assertNotIn("private", repr(employee))

    def test_parse_has_no_io_or_input_mutation(self):
        original = copy.deepcopy((self.threads, self.turns, self.items))
        with mock.patch.object(source.sqlite3, "connect", side_effect=AssertionError("DB IO")), \
                mock.patch.object(source, "Path", side_effect=AssertionError("file IO")), \
                mock.patch.object(source.time, "time", side_effect=AssertionError("clock IO")):
            self.parse()
        self.assertEqual((self.threads, self.turns, self.items), original)


class DatabaseTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        FX.mkdir(exist_ok=True)
        cls.temp = tempfile.TemporaryDirectory(prefix="runtime-", dir=FX)
        cls.addClassCleanup(cls.temp.cleanup)
        result = subprocess.run(
            [sys.executable, str(TESTS / "make_home.py")],
            env=dict(os.environ, TMPDIR=cls.temp.name), capture_output=True, text=True, check=True)
        cls.fixture_home = Path(result.stdout.strip())
        with closing(sqlite3.connect(cls.fixture_home / source.STATE_FILE)) as db:
            cls.now = db.execute("SELECT updated_at FROM threads WHERE id = ?", (PARENT_A,)).fetchone()[0]

    def setUp(self):
        # 各ケースの DB を隔離し、実 HOME へは接続しない。
        temp = tempfile.TemporaryDirectory(prefix="case-", dir=self.temp.name)
        self.addCleanup(temp.cleanup)
        self.home = Path(temp.name)
        (self.home / ".codex").mkdir()
        for filename in (source.STATE_FILE, source.HISTORY_FILE):
            shutil.copyfile(self.fixture_home / filename, self.home / filename)
        self.env = mock.patch.dict(os.environ, {"OFFICE_HOME": str(self.home)})
        self.env.start()
        self.addCleanup(self.env.stop)

    def employees(self, lang="ja"):
        return source.codex_employees(os.environ["OFFICE_HOME"], self.now, lang)

    def execute(self, filename, sql, params=()):
        with closing(sqlite3.connect(self.home / filename)) as db:
            with db:
                db.execute(sql, params)

    def test_make_home_parents_states_and_minions(self):
        employees, meta = self.employees()
        self.assertEqual(meta, {"connected": True, "n": 2, "children": 1})
        self.assertEqual([e["session"] for e in employees], ["cx-" + PARENT_A, "cx-" + PARENT_B])
        # PARENT_B は fixture で source="exec"＝終わった exec はプロセスが消えている。
        # 2026-09-08 以降 waiting（指示待ち）にはしない（届かない相手を待たせない）。
        self.assertEqual([e["state"] for e in employees], ["working", "resting"])
        self.assertEqual([e["minions"] for e in employees], [1, 0])
        self.assertEqual(employees[0]["verb"], "実行中")
        self.assertEqual(self.employees("en")[0][0]["verb"], "running")

    def test_stale_inprogress_db_is_resting_despite_fresh_updated_at(self):
        self.execute(source.HISTORY_FILE, "UPDATE thread_turns SET started_at = ? WHERE thread_id = ?",
                     (self.now - 7200, PARENT_A))
        self.assertEqual(self.employees()[0][0]["state"], "resting")

    def test_archived_db_row_is_filtered(self):
        self.execute(source.STATE_FILE, "UPDATE threads SET archived = 1 WHERE id = ?", (PARENT_A,))
        rows, _ = source.read_rows(self.home, now=self.now)
        self.assertNotIn(PARENT_A, [row["id"] for row in rows["threads"]])
        employees, meta = self.employees()
        self.assertEqual([e["session"] for e in employees], ["cx-" + PARENT_B])
        self.assertEqual(meta["children"], 0)

    def test_show_window_db_boundary(self):
        self.execute(source.STATE_FILE, "UPDATE threads SET updated_at = ? WHERE id = ?",
                     (self.now - source.SHOW_WINDOW, PARENT_A))
        self.assertEqual(len(self.employees()[0]), 2)
        self.execute(source.STATE_FILE, "UPDATE threads SET updated_at = updated_at - 1 WHERE id = ?",
                     (PARENT_A,))
        rows, _ = source.read_rows(self.home, now=self.now)
        self.assertNotIn(PARENT_A, [row["id"] for row in rows["threads"]])
        self.assertEqual(len(self.employees()[0]), 1)

    def test_missing_either_db_is_disconnected_and_not_created(self):
        for filename in (source.STATE_FILE, source.HISTORY_FILE):
            with self.subTest(filename=filename):
                path = self.home / filename
                path.rename(path.with_suffix(".saved"))
                self.assertEqual(self.employees(), ([], {"connected": False, "reason": "missing"}))
                self.assertFalse(path.exists())
                path.with_suffix(".saved").rename(path)

    def test_missing_updated_at_column_is_disconnected(self):
        self.execute(source.STATE_FILE, "ALTER TABLE threads RENAME COLUMN updated_at TO old_updated_at")
        self.assertEqual(self.employees(), ([], {"connected": False, "reason": "locked-or-schema"}))

    def test_missing_history_column_is_disconnected(self):
        self.execute(source.HISTORY_FILE, "ALTER TABLE thread_items RENAME COLUMN item_type TO old_item_type")
        self.assertEqual(self.employees(), ([], {"connected": False, "reason": "locked-or-schema"}))

    def test_exclusive_lock_returns_within_half_second(self):
        for filename in (source.STATE_FILE, source.HISTORY_FILE):
            with self.subTest(filename=filename), closing(sqlite3.connect(self.home / filename)) as db:
                db.execute("BEGIN EXCLUSIVE")
                started = time.monotonic()
                result = self.employees()
                elapsed = time.monotonic() - started
                self.assertEqual(result, ([], {"connected": False, "reason": "locked-or-schema"}))
                self.assertLess(elapsed, 0.5)
                db.rollback()

    def test_sql_trace_excludes_all_body_columns_and_wildcards(self):
        statements = []
        rows, meta = source.read_rows(self.home, now=self.now, trace=statements.append)
        self.assertTrue(meta["connected"])
        self.assertEqual(len(rows["threads"]), 3)
        sql = "\n".join(statements).lower()
        for forbidden in ("title", "first_user_message", "preview", "item_json"):
            self.assertNotRegex(sql, r"\b" + forbidden + r"\b")
        self.assertNotRegex(sql, r"select\s+(?:\w+\.)?\*")
        for table in ("threads", "thread_spawn_edges", "thread_turns", "thread_items"):
            self.assertIn("from " + table, sql)
        self.assertEqual(sql.count("pragma query_only=1"), 2)

    def test_latest_turn_uses_ordinal_and_item_uses_time_within_that_turn(self):
        self.execute(source.HISTORY_FILE, """INSERT INTO thread_turns
            (thread_id, turn_id, rollout_ordinal, status, started_at, completed_at)
            VALUES (?, 'turn-2', 2, 'completed', ?, ?)""", (PARENT_A, self.now - 90, self.now - 80))
        # turn-1 の item の方が新しくても、採るのは turn-2 の中の最新時刻。
        for item_id, ordinal, age, item_type in (("item-a", 1, 80, "fileChange"),
                                                 ("item-b", 2, 85, "reasoning")):
            self.execute(source.HISTORY_FILE, """INSERT INTO thread_items
                (thread_id, turn_id, item_id, rollout_ordinal, created_at_ms, item_json, item_type)
                VALUES (?, 'turn-2', ?, ?, ?, '{}', ?)""",
                         (PARENT_A, item_id, ordinal, (self.now - age) * 1000, item_type))
        employee = self.employees()[0][0]
        self.assertEqual((employee["state"], employee["verb"]), ("waiting", "編集中"))

    def test_readonly_connections_see_committed_wal_updates(self):
        connect = sqlite3.connect
        with closing(connect(self.home / source.STATE_FILE)) as state, \
                closing(connect(self.home / source.HISTORY_FILE)) as history:
            for db in (state, history):
                self.assertEqual(db.execute("PRAGMA journal_mode=WAL").fetchone()[0], "wal")
                db.execute("PRAGMA wal_autocheckpoint=0")
            state.execute("UPDATE threads SET tokens_used = 4321 WHERE id = ?", (PARENT_A,))
            state.commit()
            history.execute("UPDATE thread_items SET item_type = 'fileChange' WHERE thread_id = ?", (PARENT_A,))
            history.commit()
            for filename in (source.STATE_FILE, source.HISTORY_FILE):
                self.assertGreater(Path(str(self.home / filename) + "-wal").stat().st_size, 0)
            with mock.patch.object(source.sqlite3, "connect", wraps=connect) as opened:
                employee = self.employees()[0][0]
            self.assertEqual((employee["tokens"], employee["verb"]), (4321, "編集中"))
            self.assertEqual(opened.call_count, 2)
            for call in opened.call_args_list:
                self.assertIn("?mode=ro", call.args[0])
                self.assertNotIn("immutable", call.args[0])
                self.assertEqual(call.kwargs, {"uri": True, "timeout": 0.2})

    def test_uri_metacharacters_in_home_path(self):
        unusual = self.home / "space ?# home"
        shutil.copytree(self.home / ".codex", unusual / ".codex")
        self.assertEqual(source.codex_employees(unusual, self.now), self.employees())

    def test_corrupt_database_is_disconnected(self):
        (self.home / source.STATE_FILE).write_bytes(b"not a sqlite database")
        self.assertEqual(self.employees(), ([], {"connected": False, "reason": "locked-or-schema"}))

    def test_read_rows_default_clock_and_missing_turn(self):
        with mock.patch.object(source.time, "time", return_value=self.now):
            rows, _ = source.read_rows(self.home)
        self.assertEqual(len(rows["threads"]), 3)
        self.execute(source.HISTORY_FILE, "DELETE FROM thread_turns WHERE thread_id = ?", (PARENT_A,))
        employee = self.employees()[0][0]
        self.assertEqual((employee["state"], employee["verb"]), ("resting", "作業中"))


if __name__ == "__main__":
    unittest.main()
