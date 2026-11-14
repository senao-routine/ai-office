# -*- coding: utf-8 -*-
"""R99 Phase 2: コックピットの本物の端末（pty）。**標準ライブラリだけ**（server/ の不変条件）。

このモジュールは「argv を pty の中で起こし、出力を配り、入力を渡す」ことしかしない。
**何を起こすか（kind → argv）と、どこで起こすか（projectId → cwd）は office_server が決める**
（そちらに `_claude_bin` / `_child_env` / `_launch_target_for` が在る。ここは office_server を
import しない＝循環させない・単体で試せる）。

掟（破ると事故になる順）:
  1. **出力はどこにも保存しない**。持つのはメモリのリング（RING_BYTES）だけ。ファイル・
     office_json・/api/events・中継・ログのどれにも載せない。端末には秘密が流れる
     （env を打つ・トークンを貼る）＝ディスクに 1 バイトでも落ちたら取り返せない。
     このファイルに open()/write_text/logging/print が無いことを tests/test_office_pty.py が見る。
  2. **子は制御端末を持つ**。持たないと ^C（0x03）が SIGINT にならず、走り出した claude を
     止められない端末になる。pty.fork() は使わない（スレッドの多い daemon で fork 後に Python を
     走らせると、他スレッドが握っていたロックで子が固まりうる）→ Popen ＋ 3 行の shim。
  3. **閉じるときは中で起こしたものを全部止め・必ず回収**。殺すだけだと子は `Z <defunct>` で残り、
     開け閉めのたびにプロセス表が埋まる（Phase 0-B のスパイクで実測）。
     対話シェルはジョブを**別のプロセスグループ**へ移すので、グループへの kill だけでは
     SIGHUP を無視するジョブ（nohup・trap '' HUP）が生き残る（別モデルレビューが実測）。
     そこで「この端末を制御端末に持つプロセス」を**SIGHUP の前に**控えて、猶予のあと残りを止める
     （リーダーが死ぬと端末との紐づけが切れて、あとからは `ps -t` で拾えない）。
     ＝Terminal.app と違い、**閉じたら中の nohup も止まる**。見えない野良を残さない方を取った。
  4. **遅い視聴者でポンプを止めない**。キューが溢れた視聴者は切る（再接続すればリングから戻る）。
     ポンプが止まると子の出力が詰まり、子そのものが write で固まる。
  5. 同時 MAX_TERMS 本。**作業中の端末を勝手に閉じない**。自動で閉じるのは「コックピットが
     IDLE_CLOSE_SEC（24 時間）一度も開かれていない」ときだけ（一覧の取得を「見ている」に数える）。
     購読の有無で数えると、裏のタブで長い処理を走らせている claude が 30 分で閉じられた（レビュー）。
  7. **続きから読める**。出力には通し番号（バイト位置）を振り、視聴者は「どこまで読んだか」を
     渡して続きだけ受け取る。毎回 reset して頭から流し直すと、TUI が起動時に立てた画面モード
     （代替画面・貼り付けモード・カーソルキーのモード）が 128KB のリングから押し出された後では
     戻せず、タブを往復しただけで claude の画面と矢印キーが壊れる（レビュー）。頭から流し直す
     ときは、**リングの先頭時点の画面モード**を先に復元する。
  8. 名簿（`ps -t`）は **master を握っている間にだけ**引く。自然終了で master を手放した後は
     その端末名が別の端末に再利用されうる＝古いタブを閉じると新しい端末のプロセスを殺す（レビュー high）。
  6. **master の fd を閉じるのはポンプだけ**。close() が閉じると、その番号がすぐ別の端末の
     openpty に再利用され、まだ select していたポンプが**別の端末の出力を読んで配る**
     （＝他人の端末の中身が混ざる）。閉じる役を 1 人にして、書き込み・大きさ変更とは
     同じロック（_fdlock）で排他する。
"""
import atexit
import base64
import errno
import fcntl
import os
import queue
import re
import select
import signal
import struct
import subprocess
import sys
import termios
import threading
import time
import uuid

