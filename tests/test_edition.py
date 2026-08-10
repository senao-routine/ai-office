# -*- coding: utf-8 -*-
"""R42.1 エディション骨格のテスト。

核となる回帰:
(1) edition() の解決順 = OFFICE_EDITION env > config "edition" > 既定 hybrid。不正値は claude。
(2) edition_features() が商売ロジックの単一集約点（マトリクスをここでピン）。
(3) office_json/scan_office のトップレベルに edition が載る（PC UI/relay/PWA の分岐源）。
(4) claudeSessions=false（edition:openclaw）では transcript スキャンを行わない＝社員0。
既定(hybrid)では従来挙動と差分ゼロであること（employees/counts が従来どおり出る）。
"""
import importlib.util
import json
import os
import shutil
import tempfile
import time
import unittest
from pathlib import Path

TESTS = Path(__file__).resolve().parent
ROOT = TESTS.parent
FX = TESTS / "fixtures"

_home = Path(tempfile.mkdtemp(prefix="office_edition_home_"))
os.environ["OFFICE_HOME"] = str(_home)
spec = importlib.util.spec_from_file_location(
    "office_server_edition", ROOT / "server" / "office_server.py")
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


class EditionTest(unittest.TestCase):
    def setUp(self):
        proj_root = _home / ".claude" / "projects"
        if proj_root.exists():
            shutil.rmtree(proj_root)
        office._cache["t"] = 0.0
        self.tmp = Path(tempfile.mkdtemp(prefix="office_edition_cfg_"))
        os.environ.pop("OFFICE_EDITION", None)
        # 密閉性: 既定でも repo の実configを読まない（実configにeditionが入っても不変）
        self._config({"projects": {}})

    def tearDown(self):
        os.environ.pop("OFFICE_EDITION", None)
        os.environ.pop("OFFICE_CONFIG", None)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _config(self, body):
        p = self.tmp / "office_config.json"
        p.write_text(json.dumps(body, ensure_ascii=False), encoding="utf-8")
        os.environ["OFFICE_CONFIG"] = str(p)
        return p

    # ---- (1) edition() の解決順 ----

    def test_default_is_hybrid(self):
        # config にも env にも指定なし → 開発既定 hybrid（既存フローを変えない）
        self.assertEqual(office.edition(), "hybrid")

    def test_config_edition(self):
        self._config({"projects": {}, "edition": "claude"})
        self.assertEqual(office.edition(), "claude")
        self._config({"projects": {}, "edition": "openclaw"})
        self.assertEqual(office.edition(), "openclaw")

    def test_env_overrides_config(self):
        self._config({"projects": {}, "edition": "claude"})
        os.environ["OFFICE_EDITION"] = "openclaw"
        self.assertEqual(office.edition(), "openclaw")

    def test_invalid_value_falls_back_to_claude(self):
        # 明示された不正値は「最小の無料tier」= claude へ倒す（製品配布で安全側）
        for bad in ("pro", "HYBRID2", "Claude ", "1", "true"):
            self._config({"projects": {}, "edition": bad})
            self.assertEqual(office.edition(), "claude", bad)
        os.environ["OFFICE_EDITION"] = "nonsense"
        self.assertEqual(office.edition(), "claude")

    def test_case_and_space_normalized(self):
        os.environ["OFFICE_EDITION"] = " Hybrid "
        self.assertEqual(office.edition(), "hybrid")

    def test_empty_means_default(self):
        # 空文字は「未指定」と同義＝hybrid（installスクリプトの変数未展開事故で claude に落とさない）
        os.environ["OFFICE_EDITION"] = ""
        self.assertEqual(office.edition(), "hybrid")
        os.environ.pop("OFFICE_EDITION")
        self._config({"projects": {}, "edition": ""})
        self.assertEqual(office.edition(), "hybrid")

    # ---- (2) features マトリクス（商売ロジックのピン） ----

    def test_features_matrix(self):
        # 2026-08-10 ライセンス廃止: 機能ゲート撤廃＝relayPwa/push/costDash は常に全ON
        # （誰でも無料で全機能を使える）。editionは「表示モード」＝claudeSessions/openclawだけを分ける。
        self.assertEqual(office.edition_features("claude"),
                         {"claudeSessions": True, "openclaw": False,
                          "relayPwa": True, "push": True, "costDash": True})
        self.assertEqual(office.edition_features("openclaw"),
                         {"claudeSessions": False, "openclaw": True,
                          "relayPwa": True, "push": True, "costDash": True})
        self.assertEqual(office.edition_features("hybrid"),
                         {"claudeSessions": True, "openclaw": True,
                          "relayPwa": True, "push": True, "costDash": True})

    # ---- (3)(4) scan_office / office_json への搭載とスキャン制御 ----

    def test_scan_office_carries_edition_and_keeps_default_behavior(self):
        put_session("-Users-test-demo-project", "sess-ed000001.jsonl",
                    "working_tool.jsonl", age=10)
        data = office.scan_office()
        self.assertEqual(data["edition"]["id"], "hybrid")
        feats = data["edition"]["features"]
        self.assertTrue(feats["claudeSessions"] and feats["openclaw"])
        self.assertIn("license", data["edition"])
        # 既定(hybrid)では従来どおり社員が出る＝差分ゼロ
        self.assertEqual(len(data["employees"]), 1)
        self.assertIn("counts", data)

    def test_claude_edition_scans_sessions(self):
        os.environ["OFFICE_EDITION"] = "claude"
        put_session("-Users-test-demo-project", "sess-ed000002.jsonl",
                    "working_tool.jsonl", age=10)
        data = office.scan_office()
        self.assertEqual(data["edition"]["id"], "claude")
        self.assertEqual(len(data["employees"]), 1)
        self.assertFalse(data["edition"]["features"]["openclaw"])

    def test_openclaw_edition_skips_claude_scan(self):
        os.environ["OFFICE_EDITION"] = "openclaw"
        put_session("-Users-test-demo-project", "sess-ed000003.jsonl",
                    "working_tool.jsonl", age=10)
        data = office.scan_office()
        self.assertEqual(data["edition"]["id"], "openclaw")
        self.assertEqual(data["employees"], [])
        self.assertEqual(data["counts"],
                         {"working": 0, "waiting": 0, "resting": 0})

    def test_projects_missing_early_return_carries_edition(self):
        # PROJECTS ディレクトリ無しの早期returnにも edition が載る（UIの分岐源が欠けない）
        data = office.scan_office()
        self.assertEqual(data["employees"], [])
        self.assertIn("edition", data)
        self.assertEqual(data["edition"]["id"], "hybrid")

    def test_office_json_passes_edition_through_cache(self):
        office._cache["t"] = 0.0
        data = office.office_json()
        self.assertIn("edition", data)


if __name__ == "__main__":
    unittest.main()
