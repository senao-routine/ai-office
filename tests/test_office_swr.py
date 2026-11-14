# -*- coding: utf-8 -*-
"""R115-S1: office_json() の stale-while-revalidate（SWR）＋single flight を実スレッドで確かめる。

本人の症状（2026-10-09）: daemon の `/api/office` が約 60 秒おきに 3 秒待たされる・再起動の直後は 1〜3 分。
原因の構造＝office_json() が `_lock` を握ったまま scan_office() を回し、ThreadingHTTPServer の全 request thread が
その 1 本を待っていた。直し（plan r115-scan-swr の S1）:
  - `_lock` は `_cache` の読み書きだけ・走査の single flight は `_scan_lock`
  - 新鮮 → 返す／古くて前の値あり → daemon（`_SWR`＝main() が serve する間だけ）は裏で 1 本だけ走査して前の値を即返す／
    値なし（冷間）→ 呼び手が走査（同時の冷間の呼び手は同じ 1 回の結果を受け取る）
  - `_invalidate_cache()` は t=0 のまま・裏の走査の例外は前の値を保って旗を戻す（次の呼び出しで再試行）
  - 旗 OFF（relay_agent・tests・--dump）は従来どおり同期
  - 読み返し（ローカルの書き込みの直後は書き込みの後の走査の値）と古さの上限（STALE_MAX）は、verify の言語の往復と
    60 秒おきの見張り（_watch_loop の ❗ 通知）が古い値を掴まないための足し（plan からの逸脱として報告）

scan_office は差し替える（gate が開くまで戻らない遅い走査の代役）。office_server は**このテスト専用のモジュール**として
一時 HOME（tests/artifacts/.tmp）で読み込む＝旗や `_SCAN` を他のテストの office_server と共有しない。各テスト 2 秒以内。
"""
import ast
import http.client
import importlib.util
import io
import json
import os
import queue
import shutil
import sys
import tempfile
import threading
import time
import unittest
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
TMP_BASE = ROOT / "tests" / "artifacts" / ".tmp"
TMP_BASE.mkdir(parents=True, exist_ok=True)
_HOME = Path(tempfile.mkdtemp(prefix="office_swr_", dir=TMP_BASE))

_spec = importlib.util.spec_from_file_location("office_swr_t", ROOT / "server" / "office_server.py")
office = importlib.util.module_from_spec(_spec)
try:
    with mock.patch.dict(os.environ, {"OFFICE_HOME": str(_HOME)}):
        _spec.loader.exec_module(office)
except BaseException:               # 読み込めない（構文の誤り等）と tearDownModule は呼ばれない＝ここで一時 HOME を消す
    shutil.rmtree(_HOME, ignore_errors=True)
    raise
_DEFAULT_SWR = office._SWR          # 読み込んだ直後の既定（テストが触る前）
_DEFAULT_ASYNC_RES = office._ASYNC_RES


def tearDownModule():
    shutil.rmtree(_HOME, ignore_errors=True)


class Scan:
    """scan_office の代役。gate が開くまで戻らない（遅い走査）・呼ばれた回数を数える・results を順に返す（例外なら投げる）。"""

    def __init__(self, *results, block=False):
        self.results = list(results)
        self.calls = 0
        self._count = threading.Lock()
        self.started = threading.Event()
        self.gate = threading.Event()
        if not block:
            self.gate.set()

    def __call__(self):
        with self._count:
            self.calls += 1
            n = self.calls
        self.started.set()
        if not self.gate.wait(1.5):
            raise AssertionError("遅い走査の gate が開かない")
        r = self.results[min(n, len(self.results)) - 1]
        if isinstance(r, BaseException):
            raise r
        return r


class Call(threading.Thread):
    """office_json() を実スレッドで呼び、値（または例外）と待ち時間を控える。"""

    def __init__(self, fn):
        super().__init__(daemon=True)
        self.fn, self.value, self.error, self.elapsed = fn, None, None, None

    def run(self):
        t0 = time.perf_counter()
        try:
            self.value = self.fn()
        except BaseException as e:      # noqa: BLE001 - 検査側で見る
            self.error = e
        finally:
            self.elapsed = time.perf_counter() - t0


class SwrBase(unittest.TestCase):
    def setUp(self):
        with office._lock:
            office._cache.update(t=0.0, data=None)
            office._SCAN.update(seq=0, at=0.0, busy=False, thread=None, wseq=0, dseq=0)
        office._SWR = False
        self.addCleanup(setattr, office, "_SWR", False)

    def use_scan(self, scan):
        """scan_office を差し替える。後始末は「gate を開けて裏の走査を待つ」→「差し替えを戻す」の順（LIFO）＝
        差し替えを戻した後に裏の thread が本物の走査を回すことはない。"""
        patcher = mock.patch.object(office, "scan_office", scan)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self._drain, scan)
        return scan

    def _drain(self, scan):
        scan.gate.set()
        th = office._SCAN["thread"]
        if th is not None:
            th.join(1.5)

    def join_bg(self):
        th = office._SCAN["thread"]
        self.assertIsNotNone(th, "裏の走査が起きていない")
        th.join(1.5)
        self.assertFalse(th.is_alive(), "裏の走査が終わらない")

    def seed(self, data, age):
        """前の値を置く（age 秒前に走査を始めて終えた値）。"""
        now = time.time()
        with office._lock:
            office._cache.update(t=now - age, data=data)
            office._SCAN.update(at=now - age, dseq=office._SCAN["wseq"])

    @staticmethod
    def start_call(fn=None):
        c = Call(fn or office.office_json)
        c.start()
        return c

    def finish(self, call):
        call.join(1.5)
        self.assertFalse(call.is_alive(), "呼び出しが戻らない")
        if call.error is not None:
            raise call.error
        return call


