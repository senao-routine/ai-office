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
import office_server as office
import office_actions  # 同じ server/・標準ライブラリのみ
import dialog_seal     # R87: 封書の**形**だけ（live_bundles/FRAME_LEN/MAX_FRAMES/REQID_RE/ERRORS/unb64u）。復号や transcript 読みは持ち込まない（post_instruction / office_json）
import ws_client as wsc         # R79-8: RFC6455クライアント（同じ server/・stdlibのみ・KATはtest_ws_client）


def _adopt_p4_data():
    """P4: 常駐インストール済みなら daemon/officectl と同じ data/（config+assets）を読む＝
    push_status のスマホ表示が repo config と分岐しない。main() からのみ呼ぶ
    （import 時に環境を触るとテストプロセスへ漏れる）。明示 OFFICE_DATA が最優先・
    OFFICE_HOME(テスト注入口)が在るときは本番P4データを読まない。"""
    if os.environ.get("OFFICE_DATA") or os.environ.get("OFFICE_HOME"):
        return
    d = Path.home() / "Library" / "Application Support" / "AIOffice" / "data"
    if d.is_dir():
        os.environ["OFFICE_DATA"] = str(d)   # 子プロセスへの継承用
        office.DATA = d                       # config_file() は呼出時にモジュール属性参照
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
        # R100-0: 最初から 0600 で開く（作ってから chmod だと、その間は umask 次第で読める）
        fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(json.dumps(_NONCES))
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


# ── R79-10 遠隔実行（許可リスト）: act-封筒は daemon(127.0.0.1) へ回す ──────────
# 実行者を relay_agent にしない理由: launch は osascript＝**Automation TCC同意**が要り、
# 別LaunchAgentのrelay_agentから初めて叩くと「誰も居ないMacで同意ダイアログが出て詰む」。
ACT_SESSION_RE = re.compile(r"^act-[0-9a-f]{16}$")
ACT_PORT = int(os.environ.get("OFFICE_PORT", "4780"))
ACT_URL = os.environ.get("OFFICE_ACTION_URL", f"http://127.0.0.1:{ACT_PORT}/api/action/exec")


def _post_action(payload):
    """daemon の /api/action/exec へ（loopback+CSRFヘッダ）。応答dictを返す。"""
    req = urllib.request.Request(
        ACT_URL, data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        method="POST",
        headers={"Content-Type": "application/json",
                 "X-Office-Local": "1",              # CSRFゲート（ローカル操作の証）
                 "User-Agent": "aioffice-relay/1.0"})
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read().decode("utf-8"))


# ── R87 封書: dlg-<32hex> は「会話を封じて返す要求」＝ office_inbox へは書かない ──────────
# 封じるのは daemon（transcript を読める唯一のプロセス）。ここは act- と同じ配達員で、
# 返ってきた**暗号文だけ**を次の sync に載せる。平文は relay_agent のメモリにも載せない
# （transcript を読む関数・ページ定数・復号関数を一切 import しない＝test_relay_agent が grep でピン）。
DLG_SESSION_RE = re.compile(r"^dlg-[0-9a-f]{32}$")
DLG_URL = os.environ.get("OFFICE_SEAL_URL", f"http://127.0.0.1:{ACT_PORT}/api/dialog/sealed")
DLG_KEYS = frozenset({"v", "id", "s", "i", "e", "n", "c", "b", "err"})
# R100-1a: 封じて返す要求の種類（dialog＝会話ビュー／term＝ターミナル画面／R100-1b ask＝許可の全文）。
# office_server.SEALED_KINDS と同じ閉じた集合。
DLG_KINDS = frozenset({"dialog", "term", "ask"})
DLG_B_MAX = dialog_seal.MAX_FRAMES * dialog_seal.FRAME_LEN * 4 // 3 + 4     # base64url の上限
_DLG_B64 = re.compile(r"^[A-Za-z0-9_-]+$")


def _post_dialog(payload):
    """daemon の /api/dialog/sealed へ（loopback+CSRFヘッダ）。応答dictを返す。"""
    req = urllib.request.Request(
        DLG_URL, data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        method="POST",
        headers={"Content-Type": "application/json",
                 "X-Office-Local": "1",
                 "User-Agent": "aioffice-relay/1.0"})
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read().decode("utf-8"))


# ── R100-1b スマホから許可: apv-<32hex>（許可）・enr-<32hex>（許可鍵の登録）は **封筒ごと** daemon へ ──────────
# ここは配達員のまま: 署名は上で確かめているが、daemon が自分の台帳で**確かめ直す**（device_id も nonce も
# relay_agent の値を信じない）。危険判定・パスキーの検証・ON の読みはここに持ち込まない（grep の門）。
# ack の規則は act-/dlg- と同じ（受理＝200・HTTPError＝恒久拒否は ack＋nonce・不達は残置して次周）。
APV_SESSION_RE = re.compile(r"^apv-[0-9a-f]{32}$")
ENR_SESSION_RE = re.compile(r"^enr-[0-9a-f]{32}$")
RST_SESSION_RE = re.compile(r"^rst-[0-9a-f]{32}$")   # R103-1: スマホから「スマホから許可」を ON/OFF
# R116-S1b: つながりの延長（新しい秘密を求める req・新しい秘密で返す受領 ack）。台帳を書くのは daemon だけ＝封筒ごと回す
RKY_SESSION_RE = re.compile(r"^rky-[0-9a-f]{32}$")
APV_URL = os.environ.get("OFFICE_APPROVE_URL", f"http://127.0.0.1:{ACT_PORT}/api/approval/remote")
ENR_URL = os.environ.get("OFFICE_ENROLL_URL", f"http://127.0.0.1:{ACT_PORT}/api/approve/enroll")
RST_URL = os.environ.get("OFFICE_REMOTE_SET_URL", f"http://127.0.0.1:{ACT_PORT}/api/approval/remote_set")
RKY_URL = os.environ.get("OFFICE_REKEY_URL", f"http://127.0.0.1:{ACT_PORT}/api/pair/rekey")


def _post_envelope(url, env):
    req = urllib.request.Request(
        url, data=json.dumps({"envelope": env}, ensure_ascii=False).encode("utf-8"), method="POST",
        headers={"Content-Type": "application/json", "X-Office-Local": "1", "User-Agent": "aioffice-relay/1.0"})
    with urllib.request.urlopen(req, timeout=70) as resp:        # 70 秒＝R103-1 より前の daemon（登録の確認を待った）との互換
        return json.loads(resp.read().decode("utf-8"))


def _deliver_envelopes(session, g):
    """apv-/enr-/rst-/rky- の各封筒を daemon へ（封筒ごと）。(ack対象ids, コミットするnonce鍵)。"""
    url = (APV_URL if APV_SESSION_RE.match(session) else RST_URL if RST_SESSION_RE.match(session)
           else RKY_URL if RKY_SESSION_RE.match(session) else ENR_URL)
    ok_ids, ok_keys = [], []
    envs = g.get("envs") or []
    for i, iid in enumerate(g["ids"]):
        env = envs[i] if i < len(envs) else None
        key = g["keys"][i]
        if not isinstance(env, dict):
            if iid is not None:
                ok_ids.append(iid)                   # 無署名（ALLOW_UNSIGNED でも）は恒久拒否
            print(f"⛔拒否({session[:4]}-unsigned) id={iid}", flush=True)
            continue
        try:
            r = _post_envelope(url, env)
        except urllib.error.HTTPError as e:
            print(f"⛔{session[:3]} 拒否 id={iid} http={e.code}", flush=True)
            if iid is not None:
                ok_ids.append(iid)
            ok_keys.append(key)
            continue
        except NET_ERRORS as e:
            print(f"⚠ {session[:3]} 不達（daemon未起動?・残置して次周）: {e}", flush=True)
            continue
        print(f"🔏 {session[:3]} id={iid} {str((r or {}).get('state') or '?')}", flush=True)
        if iid is not None:
            ok_ids.append(iid)
        ok_keys.append(key)
    return ok_ids, ok_keys


