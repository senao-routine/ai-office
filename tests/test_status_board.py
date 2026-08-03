# -*- coding: utf-8 -*-
"""リソースモニターの fixture-first 回帰テスト。"""
import importlib.util
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from datetime import datetime, timedelta, timezone
from itertools import count
from pathlib import Path

TESTS = Path(__file__).resolve().parent
ROOT = TESTS.parent
FIXTURES = TESTS / "fixtures"
JST = timezone(timedelta(hours=9))
NOW = datetime(2026, 7, 14, 13, 0, tzinfo=JST).timestamp()
_LOAD_SEQ = count()
_MISSING = object()


def _exec_module(path, prefix):
    name = f"{prefix}_{next(_LOAD_SEQ)}"
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_status(home):
    old = os.environ.get("OFFICE_HOME", _MISSING)
    os.environ["OFFICE_HOME"] = str(home)
    try:
        return _exec_module(ROOT / "server" / "status_board.py", "status_board_t")
    finally:
        if old is _MISSING:
            os.environ.pop("OFFICE_HOME", None)
        else:
            os.environ["OFFICE_HOME"] = old


def _usage_line(timestamp, request_id="req-synthetic-appended",
                message_id="msg-synthetic-appended", model="claude-opus-4-8"):
    return json.dumps({
        "type": "assistant",
        "requestId": request_id,
        "timestamp": timestamp,
        "message": {
            "id": message_id,
            "role": "assistant",
            "model": model,
            "usage": {
                "input_tokens": 1,
                "cache_creation_input_tokens": 2,
                "cache_read_input_tokens": 3,
                "cache_creation": {
                    "ephemeral_5m_input_tokens": 1,
                    "ephemeral_1h_input_tokens": 1,
                },
                "output_tokens": 4,
                "service_tier": "standard",
            },
        },
    }, ensure_ascii=False, separators=(",", ":"))


class StatusBoardBase(unittest.TestCase):
    def setUp(self):
        self.home = Path(tempfile.mkdtemp(prefix="status_board_home_"))
        self.sb = _load_status(self.home)

    def tearDown(self):
        shutil.rmtree(self.home, ignore_errors=True)

    def _copy(self, fixture, target, mtime=NOW - 30):
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(FIXTURES / fixture, target)
        os.utime(target, (mtime, mtime))
        return target

    def _seed_claude(self):
        project = self.home / ".claude" / "projects" / "synthetic-project"
        main = self._copy("usage_transcript.jsonl", project / "session-synthetic.jsonl")
        sub = self._copy(
            "usage_subagent.jsonl",
            project / "session-synthetic" / "subagents" / "agent-synthetic.jsonl",
        )
        return main, sub

    def _seed_codex(self, fixture="codex_rollout.jsonl", name="rollout-synthetic.jsonl",
                    mtime=NOW - 30):
        return self._copy(
            fixture,
            self.home / ".codex" / "sessions" / "2026" / "07" / "14" / name,
            mtime=mtime,
        )

    def _seed_gemini(self, expiry_ms=None, refresh_token="synthetic-refresh-token",
                     mtime=NOW - 30):
        if expiry_ms is None:
            expiry_ms = int((NOW + 3600) * 1000)
        path = self.home / ".gemini" / "oauth_creds.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        data = {"expiry_date": expiry_ms}
        if refresh_token is not None:
            data["refresh_token"] = refresh_token
        path.write_text(json.dumps(data), encoding="utf-8")
        os.utime(path, (mtime, mtime))
        return path

    def _write_ledger(self, entries):
        path = self.home / ".claude" / "office_resources.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"version": 1, "entries": entries}, ensure_ascii=False),
                        encoding="utf-8")
        return path

    def _write_resources(self, entries=None, spend=None):
        path = self.home / ".claude" / "office_resources.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        data = {"version": 1, "entries": list(entries or [])}
        if spend is not None:
            data["spend"] = list(spend)
        path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        return path

    @staticmethod
    def _entry(entry_id="manual-one", label="Synthetic API", remaining=75, total=100,
               updated_at=NOW, plan="metered"):
        return {
            "id": entry_id,
            "label": label,
            "plan": plan,
            "remaining": remaining,
            "total": total,
            "unit": "credits",
            "note": "synthetic ledger entry",
            "updatedAt": updated_at,
        }


