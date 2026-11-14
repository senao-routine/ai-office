# -*- coding: utf-8 -*-
"""R102-2: スマホ許可の 3 段（remote / review / mac）の**拒否リスト**側（純関数・標準ライブラリ・Python 3.9 の構文）。

3 段の境界（R102-2 で分け、R103-1・R114 の本人裁定で今の形＝CLAUDE.md 不変条件 7。スマホから許可できるのは ON の間だけ）:
安全と言い切れる形（approval_policy の許可リスト＝remote）と、安全と言い切れないが危険とも分かっていない形（review）は**タップ**、
危険と分かっている形（ここの拒否リスト＝mac）は**毎回 Face ID**、判定できなかった形（approval_policy.UNJUDGED）は **Mac だけ**。

check(tool, ti, cwd, home, ap) -> 警告のタグ（sorted list・review で画面に出す）。拒否リストに当たれば Deny(reason)。
  - **approval_policy.classify は remote の判定より先にこれを通す**（remote は「拒否に当たらない」の真部分集合＝攻撃側レビュー F2）。
  - 見るのは本文に書かれた形だけ（`npm test` の中身・台本の中身は見ない＝名前と旗で推すだけ）。ファイルシステムは realpath・
    存在の確認・git の設定ファイル（push の宛先）だけ読む。
  - 置換（`$( )`・`` ` ` ``・`<( )`）・配列（`P=( … )`）・ヒアドキュメントは前処理で取り出し、中身を同じ手順で見る（深さ 4 まで）。
  - Claude Code の Bash は zsh で走る＝zsh の語の途中の `(`（glob 修飾子）・`${(`・`$~`・`=(` は読まない（Deny）。
  - 残るリスク（docs/security-notes.md R102-2）: 拒否リストは本質的に不完全・インラインコードの走査は正規表現・中身の見えない台本・
    判定と実行の時間差・Worker 侵害時は偽の画面で本人に許可を押させうる（許可ごとの Mac 通知・監査・ON の期限が残る防壁）。
"""
import fnmatch
import os
import re
import unicodedata

MAX_DEPTH = 4


class Deny(Exception):
    """この形はスマホから許可しない（拒否リスト・理由つき）。"""

    def __init__(self, reason):
        Exception.__init__(self, reason)
        self.reason = reason if reason in REASONS else "unreadable"


# 拒否の理由（固定の enum・監査で集計する・PWA が日本語の文にする）
REASONS = (
    "unreadable", "nesting", "unreadable-syntax", "zsh-syntax", "redirect", "tool",
    "delete", "system", "privilege", "process", "secret", "network", "network-send", "network-listen",
    "install", "publish", "dynamic", "dynamic-command", "wrapper", "env-danger",
    "pipe-exec", "download-exec", "script-name", "script-option", "option",
    "self", "sensitive-path", "system-path", "protected-write", "symlink",
    "code-delete", "code-process", "code-network", "code-dynamic", "code-privilege", "code-secret",
    "git-config", "git-destructive", "git-other", "git-push-force", "git-push-remote", "git-no-verify", "git-remote-url",
    "agent-bypass",
)
# review で見せる警告のタグ（固定の enum）
TAGS = (
    "outside-cwd", "script", "inline-code", "write", "exec-file", "dynamic-arg", "git-history", "git-push",
    "network-get", "background", "unknown-command", "xargs", "agent",
)

SELF_EXTRA = ("localhost:8789", "127.0.0.1:8789", ".github/workflows", ".husky/", ".envrc")
SECRET_SEGS = {".ssh", ".aws", ".gnupg", ".netrc", ".npmrc", ".pypirc", ".git-credentials", ".docker", ".kube",
               "keychains", ".password-store"}
HOME_DENY_TOP = SECRET_SEGS | {".config", ".local", ".gitconfig", ".oh-my-zsh"}
# ~/.claude の下で読んでよい所（書くのは jobs/*/tmp だけ）。transcript（projects）・office_*・settings・hooks・private は全部 Mac
CLAUDE_HOME_READ_OK = {"scripts", "skills", "commands", "agents", "plans", "claude.md", "imgcache", "jobs"}
PROTECTED_WRITE_SEGS = {".git", ".github", ".husky", ".vscode", ".idea", ".claude", ".circleci"}
PROTECTED_WRITE_NAMES = {".envrc", ".gitattributes", ".gitmodules", ".yarnrc", ".yarnrc.yml", ".pnpmfile.cjs",
                         ".npmrc", ".bashrc", ".zshrc", ".profile", ".zprofile", ".zshenv", ".bash_profile",
                         ".tool-versions"}
# 書いた後に実行・解釈される正本（書くのは review だけど画面で目立たせる＝攻撃側レビュー F3）
EXEC_FILES = {"makefile", "gnumakefile", "package.json", "pyproject.toml", "setup.py", "setup.cfg", "conftest.py",
              "tox.ini", "noxfile.py", "justfile", "rakefile", "gemfile", "dockerfile", "procfile", "cargo.toml",
              "go.mod", "deno.json", "build.gradle", "pom.xml", "verify.sh", "dev.sh"}
EXEC_SUFFIX = (".sh", ".bash", ".zsh", ".command", ".tool", ".plist")
SYSTEM_WRITE = ("/etc", "/private/etc", "/usr", "/bin", "/sbin", "/System", "/Library", "/Applications",
                "/private/var/db", "/opt/homebrew", "/dev", "/var")
DEV_OK = ("/dev/null", "/dev/stdout", "/dev/stderr", "/dev/tty")
TMP_OK = ("/tmp", "/private/tmp", "/private/var/folders", "/var/folders")
DANGER_ENV = re.compile(r"^(PATH|LD_\w+|DYLD_\w+|BASH_ENV|ENV|ZDOTDIR|IFS|PROMPT_COMMAND|PS4|SHELLOPTS|BASHOPTS|"
                        r"GIT_SSH|GIT_SSH_COMMAND|GIT_EXEC_PATH|GIT_DIR|GIT_WORK_TREE|GIT_CONFIG\w*|GIT_ASKPASS|"
                        r"SSH_ASKPASS|EDITOR|VISUAL|PAGER|GIT_PAGER|GIT_EDITOR|NODE_OPTIONS|PYTHONSTARTUP|PYTHONHOME|"
                        r"PYTHONPATH|PERL5OPT|PERL5LIB|RUBYOPT|RUBYLIB|HOME|npm_config_\w+|NPM_CONFIG_\w+|BROWSER|"
                        r"HISTFILE|FPATH|CDPATH)$")
ASSIGN = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)(\+?)=(.*)$", re.S)
RISKY_NAME = {"deploy", "publish", "release", "upload", "post", "tweet", "send", "notify", "mail", "install",
              "uninstall", "setup", "kickstart", "push", "delete", "remove", "purge", "wipe", "clean", "cleanup",
              "reset", "rollback", "drop", "destroy", "prod", "production"}
RISKY_FLAG = re.compile(r"^--?(deploy|publish|release|upload|post|send|push|delete|remove|purge|wipe|prod|production|"
                        r"live|force|yes|apply|commit|execute|confirm)(=|$|-)", re.I)

ALWAYS = {
    # 削除・破棄
    "rm": "delete", "unlink": "delete", "shred": "delete", "srm": "delete", "trash": "delete", "rmtrash": "delete",
    "truncate": "delete", "rmdir": "delete",
    # ディスク
    "dd": "system", "diskutil": "system", "hdiutil": "system", "fdisk": "system", "asr": "system", "tmutil": "system",
    "newfs_apfs": "system", "newfs_hfs": "system",
    # 権限
    "sudo": "privilege", "doas": "privilege", "su": "privilege", "chown": "privilege", "chgrp": "privilege",
    "chflags": "privilege", "xattr": "privilege", "spctl": "privilege", "csrutil": "privilege", "dscl": "privilege",
    "sysadminctl": "privilege", "visudo": "privilege",
    # プロセス・システム
    "kill": "process", "killall": "process", "pkill": "process", "launchctl": "system", "security": "secret",
    "osascript": "system", "crontab": "system", "at": "system", "batch": "system", "shutdown": "system",
    "reboot": "system", "halt": "system", "pmset": "system", "systemsetup": "system", "networksetup": "system",
    "scutil": "system", "softwareupdate": "install", "installer": "install", "tccutil": "system",
    "profiles": "system", "nvram": "system", "bless": "system", "kextload": "system", "kextunload": "system",
    "shortcuts": "system", "automator": "system", "tmux": "system", "screen": "system",
    # ネットワーク送信
    "ssh": "network", "scp": "network", "sftp": "network", "ftp": "network", "nc": "network", "ncat": "network",
    "netcat": "network", "socat": "network", "telnet": "network", "mosh": "network", "mail": "network",
    "mailx": "network", "sendmail": "network", "http": "network", "https": "network", "xh": "network",
    "psql": "network", "mysql": "network", "mongo": "network", "mongosh": "network", "redis-cli": "network",
    # インストール（パッケージを取ってきて実行する）
    "bunx": "install", "pnpx": "install", "uvx": "install", "pipx": "install", "gem": "install",
    "mas": "install", "port": "install", "apt": "install", "apt-get": "install", "yum": "install",
    "dnf": "install", "conda": "install", "corepack": "install",
    # 公開・クラウド
    "firebase": "publish", "vercel": "publish", "netlify": "publish", "flyctl": "publish", "fly": "publish",
    "heroku": "publish", "gcloud": "publish", "aws": "publish", "az": "publish", "terraform": "publish",
    "pulumi": "publish", "kubectl": "publish", "helm": "publish", "twine": "publish", "vsce": "publish",
    "ovsx": "publish", "pod": "publish", "fastlane": "publish", "glab": "publish", "hub": "publish",
    # 動的な実行・シェルの状態・シェルを起こせるエディタ
    "eval": "dynamic", "trap": "dynamic", "alias": "dynamic", "unalias": "dynamic", "enable": "dynamic",
    "bind": "dynamic", "complete": "dynamic", "parallel": "dynamic",
    "vi": "dynamic", "vim": "dynamic", "nvim": "dynamic", "emacs": "dynamic", "nano": "dynamic", "ed": "dynamic",
    "ex": "dynamic", "pico": "dynamic",
    # 秘密をまとめて出す
    "pbpaste": "secret", "screencapture": "secret", "history": "secret", "gpg": "secret", "age": "secret",
    "pass": "secret", "op": "secret", "bw": "secret", "lpass": "secret",
}
WRAPPERS = {"time", "nice", "nohup", "timeout", "gtimeout", "env", "command", "builtin", "exec", "caffeinate",
            "stdbuf", "xargs", "watch", "flock", "unbuffer", "chronic"}
SAFE_BUILTINS = {"cd", "pushd", "popd", "export", "declare", "local", "readonly", "typeset", "set", "shopt", "unset",
                 "read", "printf", "echo", "true", "false", ":", "return", "exit", "break", "continue", "shift",
                 "wait", "type", "ulimit", "umask", "let", "getopts", "jobs", "fg", "bg", "test", "[", "[[", "]]",
                 "hash", "disown", "sleep", "pwd", "which"}
KNOWN_BIN = {"ls", "cat", "head", "tail", "wc", "grep", "rg", "diff", "pwd", "which", "echo", "printf", "date",
             "basename", "dirname", "sort", "uniq", "cut", "tr", "stat", "tree", "jq", "sleep", "mkdir", "sed", "awk",
             "cp", "mv", "ln", "touch", "file", "ffmpeg", "ffprobe", "sips", "shasum", "md5", "du", "df", "ps",
             "pgrep", "lsof", "uptime", "cmp", "comm", "paste", "column", "fold", "nl", "od", "xxd", "hexdump",
             "strings", "base64", "iconv", "less", "more", "open", "pbcopy", "say", "afplay", "mdls",
             "mdfind", "plutil", "zip", "unzip", "tar", "gzip", "gunzip", "make", "tee", "realpath", "readlink",
             "seq", "yes", "expr", "bc", "env", "id", "whoami", "hostname", "uname", "sw_vers", "sysctl", "top",
             "vm_stat", "magick", "convert", "identify", "pdftotext", "pdftoppm", "qpdf", "textutil", "qlmanage",
             "defaults", "git", "npm", "pnpm", "yarn", "pip", "pip3", "uv", "brew", "cargo", "go",
             "gh", "wrangler", "curl", "wget", "rsync", "find", "chmod", "codex", "claude", "sqlite3", "docker",
             "tsc", "esbuild", "vite", "pytest", "jest", "vitest", "playwright", "xcodebuild", "xcrun", "swiftc",
             "clang", "gcc", "cc", "patch", "split", "csplit", "join", "look", "rev", "tac", "shuf", "numfmt",
             "printenv", "locale", "tput", "clear", "mktemp", "install", "ditto", "cksum", "sum",
             "time", "wait", "test", "true", "false"}
