# -*- coding: utf-8 -*-
"""R128 #3: 出勤の口の Bot の記録（~/.claude/office_door/bot/<id>.log.jsonl）を、Mac の作業ビューとスマホの端末画面の行へ。

呼ばれるのは office_term.session_page の bot- の分岐だけ（Mac の GET /api/session/activity・スマホの封書 kind:"term"）。
＝Mac の中央とスマホが**同じ行**を見る。契約の正本= docs/door.md の「記録の画面」。記録を書くのは office_door だけ。

★掟
  - 標準ライブラリだけ・server/ は flat（tools/check_stdlib.py）・Python 3.9 の書き方。office_server を import しない。
  - **読むだけ**（ここから書く経路を作らない・ロックも取らない＝書き手は 1 行を 1 回の追記で書き、作り直しは原子的な置き換え）。
  - 読む場所の閉じ込め: session "bot-<id>" の <id> を office_door.valid_id で確かめ（bot_of_session）・**登録した Bot だけ**
    （office_door.is_registered）・口のフォルダ（office_door/ と bot/）がリンクなら読まない・記録そのものはリンクを辿らずに開く
    （O_NOFOLLOW）・通常のファイルだけ。どれかが外れたら FileNotFoundError（呼び出し側の notfound・Mac は空の頁）。
  - 記録の中身は Bot が書いた文（信用しない入力）と人の答え・指示。行にするときは**全部の文**に office_term._fit
    （ホームの下のパスを畳む → scrub＝office_actions.scrub_output の秘密の伏字・パス→basename → 上限で切る）を通す。
    HTML として無害化はしない（描く側が必ず textContent・ui/hud/activity.js）。1 行・1 枚の上限は office_term と同じ
    （ROWS_MAX・TEXT_MAX など。封書は呼び出し側が office_term.fits／shrink で縮める）。
  - 行の形（office_term の行の種類に載せる。新しい種類は ev だけ＝古い描き手は知らない種類として黙って飛ばす）:
      report   → {"k":"a",  "t": 題＋空行＋本文, "rk": "done"|"info"|"warn"}          Bot の報告（作業ビューの本文）
      ask      → {"k":"q",  "t": 問い, "o": [選択肢]}                                     Bot の相談（質問のカード）
      answer   → {"k":"u",  "t": 答え, "as": "answer", "ch": 選んだ番号（0 始まり・選択肢のときだけ）}  人の答え（吹き出し＋札）
      instruct → {"k":"u",  "t": 指示, "as": "instruct"}                                 人の指示（吹き出し＋札）
      checkin  → {"k":"ev", "e": "working"|"waiting"|"resting", "t": doing}               出勤の内容が変わった
      expired  → {"k":"ev", "e": "expired"}                                               相談の期限が切れた（答えなし）
      withdraw → {"k":"ev", "e": "withdrawn"}                                             Bot が相談を取り下げた
    各行に ts（epoch 秒・整数・読めなければ付けない）。知らない ev・形の合わない行・壊れた行は黙って飛ばす（落ちない）。
    退勤は記録に書かれない（出勤の期限と表示の窓＝時刻で決まる）＝いまの状態は見出しの札が言う。
  - カーソル（{off, h}・遡りの {off, h, i}）・頁の形（rows/cur/reset/gap・_offs/_top/_base）・shrink・public は office_term と同じ
    （source_codex_rollout と同じ型）。記録は上限（office_door.LOG_MAX）を超えると古い半分を捨てて作り直される（別の inode）＝
    合わない差分のカーソルは reset（末尾から読み直し）・合わない遡りのカーソルは None（遡りを止める）。
"""
import json
import math
import os
import stat
import sys
from pathlib import Path

try:
    import office_door
    import office_term
except ModuleNotFoundError:   # importlib でファイルを直に読む読み込み方（server/ が sys.path に無い）
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    try:
        import office_door
        import office_term
    finally:
        del sys.path[0]

# 上限（字・行）。正本は office_term の定数（Claude・Codex の行と同じ大きさで切る）
REPORT_MAX, REPORT_LINES = office_term.TEXT_MAX, office_term.TEXT_LINES      # 報告（題 80＋本文 600 字は切らずに入る）
QUESTION_MAX, QUESTION_LINES = office_term.TEXT_MAX, 6                      # 相談（AskUserQuestion の q と同じ）
OPTION_MAX = 60                                                             # 選択肢 1 本（同上）
HUMAN_MAX, HUMAN_LINES = office_term.USER_MAX, office_term.USER_LINES        # 人の答え・指示（口は 4000 字まで受ける）
DOING_MAX = 200                                                             # 出勤の doing（口は 60 字まで）
EVENT_OF_STATE = {"working": "working", "waiting": "waiting", "resting": "resting"}
_TS_MAX = 2 ** 53


