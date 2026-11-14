#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""R99 Phase 3: 俯瞰の部屋（`?ui=cockpit`）のドットのコンタクトシート＝本人が裁定する 1 枚。

冒頭の見出しの帯に、ベンダー 3 種の idle0 を**同じ個体色で ×8**に並べる（個体色はプロジェクトの色＝ベンダーは
シルエットで見分ける、を一目で裁定するため。ペアごとの差のドット数も添える）。
その下のベンダーごとの節に、コマ（ui/core/pxtop.js の TOP_CELLS の順）を列・個体色 3 色（palette.js の
SESSION_COLORS）を行にして、部屋の床の色の上に ×4 で並べる。歩行の E の右には、描画側が作る W
（E の左右反転）も「鏡」として並べる（描かないコマも、見える形で裁定する）。下の節にタイル全部
（契約の印・足跡・扉・開口つきガラスを先頭に）。

**無いものは黙って飛ばさない**: 無いコマ・無い契約のタイルは赤い × の枠と「未」で描く。寸法が契約や
PX_CELL_W/H と違うものは名前を赤で書く。これは道具の画像（canvas ではない）なので文字を書いてよい。
**評価エラーを「未」に化けさせない**: cells・tiles・pxroom・pxtop のどれかが評価できないときは、その文言を画像の
上部と標準出力に注記し、シートは描いたうえで終了コード 1（✓ を付けない）。

色は本番（ui/cockpit/room.js）と同じ規則:
  ロボ  a＝個体色・c＝その暗いほう（palette.js の dimTint を node で呼んで得る＝計算を写さない）・他は PALETTE
  家具  TILE_PALETTE。配置表（pxroom の FURNITURE）の paint が付いた物は room.js の差し替え（窓・ガラス・紙）を
        design/check.py と同じ読み方で当てる。配置表に無いタイルは、名前がどちらかの前方一致になる配置表の
        タイルの塗り（配置表の glassWallDoor の glass を、置かれていない原本の glassWall にも当てる）
  床    room.js の ROOM.floor（RAMP の段）

値は tools/px_lint.py の load_cockpit（node で正本を評価）から読む＝門とシートが同じ物を見る。

使い方（Pillow が要る＝リポジトリの .venv）:
    .venv/bin/python tools/px_sheet.py                    # dist/px_sheet_cockpit.png
    .venv/bin/python tools/px_sheet.py --out PATH --scale 4 --tints 0,2,3
    .venv/bin/python tools/px_sheet.py --root DIR         # 別の木（回帰テストが合成の木で使う）