MAX_TERMS = 4
MAX_VIEWERS = 6                  # 1 本の端末を同時に見られる数（1 視聴 = daemon のスレッド 1 本）。満員なら古い方を追い出す
RING_BYTES = 128 * 1024          # 再接続したときに戻す直近の出力（全画面 TUI の 1 画面ぶんより十分大きく）
IDLE_CLOSE_SEC = 24 * 3600       # コックピットが一度も開かれないまま、これだけ経った端末だけ閉じる（掟 5）
EXITED_KEEP_SEC = 5 * 60         # 終わった端末を一覧に残す時間（終了コードを UI が読めるように）
READ_CHUNK = 65536
INPUT_MAX = 64 * 1024
WRITE_WAIT_SEC = 2.0             # 子が読まずに入力の口が詰まったとき、待つ上限（macOS の pty の口は小さい）
VIEWER_QUEUE = 512               # 視聴者 1 人のキュー。溢れたら切る（掟 4）
COLS_RANGE = (20, 400)
ROWS_RANGE = (5, 200)
JANITOR_SEC = 30

# 子の中で動く 1 行。start_new_session=True で setsid 済みのセッションリーダーが、fd 0（pty の
# slave）を**制御端末**として掴んでから本体へ exec する（pid はそのまま＝本体がリーダーになる）。
_CTTY_SHIM = ("import fcntl,os,sys,termios;"
              "fcntl.ioctl(0,termios.TIOCSCTTY,0);"
              "os.execvp(sys.argv[1],sys.argv[1:])")

_LOCK = threading.Lock()
_TERMS = {}
_janitor = {"started": False}


def clamp_size(cols, rows):
    """窓の大きさを安全な範囲の整数へ。不正な値は既定（100×30）。"""
    def fit(v, lo, hi, dflt):
        try:
            n = int(v)
        except (TypeError, ValueError):
            return dflt
        return min(hi, max(lo, n))
    return fit(cols, *COLS_RANGE, 100), fit(rows, *ROWS_RANGE, 30)


def _set_winsize(fd, cols, rows):
    # TIOCSWINSZ は struct winsize {rows, cols, xpixel, ypixel}
    fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))


# 画面モードの設定・解除（ESC [ ? n ; m h / l）と、端末の全リセット（ESC c＝RIS）を**出てきた順に**読む。
# RIS を見落とすと、リセットで消えたはずの代替画面や貼り付けモードが再接続で復活する（レビュー）。
_MODE_RE = re.compile(rb"\x1bc|\x1b\[\?([0-9;]+)([hl])")
# 復元する画面モード（これ以外は流し直しで戻るか、戻らなくても壊れない）
_KEEP_MODES = frozenset({"1", "7", "12", "25", "47", "1000", "1002", "1003", "1004", "1005", "1006",
                         "1015", "1047", "1049", "2004"})
# 制御列の長さの上限（これより長い「未完の ESC」は制御列ではないとみなす）
_SEQ_MAX = 64


def _scan_modes(data, modes):
    """出力の中の画面モードの設定・解除を modes（dict: 番号 → "h"/"l"）へ積む。RIS で全部消す。"""
    for m in _MODE_RE.finditer(data):
        if m.group(1) is None:                   # ESC c＝全リセット
            modes.clear()
            continue
        for num in m.group(1).decode("ascii").split(";"):
            if num in _KEEP_MODES:
                modes[num] = m.group(2).decode("ascii")


def _safe_cut(buf, cut):
    """切り口が制御列の途中なら、その ESC の手前まで戻す。

    改行の無い TUI 出力ではリングの切り口が `ESC [ ? 2004 l` の途中に来ることがあり、分断された
    列は切り捨てた側でも残った側でも読めない＝解除を見落として、古い「設定」が残り続ける（レビュー）。
    切らなければ分断は起きない。戻した分はリングに残り、次に押し出すとき丸ごと読まれる。"""
    start = max(0, cut - _SEQ_MAX)
    esc = buf.rfind(b"\x1b", start, cut)
    if esc == -1:
        return cut
    tail = bytes(buf[esc:cut])
    if len(tail) == 1:
        return esc                               # ESC だけで切れている
    if tail[1:2] == b"[":                        # CSI: 終端は 0x40–0x7E
        body = tail[2:]
        if not any(0x40 <= b <= 0x7E for b in body):
            return esc                           # 終端がまだ来ていない＝途中
    return cut