class OfficeSwrTest(SwrBase):
    def test_1_second_caller_during_a_slow_scan_gets_the_stale_value_at_once_and_scans_once(self):
        """① 遅い走査（patch）の間、1 本目も 2 本目も前の値を即座に受け取り、走査は 1 回だけ。"""
        office._SWR = True
        stale, fresh = {"gen": 0}, {"gen": 1}
        self.seed(stale, age=office.CACHE_SEC + 1)
        scan = self.use_scan(Scan(fresh, block=True))
        first = self.finish(self.start_call())              # 前の値を返し、裏の走査を 1 本起こす
        self.assertTrue(scan.started.wait(1), "裏の走査が始まらない")
        second = self.finish(self.start_call())             # 走査の途中の 2 本目
        self.assertIs(first.value, stale)
        self.assertIs(second.value, stale)
        self.assertLess(first.elapsed, 0.25)
        self.assertLess(second.elapsed, 0.25)
        self.assertEqual(scan.calls, 1)
        self.assertTrue(office._SCAN["busy"])
        scan.gate.set()
        self.join_bg()
        self.assertFalse(office._SCAN["busy"])
        self.assertIs(office.office_json(), fresh)          # 走査の後は新鮮（もう走査しない）
        self.assertEqual(scan.calls, 1)

    def _cold_callers_share_one_scan(self, swr):
        office._SWR = swr
        fresh = {"gen": 1}
        scan = self.use_scan(Scan(fresh, block=True))
        calls = [self.start_call() for _ in range(4)]
        self.assertTrue(scan.started.wait(1))
        time.sleep(0.05)                                    # 残りが走査（_scan_lock）を待つところまで進む
        self.assertTrue(all(c.is_alive() for c in calls), "冷間なのに走査の前に戻った")
        scan.gate.set()
        for c in calls:
            self.assertIs(self.finish(c).value, fresh)
        self.assertEqual(scan.calls, 1)

    def test_2_cold_concurrent_callers_wait_for_one_scan_and_get_the_same_result(self):
        """② 冷間（値なし）の同時呼び出しは走査 1 回・全員が同じ結果（daemon の旗 ON）。"""
        self._cold_callers_share_one_scan(True)

    def test_2b_cold_concurrent_callers_share_one_scan_with_the_flag_off_too(self):
        self._cold_callers_share_one_scan(False)

    def test_2c_cold_callers_share_the_scan_even_if_an_event_invalidates_it_midway(self):
        """②' 再起動の直後の実際の形: 冷間の遅い走査の途中に hook のイベント（無効化）が来ても、待っていた全員がその 1 回の
        結果を受け取る（1 人ずつ走査し直して直列に待たせない）。その値は新鮮と言わない＝次の GET が裏で走査する。"""
        office._SWR = True
        d1, d2 = {"gen": 1}, {"gen": 2}
        scan = self.use_scan(Scan(d1, d2, block=True))
        calls = [self.start_call() for _ in range(3)]
        self.assertTrue(scan.started.wait(1))
        time.sleep(0.05)
        office._invalidate_cache()
        scan.gate.set()
        for c in calls:
            self.assertIs(self.finish(c).value, d1)
        self.assertEqual(scan.calls, 1)
        self.assertEqual(office._cache["t"], 0)
        self.assertIs(office.office_json(), d1)
        self.join_bg()
        self.assertIs(office.office_json(), d2)
        self.assertEqual(scan.calls, 2)

    def test_3_invalidate_keeps_t_zero_and_the_get_after_the_background_scan_is_fresh(self):
        """③ _invalidate_cache() は t=0 のまま（ピン）。次の GET は前の値を返して裏で走査し、その完了後の GET は新鮮。"""
        office._SWR = True
        d0, d1 = {"gen": 0}, {"gen": 1}
        self.seed(d0, age=0)
        scan = self.use_scan(Scan(d1))
        self.assertIs(office.office_json(), d0)             # 新鮮＝走査しない
        self.assertEqual(scan.calls, 0)
        office._invalidate_cache()
        self.assertEqual(office._cache["t"], 0)
        self.assertIs(office.office_json(), d0)             # 前の値を即返す
        self.join_bg()
        self.assertIs(office.office_json(), d1)
        self.assertEqual(scan.calls, 1)
        self.assertLessEqual(time.time() - office._cache["t"], office.CACHE_SEC)

    def test_3b_invalidation_during_the_background_scan_is_not_lost(self):
        """③' 裏の走査の途中に来た無効化は失わない（その走査の値を新鮮と言わず、次の GET がもう 1 回走査する）。"""
        office._SWR = True
        d0, d1, d2 = {"gen": 0}, {"gen": 1}, {"gen": 2}
        self.seed(d0, age=office.CACHE_SEC + 1)
        scan = self.use_scan(Scan(d1, d2, block=True))
        self.assertIs(office.office_json(), d0)
        self.assertTrue(scan.started.wait(1))
        office._invalidate_cache()                          # 走査の途中（hook のイベント）
        scan.gate.set()
        self.join_bg()
        self.assertIs(office._cache["data"], d1)
        self.assertEqual(office._cache["t"], 0)
        self.assertIs(office.office_json(), d1)             # まだ古い＝もう 1 本
        self.join_bg()
        self.assertIs(office.office_json(), d2)
        self.assertEqual(scan.calls, 2)

    def test_4_background_failure_keeps_the_stale_value_resets_the_flag_and_retries(self):
        """④ 裏の走査の例外: 前の値と時刻を保ち、旗（busy）を戻し、ログに 1 行・次の呼び出しで再試行。冷間の例外は呼び手へ。"""
        office._SWR = True
        d0, d1 = {"gen": 0}, {"gen": 1}
        self.seed(d0, age=office.CACHE_SEC + 1)
        t_before = office._cache["t"]
        scan = self.use_scan(Scan(RuntimeError("boom"), d1))
        err = io.StringIO()
        with mock.patch.object(sys, "stderr", err):
            self.assertIs(office.office_json(), d0)
            self.join_bg()
        self.assertFalse(office._SCAN["busy"])
        self.assertIs(office._cache["data"], d0)
        self.assertEqual(office._cache["t"], t_before)
        self.assertIn("RuntimeError: boom", err.getvalue())
        self.assertIs(office.office_json(), d0)             # 次の呼び出しで再試行
        self.join_bg()
        self.assertIs(office.office_json(), d1)
        self.assertEqual(scan.calls, 2)

    def test_4b_cold_failure_goes_to_the_caller_and_the_next_call_retries(self):
        office._SWR = True
        d1 = {"gen": 1}
        scan = self.use_scan(Scan(RuntimeError("cold"), d1))
        with self.assertRaises(RuntimeError):
            office.office_json()
        self.assertIsNone(office._cache["data"])
        self.assertEqual(office._cache["t"], 0.0)
        self.assertIs(office.office_json(), d1)
        self.assertEqual(scan.calls, 2)

    def test_5_flag_off_by_default_is_synchronous_like_before(self):
        """⑤ 旗 OFF（既定＝relay_agent・tests・--dump）: 古ければ呼び手が走査を待つ（同時の 2 本目も同じ 1 回を待つ）。"""
        self.assertIs(_DEFAULT_SWR, False)
        d0, d1, d2 = {"gen": 0}, {"gen": 1}, {"gen": 2}
        self.seed(d0, age=office.CACHE_SEC + 1)
        scan = self.use_scan(Scan(d1, d2, block=True))
        calls = [self.start_call() for _ in range(2)]
        self.assertTrue(scan.started.wait(1))
        time.sleep(0.2)
        self.assertTrue(all(c.is_alive() for c in calls), "旗 OFF なのに古い値を返した")
        scan.gate.set()
        for c in calls:
            c = self.finish(c)
            self.assertIs(c.value, d1)
            self.assertGreaterEqual(c.elapsed, 0.2)
        self.assertEqual(scan.calls, 1)
        office._invalidate_cache()
        self.assertEqual(office._cache["t"], 0)
        self.assertIs(office.office_json(), d2)              # t=0 → 次の呼び出しは新鮮（同期）
        self.assertIsNone(office._SCAN["thread"])            # 裏の thread は一度も起きない

    def test_6_local_writes_are_read_back_not_served_stale(self):
        """読み返し: ローカルの書き込み（_mark_written）の直後は、書き込みの後に始めた走査の値（verify の言語の往復）。
        書き込みの前から走っていた裏の走査の値も、待っていた呼び手に共有しない。"""
        office._SWR = True
        d0, d1, d2, d3 = {"gen": 0}, {"gen": 1}, {"gen": 2}, {"gen": 3}
        self.seed(d0, age=office.CACHE_SEC + 1)
        scan = self.use_scan(Scan(d1, d2, d3, block=True))
        self.assertIs(office.office_json(), d0)              # 裏の走査 1（書き込みの前に始まる）
        self.assertTrue(scan.started.wait(1))
        with office._lock:
            office._mark_written()
        self.assertEqual(office._cache["t"], 0)
        reader = self.start_call()
        time.sleep(0.05)
        self.assertTrue(reader.is_alive(), "書き込みの前の値を返した")
        scan.gate.set()
        self.assertIs(self.finish(reader).value, d2)         # 走査 1（d1）は共有しない＝自分で走査 2
        self.join_bg()
        self.assertEqual(scan.calls, 2)
        cfg = _HOME / "office_config_swr.json"               # 本物の書き込み口（set_lang）も同じ印を付ける
        cfg.write_text('{"projects": {}, "lang": "ja"}', encoding="utf-8")
        with mock.patch.dict(os.environ, {"OFFICE_CONFIG": str(cfg)}):
            w = office._SCAN["wseq"]
            self.assertEqual(office.set_lang("en"), (True, "en"))
        self.assertEqual(office._SCAN["wseq"], w + 1)
        self.assertEqual(office._cache["t"], 0)
        self.assertIs(office.office_json(), d3)              # 同期で読み返す
        self.assertEqual(scan.calls, 3)

    def test_7_values_older_than_stale_max_are_not_served(self):
        """古さの上限: 最後の走査が終わってから STALE_MAX 秒を過ぎていたら呼び手が走査を待つ（60 秒おきの見張りが古い ❗ を掴まない）。"""
        office._SWR = True
        d0, d1 = {"gen": 0}, {"gen": 1}
        self.seed(d0, age=office.STALE_MAX + 1)
        scan = self.use_scan(Scan(d1))
        self.assertIs(office.office_json(), d1)
        self.assertEqual(scan.calls, 1)
        self.assertIsNone(office._SCAN["thread"])

    def test_8_main_turns_swr_on_only_while_serving(self):
        """main() が serve する間だけ旗（S1 の _SWR・S3 の _ASYNC_RES）が立ち、戻ると下りる（KeyboardInterrupt でも）。
        --dump は立てない。"""
        self.addCleanup(setattr, office, "_ASYNC_RES", False)
        seen = []
        srv = mock.Mock()
        srv.serve_forever.side_effect = lambda: seen.append((office._SWR, office._ASYNC_RES))
        env = {"OFFICE_EVENTS": "0", "OFFICE_TIMELINE": "0"}
        with mock.patch.object(sys, "argv", ["office_server.py"]), mock.patch.dict(os.environ, env), \
                mock.patch.object(office, "_OfficeHTTPServer", return_value=srv), \
                mock.patch.object(office, "_install_ts_logging"), mock.patch.object(office, "start_watcher"), \
                mock.patch("builtins.print"):
            office.main()
            self.assertEqual(seen, [(True, True)])
            self.assertEqual((office._SWR, office._ASYNC_RES), (False, False))
            srv.serve_forever.side_effect = KeyboardInterrupt
            office.main()
            self.assertEqual((office._SWR, office._ASYNC_RES), (False, False))
        dumped = []
        with mock.patch.object(sys, "argv", ["office_server.py", "--dump"]), \
                mock.patch.object(office, "scan_office", lambda: dumped.append((office._SWR, office._ASYNC_RES)) or {}), \
                mock.patch("builtins.print"):
            office.main()
        self.assertEqual(dumped, [(False, False)])
        self.assertEqual((office._SWR, office._ASYNC_RES), (False, False))


