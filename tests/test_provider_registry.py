#!/usr/bin/env python3
"""R103-5: AI サービス・API の接続の正本（server/provider_registry.py）と、それを読む status_board・office_server の回帰。
本人（2026-10-01）「リソースは様々な AI サービスや API がきちんと接続できる仕様に」。掟= 取れない値は None・% は上限があるときだけ・
鍵は URL／応答／office_json に出さない・公開されていない API を推測で叩かない。"""
import importlib.util
import json
import os
import re
import shutil
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "server"))
import provider_registry as reg  # noqa: E402

SECRET = "sk-ant-admin01-" + "x" * 40
NOW = datetime(2026, 10, 2, 9, 30, tzinfo=timezone.utc).timestamp()


def _load(name, home):
    old = os.environ.get("OFFICE_HOME")
    os.environ["OFFICE_HOME"] = str(home)
    try:
        spec = importlib.util.spec_from_file_location(name + "_pr", ROOT / "server" / (name + ".py"))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    finally:
        if old is None:
            os.environ.pop("OFFICE_HOME", None)
        else:
            os.environ["OFFICE_HOME"] = old


class RegistryShapeTest(unittest.TestCase):
    def test_rows_are_well_formed(self):
        ids = [p["id"] for p in reg.PROVIDERS]
        self.assertEqual(len(ids), len(set(ids)))
        keys = [p["key"] for p in reg.PROVIDERS]
        self.assertEqual(len(keys), len(set(keys)))
        for p in reg.PROVIDERS:
            self.assertRegex(p["id"], r"^[a-z][a-z0-9_]{1,23}$")
            self.assertRegex(p["key"], r"^[A-Z][A-Z0-9_]{2,40}$")
            self.assertIn(p["kind"], reg.KINDS)
            self.assertTrue(p["measures"] and set(p["measures"]) <= set(reg.MEASURES), p["id"])
            for f in ("panel", "hint"):
                self.assertTrue(isinstance(p[f], tuple) and len(p[f]) == 2 and all(isinstance(x, str) and x for x in p[f]), p["id"])
            self.assertFalse(p["getFrom"].startswith(("http:", "https:")), "発行場所は URL 文字列の表示だけ（リンクにしない）")
            self.assertIn(p["test"], (None, "groq_models", "openai_costs"))
        # 本人裁定（2026-10-02）: Anthropic・OpenAI・Mistral を先に
        self.assertEqual(ids[:3], ["anthropic", "openai_usage", "mistral"])

    def test_status_board_and_server_read_the_registry(self):
        home = Path(tempfile.mkdtemp(prefix="pr-sb-"))
        try:
            sb = _load("status_board", home)
            self.assertEqual({p["id"] for p in sb.API_PROVIDERS}, {p["id"] for p in reg.PROVIDERS} - {"openai_usage"})
            self.assertEqual(set(sb._API_FETCHERS), {p["id"] for p in sb.API_PROVIDERS})
            self.assertIsNone(sb.api_fetcher("openai_usage"))
            srv = _load("office_server", home)
            self.assertTrue(reg.KEY_NAMES <= srv._KEY_NAMES)
            self.assertIn("/api/providers/test", srv._STRICT_POST)        # 鍵を相手へ送る＝画面の操作だけ（Origin 必須）
        finally:
            shutil.rmtree(home, ignore_errors=True)


class FakeHttp:
    def __init__(self, pages):
        self.pages = list(pages)
        self.calls = []

    def __call__(self, url, headers, timeout=6):
        self.calls.append((url, dict(headers)))
        return self.pages.pop(0) if self.pages else {"error": "HTTP 500"}


