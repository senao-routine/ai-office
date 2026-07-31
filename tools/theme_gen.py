#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""AIオフィスのテーマ別アセット検査・プレビュー・生成ハーネス。"""
import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
ASSETS_REPO = ROOT / "assets"

THEMES = {
    # ★2026-07-17 スタイル一本化（ユーザー指示）: モダン参考01.jpeg の明るいピクセル
    #   オフィス調へ統一する移行用テーマ。生成後に既定ファイル名（サフィックス無し）へ
    #   コピーして正式採用し、modern/retro は退役する。
    "office": {
        "label": "オフィス（一本化スタイル）",
        "style_char": (
            "high quality modern pixel art chibi office worker, about 2.5 heads tall, "
            "soft bright cheerful colors, clean crisp pixel outlines, friendly simple face, "
            "detailed and clearly recognizable job-specific outfit and props, "
            "full body standing facing viewer, isolated on plain flat solid bright "
            "magenta background (#FF00FF), no magenta or pink in the subject"
        ),
        "style_scene": (
            "bright clean modern pixel art office interior, soft natural daylight, "
            "light warm wood and pale gray tones, lots of indoor plants, glass partitions, "
            "crisp pixel detail, cozy tech-startup atmosphere"
        ),
        "post_char": "smooth",
    },
    # 退役済みテーマの生成定義はCLI互換用に残す（ship対象は THEMES_READY のみ）。
    "modern": {
        "label": "モダン",
        "style_char": (
            "flat vector illustration, minimal geometric office worker character, "
            "blocky simple shapes, bold solid colors, flat design, no gradients, "
            "clean thick uniform outlines, full body standing, isolated on plain flat "
            "solid bright magenta background (#FF00FF), no magenta or pink colors in "
            "the subject itself"
        ),
        "style_scene": (
            "flat design vector office room, soft pastel colors, simple geometric "
            "furniture, clean minimal icon style"
        ),
        "post_char": "smooth",
    },
    "retro": {
        "label": "レトロ",
        # ★2026-07-15 二次FB「キャラがいまいち・もっとGB感」: 頭身をさらに下げ「巨頭2頭身」を最優先で明記。
        "style_char": (
            "Game Boy Color era JRPG overworld pixel sprite (Pokemon Gold/Silver NPC style), "
            "EXACTLY 2 heads tall: the head is half of the total height, huge oversized head, "
            "tiny simple body, big simple dot eyes, "
            "bright cheerful high-key colors, flat daylight lighting, "
            "chunky fat pixels, very limited palette (3-4 tones per hue), thick dark "
            "outline, full body standing facing viewer, isolated on "
            "plain flat solid bright magenta background (#FF00FF), no magenta or pink "
            "in the subject"
        ),
        # ★2026-07-15: 初回生成が夜っぽい暗色に寄った反省で「bright/daylight/no dark」を強く明記。
        #   GBCポケモン金銀の室内＝クリーム床・ミント/若草のアクセント・影なしフラット光が正。
        "style_scene": (
            "bright cheerful Game Boy Color JRPG interior tileset (Pokemon Gold/Silver "
            "indoor map style), high-key daylight palette, warm cream and pale yellow "
            "floors, mint green and soft teal accents, chunky fat pixels, very limited "
            "palette (4 tones per hue), flat lighting with no shadows, no dark moody "
            "tones, crisp thick outlines"
        ),
        "post_char": "fatpixel",
    },
    "rpg": {
        "label": "RPGタイル（モダン参考01フルフロア）",
        "style_char": (
            "cute chibi pixel-art office worker, 2-heads-tall with a huge head and tiny body, "
            "16-bit RPG town NPC look, crisp pixel outlines, bright colors, full body standing "
            "facing viewer, isolated on plain solid magenta background #FF00FF, "
            "no magenta or pink in the subject"
        ),
        "style_scene": (
            "bright 16-bit top-down RPG interior in an RPG Maker / Story of Seasons style, "
            "visible square floor tile grid, light gray tile floor with wooden accents, "
            "wood-framed walls, blue-tinted glass partitions, natural daytime light, "
            "indoor plants, crisp pixel outlines, no people, no text, no watermark"
        ),
        "post_char": "smooth",
        "char_px": 96,
        "char_colors": 32,
    },
}

# 2026-07-19 R22f: rpg（モダン参考01フルフロア×チビ2頭身）へ全面刷新。無印=rpg昇格済みだが
# __rpg セットも常設し ▶3c の52ファイル全量検査を回帰ガードとして維持する。旧一本化絵は
# _archive/assets_office_20260719/。office/modern/retro の生成定義はCLI互換用に温存。
THEMES_READY = ["rpg"]

# assets_gen.py FURN_JOBS の canvas / verify.sh ▶3 REQ と対応。変えるとき3箇所同時に。
REQUIRED_DIMS = {
    "bg": (1536, 1024),
    "bg2": (1536, 1024),
    "deskset": (280, 220),
    "deskchair": (80, 130),
    "meetset": (460, 300),
    "sofaset": (460, 300),
    "plant": (130, 180),
    "wallstrip": (347, 87),
}
CHAR_CANVAS = (192, 256)
CHAR_STEMS = (
    "works_hq",
    "blog",
    "video",
    "shorts",
    "xrun",
    "sakutto",
    "memo",
    "ribbon",
    "xpost",
    "generic_f",
    "generic_m",
)
# 汎用プール用の追加キャラ。生成済みでなくても ship 済みテーマ検査を失敗させない。
POOL_STEMS = (
    "generic_f2",
    "generic_m2",
    "generic_f3",
    "generic_m3",
    "generic_f4",
    "generic_m4",
    "generic_f5",
    "generic_m5",
)
ALL_CHAR_STEMS = CHAR_STEMS + POOL_STEMS
FURN_STEMS = ("deskset", "deskchair", "meetset", "sofaset", "plant")
# 第2ストライドと手振りは全キャラ生成完了までoptional。生成完了後はここを
# REQUIRED側へ移すだけで、spec/check/repadの対象を一括で切り替えられる。
OPTIONAL_CHAR_SUFFIXES = ("_walk2", "_walkdown2", "_walkup2", "_wave")
CHAR_REQUIRED_SUFFIXES = ("", "_walk", "_walkdown", "_walkup")
CHAR_SUFFIXES = CHAR_REQUIRED_SUFFIXES + OPTIONAL_CHAR_SUFFIXES
CHAR_THEME_VARIANTS = ("", "__rpg")
FIXED_STEMS = frozenset(REQUIRED_DIMS)
WALK_DIRECTIONS = (
    "walk",
    "walkdown",
    "walkup",
    "walk2",
    "walkdown2",
    "walkup2",
    "wave",
)
DATA_ASSETS = (
    Path.home()
    / "Library"
    / "Application Support"
    / "AIOffice"
    / "data"
    / "assets"
)

