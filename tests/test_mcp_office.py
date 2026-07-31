# -*- coding: utf-8 -*-
"""MCPサーバー mcp_office.py のテスト（wrangler/実Claude Code不要・verify ▶4 で常時）。
Part A: subprocess で実際に stdio JSON-RPC を往復（純度・応答数==id数・配達・EOF終了）。
Part B: importlib で in-process ロードし session解決5分岐・status要約・import副作用なしを検証。"""
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

TESTS = Path(__file__).resolve().parent
ROOT = TESTS.parent
FX = TESTS / "fixtures"


def _make_home():
    """make_home.py 流儀の使い捨てHOME（working+waiting の2社員）。"""
    home = Path(tempfile.mkdtemp(prefix="mcp_home_"))
    proj = home / ".claude" / "projects" / "-Users-test-demo-project"
    proj.mkdir(parents=True)
    now = time.time()
    for name, fixture, age in [("sess-mcp00000001.jsonl", "working_tool.jsonl", 10),
                               ("sess-mcp00000002.jsonl", "waiting_said.jsonl", 600)]:
        p = proj / name
        shutil.copy(FX / fixture, p)
        os.utime(p, (now - age, now - age))
    (home / ".claude" / "office_inbox").mkdir(parents=True)
    (home / "office_config.json").write_text('{"projects": {}}\n', encoding="utf-8")
    return home


