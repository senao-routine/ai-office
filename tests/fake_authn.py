# -*- coding: utf-8 -*-
"""R100-1b テスト用: スマホのパスキー（WebAuthn・ES256）の代わり（テスト専用・本番コードから import しない）。

実機の navigator.credentials.create/get と同じ形の値（authenticatorData・clientDataJSON・DER 署名・SPKI）を
P-256 の秘密鍵から作る。署名は server/p256_verify.py の曲線演算を使う（検証側と同じ式で作ると
「両方同じ間違い」になりうる＝KAT（RFC 6979）と Chrome の仮想認証器の実値で別途固定している）。
"""
import hashlib
import json
import secrets
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "server"))
import p256_verify as pv  # noqa: E402

AAGUID = b"\x00" * 16


def b64u(raw):
    import base64
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _der_int(v):
    b = v.to_bytes((v.bit_length() + 7) // 8 or 1, "big")
    if b[0] & 0x80:
        b = b"\x00" + b
    return b"\x02" + bytes([len(b)]) + b


def der_sig(r, s):
    body = _der_int(r) + _der_int(s)
    return b"\x30" + bytes([len(body)]) + body


def sign_digest(d, digest):
    e = int.from_bytes(digest, "big")
    while True:
        k = secrets.randbelow(pv.N - 1) + 1
        r = pv._mul(k, pv.G)[0] % pv.N
        s = pow(k, -1, pv.N) * (e + r * d) % pv.N
        if r and s:
            return r, s


class FakeAuthenticator:
    def __init__(self, rp_id, origin, d=None, cred_id=None, uv=True, up=True):
        self.rp_id, self.origin = rp_id, origin
        self.d = d or (secrets.randbelow(pv.N - 1) + 1)
        self.pub = pv._mul(self.d, pv.G)
        self.cred_id = cred_id or secrets.token_bytes(16)
        self.count = 0
        self.uv, self.up = uv, up

    @property
    def spki(self):
        return pv.SPKI_PREFIX + bytes.fromhex(pv.pub_hex(self.pub))

    def fp(self):
        return hashlib.sha256(self.cred_id + bytes.fromhex(pv.pub_hex(self.pub))).hexdigest()[:6]

    def _flags(self, at=False):
        return (0x01 if self.up else 0) | (0x04 if self.uv else 0) | (0x40 if at else 0)

    def _cdj(self, typ, challenge, origin=None):
        return json.dumps({"type": typ, "challenge": b64u(challenge), "origin": origin or self.origin,
                           "crossOrigin": False}, separators=(",", ":")).encode("utf-8")

    def create(self, challenge, origin=None):
        """navigator.credentials.create の戻り（attestation:"none"）。"""
        rph = hashlib.sha256(self.rp_id.encode()).digest()
        cose = b"\xa5\x01\x02\x03\x26\x20\x01\x21\x58\x20" + self.pub[0].to_bytes(32, "big") + \
               b"\x22\x58\x20" + self.pub[1].to_bytes(32, "big")
        ad = rph + bytes([self._flags(at=True)]) + struct.pack(">I", self.count) + AAGUID + \
            struct.pack(">H", len(self.cred_id)) + self.cred_id + cose
        return {"credId": b64u(self.cred_id), "spki": b64u(self.spki),
                "clientDataJSON": b64u(self._cdj("webauthn.create", challenge, origin)), "authData": b64u(ad)}

    def get(self, challenge, origin=None, count_step=0, rp_id=None, typ="webauthn.get"):
        """navigator.credentials.get の戻り（assertion）。Apple のパスキーは signCount が常に 0。"""
        self.count += count_step
        rph = hashlib.sha256((rp_id or self.rp_id).encode()).digest()
        ad = rph + bytes([self._flags()]) + struct.pack(">I", self.count)
        cdj = self._cdj(typ, challenge, origin)
        digest = hashlib.sha256(ad + hashlib.sha256(cdj).digest()).digest()
        r, s = sign_digest(self.d, digest)
        return {"credId": b64u(self.cred_id), "authData": b64u(ad), "clientDataJSON": b64u(cdj),
                "signature": b64u(der_sig(r, s))}
