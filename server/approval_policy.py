# -*- coding: utf-8 -*-
"""R100-1b: スマホから許可してよい 1 件か（危険判定・純関数・標準ライブラリ・Python 3.9 の構文）。

classify(tool, tool_input, cwd, home) -> {"level": "remote" | "review" | "mac", "reasons": [理由…](, "tags": [警告…])}
  - 3 段（R102-2 で分け、R103-1・R114 の本人裁定で今の境界＝CLAUDE.md 不変条件 7）。スマホから許可できるのは ON の間だけ:
    mac    = 共通の門（読めない・大きすぎる・サンドボックス外し・未知のツール）か、拒否リスト（approval_review.check）に当たる形。
             種類で決まった危険な形（削除・外部送信・インストール・設定・名前や旗が公開／削除を思わせる台本…）は**毎回 Face ID**
             （パスキーの署名）でスマホから許可できる。
             判定できなかった形（UNJUDGED＝読めない・動的・パイプで実行…）は全文を置かない＝**Mac だけ**（phone_approvable）。
    remote = 拒否リストに当たらず、しかも許可リスト（下の _check）に当たる形＝**タップ**（Face ID は ON にするときだけ）。
    review = 拒否リストに当たらないが許可リストにも無い形＝**タップ**（画面に警告のタグ・全文を読む義務は R114 で廃止）。
             reasons は「許可リストに無い理由」、tags は画面で目立たせる警告（cwd の外・書く・台本・インラインコード…）。
             ふつうの台本の実行・インラインコード・cwd の外への書き込みはここ＝ON の間はタップで通る（R116-S0 の反証: 同意の文が
             「スクリプトは毎回 Face ID」と言っていた。同意の文と 📱 の注記の例は test_approval_policy がこの classify と突き合わせる）。
  - 拒否リストは remote の判定より**先に**通す（remote は「拒否に当たらない」の真部分集合＝攻撃側レビュー F2）。例外は mac。
  - 同じ関数を 2 か所で呼ぶ: hook（`~/.claude/hooks/` に配られた写し・最終判定）と daemon
    （`/api/approval/remote` の 14 番目の検査・掲示の全文から再計算）。どちらかが mac なら許可しない。
  - 見るのは本文に書かれた形だけ（`npm test` の中身は見ない＝docs/security-notes.md の残るリスク）。
  - ファイルシステムは realpath と glob の展開だけ読む（シンボリックリンクで cwd の外へ出る形を止める）。

input_canon / input_sha: 表示する文字列の正本（hook の掲示・daemon の再計算・スマホの再計算が同じ値になる）。
シェルの割り方は hooks/office_shell_lex.py（正本 1 本）を**ファイルの場所で**読む（sys.path を使わない＝
cwd の同名ファイルを拾わない）。hook の写しでは同じフォルダ、リポジトリ／app では ../hooks/。
理由の一覧（固定の enum・監査で集計する）は REASONS。
"""
import fnmatch
import glob as _glob
import hashlib
import importlib.util
import json
import os
import re
import unicodedata

MAX_INPUT_BYTES = 32768
REASONS = (
    "unreadable", "too-large", "tool", "sandbox-off", "syntax", "env-prefix", "wrapper", "redirect",
    "command", "option", "git-option", "script", "sensitive-path", "self", "outside-cwd", "write", "push-target",
    # R102-2: 拒否リスト（approval_review.REASONS と同じ集合・test_approval_policy が突き合わせる）
    "nesting", "unreadable-syntax", "zsh-syntax", "delete", "system", "privilege", "process", "secret", "network",
    "network-send", "network-listen", "install", "publish", "dynamic", "dynamic-command", "env-danger", "pipe-exec",
    "download-exec", "script-name", "script-option", "system-path", "protected-write", "symlink", "code-delete",
    "code-process", "code-network", "code-dynamic", "code-privilege", "code-secret", "git-config", "git-destructive",
    "git-other", "git-push-force", "git-push-remote", "git-no-verify", "git-remote-url", "agent-bypass",
)
# R102-0: 後のセッションを導く指示のファイル（書き換え＝以後の AI へのプロンプト注入）。どの段でも Mac だけ
INSTRUCTION_FILES = ("claude.md", "claude.local.md", "agents.md", "agents.override.md", "gemini.md",
                     "copilot-instructions.md", ".cursorrules", ".windsurfrules", ".clinerules")
# R102-0: push してよいリモート名（`public` 等の公開先・既定の追跡先が分からない素の `git push` は Mac）
PUSH_REMOTES = ("origin",)

