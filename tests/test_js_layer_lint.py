#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""R99-P3: tools/js_layer_lint.py（UI の層の番人）の常設回帰テスト。

なぜ要るか:
  本物のツリーには違反が 0 件なので、門の規則が壊れても verify・dev.sh・Stop hook は緑のまま。
  「壊すと落ちる」を、探針を置いた一時ツリーで常に確かめる（本物の ui/ は 1 バイトも触らない）。

守るもの:
  1. cockpit は許可リスト（core / platform / hud / vendor / 自分自身）以外を import できない
     （pixel・iso・pwa・ui 直下・.. 経由・絶対 URL・%2e%2e・JS エスケープ・大文字・シンボリックリンク経由・
     URL パーサーが除くタブ／改行／両端の C0・/ の無い http:・指定子の中の引用符）
  2. core / platform / hud / iso / pixel は cockpit を import できない（相対でも絶対でも）
  3. 拾う import の形: 副作用 import・空白なしの from"…"・export … from・import()・キーワードの後ろのコメント・
     テンプレートの ${import()}。正規表現リテラルの " ` /* を読み違えて後ろの import・Date.now を潰さない
  4. *.mjs も走査する（除外は ui 直下の vendor/ だけ。ui/cockpit/vendor/ などは走査する）
  5. 許可された 6 種（core / platform / hud / vendor / ./x / ../core）と、コメントや '…' "…" の中の
     import／from（誤検知）は緑
