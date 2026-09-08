from pathlib import Path
import re
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

from basket_wordpress_sync.mapper import TeamMapper
from basket_wordpress_sync.models import ParsedChronicle
from basket_wordpress_sync.normalizer.text import matches_target_section, normalize_lookup_key
from basket_wordpress_sync.parser import ChronicleParser
from basket_wordpress_sync.planner import DryRunPlanner
from basket_wordpress_sync.settings import load_settings


class WordPressSyncPlannerTests(unittest.TestCase):
    def _create_docx(self, paragraphs: list[str]) -> Path:
        tmp_dir = TemporaryDirectory()
        self.addCleanup(tmp_dir.cleanup)
        path = Path(tmp_dir.name) / "chronicles.docx"
        document = Document()
        for paragraph in paragraphs:
            document.add_paragraph(paragraph)
        document.save(path)
        return path

    def test_planner_generates_updates_deletions_and_ignored(self) -> None:
        path = self._create_docx(
            [
                "SENIOR FEMENINO ASEGUNDA AUTON?MICA ORO, GRUPO 1",
                "BASKET ARANJUEZ 63 - 52 CABRINI A.P.A.",
                "Parciales: 20-11, 8-12, 19-14, 16-15",
                "Jugadoras: Mar?a Contreras 2, Luc?a Casado 14",
                "Buen comienzo de partido.",
                "JUNIOR PREFERENTE FEMENINOPRIMERA DIVISI?N, GRUPO 3",
                "SAN JOS? DEL PARQUE 49-45 BASKET ARANJUEZ ESENCIAL SALUD",
                "Parciales: 10-15, 12-15, 16-6, 11-9",
                "Jugadoras: Leire 1, Angye 2",
                "Derrota dif?cil de digerir.",
            ]
        )

        settings = load_settings(ROOT)
        chronicles = ChronicleParser().parse_docx(path)
        plan = DryRunPlanner(settings.teams, delete_mode="clear_texts_only").build_plan("senior", chronicles)

        self.assertEqual([item.team_config.web_block_name for item in plan.updates], ["Senior Femenino A"])
        self.assertEqual(len(plan.deletions), 3)
        self.assertEqual(plan.ignored[0].section, "junior")

    def test_mapper_uses_result_alias_to_break_heading_ties(self) -> None:
        settings = load_settings(ROOT)
        chronicle = ParsedChronicle(
            raw_heading="ALEVIN FEMENINO SEGUNDO ANO",
            normalized_heading=normalize_lookup_key("ALEVIN FEMENINO SEGUNDO ANO"),
            section="alevin",
            competition_lines=["LIGA MARCO ALDANY ORO", "GRUPO 2"],
            result_raw="BASKET ARANJUEZ POSTAS TAPERIA 42 - 67 COLEGIO ALAMEDA DE OSUNA",
            home_team="BASKET ARANJUEZ POSTAS TAPERIA",
            away_team="COLEGIO ALAMEDA DE OSUNA",
            home_score=42,
            away_score=67,
            basket_is_home=True,
            basket_score=42,
            rival_team="COLEGIO ALAMEDA DE OSUNA",
            rival_score=67,
            partials=[],
            players_text="Jugadoras: Sara Cuesta, Alma Velasco",
            body_paragraphs=["Buen partido del equipo alevin."],
            is_complete=True,
            warnings=[],
            source_paragraphs=[],
            order_index=1,
        )

        mapped, errors = TeamMapper(settings.teams).map_section("alevin", [chronicle])

        self.assertEqual(len(mapped), 1)
        self.assertEqual(
            normalize_lookup_key(mapped[0].team_config.web_block_name),
            normalize_lookup_key("Alevin Femenino 2º año Tapería Postas"),
        )
        self.assertEqual(errors, [])

    def test_mapper_groups_multiple_chronicles_for_same_team(self) -> None:
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

        settings = load_settings(ROOT)
        chronicles = ChronicleParser().parse_docx(path)

        mapped, errors = TeamMapper(settings.teams).map_section("infantil_femenino", chronicles)

        self.assertEqual(errors, [])
        self.assertEqual(len(mapped), 1)
        self.assertEqual(mapped[0].team_config.web_block_name, "Infantil Femenino 2º año Esencial Salud")
        self.assertEqual(len(mapped[0].all_chronicles), 2)
        self.assertEqual(mapped[0].all_chronicles[0].result_raw, "BASKET ARANJUEZ ESENCIAL SALUD 89 - 39 VALCUDE ALCOBENDAS")
        self.assertEqual(mapped[0].all_chronicles[1].result_raw, "SAN JOSÉ DEL PARQUE 4 - 55 BASKET ARANJUEZ ESENCIAL SALUD")

    def test_matches_target_section_splits_infantil_by_gender(self) -> None:
        self.assertTrue(
            matches_target_section(
                "infantil_femenino",
                chronicle_section="infantil",
                heading="INFANTIL FEMENINO PRIMER AÑO",
            )
        )
        self.assertFalse(
            matches_target_section(
                "infantil_femenino",
                chronicle_section="infantil",
                heading="INFANTIL MASCULINO PRIMER AÑO",
            )
        )
        self.assertTrue(
            matches_target_section(
                "infantil_masculino",
                chronicle_section="infantil",
                heading="INFANTIL MASCULINO PRIMER AÑO",
            )
        )

    def test_mapper_prefers_complete_entry_as_primary_when_team_has_two_matches(self) -> None:
        settings = load_settings(ROOT)
        chronicles = [
            ParsedChronicle(
                raw_heading="CADETE PREFERENTE FEMENINO PRIMER",
                normalized_heading=normalize_lookup_key("CADETE PREFERENTE FEMENINO PRIMER"),
                section="cadete",
                competition_lines=["A DIVISIÃ“N", "GRUPO 4"],
                result_raw="COLEGIO ALAMEDA DE OSUNA 16 - 37 BASKET ARANJUEZ TAPERIA POSTAS",
                home_team="COLEGIO ALAMEDA DE OSUNA",
                away_team="BASKET ARANJUEZ TAPERIA POSTAS",
                home_score=16,
                away_score=37,
                basket_is_home=False,
                basket_score=37,
                rival_team="COLEGIO ALAMEDA DE OSUNA",
                rival_score=16,
                partials=[],
                players_text=None,
                body_paragraphs=[],
                is_complete=False,
                warnings=["parse_warning:incomplete_block"],
                source_paragraphs=[],
                order_index=1,
            ),
            ParsedChronicle(
                raw_heading="CADETE PREFERENTE FEMENINO PRIMER",
                normalized_heading=normalize_lookup_key("CADETE PREFERENTE FEMENINO PRIMER"),
                section="cadete",
                competition_lines=["A DIVISIÃ“N", "GRUPO 4"],
                result_raw="BASKET ARANJUEZ POSTAS TAPERIA 67-56 ARGANZUELA CENTRO",
                home_team="BASKET ARANJUEZ POSTAS TAPERIA",
                away_team="ARGANZUELA CENTRO",
                home_score=67,
                away_score=56,
                basket_is_home=True,
                basket_score=67,
                rival_team="ARGANZUELA CENTRO",
                rival_score=56,
                partials=[],
                players_text="JUGADORAS: ...",
                body_paragraphs=["CrÃ³nica completa."],
                is_complete=True,
                warnings=[],
                source_paragraphs=[],
                order_index=2,
            ),
        ]

        mapped, errors = TeamMapper(settings.teams).map_section("cadete", chronicles)

        self.assertEqual(errors, [])
        self.assertEqual(len(mapped), 1)
        self.assertEqual(
            normalize_lookup_key(mapped[0].team_config.web_block_name),
            normalize_lookup_key("Cadete Femenino 2º año Tapería Postas"),
        )
        self.assertEqual(mapped[0].parsed_chronicle.result_raw, "BASKET ARANJUEZ POSTAS TAPERIA 67-56 ARGANZUELA CENTRO")
        self.assertEqual(len(mapped[0].all_chronicles), 2)

    def test_load_settings_exposes_configured_sections_and_defaults(self) -> None:
        settings = load_settings(ROOT)

        self.assertCountEqual(
            settings.configured_sections(),
            ["alevin", "infantil_femenino", "infantil_masculino", "cadete", "junior", "sub22", "senior"],
        )
        self.assertIsNotNone(settings.runtime.default_docx_path)
        self.assertIn("senior", settings.runtime.section_entry_urls)
        self.assertIn("alevin", settings.runtime.section_entry_urls)
        self.assertTrue(settings.runtime.photos_zip_available)

    def test_only_senior_and_sub22_keep_letter_suffixes(self) -> None:
        settings = load_settings(ROOT)
        suffix_pattern = re.compile(r"\s[ABC]$")

        disallowed = [
            team.web_block_name
            for team in settings.teams
            if team.section not in {"senior", "sub22"} and suffix_pattern.search(team.web_block_name)
        ]

        self.assertEqual(disallowed, [])

    def test_mapper_reassigns_generic_duplicate_heading_to_unused_team(self) -> None:
        settings = load_settings(ROOT)
        chronicles = [
            ParsedChronicle(
                raw_heading="ALEVÍN FEMENINO SEGUNDO AÑO",
                normalized_heading=normalize_lookup_key("ALEVÍN FEMENINO SEGUNDO AÑO"),
                section="alevin",
                competition_lines=["PLAYOFFS DIECISEISAVOS DE FINAL"],
                result_raw="LEGANÉS LEGAMAR 83 - 33 BASKET ARANJUEZ POSTAS TAPERÍA",
                home_team="LEGANÉS LEGAMAR",
                away_team="BASKET ARANJUEZ POSTAS TAPERÍA",
                home_score=83,
                away_score=33,
                basket_is_home=False,
                basket_score=33,
                rival_team="LEGANÉS LEGAMAR",
                rival_score=83,
                partials=[],
                body_paragraphs=["Primera crónica."],
                is_complete=True,
                warnings=[],
                order_index=1,
            ),
            ParsedChronicle(
                raw_heading="ALEVÍN FEMENINO SEGUNDO AÑO",
                normalized_heading=normalize_lookup_key("ALEVÍN FEMENINO SEGUNDO AÑO"),
                section="alevin",
                competition_lines=["CLASIFICACIÓN PUESTOS DEL 61 AL 66"],
                result_raw="Santa María del Pilar 43 - 51 BASKET ARANJUEZ",
                home_team="Santa María del Pilar",
                away_team="BASKET ARANJUEZ",
                home_score=43,
                away_score=51,
                basket_is_home=False,
                basket_score=51,
                rival_team="Santa María del Pilar",
                rival_score=43,
                partials=[],
                body_paragraphs=["Segunda crónica."],
                is_complete=True,
                warnings=[],
                order_index=2,
            ),
        ]

        mapped, errors = TeamMapper(settings.teams).map_section("alevin", chronicles)

        self.assertEqual(errors, [])
        self.assertEqual(
            [item.team_config.web_block_name for item in mapped],
            ["Alevín Femenino 2º año Tapería Postas", "Alevín Femenino 2º año Negro"],
        )

    def test_mapper_prefers_specific_result_alias_for_junior_masculino_b(self) -> None:
        settings = load_settings(ROOT)
        chronicle = ParsedChronicle(
            raw_heading="JUNIOR PREFERENTE MASCULINO",
            normalized_heading=normalize_lookup_key("JUNIOR PREFERENTE MASCULINO"),
            section="junior",
            competition_lines=["CLASIFICACIÃ“N PUESTOS 17-20"],
            result_raw="BASKET ARANJUEZ B 53 - 75 ARROYOMOLINOS CB A",
            home_team="BASKET ARANJUEZ B",
            away_team="ARROYOMOLINOS CB A",
            home_score=53,
            away_score=75,
            basket_is_home=True,
            basket_score=53,
            rival_team="ARROYOMOLINOS CB A",
            rival_score=75,
            partials=[],
            players_text="JUGADORES: ...",
            body_paragraphs=["CrÃ³nica breve."],
            is_complete=True,
            warnings=[],
            source_paragraphs=[],
            order_index=1,
        )

        mapped, errors = TeamMapper(settings.teams).map_section("junior", [chronicle])

        self.assertEqual(errors, [])
        self.assertEqual(len(mapped), 1)
        self.assertEqual(mapped[0].team_config.web_block_name, "Junior Masculino Negro")


if __name__ == "__main__":
    unittest.main()
