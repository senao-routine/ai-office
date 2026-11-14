"""Local, best-effort timeline storage. Only the daemon touches SQLite.

Hook files are the durable source: commit their byte cursors and the database
sequence together. Neither the SSE ring nor subscriber queues can lose history.
"""
from contextlib import closing
from datetime import date, datetime, timedelta
import hashlib
import json
import marshal
import math
import os
from pathlib import Path
import re
import sqlite3
from statistics import median
import sys
import threading
import time
import unicodedata

import office_events
from office_common import IncrementalTail, atomic_write_json, file_flock

DB_FILE = ".claude/office_timeline.sqlite"
BATCH_SECONDS = 1.0
MAX_COMMANDS = 2000
_LOCK = threading.Lock()
_WRITER = None
_FIELDS = ("sid", "vendor", "ev", "tool", "tgt", "kind", "nt", "sub", "task")
_SCHEMA = """
CREATE TABLE IF NOT EXISTS meta(k TEXT PRIMARY KEY, v TEXT);
CREATE TABLE IF NOT EXISTS sessions(
    sid TEXT PRIMARY KEY, vendor, cwdh, projectId, name, firstSeen REAL, lastSeen REAL);
CREATE TABLE IF NOT EXISTS events(
    id INTEGER PRIMARY KEY, ts REAL, day TEXT, sid, vendor, ev, tool, tgt, kind,
    nt, sub, task, ok INTEGER, seq INTEGER UNIQUE);
CREATE INDEX IF NOT EXISTS events_day_sid ON events(day,sid);
CREATE INDEX IF NOT EXISTS events_sid_ts ON events(sid,ts);
CREATE INDEX IF NOT EXISTS events_kind_ts ON events(kind,ts);
CREATE TABLE IF NOT EXISTS spans(
    id INTEGER PRIMARY KEY, sid, kind TEXT, label TEXT, start REAL, end REAL, day TEXT);
CREATE INDEX IF NOT EXISTS spans_sid_kind_start ON spans(sid,kind,start);
CREATE INDEX IF NOT EXISTS spans_day ON spans(day);
CREATE TABLE IF NOT EXISTS daily(day TEXT PRIMARY KEY, json TEXT, updatedAt REAL);
CREATE TABLE IF NOT EXISTS answers(
    spanId INTEGER PRIMARY KEY, ts REAL NOT NULL, waitedSec REAL NOT NULL);
"""


def _text(value):
    return value if isinstance(value, str) else ""


def _day(ts):
    return datetime.fromtimestamp(ts).date().isoformat()


def _time(value):
    if type(value) not in (int, float) or not math.isfinite(value):
        raise ValueError("invalid timestamp")
    _day(value)
    return float(value)


def _meta(db, key, default="0"):
    row = db.execute("SELECT v FROM meta WHERE k=?", (key,)).fetchone()
    return row[0] if row else default


def _set_meta(db, key, value):
    db.execute("INSERT OR REPLACE INTO meta VALUES (?,?)", (key, str(value)))


def _corrupt(exc):
    return (getattr(exc, "sqlite_errorcode", 0) & 255) in (
        sqlite3.SQLITE_CORRUPT, sqlite3.SQLITE_NOTADB)


def _connect(path):
    """Do not discard databases on lock, permission or disk-full errors."""
    for attempt in range(2):
        db = None
        try:
            # Create privately before SQLite opens it (also secures WAL/SHM).
            fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
            os.close(fd)
            path.chmod(0o600)
            db = sqlite3.connect(path, timeout=1.0)
            db.execute("PRAGMA busy_timeout=1000")
            if db.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                raise ValueError("corrupt timeline")
            db.execute("PRAGMA journal_mode=WAL")
            db.executescript(_SCHEMA)
            _set_meta(db, "version", "1")
            db.commit()
            return db
        except (sqlite3.DatabaseError, ValueError) as exc:
            if db is not None:
                db.close()
            if attempt or not (isinstance(exc, ValueError) or _corrupt(exc)):
                raise
            # Only this derived database and its SQLite sidecars are disposable.
            for suffix in ("", "-wal", "-shm"):
                Path(str(path) + suffix).unlink(missing_ok=True)
        except Exception:
            if db is not None:
                db.close()
            raise


def _prune(db, today):
    for table, days in (("events", 90), ("spans", 180), ("daily", 400)):
        cutoff = (today - timedelta(days=days)).isoformat()
        db.execute(f"DELETE FROM {table} WHERE day < ?", (cutoff,))
    db.execute("DELETE FROM meta WHERE k LIKE 'hook:%' AND v < ?",
               ((today - timedelta(days=90)).isoformat(),))
    db.execute("DELETE FROM meta WHERE k LIKE 'resolved:%' AND v < ?",
               ((today - timedelta(days=180)).isoformat(),))
    db.execute("DELETE FROM meta WHERE k LIKE 'cursor:%' AND substr(k,8) < ?",
               ((today - timedelta(days=90)).isoformat(),))
    db.execute("DELETE FROM answers WHERE spanId NOT IN (SELECT id FROM spans)")
    _set_meta(db, "pruned_day", today.isoformat())


def _count(db, ts, **counts):
    day = _day(ts)
    row = db.execute("SELECT json FROM daily WHERE day=?", (day,)).fetchone()
    data = json.loads(row[0]) if row else {}
    for key, value in counts.items():
        data[key] = data.get(key, 0) + value
    db.execute("INSERT OR REPLACE INTO daily VALUES (?,?,?)",
               (day, json.dumps(data, sort_keys=True), ts))


