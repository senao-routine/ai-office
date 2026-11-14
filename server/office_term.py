# -*- coding: utf-8 -*-
"""R100-1a: スマホの「ターミナル画面」に載せる行（transcript → 端末の行）と差分カーソル。

封書（R87・dialog_seal）のレーンに `kind:"term"` として載る。ここは**純関数と読み取りだけ**
（封じる・保存する・監査するのは office_server._dialog_sealed）。

★掟
  - 標準ライブラリだけ・server/ は flat（tools/check_stdlib.py）。
  - 行は**種類つきの短い文字列**だけ: 人の指示 u（800 字）／AI の文 a（1600 字）／道具 tool（名前と引数・印 id）／
    結果 res（要約 t を必ず持つ・印 for・総行数 nl・種類ごとの数 rl/add/rem/nf/…・差分 d・サブエージェントの末尾 kids／省いた数 kx）／
    質問 q（選択肢 o）／スラッシュコマンド cmd／Todo todo／中断 int／1 ターンの所要 turn／会話の圧縮 sys／考えの印 th（数 n・字数 chars）。
    各行に時刻 ts。R102-3（本人「Claude Code のセッションみたいに中身が見えるように」）。
    R113-B（本人 2026-10-08「もっとそのまま」）: Bash の入力 4000 字/40 行・出力 8000 字/120 行（失敗は先頭 40%＋中略＋末尾 60%）・
    差分 1 件 100 行×200 字・パスはプロジェクト（行の cwd）からの相対・URL は scheme://host/path・サブエージェントは末尾 20 行（kids）。
    Read の本文・画像・元のファイル・考えの本文は載せない（考えは数と字数の印 th だけ）。上限の表は下の定数が正本。
  - 道具の引数と結果は **scrub（秘密の伏字・絶対パス→basename）を通してから**載せる。封書は中継対策、伏字は端末の盗難対策。
    Bash は command、Edit/Write/Read は cwd からの相対パス（cwd が無い・プロジェクトの外は basename）、Skill はスキル名、
    検索は検索語と探した場所（R113-B（2026-10-08 本人裁定）: 検索語は scrub を通して載せる・定数 SHOW_SEARCH_TERMS で切替可）、
    URL は scheme://host/path（query・fragment・userinfo は構造的に落とす）、Task は説明。差分の本文・サブエージェントの行も伏字を通す。
    R113-B-S2（別モデルレビュー）: **伏字は切り詰めの前に素材全体へ**掛け、その後で切る（先に切ると `Bearer ` などの接頭辞が切れて
    伏字の文脈が失われ、トークンの断片だけが残る）。切るときは英数の語（秘密の型が使う字）の途中で切らず、語が長すぎて区切りが
    見つからなければ断片を返さずに固定の省略の印 _OMIT にする。サブエージェント（kids）の発話（u・a）も伏字→切る。
  - R121（本人 2026-11-01「アプリ版 Claude Code を見習って」「コードがつらつら出てきたら分からない」）: 道具の行に説明 ds
    （input.description＝Bash の「何をするか」など・伏字→DESC_MAX 字・引数と同じなら付けない）と裏で実行 bg（run_in_background）。
    作業ビュー（ui/core/activity.js）は ds を見出しにして命令の本文を畳む。Mac は GET /api/session/activity でこの行を読む。
    人の指示の貼り付けの囲み（<pasted_content …>…</pasted_content …>）は外して本文を残す（以前は `<` 始まりとして指示ごと消えた）。
  - 差分カーソル `{off, h}`: off＝最後に渡した行の直後のバイト位置、h＝その行の SHA-256 先頭 16 桁。
    **off ≤ 現在サイズ・off の直前が改行・その行の h が一致**のときだけ差分。どれか外れたら末尾から読み直し（reset）。
    中継（Worker）が古いカーソルを返しても、別のファイルに化けても、巻き戻しや取り違えにならない。
  - 封書 1 通の本文は ENVELOPE_BUDGET（dialog_seal の 4 フレーム）まで。入らなければ shrink の段の順に縮める。
"""
import datetime
import hashlib
import importlib
import json
import os
import re
import unicodedata
import zlib
from urllib.parse import urlsplit

import dialog_seal

# ── 上限（最終の字数・行数）。伏字は素材全体に掛けてから、この上限で切る（_fit・R113-B-S2） ──────────────
# ★素材の先切りを直書き（[:2000] など）にしない: 上限を上げても先切りで頭打ちになり、切れ目で伏字の文脈も切れる。
ROWS_MAX = 150                 # 1 回に返す行の上限（差分でも reset でも）
TEXT_MAX = 1600                # AI の 1 行（Claude Code は Markdown の段落を長く書く）
TEXT_LINES = 40
USER_MAX = 800                 # 人の指示
USER_LINES = 20
ARG_MAX = 200                  # 道具の引数（Bash 以外）。縮めるときの Bash の入力（従来相当）もこれ
ARG_LINES = 4
BASH_CMD_MAX = 4000            # R113-B: Bash の入力（command）
BASH_CMD_LINES = 40
RESULT_MAX = 600               # 結果（Bash 以外）。縮めるときの Bash の出力（従来相当）もこれ
RESULT_LINES = 8
BASH_OUT_MAX = 8000            # R113-B: Bash の出力（成功は先頭から・失敗は先頭 40%＋末尾 60%）
BASH_OUT_LINES = 120
DIFF_LINES = 100               # R113-B: 差分の行（1 件）
DIFF_COL = 200                 # R113-B: 差分の 1 行の字数
DIFF_LINES_OLD = 16            # 縮めるときの差分（従来相当）
DIFF_COL_OLD = 120
WRITE_HEAD = DIFF_LINES * 6 // 16      # 新規作成（Write）の先頭の行（従来 16 行に対する 6 行と同じ比率）
WRITE_HEAD_OLD = 6
PATH_MAX = 200                 # 相対パス
DESC_MAX = 120                 # R121: 道具の説明（ds）
TODO_MAX = 12                  # Todo の件数
PLAN_MAX = 3000                # R104-2: 計画（ExitPlanMode の plan）の本文＝封書の中だけ（中継の平文には載らない）
PLAN_LINES = 60
TASK_KIDS_MAX = 20             # R113-B: サブエージェントの結果に添える子の行（末尾から）
KIDS_PAGE_MAX = 8              # 1 枚で kids を添える Task の数（新しい方から）＝子を読む回数の上限
KIDS_TAIL_BYTES = 1_000_000    # 子 transcript を読む末尾（末尾 20 行には足りる・都度読み直す＝カーソルに乗せない）
SHOW_SEARCH_TERMS = True       # R113-B（2026-10-08 本人裁定）: Grep/Glob の検索語を伏字を通して載せる（False＝場所だけ）
MID_HEAD = (2, 5)              # 失敗の出力は先頭 2/5（40%）＋中略＋末尾（残り）
ENVELOPE_BUDGET = dialog_seal.MAX_FRAMES * dialog_seal.BLOCK - 4   # 封書 1 応答の本文（4 バイトの長さを除く）＝32764


