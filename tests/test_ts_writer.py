# -*- coding: utf-8 -*-
"""daemonログの時刻前置（_TsWriter）と起動時ローテ（_rotate_daemon_log）の回帰。
print() が本文と改行を別 write で出しても、完全な行にだけ [YYYY-MM-DD HH:MM:SS] が
前置されること（改行だけの write にタイムスタンプが付かないこと）を固定する。"""
import importlib.util
import io
import os
import re
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

spec = importlib.util.spec_from_file_location("office_tsw", ROOT / "server" / "office_server.py")
office = importlib.util.module_from_spec(spec)
spec.loader.exec_module(office)

TS = r"\[\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\] "


class TestTsWriter(unittest.TestCase):
    def test_print_style_two_writes(self):
        """print() 相当（本文→"\n" の2回write）で1行1前置になる。"""
        raw = io.StringIO()
        w = office._TsWriter(raw)
        w.write("🏢 起動")
        w.write("\n")
        self.assertRegex(raw.getvalue(), f"^{TS}🏢 起動\n$")

    def test_multiline_single_write(self):
        raw = io.StringIO()
        w = office._TsWriter(raw)
        w.write("a\nb\n")
        lines = raw.getvalue().splitlines()
        self.assertEqual(len(lines), 2)
        for line in lines:
            self.assertRegex(line, f"^{TS}[ab]$")

    def test_partial_line_buffered_until_newline(self):
        """改行が来るまで出力しない（半行に前置して行を割らない）。"""
        raw = io.StringIO()
        w = office._TsWriter(raw)
        w.write("途中")
        self.assertEqual(raw.getvalue(), "")
        w.write("まで\n")
        self.assertRegex(raw.getvalue(), f"^{TS}途中まで\n$")

    def test_passthrough_attrs(self):
        """isatty 等の属性は元streamへ委譲（http.server 等が触っても壊れない）。"""
        raw = io.StringIO()
        w = office._TsWriter(raw)
        self.assertFalse(w.isatty())
        w.flush()  # 例外を出さない


class TestRotateDaemonLog(unittest.TestCase):
    def _run(self, size, limit=1024):
        td = tempfile.mkdtemp()
        self.addCleanup(lambda: __import__("shutil").rmtree(td, ignore_errors=True))
        data = Path(td) / "data"
        logs = Path(td) / "logs"
        logs.mkdir(parents=True)
        data.mkdir()
        p = logs / "office.daemon.log"
        p.write_bytes(b"x" * size)
        old_env = os.environ.get("OFFICE_DATA")
        os.environ["OFFICE_DATA"] = str(data)
        try:
            office._rotate_daemon_log("office.daemon.log", limit=limit)
        finally:
            if old_env is None:
                os.environ.pop("OFFICE_DATA", None)
            else:
                os.environ["OFFICE_DATA"] = old_env
        return p, p.with_name(p.name + ".old")

    def test_rotates_when_over_limit(self):
        p, old = self._run(2048, limit=1024)
        self.assertEqual(p.stat().st_size, 0)          # truncate（renameしない＝launchdのfd維持）
        self.assertEqual(old.stat().st_size, 2048)     # 全量が .old へ退避

    def test_keeps_small_log(self):
        p, old = self._run(100, limit=1024)
        self.assertEqual(p.stat().st_size, 100)
        self.assertFalse(old.exists())


if __name__ == "__main__":
    unittest.main()
