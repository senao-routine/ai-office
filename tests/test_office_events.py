"""Hook tailing, overlay precedence, subscriptions and retention."""
from datetime import date, timedelta
import http.client
import importlib.util
import json
import os
from pathlib import Path
import queue
import socket
import struct
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parent.parent
FIXTURE = ROOT / "tests/fixtures/events/day.jsonl"


def load_events():
    spec = importlib.util.spec_from_file_location("office_events_test", ROOT / "server/office_events.py")
    events = importlib.util.module_from_spec(spec)
    with patch.object(sys, "path", [str(ROOT / "server"), *sys.path]):
        spec.loader.exec_module(events)
    return events


class OfficeEventsTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix="office_events_")
        self.addCleanup(tmp.cleanup)
        self.home = Path(tmp.name)
        self.events = load_events()
        self.addCleanup(self.events.stop)
        self.day = date.today()
        self.rows = [json.loads(line) for line in FIXTURE.read_text(encoding="utf-8").splitlines()]
        self.sid = self.rows[0]["sid"]

    def append(self, *rows, day=None, raw=None):
        path = self.home / self.events.EVENTS_DIR / f"{(day or self.day).isoformat()}.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as stream:
            stream.write(raw if raw is not None else "".join(json.dumps(row) + "\n" for row in rows))
        return path

    def event(self, ev="Stop", **fields):
        return dict(self.rows[0], ev=ev, **fields)

    def info(self, **fields):
        return dict({"session": self.sid, "state": "resting", "listening": False}, **fields)

    def test_fixture_replays_state_transitions(self):
        self.assertEqual(len(self.rows), 8)
        expected = ["working", "working", "waiting", "working", "waiting",
                    "working", "waiting", "resting"]
        for sequence, (row, state) in enumerate(zip(self.rows, expected), 1):
            with self.subTest(ev=row["ev"]):
                self.append(row)
                self.events._poll(self.home)
                self.assertEqual(self.events.state_of(self.sid, row["ts"]), {
                    "ts": row["ts"], "ev": row["ev"], "tool": row["tool"],
                    "nt": row["nt"], "seq": sequence,
                })
                overlay = self.events.overlay(self.info(), self.home, row["ts"])
                self.assertEqual(overlay["state"], state)
                self.assertEqual(overlay.get("gone", False), row["ev"] == "SessionEnd")
                self.assertEqual(overlay["evSeq"], sequence)
        self.assertEqual(self.events.seq(), 8)

    def test_overlay_preserves_ask_listening_and_input(self):
        for row in (self.rows[3], self.rows[-1]):
            self.append(row)
            self.events._poll(self.home)
            for listening in (False, True):
                info = self.info(state="waiting", listening=listening, ask={"title": "Confirm"})
                before = dict(info)
                result = self.events.overlay(info, self.home, row["ts"])
                self.assertEqual(info, before)
                self.assertEqual(result, dict(before, evSeq=self.events.seq()))
                self.assertIs(result["listening"], listening)
        state = self.events.state_of(self.sid, self.rows[-1]["ts"])
        state["ev"] = "Stop"
        self.assertEqual(self.events.state_of(self.sid, self.rows[-1]["ts"])["ev"], "SessionEnd")

    def test_ttls_and_notification_kinds(self):
        for nt in ("permission_prompt", "idle_prompt", "agent_needs_input", "other"):
            self.append(self.event("Notification", nt=nt))
            self.events._poll(self.home)
            now = self.rows[0]["ts"]
            result = self.events.overlay(self.info(), self.home, now + 60)
            self.assertEqual(result["state"], "resting" if nt == "other" else "waiting")
            original = self.info()
            self.assertIs(self.events.overlay(original, self.home, now + 60.01), original)
            self.assertIsNone(self.events.state_of(self.sid, now + 60.01))
        self.append(self.event("PreToolUse"))
        self.events._poll(self.home)
        self.assertEqual(self.events.overlay(self.info(), self.home, now + 25)["state"], "working")
        # R102-4（意図した更新）: 本体の道具が走っている間は 25 秒を過ぎても作業中（TOOL_TTL まで）
        self.assertEqual(self.events.overlay(self.info(), self.home, now + 25.01)["state"], "working")
        self.assertEqual(self.events.overlay(self.info(), self.home, now + self.events.TOOL_TTL)["state"], "working")
        self.assertEqual(self.events.overlay(self.info(), self.home,
                                             now + self.events.TOOL_TTL + 0.01)["state"], "resting")

    def test_turn_keeps_working_while_the_transcript_looks_still(self):
        """R102-4: 27 分の検査を回すセッションが 4 分で「指示待ち」に化けた。ターンが開いている間は作業中。"""
        t0 = self.rows[0]["ts"]
        ev = lambda name, dt, **kw: self.event(name, ts=t0 + dt, **kw)   # noqa: E731
        info = self.info(state="waiting")
        self.append(ev("UserPromptSubmit", 0), ev("PreToolUse", 1), ev("PostToolUse", 60))
        self.events._poll(self.home)
        # 道具の後の考え（transcript は止まって見える）: 最後のイベントから TURN_TTL までは作業中
        self.assertEqual(self.events.overlay(info, self.home, t0 + 60 + self.events.TURN_TTL)["state"], "working")
        self.assertEqual(self.events.overlay(info, self.home, t0 + 60 + self.events.TURN_TTL + 1)["state"], "waiting")
        # Stop で閉じる → 上書きしない（直後は waiting・古くなれば transcript の推定のまま）
        self.append(ev("Stop", 70))
        self.events._poll(self.home)
        self.assertEqual(self.events.overlay(info, self.home, t0 + 71)["state"], "waiting")
        self.assertEqual(self.events.overlay(self.info(), self.home, t0 + 200)["state"], "resting")
        # 閉じたターンを裏のサブエージェント（ag=1）の道具で開き直さない・本体の道具（Stop フックの配達で続きを始めた）は開く
        self.append(ev("PreToolUse", 80, ag=1), ev("SubagentStop", 81))
        self.events._poll(self.home)
        self.assertEqual(self.events.overlay(self.info(), self.home, t0 + 200)["state"], "resting")
        self.append(ev("PreToolUse", 90))
        self.events._poll(self.home)
        self.assertEqual(self.events.overlay(self.info(), self.home, t0 + 90 + 1500)["state"], "working")
        # 許可のダイアログで保留 → 作業中と言わない
        self.append(ev("PermissionRequest", 95))
        self.events._poll(self.home)
        self.assertEqual(self.events.overlay(self.info(), self.home, t0 + 200)["state"], "resting")
        self.append(ev("PostToolUse", 100))
        self.events._poll(self.home)
        self.assertEqual(self.events.overlay(self.info(), self.home, t0 + 200)["state"], "working")
        # transcript の中断（Esc・Stop フックは走らない）がターンの最後のイベントより後 → 閉じたものとして扱う
        self.assertEqual(self.events.overlay(self.info(interruptedAt=t0 + 101), self.home, t0 + 200)["state"], "resting")
        self.assertEqual(self.events.overlay(self.info(interruptedAt=t0 + 99), self.home, t0 + 200)["state"], "working")
        # 許可の掲示（ask）がある間は掲示が正本（上書きしない）
        asked = self.info(state="waiting", ask={"title": "x"})
        self.assertEqual(self.events.overlay(asked, self.home, t0 + 200)["state"], "waiting")

    def test_interrupt_survives_neutral_events_from_background_subagents(self):
        """別モデルレビュー（3fb7b3f）: Esc の中断の後に裏のサブエージェントの SubagentStop（中立）が来ると ts が進み、中断より後の
        イベントに見えて作業中に戻った → 中断は本体の最後のイベント（main）と比べる。本体の次の開始で開く。"""
        t0 = self.rows[0]["ts"]
        ev = lambda name, dt, **kw: self.event(name, ts=t0 + dt, **kw)   # noqa: E731
        self.append(ev("UserPromptSubmit", 0), ev("PreToolUse", 1))
        self.events._poll(self.home)
        cut = self.info(interruptedAt=t0 + 5)
        self.assertEqual(self.events.overlay(cut, self.home, t0 + 400)["state"], "resting")
        self.append(ev("SubagentStop", 300), ev("PostToolUse", 301, ag=1))
        self.events._poll(self.home)
        self.assertEqual(self.events.overlay(cut, self.home, t0 + 400)["state"], "resting")
        self.append(ev("UserPromptSubmit", 350))
        self.events._poll(self.home)
        self.assertEqual(self.events.overlay(cut, self.home, t0 + 400)["state"], "working")

    def test_running_main_tool_clears_the_guessed_stuck_alert(self):
        """default モードの長い Bash・サブエージェント: transcript の最後が tool_use のまま 75 秒を越え、推測の
        approvalMin（「止まっている?」❗・通知）が立っていた。本体の道具が走っていると記録で分かるなら消す。"""
        t0 = self.rows[0]["ts"]
        ev = lambda name, dt, **kw: self.event(name, ts=t0 + dt, **kw)   # noqa: E731
        stuck = self.info(state="working", approvalMin=1, stuckTool="実行中 bash verify.sh")
        self.append(ev("UserPromptSubmit", 0), ev("PreToolUse", 1))
        self.events._poll(self.home)
        r = self.events.overlay(stuck, self.home, t0 + 81, asks_wired=True, holds_seen=True)
        self.assertEqual((r["state"], r["approvalMin"], r["stuckTool"]), ("working", 0, ""))
        # サブエージェントの道具（中立）が続いても本体の道具は走っている
        self.append(ev("PreToolUse", 100, ag=1), ev("PostToolUse", 120, ag=1))
        self.events._poll(self.home)
        r = self.events.overlay(dict(stuck, approvalMin=3), self.home, t0 + 200, asks_wired=True, holds_seen=True)
        self.assertEqual((r["approvalMin"], r["stuckTool"]), (0, ""))
        # PermissionRequest を記録しない配線・承認フックが無い配線では言い切らない（本物の許可待ちを隠さない）
        r = self.events.overlay(stuck, self.home, t0 + 81)
        self.assertEqual((r["approvalMin"], r["stuckTool"]), (1, "実行中 bash verify.sh"))
        r = self.events.overlay(stuck, self.home, t0 + 81, holds_seen=True)
        self.assertEqual((r["approvalMin"], r["stuckTool"]), (1, "実行中 bash verify.sh"))
        # 許可のダイアログで保留 → 推測の❗は残す
        self.append(ev("PermissionRequest", 130))
        self.events._poll(self.home)
        r = self.events.overlay(stuck, self.home, t0 + 135, asks_wired=True, holds_seen=True)
        self.assertEqual((r["approvalMin"], r["stuckTool"]), (1, "実行中 bash verify.sh"))
        # 道具の外（結果の後の考え）は道具が走っているとは言わない（推測をそのまま残す）
        self.append(ev("PostToolUse", 140))
        self.events._poll(self.home)
        r = self.events.overlay(stuck, self.home, t0 + 200, asks_wired=True, holds_seen=True)
        self.assertEqual(r["approvalMin"], 1)

    def test_reordered_hold_events_do_not_hide_a_real_dialog_without_the_approval_hook(self):
        """別モデルレビュー: office-event.sh は非同期＝同じ呼び出しの PreToolUse が PermissionRequest より後の時刻で記録され
        保留が解けて見える（実測 5〜56ms）。承認フックが無い配線では、それで本物の許可待ちの ❗ を消してはいけない。"""
        t0 = self.rows[0]["ts"]
        ev = lambda name, dt, **kw: self.event(name, ts=t0 + dt, **kw)   # noqa: E731
        self.append(ev("UserPromptSubmit", 0), ev("PermissionRequest", 1), ev("PreToolUse", 1.05))
        self.events._poll(self.home)
        stuck = self.info(state="waiting", approvalMin=2, stuckTool="実行中 deploy.sh")
        r = self.events.overlay(stuck, self.home, t0 + 130, holds_seen=True)
        self.assertEqual((r["approvalMin"], r["stuckTool"]), (2, "実行中 deploy.sh"))

    def test_hold_without_a_posting_means_the_answer_was_given_and_the_tool_runs(self):
        """PermissionRequest の後、ターミナルで答えて道具が走っている間（PostToolUse まで）保留のまま＝waiting→stuck に化けた。
        承認フックが配線済みなら許可のダイアログには必ず掲示が出る＝掲示が無いまま HOLD_GRACE を越えた保留は答えが出たもの。"""
        t0 = self.rows[0]["ts"]
        ev = lambda name, dt, **kw: self.event(name, ts=t0 + dt, **kw)   # noqa: E731
        self.append(ev("UserPromptSubmit", 0), ev("PreToolUse", 1), ev("PermissionRequest", 2))
        self.events._poll(self.home)
        stuck = self.info(state="waiting", approvalMin=1, stuckTool="実行中 git push")
        grace = self.events.HOLD_GRACE
        # 掲示が出る前（ASK_GRACE の間）は人を待っているとみなす
        r = self.events.overlay(stuck, self.home, t0 + 2 + grace - 1, asks_wired=True, holds_seen=True)
        self.assertEqual((r["state"], r["approvalMin"]), ("waiting", 1))
        r = self.events.overlay(stuck, self.home, t0 + 2 + grace, asks_wired=True, holds_seen=True)
        self.assertEqual((r["state"], r["approvalMin"], r["stuckTool"]), ("working", 0, ""))
        r = self.events.overlay(stuck, self.home, t0 + 300, asks_wired=True, holds_seen=True)
        self.assertEqual((r["state"], r["approvalMin"]), ("working", 0))
        # 承認フックが無い配線では保留のまま（従来どおり）
        r = self.events.overlay(stuck, self.home, t0 + 300, holds_seen=True)
        self.assertEqual((r["state"], r["approvalMin"]), ("waiting", 1))
        # 道具の時計は保留の時刻から（TOOL_TTL を越えたら作業中と言わない）
        late = t0 + 2 + self.events.TOOL_TTL + 1
        self.assertEqual(self.events.overlay(self.info(), self.home, late, asks_wired=True)["state"], "resting")

    def test_ended_session_stays_gone_after_the_state_ttl(self):
        """/exit したセッションが 60 秒後に transcript の窓から「指示待ち」（上限で止まっていたものは ❗）として戻っていた。"""
        t0 = self.rows[0]["ts"]
        ev = lambda name, dt, **kw: self.event(name, ts=t0 + dt, **kw)   # noqa: E731
        self.append(ev("UserPromptSubmit", 0), ev("Stop", 30), ev("SessionEnd", 40))
        self.events._poll(self.home)
        for now in (t0 + 41, t0 + 101, t0 + 3600):
            info = self.info(state="waiting", age=int(now - (t0 + 30)), err={"k": "limit", "ago": 5})
            self.assertTrue(self.events.overlay(info, self.home, now).get("gone"), now)
        # transcript が終了より後に進んだ（イベントの hook が SessionStart を取り逃した再開）＝戻す
        self.assertFalse(self.events.overlay(self.info(age=5), self.home, t0 + 200).get("gone"))
        # 再開（SessionStart）・次の指示で消える
        self.append(ev("SessionStart", 500))
        self.events._poll(self.home)
        self.assertFalse(self.events.overlay(self.info(age=1000), self.home, t0 + 1000).get("gone"))
        self.append(ev("SessionEnd", 1100), ev("UserPromptSubmit", 1200))
        self.events._poll(self.home)
        self.assertFalse(self.events.overlay(self.info(age=1000), self.home, t0 + 2000).get("gone"))

    def test_refresh_reads_events_in_a_process_without_the_thread(self):
        """別モデルレビュー（3fb7b3f）: 中継の配達員（relay_agent）は別プロセスで読み取りのスレッドが無い＝_TURN が空のまま
        スマホへ出していた → refresh(home) が増分で読む（2 回目は新しい行だけ）。スレッドが動いているプロセスでは何もしない。"""
        t0 = self.rows[0]["ts"]
        self.append(self.event("UserPromptSubmit", ts=t0), self.event("PreToolUse", ts=t0 + 1))
        self.events.refresh(self.home)
        self.assertTrue(self.events.turn_of(self.sid)["open"])
        self.assertEqual(self.events.seq(), 2)
        self.append(self.event("Stop", ts=t0 + 2))
        self.events.refresh(self.home)
        self.assertEqual((self.events.seq(), self.events.turn_of(self.sid)["open"]), (3, False))
        self.events.refresh(self.home)
        self.assertEqual(self.events.seq(), 3)                       # 同じ行を二度読まない
        # 別の HOME へ移ったら読み直す（前の HOME の状態を持ち越さない）
        other = self.home / "other"
        self.events.refresh(other)
        self.assertEqual((self.events.seq(), self.events.turn_of(self.sid)), (3, None))
        with patch.object(self.events, "_poll") as poll:
            self.events._THREAD = Mock(is_alive=Mock(return_value=True))
            try:
                self.events.refresh(self.home)
            finally:
                self.events._THREAD = None
            poll.assert_not_called()

    def test_stop_failure_marks_an_api_error_until_the_next_prompt(self):
        t0 = self.rows[0]["ts"]
        ev = lambda name, dt, **kw: self.event(name, ts=t0 + dt, **kw)   # noqa: E731
        self.append(ev("UserPromptSubmit", 0), ev("PreToolUse", 1), ev("StopFailure", 5))
        self.events._poll(self.home)
        r = self.events.overlay(self.info(state="working"), self.home, t0 + 6)
        self.assertEqual((r["state"], r["err"]), ("waiting", {"k": "api", "ago": 1}))
        r = self.events.overlay(self.info(), self.home, t0 + 5 + self.events.ERR_TTL)
        self.assertEqual(r["err"]["k"], "api")
        self.assertNotIn("err", self.events.overlay(self.info(), self.home, t0 + 5 + self.events.ERR_TTL + 1))
        # transcript 側で分かった種類（limit など）を上書きしない
        r = self.events.overlay(self.info(err={"k": "limit", "ago": 3}), self.home, t0 + 6)
        self.assertEqual(r["err"]["k"], "limit")
        self.append(ev("UserPromptSubmit", 10))
        self.events._poll(self.home)
        self.assertNotIn("err", self.events.overlay(self.info(), self.home, t0 + 11))

    def test_midnight_reads_yesterdays_continuation_and_new_file(self):
        yesterday = self.day - timedelta(days=1)
        with patch.object(self.events, "_today", return_value=yesterday):
            self.append(self.rows[0], day=yesterday)
            self.events._poll(self.home)
        self.append(self.rows[2], day=yesterday)
        self.append(self.rows[3])
        self.events._poll(self.home)
        self.assertEqual([e["ev"] for e in self.events.recent()], ["PostToolUse", "Stop", "PreToolUse"])
        # A delayed old Stop must not undo today's PreToolUse.
        self.append(self.rows[2], day=yesterday)
        self.events._poll(self.home)
        self.assertEqual(self.events.seq(), 4)
        self.assertEqual(self.events.overlay(self.info(), self.home, self.rows[3]["ts"])["state"], "working")
        with patch.object(self.events, "_today", return_value=self.day + timedelta(days=1)):
            self.events._poll(self.home)
        self.assertNotIn(yesterday.isoformat(), self.events._TAIL.offsets)

    def test_bad_lines_are_skipped_and_partial_lines_wait_for_newline(self):
        invalid = ["{broken", "[]", "null"]
        for fields in ({"v": 2}, {"v": True}, {"sid": ""}, {"sid": "../bad"},
                       {"sid": "a" * 65}, {"sid": "日本語"}, {"sid": 3},
                       {"ts": "today"}, {"ts": float("nan")}, {"ev": []}):
            invalid.append(json.dumps(dict(self.rows[0], **fields)))
        self.append(raw="\n".join(invalid) + "\n")
        partial = json.dumps(self.rows[0])
        path = self.append(raw=partial)
        self.events._poll(self.home)
        self.assertEqual(self.events.seq(), 0)
        self.append(raw="\n" + json.dumps(self.rows[2]) + "\n")
        self.events._poll(self.home)
        self.assertEqual(self.events.seq(), 2)
        self.assertEqual(self.events.state_of(self.sid, self.rows[2]["ts"])["ev"], "Stop")
        path.write_text(json.dumps(self.rows[3]) + "\n", encoding="utf-8")
        self.events._poll(self.home)
        self.assertEqual(self.events.seq(), 3)
        self.assertEqual(self.events.state_of(self.sid, self.rows[3]["ts"])["ev"], "PreToolUse")

    def test_missing_events_directory_is_noop(self):
        self.events._poll(self.home)
        self.assertEqual(self.events.seq(), 0)
        info = self.info()
        self.assertIs(self.events.overlay(info, self.home, time.time()), info)
        self.assertFalse((self.home / self.events.EVENTS_DIR).exists())

    def test_subscriptions_delivery_limit_unsubscribe_and_restart(self):
        subscribers = [self.events.subscribe() for _ in range(8)]
        self.assertTrue(all(isinstance(q, queue.Queue) for q in subscribers))
        self.assertIsNone(self.events.subscribe())
        self.append(self.rows[0])
        self.events.start(self.home)
        thread = self.events._THREAD
        self.events.start(self.home)
        self.assertIs(self.events._THREAD, thread)
        self.assertTrue(thread.daemon)
        for q in subscribers:
            self.assertEqual(q.get(timeout=1), dict(self.rows[0], seq=1))
        self.events.unsubscribe(subscribers[0])
        self.events.unsubscribe(subscribers[0])
        self.assertIsInstance(self.events.subscribe(), queue.Queue)
        self.append(self.rows[2])
        self.assertEqual(subscribers[1].get(timeout=1)["seq"], 2)
        self.assertTrue(subscribers[0].empty())
        self.events.stop()
        self.assertFalse(thread.is_alive())
        self.events.start(self.home)
        self.append(self.rows[3])
        self.assertEqual(subscribers[1].get(timeout=1)["seq"], 3)

    def test_cache_changes_fire_before_poke_and_tools_do_not_invalidate(self):
        """R90-D7 レビュー: 失効の合図は購読者へ poke を流す**前**に同期で出る（デバウンスで遅らせない）。
        PreToolUse/PostToolUse は多すぎるので合図しない。"""
        order = []
        callback = Mock(side_effect=lambda kind: order.append(("invalidate", kind)))
        q = self.events.subscribe()
        self.append(self.rows[2], self.rows[4], self.rows[2])
        self.events.start(self.home, callback)
        for _ in range(3):
            order.append(("poke", q.get(timeout=1)["ev"]))
        appended = [self.rows[2], self.rows[4], self.rows[2]]
        expected = [r["ev"] for r in appended if r["ev"] in self.events._INVALIDATE]
        pokes = [i for i, (k, ev) in enumerate(order) if k == "poke" and ev in self.events._INVALIDATE]
        invalidates = [i for i, (k, _) in enumerate(order) if k == "invalidate"]
        self.assertEqual([order[i][1] for i in invalidates], expected)              # 失効イベントごとに1回
        for n, st in enumerate(pokes):
            self.assertLess(invalidates[n], st)                                     # 各 poke の**前**に失効
        before = callback.call_count
        self.append(self.rows[0])                                                   # PreToolUse
        q.get(timeout=1)
        self.assertEqual(callback.call_count, before)                               # ツールイベントでは失効しない

    def test_callback_and_poll_exceptions_do_not_kill_daemon(self):
        notified = threading.Event()

        def broken_callback(kind):
            notified.set()
            raise RuntimeError("callback failed")

        original = self.events._poll
        attempts = iter([True, False])

        def flaky_poll(home):
            if next(attempts, False):
                raise OSError("temporary read failure")
            return original(home)

        with patch.object(self.events, "_poll", side_effect=flaky_poll):
            self.append(self.rows[2])
            self.events.start(self.home, broken_callback)
            self.assertTrue(notified.wait(2))
            notified.clear()
            self.append(self.rows[4])
            self.assertTrue(notified.wait(2))
            self.assertTrue(self.events._THREAD.is_alive())

    def test_ring_state_bounds_sequence_and_defensive_copies(self):
        now = self.rows[0]["ts"]
        rows = [self.event(sid=f"sess-{i}", ts=now + i) for i in range(2001)]
        self.append(*rows)
        self.events._poll(self.home)
        self.assertEqual(self.events.seq(), 2001)
        self.assertIsNone(self.events.state_of("sess-0", now))
        self.assertIsNotNone(self.events.state_of("sess-1", now))
        recent = self.events.recent(limit=1000)
        self.assertEqual((len(recent), recent[0]["seq"], recent[-1]["seq"]), (500, 1502, 2001))
        self.assertEqual(len(self.events.recent()), 200)
        self.assertEqual(self.events.recent(limit=0), [])
        self.assertEqual([e["seq"] for e in self.events.recent(1999)], [2000, 2001])
        recent[-1]["ev"] = "SessionEnd"
        self.assertEqual(self.events.recent(2000)[0]["ev"], "Stop")

    def test_prune_retains_fourteen_days_and_ignores_unrelated_paths(self):
        old = self.append(self.rows[0], day=self.day - timedelta(days=15))
        keep = self.append(self.rows[0], day=self.day - timedelta(days=14))
        today = self.append(self.rows[0])
        unrelated = old.with_name("notes.jsonl")
        unrelated.write_text("keep", encoding="utf-8")
        directory = old.with_name((self.day - timedelta(days=16)).isoformat() + ".jsonl")
        directory.mkdir()
        self.events.prune(self.home)
        self.assertFalse(old.exists())
        self.assertTrue(all(path.exists() for path in (keep, today, unrelated, directory)))

    def test_prune_on_startup_and_once_per_day(self):
        with patch.object(self.events, "prune") as prune:
            self.events._poll(self.home)
            self.events._poll(self.home)
            prune.assert_called_once_with(self.home)
            with patch.object(self.events, "_today", return_value=self.day + timedelta(days=1)):
                self.events._poll(self.home)
                self.events._poll(self.home)
            self.assertEqual(prune.call_count, 2)


class OfficeEventsHTTPTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix="office_sse_")
        self.addCleanup(tmp.cleanup)
        self.home = Path(tmp.name)
        self.events = load_events()
        self.addCleanup(self.events.stop)
        spec = importlib.util.spec_from_file_location("office_server_sse", ROOT / "server/office_server.py")
        self.office = importlib.util.module_from_spec(spec)
        with patch.dict(os.environ, {"OFFICE_HOME": str(self.home)}):
            spec.loader.exec_module(self.office)
        self.office.office_events = self.events
        env = patch.dict(os.environ, {"OFFICE_EVENTS": "1"})
        env.start()
        self.addCleanup(env.stop)
        handler = type("QuietHandler", (self.office.Handler,), {"log_message": lambda *args: None})
        self.server = self.office._OfficeHTTPServer(("127.0.0.1", 0), handler)
        self.thread = threading.Thread(target=self.server.serve_forever,
                                       kwargs={"poll_interval": .02}, daemon=True)
        self.thread.start()
        self.connections = []
        self.addCleanup(self.close_server)

    def event(self, **fields):
        return {"v": 1, "sid": "sse-session", "ts": time.time(), "ev": "Stop", **fields}

    def close_server(self):
        for sock, response in self.connections:
            response.close()
            # RST makes the next write fail immediately, without waiting for a 15s ping.
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0))
            sock.close()
        self.events.stop()
        deadline = time.monotonic() + 1
        while self.events._SUBSCRIBERS and time.monotonic() < deadline:
            self.events._ingest(json.dumps(self.event()))
            time.sleep(.01)
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=1)
        self.assertFalse(self.events._SUBSCRIBERS, "disconnected SSE subscribers leaked")

    def request(self, path="/api/events", *, local=True, origin=None, host="127.0.0.1"):
        sock = socket.create_connection(self.server.server_address, timeout=1)
        headers = [f"GET {path} HTTP/1.0", f"Host: {host}"]
        if local:
            headers.append("X-Office-Local: 1")
        if origin:
            headers.append(f"Origin: {origin}")
        sock.sendall(("\r\n".join(headers) + "\r\n\r\n").encode("ascii"))
        response = http.client.HTTPResponse(sock)
        self.connections.append((sock, response))
        response.begin()
        return response

    def read_event(self, response):
        fields = {}
        while True:
            line = response.fp.readline()
            self.assertTrue(line, "SSE closed before a complete event")
            if line == b"\n":
                return fields["event"], json.loads(fields["data"])
            key, value = line.decode("utf-8").rstrip("\n").split(":", 1)
            fields[key] = value.lstrip()

    def test_hello_then_file_poke_within_one_second_and_no_body_fields(self):
        self.events.start(self.home)
        response = self.request()
        self.assertEqual(response.status, 200)
        self.assertEqual(response.version, 10)
        self.assertEqual(response.getheader("Content-Type"), "text/event-stream; charset=utf-8")
        self.assertEqual(response.getheader("Cache-Control"), "no-store")
        self.assertIsNone(response.getheader("Content-Length"))
        self.assertIsNone(response.getheader("Transfer-Encoding"))
        self.assertEqual(self.read_event(response), ("hello", {"seq": 0, "v": 2}))
        row = self.event(tool="Read", text="fixture body must stay local", nt="idle_prompt")
        path = self.home / self.events.EVENTS_DIR / f"{date.today().isoformat()}.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        started = time.monotonic()
        with path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(row) + "\n")
        self.assertEqual(self.read_event(response), ("poke", {
            "seq": 1, "ts": row["ts"], "ev": "Stop", "sid": "sse-session",
        }))
        self.assertLessEqual(time.monotonic() - started, 1)

    def test_ninth_connection_is_429(self):
        for _ in range(8):
            response = self.request()
            self.assertEqual(response.status, 200)
            self.assertEqual(self.read_event(response)[0], "hello")
        self.assertEqual(self.request().status, 429)
        self.assertEqual(len(self.events._SUBSCRIBERS), 8)

    def test_missing_header_and_foreign_origin_or_host_are_403(self):
        for kwargs in ({"local": False}, {"origin": "https://example.invalid"},
                       {"host": "example.invalid"}):
            with self.subTest(kwargs=kwargs):
                self.assertEqual(self.request(**kwargs).status, 403)
                self.assertFalse(self.events._SUBSCRIBERS)

    def test_since_replays_beyond_default_limit_without_queue_duplicates(self):
        for _ in range(220):
            self.events._ingest(json.dumps(self.event()))
        original = self.events.recent

        def concurrent_recent(*args, **kwargs):
            # This row appears both in recent() and in the subscribed queue.
            self.events._ingest(json.dumps(self.event()))
            return original(*args, **kwargs)

        with patch.object(self.events, "recent", side_effect=concurrent_recent):
            response = self.request("/api/events?since=10")
            self.assertEqual(self.read_event(response), ("hello", {"seq": 220, "v": 2}))
            for seq in range(11, 222):
                kind, data = self.read_event(response)
                self.assertEqual((kind, data["seq"]), ("poke", seq))
        self.events._ingest(json.dumps(self.event()))
        self.assertEqual(self.read_event(response)[1]["seq"], 222)

    def test_future_cursor_after_restart_does_not_hide_live_events(self):
        response = self.request("/api/events?since=999")
        self.assertEqual(self.read_event(response), ("hello", {"seq": 0, "v": 2}))
        self.events._ingest(json.dumps(self.event()))
        self.assertEqual(self.read_event(response)[1]["seq"], 1)

    def test_invalid_cursor_and_disabled_events_do_not_consume_slots(self):
        for suffix in ("", "-1", "abc", "1.5", "1&since=2", "9" * 21):
            with self.subTest(suffix=suffix):
                self.assertEqual(self.request("/api/events?since=" + suffix).status, 400)
        with patch.dict(os.environ, {"OFFICE_EVENTS": "0"}):
            self.assertEqual(self.request().status, 503)
        self.assertFalse(self.events._SUBSCRIBERS)

    def test_idle_ping_uses_fifteen_second_timeout_and_unsubscribes(self):
        subscriber = Mock()
        subscriber.get.side_effect = [queue.Empty, BrokenPipeError]
        unsubscribed = threading.Event()
        with patch.object(self.events, "subscribe", return_value=subscriber), \
                patch.object(self.events, "unsubscribe", side_effect=lambda q: unsubscribed.set()) as unsubscribe:
            response = self.request()
            self.assertEqual(self.read_event(response)[0], "hello")
            self.assertEqual(response.fp.readline(), b": ping\n")
            self.assertEqual(response.fp.readline(), b"\n")
            self.assertTrue(unsubscribed.wait(1))
            unsubscribe.assert_called_once_with(subscriber)
        self.assertEqual(subscriber.get.call_count, 2)
        subscriber.get.assert_called_with(timeout=15)


class InvalidationOrderTest(unittest.TestCase):
    """R90-D7 レビュー: 購読キューは有界／SSE は書き込みタイムアウトを持つ（コード上の固定）。"""

    def _events(self):
        import importlib, sys
        sys.path.insert(0, str(ROOT / "server"))
        try:
            return importlib.import_module("office_events")
        finally:
            sys.path.pop(0)

    def test_subscriber_queue_is_bounded(self):
        ev = self._events()
        q = ev.subscribe()
        try:
            for i in range(ev.RING + 50):
                ev._ingest(json.dumps({"v": 1, "ts": float(i), "ev": "PostToolUse", "sid": "sess-bound-0001", "tool": "Read", "nt": ""}))
            self.assertLessEqual(q.qsize(), ev.RING)
        finally:
            ev.unsubscribe(q)

    def test_sse_handler_sets_write_timeout(self):
        src = (ROOT / "server" / "office_server.py").read_text(encoding="utf-8")
        body = src[src.index("def _send_sse_headers"):src.index("def _events")]
        self.assertIn("settimeout(SSE_WRITE_TIMEOUT)", body)
        self.assertIn("socket.timeout", src[src.index("def _events"):][:6000])


if __name__ == "__main__":
    unittest.main()
