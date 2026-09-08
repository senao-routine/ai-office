# -*- coding: utf-8 -*-
"""Shared file I/O: window boundaries, incremental offsets and exclusive locks."""
import fcntl
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location(
    "office_common_test", ROOT / "server" / "office_common.py")
common = importlib.util.module_from_spec(spec)
spec.loader.exec_module(common)


class OfficeCommonTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="office_common_")
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "transcript.jsonl"

    def test_incremental_complete_lines_and_pending_utf8(self):
        reader = common.IncrementalTail(64)
        encoded = "続き".encode("utf-8")
        self.path.write_bytes(b"first\n" + encoded[:2])
        self.assertEqual(reader.read(self.path, "a", now=1), ["first"])
        self.assertEqual(reader.state("a"), {"offset": 6, "seen": 1})
        self.assertEqual(reader.read(self.path, "a", now=2), [])
        with self.path.open("ab") as f:
            f.write(encoded[2:] + b"\nlast\n")
        self.assertEqual(reader.read(self.path, "a", now=3), ["続き", "last"])
        self.assertEqual(reader.state("a")["offset"], self.path.stat().st_size)
        self.assertEqual(reader.read(self.path, "a", now=4), [])
        self.assertEqual(reader.state("a")["seen"], 4)

    def test_initial_incomplete_line_does_not_slide_out_of_window(self):
        reader = common.IncrementalTail(8)
        self.path.write_bytes(b"partial")
        self.assertEqual(reader.read(self.path, "a"), [])
        with self.path.open("ab") as f:
            f.write(b"-completed\n")
        self.assertEqual(reader.read(self.path, "a"), ["partial-completed"])

    def test_shrunk_file_restarts_from_tail_window(self):
        reader = common.IncrementalTail(8)
        self.path.write_bytes(b"older\n" * 20)
        reader.read(self.path, "a", now=1)
        self.path.write_bytes(b"ignore\nfirst\nlast\n")
        self.assertEqual(reader.read(self.path, "a", now=2), ["last"])
        self.assertEqual(reader.state("a"), {"offset": 18, "seen": 2})
        self.path.write_bytes(b"new\n")
        self.assertEqual(reader.read(self.path, "a"), ["new"])

    def test_initial_window_drops_its_first_line(self):
        self.path.write_bytes(b"old\nfirst\nlast\n")
        for window in (8, 11):  # inside 'first', or exactly at its beginning
            with self.subTest(window=window):
                reader = common.IncrementalTail(window)
                self.assertEqual(reader.read(self.path, "a"), ["last"])
                self.assertEqual(reader.read(self.path, "a"), [])

    def test_shrink_resets_even_when_offset_still_fits(self):
        reader = common.IncrementalTail(64)
        self.path.write_bytes(b"first\nunfinished-long-line")
        self.assertEqual(reader.read(self.path, "a"), ["first"])
        self.path.write_bytes(b"reset\nnew\n")
        self.assertEqual(reader.read(self.path, "a"), ["reset", "new"])

    def test_initial_fragment_remains_discarded_when_completed_later(self):
        reader = common.IncrementalTail(4)
        self.path.write_bytes(b"long-fragment")
        self.assertEqual(reader.read(self.path, "a"), [])
        with self.path.open("ab") as f:
            f.write(b"-end\nnext\n")
        self.assertEqual(reader.read(self.path, "a"), ["next"])

    def test_hold_last_preserves_source_byte_offset(self):
        reader = common.IncrementalTail(64)
        self.path.write_bytes(b"first\n" + "保留".encode("utf-8") + b"\xff\r\n")
        hold = lambda line: line == "保留"
        self.assertEqual(reader.read(self.path, "a", hold_last=hold), ["first"])
        self.assertEqual(reader.state("a")["offset"], 6)
        self.assertEqual(reader.read(self.path, "a", hold_last=hold), [])
        with self.path.open("ab") as f:
            f.write(b"result\n")
        self.assertEqual(reader.read(self.path, "a", hold_last=hold), ["保留", "result"])

    def test_keys_and_drop_are_independent(self):
        reader = common.IncrementalTail(64)
        self.path.write_bytes(b"first\n")
        self.assertEqual(reader.read(self.path, "a"), ["first"])
        self.assertEqual(reader.read(self.path, "b"), ["first"])
        reader.drop("a")
        self.assertIsNone(reader.state("a"))
        self.assertEqual(reader.read(self.path, "b"), [])
        self.assertEqual(reader.read(self.path, "a"), ["first"])

    def test_tail_line_boundaries_and_types(self):
        self.path.write_bytes(b"old\nfirst\nlast\n")
        for window, decoded, exact in (
                (11, ["last"], [b"first", b"last"]),
                (8, ["last"], [b"last"]),
                (64, ["old", "first", "last"], [b"old", b"first", b"last"])):
            with self.subTest(window=window):
                self.assertEqual(common.tail_lines(self.path, window), decoded)
                self.assertEqual(common.tail_lines_exact(self.path, window), exact)
        self.path.write_bytes(b"abcdef")
        self.assertEqual(common.tail_lines(self.path, 3), ["def"])
        self.assertEqual(common.tail_lines_exact(self.path, 3), [])

    def test_missing_file_error_contracts(self):
        self.assertEqual(common.tail_lines(self.path, 8), [])
        self.assertEqual(common.IncrementalTail(8).read(self.path, "a"), [])
        with self.assertRaises(OSError):
            common.tail_lines_exact(self.path, 8)

    def test_file_flock_creates_lock_and_releases_on_exit(self):
        target = self.path.parent / "nested" / "config.json"
        lockpath = target.with_name(target.name + ".lock")
        for fail in (False, True):
            with self.subTest(exception=fail):
                try:
                    with common.file_flock(target) as locked:
                        self.assertTrue(lockpath.is_file())
                        with lockpath.open("w") as contender:
                            with self.assertRaises(BlockingIOError):
                                fcntl.flock(contender, fcntl.LOCK_EX | fcntl.LOCK_NB)
                        if fail:
                            raise RuntimeError("test exit")
                except RuntimeError:
                    if not fail:
                        raise
                self.assertTrue(locked.closed)
                with lockpath.open("w") as contender:
                    fcntl.flock(contender, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    fcntl.flock(contender, fcntl.LOCK_UN)

    def test_atomic_json_replaces_with_requested_mode(self):
        for mode in (0o600, 0o640):
            with self.subTest(mode=mode):
                self.path.write_text('{"old": true}', encoding="utf-8")
                common.atomic_write_json(self.path, {"message": "完了"}, mode=mode)
                self.assertEqual(json.loads(self.path.read_text()), {"message": "完了"})
                self.assertEqual(self.path.stat().st_mode & 0o777, mode)

    def test_atomic_json_failure_keeps_existing_file_and_cleans_tmp(self):
        self.path.write_bytes(b'{"old": true}')
        with patch.object(common.os, "replace", side_effect=OSError("test failure")):
            with self.assertRaises(OSError):
                common.atomic_write_json(self.path, {"new": True})
        self.assertEqual(self.path.read_bytes(), b'{"old": true}')
        self.assertEqual(list(self.path.parent.iterdir()), [self.path])


if __name__ == "__main__":
    unittest.main()
