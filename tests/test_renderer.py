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

from generador_horarios.models import MatchRecord
from generador_horarios.renderer import (
    TRANSLUCENT_YELLOW,
    _draw_rounded_fill,
    _parse_result_scores,
    _resolve_team_box_fills,
)
from generador_horarios.layout import YELLOW


class RendererTests(unittest.TestCase):
    def test_translucent_fill_is_composited_over_background(self) -> None:
        canvas = Image.new("RGBA", (40, 40), (10, 20, 30, 255))
        draw = ImageDraw.Draw(canvas)
        _draw_rounded_fill(canvas, draw, (5, 5, 35, 35), radius=6, fill=TRANSLUCENT_YELLOW)
        pixel = canvas.getpixel((20, 20))
        self.assertEqual(pixel[3], 255)
        self.assertNotEqual(pixel[:3], (247, 207, 35))
        self.assertNotEqual(pixel[:3], (10, 20, 30))

    def test_translucent_yellow_uses_35_percent_opacity(self) -> None:
        self.assertEqual(TRANSLUCENT_YELLOW[3], 89)

    def test_parse_result_scores_accepts_spaced_score(self) -> None:
        self.assertEqual(_parse_result_scores("55 - 37"), (55, 37))

    def test_resolve_team_box_fills_tints_losing_local_team(self) -> None:
        match = MatchRecord(
            row_index=1,
            categoria="Junior Masc",
            local_original="BASKET ARANJUEZ NEGRO",
            visitante_original="RIVAL",
            fecha=None,
            hora=None,
            campo_original="Pabellon",
            render_mode="resultados",
            result_text="45-90",
            middle_text="45-90",
        )
        left_fill, right_fill = _resolve_team_box_fills(match)
        self.assertEqual(left_fill, TRANSLUCENT_YELLOW)
        self.assertEqual(right_fill, YELLOW)

    def test_resolve_team_box_fills_tints_losing_visitor_team(self) -> None:
        match = MatchRecord(
            row_index=1,
            categoria="Junior Masc",
            local_original="BASKET ARANJUEZ NEGRO",
            visitante_original="RIVAL",
            fecha=None,
            hora=None,
            campo_original="Pabellon",
            render_mode="resultados",
            result_text="90-45",
            middle_text="90-45",
        )
        left_fill, right_fill = _resolve_team_box_fills(match)
        self.assertEqual(left_fill, YELLOW)
        self.assertEqual(right_fill, TRANSLUCENT_YELLOW)


if __name__ == "__main__":
    unittest.main()
