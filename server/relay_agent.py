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
        g["keys"].append((key, env["ts"] + WINDOW))
        g["devices"].append(env["device_id"])   # M2: 監査へ渡す検証済み端末ID   # nonce生存=封筒のverify有効期限（未来ts漏れ穴を塞ぐ）

    ack_ids = [x for x in ack_now if x is not None]
    delivered = 0
    committed = False
    for session, g in groups.items():
        # R79-10: act-<16hex> は「遠隔実行アクション」＝office_inbox へは書かない。
        # 実行者は daemon（office_server・Automation TCC同意済み）＝ここは配達員のまま。
        # 掟: daemon に届いた（受理された）ときだけ ack＋nonceコミット。不達なら残置して次周へ。
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


def _scrub_feed_line(ln):
    """S3: feed行のパスを末尾名へ縮め、長い本文（コマンド/パターン/URL/指示文）を切り詰める。
    先頭の動詞ラベル（実行中/編集中…＝describe_tool由来）は保つ＝「何をしているか」は伝わる。"""
    if not isinstance(ln, str):
        return ln
    def replace_path(match):
        token = match.group(0)
        trailing = ""
        while token and token[-1] in ",.;:!?、。)]}」』":
            trailing = token[-1] + trailing
            token = token[:-1]
        return (Path(token).name if token else "") + trailing
    # URLを先に処理する（パス正規表現が `https://…` の `//…` を先に食うと token/query が残る）。
    # scheme ごとホスト名だけへ縮める（クエリ/パスに機微が乗りうる）。
    scrubbed = re.sub(r"https?://([^/\s?#]+)\S*", r"\1", ln)
    scrubbed = _WORK_PATH_RE.sub(replace_path, scrubbed)
    return scrubbed[:60]


def _redact_entry_for_relay(e):
    """社員/プロジェクト1件から本文・パスを落とす（employees[] と projects[] で共通）。"""
    if not isinstance(e, dict):
        return
    for k in _REDACT_FIELDS:
        if k in e:
            e[k] = ""
    if not RELAY_TITLES and "title" in e:
        e["title"] = ""                    # R85-1: opt-out時はリネーム名も根元遮断
    fd = e.get("feed")
    if isinstance(fd, list):
        # 「💬 …」＝発言本文の要約行は除去。残す動作ログ行（実行中/編集中…）も、Bashコマンド・
        # Grepパターン・URL・サブエージェント指示文・絶対/チルダパスを含みうる（describe_tool は
        # Read/Edit しか basename 化しない）。S3修正: target と同じ値が feed にも入る自己矛盾を塞ぐ
        # ため、残す行にも _scrub_feed_line でパス縮約＋本文の切り詰めを掛ける（中継＝根元遮断）。
        e["feed"] = [_scrub_feed_line(ln) for ln in fd
                     if not (isinstance(ln, str) and ln.lstrip().startswith("💬"))]
    if "work" in e:
        e["work"] = _sanitize_work_for_relay(e["work"])


def _redact_office_for_relay(office_snapshot):
    """office_json() の結果から機微になりうる本文/パスを除去して返す（破壊的・呼び出し側は都度生成物を渡す）。
    残す=state/kind/verb/feedの動作ログ(実行中・編集中…)/question/disp/dept/role/age/minions/approvalMin/stuckTool、
    および title（/renameのセッション名＝ユーザー裁定2026-08-26で既定通過・OFFICE_RELAY_TITLES=0で遮断）。
    落とす=lastSaid・target(本文由来)・lastOrder・cwd・branch、および feed の「💬 発言」行。

    R82: templates（ユーザー定義定型文）は**意図的に素通し**＝スマホから使うために保存する
    再利用フレーズであり、上限8件×120字・label/textのみ（office_server.load_templates が正規化）。
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
    # S2: relay(中継使用量の生rows)を含めると「1回syncするたびにrowsが増えて指紋が変わり→
    # また送信…」の自己参照ループになり「変化時のみ送信」が無効化する（＝無料枠を食う本丸）。
    # generatedAt と同じく指紋から外す。res.staleSec も時間ドリフトなので分単位へ量子化。
    copy = {k: v for k, v in snapshot.items() if k not in ("generatedAt", "relay")}
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


def _openclaw_enabled():
    """エディション上 openclaw 連携が有効か（機能フラグの素の値）。"""
    try:
        return bool(office.edition_features(office.edition()).get("openclaw"))
    except Exception:
        return False


def _want_openclaw(state=None, now=None):
    """**この周で** openclaw集約を要求するか。エディション無効なら常に False
    （claude単体のユーザーは cross-DO を1回も踏まない）。有効でも前回取得から
    OPENCLAW_MIN_INTERVAL 秒未満なら False＝別DOを起こさない。"""
    if not _openclaw_enabled():
        return False
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
    return body, snapshot, fp, send_office, now


def _sync_apply(d, state, snapshot, fp, send_office, now, url, token):
    """sync応答の適用（HTTP/WS共通）。(delivered, appSeenAgo, appOnline) を返す。
    掟: items は peek＝配達成功分の id を state["acks"] に積み、**次の sync** で削除する
    （at-least-once。ack前にクラッシュしても再取得→既視nonce→ack で二重配達しない）。"""
    if send_office:
        state["fp"] = fp
        state["pushed_at"] = now
    # R79: ❗は「存在」ではなく「新規遷移（エッジ）」で burst を張る。
    # 前周から増えたキーがあるときだけ True＝放置された承認まちで張り付かない。
    attn_keys = _attention_keys(snapshot)
    state["attn"] = bool(attn_keys - state.get("attn_keys", frozenset()))
    state["attn_keys"] = attn_keys
    delivered, ack_ids = _process_items(d.get("items") or [])
    state["acks"] = ack_ids
    # R42.5: oc-宛の転送（outboxが空ならリクエストゼロ・失敗は本流を巻き込まない）。
    # 転送はエディションで判断する（間引きの対象は「集約の取得」だけ＝指示は遅らせない）
    oc_on = _openclaw_enabled()
    try:
        state["oc_sent"] = forward_oc_outbox(url, token) if oc_on else 0
    except Exception as e:
        state["oc_sent"] = 0
        print(f"⚠ oc-転送のみ失敗（本流は完了済み）: {e}", flush=True)
    # R80-C2: 応答に openclaw が載っている周＝要求した周だけ保存し、取得時刻を刻む
    if oc_on and d.get("openclaw") is not None:
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
                    or now - state.get("pushed_at", 0.0) >= hb):
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
        # openclaw機能が閉じたエディション(claude版)では転送も取得もしない
        if office.edition_features(office.edition()).get("openclaw"):
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