class FakeQueue:
    """SSE の購読キューの代役。stop() で get が BrokenPipeError を投げる＝Handler の SSE が後始末へ抜けて thread が終わる。"""

    def __init__(self):
        self._q = queue.Queue()
        self._stop = threading.Event()

    def put(self, item):
        self._q.put(item)

    def empty(self):
        return self._q.empty()

    def get(self, timeout=None):
        deadline = time.monotonic() + (timeout or 0)
        while True:
            if self._stop.is_set():
                raise BrokenPipeError("test stop")
            try:
                return self._q.get(timeout=0.05)
            except queue.Empty:
                if time.monotonic() >= deadline:
                    raise

    def stop(self):
        self._stop.set()


class FakeEvents:
    """office_events の代役（subscribe・unsubscribe・seq・recent）。emit は本物の _ingest と同じ順＝失効 → 購読者へ。"""
    RING = 64

    def __init__(self):
        self.q = FakeQueue()
        self.n = 0

    def subscribe(self):
        return self.q

    def unsubscribe(self, _q):
        pass

    def seq(self):
        return self.n

    def recent(self, _since, limit=0):
        return []

    def emit(self, ev="Stop"):
        self.n += 1
        office._invalidate_cache()
        self.q.put({"seq": self.n, "ts": time.time(), "ev": ev, "sid": "sess-swr"})