class ClaudeCollectorTest(StatusBoardBase):
    def test_totals_models_last5h_sessions_duplicate_broken_and_subagent(self):
        _main, _sub = self._seed_claude()

        provider = self.sb.collect_claude(NOW)

        self.assertEqual(provider["status"], "ok")
        # fixture 4行 + subagent 1行。完全重複1行・壊れJSON・usage無しは数えない。
        self.assertEqual(provider["tokens"]["today"], {
            "input": 60, "output": 210, "cacheRead": 160,
            "cacheCreate": 110, "total": 540,
        })
        # 06:00 は now=13:00 の直近5時間外。
        self.assertEqual(provider["tokens"]["last5h"], {
            "input": 50, "output": 170, "cacheRead": 130,
            "cacheCreate": 90, "total": 440,
        })
        self.assertEqual(provider["tokens"]["byModel"], {
            "claude-opus-4-8": {"input": 195, "output": 125, "total": 320,
                                 "usd": 0.01126125},
            "claude-sonnet-5": {"input": 135, "output": 85, "total": 220,
                                 "usd": 0.0015382500000000001},
        })
        self.assertEqual(provider["sessionsToday"], 1)
        self.assertIsInstance(provider["sourceMtime"], float)
        self.assertIsNone(provider["error"])

    def test_trend_tracks_fixture_buckets_and_totals(self):
        self._seed_claude()

        trend = self.sb.collect_claude(NOW)["trend"]

        self.assertEqual(len(trend), 4)
        self.assertEqual(sum(bucket["total"] for bucket in trend), 440)
        self.assertEqual([bucket["t"] for bucket in trend],
                         sorted(bucket["t"] for bucket in trend))
        self.assertTrue(all(set(bucket) == {"t", "total"} for bucket in trend))
        self.assertLessEqual(len(trend), 20)

    def test_append_recollect_adds_only_incremental_usage(self):
        main, _sub = self._seed_claude()
        first = self.sb.collect_claude(NOW)
        with main.open("a", encoding="utf-8") as fh:
            fh.write(_usage_line("2026-07-14T12:45:00+09:00") + "\n")
        os.utime(main, (NOW - 10, NOW - 10))

        second = self.sb.collect_claude(NOW)

        self.assertEqual(first["tokens"]["today"]["total"], 540)
        self.assertEqual(second["tokens"]["today"]["total"], 550)
        self.assertEqual(second["tokens"]["today"]["input"], 61)
        self.assertEqual(second["tokens"]["byModel"]["claude-opus-4-8"],
                         {"input": 201, "output": 129, "total": 330, "usd": 0.01161825})

    def test_incomplete_appended_line_waits_for_newline(self):
        main, _sub = self._seed_claude()
        self.sb.collect_claude(NOW)
        with main.open("a", encoding="utf-8") as fh:
            fh.write(_usage_line(
                "2026-07-14T12:46:00+09:00",
                request_id="req-synthetic-partial",
                message_id="msg-synthetic-partial",
            ))
        os.utime(main, (NOW - 9, NOW - 9))

        incomplete = self.sb.collect_claude(NOW)
        self.assertEqual(incomplete["tokens"]["today"]["total"], 540)

        with main.open("a", encoding="utf-8") as fh:
            fh.write("\n")
        complete = self.sb.collect_claude(NOW)
        self.assertEqual(complete["tokens"]["today"]["total"], 550)

    def test_missing_projects_is_unavailable(self):
        provider = self.sb.collect_claude(NOW)
        self.assertEqual(provider["status"], "unavailable")
        self.assertEqual(provider["trend"], [])
        self.assertIsNotNone(provider["error"])
        self.assertIsNone(provider["sourceMtime"])

    def test_last5h_excludes_event_one_second_before_exact_cutoff(self):
        exact_now = datetime(2026, 7, 14, 13, 7, tzinfo=JST).timestamp()
        project = self.home / ".claude" / "projects" / "synthetic-project"
        path = project / "session-boundary.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            _usage_line(
                "2026-07-14T08:06:59+09:00",
                request_id="req-before-cutoff",
                message_id="msg-before-cutoff",
            ) + "\n" + _usage_line(
                "2026-07-14T08:07:00+09:00",
                request_id="req-at-cutoff",
                message_id="msg-at-cutoff",
            ) + "\n",
            encoding="utf-8",
        )
        os.utime(path, (exact_now - 1, exact_now - 1))

        provider = self.sb.collect_claude(exact_now)

        self.assertEqual(provider["tokens"]["today"]["total"], 20)
        self.assertEqual(provider["tokens"]["last5h"]["total"], 10)

    def test_next_day_resets_daily_scan_and_rescans_file(self):
        main, _sub = self._seed_claude()
        self.assertEqual(self.sb.collect_claude(NOW)["tokens"]["today"]["total"], 540)
        next_now = datetime(2026, 7, 15, 2, 0, tzinfo=JST).timestamp()
        with main.open("a", encoding="utf-8") as fh:
            fh.write(_usage_line(
                "2026-07-15T01:00:00+09:00",
                request_id="req-synthetic-next-day",
                message_id="msg-synthetic-next-day",
            ) + "\n")
        os.utime(main, (next_now - 10, next_now - 10))

        provider = self.sb.collect_claude(next_now)

        expected = {"input": 1, "output": 4, "cacheRead": 3,
                    "cacheCreate": 2, "total": 10}
        self.assertEqual(provider["tokens"]["today"], expected)
        self.assertEqual(provider["tokens"]["last5h"], expected)
        self.assertEqual(provider["sessionsToday"], 1)


