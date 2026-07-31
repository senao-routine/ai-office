#!/usr/bin/env python3
"""R4 写真アップロードのAPI境界とtheme_gen引数を、生成なしで検証する。"""
import base64
import io
import importlib.util
import json
import os
import sys
import tempfile
import time
import types
import unittest
from unittest import mock
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SERVER_PATH = ROOT / "server" / "office_server.py"
THEME_GEN_PATH = ROOT / "tools" / "theme_gen.py"
PNG_1X1 = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAusB9Y9Zl5QAAAAASUVORK5CYII="
)


def load_module(path, prefix):
    name = f"{prefix}_{time.time_ns()}"
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return name, module


class PhotoUploadApiTest(unittest.TestCase):
    ENV_KEYS = ("OFFICE_HOME", "OFFICE_CONFIG", "OFFICE_DATA", "OFFICE_FAKE_GEN")

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.home = self.tmp / "home"
        self.project = self.home / "photo-project"
        self.data = self.tmp / "data"
        self.config_path = self.tmp / "office_config.json"
        self.marker = self.tmp / "fake_gen.marker"
        self.project.mkdir(parents=True)
        (self.data / "assets").mkdir(parents=True)
        self.config_path.write_text(
            json.dumps(
                {
                    "projects": {
                        str(self.project): {
                            "name": "写真テスト部",
                            "role": "テスト",
                            "sprite": "generic_f.png",
                        }
                    }
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        self._old_env = {key: os.environ.get(key) for key in self.ENV_KEYS}
        os.environ.update(
            {
                "OFFICE_HOME": str(self.home),
                "OFFICE_CONFIG": str(self.config_path),
                "OFFICE_DATA": str(self.data),
                "OFFICE_FAKE_GEN": str(self.marker),
            }
        )
        self.module_name, self.office = load_module(SERVER_PATH, "office_server_photo")

    def tearDown(self):
        sys.modules.pop(self.module_name, None)
        for key, value in self._old_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        self._tmp.cleanup()

    def post(self, path, payload=None, raw=None):
        body = raw if raw is not None else json.dumps(payload).encode("utf-8")
        handler = self.office.Handler.__new__(self.office.Handler)
        handler.path = path
        handler.headers = {
            "Host": "127.0.0.1",
            "Content-Type": "application/json",
            "Content-Length": str(len(body)),
            "X-Office-Local": "1",
        }
        handler.rfile = io.BytesIO(body)
        captured = {}

        def capture(_handler, code, response_body, ctype):
            captured.update(code=code, body=response_body, ctype=ctype)

        handler._send = types.MethodType(capture, handler)
        handler.do_POST()
        response_body = captured["body"]
        try:
            decoded = json.loads(response_body)
        except json.JSONDecodeError:
            decoded = None
        return captured["code"], decoded

    def photo_payload(self, cwd=None, image=None, data_url=True):
        encoded = base64.b64encode(PNG_1X1 if image is None else image).decode("ascii")
        if data_url:
            encoded = "data:image/png;base64," + encoded
        return {"cwd": str(self.project) if cwd is None else cwd, "imageB64": encoded}

    def wait_for_state(self, slug, expected="done"):
        deadline = time.monotonic() + 4
        while time.monotonic() < deadline:
            state = self.office.GEN_STATUS.get(slug, {}).get("state")
            if state == expected:
                return state
            if state == "error":
                self.fail(f"生成がerrorになりました: {self.office.GEN_STATUS[slug]}")
            time.sleep(0.02)
        self.fail(f"生成状態が{expected}になりません: {self.office.GEN_STATUS.get(slug)}")

    def test_upload_png_fake_generation_updates_config_and_deletes_photo(self):
        status, response = self.post("/api/sprite/upload", self.photo_payload())

        self.assertEqual(200, status)
        self.assertTrue(response["ok"])
        self.assertTrue(response["genStarted"])
        slug = response["slug"]
        self.assertTrue(slug)
        self.wait_for_state(slug)
        self.assertTrue(self.marker.is_file())

        # 2026-07-17 スタイル一本化: 写真キャラ生成は既定スタイル（vintageスロット）2枚のみ
        expected = {
            f"{slug}.png",
            f"{slug}_walk.png",
        }
        self.assertEqual(expected, {path.name for path in (self.data / "assets").glob(f"{slug}*.png")})
        config = json.loads(self.config_path.read_text(encoding="utf-8"))
        self.assertEqual(f"{slug}.png", config["projects"][str(self.project)]["sprite"])
        upload_tmp = self.data / "upload_tmp"
        self.assertTrue(upload_tmp.is_dir())
        self.assertEqual([], list(upload_tmp.iterdir()), "写真の一時ファイルが残っています")

    def test_unknown_cwd_is_rejected(self):
        status, response = self.post(
            "/api/sprite/upload", self.photo_payload(cwd=str(self.home / "unknown"))
        )
        self.assertEqual(400, status)
        self.assertFalse(response["ok"])

    def test_broken_base64_is_rejected(self):
        status, response = self.post(
            "/api/sprite/upload",
            {"cwd": str(self.project), "imageB64": "%%%broken%%%"},
        )
        self.assertEqual(400, status)
        self.assertFalse(response["ok"])

    def test_non_image_magic_is_rejected(self):
        status, response = self.post(
            "/api/sprite/upload", self.photo_payload(image=b"plain text", data_url=False)
        )
        self.assertEqual(400, status)
        self.assertFalse(response["ok"])

    def test_image_over_five_mib_is_rejected(self):
        oversized = b"\x89PNG\r\n\x1a\n" + b"\0" * (5 * 1024 * 1024)
        status, response = self.post(
            "/api/sprite/upload", self.photo_payload(image=oversized, data_url=False)
        )
        self.assertEqual(400, status)
        self.assertFalse(response["ok"])

    def test_photo_is_deleted_when_all_mocked_generations_fail(self):
        os.environ.pop("OFFICE_FAKE_GEN", None)
        failed = types.SimpleNamespace(returncode=1, stdout="", stderr="mock failure")
        with mock.patch.object(self.office.subprocess, "run", return_value=failed):
            status, response = self.post("/api/sprite/upload", self.photo_payload())
            self.assertEqual(200, status)
            self.wait_for_state(response["slug"], expected="error")
        upload_tmp = self.data / "upload_tmp"
        self.assertTrue(upload_tmp.is_dir())
        self.assertEqual([], list(upload_tmp.iterdir()), "失敗時も写真を残してはいけません")

    def test_other_post_routes_still_read_at_most_100kb(self):
        # 全文なら有効で投函成功するJSONだが、100KBで切られるためJSON不正→400になる。
        raw = json.dumps(
            {"session": "session-cap-test", "text": "ok", "padding": "x" * 110_000}
        ).encode("utf-8")
        status, response = self.post("/api/instruct", raw=raw)
        self.assertGreater(len(raw), 100_000)
        self.assertEqual(400, status)
        self.assertFalse(response["ok"])
        self.assertFalse((self.home / ".claude" / "office_inbox" / "session-cap-test.json").exists())


class ThemeGenPhotoArgparseTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.module_name, cls.theme_gen = load_module(THEME_GEN_PATH, "theme_gen_photo")

    @classmethod
    def tearDownClass(cls):
        sys.modules.pop(cls.module_name, None)

    def test_custom_accepts_photo_ref(self):
        args = self.theme_gen.build_parser().parse_args(
            ["modern", "custom", "photo_person", "Photo Person", "--photo-ref", "/tmp/photo.png"]
        )
        self.assertEqual("modern", args.theme)
        self.assertEqual(Path("/tmp/photo.png"), args.photo_ref)

    def test_vintage_accepts_custom_and_walkframes_only_parser_paths(self):
        parser = self.theme_gen.build_parser()
        custom = parser.parse_args(
            ["vintage", "custom", "photo_person", "Photo Person", "--photo-ref", "/tmp/photo.png"]
        )
        walking = parser.parse_args(["vintage", "walkframes", "generic_m"])
        self.assertEqual("vintage", custom.theme)
        self.assertEqual(Path("/tmp/photo.png"), custom.photo_ref)
        self.assertEqual("walkframes", walking.theme_command)


if __name__ == "__main__":
    unittest.main()