# 自分の設定・鍵・許可の仕組みに触る語（大小無視・部分一致）。遠隔からは絶対に触らせない
SELF_WORDS = (
    "127.0.0.1:4780", "localhost:4780", "[::1]:4780", "x-office-local", "office_config", "office_devices",
    "office_approve", "office_remote_approve", "office_recipes", "office_relay", "office_inbox",
    "office_approvals", "office_templates", "office_actions_", ".claude/hooks", ".claude/settings",
    "launchagents", ".git/hooks", ".zshrc", ".zprofile", ".zshenv", ".bashrc", ".bash_profile", ".profile",
    "aioffice",
)
# 秘密の置き場（パスの成分・大小無視）。`.env` は接頭辞（.env.local 等）
SENSITIVE_PARTS = (
    ".ssh", ".aws", ".gnupg", ".netrc", ".npmrc", ".pypirc", ".git-credentials", ".docker", ".kube",
    ".claude", ".config", "library", "keychains", ".password-store",
)
SENSITIVE_SUFFIX = (".pem", ".key", ".p12", ".pfx", ".keychain", ".keychain-db")
SENSITIVE_PREFIX = (".env", "id_rsa", "id_ed25519", "id_ecdsa", "id_dsa", "credentials", "secrets")

ENV_OK = ("NO_COLOR", "FORCE_COLOR", "CI", "TZ", "LANG", "LC_ALL", "PYTHONDONTWRITEBYTECODE", "SKIP_UI")
ENV_VALUE = re.compile(r"^[A-Za-z0-9_./:=-]{0,64}$")
ASSIGN = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)=(.*)$", re.S)
REDIR = re.compile(r"^([0-9]?)(>>|>\||>|<)(.*)$", re.S)
REDIR_DUP = re.compile(r"^[0-9]?[<>]&[0-2-]$")
REMOTE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
BRANCH_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,199}$")
SCRIPT_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9:_.-]{0,63}$")
SOURCE_EXT = (".js", ".mjs", ".cjs", ".ts", ".tsx", ".jsx", ".sh", ".py", ".rb", ".pl")
GLOB_CHARS = set("*?[")
# glob の成分が秘密の置き場の名前に当たりうるか（`cred*`・`*.pem`・`*`）を確かめる見本（小文字）
SENSITIVE_SAMPLES = (
    ".env", ".env.local", ".ssh", ".aws", ".gnupg", ".netrc", ".npmrc", ".pypirc", ".git-credentials", ".docker",
    ".kube", ".claude", ".config", ".password-store", "library", "keychains", "credentials", "credentials.json",
    "secrets", "secrets.yml", "id_rsa", "id_ed25519", "id_ecdsa", "id_dsa", "key.pem", "server.key", "cert.p12",
    "cert.pfx", "login.keychain", "login.keychain-db",
)
SORT_FLAGS = re.compile(r"^-[bcCdfghiMmnrRsuVz]+$")
SORT_LONG = ("--numeric-sort", "--reverse", "--unique", "--ignore-case", "--human-numeric-sort", "--version-sort",
             "--month-sort", "--stable", "--general-numeric-sort", "--check", "--merge", "--dictionary-order",
             "--ignore-leading-blanks", "--zero-terminated", "--random-sort")

READ_CMDS = frozenset((
    "ls", "cat", "head", "tail", "wc", "grep", "rg", "diff", "pwd", "which", "echo", "printf", "date",
    "basename", "dirname", "sort", "uniq", "cut", "tr", "true", "false", "test", "[", "stat", "tree", "jq",
    "find", "sleep", ":", "mkdir",
))
TOOLS_FREE = frozenset((
    "Task", "Agent", "TaskCreate", "TaskUpdate", "TaskList", "TaskGet", "TaskOutput", "TaskStop",
    "KillShell", "BashOutput", "TodoWrite",
))
TOOLS_READ = frozenset(("Read", "Glob", "Grep", "LS"))
TOOLS_WEB = frozenset(("WebFetch", "WebSearch"))              # R102-2: review だけ（remote にはならない）
TOOLS_EDIT = {"Edit": "file_path", "Write": "file_path", "MultiEdit": "file_path", "NotebookEdit": "notebook_path"}


class Mac(Exception):
    """この形はスマホから許可しない（理由つき）。"""

    def __init__(self, reason):
        Exception.__init__(self, reason)
        self.reason = reason if reason in REASONS else "unreadable"


def input_canon(tool, tool_input):
    """表示と署名の正本の文字列（キーは整列・区切りは詰める・日本語はそのまま）。"""
    return json.dumps({"tool": tool, "input": tool_input}, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False)


def input_sha(canon):
    return hashlib.sha256(canon.encode("utf-8")).hexdigest()


def bad_chars(value):
    """制御文字・書式文字（U+202E 等）・私用・サロゲート・行/段落区切りが入っているか（`\\n` `\\t` は除く）。
    json.dumps は制御文字を \\uXXXX に逃がす＝正本の文字列でなく**値そのもの**を歩いて見る。"""
    if isinstance(value, str):
        for ch in value:
            if ch in "\n\t":
                continue
            if unicodedata.category(ch) in ("Cc", "Cf", "Co", "Cs", "Cn", "Zl", "Zp"):
                return True
        return False
    if isinstance(value, dict):
        return any(bad_chars(k) or bad_chars(v) for k, v in value.items())
    if isinstance(value, (list, tuple)):
        return any(bad_chars(v) for v in value)
    return False


