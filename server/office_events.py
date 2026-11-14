"""Tail local hook events and expose recent activity to polling and SSE clients."""
from collections import deque
from copy import deepcopy
from datetime import date, timedelta
import json
import math
from pathlib import Path
import queue
import re
import sys
import threading
import time
from typing import Optional   # 素の Mac の python3（3.9）は注釈を実行時に評価する＝`X | None` は使えない

from office_common import IncrementalTail

EVENTS_DIR = ".claude/office_events"
RING = 500
STATE_TTL = 60.0
WORKING_TTL = 25.0
DEBOUNCE = 0.3

_INVALIDATE = frozenset({
    "Stop", "Notification", "PermissionRequest", "UserPromptSubmit",
    "SessionStart", "SessionEnd", "TaskCompleted", "SubagentStart", "SubagentStop",
})
_EVENTS = _INVALIDATE | frozenset({
    "PreToolUse", "PostToolUse", "PostToolUseFailure", "PermissionDenied",
    "StopFailure", "TaskCreated", "PreCompact", "PostCompact",
})
_WAITING_NOTIFICATIONS = frozenset({
    "permission_prompt", "idle_prompt", "agent_needs_input",
})
# R102-4 ターンの開閉（本人「作業中なのに指示待ちと出る」の根治）。transcript は道具の実行中・長い考えの間は止まって
# 見える（27 分の検査を回すセッションが 4 分で「指示待ち」に化けた）＝フックのイベントで「ターンが開いているか」を追う。
#   開く: UserPromptSubmit・本体（ag=0）の道具（Stop フックの配達で続きを始めたターンは UserPromptSubmit を出さない）
#   閉じる: Stop・StopFailure・SessionEnd・SessionStart
#   保留: 許可のダイアログ（PermissionRequest・permission_prompt）＝人を待っている＝作業中と言わない
#   中立: サブエージェント（ag=1）の道具・Subagent*・Task*・Compact＝開いているターンの時刻だけ進める（裏のサブエージェントで
#         閉じたターンを開き直さない）
TOOL_TTL = 1800.0      # 本体の道具が走っている間（Bash は前面 10 分まで・Task/MCP は長い）
TURN_TTL = 600.0       # 道具の外（考え・出力）で最後のイベントから
ERR_TTL = 3 * 3600.0   # StopFailure（API エラーでターンが落ちた）を覚えておく長さ
# 許可の保留を「答えが出た」と読むまでの猶予。掲示は ASK_GRACE（3 秒）の間は見えない＝それより十分長く待つ
# （hook の起動・掲示の書き込みの遅れ込み）。これより短い保留は従来どおり人を待っているとみなす。
HOLD_GRACE = 10.0
# 終了（SessionEnd）の後に transcript が進んだとみなす余裕（/exit の後始末の行が数秒遅れて書かれることがある）
END_SLACK = 10.0
_TURN_OPEN = frozenset({"UserPromptSubmit", "PreToolUse", "PostToolUse", "PostToolUseFailure"})
_TURN_CLOSE = frozenset({"Stop", "StopFailure", "SessionEnd", "SessionStart"})
_LOCK = threading.Lock()
_RING = deque(maxlen=RING)
_STATE = {}
_TURN = {}           # sid → {"open", "ts"（ターンの最後のイベント）, "tool"（本体の道具の開始・結果で 0）, "hold", "held"（保留の時刻）,
#                      "err", "ended"（SessionEnd の時刻・本体の次の開始と SessionStart で 0）}
_SEQ = 0
_SUBSCRIBERS = set()
_THREAD = None
_STOP = None
_ON_CHANGE = None
_HOME = None
_DAY = None
# Read all complete lines on the first pass; subsequent passes use byte offsets.
_TAIL = IncrementalTail(sys.maxsize)


def _today():
    return date.today()


