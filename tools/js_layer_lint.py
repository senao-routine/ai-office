#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""新UI（R50）の層の逆流を機械で禁止する番人。

なぜ必要か:
  「core は DOM を触らない」を規約で守ろうとすると必ず破れる。破れた瞬間に
  core は node --test でテストできなくなり、検証の土台が崩れる。
  だから規約ではなくゲートで守る。

層（依存は必ず下向き）:
  ui/iso/**（等）        presentation。スタイル同士は import しない
                           （pixel はユーザー判断で撤去・2026-07-30。規則は温存）
  ui/platform/**           ブラウザに触る（fetch・localStorage・rAF・window）
  ui/core/**               純ロジック。DOM もネットワークも時刻も乱数も触らない

使い方: python3 tools/js_layer_lint.py         （verify.sh と PostToolUse hook から呼ぶ）
"""
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
UI = ROOT / "ui"

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
IMPORT_RULES = [
    ("ui/core", r"/ui/(platform|iso|hud|pixel|pwa)/", "core が上位層を import している"),
    ("ui/platform", r"/ui/(iso|hud|pixel|pwa)/", "platform が presentation を import している"),
    ("ui/hud", r"/ui/(iso|pixel|pwa)/", "hud が presentation を import している（hud は core/platform のみ）"),
    ("ui/iso", r"/ui/pixel/", "iso が他スタイルを import している（presentation は互いに独立）"),
    ("ui/pixel", r"/ui/iso/", "pixel が iso を import している（presentation は互いに独立）"),
]

def strip_noise(src):
    """コメントと文字列リテラルを潰す（誤検知を防ぐ）。行数は保つ。

    R90-P1 で踏んだ罠: 以前は正規表現 3 本で潰していたが、`(['"`])(?:\\.|(?!\\1).)*\\1` は
    改行の無い巨大な 1 行（ui/pwa/app.html＝実ファイル化した PWA シェル）でバックトラックが爆発し
    2 分以上止まった（dev.sh --check・verify ▶2b・PostToolUse hook が全部詰まる）。
    線形の状態機械に置き換える（入力長に比例・正規表現なし）。"""
    out = []
    i, n = 0, len(src)
    while i < n:
        c = src[i]
        nxt = src[i + 1] if i + 1 < n else ""
        if c == "/" and nxt == "*":                       # ブロックコメント
            j = src.find("*/", i + 2)
            j = n if j < 0 else j + 2
            out.append("".join("\n" if ch == "\n" else " " for ch in src[i:j]))
            i = j
        elif c == "/" and nxt == "/" and (i == 0 or src[i - 1] != ":"):   # 行コメント（http:// は除外）
            j = src.find("\n", i)
            j = n if j < 0 else j
            out.append(" " * (j - i))
            i = j
        elif c in "'\"`":                                 # 文字列（エスケープを跨ぐ・テンプレは改行可）
            q = c
            j = i + 1
            while j < n:
                ch = src[j]
                if ch == "\\":
                    j += 2
                    continue
                if ch == q:
                    j += 1
                    break
                if ch == "\n" and q != "`":               # 閉じ忘れの通常文字列は行末で打ち切る
                    break
                j += 1
            out.append("".join("\n" if ch == "\n" else " " for ch in src[i:j]))
            i = j
        else:
            out.append(c)
            i += 1
    return "".join(out)


def imports_of(src):
    """import 文の from 文字列だけを集める（文字列を潰す前の原文から）。"""
    out = []
    for m in re.finditer(r"""\bfrom\s+['"]([^'"]+)['"]|\bimport\s*\(\s*['"`]([^'"`]+)""", src):
        out.append(m.group(1) or m.group(2))
    return out


def main():
    if not UI.is_dir():
        print("  - ui/ がありません → 省略")
        return 0
    problems = []
    files = sorted(p for p in UI.rglob("*.js") if "vendor" not in p.parts)
    for path in files:
        rel = path.relative_to(ROOT).as_posix()
        raw = path.read_text(encoding="utf-8")
        code = strip_noise(raw)

        if rel.startswith("ui/core/"):
            for pattern, why in CORE_FORBIDDEN:
                for m in re.finditer(pattern, code):
                    line = code[:m.start()].count("\n") + 1
                    problems.append(f"{rel}:{line} core に {why}")

        for prefix, bad, why in IMPORT_RULES:
            if not rel.startswith(prefix + "/"):
                continue
            for spec in imports_of(raw):
                # 相対 import（./x.js, ../core/x.js）は自分の位置から絶対化して判定する
                # （"../iso/scene3d.js" のような他スタイル参照を見逃さないため・R90-H）
                spec_abs = spec
                if spec.startswith("."):
                    try:
                        spec_abs = "/" + (path.parent / spec).resolve().relative_to(ROOT).as_posix()
                    except ValueError:
                        pass                      # リポ外へ抜ける相対参照はそのまま判定
                if re.search(bad, spec_abs):
                    problems.append(f"{rel} {why}: {spec}")

        for spec in imports_of(raw):
            if "office_page.html" in spec:
                problems.append(f"{rel} 旧UI(office_page.html)を参照している")

    if problems:
        for p in problems[:40]:
            print(f"  ✗ {p}")
        if len(problems) > 40:
            print(f"  … 他 {len(problems) - 40} 件")
        print(f"層lint: {len(problems)} 件の違反")
        return 1
    print(f"✓ 層lint: {len(files)}ファイル 違反なし")
    return 0


if __name__ == "__main__":
    sys.exit(main())