def _mode_prefix(modes):
    """modes の「立っている」ものを立て直す列。代替画面（1049/1047/47）は最初に入る。"""
    on = [n for n, v in modes.items() if v == "h"]
    alt = [n for n in on if n in ("1049", "1047", "47")]
    rest = sorted((n for n in on if n not in ("1049", "1047", "47")), key=int)
    return "".join(f"\x1b[?{n}h" for n in alt + rest).encode("ascii")


def _tty_pids(tty):
    """この端末を制御端末に持つプロセスの pid（ジョブ制御で別グループへ移ったジョブも含む）。
    macOS の ps は `-o sess` を持たない（常に 0）ので、端末名で引く。取れなければ空。"""
    if not tty:
        return []
    try:
        out = subprocess.run(["/bin/ps", "-t", os.path.basename(tty), "-o", "pid="],
                             capture_output=True, text=True, timeout=2).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    me = os.getpid()
    return [int(x) for x in out.split() if x.isdigit() and int(x) != me]


class _Viewer:
    """視聴者 1 人＝自分専用のキュー。put が満杯なら queue.Full を投げる（呼び手が切る＝掟 4）。"""
    __slots__ = ("q", "dropped")

    def __init__(self):
        self.q = queue.Queue(maxsize=VIEWER_QUEUE)
        self.dropped = False

    def put(self, item):
        self.q.put_nowait(item)


class _MuxViewer(_Viewer):
    """R111-B0: 多重化 stream（1 接続で複数の端末）の視聴者。キューは**接続で 1 本を共有**し、
    どの端末の出力かを `(tid, item)` で積む（端末ごとの順序はキューの FIFO がそのまま守る）。
    数え方は単一の視聴者と同じ＝端末ごとに 1 人（MAX_VIEWERS は端末ごと）。

    ★終わりの印（exit）を積めなかったら dropped を立てる。共有キューは別の端末の出力でも
    満杯になるので、落とした exit を待ち続けると接続が永久に閉じない。切って繋ぎ直せば、
    終わった端末の subscribe は exit を即座に積む。"""
    __slots__ = ("tid",)

    def __init__(self, shared, tid):
        self.q = shared
        self.tid = tid
        self.dropped = False

    def put(self, item):
        try:
            self.q.put_nowait((self.tid, item))
        except queue.Full:
            if isinstance(item, tuple):
                self.dropped = True
            raise


def mux_viewer(shared, tid):
    """多重化 stream の視聴者を作る（office_server が端末ごとに 1 つ作って subscribe へ渡す）。"""
    return _MuxViewer(shared, tid)