SHELLS = {"sh", "bash", "zsh", "dash", "ksh", "fish"}
PY_RE = re.compile(r"^(python|pypy)(\d+(\.\d+)?)?$")

CODE_DENY_PY = [
    ("delete", r"\bos\.(remove|unlink|rmdir|removedirs|truncate)\b|\bshutil\.rmtree\b|\.unlink\(|\.rmdir\(|send2trash"),
    ("process", r"\bsubprocess\b|\bos\.(system|popen|exec\w*|spawn\w*|posix_spawn\w*|fork|kill|killpg)\b|\bpty\b|"
                r"\bctypes\b|\bsignal\.(pthread_kill)|\bmultiprocessing\b"),
    ("network", r"\b(socket|urllib|urllib3|http\.client|requests|httpx|aiohttp|smtplib|ftplib|paramiko|telnetlib|"
                r"xmlrpc|websocket|websockets|boto3|googleapiclient|webbrowser)\b"),
    # 文字列から API を組み立てる形（`getattr(os, 'rem' + 'ove')`・`__dict__[…]`）・任意の py ファイルの実行（runpy）
    ("dynamic", r"(?<![\w.])(eval|exec|compile)\s*\(|__import__|\bimportlib\b|\bmarshal\b|\brunpy\b|"
                r"\bgetattr\s*\(|\b__dict__\b|\bglobals\s*\(\s*\)|\b__builtins__\b|\bbuiltins\b|\bpickle\b"),
    ("privilege", r"\bos\.(chmod|chown|setuid|setgid|lchown)\b"),
    # 環境変数（秘密）をまとめて吸い出す形。名前を字面で書いた 1 個の読み（os.environ.get("X")・os.environ["X"]）は通す
    ("secret", r"\bos\.environ\b(?!\s*(\.get\s*\(|\[)\s*['\"][A-Za-z_][A-Za-z0-9_]*['\"])|\bfrom\s+os\s+import\s+[^\n]*\benviron\b"),
]
CODE_DENY_JS = [
    ("delete", r"\bfs\w*\.(rm|rmSync|unlink|unlinkSync|rmdir|rmdirSync|truncate\w*)\b|\brimraf\b|"
               r"\.(rm|rmSync|unlink|unlinkSync|rmdir|rmdirSync|truncate|truncateSync|ftruncate|ftruncateSync|emptyDir|"
               r"emptyDirSync|remove|removeSync)\s*\("),
    ("process", r"child_process|\bspawn(Sync)?\s*\(|\bexec(Sync|File|FileSync)?\s*\(|process\.kill|"
                r"worker_threads|process\.binding"),
    ("network", r"\bfetch\s*\(|\bhttps?\.(request|get)\b|require\(['\"](node:)?(net|http|https|dgram|tls)['\"]|"
                r"from\s+['\"](node:)?(net|http|https|dgram|tls)['\"]|\bWebSocket\b|\bXMLHttpRequest\b|axios"),
    ("dynamic", r"(?<![\w.])eval\s*\(|new\s+Function\s*\(|\bvm\.run|\bvm\.Script|\b(global|globalThis|window|self)\s*\[|"
                r"\bimport\s*\(|\brequire\s*\(\s*[^'\"\s)]"),
    ("secret", r"\bprocess\.env\b(?!\.[A-Za-z_][A-Za-z0-9_]*)"),
]
CODE_DENY_GENERIC = [
    ("process", r"\bsystem\s*\(|\bexec\s*\(|`|\bopen\s*\(\s*['\"]\||\bpopen\b|\bIPC::|\bspawn\b|%x"),
    ("delete", r"\bunlink\b|\brmtree\b|\bFileUtils\.rm|\bFile\.delete"),
    ("network", r"\bSocket\b|\bNet::|\bLWP\b|\bHTTP::|\bopen-uri\b|\bcurl\b"),
    ("dynamic", r"\beval\b"),
    ("secret", r"\bENV\b|%ENV|\$ENV|getenv\s*\(\s*\)"),
]
# 書き込みの API（インラインコードの中の文字列を書き込みのパスとして見る＝攻撃側レビュー F4）
CODE_WRITE = re.compile(
    r"open\s*\([^)]*['\"][wax+]|\.write_(text|bytes)\s*\(|\bos\.(rename|replace|symlink|link|makedirs|mkdir)\b|"
    r"\bshutil\.(copy\w*|move)\b|\.(rename|replace|symlink_to|hardlink_to|touch)\s*\(|"
    r"\bfs\w*\.(writeFile\w*|appendFile\w*|rename\w*|copyFile\w*|cp\w*|symlink\w*|link\w*|createWriteStream|mkdir\w*)\b|"
    r"\.(writeFile|writeFileSync|appendFile|appendFileSync|rename|renameSync|copyFile|copyFileSync|cp|cpSync|symlink|"
    r"symlinkSync|link|linkSync|createWriteStream|writeSync|truncateSync|outputFile|outputFileSync)\s*\(|"
    r"\bFile\.(write|open)|\bIO\.write|>>?\s*['\"]")
CODE_STR = re.compile(r"'([^'\\\n]{1,300})'|\"([^\"\\\n]{1,300})\"")
SENSITIVE_IN_CODE = re.compile(
    r"(^|[/'\"~\s])\.(ssh|aws|gnupg|netrc|npmrc|pypirc|git-credentials|docker|kube|password-store)\b|"
    r"\bid_(rsa|ed25519|ecdsa|dsa)\b|/Keychains\b|['\"/]\.env(\.[\w.-]+)?['\"]|credentials\.json|\.pem\b|\.p12\b",
    re.I)


class Ctx:
    def __init__(self, cwd, home, ap):
        self.ap = ap
        self.cwd = os.path.realpath(cwd) if isinstance(cwd, str) and os.path.isabs(cwd) else None
        self.home = os.path.realpath(home)
        self.dirs = [self.cwd or self.home]
        self.vars = {"HOME": self.home, "PWD": self.cwd or "", "TMPDIR": "/private/tmp"}
        self.warn = set()
        self.downloaded = set()
        self.funcs = set()
        self.collect = None          # R103-1: 全部の理由を集める（Deny を区切り・置換ごとに受け止めて先へ進む）


# ------------------------------------------------------------------ 前処理（置換・配列・ヒアドキュメント）

def _read_delim(text, j):
    """<< の直後から終端語を読む → (word, quoted, dash, 次の位置) / 読めなければ None。"""
    dash = text[j:j + 1] == "-"
    j += 1 if dash else 0
    while j < len(text) and text[j] in " \t":
        j += 1
    word, q, quoted = [], "", False
    while j < len(text):
        ch = text[j]
        if q:
            if ch == q:
                q = ""
            else:
                word.append(ch)
        elif ch == "$" and text[j + 1:j + 2] in ("'", '"'):
            return None
        elif ch in "'\"":
            q = ch
            quoted = True
        elif ch == "\\" and j + 1 < len(text):
            j += 1
            word.append(text[j])
            quoted = True
        elif ch in " \t\n;&|<>()":
            break
        else:
            word.append(ch)
        j += 1
    if q or not word:
        return None
    return "".join(word), quoted, dash, j


def prepass(text, depth=0):
    """引用を尊重して、コマンド置換・プロセス置換・配列・ヒアドキュメントを取り出して印の語に置き換える。
    返り値= (置き換えた本文, subs=[(kind, body)], heredocs={id: (quoted, body)}, arrays=[body])。読めない形は Deny。"""
    if depth > MAX_DEPTH:
        raise Deny("nesting")
    out, subs, heredocs, arrays = [], [], [], []
    pending = []
    n = len(text)
    ph = [0]

    def find_close(k, open_depth=1):
        """k は `(` の直後。対応する `)` の位置（引用・入れ子・ヒアドキュメント・バッククォートを尊重）。"""
        d, q, pend = open_depth, "", []
        while k < n:
            c = text[k]
            if q == "'":
                if c == "'":
                    q = ""
                k += 1
                continue
            if c == "\\":
                k += 2
                continue
            if q == '"':
                if c == '"':
                    q = ""
                elif text[k:k + 2] == "$(":
                    e = find_close(k + 2)
                    k = e + 1
                    continue
                elif c == "`":
                    e = text.find("`", k + 1)
                    if e < 0:
                        raise Deny("unreadable-syntax")
                    k = e + 1
                    continue
                k += 1
                continue
            if c in "'\"":
                q = c
            elif c == "`":
                e = text.find("`", k + 1)
                if e < 0:
                    raise Deny("unreadable-syntax")
                k = e + 1
                continue
            elif c == "#" and (k == 0 or text[k - 1] in " \t\n;(|&"):
                nl = text.find("\n", k)
                k = n if nl < 0 else nl
                continue
            elif c == "<" and text[k:k + 2] == "<<" and text[k:k + 3] != "<<<":
                r = _read_delim(text, k + 2)
                if r is None:
                    raise Deny("unreadable-syntax")
                pend.append(r)
                k = r[3]
                continue
            elif c == "\n" and pend:
                p = k + 1
                for word, _quoted, dash, _ in pend:
                    while True:
                        nl = text.find("\n", p)
                        line = text[p:] if nl < 0 else text[p:nl]
                        if (line.lstrip("\t") if dash else line) == word:
                            p = n if nl < 0 else nl + 1
                            break
                        if nl < 0:
                            raise Deny("unreadable-syntax")
                        p = nl + 1
                pend = []
                k = p
                continue
            elif c == "(":
                d += 1
            elif c == ")":
                d -= 1
                if d == 0:
                    return k
            k += 1
        raise Deny("unreadable-syntax")

    def at_word_start(k):
        return k == 0 or text[k - 1] in " \t\n;&|("

    def placeholder(kind):
        ph[0] += 1
        return "__%s%d__" % (kind, ph[0])

    i, quote = 0, ""
    while i < n:
        c = text[i]
        if quote == "'":
            out.append(c)
            if c == "'":
                quote = ""
            i += 1
            continue
        if c == "\\" and i + 1 < n:
            out.append(text[i:i + 2])
            i += 2
            continue
        if quote == '"':
            if c == '"':
                quote = ""
                out.append(c)
                i += 1
                continue
            if text[i:i + 3] == "$((":
                e = find_close(i + 3, 2)
                _t2, s2, _h2, _a2 = prepass(text[i + 3:e - 1], depth + 1)
                subs += s2
                out.append("0")
                i = e + 1
                continue
            if text[i:i + 2] == "$(":
                e = find_close(i + 2)
                subs.append(("subst", text[i + 2:e]))
                out.append(placeholder("S"))
                i = e + 1
                continue
            if c == "`":
                e = text.find("`", i + 1)
                if e < 0:
                    raise Deny("unreadable-syntax")
                subs.append(("subst", text[i + 1:e].replace("\\`", "`")))
                out.append(placeholder("S"))
                i = e + 1
                continue
            out.append(c)
            i += 1
            continue
        # 引用の外
        if c == "$" and text[i:i + 2] == "$'":
            e = i + 2
            while e < n and text[e] != "'":
                e += 2 if text[e] == "\\" else 1
            out.append(text[i:e + 1])
            i = e + 1
            continue
        if c in "'\"":
            quote = c
            out.append(c)
            i += 1
            continue
        if c == "#" and at_word_start(i):
            nl = text.find("\n", i)
            i = n if nl < 0 else nl
            continue
        if text[i:i + 3] == "$((":
            e = find_close(i + 3, 2)
            _t2, s2, _h2, _a2 = prepass(text[i + 3:e - 1], depth + 1)
            subs += s2
            out.append("0")
            i = e + 1
            continue
        if text[i:i + 2] == "$(":
            e = find_close(i + 2)
            subs.append(("subst", text[i + 2:e]))
            out.append(placeholder("S"))
            i = e + 1
            continue
        if c == "`":
            e = text.find("`", i + 1)
            if e < 0:
                raise Deny("unreadable-syntax")
            subs.append(("subst", text[i + 1:e].replace("\\`", "`")))
            out.append(placeholder("S"))
            i = e + 1
            continue
        if c in "<>" and text[i + 1:i + 2] == "(":
            e = find_close(i + 2)
            subs.append(("procsubst", text[i + 2:e]))
            out.append("/dev/fd/63")
            i = e + 1
            continue
        if c == "<" and text[i:i + 3] == "<<<":
            raise Deny("unreadable-syntax")      # ヒアストリング（読まない）
        if c == "<" and text[i:i + 2] == "<<":
            r = _read_delim(text, i + 2)
            if r is None:
                raise Deny("unreadable-syntax")
            word, quoted, dash, j = r
            hid = len(heredocs) + len(pending)
            pending.append((word, quoted, dash, hid))
            out.append(" __H%d__ " % hid)
            i = j
            continue
        m = re.match(r"([A-Za-z_][A-Za-z0-9_]*)\+?=\(", text[i:]) if at_word_start(i) else None
        if m:                                    # 配列 NAME=( … )（今の字句は関数定義と取り違えた）
            e = find_close(i + m.end())
            t2, s2, _h2, _a2 = prepass(text[i + m.end():e], depth + 1)
            subs += s2
            arrays.append(t2)
            out.append("%s=__ARRAY__" % m.group(1))
            i = e + 1
            continue
        if c == "\n" and pending:
            out.append("\n")
            p = i + 1
            for word, quoted, dash, hid in pending:
                body = []
                while True:
                    nl = text.find("\n", p)
                    line = text[p:] if nl < 0 else text[p:nl]
                    if (line.lstrip("\t") if dash else line) == word:
                        p = n if nl < 0 else nl + 1
                        break
                    if nl < 0:
                        raise Deny("unreadable-syntax")
                    if not quoted and line.endswith("\\"):
                        raise Deny("unreadable-syntax")
                    body.append(line.lstrip("\t") if dash else line)
                    p = nl + 1
                b = "\n".join(body)
                if not quoted:
                    # 終端を引用しない本文は展開される＝置換を取り出す（二重引用符の中と同じ扱い）
                    _t2, s2, _h2, _a2 = prepass('"' + b.replace('"', "'") + '"', depth + 1)
                    subs += s2
                heredocs.append((hid, quoted, b))
            pending = []
            i = p
            continue
        out.append(c)
        i += 1
    if quote or pending:
        raise Deny("unreadable-syntax")
    return "".join(out), subs, {hid: (q, b) for hid, q, b in heredocs}, arrays


