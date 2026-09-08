# -*- coding: utf-8 -*-
"""Codex CLI の SQLite メタデータを employee へ変換するアダプタ。

DB 取得と純粋な変換を分離し、会話本文や rollout は読み取らない。
残留した inProgress は開始時刻で失効させ、子は親の minions にだけ数える。
"""
import math
import re
import sqlite3
import time
from contextlib import closing
from pathlib import Path

STATE_FILE = ".codex/state_5.sqlite"
HISTORY_FILE = ".codex/thread_history_1.sqlite"
SHOW_WINDOW = 3 * 3600
# `codex exec`（委譲・レビューの一発仕事）は終わったらオフィスに居ない。実測 2026-09-07:
# 直近3時間の thread 91 件のうち 77 件が exec で、最終更新の中央値が 86 分前だった＝そのまま出すと
# オフィスが「終わった仕事の幽霊」で埋まる。対話セッション（cli/vscode）は 3 時間のまま。
EXEC_WINDOW = 30 * 60
TURN_ALIVE = 1800
VERB = {
    "commandExecution": ("実行中", "running"),
    "fileChange": ("編集中", "editing"),
    "agentMessage": ("報告中", "reporting"),
    "reasoning": ("考え中…", "thinking"),
    "mcpToolCall": ("スキル実行中", "using a tool"),
    "imageGeneration": ("画像を生成中", "generating image"),
}
# office の表示用・指示用の両規則を満たす。ID は加工して衝突させない。
_SESSION_RE = re.compile(r"[A-Za-z0-9-]{8,64}")


def _text(value):
    return value if isinstance(value, str) else ""


def _number(value):
    try:
        number = float(value)
        return number if math.isfinite(number) else 0.0
    except (TypeError, ValueError, OverflowError):
        return 0.0


def _tokens(value):
    try:
        return int(value or 0)
    except (TypeError, ValueError, OverflowError):
        return 0


def _prepare(db, trace):
    db.row_factory = sqlite3.Row
    if trace is not None:
        db.set_trace_callback(trace)
    db.execute("PRAGMA query_only=1")
    db.execute("BEGIN")


def read_rows(home, now=None, trace=None):
    """非本文列だけを読む。trace は両接続の set_trace_callback に渡す。

    threads / edges は dict のリスト、turns / last_item_type は thread id を
    キーとする辞書。now を省略すると現在時刻を使う。読み取りは各 DB 内で
    同じスナップショットを使い、WAL の確定済みデータも参照する。
    """
    now = time.time() if now is None else now
    state = Path(home) / STATE_FILE
    history = Path(home) / HISTORY_FILE
    if not state.is_file() or not history.is_file():
        return None, {"connected": False, "reason": "missing"}
    try:
        # as_uri はパス中の空白・?・# もエスケープする。immutable は使わない。
        with closing(sqlite3.connect(state.resolve().as_uri() + "?mode=ro",
                                     uri=True, timeout=0.2)) as db:
            _prepare(db, trace)
            threads = [dict(row) for row in db.execute("""
                SELECT id, updated_at, source, cwd, git_branch, tokens_used,
                       archived, agent_nickname, name
                FROM threads
                WHERE archived = 0 AND updated_at >= ?
                ORDER BY id
            """, (now - SHOW_WINDOW,))]
            edges = [dict(row) for row in db.execute("""
                SELECT parent_thread_id, child_thread_id, status
                FROM thread_spawn_edges
            """)]
        turns = {}
        last_item_type = {}
        with closing(sqlite3.connect(history.resolve().as_uri() + "?mode=ro",
                                     uri=True, timeout=0.2)) as db:
            _prepare(db, trace)
            for thread in threads:
                thread_id = thread["id"]
                turn = db.execute("""
                    SELECT thread_id, turn_id, rollout_ordinal, status,
                           started_at, completed_at
                    FROM thread_turns
                    WHERE thread_id = ?
                    ORDER BY rollout_ordinal DESC, turn_id DESC
                    LIMIT 1
                """, (thread_id,)).fetchone()
                if turn is None:
                    continue
                turns[thread_id] = dict(turn)
                item = db.execute("""
                    SELECT item_type
                    FROM thread_items
                    WHERE thread_id = ? AND turn_id = ?
                    ORDER BY created_at_ms DESC, rollout_ordinal DESC, item_id DESC
                    LIMIT 1
                """, (thread_id, turn["turn_id"])).fetchone()
                if item is not None:
                    last_item_type[thread_id] = item["item_type"]
    except (sqlite3.DatabaseError, OSError):
        # OperationalError（ロック・列欠損）に加え、壊れた DB も未接続扱い。
        return None, {"connected": False, "reason": "locked-or-schema"}
    return {"threads": threads, "edges": edges, "turns": turns,
            "last_item_type": last_item_type}, {"connected": True}


