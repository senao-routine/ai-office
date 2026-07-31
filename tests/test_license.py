# -*- coding: utf-8 -*-
"""R42.2 ライセンス機構のテスト。

核となる回帰:
(1) KAT固定＝canonical/EMSA-PKCS1-v1_5 を変えると即破れる（テスト鍵で既知署名をピン）。
(2) 全フィールドの改竄・不正署名・範囲外・不正editionを拒否。
(3) ファイル欠如=無料tier（reason=ライセンス未登録）・edition_features の有償フラグが閉じる。
(4) apply_license の保存経路（600権限・検証してから保存・解除・文字列JSON受理）。
既存の署名封筒KAT（test_crypto.KAT_SIG / js_sign_kat.mjs）とは別系統＝非接触。
"""
import importlib.util
import json
import os
import stat
import tempfile
import time
import unittest
from pathlib import Path

TESTS = Path(__file__).resolve().parent
ROOT = TESTS.parent

_KEY = json.loads((TESTS / "fixtures" / "license_test_key.json").read_text(encoding="utf-8"))
TEST_N = int(str(_KEY["n"]), 16)
TEST_E = int(_KEY["e"])
TEST_D = int(str(_KEY["d"]), 16)

_home = Path(tempfile.mkdtemp(prefix="office_license_home_"))
os.environ["OFFICE_HOME"] = str(_home)
_spec = importlib.util.spec_from_file_location("license_t", ROOT / "server" / "license.py")
lic = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(lic)
_ospec = importlib.util.spec_from_file_location(
    "office_server_license", ROOT / "server" / "office_server.py")
office = importlib.util.module_from_spec(_ospec)
_ospec.loader.exec_module(office)
_sspec = importlib.util.spec_from_file_location(
    "license_sign_t", ROOT / "tools" / "license_sign.py")
signer = importlib.util.module_from_spec(_sspec)
_sspec.loader.exec_module(signer)

# KAT: 固定ライセンス＋テスト鍵の既知署名（canonical/EMSA変更で破れる錠）
KAT_LIC = {"v": 1, "edition": "hybrid", "key_id": "kat-0001", "issued": 1753500000,
           "holder": "abcdef012345", "alg": "RS256"}
KAT_SIG = (
    "27e0c592d120f16b72d2681cde1c9ff8b6dd8a7e1672f3c163e4de0d69a768dd"
    "c9d2393762335caeb6eba107da8067ab51747a5ccfd557a08705190f72632786"
    "09a811248f66c11e377429360ee48cb3b9eada70d2dacaf416e440a2e26c429a"
    "fda41c466d60149d5efdf3900f872478fb53d576031530c54429625189e433c5"
    "40ca559837370a2dd24ae2c795cee8b1dadf88cd4e785efde9ac0f54b5444de4"
    "d7063927d7e36f7e0d8c351739555e6126ef4864e5d31b188ecffa9181457dbc"
    "3c5699817d1581f493e91518f5866232eb086a0ac7c3ae5b810d267305fffa59"
    "c8154550db18f99c3a6c19451f72806d6d5924cdf4f744432eb91839ea1b1f13")


def kat(**over):
    d = {**KAT_LIC, "sig": KAT_SIG}
    d.update(over)
    return d


class VerifyLicenseTest(unittest.TestCase):
    def test_kat_valid(self):
        ok, reason = lic.verify_license(kat(), n=TEST_N, e=TEST_E)
        self.assertTrue(ok, reason)

    def test_kat_rejected_by_prod_key(self):
        # テスト鍵の署名は本番公開鍵（既定）では絶対に通らない
        ok, _ = lic.verify_license(kat())
        self.assertFalse(ok)

    def test_tamper_each_field(self):
        for over in ({"edition": "claude"}, {"key_id": "kat-0002"},
                     {"issued": 1753500001}, {"holder": "abcdef012346"}):
            ok, reason = lic.verify_license(kat(**over), n=TEST_N, e=TEST_E)
            self.assertFalse(ok, f"改竄が通った: {over}")
            self.assertEqual(reason, "署名が一致しません", over)

    def test_reject_malformed(self):
        cases = [
            (kat(v=2), "バージョン"),
            (kat(alg="HS256"), "バージョン"),
            (kat(edition="openclaw"), "edition"),   # ②は無料＝ライセンス対象外
            (kat(edition="pro"), "edition"),
            (kat(issued=0), "issued"),
            (kat(sig="zz"), "16進"),
            (kat(sig=format(TEST_N, "x")), "範囲外"),  # sig >= n
            ("not-a-dict", "形式"),
            (None, "形式"),
        ]
        for bad, frag in cases:
            ok, reason = lic.verify_license(bad, n=TEST_N, e=TEST_E)
            self.assertFalse(ok, f"不正が通った: {bad!r}")
            self.assertIn(frag, reason, f"{bad!r} => {reason}")

    def test_prod_pubkey_sane(self):
        self.assertEqual(lic.PUBKEY_N.bit_length(), 2048)
        self.assertEqual(lic.PUBKEY_E, 65537)
        self.assertNotEqual(lic.PUBKEY_N, TEST_N, "本番鍵にテスト鍵が混入")

    def test_sign_roundtrip_via_cli_builder(self):
        built = signer.build_license("claude", "Buyer@Example.com ", TEST_N, TEST_E, TEST_D)
        ok, reason = lic.verify_license(built, n=TEST_N, e=TEST_E)
        self.assertTrue(ok, reason)
        self.assertEqual(len(built["holder"]), 12)
        # メールは小文字/trim正規化してhash（大小・空白ゆれで別人化しない）
        self.assertEqual(built["holder"],
                         signer.build_license("claude", "buyer@example.com",
                                              TEST_N, TEST_E, TEST_D)["holder"])


class OfficeLicenseStateTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="office_license_state_"))
        self.licpath = self.tmp / "office_license.json"
        os.environ["OFFICE_LICENSE"] = str(self.licpath)
        os.environ["OFFICE_LICENSE_PUBKEY_N"] = format(TEST_N, "x")
        os.environ.pop("OFFICE_EDITION", None)
        office._license_cache.update({"path": None, "mtime": None, "state": None})
        office._cache["t"] = 0.0

    def tearDown(self):
        os.environ.pop("OFFICE_LICENSE", None)
        os.environ.pop("OFFICE_LICENSE_PUBKEY_N", None)
        os.environ.pop("OFFICE_EDITION", None)

    def _write(self, body):
        self.licpath.write_text(json.dumps(body, ensure_ascii=False), encoding="utf-8")
        os.utime(self.licpath, (time.time() + 2, time.time() + 2))  # mtimeキャッシュを確実に破る

    def test_missing_file_is_free_tier(self):
        state = office.license_state()
        self.assertFalse(state["valid"])
        self.assertEqual(state["reason"], "ライセンス未登録")
        feats = office.edition_features("hybrid", state)
        self.assertFalse(feats["relayPwa"])
        self.assertFalse(feats["push"])
        self.assertFalse(feats["costDash"])
        self.assertTrue(feats["claudeSessions"] and feats["openclaw"])

    def test_valid_license_unlocks(self):
        self._write(kat())
        state = office.license_state()
        self.assertTrue(state["valid"], state)
        self.assertEqual(state["edition"], "hybrid")
        feats = office.edition_features("hybrid", state)
        self.assertTrue(feats["relayPwa"] and feats["push"] and feats["costDash"])
        # hybridライセンスは①claude利用もカバー（アップグレード動線）
        self.assertTrue(office.edition_features("claude", state)["costDash"])

    def test_claude_license_does_not_cover_hybrid(self):
        built = signer.build_license("claude", "a@b", TEST_N, TEST_E, TEST_D)
        self._write(built)
        state = office.license_state()
        self.assertTrue(state["valid"])
        self.assertFalse(office.edition_features("hybrid", state)["costDash"])
        self.assertTrue(office.edition_features("claude", state)["costDash"])

    def test_openclaw_edition_free_without_license(self):
        feats = office.edition_features("openclaw", office.license_state())
        self.assertTrue(feats["relayPwa"] and feats["push"])
        self.assertFalse(feats["costDash"])  # ②にclaudeコストダッシュは無い

    def test_tampered_file_rejected(self):
        self._write(kat(holder="000000000000"))
        state = office.license_state()
        self.assertFalse(state["valid"])
        self.assertEqual(state["reason"], "署名が一致しません")

    def test_office_json_carries_license_summary(self):
        self._write(kat())
        data = office.office_json()
        self.assertTrue(data["edition"]["license"]["valid"])
        self.assertEqual(data["edition"]["license"]["edition"], "hybrid")
        self.assertNotIn("sig", json.dumps(data["edition"]))  # 署名値は外に出さない

    def test_apply_license_saves_600_and_removes(self):
        ok, msg, extra = office.apply_license(json.dumps(kat()))  # 文字列JSONも受ける
        self.assertTrue(ok, msg)
        mode = stat.S_IMODE(self.licpath.stat().st_mode)
        self.assertEqual(mode, 0o600, oct(mode))
        self.assertTrue(extra["license"]["valid"])
        ok, msg, _ = office.apply_license(None)
        self.assertTrue(ok)
        self.assertFalse(self.licpath.exists())

    def test_apply_license_rejects_invalid_without_saving(self):
        ok, msg, _ = office.apply_license(kat(edition="claude"))
        self.assertFalse(ok)
        self.assertIn("検証に失敗", msg)
        self.assertFalse(self.licpath.exists())


if __name__ == "__main__":
    unittest.main()
