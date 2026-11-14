#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""R126 出勤の口（door）の芯 — 常駐 Bot・スクリプトを「質問を持てる 1 人」として office_json に載せる。

契約の正本= docs/door.md（道具 5 つ・上限・行の必須キー・置き場）。設計= ~/.claude/plans/deep-rolling-book.md §1。

置き場（全部ファイル＝不変条件 9・0600／フォルダ 0700）:
  ~/.claude/office_door/bots.json            台帳（登録した Bot だけが出勤できる）
  ~/.claude/office_door/bot/<id>.json        状態（出勤・開いている相談 1 件・未読・適用済みの id）
  ~/.claude/office_door/bot/<id>.log.jsonl   記録（出勤の変化・報告・相談・答え・指示。上限で古い半分を捨てる）
  ~/.claude/office_door/audit.jsonl          監査（何が起きたかだけ・本文なし）

掟:
  * office_server を import しない（循環と CLI の重さ）。標準ライブラリと同じ server/ の office_common だけ。
  * 書き込みは Bot ごとの flock（office_common.file_flock）＋原子的な置き換え（atomic_write_json）。
    door_employees() は読むだけ（ロックも書き込みもしない＝走査を止めない）。
  * Bot の相談は承認の掲示板（~/.claude/office_approvals/）に置かない。Bot は許可を出せない（不変条件 7）。
  * Bot へ言葉を渡せるのは人だけ（HUMAN_SRCS＝Mac の画面とスマホ）。セッション（MCP）の文は Bot に入れない。
  * R127: Bot の行は中継に載る（relay_agent の Bot 用の狭い allowlist・不変条件 8 の例外＝Bot が自分で書いた問いと出勤の様子）。
    スマホの答えは id8 が開いている相談に合うときだけ届く（answer）・合わない答えは捨てて監査（指示に落とさない）。
  * R128 #4: 登録・直し・削除・消音は人の操作（この CLI と Mac の画面の ⚙ Bot パネル＝office_server の /api/door/*）。
    Bot の道具（5 つ）・中継（relay_agent）・セッション（MCP）には道が無い。消音は台帳の muted（鳴らさないだけ＝行には出る）。

CLI（server/ と一緒に常駐先へ配られる）:
  python3 server/office_door.py add <id> [--name 名前] [--role 役]     （結果の mcp＝その Bot を MCP として登録する 1 行）
  python3 server/office_door.py list | remove <id>
  python3 server/office_door.py checkin  --bot <id> --doing 文 [--state working|waiting|resting] [--ttl-min 15]
  python3 server/office_door.py report   --bot <id> --title 題 [--body 本文] [--kind done|info|warn]
  python3 server/office_door.py ask      --bot <id> --question 問い [--option 選択肢 ...] [--ttl-hours 24]
  python3 server/office_door.py inbox    --bot <id> [--ack <id> ...]
  python3 server/office_door.py withdraw --bot <id> --ask-id <32 桁>
  結果は 1 行の JSON（標準出力）・受け付けたら終了 0・断ったら 1・使い方の誤りは 2。