def _ingest(line):
    """Accept one valid line, returning its event name for cache invalidation."""
    global _SEQ
    try:
        event = json.loads(line)
        if not isinstance(event, dict) or type(event.get("v")) is not int or event["v"] != 1:
            return None
        sid, ts, ev = event.get("sid"), event.get("ts"), event.get("ev")
        if not isinstance(sid, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", sid):
            return None
        if (type(ts) not in (int, float) or not math.isfinite(ts)
                or not isinstance(ev, str) or ev not in _EVENTS
                or not isinstance(event.get("tool", ""), str)
                or not isinstance(event.get("nt", ""), str)):
            return None
    except (ValueError, TypeError, OverflowError, RecursionError):
        return None
    with _LOCK:
        _SEQ += 1
        event["seq"] = _SEQ
        _RING.append(event)
        previous = _STATE.get(sid)
        # Yesterday can receive a late append after today's file has been read.
        if previous is None or ts >= previous["ts"]:
            _STATE[sid] = {"ts": ts, "ev": ev, "tool": event.get("tool", ""),
                           "nt": event.get("nt", ""), "seq": _SEQ}
            _turn_step(sid, ev, ts, event.get("nt", ""), event.get("ag") == 1)
        if len(_STATE) > 2000:
            oldest = min(_STATE, key=lambda key: (_STATE[key]["ts"], _STATE[key]["seq"]))
            del _STATE[oldest]
            _TURN.pop(oldest, None)
        subscribers = list(_SUBSCRIBERS)
    # 失効イベントは **_STATE を更新した直後・購読者へ流す前** に同期で失効させる。
    # 順序が逆だと poke を受けた UI が /api/office を取り直しても 2 秒キャッシュの古い状態が返る
    # （Astra レビュー指摘）。デバウンス付きの _run 側の失効は残す（連続イベントの合図をまとめる）。
    if ev in _INVALIDATE and _ON_CHANGE is not None:
        try:
            _ON_CHANGE(ev)
        except Exception:
            pass
    for subscriber in subscribers:
        try:
            subscriber.put_nowait(deepcopy(event))
        except queue.Full:
            pass                     # 読まないクライアントには積まない（有界・SSE 側の書き込みタイムアウトで切れる）
    return ev


def _turn_step(sid, ev, ts, nt, sub_agent):
    """ターンの表を 1 イベント進める（_LOCK の中で呼ぶ・時刻順のイベントだけ）。"""
    t = _TURN.get(sid) or {"open": False, "ts": 0.0, "main": 0.0, "tool": 0.0, "hold": False, "held": 0.0,
                           "err": 0.0, "ended": 0.0}
    if ev in _TURN_CLOSE:
        t = {"open": False, "ts": ts, "main": ts, "tool": 0.0, "hold": False, "held": 0.0,
             "err": ts if ev == "StopFailure" else 0.0, "ended": ts if ev == "SessionEnd" else 0.0}
    elif ev in _TURN_OPEN and not sub_agent:
        t = dict(t, open=True, ts=ts, main=ts, hold=False, held=0.0, ended=0.0)
        if ev == "UserPromptSubmit":
            t["err"], t["tool"] = 0.0, 0.0
        elif ev == "PreToolUse":
            t["tool"] = ts
        else:
            t["tool"] = 0.0
    elif ev == "PermissionRequest" or (ev == "Notification" and nt == "permission_prompt"):
        t = dict(t, hold=True, held=ts, tool=0.0, ts=ts if t["open"] else t["ts"])
    elif t["open"]:                                     # 中立（サブエージェントの道具・Subagent*・Compact…）
        t = dict(t, ts=ts)
    else:
        return
    _TURN[sid] = t


def turn_of(sid) -> Optional[dict]:
    with _LOCK:
        t = _TURN.get(sid)
        return dict(t) if t is not None else None


def _poll(home):
    """Read yesterday before today, keeping only those two file offsets."""
    global _DAY
    today = _today()
    if _DAY != today:
        _DAY = today
        prune(home)
    days = (today - timedelta(days=1), today)
    keys = {day.isoformat() for day in days}
    for key in list(_TAIL.offsets):
        if key not in keys:
            _TAIL.drop(key)
    changed = None
    for day in days:
        try:
            key = day.isoformat()
            path = Path(home) / EVENTS_DIR / (key + ".jsonl")
            for line in _TAIL.read(path, key, now=time.time()):
                try:
                    ev = _ingest(line)
                    if ev in _INVALIDATE:
                        changed = ev
                except Exception:
                    continue
        except Exception:
            continue
    return changed


def _run(home, stopped, on_change):
    # 失効の合図は _ingest が同期で出す（poke より先にキャッシュを失効させる順序が要る・Astra レビュー指摘）。
    # ここは tail のポーリングだけ。on_change は start() が _ON_CHANGE に保持する。
    next_poll = 0.0
    while not stopped.is_set():
        try:
            if time.monotonic() >= next_poll:
                _poll(home)
                next_poll = time.monotonic() + 0.5
            stopped.wait(max(0.0, next_poll - time.monotonic()))
        except Exception:
            stopped.wait(0.5)


def start(home, on_change=None):
    """Start at most one daemon; retain offsets when restarting the same home."""
    global _ON_CHANGE, _THREAD, _STOP, _HOME, _TAIL, _DAY
    _ON_CHANGE = on_change
    with _LOCK:
        if _THREAD is not None and _THREAD.is_alive():
            return
        home = Path(home)
        if _HOME != home:
            _HOME = home
            _TAIL = IncrementalTail(sys.maxsize)
            _RING.clear()
            _STATE.clear()
            _TURN.clear()
        _DAY = None
        _STOP = threading.Event()
        _THREAD = threading.Thread(target=_run, args=(home, _STOP, on_change),
                                   name="office-events", daemon=True)
        _THREAD.start()


def stop():
    with _LOCK:
        thread = _THREAD
        if _STOP is not None:
            _STOP.set()
    if thread is not None and thread is not threading.current_thread():
        thread.join()


_REFRESH_LOCK = threading.Lock()


def refresh(home):
    """R102-4（別モデルレビュー）: 読み取りのスレッドが動いていないプロセス（中継の配達員 relay_agent は別の LaunchAgent）でも、
    呼ばれた時点までのイベントを増分で読む＝スマホへ出す office_json もターンの判定と StopFailure を使える（状態はファイルが正本・
    不変条件 9）。daemon ではスレッドが読むので何もしない。"""
    global _HOME, _TAIL
    if _THREAD is not None and _THREAD.is_alive():
        return
    home = Path(home)
    with _REFRESH_LOCK:
        with _LOCK:
            if _HOME is None:
                _HOME = home                     # 初めて＝すでに入っている状態は消さない
            elif _HOME != home:
                _HOME = home                     # 別の HOME へ移った＝読み直す
                _TAIL = IncrementalTail(sys.maxsize)
                _RING.clear()
                _STATE.clear()
                _TURN.clear()
        try:
            _poll(home)
        except Exception:
            pass


def seq() -> int:
    with _LOCK:
        return _SEQ


def recent(since_seq=0, limit=200) -> list[dict]:
    with _LOCK:
        rows = [row for row in _RING if row["seq"] > since_seq]
        return deepcopy(rows[:max(0, limit)])


def state_of(sid, now) -> Optional[dict]:
    with _LOCK:
        state = _STATE.get(sid)
        if state is None or now - state["ts"] > STATE_TTL:
            return None
        return dict(state)


def turn_live(turn, info, now) -> bool:
    """R102-4: ターンが開いていて、本体の道具が走っている（TOOL_TTL）か最後のイベントから TURN_TTL 以内か。
    許可のダイアログで保留中・transcript の中断（Esc）がターンの最後のイベントより後なら False。"""
    if not turn or not turn["open"] or turn["hold"] or isinstance(info.get("err"), dict):
        return False                                    # transcript の最後が合成のエラー＝ターンは落ちている
    try:
        # 中断はターンの「本体の」最後のイベントと比べる（裏のサブエージェントの中立のイベントで ts が進んでも開き直さない）
        if float(info.get("interruptedAt") or 0) >= turn.get("main", turn["ts"]):
            return False
    except (TypeError, ValueError):
        pass
    return bool((turn["tool"] and now - turn["tool"] <= TOOL_TTL) or now - turn["ts"] <= TURN_TTL)


def _ended_for_good(turn, info, now) -> bool:
    """終了（SessionEnd）が transcript の最後の行より後＝そのセッションは閉じたまま（STATE_TTL の 60 秒を過ぎても）。
    transcript の最後の行の時刻は now − age（parse_session の鮮度＝中身の最終イベント）。再開すれば SessionStart で消える。"""
    ended = turn.get("ended") or 0.0
    if not ended:
        return False
    try:
        last = now - float(info.get("age") or 0)
    except (TypeError, ValueError):
        return False
    return last <= ended + END_SLACK


def _tool_running(turn, now) -> bool:
    """本体の道具が走っている（PreToolUse から結果まで・保留でない・TOOL_TTL 以内）。"""
    return bool(turn["open"] and not turn["hold"] and turn["tool"] and now - turn["tool"] <= TOOL_TTL)


def overlay(info, home, now, asks_wired=False, holds_seen=False) -> dict:
    """Apply recent hook state without mutating the caller or its listening flag.

    asks_wired: 承認フック（office-approval-wait.sh）が配線済み＝許可のダイアログには必ず掲示（ask）が出る。
      掲示が無いのに保留が HOLD_GRACE を越えて残っている＝ターミナルで答えが出て道具が走っている（保留を解く）。
    holds_seen: イベント記録が PermissionRequest も拾う配線。
    推測の ❗（approvalMin・stuckTool）を消すのは asks_wired かつ holds_seen のときだけ（掲示＝本物の許可待ちの一次情報が
    あるときだけ）。イベントの記録は非同期で、同じ呼び出しの PreToolUse が PermissionRequest より後の時刻で記録される
    ことがあり、並列の道具は保留を開き直す＝記録だけでは「保留が解けた」と言い切れない（承認フックが無い配線で消すと
    本物の許可待ちを隠す・別モデルレビュー）。"""
    sid = info.get("session")
    state = state_of(sid, now)
    turn = turn_of(sid)
    if state is None and turn is None:
        return info
    result = dict(info, evSeq=state["seq"]) if state is not None else info
    if info.get("ask"):
        return result
    if state is not None:
        ev = state["ev"]
        if (ev in {"Stop", "StopFailure", "PermissionRequest"}
                or ev == "Notification" and state["nt"] in _WAITING_NOTIFICATIONS):
            result["state"] = "waiting"
        elif ev in {"PreToolUse", "PostToolUse", "UserPromptSubmit"}:
            if now - state["ts"] <= WORKING_TTL:
                result["state"] = "working"
        elif ev == "SessionEnd":
            result["gone"] = True
            return result
    if turn is not None:
        # 終了したセッションは 60 秒を過ぎても戻ってこない（transcript の 3 時間の窓から「指示待ち」・❗で蘇っていた）
        if _ended_for_good(turn, info, now):
            return dict(result, gone=True)
        if turn["hold"] and asks_wired and now - (turn.get("held") or turn["ts"]) >= HOLD_GRACE:
            # 掲示が無い（scan_office は掲示のあるセッションでは overlay を呼ばない）＝答えが出た。道具は保留の時刻から走っている
            turn = dict(turn, hold=False, tool=turn.get("held") or turn["ts"])
        # R102-4: ターンが開いている間は作業中（transcript が止まって見えても）。閉じたターンは上書きしない（Stop の直後の
        # waiting・古くなれば transcript の推定のまま）
        live = turn_live(turn, info, now)
        if live and result.get("state") != "working":
            result = dict(result, state="working")
        if (live and holds_seen and asks_wired and _tool_running(turn, now)
                and (result.get("approvalMin") or result.get("stuckTool"))):
            # 長い Bash・サブエージェント・許可の後の実行は transcript の最後が tool_use のまま 75 秒を越える
            # ＝推測の「止まっている?」❗と通知が立っていた。走っていると分かっているので消す
            result = dict(result, approvalMin=0, stuckTool="")
        if (not turn["open"] and turn["err"] and now - turn["err"] <= ERR_TTL
                and not isinstance(result.get("err"), dict)):
            result = dict(result, err={"k": "api", "ago": int(max(0.0, now - turn["err"]))})
    return result


def subscribe() -> Optional[queue.Queue]:
    with _LOCK:
        if len(_SUBSCRIBERS) >= 8:
            return None
        subscriber = queue.Queue(maxsize=RING)
        _SUBSCRIBERS.add(subscriber)
        return subscriber


def unsubscribe(subscriber):
    with _LOCK:
        _SUBSCRIBERS.discard(subscriber)


def prune(home, days=14):
    """Remove only dated JSONL files older than the local calendar cutoff."""
    cutoff = _today() - timedelta(days=days)
    try:
        paths = (Path(home) / EVENTS_DIR).glob("*.jsonl")
        for path in paths:
            try:
                if not re.fullmatch(r"\d{4}-\d{2}-\d{2}\.jsonl", path.name):
                    continue
                if date.fromisoformat(path.stem) < cutoff and path.is_file():
                    path.unlink()
            except (OSError, ValueError):
                continue
    except OSError:
        pass
