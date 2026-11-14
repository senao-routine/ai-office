#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""新UI（R50）の層の逆流を機械で禁止する番人。

なぜ必要か:
  「core は DOM を触らない」を規約で守ろうとすると必ず破れる。破れた瞬間に
  core は node --test でテストできなくなり、検証の土台が崩れる。
  だから規約ではなくゲートで守る。

層（依存は必ず下向き）:
  ui/iso/** ui/pixel/** ui/cockpit/**
                           presentation（様式）。様式同士は import しない。
                           流用したい論理は ui/core（純関数）か ui/hud（操作系）へ移してから使う
  ui/hud/**                様式非依存の操作系。core/platform だけを見る
  ui/platform/**           ブラウザに触る（fetch・localStorage・rAF・window）
  ui/core/**               純ロジック。DOM もネットワークも時刻も乱数も触らない

使い方: python3 tools/js_layer_lint.py         （verify.sh ▶2b・dev.sh --check・Stop hook の stop-check.sh から呼ぶ）
      python3 tools/js_layer_lint.py --root DIR （DIR/ui を検査する。回帰テスト tests/test_js_layer_lint.py が一時ツリーで使う）

走査するのは ui/ 配下の *.js と *.mjs（ui 直下の vendor/ だけ除外。ui/cockpit/vendor/ などは走査する）。拾う import の形:
  import … from "x" ／ import{a}from"x"（空白なし）／ export … from "x" ／ import "x"（副作用）／ import("x")
  キーワードと指定子のあいだのコメント（from /* c */ "x"）は許す。コメントの中と '…' "…" の文字列の中の
  import／from は拾わない（テンプレートリテラルの中は ${import("x")} があり得るので拾う＝安全側）。
指定子はブラウザ→サーバーが最終的に読むパスへ直してから判定する（resolve_spec）:
  JS のエスケープ（\\x2e・\\t など）を JS が読む値へ戻す → WHATWG URL の前処理（両端の C0 制御文字と空白・
  途中のタブと改行を除く）→ 相対（./ ../）は自分の URL から解決・絶対 URL は scheme と authority を外す
  （http: の後の / は省ける）→ %2e%2e などのパーセント符号化を戻す → .. を畳む → 大文字小文字を区別しない
  （macOS のファイルシステムは区別しない）。urllib.parse.urlsplit は使わない（前処理と検証が Python の版で違い、
  同じ指定子が 3.14 で赤・3.9 で緑になった）。
