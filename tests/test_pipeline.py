from __future__ import annotations

from pathlib import Path
import site
import sys
from tempfile import TemporaryDirectory
import json
import logging
import unittest
import zipfile
from xml.sax.saxutils import escape

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
USER_SITE = Path(site.getusersitepackages())
for candidate in (ROOT, SRC, USER_SITE):
    candidate_str = str(candidate)
    if candidate_str not in sys.path:
        sys.path.insert(0, candidate_str)

from openpyxl import Workbook
from openpyxl.styles import PatternFill
from PIL import Image, ImageDraw

from config import AppConfig
from generador_horarios.excel_reader import load_matches
from generador_horarios.pipeline import run_pipeline


def create_test_docx(path: Path, paragraphs: list[str]) -> None:
    body = []
    for paragraph in paragraphs:
        body.append(f"<w:p><w:r><w:t>{escape(paragraph)}</w:t></w:r></w:p>")
    xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        '<w:body>'
        + ''.join(body)
        + '</w:body></w:document>'
    )
    with zipfile.ZipFile(path, 'w') as archive:
        archive.writestr('word/document.xml', xml)


def create_cover_example(path: Path) -> None:
    image = Image.new("RGBA", (1080, 1350), (253, 207, 33, 255))
    draw = ImageDraw.Draw(image)
    draw.rectangle((0, 513, 1080, 836), fill=(6, 5, 3, 255))
    image.save(path)