class CodexCollectorTest(StatusBoardBase):
    def test_latest_rate_limits_secondary_and_tokens(self):
        source = self._seed_codex()

        provider = self.sb.collect_codex(NOW)

        self.assertEqual(provider["status"], "ok")
        self.assertEqual(provider["usedPercent"], 42.5)
        self.assertEqual(provider["plan"], "pro")
        self.assertEqual(provider["resetsAt"], 1784007200)
        self.assertEqual(provider["windowMinutes"], 300)
        self.assertEqual(provider["secondary"], {
            "usedPercent": 18.75,
            "resetsAt": 1784612000,
            "windowMinutes": 10080,
        })
        self.assertEqual(provider["tokens"], {
            "session": {"input": 1200, "cached": 300, "output": 400, "total": 1600},
        })
        self.assertEqual(provider["sourceMtime"], source.stat().st_mtime)
        self.assertIsNone(provider["error"])

    def test_newest_meta_only_falls_back_to_next_file(self):
        good = self._seed_codex(name="rollout-older.jsonl", mtime=NOW - 120)
        self._seed_codex(
            fixture="codex_rollout_meta_only.jsonl",
            name="rollout-newest.jsonl",
            mtime=NOW - 10,
        )

        provider = self.sb.collect_codex(NOW)

        self.assertEqual(provider["usedPercent"], 42.5)
        self.assertEqual(provider["sourceMtime"], good.stat().st_mtime)

    def test_latest_primary_keeps_newest_nonnull_secondary(self):
        source = self._seed_codex(
            fixture="codex_rollout_secondary_null.jsonl",
            name="rollout-secondary-null.jsonl",
        )

        provider = self.sb.collect_codex(NOW)

        self.assertEqual(provider["usedPercent"], 55.0)
        self.assertEqual(provider["secondary"], {
            "usedPercent": 12.5,
            "resetsAt": 1784610000,
            "windowMinutes": 10080,
        })
        self.assertEqual(provider["sourceMtime"], source.stat().st_mtime)

    def test_missing_sessions_dir_is_unavailable(self):
        provider = self.sb.collect_codex(NOW)
        self.assertEqual(provider["status"], "unavailable")
        self.assertIsNotNone(provider["error"])

    def test_old_source_is_stale_but_keeps_data(self):
        self._seed_codex(mtime=NOW - 6 * 3600 - 1)
        provider = self.sb.collect_codex(NOW)
        self.assertEqual(provider["status"], "stale")
        self.assertEqual(provider["usedPercent"], 42.5)

    def test_complete_line_at_exact_64k_tail_boundary_is_kept(self):
        source = (FIXTURES / "codex_rollout.jsonl").read_bytes().splitlines()[-1] + b"\n"
        self.assertLess(len(source), 64 * 1024)
        suffix = b"x" * (64 * 1024 - len(source))
        path = self.home / ".codex" / "sessions" / "2026" / "07" / "14" / "rollout-boundary.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        # tail の直前は改行、tail の先頭が rate_limits 完全行になるよう正確に配置する。
        path.write_bytes(b'{"type":"session_meta","payload":{}}\n' + source + suffix)
        os.utime(path, (NOW - 30, NOW - 30))

        provider = self.sb.collect_codex(NOW)

        self.assertEqual(provider["status"], "ok")
        self.assertEqual(provider["usedPercent"], 42.5)


class GeminiCollectorTest(StatusBoardBase):
    def test_credentials_with_future_expiry_are_logged_in(self):
        source = self._seed_gemini()
        provider = self.sb.collect_gemini(NOW)
        self.assertEqual(provider["status"], "ok")
        self.assertTrue(provider["loggedIn"])
        self.assertEqual(provider["sourceMtime"], source.stat().st_mtime)

    def test_missing_credentials_are_unavailable(self):
        provider = self.sb.collect_gemini(NOW)
        self.assertEqual(provider["status"], "unavailable")
        self.assertFalse(provider["loggedIn"])
        self.assertIsNotNone(provider["error"])

    def test_expired_credentials_with_refresh_token_are_logged_in(self):
        self._seed_gemini(expiry_ms=int((NOW - 1) * 1000))
        provider = self.sb.collect_gemini(NOW)
        self.assertEqual(provider["status"], "ok")
        self.assertTrue(provider["loggedIn"])

    def test_credentials_without_refresh_token_are_not_logged_in(self):
        self._seed_gemini(refresh_token=None)
        provider = self.sb.collect_gemini(NOW)
        self.assertEqual(provider["status"], "ok")
        self.assertFalse(provider["loggedIn"])