"""
import pathlib
import posixpath
import re
import sys
from urllib.parse import unquote

ROOT = pathlib.Path(__file__).resolve().parent.parent
SCAN_GLOBS = ("*.js", "*.mjs")

# core に出てはいけない識別子（＝ブラウザ依存・非決定論）
CORE_FORBIDDEN = [
    (r"\bdocument\b", "document（DOMは presentation/platform の仕事）"),
    (r"\bwindow\b", "window"),
    (r"\blocalStorage\b", "localStorage"),
    (r"\bsessionStorage\b", "sessionStorage"),
    (r"\bfetch\s*\(", "fetch()（通信は ui/platform/api.js だけ）"),
    (r"\brequestAnimationFrame\b", "requestAnimationFrame"),
    (r"\bnavigator\b", "navigator"),
    (r"\blocation\b", "location"),
    (r"\bMath\.random\b", "Math.random（決定論が壊れる。core/rng を使う）"),
    (r"\bDate\.now\b", "Date.now（決定論が壊れる。時刻は注入する）"),
    (r"\bnew\s+Date\s*\(\s*\)", "new Date()（決定論が壊れる）"),
    (r"\bperformance\.now\b", "performance.now（決定論が壊れる）"),
]

# import の向きの禁止（from パス → 禁止する参照元ディレクトリ）
# R90-S1: presentation は ui/iso に統合。ui/hud はスタイル非依存の操作系。
#   hud は core/platform だけを見る＝どのスタイルからも使えるが、スタイルを知らない。
#   R99: cockpit は禁止リストでなく許可リストで縛る（core/platform/hud/vendor と自分自身だけ）。
#   様式が増えるたびに禁止側へ足し忘れると、その様式への import が黙って通るため。
# R122: ui/app（明るい 3 列の新画面）も許可リストで縛る（core/platform/hud/vendor と自分自身）。例外は**部屋と端末の部品の名指し**だけ＝
#   3D の部屋（ui/iso/scene3d.js の IsoScene）・ドット絵の部屋（ui/cockpit/room.js と個体色の ui/cockpit/palette.js）・本物の端末（ui/cockpit/term.js）を右のカードと中央に載せるため
#   （PWA の bootpx.js／旧 boot3d.js と同じ使い方）。様式の HUD（index.js など）は import しない。ほかの層・様式から ui/app は import しない。
APP_ALLOWED = r"/ui/(?!(?:core|platform|hud|vendor|app)/|iso/scene3d\.js$|cockpit/(?:room|term|palette)\.js$)"
IMPORT_RULES = [
    ("ui/core", r"/ui/(platform|iso|hud|pixel|cockpit|pwa|app)/", "core が上位層を import している"),
    ("ui/platform", r"/ui/(iso|hud|pixel|cockpit|pwa|app)/", "platform が presentation を import している"),
    ("ui/hud", r"/ui/(iso|pixel|cockpit|pwa|app)/", "hud が presentation を import している（hud は core/platform のみ）"),
    ("ui/iso", r"/ui/(pixel|cockpit|app)/", "iso が他スタイルを import している（presentation は互いに独立）"),
    ("ui/pixel", r"/ui/(iso|cockpit|app)/", "pixel が他スタイルを import している（presentation は互いに独立）"),
    ("ui/app", APP_ALLOWED,
     "app が core/platform/hud/vendor と名指しの部屋・端末の部品（iso/scene3d.js・cockpit/room.js・cockpit/palette.js・cockpit/term.js）以外を import している"),
    ("ui/cockpit", r"/ui/(?!(?:core|platform|hud|vendor|cockpit)/)",
     "cockpit が core/platform/hud/vendor 以外を import している（様式同士は独立・流用する論理は core へ移す）"),
]

# 走査の速さ: 1 字ずつの Python ループは遅い（Stop hook で毎ターン回る）。次に意味のある字までを 1 文字クラスの
# search で飛ばす（バックトラックの無い 1 文字の検索なので入力長に比例＝R90-P1 の罠は踏まない）
_NOT_NL = re.compile(r"[^\n]")
_CODE_STOP = re.compile(r"[/'\"`]")
_STR_STOP = {"'": re.compile(r"[\\'\n]"), '"': re.compile(r'[\\"\n]'), "`": re.compile(r"[\\`]")}


def _blank(s):
    return _NOT_NL.sub(" ", s)


def _string_end(src, i):
    """src[i]（' " `）で始まる文字列リテラルの終わり（閉じ引用符の次）。エスケープを跨ぐ・テンプレは改行可・
    閉じ忘れの ' " は行末で打ち切る。"""
    q, j, stop = src[i], i + 1, _STR_STOP[src[i]]
    while True:
        m = stop.search(src, j)
        if not m:
            return len(src)
        j = m.start()
        if src[j] == "\\":
            j += 2
            continue
        return j + 1 if src[j] == q else j               # 閉じ引用符の次／閉じ忘れの ' " は改行の手前まで


_REGEX_AFTER_WORDS = {"return", "typeof", "case", "do", "else", "in", "of", "new", "delete", "void",
                      "throw", "yield", "await", "instanceof"}


