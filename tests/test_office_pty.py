#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""R99 Phase 2: server/office_pty.py（コックピットの本物の端末）の番人。

どれも「静かに壊れる」類を 1 つずつ固定する:
  - 往復（打った文字が返る）
  - **^C が効く**（子が制御端末を持っている。持たないと走り出した claude を止められない）
  - 窓の大きさが子に届く（stty size）
  - 閉じたら**孫まで止まり、ゾンビも残らない**
  - 同時 4 本まで・閉じれば空く
  - リングは上限を超えない・切り口は改行
  - **遅い視聴者でポンプが止まらない**（止まると子が write で固まる）
  - 再接続の backlog と以降の配信の間に取りこぼしが無い
  - **出力をディスクに書く API がこのファイルに 1 つも無い**（静的に見る）
  - R111-B0 多重化 stream（1 接続で複数の端末）: id つきの out・端末ごとの順序・per-id の from・
    未知の id はその id だけ exit null・溢れたら接続ごと閉じる（固まらない）
"""
import importlib.util
import json
import os
import queue
import pathlib
import re
import signal
import sys
import tempfile
import threading
import time
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
SPEC = importlib.util.spec_from_file_location("office_pty", ROOT / "server" / "office_pty.py")
pty_mod = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(pty_mod)

ENV = {"PATH": "/usr/bin:/bin", "HOME": tempfile.gettempdir(), "LANG": "C", "TERM": "xterm-256color",
       "PS1": "$ "}


def read_until(viewer, needle, timeout=5.0, start=b""):
    """視聴者のキューから needle が来るまで読む。来なければ集めたものを返す。

    ★印は**出力にしか現れない形**で打つこと（`echo X_$((1+1))` → 待つのは `X_2`）。
    端末は打った文字を画面に返す（エコー）ので、`echo DONE` と打って `DONE` を待つと、
    コマンド行のエコーで即座に「来た」と判定して**実行を待たずに合格する**（実際に 3 本がそうだった）。"""
    buf = bytearray(start)
    end = time.time() + timeout
    while time.time() < end:
        if needle in buf:
            return bytes(buf)
        try:
            item = viewer.q.get(timeout=0.1)
        except Exception:  # noqa: BLE001  queue.Empty
            continue
        if isinstance(item, (bytes, bytearray)):
            buf += item
        elif item is None or (isinstance(item, tuple) and item[0] == "exit"):
            break
    return bytes(buf)


def read_until_re(viewer, pattern, timeout=5.0, start=b""):
    """正規表現が**出力に**現れるまで読む（エコーでは合わない形の印と組み合わせる）。"""
    buf = bytearray(start)
    end = time.time() + timeout
    while time.time() < end:
        m = re.search(pattern, buf)
        if m:
            return m, bytes(buf)
        try:
            item = viewer.q.get(timeout=0.1)
        except Exception:  # noqa: BLE001
            continue
        if isinstance(item, (bytes, bytearray)):
            buf += item
        elif item is None or (isinstance(item, tuple) and item[0] == "exit"):
            break
    return None, bytes(buf)


class _Sink:
    """SSE の受け口。do_GET をスレッドで回しながら、届いた分をいつでも読める（BytesIO は読み書きが同時に安全でない）。
    gate を渡すと、それが立つまで最初の書き込みで止まる＝読まない遅いクライアントを再現する。"""

    def __init__(self, gate=None):
        self._buf = bytearray()
        self._lock = threading.Lock()
        self._gate = gate

    def write(self, data):
        if self._gate is not None:
            self._gate.wait(10)
        with self._lock:
            self._buf += data
        return len(data)

    def flush(self):
        pass

    def getvalue(self):
        with self._lock:
            return bytes(self._buf)


def sse_events(raw):
    """応答（ヘッダ込み）→ [(event, data)]。ping（`: ping`）と未完の末尾は捨てる。"""
    _head, _, rest = raw.partition(b"\r\n\r\n")
    events = []
    for block in rest.decode("ascii").split("\n\n")[:-1]:
        if not block or block.startswith(":"):
            continue
        kind_line, _, data_line = block.partition("\n")
        events.append((kind_line[len("event: "):], data_line[len("data: "):]))
    return events


def mux_outs(test, events):
    """多重化 stream の out を id ごとに連結する。data は `<id 16 桁> <base64>`（JSON にしない）。"""
    outs = {}
    for kind, data in events:
        if kind != "out":
            continue
        test.assertRegex(data[:17], r"^[0-9a-f]{16} $", f"out の先頭が id＋空白でない: {data[:40]!r}")
        chunk = pty_mod.unb64(data[17:])
        test.assertIsNotNone(chunk, "out の本体が base64 でない")
        outs.setdefault(data[:16], bytearray()).extend(chunk)
    return outs


def pid_alive(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    # kill(0) はゾンビにも成功する＝回収済みかどうかは ps で見る
    state = os.popen(f"ps -o stat= -p {pid}").read().strip()
    return bool(state) and not state.startswith("Z")


class PtyTest(unittest.TestCase):
    def setUp(self):
        pty_mod.close_all()

    def tearDown(self):
        pty_mod.close_all()

    def open_sh(self, **kw):
        t, err = pty_mod.open_term(["/bin/sh"], cwd=tempfile.gettempdir(), env=ENV, kind="shell", **kw)
        self.assertIsNotNone(t, err)
        backlog, _, _, _, viewer = t.subscribe()
        return t, viewer, backlog

    def test_round_trip(self):
        t, v, backlog = self.open_sh()
        self.assertTrue(t.write(b"echo R99_ROUND_$((6*7))\n"))
        out = read_until(v, b"R99_ROUND_42", start=backlog)
        self.assertIn(b"R99_ROUND_42", out, out[-300:])

    def test_ctrl_c_interrupts_foreground(self):
        """制御端末が無いと 0x03 はただの文字になり、SIGINT にならない。

        ★**シェルを挟まずに**確かめる。bash は起動時に自分で端末を開き直して制御端末を取りに行くので、
        `/bin/sh` 経由だと shim が無くても ^C が効いてしまい、このテストは何も証明しない
        （実際に TIOCSCTTY を消しても通った）。本番の `claude`（node）はそれをしない＝shim が頼り。
        `sleep` は端末を開かない素のプロセスなので、制御端末を持っているかどうかがそのまま出る。"""
        t, err = pty_mod.open_term(["/bin/sleep", "30"], env=ENV, kind="shell")
        self.assertIsNotNone(t, err)
        v = t.subscribe()[-1]
        time.sleep(0.3)
        t.write(b"\x03")
        end = time.time() + 3
        while time.time() < end and t.proc.poll() is None:
            time.sleep(0.05)
        self.assertIsNotNone(t.proc.poll(), "^C が SIGINT にならない（子が制御端末を持っていない）")
        self.assertEqual(t.proc.returncode, -signal.SIGINT)

    def test_ctrl_c_through_shell(self):
        """シェル越しでも ^C で前面のジョブが止まり、次のコマンドがすぐ走る（使い勝手の確認）。"""
        t, v, backlog = self.open_sh()
        t.write(b"sleep 30\n")
        time.sleep(0.4)
        started = time.time()
        t.write(b"\x03")
        t.write(b"echo AFTER_$((3+4))\n")
        out = read_until(v, b"AFTER_7", timeout=4.0, start=backlog)
        self.assertIn(b"AFTER_7", out)
        self.assertLess(time.time() - started, 4.0)

    def test_resize_reaches_child(self):
        t, v, backlog = self.open_sh(cols=100, rows=30)
        self.assertTrue(t.resize(123, 45))
        t.write(b"stty size\n")
        out = read_until(v, b"45 123", start=backlog)
        self.assertIn(b"45 123", out, out[-200:])
        self.assertEqual((t.cols, t.rows), (123, 45))

    def test_size_is_clamped(self):
        self.assertEqual(pty_mod.clamp_size(99999, -5), (400, 5))
        self.assertEqual(pty_mod.clamp_size("x", None), (100, 30))

    def test_close_kills_grandchildren_and_reaps(self):
        t, v, backlog = self.open_sh()
        t.write(b"sleep 300 & echo GRAND=$!\n")
        m, out = read_until_re(v, rb"GRAND=(\d+)", start=backlog)   # エコーは GRAND=$! なので合わない
        self.assertIsNotNone(m, out[-200:])
        grand = int(m.group(1))
        self.assertTrue(pid_alive(grand))
        child = t.proc.pid
        t.close()
        time.sleep(0.3)
        self.assertFalse(pid_alive(grand), "孫（sleep）が生き残っている＝プロセスグループを止めていない")
        self.assertIsNotNone(t.proc.returncode, "子が回収されていない（ゾンビが残る）")
        self.assertFalse(pid_alive(child))
        self.assertFalse(t.alive)

    def test_close_kills_jobs_in_other_groups_that_ignore_hup(self):
        """対話シェルのジョブは別のプロセスグループに入る。HUP を無視したジョブは、
        シェルのグループへの kill では止まらない（別モデルレビューが実測）＝端末の名簿で止める。"""
        t, err = pty_mod.open_term(["/bin/sh", "-i"], env=ENV, kind="shell")
        self.assertIsNotNone(t, err)
        backlog, _, _, _, v = t.subscribe()
        t.write(b"trap '' HUP; sleep 300 & echo JOB=$!\n")
        m, out = read_until_re(v, rb"JOB=(\d+)", start=backlog)
        self.assertIsNotNone(m, out[-200:])
        job = int(m.group(1))
        self.assertTrue(pid_alive(job))
        self.assertNotEqual(os.getpgid(job), os.getpgid(t.proc.pid), "ジョブ制御が効いていない（前提が崩れた）")
        t.close()
        time.sleep(0.3)
        self.assertFalse(pid_alive(job), "HUP を無視した別グループのジョブが、端末を閉じても生き残った")

    def test_limit_four_and_frees_on_close(self):
        terms = []
        for _ in range(pty_mod.MAX_TERMS):
            t, err = pty_mod.open_term(["/bin/cat"], env=ENV, kind="shell")
            self.assertIsNotNone(t, err)
            terms.append(t)
        t5, err = pty_mod.open_term(["/bin/cat"], env=ENV, kind="shell")
        self.assertIsNone(t5)
        self.assertEqual(err, "too_many")
        terms[0].close()
        t6, err = pty_mod.open_term(["/bin/cat"], env=ENV, kind="shell")
        self.assertIsNotNone(t6, err)

    def test_ring_is_bounded_and_cut_at_newline(self):
        t, v, backlog = self.open_sh()
        n = pty_mod.RING_BYTES // 20          # 1 行 ~35 バイト × n ＝ リングの 1.7 倍前後を流す
        t.write(f"i=0; while [ $i -lt {n} ]; do echo line-$i-xxxxxxxxxxxxxxxxxxxxxxx; i=$((i+1)); done; echo RING_$((5*5))\n".encode())
        out = read_until(v, b"RING_25", timeout=20.0, start=backlog)
        self.assertIn(b"RING_25", out[-200:])
        ring = t.backlog()
        self.assertLessEqual(len(ring), pty_mod.RING_BYTES)
        self.assertGreater(len(ring), pty_mod.RING_BYTES // 2)
        self.assertTrue(ring.startswith(b"line-"), ring[:40])

    def test_slow_viewer_does_not_stall_pump(self):
        t, v_fast, backlog = self.open_sh()
        v_slow = t.subscribe()[-1]           # 一度も読まない視聴者
        # 溢れるかは「塊の数」で決まる（キューは塊の個数）。負荷が高いと 3000 行が数個の大きな塊で届き、512 に届かず
        # 緑にならなかった（load 145〜416 で実測）。遅い視聴者だけキューを小さくし、出力も sleep で塊に分ける＝負荷に依らない
        v_slow.q = queue.Queue(maxsize=8)
        t.write(("i=0; while [ $i -lt 60 ]; do echo spam-$i; sleep 0.02; i=$((i+1)); done; echo SLOW_$((8*8))\n").encode())
        out = read_until(v_fast, b"SLOW_64", timeout=30.0, start=backlog)
        self.assertIn(b"SLOW_64", out, "遅い視聴者のせいでポンプが止まった")
        self.assertTrue(v_slow.dropped, "溢れた視聴者を切っていない（キューが無限に伸びる）")

    def test_backlog_then_live_has_no_gap(self):
        t, v, backlog = self.open_sh()
        t.write(b"echo FIRST_$((2+9))\n")
        read_until(v, b"FIRST_11", start=backlog)
        backlog2, _, _, _, v2 = t.subscribe()        # 途中から見始めた視聴者
        self.assertIn(b"FIRST_11", backlog2)     # backlog から
        t.write(b"echo SECOND_$((3+9))\n")
        out = read_until(v2, b"SECOND_12", start=b"")
        self.assertIn(b"SECOND_12", out)         # 以降の配信から（backlog を混ぜずに見る）

    def test_exit_is_reported(self):
        t, v, backlog = self.open_sh()
        t.write(b"exit 7\n")
        end = time.time() + 5
        got = None
        while time.time() < end and got is None:
            try:
                item = v.q.get(timeout=0.2)
            except Exception:  # noqa: BLE001
                continue
            if isinstance(item, tuple) and item[0] == "exit":
                got = item
        self.assertEqual(got, ("exit", 7))
        self.assertFalse(t.alive)
        pub = t.public()
        self.assertEqual(pub["exitCode"], 7)

    def test_public_carries_no_output_or_paths(self):
        t, v, backlog = self.open_sh()
        t.write(b"echo SECRET_TOKEN_$((100+23))\n")
        read_until(v, b"SECRET_TOKEN_123", start=backlog)
        pub = repr(pty_mod.list_public())
        self.assertNotIn("SECRET_TOKEN", pub)
        self.assertNotIn(tempfile.gettempdir(), pub)
        self.assertNotIn("/bin/sh", pub)

    def test_full_viewers_evict_the_oldest(self):
        """満員なら古い視聴者を追い出して新しい方を入れる。拒否すると、素早いタブ切替で残った
        古い購読（サーバーは次の ping まで気づかない）が枠を埋め、見ている画面が止まる（レビュー）。"""
        t, first, _ = self.open_sh()
        t.unsubscribe(first)                     # 数えやすいよう 0 本から始める
        subs = [t.subscribe()[-1] for _ in range(pty_mod.MAX_VIEWERS)]
        self.assertEqual(t.viewers(), pty_mod.MAX_VIEWERS)
        newest = t.subscribe()[-1]
        self.assertEqual(t.viewers(), pty_mod.MAX_VIEWERS, "上限を超えて抱えている")
        self.assertTrue(subs[0].dropped, "いちばん古い視聴者を追い出していない")
        self.assertFalse(any(v.dropped for v in subs[1:]), "古い 1 人以外まで追い出した")
        self.assertFalse(newest.dropped)

    def test_resume_from_offset_without_reset(self):
        """読み終えた通し番号を渡せば、続きだけが来る（reset しない＝画面モードが残る）。"""
        t, v, backlog = self.open_sh()
        t.write(b"echo PART_$((1+1))\n")
        out = read_until(v, b"PART_2", start=backlog)
        t.unsubscribe(v)
        offset = t._base + len(t.backlog())      # ここまで読んだ
        t.write(b"echo MORE_$((2+2))\n")
        time.sleep(0.5)
        more, start, reset, prefix, v2 = t.subscribe(offset)
        self.assertFalse(reset, "続きがリングにあるのに頭から流し直した")
        self.assertEqual(start, offset)
        self.assertIn(b"MORE_4", more)
        self.assertNotIn(b"PART_2\r\n", more, "読み終えた分まで二重に送っている")

    def test_modes_are_restored_after_they_leave_the_ring(self):
        """TUI が最初に立てた画面モードがリングから押し出されても、頭から流し直すときに立て直す。"""
        t, v, backlog = self.open_sh()
        t.write(b"printf '\\033[?1049h\\033[?2004hMODES_ON\\n'\n")
        read_until(v, b"MODES_ON\r\n", start=backlog)
        n = pty_mod.RING_BYTES // 20
        t.write(f"i=0; while [ $i -lt {n} ]; do echo fill-$i-xxxxxxxxxxxxxxxxxxxxxxx; i=$((i+1)); done; echo FILL_$((9*9))\n".encode())
        read_until(v, b"FILL_81", timeout=20.0)
        self.assertNotIn(b"\x1b[?1049h", t.backlog(), "前提: モードはもうリングに無い")
        _, _, reset, prefix, _ = t.subscribe(None)
        self.assertTrue(reset)
        self.assertIn(b"\x1b[?1049h", prefix, "代替画面が戻らない（claude の画面が壊れる）")
        self.assertIn(b"\x1b[?2004h", prefix, "貼り付けモードが戻らない")
        self.assertTrue(prefix.startswith(b"\x1b[?1049h"), "代替画面は最初に入る")

    def test_closing_an_exited_term_never_touches_a_reused_tty(self):
        """自然終了で master を手放した端末の名前は、別の端末に再利用されうる。
        古いタブを閉じたときにその名前で名簿を引くと、**新しい端末のプロセスを殺す**（レビュー high）。"""
        a, err = pty_mod.open_term(["/bin/sh", "-c", "exit 0"], env=ENV, kind="shell")
        self.assertIsNotNone(a, err)
        end = time.time() + 5
        while a.alive and time.time() < end:
            time.sleep(0.05)
        self.assertFalse(a.alive)
        b, v, backlog = self.open_sh()
        b.write(b"sleep 300 & echo BJOB=$!\n")
        m, out = read_until_re(v, rb"BJOB=(\d+)", start=backlog)
        self.assertIsNotNone(m, out[-200:])
        victim = int(m.group(1))
        a.tty = b.tty                            # 名前の再利用を再現する
        pty_mod.close(a.id)
        time.sleep(0.3)
        self.assertTrue(pid_alive(victim), "閉じた古い端末が、同じ名前の新しい端末のプロセスを殺した")
        self.assertTrue(b.alive)

    def test_idle_close_counts_the_list_poll_as_watching(self):
        """購読していない（裏のタブの）端末でも、一覧を取りに来ている間は閉じない。"""
        t, v, _ = self.open_sh()
        t.unsubscribe(v)
        old = pty_mod.IDLE_CLOSE_SEC
        pty_mod.IDLE_CLOSE_SEC = 0.4
        try:
            for _ in range(4):
                time.sleep(0.2)
                pty_mod.list_public(touch=True)  # コックピットが開いている
            time.sleep(0.1)
            self.assertTrue(t.alive, "コックピットを開いているのに、裏の端末を閉じた")
            time.sleep(0.6)
            pty_mod.list_public()                # 見ていない（touch しない）
            time.sleep(1.8)
            self.assertFalse(t.alive, "誰も開いていないのに閉じない（野良が永久に残る）")
        finally:
            pty_mod.IDLE_CLOSE_SEC = old

    def test_mux_viewer_tags_items_and_flags_a_lost_exit(self):
        """多重化の視聴者は共有キューへ (tid, item) で積む。共有キューは別の端末の出力でも満杯になるので、
        exit を積めなかったら dropped を立てる（立てないと、その接続は来ない exit を永久に待つ）。"""
        t, err = pty_mod.open_term(["/bin/cat"], env=ENV, kind="shell")
        self.assertIsNotNone(t, err)
        shared = queue.Queue(maxsize=2)
        v = pty_mod.mux_viewer(shared, t.id)
        _, _, _, _, got = t.subscribe(viewer=v)
        self.assertIs(got, v, "作り置きの視聴者を使っていない")
        self.assertEqual(t.viewers(), 1)
        t._publish(b"x")
        self.assertEqual(shared.get_nowait(), (t.id, b"x"))
        t._publish(b"y")
        shared.put_nowait(("f" * 16, b"other"))      # 別の端末の出力で満杯
        self.assertFalse(v.dropped)
        t.close()
        self.assertTrue(v.dropped, "exit を積めなかったのに dropped を立てていない（接続が閉じない）")
        # 終わった端末に後から繋いだ多重化の視聴者には、exit がすぐ積まれる
        shared2 = queue.Queue(maxsize=4)
        t.subscribe(viewer=pty_mod.mux_viewer(shared2, t.id))
        tid, item = shared2.get_nowait()
        self.assertEqual((tid, item[0]), (t.id, "exit"))

    def test_input_decoding(self):
        self.assertEqual(pty_mod.unb64(pty_mod.b64(b"\x03ls\n")), b"\x03ls\n")
        self.assertIsNone(pty_mod.unb64("not base64 !!"))
        self.assertIsNone(pty_mod.unb64(123))
        self.assertIsNone(pty_mod.unb64("A" * (pty_mod.INPUT_MAX * 2)))



class ModeTrackingTest(unittest.TestCase):
    """画面モードの追跡の境界（レビュー 3 巡目）。純粋関数と、小さなリングで実際に切らせる形で見る。"""

    def test_ris_clears_tracked_modes(self):
        modes = {}
        pty_mod._scan_modes(b"\x1b[?1049h\x1b[?2004h", modes)
        self.assertEqual(modes, {"1049": "h", "2004": "h"})
        pty_mod._scan_modes(b"hello\x1bcworld", modes)          # ESC c＝全リセット
        self.assertEqual(modes, {}, "リセット後も古いモードを覚えている（再接続で復活する）")
        pty_mod._scan_modes(b"\x1bc\x1b[?1h", modes)            # リセットの後の設定は生きる（順番どおり）
        self.assertEqual(modes, {"1": "h"})

    def test_safe_cut_never_splits_a_control_sequence(self):
        buf = b"abc\x1b[?2004lxyz"
        self.assertEqual(pty_mod._safe_cut(buf, 5), 3, "CSI の途中で切った")
        self.assertEqual(pty_mod._safe_cut(buf, 10), 3, "終端の手前で切った")
        self.assertEqual(pty_mod._safe_cut(buf, 11), 11, "終わった列の後ろまで戻しすぎ")
        self.assertEqual(pty_mod._safe_cut(b"abc\x1b", 4), 3, "ESC だけで切った")
        self.assertEqual(pty_mod._safe_cut(b"plain text only", 7), 7)

    def test_split_unset_is_not_lost_when_trimming(self):
        """改行の無い TUI 出力で、解除列がリングの切り口にまたがっても、解除を見落とさない。"""
        old = pty_mod.RING_BYTES
        pty_mod.RING_BYTES = 40
        try:
            t, err = pty_mod.open_term(["/bin/cat"], env=ENV, kind="shell")
            self.assertIsNotNone(t, err)
            t._publish(b"\x1b[?2004h" + b"A" * 30)           # 設定（改行なし）＝38 バイト
            # 解除列は 65〜72 バイト目。全体 38+27+8+36=109 → 切り口 109-40=69 ＝**解除列のど真ん中**
            t._publish(b"B" * 27 + b"\x1b[?2004l" + b"C" * 36)
            t._publish(b"D" * 60)                              # 解除を完全にリングの外へ押し出す
            self.assertNotIn(b"2004", t.backlog(), "前提: 解除列はもうリングの外")
            _, _, reset, prefix, _ = t.subscribe(None)
            self.assertTrue(reset)
            self.assertNotIn(b"2004h", prefix, "解除を見落とし、切ったはずの貼り付けモードを復活させた")
        finally:
            pty_mod.RING_BYTES = old
            pty_mod.close_all()

class TermApiTest(unittest.TestCase):
    """HTTP の口。**門**（loopback＋CSRF＋Host）と、**出力がどこにも漏れない**ことを見る。"""

    @classmethod
    def setUpClass(cls):
        import io  # noqa: F401
        cls._tmp = tempfile.TemporaryDirectory()
        cls.home = pathlib.Path(cls._tmp.name)
        cls._old = {k: os.environ.get(k) for k in ("OFFICE_HOME", "OFFICE_PTY_SHELL")}
        os.environ["OFFICE_HOME"] = str(cls.home)
        os.environ["OFFICE_PTY_SHELL"] = "/bin/sh"
        spec = importlib.util.spec_from_file_location("office_server_pty_test",
                                                      ROOT / "server" / "office_server.py")
        cls.office = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.office)
        cls.pty = cls.office.office_pty        # サーバーが実際に使っている方（登録簿を共有する）

    @classmethod
    def tearDownClass(cls):
        cls.pty.close_all()
        for k, v in cls._old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        cls._tmp.cleanup()

    def tearDown(self):
        self.pty.close_all()

    def handler(self, method, path, data=None, headers=None, client="127.0.0.1", wfile=None):
        import io
        body = b"" if data is None else json.dumps(data).encode("utf-8")
        h = self.office.Handler.__new__(self.office.Handler)
        h.path, h.command = path, method
        h.request_version = "HTTP/1.1"
        h.requestline = f"{method} {path} HTTP/1.1"
        h.client_address = (client, 50000)
        base = {"Host": "127.0.0.1:4780", "X-Office-Local": "1",
                "Content-Type": "application/json", "Content-Length": str(len(body))}
        for k, v in (headers or {}).items():
            if v is None:
                base.pop(k, None)
            else:
                base[k] = v
        h.headers = base
        h.rfile = io.BytesIO(body)
        h.wfile = io.BytesIO() if wfile is None else wfile
        return h

    def call(self, method, path, data=None, headers=None, client="127.0.0.1"):
        h = self.handler(method, path, data, headers, client)
        getattr(h, f"do_{method}")()
        raw = h.wfile.getvalue()
        head, _, rest = raw.partition(b"\r\n\r\n")
        return int(head.split(b"\r\n", 1)[0].split()[1]), rest

    def stream_bg(self, path, sink=None):
        """長い GET（SSE）をスレッドで開く。(thread, sink) を返す＝届いた分は sse_events(sink.getvalue())。"""
        sink = sink or _Sink()
        h = self.handler("GET", path, wfile=sink)
        th = threading.Thread(target=h.do_GET, daemon=True, name="test-mux")
        th.start()
        return th, sink

    def wait_events(self, sink, pred, timeout=8.0, why=""):
        end = time.time() + timeout
        while time.time() < end:
            events = sse_events(sink.getvalue())
            if pred(events):
                return events
            time.sleep(0.05)
        self.fail(f"待ちきれない: {why}\n{sse_events(sink.getvalue())[-6:]}")

    def open_shell_api(self):
        code, body = self.call("POST", "/api/term/open", {"kind": "shell"})
        self.assertEqual(code, 200, body)
        return json.loads(body)["term"]["id"]

    def type_api(self, tid, text):
        code, _ = self.call("POST", "/api/term/input", {"id": tid, "data": self.pty.b64(text.encode())})
        self.assertEqual(code, 200)

    # ── 門 ──────────────────────────────────────────────────────────
    def test_gate_matrix(self):
        """どれか 1 つでも欠けたら 403。端末は任意コマンドの実行口なので、ここが唯一の壁。"""
        bad = [
            ({"X-Office-Local": None}, "127.0.0.1", "CSRF ヘッダ無し"),
            ({"Origin": "https://evil.example"}, "127.0.0.1", "別オリジン"),
            ({"Host": "evil.example"}, "127.0.0.1", "DNS リバインディング"),
            ({}, "192.168.1.20", "loopback 以外"),
        ]
        for headers, client, why in bad:
            for method, path, data in (("POST", "/api/term/open", {"kind": "shell"}),
                                       ("POST", "/api/term/input", {"id": "0" * 16, "data": "aGk="}),
                                       ("GET", "/api/term/list", None),
                                       ("GET", "/api/term/stream?id=" + "0" * 16, None),
                                       ("GET", "/api/term/streams?ids=" + "0" * 16, None)):
                code, _ = self.call(method, path, data, headers, client)
                self.assertEqual(code, 403, f"{why}: {method} {path} が {code}")
        self.assertEqual(self.pty.list_public(), [], "門で弾いたはずなのに端末が起きている")

    def test_kind_is_the_only_input(self):
        code, body = self.call("POST", "/api/term/open", {"kind": "rm -rf /"})
        self.assertEqual(code, 400)
        code, body = self.call("POST", "/api/term/open", {"kind": "claude"})
        self.assertEqual(code, 404, "claude はプロジェクトの外で起こさない")
        # argv を直接渡そうとしても使われない
        code, body = self.call("POST", "/api/term/open",
                               {"kind": "shell", "argv": ["/usr/bin/touch", "/tmp/pwned"], "cmd": "x"})
        self.assertEqual(code, 200)
        term = json.loads(body)["term"]
        self.assertEqual(term["kind"], "shell")
        self.assertFalse(pathlib.Path("/tmp/pwned").exists() and
                         pathlib.Path("/tmp/pwned").stat().st_mtime > time.time() - 5)

    def test_claude_spec_pins_session_id(self):
        old = self.office._launch_target_for
        self.office._launch_target_for = lambda pid: (str(self.home), "p") if pid == "abc123" else ("", "")
        os.environ["OFFICE_CLAUDE_BIN"] = "/bin/echo"
        try:
            argv, cwd, env, session, err = self.office.term_spec("claude", "abc123")
            self.assertEqual(err, "")
            self.assertEqual(argv[0], "/bin/echo")
            self.assertEqual(argv[1], "--session-id")
            self.assertRegex(argv[2], r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
            self.assertEqual(session, argv[2], "端末とセッションの対応を確定で持っていない")
            self.assertEqual(cwd, str(self.home))
            self.assertEqual(env["TERM"], "xterm-256color")
            self.assertNotIn("OFFICE_HOME", env, "daemon の env を子へ漏らしている")
            _, _, _, _, err = self.office.term_spec("claude", "unknown")
            self.assertEqual(err, "no_project")
        finally:
            self.office._launch_target_for = old
            os.environ.pop("OFFICE_CLAUDE_BIN", None)

    # ── 往復と、漏れないこと ─────────────────────────────────────────
    def test_output_goes_nowhere_but_the_stream(self):
        code, body = self.call("POST", "/api/term/open", {"kind": "shell", "cols": 90, "rows": 20})
        self.assertEqual(code, 200, body)
        tid = json.loads(body)["term"]["id"]
        term = self.pty.get(tid)
        backlog, _, _, _, viewer = term.subscribe()
        marker_cmd = "echo LEAK_CANARY_$((31337+1))\n"
        code, _ = self.call("POST", "/api/term/input",
                            {"id": tid, "data": self.pty.b64(marker_cmd.encode())})
        self.assertEqual(code, 200)
        out = read_until(viewer, b"LEAK_CANARY_31338", start=backlog)
        self.assertIn(b"LEAK_CANARY_31338", out)
        # office_json（＝中継へ行く元）にも /api/term/list にも出ない
        self.assertNotIn("LEAK_CANARY", json.dumps(self.office.office_json(), ensure_ascii=False))
        code, lst = self.call("GET", "/api/term/list")
        self.assertEqual(code, 200)
        self.assertNotIn(b"LEAK_CANARY", lst)
        # ディスクのどこにも落ちていない（daemon の HOME 配下を総なめ）
        hits = []
        for f in self.home.rglob("*"):
            if f.is_file():
                try:
                    if b"LEAK_CANARY_31338" in f.read_bytes():
                        hits.append(str(f))
                except OSError:
                    pass
        self.assertEqual(hits, [], f"端末の出力がディスクに落ちた: {hits}")

    def test_stream_round_trip(self):
        term, err = self.pty.open_term(["/bin/sh", "-c", "echo STREAM_$((2*21)); exit 3"],
                                       env=ENV, kind="shell")
        self.assertIsNotNone(term, err)
        time.sleep(0.3)
        code, raw = self.call("GET", f"/api/term/stream?id={term.id}")
        self.assertEqual(code, 200)
        events = [e for e in raw.decode("ascii").split("\n\n") if e.strip()]
        kinds = [e.split("\n", 1)[0].replace("event: ", "") for e in events]
        self.assertEqual(kinds[0], "hello")
        self.assertEqual(kinds[-1], "exit")
        out = b"".join(self.pty.unb64(e.split("data: ", 1)[1]) or b""
                       for e in events if e.startswith("event: out"))
        self.assertIn(b"STREAM_42", out)
        self.assertEqual(json.loads(events[-1].split("data: ", 1)[1])["code"], 3)

    def test_resize_close_and_unknown_id(self):
        code, body = self.call("POST", "/api/term/open", {"kind": "shell"})
        tid = json.loads(body)["term"]["id"]
        code, body = self.call("POST", "/api/term/resize", {"id": tid, "cols": 132, "rows": 40})
        self.assertEqual((code, json.loads(body)["ok"]), (200, True))
        term = self.pty.get(tid)
        code, body = self.call("POST", "/api/term/close", {"id": tid})
        self.assertEqual(code, 200)
        self.assertFalse(term.alive)
        self.assertIsNone(self.pty.get(tid), "本人が閉じた端末が一覧に残っている（死んだタブが居座る）")
        code, _ = self.call("POST", "/api/term/input", {"id": "f" * 16, "data": "aGk="})
        self.assertEqual(code, 404)
        code, _ = self.call("GET", "/api/term/stream?id=../../etc")
        self.assertEqual(code, 400)

    def test_single_stream_unchanged(self):
        """多重化を足しても、単一の /api/term/stream の形は今のまま（クライアントの移行は別ステップ）。
        out は素の base64（id を前に付けない）・exit は {"code"} だけ。"""
        term, err = self.pty.open_term(["/bin/sh", "-c", "echo SINGLE_$((3*3)); exit 4"], env=ENV, kind="shell")
        self.assertIsNotNone(term, err)
        time.sleep(0.3)
        code, raw = self.call("GET", f"/api/term/stream?id={term.id}")
        self.assertEqual(code, 200)
        events = sse_events(b"\r\n\r\n" + raw)
        self.assertEqual(events[0][0], "hello")
        self.assertEqual(json.loads(events[0][1])["id"], term.id)
        outs = [d for k, d in events if k == "out"]
        self.assertTrue(outs)
        self.assertTrue(all(" " not in d for d in outs), "単一 stream の out に id が付いた（形が変わった）")
        self.assertIn(b"SINGLE_9", b"".join(self.pty.unb64(d) for d in outs))
        self.assertEqual(events[-1], ("exit", json.dumps({"code": 4})))

    # ── R111-B0 多重化 stream（1 接続で複数の端末）──────────────────────
    def test_mux_round_trip(self):
        """2 本を 1 接続で。out の先頭 16 文字が id・各端末へ打った印はその id の out にだけ出る・閉じると各 id に exit。"""
        a, b = self.open_shell_api(), self.open_shell_api()
        th, sink = self.stream_bg(f"/api/term/streams?ids={a},{b}")
        events = self.wait_events(sink, lambda ev: sum(k == "hello" for k, _ in ev) == 2, why="hello 2 つ")
        hellos = [json.loads(d) for k, d in events if k == "hello"]
        self.assertEqual([h["id"] for h in hellos], [a, b], "hello が ids の順に来ない")
        for h in hellos:
            self.assertTrue(h["reset"])
            self.assertIsInstance(h["from"], int)
            self.assertIn("cols", h)
        self.type_api(a, "echo MUXA_$((40+1))\n")
        self.type_api(b, "echo MUXB_$((40+2))\n")
        events = self.wait_events(
            sink, lambda ev: (b"MUXA_41" in mux_outs(self, ev).get(a, b"")
                              and b"MUXB_42" in mux_outs(self, ev).get(b, b"")),
            why="両方の印")
        outs = mux_outs(self, events)
        self.assertEqual(set(outs), {a, b})
        self.assertNotIn(b"MUXB_42", outs[a], "b の出力が a に混ざった")
        self.assertNotIn(b"MUXA_41", outs[b], "a の出力が b に混ざった")
        self.assertEqual(self.pty.get(a).viewers(), 1, "多重化の視聴者が端末ごとに 1 人でない")
        self.call("POST", "/api/term/close", {"id": a})
        self.call("POST", "/api/term/close", {"id": b})
        th.join(8)
        self.assertFalse(th.is_alive(), "全部の端末が終わったのに接続が閉じない")
        events = sse_events(sink.getvalue())
        exits = [json.loads(d) for k, d in events if k == "exit"]
        self.assertEqual(sorted(e["id"] for e in exits), sorted([a, b]))
        self.assertEqual(events[-1][0], "exit")

    def test_mux_from_resumes_per_id(self):
        """from は id ごと。a は読み終えた位置から（reset:false・読んだ分を二重に送らない）、b は頭から（reset:true）。"""
        a, err = self.pty.open_term(["/bin/sh"], env=ENV, kind="shell")
        self.assertIsNotNone(a, err)
        b, err = self.pty.open_term(["/bin/sh"], env=ENV, kind="shell")
        self.assertIsNotNone(b, err)
        backlog, _, _, _, v = a.subscribe()
        a.write(b"echo PART_$((1+1))\n")
        read_until(v, b"PART_2", start=backlog)
        offset = a._base + len(a.backlog())       # ここまで読んだ
        a.unsubscribe(v)
        a.write(b"echo MORE_$((2+2))\n")
        b.write(b"echo BEE_$((3+3))\n")
        time.sleep(0.5)
        # ids に無い id の from は無視する（クライアントが古い控えを持っていても 400 にしない）
        th, sink = self.stream_bg(f"/api/term/streams?ids={a.id},{b.id}&from={a.id}:{offset},{'e' * 16}:5")
        events = self.wait_events(
            sink, lambda ev: (sum(k == "hello" for k, _ in ev) == 2
                              and b"MORE_4" in mux_outs(self, ev).get(a.id, b"")
                              and b"BEE_6" in mux_outs(self, ev).get(b.id, b"")),
            why="hello 2 つと backlog")
        hello = {json.loads(d)["id"]: json.loads(d) for k, d in events if k == "hello"}
        self.assertFalse(hello[a.id]["reset"], "続きがリングにあるのに頭から流し直した")
        self.assertEqual(hello[a.id]["from"], offset)
        self.assertTrue(hello[b.id]["reset"], "from の無い端末を頭から流していない")
        self.assertEqual(hello[b.id]["from"], b._base)
        outs = mux_outs(self, events)
        self.assertNotIn(b"PART_2\r\n", outs[a.id], "読み終えた分まで二重に送っている")
        self.pty.close(a.id)
        self.pty.close(b.id)
        th.join(8)
        self.assertFalse(th.is_alive())

    def test_mux_rejects_bad_ids(self):
        ids5 = [f"{i:016x}" for i in range(1, self.pty.MAX_TERMS + 2)]
        bad = {
            "ids が上限を超える": "?ids=" + ",".join(ids5),
            "重複": f"?ids={'a' * 16},{'a' * 16}",
            "16 進でない": "?ids=" + "z" * 16,
            "長さ違い": "?ids=" + "a" * 15,
            "パスもどき": "?ids=../../etc",
            "空": "?ids=",
            "無し": "",
            "ids が 2 つ": f"?ids={'a' * 16}&ids={'b' * 16}",
            "from の形": f"?ids={'a' * 16}&from=xyz",
            "from の重複": f"?ids={'a' * 16}&from={'a' * 16}:1,{'a' * 16}:2",
        }
        for why, qs in bad.items():
            code, body = self.call("GET", "/api/term/streams" + qs)
            self.assertEqual(code, 400, f"{why}: {code} {body[:80]!r}")
        # 未知の id だけ＝404 にせず、各 id に exit null を送って閉じる
        u1, u2 = "1" * 16, "2" * 16
        code, raw = self.call("GET", f"/api/term/streams?ids={u1},{u2}")
        self.assertEqual(code, 200)
        self.assertEqual(sse_events(b"\r\n\r\n" + raw),
                         [("exit", json.dumps({"id": u1, "code": None}, separators=(",", ":"))),
                          ("exit", json.dumps({"id": u2, "code": None}, separators=(",", ":")))])
        # 未知の id が混ざっても、他の端末は生きて流れ続ける（一覧との競合で接続全体を殺さない）
        live = self.open_shell_api()
        th, sink = self.stream_bg(f"/api/term/streams?ids={u1},{live}")
        self.wait_events(sink, lambda ev: any(k == "hello" for k, _ in ev), why="生きている方の hello")
        self.type_api(live, "echo ALIVE_$((5*5))\n")
        events = self.wait_events(sink, lambda ev: b"ALIVE_25" in mux_outs(self, ev).get(live, b""),
                                  why="生きている方の出力")
        exits = [json.loads(d) for k, d in events if k == "exit"]
        self.assertEqual(exits, [{"id": u1, "code": None}], "未知の id 以外まで終わらせた")
        self.assertTrue(th.is_alive())
        self.call("POST", "/api/term/close", {"id": live})
        th.join(8)
        self.assertFalse(th.is_alive())

    def test_mux_keeps_per_term_order(self):
        """2 本が同時に多量に出す → 各 id の out を連結すると元の順。まとめ送りで引いた別の端末の塊を
        put で戻すと、その端末の後続より後ろに回って順序が崩れる（carry で持つのが正）。"""
        n = 1500
        script = ("sleep 0.4; i=0; while [ $i -lt {n} ]; do echo {tag}-$i; "
                  "[ $((i % 40)) -eq 0 ] && sleep 0.01; i=$((i+1)); done; echo {tag}_END_$((2*5))")
        a, err = self.pty.open_term(["/bin/sh", "-c", script.format(n=n, tag="AA")], env=ENV, kind="shell")
        self.assertIsNotNone(a, err)
        b, err = self.pty.open_term(["/bin/sh", "-c", script.format(n=n, tag="BB")], env=ENV, kind="shell")
        self.assertIsNotNone(b, err)
        code, raw = self.call("GET", f"/api/term/streams?ids={a.id},{b.id}")   # 両方が exit するまで読む
        self.assertEqual(code, 200)
        events = sse_events(b"\r\n\r\n" + raw)
        outs = mux_outs(self, events)
        for term, tag, other in ((a, b"AA", b"BB"), (b, b"BB", b"AA")):
            out = bytes(outs[term.id])
            self.assertIn(tag + b"_END_10", out)
            got = [int(x) for x in re.findall(tag + rb"-(\d+)\r\n", out)]
            self.assertEqual(got, list(range(n)), f"{tag!r} の出力の順序が崩れた／欠けた")
            self.assertNotIn(other + b"-", out, "別の端末の出力が混ざった")
        # 前提: 本当に入り混じって届いた（交互になっていなければ順序の検査は何も証明しない）
        order = [d[:16] for k, d in events if k == "out"]
        switches = sum(1 for x, y in zip(order, order[1:]) if x != y)
        self.assertGreaterEqual(switches, 2, f"2 本の出力が交互に届いていない（switches={switches}）")
        self.assertEqual(sorted(json.loads(d)["id"] for k, d in events if k == "exit"), sorted([a.id, b.id]))

    def test_mux_overflow_closes_the_connection(self):
        """読まない遅いクライアントで共有キューが溢れたら、**接続ごと閉じる**（固まらない・ポンプも止めない）。
        クライアントは全 id の from で繋ぎ直す＝単一 stream と同じ意味。exit は送らない（端末は生きている）。"""
        old = self.pty.VIEWER_QUEUE
        self.pty.VIEWER_QUEUE = 4                 # 2 本で 8 件のキュー
        try:
            a, err = self.pty.open_term(
                ["/bin/sh", "-c", "sleep 0.2; i=0; while [ $i -lt 300 ]; do echo flood-$i; sleep 0.003; "
                                  "i=$((i+1)); done; sleep 30"], env=ENV, kind="shell")
            self.assertIsNotNone(a, err)
            b, err = self.pty.open_term(["/bin/sleep", "30"], env=ENV, kind="shell")
            self.assertIsNotNone(b, err)
            gate = threading.Event()
            th, sink = self.stream_bg(f"/api/term/streams?ids={a.id},{b.id}", sink=_Sink(gate))
            # 最初の書き込み（ヘッダ）で止めたまま、a が溢れさせる
            end = time.time() + 10
            while time.time() < end and not (b.viewers() == 1 and a.viewers() == 0):
                time.sleep(0.05)
            self.assertEqual(a.viewers(), 0, "溢れた多重化の視聴者を切っていない（キューが無限に伸びる）")
            gate.set()
            th.join(8)
            self.assertFalse(th.is_alive(), "溢れたのに接続が閉じない（クライアントが繋ぎ直せない）")
            self.assertTrue(a.alive and b.alive)
            events = sse_events(sink.getvalue())
            self.assertFalse([d for k, d in events if k == "exit"], "生きている端末に exit を送った")
            self.assertEqual((a.viewers(), b.viewers()), (0, 0), "閉じた接続の購読が残っている")
        finally:
            self.pty.VIEWER_QUEUE = old

    def test_mux_output_goes_nowhere_but_the_stream(self):
        """多重化でも、出力は stream の外（office_json・一覧・ディスク）へ 1 バイトも出ない。"""
        tid = self.open_shell_api()
        other = self.open_shell_api()
        th, sink = self.stream_bg(f"/api/term/streams?ids={tid},{other}")
        self.wait_events(sink, lambda ev: sum(k == "hello" for k, _ in ev) == 2, why="hello 2 つ")
        self.type_api(tid, "echo LEAK_MUX_$((31337+2))\n")
        self.wait_events(sink, lambda ev: b"LEAK_MUX_31339" in mux_outs(self, ev).get(tid, b""),
                         why="印が stream に届く")
        self.assertNotIn("LEAK_MUX", json.dumps(self.office.office_json(), ensure_ascii=False))
        code, lst = self.call("GET", "/api/term/list")
        self.assertEqual(code, 200)
        self.assertNotIn(b"LEAK_MUX", lst)
        hits = []
        for f in self.home.rglob("*"):
            if f.is_file():
                try:
                    if b"LEAK_MUX_31339" in f.read_bytes():
                        hits.append(str(f))
                except OSError:
                    pass
        self.assertEqual(hits, [], f"端末の出力がディスクに落ちた: {hits}")
        self.call("POST", "/api/term/close", {"id": tid})
        self.call("POST", "/api/term/close", {"id": other})
        th.join(8)
        self.assertFalse(th.is_alive())


class NoDiskTest(unittest.TestCase):
    """掟 1: 出力はどこにも保存しない。**書く口がソースに無い**ことを見る（動かして探すより確実）。"""

    def test_module_has_no_file_or_log_writes(self):
        src = (ROOT / "server" / "office_pty.py").read_text(encoding="utf-8")
        code = "\n".join(line.split("#", 1)[0] for line in src.splitlines())
        # docstring の中の説明文（「open()/write_text/logging/print が無い」）は数えない
        code = re.sub(r'"""[\s\S]*?"""', "", code)
        for pat in (r"\bopen\(", r"write_text", r"write_bytes", r"\blogging\b", r"\bprint\(",
                    r"sys\.stderr", r"sys\.stdout", r"tempfile", r"shelve", r"sqlite3", r"pickle"):
            self.assertIsNone(re.search(pat, code), f"office_pty.py に {pat}＝出力がディスクへ落ちうる")


if __name__ == "__main__":
    unittest.main()