_LEX = []


def _lex():
    if not _LEX:
        here = os.path.dirname(os.path.abspath(__file__))
        for p in (os.path.join(here, "office_shell_lex.py"), os.path.join(here, "..", "hooks", "office_shell_lex.py")):
            if os.path.isfile(p):
                spec = importlib.util.spec_from_file_location("_office_shell_lex_for_policy", p)
                mod = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(mod)
                _LEX.append(mod)
                break
        else:
            raise ImportError("office_shell_lex.py が見つからない")
    return _LEX[0]


_REVIEW = []


def _review():
    """拒否リスト（approval_review.py）を**ファイルの場所で**読む（hook の写しでは同じフォルダ・リポジトリでは server/）。"""
    if not _REVIEW:
        here = os.path.dirname(os.path.abspath(__file__))
        p = os.path.join(here, "approval_review.py")
        if not os.path.isfile(p):
            raise ImportError("approval_review.py が見つからない")
        spec = importlib.util.spec_from_file_location("_office_approval_review", p)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        _REVIEW.append(mod)
    return _REVIEW[0]


# R103-1（本人裁定 2026-10-02「Mac だけの操作も全文確認＋Face ID でスマホから」）: 「判定できなかった」理由＝スマホで全文を読んでも
# 実際に何が動くかが分からない（読めない・大きすぎる・未知の道具・解析できない構文・入れ子・動的に組み立てる・別の出力をそのまま実行・
# ダウンロードして実行）。これらは今までどおり Mac だけ（安全側に倒す）。それ以外（削除・送信・設定の変更…＝種類で決まった理由）は
# 全文を掲示に置き、スマホから毎回 Face ID で許可できる。hook（掲示・決定）と daemon（14 段の再判定）が同じこの関数を使う＝食い違わない（別モデルレビュー）。
UNJUDGED = frozenset({
    "unreadable", "too-large", "tool", "syntax", "nesting", "unreadable-syntax", "zsh-syntax",
    "dynamic", "dynamic-command", "pipe-exec", "download-exec",
})
LEVEL_RANK = {"remote": 0, "review": 1, "mac": 2}


def phone_approvable(level, reasons):
    """その段・理由の形を、スマホから許可してよいか（remote・review は ON の間タップ、mac は毎回 Face ID・UNJUDGED は Mac だけ）。"""
    if level in ("remote", "review"):
        return True
    if level != "mac":
        return False
    rs = [r for r in (reasons or []) if isinstance(r, str)]
    return bool(rs) and not (UNJUDGED & set(rs))


def classify(tool, tool_input, cwd, home):
    try:
        _gate(tool, tool_input)
    except Mac as e:
        return {"level": "mac", "reasons": [e.reason]}
    except Exception:
        return {"level": "mac", "reasons": ["unreadable"]}
    rv = None
    try:
        rv = _review()
        tags = rv.check(tool, tool_input, cwd, home, _AP)
    except Exception as e:
        if rv is not None and isinstance(e, rv.Deny):
            return {"level": "mac", "reasons": _all_reasons(rv, e.reason, tool, tool_input, cwd, home)}
        return {"level": "mac", "reasons": ["unreadable"]}
    try:
        _check(tool, tool_input, cwd, home)
        return {"level": "remote", "reasons": []}
    except Mac as e:
        return {"level": "review", "reasons": [e.reason], "tags": list(tags)}
    except Exception:
        return {"level": "mac", "reasons": ["unreadable"]}


def _all_reasons(rv, first, tool, tool_input, cwd, home):
    """R103-1（別モデルレビュー high）: 拒否の判定は最初の理由で止まる＝後ろの「判定できない」部分（`rm -rf b; eval "$X"`）を見ていない。
    Mac だけと決まったら全部を判定し直して理由を集める（先頭は今までと同じ最初の理由）。集められなければ unreadable を足す＝スマホでは許可しない。"""
    rs = [first if first in REASONS else "unreadable"]
    try:
        more = rv.check_all(tool, tool_input, cwd, home, _AP)
    except Exception:
        more = ["unreadable"]
    for r in more:
        r = r if r in REASONS else "unreadable"
        if r not in rs:
            rs.append(r)
    return rs


