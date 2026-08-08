# -*- coding: utf-8 -*-
"""R82: クイック定型文（ユーザー定義・スマホ同期）の契約テスト。
- 保存は正規化（strip/上限）・0600・壊れたファイルは空扱い
- office_json.templates は label/text のみ（中継搬送は設計意図＝形をここでピン）"""
import importlib.util
import json
import os
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def load_office(home):
    os.environ["OFFICE_HOME"] = str(home)
    os.environ["OFFICE_DATA"] = str(home)
    os.environ["OFFICE_CONFIG"] = str(home / "office_config.json")
    spec = importlib.util.spec_from_file_location(
        "office_templates_test", ROOT / "server" / "office_server.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class TemplatesTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        home = Path(self.tmp.name)
        (home / ".claude").mkdir(parents=True)
        self._env = {k: os.environ.get(k) for k in
                     ("OFFICE_HOME", "OFFICE_DATA", "OFFICE_CONFIG")}
        self.office = load_office(home)
        self.home = home

    def tearDown(self):
        for k, v in self._env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        self.tmp.cleanup()

    def test_save_load_roundtrip_and_mode(self):
        ok, msg = self.office.save_templates(
            [{"label": " ビルド確認 ", "text": " verifyを回して結果を1行で報告して "}])
        self.assertTrue(ok, msg)
        rows = self.office.load_templates()
        self.assertEqual(rows, [{"label": "ビルド確認",
                                 "text": "verifyを回して結果を1行で報告して"}])
        mode = self.office.TEMPLATES_FILE.stat().st_mode & 0o777
        self.assertEqual(0o600, mode)

    def test_limits_and_invalid(self):
        ok, _ = self.office.save_templates([{"label": "a", "text": "b"}] * 9)
        self.assertFalse(ok)
        ok, _ = self.office.save_templates([{"label": "", "text": "b"}])
        self.assertFalse(ok)
        ok, _ = self.office.save_templates([{"label": "a", "text": "x" * 121}])
        self.assertFalse(ok)
        ok, _ = self.office.save_templates("not-a-list")
        self.assertFalse(ok)

    def test_broken_file_is_empty(self):
        self.office.TEMPLATES_FILE.write_text("{broken", encoding="utf-8")
        self.assertEqual([], self.office.load_templates())

    def test_office_json_carries_label_text_only(self):
        self.office.save_templates([{"label": "承認", "text": "はい、進めてください"}])
        data = self.office.office_json()
        self.assertEqual(data["templates"],
                         [{"label": "承認", "text": "はい、進めてください"}])


if __name__ == "__main__":
    unittest.main()
