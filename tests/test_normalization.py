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

from generador_horarios.normalization import (
    clean_club_name,
    clean_venue_name,
    derive_basket_display_name,
    detect_match_type,
    normalize_lookup_key,
)


class NormalizationTests(unittest.TestCase):
    def test_detect_match_type(self) -> None:
        self.assertEqual(detect_match_type("BASKET ARANJUEZ NEGRO", "RIVAL"), ("local", None))
        self.assertEqual(detect_match_type("RIVAL", "BASKET ARANJUEZ BLANCO"), ("visitante", None))

    def test_sponsor_hides_year_and_color(self) -> None:
        display = derive_basket_display_name(
            "Alv Fem 1\u00baa\u00f1o LIGA MARCO ALDANY",
            "BASKET ARANJUEZ POSTAS TAPERIA",
        )
        self.assertEqual(display, "Alev\u00edn Fem Postas Taper\u00eda")

    def test_mas_abbreviation_maps_to_masc(self) -> None:
        display = derive_basket_display_name(
            "Alv Mas 2\u00baa\u00f1o LIGA MARCO ALDANY - PLATA",
            "BASKET ARANJUEZ CLINICA RICHER",
        )
        self.assertEqual(display, "Alev\u00edn Masc Cl\u00ednica Richer")

    def test_first_year_is_kept_for_infantil_without_sponsor(self) -> None:
        display = derive_basket_display_name(
            "Infantil Masc. 1\u00baa\u00f1o",
            "BASKET ARANJUEZ BLANCO",
        )
        self.assertEqual(display, "Infantil Masc 1\u00ba A\u00f1o Blanco")

    def test_color_kept_when_no_sponsor(self) -> None:
        display = derive_basket_display_name("Junior Fem. Pref.", "BASKET ARANJUEZ NEGRO")
        self.assertEqual(display, "Junior Fem Negro")

    def test_senior_series_inferred_from_category(self) -> None:
        display = derive_basket_display_name("2\u00aa Div Aut Masc PLATA", "BASKET ARANJUEZ")
        self.assertEqual(display, "Senior Masc B")

    def test_clean_venue_name(self) -> None:
        self.assertEqual(clean_venue_name("LAS OLIVAS, CIUDAD DEPORTIVA"), "Las Olivas")
        self.assertEqual(
            clean_venue_name("LICEO FRANCES, PABELLON COLEGIO"),
            "Liceo Franc\u00e9s, Pabell\u00f3n",
        )

    def test_lookup_key_ignores_quotes_and_accents(self) -> None:
        self.assertEqual(
            normalize_lookup_key('LICEO FRANCES "A"'),
            normalize_lookup_key("Liceo Franc\u00e9s A"),
        )

    def test_clean_club_name_preserves_uppercase_brand_tokens(self) -> None:
        self.assertEqual(clean_club_name("ADC BOADILLA"), "ADC Boadilla")
        self.assertEqual(clean_club_name("FGS SPORTS"), "FGS Sports")
        self.assertEqual(clean_club_name("INNOVA-TSN"), "Innova-TSN")


if __name__ == "__main__":
    unittest.main()
