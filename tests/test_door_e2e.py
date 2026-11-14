# -*- coding: utf-8 -*-
"""R127 相談の往復を端から端まで（本物のプロセスだけ・一時 HOME・テスト用の空きポート）。契約の正本= docs/door.md。

  Bot（本物の `mcp_office.py --bot <id>` を子プロセスで起こす）が office_ask
  → Mac の画面と同じ HTTP（本物の daemon `office_server.py --port <空き>` の /api/office と /api/approval/reply）で答える
  → Bot が office_inbox で受け取る（選択肢は口が choice に解決）→ ack で消える。
  スマホの道: 中継の配達員（`relay_agent._process_items`）を別プロセスで動かし、ペアリングした端末の署名封筒（id8 つきの答え）を
  渡す → 同じ Bot が office_inbox で受け取る。

守る線（ここで端から端まで見るもの）: Bot への allow は 400（掲示板にも口にも何も起きない）・askId の違う答えは捨てて監査
（相談は開いたまま・Bot に何も届かない）・閉じた相談への 2 度目の答えも捨てて監査・答えた直後の /api/office に閉じた相談を出さない・
Bot の相談と答えは承認の掲示板（office_approvals/）を一切使わない・印の無い言葉は相談が開いている間は答えにならない。
本物の ~/.claude と本番のポート 4780 には触らない（HOME ごと一時フォルダ・ポートは空きを取る）。
"""
import importlib.util
import json
import os
import select
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SERVER = ROOT / "server"
BOT = "e2ebot"
SESSION = "bot-" + BOT
DOOR_TOOLS = ["office_checkin", "office_report", "office_ask", "office_inbox", "office_withdraw"]


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


door = _load("office_door_e2e_t", SERVER / "office_door.py")


def _free_port():
    s = socket.socket()
    try:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]
    finally:
        s.close()


class _Mcp:
    """本物の mcp_office.py --bot <id>（stdio JSON-RPC）。1 行送って 1 行受ける（答えが無ければ時間切れで赤）。"""

    def __init__(self, env):
        self.p = subprocess.Popen([sys.executable, str(SERVER / "mcp_office.py"), "--bot", BOT],
                                  stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env)
        self.mid = 0
        self.buf = b""

    def _readline(self, timeout):
        deadline = time.monotonic() + timeout
        fd = self.p.stdout.fileno()
        while b"\n" not in self.buf:
            left = deadline - time.monotonic()
            if left <= 0:
                raise TimeoutError("mcp_office.py --bot が答えない")
            ready, _, _ = select.select([fd], [], [], left)
            if ready:
                chunk = os.read(fd, 65536)
                if not chunk:
                    raise EOFError("mcp_office.py --bot が終わった: " + self.p.stderr.read().decode("utf-8", "replace")[-800:])
                self.buf += chunk
        line, _, self.buf = self.buf.partition(b"\n")
        return json.loads(line.decode("utf-8"))

    def call(self, method, params=None, timeout=30):
        self.mid += 1
        msg = {"jsonrpc": "2.0", "id": self.mid, "method": method}
        if params is not None:
            msg["params"] = params
        self.p.stdin.write(json.dumps(msg, ensure_ascii=False).encode("utf-8") + b"\n")
        self.p.stdin.flush()
        resp = self._readline(timeout)
        if resp.get("id") != self.mid:
            raise AssertionError(f"応答の id が違う: {resp}")
        return resp

    def tool(self, name, args=None):
        res = self.call("tools/call", {"name": name, "arguments": args or {}})["result"]
        return json.loads(res["content"][0]["text"]), res["isError"]

    def close(self):
        try:
            self.p.stdin.close()
        except OSError:
            pass
        try:
            self.p.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self.p.kill()
            self.p.wait()
        self.p.stdout.close()
        self.p.stderr.close()