終了コード: 0＝描けた／1＝node か palette を評価できない（描かない）・cells／tiles／pxroom／pxtop を評価できない
（描いたうえで注記）／2＝Pillow が無い・--tints が不正。
"""
import argparse
import glob
import importlib.util
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import px_lint  # noqa: E402  （同じ tools/ の門。正本の読み方を 1 本にする）

ROOT = px_lint.ROOT
OUT = ROOT / "dist" / "px_sheet_cockpit.png"
MISSING = "#e5484d"          # 無い・寸法違い（道具の画像の中だけの色）
FONT_CANDIDATES = (
    "/System/Library/Fonts/*角ゴシック W3.ttc", "/System/Library/Fonts/*W3.ttc",
    "/System/Library/Fonts/Hiragino Sans GB.ttc", "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
    "/Library/Fonts/Arial Unicode.ttf", "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
)


def rgb(hexval):
    return tuple(int(hexval[i:i + 2], 16) for i in (1, 3, 5))


def load_font(size):
    """日本語の書ける字体（無ければ Pillow の既定＝ASCII だけで書く）。(字体, 日本語が書けるか)。"""
    from PIL import ImageFont
    for pat in FONT_CANDIDATES:
        for path in sorted(glob.glob(pat)):
            try:
                return ImageFont.truetype(path, size), True
            except OSError:
                continue
    try:
        return ImageFont.load_default(size=size), False
    except TypeError:
        return ImageFont.load_default(), False


def floor_color(root, ramp):
    """room.js の ROOM.floor（`floor: RAMP.stone[2]` の形）。読めなければ stone[2]（今の値）を使うと明記する。"""
    src = (root / "ui" / "cockpit" / "room.js")
    m = re.search(r"\bfloor:\s*RAMP\.(\w+)\[(\d+)\]", src.read_text(encoding="utf-8")) if src.is_file() else None
    if m and m.group(1) in ramp and int(m.group(2)) < len(ramp[m.group(1)]):
        return ramp[m.group(1)][int(m.group(2))], None
    return ramp["stone"][2], "room.js の ROOM.floor を読めない＝床は RAMP.stone[2] で描いた"


def tile_paints(root, pal):
    """配置表の paint → 差し替えの色 {paint: {文字: 色}}（null は {}）。room.js の PAINTS を
    design/check.py の override_problems／paints_of で読む（読み方を 2 つ持たない）。(表, 注記)。"""
    chk, room = root / "design" / "check.py", root / "ui" / "cockpit" / "room.js"
    if not chk.is_file() or not room.is_file():
        return {None: {}}, "design/check.py か ui/cockpit/room.js が無い＝家具は TILE_PALETTE のまま描いた"
    keep = sys.dont_write_bytecode
    sys.dont_write_bytecode = True
    try:
        spec = importlib.util.spec_from_file_location("design_check_for_px_sheet", chk)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        src = room.read_text(encoding="utf-8")
        _, blocks, _ = mod.override_problems("ui/cockpit/room.js", src, pal["TILE_PALETTE"], pal["RAMP"])
        paints, err = mod.paints_of(src, blocks, pal["RAMP"])
    except Exception as e:  # noqa: BLE001 — 読めなくてもシートは描く（注記で知らせる）
        return {None: {}}, f"room.js の塗り分けを読めない（{type(e).__name__}）＝家具は TILE_PALETTE のまま描いた"
    finally:
        sys.dont_write_bytecode = keep
    if err:
        return {None: {}}, f"{err}＝家具は TILE_PALETTE のまま描いた"
    return paints, None


def paint_of(name, furniture):
    """タイル → 配置表の paint（最初に出てくる行）。表に無いタイルは、名前が**どちら向きでも**前方一致する
    表のタイルの塗り（一致の長いほう＝短いほうの名前の長さが長いものを先に）。置かれていない原本の glassWall には
    配置表の glassWallDoor の glass を、逆に原本だけが置かれている木なら glassWallDoor に glassWall の塗りを当てる
    （同じ素材が道具の都合で別の色に見えないように）。どれも無ければ None（TILE_PALETTE のまま）。"""
    for f in furniture:
        if name in (f.get("tile"), f.get("lit")):
            return f.get("paint")
    best = None
    for f in furniture:
        t = f.get("tile") or ""
        if t and (name.startswith(t) or t.startswith(name)):
            common = min(len(t), len(name))
            if best is None or common > best[0]:
                best = (common, f.get("paint"))
    return best[1] if best else None


def unplaced(name, furniture, marks, drawn):
    """シートに「未配置」と書くタイルか＝配置表（tile / lit）に無く、印（TOP_MARK_TILES）でも、描画のコードが直接描く
    タイル（床の目地・足跡＝px_lint の drawn_tiles）でも、許可リストでもない（px_lint ⑤ の「置かれていない」と同じ判定）。"""
    placed = {f.get("tile") for f in furniture} | {f.get("lit") for f in furniture}
    return (name not in placed and name not in marks and name not in drawn
            and name not in px_lint.UNUSED_TILE_ALLOW)


EVAL_MODULES = (("cells", "ui/cockpit/cells.js"), ("tiles", "ui/cockpit/tiles.js"),
                ("pxroom", "ui/core/pxroom.js"), ("pxtop", "ui/core/pxtop.js"))


def eval_errors(data):
    """評価できなかったモジュールの注記（シートの「未」が評価エラーのせいか、描かれていないせいかを分ける）。
    pxtop はファイルが無い（None）なら「未作成」の注記が別に出る＝ここでは評価エラーだけ。"""
    out = []
    for key, rel in EVAL_MODULES:
        ns = data.get(key)
        if isinstance(ns, dict) and "__error__" in ns:
            out.append(f"{rel} を評価できない: {ns['__error__']}（このシートの「未」はこのエラーのせいで、"
                       "絵が無いとは限らない）")
        elif ns is None and key != "pxtop":
            out.append(f"{rel} の値が無い（評価の結果に {key} が無い）")
    return out


def plan(data):
    """描く節の表と、タイル（契約を先頭に）と注記。節 = {title, names（期待の並び）, frames（名前 → 行 | None）,
    extras（在るのに期待に無い名前）, vendor（ベンダーの節か）}。**無いものは None として残す**（飛ばさない）。"""
    notes = []
    cells = data.get("cells") or {}
    px = cells.get("PX_CELLS") if isinstance(cells.get("PX_CELLS"), dict) else {}
    top = data.get("pxtop") if isinstance(data.get("pxtop"), dict) else None
    names = top.get("TOP_CELLS") if top and px_lint._str_list(top.get("TOP_CELLS")) else None
    vendors = top.get("TOP_VENDORS") if top and px_lint._str_list(top.get("TOP_VENDORS")) else None
    flat = bool(px) and any(not isinstance(v, dict) for v in px.values())
    if top is None:
        notes.append("pxtop 未作成（ui/core/pxtop.js が無い）＝コマの並びは生成物の順・足りないコマは判定できない")
    if vendors is None:
        pv = cells.get("PX_VENDORS")
        vendors = pv if px_lint._str_list(pv) else ([] if flat else list(px))
    if names is None:
        seen = []
        for f in (px.values() if not flat else [px]):
            seen += [n for n in (f if isinstance(f, dict) else {}) if n not in seen]
        names = seen
    sections = []
    if flat:
        notes.append("PX_CELLS が平らな形（ベンダーの入れ子でない）＝そのまま 1 節に描き、ベンダーの節は全部「未」にした")
        flat_names = list(px)
        sections.append({"title": "PX_CELLS（平ら・ベンダー不明）", "vendor": False,
                         "names": flat_names, "frames": dict(px), "extras": []})
        if not vendors:
            notes.append("ベンダーの表が無い（pxtop も PX_VENDORS も無い）＝ベンダーごとの節は描けない")
    for v in vendors:
        got = px.get(v) if not flat and isinstance(px.get(v), dict) else {}
        sections.append({"title": v, "vendor": True, "names": list(names),
                         "frames": {n: got.get(n) for n in names},
                         "extras": [n for n in got if n not in names]})
        sections[-1]["frames"].update({n: got[n] for n in sections[-1]["extras"]})
    tiles = (data.get("tiles") or {}).get("PX_TILES")
    tiles = tiles if isinstance(tiles, dict) else {}
    contract, cbad = px_lint.contract_tiles(data)
    notes += [f"契約を読めない: {m}" for m in cbad]
    order = list(contract) + [n for n in tiles if n not in contract]
    return sections, tiles, order, notes


def main(argv=None):
    ap = argparse.ArgumentParser(description="俯瞰の部屋のドットのコンタクトシート（本人の裁定用）")
    ap.add_argument("--out", default=None, help=f"出力先（既定 {OUT.relative_to(ROOT)}）")
    ap.add_argument("--scale", type=int, default=4)
    ap.add_argument("--tints", default="0,2,3", help="SESSION_COLORS の添字 3 つ（行）")
    ap.add_argument("--root", default=None, help="見る木（既定はこのリポジトリ）")
    args = ap.parse_args(argv)
    try:
        from PIL import Image, ImageDraw
    except ImportError:
        print("✗ Pillow が無い。リポジトリの .venv で走らせる: .venv/bin/python tools/px_sheet.py")
        return 2
    root = Path(args.root).resolve() if args.root else ROOT
    out = Path(args.out) if args.out else (root / "dist" / "px_sheet_cockpit.png")
    S = max(1, args.scale)

    data = px_lint.load_cockpit(root)
    if data.get("node_error"):
        print(f"✗ node で正本を評価できない: {data['node_error']}")
        return 1
    pal = data.get("palette") or {}
    if "__error__" in pal or not isinstance(pal.get("PALETTE"), dict):
        print(f"✗ ui/cockpit/palette.js を評価できない: {pal.get('__error__', 'PALETTE が無い')}")
        return 1
    colors, dims = pal.get("SESSION_COLORS") or [], pal.get("__dims__") or []
    try:
        picks = [int(x) for x in args.tints.split(",") if x.strip()]
        tints = [(colors[i], dims[i]) for i in picks]
    except (ValueError, IndexError):
        print(f"✗ --tints {args.tints} が SESSION_COLORS（{len(colors)} 色）の添字でない")
        return 2
    ramp, P, TP = pal["RAMP"], pal["PALETTE"], pal["TILE_PALETTE"]
    floor, fnote = floor_color(root, ramp)
    furniture = (data.get("pxroom") or {}).get("FURNITURE")
    furniture = [f for f in furniture if isinstance(f, dict)] if isinstance(furniture, list) else []
    paints, pnote = tile_paints(root, pal)
    sections, tiles, torder, notes = plan(data)
    errors = eval_errors(data)
    notes = errors + notes + [n for n in (fnote, pnote) if n]
    contract = {n: wh for n, (wh, _src) in px_lint.contract_tiles(data)[0].items()}
    marks = set(px_lint.mark_tiles(data)) | set(px_lint.prop_tiles(data)[0])   # R110: 小道具の表も「置かれている」
    cw, ch = (data.get("cells") or {}).get("PX_CELL_W"), (data.get("cells") or {}).get("PX_CELL_H")
    cw = cw if px_lint._is_int(cw) else 12
    ch = ch if px_lint._is_int(ch) else 16

    ink, text, faint, accent, miss = (rgb(ramp["ink"][0]), rgb(ramp["shell"][3]), rgb(ramp["shell"][1]),
                                      rgb(ramp["amber"][2]), rgb(MISSING))

    font, ja = load_font(12)
    hfont, _ = load_font(17)
    T = (lambda j, e: j) if ja else (lambda j, e: e)
    drawn = set(data.get("drawn") or {})

    def tile_label(name, rows, w, h):
        want = contract.get(name)
        bad_size = want is not None and px_lint._is_grid(rows) and (w, h) != want
        label = f"{name} {w}×{h}" if not bad_size else f"{name} {w}×{h}≠{want[0]}×{want[1]}"
        if px_lint._is_grid(rows) and unplaced(name, furniture, marks, drawn):
            label += T("（未配置）", " (unplaced)")
        return label, bad_size

    # 列 = 期待のコマ ＋ 鏡の列（歩行 E → W・描画側が左右反転で作る）＋ 余りのコマ（期待に無いのに在る）
    def columns(sec):
        cols = [(n, n, "") for n in sec["names"]]
        mir = [(n, n.replace("walkE", "walkW", 1), "mirror") for n in sec["names"]
               if px_lint.WALK_CELL.match(n) and px_lint.WALK_CELL.match(n).group(1) == "E"]
        return cols, mir + [(n, n, "extra") for n in sec["extras"]]

    TAG = {"mirror": T(" 鏡", " mir"), "extra": T(" 余", " extra"), "": ""}
    M = 12                                        # 余白
    LEFT = 118                                    # 行見出し（個体色）の幅
    gap = 26                                      # 鏡・余りの列の前の隙間
    heads = [dst + TAG[kind] for sec in sections for part in columns(sec) for _, dst, kind in part]
    pitch = int(max([cw * S] + [font.getlength(h) for h in heads])) + 14   # 列の間隔（名前が収まる幅）
    row_h = ch * S + 2 * S + 10
    sec_h = 34 + 20 + len(tints) * row_h + 16
    width = max([900] + [M + LEFT + len(c) * pitch + (gap + len(r) * pitch if r else 0) + M
                         for c, r in map(columns, sections)])

    # 見出しの帯: ベンダー 3 種の idle0 を同じ個体色（tints の先頭）で ×BIG（本人がベンダーの見分けを一目で裁定する）
    BIG = 8
    vend_secs = [sec for sec in sections if sec["vendor"]]
    band_pitch = cw * BIG + 2 * BIG + 40
    pairs = [(a, b) for i, a in enumerate(vend_secs) for b in vend_secs[i + 1:]]
    pair_w = int(max([0] + [font.getlength(f"{a['title']} ↔ {b['title']}  差 000 ドット（{px_lint.MIN_VENDOR_DIFF} 以上）")
                            for a, b in pairs])) + 24
    band_h = 34 + (ch * BIG + 2 * BIG + 26 if vend_secs else 20) + 20
    width = max(width, M + len(vend_secs) * band_pitch + pair_w + M)

    # タイルの流し込み（幅は上の節に合わせて折り返す）
    tile_boxes, x, y, line_h = [], M, 0, 0
    for name in torder:
        rows = tiles.get(name)
        w, h = ((len(rows[0]), len(rows)) if px_lint._is_grid(rows) else contract.get(name, (8, 8)))
        bw, bh = int(max(w * S + 2 * S, font.getlength(tile_label(name, rows, w, h)[0]))), h * S + 2 * S + 18
        if x + bw > width - M and x > M:
            x, y, line_h = M, y + line_h + 12, 0
        tile_boxes.append((name, rows, w, h, x, y))
        x, line_h = x + bw + 12, max(line_h, bh)
    tiles_h = y + line_h
    note_h = 18 * len(notes)
    height = M + note_h + band_h + len(sections) * sec_h + 40 + tiles_h + M

    img = Image.new("RGB", (width, height), ink)
    d = ImageDraw.Draw(img)

    def floor_box(x0, y0, w, h, s=S):
        d.rectangle([x0 - s, y0 - s, x0 + w * s + s - 1, y0 + h * s + s - 1], fill=rgb(floor))

    def missing_box(x0, y0, w, h, s=S):
        x1, y1 = x0 + w * s - 1, y0 + h * s - 1
        d.rectangle([x0, y0, x1, y1], outline=miss, width=2)
        d.line([x0, y0, x1, y1], fill=miss, width=2)
        d.line([x0, y1, x1, y0], fill=miss, width=2)
        label = T("未", "MISSING")
        tw = d.textlength(label, font=font)
        d.rectangle([x0 + (w * s - tw) / 2 - 2, y0 + h * s / 2 - 8, x0 + (w * s + tw) / 2 + 2, y0 + h * s / 2 + 8],
                    fill=ink)
        d.text((x0 + (w * s - tw) / 2, y0 + h * s / 2 - 7), label, fill=miss, font=font)

    def blit(rows, x0, y0, pick, flip=False, s=S):
        for ry, row in enumerate(rows):
            for rx, c in enumerate(row):
                col = pick(c)
                if not col:
                    continue
                px = (len(row) - 1 - rx) if flip else rx
                d.rectangle([x0 + px * s, y0 + ry * s, x0 + px * s + s - 1, y0 + ry * s + s - 1], fill=rgb(col))

    def robot_pick(tint, dim):
        return lambda c: None if c == "." else tint if c == "a" else dim if c == "c" else P.get(c) or MISSING

    yy = M
    for n in notes:
        d.text((M, yy), "※ " + n, fill=miss, font=font)
        yy += 18

    # 見出しの帯（ベンダーの見分け）
    tint0, dim0 = tints[0] if tints else (P.get("B") or MISSING, P.get("b") or MISSING)
    band_head = T(f"ベンダーの見分け: idle0・×{BIG}・同じ個体色 {tint0}（個体色はプロジェクトの色＝違いはシルエットだけ）",
                  f"vendors: idle0 x{BIG}, same tint {tint0} (silhouette only)")
    d.text((M, yy + 6), band_head, fill=accent, font=hfont)
    by = yy + 34 + BIG
    if not vend_secs:
        d.text((M, by), T("ベンダーの節が無い（見分けを裁定できない）", "no vendor sections"), fill=miss, font=font)
    for i, sec in enumerate(vend_secs):
        bx = M + BIG + i * band_pitch
        rows = sec["frames"].get("idle0")
        floor_box(bx, by, cw, ch, BIG)
        if px_lint._is_grid(rows):
            blit(rows, bx, by, robot_pick(tint0, dim0), s=BIG)
        else:
            missing_box(bx, by, cw, ch, BIG)
        d.text((bx, by + ch * BIG + BIG + 4), sec["title"], fill=text, font=hfont)
    px0 = M + len(vend_secs) * band_pitch
    for k, (a, b) in enumerate(pairs):
        ga, gb = a["frames"].get("idle0"), b["frames"].get("idle0")
        n = px_lint.diff_dots(ga, gb) if px_lint._is_grid(ga) and px_lint._is_grid(gb) else None
        low = n is None or n < px_lint.MIN_VENDOR_DIFF
        line = (f"{a['title']} ↔ {b['title']}  差 {n if n is not None else '?'} ドット（{px_lint.MIN_VENDOR_DIFF} 以上）"
                if ja else f"{a['title']} vs {b['title']}: {n if n is not None else '?'} dots (>= {px_lint.MIN_VENDOR_DIFF})")
        d.text((px0, by + k * 20), line, fill=miss if low else faint, font=font)
    yy += band_h
    n_missing = 0
    for sec in sections:
        names = sec["names"]
        have = sum(1 for n in names if px_lint._is_grid(sec["frames"].get(n)))
        lack = len(names) - have
        n_missing += lack if sec["vendor"] else 0
        d.text((M, yy + 6), sec["title"], fill=accent if sec["vendor"] else miss, font=hfont)
        vx = M + d.textlength(sec["title"], font=hfont) + 12
        d.text((vx, yy + 10), T(f"{have}/{len(names)} コマ", f"{have}/{len(names)} cells"), fill=faint, font=font)
        if lack:
            d.text((vx + 90, yy + 10), T(f"未 {lack}", f"missing {lack}"), fill=miss, font=font)
        if sec["extras"]:
            d.text((vx + 150, yy + 10), T(f"余 {len(sec['extras'])}（コマ表に無い）", f"extra {len(sec['extras'])}"),
                   fill=miss, font=font)
        yy += 34
        main_cols, rest = columns(sec)
        base = M + LEFT + len(main_cols) * pitch + gap
        placed_cols = ([(M + LEFT + i * pitch, c) for i, c in enumerate(main_cols)]
                       + [(base + i * pitch, c) for i, c in enumerate(rest)])
        for cx, (src, dst, kind) in placed_cols:
            rows = sec["frames"].get(src)
            bad_size = px_lint._is_grid(rows) and (len(rows) != ch or any(len(r) != cw for r in rows))
            color = miss if (bad_size or kind == "extra") else (faint if kind == "mirror" else text)
            d.text((cx, yy), dst + TAG[kind], fill=color, font=font)
        for r, (tint, dim) in enumerate(tints):
            ry = yy + 20 + r * row_h + S
            d.rectangle([M, ry + 4, M + 14, ry + 18], fill=rgb(tint))
            d.text((M + 20, ry + 4), tint, fill=faint, font=font)
            pick = robot_pick(tint, dim)
            for cx, (src, _dst, kind) in placed_cols:
                rows = sec["frames"].get(src)
                floor_box(cx, ry, cw, ch)
                if px_lint._is_grid(rows):
                    blit(rows, cx, ry, pick, kind == "mirror")
                else:
                    missing_box(cx, ry, cw, ch)
        yy += 20 + len(tints) * row_h + 16

    # タイル
    n_tile_missing = sum(1 for n in contract if not px_lint._is_grid(tiles.get(n)))
    head = T(f"タイル {len(tiles)} 種（契約 {len(contract)} 種を先頭に・×{S}）",
             f"tiles {len(tiles)} (contract {len(contract)} first, x{S})")
    d.text((M, yy + 8), head, fill=accent, font=hfont)
    if n_tile_missing:
        d.text((M + d.textlength(head, font=hfont) + 12, yy + 12),
               T(f"契約の未 {n_tile_missing}", f"contract missing {n_tile_missing}"), fill=miss, font=font)
    ty = yy + 40
    for name, rows, w, h, bx, by in tile_boxes:
        label, bad_size = tile_label(name, rows, w, h)
        d.text((bx, ty + by), label, fill=miss if bad_size else text, font=font)
        oy = ty + by + 18 + S
        floor_box(bx + S, oy, w, h)
        if px_lint._is_grid(rows):
            cmap = {**TP, **(paints.get(paint_of(name, furniture)) or {})}
            blit(rows, bx + S, oy, lambda c, cm=cmap: None if c == "." else cm.get(c) or MISSING)
        else:
            missing_box(bx + S, oy, w, h)

    out.parent.mkdir(parents=True, exist_ok=True)
    img.save(out)
    rel = out.relative_to(root) if out.is_relative_to(root) else out
    vend = [sec for sec in sections if sec["vendor"]]
    n_names = len(vend[0]["names"]) if vend else 0
    n_extra = sum(len(sec["extras"]) for sec in vend)
    mark = "✗" if errors else "✓"
    tail = f"・評価エラー {len(errors)} 件（下の注記＝「未」は評価エラーのせい）" if errors else ""
    print(f"{mark} コンタクトシート: {rel}（{width}×{height}・{len(vend)} ベンダー × {n_names} コマ × 個体色 "
          f"{len(tints)}・コマの未 {n_missing}・余 {n_extra}／タイル {len(tiles)} 種・契約の未 {n_tile_missing}{tail}）")
    for n in notes:
        print(f"  ※ {n}")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