class Term:
    """1 本の pty。master を読み続け、リングへ積み、視聴者へ配る。"""

    def __init__(self, argv, cwd=None, env=None, cols=100, rows=30, kind="", session=None, project_id=None):
        if not argv or not all(isinstance(a, str) and a for a in argv):
            raise ValueError("argv が不正")
        cols, rows = clamp_size(cols, rows)
        master, slave = os.openpty()
        try:
            self.tty = os.ttyname(slave)             # /dev/ttysNNN（閉じるときに中のプロセスを引く鍵）
        except OSError:
            self.tty = None
        try:
            _set_winsize(slave, cols, rows)          # 子が最初に読む大きさを先に決めておく
            self.proc = subprocess.Popen(
                [sys.executable, "-I", "-c", _CTTY_SHIM, *argv],
                stdin=slave, stdout=slave, stderr=slave, cwd=cwd, env=env,
                start_new_session=True, close_fds=True)
        except Exception:
            os.close(master)
            os.close(slave)
            raise
        os.close(slave)                              # 親は slave を持たない（持つと子が終わっても EOF が来ない）
        os.set_blocking(master, False)
        self.fd = master
        self.id = uuid.uuid4().hex[:16]
        self.kind = kind
        self.session = session
        self.project_id = project_id
        self.cols, self.rows = cols, rows
        self.started_at = time.time()
        self.ended_at = None
        self.exit_code = None
        self.alive = True
        self.last_viewed = time.time()
        self._ring = bytearray()
        self._base = 0                        # リング先頭のバイトの通し番号（掟 7）
        self._base_modes = {}                 # リング先頭時点の画面モード（押し出した分から積む）
        self._viewers = []
        self._lock = threading.Lock()
        self._fdlock = threading.Lock()      # fd を閉じる（ポンプ）と使う（write/resize）の排他（掟 6）
        self._fd_open = True
        self._closed = False
        threading.Thread(target=self._pump, daemon=True, name=f"pty-{self.id}").start()

    # ── 出力 ────────────────────────────────────────────────────────
    def _pump(self):
        # close() が立てた旗は 0.5 秒以内に拾う。孫が別セッションへ逃げて slave を掴み続けても、
        # EIO を待たずにここで終われる（待つ形にするとポンプが永久に残る）。
        while not self._closed:
            try:
                ready, _, _ = select.select([self.fd], [], [], 0.5)
            except (OSError, ValueError):
                break
            if not ready:
                if self.proc.poll() is not None:
                    self._drain()
                    break
                continue
            try:
                data = os.read(self.fd, READ_CHUNK)
            except BlockingIOError:
                continue
            except OSError as exc:
                if exc.errno in (errno.EIO, errno.EBADF):   # 子側が全部閉じた（macOS は EIO）
                    break
                continue
            if not data:
                break
            self._publish(data)
        with self._fdlock:                           # 掟 6: 閉じるのはここだけ
            self._fd_open = False
            try:
                os.close(self.fd)
            except OSError:
                pass
        self._finish()

    def _drain(self):
        """子が終わった直後に残っている出力を、有限回だけ読み切る（EOF を待たない）。
        孫が slave を掴んだまま生きていると EOF は永久に来ない＝ここで固まる（R79-10 で踏んだ罠）。"""
        for _ in range(16):
            try:
                data = os.read(self.fd, READ_CHUNK)
            except (BlockingIOError, OSError):
                return
            if not data:
                return
            self._publish(data)

    def _publish(self, data):
        with self._lock:
            self._ring += data
            over = len(self._ring) - RING_BYTES
            if over > 0:
                # 切り口を次の改行まで進める（行の途中・エスケープの途中から再生しない）
                nl = self._ring.find(b"\n", over, over + 4096)
                cut = (nl + 1) if nl != -1 else _safe_cut(self._ring, over)
                _scan_modes(bytes(self._ring[:cut]), self._base_modes)   # 押し出す前にモードを積む
                del self._ring[:cut]
                self._base += cut
            for v in list(self._viewers):
                try:
                    v.put(data)
                except queue.Full:
                    # 掟 4: 遅い視聴者でポンプを止めない。切って、再接続でリングから戻ってもらう
                    v.dropped = True
                    self._viewers.remove(v)
                    try:
                        v.put(None)
                    except queue.Full:
                        pass

    def _finish(self):
        # 出力の口が死んだ（EIO）のに子がまだ生きている形がある（子が fd を全部閉じて走り続ける）。
        # ここで回収しないと誰も wait しない＝ゾンビ／野良が残る。端末としては終わったので止めて回収する。
        code = self.proc.poll()
        if code is None:
            try:
                code = self.proc.wait(timeout=0.5)
            except subprocess.TimeoutExpired:
                self._kill_group()
                code = self.proc.returncode
        with self._lock:
            self.alive = False
            self.exit_code = code
            self.ended_at = time.time()
            for v in self._viewers:
                try:
                    v.put(("exit", code))
                except queue.Full:
                    pass

    # ── 視聴 ────────────────────────────────────────────────────────
    def subscribe(self, from_offset=None, viewer=None):
        """(backlog, start, reset, prefix, viewer) を返す。

        from_offset（視聴者が読み終えた通し番号）がリングの中なら**続きだけ**返す（reset=False）。
        範囲外・未指定なら頭から（reset=True）＋ prefix＝リング先頭時点の画面モードの立て直し（掟 7）。
        満員なら**いちばん古い視聴者を追い出す**（素早いタブ切替で残った古い購読が枠を埋め、
        見ている画面の更新が止まった＝レビュー）。backlog と以降の配信の間に取りこぼしは無い。
        viewer＝作り置きの視聴者（多重化 stream の mux_viewer）。省略時はここで専用の 1 人を作る。"""
        with self._lock:
            while len(self._viewers) >= MAX_VIEWERS:
                old = self._viewers.pop(0)
                old.dropped = True
                try:
                    old.put(None)
                except queue.Full:
                    pass
            v = viewer if viewer is not None else _Viewer()
            end = self._base + len(self._ring)
            if isinstance(from_offset, int) and self._base <= from_offset <= end:
                backlog, start, reset, prefix = bytes(self._ring[from_offset - self._base:]), from_offset, False, b""
            else:
                backlog, start, reset = bytes(self._ring), self._base, True
                prefix = _mode_prefix(self._base_modes)
            if not self.alive:
                try:
                    v.put(("exit", self.exit_code))
                except queue.Full:
                    pass                             # 共有キューが満杯（mux は dropped が立つ＝繋ぎ直し）
            else:
                self._viewers.append(v)
            self.last_viewed = time.time()
            return backlog, start, reset, prefix, v

    def unsubscribe(self, viewer):
        with self._lock:
            if viewer in self._viewers:
                self._viewers.remove(viewer)
            self.last_viewed = time.time()

    def viewers(self):
        with self._lock:
            return len(self._viewers)

    def backlog(self):
        with self._lock:
            return bytes(self._ring)

    # ── 入力・大きさ ────────────────────────────────────────────────
    def write(self, data):
        """入力を子へ渡す。渡し切れたら True。子が読まずに詰まったら WRITE_WAIT_SEC で諦めて False。"""
        if not self.alive or not data:
            return False
        view = memoryview(bytes(data[:INPUT_MAX]))
        deadline = time.monotonic() + WRITE_WAIT_SEC
        with self._fdlock:
            if not self._fd_open:
                return False
            while view:
                try:
                    n = os.write(self.fd, view)
                    view = view[n:]
                    continue
                except BlockingIOError:
                    pass
                except OSError:
                    return False
                left = deadline - time.monotonic()
                if left <= 0:
                    return False
                try:
                    select.select([], [self.fd], [], min(left, 0.2))
                except (OSError, ValueError):
                    return False
        return True

    def resize(self, cols, rows):
        if not self.alive:
            return False
        cols, rows = clamp_size(cols, rows)
        with self._fdlock:
            if not self._fd_open:
                return False
            try:
                _set_winsize(self.fd, cols, rows)    # 子の前面グループへ SIGWINCH が飛ぶ
            except OSError:
                return False
        self.cols, self.rows = cols, rows
        return True

    # ── 終わらせる ──────────────────────────────────────────────────
    def _attached_now(self):
        """いまこの端末に繋がっている pid。**master を握っている間だけ**引く（掟 8）。
        手放した後の端末名は別の端末に再利用されうる＝引くと他人のプロセスを拾う。"""
        with self._fdlock:
            return _tty_pids(self.tty) if self._fd_open else []

    def _kill_group(self, attached=None):
        """中で起こしたものを全部止めて回収する（掟 3）。
        ①端末に繋がっている pid を控える ②グループへ SIGHUP（シェルはジョブへ伝える）→ 1 秒待つ
        ③グループへ SIGKILL ④控えた pid のうち生き残り（HUP を無視したジョブ）へ SIGKILL ⑤回収。"""
        if attached is None:
            attached = self._attached_now()
        pgid = None
        try:
            pgid = os.getpgid(self.proc.pid)
        except OSError:
            pass
        if pgid is not None:
            try:
                os.killpg(pgid, signal.SIGHUP)       # 端末が閉じたときと同じ合図
            except OSError:
                pass
        try:
            self.proc.wait(timeout=1.0)
        except subprocess.TimeoutExpired:
            pass
        if pgid is not None:
            try:
                os.killpg(pgid, signal.SIGKILL)
            except OSError:
                pass
        for pid in attached:
            try:
                os.kill(pid, signal.SIGKILL)
            except OSError:
                pass                                 # もう居ない（ESRCH）＝素直に終わった
        try:
            self.proc.wait(timeout=2.0)
        except subprocess.TimeoutExpired:
            pass

    def close(self):
        """プロセスグループごと止めて、**必ず回収する**（掟 3）。何度呼んでも安全。
        fd はここで閉じない（掟 6）。_closed を見たポンプが 0.5 秒以内に自分で閉じる。"""
        if self._closed:
            return
        attached = self._attached_now()          # 旗を立てる前に控える（ポンプが master を閉じる前に）
        self._closed = True
        self._kill_group(attached)
        with self._lock:
            self.alive = False
            if self.exit_code is None:
                self.exit_code = self.proc.returncode
            if self.ended_at is None:
                self.ended_at = time.time()
            for v in self._viewers:
                try:
                    v.put(("exit", self.exit_code))
                except queue.Full:
                    pass
            self._viewers.clear()

    def public(self):
        """UI へ渡す形。**出力・argv・cwd・env は載せない**（出力は stream でだけ流す）。"""
        return {"id": self.id, "kind": self.kind, "session": self.session,
                "projectId": self.project_id, "cols": self.cols, "rows": self.rows,
                "alive": self.alive, "exitCode": self.exit_code,
                "startedAt": int(self.started_at), "viewers": self.viewers()}


