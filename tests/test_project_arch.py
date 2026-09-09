"""Accessory persistence and the local-only API boundary."""
import importlib.util
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, Mock

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("office_arch_test", ROOT / "server/office_server.py")
office = importlib.util.module_from_spec(_spec)
# Import under an isolated home; no real config or state is used by these tests.
with tempfile.TemporaryDirectory() as _home, patch.dict(os.environ, {"OFFICE_HOME": _home}):
    _spec.loader.exec_module(office)


class ProjectArchTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.cfg = Path(self.tmp.name) / "office_config.json"
        self.original = {"lang": "en", "projects": {"fixture/project": {"name": "Demo", "role": "coding"},
                                                  "fixture/other": {"name": "Other"}}}
        self.cfg.write_text(json.dumps(self.original), encoding="utf-8")
        self.pid = "012345abcdef"
        self.rows = {"roster": [{"projectId": self.pid, "cwd": "/fixture/project"}]}
        for target, value in (("config_file", lambda: self.cfg), ("office_json", lambda: self.rows)):
            patcher = patch.object(office, target, value)
            patcher.start(); self.addCleanup(patcher.stop)
        patcher = patch.object(office.projects_index, "projects_json", return_value={"projects": []})
        patcher.start(); self.addCleanup(patcher.stop)

    def test_every_choice_roundtrips_without_losing_other_settings(self):
        for arch in [*office.PROJECT_ARCHES, None]:
            with patch.object(office, "_file_flock", wraps=office._file_flock) as lock:
                self.assertTrue(office.set_project_arch(self.pid, arch)[0])
                lock.assert_called_once_with(self.cfg)
            saved = json.loads(self.cfg.read_text())
            self.assertEqual(saved["projects"]["fixture/project"], {**self.original["projects"]["fixture/project"], "arch": arch})
            self.assertEqual(saved["lang"], "en")
            self.assertEqual(saved["projects"]["fixture/other"], {"name": "Other"})
            self.assertEqual(office._cache["t"], 0)
            grouped = office.group_by_project([{"session": "a", "cwd": "/fixture/project", "arch": arch}])
            self.assertEqual(grouped[0]["arch"], arch)

    def test_invalid_unknown_and_broken_config_are_not_written(self):
        before = self.cfg.read_bytes()
        for pid, arch in [(self.pid, "dev"), (self.pid, {}), (self.pid, False),
                          ("../project", "cap"), ("A" * 12, None), (None, None), ("0" * 12, None)]:
            self.assertFalse(office.set_project_arch(pid, arch)[0])
            self.assertEqual(self.cfg.read_bytes(), before)
        self.cfg.write_text("{broken")
        self.assertFalse(office.set_project_arch(self.pid, "cap")[0])
        self.assertEqual(self.cfg.read_text(), "{broken")

    def test_endpoint_requires_loopback_csrf_and_explicit_arch(self):
        for addr, headers, payload, code in [
            ("192.0.2.1", {"X-Office-Local": "1"}, {"arch": "cap"}, 403),
            ("127.0.0.1", {}, {"arch": "cap"}, 403),
            ("127.0.0.1", {"X-Office-Local": "1", "Origin": "https://example.invalid"}, {"arch": "cap"}, 403),
            ("127.0.0.1", {"X-Office-Local": "1"}, {}, 400),
        ]:
            handler = object.__new__(office.Handler)
            handler.path = "/api/project/arch"; handler.client_address = (addr, 12345)
            body = json.dumps({"projectId": self.pid, **payload}).encode()
            handler.headers = {"Host": "127.0.0.1:4797", "Content-Length": str(len(body)), **headers}
            handler.rfile = io.BytesIO(body); handler._deny = Mock(); handler._send = Mock()
            with patch.object(office, "set_project_arch") as write:
                handler.do_POST(); write.assert_not_called()
            self.assertEqual(handler._deny.call_args.args[0], code)


