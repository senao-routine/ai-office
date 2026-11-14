# -*- coding: utf-8 -*-
"""R126 出勤の口（server/office_door.py）の門。契約の正本= docs/door.md。

一時 HOME だけを使う（本物の ~/.claude に書かない）。見るもの:
  登録なしは拒否・上限・期限・冪等・並行の flock・毒入りの行・選択肢の解決 4 型（Mac とスマホの文の型は ui/ の実物から拾う）・
  行の必須キー・守る線（承認の掲示板に置かない・人だけが Bot へ言葉を渡せる・セッションの稼働と日報に混ぜない）・CLI。
"""
import importlib.util
import json
import math
import multiprocessing as mp
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
SERVER = ROOT / "server"


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


door = _load("office_door_t", SERVER / "office_door.py")
HOUR = 3600.0


class DoorBase(unittest.TestCase):
    def setUp(self):
        self.home = Path(tempfile.mkdtemp(prefix="door_home_"))
        self.addCleanup(shutil.rmtree, self.home, ignore_errors=True)
        self.now = time.time()

    def add(self, bot_id="demo", name="見回り", **kw):
        r = door.add_bot(bot_id, name, home=self.home, now=self.now, **kw)
        self.assertTrue(r["ok"], r)
        return r

    def call(self, fn, *args, at=None, **kw):
        return fn(*args, home=self.home, now=self.now if at is None else at, **kw)

    def state(self, bot_id="demo"):
        return json.loads(door.state_path(bot_id, self.home).read_text(encoding="utf-8"))

    def rows(self, at=None, lang="ja"):
        return door.door_employees(self.home, self.now if at is None else at, lang=lang, show_window=3 * 3600)

    def audit(self):
        p = door.audit_path(self.home)
        return [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines()] if p.exists() else []

    def log(self, bot_id="demo"):
        p = door.log_path(bot_id, self.home)
        return [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines()] if p.exists() else []

    def snapshot(self):
        """口のファイルの中身と時刻（flock の .lock は開くたびに時刻が動くだけ＝データではないので除く）。"""
        base = door.door_dir(self.home)
        if not base.exists():
            return {}
        return {str(p.relative_to(base)): (p.stat().st_mtime_ns, p.read_bytes())
                for p in base.rglob("*") if p.is_file() and not p.name.endswith(".lock")}


# ── 台帳 ─────────────────────────────────────────────────
class LedgerTest(DoorBase):
    def test_add_registers_and_every_file_is_private(self):
        r = self.add("demo", "見回り", role="受信箱")
        self.assertEqual((r["session"], r["name"], r["role"], r["updated"]), ("bot-demo", "見回り", "受信箱", False))
        self.call(door.checkin, "demo", "見回り中")
        self.call(door.report, "demo", "済み")
        for d in (door.door_dir(self.home), door.door_dir(self.home) / "bot"):
            self.assertEqual(stat.S_IMODE(d.stat().st_mode), 0o700, d)
        for p in (door.ledger_path(self.home), door.state_path("demo", self.home),
                  door.log_path("demo", self.home), door.audit_path(self.home)):
            self.assertEqual(stat.S_IMODE(p.stat().st_mode), 0o600, p)

    def test_bad_ids_are_refused(self):
        for bad in ("abc", "ABCD", "-abc", "a" * 33, "../evil", "bot/x", "", None, "démo", "ab cd", "abc_"):
            r = door.add_bot(bad, "x", home=self.home, now=self.now)
            self.assertEqual(r.get("error"), "bad-args", bad)
        for good in ("abcd", "a" * 32, "a-b-", "0bot"):
            self.assertTrue(door.add_bot(good, "x", home=self.home, now=self.now)["ok"], good)
        self.assertEqual(set(door.list_bots(home=self.home, now=self.now)[i]["bot"] for i in range(4)),
                         {"abcd", "a" * 32, "a-b-", "0bot"})

    def test_add_again_updates_the_name_and_keeps_the_added_time(self):
        self.add("demo", "見回り")
        r = door.add_bot("demo", "郵便受け", home=self.home, now=self.now + 50)
        self.assertTrue(r["ok"] and r["updated"])
        raw = json.loads(door.ledger_path(self.home).read_text(encoding="utf-8"))
        self.assertEqual(raw["bots"]["demo"]["name"], "郵便受け")
        self.assertAlmostEqual(raw["bots"]["demo"]["added"], self.now, places=3)

    def test_name_and_role_limits_and_cleaning(self):
        self.assertEqual(door.add_bot("demo", "x" * 25, home=self.home)["error"], "bad-args")
        self.assertEqual(door.add_bot("demo", "x", role="y" * 17, home=self.home)["error"], "bad-args")
        self.assertEqual(door.add_bot("demo", " ‮\x00 ", home=self.home)["error"], "bad-args")   # 見えない文字だけ＝空
        r = self.add("demo", "見\x00回‮り\n係", role="受\t信")
        self.assertEqual((r["name"], r["role"]), ("見回り 係", "受 信"))

    def test_remove_deletes_state_and_log_and_closes_the_door(self):
        self.add()
        self.call(door.checkin, "demo", "見回り中")
        self.call(door.report, "demo", "済み")
        self.assertTrue(self.call(door.remove_bot, "demo")["ok"])
        self.assertFalse(door.state_path("demo", self.home).exists())
        self.assertFalse(door.log_path("demo", self.home).exists())
        self.assertEqual(self.call(door.checkin, "demo", "x")["error"], "unregistered")
        self.assertFalse(door.state_path("demo", self.home).exists(), "外した Bot の状態が作り直された")
        self.assertEqual(self.call(door.remove_bot, "demo")["error"], "unregistered")
        self.assertEqual(self.rows()[0], [])

    def test_a_broken_ledger_is_never_overwritten(self):
        self.add()
        door.ledger_path(self.home).write_text("{oops", encoding="utf-8")
        self.assertEqual(self.call(door.add_bot, "other", "x")["error"], "ledger-broken")
        self.assertEqual(door.ledger_path(self.home).read_text(encoding="utf-8"), "{oops")
        rows, meta = self.rows()
        self.assertEqual((rows, meta["ok"], meta["bots"]), ([], False, 0))


# ── 登録なしは拒否 ────────────────────────────────────────
class UnregisteredTest(DoorBase):
    def test_every_tool_refuses_an_unregistered_bot(self):
        self.add("other", "ほか")
        calls = [
            lambda: self.call(door.checkin, "ghost", "x"),
            lambda: self.call(door.report, "ghost", "x"),
            lambda: self.call(door.ask, "ghost", "x?"),
            lambda: self.call(door.inbox, "ghost"),
            lambda: self.call(door.withdraw, "ghost", "0" * 32),
            lambda: self.call(door.instruct, "ghost", "x", src="local"),
            lambda: self.call(door.answer, "ghost", "0" * 32, "x", src="local"),
        ]
        for c in calls:
            self.assertEqual(c()["error"], "unregistered")
        self.assertFalse(door.state_path("ghost", self.home).exists())
        self.assertFalse(door.log_path("ghost", self.home).exists())
        refusals = [a for a in self.audit() if a["ev"] == "refuse" and a["bot"] == "ghost"]
        self.assertEqual(len(refusals), len(calls))
        self.assertTrue(all(a["why"] == "unregistered" for a in refusals))

    def test_a_fresh_home_is_not_written_by_a_refusal(self):
        r = self.call(door.checkin, "ghost", "x")
        self.assertEqual(r["error"], "unregistered")
        self.assertFalse((self.home / ".claude").exists(), "登録の無い HOME に口のフォルダを作った")
        self.assertEqual(self.rows(), ([], {"ok": True, "bots": 0, "n": 0, "asks": 0}))
        self.assertFalse((self.home / ".claude").exists(), "走査が書いた")


