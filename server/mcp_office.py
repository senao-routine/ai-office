#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""AI Office MCPサーバー（P5） — 標準ライブラリのみ・stdio JSON-RPC を手書き実装。

他の Claude Code セッション（や OpenClaw）が MCP ツール3つで AI Office を操作する:
  - office_status   : Mac上の全セッション（AI社員）の出勤状況を要約
  - office_instruct : 指定セッションへ指示を投函（既存 post_instruction を再利用）
  - office_digest   : 指定日の実績を数値と表示名だけで要約

R126 出勤の口（`--bot <id>` で起動したとき・正本= docs/door.md）: 道具は次の 5 つ**だけ**（上の 3 つは出さない＝
Bot からセッションへ指示もしないし、セッションの様子も読まない）。どの Bot かは起動の引数で決まる（道具の引数では名乗れない）。
  - office_checkin / office_report / office_ask / office_inbox / office_withdraw（中身は server/office_door.py）

不変条件（掟）:
  * ネットワークを一切開かない（stdio のみ・:4780 サーバーの生死と独立）。
  * **stdout には JSON-RPC メッセージ以外を1バイトも書かない**。ログ・デバッグは全て stderr。
  * トランスクリプトは読み取り専用（office_json）。書き込みは post_instruction 1本だけ（--bot のときは office_door の口だけ）。
  * メッセージは1行=1 JSON（改行区切り・埋め込み改行なし）。EOF で exit 0。
  * R126: office_instruct は Bot（bot-）宛を断る・office_status は Bot の行を名前と状態だけにする（Bot の文をセッションのプロンプトに入れない）。
仕様: modelcontextprotocol.io（stdio transport / lifecycle / server-tools）。

登録（ユーザー手順・自走で実行しない）:
  claude mcp add --scope user aioffice -- <python3実体> "<...>/AI Office/server/mcp_office.py"
  Bot 用は**その仕事の設定にだけ**足す（--scope user で全セッションに配らない）: … mcp_office.py --bot <id>
