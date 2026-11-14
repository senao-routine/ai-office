# -*- coding: utf-8 -*-
"""relay_agent（中継エージェント）の単体テスト。wrangler不要な部分（設定解決・署名検証・
リプレイ防御・レート制限・at-least-once配達）をHTTPモックで検証し verify.sh ▶4 に乗せる。
実wranglerを使うE2Eは tests/relay_e2e.sh（別途・署名版）。

P3の核となる回帰: (1)nonceは配達成功後にのみコミット→一時障害でも再配達される・二重配達しない
(2)鮮度は署名済みtsで判定(DO列tsではない) (3)レート超過は延期でロスしない。"""
import importlib.util
import json
import contextlib
import io
import base64
import os
import secrets
import tempfile
import time
import unittest
from unittest import mock
import urllib.error
from pathlib import Path

TESTS = Path(__file__).resolve().parent
ROOT = TESTS.parent

spec = importlib.util.spec_from_file_location("relay_agent_t", ROOT / "server" / "relay_agent.py")
ra = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ra)

SEC = "11" * 32          # 64 hex = テスト用デバイス秘密
DID = "d_aaaaaaaaaaaa"


class RelayAgentTest(unittest.TestCase):
    def setUp(self):
        self._orig = {
            "_req": ra._req,
            "post_instruction": ra.office.post_instruction,
            "office_json": ra.office.office_json,
            "INBOX": ra.office.INBOX,
            "APPROVALS": ra.office.APPROVALS,
            "write_approval_reply": ra.office.write_approval_reply,
            "HISTORY_FILE": ra.office.HISTORY_FILE,
            "DEVICES_FILE": ra.office.DEVICES_FILE,
            "DEVICES_SEEN_FILE": ra.office.DEVICES_SEEN_FILE,
            "NONCE_FILE": ra.NONCE_FILE,
            "WINDOW": ra.WINDOW, "RATE_CAP": ra.RATE_CAP, "RATE_REFILL": ra.RATE_REFILL,
            "ALLOW_UNSIGNED": ra.ALLOW_UNSIGNED, "time_time": ra.time.time,
        }
        self.tmp = Path(tempfile.mkdtemp(prefix="relay_t_"))
        ra.office.INBOX = self.tmp / "office_inbox"
        # R99-P4-5: 承認フックの掲示／回答も一時 HOME へ（実 ~/.claude/office_approvals を読まない・書かない）。
        # 形は hooks/office-approval-wait.sh と同じ <OFFICE_HOME>/.claude/office_approvals。
        ra.office.APPROVALS = self.tmp / ".claude" / "office_approvals"
        ra.office.HISTORY_FILE = ra.office.INBOX / "_history.json"
        ra.office.DEVICES_FILE = self.tmp / "office_devices.json"
        ra.office.DEVICES_SEEN_FILE = self.tmp / "office_devices_seen.json"   # b4-mac: 実 HOME へ書かない
        ra.NONCE_FILE = self.tmp / "office_nonces.json"
        ra._NONCES.clear()
        ra._RATE.clear()
        ra._SEEN_AT.clear()

    def tearDown(self):
        ra._req = self._orig["_req"]
        ra.office.post_instruction = self._orig["post_instruction"]
        ra.office.office_json = self._orig["office_json"]
        ra.office.INBOX = self._orig["INBOX"]
        ra.office.APPROVALS = self._orig["APPROVALS"]
        ra.office.write_approval_reply = self._orig["write_approval_reply"]
        ra.office.HISTORY_FILE = self._orig["HISTORY_FILE"]
        ra.office.DEVICES_FILE = self._orig["DEVICES_FILE"]
        ra.office.DEVICES_SEEN_FILE = self._orig["DEVICES_SEEN_FILE"]
        ra.NONCE_FILE = self._orig["NONCE_FILE"]
        ra.WINDOW = self._orig["WINDOW"]
        ra.RATE_CAP = self._orig["RATE_CAP"]
        ra.RATE_REFILL = self._orig["RATE_REFILL"]
        ra.ALLOW_UNSIGNED = self._orig["ALLOW_UNSIGNED"]
        ra.time.time = self._orig["time_time"]
        ra._NONCES.clear()
        ra._RATE.clear()
        for k in ("RELAY_URL", "RELAY_TOKEN", "RELAY_INTERVAL", "OFFICE_DATA"):
            os.environ.pop(k, None)

    # ---- ヘルパ ----
    def _seed_device(self, device_id=DID, secret=SEC, expires=None, revoked=False):
        now = int(time.time())
        d = ra.office.load_devices()
        d["devices"][device_id] = {"secret": secret, "label": "t", "created": now,
                                   "expires": now + 86400 if expires is None else expires,
                                   "revoked": revoked, "last_used": 0}
        ra.office.save_devices(d)

    def _sign(self, device_id=DID, secret=SEC, session="relaytest-0001", text="やあ", ts=None, nonce=None):
        ts = int(time.time()) if ts is None else ts
        nonce = nonce if nonce is not None else secrets.token_hex(16)
        return ra.office.sign_envelope(secret, device_id, session, text, ts, nonce)

    def _mock(self, envelopes, plain=None, remove_on_ack=True):
        """署名封筒(dict)を items[{id,session,ts,text=json}] に包む _req モックを仕込む。
        plain=[{id?,session,text}] は無署名item。remove_on_ack=False で ack取りこぼしを模擬。"""
        items, nid = [], 1
        for e in envelopes:
            items.append({"id": nid, "session": e["session"], "ts": int(time.time()),
                          "text": json.dumps(e)})
            nid += 1
        for p in (plain or []):
            p = dict(p)
            p.setdefault("id", nid)
            nid = max(nid, p["id"]) + 1
            items.append(p)
        calls = {"ack": None, "status": None, "pulls": 0}

        def fake(method, url, token, body=None):
            if url.endswith("/pull"):
                calls["pulls"] += 1
                return {"ok": True, "items": list(items)}
            if url.endswith("/ack"):
                calls["ack"] = body
                if remove_on_ack:
                    ids = set(body.get("ids", []))
                    items[:] = [it for it in items if it.get("id") not in ids]
                return {"ok": True, "acked": len(body.get("ids", []))}
            if url.endswith("/status"):
                calls["status"] = body
                return {"ok": True}
            return {"ok": True}
        ra._req = fake
        return calls, items

    def _inbox(self, session):
        return ra.office.INBOX / f"{session}.json"

    # ---- b4-mac: 端末の「最後に使った時刻」 ----
    def test_verified_envelope_marks_device_last_used_once_a_minute(self):
        """署名が通った封筒だけが端末の last_used を進める（📱一覧の「最後に使用」）。秘密の台帳には書かない・
        60 秒に 1 回まで・署名の通らない封筒（別の秘密）では進めない・ファイルは 0600。"""
        self._seed_device()
        before = ra.office.DEVICES_FILE.read_text(encoding="utf-8")
        ra.office.post_instruction = lambda s, t, *, src: (True, "ok")
        self._mock([self._sign(text="一通目")])
        ra._process_items(ra._req("GET", "x/pull", "t")["items"])
        seen = json.loads(ra.office.DEVICES_SEEN_FILE.read_text(encoding="utf-8"))
        self.assertIn(DID, seen)
        first = seen[DID]
        self.assertLessEqual(abs(first - time.time()), 5)
        self.assertEqual(ra.office.DEVICES_SEEN_FILE.stat().st_mode & 0o777, 0o600)
        self.assertEqual(ra.office.DEVICES_FILE.read_text(encoding="utf-8"), before)   # 秘密の台帳は触らない
        listed = [d for d in ra.office.list_devices() if d["device_id"] == DID][0]
        self.assertEqual(listed["last_used"], first)
        # 偽の署名（別の秘密）は last_used を進めない
        ra._SEEN_AT.clear()
        ra.office.DEVICES_SEEN_FILE.unlink()
        self._mock([self._sign(secret="22" * 32, text="偽物")])
        ra._process_items(ra._req("GET", "x/pull", "t")["items"])
        self.assertFalse(ra.office.DEVICES_SEEN_FILE.exists())
        # 間引き: 60 秒以内の 2 通目は書かない
        ra._mark_seen(DID, 1000)
        ra._mark_seen(DID, 1030)
        self.assertEqual(json.loads(ra.office.DEVICES_SEEN_FILE.read_text(encoding="utf-8"))[DID], 1000)
        ra._mark_seen(DID, 1061)
        self.assertEqual(json.loads(ra.office.DEVICES_SEEN_FILE.read_text(encoding="utf-8"))[DID], 1061)

    def test_mark_device_used_rejects_bad_ids_and_caps_size(self):
        self.assertFalse(ra.office.mark_device_used("../../etc", 5))
        self.assertFalse(ra.office.mark_device_used(None, 5))
        cap = ra.office.DEVICE_SEEN_MAX
        for i in range(cap + 5):
            ra.office.mark_device_used("d_%012x" % i, 1000 + i)
        seen = ra.office.load_device_seen()
        self.assertEqual(len(seen), cap)
        self.assertNotIn("d_%012x" % 0, seen)                 # 古い順に捨てる
        self.assertIn("d_%012x" % (cap + 4), seen)
        ra.office.DEVICES_SEEN_FILE.write_text("[1, 2]", encoding="utf-8")
        self.assertEqual(ra.office.load_device_seen(), {})     # 壊れていても一覧は落ちない

    # ---- 設定解決（既存・不変） ----
    def test_load_config_env_priority_and_trailing_slash(self):
        os.environ["RELAY_URL"] = "http://x:8788/"
        os.environ["RELAY_TOKEN"] = "tok"
        os.environ["RELAY_INTERVAL"] = "9"
        url, token, interval = ra.load_config()
        self.assertEqual((url, token, interval), ("http://x:8788", "tok", 9.0))

    def test_load_config_default_interval(self):
        # R50提案4: 既定60秒（旧既定5秒がCF無料枠を食い潰した実測への恒久対策）
        os.environ["RELAY_URL"] = "http://x"
        os.environ["RELAY_TOKEN"] = "t"
        self.assertEqual(ra.load_config()[2], 60.0)

    def test_load_config_file_interval_floor(self):
        # configファイル由来の interval は 15 未満へ下げられない（floor）
        cfg = self.tmp / "office_relay.json"
        cfg.write_text(json.dumps({"url": "http://x", "token": "t", "interval": 5}),
                       encoding="utf-8")
        orig = ra.CONFIG
        ra.CONFIG = cfg
        try:
            self.assertEqual(ra.load_config()[2], 15.0)
        finally:
            ra.CONFIG = orig

    def test_load_config_env_interval_bypasses_floor(self):
        # env はテスト注入口＝floorを通さない（relay_e2e の高速回しを守る）
        os.environ["RELAY_URL"] = "http://x"
        os.environ["RELAY_TOKEN"] = "t"
        os.environ["RELAY_INTERVAL"] = "2"
        self.assertEqual(ra.load_config()[2], 2.0)

    def test_adopt_p4_data_respects_explicit_env(self):
        # 明示 OFFICE_DATA が最優先＝_adopt_p4_data はテスト注入を上書きしない（P4分岐防止の前提）
        os.environ["OFFICE_DATA"] = "/tmp/explicit-x"
        before_data = ra.office.DATA
        ra._adopt_p4_data()
        self.assertEqual(ra.office.DATA, before_data)

    # ---- 署名検証・配達 ----
    def test_valid_signature_delivered_and_acked(self):
        self._seed_device()
        calls, _ = self._mock([self._sign(text="やあ")])
        self.assertEqual(ra.pull_and_deliver("http://x", "t"), 1)
        self.assertTrue(self._inbox("relaytest-0001").exists())
        self.assertEqual(calls["ack"]["ids"], [1])

    def test_bad_items_filtered_but_acked(self):
        self._seed_device()
        good = self._sign(session="relaytest-0001", text="ok")
        unknown = self._sign(device_id="d_ffffffffffff", session="relaytest-0002", text="x")  # 未登録
        calls, _ = self._mock([good, unknown])
        self.assertEqual(ra.pull_and_deliver("http://x", "t"), 1)          # 配達できたのは1
        self.assertTrue(self._inbox("relaytest-0001").exists())
        self.assertFalse(self._inbox("relaytest-0002").exists())          # 未登録は配達しない
        self.assertEqual(sorted(calls["ack"]["ids"]), [1, 2])             # 恒久不正も ack して捨てる

    def test_tampered_text_rejected(self):
        self._seed_device()
        env = self._sign(text="正")
        env["text"] = "偽"                                                # 署名後に本文改竄
        calls, _ = self._mock([env])
        self.assertEqual(ra.pull_and_deliver("http://x", "t"), 0)
        self.assertFalse(self._inbox("relaytest-0001").exists())
        self.assertEqual(calls["ack"]["ids"], [1])                        # 改竄は恒久不正→ack破棄

    def test_expired_device_rejected(self):
        self._seed_device(expires=int(time.time()) - 10)
        calls, _ = self._mock([self._sign()])
        self.assertEqual(ra.pull_and_deliver("http://x", "t"), 0)
        self.assertEqual(calls["ack"]["ids"], [1])

    def test_revoked_device_rejected(self):
        self._seed_device(revoked=True)
        calls, _ = self._mock([self._sign()])
        self.assertEqual(ra.pull_and_deliver("http://x", "t"), 0)
        self.assertEqual(calls["ack"]["ids"], [1])

    def test_freshness_uses_signed_ts_not_do_column(self):
        # DO列 ts は新鮮(_mock が int(time.time()) を入れる)でも、署名済み env.ts が古ければ拒否
        self._seed_device()
        stale = self._sign(ts=int(time.time()) - 10_000)
        calls, _ = self._mock([stale])
        self.assertEqual(ra.pull_and_deliver("http://x", "t"), 0)
        self.assertEqual(calls["ack"]["ids"], [1])

    def test_same_session_aggregated_not_lost(self):
        self._seed_device()
        e1 = self._sign(session="relaytest-0001", text="指示アルファ")
        e2 = self._sign(session="relaytest-0001", text="指示ベータ")
        calls, _ = self._mock([e1, e2])
        self.assertEqual(ra.pull_and_deliver("http://x", "t"), 1)          # 同一sessionは1通に集約
        body = self._inbox("relaytest-0001").read_text(encoding="utf-8")
        self.assertIn("指示アルファ", body)
        self.assertIn("指示ベータ", body)
        self.assertEqual(sorted(calls["ack"]["ids"]), [1, 2])

    def test_aggregation_over_limit_splits_across_ticks_no_loss(self):
        # 同一sessionの正当な大きめ指示2件（各≤4000）が結合で4000字超→単一スロット上書きで
        # 両方ロストする退行[review#2]の回帰。先頭chunkだけ配達し残りは次tickで配達＝ロストしない。
        self._seed_device()
        big = "あ" * 2500
        e1 = self._sign(session="relaytest-0001", text=big + "1", nonce="1111" * 8)
        e2 = self._sign(session="relaytest-0001", text=big + "2", nonce="2222" * 8)
        calls, _ = self._mock([e1, e2], remove_on_ack=True)
        self.assertEqual(ra.pull_and_deliver("http://x", "t"), 1)   # tick1: 先頭1件だけ配達
        self.assertEqual(calls["ack"]["ids"], [1])                  # 配達したchunk分だけ ack
        self.assertIn(big + "1", self._inbox("relaytest-0001").read_text(encoding="utf-8"))
        self.assertEqual(ra.pull_and_deliver("http://x", "t"), 1)   # tick2: 残り1件を配達（ロストなし）
        self.assertEqual(calls["ack"]["ids"], [2])
        self.assertIn(big + "2", self._inbox("relaytest-0001").read_text(encoding="utf-8"))

    def test_replay_same_nonce_rejected(self):
        self._seed_device()
        env = self._sign(nonce="abcd" * 8)
        self._mock([env])
        self.assertEqual(ra.pull_and_deliver("http://x", "t"), 1)          # 初回=配達
        # 同じ nonce の封筒を再投函（別item id）→ リプレイで drop
        env2 = self._sign(nonce="abcd" * 8, text="再送")
        calls, _ = self._mock([env2])
        self.assertEqual(ra.pull_and_deliver("http://x", "t"), 0)
        self.assertEqual(calls["ack"]["ids"], [1])

    def test_delivery_failure_then_retry_redelivers_then_replay_dropped(self):
        """★核: 一時OSError→残置→再配達(ロスなし)。配達後のリプレイは二重配達しない。"""
        self._seed_device()
        env = self._sign(nonce="dead" * 8, text="重要指示")
        calls, _ = self._mock([env], remove_on_ack=False)                 # ack取りこぼしを模擬

        def boom(session, text, *, src):
            raise OSError("disk full")
        ra.office.post_instruction = boom
        self.assertEqual(ra.pull_and_deliver("http://x", "t"), 0)          # tick1: 障害→n=0
        self.assertIsNone(calls["ack"])                                   # ackせず残置
        self.assertNotIn(f"{DID}:{'dead'*8}", ra._NONCES)                 # nonce未コミット

        ra.office.post_instruction = self._orig["post_instruction"]       # 復旧
        self.assertEqual(ra.pull_and_deliver("http://x", "t"), 1)          # tick2: 再配達成功
        self.assertTrue(self._inbox("relaytest-0001").exists())
        self.assertIn(f"{DID}:{'dead'*8}", ra._NONCES)                    # 配達後にnonceコミット

        self.assertEqual(ra.pull_and_deliver("http://x", "t"), 0)          # tick3: 同一item=リプレイ→drop
        self.assertEqual(calls["ack"]["ids"], [1])                        # 破棄のため ack はする（二重配達しない）

    def test_nonce_persist_survives_restart(self):
        self._seed_device()
        env = self._sign(nonce="beef" * 8)
        self._mock([env])
        self.assertEqual(ra.pull_and_deliver("http://x", "t"), 1)          # 配達→NONCE_FILE書出し
        self.assertTrue(ra.NONCE_FILE.exists())
        ra._NONCES.clear()                                                # 再起動を模擬
        ra._load_nonces()                                                 # ファイルから復元
        env2 = self._sign(nonce="beef" * 8, text="再送")
        calls, _ = self._mock([env2])
        self.assertEqual(ra.pull_and_deliver("http://x", "t"), 0)          # 復元済nonceでリプレイ拒否
        self.assertEqual(calls["ack"]["ids"], [1])

    def test_nonce_file_is_0600_from_creation(self):
        """R100-0: nonce 台帳は最初から 0600（作ってから chmod だとその間は umask 次第で読める）。"""
        from unittest.mock import patch
        ra.NONCE_FILE.unlink(missing_ok=True)
        ra._NONCES["dev:" + "ab" * 16] = time.time()
        old = os.umask(0o022)
        try:
            with patch.object(ra.os, "chmod", lambda *a, **k: None):
                ra._save_nonces()
        finally:
            os.umask(old)
        self.assertEqual(ra.NONCE_FILE.stat().st_mode & 0o777, 0o600)

    def test_rate_limit_defers_not_drops(self):
        self._seed_device()
        ra.RATE_CAP, ra.RATE_REFILL = 2, 60.0            # 上限2・毎分60補充(=1/秒)
        clock = {"t": 1_000_000}
        ra.time.time = lambda: clock["t"]
        # 同一デバイスから3セッションへ（distinct nonce）。上限2なので1件は延期される
        envs = [self._sign(session=f"ratetest-000{i}", text=f"m{i}") for i in range(1, 4)]
        calls, _ = self._mock(envs, remove_on_ack=True)   # ack済みは items から除去→残りが次pullで来る
        self.assertEqual(ra.pull_and_deliver("http://x", "t"), 2)          # 2件配達・1件は延期
        self.assertEqual(len(calls["ack"]["ids"]), 2)                     # 延期分は ack されない=キュー残置
        clock["t"] += 5                                                   # 5秒経過→トークン補充
        self.assertEqual(ra.pull_and_deliver("http://x", "t"), 1)          # 延期分が次tickで配達=ロスなし
        self.assertTrue(self._inbox("ratetest-0003").exists())

    def test_unsigned_rejected_by_default(self):
        calls, _ = self._mock([], plain=[{"session": "relaytest-0009", "text": "無署名"}])
        self.assertEqual(ra.pull_and_deliver("http://x", "t"), 0)
        self.assertFalse(self._inbox("relaytest-0009").exists())
        self.assertEqual(calls["ack"]["ids"], [1])                        # ゴミ扱いでack破棄

    def test_unsigned_allowed_with_flag(self):
        ra.ALLOW_UNSIGNED = True
        calls, _ = self._mock([], plain=[{"session": "relaytest-0009", "text": "無署名"}])
        self.assertEqual(ra.pull_and_deliver("http://x", "t"), 1)          # フラグ時のみ素通し
        self.assertTrue(self._inbox("relaytest-0009").exists())

    def test_empty_pull_no_ack(self):
        calls, _ = self._mock([])
        self.assertEqual(ra.pull_and_deliver("http://x", "t"), 0)
        self.assertIsNone(calls["ack"])

    # ---- R99-P4-5: スマホから ❗ に答える分岐（relay_agent._process_items の pending_approval） ----
    # 相手が承認フックで止まっている（掲示がある）ときは、指示ポスト（受信箱）ではなく**フックへの回答**
    # として渡す。ここから allow は出せない（掟 7）: 回答は必ず behavior=deny・src=relay で、言葉は
    # message に載る。R86-H で入ったこの分岐には単体テストも E2E も無かった（Phase 4 の棚卸し）。
    ASK = "ab" * 16          # R100-0: 掲示と回答は <session>.<askId>.json（1 件ごと）

    def _post_ask(self, session="relaytest-0001", kind="question", tool="AskUserQuestion",
                  title="どちらで進めますか?", options=("A案", "B案"), ask_id=ASK, ts=None):
        """承認フック（hooks/office-approval-wait.sh）が出す掲示と同じ形。mtime＝心拍は今。
        ask_id=None は移行窓の旧 hook の掲示（<session>.json・askId 無し）。"""
        d = ra.office.APPROVALS
        d.mkdir(parents=True, exist_ok=True)
        now = time.time()
        # 既定は 10 秒前に出た掲示（スマホに出るのは ASK_GRACE＝3 秒を過ぎてから＝送った人が見ていた掲示）
        rec = {"session": session, "tool": tool, "kind": kind, "title": title,
               "options": list(options), "cwd": "/tmp", "ts": ts or now - 10,
               "deadline": now + 600, "pid": 1}
        if ask_id:
            rec["askId"] = ask_id
        p = d / (f"{session}.{ask_id}.json" if ask_id else f"{session}.json")
        p.write_text(json.dumps(rec, ensure_ascii=False), encoding="utf-8")
        return p

    def _reply(self, session="relaytest-0001", ask_id=ASK):
        p = ra.office.APPROVALS / (f"{session}.{ask_id}.reply.json" if ask_id else f"{session}.reply.json")
        return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None

    def test_r100_answer_goes_to_the_oldest_request_by_ask_id(self):
        """R100-0: 同じセッションで 2 件並んでいたら、スマホの答えは最古の 1 件へ askId 付きで渡る（後の 1 件は待ったまま）。"""
        self._seed_device()
        old, new = "0a" * 16, "0b" * 16
        self._post_ask(ask_id=new, ts=time.time() - 1)
        self._post_ask(ask_id=old, ts=time.time() - 9)
        calls, _ = self._mock([self._sign(text="A案で")])
        self.assertEqual(ra.pull_and_deliver("http://x", "t"), 1)
        rep = self._reply(ask_id=old)
        self.assertIsNotNone(rep, "最古の 1 件へ回答が渡らない")
        self.assertEqual((rep["askId"], rep["src"], rep["behavior"]), (old, "relay", "deny"))
        self.assertIsNone(self._reply(ask_id=new), "後の 1 件にも回答が置かれた")
        self.assertIsNone(self._reply(ask_id=None), "旧名に回答を置いた")

    def test_r102_answer_goes_only_to_the_request_the_phone_showed(self):
        """R102-11: スマホは表示していた掲示の askId の先頭 8 桁を答えに添える＝最古ではなく**その 1 件**へ答える。"""
        self._seed_device()
        old, new = "0a" * 16, "0b" * 16
        self._post_ask(ask_id=old, ts=time.time() - 30)
        self._post_ask(ask_id=new, ts=time.time() - 20)
        text = json.dumps({"aioffice": 1, "kind": "answer", "ask": new[:8], "text": "B案で"},
                          ensure_ascii=False, separators=(",", ":"))
        calls, _ = self._mock([self._sign(text=text)])
        self.assertEqual(ra.pull_and_deliver("http://x", "t"), 1)
        rep = self._reply(ask_id=new)
        self.assertIsNotNone(rep, "表示していた 1 件へ回答が渡らない")
        self.assertEqual((rep["askId"], rep["src"], rep["behavior"], rep["message"]), (new, "relay", "deny", "B案で"))
        self.assertIsNone(self._reply(ask_id=old), "見ていない最古の 1 件に答えた")
        self.assertFalse(self._inbox("relaytest-0001").exists())
        self.assertEqual(calls["ack"]["ids"], [1])

    def test_r102_answer_to_a_finished_request_becomes_an_instruction(self):
        """R102-11: 表示していた要求がもう無い（Mac で答えた・別の要求に替わった）＝別の掲示へは答えず、言葉を指示として届ける。"""
        self._seed_device()
        other = "0c" * 16
        self._post_ask(ask_id=other)
        text = json.dumps({"aioffice": 1, "kind": "answer", "ask": "deadbeef", "text": "いったん止めて"},
                          ensure_ascii=False, separators=(",", ":"))
        calls, _ = self._mock([self._sign(text=text)])
        self.assertEqual(ra.pull_and_deliver("http://x", "t"), 1)
        self.assertIsNone(self._reply(ask_id=other), "見ていない別の要求に答えた")
        inbox = json.loads(self._inbox("relaytest-0001").read_text(encoding="utf-8"))
        self.assertIn("いったん止めて", json.dumps(inbox, ensure_ascii=False))
        self.assertNotIn("aioffice", json.dumps(inbox, ensure_ascii=False), "答えの封筒の JSON がそのまま指示に入った")
        self.assertEqual(calls["ack"]["ids"], [1])

    def test_r102_plain_words_do_not_answer_a_request_posted_after_they_were_sent(self):
        """R102-11: 印の無い言葉（自由入力）は、送った後に出た掲示へ答えない（作業中に送った指示が、届いた瞬間に
        出た許可の拒否理由になっていた）。ASK_GRACE より若い掲示はスマホに出ていない＝見ていない。"""
        self._seed_device()
        sent = int(time.time()) - 5
        self._post_ask(kind="permission", tool="Bash", title="rm -rf build", options=(), ts=sent + 1)
        calls, _ = self._mock([self._sign(text="テストも足して", ts=sent)])
        self.assertEqual(ra.pull_and_deliver("http://x", "t"), 1)
        self.assertIsNone(self._reply(), "送った後に出た許可へ答えた（読んでいない操作を拒否した）")
        self.assertTrue(self._inbox("relaytest-0001").exists(), "言葉が指示として届かない")
        self.assertEqual(calls["ack"]["ids"], [1])

    def test_r102_answer_envelope_shape_is_strict(self):
        for bad in ('{"aioffice":1,"kind":"answer","ask":"XYZ","text":"a"}',
                    '{"aioffice":1,"kind":"answer","ask":"0123456","text":"a"}',
                    '{"aioffice":1,"kind":"answer","ask":"01234567","text":""}',
                    '{"aioffice":1,"kind":"answer","ask":"01234567"}',
                    '{"aioffice":2,"kind":"answer","ask":"01234567","text":"a"}', "ふつうの言葉"):
            self.assertIsNone(ra._answer_of(bad), bad)
        self.assertEqual(ra._answer_of('{"aioffice":1,"kind":"answer","ask":"01234567","text":"a"}'), ("01234567", "a"))

    def test_r100_legacy_posting_gets_a_legacy_reply(self):
        """移行窓: 配備の瞬間に待っていた旧 hook（<session>.json）には旧名で渡す（そうしないと永久に届かない）。"""
        self._seed_device()
        self._post_ask(ask_id=None)
        self._mock([self._sign(text="B案で")])
        self.assertEqual(ra.pull_and_deliver("http://x", "t"), 1)
        rep = self._reply(ask_id=None)
        self.assertIsNotNone(rep)
        self.assertNotIn("askId", rep)
        self.assertEqual((rep["src"], rep["behavior"]), ("relay", "deny"))

    def test_r86h_question_answer_goes_to_hook_verbatim_as_relay_deny(self):
        """AskUserQuestion の選択肢への回答は**そのまま**の文言で、フックへ deny+message として渡る。"""
        self._seed_device()
        self._post_ask()
        text = "選択肢「B案」でお願いします。"          # PWA の選択肢ボタンが送る文言と同じ形
        env = self._sign(text=text, nonce="a5a5" * 8)
        calls, _ = self._mock([env])
        self.assertEqual(ra.pull_and_deliver("http://x", "t"), 1)
        rep = self._reply()
        self.assertIsNotNone(rep, "掲示があるのにフックへの回答が置かれない（受信箱へ回ると止まっている間は届かない）")
        self.assertEqual(rep["session"], "relaytest-0001")
        self.assertEqual(rep["src"], "relay", "中継経由の回答が src=relay でない（フックの allow 判定の前提が崩れる）")
        self.assertEqual(rep["behavior"], "deny", "中継経由の回答が deny でない（掟 7）")
        self.assertEqual(rep["message"], text, "質問への回答に但し書きが付いた／文言が変わった（選択肢の判定を壊す）")
        self.assertFalse(self._inbox("relaytest-0001").exists(), "回答が受信箱にも二重に置かれた")
        self.assertEqual(calls["ack"]["ids"], [1])
        self.assertIn(f"{DID}:{'a5a5' * 8}", ra._NONCES)             # 配達したので nonce はコミット

    def test_r86h_permission_yes_from_phone_never_becomes_allow(self):
        """許可を求める ❗ に「はい」と答えても allow にならない（掟 7）。事実の但し書きが付く。"""
        self._seed_device()
        self._post_ask(kind="permission", tool="Bash", title="git push origin main", options=())
        text = "はい、承認します。実行してください"
        calls, _ = self._mock([self._sign(text=text)])
        self.assertEqual(ra.pull_and_deliver("http://x", "t"), 1)
        rep = self._reply()
        self.assertIsNotNone(rep)
        self.assertEqual(rep["behavior"], "deny", "スマホの『はい』が allow に化けた（掟 7）")
        self.assertEqual(rep["src"], "relay")
        self.assertTrue(rep["message"].startswith(text), rep["message"])
        self.assertIn("実行されていません", rep["message"], "許可系なのに『実行されていない』事実を添えていない")
        self.assertFalse(self._inbox("relaytest-0001").exists())
        self.assertEqual(calls["ack"]["ids"], [1])

    def test_r86h_plan_approval_answer_says_not_approved(self):
        """計画の承認（ExitPlanMode）はフックが question として掲示するが、スマホの答えは deny＝差し戻しにしかならない。
        「OK、進めて」が却下理由としてだけ届かないよう、承認されていない事実を 1 行添える（掟 7 と揃える）。"""
        self._seed_device()
        self._post_ask(kind="question", tool="ExitPlanMode", title="プランの承認", options=())
        text = "OK、進めて"
        calls, _ = self._mock([self._sign(text=text)])
        self.assertEqual(ra.pull_and_deliver("http://x", "t"), 1)
        rep = self._reply()
        self.assertIsNotNone(rep)
        self.assertEqual(rep["behavior"], "deny", "スマホの『OK、進めて』が計画の承認に化けた（掟 7）")
        self.assertEqual(rep["src"], "relay")
        self.assertTrue(rep["message"].startswith(text), rep["message"])
        self.assertIn("計画は承認されていません", rep["message"],
                      "計画の承認への回答に『承認されていない』事実が無い（モデルには却下理由としてだけ届く）")
        self.assertIn("承認は Mac のターミナルで", rep["message"])
        self.assertFalse(self._inbox("relaytest-0001").exists())
        self.assertEqual(calls["ack"]["ids"], [1])

    def test_r86h_answer_text_annotates_every_non_allowable_approval(self):
        """中継から通せない承認（許可・計画の承認）には事実を添え、ただの質問は文言そのまま（選択肢の判定を壊さない）。"""
        q = ra._answer_text({"kind": "question", "tool": "AskUserQuestion"}, " 選択肢「B案」でお願いします。 ")
        self.assertEqual(q, "選択肢「B案」でお願いします。")
        plan = ra._answer_text({"kind": "question", "tool": "ExitPlanMode"}, " OK、進めて ")
        self.assertEqual(plan, "OK、進めて\n（計画は承認されていません。承認は Mac のターミナルで）")
        perm = ra._answer_text({"kind": "permission", "tool": "Bash"}, "はい")
        self.assertTrue(perm.startswith("はい\n") and "実行されていません" in perm, perm)

    def test_r86h_ghost_posting_falls_back_to_inbox(self):
        """心拍の止まった掲示（人間がターミナルで答えてフックが kill された幽霊）には回答しない＝受信箱へ。"""
        self._seed_device()
        p = self._post_ask()
        old = time.time() - 60
        os.utime(p, (old, old))
        self._mock([self._sign(text="幽霊には答えない")])
        self.assertEqual(ra.pull_and_deliver("http://x", "t"), 1)
        self.assertIsNone(self._reply(), "心拍の止まった掲示へ回答した（誰も受け取らない）")
        self.assertIn("幽霊には答えない", self._inbox("relaytest-0001").read_text(encoding="utf-8"))

    def test_r86h_reply_write_failure_is_retried_not_lost(self):
        """回答の書き込みが一時失敗したら ack せず nonce も焼かない＝次の周で届く（at-least-once）。"""
        self._seed_device()
        self._post_ask()
        env = self._sign(text="B案で", nonce="b6b6" * 8)
        calls, _ = self._mock([env], remove_on_ack=True)

        def boom(*a, **k):
            raise OSError("disk full")
        ra.office.write_approval_reply = boom
        self.assertEqual(ra.pull_and_deliver("http://x", "t"), 0)
        self.assertIsNone(calls["ack"], "失敗したのに ack した（回答がロストする）")
        self.assertNotIn(f"{DID}:{'b6b6' * 8}", ra._NONCES)
        self.assertFalse(self._inbox("relaytest-0001").exists(), "失敗を受信箱へ逃がした（止まっている間は届かない）")
        ra.office.write_approval_reply = self._orig["write_approval_reply"]
        self.assertEqual(ra.pull_and_deliver("http://x", "t"), 1)
        self.assertEqual(self._reply()["message"], "B案で")
        self.assertEqual(calls["ack"]["ids"], [1])

    def test_r86h_chain_through_real_hook(self):
        """署名封筒 → relay_agent → 回答 → **本物の承認フック**の標準出力までを wrangler 無しで通す
        （既定の verify で毎回走る。wrangler を使う版は tests/relay_e2e.sh の R99-P4-5）。
        質問 → deny＋回答そのもの／許可 → deny＋但し書き／計画の承認 → deny＋「承認されていない」（allow は絶対に出ない）。"""
        import subprocess
        self._seed_device()
        hook = ROOT / "hooks" / "office-approval-wait.sh"
        env = dict(os.environ, OFFICE_HOME=str(self.tmp), OFFICE_APPROVAL_WAIT="30",
                   OFFICE_APPROVAL_POLL="0.1")
        cases = {
            "relaytest-q001": ({"tool_name": "AskUserQuestion", "tool_input": {"questions": [{
                "question": "どちらで進めますか?", "options": [{"label": "A案"}, {"label": "B案"}]}]}},
                "選択肢「B案」でお願いします。"),
            "relaytest-p002": ({"tool_name": "Bash", "tool_input": {"command": "git push origin main"}},
                               "はい、承認します"),
            "relaytest-x003": ({"tool_name": "ExitPlanMode", "tool_input": {"plan": "手順を3つに分けます"}},
                               "OK、進めて"),
        }
        procs = {}
        try:
            for sess, (payload, _t) in cases.items():
                pl = dict(payload, session_id=sess, cwd="/tmp", hook_event_name="PermissionRequest",
                          transcript_path="/nonexistent/transcript.jsonl")
                p = subprocess.Popen(["bash", str(hook)], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                     stderr=subprocess.DEVNULL, text=True, env=env,
                                     start_new_session=True)
                p.stdin.write(json.dumps(pl, ensure_ascii=False))
                p.stdin.close()
                p.stdin = None
                procs[sess] = p
            end = time.time() + 15
            while time.time() < end and not all(ra.office.pending_approval(s) for s in cases):
                time.sleep(0.05)
            self.assertTrue(all(ra.office.pending_approval(s) for s in cases), "フックの掲示が出ない")
            # R102-11: スマホは表示していた掲示の印（askId の先頭 8 桁）を添えて答える（印の無い言葉は、出た直後の掲示へは答えない）
            ans = lambda s, t: json.dumps({"aioffice": 1, "kind": "answer",                 # noqa: E731
                                           "ask": ra.office.pending_approval(s)["askId"][:8], "text": t},
                                          ensure_ascii=False, separators=(",", ":"))
            self._mock([self._sign(session=s, text=ans(s, t)) for s, (_p, t) in cases.items()])
            self.assertEqual(ra.pull_and_deliver("http://x", "t"), 3)
            out = {}
            for sess, p in procs.items():
                stdout, _ = p.communicate(timeout=20)
                out[sess] = json.loads(stdout)["hookSpecificOutput"]["decision"]
        finally:
            for p in procs.values():
                if p.poll() is None:
                    try:
                        os.killpg(os.getpgid(p.pid), 9)
                    except (ProcessLookupError, PermissionError):
                        pass
                    p.wait(timeout=10)
        self.assertEqual(out["relaytest-q001"], {"behavior": "deny", "message": "選択肢「B案」でお願いします。"})
        self.assertEqual(out["relaytest-p002"]["behavior"], "deny", f"スマホ経由で allow が出た（掟 7）: {out}")
        self.assertIn("はい、承認します", out["relaytest-p002"]["message"])
        self.assertIn("実行されていません", out["relaytest-p002"]["message"])
        self.assertEqual(out["relaytest-x003"]["behavior"], "deny", f"スマホ経由で計画が承認された（掟 7）: {out}")
        # フックの clip() は改行を空白へ畳む（許可の但し書きと同じ）＝文言そのもの＋事実の 1 行を中身で見る
        self.assertTrue(out["relaytest-x003"]["message"].startswith("OK、進めて"), out)
        self.assertIn("計画は承認されていません。承認は Mac のターミナルで", out["relaytest-x003"]["message"])

    def test_r112h_every_question_reaches_the_relay_through_the_real_hook(self):
        """R112-H の鎖: **本物の承認フック**の掲示 → daemon の ask（pending_approval）→ 中継の ask（redaction）に
        全部の問いと選択肢が届く。3 問・2 問目が複数選択・description は載らない・1 問目の互換（qn/multi）も残る。"""
        import subprocess
        hook = ROOT / "hooks" / "office-approval-wait.sh"
        env = dict(os.environ, OFFICE_HOME=str(self.tmp), OFFICE_APPROVAL_WAIT="20", OFFICE_APPROVAL_POLL="0.1")
        sess = "relaytest-r112h"
        pl = {"session_id": sess, "cwd": "/tmp", "hook_event_name": "PermissionRequest",
              "transcript_path": "/nonexistent/transcript.jsonl", "tool_name": "AskUserQuestion",
              "tool_input": {"questions": [
                  {"question": "どの順で直しますか?", "header": "順序",
                   "options": [{"label": "server から", "description": "説明は載せない"}, {"label": "PWA から"}]},
                  {"question": "どれを含めますか?", "multiSelect": True,
                   "options": [{"label": "hook"}, {"label": "server"}, {"label": "relay"}]},
                  {"question": "3 問目", "options": [{"label": "はい"}, {"label": "いいえ"}]}]}}
        p = subprocess.Popen(["bash", str(hook)], stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL, text=True, env=env, start_new_session=True)
        try:
            p.stdin.write(json.dumps(pl, ensure_ascii=False))
            p.stdin.close()
            p.stdin = None
            end = time.time() + 15
            while time.time() < end and not ra.office.pending_approval(sess):
                time.sleep(0.05)
            ask = ra.office.pending_approval(sess)
            self.assertTrue(ask, "フックの掲示が出ない")
            self.assertEqual(ask["questions"], self.R112H_QS)
            out = ra._redact_office_for_relay({"employees": [{"session": sess, "ask": dict(ask, min=0)}]})
        finally:
            try:
                os.killpg(os.getpgid(p.pid), 9)
            except (ProcessLookupError, PermissionError):
                pass
            p.wait(timeout=10)
        relayed = out["employees"][0]["ask"]
        self.assertEqual(relayed["questions"], self.R112H_QS)
        self.assertEqual([q["multi"] for q in relayed["questions"]], [0, 1, 0])
        self.assertEqual((relayed["qn"], relayed["multi"], relayed["id8"]), (3, 1, ask["askId"][:8]))
        self.assertNotIn("説明は載せない", json.dumps(out, ensure_ascii=False))

    def test_tick_reports_delivery_even_if_status_fails(self):
        self._seed_device()
        env = self._sign(text="y")

        def fake(method, url, token, body=None):
            if url.endswith("/pull"):
                return {"ok": True, "items": [{"id": 7, "session": env["session"],
                                               "ts": int(time.time()), "text": json.dumps(env)}]}
            if url.endswith("/ack"):
                return {"ok": True}
            if url.endswith("/status"):
                raise ra.urllib.error.URLError("status down")
            return {"ok": True}
        ra._req = fake
        self.assertEqual(ra.tick("http://x", "t"), 1)     # status失敗でも配達成功件数を返す

    def test_push_status_posts_office_json(self):
        sent = {}
        ra.office.office_json = lambda: {"employees": [], "counts": {"working": 0}}

        def fake(method, url, token, body=None):
            sent["url"] = url
            sent["body"] = body
            return {"ok": True}
        ra._req = fake
        ra.push_status("http://x", "t")
        self.assertTrue(sent["url"].endswith("/status"))
        self.assertIn("employees", sent["body"]["office"])

    def test_redact_strips_message_body_keeps_activity(self):
        """中継へ送る前に本文/パスを落とし、動作ログ・質問・状態・名前は残す（プライバシー根元遮断）。"""
        snap = {"employees": [{
            "disp": "制作本部(works)", "session": "s1", "state": "working",
            "verb": "考え中…", "target": "アメックスの明細を精査",
            "lastSaid": "あなたのアメックスの利用明細は…", "lastOrder": "アメックスを調べて",
            "cwd": "/Users/x/amex_taxes", "branch": "amex-fix",
            "question": "この方針で進めて良いですか？",
            "questionOptions": [{"label": "そのまま進める (Recommended)", "desc": "現方針で続行"}],
            "minions": 2,
            "skills": ["x-post", "video-edit"],
            "tool": "Bash",
            "feed": ["実行中 明細CSV読込", "編集中 report.md", "💬 アメックスの残高は…"],
        }]}
        out = ra._redact_office_for_relay(snap)
        e = out["employees"][0]
        # R110: 工具名（tool）はローカルの部屋の用事の根拠＝新フィールドは既定で中継に載らない（allowlist）
        self.assertNotIn("tool", e)
        # 本文・パスは消える
        for k in ("lastSaid", "target", "lastOrder", "cwd", "branch"):
            self.assertEqual(e[k], "", f"{k} が残っている（本文露出）")
        # 💬発言行は消え、動作ログは残る
        self.assertEqual(e["feed"], ["実行中 明細CSV読込", "編集中 report.md"])
        self.assertFalse(any("💬" in ln for ln in e["feed"]))
        # 状態・動詞・質問・名前・minionsは保持（トリアージ用）
        self.assertEqual(e["state"], "working")
        self.assertEqual(e["verb"], "考え中…")
        self.assertEqual(e["question"], "この方針で進めて良いですか？")
        self.assertEqual(e["questionOptions"], [{"label": "そのまま進める (Recommended)", "desc": "現方針で続行"}])
        self.assertEqual(e["disp"], "制作本部(works)")
        self.assertEqual(e["minions"], 2)
        self.assertEqual(e["skills"], ["x-post", "video-edit"])

    def test_redact_drops_evidence_r98(self):
        """R98: 証拠列 evidence は allowlist 外＝中継（スマホ）には載らない（新フィールドは既定で非搬送の不変条件）。"""
        snap = {"employees": [{"session": "s1", "state": "working",
                               "evidence": {"kind": "committed", "ago": 41}}],
                "roster": [{"projectId": "p1", "session": "s1", "state": "working",
                            "evidence": {"kind": "failed", "ago": 3}, "sessions": [{"session": "s1", "state": "working"}]}]}
        out = ra._redact_office_for_relay(snap)
        self.assertNotIn("evidence", out["employees"][0])
        self.assertNotIn("evidence", out["roster"][0])
        self.assertIn("evidence", ra._LOCAL_ONLY_ENTRY)

    def test_redact_scrubs_paths_and_urls_in_feed(self):
        """S3: feed の動作ログ行にもパス縮約・URLホスト化・切り詰めが掛かる
        （target と同じ値が feed にも入る自己矛盾＝中継への本文/パス漏れを塞ぐ）。"""
        snap = {"employees": [{
            "disp": "P", "session": "s", "state": "working", "verb": "実行中",
            "feed": ["実行中 cd /Users/alice/Documents/AmexTax-2026 && ls",
                     "調査中 https://internal.example.com/customers/acme?token=secret",
                     "部下に指示中 ~/work/nda/顧客名リストを要約して次の章を書き出す長い長い指示文",
                     "💬 これは発言なので消える"],
        }]}
        out = ra._redact_office_for_relay(snap)
        feed = out["employees"][0]["feed"]
        joined = " / ".join(feed)
        self.assertNotIn("/Users/alice", joined)          # 絶対パスが消える
        self.assertNotIn("Documents", joined)
        self.assertNotIn("internal.example.com/customers", joined)  # URLのパス/クエリが消える
        self.assertNotIn("token=secret", joined)
        self.assertNotIn("~/work/nda", joined)            # チルダパスが消える
        for ln in feed:
            self.assertLessEqual(len(ln), 60)             # 長い指示文の切り詰め
        self.assertTrue(any("実行中" in ln for ln in feed))  # 動詞ラベルは残る
        self.assertFalse(any("💬" in ln for ln in feed))

    def test_redact_covers_projects_array(self):
        """R50: roster[] も employees[] と同じ規則で本文/パスを落とす。
        新フィールドを office_json に足したら redaction を通す＝掟の機械化。"""
        snap = {"employees": [], "roster": [{
            "projectId": "a1b2c3d4e5f6", "session": "s1", "name": "works", "crew": 3,
            "state": "working", "kind": "tool",
            "verb": "編集中", "target": "/Users/x/secret/amex.csv",
            "lastSaid": "アメックスの残高は…", "lastOrder": "アメックスを調べて",
            "cwd": "/Users/x/amex_taxes", "branch": "amex-fix",
            "question": "この方針で良いですか？",
            "feed": ["実行中 集計", "💬 残高は…"],
            "work": {"now": ["/Users/x/secret/plan.md を更新"], "next": [], "done": [],
                     "counts": {"pending": 1, "in_progress": 2, "completed": 3}},
            "sessions": [{"session": "s1", "state": "working", "age": 5,
                          "attention": False, "minions": 0, "pending": False}],
        }]}
        out = ra._redact_office_for_relay(snap)
        p = out["roster"][0]
        for k in ("lastSaid", "target", "lastOrder", "cwd", "branch"):
            self.assertEqual(p[k], "", f"roster[].{k} が残っている（本文露出）")
        self.assertEqual(p["feed"], ["実行中 集計"])
        self.assertNotIn("/Users/", json.dumps(p, ensure_ascii=False), "パスが中継へ漏れている")
        # トリアージに要る情報は残る
        self.assertEqual(p["projectId"], "a1b2c3d4e5f6")   # cwdのハッシュ＝パスを含まない
        self.assertEqual(p["crew"], 3)
        self.assertEqual(p["kind"], "tool")
        self.assertEqual(p["question"], "この方針で良いですか？")
        self.assertEqual(p["work"]["counts"], {"pending": 1, "in_progress": 2, "completed": 3})
        self.assertEqual(p["sessions"][0]["session"], "s1")

    def test_relay_agent_never_reads_dialog_plaintext(self):
        """R86-B → R87: 会話本文API(/api/session/dialog)は中継が構造的に呼ばない＝
        本文が Cloudflare へ流れる経路が存在しないことをソースで固定する。
        R87 で封書（暗号文）のレーンを足したので、**平文を読む関数・ページ定数・復号関数を
        relay_agent が一切持たない**ことと、allowlist に会話系のキーが混ざらないことも足す。"""
        src = Path(ra.__file__).read_text(encoding="utf-8")
        self.assertNotIn("session/dialog", src)
        self.assertNotIn("dialog_from_lines", src)
        for token in ("tail_lines", "DIALOG_", "unseal", "_session_transcript", "dialog_page"):
            self.assertNotIn(token, src, f"relay_agent が平文経路 {token} を持っている")
        for key in ra._ALLOW_TOP | ra._ALLOW_ENTRY:
            self.assertNotRegex(key, r"dialog|dlg|messages", f"allowlist に会話系のキー {key}")

    def test_redact_err_and_perm_mode_are_enums_only(self):
        """R102-4: 止まった理由（err）と権限モード（permMode）は enum と経過秒だけ。本文・知らない値は落ちる・
        中断の時刻（interruptedAt）はローカル専用。"""
        snap = {"employees": [
            {"session": "a", "err": {"k": "limit", "ago": 42, "text": "You've reached your limit"},
             "permMode": "plan", "interruptedAt": 1800000000.5},
            {"session": "b", "err": {"k": "evil", "ago": 1}, "permMode": "<img>"},
            {"session": "c", "err": "API Error: 529", "permMode": "bypassPermissions"},
            {"session": "d", "err": {"k": "api", "ago": "3"}}],
            "roster": [{"projectId": "p", "session": "a", "err": {"k": "ctx", "ago": 5}, "permMode": "acceptEdits"}]}
        out = ra._redact_office_for_relay(snap)
        a, b, c, d = out["employees"]
        self.assertEqual((a["err"], a["permMode"]), ({"k": "limit", "ago": 42}, "plan"))
        self.assertNotIn("interruptedAt", a)
        self.assertNotIn("err", b)
        self.assertNotIn("permMode", b)
        self.assertEqual(("err" in c, c["permMode"]), (False, "bypassPermissions"))
        self.assertEqual(d["err"], {"k": "api", "ago": 0})
        self.assertEqual((out["roster"][0]["err"], out["roster"][0]["permMode"]), ({"k": "ctx", "ago": 5}, "acceptEdits"))

    def test_redact_title_default_pass_and_optout_strip(self):
        """R85-1: title（/renameのセッション名）は既定で中継へ通す（ユーザー裁定2026-08-26・
        Push本文にも同じ名前を出す）。OFFICE_RELAY_TITLES=0 なら根元遮断（lastSaidと同流儀）。"""
        def snap():
            return {"employees": [{"disp": "開発", "title": "決済チーム", "session": "s1"}],
                    "roster": [{"projectId": "p1", "name": "開発", "disp": "開発",
                                "title": "決済チーム", "session": "s1"}]}
        out = ra._redact_office_for_relay(snap())
        self.assertEqual(out["employees"][0]["title"], "決済チーム")
        self.assertEqual(out["roster"][0]["title"], "決済チーム")
        orig = ra.RELAY_TITLES
        try:
            ra.RELAY_TITLES = False
            out2 = ra._redact_office_for_relay(snap())
            self.assertEqual(out2["employees"][0]["title"], "")
            self.assertEqual(out2["roster"][0]["title"], "")
        finally:
            ra.RELAY_TITLES = orig

    def test_redact_drops_retired_edition_toplevel(self):
        """Retired metadata from an older producer must not reach the relay."""
        snap = {
            "edition": {"id": "hybrid",
                        "features": {"claudeSessions": True, "openclaw": True}},
            "employees": [{"session": "s1", "lastSaid": "秘密", "cwd": "/Users/x",
                           "target": "", "lastOrder": "", "branch": "", "feed": []}],
        }
        out = ra._redact_office_for_relay(snap)
        self.assertNotIn("edition", out)
        self.assertEqual(out["employees"][0]["lastSaid"], "")

    def test_redact_drops_history_bodies(self):
        """R50提案1: history[] は指示の全文を含む＝中継へ1件も流さない（PWAは描画しない）。"""
        snap = {
            "history": [{"session": "s1", "disp": "編集部", "text": "カード番号は 4111... を使って",
                         "ts": 1753900000, "pending": True}],
            "employees": [], "roster": [],
        }
        out = ra._redact_office_for_relay(snap)
        self.assertEqual(out["history"], [])

    # ---- R50提案4: /sync（1周1リクエスト）・指紋push ----
    def _mock_sync(self, envelopes, appseen=None, openclaw=None):
        """POST /sync だけを受けるモック。ackIds で items から削除（DO側の削除を模擬）。"""
        items = [{"id": i + 1, "text": json.dumps(e)} for i, e in enumerate(envelopes)]
        calls = {"sync": []}

        def fake(method, url, token, body=None):
            if url.endswith("/sync") and method == "POST":
                calls["sync"].append(json.loads(json.dumps(body)))
                ids = set(body.get("ackIds") or [])
                items[:] = [it for it in items if it["id"] not in ids]
                return {"ok": True, "items": list(items),
                        "appSeenAgo": appseen, "openclaw": openclaw}
            raise AssertionError(f"unexpected request: {method} {url}")
        ra._req = fake
        return calls, items

    def test_fingerprint_stable_under_age_drift(self):
        base = {"roster": [{"disp": "A", "age": 10, "state": "working",
                            "sessions": [{"session": "s", "age": 30}]}],
                "employees": [], "generatedAt": 100}
        drift = {"roster": [{"disp": "A", "age": 40, "state": "working",
                             "sessions": [{"session": "s", "age": 55}]}],
                 "employees": [], "generatedAt": 165}
        changed = {"roster": [{"disp": "A", "age": 10, "state": "waiting",
                               "sessions": [{"session": "s", "age": 30}]}],
                   "employees": [], "generatedAt": 100}
        self.assertEqual(ra._status_fingerprint(base), ra._status_fingerprint(drift))
        self.assertNotEqual(ra._status_fingerprint(base), ra._status_fingerprint(changed))
        # R102-4: 止まってからの秒（err.ago）も分単位＝時間だけで毎周「変化あり」にしない。種類が変われば変化
        e1 = {"roster": [{"disp": "A", "state": "waiting", "err": {"k": "api", "ago": 61}}], "employees": []}
        e2 = {"roster": [{"disp": "A", "state": "waiting", "err": {"k": "api", "ago": 119}}], "employees": []}
        e3 = {"roster": [{"disp": "A", "state": "waiting", "err": {"k": "limit", "ago": 61}}], "employees": []}
        self.assertEqual(ra._status_fingerprint(e1), ra._status_fingerprint(e2))
        self.assertNotEqual(ra._status_fingerprint(e1), ra._status_fingerprint(e3))

    def test_fingerprint_ignores_relay_usage_and_res_stale(self):
        """S2: 中継使用量(relay.rows)とres.staleSecは毎sync増える揮発値。指紋に含めると
        『1回syncするたび指紋が変わり→また送信』の自己参照ループで無料枠を食う。除外を固定。"""
        a = {"roster": [], "employees": [], "generatedAt": 1,
             "relay": {"rows": 100, "pct": 3, "level": 0},
             "res": {"fiveHour": 20, "sevenDay": 40, "staleSec": 5}}
        b = {"roster": [], "employees": [], "generatedAt": 99,
             "relay": {"rows": 900, "pct": 9, "level": 0},          # rows激増でも
             "res": {"fiveHour": 20, "sevenDay": 40, "staleSec": 55}}  # staleドリフトでも
        self.assertEqual(ra._status_fingerprint(a), ra._status_fingerprint(b))
        # 実データ（枠%）が動いたら指紋は変わる（＝本当の変化は拾う）
        c = {"roster": [], "employees": [], "generatedAt": 1,
             "relay": {"rows": 100, "pct": 3, "level": 0},
             "res": {"fiveHour": 55, "sevenDay": 40, "staleSec": 5}}
        self.assertNotEqual(ra._status_fingerprint(a), ra._status_fingerprint(c))

    def test_sync_tick_delivers_and_acks_next_round(self):
        """配達→ack持ち越し→次周のackIdsで削除・無変化ならoffice=None（変化時のみpush）。"""
        self._seed_device()
        fixed = {"roster": [], "employees": [], "history": []}
        ra.office.office_json = lambda: json.loads(json.dumps(fixed))
        calls, items = self._mock_sync([self._sign(text="やあ")])
        state = {"acks": [], "fp": None, "pushed_at": 0.0}
        n, seen, app_online = ra.sync_tick("http://x", "t", state)   # R79-8: 3タプル（appOnline追加）
        self.assertEqual(n, 1)
        self.assertTrue(self._inbox("relaytest-0001").exists())
        self.assertEqual(state["acks"], [1])                       # ackは次周へ持ち越し
        self.assertIsNotNone(calls["sync"][0]["office"])           # 初回は必ずpush
        self.assertIsNone(seen)
        self.assertFalse(app_online)                               # WS未接続のモックではFalse
        n2, _, _ = ra.sync_tick("http://x", "t", state)
        self.assertEqual(calls["sync"][1]["ackIds"], [1])          # 前周の配達分を削除
        self.assertIsNone(calls["sync"][1]["office"])              # 無変化＝push省略
        self.assertEqual(items, [])                                # DO側から消えた
        self.assertEqual((n2, state["acks"]), (0, []))

    # ── R79-10: act-封筒は office_inbox に書かず daemon へ回す ──
    def test_r7910_act_goes_to_daemon_not_inbox(self):
        """act- 宛は post_instruction を呼ばない（孤児inbox根絶）＋daemonへPOSTして ack。"""
        self._seed_device()
        calls = []
        ra._post_action = lambda payload: (calls.append(payload) or {"ok": True, "state": "running"})
        posted = []
        real_post = ra.office.post_instruction
        ra.office.post_instruction = lambda s, t, *, src: (posted.append((s, t)) or (True, "ok"))
        try:
            act = {"aioffice": 1, "kind": "run", "recipe": "r_verify", "args": [],
                   "reqId": "req-unit0001"}
            env = self._sign(session="act-0123456789abcdef", text=json.dumps(act))
            items = [{"id": 7, "session": env["session"], "ts": int(time.time()),
                      "text": json.dumps(env)}]
            delivered, ack_ids = ra._process_items(items)
        finally:
            ra.office.post_instruction = real_post
        self.assertEqual(posted, [], "act- が office_inbox へ書かれた（孤児inbox）")
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["action"]["recipe"], "r_verify")
        self.assertEqual((delivered, ack_ids), (1, [7]))
        self.assertFalse(self._inbox("act-0123456789abcdef").exists())

    def test_r7910_act_not_acked_when_daemon_down(self):
        """daemon不達なら **ackせず・nonceも焼かず** 残置（次周で再送＝配達durabilityの掟）。"""
        self._seed_device()

        def boom(_payload):
            raise OSError("daemon down")
        ra._post_action = boom
        act = {"aioffice": 1, "kind": "run", "recipe": "r_verify", "args": [],
               "reqId": "req-unit0002"}
        env = self._sign(session="act-fedcba9876543210", text=json.dumps(act))
        items = [{"id": 9, "session": env["session"], "ts": int(time.time()),
                  "text": json.dumps(env)}]
        delivered, ack_ids = ra._process_items(items)
        self.assertEqual((delivered, ack_ids), (0, []))
        key = f'{env["device_id"]}:{env["nonce"]}'
        self.assertNotIn(key, ra._NONCES, "不達なのに nonce を焼いた（恒久ロストの原因）")

    def test_r7910_act_bad_format_is_acked_and_dropped(self):
        """形式不正の act- は恒久不正＝即ackで捨てる（キューを詰まらせない）。"""
        self._seed_device()
        ra._post_action = lambda payload: {"ok": True}
        env = self._sign(session="act-00112233445566aa", text="これはJSONではない")
        items = [{"id": 11, "session": env["session"], "ts": int(time.time()),
                  "text": json.dumps(env)}]
        delivered, ack_ids = ra._process_items(items)
        self.assertEqual((delivered, ack_ids), (1, [11]))

    # ── R100-1b スマホから許可: apv-/enr- は封筒ごと daemon へ（inbox へは書かない・daemon が確かめ直す） ──
    def _apv_items(self, prefix="apv", rid="1" * 32, iid=41, signed=True):
        body = {"aioffice": 1, "kind": "approve" if prefix == "apv" else "enroll", "reqId": rid}
        env = self._sign(session=f"{prefix}-{rid}", text=json.dumps(body))
        text = json.dumps(env) if signed else json.dumps(body)
        return env, [{"id": iid, "session": env["session"], "ts": int(time.time()), "text": text}]

    def test_r100_apv_and_enr_go_to_daemon_with_the_whole_envelope(self):
        self._seed_device()
        calls = []
        ra._post_envelope = lambda url, env: (calls.append((url, env)) or {"ok": True, "state": "granted"})
        posted = []
        real_post = ra.office.post_instruction
        ra.office.post_instruction = lambda s, t, *, src: (posted.append((s, t)) or (True, "ok"))
        try:
            env1, it1 = self._apv_items("apv", "1" * 32, 41)
            env2, it2 = self._apv_items("enr", "2" * 32, 42)
            delivered, ack_ids = ra._process_items(it1 + it2)
        finally:
            ra.office.post_instruction = real_post
        self.assertEqual(posted, [], "apv-/enr- が office_inbox へ書かれた")
        self.assertEqual(sorted(ack_ids), [41, 42])
        self.assertEqual([c[0] for c in calls], [ra.APV_URL, ra.ENR_URL])
        self.assertEqual(calls[0][1], env1)                  # 封筒をそのまま（device_id を relay_agent が決めない）
        self.assertEqual(calls[1][1], env2)

    def test_r103_rst_goes_to_the_remote_set_daemon_route(self):
        """R103-1: rst-（スマホから「スマホから許可」を ON/OFF）も封筒ごと daemon の /api/approval/remote_set へ。inbox へは書かない。"""
        self._seed_device()
        calls = []
        ra._post_envelope = lambda url, env: (calls.append((url, env)) or {"ok": True, "state": "granted"})
        posted = []
        real_post = ra.office.post_instruction
        ra.office.post_instruction = lambda s, t, *, src: (posted.append((s, t)) or (True, "ok"))
        try:
            body = {"aioffice": 1, "kind": "remote_set", "reqId": "5" * 32, "on": False}
            env = self._sign(session="rst-" + "5" * 32, text=json.dumps(body))
            delivered, ack_ids = ra._process_items([{"id": 45, "session": env["session"], "ts": int(time.time()),
                                                     "text": json.dumps(env)}])
        finally:
            ra.office.post_instruction = real_post
        self.assertEqual(posted, [], "rst- が office_inbox へ書かれた")
        self.assertEqual(ack_ids, [45])
        self.assertEqual(calls, [(ra.RST_URL, env)])
        self.assertTrue(ra.RST_URL.endswith("/api/approval/remote_set"))
        with mock.patch.object(ra, "ALLOW_UNSIGNED", True):              # 無署名は恒久拒否（daemon へ回さない）
            calls.clear()
            raw = [{"id": 46, "session": "rst-" + "6" * 32, "ts": int(time.time()), "text": json.dumps(body)}]
            self.assertEqual(ra._process_items(raw)[1], [46])
        self.assertEqual(calls, [])

    def test_r116_rky_goes_to_the_rekey_daemon_route(self):
        """R116-S1b: rky-（つながりの延長の req／ack）も封筒ごと daemon の /api/pair/rekey へ。台帳を書くのは daemon だけ＝
        relay_agent は確かめて回すだけ・inbox へは書かない・無署名は daemon へ回さず恒久拒否。"""
        self._seed_device()
        calls = []
        ra._post_envelope = lambda url, env: (calls.append((url, env)) or {"ok": True, "state": "sealed"})
        posted = []
        real_post = ra.office.post_instruction
        ra.office.post_instruction = lambda s, t, *, src: (posted.append((s, t)) or (True, "ok"))
        try:
            body = {"aioffice": 1, "kind": "rekey", "step": "req", "reqId": "7" * 32}
            env = self._sign(session="rky-" + "7" * 32, text=json.dumps(body))
            delivered, ack_ids = ra._process_items([{"id": 47, "session": env["session"], "ts": int(time.time()),
                                                     "text": json.dumps(env)}])
        finally:
            ra.office.post_instruction = real_post
        self.assertEqual(posted, [], "rky- が office_inbox へ書かれた")
        self.assertEqual(ack_ids, [47])
        self.assertEqual(calls, [(ra.RKY_URL, env)])
        self.assertTrue(ra.RKY_URL.endswith("/api/pair/rekey"))
        with mock.patch.object(ra, "ALLOW_UNSIGNED", True):
            calls.clear()
            raw = [{"id": 48, "session": "rky-" + "8" * 32, "ts": int(time.time()), "text": json.dumps(body)}]
            self.assertEqual(ra._process_items(raw)[1], [48])
        self.assertEqual(calls, [])

    def test_r100_apv_is_never_unsigned_even_with_allow_unsigned(self):
        self._seed_device()
        calls = []
        ra._post_envelope = lambda url, env: (calls.append(env) or {"ok": True})
        with mock.patch.object(ra, "ALLOW_UNSIGNED", True):
            _env, items = self._apv_items("apv", "3" * 32, 43, signed=False)
            delivered, ack_ids = ra._process_items(items)
        self.assertEqual(calls, [])
        self.assertEqual(ack_ids, [43])                      # 恒久拒否（捨てる）

    def test_r100_apv_not_acked_when_daemon_down(self):
        self._seed_device()

        def boom(url, env):
            raise OSError("daemon down")
        ra._post_envelope = boom
        env, items = self._apv_items("apv", "4" * 32, 44)
        self.assertEqual(ra._process_items(items), (0, []))
        self.assertNotIn(f'{env["device_id"]}:{env["nonce"]}', ra._NONCES)

    def test_r100_relay_agent_never_holds_the_approval_parts(self):
        """危険判定・パスキーの検証・ON の読みは daemon と hook だけ。relay_agent は配達員のまま（grep の門）。"""
        src = Path(ra.__file__).read_text(encoding="utf-8")
        for token in ("approval_policy", "p256_verify", "office_remote_approve", "office_approve"):
            self.assertNotIn(token, src, f"relay_agent が {token} を持っている")

    def test_r100_ask_redaction_keeps_only_tool_kind_remote_and_verb(self):
        e = {"session": "s1", "state": "waiting", "stuckTool": "実行中 npm test --token sk-abc",
             "ask": {"tool": "Bash", "kind": "permission", "title": "npm test", "askId": "a" * 32, "remote": 1,
                     "options": []}}
        ra._redact_entry_for_relay(e)
        # R102-11: 本文は落とし、掲示の識別は askId の先頭 8 桁（id8）だけ（意図した更新）
        self.assertEqual(e["ask"], {"tool": "Bash", "kind": "permission", "remote": 1, "id8": "a" * 8})
        self.assertEqual(e["stuckTool"], "実行中")
        e2 = {"session": "s2", "ask": {"tool": "Bash", "kind": "permission", "remote": True}}
        ra._redact_entry_for_relay(e2)
        self.assertEqual(e2["ask"]["remote"], 0)             # 1 以外は 0（True も 0）

    def test_ask_minutes_question_shape_and_live_minions_are_integers_only(self):
        """掲示からの分（ask.min）・質問の問いの数と複数選択（ask.qn・ask.multi）・直近 60 秒の子（minionsLive）は
        整数だけ通す（文字・bool・範囲外はキーごと落とす）。1 問目の質問文（title）・選択肢（options）は ask に入らない
        （全部の問いと選択肢は R112-H の questions＝形を検証して別に運ぶ・下の test_r112h_*）。"""
        snap = {"employees": [
            {"session": "a", "minionsLive": 2,
             "ask": {"tool": "AskUserQuestion", "kind": "question", "title": "どの順で直しますか？",
                     "options": ["x"], "min": 41, "qn": 3, "multi": 1, "remote": 0}},
            {"session": "b", "minionsLive": "2",
             "ask": {"tool": "AskUserQuestion", "kind": "question", "min": "41", "qn": True, "multi": 2}},
            {"session": "c", "minionsLive": True, "ask": {"tool": "Bash", "kind": "permission", "min": -1}}],
            "roster": [{"projectId": "p", "session": "a", "minionsLive": 1,
                        "ask": {"tool": "ExitPlanMode", "kind": "question", "min": 0}}]}
        out = ra._redact_office_for_relay(snap)
        a, b, c = out["employees"]
        self.assertEqual(a["ask"], {"tool": "AskUserQuestion", "kind": "question", "remote": 0,
                                    "min": 41, "qn": 3, "multi": 1})
        self.assertEqual(a["minionsLive"], 2)
        self.assertEqual(b["ask"], {"tool": "AskUserQuestion", "kind": "question", "remote": 0})
        self.assertNotIn("minionsLive", b)
        self.assertEqual(c["ask"], {"tool": "Bash", "kind": "permission", "remote": 0})
        self.assertNotIn("minionsLive", c)
        self.assertEqual(out["roster"][0]["ask"], {"tool": "ExitPlanMode", "kind": "question", "remote": 0, "min": 0})
        self.assertEqual(out["roster"][0]["minionsLive"], 1)

    def test_ask_id8_is_the_posting_prefix_only(self):
        """R102-11: 掲示の識別は askId の先頭 8 桁だけ（hook の乱数・本文ではない）。形が違えば載せない。"""
        aid = "0123456789abcdef0123456789abcdef"
        snap = {"employees": [{"session": "a", "ask": {"tool": "Bash", "kind": "permission", "askId": aid, "title": "rm -rf x"}},
                              {"session": "b", "ask": {"tool": "Bash", "kind": "permission", "askId": "ZZZZZZZZ"}},
                              {"session": "c", "ask": {"tool": "Bash", "kind": "permission", "askId": 12345678}}]}
        out = ra._redact_office_for_relay(snap)
        a, b, c = out["employees"]
        self.assertEqual(a["ask"].get("id8"), "01234567")
        self.assertNotIn("askId", a["ask"])
        self.assertNotIn("title", a["ask"])
        self.assertNotIn("id8", b["ask"])
        self.assertNotIn("id8", c["ask"])

    # ── R116-S0: 伏字は office_json() のキャッシュ（2 秒間は同じ dict）を書き換えない ──
    def _r116_office(self, ask=True):
        """office_json() が返す形に、伏字で落とす・縮める・作り直すキーを一通り入れた 1 件（掲示あり／なし）。"""
        aid = "89abcdef" + "0" * 24
        emp = {"session": "s-r116-0001", "state": "waiting", "disp": "検証部", "title": "名前",
               "lastSaid": "秘密の本文", "target": "/Users/x/秘密", "lastOrder": "秘密の指示", "cwd": "/Users/x/p",
               "branch": "b", "detail": "/Users/x/a.py を直した", "feed": ["💬 発言", "実行中 /Users/x/run.sh"],
               "stuckTool": "実行中 rm -rf /Users/x/tmp",
               "sessions": [{"session": "s-r116-0001", "state": "waiting", "pid": 5, "cwd": "/Users/x/p"}],
               "work": {"now": ["/Users/x/a を修正"], "next": [], "done": [], "counts": {"pending": 1}},
               "err": {"k": "api", "ago": 61}, "permMode": "default", "minionsLive": 1, "bg": {"x": 1}, "pid": 9}
        if ask:
            emp["approvalMin"] = 1
            emp["ask"] = {"tool": "Bash", "kind": "permission", "title": "rm -rf /Users/x/tmp", "askId": aid,
                          "options": [], "ts": 1.0, "askCount": 1, "remote": 1, "min": 0}
        return aid, {"employees": [emp], "roster": [dict(emp, projectId="0123456789ab")],
                     "history": [{"text": "秘密"}], "timeline": [1], "macActive": 1,
                     "sources": {"claude": {"fg": 1, "paths": ["/Users/x"]}}, "events": {"seq": 3, "files": ["/Users/x"]},
                     "growth": {"byProject": {"p": {"xp": 1, "level": 0, "breakdown": {}}}, "office": {"xp": 1}},
                     "generatedAt": 1}

    def test_r116_redaction_leaves_the_cached_office_json_untouched(self):
        """R116-S0: office_json() は CACHE_SEC（2 秒）の間**同じ dict** を返す。伏字はそれを書き換えず新しい dict を組む。
        以前はその場で削ったので、ws_loop のローカル走査と直後の sync が同じ dict に 2 回かけると 1 回目で askId が消え、
        中継へ送る 2 回目から id8 が落ちた（R102-11 の「表示した 1 件にだけ答える」と全文の先読みが本番で効いていなかった）。"""
        import copy
        aid, snap = self._r116_office()
        before = copy.deepcopy(snap)
        out1 = ra._redact_office_for_relay(snap)
        out2 = ra._redact_office_for_relay(snap)
        self.assertEqual(snap, before, "入力（office_json のキャッシュ）を書き換えた")
        self.assertIsNot(out1, snap)
        self.assertEqual(out1, out2, "同じ入力に 2 回かけて結果が変わった")
        for key in ("employees", "roster"):
            self.assertEqual(out2[key][0]["ask"].get("id8"), aid[:8], key)
            self.assertNotIn("askId", out2[key][0]["ask"])
        self.assertEqual(ra._status_fingerprint(out1), ra._status_fingerprint(out2))
        # 落とすものは今までどおり落ちる（allowlist の意味と落とすキーの集合は不変）
        blob = json.dumps(out2, ensure_ascii=False)
        for leak in ("秘密", "/Users/x", "rm -rf", "timeline", '"pid"', '"bg"', "paths", "files", "breakdown"):
            self.assertNotIn(leak, blob, leak)
        self.assertEqual(out2["history"], [])

    def test_r116_ws_loop_keeps_id8_and_does_not_resync_every_scan(self):
        """R116-S0 の鎖（wrangler 無し・既定の verify で毎回）: **本物の ws_loop** を偽の WS で回す。office_json() は本物と同じく
        「走査ごとに新しい dict・次の走査までは同じ dict」を返す（1 回目は掲示の前・2 回目から掲示あり）。
        掲示が出た周の sync に id8 が載り、その後の周は（何も変わらないので）sync を送らない。以前は 1 回目の伏字がキャッシュの
        askId を消し、送る方から id8 が落ち、印あり／なしの指紋が交互に立って走査のたびに sync を送った（分析 W1/W2・本番と同じ
        WS 常駐の形で再現）。中継の本物の経路（wrangler・本物の承認フック）は tests/relay_e2e.sh の R116-S0。"""
        aid, with_ask = self._r116_office()
        _, without = self._r116_office(ask=False)
        scans = {"n": 0, "cur": None}

        def fresh_scan():
            scans["n"] += 1
            scans["cur"] = json.loads(json.dumps(without if scans["n"] == 1 else with_ask))
            # 反証（本番の形）: 送信履歴のある HOME（本番は常に）では today.lastSentAgo が 2 秒の走査のたびに増える
            scans["cur"]["today"] = {"sent": 1, "answered": 0, "lastSentAgo": 100 + 2 * scans["n"], "capped": False}

        fresh_scan()
        sent = []
        rounds = 6

        class Stop(Exception):
            pass

        class FakeWS:
            def __init__(self, url, token=None, timeout=10):
                self.inbox, self.rounds = [], 0

            def connect(self):
                pass

            def close(self):
                pass

            def send_text(self, s):
                if s == "p":
                    return
                d = json.loads(s)
                if d.get("t") == "sync":
                    sent.append(d)
                    self.inbox.append(json.dumps({"t": "sync", "ok": True, "items": [], "appOnline": True}))

            def recv(self, timeout=None):
                if self.inbox:
                    return self.inbox.pop(0)
                self.rounds += 1
                if self.rounds > rounds:
                    raise Stop()
                fresh_scan()           # 走査の間隔（2 秒）＞ CACHE_SEC＝次のローカル走査は新しい dict
                return None

        state = {"acks": [], "fp": None, "pushed_at": 0.0, "attn": False}
        with mock.patch.object(ra.wsc, "WSClient", FakeWS), \
                mock.patch.object(ra.office, "office_json", lambda: scans["cur"]), \
                mock.patch.object(ra, "_dialog_payload", lambda st: (None, "")), \
                mock.patch.object(ra, "forward_oc_outbox", lambda url, token: 0), \
                contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaises(Stop):
                ra.ws_loop("http://x", "t", state)
        self.assertEqual(scans["n"], rounds + 1, "ローカル走査が回っていない")
        self.assertEqual(len(sent), 2, f"掲示の後も走査のたびに sync を送っている: {len(sent)} 回（{rounds} 周）")
        self.assertNotIn("ask", sent[0]["office"]["employees"][0])
        for key in ("employees", "roster"):
            self.assertEqual(sent[1]["office"][key][0]["ask"].get("id8"), aid[:8], f"中継へ送る ask に id8 が無い（{key}）")

    def test_r116_fingerprint_ignores_seconds_since_last_send(self):
        """R116-S0（反証）: today.lastSentAgo は最後の送信からの**秒**＝走査のたびに増える。指紋に入れると、送信履歴が 1 行でもある
        HOME（_history.json は 50 件を期限なしで持つ＝本番は常に）で走査のたびに office を載せた sync を送る（不在 30 秒・在席 2 秒
        ごと＝DO 4 行）。スマホは読まない＝指紋から外す。新しい送信（sent）・答えた数（answered）・上限（capped）は今までどおり出る。"""
        def fp(**today):
            snap = {"employees": [], "roster": [], "generatedAt": 1,
                    "today": dict({"sent": 1, "answered": 0, "lastSentAgo": 100, "capped": False}, **today)}
            before = json.dumps(snap, sort_keys=True)
            out = ra._status_fingerprint(snap)
            self.assertEqual(json.dumps(snap, sort_keys=True), before, "指紋が入力を書き換えた")
            return out

        base = fp()
        for ago in (101, 160, 100 + 86400, None):
            self.assertEqual(fp(lastSentAgo=ago), base, f"lastSentAgo={ago} で指紋が変わった（走査のたびに sync を送る）")
        self.assertNotEqual(fp(sent=2, lastSentAgo=0), base, "新しい送信が指紋に出ない")
        self.assertNotEqual(fp(answered=1), base)
        self.assertNotEqual(fp(capped=True), base)

    def test_r116_real_office_json_fingerprint_holds_while_only_time_passes(self):
        """R116-S0（反証・本番の形）: **本物の office_json()** を一時 HOME（transcript 1 本＋送信履歴 1 行）で時刻だけ 2 秒ずつ進めて
        走査し、中継の伏字の後の指紋が変わらない（変われば ws_loop は走査のたびに sync を送る）。手組みの _r116_office() は today を
        持たず、relay_e2e の一時 HOME は送信履歴を持たなかった＝本番の条件を誰も作っていなかった（today.lastSentAgo を見逃した）。
        別プロセス（OFFICE_HOME を読み込みの前に決める＝実 HOME を読まない・書かない・時計は差し替え）。
        R127: 出勤の口の Bot（出勤中・相談中）も同じ HOME に居る＝中継に載る Bot の行も時刻だけでは指紋を動かさない。"""
        import subprocess
        import shutil
        import sys
        script = r'''
import json, os, shutil, sys, time
root, home = sys.argv[1], sys.argv[2]
sys.path.insert(0, os.path.join(root, "server"))
T0 = 1_800_000_000.0
NOW = [T0]
time.time = lambda: NOW[0]
import relay_agent as ra
office = ra.office
door = office.office_door
assert door.add_bot("fpbot", "見回り", home=home)["ok"]
assert door.checkin("fpbot", "受信箱を見回り中", ttl_min=240, home=home)["ok"]
aid = door.ask("fpbot", "送ってよい？", ["はい", "いいえ"], home=home)["askId"]
proj = os.path.join(home, ".claude", "projects", "-Users-test-fp")
os.makedirs(proj)
tr = os.path.join(proj, "e2e-fp-0001.jsonl")
shutil.copy(os.path.join(root, "tests", "fixtures", "working_tool.jsonl"), tr)
os.utime(tr, (T0 - 5, T0 - 5))                 # 経過は 5〜25 秒＝分の境を跨がない（age は分で量子化）
hist = os.path.join(home, ".claude", "office_inbox", "_history.json")
os.makedirs(os.path.dirname(hist))
rows = [{"session": "e2e-fp-0001", "disp": "fp", "text": "t", "ts": T0 - 100}]
with open(hist, "w", encoding="utf-8") as f:
    json.dump(rows, f)
out = {"fps": [], "ago": [], "emps": [], "bot": None}
def scan(t):
    NOW[0] = T0 + t
    office._invalidate_cache()
    snap = ra._redact_office_for_relay(office.office_json())
    out["ago"].append((snap.get("today") or {}).get("lastSentAgo"))
    out["emps"].append(len(snap.get("employees") or []))
    out["bot"] = [e for e in snap.get("employees") or [] if e.get("vendor") == "bot"]
    return ra._status_fingerprint(snap)
for t in (0, 2, 4, 20):
    out["fps"].append(scan(t))
rows.append({"session": "e2e-fp-0001", "disp": "fp", "text": "t2", "ts": T0 + 21})   # 新しい送信＝本当の変化
with open(hist, "w", encoding="utf-8") as f:
    json.dump(rows, f)
out["sent_fp"] = scan(22)
out["id8"] = aid[:8]
print(json.dumps(out, ensure_ascii=False))
'''
        base = ROOT / "tests" / "artifacts" / ".tmp"
        base.mkdir(parents=True, exist_ok=True)
        home = Path(tempfile.mkdtemp(prefix="r116_fp_", dir=base))
        self.addCleanup(shutil.rmtree, home, True)
        env = {k: v for k, v in os.environ.items() if not k.startswith(("OFFICE_", "RELAY_"))}
        env.update(HOME=str(home), OFFICE_HOME=str(home), OFFICE_DATA=str(home),
                   OFFICE_CONFIG=str(home / "office_config.json"), OFFICE_FAKE_MAC_IDLE="0",
                   OFFICE_AGENTS_CLI="0", OFFICE_SOURCES_CODEX="0", OFFICE_LANG="ja",
                   OFFICE_OPENCLAW_FIXTURE=str(home / "missing-openclaw.json"))
        r = subprocess.run([sys.executable, "-c", script, str(ROOT), str(home)], env=env,
                           capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr[-2000:])
        got = json.loads(r.stdout.strip().splitlines()[-1])
        self.assertEqual(got["emps"], [2] * 5, "transcript と Bot が走査に載らない（前提が崩れた）")
        (bot,) = got["bot"]
        self.assertEqual((bot["session"], bot["state"], bot["ask"].get("id8")), ("bot-fpbot", "waiting", got["id8"]))
        self.assertEqual(got["ago"], [100, 102, 104, 120, 1], "送信履歴が today に載らない（前提が崩れた）")
        self.assertEqual(len(set(got["fps"])), 1,
                         "時刻だけ進めた走査で中継の指紋が変わった＝走査のたびに office を載せた sync を送る")
        self.assertNotEqual(got["sent_fp"], got["fps"][0], "新しい送信が指紋に出ない（変化時のみ送信が鈍った）")

    def test_r127_real_office_json_with_a_bot_resyncs_at_most_every_ten_minutes(self):
        """R127（R116 と同じ型＝本物の office_json・別プロセス・時計の差し替え）: 相談を待つ Bot だけの HOME で時刻だけを進める。
        Bot の行の age・ask.min は指紋で 10 分に丸める＝分の境を何度跨いでも sync を送らず、10 分の境で 1 回送り直す。
        中継へ送る形に Bot の行（名前・状態・問い・印）が載り、askId 全体・doing・sources.door は載らない。"""
        import subprocess
        import shutil
        import sys
        script = r'''
import json, os, sys, time
root, home = sys.argv[1], sys.argv[2]
sys.path.insert(0, os.path.join(root, "server"))
T0 = 1_800_000_000.0
NOW = [T0]
time.time = lambda: NOW[0]
import relay_agent as ra
office = ra.office
door = office.office_door
assert door.add_bot("fpbot", "見回り", home=home)["ok"]
assert door.checkin("fpbot", "受信箱を見回り中 /Users/alice/inbox", ttl_min=240, home=home)["ok"]
aid = door.ask("fpbot", "送ってよい？", ["はい", "いいえ"], home=home)["askId"]
out = {"fps": [], "snap": None, "aid": aid}
def scan(t):
    NOW[0] = T0 + t
    office._invalidate_cache()
    snap = ra._redact_office_for_relay(office.office_json())
    out["snap"] = snap
    return ra._status_fingerprint(snap)
for t in (1, 61, 125, 299, 599):
    out["fps"].append(scan(t))
out["ten"] = scan(601)
print(json.dumps(out, ensure_ascii=False))
'''
        base = ROOT / "tests" / "artifacts" / ".tmp"
        base.mkdir(parents=True, exist_ok=True)
        home = Path(tempfile.mkdtemp(prefix="r127_fp_", dir=base))
        self.addCleanup(shutil.rmtree, home, True)
        env = {k: v for k, v in os.environ.items() if not k.startswith(("OFFICE_", "RELAY_"))}
        env.update(HOME=str(home), OFFICE_HOME=str(home), OFFICE_DATA=str(home),
                   OFFICE_CONFIG=str(home / "office_config.json"), OFFICE_FAKE_MAC_IDLE="0",
                   OFFICE_AGENTS_CLI="0", OFFICE_SOURCES_CODEX="0", OFFICE_LANG="ja",
                   OFFICE_OPENCLAW_FIXTURE=str(home / "missing-openclaw.json"))
        r = subprocess.run([sys.executable, "-c", script, str(ROOT), str(home)], env=env,
                           capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr[-2000:])
        got = json.loads(r.stdout.strip().splitlines()[-1])
        self.assertEqual(len(set(got["fps"])), 1, "分の境を跨いだだけで Bot の行の指紋が変わった（毎分 sync を送る）")
        self.assertNotEqual(got["ten"], got["fps"][0], "10 分たっても送り直さない（スマホの待ち時間が古いまま）")
        snap = got["snap"]
        (bot,) = [e for e in snap["employees"] if e.get("vendor") == "bot"]
        self.assertEqual((bot["session"], bot["disp"], bot["state"], bot["verb"], bot["question"]),
                         ("bot-fpbot", "見回り", "waiting", "相談の答え待ち", "送ってよい？"))
        self.assertEqual(bot["ask"], {"tool": "office_ask", "kind": "question", "remote": 0, "min": 10, "id8": got["aid"][:8]})
        blob = json.dumps(snap, ensure_ascii=False)
        for secret in (got["aid"], "受信箱を見回り中", "/Users/alice", '"door"'):
            self.assertNotIn(secret, blob, secret)

    R112H_QS = [{"q": "どの順で直しますか?", "options": ["server から", "PWA から"], "multi": 0},
                {"q": "どれを含めますか?", "options": ["hook", "server", "relay"], "multi": 1},
                {"q": "3 問目", "options": ["はい", "いいえ"], "multi": 0}]

    def test_allow_ask_table_is_pinned(self):
        """中継に載る entry.ask のキーの表（番人）。足すときはこの表・docs/office-json.md の ask 行・下のピンを同時に。
        R112-H（2026-10-07・本人の報告「1、2、3 の選択肢でもスマホから答えられない」）: questions を**意図して**足した
        ＝AskUserQuestion の全部の問いと選択肢（entry の question/questionOptions と同じ「回答のために通す」・質問のときだけ）。
        title（コマンド全文・質問文）・options・askId・ts・askCount・inputCanon は今も載らない。"""
        self.assertEqual(ra._ALLOW_ASK, frozenset({"tool", "kind", "remote", "min", "qn", "multi", "id8", "questions"}))
        for k in ("title", "options", "askId", "ts", "askCount", "inputCanon", "inputSha", "level", "tags", "cwd"):
            self.assertNotIn(k, ra._ALLOW_ASK, k)
        # 表は飾りではない: 何を入れても出てくる ask のキーは表の中だけ
        full = {"tool": "AskUserQuestion", "kind": "question", "title": "どの順で?", "options": ["x"], "askId": "f" * 32,
                "ts": 1.5, "askCount": 2, "remote": 0, "min": 3, "qn": 3, "multi": 1, "questions": self.R112H_QS,
                "inputCanon": "rm -rf /", "level": "mac", "tags": ["x"], "cwd": "/Users/someone/p", "junk": {"a": 1}}
        out = ra._redact_office_for_relay({"employees": [{"session": "a", "ask": dict(full)}]})
        self.assertLessEqual(set(out["employees"][0]["ask"]), set(ra._ALLOW_ASK))
        self.assertEqual(out["employees"][0]["ask"]["questions"], self.R112H_QS)

    # ── R127: 出勤の口の Bot の行は Bot だけの狭い allowlist で中継に載る（不変条件 8 の改定・本人裁定 2026-11-10）──
    def _r127_bot_row(self, **kw):
        row = {"session": "bot-demo", "vendor": "bot", "dept": "見回り", "disp": "見回り", "role": "受信箱",
               "state": "waiting", "kind": "said", "verb": "相談の答え待ち", "age": 5, "mtime": 1.0,
               "listening": True, "pending": True, "approvalMin": 0, "minions": 0, "avatar": 0, "skills": [],
               "cwd": "", "branch": "", "lastSaid": "", "lastOrder": "", "target": "", "feed": [], "stuckTool": "",
               "question": "請求書 3 件を送ってよい？",
               "questionOptions": [{"label": "はい", "desc": ""}, {"label": "いいえ", "desc": ""}],
               "ask": {"tool": "office_ask", "kind": "question", "title": "請求書 3 件を送ってよい？",
                       "options": ["はい", "いいえ"], "askId": "ab" * 16, "ts": 1.0, "min": 7, "remote": 0}}
        row.update(kw)
        return row

    def test_r127_door_allowlist_is_pinned(self):
        """Bot の行の表（番人）。足すときはこの表・docs/office-json.md の「Bot の行」・下のピンを同時に。
        本文とパスの鍵・feed・sessions・title・detail・work・stuckTool は Bot の行の表に無い（鍵ごと載らない）。"""
        self.assertEqual(ra._ALLOW_DOOR_ENTRY, frozenset({
            "session", "vendor", "projectId", "crew", "name", "dept", "disp", "role",
            "state", "kind", "verb", "age", "mtime", "pending", "listening", "attention", "approvalMin",
            "question", "questionOptions", "ask"}))
        self.assertLessEqual(ra._ALLOW_DOOR_ENTRY, ra._ALLOW_ENTRY, "Bot の表がふつうの表より広い")
        # R128 #4: muted（消音）は Mac の音と通知だけ＝中継の平文に載せない（スマホの Push は Worker が決める・載せても Worker は読まない）
        for k in ("cwd", "branch", "lastSaid", "lastOrder", "target", "feed", "sessions", "title", "detail", "work",
                  "stuckTool", "skills", "minions", "minionsLive", "err", "permMode", "external", "site", "aiTitle", "muted"):
            self.assertNotIn(k, ra._ALLOW_DOOR_ENTRY, k)
        self.assertEqual(ra._ALLOW_DOOR_ASK, frozenset({"tool", "kind", "remote", "min", "id8"}))

    def test_r127_bot_rows_ride_the_relay_through_the_door_allowlist_only(self):
        """Bot の行は employees・roster に載る。載るのは Bot の表の鍵だけ（Mac のセッションの本文・パスは手違いで持っていても出さない）・
        verb（Bot の doing）は縮約（パス→末尾名・URL→ホスト・秘密→伏字・60 字）・問いと選択肢はそのまま・ask は印だけ・
        counts は Mac と同じ（Bot を引かない）・sources.door は載らない・入力（office_json のキャッシュ）は書き換えない。"""
        import copy
        poison = dict(cwd="/Users/alice/secret-proj", branch="feature/amex", lastSaid="SESSION_BODY_SECRET",
                      lastOrder="ORDER_SECRET", target="/Users/alice/x.md",
                      feed=["💬 FEED_SECRET", "編集中 /Users/alice/y.md"], title="TITLE_SECRET", detail="DETAIL_SECRET",
                      work={"now": ["WORK_SECRET"]}, stuckTool="実行中 rm -rf /Users/alice/z",
                      sessions=[{"session": "bot-demo", "state": "waiting", "age": 5, "cwd": "/Users/alice/s"}],
                      tokens=9, bg={"id": "x"}, err={"k": "api", "ago": 1}, permMode="plan", minionsLive=3,
                      external={"site": "x"}, approvalMin=9, aiTitle="AI_TITLE_SECRET", muted=True)
        verb = ("作業中 /Users/alice/works/顧客台帳.csv https://internal.example.com/a?token=abc "
                "sk-proj-abcdefghijklmnop と長い説明の続き") * 2
        bot = self._r127_bot_row(verb=verb, **poison)
        claude = {"session": "s-claude-1", "vendor": "claude", "state": "working", "disp": "検証部", "verb": "編集中"}
        snap = {"employees": [claude, bot],
                "roster": [dict(claude, projectId="p1"), dict(bot, projectId="p-bot", name="見回り", crew=1, attention=True)],
                "counts": {"working": 1, "waiting": 1, "resting": 0},
                "sources": {"claude": {"fg": 1}, "door": {"ok": True, "bots": 1, "n": 1, "asks": 1}}}
        before = copy.deepcopy(snap)
        out = ra._redact_office_for_relay(snap)
        self.assertEqual(snap, before, "入力（office_json のキャッシュ）を書き換えた")
        self.assertEqual([e["session"] for e in out["employees"]], ["s-claude-1", "bot-demo"])
        self.assertEqual([r["session"] for r in out["roster"]], ["s-claude-1", "bot-demo"])
        for row in (out["employees"][1], out["roster"][1]):
            self.assertLessEqual(set(row), set(ra._ALLOW_DOOR_ENTRY))
            for k in ("cwd", "branch", "lastSaid", "lastOrder", "target", "feed", "title", "detail", "work", "stuckTool",
                      "sessions", "tokens", "bg", "err", "permMode", "minionsLive", "external", "skills", "avatar", "aiTitle"):
                self.assertNotIn(k, row, k)
            self.assertEqual((row["vendor"], row["dept"], row["disp"], row["role"], row["state"], row["kind"]),
                             ("bot", "見回り", "見回り", "受信箱", "waiting", "said"))
            self.assertEqual(row["question"], "請求書 3 件を送ってよい？")
            self.assertEqual(row["questionOptions"], [{"label": "はい", "desc": ""}, {"label": "いいえ", "desc": ""}])
            self.assertEqual(row["ask"], {"tool": "office_ask", "kind": "question", "remote": 0, "min": 7, "id8": "abababab"})
            self.assertEqual(row["approvalMin"], 0)
            self.assertLessEqual(len(row["verb"]), 60)
            self.assertTrue(row["verb"].startswith("作業中 顧客台帳.csv internal.example.com [secret]"), row["verb"])
        self.assertEqual(out["roster"][1]["projectId"], "p-bot")
        blob = json.dumps(out, ensure_ascii=False)
        for secret in ("SESSION_BODY_SECRET", "ORDER_SECRET", "FEED_SECRET", "TITLE_SECRET", "DETAIL_SECRET", "WORK_SECRET",
                       "AI_TITLE_SECRET", "/Users/alice", "feature/amex", "token=abc", "sk-proj-abcdefghijklmnop",
                       "ab" * 16, '"door"'):
            self.assertNotIn(secret, blob, secret)
        self.assertEqual(out["counts"], {"working": 1, "waiting": 1, "resting": 0}, "Bot を状態別の人数から引いた")
        self.assertEqual(set(out["sources"]), {"claude", "codex", "openclaw"})
        # 形の違う Bot の行: 許可の形の ask は載らない（スマホに許可のカードを出させない）・宛先の形が違う行は載らない
        odd = ra._redact_office_for_relay({"employees": [
            self._r127_bot_row(session="bot-perm", ask={"tool": "Bash", "kind": "permission", "remote": 1,
                                                        "askId": "cd" * 16, "min": 1, "title": "rm -rf ~"}),
            self._r127_bot_row(session="sess-not-a-bot-1"), self._r127_bot_row(session="bot-UPPER")]})
        self.assertEqual([e["session"] for e in odd["employees"]], ["bot-perm"])
        self.assertNotIn("ask", odd["employees"][0])
        self.assertNotIn("rm -rf", json.dumps(odd, ensure_ascii=False))

    def test_r127_real_door_rows_ride_with_the_question_and_the_id8(self):
        """office_door が実際に作る行（相談つき）と、それを畳んだ roster（session／project の両方）で確かめる。
        askId 全体は載らない（印の id8 だけ）・相談中は doing を出さない・相談が閉じたら doing が縮約されて載る。"""
        door = ra.office.office_door
        claude = {"session": "s-claude-1", "vendor": "claude", "state": "working", "disp": "検証部", "cwd": "/Users/x/p"}
        with tempfile.TemporaryDirectory(prefix="relay_door_") as tmp:
            home = Path(tmp)
            self.assertTrue(door.add_bot("demo", "見回り", role="受信箱", home=home)["ok"])
            self.assertTrue(door.checkin("demo", "受信箱を見回り中 /Users/alice/inbox", ttl_min=60, home=home)["ok"])
            aid = door.ask("demo", "請求書 3 件を送ってよい？", ["はい", "いいえ"], home=home)["askId"]
            rows, meta = door.door_employees(home, time.time())
            self.assertEqual(meta["asks"], 1)
            for mode in ("session", "project"):
                emps = [claude] + rows
                out = ra._redact_office_for_relay({"employees": emps, "roster": ra.office.group_by_project(emps, mode=mode),
                                                   "counts": {"working": 1, "waiting": 1, "resting": 0}})
                blob = json.dumps(out, ensure_ascii=False)
                self.assertNotIn(aid, blob, (mode, "askId 全体が載った"))
                self.assertNotIn("/Users/", blob, mode)
                self.assertNotIn("受信箱を見回り中", blob, (mode, "相談中に doing を出した"))
                for key in ("employees", "roster"):
                    (bot,) = [r for r in out[key] if r["session"] == "bot-demo"]
                    self.assertEqual((bot["disp"], bot["role"], bot["state"], bot["verb"]),
                                     ("見回り", "受信箱", "waiting", "相談の答え待ち"), (mode, key))
                    self.assertEqual(bot["question"], "請求書 3 件を送ってよい？")
                    self.assertEqual([o["label"] for o in bot["questionOptions"]], ["はい", "いいえ"])
                    self.assertEqual(bot["ask"]["id8"], aid[:8])
                    self.assertEqual((bot["ask"]["kind"], bot["ask"]["remote"]), ("question", 0))
            self.assertTrue(door.answer("demo", aid, "はい", src="local", home=home)["ok"])
            rows, _ = door.door_employees(home, time.time())
        out = ra._redact_office_for_relay({"employees": rows})
        (bot,) = out["employees"]
        self.assertEqual((bot["state"], bot["verb"], bot["question"]), ("working", "受信箱を見回り中 inbox", ""))
        self.assertNotIn("ask", bot)

    def test_r127_bot_rows_resync_at_most_every_ten_minutes(self):
        """Bot の行の age・ask.min は送信の指紋で 10 分に丸める（長く居る Bot・長く待つ相談が毎分の sync を起こさない）。
        本当の変化（相談が閉じた・状態が替わった）はすぐ指紋に出る。"""
        door = ra.office.office_door
        with tempfile.TemporaryDirectory(prefix="relay_door_fp_") as tmp:
            home = Path(tmp)
            t0 = time.time()
            self.assertTrue(door.add_bot("demo", "見回り", home=home, now=t0)["ok"])
            self.assertTrue(door.checkin("demo", "見回り中", ttl_min=240, home=home, now=t0)["ok"])
            aid = door.ask("demo", "送ってよい？", ["はい", "いいえ"], home=home, now=t0)["askId"]

            def fp(t):
                rows, _ = door.door_employees(home, t)
                snap = {"employees": rows, "roster": ra.office.group_by_project(rows, mode="session")}
                return ra._status_fingerprint(ra._redact_office_for_relay(snap))

            base = fp(t0)
            for dt in (1, 59, 61, 125, 299, 599):
                self.assertEqual(fp(t0 + dt), base, f"{dt} 秒で Bot の行の指紋が変わった（毎分 sync を送る）")
            self.assertNotEqual(fp(t0 + 600), base, "10 分たっても送り直さない（スマホの待ち時間が古いまま）")
            self.assertTrue(door.answer("demo", aid, "はい", src="local", home=home, now=t0 + 30)["ok"])
            self.assertNotEqual(fp(t0 + 31), base, "相談が閉じたのに指紋が変わらない（スマホにカードが残る）")

    # ── R127: スマホから Bot 宛（id8 つきの答え・印の無い言葉）──
    def _door_home(self):
        door = ra.office.office_door
        home = self.tmp / "door_home"
        self.assertTrue(door.add_bot("demo", "見回り", home=home)["ok"])
        patcher = mock.patch.object(ra.office, "_HOME", home)
        patcher.start()
        self.addCleanup(patcher.stop)
        return door, home

    def _answer_env(self, id8, text, session="bot-demo", **kw):
        """スマホの答えの本文（ui/pwa/app.js の JSON.stringify と同じ＝区切りに空白なし）。"""
        body = json.dumps({"aioffice": 1, "kind": "answer", "ask": id8, "text": text},
                          ensure_ascii=False, separators=(",", ":"))
        return self._sign(session=session, text=body, **kw)

    @staticmethod
    def _items(envs):
        return [{"id": i + 1, "session": e["session"], "ts": int(time.time()), "text": json.dumps(e)}
                for i, e in enumerate(envs)]

    @staticmethod
    def _door_audit(home):
        p = ra.office.office_door.audit_path(home)
        return [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines()] if p.exists() else []

    def _spy_session_paths(self):
        """Bot 宛が Claude の道（承認の掲示・指示の受信箱）へ落ちないかを見る（post_instruction は Bot 宛だけ本物へ通す）。"""
        posted, replies = [], []
        real = ra.office.post_instruction

        def post(session, text, *, src, req_id=None):
            if session.startswith("bot-"):
                return real(session, text, src=src, req_id=req_id)
            posted.append((session, text, src))
            return True, "ok"
        ra.office.post_instruction = post
        ra.office.write_approval_reply = lambda *a, **k: replies.append((a, k))
        return posted, replies

    def test_r127_phone_answer_with_the_shown_id8_reaches_the_bot_once(self):
        self._seed_device()
        door, home = self._door_home()
        posted, replies = self._spy_session_paths()
        aid = door.ask("demo", "送ってよい？", ["はい", "いいえ"], home=home)["askId"]
        env = self._answer_env(aid[:8], "選択肢「はい」でお願いします。")
        self.assertEqual(ra._process_items(self._items([env])), (1, [1]))
        items = door.inbox("demo", home=home)["items"]
        self.assertEqual([(i["kind"], i["askId"], i["text"], i["choice"]) for i in items],
                         [("answer", aid, "選択肢「はい」でお願いします。", {"index": 0, "label": "はい"})])
        self.assertEqual(door.closed_how("demo", aid, home=home), "answered")
        stored = json.loads(door.state_path("demo", home).read_text(encoding="utf-8"))["inbox"]
        self.assertEqual(stored[0]["src"], "relay")
        self.assertIn(("answer", "relay"), [(a["ev"], a.get("src")) for a in self._door_audit(home)])
        self.assertEqual((posted, replies), ([], []), "Bot への答えが Claude の道へ落ちた")
        self.assertFalse(self._inbox("bot-demo").exists())
        self.assertIn(f'{env["device_id"]}:{env["nonce"]}', ra._NONCES, "届けた封筒の nonce をコミットしていない")
        # ack を失った再取得＝リプレイで落ちる・nonce の控えを失った再配達でも口の冪等（req_id）で 2 件目は入らない
        self.assertEqual(ra._process_items(self._items([env])), (0, [1]))
        ra._NONCES.clear()
        ra._process_items(self._items([env]))
        self.assertEqual(len(door.inbox("demo", home=home)["items"]), 1, "同じ封筒の答えが二重に入った")
        self.assertNotIn(("discard", "closed"), [(a["ev"], a.get("why")) for a in self._door_audit(home)])

    def test_r127_answer_to_a_question_not_shown_is_dropped_and_audited(self):
        """id8 が開いている相談に合わない答え・閉じた相談への答えは捨てて監査（指示に落とさない）。ack と nonce は焼く。"""
        self._seed_device()
        door, home = self._door_home()
        posted, replies = self._spy_session_paths()
        aid = door.ask("demo", "送ってよい？", home=home)["askId"]
        other = "ffffffff" if aid[:8] != "ffffffff" else "eeeeeeee"
        env = self._answer_env(other, "はい")
        self.assertEqual(ra._process_items(self._items([env])), (0, [1]))
        self.assertEqual(door.inbox("demo", home=home)["items"], [], "合わない答えが届いた（指示に化けた）")
        self.assertIsNone(door.closed_how("demo", aid, home=home), "合わない答えで相談が閉じた")
        self.assertIn(("discard", "nomatch", "relay"), [(a["ev"], a.get("why"), a.get("src")) for a in self._door_audit(home)])
        self.assertIn(f'{env["device_id"]}:{env["nonce"]}', ra._NONCES)
        door.withdraw("demo", aid, home=home)                          # ほかの窓口で先に片付いた
        env2 = self._answer_env(aid[:8], "はい")                        # 閉じた相談への答え（相談はもう無い）
        self.assertEqual(ra._process_items(self._items([env2])), (0, [1]))
        self.assertEqual(door.inbox("demo", home=home)["items"], [], "閉じた相談への答えが指示として届いた")
        self.assertIn(("discard", "closed", "relay"), [(a["ev"], a.get("why"), a.get("src")) for a in self._door_audit(home)])
        self.assertEqual((posted, replies), ([], []))

    def test_r127_plain_words_are_an_instruction_only_while_no_question_is_open(self):
        """印の無い言葉: 相談が閉じていれば指示として口へ（呼び手は relay）・開いていれば口が断る（ask-open）＝答えにもしない。"""
        self._seed_device()
        door, home = self._door_home()
        posted, replies = self._spy_session_paths()
        self.assertEqual(ra._process_items(self._items([self._sign(session="bot-demo", text="棚を見て")])), (1, [1]))
        self.assertEqual([(i["kind"], i["text"]) for i in door.inbox("demo", home=home)["items"]], [("instruct", "棚を見て")])
        self.assertEqual(ra.office.load_history()[-1]["session"], "bot-demo")
        aid = door.ask("demo", "送ってよい？", ["はい", "いいえ"], home=home)["askId"]
        # 形の違う印（大文字を含む 8 桁）は印として読まない＝印の無い言葉と同じ扱い。先頭を必ず大文字の A にする
        # （aid[:8].upper() は先頭 8 桁が数字だけだと変わらず、有効な答えになって落ちた＝約 2% の回・別モデルレビュー medium）
        for text in ("はい", '{"aioffice":1,"kind":"answer","ask":"A' + aid[1:8] + '","text":"はい"}'):
            with self.subTest(text=text[:20]):
                self.assertEqual(ra._process_items(self._items([self._sign(session="bot-demo", text=text)])), (0, [1]))
                self.assertIsNone(door.closed_how("demo", aid, home=home), "印の無い言葉が相談の答えになった")
        self.assertEqual(len(door.inbox("demo", home=home)["items"]), 1)
        self.assertIn(("refuse", "instruct", "ask-open"), [(a["ev"], a.get("op"), a.get("why")) for a in self._door_audit(home)])
        self.assertEqual((posted, replies), ([], []))

    def test_r127_order_is_kept_and_a_transient_failure_is_retried(self):
        """同じ Bot の答え→指示は順に届ける。一時障害（OSError）は ack も nonce もせず、後ろの封筒も次周へ（答えより先に指示が
        着いて断られない）。"""
        self._seed_device()
        door, home = self._door_home()
        self._spy_session_paths()
        aid = door.ask("demo", "送ってよい？", home=home)["askId"]
        items = self._items([self._answer_env(aid[:8], "はい"), self._sign(session="bot-demo", text="次は棚を見て")])
        with mock.patch.object(door, "answer", side_effect=OSError("disk busy")):
            self.assertEqual(ra._process_items(items), (0, []))
        self.assertEqual(ra._NONCES, {}, "一時障害の封筒の nonce を焼いた（再配達がリプレイで落ちる）")
        self.assertEqual(door.inbox("demo", home=home)["items"], [], "答えより先に指示を処理した")
        self.assertEqual(ra._process_items(items), (1, [1, 2]))
        self.assertEqual([(i["kind"], i["text"]) for i in door.inbox("demo", home=home)["items"]],
                         [("answer", "はい"), ("instruct", "次は棚を見て")])

    def test_r127_poison_never_escapes_and_never_answers(self):
        """毒入りの文: 見えない文字・双方向の上書きは口が落としてから選択肢に解決・HTML は文字のまま・口の中の予期しない例外は
        外へ出さず（他のセッションの配達と ack を巻き込まない）その 1 通を捨てて監査・無署名と形の違う宛先は人の言葉と言えない。"""
        self._seed_device()
        door, home = self._door_home()
        posted, _ = self._spy_session_paths()
        aid = door.ask("demo", "送ってよい？", ["はい", "いいえ"], home=home)["askId"]
        with mock.patch.object(door, "answer", side_effect=TypeError("boom")):
            delivered, ack_ids = ra._process_items(self._items([self._answer_env(aid[:8], "はい"),
                                                                self._sign(text="進めて")]))
        self.assertEqual((delivered, sorted(ack_ids)), (1, [1, 2]))
        self.assertEqual(posted, [("relaytest-0001", "進めて", "relay")], "Bot の例外で他のセッションの配達が止まった")
        self.assertIn(("discard", "error"), [(a["ev"], a.get("why")) for a in self._door_audit(home)])
        self.assertIsNone(door.closed_how("demo", aid, home=home))
        poison = "選択肢「は​い‮」でお願いします。"
        self.assertEqual(ra._process_items(self._items([self._answer_env(aid[:8], poison)])), (1, [1]))
        (item,) = door.inbox("demo", home=home)["items"]
        self.assertEqual((item["text"], item["choice"]), ("選択肢「はい」でお願いします。", {"index": 0, "label": "はい"}))
        html = '<img src=x onerror="alert(1)">棚を見て'
        self.assertEqual(ra._process_items(self._items([self._sign(session="bot-demo", text=html)])), (1, [1]))
        self.assertEqual(door.inbox("demo", home=home)["items"][-1]["text"], html, "文字をそのまま渡していない")
        ra.ALLOW_UNSIGNED = True
        self.assertEqual(ra._process_items([{"id": 7, "session": "bot-demo", "text": "無署名"}]), (0, [7]))
        self.assertEqual(ra._process_items(self._items([self._sign(session="bot-Bad-Name", text="x")])), (0, [1]))
        self.assertEqual([i["text"] for i in door.inbox("demo", home=home)["items"]][-1], html, "無署名・形の違う宛先が届いた")
        whys = [(a["ev"], a.get("why")) for a in self._door_audit(home)]
        self.assertIn(("discard", "unsigned"), whys)
        self.assertIn(("discard", "bad-id"), whys)

    def test_r126_phone_instructions_name_the_relay_as_the_caller(self):
        self._seed_device()
        seen = []
        ra.office.post_instruction = lambda s, t, *, src: (seen.append(src) or (True, "ok"))
        self._mock([self._sign(text="進めて")])
        ra._process_items(ra._req("GET", "x/pull", "t")["items"])
        self.assertEqual(seen, ["relay"])

    def test_r112h_ask_questions_ride_the_relay_for_questions_only(self):
        """R112-H: AskUserQuestion の questions は office_json と同じ検証（office_server.clean_ask_questions）で中継へ写す。
        許可（Bash）・計画の承認（ExitPlanMode）には形が正しくても載せない（許可の本文を広げない）。壊れた形は何も足さない。"""
        q = {"tool": "AskUserQuestion", "kind": "question", "askId": "a" * 32, "qn": 3, "multi": 1}
        snap = {"employees": [
            {"session": "a", "ask": dict(q, questions=self.R112H_QS)},
            {"session": "b", "ask": {"tool": "Bash", "kind": "permission", "questions": self.R112H_QS}},
            {"session": "c", "ask": {"tool": "ExitPlanMode", "kind": "question", "questions": self.R112H_QS}},
            {"session": "d", "ask": dict(q, questions=[dict(self.R112H_QS[0], q="長" * 300)])},
            {"session": "e", "ask": dict(q, questions=[dict(self.R112H_QS[0], options=[{"label": "A"}])])},
            {"session": "f", "ask": dict(q, questions="文字列")},
            {"session": "g", "ask": dict(q, questions=[dict(self.R112H_QS[0], extra="/Users/someone/secret")])}],
            "roster": [{"projectId": "p", "session": "a", "ask": dict(q, questions=self.R112H_QS)}]}
        out = ra._redact_office_for_relay(snap)
        a, b, c, d, e, f, g = out["employees"]
        self.assertEqual(a["ask"], {"tool": "AskUserQuestion", "kind": "question", "remote": 0, "qn": 3, "multi": 1,
                                    "id8": "a" * 8, "questions": self.R112H_QS})
        for row in (b, c, d, e, f):
            self.assertNotIn("questions", row["ask"], row["session"])
        self.assertEqual(g["ask"]["questions"], [self.R112H_QS[0]])          # 知らないキーは写さない
        self.assertNotIn("secret", json.dumps(g, ensure_ascii=False))
        self.assertEqual(out["roster"][0]["ask"]["questions"], self.R112H_QS)

    # ── R87 封書: dlg-封筒は office_inbox に書かず daemon に封じさせ、暗号文だけを載せる ──
    def _dlg_env(self, req_id="a" * 32, session=None, kind="dialog"):
        act = {"aioffice": 1, "kind": kind, "reqId": req_id, "session": "sess-target-1", "depth": 0}
        return self._sign(session=session or f"dlg-{req_id}", text=json.dumps(act))

    def _kat(self):
        kat = json.loads((Path(__file__).parent / "fixtures/dialog_seal_kat.json")
                         .read_text(encoding="utf-8"))
        kat["bundle"]["e"] = int(time.time()) + 60     # 固定ベクタは過去日付＝期限だけ今へ（e は AAD 外）
        return kat

    def test_r87_dlg_goes_to_daemon_not_inbox(self):
        self._seed_device()
        calls = []
        ra._post_dialog = lambda payload: (calls.append(payload) or {"ok": True, "msg": "sealed"})
        posted = []
        real_post = ra.office.post_instruction
        ra.office.post_instruction = lambda s, t, *, src: (posted.append((s, t)) or (True, "ok"))
        try:
            env = self._dlg_env()
            items = [{"id": 21, "session": env["session"], "ts": int(time.time()),
                      "text": json.dumps(env)}]
            delivered, ack_ids = ra._process_items(items)
        finally:
            ra.office.post_instruction = real_post
        self.assertEqual(posted, [], "dlg- が office_inbox へ書かれた（孤児inbox）")
        self.assertEqual((delivered, ack_ids), (1, [21]))
        self.assertEqual(calls[0]["action"]["kind"], "dialog")
        self.assertEqual(calls[0]["device_id"], DID)            # 検証済みの端末IDを daemon へ渡す
        self.assertFalse(self._inbox(env["session"]).exists())

    def test_r100_term_goes_to_daemon_and_unknown_kinds_are_refused(self):
        """R100-1a: kind:"term"（ターミナル画面）は dialog と同じ経路で daemon へ・閉じた集合の外（bogus）は恒久拒否。"""
        import office_server
        self.assertEqual(ra.DLG_KINDS, office_server.SEALED_KINDS, "relay_agent と daemon の封書の種類がずれた")
        self._seed_device()
        calls = []
        ra._post_dialog = lambda payload: (calls.append(payload) or {"ok": True, "msg": "sealed"})
        env = self._dlg_env(req_id="c" * 32, kind="term")
        bad = self._dlg_env(req_id="d" * 32, kind="bogus")
        items = [{"id": 31, "session": env["session"], "ts": int(time.time()), "text": json.dumps(env)},
                 {"id": 32, "session": bad["session"], "ts": int(time.time()), "text": json.dumps(bad)}]
        delivered, ack_ids = ra._process_items(items)
        self.assertEqual([c["action"]["kind"] for c in calls], ["term"], "bogus を daemon へ回した")
        self.assertEqual(sorted(ack_ids), [31, 32])                              # bogus は恒久＝ack して捨てる

    def test_r87_dlg_not_acked_when_daemon_down(self):
        self._seed_device()

        def boom(_payload):
            raise OSError("daemon down")
        ra._post_dialog = boom
        env = self._dlg_env(req_id="b" * 32)
        items = [{"id": 22, "session": env["session"], "ts": int(time.time()),
                  "text": json.dumps(env)}]
        self.assertEqual(ra._process_items(items), (0, []))
        self.assertNotIn(f'{env["device_id"]}:{env["nonce"]}', ra._NONCES)

    def test_r87_dlg_rejected_by_daemon_is_acked(self):
        import urllib.error
        self._seed_device()

        def refuse(_payload):
            raise urllib.error.HTTPError("http://x", 400, "bad", {}, None)
        ra._post_dialog = refuse
        env = self._dlg_env(req_id="c" * 32)
        items = [{"id": 23, "session": env["session"], "ts": int(time.time()),
                  "text": json.dumps(env)}]
        self.assertEqual(ra._process_items(items), (1, [23]))

    def test_r87_session_and_reqid_must_agree(self):
        """封筒の session（署名対象）と本文の reqId が食い違う＝転用。ack して捨てる・daemon へ回さない。"""
        self._seed_device()
        calls = []
        ra._post_dialog = lambda payload: (calls.append(payload) or {"ok": True})
        env = self._dlg_env(req_id="d" * 32, session="dlg-" + "e" * 32)
        items = [{"id": 24, "session": env["session"], "ts": int(time.time()),
                  "text": json.dumps(env)}]
        self.assertEqual(ra._process_items(items), (1, [24]))
        self.assertEqual(calls, [])

    def test_r87_assert_opaque_accepts_kat_and_refuses_plaintext(self):
        kat = self._kat()
        ok = ra._assert_opaque(kat["bundle"])
        self.assertEqual(set(ok), {"v", "id", "s", "i", "e", "n", "c", "b"})
        for label, bad in (
                ("平文の混入", {**kat["bundle"], "b": "こんにちは"}),
                ("余分なキー", {**kat["bundle"], "messages": [{"text": "x"}]}),
                ("不正な err", {"v": 1, "id": "f" * 32, "e": int(time.time()) + 60, "err": "oops"}),
                ("err と本文の同居", {**kat["bundle"], "err": "denied"}),
                ("長さ違い", {**kat["bundle"], "b": kat["bundle"]["b"][:-8]}),
                ("フレーム数詐称", {**kat["bundle"], "n": 2}),
                ("圧縮方式", {**kat["bundle"], "c": "gz"}),
                ("期限が無限", {**kat["bundle"], "e": 10 ** 12})):
            with self.assertRaises(ValueError, msg=label):
                ra._assert_opaque(bad)
        good_err = ra._assert_opaque({"v": 1, "id": "f" * 32, "e": int(time.time()) + 60, "err": "denied"})
        self.assertEqual(set(good_err), {"v", "id", "e", "err"})

    def test_r87_dialog_payload_drops_oldest_bundles_beyond_relay_limit(self):
        """別モデルレビュー（2026-09-14）: 有効な封書が複数あると中継の上限を超え、Worker が黙って捨てたうえ
        指紋は確定＝再送も無かった。relay_agent 側で**新しい順に上限内へ切り詰め**、落とした分は標準出力に残す。"""
        kat = self._kat()
        ds = ra.dialog_seal
        frames = ds.MAX_FRAMES
        raw = ds.MAGIC + b"\0" * (frames * ds.FRAME_LEN - len(ds.MAGIC))
        blob = base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")
        rows = []
        for k in range(5):
            rows.append({"v": 1, "id": f"{k:032x}", "s": kat["bundle"]["s"], "i": 1000 + k,
                         "e": int(time.time()) + 60, "n": frames, "c": kat["bundle"]["c"], "b": blob})
        saved = ds.live_bundles
        ds.live_bundles = lambda: list(rows)
        try:
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                text, fp = ra._dialog_payload({})
        finally:
            ds.live_bundles = saved
        self.assertIsNotNone(text)
        self.assertLessEqual(len(text), ra.DLG_MAX_CHARS)
        ids = [i["id"] for i in json.loads(text)["items"]]
        self.assertEqual(ids, [f"{k:032x}" for k in (4, 3, 2, 1)])       # 新しい順・MAX_LIVE(4) は全部入り最古だけ落ちる
        self.assertEqual(buf.getvalue().count("封書を落とす"), 1)
        self.assertEqual(len(fp), 16)

    def test_r87_sync_carries_dlg_once_then_empty_once_and_never_plaintext(self):
        """封書は指紋が変わった周だけ載る。期限切れになったら**空を1回**送って中継の行を消す。
        送信 body の全文に fixture の平文トークンが1つも無いこと（不透明の証明）。"""
        kat = self._kat()
        ds = ra.dialog_seal
        saved = (ds.BUNDLES_FILE,)
        ds.BUNDLES_FILE = self.tmp / "office_dialog_bundles.json"
        ds._FILE_CACHE.update(key=None, rows=[]); ds._RESERVED.clear()
        ra.office.office_json = lambda: {"employees": [], "roster": []}
        try:
            state = {"acks": [], "fp": None, "pushed_at": 0.0, "attn": False}
            b1, *_ = ra._sync_request(state)
            self.assertNotIn("dlg", b1)                            # 一度も使っていない＝載せない
            ds.store_bundle(kat["bundle"]["id"], kat["bundle"])
            ds._FILE_CACHE.update(key=None, rows=[])
            b2, snap, fp, send, now = ra._sync_request(state)
            self.assertIn("dlg", b2)
            payload = json.loads(b2["dlg"])
            self.assertEqual([i["id"] for i in payload["items"]], [kat["bundle"]["id"]])
            wire = json.dumps(b2, ensure_ascii=False)
            for leak in (kat["page"]["messages"][0]["text"], kat["targetSession"], "messages", "text"):
                self.assertNotIn(leak, wire, leak)
            ra._sync_apply({"ok": True, "items": []}, state, snap, fp, send, now, "http://x", "t")
            b3, *_ = ra._sync_request(state)
            self.assertNotIn("dlg", b3)                            # 変化なし＝再送しない
            # 期限切れ → 空を1回
            rows = json.loads(ds.BUNDLES_FILE.read_text())
            rows["bundles"][0]["e"] = int(time.time()) - 1
            ds.BUNDLES_FILE.write_text(json.dumps(rows))
            ds._FILE_CACHE.update(key=None, rows=[])
            b4, snap, fp, send, now = ra._sync_request(state)
            self.assertEqual(json.loads(b4["dlg"]), {"v": 1, "items": []})
            ra._sync_apply({"ok": True, "items": []}, state, snap, fp, send, now, "http://x", "t")
            b5, *_ = ra._sync_request(state)
            self.assertNotIn("dlg", b5)                            # 空も1回だけ
        finally:
            ds.BUNDLES_FILE = saved[0]
            ds._FILE_CACHE.update(key=None, rows=[]); ds._RESERVED.clear()

    def test_r87_lane_holds_non_empty_payload_while_slowed_down(self):
        kat = self._kat()
        ds = ra.dialog_seal
        saved = (ds.BUNDLES_FILE,)
        ds.BUNDLES_FILE = self.tmp / "office_dialog_bundles.json"
        ds._FILE_CACHE.update(key=None, rows=[]); ds._RESERVED.clear()
        ra.office.office_json = lambda: {"employees": [], "roster": []}
        try:
            ds.store_bundle(kat["bundle"]["id"], kat["bundle"])
            ds._FILE_CACHE.update(key=None, rows=[])
            state = {"acks": [], "fp": None, "pushed_at": 0.0, "attn": False, "usage": {"level": 1}}
            body, *_ = ra._sync_request(state)
            self.assertNotIn("dlg", body)                          # R80 の減速に従って保留
            state["usage"] = {"level": 0}
            body, *_ = ra._sync_request(state)
            self.assertIn("dlg", body)
        finally:
            ds.BUNDLES_FILE = saved[0]
            ds._FILE_CACHE.update(key=None, rows=[]); ds._RESERVED.clear()

    def test_lane_sends_the_approval_bundle_even_while_slowed_down(self):
        """中継の使用量が 50% を超える（level 1）と許可の全文の封書まで保留され、「スマホで確認して許可」が
        UTC 0 時まで「届きませんでした」になっていた。急ぎの印がある封書を含むときは全体を送る。"""
        kat = self._kat()
        ds = ra.dialog_seal
        saved = (ds.BUNDLES_FILE,)
        ds.BUNDLES_FILE = self.tmp / "office_dialog_bundles.json"
        ds._FILE_CACHE.update(key=None, rows=[]); ds._RESERVED.clear()
        ra.office.office_json = lambda: {"employees": [], "roster": []}
        try:
            other = dict(kat["bundle"], id="f" * 32)
            ds.store_bundle(other["id"], other)
            ds.store_bundle(kat["bundle"]["id"], kat["bundle"], replace_key="ask:dev", urgent=True)
            ds._FILE_CACHE.update(key=None, rows=[])
            state = {"acks": [], "fp": None, "pushed_at": 0.0, "attn": False, "usage": {"level": 2}}
            body, *_ = ra._sync_request(state)
            self.assertIn("dlg", body)
            ids = sorted(b["id"] for b in json.loads(body["dlg"])["items"])
            self.assertEqual(ids, sorted([kat["bundle"]["id"], "f" * 32]))   # 送り済みの封書を消さない＝全体
        finally:
            ds.BUNDLES_FILE = saved[0]
            ds._FILE_CACHE.update(key=None, rows=[]); ds._RESERVED.clear()

    def test_r7910_redaction_keeps_actions_but_drops_paths(self):
        """office_json に増えた actions は中継へ通るが、cwd等のパスは従来どおり落ちる。"""
        snap = {"employees": [{"session": "s", "cwd": "/Users/me/secret", "lastSaid": "本文"}],
                "roster": [], "actions": {"recipes": [{"id": "r1", "label": "検証"}],
                                          "results": [], "caps": {"actions": 1}}}
        out = ra._redact_office_for_relay(json.loads(json.dumps(snap)))
        self.assertEqual(out["employees"][0]["cwd"], "")
        self.assertEqual(out["employees"][0]["lastSaid"], "")
        self.assertEqual(out["actions"]["recipes"][0]["id"], "r1")

    # ── R80: 通信の安全弁（Cloudflare無料枠を割る前に自分で減速する） ──
    def test_r80_cross_do_throttle(self):
        """Cross-DO requests remain limited to once per 60 seconds."""
        state = {}
        self.assertTrue(ra._want_openclaw(state, 1000.0))       # 初回は取りに行く
        state["oc_fetched_at"] = 1000.0
        self.assertFalse(ra._want_openclaw(state, 1030.0))      # 30秒後はまだ
        self.assertTrue(ra._want_openclaw(state, 1061.0))       # 60秒超で再取得

    def test_r80_scan_slows_down_as_usage_rises(self):
        """使用量レベルが上がるほど scan 間隔が伸びる（純関数・決定論）。"""
        self.assertEqual(ra._scan_interval(True, 0), ra.SCAN_FAST)
        self.assertEqual(ra._scan_interval(True, 1), ra.SCAN_FAST * 2)
        self.assertEqual(ra._scan_interval(True, 2), ra.SCAN_FAST * 6)
        self.assertEqual(ra._scan_interval(False, 2), ra.SCAN_SLOW * 6)
        # 不正値でも減速側に倒れない（既定倍率1.0）
        self.assertEqual(ra._scan_interval(False, "x"), ra.SCAN_SLOW)

    def test_r80_usage_is_forwarded_to_ui(self):
        """C7: 中継が返した usage が office 側（UIの観測点）へ渡る。"""
        self._seed_device()
        got = {}
        ra.office.set_relay_usage = lambda u: got.update(u) or True
        ra.office.office_json = lambda: {"roster": [], "employees": []}
        state = {"acks": [], "fp": None, "pushed_at": 0.0}
        d = {"ok": True, "items": [], "usage": {"rows": 51234, "limit": 100000,
                                                "pct": 51, "level": 1}}
        ra._sync_apply(d, state, {"roster": [], "employees": []}, "fp", False, 0.0,
                       "http://x", "t")
        self.assertEqual(got.get("level"), 1)
        self.assertEqual(state["usage"]["pct"], 51)

    def test_r798_ws_pacing_and_url(self):
        """R79-8: ローカルscan間隔（在席2s↔無人30s）とWS URL導出の純関数ピン。"""
        self.assertEqual(ra._scan_interval(True), ra.SCAN_FAST)
        self.assertEqual(ra._scan_interval(False), ra.SCAN_SLOW)
        self.assertEqual(ra._ws_url("https://r.example"), "wss://r.example/ws?role=agent")
        self.assertEqual(ra._ws_url("http://127.0.0.1:8789"),
                         "ws://127.0.0.1:8789/ws?role=agent")

    def test_sync_tick_heartbeat_pushes_even_without_change(self):
        self._seed_device()
        fixed = {"roster": [], "employees": []}
        ra.office.office_json = lambda: json.loads(json.dumps(fixed))
        calls, _ = self._mock_sync([])
        state = {"acks": [], "fp": None, "pushed_at": 0.0}
        ra.sync_tick("http://x", "t", state)
        state["pushed_at"] = ra.time.time() - ra.PUSH_HEARTBEAT - 1   # ハートビート経過を模擬
        ra.sync_tick("http://x", "t", state)
        self.assertIsNotNone(calls["sync"][1]["office"])

    def test_burst_is_edge_triggered_not_level(self):
        """R79: ❗が残り続けても burst を再武装しない（恒久8秒周期＝無料枠食い潰しの根絶）。

        旧実装は state["attn"] が「❗が存在する」レベル値だったため、承認まちを1件
        放置しただけで毎tick再武装され、60秒運用のつもりが 10,800 req/日になっていた。
        """
        self._seed_device()
        attn = {"roster": [{"projectId": "p1", "session": "s1", "question": "どっち?"}],
                "employees": []}
        ra.office.office_json = lambda: json.loads(json.dumps(attn))
        self._mock_sync([])
        state = {"acks": [], "fp": None, "pushed_at": 0.0}

        ra.sync_tick("http://x", "t", state)
        self.assertTrue(state["attn"], "❗が新しく出た周は burst を張る")
        self.assertEqual(state["attn_keys"], {"p1"})

        ra.sync_tick("http://x", "t", state)
        self.assertFalse(state["attn"], "同じ❗が残っているだけでは再武装しない")

        # 別の相手に❗が出たらまた張る（エッジであることの確認）
        attn["roster"].append({"projectId": "p2", "session": "s2", "approvalMin": 3})
        ra.sync_tick("http://x", "t", state)
        self.assertTrue(state["attn"], "新しい相手の❗はエッジ＝burstを張る")
        self.assertEqual(state["attn_keys"], {"p1", "p2"})

    def test_save_openclaw_contract_unwraps_sync_shape(self):
        """/sync の openclaw は getStatus と同形 {json, ts} ＝ unwrapして契約v1のみ保存。"""
        home = self.tmp / "home"
        (home / ".claude").mkdir(parents=True)
        orig = ra.office._HOME
        ra.office._HOME = home
        try:
            contract = {"v": 1, "site": "macmini", "agents": []}
            self.assertTrue(ra._save_openclaw_contract(
                {"json": json.dumps(contract), "ts": 123}))
            saved = json.loads(
                (home / ".claude" / "openclaw_status.json").read_text(encoding="utf-8"))
            self.assertEqual(saved["site"], "macmini")
            self.assertFalse(ra._save_openclaw_contract({"json": ""}))
            self.assertFalse(ra._save_openclaw_contract({"json": json.dumps({"v": 2})}))
        finally:
            ra.office._HOME = orig

    def test_sync_unsupported_raises_for_legacy_fallback(self):
        ra.office.office_json = lambda: {"roster": [], "employees": []}

        def fake(method, url, token, body=None):
            raise urllib.error.HTTPError(url, 404, "not found", {}, None)
        ra._req = fake
        with self.assertRaises(ra.SyncUnsupported):
            ra.sync_tick("http://x", "t", {"acks": [], "fp": None, "pushed_at": 0.0})

    def test_pull_openclaw_status_saves_contract_only(self):
        """R42.4: 契約v1だけを保存する（別siteのoffice snapshot等の毒は書かない・原子置換）。"""
        home = self.tmp / "home"
        (home / ".claude").mkdir(parents=True)
        orig_home = ra.office._HOME
        ra.office._HOME = home
        try:
            contract = {"v": 1, "site": "macmini", "generatedAt": 123.0,
                        "agents": [{"id": "main", "name": "OpenClaw", "state": "working",
                                    "verb": "x", "age": 1}]}
            ra._req = lambda m, u, t, b=None: {"ok": True, "json": json.dumps(contract)}
            self.assertTrue(ra.pull_openclaw_status("http://x", "t"))
            saved = json.loads((home / ".claude" / "openclaw_status.json").read_text())
            self.assertEqual(saved["site"], "macmini")
            # 毒: 契約でないJSON（officeスナップショット様）は書かない
            ra._req = lambda m, u, t, b=None: {"ok": True,
                                               "json": json.dumps({"employees": []})}
            self.assertFalse(ra.pull_openclaw_status("http://x", "t"))
            saved2 = json.loads((home / ".claude" / "openclaw_status.json").read_text())
            self.assertEqual(saved2["v"], 1, "毒で上書きされた")
            # 空応答・壊れJSONも書かない
            ra._req = lambda m, u, t, b=None: {"ok": True, "json": ""}
            self.assertFalse(ra.pull_openclaw_status("http://x", "t"))
            ra._req = lambda m, u, t, b=None: {"ok": True, "json": "{broken"}
            self.assertFalse(ra.pull_openclaw_status("http://x", "t"))
        finally:
            ra.office._HOME = orig_home

    def test_tick_isolates_openclaw_pull_failure(self):
        """R42.4: mini集約の失敗が配達・status pushを巻き込まない。"""
        calls = []

        def fake(method, url, token, body=None):
            calls.append(url)
            if "site=" in url:
                raise ra.urllib.error.URLError("mini down")
            if url.endswith("/pull"):
                return {"ok": True, "items": []}
            return {"ok": True, "json": ""}
        ra._req = fake
        ra.office.office_json = lambda: {"employees": []}
        n = ra.tick("http://x", "t")
        self.assertEqual(n, 0)
        self.assertTrue(any("site=" in u for u in calls), "mini aggregation was not called")

    def test_license_gate_removed(self):
        """R85-2: ライセンスゲートは撤去済み＝relay_agent に paywall 経路が存在しないことをピン
        （復活させると R84 全機能無料化と矛盾する）。"""
        self.assertFalse(hasattr(ra, "_license_gate_ok"))
        self.assertFalse(hasattr(ra.office, "license_state"))

    def test_redact_sanitizes_work_without_releasing_existing_redactions(self):
        long_item = "/Users/private/project/" + ("x" * 90) + ".md を確認"
        snap = {"employees": [{
            "session": "s-work", "lastSaid": "秘密の報告", "target": "秘密の対象",
            "lastOrder": "秘密の指示", "cwd": "/Users/private/project", "branch": "secret",
            "feed": ["💬 秘密の発言", "実行中 build"],
            "work": {
                "now": ["/Users/x/a/run.py を修正", "~/private/plan.md を確認"],
                "next": [long_item, "次の1", "次の2", "次の3"],
                "done": ["完了1", "完了2", "完了3", "完了4"],
                "counts": {"pending": 4, "in_progress": 2, "completed": 4},
            },
        }]}
        out = ra._redact_office_for_relay(snap)
        e = out["employees"][0]
        self.assertEqual(e["work"]["now"], ["run.py を修正", "plan.md を確認"])
        items = e["work"]["now"] + e["work"]["next"] + e["work"]["done"]
        self.assertEqual(len(items), 8)
        self.assertLessEqual(max(map(len, items)), 60)
        self.assertEqual(e["work"]["counts"], {"pending": 4, "in_progress": 2, "completed": 4})
        for field in ("lastSaid", "target", "lastOrder", "cwd", "branch"):
            self.assertEqual(e[field], "")
        self.assertEqual(e["feed"], ["実行中 build"])

    def test_push_status_redacts_before_send(self):
        """push_status が中継へ送る body に本文が含まれない（Cloudflareに乗らない）。"""
        ra.office.office_json = lambda: {"employees": [{
            "disp": "x", "session": "s", "state": "waiting", "verb": "指示待ち",
            "lastSaid": "秘密の本文", "target": "秘密の本文2",
            "feed": ["💬 秘密の発言", "実行中 build"],
        }]}
        sent = {}
        ra._req = lambda method, url, token, body=None: sent.update(body=body) or {"ok": True}
        ra.push_status("http://x", "t")
        e = sent["body"]["office"]["employees"][0]
        self.assertEqual(e["lastSaid"], "")
        self.assertEqual(e["target"], "")
        self.assertEqual(e["feed"], ["実行中 build"])
        blob = json.dumps(sent["body"], ensure_ascii=False)
        self.assertNotIn("秘密の本文", blob)
        self.assertNotIn("秘密の発言", blob)


