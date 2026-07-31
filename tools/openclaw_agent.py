#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""OpenClaw側(Mac mini) 受信エージェント（R42.5・標準ライブラリのみ）。

macオフィスで oc-宛に投函された指示を、中継の site=macmini キューから
peek → per-device HMAC検証 → 配達 → ack で受け取る（relay_agent と同一パターン移植）。

配達先は ~/.claude/openclaw_inbox/<session>.json（単一スロット・原子書き込み）**まで**が
本スクリプトの受け入れ範囲＝OpenClaw本体への注入は R42.3b（mini実機spike）の領域。

設定= ~/.claude/office_push.macmini.json（R42.4の控えに2キー追記・600）:
  {url, token(RELAY_MACMINI_TOKEN), ocDeviceId, ocSecret, interval}
  ocDeviceId/ocSecret は mac側 office_relay.json と同値（mac→mini方向の署名鍵）。
env注入口（テスト用）= RELAY_URL / RELAY_MACMINI_TOKEN / OC_DEVICE_ID / OC_SECRET / RELAY_INTERVAL

起動:  python3 tools/openclaw_agent.py          # 常駐ポーリング
       python3 tools/openclaw_agent.py --once   # 1周だけ（テスト/デバッグ）

核となる不変条件（relay_agent と同一・崩すな）:
- /pull は peek（消さない）・削除は配達成功分の /ack のみ＝at-least-once
- nonce は配達成功後にのみコミット（検証時に焼くと一時障害→再pullで恒久ロスト）
- 恒久不正（署名NG/期限切れ/リプレイ）は即 ack して捨てる（キューを詰まらせない）
"""
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
SERVER = HERE.parent / "server"
if str(SERVER) not in sys.path:
    sys.path.insert(0, str(SERVER))
import office_server as office     # noqa: E402  verify_envelope 再利用（canonical/KAT非接触）
import relay_agent as ra           # noqa: E402  _first_chunk 再利用（4000字繰り越しの単一正本）

NET_ERRORS = (urllib.error.URLError, OSError, ValueError)
_HOME = Path(os.environ.get("OFFICE_HOME", str(Path.home())))
CONFIG = _HOME / ".claude" / "office_push.macmini.json"
OC_INBOX = _HOME / ".claude" / "openclaw_inbox"
NONCE_FILE = _HOME / ".claude" / "openclaw_nonces.json"
WINDOW = int(os.environ.get("OFFICE_SIG_WINDOW", "300"))
SITE = os.environ.get("OFFICE_OPENCLAW_SITE", "macmini")
TIMEOUT = 30
_NONCES = {}


def load_config():
    """env(テスト注入)が最優先。interval は床15・既定60（relay_agent と同じ経済則）。"""
    url = os.environ.get("RELAY_URL")
    token = os.environ.get("RELAY_MACMINI_TOKEN")
    device_id = os.environ.get("OC_DEVICE_ID")
    secret = os.environ.get("OC_SECRET")
    env_interval = os.environ.get("RELAY_INTERVAL")
    interval = env_interval
    if CONFIG.exists():
        try:
            c = json.loads(CONFIG.read_text(encoding="utf-8"))
            url = url or c.get("url")
            token = token or c.get("token")
            device_id = device_id or c.get("ocDeviceId")
            secret = secret or c.get("ocSecret")
            interval = interval or c.get("interval")
        except (OSError, json.JSONDecodeError):
            pass
    try:
        interval = float(interval) if interval else 60.0
    except (TypeError, ValueError):
        interval = 60.0
    if not env_interval:
        interval = max(interval, 15.0)
    return ((url or "").rstrip("/"), token or "", str(device_id or ""),
            str(secret or ""), interval)


def _req(method, url, token, body=None):
    data = json.dumps(body).encode("utf-8") if body is not None else None
    # User-Agent 必須（既定Python-urllibはCloudflareのBot対策に403で弾かれる・relay_agentの実測）
    req = urllib.request.Request(
        url, data=data, method=method,
        headers={"Authorization": f"Bearer {token}",
                 "Content-Type": "application/json",
                 "User-Agent": "aioffice-openclaw-agent/1.0"})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _load_nonces():
    _NONCES.clear()
    try:
        d = json.loads(NONCE_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return
    if isinstance(d, dict):
        now = int(time.time())
        for k, exp in d.items():
            try:
                if int(exp) >= now:
                    _NONCES[k] = int(exp)
            except (TypeError, ValueError):
                pass


def _save_nonces():
    """★配達成功後にのみ呼ぶ（原子保存・600）。"""
    try:
        NONCE_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp = NONCE_FILE.with_name(NONCE_FILE.name + ".tmp")
        tmp.write_text(json.dumps(_NONCES), encoding="utf-8")
        tmp.replace(NONCE_FILE)
        try:
            os.chmod(NONCE_FILE, 0o600)
        except OSError:
            pass
    except OSError as e:
        print(f"⚠ nonce保存失敗: {e}", flush=True)


def _deliver(session, text):
    """openclaw_inbox へ原子書き込み（単一スロット・last-write-wins）。"""
    OC_INBOX.mkdir(parents=True, exist_ok=True)
    tmp = OC_INBOX / f".{session}.tmp"
    tmp.write_text(json.dumps({"text": text, "ts": time.time(), "from": "office-relay"},
                              ensure_ascii=False), encoding="utf-8")
    tmp.rename(OC_INBOX / f"{session}.json")


def pull_and_deliver(url, token, devices):
    """1周: peek → 検証 → セッション別集約配達 → 成功分だけ ack。配達セッション数を返す。"""
    d = _req("GET", url + "/pull?site=" + SITE, token)
    items = d.get("items", [])
    if not items:
        return 0
    now = int(time.time())
    for k in [k for k, exp in _NONCES.items() if exp < now]:
        del _NONCES[k]
    batch = set()
    groups = {}      # session -> {"ids": [...], "texts": [...], "keys": [...]}
    ack_now = []     # 恒久不正＝即 ack で破棄

    for it in items:
        iid = it.get("id")
        try:
            env = json.loads(it.get("text", ""))
        except (ValueError, TypeError):
            env = None
        if not isinstance(env, dict):
            if iid is not None:
                ack_now.append(iid)
            print(f"⛔拒否(parse) id={iid}", flush=True)
            continue
        ok, reason, sess, text = office.verify_envelope(env, devices, now, WINDOW)
        if not ok:
            if iid is not None:
                ack_now.append(iid)
            lvl = "⚠期限切れ" if reason == "stale-ts" else "⛔拒否"
            print(f"{lvl}({reason}) id={iid}", flush=True)
            continue
        key = f'{env["device_id"]}:{env["nonce"]}'
        if key in _NONCES or key in batch:
            if iid is not None:
                ack_now.append(iid)
            print(f"⛔拒否(replay) id={iid}", flush=True)
            continue
        batch.add(key)
        g = groups.setdefault(sess, {"ids": [], "texts": [], "keys": []})
        g["ids"].append(iid)
        g["texts"].append(text)
        g["keys"].append((key, env["ts"] + WINDOW))

    ack_ids = [x for x in ack_now if x is not None]
    delivered = 0
    committed = False
    for session, g in groups.items():
        chunk, n = ra._first_chunk(g["texts"])   # 4000字繰り越し（残りは残置→次tick）
        try:
            _deliver(session, chunk)
        except OSError as e:
            print(f"⚠ 配達失敗・残置して再試行 ({session}): {e}", flush=True)
            continue
        delivered += 1
        committed = True
        for k in g["keys"][:n]:
            _NONCES[k[0]] = k[1]                 # ★配達したchunk分だけ nonce をコミット
        if n < len(g["texts"]):
            print(f"⏸ {session}: 残り{len(g['texts']) - n}件は次tickで配達", flush=True)
        ack_ids.extend(x for x in g["ids"][:n] if x is not None)

    if committed:
        _save_nonces()
    if ack_ids:
        try:
            _req("POST", url + "/ack?site=" + SITE, token, {"ids": ack_ids})
        except NET_ERRORS as e:
            print(f"⚠ ack送信失敗・次周は既視nonceでdropされ二重配達しない: {e}", flush=True)
    return delivered


def main():
    once = "--once" in sys.argv
    url, token, device_id, secret, interval = load_config()
    if not (url and token and device_id and secret):
        print("✗ 設定不足: office_push.macmini.json に url/token/ocDeviceId/ocSecret を設定",
              file=sys.stderr)
        sys.exit(1)
    devices = {"devices": {device_id: {"secret": secret, "revoked": False,
                                       "expires": 2**53, "label": "oc-forward"}}}
    _load_nonces()
    print(f"🦞 openclaw_agent 起動: {url} site={SITE} (間隔{interval:g}s"
          f"{' ・1周のみ' if once else ''})", flush=True)
    while True:
        try:
            n = pull_and_deliver(url, token, devices)
            if n:
                print(f"📥 {n}セッションへ配達 (openclaw_inbox)", flush=True)
        except NET_ERRORS as e:
            print(f"⚠ 中継エラー（次周でリトライ）: {e}", flush=True)
        if once:
            break
        time.sleep(interval)


if __name__ == "__main__":
    main()
