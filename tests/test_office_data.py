# -*- coding: utf-8 -*-
"""P4 OFFICE_DATA リダイレクトの契約テスト:
- 未設定 → DATA=ROOT（後方互換・repo の config/assets を読む）
- 設定   → config_file() が OFFICE_DATA 配下（daemon/dev が同一データ＝分岐しない）"""
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

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
        self.assertEqual(snapshot["setup"], {"hookInstalled": True, "eventsWired": False})
        self.assertNotIn("settings.json", json.dumps(snapshot, ensure_ascii=False))
        self.assertNotIn("test-secret", json.dumps(snapshot, ensure_ascii=False))

    def test_events_wired_accepts_wildcard_matcher_like_installer(self):
        home = self._tmpdir("events_star_")
        (home / ".claude").mkdir()
        cmd = 'bash "$HOME/.claude/hooks/office-event.sh"'
        (home / ".claude" / "settings.json").write_text(json.dumps({"hooks": {"Stop": [
            {"matcher": "*", "hooks": [{"type": "command", "command": cmd, "async": True}]}]}}), encoding="utf-8")
        os.environ["OFFICE_HOME"] = str(home)
        o = _load("office_events_star", ROOT / "server" / "office_server.py")
        self.assertTrue(o.events_wired())
        (home / ".claude" / "settings.json").write_text(json.dumps({"hooks": {"Stop": [
            {"matcher": "Edit", "hooks": [{"type": "command", "command": cmd, "async": True}]},
            {"hooks": [{"type": "command", "command": "bash /other/office-event.sh"}]}]}}), encoding="utf-8")
        self.assertFalse(o.events_wired())                # 限定 matcher・他プロジェクト同名は配線と数えない

    def test_office_json_setup_hook_installed_false_for_missing_or_invalid_settings(self):
        home = self._tmpdir("hook_false_")
        (home / ".claude").mkdir()
        os.environ["OFFICE_HOME"] = str(home)
        o = _load("office_hook_missing", ROOT / "server" / "office_server.py")
        self.assertEqual(o.office_json()["setup"], {"hookInstalled": False, "eventsWired": False})

        (home / ".claude" / "settings.json").write_text("{broken", encoding="utf-8")
        o = _load("office_hook_invalid", ROOT / "server" / "office_server.py")
        self.assertEqual(o.office_json()["setup"], {"hookInstalled": False, "eventsWired": False})


class SourcesMetadataTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix="office_sources_")
        self.addCleanup(tmp.cleanup)
        result = subprocess.run([sys.executable, str(ROOT / "tests/make_home.py")],
                                env=dict(os.environ, TMPDIR=tmp.name),
                                capture_output=True, text=True, check=True)
        self.home = Path(result.stdout)
        self.now = time.time()
        raw = json.loads((ROOT / "tests/fixtures/openclaw_status.json").read_text(encoding="utf-8"))
        raw["generatedAt"] = self.now
        oc_fixture = self.home / "openclaw.json"
        oc_fixture.write_text(json.dumps(raw), encoding="utf-8")
        env = patch.dict(os.environ, {
            "OFFICE_HOME": str(self.home), "OFFICE_DATA": str(self.home),
            "OFFICE_CONFIG": str(self.home / "office_config.json"),
            "OFFICE_AGENTS_CLI": "1", "OFFICE_SOURCES_CODEX": "1",
            "OFFICE_AGENTS_FIXTURE": str(self.home / ".claude/jobs/agents.json"),
            "OFFICE_OPENCLAW_FIXTURE": str(oc_fixture), "OFFICE_EDITION": "hybrid",
            "OFFICE_LANG": "en",
        })
        env.start()
        self.addCleanup(env.stop)
        self.office = _load("office_sources_metadata", ROOT / "server/office_server.py")

    def test_sources_report_counts_and_connections_without_paths(self):
        data = self.office.office_json()
        sources = data["sources"]
        self.assertEqual(sources, {
            "claude": {"fg": 1, "bg": 1, "agentsCli": True},
            "codex": {"connected": True, "n": 2, "reason": ""},
            "openclaw": {"connected": True},
        })
        for key in ("fg", "bg"):
            self.assertIs(type(sources["claude"][key]), int)
        self.assertIs(type(sources["codex"]["n"]), int)
        self.assertIs(type(sources["claude"]["agentsCli"]), bool)
        for vendor in ("codex", "openclaw"):
            self.assertIs(type(sources[vendor]["connected"]), bool)
        encoded = json.dumps(sources)
        for forbidden in ("/", "\\", str(self.home), "cwd", "session", "detail"):
            self.assertNotIn(forbidden, encoded)
        employees = data["employees"]
        self.assertEqual({e["vendor"] for e in employees}, {"claude", "codex", "openclaw"})
        self.assertTrue(all("disp" in e for e in employees))
        codex = [e for e in employees if e["vendor"] == "codex"]
        self.assertEqual([e["session"] for e in codex], ["cx-cx-parent-a", "cx-cx-parent-b"])
        self.assertEqual(codex[0]["verb"], "running")
        self.assertEqual(codex[0]["minions"], 1)
        self.assertEqual([e["vendor"] for e in employees[-2:]], ["codex", "codex"])

    def test_codex_can_be_disabled_by_source_config_or_env(self):
        for config, env in (({"sources": {"codex": False}}, {}),
                            ({}, {"OFFICE_SOURCES_CODEX": "0"})):
            with self.subTest(config=config, env=env), \
                    patch.object(self.office, "load_config", return_value=config), \
                    patch.dict(os.environ, env), \
                    patch.object(self.office.source_codex, "codex_employees") as source:
                data = self.office.scan_office()
                source.assert_not_called()
                self.assertFalse(any(e["vendor"] == "codex" for e in data["employees"]))
                self.assertEqual(data["sources"]["codex"],
                                 {"connected": False, "n": 0, "reason": "disabled"})

    def test_retired_edition_cannot_disable_any_source(self):
        for ed in ("claude", "hybrid", "openclaw"):
            with patch.dict(os.environ, {"OFFICE_EDITION": ed}), \
                    patch.object(self.office, "load_config", return_value={"edition": ed}):
                data = self.office.scan_office()
                self.assertEqual({e["vendor"] for e in data["employees"]}, {"claude", "codex", "openclaw"})
                self.assertNotIn("edition", data)

    def test_missing_codex_is_disconnected_with_stable_metadata_shape(self):
        with patch.object(self.office, "_HOME", self.home / "missing-home"):
            data = self.office.scan_office()
        self.assertEqual(data["sources"]["codex"],
                         {"connected": False, "n": 0, "reason": "missing"})


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
