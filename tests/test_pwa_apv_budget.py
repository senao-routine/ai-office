#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""R116-S1 門④（本人 2026-10-09「bash の許可を与えるのもごちゃごちゃしすぎている」・計画 ~/.claude/plans/r116-s1-card.md）:
スマホの許可まわりの文言を「足した分より多く消した」まま保つ。数えるのは本人に見える日本語の文言の種類（同じ文言は 1 本）。

  範囲＝ ui/pwa/app.js の許可の関数（apv*・approve*・openApprove・closeApprove・onApvItem・cancelApproveDeny）の T("…") の日本語、
         言語切替（applyLangChrome）で許可の要素（apv*・st_apv*）に入れる文言、app.html のカード（#apvpark）と設定の ┤ 許可 ├。
  上限＝ 40 本（R116 の分析の時点で約 175 本）。危険・注意の理由の種類（APV_RISK）は別枠で 10 語以下（48＋13 の言い回しを畳んだ）。
  古い文言（全画面の許可の画面・ゴーストのボタン・設定へ飛ばす導線）が app.js／app.html に 1 本も残っていないことも見る。
足すなら同じ数を消す（上限を上げない）。"""
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
JS = (ROOT / "ui" / "pwa" / "app.js").read_text(encoding="utf-8")
HTML = (ROOT / "ui" / "pwa" / "app.html").read_text(encoding="utf-8")
JA = re.compile(r"[぀-ヿ㐀-鿿！-｠]")
LIMIT, RISK_LIMIT = 40, 10
APV_FUNCS = re.compile(r"^(apv[A-Z]\w*|approve\w*|openApprove|closeApprove|onApvItem|cancelApproveDeny)$")
OLD = ("スマホで確認して許可", "許可の確認", "表示と署名の元の文字列", "Face ID 不要", "設定で ON にする", "確認して許可（Face ID）",
       "Mac 側で先に決まっていました", "モデルの説明（信用しない）", "全文を見る", "登録だけする", "許可鍵を登録」のリンク",
       "許可は Mac で", "以前は Mac だけで", "確認が必要な操作（危険と分かっている形ではありません）", "続けて許可しすぎです",
       "確認し直しています", "Mac から全文を取得中", "指示を書いて拒否", "照合コード ")


_REGEX_PREV = set("(,=:[!&|?{};+-*%<>~^")


def _skip(src, i):
    """i が文字列・テンプレート・コメント・正規表現の先頭なら、その終わりの次の位置。そうでなければ None（本体の書き換えに使った読み方と同じ）。"""
    c = src[i]
    if c in "\"'":
        j = i + 1
        while j < len(src):
            if src[j] == "\\":
                j += 2
                continue
            if src[j] == c:
                return j + 1
            j += 1
        raise ValueError("unterminated string at %d" % i)
    if c == "`":
        j = i + 1
        while j < len(src):
            if src[j] == "\\":
                j += 2
                continue
            if src[j] == "`":
                return j + 1
            if src[j] == "$" and src[j + 1:j + 2] == "{":
                j = _match(src, j + 1) + 1
                continue
            j += 1
        raise ValueError("unterminated template at %d" % i)
    if c == "/" and i + 1 < len(src):
        n = src[i + 1]
        if n == "/":
            j = src.find("\n", i)
            return len(src) if j < 0 else j
        if n == "*":
            return src.index("*/", i + 2) + 2
        k = i - 1
        while k >= 0 and src[k] in " \t\r\n":
            k -= 1
        prev = src[k] if k >= 0 else ""
        mw = re.search(r"[\w$]+$", src[:k + 1]) if k >= 0 else None
        word = mw.group(0) if mw and not mw.group(0)[0].isdigit() else ""
        if prev in _REGEX_PREV or prev == "" or word in ("return", "typeof", "case", "in", "of", "new", "void", "throw"):
            j, cls = i + 1, False
            while j < len(src):
                ch = src[j]
                if ch == "\\":
                    j += 2
                    continue
                if ch == "[":
                    cls = True
                elif ch == "]":
                    cls = False
                elif ch == "/" and not cls:
                    j += 1
                    while j < len(src) and src[j].isalpha():
                        j += 1
                    return j
                elif ch == "\n":
                    raise ValueError("newline in regex at %d" % i)
                j += 1
    return None


def _match(src, i):
    """src[i] == '{' の対の '}' の位置。"""
    depth, j = 0, i
    while j < len(src):
        e = _skip(src, j)
        if e is not None:
            j = e
            continue
        if src[j] == "{":
            depth += 1
        elif src[j] == "}":
            depth -= 1
            if depth == 0:
                return j
        j += 1
    raise ValueError("unbalanced from %d" % i)


def _functions(src):
    """トップレベルの function 名 → 本文（宣言から対の } まで）。"""
    out = {}
    for m in re.finditer(r"(?:^|(?<=[;}\s/]))function ([A-Za-z_$][\w$]*)\(", src, re.M):
        b = src.index("{", m.end())
        out.setdefault(m.group(1), src[m.start():_match(src, b) + 1])
    return out


def _strip_comments(src):
    """コメントを除いた本文（画面に出る文言の検査用）。"""
    out, i = [], 0
    while i < len(src):
        e = _skip(src, i)
        if e is None:
            out.append(src[i])
            i += 1
            continue
        if src.startswith("//", i) or src.startswith("/*", i):
            out.append(" ")
        else:
            out.append(src[i:e])
        i = e
    return "".join(out)


def _t_first_args(code):
    """T( の第 1 引数の文字列リテラル部分（連結は 1 本に畳む）。"""
    found = []
    for m in re.finditer(r"(?<![\w$.])T\(", code):
        j, depth, lits = m.end(), 0, []
        while j < len(code):
            c = code[j]
            if c in "\"'":
                k = _skip(code, j)
                lits.append(code[j + 1:k - 1])
                j = k
                continue
            if c in "([{":
                depth += 1
            elif c in ")]}":
                if depth == 0:
                    break
                depth -= 1
            elif c == "," and depth == 0:
                break
            j += 1
        s = "…".join(lits)
        if JA.search(s):
            found.append(s)
    return found


def approval_strings():
    fns = _functions(JS)
    seen = set()
    for name, body in fns.items():
        if APV_FUNCS.match(name):
            seen.update(_t_first_args(body))
    lang = fns.get("applyLangChrome", "")
    for m in re.finditer(r'st\("((?:apv|st_apv)[\w]*)",\s*("(?:[^"\\]|\\.)*")', lang):
        s = m.group(2)[1:-1]
        if JA.search(s):
            seen.add(s)
    for m in re.finditer(r'T\("([^"]*)"', lang[lang.find("apvdenytext"):lang.find("apvdenytext") + 400] if "apvdenytext" in lang else ""):
        if JA.search(m.group(1)):
            seen.add(m.group(1))
    i = HTML.index("<div id=apvpark")
    card = HTML[i:HTML.index("</section></div>", i)]
    j = HTML.index("<h3 class=sec id=st_apv_title>")
    group = HTML[j:HTML.index("</section>", j)]
    for frag in (card, group):
        for t in re.findall(r">([^<>]+)<", frag) + re.findall(r'(?:placeholder|aria-label)="([^"]+)"', frag):
            t = t.strip()
            if JA.search(t):
                seen.add(t)
    return seen


def risk_words():
    m = re.search(r"var APV_RISK=\[(.*?)\];", JS, re.S)
    return re.findall(r',"([^"]+)","[^"]+"\]', m.group(1)) if m else []


class ApvBudgetTest(unittest.TestCase):
    def test_strings_within_budget(self):
        got = sorted(approval_strings())
        self.assertLessEqual(len(got), LIMIT, "許可まわりの文言が %d 本（上限 %d）: %s" % (len(got), LIMIT, got))

    def test_risk_words_are_categories(self):
        words = risk_words()
        self.assertTrue(words, "APV_RISK が見つからない")
        self.assertLessEqual(len(words), RISK_LIMIT, words)
        self.assertEqual(len(words), len(set(words)), words)

    def test_old_texts_are_gone(self):
        body = _strip_comments(JS)
        left = [t for t in OLD if t in body or t in HTML]
        self.assertEqual(left, [], "古い許可の画面の文言が残っている: %s" % left)

    def test_single_allow_button_in_markup(self):
        self.assertEqual(HTML.count("id=apv_allow"), 1)
        self.assertEqual(len(re.findall(r'id=["\']?apv_allow', JS)), 0, "JS が #apv_allow を作っている（DOM は 1 つだけ）")


if __name__ == "__main__":
    unittest.main()
