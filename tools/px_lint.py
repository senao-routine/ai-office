#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""R98-W2: フロア帯のドット絵（文字グリッド）の番人。

なぜ文字グリッドか: 本番のロボは **PNG を持たない**。`ui/pixel/px/*.js` に 16×24 の文字グリッドを
置き、描画時にパレットを当てる。理由は 3 つ。
  ① diff が読める（1 ドット変えた差分がレビューできる）
  ② golden が決定論（画像デコーダの差が入らない）
  ③ 生成画像をそのまま貼らない（本人裁定: 生成画像は**ムードボードと量子化の入力にだけ**使う）

検査するもの:
  - セルは 16×24 ちょうど・文字は凡例の中だけ
  - 1 セルの色数 ≤8（パレットの表現力を超えない）
  - 輪郭が枠からはみ出していない（右端・左端の列が透明でないセルを弾く＝隣の机と重なる）
  - 4 ベンダー × 14 コマ = 56 セルが揃っている（`ui/core/pxpose.js` の CELLS が正本）
  - 種類の全表が ui/core/pxpose.js の VENDORS と同じ集合（両モード共通・読めない表も赤）
  - 歩行 4 コマは隣り合うコマ同士で ≥6 ドット違う（「歩いていない歩行」を弾く）
  - `ui/pixel/**` に PNG が 0 個・文字グリッドの合計 ≤80KB

第 2 モード `--cockpit`（R99 Phase 3・俯瞰の部屋 `?ui=cockpit` のドット）:
  値は**正本を node で評価して**読む（字面で拾うと、入れ子にした・export を忘れた、のどれでも
  「0 件を検査して ✓」になる）。正本= ui/cockpit/{cells,tiles,palette}.js・ui/core/pxroom.js
  （ROOM.robot・FURNITURE・DOOR・GLASS_OPENING）・ui/core/pxtop.js（TOP_CELLS・TOP_VENDORS＝俯瞰のコマ表・
  TOP_MARK_TILES と印の寸法）・design/gen_cells.py（W, H）。**どれか 1 つでも評価できなければ赤**（評価できない
  モジュールの節を飛ばして「0 ベンダー × 0 コマ ✓」にしない）。
  ① 寸法の三点一致（PX_CELL_W/H・pxroom の ROOM.robot・gen_cells の W, H）
  ② 全ベンダー × 全コマが在る（PX_CELLS[vendor][cell] の入れ子・別名なし・TOP_CELLS の順）・各コマ W×H・
     凡例は PALETTE のキー＋個体色 a/c だけ（平らな 1 ベンダーの形を黙って受け入れない）
  ③ 同じ向きの隣の歩行コマ（最後→最初の折り返しも）が 6 ドット以上違う・ベンダー同士（全ペア）の idle0 が 10 ドット以上違う
  ④ タイルの外形（全行が同じ長さ・外周に全透明の行や列が無い）・凡例は TILE_PALETTE のキーだけ
     （room.js の塗り分け e〜h・v〜x・y は同じ文字を塗り直すだけ＝design/check.py の #4・#5 と同じ扱い）
  ⑤ FURNITURE の tile / lit と、描画のコードが配置表を通らずに直接描くタイルが全部在る。描画のコード＝ui/cockpit で
     PX_TILES を読むモジュールの `tileSprite("名前")`・`T("名前")` の字面（注釈の中は数えない＝消えた呼び出しの
     注釈で緑にしない）。どこにも置かれず・描かれていないタイルは印（pxtop の TOP_MARK_TILES の値**だけ**＝名前の形
     では逃がさない）・職業の小道具と机の小物（R110・pxtop の TOP_ARCH_TILES・TOP_DESK_TILES の値＝描画のコードは
     表を引いて描く）と許可リスト（理由つき）だけ。表の値が PX_TILES に無ければ赤。許可リストの項目が置かれた・
     描かれた・印になった・小道具の表に入った・消えたら赤
  ⑥ 契約（描画側と経路探索が依存する）: 寸法＝印は pxtop・扉は pxroom の DOOR・足跡と開口つきガラスは
     TILE_CONTRACT。扉は FURNITURE の 1 枚が DOOR の位置。会議室のガラスは**ドットの開口が GLASS_OPENING と一致**
     （**開口のドットの門はここが正本**＝tests/pxroom_tiles.test.mjs に同じ規則を持たない。'.' と 'u'＝落ち影は
     通れる床。GLASS_OPENING の最下行＝框の最下行・開口の列は GLASS_OPENING の行から下端まで通れる・その左右 1 列は
     開口が上へ続く行から GLASS_OPENING の最下行まで通れない＝ずれた・塞いだ・広げた開口は赤）
  ⑦ ui/cockpit に画像ファイルが 0 個

使い方:
    python3 tools/px_lint.py            # 検査（verify ▶3b から呼ぶ）
    python3 tools/px_lint.py --list     # 見つけたセルの一覧
    python3 tools/px_lint.py --cockpit  # 第 2 モード（verify ▶3b・dev.sh --check から呼ぶ）
    python3 tools/px_lint.py --cockpit --root DIR   # 別の木を見る（回帰テストが合成の木で使う）
