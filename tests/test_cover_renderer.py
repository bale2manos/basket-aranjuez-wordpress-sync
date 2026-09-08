from __future__ import annotations

from datetime import date
from pathlib import Path
import site
import sys
from tempfile import TemporaryDirectory
import logging
import unittest

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
USER_SITE = Path(site.getusersitepackages())
for candidate in (ROOT, SRC, USER_SITE):
    candidate_str = str(candidate)
    if candidate_str not in sys.path:
        sys.path.insert(0, candidate_str)

from PIL import Image, ImageDraw

from config import AppConfig
from generador_horarios.cover_renderer import (
    MONTH_GAP,
    MONTH_VERTICAL_SHIFT,
    SEPARATOR_BASELINE_SHIFT,
    build_cover_date_segments,
    render_cover,
)


def create_cover_example(path: Path) -> None:
    image = Image.new("RGBA", (1080, 1350), (253, 207, 33, 255))
    draw = ImageDraw.Draw(image)
    draw.rectangle((0, 513, 1080, 836), fill=(6, 5, 3, 255))
    image.save(path)


class CoverRendererTests(unittest.TestCase):
    def test_build_cover_date_segments_same_month(self) -> None:
        segments = build_cover_date_segments([date(2026, 3, 21), date(2026, 3, 22)])
        self.assertEqual([segment.text for segment in segments], ["21", " y ", "22", "MARZO"])
        self.assertEqual([segment.style for segment in segments], ["outline", "outline", "outline", "solid"])
        self.assertEqual(segments[1].baseline_shift, SEPARATOR_BASELINE_SHIFT)
        self.assertEqual(segments[2].gap_after, MONTH_GAP)
        self.assertEqual(segments[3].baseline_shift, MONTH_VERTICAL_SHIFT)

    def test_build_cover_date_segments_multiple_months(self) -> None:
        segments = build_cover_date_segments([date(2026, 3, 30), date(2026, 4, 1)])
        self.assertEqual([segment.text for segment in segments], ["30", "MARZO", " y ", "1", "ABRIL"])
        self.assertEqual([segment.style for segment in segments], ["outline", "solid", "outline", "outline", "solid"])
        self.assertEqual(segments[0].gap_after, MONTH_GAP)
        self.assertEqual(segments[1].baseline_shift, MONTH_VERTICAL_SHIFT)
        self.assertEqual(segments[2].baseline_shift, SEPARATOR_BASELINE_SHIFT)
        self.assertEqual(segments[4].baseline_shift, MONTH_VERTICAL_SHIFT)

    def test_render_cover_returns_none_when_italic_font_is_missing(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            base = Path(tmp_dir)
            output_dir = base / "output"
            output_dir.mkdir()
            example_path = base / "cover.png"
            basket_logo_path = base / "basket.png"
            clupik_logo_path = base / "clupik.png"
            create_cover_example(example_path)
            Image.new("RGBA", (200, 200), color=(255, 200, 0, 255)).save(basket_logo_path)
            Image.new("RGBA", (240, 100), color=(20, 20, 20, 255)).save(clupik_logo_path)

            logger = logging.getLogger("cover_test_missing_font")
            logger.handlers.clear()
            logger.addHandler(logging.NullHandler())

            config = AppConfig(
                excel_path=base / "dummy.xlsx",
                background_image_path=base / "bg.png",
                header_overlay_path=base / "header.png",
                logos_dir=base,
                font_path=ROOT / "Poppins-Bold.ttf",
                output_root_dir=base,
                basket_logo_path=basket_logo_path,
                club_logo_map_path=base / "club.csv",
                venue_map_path=base / "venue.csv",
                cover_example_path=example_path,
                cover_font_bold_path=ROOT / "fonts" / "Poppins-Bold.ttf",
                cover_font_italic_path=base / "missing.ttf",
                clupik_logo_path=clupik_logo_path,
                cover_basket_logo_path=base / "cover_ba_logo.png",
            )

            result = render_cover(output_dir, config, "horarios", [date(2026, 3, 21)], logger)
            self.assertIsNone(result)
            self.assertFalse((output_dir / "portada.png").exists())

    def test_render_cover_keeps_date_inside_band_and_native_size(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            base = Path(tmp_dir)
            output_dir = base / "output"
            output_dir.mkdir()
            example_path = base / "cover.png"
            basket_logo_path = base / "basket.png"
            clupik_logo_path = base / "clupik.png"
            cover_overlay_path = base / "cover_ba_logo.png"
            create_cover_example(example_path)
            Image.new("RGBA", (200, 200), color=(255, 200, 0, 255)).save(basket_logo_path)
            Image.new("RGBA", (240, 100), color=(20, 20, 20, 255)).save(clupik_logo_path)
            Image.new("RGBA", (1080, 1350), color=(0, 0, 0, 0)).save(cover_overlay_path)

            logger = logging.getLogger("cover_test_layout")
            logger.handlers.clear()
            logger.addHandler(logging.NullHandler())

            config = AppConfig(
                excel_path=base / "dummy.xlsx",
                background_image_path=base / "bg.png",
                header_overlay_path=base / "header.png",
                logos_dir=base,
                font_path=ROOT / "Poppins-Bold.ttf",
                output_root_dir=base,
                basket_logo_path=basket_logo_path,
                club_logo_map_path=base / "club.csv",
                venue_map_path=base / "venue.csv",
                cover_example_path=example_path,
                cover_font_bold_path=ROOT / "fonts" / "Poppins-Bold.ttf",
                cover_font_italic_path=ROOT / "fonts" / "Poppins-BoldItalic.ttf",
                clupik_logo_path=clupik_logo_path,
                cover_basket_logo_path=cover_overlay_path,
            )

            result = render_cover(output_dir, config, "horarios", [date(2026, 3, 21), date(2026, 3, 22)], logger)
            self.assertIsNotNone(result)

            image = Image.open(result).convert("RGBA")
            self.assertEqual(image.size, (1080, 1350))

            background_rgb = (253, 207, 33)
            center_strip_has_only_background = True
            for y in range(846, 900):
                for x in range(140, 940):
                    if image.getpixel((x, y))[:3] != background_rgb:
                        center_strip_has_only_background = False
                        break
                if not center_strip_has_only_background:
                    break
            self.assertTrue(center_strip_has_only_background)


if __name__ == "__main__":
    unittest.main()
