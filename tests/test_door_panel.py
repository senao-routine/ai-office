# -*- coding: utf-8 -*-
"""R128 #4 Bot パネル（⚙ の一覧・追加・直し・削除・消音・登録コマンド）の門。契約の正本= docs/door.md §10。

一時 HOME だけを使う（本物の ~/.claude・本番のポート 4780 に触らない）。見るもの:
  口（office_door）: 消音は台帳に残る（0600）・形の違う値は断って書かない・壊れた台帳は上書きしない・登録の直しで消音と登録時刻を保つ・
    行に muted（鳴らさないだけ＝行も相談も出る）・読み直し（再起動相当）でも効く・一覧の形（数と有無だけ）・登録コマンドの形
    （valid_id を通った id だけ＝シェルの特殊文字が入らない・パスは 1 語）・CLI の add に mcp。
  API（office_server の Handler をその場で回す）: 一覧・追加・直し・削除・消音・断りの code と口の文（不正な id・長すぎる名前・上限・
    壊れた台帳）・CSRF の無い POST・Origin の違う／無い POST・cross-site・loopback 以外は通らない・直した直後の /api/office に出る。
  届かない道: 中継（relay_agent）・MCP（mcp_office）は登録・削除・消音を呼ばない・台帳を書くのは add_bot／remove_bot／set_muted だけ
    （Bot の道具 5 つは台帳に触れない）・消音の印は中継の平文に載らない（Bot の表に無い＝スマホの Push は Worker のまま）。
  Mac の知らせ（office_server）: 消音の Bot の相談では新しい ❗・待たせすぎ・朝のまとめを鳴らさない／消音でない Bot とセッションは
    今までどおり鳴る／解いた瞬間に前からの相談で鳴らさない（次の新しい相談から）／_watch_once（daemon の 1 周）で端から端まで・
    daemon を作り直しても（読み直し）効く。
  画面の配線: 4 つの様式の apply が world に消音の集合を添える（buildWorld はスマホの束と共有＝そこには足さない）。
"""
import ast
import importlib.util
import io
import json
import os
import re
import shlex
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
SERVER = ROOT / "server"
HOUR = 3600.0


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


door = _load("office_door_panel_t", SERVER / "office_door.py")


def _env(home, **extra):
    """office_server を一時 HOME で読むための環境（本物の設定・Codex・agents CLI を読まない）。"""
    return {"OFFICE_HOME": str(home), "OFFICE_DATA": str(home), "OFFICE_CONFIG": str(home / "office_config.json"),
            "OFFICE_LANG": "ja", "OFFICE_SOURCES_CODEX": "0", "OFFICE_AGENTS_CLI": "0", **extra}


def _new_home(prefix):
    home = Path(tempfile.mkdtemp(prefix=prefix))
    (home / ".claude" / "office_inbox").mkdir(parents=True)
    (home / "office_config.json").write_text('{"projects": {}}\n', encoding="utf-8")
    return home