# ── 出勤 ─────────────────────────────────────────────────
class CheckinTest(DoorBase):
    def test_checkin_records_and_tells_what_is_waiting(self):
        self.add()
        self.call(door.instruct, "demo", "棚を見て", src="local")
        r = self.call(door.checkin, "demo", "受信箱を見回り中", state="working", ttl_min=20)
        self.assertTrue(r["ok"])
        self.assertEqual((r["answers"], r["instructs"], r["others"], r["same"]), (0, 1, 0, False))
        self.assertIn("答え 0・指示 1", r["text"])
        st = self.state()
        self.assertEqual(st["checkin"]["doing"], "受信箱を見回り中")
        self.assertEqual(st["checkin"]["ttl"], 20)

    def test_the_same_checkin_is_not_written_until_half_its_ttl(self):
        self.add()
        self.call(door.checkin, "demo", "見回り中", ttl_min=10)
        before = self.snapshot()
        r = self.call(door.checkin, "demo", "見回り中", ttl_min=10, at=self.now + 60)
        self.assertTrue(r["ok"] and r["same"])
        self.assertEqual(self.snapshot(), before, "同じ内容の出勤で書いた")
        r = self.call(door.checkin, "demo", "見回り中", ttl_min=10, at=self.now + 6 * 60)
        self.assertFalse(r["same"])                                   # 期限の半分を過ぎた＝延ばす
        self.assertAlmostEqual(self.state()["checkin"]["ts"], self.now + 360, places=3)
        r = self.call(door.checkin, "demo", "別の仕事", ttl_min=10, at=self.now + 400)
        self.assertFalse(r["same"])
        self.assertEqual([x["doing"] for x in self.log() if x["ev"] == "checkin"], ["見回り中", "別の仕事"],
                         "期限を延ばしただけの出勤を記録に残した")

    def test_checkin_limits(self):
        self.add()
        for kw in ({"doing": ""}, {"doing": "x" * 61}, {"doing": None}, {"doing": 5},
                   {"doing": "x", "state": "sleeping"}, {"doing": "x", "ttl_min": 0},
                   {"doing": "x", "ttl_min": 241}, {"doing": "x", "ttl_min": True},
                   {"doing": "x", "ttl_min": "15"}, {"doing": "x", "ttl_min": 1.5}):
            args = dict({"state": "working", "ttl_min": 15}, **kw)
            r = self.call(door.checkin, "demo", args.pop("doing"), **args)
            self.assertEqual(r.get("error"), "bad-args", kw)
        self.assertFalse(door.state_path("demo", self.home).exists())
        self.assertTrue(self.call(door.checkin, "demo", "x" * 60, ttl_min=15.0)["ok"])
        self.assertTrue(self.call(door.checkin, "demo", "y", ttl_min=240)["ok"])
        self.assertTrue(self.call(door.checkin, "demo", "z", ttl_min=1)["ok"])


# ── 期限 ─────────────────────────────────────────────────
class ExpiryTest(DoorBase):
    def test_working_for_ttl_then_resting_then_gone(self):
        self.add()
        self.call(door.checkin, "demo", "見回り中", ttl_min=15)
        (row,), _ = self.rows(self.now + 60)
        self.assertEqual((row["state"], row["verb"], row["kind"]), ("working", "見回り中", "tool"))
        (row,), _ = self.rows(self.now + 15 * 60 + 1)
        self.assertEqual((row["state"], row["verb"], row["kind"]), ("resting", "休憩中", "idle"))
        (row,), _ = self.rows(self.now + 15 * 60 + 3 * HOUR - 1, lang="en")
        self.assertEqual(row["verb"], "resting")
        self.assertEqual(self.rows(self.now + 15 * 60 + 3 * HOUR + 1)[0], [], "退勤しない")

    def test_an_open_question_keeps_the_bot_present_until_its_deadline(self):
        self.add()
        aid = self.call(door.ask, "demo", "送ってよい？", ["はい", "いいえ"], ttl_hours=24)["askId"]
        (row,), meta = self.rows(self.now + 10 * HOUR)                  # 3 時間の窓の外でも居る
        self.assertEqual((row["state"], row["ask"]["askId"], row["ask"]["min"], meta["asks"]), ("waiting", aid, 600, 1))
        self.assertEqual(row["verb"], "相談の答え待ち")
        self.assertEqual(self.rows(self.now + 24 * HOUR + 1)[0], [], "期限の切れた相談で居座った")
        # 期限切れの知らせは掃除（Bot が道具を呼んだとき）で 1 回だけ届く
        r = self.call(door.inbox, "demo", at=self.now + 24 * HOUR + 2)
        self.assertEqual([(i["kind"], i["askId"]) for i in r["items"]], [("expired", aid)])
        r = self.call(door.inbox, "demo", at=self.now + 24 * HOUR + 3)
        self.assertEqual(len(r["items"]), 1)
        self.assertIsNone(self.state()["ask"])
        self.assertEqual(self.state()["closed"][-1][2], "expired")
        self.assertEqual(self.call(door.withdraw, "demo", aid, at=self.now + 25 * HOUR)["already"], "expired")

    def test_sweep_all_expires_questions_of_bots_that_never_call(self):
        self.add()
        self.add("other", "ほか")
        aid = self.call(door.ask, "demo", "どうする？", ttl_hours=1)["askId"]
        self.assertEqual(door.sweep_all(self.home, self.now + 30 * 60), 0)
        self.assertEqual(door.sweep_all(self.home, self.now + HOUR + 1), 1)
        st = self.state()
        self.assertIsNone(st["ask"])
        self.assertEqual([(i["kind"], i["askId"]) for i in st["inbox"]], [("expired", aid)])
        self.assertEqual(door.sweep_all(self.home, self.now + HOUR + 2), 0, "掃除が毎回書く")
        self.assertIn(("expire", aid), [(a["ev"], a.get("askId")) for a in self.audit()])

    def test_unread_items_are_dropped_after_14_days(self):
        self.add()
        self.call(door.instruct, "demo", "古い指示", src="local")
        self.call(door.instruct, "demo", "新しい指示", src="local", at=self.now + 13 * 86400)
        r = self.call(door.inbox, "demo", at=self.now + 14 * 86400 + 1)
        self.assertEqual([i["text"] for i in r["items"]], ["新しい指示"])
        self.assertIn(("drop", "old"), [(a["ev"], a.get("why")) for a in self.audit()])


# ── 上限 ─────────────────────────────────────────────────
class LimitTest(DoorBase):
    def test_question_shape_limits(self):
        self.add()
        bad = [("x" * 201, None, 24), ("", None, 24), ("q", ["a"] * 1 + ["b", "c", "d", "e"], 24),
               ("q", ["y" * 41], 24), ("q", ["a", "a"], 24), ("q", "a,b", 24), ("q", [""], 24), ("q", [3], 24),
               ("q", None, 0), ("q", None, 169), ("q", None, "24"), ("q", None, True)]
        for q, opts, ttl in bad:
            self.assertEqual(self.call(door.ask, "demo", q, opts, ttl_hours=ttl).get("error"), "bad-args", (q[:5], opts, ttl))
        r = self.call(door.ask, "demo", "x" * 200, ["a" * 40, "b", "c", "d"], ttl_hours=168)
        self.assertTrue(r["ok"], r)
        self.assertEqual(self.state()["ask"]["options"], ["a" * 40, "b", "c", "d"])

    def test_report_limits(self):
        self.add()
        for args in (("x" * 81, ""), ("", ""), ("t", "y" * 601), ("t", "", "error"), ("t", 5)):
            self.assertEqual(self.call(door.report, "demo", *args).get("error"), "bad-args", args[:1])
        self.assertTrue(self.call(door.report, "demo", "t" * 80, "b" * 600, "warn")["ok"])
        self.assertEqual([(x["kind"], len(x["title"]), len(x["body"])) for x in self.log()], [("warn", 80, 600)])

    def test_one_open_question_per_bot(self):
        self.add()
        first = self.call(door.ask, "demo", "一つ目？")["askId"]
        r = self.call(door.ask, "demo", "二つ目？")
        self.assertEqual((r["ok"], r["error"], r["askId"]), (False, "ask-open", first))
        self.assertEqual(self.state()["ask"]["question"], "一つ目？")

    def test_questions_are_rate_limited_per_hour_and_day(self):
        self.add()
        t = self.now

        def ask_and_withdraw(at):
            r = self.call(door.ask, "demo", "どう？", at=at)
            if r["ok"]:
                self.assertTrue(self.call(door.withdraw, "demo", r["askId"], at=at)["ok"])
            return r

        for i in range(door.ASK_PER_HOUR):
            self.assertTrue(ask_and_withdraw(t + i)["ok"], i)
        self.assertEqual(ask_and_withdraw(t + 10)["error"], "rate-hour")
        day = [t + HOUR + 1 + 60 * i for i in range(door.ASK_PER_HOUR)]           # 2 時間目
        day += [t + 2 * HOUR + 2 + 60 * i for i in range(door.ASK_PER_HOUR)]      # 3 時間目
        day += [t + 3 * HOUR + 3 + 60 * i for i in range(door.ASK_PER_DAY - 3 * door.ASK_PER_HOUR)]
        for at in day:
            self.assertTrue(ask_and_withdraw(at)["ok"])
        self.assertEqual(ask_and_withdraw(t + 5 * HOUR)["error"], "rate-day")
        self.assertTrue(ask_and_withdraw(t + 24 * HOUR + 5 * 60)["ok"])          # 最初の 6 件が 24 時間の外へ

    def test_unread_cap_drops_the_oldest(self):
        self.add()
        for i in range(door.INBOX_MAX + 5):
            self.assertTrue(self.call(door.instruct, "demo", f"指示{i}", src="local", at=self.now + i)["ok"])
        items = self.call(door.inbox, "demo", at=self.now + 200)["items"]
        self.assertEqual(len(items), door.INBOX_MAX)
        self.assertEqual(items[0]["text"], "指示5")
        self.assertEqual(sum(a.get("n", 0) for a in self.audit() if a["ev"] == "drop" and a["why"] == "cap"), 5)


