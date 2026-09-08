# -*- coding: utf-8 -*-
"""Claude Code の公式状態で transcript 由来の推測を補うアダプタ。

対話セッションは jobs に載らないため agents --json も扱う。resume 起動では
sessionId / resumeSessionId / transcript の stem の3キーで同じ job を突合する。
本文（intent、fan.label、output、dispatch 等）は参照せず、短い detail と
許可したメタデータだけを返す。ファイル/CLI の取得と純粋な変換を分離する。
"""
import copy
import json
import math
import os
import subprocess
from datetime import datetime
from pathlib import Path

ALLOWED_STATES = ("working", "blocked", "done", "failed", "stopped")
_AGENTS_CACHE = {}
_BG_FIELDS = ("id", "name", "nameSource", "state", "tempo", "detail",
              "inFlight", "fan", "template")


def _decode(raw):
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except (ValueError, RecursionError):
            return None
    return raw


def _text(value):
    # str(dict) で未知の本文を露出しない。
    return value if isinstance(value, str) else ""


def _integer(value):
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        return 0
    try:
        number = int(value)
        if isinstance(value, float) and number != value:
            return 0
        return max(0, number)
    except (ValueError, OverflowError):
        return 0


def _epoch(value):
    """ISO 時刻、epoch 秒、公式 roster/agents のミリ秒を秒へ揃える。"""
    try:
        if isinstance(value, str):
            normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
            number = datetime.fromisoformat(normalized).timestamp()
        elif isinstance(value, (int, float)) and not isinstance(value, bool):
            number = float(value)
            if number >= 100_000_000_000:
                number /= 1000
        else:
            return 0.0
        return number if math.isfinite(number) and number > 0 else 0.0
    except (ValueError, OverflowError, OSError):
        return 0.0


def parse_job_state(raw, now):
    """読み取り済み JSON から許可した job メタデータだけを作る純関数。"""
    raw = _decode(raw)
    if not isinstance(raw, dict):
        return None
    state = raw.get("state")
    session_id = _text(raw.get("sessionId"))
    if state not in ALLOWED_STATES or not session_id.strip():
        return None
    in_flight = raw.get("inFlight")
    if not isinstance(in_flight, dict):
        in_flight = {}
    kinds = in_flight.get("kinds")
    if not isinstance(kinds, list):
        kinds = []
    fan = {"shell": 0, "todo": 0, "running": 0, "done": 0}
    entries = raw.get("fan")
    for entry in entries if isinstance(entries, list) else []:
        if not isinstance(entry, dict):
            continue
        kind = entry.get("kind")
        if kind in ("shell", "todo"):
            fan[kind] += 1
        started = _epoch(entry.get("startedAt"))
        done = _epoch(entry.get("doneAt"))
        if done > 0:
            fan["done"] += 1
        elif kind == "todo" and started > 0:
            fan["running"] += 1
    link = _text(raw.get("linkScanPath"))
    updated = _epoch(raw.get("updatedAt"))
    return {
        "id": _text(raw.get("daemonShort")),
        "state": state,
        "detail": _text(raw.get("detail"))[:200],
        "tempo": _text(raw.get("tempo")),
        "inFlight": {
            "tasks": _integer(in_flight.get("tasks")),
            "queued": _integer(in_flight.get("queued")),
            "kinds": [kind for kind in kinds if isinstance(kind, str)],
        },
        "fan": fan,
        "tokens": _integer(raw.get("tokens")),
        "name": _text(raw.get("name")),
        "nameSource": _text(raw.get("nameSource")),
        "sessionId": session_id,
        "resumeSessionId": _text(raw.get("resumeSessionId")),
        "transcriptStem": Path(link).stem if link else "",
        "cwd": _text(raw.get("cwd")),
        "template": _text(raw.get("template")),
        "createdAt": _epoch(raw.get("createdAt")),
        "updatedAt": updated,
        "age": now - updated,
    }


def parse_roster(raw):
    """roster の有効な worker 行だけを返す。dispatch は参照しない。"""
    raw = _decode(raw)
    if not isinstance(raw, dict) or not isinstance(raw.get("workers"), dict):
        return {}
    result = {}
    for short, worker in raw["workers"].items():
        if not isinstance(short, str) or not short.strip() or not isinstance(worker, dict):
            continue
        pid = _integer(worker.get("pid"))
        session_id = _text(worker.get("sessionId"))
        if not pid or not session_id.strip():
            continue
        result[short] = {
            "pid": pid, "sessionId": session_id,
            "cwd": _text(worker.get("cwd")),
            "startedAt": _epoch(worker.get("startedAt")),
        }
    return result