# スマホの道の配達員（relay_agent）を別プロセスで 1 回動かす。端末をペアリングの台帳に置き、中継へ送る形（伏字の後）から
# Bot の印（id8）を読み、スマホと同じ本文（JSON.stringify＝区切りに空白なし）を署名して _process_items に渡す。
_RELAY_SCRIPT = r'''
import json, os, secrets, sys, time
sys.path.insert(0, sys.argv[1])
import relay_agent as ra
office = ra.office
session, plan = sys.argv[2], json.loads(sys.argv[3])
dev, sec = "d_" + "e" * 12, "5a" * 32
now = int(time.time())
d = office.load_devices()
d["devices"][dev] = {"secret": sec, "label": "e2e", "created": now, "expires": now + 3600, "revoked": False, "last_used": 0}
office.save_devices(d)
row = next(e for e in ra._redact_office_for_relay(office.office_json())["employees"] if e.get("session") == session)
id8 = (row.get("ask") or {}).get("id8") or ""
out = {"row": row, "results": []}
for i, (kind, text) in enumerate(plan):
    if kind == "answer":
        text = json.dumps({"aioffice": 1, "kind": "answer", "ask": id8, "text": text}, ensure_ascii=False, separators=(",", ":"))
    elif kind == "answer-other":
        other = "ffffffff" if id8 != "ffffffff" else "eeeeeeee"
        text = json.dumps({"aioffice": 1, "kind": "answer", "ask": other, "text": text}, ensure_ascii=False, separators=(",", ":"))
    env = office.sign_envelope(sec, dev, session, text, now, secrets.token_hex(16))
    item = {"id": i + 1, "session": session, "ts": now, "text": json.dumps(env, ensure_ascii=False)}
    out["results"].append(list(ra._process_items([item])))
print(json.dumps(out))
'''


# R128 #3: スマホが Bot の記録を開く道（封書 kind:"term"）。ペアリングした端末の署名封筒（dlg-<reqId>）を別プロセスの relay_agent に渡し、
# 本物の daemon の /api/dialog/sealed（OFFICE_PORT＝テスト用のポート）が口の記録を封じる → その端末の秘密で開いて行を返す。
_RELAY_TERM_SCRIPT = r'''
import json, os, secrets, sys, time
sys.path.insert(0, sys.argv[1])
import relay_agent as ra
office = ra.office
session = sys.argv[2]
dev, sec = "d_" + "f" * 12, "6b" * 32
now = int(time.time())
d = office.load_devices()
d["devices"][dev] = {"secret": sec, "label": "e2e-term", "created": now, "expires": now + 3600, "revoked": False, "last_used": 0}
office.save_devices(d)
out = []
for kind in ("term", "dialog"):
    rid = secrets.token_hex(16)
    act = json.dumps({"aioffice": 1, "kind": kind, "reqId": rid, "session": session, "after": None}, separators=(",", ":"))
    env = office.sign_envelope(sec, dev, "dlg-" + rid, act, now, secrets.token_hex(16))
    res = list(ra._process_items([{"id": 1, "session": "dlg-" + rid, "ts": now, "text": json.dumps(env)}]))
    bundle = office.dialog_seal.live_bundle(rid) or {}
    page = office.dialog_seal.unseal(sec, dev, session, bundle) if "b" in bundle else None
    out.append({"kind": kind, "res": res, "err": bundle.get("err"), "page": page})
print(json.dumps(out, ensure_ascii=False))
'''


class DoorRoundTripTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.home = Path(tempfile.mkdtemp(prefix="door_e2e_"))
        (cls.home / ".claude" / "office_inbox").mkdir(parents=True)
        (cls.home / "office_config.json").write_text('{"projects": {}}\n', encoding="utf-8")
        env = {k: v for k, v in os.environ.items() if not k.startswith(("OFFICE_", "RELAY_"))}
        env.update(HOME=str(cls.home), OFFICE_HOME=str(cls.home), OFFICE_DATA=str(cls.home),
                   OFFICE_CONFIG=str(cls.home / "office_config.json"), OFFICE_LANG="ja",
                   OFFICE_SOURCES_CODEX="0", OFFICE_AGENTS_CLI="0", OFFICE_TIMELINE="0", OFFICE_FAKE_MAC_IDLE="0",
                   OFFICE_OPENCLAW_FIXTURE=str(cls.home / "missing-openclaw.json"))
        cls.env = env
        res = door.add_bot(BOT, "見回り", "受信箱", home=cls.home)
        assert res["ok"], res
        cls.port = _free_port()
        cls.log = open(cls.home / "daemon.log", "w", encoding="utf-8")
        cls.proc = subprocess.Popen([sys.executable, str(SERVER / "office_server.py"), "--port", str(cls.port)],
                                    env=env, stdout=cls.log, stderr=subprocess.STDOUT)
        deadline = time.monotonic() + 60
        while True:
            try:
                if cls._get_office()[0] == 200:
                    break
            except OSError:
                pass
            if cls.proc.poll() is not None or time.monotonic() > deadline:
                cls._stop()
                raise RuntimeError("テスト用の daemon が起きない（daemon.log を見る）")
            time.sleep(0.2)

    @classmethod
    def _stop(cls):
        cls.proc.terminate()
        try:
            cls.proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            cls.proc.kill()
            cls.proc.wait()
        cls.log.close()

    @classmethod
    def tearDownClass(cls):
        cls._stop()
        shutil.rmtree(cls.home, ignore_errors=True)

    def setUp(self):
        self.mcp = _Mcp(self.env)
        self.addCleanup(self.mcp.close)
        init = self.mcp.call("initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                                            "clientInfo": {"name": "door-e2e", "version": "0"}})
        self.assertIn("tools", init["result"]["capabilities"])

    def tearDown(self):
        # 次のテストへ相談を持ち越さない（開いていれば取り下げ・未読は全部 ack）
        st = json.loads(door.state_path(BOT, self.home).read_text(encoding="utf-8")) \
            if door.state_path(BOT, self.home).exists() else {}
        if (st.get("ask") or {}).get("askId"):
            door.withdraw(BOT, st["ask"]["askId"], home=self.home)
        items = door.inbox(BOT, home=self.home)["items"]
        if items:
            door.inbox(BOT, [it["id"] for it in items], home=self.home)

    # ── HTTP（Mac の画面と同じ道）──
    @classmethod
    def _request(cls, method, path, body=None, origin=True):
        headers = {"X-Office-Local": "1"}
        if origin:
            headers["Origin"] = f"http://127.0.0.1:{cls.port}"
        data = None
        if body is not None:
            data = json.dumps(body, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(f"http://127.0.0.1:{cls.port}{path}", data=data, method=method, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return r.status, json.loads(r.read() or b"{}")
        except urllib.error.HTTPError as e:
            with e:
                raw = e.read()
            try:
                return e.code, json.loads(raw or b"{}")
            except ValueError:
                return e.code, {"raw": raw.decode("utf-8", "replace")}

    @classmethod
    def _get_office(cls):
        return cls._request("GET", "/api/office")

    def _bot_row(self):
        status, office = self._get_office()
        self.assertEqual(status, 200)
        rows = [e for e in office["employees"] if e.get("session") == SESSION]
        return rows[0] if rows else None

    def _wait_row(self, pred, what, timeout=20.0):
        """Bot は別プロセスで口のファイルを書く＝daemon は次の走査で知る（R115 の stale-while-revalidate は前の値を即返して
        裏で走査する）。Mac の画面と同じく、出るまで取り直す（出なければ赤）。"""
        deadline = time.monotonic() + timeout
        while True:
            row = self._bot_row()
            if row is not None and pred(row):
                return row
            if time.monotonic() > deadline:
                self.fail(f"/api/office に {what} が出ない: {row}")
            time.sleep(0.25)

    def reply(self, body, origin=True):
        return self._request("POST", "/api/approval/reply", dict({"session": SESSION}, **body), origin=origin)

    def _audit(self):
        p = door.audit_path(self.home)
        return [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines()] if p.exists() else []

    def _no_postings(self):
        apv = self.home / ".claude" / "office_approvals"
        self.assertEqual(sorted(p.name for p in apv.glob("bot-*")) if apv.exists() else [], [],
                         "Bot の相談・答えが承認の掲示板（office_approvals/）を使った")

    # ── Mac の画面から ──
    def test_mac_answers_the_question_and_the_bot_reads_it_once(self):
        tools = self.mcp.call("tools/list")["result"]["tools"]
        self.assertEqual([t["name"] for t in tools], DOOR_TOOLS)
        res, err = self.mcp.tool("office_checkin", {"doing": "受信箱を見回り中", "ttl_min": 30})
        self.assertFalse(err, res)
        res, err = self.mcp.tool("office_ask", {"question": "請求書 3 件を送ってよい？", "options": ["はい", "いいえ"]})
        self.assertFalse(err, res)
        aid = res["askId"]
        self.assertRegex(aid, r"^[0-9a-f]{32}$")

        row = self._wait_row(lambda r: (r.get("ask") or {}).get("askId") == aid, "Bot の相談")   # Mac の質問カードの材料
        self.assertEqual((row["state"], row["question"], row["ask"]["askId"], row["ask"]["kind"], row["ask"]["tool"]),
                         ("waiting", "請求書 3 件を送ってよい？", aid, "question", "office_ask"))
        self.assertEqual([o["label"] for o in row["questionOptions"]], ["はい", "いいえ"])

        # 守る線 1: Bot へ allow は出せない（400・相談は開いたまま・Bot に何も届かない）
        status, body = self.reply({"behavior": "allow", "message": "", "askId": aid})
        self.assertEqual(status, 400, body)
        self.assertIn("許可", body.get("error", ""))
        # 守る線 2: 権限を動かす POST は画面の Origin が要る（同じユーザーの素朴な呼び出しを止める）
        status, _ = self.reply({"behavior": "deny", "message": "はい", "askId": aid}, origin=False)
        self.assertEqual(status, 403)
        # 守る線 3: askId の違う答え（表示していない相談）は捨てて監査（指示にも答えにもならない）
        other = "f" * 32 if aid != "f" * 32 else "e" * 32
        status, body = self.reply({"behavior": "deny", "message": "はい", "askId": other})
        self.assertEqual(status, 400, body)
        res, _ = self.mcp.tool("office_inbox")
        self.assertEqual(res["items"], [], "allow／違う askId の答えが Bot に届いた")
        self.assertEqual(self._bot_row()["state"], "waiting", "allow／違う askId の答えで相談が閉じた")

        status, body = self.reply({"behavior": "deny", "message": "選択肢「はい」でお願いします。", "askId": aid})
        self.assertEqual((status, body), (200, {"ok": True, "kind": "question", "askId": aid,
                                                "choice": {"index": 0, "label": "はい"}}))
        row = self._bot_row()                                          # 答えた直後の画面に閉じた相談を出さない
        self.assertNotIn("ask", row)
        self.assertEqual((row["question"], row["state"]), ("", "working"))

        res, err = self.mcp.tool("office_inbox")
        self.assertFalse(err, res)
        (item,) = res["items"]
        self.assertEqual((item["kind"], item["askId"], item["text"], item["choice"]),
                         ("answer", aid, "選択肢「はい」でお願いします。", {"index": 0, "label": "はい"}))
        res, err = self.mcp.tool("office_inbox", {"ack": [item["id"]]})
        self.assertEqual((err, res["acked"], res["items"]), (False, 1, []))

        status, body = self.reply({"behavior": "deny", "message": "やっぱりいいえ", "askId": aid})   # もう閉じた
        self.assertEqual(status, 400, body)
        res, _ = self.mcp.tool("office_inbox")
        self.assertEqual(res["items"], [], "閉じた相談への 2 度目の答えが届いた")

        # 相談が閉じた後の Mac の言葉は指示（/api/instruct・R126 の道）＝同じ Bot が office_inbox で読む
        status, body = self._request("POST", "/api/instruct", {"session": SESSION, "text": "棚も見て"})
        self.assertEqual(status, 200, body)
        res, _ = self.mcp.tool("office_inbox")
        self.assertEqual([(i["kind"], i["text"]) for i in res["items"]], [("instruct", "棚も見て")])

        evs = [(a["ev"], a.get("op"), a.get("why"), a.get("src")) for a in self._audit() if a.get("bot") == BOT]
        for want in (("refuse", "answer", "allow", "local"), ("discard", None, "nomatch", "local"),
                     ("answer", None, None, "local"), ("ack", None, None, None), ("discard", None, "closed", "local"),
                     ("instruct", None, None, "local")):
            self.assertIn(want, evs)
        self.assertNotIn("請求書", door.audit_path(self.home).read_text(encoding="utf-8"), "監査に本文が入った")
        self._no_postings()

    # ── スマホから（中継の配達員の別プロセス）──
    def test_the_phone_answers_through_the_relay_agent_with_the_shown_id8(self):
        res, err = self.mcp.tool("office_ask", {"question": "どの順で送る？", "options": ["古い順", "新しい順"]})
        self.assertFalse(err, res)
        aid = res["askId"]
        plan = [["plain", "はい"],                                       # 印の無い言葉＝相談が開いている間は答えにならない
                ["answer-other", "古い順"],                              # 表示していない相談への答え＝捨てて監査
                ["answer", "Please go with the option: 新しい順"]]        # 表示していた相談（id8）への答え（スマホ en の型）
        r = subprocess.run([sys.executable, "-c", _RELAY_SCRIPT, str(SERVER), SESSION, json.dumps(plan)],
                           env=self.env, capture_output=True, text=True, encoding="utf-8", timeout=120)
        self.assertEqual(r.returncode, 0, r.stderr[-2000:])
        got = json.loads(r.stdout.strip().splitlines()[-1])
        row = got["row"]                                               # スマホに届く Bot の行（中継へ送る形）
        self.assertEqual((row["question"], row["ask"]["id8"], row["ask"]["kind"], row["ask"]["remote"]),
                         ("どの順で送る？", aid[:8], "question", 0))
        self.assertNotIn(aid, json.dumps(row, ensure_ascii=False), "askId 全体が中継へ送る形に載った")
        for k in ("cwd", "lastSaid", "lastOrder", "target", "branch", "feed"):
            self.assertNotIn(k, row)
        self.assertEqual(got["results"], [[0, [1]], [0, [2]], [1, [3]]], got["results"])   # どれも恒久＝ack・届いたのは id8 の答えだけ

        res, err = self.mcp.tool("office_inbox")
        self.assertFalse(err, res)
        (item,) = res["items"]
        self.assertEqual((item["kind"], item["askId"], item["choice"]), ("answer", aid, {"index": 1, "label": "新しい順"}))
        res, _ = self.mcp.tool("office_inbox", {"ack": [item["id"]]})
        self.assertEqual((res["acked"], res["items"]), (1, []))
        evs = [(a["ev"], a.get("op"), a.get("why"), a.get("src")) for a in self._audit() if a.get("bot") == BOT]
        for want in (("refuse", "instruct", "ask-open", None), ("discard", None, "nomatch", "relay"),
                     ("answer", None, None, "relay")):
            self.assertIn(want, evs)
        self._wait_row(lambda r: "ask" not in r, "相談が閉じた Bot の行")
        self._no_postings()

    # ── R128 #3: 記録の画面（Mac の GET /api/session/activity とスマホの封書 kind:"term" が、本物の daemon で同じ口の記録を返す）──
    def test_the_record_screen_returns_the_bots_log_through_the_real_daemon(self):
        poison = '<img src=x onerror="alert(1)">'
        secret = "sk-ant-api03-" + "A" * 40
        res, err = self.mcp.tool("office_checkin", {"doing": "記録の確認中" + poison, "ttl_min": 30})
        self.assertFalse(err, res)
        res, err = self.mcp.tool("office_report", {"title": "請求書を確認" + poison, "body": "token=" + secret, "kind": "info"})
        self.assertFalse(err, res)
        res, err = self.mcp.tool("office_ask", {"question": "記録の相談？", "options": ["はい", "いいえ"]})
        self.assertFalse(err, res)
        aid = res["askId"]
        status, _ = self.reply({"behavior": "deny", "message": "選択肢「いいえ」でお願いします。", "askId": aid})
        self.assertEqual(status, 200)
        status, page = self._request("GET", f"/api/session/activity?session={SESSION}")
        self.assertEqual(status, 200, page)
        tail = [(r["k"], r.get("e") or r.get("as") or r.get("rk") or "", r["t"]) for r in page["rows"][-4:]]
        self.assertEqual(tail, [("ev", "working", "記録の確認中" + poison), ("a", "info", "請求書を確認" + poison + "\n\ntoken=[secret]"),
                                ("q", "", "記録の相談？"), ("u", "answer", "選択肢「いいえ」でお願いします。")])
        self.assertEqual(page["rows"][-1]["ch"], 1)
        self.assertNotIn(secret, json.dumps(page))
        self.assertNotIn(aid, json.dumps(page))                                      # 相談の id は行に載せない
        # 差分カーソル: Mac の指示（/api/instruct）の後は 1 行だけ増える
        cur = "%d:%s" % (page["cur"]["off"], page["cur"]["h"])
        status, body = self._request("POST", "/api/instruct", {"session": SESSION, "text": "棚も見て"})
        self.assertEqual(status, 200, body)
        status, delta = self._request("GET", f"/api/session/activity?session={SESSION}&after={cur}")
        self.assertEqual((status, delta["reset"], [(r["k"], r.get("as"), r["t"]) for r in delta["rows"]]),
                         (200, False, [("u", "instruct", "棚も見て")]))
        # 読めない Bot は空の頁（200）・形の違う session は 400・CSRF の門は今までどおり
        status, empty = self._request("GET", "/api/session/activity?session=bot-nobody")
        self.assertEqual((status, empty["rows"]), (200, []))
        self.assertEqual(self._request("GET", "/api/session/activity?session=bot-..%2Fx")[0], 400)
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}/api/session/activity?session={SESSION}")
        with self.assertRaises(urllib.error.HTTPError) as cm:
            urllib.request.urlopen(req, timeout=30)
        self.assertEqual(cm.exception.code, 403)
        cm.exception.close()
        # 記録の本文は office_json（中継が伏字にする元）に載らない＝中継の平文に新しい鍵は無い
        status, office = self._get_office()
        self.assertNotIn("請求書を確認", json.dumps(office, ensure_ascii=False))
        # スマホ: 封書 kind:"term" → 同じ行（会話を見せるが ON の間）。dialog（会話ビュー）は Bot には開けない＝notfound
        cfg_path = self.home / "office_config.json"
        saved = cfg_path.read_text(encoding="utf-8")
        cfg_path.write_text(json.dumps({"projects": {}, "dialogRelay": True}), encoding="utf-8")
        try:
            env = dict(self.env, OFFICE_PORT=str(self.port))
            r = subprocess.run([sys.executable, "-c", _RELAY_TERM_SCRIPT, str(SERVER), SESSION], env=env,
                               capture_output=True, text=True, encoding="utf-8", timeout=120)
        finally:
            cfg_path.write_text(saved, encoding="utf-8")
        self.assertEqual(r.returncode, 0, r.stderr[-2000:])
        term, dialog = json.loads(r.stdout.strip().splitlines()[-1])
        self.assertIsNone(term["err"], term)
        self.assertEqual((term["page"]["kind"], term["page"]["session"]), ("term", SESSION))
        status, mac = self._request("GET", f"/api/session/activity?session={SESSION}")
        self.assertEqual(term["page"]["rows"], mac["rows"], "Mac の中央とスマホの端末画面が同じ記録を見ていない")
        self.assertEqual(dialog["err"], "notfound")
        self._no_postings()


if __name__ == "__main__":
    unittest.main()