# ── 冪等 ─────────────────────────────────────────────────
class IdempotencyTest(DoorBase):
    def test_the_same_request_id_is_applied_once(self):
        self.add()
        r1 = self.call(door.report, "demo", "済み", req_id="job-1")
        r2 = self.call(door.report, "demo", "済み", req_id="job-1")
        self.assertTrue(r1["ok"] and r2["ok"] and r2["dup"])
        self.assertEqual(len([x for x in self.log() if x["ev"] == "report"]), 1)
        a1 = self.call(door.ask, "demo", "どう？", req_id="ask-1")
        a2 = self.call(door.ask, "demo", "どう？", req_id="ask-1")
        self.assertEqual(a1["askId"], a2["askId"])
        self.assertEqual(len(self.state()["asks"]), 1)
        self.assertTrue(self.call(door.withdraw, "demo", a1["askId"])["ok"])
        self.assertEqual(self.call(door.withdraw, "demo", a1["askId"])["already"], "withdrawn")
        self.assertEqual(self.call(door.report, "demo", "x", req_id="bad id!")["error"], "bad-args")

    def test_peek_does_not_write_and_ack_is_idempotent(self):
        self.add()
        self.call(door.instruct, "demo", "棚を見て", src="local")
        before = self.snapshot()
        items = self.call(door.inbox, "demo")["items"]
        self.assertEqual(self.snapshot(), before, "覗いただけで書いた")
        self.assertEqual([i["kind"] for i in items], ["instruct"])
        r = self.call(door.inbox, "demo", ack=[items[0]["id"], "f" * 16, "nonsense"])
        self.assertEqual((r["acked"], r["items"]), (1, []))
        r = self.call(door.inbox, "demo", ack=[items[0]["id"]])
        self.assertEqual((r["ok"], r["acked"]), (True, 0))
        for bad in ("abc", [1], {"x": 1}):
            self.assertEqual(self.call(door.inbox, "demo", ack=bad)["error"], "bad-args")


# ── 並行の flock ──────────────────────────────────────────
def _parallel_worker(home, idx, n, out):
    os.environ["OFFICE_HOME"] = home
    d = _load(f"office_door_w{idx}", SERVER / "office_door.py")
    ok = 0
    for i in range(n):
        ok += bool(d.instruct("demo", f"指示 {idx}-{i}", src="local", home=home)["ok"])
        ok += bool(d.report("demo", f"報告 {idx}-{i}", home=home)["ok"])
    out.put(ok)


def _ask_worker(home, idx, barrier, out):
    d = _load(f"office_door_a{idx}", SERVER / "office_door.py")
    barrier.wait()
    out.put(bool(d.ask("demo", f"相談 {idx}", home=home)["ok"]))


class ConcurrencyTest(DoorBase):
    WORKERS, EACH = 4, 10

    def test_parallel_writers_lose_nothing(self):
        self.add()
        ctx = mp.get_context("spawn")                                   # 別の interpreter＝実運用の daemon・CLI・MCP 相当
        out = ctx.Queue()
        procs = [ctx.Process(target=_parallel_worker, args=(str(self.home), k, self.EACH, out))
                 for k in range(self.WORKERS)]
        for p in procs:
            p.start()
        oks = [out.get(timeout=60) for _ in procs]
        for p in procs:
            p.join(60)
            self.assertEqual(p.exitcode, 0)
        self.assertEqual(sum(oks), self.WORKERS * self.EACH * 2)
        st = self.state()
        texts = sorted(i["text"] for i in st["inbox"])
        self.assertEqual(len(texts), self.WORKERS * self.EACH, "lost update（flock が効いていない）")
        self.assertEqual(len(set(i["id"] for i in st["inbox"])), len(texts))
        self.assertEqual(len([x for x in self.log() if x["ev"] == "report"]), self.WORKERS * self.EACH)

    def test_only_one_of_simultaneous_questions_opens(self):
        self.add()
        ctx = mp.get_context("spawn")
        n = 5
        barrier, out = ctx.Barrier(n), ctx.Queue()
        procs = [ctx.Process(target=_ask_worker, args=(str(self.home), k, barrier, out)) for k in range(n)]
        for p in procs:
            p.start()
        wins = [out.get(timeout=60) for _ in procs]
        for p in procs:
            p.join(60)
        self.assertEqual(sum(wins), 1, "開いている相談が 2 件になった（読み→書きが直列でない）")
        self.assertEqual(len(self.state()["asks"]), 1)