"""
import argparse
import json
import math
import os
import re
import secrets
import shlex
import sys
import time
import unicodedata
from pathlib import Path

try:
    import office_common
except ModuleNotFoundError:  # importlib でファイルを直接読むテスト向け（status_board と同じ流儀）
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    try:
        import office_common
    finally:
        del sys.path[0]

# 上限は掲示の質問と同じ値（正本= office_common）
ASK_Q_LEN = office_common.ASK_Q_LEN
ASK_OPT_MAX = office_common.ASK_OPT_MAX
ASK_OPT_LEN = office_common.ASK_OPT_LEN

ID_RE = re.compile(r"[a-z0-9][a-z0-9-]{3,31}")       # Bot の id（session は "bot-<id>"＝8〜36 字）
SESSION_PREFIX = "bot-"
MCP_NAME_PREFIX = "aioffice-door-"                     # R128 #4: 登録コマンドの MCP の名前（docs/door.md §3）
ASK_ID_RE = re.compile(r"[0-9a-f]{32}")
ID8_RE = re.compile(r"[0-9a-f]{8}")
ITEM_ID_RE = re.compile(r"[0-9a-f]{16}")
REQ_ID_RE = re.compile(r"[A-Za-z0-9_.:-]{1,64}")

NAME_LEN, ROLE_LEN = 24, 16
DOING_LEN, TITLE_LEN, BODY_LEN = 60, 80, 600
INSTRUCT_LEN = ANSWER_LEN = 4000                       # post_instruction と同じ
STATES = ("working", "waiting", "resting")
KINDS = ("done", "info", "warn")
INBOX_KINDS = ("answer", "instruct", "expired", "rejected")
TTL_MIN_RANGE, TTL_MIN_DEFAULT = (1, 240), 15
TTL_HOURS_RANGE, TTL_HOURS_DEFAULT = (1, 168), 24
ASK_PER_HOUR, ASK_PER_DAY = 6, 20
INBOX_MAX = 100                                        # 未読の上限（超えたら古い順に捨てて監査）
UNREAD_TTL = 14 * 86400                                # 未読は 14 日で消す
CLOSED_KEEP = 14 * 86400
CLOSED_MAX, APPLIED_MAX = 20, 200
BOTS_MAX = 64
LOG_MAX = 256 * 1024                                   # 記録 1 本の上限（超えたら新しい半分だけ残す）
AUDIT_MAX = 1024 * 1024
# これより大きい状態は壊れたものとして読まない（毒）。上限いっぱいの未読（INBOX_MAX 件 × 4000 字 × UTF-8 の 4 バイト＝1.6MB）は
# 正しい状態＝それが収まる大きさにする（1MB のころ、4000 字の日本語の指示 87 件で 1.05MB になり、次の操作が正しい状態を
# 「壊れている」と読んで未読と出勤を空で上書きした＝R126 の別モデルレビュー medium・2026-11-14）。
STATE_READ_MAX = INBOX_MAX * INSTRUCT_LEN * 4 + 256 * 1024
STATE_WRITE_MAX = STATE_READ_MAX - 64 * 1024           # 書く側はここまで（自分で書いた状態を読めなくしない＝_fit_state）
LEDGER_READ_MAX = 256 * 1024
FUTURE_SKEW = 300.0                                    # 未来の時刻はこれまで（時計の揺れ）。それより先は毒
SHOW_WINDOW_DEFAULT = 3 * 3600                         # office_server.SHOW_WINDOW と同じ既定（走査は値を渡す）
# 人の言葉を Bot へ渡せる呼び手。local＝Mac の画面（loopback＋CSRF）・relay＝スマホ（ペアリングした端末の署名を
# relay_agent が確かめた封筒）。R127 で relay を足した＝relay_agent の bot- の枝が「id8 が合う相談にだけ答える・
# 印の無い言葉は相談が閉じているときだけ指示」を守る（表示していない相談への答えが指示に化けない）。
# mcp（セッション）は入れない＝セッションの文を Bot に入れない（R126 の壁）。
HUMAN_SRCS = ("local", "relay")
_KIND_OF_STATE = {"working": "tool", "waiting": "said", "resting": "idle"}


def _t(lang, ja, en):
    return en if lang == "en" else ja


def valid_id(bot_id):
    return isinstance(bot_id, str) and ID_RE.fullmatch(bot_id) is not None


def session_of(bot_id):
    return SESSION_PREFIX + bot_id


def bot_of_session(session):
    """"bot-<id>" → id（形が違えば None）。"""
    if isinstance(session, str) and session.startswith(SESSION_PREFIX):
        bot_id = session[len(SESSION_PREFIX):]
        if valid_id(bot_id):
            return bot_id
    return None


def is_registered(bot_id, *, home=None):
    """登録した Bot か（読むだけ・台帳が読めなければ False）。R128: 記録の画面（source_door_log）は登録した Bot の記録だけを読む。"""
    return valid_id(bot_id) and bot_id in _read_ledger(_home(home))[0]


def mcp_command(bot_id, *, python=None):
    """R128 #4: その Bot を MCP として登録する 1 行（docs/door.md §3 の形＝その仕事のフォルダで実行・--scope user にしない）。
    id は valid_id を通ったものだけ（英小文字・数字・- だけ＝シェルの特殊文字が入らない）・形が違えば None。
    python3 の実体（既定はこのプロセスの sys.executable＝daemon なら install.sh が焼いた実体）とこのファイルの隣の
    mcp_office.py は shlex.quote（スペースの入ったフォルダ）。秘密は含まない（鍵もトークンも使わない stdio の MCP）。"""
    if not valid_id(bot_id):
        return None
    py = str(python or sys.executable or "python3")
    script = str(Path(__file__).resolve().parent / "mcp_office.py")
    return f"claude mcp add {MCP_NAME_PREFIX}{bot_id} -- {shlex.quote(py)} {shlex.quote(script)} --bot {bot_id}"


# ── 置き場 ─────────────────────────────────────────────
def _home(home=None):
    if home is not None:
        return Path(home)
    return Path(os.environ.get("OFFICE_HOME") or str(Path.home()))


def door_dir(home=None):
    return _home(home) / ".claude" / "office_door"


def ledger_path(home=None):
    return door_dir(home) / "bots.json"


def state_path(bot_id, home=None):
    return door_dir(home) / "bot" / f"{bot_id}.json"


def log_path(bot_id, home=None):
    return door_dir(home) / "bot" / f"{bot_id}.log.jsonl"


def audit_path(home=None):
    return door_dir(home) / "audit.jsonl"


def _ensure_dirs(home):
    base = door_dir(home)
    for d in (base, base / "bot"):
        d.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(d, 0o700)
        except OSError:
            pass


def _now(now=None):
    return float(time.time() if now is None else now)


# ── 自由文の正規化（Bot の文は信じない）────────────────────
_LINE_BREAKS = re.compile(r"[\t\n\r\v\f\x85  ]")
_SPACES = re.compile(r" {2,}")


def _drop_invisible(s, keep_newline):
    """制御文字（Cc）と書式文字（Cf＝双方向の上書き・ゼロ幅）を落とす。絵文字の結合（ZWJ）だけ残す。"""
    out = []
    for ch in s:
        if keep_newline and ch == "\n":
            out.append(ch)
            continue
        cat = unicodedata.category(ch)
        if cat == "Cc" or (cat == "Cf" and ch != "‍"):
            continue
        out.append(ch)
    return "".join(out)


def clean_line(value):
    """1 行の欄（名前・役・doing・題・問い・選択肢）: NFC・改行とタブは空白へ・見えない文字を落とす・空白を畳む。"""
    if not isinstance(value, str):
        return None
    s = _LINE_BREAKS.sub(" ", unicodedata.normalize("NFC", value))
    return _SPACES.sub(" ", _drop_invisible(s, False)).strip()


def clean_text(value):
    """複数行の欄（報告の本文・指示・答え）: NFC・改行は \\n にそろえる・見えない文字を落とす。"""
    if not isinstance(value, str):
        return None
    s = unicodedata.normalize("NFC", value).replace("\r\n", "\n")
    s = re.sub(r"[\r\x85  \v\f]", "\n", s).replace("\t", " ")
    s = "\n".join(line.rstrip() for line in _drop_invisible(s, True).split("\n"))
    return re.sub(r"\n{3,}", "\n\n", s).strip()


def _num(v):
    """有限の実数（bool は除く）。それ以外は None。"""
    if type(v) in (int, float) and math.isfinite(v):
        return float(v)
    return None


def _int_arg(v):
    """道具の整数の引数。15.0 のような整数の浮動小数は受ける（bool・文字列は受けない）。"""
    if type(v) is int:
        return v
    if type(v) is float and math.isfinite(v) and v.is_integer():
        return int(v)
    return None


# ── 断り（結果の形は 1 つ: {"ok", "text", ...}）────────────────
class _Refuse(Exception):
    def __init__(self, code, ja, en, audited=False, **extra):
        super().__init__(code)
        self.code, self.ja, self.en, self.audited, self.extra = code, ja, en, audited, extra

    def result(self, lang):
        return {"ok": False, "error": self.code, "text": _t(lang, self.ja, self.en), **self.extra}


def _ok(lang, ja, en, **extra):
    return {"ok": True, "text": _t(lang, ja, en), **extra}


def _bad(ja, en):
    return _Refuse("bad-args", ja, en)


# ── 記録と監査（追記・上限で古い半分を捨てる）────────────────
def _rotate(path, cap):
    """新しい側の半分（cap/2 バイトまで・行の境目から）だけ残して原子的に置き換える。呼び手が path の flock を持つ。"""
    try:
        data = path.read_bytes()
    except OSError:
        return
    keep = data[-(cap // 2):]
    nl = keep.find(b"\n")
    keep = keep[nl + 1:] if nl >= 0 and len(keep) < len(data) else keep
    tmp = path.with_name(f".{path.name}.rot")
    try:
        fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "wb") as f:
            f.write(keep)
        os.replace(tmp, path)
    except OSError:
        try:
            tmp.unlink()
        except OSError:
            pass


def _append_jsonl(path, obj, cap):
    line = (json.dumps(obj, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")
    with office_common.file_flock(path):
        fd = os.open(str(path), os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
        try:
            os.write(fd, line)
            size = os.fstat(fd).st_size
        finally:
            os.close(fd)
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
        if size > cap:
            _rotate(path, cap)


def _audit(home, now, ev, bot, **fields):
    """監査 1 行（本文なし＝数・種類・id だけ）。口のフォルダが無い（誰も登録していない）HOME には作らない。"""
    if not door_dir(home).is_dir():
        return
    row = {"ts": round(now, 3), "ev": ev, "bot": bot if valid_id(bot) else "?"}
    for k, v in fields.items():
        if v is None or type(v) in (bool, int) or (type(v) is float and math.isfinite(v)):
            row[k] = v
        elif isinstance(v, str) and len(v) <= 64:
            row[k] = v
    try:
        _append_jsonl(audit_path(home), row, AUDIT_MAX)
    except OSError:
        pass                                          # 監査は本流を止めない


def audit_event(ev, bot, *, home=None, now=None, **fields):
    """ほかのプロセス（relay_agent など）から口の監査へ 1 行。"""
    _audit(_home(home), _now(now), ev, bot, **fields)


class _Ctx:
    """1 回の操作の記録と監査を溜め、状態を書けたあとで流す（書けなかった操作を記録に残さない）。"""

    def __init__(self, home, bot_id, now):
        self.home, self.bot, self.now = home, bot_id, now
        self.dirty = False
        self._log, self._audit = [], []

    def log(self, obj):
        self._log.append(obj)

    def audit(self, ev, **fields):
        self._audit.append((ev, fields))

    def flush(self):
        for obj in self._log:
            try:
                _append_jsonl(log_path(self.bot, self.home), {"ts": round(self.now, 3), **obj}, LOG_MAX)
            except OSError:
                pass
        for ev, fields in self._audit:
            _audit(self.home, self.now, ev, self.bot, **fields)
        self._log, self._audit = [], []


# ── 台帳 ───────────────────────────────────────────────
def _read_json(path, limit):
    """(値, 状態)。状態= "missing"（無い）/"ok"/"broken"（読めない・大きすぎる・JSON でない）。"""
    try:
        if path.stat().st_size > limit:
            return None, "broken"
        return json.loads(path.read_text(encoding="utf-8")), "ok"
    except FileNotFoundError:
        return None, "missing"
    except (OSError, ValueError, UnicodeDecodeError):
        return None, "broken"


def _read_ledger(home):
    """(bots, ok)。bots= {id: {name, role, added, muted}}（形の合う行だけ・id 順に BOTS_MAX まで）。ok=False は台帳が壊れている。
    R128 #4: muted（消音）は真偽の True だけを消音と読む（"true"・1 などの形の違う値は消音にしない＝鳴る側に倒す）。"""
    raw, status = _read_json(ledger_path(home), LEDGER_READ_MAX)
    if status == "missing":
        return {}, True
    if status != "ok" or not isinstance(raw, dict) or not isinstance(raw.get("bots"), dict):
        return {}, False
    bots = {}
    for bot_id in sorted(k for k in raw["bots"] if isinstance(k, str)):
        entry = raw["bots"][bot_id]
        if not valid_id(bot_id) or not isinstance(entry, dict):
            continue                                  # 毒の行は黙って捨てる（1 行で全員を落とさない）
        name = (clean_line(entry.get("name")) or bot_id)[:NAME_LEN]
        role = (clean_line(entry.get("role")) or "Bot")[:ROLE_LEN]
        bots[bot_id] = {"name": name, "role": role, "added": _num(entry.get("added")) or 0.0,
                        "muted": entry.get("muted") is True}
        if len(bots) >= BOTS_MAX:
            break
    return bots, True


