#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""R79-10 遠隔実行コアの検証（server/office_actions.py）。

最も厚くテストする場所＝scrub_output（中継へ何を出すかの最終防波堤）と
validate_recipes（許可リストの受け入れ条件）。実行系は「タイムアウトで孫まで死ぬ」
「同時実行上限」「不明レシピはdenied」「reqId冪等」を実プロセスで確かめる。
"""
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

_TMP_HOME = tempfile.mkdtemp(prefix="aioffice-actions-")
os.environ["OFFICE_HOME"] = _TMP_HOME       # import前に注入（RECIPES_FILE 等が確定する）
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "server"))
import office_actions as oa  # noqa: E402


def _recipe(**kw):
    base = {"id": "r_ok", "label": "テスト", "argv": ["/bin/echo", "hi"],
            "cwd": "/tmp", "timeoutSec": 10, "returnOutput": "none"}
    base.update(kw)
    return base


def _valid(**kw):
    """start_action に渡すのは必ず validate 済みレシピ（実運用は load_recipes 経由）。"""
    recipes, errors = oa.validate_recipes({"recipes": [_recipe(**kw)]})
    assert not errors, errors
    return recipes[0]


class TestValidateRecipes(unittest.TestCase):
    def test_ok(self):
        recipes, errors = oa.validate_recipes({"recipes": [_recipe()]})
        self.assertEqual(errors, [])
        self.assertEqual(recipes[0]["id"], "r_ok")
        self.assertFalse(recipes[0]["dangerous"])

    def test_shell_string_argv_rejected(self):
        """argvは配列のみ＝shell文字列は受け付けない（shell=Falseの前提を入口で守る）。"""
        _, errors = oa.validate_recipes({"recipes": [_recipe(argv="bash verify.sh")]})
        self.assertTrue(any("argv" in e for e in errors))

    def test_relative_cwd_rejected(self):
        for bad in ("relative/dir", "~/works", ""):
            _, errors = oa.validate_recipes({"recipes": [_recipe(cwd=bad)]})
            self.assertTrue(any("cwd" in e for e in errors), bad)

    def test_bad_id_and_dup(self):
        _, errors = oa.validate_recipes({"recipes": [_recipe(id="BAD ID")]})
        self.assertTrue(any("id" in e for e in errors))
        recipes, errors = oa.validate_recipes({"recipes": [_recipe(), _recipe()]})
        self.assertEqual(len(recipes), 1)
        self.assertTrue(any("重複" in e for e in errors))

    def test_env_allowlist(self):
        ok, errors = oa.validate_recipes({"recipes": [_recipe(env={"CI": "1"})]})
        self.assertEqual(errors, [])
        self.assertEqual(ok[0]["env"], {"CI": "1"})
        _, errors = oa.validate_recipes({"recipes": [_recipe(env={"bad-name": "1"})]})
        self.assertTrue(any("env" in e for e in errors))
        _, errors = oa.validate_recipes({"recipes": [_recipe(env={"PATH": "x" * 500})]})
        self.assertTrue(any("env" in e for e in errors))

    def test_timeout_bounds(self):
        for bad in (0, 3601, "abc"):
            _, errors = oa.validate_recipes({"recipes": [_recipe(timeoutSec=bad)]})
            self.assertTrue(any("timeoutSec" in e for e in errors), bad)

    def test_return_output_enum(self):
        _, errors = oa.validate_recipes({"recipes": [_recipe(returnOutput="everything")]})
        self.assertTrue(any("returnOutput" in e for e in errors))

    def test_save_load_roundtrip_0600(self):
        oa.save_recipes([_recipe()])
        self.assertEqual(oa.RECIPES_FILE.stat().st_mode & 0o777, 0o600)
        recipes, errors = oa.load_recipes()
        self.assertEqual((len(recipes), errors), (1, []))
        oa.RECIPES_FILE.unlink()
        self.assertEqual(oa.load_recipes(), ([], []))   # 無し=レシピゼロ（実行できるものが無い）


class TestParseAction(unittest.TestCase):
    def test_run_ok(self):
        act = oa.parse_action(json.dumps(
            {"aioffice": 1, "kind": "run", "recipe": "r_verify", "args": [],
             "reqId": "abc12345"}))
        self.assertEqual(act, {"kind": "run", "recipe": "r_verify", "reqId": "abc12345"})

    def test_launch_ok(self):
        act = oa.parse_action(json.dumps(
            {"aioffice": 1, "kind": "launch", "project": "0123456789ab",
             "reqId": "req-00000001"}))
        self.assertEqual(act["kind"], "launch")

    def test_rejects(self):
        bad = [
            "not json",
            json.dumps({"kind": "run", "recipe": "r", "reqId": "abc12345"}),      # aioffice無し
            json.dumps({"aioffice": 1, "kind": "run", "reqId": "abc12345"}),       # recipe無し
            json.dumps({"aioffice": 1, "kind": "run", "recipe": "r", "reqId": "x"}),  # reqId短い
            json.dumps({"aioffice": 1, "kind": "run", "recipe": "../etc", "reqId": "abc12345"}),
            json.dumps({"aioffice": 1, "kind": "run", "recipe": "r",
                        "args": ["--force"], "reqId": "abc12345"}),                # 引数注入は不可
            json.dumps({"aioffice": 1, "kind": "launch", "project": "/etc",
                        "reqId": "abc12345"}),                                     # パスは不可
            json.dumps({"aioffice": 1, "kind": "shell", "reqId": "abc12345"}),
        ]
        for b in bad:
            self.assertIsNone(oa.parse_action(b), b[:60])


class TestScrubOutput(unittest.TestCase):
    def test_paths_to_basename(self):
        s = oa.scrub_output("編集: /Users/someone/private/works/secret_plan.md を更新")
        self.assertNotIn("/Users/", s)
        self.assertIn("secret_plan.md", s)
        self.assertNotIn("private", s)
        self.assertNotIn("/Users", oa.scrub_output("cd ~/Downloads/works/顧客A && ls"))

    def test_secrets_masked(self):
        cases = [
            "export OPENAI_API_KEY=sk-proj-ABCDEFGHIJKLMNOP1234",
            "token: ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ012345",
            "aws AKIAIOSFODNN7EXAMPLE key",
            "Authorization: Bearer eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.abcdefgh",
            "sig=" + "a" * 64,
        ]
        for c in cases:
            out = oa.scrub_output(c)
            self.assertIn("[secret]", out, c)
            for leak in ("sk-proj-ABCDEFGHIJKLMNOP1234", "ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ012345",
                         "AKIAIOSFODNN7EXAMPLE", "a" * 64):
                self.assertNotIn(leak, out)

    def test_ansi_removed(self):
        self.assertEqual(oa.scrub_output("\x1b[32m✓ ok\x1b[0m"), "✓ ok")

    def test_utf8_boundary_safe(self):
        s = "あ" * 5000
        out = oa.scrub_output(s, limit=100)
        self.assertLessEqual(len(out.encode("utf-8")), 100 + 4)
        self.assertNotIn("�", out.rstrip("…"))   # 途中で切れた壊れ文字を作らない
        self.assertTrue(out.startswith("…"))

    def test_idempotent(self):
        s = "sk-proj-ABCDEFGHIJKLMNOP1234 と /Users/x/y/z.txt"
        once = oa.scrub_output(s)
        self.assertEqual(once, oa.scrub_output(once))   # 二重適用（生産者+公開ビュー）で壊れない

    def test_empty(self):
        self.assertEqual(oa.scrub_output(None), "")


class TestAdmit(unittest.TestCase):
    """同時実行ガードは純関数 admit() で固定（プロセスを起こさない＝速く決定論的）。"""

    def test_allows_when_idle(self):
        self.assertTrue(oa.admit("r_a", []))

    def test_same_recipe_blocked(self):
        self.assertFalse(oa.admit("r_a", [{"recipe": "r_a"}]))

    def test_other_recipe_allowed_up_to_limit(self):
        self.assertTrue(oa.admit("r_b", [{"recipe": "r_a"}]))
        self.assertFalse(oa.admit("r_c", [{"recipe": "r_a"}, {"recipe": "r_b"}]))

    def test_limit_is_max_concurrent(self):
        running = [{"recipe": f"r{i}"} for i in range(oa.MAX_CONCURRENT)]
        self.assertFalse(oa.admit("r_new", running))


class TestStartAction(unittest.TestCase):
    def setUp(self):
        oa._ACTIONS.clear()
        oa._ORDER.clear()
        oa.RESULTS_FILE.unlink(missing_ok=True)   # R92: 前のテストの結果を持ち越さない
        oa._FILE_CACHE.update(key=None, rows=[])
        os.environ["OFFICE_FAKE_CONFIRM"] = "ok"

    def tearDown(self):
        os.environ.pop("OFFICE_FAKE_CONFIRM", None)
        # 走らせっぱなしの子プロセスを残さない（残すと孤児sleepがCIの完了検出を遅らせる）
        oa.kill_running("test-teardown")

    def _wait(self, req_id, timeout=20):
        end = time.time() + timeout
        while time.time() < end:
            rec = oa._ACTIONS.get(req_id)
            if rec and rec["state"] != "running":
                return rec
            time.sleep(0.05)
        return oa._ACTIONS.get(req_id)

    def test_unknown_recipe_denied(self):
        state, rec = oa.start_action({"kind": "run", "recipe": "nope", "reqId": "req-unknown1"},
                                     [])
        self.assertEqual((state, rec["state"]), ("denied", "denied"))

    def test_run_done_and_output_none_hides_content(self):
        r = _valid(id="r_echo", argv=["/bin/echo", "SECRET-MARKER"], returnOutput="none")
        state, rec = oa.start_action({"kind": "run", "recipe": "r_echo", "reqId": "req-echo001"},
                                     [r])
        self.assertEqual(state, "running")
        done = self._wait("req-echo001")
        self.assertEqual((done["state"], done["exitCode"]), ("done", 0))
        self.assertGreater(done["bytes"], 0)         # バイト数は出す
        self.assertEqual(done["output"], "")         # 内容は1バイトも出さない（既定none）
        self.assertNotIn("SECRET-MARKER", json.dumps(oa.results_public(), ensure_ascii=False))

    def test_run_tail_returns_scrubbed_output(self):
        r = _valid(id="r_tail", argv=["/bin/echo", "done /Users/me/works/plan.md"],
                    returnOutput="tail")
        oa.start_action({"kind": "run", "recipe": "r_tail", "reqId": "req-tail001"}, [r])
        done = self._wait("req-tail001")
        self.assertEqual(done["state"], "done")
        self.assertIn("plan.md", done["output"])
        self.assertNotIn("/Users/", done["output"])

    def test_failed_exit_code(self):
        r = _valid(id="r_fail", argv=["/bin/sh", "-c", "exit 3"])
        oa.start_action({"kind": "run", "recipe": "r_fail", "reqId": "req-fail001"}, [r])
        done = self._wait("req-fail001")
        self.assertEqual((done["state"], done["exitCode"]), ("failed", 3))

    def test_timeout_kills_grandchild(self):
        """タイムアウトでプロセスグループごと死ぬ＝**孫まで**確実に落ちる
        （verify.sh が孫に wrangler dev を生むこのリポジトリでは必須の性質）。"""
        marker = Path(_TMP_HOME) / "grandchild.pid"
        script = (f"/bin/sh -c 'echo $$ > {marker}; sleep 60' & sleep 60")
        r = _valid(id="r_slow", argv=["/bin/sh", "-c", script], timeoutSec=1)
        oa.start_action({"kind": "run", "recipe": "r_slow", "reqId": "req-slow001"}, [r])
        done = self._wait("req-slow001", timeout=25)
        self.assertEqual(done["state"], "timeout")
        pid = int(marker.read_text().strip())
        time.sleep(0.5)
        alive = subprocess.run(["/bin/ps", "-p", str(pid)], capture_output=True)
        self.assertNotEqual(alive.returncode, 0, "孫プロセスが生き残っている（killpg不全）")

    def test_reqid_idempotent(self):
        r = _valid(id="r_idem", argv=["/bin/echo", "x"])
        s1, _ = oa.start_action({"kind": "run", "recipe": "r_idem", "reqId": "req-idem001"}, [r])
        self._wait("req-idem001")
        s2, rec2 = oa.start_action({"kind": "run", "recipe": "r_idem", "reqId": "req-idem001"}, [r])
        self.assertEqual(s1, "running")
        self.assertEqual(s2, rec2["state"])          # 再実行せず既存recordを返す
        self.assertEqual(len([r for r in oa._ORDER if r == "req-idem001"]), 1)

    def test_dangerous_requires_confirmation(self):
        os.environ["OFFICE_FAKE_CONFIRM"] = "cancel"
        r = _valid(id="r_danger", argv=["/bin/echo", "x"], dangerous=True)
        state, rec = oa.start_action({"kind": "run", "recipe": "r_danger", "reqId": "req-dang001"},
                                     [r])
        self.assertEqual((state, rec.get("reason")), ("denied", "not-confirmed"))

    def test_audit_appended_0600(self):
        r = _valid(id="r_audit", argv=["/bin/echo", "x"])
        oa.start_action({"kind": "run", "recipe": "r_audit", "reqId": "req-audit01"}, [r])
        self._wait("req-audit01")
        self.assertTrue(oa.AUDIT_FILE.exists())
        self.assertEqual(oa.AUDIT_FILE.stat().st_mode & 0o777, 0o600)
        lines = [json.loads(x) for x in oa.AUDIT_FILE.read_text().splitlines() if x.strip()]
        self.assertTrue(any(x.get("reqId") == "req-audit01" for x in lines))

    def test_recipes_public_hides_argv_cwd(self):
        pub = oa.recipes_public([_valid(id="r_pub", cwd="/Users/me/secret")])
        self.assertEqual(set(pub[0]), {"id", "label", "dangerous", "returnOutput"})
        self.assertNotIn("secret", json.dumps(pub, ensure_ascii=False))


class SharedResultsTest(unittest.TestCase):
    """R92: 実行結果が**別プロセス**へ渡るか。

    実行者は daemon、スマホへ配達する relay_agent は別プロセスで自分の office_json() を
    組み立てる。メモリのレジストリしか見ていなかったので、スマホの ▶実行は ⏳ のまま
    永久に結果が来なかった。ここでは「メモリを空にする＝別プロセス」で再現する。
    """

    def setUp(self):
        oa._ACTIONS.clear()
        oa._ORDER.clear()
        oa.RESULTS_FILE.parent.mkdir(parents=True, exist_ok=True)
        oa.RESULTS_FILE.unlink(missing_ok=True)
        oa._FILE_CACHE.update(key=None, rows=[])

    def tearDown(self):
        oa.RESULTS_FILE.unlink(missing_ok=True)
        oa._FILE_CACHE.update(key=None, rows=[])

    def _forget_memory(self):
        """relay_agent 側のプロセスを模す（レジストリは空・ファイルだけが手掛かり）。"""
        oa._ACTIONS.clear()
        oa._ORDER.clear()
        oa._FILE_CACHE.update(key=None, rows=[])

    def test_a_result_reaches_a_process_that_did_not_run_it(self):
        oa.register_result("req-cross01", "launch", "works", "done", exitCode=0)
        self.assertEqual(oa.RESULTS_FILE.stat().st_mode & 0o777, 0o600)
        self._forget_memory()
        pub = oa.results_public()
        self.assertEqual([r["reqId"] for r in pub], ["req-cross01"])
        self.assertEqual(pub[0]["state"], "done")

    def test_the_pending_spinner_also_crosses(self):
        """予約した時点で ⏳ が見えないと、スマホはタップの手応えを失う。"""
        oa.reserve_result("req-cross02", "run", "verify", recipe="r_x")
        self._forget_memory()
        self.assertEqual([r["state"] for r in oa.results_public()], ["running"])

    def test_the_process_that_ran_it_wins_over_a_stale_file_copy(self):
        record, _ = oa.reserve_result("req-cross03", "run", "verify", recipe="r_x")
        oa._FILE_CACHE.update(key=None, rows=[])
        stale = json.loads(oa.RESULTS_FILE.read_text())      # running のまま保存された版
        oa.finish_result(record, "done", exitCode=0)
        oa.RESULTS_FILE.write_text(json.dumps(stale), encoding="utf-8")
        oa._FILE_CACHE.update(key=None, rows=[])
        self.assertEqual([r["state"] for r in oa.results_public()], ["done"])

    def test_a_running_row_nobody_is_watching_is_dropped_not_invented(self):
        oa.reserve_result("req-cross04", "run", "verify", recipe="r_x")
        oa._FILE_CACHE.update(key=None, rows=[])
        rows = json.loads(oa.RESULTS_FILE.read_text())
        self._forget_memory()
        # timeoutSec の上限内ならまだ走っている可能性がある＝出す
        rows["results"][0]["startedAt"] = time.time() - 60
        oa.RESULTS_FILE.write_text(json.dumps(rows), encoding="utf-8")
        oa._FILE_CACHE.update(key=None, rows=[])
        self.assertEqual(len(oa.results_public()), 1)
        # 上限を超えたら「結果不明」＝done/failed をでっち上げずに落とす
        rows["results"][0]["startedAt"] = time.time() - oa.STALE_RUNNING_SEC - 1
        oa.RESULTS_FILE.write_text(json.dumps(rows), encoding="utf-8")
        oa._FILE_CACHE.update(key=None, rows=[])
        self.assertEqual(oa.results_public(), [])

    def test_a_hand_broken_file_cannot_take_down_the_office(self):
        for junk in ['{"results": "nope"}', '{"results": [1, 2, {"reqId": 3}]}',
                     '{"results": [{"reqId": "../../etc", "state": "done"}]}',
                     'not json at all', '[]', '{}']:
            oa.RESULTS_FILE.write_text(junk, encoding="utf-8")
            oa._FILE_CACHE.update(key=None, rows=[])
            self.assertEqual(oa.results_public(), [], junk)

    def test_infinity_and_nan_in_the_file_do_not_kill_the_api(self):
        """Astra レビュー指摘: JSON は Infinity/NaN/巨大整数を通し、int() で落ちる。"""
        for bad in ("1e309", "-1e309", "NaN", "1" + "0" * 400):
            oa.RESULTS_FILE.write_text(
                '{"results": [{"reqId": "req-inf00001", "state": "done",'
                f' "durationMs": {bad}, "bytes": {bad}, "startedAt": {bad}}}]}}',
                encoding="utf-8")
            oa._FILE_CACHE.update(key=None, rows=[])
            pub = oa.results_public()          # 例外を出さないこと自体が検査
            self.assertEqual([r["reqId"] for r in pub], ["req-inf00001"], bad)
            self.assertEqual((pub[0]["durationMs"], pub[0]["bytes"], pub[0]["startedAt"]),
                             (0, 0, 0), bad)

    def test_the_file_never_leaks_argv_cwd_or_env(self):
        oa.register_result("req-cross05", "run", "backup", "done",
                           recipe="r_x", argv=["/bin/rm", "-rf", "/Users/me/secret"],
                           cwd="/Users/me/secret")
        saved = json.loads(oa.RESULTS_FILE.read_text())
        self.assertNotIn("secret", json.dumps(saved, ensure_ascii=False))
        self.assertEqual(set(saved["results"][0]) - set(oa._PUBLIC_KEYS), set())

    def test_the_file_is_capped_like_the_registry(self):
        for i in range(oa.RESULT_KEEP + 6):
            oa.register_result(f"req-cap{i:04d}", "launch", "x", "done")
        saved = json.loads(oa.RESULTS_FILE.read_text())
        self.assertLessEqual(len(saved["results"]), oa.RESULT_KEEP)
        self._forget_memory()
        self.assertEqual(len(oa.results_public(limit=50)), oa.RESULT_KEEP)


if __name__ == "__main__":
    unittest.main()