def _gate(tool, ti):
    """共通の門（どの段でも Mac）: 型・大きさ・制御文字・サンドボックス外し・知らないツール（MCP・計画の承認・質問）。"""
    if not isinstance(tool, str) or not isinstance(ti, dict):
        raise Mac("unreadable")
    try:
        size = len(input_canon(tool, ti).encode("utf-8"))
    except (TypeError, ValueError, UnicodeError):
        raise Mac("unreadable")
    if size > MAX_INPUT_BYTES:
        raise Mac("too-large")
    if bad_chars(tool) or bad_chars(ti):
        raise Mac("unreadable")
    if ti.get("dangerouslyDisableSandbox") not in (None, False):
        raise Mac("sandbox-off")
    if tool not in TOOLS_FREE and tool not in TOOLS_READ and tool not in TOOLS_EDIT and tool not in TOOLS_WEB \
            and tool != "Bash":
        raise Mac("tool")


def _check(tool, ti, cwd, home):
    if not isinstance(tool, str) or not isinstance(ti, dict):
        raise Mac("unreadable")
    try:
        size = len(input_canon(tool, ti).encode("utf-8"))
    except (TypeError, ValueError, UnicodeError):
        raise Mac("unreadable")
    if size > MAX_INPUT_BYTES:
        raise Mac("too-large")
    if bad_chars(tool) or bad_chars(ti):
        raise Mac("unreadable")
    if ti.get("dangerouslyDisableSandbox") not in (None, False):
        raise Mac("sandbox-off")
    if tool in TOOLS_FREE:
        return
    if tool not in TOOLS_READ and tool not in TOOLS_EDIT and tool != "Bash":
        raise Mac("tool")                                # WebFetch/WebSearch は review（1 段の許可リストに無い）
    scope = _Scope(cwd, home)
    if tool == "Bash":
        cmd = ti.get("command")
        if not isinstance(cmd, str) or not cmd.strip():
            raise Mac("unreadable")
        _bash(cmd, scope)
    elif tool in TOOLS_EDIT:
        p = ti.get(TOOLS_EDIT[tool])
        if not isinstance(p, str) or not p:
            raise Mac("unreadable")
        scope.path(p, write=True)
    else:
        # Read=file_path／LS=path／Glob=pattern（パスの glob）+path／Grep=glob（パスの glob）+path・pattern は正規表現（本文）
        paths = {"Read": ("file_path",), "LS": ("path",), "Glob": ("path",), "Grep": ("path",)}[tool]
        pats = {"Glob": ("pattern",), "Grep": ("glob",)}.get(tool, ())
        for key in paths + pats:
            v = ti.get(key)
            if v is None:
                continue
            if not isinstance(v, str):
                raise Mac("unreadable")
            if key in paths:
                scope.path(v)
            else:
                scope.pattern(v)


class _Scope:
    """cwd の中か・秘密の置き場か・自分の設定か。cwd が / やホームそのもの（とその祖先）は範囲なし＝全部 mac。"""

    def __init__(self, cwd, home):
        if not isinstance(cwd, str) or not os.path.isabs(cwd) or not isinstance(home, str) or not home:
            raise Mac("outside-cwd")
        self.root = os.path.realpath(cwd)
        h = os.path.realpath(home)
        if self.root == "/" or self.root == h or h.startswith(self.root.rstrip("/") + "/"):
            raise Mac("outside-cwd")
        self.home = h
        self.dirs = [self.root]
        _words(self.root[len(h):] if self.root.startswith(h + "/") else self.root)
        _parts(self.root[len(h):] if self.root.startswith(h + "/") else self.root)

    def inside(self, real):
        return real == self.root or real.startswith(self.root.rstrip("/") + "/")

    def path(self, p, write=False):
        """1 つのパス（絶対・~・相対）を確かめる。相対は cd で有り得る全部の場所から。
        write=True は中身を書く（R102-0: 1 段の許可＝remote では書かない＝全部 Mac・指示のファイルは `self`）、
        write="dir" はディレクトリを作るだけ（mkdir -p）。"""
        if "$" in p or "`" in p:
            raise Mac("syntax")
        if "{" in p and ("," in p or ".." in p):
            raise Mac("syntax")                          # ブレース展開は読まない
        _words(p)
        if p.startswith("~"):
            full = [os.path.expanduser(p)]
            if full[0].startswith("~"):
                raise Mac("outside-cwd")
        elif p.startswith("/"):
            full = [p]
        else:
            full = [os.path.join(d, p) for d in self.dirs]
        for f in full:
            norm = os.path.normpath(f)
            _parts(p)
            _parts(norm[len(self.root):] if norm.startswith(self.root) else norm)
            if norm == "/dev/null" and not write:
                continue
            if not (self.inside(norm) or self.inside(os.path.realpath(norm))):
                raise Mac("outside-cwd")                 # 字面でも実体でも cwd の外（/var と /private/var の両方を許す）
            cands = [norm]
            if GLOB_CHARS & set(p):
                hits = _glob.glob(norm)
                if len(hits) > 500:
                    raise Mac("syntax")
                cands += hits
                _glob_parts(p)                           # `.e*`・`cred*`・`*.pem` は秘密の置き場に当たりうる
            for c in cands:
                real = os.path.realpath(c)
                if not self.inside(real):
                    raise Mac("outside-cwd")
                rel = real[len(self.root):]
                _parts(rel)
                if write and any(seg.startswith(".") for seg in rel.split("/") if seg):
                    raise Mac("sensitive-path")          # .git/ .claude/ .github/ .husky/ .env 等へ書かない
                if write and os.path.basename(c).lower() in INSTRUCTION_FILES:
                    raise Mac("self")                    # CLAUDE.md 等＝以後の AI を導く文書
        if write is True:
            raise Mac("write")                           # 上書き・切り詰めは戻せないことがある＝1 段の許可にしない

    def pattern(self, pat):
        """Glob/Grep の検索パターン。絶対・~・.. を含むものは cwd の外を見うる。"""
        if pat.startswith(("/", "~")) or ".." in pat.split("/"):
            raise Mac("outside-cwd")
        _words(pat)
        _glob_parts(pat)

    def cd(self, arg):
        if arg in ("", "-") or arg.startswith(("/", "~")):
            raise Mac("outside-cwd")
        self.path(arg)
        new = [os.path.normpath(os.path.join(d, arg)) for d in self.dirs]
        self.dirs = list(dict.fromkeys(self.dirs + new))[:16]


