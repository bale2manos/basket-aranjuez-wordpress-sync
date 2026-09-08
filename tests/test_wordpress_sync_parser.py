from pathlib import Path
import site
import sys
from tempfile import TemporaryDirectory
import unittest

from docx import Document

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
USER_SITE = Path(site.getusersitepackages())
for candidate in (ROOT, SRC, USER_SITE):
    candidate_str = str(candidate)
    if candidate_str not in sys.path:
        sys.path.insert(0, candidate_str)

from basket_wordpress_sync.parser import ChronicleParser


class WordPressSyncParserTests(unittest.TestCase):
    def _create_docx(self, paragraphs: list[str]) -> Path:
        tmp_dir = TemporaryDirectory()
        self.addCleanup(tmp_dir.cleanup)
        path = Path(tmp_dir.name) / "chronicles.docx"
        document = Document()
        for paragraph in paragraphs:
            document.add_paragraph(paragraph)
        document.save(path)
        return path

    def test_parser_handles_merged_heading_and_competition(self) -> None:
        path = self._create_docx(
            [
                "CRÓNICAS BASKET ARANJUEZ",
                "21-22 de marzo de 2026",
                "SENIOR FEMENINO ASEGUNDA AUTONÓMICA ORO, GRUPO 1",
                "BASKET ARANJUEZ 63 - 52 CABRINI A.P.A.",
                "Parciales: 20-11, 8-12, 19-14, 16-15",
                "Jugadoras: María Contreras 2, Lucía Casado 14",
                "Buen comienzo de partido.",
            ]
        )

        parser = ChronicleParser()
        chronicles = parser.parse_docx(path)

        self.assertEqual(len(chronicles), 1)
        chronicle = chronicles[0]
        self.assertEqual(chronicle.raw_heading, "SENIOR FEMENINO A")
        self.assertEqual(chronicle.section, "senior")
        self.assertEqual(chronicle.competition_lines, ["SEGUNDA AUTONÓMICA ORO", "GRUPO 1"])
        self.assertEqual(chronicle.home_team, "BASKET ARANJUEZ")
        self.assertEqual(chronicle.away_team, "CABRINI A.P.A")
        self.assertEqual(len(chronicle.partials), 4)
        self.assertTrue(chronicle.is_complete)

    def test_parser_handles_six_partials(self) -> None:
        path = self._create_docx(
            [
                "ALEVÍN MASCULINO SEGUNDO AÑO LIGA MARCO ALDANY PLATA GRUPO 10",
                "BASKET ARANJUEZ CLÍNICA RICHER 58 - 8 JANSEN BT BLANCO",
                "PARCIALES: 25-1, 8-5, 19-2, 6-0 , - , -",
                "JUGADORES: Diego García, Pablo Olmeda",
                "Gran primer sexto de partido.",
            ]
        )

        parser = ChronicleParser()
        chronicles = parser.parse_docx(path)

        self.assertEqual(len(chronicles), 1)
        partials = chronicles[0].partials
        self.assertEqual(len(partials), 6)
        self.assertEqual([partial.label for partial in partials], ["1P", "2P", "3P", "4P", "5P", "6P"])
        self.assertIsNone(partials[4].home_score)
        self.assertIsNone(partials[5].away_score)

    def test_parser_marks_incomplete_blocks(self) -> None:
        path = self._create_docx(
            [
                "ALEVÍN FEMENINO PRIMER AÑOGRUPO 4",
                "Jugadoras: Naiara, Mia",
                "Partido suspendido.",
            ]
        )

        parser = ChronicleParser()
        chronicles = parser.parse_docx(path)

        self.assertEqual(len(chronicles), 1)
        chronicle = chronicles[0]
        self.assertFalse(chronicle.is_complete)
        self.assertIn("missing_result", chronicle.warnings)

    def test_parser_splits_multiple_matches_for_same_heading(self) -> None:
        path = self._create_docx(
            [
                "INFANTIL PREFERENTE FEMENINO",
                "PRIMERA DIVISIÓN, GRUPO 2",
                "BASKET ARANJUEZ ESENCIAL SALUD 89 - 39 VALCUDE ALCOBENDAS",
                "PARCIALES: 25-18, 16-4, 22-9, 26-8",
                "JUGADORAS: Noa, Marian",
                "Primera crónica.",
                "SAN JOSÉ DEL PARQUE 4 - 55 BASKET ARANJUEZ ESENCIAL SALUD",
                "PARCIALES: 23-2, 21-0, 2-7, 4-0",
                "JUGADORAS: Noa, Marian",
                "Segunda crónica.",
            ]
        )

        parser = ChronicleParser()
        chronicles = parser.parse_docx(path)

        self.assertEqual(len(chronicles), 2)
        self.assertEqual(chronicles[0].raw_heading, chronicles[1].raw_heading)
        self.assertEqual(chronicles[0].competition_lines, ["PRIMERA DIVISIÓN", "GRUPO 2"])
        self.assertEqual(chronicles[0].result_raw, "BASKET ARANJUEZ ESENCIAL SALUD 89 - 39 VALCUDE ALCOBENDAS")
        self.assertEqual(chronicles[1].result_raw, "SAN JOSÉ DEL PARQUE 4 - 55 BASKET ARANJUEZ ESENCIAL SALUD")
        self.assertEqual(chronicles[0].body_paragraphs, ["Primera crónica."])
        self.assertEqual(chronicles[1].body_paragraphs, ["Segunda crónica."])
        self.assertEqual(chronicles[0].order_index, 1)
        self.assertEqual(chronicles[1].order_index, 2)

    def test_parser_does_not_treat_score_mentions_inside_body_as_new_match(self) -> None:
        path = self._create_docx(
            [
                "INFANTIL MASCULINO",
                "LIGA AHORRAMAS PLATA/BRONCE FASE DESCENSO",
                "BASKET ARANJUEZ BLANCO 87-37 ADC BOADILLA A",
                "PARCIALES: 20-10, 18-9, 25-8, 24-10",
                "JUGADORES: Uno, Dos",
                "Partido muy completo que arrancamos con un 9-0 y buenas sensaciones tanto en ataque como en defensa.",
            ]
        )

        parser = ChronicleParser()
        chronicles = parser.parse_docx(path)

        self.assertEqual(len(chronicles), 1)
        self.assertEqual(
            chronicles[0].body_paragraphs,
            ["Partido muy completo que arrancamos con un 9-0 y buenas sensaciones tanto en ataque como en defensa."],
        )

    def test_parser_handles_titlecase_result_lines_and_hyphenated_team_names(self) -> None:
        path = self._create_docx(
            [
                "ALEVÍN FEMENINO SEGUNDO AÑO",
                "CLASIFICACIÓN PUESTOS DEL 61 AL 66",
                "Santa María del Pilar 43 - 51 BASKET ARANJUEZ.",
                "PARCIALES: 10-12, 11-14, 12-13, 10-12",
                "Partido muy serio.",
                "ALEVIN MASCULINO PRIMER AÑO",
                "CLASIFICACIÓN PUESTOS DEL 55 AL 60",
                "INNOVA-TSN LEGANÉS 37 - 79 BASKET ARANJUEZ",
                "PARCIALES: 8-20, 10-21, 9-18, 10-20",
                "Gran partido colectivo.",
            ]
        )

        parser = ChronicleParser()
        chronicles = parser.parse_docx(path)

        self.assertEqual(len(chronicles), 2)
        self.assertEqual(chronicles[0].result_raw, "Santa María del Pilar 43 - 51 BASKET ARANJUEZ.")
        self.assertEqual(chronicles[0].home_team, "Santa María del Pilar")
        self.assertEqual(chronicles[0].away_team, "BASKET ARANJUEZ")
        self.assertEqual(chronicles[1].home_team, "INNOVA-TSN LEGANÉS")
        self.assertEqual(chronicles[1].away_team, "BASKET ARANJUEZ")

    def test_parser_keeps_primera_division_without_truncating_heading(self) -> None:
        path = self._create_docx(
            [
                "CADETE PREFERENTE FEMENINO PRIMERA DIVISIÓN, GRUPO 4",
                "BASKET ARANJUEZ POSTAS TAPERIA 67-56 ARGANZUELA CENTRO",
                "PARCIALES: 18-15, 22-13, 13-15, 14-13",
                "Crónica del partido.",
            ]
        )

        parser = ChronicleParser()
        chronicles = parser.parse_docx(path)

        self.assertEqual(len(chronicles), 1)
        chronicle = chronicles[0]
        self.assertEqual(chronicle.raw_heading, "CADETE PREFERENTE FEMENINO PRIMERA")
        self.assertEqual(chronicle.competition_lines, ["DIVISIÓN", "GRUPO 4"])


if __name__ == "__main__":
    unittest.main()