def _event(db, row, seq=None):
    db.execute("""INSERT INTO events
        (ts,day,sid,vendor,ev,tool,tgt,kind,nt,sub,task,ok,seq)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
               (row["ts"], _day(row["ts"]), *[row.get(k, "") for k in _FIELDS],
                int(bool(row.get("ok", True))), seq))
    counts = {"events": 1}
    if row["ev"] in ("hire", "turnCompleted"):
        counts[row["ev"]] = 1
    if (row["ev"] == "PostToolUse" and row.get("ok", True)
            and row.get("kind") in ("git:commit", "git:push")):
        counts[row["kind"]] = 1
    _count(db, row["ts"], **counts)


def _session(db, row, ts):
    sid = row["sid"]
    old = db.execute("SELECT lastSeen FROM sessions WHERE sid=?", (sid,)).fetchone()
    if old is None:
        db.execute("INSERT INTO sessions VALUES (?,?,?,?,?,?,?)",
                   (sid, row["vendor"], row.get("cwdh", ""), row.get("projectId") or row.get("cwdh", ""),
                    row.get("name", ""), ts, ts))
        _event(db, {"sid": sid, "vendor": row["vendor"], "ev": "hire", "ts": ts})
    else:
        db.execute("""UPDATE sessions SET firstSeen=MIN(firstSeen,?),
            lastSeen=MAX(lastSeen,?) WHERE sid=?""", (ts, ts, sid))
        if ts >= old[0]:
            for key in ("vendor", "cwdh", "name"):
                if row.get(key):
                    db.execute(f"UPDATE sessions SET {key}=? WHERE sid=?", (row[key], sid))
        # Hooks can be replayed through SessionEnd before earlier snapshots.
        # Only snapshots know the avatar ID; a newer hook's cwd hash must not
        # prevent that authoritative attribution from being restored.
        if row.get("projectId"):
            db.execute("UPDATE sessions SET projectId=? WHERE sid=?", (row["projectId"], sid))


def _open_span(db, sid, kind, label, ts):
    row = db.execute("""SELECT id FROM spans
        WHERE sid=? AND kind=? AND (?='ask' OR label=?) AND end IS NULL""",
                     (sid, kind, kind, label)).fetchone()
    if row is None:
        db.execute("INSERT INTO spans(sid,kind,label,start,day) VALUES (?,?,?,?,?)",
                   (sid, kind, label, ts, _day(ts)))


def _close_spans(db, sid, kind, ts, label=None):
    sql = "SELECT id,start FROM spans WHERE sid=? AND kind=? AND end IS NULL"
    args = [sid, kind]
    if label is not None:
        sql += " AND label=?"
        args.append(label)
    rows = db.execute(sql, args).fetchall()
    for span_id, start in rows:
        end = max(ts, math.nextafter(start, math.inf))
        db.execute("UPDATE spans SET end=? WHERE id=?", (end, span_id))
        ts = max(ts, end)
    return ts, bool(rows)


class _Commands:
    """Bound retries; mark discarded observation intervals instead of extending work."""
    def __init__(self):
        self.maxsize = MAX_COMMANDS
        self._pending = []
        self._lock = threading.Lock()

    def _merge(self, pending):
        if len(pending) > self.maxsize:
            states = [i for i, item in enumerate(pending) if item[0] == "states"]
            latest = max(states,
                         key=lambda i: (pending[i][1][-1], i), default=None)
            # Retained answers need the session identity even if every snapshot
            # containing it is discarded. Keep one metadata row per answer, not
            # a roster that would resurrect departed employees after recovery.
            known, answers = {}, []
            for kind, args in pending:
                if kind == "states":
                    rows, ts = args
                    for row in rows:
                        if row["sid"] not in known or ts >= known[row["sid"]][1]:
                            known[row["sid"]] = (row, ts)
                elif kind == "resolved":
                    project, sid, waited, ts, *saved = args
                    observed = saved[0] if saved else known.get(sid)
                    if observed is None and not sid:
                        observed = max((item for item in known.values()
                                        if item[0]["projectId"] == project),
                                       key=lambda item: (item[0]["attention"], item[1]), default=None)
                    answers.append((kind, (project, sid, waited, ts, observed)))
            if latest is not None:
                first = pending[states[0]]
                last = pending[latest]
                gaps = [args for kind, args in pending if kind == "gap"]
                start = min([first[1][-1], *(args[0] for args in gaps)])
                end = max([last[1][-1], *(args[1] for args in gaps)])
                # Preserve the first transition (e.g. working -> waiting), then
                # close spans there. The missing interval earns no active XP.
                pending = [first, ("gap", (start, end)),
                           *answers[-(self.maxsize - 3):], last]
            else:
                gaps = [(kind, args) for kind, args in pending if kind == "gap"]
                pending = gaps[:1] + answers[-(self.maxsize - bool(gaps)):]
        # Ordinary batches retain arrival order; compacted batches keep the
        # boundary observations and explicitly mark the interval between them.
        self._pending = pending

    def put_nowait(self, command):
        with self._lock:
            self._merge(self._pending + [command])

    def drain(self):
        with self._lock:
            pending, self._pending = self._pending, []
            return pending

    def restore(self, pending):
        with self._lock:
            self._merge(pending + self._pending)

    def qsize(self):
        with self._lock:
            return len(self._pending)


class _Timeline:
    def __init__(self, home):
        self.path = home / DB_FILE
        self.directory = home / office_events.EVENTS_DIR
        self.tail = IncrementalTail(sys.maxsize)
        self.commands = _Commands()
        self.stopped = threading.Event()
        self.thread = threading.Thread(target=self.run, name="office-timeline", daemon=True)
        self.last_error = None

    def hook(self, db, raw, position):
        if (not isinstance(raw, dict) or type(raw.get("v")) is not int or raw["v"] != 1
                or not isinstance(raw.get("sid"), str)
                or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", raw["sid"])
                or not isinstance(raw.get("ev"), str) or raw["ev"] not in office_events._EVENTS
                or not isinstance(raw.get("tool", ""), str)
                or not isinstance(raw.get("nt", ""), str)):
            return
        try:
            row = {k: _text(raw.get(k)) for k in (*_FIELDS, "cwdh")}
            row.update(ts=_time(raw["ts"]), vendor=row["vendor"] or "claude",
                       ok=bool(raw.get("ok", True)))
        except (ValueError, TypeError, OverflowError, OSError, KeyError):
            return
        if not row["sid"] or not row["ev"]:
            return
        # Never fingerprint or persist unknown/free-text fields, including tsub.
        fingerprint = hashlib.sha256(json.dumps(row, sort_keys=True).encode()).hexdigest()
        key = "hook:" + position + ":" + fingerprint
        if _meta(db, key, "") or _day(row["ts"]) < (date.today() - timedelta(days=90)).isoformat():
            return
        _set_meta(db, key, _day(row["ts"]))
        # Migrate existing D8 databases without re-inserting their saved hooks.
        # New keys include the line position, so identical distinct lines count.
        if _meta(db, "hook:" + fingerprint, ""):
            return
        seq = int(_meta(db, "last_seq")) + 1
        _set_meta(db, "last_seq", seq)
        _session(db, row, row["ts"])
        _event(db, row, seq)
        sid, ts, ev = row["sid"], row["ts"], row["ev"]
        if ev == "TaskCreated" and row["task"]:
            _open_span(db, sid, "task", row["task"], ts)
        elif ev == "TaskCompleted" and row["task"]:
            _, closed = _close_spans(db, sid, "task", ts, row["task"])
            if closed:
                _count(db, ts, taskCompleted=1)
        elif ev == "PermissionRequest" or (ev == "Notification" and row["nt"] in (
                "permission_prompt", "idle_prompt", "agent_needs_input")):
            _open_span(db, sid, "ask", "attention", ts)
        elif ev == "SessionEnd":
            for kind in ("state", "ask", "turn"):
                _close_spans(db, sid, kind, ts)

    def states(self, db, employees, now):
        # Concurrent scans can finish out of order. Do not rewind a snapshot.
        if now < float(_meta(db, "states_at", "-inf")):
            return
        _set_meta(db, "states_at", now)
        present = set()
        for row in employees:
            sid, state = row["sid"], row["state"]
            present.add(sid)
            _session(db, row, now)
            old = db.execute("""SELECT label FROM spans
                WHERE sid=? AND kind='state' AND end IS NULL""", (sid,)).fetchone()
            if old is None or old[0] != state:
                ts, _ = _close_spans(db, sid, "state", now)
                _open_span(db, sid, "state", state, ts)
                if row["vendor"] == "codex":
                    if old and old[0] == "working" and state == "waiting":
                        _event(db, {"sid": sid, "vendor": "codex", "ev": "turnCompleted", "ts": ts})
                    if state != "working":
                        _close_spans(db, sid, "turn", ts)
                    else:
                        _open_span(db, sid, "turn", "codex", ts)
            if row["attention"]:
                _open_span(db, sid, "ask", "attention", now)
                # Raw PermissionRequest hooks include transient auto-approvals.
                # Only parser observations (after ASK_GRACE) confirm a wait.
                db.execute("""UPDATE spans SET label='confirmed'
                    WHERE sid=? AND kind='ask' AND end IS NULL""", (sid,))
            else:
                _close_spans(db, sid, "ask", now)
        for (sid,) in db.execute("SELECT sid FROM spans WHERE kind='state' AND end IS NULL").fetchall():
            if sid not in present:
                for kind in ("state", "ask", "turn"):
                    _close_spans(db, sid, kind, now)

    def gap(self, db, start, end):
        if end < float(_meta(db, "states_at", "-inf")):
            return
        for sid, in db.execute("SELECT DISTINCT sid FROM spans WHERE end IS NULL").fetchall():
            for kind in ("state", "ask", "turn"):
                _close_spans(db, sid, kind, start)
        _set_meta(db, "states_at", end)

    def resolved(self, db, project_id, sid, waited, now, observed=None):
        if observed is not None:
            row, ts = observed
            _session(db, row, ts)
            sid = sid or row["sid"]
        if not sid:
            row = db.execute("""SELECT s.sid FROM sessions s JOIN spans p ON p.sid=s.sid
                WHERE s.projectId=? AND p.kind='ask' ORDER BY p.start DESC LIMIT 1""",
                             (project_id,)).fetchone()
            if row is None:
                return
            sid = row[0]
        # scan_office normally closes the ask before the watcher sees resolution.
        row = db.execute("""SELECT id,start,end FROM spans
            WHERE sid=? AND kind='ask' ORDER BY start DESC,id DESC LIMIT 1""", (sid,)).fetchone()
        start = now - waited
        if row and (row[2] is None or row[2] >= start):
            span_id, start, end = row
            if end is None:
                _close_spans(db, sid, "ask", now)
        else:
            start = min(start, math.nextafter(now, -math.inf))
            cursor = db.execute("""INSERT INTO spans(sid,kind,label,start,end,day)
                VALUES (?,'ask','attention',?,?,?)""", (sid, start, now, _day(start)))
            span_id = cursor.lastrowid
        # Repeated watcher callbacks for the same interval must not add XP twice.
        key = "resolved:" + hashlib.sha256(json.dumps([sid, start]).encode()).hexdigest()
        if not _meta(db, key, ""):
            _set_meta(db, key, _day(now))
            _count(db, now, askResolved=1, waitedSec=waited)
            # Keep the actual answer time/duration, rather than interpreting a
            # session disappearance (which also closes ask spans) as an answer.
            db.execute("INSERT OR IGNORE INTO answers VALUES (?,?,?)", (span_id, now, waited))

    def file_hooks(self, db):
        cutoff = (date.today() - timedelta(days=90)).isoformat()
        for path in sorted(self.directory.glob("*.jsonl")):
            if not re.fullmatch(r"\d{4}-\d{2}-\d{2}\.jsonl", path.name) or path.stem < cutoff:
                continue
            try:
                date.fromisoformat(path.stem)
                info = path.stat()
            except (OSError, ValueError):
                continue
            key = path.stem
            cursor = json.loads(_meta(db, "cursor:" + key, "{}"))
            identity = [info.st_dev, info.st_ino]
            self.tail.drop(key)
            line_no = 0
            if (cursor.get("identity") == identity
                    and 0 <= cursor.get("offset", -1) <= info.st_size):
                self.tail.offsets[key] = {"offset": cursor["offset"], "seen": 0.0}
                line_no = cursor["line"]
            # IncrementalTail only consumes complete lines. On a transaction
            # failure the next pass reloads the committed cursor, not this tail.
            for line in self.tail.read(path, key):
                line_no += 1
                try:
                    row = json.loads(line)
                except (ValueError, RecursionError):
                    continue
                self.hook(db, row, f"{key}:{line_no}")
            state = self.tail.state(key)
            if state is not None:
                _set_meta(db, "cursor:" + key, json.dumps({
                    "identity": identity, "offset": state["offset"], "line": line_no}))
            self.tail.drop(key)

    def batch(self, db, commands):
        with db:
            self.file_hooks(db)
            for kind, args in commands:
                if kind == "states":
                    self.states(db, *args)
                elif kind == "resolved":
                    self.resolved(db, *args)
                elif kind == "gap":
                    self.gap(db, *args)
            today = date.today()
            if _meta(db, "pruned_day", "") != today.isoformat():
                _prune(db, today)

    def run(self):
        db = None
        try:
            while True:
                stopping = self.stopped.wait(BATCH_SECONDS)
                if os.environ.get("OFFICE_TIMELINE") == "0" or not self.directory.is_dir():
                    if stopping:
                        break
                    continue  # No file creation until hooks have created their directory.
                commands = []
                try:
                    if db is None:
                        db = _connect(self.path)
                        with db:
                            _prune(db, date.today())
                    commands = self.commands.drain()
                    self.batch(db, commands)
                    self.last_error = None
                except Exception as exc:
                    self.last_error = type(exc).__name__
                    self.commands.restore(commands)
                    if db is not None:
                        db.close()
                        db = None
                    # No hook retry buffer: uncommitted file cursors replay it.
                finally:
                    commands.clear()
                if stopping:
                    break
        finally:
            if db is not None:
                db.close()


def start(home):
    """Start once; wait without writing until the hook directory exists."""
    global _WRITER
    try:
        if os.environ.get("OFFICE_TIMELINE") == "0":
            return
        home = Path(home)
        with _LOCK:
            if _WRITER is not None:
                return
            writer = _Timeline(home)
            writer.thread.start()
            _WRITER = writer
    except Exception:
        return


def stop():
    global _WRITER
    with _LOCK:
        writer = _WRITER
        if writer is None:
            return
        writer.stopped.set()
        writer.thread.join()
        _WRITER = None


def record_states(employees, now):
    try:
        writer = _WRITER
        if writer is None or os.environ.get("OFFICE_TIMELINE") == "0":
            return
        now = _time(now)
        rows = []
        for employee in employees:
            sid = _text(employee.get("session"))
            state = employee.get("state")
            if not sid or state not in ("working", "waiting", "resting"):
                continue
            cwd = unicodedata.normalize("NFC", _text(employee.get("cwd")))
            cwdh = hashlib.sha1(cwd.encode()).hexdigest()[:12] if cwd else ""
            rows.append({"sid": sid, "vendor": _text(employee.get("vendor")) or "claude",
                         "cwdh": cwdh, "projectId": _text(employee.get("projectId")) or cwdh,
                         "name": _text(employee.get("title") or employee.get("disp") or employee.get("dept")),
                         "state": state,
                         "attention": bool(employee.get("ask") or employee.get("question")
                                           or employee.get("approvalMin"))})
        writer.commands.put_nowait(("states", (rows, now)))
    except Exception:
        pass


def record_ask_resolved(projectId, sid, waited_sec, now):
    try:
        writer = _WRITER
        if writer is None or os.environ.get("OFFICE_TIMELINE") == "0":
            return
        now = _time(now)
        waited = max(0.0, float(waited_sec))
        if not math.isfinite(waited):
            return
        writer.commands.put_nowait(("resolved", (_text(projectId), _text(sid), waited, now)))
    except Exception:
        pass


# D9 readers use separate read-only connections. They never start a writer or
# inspect transcripts. Synthetic active/askResolved facts contain numbers only.
# R115: readers select only the rows a result needs (never every hook row) and
# cache the small results for 60 seconds, never the facts behind them — and a
# result only until `now` reaches the first row already stored with a later time.
_READ_LOCK = threading.Lock()
_READ_CACHE = {}          # (kind, home, day(now), …) → (at, until, marshal of the result)
READ_TTL = 60
_SEEN_LOCK = threading.Lock()
SEEN_FILE = ".claude/office_seen.json"
_RATES = {"tasksDone": 10, "commits": 5, "asksAnswered": 2,
          "fastAnswers": 1, "activeMin": 0.2, "hires": 5, "turnsCompleted": 3}


def _number(value):
    return float(value) if type(value) in (int, float) and math.isfinite(value) and value >= 0 else 0.0


def _project(value):
    # Project ids are opaque identifiers, never filesystem paths.
    value = _text(value)
    return value if re.fullmatch(r"[A-Za-z0-9_-]{1,128}", value) else "unknown"


def _counts():
    return dict.fromkeys(_RATES, 0)


def _measure(events):
    counts, tools, waits, tasks, hires = _counts(), {}, [], set(), set()
    for row in events:
        ev = row.get("ev")
        sid = row.get("sid", "")
        if ev == "TaskCompleted" and row.get("ok", True):
            key = (sid, row.get("task"))
            if not row.get("task") or key not in tasks:
                counts["tasksDone"] += 1
                tasks.add(key)
        elif ev == "PostToolUse" and row.get("ok", True):
            tool = _text(row.get("tool"))
            if tool:
                tools[tool] = tools.get(tool, 0) + 1
            counts["commits"] += row.get("kind") == "git:commit"
        elif ev == "hire" and sid not in hires:
            counts["hires"] += 1
            hires.add(sid)
        elif ev == "turnCompleted" and row.get("vendor") == "codex":
            counts["turnsCompleted"] += 1
        elif ev == "askResolved":
            waited = _number(row.get("waitedSec"))
            counts["asksAnswered"] += 1
            counts["fastAnswers"] += waited <= 300
            waits.append(waited)
        elif ev == "active":
            counts["activeMin"] += _number(row.get("activeMin"))
    return counts, tools, waits


def _growth(counts):
    # Fast answers receive the ordinary 2 XP plus a 1 XP bonus (3 total).
    breakdown = {key: {"count": round(counts[key], 9), "xp": round(counts[key] * rate, 9)}
                 for key, rate in _RATES.items()}
    xp = round(sum(item["xp"] for item in breakdown.values()), 9)
    return {"xp": xp, "level": math.isqrt(int(xp // 100)), "breakdown": breakdown}


def _iso_day(value):
    if not isinstance(value, str):
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def _streak(events, today, days=None):
    """`days`: ISO days with activity whose rows are not in `events` (R115)."""
    active, waits = set(), {}
    for row in events:
        parsed = _iso_day(row.get("day"))
        if parsed is None or parsed > today:
            continue
        active.add(parsed)
        if row.get("ev") == "askResolved":
            waits.setdefault(parsed, []).append(_number(row.get("waitedSec")))
    for day in days or ():
        parsed = _iso_day(day)
        if parsed is not None and parsed <= today:
            active.add(parsed)
    if not active:
        return 0
    current, first, streak = today, min(active), 0
    while current >= first:
        if waits.get(current) and median(waits[current]) >= 900:
            break
        streak += 1
        current -= timedelta(days=1)
    return streak


def growth_from_events(events, today=None, days=None):
    """Pure XP calculation: no I/O, clock, randomness, or token accounting.

    Accept hook/hire/turnCompleted rows plus numeric `active` (activeMin) and
    `askResolved` (waitedSec) facts. Rows carry sid, projectId and ISO day.
    `today` bounds streak15; when omitted the latest supplied day is used.
    Days without answers count from the first observed day, never before it.
    `days` (optional) adds ISO days with activity whose rows were not supplied:
    rows that count nothing need not be read just to date the first day.
    """
    events = list(events)
    days = list(days or ())
    groups, ids = {}, {}
    for row in events:
        value = row.get("projectId")
        if type(value) is str:      # Ids repeat across thousands of rows.
            pid = ids.get(value)
            if pid is None:
                pid = ids[value] = _project(value)
        else:
            pid = _project(value)
        groups.setdefault(pid, []).append(row)
    projects, total = {}, _counts()
    for pid, rows in sorted(groups.items()):
        counts, _, _ = _measure(rows)
        projects[pid] = _growth(counts)
        for key in total:
            total[key] += counts[key]
    office = _growth(total)
    office["nextAt"] = 100 * (office["level"] + 1) ** 2
    if today is None:
        today = max([row.get("day", "") for row in events] + days, default="")
    today = date.fromisoformat(today) if isinstance(today, str) and today else today
    return {"byProject": projects, "office": office,
            "streak15": _streak(events, today, days) if today else 0}


def _read_seen(home):
    try:
        value = json.loads((Path(home) / SEEN_FILE).read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError, UnicodeError):
        return {}


def mark_seen(home, now=None):
    now = _time(time.time() if now is None else now)
    path = Path(home) / SEEN_FILE
    with _SEEN_LOCK, file_flock(path):
        data = _read_seen(home)
        data["seenAt"] = max(now, _number(data.get("seenAt")))
        atomic_write_json(path, data)
        return {"ok": True, "seenAt": data["seenAt"]}


def _reader(home):
    if os.environ.get("OFFICE_TIMELINE") == "0":
        raise OSError("timeline disabled")
    db = sqlite3.connect((Path(home) / DB_FILE).resolve().as_uri() + "?mode=ro", uri=True, timeout=0.1)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA query_only=ON")
    db.execute("BEGIN")
    return db


_EVIDENCE_LOCK = threading.Lock()
_EVIDENCE_CACHE = {}          # home → (at, {sid: (kind, ts)})
EVIDENCE_WINDOW = 24 * 3600
EVIDENCE_TTL = 5.0


def evidence_for(home, sids, now):
    """R98: 直近 24 時間の hook 記録から、セッションごとの**最後の証拠**を返す。

    値は committed（git commit が通った）／tested（テスト系コマンドが通った）／failed（どちらかが失敗）。
    「通った」の根拠は hook が記録した終了コード。**終了コードがランナー自身のものでない形**
    （`pytest | tail`・`pytest || true`）は hook 側で分類しない＝ここには来ない（分からないときは何も言わない）。
    24h に記録が無いセッションはキーごと無し（UI は「—」）。本文は一切持たない（kind と ok と ts だけ）。
    1 クエリ・5 秒キャッシュ＝office_json のポーリング（3 秒）ごとに SQLite を叩かない。
    失敗はすべて空 dict（office_json を落とさない）。"""
    try:
        now = _time(now)
        key = str(Path(home).resolve())
        with _EVIDENCE_LOCK:
            cached = _EVIDENCE_CACHE.get(key)
            if cached is None or not 0 <= now - cached[0] < EVIDENCE_TTL:
                last = {}
                with closing(_reader(home)) as db:
                    rows = db.execute("""SELECT sid, kind, ok, ts FROM events
                        WHERE ts > ? AND kind IN ('git:commit', 'test')
                          AND ev IN ('PostToolUse', 'PostToolUseFailure')
                        ORDER BY ts, id""", (now - EVIDENCE_WINDOW,))
                    for row in rows:
                        kind = "failed" if not row["ok"] else ("committed" if row["kind"] == "git:commit" else "tested")
                        last[row["sid"]] = (kind, row["ts"])
                cached = (now, last)
                if len(_EVIDENCE_CACHE) >= 8:
                    _EVIDENCE_CACHE.pop(next(iter(_EVIDENCE_CACHE)))
                _EVIDENCE_CACHE[key] = cached
        out = {}
        for sid in sids:
            hit = cached[1].get(sid)
            if hit:
                out[sid] = {"kind": hit[0], "ago": int(max(0, now - hit[1]))}
        return out
    except Exception:
        return {}


def timeline_json(home, since=0, sid="", limit=200):
    """Newest `limit` events after an epoch timestamp, in chronological order."""
    since = _time(since)
    if since < 0 or not isinstance(sid, str) or (sid and not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", sid)):
        raise ValueError("bad since or sid")
    if type(limit) is not int or not 1 <= limit <= 500:
        raise ValueError("bad limit")
    rows = []
    try:
        with closing(_reader(home)) as db:
            rows = [dict(row) for row in db.execute("""SELECT e.*,s.projectId,s.name FROM events e
                LEFT JOIN sessions s ON s.sid=e.sid WHERE e.ts>? AND (?='' OR e.sid=?)
                ORDER BY e.ts DESC,e.id DESC LIMIT ?""", (since, sid, sid, limit))]
    except (OSError, sqlite3.Error):
        pass
    return {"events": list(reversed(rows)), "since": since, "limit": limit}


# Only these event rows change XP counts (`_measure`). Event rows carry hook
# names, hire or turnCompleted — never the synthetic active/askResolved facts.
_XP_EVENTS = ("(e.ev IN ('TaskCompleted','hire','turnCompleted')"
              " OR (e.ev='PostToolUse' AND e.kind='git:commit'))")
# Per row only what `_measure` reads for its ev; other fields are None. Days and
# times stay in SQL (`days`, windows); projectId comes from the sessions rows.
_EVENT_ROWS = """SELECT e.sid,e.ev,e.ok,CASE WHEN e.ev='turnCompleted' THEN e.vendor END,
    CASE WHEN e.ev='PostToolUse' THEN e.tool END,CASE WHEN e.ev='PostToolUse' THEN e.kind END,
    CASE WHEN e.ev='TaskCompleted' THEN e.task END FROM events e"""


def _tuples(db, sql, args=()):
    # Plain tuples: thousands of rows, read once (sqlite3.Row costs more).
    cursor = db.cursor()
    cursor.row_factory = None
    return cursor.execute(sql, args)


def _event_facts(db, sessions, where, args):
    facts, projects = [], {}
    for sid, ev, ok, vendor, tool, kind, task in _tuples(db, _EVENT_ROWS + where, args):
        pid = projects.get(sid)
        if pid is None:
            pid = projects[sid] = _project(sessions.get(sid, {}).get("projectId"))
        facts.append({"sid": sid, "ev": ev, "ok": ok, "vendor": vendor, "tool": tool,
                      "kind": kind, "task": task, "projectId": pid})
    return facts


def _sessions(db):
    return {sid: {"sid": sid, "vendor": vendor, "projectId": project, "name": name, "lastSeen": last}
            for sid, vendor, project, name, last in _tuples(
                db, "SELECT sid,vendor,projectId,name,lastSeen FROM sessions")}


def _resolved_key(sid, start):
    return "resolved:" + hashlib.sha256(json.dumps([sid, start]).encode()).hexdigest()


def _span_scan(db, sessions, now, window=None):
    """Span facts by the rules of reading every row: state pieces split at local
    midnight (an open span counts only until min(now, lastSeen)) and answered
    asks. Only pieces inside `window` (start, end) become dicts.

    Returns (facts, stops, active, present):
      facts    pieces clipped to `window`, then every askResolved fact; each kind
               in span order (no result depends on how the two interleave)
      stops    {ISO day: earliest piece end}: a day has a piece by t iff stops[day] <= t
      active   {projectId: activeMin} added piece by piece, as `_measure` adds facts
      present  {projectId: base fact} for every project with a piece
    """
    bases = {}

    def base(sid):
        found = bases.get(sid)
        if found is None:
            session = sessions[sid]
            found = bases[sid] = {"sid": sid, "vendor": session["vendor"],
                                  "projectId": _project(session["projectId"])}
        return found

    first, last = window if window is not None else (math.inf, -math.inf)
    facts, stops, active, present, midnights = [], {}, {}, {}, {}
    # The last piece's day as naive local datetimes [00:00, next 00:00): a wall
    # clock inside it has that date, whatever the zone does, so .date() is skipped.
    lo = hi = datetime.max
    # A flag instead of the label, which only a piece that can reach the window needs.
    for sid, working, label, start, end in _tuples(db, """SELECT sid,label='working',
            CASE WHEN start<? AND (end IS NULL OR end>?) THEN label END,start,end
            FROM spans WHERE kind='state' ORDER BY id""", (last, first)):
        if sid not in sessions or start is None:
            continue
        # An unclosed span is only observed through the last scan. A
        # stopped daemon must not accrue an entire night of active XP.
        end = min(now, end if end is not None else sessions[sid]["lastSeen"])
        span = base(sid)
        pid = span["projectId"]
        while start < end:
            clock = datetime.fromtimestamp(start)
            if not lo <= clock < hi:
                day = clock.date()
                midnight = midnights.get(day)
                if midnight is None:
                    after = datetime.combine(day + timedelta(days=1), datetime.min.time())
                    midnight = midnights[day] = (datetime.combine(day, datetime.min.time()), after,
                                                 after.timestamp(), day.isoformat())
                lo, hi, boundary, iso = midnight
            stop = min(end, boundary)
            if iso not in stops or stop < stops[iso]:
                stops[iso] = stop
            present.setdefault(pid, span)
            if working:
                active[pid] = active.get(pid, 0) + _number((stop - start) / 60)
            if window is not None:
                left, right = max(first, start), min(last, stop)
                if left < right:
                    facts.append(dict(span, ev="active" if working else "state", state=label,
                                      start=left, ts=right, day=iso,
                                      activeMin=(right - left) / 60 if working else 0))
            start = stop

    answered, rows = set(), []
    # A D8 database can be read before its writer migrates the schema.
    if db.execute("SELECT 1 FROM sqlite_master WHERE name='answers'").fetchone():
        answered = set(_tuples(db, "SELECT sid,start FROM spans WHERE id IN (SELECT spanId FROM answers)"))
        rows = _tuples(db, """SELECT p.id,p.sid,p.start,a.ts,a.waitedSec FROM spans p
            JOIN answers a ON a.spanId=p.id WHERE p.kind='ask'""").fetchall()
    # Every key the lookup below can match (LIKE 'resolved:%' scanned all of meta).
    # An unanswered interval never has an answered interval's key, so only the
    # rest can match; usually none are left and no unanswered ask is read.
    resolved = set(row[0] for row in _tuples(db, "SELECT k FROM meta WHERE k>='resolved:' AND k<'resolved;'"))
    if resolved:
        resolved -= {_resolved_key(sid, start) for sid, start in answered}
    if resolved:
        asks = _tuples(db, "SELECT id,sid,start,end FROM spans WHERE kind='ask' ORDER BY id").fetchall()
        latest = {(sid, start): span_id for span_id, sid, start, _ in asks}
        rows += [(span_id, sid, start, end, end - start) for span_id, sid, start, end in asks
                 if end is not None and (sid, start) not in answered
                 and latest[(sid, start)] == span_id and _resolved_key(sid, start) in resolved]
    rows.sort(key=lambda row: row[0])
    for _, sid, start, ts, waited in rows:
        if sid in sessions and start is not None:
            facts.append(dict(base(sid), ev="askResolved", ts=ts, day=_day(ts), waitedSec=waited))
    return facts, stops, active, present


def _event_days(db, bound, today):
    """Stored days up to `today` with an event at or before `bound`: one index
    probe per day instead of reading the rows (streaks need days, not rows)."""
    days = []
    day = db.execute("SELECT MIN(day) FROM events").fetchone()[0]
    while day is not None:
        parsed = _iso_day(day)
        if parsed is not None and parsed <= today and db.execute(
                "SELECT 1 FROM events WHERE day=? AND ts<=? LIMIT 1", (day, bound)).fetchone():
            days.append(day)
        day = db.execute("SELECT MIN(day) FROM events WHERE day>?", (day,)).fetchone()[0]
    return days


def _next_event_time(db, bound, strict):
    """Earliest stored event time after `bound` (`strict`) or at or after it, of
    any ev: one events_kind_ts probe per stored kind instead of a pass over the
    rows (a database its writer has not migrated yet is read once instead)."""
    op = ">" if strict else ">="
    if db.execute("SELECT 1 FROM sqlite_master WHERE type='index' AND name='events_kind_ts'").fetchone() is None:
        times = [db.execute(f"SELECT MIN(ts) FROM events WHERE ts{op}?", (bound,)).fetchone()[0]]
    else:
        times = [db.execute(f"SELECT MIN(ts) FROM events WHERE kind IS NULL AND ts{op}?", (bound,)).fetchone()[0]]
        kind = db.execute("SELECT MIN(kind) FROM events").fetchone()[0]
        while kind is not None:
            times.append(db.execute(f"SELECT MIN(ts) FROM events WHERE kind=? AND ts{op}?",
                                    (kind, bound)).fetchone()[0])
            kind = db.execute("SELECT MIN(kind) FROM events WHERE kind>?", (kind,)).fetchone()[0]
    return min((value for value in times if value is not None), default=math.inf)


def _xp_facts(db, sessions, now):
    """Facts XP needs at `now`, the days of state pieces and the first answer
    time after `now`. Counted event rows; per project one `active` fact with the
    summed minutes (or a `state` fact, counting nothing, when it only waited or
    rested); askResolved facts; and one uncounted row for a project with nothing
    else — byProject keeps every project that had any fact, as when every row
    was read."""
    facts = _event_facts(db, sessions, " WHERE e.ts<=? AND " + _XP_EVENTS + " ORDER BY e.ts,e.id", (now,))
    spans, stops, active, present = _span_scan(db, sessions, now)
    facts += [row for row in spans if row["ts"] <= now]
    later = min((row["ts"] for row in spans if row["ts"] > now), default=math.inf)
    for pid, base in present.items():
        facts.append(dict(base, ev="active", activeMin=active[pid]) if pid in active
                     else dict(base, ev="state"))
    projects = {row["projectId"] for row in facts}
    # A session with a hire row by `now` is already among the facts above.
    for (sid,) in _tuples(db, """SELECT d.sid FROM (SELECT DISTINCT sid FROM events WHERE ts<=?) d
            WHERE NOT EXISTS (SELECT 1 FROM events h WHERE h.sid IS d.sid AND h.ev='hire' AND h.ts<=?)
            ORDER BY d.sid""", (now, now)).fetchall():
        project = _project(sessions.get(sid, {}).get("projectId"))
        if project not in projects:
            projects.add(project)
            facts += _event_facts(db, sessions, " WHERE e.sid IS ? AND e.ts<=? ORDER BY e.ts,e.id LIMIT 1",
                                  (sid, now))
    return facts, list(stops), later


def _snapshot(home, now):
    """XP facts at `now`, the activity days they leave out (stored days of
    events up to `now`, days of state pieces) and `until`, the first stored
    time after `now` (an event of any ev or an answer): before it a later `now`
    adds no stored row (state pieces stay as observed at `now`, as a cached
    snapshot kept them). R115: read through indexes on each call — never every
    row, never cached (results are)."""
    try:
        with closing(_reader(home)) as db:
            sessions = _sessions(db)
            facts, days, later = _xp_facts(db, sessions, now)
            return (sessions, facts, _event_days(db, now, date.fromisoformat(_day(now))) + days,
                    min(later, _next_event_time(db, now, strict=True)))
    except (OSError, sqlite3.Error):
        return None   # Do not cache absence during writer startup/recovery.


def _cached(key, now, compute):
    """Keep results (serialized, never facts) for at most 60 seconds, reset at
    midnight, and only while `now` is before their `until`: a row already
    stored with a later time joins once `now` reaches it, as when each call
    filtered cached rows by its own `now`. Callers get their own copy.
    compute() is None while unreadable, else (result, until)."""
    with _READ_LOCK:
        hit = _READ_CACHE.get(key)
        if hit is not None and 0 <= now - hit[0] < READ_TTL and now < hit[1]:
            return marshal.loads(hit[2])
        computed = compute()
        if computed is None:
            return None
        result, until = computed
        if len(_READ_CACHE) >= 16:
            _READ_CACHE.pop(next(iter(_READ_CACHE)))
        _READ_CACHE[key] = (now, until, marshal.dumps(result))
        return result


def growth_json(home, now=None):
    now = time.time() if now is None else now
    today = _day(now)
    result = None
    if os.environ.get("OFFICE_TIMELINE") != "0":
        def compute():
            snapshot = _snapshot(home, now)
            return snapshot and (growth_from_events(snapshot[1], today, snapshot[2]), snapshot[3])
        result = _cached(("growth", str(Path(home).resolve()), today), now, compute)
    return result if result is not None else growth_from_events([], today)


def _near(day, delta):
    try:
        return (day + timedelta(days=delta)).isoformat()
    except OverflowError:
        return (date.min if delta < 0 else date.max).isoformat()


def _digest_read(home, day, parsed, start, end, now):
    """The rows of a digest and `until` (see `_snapshot`): None while unreadable."""
    try:
        with closing(_reader(home)) as db:
            sessions = _sessions(db)
            spans, stops, _, _ = _span_scan(db, sessions, now, (start, end))
            # The window is [start, end) on the clock. Stored days are the local
            # date when written, so allow any change of UTC offset (two days).
            events = _event_facts(db, sessions, " WHERE e.day BETWEEN ? AND ? AND e.ts>=? AND e.ts<?"
                                  " ORDER BY e.ts,e.id", (_near(parsed, -2), _near(parsed, 2), start, end)
                                  ) if start < end else []
            days = _event_days(db, end, parsed) + [iso for iso, stop in stops.items() if stop <= end]
            observed = (day in stops or any(row["day"] == day for row in spans)
                        or db.execute("SELECT 1 FROM events WHERE day=? LIMIT 1", (day,)).fetchone() is not None)
            until = math.inf    # A day that is over ends at its midnight for any later `now`.
            if end == now:
                # The window [start, end) and the streak (ts <= end) end at `now`: a
                # stored row at `end` joins once `now` passes it, a later one once
                # `now` reaches it (an event of any ev or an answer).
                first = min([_next_event_time(db, end, strict=False)]
                            + [row["ts"] for row in spans if row["ev"] == "askResolved" and row["ts"] >= end])
                until = max(first, math.nextafter(end, math.inf))
    except (OSError, sqlite3.Error):
        return None
    return (sessions, events, spans, days, observed), until


def _digest(day, parsed, since, start, end, read):
    metadata, events, spans, days, observed = read or ({}, [], [], [], False)
    # `spans` holds pieces already clipped to [start, end) and every askResolved.
    facts = list(events) + [row for row in spans
                            if row["ev"] != "askResolved" or start <= row["ts"] < end]
    growth = growth_from_events(facts, day)
    # Streak uses whole days/history, independently of the unread `since` view.
    growth["streak15"] = _streak([row for row in spans if row["ts"] <= end], parsed, days)

    def metrics(rows):
        counts, tools, waits = _measure(rows)
        return {**{key: round(value, 9) for key, value in counts.items()}, "tools": tools,
                "waitingMin": round(sum((row["ts"] - row["start"]) / 60 for row in rows
                                        if row.get("state") == "waiting"), 9),
                "avgWaitSec": sum(waits) / len(waits) if waits else 0,
                "medianWaitSec": median(waits) if waits else 0}

    by_sid = {}
    for row in facts:
        by_sid.setdefault(row["sid"], []).append(row)
    sessions = []
    for sid in sorted(by_sid):
        meta = metadata.get(sid, {})
        sessions.append({"sid": sid, "vendor": meta.get("vendor", ""), "name": meta.get("name", ""),
                         "projectId": _project(meta.get("projectId")), **metrics(by_sid[sid])})
    totals = metrics(facts)
    totals.update(xp=growth["office"]["xp"], level=growth["office"]["level"])
    return {"day": day, "since": since, "sessions": sessions, "totals": totals,
            "hires": totals["hires"], "streak15": growth["streak15"], "growth": growth,
            "available": bool(read and observed)}


def digest_json(home, day=None, since=None, now=None):
    now = _time(time.time() if now is None else now)
    day = _day(now) if day is None else day
    if not isinstance(day, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", day):
        raise ValueError("bad day")
    parsed = date.fromisoformat(day)
    since = _time(_number(_read_seen(home).get("seenAt")) if since is None else since)
    if since < 0:
        raise ValueError("bad since")
    start = max(since, datetime.combine(parsed, datetime.min.time()).timestamp())
    end = min(now, datetime.combine(parsed + timedelta(days=1), datetime.min.time()).timestamp())
    result = None
    if os.environ.get("OFFICE_TIMELINE") != "0":
        def compute():
            found = _digest_read(home, day, parsed, start, end, now)
            return found and (_digest(day, parsed, since, start, end, found[0]), found[1])
        result = _cached(("digest", str(Path(home).resolve()), _day(now), day, since), now, compute)
    return result if result is not None else _digest(day, parsed, since, start, end, None)