class PokeFreshTest(SwrBase):
    """R115-S1b: office_events は「キャッシュを失効させてから poke」の順を守る（Astra レビュー）＝poke を受けた UI の取り直しは
    イベントの後の値を受け取る。SWR だけだと取り直しが前の値を掴み、次の poke か 15 秒のポーリングまで ❗ が出ない。
    SSE の thread が poke の前に走査し直しを待つ（GET は待たせない）。"""

    def test_9_wait_fresh_rescans_an_invalidated_value_and_does_not_wait_otherwise(self):
        office._SWR = True
        d0, d1 = {"gen": 0}, {"gen": 1}
        self.seed(d0, age=0)
        scan = self.use_scan(Scan(d1))
        office._invalidate_cache()
        self.assertTrue(office._wait_fresh(1.0))
        self.assertIs(office._cache["data"], d1)
        self.assertGreater(office._cache["t"], 0)
        self.assertEqual(scan.calls, 1)
        self.assertTrue(office._wait_fresh(1.0))             # 新鮮なら待たない
        self.seed(d1, age=office.CACHE_SEC + 1)
        self.assertTrue(office._wait_fresh(1.0))             # 時間切れだけ（t>0）でも待たない＝SWR のまま
        self.assertEqual(scan.calls, 1)

    def test_9b_wait_fresh_is_bounded_and_does_nothing_without_the_flag(self):
        d0 = {"gen": 0}
        self.seed(d0, age=0)
        office._invalidate_cache()
        scan = self.use_scan(Scan({"gen": 1}, block=True))
        t0 = time.perf_counter()
        self.assertTrue(office._wait_fresh(1.0))             # 旗 OFF（relay_agent・tests）は何もしない
        self.assertLess(time.perf_counter() - t0, 0.05)
        self.assertEqual(scan.calls, 0)
        office._SWR = True
        t0 = time.perf_counter()
        self.assertFalse(office._wait_fresh(0.2))            # 走査が終わらなければ時間切れ（poke はそのまま送る）
        self.assertGreaterEqual(time.perf_counter() - t0, 0.19)
        self.assertLess(time.perf_counter() - t0, 0.6)
        scan.gate.set()
        self.join_bg()

    def test_9c_an_invalidation_during_the_wait_gets_one_more_scan(self):
        office._SWR = True
        d0, d1, d2 = {"gen": 0}, {"gen": 1}, {"gen": 2}
        self.seed(d0, age=0)
        office._invalidate_cache()
        scan = self.use_scan(Scan(d1, d2, block=True))
        waiter = self.start_call(lambda: office._wait_fresh(1.5))
        self.assertTrue(scan.started.wait(1))
        office._invalidate_cache()                           # 走査の途中にもう 1 件
        scan.gate.set()
        self.assertIs(self.finish(waiter).value, True)
        self.assertIs(office._cache["data"], d2)
        self.assertEqual(scan.calls, 2)

    def test_10_the_ui_refetch_after_an_sse_poke_gets_the_value_after_the_event(self):
        """実 HTTP（_OfficeHTTPServer＋Handler・空きポート）: hook のイベント（失効→poke）を受けた UI が取り直す
        /api/office は、走査し直した後の値。poke は走査が終わるまで出ない（GET は待たせない）。"""
        office._SWR = True
        d0, d1 = {"gen": 0}, {"gen": 1}
        self.seed(d0, age=0)
        scan = self.use_scan(Scan(d1, block=True))
        events = FakeEvents()
        for patcher in (mock.patch.object(office, "office_events", events),
                        mock.patch.dict(os.environ, {"OFFICE_EVENTS": "1"})):
            patcher.start()
            self.addCleanup(patcher.stop)
        srv = office._OfficeHTTPServer(("127.0.0.1", 0), office.Handler)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.addCleanup(srv.server_close)
        self.addCleanup(srv.shutdown)
        self.addCleanup(events.q.stop)                       # 後始末の順（LIFO）: SSE を抜けさせる → serve を止める → 閉じる
        port = srv.server_address[1]
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=3)
        self.addCleanup(conn.close)
        conn.request("GET", "/api/events", headers={"X-Office-Local": "1"})
        resp = conn.getresponse()
        self.assertEqual(resp.status, 200)
        lines = []

        def read():
            try:
                for raw in resp:
                    lines.append((time.monotonic(), raw.decode("utf-8").strip()))
            except Exception:                                # noqa: BLE001 - 切断で終わる
                pass

        threading.Thread(target=read, daemon=True).start()

        def wait_line(text, timeout=1.0):
            end = time.monotonic() + timeout
            while time.monotonic() < end:
                hit = [t for t, line in lines if line == text]
                if hit:
                    return hit[0]
                time.sleep(0.01)
            return None

        self.assertIsNotNone(wait_line("event: hello"))
        events.emit("Stop")
        time.sleep(0.2)
        self.assertIsNone(wait_line("event: poke", timeout=0), "走査し直しの前に poke が出た")
        opened = time.monotonic()
        scan.gate.set()
        poked = wait_line("event: poke")
        self.assertIsNotNone(poked, "poke が届かない")
        self.assertGreaterEqual(poked, opened)
        ui = http.client.HTTPConnection("127.0.0.1", port, timeout=3)
        self.addCleanup(ui.close)
        t0 = time.perf_counter()
        ui.request("GET", "/api/office", headers={"X-Office-Local": "1"})
        body = json.loads(ui.getresponse().read())
        self.assertEqual(body, d1)                           # poke の後の取り直し＝イベントの後の値
        self.assertLess(time.perf_counter() - t0, 0.5)
        self.assertEqual(scan.calls, 1)


