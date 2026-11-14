# -*- coding: utf-8 -*-
"""R113-B（本人裁定 2026-10-08）: Codex のやり取りをスマホの「セッション画面」（端末の行・E2EE 封書 kind:"term"）へ。

一覧とゲージは従来どおり SQLite のメタデータだけ（source_codex）。本文（rollout の JSONL）を読むのは**ここだけ**で、
呼ばれるのは office_term.session_page の cx- の分岐（＝スマホに会話を見せる dialogRelay が ON のときの term 封書）だけ。

★掟
  - 標準ライブラリだけ・server/ は flat（tools/check_stdlib.py）。**読み取り専用**＝書く経路を作らない。
  - 安全な読み取り（B11）: `cx-<thread_id>` を source_codex._SESSION_RE で検算 → `state_5.sqlite` を `mode=ro`＋`PRAGMA query_only=1`
    で開き `threads.rollout_path` を引く → `Path.resolve()` が `~/.codex/sessions/` か `~/.codex/archived_sessions/` の**配下**で、
    名前が `rollout-*.jsonl` の通常ファイルであることを検算（違えば読まない）→ そこだけを `rb` で開く。
    DB が書き換えられて rollout_path が別の場所（鍵・設定・別の閉じ込めの外）を指しても読まない。
    許可の根（と ~/.codex）そのものがシンボリックリンクなら、その根は使わない（R113-B-S3・_roots＝根の実体で比べても外を指せる）。
  - 行への変換（B12・rows_from_codex_lines）: rollout は**非公開仕様**（形の記録＝docs/codex-rollout-format.md）。
    不明な type/payload は黙って無視（落ちない）。上限と縮め方は office_term と**同じ 1 本**（R113-B-I）:
    命令（Shell・exec）の入力＝office_term.cmd_text（BASH_CMD_MAX）・出力＝office_term.out_text（shell は BASH_OUT_MAX・失敗は
    先頭 40%＋中略＋末尾 60%）・差分＝office_term._diff_rows／_diff_body（DIFF_LINES×DIFF_COL）・新規作成は WRITE_HEAD 行・
    縮める段（shrink の SHRINK_OLD）は office_term.CMD_TOOLS で Shell・exec も Bash と同じに扱う。
    道具の引数と結果は office_term._fit（R113-B-S2: ホームの下のパスを畳む→scrub＝office_actions.scrub_output の秘密の伏字・
    パス→basename を**素材全体**へ掛けてから、英数の語の途中で切らずに上限へ）を通してから載せる。素材の先切りを直書きしない。
  - 同じ出来事を二度書く形がある＝**片方だけを使う**（実測 2026-10-08・docs に数字）:
    `event_msg.item_completed` の AgentMessage／UserMessage／Reasoning は `response_item` の message／reasoning の写し
    （AgentMessage は同じ id・同じ本文で 1097/1097）。差分の取得（数秒おき）は行の途中で区切られるので、窓をまたいだ写しは
    状態なしでは突き合わせられない＝**どの版にもある response_item 側だけ**を使う（item_completed の無い旧版もそのまま同じ行になる）。
    item_completed からは response_item に無いものだけ（CommandExecution・FileChange・exec の中から呼んだ McpToolCall）を使う。
    R113-B-S3: McpToolCall が写しかどうかも窓の中で突き合わせない（窓をまたぐと二重に出た）＝行 1 本の id の形だけで決める（_exec_mcp）。
  - 人の指示は response_item の user のうち、`<` で始まる注入（environment_context・recommended_plugins・image の枠…）と
    `# AGENTS.md instructions` を部品ごとに落とす（実測: 落とした残り＝UserMessage の本文と一致 175/175）。
  - 考え（reasoning）は本文を載せない（encrypted_content も summary も）＝続いた件数だけの th（n）。
  - exec（JS のコード文字列）の input は**解釈しない**（自前で JS を解析しない）。SHOW_EXEC_INPUT（本人裁定 2026-10-08＝True）なら
    そのまま scrub＋clip（office_term の Bash 入力と同じ上限）、False なら名前だけ。
  - サブエージェント同士のやり取り（response_item.agent_message・SubAgentActivity・CollabAgentToolCall）は出さない
    （Claude 側のサブエージェントの方針に揃える＝呼び出し（spawn_agent）の行と結果だけ）。
  - カーソル（{off, h}・遡りの {off, h, i}）・shrink・public は office_term の形のまま（形式に依らない）。
"""
import json
import os
import re
import sqlite3
import sys
from contextlib import closing
from pathlib import Path