def _write_ledger(home, bots):
    office_common.atomic_write_json(ledger_path(home), {"v": 1, "bots": bots}, 0o600)


def add_bot(bot_id, name=None, role=None, *, home=None, now=None, lang="ja"):
    """Bot を登録する（同じ id なら名前と役を直す＝登録した時刻と消音はそのまま）。登録は人の操作（CLI・R128 #4 で Mac の画面）。"""
    home, now = _home(home), _now(now)
    try:
        if not valid_id(bot_id):
            raise _bad("Bot の id は英小文字・数字・- の 4〜32 字（先頭は英数字）",
                       "A bot id is 4-32 of a-z, 0-9 and - (starting with a letter or digit)")
        nm = clean_line(name) if name is not None else bot_id
        if not nm or len(nm) > NAME_LEN:
            raise _bad(f"名前は 1〜{NAME_LEN} 字", f"name must be 1-{NAME_LEN} characters")
        rl = clean_line(role) if role is not None else "Bot"
        if not rl or len(rl) > ROLE_LEN:
            raise _bad(f"役は 1〜{ROLE_LEN} 字", f"role must be 1-{ROLE_LEN} characters")
        _ensure_dirs(home)
        with office_common.file_flock(ledger_path(home)):
            bots, ok = _read_ledger(home)
            if not ok:
                raise _Refuse("ledger-broken",
                              f"台帳が読めません（{ledger_path(home)} を直すか消してください）",
                              f"The bot ledger is unreadable ({ledger_path(home)})")
            existed = bot_id in bots
            if not existed and len(bots) >= BOTS_MAX:
                raise _Refuse("too-many", f"登録できる Bot は {BOTS_MAX} 体まで", f"Up to {BOTS_MAX} bots")
            prev = bots.get(bot_id, {})
            bots[bot_id] = {"name": nm, "role": rl, "added": prev.get("added") or now, "muted": prev.get("muted") is True}
            _write_ledger(home, bots)
    except _Refuse as r:
        _audit(home, now, "refuse", bot_id, op="add", why=r.code)
        return r.result(lang)
    _audit(home, now, "update" if existed else "add", bot_id)
    return _ok(lang, "登録を直しました" if existed else "登録しました",
               "Updated" if existed else "Registered",
               bot=bot_id, session=session_of(bot_id), name=nm, role=rl, updated=existed)


def remove_bot(bot_id, *, home=None, now=None, lang="ja"):
    """登録を外し、その Bot の状態と記録を消す（台帳 → 状態の順にロック＝操作中の Bot と競っても孤児を作らない）。"""
    home, now = _home(home), _now(now)
    try:
        if not valid_id(bot_id):
            raise _bad("Bot の id が不正です", "bad bot id")
        if not ledger_path(home).exists():
            raise _Refuse("unregistered", "登録されていない Bot です", "Not a registered bot")
        with office_common.file_flock(ledger_path(home)):
            bots, ok = _read_ledger(home)
            if not ok:
                raise _Refuse("ledger-broken", "台帳が読めません", "The bot ledger is unreadable")
            if bot_id not in bots:
                raise _Refuse("unregistered", "登録されていない Bot です", "Not a registered bot")
            with office_common.file_flock(state_path(bot_id, home)):
                del bots[bot_id]
                _write_ledger(home, bots)
                for p in (state_path(bot_id, home), log_path(bot_id, home)):
                    try:
                        p.unlink()
                    except FileNotFoundError:
                        pass
    except _Refuse as r:
        _audit(home, now, "refuse", bot_id, op="remove", why=r.code)
        return r.result(lang)
    _audit(home, now, "remove", bot_id)
    return _ok(lang, "登録を外しました", "Removed", bot=bot_id)


def set_muted(bot_id, on, *, home=None, now=None, lang="ja"):
    """R128 #4: 消音の切替（人の操作＝Mac の画面の ⚙ Bot パネルだけ・Bot の道具と中継には道が無い）。台帳の muted に持つ（不変条件 9）。
    消音は**鳴らさないだけ**: その Bot の相談で Mac の音と通知（新しい ❗・待たせすぎ・朝のまとめ・画面の通知と音）を出さない。
    行・あなたの番・記録・受付の並びには今までどおり出る。on は真偽だけ（"true"・1 は断る）。同じ値なら書かない（changed=False）。"""
    home, now = _home(home), _now(now)
    try:
        if not valid_id(bot_id):
            raise _bad("Bot の id が不正です", "bad bot id")
        if type(on) is not bool:
            raise _bad("on は true か false", "on must be true or false")
        if not ledger_path(home).exists():
            raise _Refuse("unregistered", "登録されていない Bot です", "Not a registered bot")
        with office_common.file_flock(ledger_path(home)):
            bots, ok = _read_ledger(home)
            if not ok:
                raise _Refuse("ledger-broken", "台帳が読めません", "The bot ledger is unreadable")
            if bot_id not in bots:
                raise _Refuse("unregistered", "登録されていない Bot です", "Not a registered bot")
            changed = bots[bot_id]["muted"] is not on
            if changed:
                bots[bot_id]["muted"] = on
                _write_ledger(home, bots)
    except _Refuse as r:
        _audit(home, now, "refuse", bot_id, op="mute", why=r.code)
        return r.result(lang)
    if changed:
        _audit(home, now, "mute", bot_id, on=on)
    return _ok(lang, "消音にしました" if on else "消音を解きました", "Muted" if on else "Unmuted",
               bot=bot_id, muted=on, changed=changed)