class ResAsyncTest(unittest.TestCase):
    """R115-S3: res_summary() の外部 HTTPS（プロバイダごとに最大 6 秒）を呼び手の外へ。_ASYNC_RES（daemon と relay_agent の常駐）
    では切れたら裏で 1 本だけ更新して旧値（無ければ None）を即返す。旗 OFF（tests・--once・--dump）は同期のまま。"""

    def setUp(self):
        office._ASYNC_RES = False
        self.addCleanup(setattr, office, "_ASYNC_RES", False)
        office._RES_CACHE.update(ts=0.0, data=None)
        office._RES_BG.update(busy=False, thread=None)

    def use_fetch(self, *values, block=False):
        """status_board の 2 つの読み口を差し替える（claude_gauge_public が遅い外部 HTTPS の代役）。後始末は「gate を開けて
        裏の更新を待つ」→「差し替えを戻す」の順（LIFO）。"""
        f = SimpleNamespace(calls=[], gate=threading.Event(), started=threading.Event())
        if not block:
            f.gate.set()

        def claude(now):
            f.calls.append(now)
            f.started.set()
            if not f.gate.wait(1.5):
                raise AssertionError("遅い取得の gate が開かない")
            return values[min(len(f.calls), len(values)) - 1]

        for patcher in (mock.patch.object(office.status_board, "claude_gauge_public", claude),
                        mock.patch.object(office.status_board, "gauges_public", lambda now: {"providers": []})):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.addCleanup(self._drain, f)
        return f

    def _drain(self, f):
        f.gate.set()
        th = office._RES_BG["thread"]
        if th is not None:
            th.join(1.5)

    def join_bg(self):
        th = office._RES_BG["thread"]
        self.assertIsNotNone(th, "裏の更新が起きていない")
        th.join(1.5)
        self.assertFalse(th.is_alive(), "裏の更新が終わらない")

    def test_11_flag_off_by_default_refreshes_synchronously_with_the_60s_cache(self):
        self.assertIs(_DEFAULT_ASYNC_RES, False)
        v1, v2 = {"fiveHour": 10}, {"fiveHour": 20}
        f = self.use_fetch(v1, v2)
        self.assertEqual(office.res_summary(1000.0), v1)
        self.assertEqual(office.res_summary(1059.0), v1)    # 60 秒のキャッシュ
        self.assertEqual(office.res_summary(1061.0), v2)    # 切れたら同期で取り直す
        self.assertEqual(f.calls, [1000.0, 1061.0])
        self.assertIsNone(office._RES_BG["thread"])

    def test_12_cold_returns_none_at_once_and_one_background_refresh_fills_it(self):
        office._ASYNC_RES = True
        v1 = {"fiveHour": 10}
        f = self.use_fetch(v1, block=True)
        now = time.time()
        t0 = time.perf_counter()
        self.assertIsNone(office.res_summary(now))          # 冷間＝null（office-json.md の取得不可時）
        self.assertLess(time.perf_counter() - t0, 0.25)
        self.assertTrue(f.started.wait(1))
        self.assertIsNone(office.res_summary(now + 0.5))    # 更新中の 2 回目も即返す・起こし直さない
        self.assertEqual(len(f.calls), 1)
        f.gate.set()
        self.join_bg()
        self.assertFalse(office._RES_BG["busy"])
        self.assertEqual(office.res_summary(now + 1), v1)
        self.assertEqual(len(f.calls), 1)

    def test_12b_expired_returns_the_old_value_while_refreshing(self):
        office._ASYNC_RES = True
        old, new = {"fiveHour": 10}, {"fiveHour": 30}
        now = time.time()
        office._RES_CACHE.update(ts=now - 61, data=old)
        f = self.use_fetch(new, block=True)
        t0 = time.perf_counter()
        self.assertEqual(office.res_summary(now), old)
        self.assertLess(time.perf_counter() - t0, 0.25)
        self.assertTrue(f.started.wait(1))
        f.gate.set()
        self.join_bg()
        self.assertEqual(office.res_summary(now + 1), new)
        self.assertEqual(len(f.calls), 1)

    def test_13_relay_agent_main_turns_it_on_for_the_loop_but_not_for_once(self):
        """relay_agent の常駐（ループ）は ON・--once（E2E/デバッグ）は同期のまま。"""
        spec = importlib.util.spec_from_file_location("relay_agent_swr_t", ROOT / "server" / "relay_agent.py")
        ra = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(ra)
        self.addCleanup(setattr, ra.office, "_ASYNC_RES", ra.office._ASYNC_RES)
        ra.office._ASYNC_RES = False
        seen = []

        class Stop(BaseException):
            pass

        def ws_loop(url, token, state):
            seen.append(("loop", ra.office._ASYNC_RES))
            raise Stop()

        def sync_tick(url, token, state):
            seen.append(("once", ra.office._ASYNC_RES))
            return 0, None, False

        def run(argv):
            with ExitStack() as st:
                for patcher in (mock.patch.object(ra, "load_config", return_value=("http://relay.invalid", "t", 30)),
                                mock.patch.object(ra, "_adopt_p4_data"), mock.patch.object(ra, "_load_nonces"),
                                mock.patch.object(ra.office, "_install_ts_logging"), mock.patch("builtins.print"),
                                mock.patch.object(ra, "WS_ENABLED", True), mock.patch.object(ra, "ws_loop", ws_loop),
                                mock.patch.object(ra, "sync_tick", sync_tick), mock.patch.object(ra, "_req"),
                                mock.patch.object(sys, "argv", argv)):
                    st.enter_context(patcher)
                ra.main()

        run(["relay_agent.py", "--once"])
        self.assertEqual(seen, [("once", False)])
        with self.assertRaises(Stop):
            run(["relay_agent.py"])
        self.assertEqual(seen, [("once", False), ("loop", True)])


