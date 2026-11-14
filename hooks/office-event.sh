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
#   - **本文を1バイトも書かない**: prompt 本文・コマンド文字列・transcript_path は読まない。
#     書くのは種別（hook 名・ツール名）と、ファイル名の basename・スキル名・エージェント種別・
#     「git commit/push だったか」の分類・既知の分類値・件数・長さだけ（タスク件名や reason の自由記述も書かない）。allowlist 抽出は**この hook 側**で行う
#     （サーバー側の redaction に頼らない＝ファイルに本文が存在しない）。
#     R106（2026-10-03）: Bash の `tool_response.stdout`/`stderr`（コマンドの生出力＝本文）は、
#     commit の有無を確かめる git 問い合わせと、test のランナー自身の要約行（`N passed`／`FAILED (` 等・
#     .claude/rules/hooks.md の対応表）の有無だけを見るために**一時的にメモリへ読む**。読んだ本文は
#     正規表現の真偽判定にしか使わず、書き出す・ディスクに残すのは種別（`kind`）と真偽（`ok`）の値だけ
#     （本文そのもの・部分文字列・行番号は一切書かない＝上の「本文を1バイトも書かない」はファイルに
#     書く内容についての契約であり、ここで破れていない）。
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
    "$OFFICE_PY_CAND" -I -c 'import json,sys' >/dev/null 2>&1 || continue
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
  chmod 700 "$OFFICE_EVD" 2>/dev/null || true                 # 契約は dir 0700 / file 0600（python の経路と同じ）
  { printf '{"t":%s,"kind":"hook_no_python"}\n' "$(date +%s 2>/dev/null || echo 0)" >> "$OFFICE_EVD/hook_errors.jsonl"; } 2>/dev/null || true
  chmod 600 "$OFFICE_EVD/hook_errors.jsonl" 2>/dev/null || true
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
# R99: Claude Code は最後のコマンドが grep/rg/egrep/fgrep/find/diff/test/[・git diff/git grep のとき、
# 終了 1 を**成功扱い**にして PostToolUse を返し、応答に returnCodeInterpretation を付ける。
# None でない値が付いていたら終了コードは 0 ではない＝どの段も「走って成功した」と言えない（値の中身は読まない）。
nonzero_ok = tr.get("returnCodeInterpretation") is not None
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
ok_override = None                              # R106: 実データの事実（HEAD・ランナー自身の要約）で ok を決め直す

# R100-0: 割り方の正本は hooks/office_shell_lex.py（R100-1b の許可判定と共有する＝ここへ写さない）。
# sys.path は使わず**ファイルの場所で**読む（python -c の sys.path[0] は cwd＝ユーザーのプロジェクト。
# 同名のファイルがあっても拾わない）。読めなければ「証拠にできない」（unsafe）に倒す＝失敗を成功で上書きしない。
# 旧判定（下の if、`not nonzero_ok` も要る）と R106（更に下、`nonzero_ok` でも走る）の両方が要るので、
# どちらの条件でも通る `tool == "Bash" and not background` で 1 回だけ読む（別モデルレビュー指摘・
# 2026-10-04: 以前はそれぞれが同じファイルを個別に importlib で読み込み・コンパイル・exec していた＝
# 普通の Bash 呼び出し 1 回ごとに同じモジュールを 2 回ロードしていた）。
# 赤チーム（2026-10-04）: 文字単位の割り方は 1MB で 2.7 秒・10MB で 9.7 秒（hook の timeout 10 秒を超えて記録ごと消える）、
# `&&` や `;` の段が数千あると旧判定の鎖の再走査が二乗。実務のコマンドはこの何分の 1 も無い＝大きすぎる・段が多すぎる形は
# **分類しない**（分からないときは何も言わない）。同じ文字列は 1 回だけ割る（旧判定・R106 の test・commit で共有）。
_R106_MAX_CMD = 200_000
_R106_MAX_SEGS = 300
_office_split = None
if tool == "Bash" and not background and len(str(ti.get("command") or "")) <= _R106_MAX_CMD:
    def _load_lex():
        import importlib.util
        d = os.environ.get("OFFICE_HOOK_DIR") or ""
        if not os.path.isabs(d):
            raise ImportError("OFFICE_HOOK_DIR")
        spec = importlib.util.spec_from_file_location("office_shell_lex", os.path.join(d, "office_shell_lex.py"))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod.split

    try:
        _office_split_raw = _load_lex()
    except Exception:
        def _office_split_raw(text):
            return [], [], True, False, False, False
    _split_memo = {}

    def _office_split(text):
        r = _split_memo.get(text)
        if r is None:
            r = _office_split_raw(text)
            if len(r[0]) > _R106_MAX_SEGS:
                r = ([], [], True, False, False, False)         # 段が多すぎる＝分類しない（unsafe）
            _split_memo[text] = r
        return r