class LedgerTest(StatusBoardBase):
    def _upsert(self, entry_id="manual-one", **changes):
        entry = self._entry(entry_id=entry_id)
        entry.pop("updatedAt")
        entry.update(changes)
        return self.sb.ledger_apply({"op": "upsert", "entry": entry})

    def test_upsert_creates_versioned_file_and_server_timestamp(self):
        before = time.time()
        ok, msg = self._upsert()
        after = time.time()

        self.assertTrue(ok, msg)
        data = json.loads(self.sb.LEDGER_FILE.read_text(encoding="utf-8"))
        self.assertEqual(data["version"], 1)
        self.assertEqual(len(data["entries"]), 1)
        self.assertLessEqual(before, data["entries"][0]["updatedAt"])
        self.assertLessEqual(data["entries"][0]["updatedAt"], after)
        provider = self.sb.collect_ledger(after)[0]
        self.assertEqual(provider["kind"], "ledger")
        self.assertEqual(provider["usedPercent"], 25.0)

    def test_upsert_overrides_client_updated_at(self):
        entry = self._entry()
        entry["updatedAt"] = 1
        before = time.time()
        ok, msg = self.sb.ledger_apply({"op": "upsert", "entry": entry})
        self.assertTrue(ok, msg)
        written = json.loads(self.sb.LEDGER_FILE.read_text(encoding="utf-8"))["entries"][0]
        self.assertGreaterEqual(written["updatedAt"], before)

    def test_second_upsert_preserves_first_entry(self):
        self.assertTrue(self._upsert("manual-one")[0])
        self.assertTrue(self._upsert("manual-two", label="Second Synthetic API")[0])
        entries = json.loads(self.sb.LEDGER_FILE.read_text(encoding="utf-8"))["entries"]
        self.assertEqual([entry["id"] for entry in entries], ["manual-one", "manual-two"])

    def test_delete_removes_only_requested_entry(self):
        self._upsert("manual-one")
        self._upsert("manual-two", label="Second Synthetic API")
        ok, msg = self.sb.ledger_apply({"op": "delete", "id": "manual-one"})
        self.assertTrue(ok, msg)
        entries = json.loads(self.sb.LEDGER_FILE.read_text(encoding="utf-8"))["entries"]
        self.assertEqual([entry["id"] for entry in entries], ["manual-two"])

    def test_rejects_invalid_ids(self):
        for bad_id in ("", "UPPER", "has space", "x" * 33, 123):
            with self.subTest(bad_id=bad_id):
                ok, _msg = self._upsert(bad_id)
                self.assertFalse(ok)
        ok, _msg = self.sb.ledger_apply({"op": "delete", "id": "bad/id"})
        self.assertFalse(ok)

    def test_rejects_negative_numbers(self):
        for field in ("remaining", "total"):
            with self.subTest(field=field):
                ok, _msg = self._upsert(**{field: -0.1})
                self.assertFalse(ok)

    def test_rejects_wrong_types_bool_nan_and_unknown_op(self):
        bad_changes = (
            {"remaining": "1"},
            {"remaining": True},
            {"total": float("nan")},
            {"label": 123},
            {"label": "x" * 41},
        )
        for changes in bad_changes:
            with self.subTest(changes=changes):
                ok, _msg = self._upsert(**changes)
                self.assertFalse(ok)
        self.assertFalse(self.sb.ledger_apply({"op": "replace"})[0])
        self.assertFalse(self.sb.ledger_apply([])[0])

    def test_huge_number_is_rejected_without_raising(self):
        ok, _msg = self._upsert(remaining=1 << 100000)
        self.assertFalse(ok)

    def test_entry_older_than_seven_days_is_stale(self):
        self._write_ledger([self._entry(updated_at=NOW - 8 * 86400)])
        provider = self.sb.collect_ledger(NOW)[0]
        self.assertEqual(provider["status"], "stale")

    def test_apply_invalidates_board_cache_for_next_get(self):
        self._write_ledger([self._entry(entry_id="manual-one")])
        first = self.sb.status_board_json(now=NOW)
        self.assertIn("manual-one", [provider["id"] for provider in first["providers"]])

        ok, msg = self._upsert("manual-two", label="Second Synthetic API")
        self.assertTrue(ok, msg)
        second = self.sb.status_board_json(now=NOW)
        self.assertIn("manual-two", [provider["id"] for provider in second["providers"]])
        self.assertFalse(second["refreshing"])


class BoardTest(StatusBoardBase):
    def _seed_everything(self):
        self._seed_claude()
        self._seed_codex()
        self._seed_gemini()
        self._write_ledger([
            self._entry(entry_id="manual-one"),
            self._entry(entry_id="manual-two", label="Second Synthetic API"),
        ])

    def test_envelope_and_provider_order(self):
        self._seed_everything()
        board = self.sb.status_board_json(now=NOW)
        self.assertEqual(set(board), {"generatedAt", "refreshing", "providers", "spend", "fx"})
        self.assertEqual(board["generatedAt"], NOW)
        self.assertFalse(board["refreshing"])
        self.assertEqual(board["spend"], {"items": [], "totalJpy": 0, "totalUsd": 0})
        self.assertEqual(board["fx"], {"jpyPerUsd": 155})
        self.assertEqual([provider["id"] for provider in board["providers"]],
                         ["claude", "codex", "gemini", "xapi", "openai", "manual-one", "manual-two"])

    def test_broken_codex_does_not_break_other_providers(self):
        self._seed_claude()
        self._seed_gemini()
        broken = self.home / ".codex" / "sessions" / "2026" / "07" / "14" / "rollout-broken.jsonl"
        broken.parent.mkdir(parents=True, exist_ok=True)
        broken.write_text('{"payload":{"rate_limits":\n', encoding="utf-8")
        os.utime(broken, (NOW - 5, NOW - 5))

        board = self.sb.status_board_json(now=NOW)
        providers = {provider["id"]: provider for provider in board["providers"]}
        self.assertEqual(providers["claude"]["status"], "ok")
        self.assertEqual(providers["codex"]["status"], "unavailable")
        self.assertEqual(providers["gemini"]["status"], "ok")

    def test_collector_exception_is_isolated(self):
        self._seed_claude()
        self._seed_gemini()

        def explode(_now):
            raise RuntimeError("synthetic collector failure")

        self.sb.collect_codex = explode
        board = self.sb.status_board_json(now=NOW)
        providers = {provider["id"]: provider for provider in board["providers"]}
        self.assertEqual(providers["claude"]["status"], "ok")
        self.assertEqual(providers["gemini"]["status"], "ok")
        self.assertEqual(providers["codex"]["status"], "unavailable")
        self.assertIn("synthetic collector failure", providers["codex"]["error"])

    def test_stale_cache_returns_immediately_and_refreshes_single_flight(self):
        self._seed_everything()
        first = self.sb.status_board_json(now=NOW)
        original = self.sb.collect_claude
        started = threading.Event()
        release = threading.Event()
        calls = []

        def blocked(now):
            calls.append(now)
            started.set()
            release.wait(2)
            return original(now)

        self.sb.collect_claude = blocked
        try:
            stale = self.sb.status_board_json(now=NOW + 60)
            self.assertTrue(stale["refreshing"])
            self.assertEqual(stale["generatedAt"], first["generatedAt"])
            self.assertTrue(started.wait(1))
            again = self.sb.status_board_json(now=NOW + 60)
            self.assertTrue(again["refreshing"])
            self.assertEqual(len(calls), 1)
        finally:
            release.set()
            self.sb.collect_claude = original

        deadline = time.time() + 2
        refreshed = None
        while time.time() < deadline:
            refreshed = self.sb.status_board_json(now=NOW + 60)
            if not refreshed["refreshing"]:
                break
            time.sleep(0.01)
        self.assertIsNotNone(refreshed)
        self.assertFalse(refreshed["refreshing"])
        self.assertEqual(refreshed["generatedAt"], NOW + 60)


