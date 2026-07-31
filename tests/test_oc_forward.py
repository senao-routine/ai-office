# -*- coding: utf-8 -*-
"""R42.5 双方向中継の単体テスト（wrangler不要・HTTPモック）。

経路: post_instruction oc-分岐 → OC_OUTBOX → relay_agent.forward_oc_outbox（署名して
site=macmini へ）→ mini側 tools/openclaw_agent.py（peek→検証→openclaw_inbox→ack）。

核となる回帰: (1) oc-宛は office_inbox に書かない（孤児inbox根絶）
(2) 転送は成功したファイルだけ削除＝at-least-once (3) ocSecret無しは送らない=fail-closed
(4) mini側は nonce を配達成功後にのみコミット・改竄/リプレイは即ackで捨てる。
"""
import importlib.util
import json
import os
import secrets as _secrets
import tempfile
import time
import unittest
from pathlib import Path

TESTS = Path(__file__).resolve().parent
ROOT = TESTS.parent


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


ra = _load("relay_agent_oc_t", ROOT / "server" / "relay_agent.py")
oa = _load("openclaw_agent_t", ROOT / "tools" / "openclaw_agent.py")
office = ra.office

DID = "d_ab12cd34ef56"          # verify_envelope の形式 d_[0-9a-f]{12}
SEC = "22" * 32                 # 64 hex


class OcOutboxTest(unittest.TestCase):
    """post_instruction の oc-分岐（office_server側）。"""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="oc_outbox_"))
        self._orig = {"OC_OUTBOX": office.OC_OUTBOX, "INBOX": office.INBOX,
                      "HISTORY_FILE": office.HISTORY_FILE}
        office.OC_OUTBOX = self.tmp / "office_oc_outbox"
        office.INBOX = self.tmp / "office_inbox"
        office.HISTORY_FILE = office.INBOX / "_history.json"

    def tearDown(self):
        for k, v in self._orig.items():
            setattr(office, k, v)

    def test_oc_session_goes_to_outbox_not_inbox(self):
        ok, msg = office.post_instruction("oc-lobster-1", "ロブスターへ指示")
        self.assertTrue(ok, msg)
        files = list(office.OC_OUTBOX.glob("*.json"))
        self.assertEqual(len(files), 1)
        d = json.loads(files[0].read_text(encoding="utf-8"))
        self.assertEqual((d["session"], d["text"]), ("oc-lobster-1", "ロブスターへ指示"))
        self.assertFalse((office.INBOX / "oc-lobster-1.json").exists())   # 孤児inboxを作らない
        hist = json.loads(office.HISTORY_FILE.read_text(encoding="utf-8"))
        self.assertEqual(hist[-1]["session"], "oc-lobster-1")             # 履歴は共通で記録

    def test_oc_same_ms_posts_get_distinct_files(self):
        office.post_instruction("oc-lobster-1", "一通目")
        office.post_instruction("oc-lobster-1", "二通目")
        self.assertEqual(len(list(office.OC_OUTBOX.glob("*.json"))), 2)

    def test_validation_is_shared(self):
        ok, _ = office.post_instruction("oc-x", "短すぎるsession")   # regex 8文字未満
        self.assertFalse(ok)
        ok, _ = office.post_instruction("oc-lobster-1", "x" * 4001)
        self.assertFalse(ok)
        self.assertFalse(office.OC_OUTBOX.exists())

    def test_normal_session_still_uses_inbox(self):
        ok, _ = office.post_instruction("relaytest-0001", "通常の投函")
        self.assertTrue(ok)
        self.assertTrue((office.INBOX / "relaytest-0001.json").exists())
        self.assertFalse(office.OC_OUTBOX.exists())


class ForwardTest(unittest.TestCase):
    """relay_agent.forward_oc_outbox（mac側の転送）。"""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="oc_fwd_"))
        self._orig = {"OC_OUTBOX": office.OC_OUTBOX, "CONFIG": ra.CONFIG,
                      "_req": ra._req}
        office.OC_OUTBOX = self.tmp / "outbox"
        office.OC_OUTBOX.mkdir(parents=True)
        ra.CONFIG = self.tmp / "office_relay.json"
        ra._OC_WARNED["nosecret"] = False

    def tearDown(self):
        office.OC_OUTBOX = self._orig["OC_OUTBOX"]
        ra.CONFIG = self._orig["CONFIG"]
        ra._req = self._orig["_req"]

    def _seed_outbox(self, session="oc-lobster-1", text="転送テスト"):
        p = office.OC_OUTBOX / f"{int(time.time() * 1000)}-000.json"
        p.write_text(json.dumps({"session": session, "text": text, "ts": time.time()}),
                     encoding="utf-8")
        return p

    def _config(self, with_keys=True):
        c = {"url": "http://x", "token": "t"}
        if with_keys:
            c.update(ocDeviceId=DID, ocSecret=SEC)
        ra.CONFIG.write_text(json.dumps(c), encoding="utf-8")

    def test_forward_signs_posts_and_deletes(self):
        self._config()
        p = self._seed_outbox()
        calls = []

        def fake(method, url, token, body=None):
            calls.append((method, url, body))
            return {"ok": True, "queued": 1}
        ra._req = fake
        self.assertEqual(ra.forward_oc_outbox("http://x", "t"), 1)
        self.assertFalse(p.exists())                                # 成功分は削除
        method, url, env = calls[0]
        self.assertEqual((method, url), ("POST", "http://x/instruct?site=macmini"))
        # 送った封筒が本物の署名になっている（mini側と同じ verify_envelope で検証）
        devices = {"devices": {DID: {"secret": SEC, "revoked": False, "expires": 2**53}}}
        ok, reason, sess, text = office.verify_envelope(env, devices,
                                                        int(time.time()), 300)
        self.assertTrue(ok, reason)
        self.assertEqual((sess, text), ("oc-lobster-1", "転送テスト"))

    def test_forward_failure_keeps_file(self):
        self._config()
        p = self._seed_outbox()
        ra._req = lambda m, u, t, b=None: (_ for _ in ()).throw(OSError("down"))
        self.assertEqual(ra.forward_oc_outbox("http://x", "t"), 0)
        self.assertTrue(p.exists())                                 # 残置→次tickで再試行

    def test_no_secret_sends_nothing(self):
        self._config(with_keys=False)
        p = self._seed_outbox()
        calls = []
        ra._req = lambda m, u, t, b=None: calls.append(1) or {"ok": True}
        self.assertEqual(ra.forward_oc_outbox("http://x", "t"), 0)
        self.assertEqual(calls, [])                                 # fail-closed
        self.assertTrue(p.exists())

    def test_empty_outbox_makes_zero_requests(self):
        self._config()
        calls = []
        ra._req = lambda m, u, t, b=None: calls.append(1) or {"ok": True}
        self.assertEqual(ra.forward_oc_outbox("http://x", "t"), 0)
        self.assertEqual(calls, [])                                 # リクエスト経済


