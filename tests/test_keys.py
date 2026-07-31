#!/usr/bin/env python3
"""連携設定 API の回帰テスト。"""

import importlib.util
import io
import json
import os
import shutil
import stat
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SERVER_FILE = ROOT / "server" / "office_server.py"


class KeysApiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.home = Path(cls._tmp.name)

        old_home = os.environ.get("OFFICE_HOME")
        os.environ["OFFICE_HOME"] = str(cls.home)
        try:
            spec = importlib.util.spec_from_file_location(
                "office_server_keys_test", SERVER_FILE)
            cls.office = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(cls.office)
        finally:
            if old_home is None:
                os.environ.pop("OFFICE_HOME", None)
            else:
                os.environ["OFFICE_HOME"] = old_home

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def setUp(self):
        shutil.rmtree(self.home, ignore_errors=True)
        self.home.mkdir()

    @property
    def secrets_file(self):
        return self.home / ".claude" / "office_secrets"

    def request_json(self, method, path, data=None):
        body = None if data is None else json.dumps(data).encode("utf-8")
        handler = self.office.Handler.__new__(self.office.Handler)
        handler.path = path
        handler.command = method
        handler.request_version = "HTTP/1.1"
        handler.requestline = f"{method} {path} HTTP/1.1"
        handler.headers = {
            "Host": "127.0.0.1:4780",
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

    def test_status_reports_six_providers_without_exposing_key(self):
        self.office.PROJECTS.mkdir(parents=True)
        codex_auth = self.home / ".codex" / "auth.json"
        codex_auth.parent.mkdir(parents=True)
        codex_auth.write_text("{}", encoding="utf-8")

        secret = "sk-abcdefghijklmnopqrstuvwxyz0123456789abcd"
        self.secrets_file.write_text(
            f"OTHER=preserve\nOPENAI_API_KEY={secret}\n", encoding="utf-8")

        code, body = self.request_json("GET", "/api/keys/status")

        self.assertEqual(code, 200)
        providers = body["providers"]
        self.assertEqual(
            [provider["id"] for provider in providers],
            ["claude", "codex", "gemini", "openai_key", "x_api", "openai_usage"],
        )
        self.assertEqual(len(providers), 6)
        by_id = {provider["id"]: provider for provider in providers}
        self.assertTrue(by_id["claude"]["connected"])
        self.assertTrue(by_id["codex"]["connected"])
        self.assertFalse(by_id["gemini"]["connected"])
        self.assertTrue(by_id["openai_key"]["connected"])
        self.assertEqual(by_id["openai_key"]["masked"], secret[:5] + "…" + secret[-4:])
        self.assertFalse(by_id["x_api"]["connected"])
        self.assertFalse(by_id["openai_usage"]["connected"])
        self.assertNotIn("value", by_id["openai_key"])
        self.assertNotIn(secret, json.dumps(body, ensure_ascii=False))

    def test_set_creates_0600_file_and_replaces_only_named_line(self):
        first = "sk-FirstKey_abcdefghijklmnopqrstuvwxyz"
        code, body = self.request_json(
            "POST", "/api/keys/set", {"name": "OPENAI_API_KEY", "value": first})

        self.assertEqual(code, 200)
        self.assertEqual(body, {"ok": True})
        self.assertEqual(self.secrets_file.read_text(encoding="utf-8").splitlines(),
                         [f"OPENAI_API_KEY={first}"])
        self.assertEqual(stat.S_IMODE(self.secrets_file.stat().st_mode), 0o600)

        old = "sk-OldKey_abcdefghijklmnopqrstuvwxyz"
        second = "sk-ReplacedKey_abcdefghijklmnopqrstuvwxyz"
        self.secrets_file.write_text(
            f"OTHER=preserve\nOPENAI_API_KEY={old}\nTRAIL=keep\n", encoding="utf-8")
        self.secrets_file.chmod(0o644)

        code, body = self.request_json(
            "POST", "/api/keys/set", {"name": "OPENAI_API_KEY", "value": second})

        self.assertEqual(code, 200)
        self.assertEqual(body, {"ok": True})
        self.assertEqual(
            self.secrets_file.read_text(encoding="utf-8").splitlines(),
            ["OTHER=preserve", f"OPENAI_API_KEY={second}", "TRAIL=keep"],
        )
        self.assertEqual(stat.S_IMODE(self.secrets_file.stat().st_mode), 0o600)

    def test_set_rejects_unknown_name(self):
        code, body = self.request_json(
            "POST",
            "/api/keys/set",
            {"name": "GEMINI_API_KEY", "value": "sk-valid_abcdefghijklmnopqrstuvwxyz"},
        )

        self.assertEqual(code, 400)
        self.assertFalse(body["ok"])
        self.assertFalse(self.secrets_file.exists())

    def test_set_rejects_invalid_value(self):
        invalid_values = [
            "short",
            "sk-invalid value_abcdefghijklmnopqrstuvwxyz",
            "x" * 301,
            12345,
        ]
        for value in invalid_values:
            with self.subTest(value=value):
                code, body = self.request_json(
                    "POST",
                    "/api/keys/set",
                    {"name": "OPENAI_API_KEY", "value": value},
                )
                self.assertEqual(code, 400)
                self.assertFalse(body["ok"])
        self.assertFalse(self.secrets_file.exists())

    def test_set_and_status_supports_x_and_openai_admin_without_exposing_values(self):
        values = {
            "X_BEARER_TOKEN": "AAAA%3D-token_abcdefghijklmnopqrstuvwxyz",
            "OPENAI_ADMIN_KEY": "sk-admin-abcdefghijklmnopqrstuvwxyz123456",
        }
        for name, value in values.items():
            code, body = self.request_json(
                "POST", "/api/keys/set", {"name": name, "value": value})
            self.assertEqual(code, 200)
            self.assertEqual(body, {"ok": True})

        code, body = self.request_json("GET", "/api/keys/status")
        self.assertEqual(code, 200)
        providers = {provider["id"]: provider for provider in body["providers"]}
        for provider_id, value in (("x_api", values["X_BEARER_TOKEN"]),
                                   ("openai_usage", values["OPENAI_ADMIN_KEY"])):
            self.assertTrue(providers[provider_id]["connected"])
            self.assertEqual(providers[provider_id]["masked"], value[:5] + "…" + value[-4:])
            self.assertNotIn(value, json.dumps(body, ensure_ascii=False))

    def test_new_key_values_reject_bad_characters_and_overlong_values(self):
        for name in ("X_BEARER_TOKEN", "OPENAI_ADMIN_KEY"):
            for value in ("A" * 19, "A" * 301, "A" * 20 + " space"):
                with self.subTest(name=name, value=value):
                    code, body = self.request_json(
                        "POST", "/api/keys/set", {"name": name, "value": value})
                    self.assertEqual(code, 400)
                    self.assertFalse(body["ok"])


if __name__ == "__main__":
    unittest.main()
