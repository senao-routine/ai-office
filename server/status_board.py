#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""AI Office のローカル専用リソースモニター。

重要: status board のデータを office_json に混ぜない。
Cloudflare relay への漏洩を防ぐため、必ず独立ルートで提供する。
Claude のトランスクリプトは読み取り専用であり、このモジュールから書き込まない。
"""
import copy
import fcntl
import hashlib
import json
import math
import os
import re
import socket
import threading
import time
from datetime import datetime
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

_HOME = Path(os.environ.get("OFFICE_HOME") or Path.home())
PROJECTS = _HOME / ".claude" / "projects"
CODEX_SESSIONS = _HOME / ".codex" / "sessions"
GEMINI_DIR = _HOME / ".gemini"
LEDGER_FILE = _HOME / ".claude" / "office_resources.json"

CACHE_TTL = 60.0
FIVE_HOURS = 5 * 3600
BUCKET_SECONDS = 15 * 60
CODEX_TAIL_BYTES = 64 * 1024
# Rollout は Codex 使用中だけ書かれるため、短いアイドル時間を stale にしない。
CODEX_STALE_SECONDS = 6 * 3600
LEDGER_STALE_SECONDS = 7 * 86400
EXTERNAL_CACHE_TTL = 15 * 60.0
MONTH_PROJECT_LIMIT = 10
DEFAULT_JPY_PER_USD = 155
MIN_JPY_PER_USD = 50
MAX_JPY_PER_USD = 1000
_ID_RE = re.compile(r"^[a-z0-9_-]{1,32}$")
_SPEND_MONTH_RE = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")

_BASE_MODEL_PRICES = {
    # USD / 1,000,000 tokens.  These are intentionally local estimates.
    "claude-fable-5": {"in": 3.0, "out": 15.0, "cacheRead": 0.3, "cacheCreate": 3.75},
    "claude-opus-4-8": {"in": 15.0, "out": 75.0, "cacheRead": 1.5, "cacheCreate": 18.75},
    "claude-sonnet-5": {"in": 3.0, "out": 15.0, "cacheRead": 0.3, "cacheCreate": 3.75},
    "default": {"in": 3.0, "out": 15.0, "cacheRead": 0.3, "cacheCreate": 3.75},
}


def _valid_price(value):
    if not isinstance(value, (int, float)) or isinstance(value, bool) or value < 0:
        return False
    try:
        return math.isfinite(value)
    except (OverflowError, ValueError):
        return False


def _normalized_model_price(price):
    """4単価へ正規化する。旧形式はinからキャッシュ単価を導出する。"""
    if not isinstance(price, dict):
        return None
    incoming = price.get("in")
    outgoing = price.get("out")
    if not (_valid_price(incoming) and _valid_price(outgoing)):
        return None
    cache_read = price.get("cacheRead")
    cache_create = price.get("cacheCreate")
    return {
        "in": float(incoming),
        "out": float(outgoing),
        "cacheRead": float(cache_read) if _valid_price(cache_read) else float(incoming) * 0.1,
        "cacheCreate": (float(cache_create) if _valid_price(cache_create)
                        else float(incoming) * 1.25),
    }


def _read_model_price_overrides():
    path = _HOME / ".claude" / "office_model_prices.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return {}
    if not isinstance(data, dict):
        return {}
    overrides = {}
    for model, price in data.items():
        if not isinstance(model, str) or not model or not isinstance(price, dict):
            continue
        normalized = _normalized_model_price(price)
        if normalized is not None:
            overrides[model] = normalized
    return overrides


def _merge_model_prices(base, override):
    merged = {}
    for model, price in (base.items() if isinstance(base, dict) else ()):
        normalized = _normalized_model_price(price)
        if normalized is not None:
            merged[model] = normalized
    for model, price in override.items():
        normalized = _normalized_model_price(price)
        if normalized is not None:
            merged[model] = normalized
    return merged


def _usage_usd(values, price):
    """キャッシュ種別を分けた、USD / 1,000,000 tokens の換算。"""
    if not isinstance(price, dict):
        return 0.0
    return (
        values["input"] * price["in"]
        + values["cacheRead"] * price["cacheRead"]
        + values["cacheCreate"] * price["cacheCreate"]
        + values["output"] * price["out"]
    ) / 1_000_000


# Import時にも反映し、テストや実運用でファイルを後から更新した場合は
# _effective_model_prices() が次のClaude走査で再読込する。
MODEL_PRICES = _merge_model_prices(_BASE_MODEL_PRICES, _read_model_price_overrides())


_EXT_CACHE = {}

_LOCK = threading.RLock()
_SCAN_LOCK = threading.Lock()
_CACHE = {"data": None}
_CACHE_VERSION = 0
_REFRESHING = False
_REFRESH_THREAD = None


class _file_flock:
    """対象ファイルごとの小さなプロセス間ロック。常に _LOCK の後に取得する。"""

    def __init__(self, target):
        target = Path(target)
        self._lockpath = target.with_name(target.name + ".lock")

    def __enter__(self):
        self._lockpath.parent.mkdir(parents=True, exist_ok=True)
        self._file = open(self._lockpath, "w", encoding="utf-8")
        fcntl.flock(self._file, fcntl.LOCK_EX)
        return self._file

    def __exit__(self, *_exc):
        try:
            fcntl.flock(self._file, fcntl.LOCK_UN)
        finally:
            self._file.close()


def _zero_tokens():
    return {"input": 0, "output": 0, "cacheRead": 0, "cacheCreate": 0, "total": 0}


def _new_scan(day=None):
    return {
        "day": day,
        "month": None,
        "now": None,
        "files": {},
        "seen": {},
        "today": _zero_tokens(),
        "byModel": {},
        "projects": {},
        "buckets": {},
        "bucketEvents": {},
        "sessionsToday": set(),
    }


_SCAN = _new_scan()


def _reset_scan(day=None):
    _SCAN.clear()
    _SCAN.update(_new_scan(day))


def _now_value(now):
    return float(time.time() if now is None else now)


def _effective_model_prices():
    return _merge_model_prices(MODEL_PRICES, _read_model_price_overrides())


def _office_secrets_file():
    return _HOME / ".claude" / "office_secrets"


def _secret_value(name):
    """office_secretsから指定行だけを読み、値とファイルmtimeを返す。"""
    value = ""
    try:
        source_mtime = _office_secrets_file().stat().st_mtime_ns
        for line in _office_secrets_file().read_text(encoding="utf-8").splitlines():
            line_name, separator, candidate = line.partition("=")
            if separator and line_name == name:
                value = candidate
        return value, source_mtime
    except (OSError, UnicodeError):
        return "", None


def _fetch_json(url, headers, timeout=6):
    """GET JSONをfail-softで取得する。秘密値はURL・エラーへ入れない。"""
    request_headers = {"User-Agent": "AI-Office-Resource-Monitor/1.0"}
    if isinstance(headers, dict):
        request_headers.update(headers)
    request = Request(url, headers=request_headers, method="GET")
    try:
        with urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
        return payload if isinstance(payload, dict) else {"error": "invalid JSON"}
    except HTTPError as exc:
        return {"error": f"HTTP {int(exc.code)}"}
    except (socket.timeout, TimeoutError):
        return {"error": "timeout"}
    except URLError as exc:
        reason = exc.reason
        if isinstance(reason, (socket.timeout, TimeoutError)) or "timed out" in str(reason).lower():
            return {"error": "timeout"}
        return {"error": "network error"}
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError):
        return {"error": "取得エラー"}


def _external_error(payload):
    value = payload.get("error") if isinstance(payload, dict) else None
    if not isinstance(value, str) or not value:
        return "取得エラー"
    if re.fullmatch(r"HTTP [1-5][0-9]{2}", value):
        return value
    if value == "timeout":
        return value
    return value[:80]


def _external_cached(name, key_name, now):
    secret, source_mtime = _secret_value(key_name)
    if not secret:
        return {"connected": False}
    cached = _EXT_CACHE.get(name)
    if (isinstance(cached, dict) and cached.get("mtime") == source_mtime
            and isinstance(cached.get("at"), (int, float))
            and 0 <= now - cached["at"] < EXTERNAL_CACHE_TTL
            and isinstance(cached.get("data"), dict)):
        return copy.deepcopy(cached["data"])
    # Request threads may render the last value, but never initiate an external fetch.
    return {"connected": True, "error": "未取得"}


def _cache_external(name, source_mtime, now, data):
    _EXT_CACHE[name] = {
        "at": now,
        "mtime": source_mtime,
        "data": copy.deepcopy(data),
    }


def _error_text(exc):
    text = str(exc).strip()
    return f"{type(exc).__name__}: {text}" if text else type(exc).__name__


def _claude_unavailable(error, source_mtime=None):
    return {
        "id": "claude",
        "label": "Claude Code",
        "kind": "tokens",
        "status": "unavailable",
        "tokens": {"today": _zero_tokens(), "last5h": _zero_tokens(), "byModel": {}},
        "projects": [],
        "estimate": True,
        "sessionsToday": 0,
        "trend": [],
        "sourceMtime": source_mtime,
        "error": str(error),
    }


def _codex_unavailable(error, source_mtime=None):
    return {
        "id": "codex",
        "label": "Codex",
        "kind": "gauge",
        "plan": None,
        "status": "unavailable",
        "usedPercent": 0.0,
        "resetsAt": None,
        "windowMinutes": None,
        "secondary": None,
        "tokens": None,
        "sourceMtime": source_mtime,
        "error": str(error),
    }


def _gemini_unavailable(error, source_mtime=None):
    return {
        "id": "gemini",
        "label": "Gemini CLI",
        "kind": "login",
        "status": "unavailable",
        "loggedIn": False,
        "sourceMtime": source_mtime,
        "error": str(error),
    }


def _valid_number(value):
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return False
    try:
        return math.isfinite(value)
    except (OverflowError, ValueError):
        return False


def _token_number(value):
    if not _valid_number(value) or value < 0:
        raise ValueError("token count is not a non-negative number")
    return value


def _optional_int(value):
    if value is None:
        return None
    if not _valid_number(value):
        raise ValueError("expected an integer or null")
    integer = int(value)
    if integer != value:
        raise ValueError("expected an integer or null")
    return integer


def _parse_iso_epoch(value):
    if not isinstance(value, str) or not value:
        raise ValueError("timestamp is missing")
    normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None:
        parsed = parsed.astimezone()
    return parsed.timestamp(), parsed.astimezone().date().isoformat()


def _local_day(now):
    return datetime.fromtimestamp(now).astimezone().date().isoformat()


def _local_month(now):
    local = datetime.fromtimestamp(now).astimezone()
    return f"{local.year:04d}-{local.month:02d}"


def _local_month_start(now):
    local = datetime.fromtimestamp(now).astimezone()
    return local.replace(day=1, hour=0, minute=0, second=0, microsecond=0).timestamp()


def _local_midnight(now):
    local = datetime.fromtimestamp(now).astimezone()
    return local.replace(hour=0, minute=0, second=0, microsecond=0).timestamp()


def _add_tokens(target, values):
    for key in target:
        target[key] += values[key]


def _claude_paths(now):
    if not PROJECTS.is_dir():
        raise FileNotFoundError("Claude projects directory is unavailable")
    # 当月累計も同じ増分走査で拾う。月初より前のファイルは対象外にして
    # 古いプロジェクト履歴を毎回読み直さない。
    threshold = _local_month_start(now) - 3600
    found = {}
    patterns = ("*/*.jsonl", "*/*/subagents/*.jsonl")
    for pattern in patterns:
        for path in PROJECTS.glob(pattern):
            try:
                stat = path.stat()
            except OSError:
                continue
            if path.is_file() and stat.st_mtime >= threshold:
                found[str(path)] = (path, stat)
    return [found[key] for key in sorted(found)]


def _parent_session(path):
    try:
        parts = path.relative_to(PROJECTS).parts
    except ValueError:
        return str(path)
    if len(parts) >= 4 and parts[-2] == "subagents":
        return f"{parts[0]}/{parts[-3]}"
    if len(parts) >= 2:
        return f"{parts[0]}/{Path(parts[-1]).stem}"
    return str(path)


def _usage_values(usage):
    direct = _token_number(usage.get("input_tokens", 0))
    output = _token_number(usage.get("output_tokens", 0))
    cache_read = _token_number(usage.get("cache_read_input_tokens", 0))
    cache_create = _token_number(usage.get("cache_creation_input_tokens", 0))
    return {
        "input": direct,
        "output": output,
        "cacheRead": cache_read,
        "cacheCreate": cache_create,
        "total": direct + output + cache_read + cache_create,
    }


def _project_display_name(dirname):
    if dirname.startswith("-"):
        restored = dirname.replace("-", "/").strip("/")
        if restored:
            return Path(restored).name or dirname
    return dirname


def _scan_claude_line(raw, path, day, month, now):
    if b'"usage"' not in raw:
        return
    try:
        row = json.loads(raw)
        if not isinstance(row, dict) or row.get("type") != "assistant":
            return
        message = row.get("message")
        if not isinstance(message, dict) or not isinstance(message.get("usage"), dict):
            return
        event_epoch, event_day = _parse_iso_epoch(row.get("timestamp"))
        values = _usage_values(message["usage"])
    except (UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError, OverflowError):
        return

    file_key = str(path)
    request_id = row.get("requestId")
    message_id = message.get("id")
    dedupe = None
    if isinstance(request_id, str) and request_id and isinstance(message_id, str) and message_id:
        dedupe = (request_id, message_id)
        seen = _SCAN["seen"].setdefault(file_key, set())
        if dedupe in seen:
            return
        seen.add(dedupe)

    model = message.get("model")
    if not isinstance(model, str) or not model:
        model = "unknown"
    total_input_tokens = values["input"] + values["cacheRead"] + values["cacheCreate"]

    if event_day[:7] == month:
        try:
            project_dir = path.relative_to(PROJECTS).parts[0]
        except (ValueError, IndexError):
            project_dir = path.parent.name
        project = _SCAN["projects"].setdefault(project_dir, {
            "name": _project_display_name(project_dir),
            "inTok": 0,
            "outTok": 0,
            "usd": 0.0,
        })
        project["inTok"] += total_input_tokens
        project["outTok"] += values["output"]
        prices = _SCAN.get("prices") or MODEL_PRICES
        price = (_normalized_model_price(prices.get(model)) or
                 _normalized_model_price(prices.get("default")) or
                 _normalized_model_price(_BASE_MODEL_PRICES["default"]))
        project["usd"] += _usage_usd(values, price)

    if event_day == day:
        _add_tokens(_SCAN["today"], values)
        _SCAN["sessionsToday"].add(_parent_session(path))
        model_totals = _SCAN["byModel"].setdefault(
            model, {"input": 0, "output": 0, "total": 0, "usd": 0.0})
        model_totals["input"] += total_input_tokens
        model_totals["output"] += values["output"]
        model_totals["total"] += total_input_tokens + values["output"]
        model_totals["usd"] += _usage_usd(values, price)

    if event_epoch >= now - FIVE_HOURS - BUCKET_SECONDS:
        bucket = int(event_epoch // BUCKET_SECONDS) * BUCKET_SECONDS
        bucket_totals = _SCAN["buckets"].setdefault(bucket, _zero_tokens())
        _add_tokens(bucket_totals, values)
        _SCAN["bucketEvents"].setdefault(bucket, []).append((event_epoch, values))


def _claude_trend(cutoff, now):
    """直近5時間の非空15分バケツを、表示用の読み取り専用コピーにする。"""
    trend = []
    for bucket in sorted(_SCAN["bucketEvents"]):
        total = 0
        found = False
        for event_epoch, values in _SCAN["bucketEvents"][bucket]:
            if cutoff <= event_epoch <= now:
                total += values["total"]
                found = True
        if found:
            trend.append({"t": bucket, "total": int(total)})
    return trend[-20:]


def _claude_projects():
    rows = []
    for project in _SCAN["projects"].values():
        rows.append({
            "name": project["name"],
            "inTok": int(project["inTok"]),
            "outTok": int(project["outTok"]),
            "usd": round(float(project["usd"]), 6),
        })
    rows.sort(key=lambda item: (-item["usd"], item["name"]))
    if len(rows) <= MONTH_PROJECT_LIMIT:
        return rows
    other = rows[MONTH_PROJECT_LIMIT:]
    return rows[:MONTH_PROJECT_LIMIT] + [{
        "name": "その他",
        "inTok": sum(item["inTok"] for item in other),
        "outTok": sum(item["outTok"] for item in other),
        "usd": round(sum(item["usd"] for item in other), 6),
    }]


def _collect_claude(now):
    day = _local_day(now)
    month = _local_month(now)
    with _SCAN_LOCK:
        previous_now = _SCAN.get("now")
        prices = _effective_model_prices()
        if (_SCAN.get("month") != month or (previous_now is not None and now < previous_now)
                or _SCAN.get("prices") != prices):
            _reset_scan(day)
            _SCAN["month"] = month
        elif _SCAN.get("day") != day:
            # 月次の files/seen/projects は保持し、日次表示だけを切り替える。
            _SCAN["day"] = day
            _SCAN["today"] = _zero_tokens()
            _SCAN["byModel"] = {}
            _SCAN["sessionsToday"] = set()
        _SCAN["prices"] = prices

        paths = _claude_paths(now)
        # append-only 前提が破れた場合は、日次累計も含めて安全に再構築する。
        if any(stat.st_size < _SCAN["files"].get(str(path), 0) for path, stat in paths):
            _reset_scan(day)
            _SCAN["month"] = month
            _SCAN["prices"] = prices

        source_mtime = None
        for path, stat in paths:
            source_mtime = stat.st_mtime if source_mtime is None else max(source_mtime, stat.st_mtime)
            file_key = str(path)
            offset = _SCAN["files"].get(file_key, 0)
            with path.open("rb") as fh:
                fh.seek(offset)
                pending = fh.read()
            newline = pending.rfind(b"\n")
            if newline < 0:
                continue
            complete = pending[:newline + 1]
            for raw in complete.splitlines():
                _scan_claude_line(raw, path, day, month, now)
            _SCAN["files"][file_key] = offset + newline + 1

        cutoff = now - FIVE_HOURS
        cutoff_bucket = int(cutoff // BUCKET_SECONDS) * BUCKET_SECONDS
        for bucket in list(_SCAN["buckets"]):
            if bucket < cutoff_bucket:
                del _SCAN["buckets"][bucket]
                _SCAN["bucketEvents"].pop(bucket, None)
        last_five = _zero_tokens()
        current_bucket = int(now // BUCKET_SECONDS) * BUCKET_SECONDS
        for bucket, totals in _SCAN["buckets"].items():
            if cutoff_bucket < bucket < current_bucket:
                _add_tokens(last_five, totals)
                continue
            for event_epoch, values in _SCAN["bucketEvents"].get(bucket, ()):
                if cutoff <= event_epoch <= now:
                    _add_tokens(last_five, values)

        _SCAN["now"] = now
        return {
            "id": "claude",
            "label": "Claude Code",
            "kind": "tokens",
            "status": "ok",
            "tokens": {
                "today": dict(_SCAN["today"]),
                "last5h": last_five,
                "byModel": copy.deepcopy(_SCAN["byModel"]),
            },
            "projects": _claude_projects(),
            "estimate": True,
            "sessionsToday": len(_SCAN["sessionsToday"]),
            "trend": _claude_trend(cutoff, now),
            "sourceMtime": source_mtime,
            "error": None,
        }


def collect_claude(now):
    """Claude transcript を増分・読み取り専用で集計する。"""
    now = _now_value(now)
    try:
        return _collect_claude(now)
    except Exception as exc:  # collector 境界: 他 provider を巻き込まない
        return _claude_unavailable(_error_text(exc))


def _named_dirs(parent):
    return sorted((path for path in parent.iterdir() if path.is_dir()),
                  key=lambda path: path.name, reverse=True)


def _codex_candidates():
    if not CODEX_SESSIONS.is_dir():
        raise FileNotFoundError("Codex sessions directory is unavailable")
    candidates = []
    for year in _named_dirs(CODEX_SESSIONS):
        for month in _named_dirs(year):
            for day in _named_dirs(month):
                files = []
                for path in day.glob("rollout-*.jsonl"):
                    try:
                        if path.is_file():
                            files.append((path, path.stat().st_mtime))
                    except OSError:
                        continue
                files.sort(key=lambda item: item[1], reverse=True)
                candidates.extend(files)
                if len(candidates) >= 5:
                    return candidates[:5]
    return candidates[:5]


def _tail_lines(path):
    size = path.stat().st_size
    start = max(0, size - CODEX_TAIL_BYTES)
    with path.open("rb") as fh:
        previous = b"\n"
        if start:
            fh.seek(start - 1)
            previous = fh.read(1)
        fh.seek(start)
        data = fh.read()
    # tail の先頭が行境界なら完全行を保持し、行途中からなら断片だけを捨てる。
    if start and previous != b"\n":
        newline = data.find(b"\n")
        if newline < 0:
            return []
        data = data[newline + 1:]
    return data.splitlines()


def _secondary_gauge(secondary):
    if secondary is None:
        return None
    if not isinstance(secondary, dict) or not _valid_number(secondary.get("used_percent")):
        raise ValueError("rate_limits.secondary is invalid")
    return {
        "usedPercent": float(secondary["used_percent"]),
        "resetsAt": _optional_int(secondary.get("resets_at")),
        "windowMinutes": _optional_int(secondary.get("window_minutes")),
    }


def _rate_gauge(rate, source_mtime, now, payload):
    primary = rate.get("primary")
    if not isinstance(primary, dict):
        raise ValueError("rate_limits.primary is missing")
    used = primary.get("used_percent")
    if not _valid_number(used):
        raise ValueError("rate_limits.primary.used_percent is invalid")

    secondary_out = _secondary_gauge(rate.get("secondary"))

    tokens_out = None
    info = payload.get("info")
    total_usage = info.get("total_token_usage") if isinstance(info, dict) else None
    if isinstance(total_usage, dict):
        token_values = {}
        for source, target in (("input_tokens", "input"),
                               ("cached_input_tokens", "cached"),
                               ("output_tokens", "output"),
                               ("total_tokens", "total")):
            value = total_usage.get(source)
            if not _valid_number(value) or value < 0:
                raise ValueError(f"total_token_usage.{source} is invalid")
            token_values[target] = value
        tokens_out = {"session": token_values}

    plan = rate.get("plan_type")
    if plan is not None and not isinstance(plan, str):
        raise ValueError("rate_limits.plan_type is invalid")
    status = "stale" if source_mtime < now - CODEX_STALE_SECONDS else "ok"
    return {
        "id": "codex",
        "label": "Codex",
        "kind": "gauge",
        "plan": plan,
        "status": status,
        "usedPercent": float(used),
        "resetsAt": _optional_int(primary.get("resets_at")),
        "windowMinutes": _optional_int(primary.get("window_minutes")),
        "secondary": secondary_out,
        "tokens": tokens_out,
        "sourceMtime": source_mtime,
        "error": None,
    }


def _collect_codex(now):
    candidates = _codex_candidates()
    if not candidates:
        raise FileNotFoundError("No Codex rollout files were found")
    last_error = None
    latest_rate = None
    latest_payload = None
    latest_source_mtime = None
    latest_secondary = None
    for path, source_mtime in candidates:
        try:
            lines = _tail_lines(path)
        except OSError as exc:
            last_error = exc
            continue
        for raw in reversed(lines):
            if b'"rate_limits"' not in raw:
                continue
            try:
                row = json.loads(raw)
            except (UnicodeDecodeError, json.JSONDecodeError):
                continue
            if not isinstance(row, dict):
                continue
            payload = row.get("payload")
            if not isinstance(payload, dict):
                continue
            rate = payload.get("rate_limits")
            if not isinstance(rate, dict):
                continue
            if latest_rate is None:
                latest_rate = rate
                latest_payload = payload
                latest_source_mtime = source_mtime
            if latest_secondary is None and rate.get("secondary") is not None:
                latest_secondary = _secondary_gauge(rate["secondary"])
            if latest_secondary is not None:
                break
        if latest_secondary is not None:
            break
    if latest_rate is not None:
        result = _rate_gauge(latest_rate, latest_source_mtime, now, latest_payload)
        # 最新 primary 行の secondary が null の場合は、走査中の直近非null値を使う。
        result["secondary"] = latest_secondary
        return result
    if last_error is not None:
        raise last_error
    raise ValueError("rate_limits not found in the latest Codex rollout files")


def collect_codex(now):
    """最新5 rollout の末尾を逆走し、最も新しい rate limit 行を返す。"""
    now = _now_value(now)
    try:
        return _collect_codex(now)
    except Exception as exc:  # collector 境界
        return _codex_unavailable(_error_text(exc))


def collect_gemini(now):
    """Gemini CLI の OAuth refresh token の有無だけを読む。"""
    creds = GEMINI_DIR / "oauth_creds.json"
    source_mtime = None
    try:
        source_mtime = creds.stat().st_mtime
        with creds.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
        if not isinstance(data, dict):
            raise ValueError("OAuth credentials must be an object")
        return {
            "id": "gemini",
            "label": "Gemini CLI",
            "kind": "login",
            "status": "ok",
            "loggedIn": "refresh_token" in data,
            "sourceMtime": source_mtime,
            "error": None,
        }
    except Exception as exc:  # collector 境界
        return _gemini_unavailable(_error_text(exc), source_mtime)


def _external_result(name, key_name, now, fetch):
    now = _now_value(now)
    secret, source_mtime = _secret_value(key_name)
    if not secret:
        return {"connected": False}
    cached = _EXT_CACHE.get(name)
    if (isinstance(cached, dict) and cached.get("mtime") == source_mtime
            and isinstance(cached.get("at"), (int, float))
            and 0 <= now - cached["at"] < EXTERNAL_CACHE_TTL
            and isinstance(cached.get("data"), dict)):
        return copy.deepcopy(cached["data"])
    try:
        data = fetch(secret)
    except (socket.timeout, TimeoutError):
        # collector境界では認証値やURLを例外文字列へ流さない。
        data = {"error": "timeout"}
    except Exception:
        # collector境界では認証値やURLを例外文字列へ流さない。
        data = {"error": "取得エラー"}
    if not isinstance(data, dict):
        data = {"error": "取得エラー"}
    result = copy.deepcopy(data)
    result["connected"] = True
    if "error" in result:
        result = {"connected": True, "error": _external_error(result)}
    _cache_external(name, source_mtime, now, result)
    return result


def _number_from_payload(value, field):
    # 新X Developer Console(Pay Per Use)は project_usage/project_cap を文字列で返す
    if isinstance(value, str):
        try:
            value = float(value) if "." in value else int(value)
        except ValueError:
            pass
    if not _valid_number(value) or value < 0:
        raise ValueError(f"{field} is invalid")
    return value


def collect_xapi(now):
    """X APIの月間使用量を読み取る。外部結果は15分だけ再利用する。"""
    def fetch(secret):
        payload = _fetch_json(
            "https://api.x.com/2/usage/tweets",
            {"Authorization": f"Bearer {secret}"},
            timeout=6,
        )
        if isinstance(payload, dict) and payload.get("error"):
            return {"error": _external_error(payload)}
        try:
            data = payload.get("data")
            if not isinstance(data, dict):
                raise ValueError("data is missing")
            used = int(_number_from_payload(data.get("project_usage"), "project_usage"))
            cap = int(_number_from_payload(data.get("project_cap"), "project_cap"))
            reset_day = int(_number_from_payload(data.get("cap_reset_day"), "cap_reset_day"))
            pct = round(used / cap * 100, 2) if cap else 0.0
            return {"used": used, "cap": cap, "pct": pct, "resetDay": reset_day}
        except (AttributeError, TypeError, ValueError, OverflowError):
            return {"error": "invalid response"}

    return _external_result("xapi", "X_BEARER_TOKEN", now, fetch)


def _month_start_unix(now):
    return int(_local_month_start(now))


def collect_openai_cost(now):
    """OpenAI organization costsを当月分だけ合算する。"""
    now = _now_value(now)

    def fetch(secret):
        start_time = _month_start_unix(now)
        total = 0.0
        page = None
        # UTC日次バケットはJST月初起点だと当月31日+端数で32枚になり得るため has_more を追う（上限は安全弁）。
        for _ in range(4):
            params = {"start_time": start_time, "limit": 31}
            if page:
                params["page"] = page
            url = "https://api.openai.com/v1/organization/costs?" + urlencode(params)
            payload = _fetch_json(url, {"Authorization": f"Bearer {secret}"}, timeout=6)
            if isinstance(payload, dict) and payload.get("error"):
                error = _external_error(payload)
                if error in ("HTTP 401", "HTTP 403"):
                    return {"error": f"管理キー権限なし({error[-3:]})"}
                return {"error": error}
            try:
                buckets = payload.get("data", payload.get("buckets", []))
                if not isinstance(buckets, list):
                    raise ValueError("buckets is invalid")
                for bucket in buckets:
                    if not isinstance(bucket, dict):
                        continue
                    results = bucket.get("results", [])
                    if not isinstance(results, list):
                        continue
                    for result in results:
                        if not isinstance(result, dict):
                            continue
                        amount = result.get("amount")
                        value = amount.get("value") if isinstance(amount, dict) else None
                        total += float(_number_from_payload(value, "amount.value"))
                page = payload.get("next_page") if payload.get("has_more") else None
            except (AttributeError, TypeError, ValueError, OverflowError):
                return {"error": "invalid response"}
            if not page:
                break
        return {"monthUsd": round(total, 6), "sinceDay": 1}

    return _external_result("openai", "OPENAI_ADMIN_KEY", now, fetch)


def _validate_id(value):
    return isinstance(value, str) and _ID_RE.fullmatch(value) is not None


def _validate_ledger_entry(entry, require_updated=True):
    if not isinstance(entry, dict):
        raise ValueError("entry must be an object")
    if not _validate_id(entry.get("id")):
        raise ValueError("id must match ^[a-z0-9_-]{1,32}$")
    label = entry.get("label")
    if not isinstance(label, str) or len(label) > 40:
        raise ValueError("label must be a string of at most 40 characters")
    for field in ("remaining", "total"):
        value = entry.get(field)
        if not _valid_number(value) or value < 0:
            raise ValueError(f"{field} must be a non-negative number")
    for field in ("plan", "unit", "note"):
        value = entry.get(field, "")
        if not isinstance(value, str):
            raise ValueError(f"{field} must be a string")
    if require_updated and (not _valid_number(entry.get("updatedAt"))
                            or entry["updatedAt"] < 0):
        raise ValueError("updatedAt must be a non-negative number")


def _validate_spend_item(item, require_id=True, allow_negative=False):
    if not isinstance(item, dict):
        raise ValueError("spend item must be an object")
    if require_id and not _validate_id(item.get("id")):
        raise ValueError("id must match ^[a-z0-9_-]{1,32}$")
    label = item.get("label")
    if not isinstance(label, str) or not label.strip() or len(label) > 40:
        raise ValueError("label must be a non-empty string of at most 40 characters")
    amount = item.get("amount")
    if not _valid_number(amount) or (amount < 0 and not allow_negative):
        raise ValueError("amount must be a non-negative number")
    if item.get("currency") not in ("jpy", "usd"):
        raise ValueError("currency must be jpy or usd")
    if item.get("kind") not in ("sub", "payg"):
        raise ValueError("kind must be sub or payg")
    if "renewDay" in item and item.get("renewDay") is not None:
        renew_day = item.get("renewDay")
        if (not isinstance(renew_day, int) or isinstance(renew_day, bool)
                or not 1 <= renew_day <= 31):
            raise ValueError("renewDay must be an integer from 1 to 31")
    if "month" in item and item.get("month") is not None:
        month = item.get("month")
        if not isinstance(month, str) or _SPEND_MONTH_RE.fullmatch(month) is None:
            raise ValueError("month must be a YYYY-MM string")
    note = item.get("note", "")
    if not isinstance(note, str) or len(note) > 200:
        raise ValueError("note must be a string of at most 200 characters")


def _validate_fx_rate(value):
    if not _valid_number(value) or not MIN_JPY_PER_USD <= value <= MAX_JPY_PER_USD:
        raise ValueError(f"jpyPerUsd must be between {MIN_JPY_PER_USD} and {MAX_JPY_PER_USD}")


def _normalized_fx(data):
    """台帳の為替設定を読み取り、旧台帳には既定値を補う。"""
    raw = data.get("fx") if isinstance(data, dict) else None
    if raw is None:
        return {"jpyPerUsd": DEFAULT_JPY_PER_USD}
    if not isinstance(raw, dict) or "jpyPerUsd" not in raw:
        raise ValueError("fx.jpyPerUsd is required")
    rate = raw["jpyPerUsd"]
    _validate_fx_rate(rate)
    return {"jpyPerUsd": float(rate)}


def _spend_id(label):
    normalized = re.sub(r"[^a-z0-9]+", "-", label.strip().lower()).strip("-")
    if normalized:
        return f"spend-{normalized[:25]}"
    digest = hashlib.sha256(label.encode("utf-8")).hexdigest()[:10]
    return f"spend-{digest}"


def _normalized_spend_item(item, allow_negative=False):
    if not isinstance(item, dict):
        raise ValueError("spend item must be an object")
    normalized = {
        "id": item.get("id") or _spend_id(item.get("label", "")),
        "label": item.get("label", ""),
        "amount": item.get("amount"),
        "currency": item.get("currency"),
        "kind": item.get("kind"),
        "note": item.get("note", ""),
    }
    if "renewDay" in item and item.get("renewDay") is not None:
        normalized["renewDay"] = item.get("renewDay")
    if "month" in item and item.get("month") is not None:
        normalized["month"] = item.get("month")
    _validate_spend_item(normalized, allow_negative=allow_negative)
    if normalized["kind"] != "sub":
        normalized.pop("renewDay", None)
    if normalized["kind"] != "payg":
        # 月スタンプは従量の当月集計にだけ意味を持つ（サブスクは毎月固定）。
        normalized.pop("month", None)
    normalized["amount"] = float(normalized["amount"])
    return normalized


def _read_ledger():
    if not LEDGER_FILE.exists():
        return {"version": 1, "entries": []}
    with LEDGER_FILE.open("r", encoding="utf-8") as fh:
        data = json.load(fh)
    if not isinstance(data, dict) or data.get("version") != 1:
        raise ValueError("ledger version must be 1")
    entries = data.get("entries")
    if not isinstance(entries, list):
        raise ValueError("ledger entries must be an array")
    for entry in entries:
        _validate_ledger_entry(entry)
    spend = data.get("spend", [])
    if not isinstance(spend, list):
        raise ValueError("spend must be an array")
    for item in spend:
        _validate_spend_item(item)
    _normalized_fx(data)
    return data


def _normalized_entry(entry, updated_at):
    _validate_ledger_entry(entry, require_updated=False)
    return {
        "id": entry["id"],
        "label": entry["label"],
        "plan": entry.get("plan", ""),
        "remaining": entry["remaining"],
        "total": entry["total"],
        "unit": entry.get("unit", ""),
        "note": entry.get("note", ""),
        "updatedAt": updated_at,
    }


def _write_ledger(data):
    LEDGER_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = LEDGER_FILE.with_name("." + LEDGER_FILE.name + ".tmp")
    try:
        with tmp.open("w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, separators=(",", ":"))
            fh.write("\n")
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, LEDGER_FILE)
    finally:
        try:
            tmp.unlink()
        except FileNotFoundError:
            pass


def _invalidate_cache_locked():
    global _CACHE_VERSION
    _CACHE_VERSION += 1
    _CACHE["data"] = None


def ledger_apply(data):
    """手動台帳を upsert/delete し、(成功可否, メッセージ) を返す。"""
    if not isinstance(data, dict):
        return False, "request must be an object"
    operation = data.get("op")
    if operation not in ("upsert", "delete"):
        return False, "op must be upsert or delete"
    if operation == "delete" and not _validate_id(data.get("id")):
        return False, "id must match ^[a-z0-9_-]{1,32}$"
    if operation == "upsert":
        try:
            normalized = _normalized_entry(data.get("entry"), 0)
        except (OverflowError, TypeError, ValueError) as exc:
            return False, str(exc)

    try:
        with _LOCK:
            with _file_flock(LEDGER_FILE):
                ledger = _read_ledger()
                entries = list(ledger["entries"])
                if operation == "upsert":
                    # flock の取得順と updatedAt の順を一致させる。
                    normalized["updatedAt"] = time.time()
                    for index, entry in enumerate(entries):
                        if entry["id"] == normalized["id"]:
                            entries[index] = normalized
                            break
                    else:
                        entries.append(normalized)
                    message = "upserted"
                else:
                    entry_id = data["id"]
                    entries = [entry for entry in entries if entry["id"] != entry_id]
                    message = "deleted"
                updated_data = dict(ledger)
                updated_data["version"] = 1
                updated_data["entries"] = entries
                updated_data.setdefault("spend", list(ledger.get("spend", [])))
                _write_ledger(updated_data)
                _invalidate_cache_locked()
        return True, message
    except (OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
        return False, _error_text(exc)


def _spend_counts_this_month(item, month):
    """サブスクは毎月発生。従量は月スタンプが当月のものだけ合算する（無印=旧データは互換で当月扱い）。"""
    if item.get("kind") != "payg":
        return True
    stamped = item.get("month")
    return stamped is None or stamped == month


def _spend_summary(items, now=None):
    now = _now_value(now)
    month = _local_month(now)
    normalized = [copy.deepcopy(item) for item in items]
    counted = [item for item in normalized if _spend_counts_this_month(item, month)]
    total_jpy = sum(item["amount"] for item in counted if item["currency"] == "jpy")
    total_usd = sum(item["amount"] for item in counted if item["currency"] == "usd")
    return {
        "items": normalized,
        "totalJpy": round(total_jpy, 6),
        "totalUsd": round(total_usd, 6),
    }


def spend_apply(data):
    """実支出台帳を同一labelまたはidでRMWする。amount<0は削除。従量upsertは当月(YYYY-MM)を自動スタンプ。"""
    if not isinstance(data, dict):
        return False, "request must be an object"
    operation = data.get("op", "upsert")
    if operation not in ("upsert", "delete"):
        return False, "op must be upsert or delete"

    candidate = data.get("item")
    if candidate is None:
        candidate = data.get("entry")
    if candidate is None:
        candidate = data

    if operation == "delete":
        target_id = data.get("id", candidate.get("id") if isinstance(candidate, dict) else None)
        target_label = data.get("label", candidate.get("label") if isinstance(candidate, dict) else None)
        if target_id is not None and not _validate_id(target_id):
            return False, "id must match ^[a-z0-9_-]{1,32}$"
        if target_label is not None and (not isinstance(target_label, str) or not target_label.strip()):
            return False, "label must be a non-empty string"
        if target_id is None and target_label is None:
            return False, "id or label is required"
    else:
        raw_amount = candidate.get("amount") if isinstance(candidate, dict) else None
        if _valid_number(raw_amount) and raw_amount < 0:
            operation = "delete"
            target_id = candidate.get("id")
            target_label = candidate.get("label")
            if target_id is not None and not _validate_id(target_id):
                return False, "id must match ^[a-z0-9_-]{1,32}$"
            if target_label is not None and (not isinstance(target_label, str) or not target_label.strip()):
                return False, "label must be a non-empty string"
            if target_id is None and target_label is None:
                return False, "id or label is required"
        else:
            try:
                normalized = _normalized_spend_item(candidate)
            except (OverflowError, TypeError, ValueError) as exc:
                return False, str(exc)
            target_id = normalized["id"]
            target_label = normalized["label"]

    try:
        with _LOCK:
            with _file_flock(LEDGER_FILE):
                ledger = _read_ledger()
                items = list(ledger.get("spend", []))
                # 一括移行: 月無印の旧従量は「当月扱い」互換で毎月合算され続けるため、
                # 書き込み機会に当月をスタンプして翌月から自然に集計外へ落とす。
                now_month = _local_month(time.time())
                for item in items:
                    if item.get("kind") == "payg" and "month" not in item:
                        item["month"] = now_month
                if operation == "delete":
                    # idがあればidで、無ければlabelで対象1種だけ落とす（AND合成はlabel単独時に全滅する）。
                    if target_id is not None:
                        items = [item for item in items if item["id"] != target_id]
                    else:
                        items = [item for item in items if item["label"] != target_label]
                    message = "deleted"
                else:
                    if normalized["kind"] == "payg" and "month" not in normalized:
                        normalized["month"] = now_month
                    target_month = normalized.get("month") or now_month
                    replaced = False
                    for index, item in enumerate(items):
                        if item["id"] != normalized["id"] and item["label"] != normalized["label"]:
                            continue
                        # 従量は月ごとに別レコード。過去月の従量を同名upsertで再スタンプ・上書きしない
                        # （無印=旧データは当月扱いで置換対象）。
                        if (item.get("kind") == "payg"
                                and (item.get("month") or now_month) != target_month):
                            continue
                        normalized["id"] = item["id"]
                        items[index] = normalized
                        replaced = True
                        break
                    if not replaced:
                        # 置換しなかった月違いレコードとidが衝突し得るため、月サフィックスで一意化する。
                        existing_ids = {item["id"] for item in items}
                        if normalized["id"] in existing_ids:
                            stamp = target_month.replace("-", "")
                            candidate = f"{normalized['id'][:32 - len(stamp) - 1]}-{stamp}"
                            seq = 2
                            while candidate in existing_ids:
                                suffix = f"-{stamp}-{seq}"
                                candidate = normalized["id"][:32 - len(suffix)] + suffix
                                seq += 1
                            normalized["id"] = candidate
                        items.append(normalized)
                    message = "upserted"
                updated_data = dict(ledger)
                updated_data["version"] = 1
                updated_data["entries"] = list(ledger["entries"])
                updated_data["spend"] = items
                _write_ledger(updated_data)
                _invalidate_cache_locked()
        return True, message
    except (OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
        return False, _error_text(exc)


def fx_apply(data):
    """為替レートをflock下でread-modify-writeする。"""
    if not isinstance(data, dict):
        return False, "request must be an object"
    rate = data.get("jpyPerUsd")
    try:
        _validate_fx_rate(rate)
    except (TypeError, ValueError, OverflowError) as exc:
        return False, str(exc)

    try:
        with _LOCK:
            with _file_flock(LEDGER_FILE):
                ledger = _read_ledger()
                updated_data = dict(ledger)
                updated_data["version"] = 1
                updated_data["fx"] = {"jpyPerUsd": float(rate)}
                updated_data["entries"] = list(ledger.get("entries", []))
                updated_data["spend"] = list(ledger.get("spend", []))
                _write_ledger(updated_data)
                _invalidate_cache_locked()
        return True, "updated"
    except (OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
        return False, _error_text(exc)


def collect_ledger(now):
    """台帳を記載順の provider 配列へ変換する。ファイルが無ければ空配列。"""
    now = _now_value(now)
    ledger = _read_ledger()
    providers = []
    for entry in ledger["entries"]:
        total = entry["total"]
        used_percent = round((1 - entry["remaining"] / total) * 100, 1) if total > 0 else None
        providers.append({
            "id": entry["id"],
            "label": entry["label"],
            "kind": "ledger",
            "plan": entry.get("plan", ""),
            "status": "stale" if entry["updatedAt"] < now - LEDGER_STALE_SECONDS else "ok",
            "remaining": entry["remaining"],
            "total": total,
            "unit": entry.get("unit", ""),
            "note": entry.get("note", ""),
            "usedPercent": used_percent,
            "updatedAt": entry["updatedAt"],
        })
    return providers


def _safe_provider(collector, fallback, now):
    try:
        return collector(now)
    except Exception as exc:
        return fallback(_error_text(exc))


def _external_provider(provider_id, label, payload):
    return {
        "id": provider_id,
        "label": label,
        "kind": "external",
        **copy.deepcopy(payload),
    }


def _build_board(now, fetch_external=True):
    providers = [
        _safe_provider(collect_claude, _claude_unavailable, now),
        _safe_provider(collect_codex, _codex_unavailable, now),
        _safe_provider(collect_gemini, _gemini_unavailable, now),
    ]
    if fetch_external:
        xapi = collect_xapi(now)
        openai = collect_openai_cost(now)
    else:
        xapi = _external_cached("xapi", "X_BEARER_TOKEN", now)
        openai = _external_cached("openai", "OPENAI_ADMIN_KEY", now)
    providers.extend([
        _external_provider("xapi", "X API", xapi),
        _external_provider("openai", "OpenAI", openai),
    ])
    try:
        providers.extend(collect_ledger(now))
    except Exception:
        # 台帳は0件以上の可変 provider なので、壊れていても固定3 provider を生かす。
        pass
    try:
        ledger = _read_ledger()
        spend = _spend_summary(ledger.get("spend", []), now)
        fx = _normalized_fx(ledger)
    except Exception:
        # spendはローカル手入力の補助表示。壊れていても他のproviderを落とさない。
        spend = {"items": [], "totalJpy": 0, "totalUsd": 0}
        fx = {"jpyPerUsd": DEFAULT_JPY_PER_USD}
    return {"generatedAt": now, "providers": providers, "spend": spend, "fx": fx}


def _response(data, refreshing):
    return {
        "generatedAt": data["generatedAt"],
        "refreshing": bool(refreshing),
        "providers": copy.deepcopy(data["providers"]),
        "spend": copy.deepcopy(data.get("spend", {
            "items": [], "totalJpy": 0, "totalUsd": 0,
        })),
        "fx": copy.deepcopy(data.get("fx", {"jpyPerUsd": DEFAULT_JPY_PER_USD})),
    }


def _refresh_cache(now, version):
    global _REFRESHING, _REFRESH_THREAD
    try:
        built = _build_board(now, fetch_external=True)
        with _LOCK:
            if version == _CACHE_VERSION:
                _CACHE["data"] = built
    finally:
        with _LOCK:
            _REFRESHING = False
            _REFRESH_THREAD = None


def _external_needs_refresh(now):
    for name, key_name in (("xapi", "X_BEARER_TOKEN"), ("openai", "OPENAI_ADMIN_KEY")):
        secret, source_mtime = _secret_value(key_name)
        if not secret:
            continue
        cached = _EXT_CACHE.get(name)
        if not (isinstance(cached, dict) and cached.get("mtime") == source_mtime
                and isinstance(cached.get("at"), (int, float))
                and 0 <= now - cached["at"] < EXTERNAL_CACHE_TTL):
            return True
    return False


def _start_refresh_locked(now):
    global _REFRESHING, _REFRESH_THREAD
    if _REFRESHING:
        return
    _REFRESHING = True
    version = _CACHE_VERSION
    thread = threading.Thread(
        target=_refresh_cache,
        args=(now, version),
        name="status-board-refresh",
        daemon=True,
    )
    _REFRESH_THREAD = thread
    thread.start()


def status_board_json(now=None):
    """stale-while-revalidate のローカル resource board JSON を返す。"""
    global _REFRESHING, _REFRESH_THREAD
    now = _now_value(now)
    with _LOCK:
        cached = _CACHE.get("data")
        if cached is None:
            # 初回（または台帳更新直後）はローカル走査だけを同期実行する。
            # 外部APIは下のバックグラウンド更新で取得し、リクエストをブロックしない。
            cached = _build_board(now, fetch_external=False)
            _CACHE["data"] = cached
            if _external_needs_refresh(now):
                _start_refresh_locked(now)
                return _response(cached, True)
            return _response(cached, False)

        if now - cached["generatedAt"] >= CACHE_TTL:
            _start_refresh_locked(now)
            return _response(cached, True)
        if _external_needs_refresh(now):
            _start_refresh_locked(now)
            return _response(cached, True)
        return _response(cached, False)