def parse_codex(threads, edges, turns, last_item_type, now, lang="ja"):
    """read_rows の行を変換する純関数。入力を変更せず、IO もしない。

    children は表示した親に紐づく open な子の合計。子自身の表示窓とは
    独立して edge を数え、同じ親子の重複は数えない。
    """
    children = {}
    for edge in edges:
        if edge.get("status") == "open":
            parent = _text(edge.get("parent_thread_id"))
            child = _text(edge.get("child_thread_id"))
            if parent and child:
                children.setdefault(parent, set()).add(child)
    employees = []
    for thread in threads:
        thread_id = _text(thread.get("id"))
        session = "cx-" + thread_id
        source = _text(thread.get("source"))
        updated = _number(thread.get("updated_at"))
        window = EXEC_WINDOW if source == "exec" else SHOW_WINDOW
        if (thread.get("archived", 0) != 0 or updated < now - window
                or source.startswith("{") or not _SESSION_RE.fullmatch(session)):
            continue
        turn = turns.get(thread_id) or {}
        started = _number(turn.get("started_at"))
        completed = _number(turn.get("completed_at"))
        state = "resting"
        if turn.get("status") == "inProgress" and started > 0 and now - started < TURN_ALIVE:
            state = "working"
        elif (turn.get("status") == "completed" and completed > 0
              and now - completed < TURN_ALIVE and source != "exec"):
            # 2026-09-08: **`exec` はターンが終わったらプロセスごと消えている**。
            # `waiting`（指示待ち）にすると `listening=True` になり、オフィスが
            # 「ここに指示を送れます」と言うが `codex queue` は届かない＝嘘になる。
            # 実測: 監査ワークフローを1本回しただけで、終わった exec が 52 体
            # 「指示待ち」で並び、対話セッションが埋もれた。
            # 対話（cli/vscode）は人が座っているので waiting のままでよい。
            state = "waiting"
        verb = VERB.get(last_item_type.get(thread_id), ("作業中", "working"))[lang == "en"]
        name = _text(thread.get("name"))
        employees.append({
            "session": session,
            "cwd": _text(thread.get("cwd")),
            "branch": _text(thread.get("git_branch")),
            "title": name,
            "age": int(now - max(updated, started, completed)),
            "mtime": updated,
            "state": state,
            "kind": {"working": "tool", "waiting": "said", "resting": "idle"}[state],
            "verb": verb,
            "target": "",
            "feed": [verb],
            "skills": [],
            "minions": len(children.get(thread_id, ())),
            "pending": False,
            "listening": state != "resting",
            "lastSaid": "",
            "lastOrder": "",
            "question": "",
            "approvalMin": 0,
            "stuckTool": "",
            "dept": name or _text(thread.get("agent_nickname")) or "Codex",
            "role": source,
            "vendor": "codex",
            "tokens": _tokens(thread.get("tokens_used")),
        })
    return employees, {"connected": True, "n": len(employees),
                       "children": sum(employee["minions"] for employee in employees)}


def codex_employees(home, now, lang="ja"):
    """指定 HOME のメタデータを employee へ変換する（config 判定は呼出側）。"""
    rows, meta = read_rows(home, now=now)
    if rows is None:
        return [], meta
    return parse_codex(**rows, now=now, lang=lang)