class AnthropicTest(unittest.TestCase):
    def test_month_to_date_sum_in_cents_over_pages(self):
        http = FakeHttp([
            {"data": [{"starting_at": "2026-10-01T00:00:00Z", "results": [{"amount": "1234.5", "currency": "USD"}]},
                      {"starting_at": "2026-10-02T00:00:00Z", "results": []}],
             "has_more": True, "next_page": "page_abc"},
            {"data": [{"results": [{"amount": "100", "currency": "USD"}, {"amount": 65.5, "currency": "USD"}]}],
             "has_more": False, "next_page": None},
        ])
        out = reg.fetch_anthropic(SECRET, http, NOW)
        self.assertEqual(out, {"currency": "USD", "spentMonth": 14.0, "limit": None})   # (1234.5+100+65.5) セント＝$14.00
        u0, h0 = http.calls[0]
        q0 = parse_qs(urlsplit(u0).query)
        self.assertEqual(q0["starting_at"], ["2026-10-01T00:00:00Z"])
        self.assertEqual((q0["bucket_width"], q0["limit"]), (["1d"], ["31"]))
        self.assertEqual(parse_qs(urlsplit(http.calls[1][0]).query)["page"], ["page_abc"])
        self.assertEqual(h0["x-api-key"], SECRET)
        self.assertEqual(h0["anthropic-version"], "2023-06-01")
        for u, _h in http.calls:
            self.assertNotIn(SECRET, u)                                    # 鍵は URL に入れない

    def test_errors_are_honest(self):
        self.assertEqual(reg.fetch_anthropic(SECRET, FakeHttp([{"error": "HTTP 401"}]), NOW), {"error": "HTTP 401"})
        bad = {"data": [{"results": [{"amount": "-5", "currency": "USD"}]}], "has_more": False}
        self.assertEqual(reg.fetch_anthropic(SECRET, FakeHttp([bad]), NOW), {"error": "invalid response"})
        nan = {"data": [{"results": [{"amount": "nan", "currency": "USD"}]}], "has_more": False}
        self.assertEqual(reg.fetch_anthropic(SECRET, FakeHttp([nan]), NOW), {"error": "invalid response"})
        eur = {"data": [{"results": [{"amount": "5", "currency": "EUR"}]}], "has_more": False}
        self.assertEqual(reg.fetch_anthropic(SECRET, FakeHttp([eur]), NOW), {"error": "unexpected currency"})
        self.assertEqual(reg.fetch_anthropic(SECRET, FakeHttp([{"data": "x"}]), NOW), {"error": "invalid response"})

    def test_pages_are_bounded_and_partial_sums_are_not_reported(self):
        """別モデルレビュー: 一部のページだけの額を「今月の合計」として出さない（予算の % が嘘になる）。"""
        page = {"data": [{"results": [{"amount": "100", "currency": "USD"}]}], "has_more": True, "next_page": "p"}
        http = FakeHttp([page] * 10)
        self.assertEqual(reg.fetch_anthropic(SECRET, http, NOW), {"error": "invalid response"})
        self.assertEqual(len(http.calls), 4)                              # 続きがあっても 4 ページで打ち切る（通信の回数の上限）
        no_cursor = {"data": [{"results": [{"amount": "100", "currency": "USD"}]}], "has_more": True, "next_page": None}
        self.assertEqual(reg.fetch_anthropic(SECRET, FakeHttp([no_cursor]), NOW), {"error": "invalid response"})

    def test_malformed_buckets_are_errors_not_zero(self):
        """別モデルレビュー: 欠けた・壊れたバケットを空として数えると「今月 $0」と嘘をつく。"""
        for data in ([{}], [None], [{"results": None}], [{"results": "x"}], [{"results": [None]}]):
            self.assertEqual(reg.fetch_anthropic(SECRET, FakeHttp([{"data": data, "has_more": False}]), NOW),
                             {"error": "invalid response"}, data)
        ok = {"data": [{"results": []}, {"results": []}], "has_more": False}
        self.assertEqual(reg.fetch_anthropic(SECRET, FakeHttp([ok]), NOW)["spentMonth"], 0.0)   # 本当に使っていない月は 0


