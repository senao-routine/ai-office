# -*- coding: utf-8 -*-
"""R24の外部コネクタとプロジェクト別概算の決定論テスト。"""
import importlib.util
import json
import os
import shutil
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
JST = timezone(timedelta(hours=9))
NOW = datetime(2026, 7, 14, 13, 0, tzinfo=JST).timestamp()


def load_status(home):
    old = os.environ.get("OFFICE_HOME")
    os.environ["OFFICE_HOME"] = str(home)
    try:
        spec = importlib.util.spec_from_file_location(
            f"status_board_ext_{id(home)}", ROOT / "server" / "status_board.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        if old is None:
            os.environ.pop("OFFICE_HOME", None)
        else:
            os.environ["OFFICE_HOME"] = old


class ExternalConnectorTest(unittest.TestCase):
    def setUp(self):
        self.home = Path(tempfile.mkdtemp(prefix="status_board_ext_"))
        secrets = self.home / ".claude"
        secrets.mkdir(parents=True)
        (secrets / "office_secrets").write_text(
            "X_BEARER_TOKEN=" + "A" * 32 + "\n" +
            "OPENAI_ADMIN_KEY=" + "B" * 32 + "\n",
            encoding="utf-8",
        )
        self.sb = load_status(self.home)

    def tearDown(self):
        shutil.rmtree(self.home, ignore_errors=True)

    def test_xapi_normal_http_error_and_timeout(self):
        with patch.object(self.sb, "_fetch_json", return_value={
            "data": {"project_usage": 12, "project_cap": 100, "cap_reset_day": 25},
        }):
            self.assertEqual(self.sb.collect_xapi(NOW), {
                "connected": True, "used": 12, "cap": 100, "pct": 12.0, "resetDay": 25,
            })

        # 新X Developer Console(Pay Per Use)実レスポンス: 数値が文字列で来る(2026-07-21観測)
        self.sb._EXT_CACHE.clear()
        with patch.object(self.sb, "_fetch_json", return_value={
            "data": {"cap_reset_day": 18, "project_cap": "2000000",
                     "project_id": "2020595706139115520", "project_usage": "0"},
        }):
            self.assertEqual(self.sb.collect_xapi(NOW), {
                "connected": True, "used": 0, "cap": 2000000, "pct": 0.0, "resetDay": 18,
            })

        self.sb._EXT_CACHE.clear()
        with patch.object(self.sb, "_fetch_json", return_value={"error": "HTTP 401"}):
            self.assertEqual(self.sb.collect_xapi(NOW), {
                "connected": True, "error": "HTTP 401",
            })

        self.sb._EXT_CACHE.clear()
        with patch.object(self.sb, "_fetch_json", return_value={"error": "timeout"}):
            self.assertEqual(self.sb.collect_xapi(NOW), {
                "connected": True, "error": "timeout",
            })

    def test_openai_cost_sums_buckets_and_maps_auth_errors(self):
        payload = {"data": [
            {"results": [{"amount": {"value": 1.25}}, {"amount": {"value": 2}}]},
            {"results": [{"amount": {"value": 0.75}}]},
        ]}
        with patch.object(self.sb, "_fetch_json", return_value=payload):
            self.assertEqual(self.sb.collect_openai_cost(NOW), {
                "connected": True, "monthUsd": 4.0, "sinceDay": 1,
            })

        self.sb._EXT_CACHE.clear()
        with patch.object(self.sb, "_fetch_json", return_value={"error": "HTTP 401"}):
            self.assertEqual(self.sb.collect_openai_cost(NOW), {
                "connected": True, "error": "管理キー権限なし(401)",
            })

        self.sb._EXT_CACHE.clear()
        with patch.object(self.sb, "_fetch_json", return_value={"error": "timeout"}):
            self.assertEqual(self.sb.collect_openai_cost(NOW), {
                "connected": True, "error": "timeout",
            })

    def test_openai_cost_follows_has_more_pages(self):
        # JST月初起点だとUTC日次バケットが32枚になり得る=has_moreの2ページ目を必ず追う。
        pages = [
            {"data": [{"results": [{"amount": {"value": 3.0}}]}],
             "has_more": True, "next_page": "cursor-2"},
            {"data": [{"results": [{"amount": {"value": 1.5}}]}],
             "has_more": False},
        ]
        calls = []

        def fake_fetch(url, headers, timeout=6):
            calls.append(url)
            return pages[len(calls) - 1]

        self.sb._EXT_CACHE.clear()
        with patch.object(self.sb, "_fetch_json", side_effect=fake_fetch):
            self.assertEqual(self.sb.collect_openai_cost(NOW), {
                "connected": True, "monthUsd": 4.5, "sinceDay": 1,
            })
        self.assertEqual(len(calls), 2)
        self.assertIn("page=cursor-2", calls[1])


class ProjectEstimateTest(unittest.TestCase):
    def setUp(self):
        self.home = Path(tempfile.mkdtemp(prefix="status_board_project_"))
        self.sb = load_status(self.home)
        self.projects = self.home / ".claude" / "projects"
        self.projects.mkdir(parents=True)

    def tearDown(self):
        shutil.rmtree(self.home, ignore_errors=True)

    def add_usage(self, dirname, input_tokens, output_tokens, model="synthetic-model",
                  cache_read=0, cache_create=0):
        directory = self.projects / dirname
        directory.mkdir(parents=True, exist_ok=True)
        row = {
            "type": "assistant",
            "requestId": f"req-{dirname}",
            "timestamp": "2026-07-14T12:00:00+09:00",
            "message": {
                "id": f"msg-{dirname}", "model": model,
                "usage": {"input_tokens": input_tokens, "output_tokens": output_tokens,
                          "cache_read_input_tokens": cache_read,
                          "cache_creation_input_tokens": cache_create},
            },
        }
        path = directory / "session.jsonl"
        path.write_text(json.dumps(row) + "\n", encoding="utf-8")
        os.utime(path, (NOW - 30, NOW - 30))

    def test_override_prices_are_merged_and_project_cost_is_deterministic(self):
        prices = self.home / ".claude" / "office_model_prices.json"
        prices.parent.mkdir(parents=True, exist_ok=True)
        prices.write_text(json.dumps({
            "synthetic-model": {"in": 2.0, "out": 4.0},
            "default": {"in": 1.0, "out": 1.0},
        }), encoding="utf-8")
        self.add_usage("-Users-test-alpha", 100, 50)
        self.add_usage("plain-project", 200, 100, model="unknown-model")

        provider = self.sb.collect_claude(NOW)
        rows = {row["name"]: row for row in provider["projects"]}
        self.assertEqual(rows["alpha"], {
            "name": "alpha", "inTok": 100, "outTok": 50, "usd": 0.0004,
        })
        self.assertEqual(rows["plain-project"]["usd"], 0.0003)
        self.assertTrue(provider["estimate"])
        self.assertEqual(self.sb.MODEL_PRICES["default"], {
            "in": 3.0, "out": 15.0, "cacheRead": 0.3, "cacheCreate": 3.75,
        })

    def test_top_ten_plus_other_is_sorted_and_aggregated(self):
        prices = self.home / ".claude" / "office_model_prices.json"
        prices.parent.mkdir(parents=True, exist_ok=True)
        prices.write_text(json.dumps({"synthetic-model": {"in": 1.0, "out": 1.0}}), encoding="utf-8")
        for index in range(12):
            self.add_usage(f"project-{index:02d}", index + 1, index + 1)

        rows = self.sb.collect_claude(NOW)["projects"]
        self.assertEqual(len(rows), 11)
        self.assertEqual(rows[0]["name"], "project-11")
        self.assertEqual(rows[-1]["name"], "その他")
        self.assertEqual(rows[-1]["inTok"], 3)
        self.assertEqual(rows[-1]["outTok"], 3)
        self.assertEqual(rows[-1]["usd"], 0.000006)

    def test_cache_read_uses_discounted_price_in_project_and_model_usd(self):
        prices = self.home / ".claude" / "office_model_prices.json"
        prices.parent.mkdir(parents=True, exist_ok=True)
        prices.write_text(json.dumps({"synthetic-model": {"in": 2.0, "out": 4.0}}),
                          encoding="utf-8")
        self.add_usage("cache-project", 100, 0, cache_read=9000)

        provider = self.sb.collect_claude(NOW)
        row = {item["name"]: item for item in provider["projects"]}["cache-project"]
        # 100*2 + 9,000*(2*0.1) = 2,000 USD / 1M; 旧式なら18,200 USD / 1M。
        self.assertAlmostEqual(row["usd"], 0.002, places=9)
        self.assertAlmostEqual(
            provider["tokens"]["byModel"]["synthetic-model"]["usd"], 0.002, places=9)
        self.assertLess(row["usd"], 0.0182 / 8)

    def test_four_key_override_keeps_cache_create_price(self):
        prices = self.home / ".claude" / "office_model_prices.json"
        prices.parent.mkdir(parents=True, exist_ok=True)
        prices.write_text(json.dumps({"synthetic-model": {
            "in": 10.0, "out": 20.0, "cacheRead": 1.0, "cacheCreate": 30.0,
        }}), encoding="utf-8")
        self.add_usage("four-key", 10, 5, cache_read=100, cache_create=20)

        provider = self.sb.collect_claude(NOW)
        row = {item["name"]: item for item in provider["projects"]}["four-key"]
        self.assertAlmostEqual(row["usd"], 0.0009, places=9)

    def test_status_board_adds_external_ids_and_projects_without_office_json_pollution(self):
        self.add_usage("plain-project", 10, 20)
        board = self.sb.status_board_json(NOW)
        providers = {provider["id"]: provider for provider in board["providers"]}
        self.assertIn("xapi", providers)
        self.assertIn("openai", providers)
        self.assertIn("projects", providers["claude"])
        self.assertNotIn("xapi", board.get("office_json", {}))
        self.assertNotIn("openai", board.get("office_json", {}))

    def test_spend_response_keeps_optional_renew_day(self):
        item = {"label": "Synthetic Sub", "amount": 12,
                "currency": "usd", "kind": "sub", "renewDay": 31}
        ok, msg = self.sb.spend_apply({"op": "upsert", "item": item})
        self.assertTrue(ok, msg)
        board = self.sb.status_board_json(NOW)
        self.assertEqual(board["spend"]["items"][0]["renewDay"], 31)

        ok, _msg = self.sb.spend_apply({
            "op": "upsert",
            "item": {"label": "Bad renewal", "amount": 1,
                     "currency": "usd", "kind": "sub", "renewDay": 32},
        })
        self.assertFalse(ok)


if __name__ == "__main__":
    unittest.main()


class ClaudePaceTest(unittest.TestCase):
    """R55.1: Claudeペースゲージ（7日ピーク比・嘘の枠%を作らない相対値）。"""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="pace_"))
        self.sb = load_status(self.tmp)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_first_sample_is_100pct_and_persists(self):
        p = self.sb._claude_pace(50_000_000, NOW)
        self.assertEqual((p["pct"], p["peak5h"]), (100.0, 50_000_000))
        self.assertTrue(self.sb.CLAUDE_PEAK_FILE.exists())

    def test_relative_to_peak_and_peak_updates(self):
        self.sb._claude_pace(100_000_000, NOW)                  # ピーク確立
        p = self.sb._claude_pace(25_000_000, NOW + 3600)
        self.assertEqual(p["pct"], 25.0)
        self.assertEqual(p["peak5h"], 100_000_000)
        p = self.sb._claude_pace(150_000_000, NOW + 7200)       # ピーク更新
        self.assertEqual((p["pct"], p["peak5h"]), (100.0, 150_000_000))

    def test_prunes_to_last_7_days(self):
        for i in range(10):
            self.sb._claude_pace(1_000_000 * (i + 1), NOW + i * 86400)
        d = json.loads(self.sb.CLAUDE_PEAK_FILE.read_text(encoding="utf-8"))
        self.assertEqual(len(d["days"]), 7)

    def test_zero_and_broken_file_are_safe(self):
        self.assertIsNone(self.sb._claude_pace(0, NOW))         # 実測ゼロ=ゲージ出さない
        self.sb.CLAUDE_PEAK_FILE.parent.mkdir(parents=True, exist_ok=True)
        self.sb.CLAUDE_PEAK_FILE.write_text("{broken", encoding="utf-8")
        p = self.sb._claude_pace(10, NOW)                       # 壊れた基準ファイル→再生成
        self.assertEqual(p["pct"], 100.0)