# ── 状態 ───────────────────────────────────────────────
def _fresh_state():
    return {"v": 1, "checkin": None, "seen": 0.0, "ask": None, "inbox": [], "asks": [],
            "closed": [], "applied": []}


def _ts_ok(v, now):
    t = _num(v)
    return t if t is not None and 0 < t <= now + FUTURE_SKEW else None


def _norm_options(v):
    if not isinstance(v, list) or len(v) > ASK_OPT_MAX:
        return None
    out = []
    for o in v:
        o = clean_line(o)
        if not o or len(o) > ASK_OPT_LEN or o in out:
            return None
        out.append(o)
    return out


def _norm_item(it, now):
    """未読 1 件の形を確かめて写す（知らないキー・形の違う件は落とす）。"""
    if not isinstance(it, dict) or it.get("kind") not in INBOX_KINDS:
        return None
    iid, ts = it.get("id"), _ts_ok(it.get("ts"), now)
    if not (isinstance(iid, str) and ITEM_ID_RE.fullmatch(iid)) or ts is None:
        return None
    out = {"id": iid, "kind": it["kind"], "ts": ts}
    aid = it.get("askId")
    if it["kind"] in ("answer", "expired", "rejected"):
        if not (isinstance(aid, str) and ASK_ID_RE.fullmatch(aid)):
            return None
        out["askId"] = aid
    if it["kind"] in ("answer", "instruct"):
        text = it.get("text")
        if not isinstance(text, str) or not text or len(text) > INSTRUCT_LEN:
            return None
        out["text"] = text
        if isinstance(it.get("src"), str) and it["src"] in HUMAN_SRCS + ("relay",):
            out["src"] = it["src"]
    ch = it.get("choice")
    if it["kind"] == "answer" and isinstance(ch, dict) and type(ch.get("index")) is int \
            and 0 <= ch["index"] < ASK_OPT_MAX and isinstance(ch.get("label"), str) and len(ch["label"]) <= ASK_OPT_LEN:
        out["choice"] = {"index": ch["index"], "label": ch["label"]}
    if it["kind"] == "rejected" and isinstance(it.get("why"), str) and len(it["why"]) <= 32:
        out["why"] = it["why"]
    return out


def _norm_state(raw, now):
    """状態ファイルの中身を、形の合う所だけ写す（毒の値で走査も道具も落とさない）。"""
    st = _fresh_state()
    if not isinstance(raw, dict):
        return st
    ck = raw.get("checkin")
    if isinstance(ck, dict):
        ts, doing, ttl = _ts_ok(ck.get("ts"), now), clean_line(ck.get("doing")), ck.get("ttl")
        if (ts is not None and doing and len(doing) <= DOING_LEN and ck.get("state") in STATES
                and type(ttl) is int and TTL_MIN_RANGE[0] <= ttl <= TTL_MIN_RANGE[1]):
            st["checkin"] = {"ts": ts, "doing": doing, "state": ck["state"], "ttl": ttl}
    st["seen"] = _ts_ok(raw.get("seen"), now) or 0.0
    a = raw.get("ask")
    if isinstance(a, dict):
        ts, dl, q = _ts_ok(a.get("ts"), now), _num(a.get("deadline")), clean_line(a.get("question"))
        opts = _norm_options(a.get("options") if a.get("options") is not None else [])
        aid = a.get("askId")
        if (isinstance(aid, str) and ASK_ID_RE.fullmatch(aid) and ts is not None and dl is not None
                and ts < dl <= ts + TTL_HOURS_RANGE[1] * 3600 and q and len(q) <= ASK_Q_LEN and opts is not None):
            st["ask"] = {"askId": aid, "question": q, "options": opts, "ts": ts, "deadline": dl}
    if isinstance(raw.get("inbox"), list):
        items = [x for x in (_norm_item(it, now) for it in raw["inbox"][-INBOX_MAX * 2:]) if x]
        st["inbox"] = items[-INBOX_MAX:]
    if isinstance(raw.get("asks"), list):
        st["asks"] = [t for t in (_ts_ok(x, now) for x in raw["asks"][-ASK_PER_DAY * 2:]) if t is not None]
    if isinstance(raw.get("closed"), list):
        for row in raw["closed"][-CLOSED_MAX:]:
            if (isinstance(row, list) and len(row) == 3 and isinstance(row[0], str) and ASK_ID_RE.fullmatch(row[0])
                    and _ts_ok(row[1], now) is not None and row[2] in ("answered", "withdrawn", "expired")):
                st["closed"].append([row[0], float(row[1]), row[2]])
    if isinstance(raw.get("applied"), list):
        for row in raw["applied"][-APPLIED_MAX:]:
            if (isinstance(row, list) and len(row) == 3 and isinstance(row[0], str) and REQ_ID_RE.fullmatch(row[0])
                    and _ts_ok(row[1], now) is not None and isinstance(row[2], dict)):
                st["applied"].append([row[0], float(row[1]), row[2]])
    return st


def _read_state(path, now):
    """(状態, 壊れていたか)。無い・壊れている・大きすぎる状態は空から始める。"""
    raw, status = _read_json(path, STATE_READ_MAX)
    return _norm_state(raw if status == "ok" else None, now), status == "broken"


def _json_size(obj):
    return len(json.dumps(obj, ensure_ascii=False).encode("utf-8"))      # atomic_write_json と同じ直列化


def _fit_state(st, limit=None):
    """書く前に、状態が STATE_WRITE_MAX に収まるよう未読を古い順に捨てる（捨てた数を返す）。
    読む側の上限（STATE_READ_MAX）を超える状態を書くと、次の操作がそれを「壊れている」と読んで全部を空で上書きする
    ＝定数を変えても「自分で書いたものは読める」を保つ安全網（今の上限では、正しい未読だけなら捨てない）。"""
    limit = STATE_WRITE_MAX if limit is None else limit
    sizes = [_json_size(it) + 2 for it in st["inbox"]]                    # 区切り（", "）の分
    total = _json_size(dict(st, inbox=[])) + sum(sizes)
    lost = 0
    while lost < len(sizes) and total > limit:
        total -= sizes[lost]
        lost += 1
    if lost:
        st["inbox"] = st["inbox"][lost:]
    return lost


def _write_state(path, st, ctx=None):
    lost = _fit_state(st)
    if lost and ctx is not None:
        ctx.audit("drop", n=lost, why="bytes")
    office_common.atomic_write_json(path, st, 0o600)


def _push(st, item, now, ctx):
    """未読へ 1 件（id は乱数・上限を超えたら古い順に捨てて監査）。"""
    item = dict(item, id=secrets.token_hex(8), ts=now)
    st["inbox"].append(item)
    over = len(st["inbox"]) - INBOX_MAX
    if over > 0:
        st["inbox"] = st["inbox"][over:]
        ctx.audit("drop", n=over, why="cap")
    return item