def _regex_end(src, i, last, paren=False):
    """src[i] の / が正規表現リテラルの始まりならその終わり（フラグの次）、違えば -1。
    直前の意味のある字 src[last] で割り算と見分ける（識別子・数・文字列・) ] ・後置の ++ -- の後は割り算）。
    同じ行で閉じなければ正規表現ではない（割り算を読み違えても波及はその 1 行の中だけ）。
    ) の直後は字面では決まらない（`if (x) /re/…` は正規表現・`(a + b) / 2` は割り算）。paren=True なら
    正規表現とみなす＝imports_of は両方の読みで回して和を取る（どちらかの読みで見えた import は拾う）。
    キーワードと同じ名前のプロパティ（`counts.new / total`）は割り算。"""
    if src[max(0, last - 1):last + 1] in ("++", "--"):
        return -1
    if last >= 0 and src[last] not in "(,=:[!&|?{};+-*%<>~^}" + (")" if paren else ""):
        w = re.search(r"[A-Za-z_$][\w$]*$", src[max(0, last - 11):last + 1])
        if not (w and w.group() in _REGEX_AFTER_WORDS) or src[last - len(w.group()):last - len(w.group()) + 1] == ".":
            return -1
    j, n, cls = i + 1, len(src), False
    while j < n and src[j] != "\n":
        ch = src[j]
        if ch == "\\":
            j += 1 if src[j + 1:j + 2] == "\n" else 2
            continue
        if ch == "/" and not cls:
            j += 1
            while j < n and (src[j].isalnum() or src[j] in "_$"):
                j += 1
            return j
        if ch in "[]":
            cls = ch == "["
        j += 1
    return -1


def strip_noise(src, keep="", paren=False):
    """コメントと文字列リテラルを空白に潰す（誤検知を防ぐ）。長さと改行の位置を保つ（原文と位置で突き合わせられる）。

    keep に挙げた引用符（' " `）の文字列は潰さずに残す（imports_of がコメントだけを潰した写しを作るのに使う）。
    正規表現リテラルは中身をそのまま残して読み飛ばす（R99-P3）: /"/ の " や /^\\/*/ の /* を文字列・コメントの
    始まりと読み違えると、その先の本物のコード（import・Date.now）まで潰して黙って通してしまう。

    R90-P1 で踏んだ罠: 以前は正規表現 3 本で潰していたが、`(['"`])(?:\\.|(?!\\1).)*\\1` は
    改行の無い巨大な 1 行（ui/pwa/app.html＝実ファイル化した PWA シェル）でバックトラックが爆発し
    2 分以上止まった（dev.sh --check・verify ▶2b・PostToolUse hook が全部詰まる）。
    線形の状態機械に置き換える（入力長に比例・使う正規表現は 1 文字クラスの検索だけ）。"""
    out = []
    i, n, last = 0, len(src), -1                          # last = 直前の意味のある字（空白・コメント以外）の位置
    while i < n:
        c = src[i]
        if c not in "/'\"`":                              # ふつうのコード: 次の / ' " ` まで一気に写す
            m = _CODE_STOP.search(src, i)
            j = m.start() if m else n
            seg = src[i:j].rstrip()
            if seg:
                last = i + len(seg) - 1
            out.append(src[i:j])
            i = j
            continue
        nxt = src[i + 1] if i + 1 < n else ""
        if c == "/" and nxt == "*":                       # ブロックコメント
            j = src.find("*/", i + 2)
            j = n if j < 0 else j + 2
            out.append(_blank(src[i:j]))
            i = j
            continue
        if c == "/" and nxt == "/" and (i == 0 or src[i - 1] != ":"):   # 行コメント（http:// は除外）
            j = src.find("\n", i)
            j = n if j < 0 else j
            out.append(" " * (j - i))
            i = j
            continue
        if c in "'\"`":                                   # 文字列
            j = _string_end(src, i)
            out.append(src[i:j] if c in keep else _blank(src[i:j]))
        elif c == "/" and (j := _regex_end(src, i, last, paren)) > 0:   # 正規表現リテラル（中身はそのまま）
            out.append(src[i:j])
        else:                                             # 割り算の /
            j = i + 1
            out.append(c)
        last = j - 1
        i = j
    return "".join(out)


