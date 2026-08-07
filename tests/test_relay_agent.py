# -*- coding: utf-8 -*-
"""relay_agent（中継エージェント）の単体テスト。wrangler不要な部分（設定解決・署名検証・
リプレイ防御・レート制限・at-least-once配達）をHTTPモックで検証し verify.sh ▶4 に乗せる。
実wranglerを使うE2Eは tests/relay_e2e.sh（別途・署名版）。

P3の核となる回帰: (1)nonceは配達成功後にのみコミット→一時障害でも再配達される・二重配達しない
(2)鮮度は署名済みtsで判定(DO列tsではない) (3)レート超過は延期でロスしない。"""
import importlib.util
import json
import os
import secrets
import tempfile
import time
import unittest
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
            "HISTORY_FILE": ra.office.HISTORY_FILE,
            "DEVICES_FILE": ra.office.DEVICES_FILE,
            "NONCE_FILE": ra.NONCE_FILE,
            "WINDOW": ra.WINDOW, "RATE_CAP": ra.RATE_CAP, "RATE_REFILL": ra.RATE_REFILL,
            "ALLOW_UNSIGNED": ra.ALLOW_UNSIGNED, "time_time": ra.time.time,
        }
        self.tmp = Path(tempfile.mkdtemp(prefix="relay_t_"))
        ra.office.INBOX = self.tmp / "office_inbox"
        ra.office.HISTORY_FILE = ra.office.INBOX / "_history.json"
        ra.office.DEVICES_FILE = self.tmp / "office_devices.json"
        ra.NONCE_FILE = self.tmp / "office_nonces.json"
        ra._NONCES.clear()
        ra._RATE.clear()

    def tearDown(self):
        ra._req = self._orig["_req"]
        ra.office.post_instruction = self._orig["post_instruction"]
        ra.office.office_json = self._orig["office_json"]
        ra.office.INBOX = self._orig["INBOX"]
        ra.office.HISTORY_FILE = self._orig["HISTORY_FILE"]
        ra.office.DEVICES_FILE = self._orig["DEVICES_FILE"]
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

        def boom(session, text):
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
            "feed": ["実行中 明細CSV読込", "編集中 report.md", "💬 アメックスの残高は…"],
        }]}
        out = ra._redact_office_for_relay(snap)
        e = out["employees"][0]
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

    def test_redact_keeps_edition_toplevel(self):
        """R42.1: edition はPWAの表示分岐源＝redaction後もトップレベルに残る（本文/パス由来でない）。"""
        snap = {
            "edition": {"id": "hybrid",
                        "features": {"claudeSessions": True, "openclaw": True}},
            "employees": [{"session": "s1", "lastSaid": "秘密", "cwd": "/Users/x",
                           "target": "", "lastOrder": "", "branch": "", "feed": []}],
        }
        out = ra._redact_office_for_relay(snap)
        self.assertEqual(out["edition"]["id"], "hybrid")
        self.assertEqual(out["edition"]["features"],
                         {"claudeSessions": True, "openclaw": True})
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
        ra.office.post_instruction = lambda s, t: (posted.append((s, t)) or (True, "ok"))
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
        """C2: wantOpenclaw は **claude単体では常にFalse**・有効でも60秒に1回まで。
        （Worker側で別DOへのRPC=1:1課金になり、WSの20:1圧縮を無効化するため）"""
        ra._openclaw_enabled = lambda: False
        self.assertFalse(ra._want_openclaw({}, 0.0))
        ra._openclaw_enabled = lambda: True
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
        orig_feats = ra.office.edition_features
        ra.office.edition_features = lambda ed, lic=None: {"openclaw": True, "relayPwa": True}
        try:
            n = ra.tick("http://x", "t")   # 例外が漏れないこと
            self.assertEqual(n, 0)
            self.assertTrue(any("site=" in u for u in calls), "mini集約が呼ばれていない")
        finally:
            ra.office.edition_features = orig_feats

    def test_license_gate_relaypwa(self):
        """R42.2: relayPwa閉なら常駐ゲートが閉まる。内部例外はfail-open（配達を止めない）。"""
        orig = (ra.office.edition, ra.office.edition_features, ra.office.license_state)
        try:
            ra.office.edition = lambda: "hybrid"
            ra.office.license_state = lambda: {"valid": False}
            ra.office.edition_features = lambda ed, lic=None: {"relayPwa": False}
            self.assertFalse(ra._license_gate_ok())
            ra.office.edition_features = lambda ed, lic=None: {"relayPwa": True}
            self.assertTrue(ra._license_gate_ok())
            def boom():
                raise RuntimeError("license_state爆発")
            ra.office.license_state = boom
            self.assertTrue(ra._license_gate_ok(), "fail-openでない")
        finally:
            ra.office.edition, ra.office.edition_features, ra.office.license_state = orig

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


if __name__ == "__main__":
    unittest.main()