# ★office テーマ用の職業特化キャラ記述（ユーザーFB「作業の雰囲気に合った特徴的なキャラを」）。
#   theme=="office" のとき assets_gen.JOBS の記述の代わりにこちらを使う。
OFFICE_JOB_DESC = {
    "works_hq":  "female office manager with a red beret, neat bob hair, mustard cardigan, holding a clipboard, calm reliable leader vibe",
    "blog":      "female writer-blogger with round glasses and a messy bun, cozy beige cardigan, holding an open notebook and pen, a coffee mug hooked on one finger",
    "video":     "male video editor with large black headphones around his ears, dark t-shirt, holding a film clapperboard, a color-grading tablet under his arm, creator vibe",
    "shorts":    "energetic girl short-form video creator with twin tails, backwards cap, holding a smartphone on a small gimbal rig, ring-light badge on her bag",
    "xrun":      "male social media growth marketer wearing a blue cap and blue windbreaker, holding a megaphone, analytics chart sticking out of his pocket",
    "sakutto":   "male software developer in a teal hoodie with sticker-covered laptop under one arm, holding a wrench, tousled hair, maker vibe",
    "memo":      "male note-taking specialist in a neat navy suit vest and tie, pen behind his ear, holding a thick memo pad, precise secretary vibe",
    "ribbon":    "warm female community coordinator with a soft apron over office wear, holding a small gift box with a ribbon, welcoming host vibe",
    "xpost":     "male sharp social media strategist in a smart blazer, holding up a smartphone mid-post, confident smile, briefcase at his side",
    "generic_f": "female office worker in a white blouse and gray skirt, shoulder-length hair, holding a document folder",
    "generic_m": "male office worker in a light shirt and dark trousers, short dark hair, holding some papers",
    "generic_f2": "young woman with a high ponytail, mint hoodie, sneakers, energetic",
    "generic_m2": "young man with curly dark hair, casual white tee and chinos, friendly",
    "generic_f3": "woman with round glasses and low bun, lavender blouse, calm professional",
    "generic_m3": "slim young man with glasses, mustard sweater vest over shirt, studious",
    "generic_f4": "woman with short pixie cut, denim jacket, creative vibe",
    "generic_m4": "tall man with tied-back hair, olive shirt, relaxed",
    "generic_f5": "woman with side braid, coral cardigan, warm smile",
    "generic_m5": "young man with a gray beanie, plaid flannel shirt, maker vibe",
}
# 職業差分はオフィス調とRPG調のどちらでも維持する。スタイル記述だけをテーマ側で差し替える。
JOB_DESC_THEMES = ("office", "rpg")

PLANT_PROMPT = (
    "draw ONLY one healthy medium-height indoor potted plant, complete and not cropped, "
    "centered, matching the office reference's art style, palette, lighting, and slight "
    "top-down camera angle, no furniture, no character, no text, isolated on a plain flat "
    "solid bright magenta background (#FF00FF), the plant and pot contain no magenta colors"
)

PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
MAX_BG_BYTES = 1_200_000

# check は標準ライブラリだけで動かす。PIL / requests / assets_gen の import は行わず、
# preview や将来の生成系サブコマンドで必要になった関数の内側だけに閉じ込める。


def _theme_asset_specs(theme):
    """テーマ1種に必要なファイル名と検査条件を返す。"""
    for stem, dims in REQUIRED_DIMS.items():
        yield f"{stem}__{theme}.png", dims, False, stem in ("bg", "bg2"), False
    for stem in CHAR_STEMS:
        for suffix in CHAR_REQUIRED_SUFFIXES:
            yield f"{stem}__{theme}{suffix}.png", None, True, False, False
        for suffix in OPTIONAL_CHAR_SUFFIXES:
            yield f"{stem}__{theme}{suffix}.png", None, True, False, True
    for stem in POOL_STEMS:
        for suffix in CHAR_REQUIRED_SUFFIXES:
            yield f"{stem}__{theme}{suffix}.png", None, True, False, True


def _inspect_png(path, dims, char_only, size_limited):
    """PNGマジック、IHDR寸法、必要なら背景サイズ上限を検査する。"""
    errors = []
    try:
        size = path.stat().st_size
        with path.open("rb") as src:
            head = src.read(24)
    except OSError as exc:
        return [f"{path.name}: 読み取り失敗 ({exc})"]

    if size_limited and size > MAX_BG_BYTES:
        errors.append(
            f"{path.name}: {size} bytes > 上限 {MAX_BG_BYTES} bytes"
        )
    if len(head) < 24:
        errors.append(f"{path.name}: PNGヘッダが24 bytes未満")
        return errors
    if head[:8] != PNG_MAGIC:
        errors.append(f"{path.name}: PNGマジック不正")
        return errors
    if head[12:16] != b"IHDR":
        errors.append(f"{path.name}: IHDRが先頭チャンクにない")
        return errors

    width = int.from_bytes(head[16:20], "big")
    height = int.from_bytes(head[20:24], "big")
    if char_only:
        if (width, height) != CHAR_CANVAS:
            errors.append(
                f"{path.name}: 寸法 {width}x{height} != 期待 {CHAR_CANVAS[0]}x{CHAR_CANVAS[1]}"
            )
    elif (width, height) != dims:
        errors.append(
            f"{path.name}: 寸法 {width}x{height} != 期待 {dims[0]}x{dims[1]}"
        )
    return errors


