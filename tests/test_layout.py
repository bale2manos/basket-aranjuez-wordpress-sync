from pathlib import Path
import site
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
USER_SITE = Path(site.getusersitepackages())
for candidate in (ROOT, SRC, USER_SITE):
    candidate_str = str(candidate)
    if candidate_str not in sys.path:
        sys.path.insert(0, candidate_str)

from PIL import Image, ImageDraw

from generador_horarios.layout import CANVAS_SIZE, fit_text_block, get_match_slots


FONT_PATH = ROOT / "Poppins-Bold.ttf"


class LayoutTests(unittest.TestCase):
    def test_fit_text_block_caps_lines(self) -> None:
        canvas = Image.new("RGB", (600, 200), "white")
        draw = ImageDraw.Draw(canvas)
        fitted = fit_text_block(
            draw,
            "Colegio Miramadrid Paracuellos B con un nombre especialmente largo para forzar el ajuste",
            FONT_PATH,
            max_width=240,
            max_height=72,
            base_size=28,
            min_size=18,
            max_lines=3,
        )
        self.assertLessEqual(len(fitted.lines), 3)
        self.assertLessEqual(fitted.line_height * len(fitted.lines), 72)

    def test_match_slots_stay_inside_canvas(self) -> None:
        slots = get_match_slots(8)
        self.assertEqual(len(slots), 8)
        for slot in slots:
            for box in (
                slot.left_logo_box,
                slot.right_logo_box,
                slot.left_team_box,
                slot.vs_box,
                slot.right_team_box,
                slot.info_box,
            ):
                self.assertGreaterEqual(box[0], 0)
                self.assertGreaterEqual(box[1], 0)
                self.assertLessEqual(box[2], CANVAS_SIZE[0])
                self.assertLessEqual(box[3], CANVAS_SIZE[1])


if __name__ == "__main__":
    unittest.main()
