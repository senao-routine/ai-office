#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""R99 Phase 3: tools/px_lint.py の第 2 モード（--cockpit）と tools/px_sheet.py の常設回帰テスト。

なぜ要るか:
  本物のツリーは委譲（Codex）と統合の途中で緑にも赤にもなる＝本物だけ見ていると、門の規則が壊れても
  気づけない。**合成の木**（3 ベンダー × 24 コマ・契約のタイル・配置表・コマ表）を「緑の基準」として作り、
  1 か所ずつわざと壊して**その番号で**赤になることを確かめる（本物の ui/ は 1 バイトも触らない）。

守るもの（丸数字は px_lint.py の docstring の①〜⑦）:
  - 基準の木は緑（0 件を見て ✓ にしていないことも、緑の行の件数で見る）
  - cells・tiles・pxroom・palette のどれかが評価できない・値が無い → 必ず赤（節が丸ごと飛んで ✓ にならない）
    ／検査の節が黙って飛んでも最後の砦（検査した物が 0 件）で赤
  - わざと欠けたコマ・平らな 9 コマの形・別名・pxtop が無い・余計なベンダー・PX_VENDORS の順違い → ②
  - ずれた寸法（PX_CELL_W・pxroom の ROOM.robot・gen_cells の W, H・1 コマだけ 11 桁）→ ①／②
  - 止まって見える歩行（折り返しの 3→0 も）・見分けられないベンダー（claude 以外のペアも）・idle0 が無い → ③
  - 行の長さが揃わない・外周が全透明・凡例に無い文字のタイル・TILE_PALETTE が空 → ④
  - 余ったタイル・配置表が無いタイルを指す・許可リストの腐り（消えた・置かれた・描かれた・印になった）・
    TOP_MARK_TILES に無い mark* → ⑤
  - 描画のコードが直接描くタイル（ui/cockpit の tileSprite("…")・T("…") の字面）: 描くのをやめた足跡・注釈にだけ
    残った呼び出し・許可リストの項目を描き始めた・PX_TILES に無いタイルを描く・描画のモジュールが無い → ⑤
    （字句解析 js_code_only は注釈だけを潰し、文字列・テンプレート・正規表現の中の // や引用符を取り違えない）
  - 契約の寸法違い（印は pxtop・扉は DOOR）・契約のタイルが無い・扉の位置（x だけ・y だけ）・会議室のドットの開口が
    GLASS_OPENING とずれる／塞がる／左右どちらかへ広がる／上の行でだけ広がる／框の行とずれる・開口つきガラスが
    1 枚でない（0 枚・2 枚）・開口がタイルの左右の端に掛かる → ⑥。'u'（落ち影）は開口の中なら通れる床（緑）・
    左右の列なら開口が広い（赤）・框より下の行の左右の列は床（緑）
  - 閾値の境界: 歩行の差 5 で赤・6 で緑／ベンダーの差 9 で赤・10 で緑
  - 最後の砦: 契約のタイルを 1 枚も突き合わせなかったら赤
  - ui/cockpit に PNG（下の階層・大文字の拡張子も）→ ⑦
  - 公開物（design/ も tools/publish_snapshot.sh も無い）では生成器の寸法を比べない・開発ツリーで design/ が消えたら赤
  - px_sheet: 無いコマを黙って飛ばさず赤い × で描く・評価エラー（cells・tiles・pxroom・pxtop）を「未」に化けさせない
    （rc=1）・描画のコードが描くタイルを「未配置」と書かない・見出しの帯にベンダーの idle0 を ×8・原本の glassWall にも
    配置表のガラスの塗り（Pillow の在る .venv が在るときだけ）