def _deliver_dialog(session, g):
    """dlg-宛の各封筒を daemon へ回す。ack 規則は _deliver_actions と**同型**:
    形式不正・session と reqId の不一致＝恒久＝ack して捨てる／HTTPError（受け取って拒否）＝ack＋nonce／
    NET_ERRORS（不達）＝ack せず nonce も焼かず残置→次周（300秒の鮮度窓で自然消滅）。"""
    ok_ids, ok_keys = [], []
    for i, text in enumerate(g["texts"]):
        iid = g["ids"][i]
        key = g["keys"][i]
        try:
            act = json.loads(text)
        except (ValueError, TypeError):
            act = None
        req_id = str((act or {}).get("reqId") or "") if isinstance(act, dict) else ""
        if (not isinstance(act, dict) or act.get("aioffice") != 1 or act.get("kind") not in DLG_KINDS
                or session != f"dlg-{req_id}"):
            if iid is not None:
                ok_ids.append(iid)
            print(f"⛔拒否(dlg-format) id={iid}", flush=True)
            continue
        _devs = g.get("devices") or []
        dev = _devs[i] if i < len(_devs) else ""
        try:
            r = _post_dialog({"action": act, "device_id": dev})
        except urllib.error.HTTPError as e:
            print(f"⛔封書拒否 id={iid} http={e.code}", flush=True)
            if iid is not None:
                ok_ids.append(iid)
            ok_keys.append(key)
            continue
        except NET_ERRORS as e:
            print(f"⚠ 封書不達（daemon未起動?・残置して次周）: {e}", flush=True)
            continue                 # ★ackしない・nonceも焼かない
        print(f"✉️ 封書 id={iid} {str((r or {}).get('msg') or (r or {}).get('state') or '?')}", flush=True)
        if iid is not None:
            ok_ids.append(iid)
        ok_keys.append(key)
    return ok_ids, ok_keys


def _assert_opaque(bundle):
    """送信直前の門。封書は**不透明なバイト列と数字と固定 enum**だけで出来ていること。
    それ以外（平文・余分なキー・長さ違い）が混じったら例外＝その周は送らない。"""
    if not isinstance(bundle, dict):
        raise ValueError("bundle is not a dict")
    extra = set(bundle) - DLG_KEYS
    if extra:
        raise ValueError(f"unexpected keys: {sorted(extra)}")
    out = {}
    if bundle.get("v") != 1:
        raise ValueError("bad v")
    out["v"] = 1
    rid = bundle.get("id")
    if not isinstance(rid, str) or not dialog_seal.REQID_RE.match(rid):
        raise ValueError("bad id")
    out["id"] = rid
    e = bundle.get("e")
    if not isinstance(e, int) or isinstance(e, bool) or not (0 < e < 4102444800):
        raise ValueError("bad e")
    out["e"] = e
    if "err" in bundle:
        if bundle["err"] not in dialog_seal.ERRORS:
            raise ValueError("bad err")
        for k in ("s", "i", "n", "c", "b"):
            if k in bundle:
                raise ValueError("err bundle carries payload")
        out["err"] = bundle["err"]
        return out
    for k in ("s", "b"):
        v = bundle.get(k)
        if not isinstance(v, str) or not _DLG_B64.match(v):
            raise ValueError(f"bad {k}")
    if len(bundle["s"]) > 64 or len(bundle["b"]) > DLG_B_MAX:
        raise ValueError("too long")
    n = bundle.get("n")
    if not isinstance(n, int) or isinstance(n, bool) or not (1 <= n <= dialog_seal.MAX_FRAMES):
        raise ValueError("bad n")
    i = bundle.get("i")
    if not isinstance(i, int) or isinstance(i, bool) or not (0 < i < 4102444800):
        raise ValueError("bad i")
    if bundle.get("c") != dialog_seal.COMPRESSION:
        raise ValueError("bad c")
    raw = dialog_seal.unb64u(bundle["b"])
    if len(raw) != n * dialog_seal.FRAME_LEN or not raw.startswith(dialog_seal.MAGIC):
        raise ValueError("frame length/magic mismatch")
    out.update({"s": bundle["s"], "i": i, "n": n, "c": bundle["c"], "b": bundle["b"]})
    return out


# 中継（worker.js sync の dlgJson 上限）と同じ値。超える分は**古い順に落とす**＝黙って全部捨てない。
DLG_MAX_CHARS = 180000


def _dialog_payload(state):
    """次の sync に載せる封書のレーン。(payload_json or None, fingerprint)。
    None/"" = 送るものが無い（ファイル未使用）。期限切れだけなら**空を1回**送って中継の行を消す。
    usage.level>=1（R80 の自動減速）では非空の payload を保留する（空は送る）。
    ただし急ぎの封書（許可の全文とその理由・dialog_seal.urgent_ids）が入っていれば全体を送る
    ＝「スマホで確認して許可」が減速中ずっと「届きませんでした」になっていた（一部だけ送ると中継の行が置き換わり、
    送り済みの会話の封書を消してしまうので全体）。"""
    rows = dialog_seal.live_bundles()
    if rows is None:
        return None, ""
    items = []
    for b in rows:
        try:
            items.append(_assert_opaque(b))
        except ValueError as e:
            print(f"⛔封書を送らない（不透明でない）: {e}", flush=True)
    # 新しい順に並べ、中継の上限を超える分は古い封書から落とす（別モデルレビュー 2026-09-14:
    # 有効な封書 2 通で 65,536 字を超え、中継が黙って捨てたうえ指紋は確定＝再送も無かった）。
    items.sort(key=lambda b: int(b.get("i") or 0), reverse=True)
    text = json.dumps({"v": 1, "items": items}, separators=(",", ":"))
    while len(text) > DLG_MAX_CHARS and len(items) > 1:
        dropped = items.pop()
        print(f"⚠封書を落とす（中継の上限 {DLG_MAX_CHARS} 字超・古い順）: id={str(dropped.get('id', '?'))[:8]}", flush=True)
        text = json.dumps({"v": 1, "items": items}, separators=(",", ":"))
    fp = hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]
    lvl = int((state.get("usage") or {}).get("level") or 0)
    if items and lvl >= 1 and not (dialog_seal.urgent_ids() & {b.get("id") for b in items}):
        return None, state.get("dlg_fp", "")   # 減速中は保留（指紋も動かさない＝後で送る）
    return text, fp


def _deliver_actions(session, g):
    """act-宛の各封筒を daemon へ受理させる。(ack対象ids, コミットするnonce鍵) を返す。
    受理できなかった分は**ackしない・nonceも焼かない**＝次周で再送（300秒の鮮度窓を超えたら
    自然に stale-ts で落ちる＝「3時間前のタップが今走る」を防ぐアクション本来の性質）。"""
    ok_ids, ok_keys = [], []
    for i, text in enumerate(g["texts"]):
        iid = g["ids"][i]
        key = g["keys"][i]
        try:
            act = json.loads(text)
        except (ValueError, TypeError):
            act = None
        if not isinstance(act, dict) or act.get("aioffice") != 1:
            if iid is not None:      # 形式不正は恒久＝即ackで捨てる（キューを詰まらせない）
                ok_ids.append(iid)
            print(f"⛔拒否(act-format) id={iid}", flush=True)
            continue
        # M2: 監査ログには使い捨ての act- session ではなく、verify_envelope が返した
        # 検証済み device_id を渡す（盗まれた端末を事後に失効特定できるようにする）。
        _devs = g.get("devices") or []
        dev = _devs[i] if i < len(_devs) else session
        try:
            r = _post_action({"action": act, "device_id": dev or session})
        except urllib.error.HTTPError as e:
            # daemonは**受け取ったうえで拒否**した（denied/形式不正）＝恒久的な結果。
            # 残置すると毎周リトライでキューが詰まるので ack して捨てる（nonceも焼く）。
            print(f"⛔アクション拒否 id={iid} http={e.code}", flush=True)
            if iid is not None:
                ok_ids.append(iid)
            ok_keys.append(key)
            continue
        except NET_ERRORS as e:
            print(f"⚠ アクション不達（daemon未起動?・残置して次周）: {e}", flush=True)
            continue                 # ★ackしない・nonceも焼かない
        state = str((r or {}).get("state") or (r or {}).get("msg") or "?")
        print(f"🎬 アクション受理 id={iid} state={state}", flush=True)
        if iid is not None:
            ok_ids.append(iid)
        ok_keys.append(key)
    return ok_ids, ok_keys


# b4-mac: 端末の「最後に使った時刻」を控える間隔（同じ端末は 60 秒に 1 回まで書く＝連投でファイルを叩かない）。
# 控えは ~/.claude/office_devices_seen.json（daemon の 📱一覧が読む・秘密の台帳とは別のファイル）。
SEEN_EVERY = 60
_SEEN_AT = {}


def _door_audit(ev, bot, **fields):
    """口の監査へ 1 行（本流を止めない＝監査が書けなくても配達の判定は変えない）。"""
    try:
        office.office_door.audit_event(ev, bot, home=office._HOME, **fields)
    except Exception as e:                                # noqa: BLE001
        print(f"⚠ 口の監査を書けない: {type(e).__name__}", flush=True)


def _door_req_id(key):
    """R127: スマホの封筒 1 通の冪等の id（口の適用済み id に残る＝nonce の控えを失った再配達でも二重に入れない）。
    端末 id と nonce のハッシュ（本文は入れない・office_door.REQ_ID_RE に合う 46 字）。"""
    return "relay:" + hashlib.sha256(str(key[0]).encode("utf-8")).hexdigest()[:40]