def _close(st, ask_id, now, how):
    st["closed"] = (st["closed"] + [[ask_id, now, how]])[-CLOSED_MAX:]


def _closed_how(st, ask_ref):
    """閉じた相談の閉じ方（answered/withdrawn/expired）。ask_ref は askId か先頭 8 桁。"""
    return next((how for aid, _ts, how in reversed(st["closed"])
                 if aid == ask_ref or (len(ask_ref) == 8 and aid[:8] == ask_ref)), None)


def _sweep(st, now, ctx):
    """期限の切れた相談を閉じて未読に expired を 1 件・古い未読（14 日）と古い控えを捨てる。変えたら True。"""
    changed = False
    a = st["ask"]
    if a and now >= a["deadline"]:
        st["ask"] = None
        _close(st, a["askId"], now, "expired")
        _push(st, {"kind": "expired", "askId": a["askId"]}, now, ctx)
        ctx.log({"ev": "expired", "askId": a["askId"]})
        ctx.audit("expire", askId=a["askId"])
        changed = True
    keep = [it for it in st["inbox"] if now - it["ts"] <= UNREAD_TTL]
    if len(keep) != len(st["inbox"]):
        ctx.audit("drop", n=len(st["inbox"]) - len(keep), why="old")
        st["inbox"], changed = keep, True
    for key, idx, ttl in (("asks", None, 86400), ("closed", 1, CLOSED_KEEP), ("applied", 1, UNREAD_TTL)):
        rows = [r for r in st[key] if now - (r if idx is None else r[idx]) < ttl]
        if len(rows) != len(st[key]):
            st[key], changed = rows, True
    return changed


def _counts(st, lang):
    a = sum(1 for it in st["inbox"] if it["kind"] == "answer")
    m = sum(1 for it in st["inbox"] if it["kind"] == "instruct")
    o = len(st["inbox"]) - a - m
    tail = _t(lang, f"・ほか {o}", f", other {o}") if o else ""
    return {"answers": a, "instructs": m, "others": o,
            "waiting": _t(lang, f"届いているもの: 答え {a}・指示 {m}{tail}",
                          f"Waiting for you: answers {a}, instructions {m}{tail}")}


def _bot_op(home, bot_id, now, lang, op, mutate, req_id=None):
    """登録を確かめ → その Bot の状態を flock の中で読み・掃除し・mutate(st, ctx) → 変わったら原子的に書いて記録と監査を流す。"""
    home, now = _home(home), _now(now)
    if not valid_id(bot_id):
        _audit(home, now, "refuse", "?", op=op, why="bad-id")
        return _Refuse("bad-id", "Bot の id が不正です", "bad bot id").result(lang)
    unregistered = _Refuse("unregistered",
                           f"登録されていない Bot です（python3 server/office_door.py add {bot_id} --name 名前）",
                           f"Not a registered bot (python3 server/office_door.py add {bot_id} --name NAME)")
    if req_id is not None and not (isinstance(req_id, str) and REQ_ID_RE.fullmatch(req_id)):
        return _bad("req_id が不正です", "bad req_id").result(lang)
    if bot_id not in _read_ledger(home)[0]:
        _audit(home, now, "refuse", bot_id, op=op, why="unregistered")
        return unregistered.result(lang)
    _ensure_dirs(home)
    sp = state_path(bot_id, home)
    with office_common.file_flock(sp):
        # 外しと競ったら書かない（remove_bot は台帳 → 状態の順にロックして消す＝ここで読み直せば孤児の状態を作らない）
        if bot_id not in _read_ledger(home)[0]:
            _audit(home, now, "refuse", bot_id, op=op, why="unregistered")
            return unregistered.result(lang)
        st, broken = _read_state(sp, now)
        ctx = _Ctx(home, bot_id, now)
        if broken:
            ctx.audit("reset", why="broken")
            ctx.dirty = True
        changed = _sweep(st, now, ctx)
        hit = next((row for row in st["applied"] if row[0] == req_id), None) if req_id else None
        if hit:
            result = dict(hit[2], dup=True)              # 同じ id の再配達＝前の結果を返す（二重に入れない）
        else:
            try:
                result = mutate(st, ctx)
                if req_id and result.get("ok"):
                    st["applied"] = (st["applied"] + [[req_id, now, result]])[-APPLIED_MAX:]
                    ctx.dirty = True
            except _Refuse as r:
                if not r.audited:
                    ctx.audit("refuse", op=op, why=r.code)
                result = r.result(lang)
        if ctx.dirty or changed:
            _write_state(sp, st, ctx)
        ctx.flush()
    return result


# ── Bot の道具（出勤・報告・相談・未読・取り下げ）──────────────
def checkin(bot_id, doing, state="working", ttl_min=TTL_MIN_DEFAULT, *, home=None, now=None, req_id=None, lang="ja"):
    """出勤を記録する。前と同じ内容で、期限の半分より前なら書かない（期限は延ばさなくても切れない）。"""
    now = _now(now)
    d, ttl = clean_line(doing), _int_arg(ttl_min)

    def mutate(st, ctx):
        if not d or len(d) > DOING_LEN:
            raise _bad(f"doing は 1〜{DOING_LEN} 字", f"doing must be 1-{DOING_LEN} characters")
        if state not in STATES:
            raise _bad("state は working / waiting / resting", "state must be working, waiting or resting")
        if ttl is None or not TTL_MIN_RANGE[0] <= ttl <= TTL_MIN_RANGE[1]:
            raise _bad(f"ttl_min は {TTL_MIN_RANGE[0]}〜{TTL_MIN_RANGE[1]} の整数",
                       f"ttl_min must be an integer {TTL_MIN_RANGE[0]}-{TTL_MIN_RANGE[1]}")
        ck = st["checkin"]
        same = bool(ck and ck["doing"] == d and ck["state"] == state and ck["ttl"] == ttl
                    and ck["ts"] + ttl * 60 - now > ttl * 30)
        if not same:
            changed = not ck or ck["doing"] != d or ck["state"] != state
            st["checkin"] = {"ts": now, "doing": d, "state": state, "ttl": ttl}
            st["seen"] = now
            ctx.dirty = True
            if changed:                                   # 期限を延ばしただけの出勤は記録・監査に残さない
                ctx.log({"ev": "checkin", "doing": d, "state": state, "ttl": ttl})
                ctx.audit("checkin", state=state, ttl=ttl)
        c = _counts(st, lang)
        head = _t(lang, "出勤は前と同じです（書いていません）。" if same else "出勤を記録しました。",
                  "Same check-in as before (not written). " if same else "Checked in. ")
        return _ok(lang, head + c["waiting"], head + c["waiting"], same=same,
                   answers=c["answers"], instructs=c["instructs"], others=c["others"],
                   until=round((st["checkin"]["ts"] + ttl * 60), 3))
    return _bot_op(home, bot_id, now, lang, "checkin", mutate, req_id)


