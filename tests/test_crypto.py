# -*- coding: utf-8 -*-
"""P3 署名プロトコルのゴールデン（wrangler不要・verify.sh ▶4 の unittest で常時実行）。
KAT定数（固定入力→固定sig）で JS(WebCrypto)/Python の乖離と canonical の破壊的変更を機械検知する。
KAT が破れたら「署名対象(canonical)を変えた」ということ＝スマホ側 APP_HTML の署名も更新が必要。"""
import importlib.util
import tempfile
import unicodedata
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("office_server_c", ROOT / "server" / "office_server.py")
office = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(office)

# KAT: この固定入力の sig は server/office_server.py の _canonical/sign_envelope で凍結。
SEC = "00112233445566778899aabbccddeeff00112233445566778899aabbccddeeff"
DID = "d_0123456789ab"
SESS = "sess-kat-00000001"
TEXT = "こんにちは、世界"
TS = 1720400000
NONCE = "0123456789abcdef0123456789abcdef"
KAT_SIG = "6ec594fdd36b8eeca7ef8a2c96676849cdbc51560fac6449140a71a4ffd54773"


class CryptoTest(unittest.TestCase):
    def test_kat_fixed_sig(self):
        env = office.sign_envelope(SEC, DID, SESS, TEXT, TS, NONCE)
        self.assertEqual(env["sig"], KAT_SIG,
                         "canonical が変わった疑い。APP_HTML のJS署名も揃えて KAT_SIG を更新のこと")
        self.assertEqual(env["alg"], "HS256")
        self.assertEqual(env["v"], 1)

    def test_sign_verify_roundtrip(self):
        env = office.sign_envelope(SEC, DID, SESS, "テスト指示", TS, NONCE)
        devices = {"version": 1, "devices": {DID: {"secret": SEC, "expires": TS + 1000, "revoked": False}}}
        ok, reason, sess, text = office.verify_envelope(env, devices, TS, 300)
        self.assertTrue(ok, reason)
        self.assertEqual((sess, text), (SESS, "テスト指示"))

    def test_canonical_no_nfc(self):
        # 見た目が同じでも合成(NFC)と分解(NFD)は生バイトが違う → sig が変われば「正規化していない」証明
        nfc = unicodedata.normalize("NFC", "が")
        nfd = unicodedata.normalize("NFD", "が")
        self.assertNotEqual(nfc, nfd)
        s1 = office.sign_envelope(SEC, DID, SESS, nfc, TS, NONCE)["sig"]
        s2 = office.sign_envelope(SEC, DID, SESS, nfd, TS, NONCE)["sig"]
        self.assertNotEqual(s1, s2)

    def test_tampered_text_rejected(self):
        env = office.sign_envelope(SEC, DID, SESS, "本文A", TS, NONCE)
        env["text"] = "本文B"    # 署名後に本文だけ差し替え → sha/sig 不一致
        devices = {"version": 1, "devices": {DID: {"secret": SEC, "expires": TS + 1000, "revoked": False}}}
        ok, reason, _, _ = office.verify_envelope(env, devices, TS, 300)
        self.assertFalse(ok)
        self.assertEqual(reason, "bad-sig")

    def test_stale_and_future_ts_rejected(self):
        devices = {"version": 1, "devices": {DID: {"secret": SEC, "expires": TS + 10 ** 9, "revoked": False}}}
        env = office.sign_envelope(SEC, DID, SESS, "x", TS, NONCE)
        self.assertEqual(office.verify_envelope(env, devices, TS + 1000, 300)[1], "stale-ts")
        self.assertEqual(office.verify_envelope(env, devices, TS - 1000, 300)[1], "stale-ts")

    def test_expired_and_revoked_and_unknown(self):
        env = office.sign_envelope(SEC, DID, SESS, "x", TS, NONCE)
        self.assertEqual(office.verify_envelope(
            env, {"devices": {DID: {"secret": SEC, "expires": TS - 1, "revoked": False}}}, TS, 300)[1], "expired")
        self.assertEqual(office.verify_envelope(
            env, {"devices": {DID: {"secret": SEC, "expires": TS + 1000, "revoked": True}}}, TS, 300)[1], "revoked")
        self.assertEqual(office.verify_envelope(env, {"devices": {}}, TS, 300)[1], "unknown-device")

    def test_verify_never_raises_on_nonstring_fields(self):
        # 非文字列フィールドで re.fullmatch が TypeError→relay_agent常駐死する退行[review#7]の回帰
        devices = {"version": 1, "devices": {DID: {"secret": SEC, "expires": TS + 1000, "revoked": False}}}
        for bad in ({"device_id": 123}, {"session": []}, {"nonce": {}}, {"sig": 1}, {"text": 5}):
            env = office.sign_envelope(SEC, DID, SESS, "x", TS, NONCE)
            env.update(bad)
            ok, reason, _, _ = office.verify_envelope(env, devices, TS, 300)   # 例外を出さないこと
            self.assertFalse(ok)

    def test_verify_corrupt_ledger_record_returns_bad_record(self):
        # 台帳の手編集/移行不整合で bytes.fromhex/int が例外→配達バッチ全滅する退行[review#1/#8]の回帰
        env = office.sign_envelope(SEC, DID, SESS, "x", TS, NONCE)
        self.assertEqual(office.verify_envelope(
            env, {"devices": {DID: {"expires": TS + 1000}}}, TS, 300)[1], "bad-record")           # secret欠損
        self.assertEqual(office.verify_envelope(
            env, {"devices": {DID: {"secret": "zz", "expires": TS + 1000}}}, TS, 300)[1], "bad-record")  # 不正hex
        self.assertEqual(office.verify_envelope(
            env, {"devices": {DID: {"secret": SEC, "expires": None}}}, TS, 300)[1], "bad-record")   # expires型不正

    def test_list_devices_never_leaks_secret(self):
        office.DEVICES_FILE = Path(tempfile.mkdtemp(prefix="devx_")) / "office_devices.json"
        office.new_device("A")
        office.new_device("B")
        import json as _json
        dump = _json.dumps(office.list_devices())
        self.assertNotIn("secret", dump)
        self.assertNotIn("token", dump)

    def test_new_device_list_redacted_revoke(self):
        office.DEVICES_FILE = Path(tempfile.mkdtemp(prefix="dev_")) / "office_devices.json"
        dev = office.new_device("iPhone")
        self.assertTrue(dev["device_id"].startswith("d_"))
        self.assertEqual(len(dev["secret"]), 64)
        self.assertEqual(dev["expires"] - dev["created"], office.DEVICE_TTL)
        lst = office.list_devices()
        self.assertEqual(len(lst), 1)
        self.assertNotIn("secret", lst[0])          # 一覧に secret を漏らさない
        # 600 権限で保存されていること
        import os
        self.assertEqual(oct(os.stat(office.DEVICES_FILE).st_mode & 0o777), "0o600")
        self.assertTrue(office.revoke_device(dev["device_id"]))
        env = office.sign_envelope(dev["secret"], dev["device_id"], SESS, "x", TS, NONCE)
        ok, reason, _, _ = office.verify_envelope(env, office.load_devices(), TS, 300)
        self.assertFalse(ok)
        self.assertEqual(reason, "revoked")


if __name__ == "__main__":
    unittest.main()