class MistralTest(unittest.TestCase):
    def test_connect_only_and_no_guessed_numbers(self):
        http = FakeHttp([{"object": "list", "data": [{"id": "mistral-large"}]}])
        out = reg.fetch_mistral("m" * 32, http, NOW)
        self.assertEqual(out, {"currency": "USD", "spentMonth": None, "balance": None, "limit": None, "noApi": True})
        self.assertEqual(http.calls[0][0], "https://api.mistral.ai/v1/models")
        self.assertEqual(reg.fetch_mistral("m" * 32, FakeHttp([{"error": "HTTP 401"}]), NOW), {"error": "HTTP 401"})


class ConnectionTestTest(unittest.TestCase):
    def test_messages_and_no_secret_echo(self):
        ok, msg = reg.test("anthropic", SECRET, None,
                           lambda s: {"currency": "USD", "spentMonth": 12.5, "limit": None}, NOW)
        self.assertTrue(ok)
        self.assertIn("今月 12.50 USD", msg)
        ok, msg = reg.test("anthropic", SECRET, None, lambda s: {"error": "HTTP 401"}, NOW)
        self.assertFalse(ok)
        self.assertIn("鍵が無効", msg)
        self.assertIn("HTTP 401", msg)
        ok, msg = reg.test("groq", "gsk_" + "g" * 40, FakeHttp([{"data": []}]), None, NOW)
        self.assertEqual((ok, msg), (True, "つながりました（鍵は使えます）"))
        ok, msg = reg.test("openai_usage", "sk-admin-" + "o" * 30, FakeHttp([{"error": "timeout"}]), None, NOW, lang_ja=False)
        self.assertEqual((ok, msg), (False, "No response (timed out)"))
        ok, _m = reg.test("nope", SECRET, None, None, NOW)
        self.assertFalse(ok)
        for pid in reg.BY_ID:
            for err in ("HTTP 401", "HTTP 500", "timeout", "network error", "weird " + SECRET):
                self.assertNotIn(SECRET, reg.error_text(err))              # 相手の言葉や鍵をそのまま返さない


class NoCapRuleTest(unittest.TestCase):
    """上限が取れず手動の予算も無いなら % を出さない（全プロバイダ横断）。予算があれば予算に対する %。"""

    def test_every_api_provider(self):
        home = Path(tempfile.mkdtemp(prefix="pr-cap-"))
        try:
            sb = _load("status_board", home)
            for spec in sb.API_PROVIDERS:
                payload = {"connected": True, "currency": "USD", "spentMonth": 5.0, "limit": None}
                e = sb._api_provider_entry(spec, payload, None)
                self.assertIsNone(e["pct"], spec["id"])
                self.assertEqual(e["note"], "no_limit", spec["id"])
                e2 = sb._api_provider_entry(spec, payload, {"amount": 20.0, "currency": "USD", "period": "monthly"})
                self.assertEqual((e2["pct"], e2["limitSource"]), (25.0, "manual"), spec["id"])
        finally:
            shutil.rmtree(home, ignore_errors=True)

    def test_phone_gauges_carry_no_amounts(self):
        home = Path(tempfile.mkdtemp(prefix="pr-g-"))
        try:
            sb = _load("status_board", home)
            rows = sb._gauges_from_providers([
                {"id": "anthropic", "label": "Anthropic", "kind": "api", "connected": True, "status": "ok",
                 "spentMonth": 12.34, "pct": None, "limit": None},
                {"id": "mistral", "label": "Mistral", "kind": "api", "connected": True, "status": "ok",
                 "spentMonth": None, "pct": 40.0, "limit": 50.0, "limitSource": "manual"},
                {"id": "anthropic", "label": "Anthropic", "kind": "api", "connected": True, "status": "error",
                 "spentMonth": None, "pct": None},
            ])
            self.assertEqual(rows[0], {"id": "anthropic", "label": "Anthropic", "bars": [], "note": "cap-none"})
            self.assertEqual(rows[1]["bars"], [{"k": "月", "pct": 40}])
            self.assertEqual(len(rows), 2)                                   # 失敗している行は出さない（嘘の 0% を見せない）
            self.assertNotIn("12.34", json.dumps(rows))                      # 額は中継に載せない
        finally:
            shutil.rmtree(home, ignore_errors=True)