def parse_agents_json(raw):
    """background と interactive の両方を sessionId で引ける形にする。"""
    raw = _decode(raw)
    if not isinstance(raw, list):
        return {}
    result = {}
    for agent in raw:
        if not isinstance(agent, dict):
            continue
        session_id = _text(agent.get("sessionId"))
        kind = agent.get("kind")
        state = agent.get("state")
        pid = _integer(agent.get("pid"))
        if (not session_id.strip() or kind not in ("background", "interactive")
                or state not in ALLOWED_STATES or not pid):
            continue
        status = _text(agent.get("status"))
        result[session_id] = {
            "id": _text(agent.get("id")), "kind": kind, "pid": pid,
            "name": _text(agent.get("name")), "status": status, "state": state,
            "waiting": bool(_text(agent.get("waitingFor"))) or status == "waiting",
            "cwd": _text(agent.get("cwd")),
            "startedAt": _epoch(agent.get("startedAt")),
        }
    return result


def _read_json(path):
    try:
        return _decode(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError):
        return None


class BgIndex:
    """3キーの辞書と job 一覧。衝突したキーは updatedAt の新しい方を採る。

    record の任意の roster キーには、その job に対応する安全な worker 情報を
    添える。overlay はこの情報から CLI がない場合の PID を補う。
    """

    def __init__(self, records, roster):
        self.records = records
        self.roster = roster
        self._by_key = {}
        for record in records:
            for field in ("sessionId", "resumeSessionId", "transcriptStem"):
                key = record[field]
                current = self._by_key.get(key)
                if key and (current is None or record["updatedAt"] > current["updatedAt"]):
                    self._by_key[key] = record

    def lookup(self, session_id_or_stem):
        return self._by_key.get(session_id_or_stem)


def bg_index(home, now, show_window=3 * 3600):
    """job の状態ファイルと roster のみを読む（transcript/timeline は読まない）。"""
    claude = Path(home) / ".claude"
    roster = parse_roster(_read_json(claude / "daemon" / "roster.json"))
    records = []
    try:
        paths = sorted((claude / "jobs").glob("*/state.json"))
    except OSError:
        paths = []
    for path in paths:
        record = parse_job_state(_read_json(path), now)
        if (record is None or record["updatedAt"] <= 0
                or record["updatedAt"] < now - show_window):
            continue
        worker = roster.get(record["id"])
        if worker and worker["sessionId"] in (
                record["sessionId"], record["resumeSessionId"], record["transcriptStem"]):
            record["roster"] = dict(worker)
        records.append(record)
    return BgIndex(records, roster)


def agents_cli(home, now, timeout=5.0):
    """fixture → 無効化指定 → CLI（home ごとの10秒キャッシュ）の順で取得する。"""
    fixture = os.environ.get("OFFICE_AGENTS_FIXTURE")
    if fixture:
        return parse_agents_json(_read_json(Path(fixture)))
    if os.environ.get("OFFICE_AGENTS_CLI") == "0":
        return {}
    key = str(home)
    cached = _AGENTS_CACHE.get(key)
    if cached is not None and 0 <= now - cached["ts"] < 10:
        return copy.deepcopy(cached["value"])
    value = {}
    try:
        completed = subprocess.run(
            ["claude", "agents", "--json"], capture_output=True, text=True,
            timeout=timeout, shell=False)
        if completed.returncode == 0:
            value = parse_agents_json(completed.stdout)
    except (OSError, subprocess.TimeoutExpired, UnicodeError):
        pass
    _AGENTS_CACHE[key] = {"ts": now, "value": copy.deepcopy(value)}
    return value


def overlay(info, bg, agent, now):
    """承認・公式状態を優先してコピーを補完する。心拍と title は変更しない。"""
    result = dict(info)
    bg = bg or {}
    agent = agent or {}
    if not info.get("ask"):
        if agent.get("waiting"):
            result["state"] = "waiting"
        elif bg.get("state") == "blocked":
            result["state"] = "waiting"
        elif bg.get("state") == "working" and bg.get("age", float("inf")) < 240:
            result["state"] = "working"
        elif bg.get("state") in ("done", "failed", "stopped"):
            if not (info.get("state") == "working" and info.get("age", float("inf")) < 25):
                result["state"] = "resting"
    if bg:
        result["bg"] = {key: copy.deepcopy(bg[key]) for key in _BG_FIELDS if key in bg}
    if not result.get("name"):
        name = bg.get("name") or agent.get("name")
        if name:
            result["name"] = name
    pid = agent.get("pid") or bg.get("roster", {}).get("pid")
    if pid:
        result["pid"] = pid
    return result
