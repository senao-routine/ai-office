# -*- coding: utf-8 -*-
"""シェルのコマンド行をトークンへ割る（R100-0 で hooks/office-event.sh から切り出した正本）。

使う側:
  - hooks/office-event.sh（R98 の証拠列: テストを走らせたか・git commit が成功したか）
  - （R100-1b）server/approval_policy.py（スマホから許可してよい形かの判定）
同じ割り方を 2 か所に写すと必ずずれる（片方だけ直る）＝ここ 1 本だけを直す。

★掟
  - 標準ライブラリだけ・**Python 3.9 の構文**（hook は /usr/bin/python3＝3.9 が先に選ばれる）。
    門= tests/test_shell_lex.py（`ast.parse(feature_version=(3, 9))`・固定コーパスの割り方が変わらない）。
  - hook からは sys.path を使わず**ファイルの場所で読む**（cwd＝ユーザーのプロジェクトに同名の
    ファイルがあっても拾わない）。
"""
import re


def split(text, why=None, redir=None):
    """引用・コメント・置換を尊重して**トークン**へ割る。

    返り値= ([セグメント], 証拠にできない形か)。セグメントは [(トークン, 引用を含むか), …]。
    引用のトークン境界を保つのが肝: `TEST_CMD='npx vitest run'` は**代入 1 個**であって
    vitest の実行ではない（引用を剥がして空白で割ると実行と誤る・別モデルレビュー）。
    証拠にできない形= `||`／単独の `&`／コマンド置換（読めるリテラルを除く）／終端の無いヒアドキュメント。
    seps[k] は k 番目のセグメントの後ろの区切り（`&&`・`|`・`;`（改行も）・`||`・`&`・末尾は ""）。
    why（R100-1b）: リストを渡すと「証拠にできない形」の理由を足していく（`or` `bg` `subst` `herestring`
    `heredoc` `procsubst` `funcdef`、unsafe にはしない `heredoc-expand`＝区切りを引用しない本文は展開される）。スマホ許可の判定は `or` だけを読める形として扱う＝割り方は 1 本のまま。
    redir（R100-1b）: リストを渡すとトークンごとに「引用の外の `<` `>` を含むか」を順に足す（全セグメント通しの
    平らな並び）。`-m "a <b@c>"` は本文、`"">/tmp/x` はリダイレクト＝引用の印だけでは見分けられない。"""
    segs, seps, seg, tok, quote, esc, unsafe = [], [], [], [], "", False, False
    why = [] if why is None else why
    redir = [] if redir is None else redir
    tok_redir = False
    tok_quoted = False
    had_and = had_semi = False                           # `&&`（短絡）／`;`（順次）が在ったか
    # サブシェルの括弧が在ったか。括弧は捨てて段を平らに並べるので、`( a && b ) | tail` の `|` が
    # 括弧の外（tail の終了コードが全体のもの）だと段の並びからは読めない＝手前の段を見ない
    had_group = False
    depth = 0
    started = False                                      # 引用で始まった空トークンも 1 個と数える
    heredocs = []                                        # この行で開いたヒアドキュメント（終端, `<<-` か, 区切りを引用したか）

    def end_tok():
        nonlocal tok, tok_quoted, started, tok_redir
        if tok or started:
            seg.append(("".join(tok), tok_quoted))
            redir.append(tok_redir)
        tok, tok_quoted, started, tok_redir = [], False, False, False

    def end_seg(sep=""):
        nonlocal seg
        end_tok()
        if seg:
            segs.append(seg)
            seps.append(sep)
        seg = []

    def skip_bodies(pos):
        """行末の改行の直後から、開いていたヒアドキュメントの本文を読み飛ばす。
        本文は標準入力であって実行ではなく、終了コードの持ち主も変えない。終端が無ければ -1。
        区切りを引用しない形（`<<EOF`）では、シェルは本文の `\\`＋改行を先に繋げてから終端と比べる＝
        生の行で比べると本文の終わりがずれる。`\\` で終わる行があれば読めない（-1）。"""
        for tag, dash, quoted in heredocs:
            while True:
                nl = text.find("\n", pos)
                line = text[pos:] if nl < 0 else text[pos:nl]
                if not quoted and line.endswith("\\"):
                    return -1
                if (line.lstrip("\t") if dash else line) == tag:
                    pos = len(text) if nl < 0 else nl + 1
                    break
                if nl < 0:
                    return -1
                pos = nl + 1
        heredocs.clear()
        return pos

    # 二重引用符の中の `$(cat <<'EOF' … EOF\n)`（Claude Code 標準のコミット形）。区切りを引用した
    # ヒアドキュメントは展開されない＝走るのは cat だけで、値は本文そのもの（1 語）として読める。
    # 値を読んでトークンに入れるので、`--collect-only` を忍ばせても下の非実行判定がそのまま効く。
    LITERAL_SUBST = re.compile(r"\$\(\s*cat[ \t]+<<(-?)[ \t]*(?:'([A-Za-z0-9_]+)'|\"([A-Za-z0-9_]+)\")[ \t]*\n")

    def literal_subst(pos):
        m = LITERAL_SUBST.match(text, pos)
        if not m:
            return None
        dash, tag = m.group(1) == "-", m.group(2) or m.group(3)
        body, p = [], m.end()
        while True:
            nl = text.find("\n", p)
            if nl < 0:
                return None
            line = text[p:nl]
            if (line.lstrip("\t") if dash else line) == tag:
                close = re.match(r"[ \t\n]*\)", text[nl + 1:])
                if not close:
                    return None
                return "\n".join(body).rstrip("\n"), nl + 1 + close.end()
            body.append(line.lstrip("\t") if dash else line)
            p = nl + 1

    i = 0
    while i < len(text):
        c = text[i]
        if esc:
            if c != "\n":                                # `\`＋改行は行継続（どちらも消える）
                tok.append(c); started = True
            esc = False; i += 1; continue
        if c == "\\" and quote != "'":
            esc = True; i += 1; continue
        if quote:
            if c == quote:
                quote = ""
            else:
                if quote == '"' and text[i:i + 2] == "$(":
                    lit = literal_subst(i)
                    if lit is not None:
                        tok.append(lit[0]); i = lit[1]; continue
                # 二重引用符の中でも `$( … )` と `` ` `` は展開される＝置換として扱う
                if quote == '"' and (text[i:i + 2] == "$(" or c == "`"):
                    unsafe = True; why.append("subst")
                tok.append(c)
            i += 1; continue
        if c in "'\"":
            quote = c; tok_quoted = True; started = True; i += 1; continue
        if c == "#" and not depth and not tok and not started:
            nl = text.find("\n", i)
            i = len(text) if nl < 0 else nl
            continue
        if c == "$" and text[i:i + 2] == "$(":
            unsafe = True; why.append("subst"); depth += 1; tok.append(text[i:i + 2]); started = True; i += 2; continue
        if c == "`":
            unsafe = True; why.append("subst")
            depth += 1 if depth == 0 else -1
            tok.append(c); started = True; i += 1; continue
        if c == ")" and depth:
            depth -= 1; tok.append(c); i += 1; continue
        if depth:
            tok.append(c); i += 1; continue
        if c == "<" and text[i:i + 2] == "<<":
            if text[i:i + 3] == "<<<":
                unsafe = True; why.append("herestring")  # ヒアストリングは読まない（保守的）
                break
            # ヒアドキュメント: 終端の語だけ読み、本文は行末の改行で読み飛ばす
            end_tok()
            j = i + 2
            dash = text[j:j + 1] == "-"
            j += 1 if dash else 0
            while j < len(text) and text[j] in " \t":
                j += 1
            word, q, quoted, ansi = [], "", False, False
            while j < len(text):
                ch = text[j]
                if q:
                    if ch == q:
                        q = ""
                    else:
                        word.append(ch)
                elif ch == "$" and text[j + 1:j + 2] in ("'", '"'):
                    ansi = True                          # `<<$'EOF'` の終端は EOF（$ は消える）＝読まない
                    break
                elif ch in "'\"":
                    q = ch; quoted = True
                elif ch == "\\" and j + 1 < len(text):
                    j += 1; word.append(text[j]); quoted = True
                elif ch in " \t\n;&|<>()":
                    break
                else:
                    word.append(ch)
                j += 1
            if q or not word or ansi:
                unsafe = True; why.append("heredoc")
                break
            heredocs.append(("".join(word), dash, quoted))
            if not quoted:
                # 区切りを引用しないヒアドキュメントは本文が展開される（`$( … )`・`` ` ``・`$VAR`）。証拠列は本文を
                # 標準入力として扱うので unsafe にはしない（割り方は不変）が、スマホ許可は本文の中身を検査できない＝理由を足す
                why.append("heredoc-expand")
            # コマンド語の無いヒアドキュメント（`…; <<'EOF'`）もそれ自体が 1 つのコマンドで、終了 0 を返す。
            # 段ごと消すと手前の段（失敗したテスト）が「最後の段」に見える＝印の語を置いて段として残す
            if not seg and not tok:
                seg.append(("<<", False))
                redir.append(False)
            i = j; continue
        if c in "<>" and text[i + 1:i + 2] == "(":
            unsafe = True; why.append("procsubst")       # プロセス置換 <( … ) / >( … )
            break
        if c in " \t":
            end_tok(); i += 1; continue
        if c == "\n" or c == ";":
            had_semi = True; end_seg(";"); i += 1
            if c == "\n" and heredocs:
                i = skip_bodies(i)
                if i < 0:
                    unsafe = True; why.append("heredoc")  # 終端が無い＝どこまでが本文か読めない
                    break
            continue
        if c == "|":
            if text[i:i + 2] == "||":
                unsafe = True; why.append("or"); i += 2; end_seg("||")
            else:
                i += 1; end_seg("|")
            continue
        if c == "&":
            if text[i:i + 2] == "&&":
                had_and = True; end_seg("&&"); i += 2; continue
            prev = "".join(tok).rstrip()
            if prev.endswith(">") or prev.endswith("<"):
                tok.append(c); i += 1; continue
            unsafe = True; why.append("bg")
            end_seg("&"); i += 1; continue
        if c == "(" and (tok or started):
            unsafe = True; why.append("funcdef")         # `pytest() ( … )` ＝関数定義（実行ではない）
            i += 1; continue
        if c in "()":
            had_group = True                             # サブシェルの括弧（中身が終了コードを決める）
            end_tok(); i += 1; continue
        if c in "<>":
            tok_redir = True
        tok.append(c); started = True; i += 1
    end_seg()
    return segs, seps, unsafe, had_and, had_semi, had_group