class PipelineTests(unittest.TestCase):
    def test_load_matches_detects_playoff_highlight(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            base = Path(tmp_dir)
            workbook_path = base / "partidos.xlsx"

            workbook = Workbook()
            worksheet = workbook.active
            worksheet.append(["Categor\u00eda", "Local", "Visitante", "Fecha", "Hora", "Campo de juego"])
            worksheet.append([
                "Junior Fem. Pref.",
                "BASKET ARANJUEZ NEGRO",
                "Rival",
                46095,
                0.5,
                "LAS OLIVAS, CIUDAD DEPORTIVA",
            ])
            playoff_fill = PatternFill(fill_type="solid", fgColor="FFFFFF00")
            for column in "ABCDEF":
                worksheet[f"{column}2"].fill = playoff_fill
            workbook.save(workbook_path)

            logger = logging.getLogger("test_load_matches_playoff")
            logger.handlers.clear()
            logger.addHandler(logging.NullHandler())

            records = load_matches(workbook_path, logger)
            self.assertEqual(len(records), 1)
            self.assertTrue(records[0].is_playoff)

    def test_load_matches_skips_missing_required_data(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            base = Path(tmp_dir)
            workbook_path = base / "partidos.xlsx"

            workbook = Workbook()
            worksheet = workbook.active
            worksheet.append(["Categor\u00eda", "Local", "Visitante", "Fecha", "Hora", "Campo de juego"])
            worksheet.append(["Junior Fem. Pref.", "", "Rival", 46095, 0.5, "LAS OLIVAS, CIUDAD DEPORTIVA"])
            workbook.save(workbook_path)

            logger = logging.getLogger("test_load_matches")
            logger.handlers.clear()
            logger.addHandler(logging.NullHandler())

            records = load_matches(workbook_path, logger)
            self.assertEqual(len(records), 1)
            self.assertEqual(records[0].status, "skipped")
            self.assertIn("Local", records[0].skip_reason)

    def test_pipeline_renders_horarios_and_bootstraps_lookup_files(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            base = Path(tmp_dir)
            excel_path = base / "partidos.xlsx"
            background_path = base / "background.png"
            header_path = base / "header.png"
            cover_example_path = base / "cover.png"
            logos_dir = base / "logos"
            output_dir = base / "output"
            data_dir = base / "data"
            font_path = ROOT / "Poppins-Bold.ttf"
            cover_font_bold_path = ROOT / "fonts" / "Poppins-Bold.ttf"
            cover_font_italic_path = ROOT / "fonts" / "Poppins-BoldItalic.ttf"
            basket_logo_path = logos_dir / "basket_aranjuez.png"
            clupik_logo_path = logos_dir / "Clupik-logo.png"
            club_map_path = data_dir / "club_logo_map.csv"
            venue_map_path = data_dir / "venue_map.csv"

            logos_dir.mkdir(parents=True, exist_ok=True)
            output_dir.mkdir(parents=True, exist_ok=True)
            data_dir.mkdir(parents=True, exist_ok=True)

            workbook = Workbook()
            worksheet = workbook.active
            worksheet.title = "Table"
            worksheet.append(["Categor\u00eda", "Local", "Visitante", "Fecha", "Hora", "Campo de juego"])
            worksheet.append([
                "Junior Fem. Pref.",
                "BASKET ARANJUEZ NEGRO",
                "LICEO FRANCES 08",
                46095,
                0.46875,
                "LICEO FRANCES, PABELLON COLEGIO",
            ])
            workbook.save(excel_path)

            Image.new("RGB", (1200, 800), color="#808080").save(background_path)
            Image.new("RGBA", (1080, 1350), color=(0, 0, 0, 0)).save(header_path)
            Image.new("RGBA", (220, 220), color=(255, 200, 0, 255)).save(basket_logo_path)
            Image.new("RGBA", (260, 120), color=(20, 20, 20, 255)).save(clupik_logo_path)
            create_cover_example(cover_example_path)

            config = AppConfig(
                excel_path=excel_path,
                background_image_path=background_path,
                header_overlay_path=header_path,
                logos_dir=logos_dir,
                font_path=font_path,
                output_root_dir=output_dir,
                basket_logo_path=basket_logo_path,
                club_logo_map_path=club_map_path,
                venue_map_path=venue_map_path,
                cover_example_path=cover_example_path,
                cover_font_bold_path=cover_font_bold_path,
                cover_font_italic_path=cover_font_italic_path,
                clupik_logo_path=clupik_logo_path,
                max_per_page=8,
            )

            result = run_pipeline(config)

            self.assertEqual(result.render_mode, "horarios")
            self.assertEqual(result.output_dir.name, "horarios")
            self.assertTrue((result.output_dir / "portada.png").exists())
            self.assertTrue((result.output_dir / "local1.png").exists())
            self.assertTrue((result.output_dir / "summary.json").exists())
            self.assertTrue(club_map_path.exists())
            self.assertTrue(venue_map_path.exists())
            self.assertEqual(result.rendered_count, 1)
            self.assertEqual(len(result.images), 2)
            self.assertEqual(result.images[0]["page_type"], "cover")

            summary = json.loads((result.output_dir / "summary.json").read_text(encoding="utf-8"))
            self.assertIn("rendered_missing_logo", json.dumps(summary, ensure_ascii=False))
            self.assertIn("Liceo Franc\u00e9s, Pabell\u00f3n", json.dumps(summary, ensure_ascii=False))
            self.assertEqual(summary["render_mode"], "horarios")
            self.assertEqual(summary["images"][0]["page_type"], "cover")
            self.assertEqual(summary["images"][0]["output_image"], "portada.png")

    def test_pipeline_uses_playoffs_middle_text_for_highlighted_rows_in_horarios(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            base = Path(tmp_dir)
            excel_path = base / "partidos.xlsx"
            background_path = base / "background.png"
            header_path = base / "header.png"
            cover_example_path = base / "cover.png"
            logos_dir = base / "logos"
            output_dir = base / "output"
            data_dir = base / "data"
            font_path = ROOT / "Poppins-Bold.ttf"
            cover_font_bold_path = ROOT / "fonts" / "Poppins-Bold.ttf"
            cover_font_italic_path = ROOT / "fonts" / "Poppins-BoldItalic.ttf"
            basket_logo_path = logos_dir / "basket_aranjuez.png"
            clupik_logo_path = logos_dir / "Clupik-logo.png"
            club_map_path = data_dir / "club_logo_map.csv"
            venue_map_path = data_dir / "venue_map.csv"

            logos_dir.mkdir(parents=True, exist_ok=True)
            output_dir.mkdir(parents=True, exist_ok=True)
            data_dir.mkdir(parents=True, exist_ok=True)

            workbook = Workbook()
            worksheet = workbook.active
            worksheet.title = "Table"
            worksheet.append(["Categor\u00eda", "Local", "Visitante", "Fecha", "Hora", "Campo de juego"])
            worksheet.append([
                "Junior Fem. Pref.",
                "BASKET ARANJUEZ NEGRO",
                "LICEO FRANCES 08",
                46095,
                0.46875,
                "LICEO FRANCES, PABELLON COLEGIO",
            ])
            playoff_fill = PatternFill(fill_type="solid", fgColor="FFFFFF00")
            for column in "ABCDEF":
                worksheet[f"{column}2"].fill = playoff_fill
            workbook.save(excel_path)

            Image.new("RGB", (1200, 800), color="#808080").save(background_path)
            Image.new("RGBA", (1080, 1350), color=(0, 0, 0, 0)).save(header_path)
            Image.new("RGBA", (220, 220), color=(255, 200, 0, 255)).save(basket_logo_path)
            Image.new("RGBA", (260, 120), color=(20, 20, 20, 255)).save(clupik_logo_path)
            create_cover_example(cover_example_path)

            config = AppConfig(
                excel_path=excel_path,
                background_image_path=background_path,
                header_overlay_path=header_path,
                logos_dir=logos_dir,
                font_path=font_path,
                output_root_dir=output_dir,
                basket_logo_path=basket_logo_path,
                club_logo_map_path=club_map_path,
                venue_map_path=venue_map_path,
                cover_example_path=cover_example_path,
                cover_font_bold_path=cover_font_bold_path,
                cover_font_italic_path=cover_font_italic_path,
                clupik_logo_path=clupik_logo_path,
                max_per_page=8,
            )

            result = run_pipeline(config)

            self.assertEqual(result.render_mode, "horarios")
            summary = json.loads((result.output_dir / "summary.json").read_text(encoding="utf-8"))
            self.assertTrue(summary["items"][0]["is_playoff"])
            self.assertEqual(summary["items"][0]["middle_text"], "PLAYOFFS")

    def test_pipeline_renders_resultados_from_docx(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            base = Path(tmp_dir)
            excel_path = base / "partidos.xlsx"
            results_doc_path = base / "results.docx"
            results_overrides_path = base / "result_overrides.json"
            background_path = base / "background.png"
            header_path = base / "header.png"
            cover_example_path = base / "cover.png"
            logos_dir = base / "logos"
            output_dir = base / "output"
            data_dir = base / "data"
            font_path = ROOT / "Poppins-Bold.ttf"
            cover_font_bold_path = ROOT / "fonts" / "Poppins-Bold.ttf"
            cover_font_italic_path = ROOT / "fonts" / "Poppins-BoldItalic.ttf"
            basket_logo_path = logos_dir / "basket_aranjuez.png"
            clupik_logo_path = logos_dir / "Clupik-logo.png"
            club_map_path = data_dir / "club_logo_map.csv"
            venue_map_path = data_dir / "venue_map.csv"

            logos_dir.mkdir(parents=True, exist_ok=True)
            output_dir.mkdir(parents=True, exist_ok=True)
            data_dir.mkdir(parents=True, exist_ok=True)

            workbook = Workbook()
            worksheet = workbook.active
            worksheet.title = "Table"
            worksheet.append(["Categor\u00eda", "Local", "Visitante", "Fecha", "Hora", "Campo de juego"])
            worksheet.append([
                "Junior Fem. Pref.",
                "BASKET ARANJUEZ NEGRO",
                "LICEO FRANCES 08",
                46095,
                0.46875,
                "LICEO FRANCES, PABELLON COLEGIO",
            ])
            workbook.save(excel_path)

            create_test_docx(
                results_doc_path,
                [
                    "JUNIOR PREFERENTE FEMENINO SEGUNDA DIVISI?N, GRUPO 5",
                    "BASKET ARANJUEZ NEGRO 37-55 LICEO FRANCES 08",
                    "Parciales: 15-15, 16-6, 15-6, 9-10",
                ],
            )

            Image.new("RGB", (1200, 800), color="#808080").save(background_path)
            Image.new("RGBA", (1080, 1350), color=(0, 0, 0, 0)).save(header_path)
            Image.new("RGBA", (220, 220), color=(255, 200, 0, 255)).save(basket_logo_path)
            Image.new("RGBA", (260, 120), color=(20, 20, 20, 255)).save(clupik_logo_path)
            create_cover_example(cover_example_path)

            config = AppConfig(
                excel_path=excel_path,
                background_image_path=background_path,
                header_overlay_path=header_path,
                logos_dir=logos_dir,
                font_path=font_path,
                output_root_dir=output_dir,
                basket_logo_path=basket_logo_path,
                club_logo_map_path=club_map_path,
                venue_map_path=venue_map_path,
                cover_example_path=cover_example_path,
                cover_font_bold_path=cover_font_bold_path,
                cover_font_italic_path=cover_font_italic_path,
                clupik_logo_path=clupik_logo_path,
                mode="resultados",
                results_doc_path=results_doc_path,
                results_overrides_path=results_overrides_path,
                max_per_page=8,
            )

            result = run_pipeline(config)

            self.assertEqual(result.render_mode, "resultados")
            self.assertEqual(result.output_dir.name, "resultados")
            self.assertTrue((result.output_dir / "portada.png").exists())
            self.assertTrue((result.output_dir / "local1.png").exists())
            self.assertTrue((result.output_dir / "summary.json").exists())
            self.assertEqual(len(result.images), 2)
            self.assertEqual(result.images[0]["page_type"], "cover")

            summary = json.loads((result.output_dir / "summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["render_mode"], "resultados")
            self.assertEqual(summary["images"][0]["page_type"], "cover")
            self.assertEqual(summary["items"][0]["result_text"], "37-55")
            self.assertEqual(summary["items"][0]["result_source"], "docx")
            self.assertEqual(summary["items"][0]["middle_text"], "37-55")

    def test_pipeline_writes_result_overrides_when_result_is_missing(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            base = Path(tmp_dir)
            excel_path = base / "partidos.xlsx"
            results_doc_path = base / "results.docx"
            results_overrides_path = base / "result_overrides.json"
            background_path = base / "background.png"
            header_path = base / "header.png"
            cover_example_path = base / "cover.png"
            logos_dir = base / "logos"
            output_dir = base / "output"
            data_dir = base / "data"
            font_path = ROOT / "Poppins-Bold.ttf"
            cover_font_bold_path = ROOT / "fonts" / "Poppins-Bold.ttf"
            cover_font_italic_path = ROOT / "fonts" / "Poppins-BoldItalic.ttf"
            basket_logo_path = logos_dir / "basket_aranjuez.png"
            clupik_logo_path = logos_dir / "Clupik-logo.png"
            club_map_path = data_dir / "club_logo_map.csv"
            venue_map_path = data_dir / "venue_map.csv"

            logos_dir.mkdir(parents=True, exist_ok=True)
            output_dir.mkdir(parents=True, exist_ok=True)
            data_dir.mkdir(parents=True, exist_ok=True)

            workbook = Workbook()
            worksheet = workbook.active
            worksheet.title = "Table"
            worksheet.append(["Categor\u00eda", "Local", "Visitante", "Fecha", "Hora", "Campo de juego"])
            worksheet.append([
                "Junior Fem. Pref.",
                "BASKET ARANJUEZ NEGRO",
                "LICEO FRANCES 08",
                46095,
                0.46875,
                "LICEO FRANCES, PABELLON COLEGIO",
            ])
            workbook.save(excel_path)

            create_test_docx(
                results_doc_path,
                [
                    "JUNIOR PREFERENTE FEMENINO SEGUNDA DIVISI?N, GRUPO 5",
                    "Otro partido 10-20 Rival",
                ],
            )

            Image.new("RGB", (1200, 800), color="#808080").save(background_path)
            Image.new("RGBA", (1080, 1350), color=(0, 0, 0, 0)).save(header_path)
            Image.new("RGBA", (220, 220), color=(255, 200, 0, 255)).save(basket_logo_path)
            Image.new("RGBA", (260, 120), color=(20, 20, 20, 255)).save(clupik_logo_path)
            create_cover_example(cover_example_path)

            config = AppConfig(
                excel_path=excel_path,
                background_image_path=background_path,
                header_overlay_path=header_path,
                logos_dir=logos_dir,
                font_path=font_path,
                output_root_dir=output_dir,
                basket_logo_path=basket_logo_path,
                club_logo_map_path=club_map_path,
                venue_map_path=venue_map_path,
                cover_example_path=cover_example_path,
                cover_font_bold_path=cover_font_bold_path,
                cover_font_italic_path=cover_font_italic_path,
                clupik_logo_path=clupik_logo_path,
                mode="resultados",
                results_doc_path=results_doc_path,
                results_overrides_path=results_overrides_path,
                max_per_page=8,
            )

            result = run_pipeline(config)

            self.assertEqual(len(result.images), 1)
            self.assertTrue((result.output_dir / "portada.png").exists())
            self.assertTrue(results_overrides_path.exists())
            overrides = json.loads(results_overrides_path.read_text(encoding="utf-8"))
            self.assertEqual(len(overrides), 1)
            self.assertIsNone(overrides[0]["result"])

            summary = json.loads((result.output_dir / "summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["images"][0]["page_type"], "cover")
            self.assertEqual(summary["items"][0]["result_source"], "missing")
            self.assertEqual(summary["items"][0]["status"], "skipped")


if __name__ == "__main__":
    unittest.main()