def check_themes():
    """ship済みテーマは全量、未shipテーマは存在分だけ検査する。"""
    errors = []
    checked = 0
    ready = set(THEMES_READY)

    for theme in sorted(ready - set(THEMES)):
        errors.append(f"THEMES_READY: 未定義テーマ {theme!r}")

    for theme in THEMES:
        require_all = theme in ready
        for filename, dims, char_only, size_limited, optional in _theme_asset_specs(theme):
            path = ASSETS_REPO / filename
            if not path.is_file():
                # R30-P3: 背景一枚絵はタイル駆動へ移行済み。生成定義/検査の
                # 互換性は残すが、退役したbg/bg2だけはship必須から外す。
                legacy_background = filename.startswith(("bg__", "bg2__"))
                if require_all and not optional and not legacy_background:
                    errors.append(f"{theme}: 必須ファイル欠落 {filename}")
                continue
            checked += 1
            errors.extend(
                f"{theme}: {error}"
                for error in _inspect_png(path, dims, char_only, size_limited)
            )

    if errors:
        print(f"✗ テーマアセット検査: {len(errors)}件の不備", file=sys.stderr)
        for error in errors:
            print(f"  - {error}", file=sys.stderr)
        return 1

    ready_label = ", ".join(THEMES_READY) if THEMES_READY else "なし"
    print(f"✓ テーマアセット検査OK（ship済み: {ready_label}・検査 {checked}ファイル）")
    return 0


# 座標は ui/office_page.html の DESK_ANCHORS/FLOOR_END と揃える（UI側変更時はここも）。
PREVIEW_SCALE = 1536 / 1080
DESK_XS = (255, 395, 535, 675)
DESK_YS = (205, 360)
DESK_SIZE = (120, 94)
FLOOR_END = 380


def preview_theme(theme):
    """bg2へ机グリッドと会議室床端を重ね、配置確認画像を保存する。"""
    source = ASSETS_REPO / ("bg2.png" if theme == "vintage" else f"bg2__{theme}.png")
    if not source.is_file():
        print(f"✗ preview元のbg2が無い: {source.relative_to(ROOT)}", file=sys.stderr)
        return 1

    try:
        from PIL import Image, ImageDraw
    except ImportError:
        print("✗ preview には Pillow (PIL) が必要", file=sys.stderr)
        return 1

    try:
        with Image.open(source) as image:
            canvas = image.convert("RGBA")
    except OSError as exc:
        print(f"✗ preview元を開けない: {source.relative_to(ROOT)} ({exc})", file=sys.stderr)
        return 1

    draw = ImageDraw.Draw(canvas)
    desk_color = (0, 220, 255, 255)
    floor_color = (255, 45, 85, 255)
    line_width = max(3, round(3 * PREVIEW_SCALE))
    desk_w, desk_h = DESK_SIZE
    for dy in DESK_YS:
        for dx in DESK_XS:
            draw.rectangle(
                (
                    round(dx * PREVIEW_SCALE),
                    round(dy * PREVIEW_SCALE),
                    round((dx + desk_w) * PREVIEW_SCALE),
                    round((dy + desk_h) * PREVIEW_SCALE),
                ),
                outline=desk_color,
                width=line_width,
            )
    floor_y = round(FLOOR_END * PREVIEW_SCALE)
    draw.line((0, floor_y, canvas.width - 1, floor_y), fill=floor_color, width=line_width)

    output = ROOT / "tests" / "artifacts" / f"theme_preview_{theme}.png"
    output.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(output, "PNG")
    print(f"✓ テーマ配置preview生成: {output.relative_to(ROOT)}")
    return 0


def themed_name(stem, theme, walk=False, direction=None):
    """テーマ付きアセット名を返す（ファイル操作は行わない）。"""
    if direction is not None:
        if direction not in WALK_DIRECTIONS:
            raise ValueError(f"不明な歩行方向: {direction}")
        suffix = f"_{direction}"
    else:
        suffix = "_walk" if walk else ""
    # vintage は既存アセットそのものが正本。THEMES/check の対象には追加せず、
    # custom / walkframes の特別経路だけがサフィックスなしの名前を使う。
    if theme == "vintage":
        return f"{stem}{suffix}.png"
    return f"{stem}__{theme}{suffix}.png"