class InvariantTest(unittest.TestCase):
    def test_14_only_the_two_helpers_write_zero_to_the_cache_time(self):
        """掟（.claude/rules/server.md の R115）: `_cache["t"]` に 0 を書けるのは _invalidate_cache（イベント＝SWR のまま）と
        _mark_written（ローカルの書き込み＝読み返し）だけ。直書きを足すと daemon ではイベント扱いになり、書いた直後の GET が
        前の値を返す（言語を切り替えた直後の画面が前の言語に戻る）。"""
        tree = ast.parse((ROOT / "server" / "office_server.py").read_text(encoding="utf-8"))

        def zero_to_cache_t(node):
            return (isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant) and node.value.value == 0
                    and any(isinstance(t, ast.Subscript) and isinstance(t.value, ast.Name) and t.value.id == "_cache"
                            and isinstance(t.slice, ast.Constant) and t.slice.value == "t" for t in node.targets))

        writers = sorted(fn.name for fn in ast.walk(tree) if isinstance(fn, ast.FunctionDef)
                         for node in ast.walk(fn) if zero_to_cache_t(node))
        self.assertEqual(writers, ["_invalidate_cache", "_mark_written"])
        self.assertFalse([n for n in tree.body if zero_to_cache_t(n)], "モジュールの直下で _cache[\"t\"] に 0 を書いている")


# ── R115 反証レビューの回帰（S1 の修正役・2026-10-09）──────────────────────────────────────────────

