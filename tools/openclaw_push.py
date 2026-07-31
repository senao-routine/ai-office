#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""R42.4 Mac mini側: OpenClaw状態を契約スキーマv1で中継へpushする（標準ライブラリのみ）。

経路: このスクリプト → POST <relay>/status?site=<site>（Bearer=RELAY_MACMINI_TOKEN）
→ メインMacの relay_agent が取得 → scan_office が OpenClaw室へ表示。

設定: ~/.claude/office_push.json (600)
  {"url": "https://<worker>.workers.dev", "token": "<RELAY_MACMINI_TOKEN>",
   "site": "macmini", "interval": 15}

使い方:
  python3 tools/openclaw_push.py --input agents.json --once   # 契約v1(全体 or agents配列)を送る
  python3 tools/openclaw_push.py --collect openclaw           # 実OpenClaw読取（R42.3b spikeで実装）

--input は {"v":1,"agents":[...]} 全体でも [ {...}, ... ] のagents配列だけでもよい。
generatedAt/site はここで毎回スタンプする（staleゲートの正本はmac側 openclaw_source）。
"""
import argparse
import json
import os
import sys
import time
import urllib.request
from pathlib import Path

CONFIG = Path(os.environ.get("OFFICE_PUSH_CONFIG",
                             str(Path.home() / ".claude" / "office_push.json")))


def load_config():
    if not CONFIG.exists():
        sys.exit(f"設定がありません: {CONFIG}（url/token/site を記入・600）")
    c = json.loads(CONFIG.read_text(encoding="utf-8"))
    url = str(c.get("url") or "").rstrip("/")
    token = str(c.get("token") or "")
    site = str(c.get("site") or "macmini")
    interval = float(c.get("interval") or 15)
    if not url or not token:
        sys.exit("設定に url/token がありません")
    return url, token, site, interval


def collect_from_input(path):
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    if isinstance(raw, list):
        return raw
    if isinstance(raw, dict):
        return raw.get("agents") or []
    return []


def collect_openclaw():
    # R42.3b spike: Mac mini実機で OpenClaw の状態面（gateway API / セッションファイル /
    # CLI出力）を調査してからここを実装する。契約v1のagents配列を返すこと。
    sys.exit("--collect openclaw は未実装（R42.3b spike後に実装。今は --input を使う）")


def push(url, token, site, agents):
    body = {"v": 1, "site": site, "generatedAt": time.time(), "agents": agents}
    req = urllib.request.Request(
        f"{url}/status?site={site}",
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            # 既定のPython-urllib UAはCloudflareのBot対策で403（P2実機の教訓）
            "User-Agent": "aioffice-openclaw-push/1.0",
        }, method="POST")
    with urllib.request.urlopen(req, timeout=15) as res:
        return json.loads(res.read().decode("utf-8"))


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--input", help="契約v1 JSON（全体 or agents配列）")
    src.add_argument("--collect", choices=["openclaw"], help="実OpenClawから収集（spike後）")
    ap.add_argument("--once", action="store_true")
    args = ap.parse_args()
    url, token, site, interval = load_config()
    while True:
        agents = (collect_from_input(args.input) if args.input else collect_openclaw())
        try:
            r = push(url, token, site, agents)
            print(f"📤 push {site}: {len(agents)}体 → {r.get('ok')}", flush=True)
        except Exception as e:  # 常駐時はネットワーク断でも死なない
            print(f"⚠ push失敗（次周でリトライ）: {e}", file=sys.stderr, flush=True)
            if args.once:
                sys.exit(1)
        if args.once:
            break
        time.sleep(interval)


if __name__ == "__main__":
    main()