def _codex_gen(prompt, outdir, refs=(), count=1):
    """Codex画像生成ラッパーを1回呼び、今回生成されたPNGパスを返す。

    codex_image.sh は「最新PNG」を収穫するため、複数呼び出しを並列実行すると
    別ジョブの出力が混線し得る。この関数と全呼び出し元は直列運用を前提とする。
    """
    outdir = Path(outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    ref_args = sum([["-r", str(ref)] for ref in refs], [])
    command = [
        "bash",
        str(Path.home() / ".claude" / "scripts" / "codex_image.sh"),
        *ref_args,
        str(count),
        str(outdir),
        prompt,
    ]
    # ★launchd デーモン経由だと PATH が最小構成で `codex` が見つからず即失敗する
    #   （2026-07-16 実障害）。codex/node の定番配置を PATH 先頭へ明示注入する。
    env = dict(os.environ)
    extra = [str(Path.home() / ".npm-global" / "bin"), "/opt/homebrew/bin", "/usr/local/bin"]
    env["PATH"] = ":".join(extra + [env.get("PATH", "/usr/bin:/bin")])
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=900,
            env=env,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError("codex_image.sh が900秒でタイムアウトしました") from exc

    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or "詳細なし"
        raise RuntimeError(
            f"codex_image.sh が終了コード {result.returncode} で失敗: {detail}"
        )

    generated = []
    for line in result.stdout.splitlines():
        candidate = Path(line.strip()).expanduser()
        if candidate.suffix.lower() == ".png" and candidate.is_file():
            generated.append(candidate)
    if not generated:
        raise RuntimeError("codex_image.sh のstdoutに存在するPNGパスがありません")
    return generated


def _scaled_width(image, height):
    """縦横比を保った指定高さの幅（最低1px）を返す。"""
    if image.height <= 0:
        raise ValueError("画像の高さが0です")
    return max(1, round(image.width * height / image.height))


def _finalize_char(
    src,
    dst,
    post,
    smooth_nearest=False,
    char_px=None,
    char_colors=None,
):
    """生成された立ち絵/歩き絵を透過・テーマ別縮小して固定キャンバスにする。"""
    from PIL import Image
    from assets_gen import chroma_key, pad_to_canvas

    src = Path(src)
    dst = Path(dst)
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy(src, dst)
    chroma_key(dst)

    nearest = Image.Resampling.NEAREST
    with Image.open(dst) as opened:
        image = opened.convert("RGBA")
    if char_px is not None and char_colors is not None:
        image = image.resize((_scaled_width(image, int(char_px)), int(char_px)), nearest)
        image = image.quantize(colors=int(char_colors))
        image = image.resize((_scaled_width(image, 256), 256), nearest)
    elif post == "fatpixel":
        image = image.resize((_scaled_width(image, 64), 64), nearest)
        image = image.quantize(colors=12)
        image = image.resize((_scaled_width(image, 256), 256), nearest)
    elif post == "smooth":
        image = image.resize(
            (_scaled_width(image, 256), 256),
            nearest if smooth_nearest else Image.Resampling.LANCZOS,
        )
    else:
        raise ValueError(f"不明なキャラ後処理: {post}")
    image.convert("RGBA").save(dst, "PNG")
    pad_to_canvas(dst, CHAR_CANVAS)
    return dst


def _finalize_furn(src, dst, canvas):
    """家具を透過し、固定キャンバスへ下端中央揃えする。"""
    from assets_gen import chroma_key, pad_to_canvas

    src = Path(src)
    dst = Path(dst)
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy(src, dst)
    chroma_key(dst)
    pad_to_canvas(dst, canvas)
    return dst


def _standard_char_names():
    """CHAR_STEMS/POOL_STEMS の無印・__rpg各アセット名を返す。"""
    return {
        f"{stem}{theme}{suffix}.png"
        for stem in ALL_CHAR_STEMS
        for theme in CHAR_THEME_VARIANTS
        for suffix in CHAR_SUFFIXES
    }


def _asset_base_stem(stem):
    """テーマサフィックスを除いたアセットstemを返す。"""
    return stem.split("__", 1)[0]


def _repad_candidates(root, include_custom=False):
    """root内のrepad対象PNGを返す。customはdata側だけで許可する。"""
    root = Path(root)
    if not root.is_dir():
        return []

    from PIL import Image

    standard_names = _standard_char_names()
    candidates = {
        path
        for path in root.glob("*.png")
        if path.name in standard_names
    }
    if include_custom:
        for path in root.glob("*.png"):
            if path.name in standard_names or _asset_base_stem(path.stem) in FIXED_STEMS:
                continue
            with Image.open(path) as image:
                if image.height == CHAR_CANVAS[1]:
                    candidates.add(path)
    return sorted(candidates)


def _repad_files(paths):
    """画像群を固定キャンバス化し、(repad枚数, skip枚数)を返す。"""
    from PIL import Image
    from assets_gen import pad_to_canvas

    repadded = 0
    skipped = 0
    for path in paths:
        path = Path(path)
        with Image.open(path) as image:
            already_fixed = image.size == CHAR_CANVAS
        if already_fixed:
            skipped += 1
            continue
        pad_to_canvas(path, CHAR_CANVAS)
        repadded += 1
    return repadded, skipped


def repad_assets(include_data=False):
    """repo標準キャラを固定キャンバス化し、必要ならdata側customも処理する。"""
    repo_paths = _repad_candidates(ASSETS_REPO)
    repadded, skipped = _repad_files(repo_paths)
    repo_changed = bool(repadded)

    if include_data:
        data_paths = _repad_candidates(DATA_ASSETS, include_custom=True)
        data_repadded, data_skipped = _repad_files(data_paths)
        repadded += data_repadded
        skipped += data_skipped

    if repo_changed:
        command = [sys.executable, str(ROOT / "tools" / "gen_pwa_sprites.py")]
        result = subprocess.run(command, cwd=ROOT)
        if result.returncode != 0:
            raise RuntimeError(
                "repo側キャラ変更後の gen_pwa_sprites.py が失敗しました"
            )

    print(f"✓ repad {repadded}枚 / skip {skipped}枚")
    return 0


def _cmd_repad(args):
    return repad_assets(include_data=args.data)


def _finalize_bg(src, dst, theme, size=(1536, 1024)):
    """背景を固定寸法へ変換し、256色PNGへ軽量化する。"""
    from PIL import Image
    from assets_gen import slim_bg

    src = Path(src)
    dst = Path(dst)
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy(src, dst)
    resample = (
        Image.Resampling.NEAREST
        if theme == "retro"
        else Image.Resampling.LANCZOS
    )
    with Image.open(dst) as opened:
        image = opened.convert("RGB").resize(size, resample)
    image.save(dst, "PNG")
    slim_bg(dst)
    byte_size = dst.stat().st_size
    if byte_size > MAX_BG_BYTES:
        print(
            f"⚠ {dst.name}: {byte_size} bytes > verify ▶3 上限 {MAX_BG_BYTES} bytes",
            file=sys.stderr,
            flush=True,
        )
    return dst


def make_wallstrip(theme):
    """壁天面＋壁面タイルを横リピートしてUI用wallstripを作る。"""
    from PIL import Image
    from assets_gen import ASSETS

    top_source = ASSETS / "tile_wall_top.png"
    face_source = ASSETS / "tile_wall_face.png"
    for source in (top_source, face_source):
        if not source.is_file():
            raise RuntimeError(f"wallstrip元のタイルがありません: {source}")
    output = ASSETS / themed_name("wallstrip", theme)
    output.parent.mkdir(parents=True, exist_ok=True)
    # PCのfloorlayerと同じ24pxタイルを使い、上24pxを天面、残りを壁面にする。
    # PWA/worker側の347x87契約は変更しない。
    tile_size = 24
    width, height = REQUIRED_DIMS["wallstrip"]
    with Image.open(top_source) as opened_top, Image.open(face_source) as opened_face:
        top_tile = opened_top.convert("RGBA").resize(
            (tile_size, tile_size), Image.Resampling.NEAREST
        )
        face_tile = opened_face.convert("RGBA").resize(
            (tile_size, tile_size), Image.Resampling.NEAREST
        )
    strip = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    for x in range(0, width, tile_size):
        strip.paste(top_tile, (x, 0), top_tile)
        for y in range(tile_size, height, tile_size):
            strip.paste(face_tile, (x, y), face_tile)
    strip.save(output, "PNG")
    return output


def _announce(generations):
    minutes = max(2, generations * 2)
    print(
        f"⚠ Codex課金・直列・約{minutes}分（{generations}枚）",
        file=sys.stderr,
        flush=True,
    )


def _require_file(path, purpose):
    path = Path(path)
    if not path.is_file():
        raise RuntimeError(f"{purpose}がありません: {path}")
    return path


def _reject_deprecated_bg_lane():
    raise RuntimeError(
        "R30でタイル駆動へ移行済み・bgレーンは非推奨です（tile_* / furn_* を使用してください）"
    )


def _prefer_asset(assets, filename):
    """data側を優先し、無ければrepo同梱アセットを返す。"""
    data_path = Path(assets) / filename
    return data_path if data_path.is_file() else ASSETS_REPO / filename


def _raw_outdir(theme, job):
    output = ROOT / "tests" / "artifacts" / "theme_raw" / theme / job
    output.mkdir(parents=True, exist_ok=True)
    return output


def _one_generated(prompt, outdir, refs):
    generated = _codex_gen(prompt, outdir, refs=refs, count=1)
    return generated[-1]


def _without_style(prompt, style):
    """assets_genの先頭STYLEだけを除き、キャラ固有記述を返す。"""
    if prompt.startswith(style):
        return prompt[len(style):].lstrip(" ,")
    return prompt


def _character_theme(theme):
    """キャラ生成用のstyle・後処理を返す（vintageは実行時だけ特別扱い）。"""
    if theme == "vintage":
        # vintage を THEMES へ足すと check/REQUIRED の不変条件が変わるため、
        # custom / walkframes からだけ既存 assets_gen の正本STYLEを流用する。
        from assets_gen import STYLE

        return STYLE, "smooth", True
    spec = THEMES[theme]
    return spec["style_char"], spec["post_char"], False


def _character_post_options(theme):
    """テーマ定義にある任意のピクセル量子化設定を返す。"""
    if theme == "vintage":
        return None, None
    spec = THEMES[theme]
    return spec.get("char_px"), spec.get("char_colors")


def _character_description(theme, name, jobs, base_style):
    """テーマに応じた職業記述を選ぶ（スタイル記述とは分離する）。"""
    if theme in JOB_DESC_THEMES and name in OFFICE_JOB_DESC:
        return OFFICE_JOB_DESC[name]
    return _without_style(jobs[name]["prompt"], base_style)


def _walk_prompt(base_prompt):
    """入力テーマを別スタイルへ変えずに歩行ポーズだけを派生させる。"""
    return (
        base_prompt
        .replace("same exact pixel art character", "same exact character")
        .replace(
            "retro 16-bit pixel art, crisp pixels, no anti-aliasing",
            "the exact same visual rendering style as the input reference",
        )
    )


OPPOSITE_STRIDE_PROMPT = (
    "the OPPOSITE stride of the reference mid-walk pose: the other leg forward, "
    "arms swung the other way"
)
WAVE_POSE_PROMPT = (
    "same exact character standing and cheerfully waving one hand raised beside the head, "
    "friendly greeting pose, other arm relaxed"
)


def _wave_prompt(base_prompt):
    """歩行用promptのスタイル・背景指定を保った手振りpromptを返す。"""
    normalized = _walk_prompt(base_prompt)
    style_marker = "the exact same visual rendering style as the input reference"
    rendering = (
        normalized[normalized.index(style_marker):]
        if style_marker in normalized
        else normalized
    )
    return (
        f"{WAVE_POSE_PROMPT}, identical colors, style, proportions and held items. "
        f"{rendering}"
    )


def _theme_neutral_scene_prompt(prompt):
    """既存家具promptの構図条件を残し、retro固定語だけを中立化する。"""
    return (
        prompt
        .replace("pixel art style", "visual style")
        .replace("pixel art office room", "office room")
        .replace("crisp pixels", "clean rendering")
    )


def _cmd_bg(args):
    _reject_deprecated_bg_lane()
    _announce(3)
    reference = _require_file(ASSETS_REPO / "bg2.png", "bg生成reference")
    extra_refs = tuple(Path(p) for p in (getattr(args, "ref", None) or []))
    candidates = ROOT / "tests" / "artifacts" / "theme_cand" / args.theme
    reference_note = (
        " Additional reference images are for art style, tile rendering, and palette only; "
        "follow the first reference strictly for the room layout and camera."
        if extra_refs
        else ""
    )
    prompt = (
        "Redraw the reference as the same exact office room: preserve the room layout, "
        "camera angle, perspective, architectural zones, walls, windows, and lighting. "
        f"Render it in this style: {THEMES[args.theme]['style_scene']}. "
        f"No people, characters, text, letters, logos, or watermark.{reference_note}"
    )
    generated = _codex_gen(prompt, candidates, refs=(reference, *extra_refs), count=3)
    if len(generated) != 3:
        raise RuntimeError(f"bg候補が3枚ではありません: {len(generated)}枚")
    print("候補3枚を生成しました。自動採用はしていません:")
    for path in generated:
        print(path)
    return 0


def _cmd_promote_bg(args):
    _reject_deprecated_bg_lane()
    from assets_gen import ASSETS, _sync_repo

    source = _require_file(args.file, "採用候補")
    ASSETS.mkdir(parents=True, exist_ok=True)
    filename = themed_name("bg", args.theme)
    output = _finalize_bg(source, ASSETS / filename, args.theme)
    _sync_repo([filename])
    print(f"✓ bg候補を採用: {output}")
    return 0


def _cmd_bg2(args):
    _reject_deprecated_bg_lane()
    _announce(1)
    from assets_gen import ASSETS, FURN_JOBS, _sync_repo

    ASSETS.mkdir(parents=True, exist_ok=True)
    original = _require_file(_prefer_asset(ASSETS, "bg2.png"), "bg2 reference")
    themed = _prefer_asset(ASSETS, themed_name("bg2", args.theme))
    first_reference = themed if themed.is_file() else original
    empty_room = _theme_neutral_scene_prompt(FURN_JOBS["bg2"]["prompt"])
    prompt = (
        f"Render in this style: {THEMES[args.theme]['style_scene']}. "
        f"{empty_room} Preserve the reference layout and camera exactly."
    )
    raw = _one_generated(
        prompt,
        _raw_outdir(args.theme, "bg2"),
        (first_reference, original),
    )
    filename = themed_name("bg2", args.theme)
    output = _finalize_bg(raw, ASSETS / filename, args.theme)
    _sync_repo([filename])
    print(f"✓ bg2生成: {output}")
    return 0


def _cmd_wallstrip(args):
    from assets_gen import _sync_repo

    output = make_wallstrip(args.theme)
    _sync_repo([output.name])
    print(f"✓ wallstrip生成: {output}")
    return 0


def _cmd_furniture(args):
    names = list(args.names) or list(FURN_STEMS)
    _announce(len(names))
    from assets_gen import ASSETS, FURN_JOBS, _sync_repo

    ASSETS.mkdir(parents=True, exist_ok=True)
    background = _require_file(
        _prefer_asset(ASSETS, themed_name("bg", args.theme)),
        "テーマ家具のreference bg",
    )
    generated_names = []
    for name in names:
        base_prompt = (
            PLANT_PROMPT
            if name == "plant"
            else _theme_neutral_scene_prompt(FURN_JOBS[name]["prompt"])
        )
        prompt = (
            f"Match this theme style: {THEMES[args.theme]['style_scene']}. "
            f"Use the reference for palette, lighting, perspective, and line treatment. "
            f"{base_prompt}. The complete object must be isolated on a plain flat solid "
            "bright magenta background (#FF00FF), with no magenta in the object."
        )
        raw = _one_generated(
            prompt,
            _raw_outdir(args.theme, f"furniture_{name}"),
            (background,),
        )
        filename = themed_name(name, args.theme)
        _finalize_furn(raw, ASSETS / filename, REQUIRED_DIMS[name])
        generated_names.append(filename)
        print(f"✓ 家具生成: {ASSETS / filename}")
    _sync_repo(generated_names)
    return 0


def _cmd_chars(args):
    selected = list(args.names) or list(ALL_CHAR_STEMS)
    from assets_gen import ASSETS, JOBS, STYLE, _sync_repo
    # generic_m はスタイルアンカー。分割バッチ再実行（ハング対策）で毎回作り直さないよう、
    # 明示指定されたときだけ先頭で再生成し、それ以外は既存アンカーを参照に使う。
    if "generic_m" in selected:
        names = ["generic_m", *(name for name in selected if name != "generic_m")]
    else:
        names = list(selected)
    _announce(len(names))
    char_px, char_colors = _character_post_options(args.theme)

    ASSETS.mkdir(parents=True, exist_ok=True)
    background = _require_file(
        _prefer_asset(ASSETS, themed_name("bg", args.theme)),
        "テーマキャラのreference bg",
    )
    anchor = ASSETS / themed_name("generic_m", args.theme)
    generated_names = []
    for index, name in enumerate(names):
        # office / rpg テーマは職業特化の専用記述（OFFICE_JOB_DESC）を優先する
        description = _character_description(args.theme, name, JOBS, STYLE)
        prompt = f"{THEMES[args.theme]['style_char']}, {description}"
        # generic_m 自身の生成時はアンカー参照なし。他キャラは既存アンカーがあれば参照に足す
        if name == "generic_m" or not anchor.is_file():
            references = (background,)
        else:
            references = (background, anchor)
        # --ref 外部参照（例: ユーザー提供のGB実機スクショ）を先頭に足してスタイルを強く引っ張る
        extra = tuple(Path(p) for p in (getattr(args, "ref", None) or []))
        references = (*extra, *references)
        raw = _one_generated(
            prompt,
            _raw_outdir(args.theme, f"char_{name}"),
            references,
        )
        filename = themed_name(name, args.theme)
        _finalize_char(
            raw,
            ASSETS / filename,
            THEMES[args.theme]["post_char"],
            char_px=char_px,
            char_colors=char_colors,
        )
        generated_names.append(filename)
        print(f"✓ キャラ生成: {ASSETS / filename}")
    _sync_repo(generated_names)
    return 0


def _cmd_walkframes(args):
    names = list(args.names) or list(ALL_CHAR_STEMS)
    from assets_gen import ASSETS, WALK_PROMPT, _sync_repo

    _style_char, post_char, smooth_nearest = _character_theme(args.theme)
    char_px, char_colors = _character_post_options(args.theme)
    raw_dirs = getattr(args, "dirs", None) or ["walk", "walkdown", "walkup"]
    directions = [part for value in raw_dirs for part in str(value).split(",") if part]
    invalid = [direction for direction in directions if direction not in WALK_DIRECTIONS]
    if not directions or invalid:
        raise ValueError(
            "--dirs は "
            + ", ".join(WALK_DIRECTIONS)
            + " のいずれかを指定してください"
        )
    _announce(len(names) * len(directions))
    ASSETS.mkdir(parents=True, exist_ok=True)
    generated_names = []
    for name in names:
        standing = _require_file(
            _prefer_asset(ASSETS, themed_name(name, args.theme)),
            f"{name}の立ち絵reference",
        )
        for direction in directions:
            prompt = (
                _wave_prompt(WALK_PROMPT)
                if direction == "wave"
                else _walk_prompt(WALK_PROMPT)
            )
            if direction in ("walkdown", "walkdown2"):
                prompt += (
                    " Show the same character facing directly toward the viewer in a clear "
                    "front-facing mid-walk pose."
                )
            elif direction in ("walkup", "walkup2"):
                prompt += (
                    " Show the same character facing directly away from the viewer in a clear "
                    "back-facing mid-walk pose."
                )
            if direction in ("walk2", "walkdown2", "walkup2"):
                prompt += f" {OPPOSITE_STRIDE_PROMPT}."
            references = [standing]
            if direction in ("walk2", "walkdown2", "walkup2"):
                first_direction = direction[:-1]
                first_frame = _prefer_asset(
                    ASSETS,
                    themed_name(name, args.theme, direction=first_direction),
                )
                if first_frame.is_file():
                    references.append(first_frame)
            raw = _one_generated(
                prompt,
                _raw_outdir(args.theme, f"walk_{name}_{direction}"),
                tuple(references),
            )
            filename = themed_name(name, args.theme, direction=direction)
            _finalize_char(
                raw,
                ASSETS / filename,
                post_char,
                smooth_nearest=smooth_nearest,
                char_px=char_px,
                char_colors=char_colors,
            )
            generated_names.append(filename)
            print(f"✓ 歩き絵生成: {ASSETS / filename}")
    _sync_repo(generated_names)
    return 0


def _generate_custom(theme, slug, label, photo_ref=None):
    """custom立ち絵と歩き絵を直列生成する（repo同期はしない）。"""
    from assets_gen import ASSETS, WALK_PROMPT

    style_char, post_char, smooth_nearest = _character_theme(theme)
    char_px, char_colors = _character_post_options(theme)
    ASSETS.mkdir(parents=True, exist_ok=True)
    photo = None
    if photo_ref is not None:
        photo = _require_file(photo_ref, "人物写真reference")
        # 写真が人物像の正本。custom_spec のmd5由来の髪型・服装指定は混ぜない。
        description = (
            "the attached photo is the person reference: capture their hairstyle, "
            "hair color, glasses, clothing colors and overall vibe in the sprite"
        )
    else:
        from assets_gen import STYLE, custom_spec

        description = _without_style(custom_spec(label)["prompt"], STYLE)
    prompt = f"{style_char}, {description}"
    references = []
    if photo is not None:
        references.append(photo)
    for filename in (themed_name("bg", theme), themed_name("generic_m", theme)):
        candidate = _prefer_asset(ASSETS, filename)
        if candidate.is_file():
            references.append(candidate)

    standing_name = themed_name(slug, theme)
    standing = ASSETS / standing_name
    raw_standing = _one_generated(
        prompt,
        _raw_outdir(theme, f"custom_{slug}"),
        tuple(references),
    )
    _finalize_char(
        raw_standing,
        standing,
        post_char,
        smooth_nearest=smooth_nearest,
        char_px=char_px,
        char_colors=char_colors,
    )

    walk_name = themed_name(slug, theme, walk=True)
    walk = ASSETS / walk_name
    walk_style = style_char.replace(
        "full body standing",
        "full body in a clear mid-walk pose",
    )
    walk_prompt = (
        f"{_walk_prompt(WALK_PROMPT)} Character identity details: {description}. "
        f"Theme rendering requirements: {walk_style}"
    )
    raw_walk = _one_generated(
        walk_prompt,
        _raw_outdir(theme, f"custom_{slug}_walk"),
        (*((photo,) if photo is not None else ()), standing),
    )
    _finalize_char(
        raw_walk,
        walk,
        post_char,
        smooth_nearest=smooth_nearest,
        char_px=char_px,
        char_colors=char_colors,
    )
    print(f"✓ custom生成（repo同期対象外）: {standing} {walk}")
    return standing, walk


def _cmd_custom(args):
    _announce(2)
    _generate_custom(
        args.theme,
        args.slug,
        " ".join(args.label),
        photo_ref=getattr(args, "photo_ref", None),
    )
    return 0


def _load_office_config(assets):
    """OFFICE_CONFIGまたはdata/repoのconfig JSONを読み込む。"""
    configured = os.environ.get("OFFICE_CONFIG")
    if configured and configured.lstrip().startswith(("{", "[")):
        try:
            return json.loads(configured)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"OFFICE_CONFIGのJSONが不正です: {exc}") from exc

    candidates = []
    if configured:
        configured_path = Path(configured).expanduser()
        if configured_path.is_dir():
            candidates.extend((configured_path / "config.json", configured_path / "config"))
        else:
            candidates.append(configured_path)
    candidates.extend(
        (
            Path(assets).parent / "config.json",
            Path(assets).parent / "config",
            ROOT / "config.json",
            ROOT / "config",
        )
    )
    seen = set()
    for path in candidates:
        if path in seen or not path.is_file():
            continue
        seen.add(path)
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"configを読めません: {path} ({exc})") from exc
    raise RuntimeError(
        "OFFICE_CONFIG/configが見つかりません（OFFICE_CONFIGまたはdata/config.jsonを確認）"
    )