def _words(text):
    low = text.lower()
    for w in SELF_WORDS:
        if w in low:
            raise Mac("self")


def _glob_parts(pat):
    """パス／glob の各成分: 字面は _parts、glob の成分は秘密の置き場の見本に当たるか（fnmatch は先頭の . を特別扱い
    しない＝`*` も .env に当たる扱い＝保守的）。Read と Grep の glob で判定が食い違わない（別モデルレビュー）。"""
    for seg in pat.lower().split("/"):
        if not seg or seg in (".", ".."):
            continue
        if GLOB_CHARS & set(seg):
            if seg == "**":
                continue
            # ドットで始まる glob（`.env.production*`・`.e*`）は隠しファイル＝全部 mac（見本に無い名前も当たる）
            if seg.startswith("."):
                raise Mac("sensitive-path")
            # 字面の頭と尻（`credentials.prod*` の頭・`*.pem` の尻）も _parts と同じ基準で
            head = seg[:min(seg.index(c) for c in GLOB_CHARS if c in seg)]
            tail = seg[max(seg.rindex(c) for c in GLOB_CHARS if c in seg) + 1:]
            if head:
                _parts("/" + head)
            if tail.endswith(SENSITIVE_SUFFIX):
                raise Mac("sensitive-path")
            if any(fnmatch.fnmatchcase(n, seg) for n in SENSITIVE_SAMPLES):
                raise Mac("sensitive-path")
        else:
            _parts("/" + seg)


def _parts(path):
    for seg in path.lower().split("/"):
        if not seg:
            continue
        if seg in SENSITIVE_PARTS or seg.endswith(SENSITIVE_SUFFIX) or seg.startswith(SENSITIVE_PREFIX):
            raise Mac("sensitive-path")


# ------------------------------------------------------------------ Bash

def _bash(cmd, scope):
    lex = _lex()
    why, redir = [], []
    segs, seps, _unsafe, _a, _s, _g = lex.split(cmd, why, redir)
    if [w for w in why if w != "or"] or "&" in seps:
        raise Mac("syntax")
    if not segs or len(redir) != sum(len(seg) for seg in segs):
        raise Mac("unreadable")
    k = 0
    for seg in segs:
        _segment([(t, redir[k + j]) for j, (t, _q) in enumerate(seg)], scope)
        k += len(seg)


