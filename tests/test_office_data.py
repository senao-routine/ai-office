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

    def test_office_json_today_answered_comes_from_daily_stats(self):
        """R96-B: 「今日のまとめ」の正本は daemon 側の日次 stats（端末ごとに数字が違わない）。無ければ 0。"""
        import datetime, json
        tmp = self._tmpdir("today_")
        config = tmp / "office_config.json"
        config.write_text('{"projects":{}}\n', encoding="utf-8")
        os.environ["OFFICE_HOME"] = str(tmp)
        os.environ["OFFICE_DATA"] = str(tmp)
        os.environ["OFFICE_CONFIG"] = str(config)
        o = _load("office_today", ROOT / "server" / "office_server.py")
        self.assertEqual(o.office_json()["today"]["answered"], 0)
        day = datetime.datetime.now().strftime("%Y-%m-%d")
        (o.DAILY_DIR).mkdir(parents=True, exist_ok=True)
        (o.DAILY_DIR / f"{day}.stats.json").write_text(json.dumps({"answered": 3, "totalWaitSec": 900}), encoding="utf-8")
        o._cache["t"] = 0.0
        self.assertEqual(o.office_json()["today"]["answered"], 3)

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
        self.assertEqual(snapshot["setup"],
                         {"hookInstalled": True, "eventsWired": False, "approvalWired": False})
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
        self.assertEqual(o.office_json()["setup"],
                         {"hookInstalled": False, "eventsWired": False, "approvalWired": False})

        (home / ".claude" / "settings.json").write_text("{broken", encoding="utf-8")
        o = _load("office_hook_invalid", ROOT / "server" / "office_server.py")
        self.assertEqual(o.office_json()["setup"],
                         {"hookInstalled": False, "eventsWired": False, "approvalWired": False})


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
            # R126: 出勤の口（登録なし＝0 人・台帳が無いのは正常＝ok）
            "door": {"ok": True, "bots": 0, "n": 0, "asks": 0},
        })
        for key in ("fg", "bg"):
            self.assertIs(type(sources["claude"][key]), int)
        self.assertIs(type(sources["codex"]["n"]), int)
        self.assertIs(type(sources["claude"]["agentsCli"]), bool)
        for vendor in ("codex", "openclaw"):
            self.assertIs(type(sources[vendor]["connected"]), bool)
        self.assertIs(type(sources["door"]["ok"]), bool)
        for key in ("bots", "n", "asks"):
            self.assertIs(type(sources["door"][key]), int)
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

    def test_door_bots_join_next_to_openclaw_and_are_counted(self):
        """R126: 登録した Bot は OpenClaw の隣に 1 行ずつ（vendor "bot"・session "bot-<id>"）。sources.door は数だけ。
        壊れた台帳でも走査は落ちない（ok=False・Bot は 0 人・ほかのソースはそのまま）。"""
        door = self.office.office_door
        now = time.time()
        for bot_id, name in (("watch", "見回り"), ("mail", "郵便受け"), ("idle", "眠り")):
            self.assertTrue(door.add_bot(bot_id, name, home=self.home)["ok"])
        self.assertTrue(door.checkin("watch", "受信箱を見回り中", home=self.home, now=now)["ok"])
        self.assertTrue(door.ask("mail", "送ってよい？", ["はい", "いいえ"], home=self.home, now=now)["ok"])
        data = self.office.scan_office()
        self.assertEqual(data["sources"]["door"], {"ok": True, "bots": 3, "n": 2, "asks": 1})
        vendors = [e["vendor"] for e in data["employees"]]
        self.assertEqual(vendors.count("bot"), 2)                       # 一度も動いていない Bot は居ない
        self.assertLess(vendors.index("openclaw"), vendors.index("bot"))
        self.assertLess(vendors.index("bot"), vendors.index("codex"))
        bots = {e["session"]: e for e in data["employees"] if e["vendor"] == "bot"}
        self.assertEqual(set(bots), {"bot-watch", "bot-mail"})
        self.assertEqual(bots["bot-mail"]["state"], "waiting")
        self.assertNotIn("external", bots["bot-watch"])                 # Claude のセッションと同じ道を通す
        self.assertEqual(data["counts"]["waiting"], sum(1 for e in data["employees"] if e["state"] == "waiting"))
        door.ledger_path(self.home).write_text("{not json", encoding="utf-8")
        data = self.office.scan_office()
        self.assertEqual(data["sources"]["door"], {"ok": False, "bots": 0, "n": 0, "asks": 0})
        self.assertFalse(any(e["vendor"] == "bot" for e in data["employees"]))
        self.assertEqual(data["sources"]["codex"]["n"], 2)
        with patch.object(door, "door_employees", side_effect=RuntimeError("boom")):
            data = self.office.scan_office()                           # 口の例外は握る（オフィスは落ちない）
        self.assertEqual(data["sources"]["door"], {"ok": False, "bots": 0, "n": 0, "asks": 0})

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