def _normalise_sprite_slug(sprite):
    slug = Path(sprite).name
    if slug.lower().endswith(".png"):
        slug = slug[:-4]
    if slug.endswith("_walk"):
        slug = slug[:-5]
    for theme in THEMES:
        marker = f"__{theme}"
        if slug.endswith(marker):
            slug = slug[:-len(marker)]
            break
    return slug


def _custom_sprites(config, standard_names):
    """任意階層のconfigからspriteと表示ラベルを重複なく収集する。"""
    found = {}

    def visit(value):
        if isinstance(value, dict):
            sprite = value.get("sprite")
            if isinstance(sprite, str) and sprite.strip():
                slug = _normalise_sprite_slug(sprite.strip())
                if slug and slug not in standard_names:
                    label = next(
                        (
                            value[key]
                            for key in ("label", "name", "title", "display_name")
                            if isinstance(value.get(key), str) and value[key].strip()
                        ),
                        slug,
                    )
                    found.setdefault(slug, label)
            for child in value.values():
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)

    visit(config)
    return list(found.items())


def _cmd_custom_all(args):
    from assets_gen import ASSETS, JOBS

    config = _load_office_config(ASSETS)
    custom = _custom_sprites(config, set(JOBS))
    if not custom:
        print("✓ configにJOBS外のcustom spriteはありません")
        return 0
    _announce(len(custom) * 2)
    for slug, label in custom:
        _generate_custom(args.theme, slug, label)
    return 0