判定は Python の版に依らない（urlsplit を使わない）。/usr/bin/python3（3.9）でもこのファイルは同じ結果で通る。
"""
import importlib.util
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LINT = ROOT / "tools" / "js_layer_lint.py"

_spec = importlib.util.spec_from_file_location("js_layer_lint_under_test", LINT)
layer_lint = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(layer_lint)

# 探針の import 先になる、中身が無害な土台のファイル（これだけなら違反 0 件）
BASE = {
    "ui/boot.js": "export const a = 1;\n",
    "ui/core/x.js": "export const a = 1;\n",
    "ui/platform/x.js": "export const a = 1;\n",
    "ui/hud/x.js": "export const a = 1;\n",
    "ui/iso/scene3d.js": "export const a = 1;\n",
    "ui/pixel/strip.js": "export const a = 1;\n",
    "ui/pwa/app.js": "export const a = 1;\n",
    "ui/cockpit/room.js": "export const a = 1;\n",
    "ui/cockpit/term.js": "export const a = 1;\n",
    "ui/cockpit/palette.js": "export const a = 1;\n",
    "ui/app/x.js": "export const a = 1;\n",
    "ui/vendor/xterm/xterm.mjs": "export const Terminal = 1;\n",
}

# (説明, 探針のパス, 探針の中身)。すべて違反として落ちなければならない
RED = [
    ("cockpit→pixel", "ui/cockpit/probe.js", 'import { a } from "/ui/pixel/strip.js";\n'),
    ("cockpit→iso", "ui/cockpit/probe.js", 'import { a } from "/ui/iso/scene3d.js";\n'),
    ("cockpit→pwa", "ui/cockpit/probe.js", 'import { a } from "/ui/pwa/app.js";\n'),
    ("cockpit→ui 直下", "ui/cockpit/probe.js", 'import { a } from "/ui/boot.js";\n'),
    ("cockpit→相対 ../pixel", "ui/cockpit/probe.js", 'import { a } from "../pixel/strip.js";\n'),
    ("cockpit→相対 ./../../ui/iso", "ui/cockpit/probe.js", 'import { a } from "./../../ui/iso/scene3d.js";\n'),
    ("cockpit→許可接頭辞を .. で抜ける", "ui/cockpit/probe.js", 'import { a } from "/ui/cockpit/../pixel/strip.js";\n'),
    # ルートを越える .. はブラウザがルートで止める。越えた先から許可接頭辞へ戻り、もう一度 .. で抜ける形は
    # ファイルシステムでの解決（リポの外へ出るので捨てる）では拾えず、文字列の正規化だけが拾う
    ("cockpit→ルートを越えてから許可接頭辞を .. で抜ける", "ui/cockpit/probe.js",
     'import { a } from "/ui/cockpit/../../../../ui/cockpit/../pixel/strip.js";\n'),
    ("cockpit→副作用 import（絶対）", "ui/cockpit/probe.js", 'import "/ui/pixel/strip.js";\n'),
    ("cockpit→副作用 import（相対）", "ui/cockpit/probe.js", 'import "../iso/scene3d.js";\n'),
    ("cockpit→空白なしの from", "ui/cockpit/probe.js", 'import{a}from"/ui/pixel/strip.js";\n'),
    ("cockpit→export * from", "ui/cockpit/probe.js", 'export * from "/ui/pixel/strip.js";\n'),
    ("cockpit→export { } from（単引用符）", "ui/cockpit/probe.js", "export { a } from '/ui/iso/scene3d.js';\n"),
    ("cockpit→動的 import", "ui/cockpit/probe.js", 'const m = await import("/ui/pixel/strip.js");\n'),
    ("cockpit→絶対 URL の ..", "ui/cockpit/probe.js",
     'import { a } from "http://127.0.0.1:4780/ui/cockpit/../pixel/strip.js";\n'),
    ("cockpit→%2e%2e", "ui/cockpit/probe.js", 'import { a } from "/ui/cockpit/%2e%2e/pixel/strip.js";\n'),
    ("cockpit→相対の %2E%2E", "ui/cockpit/probe.js", 'import { a } from "./%2E%2E/pixel/strip.js";\n'),
    ("cockpit→..%2f（サーバーが unquote して解決）", "ui/cockpit/probe.js",
     'import { a } from "/ui/cockpit/..%2fpixel/strip.js";\n'),
    ("cockpit→JS エスケープの ..", "ui/cockpit/probe.js",
     'import { a } from "/ui/cockpit/\\x2e\\x2e/pixel/strip.js";\n'),
    ("cockpit→大文字（macOS は区別しない）", "ui/cockpit/probe.js", 'import { a } from "/UI/Pixel/strip.js";\n'),
    # R99-P3 3 巡目: URL パーサーはタブと改行を除く（.\t. は ..）。JS の 1 文字エスケープを制御文字へ戻してから判定する
    ("cockpit→JS エスケープのタブで .. を割る", "ui/cockpit/probe.js",
     'import { a } from "/ui/cockpit/.\\t./pixel/strip.js";\n'),
    ("cockpit→JS エスケープの改行で .. を割る", "ui/cockpit/probe.js",
     'import { a } from "/ui/cockpit/.\\n./pixel/strip.js";\n'),
    ("cockpit→JS エスケープの CR で .. を割る", "ui/cockpit/probe.js",
     'import { a } from "/ui/cockpit/.\\r./pixel/strip.js";\n'),
    ("cockpit→生のタブで .. を割る", "ui/cockpit/probe.js", 'import { a } from "/ui/cockpit/.\t./pixel/strip.js";\n'),
    # 両端の C0 制御文字と空白も URL パーサーが除く（urlsplit 任せだと 3.14 は赤・3.9 は緑だった）
    ("cockpit→先頭が空白の絶対 URL", "ui/cockpit/probe.js",
     'import { a } from " http://127.0.0.1:4780/ui/cockpit/../pixel/strip.js";\n'),
    # 特別な scheme（http 等）は scheme の後の / を省ける＝同じオリジンの /ui/pixel/ を読む
    ("cockpit→/ の無い http:", "ui/cockpit/probe.js",
     'import { a } from "http:127.0.0.1:4780/ui/cockpit/../pixel/strip.js";\n'),
    # 指定子は閉じ引用符まで読む（別の引用符・エスケープした引用符で切ると許可された /ui/cockpit/x に見える）
    ("cockpit→指定子の中の別の引用符", "ui/cockpit/probe.js",
     "import { a } from '/ui/cockpit/x\"/../../pixel/strip.js';\n"),
    ("cockpit→指定子の中のエスケープした引用符", "ui/cockpit/probe.js",
     'import { a } from "/ui/cockpit/x\\"/../../pixel/strip.js";\n'),
    ("cockpit→動的 import の中の別の引用符", "ui/cockpit/probe.js",
     "import(\"/ui/cockpit/x'/../../pixel/strip.js\");\n"),
    # キーワードと指定子のあいだのコメント
    ("cockpit→from /* c */ \"x\"", "ui/cockpit/probe.js", 'import { a } from /* c */ "/ui/pixel/strip.js";\n'),
    ("cockpit→import /* c */ \"x\"", "ui/cockpit/probe.js", 'import /* c */ "/ui/pixel/strip.js";\n'),
    ("cockpit→import(/* c */ \"x\")", "ui/cockpit/probe.js", 'import(/* c */ "/ui/pixel/strip.js");\n'),
    ("cockpit→from // c ＋改行", "ui/cockpit/probe.js", 'import { a } from // c\n  "/ui/pixel/strip.js";\n'),
    # 正規表現リテラルの " や /* をコメント・文字列と読み違えると、その先の本物の import を潰して黙って通す
    ("cockpit→正規表現の \" の後ろの同じ行の import", "ui/cockpit/probe.js",
     'const r = /"/; import("/ui/pixel/strip.js");\n'),
    ("cockpit→正規表現の /* の後ろの import", "ui/cockpit/probe.js",
     'const r = /^\\/*/;\nconst m = await import("/ui/pixel/strip.js");\n/* 終 */\n'),
    ("cockpit→文字クラスの / と \" の後ろの import", "ui/cockpit/probe.js",
     'const r = /[/"]/; import("/ui/pixel/strip.js");\n'),
    ("cockpit→return の後ろの正規表現の \"", "ui/cockpit/probe.js",
     'const f = (x) => { return /"/.test(x) || import("/ui/pixel/strip.js"); };\n'),
    ("cockpit→テンプレートの ${import()}", "ui/cockpit/probe.js",
     'const s = `${await import("/ui/pixel/strip.js")}`;\n'),
    ("cockpit の .mjs→pixel", "ui/cockpit/probe.mjs", 'import { a } from "/ui/pixel/strip.js";\n'),
    # 除外は ui 直下の vendor/ だけ（cockpit/vendor/ を踏み台に pixel へ抜けられない）
    ("cockpit/vendor の中→pixel", "ui/cockpit/vendor/probe.js", 'import { a } from "/ui/pixel/strip.js";\n'),
    ("core/vendor の中に Date.now", "ui/core/vendor/probe.js", "export const t = Date.now();\n"),
    ("core の正規表現の /* の後ろの Date.now", "ui/core/probe.js",
     "export const r = /^\\/*/;\nexport const t = Date.now();\n/* 終 */\n"),
    ("core の正規表現の ` の後ろの Date.now", "ui/core/probe.js",
     "export const r = /[`*]+/g;\nexport const t = Date.now();\nexport const u = `x`;\n"),
    ("core→相対 ../cockpit", "ui/core/probe.js", 'import { a } from "../cockpit/room.js";\n'),
    ("core→副作用 import の cockpit", "ui/core/probe.js", 'import "/ui/cockpit/room.js";\n'),
    ("core→ルートを越えてから .. で cockpit", "ui/core/probe.js",
     'import { a } from "/ui/core/../../../../ui/core/../cockpit/room.js";\n'),
    ("core の .mjs に Date.now", "ui/core/probe.mjs", "export const t = Date.now();\n"),
    ("platform→cockpit", "ui/platform/probe.js", 'import { a } from "../cockpit/room.js";\n'),
    ("hud→cockpit", "ui/hud/probe.js", 'import { a } from "/ui/cockpit/room.js";\n'),
    ("iso→cockpit", "ui/iso/probe.js", 'import "../cockpit/room.js";\n'),
    ("pixel→cockpit", "ui/pixel/probe.js", 'import { a } from "/ui/cockpit/room.js";\n'),
    # ) の直後の正規表現（割り算と読むと /* をコメントと取り違え、先の import が消える）
    ("cockpit→) の後の正規表現の /* の先の import", "ui/cockpit/probe.js",
     'if (x) /^\\/*/.test(p);\nimport { a } from "/ui/pixel/strip.js";\n/* end */\n'),
    ("cockpit→) の後の正規表現の \" と同じ行の import", "ui/cockpit/probe.js",
     'if (x) /"/.test(s) && import("/ui/pixel/strip.js");\n'),
    ("cockpit→キーワード名のプロパティの割り算の後の import", "ui/cockpit/probe.js",
     'const r = counts.new / total; // see a/*.js\nimport "/ui/pixel/strip.js";\n/* e */\n'),
]
# 先頭の C0 制御文字は URL パーサーが除く（\b→"b" と読むと "bhttp:" という別の scheme に見えて緑になる）
RED += [(f"cockpit→先頭の \\{e}（JS エスケープの C0）", "ui/cockpit/probe.js",
         f'import {{ a }} from "\\{e}http:127.0.0.1:4780/ui/cockpit/../pixel/strip.js";\n') for e in "bfv0"]

# すべて違反なしでなければならない（許可 6 種＋正規化の誤検知が無いこと）
# R122: ui/app は許可リスト＋部屋と端末の部品の名指しだけ／ほかの層・様式から ui/app へは import しない
RED += [
    ("app→pixel", "ui/app/probe.js", 'import { a } from "/ui/pixel/strip.js";\n'),
    ("app→iso の HUD（名指し以外）", "ui/app/probe.js", 'import { a } from "/ui/iso/index.js";\n'),
    ("app→cockpit の HUD（名指し以外）", "ui/app/probe.js", 'import { a } from "/ui/cockpit/index.js";\n'),
    ("app→名指しの名前で始まる別のファイル", "ui/app/probe.js", 'import { a } from "/ui/cockpit/room.js.bak.js";\n'),
    ("app→pwa", "ui/app/probe.js", 'import { a } from "/ui/pwa/app.js";\n'),
    ("app→相対 ../pixel", "ui/app/probe.js", 'import { a } from "../pixel/strip.js";\n'),
    ("app→許可接頭辞を .. で抜ける", "ui/app/probe.js", 'import { a } from "/ui/app/../pixel/strip.js";\n'),
    ("hud→app", "ui/hud/probe.js", 'import { a } from "/ui/app/x.js";\n'),
    ("core→app", "ui/core/probe.js", 'import { a } from "/ui/app/x.js";\n'),
    ("cockpit→app", "ui/cockpit/probe.js", 'import { a } from "/ui/app/x.js";\n'),
    ("iso→app", "ui/iso/probe.js", 'import { a } from "/ui/app/x.js";\n'),
]

GREEN = [
    ("app→core・hud・platform・自分", "ui/app/probe.js",
     'import { a } from "/ui/core/x.js";\nimport { b } from "/ui/hud/x.js";\nimport { c } from "/ui/platform/x.js";\nimport { d } from "./x.js";\n'),
    ("app→名指しの部屋と端末", "ui/app/probe.js",
     'import { a } from "/ui/iso/scene3d.js";\nimport { b } from "/ui/cockpit/room.js";\nimport { c } from "../cockpit/term.js";\nimport { d } from "/ui/cockpit/palette.js";\n'),
    ("cockpit→core", "ui/cockpit/probe.js", 'import { a } from "/ui/core/x.js";\n'),
    ("cockpit→platform", "ui/cockpit/probe.js", 'import { a } from "/ui/platform/x.js";\n'),
    ("cockpit→hud", "ui/cockpit/probe.js", 'import { a } from "/ui/hud/x.js";\n'),
    ("cockpit→vendor", "ui/cockpit/probe.js", 'import { Terminal } from "/ui/vendor/xterm/xterm.mjs";\n'),
    ("cockpit→自分自身（./x）", "ui/cockpit/probe.js", 'import { a } from "./room.js";\n'),
    ("cockpit→相対 ../core", "ui/cockpit/probe.js", 'import { a } from "../core/x.js";\n'),
    ("cockpit→.. で core へ", "ui/cockpit/probe.js", 'import { a } from "/ui/cockpit/../core/x.js";\n'),
    ("cockpit→?query 付きの自分自身", "ui/cockpit/probe.js", 'import { a } from "/ui/cockpit/room.js?v=2";\n'),
    ("import でない文字列とコメント", "ui/cockpit/probe.js",
     '// 論理は ../pixel/strip.js から core へ移した\nconst s = "/ui/pixel/strip.js";\n'),
    # キーワードがコメントや '…' "…" の中にあるものは import ではない（Stop hook を誤検知の ⛔ で止めない）
    ("行コメントの中の import \"…\"", "ui/cockpit/probe.js", '// 以前は import "/ui/pixel/strip.js" していた\n'),
    ("ブロックコメントの中の from \"…\"", "ui/cockpit/probe.js",
     '/* 使わない: import { a } from "/ui/pixel/strip.js"; */\nexport const a = 1;\n'),
    ("文字列の中の from\"…\"", "ui/cockpit/probe.js", "const s = 'copied from\"/ui/pixel/strip.js\"';\n"),
    ("文字列の中の import(\"…\")", "ui/cockpit/probe.js", "const s = \"import('/ui/pixel/strip.js')\";\n"),
    # 割り算を正規表現と読み違えると、後ろの // を正規表現の閉じと取り違えてコメントの import を拾う
    ("割り算の後ろの行コメント", "ui/cockpit/probe.js", 'const x = a / 2; // 以前は import "/ui/pixel/strip.js"\n'),
    ("後置 ++ の後ろの割り算", "ui/cockpit/probe.js", "let i = 0; const n = i++ / 2, s = 'from\"/ui/pixel/strip.js\"';\n"),
    ("vendor の中は走査しない", "ui/vendor/xterm/probe.mjs", 'import "/ui/pixel/strip.js";\n'),
]


def _write(root, rel, text):
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")
    return p


class JsLayerLintTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="aioffice_layer_lint_")
        self.root = Path(self._tmp.name)
        for rel, text in BASE.items():
            _write(self.root, rel, text)

    def tearDown(self):
        self._tmp.cleanup()

    def _lint_with(self, rel, text):
        probe = _write(self.root, rel, text)
        try:
            return layer_lint.lint(self.root)
        finally:
            probe.unlink()

    def test_base_tree_is_clean(self):
        problems, nfiles = layer_lint.lint(self.root)
        self.assertEqual(problems, [])
        self.assertEqual(nfiles, len(BASE) - 1)          # ui/vendor の 1 本は数えない

    def test_red_cases(self):
        for why, rel, text in RED:
            with self.subTest(why):
                problems, _ = self._lint_with(rel, text)
                hits = [p for p in problems if p.startswith(rel)]
                self.assertTrue(hits, f"{why}: 違反として落ちなかった（{rel}: {text.strip()}）→ {problems}")

    def test_green_cases(self):
        for why, rel, text in GREEN:
            with self.subTest(why):
                problems, _ = self._lint_with(rel, text)
                self.assertEqual(problems, [], f"{why}: 許可されたものが落ちた")

    @unittest.skipUnless(hasattr(os, "symlink"), "シンボリックリンクを作れない環境")
    def test_symlink_into_other_style_is_red(self):
        # ui/cockpit/lnk → ../pixel。文字列の上では /ui/cockpit/ の中だが、サーバーはリンクを辿って pixel を配る
        os.symlink("../pixel", self.root / "ui" / "cockpit" / "lnk")
        problems, _ = self._lint_with("ui/cockpit/probe.js", 'import { a } from "./lnk/strip.js";\n')
        self.assertTrue([p for p in problems if p.startswith("ui/cockpit/probe.js")], problems)

    def test_cli_exit_codes(self):
        # 呼び出し元（verify ▶2b・dev.sh・stop-check.sh）が見るのは終了コード
        ok = subprocess.run([sys.executable, str(LINT), "--root", str(self.root)],
                            capture_output=True, text=True)
        self.assertEqual(ok.returncode, 0, ok.stdout + ok.stderr)
        _write(self.root, "ui/cockpit/probe.js", 'import "/ui/pixel/strip.js";\n')
        ng = subprocess.run([sys.executable, str(LINT), "--root", str(self.root)],
                            capture_output=True, text=True)
        self.assertEqual(ng.returncode, 1, ng.stdout + ng.stderr)
        self.assertIn("ui/cockpit/probe.js", ng.stdout)


if __name__ == "__main__":
    unittest.main()