try:
    import office_term
    import source_codex
except ModuleNotFoundError:   # importlib でファイルを直に読む読み込み方（server/ が sys.path に無い）
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    try:
        import office_term
        import source_codex
    finally:
        del sys.path[0]

# 本人裁定（2026-10-08）: exec の input（JS のコード）はそのまま（伏字＋上限）。False＝道具の名前だけ。
SHOW_EXEC_INPUT = True

ROLLOUT_DIRS = (".codex/sessions", ".codex/archived_sessions")
FILES_MAX = 8                  # FileChange 1 件で出すファイルの上限（1 ファイル 1 行）
_PEEK = 2000                   # 出力の形を当てるために覗く先頭（終了コードの行・Script failed の行）＝素材の先切りではない
_PATCH_SCAN = 200_000          # apply_patch の本文から触ったファイルの名前を拾う範囲（本文そのものは載せない）
_AGENTS_MD = "# AGENTS.md instructions"
_SHELLS = ("bash", "zsh", "sh", "dash", "fish")
_SHELL_FUNCS = ("shell", "shell_command", "exec_command", "local_shell", "container.exec")
_EXIT_RE = re.compile(r"^Process exited with code (-?\d+)", re.M)
_HUNK_RE = re.compile(r"^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@")
_PATCH_FILE_RE = re.compile(r"^\*\*\* (?:Add|Update|Delete) File: (.+)$", re.M)
_TEXT_TYPES = ("input_text", "output_text", "text")
# サブエージェントを扱う道具（collaboration）＝結果の本文（サブエージェントの報告）は載せない（Claude 側の Task の結果と同じ扱い）
_AGENT_FUNCS = ("spawn_agent", "send_message", "followup_task", "wait_agent", "list_agents", "close_agent", "resume_agent")
_AGENT_OUT_KEYS = ("timed_out", "task_name", "agents", "submission_id")
_IMAGE_TYPES = ("input_image", "output_image", "image")
_EXEC_MCP_PREFIX = "exec-"     # exec（JS）の中から呼んだ McpToolCall の id の形（response_item に呼び出しが無い＝_exec_mcp）
NAME_MAX = 48                  # 道具の名前（n）の字数
MCP_PART_MAX = 40              # McpToolCall のサーバー名・道具名それぞれの字数（長いサーバー名でも道具名が見える）


# ── B11 安全な読み取り ─────────────────────────────────────────────────────────
def _home(home=None):
    return Path(home) if home is not None else Path(os.environ.get("OFFICE_HOME") or str(Path.home()))


def thread_id_of(session):
    """`cx-<thread_id>` → thread_id（形が違えば None）。source_codex が作る session と同じ規則で検算する。"""
    if not isinstance(session, str) or not session.startswith("cx-"):
        return None
    if not source_codex._SESSION_RE.fullmatch(session) or len(session) <= 3:
        return None
    return session[3:]


def _lookup(home, thread_id, trace=None):
    """SQLite（読み取り専用）から rollout_path の文字列だけを引く。無い・壊れている・ロック＝None。"""
    state = home / source_codex.STATE_FILE
    if not state.is_file():
        return None
    try:
        with closing(sqlite3.connect(state.resolve().as_uri() + "?mode=ro", uri=True, timeout=0.2)) as db:
            if trace is not None:
                db.set_trace_callback(trace)
            db.execute("PRAGMA query_only=1")
            row = db.execute("SELECT rollout_path FROM threads WHERE id = ?", (thread_id,)).fetchone()
    except (sqlite3.DatabaseError, OSError):
        return None
    return row[0] if row and isinstance(row[0], str) else None


def _confine(raw, home):
    """rollout_path → 閉じ込めの中の実ファイル（resolve 済み）。外・名前違い・通常ファイルでない＝None。"""
    if not isinstance(raw, str) or not raw or "\x00" in raw:
        return None
    p = Path(raw)
    if not p.is_absolute():
        return None
    try:
        real = p.resolve(strict=True)
    except (OSError, RuntimeError):
        return None
    if not (real.name.startswith("rollout-") and real.suffix == ".jsonl" and real.is_file()):
        return None
    for root in _roots(home):
        if real != root and real.is_relative_to(root):
            return real
    return None