"""
import importlib.util
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PX_DIR = ROOT / "ui" / "pixel"
GRID_DIR = PX_DIR / "px"
WIDTH, HEIGHT = 16, 24
MAX_COLORS = 8
MAX_BYTES = 80 * 1024
# 凡例（ui/pixel/px/palette.js と 1:1）。`.` は透明。
LEGEND = set(".osfeahAB")


def cells_from(source):
    """`name: ["....", ...]` の並びを {name: [行, ...]} に読む（JS を実行しない）。"""
    out = {}
    for match in re.finditer(r"(\w+)\s*:\s*\[([^\]]*)\]", source, re.S):
        rows = re.findall(r'"([^"]*)"', match.group(2))
        if rows:
            out[match.group(1)] = rows
    return out


def load_cells():
    grids = {}
    if not GRID_DIR.is_dir():
        return grids
    for path in sorted(GRID_DIR.glob("*.js")):
        if path.name == "palette.js":
            continue
        grids[path.stem] = cells_from(path.read_text(encoding="utf-8"))
    return grids


def expected_cells():
    """コマ表の正本は ui/core/pxpose.js（二重管理を作らない）。"""
    source = (ROOT / "ui" / "core" / "pxpose.js").read_text(encoding="utf-8")
    cells = re.search(r"export const CELLS = Object\.freeze\(\[(.*?)\]\)", source, re.S)
    vendors = re.search(r"export const VENDORS = Object\.freeze\(\[(.*?)\]\)", source, re.S)
    if not cells or not vendors:
        raise SystemExit("✗ ui/core/pxpose.js から CELLS / VENDORS を読めない")
    return re.findall(r'"([^"]+)"', cells.group(1)), re.findall(r'"([^"]+)"', vendors.group(1))


def diff_dots(a, b):
    return sum(1 for ra, rb in zip(a, b) for ca, cb in zip(ra, rb) if ca != cb)


def main():
    if "--cockpit" in sys.argv:
        return cockpit_main(sys.argv[1:])
    names, vendors = expected_cells()
    grids = load_cells()
    bad = vendor_table_problems(ROOT)
    total = 0

    pngs = [p for p in PX_DIR.rglob("*") if p.suffix.lower() in (".png", ".webp", ".jpg", ".gif")]
    if pngs:
        bad.append(f"ui/pixel に画像ファイルがある（文字グリッドだけで作る掟）: "
                   + ", ".join(p.relative_to(ROOT).as_posix() for p in pngs[:5]))

    if not grids:
        print("  ・ 文字グリッドはまだ無い（ui/pixel/px/*.js）＝検査するものが無い")
        return 1 if bad else 0

    for path in sorted(GRID_DIR.glob("*.js")):
        total += len(path.read_bytes())
    if total > MAX_BYTES:
        bad.append(f"文字グリッドの合計が {total // 1024}KB（上限 {MAX_BYTES // 1024}KB）")

    # 完全性（全ベンダー × CELLS）は「帯を実際に描き始めたら」必須にする。作りかけの基準セルだけの間は
    # 形・色・枠の検査はするが「足りない」では落とさない（門を先に置く流儀を保ったまま赤を作らない）。
    wired = (PX_DIR / "strip.js").is_file()
    complete_required = wired or all(v in grids for v in vendors)
    made = 0
    for vendor in vendors:
        cells = grids.get(vendor)
        if cells is None:
            if complete_required:
                bad.append(f"ベンダー {vendor} の文字グリッド（ui/pixel/px/{vendor}.js）が無い")
            continue
        made += len(cells)
        missing = [n for n in names if n not in cells]
        if missing and complete_required:
            bad.append(f"{vendor}: コマが足りない {' '.join(missing)}")
        for name, rows in cells.items():
            where = f"{vendor}.{name}"
            if len(rows) != HEIGHT or any(len(r) != WIDTH for r in rows):
                bad.append(f"{where}: {WIDTH}×{HEIGHT} でない（{len(rows)} 行）")
                continue
            used = {c for r in rows for c in r}
            unknown = used - LEGEND
            if unknown:
                bad.append(f"{where}: 凡例に無い文字 {''.join(sorted(unknown))}")
            if len(used - {"."}) > MAX_COLORS:
                bad.append(f"{where}: 色数 {len(used - {'.'})}（上限 {MAX_COLORS}）")
            if any(r[0] != "." or r[-1] != "." for r in rows):
                bad.append(f"{where}: 輪郭が左右の枠に触れている（隣の机と重なる）")
        extra = [n for n in cells if n not in names]
        if extra:
            bad.append(f"{vendor}: コマ表に無い名前 {' '.join(extra)}（正本= ui/core/pxpose.js）")
        walk = [cells[n] for n in names if n.startswith("walk") and n in cells]
        for i in range(len(walk) - 1):
            if diff_dots(walk[i], walk[i + 1]) < 6:
                bad.append(f"{vendor}: 歩行コマ {i}→{i + 1} の差が小さい（歩いて見えない）")

    if "--list" in sys.argv:
        for vendor, cells in sorted(grids.items()):
            print(f"  {vendor}: {len(cells)} コマ（{' '.join(sorted(cells))}）")

    if bad:
        print(f"✗ ドット絵 lint: {len(bad)} 件")
        for line in bad[:12]:
            print("   " + line)
        return 1
    want = len(vendors) * len(names)
    state = "" if made >= want else f"（作りかけ {made}/{want}・帯の配線で完全性が必須になる）"
    print(f"✓ ドット絵 lint: {made} セル / {total // 1024}KB・PNG 0 個{state}")
    return 0


# ══ 第 2 モード: cockpit（俯瞰の部屋・R99 Phase 3）═══════════════════════════════════
# 数字は手で書かない（寸法・コマ表・凡例・配置表は正本を node で評価して読む）。ここに手で持つのは
# **契約**（Codex への依頼の「契約」節＝描画側と経路探索が依存する名前と寸法）と許可リストだけ。
UNDEF = "__undefined__"
IMG_EXT = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".bmp", ".avif", ".ico")
HEX = re.compile(r"^#[0-9a-fA-F]{6}$")
WALK_CELL = re.compile(r"^walk([A-Z])(\d+)$")
MIN_WALK_DIFF = 6          # 同じ向きの隣の歩行コマの差（これ未満は止まって見える）
MIN_VENDOR_DIFF = 10       # ベンダー同士の idle0 の差（個体色はプロジェクトの色＝ベンダーはシルエットで見分ける）
# 契約のタイルのうち、**ほかに正本が無いもの**の寸法（幅, 高さ）＝Codex への委譲の契約の表。
# 印の名前と寸法は ui/core/pxtop.js・扉の寸法は ui/core/pxroom.js の DOOR が正本（ここに書き写さない）。
# 会議室の開口の位置（タイル内 x 50〜61・12 幅）の契約は ui/core/pxroom.test.js が GLASS_OPENING でピン留めしていて、
# ここはドットがその GLASS_OPENING と一致するかを見る（数字を 2 か所に持たない）。
TILE_CONTRACT = {"footstep": (2, 2), "glassWallDoor": (64, 26)}
MARK_SIZE_EXPORT = "TOP_MARK_SIZE"   # ui/core/pxtop.js の印の寸法の表の名前（{タイル名: [幅, 高さ]}）
# ui/core/pxtop.js の、描画のコードが表を引いて描くタイルの表（{職業: タイル名}）。R110: 職業の小道具（頭・胸）と机の小物
PROP_TABLE_EXPORTS = ("TOP_ARCH_TILES", "TOP_DESK_TILES")
# 人が通れるドット＝透明 '.' と落ち影 'u'（床の上に落ちた影＝框ではない）。会議室の開口の門の定義
# （統括の裁定 2026-09-24: 開口のドットの門は px_lint の 1 本・'u' は開口の中に在ってよい）。
PASSABLE = frozenset(".u")
# 描画のコードが配置表を通らずに直接描くタイルの呼び出し（ui/cockpit/room.js の tileSprite＝印・足跡の焼き置き、
# T＝paintBack の敷き詰め＝床の目地）。第 1 引数の文字列の字面を拾う（変数で渡すもの＝配置表・印は別の正本から読む）。
# 関数の名前を変えたら、拾えなくなったタイルが「置かれていない」で赤になる（黙って緑にならない）＝ここを直す。
DRAW_CALL = re.compile(r"""\b(tileSprite|T)\(\s*(["'])([^"'\n\\]*)\2""")
# 配置表に載らず・描画のコードも描かないのに在ってよいタイル。**理由の書けるものだけ**。置かれた・描かれた・
# 印になった・消えたら赤（腐らせない）。印（pxtop の TOP_MARK_TILES の値）と描画のコードが直接描くタイル
# （床の目地・足跡）はここに書かずに、それぞれの正本（pxtop・ui/cockpit の呼び出しの字面）から読む。
UNUSED_TILE_ALLOW = {
    "glassWall": "開口の無い原本（委譲の契約で残す）。配置は glassWallDoor に替わった。"
                 "tests/pxroom_tiles.test.mjs が開口つきガラスの比較元（寸法が同じ・開口とその左右の列の外は同じ）に使う",
}
_REGEX_PREV = set("(,=:[!&|?{};+-*%<>~^")
_REGEX_WORDS = {"return", "typeof", "case", "do", "else", "in", "of", "void", "yield", "await", "delete",
                "instanceof", "new", "throw"}


def js_code_only(src):
    """JS の注釈を空白に潰した写し（改行は残す＝行番号が変わらない）。文字列・テンプレート・正規表現の字面は
    そのまま残す（その中の `//` や引用符を注釈・字面の始まりと取り違えない）。描画のコードの呼び出しの字面を
    拾うためだけの最小の字句解析（注釈に残った `tileSprite("x")` を「描いている」と数えない）。"""
    out, i, n = [], 0, len(src)
    tpl = []                     # テンプレートの ${ … } の入れ子（各段の { の深さ）
    prev, word = "", ""          # 注釈と空白を除いた直前の字・直前の語（正規表現と割り算の見分け）
    while i < n:
        c, nx = src[i], src[i + 1] if i + 1 < n else ""
        if c == "/" and nx and nx in "/*":
            k = src.find("\n", i) if nx == "/" else src.find("*/", i + 2)
            j = n if k < 0 else k if nx == "/" else k + 2
            out.append(re.sub(r"[^\n]", " ", src[i:j]))
            i = j
            continue
        if c in "'\"":
            j = i + 1
            while j < n and src[j] not in (c, "\n"):
                j += 2 if src[j] == "\\" else 1
            j = min(j + 1, n)
        elif c == "`" or (c == "}" and tpl and tpl[-1] == 0):
            if c == "}":
                tpl.pop()
            j = i + 1
            while j < n and src[j] != "`" and src[j:j + 2] != "${":
                j += 2 if src[j] == "\\" else 1
            if src[j:j + 2] == "${":
                tpl.append(0)
                j += 2
            else:
                j = min(j + 1, n)
        elif c == "/" and (prev == "" or prev in _REGEX_PREV or word in _REGEX_WORDS):
            j, cls = i + 1, False
            while j < n and src[j] != "\n" and (cls or src[j] != "/"):
                if src[j] == "\\":
                    j += 1
                elif src[j] in "[]":
                    cls = src[j] == "["
                j += 1
            j = min(j + 1, n)
        else:
            if c == "{" and tpl:
                tpl[-1] += 1
            elif c == "}" and tpl:
                tpl[-1] -= 1
            if c.isalnum() or c in "_$":
                word = word + c if prev and (prev.isalnum() or prev in "_$") else c
                prev = c
            elif not c.isspace():
                prev, word = c, ""
            elif word:
                prev = " "               # 語の後の空白（`return /x/` の / を正規表現と見る・`a /b` は割り算）
            out.append(c)
            i += 1
            continue
        out.append(src[i:j])
        prev, word = src[j - 1], ""
        i = j
    return "".join(out)


def _table_literal(src, pattern, opener):
    """指定した表のリテラルを読む。入れ子は辿るが、JS を import して構造を変えない。"""
    match = re.search(pattern, src, re.M)
    if not match:
        raise ValueError("表が見つからない")
    start = match.end() - 1
    if src[start] != opener:
        raise ValueError("表の形を読めない")
    closer = {"[": "]", "{": "}", "(": ")"}[opener]
    depth, quote, escaped = 0, None, False
    for i in range(start, len(src)):
        ch = src[i]
        if quote:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == quote:
                quote = None
        elif ch in "\"'`":
            quote = ch
        elif ch == opener:
            depth += 1
        elif ch == closer:
            depth -= 1
            if depth == 0:
                return src[start + 1:i]
    raise ValueError("表が閉じていない")


def _table_strings(body):
    """種類配列は文字列だけ。読めない式や空表を黙って飛ばさない。"""
    token = r'''["']([a-z][a-z0-9_-]*)["']'''
    values = re.findall(token, body)
    if re.sub(token, "", body).strip(" ,\n\r\t"):
        raise ValueError("種類が文字列リテラルでない")
    if not values or len(values) != len(set(values)):
        raise ValueError("種類が空または重複している")
    return set(values)


def _table_keys(body):
    """種類オブジェクトの最上位キーだけを読む（各種類の部品や寸法のキーは含めない）。"""
    # 表の値は入れ子の配列/オブジェクトまたは定数。入れ子を潰して最上位のキーを拾う。
    flat, depth, quote, escaped = [], 0, None, False
    for ch in body:
        if quote:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == quote:
                quote = None
            if depth == 0:
                flat.append(ch)
        elif ch in "\"'`":
            quote = ch
            if depth == 0:
                flat.append(ch)
        elif ch in "[{(":
            depth += 1
        elif ch in "]})":
            depth -= 1
        elif depth == 0:
            flat.append(ch)
    keys = re.findall(r'''(?:^|,)\s*["']?([a-z][a-z0-9_-]*)["']?\s*:''', "".join(flat))
    if not keys or len(keys) != len(set(keys)):
        raise ValueError("種類キーが空または重複している")
    return set(keys)


def vendor_table_problems(root):
    """R128: 種類の正本と各消費側の表を比較する。表の不在/読取不能は失敗にする。"""
    root, bad, cache = Path(root), [], {}

    def source(rel):
        if rel not in cache:
            cache[rel] = js_code_only((root / rel).read_text(encoding="utf-8"))
        return cache[rel]

    def read(rel, label, kind, pattern, opener):
        try:
            body = _table_literal(source(rel), pattern, opener)
            return _table_keys(body) if kind == "keys" else _table_strings(body)
        except (OSError, ValueError) as exc:
            reason = "ファイルが無い/読めない" if isinstance(exc, OSError) else str(exc)
            bad.append(f"⑧ {rel} の {label} を読めない（{reason}）")
            return None

    canonical = read("ui/core/pxpose.js", "VENDORS（正本）", "array",
                     r"\bexport\s+const\s+VENDORS\s*=\s*Object\.freeze\(\s*\[", "[")
    if canonical is None:
        return bad

    def compare(rel, label, actual):
        if actual is not None and actual != canonical:
            missing, extra = canonical - actual, actual - canonical
            bad.append(f"⑧ {rel} の {label} が正本 VENDORS と違う"
                       f"（不足: {','.join(sorted(missing)) or '-'} / 余分: {','.join(sorted(extra)) or '-'}）")

    arrays = (
        ("ui/core/world.js", "VENDORS", r"\bconst\s+VENDORS\s*=\s*\["),
        ("ui/core/pxtop.js", "TOP_VENDORS", r"\bexport\s+const\s+TOP_VENDORS\s*=\s*Object\.freeze\(\s*\["),
        ("ui/core/pxroom.js", "ROOM_VENDORS", r"\bconst\s+ROOM_VENDORS\s*=\s*new\s+Set\(\s*\["),
        ("ui/cockpit/cells.js", "PX_VENDORS", r"\bexport\s+const\s+PX_VENDORS\s*=\s*Object\.freeze\(\s*\["),
        ("ui/hud/board.js", "vendor", r"\bvendor\s*:\s*\["),
        ("ui/pwa/app.js", "vendorOf", r"\bfunction\s+vendorOf\(e\)\{[^\n]*?\breturn\s*\["),
    )
    for rel, label, pattern in arrays:
        actual = read(rel, label, "array", pattern, "[")
        if label == "ROOM_VENDORS" and actual is not None:
            actual -= {"other"}  # 未知種の印は維持し、正規の種類だけを比べる。
        compare(rel, label, actual)
    # 公開スナップショットは design/ を含まない。開発ツリーでは生成器を必須にする（寸法の門と同じ）。
    if (root / "design/gen_cells.py").is_file() or (root / "tools/publish_snapshot.sh").is_file():
        compare("design/gen_cells.py", "VENDORS", read("design/gen_cells.py", "VENDORS", "array",
                r"^VENDORS\s*=\s*\(", "("))
        compare("design/cells.js", "PX_VENDORS", read("design/cells.js", "PX_VENDORS", "array",
                r"\bconst\s+PX_VENDORS\s*=\s*Object\.freeze\(\s*\[", "["))
    for rel, name in (("ui/pixel/px/palette.js", "SHELL"), ("ui/iso/robot.js", "VENDORS"),
                      ("ui/iso/rigbot.js", "VENDOR_HEAD"), ("ui/pixel/strip.js", "SHEETS"),
                      ("tools/px_quantize.py", "SHELL")):
        compare(rel, name, read(rel, name, "keys", rf"\b(?:const\s+)?{name}\s*=\s*(?:Object\.freeze\(\s*)?\{{", "{"))

    for locale in ("ja", "en"):
        rel, label = "ui/hud/strings.js", f"{locale}.board_vendor_*"
        try:
            body = _table_literal(source(rel), rf"^  {locale}:\s*\{{", "{")
            actual = set(re.findall(r"\bboard_vendor_([a-z][a-z0-9_]*)\s*:", body)) - {"other"}
            if not actual:
                raise ValueError("種類の訳が空")
            compare(rel, label, actual)
        except (OSError, ValueError):
            bad.append(f"⑧ {rel} の {label} を読めない")

    rel = "ui/iso/scene3d.js"
    try:
        match = re.search(r"\bactor\.vendor\s*=\s*([^;]+);", source(rel))
        actual = set(re.findall(r'''["']([a-z][a-z0-9_-]*)["']''', match[1])) if match else set()
        if not actual:
            raise ValueError("種類の決め方を読めない")
        compare(rel, "actor.vendor", actual)
    except (OSError, ValueError):
        bad.append(f"⑧ {rel} の actor.vendor を読めない")

    for rel in ("ui/hud/hud.css", "ui/pwa/app.css", "ui/pixel/style.css"):
        try:
            src = re.sub(r"/\*.*?\*/", "", (root / rel).read_text(encoding="utf-8"), flags=re.S)
            # vendor-dot は PWA の印の基底クラス。other と同じく種類の集合には含めない。
            actual = set(re.findall(r"\.vendor-([a-z][a-z0-9_-]*)\s*\{", src)) - {"other", "dot"}
            if not actual:
                raise ValueError("種類の印が空")
            compare(rel, "vendor-*", actual)
        except (OSError, ValueError):
            bad.append(f"⑧ {rel} の vendor-* を読めない")
    try:
        if not re.search(r"`pxvendor vendor-\$\{v\}`", source("ui/hud/table.js")):
            raise ValueError("種類を引く印が無い")
    except (OSError, ValueError):
        bad.append("⑧ ui/hud/table.js の pxvendor vendor-${v} を読めない")
    return bad


def drawn_tiles(root):
    """描画のコード（ui/cockpit で PX_TILES を読むモジュール・定義する tiles.js は除く）が直接描くタイル
    {名前: ["ui/cockpit/room.js:202", …]} と、読んだモジュールの一覧。注釈の中の呼び出しは数えない。"""
    root = Path(root)
    ui, drawn, sources = root / "ui" / "cockpit", {}, []
    tiles_rel = dict((k, rel) for k, rel, _ in COCKPIT_MODULES)["tiles"]
    for p in sorted(ui.rglob("*.js")) if ui.is_dir() else []:
        rel = p.relative_to(root).as_posix()
        if rel == tiles_rel:
            continue
        try:
            code = js_code_only(p.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError):
            continue
        if not re.search(r"\bPX_TILES\b", code):
            continue
        sources.append(rel)
        for m in DRAW_CALL.finditer(code):
            drawn.setdefault(m.group(3), []).append(f"{rel}:{code.count(chr(10), 0, m.start()) + 1}")
    return drawn, sources

# ESM を本番と同じく import で評価する。モジュールごとに try（1 つ壊れても他は見る）。
# export を忘れた名前は UNDEF（本番の import が undefined を掴むのと同じ）。
COCKPIT_NODE = r"""
import { pathToFileURL } from "node:url";
import path from "node:path";
const [root, ...specs] = process.argv.slice(1);
const out = {};
for (const spec of specs) {
  const [key, rel, names] = spec.split("|");
  try {
    const ns = await import(pathToFileURL(path.join(root, rel)).href);
    const o = {};
    for (const n of names.split(",")) o[n] = n in ns ? ns[n] : undefined;
    if (key === "palette" && typeof ns.dimTint === "function" && Array.isArray(ns.SESSION_COLORS)) {
      o.__dims__ = ns.SESSION_COLORS.map((c) => ns.dimTint(c));
    }
    out[key] = o;
  } catch (e) {
    out[key] = { __error__: String((e && e.message) || e).split("\n")[0].slice(0, 200) };
  }
}
process.stdout.write(JSON.stringify(out, (k, v) => v === undefined ? "__undefined__"
  : typeof v === "function" ? "__function__" : v));
"""
COCKPIT_MODULES = (
    ("cells", "ui/cockpit/cells.js", "PX_CELL_W,PX_CELL_H,PX_CELLS,PX_VENDORS"),
    ("tiles", "ui/cockpit/tiles.js", "PX_TILES"),
    ("palette", "ui/cockpit/palette.js", "PALETTE,TILE_PALETTE,SESSION_COLORS,RAMP"),
    ("pxroom", "ui/core/pxroom.js", "ROOM,FURNITURE,DOOR,GLASS_OPENING"),
    ("pxtop", "ui/core/pxtop.js", f"TOP_CELLS,TOP_VENDORS,TOP_MARK_TILES,{MARK_SIZE_EXPORT},{','.join(PROP_TABLE_EXPORTS)}"),
)
# 評価できないと検査の節が丸ごと飛ぶモジュール（値が無い・__error__ なら必ず赤）。pxtop は「未作成」を別に名指しする
REQUIRED_MODULES = (("cells", "ui/cockpit/cells.js", "②"), ("tiles", "ui/cockpit/tiles.js", "④"),
                    ("palette", "ui/cockpit/palette.js", "②"), ("pxroom", "ui/core/pxroom.js", "①"))


def _gen_cells_dims(root):
    """design/gen_cells.py の W, H を読む（読み込むだけ・main は走らない・__pycache__ も書かない）。
    design/ は公開物に無い（tools/_publish_excludes.sh）＝開発ツリー（tools/publish_snapshot.sh が在る＝公開物・配布物から除外している。CLAUDE.md は受け取った人が /init で作りうるので印にしない）でだけ必須。"""
    gen = root / "design" / "gen_cells.py"
    if not gen.is_file():
        if (root / "tools/publish_snapshot.sh").is_file():
            return {"error": "design/gen_cells.py が無い（開発ツリーでは生成器の寸法も比べる）"}
        return {"skip": "design/ は公開物に無い＝生成器の寸法は比べない"}
    keep = sys.dont_write_bytecode
    sys.dont_write_bytecode = True
    try:
        spec = importlib.util.spec_from_file_location("gen_cells_for_px_lint", gen)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
    except Exception as e:  # noqa: BLE001 — 作り替えの途中で壊れていても名指しで赤にする
        return {"error": f"design/gen_cells.py を読み込めない（{type(e).__name__}: {str(e)[:120]}）"}
    finally:
        sys.dont_write_bytecode = keep
    return {"W": getattr(mod, "W", UNDEF), "H": getattr(mod, "H", UNDEF)}


def load_cockpit(root=ROOT):
    """cockpit の正本の値を 1 つの dict に集める（検査はしない＝cockpit_problems が純関数で見る）。
    pxtop は**ファイルが無ければ None**（「未作成」と「評価できない」を分けて報告する）。"""
    root = Path(root)
    specs = [f"{k}|{rel}|{names}" for k, rel, names in COCKPIT_MODULES
             if k != "pxtop" or (root / rel).is_file()]
    data = {"root": str(root), "node_error": None}
    try:
        r = subprocess.run(["node", "--input-type=module", "-e", COCKPIT_NODE, str(root), *specs],
                           capture_output=True, text=True, timeout=60)
        if r.returncode != 0:
            lines = [ln for ln in r.stderr.splitlines() if ln.strip()]
            data["node_error"] = next((ln for ln in lines if "Error" in ln), lines[-1] if lines else "?")[:200]
        else:
            data.update(json.loads(r.stdout))
    except (OSError, subprocess.TimeoutExpired, ValueError) as e:
        data["node_error"] = f"node で評価できない（{e}）"
    data.setdefault("pxtop", None)
    data["gen"] = _gen_cells_dims(root)
    ui = root / "ui" / "cockpit"
    data["images"] = sorted(p.relative_to(root).as_posix() for p in ui.rglob("*")
                            if p.is_file() and p.suffix.lower() in IMG_EXT) if ui.is_dir() else []
    data["drawn"], data["draw_sources"] = drawn_tiles(root)
    return data


def _is_int(v):
    return isinstance(v, int) and not isinstance(v, bool)


def _is_grid(rows):
    return isinstance(rows, list) and bool(rows) and all(isinstance(r, str) for r in rows)


def _str_list(v):
    return isinstance(v, list) and bool(v) and all(isinstance(x, str) and x for x in v)


def _hex_keys(d):
    return {k for k, v in d.items() if isinstance(v, str) and HEX.match(v)} if isinstance(d, dict) else set()


def _module(data, key, label, num, bad):
    """評価できたモジュールの値（dict）か None。**値が無い（load_cockpit が読めていない）ときも赤**にする
    （None を黙って返すと、そのモジュールの節が丸ごと飛んで「0 件を検査して ✓」になる）。"""
    ns = data.get(key)
    if ns is None:
        bad.append(f"{num} {label} の値が無い（評価の結果に {key} が無い＝検査できない）")
        return None
    if not isinstance(ns, dict) or "__error__" in ns:
        bad.append(f"{num} {label} を評価できない: {ns.get('__error__') if isinstance(ns, dict) else ns}")
        return None
    return ns


def _ok(data, key):
    """評価できたモジュールの値か None（赤は cockpit_problems が出す＝ここは読むだけ。px_sheet も使う）。"""
    ns = data.get(key)
    return ns if isinstance(ns, dict) and "__error__" not in ns else None


def _dims(rows):
    return (len(rows[0]), len(rows)) if _is_grid(rows) else None


def mark_tiles(data):
    """印のタイル名（pxtop の TOP_MARK_TILES の値・印の優先の順）。読めなければ []。"""
    mt = (_ok(data, "pxtop") or {}).get("TOP_MARK_TILES")
    if not isinstance(mt, dict):
        return []
    return [v for v in mt.values() if isinstance(v, str) and v]


def prop_tiles(data):
    """職業の小道具と机の小物のタイル名（pxtop の TOP_ARCH_TILES・TOP_DESK_TILES の値）→ 出どころの表の名前。
    表が無い（export していない・古い木）は空＝そのタイルは「置かれていない」で赤になる。読めない形は bad へ。"""
    out, bad = {}, []
    top = _ok(data, "pxtop") or {}
    for name in PROP_TABLE_EXPORTS:
        table = top.get(name)
        if table is None or table == UNDEF:
            continue
        if not isinstance(table, dict) or not all(isinstance(v, str) and v for v in table.values()):
            bad.append(f"ui/core/pxtop.js の {name} が {{職業: タイル名}} でない（{str(table)[:40]}）")
            continue
        for v in table.values():
            out.setdefault(v, name)
    return out, bad


def contract_tiles(data):
    """契約のタイル → ((幅, 高さ), 寸法の出どころ) の順序つき dict と、契約そのものを読めない問題のリスト。
    並び＝コンタクトシートの並び（印 → 足跡 → 扉 → 開口つきガラス）。寸法の正本: 印＝ui/core/pxtop.js・
    扉＝ui/core/pxroom.js の DOOR・残り＝TILE_CONTRACT（委譲の契約）。"""
    out, bad = {}, []
    top = _ok(data, "pxtop")
    if top is not None:
        mt = top.get("TOP_MARK_TILES")
        sizes = top.get(MARK_SIZE_EXPORT)
        if not (isinstance(mt, dict) and mt and all(isinstance(v, str) and v for v in mt.values())):
            bad.append(f"ui/core/pxtop.js の TOP_MARK_TILES が {{印: タイル名}} でない・export されていない（{str(mt)[:40]}）")
        elif not isinstance(sizes, dict) or not sizes:
            bad.append(f"ui/core/pxtop.js が印の寸法 {MARK_SIZE_EXPORT} を export していない・空（{str(sizes)[:40]}）"
                       "＝印のタイルの寸法の正本が無い")
        else:
            for name in mt.values():
                wh = sizes.get(name)
                if isinstance(wh, list) and len(wh) == 2 and all(_is_int(x) and x > 0 for x in wh):
                    out[name] = (tuple(wh), f"ui/core/pxtop.js の {MARK_SIZE_EXPORT}")
                else:
                    bad.append(f"ui/core/pxtop.js の {MARK_SIZE_EXPORT}.{name} が [幅, 高さ] の正の整数でない（{wh}）")
            extra = [n for n in sizes if n not in mt.values()]
            if extra:
                bad.append(f"ui/core/pxtop.js の {MARK_SIZE_EXPORT} に TOP_MARK_TILES に無いタイル {' '.join(extra)}")
    out["footstep"] = (TILE_CONTRACT["footstep"], "委譲の契約（tools/px_lint.py の TILE_CONTRACT）")
    room = _ok(data, "pxroom")
    if room is not None:
        door = room.get("DOOR")
        if isinstance(door, dict) and all(_is_int(door.get(k)) for k in "xywh") and door["w"] > 0 and door["h"] > 0:
            out["door"] = ((door["w"], door["h"]), "ui/core/pxroom.js の DOOR")
        else:
            bad.append(f"ui/core/pxroom.js の DOOR が {{x, y, w, h}} の整数でない・export されていない（{str(door)[:40]}）")
    out["glassWallDoor"] = (TILE_CONTRACT["glassWallDoor"], "委譲の契約（tools/px_lint.py の TILE_CONTRACT）")
    return out, bad


def _glass_opening_problems(room, pt, furn):
    """会議室のガラス（glassWallDoor）の**ドットの開口**が経路の正本 GLASS_OPENING と一致するか（開口のドットの門の
    正本＝ここ 1 本）。通れるドット＝PASSABLE（'.' と 'u'＝落ち影）。見る順:
      1. 開口の列が GLASS_OPENING の行から**タイルの下端まで**全部通れる（人が歩いて抜ける所に框のドットが無い）
      2. GLASS_OPENING の最下行＝框の最下行（体＝通れないドットの一番下の行。開口の行が框の行とずれていない）
      3. その左右 1 列は、開口が上へ続く行（立面の扉口）から GLASS_OPENING の最下行まで通れない（ドットの開口が
         広すぎない）。框より下（落ち影の行）は床＝左右の列も通れてよい"""
    op = room.get("GLASS_OPENING")
    if not (isinstance(op, list) and len(op) == 4 and all(_is_int(v) for v in op) and op[2] > 0 and op[3] > 0):
        return [f"ui/core/pxroom.js の GLASS_OPENING が [x, y, w, h] の正の整数でない・export されていない（{str(op)[:40]}）"]
    placed = [f for f in furn if isinstance(f, dict) and f.get("tile") == "glassWallDoor"]
    if len(placed) != 1:
        return [f"FURNITURE の glassWallDoor が {len(placed)} 枚（会議室の開口つきガラスは 1 枚＝"
                "GLASS_OPENING のドットと突き合わせる相手が決まらない）"]
    f, rows = placed[0], pt.get("glassWallDoor")
    if not _is_grid(rows) or len({len(r) for r in rows}) != 1 or not _is_int(f.get("x")) or not _is_int(f.get("y")):
        return []                                             # 形の赤は ④／⑥ の寸法が出す（二重に並べない）
    ox, oy, ow, oh = op
    c0, r0 = ox - f["x"], oy - f["y"]
    w, h = len(rows[0]), len(rows)
    where = f"GLASS_OPENING {op}＝タイル内 x {c0}..{c0 + ow - 1}・行 {r0}..{r0 + oh - 1}"
    if c0 < 1 or c0 + ow > w - 1 or r0 < 0 or r0 + oh > h:
        return [f"{where} が glassWallDoor（{w}×{h}・論理 ({f['x']},{f['y']})）の内に収まらない（開口の左右に框の列が要る）"]

    def opened(r):
        return all(c in PASSABLE for c in rows[r][c0:c0 + ow])

    shut = [r for r in range(r0, h) if not opened(r)]
    if shut:
        return [f"glassWallDoor のドットの開口が {where} と一致しない: 行 {_span(shut)} で開口の列が通れない"
                f"（例 行 {shut[0]}「{rows[shut[0]][c0:c0 + ow]}」・通れるのは '.' と 'u'）＝ドットの開口がずれている・"
                "塞がっている（経路は GLASS_OPENING を通る＝人が框の絵を突き抜けて歩く）"]
    solid = [r for r, row in enumerate(rows) if any(c not in PASSABLE for c in row)]
    bottom = r0 + oh - 1
    if not solid or solid[-1] != bottom:
        return [f"{where} の最下行 {bottom} が glassWallDoor の框の最下行（通れないドットの一番下の行 "
                f"{solid[-1] if solid else '無し'}）と違う＝経路の開口の行がドットの框の行とずれている"]
    top = r0
    while top > 0 and opened(top - 1):
        top -= 1
    wide = [r for r in range(top, bottom + 1) if rows[r][c0 - 1] in PASSABLE or rows[r][c0 + ow] in PASSABLE]
    if wide:
        return [f"glassWallDoor のドットの開口の左右 1 列（タイル内 x {c0 - 1}・{c0 + ow}）が行 {_span(wide)} で通れる"
                f"（'.' か 'u'）＝ドットの開口が GLASS_OPENING（{ow} 幅）より広い（経路の開口と絵の開口がずれる）"]
    return []


def _span(rs):
    """[3,4,5,9] → '3..5・9'（行番号の並びを短く書く）。"""
    out, i = [], 0
    while i < len(rs):
        j = i
        while j + 1 < len(rs) and rs[j + 1] == rs[j] + 1:
            j += 1
        out.append(str(rs[i]) if i == j else f"{rs[i]}..{rs[j]}")
        i = j + 1
    return "・".join(out)


def cockpit_problems(data):
    """load_cockpit の値 → 問題の文字列のリスト（空なら緑）。IO を持たない純関数（回帰テストが直接突く）。
    各行の先頭の丸数字は docstring の ①〜⑦。"""
    bad = []
    add = lambda num, msg: bad.append(f"{num} {msg}")  # noqa: E731
    if data.get("node_error"):
        add("—", f"node で正本を評価できない: {data['node_error']}")
        return bad
    mods = {key: _module(data, key, label, num, bad) for key, label, num in REQUIRED_MODULES}
    cells, tiles, pal, room = mods["cells"], mods["tiles"], mods["palette"], mods["pxroom"]
    if data.get("pxtop") is None:
        add("②", "pxtop 未作成（ui/core/pxtop.js が無い＝TOP_CELLS・TOP_VENDORS＝俯瞰のコマ表の正本が無いので、"
                 "何を揃えるべきか決まらない）")
        top = None
    else:
        top = _module(data, "pxtop", "ui/core/pxtop.js", "②", bad)

    # ① 寸法の三点一致 ─────────────────────────────────────────────────
    dims = {}
    if cells is not None:
        dims["ui/cockpit/cells.js の PX_CELL_W×PX_CELL_H"] = (cells.get("PX_CELL_W"), cells.get("PX_CELL_H"))
    if room is not None:
        robot = room.get("ROOM", {}).get("robot") if isinstance(room.get("ROOM"), dict) else None
        dims["ui/core/pxroom.js の ROOM.robot"] = ((robot.get("w"), robot.get("h"))
                                                  if isinstance(robot, dict) else (UNDEF, UNDEF))
    gen = data.get("gen") or {}
    if "error" in gen:
        add("①", gen["error"])
    elif "skip" not in gen:
        dims["design/gen_cells.py の W, H"] = (gen.get("W"), gen.get("H"))
    good = {}
    for label, (w, h) in dims.items():
        if _is_int(w) and _is_int(h) and w > 0 and h > 0:
            good[label] = (w, h)
        else:
            add("①", f"{label} が正の整数でない・export されていない（{w}×{h}）")
    if len(set(good.values())) > 1:
        add("①", "ロボの寸法が一致しない: " + "／".join(f"{k}={w}×{h}" for k, (w, h) in good.items()))
    cw, ch = next(iter(good.values())) if len(set(good.values())) == 1 else (None, None)

    # ② コマ表の完全性・寸法・凡例 ─────────────────────────────────────────
    names = vendors = None
    if top is not None:
        for label in ("TOP_CELLS", "TOP_VENDORS"):
            v = top.get(label)
            if not _str_list(v):
                add("②", f"ui/core/pxtop.js の {label} が文字列の配列でない・export されていない（{str(v)[:40]}）")
            elif len(set(v)) != len(v):
                add("②", f"ui/core/pxtop.js の {label} に同じ名前が 2 回 "
                         f"{sorted({x for x in v if v.count(x) > 1})}")
            elif label == "TOP_CELLS":
                names = v
            else:
                vendors = v
    cell_ok = ({".", "a", "c"} | _hex_keys(pal.get("PALETTE"))) if pal is not None else None
    if pal is not None and not _hex_keys(pal.get("PALETTE")):
        add("②", "ui/cockpit/palette.js の PALETTE が無い・色が 1 つも無い")
    px = cells.get("PX_CELLS") if cells is not None else None
    nested = False
    if cells is not None:
        if not isinstance(px, dict) or not px:
            add("②", f"PX_CELLS が無い・空（{str(px)[:40]}）")
        else:
            flat = [k for k, v in px.items() if not isinstance(v, dict)]
            if flat:
                add("②", f"PX_CELLS が平らな形（{' '.join(flat[:5])}{' …' if len(flat) > 5 else ''}）＝"
                         "PX_CELLS[vendor][cell] の入れ子でない（1 ベンダーぶんの平らな表を黙って受け入れない）")
            else:
                nested = True
        pv = cells.get("PX_VENDORS")
        if vendors is not None:
            if pv == UNDEF:
                add("②", "ui/cockpit/cells.js が PX_VENDORS を export していない（契約）")
            elif pv != vendors:
                add("②", f"PX_VENDORS {pv} が TOP_VENDORS {vendors} と違う")
    if nested and names and vendors:
        for v in vendors:
            got = px.get(v)
            if got is None:
                add("②", f"{v}: PX_CELLS.{v} が無い（{len(names)} コマ全部が未）")
                continue
            missing = [n for n in names if n not in got]
            extra = [n for n in got if n not in names]
            if missing:
                add("②", f"{v}: コマが足りない {len(missing)}/{len(names)}（{' '.join(missing)}）")
            if extra:
                add("②", f"{v}: TOP_CELLS に無いコマ {' '.join(extra)}（別名・旧名を置かない＝正本 ui/core/pxtop.js）")
            if not missing and not extra and list(got) != names:
                add("②", f"{v}: コマの並びが TOP_CELLS と違う（契約の ORDER＝TOP_CELLS の順）")
        others = [v for v in px if v not in vendors]
        if others:
            add("②", f"PX_CELLS に TOP_VENDORS に無いベンダー {' '.join(others)}")
    if nested:
        for v, frames in px.items():
            for n, rows in frames.items():
                where = f"{v}.{n}"
                if isinstance(rows, str):
                    add("②", f"{where}: 別のコマの名前「{rows}」を指している＝別名（絵そのものを置く）")
                    continue
                if not _is_grid(rows):
                    add("②", f"{where}: 文字列の配列でない（{str(rows)[:40]}）")
                    continue
                if cw and (len(rows) != ch or any(len(r) != cw for r in rows)):
                    add("②", f"{where}: {cw}×{ch} でない（{len(rows)} 行・幅 {sorted({len(r) for r in rows})}）")
                if cell_ok is not None:
                    unknown = sorted({c for r in rows for c in r} - cell_ok)
                    if unknown:
                        add("②", f"{where}: 凡例に無い文字 {''.join(unknown)}（使えるのは {''.join(sorted(cell_ok))}・"
                                 "無い文字は room.js で黙って透明になる）")

    # ③ 動いて見えるか・ベンダーを見分けられるか ─────────────────────────────
    if nested:
        walk_names = names or sorted({n for f in px.values() for n in f})
        groups = {}
        for n in walk_names:
            m = WALK_CELL.match(n)
            if m:
                groups.setdefault(m.group(1), []).append((int(m.group(2)), n))
        for v in (vendors or list(px)):
            frames = px.get(v) if isinstance(px.get(v), dict) else {}
            for d, seq in sorted(groups.items()):
                seq = [n for _, n in sorted(seq)]
                pairs = list(zip(seq, seq[1:])) + ([(seq[-1], seq[0])] if len(seq) > 2 else [])
                for a, b in pairs:
                    ga, gb = frames.get(a), frames.get(b)
                    if _is_grid(ga) and _is_grid(gb):
                        diff = diff_dots(ga, gb)
                        if diff < MIN_WALK_DIFF:
                            add("③", f"{v}: {a}→{b} の差が {diff} ドット（{MIN_WALK_DIFF} 以上＝止まって見える）")
        if names is not None and "idle0" not in names:
            add("③", "TOP_CELLS に idle0 が無い（ベンダーの見分けを測る基準のコマ）")
        else:
            vs = vendors or list(px)
            for i, va in enumerate(vs):
                for vb in vs[i + 1:]:
                    ga = (px.get(va) or {}).get("idle0")
                    gb = (px.get(vb) or {}).get("idle0")
                    if _is_grid(ga) and _is_grid(gb):
                        diff = diff_dots(ga, gb)
                        if diff < MIN_VENDOR_DIFF:
                            add("③", f"{va} と {vb} の idle0 の差が {diff} ドット（{MIN_VENDOR_DIFF} 以上＝"
                                     "個体色はプロジェクトの色なので、ベンダーはシルエットで見分ける）")

    # ④ タイルの外形と凡例 ─────────────────────────────────────────────
    pt = tiles.get("PX_TILES") if tiles is not None else None
    tile_ok = ({"."} | _hex_keys(pal.get("TILE_PALETTE"))) if pal is not None else None
    if pal is not None and not _hex_keys(pal.get("TILE_PALETTE")):
        add("④", "ui/cockpit/palette.js の TILE_PALETTE が無い・色が 1 つも無い")
    if tiles is not None and (not isinstance(pt, dict) or not pt):
        add("④", f"PX_TILES が無い・空（{str(pt)[:40]}）")
        pt = None
    if pt is not None:
        for name, rows in pt.items():
            if not _is_grid(rows):
                add("④", f"タイル {name}: 文字列の配列でない（{str(rows)[:40]}）")
                continue
            widths = sorted({len(r) for r in rows})
            if len(widths) != 1 or widths[0] == 0:
                add("④", f"タイル {name}: 行の長さが揃っていない（{widths}）")
                continue
            edges = [label for label, empty in (
                ("上の行", set(rows[0]) == {"."}), ("下の行", set(rows[-1]) == {"."}),
                ("左の列", all(r[0] == "." for r in rows)), ("右の列", all(r[-1] == "." for r in rows)))
                if empty]
            if edges:
                add("④", f"タイル {name}: 外周に全透明の{'・'.join(edges)}がある（寸法が絵の外形より大きい＝"
                         "置いた座標と見える位置がずれる。余白は配置の座標で取る）")
            if tile_ok is not None:
                unknown = sorted({c for r in rows for c in r} - tile_ok)
                if unknown:
                    add("④", f"タイル {name}: TILE_PALETTE に無い文字 {''.join(unknown)}（room.js の塗り分けは"
                             "同じ文字を塗り直すだけ＝新しい文字は足せない・無い文字は黙って透明になる）")

    # ⑤ 配置表とタイルの突き合わせ ───────────────────────────────────────
    furn = room.get("FURNITURE") if room is not None else None
    if room is not None and (not isinstance(furn, list) or not furn):
        add("⑤", f"ui/core/pxroom.js の FURNITURE が無い・空（{str(furn)[:40]}）")
        furn = None
    marks = mark_tiles(data)
    props, pbad = prop_tiles(data)
    for msg in pbad:
        add("⑤", msg)
    drawn = data.get("drawn") if isinstance(data.get("drawn"), dict) else {}
    if furn is not None and pt is not None:
        used = set()
        for f in furn:
            for key in ("tile", "lit"):
                t = f.get(key) if isinstance(f, dict) else None
                if t is None:
                    continue
                used.add(t)
                if t not in pt:
                    add("⑤", f"FURNITURE {f.get('id')} の {key}「{t}」が PX_TILES に無い（描くと何も出ない）")
        # 描画のコードが配置表を通らずに直接描くタイル（床の目地・足跡）。読むモジュールが無ければ名指しで赤
        if not data.get("draw_sources"):
            add("⑤", "ui/cockpit に PX_TILES を読む描画のモジュールが無い＝描画のコードが直接描くタイル（床の目地・足跡）を"
                     "読めない（tools/px_lint.py の drawn_tiles）")
        for t, where in drawn.items():
            if t not in pt:
                add("⑤", f"描画のコード（{'・'.join(where[:3])}）が描くタイル「{t}」が PX_TILES に無い（描くと何も出ない）")
        # 職業の小道具・机の小物の表（pxtop）が指すタイルが無い＝描くと何も出ない
        for t, src in props.items():
            if t not in pt:
                add("⑤", f"ui/core/pxtop.js の {src} が指すタイル「{t}」が PX_TILES に無い（描くと何も出ない）")
        # 置かなくてよいのは pxtop の TOP_MARK_TILES の値（mark で始まる名前でも、表に無い印は誰も描かない）と、
        # 描画のコードが表を引いて描く小道具・机の小物（TOP_ARCH_TILES・TOP_DESK_TILES の値）だけ
        unused = [n for n in pt if n not in used and n not in drawn and n not in marks and n not in props
                  and n not in UNUSED_TILE_ALLOW]
        if unused:
            add("⑤", f"どこにも置かれていないタイル {' '.join(unused)}（FURNITURE に置く／頭上の印なら ui/core/pxtop.js の "
                     "TOP_MARK_TILES へ／描画のコードが直接描くなら ui/cockpit の tileSprite(\"名前\")・T(\"名前\") の字面で"
                     "呼ぶ／置かない理由があれば tools/px_lint.py の UNUSED_TILE_ALLOW へ理由つきで）")
        for n in UNUSED_TILE_ALLOW:
            if n not in pt:
                add("⑤", f"許可リストの {n} はもう PX_TILES に無い（UNUSED_TILE_ALLOW から消す）")
            elif n in used:
                add("⑤", f"許可リストの {n} は FURNITURE に置かれた（UNUSED_TILE_ALLOW から消す）")
            elif n in drawn:
                add("⑤", f"許可リストの {n} は描画のコード（{'・'.join(drawn[n][:3])}）が描いている"
                         "（UNUSED_TILE_ALLOW から消す）")
            elif n in marks:
                add("⑤", f"許可リストの {n} は TOP_MARK_TILES の印になった（UNUSED_TILE_ALLOW から消す）")
            elif n in props:
                add("⑤", f"許可リストの {n} は {props[n]} の小道具になった（UNUSED_TILE_ALLOW から消す）")

    # ⑥ 契約（寸法・扉の位置・会議室の開口）──────────────────────────────────
    contract, cbad = contract_tiles(data)
    for msg in cbad:
        add("⑥", msg)
    n_contract = 0                   # 寸法を実際に突き合わせた契約のタイルの数（最後の砦が数える）
    if pt is not None:
        for name, ((w, h), src) in contract.items():
            rows = pt.get(name)
            if rows is None:
                add("⑥", f"契約のタイル {name}（{w}×{h}・寸法の正本は {src}）が PX_TILES に無い")
            elif _is_grid(rows):
                n_contract += 1
                if _dims(rows) != (w, h):
                    add("⑥", f"タイル {name} が {len(rows[0])}×{len(rows)}（契約は {w}×{h}・正本は {src}）")
    door = room.get("DOOR") if room is not None else None
    if furn is not None and isinstance(door, dict) and _is_int(door.get("x")) and _is_int(door.get("y")):
        placed = [f for f in furn if isinstance(f, dict) and f.get("tile") == "door"]
        if len(placed) != 1:
            add("⑥", f"FURNITURE の扉（tile door）が {len(placed)} 枚（1 枚＝DOOR の位置。人は DOOR から出入りする）")
        elif (placed[0].get("x"), placed[0].get("y")) != (door["x"], door["y"]):
            add("⑥", f"FURNITURE の扉 {placed[0].get('id')} が ({placed[0].get('x')},{placed[0].get('y')})"
                     f"・DOOR は ({door['x']},{door['y']})＝人が壁の絵の扉の外から出入りする")
    if furn is not None and pt is not None and room is not None:
        for msg in _glass_opening_problems(room, pt, furn):
            add("⑥", msg)

    # ⑦ 画像ファイル ─────────────────────────────────────────────────
    imgs = data.get("images") or []
    if imgs:
        add("⑦", f"ui/cockpit に画像ファイル {len(imgs)} 個（ドットは文字グリッドだけ）: {', '.join(imgs[:5])}")

    # 最後の砦: 赤が 1 件も無いのに、検査した物が 0 件（どこかの節が黙って飛んだ）なら赤。
    # 契約のタイルは「PX_TILES に在って寸法を突き合わせた数」（契約の表の長さは TILE_CONTRACT の分だけ必ず 2 以上＝数えない）
    if not bad:
        n_cells = sum(len(f) for f in px.values() if isinstance(f, dict)) if nested else 0
        empty = [label for label, n in (("コマ", n_cells), ("ベンダー", len(vendors or ())),
                                        ("タイル", len(pt or ())), ("家具", len(furn or ())),
                                        ("印", len(marks)), ("契約のタイル", n_contract)) if not n]
        if empty:
            add("—", f"検査した{'・'.join(empty)}が 0 件（どこかの節が黙って飛んだ＝緑にしない）")
    return bad


def cockpit_summary(data):
    """緑のときの 1 行（何を何件見たか＝0 件を見て ✓ にしていないことを行で示す）。"""
    px = (data.get("cells") or {}).get("PX_CELLS") or {}
    n_cells = sum(len(f) for f in px.values() if isinstance(f, dict))
    pt = (data.get("tiles") or {}).get("PX_TILES") or {}
    marks = sum(1 for n in mark_tiles(data) if n in pt)
    props = sum(1 for n in prop_tiles(data)[0] if n in pt)
    contract, _ = contract_tiles(data)
    cells = data.get("cells") or {}
    gen = data.get("gen") or {}
    gen_note = f"・{gen['skip']}" if "skip" in gen else ""
    op = (data.get("pxroom") or {}).get("GLASS_OPENING")
    drawn = sorted(data.get("drawn") or {})
    return (f"{len(px)} ベンダー × {n_cells // max(1, len(px))} コマ = {n_cells} セル"
            f"（{cells.get('PX_CELL_W')}×{cells.get('PX_CELL_H')}・寸法の一致{gen_note}）／タイル {len(pt)} 種"
            f"（契約 {len(contract)} 種・印 {marks}・小道具 {props}・描画のコードが描く {len(drawn)}（{'・'.join(drawn)}）・"
            f"許可 {len(UNUSED_TILE_ALLOW)}・会議室の開口 {op} とドットが一致）／画像 0 個")


def cockpit_main(argv):
    root = ROOT
    if "--root" in argv:
        i = argv.index("--root")
        if i + 1 >= len(argv):
            print("✗ --root に木のパスが要る")
            return 2
        root = Path(argv[i + 1]).resolve()
    data = load_cockpit(root)
    bad = vendor_table_problems(root) + cockpit_problems(data)
    if "--list" in argv:
        px = (data.get("cells") or {}).get("PX_CELLS")
        if isinstance(px, dict):
            for v, f in px.items():
                print(f"  {v}: {len(f) if isinstance(f, (dict, list)) else '?'} コマ"
                      f"（{' '.join(f) if isinstance(f, dict) else '平ら'}）")
        pt = (data.get("tiles") or {}).get("PX_TILES")
        if isinstance(pt, dict):
            print(f"  タイル {len(pt)} 種: {' '.join(pt)}")
    if bad:
        print(f"✗ ドット絵 lint（cockpit）: {len(bad)} 件")
        for line in bad[:30]:
            print("   " + line)
        if len(bad) > 30:
            print(f"   …ほか {len(bad) - 30} 件")
        return 1
    print(f"✓ ドット絵 lint（cockpit）: {cockpit_summary(data)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
