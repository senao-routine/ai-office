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

    def test_tasks_survive_80kb_tail_scroll_and_completed_expire_after_60_minutes(self):
        """R64更新: 未完了(pending/in_progress)は時間で消えない。completedだけ60分で掃除。"""
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
        after = office.parse_session(path, now=mtime + 61 * 60)
        # 旧仕様=全消し。新仕様=未完了2件は残り、completedだけ掃除される
        self.assertIn("work", after)
        self.assertEqual(after["work"]["counts"],
                         {"pending": 1, "in_progress": 1, "completed": 0})


if __name__ == "__main__":
    unittest.main()


class TaskWindowTest(unittest.TestCase):
    """R64: 長大セッションの増分読み＋未完了タスクの保持（時間で消さない）。

    実測バグ: 55MBセッションで TaskCreate が末尾窓の外へ流れ、さらに60分の
    全消し剪定で「作業中なのにタスクの進みが空」になった。"""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="office_task_win_"))
        office._TASK_MEMORY.clear()
        office._TASK_OFFSETS.clear()

    def tearDown(self):
        office._TASK_MEMORY.clear()
        office._TASK_OFFSETS.clear()
        shutil.rmtree(self.tmp, ignore_errors=True)

    @staticmethod
    def _create_line(use_id, subject):
        import json
        return (json.dumps({"type": "assistant", "timestamp": "2026-07-24T10:00:00+09:00",
                            "message": {"content": [{"type": "tool_use", "id": use_id,
                                                     "name": "TaskCreate",
                                                     "input": {"subject": subject}}]}})
                + "\n"
                + json.dumps({"type": "user", "timestamp": "2026-07-24T10:00:01+09:00",
                              "message": {"content": [{"type": "tool_result",
                                                       "tool_use_id": use_id,
                                                       "content": f"Task #7 created successfully: {subject}"}]}})
                + "\n")

    @staticmethod
    def _update_line(task_id, status):
        import json
        return json.dumps({"type": "assistant", "timestamp": "2026-07-24T11:30:00+09:00",
                           "message": {"content": [{"type": "tool_use", "name": "TaskUpdate",
                                                    "input": {"taskId": task_id,
                                                              "status": status}}]}}) + "\n"

    def test_incremental_read_keeps_create_outside_window(self):
        """TaskCreateが初回窓に入ってさえいれば、その後何MB伸びても追跡が切れない。"""
        path = self.tmp / "long.jsonl"
        key = str(path)
        base = office.transcript_event_time({"timestamp": "2026-07-24T10:00:00+09:00"}, 0)
        path.write_text(self._create_line("u1", "巨大タスク"), encoding="utf-8")
        lines0 = office._task_lines(path, session_key=key, now=base)  # 初回読み
        office._remembered_tasks(key, lines0, base, base)             # 実運用と同じ対呼び出し
        # 窓サイズを超えるノイズ行を追記（旧実装ならCreateが窓外へ消える量）
        noise = '{"type":"assistant","message":{"content":[{"type":"text","text":"' \
                + "x" * 4000 + '"}]}}\n'
        with path.open("a", encoding="utf-8") as f:
            for _ in range(600):                                      # ≈2.4MB
                f.write(noise)
            f.write(self._update_line("7", "in_progress"))
        old_tail = office.TASK_TAIL_BYTES
        office.TASK_TAIL_BYTES = 1 * 1024 * 1024                      # 窓1MB=Createは確実に窓外
        try:
            lines = office._task_lines(path, session_key=key, now=base + 10)
            tasks = office._remembered_tasks(key, lines, base + 20, base)
        finally:
            office.TASK_TAIL_BYTES = old_tail
        self.assertEqual(tasks["7"]["subject"], "巨大タスク")          # タイトルが生きている
        self.assertEqual(tasks["7"]["status"], "in_progress")

    def test_unfinished_tasks_survive_task_window(self):
        """pending/in_progress は60分無操作でも消えない。completed だけ掃除される。"""
        path = self.tmp / "quiet.jsonl"
        key = str(path)
        base = office.transcript_event_time({"timestamp": "2026-07-24T10:00:00+09:00"}, 0)
        path.write_text(self._create_line("u1", "残る仕事")
                        + self._update_line("7", "in_progress"), encoding="utf-8")
        lines = office._task_lines(path, session_key=key, now=base)
        much_later = base + office._TASK_WINDOW * 3                   # 3時間後
        tasks = office._remembered_tasks(key, lines, much_later, base)
        self.assertEqual(tasks["7"]["status"], "in_progress")         # 消えていない
        # 完了へ更新→さらに窓超過→掃除される
        path.write_text(self._update_line("7", "completed"), encoding="utf-8")
        office._TASK_OFFSETS[key]["offset"] = 0
        lines2 = office._task_lines(path, session_key=key, now=much_later)
        tasks2 = office._remembered_tasks(key, lines2, much_later + office._TASK_WINDOW + 60,
                                          base)
        self.assertEqual(tasks2, {})

    def test_incremental_second_read_returns_only_new_lines(self):
        path = self.tmp / "inc.jsonl"
        key = str(path)
        base = 1000.0
        path.write_text(self._create_line("u1", "A"), encoding="utf-8")
        first = office._task_lines(path, session_key=key, now=base)
        self.assertTrue(first)
        second = office._task_lines(path, session_key=key, now=base + 1)
        self.assertEqual(second, [])                                  # 増分なし
        with path.open("a", encoding="utf-8") as f:
            f.write(self._update_line("7", "completed"))
        third = office._task_lines(path, session_key=key, now=base + 2)
        self.assertEqual(len(third), 1)                               # 追記分だけ

    def test_shrunk_file_resets_offset(self):
        path = self.tmp / "shrink.jsonl"
        key = str(path)
        path.write_text(self._create_line("u1", "A") * 3, encoding="utf-8")
        office._task_lines(path, session_key=key, now=1.0)
        path.write_text(self._create_line("u2", "B"), encoding="utf-8")  # 縮小
        lines = office._task_lines(path, session_key=key, now=2.0)
        self.assertTrue(lines)                                        # リセットして読み直せる
