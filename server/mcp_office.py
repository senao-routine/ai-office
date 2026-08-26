#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""AI Office MCPサーバー（P5） — 標準ライブラリのみ・stdio JSON-RPC を手書き実装。

他の Claude Code セッション（や OpenClaw）が MCP ツール2つで AI Office を操作する:
  - office_status   : Mac上の全セッション（AI社員）の出勤状況を要約
  - office_instruct : 指定セッションへ指示を投函（既存 post_instruction を再利用）

不変条件（掟）:
  * ネットワークを一切開かない（stdio のみ・:4780 サーバーの生死と独立）。
  * **stdout には JSON-RPC メッセージ以外を1バイトも書かない**。ログ・デバッグは全て stderr。
  * トランスクリプトは読み取り専用（office_json）。書き込みは post_instruction 1本だけ。
  * メッセージは1行=1 JSON（改行区切り・埋め込み改行なし）。EOF で exit 0。
仕様: modelcontextprotocol.io（stdio transport / lifecycle / server-tools）。

登録（ユーザー手順・自走で実行しない）:
  claude mcp add --scope user aioffice -- <python3実体> "<...>/AI Office/server/mcp_office.py"
"""
import json
import os
import re
import sys
import traceback
import unicodedata
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:          # exec_module 反復でも sys.path に重複挿入しない
    sys.path.insert(0, str(HERE))
import office_server as office   # 同 server/・標準ライブラリのみ（office_json / post_instruction）

# 対応プロトコル版。クライアント提示が集合内なら鸚鵡返し（MUST）、集合外は LATEST を返す。
SUPPORTED = {"2024-11-05", "2025-03-26", "2025-06-18", "2025-11-25"}
LATEST = "2025-11-25"
MAX_LINE = 10 * 1024 * 1024          # 巨大行ガード（10MB）
MAX_EMPLOYEES = 30                   # status 要約の打切り（10kトークン警告回避）
SERVER_INFO = {"name": "aioffice", "title": "AI Office", "version": "1.0.0"}

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
]


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
    emps = office.office_json().get("employees", [])
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


def _tool_instruct(args):
    """(text, is_error) を返す。"""
    if not isinstance(args, dict):
        return "session と text（文字列）が必要です", True
    session_q = args.get("session")
    text = args.get("text")
    if not isinstance(session_q, str) or not isinstance(text, str):
        return "session と text（文字列）が必要です", True
    sid, note = _resolve_session(session_q)
    if sid is None:
        if note.startswith("AMBIGUOUS:"):
            return ("宛先が複数見つかりました。session を全文で指定してください:\n"
                    + note[len("AMBIGUOUS:"):].lstrip("\n"), True)
        return "宛先が見つかりません。office_status で session を確認してください", True
    ok, msg = office.post_instruction(sid, text)
    if not ok:
        return f"投函できませんでした: {msg}", True
    disp = ""
    for e in office.office_json().get("employees", []):
        if e.get("session") == sid:
            disp = e.get("disp", "")
            break
    return f"📨 {disp or sid} へ投函しました（待機中なら数秒で配達）{note}", False


def _on_tools_call(mid, params):
    name = params.get("name")
    args = params.get("arguments") or {}
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
    return _result(mid, {
        "protocolVersion": version,
        "capabilities": {"tools": {}},
        "serverInfo": SERVER_INFO,
        "instructions": ("office_status でMac上の全Claude Codeセッションを確認し、"
                         "office_instruct で指示を投函できる。❗付き社員は人の対応待ち。"),
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
        return _result(mid, {"tools": TOOLS})
    if method == "tools/call":
        return _on_tools_call(mid, params)
    return _err(mid, -32601, f"Method not found: {method}")


def _drain_to_newline():
    while True:
        chunk = sys.stdin.buffer.readline(MAX_LINE)
        if chunk == b"" or chunk.endswith(b"\n"):
            return


def main():
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
