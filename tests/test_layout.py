import copy
import importlib.util
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SERVER_FILE = ROOT / "server" / "office_server.py"

DEFAULT_LAYOUT = {
    "desks": [
        {"dx": 336, "dy": 408}, {"dx": 456, "dy": 408},
        {"dx": 576, "dy": 408}, {"dx": 696, "dy": 408},
        {"dx": 336, "dy": 488}, {"dx": 456, "dy": 488},
        {"dx": 576, "dy": 488}, {"dx": 696, "dy": 488},
    ],
    "sofa": {"x": 864, "y": 336, "w": 96},
    "door": {"x": 530, "y": 625},
}


class LayoutApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.home = Path(cls._tmp.name) / "home"
        cls.data = Path(cls._tmp.name) / "data"
        cls.home.mkdir()
        cls.data.mkdir()
        env_names = ("OFFICE_HOME", "OFFICE_DATA", "OFFICE_LAYOUT", "OFFICE_CONFIG")
        cls._old_env = {name: os.environ.get(name) for name in env_names}
        cls.addClassCleanup(cls._tmp.cleanup)

        def restore_env():
            for name, value in cls._old_env.items():
                if value is None:
                    os.environ.pop(name, None)
                else:
                    os.environ[name] = value

        cls.addClassCleanup(restore_env)
        os.environ["OFFICE_HOME"] = str(cls.home)
        os.environ["OFFICE_DATA"] = str(cls.data)
        os.environ.pop("OFFICE_LAYOUT", None)
        os.environ.pop("OFFICE_CONFIG", None)

        cls._module_name = "office_server_layout_test"
        spec = importlib.util.spec_from_file_location(cls._module_name, SERVER_FILE)
        cls.office = importlib.util.module_from_spec(spec)
        sys.modules[cls._module_name] = cls.office
        cls.addClassCleanup(sys.modules.pop, cls._module_name, None)
        spec.loader.exec_module(cls.office)

    def setUp(self):
        self.layout_path = self.data / "office_layout.json"
        self.layout_path.unlink(missing_ok=True)
        self.layout_path.with_name(f".{self.layout_path.name}.tmp").unlink(missing_ok=True)

    def request(self, method, path, payload=None, local=False):
        headers = {"Host": "127.0.0.1"}
        body = b""
        if payload is not None:
            body = json.dumps(payload).encode("utf-8")
            headers["Content-Type"] = "application/json"
            headers["Content-Length"] = str(len(body))
        if local:
            headers["X-Office-Local"] = "1"
        handler = self.office.Handler.__new__(self.office.Handler)
        handler.path = path
        handler.headers = headers
        handler.rfile = io.BytesIO(body)
        response = {}
        handler._send = lambda code, raw, ctype: response.update(
            status=code, body=json.loads(raw.decode("utf-8")), ctype=ctype)
        if method == "GET":
            handler.do_GET()
        elif method == "POST":
            handler.do_POST()
        else:
            raise AssertionError(f"unsupported test method: {method}")
        return response["status"], response["body"]

    def test_get_without_and_with_v2_layout_file(self):
        self.assertEqual(self.request("GET", "/api/layout")[1],
                         {"custom": False, "layout": None, "roomPins": {}})
        self.layout_path.write_text(json.dumps(DEFAULT_LAYOUT), encoding="utf-8")
        self.assertEqual(self.request("GET", "/api/layout")[1],
                         {"custom": True, "layout": {**DEFAULT_LAYOUT, "roomPins": {}},
                          "roomPins": {}})

    def test_office_layout_env_overrides_default_path(self):
        override = self.data / "injected-layout.json"
        with mock.patch.dict(os.environ, {"OFFICE_LAYOUT": str(override)}):
            self.assertEqual(self.office.layout_file(), override)
            self.assertEqual(self.request("GET", "/api/layout")[1],
                             {"custom": False, "layout": None, "roomPins": {}})
            override.write_text(json.dumps(DEFAULT_LAYOUT), encoding="utf-8")
            self.assertEqual(self.request("GET", "/api/layout")[1],
                             {"custom": True, "layout": {**DEFAULT_LAYOUT, "roomPins": {}},
                              "roomPins": {}})

    def test_post_atomically_saves_v2_whitelist_and_round_trips(self):
        submitted = {
            "desks": [{"dx": 336, "dy": 408, "ignored": "desk"}],
            "sofa": {"x": 864, "y": 336, "w": 96, "ignored": "sofa"},
            "door": {"x": 530, "y": 625, "ignored": "door"},
            "ignored": "top",
        }
        expected = {
            "desks": [{"dx": 336, "dy": 408}],
            "sofa": {"x": 864, "y": 336, "w": 96},
            "door": {"x": 530, "y": 625},
            "roomPins": {},
        }
        self.assertEqual(self.request("POST", "/api/layout", {"layout": submitted})[0], 403)
        real_replace = self.office.os.replace
        with mock.patch.object(self.office.os, "replace", wraps=real_replace) as replace:
            status, body = self.request("POST", "/api/layout", {"layout": submitted}, local=True)
        self.assertEqual(status, 200)
        self.assertTrue(body["ok"])
        replace.assert_called_once()
        self.assertEqual(Path(replace.call_args.args[1]), self.layout_path)
        self.assertEqual(json.loads(self.layout_path.read_text(encoding="utf-8")), expected)
        self.assertEqual(self.request("GET", "/api/layout")[1],
                         {"custom": True, "layout": expected, "roomPins": {}})

    def test_post_null_deletes_layout(self):
        self.layout_path.write_text(json.dumps(DEFAULT_LAYOUT), encoding="utf-8")
        status, body = self.request("POST", "/api/layout", {"layout": None}, local=True)
        self.assertEqual(status, 200)
        self.assertTrue(body["ok"])
        self.assertFalse(self.layout_path.exists())
        self.assertEqual(self.request("GET", "/api/layout")[1],
                         {"custom": False, "layout": None, "roomPins": {}})

    def test_invalid_v2_layouts_are_rejected_without_partial_save(self):
        cases = {}
        too_many = copy.deepcopy(DEFAULT_LAYOUT)
        too_many["desks"] = [{"dx": 55, "dy": 125} for _ in range(11)]
        cases["11 desks"] = too_many
        outside = copy.deepcopy(DEFAULT_LAYOUT)
        outside["door"]["x"] = 641
        cases["outside entrance"] = outside
        floor_edge = copy.deepcopy(DEFAULT_LAYOUT)
        floor_edge["desks"][0]["dy"] = 576
        cases["main floor edge"] = floor_edge
        wrong_type = copy.deepcopy(DEFAULT_LAYOUT)
        wrong_type["desks"][0]["dx"] = "288"
        cases["wrong type"] = wrong_type
        cases["old v2 coordinate"] = {
            **copy.deepcopy(DEFAULT_LAYOUT),
            "desks": [{"dx": 55, "dy": 125}] + DEFAULT_LAYOUT["desks"][1:],
        }
        cases["partial layout"] = {"desks": [{"dx": 336, "dy": 408}]}

        for name, layout in cases.items():
            with self.subTest(name=name):
                self.layout_path.write_text(json.dumps(DEFAULT_LAYOUT), encoding="utf-8")
                status, body = self.request("POST", "/api/layout", {"layout": layout}, local=True)
                self.assertEqual(status, 400)
                self.assertFalse(body["ok"])
                self.assertEqual(json.loads(self.layout_path.read_text(encoding="utf-8")),
                                 DEFAULT_LAYOUT)

    def test_v1_meeting_layout_is_rejected_as_stale_on_read(self):
        stale = {
            "desks": [{"dx": 55, "dy": 125}],
            "meet": {"x": 52, "y": 250, "w": 180},
            "meetLead": {"x": 142, "y": 234},
            "minions": [{"x": 90, "y": 245, "z": 317}],
            "sofa": {"x": 54, "y": 498, "w": 176},
            "door": {"x": 540, "y": 618},
        }
        self.layout_path.write_text(json.dumps(stale), encoding="utf-8")
        self.assertEqual(self.office.layout_json(),
                         {"custom": False, "layout": None, "roomPins": {}})

    def test_default_desk_plus_one_pixel_is_accepted_without_coordinate_warp(self):
        submitted = copy.deepcopy(DEFAULT_LAYOUT)
        submitted["desks"][0]["dy"] += 1
        status, body = self.request("POST", "/api/layout", {"layout": submitted}, local=True)
        self.assertEqual(status, 200)
        self.assertTrue(body["ok"])
        saved = json.loads(self.layout_path.read_text(encoding="utf-8"))
        self.assertEqual(saved["desks"][0], {"dx": 336, "dy": 409})
        self.assertEqual(saved["desks"][1:], DEFAULT_LAYOUT["desks"][1:])

    def test_room_pins_partial_update_validates_and_merges(self):
        self.layout_path.write_text(json.dumps(DEFAULT_LAYOUT), encoding="utf-8")
        pins = {"cwd:/mock/project-one": "proj1"}
        status, body = self.request("POST", "/api/layout", {"roomPins": pins}, local=True)
        self.assertEqual(status, 200)
        self.assertEqual(body["roomPins"], pins)
        saved = json.loads(self.layout_path.read_text(encoding="utf-8"))
        self.assertEqual(saved["roomPins"], pins)
        self.assertEqual(saved["desks"], DEFAULT_LAYOUT["desks"])
        self.assertEqual(self.request("GET", "/api/layout")[1]["roomPins"], pins)

    def test_room_pins_duplicate_room_or_invalid_key_is_rejected(self):
        for pins in (
                {"cwd:/mock/project-one": "proj1", "cwd:/mock/project-two": "proj1"},
                {"dept:project-one": "proj1"},
                {"cwd:/mock/project-one/": "proj1"},
                {"cwd:/mock/project-one": "main"},
                {"cwd:/mock/project-one": 1},
                {"cwd:" + "x" * 197: "proj1"},
        ):
            with self.subTest(pins=pins):
                status, body = self.request("POST", "/api/layout", {"roomPins": pins}, local=True)
                self.assertEqual(status, 400)
                self.assertFalse(body["ok"])
                self.assertFalse(self.layout_path.exists())

    def test_layout_never_pollutes_office_json(self):
        before_keys = set(self.office.office_json())
        status, _ = self.request("POST", "/api/layout", {"layout": DEFAULT_LAYOUT}, local=True)
        self.assertEqual(status, 200)
        status, after = self.request("GET", "/api/office")
        self.assertEqual(status, 200)
        self.assertEqual(set(after), before_keys)
        self.assertNotIn("layout", after)


if __name__ == "__main__":
    unittest.main()
