#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""OpenClaw（別Mac）用の指示送信CLI（P5） — 標準ライブラリのみ。

Discord承認後などに、中継Worker経由で AI Office の指定セッションへ指示を投函する。
真正性は per-device HMAC 署名（office UI で label=OpenClaw のデバイスを発行）＝P3の認証モデルを
そのまま使うので、中継のPOSTトークンが漏れても指示は偽造できない。

使い方:  python3 office_send.py <session> <text...>
設定:    ~/.claude/office_send.json (600) = {"url","post_token","device_id","secret"}
配布:    office_server.py（sign_envelope 正本）と本ファイルの2つを OpenClaw Mac へコピー。
"""
import json
import os
import secrets
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlparse


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """リダイレクトを追わない（別ホストへ Authorization ヘッダを転送させない＝post_token漏洩防止）。"""
    def redirect_request(self, *a, **k):
        return None

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "server"))
import office_server as office   # sign_envelope の単一正本を再利用（stdlibのみ）

CONFIG = Path(os.environ.get("OFFICE_HOME", str(Path.home()))) / ".claude" / "office_send.json"


def main():
    if len(sys.argv) < 3:
        print("使い方: office_send.py <session> <text...>", file=sys.stderr)
        sys.exit(2)
    session, text = sys.argv[1], " ".join(sys.argv[2:])
    try:
        c = json.loads(CONFIG.read_text(encoding="utf-8"))
        url = c["url"].rstrip("/")
        post_token, device_id, secret = c["post_token"], c["device_id"], c["secret"]
        if not all(isinstance(x, str) for x in (url, post_token, device_id, secret)):
            raise ValueError("url/post_token/device_id/secret はすべて文字列")
        bytes.fromhex(secret)   # secret 非hex を早期検知（sign_envelope 内の生tracebackを避ける）
        u = urlparse(url)
        loopback = u.hostname in ("127.0.0.1", "localhost", "::1")
        if not (u.scheme == "https" or (u.scheme == "http" and loopback)):
            raise ValueError(f"url は https（またはloopbackのhttp）のみ: {url}")
    except (OSError, KeyError, ValueError, TypeError) as e:  # JSONDecodeError は ValueError 派生
        print(f"✗ 設定エラー（{CONFIG}・600で url/post_token/device_id/secret）: {e}", file=sys.stderr)
        sys.exit(1)

    env = office.sign_envelope(secret, device_id, session, text,
                               int(time.time()), secrets.token_hex(16))
    req = urllib.request.Request(
        url + "/instruct", data=json.dumps(env).encode("utf-8"), method="POST",
        headers={"Authorization": f"Bearer {post_token}", "Content-Type": "application/json",
                 "User-Agent": "aioffice-send/1.0"})   # 既定Python-urllibはCloudflareに403される
    opener = urllib.request.build_opener(_NoRedirect())
    try:
        with opener.open(req, timeout=30) as resp:
            body = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            detail = e.read().decode("utf-8", "replace")[:500]
        except OSError:
            detail = ""
        print(f"✗ 送信失敗: {e} {detail}", file=sys.stderr)
        sys.exit(1)
    except (urllib.error.URLError, OSError, ValueError) as e:
        print(f"✗ 送信失敗: {e}", file=sys.stderr)
        sys.exit(1)
    if body.get("ok"):
        print(f"📨 投函しました（queued={body.get('queued')}）→ Mac が pull して {session} へ配達")
    else:
        print(f"✗ 中継が受理せず: {body}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
