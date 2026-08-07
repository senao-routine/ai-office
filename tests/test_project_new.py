# -*- coding: utf-8 -*-
"""P1 ➕新プロジェクト登録のテスト（add_project / pick_folder / launch はモック注入口経由）"""
import importlib.util
import json
import os
import sys
import tempfile
import types
import unittest
from pathlib import Path

TESTS = Path(__file__).resolve().parent
ROOT = TESTS.parent

_home = Path(tempfile.mkdtemp(prefix="office_p1_home_"))
os.environ.setdefault("OFFICE_HOME", str(_home))
spec = importlib.util.spec_from_file_location(
    "office_server_p1", ROOT / "server" / "office_server.py")
office = importlib.util.module_from_spec(spec)
spec.loader.exec_module(office)


class ProjectNewTest(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(prefix="office_p1_"))
        self.cfg = self.dir / "office_config.json"
        self.cfg.write_text(json.dumps(
            {"_使い方": "テスト", "projects": {"Downloads/works": {"name": "既存", "role": "r"}}},
            ensure_ascii=False), encoding="utf-8")
        os.environ["OFFICE_CONFIG"] = str(self.cfg)
        self.proj = self.dir / "新しいアプリ"
        self.proj.mkdir()

    def tearDown(self):
        os.environ.pop("OFFICE_CONFIG", None)
        os.environ.pop("OFFICE_PICK_DIR", None)
        os.environ.pop("OFFICE_FAKE_LAUNCH", None)

    def test_add_project_inserts_at_top(self):
        ok, msg, info = office.add_project(str(self.proj), "テスト部", "検証")
        self.assertTrue(ok, msg)
        cfg = json.loads(self.cfg.read_text(encoding="utf-8"))
        keys = list(cfg["projects"].keys())
        self.assertEqual(len(keys), 2)
        # 先頭に挿入＝広い既存パターンより優先マッチ
        self.assertEqual(cfg["projects"][keys[0]]["name"], "テスト部")
        self.assertEqual(cfg["projects"][keys[1]]["name"], "既存")
        self.assertEqual(cfg["_使い方"], "テスト")  # projects以外のキーを保存
        self.assertFalse(info["existing"])

    def test_add_project_rejects_missing_dir(self):
        ok, _msg, _ = office.add_project(str(self.dir / "nai"), "x", "")
        self.assertFalse(ok)
        ok, _msg, _ = office.add_project("", "x", "")
        self.assertFalse(ok)

    def test_add_project_duplicate_updates_not_duplicates(self):
        office.add_project(str(self.proj), "一回目", "")
        ok, _msg, info = office.add_project(str(self.proj), "二回目", "役割2")
        self.assertTrue(ok)
        self.assertTrue(info["existing"])
        cfg = json.loads(self.cfg.read_text(encoding="utf-8"))
        self.assertEqual(len(cfg["projects"]), 2)  # 重複エントリを作らない
        top = cfg["projects"][list(cfg["projects"])[0]]
        self.assertEqual(top["name"], "二回目")
        self.assertEqual(top["role"], "役割2")

    def test_broken_config_not_clobbered(self):
        self.cfg.write_text("{壊れたjson", encoding="utf-8")
        ok, _msg, _ = office.add_project(str(self.proj), "x", "")
        self.assertFalse(ok)
        self.assertEqual(self.cfg.read_text(encoding="utf-8"), "{壊れたjson")




    def test_rejects_home_and_broad_parents(self):
        ok, _msg, _ = office.add_project(str(Path.home()), "乗っ取り部", "")
        self.assertFalse(ok)  # ホーム自体は全社員を部分マッチで乗っ取るので拒否
        ok, _msg, _ = office.add_project(str(Path.home().parent), "親", "")
        self.assertFalse(ok)

    def test_rejects_non_string_path(self):
        ok, _msg, _ = office.add_project(123, "x", "")   # 型不正は接続断でなく False
        self.assertFalse(ok)



    def test_pick_folder_mock(self):
        os.environ["OFFICE_PICK_DIR"] = str(self.proj)
        ok, path = office.pick_folder()
        self.assertTrue(ok)
        self.assertEqual(path, str(self.proj))
        os.environ["OFFICE_PICK_DIR"] = str(self.dir / "nai")
        ok, _ = office.pick_folder()
        self.assertFalse(ok)

    def test_launch_mock_marker(self):
        marker = self.dir / "launched.txt"
        os.environ["OFFICE_FAKE_LAUNCH"] = str(marker)
        ok, _msg, info = office.add_project(str(self.proj), "起動部", "", launch=True)
        self.assertTrue(ok)
        self.assertTrue(info["launched"])
        self.assertEqual(marker.read_text(encoding="utf-8"), str(self.proj.resolve()))


if __name__ == "__main__":
    unittest.main()