KINDS = ("u", "a", "tool", "res", "q", "cmd", "th", "int", "turn", "sys", "todo", "plan")
# 入力が「命令の本文」の道具（Bash の入力の上限 BASH_CMD_MAX・縮めるときは ARG_MAX へ）。R113-B-I: Codex の Shell（命令の実行）と
# exec（JS のコード）も Claude の Bash と同じ上限・同じ縮め方（source_codex_rollout が cmd_text／out_text を使う）
CMD_TOOLS = ("Bash", "Shell", "exec")
_PATH_TOOLS = ("Edit", "Write", "Read", "NotebookEdit", "MultiEdit")
_INTERRUPT = ("[Request interrupted by user", "[Request interrupted")
_AGENT_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")         # 子 transcript の名前に使う＝パスの部品にならない形だけ
_MARK_RE = re.compile(r"^…（中略(?: (\d+) 行)?）…$")
_PASTE_TAG_RE = re.compile(r"</?pasted_content\b[^>]{0,200}>")   # R121: 人の指示の貼り付けの囲み
_EDGE = 1024                   # 切れ目で捨てる語の長さの上限（これより長い語＝区切りが見つからない＝断片を返さず _OMIT にする）
_OMIT = "…（長い語を省略）…"     # R113-B-S2: 区切りの見つからない長い語の切れ端の代わり（断片は返さない）
_WORD = frozenset("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789._~+/=-")   # 秘密の型（scrub_output）が使う字
TAIL_BYTES = 2_000_000         # reset で読む末尾（会話ビューの depth0 と同じ）
DELTA_MAX = 1_000_000          # 差分がこれより大きいなら reset（巨大な追記を差分で読まない）
LINES_MAX = 12                 # 1 行に含める改行の上限（長い出力で画面を埋めない）
HASH_BACK = 256 * 1024         # カーソル検査で直前の行を探す範囲


def _lines(text):
    """NFC・改行の統一・行末の空白を落とした行（末尾の空行は落とす）。"""
    t = unicodedata.normalize("NFC", str(text if text is not None else ""))
    out = [ln.rstrip() for ln in t.replace("\r\n", "\n").replace("\r", "\n").split("\n")]
    while out and not out[-1]:
        out.pop()
    return out


def clip(text, n=TEXT_MAX, lines=LINES_MAX, words=False):
    """改行は残す（端末らしく）が、行数と文字数は上限で切る（先頭優先）。空白だけの行の連続は 1 行に。
    words＝字で切るとき英数の語の途中で切らない（_head・伏字を通した道具の引数と結果＝_fit）。"""
    out, blank = [], False
    for line in _lines(text):
        if not line:
            if blank or not out:
                continue
            blank = True
        else:
            blank = False
        out.append(line)
    while out and not out[-1]:
        out.pop()
    cut = len(out) > lines
    t = "\n".join(out[:lines])
    if len(t) > n:
        t, cut = (_head(t, n - 1) if words else t[: n - 1]), True
    return t + ("…" if cut and not t.endswith(_OMIT) else "")


def _mark(omitted):
    return "…（中略 %d 行）…" % omitted if omitted > 0 else "…（中略）…"


_MARK_ROOM = 22               # 中略の印（…（中略 N 行）…・N は 11 桁まで）と前後の改行の分