if tool == "Bash" and not background and not nonzero_ok and _office_split is not None:
    cmd = str(ti.get("command") or "")
    _split = _office_split
    # R98: Bash の分類は **git も test も同じ 1 本の判定**を通す（分類だけ・コマンド本文は捨てる）。
    # 台帳の証拠列（committed / tested / failed）はこの kind だけを根拠にするので、
    # 「実行したのか」「終了コードは誰のものか」を構造的に確かめてからでないと分類しない。
    #   ① 名前に触れただけを実行と誤らない（`cat verify.sh`・`echo 'git commit'`・引用やコメントの中）
    #   ② 終了コードがそのコマンドのものでない形は証拠にしない
    #      （`| tail`・`|| true`・`; echo`・`&`・`$( )`・終端の無いヒアドキュメント・run_in_background）
    #   ③ `&&` だけで繋がって終了 0 なら、パイプに流していない段は走って成功した（`git commit … && git log -1`）。
    #      ただしサブシェルの括弧が在る形（`( … && … ) | tail`）と、終了 1 を成功扱いされる形（最後が grep 等）は除く。
    #      ヒアドキュメントの本文は標準入力、`"$(cat <<'EOF' … EOF)"` は読めるリテラル（R99）
    #   ④ 同じ `&&` の鎖に成功した git commit があれば、後ろの git push より git:commit を記録する（R99）
    # 分からないときは何も言わない（台帳は「—」）＝失敗を成功で上書きしない。
    PREFIX = {"sudo", "time", "nice", "env", "npx", "bunx", "command", "exec"}
    RUNNER = {"uv", "poetry", "pipenv", "rye", "pdm"}       # `uv run pytest` の形
    GIT_OPT_VALUE = {"-C", "-c", "--git-dir", "--work-tree", "--namespace", "--exec-path"}
    # git commit の**暗黙の dry-run**（表示するだけでコミットしない）
    GIT_NO_RUN = {"--short", "--porcelain", "--long", "--null", "-z", "--dry-run"}
    # サブコマンドを実行せず表示だけして終わる git の大域オプション
    GIT_INFO_ONLY = {"--version", "--help", "-h", "--html-path", "--man-path", "--info-path",
                     "--exec-path"}

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
        segs, seps, unsafe, had_and, had_semi, had_group = _split(cmd)
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
                if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*=.*", tok, re.S):
                    j += 1; continue        # 値に空白があっても代入（1 トークンなので実行と混ざらない）
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
        all_segs = segs                                      # 下の門を通る前の全段（手前の段の証拠に使う）
        # 先行セグメントの `export FOO=…` や代入も、後ろの実行を変える（`export MAKEFLAGS=-i; make test`）
        lead_env = []
        for seg in segs[:-1]:
            for tok, _q in seg:
                if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*=.*", tok, re.S):
                    lead_env.append(tok)                     # 値に空白があっても見る
        # 代入だけのセグメント（head が空）は変数を置くだけ＝実行の結果に影響しない支度
        heads = [_head_token(seg) for seg in segs[:-1]]
        lead_safe = all(h == "" or h in SAFE_LEAD for h in heads)
        # 到達保証の例外は「先行が全部わかっている支度」のときだけ。`builtin exit 0 && pytest` の
        # ように**読めない先行**は通さない（別モデルレビュー）。
        proven = (had_and and not had_semi and ok_now
                  and all(h == "" or h in SAFE_LEAD or h in KNOWN_RUNNERS for h in heads))
        if len(segs) > 1 and not (lead_safe or proven):
            segs = []
        # `&&` が在る失敗は**誰の失敗か分からない**（`cd /なし && pytest` は pytest を走らせずに失敗）
        if segs and had_and and not ok_now:
            segs = []
        def _classify(toks, lead_env):
            """1 つのセグメントの分類（`git:commit`／`git:push`／`test`／""）。"""
            kind = ""
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
            while i < len(toks) and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*=.*", toks[i][0], re.S):
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
            return kind

        if segs and not unsafe:
            kind = _classify(segs[-1], lead_env)             # 終了コードを決めるのは最後のセグメント
        # Claude Code が終了 1 を成功扱いにする最後のコマンド（returnCodeInterpretation の判定と同じ表）。
        # 最後の段がこれなら PostToolUse でも全体の終了コードは 0 とは限らない＝手前の段の証拠にならない。
        # 応答の印（nonzero_ok）が無い古い形でも効くように、段の先頭でも見る（生の先頭語と前置きを外した名前の両方）。
        LENIENT_RC = {"grep", "rg", "egrep", "fgrep", "find", "diff", "test", "["}

        def _lenient_rc(seg):
            toks = [t for t, _q in seg]
            names = {toks[0].rsplit("/", 1)[-1] if toks else "", _head_token(seg)}
            if names & LENIENT_RC:
                return True
            if "git" not in names:
                return False
            j = next((n for n, t in enumerate(toks) if t.rsplit("/", 1)[-1] == "git"), len(toks)) + 1
            while j < len(toks) and toks[j].startswith("-"):
                j += 2 if toks[j] in GIT_OPT_VALUE else 1
            return j < len(toks) and toks[j] in ("diff", "grep")

        def _chain():
            """R99: 最後の `;` より後ろの `&&` の鎖で、**走って 0 を返した**と言える段の分類（後ろから順に）。
            `git commit … && git log --oneline -1` が 4 日で 1 件も拾えていなかった。鎖が終了 0 なら、
            パイプに流していない段は走って 0 を返した。パイプに流した段の終了コードは消えるので使わない。
            鎖より前の `;` の段は支度だけ、鎖の中で手前にある段は読める段だけ（上と同じ許可リスト）。
            見ない形: サブシェルの括弧が在る（`( … && … ) | tail` は tail の 0）／最後の段が終了 1 を
            成功扱いされるコマンド（`… && git diff`・`… | grep x`）。"""
            if not all_segs or unsafe or not ok_now or had_group or _lenient_rc(all_segs[-1]):
                return []
            start = max([k + 1 for k in range(len(all_segs) - 1) if seps[k] == ";"] or [0])
            if not all(_head_token(s) in SAFE_LEAD or _head_token(s) == "" for s in all_segs[:start]):
                return []
            out = []
            for k in range(len(all_segs) - 1, start - 1, -1):
                if (k < len(all_segs) - 1 and seps[k] != "&&") or not all(
                        h == "" or h in SAFE_LEAD or h in KNOWN_RUNNERS
                        for h in (_head_token(s) for s in all_segs[start:k])):
                    continue
                env = [t for s in all_segs[:k] for t, _q in s
                       if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*=.*", t, re.S)]
                out.append(_classify(all_segs[k], env))
            return out

        chain = _chain()
        # 上で何も言えなかったときだけ鎖を見る。後ろの段から見るので、分類できる段が複数あれば
        # 最後に走ったものが勝つ（上の「最後の段」と同じ）
        if not kind:
            kind = next((x for x in chain if x), "")
        # ただし同じ鎖に成功した git commit があれば、後ろの git push より commit を記録する
        # （`git commit … && git push` が push として数えられ、台帳の証拠列にも日報のコミット数にも出なかった。
        # git:push の消費者は日次の件数表だけで、読む側が無い＝commit を優先しても失うものが無い）
        if kind == "git:push" and "git:commit" in chain:
            kind = "git:commit"
    except Exception:
        kind = ""                                            # 判定に失敗したら分類しない（hook は落ちない）

# R106（2026-10-03・本人の実データ）: 文字形だけの保守的な判定は、3 日分で commit 527 回・test 779 回に対し
# commit 約13・test **0** しか拾えなかった（`> log 2>&1; echo "exit=$?"`／`| tail`／`$( )`／複数行／
# run_in_background がほぼ全部をはじく）。ここから先は文字形を離れ、**実際に起きたこと**で決める。
#   commit = このツール呼び出しの前後で対象ディレクトリの HEAD が本当に動いたか（PreToolUse で控え、
#            PostToolUse で比べる。シェルの終了コードは一切見ない＝文字形が何であっても拾える）。
#   test   = ランナーが**コマンドのどこかに**現れた（終了コードの帰属＝「最後のセグメントか」は問わない。
#            上の old 判定とは独立）**かつ**、ランナー自身の要約行が実際の出力（stdout+stderr の全文。
#            窓を区切らない＝別モデルレビュー指摘 2026-10-04・下の _r106_test_verdict）に在るときだけ、
#            その要約で ok を決める（exit コードは見ない）。要約が無ければ何も言わない＝old の kind="test"
#            は信用しない（下の1行で必ず捨てる。「数えない」は安全側の既定で、新方式の確認が無い限り数えない）。
# どちらも「分かったときだけ言う」。git が無い・dir が無い・要約が無い等は常に黙って引く（commit は旧判定へ、
# test は「—」のまま）＝失敗を成功で上書きしない。
if kind == "test":
    kind = ""                                               # old の文字形だけの test 判定はもう信用しない
home = os.environ.get("OFFICE_HOME") or os.path.expanduser("~")  # R106 の控えファイルが要るので先に確定させる

if tool == "Bash" and not background and _office_split is not None:
    _r106_split = _office_split  # 旧判定と同じ 1 回のロードを共有する（上の _office_split）

    # ---------- test: ランナーがどこかに現れたか（終了コードの帰属は問わない） ----------
    _R106_PREFIX = {"sudo", "time", "nice", "env", "npx", "bunx", "command", "exec"}
    _R106_WRAP = {"uv", "poetry", "pipenv", "rye", "pdm"}
    _R106_NO_RUN = {"--version", "-V", "--help", "-h", "--collect-only", "--co", "--no-run",
                    "--fixtures", "--markers", "--showconfig", "--showConfig", "--show-config",
                    "--dry-run", "--just-print", "--listTests", "--list-tests", "--list", "-list",
                    "--setup-plan", "--setup-only", "--fixtures-per-test", "--trace-config",
                    "--collectonly", "--collect_only", "--if-present",
                    "--cache-show", "--cache-clear", "--clearCache", "--clear-cache"}

    def _r106_env_no_run(toks):
        no_run = False
        for tok in toks:
            name, _, value = tok.partition("=")
            parts = [x for x in re.split(r"[\s,]+", value) if x]
            if {x.split("=", 1)[0] for x in parts} & _R106_NO_RUN:
                no_run = True
            if name in ("MAKEFLAGS", "GNUMAKEFLAGS") and any(
                    re.fullmatch(r"-?[A-Za-z]+", x) and set("ntqi") & set(x.lstrip("-")) for x in parts):
                no_run = True
        return no_run

    def _r106_test_family(seg, lead_no_run):
        """このセグメント単体が(非 dry-run の)ランナー起動に見えるか。既存 _classify の test 分岐と同じ
        パターンだが、終了コードの帰属（最後のセグメントか・鎖がどう繋がるか）は一切問わない＝だから
        どのセグメントに現れても見る（R106 は出力の実際の要約行で ok を決めるので、ここでは『ランナーが
        走った可能性』だけを確かめれば十分）。家系（family）を返す＝どの要約パターンで読むかを選ぶため。"""
        toks = [t for t, _q in seg]
        unquoted = {t for t, q in seg if not q}
        if not toks:
            return ""
        env_no_run = lead_no_run
        i = 0
        while i < len(toks) and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*=.*", toks[i], re.S):
            if _r106_env_no_run([toks[i]]):
                env_no_run = True
            i += 1
        while i < len(toks) and toks[i] in _R106_PREFIX | _R106_WRAP:
            wrap = toks[i] in _R106_WRAP
            i += 1
            if wrap and i < len(toks) and toks[i] == "run":
                i += 1
        head = toks[i].rsplit("/", 1)[-1] if i < len(toks) else ""
        args = toks[i + 1:]
        first = args[0].rsplit("/", 1)[-1] if args else ""
        if (env_no_run or ({a.split("=", 1)[0] for a in args} & _R106_NO_RUN)
                or any(x in _R106_NO_RUN for a in args for x in re.split(r"[\s,=]+", a) if x.startswith("-"))):
            return ""
        if args[:1] == ["list"] and head in ("vitest", "jest", "mocha", "go", "cargo"):
            return ""
        if head in ("pytest", "py.test"):
            return "pytest"
        if head in ("python", "python3") and args[:1] == ["-m"] and args[1:2] == ["pytest"]:
            return "pytest"
        if head in ("python", "python3") and args[:1] == ["-m"] and args[1:2] == ["unittest"]:
            return "unittest"
        if head in ("vitest", "jest", "mocha"):
            return "jest_vitest"
        if head in ("npm", "pnpm", "yarn", "bun") and (args[:1] == ["test"] or args[:2] == ["run", "test"]):
            return "jest_vitest"
        if head == "node":
            j = 0
            while j < len(args):
                a = args[j]
                if not a.startswith("-"):
                    return ""
                base = a.split("=", 1)[0]
                if base in ("-e", "--eval", "-p", "--print", "-c", "--check") or a == "--":
                    return ""
                if a == "--test" and a in unquoted:
                    return "node"
                j += 1
            return ""
        if head == "verify.sh" or (head in ("bash", "sh", "zsh") and first == "verify.sh"):
            return "verify"
        if head == "dev.sh" and "--check" in args:
            return "devcheck"
        if head in ("bash", "sh", "zsh") and first == "dev.sh" and "--check" in args:
            return "devcheck"
        return ""                                            # cargo/go/make test 等＝要約パターンを定義しない

    _R106_MAX_OUT = 8_000_000
    _R106_ANSI = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")

    def _r106_test_verdict(families, text):
        """ランナー自身の要約行だけで ok を決める。fail の印が1つでもあれば fail が勝つ（別の家系・
        同じ家系の別の回の pass 行で上書きしない＝失敗を成功で隠さない）。要約が無ければ None（何も言わない）。
        別モデルレビュー指摘（2026-10-04）: 以前はこの判定を出力**末尾 8KB だけ**に限定していたため、
        1 回の Bash 呼び出しで複数回ランナーを打つ形（例: `pytest a; pytest b`）で、手前の実行の fail
        要約が窓の外へ押し出されると「後の pass 要約だけが見える」→ fail を見逃して ok=true（静かな嘘）
        になっていた（実機で再現）。fail/pass とも窓を区切らず、捕まえた出力全文を見る
        （すでにメモリ上にある文字列を正規表現で走査するだけ＝150ms 予算への影響は無視できる）。"""
        if len(text) > _R106_MAX_OUT:
            return None                                      # 数十 MB の出力は走査しない（時間が読めない＝何も言わない）
        text = _R106_ANSI.sub("", text)                      # FORCE_COLOR の色の制御列（行頭・行末）で要約を見落とさない
        fail = passed = False
        if "pytest" in families:
            if re.search(r"\b[1-9]\d*\s+failed\b", text) or re.search(r"\b[1-9]\d*\s+errors?\b", text):
                fail = True
            elif re.search(r"\b[1-9]\d*\s+passed\b", text):
                passed = True
        if "unittest" in families:
            if re.search(r"(?m)^FAILED \(", text):
                fail = True
            elif re.search(r"(?m)^OK\b", text):
                passed = True
        if "node" in families:
            # 要約は**全部**見る（同じ呼び出しで 2 回走れば要約も 2 組＝後ろの fail を最初の fail 0 で隠さない）。
            # spec（ℹ）と TAP（# ・非 TTY や --test-reporter=tap）の両方。cancelled（タイムアウト等で未完了）は
            # fail 0 でも成功ではない＝fail として扱う（別モデルレビュー 2026-10-04）
            nfail = [int(x) for x in re.findall(r"(?m)^[ \t]*(?:ℹ|#) fail (\d+)[ \t]*\r?$", text)]
            npass = [int(x) for x in re.findall(r"(?m)^[ \t]*(?:ℹ|#) pass (\d+)[ \t]*\r?$", text)]
            ncan = [int(x) for x in re.findall(r"(?m)^[ \t]*(?:ℹ|#) cancelled (\d+)[ \t]*\r?$", text)]
            if any(n > 0 for n in nfail) or any(n > 0 for n in ncan):
                fail = True
            elif nfail and any(n > 0 for n in npass):
                passed = True
        if "jest_vitest" in families:
            # Jest: `Test Suites: 1 failed, 1 passed`（スイートが起動できない失敗はここにしか出ない）・`Tests: …`。
            # Vitest: 行頭に空白・コロン無し（` Test Files  1 passed (1)`・`      Tests  3 passed (3)`）
            for m in re.finditer(r"(?m)^[ \t]*(?:Test Suites:|Tests:|Tests[ \t]{2,}|Test Files[ \t]).*$", text):
                if re.search(r"\b\d+\s+failed\b", m.group(0)):
                    fail = True
                elif re.search(r"\b\d+\s+passed\b", m.group(0)):
                    passed = True
        if "verify" in families:
            if re.search(r"❌\s*verify:", text):
                fail = True
            elif re.search(r"verify:\s*(全チェック合格|合格)", text):
                passed = True
        if "devcheck" in families:
            if re.search(r"❌\s*\d+\s*件", text):
                fail = True
            elif "✅ 高速レーン green" in text:
                passed = True
        if fail:
            return False
        if passed:
            return True
        return None

    _R106_DUMPERS = {"cat", "tac", "nl", "head", "tail", "less", "more", "sed", "awk", "gawk", "grep", "egrep", "fgrep",
                     "rg", "jq", "sort", "uniq", "cut", "column", "strings", "bat", "xxd", "od", "zcat", "gzcat"}
    _R106_PRINTERS = {"echo", "printf", "print"}
    # echo/printf の引数が要約に見えるか。3 巡目のレビュー: 引数を書かれたままの文字で見ていたので、`printf '\033[32mOK…'`
    # （色の制御列の後ろの OK＝行頭ではない）が素通りし、出力側だけ色を落としてから unittest の「OK」と読んでいた。
    # 端末に出る形（逃がし文字を解き・色を落とし・引数ごと／つなげた形）で見て、OK/FAILED は行頭に限らない。
    # 守る範囲: 正直なコマンドの出力の取り違え。python -c の print など任意の手段で要約を偽る呼び出しまでは防げない（rules/hooks.md）
    # 5 巡目のレビュー: ① `\d+` に左の境界が無いと長い数字の列で各桁から探し直す二乗（3 万桁で 11 秒＝hook の timeout で記録ごと消えた）
    # → (?<!\d) で数字の列の頭からだけ始める ② `WEBHOOK` の末尾の OK まで要約と見て、正しいテストの記録を捨てた → 英字の直後の OK は見ない
    _R106_SUMMARY = re.compile(r"(?<!\d)\d+\s+(?:passed|failed)\b|(?:ℹ|#)\s*(?:pass|fail|cancelled)\s+\d"
                               r"|(?<![A-Za-z])(?:OK|FAILED)\b|Test (?:Files|Suites)|Tests:|verify:|高速レーン")
    _R106_ESC = re.compile(r"\\(?:[eE]|x[0-9A-Fa-f]{1,2}|u[0-9A-Fa-f]{4}|0?[0-7]{1,3}|[nrtv])")

    def _r106_unescape(s):
        """echo -e・printf・$'…' が解く逃がし文字（8 進・16 進・e・u の色の制御列と改行）を解く。"""
        def one(m):
            t = m.group(0)[1:]
            if t in ("e", "E"):
                return "\x1b"
            if t in ("n", "r", "v"):
                return "\n"
            if t == "t":
                return "\t"
            if t[0] in "xu":
                return chr(int(t[1:], 16))
            return chr(int(t, 8) & 0xFF)
        return _R106_ESC.sub(one, s)

    # 7 巡目のレビュー: フラグ [-+ #0]* と幅 \d+ がどちらも 0 を取れると、`%000…0!` で取り分けを試し直す二乗（3 万個で 11 秒＝
    # hook の timeout）→ 幅は 1〜9 で始める（先頭の 0 は printf でもフラグ）＝取り分けが 1 通りに決まる
    _R106_FMT = re.compile(r"%(?:%|[-+ #0]*(?:[1-9]\d*|\*)?(?:\.(?:\d*|\*))?[a-zA-Z])")

    def _r106_printf_render(args):
        """printf が実際に出す形（書式の指定に引数を順に入れ、引数が余れば書式を繰り返す）。6 巡目のレビュー:
        `printf '%sOK\n' ''` の %s の s を英字と見て OK を見落とした（書式の指定は出力に残らない）。"""
        if not args:
            return ""
        fmt, rest = args[0], list(args[1:])
        out, total = [], 0
        for _i in range(20):
            def _sub(m):
                spec = m.group(0)
                if spec == "%%":
                    return "%"
                prec = None
                head_, dot, tail_ = spec[1:-1].partition(".")
                if "*" in head_:
                    rest.pop(0) if rest else None             # 幅を引数で渡す形（値は使わない）
                if dot:                                       # 8 巡目のレビュー: 精度を守る（`%.s`・`%.0s` は引数を出さない）
                    # 9 巡目のレビュー: 負の精度は「精度なし」・20 万桁の精度を int() に渡すと二乗（8.5 秒）＝6 桁を超える数は「切らない」
                    raw = (rest.pop(0) if rest else "0").strip() if tail_ == "*" else (tail_ or "0")
                    if re.fullmatch(r"\d{1,6}", raw):
                        prec = int(raw)
                    elif re.fullmatch(r"-\d+|\d+", raw):
                        prec = None                           # 負・桁が多すぎる＝切らない
                    else:
                        prec = 0                              # 数でない（printf は 0 として扱う）
                arg = rest.pop(0) if rest else ""
                if spec[-1] == "b":
                    arg = _r106_unescape(arg)                 # %b は逃がし文字を解いた後に精度で切る（9 巡目のレビュー）
                if prec is not None and spec[-1] in "sb":
                    arg = arg[:prec]
                return arg
            piece = _R106_FMT.sub(_sub, fmt)
            out.append(piece)
            total += len(piece)
            if not rest or total > 400_000 or not _R106_FMT.search(fmt):
                break
        return "".join(out)

    def _r106_prints_summary(head, args):
        forms = [" ".join(args), "".join(args), "\n".join(args)]
        if head == "printf":
            forms.append(_r106_printf_render(args))
        for s in forms:
            if _R106_SUMMARY.search(_R106_ANSI.sub("", _r106_unescape(s))):
                return True
        return False

    # パイプの下流で標準入力を流すだけの段が、位置引数（パターン・スクリプト）をいくつ取るか。これを超えたら
    # ファイルを読んでいる＝ランナーの出力ではないものを出す独立した出力源（`… | cat old.log` 等・2 巡目のレビュー）。
    # 3 巡目のレビュー: オプションの値（`grep -e '# pass' -e '# fail'`・`sort -k 1,1`・`awk -v n=1`）をファイルと数えて
    # 正しい要約を捨てていた → 値を取るオプションは値ごと飛ばし、-e/-f でパターン（スクリプト）を渡したら位置引数は全部ファイル
    _R106_FILTER_POS = {"grep": 1, "rg": 1, "sed": 1, "awk": 1, "jq": 1}
    _R106_FILTER_ALIAS = {"egrep": "grep", "fgrep": "grep", "gawk": "awk"}
    _R106_PATTERN_OPTS = {"grep": {"-e", "-f", "--regexp", "--file"}, "rg": {"-e", "-f", "--regexp", "--file"},
                          "sed": {"-e", "-f", "--expression", "--file"}, "awk": {"-f", "--file"},
                          "jq": {"-f", "--from-file"}}
    _R106_OPT_VAL = {
        "grep": {"-e", "-f", "-m", "-A", "-B", "-d", "-D", "--regexp", "--file", "--max-count", "--label",
                 "--after-context", "--before-context", "--devices", "--directories"},
        "rg": {"-e", "-f", "-g", "-t", "-T", "-m", "-A", "-B", "-C", "-M", "-j", "-r", "-E", "--regexp", "--file",
               "--glob", "--type", "--type-not", "--max-count", "--after-context", "--before-context", "--context",
               "--max-columns", "--threads", "--replace", "--encoding"},
        "sed": {"-e", "-f", "--expression", "--file"},
        "awk": {"-f", "-v", "-F", "--file", "--assign", "--field-separator"},
        "jq": {"-f", "-L", "--from-file", "--indent"},
        "sort": {"-k", "-t", "-o", "-S", "-T", "--key", "--field-separator", "--output", "--buffer-size",
                 "--temporary-directory", "--parallel", "--batch-size"},
        "head": {"-n", "-c", "--lines", "--bytes"}, "tail": {"-n", "-c", "--lines", "--bytes"},
        "cut": {"-d", "-f", "-c", "-b", "--delimiter", "--fields", "--characters", "--bytes", "--output-delimiter"},
        "uniq": {"-f", "-s", "-w", "--skip-fields", "--skip-chars", "--check-chars"},
        "column": {"-s", "-c", "-o", "-N", "-R", "-E", "-H", "-W", "-O", "-l", "--separator", "--output-separator",
                   "--table-columns", "--output-width"},
        "nl": {"-b", "-d", "-f", "-h", "-i", "-l", "-n", "-s", "-v", "-w"},
        "tac": {"-s", "--separator"}, "strings": {"-n", "-t", "-e", "--bytes", "--radix", "--encoding"},
        "xxd": {"-c", "-g", "-l", "-s", "-o"}, "od": {"-A", "-t", "-j", "-N"},
        "bat": {"-l", "-H", "-r", "-m", "--language", "--highlight-line", "--line-range", "--map-syntax", "--theme",
                "--style", "--tabs", "--terminal-width", "--wrap"},
    }
    # 値を 2 つ取る。迷うオプション（値を取るか版で違う: Mac の sed -l・grep -C・od -w）は値を取らない側に倒す
    # ＝位置引数を多めに数えて「ファイルを読む段」にする＝記録を捨てるだけで嘘にはならない（5 巡目のレビュー）
    _R106_OPT_VAL2 = {"jq": {"--arg", "--argjson", "--slurpfile", "--rawfile"}}
    _R106_OPT_FILE2 = {"--slurpfile", "--rawfile"}                                # 2 つ目の値はファイル（jq）

    def _r106_filter_files(head, args):
        """パイプの下流の段が読むファイルの数（0＝標準入力だけを流す＝ランナーの出力の通り道）。"""
        h = _R106_FILTER_ALIAS.get(head, head)
        vals, vals2 = _R106_OPT_VAL.get(h, set()), _R106_OPT_VAL2.get(h, set())
        pat_opts = _R106_PATTERN_OPTS.get(h, set())
        npat = _R106_FILTER_POS.get(h, 0)
        files = 0
        opts_done = False
        j = 0
        while j < len(args):
            a = args[j]
            if a == "-":
                pass                                          # 標準入力
            elif not opts_done and a == "--":
                opts_done = True
            elif not opts_done and a.startswith("-"):
                name = a.split("=", 1)[0]
                if name in pat_opts or (not a.startswith("--") and a[:2] in pat_opts):
                    npat = 0                                  # パターン／スクリプトは -e/-f で渡った
                if "=" not in a and a in vals2:
                    if a in _R106_OPT_FILE2:
                        files += 1                            # `jq --rawfile name old.log` は古いファイルの中身を出せる
                    j += 2
                elif "=" not in a and a in vals:
                    j += 1
            elif npat > 0:
                npat -= 1                                     # パターン／プログラム（awk の 'NF=NF' も代入より先にこちら）
            elif h == "awk" and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*=.*", a, re.S):
                pass                                          # awk の変数代入（ファイルではない）
            else:
                files += 1
            j += 1
        return files

    def _r106_foreign_output(segs, seps, is_runner):
        """ランナーの出力ではないものを出す段が在るか（在れば要約の帰属を諦める）。
        パイプの上流・外: ファイルを出す段（cat/tail/grep …）は在るだけで出力源、echo/printf は要約に見える文字を打つときだけ。
        パイプの下流: 標準入力を流すだけの段は出力源ではないが、ファイルを読む段（位置引数が多い）と要約を打つ echo/printf は出力源
        （`node --test x.mjs | printf '# pass 1'` は node が落ちても合格になった）。"""
        downstream = False                                   # 今の段がランナーを含むパイプの下流か
        for k, seg in enumerate(segs):
            if is_runner[k]:
                downstream = True
            else:
                toks = [t for t, _q in seg]
                i = 0
                while i < len(toks) and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*=.*", toks[i], re.S):
                    i += 1
                while i < len(toks) and toks[i] in _R106_PREFIX:
                    i += 1
                head = toks[i].rsplit("/", 1)[-1] if i < len(toks) else ""
                args = toks[i + 1:]
                if head in _R106_PRINTERS and _r106_prints_summary(head, args):
                    return True
                if head in _R106_DUMPERS:
                    if not downstream:
                        return True
                    if _r106_filter_files(head, args) > 0:
                        return True
            if k < len(seps) and seps[k] != "|":
                downstream = False                           # パイプの外へ出たら下流ではない
        return False

    if ev in ("PostToolUse", "PostToolUseFailure"):
        try:
            _r106_cmd = str(ti.get("command") or "")
            _r106_segs, _r106_seps, _r106_unsafe, _r106_and, _r106_semi, _r106_grp = _r106_split(_r106_cmd)
            _r106_families = set()
            _r106_no_run = False                             # 先行の代入の「走らせない」印は累積の真偽値（毎段の再走査は二乗）
            _r106_is_runner = []
            for _r106_seg in _r106_segs:
                _r106_fam = _r106_test_family(_r106_seg, _r106_no_run)
                _r106_is_runner.append(bool(_r106_fam))
                if _r106_fam:
                    _r106_families.add(_r106_fam)
                if not _r106_no_run:
                    _r106_no_run = _r106_env_no_run([t for t, _q in _r106_seg
                                                     if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*=.*", t, re.S)])
            # 出力の帰属（別モデルレビュー 2026-10-04）: 要約行がランナー自身のものと言えない形では何も言わない。
            # ランナーの出力をパイプで受けていない段が、ファイルの中身を出す（cat/tail/grep …＝古いログの要約が混ざる）か、
            # 要約に見える文字を自分で打つ（echo/printf の引数）なら、その出力はランナーのものと区別できない
            # （`printf '5 passed'; false && pytest -q; true` は pytest が走らずに「合格」になった）
            if _r106_families and _r106_foreign_output(_r106_segs, _r106_seps, _r106_is_runner):
                _r106_families = set()
            if _r106_families:
                _r106_out = str(tr.get("stdout") or "") + "\n" + str(tr.get("stderr") or "")
                _r106_verdict = _r106_test_verdict(_r106_families, _r106_out)
                if _r106_verdict is not None:
                    kind = "test"
                    ok_override = _r106_verdict
        except Exception:
            pass

    # ---------- commit: HEAD が実際に動いたか（文字形は見ない） ----------
    def _r106_commit_dir(cmd, cwd):
        """先頭の `cd <dir>` と、`commit` を打つ `git` セグメントの `-C <dir>` だけを見る（複雑にしない。
        間違えても実害は『控え忘れ＝旧判定に任せる』だけ）。戻り値= (dir, そのセグメントが commit か)。"""
        try:
            segs, _seps, _unsafe, _a, _s, _g = _r106_split(cmd)
        except Exception:
            return cwd, False
        dir0 = cwd
        if segs:
            toks0 = [t for t, _q in segs[0]]
            if toks0 and toks0[0].rsplit("/", 1)[-1] == "cd" and len(toks0) >= 2 and toks0[1] not in ("", "-"):
                dir0 = toks0[1] if toks0[1].startswith("/") else os.path.normpath(os.path.join(cwd, toks0[1]))
        pre = {"sudo", "time", "nice", "env", "command", "exec"}
        opt_value = {"-C", "-c", "--git-dir", "--work-tree", "--namespace", "--exec-path"}
        for seg in segs:
            toks = [t for t, _q in seg]
            i = 0
            while i < len(toks) and toks[i] in pre:
                i += 1
            if i >= len(toks) or toks[i].rsplit("/", 1)[-1] != "git":
                continue
            args = toks[i + 1:]
            c_opt, j = None, 0
            while j < len(args) and args[j].startswith("-"):
                if args[j] == "-C" and j + 1 < len(args):
                    c_opt = args[j + 1]
                j += 2 if args[j] in opt_value else 1
            if j < len(args) and args[j] == "commit":
                if c_opt:
                    return (c_opt if c_opt.startswith("/") else os.path.normpath(os.path.join(dir0, c_opt))), True
                return dir0, True
        return dir0, False

    def _r106_git_head(git_bin, dir_):
        r = subprocess.run([git_bin, "-C", dir_, "log", "-1", "--format=%H%x1f%ct"],
                           capture_output=True, text=True, timeout=1.5)
        if r.returncode != 0:
            return None, None
        sha, _sep, ct = r.stdout.strip().partition("\x1f")
        ct = ct.strip()
        if not re.fullmatch(r"[0-9a-f]{40}", sha) or not re.fullmatch(r"-?\d+", ct):
            return None, None
        return sha, int(ct)

    _r106_cmd2 = str(ti.get("command") or "")
    _r106_tuid = str(d.get("tool_use_id") or "")
    # 赤チーム（2026-10-04）: tool_use_id が無いと控えの鍵が「セッション」になり、並行の別の呼び出しの控えと取り違えて
    # 失敗した呼び出しを「commit 成功」にした＝tool_use_id が無い呼び出しでは R106 の commit 判定をしない（旧判定のまま）。
    # 「git と commit の語がある」だけでは割らずに、commit を打つ段が本当にあるときだけ控える・待つ（毎回 0.5 秒待たない）
    if _r106_tuid and "git" in _r106_cmd2 and "commit" in _r106_cmd2:
        import shutil
        import subprocess
        _r106_git_bin = shutil.which("git") or ("/usr/bin/git" if os.path.exists("/usr/bin/git") else "")
        _r106_dir_c, _r106_is_commit = _r106_commit_dir(_r106_cmd2, str(d.get("cwd") or ""))
        if _r106_git_bin and _r106_is_commit and os.path.isdir(_r106_dir_c):
            _r106_dir = os.path.join(home, ".claude", "office_events", "commit_pre")
            _r106_pre_path = os.path.join(_r106_dir, hashlib.sha1(_r106_tuid.encode("utf-8")).hexdigest()[:24] + ".json")
            # 控えには本文を書かない＝コマンドと dir は SHA-1 の先頭だけ（Post で同じ呼び出しかを照合する）
            _r106_cmdh = hashlib.sha1(_r106_cmd2.encode("utf-8")).hexdigest()[:16]
            _r106_dirh = hashlib.sha1(_r106_dir_c.encode("utf-8")).hexdigest()[:16]

            def _r106_write_0600(path, obj):
                fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
                try:
                    try:
                        os.fchmod(fd, 0o600)                 # 既に在った緩いファイルも直す（O_CREAT の mode は新規のときだけ）
                    except OSError:
                        pass
                    os.write(fd, json.dumps(obj).encode("utf-8"))
                finally:
                    os.close(fd)

            if ev == "PreToolUse":
                try:
                    _sha, _ct = _r106_git_head(_r106_git_bin, _r106_dir_c)
                    if _sha:
                        os.makedirs(_r106_dir, mode=0o700, exist_ok=True)
                        try:
                            os.chmod(_r106_dir, 0o700)
                        except OSError:
                            pass
                        try:                                          # 古い控え・記帳（30 分）を掃除
                            _now_t = time.time()
                            for _name in os.listdir(_r106_dir):
                                _p = os.path.join(_r106_dir, _name)
                                try:
                                    if _now_t - os.stat(_p).st_mtime > 1800:
                                        os.unlink(_p)
                                except OSError:
                                    pass
                        except OSError:
                            pass
                        _r106_write_0600(_r106_pre_path, {"head": _sha, "t": time.time(),
                                                          "cmdh": _r106_cmdh, "dirh": _r106_dirh})
                except Exception:
                    pass
            elif ev in ("PostToolUse", "PostToolUseFailure"):
                try:
                    # hook は非同期（install.sh が async: true で配線）＝ごく短いコマンドでは控えの書き込みが
                    # まだ終わっていないことがある。commit を打つ呼び出しのときだけ 0.5 秒まで待つ（この hook も非同期）
                    for _w in range(10):
                        if os.path.exists(_r106_pre_path):
                            break
                        time.sleep(0.05)
                    with open(_r106_pre_path, "r", encoding="utf-8") as _f:
                        _pre = json.load(_f)
                    os.unlink(_r106_pre_path)                         # 使い捨て（1 回のツール呼び出しに 1 件）
                    _pre_head, _pre_t = _pre.get("head"), float(_pre.get("t", 0))
                    if (isinstance(_pre_head, str) and re.fullmatch(r"[0-9a-f]{40}", _pre_head)
                            and _pre.get("cmdh") == _r106_cmdh and _pre.get("dirh") == _r106_dirh
                            and time.time() - _pre_t < 1800):
                        _new_sha, _committer_ts = _r106_git_head(_r106_git_bin, _r106_dir_c)
                        # 控えの後に HEAD が動いたときだけ。非同期の追い越し（控えの前に commit が終わる）は**数えない**＝見落とすだけ。
                        # reflog の時刻で救う版は、別の呼び出しの commit・--dry-run・作業木の違い・並行の二重計上で「成功」と嘘をついた
                        # （2026-10-04 の赤チームと 2 巡目の別モデルレビュー）＝分からないときは何も言わない、へ戻した
                        if (_new_sha and _new_sha != _pre_head and _committer_ts is not None
                                and _committer_ts >= _pre_t - 2):
                            kind = "git:commit"
                            ok_override = True
                except Exception:
                    pass

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
    # R102-4: サブエージェントの中の道具か（0/1 だけ・ID は載せない）。裏のサブエージェントは親のターンが終わった後も
    # 道具を使う＝親の「ターンが開いている（作業中）」の判定に混ぜない
    "ag": 1 if (d.get("agent_id") and not ev.startswith("Subagent")) else 0,
    "task": clip(d.get("task_id"), 40),
    # R106: 実際に起きたことが分かったとき（HEAD が動いた／ランナー自身の要約が読めた）はそれを使う。
    "ok": ok_override if ok_override is not None else ev not in ("PostToolUseFailure", "StopFailure", "PermissionDenied"),
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
# R100-0: 割り方の正本 office_shell_lex.py はこの hook と同じ場所（~/.claude/hooks・リポジトリの hooks/）に置く
OFFICE_HOOK_DIR="$(cd "$(dirname "$0")" 2>/dev/null && pwd)"
OFFICE_HOME="$H" OFFICE_HOOK_DIR="$OFFICE_HOOK_DIR" "$OFFICE_PY" -I -c "$PY" >/dev/null 2>&1
exit 0