def report(bot_id, title, body="", kind="done", *, home=None, now=None, req_id=None, lang="ja"):
    """終わったことの記録（Bot の記録に 1 行・鳴らさない）。"""
    now = _now(now)
    ti, bd = clean_line(title), clean_text("" if body is None else body)

    def mutate(st, ctx):
        if not ti or len(ti) > TITLE_LEN:
            raise _bad(f"title は 1〜{TITLE_LEN} 字", f"title must be 1-{TITLE_LEN} characters")
        if bd is None or len(bd) > BODY_LEN:
            raise _bad(f"body は {BODY_LEN} 字まで", f"body must be at most {BODY_LEN} characters")
        if kind not in KINDS:
            raise _bad("kind は done / info / warn", "kind must be done, info or warn")
        st["seen"] = now
        ctx.dirty = True
        ctx.log({"ev": "report", "kind": kind, "title": ti, "body": bd})
        ctx.audit("report", kind=kind)
        return _ok(lang, "記録しました", "Recorded")
    return _bot_op(home, bot_id, now, lang, "report", mutate, req_id)


def ask(bot_id, question, options=None, ttl_hours=TTL_HOURS_DEFAULT, *, home=None, now=None, req_id=None, lang="ja"):
    """人に相談する（Bot ごとに開いている相談は 1 件・1 時間 6 件／1 日 20 件まで）。askId を返す。"""
    now = _now(now)
    q, opts, ttl = clean_line(question), _norm_options([] if options is None else options), _int_arg(ttl_hours)

    def mutate(st, ctx):
        if not q or len(q) > ASK_Q_LEN:
            raise _bad(f"question は 1〜{ASK_Q_LEN} 字", f"question must be 1-{ASK_Q_LEN} characters")
        if opts is None:
            raise _bad(f"options は {ASK_OPT_LEN} 字までの文字列を {ASK_OPT_MAX} 個まで（重複なし）",
                       f"options: up to {ASK_OPT_MAX} distinct strings of at most {ASK_OPT_LEN} characters")
        if ttl is None or not TTL_HOURS_RANGE[0] <= ttl <= TTL_HOURS_RANGE[1]:
            raise _bad(f"ttl_hours は {TTL_HOURS_RANGE[0]}〜{TTL_HOURS_RANGE[1]} の整数",
                       f"ttl_hours must be an integer {TTL_HOURS_RANGE[0]}-{TTL_HOURS_RANGE[1]}")
        if st["ask"]:
            raise _Refuse("ask-open", "先の答えを待っています（取り下げるなら office_withdraw）",
                          "Still waiting for the answer to your previous question (office_withdraw to withdraw it)",
                          askId=st["ask"]["askId"])
        if sum(1 for t in st["asks"] if now - t < 3600) >= ASK_PER_HOUR:
            raise _Refuse("rate-hour", f"相談は 1 時間に {ASK_PER_HOUR} 件まで", f"Up to {ASK_PER_HOUR} questions an hour")
        if len(st["asks"]) >= ASK_PER_DAY:
            raise _Refuse("rate-day", f"相談は 1 日 {ASK_PER_DAY} 件まで", f"Up to {ASK_PER_DAY} questions a day")
        aid = secrets.token_hex(16)
        st["ask"] = {"askId": aid, "question": q, "options": opts, "ts": now, "deadline": now + ttl * 3600}
        st["asks"].append(now)
        st["seen"] = now
        ctx.dirty = True
        ctx.log({"ev": "ask", "askId": aid, "question": q, "options": opts, "ttlH": ttl})
        ctx.audit("ask", askId=aid, n=len(opts), ttlH=ttl)
        return _ok(lang, "相談を出しました（答えは office_inbox で受け取れます）",
                   "Question posted (read the answer with office_inbox)",
                   askId=aid, deadline=round(now + ttl * 3600, 3))
    return _bot_op(home, bot_id, now, lang, "ask", mutate, req_id)


def _public_item(it):
    return {k: it[k] for k in ("id", "kind", "ts", "askId", "text", "choice", "why") if k in it}


def inbox(bot_id, ack=None, *, home=None, now=None, lang="ja"):
    """未読を覗く（消さない）。ack に読んだ id を渡すとそれだけ消す（at-least-once）。"""
    now = _now(now)

    def mutate(st, ctx):
        if ack is not None and (not isinstance(ack, list) or len(ack) > INBOX_MAX * 2
                                or not all(isinstance(x, str) and len(x) <= 64 for x in ack)):
            raise _bad("ack は読んだ id（文字列）の配列", "ack must be a list of item ids")
        n = 0
        if ack:
            gone = set(ack)
            keep = [it for it in st["inbox"] if it["id"] not in gone]
            n = len(st["inbox"]) - len(keep)
            if n:
                st["inbox"] = keep
                ctx.dirty = True
                ctx.audit("ack", n=n)
        c = _counts(st, lang)
        return _ok(lang, (f"{n} 件を既読にしました。" if n else "") + c["waiting"],
                   (f"Marked {n} read. " if n else "") + c["waiting"],
                   items=[_public_item(it) for it in st["inbox"]], acked=n)
    return _bot_op(home, bot_id, now, lang, "inbox", mutate)


def withdraw(bot_id, ask_id, *, home=None, now=None, req_id=None, lang="ja"):
    """相談を取り下げる（ほかの窓口で先に答えが出たとき）。もう閉じた相談なら閉じ方を返す（冪等）。"""
    now = _now(now)

    def mutate(st, ctx):
        if not (isinstance(ask_id, str) and ASK_ID_RE.fullmatch(ask_id)):
            raise _bad("askId は 32 桁の 16 進", "askId must be 32 hex digits")
        a = st["ask"]
        if a and a["askId"] == ask_id:
            st["ask"] = None
            _close(st, ask_id, now, "withdrawn")
            st["seen"] = now
            ctx.dirty = True
            ctx.log({"ev": "withdraw", "askId": ask_id})
            ctx.audit("withdraw", askId=ask_id)
            return _ok(lang, "取り下げました", "Withdrawn", askId=ask_id)
        how = _closed_how(st, ask_id)
        if how:
            return _ok(lang, f"もう閉じています（{how}）", f"Already closed ({how})", askId=ask_id, already=how)
        raise _Refuse("no-ask", "その相談はありません", "No such question")
    return _bot_op(home, bot_id, now, lang, "withdraw", mutate, req_id)


# ── 人から Bot へ（指示・答え）────────────────────────────
# 選択肢の答えの文の型（Mac とスマホがそれぞれ送る文。実物= ui/hud/strings.js の opt_text（ja/en）と
# ui/pwa/app.js の T("選択肢「…」でお願いします。","Please go with the option: …")。tests/test_office_door.py が実物から拾ってピン）
_CHOICE_RES = (
    re.compile(r"選択肢「(.+)」でお願いします。", re.S),     # Mac・スマホ（ja）
    re.compile(r'Please go with option "(.+)"\.', re.S),   # Mac（en）
    re.compile(r"Please go with the option: (.+)", re.S),  # スマホ（en）
)


def resolve_choice(text, options):
    """答えの文が選択肢のどれかなら {index, label}（Bot が文字列の一致に頼らなくて済む）。当たらなければ None。"""
    if not isinstance(text, str) or not isinstance(options, list) or not options:
        return None
    t = unicodedata.normalize("NFC", text).strip()
    cands = [m.group(1).strip() for rx in _CHOICE_RES for m in [rx.fullmatch(t)] if m] + [t]
    for c in cands:
        for i, o in enumerate(options):
            if c == o:
                return {"index": i, "label": o}
    return None


