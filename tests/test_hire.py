#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""R90-D11: 雇う（hire_session / POST /api/hire / 遠隔ゲート）の番人。

守るもの:
  1. cwd は projects_index に在る登録済みプロジェクトだけ（未登録 projectId は denied＝任意パス起動不可）
  2. argv は固定形（--bg [-n name] [-w] <prompt>・shell=False）・cwd は引き当てた登録パス
  3. 入力検証（projectId 形式・prompt 空/長すぎ・name 文字種）・同時 2 本まで（busy）
  4. 遠隔（act 封筒）は config remoteHire:true のときだけ受理。既定は denied（reason=remote-hire-off）
  5. 結果は office_actions の台帳に kind=hire で残り、results_public に bgId が出る
"""
import importlib.util
import json
import os
import pathlib
import stat
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

_home = Path(tempfile.mkdtemp(prefix="office_hire_home_"))
_proj_dir = _home / "demo-project"
_proj_dir.mkdir()
_transcripts = _home / ".claude" / "projects" / "-demo-project"
_transcripts.mkdir(parents=True)
(_transcripts / "sess-hire-0001.jsonl").write_text(
    json.dumps({"cwd": str(_proj_dir), "type": "user"}) + "\n", encoding="utf-8")
(_home / ".claude" / "office_inbox").mkdir()
_config = _home / "office_config.json"
_config.write_text('{"projects": {}}\n', encoding="utf-8")
os.environ["OFFICE_HOME"] = str(_home)
os.environ["OFFICE_CONFIG"] = str(_config)
os.environ["OFFICE_FAKE_NOTIFY"] = str(_home / "notify.log")
spec = importlib.util.spec_from_file_location("office_server_hire", ROOT / "server" / "office_server.py")
office = importlib.util.module_from_spec(spec)
spec.loader.exec_module(office)
PID = office.project_id_for(str(_proj_dir))
# projects_index は共有モジュール（import 時に OFFICE_HOME を固定・60秒キャッシュ）。discover で他テストの
# HOME が先に入ると引き当てが空振りするので、このテストの HOME に向け直してキャッシュを捨てる。
office.projects_index.PROJECTS = _home / ".claude" / "projects"


def fake_claude(body):
    d = Path(tempfile.mkdtemp(prefix="fake_claude_"))
    p = d / "claude"
    p.write_text("#!/bin/bash\n" + body + "\n", encoding="utf-8")
    p.chmod(p.stat().st_mode | stat.S_IXUSR)
    os.environ["OFFICE_CLAUDE_BIN"] = str(p)
    return d


class HireTest(unittest.TestCase):
    def setUp(self):
        for k in ("OFFICE_FAKE_HIRE", "OFFICE_CLAUDE_BIN"):
            os.environ.pop(k, None)
        office._HIRE_ACTIVE["n"] = 0
        os.environ["OFFICE_CONFIG"] = str(_config)       # 他テストの tearDown が pop するので毎回張り直す
        _config.write_text('{"projects": {}}\n', encoding="utf-8")
        office.projects_index.PROJECTS = _home / ".claude" / "projects"
        office.projects_index._cache.update({"at": None, "data": None})
        office.projects_index._cwd_cache.clear()
        office._LAUNCHABLE_CACHE.update({"ts": 0, "data": []})

    def test_unregistered_project_is_denied(self):
        os.environ["OFFICE_FAKE_HIRE"] = str(_home / "hire0.marker")
        ok, msg, extra = office.hire_session("0123456789ab", "hello")
        self.assertFalse(ok)
        self.assertEqual(extra["state"], "denied")
        self.assertFalse((_home / "hire0.marker").exists())

    def test_fake_marker_records_cwd_prompt_name(self):
        os.environ["OFFICE_FAKE_HIRE"] = str(_home / "hire.marker")
        ok, msg, extra = office.hire_session(PID, "  README を  要約して ", "verify-bot", worktree=True)
        self.assertTrue(ok, msg)
        rec = json.loads((_home / "hire.marker").read_text(encoding="utf-8"))
        self.assertEqual(rec, {"cwd": str(_proj_dir), "prompt": "README を 要約して",
                               "name": "verify-bot", "worktree": True})
        self.assertEqual(extra["state"], "done")
        pub = [r for r in office.office_actions.results_public(limit=20) if r["reqId"] == extra["reqId"]]
        self.assertEqual((pub[0]["kind"], pub[0]["state"], pub[0]["bgId"]), ("hire", "done", "fake0001"))
        self.assertIn("入社", (_home / "notify.log").read_text(encoding="utf-8"))

    def test_input_validation(self):
        os.environ["OFFICE_FAKE_HIRE"] = str(_home / "hire2.marker")
        self.assertEqual(office.hire_session("zzz", "x")[2]["state"], "denied")
        self.assertEqual(office.hire_session(PID, "   ")[2]["state"], "denied")
        self.assertEqual(office.hire_session(PID, "x" * 4001)[2]["state"], "denied")
        self.assertEqual(office.hire_session(PID, "x", name="悪い名前;rm")[2]["state"], "denied")
        self.assertEqual(office.hire_session(PID, "hello\x00world")[2]["state"], "denied")
        self.assertFalse((_home / "hire2.marker").exists())

    def test_busy_when_two_in_flight(self):
        os.environ["OFFICE_FAKE_HIRE"] = str(_home / "hire3.marker")
        office._HIRE_ACTIVE["n"] = 2
        ok, msg, extra = office.hire_session(PID, "hello")
        self.assertFalse(ok)
        self.assertEqual(extra["state"], "busy")
        self.assertEqual(office._HIRE_ACTIVE["n"], 2)          # 増減していない

    def test_real_argv_and_cwd_with_fake_binary(self):
        d = fake_claude('pwd > "$(dirname "$0")/cwd.txt"; printf "%s\\n" "$@" > "$(dirname "$0")/argv.txt"; echo "Started background session 1a2b3c4d"; exit 0')
        ok, msg, extra = office.hire_session(PID, "fix the tests", name="fixer", worktree=True)
        self.assertTrue(ok, msg)
        self.assertEqual(extra["bgId"], "1a2b3c4d")
        self.assertEqual((d / "argv.txt").read_text(encoding="utf-8").splitlines(),
                         ["--bg", "-n", "fixer", "-w", "--", "fix the tests"])   # -- でオプション終端
        self.assertEqual(Path((d / "cwd.txt").read_text(encoding="utf-8").strip()).resolve(), _proj_dir.resolve())

    def test_option_like_prompt_stays_after_terminator(self):
        """`--exec=<cmd>` は claude のシェル実行モード。-- の後ろに置かれオプションに化けない（レビュー指摘）。"""
        d = fake_claude('printf "%s\\n" "$@" > "$(dirname "$0")/argv.txt"; echo "ok 0badc0de"; exit 0')
        ok, msg, extra = office.hire_session(PID, "--exec=printf PROBE", worktree=True)
        self.assertTrue(ok, msg)
        argv = (d / "argv.txt").read_text(encoding="utf-8").splitlines()
        self.assertEqual(argv, ["--bg", "-w", "--", "--exec=printf PROBE"])

    def test_same_reqid_in_parallel_launches_once(self):
        """同じ reqId の並列配達（at-least-once の再送）で claude --bg を2回起動しない・台帳も1件。"""
        import threading
        d = fake_claude('sleep 0.6; printf "x\\n" >> "$(dirname "$0")/launches.txt"; echo "ok 1234abcd"; exit 0')
        results = []
        def go():
            results.append(office.hire_session(PID, "dup test", req_id="req-dup-0001", device="devA"))
        ts = [threading.Thread(target=go) for _ in range(2)]
        [t.start() for t in ts]; [t.join() for t in ts]
        self.assertEqual(len((d / "launches.txt").read_text(encoding="utf-8").splitlines()), 1)
        recs = [r for r in office.office_actions.results_public(limit=50) if r["reqId"] == "req-dup-0001"]
        self.assertEqual(len(recs), 1)
        self.assertEqual(recs[0]["state"], "done")
        audit = pathlib.Path(office.office_actions.AUDIT_FILE).read_text(encoding="utf-8")   # 共有モジュールの実パス（discover で HOME が先に固定される）
        line = [l for l in audit.splitlines() if "req-dup-0001" in l][-1]
        self.assertIn('"device": "devA"', line)                    # 端末IDが監査に残る

    def test_nonzero_exit_is_failed(self):
        fake_claude("echo boom >&2; exit 3")
        ok, msg, extra = office.hire_session(PID, "hello")
        self.assertFalse(ok)
        self.assertEqual(extra["state"], "failed")

    def test_remote_gate_default_off_and_opt_in(self):
        os.environ["OFFICE_FAKE_HIRE"] = str(_home / "hire4.marker")
        act = {"aioffice": 1, "kind": "hire", "reqId": "req-hire-0001", "project": PID,
               "prompt": "hello from phone", "name": "phone"}
        ok, state, extra = office._action_exec({"action": act, "device_id": "dev1"})
        self.assertFalse(ok)
        self.assertEqual((extra["state"], extra["reason"]), ("denied", "remote-hire-off"))
        self.assertFalse((_home / "hire4.marker").exists())
        _config.write_text('{"projects": {}, "remoteHire": true}\n', encoding="utf-8")
        act["reqId"] = "req-hire-0002"
        ok, state, extra = office._action_exec({"action": act, "device_id": "dev1"})
        self.assertTrue(ok, extra)
        self.assertEqual(extra["state"], "done")
        self.assertTrue((_home / "hire4.marker").exists())
        # 同じ reqId は再実行しない（at-least-once の再配達で二重雇用しない）
        (_home / "hire4.marker").unlink()
        ok2, state2, extra2 = office._action_exec({"action": act, "device_id": "dev1"})
        self.assertTrue(ok2)
        self.assertFalse((_home / "hire4.marker").exists())

    def test_parse_action_hire(self):
        pa = office.office_actions.parse_action
        base = {"aioffice": 1, "kind": "hire", "reqId": "req-parse-0001", "project": PID, "prompt": " do it ", "name": "n"}
        self.assertEqual(pa(json.dumps(base))["prompt"], "do it")
        self.assertIsNone(pa(json.dumps({**base, "prompt": "x" * 2001})))
        self.assertIsNone(pa(json.dumps({**base, "name": "x" * 31})))
        self.assertIsNone(pa(json.dumps({**base, "project": "not-a-pid"})))
        self.assertIsNone(pa(json.dumps({**base, "prompt": ""})))


if __name__ == "__main__":
    unittest.main()