class SpendTest(StatusBoardBase):
    @staticmethod
    def _item(item_id="claude-max", label="Claude Max", amount=11800,
              currency="jpy", kind="sub", note="", renew_day=None):
        item = {"id": item_id, "label": label, "amount": amount,
                "currency": currency, "kind": kind, "note": note}
        if renew_day is not None:
            item["renewDay"] = renew_day
        return item

    def test_spend_rmw_add_update_by_label_and_delete_by_negative_amount(self):
        self._write_resources(entries=[self._entry(entry_id="keep-meter")])

        ok, msg = self.sb.spend_apply({"op": "upsert", "item": self._item()})
        self.assertTrue(ok, msg)
        ok, msg = self.sb.spend_apply({"op": "upsert", "item": self._item(
            item_id="another-id", amount=12800)})
        self.assertTrue(ok, msg)
        ok, msg = self.sb.spend_apply({"op": "upsert", "item": self._item(
            item_id="claude-max", label="Claude Max updated", amount=13000)})
        self.assertTrue(ok, msg)

        data = json.loads(self.sb.LEDGER_FILE.read_text(encoding="utf-8"))
        self.assertEqual([entry["id"] for entry in data["entries"]], ["keep-meter"])
        self.assertEqual(data["spend"], [self._item(
            label="Claude Max updated", amount=13000)])

        ok, msg = self.sb.spend_apply({"item": self._item(
            label="Claude Max updated", amount=-1)})
        self.assertTrue(ok, msg)
        self.assertEqual(json.loads(self.sb.LEDGER_FILE.read_text(encoding="utf-8"))["spend"], [])

    def test_spend_rejects_invalid_types(self):
        for changes in (
            {"amount": "11800"},
            {"amount": True},
            {"currency": "JPY"},
            {"kind": "subscription"},
            {"label": ""},
        ):
            with self.subTest(changes=changes):
                item = self._item()
                item.update(changes)
                ok, _msg = self.sb.spend_apply({"op": "upsert", "item": item})
                self.assertFalse(ok)

    def test_spend_renew_day_is_saved_returned_and_optional(self):
        item = self._item(renew_day=15)
        ok, msg = self.sb.spend_apply({"op": "upsert", "item": item})
        self.assertTrue(ok, msg)
        saved = json.loads(self.sb.LEDGER_FILE.read_text(encoding="utf-8"))["spend"][0]
        self.assertEqual(saved["renewDay"], 15)

        board = self.sb.status_board_json(now=NOW)
        self.assertEqual(board["spend"]["items"][0]["renewDay"], 15)

        omitted = self._item(item_id="payg-one", label="Payg one", kind="payg")
        ok, msg = self.sb.spend_apply({"op": "upsert", "item": omitted})
        self.assertTrue(ok, msg)
        saved = json.loads(self.sb.LEDGER_FILE.read_text(encoding="utf-8"))["spend"]
        self.assertNotIn("renewDay", saved[1])

    def test_spend_renew_day_rejects_out_of_range_or_non_integer(self):
        for renew_day in (0, 32, 1.5, True, "15"):
            with self.subTest(renew_day=renew_day):
                item = self._item(renew_day=renew_day)
                ok, _msg = self.sb.spend_apply({"op": "upsert", "item": item})
                self.assertFalse(ok)

    def test_get_response_contains_separate_spend_totals(self):
        self._write_resources(spend=[
            self._item(amount=11800),
            self._item(item_id="x-api", label="X API", amount=12.5,
                       currency="usd", kind="payg"),
        ])
        board = self.sb.status_board_json(now=NOW)
        self.assertEqual(set(board["spend"]), {"items", "totalJpy", "totalUsd"})
        self.assertEqual(board["spend"]["totalJpy"], 11800)
        self.assertEqual(board["spend"]["totalUsd"], 12.5)
        self.assertEqual(len(board["spend"]["items"]), 2)

    def test_spend_delete_by_label_only_keeps_other_items(self):
        # 回帰: 旧実装はlabel単独deleteでAND合成が全件Falseになり台帳が全滅した。
        ok, msg = self.sb.spend_apply({"op": "upsert", "item": self._item()})
        self.assertTrue(ok, msg)
        ok, msg = self.sb.spend_apply({"op": "upsert", "item": self._item(
            item_id="keep-me", label="Keep me", amount=10, currency="usd", kind="payg")})
        self.assertTrue(ok, msg)

        ok, msg = self.sb.spend_apply({"op": "delete", "label": "Claude Max"})
        self.assertTrue(ok, msg)
        labels = [item["label"] for item in
                  json.loads(self.sb.LEDGER_FILE.read_text(encoding="utf-8"))["spend"]]
        self.assertEqual(labels, ["Keep me"])

    def test_spend_delete_prefers_id_and_ignores_label_of_other_item(self):
        ok, msg = self.sb.spend_apply({"op": "upsert", "item": self._item()})
        self.assertTrue(ok, msg)
        ok, msg = self.sb.spend_apply({"op": "upsert", "item": self._item(
            item_id="other", label="Other", amount=5, currency="usd", kind="payg")})
        self.assertTrue(ok, msg)

        ok, msg = self.sb.spend_apply({"op": "delete", "id": "claude-max", "label": "Other"})
        self.assertTrue(ok, msg)
        labels = [item["label"] for item in
                  json.loads(self.sb.LEDGER_FILE.read_text(encoding="utf-8"))["spend"]]
        self.assertEqual(labels, ["Other"])

    def test_spend_negative_amount_delete_by_label_only_keeps_others(self):
        ok, msg = self.sb.spend_apply({"op": "upsert", "item": self._item()})
        self.assertTrue(ok, msg)
        ok, msg = self.sb.spend_apply({"op": "upsert", "item": self._item(
            item_id="keep-me", label="Keep me")})
        self.assertTrue(ok, msg)

        ok, msg = self.sb.spend_apply({"item": {
            "label": "Claude Max", "amount": -1, "currency": "jpy",
            "kind": "sub", "note": ""}})
        self.assertTrue(ok, msg)
        labels = [item["label"] for item in
                  json.loads(self.sb.LEDGER_FILE.read_text(encoding="utf-8"))["spend"]]
        self.assertEqual(labels, ["Keep me"])

    def test_spend_payg_upsert_stamps_current_month_and_sub_does_not(self):
        ok, msg = self.sb.spend_apply({"op": "upsert", "item": self._item(
            item_id="payg-one", label="Payg one", kind="payg")})
        self.assertTrue(ok, msg)
        ok, msg = self.sb.spend_apply({"op": "upsert", "item": self._item()})
        self.assertTrue(ok, msg)
        saved = json.loads(self.sb.LEDGER_FILE.read_text(encoding="utf-8"))["spend"]
        payg = next(item for item in saved if item["kind"] == "payg")
        sub = next(item for item in saved if item["kind"] == "sub")
        self.assertEqual(payg["month"], self.sb._local_month(time.time()))
        self.assertNotIn("month", sub)

    def test_spend_payg_explicit_month_is_honored_and_invalid_month_rejected(self):
        item = self._item(item_id="payg-back", label="Payg back", kind="payg")
        item["month"] = "2026-06"
        ok, msg = self.sb.spend_apply({"op": "upsert", "item": item})
        self.assertTrue(ok, msg)
        saved = json.loads(self.sb.LEDGER_FILE.read_text(encoding="utf-8"))["spend"][0]
        self.assertEqual(saved["month"], "2026-06")

        for month in ("2026-13", "2026-1", "202607", "abc", 202607, "2026-00"):
            with self.subTest(month=month):
                bad = self._item(item_id="payg-bad", label="Payg bad", kind="payg")
                bad["month"] = month
                ok, _msg = self.sb.spend_apply({"op": "upsert", "item": bad})
                self.assertFalse(ok)

    def test_spend_payg_upsert_next_month_keeps_previous_month_record(self):
        # 回帰: 同名従量の翌月upsertが先月レコードを当月に再スタンプ・上書きしない（月別履歴の保存則）。
        current = self.sb._local_month(time.time())
        stale = self._item(item_id="spend-codex", label="Codex従量", amount=30,
                           currency="usd", kind="payg")
        stale["month"] = "2020-01"
        self._write_resources(spend=[stale])

        ok, msg = self.sb.spend_apply({"op": "upsert", "item": {
            "label": "Codex従量", "amount": 12, "currency": "usd",
            "kind": "payg", "note": ""}})
        self.assertTrue(ok, msg)
        saved = json.loads(self.sb.LEDGER_FILE.read_text(encoding="utf-8"))["spend"]
        self.assertEqual(sorted(item["month"] for item in saved), ["2020-01", current])
        self.assertEqual(len({item["id"] for item in saved}), 2)
        old = next(item for item in saved if item["month"] == "2020-01")
        self.assertEqual(old["amount"], 30)

        ok, msg = self.sb.spend_apply({"op": "upsert", "item": {
            "label": "Codex従量", "amount": 15, "currency": "usd",
            "kind": "payg", "note": ""}})
        self.assertTrue(ok, msg)
        saved = json.loads(self.sb.LEDGER_FILE.read_text(encoding="utf-8"))["spend"]
        self.assertEqual(len(saved), 2)
        fresh = next(item for item in saved if item["month"] == current)
        self.assertEqual(fresh["amount"], 15)

    def test_spend_apply_stamps_month_on_legacy_payg_items(self):
        # 月無印の旧従量は書き込み機会に当月へ移行スタンプされ、翌月から自動で集計外になる。
        current = self.sb._local_month(time.time())
        self._write_resources(spend=[
            self._item(item_id="payg-legacy", label="Legacy payg", kind="payg"),
            self._item(amount=11800),
        ])
        ok, msg = self.sb.spend_apply({"op": "upsert", "item": self._item(
            item_id="other-sub", label="Other sub", amount=5, currency="usd")})
        self.assertTrue(ok, msg)
        saved = json.loads(self.sb.LEDGER_FILE.read_text(encoding="utf-8"))["spend"]
        legacy = next(item for item in saved if item["id"] == "payg-legacy")
        self.assertEqual(legacy["month"], current)
        sub = next(item for item in saved if item["id"] == "claude-max")
        self.assertNotIn("month", sub)

    def test_spend_sub_upsert_does_not_clobber_stale_payg_with_same_label(self):
        stale = self._item(item_id="spend-x-api", label="X API", amount=42, kind="payg")
        stale["month"] = "2020-01"
        self._write_resources(spend=[stale])

        ok, msg = self.sb.spend_apply({"op": "upsert", "item": {
            "label": "X API", "amount": 40, "currency": "usd", "kind": "sub", "note": ""}})
        self.assertTrue(ok, msg)
        saved = json.loads(self.sb.LEDGER_FILE.read_text(encoding="utf-8"))["spend"]
        self.assertEqual(len(saved), 2)
        self.assertEqual(len({item["id"] for item in saved}), 2)
        kept = next(item for item in saved if item["kind"] == "payg")
        self.assertEqual(kept["amount"], 42)
        self.assertEqual(kept["month"], "2020-01")

    def test_spend_totals_count_subs_and_current_month_payg_only(self):
        # NOW=2026-07-14: サブスクは常時、従量は当月(2026-07)と無印(旧データ互換)だけ合算し、
        # 先月分は items には残しつつ totals から除外する。
        stale = self._item(item_id="payg-old", label="Payg old", amount=9999, kind="payg")
        stale["month"] = "2026-06"
        fresh = self._item(item_id="payg-new", label="Payg new", amount=12.5,
                           currency="usd", kind="payg")
        fresh["month"] = "2026-07"
        legacy = self._item(item_id="payg-legacy", label="Payg legacy",
                            amount=500, kind="payg")
        self._write_resources(spend=[self._item(amount=11800), stale, fresh, legacy])

        board = self.sb.status_board_json(now=NOW)
        self.assertEqual(board["spend"]["totalJpy"], 12300)
        self.assertEqual(board["spend"]["totalUsd"], 12.5)
        self.assertEqual(len(board["spend"]["items"]), 4)
        months = {item["id"]: item.get("month") for item in board["spend"]["items"]}
        self.assertEqual(months["payg-old"], "2026-06")

    def test_fx_rate_is_saved_and_returned_with_default_for_old_ledger(self):
        self._write_resources(spend=[])
        self.assertEqual(self.sb.status_board_json(now=NOW)["fx"], {"jpyPerUsd": 155})

        ok, msg = self.sb.fx_apply({"jpyPerUsd": 160})
        self.assertTrue(ok, msg)
        saved = json.loads(self.sb.LEDGER_FILE.read_text(encoding="utf-8"))
        self.assertEqual(saved["fx"], {"jpyPerUsd": 160.0})
        self.assertEqual(self.sb.status_board_json(now=NOW)["fx"], {"jpyPerUsd": 160.0})

    def test_fx_rate_rejects_values_outside_range(self):
        for rate in (49, 1001, "155", True):
            with self.subTest(rate=rate):
                ok, _msg = self.sb.fx_apply({"jpyPerUsd": rate})
                self.assertFalse(ok)


class SpendApiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.home = Path(tempfile.mkdtemp(prefix="status_board_api_home_"))
        old = os.environ.get("OFFICE_HOME", _MISSING)
        os.environ["OFFICE_HOME"] = str(cls.home)
        try:
            cls.office = _exec_module(ROOT / "server" / "office_server.py", "office_spend_api")
            # 他テストが importlib 経由で残した status_board に依存せず、APIも専用HOMEへ固定する。
            cls.office.status_board = _load_status(cls.home)
        finally:
            if old is _MISSING:
                os.environ.pop("OFFICE_HOME", None)
            else:
                os.environ["OFFICE_HOME"] = old

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.home, ignore_errors=True)

    def setUp(self):
        shutil.rmtree(self.home, ignore_errors=True)
        self.home.mkdir()
        # R42.2: status_board APIはPro機能ゲート配下＝テスト鍵ライセンスで解錠して従来挙動を検証
        _licfx_spec = importlib.util.spec_from_file_location(
            "license_fixture_sb", Path(__file__).resolve().parent / "license_fixture.py")
        licfx = importlib.util.module_from_spec(_licfx_spec)
        _licfx_spec.loader.exec_module(licfx)
        licfx.install(self.home / "office_license.json")
        self.office._license_cache.update({"path": None, "mtime": None, "state": None})
        self.addCleanup(os.environ.pop, "OFFICE_LICENSE", None)
        self.addCleanup(os.environ.pop, "OFFICE_LICENSE_PUBKEY_N", None)

    def request_json(self, method, path, data=None):
        body = None if data is None else json.dumps(data).encode("utf-8")
        handler = self.office.Handler.__new__(self.office.Handler)
        handler.path = path
        handler.command = method
        handler.request_version = "HTTP/1.1"
        handler.requestline = f"{method} {path} HTTP/1.1"
        handler.headers = {
            "Host": "127.0.0.1:4797",
            "X-Office-Local": "1",
            "Content-Type": "application/json",
            "Content-Length": str(len(body or b"")),
        }
        handler.rfile = io.BytesIO(body or b"")
        handler.wfile = io.BytesIO()
        getattr(handler, f"do_{method}")()
        raw = handler.wfile.getvalue()
        head, _, response_body = raw.partition(b"\r\n\r\n")
        code = int(head.split(b"\r\n", 1)[0].split()[1])
        return code, json.loads(response_body.decode("utf-8"))

    def test_spend_endpoint_returns_400_for_invalid_type(self):
        code, body = self.request_json("POST", "/api/status_board/spend", {
            "op": "upsert",
            "item": {"label": "Claude Max", "amount": "bad",
                      "currency": "jpy", "kind": "sub"},
        })
        self.assertEqual(code, 400)
        self.assertFalse(body["ok"])

    def test_spend_endpoint_validates_renew_day(self):
        code, body = self.request_json("POST", "/api/status_board/spend", {
            "op": "upsert",
            "item": {"label": "Claude Max", "amount": 11800,
                      "currency": "jpy", "kind": "sub", "renewDay": 15},
        })
        self.assertEqual(code, 200)
        self.assertTrue(body["ok"])

        code, body = self.request_json("POST", "/api/status_board/spend", {
            "op": "upsert",
            "item": {"label": "Invalid renewal", "amount": 1,
                      "currency": "jpy", "kind": "sub", "renewDay": 0},
        })
        self.assertEqual(code, 400)
        self.assertFalse(body["ok"])

    def test_fx_endpoint_saves_and_get_returns_rate(self):
        code, body = self.request_json("POST", "/api/status_board/fx", {"jpyPerUsd": 172.5})
        self.assertEqual(code, 200)
        self.assertTrue(body["ok"])

        code, body = self.request_json("GET", "/api/status_board")
        self.assertEqual(code, 200)
        self.assertEqual(body["fx"], {"jpyPerUsd": 172.5})

        code, body = self.request_json("POST", "/api/status_board/fx", {"jpyPerUsd": 49})
        self.assertEqual(code, 400)
        self.assertFalse(body["ok"])