# 拾う形（R99-P3）: 以前は `from '…'`（空白必須）と `import('…')` しか拾わず、
#   副作用 import（import "/ui/pixel/x.js"）と空白なしの from"…" が全規則を素通りしていた。
#   指定子の中身は _string_end で閉じ引用符まで読む（[^'"]+ だと '/ui/cockpit/x"/../../pixel/…' の " で切れて
#   許可された /ui/cockpit/x に見え、ブラウザが読む /ui/pixel/… を見落とした）。
_IMPORT_RE = re.compile(
    r"""\b(?:from|import)\s*(?=['"])"""      # import … from "x" / import{a}from"x" / export … from "x" / import "x"
    r"""|\bimport\s*\(\s*(?=['"`])""")       # import("x") / import(`x`)


def imports_of(src):
    """import / export の指定子（引用符の中身・エスケープは原文のまま）を集める。

    コメントだけを潰した写し（`from /* c */ "x"` を拾える）に当て、キーワードがコメントか '…' "…" の文字列の
    中にあるもの（`// 以前は import "…"`・'copied from"…"'）は捨てる。テンプレートリテラルの中は捨てない。"""
    specs = []
    # ) の後ろの / は割り算とも正規表現とも読める。読み違えると `/*` や `"` を取り違えて先の import を潰す
    # ＝両方の読みで回して和を取る（import の見落としは素通り・余分に拾うのは赤で気づける）
    for paren in (False, True):
        text = strip_noise(src, keep="'\"`", paren=paren)
        mask = strip_noise(src, keep="`", paren=paren)
        for m in _IMPORT_RE.finditer(text):
            if mask[m.start()] == " ":
                continue
            k = m.end()
            body = text[k + 1:_string_end(text, k)]
            spec = body[:-1] if body.endswith(text[k]) else body
            if spec not in specs:
                specs.append(spec)
    return specs


_JS_ESCAPE = re.compile(r"\\(?:x([0-9a-fA-F]{2})|u\{([0-9a-fA-F]+)\}|u([0-9a-fA-F]{4})|(\r\n|[\s\S]))")
# 1 文字エスケープは JS が読む制御文字へ（\t を "t" と読むと、URL パーサーが除くタブを見落とす）。
# 行継続（\ ＋改行）は何も残さない。ほかの \c は c そのもの
_JS_SINGLE = {"t": "\t", "n": "\n", "r": "\r", "b": "\b", "f": "\f", "v": "\v", "0": "\0",
              "\n": "", "\r": "", "\r\n": "", "\u2028": "", "\u2029": ""}


def _js_unescape(s):
    """原文の文字列リテラルの中身を、JS が読む値へ戻す（"\\x2e\\x2e" は ".."・"\\t" はタブ）。"""
    def rep(m):
        h = m.group(1) or m.group(2) or m.group(3)
        if h:
            try:
                return chr(int(h, 16))
            except (ValueError, OverflowError):
                return ""
        return _JS_SINGLE.get(m.group(4), m.group(4))
    return _JS_ESCAPE.sub(rep, s)


_C0_OR_SPACE = "".join(map(chr, range(0x21)))           # WHATWG の C0 control or space（U+0000〜U+0020）
# 絶対 URL の頭（scheme と authority）。http(s)・ws(s)・ftp は WHATWG の「特別な scheme」で、scheme の後の / は
# 何本でもよく 0 本でもよい（"http:127.0.0.1:4780/ui/…" も同じオリジンを読む）。ほかは scheme:// か //（scheme 省略）
_URL_HEAD = re.compile(r"(?i)^(?:(?:https?|wss?|ftp):/*|(?:[a-z][a-z0-9+.-]*:)?//)[^/?#]*")