# ── 置き場（閉じ込め）──────────────────────────────────────────
def log_file(session, home=None):
    """session "bot-<id>" → その Bot の記録のパス。形の違う id・登録していない Bot・口のフォルダがリンク＝FileNotFoundError。"""
    bot_id = office_door.bot_of_session(session)
    if bot_id is None:
        raise FileNotFoundError("door log: bad bot id")
    if not office_door.is_registered(bot_id, home=home):
        raise FileNotFoundError("door log: not a registered bot")
    base = office_door.door_dir(home)
    for d in (base, base / "bot"):
        try:
            st = os.lstat(d)
        except OSError:
            raise FileNotFoundError("door log: no folder")
        if not stat.S_ISDIR(st.st_mode):                  # リンク（S_ISLNK）・ファイル＝口のフォルダの外を指しうる
            raise FileNotFoundError("door log: folder is not a plain directory")
    return office_door.log_path(bot_id, home)


def _open(path):
    """記録を読むために開く（リンクを辿らない・通常のファイルだけ）。外れたら FileNotFoundError。"""
    try:
        # O_NONBLOCK: 記録の場所が FIFO に替わっていても、open が書き手を待って止まらない（開いてから種類を見て断る）。通常ファイルの読み取りには効かない
        fd = os.open(str(path), os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
    except OSError:
        raise FileNotFoundError("door log: cannot open")
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise FileNotFoundError("door log: not a regular file")
        return os.fdopen(fd, "rb")
    except BaseException:
        os.close(fd)
        raise


def term_page(session, after, scrub, before=None, home=None):
    """office_term.session_page の bot- の分岐の先。頁の形は office_term.read_page / read_before と同じ。
    読めない（形の違う id・登録なし・記録が無い・閉じ込めの外）＝FileNotFoundError（OSError＝呼び出し側の notfound）。
    遡りのカーソルが合わない（記録が作り直された）＝None。"""
    path = log_file(session, home)
    if before is not None and after is None:
        return read_before(path, before, scrub)
    return read_page(path, after, scrub)


# ── 行への変換（純関数）──────────────────────────────────────────
def _ts(v):
    """記録の ts（epoch 秒・小数）→ 整数の秒（office_term の行と同じ）。数でない・有限でない・範囲の外は None。"""
    if type(v) is int:                                # 範囲を先に見る（isfinite は float へ変換する＝10**400 は OverflowError で頁ごと落ちた）
        return v if 0 < v < _TS_MAX else None
    if type(v) is float and math.isfinite(v) and 0 < v < _TS_MAX:
        return int(v)
    return None


def _text(v):
    return v if isinstance(v, str) and v.strip() else None


def _row(d, scrub):
    """記録の 1 行（dict）→ 端末の行（ts は呼び出し側）か None。形の合わない行は None（飛ばす）。"""
    fit = office_term._fit
    ev = d.get("ev")
    if ev == "checkin":
        doing, state = _text(d.get("doing")), d.get("state")
        if doing is None or not isinstance(state, str) or state not in EVENT_OF_STATE:   # 文字列でない値で dict を引かない（[1] は TypeError）
            return None
        t = fit(scrub, doing, DOING_MAX, 1)
        return {"k": "ev", "e": EVENT_OF_STATE[state], "t": t} if t else None
    if ev == "report":
        title, body = _text(d.get("title")), d.get("body") or ""
        if title is None or not isinstance(body, str):
            return None
        text = title.strip() + ("\n\n" + body.strip() if body.strip() else "")
        t = fit(scrub, text, REPORT_MAX, REPORT_LINES)
        if not t:
            return None
        return {"k": "a", "t": t, "rk": d.get("kind") if d.get("kind") in office_door.KINDS else "done"}
    if ev == "ask":
        question, options = _text(d.get("question")), d.get("options", [])
        if question is None or not isinstance(options, list):
            return None
        t = fit(scrub, question, QUESTION_MAX, QUESTION_LINES)
        if not t:
            return None
        row = {"k": "q", "t": t}
        labels = [fit(scrub, o, OPTION_MAX, 1) for o in options[:office_door.ASK_OPT_MAX] if _text(o) is not None]
        labels = [x for x in labels if x]
        if labels:
            row["o"] = labels
        return row
    if ev in ("answer", "instruct"):
        text = _text(d.get("text"))
        if text is None:
            return None
        t = fit(scrub, text, HUMAN_MAX, HUMAN_LINES)
        if not t:
            return None
        row = {"k": "u", "t": t, "as": ev}
        choice = d.get("choice")
        if ev == "answer" and isinstance(choice, dict) and type(choice.get("index")) is int \
                and 0 <= choice["index"] < office_door.ASK_OPT_MAX:
            row["ch"] = choice["index"]
        return row
    if ev == "expired":
        return {"k": "ev", "e": "expired"}
    if ev == "withdraw":
        return {"k": "ev", "e": "withdrawn"}
    return None


def rows_from_door_lines(lines, scrub, line_index=False):
    """記録の行（str か bytes）→ 端末の行 [{"k": 種類, …}]（時系列）。壊れた行・知らない行は黙って飛ばす。
    line_index＝元の行の番号を _l に（遡りのカーソル用・封じる前に外す＝office_term._with_offsets と同じ）。"""
    rows = []
    for li, ln in enumerate(lines):
        try:
            d = json.loads(ln)
        except (ValueError, TypeError, RecursionError):
            continue
        if not isinstance(d, dict):
            continue
        r = _row(d, scrub)
        if r is None:
            continue
        ts = _ts(d.get("ts"))
        if ts is not None:
            r["ts"] = ts
        if line_index:
            r["_l"] = li
        rows.append(r)
    return rows


# ── 頁（カーソルの約束は office_term.read_page / read_before と同じ）────────────────
def read_page(path, after, scrub):
    """{"rows", "cur", "reset", "gap", …}。path は log_file で閉じ込め済み。形と検算は office_term.read_page と同じ。"""
    after = office_term.valid_after(after)
    with _open(path) as f:
        size = os.fstat(f.fileno()).st_size
        if after and after["off"] <= size and size - after["off"] <= office_term.DELTA_MAX \
                and office_term._hash_before(f, after["off"]) == after["h"]:
            f.seek(after["off"])
            data = f.read(size - after["off"])
            cut = data.rfind(b"\n")
            if cut < 0:                                   # 新しい完全な行が無い（書きかけだけ）
                return {"rows": [], "cur": after, "reset": False, "gap": False}
            lines = data[:cut].split(b"\n")
            rows = rows_from_door_lines(lines, scrub)
            cur = {"off": after["off"] + cut + 1, "h": office_term.line_hash(lines[-1])}
            return {"rows": rows[-office_term.ROWS_MAX:], "cur": cur, "reset": False,
                    "gap": len(rows) > office_term.ROWS_MAX}
        start = max(0, size - office_term.TAIL_BYTES)
        f.seek(start)
        data = f.read(size - start)
    cut = data.rfind(b"\n")
    if cut < 0:
        return {"rows": [], "cur": None, "reset": True, "gap": start > 0}
    lines = data[:cut].split(b"\n")
    base = start
    if start > 0:
        base += len(lines[0]) + 1
        lines = lines[1:]                                 # 先頭は行の途中
    page = _with_offsets(lines, base, scrub, office_term.ROWS_MAX, at_top=(start == 0))
    page.update({"cur": {"off": start + cut + 1, "h": office_term.line_hash(data[:cut].rsplit(b"\n", 1)[-1])},
                 "reset": True})
    page["gap"] = page["gap"] or start > 0
    return page


def _with_offsets(lines, base, scrub, keep, at_top, cut_last=None):
    """office_term._with_offsets と同じ（行の作り方だけ rows_from_door_lines）。記録の 1 行は表示の行 1 つ以下。"""
    starts, o = [], base
    for ln in lines:
        starts.append(o)
        o += len(ln) + 1
    rows = rows_from_door_lines(lines, scrub, line_index=True)
    nth, seen = [], {}
    for r in rows:
        li = r.get("_l")
        nth.append(seen.get(li, 0))
        seen[li] = seen.get(li, 0) + 1
    if cut_last is not None and lines:
        last = len(lines) - 1
        pairs = [(r, n) for r, n in zip(rows, nth) if not (r.get("_l") == last and n >= cut_last)]
        rows, nth = [r for r, _ in pairs], [n for _, n in pairs]
    kept, kept_nth = rows[-keep:], nth[-keep:]
    offs = []
    for r, n in zip(kept, kept_nth):
        li = r.pop("_l", None)
        offs.append({"off": starts[li], "h": office_term.line_hash(lines[li]), "i": n} if isinstance(li, int) else None)
    for r in rows[:-keep] if len(rows) > keep else []:
        r.pop("_l", None)
    page = {"rows": kept, "_offs": offs, "_top": at_top and len(rows) <= keep, "gap": len(rows) > keep}
    if lines:
        page["_base"] = {"off": base, "h": office_term.line_hash(lines[0]), "i": 0}
    return page


def read_before(path, before, scrub):
    """画面のいちばん上の行（before）より前の BACK_ROWS 行。形と検算は office_term.read_before と同じ（合わない＝None）。"""
    before = office_term.valid_before(before)
    if not before:
        return None
    with _open(path) as f:
        size = os.fstat(f.fileno()).st_size
        cursor_line = office_term._line_at(f, before["off"]) if before["off"] <= size else None
        if cursor_line is None or office_term.line_hash(cursor_line) != before["h"]:
            return None
        span = office_term.BACK_BYTES
        while True:
            start = max(0, before["off"] - span)
            f.seek(start)
            data = f.read(before["off"] - start)
            lines = data[:-1].split(b"\n") if data.endswith(b"\n") else (data.split(b"\n") if data else [])
            base = start
            if start > 0 and lines:
                base += len(lines[0]) + 1
                lines = lines[1:]
            if lines or start == 0 or span >= office_term.LINE_MAX:
                break
            span *= 4
    if before.get("i"):
        lines = lines + [cursor_line]
        page = _with_offsets(lines, base, scrub, office_term.BACK_ROWS, at_top=(start == 0), cut_last=before["i"])
    else:
        page = _with_offsets(lines, base, scrub, office_term.BACK_ROWS, at_top=(start == 0))
    if not lines and start > 0:
        page["_top"] = False
    page.update({"older": True, "reset": False, "cur": None})
    return page
