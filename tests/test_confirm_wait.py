# -*- coding: utf-8 -*-
"""R102-7: Mac の前での物理確認（_confirm_local）の待ち時間。

許可鍵の登録はスマホを操作している人が Mac の前へ移る時間が要る＝45 秒（ENROLL_CONFIRM_WAIT）。ほかの確認は 10 秒のまま。
2026-09-30 本人の実機: 10 秒のダイアログが押されずに閉じ（cancelled）、やり直すとリンクを使い切っていて bad-ticket で詰んだ。
"""
import importlib.util
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
os.environ.setdefault("OFFICE_HOME", tempfile.mkdtemp(prefix="office_home_"))
sys.path.insert(0, str(ROOT / "server"))
import office_actions  # noqa: E402
import office_approve  # noqa: E402

spec = importlib.util.spec_from_file_location("office_server_confirm_wait", ROOT / "server" / "office_server.py")
office = importlib.util.module_from_spec(spec)
spec.loader.exec_module(office)


class ConfirmWaitTest(unittest.TestCase):
    def setUp(self):
        env = mock.patch.dict(os.environ, {}, clear=False)
        env.start()
        self.addCleanup(env.stop)
        os.environ.pop("OFFICE_FAKE_CONFIRM", None)

    def run_dialog(self, **kw):
        calls = []

        def fake(args, **k):
            calls.append((args, k))
            return subprocess.CompletedProcess(args, 0, stdout="button returned:登録, gave up:false", stderr="")
        with mock.patch.object(office_actions.subprocess, "run", side_effect=fake):
            ok = office_actions._confirm_local("新しい端末を登録", "登録", **kw)
        return ok, calls[0][0][2], calls[0][1]["timeout"]

    def test_default_is_ten_seconds_and_enroll_waits_longer(self):
        ok, script, timeout = self.run_dialog()
        self.assertTrue(ok)
        self.assertIn("giving up after 10", script)
        self.assertEqual(timeout, 20)
        ok, script, timeout = self.run_dialog(wait=office_approve.ENROLL_CONFIRM_WAIT)
        self.assertIn("giving up after %d" % office_approve.ENROLL_CONFIRM_WAIT, script)
        self.assertEqual(timeout, office_approve.ENROLL_CONFIRM_WAIT + 10)
        for bad in (0, 3, 999, "45", True, None):
            self.assertIn("giving up after 10", self.run_dialog(wait=bad)[1], bad)

    def test_enroll_handler_never_asks_the_mac(self):
        """R103-1（本人裁定 2026-10-02「登録も ON もスマホだけで完結」）: 登録で Mac の確認ダイアログを出さない（confirm=None）。"""
        seen = {}

        def fake_enroll(env, *, devices, verify, rp, confirm, notify=None, now=None):
            seen["confirm"] = confirm
            return "granted", "", "abcdef"

        def fake_confirm(msg, ok, wait=10):
            seen["asked"] = True
            return False
        with mock.patch.object(office.office_approve, "enroll", side_effect=fake_enroll), \
                mock.patch.object(office.office_actions, "_confirm_local", side_effect=fake_confirm):
            office._approve_enroll({"envelope": {"session": "enr-" + "0" * 32}})
        self.assertIsNone(seen["confirm"])
        self.assertNotIn("asked", seen)


if __name__ == "__main__":
    unittest.main()