def _roots(home):
    """許可の根（~/.codex/sessions・archived_sessions）の実体。R113-B-S3: 根そのもの（と ~/.codex）がリンクなら使わない＝安全側。
    根を resolve した実体だけで比べると、根が外（鍵の置き場・/ など）を指すリンクのとき、その先の rollout-*.jsonl を「配下」と
    誤って読む。だから「ホームの実体＋sub」と「根を resolve した実体」が一致する根だけを使う（ホームそのもののリンクは許す）。"""
    try:
        base = home.resolve(strict=True)
    except (OSError, RuntimeError):
        return []
    out = []
    for sub in ROLLOUT_DIRS:
        try:
            root = (home / sub).resolve(strict=True)
        except (OSError, RuntimeError):
            continue
        if root == base / sub and root.is_dir():
            out.append(root)
    return out


def rollout_path(session, home=None, trace=None):
    """cx- の session → 閉じ込めを通った rollout の実パス（読めないなら None）。trace は SQLite の set_trace_callback（テスト用）。"""
    thread_id = thread_id_of(session)
    if thread_id is None:
        return None
    home = _home(home)
    return _confine(_lookup(home, thread_id, trace), home)


def term_page(session, after, scrub, before=None, home=None):
    """office_term.session_page の cx- の分岐の先。頁の形は office_term.read_page / read_before と同じ。
    rollout が引けない・閉じ込めの外＝FileNotFoundError（OSError＝呼び出し側の notfound）。遡りのカーソルが合わない＝None。"""
    path = rollout_path(session, home)
    if path is None:
        raise FileNotFoundError("codex rollout")
    if before is not None and after is None:
        return read_before(path, before, scrub)
    return read_page(path, after, scrub)


# ── B12 行への変換（純関数） ──────────────────────────────────────────────────
def _payload(d):
    p = d.get("payload")
    return p if isinstance(p, dict) else {}


def _texts(content):
    """content の部品 → (本文の並び, 画像を含むか)。部品でないもの・知らない種類は飛ばす。"""
    out, img = [], False
    for x in content if isinstance(content, list) else []:
        if not isinstance(x, dict):
            continue
        ty = x.get("type")
        if ty in _TEXT_TYPES and isinstance(x.get("text"), str):
            out.append(x["text"])
        elif ty in _IMAGE_TYPES:
            img = True
    return out, img


def _user_text(content):
    keep = []
    for t in _texts(content)[0]:
        s = t.strip()
        if s and not s.startswith("<") and not s.startswith(_AGENTS_MD):
            keep.append(s)
    return "\n".join(keep)


def _args(raw):
    if isinstance(raw, dict):
        return raw
    if not isinstance(raw, str) or not raw.strip():
        return {}
    try:
        v = json.loads(raw)
    except (ValueError, RecursionError):
        return {}
    return v if isinstance(v, dict) else {}


def _cmd_text(cmd):
    """[zsh, -lc, 本文] → 本文（シェルに渡した文字列）。それ以外の argv は空白でつなぐ。"""
    if isinstance(cmd, list):
        parts = [str(x) for x in cmd]
        if len(parts) >= 3 and parts[1] in ("-lc", "-c") and os.path.basename(parts[0]) in _SHELLS:
            return parts[2]
        return " ".join(parts)
    return str(cmd if cmd is not None else "")


def _label(text, scrub, n=NAME_MAX):
    """道具の名前（n）＝伏字を名前の全体に掛けてから n 字・1 行（R113-B-S4: 先に [:48] で切ると秘密の断片が残った・伏字も無かった）。
    名前は区切りの無い 1 語が多い（mcp__server__tool）＝語の途中でも切る（伏字の後なので秘密の断片にはならない）。"""
    return office_term.clip(office_term._scrub(scrub, str(text if text is not None else "")), n, 1)


def _mcp_name(item, scrub):
    """McpToolCall → 「サーバー.道具」。伏字は**つないだ全体**に掛けてから切る（部品の境目をまたぐ秘密も伏字の型に当てる）。
    全体に伏字の当たりが無ければ部品ごとに MCP_PART_MAX 字（伏字を通した後の素材を切るだけ）、当たりがあれば伏字の後の全体を切る。"""
    parts = [x for x in (item.get("server"), item.get("tool")) if isinstance(x, str) and x]
    raw = ".".join(parts)
    whole = office_term._scrub(scrub, raw)
    if whole == raw:
        whole = ".".join(office_term.clip(x, MCP_PART_MAX, 1) for x in parts)
    return office_term.clip(whole, NAME_MAX, 1) or "mcp"