"""
import copy
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
LINT = ROOT / "tools" / "px_lint.py"
SHEET = ROOT / "tools" / "px_sheet.py"
TMP_BASE = ROOT / "tests" / "artifacts" / ".tmp"

sys.path.insert(0, str(ROOT / "tools"))
import px_lint  # noqa: E402
import px_sheet  # noqa: E402  （Pillow は main の中でだけ import する＝ここは純関数だけ使う）

NAMES = ["idle0", "idle1", "type0", "type1", "read0", "read1", "raise0", "raise1", "think0", "rest0",
         "rest1", "done0"] + [f"walk{d}{i}" for d in "SNE" for i in range(4)]
VENDORS = ["claude", "codex", "openclaw"]
W, H = 12, 16
HEXES = ["#101010", "#202020", "#303030", "#404040"]
# 合成の木の印（名前と寸法の正本は pxtop の TOP_MARK_TILES と px_lint.MARK_SIZE_EXPORT の表＝ここはその合成の値）
MARKS = {"call": "markCall", "warn": "markWarn", "done": "markDone", "wait": "markWait", "think": "markThink"}
MARK_SIZES = {"markCall": [3, 7], "markWarn": [7, 7], "markDone": [7, 6], "markWait": [9, 7], "markThink": [9, 8]}
DOOR = {"x": 150, "y": 156, "w": 18, "h": 4}
GLASS_XY = (58, 16)
OPENING = [108, 38, 12, 2]          # タイル内 x 50..61・行 22..23（足元＝框の行）
# 描画のコードが直接描くタイル（load_cockpit が ui/cockpit の呼び出しの字面から読む形）
DRAWN = {"floor": ["ui/cockpit/room.js:4"], "footstep": ["ui/cockpit/room.js:5"]}
ROOM_JS = """import { PX_TILES } from "./tiles.js";
const T = (name) => PX_TILES[name];
const tileSprite = (name) => PX_TILES[name];
export function paintBack() { T("floor", 0, 0); }
export function paintPrints() { return tileSprite("footstep"); }
"""


def glass(open_x=50, open_w=12, from_row=22, fill="a"):
    """64×26 のガラス（本物の glassWallDoor の形）。框は行 22..23 まで、行 24..25 は落ち影 'u'。
    行 from_row から下端まで、x open_x..open_x+open_w-1 が開口（透明）。"""
    rows = []
    for r in range(26):
        wall = fill if r < 24 else "u"
        if r >= from_row:
            rows.append(wall * open_x + "." * open_w + wall * (64 - open_x - open_w))
        else:
            rows.append(wall * 64)
    return rows


def punch(rows, cols, row_range, ch):
    """rows の (x, y)（x ∈ cols・y ∈ row_range）を ch にした写し。"""
    out = [list(r) for r in rows]
    for y in row_range:
        for x in cols:
            out[y][x] = ch
    return ["".join(r) for r in out]


def differ_by(rows, n, ch="o"):
    """rows と**ちょうど n ドット**違う写し（透明のドットを左上から ch に変える）。"""
    out, k = [list(r) for r in rows], 0
    for y, r in enumerate(out):
        for x, c in enumerate(r):
            if k < n and c == ".":
                r[x], k = ch, k + 1
    assert k == n, f"{n} ドット変えられない"
    return ["".join(r) for r in out]


def frame(vi, fi):
    """コマごとに 1 行（fi % 13 行目）、ベンダーごとに 1 行（13 + vi 行目）を塗る＝隣の歩行コマは 24 ドット・
    ベンダー同士の idle0 も 24 ドット違う（基準の木が③を満たす）。"""
    rows = [["."] * W for _ in range(H)]
    rows[fi % 13] = ["r"] * W
    rows[13 + vi] = ["a"] * W
    rows[12 if fi % 13 != 12 else 11][0] = "c"
    return ["".join(r) for r in rows]


def block(w, h, ch="a"):
    return [ch * w for _ in range(h)]


def base_values():
    """load_cockpit が返す形の、緑の基準（node を通さない純関数の検査に使う）。"""
    tiles = {name: block(w, h) for name, (w, h) in MARK_SIZES.items()}
    tiles.update({"footstep": block(2, 2), "door": block(DOOR["w"], DOOR["h"]),
                  "glassWallDoor": glass(), "glassWall": block(64, 26),
                  "desk": block(4, 3, "b"), "monitorOff": block(2, 2), "monitorOn": block(2, 2, "b"),
                  "floor": block(3, 3)})
    return {
        "root": "(合成)", "node_error": None,
        "cells": {"PX_CELL_W": W, "PX_CELL_H": H, "PX_VENDORS": list(VENDORS),
                  "PX_CELLS": {v: {n: frame(vi, fi) for fi, n in enumerate(NAMES)} for vi, v in enumerate(VENDORS)}},
        "tiles": {"PX_TILES": tiles},
        "palette": {"PALETTE": {"o": HEXES[0], "b": HEXES[1], "B": HEXES[2], "r": HEXES[3], "a": None, "c": None},
                    "TILE_PALETTE": {"a": HEXES[0], "b": HEXES[1], "u": HEXES[2]},
                    "SESSION_COLORS": ["#5fb6c4", "#8fbf5f", "#e09a4f", "#a889d6"],
                    "RAMP": {k: list(HEXES) for k in ("ink", "stone", "shell", "amber")},
                    "__dims__": ["#3b7179", "#597641", "#8b5f31", "#685584"]},
        "pxroom": {"ROOM": {"robot": {"w": W, "h": H}},
                   "FURNITURE": [{"id": "desk0", "tile": "desk", "x": 10, "y": 50, "paint": None},
                                 {"id": "monitor0", "tile": "monitorOff", "lit": "monitorOn", "x": 11, "y": 45,
                                  "paint": None},
                                 # 統合が置く扉と会議室の開口つきガラス（置かれていないと⑤が赤）
                                 {"id": "door", "tile": "door", "x": DOOR["x"], "y": DOOR["y"], "paint": None},
                                 {"id": "glassWall", "tile": "glassWallDoor", "x": GLASS_XY[0], "y": GLASS_XY[1],
                                  "paint": "glass"}],
                   "DOOR": dict(DOOR), "GLASS_OPENING": list(OPENING)},
        "pxtop": {"TOP_CELLS": list(NAMES), "TOP_VENDORS": list(VENDORS), "TOP_MARK_TILES": dict(MARKS),
                  px_lint.MARK_SIZE_EXPORT: copy.deepcopy(MARK_SIZES)},
        "gen": {"W": W, "H": H},
        "images": [],
        "drawn": copy.deepcopy(DRAWN), "draw_sources": ["ui/cockpit/room.js"],
    }


def write_vendor_tables(root, vendors):
    """種類の門の合成表。実ファイルからコピーせず、各読み口の最小の形を固定する。"""
    arr = json.dumps(vendors)
    keys = ", ".join(f"{v}: [1, 1, 1]" for v in vendors)
    sources = {
        "ui/core/pxpose.js": f"export const VENDORS = Object.freeze({arr});\n",
        "ui/core/world.js": f"const VENDORS = {arr};\n",
        "ui/core/pxtop.js": f"export const TOP_VENDORS = Object.freeze({arr});\n",
        "ui/core/pxroom.js": f"const ROOM_VENDORS = new Set({json.dumps(vendors + ['other'])});\n",
        "ui/cockpit/cells.js": f"export const PX_VENDORS = Object.freeze({arr});\n",
        "design/cells.js": f"const PX_VENDORS = Object.freeze({arr});\n",
        "design/gen_cells.py": f"VENDORS = {tuple(vendors)!r}\n",
        "ui/hud/board.js": f"const row = {{ vendor: {arr}.includes(v) ? v : 'other' }};\n",
        "ui/pwa/app.js": f"function vendorOf(e){{return {arr}.indexOf(e)>=0?e:'other'}}\n",
        "ui/pixel/px/palette.js": f"export const SHELL = Object.freeze({{{keys}}});\n",
        "ui/pixel/strip.js": f"const SHEETS = {{{keys}}};\n",
        "tools/px_quantize.py": "SHELL = {" + ", ".join(f"'{v}': (1, 2, 3)" for v in vendors) + "}\n",
        "ui/iso/robot.js": f"const VENDORS = {{{keys}}};\n",
        "ui/iso/rigbot.js": f"const VENDOR_HEAD = {{{keys}}};\n",
        "ui/iso/scene3d.js": "actor.vendor = " + " : ".join(f"'{v}'" for v in vendors) + ";\n",
        "ui/hud/table.js": "const dot = el('i', `pxvendor vendor-${v}`);\n",
        "ui/hud/strings.js": "const DICT = {\n" + "\n".join(
            f"  {lang}: {{ " + ", ".join(f"board_vendor_{v}: '{v}'" for v in vendors + ["other"]) + " },"
            for lang in ("ja", "en")) + "\n};\n",
    }
    for rel in ("ui/hud/hud.css", "ui/pwa/app.css", "ui/pixel/style.css"):
        sources[rel] = "\n".join(f".vendor-{v} {{ background: #abcdef; }}" for v in vendors)
    for rel, src in sources.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(src, encoding="utf-8")


class VendorTables(unittest.TestCase):
    """正本に bot がある木から、各消費側の bot を一つずつ落として赤にする。"""

    def setUp(self):
        TMP_BASE.mkdir(parents=True, exist_ok=True)
        self.root = Path(tempfile.mkdtemp(prefix="px_vendor_tables_", dir=TMP_BASE))
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        write_vendor_tables(self.root, VENDORS + ["bot"])
        marker = self.root / "tools/publish_snapshot.sh"
        marker.write_text("開発ツリーの印\n", encoding="utf-8")

    def test_all_tables_match(self):
        self.assertEqual(px_lint.vendor_table_problems(self.root), [])

    def test_vendor_dot_base_class_is_not_a_vendor(self):
        path = self.root / "ui/pwa/app.css"
        path.write_text(path.read_text(encoding="utf-8") + "\n.vendor-dot { display: inline-block; }\n",
                        encoding="utf-8")
        self.assertEqual(px_lint.vendor_table_problems(self.root), [])
        path.write_text(path.read_text(encoding="utf-8") + "\n.vendor-unknown { background: #abcdef; }\n",
                        encoding="utf-8")
        self.assertTrue(any("余分: unknown" in b for b in px_lint.vendor_table_problems(self.root)))

    def test_bot_removed_from_each_table_is_red(self):
        original = {p: p.read_text(encoding="utf-8") for p in self.root.rglob("*") if p.is_file()}
        write_vendor_tables(self.root, VENDORS)
        without_bot = {p: p.read_text(encoding="utf-8") for p in original}
        for p, text in original.items():
            p.write_text(text, encoding="utf-8")
        for path, text in without_bot.items():
            if text == original[path] or path.relative_to(self.root).as_posix() == "ui/core/pxpose.js":
                continue
            with self.subTest(table=path.relative_to(self.root).as_posix()):
                path.write_text(text, encoding="utf-8")
                try:
                    bad = px_lint.vendor_table_problems(self.root)
                    self.assertTrue(any("bot" in b and path.relative_to(self.root).as_posix() in b for b in bad), bad)
                finally:
                    path.write_text(original[path], encoding="utf-8")

    def test_missing_table_is_red(self):
        for path in list(self.root.rglob("*")):
            if not path.is_file() or path.name == "publish_snapshot.sh":
                continue
            with self.subTest(table=path.relative_to(self.root).as_posix()):
                text = path.read_text(encoding="utf-8")
                path.unlink()
                bad = px_lint.vendor_table_problems(self.root)
                self.assertTrue(any(path.relative_to(self.root).as_posix() in b and "読めない" in b for b in bad), bad)
                path.write_text(text, encoding="utf-8")

    def test_empty_or_unreadable_canonical_is_red(self):
        path = self.root / "ui/core/pxpose.js"
        for src in ("export const VENDORS = Object.freeze([]);", "export const VENDORS = suppliers;",
                    'export const VENDORS = Object.freeze(["claude", "claude"]);'):
            with self.subTest(source=src):
                path.write_text(src, encoding="utf-8")
                bad = px_lint.vendor_table_problems(self.root)
                self.assertTrue(any("正本" in b and "読めない" in b for b in bad), bad)

    def test_bot_translation_required_in_each_language(self):
        path = self.root / "ui/hud/strings.js"
        original = path.read_text(encoding="utf-8")
        for locale in ("ja", "en"):
            with self.subTest(locale=locale):
                lines = original.splitlines()
                lines = [re.sub(r", board_vendor_bot: 'bot'", "", line) if line.startswith(f"  {locale}:")
                         else line for line in lines]
                path.write_text("\n".join(lines), encoding="utf-8")
                bad = px_lint.vendor_table_problems(self.root)
                self.assertTrue(any(locale + ".board_vendor_*" in b and "bot" in b for b in bad), bad)

    def test_both_modes_run_vendor_gate(self):
        with mock.patch.object(px_lint, "vendor_table_problems", return_value=["種類表の欠落"]) as gate, \
             mock.patch.object(px_lint, "expected_cells", return_value=([], [])), \
             mock.patch.object(px_lint, "load_cells", return_value={}), \
             mock.patch.object(px_lint, "PX_DIR", self.root), \
             mock.patch.object(sys, "argv", [str(LINT)]):
            self.assertEqual(px_lint.main(), 1)
            gate.assert_called_once()
        with mock.patch.object(px_lint, "vendor_table_problems", return_value=["種類表の欠落"]) as gate, \
             mock.patch.object(px_lint, "load_cockpit", return_value={}), \
             mock.patch.object(px_lint, "cockpit_problems", return_value=[]):
            self.assertEqual(px_lint.cockpit_main([]), 1)
            gate.assert_called_once()


def write_tree(root, v, room_js=ROOM_JS):
    """base_values と同じ中身の ESM の木を書く（load_cockpit が node で評価する経路ごと確かめる）。
    描画のコードは room_js（評価しない＝呼び出しの字面だけを読まれる）。"""
    def js(rel, text):
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
    c, pal = v["cells"], v["palette"]
    write_vendor_tables(root, c["PX_VENDORS"])
    js("ui/cockpit/cells.js",
       f"export const PX_CELL_W = {json.dumps(c['PX_CELL_W'])};\nexport const PX_CELL_H = {json.dumps(c['PX_CELL_H'])};\n"
       f"export const PX_VENDORS = Object.freeze({json.dumps(c['PX_VENDORS'])});\n"
       f"export const PX_CELLS = Object.freeze({json.dumps(c['PX_CELLS'])});\n")
    js("ui/cockpit/tiles.js", f"export const PX_TILES = Object.freeze({json.dumps(v['tiles']['PX_TILES'])});\n")
    js("ui/cockpit/palette.js", "".join(f"export const {k} = {json.dumps(pal[k])};\n"
                                        for k in ("PALETTE", "TILE_PALETTE", "SESSION_COLORS", "RAMP"))
       + "export function dimTint(hex) { return hex; }\n")
    js("ui/core/pxroom.js", "".join(f"export const {k} = Object.freeze({json.dumps(v['pxroom'][k])});\n"
                                    for k in ("ROOM", "FURNITURE", "DOOR", "GLASS_OPENING"))
       + f"const ROOM_VENDORS = new Set({json.dumps(c['PX_VENDORS'] + ['other'])});\n")
    js("ui/core/pxtop.js", "".join(f"export const {k} = Object.freeze({json.dumps(v['pxtop'][k])});\n"
                                   for k in ("TOP_CELLS", "TOP_VENDORS", "TOP_MARK_TILES", px_lint.MARK_SIZE_EXPORT,
                                             *px_lint.PROP_TABLE_EXPORTS) if k in v["pxtop"]))
    js("design/gen_cells.py", f"W, H = {v['gen']['W']}, {v['gen']['H']}\nVENDORS = {tuple(c['PX_VENDORS'])!r}\n")
    js("tools/publish_snapshot.sh", "開発ツリーの印（design/ を必須にする）\n")
    js("ui/cockpit/room.js", room_js)


class PureChecks(unittest.TestCase):
    """cockpit_problems は IO を持たない純関数＝壊した値を直接渡して、番号つきの赤を確かめる。"""

    def problems(self, mutate=None):
        v = base_values()
        if mutate:
            mutate(v)
        return px_lint.cockpit_problems(v)

    def assertRed(self, mutate, num, *words):
        bad = self.problems(mutate)
        hit = [b for b in bad if b.startswith(num) and all(w in b for w in words)]
        self.assertTrue(hit, f"{num} {words} で赤にならない: {bad}")
        return bad

    def test_base_is_green(self):
        self.assertEqual(self.problems(), [])

    # ② 完全性
    def test_missing_frame(self):
        self.assertRed(lambda v: v["cells"]["PX_CELLS"]["codex"].pop("walkN2"), "②", "codex", "walkN2", "1/24")

    def test_missing_vendor(self):
        self.assertRed(lambda v: v["cells"]["PX_CELLS"].pop("openclaw"), "②", "openclaw", "24 コマ全部が未")

    def test_flat_nine_frames(self):
        def flat(v):
            v["cells"]["PX_CELLS"] = {n: frame(0, i) for i, n in enumerate(NAMES[:9])}
        self.assertRed(flat, "②", "平らな形")

    def test_alias_name_and_alias_value(self):
        def alias(v):
            v["cells"]["PX_CELLS"]["claude"]["walk0"] = "idle0"
        bad = self.assertRed(alias, "②", "TOP_CELLS に無いコマ", "walk0")
        self.assertTrue(any("別名" in b and "claude.walk0" in b for b in bad), bad)

    def test_order(self):
        def swap(v):
            f = v["cells"]["PX_CELLS"]["claude"]
            v["cells"]["PX_CELLS"]["claude"] = dict(reversed(list(f.items())))
        self.assertRed(swap, "②", "claude", "並び")

    def test_pxtop_missing(self):
        self.assertRed(lambda v: v.update(pxtop=None), "②", "pxtop 未作成")

    def test_pxtop_broken(self):
        self.assertRed(lambda v: v.update(pxtop={"__error__": "SyntaxError: x"}), "②", "pxtop.js を評価できない")

    def test_px_vendors_missing(self):
        self.assertRed(lambda v: v["cells"].update(PX_VENDORS=px_lint.UNDEF), "②", "PX_VENDORS")

    def test_unknown_cell_letter(self):
        def z(v):
            v["cells"]["PX_CELLS"]["codex"]["idle1"][0] = "z" + "." * (W - 1)
        self.assertRed(z, "②", "codex.idle1", "凡例に無い文字 z")

    # ① 寸法
    def test_cell_width_mismatch(self):
        self.assertRed(lambda v: v["cells"].update(PX_CELL_W=16), "①", "一致しない", "16×16")

    def test_robot_mismatch(self):
        self.assertRed(lambda v: v["pxroom"]["ROOM"]["robot"].update(h=24), "①", "ROOM.robot=12×24")

    def test_gen_mismatch_and_unreadable(self):
        self.assertRed(lambda v: v.update(gen={"W": 16, "H": 16}), "①", "gen_cells.py の W, H=16×16")
        self.assertRed(lambda v: v.update(gen={"error": "design/gen_cells.py を読み込めない（SyntaxError）"}),
                       "①", "読み込めない")

    def test_one_frame_wrong_size(self):
        def short(v):
            v["cells"]["PX_CELLS"]["openclaw"]["rest1"][5] = "." * (W - 1)
        self.assertRed(short, "②", "openclaw.rest1", "12×16 でない")

    # ③ 動き・見分け
    def test_walk_standing_still(self):
        def still(v):
            f = v["cells"]["PX_CELLS"]["claude"]
            f["walkS1"] = list(f["walkS0"])
        self.assertRed(still, "③", "claude", "walkS0→walkS1", "0 ドット")

    def test_walk_wraps_last_to_first(self):
        def wrap(v):
            f = v["cells"]["PX_CELLS"]["codex"]
            f["walkE3"] = list(f["walkE0"])
        self.assertRed(wrap, "③", "codex", "walkE3→walkE0")

    def test_vendor_indistinguishable(self):
        def same(v):
            cells = v["cells"]["PX_CELLS"]
            cells["codex"]["idle0"] = list(cells["claude"]["idle0"])
        self.assertRed(same, "③", "claude と codex の idle0")

    # ④ タイルの外形と凡例
    def test_tile_ragged(self):
        self.assertRed(lambda v: v["tiles"]["PX_TILES"].update(desk=["aaaa", "aaa", "aaaa"]), "④", "desk", "揃っていない")

    def test_tile_transparent_edge(self):
        self.assertRed(lambda v: v["tiles"]["PX_TILES"].update(desk=["....", "abba", "abba"]), "④", "desk", "上の行")
        self.assertRed(lambda v: v["tiles"]["PX_TILES"].update(markCall=[".a."] * 7), "④", "markCall", "左の列・右の列")

    def test_tile_unknown_letter(self):
        self.assertRed(lambda v: v["tiles"]["PX_TILES"].update(desk=["aaaa", "azza", "aaaa"]), "④", "desk", "z")

    # ⑤ 配置表
    def test_extra_unused_tile(self):
        self.assertRed(lambda v: v["tiles"]["PX_TILES"].update(sofa2=block(3, 3)), "⑤", "置かれていない", "sofa2")

    def test_furniture_points_to_missing_tile(self):
        self.assertRed(lambda v: v["pxroom"]["FURNITURE"].append({"id": "x", "tile": "ghost"}), "⑤", "ghost")

    def test_door_not_placed(self):
        # 扉とガラスの開口は経路探索が依存する＝生成器に在っても配置表に置かれていなければ赤
        def unplace(v):
            v["pxroom"]["FURNITURE"] = [f for f in v["pxroom"]["FURNITURE"] if f["tile"] != "door"]
        self.assertRed(unplace, "⑤", "置かれていない", "door")

    def test_marks_need_no_placement(self):
        # 置かなくてよい印は pxtop の TOP_MARK_TILES の値だけ（基準の木の mark* は配置表に無くても緑）。
        # 名前の形では逃がさない＝表に無い名前（marker）は「置かれていない」で赤
        self.assertEqual(self.problems(), [])
        self.assertRed(lambda v: v["tiles"]["PX_TILES"].update(marker=block(2, 2)), "⑤", "置かれていない", "marker")

    # ⑤ 描画のコードが直接描くタイル（床の目地・足跡）は ui/cockpit の呼び出しの字面が正本
    def test_drawn_tile_stopped_being_drawn(self):
        # 足跡を描くのをやめた（呼び出しが消えた）＝許可リストにも無いので「置かれていない」で赤（腐らせない）
        self.assertRed(lambda v: v["drawn"].pop("footstep"), "⑤", "置かれていない", "footstep")
        self.assertRed(lambda v: v["drawn"].pop("floor"), "⑤", "置かれていない", "floor")

    def test_allowlist_item_drawn_is_rot(self):
        # 許可リスト（置かれず・描かれない理由つき）の項目を描画のコードが描き始めた＝理由が嘘になった
        bad = self.assertRed(lambda v: v["drawn"].update(glassWall=["ui/cockpit/room.js:9"]), "⑤",
                             "許可リストの glassWall", "描画のコード", "ui/cockpit/room.js:9", "描いている")
        self.assertEqual(len(bad), 1, bad)

    def test_drawn_tile_missing_from_tiles(self):
        self.assertRed(lambda v: v["tiles"]["PX_TILES"].pop("floor"), "⑤", "描画のコード", "floor", "PX_TILES に無い")
        self.assertRed(lambda v: v["drawn"].update(ghost=["ui/cockpit/room.js:7"]), "⑤", "ghost", "PX_TILES に無い")

    def test_no_drawing_module(self):
        # 描画のモジュール（PX_TILES を読む ui/cockpit の JS）が見つからない＝名指しで赤（足跡・床の赤に紛れさせない）
        self.assertRed(lambda v: v.update(drawn={}, draw_sources=[]), "⑤", "描画のモジュールが無い")

    # ⑥ 契約の寸法
    def test_contract_size(self):
        self.assertRed(lambda v: v["tiles"]["PX_TILES"].update(markCall=block(3, 6)), "⑥", "markCall", "3×6", "3×7")
        self.assertRed(lambda v: v["tiles"]["PX_TILES"].update(glassWallDoor=block(64, 25)), "⑥", "glassWallDoor")

    def test_contract_missing(self):
        self.assertRed(lambda v: v["tiles"]["PX_TILES"].pop("door"), "⑥", "door", "18×4", "無い")

    # ⑦ 画像
    def test_images(self):
        self.assertRed(lambda v: v.update(images=["ui/cockpit/robot.png"]), "⑦", "robot.png")

    def test_node_error_is_red(self):
        bad = self.problems(lambda v: v.update(node_error="SyntaxError: Unexpected token"))
        self.assertEqual(len(bad), 1)
        self.assertIn("node", bad[0])

    # 評価できないモジュール（節が丸ごと飛んで「0 ベンダー × 0 コマ ✓」にならない）
    def assertUnreadable(self, key, num, rel):
        self.assertRed(lambda v: v.update({key: {"__error__": "SyntaxError: Identifier 'X' has already been declared"}}),
                       num, rel, "評価できない", "SyntaxError")
        self.assertRed(lambda v: v.pop(key), num, rel, "値が無い")

    def test_cells_unreadable(self):
        self.assertUnreadable("cells", "②", "ui/cockpit/cells.js")

    def test_tiles_unreadable(self):
        self.assertUnreadable("tiles", "④", "ui/cockpit/tiles.js")

    def test_pxroom_unreadable(self):
        self.assertUnreadable("pxroom", "①", "ui/core/pxroom.js")

    def test_palette_unreadable(self):
        self.assertUnreadable("palette", "②", "ui/cockpit/palette.js")

    def test_empty_values_are_named(self):
        # 空の表は他の節の赤に紛れさせず、その表を名指しで赤にする（M16・M17・M19）
        self.assertRed(lambda v: v["cells"].update(PX_CELLS={}), "②", "PX_CELLS が無い・空")
        self.assertRed(lambda v: v["tiles"].update(PX_TILES={}), "④", "PX_TILES が無い・空")
        self.assertRed(lambda v: v["pxroom"].update(FURNITURE=[]), "⑤", "FURNITURE が無い・空")

    def test_last_guard_when_a_section_is_skipped_silently(self):
        # 変異「評価できないモジュールを黙って None にする」を入れても、検査した物が 0 件なので赤（最後の砦）
        def silent(data, key, label, num, bad):
            ns = data.get(key)
            return ns if isinstance(ns, dict) and "__error__" not in ns else None
        with mock.patch.object(px_lint, "_module", silent):
            bad = self.problems(lambda v: v.update(cells={"__error__": "SyntaxError"}))
        self.assertTrue(any(b.startswith("—") and "0 件" in b and "コマ" in b for b in bad), bad)

    # ② 検収役の生き残った変異（M1・M2・M6）
    def test_extra_vendor_in_cells(self):                                     # M1
        self.assertRed(lambda v: v["cells"]["PX_CELLS"].update(gemini=v["cells"]["PX_CELLS"]["claude"]),
                       "②", "TOP_VENDORS に無いベンダー", "gemini")

    def test_px_vendors_order_differs(self):                                  # M2（UNDEF ではなく順違い）
        self.assertRed(lambda v: v["cells"].update(PX_VENDORS=list(reversed(VENDORS))), "②", "PX_VENDORS", "違う")

    def test_top_cells_duplicate(self):                                       # M6
        self.assertRed(lambda v: v["pxtop"]["TOP_CELLS"].append("idle1"), "②", "TOP_CELLS", "2 回", "idle1")

    # ③ 検収役の生き残った変異（M3・M5）
    def test_vendor_pair_without_claude(self):                                # M3（claude 相手だけ測る形を落とす）
        def same(v):
            cells = v["cells"]["PX_CELLS"]
            cells["openclaw"]["idle0"] = list(cells["codex"]["idle0"])
        bad = self.assertRed(same, "③", "codex と openclaw の idle0")
        self.assertFalse(any("claude と" in b for b in bad), bad)

    def test_idle0_missing_from_top_cells(self):                              # M5
        self.assertRed(lambda v: v["pxtop"]["TOP_CELLS"].remove("idle0"), "③", "TOP_CELLS に idle0 が無い")

    # ②／④ パレットが空（M7・M8）
    def test_palette_empty(self):                                             # M7
        self.assertRed(lambda v: v["palette"].update(PALETTE={}), "②", "PALETTE が無い")

    def test_tile_palette_empty(self):                                        # M8
        self.assertRed(lambda v: v["palette"].update(TILE_PALETTE={}), "④", "TILE_PALETTE が無い")

    # ⑤ 印は pxtop の TOP_MARK_TILES だけ（mark[A-Z] の名前の形では逃がさない）
    def test_mark_named_tile_not_in_top_mark_tiles(self):
        self.assertRed(lambda v: v["tiles"]["PX_TILES"].update(markSleep=block(3, 3)), "⑤", "置かれていない", "markSleep")

    def test_glasswall_allowlist_rot(self):
        self.assertRed(lambda v: v["tiles"]["PX_TILES"].pop("glassWall"), "⑤", "許可リストの glassWall", "無い")
        self.assertRed(lambda v: v["pxroom"]["FURNITURE"].append({"id": "g2", "tile": "glassWall", "x": 0, "y": 0}),
                       "⑤", "許可リストの glassWall", "置かれた")

    def test_allowlist_entry_became_mark(self):
        def to_mark(v):
            v["pxtop"]["TOP_MARK_TILES"]["wall"] = "glassWall"
            v["pxtop"][px_lint.MARK_SIZE_EXPORT]["glassWall"] = [64, 26]
        self.assertRed(to_mark, "⑤", "許可リストの glassWall", "印になった")

    # ⑤ 職業の小道具と机の小物（R110）: pxtop の TOP_ARCH_TILES・TOP_DESK_TILES の値は「置かれている」
    @staticmethod
    def with_props(v):
        v["tiles"]["PX_TILES"].update(propHat=block(3, 2), deskThing=block(2, 2))
        v["pxtop"]["TOP_ARCH_TILES"] = {"video": "propHat"}
        v["pxtop"]["TOP_DESK_TILES"] = {"video": "deskThing", "writer": "deskThing"}

    def test_prop_tables_count_as_placed(self):
        self.assertEqual(self.problems(self.with_props), [])
        # 表から外した（描画のコードが引かなくなった）小道具は「置かれていない」で赤
        def dropped(v):
            self.with_props(v)
            v["pxtop"]["TOP_ARCH_TILES"] = {}
        self.assertRed(dropped, "⑤", "置かれていない", "propHat")
        def not_exported(v):
            self.with_props(v)
            v["pxtop"]["TOP_DESK_TILES"] = px_lint.UNDEF
        self.assertRed(not_exported, "⑤", "置かれていない", "deskThing")

    def test_prop_table_points_to_missing_tile(self):
        def ghost(v):
            self.with_props(v)
            v["pxtop"]["TOP_DESK_TILES"]["design"] = "deskGhost"
        self.assertRed(ghost, "⑤", "TOP_DESK_TILES", "deskGhost", "PX_TILES に無い")

    def test_prop_table_malformed(self):
        def bad(v):
            self.with_props(v)
            v["pxtop"]["TOP_ARCH_TILES"] = ["propHat"]
        self.assertRed(bad, "⑤", "TOP_ARCH_TILES", "{職業: タイル名} でない")
        def empty_name(v):
            self.with_props(v)
            v["pxtop"]["TOP_ARCH_TILES"]["audio"] = ""
        self.assertRed(empty_name, "⑤", "TOP_ARCH_TILES", "{職業: タイル名} でない")

    def test_allowlist_entry_became_prop(self):
        def to_prop(v):
            v["pxtop"]["TOP_ARCH_TILES"] = {"infra": "glassWall"}
        self.assertRed(to_prop, "⑤", "許可リストの glassWall", "小道具になった")

    # ⑥ 印の名前と寸法は pxtop が正本
    def test_mark_tiles_not_exported(self):
        self.assertRed(lambda v: v["pxtop"].update(TOP_MARK_TILES=px_lint.UNDEF), "⑥", "TOP_MARK_TILES")

    def test_mark_sizes_not_exported(self):
        self.assertRed(lambda v: v["pxtop"].update({px_lint.MARK_SIZE_EXPORT: px_lint.UNDEF}), "⑥",
                       px_lint.MARK_SIZE_EXPORT, "export していない")

    def test_mark_size_mismatch_follows_pxtop(self):
        # 正本（pxtop）の寸法を変えるとタイルが契約違反になる＝px_lint は数字を自前で持たない
        self.assertRed(lambda v: v["pxtop"][px_lint.MARK_SIZE_EXPORT].update(markWait=[9, 8]), "⑥", "markWait",
                       "9×7", "9×8", "pxtop")

    def test_mark_tile_missing(self):
        self.assertRed(lambda v: v["tiles"]["PX_TILES"].pop("markWarn"), "⑥", "契約のタイル markWarn", "無い")

    def test_mark_size_row_for_unknown_tile(self):
        self.assertRed(lambda v: v["pxtop"][px_lint.MARK_SIZE_EXPORT].update(markGhost=[3, 3]), "⑥", "markGhost")

    # ⑥ 扉（寸法は DOOR が正本・配置表の 1 枚が DOOR の位置）
    def test_door_size_follows_door(self):
        self.assertRed(lambda v: v["pxroom"]["DOOR"].update(w=17), "⑥", "door", "18×4", "17×4", "DOOR")

    def test_door_placed_off_door(self):
        def move(v):
            next(f for f in v["pxroom"]["FURNITURE"] if f["tile"] == "door")["x"] = 140
        self.assertRed(move, "⑥", "扉", "(140,156)", "(150,156)")

    def test_door_placed_off_door_y_only(self):
        def move(v):
            next(f for f in v["pxroom"]["FURNITURE"] if f["tile"] == "door")["y"] = 150
        self.assertRed(move, "⑥", "扉", "(150,150)", "(150,156)")

    def test_door_placed_twice(self):
        self.assertRed(lambda v: v["pxroom"]["FURNITURE"].append({"id": "door2", "tile": "door", "x": 150, "y": 156}),
                       "⑥", "扉", "2 枚")

    def test_door_not_exported(self):
        self.assertRed(lambda v: v["pxroom"].update(DOOR=px_lint.UNDEF), "⑥", "DOOR", "export されていない")

    # ⑥ 会議室のガラス: ドットの開口が GLASS_OPENING（経路の正本）と一致する
    def set_glass(self, rows):
        return lambda v: v["tiles"]["PX_TILES"].update(glassWallDoor=rows)

    def test_glass_opening_shifted(self):
        self.assertRed(self.set_glass(glass(open_x=40)), "⑥", "glassWallDoor", "一致しない", "x 50..61")

    def test_glass_opening_blocked(self):
        self.assertRed(self.set_glass(block(64, 26)), "⑥", "glassWallDoor", "一致しない", "塞がっている")

    def test_glass_opening_widened(self):
        self.assertRed(self.set_glass(glass(open_x=49, open_w=13)), "⑥", "左右 1 列", "x 49・62", "広い")

    def test_glass_opening_shut_below_the_foot(self):
        # 足元の行（22..23）は開いていても、その下（24..25）に框が残っていれば赤（開口は下端まで）
        rows = glass()
        rows[25] = "a" * 64
        self.assertRed(self.set_glass(rows), "⑥", "行 25", "開口の列が通れない")

    def test_glass_opening_open_above_the_foot_is_fine(self):
        # 開口が上（立面の扉口）へ続くのは正しい。その行でも左右 1 列は不透明なら緑
        self.assertEqual(self.problems(self.set_glass(glass(from_row=8))), [])

    def test_glass_opening_moved_in_pxroom(self):
        # 経路の開口だけ動かした（ドットは据え置き）＝人が框の絵を突き抜ける
        self.assertRed(lambda v: v["pxroom"].update(GLASS_OPENING=[100, 38, 12, 2]), "⑥", "一致しない", "x 42..53")

    def test_glass_opening_out_of_tile(self):
        self.assertRed(lambda v: v["pxroom"].update(GLASS_OPENING=[110, 38, 12, 2]), "⑥", "収まらない")

    def test_glass_opening_not_exported(self):
        self.assertRed(lambda v: v["pxroom"].update(GLASS_OPENING=px_lint.UNDEF), "⑥", "GLASS_OPENING",
                       "export されていない")

    def test_glass_wall_door_not_placed_once(self):
        def unplace(v):
            v["pxroom"]["FURNITURE"] = [f for f in v["pxroom"]["FURNITURE"] if f["tile"] != "glassWallDoor"]
        self.assertRed(unplace, "⑥", "glassWallDoor が 0 枚")

    def test_glass_wall_door_placed_twice(self):
        # 2 枚＝どちらのドットを GLASS_OPENING と突き合わせるか決まらない（1 枚だけ見て緑にしない）
        self.assertRed(lambda v: v["pxroom"]["FURNITURE"].append(
            {"id": "glassWall2", "tile": "glassWallDoor", "x": 0, "y": 60, "paint": "glass"}), "⑥", "glassWallDoor が 2 枚")

    def test_glass_opening_widened_right(self):
        self.assertRed(self.set_glass(glass(open_x=50, open_w=13)), "⑥", "左右 1 列", "x 49・62", "行 22..23", "広い")

    def test_glass_opening_widened_above_only(self):
        # 開口が上（立面の扉口）へ続く行でだけ左の列が抜けた＝足元の行は合っていても絵の開口は広い
        rows = punch(glass(from_row=8), [49], range(8, 22), ".")
        self.assertRed(self.set_glass(rows), "⑥", "左右 1 列", "行 8..21", "広い")

    def test_glass_shadow_in_opening_is_floor(self):
        # 統括の裁定: 'u'（落ち影）は開口の中に在ってよい＝通れる床（足元の行・その下の行のどちらでも緑）
        self.assertEqual(self.problems(self.set_glass(punch(glass(), range(50, 62), range(24, 26), "u"))), [])
        self.assertEqual(self.problems(self.set_glass(punch(glass(), range(50, 62), range(22, 26), "u"))), [])

    def test_glass_shadow_in_side_column_is_open(self):
        # 左右の列の 'u' は框ではない＝ドットの開口が広い（'.' と同じく赤）
        self.assertRed(self.set_glass(punch(glass(from_row=8), [49], range(8, 24), "u")), "⑥", "左右 1 列",
                       "行 8..23", "広い")
        self.assertRed(self.set_glass(punch(glass(), [62], range(22, 24), "u")), "⑥", "左右 1 列", "行 22..23", "広い")

    def test_glass_side_columns_below_the_frame_are_floor(self):
        # 框より下（落ち影の行 24..25）は床＝左右の列が通れても緑（本物のタイルは x 49・62 の行 24..25 が 'u'）
        self.assertEqual(self.problems(self.set_glass(punch(glass(), [49, 62], range(24, 26), "."))), [])

    def test_glass_opening_row_off_the_frame(self):
        # 経路の開口の行が框の行とずれた（開口は上へ続くので列は通れる＝行の突き合わせでだけ見つかる）
        for y, last in ((37, 22), (39, 24)):
            def move(v, y=y):
                v["tiles"]["PX_TILES"]["glassWallDoor"] = glass(from_row=8)
                v["pxroom"]["GLASS_OPENING"] = [108, y, 12, 2]
            self.assertRed(move, "⑥", f"最下行 {last}", "框の最下行", "23", "ずれている")

    def test_glass_opening_touches_tile_edge(self):
        # 開口の左の框の列がタイルの外（c0=0）＝右端（c0+w=64）と同じく収まらない
        self.assertRed(lambda v: v["pxroom"].update(GLASS_OPENING=[58, 38, 12, 2]), "⑥", "x 0..11", "収まらない")
        self.assertRed(lambda v: v["pxroom"].update(GLASS_OPENING=[110, 38, 12, 2]), "⑥", "x 52..63", "収まらない")

    # ③ 閾値の境界（px_lint が差の数字の正本＝tests/cockpit_cells.test.mjs はここを読む）
    def test_walk_diff_threshold(self):
        def diff(n):
            def mut(v):
                f = v["cells"]["PX_CELLS"]["claude"]
                f["walkS1"] = differ_by(f["walkS0"], n)
            return mut
        self.assertRed(diff(px_lint.MIN_WALK_DIFF - 1), "③", "walkS0→walkS1", f"{px_lint.MIN_WALK_DIFF - 1} ドット")
        self.assertEqual(self.problems(diff(px_lint.MIN_WALK_DIFF)), [])
        self.assertEqual(px_lint.MIN_WALK_DIFF, 6)

    def test_vendor_diff_threshold(self):
        def diff(n):
            def mut(v):
                cells = v["cells"]["PX_CELLS"]
                cells["codex"]["idle0"] = differ_by(cells["claude"]["idle0"], n)
            return mut
        self.assertRed(diff(px_lint.MIN_VENDOR_DIFF - 1), "③", "claude と codex", f"{px_lint.MIN_VENDOR_DIFF - 1} ドット")
        self.assertEqual(self.problems(diff(px_lint.MIN_VENDOR_DIFF)), [])
        self.assertEqual(px_lint.MIN_VENDOR_DIFF, 10)

    # 最後の砦: 契約のタイルを 1 枚も突き合わせなかった（契約の表が黙って空になった）
    def test_last_guard_contract_tiles_compared(self):
        with mock.patch.object(px_lint, "contract_tiles", lambda data: ({}, [])):
            bad = self.problems()
        self.assertEqual(len(bad), 1, bad)
        self.assertTrue(bad[0].startswith("—") and "契約のタイル" in bad[0] and "0 件" in bad[0], bad)


class JsCodeOnly(unittest.TestCase):
    """描画のコードの呼び出しの字面を拾う前の字句解析（注釈だけを潰す）。"""

    SRC = r"""a("x"); // tileSprite("gone1")