class CrewPeakTest(unittest.TestCase):
    """R97: オフィスの広さ（机の数）を決める「同時に居た最大人数」を Mac が覚える。

    ブラウザの保存領域にしか無かったとき、別のブラウザで開く・領域が消えるだけで机が消え、
    動いている物が壊れたように見えた（本人の報告「机がだいぶ無くなっている」）。
    """

    def setUp(self):
        self.home = Path(tempfile.mkdtemp())
        (self.home / ".claude").mkdir(parents=True, exist_ok=True)

    def _office(self, name="office_server_peak"):
        os.environ["OFFICE_HOME"] = str(self.home)
        return _load(name, ROOT / "server" / "office_server.py")

    def tearDown(self):
        os.environ.pop("OFFICE_HOME", None)

    def test_peak_only_grows_and_survives_restart(self):
        o = self._office()
        self.assertEqual(o.observed_peak(3), 3)
        self.assertEqual(o.observed_peak(9), 9)
        self.assertEqual(o.observed_peak(1), 9, "閉じただけで机が減ってはいけない")
        # プロセスを作り直しても覚えている（ファイルに落ちている＝不変条件 #9）
        again = self._office("office_server_peak2")
        self.assertEqual(again.observed_peak(0), 9)

    def test_peak_survives_broken_file(self):
        """手編集・復元で JSON が壊れても、辞書でなくても、/api/office ごと落ちない。"""
        o = self._office()
        peak = self.home / ".claude" / "office_peak.json"
        peak.parent.mkdir(parents=True, exist_ok=True)
        for broken in ("{壊れた", "null", "[]", '"x"', '{"maxSeen": "九"}'):
            peak.write_text(broken, encoding="utf-8")
            self.assertEqual(o.observed_peak(4), 4, broken)

    def test_peak_is_serialized_between_processes(self):
        """daemon と relay_agent は別プロセスで併走する。直列化しないと 12 を 9 で上書きできる。"""
        o = self._office("office_server_peak_race")
        self.assertEqual(o.observed_peak(12), 12)
        code = ("import os, sys; os.environ['OFFICE_HOME'] = %r; sys.path.insert(0, %r);"
                "import office_server as s; print(s.observed_peak(9))"
                % (str(self.home), str(ROOT / "server")))
        out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
        self.assertEqual(out.stdout.strip(), "12", out.stderr[-400:])
        self.assertEqual(o.observed_peak(0), 12, "別プロセスが小さい値で上書きした")

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


class SessionBriefDeliveryTest(unittest.TestCase):
    """R96-B 残: 内訳 brief は listening と ask{tool,kind} を運ぶが、本文・パス・その他の ask 項目は構造的に持たない。"""

    def test_brief_carries_listening_and_ask_shape_only(self):
        office = _load("office_brief", ROOT / "server" / "office_server.py")
        e = {"session": "s1", "state": "working", "listening": False,
             "ask": {"tool": "Bash", "kind": "permission", "command": "rm -rf /secret", "prompt": "本文"},
             "cwd": "/Users/x/secret", "lastSaid": "本文"}
        b = office._session_brief(e)
        self.assertIs(b["listening"], False)
        self.assertEqual(b["ask"], {"tool": "Bash", "kind": "permission"})
        self.assertNotIn("cwd", b); self.assertNotIn("lastSaid", b)
        self.assertNotIn("listening", office._session_brief({"session": "s2", "state": "waiting"}))
        self.assertNotIn("ask", office._session_brief({"session": "s3", "ask": None}))
