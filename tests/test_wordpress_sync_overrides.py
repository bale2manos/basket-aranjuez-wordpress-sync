from pathlib import Path
import json
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

from basket_wordpress_sync.enrichment import enrich_chronicles_with_result_overrides
from basket_wordpress_sync.models import ParsedChronicle, TeamConfig
from basket_wordpress_sync.normalizer.text import smart_title


class WordPressSyncOverridesTests(unittest.TestCase):
    def _create_docx(self, paragraphs: list[str]) -> Path:
        tmp_dir = TemporaryDirectory()
        self.addCleanup(tmp_dir.cleanup)
        path = Path(tmp_dir.name) / "Crónicas Basket Aranjuez 21-22 de marzo.docx"
        document = Document()
        for paragraph in paragraphs:
            document.add_paragraph(paragraph)
        document.save(path)
        return path

    def _create_overrides(self, payload: list[dict[str, object]]) -> Path:
        tmp_dir = TemporaryDirectory()
        self.addCleanup(tmp_dir.cleanup)
        path = Path(tmp_dir.name) / "result_overrides.json"
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return path

    def test_smart_title_keeps_single_letter_suffixes_uppercase(self) -> None:
        self.assertEqual(smart_title("senior masculino b"), "Senior Masculino B")
        self.assertEqual(smart_title("sub 22 masculino a"), "Sub 22 Masculino A")

    def test_enrichment_uses_result_overrides_for_missing_result(self) -> None:
        docx_path = self._create_docx(
            [
                "CRÓNICAS BASKET ARANJUEZ",
                "21-22 de marzo de 2026",
                "JUNIOR PREFERENTE MASCULINO PRIMER",
                "Buen trabajo pese a la derrota.",
            ]
        )
        overrides_path = self._create_overrides(
            [
                {
                    "match_key": "2026-03-21__JUNIOR MASC PREF__BASKET ARANJUEZ A__LICEO FRANCES 08",
                    "fecha": "2026-03-21",
                    "categoria": "Junior Masc. Pref.",
                    "local_original": "BASKET ARANJUEZ A",
                    "visitante_original": "LICEO FRANCES 08",
                    "result": "59-79",
                },
                {
                    "match_key": "2026-03-22__2A DIV AUT MASC PLATA__BASKET ARANJUEZ__FGS SPORTS C D E",
                    "fecha": "2026-03-22",
                    "categoria": "2ª Div Aut Masc PLATA",
                    "local_original": "BASKET ARANJUEZ",
                    "visitante_original": "FGS SPORTS C.D.E.",
                    "result": "76-64",
                }
            ]
        )
        chronicle = ParsedChronicle(
            raw_heading="JUNIOR PREFERENTE MASCULINO PRIMER",
            normalized_heading="JUNIOR PREFERENTE MASCULINO PRIMER",
            section="junior",
            competition_lines=[],
            result_raw=None,
            home_team=None,
            away_team=None,
            home_score=None,
            away_score=None,
            basket_is_home=None,
            basket_score=None,
            rival_team=None,
            rival_score=None,
            partials=[],
            players_text=None,
            body_paragraphs=["Buen trabajo pese a la derrota."],
            is_complete=False,
            warnings=["missing_result", "parse_warning:incomplete_block"],
            source_paragraphs=[],
            order_index=1,
        )
        teams = [
            TeamConfig(
                section="junior",
                web_block_name="Junior Masculino Blanco",
                heading_aliases=["JUNIOR PREFERENTE MASCULINO PRIMER"],
                competition_aliases=["GRUPO 2"],
                result_aliases=["BASKET ARANJUEZ"],
                expected_order=1,
            )
        ]

        applied = enrich_chronicles_with_result_overrides(
            docx_path=docx_path,
            chronicles=[chronicle],
            teams=teams,
            overrides_path=overrides_path,
        )

        self.assertEqual(applied, 1)
        self.assertEqual(chronicle.home_team, "BASKET ARANJUEZ A")
        self.assertEqual(chronicle.away_team, "LICEO FRANCES 08")
        self.assertEqual((chronicle.home_score, chronicle.away_score), (59, 79))
        self.assertTrue(chronicle.is_complete)
        self.assertIn("result_enriched_from_overrides", chronicle.warnings)
        self.assertNotIn("missing_result", chronicle.warnings)

    def test_enrichment_handles_split_infantil_sections(self) -> None:
        docx_path = self._create_docx(
            [
                "CRÓNICAS BASKET ARANJUEZ",
                "11-12 de abril de 2026",
                "INFANTIL PREFERENTE FEMENINO PRIMER",
                "A DIVISIÓN",
                "GRUPO 4",
                "Partido complicado.",
            ]
        )
        overrides_path = self._create_overrides(
            [
                {
                    "match_key": "2026-04-11__INFANTIL FEM PREF__BALONCESTO RIVAS SURESTE B__BASKET ARANJUEZ NEGRO",
                    "fecha": "2026-04-11",
                    "categoria": "Infantil Fem. Pref.",
                    "local_original": "BALONCESTO RIVAS SURESTE B",
                    "visitante_original": "BASKET ARANJUEZ NEGRO",
                    "result": "74-33",
                }
            ]
        )
        chronicle = ParsedChronicle(
            raw_heading="INFANTIL PREFERENTE FEMENINO PRIMER",
            normalized_heading="INFANTIL PREFERENTE FEMENINO PRIMER",
            section="infantil",
            competition_lines=["A DIVISIÓN", "GRUPO 4"],
            result_raw=None,
            home_team=None,
            away_team=None,
            home_score=None,
            away_score=None,
            basket_is_home=None,
            basket_score=None,
            rival_team=None,
            rival_score=None,
            partials=[],
            players_text=None,
            body_paragraphs=["Partido complicado."],
            is_complete=False,
            warnings=["missing_result", "parse_warning:incomplete_block"],
            source_paragraphs=[],
            order_index=1,
        )
        teams = [
            TeamConfig(
                section="infantil_femenino",
                web_block_name="Infantil Femenino 2º año Negro",
                heading_aliases=["INFANTIL PREFERENTE FEMENINO PRIMER"],
                competition_aliases=["GRUPO 4"],
                result_aliases=["BASKET ARANJUEZ NEGRO"],
                expected_order=1,
            )
        ]

        applied = enrich_chronicles_with_result_overrides(
            docx_path=docx_path,
            chronicles=[chronicle],
            teams=teams,
            overrides_path=overrides_path,
        )

        self.assertEqual(applied, 1)
        self.assertEqual((chronicle.home_score, chronicle.away_score), (74, 33))
        self.assertEqual(chronicle.away_team, "BASKET ARANJUEZ NEGRO")


if __name__ == "__main__":
    unittest.main()