def _short(text, scrub, n=80):
    """短い説明（Todo の項目・サブエージェントの名前・js の題）＝office_term の Task の説明と同じ作り方（80 字・1 行）。"""
    return office_term._fit(scrub, text, n, 1)


def _name(path, scrub):
    """パス → 伏字を通した basename（字数ではまだ切らない）。R113-B-S3: 伏字は**パスの全体**に掛けてから basename＝切る
    （先に basename で区切ると、`/` を含む秘密（Bearer の値など）の後ろ半分が型に当たらない断片として残る）。"""
    t = office_term._scrub(scrub, str(path if path is not None else "").strip().rstrip("/"))
    return os.path.basename(t.rstrip("/"))


def _base(path, scrub):
    """パス → basename（Codex の行はプロジェクトからの相対にしない＝C の決め）。伏字→basename→PATH_MAX 字（_fit）。"""
    return office_term._fit(scrub, _name(path, scrub), office_term.PATH_MAX, 1)


def _patch_files(patch, scrub):
    """apply_patch の本文 → 触ったファイルの basename（本文の差分は載せない＝差分は FileChange の行）。
    R113-B-S3: 本文を _PATCH_SCAN で先に切らない（見出しの行の途中で切ると、名前の末尾の秘密が型に当たらない断片で残る）。
    拾う範囲は「見出しが _PATCH_SCAN までに始まるもの」で区切り、見出しの行そのものは全部を伏字に通してから basename・_fit で切る。"""
    seen = []
    for m in _PATCH_FILE_RE.finditer(str(patch or "")):
        if m.start() >= _PATCH_SCAN:
            break
        b = _name(m.group(1), scrub)
        if b and b not in seen:
            seen.append(b)
    return office_term._fit(scrub, ", ".join(seen), office_term.PATH_MAX, 1)


def _todo(args, scrub):
    items = []
    for t in args.get("plan") if isinstance(args.get("plan"), list) else []:
        if not isinstance(t, dict):
            continue
        st = {"completed": "c", "in_progress": "i"}.get(str(t.get("status") or ""), "p")
        items.append([st, _short(t.get("step") or t.get("content") or "", scrub)])
        if len(items) >= office_term.TODO_MAX:
            break
    return items


def _call_row(pt, name, pl, scrub, show_exec):
    """function_call／custom_tool_call → 道具の行（update_plan は todo の行）。引数は名前ごとに決めたものだけ・伏字を通す。"""
    if not name:
        return None                                       # 名前の無い呼び出し＝壊れた形（推測しない）
    raw = pl.get("input") if pt == "custom_tool_call" else pl.get("arguments")
    args = _args(raw) if pt == "function_call" else {}
    if name in _SHELL_FUNCS:
        return {"k": "tool", "n": "Shell", "t": office_term.cmd_text(_cmd_text(args.get("command", args.get("cmd"))), scrub)}
    if name == "apply_patch":
        patch = raw if pt == "custom_tool_call" else (args.get("input") or args.get("patch") or "")
        return {"k": "tool", "n": "apply_patch", "t": _patch_files(patch, scrub)}
    if name == "exec" and pt == "custom_tool_call":
        # B13: JS のコードは解釈しない（字面のまま伏字＋上限＝Bash の入力と同じ）。裁定で False にすると名前だけ
        return {"k": "tool", "n": "exec", "t": office_term.cmd_text(raw, scrub) if show_exec else ""}
    if name == "update_plan":
        items = _todo(args, scrub)
        return {"k": "todo", "i": items, "t": "Todo を更新"} if items else None
    if name == "spawn_agent":
        return {"k": "tool", "n": "spawn_agent", "t": _short(args.get("task_name"), scrub)}
    if name == "js":
        return {"k": "tool", "n": "js", "t": _short(args.get("title"), scrub)}
    if name == "view_image":
        return {"k": "tool", "n": "view_image", "t": _base(args.get("path"), scrub)}
    return {"k": "tool", "n": _label(name, scrub), "t": ""}   # 知らない道具は名前だけ（引数は載せない・名前も伏字→切る）


