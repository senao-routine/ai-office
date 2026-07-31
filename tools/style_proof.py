#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""テーマ別の背景・家具・キャラを一枚に合成する検品用proof。"""
import argparse
import textwrap
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from theme_gen import ASSETS_REPO, DESK_XS, DESK_YS, themed_name


ROOT = ASSETS_REPO.parent
PROOF_SIZE = (1080, 720)
DESK_DISPLAY = (100, 78)
MEET_DISPLAY_WIDTH = 180
SOFA_DISPLAY_WIDTH = 176
PLANT_DISPLAY_WIDTH = 36
CHAR_DISPLAY_HEIGHT = 76
DEFAULT_CHARS = ("generic_m", "works_hq", "blog")


def _font():
    try:
        return ImageFont.load_default()
    except OSError:
        return None


def _placeholder(size, label):
    """欠落アセットを検品可能なグレー矩形として返す。"""
    image = Image.new("RGBA", size, (116, 122, 130, 255))
    draw = ImageDraw.Draw(image)
    draw.rectangle((0, 0, size[0] - 1, size[1] - 1), outline=(225, 230, 235, 255), width=2)
    font = _font()
    lines = textwrap.wrap(label, width=max(8, size[0] // 7))
    line_height = 11
    y = max(3, (size[1] - line_height * len(lines)) // 2)
    for line in lines:
        draw.text((4, y), line, fill=(245, 245, 245, 255), font=font)
        y += line_height
    return image


def _load(path, fallback_size):
    """画像をRGBAで開き、欠落・破損時はplaceholderを返す。"""
    try:
        with Image.open(path) as opened:
            return opened.convert("RGBA")
    except (FileNotFoundError, OSError):
        return _placeholder(fallback_size, path.name)


def _resize_width(image, width):
    height = max(1, round(image.height * width / image.width))
    return image.resize((width, height), Image.Resampling.NEAREST)


def _resize_height(image, height):
    width = max(1, round(image.width * height / image.height))
    return image.resize((width, height), Image.Resampling.NEAREST)


def _paste(canvas, image, xy):
    canvas.paste(image, xy, image if image.mode == "RGBA" else None)


def _asset(theme, stem):
    return ASSETS_REPO / themed_name(stem, theme)


def _compose(theme, chars=None):
    """proof画像を合成して返す。chars=Noneは存在する既定キャラだけを使う。"""
    background_path = _asset(theme, "bg2")
    background = _load(background_path, PROOF_SIZE)
    canvas = background.resize(PROOF_SIZE, Image.Resampling.NEAREST)

    desk_path = _asset(theme, "deskset")
    desk = _load(desk_path, DESK_DISPLAY).resize(DESK_DISPLAY, Image.Resampling.NEAREST)
    for dy in DESK_YS:
        for dx in DESK_XS:
            _paste(canvas, desk, (dx, dy))

    meet_path = _asset(theme, "meetset")
    meet = _resize_width(_load(meet_path, (MEET_DISPLAY_WIDTH, 118)), MEET_DISPLAY_WIDTH)
    _paste(canvas, meet, (840, 240))

    sofa_path = _asset(theme, "sofaset")
    sofa = _resize_width(_load(sofa_path, (SOFA_DISPLAY_WIDTH, 115)), SOFA_DISPLAY_WIDTH)
    _paste(canvas, sofa, (70, 470))

    plant_path = _asset(theme, "plant")
    plant = _resize_width(_load(plant_path, (PLANT_DISPLAY_WIDTH, 50)), PLANT_DISPLAY_WIDTH)
    for xy in ((238, 158), (736, 158), (150, 238)):
        _paste(canvas, plant, xy)

    if chars is None:
        char_names = [name for name in DEFAULT_CHARS if _asset(theme, name).is_file()]
        placements = [
            (DESK_XS[0] + 12, DESK_YS[0] - CHAR_DISPLAY_HEIGHT),
            (420, 430),
            (700, 430),
        ]
    else:
        char_names = list(chars)
        placements = []
        x = 8
        for name in char_names:
            placements.append((x, PROOF_SIZE[1] - CHAR_DISPLAY_HEIGHT))
            x += 86

    for name, xy in zip(char_names, placements):
        path = _asset(theme, name)
        char = _resize_height(_load(path, (CHAR_DISPLAY_HEIGHT, CHAR_DISPLAY_HEIGHT)), CHAR_DISPLAY_HEIGHT)
        _paste(canvas, char, xy)
    return canvas


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("theme", nargs="?", default="vintage")
    parser.add_argument("--chars", nargs="*", default=None, metavar="STEM")
    args = parser.parse_args(argv)

    output = ROOT / "tests" / "artifacts" / f"style_proof_{args.theme}.png"
    output.parent.mkdir(parents=True, exist_ok=True)
    _compose(args.theme, args.chars).save(output, "PNG")
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