class ServerTest(unittest.TestCase):
    def setUp(self):
        self.home = Path(tempfile.mkdtemp(prefix="pr-srv-"))
        self.srv = _load("office_server", self.home)

    def tearDown(self):
        shutil.rmtree(self.home, ignore_errors=True)

    def test_keys_status_rows_come_from_the_registry(self):
        (self.home / ".claude").mkdir(parents=True, exist_ok=True)
        (self.home / ".claude" / "office_secrets").write_text("ANTHROPIC_ADMIN_KEY=%s\n" % SECRET, encoding="utf-8")
        rows = {r["id"]: r for r in self.srv.keys_status()["providers"]}
        for p in reg.PROVIDERS:
            r = rows[p["id"]]
            self.assertEqual((r["keyName"], r["kind"], r["mode"]), (p["key"], p["kind"], "key"))
            self.assertEqual(r["budget"], p["id"] != "openai_usage")
        self.assertTrue(rows["anthropic"]["connected"])
        self.assertNotIn(SECRET, json.dumps(rows))
        self.assertEqual(rows["anthropic"]["masked"], SECRET[:5] + "…" + SECRET[-4:])

    def test_provider_test_gates(self):
        calls = []
        self.srv.status_board.provider_registry.test, real = (
            lambda pid, value, get_json, fetch, now, lang_ja=True: (calls.append((pid, value)) or (True, "つながりました")),
            self.srv.status_board.provider_registry.test)
        try:
            self.assertFalse(self.srv.provider_test({"id": "nope"})[0])
            self.assertFalse(self.srv.provider_test({"id": "anthropic"})[0])                     # 保存されていない
            self.assertFalse(self.srv.provider_test({"id": "anthropic", "value": "short"})[0])   # 形式
            ok, _m, extra = self.srv.provider_test({"id": "anthropic", "value": SECRET})
            self.assertTrue(ok)
            self.assertEqual(extra, {"result": {"ok": True, "message": "つながりました"}})
            self.assertNotIn(SECRET, json.dumps(extra))
            self.assertFalse(self.srv.provider_test({"id": "anthropic", "value": SECRET})[0])    # 同じプロバイダは 3 秒に 1 回
            self.assertEqual(calls, [("anthropic", SECRET)])
        finally:
            self.srv.status_board.provider_registry.test = real

    def test_provider_test_never_raises_or_leaks(self):
        """別モデルレビュー: 途中で切れた応答（IncompleteRead）等の例外が POST まで抜けて応答が JSON にならなかった。"""
        import http.client
        real = self.srv.status_board.provider_registry.test

        def boom(*a, **k):
            raise http.client.IncompleteRead(b"partial " + SECRET.encode())
        self.srv.status_board.provider_registry.test = boom
        try:
            ok, _m, extra = self.srv.provider_test({"id": "mistral", "value": "m" * 32})
        finally:
            self.srv.status_board.provider_registry.test = real
        self.assertTrue(ok)
        self.assertFalse(extra["result"]["ok"])
        self.assertNotIn(SECRET, json.dumps(extra))

    def test_fetch_json_turns_incomplete_read_into_an_error(self):
        import http.client
        from unittest import mock
        sb = self.srv.status_board

        class Resp:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def read(self):
                raise http.client.IncompleteRead(b"{")
        with mock.patch.object(sb, "urlopen", lambda *a, **k: Resp()):
            self.assertEqual(sb._fetch_json("https://example.invalid/x", {}), {"error": "取得エラー"})


if __name__ == "__main__":
    unittest.main()
