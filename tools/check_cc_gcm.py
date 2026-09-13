#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""R87-H0: 封書の土台（macOS CommonCrypto の AES-256-GCM）が本当に使えるかの番人。

`server/` は標準ライブラリのみという不変条件を守ったまま AES-256-GCM を使うため、
`ctypes` で libSystem の CommonCrypto を叩く。**`check_stdlib.py` は import 名しか
見ないのでこの依存を見張れない**（`pair_qr_svg` の vendored segno と同じ位置づけ）。
だからここで毎回、機械が次を確かめる:

  1. 2つのバックエンドのシンボルが解決できるか（one-shot SPI / 公開API のモード版）
  2. 解決できたバックエンドが **NIST GCM AES-256 TC13/TC14 とバイト一致**するか
  3. 鍵・IV・AAD・暗号文・タグの**どれを1バイト変えても復号が失敗**するか

終了コードは**理由で分ける**（呼び出し側が「使えない Mac」と「暗号が壊れている」を
取り違えないため。全部まとめて警告にすると、KAT 不一致でも verify が合格してしまう）:
  0 … 合格
  2 … このMacに使えるバックエンドが無い（＝会話ビューアを出さないのが正しい姿）
  1 … **検算に失敗した**（NIST 不一致・改竄を検出できない・往復できない）＝止めるべき異常

使い方: python3 tools/check_cc_gcm.py        # 0=合格 / 2=非対応 / 1=異常
        python3 tools/check_cc_gcm.py -v     # 各項目を1行ずつ出す