# ── 口（office_door）──────────────────────────────────────────
class DoorMuteTest(unittest.TestCase):
    def setUp(self):
        self.home = Path(tempfile.mkdtemp(prefix="door_panel_"))
        self.addCleanup(shutil.rmtree, self.home, ignore_errors=True)
        self.now = time.time()

    def add(self, bot_id="mailbox", name="郵便受け", **kw):
        r = door.add_bot(bot_id, name, home=self.home, now=self.now, **kw)
        self.assertTrue(r["ok"], r)
        return r

    def ledger(self):
        return json.loads(door.ledger_path(self.home).read_text(encoding="utf-8"))

    def audit(self):
        p = door.audit_path(self.home)
        return [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines()] if p.exists() else []

    def rows(self, at):
        return door.door_employees(self.home, at, show_window=3 * 3600)

    def test_mute_is_kept_in_the_ledger_and_read_back_after_a_restart(self):
        self.add()
        r = door.set_muted("mailbox", True, home=self.home, now=self.now)
        self.assertEqual((r["ok"], r["bot"], r["muted"], r["changed"]), (True, "mailbox", True, True))
        self.assertIs(self.ledger()["bots"]["mailbox"]["muted"], True)
        self.assertEqual(stat.S_IMODE(door.ledger_path(self.home).stat().st_mode), 0o600)
        before = door.ledger_path(self.home).read_bytes()
        again = door.set_muted("mailbox", True, home=self.home, now=self.now + 1)
        self.assertEqual((again["ok"], again["changed"]), (True, False))
        self.assertEqual(door.ledger_path(self.home).read_bytes(), before, "同じ値で台帳を書き直した")
        # 読み直し（daemon を作り直した相当）: 別に読み込んだ口も、台帳から消音を読む
        fresh = _load("office_door_panel_fresh", SERVER / "office_door.py")
        self.assertIs(fresh._read_ledger(self.home)[0]["mailbox"]["muted"], True)
        self.assertTrue(door.set_muted("mailbox", False, home=self.home, now=self.now + 2)["ok"])
        self.assertIs(self.ledger()["bots"]["mailbox"]["muted"], False)
        self.assertEqual([(a["ev"], a.get("on"), a["bot"]) for a in self.audit() if a["ev"] == "mute"],
                         [("mute", True, "mailbox"), ("mute", False, "mailbox")], "監査に消音の切替（ev=mute・on）が無い")
        self.assertNotIn("郵便受け", door.audit_path(self.home).read_text(encoding="utf-8"), "監査に名前を書いた")

    def test_mute_refuses_bad_values_and_ids_without_writing(self):
        self.add()
        before = door.ledger_path(self.home).read_bytes()
        for bad in ("true", 1, 0, None, [], {"on": True}):
            self.assertEqual(door.set_muted("mailbox", bad, home=self.home).get("error"), "bad-args", repr(bad))
        for bad_id in ("ABCD", "a; rm -rf ~", "bot-mail box", "", None, "abc", 1234):
            self.assertEqual(door.set_muted(bad_id, True, home=self.home).get("error"), "bad-args", repr(bad_id))
        self.assertEqual(door.set_muted("ghost1", True, home=self.home)["error"], "unregistered")
        self.assertEqual(door.ledger_path(self.home).read_bytes(), before, "断った消音で台帳が変わった")
        self.assertTrue([a for a in self.audit() if (a["ev"], a.get("op")) == ("refuse", "mute")], "断りが監査に無い")
        empty = Path(tempfile.mkdtemp(prefix="door_panel_empty_"))
        self.addCleanup(shutil.rmtree, empty, ignore_errors=True)
        self.assertEqual(door.set_muted("mailbox", True, home=empty)["error"], "unregistered")
        self.assertFalse(door.door_dir(empty).exists(), "口のフォルダが無い HOME に何かを作った")

    def test_a_broken_ledger_is_never_overwritten(self):
        self.add()
        door.ledger_path(self.home).write_text("{broken", encoding="utf-8")
        self.assertEqual(door.set_muted("mailbox", True, home=self.home)["error"], "ledger-broken")
        self.assertEqual(door.add_bot("other1", "x", home=self.home)["error"], "ledger-broken")
        self.assertEqual(door.remove_bot("mailbox", home=self.home)["error"], "ledger-broken")
        self.assertEqual(door.ledger_path(self.home).read_text(encoding="utf-8"), "{broken")
        self.assertEqual(door.bots_overview(home=self.home), {"ok": False, "max": door.BOTS_MAX, "bots": []})

    def test_updating_a_bot_keeps_its_mute_and_added_time(self):
        self.add()
        door.set_muted("mailbox", True, home=self.home)
        r = door.add_bot("mailbox", "受信箱の見回り", "受信", home=self.home, now=self.now + 60)
        self.assertTrue(r["ok"] and r["updated"])
        raw = self.ledger()["bots"]["mailbox"]
        self.assertEqual((raw["name"], raw["role"], raw["muted"]), ("受信箱の見回り", "受信", True), "直しで消音が外れた")
        self.assertAlmostEqual(raw["added"], self.now, places=3)

    def test_only_a_real_true_mutes(self):
        door._ensure_dirs(self.home)
        door.office_common.atomic_write_json(door.ledger_path(self.home), {"v": 1, "bots": {
            "aaaa": {"name": "a", "muted": True}, "bbbb": {"name": "b", "muted": "true"},
            "cccc": {"name": "c", "muted": 1}, "dddd": {"name": "d"}}}, 0o600)
        bots, ok = door._read_ledger(self.home)
        self.assertTrue(ok)
        self.assertEqual({k: v["muted"] for k, v in bots.items()},
                         {"aaaa": True, "bbbb": False, "cccc": False, "dddd": False}, "形の違う値を消音と読んだ（鳴らない側に倒した）")

    def test_a_muted_bot_still_shows_and_asks(self):
        """消音は鳴らさないだけ: 行・相談・あなたの番の材料（question・ask）はそのまま。"""
        self.add()
        door.checkin("mailbox", "見回り中", home=self.home, now=self.now)
        (row,), _ = self.rows(self.now + 5)
        self.assertIs(row["muted"], False)
        door.set_muted("mailbox", True, home=self.home)
        aid = door.ask("mailbox", "送ってよい？", ["はい", "いいえ"], home=self.home, now=self.now + 10)["askId"]
        (row,), meta = self.rows(self.now + 20)
        self.assertIs(row["muted"], True)
        self.assertEqual((row["state"], row["question"], row["ask"]["askId"], meta["asks"]),
                         ("waiting", "送ってよい？", aid, 1), "消音で行や相談を隠した")

    def test_overview_rows_are_counts_and_flags_only(self):
        self.add("mailbox", "郵便受け", role="受信箱")
        self.add("never1", "まだ")
        door.checkin("mailbox", "見回り中", home=self.home, now=self.now)
        door.instruct("mailbox", "棚を見て", src="local", home=self.home, now=self.now + 2)
        door.ask("mailbox", "どう？", home=self.home, now=self.now + 5)
        door.set_muted("mailbox", True, home=self.home)
        view = door.bots_overview(home=self.home, now=self.now + 60)
        self.assertEqual((view["ok"], view["max"]), (True, 64))
        by = {b["bot"]: b for b in view["bots"]}
        self.assertEqual(sorted(by), ["mailbox", "never1"])
        m = by["mailbox"]
        self.assertEqual((m["session"], m["name"], m["role"], m["ask"], m["unread"], m["muted"]),
                         ("bot-mailbox", "郵便受け", "受信箱", True, 1, True))
        self.assertAlmostEqual(m["lastSeen"], self.now + 5, places=3)
        n = by["never1"]
        self.assertEqual((n["lastSeen"], n["ask"], n["unread"], n["muted"]), (0.0, False, 0, False))
        self.assertEqual(door.list_bots(home=self.home, now=self.now + 60), view["bots"])
        text = json.dumps(view, ensure_ascii=False)
        self.assertNotIn("どう？", text, "一覧に相談の文を載せた")
        self.assertNotIn("棚を見て", text, "一覧に未読の中身を載せた")
        self.assertNotIn("見回り中", text, "一覧に doing を載せた")

    def test_register_command_shape_and_no_shell_metacharacters(self):
        cmd = door.mcp_command("mailbox", python="/opt/homebrew/bin/python3")
        argv = shlex.split(cmd)
        self.assertEqual(argv[:6], ["claude", "mcp", "add", "aioffice-door-mailbox", "--", "/opt/homebrew/bin/python3"])
        self.assertEqual(Path(argv[6]), SERVER.resolve() / "mcp_office.py")
        self.assertEqual(argv[7:], ["--bot", "mailbox"])
        self.assertNotIn("--scope", cmd, "全セッションへ配る形（--scope user）になった")
        spaced = door.mcp_command("mailbox", python="/Users/a b/it's/python3")
        self.assertEqual(shlex.split(spaced)[5], "/Users/a b/it's/python3", "スペース・引用符のあるパスが 1 語にならない")
        for bad in ("a; rm -rf ~", "abcd$(touch /tmp/x)", "abcd`id`", "ab cd", "ABCD", "abcd\n", "abcd|sh",
                    "../../x", "-abcd", "", None, 123, "a" * 33, "abcd'", 'abcd"'):
            self.assertIsNone(door.mcp_command(bad), repr(bad))
        for good in ("abcd", "a-b-", "0bot", "a" * 32):
            words = shlex.split(door.mcp_command(good, python="/usr/bin/python3"))
            self.assertRegex(" ".join(words[:5] + words[7:]), r"^[a-z0-9 -]+$", good)
            self.assertEqual(words[-1], good)

    def test_poisoned_ledger_keys_never_reach_the_panel(self):
        door._ensure_dirs(self.home)
        door.office_common.atomic_write_json(door.ledger_path(self.home), {"v": 1, "bots": {
            "good1": {"name": "ok"}, "x; rm -rf ~": {"name": "evil"}, "abcd$(id)": {"name": "evil"},
            "ABCD": {"name": "evil"}, "ab cd": {"name": "evil"}, "abcd\n": {"name": "evil"}}}, 0o600)
        self.assertEqual([b["bot"] for b in door.bots_overview(home=self.home)["bots"]], ["good1"])

    def test_cli_add_prints_the_register_command(self):
        env = {k: v for k, v in os.environ.items() if not k.startswith("OFFICE_")}
        env.update(OFFICE_HOME=str(self.home), OFFICE_LANG="ja")
        p = subprocess.run([sys.executable, str(SERVER / "office_door.py"), "add", "mailbox", "--name", "郵便受け"],
                           env=env, capture_output=True, text=True, timeout=30)
        self.assertEqual(p.returncode, 0, p.stderr)
        lines = p.stdout.splitlines()
        self.assertEqual(len(lines), 1)
        argv = shlex.split(json.loads(lines[0])["mcp"])
        self.assertEqual((argv[:5], argv[-2:]), (["claude", "mcp", "add", "aioffice-door-mailbox", "--"], ["--bot", "mailbox"]))


