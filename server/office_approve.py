# -*- coding: utf-8 -*-
"""R100-1b: スマホから許可（apv-）と許可鍵の登録（enr-）の Mac 側の正本（stdlib・server/ は flat）。

入口は office_server（/api/approval/remote・/api/approve/enroll・/api/approve/enroll_ticket・
/api/config/remote_approve）と、封書 kind:"ask" の _ask_sealed。ここは状態とその検査だけを持つ。
office_server に依存しない（署名の検証・端末の台帳・掲示の読み・回答の書き込みは引数で受け取る）＝
単体で全部の枝を試せる。

★掟
  - allow を書くのは approve() の最後の 1 か所だけ（14 の検査を全部通った後）。hook はそれでも自分の入力から
    再計算して一致を確かめる（最終判定は hook・docs/security-notes.md）。
  - ON（office_remote_approve.json）・鍵の台帳・状態は **ファイル**（daemon が再起動しても消えない・0600）。
    書き手は daemon だけ。読むのは daemon と hook（ON だけ）。relay_agent は読まない（grep の門）。
  - reason は固定の enum（REASONS）。自由文字列を中継へ出さない。
  - 鮮度は Mac の時計（封じたときの iat・AAD で認証済み）から SEEN_FRESH 秒。スマホの時計は信じない。
  - R114（本人裁定 2026-10-08「ON の間はタップだけ」）: 許可（apv-）の本文の mode で段を分ける（approve() の docstring の表）。
  - R118（本人裁定 2026-10-13「Claude のスマホアプリと同じく、サクッと許可」）: "tap" は段を問わず通す（危険な種類も・hook も同じ）・
    ON は期限なし（REMOTE_ALWAYS）で ON も OFF も封筒だけ（パスキー不要）・許可できる端末はペアリングが有効な端末（approver）。
    変えないもの: askId と全文の SHA-256 への束縛・決定の瞬間の危険判定（掲示の時点より上がっていない・判定できない形は Mac だけ）・
    seen・180 秒・used・rate・hook の独立の再計算。
"""
import base64
import binascii
import fcntl
import hashlib
import json
import os
import re
import secrets
import threading
import time
from contextlib import contextmanager
from pathlib import Path

import approval_policy
import p256_verify

# R118（本人裁定 2026-10-13「常に ON」）: ON は期限なし＝until に 2100-01-01 を書く（読み手は daemon も hook も今と同じ until > now）。
# OFF にするまでずっと（設定と Mac でいつでも OFF・端末の失効）。R100-1b〜R116 の 1・4・12 時間は廃止
REMOTE_ALWAYS = 4102444800
KEY_TTL = 30 * 86400               # 鍵の期限（端末の期限より長くしない）
TICKET_TTL = 600                   # 登録チケット（10 分・一度きり＝ただし Mac の確認で取り消されたときは戻す）
ENROLL_TRIES = 3                   # 取り消し（Mac の確認が押されなかった）で同じチケットをやり直せる回数
ENROLL_CONFIRM_WAIT = 45           # 登録の物理確認を待つ秒（スマホを操作している人が Mac の前へ移る時間。許可の確認は 10 秒のまま）
SEEN_FRESH = 180                   # 封じてから許可できるまでの窓（Mac の時計）
NONCE_TTL = 600                    # 封筒の鮮度窓 300 秒の倍
USED_TTL = 86400                   # hook の待機（12 時間）より長く
RESULT_TTL = 86400
RATE_GAP = 5                       # 端末ごと 5 秒に 1 件
RATE_HOUR = 60                     # 端末ごと 1 時間に 60 件
WINDOW = 300                       # 封筒の ts の窓（relay_agent の WINDOW と同じ）
AUDIT_ROTATE = 5 * 1024 * 1024

REASONS = (
    "bad-sig", "replay", "bad-format", "off", "unavailable", "not-enrolled", "scope", "not-sealed", "expired",
    "no-ask", "ask-mismatch", "input-mismatch", "bad-client-data", "bad-auth-data", "clone", "bad-sig-inner",
    "used", "rate", "dangerous", "cancelled", "bad-ticket", "no-relay", "write-failed",
    "tap-mac",                     # R114〜R116: タップで mac の段を許可しようとした（R118 で廃止＝新しい拒否には使わない・古い監査の読み用）
)
# R114: 許可（apv-）の方式。"sig"＝パスキーの署名（Face ID・本文の mode 省略も同じ）／"tap"＝ON の間のタップ（WebAuthn 無し）。
# R118: tap は段を問わず通す（スマホは tap だけを送る・sig は古い端末のために受けるだけ）
APPROVE_MODES = ("sig", "tap")
# R116-S1: スマホの決裁カードが allow の本文に添える監査用の 1 項目 ux（入口・カードの状態・見えてから押すまでの ms・5 秒の待ちの ms）。
# 許可の可否には使わない（形が違えば捨てるだけ）・本文や命令は載らない・署名の canonical（WebAuthn の challenge）にも入らない。
UX_ENTRIES = ("home", "split", "tab", "term", "sheet", "notif")
UX_STATES = ("ready", "ready-faceid")
UX_MS_MAX = 3600000
WEBAUTHN_FIELDS = ("credId", "authData", "clientDataJSON", "signature")
HEX32 = re.compile(r"^[0-9a-f]{32}$")
HEX64 = re.compile(r"^[0-9a-f]{64}$")
B64U = re.compile(r"^[A-Za-z0-9_-]{1,4000}$")
SESSION_RE = re.compile(r"^[A-Za-z0-9-]{8,64}$")
DEVICE_RE = re.compile(r"^d_[0-9a-f]{12}$")
PROJECT_RE = re.compile(r"^[0-9a-f]{12}$")
TOOL_RE = re.compile(r"^[A-Za-z0-9_]{1,64}$")
ENROLL_LABEL = "aioffice-enroll"
APPROVE_LABEL = "aioffice-approve"
REMOTE_LABEL = "aioffice-remote"   # R103-1: スマホから ON/OFF（rst-）の内側の署名対象
RST_GAP = 10                       # R103-1: ON/OFF は端末ごと 10 秒に 1 回
RST_HOUR = 20                      # R103-1: ON/OFF は端末ごと 1 時間に 20 回（個々の許可の予算とは別の枠）