# ------------------------------------------------------------------ パスの検査

def _pathlike(t):
    return ("/" in t or t.startswith(("~", ".")) or re.search(r"\.[A-Za-z0-9]{1,8}$", t) is not None
            or re.search(r":\d{2,5}\b", t) is not None)


def self_words(t, ctx):
    low = t.lower()
    for w in tuple(ctx.ap.SELF_WORDS) + SELF_EXTRA:
        if w in low:
            raise Deny("self")


def resolve_vars(t, ctx):
    def rep(m):
        name = m.group(1) or m.group(2)
        if name in ctx.vars and ctx.vars[name] is not None:
            return ctx.vars[name]
        return "\x00"
    return re.sub(r"\$\{([A-Za-z_][A-Za-z0-9_]*)[^}]*\}|\$([A-Za-z_][A-Za-z0-9_]*)", rep, t)


def check_path(p, ctx, write=False):
    """1 つのパス（らしきもの）を確かめる。秘密・自分の設定・書いてはいけない場所なら Deny。"""
    if not p:
        return
    if p.startswith("file://"):
        p = p[7:]
    r = resolve_vars(p, ctx)
    if "\x00" in r or "__S" in r:
        ctx.warn.add("dynamic-arg")
    lit = r.replace("\x00", "")
    self_words(lit, ctx)
    ap = ctx.ap
    for s in [s for s in lit.lower().split("/") if s]:
        if s in SECRET_SEGS or s.endswith(ap.SENSITIVE_SUFFIX) or s.startswith(ap.SENSITIVE_PREFIX):
            raise Deny("sensitive-path")
        if any(ch in s for ch in "*?["):
            if s.startswith("."):
                raise Deny("sensitive-path")
            head = re.split(r"[*?\[]", s, maxsplit=1)[0]
            tail = re.split(r"[*?\]]", s)[-1]
            if (head and head.startswith(ap.SENSITIVE_PREFIX)) or (tail and tail.endswith(ap.SENSITIVE_SUFFIX)):
                raise Deny("sensitive-path")
            if s != "**" and any(fnmatch.fnmatchcase(x, s) for x in ap.SENSITIVE_SAMPLES):
                raise Deny("sensitive-path")     # `cred*`・`id_*`＝秘密の置き場の名前に当たりうる glob
    if r.startswith("\x00") or r.startswith("__S"):
        if write:
            ctx.warn.add("write")
        return          # 値の見えない変数で始まる＝場所は分からない（警告だけ）
    if lit.startswith("~"):
        full0 = os.path.expanduser(lit.replace("~", ctx.home, 1)) if lit.startswith("~/") or lit == "~" else lit
        if full0.startswith("~"):
            raise Deny("sensitive-path")     # ~other-user
        fulls = [full0]
    elif lit.startswith("/"):
        fulls = [lit]
    else:
        fulls = [os.path.join(d, lit) for d in ctx.dirs]
    for f in fulls:
        norm = os.path.normpath(f)
        real = os.path.realpath(norm)
        both = {norm, real}
        if real != norm:                         # リンクの先が秘密の置き場（readme.txt → .env）＝名前で見た判定を解決先にも
            for seg in [x for x in real.lower().split("/") if x]:
                if seg in SECRET_SEGS or seg.endswith(ap.SENSITIVE_SUFFIX) or seg.startswith(ap.SENSITIVE_PREFIX):
                    raise Deny("sensitive-path")
        if ctx.cwd and not any(x == ctx.cwd or x.startswith(ctx.cwd.rstrip("/") + "/") for x in both):
            ctx.warn.add("outside-cwd")          # 字面でも実体でも cwd の外（/var と /private/var のどちらでも中なら中）
        for full in both:
            _check_abs(full, ctx, write)
    if write:
        ctx.warn.add("write")
        base = os.path.basename(lit.rstrip("/")).lower()
        if base in EXEC_FILES or base.endswith(EXEC_SUFFIX):
            ctx.warn.add("exec-file")


def _check_abs(full, ctx, write):
    home = ctx.home
    self_words(full, ctx)
    if full.startswith(home + "/") or full == home:
        rel = full[len(home) + 1:]
        parts = rel.split("/") if rel else []
        top = parts[0].lower() if parts else ""
        job_tmp = top == ".claude" and len(parts) >= 4 and parts[1] == "jobs" and parts[3] == "tmp"
        if write and (not parts or top.startswith(".") or top == "bin") and not job_tmp:
            raise Deny("sensitive-path" if top != ".claude" else "self")
        if top == ".claude" and not job_tmp:
            second = parts[1].lower() if len(parts) > 1 else ""
            if write or second not in CLAUDE_HOME_READ_OK:
                raise Deny("self")
        if top == "library":
            second = parts[1].lower() if len(parts) > 1 else ""
            if second != "cloudstorage":
                raise Deny("sensitive-path")
        if top in HOME_DENY_TOP:
            raise Deny("sensitive-path")
    if write:
        rel = full[len(home) + 1:] if full.startswith(home + "/") else ""
        jt = rel.split("/")[:4] and len(rel.split("/")) >= 4 and rel.split("/")[0] == ".claude" \
            and rel.split("/")[1] == "jobs" and rel.split("/")[3] == "tmp"
        if not jt and full not in DEV_OK and not full.startswith("/dev/fd/") and full.startswith(SYSTEM_WRITE) \
                and not full.startswith(TMP_OK):
            raise Deny("system-path")
        segs = [s.lower() for s in full.split("/") if s]
        if any(s in PROTECTED_WRITE_SEGS for s in segs) or (segs and segs[-1] in PROTECTED_WRITE_NAMES):
            raise Deny("protected-write")
        if ctx.cwd and (full == ctx.cwd or full.startswith(ctx.cwd.rstrip("/") + "/")):
            rel_cwd = full[len(ctx.cwd):]
            if any(s.startswith(".") for s in rel_cwd.split("/") if s):
                raise Deny("protected-write")    # プロジェクトの dotfile・dot フォルダ（.pre-commit-config.yaml・.gitignore 等＝設定）
        if segs and segs[-1] in ctx.ap.INSTRUCTION_FILES:
            raise Deny("self")                   # CLAUDE.md 等＝以後の AI を導く文書（どの段でも Mac）


def scan_tokens(toks, ctx, skip=()):
    for i, t in enumerate(toks):
        if i in skip:
            continue
        for part in ([t] + t.split("=", 1)[1:]):
            if _pathlike(part) or any(ch in part for ch in "*?["):
                check_path(part, ctx)            # glob（`cred*`・`id_*`）も秘密の置き場に当たりうる


def code_scan(code, lang, ctx):
    """インラインコード（`python -c`・heredoc の本文・`node -e`）。危険な API の名前は Deny・書き込みの API があれば
    文字列の中のパスを書き込みとして見る（`open('.git/config','a')`・`writeFileSync('CLAUDE.md', …)`）。
    難読化（組み立てた名前）は原理的に取りこぼす＝インラインコードは review 止まり（remote にはならない）。"""
    ctx.warn.add("inline-code")
    code = unicodedata.normalize("NFKC", code)
    low = code.lower()
    for w in tuple(ctx.ap.SELF_WORDS) + SELF_EXTRA:
        if w in low:
            raise Deny("self")
    if re.search(r"\.claude\b", code) and re.search(r"~|home\(\)|HOME|expanduser|/Users/", code):
        if not re.search(r"\.claude/(scripts|skills|commands|agents|plans|jobs/[^/]+/tmp|imgcache)\b", code):
            raise Deny("self")
    if SENSITIVE_IN_CODE.search(code):
        raise Deny("sensitive-path")
    table = {"py": CODE_DENY_PY, "js": CODE_DENY_JS}.get(lang, CODE_DENY_GENERIC)
    for kind, pat in table:
        if re.search(pat, code):
            raise Deny("code-" + kind)
    writes = bool(CODE_WRITE.search(code))
    for m in CODE_STR.finditer(code):
        s = m.group(1) if m.group(1) is not None else m.group(2)
        if s and _pathlike(s) and not re.match(r"^[a-z][a-z0-9+.-]*://", s, re.I):
            check_path(s, ctx, write=writes)


def name_tokens(base):
    b = re.sub(r"\.[A-Za-z0-9]+$", "", base)
    b = re.sub(r"([a-z])([A-Z])", r"\1_\2", b)
    return {w.lower() for w in re.split(r"[^A-Za-z0-9]+", b) if w}


def script_rule(path, args, ctx):
    if path.startswith("/dev/") or path == "-":
        raise Deny("pipe-exec")              # <( … ) や /dev/stdin を台本として実行＝中身は別コマンドの出力
    check_path(path, ctx)
    base = os.path.basename(path)
    only_help = bool(args) and all(a in ("--help", "-h", "--version", "-V") for a in args)
    if name_tokens(base) & RISKY_NAME and not only_help:
        raise Deny("script-name")
    if base in ctx.downloaded:
        raise Deny("download-exec")
    for a in args:
        if RISKY_FLAG.match(a):
            raise Deny("script-option")
    ctx.warn.add("script")
    scan_tokens(args, ctx)


# ------------------------------------------------------------------ Bash

def bash_review(cmd, ctx, depth=0):
    if depth > MAX_DEPTH:
        raise Deny("nesting")
    text, subs, heredocs, arrays = prepass(cmd, depth)
    for _kind, body in subs:
        _each(ctx, lambda body=body: bash_review(body, ctx, depth + 1))
    lx = ctx.ap._lex()
    for a in arrays:
        segs = lx.split(a)[0]
        scan_tokens([t for s in segs for t, _q in s], ctx)
    # zsh の読み方: 語の途中の `(` は glob 修飾子（`*(e:'rm x':)` はコードを実行する）・`${(e)x}`・`$~x`・`=(…)` は読まない。
    # `name()` の関数定義だけ許す
    bare = re.sub(r"'[^']*'", "''", text)
    bare = re.sub(r'"(?:[^"\\]|\\.)*"', '""', bare)
    for m in re.finditer(r"[^\s;&|(<>]\(", bare):
        words = bare[max(0, m.start() - 40):m.end() + 1].split()
        if not (words and re.match(r"^[A-Za-z_][\w-]*\(\)$", words[-1])):
            raise Deny("zsh-syntax")
    if "${(" in text or "$~" in text or "=(" in bare:
        raise Deny("zsh-syntax")
    why, redir = [], []
    segs, seps, _unsafe, _a, _s, _g = lx.split(text, why, redir)
    if any(w in ("herestring", "heredoc", "procsubst") for w in why):
        raise Deny("unreadable-syntax")
    if "bg" in why or "&" in seps:
        ctx.warn.add("background")
    if len(redir) != sum(len(s) for s in segs):
        raise Deny("unreadable-syntax")
    k = 0
    for si, seg in enumerate(segs):
        marked = [(t, redir[k + j], q) for j, (t, q) in enumerate(seg)]
        k += len(seg)
        prev_sep = seps[si - 1] if si > 0 else ""
        _each(ctx, lambda marked=marked, prev_sep=prev_sep: _one_segment(marked, ctx, depth, prev_sep, heredocs),
              [t for t, _r, _q in marked])


def _one_segment(marked, ctx, depth, prev_sep, heredocs):
    toks, hds = strip_redirects(marked, ctx, heredocs)
    if _truncates(marked, toks):
        raise Deny("delete")                 # `: > f`・`> f`・`true > f`＝中身を消す（削除と同じ扱い）
    if toks and toks[0] in ("case", "select"):
        raise Deny("unreadable-syntax")    # case 文は読まない（パターンの `)` が字句で消える）
    analyze(toks, ctx, depth, piped=(prev_sep == "|"), heredocs=hds)