def _segment(marked, scope):
    """marked=[(トークン, 引用の外の < > を含むか)]。"""
    toks = _redirects(marked, scope)
    if toks and toks[0] == "{":
        toks = toks[1:]
    if toks and toks[-1] == "}":
        toks = toks[:-1]
    if not toks or toks == ["<<"]:
        return
    for t in toks:
        if "{" in t and ("," in t or ".." in t):
            raise Mac("syntax")                          # ブレース展開（`{rm,-rf,x}`・`-{p,x}`）は読まない
    # 先頭の代入（NO_COLOR=1 npm test）
    while toks and ASSIGN.match(toks[0]):
        _assign(toks[0])
        toks = toks[1:]
    if not toks:
        return
    # 包み: time / nice は剥がす・env は許可した代入だけを伴う形
    while toks:
        w = toks[0]
        if w == "time":
            toks = toks[1:]
        elif w == "nice":
            toks = toks[1:]
            if toks and toks[0] == "-n" and len(toks) > 1 and toks[1].lstrip("-").isdigit():
                toks = toks[2:]
        elif w == "env":
            toks = toks[1:]
            if not toks or not ASSIGN.match(toks[0]):
                raise Mac("wrapper")
            while toks and ASSIGN.match(toks[0]):
                _assign(toks[0])
                toks = toks[1:]
        else:
            break
    if not toks:
        raise Mac("wrapper")
    word = toks[0]
    if "/" in word or "$" in word or "`" in word or "~" in word or GLOB_CHARS & set(word):
        raise Mac("command")
    name = word.lower()
    args = toks[1:]
    if name in ("sudo", "doas", "nohup", "caffeinate", "xargs", "exec", "eval", "source", ".", "command",
                "builtin", "alias", "hash", "enable", "unalias", "trap"):
        if name == "command" and len(args) == 2 and args[0] == "-v":
            return _plain_args(args[1:], scope)
        raise Mac("wrapper")
    if name in ("sh", "bash", "zsh", "dash", "fish", "ksh", "perl", "ruby", "osascript", "deno"):
        raise Mac("script")
    if name == "cd":
        if len(args) != 1:
            raise Mac("outside-cwd")
        return scope.cd(args[0])
    if name == "set":
        if all(re.match(r"^[-+][euxo]+$", a) or a == "pipefail" for a in args):
            return None
        raise Mac("command")
    if name == "export":
        for a in args:
            if not ASSIGN.match(a):
                raise Mac("env-prefix")
            _assign(a)
        return None
    if name in READ_CMDS:
        return _read_cmd(name, args, scope)
    if name == "git":
        return _git(args, scope)
    if name in ("npm", "pnpm", "yarn", "bun"):
        return _pkg(name, args, scope)
    if name == "pytest":
        return _plain_args(args, scope)
    if name in ("python3", "python"):
        if len(args) >= 2 and args[0] == "-m" and args[1] in ("pytest", "unittest", "py_compile", "json.tool"):
            if args[1] == "json.tool":
                pos = [a for a in args[2:] if not a.startswith("-")]
                if len(pos) >= 2:
                    scope.path(pos[1], write=True)       # 2 つめの位置引数は出力先（書き込み＝dotfile へは書かない）
            return _plain_args(args[2:], scope)
        raise Mac("script")
    if name == "node":
        if args and args[0] == "--test":
            rest = args[1:]
            for a in rest:
                if a.startswith("-") and not a.startswith("--test-"):
                    raise Mac("option")
            return _plain_args(rest, scope)
        if len(args) == 2 and args[0] == "--check":
            return _plain_args(args[1:], scope)
        raise Mac("script")
    raise Mac("command")


def _assign(tok):
    m = ASSIGN.match(tok)
    if not m or m.group(1) not in ENV_OK or not ENV_VALUE.match(m.group(2)):
        raise Mac("env-prefix")


def _redirects(marked, scope):
    """リダイレクトを取り除いて残りを返す。`2>&1` `>/dev/null` `</dev/null` と cwd 内の相対パスだけ。
    引用の外の `<` `>` を含むのに形が読めない語（`a>b`・`"">/tmp/x` の偽装）は全部 mac。
    引用の中の `<` `>`（コミットの文の `<noreply@…>`）は本文＝リダイレクトではない。"""
    toks = [t for t, _r in marked]
    out, i = [], 0
    while i < len(toks):
        t = toks[i]
        if not marked[i][1]:
            out.append(t)
            i += 1
            continue
        if REDIR_DUP.match(t):
            i += 1
            continue
        m = REDIR.match(t)
        if not m:
            raise Mac("redirect")
        target = m.group(3)
        if not target:
            if i + 1 >= len(toks):
                raise Mac("redirect")
            target = toks[i + 1]
            i += 1
        if "<" in target or ">" in target or target.startswith("&"):
            raise Mac("redirect")
        if target != "/dev/null":
            if target.startswith(("/", "~")) or ".." in target.split("/"):
                raise Mac("redirect")
            scope.path(target, write=m.group(2) != "<")
        i += 1
    return out


def _plain_args(args, scope, data=()):
    """引数を全部パスの候補として確かめる（`--opt=値` は値の側も）。data の位置は本文（コミットの文）。"""
    for i, a in enumerate(args):
        if i in data:
            continue
        if a.startswith("-"):
            if "$" in a or "`" in a:
                raise Mac("syntax")
            if "=" in a:
                scope.path(a.split("=", 1)[1])
            else:
                _words(a)
            continue
        scope.path(a)
        if "=" in a:
            scope.path(a.split("=", 1)[1])
    return None