class FailingScanTest(SwrBase):
    """反証 R115 #1（medium・#3・#4 も同じ欠陥）: 走査が毎回同じ所で落ちる状態（office_config.json が UTF-8 でない・手編集の
    型の誤り）で、_wait_fresh は失敗した走査を期限まで起こし直し続けた（poke 1 回で数万回の走査・ログが数 MB・CPU 1 コア）。
    待つ間に走査が失敗したら起こし直さずに False（再試行は次の GET＝plan の「失敗は次の GET で再試行」）。"""

    def test_15_wait_fresh_does_not_respin_a_scan_that_keeps_failing(self):
        office._SWR = True
        self.seed({"gen": 0}, age=0)
        office._invalidate_cache()                           # hook のイベント（失効）
        scan = self.use_scan(Scan(RuntimeError("spin")))     # 毎回すぐ落ちる走査
        err = io.StringIO()
        with mock.patch.object(sys, "stderr", err):
            t0 = time.perf_counter()
            self.assertFalse(office._wait_fresh(1.0))
            elapsed = time.perf_counter() - t0
            self.join_bg()
        self.assertLess(elapsed, 0.5, "期限まで起こし直した")
        self.assertEqual(scan.calls, 1)
        self.assertEqual(err.getvalue().count("office の裏の走査が失敗"), 1)
        self.assertFalse(office._SCAN["busy"])
        self.assertEqual(office._cache["t"], 0)              # 失効のまま＝次の GET が再試行する
        with mock.patch.object(sys, "stderr", io.StringIO()):
            self.assertFalse(office._wait_fresh(1.0))        # 次の poke（失効しないイベントでも t=0 が残る形）も高々 1 回
            self.join_bg()
        self.assertEqual(scan.calls, 2)

    def test_15b_a_failure_of_the_scan_already_in_flight_ends_the_wait_without_a_new_scan(self):
        """GET が起こした裏の走査の途中にイベント → poke の待ち。その走査が失敗したら、待ちは新しい走査を起こさずに終わる。"""
        office._SWR = True
        d0 = {"gen": 0}
        self.seed(d0, age=office.CACHE_SEC + 1)
        scan = self.use_scan(Scan(RuntimeError("inflight"), block=True))
        self.assertIs(office.office_json(), d0)              # 裏の走査（落ちる）を 1 本起こす
        self.assertTrue(scan.started.wait(1))
        office._invalidate_cache()
        waiting = threading.Event()
        real_wait = office._scan_done.wait

        def wait(timeout=None):
            waiting.set()
            return real_wait(timeout)

        patcher = mock.patch.object(office._scan_done, "wait", wait)
        patcher.start()
        self.addCleanup(patcher.stop)
        waiter = self.start_call(lambda: office._wait_fresh(1.0))
        self.assertTrue(waiting.wait(1), "待ちに入らない")       # 走査の途中で待っている
        with mock.patch.object(sys, "stderr", io.StringIO()):
            scan.gate.set()
            self.assertIs(self.finish(waiter).value, False)
            self.join_bg()
        self.assertLess(waiter.elapsed, 0.6)
        self.assertEqual(scan.calls, 1)


class SseBase(SwrBase):
    """実 HTTP（_OfficeHTTPServer＋Handler・空きポート）で SSE を 1 本つなぐ（test_10 と同じ組み立て）。"""

    def patch_attr(self, name, value):
        patcher = mock.patch.object(office, name, value)
        patcher.start()
        self.addCleanup(patcher.stop)

    def open_sse(self):
        events = FakeEvents()
        for patcher in (mock.patch.object(office, "office_events", events),
                        mock.patch.dict(os.environ, {"OFFICE_EVENTS": "1"})):
            patcher.start()
            self.addCleanup(patcher.stop)
        handler = type("QuietHandler", (office.Handler,), {"log_message": lambda *args: None})
        srv = office._OfficeHTTPServer(("127.0.0.1", 0), handler)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.addCleanup(srv.server_close)
        self.addCleanup(srv.shutdown)
        self.addCleanup(events.q.stop)                       # 後始末の順（LIFO）: SSE を抜けさせる → serve を止める → 閉じる
        port = srv.server_address[1]
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=3)
        self.addCleanup(conn.close)
        conn.request("GET", "/api/events", headers={"X-Office-Local": "1"})
        resp = conn.getresponse()
        self.assertEqual(resp.status, 200)
        pokes, hello = [], threading.Event()

        def read():
            kind = ""
            try:
                for raw in resp:
                    line = raw.decode("utf-8").strip()
                    if line.startswith("event: "):
                        kind = line[len("event: "):]
                        if kind == "hello":
                            hello.set()
                    elif line.startswith("data: ") and kind == "poke":
                        pokes.append(json.loads(line[len("data: "):]))
            except Exception:                                # noqa: BLE001 - 切断で終わる
                pass

        threading.Thread(target=read, daemon=True).start()
        self.assertTrue(hello.wait(1.0), "hello が来ない")

        def get_office():
            ui = http.client.HTTPConnection("127.0.0.1", port, timeout=3)
            try:
                ui.request("GET", "/api/office", headers={"X-Office-Local": "1"})
                return json.loads(ui.getresponse().read())
            finally:
                ui.close()

        def wait_pokes(n, timeout):
            end = time.monotonic() + timeout
            while len(pokes) < n and time.monotonic() < end:
                time.sleep(0.01)
            return len(pokes) >= n

        return SimpleNamespace(events=events, pokes=pokes, office=get_office, wait_pokes=wait_pokes)