# ── API（Handler をその場で回す）──────────────────────────────
class PanelApiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.home = _new_home("door_panel_api_")
        cls.env = mock.patch.dict(os.environ, _env(cls.home))
        cls.env.start()
        cls.office = _load("office_server_panel_t", SERVER / "office_server.py")
        cls.door = cls.office.office_door

    @classmethod
    def tearDownClass(cls):
        cls.env.stop()
        shutil.rmtree(cls.home, ignore_errors=True)

    def setUp(self):
        shutil.rmtree(self.door.door_dir(self.home), ignore_errors=True)
        with self.office._lock:
            self.office._mark_written()

    def call(self, method, path, body=None, headers=None, client="127.0.0.1", drop=()):
        raw = b"" if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8")
        h = self.office.Handler.__new__(self.office.Handler)
        h.path = path
        hd = {"Host": "127.0.0.1:4797", "X-Office-Local": "1", "Origin": "http://127.0.0.1:4797",
              "Content-Length": str(len(raw)), **(headers or {})}
        for k in drop:
            hd.pop(k, None)
        h.headers = hd
        h.client_address = (client, 50000)
        h.rfile = io.BytesIO(raw)
        h._send = mock.Mock()
        (h.do_GET if method == "GET" else h.do_POST)()
        code, out, _ctype = h._send.call_args.args
        return code, json.loads(out)

    def ledger_bytes(self):
        p = self.door.ledger_path(self.home)
        return p.read_bytes() if p.exists() else None

    def test_list_add_update_mute_remove_round_trip(self):
        code, out = self.call("GET", "/api/door/bots")
        self.assertEqual((code, out["ok"], out["ledger"], out["max"], out["bots"]), (200, True, True, 64, []))
        code, out = self.call("POST", "/api/door/bot/add", {"bot": "mailbox", "name": "郵便受け", "role": "受信箱"})
        self.assertEqual(code, 200, out)
        self.assertEqual((out["bot"], out["session"], out["name"], out["role"], out["updated"]),
                         ("mailbox", "bot-mailbox", "郵便受け", "受信箱", False))
        self.assertEqual(out["mcp"], self.door.mcp_command("mailbox"))
        self.assertEqual(shlex.split(out["mcp"])[-2:], ["--bot", "mailbox"])
        code, out = self.call("POST", "/api/door/bot/add", {"bot": "mailbox", "name": "受信箱の見回り", "role": None})
        self.assertEqual((code, out["updated"], out["name"], out["role"]), (200, True, "受信箱の見回り", "Bot"))
        code, out = self.call("POST", "/api/door/bot/add", {"bot": "plain1"})
        self.assertEqual((code, out["name"], out["role"]), (200, "plain1", "Bot"), "名前と役を省くとサーバーの既定")
        code, out = self.call("GET", "/api/door/bots")
        rows = {b["bot"]: b for b in out["bots"]}
        self.assertEqual(sorted(rows), ["mailbox", "plain1"])
        m = rows["mailbox"]
        self.assertEqual((m["name"], m["role"], m["muted"], m["ask"], m["unread"], m["age"], m["lastSeen"]),
                         ("受信箱の見回り", "Bot", False, False, 0, None, 0.0))
        self.assertEqual(m["mcp"], self.door.mcp_command("mailbox"))
        # 消音 → 台帳と /api/office の行（直した直後に出る＝read-your-writes・行は隠さない）
        self.door.checkin("mailbox", "見回り中", home=self.home)
        with self.office._lock:
            self.office._mark_written()
        warm = self.office.office_json()                       # 2 秒の cache を温める（消音の前の値）
        self.assertIs(next(e for e in warm["employees"] if e["session"] == "bot-mailbox")["muted"], False)
        code, out = self.call("POST", "/api/door/bot/mute", {"bot": "mailbox", "on": True})
        self.assertEqual((code, out["ok"], out["muted"], out["changed"]), (200, True, True, True))
        row = next(e for e in self.office.office_json()["employees"] if e["session"] == "bot-mailbox")
        self.assertIs(row["muted"], True, "消音した直後の /api/office に前の値が出た（_mark_written）")
        self.assertEqual((row["verb"], row["state"]), ("見回り中", "working"), "消音で行が変わった（鳴らさないだけのはず）")
        code, out = self.call("GET", "/api/door/bots")
        m = {b["bot"]: b for b in out["bots"]}["mailbox"]
        self.assertIs(m["muted"], True)
        self.assertIsInstance(m["age"], int)
        # 削除 → 状態と記録も消える・一覧と /api/office から消える
        self.door.report("mailbox", "済み", home=self.home)
        self.assertTrue(self.door.log_path("mailbox", self.home).exists())
        code, out = self.call("POST", "/api/door/bot/remove", {"bot": "mailbox"})
        self.assertEqual((code, out["ok"], out["bot"]), (200, True, "mailbox"))
        self.assertFalse(self.door.state_path("mailbox", self.home).exists())
        self.assertFalse(self.door.log_path("mailbox", self.home).exists())
        self.assertEqual([b["bot"] for b in self.call("GET", "/api/door/bots")[1]["bots"]], ["plain1"])
        self.assertFalse(any(e["session"] == "bot-mailbox" for e in self.office.office_json()["employees"]))

    def test_refusals_return_the_code_and_the_door_text(self):
        cases = [({"bot": "ABCD"}, "bad-args", "id"), ({"bot": "a; rm -rf ~"}, "bad-args", "id"),
                 ({"bot": None}, "bad-args", "id"), ({}, "bad-args", "id"), ({"bot": ["mailbox"]}, "bad-args", "id"),
                 ({"bot": "mailbox", "name": "x" * 25}, "bad-args", "名前"),
                 ({"bot": "mailbox", "role": "y" * 17}, "bad-args", "役"),
                 ({"bot": "mailbox", "name": 123}, "bad-args", "名前"),
                 ({"bot": "mailbox", "name": " ‮​ "}, "bad-args", "名前")]     # 見えない文字だけ＝空
        for body, want, word in cases:
            code, out = self.call("POST", "/api/door/bot/add", body)
            self.assertEqual((code, out["ok"], out["code"]), (400, False, want), body)
            self.assertIn(word, out["error"], body)
        self.assertIsNone(self.ledger_bytes(), "断った追加で台帳を作った")
        for route, body in (("remove", {"bot": "ghost1"}), ("mute", {"bot": "ghost1", "on": True})):
            code, out = self.call("POST", f"/api/door/bot/{route}", body)
            self.assertEqual((code, out["code"]), (400, "unregistered"), route)
            self.assertIn("登録されていない", out["error"])
        self.assertEqual(self.call("POST", "/api/door/bot/add", {"bot": "mailbox"})[0], 200)
        for on in ("true", 1, None):
            code, out = self.call("POST", "/api/door/bot/mute", {"bot": "mailbox", "on": on})
            self.assertEqual((code, out["code"]), (400, "bad-args"), repr(on))
        for i in range(63):                                     # 上限（64 体）
            self.assertEqual(self.call("POST", "/api/door/bot/add", {"bot": f"bot{i:03d}"})[0], 200)
        code, out = self.call("POST", "/api/door/bot/add", {"bot": "onemore"})
        self.assertEqual((code, out["code"]), (400, "too-many"))
        self.assertIn("64", out["error"])
        self.assertEqual(self.call("POST", "/api/door/bot/add", {"bot": "mailbox", "name": "直し"})[0], 200,
                         "上限いっぱいでも直しは通る")
        self.door.ledger_path(self.home).write_text("{broken", encoding="utf-8")
        for route, body in (("add", {"bot": "newbot"}), ("remove", {"bot": "mailbox"}), ("mute", {"bot": "mailbox", "on": True})):
            code, out = self.call("POST", f"/api/door/bot/{route}", body)
            self.assertEqual((code, out["code"]), (400, "ledger-broken"), route)
        self.assertEqual(self.ledger_bytes(), b"{broken", "壊れた台帳を上書きした")
        code, out = self.call("GET", "/api/door/bots")
        self.assertEqual((code, out["ledger"], out["bots"]), (200, False, []))

    def test_only_the_person_at_the_mac_can_change_bots(self):
        self.assertEqual(self.call("POST", "/api/door/bot/add", {"bot": "mailbox"})[0], 200)
        before = self.ledger_bytes()
        bodies = {"/api/door/bot/add": {"bot": "intruder"}, "/api/door/bot/remove": {"bot": "mailbox"},
                  "/api/door/bot/mute": {"bot": "mailbox", "on": True}}
        variants = [({}, ("Origin",), "127.0.0.1", "Origin 無し（curl・relay_agent・Bot のプロセスの素朴な呼び出し）"),
                    ({"Origin": "https://evil.example"}, (), "127.0.0.1", "別のオリジン"),
                    ({"Origin": "http://127.0.0.1.evil.example"}, (), "127.0.0.1", "ループバックに似せたオリジン"),
                    ({"Sec-Fetch-Site": "cross-site"}, (), "127.0.0.1", "cross-site"),
                    ({"Sec-Fetch-Site": "same-site"}, (), "127.0.0.1", "same-site"),
                    ({}, ("X-Office-Local",), "127.0.0.1", "X-Office-Local 無し"),
                    ({"Host": "evil.example"}, (), "127.0.0.1", "ループバック以外の Host"),
                    ({}, (), "10.0.0.7", "ループバック以外の接続元")]
        for path, body in bodies.items():
            for headers, drop, client, why in variants:
                code, _ = self.call("POST", path, body, headers=headers, client=client, drop=drop)
                self.assertEqual(code, 403, f"{path}: {why}")
        self.assertEqual(self.ledger_bytes(), before, "断った POST で台帳が変わった")
        for path in ("/api/door/bot/addX", "/api/door/bot/mute/", "/api/door/bot/remove?x=1"):
            code, _ = self.call("POST", path, {"bot": "mailbox", "on": True}, drop=("Origin",))
            self.assertEqual(code, 403, f"前方一致の振り分けと同じ門: {path}")
        self.assertEqual(self.ledger_bytes(), before)
        for headers, drop, client, why in (({}, ("X-Office-Local",), "127.0.0.1", "X-Office-Local 無し"),
                                           ({"Origin": "https://evil.example"}, (), "127.0.0.1", "別のオリジン"),
                                           ({}, (), "10.0.0.7", "ループバック以外の接続元"),
                                           ({"Host": "evil.example"}, (), "127.0.0.1", "Host")):
            code, _ = self.call("GET", "/api/door/bots", headers=headers, client=client, drop=drop)
            self.assertEqual(code, 403, f"一覧（登録コマンドはこの Mac のパスを含む）: {why}")
        self.assertEqual(self.call("GET", "/api/door/bots", drop=("Origin",))[0], 200, "同じオリジンの GET（Origin なし）が通らない")
        code, out = self.call("POST", "/api/door/bot/mute", {"bot": "mailbox", "on": True}, headers={"Sec-Fetch-Site": "same-origin"})
        self.assertEqual(code, 200, out)
        self.assertEqual(set(self.office.DOOR_BOT_ROUTES), {"/api/door/bot/add", "/api/door/bot/remove", "/api/door/bot/mute"})
        self.assertTrue(set(self.office.DOOR_BOT_ROUTES) <= set(self.office._STRICT_POST), "Bot パネルの POST が _STRICT_POST に無い")

    def test_the_mute_mark_never_rides_the_relay(self):
        """消音の印は Mac の画面と知らせだけ＝中継の平文（Bot の表 _ALLOW_DOOR_ENTRY）に無い＝スマホの Push は Worker のまま。"""
        ra = _load("relay_agent_panel_t", SERVER / "relay_agent.py")
        self.assertNotIn("muted", ra._ALLOW_DOOR_ENTRY)
        self.door.add_bot("mailbox", "郵便受け", home=self.home)
        self.door.set_muted("mailbox", True, home=self.home)
        self.door.ask("mailbox", "送ってよい？", home=self.home)
        with self.office._lock:
            self.office._mark_written()
        snap = self.office.office_json()
        self.assertTrue(any(e.get("muted") is True for e in snap["employees"]))
        out = ra._redact_office_for_relay(snap)
        bots = [e for e in out["employees"] + out["roster"] if e.get("session") == "bot-mailbox"]
        self.assertEqual(len(bots), 2)
        for row in bots:
            self.assertNotIn("muted", row)
            self.assertEqual(row["question"], "送ってよい？", "消音で中継の行から相談を消した（隠すのではなく鳴らさないだけ）")