def _deliver_door(session, g):
    """R127: 出勤の口の Bot 宛（session "bot-<id>"）の封筒を 1 通ずつ順に口へ渡す（口の受信箱は何件でも持てる＝連結しない）。
    - id8 つきの答え（R102-11 の本文 {"aioffice":1,"kind":"answer","ask":id8,"text"}）→ office_door.answer（src="relay"）。
      開いている相談の askId の先頭 8 桁に合うときだけ届く。合わない・もう閉じた相談への答えは口が**捨てて監査に残す**
      （discard・nomatch／closed）＝指示に落とさない（別の依頼への「やる」に化けない・セッションの R102-11 との違い）。
    - 印の無い言葉 → post_instruction(src="relay")＝相談が閉じていれば指示として口の受信箱へ。開いていれば口が断る
      （refuse・ask-open＝指示は相談が閉じてから・見ていない相談の答えにはしない）＝捨てて監査。
    - 宛先の形が違う・無署名（RELAY_ALLOW_UNSIGNED の移行用でも）＝人の言葉と言えない＝捨てて監査。
    ack の規則: 口が受けた・断った（恒久）＝ack＋nonce のコミット（ack を失った再取得はリプレイで落ちる＝断った言葉が後で
    状態が変わって届くことも無い）。OSError／ValueError（ファイル・ロックの一時障害）＝ack せず nonce も焼かず残置し、
    この Bot の後ろの封筒も次周へ（順番を守る＝答えより先に指示が着いて断られない）。それ以外の例外は外へ出さない
    （その 1 通を捨てて監査＝毒の 1 通で後ろを鮮度の窓まで止めない）。→ (届けた数, ack する id, コミットする nonce 鍵)"""
    door = office.office_door
    bot_id = door.bot_of_session(session)
    delivered, ok_ids, ok_keys = 0, [], []
    lang = None
    for i, text in enumerate(g["texts"]):
        iid, key = g["ids"][i], g["keys"][i]
        if bot_id is None or key is None:
            why = "bad-id" if bot_id is None else "unsigned"
            _door_audit("discard", bot_id or "?", src="relay", why=why)
            if iid is not None:
                ok_ids.append(iid)
            print(f"⛔ Bot 宛を捨てた（{why}） id={iid}", flush=True)
            continue
        ans = _answer_of(text)
        what = "答え" if ans is not None else "指示"
        try:
            if lang is None:
                lang = office.office_lang()
            if ans is not None:
                res = door.answer(bot_id, ans[0], ans[1], src="relay", home=office._HOME,
                                  req_id=_door_req_id(key), lang=lang)
                ok, why = bool(res.get("ok")), str(res.get("error") or "")
            else:
                ok, _msg = office.post_instruction(session, text, src="relay", req_id=_door_req_id(key))
                why = "" if ok else "refused"
        except (OSError, ValueError) as e:
            print(f"⚠ 口への受け渡し失敗・残置して再試行 ({session}・この通から {len(g['texts']) - i} 通を次周へ): {e}", flush=True)
            break
        except Exception as e:                            # noqa: BLE001 - 外へ出さない（他のセッションの配達と ack を巻き込まない）
            ok, why = False, "error"
            _door_audit("discard", bot_id, src="relay", why="error")
            print(f"⛔ 口への受け渡しで予期しない失敗＝捨てて監査 ({session}): {type(e).__name__}", flush=True)
        if iid is not None:
            ok_ids.append(iid)
        ok_keys.append(key)
        if ok:
            delivered += 1
            print(f"✓ Bot へ{what}を届けた {session}", flush=True)
        else:
            print(f"⛔ Bot 宛の{what}を口が受けなかった（{why}・捨てて監査） {session}", flush=True)
    return delivered, ok_ids, ok_keys


def _mark_seen(device_id, now):
    if now - _SEEN_AT.get(device_id, 0) < SEEN_EVERY:
        return
    _SEEN_AT[device_id] = now
    try:
        office.mark_device_used(device_id, now)
    except OSError as e:                  # 一覧の飾り＝配達は止めない
        print(f"⚠ 端末の使用時刻を控えられない: {e}", flush=True)


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
            if (ALLOW_UNSIGNED and it.get("session") and isinstance(raw, str) and raw
                    and not APV_SESSION_RE.match(str(it["session"])) and not ENR_SESSION_RE.match(str(it["session"]))
                    and not RST_SESSION_RE.match(str(it["session"]))):
                g = groups.setdefault(it["session"], {"ids": [], "texts": [], "keys": [], "devices": []})
                g["ids"].append(iid)
                g["texts"].append(raw)
                g["keys"].append(None)
                g["devices"].append("unsigned")   # 無署名は端末不明（ALLOW_UNSIGNED時のみ）
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
        _mark_seen(env["device_id"], now)   # b4-mac: 署名が通った＝その端末を今使っている（📱一覧の「最後に使用」）
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
        g = groups.setdefault(sess, {"ids": [], "texts": [], "keys": [], "devices": []})
        g["ids"].append(iid)
        g["texts"].append(text)
        g.setdefault("envs", []).extend([None] * (len(g["ids"]) - 1 - len(g.get("envs", []))))
        g["envs"].append(env)                    # R100-1b: apv-/enr- は封筒ごと daemon へ
        g["keys"].append((key, env["ts"] + WINDOW))
        g["devices"].append(env["device_id"])   # M2: 監査へ渡す検証済み端末ID   # nonce生存=封筒のverify有効期限（未来ts漏れ穴を塞ぐ）

    ack_ids = [x for x in ack_now if x is not None]
    delivered = 0
    committed = False
    for session, g in groups.items():
        if (APV_SESSION_RE.match(session) or ENR_SESSION_RE.match(session) or RST_SESSION_RE.match(session)
                or RKY_SESSION_RE.match(session)):
            # R100-1b: 許可／許可鍵の登録／R103-1: ON/OFF／R116-S1b: 延長。inbox へは絶対に書かない（汎用経路より前）。
            done_ids, done_keys = _deliver_envelopes(session, g)
            if done_ids:
                delivered += 1
                committed = True
                for k in done_keys:
                    if k is not None:
                        _NONCES[k[0]] = k[1]
                ack_ids.extend(done_ids)
            continue
        # R79-10: act-<16hex> は「遠隔実行アクション」＝office_inbox へは書かない。
        # 実行者は daemon（office_server・Automation TCC同意済み）＝ここは配達員のまま。
        # 掟: daemon に届いた（受理された）ときだけ ack＋nonceコミット。不達なら残置して次周へ。
        if DLG_SESSION_RE.match(session):
            # R87: 封書の要求。daemon に封じさせ、結果は次の sync の dlg レーンに載る。
            done_ids, done_keys = _deliver_dialog(session, g)
            if done_ids:
                delivered += 1
                committed = True
                for k in done_keys:
                    if k is not None:
                        _NONCES[k[0]] = k[1]
                ack_ids.extend(done_ids)
            continue
        if ACT_SESSION_RE.match(session):
            done_ids, done_keys = _deliver_actions(session, g)
            if done_ids:
                delivered += 1
                committed = True
                for k in done_keys:
                    if k is not None:
                        _NONCES[k[0]] = k[1]
                ack_ids.extend(done_ids)
            continue
        if session.startswith(office.office_door.SESSION_PREFIX):
            # R127: 出勤の口の Bot 宛（スマホの答え・指示）。下の汎用の経路（承認の掲示・Claude の受信箱）には落とさない
            # ＝表示していない相談への答え（id8）が「指示」に化けない・Bot の相談は掲示板に無い（不変条件 7）
            done, done_ids, done_keys = _deliver_door(session, g)
            if done:
                delivered += 1
            if done_keys:
                committed = True
                for k in done_keys:
                    _NONCES[k[0]] = k[1]
            ack_ids.extend(done_ids)
            continue
        ans = _answer_of(g["texts"][0])
        if ans is not None:
            # R102-11: スマホが**表示していた要求**（掲示の askId の先頭 8 桁）への答え。一致する掲示にだけ答える。
            # 掲示が替わった・もう無い（Mac で答えた）なら、見ていない要求へは答えず、言葉を指示として届ける
            # （以前は届いた時点の最古の掲示へ答えた＝読んでいない許可・質問への拒否になった・別モデルの監査）。
            id8, body = ans
            target = next((a for a in office.pending_approvals(session) if (a.get("askId") or "")[:8] == id8), None)
            try:
                if target:
                    office.write_approval_reply(session, "deny", _answer_text(target, body),
                                                src="relay", ask_id=target.get("askId"))
                    print(f"✓ 承認まちへ回答を配達 {session} ({target['kind']}・表示していた要求)", flush=True)
                else:
                    ok, _msg = office.post_instruction(session, body, src="relay")
                    print(f"↪ 答えた要求はもう終わっている＝指示として配達 {session} ok={ok}", flush=True)
            except (OSError, ValueError) as e:
                print(f"⚠ 回答の受け渡し失敗・残置して再試行 ({session}): {e}", flush=True)
                continue
            delivered += 1
            committed = True
            if g["keys"][0] is not None:
                _NONCES[g["keys"][0][0]] = g["keys"][0][1]
            ack_ids.extend(x for x in g["ids"][:1] if x is not None)   # 残り（2 通目以降）は次 tick
            continue
        chunk, n = _first_chunk(g["texts"])   # 4000字に収まる先頭chunkだけ（残りは次tickへ繰り越す）
        # R86-H: 相手が「いま人間に聞いていて止まっている」なら、指示ポストでは届かない
        # （ターンが終わらない＝Stop hook が起動しない）。承認フックが待っているので
        # そちらへ回答として渡す。★ここから allow は出せない（write_approval_reply の
        # src="relay" をフック側が構造的に拒否する）＝スマホは「言葉を届ける」だけ。
        ask = office.pending_approval(session)
        # R102-11: 要求の印が無い言葉（旧い画面・自由入力）は、送った後に出た掲示へは答えない（送った人はそれを見ていない）。
        # ASK_GRACE（3 秒）より若い掲示はスマホに出ない＝送信の時刻にその猶予を足しても間に合わない掲示は見えていなかった
        sent_at = _env_ts(g, 0)
        if ask and sent_at is not None and float(ask.get("ts") or 0) + office.ASK_GRACE > sent_at:
            ask = None
        if ask:
            try:
                office.write_approval_reply(session, "deny", _answer_text(ask, chunk),
                                            src="relay", ask_id=ask.get("askId"))
            except (OSError, ValueError) as e:
                print(f"⚠ 回答の受け渡し失敗・残置して再試行 ({session}): {e}", flush=True)
                continue
            delivered += 1
            committed = True
            for k in g["keys"][:n]:
                if k is not None:
                    _NONCES[k[0]] = k[1]
            ack_ids.extend(g["ids"][:n])
            print(f"✓ 承認まちへ回答を配達 {session} ({ask['kind']})", flush=True)
            continue
        try:
            ok, _msg = office.post_instruction(session, chunk, src="relay")
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
# R90-D12: denylist → **allowlist**。office_json に足した新フィールドは既定で中継に載らない
# （載せたいものはここへ明示的に追加する＝掟の機械化。docs/office-json.md が同じ表を持つ）。
# 互換: _REDACT_FIELDS の5キーはキーを残して空文字（PWA の `e.lastSaid` 参照と既存テストを壊さない）。
_ALLOW_ENTRY = frozenset({
    "session", "state", "kind", "verb", "age", "mtime", "minions", "minionsLive", "pending", "listening", "attention",
    "approvalMin", "question", "questionOptions", "stuckTool", "ask", "feed", "work", "skills",
    "dept", "role", "disp", "title", "name", "external", "site", "crew", "projectId", "sessions",
    "vendor", "detail", "arch", "color",
    "err", "permMode",                                      # R102-4: 止まった理由・権限モード（enum だけ・下で形を固める）
    "lastSaid", "target", "lastOrder", "cwd", "branch",      # 互換（空文字化）
})
_ERR_KINDS = frozenset({"api", "limit", "ctx"})
_PERM_MODES = frozenset({"default", "acceptEdits", "plan", "bypassPermissions", "dontAsk", "auto"})
_ALLOW_SESSION = frozenset({"session", "state", "age", "attention", "minions", "pending", "vendor"})
# entry.ask の中で中継に載るキー（_redact_entry_for_relay が形を固めたうえで、最後にこの表で切る）。
# title（コマンド全文・質問文）・options・askId・ts・askCount は載らない。
# R112-H: questions（AskUserQuestion の全部の問いと選択肢・質問のときだけ）を意図して足した＝docs/office-json.md の ask 行
_ALLOW_ASK = frozenset({"tool", "kind", "remote", "min", "qn", "multi", "id8", "questions"})
# R98: UI が読むが**意図して中継に載せない**フィールド（Mac の画面だけの情報）。
# test_allowlist_covers_ui_and_pwa_field_reads は「UI が読むのに allowlist に無い」を止める番人なので、
# ローカル専用はここに宣言して番人に伝える（黙って allowlist を広げない・黙って番人を弱めない）。
_LOCAL_ONLY_ENTRY = frozenset({
    "evidence",   # 台帳の証拠列 {kind, ago}＝スマホは描かない
    "aiTitle",    # R123-A: Claude Code の自動タイトル（会話から作られた文＝本文に近い）。スマホへ出すなら本人の裁定の後に allowlist へ
})
_ALLOW_TOP = frozenset({
    "officeName", "employees", "roster", "history", "today", "generatedAt", "setup",
    "actions", "relay", "res", "templates", "launchable", "lang", "avatarMode", "counts", "tasks",
    "v", "sources", "events", "growth",
    "macActive",                         # R102-5: Mac の前に人がいるか（0/1 だけ・下で形を固める）＝いる間は通知しない
})
# R90 裁定（2026-09-07）: bg の一行要約 detail は title と同じ扱い＝既定で通す・OFFICE_RELAY_DETAIL=0 で遮断
RELAY_DETAIL = os.environ.get("OFFICE_RELAY_DETAIL", "1") not in ("0", "false")
# R85-1: title（/rename のセッション名）は自由入力だが「ユーザーが意図して付けた短い表示名」
# ＝dept/disp と同じ性格。「スマホにも同じ名前を出す」をユーザーが明示裁定（2026-08-26）した
# ため既定で通す。OFFICE_RELAY_TITLES=0 で Mac 画面のみへ戻せる（値を空にする＝lastSaid と同流儀）。
RELAY_TITLES = os.environ.get("OFFICE_RELAY_TITLES", "1") not in ("0", "false")
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