class AvatarStyleTest(ProjectArchTest):
    """R91: 1体ずつ変えられること（本人指摘「統一で全部変わるのは中途半端」）。"""

    def setUp(self):
        super().setUp()
        self.other = "abcdef012345"
        self.rows["roster"] = [
            {"projectId": self.pid, "cwd": "/fixture/project"},
            {"projectId": self.other, "cwd": "/fixture/project"},
        ]

    def test_session_scope_touches_one_avatar_and_not_its_neighbours(self):
        self.assertTrue(office.set_avatar_style(self.pid, "session", {"arch": "cap"})[0])
        saved = json.loads(self.cfg.read_text())
        self.assertEqual(saved["avatars"][self.pid]["arch"], "cap")
        self.assertNotIn(self.other, saved["avatars"])
        # 同じフォルダの設定は動かない＝隣の席の帽子が変わらない
        self.assertEqual(saved["projects"]["fixture/project"], self.original["projects"]["fixture/project"])

    def test_color_and_accessory_are_independent_fields(self):
        self.assertTrue(office.set_avatar_style(self.pid, "session", {"color": "sage"})[0])
        self.assertTrue(office.set_avatar_style(self.pid, "session", {"arch": "beret"})[0])
        saved = json.loads(self.cfg.read_text())["avatars"][self.pid]
        self.assertEqual((saved["arch"], saved["color"]), ("beret", "sage"))

    def test_project_scope_clears_the_per_avatar_overrides_it_would_be_hidden_by(self):
        office.set_avatar_style(self.pid, "session", {"arch": "cap"})
        office.set_avatar_style(self.other, "session", {"arch": "beret"})
        self.assertTrue(office.set_avatar_style(self.pid, "project", {"arch": "bowtie"})[0])
        saved = json.loads(self.cfg.read_text())
        self.assertEqual(saved["projects"]["fixture/project"]["arch"], "bowtie")
        self.assertEqual(saved["avatars"], {})

    def test_rejects_bad_scope_bad_color_absent_avatar_and_empty_style(self):
        before = self.cfg.read_bytes()
        for avatar_id, scope, fields in [
            (self.pid, "global", {"arch": "cap"}),
            (self.pid, "session", {"color": "neon"}),
            (self.pid, "session", {"color": 3}),
            (self.pid, "session", {}),
            (self.pid, "session", {"zzz": "cap"}),
            ("f" * 12, "session", {"arch": "cap"}),      # 出勤していない個体
            ("nothex" * 2, "session", {"arch": "cap"}),
        ]:
            self.assertFalse(office.set_avatar_style(avatar_id, scope, fields)[0],
                             f"{scope} {fields} should be refused")
            self.assertEqual(self.cfg.read_bytes(), before)

    def test_the_table_is_capped_so_dead_sessions_cannot_grow_the_config(self):
        keep = office.AVATAR_STYLE_MAX
        rows = [{"projectId": f"{i:012x}", "cwd": "/fixture/project"} for i in range(keep + 5)]
        self.rows["roster"] = rows
        with patch.object(office.time, "time", side_effect=range(1000, 1000 + len(rows))):
            for row in rows:
                self.assertTrue(office.set_avatar_style(row["projectId"], "session", {"arch": "cap"})[0])
        avatars = json.loads(self.cfg.read_text())["avatars"]
        self.assertEqual(len(avatars), keep)
        self.assertNotIn(rows[0]["projectId"], avatars)      # 古い順に捨てる
        self.assertIn(rows[-1]["projectId"], avatars)

    def test_folder_wide_apply_also_clears_avatars_that_are_off_duty(self):
        """Astra レビュー指摘: 鮮度の窓から外れて非表示の個体が古い帽子を蘇らせる。"""
        office.set_avatar_style(self.other, "session", {"arch": "cap"})
        # other が退勤して roster から消えた状態でフォルダ全員へ適用する
        self.rows["roster"] = [{"projectId": self.pid, "cwd": "/fixture/project"}]
        self.assertTrue(office.set_avatar_style(self.pid, "project", {"arch": "bowtie"})[0])
        self.assertEqual(json.loads(self.cfg.read_text())["avatars"], {})

    def test_clearing_still_works_after_the_folder_gets_its_own_config_entry(self):
        """Astra レビュー指摘: 後から add_project すると設定パターンが変わる。

        だから個体には「設定パターン」ではなく実フォルダ（cwd）を控える。
        """
        office.set_avatar_style(self.other, "session", {"arch": "cap"})
        saved = json.loads(self.cfg.read_text())
        self.assertEqual(saved["avatars"][self.other]["cwd"], "/fixture/project")
        # 個別登録でパターンが変わった状況を作る（旧実装はこれで解除に失敗した）
        saved["projects"] = {"/fixture/project": {"name": "Demo"}, **saved["projects"]}
        self.cfg.write_text(json.dumps(saved), encoding="utf-8")
        self.rows["roster"] = [{"projectId": self.pid, "cwd": "/fixture/project"}]
        self.assertTrue(office.set_avatar_style(self.pid, "project", {"arch": "bowtie"})[0])
        self.assertEqual(json.loads(self.cfg.read_text())["avatars"], {})

    def test_pruning_survives_a_hand_broken_timestamp(self):
        """Astra レビュー指摘: at が null だと剪定のソートが TypeError で落ちる。"""
        keep = office.AVATAR_STYLE_MAX
        broken = {f"{i:012x}": {"arch": "cap", "at": None if i == 0 else i} for i in range(keep)}
        self.cfg.write_text(json.dumps({**self.original, "avatars": broken}), encoding="utf-8")
        self.assertTrue(office.set_avatar_style(self.pid, "session", {"color": "sage"})[0])
        avatars = json.loads(self.cfg.read_text())["avatars"]
        self.assertEqual(len(avatars), keep)
        self.assertNotIn("000000000000", avatars)     # at が壊れたものを最古として捨てる
        self.assertEqual(avatars[self.pid]["color"], "sage")

    def test_endpoint_requires_loopback_and_csrf(self):
        for addr, headers, code in [
            ("192.0.2.1", {"X-Office-Local": "1"}, 403),
            ("127.0.0.1", {}, 403),
            ("127.0.0.1", {"X-Office-Local": "1", "Origin": "https://example.invalid"}, 403),
        ]:
            handler = object.__new__(office.Handler)
            handler.path = "/api/avatar/style"; handler.client_address = (addr, 12345)
            body = json.dumps({"avatarId": self.pid, "scope": "session", "arch": "cap"}).encode()
            handler.headers = {"Host": "127.0.0.1:4797", "Content-Length": str(len(body)), **headers}
            handler.rfile = io.BytesIO(body); handler._deny = Mock(); handler._send = Mock()
            with patch.object(office, "set_avatar_style") as write:
                handler.do_POST(); write.assert_not_called()
            self.assertEqual(handler._deny.call_args.args[0], code)


if __name__ == "__main__":
    unittest.main()