def resolve_spec(spec, rel):
    """指定子を、ブラウザ→サーバーが最終的に読むファイルの URL パスへ直す（判定用。小文字にするのは targets_of）。

    rel = import している側のファイルの ROOT 相対パス（URL は "/" + rel で配信される）。
    ブラウザは相対指定子を自分の URL から解決し、`\\` を `/` として読む。サーバー（office_server.ui_asset）は
    パスを unquote してから解決するので、`%2e%2e` も `..%2f` も最後は `..` として効く。
    どれか 1 つでも省くと、許可された接頭辞（/ui/cockpit/）を経由して他の様式へ抜けられる。
    URL の前処理と scheme・authority の切り出しは自前で行う（urlsplit は Python の版で結果が違う）。"""
    s = _js_unescape(spec)
    s = re.sub(r"[\t\n\r]", "", s.strip(_C0_OR_SPACE)).replace("\\", "/")   # WHATWG の前処理（両端→途中の順）
    if s.startswith(("./", "../")) or s in (".", ".."):
        s = posixpath.dirname("/" + rel) + "/" + s
    path = re.split(r"[?#]", _URL_HEAD.sub("", s, count=1), maxsplit=1)[0]   # 絶対 URL の頭と ?query・#hash を外す
    path = unquote(path).replace("\\", "/")
    if path.startswith("/"):
        path = "/" + posixpath.normpath(path).lstrip("/")   # normpath は先頭の // を残すので 1 本に
    return path


def targets_of(spec, rel, root):
    """判定にかける URL パスの候補（小文字）。文字列で畳んだパスと、サーバーと同じくファイルシステムで
    解決したパス（ui/ の中のシンボリックリンクを辿った先）の両方を返す。"""
    path = resolve_spec(spec, rel)
    out = [path.lower()]
    if path.startswith("/"):
        try:
            real = (root / path.lstrip("/")).resolve()
            out.append("/" + real.relative_to(root.resolve()).as_posix().lower())
        except (OSError, RuntimeError, ValueError):
            pass
    return out


def lint(root):
    """root/ui を検査して（違反の一覧, 走査したファイル数）を返す。ui/ が無ければ (None, 0)。"""
    root = pathlib.Path(root)
    ui = root / "ui"
    if not ui.is_dir():
        return None, 0
    problems = []
    files = sorted({p for g in SCAN_GLOBS for p in ui.rglob(g)          # 除外は ui 直下の vendor/ だけ
                    if p.is_file() and p.relative_to(ui).parts[0] != "vendor"})
    for path in files:
        rel = path.relative_to(root).as_posix()
        raw = path.read_text(encoding="utf-8")
        code = strip_noise(raw)

        if rel.startswith("ui/core/"):
            for pattern, why in CORE_FORBIDDEN:
                for m in re.finditer(pattern, code):
                    line = code[:m.start()].count("\n") + 1
                    problems.append(f"{rel}:{line} core に {why}")

        specs = imports_of(raw)
        for prefix, bad, why in IMPORT_RULES:
            if not rel.startswith(prefix + "/"):
                continue
            for spec in specs:
                # 相対（"../iso/scene3d.js"・R90-H）も絶対（"/ui/cockpit/../pixel/x.js"）も解決してから判定する
                if any(re.search(bad, t) for t in targets_of(spec, rel, root)):
                    problems.append(f"{rel} {why}: {spec}")

        for spec in specs:
            if "office_page.html" in spec:
                problems.append(f"{rel} 旧UI(office_page.html)を参照している")
    return problems, len(files)


def main(argv=None):
    args = sys.argv[1:] if argv is None else list(argv)
    root = ROOT
    if len(args) == 2 and args[0] == "--root":
        root = pathlib.Path(args[1]).resolve()
    elif args:
        print("使い方: js_layer_lint.py [--root DIR]", file=sys.stderr)
        return 2
    problems, nfiles = lint(root)
    if problems is None:
        print("  - ui/ がありません → 省略")
        return 0
    if problems:
        for p in problems[:40]:
            print(f"  ✗ {p}")
        if len(problems) > 40:
            print(f"  … 他 {len(problems) - 40} 件")
        print(f"層lint: {len(problems)} 件の違反")
        return 1
    print(f"✓ 層lint: {nfiles}ファイル 違反なし")
    return 0


if __name__ == "__main__":
    sys.exit(main())
