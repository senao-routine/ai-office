# -*- coding: utf-8 -*-
"""R42.2d 言語設定（オフィス全体・サーバー正本）のテスト。

核となる回帰:
(1) office_lang() の解決順 = OFFICE_LANG env > config "lang" > 既定 ja。不正値は ja。
(2) lang=en で verb（TOOL_VERB系）・状態動詞・disp号数・officeName既定が英語化される。
(3) office_json トップレベルに lang が載る（PC/PWA/Pushの分岐源）。
(4) 既定(ja)では従来出力と完全一致＝既存テスト・fixture・redactionに波及しない。
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

_home = Path(tempfile.mkdtemp(prefix="office_lang_home_"))
os.environ["OFFICE_HOME"] = str(_home)
spec = importlib.util.spec_from_file_location(
    "office_server_lang", ROOT / "server" / "office_server.py")
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


class OfficeLangTest(unittest.TestCase):
    def setUp(self):
        proj_root = _home / ".claude" / "projects"
        if proj_root.exists():
            shutil.rmtree(proj_root)
        office._cache["t"] = 0.0
        office._LANG = "ja"
        self.tmp = Path(tempfile.mkdtemp(prefix="office_lang_cfg_"))
        os.environ.pop("OFFICE_LANG", None)
        # ロケール段（R50提案2c）を切り離す＝実行マシンのロケールでテストが揺れない
        self._locale_orig = {k: os.environ.pop(k, None)
                             for k in ("LC_ALL", "LC_MESSAGES", "LANG")}
        self._config({"projects": {}})

    def tearDown(self):
        os.environ.pop("OFFICE_LANG", None)
        os.environ.pop("OFFICE_CONFIG", None)
        for k, v in self._locale_orig.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _config(self, body):
        p = self.tmp / "office_config.json"
        p.write_text(json.dumps(body, ensure_ascii=False), encoding="utf-8")
        os.environ["OFFICE_CONFIG"] = str(p)
        return p

    # ---- (1) 解決順 ----

    def test_default_is_ja(self):
        self.assertEqual(office.office_lang(), "ja")

    def test_config_and_env(self):
        self._config({"projects": {}, "lang": "en"})
        self.assertEqual(office.office_lang(), "en")
        os.environ["OFFICE_LANG"] = "ja"
        self.assertEqual(office.office_lang(), "ja")  # envが勝つ

    def test_invalid_falls_back_to_ja(self):
        for bad in ("fr", "EN-us", "1", ""):
            self._config({"projects": {}, "lang": bad})
            self.assertEqual(office.office_lang(), "ja", bad)
        os.environ["OFFICE_LANG"] = " En "
        self.assertEqual(office.office_lang(), "en")  # 大小・空白は正規化

    def test_os_locale_fallback(self):
        """R50提案2c: config/env 未指定なら OSロケール（en系のみ en）。config指定は勝つ。"""
        os.environ["LANG"] = "en_US.UTF-8"
        self.assertEqual(office.office_lang(), "en")
        os.environ["LANG"] = "C.UTF-8"
        self.assertEqual(office.office_lang(), "ja")
        os.environ["LANG"] = "en_US.UTF-8"
        self._config({"projects": {}, "lang": "ja"})
        self.assertEqual(office.office_lang(), "ja")   # config が勝つ
        os.environ["LC_ALL"] = "ja_JP.UTF-8"           # LC_ALL 優先
        self._config({"projects": {}})
        self.assertEqual(office.office_lang(), "ja")

    # ---- (2)(3) scan_office の英語化 ----

    def test_en_scan_translates_verbs_disp_and_name(self):
        os.environ["OFFICE_LANG"] = "en"
        put_session("-Users-test-demo-project", "sess-lang0001.jsonl",
                    "working_tool.jsonl", age=10)
        put_session("-Users-test-demo-project", "sess-lang0002.jsonl",
                    "working_tool.jsonl", age=600)
        data = office.scan_office()
        self.assertEqual(data["lang"], "en")
        self.assertEqual(data["officeName"], "AI Office")
        import re
        jp = re.compile(r"[ぁ-んァ-ヶ一-龥]")
        for e in data["employees"]:
            self.assertFalse(jp.search(e["verb"] or ""), e["verb"])
            for line in e.get("feed") or []:
                # 動作ログ行の動詞部分が英語（対象名は実データ由来で任意）
                self.assertFalse(jp.search(line.split(" ", 1)[0]), line)
        disps = sorted(e["disp"] for e in data["employees"])
        self.assertTrue(disps[1].endswith("#2"), disps)

    def test_ja_scan_unchanged(self):
        put_session("-Users-test-demo-project", "sess-lang0003.jsonl",
                    "working_tool.jsonl", age=10)
        put_session("-Users-test-demo-project", "sess-lang0004.jsonl",
                    "waiting_said.jsonl", age=600)
        data = office.scan_office()
        self.assertEqual(data["lang"], "ja")
        self.assertEqual(data["officeName"], "AIオフィス")
        disps = sorted(e["disp"] for e in data["employees"])
        self.assertTrue(disps[1].endswith("2号"), disps)
        verbs = {e["session"]: e["verb"] for e in data["employees"]}
        self.assertIn("実行中", str(verbs))

    def test_waiting_state_verb_en(self):
        os.environ["OFFICE_LANG"] = "en"
        put_session("-Users-test-demo-project", "sess-lang0005.jsonl",
                    "waiting_said.jsonl", age=600)
        data = office.scan_office()
        e = data["employees"][0]
        self.assertEqual(e["state"], "waiting")
        import re
        self.assertFalse(re.search(r"[ぁ-んァ-ヶ一-龥]", e["verb"]), e["verb"])


if __name__ == "__main__":
    unittest.main()