# ── 届かない道（中継・MCP・Bot の道具）──────────────────────────
class NoOtherPathTest(unittest.TestCase):
    WRITERS = {"add_bot", "remove_bot", "set_muted"}

    @staticmethod
    def calls(path):
        names = set()
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Call):
                f = node.func
                names.add(f.attr if isinstance(f, ast.Attribute) else getattr(f, "id", None))
        return names

    def test_relay_and_mcp_cannot_register_remove_or_mute(self):
        for name in ("relay_agent.py", "mcp_office.py", "ws_client.py", "source_door_log.py"):
            path = SERVER / name
            self.assertFalse(self.WRITERS & self.calls(path), f"{name} が登録・削除・消音を呼ぶ")
            self.assertNotIn("/api/door/", path.read_text(encoding="utf-8"), f"{name} が Bot パネルの API を呼ぶ")

    def test_only_the_three_person_operations_write_the_ledger(self):
        """台帳（登録と消音）を書くのは人の操作の 3 つだけ＝Bot の道具 5 つ・掃除・人からの言葉は台帳に触れない。"""
        tree = ast.parse((SERVER / "office_door.py").read_text(encoding="utf-8"))
        writers = {fn.name for fn in ast.walk(tree) if isinstance(fn, ast.FunctionDef)
                   and any(isinstance(n, ast.Call) and getattr(n.func, "id", None) == "_write_ledger" for n in ast.walk(fn))}
        self.assertEqual(writers, self.WRITERS)

    def test_bot_tools_stay_five(self):
        """Bot の MCP の道具は 5 つのまま（登録・削除・消音の道具は無い）。実行時の一覧は test_mcp_office・test_door_e2e が見る。"""
        tree = ast.parse((SERVER / "mcp_office.py").read_text(encoding="utf-8"))
        names = []
        for node in tree.body:
            if isinstance(node, ast.Assign) and any(getattr(t, "id", None) == "DOOR_TOOLS" for t in node.targets):
                for tool in node.value.elts:
                    for key, value in zip(tool.keys, tool.values):
                        if isinstance(key, ast.Constant) and key.value == "name":
                            names.append(value.value)
        self.assertEqual(names, ["office_checkin", "office_report", "office_ask", "office_inbox", "office_withdraw"])