"""
import binascii
import ctypes
import hmac
import sys

DYLIB = "/usr/lib/libSystem.B.dylib"
KCC_ALGORITHM_AES = 0
KCC_MODE_GCM = 11
KCC_ENCRYPT, KCC_DECRYPT = 0, 1

# NIST SP800-38D / gcmEncryptExtIV256.rsp — AES-256, 96bit IV
# TC13: 鍵もIVも全ゼロ・平文なし・AADなし
KAT13_TAG = "530f8afbc74536b9a963b4f1c4cb738b"
# TC14: 同じ鍵とIVで16バイトのゼロ平文
KAT14_CT = "cea7403d4d606b6e074ec5d3baf39d18"
KAT14_TAG = "d0d1c8a799996bf0265b98b5d48ab919"


def _lib():
    return ctypes.CDLL(DYLIB)


# ── backend A: one-shot（SPI・CommonCryptorSPI.h） ──────────────────────────
def oneshot_available(lib):
    return hasattr(lib, "CCCryptorGCMOneshotEncrypt") and hasattr(lib, "CCCryptorGCMOneshotDecrypt")


def _bind_oneshot(lib):
    for name in ("CCCryptorGCMOneshotEncrypt", "CCCryptorGCMOneshotDecrypt"):
        fn = getattr(lib, name)
        fn.restype = ctypes.c_int32
        # ★第1引数は alg（AES=0）であって op ではない。1 を渡すと DES 扱いで -4300
        fn.argtypes = [ctypes.c_uint32,
                       ctypes.c_void_p, ctypes.c_size_t,     # key
                       ctypes.c_void_p, ctypes.c_size_t,     # iv
                       ctypes.c_void_p, ctypes.c_size_t,     # aad
                       ctypes.c_void_p, ctypes.c_size_t,     # in
                       ctypes.c_void_p,                      # out
                       ctypes.c_void_p, ctypes.c_size_t]     # tag
    return lib.CCCryptorGCMOneshotEncrypt, lib.CCCryptorGCMOneshotDecrypt


def oneshot_encrypt(lib, key, iv, aad, pt):
    enc, _ = _bind_oneshot(lib)
    out = ctypes.create_string_buffer(max(len(pt), 1))
    tag = ctypes.create_string_buffer(16)
    rc = enc(KCC_ALGORITHM_AES, key, len(key), iv, len(iv), aad, len(aad),
             pt, len(pt), out, tag, 16)
    if rc != 0:
        raise OSError(f"CCCryptorGCMOneshotEncrypt rc={rc}")
    return out.raw[:len(pt)], tag.raw


def oneshot_decrypt(lib, key, iv, aad, ct, tag):
    """復号する。★`tag` は**入力**で、CommonCrypto が内部で照合する（実測）。
    鍵・IV・AAD・暗号文・タグのどれが食い違っても rc≠0（-4308）で返り、
    平文は出てこない＝認証は OS 側が持つ。自前でタグを比較する実装にしないこと
    （タグを受け取る out パラメータだと誤解すると、未初期化バッファとの比較になる）。"""
    _, dec = _bind_oneshot(lib)
    out = ctypes.create_string_buffer(max(len(ct), 1))
    tag_in = ctypes.create_string_buffer(bytes(tag), 16)
    rc = dec(KCC_ALGORITHM_AES, key, len(key), iv, len(iv), aad, len(aad),
             ct, len(ct), out, tag_in, 16)
    if rc != 0:
        raise ValueError(f"authentication failed (rc={rc})")
    return out.raw[:len(ct)]


# ── backend B: 公開API（CCCryptorCreateWithMode + GCM 補助） ────────────────
# ★終端は `CCCryptorGCMFinal`。`CCCryptorGCMFinalize` は**シンボルは在るのに
# 必ず -4300(kCCParamError) を返す**（実測）ので、存在で選ぶと動かない。
_MODE_CORE = ("CCCryptorCreateWithMode", "CCCryptorGCMEncrypt", "CCCryptorGCMDecrypt",
              "CCCryptorGCMFinal", "CCCryptorRelease")


def mode_available(lib):
    return (all(hasattr(lib, n) for n in _MODE_CORE)
            and any(hasattr(lib, n) for n in ("CCCryptorGCMSetIV", "CCCryptorGCMAddIV"))
            and any(hasattr(lib, n) for n in ("CCCryptorGCMAddAAD", "CCCryptorGCMaddAAD")))


def _first(lib, *names):
    for n in names:
        if hasattr(lib, n):
            return getattr(lib, n), n
    raise OSError(f"どのシンボルも無い: {names}")


def _mode_call(lib, op, key, iv, aad, data):
    """GCM を1回分回して (出力, 計算されたタグ) を返す。encrypt/decrypt 共通。"""
    ref = ctypes.c_void_p()
    create = lib.CCCryptorCreateWithMode
    create.restype = ctypes.c_int32
    create.argtypes = [ctypes.c_uint32] * 4 + [
        ctypes.c_void_p, ctypes.c_void_p, ctypes.c_size_t,
        ctypes.c_void_p, ctypes.c_size_t, ctypes.c_int, ctypes.c_uint32,
        ctypes.POINTER(ctypes.c_void_p)]
    rc = create(op, KCC_MODE_GCM, KCC_ALGORITHM_AES, 0,
                None, key, len(key), None, 0, 0, 0, ctypes.byref(ref))
    if rc != 0:
        raise OSError(f"CCCryptorCreateWithMode rc={rc}")
    try:
        setiv, _ = _first(lib, "CCCryptorGCMSetIV", "CCCryptorGCMAddIV")
        setiv.restype = ctypes.c_int32
        setiv.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_size_t]
        rc = setiv(ref, iv, len(iv))
        if rc != 0:
            raise OSError(f"GCMSetIV rc={rc}")
        if aad:
            addaad, _ = _first(lib, "CCCryptorGCMAddAAD", "CCCryptorGCMaddAAD")
            addaad.restype = ctypes.c_int32
            addaad.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_size_t]
            rc = addaad(ref, aad, len(aad))
            if rc != 0:
                raise OSError(f"GCMAddAAD rc={rc}")
        out = ctypes.create_string_buffer(max(len(data), 1))
        step = lib.CCCryptorGCMEncrypt if op == KCC_ENCRYPT else lib.CCCryptorGCMDecrypt
        step.restype = ctypes.c_int32
        step.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_size_t, ctypes.c_void_p]
        rc = step(ref, data, len(data), out)
        if rc != 0:
            raise OSError(f"GCM{'Encrypt' if op == KCC_ENCRYPT else 'Decrypt'} rc={rc}")
        tag = ctypes.create_string_buffer(16)
        size = ctypes.c_size_t(16)
        fin = lib.CCCryptorGCMFinal
        fin.restype = ctypes.c_int32
        fin.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.POINTER(ctypes.c_size_t)]
        rc = fin(ref, tag, ctypes.byref(size))
        if rc != 0:
            raise OSError(f"CCCryptorGCMFinal rc={rc}")
        return out.raw[:len(data)], tag.raw[:16]
    finally:
        lib.CCCryptorRelease(ref)


def mode_encrypt(lib, key, iv, aad, pt):
    return _mode_call(lib, KCC_ENCRYPT, key, iv, aad, pt)


def mode_decrypt(lib, key, iv, aad, ct, tag):
    """★one-shot と違い、公開APIは復号時も**タグを出力するだけ**で照合しない。
    照合はこちら側の責任（忘れると認証の無い『ただの復号』になる）。"""
    pt, got = _mode_call(lib, KCC_DECRYPT, key, iv, aad, ct)
    if not hmac.compare_digest(got, bytes(tag)):
        raise ValueError("authentication failed (tag mismatch)")
    return pt


# name, 使えるか, 暗号化, 復号（復号は**認証まで**やる契約＝失敗は例外）
BACKENDS = (("oneshot", oneshot_available, oneshot_encrypt, oneshot_decrypt),
            ("mode", mode_available, mode_encrypt, mode_decrypt))


def main():
    verbose = "-v" in sys.argv
    lib = _lib()
    ok, fail = [], []
    key, iv = b"\0" * 32, b"\0" * 12

    usable = []
    for name, available, encrypt, _decrypt in BACKENDS:
        if not available(lib):
            fail.append(f"{name}: シンボルが解決できない")
            continue
        try:
            _, tag13 = encrypt(lib, key, iv, b"", b"")
            ct14, tag14 = encrypt(lib, key, iv, b"", b"\0" * 16)
        except OSError as e:
            fail.append(f"{name}: 実行に失敗 ({e})")
            continue
        got = (binascii.hexlify(tag13).decode(),
               binascii.hexlify(ct14).decode(), binascii.hexlify(tag14).decode())
        if got != (KAT13_TAG, KAT14_CT, KAT14_TAG):
            fail.append(f"{name}: NIST TC13/TC14 と不一致 {got}")
            continue
        usable.append(name)
        ok.append(f"{name}: NIST GCM AES-256 TC13/TC14 一致")

    if not usable:
        # 「シンボルが無い」だけなら非対応(2)。KAT 不一致や実行失敗が混じっていれば異常(1)。
        broken = [f for f in fail if "シンボルが解決できない" not in f]
        if broken:
            print("✗ 封緘の検算に失敗（暗号が壊れている可能性）")
            for f in broken:
                print(f"   - {f}")
            return 1
        print("ℹ 封緘バックエンドが無い Mac（会話ビューアは出さない）")
        for f in fail:
            print(f"   - {f}")
        return 2

    # 改竄検出は**使えるバックエンドすべてに掛ける**。one-shot は OS がタグを照合し、
    # 公開APIは呼び出し側が照合する＝壊れ方が違うので、片方だけ見ても保証にならない。
    k, i, a, plain = b"\x11" * 32, b"\x22" * 12, b"aad-fixed", b"hello sealed world"
    flip = lambda b: bytes([b[0] ^ 1]) + b[1:]
    for name, available, encrypt, decrypt in BACKENDS:
        if name not in usable:
            continue
        ct, tag = encrypt(lib, k, i, a, plain)
        try:
            if decrypt(lib, k, i, a, ct, tag) != plain:
                print(f"✗ {name}: 正しい入力で往復できない")
                return 1
        except (ValueError, OSError) as e:
            print(f"✗ {name}: 正しい入力の復号が失敗した ({e})")
            return 1
        ok.append(f"{name}: 往復（封じて開ける）")
        for label, args in (("鍵", (flip(k), i, a, ct, tag)),
                            ("IV", (k, flip(i), a, ct, tag)),
                            ("AAD", (k, i, b"aad-fixeD", ct, tag)),
                            ("暗号文", (k, i, a, flip(ct), tag)),
                            ("タグ", (k, i, a, ct, flip(tag)))):
            try:
                decrypt(lib, *args)
            except (ValueError, OSError):
                ok.append(f"{name}: {label} を1バイト変えると復号が失敗する")
            else:
                print(f"✗ {name}: {label} を変えても復号が通った（認証が効いていない）")
                return 1

    if verbose:
        for line in ok:
            print(f"  ✓ {line}")
        for line in fail:
            print(f"  ℹ 使えないバックエンド — {line}")
    print(f"✓ 封緘バックエンド: {', '.join(usable)}（NIST一致・改竄検出あり）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
