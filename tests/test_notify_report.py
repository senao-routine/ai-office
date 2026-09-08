#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""R50-P7d: デスクトップ通知（❗エッジ検出）と日報ビルダーの単体テスト。"""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

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


class TestNotificationV2(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name)
        self.enterContext(patch.object(office, "_HOME", self.home))
        self.enterContext(patch.object(office, "DAILY_DIR", self.home / "daily"))
        self.enterContext(patch.dict(os.environ, {"OFFICE_TIMELINE": "1"}))
        self.lang = self.enterContext(patch.object(office, "office_lang", return_value="ja"))
        self.now = datetime(2026, 9, 7, 8, 30).timestamp()
        self.seen = datetime(2026, 9, 6, 18).timestamp()
        self.timeline = office.office_timeline
        self.timeline.mark_seen(self.home, self.seen)
        self.db = self.timeline._connect(self.home / self.timeline.DB_FILE)
        self.addCleanup(self.db.close)
        self.roster = [{"projectId": "p1", "disp": "works 6号", "approvalMin": 420,
                        "ask": {"kind": "permission", "tool": "Bash", "ts": self.now - 420 * 60},
                        "question": "PRIVATE_QUESTION"}]

    def event(self, ev, ts, **extra):
        self.timeline._event(self.db, {"sid": "session-a", "ts": ts, "ev": ev, **extra})
        self.db.commit()
        self.timeline._READ_CACHE.clear()

    def test_morning_golden_ja_en_and_cross_midnight(self):
        for index, ts in enumerate([self.seen + 60, self.now - 60, self.now - 30]):
            self.event("TaskCompleted", ts, task=str(index))
        self.event("TaskCompleted", self.seen - 1, task="already-seen")
        result = office.build_morning_report({"roster": self.roster}, self.now)
        self.assertEqual(result[1], "🏢 おはようございます — 夜のあいだに ✅3件 終了、❗1体 が待っています"
                         "（works 6号・Bash の許可・7時間）")
        self.lang.return_value = "en"
        self.assertEqual(office.build_morning_report({"roster": self.roster}, self.now)[1],
                         "🏢 Good morning — Overnight: ✅3 completed, ❗1 waiting"
                         " (works 6号 · Bash permission · 7h)")

    def test_empty_or_seen_day_is_silent_even_with_old_attention(self):
        self.assertIsNone(office.build_morning_report({}, self.now))
        self.event("TaskCompleted", self.seen - 1, task="old")
        old = dict(self.roster[0], ask={"kind": "permission", "ts": self.seen - 60})
        self.assertIsNone(office.build_morning_report({"roster": [old]}, self.now))
        self.timeline.mark_seen(self.home, self.now)
        self.assertIsNone(office.build_morning_report({"roster": self.roster}, self.now))

    def test_resolved_new_attention_still_counts_after_many_events(self):
        writer = self.timeline._Timeline(self.home)
        row = {"sid": "session-a", "vendor": "claude", "state": "waiting", "attention": True}
        writer.states(self.db, [row], self.seen + 1)
        writer.states(self.db, [dict(row, attention=False)], self.seen + 60)
        for index in range(501):
            self.timeline._event(self.db, {"sid": "session-a", "ts": self.seen + index + 2,
                                          "ev": "PostToolUse"})
        self.db.commit()
        report = office.build_morning_report({}, self.now)
        self.assertIsNotNone(report)
        self.assertIn("✅0件 終了、❗0体", report[1])

    def test_auto_permission_request_does_not_break_morning_silence(self):
        writer = self.timeline._Timeline(self.home)
        ts = self.seen + 60
        for index, (ev, offset) in enumerate([
                ("PermissionRequest", 0), ("PreToolUse", 0.2), ("PostToolUse", 0.3)]):
            writer.hook(self.db, {"v": 1, "sid": "session-a", "ev": ev,
                                  "ts": ts + offset, "tool": "Bash"}, str(index))
        writer.states(self.db, [{"sid": "session-a", "vendor": "claude",
                                "state": "working", "attention": False}], ts + 1)
        self.db.commit()
        self.assertIsNone(office.build_morning_report({}, self.now))
        self.assertEqual(self.db.execute("SELECT label FROM spans WHERE kind='ask'").fetchall(),
                         [("attention",)])

    def test_seen_project_question_uses_questioner_age_despite_active_peer(self):
        self.timeline.mark_seen(self.home, self.now - 30 * 60)  # 08:00
        employees = [
            {"session": "questioner", "cwd": "/demo", "state": "waiting",
             "disp": "Demo", "age": 40 * 60, "question": "PRIVATE_QUESTION"},  # 07:50
            {"session": "active-peer", "cwd": "/demo", "state": "working", "age": 10},
        ]
        roster = office.group_by_project(employees)
        self.assertEqual(roster[0]["age"], 10)
        self.assertIsNone(office.build_morning_report({"roster": roster}, self.now))
        self.event("TaskCompleted", self.now - 5, task="new")
        self.assertIn("質問・40分", office.build_morning_report({"roster": roster}, self.now)[1])

    def test_attention_only_and_question_duration(self):
        row = dict(self.roster[0], approvalMin=0, ask={"kind": "question", "ts": self.now - 180})
        self.assertEqual(office.build_morning_report({"roster": [row]}, self.now)[1],
                         "🏢 おはようございます — 夜のあいだに ✅0件 終了、❗1体 が待っています"
                         "（works 6号・質問・3分）")

    def test_daily_golden_ja_en_and_level_omission(self):
        today = {"available": True, "totals": {"asksAnswered": 12, "medianWaitSec": 360,
                                               "tasksDone": 5, "activeMin": 180}}
        yesterday = {"available": True, "totals": {"asksAnswered": 2, "medianWaitSec": 4500}}
        snapshot = {"roster": [dict(self.roster[0], projectId=f"p{i}") if i < 2
                                else {"projectId": f"p{i}"} for i in range(9)],
                    "generatedAt": self.now, "growth": {"office": {"level": 8}}}
        with patch.object(office, "_notification_digest", side_effect=lambda day: (
                today if day == "2026-09-07" else yesterday)), \
                patch.object(self.timeline, "growth_json", return_value={"office": {"level": 7}}):
            self.assertEqual(office.build_daily_report(snapshot, "2026-09-07")[1],
                             "🏢 今日のオフィス — ❗12件に答えた（中央値 6分・昨日 75分）"
                             " · ✅5件 · 稼働 9 · Lv.7→8 · 止まったまま帰る子: 2体")
            self.lang.return_value = "en"
            self.assertEqual(office.build_daily_report(snapshot, "2026-09-07")[1],
                             "🏢 Today's office — Answered ❗12 (median 6 min; yesterday 75 min)"
                             " · ✅5 completed · Active 9 · Lv.7→8 · Still waiting at closing: 2")
            snapshot.update(roster=[], growth={"office": {"level": 7}})
            body = office.build_daily_report(snapshot, "2026-09-07")[1]
            self.assertNotIn("Lv.", body)
            self.assertNotIn("Still waiting", body)

    def test_no_yesterday_median_when_unobserved(self):
        digest = {"available": True, "totals": {"asksAnswered": 0, "tasksDone": 0}}
        with patch.object(office, "_notification_digest", side_effect=[digest, None]), \
                patch.object(self.timeline, "growth_json", side_effect=OSError):
            body = office.build_daily_report({}, "2026-09-07")[1]
        self.assertNotIn("昨日", body)
        self.assertNotIn("Lv.", body)
        self.assertNotIn("止まったまま", body)

    def test_level_change_uses_cumulative_history_not_daily_xp(self):
        for index in range(9):
            self.event("TaskCompleted", self.seen + index, task=f"previous-{index}")
        for index in range(2):
            self.event("TaskCompleted", self.now - 60 + index, task=f"today-{index}")
        with patch.object(office.time, "time", return_value=self.now):
            body = office.build_daily_report({"generatedAt": self.now}, "2026-09-07")[1]
        self.assertIn("✅2件", body)
        self.assertIn("Lv.0→1", body)   # 90 historical XP + 20 today, not daily level 0.

    def test_watcher_morning_once_and_silence_survives_restart(self):
        for report in (None, ("title", "body")):
            with self.subTest(report=report), tempfile.TemporaryDirectory() as tmp, \
                    patch.object(office, "DAILY_DIR", Path(tmp)), \
                    patch.object(office, "office_json", return_value={}), \
                    patch.object(office, "build_morning_report", return_value=report) as build, \
                    patch.object(office, "notify_mac") as notify:
                for _restart in range(2):
                    with patch.object(office.time, "sleep", side_effect=[None, None, None, KeyboardInterrupt]), \
                            patch.object(office, "datetime") as clock:
                        clock.now.side_effect = [datetime(2026, 9, 7, 8, m) for m in (29, 30, 31)]
                        with self.assertRaises(KeyboardInterrupt):
                            office._watch_loop()
                self.assertEqual(build.call_count, 1)
                self.assertEqual(notify.call_count, int(report is not None))

    def test_native_notification_keeps_full_english_digest(self):
        body = "🏢 Today's office — " + "x" * 150
        with patch.dict(os.environ, {"OFFICE_FAKE_NOTIFY": ""}), \
                patch.object(office.subprocess, "run") as run:
            office.notify_mac("title", body)
        self.assertIn(body, run.call_args.args[0][2])

    def test_push_golden_permission_question_approval_and_privacy(self):
        source = (Path(__file__).resolve().parents[1] / "relay/src/worker.js").read_text()
        targets = source[source.index("function pushTargets("):source.index("// R5_PUSH_TARGETS_END")]
        push = source[source.index("async function sendAttnPushes("):source.index("// P7: /status push")]
        script = targets + "\n" + push + "\n" + r'''
const assert = require('node:assert/strict');
const sent = [];
async function sendWebPush(sub, payload) { sent.push(payload); return 201; }
(async () => {
  for (const [kind, expected] of [['permission', 'Bash の許可'], ['question', '質問'], ['approval', '承認']]) {
    const entry = { projectId: 'p1', disp: 'works 6号', ask: { kind, tool: 'Bash', title: 'PRIVATE_TITLE' },
      approvalMin: 3, question: 'PRIVATE_QUESTION' };
    const room = { getStatus: async () => ({ json: JSON.stringify({ roster: [entry] }) }) };
    await sendAttnPushes(room, { VAPID_JWK: '{}' }, ['p1'], { p1: { disp: entry.disp, dept: 'works' } },
      [{ k: 'push:test', v: '{}' }], 'https://example.test');
    assert.equal(sent.at(-1).body, `❗ works 6号 — ${expected}を待っています（3分）`);
    assert(!JSON.stringify(sent).includes('PRIVATE_'));
  }
  const before = sent.length;
  await sendAttnPushes({}, { VAPID_JWK: '{}' }, ['p2'], { p2: { disp: 'hidden', dept: 'other' } },
    [{ k: 'push:test', v: JSON.stringify({ depts: ['works'] }) }], 'https://example.test');
  assert.equal(sent.length, before);
})().catch(e => { console.error(e); process.exitCode = 1; });
'''
        result = subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)

    def push_body(self, entry):
        source = (Path(__file__).resolve().parents[1] / "relay/src/worker.js").read_text()
        targets = source[source.index("function pushTargets("):source.index("// R5_PUSH_TARGETS_END")]
        push = source[source.index("async function sendAttnPushes("):source.index("// P7: /status push")]
        script = targets + "\n" + push + r'''
async function sendWebPush(sub, payload) { console.log(JSON.stringify(payload)); return 201; }
const entry = JSON.parse(process.argv[1]);
const room = { getStatus: async () => ({ json: JSON.stringify({ roster: [entry] }) }) };
sendAttnPushes(room, { VAPID_JWK: '{}' }, ['p1'], { p1: { disp: 'Demo', dept: 'demo' } },
  [{ k: 'push:test', v: '{}' }], 'https://example.test').catch(() => process.exitCode = 1);
'''
        result = subprocess.run(["node", "-e", script, json.dumps(dict(entry, projectId="p1"))],
                                capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("PRIVATE_", result.stdout)
        return json.loads(result.stdout)["body"]

    def test_push_legacy_question_without_ask_is_classified_as_question(self):
        self.assertEqual(self.push_body({"question": "PRIVATE_QUESTION", "approvalMin": 0}),
                         "❗ Demo — 質問を待っています（0分）")

    def test_push_question_minutes_through_parser_grouping_and_redaction(self):
        import relay_agent
        path = self.home / "session-question.jsonl"
        path.write_text(json.dumps({"type": "assistant", "cwd": "/demo", "message": {
            "role": "assistant", "content": [{"type": "tool_use", "id": "question-1",
                "name": "AskUserQuestion", "input": {"questions": [{"question": "PRIVATE_QUESTION"}]}}]
        }}) + "\n")
        os.utime(path, (self.now - 7 * 60, self.now - 7 * 60))
        for ask in (None, {"kind": "question", "tool": "AskUserQuestion", "title": "PRIVATE_TITLE",
                           "options": [], "ts": self.now - 7 * 60}):
            with self.subTest(hooked=bool(ask)), patch.object(office, "pending_approval", return_value=ask):
                questioner = office.parse_session(path, self.now)
                self.assertEqual(questioner["approvalMin"], 0)
                self.assertTrue(questioner["question"])
                entries = [questioner, {"session": "peer", "cwd": "/demo", "state": "working", "age": 1}]
                project = office.group_by_project(entries)[0]
                for entry in (questioner, project):
                    relay_agent._redact_entry_for_relay(entry)
                    self.assertEqual(self.push_body(entry), "❗ Demo — 質問を待っています（7分）")


if __name__ == "__main__":
    unittest.main()