# ── 毒入りの行 ────────────────────────────────────────────
class PoisonTest(DoorBase):
    def _write(self, path, obj):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(obj if isinstance(obj, str) else json.dumps(obj), encoding="utf-8")

    def test_poisoned_ledger_rows_are_skipped_one_by_one(self):
        self.add("good", "正しい")
        self.call(door.checkin, "good", "仕事中")
        raw = json.loads(door.ledger_path(self.home).read_text(encoding="utf-8"))
        raw["bots"].update({"../evil": {"name": "x"}, "UPPER": {"name": "x"}, "ab": {"name": "x"},
                            "x" * 40: {"name": "x"}, "listy": [], "numb": 3, "nulled": None,
                            "longname": {"name": "名" * 300 + "\x1b[31m", "role": {"x": 1}},
                            "ctrlname": {"name": "<img src=x onerror=alert(1)>‮\n", "added": "nan"}})
        self._write(door.ledger_path(self.home), raw)
        rows, meta = self.rows()
        self.assertEqual(meta["bots"], 3)                              # good・longname・ctrlname だけが形の合う行
        self.assertEqual([r["session"] for r in rows], ["bot-good"])
        bots = {b["bot"]: b for b in door.list_bots(home=self.home, now=self.now)}
        self.assertEqual(set(bots), {"good", "longname", "ctrlname"})
        self.assertEqual(len(bots["longname"]["name"]), door.NAME_LEN)
        self.assertEqual(bots["longname"]["role"], "Bot")
        # 文字のまま（描く側が textContent）・見えない文字と改行を落として 24 字で切る
        self.assertEqual(bots["ctrlname"]["name"], "<img src=x onerror=alert(1)>"[:door.NAME_LEN])
        for bad in (["x"], "x", None, {"bots": []}, {"bots": "x"}):
            self._write(door.ledger_path(self.home), bad)
            self.assertEqual(self.rows(), ([], {"ok": False, "bots": 0, "n": 0, "asks": 0}), bad)

    def test_poisoned_state_values_are_dropped_and_the_tools_still_work(self):
        self.add()
        now = self.now
        self._write(door.state_path("demo", self.home), {
            "checkin": {"ts": "x", "doing": "y", "state": "working", "ttl": 15},
            "seen": float("nan") if False else "NaN",
            "ask": {"askId": "zz", "question": "q", "options": [], "ts": now, "deadline": now + 60},
            "inbox": [1, "x", None, {"kind": "answer", "id": "bad", "ts": now, "askId": "0" * 32, "text": "t"},
                      {"kind": "instruct", "id": "0" * 16, "ts": now, "text": "正しい指示"},
                      {"kind": "instruct", "id": "1" * 16, "ts": now, "text": 5},
                      {"kind": "hack", "id": "2" * 16, "ts": now},
                      {"kind": "answer", "id": "3" * 16, "ts": now, "askId": "a" * 32, "text": "ok",
                       "choice": {"index": 99, "label": "x"}, "extra": "<script>"}],
            "asks": "lots", "closed": [[1, 2, 3], ["0" * 32, now, "eaten"]], "applied": {"x": 1}})
        rows, _ = self.rows()
        self.assertEqual(rows, [], "毒の状態から行を作った")
        items = self.call(door.inbox, "demo")["items"]
        self.assertEqual([(i["kind"], i.get("text"), i.get("choice")) for i in items],
                         [("instruct", "正しい指示", None), ("answer", "ok", None)])
        self.assertNotIn("extra", json.dumps(items))
        self.assertTrue(self.call(door.checkin, "demo", "立て直し")["ok"])
        self.assertTrue(self.call(door.ask, "demo", "大丈夫？")["ok"])
        st = self.state()
        self.assertEqual((st["asks"][-1], st["applied"], st["closed"]), (self.now, [], []))

    def test_future_or_nonfinite_times_cannot_pin_a_bot(self):
        self.add()
        far = self.now + 10 * 365 * 86400
        self._write(door.state_path("demo", self.home), {
            "checkin": {"ts": far, "doing": "ずっと仕事中", "state": "working", "ttl": 240}, "seen": far,
            "ask": {"askId": "a" * 32, "question": "q", "options": [], "ts": self.now - 10, "deadline": far}})
        self.assertEqual(self.rows()[0], [], "未来の時刻・遠い期限で居座った")
        self._write(door.state_path("demo", self.home), '{"seen": 1e999, "checkin": {"ts": NaN}}')
        self.assertEqual(self.rows()[0], [])

    def test_broken_or_huge_state_starts_fresh_and_is_audited(self):
        self.add()
        self._write(door.state_path("demo", self.home), "{")
        self.assertTrue(self.call(door.checkin, "demo", "再開")["ok"])
        self.assertIn(("reset", "broken"), [(a["ev"], a.get("why")) for a in self.audit()])
        self._write(door.state_path("demo", self.home), '{"seen": 1, "pad": "' + "x" * (door.STATE_READ_MAX + 10) + '"}')
        self.assertEqual(self.rows()[0], [])
        self.assertTrue(self.call(door.checkin, "demo", "再開")["ok"])
        self.assertLess(door.state_path("demo", self.home).stat().st_size, 4096)

    def test_a_full_inbox_of_long_messages_stays_readable(self):
        """R126 の別モデルレビュー（medium）: 上限いっぱいの正しい未読で状態が読み取り上限を超え、次の操作が全部を空で上書きした
        （4000 字の日本語の指示 87 件で 1.05MB＞1MB）。上限いっぱい（INBOX_MAX 件 × 4000 字 × 4 バイトの字）でも読める。"""
        self.add()
        self.assertTrue(self.call(door.checkin, "demo", "見回り中")["ok"])
        text = "😀" * door.INSTRUCT_LEN                              # UTF-8 で 4 バイトの字を上限まで
        for i in range(door.INBOX_MAX):
            self.assertTrue(self.call(door.instruct, "demo", text, src="local")["ok"], i)
        size = door.state_path("demo", self.home).stat().st_size
        self.assertGreater(size, 1024 * 1024)                               # 直す前の読み取り上限（1MB）を超える大きさ
        self.assertLessEqual(size, door.STATE_WRITE_MAX)
        got = self.call(door.inbox, "demo")
        self.assertEqual(len(got["items"]), door.INBOX_MAX)                 # 未読は 1 件も消えない
        self.assertEqual(self.state()["checkin"]["doing"], "見回り中")        # 出勤も残る
        self.assertNotIn("reset", [a["ev"] for a in self.audit()])
        self.assertNotIn(("drop", "bytes"), [(a["ev"], a.get("why")) for a in self.audit()])

    def test_writes_never_exceed_what_reads_accept(self):
        """安全網: 書く側は読む側の上限より小さく収める（未読を古い順に捨てて監査）＝自分で書いた状態を「壊れている」と読まない。"""
        self.add()
        self.assertTrue(self.call(door.checkin, "demo", "見回り中")["ok"])
        with mock.patch.object(door, "STATE_WRITE_MAX", 40 * 1024):
            for i in range(12):
                self.assertTrue(self.call(door.instruct, "demo", f"{i:02d}" + "あ" * 3998, src="local")["ok"], i)
            self.assertLessEqual(door.state_path("demo", self.home).stat().st_size, 40 * 1024)
        items = self.call(door.inbox, "demo")["items"]
        self.assertTrue(0 < len(items) < 12, len(items))
        self.assertEqual(items[-1]["text"][:2], "11")                       # 新しい方が残る
        self.assertGreater(sum(a.get("n", 0) for a in self.audit() if a["ev"] == "drop" and a.get("why") == "bytes"), 0)
        self.assertEqual(self.state()["checkin"]["doing"], "見回り中")
        self.assertNotIn("reset", [a["ev"] for a in self.audit()])

    def test_bot_text_loses_control_and_direction_characters(self):
        self.add()
        self.call(door.checkin, "demo", "見回\x1b[31mり‮中\n次の行")
        self.call(door.ask, "demo", "送って​よい？\r\n", ["は\x00い", "👨‍💻"])
        (row,), _ = self.rows()
        self.assertEqual(row["question"], "送ってよい？")
        self.assertEqual(row["ask"]["options"], ["はい", "👨‍💻"])     # 絵文字の結合（ZWJ）は残す
        self.assertEqual(self.state()["checkin"]["doing"], "見回[31mり中 次の行")
        self.call(door.report, "demo", "題", "一行目\r\n\x07二行目\n\n\n\n三行目\t終")
        self.assertEqual(self.log()[-1]["body"], "一行目\n二行目\n\n三行目 終")

    def test_garbage_in_the_log_and_rotation_keep_appending(self):
        self.add()
        p = door.log_path("demo", self.home)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"\xff\xfe not json\n{\n")
        with mock.patch.object(door, "LOG_MAX", 2048):
            for i in range(80):
                self.assertTrue(self.call(door.report, "demo", f"報告 {i}")["ok"])
        data = p.read_bytes()
        self.assertLessEqual(len(data), 2048 + 400)
        last = data.decode("utf-8").splitlines()[-1]
        self.assertEqual(json.loads(last)["title"], "報告 79")
        self.assertTrue(data.decode("utf-8").splitlines()[0].startswith("{"), "回した後の先頭が行の途中")


# ── 選択肢の解決 4 型（Mac とスマホの文の型は実物から拾う）──────────────────
def _templates():
    """[(名前, label→文)]。ui/hud/strings.js の opt_text（ja・en＝Mac）と ui/pwa/app.js の T(...)（ja・en＝スマホ）。"""
    out = []
    hud = (ROOT / "ui" / "hud" / "strings.js").read_text(encoding="utf-8")
    found = re.findall(r"opt_text: \(label\) => `([^`]*)`", hud)
    assert len(found) == 2, f"ui/hud/strings.js の opt_text が ja/en の 2 つでない: {found}"
    for lang, tpl in zip(("Mac ja", "Mac en"), found):
        assert tpl.count("${label}") == 1, tpl
        out.append((lang, lambda label, tpl=tpl: tpl.replace("${label}", label)))
    pwa = (ROOT / "ui" / "pwa" / "app.js").read_text(encoding="utf-8")
    m = re.search(r'T\("([^"]*)"\+option\.raw\+"([^"]*)","([^"]*)"\+option\.raw\)', pwa)
    assert m, "ui/pwa/app.js の選択肢の答えの文（T(...+option.raw...)）が見つからない"
    ja_pre, ja_post, en_pre = m.groups()
    out.append(("スマホ ja", lambda label: ja_pre + label + ja_post))
    out.append(("スマホ en", lambda label: en_pre + label))
    return out


