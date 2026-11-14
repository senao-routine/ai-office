# -*- coding: utf-8 -*-
"""R100-1b: スマホから許可の固定値（KAT）。Python（Mac）と JS（スマホ・tests/approve_kat.mjs）の両側で同じ値に凍結する。

- 表示と署名の正本 inputCanon・その SHA-256（approval_policy.input_canon / input_sha）
- 内側の canonical と WebAuthn の challenge（office_approve.canonical / approve_challenge）
- 照合コード fp（office_approve.key_fp）
- P-256 の検証（RFC 6979 A.2.5・SHA-256・"sample" と "test"）の正と負
- SPKI と DER の読み方（壊れた形は全部 False／ValueError）
値を変えると両側がずれる＝変えるなら Python と JS と docs を同時に（.claude/rules/crypto.md）。
"""
import hashlib
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "server"))
sys.path.insert(0, str(ROOT / "tests"))
os.environ.setdefault("OFFICE_HOME", tempfile.mkdtemp(prefix="apvkat-"))
import approval_policy as ap  # noqa: E402
import office_approve as oa  # noqa: E402
import p256_verify as pv  # noqa: E402
from fake_authn import der_sig  # noqa: E402

D = 0xC9AFA9D845BA75166B5C215767B1D6934E50C3DB36E89B127B8A622B120F6721
UX = 0x60FED4BA255A9D31C961EB74C6356D68C049B8923B61FA6CE669622E60F29FB6
UY = 0x7903FE1008B8BC99A41AE9E95628BC64F2F1B20C2D7E9F5177A3C294D4462299
VECTORS = (
    (b"sample", 0xEFD48B2AACB6A8FD1140DD9CD45E81D69D2C877B56AAF991C34D0EA84EAF3716,
     0xF7CB1C942D657C41D436C7A1B6E29F65F3E900DBB9AFF4064DC4AB2F843ACDA8),
    (b"test", 0xF1ABB023518351CD71D881567B1EA663ED3EFCF6C5132B354F28D3B0B7D38367,
     0x019F4113742A2B14BD25926B49C649155F267E60D3814B4C0CC84250E46F0083),
)
KAT = {
    "tool": "Bash", "input": {"command": "npm test", "description": "テストを実行"},
    "device": "d_0123456789ab", "session": "3f2a9c1e-7b4d-4e21-9a0c-5d6e7f809102",
    "askId": "0123456789abcdef0123456789abcdef", "reqId": "fedcba9876543210fedcba9876543210", "iat": 1790000000,
    "canon": '{"input":{"command":"npm test","description":"テストを実行"},"tool":"Bash"}',
    "sha": "83ed35af0246956ae019212ff07d19ff5b8c9a03cf0dedd1ba9f2eadc72afea1",
    # 2026-09-29 別モデルレビュー: 署名を「Mac が封じた時刻（iat）」に結ぶ＝canonical に iat を足した（計画の値から変えた）
    "challenge": "1c17f070a8687eb882e67fd738b2cab81b06d78655bf08a3691fd22b12c5cf75",
    "challenge_b64u": "HBfwcKhofriC5n_XOLLKuBsG14ZVvwijaR_SKxLFz3U",
    "fp": "7fd28a",
}


