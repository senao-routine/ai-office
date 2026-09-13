#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""R87: 封書（server/dialog_seal.py）。

暗号は「動いた」だけでは足りない。**公式ベクタで固め、改竄が必ず落ちること**を見る
（ws_client の RFC6455 KAT・test_crypto の署名 KAT と同じ流儀）。
"""
import importlib.util
import json
import pathlib
import sys
import time
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("dialog_seal_t", ROOT / "server/dialog_seal.py")
ds = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ds)

SECRET = "5e" * 32
DEV, SESS = "dev-kat-0001", "sess-kat-0001"
REQ = "a1b2c3d4e5f60718293a4b5c6d7e8f90"


@unittest.skipUnless(ds.AVAILABLE, "この Mac に CommonCrypto の AES-256-GCM が無い")
class SealTest(unittest.TestCase):
    def test_each_backend_is_checked_against_the_official_vectors_by_name(self):
        """★片方ずつ名指しで検算する。まとめて `_load_backend()` を呼ぶだけだと
        選択順に隠れて **もう片方が一度も検証されない**（最初そう書いていた）。"""
        page = {"messages": [{"role": "user", "text": "確認"}]}
        checked = []
        for name in ("oneshot", "mode"):
            found = ds._load_backend(only=name)
            if not found:
                continue                       # この Mac に無いバックエンドは飛ばす
            self.assertEqual(found[0], name)   # NIST 一致は _load_backend の中で判定済み
            saved = ds._BACKEND
            try:
                ds._BACKEND = found            # そのバックエンドで実際に往復する
                b = ds.seal(SECRET, DEV, REQ, SESS, page)
                self.assertEqual(ds.unseal(SECRET, DEV, SESS, b), page, name)
            finally:
                ds._BACKEND = saved
            checked.append(name)
        self.assertTrue(checked, "検算できたバックエンドが1つも無い")
        self.assertTrue(ds.selftest()[0], ds.selftest()[1])

    def test_the_two_backends_produce_the_same_bytes(self):
        """同じ入力なら実装が違ってもバイト一致する＝どちらが選ばれても端末は開ける。"""
        both = [ds._load_backend(only=n) for n in ("oneshot", "mode")]
        if not all(both):
            self.skipTest("この Mac では片方のバックエンドしか無い")
        out = []
        saved = ds._BACKEND
        try:
            for backend in both:
                ds._BACKEND = backend
                out.append(ds.seal(SECRET, DEV, REQ, SESS, {"t": "同じ"},
                                   iat=1788900000, salt=b"\x07" * 16,
                                   ivs=[bytes([9]) * 12] * 4))
        finally:
            ds._BACKEND = saved
        self.assertEqual(out[0], out[1])

    def test_roundtrip_across_the_frame_boundary(self):
        for n in (0, 1, 100, ds.BLOCK - 5, ds.BLOCK - 4, ds.BLOCK, ds.BLOCK + 1):
            page = {"t": "あ" * n}                       # zlib 後の長さで分岐する
            b = ds.seal(SECRET, DEV, REQ, SESS, page)
            self.assertEqual(ds.unseal(SECRET, DEV, SESS, b), page, n)
            self.assertEqual(len(ds.unb64u(b["b"])), b["n"] * ds.FRAME_LEN, n)

    def test_the_wire_never_carries_the_conversation(self):
        page = {"messages": [{"role": "user", "text": "アメックスの請求書を確認して"}]}
        b = ds.seal(SECRET, DEV, REQ, SESS, page)
        wire = json.dumps(b, ensure_ascii=False)
        for leak in ("アメックス", "請求書", SESS, "messages", "role"):
            self.assertNotIn(leak, wire, leak)
        self.assertEqual(set(b) - {"v", "id", "s", "i", "e", "n", "c", "b", "err"}, set())

    def test_length_only_leaks_in_8kb_steps(self):
        short = ds.seal(SECRET, DEV, REQ, SESS, {"t": "短い"})
        long_ = ds.seal(SECRET, DEV, REQ, SESS, {"t": "あ" * 3000})
        self.assertEqual(len(short["b"]), len(long_["b"]))     # どちらも1フレーム

    def test_every_single_byte_change_is_refused(self):
        b = ds.seal(SECRET, DEV, REQ, SESS, {"t": "x"})
        raw = bytearray(ds.unb64u(b["b"]))
        for label, pos in (("マジック", 0), ("IV", 5), ("暗号文", 100), ("タグ", len(raw) - 1)):
            bad = bytearray(raw)
            bad[pos] ^= 1
            with self.assertRaises(ValueError, msg=label):
                ds.unseal(SECRET, DEV, SESS, {**b, "b": ds.b64u(bytes(bad))})

    def test_aad_binds_device_request_session_and_time(self):
        b = ds.seal(SECRET, DEV, REQ, SESS, {"t": "x"})
        for label, args, patch in (
                ("別端末", ("dev-other", SESS), {}),
                ("別セッション", (DEV, "sess-other"), {}),
                ("別要求", (DEV, SESS), {"id": "f" * 32}),
                ("時刻すり替え", (DEV, SESS), {"i": b["i"] + 1}),
                ("フレーム数詐称", (DEV, SESS), {"n": 2})):
            with self.assertRaises((ValueError, KeyError), msg=label):
                ds.unseal(SECRET, args[0], args[1], {**b, **patch})

    def test_another_devices_secret_cannot_open_it(self):
        b = ds.seal(SECRET, DEV, REQ, SESS, {"t": "x"})
        with self.assertRaises(ValueError):
            ds.unseal("aa" * 32, DEV, SESS, b)

    def test_the_conversation_key_never_reveals_the_pairing_secret(self):
        """用途分離: 会話鍵が全部漏れても署名鍵（元の秘密）は復元できない。"""
        k = ds.derive_key(SECRET, b"\0" * 16, DEV, REQ)
        self.assertEqual(len(k), 32)
        self.assertNotIn(k.hex(), SECRET)
        self.assertNotEqual(k, bytes.fromhex(SECRET))
        # salt/端末/要求 のどれが変わっても別の鍵になる
        keys = {ds.derive_key(SECRET, s, d, r).hex()
                for s, d, r in ((b"\0" * 16, DEV, REQ), (b"\1" * 16, DEV, REQ),
                                (b"\0" * 16, "dev-x", REQ), (b"\0" * 16, DEV, "b" * 32))}
        self.assertEqual(len(keys), 4)

    def test_hkdf_refuses_to_be_used_as_a_keystream(self):
        with self.assertRaises(ValueError):
            ds.hkdf_sha256(b"s", b"k", b"i", length=64)

    def test_invalid_request_ids_are_refused(self):
        for bad in ("", "xyz", "A" * 32, "0" * 31, None):
            with self.assertRaises(ValueError):
                ds.seal(SECRET, DEV, bad, SESS, {"t": "x"})

    def test_a_payload_that_cannot_fit_is_refused_not_silently_cut(self):
        with self.assertRaises(ValueError) as cm:
            ds.seal(SECRET, DEV, REQ, SESS, {"t": "".join(chr(0x4e00 + (i % 20000))
                                                          for i in range(200000))})
        self.assertEqual(str(cm.exception), "toolarge")

    def test_the_fixed_vector_in_the_repository_still_seals_the_same_way(self):
        """両側KATの固定値が動いたら、JS 側と静かに食い違っている。"""
        kat = json.loads((ROOT / "tests/fixtures/dialog_seal_kat.json").read_text(encoding="utf-8"))
        again = ds.seal(kat["secretHex"], kat["deviceId"], kat["bundle"]["id"],
                        kat["targetSession"], kat["page"], iat=kat["iat"],
                        salt=bytes.fromhex(kat["saltHex"]),
                        ivs=[bytes([0x40 + i] * 12) for i in range(4)])
        self.assertEqual(again, kat["bundle"])



class SharedBundlesTest(unittest.TestCase):
    """封じたものが**別プロセス**（relay_agent）へ渡るか。R92 の SharedResultsTest と同じ型。

    daemon が封じ、relay_agent が自分のプロセスで読む。メモリのレジストリでは渡らないので
    ファイル（0600・単一書き手・TTL）で渡す。ここでは「メモリを空にする＝別プロセス」で再現する。
    """

    def setUp(self):
        import tempfile
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.saved = (ds.BUNDLES_FILE, ds.BUNDLE_TTL)
        ds.BUNDLES_FILE = pathlib.Path(self.tmp.name) / ".claude" / "office_dialog_bundles.json"
        ds.BUNDLE_TTL = 90
        self._forget_memory()

    def tearDown(self):
        ds.BUNDLES_FILE, ds.BUNDLE_TTL = self.saved
        self._forget_memory()

    def _forget_memory(self):
        ds._RESERVED.clear()
        ds._FILE_CACHE.update(key=None, rows=[])

    def _bundle(self, rid, exp_in=90):
        return {"v": 1, "id": rid, "s": "AAAA", "i": int(time.time()),
                "e": int(time.time()) + exp_in, "n": 1, "c": "zl", "b": "QU8x"}

    def test_never_used_means_nothing_to_send(self):
        self.assertIsNone(ds.live_bundles())

    def test_a_bundle_reaches_a_process_that_did_not_seal_it(self):
        rid = "1" * 32
        self.assertEqual(ds.reserve_dialog(rid), (None, True))
        ds.store_bundle(rid, self._bundle(rid))
        self.assertEqual(ds.BUNDLES_FILE.stat().st_mode & 0o777, 0o600)
        self._forget_memory()                                    # ＝relay_agent 側
        self.assertEqual([b["id"] for b in ds.live_bundles()], [rid])
        self.assertEqual(ds.live_bundle(rid)["b"], "QU8x")

    def test_redelivery_is_idempotent_even_after_a_daemon_restart(self):
        rid = "2" * 32
        ds.reserve_dialog(rid)
        ds.store_bundle(rid, self._bundle(rid))
        self._forget_memory()                                    # ＝daemon 再起動
        again, created = ds.reserve_dialog(rid)
        self.assertEqual((again["id"], created), (rid, False))   # 二重に読み直さない・封じ直さない

    def test_an_in_flight_reservation_blocks_a_second_seal(self):
        rid = "3" * 32
        self.assertEqual(ds.reserve_dialog(rid), (None, True))
        self.assertEqual(ds.reserve_dialog(rid), (None, False))  # 封緘中は待つ

    def test_expired_bundles_are_dropped_not_served(self):
        rid = "4" * 32
        ds.store_bundle(rid, self._bundle(rid, exp_in=-1))
        self._forget_memory()
        self.assertEqual(ds.live_bundles(), [])                  # 空＝「空を1回送って消す」の合図
        self.assertIsNone(ds.live_bundle(rid))
        ds.sweep()
        self.assertEqual(json.loads(ds.BUNDLES_FILE.read_text())["bundles"], [])

    def test_the_file_is_capped(self):
        for i in range(ds.MAX_LIVE + 3):
            rid = f"{i:032x}"
            ds.store_bundle(rid, self._bundle(rid))
        self.assertEqual(len(ds.live_bundles()), ds.MAX_LIVE)

    def test_a_hand_broken_file_cannot_take_down_the_daemon(self):
        ds.BUNDLES_FILE.parent.mkdir(parents=True, exist_ok=True)
        for junk in ("not json", '{"bundles": "x"}', '{"bundles": [1, {"id": 3}, {"id": "zz", "e": "soon"}]}',
                     '{"bundles": [{"id": "%s", "e": 1e309}]}' % ("5" * 32)):
            ds.BUNDLES_FILE.write_text(junk, encoding="utf-8")
            self._forget_memory()
            self.assertIn(ds.live_bundles(), ([], None), junk)
            self.assertEqual(ds.reserve_dialog("6" * 32), (None, True), junk)
            self._forget_memory()

    def test_the_file_never_carries_plaintext(self):
        rid = "7" * 32
        page = {"messages": [{"role": "user", "text": "アメックスの請求書"}]}
        b = ds.seal(SECRET, DEV, rid, SESS, page) if ds.AVAILABLE else self._bundle(rid)
        ds.store_bundle(rid, b)
        raw = ds.BUNDLES_FILE.read_text(encoding="utf-8")
        for leak in ("アメックス", "請求書", "messages", SESS):
            self.assertNotIn(leak, raw, leak)


class UnavailableTest(unittest.TestCase):
    def test_without_a_backend_the_feature_is_off_not_plaintext(self):
        saved_flag, saved_backend = ds.AVAILABLE, ds._BACKEND
        try:
            ds.AVAILABLE, ds._BACKEND = False, None
            with self.assertRaises(RuntimeError):
                ds.seal(SECRET, DEV, REQ, SESS, {"t": "x"})
            self.assertFalse(ds.selftest()[0])
        finally:
            ds.AVAILABLE, ds._BACKEND = saved_flag, saved_backend


if __name__ == "__main__":
    unittest.main()