# 2026-09-09（本人指摘）: works の `.env` は **API キーを取得してくるリスト**。
# bg の一行要約 `detail` は Haiku が自由に書くので、パスだけでなく**キーの値そのもの**を
# 書きうる（実測: `AWS_SECRET_ACCESS_KEY=AKIA…` はパス縮約を素通りした）。
# `office_actions.scrub_output` が既に持っている秘密の伏字を**同じ定義で共有**する
# （二重管理しない・向こうを強くすればこちらも強くなる）。
_SECRET_RES = office_actions._SECRET_RES


def _mask_secrets(text):
    for rx in _SECRET_RES:
        text = rx.sub("[secret]", text)
    return text


def _scrub_text_for_relay(text, limit):
    """パスは末尾名へ、URL はホスト名へ縮めてから `limit` 字で切る。

    **縮約と長さ制限を分ける**のが要点（2026-09-08）。以前は `_scrub_feed_line` が
    末尾で 60 字に切っていたので、それを detail に流用すると「一行要約は 200 字」という
    R90 の裁定が黙って 60 字へ縮んだ。用途ごとに長さを渡す。
    """
    if not isinstance(text, str):
        return text
    def replace_path(match):
        token = match.group(0)
        trailing = ""
        while token and token[-1] in ",.;:!?、。)]}」』":
            trailing = token[-1] + trailing
            token = token[:-1]
        return (Path(token).name if token else "") + trailing
    # URLを先に処理する（パス正規表現が `https://…` の `//…` を先に食うと token/query が残る）。
    # scheme ごとホスト名だけへ縮める（クエリ/パスに機微が乗りうる）。
    scrubbed = re.sub(r"https?://([^/\s?#]+)\S*", r"\1", text)
    return _mask_secrets(_WORK_PATH_RE.sub(replace_path, scrubbed))[:limit]


def _scrub_feed_line(ln):
    """S3: feed行のパスを末尾名へ縮め、長い本文（コマンド/パターン/URL/指示文）を切り詰める。
    先頭の動詞ラベル（実行中/編集中…＝describe_tool由来）は保つ＝「何をしているか」は伝わる。"""
    return _scrub_text_for_relay(ln, 60) if isinstance(ln, str) else ln


def _answer_of(text):
    """R102-11: スマホの「表示していた要求への答え」の封筒の本文。{"aioffice":1,"kind":"answer","ask":"<8 桁>","text":"…"}。
    形が違えば None（ふつうの言葉として扱う）。署名は本文の SHA-256 に畳まれる＝canonical と KAT は変わらない。"""
    if not isinstance(text, str) or not text.startswith('{"aioffice":1,"kind":"answer"'):
        return None
    try:
        a = json.loads(text)
    except ValueError:
        return None
    if not isinstance(a, dict) or a.get("aioffice") != 1 or a.get("kind") != "answer":
        return None
    id8, body = a.get("ask"), a.get("text")
    if not (isinstance(id8, str) and len(id8) == 8 and all(c in "0123456789abcdef" for c in id8)):
        return None
    if not isinstance(body, str) or not body.strip() or len(body) > 20000:
        return None
    return id8, body


def _env_ts(g, i):
    """i 番目の封筒の署名時刻（送った時刻）。無署名（移行用）なら None。"""
    envs = g.get("envs") or []
    env = envs[i] if i < len(envs) else None
    ts = env.get("ts") if isinstance(env, dict) else None
    return float(ts) if isinstance(ts, (int, float)) and not isinstance(ts, bool) else None


