# -*- coding: utf-8 -*-
"""P1 ➕新プロジェクト登録のテスト（add_project / pick_folder / launch はモック注入口経由）"""
import importlib.util
import json
import os
import sys
import tempfile
import types
import unittest
from pathlib import Path

TESTS = Path(__file__).resolve().parent
ROOT = TESTS.parent

_home = Path(tempfile.mkdtemp(prefix="office_p1_home_"))
os.environ.setdefault("OFFICE_HOME", str(_home))
spec = importlib.util.spec_from_file_location(
    "office_server_p1", ROOT / "server" / "office_server.py")
office = importlib.util.module_from_spec(spec)
spec.loader.exec_module(office)


class ProjectNewTest(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(prefix="office_p1_"))
        self.cfg = self.dir / "office_config.json"
        self.cfg.write_text(json.dumps(
            {"_使い方": "テスト", "projects": {"Downloads/works": {"name": "既存", "role": "r"}}},
            ensure_ascii=False), encoding="utf-8")
        os.environ["OFFICE_CONFIG"] = str(self.cfg)
        self.proj = self.dir / "新しいアプリ"
        self.proj.mkdir()

    def tearDown(self):
        os.environ.pop("OFFICE_CONFIG", None)
        os.environ.pop("OFFICE_PICK_DIR", None)
        os.environ.pop("OFFICE_FAKE_LAUNCH", None)

    def test_add_project_inserts_at_top(self):
        ok, msg, info = office.add_project(str(self.proj), "テスト部", "検証")
        self.assertTrue(ok, msg)
        cfg = json.loads(self.cfg.read_text(encoding="utf-8"))
        keys = list(cfg["projects"].keys())
        self.assertEqual(len(keys), 2)
        # 先頭に挿入＝広い既存パターンより優先マッチ
        self.assertEqual(cfg["projects"][keys[0]]["name"], "テスト部")
        self.assertEqual(cfg["projects"][keys[1]]["name"], "既存")
        self.assertEqual(cfg["_使い方"], "テスト")  # projects以外のキーを保存
        self.assertFalse(info["existing"])
        self.assertFalse(info["genStarted"])  # gen_sprite未指定なら生成しない

    def test_add_project_rejects_missing_dir(self):
        ok, _msg, _ = office.add_project(str(self.dir / "nai"), "x", "")
        self.assertFalse(ok)
        ok, _msg, _ = office.add_project("", "x", "")
        self.assertFalse(ok)

    def test_add_project_duplicate_updates_not_duplicates(self):
        office.add_project(str(self.proj), "一回目", "")
        ok, _msg, info = office.add_project(str(self.proj), "二回目", "役割2")
        self.assertTrue(ok)
        self.assertTrue(info["existing"])
        cfg = json.loads(self.cfg.read_text(encoding="utf-8"))
        self.assertEqual(len(cfg["projects"]), 2)  # 重複エントリを作らない
        top = cfg["projects"][list(cfg["projects"])[0]]
        self.assertEqual(top["name"], "二回目")
        self.assertEqual(top["role"], "役割2")

    def test_broken_config_not_clobbered(self):
        self.cfg.write_text("{壊れたjson", encoding="utf-8")
        ok, _msg, _ = office.add_project(str(self.proj), "x", "")
        self.assertFalse(ok)
        self.assertEqual(self.cfg.read_text(encoding="utf-8"), "{壊れたjson")

    def test_sprite_slug_ascii_and_fallback(self):
        self.assertRegex(office.sprite_slug("pat", "新しいアプリ"), r"^proj_[0-9a-f]{6}$")
        self.assertTrue(office.sprite_slug("pat", "MyApp 2.0").startswith("myapp_2"))

    def test_sprite_slug_avoids_existing_png(self):
        # ASSETS を一時ディレクトリへ差し替え（実 assets/ の中身に依存しない）
        assets = self.dir / "assets"
        assets.mkdir()
        (assets / "app.png").write_bytes(b"x")
        orig = office.ASSETS
        office.ASSETS = assets
        try:
            self.assertEqual(office.sprite_slug("pat", "app2unique"), "app2unique")
            self.assertEqual(office.sprite_slug("pat", "app"), "app2")  # png実在→連番
        finally:
            office.ASSETS = orig

    def test_sprite_slug_avoids_reserved(self):
        # 生成中(PNG未出現)のslugは _RESERVING で予約され衝突を避ける
        assets = self.dir / "assets2"
        assets.mkdir()
        orig = office.ASSETS
        office.ASSETS = assets
        office._RESERVING.add("dup")
        try:
            self.assertEqual(office.sprite_slug("pat", "dup"), "dup2")
        finally:
            office.ASSETS = orig
            office._RESERVING.discard("dup")

    def test_rejects_home_and_broad_parents(self):
        ok, _msg, _ = office.add_project(str(Path.home()), "乗っ取り部", "")
        self.assertFalse(ok)  # ホーム自体は全社員を部分マッチで乗っ取るので拒否
        ok, _msg, _ = office.add_project(str(Path.home().parent), "親", "")
        self.assertFalse(ok)

    def test_rejects_non_string_path(self):
        ok, _msg, _ = office.add_project(123, "x", "")   # 型不正は接続断でなく False
        self.assertFalse(ok)

    def test_gen_sprite_reserves_slug(self):
        # 実生成($)を起こさないよう gen_sprite_async を差し替え
        calls = []
        orig = office.gen_sprite_async
        office.gen_sprite_async = lambda slug, label, pattern: calls.append((slug, label, pattern))
        try:
            ok, _msg, info = office.add_project(str(self.proj), "生成部", "", gen_sprite=True)
            self.assertTrue(ok)
            self.assertTrue(info["genStarted"])
            self.assertTrue(info["slug"])
            self.assertEqual(len(calls), 1)
            self.assertEqual(calls[0][0], info["slug"])
            self.assertIn(info["slug"], office._RESERVING)  # 生成キック前に予約済み
        finally:
            office.gen_sprite_async = orig
            office._RESERVING.discard(info.get("slug", ""))

    def test_set_sprite_writes_and_noop(self):
        office.add_project(str(self.proj), "着替え部", "")
        cfg = json.loads(self.cfg.read_text(encoding="utf-8"))
        pattern = list(cfg["projects"])[0]
        office._set_sprite(pattern, "gen123.png")
        cfg = json.loads(self.cfg.read_text(encoding="utf-8"))
        self.assertEqual(cfg["projects"][pattern]["sprite"], "gen123.png")
        # 存在しないpatternは無言no-op（例外なし・エントリを増やさない）
        n = len(cfg["projects"])
        office._set_sprite("no/such/pattern/xyz", "z.png")
        cfg = json.loads(self.cfg.read_text(encoding="utf-8"))
        self.assertEqual(len(cfg["projects"]), n)

    def test_generate_sprite_error_status_and_release(self):
        # subprocess.run を差し替えて失敗を模擬（実API課金なし）
        office._RESERVING.add("failslug")
        orig = office.subprocess.run
        office.subprocess.run = lambda *a, **k: types.SimpleNamespace(
            returncode=1, stdout="", stderr="boom")
        try:
            office._generate_sprite("failslug", "ラベル", "pat")
            self.assertEqual(office.GEN_STATUS["failslug"]["state"], "error")
            self.assertNotIn("failslug", office._RESERVING)  # 失敗でも予約解除
        finally:
            office.subprocess.run = orig
            office.GEN_STATUS.pop("failslug", None)

    def test_ready_themes_parses_theme_gen_source(self):
        fake_root = self.dir / "fake_root_parse"
        tools = fake_root / "tools"
        tools.mkdir(parents=True)
        (tools / "theme_gen.py").write_text(
            'THEMES_READY = ["modern", "retro"]\n', encoding="utf-8")
        orig_root = office.ROOT
        office.ROOT = fake_root
        try:
            self.assertEqual(office._ready_themes(), ["modern", "retro"])
        finally:
            office.ROOT = orig_root

    def test_generate_sprite_skips_theme_subprocess_when_none_ready(self):
        fake_root = self.dir / "fake_root_empty"
        tools = fake_root / "tools"
        tools.mkdir(parents=True)
        (tools / "theme_gen.py").write_text(
            "THEMES_READY = []\n", encoding="utf-8")
        fake_assets = self.dir / "assets_empty"
        fake_assets.mkdir()
        (fake_assets / "emptythemeslug.png").write_bytes(b"png")
        calls = []

        def fake_run(argv, **kwargs):
            calls.append((argv, kwargs))
            return types.SimpleNamespace(returncode=0, stdout="", stderr="")

        orig_root = office.ROOT
        orig_assets = office.ASSETS
        orig_run = office.subprocess.run
        orig_set_sprite = office._set_sprite
        office.ROOT = fake_root
        office.ASSETS = fake_assets
        office.subprocess.run = fake_run
        office._set_sprite = lambda *args: None
        try:
            office._generate_sprite("emptythemeslug", "空テーマ", "pat")
            self.assertEqual(len(calls), 1)  # vintage 生成のみ
            self.assertFalse(any(
                Path(call[0][1]).name == "theme_gen.py" for call in calls))
        finally:
            office.ROOT = orig_root
            office.ASSETS = orig_assets
            office.subprocess.run = orig_run
            office._set_sprite = orig_set_sprite
            office.GEN_STATUS.pop("emptythemeslug", None)
            office._RESERVING.discard("emptythemeslug")

    def test_generate_sprite_theme_lane_is_primary_and_promotes_to_plain(self):
        """R23.5: READYテーマがあれば theme_gen が主レーン＝__テーマ版を無印へ昇格し、assets_genは呼ばない"""
        fake_root = self.dir / "fake_root_modern"
        tools = fake_root / "tools"
        tools.mkdir(parents=True)
        theme_gen = tools / "theme_gen.py"
        theme_gen.write_text(
            'THEMES_READY = ["modern"]\n', encoding="utf-8")
        fake_assets = self.dir / "assets_modern"
        fake_assets.mkdir()
        (fake_assets / "modernslug__modern.png").write_bytes(b"themedpng")
        (fake_assets / "modernslug__modern_walk.png").write_bytes(b"themedwalk")
        calls = []

        def fake_run(argv, **kwargs):
            calls.append((argv, kwargs))
            return types.SimpleNamespace(returncode=0, stdout="", stderr="")

        orig_root = office.ROOT
        orig_assets = office.ASSETS
        orig_run = office.subprocess.run
        orig_set_sprite = office._set_sprite
        office.ROOT = fake_root
        office.ASSETS = fake_assets
        office.subprocess.run = fake_run
        office._set_sprite = lambda *args: None
        try:
            office._generate_sprite("modernslug", "モダン部", "pat")
            self.assertEqual(len(calls), 1)  # theme主レーンのみ・assets_genフォールバック不発
            self.assertEqual(calls[0][0], [
                sys.executable, str(theme_gen), "modern", "custom",
                "modernslug", "モダン部",
            ])
            self.assertEqual((fake_assets / "modernslug.png").read_bytes(), b"themedpng")
            self.assertEqual((fake_assets / "modernslug_walk.png").read_bytes(), b"themedwalk")
            self.assertEqual(office.GEN_STATUS["modernslug"]["state"], "done")
        finally:
            office.ROOT = orig_root
            office.ASSETS = orig_assets
            office.subprocess.run = orig_run
            office._set_sprite = orig_set_sprite
            office.GEN_STATUS.pop("modernslug", None)
            office._RESERVING.discard("modernslug")

    def test_generate_sprite_falls_back_to_assets_gen_when_theme_lane_fails(self):
        """R23.5: theme_gen が成果物を出せない(Codex不調等)なら assets_gen custom へフォールバック"""
        fake_root = self.dir / "fake_root_fallback"
        tools = fake_root / "tools"
        tools.mkdir(parents=True)
        theme_gen = tools / "theme_gen.py"
        theme_gen.write_text(
            'THEMES_READY = ["modern"]\n', encoding="utf-8")
        fake_assets = self.dir / "assets_fallback"
        fake_assets.mkdir()
        (fake_assets / "fallbackslug.png").write_bytes(b"apipng")   # assets_gen成果物を模擬
        calls = []

        def fake_run(argv, **kwargs):
            calls.append((argv, kwargs))
            return types.SimpleNamespace(returncode=0, stdout="", stderr="")

        orig_root = office.ROOT
        orig_assets = office.ASSETS
        orig_run = office.subprocess.run
        orig_set_sprite = office._set_sprite
        office.ROOT = fake_root
        office.ASSETS = fake_assets
        office.subprocess.run = fake_run
        office._set_sprite = lambda *args: None
        try:
            office._generate_sprite("fallbackslug", "予備部", "pat")
            self.assertEqual(len(calls), 2)  # theme失敗→assets_gen
            self.assertEqual(calls[0][0][2:], ["modern", "custom", "fallbackslug", "予備部"])
            self.assertEqual(Path(calls[1][0][1]).name, "assets_gen.py")
            self.assertEqual(calls[1][0][2:], ["custom", "fallbackslug", "予備部"])
            self.assertEqual(office.GEN_STATUS["fallbackslug"]["state"], "done")
        finally:
            office.ROOT = orig_root
            office.ASSETS = orig_assets
            office.subprocess.run = orig_run
            office._set_sprite = orig_set_sprite
            office.GEN_STATUS.pop("fallbackslug", None)
            office._RESERVING.discard("fallbackslug")

    def test_pick_folder_mock(self):
        os.environ["OFFICE_PICK_DIR"] = str(self.proj)
        ok, path = office.pick_folder()
        self.assertTrue(ok)
        self.assertEqual(path, str(self.proj))
        os.environ["OFFICE_PICK_DIR"] = str(self.dir / "nai")
        ok, _ = office.pick_folder()
        self.assertFalse(ok)

    def test_launch_mock_marker(self):
        marker = self.dir / "launched.txt"
        os.environ["OFFICE_FAKE_LAUNCH"] = str(marker)
        ok, _msg, info = office.add_project(str(self.proj), "起動部", "", launch=True)
        self.assertTrue(ok)
        self.assertTrue(info["launched"])
        self.assertEqual(marker.read_text(encoding="utf-8"), str(self.proj.resolve()))


if __name__ == "__main__":
    unittest.main()
