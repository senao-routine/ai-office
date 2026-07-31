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
IMPORT_RULES = [
    ("ui/core", r"/ui/(platform|iso|pixel)/", "core が上位層を import している"),
    ("ui/platform", r"/ui/(iso|pixel)/", "platform が presentation を import している"),
    ("ui/iso", r"/ui/pixel/", "iso が pixel を import している（presentation は互いに独立）"),
    ("ui/pixel", r"/ui/iso/", "pixel が iso を import している（presentation は互いに独立）"),
]

_BLOCK = re.compile(r"/\*.*?\*/", re.S)
_LINE = re.compile(r"(^|[^:])//.*$", re.M)
_STR = re.compile(r"(['\"`])(?:\\.|(?!\1).)*\1", re.S)


def strip_noise(src):
    """コメントと文字列リテラルを潰す（誤検知を防ぐ）。行数は保つ。"""
    def blank(m):
        return re.sub(r"[^\n]", " ", m.group(0))
    src = _BLOCK.sub(blank, src)
    src = _LINE.sub(lambda m: m.group(1) + " " * (len(m.group(0)) - len(m.group(1))), src)
    return _STR.sub(blank, src)


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
                if re.search(bad, spec):
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
