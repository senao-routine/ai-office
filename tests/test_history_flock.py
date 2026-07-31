# -*- coding: utf-8 -*-
"""P6: 送信履歴 _history.json のプロセス間 flock 化の回帰。
daemon/dev/relay_agent/mcp_office が同一 HISTORY_FILE を共有する状況を multiprocessing で再現し、
flock 無しなら起きる inter-process lost update（件数不足・torn read）が起きないことを固定する。"""
import importlib.util
import json
import multiprocessing as mp
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

CHILDREN = 4
POSTS_PER_CHILD = 10   # 4×10=40 < 50（保持上限）＝lost update ゼロなら丁度40件


def _worker(home, idx):
    # 各子は fresh import（別プロセス）＝実運用の daemon/relay_agent/mcp_office 相当
    import os
    os.environ["OFFICE_HOME"] = home
    os.environ["OFFICE_DATA"] = home
    os.environ["OFFICE_CONFIG"] = str(Path(home) / "office_config.json")
    spec = importlib.util.spec_from_file_location(f"office_w{idx}", ROOT / "server" / "office_server.py")
    o = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(o)
    for i in range(POSTS_PER_CHILD):
        ok, _ = o.post_instruction(f"child{idx}sess{i:02d}", f"msg {idx}-{i}")
        assert ok


class HistoryFlockTest(unittest.TestCase):
    def test_no_lost_update_across_processes(self):
        home = tempfile.mkdtemp(prefix="hflock_")
        (Path(home) / ".claude" / "office_inbox").mkdir(parents=True)
        (Path(home) / "office_config.json").write_text('{"projects": {}}\n', encoding="utf-8")
        ctx = mp.get_context("spawn")   # fresh interpreter＝共有モジュール状態を持ち越さない
        procs = [ctx.Process(target=_worker, args=(home, k)) for k in range(CHILDREN)]
        for p in procs:
            p.start()
        for p in procs:
            p.join(30)
            self.assertEqual(p.exitcode, 0)
        hist = Path(home) / ".claude" / "office_inbox" / "_history.json"
        data = json.loads(hist.read_text(encoding="utf-8"))   # torn read なら例外＝flock/原子rename担保
        self.assertEqual(len(data), CHILDREN * POSTS_PER_CHILD)  # 40件＝lost updateゼロ
        sessions = {d["session"] for d in data}
        self.assertEqual(len(sessions), CHILDREN * POSTS_PER_CHILD)  # 全件ユニーク（上書き無し）


if __name__ == "__main__":
    unittest.main()