def _answer_text(ask, text):
    """スマホからの回答文をフックへ渡す形に整える。

    許可(allow)が必要な操作に「はい」と答えられても、こちらは実行を通せない。
    モデルが「内容が気に入らなくて拒否された」と誤解しないよう、事実を1行添える
    （ここを曖昧にすると同じツールを何度も再試行して詰まる）。
    計画の承認（ExitPlanMode）はフックが question として掲示するが、中継からの回答は deny＝計画の差し戻し
    にしかならない（掟 7）。「OK、進めて」が却下理由としてだけ届かないよう、承認されていない事実を 1 行添える。"""
    text = (text or "").strip()
    if ask.get("tool") == "ExitPlanMode":
        return text + "\n（計画は承認されていません。承認は Mac のターミナルで）"
    if ask.get("kind") == "question":
        return text
    return (text + "\n（AIオフィス経由のユーザー回答です。実行の許可はMacのターミナルでのみ"
                   "与えられるため、この操作自体は実行されていません。上の回答に従ってください）")


def _small_int(v, hi):
    """0 以上 hi 未満の整数（bool は除く）。"""
    return type(v) is int and 0 <= v < hi


def _redact_entry_for_relay(e):
    """社員/プロジェクト1件から本文・パスを落とす（employees[] と projects[] で共通）。"""
    if not isinstance(e, dict):
        return
    for k in list(e):                       # R90-D12: allowlist 外のキーはここで消える（bg/pid/tokens/homeCwd…）
        if k not in _ALLOW_ENTRY:
            del e[k]
    for k in _REDACT_FIELDS:
        if k in e:
            e[k] = ""
    if not RELAY_TITLES and "title" in e:
        e["title"] = ""                    # R85-1: opt-out時はリネーム名も根元遮断
    if "detail" in e:
        # 2026-09-08: **detail も feed と同じスクラブを通す**。R90 の裁定で「一行要約は中継に載せる」
        # と決めたが、載せてよいのは「何をしているか」であって**パスや URL ではない**。
        # detail は Haiku が生成する自由文で、実測で `/Users/<実名>/project/…/office_server.py を直した`
        # のような絶対パスがそのまま入る＝Cloudflare とスマホへ実名パスが出ていた（feed は縮んでいた）。
        e["detail"] = (_scrub_text_for_relay(e["detail"], 200)
                       if (RELAY_DETAIL and isinstance(e["detail"], str)) else "")
    if isinstance(e.get("sessions"), list):
        e["sessions"] = [{k: v for k, v in s.items() if k in _ALLOW_SESSION}
                         for s in e["sessions"] if isinstance(s, dict) and not _is_door_row(s)]
    fd = e.get("feed")
    if isinstance(fd, list):
        # 「💬 …」＝発言本文の要約行は除去。残す動作ログ行（実行中/編集中…）も、Bashコマンド・
        # Grepパターン・URL・サブエージェント指示文・絶対/チルダパスを含みうる（describe_tool は
        # Read/Edit しか basename 化しない）。S3修正: target と同じ値が feed にも入る自己矛盾を塞ぐ
        # ため、残す行にも _scrub_feed_line でパス縮約＋本文の切り詰めを掛ける（中継＝根元遮断）。
        e["feed"] = [_scrub_feed_line(ln) for ln in fd
                     if not (isinstance(ln, str) and ln.lstrip().startswith("💬"))]
    if isinstance(e.get("stuckTool"), str):
        # R99-P4-0: stuckTool は server が "{verb} {target}" で作る（承認まちのときだけ）。target は上で
        # 空にしているのに、同じ本文（Bash コマンド全文・URL・パス）がここに残って中継へ出ていた。
        # feed と同じ縮約を掛ける（動詞は残る＝「何で止まっているか」と ⚠ の材料は消えない）。
        e["stuckTool"] = _scrub_feed_line(e["stuckTool"])
        if isinstance(e.get("ask"), dict):
            # R100-1b: 許可を聞いている間は動詞だけ（全文は登録した端末へ封書で届く＝平文の要約を中継に残さない）
            e["stuckTool"] = e["stuckTool"].split(" ", 1)[0]
    if "work" in e:
        e["work"] = _sanitize_work_for_relay(e["work"])
    if "err" in e:
        # R102-4: 種類の enum と経過秒だけ（エラーの本文・API の応答は持たない）
        er = e["err"]
        if isinstance(er, dict) and er.get("k") in _ERR_KINDS:
            ago = er.get("ago")
            e["err"] = {"k": er["k"], "ago": int(ago) if type(ago) is int and 0 <= ago < 10 ** 9 else 0}
        else:
            del e["err"]
    if "permMode" in e and e["permMode"] not in _PERM_MODES:
        del e["permMode"]
    if "minionsLive" in e and not _small_int(e["minionsLive"], 10 ** 4):
        del e["minionsLive"]                                  # 直近 60 秒に書いた子の数（整数だけ）
    if isinstance(e.get("ask"), dict):
        # R86-H: 「聞かれている」事実は運ぶが、聞かれている**中身**は運ばない。
        # title は Bash コマンド全文・質問文そのもの＝本文。tool/kind だけ残す（例外: R112-H の questions＝質問のときだけ・下）。
        # R100-1b: remote＝hook の危険判定が「スマホから許可できる形」と言ったか（0/1 だけ）
        a = e["ask"]
        e["ask"] = {"tool": a.get("tool", ""), "kind": a.get("kind", ""),
                    "remote": 1 if type(a.get("remote")) is int and a["remote"] == 1 else 0}
        # 掲示からの分・質問の問いの数・複数選択（整数だけ・本文は持たない）。形が違えばキーごと落とす
        for k, hi in (("min", 10 ** 6), ("qn", 100), ("multi", 2)):
            if _small_int(a.get(k), hi):
                e["ask"][k] = a[k]
        # R102-11: 掲示の識別（askId の先頭 8 桁＝hook が作る乱数・本文ではない）。スマホは答えにこれを添え、
        # relay_agent は一致する掲示にだけ答える（見ていない要求に答えない）・「送信しました」も要求ごとに照合する
        aid = a.get("askId")
        if isinstance(aid, str) and len(aid) >= 8 and all(c in "0123456789abcdef" for c in aid[:8]):
            e["ask"]["id8"] = aid[:8]
        # R112-H: AskUserQuestion の全部の問いと選択肢（意図して運ぶ＝entry の question/questionOptions と同じ「回答のために通す」）。
        # 本人の報告（2026-10-07）: 1 問目しか運ばず、2〜4 問を束ねた質問にスマホから答えられなかった。
        # 質問のときだけ（許可の掲示はコマンド全文になりうる＝構造的に載せない）・office_json と同じ検証（形が違えば何も足さない）
        if a.get("kind") == "question" and a.get("tool") == "AskUserQuestion":
            qs = office.clean_ask_questions(a.get("questions"))
            if qs:
                e["ask"]["questions"] = qs
        for k in list(e["ask"]):           # 中継に載る ask のキーは _ALLOW_ASK の表だけ（足すときは表とテストを同時に）
            if k not in _ALLOW_ASK:
                del e["ask"][k]


def _is_door_row(e):
    """出勤の口の Bot の行（vendor "bot" か session "bot-…"）。R126 は中継に一切載せなかった。R127（不変条件 8 の改定）から
    ふつうの行の allowlist ではなく、Bot だけの狭い allowlist（_redact_door_entry）で載せる。"""
    return isinstance(e, dict) and (e.get("vendor") == "bot"
                                    or str(e.get("session") or "").startswith(office.office_door.SESSION_PREFIX))


# R127（不変条件 8 の改定・本人裁定 2026-11-10「Bot の文と Bot への答えは中継を通してよい」）: 出勤の口の Bot の行だけの allowlist。
# 載るのは Bot が自分で書いた問い・選択肢・出勤の様子（verb＝doing は縮約）と、名前・役・状態・識別だけ。
# 本文とパスの鍵（cwd・lastSaid・lastOrder・target・branch）・feed・sessions・title・detail・work・err・stuckTool は**鍵ごと無い**
# （Bot の行は Mac のセッションの文を持たない・手違いで持っていても出さない）。足すときはこの表・docs/office-json.md・test_relay_agent を同時に。
_ALLOW_DOOR_ENTRY = frozenset({
    "session", "vendor", "projectId", "crew", "name", "dept", "disp", "role",
    "state", "kind", "verb", "age", "mtime", "pending", "listening", "attention", "approvalMin",
    "question", "questionOptions", "ask",
})
# Bot の ask で中継に載る鍵。tool は "office_ask"・kind は "question"・remote は 0 に固定（Bot は許可を出せない＝スマホに許可の
# カードを出させない）。min＝相談からの分・id8＝askId の先頭 8 桁（スマホが答えに添える印）。問いの文・選択肢・askId 全体は載らない。
_ALLOW_DOOR_ASK = frozenset({"tool", "kind", "remote", "min", "id8"})
_DOOR_STATES = frozenset({"working", "waiting", "resting"})
_DOOR_KINDS = frozenset({"tool", "said", "idle"})
_HEX8 = re.compile(r"[0-9a-f]{8}")