class ChoiceTest(DoorBase):
    LABELS = ["はい", "A「B」C", 'say "hi"', "Option: 2"]

    def test_the_four_sentence_forms_resolve_to_the_option(self):
        forms = _templates()
        self.assertEqual([f[0] for f in forms], ["Mac ja", "Mac en", "スマホ ja", "スマホ en"])
        for name, form in forms:
            for i, label in enumerate(self.LABELS):
                with self.subTest(form=name, label=label):
                    self.assertEqual(door.resolve_choice(form(label), self.LABELS), {"index": i, "label": label})
        self.assertEqual(door.resolve_choice("はい", self.LABELS), {"index": 0, "label": "はい"})
        self.assertIsNone(door.resolve_choice("はい、でも少し待って", self.LABELS))
        self.assertIsNone(door.resolve_choice(forms[0][1]("ちがう"), self.LABELS))
        self.assertIsNone(door.resolve_choice("はい", []))

    def test_answers_reach_the_inbox_with_the_resolved_choice(self):
        self.add()
        for name, form in _templates():
            with self.subTest(form=name):
                aid = self.call(door.ask, "demo", "どれ？", self.LABELS)["askId"]
                r = self.call(door.answer, "demo", aid, form("Option: 2"), src="local")
                self.assertTrue(r["ok"], r)
                item = self.call(door.inbox, "demo")["items"][-1]
                self.assertEqual((item["kind"], item["askId"], item["choice"]), ("answer", aid, {"index": 3, "label": "Option: 2"}))
                self.call(door.inbox, "demo", ack=[item["id"]])
                self.state()                                          # 相談は閉じている
                self.assertIsNone(self.state()["ask"])


# ── 人から Bot へ（指示・答え）───────────────────────────────
class HumanSideTest(DoorBase):
    def test_only_a_person_can_send_words(self):
        """人＝Mac の画面（local）とスマホ（relay・R127 から）。セッション（mcp）・名乗らない呼び手・Bot は断る。"""
        self.assertEqual(door.HUMAN_SRCS, ("local", "relay"))
        self.add()
        for src in ("mcp", None, "bot", "Relay", ""):
            self.assertEqual(self.call(door.instruct, "demo", "やって", src=src)["error"], "src", src)
            self.assertEqual(self.call(door.answer, "demo", "0" * 32, "はい", src=src)["error"], "src", src)
        self.assertFalse(door.state_path("demo", self.home).exists())
        self.assertTrue(self.call(door.instruct, "demo", "棚を見て", src="local")["ok"])
        item = self.call(door.inbox, "demo")["items"][0]
        self.assertEqual((item["kind"], item["text"]), ("instruct", "棚を見て"))
        self.assertEqual(self.log()[-1], {"ts": round(self.now, 3), "ev": "instruct", "src": "local", "text": "棚を見て"})
        self.assertTrue(self.call(door.instruct, "demo", "スマホから", src="relay")["ok"])
        self.assertEqual(self.call(door.inbox, "demo")["items"][-1]["text"], "スマホから")
        self.assertEqual(self.log()[-1]["src"], "relay")

    def test_req_id_applies_an_answer_or_an_instruction_once(self):
        """R127: スマホの封筒は relay_agent が端末と nonce から作る req_id で冪等（nonce の控えを失った再配達でも二重に入れない）。"""
        self.add()
        r1 = self.call(door.instruct, "demo", "棚を見て", src="relay", req_id="relay:" + "a" * 40)
        r2 = self.call(door.instruct, "demo", "棚を見て", src="relay", req_id="relay:" + "a" * 40)
        self.assertTrue(r1["ok"] and r2["ok"] and r2.get("dup"), (r1, r2))
        self.assertEqual(len(self.call(door.inbox, "demo")["items"]), 1, "同じ封筒の指示が二重に入った")
        aid = self.call(door.ask, "demo", "送ってよい？", ["はい", "いいえ"])["askId"]
        a1 = self.call(door.answer, "demo", aid[:8], "選択肢「はい」でお願いします。", src="relay", req_id="relay:" + "b" * 40)
        a2 = self.call(door.answer, "demo", aid[:8], "選択肢「はい」でお願いします。", src="relay", req_id="relay:" + "b" * 40)
        self.assertTrue(a1["ok"] and a2["ok"] and a2.get("dup"), (a1, a2))
        answers = [it for it in self.call(door.inbox, "demo")["items"] if it["kind"] == "answer"]
        self.assertEqual([(it["askId"], it["choice"]) for it in answers], [(aid, {"index": 0, "label": "はい"})])
        self.assertNotIn(("discard", "closed"), [(a["ev"], a.get("why")) for a in self.audit()],
                         "同じ封筒の 2 回目を「閉じた相談への答え」として捨てた（冪等になっていない）")
        self.assertEqual(self.call(door.answer, "demo", aid, "x", src="relay", req_id="bad id!")["error"], "bad-args")

    def test_closed_how_tells_who_closed_the_question_and_only_reads(self):
        """R127: daemon の _attn_track が「人が答えた相談」だけを数えるために読む（期限切れ・取り下げは answered でない）。"""
        self.add()
        aid = self.call(door.ask, "demo", "一つ目", ttl_hours=1)["askId"]
        self.assertIsNone(door.closed_how("demo", aid, home=self.home, now=self.now))       # 開いている
        self.call(door.answer, "demo", aid, "はい", src="local")
        self.assertEqual(door.closed_how("demo", aid, home=self.home, now=self.now), "answered")
        self.assertEqual(door.closed_how("demo", aid[:8], home=self.home, now=self.now), "answered")
        aid2 = self.call(door.ask, "demo", "二つ目")["askId"]
        self.call(door.withdraw, "demo", aid2)
        self.assertEqual(door.closed_how("demo", aid2, home=self.home, now=self.now), "withdrawn")
        aid3 = self.call(door.ask, "demo", "三つ目", ttl_hours=1)["askId"]
        self.call(door.inbox, "demo", at=self.now + 2 * HOUR)                                 # 掃除で期限切れ
        self.assertEqual(door.closed_how("demo", aid3, home=self.home, now=self.now + 2 * HOUR), "expired")
        before = self.snapshot()
        for bot_id, ref in (("demo", "f" * 32), ("demo", "xyz"), ("Demo", aid), ("ghost", aid), ("demo", None)):
            self.assertIsNone(door.closed_how(bot_id, ref, home=self.home, now=self.now), (bot_id, ref))
        self.assertEqual(self.snapshot(), before, "閉じ方を読むだけで口のファイルを書いた")

    def test_instructions_wait_until_the_question_closes(self):
        self.add()
        aid = self.call(door.ask, "demo", "送ってよい？")["askId"]
        self.assertEqual(self.call(door.instruct, "demo", "やって", src="local")["error"], "ask-open")
        self.assertEqual(self.call(door.inbox, "demo")["items"], [])
        self.call(door.withdraw, "demo", aid)
        self.assertTrue(self.call(door.instruct, "demo", "やって", src="local")["ok"])

    def test_an_answer_closes_the_question(self):
        self.add()
        aid = self.call(door.ask, "demo", "送ってよい？", ["はい", "いいえ"])["askId"]
        r = self.call(door.answer, "demo", aid[:8], "いいえ、明日にして", src="local")   # id8 でも 1 件を指す
        self.assertTrue(r["ok"])
        self.assertIsNone(r["choice"])
        self.assertIsNone(self.state()["ask"])
        self.assertEqual(self.rows()[0][0]["state"], "resting")
        item = self.call(door.inbox, "demo")["items"][0]
        self.assertEqual((item["kind"], item["text"], "choice" in item), ("answer", "いいえ、明日にして", False))

    def test_an_answer_to_a_question_not_shown_is_dropped_and_audited(self):
        self.add()
        aid = self.call(door.ask, "demo", "送ってよい？")["askId"]
        other = "f" * 32 if aid != "f" * 32 else "e" * 32
        r = self.call(door.answer, "demo", other, "はい", src="local")
        self.assertEqual(r["error"], "no-ask")
        self.assertEqual(self.state()["ask"]["askId"], aid, "合わない答えで相談が閉じた")
        self.assertEqual(self.call(door.inbox, "demo")["items"], [], "合わない答えが届いた（指示に化けた）")
        self.assertIn(("discard", "nomatch"), [(a["ev"], a.get("why")) for a in self.audit()])
        self.assertTrue(self.call(door.answer, "demo", aid, "はい", src="local")["ok"])
        r = self.call(door.answer, "demo", aid, "やっぱりいいえ", src="local")      # 2 回目＝もう閉じている
        self.assertEqual(r["error"], "no-ask")
        self.assertIn(("discard", "closed"), [(a["ev"], a.get("why")) for a in self.audit()])
        self.assertEqual(len(self.call(door.inbox, "demo")["items"]), 1)
        self.assertEqual(self.call(door.answer, "demo", "xyz", "はい", src="local")["error"], "bad-args")

    def test_withdraw(self):
        self.add()
        aid = self.call(door.ask, "demo", "送ってよい？")["askId"]
        self.assertEqual(self.call(door.withdraw, "demo", "a" * 31)["error"], "bad-args")
        self.assertEqual(self.call(door.withdraw, "demo", "b" * 32)["error"], "no-ask")
        self.assertTrue(self.call(door.withdraw, "demo", aid)["ok"])
        self.assertIsNone(self.state()["ask"])
        self.assertEqual(self.call(door.inbox, "demo")["items"], [], "取り下げで知らせが届いた")
        self.assertEqual(self.call(door.answer, "demo", aid, "はい", src="local")["error"], "no-ask")