def _res_text(text, err, scrub, shell=False):
    """結果の本文の行＝office_term.out_text（Claude の Bash／その他の結果と同じ上限・失敗はサンドイッチ）。
    shell＝命令（Shell・exec・CommandExecution）の出力。"""
    text = str(text if text is not None else "")
    row = {"k": "res", "nl": office_term._nlines(text)}
    if err:
        row["err"] = 1
    row["t"] = office_term.out_text(text, scrub, err=err, shell=shell) or ("（失敗）" if err else "（出力なし）")
    return row


def _agent_row(name, j):
    """サブエージェントの道具の結果 → 要約だけ（報告の本文は載せない）。"""
    if name == "spawn_agent" or (name is None and "task_name" in j):
        return {"k": "res", "st": "bg", "t": "裏で動いています（サブエージェント）"}
    if j.get("timed_out") is True:
        return {"k": "res", "t": "待ち時間切れ"}
    return {"k": "res", "t": "完了"}


def _output_row(pl, scrub, name=None):
    """*_output → 結果の行。出力は版で形が違う（文字列・{output, metadata}・部品の並び・{content, success}）。
    name＝同じ窓で見えた呼び出しの名前（窓の外なら None＝形から当てる）。"""
    o = pl.get("output")
    text, img, err = "", False, False
    shell = name in _SHELL_FUNCS or name == "exec"        # 命令の出力＝Bash の出力と同じ上限（名前が窓の外なら形から当てる）
    j = _args(o) if isinstance(o, str) and o.lstrip().startswith("{") else {}
    if name in _AGENT_FUNCS or (name is None and any(k in j for k in _AGENT_OUT_KEYS)):
        return _agent_row(name, j)
    if isinstance(o, str):
        text = o
        if isinstance(j.get("output"), str):
            text = j["output"]
            md = j.get("metadata")
            code = md.get("exit_code") if isinstance(md, dict) else None
            err = isinstance(code, int) and not isinstance(code, bool) and code != 0
            shell = shell or (name is None and isinstance(md, dict) and "exit_code" in md)
        elif isinstance(j.get("message"), str):
            text = j["message"]
        else:
            m = _EXIT_RE.search(text[:_PEEK])               # exec_command（0.141〜）の文字列の出力だけ
            err = bool(m and m.group(1) != "0")
            shell = shell or (name is None and m is not None)
    elif isinstance(o, list):
        parts, img = _texts(o)
        text = "\n".join(parts)
    elif isinstance(o, dict):
        c = o.get("content")
        text = c if isinstance(c, str) else "\n".join(_texts(c)[0])
        err = o.get("success") is False
    head = text.lstrip()[:200]
    if head.startswith(("Script failed", "Script error")):
        err = True                                        # exec（0.144〜）の 1 行目（中で動いた命令の終了コードは見ない）
    shell = shell or (name is None and head.startswith(("Script failed", "Script error", "Script completed")))
    if img and not text.strip():
        row = {"k": "res", "img": 1, "t": "画像を読み込み"}
        if err:
            row["err"] = 1
        return row
    return _res_text(text, err, scrub, shell=shell)


def _hunks(unified):
    """unified diff の文字列 → office_term._diff_rows が読む hunk の並び（{oldStart,newStart,lines}）。"""
    out, cur = [], None
    for line in str(unified or "").split("\n"):
        m = _HUNK_RE.match(line)
        if m:
            cur = {"oldStart": int(m.group(1)), "newStart": int(m.group(2)), "lines": []}
            out.append(cur)
            continue
        if cur is None or not line or line.startswith("\\"):
            continue                                       # 先頭の ---/+++・「\ No newline at end of file」
        cur["lines"].append(line)
    return out


def _file_rows(item, scrub):
    """FileChange → ファイルごとの結果の行（差分は office_term._diff_rows の型・本文は伏字を通す）。"""
    rows = []
    changes = item.get("changes")
    failed = item.get("status") == "failed"
    for path, ch in list(changes.items())[:FILES_MAX] if isinstance(changes, dict) else []:
        ch = ch if isinstance(ch, dict) else {}
        base = _base(path, scrub)
        row = {"k": "res", "f": base}
        if failed:
            row["err"] = 1
        ty = ch.get("type")
        if ty == "add" or (ty is None and isinstance(ch.get("content"), str)):
            body = str(ch.get("content") or "")
            row.update(wl=office_term._nlines(body),         # 新規作成＝Claude の Write と同じ（先頭 WRITE_HEAD 行・DIFF_COL 字）
                       d=[["+", i + 1, office_term._diff_body(x, scrub)]
                          for i, x in enumerate(body.split("\n")[:office_term.WRITE_HEAD])])
            row["t"] = "%s に %d 行を書き込み" % (base, row["wl"])
        elif ty == "delete":
            row["t"] = "%s を削除" % base
        else:
            d, add, rem = office_term._diff_rows(_hunks(ch.get("unified_diff")), scrub)
            row.update(add=add, rem=rem, d=d, t="%s を更新（+%d −%d）" % (base, add, rem))
        rows.append(row)
    return rows


