# -*- coding: utf-8 -*-
"""scan_office のテスト（OFFICE_HOME フィクスチャで社員一覧を検証）"""
import importlib.util
import os
import shutil
import tempfile
import time
import unittest
from pathlib import Path

TESTS = Path(__file__).resolve().parent
ROOT = TESTS.parent
FX = TESTS / "fixtures"

_home = Path(tempfile.mkdtemp(prefix="office_scan_home_"))
os.environ["OFFICE_HOME"] = str(_home)
spec = importlib.util.spec_from_file_location(
    "office_server_scan", ROOT / "server" / "office_server.py")
office = importlib.util.module_from_spec(spec)
spec.loader.exec_module(office)


def put_session(proj, name, fixture, age):
    d = _home / ".claude" / "projects" / proj
    d.mkdir(parents=True, exist_ok=True)
    p = d / name
    shutil.copy(FX / fixture, p)
    t = time.time() - age
    os.utime(p, (t, t))
    return p


class ScanOfficeTest(unittest.TestCase):
    def setUp(self):
        proj_root = _home / ".claude" / "projects"
        if proj_root.exists():
            shutil.rmtree(proj_root)
        office._cache["t"] = 0.0

    def test_scan_finds_and_numbers_employees(self):
        put_session("-Users-test-demo-project", "sess-aaaa0001.jsonl", "working_tool.jsonl", age=10)
        put_session("-Users-test-demo-project", "sess-aaaa0002.jsonl", "waiting_said.jsonl", age=600)
        data = office.scan_office()
        emps = data["employees"]
        self.assertEqual(len(emps), 2)
        disps = sorted(e["disp"] for e in emps)
        self.assertEqual(disps[0], "demo-project")
        self.assertTrue(disps[1].startswith("demo-project 2号"))
        self.assertEqual(data["counts"]["working"], 1)
        self.assertEqual(data["counts"]["waiting"], 1)

    def test_old_sessions_hidden(self):
        put_session("-Users-test-demo-project", "sess-old0001.jsonl", "waiting_said.jsonl",
                    age=office.SHOW_WINDOW + 100)
        data = office.scan_office()
        self.assertEqual(len(data["employees"]), 0)

    def test_sprite_fallback(self):
        put_session("-Users-test-demo-project", "sess-aaaa0003.jsonl", "working_tool.jsonl", age=10)
        data = office.scan_office()
        spr = data["employees"][0]["sprite"]
        self.assertTrue(spr == "" or spr.startswith("/assets/"))


if __name__ == "__main__":
    unittest.main()