# ── 行の必須キー ──────────────────────────────────────────
class RowTest(DoorBase):
    def test_the_row_has_the_required_keys_and_no_body_or_path(self):
        self.add("demo", "見回り", role="受信箱")
        self.call(door.checkin, "demo", "受信箱を見回り中")
        aid = self.call(door.ask, "demo", "送ってよい？", ["はい", "いいえ"], at=self.now + 90)["askId"]
        (row,), meta = self.rows(self.now + 200)
        self.assertEqual(meta, {"ok": True, "bots": 1, "n": 1, "asks": 1})
        self.assertEqual((row["session"], row["vendor"], row["dept"], row["disp"], row["role"]),
                         ("bot-demo", "bot", "見回り", "見回り", "受信箱"))
        self.assertEqual((row["state"], row["kind"], row["listening"], row["pending"]), ("waiting", "said", True, False))
        self.assertIs(row["muted"], False)                              # R128 #4: 消音（真偽の鍵 1 つ・既定は鳴らす）
        self.assertEqual((row["age"], row["mtime"]), (110, self.now + 90))
        for k in ("cwd", "branch", "lastSaid", "lastOrder", "target"):
            self.assertEqual(row[k], "", k)
        self.assertEqual(row["feed"], [])
        self.assertNotIn("external", row)
        self.assertEqual(row["question"], "送ってよい？")
        self.assertEqual(row["questionOptions"], [{"label": "はい", "desc": ""}, {"label": "いいえ", "desc": ""}])
        self.assertEqual(row["ask"], {"tool": "office_ask", "kind": "question", "title": "送ってよい？",
                                      "options": ["はい", "いいえ"], "askId": aid, "ts": self.now + 90,
                                      "min": 1, "remote": 0})
        self.assertRegex(row["ask"]["askId"], r"^[0-9a-f]{32}$")
        for k in ("age", "minions", "approvalMin", "avatar"):
            self.assertIs(type(row[k]), int, k)
        self.assertNotIn("受信箱を見回り中", json.dumps(row, ensure_ascii=False), "相談中に doing を出した")

    def test_muted_follows_the_ledger_and_the_row_stays(self):
        """R128 #4: 消音は台帳の muted を写すだけ（鳴らさないだけ＝行・状態・相談はそのまま）。真偽の True だけを消音と読む。"""
        self.add()
        self.call(door.checkin, "demo", "見回り中")
        door.set_muted("demo", True, home=self.home, now=self.now)
        (row,), _ = self.rows()
        self.assertIs(row["muted"], True)
        self.assertEqual((row["state"], row["verb"]), ("working", "見回り中"))
        door.set_muted("demo", False, home=self.home, now=self.now)
        self.assertIs(self.rows()[0][0]["muted"], False)

    def test_pending_follows_the_unread_items(self):
        self.add()
        self.call(door.checkin, "demo", "見回り中")
        self.assertFalse(self.rows()[0][0]["pending"])
        self.call(door.instruct, "demo", "棚を見て", src="local")
        self.assertTrue(self.rows()[0][0]["pending"])
        item = self.call(door.inbox, "demo")["items"][0]
        self.call(door.inbox, "demo", ack=[item["id"]])
        self.assertFalse(self.rows()[0][0]["pending"])

    def test_same_names_are_numbered_by_id_and_never_merged(self):
        for bot_id in ("zeta", "alfa", "mike"):
            self.add(bot_id, "見回り")
            self.call(door.checkin, bot_id, "見回り中")
        rows, _ = self.rows()
        self.assertEqual([(r["session"], r["disp"]) for r in rows],
                         [("bot-alfa", "見回り"), ("bot-mike", "見回り 2号"), ("bot-zeta", "見回り 3号")])
        self.assertEqual([r["disp"] for r in self.rows(lang="en")[0]], ["見回り", "見回り #2", "見回り #3"])

    def test_a_bot_that_never_worked_is_not_shown(self):
        self.add()
        self.assertEqual(self.rows(), ([], {"ok": True, "bots": 1, "n": 0, "asks": 0}))

    def test_reading_rows_never_writes(self):
        self.add()
        self.call(door.ask, "demo", "どう？", ttl_hours=1)
        self.call(door.instruct, "demo", "x", src="local", at=self.now - 20 * 86400 + 30)
        before = self.snapshot()
        self.rows(self.now + 2 * HOUR)                                  # 期限切れ・古い未読があっても書かない
        door.list_bots(home=self.home, now=self.now + 30 * 86400)
        self.assertEqual(self.snapshot(), before)


class ConstantsTest(unittest.TestCase):
    def test_question_limits_are_shared_through_office_common(self):
        common = door.office_common
        self.assertEqual((common.ASK_Q_MAX, common.ASK_Q_LEN, common.ASK_OPT_MAX, common.ASK_OPT_LEN), (8, 200, 4, 40))
        self.assertEqual((door.ASK_Q_LEN, door.ASK_OPT_MAX, door.ASK_OPT_LEN), (200, 4, 40))
        src = (SERVER / "office_server.py").read_text(encoding="utf-8")
        self.assertRegex(src, r"ASK_Q_MAX, ASK_Q_LEN, ASK_OPT_MAX, ASK_OPT_LEN = \(office_common\.ASK_Q_MAX")
        self.assertNotIn("import office_server", (SERVER / "office_door.py").read_text(encoding="utf-8"))


