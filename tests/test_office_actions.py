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


if __name__ == "__main__":
    unittest.main()
