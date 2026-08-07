#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""R42.2 ライセンス発行CLI（開発者専用・購入者のMacでは使わない）。

秘密鍵はリポジトリ外 ~/.claude/office_license_signing.json（600・{"n":hex,"e":int,"d":hex}）。
検証器は server/license.py（公開鍵ハードコード）＝発行後に必ず自己検証してから出力する。

使い方:
  python3 tools/license_sign.py gen-key            # 初回のみ（既存があれば拒否）
  python3 tools/license_sign.py issue --edition hybrid --email buyer@example.com
  python3 tools/license_sign.py issue --edition claude --email x@y --out /tmp/lic.json
  python3 tools/license_sign.py install --edition hybrid --email me@local   # 自Mac用=発行して
                                                     # ~/.claude/office_license.json へ直置き(600)
"""
import argparse
import hashlib
import json
import os
import re
import secrets
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "server"))
import license as liccheck  # noqa: E402  (server/license.py=検証器の正本)

SIGNING_KEY = Path(os.environ.get("OFFICE_LICENSE_SIGNING",
                                  str(Path.home() / ".claude" / "office_license_signing.json")))
LICENSE_OUT = Path.home() / ".claude" / "office_license.json"


def _load_key():
    if not SIGNING_KEY.exists():
        sys.exit(f"署名鍵がありません: {SIGNING_KEY}（先に gen-key）")
    k = json.loads(SIGNING_KEY.read_text(encoding="utf-8"))
    return int(str(k["n"]), 16), int(k["e"]), int(str(k["d"]), 16)


def gen_key(args):
    if SIGNING_KEY.exists() and not args.force:
        sys.exit(f"既に署名鍵があります: {SIGNING_KEY}（ローテするなら --force。全ライセンス再発行になる）")
    out = subprocess.run(["openssl", "genrsa", "2048"], capture_output=True, text=True, check=True)
    txt = subprocess.run(["openssl", "rsa", "-text", "-noout"], input=out.stdout,
                         capture_output=True, text=True, check=True).stdout
    def grab(label):
        m = re.search(label + r":\s*\n((?:\s+[0-9a-f:]+\n)+)", txt)
        return int(re.sub(r"[\s:]", "", m.group(1)), 16)
    n, d = grab("modulus"), grab("privateExponent")
    e = int(re.search(r"publicExponent: (\d+)", txt).group(1))
    SIGNING_KEY.parent.mkdir(parents=True, exist_ok=True)
    SIGNING_KEY.write_text(json.dumps({"v": 1, "alg": "RS256",
                                       "n": hex(n), "e": e, "d": hex(d)}), encoding="utf-8")
    os.chmod(SIGNING_KEY, 0o600)
    print(f"署名鍵を保存: {SIGNING_KEY} (600)")
    print("server/license.py へ埋める公開鍵 PUBKEY_N:")
    print(hex(n))


def _sign(lic, n, d):
    digest = hashlib.sha256(liccheck.canonical(lic)).digest()
    klen = (n.bit_length() + 7) // 8
    em = int.from_bytes(liccheck._emsa_pkcs1_v15(digest, klen), "big")
    return format(pow(em, d, n), "x")


def build_license(edition, email, n, e, d, key_id=None, product=None):
    """R80: 既定で **v2（product入り）** を発行する。1組の署名鍵で複数プロダクト
    （AI Office / 他アプリ / 有料スキル）を扱うため、鍵がどの製品向けかを署名対象に含める。
    product=None は "ai-office"。v1（product無し）は既発行分の互換のためだけに残す。"""
    lic = {
        "v": 2,
        "product": product or liccheck.LEGACY_PRODUCT,
        "edition": edition,
        "key_id": key_id or ("manual-" + secrets.token_hex(4)),
        "issued": int(time.time()),
        "holder": hashlib.sha256(email.strip().lower().encode("utf-8")).hexdigest()[:12],
        "alg": "RS256",
    }
    lic["sig"] = _sign(lic, n, d)
    ok, reason = liccheck.verify_license(lic, n=n, e=e)
    if not ok:
        sys.exit(f"自己検証に失敗（鍵不整合の疑い）: {reason}")
    return lic


def issue(args, install=False):
    n, e, d = _load_key()
    if n != liccheck.PUBKEY_N:
        print("⚠ 署名鍵が server/license.py の PUBKEY_N と不一致（この出力は製品側で無効）",
              file=sys.stderr)
    lic = build_license(args.edition, args.email, n, e, d, key_id=args.key_id,
                        product=getattr(args, "product", None))
    body = json.dumps(lic, ensure_ascii=False, indent=2)
    if install:
        LICENSE_OUT.write_text(body, encoding="utf-8")
        os.chmod(LICENSE_OUT, 0o600)
        print(f"インストール: {LICENSE_OUT} (600・edition={args.edition})")
    elif args.out:
        Path(args.out).write_text(body, encoding="utf-8")
        print(f"発行: {args.out} (edition={args.edition} holder={lic['holder']})")
    else:
        print(body)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    g = sub.add_parser("gen-key")
    g.add_argument("--force", action="store_true")
    for name in ("issue", "install"):
        p = sub.add_parser(name)
        p.add_argument("--edition", required=True, choices=list(liccheck.VALID_LICENSE_EDITIONS))
        p.add_argument("--email", required=True)
        p.add_argument("--key-id", default=None)
        # R80: 1組の署名鍵で複数プロダクトを扱う（鍵の使い回しを署名で禁じる）
        p.add_argument("--product", default=None,
                       help="対象プロダクト（既定 ai-office。例: sakutto-editor / skill-xxx）")
        if name == "issue":
            p.add_argument("--out", default=None)
    args = ap.parse_args()
    if args.cmd == "gen-key":
        gen_key(args)
    else:
        issue(args, install=(args.cmd == "install"))


if __name__ == "__main__":
    main()
