# -*- coding: utf-8 -*-
"""P4 OFFICE_DATA リダイレクトの契約テスト:
- 未設定 → DATA=ROOT（後方互換・repo の config/assets を読む）
- 設定   → config_file() が OFFICE_DATA 配下（daemon/dev が同一データ＝分岐しない）"""
import importlib.util
import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class OfficeDataTest(unittest.TestCase):
    def setUp(self):
        self._env = {k: os.environ.get(k) for k in ("OFFICE_DATA", "OFFICE_CONFIG", "OFFICE_HOME", "OPENAI_API_KEY")}
        for k in self._env:
            os.environ.pop(k, None)

    def tearDown(self):
        for k, v in self._env.items():
            os.environ.pop(k, None)
            if v is not None:
                os.environ[k] = v

    def _tmpdir(self, prefix):
        d = Path(tempfile.mkdtemp(prefix=prefix))
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        return d

    def test_default_data_is_root(self):
        o = _load("office_data_default", ROOT / "server" / "office_server.py")
        self.assertEqual(o.DATA, ROOT)
        self.assertEqual(o.config_file(), ROOT / "office_config.json")

    def test_office_data_redirects_config_and_assets(self):
        tmp = self._tmpdir("odata_")
        os.environ["OFFICE_DATA"] = str(tmp)
        o = _load("office_data_redirect", ROOT / "server" / "office_server.py")
        self.assertEqual(o.DATA, tmp)
        self.assertEqual(o.config_file(), tmp / "office_config.json")
        # OFFICE_CONFIG（テスト注入口）は OFFICE_DATA より優先のまま
        os.environ["OFFICE_CONFIG"] = str(tmp / "other.json")
        self.assertEqual(o.config_file(), tmp / "other.json")

    def test_office_json_exposes_configured_office_name(self):
        tmp = self._tmpdir("office_name_")
        config = tmp / "office_config.json"
        config.write_text('{"officeName":"テスト本部","projects":{}}\n', encoding="utf-8")
        os.environ["OFFICE_HOME"] = str(tmp)
        os.environ["OFFICE_DATA"] = str(tmp)
        os.environ["OFFICE_CONFIG"] = str(config)
        o = _load("office_name", ROOT / "server" / "office_server.py")
        self.assertEqual(o.office_json()["officeName"], "テスト本部")

    def test_office_json_setup_hook_installed_true(self):
        home = self._tmpdir("hook_true_")
        claude = home / ".claude"
        claude.mkdir()
        (claude / "settings.json").write_text(json.dumps({
            "hooks": {"Stop": [{"hooks": [{
                "type": "command",
                "command": "/private/path/hooks/office-inbox-wait.sh",
            }]}]},
            "secrets": {"doNotExpose": "test-secret"},
        }), encoding="utf-8")
        os.environ["OFFICE_HOME"] = str(home)
        o = _load("office_hook_true", ROOT / "server" / "office_server.py")
        snapshot = o.office_json()
        self.assertEqual(snapshot["setup"], {"hookInstalled": True})
        self.assertNotIn("settings.json", json.dumps(snapshot, ensure_ascii=False))
        self.assertNotIn("test-secret", json.dumps(snapshot, ensure_ascii=False))

    def test_office_json_setup_hook_installed_false_for_missing_or_invalid_settings(self):
        home = self._tmpdir("hook_false_")
        (home / ".claude").mkdir()
        os.environ["OFFICE_HOME"] = str(home)
        o = _load("office_hook_missing", ROOT / "server" / "office_server.py")
        self.assertEqual(o.office_json()["setup"], {"hookInstalled": False})

        (home / ".claude" / "settings.json").write_text("{broken", encoding="utf-8")
        o = _load("office_hook_invalid", ROOT / "server" / "office_server.py")
        self.assertEqual(o.office_json()["setup"], {"hookInstalled": False})


class PageFallbackTest(unittest.TestCase):
    def setUp(self):
        self._office_home = os.environ.get("OFFICE_HOME")
        self._tmp_home = Path(tempfile.mkdtemp(prefix="fallback_home_"))
        os.environ["OFFICE_HOME"] = str(self._tmp_home)
        self.office = _load("office_page_fallback", ROOT / "server" / "office_server.py")

    def tearDown(self):
        os.environ.pop("OFFICE_HOME", None)
        if self._office_home is not None:
            os.environ["OFFICE_HOME"] = self._office_home
        shutil.rmtree(self._tmp_home, ignore_errors=True)

    def test_minimal_static_page_is_pinned(self):
        page = self.office.PAGE_FALLBACK
        self.assertIn("install.sh", page)
        self.assertLess(len(page.encode("utf-8")), 2048)
        self.assertNotIn("<script", page)
        self.assertIn("ui/boot.html", page)   # R52: 旧UI削除に追随


if __name__ == "__main__":
    unittest.main()


class DisconnectNoiseTest(unittest.TestCase):
    """ブラウザを閉じただけで25行のトレースバックを吐かない（本物のエラーが埋もれる）。"""

    def test_broken_pipe_is_one_liner(self):
        import io
        import sys as _sys
        o = _load("office_noise", ROOT / "server" / "office_server.py")
        srv = o._OfficeHTTPServer.__new__(o._OfficeHTTPServer)
        buf = io.StringIO()
        old = _sys.stderr
        _sys.stderr = buf
        try:
            try:
                raise BrokenPipeError(32, "Broken pipe")
            except BrokenPipeError:
                srv.handle_error(None, ("127.0.0.1", 1))
            self.assertEqual(buf.getvalue(), "", "切断でトレースバックを吐いている")
            # 本物の例外は今までどおり出す（握り潰さない）
            try:
                raise ValueError("本物のバグ")
            except ValueError:
                srv.handle_error(None, ("127.0.0.1", 1))
            self.assertIn("本物のバグ", buf.getvalue())
        finally:
            _sys.stderr = old