# ── office_server との合流と守る線 ───────────────────────────
class OfficeGuardTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.home = Path(tempfile.mkdtemp(prefix="door_office_"))
        (cls.home / ".claude" / "office_inbox").mkdir(parents=True)
        (cls.home / "office_config.json").write_text('{"projects": {}}\n', encoding="utf-8")
        cls.env = mock.patch.dict(os.environ, {"OFFICE_HOME": str(cls.home), "OFFICE_DATA": str(cls.home),
                                               "OFFICE_CONFIG": str(cls.home / "office_config.json"),
                                               "OFFICE_LANG": "ja", "OFFICE_SOURCES_CODEX": "0",
                                               "OFFICE_AGENTS_CLI": "0"})
        cls.env.start()
        cls.office = _load("office_server_door_t", SERVER / "office_server.py")
        cls.door = cls.office.office_door
        assert cls.door.add_bot("demo", "見回り", home=cls.home)["ok"]

    @classmethod
    def tearDownClass(cls):
        cls.env.stop()
        shutil.rmtree(cls.home, ignore_errors=True)

    def tearDown(self):
        for p in (self.door.state_path("demo", self.home), self.door.log_path("demo", self.home)):
            p.unlink(missing_ok=True)

    def test_write_approval_reply_refuses_bots(self):
        for behavior in ("allow", "deny"):
            for ask_id in ("0" * 32, None):
                with self.assertRaises(ValueError):
                    self.office.write_approval_reply("bot-demo", behavior, "はい", src="local", ask_id=ask_id)
        self.assertEqual(list(self.office.APPROVALS.glob("bot-*")) if self.office.APPROVALS.exists() else [], [])

    def test_a_forged_posting_for_a_bot_is_not_read(self):
        """同じユーザーの別プロセスが bot- の掲示を置いても、❗ にも許可の材料にもならない（Bot は掲示板を使わない）。"""
        aid = "c" * 32
        self.office.APPROVALS.mkdir(parents=True, exist_ok=True)
        posting = self.office.APPROVALS / f"bot-demo.{aid}.json"
        posting.write_text(json.dumps({"session": "bot-demo", "askId": aid, "tool": "Bash", "kind": "permission",
                                       "title": "rm -rf ~", "ts": time.time(), "deadline": time.time() + 600,
                                       "level": "remote", "inputCanon": "x"}), encoding="utf-8")
        self.addCleanup(posting.unlink)
        self.assertEqual(self.office.pending_approvals("bot-demo"), [])
        self.assertIsNone(self.office.pending_approval("bot-demo"))
        self.assertIsNone(self.office._ask_raw("bot-demo", aid))

    def test_post_instruction_to_a_bot_needs_a_person_and_goes_to_the_door(self):
        o = self.office
        with self.assertRaises(TypeError):
            o.post_instruction("bot-demo", "x")                              # 呼び手を言わない経路は動かない
        with self.assertRaises(ValueError):
            o.post_instruction("bot-demo", "x", src="someone")
        ok, msg = o.post_instruction("bot-demo", "セッションの文", src="mcp")
        self.assertFalse(ok)
        self.assertIn("Bot", msg)
        self.assertEqual(self.door.inbox("demo", home=self.home)["items"], [])
        ok, msg = o.post_instruction("bot-demo", "  棚を見て  ", src="local")
        self.assertTrue(ok, msg)
        ok, msg = o.post_instruction("bot-demo", "スマホから", src="relay", req_id="relay:" + "c" * 40)   # R127: スマホも人
        self.assertTrue(ok, msg)
        ok, msg = o.post_instruction("bot-demo", "スマホから", src="relay", req_id="relay:" + "c" * 40)   # 同じ封筒の再配達
        self.assertTrue(ok, msg)
        items = self.door.inbox("demo", home=self.home)["items"]
        self.assertEqual([(i["kind"], i["text"]) for i in items], [("instruct", "棚を見て"), ("instruct", "スマホから")])
        stored = json.loads(self.door.state_path("demo", self.home).read_text(encoding="utf-8"))["inbox"]
        self.assertEqual([i.get("src") for i in stored], ["local", "relay"])          # 呼び手は状態に残る（Bot には見せない）
        self.assertEqual([h["text"] for h in o.load_history()[-2:]], ["棚を見て", "スマホから"],
                         "同じ封筒の再配達で送信履歴が二重になった")
        self.assertFalse((o.INBOX / "bot-demo.json").exists(), "Bot 宛が Claude の受信箱へ書かれた")
        self.assertEqual(o.load_history()[-1]["session"], "bot-demo")
        ok, _ = o.post_instruction("bot-ghost", "x", src="local")
        self.assertFalse(ok)                                                 # 登録なし
        self.door.ask("demo", "送ってよい？", home=self.home)
        ok, msg = o.post_instruction("bot-demo", "やって", src="local")
        self.assertFalse(ok)                                                 # 相談が開いている間は指示にしない
        self.assertIn("相談", msg)

    def test_scan_merges_bots_once_and_keeps_them_out_of_the_timeline(self):
        o = self.office
        self.door.checkin("demo", "受信箱を見回り中", home=self.home)
        recorded = []
        with mock.patch.object(o.office_timeline, "record_states", lambda rows, now: recorded.extend(rows)):
            data = o.scan_office()
        bots = [e for e in data["employees"] if e["vendor"] == "bot"]
        self.assertEqual([e["session"] for e in bots], ["bot-demo"])
        self.assertEqual(data["sources"]["door"], {"ok": True, "bots": 1, "n": 1, "asks": 0})
        self.assertEqual([r["session"] for r in data["roster"] if r["vendor"] == "bot"], ["bot-demo"])
        self.assertFalse(any(r.get("vendor") == "bot" for r in recorded), "Bot の稼働をセッションの稼働に混ぜた")

    def test_bots_are_never_merged_in_project_mode(self):
        o = self.office
        rows = [{"session": "bot-aaaa", "vendor": "bot", "dept": "見回り", "cwd": "", "state": "working"},
                {"session": "bot-bbbb", "vendor": "bot", "dept": "見回り", "cwd": "", "state": "resting"},
                {"session": "sess-0001", "vendor": "claude", "dept": "見回り", "cwd": "", "state": "working"}]
        roster = o.group_by_project(rows, mode="project")
        self.assertEqual(sorted(r["session"] for r in roster), ["bot-aaaa", "bot-bbbb", "sess-0001"])
        self.assertEqual(len({r["projectId"] for r in roster}), 3)
        self.assertEqual({r["projectId"] for r in roster if r["vendor"] == "bot"},
                         {r["projectId"] for r in o.group_by_project(rows[:2], mode="session")})

    def test_bot_questions_are_not_counted_as_answered(self):
        """askId の無い Bot の行（手組み・壊れた形）は控えない＝人が答えたかを口に確かめられないものは数えない。"""
        o = self.office
        bot = {"projectId": "p-bot", "session": "bot-demo", "vendor": "bot", "question": "送ってよい？"}
        seen, resolved = o._attn_track({}, [bot], 100.0)
        self.assertEqual((seen, resolved), ({}, []))
        seen, resolved = o._attn_track(seen, [], 900.0)                      # 相談が消えても「答えた」にならない
        self.assertEqual(resolved, [])
        claude = {"projectId": "p-1", "session": "s1", "vendor": "claude", "question": "どっち？"}
        seen, _ = o._attn_track({}, [claude], 100.0)
        self.assertEqual(o._attn_track(seen, [], 160.0)[1], [60.0])          # セッションは今までどおり数える

    # ── R127: 相談の往復（Mac の答え・知らせの鍵と数字）──────────────────
    def _roster(self, now):
        rows, _meta = self.door.door_employees(self.home, now)
        return self.office.group_by_project(rows, mode="session")

    def _audit(self):
        p = self.door.audit_path(self.home)
        return [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines()] if p.exists() else []

    def test_answer_bot_takes_words_for_the_shown_question_only_and_never_allow(self):
        """/api/approval/reply の bot- の枝（answer_bot）: allow と言葉以外は断る（監査に refuse）・askId（32 桁）で 1 件に縛る・
        表示していない相談・閉じた相談への答えは口が捨てて監査（指示に落とさない）・掲示板は書かない。"""
        o, d = self.office, self.door
        aid = d.ask("demo", "送ってよい？", ["はい", "いいえ"], home=self.home)["askId"]
        for behavior in ("allow", "ask", True, "ALLOW"):
            ok, msg, extra = o.answer_bot("bot-demo", {"behavior": behavior, "askId": aid, "message": "はい"})
            self.assertEqual((ok, extra), (False, {}), behavior)
        self.assertIn(("refuse", "answer", "allow", "local"),
                      [(a["ev"], a.get("op"), a.get("why"), a.get("src")) for a in self._audit()])
        self.assertIsNone(d.closed_how("demo", aid, home=self.home), "allow で相談が閉じた")
        for bad in ({}, {"askId": aid[:8]}, {"askId": aid.upper()}, {"askId": 123}):
            ok, msg, _ = o.answer_bot("bot-demo", dict(bad, message="はい"))
            self.assertFalse(ok, bad)
            self.assertIn("読み込み直", msg)
        other = "f" * 32 if aid != "f" * 32 else "e" * 32
        ok, msg, _ = o.answer_bot("bot-demo", {"behavior": "deny", "askId": other, "message": "はい"})
        self.assertFalse(ok)
        self.assertIn(("discard", "nomatch", "local"), [(a["ev"], a.get("why"), a.get("src")) for a in self._audit()])
        self.assertEqual(d.inbox("demo", home=self.home)["items"], [], "表示していない相談への答えが届いた")
        ok, msg, extra = o.answer_bot("bot-demo", {"behavior": "deny", "askId": aid,
                                                   "message": "選択肢「いいえ」でお願いします。"})
        self.assertTrue(ok, msg)
        self.assertEqual(extra, {"kind": "question", "askId": aid, "choice": {"index": 1, "label": "いいえ"}})
        ok, msg, _ = o.answer_bot("bot-demo", {"askId": aid, "message": "やっぱりはい"})        # もう閉じた
        self.assertFalse(ok)
        self.assertIn(("discard", "closed"), [(a["ev"], a.get("why")) for a in self._audit()])
        items = d.inbox("demo", home=self.home)["items"]
        self.assertEqual([(i["kind"], i["text"], i.get("choice")) for i in items],
                         [("answer", "選択肢「いいえ」でお願いします。", {"index": 1, "label": "いいえ"})])
        stored = json.loads(d.state_path("demo", self.home).read_text(encoding="utf-8"))["inbox"]
        self.assertEqual([i.get("src") for i in stored], ["local"])
        self.assertFalse(o.answer_bot("bot-Bad", {"askId": aid, "message": "x"})[0])
        self.assertEqual(list(o.APPROVALS.glob("bot-*")) if o.APPROVALS.exists() else [], [], "Bot の答えが掲示板へ書かれた")

    def test_people_answers_are_counted_but_expired_or_withdrawn_questions_are_not(self):
        """_attn_track: Bot の相談は askId つきで控え、消えたときは口が answered と言う分だけ「答えた ❗」に数える。"""
        o, d = self.office, self.door
        t0 = time.time()
        aid = d.ask("demo", "一つ目", home=self.home, now=t0)["askId"]
        seen, resolved = o._attn_track({}, self._roster(t0), t0)
        self.assertEqual(resolved, [])
        (key, val), = seen.items()
        self.assertTrue(key.endswith("#" + aid), key)
        self.assertEqual((val["session"], val["askId"]), ("bot-demo", aid))
        d.answer("demo", aid, "はい", src="local", home=self.home, now=t0 + 30)
        self.assertEqual(o._attn_track(seen, self._roster(t0 + 60), t0 + 60), ({}, [60.0]), "人が答えた相談を数えない")
        aid2 = d.ask("demo", "二つ目", home=self.home, now=t0 + 100)["askId"]
        seen, _ = o._attn_track({}, self._roster(t0 + 100), t0 + 100)
        d.withdraw("demo", aid2, home=self.home, now=t0 + 130)
        self.assertEqual(o._attn_track(seen, self._roster(t0 + 160), t0 + 160), ({}, []), "取り下げを「答えた」に数えた")
        d.ask("demo", "三つ目", ttl_hours=1, home=self.home, now=t0 + 200)
        seen, _ = o._attn_track({}, self._roster(t0 + 200), t0 + 200)
        self.assertEqual(len(seen), 1)
        late = t0 + 200 + 3700                                               # 期限切れ（掃除の前でも画面から消える）
        self.assertEqual(o._attn_track(seen, self._roster(late), late), ({}, []), "期限切れを「答えた」に数えた")

    def test_a_new_question_right_after_an_answer_rings_and_counts_again(self):
        """1 体 1 件＝鍵が projectId だけだと、答えた直後（同じ 60 秒の間）の次の相談は鳴らず、答えた数にも入らなかった。"""
        o, d = self.office, self.door
        t0 = time.time()
        aid = d.ask("demo", "一つ目", home=self.home, now=t0)["askId"]
        roster = self._roster(t0)
        seen, _ = o._attn_track({}, roster, t0)
        new, prev = o.attention_diff(set(), roster)
        self.assertEqual([n["question"] for n in new], ["一つ目"])
        d.answer("demo", aid, "はい", src="local", home=self.home, now=t0 + 10)
        aid2 = d.ask("demo", "二つ目", home=self.home, now=t0 + 20)["askId"]
        roster = self._roster(t0 + 60)
        new, prev = o.attention_diff(prev, roster)
        self.assertEqual([n["question"] for n in new], ["二つ目"], "答えた直後の次の相談で鳴らない")
        seen, resolved = o._attn_track(seen, roster, t0 + 60)
        self.assertEqual(resolved, [60.0], "答えた 1 件目を数えない")
        self.assertEqual([v["askId"] for v in seen.values()], [aid2])
        self.assertEqual(o.attention_diff(prev, roster)[0], [], "同じ相談で 2 度鳴った")
        # セッションの鍵は今までどおり projectId（askId を足さない＝続けて出る許可の掲示のたびに鳴らさない）
        claude = {"projectId": "p-1", "session": "s1", "vendor": "claude", "question": "どっち？",
                  "ask": {"askId": "a" * 32, "kind": "question"}}
        _, ids = o.attention_diff(set(), [claude])
        self.assertEqual(ids, {"p-1"})
        self.assertEqual(o.attention_diff(ids, [dict(claude, ask={"askId": "b" * 32, "kind": "question"})])[0], [])