# R103-1（別モデルレビュー high）: 拒否の判定は最初の理由で止まる＝`rm -rf b; eval "$X"` が「削除」だけに見えて、判定できない eval が
# スマホの 2 段で通った。Mac だけと決まった要求は、区切り（; && || |）と置換（$( ) ` `）ごとに全部を判定し直して理由を集める。
# 理由を出した区切りの残り（その区切りの判定は途中で止まっている）に別のコードを動かせる語があれば「判定できない」（dynamic）を足す。
UNJUDGED_HERE = frozenset({"unreadable", "too-large", "tool", "syntax", "nesting", "unreadable-syntax", "zsh-syntax",
                           "dynamic", "dynamic-command", "pipe-exec", "download-exec"})
EXEC_WORDS = frozenset({"eval", "exec", "source", ".", "xargs", "osascript", "awk", "gawk", "nawk", "node", "deno",
                        "bun", "ruby", "perl", "php", "lua", "rscript", "swift", "tsx", "ts-node", "expect", "parallel"})
EXEC_FLAGS = frozenset({"-exec", "-execdir", "-ok", "-okdir", "--to-command", "--use-compress-program", "--rsh",
                        "--rsync-path", "--checkpoint-action", "--info-script", "--new-volume-script"})


def _exec_marks(toks):
    for t in toks:
        low = unicodedata.normalize("NFKC", os.path.basename(str(t))).lower()
        if (low in SHELLS or low in EXEC_WORDS or PY_RE.match(low) or low in EXEC_FLAGS
                or any(low.startswith(f + "=") for f in EXEC_FLAGS) or "__s" in low
                or "sshcommand" in low or "core.pager" in low or "alias." in low):
            return True
    return False


def _each(ctx, fn, toks=None):
    if ctx.collect is None:
        return fn()
    try:
        fn()
    except Deny as e:
        ctx.collect.append(e.reason)
        if toks is not None and e.reason not in UNJUDGED_HERE and _exec_marks(toks[1:]):
            ctx.collect.append("dynamic")
    except RecursionError:
        ctx.collect.append("nesting")
    return None


def _truncates(marked, toks):
    """書き込みのリダイレクト（`>`・`>|`・`>&`以外）を持ち、コマンドが空・`:`・`true` の段＝ファイルを空にするだけ。"""
    if toks and toks[0] not in (":", "true"):
        return False
    for t, r, _q in marked:
        if r and re.match(r"^[0-9]?(>\||>)(?!>)", t) and not re.match(r"^[0-9]?>&", t):
            return True
    return False


def strip_redirects(marked, ctx, heredocs):
    out, hds, i = [], [], 0
    while i < len(marked):
        t, r, q = marked[i]
        m = re.match(r"^__H(\d+)__$", t)
        if m and not q:
            got = heredocs.get(int(m.group(1)))
            if got is None:
                raise Deny("unreadable-syntax")
            hds.append(got)
            i += 1
            continue
        if not r:
            out.append(t)
            i += 1
            continue
        if re.match(r"^[0-9]?[<>]&[0-9-]?$", t) or re.match(r"^&?>&?[0-9]$", t):
            i += 1
            continue
        mm = re.match(r"^([0-9]?|&)(>>|>\||>|<)(.*)$", t, re.S)
        if not mm:
            raise Deny("redirect")
        target = mm.group(3)
        if not target:
            if i + 1 >= len(marked):
                raise Deny("redirect")
            target = marked[i + 1][0]
            i += 1
        if target.startswith("&"):
            i += 1
            continue
        if "<" in target or ">" in target:
            raise Deny("redirect")
        check_path(target, ctx, write=mm.group(2) != "<")
        i += 1
    return out, hds


KEYWORDS_STRIP = {"if", "then", "else", "elif", "do", "while", "until", "!", "{", "time", "coproc"}
KEYWORDS_END = {"fi", "done", "esac", "}", ";;"}


def analyze(toks, ctx, depth, piped=False, heredocs=()):
    while toks and (toks[0] in KEYWORDS_STRIP or toks[0] in KEYWORDS_END):
        if toks[0] == "coproc":
            raise Deny("unreadable-syntax")
        toks = toks[1:]
    while toks and toks[-1] == "}":
        toks = toks[:-1]
    if not toks:
        return None
    if toks[0] == "for":
        scan_tokens(toks[1:], ctx)
        return None
    if toks[0] == "function":
        toks = toks[2:]
        if toks and toks[0] == "{":
            toks = toks[1:]
        return analyze(toks, ctx, depth, piped, heredocs)
    # 関数定義 name() { body …（字句で `(` `)` が消えて name { body… になる）
    if len(toks) >= 2 and toks[1] == "{":
        ctx.funcs.add(toks[0].lower())
        return analyze(toks[2:], ctx, depth, piped, heredocs)
    if "{" in toks[1:]:
        j = toks.index("{", 1)
        analyze(toks[:j], ctx, depth, piped, heredocs)
        return analyze(toks[j + 1:], ctx, depth, False, ())
    if toks[0] in ("[[", "[", "test", "(("):
        scan_tokens(toks[1:], ctx)
        return None
    while toks and ASSIGN.match(toks[0]):
        assign(toks[0], ctx)
        toks = toks[1:]
    if not toks:
        return None
    return command(toks, ctx, depth, piped, heredocs)


def assign(tok, ctx):
    m = ASSIGN.match(tok)
    name, val = m.group(1), m.group(3)
    if DANGER_ENV.match(name):
        raise Deny("env-danger")
    r = resolve_vars(val, ctx)
    ctx.vars[name] = None if ("__S" in val or "\x00" in r or val == "__ARRAY__") else r
    if _pathlike(r.replace("\x00", "")):
        check_path(val, ctx)


def unwrap(name, args):
    """包みを剥がして中身のコマンドを返す（None=中身なし）。"""
    a = list(args)
    if name in ("time", "nohup", "command", "builtin", "exec", "unbuffer", "chronic"):
        while a and a[0].startswith("-"):
            if name == "command" and a[0] == "-v":
                return None
            a = a[1:]
        return a
    if name == "nice":
        if a[:1] == ["-n"]:
            a = a[2:]
        elif a and re.match(r"^-\d+$", a[0]):
            a = a[1:]
        return a
    if name in ("timeout", "gtimeout"):
        while a and a[0].startswith("-"):
            a = a[2:] if a[0] in ("-s", "-k", "--signal", "--kill-after") else a[1:]
        return a[1:]
    if name == "env":
        while a and (a[0].startswith("-") or ASSIGN.match(a[0])):
            if a[0] in ("-S", "--split-string") or a[0].startswith("-S"):
                raise Deny("dynamic")
            if a[0] in ("-u", "-C", "-P"):
                a = a[2:]
                continue
            if ASSIGN.match(a[0]) and DANGER_ENV.match(ASSIGN.match(a[0]).group(1)):
                raise Deny("env-danger")
            a = a[1:]
        return a
    if name == "caffeinate":
        while a and a[0].startswith("-"):
            a = a[2:] if a[0] in ("-t", "-w") else a[1:]
        return a
    if name == "stdbuf":
        while a and a[0].startswith("-"):
            a = a[1:]
        return a
    if name == "xargs":
        while a and a[0].startswith("-"):
            if a[0] in ("-I", "-J", "-L", "-n", "-P", "-s", "-E", "-d", "-a", "-R", "-S"):
                a = a[2:]
            else:
                a = a[1:]
        return a or ["echo"]
    if name == "watch":
        while a and a[0].startswith("-"):
            a = a[2:] if a[0] in ("-n", "-d") else a[1:]
        return a
    if name == "flock":
        while a and a[0].startswith("-"):
            a = a[2:] if a[0] in ("-w", "-E") else a[1:]
        return a[1:]
    return a


def command(toks, ctx, depth, piped, heredocs):
    word = toks[0]
    args = toks[1:]
    if "__S" in word or "__ARRAY__" in word:
        raise Deny("dynamic-command")
    r = resolve_vars(word, ctx)
    if "\x00" in r:
        raise Deny("dynamic-command")
    if any(ch in r for ch in "*?[$\\") or ("{" in r and ("," in r or ".." in r)) or r.startswith("="):
        raise Deny("dynamic-command")
    if "/" in r or r.startswith("~"):
        check_path(r, ctx)
    # 全角・互換文字で名前を隠す形（`ｒｍ`）は NFKC で正規の名前に戻してから表を引く（攻撃側レビュー F7）
    name = unicodedata.normalize("NFKC", os.path.basename(r))
    low = name.lower()
    if low in ctx.funcs:
        scan_tokens(args, ctx)
        return None
    if low in ALWAYS:
        raise Deny(ALWAYS[low])
    if low in WRAPPERS:
        if low == "exec" and not args:
            return None
        inner = unwrap(low, args)
        if inner is None:
            scan_tokens(args, ctx)
            return None
        if low == "xargs":
            ctx.warn.add("xargs")
        if not inner:
            raise Deny("wrapper")
        return analyze(inner, ctx, depth, piped, heredocs)
    if low in ("source", "."):
        if not args:
            raise Deny("unreadable-syntax")
        return script_rule(args[0], args[1:], ctx)
    if low in SHELLS:
        return shell_rule(args, ctx, depth, piped, heredocs)
    if PY_RE.match(low):
        return interp_rule("py", args, ctx, depth, piped, heredocs)
    if low in ("node", "deno", "bun", "ruby", "perl", "php", "lua", "rscript", "swift", "tsx", "ts-node"):
        return other_interp(low, args, ctx, depth, piped, heredocs)
    if "/" in r and low not in KNOWN_BIN:
        return script_rule(r, args, ctx)
    rule = RULES.get(low)
    if rule:
        return rule(args, ctx, depth, piped, heredocs)
    if low == "cd":
        return cd(args, ctx)
    if low == "pushd":
        return cd([x for x in args if not x.startswith(("-", "+"))][:1], ctx)
    if low == "popd":
        return None                              # 戻り先はこれまでの場所のどれか（dirs は増える一方＝全部から見る）
    if low in ("printenv", "set", "export", "declare", "typeset", "env") and (not args or args[0] in ("-p", "-x")):
        raise Deny("secret")                 # 環境変数を全部出す＝鍵を画面へ
    if low in ("export", "declare", "local", "readonly", "typeset"):
        for a in args:
            if ASSIGN.match(a):
                assign(a, ctx)
        return None
    if low not in SAFE_BUILTINS and low not in KNOWN_BIN:
        ctx.warn.add("unknown-command")
    scan_tokens(args, ctx)
    return None


def cd(args, ctx):
    a = [x for x in args if x not in ("-P", "-L")]
    if not a or a[0] == "-":
        ctx.dirs.append(ctx.home)
        return
    p = resolve_vars(a[0], ctx)
    if "\x00" in p:
        ctx.warn.add("dynamic-arg")
        return
    check_path(p, ctx)
    ctx.dirs = list(dict.fromkeys(ctx.dirs + _dirs_for(p, ctx)))[:16]


def _dirs_for(p, ctx):
    """cd／git -C の行き先（相対なら今あり得る全部の場所から）。"""
    if p.startswith("~"):
        new = [p.replace("~", ctx.home, 1) if p.startswith("~/") or p == "~" else p]
    elif p.startswith("/"):
        new = [p]
    else:
        new = [os.path.join(d, p) for d in ctx.dirs]
    return [os.path.normpath(x) for x in new]


SHELL_FLAGS = set("abefhkmnptuvxBCEHPTil")            # 値を取らない 1 文字の旗（c・s・o・O は別に扱う）
SHELL_LONG = ("--norc", "--noprofile", "--login", "--posix", "--noediting", "--verbose", "--restricted")


def _stdin_body(heredocs, piped, ctx, depth, run):
    if heredocs:
        for _q, body in heredocs:
            run(body)
        return None
    if piped:
        raise Deny("pipe-exec")                  # 前の段の出力をプログラムとして読む
    return None


