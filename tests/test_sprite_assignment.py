# -*- coding: utf-8 -*-
"""未登録プロジェクトの役割別・汎用プール sprite 割当テスト。"""
import importlib.util
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]


class SpriteAssignmentTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location(
            "office_server_sprite_assignment_test",
            ROOT / "server" / "office_server.py",
        )
        cls.office = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.office)

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.assets = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def touch(self, *names):
        for name in names:
            (self.assets / name).write_bytes(b"png")

    def sprite_url(self, name, avatar, hint=""):
        with patch.object(self.office, "ASSETS", self.assets):
            return self.office.sprite_url(name, avatar, hint=hint)

    def test_role_keyword_selects_video_and_walk_sprite(self):
        self.touch("video.png", "video_walk.png", "generic_f.png")

        self.assertEqual(
            self.sprite_url("", 3, "20260712 premierePlugin"),
            ("/assets/video.png", "/assets/video_walk.png"),
        )

    def test_configured_sprite_stays_first(self):
        self.touch("video.png", "blog.png", "generic_f.png")

        self.assertEqual(
            self.sprite_url("blog.png", 0, "video project"),
            ("/assets/blog.png", ""),
        )

    def test_generic_pool_uses_existing_files_and_is_deterministic(self):
        self.touch("generic_f.png", "generic_m.png", "generic_f3.png")

        self.assertEqual(self.sprite_url("", 0, "unknown"), ("/assets/generic_f.png", ""))
        self.assertEqual(self.sprite_url("", 1, "unknown"), ("/assets/generic_m.png", ""))
        self.assertEqual(self.sprite_url("", 2, "unknown"), ("/assets/generic_f3.png", ""))
        self.assertEqual(self.sprite_url("", 12, "unknown"), ("/assets/generic_f.png", ""))
        self.assertEqual(self.sprite_url("", 12, "unknown"), self.sprite_url("", 12, "unknown"))

    def test_missing_pool_members_are_skipped(self):
        self.touch("generic_m3.png")

        self.assertEqual(
            self.sprite_url("", 0, "unknown"),
            ("/assets/generic_m3.png", ""),
        )


if __name__ == "__main__":
    unittest.main()
