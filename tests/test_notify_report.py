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
        # R53.2: 通知本文に質問プレビューを載せるため dict で返す
        self.assertEqual(sorted(i["disp"] for i in new), ["ブログ編集部", "制作本部"])
        by = {i["disp"]: i for i in new}
        self.assertEqual(by["ブログ編集部"]["question"], "どっち？")
        self.assertEqual(by["制作本部"]["approvalMin"], 3)
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
        self.assertEqual([i["disp"] for i in new], ["A"])

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


class TestAttnTrack(unittest.TestCase):
    """R54: ❗滞在時間トラッキング（純関数）と日報への反映。"""

    def test_track_and_resolve_durations(self):
        r_attn = [{"projectId": "p1", "disp": "A", "question": "q"},
                  {"projectId": "p2", "disp": "B", "approvalMin": 2}]
        seen, resolved = office._attn_track({}, r_attn, 1000.0)
        self.assertEqual((seen, resolved), ({"p1": 1000.0, "p2": 1000.0}, []))
        # 継続中は初見時刻を保つ・p2だけ解消→待たせ秒が出る
        seen, resolved = office._attn_track(
            seen, [{"projectId": "p1", "disp": "A", "question": "q"}], 1300.0)
        self.assertEqual(seen, {"p1": 1000.0})
        self.assertEqual(resolved, [300.0])
        # 全解消
        seen, resolved = office._attn_track(seen, [], 1600.0)
        self.assertEqual((seen, resolved), ({}, [600.0]))
        # 空入力で落ちない
        self.assertEqual(office._attn_track(None, None, 1.0), ({}, []))

    def test_report_includes_answered_stats(self):
        with tempfile.TemporaryDirectory() as tmp:
            orig = office.DAILY_DIR
            office.DAILY_DIR = Path(tmp)
            try:
                office._append_daily_stats([120.0, 240.0], "2026-08-01")
                office._append_daily_stats([60.0], "2026-08-01")   # 追記で積む
                _t, body, md = office.build_daily_report(
                    {"roster": [], "history": [], "tasks": {}}, "2026-08-01")
                self.assertIn("- 答えた❗: 3 件（平均待たせ 2分）", md)
                self.assertIn("❗応答3件", body)
                # stats が無い日は行自体を出さない（嘘のメトリクス禁止）
                _t2, body2, md2 = office.build_daily_report(
                    {"roster": [], "history": [], "tasks": {}}, "2026-08-02")
                self.assertNotIn("答えた❗", md2)
            finally:
                office.DAILY_DIR = orig


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
