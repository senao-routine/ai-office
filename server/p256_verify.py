# -*- coding: utf-8 -*-
"""R100-1b: P-256（secp256r1）ECDSA の**検証だけ**（標準ライブラリのみ・server/ は flat）。

スマホから許可（R100-1b）の内側の署名＝WebAuthn（パスキー・ES256）の assertion を Mac で確かめる。
入力は全部公開の値（公開鍵・署名・署名対象）＝定数時間の配慮は要らない。秘密鍵は Mac に来ない。

- verify_digest(pub, digest32, r, s): 数値の r, s で検証（RFC 6979 A.2.5 の固定ベクタで検算）
- parse_der_sig(der) → (r, s): WebAuthn の署名は ASN.1 DER の SEQUENCE { INTEGER r, INTEGER s }
- parse_spki(spki) → (x, y): PublicKeyCredential.getPublicKey() の SPKI（P-256 は 26 バイトの固定接頭辞＋04‖X‖Y）
- verify_webauthn(pub, authData, clientDataJSON, der_sig): 署名対象は authData ‖ SHA-256(clientDataJSON) の SHA-256
★曲線上にない点・r/s の範囲外・無限遠点は全部 False（例外で通さない）。Python 3.9 の構文。
"""
import hashlib

P = 0xFFFFFFFF00000001000000000000000000000000FFFFFFFFFFFFFFFFFFFFFFFF
A = P - 3
B = 0x5AC635D8AA3A93E7B3EBBD55769886BC651D06B0CC53B0F63BCE3C3E27D2604B
N = 0xFFFFFFFF00000000FFFFFFFFFFFFFFFFBCE6FAADA7179E84F3B9CAC2FC632551
G = (0x6B17D1F2E12C4247F8BCE6E563A440F277037D812DEB33A0F4A13945D898C296,
     0x4FE342E2FE1A7F9B8EE7EB4A7C0F9E162BCE33576B315ECECBB6406837BF51F5)
SPKI_PREFIX = bytes.fromhex("3059301306072a8648ce3d020106082a8648ce3d030107034200")


def on_curve(pt):
    if pt is None:
        return False
    x, y = pt
    if not (0 <= x < P and 0 <= y < P):
        return False
    return (y * y - (x * x * x + A * x + B)) % P == 0


def _add(p1, p2):
    if p1 is None:
        return p2
    if p2 is None:
        return p1
    (x1, y1), (x2, y2) = p1, p2
    if x1 == x2:
        if (y1 + y2) % P == 0:
            return None
        lam = (3 * x1 * x1 + A) * pow(2 * y1, -1, P) % P
    else:
        lam = (y2 - y1) * pow(x2 - x1, -1, P) % P
    x3 = (lam * lam - x1 - x2) % P
    return (x3, (lam * (x1 - x3) - y1) % P)


def _mul(k, pt):
    out = None
    while k:
        if k & 1:
            out = _add(out, pt)
        pt = _add(pt, pt)
        k >>= 1
    return out


def verify_digest(pub, digest, r, s):
    """pub=(x, y)・digest=32 バイト・r/s=整数。正しい署名だけ True。"""
    try:
        if not (isinstance(r, int) and isinstance(s, int) and 0 < r < N and 0 < s < N):
            return False
        if not on_curve(pub) or len(digest) != 32:
            return False
        e = int.from_bytes(digest, "big")
        w = pow(s, -1, N)
        pt = _add(_mul(e * w % N, G), _mul(r * w % N, pub))
        return pt is not None and pt[0] % N == r
    except (TypeError, ValueError, ZeroDivisionError):
        return False


def parse_der_sig(der):
    """DER の ECDSA 署名 → (r, s)。形が違えば ValueError。"""
    b = bytes(der)
    if len(b) < 8 or b[0] != 0x30 or b[1] != len(b) - 2:
        raise ValueError("der: sequence")
    i, out = 2, []
    for _ in range(2):
        if i + 2 > len(b) or b[i] != 0x02:
            raise ValueError("der: integer")
        n = b[i + 1]
        if n == 0 or n > 33 or i + 2 + n > len(b):
            raise ValueError("der: length")
        v = b[i + 2:i + 2 + n]
        if v[0] & 0x80 or (n > 1 and v[0] == 0 and not v[1] & 0x80):
            raise ValueError("der: not minimal")
        out.append(int.from_bytes(v, "big"))
        i += 2 + n
    if i != len(b):
        raise ValueError("der: trailing")
    return out[0], out[1]


def parse_spki(spki):
    """SPKI（DER）→ (x, y)。P-256 の非圧縮点だけ受け付ける。"""
    b = bytes(spki)
    if len(b) != len(SPKI_PREFIX) + 65 or not b.startswith(SPKI_PREFIX) or b[len(SPKI_PREFIX)] != 0x04:
        raise ValueError("spki: not an uncompressed P-256 key")
    raw = b[len(SPKI_PREFIX) + 1:]
    pt = (int.from_bytes(raw[:32], "big"), int.from_bytes(raw[32:], "big"))
    if not on_curve(pt):
        raise ValueError("spki: not on curve")
    return pt


def pub_hex(pt):
    """台帳に置く形（04‖X‖Y の 16 進・130 文字）。"""
    return "04" + pt[0].to_bytes(32, "big").hex() + pt[1].to_bytes(32, "big").hex()


def pub_from_hex(h):
    raw = bytes.fromhex(h)
    if len(raw) != 65 or raw[0] != 4:
        raise ValueError("pub: bad length")
    pt = (int.from_bytes(raw[1:33], "big"), int.from_bytes(raw[33:], "big"))
    if not on_curve(pt):
        raise ValueError("pub: not on curve")
    return pt


def verify_webauthn(pub, auth_data, client_data_json, der_sig):
    """WebAuthn の assertion（ES256）。署名対象 = authData ‖ SHA-256(clientDataJSON)。"""
    try:
        r, s = parse_der_sig(der_sig)
    except ValueError:
        return False
    msg = bytes(auth_data) + hashlib.sha256(bytes(client_data_json)).digest()
    return verify_digest(pub, hashlib.sha256(msg).digest(), r, s)