def _human(src, lang):
    if src not in HUMAN_SRCS:
        raise _Refuse("src", "Bot へ言葉を渡せるのは人（Mac の画面・スマホ）だけです（セッションの文は Bot に入れない）",
                      "Only a person (on the Mac screen or the phone) can send words to a bot")


def instruct(bot_id, text, *, src, home=None, now=None, req_id=None, lang="ja"):
    """人 → Bot の指示（post_instruction の bot- の枝から）。相談が開いている間は受けない（指示は相談が閉じてから）。
    req_id＝1 通ごとの冪等の id（R127: スマホの封筒は relay_agent が端末と nonce から作る＝同じ封筒を二度入れない）。"""
    now = _now(now)
    try:
        _human(src, lang)
    except _Refuse as r:
        _audit(_home(home), now, "refuse", bot_id, op="instruct", why=r.code, src=str(src)[:16])
        return r.result(lang)
    body = clean_text(text)

    def mutate(st, ctx):
        if not body or len(body) > INSTRUCT_LEN:
            raise _bad(f"指示は 1〜{INSTRUCT_LEN} 字", f"instructions must be 1-{INSTRUCT_LEN} characters")
        if st["ask"]:
            raise _Refuse("ask-open", "この Bot は相談の答えを待っています（指示は相談が閉じてから）",
                          "This bot is waiting for an answer to its question (instruct it after that)")
        _push(st, {"kind": "instruct", "text": body, "src": src}, now, ctx)
        ctx.dirty = True
        ctx.log({"ev": "instruct", "src": src, "text": body})
        ctx.audit("instruct", src=src, n=len(body))
        return _ok(lang, "Bot へ届けました（Bot が office_inbox で読みます）",
                   "Delivered to the bot (it reads it with office_inbox)")
    return _bot_op(home, bot_id, now, lang, "instruct", mutate, req_id)


def answer(bot_id, ask_ref, text, *, src, home=None, now=None, req_id=None, lang="ja"):
    """人 → Bot の答え（R127: Mac の /api/approval/reply＝askId 32 桁・スマホの封筒＝id8 から）。ask_ref＝askId（32 桁）か先頭 8 桁。
    開いている相談と合わない答えは**捨てて監査に残す**（指示に落とさない＝別の依頼への「やる」に化けない）。
    選択肢の文（Mac・スマホの 4 つの型）は口が choice{index,label} に解決する。req_id は instruct と同じ（冪等）。"""
    now = _now(now)
    try:
        _human(src, lang)
    except _Refuse as r:
        _audit(_home(home), now, "refuse", bot_id, op="answer", why=r.code, src=str(src)[:16])
        return r.result(lang)
    body = clean_text(text)

    def mutate(st, ctx):
        if not body or len(body) > ANSWER_LEN:
            raise _bad(f"答えは 1〜{ANSWER_LEN} 字", f"answers must be 1-{ANSWER_LEN} characters")
        if not (isinstance(ask_ref, str) and (ASK_ID_RE.fullmatch(ask_ref) or ID8_RE.fullmatch(ask_ref))):
            raise _bad("askId は 32 桁か先頭 8 桁の 16 進", "askId must be 32 or 8 hex digits")
        a = st["ask"]
        if not a or not (a["askId"] == ask_ref or (len(ask_ref) == 8 and a["askId"][:8] == ask_ref)):
            ctx.audit("discard", why="closed" if _closed_how(st, ask_ref) else "nomatch", src=src)
            raise _Refuse("no-ask", "その相談はもう閉じています（答えは捨てました）",
                          "That question is already closed (the answer was dropped)", audited=True)
        choice = resolve_choice(body, a["options"])
        st["ask"] = None
        _close(st, a["askId"], now, "answered")
        item = {"kind": "answer", "askId": a["askId"], "text": body, "src": src}
        if choice:
            item["choice"] = choice
        _push(st, item, now, ctx)
        ctx.dirty = True
        ctx.log({"ev": "answer", "askId": a["askId"], "text": body, **({"choice": choice} if choice else {})})
        ctx.audit("answer", askId=a["askId"], src=src, choice=choice["index"] if choice else None)
        return _ok(lang, "答えを届けました", "Answer delivered", askId=a["askId"], choice=choice)
    return _bot_op(home, bot_id, now, lang, "answer", mutate, req_id)


def closed_how(bot_id, ask_ref, *, home=None, now=None):
    """閉じた相談の閉じ方（"answered"／"withdrawn"／"expired"）。開いている・知らない・読めないなら None。読むだけ（ロックも書き込みもしない）。
    R127: daemon の _attn_track が「消えた相談のうち人が答えた分」だけを日報の「答えた ❗」に数えるために読む
    （期限切れ・取り下げは数えない）。ask_ref＝askId（32 桁）か先頭 8 桁。"""
    if not valid_id(bot_id) or not (isinstance(ask_ref, str)
                                    and (ASK_ID_RE.fullmatch(ask_ref) or ID8_RE.fullmatch(ask_ref))):
        return None
    st, _broken = _read_state(state_path(bot_id, home), _now(now))
    return _closed_how(st, ask_ref)


# ── 掃除（daemon の prune_inbox_litter から 1 時間に 1 回）──────────
def sweep_all(home=None, now=None):
    """登録した全 Bot の期限切れの相談を閉じ、古い未読を捨てる（Bot が道具を呼ぶたびにも同じ掃除が走る）。変えた数を返す。"""
    home, now = _home(home), _now(now)
    bots, _ok_ = _read_ledger(home)
    n = 0
    for bot_id in bots:
        sp = state_path(bot_id, home)
        if not sp.is_file():
            continue
        try:
            with office_common.file_flock(sp):
                st, broken = _read_state(sp, now)
                if broken:
                    continue                              # 壊れた状態は Bot の次の操作で作り直す（掃除では触らない）
                ctx = _Ctx(home, bot_id, now)
                if _sweep(st, now, ctx):
                    _write_state(sp, st, ctx)
                    ctx.flush()
                    n += 1
        except OSError:
            continue
    return n