def _redact_door_entry(e):
    """R127: Bot の行（employees・roster の 1 件）から中継に載せる**新しい dict** を作る（入力は書き換えない）。宛先の形が違う
    （"bot-<id>" でない）行は None＝載せない（スマホから答えられない行を出さない）。形は固める: 名前・役・問い・選択肢は口と同じ
    上限で切る・状態と種別は enum・数は整数・verb（Bot の doing）は feed と同じ縮約（パスは末尾名・URL はホスト・秘密は伏字・60 字）。
    問いと選択肢は**縮約しない**（人が読んで答える文・選択肢の文は口が答えの「選択肢「X」…」と突き合わせる＝変えると choice が外れる）。"""
    door = office.office_door
    sess = e.get("session")
    if not (isinstance(sess, str) and door.bot_of_session(sess)):
        return None
    out = {"session": sess, "vendor": "bot"}
    for k, cap in (("name", door.NAME_LEN), ("dept", door.NAME_LEN), ("disp", door.NAME_LEN + 8), ("role", door.ROLE_LEN)):
        if isinstance(e.get(k), str):
            out[k] = e[k][:cap]
    if isinstance(e.get("projectId"), str) and len(e["projectId"]) <= 64:
        out["projectId"] = e["projectId"]
    if _small_int(e.get("crew"), 10 ** 4):
        out["crew"] = e["crew"]
    if e.get("state") in _DOOR_STATES:
        out["state"] = e["state"]
    if e.get("kind") in _DOOR_KINDS:
        out["kind"] = e["kind"]
    out["verb"] = _scrub_text_for_relay(e["verb"], 60) if isinstance(e.get("verb"), str) else ""
    out["age"] = e["age"] if _small_int(e.get("age"), 10 ** 9) else 0
    mt = e.get("mtime")
    if type(mt) in (int, float) and 0 <= mt < 10 ** 11:
        out["mtime"] = mt
    for k in ("pending", "listening", "attention"):
        if type(e.get(k)) is bool:
            out[k] = e[k]
    out["approvalMin"] = 0                                 # Bot は許可を待たない（掲示板を使わない）
    out["question"] = e["question"][:door.ASK_Q_LEN] if isinstance(e.get("question"), str) else ""
    opts = e.get("questionOptions")
    if isinstance(opts, list):
        labels = [o["label"][:door.ASK_OPT_LEN] for o in opts[:door.ASK_OPT_MAX]
                  if isinstance(o, dict) and isinstance(o.get("label"), str) and o["label"]]
        if labels:
            out["questionOptions"] = [{"label": lb, "desc": ""} for lb in labels]
    a = e.get("ask")
    if isinstance(a, dict) and a.get("kind") == "question":
        ask = {"tool": "office_ask", "kind": "question", "remote": 0}
        if _small_int(a.get("min"), 10 ** 6):
            ask["min"] = a["min"]
        aid = a.get("askId")
        if isinstance(aid, str) and len(aid) == 32 and _HEX8.fullmatch(aid[:8]):
            ask["id8"] = aid[:8]
        out["ask"] = {k: v for k, v in ask.items() if k in _ALLOW_DOOR_ASK}
    return {k: v for k, v in out.items() if k in _ALLOW_DOOR_ENTRY}


def _redacted_entry(e):
    """_redact_entry_for_relay を**写し**にかける（R116-S0）。入れ子（ask・sessions・feed・work・err）は
    _redact_entry_for_relay が作り直して差し替える＝浅い写しで元の dict は 1 か所も変わらない（test_relay_agent の R116 が番人）。
    R127: Bot の行は _redact_door_entry（狭い allowlist・形の合わない行は None＝載せない）。"""
    if not isinstance(e, dict):
        return e
    if _is_door_row(e):
        return _redact_door_entry(e)
    c = dict(e)
    _redact_entry_for_relay(c)
    return c


def _redact_office_for_relay(office_snapshot):
    """office_json() の結果から機微になりうる本文/パスを除去した**新しい dict** を返す（入力は書き換えない）。
    残す=state/kind/verb/feedの動作ログ(実行中・編集中…)/question/disp/dept/role/age/minions/approvalMin/stuckTool、
    および title（/renameのセッション名＝ユーザー裁定2026-08-26で既定通過・OFFICE_RELAY_TITLES=0で遮断）。
    落とす=lastSaid・target(本文由来)・lastOrder・cwd・branch、および feed の「💬 発言」行。

    ★R116-S0: 入力は office_json() の **CACHE_SEC（2 秒）キャッシュの dict そのもの**（同じプロセスの次の呼び出しも同じ
    dict を受け取る）。以前はその場で削っていたので、ws_loop のローカル走査と直後の sync（_sync_request）が同じ dict に
    2 回かけると、1 回目で ask.askId が消え、中継へ送る 2 回目から掲示の印 id8 が落ちた（R102-11 の「表示した 1 件にだけ
    答える」とスマホの全文の先読みが本番で効いていなかった）うえ、印あり／なしの指紋が交互に立って走査のたびに sync を送った。

    R82: templates（ユーザー定義定型文）は**意図的に素通し**＝スマホから使うために保存する
    再利用フレーズであり、上限8件×120字・label/textのみ（office_server.load_templates が正規化）。
    R50: roster[] も同じ規則で落とす。projectId は cwd のハッシュ（パスを含まない）なので残す。
    sessions[] は _session_brief が本文を構造的に持たない形で作っているのでそのまま通す。"""
    if not isinstance(office_snapshot, dict):
        return office_snapshot
    # R90-D12: トップレベルも allowlist（R116-S0: 新しい dict に写す＝キャッシュの dict からは消さない）
    office_snapshot = {k: v for k, v in office_snapshot.items() if k in _ALLOW_TOP}
    if "macActive" in office_snapshot and office_snapshot["macActive"] not in (0, 1) \
            or type(office_snapshot.get("macActive", 0)) is bool:
        del office_snapshot["macActive"]
    for key in ("employees", "roster"):
        if isinstance(office_snapshot.get(key), (list, tuple)):     # 以前と同じ範囲（列の中の dict だけを伏字にする）
            # R127: Bot の行も載る（_redact_door_entry の狭い allowlist）。counts は Mac と同じ（Bot を含む）＝引かない
            rows = [_redacted_entry(e) for e in office_snapshot[key]]
            office_snapshot[key] = [r for r in rows if r is not None]
    # sources / events / growth は「件数・bool・数値」だけに刈る（形が増えても本文やパスが乗らない）
    src = office_snapshot.get("sources")
    if isinstance(src, dict):
        office_snapshot["sources"] = {
            "claude": {k: v for k, v in (src.get("claude") or {}).items() if k in ("fg", "bg", "agentsCli")},
            "codex": {k: v for k, v in (src.get("codex") or {}).items() if k in ("connected", "n")},
            "openclaw": {k: v for k, v in (src.get("openclaw") or {}).items() if k in ("connected",)},
        }
    ev = office_snapshot.get("events")
    if isinstance(ev, dict):
        office_snapshot["events"] = {k: v for k, v in ev.items() if k in ("seq", "wired")}
    gr = office_snapshot.get("growth")
    if isinstance(gr, dict):
        by = gr.get("byProject") if isinstance(gr.get("byProject"), dict) else {}
        office_snapshot["growth"] = {
            "byProject": {pid: {k: v for k, v in (row or {}).items() if k in ("xp", "level")}
                          for pid, row in by.items() if isinstance(row, dict)},
            "office": {k: v for k, v in (gr.get("office") or {}).items() if k in ("xp", "level", "nextAt")},
            "streak15": gr.get("streak15", 0),
            # R97: オフィスの広さ（机の数）を決める「同時に居た最大人数」。ただの整数で、
            # 誰が・何をしたかは載らない。スマホも別ブラウザ＝これが無いと部屋が縮む。
            "maxSeen": gr.get("maxSeen", 0),
        }
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


# R127: Bot の行の age・ask.min は送信の指紋で 10 分に丸める（長く居る Bot・長く待つ相談が毎分の sync を起こさない。
# 計画書 §1 守る線「無料枠」。表示の値は丸めない＝中継には他の変化か heartbeat の送信に相乗りで新しい値が載る）
DOOR_FP_MINUTES = 10


