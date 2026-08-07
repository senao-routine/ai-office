import io
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

from PIL import Image, ImageDraw


ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import theme_gen
import assets_gen
import style_proof


class ThemeGenFinalizeTests(unittest.TestCase):
    @staticmethod
    def _make_sprite(path):
        image = Image.new("RGBA", (200, 300), (255, 0, 255, 255))
        draw = ImageDraw.Draw(image)
        draw.rectangle((50, 45, 150, 255), fill=(25, 105, 195, 255))
        image.save(path, "PNG")

    def test_finalize_char_smooth(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            source = tmp / "source.png"
            output = tmp / "smooth.png"
            self._make_sprite(source)

            theme_gen._finalize_char(source, output, "smooth")

            with Image.open(output) as image:
                self.assertEqual(image.size, theme_gen.CHAR_CANVAS)
                self.assertEqual(image.mode, "RGBA")
                pixels = image.load()
                magenta = sum(
                    1
                    for y in range(image.height)
                    for x in range(image.width)
                    for r, g, b, a in (pixels[x, y],)
                    if a and r > 150 and b > 150 and g < 110
                )
                self.assertEqual(magenta, 0)

    def test_finalize_char_fatpixel(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            source = tmp / "source.png"
            output = tmp / "fatpixel.png"
            self._make_sprite(source)

            theme_gen._finalize_char(source, output, "fatpixel")

            with Image.open(output) as image:
                self.assertEqual(image.size, theme_gen.CHAR_CANVAS)

    def test_finalize_char_custom_pixel_quantization(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            source = tmp / "source.png"
            output = tmp / "rpg.png"
            self._make_sprite(source)

            theme_gen._finalize_char(
                source,
                output,
                "smooth",
                char_px=96,
                char_colors=32,
            )

            with Image.open(output) as image:
                self.assertEqual(image.size, theme_gen.CHAR_CANVAS)
                self.assertEqual(image.mode, "RGBA")

    def test_repad_is_idempotent(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "sprite.png"
            self._make_sprite(path)

            self.assertEqual(theme_gen._repad_files([path]), (1, 0))
            with Image.open(path) as image:
                self.assertEqual(image.size, theme_gen.CHAR_CANVAS)

            self.assertEqual(theme_gen._repad_files([path]), (0, 1))

    def test_finalize_furniture_uses_exact_transparent_canvas(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            source = tmp / "source.png"
            output = tmp / "furniture.png"
            self._make_sprite(source)

            theme_gen._finalize_furn(source, output, (280, 220))

            with Image.open(output) as image:
                rgba = image.convert("RGBA")
                self.assertEqual(rgba.size, (280, 220))
                corners = (
                    rgba.getpixel((0, 0)),
                    rgba.getpixel((279, 0)),
                    rgba.getpixel((0, 219)),
                    rgba.getpixel((279, 219)),
                )
                self.assertTrue(all(pixel[3] == 0 for pixel in corners))

    def test_finalize_bg_modern(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            source = tmp / "source.png"
            output = tmp / "background.png"
            image = Image.new("RGB", (240, 180), (220, 225, 235))
            draw = ImageDraw.Draw(image)
            draw.rectangle((30, 20, 210, 160), fill=(45, 95, 135))
            image.save(source, "PNG")

            theme_gen._finalize_bg(source, output, "modern")

            with Image.open(output) as finalized:
                self.assertEqual(finalized.size, (1536, 1024))
            self.assertLess(output.stat().st_size, 1_200_000)

    def test_themed_name(self):
        self.assertEqual(theme_gen.themed_name("blog", "retro"), "blog__retro.png")
        self.assertEqual(
            theme_gen.themed_name("blog", "retro", walk=True),
            "blog__retro_walk.png",
        )
        for direction in theme_gen.WALK_DIRECTIONS:
            self.assertEqual(
                theme_gen.themed_name("blog", "rpg", direction=direction),
                f"blog__rpg_{direction}.png",
            )

    def test_rpg_asset_specs_keep_52_required_files_and_add_optional_directions(self):
        specs = list(theme_gen._theme_asset_specs("rpg"))
        names = {name for name, *_ in specs}
        self.assertEqual(sum(1 for *_, optional in specs if not optional), 52)
        self.assertEqual(
            len(specs),
            52
            + len(theme_gen.CHAR_STEMS) * len(theme_gen.OPTIONAL_CHAR_SUFFIXES)
            + len(theme_gen.POOL_STEMS) * len(theme_gen.CHAR_REQUIRED_SUFFIXES),
        )
        self.assertEqual(
            sum(1 for *_, optional in specs if optional),
            len(theme_gen.CHAR_STEMS) * len(theme_gen.OPTIONAL_CHAR_SUFFIXES)
            + len(theme_gen.POOL_STEMS) * len(theme_gen.CHAR_REQUIRED_SUFFIXES),
        )
        self.assertIn("blog__rpg_walkdown.png", names)
        self.assertIn("blog__rpg_walkup.png", names)
        self.assertIn("blog__rpg_walk2.png", names)
        self.assertIn("blog__rpg_wave.png", names)
        self.assertIn("generic_f2__rpg.png", names)
        self.assertIn("generic_m5__rpg_walkup.png", names)

    def test_pool_stems_are_available_to_generation_commands(self):
        self.assertEqual(
            theme_gen.build_parser().parse_args(
                ["rpg", "chars", "generic_f2"]
            ).names,
            ["generic_f2"],
        )
        self.assertEqual(
            theme_gen.build_parser().parse_args(
                ["vintage", "walkframes", "generic_m5"]
            ).names,
            ["generic_m5"],
        )

    def test_walkframes_parser_accepts_second_frames_and_wave(self):
        args = theme_gen.build_parser().parse_args(
            [
                "rpg",
                "walkframes",
                "blog",
                "--dirs",
                "walk2",
                "walkdown2",
                "walkup2",
                "wave",
            ]
        )
        self.assertEqual(
            args.dirs,
            ["walk2", "walkdown2", "walkup2", "wave"],
        )

    def test_walkframes_second_frames_use_first_frame_reference(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            standing = tmp / "blog__rpg.png"
            walk = tmp / "blog__rpg_walk.png"
            walkdown = tmp / "blog__rpg_walkdown.png"
            for path in (standing, walk, walkdown):
                Image.new("RGBA", theme_gen.CHAR_CANVAS).save(path, "PNG")

            generated = []

            def capture(prompt, _outdir, refs):
                generated.append((prompt, refs))
                return tmp / "raw.png"

            args = theme_gen.build_parser().parse_args(
                [
                    "rpg",
                    "walkframes",
                    "blog",
                    "--dirs",
                    "walk2",
                    "walkdown2",
                    "wave",
                ]
            )
            with (
                patch.object(assets_gen, "ASSETS", tmp),
                patch.object(assets_gen, "_sync_repo"),
                patch.object(theme_gen, "_announce"),
                patch.object(theme_gen, "_raw_outdir", return_value=tmp),
                patch.object(theme_gen, "_one_generated", side_effect=capture),
                patch.object(theme_gen, "_finalize_char"),
            ):
                self.assertEqual(theme_gen._cmd_walkframes(args), 0)

            self.assertEqual(generated[0][1], (standing, walk))
            self.assertEqual(generated[1][1], (standing, walkdown))
            self.assertEqual(generated[2][1], (standing,))
            self.assertIn(theme_gen.OPPOSITE_STRIDE_PROMPT, generated[0][0])
            self.assertIn(theme_gen.OPPOSITE_STRIDE_PROMPT, generated[1][0])
            self.assertIn(theme_gen.WAVE_POSE_PROMPT, generated[2][0])
            self.assertIn("#FF00FF", generated[2][0])

    def test_repad_candidates_include_optional_character_suffixes(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            for suffix in theme_gen.OPTIONAL_CHAR_SUFFIXES:
                Image.new("RGBA", theme_gen.CHAR_CANVAS).save(
                    tmp / f"blog__rpg{suffix}.png",
                    "PNG",
                )

            self.assertEqual(
                {
                    path.name
                    for path in theme_gen._repad_candidates(tmp)
                },
                {
                    f"blog__rpg{suffix}.png"
                    for suffix in theme_gen.OPTIONAL_CHAR_SUFFIXES
                },
            )

    def test_optional_spec_missing_is_green_but_existing_size_is_checked(self):
        optional_spec = ("blog__rpg_wave.png", None, True, False, True)
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            with (
                patch.object(theme_gen, "ASSETS_REPO", tmp),
                patch.object(theme_gen, "THEMES", {"rpg": {}}),
                patch.object(theme_gen, "THEMES_READY", ["rpg"]),
                patch.object(
                    theme_gen,
                    "_theme_asset_specs",
                    side_effect=lambda _theme: iter((optional_spec,)),
                ),
                redirect_stdout(io.StringIO()),
                redirect_stderr(io.StringIO()),
            ):
                self.assertEqual(theme_gen.check_themes(), 0)

                Image.new("RGBA", (1, 1)).save(tmp / optional_spec[0], "PNG")
                self.assertEqual(theme_gen.check_themes(), 1)

    def test_ready_themes_pin(self):
        # R22f昇格後: rpgのみship済み＝▶3cが52ファイル全量を常時検査する
        self.assertEqual(theme_gen.THEMES_READY, ["rpg"])

    def test_rpg_uses_office_job_description(self):
        from assets_gen import JOBS, STYLE

        self.assertIn("rpg", theme_gen.JOB_DESC_THEMES)
        self.assertEqual(
            theme_gen._character_description("rpg", "works_hq", JOBS, STYLE),
            theme_gen.OFFICE_JOB_DESC["works_hq"],
        )

    # R79: PWAスプライト同梱は全廃（アバター=モノグラム）＝除外規則のテストも退役

    def test_style_proof_generates_png_from_vintage_assets(self):
        self.assertEqual(style_proof.main([]), 0)
        output = ROOT / "tests" / "artifacts" / "style_proof_vintage.png"
        self.assertTrue(output.is_file())
        with Image.open(output) as image:
            self.assertEqual(image.size, (1080, 720))

    def test_check_themes_with_no_ready_themes_does_not_exit(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(theme_gen, "ASSETS_REPO", Path(tmp)):
                with patch.object(theme_gen, "THEMES_READY", []):
                    with redirect_stdout(io.StringIO()):
                        result = theme_gen.check_themes()
        self.assertEqual(result, 0)


if __name__ == "__main__":
    unittest.main()
