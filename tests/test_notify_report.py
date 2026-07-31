#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""R50-P7d: デスクトップ通知（❗エッジ検出）と日報ビルダーの単体テスト。"""
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "server"))
import office_server as office  # noqa: E402


class TestAttentionDiff(unittest.TestCase):
    def test_new_attention_is_reported_once(self):
        roster = [
            {"projectId": "p1", "disp": "ブログ編集部", "question": "どっち？"},
            {"projectId": "p2", "disp": "制作本部", "approvalMin": 3},
            {"projectId": "p3", "disp": "平常運転", "approvalMin": 0},
        ]
        new, ids = office.attention_diff(set(), roster)
        self.assertEqual(sorted(new), ["ブログ編集部", "制作本部"])
        self.assertEqual(ids, {"p1", "p2"})
        # 2回目は同じ集合＝新規なし（連打しない）
        new2, ids2 = office.attention_diff(ids, roster)
        self.assertEqual(new2, [])
        self.assertEqual(ids2, ids)

    def test_resolved_then_reappears_fires_again(self):
        r1 = [{"projectId": "p1", "disp": "A", "question": "q"}]
        _, ids = office.attention_diff(set(), r1)
        _, ids = office.attention_diff(ids, [])          # 解消
        new, _ = office.attention_diff(ids, r1)          # 再発
        self.assertEqual(new, ["A"])

    def test_empty_roster_is_safe(self):
        new, ids = office.attention_diff(None, None)
        self.assertEqual((new, ids), ([], set()))


class TestDailyReport(unittest.TestCase):
    def test_report_contains_real_numbers_only(self):
        office_json = {
            "roster": [
                {"disp": "A", "work": {"counts": {"completed": 5}}},
                {"disp": "B", "work": None},
            ],
            "history": [{"text": "x"}, {"text": "y"}],
            "tasks": {"completed": 7, "inProgress": 1, "pending": 0},
        }
        title, body, md = office.build_daily_report(office_json, "2026-07-30")
        self.assertIn("完了7件", body)
        self.assertIn("2プロジェクト", body)
        self.assertIn("- 完了タスク: 7", md)
        self.assertIn("- 最多完了: A (5件)", md)
        self.assertIn("2026-07-30", md)

    def test_report_without_work_counts(self):
        title, body, md = office.build_daily_report(
            {"roster": [], "history": [], "tasks": {}}, "2026-01-01")
        self.assertIn("完了0件", body)
        self.assertNotIn("最多完了", md)


class TestNotifyFake(unittest.TestCase):
    def test_fake_notify_appends_to_marker(self):
        with tempfile.TemporaryDirectory() as tmp:
            marker = Path(tmp) / "notify.log"
            os.environ["OFFICE_FAKE_NOTIFY"] = str(marker)
            try:
                office.notify_mac("タイトル", "本文です")
                office.notify_mac("二通目", "x")
            finally:
                del os.environ["OFFICE_FAKE_NOTIFY"]
            lines = marker.read_text(encoding="utf-8").strip().split("\n")
            self.assertEqual(len(lines), 2)
            self.assertIn("タイトル\t本文です", lines[0])


if __name__ == "__main__":
    unittest.main()
