#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""R63: APIプロバイダ統合コスト（OpenRouter/Kimi/DeepSeek/Groq）の単体テスト。

ネットワークは一切叩かない（_fetch_json をモック）。実キー疎通は本番での手動確認。
検証の核:
  - limit=null（無制限）を 0 と混同せず pct=null・note="no_limit" にする
  - DeepSeek の文字列 balance を float 化し currency(CNY) を運ぶ
  - キー未登録のプロバイダは **リクエストを発行しない**（provider にも出さない）
  - 手動予算のバリデーション（負値/巨大値/不正通貨/未知provider）
"""
import importlib.util
import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
NOW = 1785600000.0


def load_status(home):
    old = os.environ.get("OFFICE_HOME")
    os.environ["OFFICE_HOME"] = str(home)
    try:
        spec = importlib.util.spec_from_file_location(
            f"status_board_api_{id(home)}", ROOT / "server" / "status_board.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        if old is None:
            os.environ.pop("OFFICE_HOME", None)
        else:
            os.environ["OFFICE_HOME"] = old


class ApiProviderTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="sbapi_"))
        (self.tmp / ".claude").mkdir(parents=True)
        self.sb = load_status(self.tmp)
        self.calls = []

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _keys(self, **pairs):
        """office_secrets へキーを書く（値は形式検証を通る長さにする）。"""
        lines = [f"{name}={value}" for name, value in pairs.items()]
        path = self.tmp / ".claude" / "office_secrets"
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        os.chmod(path, 0o600)

    def _mock_fetch(self, responses):
        """URL部分一致でレスポンスを返す _fetch_json モック（呼び出しURLを記録）。"""
        def fake(url, headers, timeout=6):
            self.calls.append(url)
            for fragment, payload in responses.items():
                if fragment in url:
                    return payload
            return {"error": "HTTP 404"}
        self.sb._fetch_json = fake

    def _providers(self):
        return {p["id"]: p for p in self.sb.collect_api_providers(NOW)}

    # ---- OpenRouter ----
    def test_openrouter_with_api_limit(self):
        self._keys(OPENROUTER_API_KEY="sk-or-v1-" + "a" * 40)
        self._mock_fetch({"openrouter.ai/api/v1/key": {"data": {
            "label": "sk-or-v1-au7...890", "limit": 100, "limit_remaining": 74.5,
            "limit_reset": "monthly", "usage": 25.5, "usage_monthly": 25.5,
            "byok_usage_monthly": 17.38, "is_free_tier": False}}})
        p = self._providers()["openrouter"]
        self.assertEqual(p["kind"], "api")
        self.assertTrue(p["connected"])
        self.assertEqual((p["spentMonth"], p["limit"], p["limitSource"]), (25.5, 100.0, "api"))
        self.assertEqual(p["pct"], 25.5)
        self.assertEqual(p["currency"], "USD")
        self.assertEqual(p["note"], "")

    def test_openrouter_null_limit_is_not_zero(self):
        """limit=null は「無制限」＝pctを出さず note=no_limit（0と混同しない）。"""
        self._keys(OPENROUTER_API_KEY="sk-or-v1-" + "b" * 40)
        self._mock_fetch({"openrouter.ai/api/v1/key": {"data": {
            "limit": None, "limit_remaining": None, "limit_reset": None,
            "usage_monthly": 3.42, "is_free_tier": False}}})
        p = self._providers()["openrouter"]
        self.assertEqual(p["spentMonth"], 3.42)
        self.assertIsNone(p["limit"])
        self.assertIsNone(p["limitSource"])
        self.assertIsNone(p["pct"])
        self.assertEqual(p["note"], "no_limit")

    def test_manual_budget_fills_missing_limit(self):
        """上限が取れないときだけ手動予算を使い pct を出す。"""
        self._keys(OPENROUTER_API_KEY="sk-or-v1-" + "c" * 40)
        self._mock_fetch({"openrouter.ai/api/v1/key": {"data": {
            "limit": None, "usage_monthly": 2.5}}})
        ok, _msg = self.sb.budget_apply({"provider": "openrouter", "amount": 10, "currency": "USD"})
        self.assertTrue(ok)
        p = self._providers()["openrouter"]
        self.assertEqual((p["limit"], p["limitSource"], p["pct"]), (10.0, "manual", 25.0))
        self.assertEqual(p["note"], "")

    def test_api_limit_wins_over_manual_budget(self):
        self._keys(OPENROUTER_API_KEY="sk-or-v1-" + "d" * 40)
        self._mock_fetch({"openrouter.ai/api/v1/key": {"data": {
            "limit": 50, "usage_monthly": 5}}})
        self.sb.budget_apply({"provider": "openrouter", "amount": 999, "currency": "USD"})
        p = self._providers()["openrouter"]
        self.assertEqual((p["limit"], p["limitSource"]), (50.0, "api"))

    # ---- Kimi / DeepSeek ----
    def test_moonshot_balance_only(self):
        self._keys(MOONSHOT_API_KEY="sk-" + "e" * 40)
        self._mock_fetch({"api.moonshot.ai": {"data": {
            "available_balance": 12.34, "voucher_balance": 0, "cash_balance": 12.34}}})
        p = self._providers()["moonshot"]
        self.assertEqual(p["balance"], 12.34)
        self.assertIsNone(p["spentMonth"])
        self.assertIsNone(p["pct"])
        self.assertEqual(p["note"], "no_limit")

    def test_deepseek_string_balance_and_cny(self):
        """DeepSeek は値が文字列・通貨がCNYのことがある（推測レートを作らない）。"""
        self._keys(DEEPSEEK_API_KEY="sk-" + "f" * 40)
        self._mock_fetch({"api.deepseek.com": {"is_available": True, "balance_infos": [
            {"currency": "CNY", "total_balance": "110.00",
             "granted_balance": "10.00", "topped_up_balance": "100.00"}]}})
        p = self._providers()["deepseek"]
        self.assertEqual(p["balance"], 110.0)
        self.assertEqual(p["currency"], "CNY")

    # ---- Groq（消費APIなし） ----
    def test_groq_has_no_api_and_uses_budget(self):
        self._keys(GROQ_API_KEY="gsk_" + "g" * 40)
        self._mock_fetch({})
        p = self._providers()["groq"]
        self.assertTrue(p["connected"])
        self.assertTrue(p.get("noApi"))
        self.assertIsNone(p["spentMonth"])
        self.assertEqual(p["note"], "no_limit")
        self.assertEqual(self.calls, [], "Groqは消費APIが無い＝リクエストしない")

    # ---- 未登録・エラー ----
    def test_unregistered_provider_is_never_requested(self):
        """キー未登録のプロバイダは provider に出さず、HTTPも発行しない。"""
        self._keys(OPENROUTER_API_KEY="sk-or-v1-" + "h" * 40)
        self._mock_fetch({"openrouter.ai/api/v1/key": {"data": {"usage_monthly": 1, "limit": None}}})
        ids = set(self._providers())
        self.assertEqual(ids, {"openrouter"})
        self.assertTrue(all("openrouter.ai" in url for url in self.calls))
        self.assertFalse(any("moonshot" in u or "deepseek" in u for u in self.calls))

    def test_error_response_is_safe(self):
        self._keys(MOONSHOT_API_KEY="sk-" + "i" * 40)
        self._mock_fetch({"api.moonshot.ai": {"error": "HTTP 401"}})
        p = self._providers()["moonshot"]
        self.assertEqual(p["status"], "error")
        self.assertEqual(p["error"], "HTTP 401")
        self.assertIsNone(p["pct"])

    def test_broken_shape_is_invalid_response(self):
        self._keys(DEEPSEEK_API_KEY="sk-" + "j" * 40)
        self._mock_fetch({"api.deepseek.com": {"balance_infos": []}})
        p = self._providers()["deepseek"]
        self.assertEqual(p["status"], "error")


class BudgetApplyTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="sbbudget_"))
        (self.tmp / ".claude").mkdir(parents=True)
        self.sb = load_status(self.tmp)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_valid_budget_roundtrip_and_removal(self):
        ok, msg = self.sb.budget_apply({"provider": "groq", "amount": 25, "currency": "USD"})
        self.assertTrue(ok, msg)
        data = json.loads((self.tmp / ".claude" / "office_resources.json").read_text(encoding="utf-8"))
        self.assertEqual(data["budgets"]["groq"],
                         {"amount": 25.0, "currency": "USD", "period": "monthly"})
        ok, msg = self.sb.budget_apply({"provider": "groq", "amount": 0})
        self.assertTrue(ok)
        self.assertEqual(msg, "removed")
        data = json.loads((self.tmp / ".claude" / "office_resources.json").read_text(encoding="utf-8"))
        self.assertNotIn("groq", data["budgets"])

    def test_rejects_bad_input(self):
        for payload in (
            {"provider": "unknown", "amount": 10},
            {"provider": "groq", "amount": -5},
            {"provider": "groq", "amount": 10_000_000},
            {"provider": "groq", "amount": 10, "currency": "BTC"},
            {"provider": "groq", "amount": "たくさん"},
            "not a dict",
        ):
            ok, _msg = self.sb.budget_apply(payload)
            self.assertFalse(ok, f"通ってはいけない: {payload}")

    def test_budget_preserves_existing_ledger(self):
        ok, msg = self.sb.spend_apply({"op": "upsert", "item": {
            "label": "Claude Max", "amount": 200, "currency": "usd", "kind": "sub"}})
        self.assertTrue(ok, msg)
        self.sb.budget_apply({"provider": "openrouter", "amount": 10, "currency": "USD"})
        data = json.loads((self.tmp / ".claude" / "office_resources.json").read_text(encoding="utf-8"))
        self.assertEqual(len(data["spend"]), 1)
        self.assertIn("openrouter", data["budgets"])


if __name__ == "__main__":
    unittest.main()
