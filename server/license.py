# -*- coding: utf-8 -*-
"""R42.2 オフライン買い切りライセンスの検証器（標準ライブラリのみ・server/同dir）。

方式= RSA-2048 / PKCS#1 v1.5 / SHA-256。検証は pow(sig, e, n) → EMSA-PKCS1-v1_5
バイト列の完全一致比較のみ（外部依存・vendoring不要・約40行）。
署名canonicalは office_server.sign_envelope（P3のHMAC封筒）とは**別系統・別モジュール**
＝既存の KAT_SIG / js_sign_kat.mjs には一切触れない（R42.2受け入れ条件）。

ライセンスJSON（~/.claude/office_license.json・600・買い切り=有効期限なし）:
  {"v":1, "edition":"claude"|"hybrid", "key_id":"ls_xxx", "issued":<unix秒int>,
   "holder":"<購入者メールsha256先頭12hex>", "alg":"RS256", "sig":"<hex>"}
発行= tools/license_sign.py（秘密鍵= ~/.claude/office_license_signing.json・リポ外600）。
クラック耐性は買い切り個人devツール相応（検証集約が目的・DRMではない）。
"""
import hashlib

# 本番公開鍵（2026-07-26生成・秘密鍵はリポジトリ外）。ローテ=差し替え+全ライセンス再発行。
PUBKEY_N = int(
    "c5a8f0c093cead0e4e6e105c994fad726c92e3bc29a9a1bebbf3ad1d2da307d5"
    "6acc84c6e661c2cb95ac3616c68258ff4a03cae0b381a4ff368ca22eb107e2eb"
    "15997841130b8721c98253abcf0e4fa49c9b34da23c1b60faccf6e7f6f400643"
    "ced1d69c7e6de1ac7f3c8b00ac2743de75be87400fd3c9c6f584fce371d6cc30"
    "1fe9275e8b06a36f900185ceeeee135b70ca81cbb17dd035ccaefbba01b9c586"
    "d89a8e450d1cbdc6879e5c32cd9684ce2423a10144103079ee9918ad1c6168ee"
    "fc6afb82da47678a4a4f976da0f791a316e327364cc9cd5d0dba92598146f4fe"
    "ed1284833260ef577d7a3d7fdffabe3055e298da35f6e7ee3767450714ee18cd", 16)
PUBKEY_E = 65537
_SHA256_DIGESTINFO = bytes.fromhex("3031300d060960864801650304020105000420")

# ライセンスが名乗れるedition（openclaw版は無料＝ライセンス対象外）
VALID_LICENSE_EDITIONS = ("claude", "hybrid")


# R80: 複数プロダクト（AI Office / 他アプリ / 有料スキル）を **1組の署名鍵**で扱うため
# product を導入した。canonical は署名対象なので**既存の v1 を一切変えず**、
# v2 を足して版で分岐する（ライセンスJSONは元から "v" を持ち verify が版を検査している＝
# 版交渉の仕組みが最初からある。指示封筒の canonical とは違い、ここは安全に拡張できる）。
#   v1（〜R80）: product を持たない ＝ **AI Office 専用**として扱う（既発行分の互換）
#   v2（R80〜）: product を署名対象に含む ＝ 鍵1枚が別プロダクトを開けない
LEGACY_PRODUCT = "ai-office"


def canonical(lic):
    """署名対象のcanonical bytes（\\n区切り・版ごとに行数固定）。issuedはintへ正規化して
    JSONの数値表記ゆれを吸収する。検証はバイト完全一致なので改竄は署名不一致になる。"""
    try:
        ver = int(lic.get("v") or 1)
    except (TypeError, ValueError):
        ver = 1
    if ver >= 2:
        return "\n".join([
            "aioffice-license", "v2",
            str(lic.get("product") or ""),
            str(lic.get("edition") or ""),
            str(lic.get("key_id") or ""),
            str(int(lic.get("issued") or 0)),
            str(lic.get("holder") or ""),
        ]).encode("utf-8")
    return "\n".join([
        "aioffice-license", "v1",
        str(lic.get("edition") or ""),
        str(lic.get("key_id") or ""),
        str(int(lic.get("issued") or 0)),
        str(lic.get("holder") or ""),
    ]).encode("utf-8")


def license_product(lic):
    """この鍵が対象とするプロダクト。v1（product無し）は AI Office 専用として扱う。"""
    if not isinstance(lic, dict):
        return ""
    return str(lic.get("product") or "") or LEGACY_PRODUCT


def _emsa_pkcs1_v15(digest, klen):
    pad = klen - len(_SHA256_DIGESTINFO) - len(digest) - 3
    return b"\x00\x01" + b"\xff" * pad + b"\x00" + _SHA256_DIGESTINFO + digest


def verify_license(lic, n=None, e=None):
    """(valid: bool, reason: str)。n/e はテスト鍵の注入口（未指定=本番公開鍵）。"""
    n = PUBKEY_N if n is None else n
    e = PUBKEY_E if e is None else e
    if not isinstance(lic, dict):
        return False, "ライセンス形式が不正です"
    if lic.get("v") not in (1, 2) or lic.get("alg") != "RS256":
        return False, "バージョン/署名方式が未対応です"
    # v2 は product を必須にする（空だと「どの製品の鍵か」が定まらない）
    if lic.get("v") == 2 and not str(lic.get("product") or "").strip():
        return False, "productが指定されていません"
    if lic.get("edition") not in VALID_LICENSE_EDITIONS:
        return False, "editionが不正です"
    try:
        issued = int(lic.get("issued") or 0)
    except (TypeError, ValueError):
        return False, "issuedが不正です"
    if issued <= 0:
        return False, "issuedが不正です"
    try:
        sig = int(str(lic.get("sig") or ""), 16)
    except ValueError:
        return False, "署名が16進ではありません"
    if not (0 < sig < n):
        return False, "署名が鍵の範囲外です"
    klen = (n.bit_length() + 7) // 8
    em = pow(sig, e, n).to_bytes(klen, "big")
    digest = hashlib.sha256(canonical(lic)).digest()
    if em != _emsa_pkcs1_v15(digest, klen):
        return False, "署名が一致しません"
    return True, ""