# ── 一覧 ────────────────────────────────────────────────────────────
def open_term(argv, cwd=None, env=None, cols=100, rows=30, kind="", session=None, project_id=None):
    """(Term, "") か (None, 理由) を返す。理由= "too_many" / "spawn_failed"。"""
    _start_janitor()
    with _LOCK:
        _sweep_locked(time.time())
        if sum(1 for t in _TERMS.values() if t.alive) >= MAX_TERMS:
            return None, "too_many"
        try:
            t = Term(argv, cwd=cwd, env=env, cols=cols, rows=rows,
                     kind=kind, session=session, project_id=project_id)
        except (OSError, ValueError, subprocess.SubprocessError):
            return None, "spawn_failed"
        _TERMS[t.id] = t
        return t, ""


def get(term_id):
    with _LOCK:
        return _TERMS.get(term_id) if isinstance(term_id, str) else None


def close(term_id):
    """本人が閉じた端末は**一覧からすぐ外す**。自分で終わった端末（exit を打った等）だけが
    EXITED_KEEP_SEC のあいだ残り、終了コードを見せる（閉じたのに死んだタブが 5 分居座らない）。"""
    with _LOCK:
        t = _TERMS.pop(term_id, None) if isinstance(term_id, str) else None
    if t is None:
        return False
    t.close()
    return True