"""
import json
import os
import re
import sys
import traceback
import unicodedata
from datetime import date
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:          # exec_module 反復でも sys.path に重複挿入しない
    sys.path.insert(0, str(HERE))
import office_server as office   # 同 server/・標準ライブラリのみ（office_json / post_instruction）
import office_door               # R126: 出勤の口（--bot のときの道具 5 つの中身）

# 対応プロトコル版。クライアント提示が集合内なら鸚鵡返し（MUST）、集合外は LATEST を返す。
SUPPORTED = {"2024-11-05", "2025-03-26", "2025-06-18", "2025-11-25"}
LATEST = "2025-11-25"
MAX_LINE = 10 * 1024 * 1024          # 巨大行ガード（10MB）
MAX_EMPLOYEES = 30                   # status 要約の打切り（10kトークン警告回避）
try:
    from office_version import VERSION as _APP_VERSION
except ImportError:      # 直接ファイルを読む経路（sys.path が通っていない）
    import pathlib as _pl, re as _re
    _m = _re.search(r'VERSION = "([^"]+)"',
                    (_pl.Path(__file__).resolve().parent / "office_version.py").read_text(encoding="utf-8"))
    _APP_VERSION = _m.group(1) if _m else "0.0.0"
# R97-D: 版の正本は server/office_version.py（ここに数字を書かない）
SERVER_INFO = {"name": "aioffice", "title": "AI Office", "version": _APP_VERSION}

TOOLS = [
    {"name": "office_status",
     "description": ("Mac上の全Claude Codeセッション（AI社員）の出勤状況を要約して返す。"
                     "❗付き社員は人の対応待ち（承認/質問/配達待ち）。指示を出すには "
                     "office_instruct へ session=... を全文コピーする。"),
     "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False}},
    {"name": "office_instruct",
     "description": ("指定セッション（AI社員）へ指示を投函する。待機中なら数秒・作業中はターン終了"
                     "直後に配達。閉じたセッション宛は次回オープン時に配達。自セッション宛も可能。"),
     "inputSchema": {"type": "object",
                     "properties": {
                         "session": {"type": "string", "minLength": 1,
                                     "description": "宛先セッションID（office_statusのsession=を全文コピー推奨。一意なら前方一致や表示名でも可）"},
                         "text": {"type": "string", "maxLength": 4000, "description": "指示本文"}},
                     "required": ["session", "text"], "additionalProperties": False}},
    {"name": "office_digest",
     "description": "指定日のオフィス実績を数値と表示名だけで要約する。質問・指示・会話本文は含まない。",
     "inputSchema": {"type": "object", "properties": {
         "day": {"type": "string", "pattern": r"^\d{4}-\d{2}-\d{2}$",
                 "description": "ローカル日付 YYYY-MM-DD（省略時は今日）"}},
         "additionalProperties": False}},
]

# R126 出勤の口（--bot <id> のときだけ・この 5 つだけ）。上限の正本は server/office_door.py（docs/door.md）
BOT = None        # 起動の引数 --bot <id>（どの Bot かは起動の設定で決まる＝道具の引数では名乗れない）
DOOR_TOOLS = [
    {"name": "office_checkin",
     "description": ("出勤を記録する（AI Office の部屋に座る）。ttl_min 分は作業中、その後 3 時間は休憩として見える。"
                     "届いている答え・指示の数を返す。前と同じ内容なら書かない（期限の半分を過ぎたら延ばす）。"),
     "inputSchema": {"type": "object", "properties": {
         "doing": {"type": "string", "minLength": 1, "maxLength": office_door.DOING_LEN,
                   "description": f"いまやっていること（{office_door.DOING_LEN} 字まで）"},
         "state": {"type": "string", "enum": list(office_door.STATES), "description": "既定 working"},
         "ttl_min": {"type": "integer", "minimum": office_door.TTL_MIN_RANGE[0],
                     "maximum": office_door.TTL_MIN_RANGE[1],
                     "description": f"出勤が続く分（既定 {office_door.TTL_MIN_DEFAULT}）"}},
         "required": ["doing"], "additionalProperties": False}},
    {"name": "office_report",
     "description": "終わったことを記録する（Bot の記録に残る・人を呼ばない。気づいてほしいことは office_ask）。",
     "inputSchema": {"type": "object", "properties": {
         "title": {"type": "string", "minLength": 1, "maxLength": office_door.TITLE_LEN,
                   "description": f"題（{office_door.TITLE_LEN} 字まで）"},
         "body": {"type": "string", "maxLength": office_door.BODY_LEN,
                  "description": f"本文（{office_door.BODY_LEN} 字まで・省略可）"},
         "kind": {"type": "string", "enum": list(office_door.KINDS), "description": "既定 done"}},
         "required": ["title"], "additionalProperties": False}},
    {"name": "office_ask",
     "description": ("人に相談する（Mac のあなたの番に並ぶ）。開いている相談は 1 件まで・1 時間 6 件／1 日 20 件まで。"
                     "askId を返す。答えは office_inbox に届く（選択肢に当たれば choice{index,label} つき）。"),
     "inputSchema": {"type": "object", "properties": {
         "question": {"type": "string", "minLength": 1, "maxLength": office_door.ASK_Q_LEN,
                      "description": f"問い（{office_door.ASK_Q_LEN} 字まで）"},
         "options": {"type": "array", "maxItems": office_door.ASK_OPT_MAX,
                     "items": {"type": "string", "minLength": 1, "maxLength": office_door.ASK_OPT_LEN},
                     "description": f"選択肢（{office_door.ASK_OPT_LEN} 字 × {office_door.ASK_OPT_MAX} まで・省略可）"},
         "ttl_hours": {"type": "integer", "minimum": office_door.TTL_HOURS_RANGE[0],
                       "maximum": office_door.TTL_HOURS_RANGE[1],
                       "description": f"答えを待つ時間（既定 {office_door.TTL_HOURS_DEFAULT}）"}},
         "required": ["question"], "additionalProperties": False}},
    {"name": "office_inbox",
     "description": ("届いている答え・指示・期限切れの知らせを覗く（消さない）。読んだものは ack に id を渡して消す。"
                     "種類= answer（text・choice）／instruct（text）／expired／rejected。"),
     "inputSchema": {"type": "object", "properties": {
         "ack": {"type": "array", "items": {"type": "string"}, "description": "読んだ id（省略可）"}},
         "additionalProperties": False}},
    {"name": "office_withdraw",
     "description": "相談を取り下げる（ほかの窓口で先に答えが出たとき）。",
     "inputSchema": {"type": "object", "properties": {
         "askId": {"type": "string", "pattern": r"^[0-9a-f]{32}$", "description": "office_ask が返した askId"}},
         "required": ["askId"], "additionalProperties": False}},
]
DOOR_NAMES = frozenset(t["name"] for t in DOOR_TOOLS)


def _tools():
    """tools/list の中身。--bot のときは出勤の口の 5 つだけ（いつもの 3 つは出さない）。"""
    return DOOR_TOOLS if BOT else TOOLS


def _is_bot(e):
    """R126: 出勤の口の Bot の行（vendor "bot" か session "bot-…"）。"""
    return e.get("vendor") == "bot" or str(e.get("session") or "").startswith(office_door.SESSION_PREFIX)


# ---- stdout に書く唯一の関数（バイナリ層直書き＝C localeでも UnicodeEncodeError で死なない） ----
def _send(obj):
    sys.stdout.buffer.write(
        json.dumps(obj, ensure_ascii=False, separators=(",", ":")).encode("utf-8") + b"\n")
    sys.stdout.buffer.flush()   # 毎回flush。忘れるとクライアントが永久ハングする


def _log(*a):
    print(*a, file=sys.stderr, flush=True)


def _result(mid, result):
    return {"jsonrpc": "2.0", "id": mid, "result": result}


def _err(mid, code, message):
    return {"jsonrpc": "2.0", "id": mid, "error": {"code": code, "message": message}}


def _text(mid, text, is_error=False):
    """tools/call の応答（引数不備・実行失敗は Protocol Error でなく isError:true 側で返す＝SEP-1303）"""
    return _result(mid, {"content": [{"type": "text", "text": text}], "isError": is_error})


def _adopt_p4_data():
    """P4常駐インストール済みなら daemon/officectl と同じ data/（config+assets）を読む＝
    表示名(disp)が repo config と分岐しない。main() からのみ呼ぶ（import時に環境を触ると
    テストプロセスへ漏れる）。明示 OFFICE_DATA が最優先。relay_agent 版は stdout print の
    ため流用せず stderr 版を自前実装する。"""
    # OFFICE_HOME はテスト注入口＝これが在るときは本番P4データを読まない（テストの密閉性）
    if os.environ.get("OFFICE_DATA") or os.environ.get("OFFICE_HOME"):
        return
    d = Path.home() / "Library" / "Application Support" / "AIOffice" / "data"
    if d.is_dir():
        os.environ["OFFICE_DATA"] = str(d)
        office.DATA = d
        office.ASSETS = d / "assets"
        _log(f"data: {d} (P4常駐と共有)")


# ---- ツール実装 ----
def _emoji(state):
    return {"working": "🔨", "waiting": "⏳", "resting": "💤"}.get(state, "・")


def _alert(e):
    return bool(e.get("approvalMin", 0) > 0 or e.get("question") or e.get("pending"))


def _disp(e):
    """表示名。R85-1: /rename のセッション名（title）があればそれを優先。"""
    return e.get("title") or e.get("disp", "?")


def _tool_status():
    d = office.office_json()
    emps = d.get("employees", [])
    c = d.get("counts", {})
    alerts = sum(1 for e in emps if _alert(e))
    lines = [f"🏢 出勤 {len(emps)}（作業中{c.get('working', 0)}/待機{c.get('waiting', 0)}/"
             f"休憩{c.get('resting', 0)}・❗要対応{alerts}）"]
    shown = sorted(emps, key=lambda e: not _alert(e))[:MAX_EMPLOYEES]   # ❗を先頭へ（安定ソート）＝打切りで隠れない
    for e in shown:
        head = "❗" if _alert(e) else " "
        if _is_bot(e):
            # R126: Bot の行は名前と状態だけ（doing・相談の文は Bot が書いた文＝セッションのプロンプトに入れない。
            # session も出さない＝office_instruct の宛先にならない）
            lines.append(f"{head}[{_emoji(e.get('state'))}] {_disp(e)}（Bot）")
            continue
        why = ""
        if e.get("question"):
            why = " ❓質問あり"
        elif e.get("approvalMin", 0) > 0:
            why = f" 承認待ち{e['approvalMin']}分"
        elif e.get("pending"):
            why = " 📨配達待ち"
        verb = (e.get("verb", "") + " " + (e.get("target") or "")).strip()
        role = f"／{e['role']}" if e.get("role") else ""
        cwd = Path(e.get("cwd") or "").name
        lines.append(f"{head}[{_emoji(e.get('state'))}] {_disp(e)}{role} {verb}{why}"
                     f" — session={e.get('session', '')} branch={e.get('branch') or '-'} …{cwd}")
    if len(emps) > MAX_EMPLOYEES:
        lines.append(f"…他{len(emps) - MAX_EMPLOYEES}人（全件は http://127.0.0.1:4780 のオフィスUIで）")
    return "\n".join(lines)


def _tool_digest(args):
    if not isinstance(args, dict) or set(args) - {"day"}:
        return "day（YYYY-MM-DD、省略可）を指定してください", True
    day = args.get("day", date.today().isoformat())
    if not isinstance(day, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", day):
        return "day は YYYY-MM-DD 形式で指定してください", True
    try:
        date.fromisoformat(day)
    except ValueError:
        return "day は実在する日付を指定してください", True
    digest = office._notification_digest(day)
    if digest is None:
        stats = office._load_daily_stats(day)
        answered = stats.get("answered") or 0
        wait = (stats.get("medianWaitSec") if "medianWaitSec" in stats else
                (stats.get("totalWaitSec") or 0) / answered if answered else 0)
        label = "中央値" if "medianWaitSec" in stats else "平均"
        return f"🏢 {day} — ❗{answered}件に答えた（{label} {round(wait / 60)}分）", False

    def metrics(row):
        # Explicit numeric fields only: never serialize a digest/office object.
        return (f"❗{row.get('asksAnswered', 0)}件に答えた"
                f"（中央値 {round(row.get('medianWaitSec', 0) / 60)}分）"
                f" · ✅{row.get('tasksDone', 0)}件 · 稼働 {row.get('activeMin', 0):g}分")

    totals = digest["totals"]
    lines = [f"🏢 {day} — {metrics(totals)} · XP {totals.get('xp', 0):g}"]
    sessions = digest.get("sessions") or []
    for row in sessions[:MAX_EMPLOYEES]:
        name = " ".join(str(row.get("name") or "?").split())[:80]
        lines.append(f"{name} — {metrics(row)}")
    if len(sessions) > MAX_EMPLOYEES:
        lines.append(f"…他{len(sessions) - MAX_EMPLOYEES}体")
    return "\n".join(lines), False


def _transcript_exists(session_id):
    """実トランスクリプト(*.jsonl)が存在するか。閉じたセッションは必ず残す＝孤児inbox防止。"""
    try:
        return any(office.PROJECTS.glob(f"*/{session_id}.jsonl"))
    except OSError:
        return False


def _resolve_session(query):
    """(session_id or None, note) を返す。宛先を段階的に解決する。"""
    q = (query or "").strip()
    if not q:
        return None, "NOTFOUND"          # 空/空白のみ→全員前方一致で誤配達するのを防ぐ
    # R126: Bot（出勤の口）はセッションからの宛先にしない（解決の候補にも入れない）
    emps = [e for e in office.office_json().get("employees", []) if not _is_bot(e)]
    # (a) 完全一致
    for e in emps:
        if e.get("session") == q:
            return q, ""
    # (b) 前方一致 or disp/dept への部分一致（NFC casefold）で候補ちょうど1件
    qn = unicodedata.normalize("NFC", q).casefold()
    cands = []
    for e in emps:
        sid = e.get("session", "")
        disp = unicodedata.normalize("NFC", e.get("disp", "")).casefold()
        dept = unicodedata.normalize("NFC", e.get("dept", "")).casefold()
        # R85-1: /rename のセッション名（title）でも宛先解決できるように
        title = unicodedata.normalize("NFC", e.get("title", "")).casefold()
        if sid.startswith(q) or (qn and (qn in disp or qn in dept or qn in title)):
            cands.append(e)
    if len(cands) == 1:
        e = cands[0]
        return e["session"], f'（"{q}" を {_disp(e)} に解決）'
    if len(cands) > 1:
        # disp/title 完全一致がちょうど1件ならそれを採用（同部署複数でも表示名一致は一意）
        exact = [e for e in cands
                 if qn in (unicodedata.normalize("NFC", e.get("disp", "")).casefold(),
                           unicodedata.normalize("NFC", e.get("title", "")).casefold())]
        if len(exact) == 1:
            return exact[0]["session"], f'（"{q}" を {_disp(exact[0])} に解決）'
        listing = "\n".join(f'  {_disp(e)} = {e.get("session", "")}' for e in cands)
        return None, "AMBIGUOUS:\n" + listing
    # (d) 出勤簿に無いが正規形式ID かつ 実トランスクリプトが存在 → 閉じたセッション宛（孤児inbox防止）
    if re.fullmatch(r"[a-zA-Z0-9-]{8,64}", q) and _transcript_exists(q):
        return q, "（出勤簿には無いが実セッション。閉じているなら次回オープン時に配達）"
    # (e)
    return None, "NOTFOUND"


def _names_a_bot(query):
    """R126: セッションの宛先に当たらなかった語が Bot の名前を指していたか（断りの文を分かりやすくするだけ）。"""
    qn = unicodedata.normalize("NFC", (query or "").strip()).casefold()
    if not qn:
        return False
    for e in office.office_json().get("employees", []):
        if _is_bot(e) and any(qn in unicodedata.normalize("NFC", str(e.get(k) or "")).casefold()
                              for k in ("disp", "dept")):
            return True
    return False


def _tool_instruct(args):
    """(text, is_error) を返す。"""
    if not isinstance(args, dict):
        return "session と text（文字列）が必要です", True
    session_q = args.get("session")
    text = args.get("text")
    if not isinstance(session_q, str) or not isinstance(text, str):
        return "session と text（文字列）が必要です", True
    # R126: セッションの文が「人の指示」として Bot に入り、のちに中継で外へ出るのを止める（post_instruction も src で断る）
    bot_refusal = ("Bot（出勤の口）へはセッションから指示できません。Bot への指示は Mac の画面から人が出します", True)
    if session_q.strip().startswith(office_door.SESSION_PREFIX):
        return bot_refusal
    sid, note = _resolve_session(session_q)
    if sid is None:
        if note.startswith("AMBIGUOUS:"):
            return ("宛先が複数見つかりました。session を全文で指定してください:\n"
                    + note[len("AMBIGUOUS:"):].lstrip("\n"), True)
        if _names_a_bot(session_q):
            return bot_refusal
        return "宛先が見つかりません。office_status で session を確認してください", True
    if sid.startswith(office_door.SESSION_PREFIX):
        return bot_refusal
    ok, msg = office.post_instruction(sid, text, src="mcp")
    if not ok:
        return f"投函できませんでした: {msg}", True
    disp = ""
    for e in office.office_json().get("employees", []):
        if e.get("session") == sid:
            disp = e.get("disp", "")
            break
    return f"📨 {disp or sid} へ投函しました（待機中なら数秒で配達）{note}", False


_DOOR_ARGS = {
    "office_checkin": {"doing", "state", "ttl_min"},
    "office_report": {"title", "body", "kind"},
    "office_ask": {"question", "options", "ttl_hours"},
    "office_inbox": {"ack"},
    "office_withdraw": {"askId"},
}


def _door_call(name, args):
    """R126: 出勤の口の道具 1 回（結果は office_door の dict・形の検査も office_door が持つ）。"""
    if not isinstance(args, dict):
        return {"ok": False, "error": "bad-args", "text": "arguments はオブジェクトで渡してください"}
    extra = sorted(str(k) for k in args if k not in _DOOR_ARGS[name])
    if extra:
        return {"ok": False, "error": "bad-args", "text": f"知らない引数: {', '.join(extra)[:120]}"}
    kw = {"home": office._HOME, "lang": office.office_lang()}
    if name == "office_checkin":
        return office_door.checkin(BOT, args.get("doing"), args.get("state", "working"),
                                   args.get("ttl_min", office_door.TTL_MIN_DEFAULT), **kw)
    if name == "office_report":
        return office_door.report(BOT, args.get("title"), args.get("body", ""), args.get("kind", "done"), **kw)
    if name == "office_ask":
        return office_door.ask(BOT, args.get("question"), args.get("options"),
                               args.get("ttl_hours", office_door.TTL_HOURS_DEFAULT), **kw)
    if name == "office_inbox":
        return office_door.inbox(BOT, args.get("ack"), **kw)
    return office_door.withdraw(BOT, args.get("askId"), **kw)


def _on_door_call(mid, params):
    """--bot のときの tools/call。5 つ以外（office_status・office_instruct・office_digest を含む）は Unknown tool。"""
    name = params.get("name")
    if not isinstance(name, str) or name not in DOOR_NAMES:     # 配列などの名前でも TypeError（-32603）にせず -32602
        return _err(mid, -32602, f"Unknown tool: {name}")
    args = params.get("arguments")
    try:
        res = _door_call(name, {} if args is None else args)
    except Exception:
        traceback.print_exc(file=sys.stderr)
        res = {"ok": False, "error": "internal", "text": f"{name} の実行に失敗しました"}
    return _text(mid, json.dumps(res, ensure_ascii=False), not res.get("ok"))


def _on_tools_call(mid, params):
    if BOT:
        return _on_door_call(mid, params)
    name = params.get("name")
    args = params.get("arguments") or {}
    if name == "office_digest":
        try:
            text, is_err = _tool_digest(params.get("arguments", {}))
            return _text(mid, text, is_err)
        except Exception:
            traceback.print_exc(file=sys.stderr)
            return _text(mid, "office_digest の生成に失敗しました", True)
    if name == "office_status":
        try:
            return _text(mid, _tool_status())
        except Exception:
            traceback.print_exc(file=sys.stderr)
            return _text(mid, "office_status の生成に失敗しました", True)
    if name == "office_instruct":
        try:
            text, is_err = _tool_instruct(args)
            return _text(mid, text, is_err)
        except Exception:
            traceback.print_exc(file=sys.stderr)
            return _text(mid, "office_instruct の実行に失敗しました", True)
    return _err(mid, -32602, f"Unknown tool: {name}")


def _on_initialize(mid, params):
    pv = params.get("protocolVersion")
    version = pv if isinstance(pv, str) and pv in SUPPORTED else LATEST   # 非hashable pv の TypeError も封じる
    if BOT:
        instructions = (f"AI Office の出勤の口（Bot「{BOT}」）。office_checkin で出勤、office_report で終わったことの記録、"
                        "office_ask で人に相談（答えは office_inbox に届く・読んだら ack）、office_withdraw で相談の取り下げ。")
    else:
        instructions = ("office_status でMac上の全Claude Codeセッションを確認し、"
                        "office_instruct で指示を投函、office_digest で日別実績を確認できる。"
                        "❗付き社員は人の対応待ち。")
    return _result(mid, {
        "protocolVersion": version,
        "capabilities": {"tools": {}},
        "serverInfo": SERVER_INFO,
        "instructions": instructions,
    })


def _handle(msg):
    """応答 dict を返す。notification（id無し）なら None。"""
    # レスポンス形（result/error を持ち method 無し）＝こちらへの応答なので黙殺（仕様: 応答に応答しない）
    if isinstance(msg, dict) and "method" not in msg and ("result" in msg or "error" in msg):
        return None
    if not isinstance(msg, dict) or not isinstance(msg.get("method"), str):
        if isinstance(msg, dict) and "id" in msg:
            return _err(msg["id"], -32600, "Invalid Request")
        if not isinstance(msg, dict):     # 非オブジェクトの正JSON（"hi"・42 等）は Invalid Request(id:null)
            return _err(None, -32600, "Invalid Request")
        return None                        # id無しの通知dictは黙殺
    if "id" not in msg:            # 通知（initialized/cancelled 等）には応答しない（truthiness禁止＝id:0対応）
        return None
    mid = msg["id"]               # 数値/文字列の型そのまま鸚鵡返し
    method = msg["method"]
    params = msg.get("params") or {}   # params 欠落に耐える
    if not isinstance(params, dict):   # 配列/文字列 params → AttributeError で即死せず -32602
        return _err(mid, -32602, "Invalid params: expected object")
    if method == "initialize":
        return _on_initialize(mid, params)
    if method == "ping":
        return _result(mid, {})
    if method == "tools/list":
        return _result(mid, {"tools": _tools()})
    if method == "tools/call":
        return _on_tools_call(mid, params)
    return _err(mid, -32601, f"Method not found: {method}")


def _drain_to_newline():
    while True:
        chunk = sys.stdin.buffer.readline(MAX_LINE)
        if chunk == b"" or chunk.endswith(b"\n"):
            return


def _parse_argv(argv):
    """引数は `--bot <id>`（`--bot=<id>`）だけ。→ Bot の id か None。それ以外の引数・不正な id は ValueError。"""
    bot = None
    i = 0
    while i < len(argv):
        a = argv[i]
        if a == "--bot" and i + 1 < len(argv):
            bot, i = argv[i + 1], i + 2
        elif a.startswith("--bot="):
            bot, i = a[len("--bot="):], i + 1
        else:
            raise ValueError(f"知らない引数: {a[:40]}（使えるのは --bot <id> だけ）")
    if bot is not None and not office_door.valid_id(bot):
        raise ValueError("--bot の id は英小文字・数字・- の 4〜32 字（先頭は英数字）")
    return bot


def main():
    global BOT
    try:
        BOT = _parse_argv(sys.argv[1:])
    except ValueError as e:
        _log(f"mcp_office: {e}")
        sys.exit(2)
    _adopt_p4_data()
    while True:
        line = sys.stdin.buffer.readline(MAX_LINE)
        if line == b"":                       # EOF = shutdown → exit 0
            return
        if len(line) == MAX_LINE and not line.endswith(b"\n"):
            _drain_to_newline()               # 巨大行 → 改行まで読み捨てて継続
            _send(_err(None, -32700, "Parse error: line too long"))
            continue
        if not line.strip():
            continue
        try:
            msg = json.loads(line)
        except ValueError:
            _send(_err(None, -32700, "Parse error"))
            continue
        try:
            if isinstance(msg, list):        # JSON-RPCバッチ（2025-03-26でMUST）
                if not msg:
                    _send(_err(None, -32600, "Invalid Request"))
                    continue
                replies = [r for m in msg if (r := _handle(m)) is not None]
                if replies:
                    _send(replies)           # まとめて1行のJSON配列で返す
                continue
            resp = _handle(msg)
        except Exception:                    # 予期せぬ例外でもプロセスを殺さない（終了はEOFのみ）
            traceback.print_exc(file=sys.stderr)
            resp = _err(msg.get("id") if isinstance(msg, dict) and "id" in msg else None,
                        -32603, "Internal error")
        if resp is not None:
            _send(resp)


if __name__ == "__main__":
    main()