class AccountUsageTest(unittest.TestCase):
    """R57: アカウント別クレジット消費（帳簿の純関数＋auth読取＋provider装飾）。"""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="acct_"))
        self.sb = load_status(self.tmp)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    # ---- _attribute_delta（純関数） ----
    def test_attribute_delta_same_day_and_rollover(self):
        led = self.sb._attribute_delta({}, "a1", "2026-08-01", 1000)
        self.assertEqual(led["claude"]["days"]["2026-08-01"], {"a1": 1000})
        led2 = self.sb._attribute_delta(led, "a1", "2026-08-01", 1600)   # 同日+600
        self.assertEqual(led2["claude"]["days"]["2026-08-01"], {"a1": 1600})
        led3 = self.sb._attribute_delta(led2, "a2", "2026-08-01", 1900)  # 途中でアカウント切替
        self.assertEqual(led3["claude"]["days"]["2026-08-01"], {"a1": 1600, "a2": 300})
        led4 = self.sb._attribute_delta(led3, "a2", "2026-08-02", 500)   # 日替わり=全量
        self.assertEqual(led4["claude"]["days"]["2026-08-02"], {"a2": 500})
        self.assertEqual(led4["claude"]["lastSeen"], {"date": "2026-08-02", "total": 500})

    def test_attribute_delta_clamps_and_is_pure(self):
        led = self.sb._attribute_delta({}, "a1", "2026-08-01", 1000)
        before = json.dumps(led, sort_keys=True)
        led2 = self.sb._attribute_delta(led, "a1", "2026-08-01", 400)    # 巻き戻り→0加算
        self.assertEqual(led2["claude"]["days"]["2026-08-01"], {"a1": 1000})
        self.assertEqual(json.dumps(led, sort_keys=True), before)        # 入力は不変（純関数）
        # アカウント不明でも lastSeen は進む（次アカウント確定時に過大計上しない）
        led3 = self.sb._attribute_delta(led2, None, "2026-08-01", 900)
        self.assertEqual(led3["claude"]["lastSeen"]["total"], 900)

    def test_attribute_delta_prunes_8_days(self):
        led = {}
        for i in range(1, 12):
            led = self.sb._attribute_delta(led, "a1", f"2026-08-{i:02d}", 100)
        self.assertEqual(len(led["claude"]["days"]), 8)

    # ---- アカウント読取（best-effort） ----
    def test_claude_account_reads_oauth(self):
        (self.tmp / ".claude.json").write_text(json.dumps({"oauthAccount": {
            "accountUuid": "uuid-1", "emailAddress": "a@b.c",
            "userRateLimitTier": "max"}}), encoding="utf-8")
        self.assertEqual(self.sb._claude_account(),
                         {"id": "uuid-1", "email": "a@b.c", "tier": "max"})

    def test_codex_account_decodes_jwt_email_without_verify(self):
        import base64 as b64
        payload = b64.urlsafe_b64encode(json.dumps({"email": "x@y.z"}).encode()).decode().rstrip("=")
        (self.tmp / ".codex").mkdir()
        (self.tmp / ".codex" / "auth.json").write_text(json.dumps({"tokens": {
            "account_id": "cx-9", "id_token": f"h.{payload}.s"}}), encoding="utf-8")
        self.assertEqual(self.sb._codex_account(), {"id": "cx-9", "email": "x@y.z"})

    def test_broken_auth_files_are_safe(self):
        (self.tmp / ".claude.json").write_text("{broken", encoding="utf-8")
        (self.tmp / ".codex").mkdir()
        (self.tmp / ".codex" / "auth.json").write_text("[]", encoding="utf-8")
        self.assertIsNone(self.sb._claude_account())
        self.assertIsNone(self.sb._codex_account())

    # ---- provider装飾（統合・帳簿600） ----
    def test_apply_accounts_decorates_and_persists(self):
        (self.tmp / ".claude.json").write_text(json.dumps({"oauthAccount": {
            "accountUuid": "uuid-1", "emailAddress": "a@b.c",
            "userRateLimitTier": "max"}}), encoding="utf-8")
        providers = [
            {"id": "claude", "status": "ok", "tokens": {"today": {"total": 5000}}},
            {"id": "codex", "status": "ok", "usedPercent": 3.0, "resetsAt": 123,
             "plan": "pro", "secondary": None},
        ]
        self.sb._apply_accounts(providers, NOW)
        self.assertEqual(providers[0]["account"], {"email": "a@b.c", "tier": "max"})
        self.assertEqual(providers[0]["accounts"][0]["todayTok"], 5000)
        self.assertTrue(providers[0]["accounts"][0]["active"])
        self.assertNotIn("account", providers[1])   # codex auth無し→装飾しない
        mode = self.sb.ACCOUNT_USAGE_FILE.stat().st_mode & 0o777
        self.assertEqual(mode, 0o600)