class ApproveKatTest(unittest.TestCase):
    def test_public_key_from_the_rfc_private_key(self):
        self.assertEqual(pv._mul(D, pv.G), (UX, UY))
        self.assertTrue(pv.on_curve((UX, UY)))

    def test_rfc6979_vectors_verify(self):
        for msg, r, s in VECTORS:
            self.assertTrue(pv.verify_digest((UX, UY), hashlib.sha256(msg).digest(), r, s), msg)

    def test_negative_cases(self):
        msg, r, s = VECTORS[0]
        dg = hashlib.sha256(msg).digest()
        self.assertFalse(pv.verify_digest((UX, UY), dg, r ^ 1, s))                       # r のビット反転
        self.assertFalse(pv.verify_digest((UX, UY), hashlib.sha256(b"other").digest(), r, s))   # 別メッセージ
        self.assertFalse(pv.verify_digest((UX, UY), dg, r, 0))                           # s=0
        self.assertFalse(pv.verify_digest((UX, UY), dg, 0, s))
        self.assertFalse(pv.verify_digest((UX, UY), dg, pv.N, s))                        # 範囲外
        self.assertFalse(pv.verify_digest((UX, UY + 1), dg, r, s))                       # 曲線外の点
        self.assertFalse(pv.verify_digest(None, dg, r, s))
        self.assertFalse(pv.verify_digest((UX, UY), dg[:31], r, s))
        self.assertFalse(pv.verify_digest((UX, UY), dg, True, s))

    def test_der_roundtrip_and_strictness(self):
        msg, r, s = VECTORS[1]
        der = der_sig(r, s)
        self.assertEqual(pv.parse_der_sig(der), (r, s))
        for bad in (b"", der[:-1], der + b"\x00", b"\x31" + der[1:], der[:2] + b"\x03" + der[3:],
                    b"\x30\x06\x02\x01\x80\x02\x01\x01",                 # 負の整数
                    b"\x30\x07\x02\x02\x00\x01\x02\x01\x01"):            # 最短でない
            with self.assertRaises(ValueError, msg=bad.hex()):
                pv.parse_der_sig(bad)

    def test_spki(self):
        spki = pv.SPKI_PREFIX + bytes.fromhex(pv.pub_hex((UX, UY)))
        self.assertEqual(pv.parse_spki(spki), (UX, UY))
        self.assertEqual(len(spki), 91)
        for bad in (spki[:-1], spki[:26] + b"\x02" + spki[27:], b"\x00" + spki[1:],
                    spki[:-1] + bytes([spki[-1] ^ 1])):
            with self.assertRaises(ValueError):
                pv.parse_spki(bad)

    def test_canon_challenge_fp(self):
        canon = ap.input_canon(KAT["tool"], KAT["input"])
        self.assertEqual(canon, KAT["canon"])
        self.assertEqual(ap.input_sha(canon), KAT["sha"])
        fields = (KAT["device"], KAT["session"], KAT["askId"], KAT["tool"], KAT["sha"], KAT["iat"], "allow", KAT["reqId"])
        self.assertEqual(oa.canonical(*fields), "\n".join(["aioffice-approve", "v1", *map(str, fields)]))
        ch = oa.approve_challenge(*fields)
        self.assertEqual(ch.hex(), KAT["challenge"])
        self.assertEqual(oa.b64u(ch), KAT["challenge_b64u"])
        cred = bytes.fromhex("00112233445566778899aabbccddeeff")
        self.assertEqual(oa.key_fp(cred, bytes.fromhex(pv.pub_hex((UX, UY)))), KAT["fp"])

    def test_webauthn_shape(self):
        """authData ‖ SHA-256(clientDataJSON) の SHA-256 に署名する形（WebAuthn §6.3.3）。"""
        from fake_authn import FakeAuthenticator
        a = FakeAuthenticator("relay.example", "https://relay.example", d=D)
        got = a.get(bytes.fromhex(KAT["challenge"]))
        args = [oa.unb64u(got[k]) for k in ("authData", "clientDataJSON", "signature")]
        self.assertTrue(pv.verify_webauthn((UX, UY), *args))
        self.assertFalse(pv.verify_webauthn((UX, UY), args[0][:-1] + b"\x01", args[1], args[2]))
        self.assertFalse(pv.verify_webauthn((UX, UY), args[0], args[1] + b" ", args[2]))
        self.assertFalse(pv.verify_webauthn((UX, UY), args[0], args[1], b"\x30\x00"))


class RemoteSetKatTest(unittest.TestCase):
    """R103-1: スマホから ON（rst-）の内側の署名対象。並び・区切り・ラベルを固定（スマホの JS 側は R103-3 で同じ値に結ぶ）。"""

    def test_remote_canonical_and_challenge_are_fixed(self):
        line = oa.remote_canonical("d_0123456789ab", "a" * 32, True, 4, 1790000000)
        self.assertEqual(line, "aioffice-remote\nv1\nd_0123456789ab\n" + "a" * 32 + "\n1\n4\n1790000000")
        self.assertEqual(oa.remote_challenge("d_0123456789ab", "a" * 32, True, 4, 1790000000).hex(),
                         __import__("hashlib").sha256(line.encode("utf-8")).hexdigest())
        self.assertEqual(oa.remote_canonical("d_0123456789ab", "a" * 32, False, None, 1790000000).split("\n")[4:6], ["0", "0"])


if __name__ == "__main__":
    unittest.main()