def _read_cmd(name, args, scope):
    if name == "find":
        for a in args:
            if a.lower().startswith(("-delete", "-exec", "-ok", "-fprint", "-fls")):
                raise Mac("option")
    if name == "rg":
        for a in args:
            if a.startswith(("--pre", "--search-zip", "-z", "--hostname-bin")):
                raise Mac("option")                      # 外部プログラムを起こすオプション
    if name == "jq":
        for a in args:
            if "env" in a.lower() or "input_filename" in a:
                raise Mac("option")
    if name == "sort":
        # 許可リスト: `--compress-program=sh` は一時ファイルの圧縮に任意のコマンドを起こす（別モデルレビューで実行を再現）・
        # `-o`（書き出し先）・`-T`（一時置き場）・`-S` も読めない形として落とす
        skip = False
        for a in args:
            if skip:
                skip = False
                continue
            if not a.startswith("-") or a == "-":
                continue
            if a in ("-k", "-t"):
                skip = True
            elif a.startswith(("-k", "-t", "--key=", "--field-separator=")) or SORT_FLAGS.match(a) or a in SORT_LONG:
                continue
            else:
                raise Mac("option")
    if name in ("tree", "uniq"):
        for a in args:
            if a.startswith(("-o", "--output")):
                raise Mac("option")                      # 書き出し先を持つ（`tree -o .husky/pre-commit`）
        if name == "uniq" and len([a for a in args if not a.startswith("-")]) > 1:
            raise Mac("option")
    if name == "mkdir":
        for a in args:
            if a.startswith("-") and a not in ("-p", "-v"):
                raise Mac("option")
        for a in args:
            if not a.startswith("-"):
                scope.path(a, write="dir")
    return _plain_args(args, scope)


# ------------------------------------------------------------------ git

GIT_GLOBAL_OK = ("--no-pager", "-P", "--no-optional-locks")
GIT_READ = frozenset((
    "status", "diff", "log", "show", "blame", "rev-parse", "ls-files", "grep", "describe", "shortlog",
    "merge-base", "ls-tree", "name-rev", "cherry", "count-objects", "cat-file", "rev-list",
))


def _git(args, scope):
    i = 0
    while i < len(args) and args[i].startswith("-"):
        if args[i] not in GIT_GLOBAL_OK:
            raise Mac("git-option")
        i += 1
    if i >= len(args):
        raise Mac("command")
    sub, rest = args[i], args[i + 1:]
    for a in rest:
        if a.startswith(("--exec-path", "--git-dir", "--work-tree", "--config-env", "--namespace", "--upload-pack",
                         "--receive-pack", "--exec=", "--ext-diff", "--textconv", "--filters", "--output",
                         "--open-files-in-pager")):
            raise Mac("git-option")
    if sub in GIT_READ:
        if sub == "grep" and any(a.startswith("-O") for a in rest):
            raise Mac("git-option")                      # -O<pager> はコマンドを起こす
        return _plain_args(rest, scope)
    if sub == "branch":
        for a in rest:
            if a in ("-d", "-D", "-m", "-M", "-c", "-C", "-f", "-u", "--delete", "--move", "--copy", "--force",
                     "--edit-description", "--unset-upstream") or a.startswith(("--set-upstream", "--track")):
                raise Mac("git-option")
        return _plain_args(rest, scope)
    if sub == "tag":
        for a in rest:
            if a in ("-d", "--delete", "-f", "--force") or a.startswith("-F"):
                raise Mac("git-option")
        return _plain_args(rest, scope)
    if sub == "remote":
        if not rest or rest[0] in ("-v", "--verbose") or rest[0] in ("show", "get-url"):
            return _plain_args(rest, scope)
        raise Mac("git-option")
    if sub == "stash":
        if not rest or rest[0] in ("list", "show", "push", "save") or rest[0].startswith("-"):
            data = _msg_positions(rest)
            for a in rest:
                if a in ("pop", "apply", "drop", "clear", "branch", "create", "store"):
                    raise Mac("git-option")
            return _plain_args(rest, scope, data)
        raise Mac("git-option")
    if sub == "worktree":
        if rest[:1] == ["list"]:
            return _plain_args(rest, scope)
        raise Mac("git-option")
    if sub == "config":
        reads = ("--get", "--get-all", "--get-regexp", "--list", "-l")
        ok = ("--show-origin", "--show-scope", "--global", "--local", "--name-only", "-z", "--null", "--includes")
        if not any(a in reads for a in rest):
            raise Mac("git-option")
        pos = [a for a in rest if not a.startswith("-")]
        if any(a.startswith("-") and a not in reads + ok for a in rest) or len(pos) > 2:
            raise Mac("git-option")
        return _plain_args(rest, scope)
    if sub == "reflog":
        if not rest or rest[0] == "show" or rest[0].startswith("-"):
            return _plain_args(rest, scope)
        raise Mac("git-option")
    if sub == "add":
        return _plain_args(rest, scope)
    if sub == "commit":
        return _git_commit(rest, scope)
    if sub == "checkout":
        if len(rest) in (2, 3) and rest[0] == "-b" and all(BRANCH_NAME.match(a) for a in rest[1:]):
            return None
        raise Mac("git-option")
    if sub == "switch":
        pos = [a for a in rest if not a.startswith("-")]
        flags = [a for a in rest if a.startswith("-")]
        if any(f not in ("-c", "--create", "--detach", "-q") for f in flags) or not 1 <= len(pos) <= 2:
            raise Mac("git-option")
        if not all(BRANCH_NAME.match(a) for a in pos):
            raise Mac("git-option")
        return None
    if sub == "fetch":
        return _git_net(rest, ("-q", "-v", "--quiet", "--verbose", "--tags", "--no-tags"), scope)
    if sub == "pull":
        if "--ff-only" not in rest:
            raise Mac("git-option")
        return _git_net(rest, ("--ff-only", "-q", "-v", "--quiet", "--verbose", "--no-rebase"), scope)
    if sub == "restore":
        if not ("--staged" in rest or "-S" in rest) or any(a in ("--worktree", "-W") for a in rest):
            raise Mac("git-option")
        for a in rest:
            if a.startswith("-") and a not in ("--staged", "-S", "-q", "--quiet", "--"):
                raise Mac("git-option")
        return _plain_args([a for a in rest if a != "--"], scope)
    if sub == "push":
        return _git_net(rest, ("-u", "--set-upstream", "-q", "-v", "--quiet", "--verbose", "--dry-run", "-n"), scope,
                        push=True)
    raise Mac("command")