class PokeFollowTest(SseBase):
    """反証 R115 #2（low）: 走査し直しが POKE_FRESH_WAIT を超えると poke は先に出る（UI の取り直しはイベントの前の値）。前は追いかけが
    無く、次のイベントか 15 秒のポーリングまで古いままだった（後に続くイベントの無い ❗ で最大 15 秒）。走査し直しが終わったら
    同じ seq の poke をもう 1 回送る。追いかけは走査を起こさない・失敗したらやめる（再試行は次の GET）。"""

    def test_16_a_poke_sent_before_a_slow_rescan_is_followed_up_once_it_lands(self):
        office._SWR = True
        d0, d1 = {"gen": 0}, {"gen": 1}
        self.seed(d0, age=0)
        scan = self.use_scan(Scan(d1, block=True))           # 期限（POKE_FRESH_WAIT）より長い走査
        self.patch_attr("POKE_FRESH_WAIT", 0.2)
        sse = self.open_sse()
        sse.events.emit("Stop")
        self.assertTrue(sse.wait_pokes(1, 1.0), "時間切れの poke が出ない")
        self.assertEqual(sse.office(), d0)                   # 走査し直しの前の取り直し＝前の値（時間切れの形）
        time.sleep(0.3)
        self.assertEqual(len(sse.pokes), 1, "走査が終わる前に追いかけた")
        scan.gate.set()
        self.assertTrue(sse.wait_pokes(2, 1.0), "走査し直しの後の追いかけの poke が来ない")
        self.assertEqual(sse.pokes[1], sse.pokes[0])         # 同じ seq（UI は poke ごとに取り直す）
        self.assertEqual(sse.office(), d1)                   # 追いかけの後の取り直し＝イベントの後の値
        time.sleep(0.3)
        self.assertEqual(len(sse.pokes), 2, "追いかけは 1 回だけ")
        self.assertEqual(scan.calls, 1)

    def test_16b_the_follow_up_gives_up_on_a_failed_rescan_and_never_scans_itself(self):
        office._SWR = True
        self.seed({"gen": 0}, age=0)
        scan = self.use_scan(Scan(RuntimeError("follow"), block=True))
        self.patch_attr("POKE_FRESH_WAIT", 0.2)
        sse = self.open_sse()
        err = io.StringIO()
        with mock.patch.object(sys, "stderr", err):
            sse.events.emit("Stop")
            self.assertTrue(sse.wait_pokes(1, 1.0))
            scan.gate.set()
            self.join_bg()
            time.sleep(0.4)
        self.assertEqual(len(sse.pokes), 1, "失敗した走査の後に追いかけた")
        self.assertEqual(scan.calls, 1, "追いかけが走査を起こした")
        self.assertEqual(err.getvalue().count("office の裏の走査が失敗"), 1)

    def test_16c_a_failed_rescan_that_restores_the_old_time_is_not_taken_for_fresh(self):
        """失効しないイベント（PostToolUse）の poke が GET の起こした裏の走査を待って時間切れ → その走査が失敗して前の時刻（t>0）が
        戻っても追いかけない（成功した走査の後だけ）。"""
        office._SWR = True
        d0 = {"gen": 0}
        self.seed(d0, age=office.CACHE_SEC + 1)              # 時間切れ（失効ではない）
        scan = self.use_scan(Scan(RuntimeError("restore"), block=True))
        self.patch_attr("POKE_FRESH_WAIT", 0.2)
        sse = self.open_sse()
        self.assertEqual(sse.office(), d0)                   # GET が裏の走査（落ちる）を起こす＝t は走査中
        self.assertTrue(scan.started.wait(1))
        sse.events.n += 1                                    # 失効しないイベント（キャッシュは触らない）
        sse.events.q.put({"seq": sse.events.n, "ts": time.time(), "ev": "PostToolUse", "sid": "sess-swr"})
        self.assertTrue(sse.wait_pokes(1, 1.0))
        with mock.patch.object(sys, "stderr", io.StringIO()):
            scan.gate.set()
            self.join_bg()
        self.assertGreater(office._cache["t"], 0)            # 失敗が前の時刻を戻した
        time.sleep(0.4)
        self.assertEqual(len(sse.pokes), 1, "失敗で戻った前の時刻を新鮮と取り違えて追いかけた")
        self.assertEqual(scan.calls, 1)


class SlowScanTest(SwrBase):
    """反証 R115 #5（medium）: 受け入れの 1 秒おきのプローブは SWR の速い道（メモリ）しか通らず、走査の遅さを測れなかった
    （STALE_MAX の内側では毎回前の値を即返す）。走査の側で測る: SCAN_SLOW 秒を超えた走査は「office の走査が遅い」を 1 行・
    _SCAN["slow"] を 1 つ進める（同期・裏のどちらの道でも）。速い走査は数えない。"""

    def test_17_slow_scans_are_logged_and_counted_on_both_paths(self):
        office._SWR = True
        mock_slow = mock.patch.object(office, "SCAN_SLOW", 0.25)
        mock_slow.start()
        self.addCleanup(mock_slow.stop)
        plan = iter([(0.35, {"gen": 1}), (0.0, {"gen": 2}), (0.35, {"gen": 3})])

        def scan():
            delay, value = next(plan)
            time.sleep(delay)
            return value

        patcher = mock.patch.object(office, "scan_office", scan)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(lambda: office._SCAN["thread"] is not None and office._SCAN["thread"].join(1.5))
        slow0 = office._SCAN["slow"]
        err = io.StringIO()
        with mock.patch.object(sys, "stderr", err):
            self.assertEqual(office.office_json(), {"gen": 1})   # 冷間＝同期（遅い）
            office._invalidate_cache()
            self.assertEqual(office.office_json(), {"gen": 1})   # 裏（速い）＝数えない
            self.join_bg()
            office._invalidate_cache()
            self.assertEqual(office.office_json(), {"gen": 2})   # 裏（遅い）
            self.join_bg()
        self.assertEqual(office.office_json(), {"gen": 3})
        self.assertEqual(office._SCAN["slow"], slow0 + 2)
        lines = [line for line in err.getvalue().splitlines() if "office の走査が遅い" in line]
        self.assertEqual(len(lines), 2, err.getvalue())
        self.assertIn("同期", lines[0])
        self.assertIn("裏", lines[1])


if __name__ == "__main__":
    unittest.main()