def _command_rows(item, scrub):
    """CommandExecution → 道具の行（シェルに渡した文字列）＋結果の行（出力・終了コード）。"""
    tid = office_term._tid(item.get("id"))
    tool = {"k": "tool", "n": "Shell", "t": office_term.cmd_text(_cmd_text(item.get("command")), scrub)}
    out = item.get("aggregated_output")
    if not isinstance(out, str):
        so, se = item.get("stdout"), item.get("stderr")
        so, se = (so if isinstance(so, str) else ""), (se if isinstance(se, str) else "")
        out = so + ("\n" + se if so and se else se)
    code = item.get("exit_code")
    err = (isinstance(code, int) and not isinstance(code, bool) and code != 0) or item.get("status") in ("failed", "declined")
    res = _res_text(out, err, scrub, shell=True)
    if tid:
        tool["id"], res["for"] = tid, tid
    return [tool, res]


def _mcp_rows(item, scrub):
    """McpToolCall → 道具の行（サーバー.道具の名前だけ・引数は載せない）＋結果の行。"""
    name = _mcp_name(item, scrub)
    tid = office_term._tid(item.get("id"))
    tool = {"k": "tool", "n": name, "t": ""}
    result = item.get("result")
    err = item.get("status") == "failed"
    img = False
    if isinstance(result, dict):
        parts, img = _texts(result.get("content"))
        text = "\n".join(parts)
        err = err or result.get("isError") is True
    else:
        text = result if isinstance(result, str) else ""
    if img and not text.strip():
        res = {"k": "res", "img": 1, "t": "画像を読み込み"}
        if err:
            res["err"] = 1
    else:
        res = _res_text(text, err, scrub)
    if tid:
        tool["id"], res["for"] = tid, tid
    return [tool, res]


def _exec_mcp(item):
    """McpToolCall が exec（JS）の中から呼ばれたもの＝response_item に呼び出しの無いものか。行 1 本だけで決まる（窓に依らない）。
    実測（2026-10-08・手元の rollout 5,611 本）: 写しでない 742 件は全部 id が `exec-`、function_call の写し 660 件は全部 `call_`
    （＝同じ call_id）。知らない形は出さない側へ倒す（二重に出すより、呼び出しの行だけが残る方が害が小さい）。"""
    return str(item.get("id") or "").startswith(_EXEC_MCP_PREFIX)


