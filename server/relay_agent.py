#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""AI Office 中継エージェント（P2） — 標準ライブラリのみ。

Cloudflare Worker(Room DO)を外向きHTTPSでポーリングし、
  1. スマホから投函された指示を取り出して ~/.claude/office_inbox へ配達
     （既存の office_server.post_instruction を再利用 → 既存 Stop hook が実セッションへ）
  2. いまのオフィス状況(office_server.office_json)を中継へ push（スマホ表示用）
ポート開放なし・完全アウトバウンド。設定= ~/.claude/office_relay.json {url, token, interval}
（環境変数 RELAY_URL / RELAY_TOKEN / RELAY_INTERVAL / OFFICE_HOME が優先＝テスト注入口）。

起動:  python3 "AI Office/server/relay_agent.py"          # 常駐ポーリング
       python3 "AI Office/server/relay_agent.py" --once   # 1周だけ（テスト/デバッグ）
"""
import hashlib
import http.client
import json
import os
import re
import secrets
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

NET_ERRORS = (urllib.error.URLError, OSError, ValueError, http.client.HTTPException)

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:          # exec_module 反復でも sys.path に重複挿入しない
    sys.path.insert(0, str(HERE))
import office_server as office  # 同じ server/・標準ライブラリのみ（post_instruction / office_json）


def _adopt_p4_data():
    """P4: 常駐インストール済みなら daemon/officectl と同じ data/（config+assets）を読む＝
    push_status のスマホ表示が repo config と分岐しない。main() からのみ呼ぶ
    （import 時に環境を触るとテストプロセスへ漏れる）。明示 OFFICE_DATA が最優先・
    OFFICE_HOME(テスト注入口)が在るときは本番P4データを読まない。"""
    if os.environ.get("OFFICE_DATA") or os.environ.get("OFFICE_HOME"):
        return
    d = Path.home() / "Library" / "Application Support" / "AIOffice" / "data"
    if d.is_dir():
        os.environ["OFFICE_DATA"] = str(d)   # subprocess(assets_gen等)への継承用
        office.DATA = d                       # config_file()/ASSETS は呼出時にモジュール属性参照
        office.ASSETS = d / "assets"
        print(f"data: {d} (P4常駐と共有)", flush=True)

_HOME = Path(os.environ.get("OFFICE_HOME", str(Path.home())))
CONFIG = _HOME / ".claude" / "office_relay.json"
TIMEOUT = 30

# --- P3: 署名検証・リプレイ防御・レート制限（真正性は Mac 側だけで担保） ---
# 検証ロジックは office_server.verify_envelope（stdlib・単一正本）を再利用する。
NONCE_FILE = _HOME / ".claude" / "office_nonces.json"   # "<device_id>:<nonce>" -> expiry_int（600）
_NONCES = {}                    # 既視 nonce（配達成功後にのみコミット＝at-least-once を壊さない）
_RATE = {}                      # device_id -> {"tokens": float, "last": int}（メモリのみ・再起動でリセット可）
WINDOW = int(os.environ.get("OFFICE_SIG_WINDOW", "300"))    # ts 鮮度窓（秒）。時計ずれ許容 & リプレイ窓
RATE_CAP = int(os.environ.get("OFFICE_RATE_CAP", "20"))     # token bucket 上限
RATE_REFILL = float(os.environ.get("OFFICE_RATE_REFILL", "10"))   # 毎分の補充数
ALLOW_UNSIGNED = os.environ.get("RELAY_ALLOW_UNSIGNED", "0") == "1"  # 移行/デバッグ用エスケープ（本番は0）


def _prune_nonces(now):
    for k in [k for k, exp in _NONCES.items() if exp < now]:
        del _NONCES[k]


def _load_nonces():
    """起動時に既視 nonce を読み込み prune（窓内リプレイを再起動後も塞ぐ）。破損時は空スタート。"""
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
    """既視 nonce を atomic 保存（★配達成功後にのみ呼ぶ）。窓外は _prune で有界。"""
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
        print(f"⚠ nonce保存失敗（クラッシュ時に窓内で重複配達の可能性）: {e}", flush=True)


def _rate_allow(device_id, now):
    """token bucket。now を引数注入して決定論的にテストできるようにする。"""
    b = _RATE.setdefault(device_id, {"tokens": float(RATE_CAP), "last": now})
    b["tokens"] = min(RATE_CAP, b["tokens"] + (now - b["last"]) * RATE_REFILL / 60.0)
    b["last"] = now
    if b["tokens"] >= 1:
        b["tokens"] -= 1
        return True
    return False


def load_config():
    """設定解決（env=テスト注入が最優先）。interval の安全弁（2026-07-31・提案4）:
    - 既定 60 秒（旧既定5秒×毎周3リクエストが Cloudflare 無料枠を食い潰した実測への恒久対策）
    - config ファイル由来は 15 秒未満へ下げられない（floor）。env RELAY_INTERVAL は
      テスト注入口なので floor を通さない（relay_e2e 等の高速回しを守る）。"""
    url = os.environ.get("RELAY_URL")
    token = os.environ.get("RELAY_TOKEN")
    env_interval = os.environ.get("RELAY_INTERVAL")
    interval = env_interval
    if (not url or not token) and CONFIG.exists():
        try:
            c = json.loads(CONFIG.read_text(encoding="utf-8"))
            url = url or c.get("url")
            token = token or c.get("token")
            interval = interval or c.get("interval")
        except (OSError, json.JSONDecodeError):
            pass
    try:
        interval = float(interval) if interval else 60.0
    except (TypeError, ValueError):
        interval = 60.0
    if not env_interval:
        interval = max(interval, 15.0)
    return (url or "").rstrip("/"), token or "", interval


def _req(method, url, token, body=None):
    data = json.dumps(body).encode("utf-8") if body is not None else None
    # ★User-Agent 必須: 既定の "Python-urllib/x.y" は Cloudflare のBot対策に 403 で弾かれる
    #   （ローカル wrangler dev では起きず、実デプロイのエッジでのみ発生する罠）
    req = urllib.request.Request(
        url, data=data, method=method,
        headers={"Authorization": f"Bearer {token}",
                 "Content-Type": "application/json",
                 "User-Agent": "aioffice-relay/1.0"})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _first_chunk(texts, limit=4000):
    """先頭から \\n\\n 連結で limit(コードポイント)を超えない最大件数を返す (chunk_text, count)。
    各 text は verify_envelope で ≤limit 保証なので最低1件は必ず入る（無限ストールなし）。
    超過分は呼び出し側が次tickへ繰り越す（office_inbox は1tick1通しか置けないため）。"""
    picked, total = [], 0
    for t in texts:
        add = len(t) + (2 if picked else 0)
        if picked and total + add > limit:
            break
        picked.append(t)
        total += add
    return "\n\n".join(picked), len(picked)


def pull_and_deliver(url, token):
    """レガシー経路（Worker /sync 未対応時）: /pull peek → 配達 → 成功分だけ /ack。
    配達できたセッション数を返す。検証・配達の核は _process_items（sync経路と共有）。"""
    d = _req("GET", url + "/pull", token)
    items = d.get("items", [])
    if not items:
        return 0
    delivered, ack_ids = _process_items(items)
    if ack_ids:
        try:
            _req("POST", url + "/ack", token, {"ids": ack_ids})
        except NET_ERRORS as e:
            # ack が届かないと次周で再取得しうるが、既視nonceで drop されるので二重配達しない
            print(f"⚠ ack送信失敗・次周で重複取得の可能性: {e}", flush=True)
    return delivered


def _process_items(items):
    """items（peek済み）を per-device HMAC署名検証 → セッション別に集約配達し、
    (delivered, ack対象idリスト) を返す。ack の送達は呼び出し側
    （レガシー=同tickで POST /ack・sync=次周の /sync ackIds）。核となる不変条件:
    - **nonce は配達成功後にのみコミット**する。検証時に焼くと、一時OSError→残置→再pull で
      「既視nonce＝リプレイ」と誤判定して指示が恒久ロストする（at-least-once が壊れる）。
    - レート超過は drop でなく「延期」（ack せず・nonce 焼かず残置→次tickで配達）。ただし WINDOW 秒を
      超えて延期され続けた指示は鮮度切れ(stale-ts)で最終的に落ちる＝レート×鮮度は本質的トレードオフ。
    - 恒久不正（署名NG/期限切れ/revoke/リプレイ）は即 ack して捨てる（毎周リトライで詰まらせない）。
      配達済みだが ack 未送達の再取得も「既視nonce＝replay」でここに落ちて ack される＝二重配達しない
      （sync経路の ack 持ち越しはこの性質に守られている）。
    - 同一セッションへの複数指示は \\n\\n で連結し1通に（単一スロットのoffice_inboxの上書き対策）。
      連結が4000字を超えるときは、同tickで複数回postすると単一スロットで上書きロストするため、
      先頭から収まるchunkだけ配達し残りは ack せず次tickへ繰り越す（分割してもロストしない）。
    """
    now = int(time.time())
    devices = office.load_devices()
    _prune_nonces(now)
    batch = set()      # 同一pull内の重複nonce dedup（初回配達前は _NONCES に無いため）
    groups = {}        # session -> {"ids": [...], "texts": [...], "keys": [(nonce_key, exp), ...]}
    ack_now = []       # 恒久不正＝配達を待たず即 ack で破棄

    for it in items:
        iid = it.get("id")
        raw = it.get("text", "")
        try:
            env = json.loads(raw)
        except (ValueError, TypeError):
            env = None
        # 3配列(ids/texts/keys)は index 整列を維持する（chunk分割で [:n] スライスするため）。
        # 無署名は nonce が無いので keys に None を入れて桁を合わせる。
        if not isinstance(env, dict):
            # 署名封筒でない ＝ 旧無署名 or ゴミ。既定は拒否。ALLOW_UNSIGNED のみ素通し（移行用）
            if ALLOW_UNSIGNED and it.get("session") and isinstance(raw, str) and raw:
                g = groups.setdefault(it["session"], {"ids": [], "texts": [], "keys": []})
                g["ids"].append(iid)
                g["texts"].append(raw)
                g["keys"].append(None)
                continue
            if iid is not None:
                ack_now.append(iid)
            print(f"⛔拒否(parse) id={iid}", flush=True)
            continue
        ok, reason, sess, text = office.verify_envelope(env, devices, now, WINDOW)
        if not ok:
            if iid is not None:
                ack_now.append(iid)
            # stale-ts は「一度は有効だった指示が期限切れで落ちた」可能性＝ログで目立たせる
            lvl = "⚠期限切れ" if reason == "stale-ts" else "⛔拒否"
            print(f"{lvl}({reason}) id={iid}", flush=True)
            continue
        key = f'{env["device_id"]}:{env["nonce"]}'
        if key in _NONCES or key in batch:
            if iid is not None:
                ack_now.append(iid)
            print(f"⛔拒否(replay) id={iid}", flush=True)
            continue
        if not _rate_allow(env["device_id"], now):
            print(f"⏸延期(rate) id={iid}", flush=True)   # ackせず・nonce焼かず残置→次tickで配達
            continue
        batch.add(key)
        g = groups.setdefault(sess, {"ids": [], "texts": [], "keys": []})
        g["ids"].append(iid)
        g["texts"].append(text)
        g["keys"].append((key, env["ts"] + WINDOW))   # nonce生存=封筒のverify有効期限（未来ts漏れ穴を塞ぐ）

    ack_ids = [x for x in ack_now if x is not None]
    delivered = 0
    committed = False
    for session, g in groups.items():
        chunk, n = _first_chunk(g["texts"])   # 4000字に収まる先頭chunkだけ（残りは次tickへ繰り越す）
        try:
            ok, _msg = office.post_instruction(session, chunk)
        except OSError as e:
            # 一時障害 → このsessionは丸ごと ack せず・nonce 焼かず残置（次周で再配達）。他sessionは続行
            print(f"⚠ 配達失敗・残置して再試行 ({session}): {e}", flush=True)
            continue
        if ok:
            delivered += 1
            committed = True
            for k in g["keys"][:n]:
                if k is not None:
                    _NONCES[k[0]] = k[1]    # ★配達したchunk分だけ nonce をコミット
            if n < len(g["texts"]):
                # 単一スロットの上書き回避のため残りは次tickで（un-ackで残置＝ロストしない）
                print(f"⏸ {session}: 残り{len(g['texts']) - n}件は次tickで配達（4000字上限で分割）", flush=True)
        # ok=True は配達済み、ok=False は恒久不正 → 配達したchunk分だけ ack（残りは繰り越し）
        ack_ids.extend(x for x in g["ids"][:n] if x is not None)

    if committed:
        _save_nonces()                  # ★永続スナップショットも配達成功後
    return delivered, ack_ids


# 中継(Cloudflare)へ出す前に落とすフィールド＝メッセージ本文・作業パス（機微情報になりうる）。
# プライバシー方針: 「本文は中継に流さない」（2026-07-09ユーザー選択）。残すのは状態/動作ログ/質問/名前/経過。
# 本文をMac側で落とすので Cloudflare の DO にもスマホにも一切乗らない（表示側の隠蔽ではなく根元遮断）。
_REDACT_FIELDS = ("lastSaid", "target", "lastOrder", "cwd", "branch")
_WORK_PATH_RE = re.compile(r"(?:/|~/)[^\s]+")


def _sanitize_work_for_relay(work):
    """workだけは中継へ通すが、本文中の絶対/チルダパスは末尾名へ縮める。"""
    if not isinstance(work, dict):
        return work

    def sanitize_text(value):
        if not isinstance(value, str):
            return value

        def replace_path(match):
            token = match.group(0)
            trailing = ""
            while token and token[-1] in ",.;:!?、。)]}」』":
                trailing = token[-1] + trailing
                token = token[:-1]
            name = Path(token).name if token else ""
            return name + trailing

        return _WORK_PATH_RE.sub(replace_path, value)[:60]

    result = {}
    remaining = 8
    for key in ("now", "next", "done"):
        values = work.get(key)
        if not isinstance(values, list) or remaining <= 0:
            result[key] = []
            continue
        sanitized = [sanitize_text(value) for value in values[:remaining]
                     if isinstance(value, str)]
        result[key] = sanitized
        remaining -= len(sanitized)
    if "counts" in work:
        result["counts"] = work["counts"]
    return result


def _redact_entry_for_relay(e):
    """社員/プロジェクト1件から本文・パスを落とす（employees[] と projects[] で共通）。"""
    if not isinstance(e, dict):
        return
    for k in _REDACT_FIELDS:
        if k in e:
            e[k] = ""
    fd = e.get("feed")
    if isinstance(fd, list):
        # 「💬 …」＝発言本文の要約行だけ除去。「実行中/編集中/執筆中…」等の動作ログは残す。
        e["feed"] = [ln for ln in fd
                     if not (isinstance(ln, str) and ln.lstrip().startswith("💬"))]
    if "work" in e:
        e["work"] = _sanitize_work_for_relay(e["work"])


def _redact_office_for_relay(office_snapshot):
    """office_json() の結果から機微になりうる本文/パスを除去して返す（破壊的・呼び出し側は都度生成物を渡す）。
    残す=state/kind/verb/feedの動作ログ(実行中・編集中…)/question/disp/dept/role/sprite/age/minions/approvalMin/stuckTool。
    落とす=lastSaid・target(本文由来)・lastOrder・cwd・branch、および feed の「💬 発言」行。

    R50: roster[] も同じ規則で落とす。projectId は cwd のハッシュ（パスを含まない）なので残す。
    sessions[] は _session_brief が本文を構造的に持たない形で作っているのでそのまま通す。"""
    if not isinstance(office_snapshot, dict):
        return office_snapshot
    for e in office_snapshot.get("employees") or []:
        _redact_entry_for_relay(e)
    for p in office_snapshot.get("roster") or []:
        _redact_entry_for_relay(p)
    # history[] は指示の全文（Mac UIから打った機微になりうる本文）を含む。PWAは
    # history を描画しない＝送る必要が無いので丸ごと落とす（lastOrder と同じ思想）。
    if "history" in office_snapshot:
        office_snapshot["history"] = []
    return office_snapshot


def push_status(url, token):
    """いまのオフィス状況を中継へ push（スマホ表示用・失敗しても致命ではない）。
    本文を落としてから送る＝機微情報を Cloudflare/スマホに出さない。"""
    snapshot = _redact_office_for_relay(office.office_json())
    _req("POST", url + "/status", token, {"office": snapshot})


# R42.4: mini側siteの契約status（docs/openclaw-status-schema.md v1）を取得してローカルへ保存。
# scan_office(openclaw_source)がこのファイルを読む＝アグリゲータ方式（PWAはsite=macのみ読む）。
OPENCLAW_SITE = os.environ.get("OFFICE_OPENCLAW_SITE", "macmini")


def _save_openclaw_contract(raw):
    """契約v1のみ ~/.claude/openclaw_status.json へ原子保存（毒は書かない）。
    raw= JSON文字列（GET /status?site 経路）または dict（/sync 経路）または None。"""
    if not raw:
        return False
    if isinstance(raw, dict) and "v" not in raw and "json" in raw:
        raw = raw.get("json")   # /sync は getStatus と同形 {json, ts} で包んで返す
        if not raw:
            return False
    if isinstance(raw, str):
        try:
            data = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return False
    elif isinstance(raw, dict):
        data = raw
    else:
        return False
    if not isinstance(data, dict) or data.get("v") != 1:
        return False
    p = Path(office._HOME) / ".claude" / "openclaw_status.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    tmp.replace(p)
    return True


def pull_openclaw_status(url, token):
    """site=macmini の契約statusを取得しローカルへ原子保存（レガシー経路）。"""
    d = _req("GET", url + "/status?site=" + OPENCLAW_SITE, token)
    raw = d.get("json") if isinstance(d, dict) else None
    return _save_openclaw_contract(raw)


# ---- R42.5: oc-宛（OpenClaw）指示の中継転送 --------------------------------
_OC_WARNED = {"nosecret": False}


def _oc_forward_keys():
    """oc-転送の署名鍵（mac→mini方向のper-device HMAC）。office_relay.json の任意キー
    ocDeviceId/ocSecret（mini側 office_push.macmini.json と同値を両端で持つ）。"""
    try:
        c = json.loads(CONFIG.read_text(encoding="utf-8"))
        return str(c.get("ocDeviceId") or ""), str(c.get("ocSecret") or "")
    except (OSError, json.JSONDecodeError):
        return "", ""


def forward_oc_outbox(url, token):
    """OC_OUTBOX（post_instruction の oc-分岐が書く転送待ち）を中継の site=macmini
    キューへ署名転送する。成功（ok:true）したファイルだけ削除＝at-least-once
    （失敗・例外は残置→次tick）。転送できた件数を返す。

    掟: ocSecret 未設定なら送らない（無署名を黙って流さない＝fail-closed・警告1回だけ）。
    outbox が空ならリクエストゼロ（リクエスト経済を崩さない）。"""
    outbox = office.OC_OUTBOX
    try:
        files = sorted(p for p in outbox.glob("*.json") if not p.name.startswith("."))
    except OSError:
        return 0
    if not files:
        return 0
    device_id, secret = _oc_forward_keys()
    if not (device_id and secret):
        if not _OC_WARNED["nosecret"]:
            _OC_WARNED["nosecret"] = True
            print("⚠ oc-宛の転送待ちがありますが ocDeviceId/ocSecret 未設定＝転送しません"
                  "（office_relay.json と mini側 office_push.macmini.json に同値を設定）",
                  flush=True)
        return 0
    sent = 0
    for p in files:
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
            env = office.sign_envelope(secret, device_id, d["session"], d["text"],
                                       int(time.time()), secrets.token_hex(16))
            r = _req("POST", url + "/instruct?site=" + OPENCLAW_SITE, token, env)
            if isinstance(r, dict) and r.get("ok"):
                p.unlink()
                sent += 1
                print(f"📤 oc-転送: {d['session']} → site={OPENCLAW_SITE}", flush=True)
            else:
                print(f"⚠ oc-転送が受理されず残置 ({p.name}): {r}", flush=True)
        except NET_ERRORS as e:
            # ネットワーク不調なら残りも同運命＝この周は打ち切り（次tickで再試行）
            print(f"⚠ oc-転送失敗・残置して再試行 ({p.name}): {e}", flush=True)
            break
        except (KeyError, ValueError, TypeError) as e:
            # 壊れたoutboxは退避（無限リトライで詰まらせない・.json外なので次周から対象外）
            print(f"⚠ 壊れたoc-outboxを退避 ({p.name}): {e}", flush=True)
            try:
                p.rename(p.with_suffix(".bad"))
            except OSError:
                pass
    return sent


# ── R50提案4: リクエスト経済（1周1リクエストの /sync・変化時のみpush・在席適応） ──
PUSH_HEARTBEAT = float(os.environ.get("OFFICE_PUSH_HEARTBEAT", "240"))  # 無変化でもこの間隔でpush（PWA鮮度表示の脈）
BURST_SECONDS = 8.0     # 速回し時の周期
BURST_WINDOW = 120.0    # 配達直後/❗あり/スマホ在席 から速回しを続ける秒数


class SyncUnsupported(Exception):
    """Worker が /sync 未対応（旧デプロイ）＝レガシー3リクエスト経路へフォールバックする合図。"""


def _quantize_entry(e):
    if not isinstance(e, dict):
        return e
    q = dict(e)
    if "age" in q:
        try:
            q["age"] = int(q.get("age") or 0) // 60
        except (TypeError, ValueError):
            pass
    if isinstance(q.get("sessions"), list):
        q["sessions"] = [_quantize_entry(s) for s in q["sessions"]]
    return q


def _status_fingerprint(snapshot):
    """push省略判定の指紋。時間経過だけで毎周変わる揮発値を量子化してから畳む
    （generatedAt=落とす・age=分単位。ここを量子化しないと毎周「変化あり」になり省略が効かない）。"""
    if not isinstance(snapshot, dict):
        return ""
    copy = {k: v for k, v in snapshot.items() if k != "generatedAt"}
    for key in ("roster", "employees"):
        if isinstance(copy.get(key), list):
            copy[key] = [_quantize_entry(e) for e in copy[key]]
    return hashlib.sha256(
        json.dumps(copy, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()


def _any_attention(snapshot):
    for e in (snapshot.get("roster") or []) + (snapshot.get("employees") or []):
        if isinstance(e, dict) and (
                e.get("question") or e.get("attention") or (e.get("approvalMin") or 0) > 0):
            return True
    return False


def _want_openclaw():
    try:
        return bool(office.edition_features(office.edition(),
                                            office.license_state()).get("openclaw"))
    except Exception:
        return False


def sync_tick(url, token, state):
    """1周1リクエスト: 前周ackの送達＋配達items取得＋状況push（変化時のみ）＋mini集約＋在席検知。
    (delivered, appSeenAgo) を返す。掟: items は peek＝配達成功分の id を state["acks"] に積み
    **次周**の /sync で削除する（at-least-once。ack前にクラッシュしても再取得→既視nonce→ack で
    二重配達しない＝ _process_items の replay-ack 性質に守られる）。"""
    now = time.time()
    snapshot = _redact_office_for_relay(office.office_json())
    fp = _status_fingerprint(snapshot)
    send_office = (fp != state.get("fp")
                   or now - state.get("pushed_at", 0.0) >= PUSH_HEARTBEAT)
    want_oc = _want_openclaw()
    body = {"office": snapshot if send_office else None,
            "ackIds": list(state.get("acks") or []),
            "wantOpenclaw": want_oc}
    try:
        d = _req("POST", url + "/sync", token, body)
    except urllib.error.HTTPError as e:
        if e.code in (404, 405):
            raise SyncUnsupported() from e
        raise
    if not isinstance(d, dict) or not d.get("ok"):
        raise SyncUnsupported()
    if send_office:
        state["fp"] = fp
        state["pushed_at"] = now
    state["attn"] = _any_attention(snapshot)
    delivered, ack_ids = _process_items(d.get("items") or [])
    state["acks"] = ack_ids
    # R42.5: oc-宛の転送（outboxが空ならリクエストゼロ・失敗は本流を巻き込まない）
    try:
        state["oc_sent"] = forward_oc_outbox(url, token) if want_oc else 0
    except Exception as e:
        state["oc_sent"] = 0
        print(f"⚠ oc-転送のみ失敗（本流は完了済み）: {e}", flush=True)
    if want_oc:
        try:
            _save_openclaw_contract(d.get("openclaw"))
        except OSError as e:
            print(f"⚠ OpenClaw集約の保存のみ失敗: {e}", flush=True)
    seen = d.get("appSeenAgo")
    return delivered, (int(seen) if isinstance(seen, (int, float)) else None)


def tick(url, token):
    """1周: 配達 → oc-転送 → 状況push → mini集約。各段の失敗は互いを巻き込まない"""
    n = pull_and_deliver(url, token)
    try:
        push_status(url, token)
    except NET_ERRORS as e:
        print(f"⚠ 状況送信のみ失敗（配達は完了済み）: {e}", flush=True)
    try:
        # openclaw機能が閉じたエディション(claude版)では転送も取得もしない
        if office.edition_features(office.edition(),
                                   office.license_state()).get("openclaw"):
            forward_oc_outbox(url, token)
            pull_openclaw_status(url, token)
    except Exception as e:
        print(f"⚠ OpenClaw集約のみ失敗（本流は完了済み）: {e}", flush=True)
    return n


def _license_gate_ok():
    """relayPwa機能の解錠判定。例外時はTrue（paywallのバグで配達を止めない）。"""
    try:
        return bool(office.edition_features(office.edition(),
                                            office.license_state()).get("relayPwa"))
    except Exception:
        return True


def main():
    once = "--once" in sys.argv
    # daemonログに時刻前置＋起動時ローテ（TTYでは素通し・office_server と同一実装を再利用）
    office._install_ts_logging("relay.daemon.log")
    url, token, interval = load_config()
    if not url or not token:
        # --once/手動は即エラー終了。常駐(launchd)では exit(1) すると PathState KeepAlive が
        # 10秒毎に再起動し続ける（office_relay.json が在るのに url/token 欠落＝設定途中の典型）ので、
        # プロセスを生かしたまま遅い間隔で config を再読込し、直った瞬間に自己回復する。
        print("✗ 中継設定が不完全（~/.claude/office_relay.json に url/token を設定）",
              file=sys.stderr)
        if once:
            sys.exit(1)
        while not (url and token):
            time.sleep(60)
            url, token, interval = load_config()
        print("✓ 中継設定を検出・起動します", flush=True)
    _adopt_p4_data()  # P4常駐インストール済みなら data/ を共有（スマホstatusの分岐防止）
    # R42.2 有料機能ゲート: スマホ中継(relayPwa)はライセンス必須（②openclaw版は無料で通る）。
    # 常駐では exit せず60秒毎に再確認＝ライセンス登録した瞬間に自己回復（PathState対策も兼ねる）。
    # 判定不能な内部エラーでは止めない（fail-open=ユーザー自身のツールの可用性優先）。
    while not _license_gate_ok():
        print("🔒 スマホ中継はPro機能です（オフィスUIの🔑からライセンス登録・60秒毎に再確認）",
              file=sys.stderr)
        if once:
            sys.exit(1)
        time.sleep(60)
    _load_nonces()   # 既視 nonce を復元（再起動後も窓内リプレイを塞ぐ）
    print(f"📡 中継エージェント起動: {url} (間隔{interval:g}s{' ・1周のみ' if once else ''})",
          flush=True)
    state = {"acks": [], "fp": None, "pushed_at": 0.0, "attn": False}
    sync_ok = True     # まず /sync（1周1リクエスト）。旧Workerなら一度だけレガシーへ降格
    burst_until = 0.0
    while True:
        appseen = None
        try:
            if sync_ok:
                try:
                    n, appseen = sync_tick(url, token, state)
                except SyncUnsupported:
                    sync_ok = False
                    print("ℹ Worker が /sync 未対応＝レガシー経路（3リクエスト/周）で継続。"
                          "relay/deploy.sh で更新すると自動で戻ります（要再起動）", flush=True)
                    n = tick(url, token)
            else:
                n = tick(url, token)
            if n:
                print(f"📨 {n}セッションへ配達", flush=True)
            # 配達直後/oc-転送直後/❗あり/スマホ在席(appSeen<120s)は2分だけ速回し＝
            # 体感のライブ感をユーザーが見ている瞬間に集中させ、無人の時間はリクエストを使わない
            if (n or state.get("oc_sent") or state.get("attn")
                    or (appseen is not None and appseen < 120)):
                burst_until = time.time() + BURST_WINDOW
        except NET_ERRORS as e:
            print(f"⚠ 中継エラー（次周でリトライ）: {e}", flush=True)
        if once:
            # --once は「次周」が無い＝持ち越しackをレガシー /ack でflushして終える
            # （放置するとキューが掃除されず、以後の周回が毎回replay-dropから始まる）
            if sync_ok and state.get("acks"):
                try:
                    _req("POST", url + "/ack", token, {"ids": state["acks"]})
                except NET_ERRORS as e:
                    print(f"⚠ --once終了時のack flush失敗: {e}", flush=True)
            break
        time.sleep(min(interval, BURST_SECONDS)
                   if time.time() < burst_until else interval)


if __name__ == "__main__":
    main()
