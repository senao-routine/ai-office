# -*- coding: utf-8 -*-
"""R42.3 OpenClawアダプタ（契約スキーマv1）のテスト。

核となる回帰:
(1) golden: 契約fixtureの正常3体が employee 互換へ変換され、毒行3種（id不正/state不正/非dict）は黙って捨てる。
(2) プライバシーはソースで満たす＝cwd/lastSaid等の redact 対象フィールドが常に空。
(3) stale(>600秒)・形式不正・ファイル欠如は「未接続」へフォールバック（例外を出さない）。
(4) scan_office マージ: hybrid では oc- 社員が employees に載り、claude 版では載らない。
(5) external_openclaw_json が実データの要約を返す（60秒キャッシュ維持）。
"""
import importlib.util
import json
import os
import tempfile
import unittest
from pathlib import Path

TESTS = Path(__file__).resolve().parent
ROOT = TESTS.parent
FX = TESTS / "fixtures" / "openclaw_status.json"

_home = Path(tempfile.mkdtemp(prefix="office_oc_home_"))
os.environ["OFFICE_HOME"] = str(_home)
_spec = importlib.util.spec_from_file_location(
    "openclaw_source_t", ROOT / "server" / "openclaw_source.py")
ocs = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ocs)
_ospec = importlib.util.spec_from_file_location(
    "office_server_oc", ROOT / "server" / "office_server.py")
office = importlib.util.module_from_spec(_ospec)
_ospec.loader.exec_module(office)

RAW = json.loads(FX.read_text(encoding="utf-8"))
NOW = RAW["generatedAt"] + 10
REDACT_FIELDS = ("lastSaid", "target", "lastOrder", "cwd", "branch")


class ParseTest(unittest.TestCase):
    def test_golden(self):
        emps, meta = ocs.parse_openclaw_status(RAW, NOW)
        self.assertTrue(meta["connected"], meta)
        self.assertEqual(meta["site"], "macmini")
        self.assertEqual([e["session"] for e in emps],
                         ["oc-main", "oc-research-bot", "oc-night-batch"])
        main = emps[0]
        self.assertEqual(main["external"], "openclaw")
        self.assertEqual(main["state"], "working")
        self.assertNotIn("sprite", main)   # R80: スプライトは全廃＝ソースから出さない
        self.assertEqual(main["feed"], ["replying on WhatsApp"])
        self.assertEqual(main["minions"], 1)
        self.assertEqual(main["role"], "whatsapp")

    def test_privacy_fields_empty_at_source(self):
        emps, _ = ocs.parse_openclaw_status(RAW, NOW)
        for e in emps:
            for k in REDACT_FIELDS:
                self.assertEqual(e[k], "", f"{e['session']}.{k} が空でない（本文が載る穴）")
            self.assertEqual(e["question"], "")

    def test_same_name_numbering(self):
        emps_ja, _ = ocs.parse_openclaw_status(RAW, NOW, lang="ja")
        self.assertEqual(emps_ja[0]["disp"], "OpenClaw")
        self.assertEqual(emps_ja[1]["disp"], "OpenClaw 2号")
        emps_en, _ = ocs.parse_openclaw_status(RAW, NOW, lang="en")
        self.assertEqual(emps_en[1]["disp"], "OpenClaw #2")

    def test_stale_and_malformed(self):
        emps, meta = ocs.parse_openclaw_status(RAW, RAW["generatedAt"] + 601)
        self.assertEqual(emps, [])
        self.assertFalse(meta["connected"])
        self.assertIn("stale", meta["reason"])
        for bad in ({}, {"v": 2}, [], None, {"v": 1}):
            emps, meta = ocs.parse_openclaw_status(bad, NOW)
            self.assertEqual(emps, [])
            self.assertFalse(meta["connected"])


class SourceFileTest(unittest.TestCase):
    def tearDown(self):
        os.environ.pop("OFFICE_OPENCLAW_FIXTURE", None)

    def test_missing_file_disconnected(self):
        emps, meta = ocs.openclaw_employees(_home, NOW)
        self.assertEqual(emps, [])
        self.assertFalse(meta["connected"])

    def test_fixture_env_injection(self):
        os.environ["OFFICE_OPENCLAW_FIXTURE"] = str(FX)
        emps, meta = ocs.openclaw_employees("/nonexistent-home", NOW)
        self.assertTrue(meta["connected"])
        self.assertEqual(len(emps), 3)

    def test_broken_json_disconnected(self):
        p = _home / "broken.json"
        p.write_text("{not json", encoding="utf-8")
        os.environ["OFFICE_OPENCLAW_FIXTURE"] = str(p)
        emps, meta = ocs.openclaw_employees(_home, NOW)
        self.assertEqual(emps, [])
        self.assertFalse(meta["connected"])


class ScanMergeTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="office_oc_cfg_"))
        cfg = self.tmp / "office_config.json"
        cfg.write_text(json.dumps({"projects": {}}), encoding="utf-8")
        os.environ["OFFICE_CONFIG"] = str(cfg)
        # fixtureのgeneratedAtは過去固定→scanのnow(実時刻)でstaleにならないよう現在時刻版を作る
        import time
        raw = dict(RAW)
        raw["generatedAt"] = time.time()
        live = self.tmp / "openclaw_status.json"
        live.write_text(json.dumps(raw), encoding="utf-8")
        os.environ["OFFICE_OPENCLAW_FIXTURE"] = str(live)
        office._cache["t"] = 0.0
        office._openclaw_cache.update({"at": None, "data": None})
        os.environ.pop("OFFICE_EDITION", None)

    def tearDown(self):
        os.environ.pop("OFFICE_OPENCLAW_FIXTURE", None)
        os.environ.pop("OFFICE_CONFIG", None)
        os.environ.pop("OFFICE_EDITION", None)

    def test_hybrid_merges_oc_employees(self):
        data = office.scan_office()
        oc = [e for e in data["employees"] if e.get("external") == "openclaw"]
        self.assertEqual(len(oc), 3)
        self.assertTrue(all(e["session"].startswith("oc-") for e in oc))
        # counts にも合算される（正直な稼働数）
        self.assertGreaterEqual(data["counts"]["working"], 1)

    def test_claude_edition_hides_oc(self):
        os.environ["OFFICE_EDITION"] = "claude"
        data = office.scan_office()
        self.assertEqual([e for e in data["employees"] if e.get("external")], [])

    def test_openclaw_edition_shows_only_oc(self):
        os.environ["OFFICE_EDITION"] = "openclaw"
        data = office.scan_office()
        self.assertEqual(len(data["employees"]), 3)
        self.assertTrue(all(e.get("external") == "openclaw" for e in data["employees"]))

    def test_external_view_summarizes(self):
        view = office.external_openclaw_json()
        self.assertTrue(view["connected"], view)
        self.assertEqual(len(view["employees"]), 3)
        self.assertNotIn("cwd", view["employees"][0])


if __name__ == "__main__":
    unittest.main()