/* T("gone2")
   T("gone3") */ b(`${"y"}//not a comment ${`${T("inner")}`}`);
const re = /"\/'[/"]/g; tileSprite("kept1");
const half = total / 2; tileSprite("kept2"); const u = "http://x"; T("kept3", 0, 0);
function f() { return /tileSprite\("fake"\)/; }  /** @see tileSprite("gone4") */
function g(a) { if (a) return 1; else return /"/.test(a); } // tileSprite("gone5")
"""
    # 最後の行: `else return /"/` の / を割り算と読むと、引用符が文字列を始めて後ろの注釈を注釈と見なくなる
    # （語の後の空白で語を切る・return の後の / を正規表現と見る、の両方がここで効く）

    def test_comments_blanked_strings_templates_regexes_kept(self):
        code = px_lint.js_code_only(self.SRC)
        self.assertEqual(len(code), len(self.SRC))
        self.assertEqual(code.count("\n"), self.SRC.count("\n"))                  # 行番号が変わらない
        self.assertEqual([m.group(3) for m in px_lint.DRAW_CALL.finditer(code)], ["inner", "kept1", "kept2", "kept3"])
        self.assertIn('"http://x"', code)
        self.assertNotIn("gone", code)

    def test_drawn_tiles_reads_only_modules_that_read_px_tiles(self):
        TMP_BASE.mkdir(parents=True, exist_ok=True)
        root = Path(tempfile.mkdtemp(prefix="px_lint_drawn_", dir=TMP_BASE))
        self.addCleanup(shutil.rmtree, root, ignore_errors=True)
        ui = root / "ui" / "cockpit"
        ui.mkdir(parents=True)
        (ui / "room.js").write_text(ROOM_JS, encoding="utf-8")
        (ui / "index.js").write_text('const T = (k) => k;\nT("door");\n', encoding="utf-8")        # i18n の T
        (ui / "tiles.js").write_text('export const PX_TILES = {};\nT("desk");\n', encoding="utf-8")  # 定義する側
        drawn, sources = px_lint.drawn_tiles(root)
        self.assertEqual(sources, ["ui/cockpit/room.js"])
        self.assertEqual(drawn, {"floor": ["ui/cockpit/room.js:4"], "footstep": ["ui/cockpit/room.js:5"]})


class SheetPure(unittest.TestCase):
    """px_sheet の純関数（Pillow が無くても回る）。"""

    FURN = [{"tile": "glassWallDoor", "paint": "glass"}, {"tile": "window", "paint": "glass"},
            {"tile": "bulletin", "paint": "paper"}, {"tile": "desk", "paint": None}]

    def test_paint_of_prefix_both_ways(self):
        self.assertEqual(px_sheet.paint_of("glassWall", self.FURN), "glass")          # 原本（置かれていない）
        self.assertEqual(px_sheet.paint_of("glassWallDoor", self.FURN), "glass")      # 置かれている
        rev = [{"tile": "glassWall", "paint": "glass"}]
        self.assertEqual(px_sheet.paint_of("glassWallDoor", rev), "glass")            # 逆向きの木
        self.assertIsNone(px_sheet.paint_of("markCall", self.FURN))                   # 無関係は TILE_PALETTE のまま

    def test_eval_errors_are_named(self):
        v = base_values()
        self.assertEqual(px_sheet.eval_errors(v), [])
        v["cells"] = {"__error__": "SyntaxError: Identifier 'PX_CELLS' has already been declared"}
        v["pxtop"] = None                                                             # 未作成は別の注記（ここでは数えない）
        errs = px_sheet.eval_errors(v)
        self.assertEqual(len(errs), 1, errs)
        self.assertIn("ui/cockpit/cells.js を評価できない", errs[0])
        v.pop("tiles")
        self.assertTrue(any("ui/cockpit/tiles.js の値が無い" in e for e in px_sheet.eval_errors(v)))
        # pxroom（配置表・扉・開口）・pxtop（コマ表・印）の評価エラーも「未」に化けさせない
        v = base_values()
        v["pxroom"] = {"__error__": "SyntaxError: Unexpected token"}
        v["pxtop"] = {"__error__": "ReferenceError: x is not defined"}
        errs = px_sheet.eval_errors(v)
        self.assertEqual(len(errs), 2, errs)
        self.assertIn("ui/core/pxroom.js を評価できない: SyntaxError", errs[0])
        self.assertIn("ui/core/pxtop.js を評価できない: ReferenceError", errs[1])

    def test_drawn_tiles_are_not_unplaced(self):
        # 描画のコードが直接描くタイル（足跡・床）・印・許可リストはシートで「未配置」と書かない
        v = base_values()
        furn, marks, drawn = v["pxroom"]["FURNITURE"], set(px_lint.mark_tiles(v)), set(v["drawn"])
        for name in ("footstep", "floor", "markCall", "glassWall", "desk", "monitorOn"):
            self.assertFalse(px_sheet.unplaced(name, furn, marks, drawn), name)
        self.assertTrue(px_sheet.unplaced("footstep", furn, marks, set()))
        self.assertTrue(px_sheet.unplaced("sofa2", furn, marks, drawn))

    def test_sheet_order_starts_with_contract(self):
        v = base_values()
        v["tiles"]["PX_TILES"] = dict(reversed(list(v["tiles"]["PX_TILES"].items())))   # 生成物の順に頼らない
        _, _, order, notes = px_sheet.plan(v)
        self.assertEqual(order[:8], ["markCall", "markWarn", "markDone", "markWait", "markThink",
                                     "footstep", "door", "glassWallDoor"])
        self.assertEqual(notes, [])


@unittest.skipUnless(shutil.which("node"), "node が無い（verify ▶2b と同じく node 前提の門）")
class EndToEnd(unittest.TestCase):
    """合成の木を実際に書き、`px_lint.py --cockpit --root` を走らせる（node で ESM を評価する経路ごと）。"""

    def setUp(self):
        TMP_BASE.mkdir(parents=True, exist_ok=True)
        self.root = Path(tempfile.mkdtemp(prefix="px_lint_cockpit_", dir=TMP_BASE))
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)

    def run_lint(self, values=None):
        if values is not None:
            write_tree(self.root, values)
        r = subprocess.run([sys.executable, str(LINT), "--cockpit", "--root", str(self.root)],
                           capture_output=True, text=True, timeout=120)
        return r.returncode, r.stdout + r.stderr

    def test_green_then_each_break(self):
        v = base_values()
        rc, out = self.run_lint(v)
        self.assertEqual(rc, 0, out)
        self.assertIn("3 ベンダー × 24 コマ = 72 セル", out)

        # わざと欠けたコマ
        broken = copy.deepcopy(v)
        broken["cells"]["PX_CELLS"]["codex"].pop("read1")
        rc, out = self.run_lint(broken)
        self.assertEqual(rc, 1, out)
        self.assertIn("codex: コマが足りない", out)

        # ずれた寸法
        broken = copy.deepcopy(v)
        broken["cells"]["PX_CELL_H"] = 24
        rc, out = self.run_lint(broken)
        self.assertEqual(rc, 1, out)
        self.assertIn("① ロボの寸法が一致しない", out)

        # 余ったタイル
        broken = copy.deepcopy(v)
        broken["tiles"]["PX_TILES"]["unusedThing"] = block(2, 2)
        rc, out = self.run_lint(broken)
        self.assertEqual(rc, 1, out)
        self.assertIn("unusedThing", out)

        # PNG（下の階層・大文字の拡張子）
        rc, out = self.run_lint(v)
        self.assertEqual(rc, 0, out)
        (self.root / "ui" / "cockpit" / "sub").mkdir()
        (self.root / "ui" / "cockpit" / "sub" / "robot.PNG").write_bytes(b"\x89PNG\r\n\x1a\n")
        rc, out = self.run_lint()
        self.assertEqual(rc, 1, out)
        self.assertIn("⑦", out)
        self.assertIn("ui/cockpit/sub/robot.PNG", out)

    def test_pxtop_missing_and_export_forgotten(self):
        v = base_values()
        write_tree(self.root, v)
        (self.root / "ui" / "core" / "pxtop.js").unlink()
        rc, out = self.run_lint()
        self.assertEqual(rc, 1, out)
        self.assertIn("pxtop 未作成", out)
        # export を忘れた＝本番の import が undefined を掴む（字面で拾う門だと 0 件で緑になる形）
        write_tree(self.root, v)
        p = self.root / "ui" / "core" / "pxtop.js"
        p.write_text(p.read_text(encoding="utf-8").replace("export const TOP_CELLS", "const TOP_CELLS"),
                     encoding="utf-8")
        rc, out = self.run_lint()
        self.assertEqual(rc, 1, out)
        self.assertIn("TOP_CELLS が文字列の配列でない", out)

    def test_prop_tables_read_through_node(self):
        # R110: 小道具の表は node で pxtop を評価して読む（export を忘れたら表は空＝小道具は「置かれていない」で赤）
        v = base_values()
        PureChecks.with_props(v)
        write_tree(self.root, v)
        rc, out = self.run_lint()
        self.assertEqual(rc, 0, out)
        self.assertIn("小道具 2", out)
        p = self.root / "ui" / "core" / "pxtop.js"
        p.write_text(p.read_text(encoding="utf-8").replace("export const TOP_ARCH_TILES ", "const TOP_ARCH_TILES "),
                     encoding="utf-8")
        rc, out = self.run_lint()
        self.assertEqual(rc, 1, out)
        self.assertIn("propHat", out)
        self.assertIn("置かれていない", out)

    def test_contract_exports_forgotten(self):
        # 契約の正本（pxroom の DOOR・GLASS_OPENING／pxtop の印の名前と寸法）の export を忘れたら、node の評価で赤
        v = base_values()
        for rel, name, word in (("ui/core/pxroom.js", "GLASS_OPENING", "GLASS_OPENING"),
                                ("ui/core/pxroom.js", "DOOR", "DOOR が"),
                                ("ui/core/pxtop.js", "TOP_MARK_TILES", "TOP_MARK_TILES"),
                                ("ui/core/pxtop.js", px_lint.MARK_SIZE_EXPORT, px_lint.MARK_SIZE_EXPORT)):
            write_tree(self.root, v)
            p = self.root / rel
            p.write_text(p.read_text(encoding="utf-8").replace(f"export const {name} ", f"const {name} "),
                         encoding="utf-8")
            rc, out = self.run_lint()
            self.assertEqual(rc, 1, f"{name}: {out}")
            self.assertIn(word, out)
            self.assertIn("⑥", out)

    def test_each_module_syntax_error_is_red(self):
        # 構文エラー（重複宣言）で評価できないモジュールは名指しで赤（0 ベンダー × 0 コマで ✓ にならない）
        v = base_values()
        for rel, num in (("ui/cockpit/cells.js", "②"), ("ui/cockpit/tiles.js", "④"),
                         ("ui/cockpit/palette.js", "②"), ("ui/core/pxroom.js", "①")):
            write_tree(self.root, v)
            p = self.root / rel
            p.write_text(p.read_text(encoding="utf-8") + "export const X = 1;\nexport const X = 2;\n", encoding="utf-8")
            rc, out = self.run_lint()
            self.assertEqual(rc, 1, f"{rel}: {out}")
            self.assertIn(f"{num} {rel} を評価できない", out)
            self.assertNotIn("✓", out)

    def test_drawing_code_is_read_from_ui_cockpit(self):
        # 描画のコードが直接描くタイルは ui/cockpit の呼び出しの字面（評価しない）から読む
        v = base_values()
        write_tree(self.root, v)
        rc, out = self.run_lint()
        self.assertEqual(rc, 0, out)
        self.assertIn("描画のコードが描く 2（floor・footstep）", out)
        # 許可リストの項目を描き始めた（腐り）＝赤
        write_tree(self.root, v, ROOM_JS + 'export const x = tileSprite("glassWall");\n')
        rc, out = self.run_lint()
        self.assertEqual(rc, 1, out)
        self.assertIn("許可リストの glassWall は描画のコード（ui/cockpit/room.js:6）が描いている", out)
        # 足跡の呼び出しが注釈にだけ残った＝描いていない＝赤
        write_tree(self.root, v, ROOM_JS.replace('return tileSprite("footstep");',
                                                 '/* tileSprite("footstep") */ return null; // tileSprite("footstep")'))
        rc, out = self.run_lint()
        self.assertEqual(rc, 1, out)
        self.assertIn("置かれていないタイル footstep", out)
        # 同じ行の文字列の // を注釈と取り違えない（取り違えると footstep を見落として赤）
        write_tree(self.root, v, ROOM_JS.replace('return tileSprite("footstep");',
                                                 'const u = "http://x"; return tileSprite("footstep");'))
        rc, out = self.run_lint()
        self.assertEqual(rc, 0, out)
        # 描画のモジュールが無い
        (self.root / "ui" / "cockpit" / "room.js").unlink()
        rc, out = self.run_lint()
        self.assertEqual(rc, 1, out)
        self.assertIn("描画のモジュールが無い", out)

    def test_public_tree_skips_generator_dev_tree_requires_it(self):
        v = base_values()
        write_tree(self.root, v)
        shutil.rmtree(self.root / "design")
        rc, out = self.run_lint()
        self.assertEqual(rc, 1, out)                        # 開発ツリー（tools/publish_snapshot.sh が在る＝公開物・配布物から除外している。CLAUDE.md は受け取った人が /init で作りうるので印にしない）では必須
        self.assertIn("design/gen_cells.py が無い", out)
        (self.root / "tools/publish_snapshot.sh").unlink()
        rc, out = self.run_lint()
        self.assertEqual(rc, 0, out)                        # 公開物では比べない（比べていないと行に出す）
        self.assertIn("比べない", out)


def venv_python():
    for cand in (os.environ.get("VENV_PY"), str(ROOT / ".venv" / "bin" / "python")):
        if cand and Path(cand).is_file():
            r = subprocess.run([cand, "-c", "import PIL"], capture_output=True)
            if r.returncode == 0:
                return cand
    return None


@unittest.skipUnless(shutil.which("node") and venv_python(), "Pillow の在る .venv が無い（公開物・CI）")
class SheetDrawsMissing(unittest.TestCase):
    def setUp(self):
        TMP_BASE.mkdir(parents=True, exist_ok=True)
        self.root = Path(tempfile.mkdtemp(prefix="px_sheet_", dir=TMP_BASE))
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self.py = venv_python()

    def sheet(self):
        out = self.root / "sheet.png"
        r = subprocess.run([self.py, str(SHEET), "--root", str(self.root), "--out", str(out)],
                           capture_output=True, text=True, timeout=120)
        return r, out

    def probe(self, out, code):
        n = subprocess.run([self.py, "-c", "from PIL import Image; import sys; "
                            "im = Image.open(sys.argv[1]).convert('RGB'); " + code, str(out)],
                           capture_output=True, text=True, timeout=60)
        return int(n.stdout.strip() or 0)

    def test_missing_frame_is_drawn_red(self):
        v = base_values()
        v["cells"]["PX_CELLS"]["codex"].pop("walkE2")
        v["cells"]["PX_CELLS"]["codex"]["walkX0"] = frame(1, 0)      # コマ表に無いコマ（余り）も描く
        v["tiles"]["PX_TILES"].pop("markThink")
        write_tree(self.root, v)
        r, out = self.sheet()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        # walkE2 は列＋鏡の列（walkW2）× 個体色 3 行 = 6 枠が「未」
        self.assertIn("コマの未 1・余 1", r.stdout)
        self.assertIn("契約の未 1", r.stdout)
        red = self.probe(out, "print(sum(1 for p in im.getdata() if p == (0xe5, 0x48, 0x4d)))")
        self.assertGreater(red, 100, "無いコマ・無い契約のタイルが赤い × で描かれていない")

    def test_header_band_draws_vendor_idle0_x8(self):
        # 合成の idle0 は 1 行（12 ドット）が個体色 a＝×8 なら 96 画素の横の連なりが 8 行ずつ・3 ベンダーで 24 本。
        # 節の ×4 は 48 画素なので数えない（90 以上だけ数える）
        write_tree(self.root, base_values())
        r, out = self.sheet()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        tint = tuple(int(base_values()["palette"]["SESSION_COLORS"][0][i:i + 2], 16) for i in (1, 3, 5))
        runs = self.probe(out, f"""
w, h = im.size
px = im.load()
n = 0
for y in range(h):
    run = 0
    for x in range(w):
        run = run + 1 if px[x, y] == {tint} else 0
        if run == 90:
            n += 1
print(n)""")
        self.assertEqual(runs, 24, "見出しの帯にベンダー 3 種の idle0 が ×8 で描かれていない")

    def test_eval_error_is_not_drawn_as_missing(self):
        # cells.js が評価できない（構文エラー）ときに「未 72」＋ rc=0 で ✓ にしない＝名指しの注記と rc=1
        write_tree(self.root, base_values())
        p = self.root / "ui" / "cockpit" / "cells.js"
        p.write_text(p.read_text(encoding="utf-8") + "export const PX_CELL_W = 3;\n", encoding="utf-8")
        r, out = self.sheet()
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("✗ コンタクトシート", r.stdout)
        self.assertIn("ui/cockpit/cells.js を評価できない", r.stdout)
        self.assertNotIn("✓", r.stdout)
        self.assertTrue(out.is_file(), "評価エラーでもシートは描く（注記つき）")


if __name__ == "__main__":
    unittest.main()