def shell_rule(args, ctx, depth, piped, heredocs):
    """sh/bash/zsh の引数: 旗の束（`-lc`・`-ec`）は 1 文字ずつ読み、c があれば次の語を**コマンドとして**同じ手順で見る。
    知らない旗・長い旗は読めない＝Mac（`bash -lc "rm -rf x"` が台本の名前として素通りした＝別モデルレビュー）。
    c／s は旗を**全部読み終えてから**決める（シェルは `-s -c`・`-c -e` のどちらでも旗の後の最初の語をコマンドにする。
    `bash -es -c 'rm x'` を s の時点で標準入力扱いにして、コマンドを見なかった＝別モデルレビュー 2 回目）。"""
    a = list(args)
    i = 0
    cmd_mode = stdin_mode = False
    while i < len(a):
        t = a[i]
        if t in ("-o", "+o", "-O", "+O"):
            i += 2
            continue
        if t == "--":
            i += 1
            break
        if t.startswith("--"):
            if t in SHELL_LONG:
                i += 1
                continue
            raise Deny("option")
        if t[:1] in ("-", "+") and len(t) > 1:
            flags = t[1:]
            for ch in flags:
                if ch not in SHELL_FLAGS and ch not in ("c", "s"):
                    raise Deny("option")
            if t[0] == "+" and ("c" in flags or "s" in flags):
                raise Deny("option")
            cmd_mode = cmd_mode or "c" in flags
            stdin_mode = stdin_mode or "s" in flags
            i += 1
            continue
        break
    a = a[i:]
    if cmd_mode:
        if not a:
            raise Deny("unreadable-syntax")
        ctx.warn.add("inline-code")
        bash_review(a[0], ctx, depth + 1)
        scan_tokens(a[1:], ctx)
        return None
    if stdin_mode or not a or a[0] == "-":
        scan_tokens(a[1:] if a[:1] == ["-"] else a, ctx)
        return _stdin_body(heredocs, piped, ctx, depth, lambda b: (ctx.warn.add("inline-code"),
                                                                   bash_review(b, ctx, depth + 1)))
    return script_rule(a[0], a[1:], ctx)


PY_FLAGS = set("bBdEhiIOqRsSuvV")                    # 値を取らない（c・m・W・X は別）


def _py_module(mod, rest, ctx):
    if mod in ("pip", "ensurepip") and not (rest[:1] and rest[0] in ("list", "show", "freeze", "check", "--version")):
        raise Deny("install")
    if mod == "http.server" and not any(x in rest for x in ("127.0.0.1", "localhost", "::1")):
        raise Deny("network-listen")
    if mod in ("smtpd", "smtplib", "ftplib", "webbrowser", "http.client", "urllib.request", "runpy", "code", "pdb"):
        raise Deny("network" if mod in ("smtpd", "smtplib", "ftplib", "webbrowser", "http.client", "urllib.request")
                   else "code-dynamic")
    if mod == "playwright" and rest[:1] == ["install"]:
        raise Deny("install")
    if mod not in ("pytest", "unittest", "json.tool", "py_compile", "compileall", "doctest", "venv", "timeit"):
        ctx.warn.add("script")
    for x in rest:
        if RISKY_FLAG.match(x):
            raise Deny("script-option")
    scan_tokens(rest, ctx)
    return None


def interp_rule(lang, args, ctx, depth, piped, heredocs):
    """python の引数: 旗の束を 1 文字ずつ読む（`-c"…"`・`-Ic "…"`・`-mpip`）。知らない旗は Mac。"""
    a = list(args)
    i = 0
    while i < len(a):
        t = a[i]
        if t == "-" or not t.startswith("-") or len(t) < 2:
            break
        if t.startswith("--"):
            if t in ("--version", "--help"):
                return None
            raise Deny("option")
        j, adv = 1, 1
        while j < len(t):
            ch = t[j]
            if ch in ("c", "m"):
                val = t[j + 1:] if j + 1 < len(t) else (a[i + 1] if i + 1 < len(a) else None)
                if val is None:
                    raise Deny("unreadable-syntax")
                rest = a[i + 1:] if j + 1 < len(t) else a[i + 2:]
                if ch == "c":
                    code_scan(val, lang, ctx)
                    scan_tokens(rest, ctx)
                    return None
                return _py_module(val, rest, ctx)
            if ch in ("W", "X"):
                adv = 1 if j + 1 < len(t) else 2          # 値は同じ語の残りか次の語
                break
            if ch in ("V", "h"):
                return None
            if ch not in PY_FLAGS:
                raise Deny("option")
            j += 1
        i += adv
    a = a[i:]
    if not a or a[0] == "-":
        scan_tokens(a[1:], ctx)
        return _stdin_body(heredocs, piped, ctx, depth, lambda b: code_scan(b, lang, ctx))
    return script_rule(a[0], a[1:], ctx)


NODE_OK = ("--test", "--check", "--version", "--no-warnings", "--no-deprecation", "--enable-source-maps",
           "--input-type", "--stack-size", "--max-old-space-size", "--experimental-vm-modules",
           "--experimental-strip-types", "--trace-warnings", "--trace-uncaught", "--watch")
# 値を取る旗（`--input-type module` の別の語も値＝値を台本の名前と取り違えて後ろの -e を見なかった＝別モデルレビュー 2 回目）
NODE_VAL = ("--input-type", "--stack-size", "--max-old-space-size")
NODE_EVAL = ("-e", "--eval", "-p", "--print", "-r", "--require", "--import", "--loader", "--experimental-loader")
GENERIC_FLAGS = set("nplawWsTtcxd0")                 # perl/ruby の値を取らない旗（e・E は code・i は書く）


def other_interp(low, args, ctx, depth, piped, heredocs):
    lang = "js" if low in ("node", "deno", "bun", "tsx", "ts-node") else "generic"
    a = list(args)
    if low == "bun":
        if a[:1] and a[0] in ("install", "i", "add", "remove", "rm", "x", "update", "upgrade", "link", "create",
                              "init", "publish", "pm"):
            raise Deny("install")
        if a[:1] and a[0] in ("test", "build"):
            ctx.warn.add("script")
            scan_tokens(a[1:], ctx)
            return None
        if a[:1] == ["run"]:
            a = a[1:]
    if low == "deno" and a[:1] and a[0] in ("install", "publish", "upgrade", "compile"):
        raise Deny("install")
    if low == "deno" and a[:1] and a[0] in ("run", "test", "eval"):
        if a[0] == "eval":
            code_scan(" ".join(a[1:]), "js", ctx)
            return None
        a = a[1:]
    if low == "swift" and a[:1] and a[0] in ("build", "test", "run", "package"):
        ctx.warn.add("script")
        scan_tokens(a[1:], ctx)
        return None
    i = 0
    while i < len(a):
        t = a[i]
        if t == "-" or not t.startswith("-") or len(t) < 2:
            break
        if lang == "js":
            name = t.split("=", 1)[0]
            code, rest = None, None
            if t.startswith(("--eval=", "--print=")):
                code, rest = t.split("=", 1)[1], a[i + 1:]
            elif name in ("-e", "--eval", "-p", "--print", "-pe", "-ep"):
                code, rest = (a[i + 1] if i + 1 < len(a) else ""), a[i + 2:]
            elif not t.startswith("--") and len(t) > 2 and t[1] in ("e", "p"):
                code, rest = t[2:], a[i + 1:]    # `-e"…"`（くっつけた形）
            if code is not None:
                code_scan(code, lang, ctx)
                scan_tokens(rest, ctx)
                return None
            if name in ("-r", "--require", "--import", "--loader", "--experimental-loader") or \
                    name.startswith(("--require", "--import", "--loader", "--experimental-loader")):
                raise Deny("code-dynamic")       # 前もって別のファイルを読み込ませる
            if name in ("--test", "--check", "-c", "--version", "-v") or name.startswith("--test"):
                for x in a[i + 1:]:              # 後ろにコードの旗があれば読めない（`--test --test-reporter spec -e …`）
                    xn = x.split("=", 1)[0]
                    if xn in NODE_EVAL or xn.startswith(NODE_EVAL[5:]) or \
                            (x.startswith("-") and not x.startswith("--") and len(x) > 2 and x[1] in ("e", "p", "r")):
                        raise Deny("code-dynamic")
                ctx.warn.add("script")
                scan_tokens(a[i + 1:], ctx)
                return None
            if name in NODE_OK:
                if name in NODE_VAL and "=" not in t:
                    if i + 1 >= len(a):
                        raise Deny("unreadable-syntax")
                    i += 2
                    continue
                i += 1
                continue
            raise Deny("option")
        # ruby / perl / php …: 旗の束。e／E は次（か残り）がコード・i は書き込み・I／r／M／m はほかのファイルを読む
        flags = t[1:]
        for k, ch in enumerate(flags):
            if ch in ("e", "E"):
                code = flags[k + 1:] or (a[i + 1] if i + 1 < len(a) else "")
                code_scan(code, lang, ctx)
                scan_tokens(a[i + 1:] if flags[k + 1:] else a[i + 2:], ctx)
                return None
            if ch == "i":
                ctx.warn.add("write")
                break
            if ch in ("I", "r", "M", "m"):
                raise Deny("code-dynamic")
            if ch not in GENERIC_FLAGS:
                raise Deny("option")
        i += 1
    a = a[i:]
    if not a or a[0] == "-":
        return _stdin_body(heredocs, piped, ctx, depth, lambda b: code_scan(b, lang, ctx))
    return script_rule(a[0], a[1:], ctx)

# ------------------------------------------------------------------ コマンドごとの規則

def _sub(args, skip_opts_with_val=()):
    i = 0
    while i < len(args) and args[i].startswith("-"):
        i += 2 if args[i] in skip_opts_with_val else 1
    return (args[i], args[i + 1:]) if i < len(args) else ("", [])


def _git_dir(start):
    """start から上へ .git を探す → (git dir, common dir) / 無ければ (None, None)。"""
    d = start
    for _ in range(40):
        if not d:
            break
        g = os.path.join(d, ".git")
        if os.path.isdir(g):
            return g, g
        if os.path.isfile(g):
            try:
                with open(g, encoding="utf-8") as fh:
                    line = fh.read(4096).strip()
            except OSError:
                return None, None
            if line.startswith("gitdir:"):
                gd = line.split(":", 1)[1].strip()
                gd = gd if os.path.isabs(gd) else os.path.normpath(os.path.join(d, gd))
                common = gd
                try:
                    with open(os.path.join(gd, "commondir"), encoding="utf-8") as fh:
                        c = fh.read(4096).strip()
                    common = c if os.path.isabs(c) else os.path.normpath(os.path.join(gd, c))
                except OSError:
                    pass
                return gd, common
            return None, None
        parent = os.path.dirname(d)
        if parent == d:
            break
        d = parent
    return None, None


def _git_config(common):
    """.git/config を読む（[section "sub"] key = value だけ・include は読まない）→ {(section, sub): {key: [値]}}。"""
    out = {}
    try:
        with open(os.path.join(common, "config"), encoding="utf-8") as fh:
            text = fh.read(200000)
    except OSError:
        return out
    cur = None
    for line in text.splitlines():
        s = line.strip()
        if not s or s[0] in "#;":
            continue
        m = re.match(r'^\[\s*([A-Za-z0-9.-]+)(?:\s+"((?:[^"\\]|\\.)*)")?\s*\]$', s)
        if m:
            cur = (m.group(1).lower(), m.group(2) or "")
            out.setdefault(cur, {})
            continue
        if cur and "=" in s:
            k, v = s.split("=", 1)
            out[cur].setdefault(k.strip().lower(), []).append(v.strip().strip('"'))
    return out


def _bare_push_target(ctx):
    """素の `git push`（宛先の名前が無い）が向かうリモート名。**あり得る全部の場所**（cd で増えた・git -C の先）で読み、
    1 つでも読めなければ None・public があれば "public"（`git -C ../safe status; git push` で宛先を別のリポから読んだ＝別モデルレビュー）。"""
    got = [_push_target_at(d) for d in (ctx.dirs or [ctx.cwd])]
    if not got or any(t is None for t in got):
        return None
    for t in got:
        if t.lower() == "public":
            return t
    return got[0]


def _push_target_at(start):
    gd, common = _git_dir(start)
    if not gd:
        return None
    try:
        with open(os.path.join(gd, "HEAD"), encoding="utf-8") as fh:
            head = fh.read(4096).strip()
    except OSError:
        return None
    if not head.startswith("ref: refs/heads/"):
        return None
    br = head[len("ref: refs/heads/"):]
    cfg = _git_config(common)
    b = cfg.get(("branch", br), {})
    for key in ("pushremote",):
        if b.get(key):
            return b[key][-1]
    rp = cfg.get(("remote", ""), {}).get("pushdefault")
    if rp:
        return rp[-1]
    if b.get("remote"):
        return b["remote"][-1]
    return "origin"


def _git_short(flags, valued=""):
    """単一ダッシュの束を 1 文字ずつ（`-vf`→v,f）。valued の文字の後ろは値（`-m"msg"`・`-fb name`）。"""
    out = set()
    for f in flags:
        if f.startswith("--") or len(f) < 2:
            continue
        for ch in f[1:]:
            out.add(ch)
            if ch in valued:
                break
    return out


def _git_long(flags, *names):
    """長い旗が names のどれかか、その省略形か（git は曖昧でない省略を受ける＝`--forc`→`--force`・4 文字以上）。"""
    for f in flags:
        if not f.startswith("--"):
            continue
        n = f.split("=", 1)[0]
        if len(n) < 4:
            continue
        for name in names:
            if ("--" + name).startswith(n):
                return True
    return False