def list_public(touch=False):
    """一覧（出力は含まない）。touch=True＝コックピットが開いている証拠として last_viewed を進める
    （見えていない端末を購読しない設計なので、購読の有無では「開いている」を判定できない）。"""
    now = time.time()
    with _LOCK:
        _sweep_locked(now)
        terms = list(_TERMS.values())
        if touch:
            for t in terms:
                t.last_viewed = now
    return [t.public() for t in sorted(terms, key=lambda x: x.started_at)]


def close_all():
    with _LOCK:
        terms = list(_TERMS.values())
        _TERMS.clear()
    for t in terms:
        t.close()


def _sweep_locked(now):
    """終わって久しい端末を一覧から外し、誰も見ていない端末を閉じる（_LOCK の中で呼ぶ）。"""
    for tid, t in list(_TERMS.items()):
        if not t.alive and t.ended_at and now - t.ended_at > EXITED_KEEP_SEC:
            _TERMS.pop(tid, None)
        elif t.alive and t.viewers() == 0 and now - t.last_viewed > IDLE_CLOSE_SEC:
            threading.Thread(target=t.close, daemon=True).start()   # 回収待ちで _LOCK を握らない


def _janitor_loop():
    while True:
        time.sleep(JANITOR_SEC)
        with _LOCK:
            _sweep_locked(time.time())


def _start_janitor():
    with _LOCK:
        if _janitor["started"]:
            return
        _janitor["started"] = True
    threading.Thread(target=_janitor_loop, daemon=True, name="pty-janitor").start()


def b64(data):
    return base64.b64encode(data).decode("ascii")


def unb64(text):
    """クライアントからの入力（base64）→ bytes。不正・大きすぎは None。"""
    if not isinstance(text, str) or len(text) > INPUT_MAX * 4 // 3 + 8:
        return None
    try:
        return base64.b64decode(text.encode("ascii"), validate=True)
    except (ValueError, UnicodeEncodeError):
        return None


atexit.register(close_all)
