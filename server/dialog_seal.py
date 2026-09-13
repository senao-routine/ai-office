#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""R87「封書」— 会話をスマホ端末だけが開ける形に封じる。標準ライブラリのみ。

**この機能が守るのは「中継（Cloudflare）に読ませないこと」だけ**。自分の Mac に対する
秘匿ではないし、端末を落とせば読まれる。正直な主張は README と docs に同じ文言で置く。

設計の要:
- 鍵は既存のペアリング秘密（`~/.claude/office_devices.json` の 256bit）から HKDF で導出する。
  **新しい秘密を配らない・ペアリングを変えない**。用途分離のため署名鍵とは別のラベルを噛ませる。
- 1応答＝1鍵（salt が毎回新規・reqId が使い捨て）。「同じ鍵で2回暗号化しない」と言い切れる。
- 暗号は **AES-256-GCM**（macOS CommonCrypto を ctypes 経由）。自前のストリーム暗号は作らない
  ＝「AES-256-GCM で暗号化しています」と正直に書ける構成にする。
- 使えない環境では `AVAILABLE=False` にして**機能ごと消す**。平文フォールバックは実装しない
  （壊れ方は「読めない」であって「漏れる」ではない）。
- 長さは 8KB 単位までしか漏らさない（固定長フレーム）。中身以外は隠せないと明記する。

