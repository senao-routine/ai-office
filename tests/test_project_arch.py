"""Accessory persistence and the local-only API boundary."""
import importlib.util
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, Mock

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("office_arch_test", ROOT / "server/office_server.py")
office = importlib.util.module_from_spec(_spec)
# Import under an isolated home; no real config or state is used by these tests.
with tempfile.TemporaryDirectory() as _home, patch.dict(os.environ, {"OFFICE_HOME": _home}):
    _spec.loader.exec_module(office)


class ProjectArchTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.cfg = Path(self.tmp.name) / "office_config.json"
        self.original = {"lang": "en", "projects": {"fixture/project": {"name": "Demo", "role": "coding"},
                                                  "fixture/other": {"name": "Other"}}}
        self.cfg.write_text(json.dumps(self.original), encoding="utf-8")
        self.pid = "012345abcdef"
        self.rows = {"roster": [{"projectId": self.pid, "cwd": "/fixture/project"}]}
        for target, value in (("config_file", lambda: self.cfg), ("office_json", lambda: self.rows)):
            patcher = patch.object(office, target, value)
            patcher.start(); self.addCleanup(patcher.stop)
        patcher = patch.object(office.projects_index, "projects_json", return_value={"projects": []})
        patcher.start(); self.addCleanup(patcher.stop)

    def test_every_choice_roundtrips_without_losing_other_settings(self):
        for arch in [*office.PROJECT_ARCHES, None]:
            with patch.object(office, "_file_flock", wraps=office._file_flock) as lock:
                self.assertTrue(office.set_project_arch(self.pid, arch)[0])
                lock.assert_called_once_with(self.cfg)
            saved = json.loads(self.cfg.read_text())
            self.assertEqual(saved["projects"]["fixture/project"], {**self.original["projects"]["fixture/project"], "arch": arch})
            self.assertEqual(saved["lang"], "en")
            self.assertEqual(saved["projects"]["fixture/other"], {"name": "Other"})
            self.assertEqual(office._cache["t"], 0)
            grouped = office.group_by_project([{"session": "a", "cwd": "/fixture/project", "arch": arch}])
            self.assertEqual(grouped[0]["arch"], arch)

    def test_invalid_unknown_and_broken_config_are_not_written(self):
        before = self.cfg.read_bytes()
        for pid, arch in [(self.pid, "dev"), (self.pid, {}), (self.pid, False),
                          ("../project", "cap"), ("A" * 12, None), (None, None), ("0" * 12, None)]:
            self.assertFalse(office.set_project_arch(pid, arch)[0])
            self.assertEqual(self.cfg.read_bytes(), before)
        self.cfg.write_text("{broken")
        self.assertFalse(office.set_project_arch(self.pid, "cap")[0])
        self.assertEqual(self.cfg.read_text(), "{broken")

    def test_endpoint_requires_loopback_csrf_and_explicit_arch(self):
        for addr, headers, payload, code in [
            ("192.0.2.1", {"X-Office-Local": "1"}, {"arch": "cap"}, 403),
            ("127.0.0.1", {}, {"arch": "cap"}, 403),
            ("127.0.0.1", {"X-Office-Local": "1", "Origin": "https://example.invalid"}, {"arch": "cap"}, 403),
            ("127.0.0.1", {"X-Office-Local": "1"}, {}, 400),
        ]:
            handler = object.__new__(office.Handler)
            handler.path = "/api/project/arch"; handler.client_address = (addr, 12345)
            body = json.dumps({"projectId": self.pid, **payload}).encode()
            handler.headers = {"Host": "127.0.0.1:4797", "Content-Length": str(len(body)), **headers}
            handler.rfile = io.BytesIO(body); handler._deny = Mock(); handler._send = Mock()
            with patch.object(office, "set_project_arch") as write:
                handler.do_POST(); write.assert_not_called()
            self.assertEqual(handler._deny.call_args.args[0], code)


if __name__ == "__main__":
    unittest.main()