_LOCK = threading.RLock()


def _home():
    return Path(os.environ.get("OFFICE_HOME") or str(Path.home()))


def remote_file():
    return _home() / ".claude" / "office_remote_approve.json"


def keys_file():
    return _home() / ".claude" / "office_approve_keys.json"


def state_file():
    return _home() / ".claude" / "office_approve_state.json"


def audit_file():
    return _home() / ".claude" / "office_approvals_audit.jsonl"


def hook_audit_file():
    return _home() / ".claude" / "office_approvals_hook.jsonl"


# ------------------------------------------------------------------ ファイル

def _read_json(path, default):
    try:
        v = json.loads(path.read_text(encoding="utf-8"))
        return v if isinstance(v, type(default)) else default
    except (OSError, ValueError):
        return default


def _write_json(path, obj):
    """最初から 0600 で書いて原子置換（0644 の窓を作らない）。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name("." + path.name + ".tmp")
    fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, separators=(",", ":"))
    os.replace(tmp, path)


@contextmanager
def _locked():
    """スレッド（_LOCK）とプロセス（隣の .lock の flock）の両方で直列化する。"""
    with _LOCK:
        p = state_file()
        p.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(str(p.with_name(p.name + ".lock")), os.O_WRONLY | os.O_CREAT, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            yield
        finally:
            os.close(fd)


def audit(record):
    """daemon 側の監査（sealed / approve / enroll の各 1 行・askId で hook の監査と突合できる）。"""
    path = audit_file()
    row = dict(record, v=1, ts=int(record.get("ts") or time.time()))
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        lk = os.open(str(path.with_name(path.name + ".lock")), os.O_WRONLY | os.O_CREAT, 0o600)
        try:
            fcntl.flock(lk, fcntl.LOCK_EX)
            try:
                if path.stat().st_size > AUDIT_ROTATE:
                    os.replace(path, path.with_name(path.name + ".1"))
            except FileNotFoundError:
                pass
            fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
            with os.fdopen(fd, "a", encoding="utf-8") as f:
                f.write(json.dumps(row, ensure_ascii=False) + "\n")
        finally:
            os.close(lk)
    except OSError:
        pass


def _load_state(now):
    st = _read_json(state_file(), {})
    for k, kind in (("nonces", dict), ("seen", list), ("used", dict), ("results", dict), ("rate", dict),
                    ("tickets", dict), ("latest", dict), ("enrUsed", list)):
        if not isinstance(st.get(k), kind):
            st[k] = kind()
    st["nonces"] = {k: v for k, v in st["nonces"].items() if _num(v) > now}
    st["seen"] = [s for s in st["seen"] if isinstance(s, dict) and now - _num(s.get("iat")) <= 600][-200:]
    st["used"] = {k: v for k, v in st["used"].items() if now - _num(v) <= USED_TTL}
    st["results"] = {k: v for k, v in st["results"].items()
                     if isinstance(v, dict) and now - _num(v.get("ts")) <= RESULT_TTL}
    st["rate"] = {d: [t for t in v if now - _num(t) <= 3600][-RATE_HOUR:]
                  for d, v in st["rate"].items() if isinstance(v, list)}
    st["tickets"] = {k: v for k, v in st["tickets"].items() if isinstance(v, dict) and _num(v.get("exp")) > now}
    # R103-1: 使った登録のチャレンジ（スマホが選んだ 32 桁）は期限なしで覚える（古い登録データを外の封筒だけ替えて出し直しても通さない）
    st["enrUsed"] = [t for t in st["enrUsed"] if isinstance(t, str) and HEX32.match(t)][-2000:]
    # R102-7: 端末ごとに「いちばん新しく発行したリンク」（取り消しで戻してよいのはこれだけ）
    st["latest"] = {d: v for d, v in st["latest"].items() if isinstance(v, dict) and _num(v.get("exp")) > now}
    # 更新前に発行されたリンク（latest の記録が無い）は、有効なリンクから補う（new_ticket は端末ごとに 1 本だけ残す＝一意）
    for k, v in st["tickets"].items():
        d = v.get("device")
        if isinstance(d, str) and d not in st["latest"]:
            st["latest"][d] = {"t": k, "exp": _num(v.get("exp"))}
    return st


def _num(v):
    return v if isinstance(v, (int, float)) and not isinstance(v, bool) else 0


# ------------------------------------------------------------------ ON（期限つき）

def remote_until(now=None):
    """ON の期限（epoch 秒）。OFF・読めない・bool・非数値は 0。hook も同じ読み方をする。"""
    now = time.time() if now is None else now
    d = _read_json(remote_file(), {})
    u = d.get("until")
    if d.get("v") != 1 or isinstance(u, bool) or not isinstance(u, int):
        return 0
    return u if u > now else 0


def set_remote(on, hours=None, now=None, _held=False):
    """(ok, msg, until)。物理確認は呼び出し側（office_server）が ON のときだけ先にやる。
    _held=True＝呼び出し側がすでに _locked() を持っている（remote_set）＝取り直さない（取り直すと自分を待って止まる）。
    R118: ON は期限なし（until＝REMOTE_ALWAYS）。hours は読まない（古い呼び出しの互換のために受けるだけ）。"""
    now = int(time.time() if now is None else now)
    if not isinstance(on, bool):
        return False, "on must be true/false", 0
    until = REMOTE_ALWAYS if on else 0
    if _held:
        _write_json(remote_file(), {"v": 1, "until": until, "set": now})
    else:
        with _locked():
            _write_json(remote_file(), {"v": 1, "until": until, "set": now})
    return True, "on" if on else "off", until


def remote_on_consent(lang="ja"):
    """R116-S0→R118: Mac から「スマホから許可」を ON にするときの同意の文（office_server が office_actions._confirm_local へ渡す）。
    境界は不変条件 7（R118）＝読み切れる操作は段を問わずタップ 1 回（危険な種類も・カードに朱の 1 行）／Mac だけ（判定できない形）・
    期限なし。括弧の中の例は test_approval_policy が代表の形を classify にかけ、スマホから許可できる形か（phone_approvable）を確かめる
    （例を足したらその表にも足す）。_confirm_local は 1 行を 120 字で切り 4 行までしか出さない＝各行 120 字以下・4 行以下（test_lang が
    実 HTTP で番人）。"""
    if lang == "en":
        lines = ("📱 Allow approvals from the phone? No time limit; turn it off anytime in Settings or on the Mac.",
                 "While on, the paired phone approves with one tap (git push, deleting, sending data out, changing settings).",
                 "Risky kinds are marked in red on the card, and the Mac is notified every time.",
                 "Commands that can't be fully read stay Mac-only.")
    else:
        lines = ("📱 スマホから許可を ON にしますか？ 期限はありません（設定と Mac でいつでも OFF にできます）。",
                 "ON の間、ペアリングしたスマホからタップ 1 回で許可できます（git push・削除・外部への送信・設定の変更）。",
                 "危険な種類はカードに朱の 1 行で示し、許可するたびに Mac に通知が出ます。",
                 "書き方を読み切れない操作は Mac だけです。")
    return "\n".join(lines)


# ------------------------------------------------------------------ 鍵の台帳

def b64u(raw):
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def unb64u(text):
    if not isinstance(text, str) or not B64U.match(text):
        raise ValueError("b64u")
    try:
        return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))
    except (binascii.Error, ValueError):
        raise ValueError("b64u")


def key_fp(cred_id, pub_raw):
    """Mac と端末の両方に出して目で照合する 6 桁（秘密ではない）。"""
    return hashlib.sha256(bytes(cred_id) + bytes(pub_raw)).hexdigest()[:6]


def load_keys():
    d = _read_json(keys_file(), {})
    if not isinstance(d.get("keys"), dict):
        d = {"version": 1, "keys": {}}
    return d


def _device_ok(devices, device, now):
    rec = (devices or {}).get("devices", {}).get(device)
    if not isinstance(rec, dict) or rec.get("revoked"):
        return None
    exp = rec.get("expires")
    if isinstance(exp, (int, float)) and not isinstance(exp, bool) and exp < now:
        return None
    return rec


def valid_key(device, devices, now=None):
    """その端末の有効な許可鍵（失効・期限切れ・端末自体の失効は None）。"""
    now = time.time() if now is None else now
    if not _device_ok(devices, device, now):
        return None
    k = load_keys()["keys"].get(device)
    if not isinstance(k, dict) or k.get("revoked") or _num(k.get("expires")) <= now:
        return None
    if not isinstance(k.get("pub"), str) or not isinstance(k.get("credId"), str):
        return None
    return k


def approver(device, devices, now=None):
    """R118: 許可できる端末＝ペアリングが有効（失効・期限切れでない）な端末。登録済みの有効な許可鍵があればその範囲（projects）、
    無ければ全プロジェクト。端末自体が無効なら None。"""
    now = time.time() if now is None else now
    if not _device_ok(devices, device, now):
        return None
    return valid_key(device, devices, now) or {"projects": "*"}


def extend_key(device, until, now=None):
    """R116-S1b: 端末のつながりを延長したら、有効な許可鍵の期限も新しい端末の期限へ追わせる（範囲つきの鍵が先に切れると
    approver が全プロジェクトへ広げる＝延長で範囲が黙って広がらないように）。失効・期限切れの鍵は生き返らせない。
    呼ぶのは延長の求め（鍵がまだ有効なうち）と昇格の 2 回（別モデルレビュー high 1）。縮めない（until が今の期限より前なら何もしない）。"""
    now = time.time() if now is None else now
    with _locked():
        d = load_keys()
        k = d["keys"].get(device)
        if (not isinstance(k, dict) or k.get("revoked") or _num(k.get("expires")) <= now
                or _num(k.get("expires")) >= int(until)):
            return False
        k["expires"] = int(until)
        _write_json(keys_file(), d)
    return True


def revoke_key(device):
    with _locked():
        d = load_keys()
        k = d["keys"].get(device)
        if not isinstance(k, dict):
            return False
        k["revoked"] = True
        k["revokedAt"] = int(time.time())
        # R103-1: 失効した資格情報は二度と登録させない（古い登録データの出し直しで復活させない）
        rc = [x for x in (d.get("revokedCreds") or []) if isinstance(x, str)]
        if isinstance(k.get("credId"), str) and k["credId"] not in rc:
            rc.append(k["credId"])
        d["revokedCreds"] = rc[-500:]
        _write_json(keys_file(), d)
    return True


def approve_public(devices, now=None):
    """office_json.actions.approve（中継へ出る）: ON か・有効な鍵の fp だけ（fp は秘密でない）。"""
    now = time.time() if now is None else now
    fps = []
    for dev, k in load_keys()["keys"].items():
        if valid_key(dev, devices, now) and isinstance(k.get("fp"), str):
            fps.append(k["fp"])
    # R102-9: 登録の受付が開いている端末（Mac で「許可鍵を登録」を押してから 10 分）。スマホはこれを見て「登録」だけで進める
    #（リンクを貼らない＝iPhone のカメラで QR を読むと Safari で開き、ホーム画面のアプリには届かなかった）。端末 ID は秘密でない
    enr = []
    try:
        st = _read_json(state_file(), {})
        tks = st.get("tickets") if isinstance(st.get("tickets"), dict) else {}
        enr = sorted({v.get("device") for v in tks.values() if isinstance(v, dict) and isinstance(v.get("device"), str)
                      and DEVICE_RE.match(v["device"]) and _num(v.get("exp")) > now})[:8]
    except Exception:
        enr = []
    # R103-1: ON の終了時刻（epoch 秒・整数。スマホの設定に「〜時まで」を出す）
    return {"on": 1 if remote_until(now) else 0, "until": int(remote_until(now)), "fps": sorted(fps)[:16], "enr": enr}


def keys_local(devices, now=None):
    """デスクトップの 📱 スマホ連携（loopback だけ）: 端末ごとの登録状態。"""
    now = time.time() if now is None else now
    out = {}
    for dev, k in load_keys()["keys"].items():
        if not isinstance(k, dict):
            continue
        out[dev] = {"fp": k.get("fp", ""), "created": _num(k.get("created")), "expires": _num(k.get("expires")),
                    "projects": k.get("projects", "*"), "lastUsed": _num(k.get("last_used")),
                    "active": bool(valid_key(dev, devices, now))}
    return out


def caps_approve(devices, available, now=None):
    """caps.approve: 封じられる ∧ ON ∧ 許可できる端末（ペアリングが有効・R118）が 1 台以上。"""
    now = time.time() if now is None else now
    if not available or not remote_until(now):
        return 0
    devs = (devices or {}).get("devices") if isinstance(devices, dict) else None
    return 1 if any(_device_ok(devices, dev, now) for dev in (devs or {})) else 0


# ------------------------------------------------------------------ 登録（enr-）

def new_ticket(device, projects, devices, now=None):
    """(ok, msg, ticket)。loopback＋Origin の門の後ろから呼ぶ（遠隔から作る経路は無い）。"""
    now = int(time.time() if now is None else now)
    if not isinstance(device, str) or not DEVICE_RE.match(device) or not _device_ok(devices, device, now):
        return False, "unknown device", ""
    if projects != "*":
        if (not isinstance(projects, list) or not projects or len(projects) > 50
                or not all(isinstance(p, str) and PROJECT_RE.match(p) for p in projects)):
            return False, "projects must be \"*\" or a list of project ids", ""
    ticket = secrets.token_hex(16)
    with _locked():
        st = _load_state(now)
        st["tickets"] = {k: v for k, v in st["tickets"].items() if v.get("device") != device}
        st["tickets"][ticket] = {"device": device, "projects": projects, "exp": now + TICKET_TTL}
        st["latest"][device] = {"t": ticket, "exp": now + TICKET_TTL}
        _write_json(state_file(), st)
    return True, "ok", ticket


# b4-mac: 登録の段階を 📱 の画面（loopback だけ）に出すための daemon のメモリ。登録（enroll）も状態の読み（enroll_local）も
# daemon の中で動く＝プロセスをまたがない。office_json にも中継にも載せない（不変条件 9 の対象外）。再起動で消えてよい飾り。
_CONFIRMING = {}       # device -> いま Mac の確認ダイアログを出している（出した時刻）
_ENROLL_LAST = {}      # device -> 直近の登録の結果 {"state", "reason", "at"}


def _note_enroll(device, state, reason, now):
    if isinstance(device, str) and DEVICE_RE.match(device):
        _ENROLL_LAST[device] = {"state": state, "reason": reason or "", "at": int(now)}


def enroll_local(now=None):
    """デスクトップの 📱（loopback だけ）: 端末ごとの登録の受付（期限・取り消しの回数）・Mac の確認を待っているか・直近の結果。"""
    now = time.time() if now is None else now
    out = {}
    st = _read_json(state_file(), {})
    tickets = st.get("tickets") if isinstance(st.get("tickets"), dict) else {}
    for v in tickets.values():
        if isinstance(v, dict) and isinstance(v.get("device"), str) and _num(v.get("exp")) > now:
            out.setdefault(v["device"], {})["open"] = {"exp": int(_num(v.get("exp"))), "tries": int(_num(v.get("tries")))}
    for d, since in list(_CONFIRMING.items()):
        out.setdefault(d, {})["confirming"] = int(since)
    for d, r in list(_ENROLL_LAST.items()):
        out.setdefault(d, {})["last"] = dict(r)
    return out


def enroll_challenge(device, ticket):
    return hashlib.sha256(("%s/v1\n%s\n%s" % (ENROLL_LABEL, device, ticket)).encode("utf-8")).digest()


def _envelope(env, devices, verify, now, st, want_prefix):
    """封筒の再検証（S）＋ daemon 自前の nonce 台帳。(device, payload, reqish) か例外 _No。"""
    ok, _reason, sess, text = verify(env, devices, now, WINDOW)
    if not ok:
        raise _No("bad-sig")
    key = "%s:%s" % (env.get("device_id"), env.get("nonce"))
    if key in st["nonces"]:
        raise _No("replay")
    st["nonces"][key] = int(env.get("ts") or now) + NONCE_TTL
    try:
        payload = json.loads(text)
    except ValueError:
        raise _No("bad-format")
    if not isinstance(payload, dict) or payload.get("aioffice") != 1 or not sess.startswith(want_prefix):
        raise _No("bad-format")
    return env["device_id"], payload, sess[len(want_prefix):]


class _Done(Exception):
    """remote_set: OFF は効いたが結果の控えが書けなかった＝受理として後段（監査・通知）へ進む。"""


class _No(Exception):
    def __init__(self, reason):
        Exception.__init__(self, reason)
        self.reason = reason if reason in REASONS else "bad-format"


def _client_data(raw, typ, challenge, origin):
    try:
        cd = json.loads(raw.decode("utf-8"))
    except (UnicodeError, ValueError):
        raise _No("bad-client-data")
    if (not isinstance(cd, dict) or cd.get("type") != typ or cd.get("origin") != origin
            or cd.get("challenge") != b64u(challenge) or cd.get("crossOrigin") is True):
        raise _No("bad-client-data")


def _auth_data(raw, rp_id):
    """rpIdHash・UP と UV・signCount。"""
    if len(raw) < 37 or raw[:32] != hashlib.sha256(rp_id.encode("utf-8")).digest():
        raise _No("bad-auth-data")
    flags = raw[32]
    if not (flags & 0x01) or not (flags & 0x04):
        raise _No("bad-auth-data")                   # 本人の在席（UP）と生体・PIN（UV）が両方要る
    return flags, int.from_bytes(raw[33:37], "big")


def enroll(env, *, devices, verify, rp, confirm, notify=None, now=None):
    """(state, reason, fp)。state は granted / refused。封筒が読めない等は ("refused", reason, "")。"""
    now = int(time.time() if now is None else now)
    rp_id, origin = rp if rp else ("", "")
    with _locked():
        st = _load_state(now)
        try:
            try:
                device, p, ticket = _envelope(env, devices, verify, now, st, "enr-")
            finally:
                _write_json(state_file(), st)       # nonce は結果に関わらず焼く
            if not rp_id:
                raise _No("no-relay")
            if (p.get("kind") != "enroll" or p.get("ticket") != ticket or not HEX32.match(ticket)
                    or not all(isinstance(p.get(k), str) and B64U.match(p[k])
                               for k in ("credId", "spki", "clientDataJSON", "authData"))):
                raise _No("bad-format")
            if ticket in st["enrUsed"]:
                raise _No("bad-ticket")              # R103-1: 使った登録のチャレンジ（別モデルレビュー: 失効した鍵が復活した）
            tk = st["tickets"].pop(ticket, None)
            key = ticket
            if tk is None:
                # R102-9: リンクを貼らない登録＝スマホが選んだ値（32 桁）で来る。この端末の受付（Mac で「許可鍵を登録」を押した
                # 10 分）が開いていれば、それを使う（端末の署名・チャレンジ・Mac の前での確認は今までどおり全部要る）
                for k, v in list(st["tickets"].items()):
                    if isinstance(v, dict) and v.get("device") == device:
                        key, tk = k, st["tickets"].pop(k)
                        break
            if tk is None:
                # R103-1（本人裁定 2026-10-02「登録も ON もスマホだけで完結」「登録した瞬間から有効」）: Mac の受付（ボタン）が
                # 無くても、ペアリング済み・失効していない端末が自分で選んだ 32 桁（WebAuthn のチャレンジに結ぶ）で登録できる。
                # 再送は封筒の nonce と鮮度（WINDOW）で止まる。範囲は全プロジェクト（Mac の受付で範囲を絞ったときだけ絞る）
                tk = {"device": device, "projects": "*", "exp": now + TICKET_TTL}
            _write_json(state_file(), st)           # チケットは一度きり（失敗しても戻さない）
            if not isinstance(tk, dict) or tk.get("device") != device or _num(tk.get("exp")) <= now:
                raise _No("bad-ticket")
            dev = _device_ok(devices, device, now)
            if not dev:
                raise _No("bad-sig")
            cred, spki, cdj, ad = (unb64u(p[k]) for k in ("credId", "spki", "clientDataJSON", "authData"))
            if not 16 <= len(cred) <= 255:
                raise _No("bad-format")
            _client_data(cdj, "webauthn.create", enroll_challenge(device, ticket), origin)
            flags, count = _auth_data(ad, rp_id)
            # 属性つき資格情報（AT フラグ）の credId が送られた credId と同じ（CBOR の手前までだけ読む）
            if not (flags & 0x40) or len(ad) < 55:
                raise _No("bad-auth-data")
            n = int.from_bytes(ad[53:55], "big")
            if ad[55:55 + n] != cred:
                raise _No("bad-auth-data")
            try:
                pub = p256_verify.parse_spki(spki)
            except ValueError:
                raise _No("bad-format")
            kd = load_keys()
            if b64u(cred) in (kd.get("revokedCreds") or []):
                raise _No("bad-ticket")              # 失効した資格情報は登録し直せない（新しいパスキーを作る）
            cur = kd["keys"].get(device)
            if isinstance(cur, dict) and cur.get("credId") == b64u(cred) and not cur.get("revoked"):
                raise _No("bad-ticket")              # 今の鍵と同じ資格情報＝出し直し（カウンタと範囲を戻させない）
            st["enrUsed"].append(ticket)
            _write_json(state_file(), st)
        except (_No, ValueError) as e:
            reason = e.reason if isinstance(e, _No) else "bad-format"
            audit({"kind": "enroll", "state": "refused", "reason": reason,
                   "device": env.get("device_id") if isinstance(env, dict) else ""})
            if reason not in ("bad-sig", "replay"):          # 署名の通らない封筒の端末名は信じない
                _note_enroll(env.get("device_id") if isinstance(env, dict) else "", "refused", reason, now)
            return "refused", reason, ""
        pub_hex = p256_verify.pub_hex(pub)
        fp = key_fp(cred, bytes.fromhex(pub_hex))
    # R103-1: Mac の物理確認（照合コードのダイアログ）は無い（本人裁定）。登録した瞬間から有効・Mac には通知と監査だけ。
    # confirm 引数は互換のため受け取るが使わない。Mac は 📱 の「許可鍵を失効」でいつでも即時に無効化できる
    label = str(dev.get("label") or "スマホ")[:40]
    with _locked():
        d = load_keys()
        dev_exp = _num(dev.get("expires")) or (now + KEY_TTL)
        d["keys"][device] = {"credId": b64u(cred), "pub": pub_hex, "created": now,
                             "expires": int(min(dev_exp, now + KEY_TTL)), "revoked": False,
                             "projects": tk.get("projects", "*"), "signCount": count, "fp": fp, "last_used": 0}
        _write_json(keys_file(), d)
    audit({"kind": "enroll", "state": "granted", "device": device, "fp": fp})
    _note_enroll(device, "granted", "", time.time())
    if notify:
        notify("スマホの許可鍵を登録しました（スマホだけで）", "%s（照合コード %s）。心当たりが無ければ 📱 で失効" % (label, fp))
    return "granted", "", fp


# ------------------------------------------------------------------ スマホから ON/OFF（rst-・R103-1）

def remote_canonical(device, req_id, on, hours, ts):
    """ON/OFF の内側の署名対象。ts＝外側の封筒の時刻（HMAC で認証済み・WINDOW の鮮度は Mac の時計で見る）。
    署名をその 1 通の封筒に結ぶ＝同じ Face ID の署名を別の封筒・別の時刻で使い回せない。"""
    return "\n".join([REMOTE_LABEL, "v1", device, req_id, "1" if on else "0", str(int(hours or 0)), str(int(ts))])


def remote_challenge(*fields):
    return hashlib.sha256(remote_canonical(*fields).encode("utf-8")).digest()


def remote_set(env, *, devices, verify, rp, notify=None, now=None):
    """(state, reason, info)。R103-1（本人裁定 2026-10-02）: スマホから「スマホから許可」を ON/OFF。
    R118（本人裁定 2026-10-13）: ON も OFF も封筒（ペアリング鍵の HMAC）だけ・ON は期限なし（hours は読まない・古い端末が送っても無視）。
    パスキーの 4 項目が付いていても見ない。結果は reqId で冪等。端末ごとの回数制限は個々の許可とは別の枠。Mac には毎回通知・監査。"""
    now = int(time.time() if now is None else now)
    rp_id, origin = rp if rp else ("", "")
    info = {"device": env.get("device_id") if isinstance(env, dict) else "", "reqId": "", "on": None,
            "hours": 0, "until": 0}
    with _locked():
        st = _load_state(now)
        try:
            try:
                device, p, req_id = _envelope(env, devices, verify, now, st, "rst-")
            finally:
                _write_json(state_file(), st)
            info["reqId"] = req_id
            on, hours = p.get("on"), p.get("hours")
            if p.get("kind") != "remote_set" or p.get("reqId") != req_id or not HEX32.match(req_id) or not isinstance(on, bool):
                raise _No("bad-format")
            info.update(on=on, hours=0)
            prev = st["results"].get(req_id)
            if isinstance(prev, dict):
                return prev.get("state", "refused"), prev.get("reason", ""), dict(info, until=int(_num(prev.get("until"))))
            if not _device_ok(devices, device, now):
                raise _No("bad-sig")
            times = st["rate"].get("rst:" + device, [])
            if (times and now - max(times) < RST_GAP) or len(times) >= RST_HOUR:
                raise _No("rate")
            try:
                st["rate"]["rst:" + device] = times + [now]
                _write_json(state_file(), st)
                prev = _read_json(remote_file(), {})
                ok, _msg, until = set_remote(on, None, now, _held=True)
                if not ok:
                    raise _No("bad-format")
                info["until"] = int(until)
                st["results"][req_id] = {"state": "granted", "reason": "", "ts": now, "until": int(until)}
                try:
                    _write_json(state_file(), st)
                except OSError:
                    if not on:
                        raise _Done()            # OFF はもう効いている（安全側）＝受理として監査・通知へ（結果の控えだけ残らない）
                    # 別モデルレビュー: 拒否（write-failed）で終わるのに新しい ON だけ残った＝ON の前の状態へ戻す（戻せなければ OFF）
                    try:
                        _write_json(remote_file(), prev if prev else {"v": 1, "until": 0, "set": now})
                    except OSError:
                        try:
                            _write_json(remote_file(), {"v": 1, "until": 0, "set": now})
                        except OSError:
                            pass
                    raise
            except OSError:
                raise _No("write-failed")
        except _Done:
            pass
        except (_No, ValueError) as e:
            reason = e.reason if isinstance(e, _No) else "bad-format"
            if info["reqId"] and HEX32.match(info["reqId"]) and reason not in ("replay", "bad-sig"):
                st["results"][info["reqId"]] = {"state": "refused", "reason": reason, "ts": now}
                _write_json(state_file(), st)
            audit({"kind": "remote_set", "state": "refused", "reason": reason, **info})
            return "refused", reason, info
    audit({"kind": "remote_set", "state": "granted", **info})
    if notify:
        label = str((_device_ok(devices, info["device"], now) or {}).get("label") or "スマホ")[:30]
        notify("スマホから許可: %s" % ("ON（期限なし）" if on else "OFF"),
               "%s が%s" % (label, "スマホから ON にしました。心当たりが無ければ 📱 で OFF・失効" if on else "OFF にしました"))
    return "granted", "", info


# ------------------------------------------------------------------ 封書 kind:"ask"（見せる前の検査）

def seal_gate(device, session, ask, devices, project_of, now=None):
    """封じてよいか（reason の enum か None）。通ったら seen に積む（呼び出し側が seal の成功後に mark_seen）。"""
    now = int(time.time() if now is None else now)
    if not remote_until(now):
        return "off"
    k = approver(device, devices, now)              # R118: ペアリングが有効な端末（許可鍵は要らない）
    if not k:
        return "not-enrolled"
    if not ask:
        return "no-ask"
    if not _in_scope(k, project_of(session, ask)):
        return "scope"
    return None


def mark_seen(device, ask_id, iat):
    with _locked():
        st = _load_state(int(iat))
        st["seen"].append({"device": device, "askId": ask_id, "iat": int(iat)})
        _write_json(state_file(), st)


def _in_scope(key, project_id):
    pr = key.get("projects", "*")
    return pr == "*" or (isinstance(pr, list) and project_id in pr)


def canonical(device, session, ask_id, tool, input_sha, iat, decision, req_id):
    """内側の署名対象（全部の項目は先に正規表現で検査済み＝改行を含まない）。
    iat＝Mac がこの端末へ全文を封じた時刻（封書の AAD で認証済み）。署名をその 1 回の封書に結ぶ＝端末秘密を持つ者が
    同じ掲示を封じ直しても、古い（届かなかった）署名を新しい鮮度の窓で使えない（別モデルレビュー）。"""
    return "\n".join([APPROVE_LABEL, "v1", device, session, ask_id, tool, input_sha, str(int(iat)), decision, req_id])


def approve_challenge(*fields):
    return hashlib.sha256(canonical(*fields).encode("utf-8")).digest()


# ------------------------------------------------------------------ 許可（apv-）

def _ux(v):
    """R116-S1: 本文の ux を監査に載せてよい形だけにする（それ以外は None＝捨てる・拒否の理由にしない）。"""
    if not isinstance(v, dict) or set(v) != {"e", "s", "ms", "q"}:
        return None
    if v["e"] not in UX_ENTRIES or v["s"] not in UX_STATES:
        return None
    if not all(isinstance(v[k], int) and not isinstance(v[k], bool) and 0 <= v[k] <= UX_MS_MAX for k in ("ms", "q")):
        return None
    return {"e": v["e"], "s": v["s"], "ms": v["ms"], "q": v["q"]}


def approve(env, *, devices, verify, rp, read_ask, write_reply, project_of, home, available,
            notify=None, now=None):
    """(state, reason, info)。state は granted / refused。14 の検査を順に見て、1 つでも外れたら許可しない。
    read_ask(session, askId) → 掲示の中身（inputCanon を含む生の dict）か None（心拍が止まった・無い）。
    write_reply(session, askId, extra) → hook への回答（src="remote"）を書く。

    R114（本人裁定 2026-10-08「ON の間はタップだけ」）: 本文の mode で段を分ける（省略＝"sig"＝R100-1b からの挙動そのまま）。
    R118（本人裁定 2026-10-13「Claude のスマホアプリと同じく、サクッと許可」）: tap は段を問わず通す（危険な種類も）・6 は
    ペアリングが有効な端末（approver・許可鍵は要らない）。スマホは tap だけを送る（sig は古い端末のために受けるだけ）。

      | 検査                                                        | sig（Face ID）       | tap（ON の間のタップ）      |
      |-------------------------------------------------------------|----------------------|-----------------------------|
      | 1・2 封筒の HMAC（daemon が確かめ直す）・nonce の台帳       | 要る                 | 要る                        |
      | 3 形式（kind・reqId・session・askId・tool・inputSha・iat）  | 要る                 | 要る                        |
      | 3 形式: WebAuthn の 4 項目（credId 等）                     | 必須                 | 求めない（在っても見ない）  |
      | 4 reqId の冪等（results）                                   | 要る                 | 要る                        |
      | 5 ON（remote_until・R118 は期限なし）・dialog_seal が使える | 要る                 | 要る                        |
      | 6 許可できる端末・掲示・範囲                                | 有効な許可鍵         | ペアリングが有効（approver）|
      | 7 seen の 3 点一致（端末・askId・iat）・180 秒の鮮度        | 要る                 | 要る                        |
      | 11 掲示と ask／input の一致                                 | 要る                 | 要る                        |
      | 8〜10 credId の一致・clientData・authData・clone・署名      | 要る                 | 省く                        |
      | 12 used・13 rate・14 危険判定（段が上がっていない）         | 要る                 | 要る                        |

    info["mode"] は監査・results・hook への回答（tap のときだけ extra に "mode":"tap"）へ運ぶ。
    R116-S1: 本文の ux（入口・状態・所要 ms）は形が正しいときだけ info["ux"]＝daemon の監査の行へ（判定には使わない）。"""
    now = int(time.time() if now is None else now)
    rp_id, origin = rp if rp else ("", "")
    info = {"device": env.get("device_id") if isinstance(env, dict) else "", "reqId": "", "askId": "",
            "session": "", "tool": "", "mode": ""}
    with _locked():
        st = _load_state(now)
        try:
            try:
                device, p, req_id = _envelope(env, devices, verify, now, st, "apv-")      # 1・2
            finally:
                _write_json(state_file(), st)
            info["reqId"] = req_id
            mode = p.get("mode", "sig")                  # R114: 省略は sig（今までのスマホ）
            info["mode"] = mode if mode in APPROVE_MODES else "?"
            ux = _ux(p.get("ux"))
            if ux:
                info["ux"] = ux
            tap = mode == "tap"
            session, ask_id, tool, sha = p.get("session"), p.get("askId"), p.get("tool"), p.get("inputSha")
            iat = p.get("iat")
            if (p.get("kind") != "approve" or p.get("reqId") != req_id or not HEX32.match(req_id)          # 3
                    or mode not in APPROVE_MODES
                    or not isinstance(session, str) or not SESSION_RE.match(session)
                    or not isinstance(ask_id, str) or not HEX32.match(ask_id)
                    or not isinstance(tool, str) or not TOOL_RE.match(tool)
                    or not isinstance(sha, str) or not HEX64.match(sha) or p.get("decision") != "allow"
                    or not isinstance(iat, int) or isinstance(iat, bool) or not 0 < iat < 4102444800
                    or (not tap and not all(isinstance(p.get(k), str) and B64U.match(p[k])
                                            for k in WEBAUTHN_FIELDS))):
                raise _No("bad-format")
            info.update(session=session, askId=ask_id, tool=tool)
            prev = st["results"].get(req_id)                                                       # 4
            if isinstance(prev, dict):
                # 同じ reqId の再送は最初の結果と同じ（方式も最初のもの・R114 より前の結果＝mode の無い行は sig だけだった）。
                # 保存した mode は上書きしない（未知の方式で拒否した "?" を sig に化けさせない・R114-S2）
                pm = prev.get("mode", "sig")
                info["mode"] = pm if isinstance(pm, str) else "?"
                return prev.get("state", "refused"), prev.get("reason", ""), info
            if not remote_until(now):                                                              # 5
                raise _No("off")
            if not available:
                raise _No("unavailable")
            key = approver(device, devices, now) if tap else valid_key(device, devices, now)      # 6（R118: tap はペアリング）
            if not key:
                raise _No("not-enrolled")
            ask = read_ask(session, ask_id)
            if not ask:
                raise _No("no-ask")
            if not _in_scope(key, project_of(session, ask)):
                raise _No("scope")
            seen = [s for s in st["seen"]                                                          # 7
                    if s.get("device") == device and s.get("askId") == ask_id and s.get("iat") == iat]
            if not seen:
                raise _No("not-sealed")                  # その封書（端末・1 件・封じた時刻）を Mac が出していない
            if not 0 <= now - iat <= SEEN_FRESH:
                raise _No("expired")
            canon = ask.get("inputCanon")                                                          # 11（先に掲示）
            if ask.get("askId") != ask_id or ask.get("tool") != tool or not isinstance(canon, str):
                raise _No("ask-mismatch")
            if approval_policy.input_sha(canon) != sha or ask.get("inputSha") != sha:
                raise _No("input-mismatch")
            count = None
            if not tap:                                  # R114: tap はパスキーの署名を求めない（8〜10 だけ省く）
                cred = unb64u(p["credId"])
                if b64u(cred) != key.get("credId"):
                    raise _No("not-enrolled")
                chal = approve_challenge(device, session, ask_id, tool, sha, iat, "allow", req_id)
                _client_data(unb64u(p["clientDataJSON"]), "webauthn.get", chal, origin)         # 8
                _flags, count = _auth_data(unb64u(p["authData"]), rp_id)                          # 9
                stored = int(_num(key.get("signCount")))
                if (count or stored) and count <= stored:
                    raise _No("clone")
                pub = p256_verify.pub_from_hex(key["pub"])                                         # 10
                if not p256_verify.verify_webauthn(pub, unb64u(p["authData"]), unb64u(p["clientDataJSON"]),
                                                   unb64u(p["signature"])):
                    raise _No("bad-sig-inner")
            if ask_id in st["used"]:                                                               # 12
                raise _No("used")
            times = st["rate"].get(device, [])                                                     # 13
            if (times and now - max(times) < RATE_GAP) or len(times) >= RATE_HOUR:
                raise _No("rate")
            try:
                inp = json.loads(canon).get("input")
            except (ValueError, AttributeError):
                raise _No("input-mismatch")
            level = approval_policy.classify(tool, inp, ask.get("cwd") or "", home)                # 14
            # R103-1（本人裁定 2026-10-02）: 「Mac だけ」の段（mac）も、全文が読める限りスマホから許可できる（Face ID の署名。
            # R114 で全文を読む義務は廃止＝要点を見て許可）。全文が読めない形は掲示に inputCanon が無い＝11 で止まる
            lv = level.get("level")
            rank = approval_policy.LEVEL_RANK
            if (not approval_policy.phone_approvable(lv, level.get("reasons"))
                    or lv not in rank or rank[lv] > rank.get(ask.get("level"), 0)):
                raise _No("dangerous")       # 判定できない形・掲示の時点より危険な段へ上がった（すり替え）は許可しない
            # R118（本人裁定 2026-10-13）: タップは段を問わず通す（R114〜R116 の "tap-mac" の拒否は廃止・hook も同じ）
            info["level"] = level.get("level")
            extra = {"inputSha": sha, "device": device, "reqId": req_id}
            if tap:
                extra["mode"] = "tap"        # hook の監査の印（R118 から段では分けない）
            try:
                write_reply(session, ask_id, extra)
            except (OSError, ValueError):
                raise _No("write-failed")
            st["used"][ask_id] = now
            st["rate"][device] = times + [now]
            st["results"][req_id] = {"state": "granted", "reason": "", "ts": now, "mode": info["mode"]}
            _write_json(state_file(), st)
            d = load_keys()
            k2 = d["keys"].get(device)
            if isinstance(k2, dict):
                if count is not None:        # tap は署名カウンタを動かさない（パスキーを使っていない）
                    k2["signCount"] = count
                k2["last_used"] = now
                _write_json(keys_file(), d)
        except (_No, ValueError) as e:
            reason = e.reason if isinstance(e, _No) else "bad-format"
            if info["reqId"] and HEX32.match(info["reqId"]) and reason not in ("replay", "bad-sig"):
                st["results"][info["reqId"]] = {"state": "refused", "reason": reason, "ts": now, "mode": info["mode"]}
                _write_json(state_file(), st)
            audit({"kind": "approve", "state": "refused", "reason": reason, **info})
            return "refused", reason, info
    summary = approval_policy.input_canon(tool, inp)[:200]
    audit({"kind": "approve", "state": "granted", "inputSha": sha, "summary": summary, **info})
    if notify:
        label = str((_device_ok(devices, device, now) or {}).get("label") or "スマホ")[:30]
        if info["mode"] == "tap":
            title = {"review": "スマホから許可（確認つきの操作・タップ）", "mac": "スマホから許可（危険な操作・タップ）"}.get(
                info.get("level"), "スマホから許可（タップ）")
        else:
            title = {"review": "スマホから許可（確認つきの操作・Face ID）", "mac": "スマホから許可（危険な操作・Face ID）"}.get(
                info.get("level"), "スマホから許可")
        notify(title, "%s: %s（%s）" % (tool, _short_input(inp), label))
    return "granted", "", info


def _short_input(inp):
    if isinstance(inp, dict):
        for k in ("command", "file_path", "path", "pattern", "description"):
            if isinstance(inp.get(k), str):
                s = " ".join(inp[k].split())
                return s if len(s) <= 60 else s[:59] + "…"
    return ""


# ------------------------------------------------------------------ 今日の件数（デスクトップの 📱 に出す）

def today_summary(now=None, tail=256 * 1024):
    """hook の監査の今日分: remote で許可した件数と、mac に落ちた理由の内訳（第 2 弾の許可リストの材料）。"""
    now = time.time() if now is None else now
    lt = time.localtime(now)
    start = time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, 0, 0, 0, 0, 0, -1))
    out = {"remote": 0, "asked": 0, "mac": {}}
    p = hook_audit_file()
    try:
        size = p.stat().st_size
        with open(p, "rb") as f:
            f.seek(max(0, size - tail))
            data = f.read()
    except OSError:
        return out
    for line in data.split(b"\n"):
        try:
            r = json.loads(line)
        except ValueError:
            continue
        if not isinstance(r, dict) or _num(r.get("ts")) < start:
            continue
        if r.get("ev") == "ask":
            out["asked"] += 1
            if r.get("level") == "mac":
                for reason in r.get("reasons") or []:
                    if reason in approval_policy.REASONS:
                        out["mac"][reason] = out["mac"].get(reason, 0) + 1
        elif r.get("ev") == "decide" and r.get("src") == "remote" and r.get("decision") == "allow":
            out["remote"] += 1
    return out