def _mid(head, tail, total, n, lines):
    """先頭の行 head・末尾の行 tail → 「先頭（字数の 40% まで）＋中略の印＋末尾（残りの字数）」。n 字・lines 行以内。
    中略の数＝元の総行数 total − 見えている行（字で途中まで見えている行も見えている側に数える＝多く言わない）。"""
    a = "\n".join(head)
    hc = max(1, n * MID_HEAD[0] // MID_HEAD[1])
    if len(a) > hc:
        a = _head(a, hc).rstrip()
    al = (a.split("\n") if a else [])[:max(0, lines - 2)]      # 印と末尾の 1 行の場所は残す
    a = "\n".join(al)
    room = max(1, lines - len(al) - 1)
    b = "\n".join(tail[-room:])
    tc = max(1, n - len(a) - _MARK_ROOM)
    if len(b) > tc:
        b = _tail(b, tc).lstrip()
    bl = (b.split("\n") if b else [])[-room:]
    mark = _mark(max(0, (total if isinstance(total, int) else 0) - len(al) - len(bl)))
    return "\n".join(x for x in (a, mark, "\n".join(bl)) if x)


def clip_mid(text, n=RESULT_MAX, lines=RESULT_LINES, tail=None, total=None):
    """失敗の結果（理由は末尾に出る）＝clip の亜種: 先頭 40%＋「…（中略 N 行）…」＋末尾 60%。収まるなら全部（空行も残す）。
    tail＝長すぎて両端だけ切り出したときの末尾側の素材（text は先頭側）・total＝元の総行数（中略の数を正しく言う）。"""
    head = _lines(text)
    hl = max(1, lines * MID_HEAD[0] // MID_HEAD[1])
    tl = max(1, lines - hl - 1)
    if tail is not None:
        return _mid(head[:hl], _lines(tail)[-tl:], total, n, lines)
    body = "\n".join(head)
    if len(head) <= lines and len(body) <= n:
        return body
    if len(head) > lines:
        return _mid(head[:hl], head[-tl:], len(head), n, lines)
    # 行は収まるが字が多い（長い行）＝字で 40%／60% に分ける（中略は間に丸ごと入った行の数）
    hc = max(1, n * MID_HEAD[0] // MID_HEAD[1])
    tc = max(1, n - hc - _MARK_ROOM)
    return _mid([_head(body, hc)], [_tail(body, tc)], len(head), n, lines)


def _remid(t, n, lines):
    """もう組んだ失敗の結果（中略の印つき）を、もっと小さい上限で組み直す（中略の数は元の総行数から数え直す）。"""
    ls = str(t or "").split("\n")
    k = next((i for i, x in enumerate(ls) if _MARK_RE.match(x)), None)
    if k is None:
        return clip_mid(t, n, lines)
    m = _MARK_RE.match(ls[k])
    head, tail = ls[:k], ls[k + 1:]
    return clip_mid("\n".join(head), n, lines, tail="\n".join(tail),
                    total=len(head) + len(tail) + int(m.group(1) or 0))


def _head(s, n):
    """先頭 n 字以内（印も含めて）。切れ目が英数の語（_WORD・秘密の型が使う字）の途中なら、その語の断片は捨てる。
    R113-B-S2: 語が _EDGE より長い（区切りが見つからない）ときも断片は返さず、固定の省略の印 _OMIT に置き換える。
    日本語などの語の途中は切ってよい（秘密の型は英数）。"""
    s = str(s if s is not None else "")
    if len(s) <= n:
        return s
    if n <= 0 or s[n] not in _WORD or s[n - 1] not in _WORD:
        return s[:max(0, n)]
    k = n
    while k > 0 and s[k - 1] in _WORD:
        k -= 1                                                # 語の始まりまで戻る（語の断片を残さない）
    if n - k <= _EDGE:
        return s[:k]
    return (s[:k] + _OMIT) if k + len(_OMIT) <= n else s[:k]


def _tail(s, n):
    """末尾 n 字以内（_head の逆向き）。切れ目が英数の語の途中なら、その語の断片は捨てる（長すぎる語は _OMIT）。"""
    s = str(s if s is not None else "")
    if len(s) <= n:
        return s
    if n <= 0:
        return ""
    start = len(s) - n
    if s[start] not in _WORD or s[start - 1] not in _WORD:
        return s[start:]
    k = start
    while k < len(s) and s[k] in _WORD:
        k += 1                                                # 語の終わりまで進む
    if k - start <= _EDGE:
        return s[k:]
    return (_OMIT + s[k:]) if len(s) - k + len(_OMIT) <= n else s[k:]


_HOME_STOP = r"\s/'\"`()\[\]<>|,;（）「」『』、。："
# ホームの下の絶対パス（ユーザー名を含む）。scrub_output のパス規則は直前が空白・=・:・引用符・括弧のときだけ畳む＝
# `…`・>・|・（・：・-（${X:-/Users/…}）・日本語の直後のパスが素通りした（R113-B の実データ下見）。ここでは直前の字を問わず畳む
# （URL の途中＝直前が ASCII の英数は除く: example.com/Users/…・localhost:4780/Users/…）。
_HOME_RE = re.compile(r"(?<![A-Za-z0-9_])/(?:Users|home)/[^%s]+(?:/[^%s]*)*" % (_HOME_STOP, _HOME_STOP))


def _home(m):
    parts = [p for p in m.group(0).split("/") if p]
    return "~" if len(parts) <= 2 else parts[-1]              # ホームそのもの（＝ユーザー名）は ~ に・下は basename


def _scrub(scrub, text):
    """伏字を素材の全文に掛ける（scrub_output の既定の上限は末尾を残す＝先頭が消える＝上限は素材の長さ）。
    先にホームの下の絶対パスを畳む（どの字の後ろでも）。R113-B-S2: 切るのはこの**後**（_fit・_mid_of）。"""
    t = _HOME_RE.sub(_home, str(text if text is not None else ""))
    try:
        return scrub(t, len(t.encode("utf-8")) + 64)
    except TypeError:                                          # 引数 1 つの伏字（テスト用など）
        return scrub(t)


def _fit(scrub, text, n, lines):
    """R113-B-S2: 伏字を素材全体へ掛けてから（切ってから掛けると Bearer などの接頭辞が切れて断片が残る）、lines 行・n 字へ。
    字で切るときは英数の語の途中で切らない（clip の words）。道具の引数と結果はみなこれを通す（Claude も Codex も）。"""
    return clip(_scrub(scrub, str(text if text is not None else "")), n, lines, words=True)


def _tid(tool_use_id):
    """道具と結果を結ぶ短い印（tool_use_id の SHA-256 先頭 8 桁＝ID そのものは載せない）。"""
    s = str(tool_use_id or "")
    return hashlib.sha256(s.encode("utf-8")).hexdigest()[:8] if s else ""


def _ts(d):
    """transcript の行の時刻（ISO 8601・Z）→ epoch 秒（int）。読めなければ None。"""
    t = d.get("timestamp")
    if not isinstance(t, str) or not t:
        return None
    try:
        s = t.strip().replace("Z", "+00:00")
        if "." in s:                                          # 3.9 の fromisoformat は小数 6 桁まで
            head, rest = s.split(".", 1)
            k = 0
            while k < len(rest) and rest[k].isdigit():
                k += 1
            s = head + "." + (rest[:k] + "000000")[:6] + rest[k:]
        dt = datetime.datetime.fromisoformat(s)
        if dt.tzinfo is None:
            return None                                       # 時差の分からない時刻は使わない
        return int(dt.timestamp())
    except (ValueError, OverflowError):
        return None


def _rel(p, cwd):
    """道具のパス → プロジェクト（行の cwd）からの相対。cwd が無い・プロジェクトの外（.. 始まり）・~ 始まりは basename。
    R113-B-S2: cwd が分からなければ相対の入力パスも basename（相対パスの断片でプロジェクトの構造を見せない）。"""
    p = unicodedata.normalize("NFC", str(p or "")).rstrip("/")
    if not p:
        return ""
    base = os.path.basename(p)
    if p.startswith("~"):
        return base
    c = unicodedata.normalize("NFC", str(cwd or "")).rstrip("/")
    if not c or not os.path.isabs(c):
        return base
    if os.path.isabs(p):
        try:
            r = os.path.relpath(p, c)
        except ValueError:
            return base
    else:
        r = os.path.normpath(p)
    if r == ".." or r.startswith("../") or os.path.isabs(r):
        return base
    return r


_SECRET_GUARD = "\x01"       # 秘密だけを伏字にするときの先頭の印（scrub のパスの畳みは先頭の / から始まる＝この印で効かない）


def _scrub_secrets(scrub, p):
    """R113-B-S4: パスの**全体**に秘密の伏字だけを掛ける＝relpath／basename で区切る前（`/` を含む秘密（Bearer の値など）を
    区切ってから伏字にすると、後ろ半分が型に当たらない断片で残った）。scrub のパスの畳み（先頭の / → basename）は先頭の印で
    効かせない＝相対の計算ができる形のまま。ホームの畳みと字数で切るのは relpath の後（_path_arg・_search_arg の _scrub→clip）。"""
    t = _SECRET_GUARD + str(p if p is not None else "")
    try:
        t = scrub(t, len(t.encode("utf-8")) + 64)
    except TypeError:                                          # 引数 1 つの伏字（テスト用など）
        t = scrub(t)
    return t[len(_SECRET_GUARD):] if t.startswith(_SECRET_GUARD) else t


def _rel_scrubbed(p, cwd, scrub):
    """伏字（秘密だけ・パスの全体）→ プロジェクトからの相対／basename（_rel）。cwd にも同じ伏字＝同じ接頭辞は同じ形で比べる。"""
    return _rel(_scrub_secrets(scrub, p), _scrub_secrets(scrub, cwd) if cwd else cwd)


def _path_arg(p, cwd, scrub):
    return clip(_scrub(scrub, _rel_scrubbed(p, cwd, scrub)), PATH_MAX, 1, words=True)


def _url(url, scrub):
    """URL → scheme://host[:port]/path（query・fragment・userinfo は構造的に落とす）。path は区切りごとに伏字
    （まとめて伏字に通すと scrub のパス規則が「/a/b」を basename に畳む）。"""
    try:
        s = urlsplit(str(url or ""))
        host, port = s.hostname or "", s.port
    except ValueError:
        return ""
    if not host:
        return ""
    scheme = re.sub(r"[^a-z0-9+.-]", "", (s.scheme or "").lower())[:10]
    path = "/".join(_scrub(scrub, seg) for seg in (s.path or "").split("/"))     # 伏字は全体に掛けてから切る（R113-B-S2）
    out = "%s://%s%s%s" % (scheme or "http", _scrub(scrub, host), ":%d" % port if port else "", path)
    return clip(out, ARG_MAX, 1, words=True)


def _search_arg(name, inp, cwd, scrub):
    """Grep/Glob の引数: 既定は検索語＋場所（＋Grep の glob）。SHOW_SEARCH_TERMS=False なら場所だけ（R100-1a の決め）。"""
    place = _scrub(scrub, _rel_scrubbed(inp.get("path"), cwd, scrub)) if inp.get("path") else ""
    if not SHOW_SEARCH_TERMS:
        return clip(place, PATH_MAX, 1, words=True)
    parts = []
    pat = str(inp.get("pattern") or "")
    if pat:
        parts.append('pattern: "%s"' % _scrub(scrub, pat))
    if place:
        parts.append('path: "%s"' % place)
    g = str(inp.get("glob") or "") if name == "Grep" else ""
    if g:
        parts.append('glob: "%s"' % _scrub(scrub, g))
    return clip(", ".join(parts), ARG_MAX, 1, words=True)


def cmd_text(cmd, scrub):
    """命令の本文（Claude の Bash の command・Codex の Shell／exec の input）→ 伏字を通した BASH_CMD_MAX 字・BASH_CMD_LINES 行。
    Claude と Codex（source_codex_rollout）で同じ 1 本＝上限も切り方（伏字→切る）も同じ。"""
    return _fit(scrub, cmd, BASH_CMD_MAX, BASH_CMD_LINES)


def out_text(text, scrub, err=False, shell=False):
    """結果の本文 → 伏字を通した本文（空なら ""＝呼び出し側が「（出力なし）」等を入れる）。
    shell＝命令の出力（Bash・Codex の Shell／exec）は BASH_OUT_MAX 字/BASH_OUT_LINES 行、それ以外は RESULT_MAX/RESULT_LINES。
    成功は先頭から・失敗は先頭 40%＋中略＋末尾 60%（理由は末尾に出る）。Claude と Codex で同じ 1 本。"""
    text = str(text if text is not None else "")
    n, ln = (BASH_OUT_MAX, BASH_OUT_LINES) if shell else (RESULT_MAX, RESULT_LINES)
    if err:
        return _mid_of(text, scrub, n, ln)
    return _fit(scrub, text, n, ln)


def _arg(name, inp, scrub, cwd=""):
    inp = inp if isinstance(inp, dict) else {}
    if name == "Bash":
        return cmd_text(inp.get("command") or "", scrub)
    if name in _PATH_TOOLS:
        return _path_arg(inp.get("file_path") or inp.get("notebook_path"), cwd, scrub)
    if name == "Skill":
        return clip(str(inp.get("skill") or ""), 60, 1)
    if name in ("Grep", "Glob"):
        return _search_arg(name, inp, cwd, scrub)
    if name == "WebFetch":
        return _url(inp.get("url"), scrub)
    if name == "WebSearch":
        return ""
    if name in ("Task", "Agent"):
        return _fit(scrub, inp.get("description") or "", 80, 1)
    if name == "TaskCreate":
        return _fit(scrub, inp.get("subject") or inp.get("title") or "", 80, 1)
    if name == "TaskUpdate":
        tid, st = str(inp.get("taskId") or inp.get("id") or "")[:16], str(inp.get("status") or "")[:16]
        return clip(("#" + tid if tid else "") + ("→" + st if st else ""), 40, 1)
    return ""


def _text_of(c):
    if isinstance(c, list):
        c = "\n".join(str(x.get("text") or "") for x in c if isinstance(x, dict) and x.get("type") == "text")
    return str(c or "")


def _nlines(s):
    s = s.rstrip("\n")
    return (s.count("\n") + 1) if s else 0


def _diff_body(body, scrub, col=DIFF_COL):
    return _fit(scrub, body, col, 1)


def _diff_rows(patch, scrub):
    """structuredPatch（hunk の並び）→ ([[記号, 行番号, 本文]], 足した行数, 消した行数)。本文は伏字を通して DIFF_COL 字。"""
    out, add, rem = [], 0, 0
    for h in patch if isinstance(patch, list) else []:
        if not isinstance(h, dict):
            continue
        o = int(h.get("oldStart") or 0) if isinstance(h.get("oldStart"), int) else 0
        n = int(h.get("newStart") or 0) if isinstance(h.get("newStart"), int) else 0
        if out and len(out) < DIFF_LINES - 1:     # 区切りも上限の中（本文が入らないのに「…」だけ積まない）
            out.append(["…", 0, ""])
        for line in h.get("lines") or []:
            line = str(line)
            sign, body = (line[:1], line[1:]) if line[:1] in ("+", "-", " ") else (" ", line)
            if sign == "+":
                add += 1
                num, n = n, n + 1
            elif sign == "-":
                rem += 1
                num, o = o, o + 1
            else:
                num, o, n = n, o + 1, n + 1
            if len(out) < DIFF_LINES:
                out.append([sign, num, _diff_body(body, scrub)])
    return out, add, rem


def _cap_diff(d, lines, col):
    """縮めるとき: 差分の本文を lines 行・1 行 col 字へ（最後が区切りだけにならない）。"""
    out = []
    for x in d[:lines] if isinstance(d, list) else []:
        if isinstance(x, list) and len(x) == 3:
            body = x[2] if isinstance(x[2], str) else ""
            out.append([x[0], x[1], clip(body, col, 1) if len(body) > col else body])
    while out and out[-1][0] == "…":
        out.pop()
    return out


def _infer_tool(tur):
    """道具の名前が分からない結果（道具の行が前のページ＝差分の取得をまたいだ）→ 結果の形から道具を当てる。
    当てられなければ ""（汎用の本文の行）。"""
    if not tur:
        return ""
    if isinstance(tur.get("structuredPatch"), list) or (tur.get("type") in ("create", "update") and "filePath" in tur):
        return "Write" if tur.get("type") == "create" else "Edit"
    if isinstance(tur.get("file"), dict):
        return "Read"
    if "stdout" in tur:
        return "Bash"
    if "numFiles" in tur or isinstance(tur.get("filenames"), list):
        return "Grep"
    if "code" in tur and "bytes" in tur:
        return "WebFetch"
    if any(k in tur for k in ("totalToolUseCount", "totalDurationMs", "totalTokens")) or tur.get("isAsync") is True \
            or (isinstance(tur.get("agentId"), str) and "status" in tur):
        return "Task"
    return ""


def _mid_of(text, scrub, n, lines):
    """失敗の本文 → サンドイッチ（先頭 40%＋中略＋末尾 60%）。R113-B-S2: 伏字は本文の全体に掛けてから切る
    （両端だけを切り出してから掛けると、末尾側の切れ目で `Bearer ` が落ちてトークンだけが残った）。中略の数は元の総行数から。"""
    return clip_mid(_scrub(scrub, text), n, lines)


def _result_row(b, tur, tool, scrub, cwd=""):
    """tool_result 1 個 → 結果の行。t には人が読める要約を必ず入れる（古い PWA でもそのまま読める）。
    Read の本文・画像・元のファイル・Write の全文は載せない（行数と先頭だけ）。"""
    row = {"k": "res"}
    fid = _tid(b.get("tool_use_id"))
    if fid:
        row["for"] = fid
    if b.get("is_error") is True:
        row["err"] = 1
    text = _text_of(b.get("content"))
    tur = tur if isinstance(tur, dict) else {}
    name = tool or _infer_tool(tur)
    if row.get("err"):
        # 失敗は理由の本文を出す（拒否された Read を「1 行を読み込み」と言わない＝別モデルレビュー）。
        # R113-B: 理由は末尾に出る＝先頭 40%＋中略＋末尾 60%（Bash は出力の上限で）
        row["nl"] = _nlines(text)
        row["t"] = out_text(text, scrub, err=True, shell=name == "Bash" or "stdout" in tur) or "（失敗）"
        if tur.get("interrupted") is True:
            row["int"] = 1
        return row
    if name == "Read" or (isinstance(tur.get("file"), dict) and tur.get("type") in ("text", "image")):
        f = tur.get("file") if isinstance(tur.get("file"), dict) else {}
        if tur.get("type") == "image":
            row.update(img=1, t="画像を読み込み")
        else:
            rl = f.get("numLines") if isinstance(f.get("numLines"), int) else _nlines(text)
            row.update(rl=rl, t="%d 行を読み込み" % rl)
        return row
    if name in ("Edit", "MultiEdit", "Write") and (isinstance(tur.get("structuredPatch"), list) or "content" in tur):
        f = _path_arg(tur.get("filePath"), cwd, scrub)
        if tur.get("type") == "create":
            body = str(tur.get("content") or "")
            lines = body.split("\n")
            row.update(f=f, wl=_nlines(body), d=[["+", i + 1, _diff_body(x, scrub)] for i, x in enumerate(lines[:WRITE_HEAD])])
            row["t"] = "%s に %d 行を書き込み" % (f, row["wl"])
            return row
        d, add, rem = _diff_rows(tur.get("structuredPatch"), scrub)
        row.update(f=f, add=add, rem=rem, d=d, t="%s を更新（+%d −%d）" % (f, add, rem))
        return row
    if name == "Bash" or "stdout" in tur:
        out = str(tur.get("stdout") or "") if tur else text
        err = str(tur.get("stderr") or "") if tur else ""
        body = (out + ("\n" + err if err and out else err)) if tur else text
        row["nl"] = _nlines(body)
        row["t"] = out_text(body, scrub, shell=True) or "（出力なし）"
        if tur.get("interrupted") is True:
            row["int"] = 1
        if tur.get("backgroundTaskId"):
            row["bg"] = 1
        return row
    if name in ("Grep", "Glob"):
        nf = tur.get("numFiles") if isinstance(tur.get("numFiles"), int) else None
        if nf is None and isinstance(tur.get("filenames"), list):
            nf = len(tur["filenames"])
        if nf is None:
            nf = _nlines(text)
        row.update(nf=nf, t="%d 件" % nf)
        return row
    if name == "WebFetch":
        code, size = tur.get("code"), tur.get("bytes")
        row.update(t="取得 %s · %d KB" % (code if isinstance(code, int) else "?",
                                            (size // 1024) if isinstance(size, int) else 0))
        if isinstance(code, int):
            row["code"] = code
        return row
    if name in ("Task", "Agent"):
        aid = tur.get("agentId")
        if isinstance(aid, str) and _AGENT_ID_RE.fullmatch(aid):
            row["_aid"] = aid                                 # 子の行（kids）を添える印＝rows_from_lines が外す
        if tur.get("isAsync") is True or tur.get("status") in ("async_launched", "running"):
            row.update(st="bg", t="裏で動いています（サブエージェント）")
            return row
        tu, tk, ms = tur.get("totalToolUseCount"), tur.get("totalTokens"), tur.get("totalDurationMs")
        parts = []
        if isinstance(tu, int):
            row["tu"] = tu
            parts.append("%d 回" % tu)
        if isinstance(tk, int):
            row["tk"] = tk
            parts.append("%.1fk トークン" % (tk / 1000.0))
        if isinstance(ms, int):
            row["ms"] = ms
            parts.append("%d秒" % (ms // 1000))
        row["t"] = "完了" + ("（" + " · ".join(parts) + "）" if parts else "")
        return row
    row["nl"] = _nlines(text)
    row["t"] = out_text(text, scrub) or "（出力なし）"
    return row


def _question(block, scrub=None):
    """(本文, 選択肢のラベル) — 選択肢は 4 件・60 字まで。scrub＝サブエージェントの行のとき伏字→切る（_say）。"""
    try:
        q = block["input"]["questions"][0]
        labels = [_say(str(o.get("label", "")), scrub, 60, 1) for o in q.get("options", []) if isinstance(o, dict)][:4]
        return _say(str(q.get("question", "")), scrub, TEXT_MAX, 6), [x for x in labels if x]
    except (KeyError, IndexError, TypeError, AttributeError):
        return "", []


def _todos(inp, scrub):
    items = []
    for t in (inp or {}).get("todos") or []:
        if not isinstance(t, dict):
            continue
        st = {"completed": "c", "in_progress": "i"}.get(str(t.get("status") or ""), "p")
        items.append([st, _fit(scrub, t.get("content") or t.get("activeForm") or "", 80, 1)])
        if len(items) >= TODO_MAX:
            break
    return items


def _say(text, scrub, n, lines):
    """発話（u・a・q）の本文。scrub＝None は親の会話（従来どおり上限だけ）。サブエージェント（kids）の発話は
    R113-B-S2 で伏字→切る（親の道具の行と同じ順＝_fit）＝親の AI が子へ渡した指示・子の返事に貼られた秘密を残さない。"""
    return clip(text, n, lines) if scrub is None else _fit(scrub, text, n, lines)


def _user_text(text, command_re, scrub=None):
    """(kind, text) か None。`<` 始まりの注入行は捨てる（<command-name> だけ /name で残す）。中断は int。
    scrub＝サブエージェントの行（kids）のとき伏字→切る（_say）。"""
    t = str(text or "").strip()
    if t.startswith("<pasted_content"):
        t = _PASTE_TAG_RE.sub("", t).strip()                 # R121: 貼り付けの囲みだけ外す（中身は人の指示）
    if not t:
        return None
    if t.startswith(_INTERRUPT):
        return ("int", "中断されました")
    if t.startswith("<"):
        m = command_re.search(t)
        return ("cmd", "/" + m.group(1)) if m else None
    return ("u", _say(t, scrub, USER_MAX, USER_LINES))


# ── サブエージェント（子 transcript）の発見＝office_server の minions と同じ規則（R113-B で関数に） ──────────
def subagent_dir(transcript):
    """親の transcript（<project>/<session>.jsonl）→ 子の置き場 <project>/<session>/subagents。"""
    p = os.fspath(transcript)
    return os.path.join(os.path.dirname(p), os.path.splitext(os.path.basename(p))[0], "subagents")


def subagent_root(transcript):
    """R113-B-S2: 子の置き場の実体。親のプロジェクトの実体（realpath）の下の <session>/subagents と**一致する**ときだけ返す
    （<session> や subagents がシンボリックリンクで外を指す＝None＝何も読まない）。無い・ディレクトリでない＝None。"""
    p = os.fspath(transcript)
    try:
        want = os.path.join(os.path.realpath(os.path.dirname(p)), os.path.splitext(os.path.basename(p))[0], "subagents")
        real = os.path.realpath(subagent_dir(p))
    except (OSError, ValueError):
        return None
    if real != want or not os.path.isdir(real):
        return None
    return real


def subagent_files(transcript):
    """子 transcript（subagents/**/*.jsonl・Workflow は subagents/workflows/wf_*/）を列挙する。無い・読めない＝何も出さない。
    R113-B-S2: 置き場は subagent_root（親のプロジェクトの配下へ閉じ込め）・下のディレクトリのリンクは辿らない（os.walk の既定）・
    各ファイルも実体が置き場の中の通常ファイルだけ（リンクで外・FIFO 等は出さない）。office_server の minions も同じ規則。"""
    root = subagent_root(transcript)
    if root is None:
        return
    try:
        for base, _dirs, files in os.walk(root):
            for fn in files:
                if fn.endswith(".jsonl"):
                    fp = os.path.join(base, fn)
                    if _inside(fp, root) and os.path.isfile(fp):
                        yield fp
    except OSError:
        return


def _inside(path, base):
    """path の実体が base（の実体）の中か（シンボリックリンクで外へ出ない＝閉じ込め）。"""
    try:
        real, root = os.path.realpath(path), os.path.realpath(base)
        return os.path.commonpath([real, root]) == root and real != root
    except (OSError, ValueError):
        return False


def kid_rows(path, scrub, command_re):
    """子 transcript の末尾 → 端末の行の末尾 TASK_KIDS_MAX 件（親と同じ変換・同じ伏字。子の行は isSidechain）。"""
    with open(path, "rb") as f:
        size = os.fstat(f.fileno()).st_size
        start = max(0, size - KIDS_TAIL_BYTES)
        f.seek(start)
        data = f.read(size - start)
    cut = data.rfind(b"\n")
    if cut < 0:
        return []
    lines = data[:cut].split(b"\n")
    if start > 0:
        lines = lines[1:]                                     # 先頭は行の途中
    return rows_from_lines(lines, scrub, command_re, sidechain=True)[-TASK_KIDS_MAX:]


def _kids_reader(transcript, scrub, command_re):
    """agentId → 子の行（無ければ None）。子の置き場の一覧は 1 枚につき 1 回だけ作る。"""
    index = None

    def kids(aid):
        nonlocal index
        if not (isinstance(aid, str) and _AGENT_ID_RE.fullmatch(aid)):
            return None
        if index is None:
            index = {}
            for fp in subagent_files(transcript):
                index.setdefault(os.path.basename(fp), fp)
        fp = index.get("agent-%s.jsonl" % aid)
        root = subagent_root(transcript)
        if not fp or root is None or not _inside(fp, root):     # 一覧の後に差し替えられても閉じ込めを確かめ直す
            return None
        return kid_rows(fp, scrub, command_re)
    return kids


def rows_from_lines(lines, scrub, command_re, line_index=False, kids=None, sidechain=False):
    """transcript の行（str か bytes）→ 端末の行 [{"k": 種類, …}]（時系列）。壊れた行・関係ない行は黙って飛ばす。
    v2（R102-3・本人「Claude Code のセッションみたいに中身が見えるように」）: 行の時刻 ts・道具と結果の印（id/for）・
    差分（d・add・rem）・Todo・中断（int）・1 ターンの所要（turn）・会話の圧縮（sys）。
    R113-B: 考え（thinking）は本文を持たず、次の見える行の直前（とループの終わり）に {k:"th", n, chars} を 1 行。
    パスは行の cwd（無ければ前の行の cwd）からの相対。kids＝agentId → 子の行（新しい KIDS_PAGE_MAX 件の Task の結果に添える）。
    sidechain＝子 transcript を読むとき（子の行は全部 isSidechain）。"""
    rows = []
    said = scrub if sidechain else None                       # R113-B-S2: 子の発話（u・a・q）は伏字→切る（_say）
    names = {}                                                # tool_use_id → 道具の名前（結果の読み方を決める）
    cwd = ""
    th = None                                                 # まだ出していない考えの印

    def flush():
        nonlocal th
        if th is not None:
            rows.append(th)
            th = None

    for li, ln in enumerate(lines):
        try:
            d = json.loads(ln)
        except (ValueError, TypeError):
            continue
        if not isinstance(d, dict) or (d.get("isSidechain") and not sidechain):
            continue
        if isinstance(d.get("cwd"), str) and d["cwd"]:
            cwd = d["cwd"]
        ts = _ts(d)

        def add(r, li=li, ts=ts):
            flush()                                           # 考えの印は次の見える行の直前に
            if ts is not None:
                r["ts"] = ts
            if line_index:
                r["_l"] = li                                  # R104-2: 元の行（遡りのカーソル用・封じる前に外す）
            rows.append(r)

        typ = d.get("type")
        if typ == "system":
            st = d.get("subtype")
            if st == "turn_duration" and isinstance(d.get("durationMs"), int):
                add({"k": "turn", "ms": int(d["durationMs"]), "t": "%d秒 作業" % (int(d["durationMs"]) // 1000)})
            elif st == "compact_boundary":
                add({"k": "sys", "t": "会話を圧縮しました"})
            continue
        if typ not in ("user", "assistant") or d.get("isMeta"):
            continue
        if d.get("isCompactSummary"):
            add({"k": "sys", "t": "会話を圧縮しました"})
            continue
        content = (d.get("message") or {}).get("content") if isinstance(d.get("message"), dict) else None
        if typ == "user":
            if isinstance(content, str):
                r = _user_text(content, command_re, said)
                if r:
                    add({"k": r[0], "t": r[1]})
                continue
            for b in content if isinstance(content, list) else []:
                if not isinstance(b, dict):
                    continue
                if b.get("type") == "text":
                    r = _user_text(b.get("text"), command_re, said)
                    if r:
                        add({"k": r[0], "t": r[1]})
                elif b.get("type") == "tool_result":
                    tool = names.get(str(b.get("tool_use_id") or ""))
                    if tool == "TodoWrite":
                        continue                              # Todo は tool_use の側（todo 行）で出す
                    add(_result_row(b, d.get("toolUseResult"), tool, scrub, cwd))
            continue
        for b in content if isinstance(content, list) else []:
            if not isinstance(b, dict):
                continue
            if b.get("type") in ("thinking", "redacted_thinking"):
                # R113-B: 考えは数と字数だけ（本文は一切持たない）。続く考えは 1 行にまとめる
                if th is None:
                    th = {"k": "th", "n": 0, "chars": 0}
                    if ts is not None:
                        th["ts"] = ts
                    if line_index:
                        th["_l"] = li
                th["n"] += 1
                th["chars"] += len(b.get("thinking")) if isinstance(b.get("thinking"), str) else 0
                continue
            elif b.get("type") == "text" and str(b.get("text") or "").strip():
                r = {"k": "a", "t": _say(b["text"], said, TEXT_MAX, TEXT_LINES)}
                if d.get("isApiErrorMessage"):
                    r["err"] = 1
                add(r)
            elif b.get("type") == "tool_use":
                name = str(b.get("name") or "")[:48]
                names[str(b.get("id") or "")] = name
                if name == "AskUserQuestion":
                    q, opts = _question(b, said)
                    if q:
                        r = {"k": "q", "t": q}
                        if opts:
                            r["o"] = opts
                        add(r)
                    continue
                if name == "ExitPlanMode":
                    # R104-2（洗い出し backlog-plan-text-missing）: 計画の確認でスマホに計画の本文が無かった＝端末の封書に載せる
                    # （スマホに会話を見せる＝dialogRelay が ON のときだけ届く・秘密は伏字・長さの上限つき）
                    plan = str((b.get("input") or {}).get("plan") or "")
                    if plan.strip():
                        # 伏字は全文に掛けてから先頭から上限へ（_scrub が上限を渡す＝伏字の既定の「末尾を残す」で先頭が消えない）
                        add({"k": "plan", "t": _fit(scrub, plan, PLAN_MAX, PLAN_LINES)})
                    continue
                if name == "TodoWrite":
                    items = _todos(b.get("input"), scrub)
                    if items:
                        add({"k": "todo", "i": items, "t": "Todo を更新"})
                    continue
                r = {"k": "tool", "n": name, "t": _arg(name, b.get("input"), scrub, cwd)}
                tid = _tid(b.get("id"))
                if tid:
                    r["id"] = tid
                inp = b.get("input") if isinstance(b.get("input"), dict) else {}
                desc = inp.get("description")
                if isinstance(desc, str) and desc.strip():
                    ds = _fit(scrub, desc, DESC_MAX, 1)        # R121: 伏字→切る（引数と同じ順）
                    if ds and ds != r["t"]:
                        r["ds"] = ds
                if inp.get("run_in_background") is True:
                    r["bg"] = 1
                if name in ("Task", "Agent"):
                    sub = str((b.get("input") or {}).get("subagent_type") or "")[:40]
                    if sub:
                        r["sub"] = sub
                add(r)
    flush()
    if kids is not None:
        n = 0
        for r in reversed(rows):                              # 新しい方の Task から（子を読む回数に上限）
            if n >= KIDS_PAGE_MAX:
                break
            if r.get("_aid"):
                n += 1
                try:
                    got = kids(r["_aid"])
                except OSError:
                    got = None
                if got:
                    r["kids"] = got
    for r in rows:
        r.pop("_aid", None)
    return rows


def line_hash(raw):
    return hashlib.sha256(raw).hexdigest()[:16]


def valid_after(after):
    """スマホから来た after を検算する（形が違えば None＝reset）。"""
    if not isinstance(after, dict):
        return None
    off, h = after.get("off"), after.get("h")
    if (not isinstance(off, int) or isinstance(off, bool) or not (0 < off < 2 ** 53)
            or not isinstance(h, str) or len(h) != 16 or any(c not in "0123456789abcdef" for c in h)):
        return None
    return {"off": off, "h": h}


def _hash_before(f, off):
    """off の直前の 1 行（改行を除く）の line_hash。off の直前が改行でない・行が長すぎる＝None。"""
    start = max(0, off - HASH_BACK)
    f.seek(start)
    data = f.read(off - start)
    if not data.endswith(b"\n"):
        return None
    body = data[:-1]
    nl = body.rfind(b"\n")
    if nl < 0 and start > 0:
        return None
    return line_hash(body[nl + 1:])


def read_page(path, after, scrub, command_re, subagents=True):
    """{"rows", "cur", "reset", "gap"}。path は呼び出し側が閉じ込め済み（_session_transcript）。
    reset=True なら画面を入れ替える（末尾から ROWS_MAX 行）。gap=True は「途中を省いた」（行が上限を超えた）。
    subagents＝Task の結果に子の末尾（kids）を添える（R113-B・子は都度読み直すスナップショット＝カーソルに乗せない）。"""
    after = valid_after(after)
    kids = _kids_reader(path, scrub, command_re) if subagents else None
    with open(path, "rb") as f:
        size = os.fstat(f.fileno()).st_size
        if after and after["off"] <= size and size - after["off"] <= DELTA_MAX \
                and _hash_before(f, after["off"]) == after["h"]:
            f.seek(after["off"])
            data = f.read(size - after["off"])
            cut = data.rfind(b"\n")
            if cut < 0:                                   # 新しい完全な行が無い（書きかけだけ）
                return {"rows": [], "cur": after, "reset": False, "gap": False}
            lines = data[:cut].split(b"\n")
            rows = rows_from_lines(lines, scrub, command_re, kids=kids)
            cur = {"off": after["off"] + cut + 1, "h": line_hash(lines[-1])}
            return {"rows": rows[-ROWS_MAX:], "cur": cur, "reset": False, "gap": len(rows) > ROWS_MAX}
        start = max(0, size - TAIL_BYTES)
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
    page = _with_offsets(lines, base, scrub, command_re, ROWS_MAX, at_top=(start == 0), kids=kids)
    page.update({"cur": {"off": start + cut + 1, "h": line_hash(data[:cut].rsplit(b"\n", 1)[-1])}, "reset": True})
    page["gap"] = page["gap"] or start > 0
    return page


BACK_BYTES = 600_000           # R104-2: 遡りで読む範囲（開く前の行）
BACK_ROWS = 60                 # 遡り 1 回の行数（封書 1 通に収める）


def _with_offsets(lines, base, scrub, command_re, keep, at_top, cut_last=None, kids=None):
    """行 → 端末の行（末尾から keep 件）。各行の元の位置を _offs に持つ（beg は public() が封じる直前に決める）。
    1 行から表示の行が複数できる＝位置は {off: その行の始まり, h, i: その行の中で何番目か}（別モデルレビュー: 行の途中で切ると前半が戻らなかった）。
    cut_last＝最後の行は i 番目より前の表示の行だけ使う（遡りのカーソルの行）。"""
    starts, o = [], base
    for ln in lines:
        starts.append(o)
        o += len(ln) + 1
    rows = rows_from_lines(lines, scrub, command_re, line_index=True, kids=kids)
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
        offs.append({"off": starts[li], "h": line_hash(lines[li]), "i": n} if isinstance(li, int) else None)
    for r in rows[:-keep] if len(rows) > keep else []:
        r.pop("_l", None)
    page = {"rows": kept, "_offs": offs, "_top": at_top and len(rows) <= keep, "gap": len(rows) > keep}
    if lines:
        page["_base"] = {"off": base, "h": line_hash(lines[0]), "i": 0}   # 表示できる行が無い区間でも遡りを止めない
    return page


def valid_before(before):
    """遡りのカーソル（その行の始まり・その行の line_hash・その行の中の表示の行の番号 i）。形が違えば None。"""
    out = valid_after(before)
    if out is None:
        return None
    i = before.get("i", 0)
    if not isinstance(i, int) or isinstance(i, bool) or not (0 <= i < 10000):
        return None
    out["i"] = i
    return out


LINE_MAX = 16 * 1024 * 1024    # カーソルの行として確かめる 1 行の上限（画像つきの大きな行も）


def _line_at(f, off):
    """off から始まる 1 行（改行を除く）。LINE_MAX を超える・改行が無い＝None（別モデルレビュー: 256KiB を超える行で必ず外れた）。"""
    f.seek(off)
    buf = b""
    while len(buf) <= LINE_MAX:
        chunk = f.read(HASH_BACK)
        if not chunk:
            return None
        nl = chunk.find(b"\n")
        if nl >= 0:
            return buf + chunk[:nl]
        buf += chunk
    return None


def _hash_at(f, off):
    line = _line_at(f, off)
    return line_hash(line) if line is not None else None


def read_before(path, before, scrub, command_re, subagents=True):
    """R104-2（洗い出し backlog-term-depth）: 画面のいちばん上の行（before）より前の BACK_ROWS 行。
    {"rows", "older": True, "_offs", "_top"}。カーソルが違う（ファイルが差し替わった）＝None（スマホは遡りをやめる）。"""
    before = valid_before(before)
    if not before:
        return None
    kids = _kids_reader(path, scrub, command_re) if subagents else None
    with open(path, "rb") as f:
        size = os.fstat(f.fileno()).st_size
        cursor_line = _line_at(f, before["off"]) if before["off"] <= size else None
        if cursor_line is None or line_hash(cursor_line) != before["h"]:
            return None
        span = BACK_BYTES
        while True:
            start = max(0, before["off"] - span)
            f.seek(start)
            data = f.read(before["off"] - start)
            lines = data[:-1].split(b"\n") if data.endswith(b"\n") else (data.split(b"\n") if data else [])
            base = start
            if start > 0 and lines:
                base += len(lines[0]) + 1
                lines = lines[1:]
            if lines or start == 0 or span >= LINE_MAX:
                break
            span *= 4                                     # 窓が大きな 1 行の途中だけ＝広げる（前に行があるのに「はじまり」と言わない）
    if before.get("i"):
        # カーソルの行の前半（i 番目より前の表示の行）も今回の頁に含める
        lines = lines + [cursor_line]
        page = _with_offsets(lines, base, scrub, command_re, BACK_ROWS, at_top=(start == 0), cut_last=before["i"], kids=kids)
    else:
        page = _with_offsets(lines, base, scrub, command_re, BACK_ROWS, at_top=(start == 0), kids=kids)
    if not lines and start > 0:
        page["_top"] = False                              # 16MiB を超える 1 行で窓が埋まった＝前は分からないが「はじまり」とは言わない
    page.update({"older": True, "reset": False, "cur": None})
    return page


def public(page):
    """封じる形: 残っている最初の行の位置を beg に（もう前が無ければ null）・内部の _offs/_top を外す。"""
    out = {k: v for k, v in page.items() if k not in ("_offs", "_top", "_base")}
    offs = page.get("_offs")
    if isinstance(offs, list):
        first = next((o for o in offs if o), None) or page.get("_base")
        top = bool(page.get("_top")) and len(offs) == len(page.get("rows") or [])
        out["beg"] = None if top else first
        out["top"] = top                                  # beg が null でも先頭とは限らない（大きすぎる 1 行で止まった）＝スマホは top のときだけ「会話のはじまり」
    return out


def sealed_size(page):
    """封じるときの本文の大きさ（dialog_seal.seal と同じ: public(page) の JSON → zlib 6）。ENVELOPE_BUDGET 以下なら 1 応答に入る。"""
    return len(zlib.compress(json.dumps(public(page), ensure_ascii=False).encode("utf-8"), 6))


def fits(page):
    return sealed_size(page) <= ENVELOPE_BUDGET


# shrink の段（R113-B で前に 3 段を足した。番号は封じる側のループがそのまま数える）
SHRINK_KIDS = 0            # 0'  サブエージェントの行（kids）を落とす（古い Task から・入った時点で止める）
SHRINK_DIFF_HALF = 1       #     差分の本文を半分（DIFF_LINES/2 行×DIFF_COL/2 字・新しい 4 件も）
SHRINK_OLD = 2             # 0'' 命令（CMD_TOOLS）の入出力・差分を従来相当へ（入力 200 字/4 行・出力 600 字/8 行・差分 16 行×120 字）
SHRINK_DIFF_DROP = 3       #     新しい 4 件以外の差分の本文を外す（R102-3 の step 0）
SHRINK_TEXT = 4            #     結果の本文を 2 行・計画を 12 行に（R102-3 の step 1）
SHRINK_HALVE = 5           # 以降 古い方から半分落として gap（R102-3 の step 2〜）
SHRINK_STEPS = SHRINK_HALVE + ROWS_MAX.bit_length()   # 150 行が 1 行になるまでの段の数（封じる側のループの回数＝これ＋1）


def shrink(page, step):
    """封書に入らないときの縮め方（step 0 から順に・上の SHRINK_* の表）。変えたら True（もう縮められなければ False）。
    0'＝kids は古い Task から外して「N 件のサブエージェントの行を省略」の印 kx を残す（入った時点で止める＝新しい方は残る）。"""
    rows = page.get("rows") or []
    if step == SHRINK_KIDS:
        order = sorted((i for i, r in enumerate(rows) if r.get("kids")), key=lambda i: (i, -len(rows[i]["kids"])))
        for i in order:
            r = rows[i]
            r["kx"] = (r.get("kx") if isinstance(r.get("kx"), int) else 0) + len(r.pop("kids"))
            if fits(page):
                break
        return True
    if step == SHRINK_DIFF_HALF:
        for r in rows:
            if isinstance(r.get("d"), list):
                r["d"] = _cap_diff(r["d"], (WRITE_HEAD if "wl" in r else DIFF_LINES) // 2, DIFF_COL // 2)
        return True
    if step == SHRINK_OLD:
        for r in rows:
            t = r.get("t")
            if r.get("k") == "tool" and r.get("n") in CMD_TOOLS and isinstance(t, str):
                r["t"] = clip(t, ARG_MAX, ARG_LINES)
            elif r.get("k") == "res" and "nl" in r and isinstance(t, str):
                r["t"] = _remid(t, RESULT_MAX, RESULT_LINES) if r.get("err") else clip(t, RESULT_MAX, RESULT_LINES)
            if isinstance(r.get("d"), list):
                r["d"] = _cap_diff(r["d"], WRITE_HEAD_OLD if "wl" in r else DIFF_LINES_OLD, DIFF_COL_OLD)
        return True
    if step == SHRINK_DIFF_DROP:
        keep = 4
        for r in reversed(rows):
            if "d" in r:
                if keep > 0:
                    keep -= 1
                else:
                    r.pop("d", None)
        return True
    if step == SHRINK_TEXT:
        for r in rows:
            if r.get("k") == "res" and isinstance(r.get("t"), str):
                r["t"] = clip(r["t"], 200, 2)
            elif r.get("k") == "plan" and isinstance(r.get("t"), str):
                r["t"] = clip(r["t"], 800, 12)             # 計画は先頭だけ残す（全文は次の取得で）
        return True
    if len(rows) < 2:
        return False
    cut = len(rows) // 2
    page["rows"] = rows[cut:]
    if isinstance(page.get("_offs"), list):
        page["_offs"] = page["_offs"][cut:]
        page["_top"] = False                              # 落とした分の前がまだある
    page["gap"] = True
    return True


def _sibling(name):
    """同じ server/ の読み手を遅延 import（あちらが office_term を import する＝循環を避ける）。
    server/ が sys.path に無い読み込み方（importlib で直に読む）でも引ける。"""
    try:
        return importlib.import_module(name)
    except ModuleNotFoundError:
        import sys
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        try:
            return importlib.import_module(name)
        finally:
            del sys.path[0]


def session_page(session, path, after, scrub, command_re, before=None, home=None):
    """R113-B: ターミナル画面の 1 枚を session id で選ぶ（分岐はここ 1 か所）。
    cx-＝Codex の rollout（source_codex_rollout が SQLite の rollout_path を ~/.codex/sessions 配下へ閉じ込めて読む・path は使わない）。
    bot-＝R128 出勤の口の Bot の記録（source_door_log が <id> を確かめ・登録した Bot だけ・口のフォルダの中の
    <id>.log.jsonl だけを読む・path は使わない・transcript は探さない）。
    それ以外＝従来の transcript（path は呼び出し側が閉じ込め済み・read_page／read_before そのまま）。
    遡り（before だけ）でカーソルが合わない＝None。cx- の rollout・bot- の記録が引けない・閉じ込めの外＝FileNotFoundError（OSError）。
    home＝Codex と口の置き場の親（office_server は自分の _HOME を渡す＝一覧と同じ HOME・None なら OFFICE_HOME か実 HOME）。"""
    if isinstance(session, str) and session.startswith("cx-"):
        return _sibling("source_codex_rollout").term_page(session, after, scrub, before=before, home=home)
    if isinstance(session, str) and session.startswith("bot-"):
        return _sibling("source_door_log").term_page(session, after, scrub, before=before, home=home)
    if before is not None and after is None:
        return read_before(path, before, scrub, command_re)
    return read_page(path, after, scrub, command_re)