# ── Mac の知らせ（新しい ❗・待たせすぎ・朝のまとめ・daemon の 1 周）──────────
class MuteNotifyTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.home = _new_home("door_panel_notify_")
        cls.notify = cls.home / "notify.txt"
        cls.env = mock.patch.dict(os.environ, _env(cls.home, OFFICE_FAKE_NOTIFY=str(cls.notify), OFFICE_NUDGES="1"))
        cls.env.start()
        cls.office = _load("office_server_panel_notify_t", SERVER / "office_server.py")
        cls.door = cls.office.office_door

    @classmethod
    def tearDownClass(cls):
        cls.env.stop()
        shutil.rmtree(cls.home, ignore_errors=True)

    def setUp(self):
        shutil.rmtree(self.door.door_dir(self.home), ignore_errors=True)
        self.notify.unlink(missing_ok=True)
        daily = mock.patch.object(self.office, "DAILY_DIR", self.home / "daily")
        daily.start()
        self.addCleanup(daily.stop)

    def bot(self, sid, ask=None, muted=None, **kw):
        row = {"projectId": f"p-{sid}", "session": sid, "vendor": "bot", "disp": sid, **kw}
        if ask:
            row.update(question=ask, ask={"tool": "office_ask", "kind": "question", "askId": "a" * 32, "min": 0})
        if muted is not None:
            row["muted"] = muted
        return row

    def test_quiet_set_is_muted_bot_rows_only(self):
        rows = [self.bot("bot-quiet1", muted=True), self.bot("bot-loud01", muted=False), self.bot("bot-str001", muted="true"),
                {"session": "sess-claude-1", "vendor": "claude", "muted": True}, {"session": "bot-X", "muted": True}, None, "x"]
        self.assertEqual(self.office._quiet_sessions(rows), frozenset({"bot-quiet1"}))
        self.assertEqual(self.office._quiet_sessions(None), frozenset())

    def test_attention_diff_skips_muted_bots_but_remembers_their_keys(self):
        o = self.office
        q = self.bot("bot-quiet1", ask="静か？")
        loud = self.bot("bot-loud01", ask="鳴る？")
        sess = {"projectId": "p-s", "session": "sess-claude-1", "vendor": "claude", "disp": "制作", "question": "どっち？"}
        quiet = frozenset({"bot-quiet1"})
        new, ids = o.attention_diff(set(), [q, loud, sess], quiet)
        self.assertEqual(sorted(n["question"] for n in new), ["どっち？", "鳴る？"], "消音の Bot で鳴った／ほかが鳴らない")
        self.assertEqual(len(ids), 3, "消音の Bot の鍵を控えない（解いた瞬間に古い相談で鳴る）")
        self.assertEqual(o.attention_diff(ids, [q, loud, sess], frozenset())[0], [], "解いた瞬間に前からの相談で鳴った")
        q2 = dict(q, question="次の相談", ask=dict(q["ask"], askId="b" * 32))
        self.assertEqual([n["question"] for n in o.attention_diff(ids, [q2, loud, sess], frozenset())[0]], ["次の相談"],
                         "解いた後の新しい相談で鳴らない")
        self.assertEqual(o.attention_diff(set(), [q, loud, sess])[0], o.attention_diff(set(), [q, loud, sess], frozenset())[0],
                         "quiet を渡さない呼び手（既存）の挙動が変わった")

    def test_secretary_nudges_skip_muted_bots_but_advance_their_stage(self):
        o = self.office
        quiet = frozenset({"bot-quiet1"})
        mem, out = o.secretary_nudges(None, [], quiet)                          # 最初の 1 回は覚えるだけ
        waiting = [dict(self.bot("bot-quiet1", ask="静か？"), age=16 * 60),
                   dict(self.bot("bot-loud01", ask="鳴る？"), age=16 * 60),
                   {"session": "sess-claude-1", "disp": "制作", "question": "どっち？", "age": 16 * 60}]
        for row in waiting[:2]:
            row["ask"]["min"] = 16
        mem, out = o.secretary_nudges(mem, waiting, quiet)
        self.assertEqual(sorted(t for t, _ in out), sorted(["⏳ bot-loud01 が 16分 待っています", "⏳ 制作 が 16分 待っています"]),
                         "消音の Bot の待たせすぎを鳴らした／ほかが鳴らない")
        self.assertEqual(mem["bot-quiet1"]["wait"], 15, "消音の間も段を進めない（解いた瞬間に過ぎた段で鳴る）")
        mem, out = o.secretary_nudges(mem, waiting, frozenset())
        self.assertEqual(out, [], "解いた瞬間に過ぎた段で鳴った")
        later = [dict(waiting[0], ask=dict(waiting[0]["ask"], min=61))]
        mem, out = o.secretary_nudges(mem, later, frozenset())
        self.assertEqual([t for t, _ in out], ["⏳ bot-quiet1 が 61分 待っています"], "解いた後の次の段で鳴らない")

    def test_morning_report_is_not_rung_by_a_muted_bot(self):
        o = self.office
        home = Path(tempfile.mkdtemp(prefix="door_panel_morning_"))
        self.addCleanup(shutil.rmtree, home, ignore_errors=True)
        for p in (mock.patch.object(o, "_HOME", home), mock.patch.object(o, "DAILY_DIR", home / "daily"),
                  mock.patch.dict(os.environ, {"OFFICE_TIMELINE": "1"}), mock.patch.object(o, "office_lang", return_value="ja")):
            p.start()
            self.addCleanup(p.stop)
        now = datetime(2026, 9, 7, 8, 30).timestamp()
        o.office_timeline.mark_seen(home, datetime(2026, 9, 6, 18).timestamp())
        db = o.office_timeline._connect(home / o.office_timeline.DB_FILE)
        self.addCleanup(db.close)
        row = {"projectId": "p-bot", "session": "bot-quiet1", "vendor": "bot", "disp": "郵便受け", "question": "送ってよい？",
               "ask": {"tool": "office_ask", "kind": "question", "ts": now - 600}}
        self.assertIsNone(o.build_morning_report({"roster": [row], "employees": [dict(row, muted=True)]}, now),
                          "消音の Bot の相談だけで朝のまとめが鳴った")
        report = o.build_morning_report({"roster": [row], "employees": [dict(row, muted=False)]}, now)
        self.assertIsNotNone(report, "消音でない Bot の相談で朝のまとめが鳴らない")
        self.assertIn("郵便受け", report[1])

    def notes(self):
        return self.notify.read_text(encoding="utf-8").splitlines() if self.notify.exists() else []

    def test_watch_once_end_to_end_with_the_real_door_files(self):
        """daemon の 1 周（_watch_once）を本物の口のファイルで: セッションと消音でない Bot は鳴る・消音の Bot は ❗ も待たせすぎも鳴らない・
        daemon を作り直しても（読み直し）効く・解いた瞬間に前からの相談で鳴らさない・解いた後の新しい相談で鳴る。"""
        o, d = self.office, self.door
        d.add_bot("loud01", "見回り係", home=self.home)
        d.add_bot("quiet1", "郵便受け", home=self.home)
        self.assertTrue(d.set_muted("quiet1", True, home=self.home)["ok"])
        sess_e = {"session": "sess-claude-1", "vendor": "claude", "dept": "制作", "disp": "制作", "state": "waiting",
                  "question": "どっち？", "age": 30}
        sess_r = {"projectId": "p-sess", "session": "sess-claude-1", "vendor": "claude", "disp": "制作", "name": "制作",
                  "question": "どっち？", "approvalMin": 0, "attention": True}

        def office_now(mod):
            data = mod.scan_office()
            return dict(data, employees=[*data["employees"], sess_e], roster=[*data["roster"], sess_r])

        noon = datetime(2026, 11, 14, 12, 0)                    # 朝のまとめ（8:30）と日報（18 時）の時間帯に入らない
        st = o._watch_state()
        with mock.patch.object(o, "office_json", lambda: office_now(o)):
            o._watch_once(st, now_local=noon)                   # 1 周目: セッションの ❗ だけ（待たせすぎは覚えるだけ）
            self.assertEqual(self.notes(), ["❗ 制作\tどっち？"])
            t0 = time.time()
            d.ask("quiet1", "静かな相談？", home=self.home, now=t0 - 20 * 60)
            d.ask("loud01", "鳴る相談？", home=self.home, now=t0 - 20 * 60)
            o._watch_once(st, now_local=noon)                   # 2 周目: 消音でない Bot の ❗ と待たせすぎだけ
        self.assertEqual(self.notes()[1:], ["❗ 見回り係\t鳴る相談？", "⏳ 見回り係 が 20分 待っています\t鳴る相談？"],
                         "消音の Bot で鳴った（ほか N 件に数えた）／消音でない Bot が鳴らない")
        self.assertNotIn("郵便受け", self.notify.read_text(encoding="utf-8"))
        # 読み直し（daemon を作り直した相当）: 新しいモジュールと新しい記憶でも、台帳の消音が効く
        fresh = _load("office_server_panel_restart_t", SERVER / "office_server.py")
        fst = fresh._watch_state()
        with mock.patch.object(fresh, "DAILY_DIR", self.home / "daily"), \
                mock.patch.object(fresh, "office_json", lambda: office_now(fresh)):
            fresh._watch_once(fst, now_local=noon)              # 作り直しの 1 周目: 今ある ❗ を全部知らせる（消音の Bot は除く）
        self.assertEqual(self.notes()[3:], ["❗ 見回り係 ほか1件\t鳴る相談？"],
                         "作り直した daemon が消音の Bot で鳴った（ほか 2 件）／鳴らない")
        # 解く: 前からの相談では鳴らさない → 取り下げて新しい相談（70 分前から）で ❗ と待たせすぎ（60 分の段）が鳴る
        self.assertTrue(d.set_muted("quiet1", False, home=self.home)["ok"])
        with mock.patch.object(o, "office_json", lambda: office_now(o)):
            o._watch_once(st, now_local=noon)
            self.assertEqual(self.notes()[4:], [], "解いた瞬間に前からの相談で鳴った")
            old = json.loads(d.state_path("quiet1", self.home).read_text(encoding="utf-8"))["ask"]["askId"]
            d.withdraw("quiet1", old, home=self.home)
            d.ask("quiet1", "次の相談？", home=self.home, now=time.time() - 70 * 60)
            o._watch_once(st, now_local=noon)
        self.assertEqual(self.notes()[4:], ["❗ 郵便受け\t次の相談？", "⏳ 郵便受け が 70分 待っています\t次の相談？"],
                         "解いた後の新しい相談で鳴らない")


# ── 画面の配線（4 つの様式・スマホの束）──────────────────────────
class ScreenWiringTest(unittest.TestCase):
    def test_every_mac_style_attaches_the_muted_set_right_after_building_the_world(self):
        for style in ("app", "cockpit", "iso", "pixel"):
            src = (ROOT / "ui" / style / "index.js").read_text(encoding="utf-8")
            self.assertRegex(src, r"built = buildWorld\((\w+)\);\s*\n\s*built\.muted = mutedSessions\(\1\);", style)

    def test_the_phone_bundle_is_untouched(self):
        """消音は Mac の音と通知だけ: スマホの束（ui/core の world・kind と ui/pwa）は muted を読まない（Push は Worker が決める・R130 で）。"""
        for rel in ("ui/core/world.js", "ui/core/kind.js", "ui/pwa/app.js", "relay/src/worker.js"):
            self.assertNotIn("muted", (ROOT / rel).read_text(encoding="utf-8"), rel)


if __name__ == "__main__":
    unittest.main()
