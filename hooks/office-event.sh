#!/bin/bash
# R90-D4: Claude Code の hook イベントを ~/.claude/office_events/<YYYY-MM-DD>.jsonl へ**記録するだけ**の hook。
#
# なぜ: AI Office はこれまで transcript を2〜3秒おきに読み直して状態を推測していた。Claude Code の hooks は
#   SessionStart / UserPromptSubmit / PreToolUse / PostToolUse / Stop / SubagentStart / SubagentStop /
#   TaskCreated / TaskCompleted / Notification / SessionEnd … と増え、"async": true で**ターンを1ミリ秒も
#   止めずに**呼べる。ここで1行落としておけば office_server が tail して即時に状態を更新し、タイムライン
#   （留守中ダイジェスト・XP）の材料にもなる。
#
# 掟（office-inbox-wait.sh / office-statusline-capture.sh と同じ）:
#   - どんな失敗でも無出力 exit 0（セッションを絶対に邪魔しない・stderr も沈黙）
#   - python3 起動は1回だけ（毎ツール呼び出しで走るので軽く保つ。実測 <150ms が受入条件）
#   - **本文を1バイトも書かない**: prompt 本文・コマンド文字列・tool_response・transcript_path は読まない。
#     書くのは種別（hook 名・ツール名）と、ファイル名の basename・スキル名・エージェント種別・
#     「git commit/push だったか」の分類・既知の分類値・件数・長さだけ（タスク件名や reason の自由記述も書かない）。allowlist 抽出は**この hook 側**で行う
#     （サーバー側の redaction に頼らない＝ファイルに本文が存在しない）
#   - 1行 ≤ 1500 バイト・O_APPEND で単一 write（APFS の append 原子性の実用範囲）・dir 0700 / file 0600
#   - 心拍（pidfile）や inbox には一切触れない（配達の不変条件は office-inbox-wait.sh が持つ）
#
# OFFICE_HOME はテスト注入口（既定 ${HOME}）。
set +e
H="${OFFICE_HOME:-$HOME}"
# R97-A: python の解決を 1 本化する（従来は /usr/bin/python3 固定・/usr/bin/env python3・python3 の 3 通り混在）。
# `/usr/bin/python3` は Xcode CLT が無い Mac では実体が無く、この hook は**無出力 exit 0** で終わっていた
# ＝「❗は出るのに答えても届かない」がログも無しに起きる（2026-09-17 の棚卸し）。
# 探索結果はキャッシュする（office-event.sh は毎ツール呼び出しで走る＝python の起動を増やさない）。
OFFICE_PY=""
OFFICE_PY_CACHE="${OFFICE_HOME:-$HOME}/.claude/.office_python"
if [ -s "$OFFICE_PY_CACHE" ]; then
  OFFICE_PY=$(cat "$OFFICE_PY_CACHE" 2>/dev/null || true)
  [ -n "$OFFICE_PY" ] && [ -x "$OFFICE_PY" ] || OFFICE_PY=""
fi
if [ -z "$OFFICE_PY" ]; then
  for OFFICE_PY_CAND in /usr/bin/python3 "$(command -v python3 2>/dev/null || true)"; do
    [ -n "$OFFICE_PY_CAND" ] && [ -x "$OFFICE_PY_CAND" ] || continue
    "$OFFICE_PY_CAND" -c 'import json,sys' >/dev/null 2>&1 || continue
    OFFICE_PY="$OFFICE_PY_CAND"
    mkdir -p "${OFFICE_PY_CACHE%/*}" 2>/dev/null || true
    printf '%s' "$OFFICE_PY" 2>/dev/null > "$OFFICE_PY_CACHE" || true
    break
  done
fi
if [ -z "$OFFICE_PY" ]; then
  # 黙って消えない。オフィス側が「なぜ届かないか」を言えるように 1 行だけ残す（本文は書かない）。
  OFFICE_EVD="${OFFICE_HOME:-$HOME}/.claude/office_events"
  mkdir -p "$OFFICE_EVD" 2>/dev/null || true
  { printf '{"t":%s,"kind":"hook_no_python"}\n' "$(date +%s 2>/dev/null || echo 0)" >> "$OFFICE_EVD/hook_errors.jsonl"; } 2>/dev/null || true
  exit 0