# ── CLI ──────────────────────────────────────────────────
class CliTest(DoorBase):
    def run_cli(self, *args):
        env = {k: v for k, v in os.environ.items() if not k.startswith("OFFICE_")}
        env.update(OFFICE_HOME=str(self.home), OFFICE_LANG="ja")
        p = subprocess.run([sys.executable, str(SERVER / "office_door.py"), *args], env=env,
                           capture_output=True, text=True, timeout=30)
        lines = p.stdout.splitlines()
        return p.returncode, (json.loads(lines[0]) if lines else None), lines, p.stderr

    def test_round_trip(self):
        rc, out, lines, _ = self.run_cli("add", "demo", "--name", "見回り")
        self.assertEqual((rc, out["ok"], out["session"], len(lines)), (0, True, "bot-demo", 1))
        rc, out, _, _ = self.run_cli("checkin", "--bot", "demo", "--doing", "受信箱を見回り中", "--ttl-min", "30")
        self.assertEqual((rc, out["ok"]), (0, True))
        rc, out, _, _ = self.run_cli("report", "--bot", "demo", "--title", "見回り完了", "--body", "3 件", "--kind", "info")
        self.assertEqual(rc, 0)
        rc, out, _, _ = self.run_cli("ask", "--bot", "demo", "--question", "送ってよい？", "--option", "はい",
                                     "--option", "いいえ", "--ttl-hours", "2")
        self.assertEqual(rc, 0)
        aid = out["askId"]
        rc, out, _, _ = self.run_cli("ask", "--bot", "demo", "--question", "二つ目")
        self.assertEqual((rc, out["error"]), (1, "ask-open"))
        rc, out, _, _ = self.run_cli("inbox", "--bot", "demo")
        self.assertEqual((rc, out["items"]), (0, []))
        rc, out, _, _ = self.run_cli("list")
        self.assertEqual([(b["bot"], b["ask"]) for b in out["bots"]], [("demo", True)])
        rc, out, _, _ = self.run_cli("withdraw", "--bot", "demo", "--ask-id", aid)
        self.assertEqual((rc, out["ok"]), (0, True))
        rc, out, _, _ = self.run_cli("inbox", "--bot", "demo", "--ack", "a" * 16, "b" * 16)
        self.assertEqual((rc, out["acked"]), (0, 0))
        rc, out, _, _ = self.run_cli("remove", "demo")
        self.assertEqual(rc, 0)
        rc, out, _, _ = self.run_cli("checkin", "--bot", "demo", "--doing", "x")
        self.assertEqual((rc, out["error"]), (1, "unregistered"))
        rc, out, lines, err = self.run_cli("checkin", "--doing", "x")             # --bot が無い＝使い方の誤り
        self.assertEqual((rc, lines), (2, []))
        self.assertIn("--bot", err)
        self.assertEqual(json.loads(json.dumps(self.audit()))[-1]["ev"], "refuse")


if __name__ == "__main__":
    unittest.main()