class BillingClassificationTest(unittest.TestCase):
    """R72: 「定額サブスク枠」と「APIキー従量」の区別はサーバーが正本を出す。
    UI はこの billing でグループ分けするだけ＝分類が画面ごとにズレない。"""

    def setUp(self):
        self.sb = _exec_module(ROOT / "server" / "status_board.py", "sb_billing")

    def test_billing_is_derived_from_kind(self):
        providers = [{"kind": k} for k in
                     ("tokens", "gauge", "login", "external", "api", "ledger")]
        self.sb._apply_billing(providers)
        self.assertEqual([p["billing"] for p in providers],
                         ["subscription", "subscription", "subscription",
                          "apikey", "apikey", "manual"])

    def test_unknown_kind_is_left_unclassified(self):
        # 嘘のグループを作らない（未知kindは無印のまま＝UIはどちらの節にも出さない）
        providers = [{"kind": "brand-new"}, {"kind": None}, "not-a-dict"]
        self.sb._apply_billing(providers)
        self.assertNotIn("billing", providers[0])
        self.assertNotIn("billing", providers[1])

    def test_board_tags_every_known_provider(self):
        board = self.sb._build_board(NOW, fetch_external=False)
        kinds = {p.get("kind") for p in board["providers"]}
        self.assertTrue({"tokens", "gauge", "login", "external"} <= kinds)
        for pr in board["providers"]:
            if pr.get("kind") in self.sb._BILLING_BY_KIND:
                self.assertIn("billing", pr, pr.get("id"))