# ── 走査（office_server.scan_office が合流する）──────────────────
def _row(bot_id, entry, st, now, lang, show_window):
    ck, a = st["checkin"], st["ask"]
    ask_open = a is not None and now < a["deadline"]
    live = ck is not None and now < ck["ts"] + ck["ttl"] * 60
    last = max([st["seen"]] + ([ck["ts"]] if ck else []) + ([a["ts"]] if ask_open else []))
    end = max(last, ck["ts"] + ck["ttl"] * 60 if ck else 0.0)
    if ask_open:
        state, verb = "waiting", _t(lang, "相談の答え待ち", "waiting for your answer")
    elif live:
        state, verb = ck["state"], ck["doing"]
    elif last > 0 and now - end <= show_window:
        state, verb = "resting", _t(lang, "休憩中", "resting")
    else:
        return None                                   # 退勤（開いている相談がある間は居続ける）
    row = {
        "session": session_of(bot_id),
        "vendor": "bot",
        "dept": entry["name"],
        "role": entry["role"],
        "state": state,
        "kind": _KIND_OF_STATE[state],
        "verb": verb,
        "age": max(0, int(now - last)),
        "mtime": last,
        # Bot はいつでも受け取れる（未読は office_inbox で読む）＝「次に操作すると届く」と誤案内しない
        "listening": True,
        "pending": bool(st["inbox"]),
        # R128 #4: 消音（⚙ の Bot パネル・台帳の muted）。Mac の知らせと音だけが読む（鳴らさないだけ＝行はこのまま出す）。
        # 中継には載らない（relay_agent の Bot の表 _ALLOW_DOOR_ENTRY に無い＝スマホの Push は Worker が決める・docs/door.md §10）
        "muted": entry["muted"],
        # 本文とパスの鍵は空・feed も空（報告の題を入れない＝feed は中継に載る鍵）
        "cwd": "", "branch": "", "lastSaid": "", "lastOrder": "", "target": "",
        "feed": [], "skills": [], "minions": 0, "avatar": 0,
        "question": "", "approvalMin": 0, "stuckTool": "",
    }
    if ask_open:
        row["question"] = a["question"]
        if a["options"]:
            row["questionOptions"] = [{"label": o, "desc": ""} for o in a["options"]]
        row["ask"] = {"tool": "office_ask", "kind": "question", "title": a["question"],
                      "options": list(a["options"]), "askId": a["askId"], "ts": a["ts"],
                      "min": max(0, int((now - a["ts"]) // 60)), "remote": 0}
    return row


def door_employees(home, now, lang="ja", show_window=None):
    """登録した Bot のうち、いま居る分の行（office_json の employees 互換）と (rows, meta)。読むだけ（ロックも書き込みもしない）。
    meta= {ok: 台帳が読めたか, bots: 登録数, n: 載せた行の数, asks: 開いている相談の数}。"""
    home, now = _home(home), _now(now)
    if show_window is None:
        show_window = int(os.environ.get("OFFICE_SHOW_WINDOW", SHOW_WINDOW_DEFAULT))
    bots, ok = _read_ledger(home)
    rows = []
    for bot_id, entry in bots.items():
        st, _broken = _read_state(state_path(bot_id, home), now)
        row = _row(bot_id, entry, st, now, lang, show_window)
        if row:
            rows.append(row)
    counts = {}
    for row in rows:                                   # 同じ名前は採番（id 順＝走査のたびに入れ替わらない）
        n = counts.get(row["dept"], 0) + 1
        counts[row["dept"]] = n
        row["disp"] = row["dept"] if n == 1 else (f"{row['dept']} #{n}" if lang == "en" else f"{row['dept']} {n}号")
    return rows, {"ok": ok, "bots": len(bots), "n": len(rows), "asks": sum(1 for r in rows if r.get("ask"))}


def bots_overview(*, home=None, now=None):
    """R128 #4: ⚙ の Bot パネルの一覧＝{ok: 台帳が読めたか, max: 登録の上限, bots: [...]}。読むだけ（ロックも書き込みもしない）。
    bots の 1 件= {bot, session, name, role, added, lastSeen（最後に動いた時刻・0 は一度も）, ask（開いている相談があるか）,
    unread（未読の数）, muted（消音）}。相談の文・未読の中身・記録は載せない（一覧は数と有無だけ）。"""
    home, now = _home(home), _now(now)
    bots, ok = _read_ledger(home)
    out = []
    for bot_id, entry in bots.items():
        st, _broken = _read_state(state_path(bot_id, home), now)
        ck = st["checkin"]
        out.append({"bot": bot_id, "session": session_of(bot_id), "name": entry["name"], "role": entry["role"],
                    "added": entry["added"], "lastSeen": max([st["seen"]] + ([ck["ts"]] if ck else [])),
                    "ask": bool(st["ask"] and now < st["ask"]["deadline"]), "unread": len(st["inbox"]),
                    "muted": entry["muted"]})
    return {"ok": ok, "max": BOTS_MAX, "bots": out}


def list_bots(*, home=None, now=None):
    """登録した Bot の一覧（名前・役・最後に動いた時刻・開いている相談・未読の数・消音）。CLI の list と同じ行（bots_overview）。"""
    return bots_overview(home=home, now=now)["bots"]


# ── CLI ───────────────────────────────────────────────
def _cli_lang():
    raw = (os.environ.get("OFFICE_LANG") or "").strip().lower()
    if raw in ("ja", "en"):
        return raw
    loc = os.environ.get("LC_ALL") or os.environ.get("LC_MESSAGES") or os.environ.get("LANG") or ""
    return "en" if loc.strip().lower().startswith("en") else "ja"


def main(argv=None):
    ap = argparse.ArgumentParser(prog="office_door.py", description="AI Office の出勤の口（R126・この Mac だけ）")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("add", help="Bot を登録する")
    p.add_argument("bot")
    p.add_argument("--name")
    p.add_argument("--role")
    sub.add_parser("list", help="登録した Bot の一覧")
    p = sub.add_parser("remove", help="登録を外す（状態と記録も消す）")
    p.add_argument("bot")
    p = sub.add_parser("checkin", help="出勤")
    p.add_argument("--bot", required=True)
    p.add_argument("--doing", required=True)
    p.add_argument("--state", default="working")
    p.add_argument("--ttl-min", type=int, default=TTL_MIN_DEFAULT)
    p.add_argument("--req-id")
    p = sub.add_parser("report", help="終わったことの記録")
    p.add_argument("--bot", required=True)
    p.add_argument("--title", required=True)
    p.add_argument("--body", default="")
    p.add_argument("--kind", default="done")
    p.add_argument("--req-id")
    p = sub.add_parser("ask", help="人に相談する")
    p.add_argument("--bot", required=True)
    p.add_argument("--question", required=True)
    p.add_argument("--option", action="append", default=[])
    p.add_argument("--ttl-hours", type=int, default=TTL_HOURS_DEFAULT)
    p.add_argument("--req-id")
    p = sub.add_parser("inbox", help="未読を覗く（--ack で既読にする）")
    p.add_argument("--bot", required=True)
    p.add_argument("--ack", nargs="+", action="extend")
    p = sub.add_parser("withdraw", help="相談を取り下げる")
    p.add_argument("--bot", required=True)
    p.add_argument("--ask-id", required=True)
    p.add_argument("--req-id")
    a = ap.parse_args(argv)
    lang = _cli_lang()
    if a.cmd == "add":
        res = add_bot(a.bot, a.name, a.role, lang=lang)
        if res.get("ok"):
            res["mcp"] = mcp_command(res["bot"])        # R128 #4: 次の手（その仕事のフォルダで MCP を登録する 1 行）
    elif a.cmd == "list":
        res = {"ok": True, "bots": list_bots()}
    elif a.cmd == "remove":
        res = remove_bot(a.bot, lang=lang)
    elif a.cmd == "checkin":
        res = checkin(a.bot, a.doing, a.state, a.ttl_min, req_id=a.req_id, lang=lang)
    elif a.cmd == "report":
        res = report(a.bot, a.title, a.body, a.kind, req_id=a.req_id, lang=lang)
    elif a.cmd == "ask":
        res = ask(a.bot, a.question, a.option, a.ttl_hours, req_id=a.req_id, lang=lang)
    elif a.cmd == "inbox":
        res = inbox(a.bot, a.ack, lang=lang)
    else:
        res = withdraw(a.bot, a.ask_id, req_id=a.req_id, lang=lang)
    sys.stdout.write(json.dumps(res, ensure_ascii=False) + "\n")
    return 0 if res.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
