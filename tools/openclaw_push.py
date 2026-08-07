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
import hashlib
import json
import os
import sys
import time
import urllib.request
from pathlib import Path

# R79-9: server/ws_client.py が在ればWS常時接続（変化時のみ送信＝DO受信20:1課金）。
# 無い環境（本ファイル単体コピー配布）は従来のHTTP POSTのまま＝壊れない。
_SERVER = Path(__file__).resolve().parent.parent / "server"
if str(_SERVER) not in sys.path:
    sys.path.insert(0, str(_SERVER))
try:
    import ws_client as wsc
except ImportError:
    wsc = None

CONFIG = Path(os.environ.get("OFFICE_PUSH_CONFIG",
                             str(Path.home() / ".claude" / "office_push.json")))


def load_config():
    if not CONFIG.exists():
        sys.exit(f"設定がありません: {CONFIG}（url/token/site を記入・600）")
    c = json.loads(CONFIG.read_text(encoding="utf-8"))
    url = str(c.get("url") or "").rstrip("/")
    token = str(c.get("token") or "")
    site = str(c.get("site") or "macmini")
    # R80-C5: interval に床を設ける（relay_agent.load_config と同じ経済則）。
    # 床が無いと config に 1 と書くだけで 86,400 req/日＝無料枠を1プロセスで割れる。
    try:
        interval = float(c.get("interval") or 60)
    except (TypeError, ValueError):
        interval = 60.0
    interval = max(interval, 15.0)
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


# R79: 無変化なら送らない。旧実装は15秒ごとに**無条件POST**で 5,760 req/日を使っていた。
# 判定は relay_agent._status_fingerprint と同じ思想（時刻など毎回変わる値を落として比較）。
# 変化が無い日でも「生きている」ことは伝えたいので、HEARTBEAT 秒ごとに1回だけ送る。
PUSH_HEARTBEAT = 240.0


def agents_fingerprint(agents):
    """契約v1 agents の意味的な指紋。age は分に量子化（1秒ごとの揺れで送らない）。"""
    norm = []
    for a in (agents if isinstance(agents, list) else []):
        if not isinstance(a, dict):
            continue
        norm.append({
            "id": a.get("id"), "name": a.get("name"), "state": a.get("state"),
            "verb": a.get("verb"), "channel": a.get("channel"),
            "minions": a.get("minions"), "age": int((a.get("age") or 0) // 60),
        })
    norm.sort(key=lambda x: str(x.get("id")))
    blob = json.dumps(norm, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


# ── R79-9: WS常時接続（常駐のみ・--once は常にHTTP＝E2E/手動確認の決定論を守る） ──
WS_KEEPALIVE = 25.0
WS_DEAD_AFTER = 90.0
WS_SYNC_TIMEOUT = 15.0
WS_FAIL_DEMOTE = 3
WS_DEMOTE_SECONDS = 600.0


def _ws_url(url, site):
    if url.startswith("https://"):
        base = "wss://" + url[len("https://"):]
    elif url.startswith("http://"):
        base = "ws://" + url[len("http://"):]
    else:
        base = url
    return base + "/ws?role=agent&site=" + site


def ws_loop(url, token, site, interval, collect):
    """WS常時接続の1セッション（例外でのみ戻る）。ローカルscanはリクエスト0円＝
    変化かheartbeat期限のときだけ {"t":"sync"} を送り、応答okを確認してから指紋を進める。"""
    ws = wsc.WSClient(_ws_url(url, site), token=token, timeout=10,
                      user_agent="aioffice-openclaw-push/1.0")
    ws.connect()
    print(f"🔌 WS接続 site={site}（変化時のみ送信・scan {interval:g}s）", flush=True)
    try:
        last_fp = None
        last_sent = 0.0
        last_ka = time.time()
        last_rx = time.time()
        while True:
            agents = collect()
            fp = agents_fingerprint(agents)
            now = time.time()
            if fp != last_fp or (now - last_sent) >= PUSH_HEARTBEAT:
                body = {"v": 1, "site": site, "generatedAt": now, "agents": agents}
                ws.send_text(json.dumps({"t": "sync", "office": body,
                                         "ackIds": [], "wantOpenclaw": False},
                                        ensure_ascii=False))
                deadline = time.time() + WS_SYNC_TIMEOUT
                got = False
                while time.time() < deadline:
                    m = ws.recv(timeout=deadline - time.time())
                    if m is None:
                        break
                    last_rx = time.time()
                    if m == "P":
                        continue
                    try:
                        d = json.loads(m)
                    except ValueError:
                        continue
                    if isinstance(d, dict) and d.get("t") == "sync" and d.get("ok"):
                        got = True
                        break
                if not got:
                    raise wsc.WSError("sync応答なし")
                last_fp = fp
                last_sent = now
                print(f"📤 push {site}: {len(agents)}体 → WS", flush=True)
            m = ws.recv(timeout=interval)
            now = time.time()
            if m is not None:
                last_rx = now   # wake等は読み流す（pushは送る係・受信はopenclaw_agentの仕事）
            if now - last_ka >= WS_KEEPALIVE:
                ws.send_text("p")
                last_ka = now
            if now - last_rx >= WS_DEAD_AFTER:
                raise wsc.WSError(f"無応答{int(now - last_rx)}秒（keepalive途絶）")
    finally:
        ws.close()


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--input", help="契約v1 JSON（全体 or agents配列）")
    src.add_argument("--collect", choices=["openclaw"], help="実OpenClawから収集（spike後）")
    ap.add_argument("--once", action="store_true")
    args = ap.parse_args()
    url, token, site, interval = load_config()
    collect = ((lambda: collect_from_input(args.input)) if args.input
               else collect_openclaw)
    ws_mode = (not args.once) and wsc is not None \
        and os.environ.get("RELAY_WS", "1") != "0"
    ws_fail = 0
    ws_down_until = 0.0
    last_fp = None
    last_sent = 0.0
    while True:
        # R79-9: 常駐はWSが主経路（3連続失敗で10分HTTPへ降格→自動復帰・全再接続ログ）
        if ws_mode and time.time() >= ws_down_until:
            started = time.time()
            try:
                ws_loop(url, token, site, interval, collect)
            except Exception as e:
                lived = time.time() - started
                ws_fail = 1 if lived > 60 else ws_fail + 1
                print(f"⚠ WS切断（{lived:.0f}s生存・連続{ws_fail}回目）: {e}", flush=True)
                if ws_fail >= WS_FAIL_DEMOTE:
                    ws_down_until = time.time() + WS_DEMOTE_SECONDS
                    ws_fail = 0
                    print(f"↩ {int(WS_DEMOTE_SECONDS / 60)}分間 HTTP pushへ降格（自動復帰）",
                          flush=True)
                time.sleep(min(2.0 * ws_fail + 1.0, 10.0))
            continue
        agents = collect()
        fp = agents_fingerprint(agents)
        now = time.time()
        # --once は必ず送る（手動確認・E2Eの意図を壊さない）
        if not args.once and fp == last_fp and (now - last_sent) < PUSH_HEARTBEAT:
            time.sleep(interval)
            continue
        try:
            r = push(url, token, site, agents)
            last_fp = fp
            last_sent = now
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
