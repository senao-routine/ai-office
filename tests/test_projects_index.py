#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import importlib.util
import json
import os
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PROJECTS_INDEX = ROOT / "server" / "projects_index.py"
OFFICE_SERVER = ROOT / "server" / "office_server.py"
_module_serial = 0


def load_module(path, prefix):
    global _module_serial
    _module_serial += 1
    spec = importlib.util.spec_from_file_location(f"{prefix}_{_module_serial}", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ProjectsIndexTests(unittest.TestCase):
    ENV_KEYS = ("OFFICE_HOME", "OFFICE_CONFIG", "OFFICE_DATA")

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="projects_index_test_")
        self.home = Path(self.temp.name)
        self.projects = self.home / ".claude" / "projects"
        self.projects.mkdir(parents=True)
        self.config = self.home / "office_config.json"
        self.config.write_text('{"projects": {}}\n', encoding="utf-8")
        self.old_env = {key: os.environ.get(key) for key in self.ENV_KEYS}
        os.environ["OFFICE_HOME"] = str(self.home)
        os.environ["OFFICE_CONFIG"] = str(self.config)
        os.environ.pop("OFFICE_DATA", None)
        self.now = 2_000_000_000.0

    def tearDown(self):
        for key, value in self.old_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        self.temp.cleanup()

    def session(self, dirname, filename, mtime, rows):
        directory = self.projects / dirname
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / filename
        path.write_text("".join(
            row if isinstance(row, str) else json.dumps(row, ensure_ascii=False) + "\n"
            for row in rows
        ), encoding="utf-8")
        os.utime(path, (mtime, mtime))
        return path

    def test_lists_new_and_old_projects_sorted_by_last_activity(self):
        recent = "-Users-test-recent-project"
        old = "-Users-test-old-project"
        self.session(recent, "older.jsonl", self.now - 120, [{"cwd": "/wrong/older"}])
        self.session(recent, "latest.jsonl", self.now - 10, [{"cwd": "/actual/recent-project"}])
        nested = self.projects / recent / "session-id" / "subagents"
        nested.mkdir(parents=True)
        (nested / "agent.jsonl").write_text('{"cwd":"/nested"}\n', encoding="utf-8")
        self.session(old, "sess.jsonl", self.now - 8 * 24 * 3600,
                     [{"cwd": "/Users/test/old/project"}])

        data = load_module(PROJECTS_INDEX, "projects_index").projects_json(self.now)
        self.assertEqual([recent, old], [project["dir"] for project in data["projects"]])
        self.assertEqual(2, data["projects"][0]["sessions"])
        self.assertEqual(1, data["projects"][1]["sessions"])
        self.assertEqual("/actual/recent-project", data["projects"][0]["cwd"])
        self.assertTrue(data["projects"][0]["active"])
        self.assertFalse(data["projects"][1]["active"])
        self.assertGreater(data["projects"][0]["lastActive"], data["projects"][1]["lastActive"])

    def test_cwd_uses_latest_jsonl_head_then_falls_back_to_dirname(self):
        preferred = "-Users-guessed-name"
        self.session(preferred, "sess.jsonl", self.now - 1,
                     ["not json\n", {"type": "system"}, {"cwd": "/Volumes/work/real-name"}])
        fallback = "-Users-test-fallback-project"
        self.session(fallback, "sess.jsonl", self.now - 2, [{"type": "system"}])

        rows = load_module(PROJECTS_INDEX, "projects_index").projects_json(self.now)["projects"]
        by_dir = {project["dir"]: project for project in rows}
        self.assertEqual("/Volumes/work/real-name", by_dir[preferred]["cwd"])
        self.assertEqual("/Users/test/fallback/project", by_dir[fallback]["cwd"])

    def test_config_link_uses_nfc_partial_match_for_name(self):
        dirname = "-Users-test-cafe-project"
        self.session(dirname, "sess.jsonl", self.now - 1,
                     [{"cwd": "/Users/test/cafe\u0301/project"}])
        self.config.write_text(json.dumps({"projects": {
            "café": {"name": "喫茶開発部"},
        }}, ensure_ascii=False), encoding="utf-8")

        project = load_module(PROJECTS_INDEX, "projects_index").projects_json(self.now)["projects"][0]
        self.assertEqual("喫茶開発部", project["name"])
        self.assertNotIn("sprite", project)   # R80: スプライト全廃

    def test_empty_and_non_directory_entries_are_skipped(self):
        (self.projects / "-broken-empty").mkdir()
        (self.projects / "-broken-file").write_text("not a directory", encoding="utf-8")
        data = load_module(PROJECTS_INDEX, "projects_index").projects_json(self.now)
        self.assertEqual([], data["projects"])

    def test_result_cache_lasts_sixty_seconds(self):
        self.session("-Users-test-one", "sess.jsonl", self.now - 1, [{"cwd": "/tmp/one"}])
        module = load_module(PROJECTS_INDEX, "projects_index")
        first = module.projects_json(self.now)
        self.session("-Users-test-two", "sess.jsonl", self.now + 1, [{"cwd": "/tmp/two"}])
        cached = module.projects_json(self.now + 59)
        refreshed = module.projects_json(self.now + 60)
        self.assertIs(first, cached)
        self.assertEqual(1, len(cached["projects"]))
        self.assertEqual(2, len(refreshed["projects"]))

    def test_cwd_resolution_is_memoized_by_directory(self):
        dirname = "-Users-test-memo"
        path = self.session(dirname, "sess.jsonl", self.now - 1, [{"cwd": "/first/path"}])
        module = load_module(PROJECTS_INDEX, "projects_index")
        first = module.projects_json(self.now)["projects"][0]
        path.write_text('{"cwd":"/changed/path"}\n', encoding="utf-8")
        os.utime(path, (self.now + 60, self.now + 60))
        second = module.projects_json(self.now + 61)["projects"][0]
        self.assertEqual("/first/path", first["cwd"])
        self.assertEqual("/first/path", second["cwd"])
        self.assertEqual(self.now + 60, second["lastActive"])

    def test_office_json_top_level_is_not_polluted(self):
        office = load_module(OFFICE_SERVER, "office_server")
        data = office.office_json()
        # R50: roster/tasks は新UIの集約ビュー（中継へは relay_agent が redact する）。
        # "projects" だけは**入れてはいけない**＝projects_index が返すローカルパス一覧のキー名。
        # R80.6: res(Claude枠%の最小ゲージ)・launchable(projectId+名前のみ)を意図的に追加。
        # R82: templates（ユーザー定義定型文=遠隔利用が目的の意図的搬送）も追加。
        # R85-2: rosterCounts は撤去（読者ゼロ）。R86-A: avatarMode（session/project・機微なし）追加。
        self.assertEqual({"officeName", "employees", "history", "generatedAt", "setup",
                          "counts", "edition", "lang", "avatarMode", "roster", "tasks",
                          "actions", "relay", "res", "launchable", "templates"},
                         set(data))
        self.assertNotIn("projects", data)
        # launchable は中継へ流れる前提＝ローカルパスを1バイトも運ばない（projectIdはハッシュ）
        for pj in data["launchable"]:
            self.assertEqual({"projectId", "name", "ageSec"}, set(pj))
            self.assertNotIn("/", pj["projectId"])


if __name__ == "__main__":
    unittest.main()
