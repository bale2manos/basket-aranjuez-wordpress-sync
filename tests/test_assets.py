from __future__ import annotations

from pathlib import Path
import site
import sys
from tempfile import TemporaryDirectory
import unittest

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
USER_SITE = Path(site.getusersitepackages())
for candidate in (ROOT, SRC, USER_SITE):
    candidate_str = str(candidate)
    if candidate_str not in sys.path:
        sys.path.insert(0, candidate_str)

from generador_horarios.assets import infer_logo_path


class AssetsTests(unittest.TestCase):
    def test_infer_logo_path_from_partial_filename(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            logos_dir = Path(tmp_dir)
            (logos_dir / "baloncesto_daganzo.png").write_bytes(b"png")
            (logos_dir / "adc_boadilla.png").write_bytes(b"png")

            inferred = infer_logo_path("BALONCESTO DAGANZO ISUZU REVEIN", logos_dir)
            self.assertIsNotNone(inferred)
            self.assertEqual(inferred.name, "baloncesto_daganzo.png")

            inferred = infer_logo_path("ADC BOADILLA", logos_dir)
            self.assertIsNotNone(inferred)
            self.assertEqual(inferred.name, "adc_boadilla.png")

    def test_infer_logo_path_from_token_subset(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            logos_dir = Path(tmp_dir)
            (logos_dir / "colegio_las_tablas.png").write_bytes(b"png")

            inferred = infer_logo_path('COLEGIO ESTUDIANTES LAS TABLAS "A"', logos_dir)
            self.assertIsNotNone(inferred)
            self.assertEqual(inferred.name, 'colegio_las_tablas.png')


if __name__ == "__main__":
    unittest.main()