class PrivacyIsolationRegressionTest(unittest.TestCase):
    def test_office_json_and_relay_push_exclude_resource_board(self):
        env = dict(os.environ)
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        fake_home = Path(subprocess.check_output(
            [sys.executable, str(TESTS / "make_home.py")], text=True, env=env).strip())
        ledger = fake_home / ".claude" / "office_resources.json"
        ledger.write_text(json.dumps({
            "version": 1,
            "entries": [{
                "id": "private-meter", "label": "Private Synthetic Meter",
                "plan": "metered", "remaining": 1, "total": 100, "unit": "credits",
                "note": "synthetic", "updatedAt": NOW, "usedPercent": 99.0,
            }],
            "spend": [{
                "id": "private-sub", "label": "Private Subscription", "amount": 11800,
                "currency": "jpy", "kind": "sub", "note": "synthetic",
            }],
        }), encoding="utf-8")

        keys = ("OFFICE_HOME", "OFFICE_DATA", "OFFICE_CONFIG")
        old = {key: os.environ.get(key, _MISSING) for key in keys}
        os.environ["OFFICE_HOME"] = str(fake_home)
        os.environ["OFFICE_DATA"] = str(fake_home)
        os.environ["OFFICE_CONFIG"] = str(fake_home / "office_config.json")
        try:
            office = _exec_module(ROOT / "server" / "office_server.py", "office_status_isolation")
            snapshot = office.office_json()
            # R50: roster/rosterCounts/tasks を追加（status_board は依然として混ぜない）
            self.assertEqual(set(snapshot), {"officeName", "employees", "history", "generatedAt",
                                             "setup", "counts", "edition", "lang",
                                             "roster", "rosterCounts", "tasks"})

            relay = _exec_module(ROOT / "server" / "relay_agent.py", "relay_status_isolation")
            relay.office = office
            sent = {}

            def fake(method, url, token, body=None):
                sent["method"] = method
                sent["url"] = url
                sent["body"] = body
                return {"ok": True}

            relay._req = fake
            relay.push_status("http://relay.synthetic", "synthetic-token")
            body = json.dumps(sent["body"], ensure_ascii=False)
            self.assertEqual(sent["method"], "POST")
            self.assertTrue(sent["url"].endswith("/status"))
            self.assertNotIn("usedPercent", body)
            self.assertNotIn("office_resources", body)
        finally:
            for key, value in old.items():
                if value is _MISSING:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value
            shutil.rmtree(fake_home, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