def rows_from_codex_lines(lines, scrub, line_index=False, show_exec_input=None):
    """rollout の行（str か bytes）→ 端末の行 [{"k": 種類, …}]（時系列・種類は office_term.KINDS の中だけ）。
    壊れた行・知らない type/payload は黙って飛ばす。show_exec_input＝None なら SHOW_EXEC_INPUT（モジュールの定数）。"""
    show_exec = SHOW_EXEC_INPUT if show_exec_input is None else bool(show_exec_input)
    rows = []
    names = {}                                            # call_id → 道具の名前（結果の読み方だけ・窓の外なら形から当てる）
    for li, ln in enumerate(lines):
        try:
            d = json.loads(ln)
        except (ValueError, TypeError, RecursionError):
            continue
        if not isinstance(d, dict):
            continue
        ts = office_term._ts(d)

        def add(r, li=li):
            if ts is not None:
                r["ts"] = ts
            if line_index:
                r["_l"] = li                              # 元の行（遡りのカーソル用・封じる前に外す）
            rows.append(r)

        typ, pl = d.get("type"), _payload(d)
        pt = pl.get("type")
        if typ == "response_item":
            if pt == "message":
                role = pl.get("role")
                if role == "user":
                    t = _user_text(pl.get("content"))
                    if t:
                        add({"k": "u", "t": office_term.clip(t, office_term.USER_MAX, office_term.USER_LINES)})
                elif role == "assistant":
                    t = "\n".join(_texts(pl.get("content"))[0])
                    if t.strip():
                        add({"k": "a", "t": office_term.clip(t, office_term.TEXT_MAX, office_term.TEXT_LINES)})
            elif pt == "reasoning":
                if rows and rows[-1].get("k") == "th":
                    rows[-1]["n"] = int(rows[-1].get("n") or 1) + 1      # 続いた考えは件数だけ足す（本文は載せない）
                else:
                    add({"k": "th", "n": 1, "t": "考えました"})
            elif pt in ("function_call", "custom_tool_call"):
                name = str(pl.get("name") or "")             # 振り分けと結果の読み方は素の名前（載せるのは _label を通した名前だけ）
                call_id = str(pl.get("call_id") or "")
                if call_id:
                    names[call_id] = name
                r = _call_row(pt, name, pl, scrub, show_exec)
                if r is None:
                    continue
                tid = office_term._tid(call_id)
                if tid and r["k"] == "tool":
                    r["id"] = tid
                add(r)
            elif pt in ("function_call_output", "custom_tool_call_output"):
                call_id = str(pl.get("call_id") or "")
                if names.get(call_id) == "update_plan":
                    continue                              # Todo は呼び出しの側（todo の行）で出す
                r = _output_row(pl, scrub, names.get(call_id))
                fid = office_term._tid(call_id)
                if fid:
                    r["for"] = fid
                add(r)
            elif pt == "web_search_call":
                add({"k": "tool", "n": "WebSearch", "t": ""})   # 検索語は載せない（Claude 側の WebSearch と同じ）
            continue
        if typ == "event_msg":
            if pt == "item_completed":
                item = pl.get("item")
                if not isinstance(item, dict):
                    continue
                ity = item.get("type")
                if ity == "CommandExecution":
                    for r in _command_rows(item, scrub):
                        add(r)
                elif ity == "FileChange":
                    for r in _file_rows(item, scrub):
                        add(r)
                elif ity == "McpToolCall":
                    # R113-B-S3: 写しかどうかを窓の中の names で決めない（窓をまたぐと二重に出た）。id の形だけで決める＝状態なし:
                    # exec の中から呼んだもの（id が exec-…）だけを出す。それ以外（function_call の call_id と同じ call_…・知らない形）は
                    # response_item の呼び出しと結果の行で足りる＝出さない（CommandExecution／FileChange と同じく response_item に無いものだけ）
                    if not _exec_mcp(item):
                        continue
                    for r in _mcp_rows(item, scrub):
                        add(r)
                # AgentMessage・UserMessage・Reasoning は response_item の写し／SubAgentActivity 等は出さない
            elif pt == "task_complete":
                ms = pl.get("duration_ms")
                if isinstance(ms, int) and not isinstance(ms, bool) and ms >= 0:
                    add({"k": "turn", "ms": ms, "t": "%d秒 作業" % (ms // 1000)})
            elif pt == "turn_aborted":
                add({"k": "int", "t": "中断されました"})
            continue
        if typ == "compacted":
            add({"k": "sys", "t": "会話を圧縮しました"})
    return rows


# ── カーソル（office_term と同じ形・同じ検算。行の作り方だけが違う） ───────────────────
def read_page(path, after, scrub):
    """{"rows", "cur", "reset", "gap", …}。path は rollout_path で閉じ込め済み。形と検算は office_term.read_page と同じ。"""
    after = office_term.valid_after(after)
    with open(path, "rb") as f:
        size = os.fstat(f.fileno()).st_size
        if after and after["off"] <= size and size - after["off"] <= office_term.DELTA_MAX \
                and office_term._hash_before(f, after["off"]) == after["h"]:
            f.seek(after["off"])
            data = f.read(size - after["off"])
            cut = data.rfind(b"\n")
            if cut < 0:                                   # 新しい完全な行が無い（書きかけだけ）
                return {"rows": [], "cur": after, "reset": False, "gap": False}
            lines = data[:cut].split(b"\n")
            rows = rows_from_codex_lines(lines, scrub)
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
    """office_term._with_offsets と同じ（行の作り方だけ rows_from_codex_lines）。"""
    starts, o = [], base
    for ln in lines:
        starts.append(o)
        o += len(ln) + 1
    rows = rows_from_codex_lines(lines, scrub, line_index=True)
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
    with open(path, "rb") as f:
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