class McpSubprocessTest(unittest.TestCase):
    """Part A: 実プロトコル往復。"""

    def _spawn(self, requests, home=None, extra_env=None):
        home = home or _make_home()
        self.addCleanup(shutil.rmtree, home, ignore_errors=True)
        env = {k: v for k, v in os.environ.items() if not k.startswith("OFFICE_")}  # OFFICE_* 除染
        env.update(OFFICE_HOME=str(home), OFFICE_CONFIG=str(home / "office_config.json"),
                   OFFICE_DATA=str(home))   # 明示OFFICE_DATA で _adopt_p4_data を早期return（本番P4データ不読）
        if extra_env:
            env.update(extra_env)
        payload = b"".join(json.dumps(r).encode("utf-8") + b"\n" for r in requests)
        p = subprocess.Popen([sys.executable, str(ROOT / "server" / "mcp_office.py")],
                             stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE, env=env)
        try:
            out, err = p.communicate(payload, timeout=15)
        except subprocess.TimeoutExpired:
            p.kill(); p.communicate(); raise   # ハング時に子を放置しない
        lines = [l for l in out.decode("utf-8").splitlines() if l.strip()]
        resps = [json.loads(l) for l in lines]   # 純度: 全stdout行が正JSON（失敗ならここで例外）
        return p.returncode, resps, err.decode("utf-8"), home

    def test_full_handshake_and_delivery(self):
        home = _make_home()
        reqs = [
            {"jsonrpc": "2.0", "id": 1, "method": "initialize",
             "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                        "clientInfo": {"name": "t", "version": "0"}}},
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
            {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
             "params": {"name": "office_instruct",
                        "arguments": {"session": "sess-mcp00000001", "text": "配達テスト"}}},  # 在席セッションへ
            {"jsonrpc": "2.0", "id": 4, "method": "tools/call",
             "params": {"name": "office_status", "arguments": {}}},
            {"jsonrpc": "2.0", "id": 5, "method": "ping"},
        ]
        rc, resps, err, _ = self._spawn(reqs, home=home)
        self.assertEqual(rc, 0)                                   # EOF正常終了
        self.assertEqual(len(resps), 5)                           # id付き5件のみ（通知に応答混入なし）
        byid = {r["id"]: r for r in resps}
        self.assertIn(byid[1]["result"]["protocolVersion"], {"2024-11-05", "2025-03-26", "2025-06-18", "2025-11-25"})
        self.assertIn("tools", byid[1]["result"]["capabilities"])
        self.assertEqual(byid[1]["result"]["serverInfo"]["name"], "aioffice")
        tools = byid[2]["result"]["tools"]
        self.assertEqual(len(tools), 2)
        self.assertTrue(all(t["inputSchema"]["type"] == "object" for t in tools))
        self.assertFalse(byid[3]["result"]["isError"])           # 投函成功
        inbox = home / ".claude" / "office_inbox" / "sess-mcp00000001.json"
        self.assertTrue(inbox.exists())                          # ★配達確認の核
        self.assertIn("配達テスト", inbox.read_text(encoding="utf-8"))
        self.assertIn("session=sess-mcp00000001", byid[4]["result"]["content"][0]["text"])  # 完全ID
        self.assertEqual(byid[5]["result"], {})                   # ping

    def test_params_omitted(self):
        rc, resps, _e, _h = self._spawn([
            {"jsonrpc": "2.0", "id": 1, "method": "initialize"},   # params 無し
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"}])  # params 無し
        self.assertEqual(rc, 0)
        self.assertEqual(len(resps), 2)
        self.assertEqual(resps[0]["result"]["serverInfo"]["name"], "aioffice")
        self.assertEqual(len(resps[1]["result"]["tools"]), 2)

    def test_id_zero_gets_response(self):
        _rc, resps, _e, _h = self._spawn([{"jsonrpc": "2.0", "id": 0, "method": "ping"}])
        self.assertEqual(len(resps), 1)                           # id:0 に応答（truthiness バグ検出）
        self.assertEqual(resps[0]["id"], 0)

    def test_broken_json_line_survives(self):
        # 壊れ行 → -32700(id:null) → 後続の ping に正常応答（プロセス生存）
        home = _make_home()
        self.addCleanup(shutil.rmtree, home, ignore_errors=True)
        env = {k: v for k, v in os.environ.items() if not k.startswith("OFFICE_")}
        env.update(OFFICE_HOME=str(home), OFFICE_CONFIG=str(home / "office_config.json"), OFFICE_DATA=str(home))
        payload = b'{"broken json\n{"jsonrpc":"2.0","id":9,"method":"ping"}\n'
        p = subprocess.Popen([sys.executable, str(ROOT / "server" / "mcp_office.py")],
                             stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env)
        out, _err = p.communicate(payload, timeout=15)
        resps = [json.loads(l) for l in out.decode("utf-8").splitlines() if l.strip()]
        self.assertEqual(resps[0]["error"]["code"], -32700)
        self.assertIsNone(resps[0]["id"])
        self.assertEqual(resps[1]["id"], 9)                       # 後続は生存して応答

    def test_unknown_method_and_tool(self):
        _rc, resps, _e, _h = self._spawn([
            {"jsonrpc": "2.0", "id": 1, "method": "no/such"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "nope", "arguments": {}}}])
        self.assertEqual(resps[0]["error"]["code"], -32601)
        self.assertEqual(resps[1]["error"]["code"], -32602)

    def test_future_and_supported_version(self):
        _rc, resps, _e, _h = self._spawn([
            {"jsonrpc": "2.0", "id": 1, "method": "initialize",
             "params": {"protocolVersion": "9999-01-01", "capabilities": {}}},
            {"jsonrpc": "2.0", "id": 2, "method": "initialize",
             "params": {"protocolVersion": "2024-11-05", "capabilities": {}}}])
        self.assertEqual(resps[0]["result"]["protocolVersion"], "2025-11-25")   # 集合外→LATEST
        self.assertEqual(resps[1]["result"]["protocolVersion"], "2024-11-05")   # 集合内→鸚鵡返し

    def test_instruct_missing_args_is_error(self):
        _rc, resps, _e, _h = self._spawn([
            {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
             "params": {"name": "office_instruct", "arguments": {"session": "x"}}}])   # text欠落
        self.assertTrue(resps[0]["result"]["isError"])            # Protocol Errorでなく isError側

    def test_malformed_params_survive(self):
        # truthyな非dict params / 非hashable protocolVersion / 文字列params でプロセスが死なず継続
        rc, resps, _e, _h = self._spawn([
            {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": [1, 2]},
            {"jsonrpc": "2.0", "id": 2, "method": "initialize", "params": {"protocolVersion": ["x"]}},
            {"jsonrpc": "2.0", "id": 3, "method": "initialize", "params": "x"},
            {"jsonrpc": "2.0", "id": 4, "method": "ping"}])
        self.assertEqual(rc, 0)
        byid = {r["id"]: r for r in resps}
        self.assertEqual(byid[1]["error"]["code"], -32602)        # 配列params→-32602(死なない)
        self.assertEqual(byid[2]["result"]["protocolVersion"], "2025-11-25")  # 非hashable pv→LATEST
        self.assertEqual(byid[3]["error"]["code"], -32602)        # 文字列params→-32602
        self.assertEqual(byid[4]["result"], {})                   # 後続 ping 生存

    def test_batch_array(self):
        rc, resps, _e, _h = self._spawn([
            [{"jsonrpc": "2.0", "id": 1, "method": "ping"},
             {"jsonrpc": "2.0", "method": "notifications/initialized"},
             {"jsonrpc": "2.0", "id": 2, "method": "tools/list"}]])
        self.assertEqual(rc, 0)
        self.assertEqual(len(resps), 1)                           # バッチ応答は1行のJSON配列
        self.assertIsInstance(resps[0], list)
        self.assertEqual({r["id"] for r in resps[0]}, {1, 2})     # 通知への応答は混ざらない

    def test_response_shaped_and_non_dict_ignored_or_erred(self):
        rc, resps, _e, _h = self._spawn([
            {"jsonrpc": "2.0", "id": 99, "result": {"whatever": 1}},  # レスポンス形→黙殺
            "hello",                                                  # 非オブジェクト正JSON→-32600(id:null)
            {"jsonrpc": "2.0", "id": 7, "method": "ping"}])
        self.assertEqual(rc, 0)
        # レスポンス形は応答なし・"hello"は -32600・ping は応答 → 計2応答
        self.assertEqual(len(resps), 2)
        self.assertEqual(resps[0]["error"]["code"], -32600)
        self.assertIsNone(resps[0]["id"])
        self.assertEqual(resps[1]["id"], 7)

    def test_adopt_p4_data_reads_appsupport(self):
        # OFFICE_DATA/OFFICE_HOME 未設定＋HOME配下に P4 data → office_status がその config を読む
        home = Path(tempfile.mkdtemp(prefix="p4home_"))
        self.addCleanup(shutil.rmtree, home, ignore_errors=True)
        proj = home / ".claude" / "projects" / "-Users-test-demo-project"
        proj.mkdir(parents=True)
        p = proj / "sess-p4adopt0001.jsonl"
        shutil.copy(FX / "working_tool.jsonl", p)   # cwd=/Users/test/demo-project
        os.utime(p, (time.time() - 5, time.time() - 5))
        (home / ".claude" / "office_inbox").mkdir(parents=True)
        data = home / "Library" / "Application Support" / "AIOffice" / "data"
        (data / "assets").mkdir(parents=True)
        # cwd 断片に一致する project 設定で disp を固有名に（data/config を読んだ証拠になる）
        (data / "office_config.json").write_text(
            '{"projects": {"test/demo-project": {"name": "P4採用確認部"}}}', encoding="utf-8")
        env = {k: v for k, v in os.environ.items() if not k.startswith("OFFICE_")}
        env["HOME"] = str(home)   # _adopt_p4_data は Path.home()＝$HOME を見る
        payload = (json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                               "params": {"name": "office_status", "arguments": {}}}) + "\n").encode()
        pr = subprocess.Popen([sys.executable, str(ROOT / "server" / "mcp_office.py")],
                              stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env)
        try:
            out, _err = pr.communicate(payload, timeout=15)
        except subprocess.TimeoutExpired:
            pr.kill(); pr.communicate(); raise
        resp = json.loads(out.decode("utf-8").splitlines()[0])
        self.assertIn("P4採用確認部", resp["result"]["content"][0]["text"])   # data/config を読んだ証拠


# ---- Part B: in-process（office属性を差し替えてロジックを直接叩く） ----
class McpResolveTest(unittest.TestCase):
    def setUp(self):
        spec = importlib.util.spec_from_file_location("mcp_office_t", ROOT / "server" / "mcp_office.py")
        self.m = importlib.util.module_from_spec(spec)
        # import 副作用チェック用に事前の OFFICE_DATA を退避
        self._env_data = os.environ.get("OFFICE_DATA")
        spec.loader.exec_module(self.m)
        # ★office は office_server の共有モジュール実体。差し替えたら必ず tearDown で戻す
        # （戻さないと discover 実行順で test_relay_agent 等の post_instruction を汚染する）
        self._orig = {"office_json": self.m.office.office_json,
                      "post_instruction": self.m.office.post_instruction,
                      "PROJECTS": self.m.office.PROJECTS}
        self._emps = []
        self._posts = []
        self.m.office.office_json = lambda: {"employees": self._emps,
                                             "counts": {"working": 0, "waiting": 0, "resting": 0}}
        self.m.office.post_instruction = lambda s, t: (self._posts.append((s, t)) or (True, "投函しました"))

    def tearDown(self):
        self.m.office.office_json = self._orig["office_json"]
        self.m.office.post_instruction = self._orig["post_instruction"]
        self.m.office.PROJECTS = self._orig["PROJECTS"]

    def _emp(self, session, disp, **kw):
        e = {"session": session, "disp": disp, "dept": kw.get("dept", disp), "state": kw.get("state", "working"),
             "verb": kw.get("verb", ""), "target": kw.get("target", ""), "branch": "main", "cwd": "/x/y",
             "approvalMin": kw.get("approvalMin", 0), "question": kw.get("question", ""),
             "pending": kw.get("pending", False), "role": kw.get("role", "")}
        self._emps.append(e)
        return e

    def test_import_no_env_mutation(self):
        # importlib ロードだけで OFFICE_DATA が変化しないこと（_adopt_p4_data は main からのみ）
        self.assertEqual(os.environ.get("OFFICE_DATA"), self._env_data)

    def test_resolve_exact(self):
        self._emp("sess-abcdef01", "AI Office")
        sid, _n = self.m._resolve_session("sess-abcdef01")
        self.assertEqual(sid, "sess-abcdef01")

    def test_resolve_prefix_unique(self):
        self._emp("sess-abcdef01", "AI Office")
        sid, note = self.m._resolve_session("sess-abc")
        self.assertEqual(sid, "sess-abcdef01")
        self.assertIn("解決", note)

    def test_resolve_disp_match(self):
        self._emp("sess-abcdef01", "AIオフィス開発部")
        sid, _n = self.m._resolve_session("オフィス")
        self.assertEqual(sid, "sess-abcdef01")

    def test_resolve_ambiguous_lists_candidates(self):
        self._emp("sess-aaaaaaa1", "動画編集ソフト")
        self._emp("sess-aaaaaaa2", "動画編集ソフト 2号")
        text, is_err = self.m._tool_instruct({"session": "動画", "text": "x"})
        self.assertTrue(is_err)
        self.assertIn("sess-aaaaaaa1", text)
        self.assertIn("sess-aaaaaaa2", text)

    def test_resolve_empty_and_whitespace_rejected(self):
        self._emp("sess-solo0001", "唯一部")   # 出勤1人でも空sessionは誤配達しない
        for q in ("", "   ", "　 "):
            sid, note = self.m._resolve_session(q)
            self.assertIsNone(sid, q)
            self.assertEqual(note, "NOTFOUND")
        text, is_err = self.m._tool_instruct({"session": "", "text": "x"})
        self.assertTrue(is_err)
        self.assertEqual(self._posts, [])   # 一切投函されない

    def test_resolve_disp_exact_wins_over_ambiguous(self):
        self._emp("sess-dup00001", "動画編集ソフト")
        self._emp("sess-dup00002", "動画編集ソフト 2号")
        sid, note = self.m._resolve_session("動画編集ソフト")   # 完全一致は1件→AMBIGUOUSにしない
        self.assertEqual(sid, "sess-dup00001")

    def test_resolve_absent_id_needs_transcript(self):
        # 実トランスクリプトが在る正規形式IDのみ通す（typo/部署名で孤児inboxを作らせない）
        tmp = Path(tempfile.mkdtemp(prefix="proj_")); self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        self.m.office.PROJECTS = tmp
        (tmp / "-x").mkdir()
        (tmp / "-x" / "closed-session-42.jsonl").write_text("{}\n", encoding="utf-8")
        sid, note = self.m._resolve_session("closed-session-42")
        self.assertEqual(sid, "closed-session-42")
        self.assertIn("実セッション", note)
        # トランスクリプトの無い正規形式語(typo/部署名)は NOTFOUND
        self.assertEqual(self.m._resolve_session("frontend")[0], None)
        self.assertEqual(self.m._resolve_session("--------")[0], None)

    def test_resolve_garbage_rejected(self):
        text, is_err = self.m._tool_instruct({"session": "??", "text": "x"})
        self.assertTrue(is_err)
        self.assertIn("見つかりません", text)

    def test_post_instruction_failure_iserror(self):
        self._emp("sess-abcdef01", "AI Office")
        self.m.office.post_instruction = lambda s, t: (False, "指示が長すぎます")
        text, is_err = self.m._tool_instruct({"session": "sess-abcdef01", "text": "x"})
        self.assertTrue(is_err)
        self.assertIn("指示が長すぎます", text)

    def test_status_alert_and_truncation(self):
        self._emp("sess-approv01", "承認待ち部", approvalMin=12)
        self._emp("sess-questi01", "質問部", question="どっち？")
        for i in range(31):
            self._emp(f"sess-bulk{i:04d}xx", f"部署{i}")
        out = self.m._tool_status()
        self.assertIn("❗", out)
        self.assertIn("承認待ち12分", out)
        self.assertIn("❓質問あり", out)
        self.assertIn("他", out)   # 30人打切り注記


if __name__ == "__main__":
    unittest.main()