def build_parser():
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    check_parser = subparsers.add_parser("check", help="テーマアセットを検査")
    check_parser.set_defaults(handler=lambda _args: check_themes())

    repad_parser = subparsers.add_parser(
        "repad",
        help="キャラPNGを192x256の透過キャンバスへ再配置",
    )
    repad_parser.add_argument(
        "--data",
        action="store_true",
        help="repoに加えてApplication Supportのdata/assetsも処理",
    )
    repad_parser.set_defaults(handler=_cmd_repad)

    preview_parser = subparsers.add_parser("preview", help="テーマの配置確認画像を生成")
    preview_parser.add_argument("theme", choices=("vintage", *THEMES))
    preview_parser.set_defaults(handler=lambda args: preview_theme(args.theme))

    for theme, spec in THEMES.items():
        theme_parser = subparsers.add_parser(theme, help=f"{spec['label']}テーマを生成")
        theme_parser.set_defaults(theme=theme)
        commands = theme_parser.add_subparsers(dest="theme_command", required=True)

        bg_parser = commands.add_parser("bg", help="背景候補を3枚生成（自動採用なし）")
        bg_parser.add_argument(
            "--ref",
            action="append",
            default=[],
            help="追加のスタイル・タイル表現・パレット参照画像（複数可）",
        )
        bg_parser.set_defaults(handler=_cmd_bg)

        promote_parser = commands.add_parser("promote-bg", help="選択したbg候補を採用")
        promote_parser.add_argument("file", type=Path)
        promote_parser.set_defaults(handler=_cmd_promote_bg)

        bg2_parser = commands.add_parser("bg2", help="可動家具なしの空部屋を生成")
        bg2_parser.set_defaults(handler=_cmd_bg2)

        wallstrip_parser = commands.add_parser("wallstrip", help="壁タイルから347x87の壁帯を生成")
        wallstrip_parser.set_defaults(handler=_cmd_wallstrip)

        furniture_parser = commands.add_parser("furniture", help="家具を生成")
        furniture_parser.add_argument("names", nargs="*", choices=FURN_STEMS)
        furniture_parser.set_defaults(handler=_cmd_furniture)

        chars_parser = commands.add_parser("chars", help="標準キャラ立ち絵を生成")
        chars_parser.add_argument("names", nargs="*", choices=ALL_CHAR_STEMS)
        chars_parser.add_argument("--ref", action="append", default=[],
                                  help="追加スタイル参照画像（先頭参照として渡す・複数可）")
        chars_parser.set_defaults(handler=_cmd_chars)

        walk_parser = commands.add_parser("walkframes", help="標準キャラ歩き絵を生成")
        walk_parser.add_argument("names", nargs="*", choices=ALL_CHAR_STEMS)
        walk_parser.add_argument(
            "--dirs",
            nargs="+",
            default=["walk", "walkdown", "walkup"],
            help=(
                "生成方向（"
                + ", ".join(WALK_DIRECTIONS)
                + "。カンマ区切り・複数指定可）"
            ),
        )
        walk_parser.set_defaults(handler=_cmd_walkframes)

        custom_parser = commands.add_parser("custom", help="customキャラ2枚を生成")
        custom_parser.add_argument("slug")
        custom_parser.add_argument("label", nargs="+")
        custom_parser.add_argument(
            "--photo-ref",
            type=Path,
            help="人物写真reference（人物像の正本・参照の先頭に渡す）",
        )
        custom_parser.set_defaults(handler=_cmd_custom)

        custom_all_parser = commands.add_parser(
            "custom-all",
            help="config内の全customキャラを生成",
        )
        custom_all_parser.set_defaults(handler=_cmd_custom_all)

    # vintage は既存ファイルが正本なので THEMES には追加しない。
    # R4で必要な custom と既存歩き絵更新用 walkframes だけを明示的に許可する。
    vintage_parser = subparsers.add_parser(
        "vintage",
        help="既存vintageキャラを生成（custom / walkframes限定）",
    )
    vintage_parser.set_defaults(theme="vintage")
    vintage_commands = vintage_parser.add_subparsers(
        dest="theme_command",
        required=True,
    )

    vintage_walk_parser = vintage_commands.add_parser(
        "walkframes",
        help="標準キャラ歩き絵を生成",
    )
    vintage_walk_parser.add_argument("names", nargs="*", choices=ALL_CHAR_STEMS)
    vintage_walk_parser.add_argument(
        "--dirs",
        nargs="+",
        default=["walk", "walkdown", "walkup"],
        help=(
            "生成方向（"
            + ", ".join(WALK_DIRECTIONS)
            + "。カンマ区切り・複数指定可）"
        ),
    )
    vintage_walk_parser.set_defaults(handler=_cmd_walkframes)

    vintage_custom_parser = vintage_commands.add_parser(
        "custom",
        help="customキャラ2枚を生成",
    )
    vintage_custom_parser.add_argument("slug")
    vintage_custom_parser.add_argument("label", nargs="+")
    vintage_custom_parser.add_argument(
        "--photo-ref",
        type=Path,
        help="人物写真reference（人物像の正本・参照の先頭に渡す）",
    )
    vintage_custom_parser.set_defaults(handler=_cmd_custom)

    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        return args.handler(args)
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"✗ {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