class AllowlistRedactionTest(unittest.TestCase):
    """R90-D12: relay redaction は allowlist。office_json に足した新フィールドは既定で中継に載らない。"""

    def test_stuck_tool_is_scrubbed_like_feed(self):
        """R99-P4-0: stuckTool は server が `"{verb} {target}"` で作る（承認まちのときだけ）。
        target は本文扱いで空にしているのに、同じ値が stuckTool の中に残って Cloudflare とスマホへ
        出ていた（feed で S3 が塞いだ「target と同じ値が別のフィールドに入る」穴と同じ型）。
        feed と同じ縮約（パスは末尾名・URL はホストだけ・60 字）を掛ける。⚠ は truthy しか見ないので絵は変わらない。"""
        snap = {"employees": [
            {"session": "s1", "approvalMin": 3,
             "stuckTool": "実行中 cd /Users/alice/Documents/AmexTax-2026 && rm -rf build && ./deploy.sh --token sk-live-abcdefghijklmnop"},
            {"session": "s2", "approvalMin": 1,
             "stuckTool": "リサーチ中 https://internal.example.com/customers/acme?token=secret"},
            {"session": "s3", "approvalMin": 0, "stuckTool": ""}],
            "roster": [{"session": "s1", "projectId": "p", "approvalMin": 3,
                        "stuckTool": "編集中 ~/work/nda/顧客名リスト.md"}]}
        out = ra._redact_office_for_relay(snap)
        vals = [e["stuckTool"] for e in out["employees"]] + [out["roster"][0]["stuckTool"]]
        joined = " / ".join(vals)
        # 末尾名（AmexTax-2026・顧客名リスト.md）は feed と同じく残る＝方針どおり（何をしているかは伝える）
        for leak in ("/Users/alice", "Documents", "internal.example.com/customers",
                     "token=secret", "~/work/nda", "sk-live-abcdefghijklmnop"):
            self.assertNotIn(leak, joined)
        for v in vals:
            self.assertLessEqual(len(v), 60)
        self.assertTrue(vals[0].startswith("実行中"))    # 何をしているか（動詞）は残る＝⚠ の材料は消えない
        self.assertTrue(vals[1])
        self.assertEqual(vals[2], "")                    # 空は空のまま（⚠ を捏造しない）

    def test_detail_is_path_scrubbed_like_feed(self):
        """R90 裁定で detail は中継に載せるが、載せてよいのは「何をしているか」だけ。

        detail は Haiku が書く自由文で、実測で絶対パスや URL がそのまま入る。
        feed は _scrub_feed_line を通っていたのに detail だけ素通りしていて、
        Cloudflare とスマホへ実名パスが出ていた（2026-09-08 の監査で発覚）。
        """
        office = {"employees": [{"session": "s1",
                                 "detail": "/Users/someone/work/proj/server/app.py を直した"},
                                {"session": "s2",
                                 "detail": "https://internal.example.com/x?token=abc を確認"}],
                  "roster": [], "history": []}
        out = ra._redact_office_for_relay(office)
        first, second = out["employees"][0]["detail"], out["employees"][1]["detail"]
        self.assertNotIn("/Users/", first)
        self.assertIn("app.py", first)                 # 何をしているかは残る
        self.assertNotIn("token=abc", second)
        self.assertNotIn("https://", second)
        self.assertIn("internal.example.com", second)  # ホストまでは残す（feed と同じ規則）

        # ★長さ制限は用途ごとに違う（2026-09-08 に一度踏んだ）。縮約関数を feed から流用した結果、
        # 「一行要約は200字」という R90 の裁定が黙って60字へ縮んでいた。両方を同時に固定する。
        long_text = "verify を回して指摘を直し、golden を撮り直してから公開リポへ反映しています。" * 3
        out2 = ra._redact_office_for_relay(
            {"employees": [{"session": "s3", "detail": long_text, "feed": [long_text]}],
             "roster": [], "history": []})
        entry = out2["employees"][0]
        self.assertGreater(len(entry["detail"]), 60)        # feed の 60 字で切られていない
        self.assertLessEqual(len(entry["detail"]), 200)     # 裁定どおり 200 字
        self.assertLessEqual(len(entry["feed"][0]), 60)     # feed は従来どおり 60 字

    def test_secrets_are_masked_in_detail_and_feed(self):
        """本人指摘(2026-09-09): works の `.env` は API キーを取得してくるリスト。

        detail は Haiku が自由に書くので、パスだけでなく**キーの値そのもの**を書きうる。
        実測でパス縮約だけでは `AWS_SECRET_ACCESS_KEY=AKIA…` が素通りしていた。
        office_actions.scrub_output と同じ伏字定義を共有する（二重管理しない）。
        """
        leaks = ["AWS_SECRET_ACCESS_KEY=AKIAIOSFODNN7EXAMPLE を読んだ",
                 "sk-proj-abcdefghijklmnop で認証した",
                 "Authorization: Bearer eyJhbGciOiJIUzI1NiJ9.eyJhIjoxfQ.sig",
                 "token " + "0123456789abcdef" * 4]
        for text in leaks:
            with self.subTest(text=text[:24]):
                out = ra._redact_office_for_relay(
                    {"employees": [{"session": "s", "detail": text, "feed": [text]}],
                     "roster": [], "history": []})
                entry = out["employees"][0]
                for value in (entry["detail"], entry["feed"][0]):
                    self.assertIn("[secret]", value)
                    for secret in ("AKIAIOSFODNN7EXAMPLE", "sk-proj-abcdefghijklmnop",
                                   "eyJhbGciOiJIUzI1NiJ9", "0123456789abcdef" * 4):
                        self.assertNotIn(secret, value)

    def test_unknown_and_local_only_keys_are_dropped(self):
        snap = {"employees": [{"session": "s1", "state": "working", "bg": {"detail": "x", "fan": {}},
                               "pid": 123, "tokens": 999, "homeCwd": "/Users/x/p", "zzz": 1, "detail": "verify 中"}],
                "roster": [{"projectId": "abc", "session": "s1", "bg": {"id": "j"}, "detail": "verify 中",
                            "sessions": [{"session": "s1", "state": "working", "pid": 5, "cwd": "/x", "vendor": "claude"}]}],
                "timeline": [1, 2], "history": [{"text": "秘密"}], "growth": {"byProject": {"abc": {"xp": 10, "level": 0, "breakdown": {"tasks": 1}}},
                                                                             "office": {"xp": 10, "level": 0, "nextAt": 100, "secret": 1}, "streak15": 2},
                "sources": {"claude": {"fg": 1, "bg": 1, "agentsCli": True, "paths": ["/x"]}, "codex": {"connected": True, "n": 2, "reason": "ok"},
                            "openclaw": {"connected": False, "site": "macmini"},
                            "door": {"ok": True, "bots": 2, "n": 1, "asks": 1}},   # R126: 口の数は中継に載せない（下の完全一致）
                "events": {"seq": 5, "wired": True, "lastTs": 1.0, "files": ["/x"]}}
        orig = ra.RELAY_DETAIL
        ra.RELAY_DETAIL = True                      # 環境変数 OFFICE_RELAY_DETAIL=0 の環境でも「既定=通す」を検査（レビュー指摘）
        try:
            out = ra._redact_office_for_relay(snap)
        finally:
            ra.RELAY_DETAIL = orig
        e = out["employees"][0]
        for k in ("bg", "pid", "tokens", "homeCwd", "zzz"):
            self.assertNotIn(k, e, k)
        self.assertEqual(e["detail"], "verify 中")
        self.assertNotIn("timeline", out)
        self.assertEqual(out["history"], [])
        self.assertEqual(out["roster"][0]["sessions"], [{"session": "s1", "state": "working", "vendor": "claude"}])
        self.assertNotIn("bg", out["roster"][0])
        self.assertEqual(out["growth"], {"byProject": {"abc": {"xp": 10, "level": 0}},
                                         "office": {"xp": 10, "level": 0, "nextAt": 100}, "streak15": 2,
                                         "maxSeen": 0})
        self.assertEqual(out["sources"], {"claude": {"fg": 1, "bg": 1, "agentsCli": True},
                                          "codex": {"connected": True, "n": 2}, "openclaw": {"connected": False}})
        self.assertEqual(out["events"], {"seq": 5, "wired": True})
        self.assertNotIn("/", json.dumps(out, ensure_ascii=False))

    def test_growth_carries_crew_peak(self):
        """R97: オフィスの広さ（机の数）を決める maxSeen はスマホにも要る（別ブラウザだから）。
        載るのは整数ひとつで、誰が・何をしたかは載らない。"""
        out = ra._redact_office_for_relay({"growth": {
            "byProject": {"p1": {"xp": 3, "level": 1, "secret": "x"}},
            "office": {"xp": 3, "level": 1, "nextAt": 10},
            "streak15": 2, "maxSeen": 9,
        }})
        self.assertEqual(out["growth"]["maxSeen"], 9)
        self.assertNotIn("secret", json.dumps(out, ensure_ascii=False))

    def test_app_version_is_not_relayed(self):
        """R97-D の決まり（R99-P4-7 の統括の裁定で再確認）: 版（office_json の app）は中継に載せない。スマホの設定に出す版は
        PWA 自身の版だけ。allowlist に足すと中継に載る面が増える（新フィールドは既定で非搬送の不変条件・test_status_board と同じ約束）。"""
        out = ra._redact_office_for_relay({"v": 2, "app": {"version": "2.1.0", "path": "/Users/alice/ai-office"}})
        self.assertNotIn("app", out)
        self.assertNotIn("app", ra._ALLOW_TOP)

    def test_ai_title_stays_on_mac(self):
        """R123-A: 自動タイトル（会話から作られた文）は Mac の画面だけ＝中継には載らない。"""
        out = ra._redact_office_for_relay({"employees": [{"session": "s", "title": "決済", "aiTitle": "請求書の住所の直し"}],
                                           "roster": [{"session": "s", "aiTitle": "請求書の住所の直し", "sessions": []}]})
        self.assertNotIn("aiTitle", out["employees"][0])
        self.assertNotIn("aiTitle", out["roster"][0])
        self.assertIn("aiTitle", ra._LOCAL_ONLY_ENTRY)

    def test_detail_optout(self):
        orig = ra.RELAY_DETAIL
        try:
            ra.RELAY_DETAIL = False
            out = ra._redact_office_for_relay({"employees": [{"session": "s", "detail": "x"}]})
            self.assertEqual(out["employees"][0]["detail"], "")
        finally:
            ra.RELAY_DETAIL = orig

    def test_allowlist_covers_ui_and_pwa_field_reads(self):
        """ui/core/world.js（p.xxx）と relay/src/worker.js の APP_HTML（e.xxx）が読む社員フィールドを
        allowlist が包含する＝表示側が読むのに中継で落ちている、という食い違いを機械で止める。"""
        import re
        ignore = {"appendChild", "get", "length", "push", "map", "filter", "forEach", "classList", "style",
                  "dataset", "textContent", "innerHTML", "hidden", "id", "className", "slice", "join",
                  "split", "trim", "replace", "includes", "indexOf", "toLowerCase", "sort", "some", "find",
                  "keys", "entries", "values", "children", "closest", "setAttribute", "remove", "focus",
                  "then", "catch", "json", "ok", "status", "message", "type",
                  # DOM/イベントオブジェクトの e.xxx（社員ではない）
                  "data", "g", "notification", "waitUntil", "preventDefault", "stopPropagation", "key", "code",
                  "touches", "changedTouches", "clientX", "clientY", "currentTarget", "timeStamp", "button",
                  "deltaY", "deltaX", "scale", "pageX", "pageY", "error", "reason", "respondWith", "request"}
        world = (ROOT / "ui" / "core" / "world.js").read_text(encoding="utf-8")
        worker = (ROOT / "relay" / "src" / "worker.js").read_text(encoding="utf-8")
        # B1（別モデルレビュー）: PWA の本文は relay/src/app_html.js へ移った＝原本の ui/pwa/app.js も見る
        # （minionsLive 等を PWA が読むのに中継で落ちていても緑になっていた）。部屋の起動の理由（ROOM_ERR）の k/m/f/l/c は社員ではない
        pwa = (ROOT / "ui" / "pwa" / "app.js").read_text(encoding="utf-8")
        ignore |= {"k", "m", "f", "l", "c"}
        reads = (set(re.findall(r"\bp\.([a-zA-Z]+)\b", world)) | set(re.findall(r"\be\.([a-zA-Z]+)\b", worker))
                 | set(re.findall(r"\be\.([a-zA-Z]+)\b", pwa)))
        reads = {r for r in reads if r not in ignore and r[0].islower()}
        # R98: ローカル専用と宣言したフィールド（_LOCAL_ONLY_ENTRY）は allowlist に無くてよい（宣言は必須）
        self.assertFalse(set(ra._LOCAL_ONLY_ENTRY) & set(ra._ALLOW_ENTRY), "ローカル専用と allowlist の両方にある")
        missing = sorted(reads - set(ra._ALLOW_ENTRY) - set(ra._LOCAL_ONLY_ENTRY))
        self.assertEqual(missing, [], f"UI/PWA が読むのに allowlist に無い: {missing}")


if __name__ == "__main__":
    unittest.main()