class OpenclawAgentTest(unittest.TestCase):
    """mini側 openclaw_agent（peek→検証→配達→ack・nonce掟）。"""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="oc_agent_"))
        self._orig = {"OC_INBOX": oa.OC_INBOX, "NONCE_FILE": oa.NONCE_FILE,
                      "_req": oa._req, "_deliver": oa._deliver}
        oa.OC_INBOX = self.tmp / "openclaw_inbox"
        oa.NONCE_FILE = self.tmp / "openclaw_nonces.json"
        oa._NONCES.clear()
        self.devices = {"devices": {DID: {"secret": SEC, "revoked": False,
                                          "expires": 2**53}}}

    def tearDown(self):
        for k, v in self._orig.items():
            setattr(oa, k, v)
        oa._NONCES.clear()

    def _sign(self, session="oc-lobster-1", text="やあ", ts=None, nonce=None):
        return office.sign_envelope(SEC, DID, session, text,
                                    int(time.time()) if ts is None else ts,
                                    nonce or _secrets.token_hex(16))

    def _mock(self, envelopes):
        items = [{"id": i + 1, "text": json.dumps(e)} for i, e in enumerate(envelopes)]
        calls = {"ack": None}

        def fake(method, url, token, body=None):
            if "/pull" in url:
                self.assertIn("site=macmini", url)
                return {"ok": True, "items": list(items)}
            if "/ack" in url:
                self.assertIn("site=macmini", url)
                calls["ack"] = body
                return {"ok": True}
            raise AssertionError(url)
        oa._req = fake
        return calls

    def test_valid_envelope_delivered_and_acked(self):
        calls = self._mock([self._sign()])
        self.assertEqual(oa.pull_and_deliver("http://x", "t", self.devices), 1)
        f = oa.OC_INBOX / "oc-lobster-1.json"
        self.assertTrue(f.exists())
        self.assertIn("やあ", f.read_text(encoding="utf-8"))
        self.assertEqual(calls["ack"], {"ids": [1]})
        self.assertTrue(oa.NONCE_FILE.exists())                     # 配達後にnonce永続

    def test_tampered_envelope_rejected_but_acked(self):
        env = self._sign(text="正しい本文")
        env["text"] = "改竄"
        calls = self._mock([env])
        self.assertEqual(oa.pull_and_deliver("http://x", "t", self.devices), 0)
        self.assertFalse((oa.OC_INBOX / "oc-lobster-1.json").exists())
        self.assertEqual(calls["ack"], {"ids": [1]})                # 恒久不正は即ack

    def test_replay_dropped(self):
        env = self._sign()
        calls = self._mock([env])
        oa.pull_and_deliver("http://x", "t", self.devices)
        (oa.OC_INBOX / "oc-lobster-1.json").unlink()
        calls2 = self._mock([env])                                  # 同nonce再投函
        self.assertEqual(oa.pull_and_deliver("http://x", "t", self.devices), 0)
        self.assertFalse((oa.OC_INBOX / "oc-lobster-1.json").exists())
        self.assertEqual(calls2["ack"], {"ids": [1]})

    def test_delivery_failure_keeps_nonce_unburned(self):
        env = self._sign()
        self._mock([env])
        oa._deliver = lambda s, t: (_ for _ in ()).throw(OSError("disk"))
        self.assertEqual(oa.pull_and_deliver("http://x", "t", self.devices), 0)
        self.assertEqual(oa._NONCES, {})                            # ★焼かない=再配達できる
        oa._deliver = self._orig["_deliver"]
        self._mock([env])                                           # 復旧後の再pullで配達成功
        self.assertEqual(oa.pull_and_deliver("http://x", "t", self.devices), 1)

    def test_same_session_batch_joined(self):
        calls = self._mock([self._sign(text="一通目"), self._sign(text="二通目")])
        self.assertEqual(oa.pull_and_deliver("http://x", "t", self.devices), 1)
        body = (oa.OC_INBOX / "oc-lobster-1.json").read_text(encoding="utf-8")
        self.assertIn("一通目", body)
        self.assertIn("二通目", body)
        self.assertEqual(calls["ack"], {"ids": [1, 2]})


if __name__ == "__main__":
    unittest.main()
