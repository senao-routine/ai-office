"""Shared local file I/O for the office server and status board.

Both services coordinate updates to shared files and read growing transcripts.
Keeping locking and incremental offsets here avoids fixes drifting between the
services, while separate tail readers preserve their different line-boundary
and decoding rules; transcript-specific decisions stay with the caller.
"""
import fcntl
import json
import os
import tempfile
from pathlib import Path


class file_flock:
    """Lock <target>.lock exclusively; acquire any thread lock before this lock."""

    def __init__(self, target):
        target = Path(target)
        self._lockpath = target.with_name(target.name + ".lock")

    def __enter__(self):
        self._lockpath.parent.mkdir(parents=True, exist_ok=True)
        self._f = open(self._lockpath, "w", encoding="utf-8")
        try:
            fcntl.flock(self._f, fcntl.LOCK_EX)
        except BaseException:
            self._f.close()
            raise
        return self._f

    def __exit__(self, *exc):
        try:
            fcntl.flock(self._f, fcntl.LOCK_UN)
        finally:
            self._f.close()


def tail_lines(path, nbytes=80_000):
    """Return decoded lines with the office server's historical window rule."""
    try:
        size = Path(path).stat().st_size
        with open(path, "rb") as f:
            f.seek(max(0, size - nbytes))
            data = f.read()
        lines = data.decode("utf-8", errors="ignore").splitlines()
        # Preserve the legacy single-line exception as well as the default size.
        return lines[1:] if size > nbytes and len(lines) > 1 else lines
    except OSError:
        return []


def tail_lines_exact(path, nbytes):
    """Return byte lines, discarding only an initial fragment; propagate OSError."""
    path = Path(path)
    size = path.stat().st_size
    start = max(0, size - nbytes)
    with path.open("rb") as f:
        previous = b"\n"
        if start:
            f.seek(start - 1)
            previous = f.read(1)
        f.seek(start)
        data = f.read()
    if start and previous != b"\n":
        newline = data.find(b"\n")
        if newline < 0:
            return []
        data = data[newline + 1:]
    return data.splitlines()


class IncrementalTail:
    """Read complete UTF-8 lines, retaining byte offsets separately for each key.

    The first window (also after truncation) discards its first line if it starts
    after byte zero. A hold_last(line) callback may defer the last complete line.
    offsets is shared with legacy callers; state(key) returns its live entry.
    """

    def __init__(self, initial_bytes):
        self.initial_bytes = initial_bytes
        self.offsets = {}
        self._skip_first = set()
        self._sizes = {}

    def state(self, key):
        return self.offsets.get(key)

    def drop(self, key):
        self.offsets.pop(key, None)
        self._skip_first.discard(key)
        self._sizes.pop(key, None)

    def read(self, path, key, now=None, hold_last=None):
        try:
            size = Path(path).stat().st_size
        except OSError:
            return []
        state = self.state(key)
        reset = (state is None or not 0 <= state["offset"] <= size
                 or size < self._sizes.get(key, size))
        start = max(0, size - self.initial_bytes) if reset else state["offset"]
        skip_first = start > 0 if reset else key in self._skip_first
        if not reset and start >= size:
            if now is not None:
                state["seen"] = now
            return []
        try:
            with open(path, "rb") as f:
                f.seek(start)
                data = f.read(size - start)
        except OSError:
            return []
        self._sizes[key] = size
        consumed = data.rfind(b"\n") + 1
        chunk = data[:consumed]
        if skip_first and chunk:
            chunk = chunk[chunk.find(b"\n") + 1:]
            skip_first = False
        lines = chunk.decode("utf-8", errors="ignore").splitlines()
        if lines and hold_last is not None and hold_last(lines[-1]):
            # Count source bytes, including CRLF and any undecodable bytes.
            last_start = chunk.rfind(b"\n", 0, len(chunk) - 1) + 1
            consumed -= len(chunk) - last_start
            lines = lines[:-1]
        self.offsets[key] = {
            "offset": start + consumed,
            "seen": now if now is not None else 0.0,
        }
        if skip_first:
            self._skip_first.add(key)
        else:
            self._skip_first.discard(key)
        return lines


def atomic_write_json(path, obj, mode=0o600):
    """Replace a JSON file atomically, with the requested permissions."""
    path = Path(path)
    tmp = None
    try:
        with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", dir=path.parent,
                prefix=f".{path.name}.", suffix=".tmp", delete=False) as f:
            tmp = Path(f.name)
            json.dump(obj, f, ensure_ascii=False)
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    finally:
        if tmp is not None:
            tmp.unlink(missing_ok=True)
