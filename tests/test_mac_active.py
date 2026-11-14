# -*- coding: utf-8 -*-
"""R102-5: Mac の前に人がいるか（macActive）。いる間はスマホへ通知しない（Worker の sendAttnPushes）。

- ioreg の HIDIdleTime（ナノ秒）が MAC_ACTIVE_IDLE 秒未満なら 1・以上なら 0・読めなければ None（キーごと載せない）。
- 5 秒は同じ値を返す（ioreg を毎周呼ばない）。テストの差し替えは OFFICE_FAKE_MAC_IDLE（秒）。
- 中継へは 0/1 の整数だけ（bool・文字列・ほかの値は落とす）。
"""
import importlib.util
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
os.environ.setdefault("OFFICE_HOME", tempfile.mkdtemp(prefix="office_home_"))
sys.path.insert(0, str(ROOT / "server"))
spec = importlib.util.spec_from_file_location("office_server_mac_active", ROOT / "server" / "office_server.py")
office = importlib.util.module_from_spec(spec)
spec.loader.exec_module(office)
import relay_agent as ra  # noqa: E402

IOREG = '''+-o IOHIDSystem  <class IOHIDSystem, id 0x100000abc>
  | {
  |   "HIDIdleTime" = %d
  | }
'''


class MacActiveTest(unittest.TestCase):
    def setUp(self):
        office._MAC_ACTIVE.update(t=-1e9, v=None)
        self.env = patch.dict(os.environ, {}, clear=False)
        self.env.start()
        os.environ.pop("OFFICE_FAKE_MAC_IDLE", None)
        self.addCleanup(self.env.stop)

    def run_with(self, idle_ns=None, exc=None):
        def fake(*a, **k):
            if exc:
                raise exc
            return subprocess.CompletedProcess(a[0], 0, stdout=IOREG % idle_ns, stderr="")
        return patch.object(office.subprocess, "run", side_effect=fake)

    def test_idle_threshold(self):
        for idle_sec, want in ((0, 1), (119.9, 1), (120, 0), (8906, 0)):
            office._MAC_ACTIVE.update(t=-1e9, v=None)
            with self.subTest(idle=idle_sec), self.run_with(int(idle_sec * 1e9)):
                self.assertEqual(office.mac_active(1000.0), want)

    def test_cached_for_five_seconds_and_unreadable_is_none(self):
        with self.run_with(5 * 10 ** 9) as run:
            self.assertEqual(office.mac_active(1000.0), 1)
            self.assertEqual(office.mac_active(1004.9), 1)
            self.assertEqual(run.call_count, 1)
            office.mac_active(1005.0)
            self.assertEqual(run.call_count, 2)
        office._MAC_ACTIVE.update(t=-1e9, v=None)
        with self.run_with(exc=OSError("no ioreg")):
            self.assertIsNone(office.mac_active(2000.0))
        office._MAC_ACTIVE.update(t=-1e9, v=None)
        with patch.object(office.subprocess, "run",
                          return_value=subprocess.CompletedProcess([], 0, stdout="nothing", stderr="")):
            self.assertIsNone(office.mac_active(3000.0))

    def test_fake_env(self):
        os.environ["OFFICE_FAKE_MAC_IDLE"] = "30"
        self.assertEqual(office.mac_active(), 1)
        os.environ["OFFICE_FAKE_MAC_IDLE"] = "600"
        self.assertEqual(office.mac_active(), 0)
        os.environ["OFFICE_FAKE_MAC_IDLE"] = "x"
        self.assertIsNone(office.mac_active())

    def test_relay_carries_zero_or_one_only(self):
        for value, kept in ((1, 1), (0, 0), (True, None), ("1", None), (2, None), (None, None)):
            with self.subTest(value=value):
                out = ra._redact_office_for_relay({"macActive": value, "employees": []})
                self.assertEqual(out.get("macActive"), kept)
        self.assertNotIn("macActive", ra._redact_office_for_relay({"employees": []}))


if __name__ == "__main__":
    unittest.main()