fi
# payload は python が stdin から直接読む（環境変数経由だと大きな tool_response で ARG_MAX を超え、
# python が起動できずイベントが落ちる＝Astra レビュー指摘）。プログラム本体は -c で渡す。
read -r -d '' PY <<'PYEOF'
import datetime
import hashlib
import json
import os
import re
import sys
import time

MAX_LINE = 1500
try:
    d = json.load(sys.stdin)
except Exception:
    sys.exit(0)
if not isinstance(d, dict):
    sys.exit(0)
sid = str(d.get("session_id") or "")
if not sid or len(sid) > 64 or any(
        c not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_" for c in sid):
    sys.exit(0)
ev = str(d.get("hook_event_name") or "")
if not ev or len(ev) > 40 or not re.fullmatch(r"[A-Za-z]+", ev):
    sys.exit(0)


def clip(s, n):
    s = " ".join(str(s or "").split())
    return s if len(s) <= n else s[: n - 1] + "…"


ti = d.get("tool_input") if isinstance(d.get("tool_input"), dict) else {}
# R98: バックグラウンド実行（run_in_background）は**起動しただけ**で PostToolUse が返る＝
# git commit もテストも「通った」と言えない。分類そのものを付けない（台帳は「—」）。
tr = d.get("tool_response") if isinstance(d.get("tool_response"), dict) else {}
background = bool(ti.get("run_in_background") or ti.get("background") or d.get("run_in_background")
                  # 途中でバックグラウンドへ移ると入力に印は無く、応答に id が返る＝まだ終わっていない
                  or tr.get("backgroundTaskId") or tr.get("background_task_id") or tr.get("taskId"))
tool = clip(d.get("tool_name"), 60)
tgt = ""
if tool in ("Edit", "Write", "Read", "NotebookEdit", "MultiEdit"):
    tgt = os.path.basename(str(ti.get("file_path") or ti.get("notebook_path") or ""))
elif tool == "Skill":
    tgt = clip(ti.get("skill"), 40)
elif tool in ("Agent", "Task"):
    tgt = clip(ti.get("subagent_type") or ti.get("agent_type"), 40)
elif tool in ("Glob", "Grep"):
    tgt = ""                                   # パターンは検索語＝本文扱い。書かない
kind = ""
if tool == "Bash" and not background:
    cmd = str(ti.get("command") or "")
    # R98: Bash の分類は **git も test も同じ 1 本の判定**を通す（分類だけ・コマンド本文は捨てる）。
    # 台帳の証拠列（committed / tested / failed）はこの kind だけを根拠にするので、
    # 「実行したのか」「終了コードは誰のものか」を構造的に確かめてからでないと分類しない。
    #   ① 名前に触れただけを実行と誤らない（`cat verify.sh`・`echo 'git commit'`・引用やコメントの中）
    #   ② 終了コードがそのコマンドのものでない形は証拠にしない
    #      （`| tail`・`|| true`・`; echo`・`&`・ヒアドキュメント・`$( )`・run_in_background）
    # 分からないときは何も言わない（台帳は「—」）＝失敗を成功で上書きしない。
    PREFIX = {"sudo", "time", "nice", "env", "npx", "bunx", "command", "exec"}
    RUNNER = {"uv", "poetry", "pipenv", "rye", "pdm"}       # `uv run pytest` の形
    GIT_OPT_VALUE = {"-C", "-c", "--git-dir", "--work-tree", "--namespace", "--exec-path"}
    # git commit の**暗黙の dry-run**（表示するだけでコミットしない）
    GIT_NO_RUN = {"--short", "--porcelain", "--long", "--null", "-z", "--dry-run"}
    # サブコマンドを実行せず表示だけして終わる git の大域オプション
    GIT_INFO_ONLY = {"--version", "--help", "-h", "--html-path", "--man-path", "--info-path",
                     "--exec-path"}

    def _split(text):
        """引用・コメント・置換を尊重して**トークン**へ割る。

        返り値= ([セグメント], 証拠にできない形か)。セグメントは [(トークン, 引用を含むか), …]。
        引用のトークン境界を保つのが肝: `TEST_CMD='npx vitest run'` は**代入 1 個**であって
        vitest の実行ではない（引用を剥がして空白で割ると実行と誤る・別モデルレビュー）。
        証拠にできない形= `||`／単独の `&`／ヒアドキュメント／コマンド置換。"""
        segs, seg, tok, quote, esc, unsafe = [], [], [], "", False, False
        tok_quoted = False
        had_and = had_semi = False                           # `&&`（短絡）／`;`（順次）が在ったか
        depth = 0
        started = False                                      # 引用で始まった空トークンも 1 個と数える

        def end_tok():
            nonlocal tok, tok_quoted, started
            if tok or started:
                seg.append(("".join(tok), tok_quoted))
            tok, tok_quoted, started = [], False, False

        def end_seg():
            nonlocal seg
            end_tok()
            if seg:
                segs.append(seg)
            seg = []

        i = 0
        while i < len(text):
            c = text[i]
            if esc:
                tok.append(c); esc = False; started = True; i += 1; continue
            if c == "\\" and quote != "'":
                esc = True; i += 1; continue
            if quote:
                if c == quote:
                    quote = ""
                else:
                    # 二重引用符の中でも `$( … )` と `` ` `` は展開される＝置換として扱う
                    if quote == '"' and (text[i:i + 2] == "$(" or c == "`"):
                        unsafe = True
                    tok.append(c)
                i += 1; continue
            if c in "'\"":
                quote = c; tok_quoted = True; started = True; i += 1; continue
            if c == "#" and not depth and not tok and not started:
                nl = text.find("\n", i)
                i = len(text) if nl < 0 else nl
                continue
            if c == "$" and text[i:i + 2] == "$(":
                unsafe = True; depth += 1; tok.append(text[i:i + 2]); started = True; i += 2; continue
            if c == "`":
                unsafe = True
                depth += 1 if depth == 0 else -1
                tok.append(c); started = True; i += 1; continue
            if c == ")" and depth:
                depth -= 1; tok.append(c); i += 1; continue
            if depth:
                tok.append(c); i += 1; continue
            if c == "<" and text[i:i + 2] == "<<":
                unsafe = True
                break
            if c in "<>" and text[i + 1:i + 2] == "(":
                unsafe = True                                # プロセス置換 <( … ) / >( … )
                break
            if c in " \t":
                end_tok(); i += 1; continue
            if c == "\n" or c == ";":
                had_semi = True; end_seg(); i += 1; continue
            if c == "|":
                if text[i:i + 2] == "||":
                    unsafe = True; i += 2
                else:
                    i += 1
                end_seg(); continue
            if c == "&":
                if text[i:i + 2] == "&&":
                    had_and = True; end_seg(); i += 2; continue
                prev = "".join(tok).rstrip()
                if prev.endswith(">") or prev.endswith("<"):
                    tok.append(c); i += 1; continue
                unsafe = True
                end_seg(); i += 1; continue
            if c == "(" and (tok or started):
                unsafe = True                                # `pytest() ( … )` ＝関数定義（実行ではない）
                i += 1; continue
            if c in "()":
                end_tok(); i += 1; continue                  # サブシェルの括弧（中身が終了コードを決める）
            tok.append(c); started = True; i += 1
        end_seg()
        return segs, unsafe, had_and, had_semi

    # テストを**走らせない**オプション（版表示・ヘルプ・収集のみ・ビルドだけ）は証拠にしない
    NO_RUN = {"--version", "-V", "--help", "-h", "--collect-only", "--co", "--no-run",
              "--fixtures", "--markers", "--showconfig", "--showConfig", "--show-config",
              "--dry-run", "--just-print", "--listTests", "--list-tests", "--list", "-list",
              "--setup-plan", "--setup-only", "--fixtures-per-test", "--trace-config",
              "--collectonly", "--collect_only",
              "--if-present",         # npm: スクリプトが無ければ何もせず 0 で終わる
              "--cache-show", "--cache-clear", "--clearCache", "--clear-cache"}

    def _make_no_run(args):
        """make は -n/-t/-q でレシピを実行せず、-i では失敗しても 0 を返す（どちらも証拠にしない）。"""
        for a in args:
            if a in ("--touch", "--question", "--just-print", "--dry-run", "--recon", "--ignore-errors"):
                return True
            if re.fullmatch(r"-[A-Za-z]+", a) and set("ntqi") & set(a[1:]):
                return True
        return False

    def _node_test(args, unquoted):
        for a in args:
            if not a.startswith("-"):
                return False                                 # ここからスクリプトの引数
            base = a.split("=", 1)[0]
            if base in ("-e", "--eval", "-p", "--print", "-c", "--check"):
                return False                                 # 評価コードを渡す形
            if a == "--" :
                return False
            if a == "--test" and a in unquoted:
                return True
        return False

    try:
        segs, unsafe, had_and, had_semi = _split(cmd)
        # 早期 return / exit がある＝最後のセグメントに辿り着いたとは限らない（`… exit 0; fi; pytest`）。
        # 制御構文（if/for/while/case/function）が混じるものも「最後が走った」と言えないので分類しない。
        # ★**許可リスト**で判定する（禁止リストだと `set -n`・`exec true`・新しい構文が出るたびに穴が開く）。
        # 先行セグメントに許すのは「実行の結果に影響しない支度」だけ。それ以外は分類しない。
        # 例外: 区切りが `&&` だけで終了 0 なら、先行が何であれ**最後のコマンドは走って成功した**。
        # 実体を隠す前置き（`command exec true` の `command`・`! …` の否定・`sudo`・`uv run`）は
        # 読み飛ばして**本当のコマンド名**を見る。exec だけは前置きでなく意味を持つので残す。
        LEAD_SKIP = (PREFIX | RUNNER | {"!"}) - {"exec"}

        def _head_token(seg):
            """そのセグメントのコマンド名（環境変数代入と前置きは読み飛ばす）。"""
            j = 0
            while j < len(seg):
                tok = seg[j][0]
                if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*=\S*", tok):
                    j += 1; continue
                base = tok.rsplit("/", 1)[-1]
                if base in LEAD_SKIP:
                    j += 1
                    if base in RUNNER and j < len(seg) and seg[j][0] == "run":
                        j += 1
                    continue
                return base
            return ""

        SAFE_LEAD = {"cd", "pushd", "popd", "export", "unset", "umask", "clear", "echo", "printf",
                     "mkdir", "touch", "true", ":", "date", "pwd", "git", "ls", "cat", "cp", "mv", "rm"}
        # 先行に来ても意味が読める（＝支度ではないが、走れば結果が後ろへ伝わる）コマンド
        KNOWN_RUNNERS = {"pytest", "py.test", "vitest", "jest", "mocha", "python", "python3",
                         "npm", "pnpm", "yarn", "bun", "node", "cargo", "go", "make",
                         "bash", "sh", "zsh", "verify.sh", "dev.sh"}
        ok_now = ev not in ("PostToolUseFailure", "StopFailure", "PermissionDenied")
        # 先行セグメントが「実行の結果に影響しない支度」なら最後のコマンドは走る。
        # そうでなくても、区切りが `&&` だけで終了 0 なら走って成功した——ただし**シェル自身の流れを
        # 変える組み込み**（exec/set/shopt/source/eval/exit・制御構文）が先行していたら成り立たない
        # （`exec true && pytest`・`set -n && pytest` は走らずに 0 で終わる）。
        BLOCKERS = {"exec", "set", "shopt", "source", ".", "eval", "exit", "return", "trap",
                    "if", "fi", "then", "else", "elif", "for", "while", "until", "do", "done",
                    "case", "esac"}
        # `! …` は終了コードを反転させる＝成功も失敗も本人のものではない
        if any(seg and seg[0][0] == "!" for seg in segs):
            segs = []
        # 先行セグメントの `export FOO=…` や代入も、後ろの実行を変える（`export MAKEFLAGS=-i; make test`）
        lead_env = []
        for seg in segs[:-1]:
            for tok, _q in seg:
                if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*=.*", tok, re.S):
                    lead_env.append(tok)                     # 値に空白があっても見る
        heads = [_head_token(seg) for seg in segs[:-1]]
        lead_safe = all(h in SAFE_LEAD for h in heads)
        # 到達保証の例外は「先行が全部わかっている支度」のときだけ。`builtin exit 0 && pytest` の
        # ように**読めない先行**は通さない（別モデルレビュー）。
        proven = (had_and and not had_semi and ok_now
                  and all(h in SAFE_LEAD or h in KNOWN_RUNNERS for h in heads))
        if len(segs) > 1 and not (lead_safe or proven):
            segs = []
        # `&&` が在る失敗は**誰の失敗か分からない**（`cd /なし && pytest` は pytest を走らせずに失敗）
        if segs and had_and and not ok_now:
            segs = []
        if segs and not unsafe:
            toks = segs[-1]                                  # 終了コードを決めるのは最後のセグメント
            i = 0
            # 先頭の環境変数代入。値の引用（`PYTEST_ADDOPTS="-q" pytest`）は消えて空白を含まない＝
            # ここで読み飛ばせる。`TEST_CMD='npx vitest run'` は空白を含む 1 トークンなので一致せず、
            # 「代入だけ」としてそのまま head になる＝実行と見なさない（別モデルレビュー）。
            env_no_run = False
            for tok in lead_env:                             # 先行 export/代入の効果も同じ規則で見る
                name, _, value = tok.partition("=")
                parts = [x for x in re.split(r"[\s,]+", value) if x]
                if {x.split("=", 1)[0] for x in parts} & NO_RUN:
                    env_no_run = True
                if name in ("MAKEFLAGS", "GNUMAKEFLAGS") and any(
                        re.fullmatch(r"-?[A-Za-z]+", x) and set("ntqi") & set(x.lstrip("-")) for x in parts):
                    env_no_run = True
            while i < len(toks) and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*=\S*", toks[i][0]):
                # `PYTEST_ADDOPTS="--collect-only" pytest`・`MAKEFLAGS=-n make test` のように、
                # 環境変数側へ非実行オプションを渡す形もある（別モデルレビュー）
                name, _, value = toks[i][0].partition("=")
                parts = [x for x in re.split(r"[\s,]+", value) if x]
                if {x.split("=", 1)[0] for x in parts} & NO_RUN:
                    env_no_run = True
                # 短縮の結合形（-n/-t/-q/-i）は make のときだけ（pytest の `-q` は普通の実行）
                if name in ("MAKEFLAGS", "GNUMAKEFLAGS") and any(
                        re.fullmatch(r"-?[A-Za-z]+", x) and set("ntqi") & set(x.lstrip("-")) for x in parts):
                    env_no_run = True
                i += 1
            while i < len(toks) and toks[i][0] in PREFIX | RUNNER:
                runner = toks[i][0] in RUNNER
                i += 1
                if runner and i < len(toks) and toks[i][0] == "run":
                    i += 1
            head = toks[i][0].rsplit("/", 1)[-1] if i < len(toks) else ""
            args = [t for t, _ in toks[i + 1:]]
            unquoted = {t for t, q in toks[i + 1:] if not q}
            first = args[0].rsplit("/", 1)[-1] if args else ""
            if head == "git" and not env_no_run and not ({a.split("=", 1)[0] for a in args} & NO_RUN):
                j = 0
                info_only = False
                while j < len(args) and args[j].startswith("-"):
                    if args[j].split("=", 1)[0] in GIT_INFO_ONLY:
                        info_only = True                     # ここで表示して終わる＝サブコマンドは走らない
                        break
                    j += 2 if args[j] in GIT_OPT_VALUE else 1
                sub = "" if info_only else (args[j] if j < len(args) else "")
                if sub in ("commit", "push") and not ({a.split("=", 1)[0] for a in args} & GIT_NO_RUN):
                    kind = "git:" + sub
            # 値つきの形（`--list=Test`・`--showConfig=true`）も同じ＝`=` の前で見る
            # 値の側に非実行オプションを書く形（`pytest -o addopts=--collect-only`）も見る
            elif (env_no_run or ({a.split("=", 1)[0] for a in args} & NO_RUN)
                    or any(x in NO_RUN for a in args for x in re.split(r"[\s,=]+", a) if x.startswith("-"))
                    or (head == "make" and _make_no_run(args))):
                kind = ""                                    # 走らせていない＝証拠にしない
            elif args[:1] == ["list"] and head in ("vitest", "jest", "mocha", "go", "cargo"):
                kind = ""                                    # 一覧を出すだけ
            elif (head in ("pytest", "py.test", "vitest", "jest", "mocha")
                    or (head in ("python", "python3") and args[:1] == ["-m"]
                        and args[1:2] and args[1] in ("pytest", "unittest"))
                    or (head in ("npm", "pnpm", "yarn", "bun")
                        and (args[:1] == ["test"] or args[:2] == ["run", "test"]))
                    # node: `--test` は**node 自身のオプション**のときだけ（スクリプトより前に在る）。
                    # `node app.js --test` はスクリプトの引数・`-e/--eval=…` は評価コード＝どちらも違う。
                    or (head == "node" and _node_test(args, unquoted))
                    or (head in ("cargo", "go", "make") and args[:1] == ["test"]
                        and not (head == "go" and any(a.split("=", 1)[0] in ("-c", "-o") for a in args)))
                    or head == "verify.sh"
                    or (head == "dev.sh" and "--check" in args)
                    or (head in ("bash", "sh", "zsh") and first == "verify.sh")
                    or (head in ("bash", "sh", "zsh") and first == "dev.sh" and "--check" in args)):
                kind = "test"
    except Exception:
        kind = ""                                            # 判定に失敗したら分類しない（hook は落ちない）
# 自由記述（task_input.subject/description・reason 等）は秘密やパスを含みうるので記録しない。
# src は Claude Code が使う既知の分類値だけ（それ以外は空）。
SRC_ALLOWED = {"startup", "resume", "clear", "compact", "logout", "prompt_input_exit", "other", "manual", "auto"}
src_raw = d.get("source") or d.get("reason") or d.get("end_reason") or d.get("trigger")
rec = {
    "v": 1,
    "ts": round(time.time(), 3),
    "ev": ev,
    "sid": sid,
    "cwdh": hashlib.sha1(str(d.get("cwd") or "").encode("utf-8")).hexdigest()[:12],
    "tool": tool,
    "tgt": clip(tgt, 80),
    "kind": kind,
    "nt": clip(d.get("notification_type"), 40),
    "sub": clip(d.get("agent_id"), 40) if ev.startswith("Subagent") else "",
    "task": clip(d.get("task_id"), 40),
    "ok": ev not in ("PostToolUseFailure", "StopFailure", "PermissionDenied"),
    "src": src_raw if isinstance(src_raw, str) and src_raw in SRC_ALLOWED else "",
    "plen": len(str(d.get("prompt_text") or d.get("prompt") or "")) if ev == "UserPromptSubmit" else 0,
}
line = json.dumps(rec, ensure_ascii=False, separators=(",", ":")) + "\n"
for drop in ("tgt", "nt", "sub"):          # 長すぎたら情報の薄い順に落として 1500B に収める
    if len(line.encode("utf-8")) <= MAX_LINE:
        break
    rec[drop] = ""
    line = json.dumps(rec, ensure_ascii=False, separators=(",", ":")) + "\n"
if len(line.encode("utf-8")) > MAX_LINE:
    sys.exit(0)

home = os.environ.get("OFFICE_HOME") or os.path.expanduser("~")
dirp = os.path.join(home, ".claude", "office_events")
try:
    os.makedirs(dirp, mode=0o700, exist_ok=True)
    try:
        os.chmod(dirp, 0o700)
    except OSError:
        pass
    day = datetime.date.today().isoformat()
    fd = os.open(os.path.join(dirp, day + ".jsonl"), os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
    try:
        os.write(fd, line.encode("utf-8"))
    finally:
        os.close(fd)
except Exception:
    pass
PYEOF
OFFICE_HOME="$H" "$OFFICE_PY" -c "$PY" >/dev/null 2>&1
exit 0