def _msg_positions(rest):
    """`-m 本文`・`--message=本文` の本文の位置（パスとして見ない）。"""
    out = set()
    for i, a in enumerate(rest):
        if a in ("-m", "--message", "-am") and i + 1 < len(rest):
            out.add(i + 1)
        elif a.startswith("--message=") or (a.startswith("-m") and len(a) > 2 and not a.startswith("--")):
            out.add(i)
    return out


def _git_commit(rest, scope):
    ok = ("-a", "--all", "-q", "--quiet", "-s", "--signoff", "-v", "--verbose", "--allow-empty", "--no-edit",
          "-m", "--message", "-am")
    data = _msg_positions(rest)
    if not data:
        raise Mac("git-option")                          # 本文なしはエディタが開く（遠隔では意味がない）
    for i, a in enumerate(rest):
        if i in data:
            if a.startswith("--message="):
                continue
            if a.startswith("-m") and len(a) > 2:
                continue
            continue
        if a.startswith("-") and a not in ok:
            raise Mac("git-option")                      # --no-verify / -n / --amend / -F / -c …
    return _plain_args(rest, scope, data)


def _git_net(rest, flags_ok, scope, push=False):
    pos = []
    for a in rest:
        if a.startswith("-"):
            if a not in flags_ok:
                raise Mac("git-option")                  # -f / --force* / --mirror / --delete / --prune / -o …
        else:
            pos.append(a)
    if pos and not REMOTE_NAME.match(pos[0]):
        raise Mac("git-option")                          # URL・ローカルパス・user@host:
    if push and (not pos or pos[0] not in PUSH_REMOTES):
        raise Mac("push-target")                         # `public`（公開リポ）・素の push（追跡先が見えない）
    for ref in pos[1:]:
        if not BRANCH_NAME.match(ref) or ".." in ref or ref.endswith(("/", ".lock")):
            raise Mac("git-option")                      # `+main`（強制）・`:branch`（削除）・`a:b`
    return None


# ------------------------------------------------------------------ パッケージの試験

def _pkg(name, args, scope):
    flags_ok = ("-s", "--silent", "-q", "--quiet")
    if "--" in args:
        k = args.index("--")
        head, tail = args[:k], args[k + 1:]
    else:
        head, tail = args, []
    head = [a for a in head if a not in flags_ok]
    if any(a.startswith("-") for a in head):
        raise Mac("option")                              # --prefix / --script-shell / --userconfig …
    if head[:1] == ["test"] and len(head) <= 1:
        pass
    elif head[:1] == ["run"] and len(head) == 2 and SCRIPT_NAME.match(head[1]) and not head[1].lower().endswith(SOURCE_EXT):
        pass
    elif name in ("pnpm", "yarn", "bun") and len(head) == 1 and head[0] == "test":
        pass
    else:
        raise Mac("command")                             # install / ci / exec / npx / run <ファイル> …
    return _plain_args(tail, scope)


# R102-2: 拒否リスト（approval_review.check）へ渡す、この判定の部品（循環 import をしない＝名前空間で渡す）
class _Parts(object):
    pass


_AP = _Parts()
for _k in ("SELF_WORDS", "SENSITIVE_SUFFIX", "SENSITIVE_PREFIX", "SENSITIVE_SAMPLES", "TOOLS_EDIT", "TOOLS_READ",
           "TOOLS_FREE", "INSTRUCTION_FILES", "_lex", "_msg_positions"):
    setattr(_AP, _k, globals()[_k])
