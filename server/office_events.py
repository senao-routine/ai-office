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
_LOCK = threading.Lock()
_RING = deque(maxlen=RING)
_STATE = {}
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
        if len(_STATE) > 2000:
            oldest = min(_STATE, key=lambda key: (_STATE[key]["ts"], _STATE[key]["seq"]))
            del _STATE[oldest]
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


def seq() -> int:
    with _LOCK:
        return _SEQ


def recent(since_seq=0, limit=200) -> list[dict]:
    with _LOCK:
        rows = [row for row in _RING if row["seq"] > since_seq]
        return deepcopy(rows[:max(0, limit)])


def state_of(sid, now) -> dict | None:
    with _LOCK:
        state = _STATE.get(sid)
        if state is None or now - state["ts"] > STATE_TTL:
            return None
        return dict(state)


def overlay(info, home, now) -> dict:
    """Apply recent hook state without mutating the caller or its listening flag."""
    state = state_of(info.get("session"), now)
    if state is None:
        return info
    result = dict(info, evSeq=state["seq"])
    if info.get("ask"):
        return result
    ev = state["ev"]
    if (ev in {"Stop", "PermissionRequest"}
            or ev == "Notification" and state["nt"] in _WAITING_NOTIFICATIONS):
        result["state"] = "waiting"
    elif ev in {"PreToolUse", "PostToolUse", "UserPromptSubmit"}:
        if now - state["ts"] <= WORKING_TTL:
            result["state"] = "working"
    elif ev == "SessionEnd":
        result["gone"] = True
    return result


def subscribe() -> queue.Queue | None:
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