番人: `tools/check_cc_gcm.py`（verify ▶12）がシンボル解決・NIST 一致・改竄拒否を毎回見る。
"""
import base64
import ctypes
import hashlib
import hmac
import json
import os
import re
import struct
import threading
import time
import zlib
from pathlib import Path

# ── フレームと封筒の定数（JS 側 ui/core/dialog_open.js と必ず一対） ──────────
MAGIC = b"AO1"
IV_LEN, TAG_LEN, BLOCK = 12, 16, 8192
FRAME_LEN = len(MAGIC) + IV_LEN + BLOCK + TAG_LEN      # 8223
MAX_FRAMES = 4                                         # v1 の遠隔は depth0＝実測1フレーム
LABEL = b"aioffice-dialog\nv1\n"
ROOT_LABEL = b"aioffice-dialog-root/v1"                # 署名 canonical と構造的に衝突しない
COMPRESSION = "zl"                                     # zlib(RFC1950)＝JS の deflate
BUNDLE_TTL = 90                                        # 画面を閉じた人に後から届けない
REQID_RE = re.compile(r"^[0-9a-f]{32}$")
# err は固定 enum のみ（自由文字列を作らない＝scrub できない値を中継に出さない）
ERRORS = ("unavailable", "denied", "notfound", "toolarge", "expired")

_KCC_AES, _KCC_MODE_GCM, _KCC_ENCRYPT, _KCC_DECRYPT = 0, 11, 0, 1


# ── CommonCrypto バックエンド ──────────────────────────────────────────────
def _load_backend(only=None):
    """(name, encrypt, decrypt) か None。import 時に NIST ベクタで検算して選ぶ。

    `only` はテスト用の絞り込み（"oneshot"/"mode"）。**片方だけを名指しで検算できないと、
    「両方のバックエンドを公式ベクタで見た」と言えない**（選択順に隠れて片方が未検証になる）。
    """
    try:
        lib = ctypes.CDLL("/usr/lib/libSystem.B.dylib")
    except OSError:
        return None

    def bind_oneshot():
        if not (hasattr(lib, "CCCryptorGCMOneshotEncrypt")
                and hasattr(lib, "CCCryptorGCMOneshotDecrypt")):
            return None
        for name in ("CCCryptorGCMOneshotEncrypt", "CCCryptorGCMOneshotDecrypt"):
            fn = getattr(lib, name)
            fn.restype = ctypes.c_int32
            # ★第1引数は alg(AES=0)。op と取り違えると DES 扱いで -4300
            fn.argtypes = [ctypes.c_uint32, ctypes.c_void_p, ctypes.c_size_t,
                           ctypes.c_void_p, ctypes.c_size_t, ctypes.c_void_p, ctypes.c_size_t,
                           ctypes.c_void_p, ctypes.c_size_t, ctypes.c_void_p,
                           ctypes.c_void_p, ctypes.c_size_t]

        def enc(key, iv, aad, pt):
            out = ctypes.create_string_buffer(max(len(pt), 1))
            tag = ctypes.create_string_buffer(TAG_LEN)
            rc = lib.CCCryptorGCMOneshotEncrypt(_KCC_AES, key, len(key), iv, len(iv),
                                                aad, len(aad), pt, len(pt), out, tag, TAG_LEN)
            if rc != 0:
                raise OSError(f"oneshot encrypt rc={rc}")
            return out.raw[:len(pt)], tag.raw

        def dec(key, iv, aad, ct, tag):
            # ★tag は**入力**で、CommonCrypto が内部で照合する（実測）。
            # 「タグを受け取る out 引数」と誤解すると未初期化バッファと比べる実装になる。
            out = ctypes.create_string_buffer(max(len(ct), 1))
            tag_in = ctypes.create_string_buffer(bytes(tag), TAG_LEN)
            rc = lib.CCCryptorGCMOneshotDecrypt(_KCC_AES, key, len(key), iv, len(iv),
                                                aad, len(aad), ct, len(ct), out, tag_in, TAG_LEN)
            if rc != 0:
                raise ValueError(f"authentication failed (rc={rc})")
            return out.raw[:len(ct)]

        return "oneshot", enc, dec

    def bind_mode():
        need = ("CCCryptorCreateWithMode", "CCCryptorGCMEncrypt", "CCCryptorGCMDecrypt",
                "CCCryptorGCMFinal", "CCCryptorRelease")
        if not all(hasattr(lib, n) for n in need):
            return None
        setiv_name = "CCCryptorGCMSetIV" if hasattr(lib, "CCCryptorGCMSetIV") else "CCCryptorGCMAddIV"
        aad_name = "CCCryptorGCMAddAAD" if hasattr(lib, "CCCryptorGCMAddAAD") else "CCCryptorGCMaddAAD"
        if not (hasattr(lib, setiv_name) and hasattr(lib, aad_name)):
            return None

        def run(op, key, iv, aad, data):
            ref = ctypes.c_void_p()
            create = lib.CCCryptorCreateWithMode
            create.restype = ctypes.c_int32
            create.argtypes = [ctypes.c_uint32] * 4 + [
                ctypes.c_void_p, ctypes.c_void_p, ctypes.c_size_t,
                ctypes.c_void_p, ctypes.c_size_t, ctypes.c_int, ctypes.c_uint32,
                ctypes.POINTER(ctypes.c_void_p)]
            rc = create(op, _KCC_MODE_GCM, _KCC_AES, 0,
                        None, key, len(key), None, 0, 0, 0, ctypes.byref(ref))
            if rc != 0:
                raise OSError(f"CCCryptorCreateWithMode rc={rc}")
            try:
                for name, buf, n in ((setiv_name, iv, len(iv)), (aad_name, aad, len(aad))):
                    if n == 0:
                        continue
                    fn = getattr(lib, name)
                    fn.restype = ctypes.c_int32
                    fn.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_size_t]
                    rc = fn(ref, buf, n)
                    if rc != 0:
                        raise OSError(f"{name} rc={rc}")
                out = ctypes.create_string_buffer(max(len(data), 1))
                step = lib.CCCryptorGCMEncrypt if op == _KCC_ENCRYPT else lib.CCCryptorGCMDecrypt
                step.restype = ctypes.c_int32
                step.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_size_t, ctypes.c_void_p]
                rc = step(ref, data, len(data), out)
                if rc != 0:
                    raise OSError(f"GCM step rc={rc}")
                tag = ctypes.create_string_buffer(TAG_LEN)
                size = ctypes.c_size_t(TAG_LEN)
                # ★`CCCryptorGCMFinalize` はシンボルが在っても常に -4300。`Final` が正しい（実測）
                fin = lib.CCCryptorGCMFinal
                fin.restype = ctypes.c_int32
                fin.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.POINTER(ctypes.c_size_t)]
                rc = fin(ref, tag, ctypes.byref(size))
                if rc != 0:
                    raise OSError(f"CCCryptorGCMFinal rc={rc}")
                return out.raw[:len(data)], tag.raw[:TAG_LEN]
            finally:
                lib.CCCryptorRelease(ref)

        def enc(key, iv, aad, pt):
            return run(_KCC_ENCRYPT, key, iv, aad, pt)

        def dec(key, iv, aad, ct, tag):
            # ★公開APIは復号時もタグを**出力するだけ**。照合はこちらの責任。
            pt, got = run(_KCC_DECRYPT, key, iv, aad, ct)
            if not hmac.compare_digest(got, bytes(tag)):
                raise ValueError("authentication failed (tag mismatch)")
            return pt

        return "mode", enc, dec

    # NIST SP800-38D AES-256 TC13/TC14。通った実装だけを採る（黙って壊れた暗号を使わない）
    for want, bind in (("oneshot", bind_oneshot), ("mode", bind_mode)):
        if only and want != only:
            continue
        try:
            got = bind()
        except (AttributeError, OSError):
            continue
        if not got:
            continue
        name, enc, dec = got
        try:
            _, tag13 = enc(b"\0" * 32, b"\0" * 12, b"", b"")
            ct14, tag14 = enc(b"\0" * 32, b"\0" * 12, b"", b"\0" * 16)
        except (OSError, ValueError):
            continue
        if (tag13.hex() == "530f8afbc74536b9a963b4f1c4cb738b"
                and ct14.hex() == "cea7403d4d606b6e074ec5d3baf39d18"
                and tag14.hex() == "d0d1c8a799996bf0265b98b5d48ab919"):
            return name, enc, dec
    return None


_BACKEND = _load_backend()
BACKEND = _BACKEND[0] if _BACKEND else ""
AVAILABLE = _BACKEND is not None


# ── 鍵導出 ────────────────────────────────────────────────────────────────
def hkdf_sha256(salt, ikm, info, length=32):
    """RFC5869。**出力は1ブロック(32B)まで**＝SHA-256 の 255×32 上限に原理的に触れない。
    keystream 用途への流用は禁止（台帳の落とし穴）。"""
    if length > 32:
        raise ValueError("この用途では 32 バイトを超える導出をしない")
    prk = hmac.new(salt, ikm, hashlib.sha256).digest()
    return hmac.new(prk, info + b"\x01", hashlib.sha256).digest()[:length]


def derive_key(secret_hex, salt, device_id, req_id):
    """署名鍵と会話鍵を**用途で分ける**。会話鍵が全部漏れても元の秘密は復元できない。"""
    secret = bytes.fromhex(secret_hex)
    root = hmac.new(secret, ROOT_LABEL, hashlib.sha256).digest()
    info = LABEL + device_id.encode("utf-8") + b"\n" + req_id.encode("utf-8")
    return hkdf_sha256(salt, root, info)


def aad_for(device_id, req_id, target_session, index, frames, iat):
    """フレームを「どの端末の・どの要求の・どのセッションの・何番目か」に縛る。
    すり替え・並べ替え・別要求への転用はここで復号が失敗する。"""
    return (LABEL + device_id.encode("utf-8") + b"\n" + req_id.encode("utf-8") + b"\n"
            + target_session.encode("utf-8") + b"\n" + str(index).encode()
            + b"\n" + str(frames).encode() + b"\n" + str(iat).encode()
            + b"\n" + COMPRESSION.encode())


def b64u(raw):
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def unb64u(text):
    pad = "=" * (-len(text) % 4)
    return base64.urlsafe_b64decode(text + pad)


# ── 封緘 ──────────────────────────────────────────────────────────────────
def seal(secret_hex, device_id, req_id, target_session, payload, iat=None,
         salt=None, ivs=None):
    """payload(dict) を封じて bundle(dict) を返す。salt/ivs はテスト専用の注入口。

    平文は「4バイト長 + zlib + ゼロ埋め」を 8192B 単位に固定長化してからフレームに切る
    ＝中継に漏れるのは 8KB 粒度の長さだけ。
    """
    if not AVAILABLE:
        raise RuntimeError("封緘バックエンドがありません")
    if not REQID_RE.match(req_id or ""):
        raise ValueError("invalid reqId")
    iat = int(iat if iat is not None else time.time())
    salt = salt if salt is not None else os.urandom(16)
    key = derive_key(secret_hex, salt, device_id, req_id)

    body = zlib.compress(json.dumps(payload, ensure_ascii=False).encode("utf-8"), 6)
    plain = struct.pack(">I", len(body)) + body
    frames = (len(plain) + BLOCK - 1) // BLOCK
    if frames > MAX_FRAMES:
        raise ValueError("toolarge")
    plain += b"\0" * (frames * BLOCK - len(plain))

    out = bytearray()
    for i in range(frames):
        iv = ivs[i] if ivs else os.urandom(IV_LEN)
        aad = aad_for(device_id, req_id, target_session, i, frames, iat)
        ct, tag = _BACKEND[1](key, iv, aad, bytes(plain[i * BLOCK:(i + 1) * BLOCK]))
        out += MAGIC + iv + ct + tag
    return {"v": 1, "id": req_id, "s": b64u(salt), "i": iat, "e": iat + BUNDLE_TTL,
            "n": frames, "c": COMPRESSION, "b": b64u(bytes(out))}


def unseal(secret_hex, device_id, target_session, bundle):
    """テストと E2E のための復号（本番の復号は端末側 ui/core/dialog_open.js）。"""
    if not AVAILABLE:
        raise RuntimeError("封緘バックエンドがありません")
    req_id, iat, frames = bundle["id"], int(bundle["i"]), int(bundle["n"])
    key = derive_key(secret_hex, unb64u(bundle["s"]), device_id, req_id)
    raw = unb64u(bundle["b"])
    if len(raw) != frames * FRAME_LEN:
        raise ValueError("frame length mismatch")
    plain = bytearray()
    for i in range(frames):
        f = raw[i * FRAME_LEN:(i + 1) * FRAME_LEN]
        if f[:len(MAGIC)] != MAGIC:
            raise ValueError("bad magic")
        iv = f[len(MAGIC):len(MAGIC) + IV_LEN]
        ct = f[len(MAGIC) + IV_LEN:-TAG_LEN]
        tag = f[-TAG_LEN:]
        aad = aad_for(device_id, req_id, target_session, i, frames, iat)
        plain += _BACKEND[2](key, iv, aad, ct, tag)
    size = struct.unpack(">I", bytes(plain[:4]))[0]
    return json.loads(zlib.decompress(bytes(plain[4:4 + size])).decode("utf-8"))


# ── 封じたものを relay_agent（別プロセス）へ渡す置き場 ─────────────────────────
# ★メモリでは渡らない。daemon（封じる側）と relay_agent（中継へ載せる側）は別の LaunchAgent
#   （docs/r87-facts.md §5・R92 で実証済みの罠）。レシピや実行結果と同じく **ファイル**で渡す。
#   書き手は daemon だけ（単一書き手＝flock 不要・os.replace で原子置換）。読み手はどのプロセスでも。
#   置きっぱなしにしない＝TTL を過ぎた行は読み出しで落ち、次の書き込みで消える（紛失端末が後から拾えない）。
_HOME = Path(os.environ.get("OFFICE_HOME", str(Path.home())))
BUNDLES_FILE = _HOME / ".claude" / "office_dialog_bundles.json"
BUNDLE_TTL = int(os.environ.get("OFFICE_DIALOG_TTL", "90"))   # 画面を閉じた人に後から届けない
MAX_LIVE = 4                                                   # 同時に生きている封書の上限
_LOCK = threading.Lock()
_RESERVED = {}          # reqId -> 予約の期限（このプロセスで封緘中＝再配達で二重に封じない）
_FILE_CACHE = {"key": None, "rows": []}


def _read_file():
    """ファイルの生の行（期限は呼び出し側で見る）。壊れていても落ちない（R92 と同じ）。"""
    try:
        st = BUNDLES_FILE.stat()
    except OSError:
        return None                       # 一度も使っていない
    key = (st.st_mtime_ns, st.st_size)
    if _FILE_CACHE["key"] == key:
        return list(_FILE_CACHE["rows"])
    rows = []
    try:
        obj = json.loads(BUNDLES_FILE.read_text(encoding="utf-8"))
        raw = obj.get("bundles") if isinstance(obj, dict) else None
        for b in (raw if isinstance(raw, list) else [])[:MAX_LIVE * 2]:
            e = b.get("e") if isinstance(b, dict) else None
            # ★期限は**範囲で**検算する。JSON は 1e309 を inf として通し、inf > now は永遠に真
            #   ＝不死の封書になる（R92 で Astra が同じ穴を実測した）。
            if (isinstance(b, dict) and isinstance(b.get("id"), str) and REQID_RE.match(b["id"])
                    and isinstance(e, (int, float)) and not isinstance(e, bool)
                    and 0 < e < 4102444800):            # 2100-01-01 より先は壊れた値
                rows.append(b)
    except (OSError, UnicodeError, ValueError, TypeError):
        rows = []
    _FILE_CACHE["key"] = key
    _FILE_CACHE["rows"] = rows
    return list(rows)


def _persist_locked(rows):
    """_LOCK を持ったまま呼ぶ（R92 と同じ理由＝書き込み順が入れ替わると古い版が勝つ）。"""
    BUNDLES_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = BUNDLES_FILE.with_name(BUNDLES_FILE.name + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump({"v": 1, "bundles": rows}, f, ensure_ascii=False)
    os.replace(tmp, BUNDLES_FILE)


def _live(rows, now):
    return [b for b in (rows or []) if b.get("e", 0) > now]


def reserve_dialog(req_id):
    """(既存bundle, 新規か)。同じ reqId の再配達で**二重に読み直さない・二重に封じない**。
    ファイルを先に見るので daemon が再起動しても冪等（メモリの予約は封緘中の分だけ）。"""
    with _LOCK:
        now = time.time()
        for b in _live(_read_file(), now):
            if b.get("id") == req_id:
                return b, False
        for k in [k for k, exp in _RESERVED.items() if exp <= now]:
            del _RESERVED[k]
        if req_id in _RESERVED:
            return None, False            # 封緘中（結果は次の周に出る）
        _RESERVED[req_id] = now + BUNDLE_TTL
        return None, True


def store_bundle(req_id, bundle):
    """封じたもの（err 封筒も）を保存。期限切れは同時に落とし、上限を超えた古い分も捨てる。"""
    with _LOCK:
        now = time.time()
        rows = [b for b in _live(_read_file(), now) if b.get("id") != req_id]
        rows.append(bundle)
        rows = rows[-MAX_LIVE:]
        try:
            _persist_locked(rows)
        finally:
            _RESERVED.pop(req_id, None)


def live_bundles():
    """relay_agent が読む口。None=一度も使っていない（何も送らない）／[]=全部期限切れ（空を1回送って消す）。"""
    with _LOCK:
        rows = _read_file()
        if rows is None:
            return None
        return _live(rows, time.time())


def live_bundle(req_id):
    for b in (live_bundles() or []):
        if b.get("id") == req_id:
            return b
    return None


def sweep():
    """期限切れを物理的に消す（daemon が定期に呼んでよい。呼ばなくても読み出しでは落ちる）。"""
    with _LOCK:
        rows = _read_file()
        if rows is None:
            return
        alive = _live(rows, time.time())
        if len(alive) != len(rows):
            _persist_locked(alive)


def selftest():
    """import 時ではなく明示的に呼ぶ自己診断（verify と daemon 起動ログ用）。"""
    if not AVAILABLE:
        return False, "CommonCrypto の AES-256-GCM が使えません"
    try:
        secret = "11" * 32
        page = {"messages": [{"role": "user", "text": "こんにちは"}]}
        b = seal(secret, "dev-selftest", "0" * 32, "sess-selftest", page)
        if unseal(secret, "dev-selftest", "sess-selftest", b) != page:
            return False, "往復で内容が一致しない"
    except (OSError, ValueError, RuntimeError) as e:
        return False, str(e)
    return True, BACKEND
