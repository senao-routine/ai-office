import importlib.util
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]


class SpriteSetTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.data_dir = Path(cls.tmp.name) / "data"
        cls.assets_dir = cls.data_dir / "assets"
        cls.assets_dir.mkdir(parents=True)
        for name in ("generic_f.png", "bad-name.png", "generic_f__night.png"):
            (cls.assets_dir / name).write_bytes(b"png")
        cls.config_path = Path(cls.tmp.name) / "office_config.json"
        cls.env = mock.patch.dict(os.environ, {
            "OFFICE_CONFIG": str(cls.config_path),
            "OFFICE_DATA": str(cls.data_dir),
            "OFFICE_HOME": str(Path(cls.tmp.name) / "home"),
        })
        cls.env.start()
        spec = importlib.util.spec_from_file_location(
            "office_server_sprite_set_test", ROOT / "server" / "office_server.py")
        cls.office_server = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.office_server)

    @classmethod
    def tearDownClass(cls):
        cls.env.stop()
        cls.tmp.cleanup()

    def setUp(self):
        self.initial = {
            "projects": {
                "work/alpha": {
                    "name": "Alpha", "role": "実装", "sprite": "generic_m.png",
                },
                "work": {
                    "name": "Work", "role": "管理", "sprite": "generic_m.png",
                },
            },
        }
        self.config_path.write_text(
            json.dumps(self.initial, ensure_ascii=False), encoding="utf-8")

    def post(self, payload):
        raw = json.dumps(payload).encode("utf-8")
        handler = object.__new__(self.office_server.Handler)
        handler.path = "/api/sprite/set"
        handler.headers = {
            "Host": "127.0.0.1",
            "X-Office-Local": "1",
            "Content-Length": str(len(raw)),
        }
        handler.rfile = io.BytesIO(raw)
        sent = {}

        def capture(code, body, ctype):
            sent.update(code=code, body=json.loads(body), ctype=ctype)

        handler._send = capture
        handler.do_POST()
        return sent

    def read_config(self):
        return json.loads(self.config_path.read_text(encoding="utf-8"))

    def test_sets_existing_project_by_cwd_substring(self):
        result = self.post({
            "cwd": "/Users/test/work/alpha/src", "sprite": "generic_f.png",
        })

        self.assertEqual(200, result["code"])
        self.assertEqual({"ok": True}, result["body"])
        config = self.read_config()
        self.assertEqual("generic_f.png", config["projects"]["work/alpha"]["sprite"])
        self.assertEqual("generic_m.png", config["projects"]["work"]["sprite"])

    def test_unknown_cwd_returns_400_without_adding_project(self):
        result = self.post({
            "cwd": "/Users/test/unknown", "sprite": "generic_f.png",
        })

        self.assertEqual(400, result["code"])
        self.assertFalse(result["body"]["ok"])
        self.assertEqual(self.initial, self.read_config())

    def test_invalid_sprite_name_returns_400(self):
        result = self.post({"cwd": "work/alpha", "sprite": "bad-name.png"})

        self.assertEqual(400, result["code"])
        self.assertFalse(result["body"]["ok"])
        self.assertEqual(self.initial, self.read_config())

    def test_theme_sprite_is_rejected_even_when_it_exists(self):
        result = self.post({
            "cwd": "work/alpha", "sprite": "generic_f__night.png",
        })

        self.assertEqual(400, result["code"])
        self.assertFalse(result["body"]["ok"])
        self.assertEqual(self.initial, self.read_config())


if __name__ == "__main__":
    unittest.main()
