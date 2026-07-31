# -*- coding: utf-8 -*-
"""TaskCreate/TaskUpdate/TodoWrite の解析ピン。"""
import importlib.util
import os
import shutil
import tempfile
import unittest
from pathlib import Path

TESTS = Path(__file__).resolve().parent
ROOT = TESTS.parent
FX = TESTS / "fixtures"

os.environ.setdefault("OFFICE_HOME", tempfile.mkdtemp(prefix="office_work_home_"))
spec = importlib.util.spec_from_file_location(
    "office_work_tasks", ROOT / "server" / "office_server.py")
office = importlib.util.module_from_spec(spec)
spec.loader.exec_module(office)


class WorkTaskTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="office_work_fx_"))
        office._TASK_MEMORY.clear()

    def tearDown(self):
        office._TASK_MEMORY.clear()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _load(self, fixture, name="work-session.jsonl", now_offset=10):
        path = self.tmp / name
        shutil.copy(FX / fixture, path)
        mtime = office.transcript_event_time(
            {"timestamp": "2026-07-24T10:00:09+09:00"}, 0)
        os.utime(path, (mtime, mtime))
        return path, office.parse_session(path, now=mtime + now_offset)

    def test_task_fixture_produces_now_next_done_and_counts(self):
        _path, employee = self._load("task_work.jsonl")
        self.assertEqual(employee["work"], {
            "now": ["概要欄を生成中"],
            "next": ["サムネイルを選ぶ"],
            "done": ["構成案をレビュー"],
            "counts": {"pending": 1, "in_progress": 1, "completed": 1},
        })

    def test_task_create_id_matching_and_status_operations(self):
        path = self.tmp / "operations.jsonl"
        shutil.copy(FX / "task_work.jsonl", path)
        lines = office._task_lines(path)
        operations = office._task_operations(lines, 0)
        created = [task_id for op, task_id, _value in operations if op == "set"]
        self.assertEqual(created, ["21", "22", "23"])
        self.assertIn(("delete", "23", None), operations)
        self.assertEqual(operations[-1][0], "replace")

    def test_deleted_task_is_removed(self):
        _path, employee = self._load("task_deleted.jsonl")
        self.assertNotIn("work", employee)

    def test_missing_created_id_falls_back_to_appearance_order(self):
        path, employee = self._load("task_fallback.jsonl")
        self.assertEqual(employee["work"]["next"], ["IDなしタスク"])
        self.assertEqual(employee["work"]["counts"], {
            "pending": 1, "in_progress": 0, "completed": 0,
        })
        self.assertEqual(office._task_operations(office._task_lines(path), 0)[0][1], "1")

    def test_tasks_survive_80kb_tail_scroll_and_expire_after_60_minutes(self):
        path = self.tmp / "scroll-session.jsonl"
        prefix = (FX / "task_work.jsonl").read_text(encoding="utf-8")
        waiting = (FX / "waiting_said.jsonl").read_text(encoding="utf-8")
        path.write_text(prefix + ("x" * 100_000) + "\n" + waiting, encoding="utf-8")
        mtime = office.transcript_event_time(
            {"timestamp": "2026-07-24T10:00:09+09:00"}, 0)
        os.utime(path, (mtime, mtime))
        first = office.parse_session(path, now=mtime + 10)
        self.assertIn("work", first)

        path.write_text(waiting, encoding="utf-8")
        os.utime(path, (mtime + 60, mtime + 60))
        retained = office.parse_session(path, now=mtime + 70)
        self.assertIn("work", retained)

        os.utime(path, (mtime + 61 * 60, mtime + 61 * 60))
        expired = office.parse_session(path, now=mtime + 61 * 60)
        self.assertNotIn("work", expired)


if __name__ == "__main__":
    unittest.main()