class ClaudeSubscriptionTest(unittest.TestCase):
    """R61: statusLine capture の実測枠%（office_usage/current.json → subscription）。"""

    def setUp(self):
        self.home = Path(tempfile.mkdtemp(prefix="status_board_sub_"))
        self.usage = self.home / ".claude" / "office_usage"
        self.usage.mkdir(parents=True)
        self.sb = load_status(self.home)

    def tearDown(self):
        shutil.rmtree(self.home, ignore_errors=True)

    def _write(self, rec):
        (self.usage / "current.json").write_text(json.dumps(rec), encoding="utf-8")

    def test_valid_capture_becomes_payload(self):
        self._write({"capturedAt": int(NOW) - 120,
                     "rateLimits": {
                         "five_hour": {"used_percentage": 43.0, "resets_at": 1754007200},
                         "seven_day": {"used_percentage": 61.0, "resets_at": 1754259200}},
                     "model": "Fable 5",
                     "account": {"id": "abcdef123456xxxx", "email": "main@example.com"}})
        sub = self.sb._claude_subscription(NOW)
        self.assertEqual(sub["fiveHour"], {"pct": 43.0, "resetsAt": 1754007200})
        self.assertEqual(sub["sevenDay"], {"pct": 61.0, "resetsAt": 1754259200})
        self.assertEqual(sub["staleSec"], 120)
        self.assertEqual(sub["account"], {"id": "abcdef123456",   # 12桁に丸め
                                          "email": "main@example.com"})

    def test_partial_window_and_clamp(self):
        # seven_day 欠落（公式docs=各窓は独立に欠落しうる）・%は0..100へclamp
        self._write({"capturedAt": int(NOW) - 5,
                     "rateLimits": {"five_hour": {"used_percentage": 120.5,
                                                  "resets_at": 1754007200}}})
        sub = self.sb._claude_subscription(NOW)
        self.assertEqual(sub["fiveHour"]["pct"], 100.0)
        self.assertIsNone(sub["sevenDay"])
        self.assertIsNone(sub["account"])

    def test_broken_or_missing_returns_none(self):
        self.assertIsNone(self.sb._claude_subscription(NOW))          # ファイル無し
        (self.usage / "current.json").write_text("{broken", encoding="utf-8")
        self.assertIsNone(self.sb._claude_subscription(NOW))          # 壊れJSON
        self._write({"capturedAt": int(NOW), "rateLimits": {}})       # 窓ゼロ
        self.assertIsNone(self.sb._claude_subscription(NOW))
        self._write({"rateLimits": {"five_hour": {"used_percentage": 1}}})
        self.assertIsNone(self.sb._claude_subscription(NOW))          # capturedAt無し