def _quantize_entry(e):
    if not isinstance(e, dict):
        return e
    q = dict(e)
    door = _is_door_row(q)
    if "age" in q:
        try:
            q["age"] = int(q.get("age") or 0) // (60 * (DOOR_FP_MINUTES if door else 1))
        except (TypeError, ValueError):
            pass
    if door and isinstance(q.get("ask"), dict) and "min" in q["ask"]:
        try:
            q["ask"] = dict(q["ask"], min=int(q["ask"].get("min") or 0) // DOOR_FP_MINUTES)
        except (TypeError, ValueError):
            pass
    if isinstance(q.get("err"), dict) and "ago" in q["err"]:
        # R102-4: 止まってからの秒も時間だけで毎周変わる＝age と同じく分単位（量子化しないと 2 秒ごとに送り直す）
        try:
            q["err"] = dict(q["err"], ago=int(q["err"].get("ago") or 0) // 60)
        except (TypeError, ValueError):
            pass
    if isinstance(q.get("sessions"), list):
        q["sessions"] = [_quantize_entry(s) for s in q["sessions"]]
    return q


def _status_fingerprint(snapshot):
    """push省略判定の指紋。時間経過だけで毎周変わる揮発値を量子化してから畳む
    （generatedAt・today.lastSentAgo=落とす・age=分単位。ここを量子化しないと毎周「変化あり」になり省略が効かない）。"""
    if not isinstance(snapshot, dict):
        return ""
    # S2: relay(中継使用量の生rows)を含めると「1回syncするたびにrowsが増えて指紋が変わり→
    # また送信…」の自己参照ループになり「変化時のみ送信」が無効化する（＝無料枠を食う本丸）。
    # generatedAt と同じく指紋から外す。res.staleSec も時間ドリフトなので分単位へ量子化。
    copy = {k: v for k, v in snapshot.items() if k not in ("generatedAt", "relay")}
    if isinstance(copy.get("today"), dict) and "lastSentAgo" in copy["today"]:
        # R116-S0（反証）: today.lastSentAgo＝最後の送信からの**秒**（office_server が走査のたびに now から引く）。送信履歴が
        # 1 行でもある HOME（_history.json は 50 件を期限なしで持つ＝本番は常に）では走査のたびに増え、不在中は 30 秒・在席中は
        # 2 秒ごとに office を載せた sync（DO 4 行）を送り続けていた。スマホは読まない（読むのは Mac の ui/iso だけ）＝generatedAt と
        # 同じく外す（分に量子化しても毎分の送り直しが残る）。新しい送信は today.sent の増分で指紋に出る。
        copy["today"] = {k: v for k, v in copy["today"].items() if k != "lastSentAgo"}
    for key in ("roster", "employees"):
        if isinstance(copy.get(key), list):
            copy[key] = [_quantize_entry(e) for e in copy[key]]
    if isinstance(copy.get("res"), dict):
        r = dict(copy["res"])
        if "staleSec" in r:
            try:
                r["staleSec"] = int(r.get("staleSec") or 0) // 60
            except (TypeError, ValueError):
                r["staleSec"] = 0
        copy["res"] = r
    return hashlib.sha256(
        json.dumps(copy, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()


def _any_attention(snapshot):
    return bool(_attention_keys(snapshot))


def _attention_keys(snapshot):
    """❗を持つ相手のキー集合。R79: burst の判定を「❗が存在する」から
    「❗が**新しく出た**（エッジ）」へ変えるための材料。

    旧実装は存在レベルで判定していたため、承認まちが1件でも放置されていると
    毎tickで burst が再武装され、60秒運用のつもりが**恒久的に8秒周期**へ張り付いた
    （実測 10,800 req/日＝意図の7.5倍）。エッジなら「出た瞬間だけ速回し」になる。"""
    keys = set()
    for e in (snapshot.get("roster") or []) + (snapshot.get("employees") or []):
        if isinstance(e, dict) and (
                e.get("question") or e.get("attention") or (e.get("approvalMin") or 0) > 0):
            k = str(e.get("projectId") or e.get("session") or "")
            if k:
                keys.add(k)
    return keys


# R80-C2: openclaw集約の取得間隔。**Worker は wantOpenclaw を受けると別DO(macmini)を
# RPCで叩き、DO RPC は 20:1 圧縮の対象外＝1:1 課金**になる。毎syncで立てると WS化で
# 圧縮したはずのリクエストが丸ごと戻ってくる（scan 2秒なら 43,200 DO req/日＝枠の43%）。
# openclaw の状態は人間が見るもので秒単位の鮮度は要らないので、間隔で間引く。
OPENCLAW_MIN_INTERVAL = float(os.environ.get("OFFICE_OPENCLAW_INTERVAL", "60"))


def _want_openclaw(state=None, now=None):
    """Keep cross-DO aggregation throttled independently of display settings."""
    if not isinstance(state, dict):
        return True          # 状態を持たない呼び出し（後方互換）は従来どおり要求
    now = time.time() if now is None else now
    return (now - float(state.get("oc_fetched_at") or 0.0)) >= OPENCLAW_MIN_INTERVAL


def _sync_request(state):
    """/sync（HTTP）・{"t":"sync"}（WS）共通のリクエスト組み立て。
    (body, snapshot, fp, send_office, now) を返す。"""
    now = time.time()
    snapshot = _redact_office_for_relay(office.office_json())
    fp = _status_fingerprint(snapshot)
    send_office = (fp != state.get("fp")
                   or now - state.get("pushed_at", 0.0) >= PUSH_HEARTBEAT)
    body = {"office": snapshot if send_office else None,
            "ackIds": list(state.get("acks") or []),
            "wantOpenclaw": _want_openclaw(state, now)}
    # R87: 封書のレーン。office_json とは別の指紋＝会話の変化で office を再送しない・その逆も無い。
    dlg_text, dlg_fp = _dialog_payload(state)
    if dlg_text is not None and dlg_fp != state.get("dlg_fp", ""):
        body["dlg"] = dlg_text
        state["_dlg_fp_pending"] = dlg_fp
    return body, snapshot, fp, send_office, now


def _sync_apply(d, state, snapshot, fp, send_office, now, url, token):
    """sync応答の適用（HTTP/WS共通）。(delivered, appSeenAgo, appOnline) を返す。
    掟: items は peek＝配達成功分の id を state["acks"] に積み、**次の sync** で削除する
    （at-least-once。ack前にクラッシュしても再取得→既視nonce→ack で二重配達しない）。"""
    if send_office:
        state["fp"] = fp
        state["pushed_at"] = now
    if "_dlg_fp_pending" in state:
        state["dlg_fp"] = state.pop("_dlg_fp_pending")     # 中継が受けたので確定
    # R79: ❗は「存在」ではなく「新規遷移（エッジ）」で burst を張る。
    # 前周から増えたキーがあるときだけ True＝放置された承認まちで張り付かない。
    attn_keys = _attention_keys(snapshot)
    state["attn"] = bool(attn_keys - state.get("attn_keys", frozenset()))
    state["attn_keys"] = attn_keys
    delivered, ack_ids = _process_items(d.get("items") or [])
    state["acks"] = ack_ids
    # R42.5: oc-宛の転送（outboxが空ならリクエストゼロ・失敗は本流を巻き込まない）。
    # Throttle aggregation only; instructions retain their existing delivery timing.
    try:
        state["oc_sent"] = forward_oc_outbox(url, token)
    except Exception as e:
        state["oc_sent"] = 0
        print(f"⚠ oc-転送のみ失敗（本流は完了済み）: {e}", flush=True)
    # R80-C2: 応答に openclaw が載っている周＝要求した周だけ保存し、取得時刻を刻む
    if d.get("openclaw") is not None:
        state["oc_fetched_at"] = now
        try:
            _save_openclaw_contract(d.get("openclaw"))
        except OSError as e:
            print(f"⚠ OpenClaw集約の保存のみ失敗: {e}", flush=True)
    # R80: 中継の使用量（今日の書込行数と無料枠比）。UIへ出し、scan間隔の減速にも使う
    usage = d.get("usage")
    if isinstance(usage, dict):
        state["usage"] = usage
        try:
            office.set_relay_usage(usage)      # office_json 経由でUIへ（失敗しても本流は続行）
        except Exception:
            pass
    seen = d.get("appSeenAgo")
    return (delivered,
            int(seen) if isinstance(seen, (int, float)) else None,
            bool(d.get("appOnline")))


def sync_tick(url, token, state):
    """1周1リクエスト（HTTP退避経路の正本）: 前周ackの送達＋配達items取得＋
    状況push（変化時のみ）＋mini集約＋在席検知。(delivered, appSeenAgo, appOnline) を返す。"""
    body, snapshot, fp, send_office, now = _sync_request(state)
    try:
        d = _req("POST", url + "/sync", token, body)
    except urllib.error.HTTPError as e:
        if e.code in (404, 405):
            raise SyncUnsupported() from e
        raise
    if not isinstance(d, dict) or not d.get("ok"):
        raise SyncUnsupported()
    return _sync_apply(d, state, snapshot, fp, send_office, now, url, token)


# ── R79-8: WebSocket常時接続（既定ON・RELAY_WS=0 で即HTTPへ・--once は常にHTTP＝E2E決定論） ──
# 経済: 送信メッセージはDO受信20:1課金・無変化なら0メッセージ。ローカルscan（リクエスト0円）を
# スマホ在席(appOnline)で 2秒↔30秒 に切替＝体感ライブ・無人時は静か。
WS_ENABLED = os.environ.get("RELAY_WS", "1") != "0"
WS_FAIL_DEMOTE = 3           # 連続失敗この回数で
WS_DEMOTE_SECONDS = 600.0    # この秒数 HTTP /sync へ降格（自動復帰＝リスク3の退避策）
WS_KEEPALIVE = 25.0          # "p" 送信間隔（DO auto-response "P"＝課金ゼロ・DOも起きない）
WS_DEAD_AFTER = 90.0         # 無受信がこの秒数続いたら死んだとみなし再接続
WS_SYNC_TIMEOUT = 15.0       # sync応答の待ち上限
SCAN_FAST, SCAN_SLOW = 2.0, 30.0


# R80: 使用量レベル（Workerが返す usage.level）に応じた減速倍率。
# 0=通常 / 1=無料枠50%超（半分の速さ）/ 2=80%超（最小限）。
# **枠を割ってから止まるのではなく、割る前に自分で遅くする**のが狙い。
USAGE_SLOWDOWN = {0: 1.0, 1: 2.0, 2: 6.0}


def _scan_interval(app_online, usage_level=0):
    """スマホがWS在席なら2秒（ライブ感）・無人なら30秒（省エネ）。
    使用量が無料枠に近づいたら倍率で伸ばす。純関数＝単体でピン。"""
    base = SCAN_FAST if app_online else SCAN_SLOW
    try:
        mult = USAGE_SLOWDOWN.get(int(usage_level), 1.0)
    except (TypeError, ValueError):
        mult = 1.0
    return base * mult


def _ws_url(url):
    if url.startswith("https://"):
        return "wss://" + url[len("https://"):] + "/ws?role=agent"
    if url.startswith("http://"):
        return "ws://" + url[len("http://"):] + "/ws?role=agent"
    return url + "/ws?role=agent"


def _ws_sync_roundtrip(ws, state, url, token):
    """WS上で1周: {"t":"sync"} 送信 → 応答適用。(delivered, appOnline) を返す。
    応答が来なければ WSError（呼び出し側が再接続を裁く）。"""
    body, snapshot, fp, send_office, now = _sync_request(state)
    ws.send_text(json.dumps({"t": "sync", **body}, ensure_ascii=False))
    deadline = time.time() + WS_SYNC_TIMEOUT
    while True:
        remain = deadline - time.time()
        if remain <= 0:
            raise wsc.WSError("sync応答タイムアウト")
        m = ws.recv(timeout=remain)
        if m is None:
            raise wsc.WSError("sync応答タイムアウト")
        if m == "P":
            continue
        try:
            d = json.loads(m)
        except ValueError:
            continue
        if isinstance(d, dict) and d.get("t") == "sync":
            if not d.get("ok"):
                raise wsc.WSError(f"sync拒否: {d}")
            n, _seen, app_online = _sync_apply(
                d, state, snapshot, fp, send_office, now, url, token)
            return n, app_online
        # 同期中に届いた wake は読み捨てず旗に変える（捨てると最悪30秒の配達遅れ）
        if isinstance(d, dict) and d.get("t") == "wake":
            state["_wake_pending"] = True


def ws_loop(url, token, state):
    """WS常時接続の1セッション。例外（切断・無応答）でのみ戻る。
    idle中は recv がそのまま wake 待ち＝指示は投函の瞬間に届く。"""
    ws = wsc.WSClient(_ws_url(url), token=token, timeout=10)
    ws.connect()
    print(f"🔌 WS接続（wake駆動・scan {SCAN_FAST:g}s↔{SCAN_SLOW:g}s）", flush=True)
    try:
        app_online = False
        last_ka = time.time()
        last_rx = time.time()
        need_sync = True          # 接続直後に1回フル同期（再接続の冪等再同期）
        while True:
            if need_sync or state.get("acks"):
                n, app_online = _ws_sync_roundtrip(ws, state, url, token)
                last_rx = time.time()
                if n:
                    print(f"📨 {n}セッションへ配達", flush=True)
                # 配達したら ack を即flush（次スキャンを待たず1周＝at-least-once窓を最短化）。
                # 同期中に来た wake も取りこぼさない
                need_sync = bool(state.get("acks")) or state.pop("_wake_pending", False)
                continue
            lvl = int((state.get("usage") or {}).get("level") or 0)
            m = ws.recv(timeout=_scan_interval(app_online, lvl))
            now = time.time()
            if m is not None:
                last_rx = now
                if m != "P":
                    try:
                        d = json.loads(m)
                    except ValueError:
                        d = None
                    if isinstance(d, dict) and d.get("t") == "wake":
                        need_sync = True   # 投函→即配達（これがWS化の本体）
                        continue
            # ローカルscan（リクエスト0円）: 変化かheartbeat期限のときだけsyncを送る
            snapshot = _redact_office_for_relay(office.office_json())
            # 使用量が高いほど heartbeat も伸ばす（無変化時の定期pushを減らす）
            hb = PUSH_HEARTBEAT * USAGE_SLOWDOWN.get(lvl, 1.0)
            if (_status_fingerprint(snapshot) != state.get("fp")
                    or now - state.get("pushed_at", 0.0) >= hb
                    or _dialog_payload(state)[1] != state.get("dlg_fp", "")):   # R87: 封書の変化
                need_sync = True
                continue
            if now - last_ka >= WS_KEEPALIVE:
                ws.send_text("p")   # auto-response "P"＝課金ゼロ・接続の生死だけ確かめる
                last_ka = now
            if now - last_rx >= WS_DEAD_AFTER:
                raise wsc.WSError(f"無応答{int(now - last_rx)}秒（keepalive途絶）")
    finally:
        ws.close()


def tick(url, token):
    """1周: 配達 → oc-転送 → 状況push → mini集約。各段の失敗は互いを巻き込まない"""
    n = pull_and_deliver(url, token)
    try:
        push_status(url, token)
    except NET_ERRORS as e:
        print(f"⚠ 状況送信のみ失敗（配達は完了済み）: {e}", flush=True)
    try:
        forward_oc_outbox(url, token)
        pull_openclaw_status(url, token)
    except Exception as e:
        print(f"⚠ OpenClaw集約のみ失敗（本流は完了済み）: {e}", flush=True)
    return n


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
    # R42.2 のライセンスゲート（_license_gate_ok ループ）は R84 全機能無料化で撤去。
    _load_nonces()   # 既視 nonce を復元（再起動後も窓内リプレイを塞ぐ）
    ws_mode = WS_ENABLED and not once   # --once は常にHTTP＝E2E/デバッグの決定論を守る
    print(f"📡 中継エージェント起動: {url} "
          f"({'WS常時接続・退避HTTP' + format(interval, 'g') + 's' if ws_mode else '間隔' + format(interval, 'g') + 's'}"
          f"{' ・1周のみ' if once else ''})", flush=True)
    # R115-S3: res の更新（status_board の読み・外部 HTTPS＝プロバイダごとに最大 6 秒）を配達ループの外へ＝切れたら裏で 1 本だけ
    # 更新して旧値を返す（スマホの許可の配達が外部のタイムアウトで遅れない）。--once は同期のまま（E2E/デバッグの決定論）
    if not once:
        office._ASYNC_RES = True
    state = {"acks": [], "fp": None, "pushed_at": 0.0, "attn": False}
    sync_ok = True     # まず /sync（1周1リクエスト）。旧Workerなら一度だけレガシーへ降格
    burst_until = 0.0
    ws_fail = 0
    ws_down_until = 0.0
    while True:
        # ── R79-8: WS常時接続が主経路。切断は再接続・3連続失敗で10分だけHTTPへ降格（自動復帰） ──
        if ws_mode and sync_ok and time.time() >= ws_down_until:
            started = time.time()
            try:
                ws_loop(url, token, state)   # 例外でのみ戻る
            except SyncUnsupported:
                sync_ok = False
                print("ℹ Worker が /sync 未対応＝レガシー経路で継続", flush=True)
            except Exception as e:   # WSは何が来ても本流(HTTP退避)を守る＝握って降格判定
                lived = time.time() - started
                ws_fail = 1 if lived > 60 else ws_fail + 1   # 長寿命後の切断=CF再利用の正常系
                print(f"⚠ WS切断（{lived:.0f}s生存・連続{ws_fail}回目）: {e}", flush=True)
                if ws_fail >= WS_FAIL_DEMOTE:
                    ws_down_until = time.time() + WS_DEMOTE_SECONDS
                    ws_fail = 0
                    print(f"↩ {int(WS_DEMOTE_SECONDS / 60)}分間 HTTP /sync へ降格"
                          "（フレーミング/経路の障害でも配達は退化しない・後で自動復帰）", flush=True)
                time.sleep(min(2.0 * ws_fail + 1.0, 10.0))
            continue
        appseen = None
        try:
            if sync_ok:
                try:
                    n, appseen, _app_online = sync_tick(url, token, state)
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