_OPTLIKE = re.compile(r"-[A-Za-z0-9]+|--[A-Za-z0-9][A-Za-z0-9-]*(?:=.*)?", re.S)
PUSH_SHORT_OK = set("uqvn")
PUSH_LONG_OK = ("--set-upstream", "--quiet", "--verbose", "--dry-run", "--porcelain", "--progress", "--no-progress",
                "--atomic")


def r_git(args, ctx, depth, piped, heredocs):
    """git -C の行き先はこの 1 回だけ（後ろのコマンドの場所を変えない＝シェルの cwd は動かない）。"""
    saved = list(ctx.dirs)
    try:
        return _r_git(args, ctx, depth, piped, heredocs)
    finally:
        ctx.dirs = saved


def _r_git(args, ctx, depth, piped, heredocs):
    i = 0
    while i < len(args) and args[i].startswith("-"):
        a = args[i]
        if a in ("-c", "--exec-path", "--git-dir", "--work-tree", "--namespace", "--config-env") or \
                a.startswith(("--exec-path=", "--git-dir=", "--work-tree=", "--config-env=", "-c")):
            raise Deny("git-config")
        if a == "-C":
            if i + 1 >= len(args):
                raise Deny("unreadable-syntax")
            tgt = resolve_vars(args[i + 1], ctx)
            if "\x00" in tgt:
                raise Deny("unreadable-syntax")
            check_path(tgt, ctx)
            ctx.dirs = _dirs_for(tgt, ctx)
            i += 2
            continue
        i += 1
    if i >= len(args):
        return None
    sub, rest = args[i], args[i + 1:]
    for a in rest:
        if a.startswith(("--exec-path", "--git-dir", "--work-tree", "--config-env", "--upload-pack",
                         "--receive-pack", "--exec=", "--open-files-in-pager", "--ext-diff", "--textconv")):
            raise Deny("git-config")
    msg = ctx.ap._msg_positions(rest)
    # -m が「メッセージ」の意味の sub だけ、本文を旗から外す（`commit -m "- new test"` の本文の n を --no-verify と
    # 読んだ＝別モデルレビュー 2 回目）。checkout の -m（merge）・cherry-pick の -m（親の番号）では外さない
    # 本文が旗の形（`-m`・`--no-verify`）なら外さない＝`commit -m '-m' --no-verify` は位置の数え方で --no-verify まで本文に
    # 見えた（別モデルレビュー 3 回目）。旗の形でない本文（`- new test`・`-n は旗ではない`）だけ外す（旗に空白は入らない）
    mpos = {k for k in msg if not _OPTLIKE.fullmatch(rest[k])} \
        if sub in ("commit", "tag", "notes", "stash", "merge") else set()
    pos = [a for k, a in enumerate(rest) if not a.startswith("-") and k not in mpos]
    flags = [a for k, a in enumerate(rest) if a.startswith("-") and k not in mpos]

    def plain():
        scan_tokens(rest, ctx, skip=msg)

    if sub in ("clean", "filter-branch", "filter-repo", "replace", "update-ref", "prune"):
        raise Deny("git-destructive")
    if sub in ("send-email", "request-pull", "daemon", "http-backend", "instaweb", "credential", "credential-store",
               "credential-cache", "submodule", "difftool", "mergetool", "bisect", "p4", "svn", "lfs", "hook"):
        raise Deny("git-other")
    if sub == "reset":
        if _git_long(flags, "hard", "merge", "keep"):
            raise Deny("git-destructive")
        ctx.warn.add("git-history")
        return plain()
    if sub == "checkout":
        if "--" in rest or "." in pos or _git_short(flags, "bB") & {"f", "B", "p", "m"} or \
                _git_long(flags, "force", "patch", "ours", "theirs", "overwrite-ignore", "merge", "conflict"):
            raise Deny("git-destructive")
        if len(pos) >= 2 and "-b" not in flags:
            raise Deny("git-destructive")
        for p in pos:
            for d in ctx.dirs:
                if os.path.exists(os.path.join(d, p)):
                    raise Deny("git-destructive")      # ファイル名なら作業の破棄
        return plain()
    if sub == "restore":
        sh = _git_short(flags, "s")
        if not ("S" in sh or _git_long(flags, "staged")) or "W" in sh or "s" in sh or \
                _git_long(flags, "worktree", "source", "overlay"):
            raise Deny("git-destructive")
        return plain()
    if sub == "switch":
        if _git_short(flags, "cC") & {"C", "f"} or _git_long(flags, "force-create", "discard-changes", "force"):
            raise Deny("git-destructive")
        return plain()
    if sub == "branch":
        if _git_short(flags, "t") & {"D", "f", "M", "C"} or _git_long(flags, "force"):
            raise Deny("git-destructive")
        return plain()
    if sub == "stash":
        if pos[:1] and pos[0] in ("drop", "clear"):
            raise Deny("git-destructive")
        ctx.warn.add("git-history")
        return scan_tokens(rest, ctx, skip=msg)
    if sub == "tag":
        if _git_short(flags, "mFu") & {"d", "f"} or _git_long(flags, "delete", "force"):
            raise Deny("git-destructive")
        return plain()
    if sub == "worktree":
        if pos[:1] and pos[0] in ("remove", "prune") and ("f" in _git_short(flags) or _git_long(flags, "force")
                                                           or pos[0] == "prune"):
            raise Deny("git-destructive")
        return plain()
    if sub == "gc":
        if _git_long(flags, "prune", "aggressive"):
            raise Deny("git-destructive")
        return plain()
    if sub == "reflog":
        if pos[:1] and pos[0] in ("expire", "delete"):
            raise Deny("git-destructive")
        return plain()
    if sub == "rm":
        if not _git_long(flags, "cached"):
            raise Deny("delete")
        return plain()
    if sub == "config":
        reads = ("--get", "--get-all", "--get-regexp", "--list", "-l", "--show-origin", "--show-scope")
        if not any(f in reads for f in flags) and len(pos) != 1:
            raise Deny("git-config")
        return plain()
    if sub == "remote":
        if pos[:1] and pos[0] not in ("show", "get-url"):
            raise Deny("git-config")
        return plain()
    if sub == "commit":
        if "n" in _git_short(flags, "mFCct") or _git_long(flags, "no-verify"):
            raise Deny("git-no-verify")      # 禁止語の門（pre-commit）を飛ばす（`-nm`・`--no-ver` の省略も）
        ctx.warn.add("git-history")
        return plain()
    if sub == "rebase":
        if _git_short(flags, "sX") & {"x", "i"} or _git_long(flags, "exec", "interactive", "autosquash"):
            raise Deny("git-other")
        ctx.warn.add("git-history")
        return plain()
    if sub == "push":
        # 旗は許可リスト（`-vf`・`--forc`＝force の省略・`-o` の束などを取りこぼさない＝別モデルレビュー）
        for f in flags:
            if f.startswith("--"):
                if f.split("=", 1)[0] not in PUSH_LONG_OK:
                    raise Deny("git-push-force")
            elif not all(ch in PUSH_SHORT_OK for ch in f[1:]):
                raise Deny("git-push-force")
        if pos and not re.match(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$", pos[0]):
            raise Deny("git-remote-url")     # URL・ローカルパス・user@host:
        target = pos[0] if pos else _bare_push_target(ctx)
        if target is None or target.lower() == "public":
            raise Deny("git-push-remote")    # 公開リポ（名前が public）・宛先が読めない素の push
        for ref in pos[1:]:
            if ref.startswith(("+", ":")) or ":" in ref or ".." in ref:
                raise Deny("git-push-force")
        ctx.warn.add("git-push")
        return None
    if sub in ("pull", "fetch"):
        if pos and not re.match(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$", pos[0]):
            raise Deny("git-remote-url")
        ctx.warn.add("git-history")
        return None
    if sub == "archive" and any(f.startswith("--remote") for f in flags):
        raise Deny("git-other")
    if sub in ("am", "apply", "cherry-pick", "merge", "revert", "mv", "add", "notes", "init", "clone"):
        if sub == "clone":
            ctx.warn.add("network-get")
        ctx.warn.add("git-history")
        return plain()
    return plain()


def r_gh(args, ctx, depth, piped, heredocs):
    pos = [a for a in args if not a.startswith("-")]
    top = pos[0] if pos else ""
    sub = pos[1] if len(pos) > 1 else ""
    reads = {"pr": ("view", "list", "status", "diff", "checks"), "issue": ("view", "list", "status"),
             "run": ("view", "list", "watch"), "repo": ("view",), "release": ("view", "list"),
             "workflow": ("view", "list"), "auth": ("status",), "label": ("list",), "gist": ("list", "view"),
             "cache": ("list",), "variable": ("list",), "secret": ("list",)}
    if top in ("search", "status", "--version", "version"):
        ctx.warn.add("network-get")
        return None
    if top == "api":
        for i, a in enumerate(args):
            if a in ("-f", "-F", "--field", "--raw-field", "--input") or a.startswith(("--field=", "--raw-field=",
                                                                                         "--input=")):
                raise Deny("publish")
            if a in ("-X", "--method") and i + 1 < len(args) and args[i + 1].upper() not in ("GET", "HEAD"):
                raise Deny("publish")
            if (a.startswith("-X") and len(a) > 2 and a[2:].upper() not in ("GET", "HEAD")) or \
                    (a.startswith("--method=") and a.split("=", 1)[1].upper() not in ("GET", "HEAD")):
                raise Deny("publish")
        ctx.warn.add("network-get")
        return None
    if top in reads and sub in reads[top]:
        ctx.warn.add("network-get")
        return None
    raise Deny("publish")


def _url_arg(a, ctx):
    """送信先の URL: 値の見えない変数・置換を含めば持ち出しの疑い（GET でも秘密を載せられる＝攻撃側レビュー F5）。"""
    if "$" in a or "__S" in a or "`" in a or "\x00" in resolve_vars(a, ctx):
        raise Deny("network-send")
    self_words(a, ctx)


# curl／wget は**許可リスト**（旗の数が多く、値の意味も旗ごとに違う＝知らない旗・知らない値の形は Mac）。別モデルレビュー 4 巡で
# 「一覧に無い旗の値を URL と取り違える」「値の中の書き込み（`-w '%output{.env}'`）」「file: の別表記」を順に踏んだ＝
# 通す形を狭く書き、残りは全部 Mac にする。URL は http／https だけ（file:・ftp: 等はローカルのファイルを読む・送る）。
CURL_FLAGS = set("sSLfkviI#NOJgZ")                   # 値を取らない 1 文字の旗
CURL_LONG_FLAGS = {"--silent", "--show-error", "--location", "--fail", "--fail-with-body", "--fail-early", "--compressed",
                   "--head", "--include", "--verbose", "--insecure", "--http1.1", "--http2", "--ipv4", "--ipv6",
                   "--globoff", "--no-progress-meter", "--progress-bar", "--remote-name", "--remote-header-name",
                   "--create-dirs", "--retry-all-errors", "--retry-connrefused", "--no-buffer", "--path-as-is",
                   "--parallel", "--no-clobber"}
# 値を取る旗 → 値の種類（header＝@ で始まればファイルを送る・method＝GET/HEAD だけ・out＝書き出し先・in＝読むファイル・
# cookie＝`a=b` なら文字・それ以外はファイル・writeout＝@ はファイル・%output{…} は書き込み・url＝http/https だけ）
CURL_VALUED = {
    "-H": "header", "--header": "header", "-A": "text", "--user-agent": "text", "-e": "text", "--referer": "text",
    "-u": "text", "--user": "text", "-r": "text", "--range": "text", "--limit-rate": "text",
    "-m": "num", "--max-time": "num", "--connect-timeout": "num", "--retry": "num", "--retry-delay": "num",
    "--retry-max-time": "num", "--max-filesize": "num", "--speed-limit": "num", "--speed-time": "num",
    "-C": "num", "--continue-at": "num", "-X": "method", "--request": "method",
    "-w": "writeout", "--write-out": "writeout",
    "-o": "out", "--output": "out", "-D": "out", "--dump-header": "out", "-c": "out", "--cookie-jar": "out",
    "--etag-save": "out", "--output-dir": "out",
    "-b": "cookie", "--cookie": "cookie", "--cacert": "in", "--etag-compare": "in", "--url": "url",
}
_CURL_SHORT_VALUED = {k[1] for k in CURL_VALUED if len(k) == 2}
_CURL_SCHEMES = frozenset({"http", "https", "file", "ftp", "ftps", "dict", "gopher", "gophers", "imap", "imaps", "ldap",
                           "ldaps", "mqtt", "pop3", "pop3s", "rtmp", "rtsp", "scp", "sftp", "smb", "smbs", "smtp", "smtps",
                           "telnet", "tftp", "ws", "wss"})
_SCHEMELESS_OTHER = ("ftp.", "dict.", "ldap.", "imap.", "pop3.", "smtp.", "telnet.", "gopher.", "mqtt.", "tftp.",
                     "scp.", "sftp.", "smb.", "rtsp.")


def _curl_url(u, ctx):
    """curl の URL: http／https だけ（大文字・`file:/x`・`FILE:///`・ftp: 等は Mac）。scheme の無い形は curl がホスト名の頭で
    プロトコルを当てる（`ftp.x` は ftp）＝その頭も Mac。"""
    _url_arg(u, ctx)
    low = u.strip().lower()
    m = re.match(r"^([a-z][a-z0-9+.-]*):(//)?", low)
    # `x:` を scheme と読むのは `://` が続くか curl の知っている名前（`file:/x` も読む）のときだけ。`localhost:8080/x` は host:port
    # （scheme の無い http）＝別モデルレビュー 5 回目（安全側の誤判定の直し）
    scheme = m.group(1) if m and (m.group(2) or m.group(1) in _CURL_SCHEMES) else ""
    if scheme and scheme not in ("http", "https"):
        raise Deny("network")
    if not scheme and low.split("/", 1)[0].startswith(_SCHEMELESS_OTHER):
        raise Deny("network")
    ctx.downloaded.add(os.path.basename(u.split("?")[0]))


def _curl_value(kind, v, ctx):
    _url_arg(v, ctx)                                 # 値の見えない変数・置換（どの値も送られうる）
    if kind == "header" and v.startswith("@"):
        raise Deny("network-send")                   # ファイルの中身をヘッダーとして送る
    if kind == "method" and v.upper() not in ("GET", "HEAD"):
        raise Deny("network-send")
    if kind == "num" and not re.fullmatch(r"[0-9.]+", v or ""):
        raise Deny("option")
    if kind == "writeout":
        if "%output" in v.lower():
            raise Deny("write")                      # `%output{path}` はファイルを作る・上書きする（curl 8.3+）
        if v.startswith("@"):
            check_path(v[1:], ctx)
    if kind == "out":
        check_path(v, ctx, write=True)
        ctx.warn.add("write")
        ctx.downloaded.add(os.path.basename(v))
    if kind == "in":
        check_path(v, ctx)
    if kind == "cookie" and "=" not in v:
        check_path(v, ctx)                           # `-b file` はファイルを読む
    if kind == "url":
        _curl_url(v, ctx)


def r_curl(args, ctx, depth, piped, heredocs):
    ctx.warn.add("network-get")
    i = 0
    while i < len(args):
        a = args[i]
        if a == "--":
            for u in args[i + 1:]:
                _curl_url(u, ctx)
            return None
        if a.startswith("--"):
            name, eq, val = a.partition("=")
            if name in CURL_LONG_FLAGS and not eq:
                if name in ("--remote-name", "--remote-header-name"):
                    ctx.warn.add("write")
                i += 1
                continue
            if name in CURL_VALUED:
                if not eq:
                    if i + 1 >= len(args):
                        raise Deny("unreadable-syntax")
                    val = args[i + 1]
                    i += 1
                _curl_value(CURL_VALUED[name], val, ctx)
                i += 1
                continue
            raise Deny("option")                     # 知らない旗（送信・プロキシ・設定ファイル・netrc・変数…を含む）
        if a.startswith("-") and len(a) > 1:
            j = 1
            while j < len(a):
                ch = a[j]
                if ch in _CURL_SHORT_VALUED:
                    val = a[j + 1:]
                    if not val:
                        if i + 1 >= len(args):
                            raise Deny("unreadable-syntax")
                        val = args[i + 1]
                        i += 1
                    _curl_value(CURL_VALUED["-" + ch], val, ctx)
                    break
                if ch not in CURL_FLAGS:
                    raise Deny("option")
                if ch in ("O", "J"):
                    ctx.warn.add("write")
                j += 1
            i += 1
            continue
        _curl_url(a, ctx)
        i += 1
    return None


WGET_FLAGS = {"-q", "--quiet", "-nv", "--no-verbose", "-c", "--continue", "-N", "--timestamping", "-S",
              "--server-response", "--spider", "--no-check-certificate", "-nc", "--no-clobber"}
WGET_VALUED = {"-O": "out", "--output-document": "out", "-P": "outdir", "--directory-prefix": "outdir",
               "-T": "num", "--timeout": "num", "-t": "num", "--tries": "num", "-U": "text", "--user-agent": "text",
               "--header": "text"}


def r_wget(args, ctx, depth, piped, heredocs):
    """wget も許可リスト（`-i .env`＝ファイルの各行を URL として取りに行く＝中身が外へ出る・`-e` は .wgetrc の命令・--post-* は送信）。"""
    ctx.warn.add("network-get")
    i = 0
    while i < len(args):
        a = args[i]
        if a in WGET_FLAGS:
            i += 1
            continue
        name, eq, val = a.partition("=")
        if name in WGET_VALUED and (eq or a == name):
            if not eq:
                if i + 1 >= len(args):
                    raise Deny("unreadable-syntax")
                val = args[i + 1]
                i += 1
            kind = WGET_VALUED[name]
            _url_arg(val, ctx)
            if kind == "out" and val != "-":
                check_path(val, ctx, write=True)
                ctx.warn.add("write")
                ctx.downloaded.add(os.path.basename(val))
            elif kind == "outdir":
                check_path(val, ctx, write=True)
                ctx.warn.add("write")
            elif kind == "num" and not re.fullmatch(r"[0-9.]+", val or ""):
                raise Deny("option")
            i += 1
            continue
        if a.startswith("-"):
            raise Deny("option")
        _curl_url(a, ctx)
        ctx.warn.add("write")                        # 既定でカレントへ保存する
        i += 1
    return None


def r_pkg(name):
    deny = {"install", "i", "in", "ins", "inst", "isnt", "add", "ci", "update", "up", "upgrade", "uninstall",
            "remove", "rm", "un", "unlink", "link", "ln", "x", "dlx", "create", "init", "publish",
            "unpublish", "deprecate", "dist-tag", "owner", "access", "token", "login", "logout", "adduser",
            "rebuild", "prune", "dedupe", "set", "global"}
    pub = ("publish", "unpublish", "deprecate", "dist-tag", "owner", "access", "token", "login", "logout", "adduser")

    def rule(args, ctx, depth, piped, heredocs):
        for a in args:
            if a in ("--script-shell", "--userconfig", "--globalconfig", "-g", "--global") or \
                    a.startswith(("--script-shell=", "--userconfig=", "--globalconfig=", "--prefix")):
                raise Deny("option")
        sub, rest = _sub(args, ("-C", "--cwd", "--filter", "-F", "-w", "--workspace"))
        if sub in deny:
            raise Deny("publish" if sub in pub else "install")
        if sub == "config" and rest[:1] and rest[0] in ("set", "delete", "edit"):
            raise Deny("option")
        if sub == "audit" and "fix" in rest:
            raise Deny("install")
        if sub == "exec":
            raise Deny("install")
        if sub in ("run", "run-script") and rest:
            if name_tokens(rest[0]) & RISKY_NAME:
                raise Deny("script-name")
        elif sub and sub not in ("test", "t", "tst", "start", "build", "ls", "list", "view", "info", "show",
                                 "outdated", "why", "explain", "help", "pack", "audit", "doctor", "root", "prefix",
                                 "bin", "version", "--version", "-v", "stop", "restart", "pkg", "query", "config",
                                 "run", "run-script"):
            if name_tokens(sub) & RISKY_NAME:      # yarn / pnpm <台本の名前> は run と同じ
                raise Deny("script-name")
        for x in rest:
            if RISKY_FLAG.match(x):
                raise Deny("script-option")
        ctx.warn.add("script")
        scan_tokens(rest, ctx)
        return None
    return rule


def r_pip(args, ctx, depth, piped, heredocs):
    sub, _rest = _sub(args)
    if sub not in ("list", "show", "freeze", "check", "help", "debug", "inspect", "index", "--version", ""):
        raise Deny("install")
    return None


def r_uv(args, ctx, depth, piped, heredocs):
    sub, rest = _sub(args)
    if sub == "pip":
        return r_pip(rest, ctx, depth, piped, heredocs)
    if sub in ("add", "remove", "sync", "tool", "lock", "self", "python", "init", "publish", "build", "venv"):
        raise Deny("install")
    if sub == "run":
        if any(a.startswith("--with") for a in rest):
            raise Deny("install")
        ctx.warn.add("script")
        inner = [a for a in rest if not a.startswith("-")]
        if inner:
            return analyze(inner, ctx, depth, piped, heredocs)
        return None
    ctx.warn.add("script")
    return None


def r_brew(args, ctx, depth, piped, heredocs):
    sub, _rest = _sub(args)
    if sub not in ("list", "ls", "info", "search", "--prefix", "--version", "--cellar", "--repository", "doctor",
                   "outdated", "deps", "uses", "config", "leaves", "desc", "home", "cat", "log", "shellenv", ""):
        raise Deny("install")
    return None


def r_cargo(args, ctx, depth, piped, heredocs):
    sub, rest = _sub(args)
    if sub in ("install", "uninstall", "publish", "login", "logout", "owner", "yank"):
        raise Deny("install" if sub in ("install", "uninstall") else "publish")
    ctx.warn.add("script")
    scan_tokens(rest, ctx)
    return None


def r_go(args, ctx, depth, piped, heredocs):
    sub, rest = _sub(args)
    if sub in ("install", "get"):
        raise Deny("install")
    ctx.warn.add("script")
    scan_tokens(rest, ctx)
    return None


def r_wrangler(args, ctx, depth, piped, heredocs):
    pos = [a for a in args if not a.startswith("-")]
    ok = [("dev",), ("tail",), ("whoami",), ("types",), ("kv", "key", "list"), ("kv", "key", "get"),
          ("kv", "namespace", "list"), ("deployments", "list"), ("versions", "list"), ("versions", "view"),
          ("d1", "list"), ("d1", "info"), ("r2", "bucket", "list"), ("pages", "project", "list"),
          ("pages", "deployment", "list"), ("secret", "list"), ("kv:key", "list"), ("kv:key", "get")]
    if not pos and any(a in ("--version", "-v") for a in args):
        return None
    if pos[:2] == ["d1", "execute"] and "--remote" not in args:
        ctx.warn.add("script")
        return None
    for o in ok:
        if tuple(pos[:len(o)]) == o:
            ctx.warn.add("network-get")
            return None
    raise Deny("publish")


def r_rsync(args, ctx, depth, piped, heredocs):
    pos = [a for a in args if not a.startswith("-")]
    for a in args:
        if a.startswith(("--delete", "--remove-source-files", "-e", "--rsh", "--rsync-path")) or \
                (re.match(r"^-[a-zA-Z]*e", a) and not a.startswith("--")):
            raise Deny("delete" if "delete" in a or "remove" in a else "network")
    for p in pos:
        if re.match(r"^[^/]*:", p) or p.startswith("rsync://"):
            raise Deny("network")
    for p in pos[:-1]:
        check_path(p, ctx)
    if pos:
        check_path(pos[-1], ctx, write=True)
    return None


def r_find(args, ctx, depth, piped, heredocs):
    i, plain = 0, []
    while i < len(args):
        a = args[i]
        low = a.lower()
        if low == "-delete" or low.startswith(("-fprint", "-fls")):
            raise Deny("delete" if low == "-delete" else "option")
        if low in ("-exec", "-execdir", "-ok", "-okdir"):
            j = i + 1
            while j < len(args) and args[j] not in (";", "+", "\\;"):
                j += 1
            payload = [x for x in args[i + 1:j] if x != "{}"]
            if not payload:
                raise Deny("unreadable-syntax")
            analyze(payload + ["{}"], ctx, depth, False, ())
            i = j + 1
            continue
        plain.append(a)
        i += 1
    scan_tokens(plain, ctx)
    return None


def r_chmod(args, ctx, depth, piped, heredocs):
    pos = [a for a in args if not a.startswith("-") or re.match(r"^-[rwxXst]+$", a)]
    if pos:
        mode = pos[0]
        if "s" in mode or "t" in mode or re.match(r"^[2467][0-7]{3}$", mode):
            raise Deny("privilege")
        for p in pos[1:]:
            check_path(p, ctx, write=True)
    return None


def _is_dir(p, ctx):
    try:
        return any(os.path.isdir(d) for d in _dirs_for(resolve_vars(p, ctx), ctx))
    except (OSError, ValueError):
        return False


def _copy_like(args, ctx, move):
    """cp・mv・install・ditto の実際の書き先（`cp nested/CLAUDE.md .` は ./CLAUDE.md を上書きする＝別モデルレビュー）。
    mv は元のパスも消える＝書き込みとして見る（`mv CLAUDE.md x` も指示のファイルの変更）。"""
    pos, tdir, i = [], None, 0
    while i < len(args):
        a = args[i]
        if a in ("-t", "--target-directory") and i + 1 < len(args):
            tdir = args[i + 1]
            i += 2
            continue
        if a.startswith("--target-directory="):
            tdir = a.split("=", 1)[1]
        elif a.startswith("-S") or a in ("-S", "--suffix"):
            pass
        elif not a.startswith("-"):
            pos.append(a)
        i += 1
    if tdir is not None:
        srcs, dest, into = pos, tdir, True
    else:
        if not pos:
            return None
        srcs, dest = pos[:-1], pos[-1]
        into = len(srcs) > 1 or dest.endswith("/") or dest in (".", "..") or _is_dir(dest, ctx)
    if srcs and srcs[0] == "/dev/null":
        raise Deny("delete")                     # cp /dev/null f＝中身を消す
    for x in srcs:
        check_path(x, ctx, write=move)
    check_path(dest, ctx, write=True)
    if into:
        for x in srcs:
            check_path(os.path.join(dest, os.path.basename(x.rstrip("/")) or x), ctx, write=True)
    ctx.warn.add("write")
    return None


def r_cp(args, ctx, depth, piped, heredocs):
    return _copy_like(args, ctx, move=False)


def r_mv(args, ctx, depth, piped, heredocs):
    return _copy_like(args, ctx, move=True)


def r_write_last(args, ctx, depth, piped, heredocs):
    pos = [a for a in args if not a.startswith("-")]
    if len(pos) >= 2 and pos[0] == "/dev/null":
        raise Deny("delete")                     # cp /dev/null f＝中身を消す
    for i, a in enumerate(args):
        if a in ("-t", "--target-directory") and i + 1 < len(args):
            check_path(args[i + 1], ctx, write=True)
    for p in pos[:-1]:
        check_path(p, ctx)
    if pos:
        check_path(pos[-1], ctx, write=True)
    ctx.warn.add("write")
    return None


def r_ln(args, ctx, depth, piped, heredocs):
    """ln: 作るリンク（最後）は書き込み。`-s` のリンク先が cwd の外（絶対・~・..）なら、後の読み書きがリンク越しに
    外へ抜ける（判定と実行の時間差）＝Mac（攻撃側レビュー F6）。"""
    flags = "".join(a[1:] for a in args if a.startswith("-") and not a.startswith("--"))
    soft = "s" in flags or "--symbolic" in args
    pos = [a for a in args if not a.startswith("-")]
    if soft and pos:
        tgt = pos[0]
        if tgt.startswith(("/", "~")) or ".." in tgt.split("/") or "$" in tgt:
            raise Deny("symlink")
    return r_write_last(args, ctx, depth, piped, heredocs)


def r_write_all(args, ctx, depth, piped, heredocs):
    for a in args:
        if not a.startswith("-"):
            check_path(a, ctx, write=True)
    ctx.warn.add("write")
    return None


def r_mkdir(args, ctx, depth, piped, heredocs):
    for a in args:
        if not a.startswith("-"):
            check_path(a, ctx, write=True)
    return None


def r_sed(args, ctx, depth, piped, heredocs):
    inplace = any(a == "-i" or a.startswith("-i") or a.startswith("--in-place") for a in args)
    prog = [a for a in args if not a.startswith("-")]
    blob = " ".join(prog)
    if re.search(r"(^|[;}\s\d$/!])[eEwW](\s|$)|/e\b|(^|[;}\s\d$/!])[wW]\s+\S", blob):
        raise Deny("code-process")           # sed の e（コマンドの実行）・w（別のファイルへ書く）
    if inplace:
        ctx.warn.add("write")
        a2 = list(args)
        for k, a in enumerate(a2):
            if a == "-i" and k + 1 < len(a2) and (a2[k + 1] == "" or a2[k + 1].startswith(".")):
                a2[k + 1] = "-"
        pos = [a for a in a2 if not a.startswith("-")]
        script_given = any(a in ("-e", "-f") for a in a2)
        for p in (pos if script_given else pos[1:]):
            if p:
                check_path(p, ctx, write=True)
    else:
        scan_tokens(args, ctx)
    return None


def r_awk(args, ctx, depth, piped, heredocs):
    pos = [a for a in args if not a.startswith("-")]
    for i, a in enumerate(args):
        if a == "-f" and i + 1 < len(args):
            return script_rule(args[i + 1], [], ctx)
    if pos:
        prog = pos[0]
        if re.search(r"system\s*\(|\|\s*\"|\"\s*\|\s*getline|\|&|>\s*\"", prog):
            raise Deny("code-process")
        scan_tokens(pos[1:], ctx)
    return None


def r_tar(args, ctx, depth, piped, heredocs):
    for a in args:
        if a.startswith(("--to-command", "--use-compress-program", "--checkpoint-action", "--rsh-command",
                         "--info-script", "--new-volume-script")) or a == "-I":
            raise Deny("code-process")
    for i, a in enumerate(args):
        if a == "-C" and i + 1 < len(args):
            check_path(args[i + 1], ctx, write=True)
    scan_tokens(args, ctx)
    return None


def r_open(args, ctx, depth, piped, heredocs):
    for i, a in enumerate(args):
        if a.lower().endswith((".command", ".tool", ".app", ".pkg", ".mpkg", ".dmg", ".terminal", ".workflow",
                               ".scpt", ".applescript", ".sh")) or \
                (a in ("-a", "-b") and i + 1 < len(args) and re.search(r"terminal|iterm|script", args[i + 1], re.I)):
            raise Deny("system")
    for a in args:
        m = re.match(r"^([a-zA-Z][a-zA-Z0-9+.-]*):", a)
        if m and m.group(1).lower() not in ("http", "https", "file"):
            raise Deny("system")
        self_words(a, ctx)
    scan_tokens([a for a in args if not a.startswith("http")], ctx)
    return None


def r_defaults(args, ctx, depth, piped, heredocs):
    sub, _rest = _sub(args, ("-host",))
    if sub not in ("read", "read-type", "domains", "find", "help"):
        raise Deny("system")
    return None


def r_docker(args, ctx, depth, piped, heredocs):
    sub, _rest = _sub(args)
    if sub not in ("ps", "images", "logs", "inspect", "version", "info", "stats", "top", "port", "diff", "history"):
        raise Deny("system")
    return None


def r_agent(args, ctx, depth, piped, heredocs):
    bad = ("--dangerously-bypass-approvals-and-sandbox", "--yolo", "--dangerously-skip-permissions",
           "--allow-dangerously-skip-permissions")
    for i, a in enumerate(args):
        if a in bad or (a in ("-s", "--sandbox") and i + 1 < len(args) and args[i + 1] == "danger-full-access") \
                or a == "--sandbox=danger-full-access" or \
                (a == "--permission-mode" and i + 1 < len(args) and args[i + 1] in ("bypassPermissions", "acceptEdits")) \
                or a.startswith("--permission-mode=bypass"):
            raise Deny("agent-bypass")
    ctx.warn.add("agent")
    return None


def r_sqlite(args, ctx, depth, piped, heredocs):
    blob = " ".join(args) + " ".join(b for _q, b in heredocs)
    if re.search(r"(^|\s|\n)\.(shell|system|load|output|once|save|backup|import)\b", blob) or \
            "load_extension" in blob.lower() or re.search(r"\battach\b", blob, re.I):
        raise Deny("code-process")
    scan_tokens(args, ctx)
    ctx.warn.add("write")
    return None


def r_zip(args, ctx, depth, piped, heredocs):
    if any(a in ("-T", "-TT", "--unzip-command") or a.startswith("-TT") for a in args):
        raise Deny("code-process")
    scan_tokens(args, ctx)
    return None


def r_npx(args, ctx, depth, piped, heredocs):
    if not any(a in ("--no-install", "--no") for a in args):
        raise Deny("install")
    inner = [a for a in args if not a.startswith("-")]
    return analyze(inner, ctx, depth, piped, heredocs) if inner else None


def r_make(args, ctx, depth, piped, heredocs):
    for a in args:
        if a.startswith(("SHELL=", "MAKEFLAGS=", "-f", "--file", "--eval", "-E")) or a in ("-C",):
            raise Deny("option")
        if not a.startswith("-") and "=" not in a and name_tokens(a) & RISKY_NAME:
            raise Deny("script-name")
    ctx.warn.add("script")
    return None


RULES = {
    "npx": r_npx, "git": r_git, "gh": r_gh, "curl": r_curl, "wget": r_wget, "npm": r_pkg("npm"), "pnpm": r_pkg("pnpm"),
    "yarn": r_pkg("yarn"), "pip": r_pip, "pip3": r_pip, "uv": r_uv, "brew": r_brew, "cargo": r_cargo, "go": r_go,
    "wrangler": r_wrangler, "rsync": r_rsync, "find": r_find, "chmod": r_chmod, "cp": r_cp,
    "mv": r_mv, "ln": r_ln, "install": r_cp, "ditto": r_cp,
    "touch": r_write_all, "mkdir": r_mkdir, "tee": r_write_all, "sed": r_sed, "gsed": r_sed, "awk": r_awk,
    "gawk": r_awk, "tar": r_tar, "open": r_open, "defaults": r_defaults, "docker": r_docker, "codex": r_agent,
    "claude": r_agent, "sqlite3": r_sqlite, "zip": r_zip, "make": r_make,
}


# ------------------------------------------------------------------ 入口

def check_all(tool, ti, cwd, home, ap):
    """R103-1: Mac だけと決まった要求の理由を全部（重複なし・見つけた順）。Bash は区切りと置換ごとに判定し直す。"""
    if tool != "Bash":
        try:
            check(tool, ti, cwd, home, ap)
            return []
        except Deny as e:
            return [e.reason]
    if not isinstance(cwd, str) or not os.path.isabs(cwd) or not isinstance(home, str) or not home:
        return ["unreadable"]
    cmd = ti.get("command") if isinstance(ti, dict) else None
    if not isinstance(cmd, str) or not cmd.strip():
        return ["unreadable"]
    ctx = Ctx(cwd, home, ap)
    ctx.collect = []
    _each(ctx, lambda: bash_review(cmd, ctx))
    out = []
    for r in ctx.collect:
        if r not in out:
            out.append(r)
    return out


def check(tool, ti, cwd, home, ap):
    """拒否リストに当たれば Deny(reason)。当たらなければ review で見せる警告のタグ（sorted list）。
    ツールの門（未知のツール・MCP）と共通の門（読めない・大きすぎる・サンドボックス外し）は approval_policy が先に見る。"""
    if not isinstance(cwd, str) or not os.path.isabs(cwd) or not isinstance(home, str) or not home:
        raise Deny("unreadable")                 # 場所の分からない要求は判定できない
    ctx = Ctx(cwd, home, ap)
    h = ctx.home
    if ctx.cwd == "/" or ctx.cwd == h or h.startswith(ctx.cwd.rstrip("/") + "/"):
        ctx.warn.add("outside-cwd")              # / やホーム（とその祖先）で開いたセッション＝範囲が広い
    try:
        if tool == "Bash":
            cmd = ti.get("command")
            if not isinstance(cmd, str) or not cmd.strip():
                raise Deny("unreadable")
            bash_review(cmd, ctx)
        elif tool in ap.TOOLS_EDIT:
            p = ti.get(ap.TOOLS_EDIT[tool])
            if not isinstance(p, str) or not p:
                raise Deny("unreadable")
            check_path(p, ctx, write=True)
        elif tool in ap.TOOLS_READ:
            for key in ("file_path", "path"):
                v = ti.get(key)
                if isinstance(v, str) and v:
                    check_path(v, ctx)
            for key in (("pattern",) if tool == "Glob" else ("glob",) if tool == "Grep" else ()):
                v = ti.get(key)
                if isinstance(v, str) and v:
                    check_path(v, ctx)
        elif tool == "WebFetch":
            u = ti.get("url")
            if not isinstance(u, str) or not u:
                raise Deny("unreadable")
            self_words(u, ctx)
            if not re.match(r"^https?://", u, re.I):
                raise Deny("network")        # file: 等は読まない
            try:
                from urllib.parse import urlsplit
                sp = urlsplit(u)
                host = (sp.hostname or "").lower()
            except ValueError:
                raise Deny("unreadable")
            if not host or host in ("localhost", "0.0.0.0", "::1", "::") or host.startswith("127.") or \
                    host.endswith(".localhost") or "@" in sp.netloc:
                raise Deny("network")        # 手元のサービス（IPv6 の ::1 も）・資格情報つきの URL
            ctx.warn.add("network-get")
        elif tool == "WebSearch":
            ctx.warn.add("network-get")
        elif tool in ap.TOOLS_FREE:
            pass
        else:
            raise Deny("tool")
    except RecursionError:
        raise Deny("nesting")
    return sorted(t for t in ctx.warn if t in TAGS)
