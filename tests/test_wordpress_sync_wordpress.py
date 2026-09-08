from pathlib import Path
import site
import sys
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import zipfile

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
USER_SITE = Path(site.getusersitepackages())
for candidate in (ROOT, SRC, USER_SITE):
    candidate_str = str(candidate)
    if candidate_str not in sys.path:
        sys.path.insert(0, candidate_str)

from basket_wordpress_sync.artifacts import RunArtifacts
from basket_wordpress_sync.models import ParsedChronicle, PartialScore
from basket_wordpress_sync.normalizer.text import format_competition_display, format_team_display
from basket_wordpress_sync.photo_repository import PhotoRepository, ResolvedPhoto
from basket_wordpress_sync.wordpress.adapters import BackgroundContainerAdapter, HeaderWidgetAdapter, ImageWidgetAdapter, TextEditorWidgetAdapter
from basket_wordpress_sync.wordpress.contexts import PreviewContext, SidePanelContext, TeamSectionAnchor, _find_heading_widget, _find_ordered_team_anchor, count_team_anchors
from basket_wordpress_sync.wordpress.service import ElementorUpdateService, SkipBlockUpdate, WordPressSessionService


def build_test_settings(**runtime_overrides):
    runtime_defaults = {
        "rich_text_mode": "visual",
        "section_period_counts": {"senior": 4, "alevin": 6},
        "period_label_templates": {4: "{index}C", 5: "{index}C", 6: "{index}P"},
        "overtime_label": "PR",
        "empty_chronicle_placeholder": "-",
        "photos_dir": Path("resultados/fotos"),
        "photo_fallback_name": "basket_aranjuez.png",
        "photos_zip_available": True,
    }
    runtime_defaults.update(runtime_overrides)
    return SimpleNamespace(root_dir=ROOT, runtime=SimpleNamespace(**runtime_defaults), selectors={}, teams=[])


class FakeLocator:
    def __init__(self, count: int = 1, text: str = "", box: dict | None = None) -> None:
        self._count = count
        self._text = text
        self.filled = None
        self.clicked = 0
        self._box = box or {"x": 0, "y": 0, "width": 100, "height": 100}

    @property
    def first(self):
        return self

    def count(self):
        return self._count

    def element_handle(self):
        return None

    def fill(self, value):
        self.filled = value

    def click(self):
        self.clicked += 1

    def text_content(self):
        return self._text

    def bounding_box(self):
        return self._box


class FakePage:
    def __init__(self) -> None:
        self.locators = {}
        self.url = ""

    def locator(self, selector):
        return self.locators.get(selector, FakeLocator(count=0))

    def goto(self, url, **_kwargs):
        self.url = url

    def wait_for_timeout(self, _ms):
        return None


class FakeDeletePage(FakePage):
    def __init__(self) -> None:
        super().__init__()
        self.evaluate_payloads = []

    def evaluate(self, _script, payload):
        self.evaluate_payloads.append(payload)
        return True


class FakeEvaluateRoot:
    def __init__(self, payload) -> None:
        self.payload = payload

    def evaluate(self, _script, *args):
        return self.payload


class FakePreviewForHeadingMatch:
    def __init__(self, payload) -> None:
        self._payload = payload
        self._root = FakeEvaluateRoot(payload)

    def root(self):
        return self._root

    def locator(self, selector):
        return FakeLocator(count=1, text=selector)

    def _locate_text(self, _root, text):
        expected = (text or "").strip()
        for item in self._payload:
            if str(item.get("text") or "").strip() == expected:
                return FakeLocator(count=1, text=expected)
        return FakeLocator(count=0, text=expected)


class DummyPreviewForAnchor:
    def __init__(self) -> None:
        self.located = []

    def root(self):
        raise AssertionError('root() should not be called when widget_catalog is preloaded')

    def locator(self, selector):
        self.located.append(selector)
        return FakeLocator(count=1, text=selector)


class DummyPreviewForVisualElements:
    def __init__(self, payload) -> None:
        self.located = []
        self._root = FakeEvaluateRoot(payload)
        self.page = FakeDeletePage()

    def root(self):
        return self._root

    def locator(self, selector):
        self.located.append(selector)
        return FakeLocator(count=1, text=selector)


class FakeAnchor:
    def __init__(self) -> None:
        self.clicked = []
        self.heading_text = "Senior Femenino A"
        self.available_fields = {
            "team_name",
            "competition",
            "group",
            "home_team",
            "score",
            "away_team",
            "table_home_name",
            "table_away_name",
            "chronicle_text",
        }

    def click_field(self, field_name, fallback_text=None):
        self.clicked.append((field_name, fallback_text))

    def has_field(self, field_name):
        return field_name in self.available_fields

    def has_team_photo_container(self):
        return True

    def team_photo_container_locators(self):
        return []

    def team_image_locators(self):
        return []


class FakePanel:
    def __init__(self) -> None:
        self.widget_checks = []
        self.title_value = None
        self.weight_value = None
        self.modes = []
        self.visual_value = None
        self.html_value = None
        self.fail_visual = False
        self.image_path = None
        self.image_reuse_name = None
        self.background_image_path = None
        self.background_reuse_name = None
        self.image_widget_attempts = []

    def ensure_widget_type(self, *expected_keywords):
        self.widget_checks.append(expected_keywords)
        if expected_keywords == ("Imagen", "Image") and self.image_widget_attempts:
            current = self.image_widget_attempts.pop(0)
            if isinstance(current, Exception):
                raise current
            return current
        return "Edit Encabezado"

    def set_header_title(self, value):
        self.title_value = value

    def set_header_weight(self, value):
        self.weight_value = value

    def select_text_editor_mode(self, mode):
        self.modes.append(mode)

    def set_text_editor_visual(self, value):
        if self.fail_visual:
            raise LookupError("visual unavailable")
        self.visual_value = value

    def set_text_editor_html(self, value):
        self.html_value = value

    def dismiss_media_modal(self):
        return None

    def set_image_file(self, value, reuse_existing_name=None, require_existing_media=False):
        self.image_path = value
        self.image_reuse_name = reuse_existing_name
        self.image_require_existing_media = require_existing_media

    def set_background_image_file(self, value, reuse_existing_name=None, require_existing_media=False):
        self.background_image_path = value
        self.background_reuse_name = reuse_existing_name
        self.background_require_existing_media = require_existing_media


class FakeImageAnchor:
    def __init__(self, locators):
        self._locators = locators
        self.clicked = []
        self.heading_text = "Alevín Rubiocar"
        self.preview = SimpleNamespace(page=SimpleNamespace(wait_for_timeout=lambda _ms: None))

    def image_locators(self):
        return self._locators

    def _click_locator(self, locator):
        self.clicked.append(locator)


class FakeBackgroundAnchor(FakeImageAnchor):
    def __init__(self, locators):
        super().__init__(locators)
        self.heading_text = "Senior Femenino B"

    def team_photo_container_locators(self):
        return self._locators


class FakeLogger:
    def __init__(self) -> None:
        self.events = []

    def record(self, level, code, message, **details):
        self.events.append((level, code, message, details))


class FakeContextForAuth:
    def __init__(self, cookies):
        self._cookies = cookies

    def cookies(self):
        return list(self._cookies)


class WordPressAdapterTests(unittest.TestCase):
    def test_format_team_display_collapses_basket_aranjuez_variants(self) -> None:
        self.assertEqual(format_team_display("BASKET ARANJUEZ RUBIOCAR"), "Basket Aranjuez")
        self.assertEqual(format_team_display("Basket Aranjuez B"), "Basket Aranjuez")
        self.assertEqual(format_team_display("Basket Aranjuez Clínica Richer"), "Basket Aranjuez")

    def test_format_team_display_preserves_acronyms_and_cas_uppercase(self) -> None:
        self.assertEqual(format_team_display("c.b. coslada"), "C.B. Coslada")
        self.assertEqual(format_team_display("cas"), "CAS")
        self.assertEqual(format_team_display('c.b. coslada "b"'), 'C.B. Coslada "B"')

    def test_format_competition_display_recovers_first_and_second_division_labels(self) -> None:
        self.assertEqual(
            format_competition_display("A DIVISIÓN", heading="CADETE PREFERENTE FEMENINO PRIMER"),
            "PRIMERA DIVISIÓN",
        )
        self.assertEqual(
            format_competition_display("DIVISIÓN", heading="CADETE PREFERENTE MASCULINO SEGUNDA"),
            "SEGUNDA DIVISIÓN",
        )

    def test_preview_context_falls_back_to_page_without_frame(self) -> None:
        page = FakePage()
        preview = PreviewContext(page, {"editor": {"preview_frame": "iframe#missing"}})
        self.assertIs(preview.root(), page)

    def test_session_service_detects_valid_login_cookie(self) -> None:
        service = WordPressSessionService(settings=SimpleNamespace(), event_logger=FakeLogger())
        context = FakeContextForAuth(
            [
                {"name": "wordpress_test_cookie"},
                {"name": "wordpress_logged_in_123"},
            ]
        )

        self.assertTrue(service._has_wordpress_login_cookie(context))

    def test_session_service_identifies_login_page(self) -> None:
        service = ElementorUpdateService(
            settings=build_test_settings(),
            event_logger=FakeLogger(),
            artifacts=RunArtifacts(
                run_id="test",
                output_dir=ROOT / "output",
                screenshot_dir=ROOT / "screenshots",
                human_log_path=ROOT / "logs" / "test.log",
                json_log_path=ROOT / "logs" / "test.jsonl",
            ),
        )
        page = FakePage()
        page.url = "https://basketaranjuez.com/wp-login.php?redirect_to=foo"
        page.locators["#loginform, form[name='loginform'], #wp-submit"] = FakeLocator(count=1)

        self.assertTrue(service._is_login_page(page))

    def test_find_heading_widget_matches_compact_heading_variants(self) -> None:
        preview = FakePreviewForHeadingMatch([
            {"dataId": "heading-1", "text": "Senior MasculinoMister Pixel"},
            {"dataId": "heading-2", "text": "Senior Femenino A"},
        ])

        match = _find_heading_widget(preview, ["Senior Mister Pixel", "SENIOR MASCULINO A"])

        self.assertEqual(match, ("Senior Mister Pixel", "heading-1"))

    def test_find_ordered_team_anchor_uses_structural_heading_order(self) -> None:
        preview = FakePreviewForHeadingMatch([
            {"dataId": "title", "text": "SENIOR: 21-22 Marzo 2026", "x": 0, "y": 300, "width": 1000, "height": 120, "type": "heading"},
            {"dataId": "team-1", "text": "Senior Femenino A", "x": 21, "y": 900, "width": 715, "height": 54, "type": "heading"},
            {"dataId": "team-2", "text": "-", "x": 21, "y": 2200, "width": 715, "height": 54, "type": "heading"},
            {"dataId": "team-3", "text": "Senior Mister Pixel", "x": 21, "y": 3600, "width": 715, "height": 54, "type": "heading"},
        ])

        self.assertEqual(_find_ordered_team_anchor(preview, 2), ("-", "team-2"))

    def test_count_team_anchors_uses_same_structural_filter(self) -> None:
        preview = FakePreviewForHeadingMatch([
            {"dataId": "title", "text": "ALEVÍN: 18-19 Abril 2026", "x": 0, "y": 300, "width": 1000, "height": 120, "type": "heading"},
            {"dataId": "team-1", "text": "Alevín Femenino 1º año Blanco", "x": 21, "y": 900, "width": 715, "height": 54, "type": "heading"},
            {"dataId": "score", "text": "77 - 52", "x": 320, "y": 1100, "width": 120, "height": 54, "type": "heading"},
            {"dataId": "team-2", "text": "Alevín Femenino 1º año Rubiocar", "x": 21, "y": 2200, "width": 715, "height": 54, "type": "heading"},
        ])

        self.assertEqual(count_team_anchors(preview), 2)

    def test_team_section_anchor_raises_when_expected_order_is_missing_in_strict_mode(self) -> None:
        preview = FakePreviewForHeadingMatch([
            {"dataId": "title", "text": "INFANTIL FEMENINO: 21-22 Marzo 2026", "x": 0, "y": 300, "width": 1000, "height": 120, "type": "heading"},
            {"dataId": "team-1", "text": "Infantil Femenino 1º año Blanco", "x": 21, "y": 900, "width": 715, "height": 54, "type": "heading"},
            {"dataId": "team-2", "text": "Infantil Femenino 1º año Negro", "x": 21, "y": 2200, "width": 715, "height": 54, "type": "heading"},
            {"dataId": "team-3", "text": "Infantil Femenino 1º año Amarillo", "x": 21, "y": 3600, "width": 715, "height": 54, "type": "heading"},
            {"dataId": "team-4", "text": "Infantil Femenino 2º año Esencial Salud", "x": 21, "y": 5000, "width": 715, "height": 54, "type": "heading"},
        ])

        with self.assertRaises(LookupError):
            TeamSectionAnchor.locate(
                preview,
                ["INFANTIL PREFERENTE FEMENINO PRIMER"],
                {"preview": {}},
                expected_order=5,
                strict_expected_order=True,
            )

    def test_team_section_anchor_does_not_force_wrong_expected_order_when_heading_mismatches(self) -> None:
        preview = FakePreviewForHeadingMatch([
            {"dataId": "title", "text": "SENIOR: 25-26 Abril 2026", "x": 0, "y": 300, "width": 1000, "height": 120, "type": "heading"},
            {"dataId": "team-1", "text": "Senior Femenino A", "x": 21, "y": 900, "width": 715, "height": 54, "type": "heading"},
            {"dataId": "team-2", "text": "Senior Mister Pixel", "x": 21, "y": 2200, "width": 715, "height": 54, "type": "heading"},
        ])

        anchor = TeamSectionAnchor.locate(
            preview,
            ["Senior Mister Pixel"],
            {"preview": {}},
            expected_order=1,
            strict_expected_order=False,
        )

        self.assertEqual((anchor.current_widget or {}).get("dataId"), "team-2")

    def test_structured_field_widgets_follow_rows_without_crossing_blocks(self) -> None:
        anchor = TeamSectionAnchor(
            preview=DummyPreviewForAnchor(),
            heading_text="Senior Femenino A",
            heading_locator=FakeLocator(),
            root_locator=FakeLocator(),
            field_selectors={},
            widget_catalog=[
                {"dataId": "team-1", "text": "Senior Femenino A", "type": "heading", "x": 21, "y": 900, "width": 715, "height": 54},
                {"dataId": "competition", "text": "Liga", "type": "heading", "x": 21, "y": 980, "width": 300, "height": 40},
                {"dataId": "group", "text": "Grupo 1", "type": "heading", "x": 380, "y": 980, "width": 300, "height": 40},
                {"dataId": "home-team", "text": "Basket Aranjuez", "type": "heading", "x": 21, "y": 1040, "width": 250, "height": 40},
                {"dataId": "score", "text": "63 - 52", "type": "heading", "x": 330, "y": 1045, "width": 120, "height": 40},
                {"dataId": "away-team", "text": "Cabrini", "type": "heading", "x": 450, "y": 1040, "width": 250, "height": 40},
                {"dataId": "label-1", "text": "1C", "type": "heading", "x": 480, "y": 1120, "width": 30, "height": 30},
                {"dataId": "label-2", "text": "2C", "type": "heading", "x": 540, "y": 1120, "width": 30, "height": 30},
                {"dataId": "label-3", "text": "3C", "type": "heading", "x": 600, "y": 1120, "width": 30, "height": 30},
                {"dataId": "label-4", "text": "4C", "type": "heading", "x": 660, "y": 1120, "width": 30, "height": 30},
                {"dataId": "row-home-name", "text": "Basket Aranjuez", "type": "heading", "x": 425, "y": 1180, "width": 50, "height": 30},
                {"dataId": "row-home-1", "text": "20", "type": "heading", "x": 480, "y": 1180, "width": 30, "height": 30},
                {"dataId": "row-home-2", "text": "8", "type": "heading", "x": 540, "y": 1180, "width": 30, "height": 30},
                {"dataId": "row-home-3", "text": "15", "type": "heading", "x": 600, "y": 1180, "width": 30, "height": 30},
                {"dataId": "row-home-4", "text": "20", "type": "heading", "x": 660, "y": 1180, "width": 30, "height": 30},
                {"dataId": "row-away-name", "text": "Cabrini", "type": "heading", "x": 425, "y": 1230, "width": 50, "height": 30},
                {"dataId": "row-away-1", "text": "11", "type": "heading", "x": 480, "y": 1230, "width": 30, "height": 30},
                {"dataId": "row-away-2", "text": "11", "type": "heading", "x": 540, "y": 1230, "width": 30, "height": 30},
                {"dataId": "row-away-3", "text": "9", "type": "heading", "x": 600, "y": 1230, "width": 30, "height": 30},
                {"dataId": "row-away-4", "text": "17", "type": "heading", "x": 660, "y": 1230, "width": 30, "height": 30},
                {"dataId": "roster", "text": "Jugadoras: ...", "type": "text_editor", "x": 425, "y": 1300, "width": 280, "height": 80},
                {"dataId": "chronicle", "text": "Cronica", "type": "text_editor", "x": 21, "y": 1450, "width": 700, "height": 140},
                {"dataId": "team-2", "text": "Senior Femenino B", "type": "heading", "x": 21, "y": 2300, "width": 715, "height": 54},
            ],
            current_widget={"dataId": "team-1", "text": "Senior Femenino A", "type": "heading", "x": 21, "y": 900, "width": 715, "height": 54},
        )

        fields = anchor._structured_field_widgets()

        self.assertEqual(fields["home_team"]["dataId"], "home-team")
        self.assertEqual(fields["score"]["dataId"], "score")
        self.assertEqual(fields["table_home_name"]["dataId"], "row-home-name")
        self.assertEqual(fields["home_period_4"]["dataId"], "row-home-4")
        self.assertEqual(fields["table_away_name"]["dataId"], "row-away-name")
        self.assertEqual(fields["away_period_3"]["dataId"], "row-away-3")
        self.assertEqual(fields["chronicle_text"]["dataId"], "chronicle")

    def test_structured_field_widgets_can_recover_corrupted_score_row(self) -> None:
        anchor = TeamSectionAnchor(
            preview=DummyPreviewForAnchor(),
            heading_text="Alevín Rubiocar",
            heading_locator=FakeLocator(),
            root_locator=FakeLocator(),
            field_selectors={},
            widget_catalog=[
                {"dataId": "team-1", "text": "Alevín Rubiocar", "type": "heading", "x": 21, "y": 900, "width": 715, "height": 54},
                {"dataId": "competition", "text": "GRUPO 4", "type": "heading", "x": 21, "y": 980, "width": 300, "height": 40},
                {"dataId": "home-team", "text": "C.B. Coslada", "type": "heading", "x": 21, "y": 1040, "width": 250, "height": 40},
                {"dataId": "score", "text": "texto roto", "type": "heading", "x": 330, "y": 1045, "width": 120, "height": 40},
                {"dataId": "away-team", "text": "Basket Aranjuez Rubiocar", "type": "heading", "x": 450, "y": 1040, "width": 250, "height": 40},
                {"dataId": "label-1", "text": "1P", "type": "heading", "x": 480, "y": 1120, "width": 30, "height": 30},
                {"dataId": "label-2", "text": "2P", "type": "heading", "x": 540, "y": 1120, "width": 30, "height": 30},
                {"dataId": "label-3", "text": "3P", "type": "heading", "x": 600, "y": 1120, "width": 30, "height": 30},
                {"dataId": "label-4", "text": "4P", "type": "heading", "x": 660, "y": 1120, "width": 30, "height": 30},
                {"dataId": "label-5", "text": "5P", "type": "heading", "x": 720, "y": 1120, "width": 30, "height": 30},
                {"dataId": "label-6", "text": "6P", "type": "heading", "x": 780, "y": 1120, "width": 30, "height": 30},
                {"dataId": "row-home-name", "text": "C.B. Coslada", "type": "heading", "x": 425, "y": 1180, "width": 50, "height": 30},
                {"dataId": "row-home-1", "text": "6", "type": "heading", "x": 480, "y": 1180, "width": 30, "height": 30},
                {"dataId": "row-home-2", "text": "13", "type": "heading", "x": 540, "y": 1180, "width": 30, "height": 30},
                {"dataId": "row-home-3", "text": "13", "type": "heading", "x": 600, "y": 1180, "width": 30, "height": 30},
                {"dataId": "row-home-4", "text": "8", "type": "heading", "x": 660, "y": 1180, "width": 30, "height": 30},
                {"dataId": "row-home-5", "text": "14", "type": "heading", "x": 720, "y": 1180, "width": 30, "height": 30},
                {"dataId": "row-home-6", "text": "8", "type": "heading", "x": 780, "y": 1180, "width": 30, "height": 30},
                {"dataId": "row-away-name", "text": "Basket Aranjuez Rubiocar", "type": "heading", "x": 425, "y": 1230, "width": 50, "height": 30},
                {"dataId": "row-away-1", "text": "3", "type": "heading", "x": 480, "y": 1230, "width": 30, "height": 30},
                {"dataId": "row-away-2", "text": "4", "type": "heading", "x": 540, "y": 1230, "width": 30, "height": 30},
                {"dataId": "row-away-3", "text": "4", "type": "heading", "x": 600, "y": 1230, "width": 30, "height": 30},
                {"dataId": "row-away-4", "text": "4", "type": "heading", "x": 660, "y": 1230, "width": 30, "height": 30},
                {"dataId": "row-away-5", "text": "0", "type": "heading", "x": 720, "y": 1230, "width": 30, "height": 30},
                {"dataId": "row-away-6", "text": "0", "type": "heading", "x": 780, "y": 1230, "width": 30, "height": 30},
                {"dataId": "chronicle", "text": "Cronica", "type": "text_editor", "x": 21, "y": 1450, "width": 700, "height": 140},
                {"dataId": "team-2", "text": "Alevín siguiente", "type": "heading", "x": 21, "y": 2300, "width": 715, "height": 54},
            ],
            current_widget={"dataId": "team-1", "text": "Alevín Rubiocar", "type": "heading", "x": 21, "y": 900, "width": 715, "height": 54},
        )

        fields = anchor._structured_field_widgets()

        self.assertEqual(fields["competition"]["dataId"], "competition")
        self.assertEqual(fields["home_team"]["dataId"], "home-team")
        self.assertEqual(fields["score"]["dataId"], "score")
        self.assertEqual(fields["away_team"]["dataId"], "away-team")
        self.assertEqual(fields["period_label_6"]["dataId"], "label-6")
        self.assertEqual(fields["away_period_6"]["dataId"], "row-away-6")

    def test_image_widgets_ignore_gallery_grid_widgets(self) -> None:
        anchor = TeamSectionAnchor(
            preview=DummyPreviewForAnchor(),
            heading_text="Senior Femenino A",
            heading_locator=FakeLocator(),
            root_locator=FakeLocator(),
            field_selectors={},
            widget_catalog=[
                {"dataId": "team-1", "text": "Senior Femenino A", "type": "heading", "className": "elementor-widget-heading", "x": 21, "y": 900, "width": 715, "height": 54},
                {"dataId": "gallery", "text": "", "type": "image", "className": "elementor-widget-thegem-gallery-grid", "x": 21, "y": 980, "width": 300, "height": 200},
                {"dataId": "real-image", "text": "", "type": "image", "className": "elementor-widget-image", "x": 21, "y": 1200, "width": 300, "height": 200},
                {"dataId": "team-2", "text": "Senior Femenino B", "type": "heading", "className": "elementor-widget-heading", "x": 21, "y": 2300, "width": 715, "height": 54},
            ],
            current_widget={"dataId": "team-1", "text": "Senior Femenino A", "type": "heading", "className": "elementor-widget-heading", "x": 21, "y": 900, "width": 715, "height": 54},
        )

        widgets = anchor._image_widgets()

        self.assertEqual([item["dataId"] for item in widgets], ["real-image"])

    def test_team_photo_container_locator_uses_background_container_before_patrocinador(self) -> None:
        anchor = TeamSectionAnchor(
            preview=DummyPreviewForVisualElements([
                {"dataId": "team-photo", "className": "e-con e-child", "widgetType": "", "text": "", "x": 21, "y": 2555, "width": 402, "height": 320, "imgSrc": "", "backgroundImage": 'url("team.jpeg")'},
                {"dataId": "guard", "className": "e-con e-child e-empty", "widgetType": "", "text": "", "x": 21, "y": 3575, "width": 813, "height": 124, "imgSrc": "", "backgroundImage": 'url("guard.png")'},
                {"dataId": "sponsor", "className": "elementor-widget-image", "widgetType": "image.default", "text": "", "x": 21, "y": 3234, "width": 385, "height": 250, "imgSrc": "sponsor.jpg", "backgroundImage": "none"},
            ]),
            heading_text="Alevín Rubiocar",
            heading_locator=FakeLocator(box={"x": 21, "y": 2354, "width": 813, "height": 54}),
            root_locator=FakeLocator(),
            field_selectors={},
            widget_catalog=[
                {"dataId": "team-1", "text": "Alevín Rubiocar", "type": "heading", "x": 21, "y": 2354, "width": 813, "height": 54},
                {"dataId": "patro", "text": "PATROCINADOR", "type": "heading", "x": 21, "y": 3208, "width": 385, "height": 25},
                {"dataId": "team-2", "text": "Alevín Postas Tapería", "type": "heading", "x": 21, "y": 3799, "width": 813, "height": 54},
            ],
            current_widget={"dataId": "team-1", "text": "Alevín Rubiocar", "type": "heading", "x": 21, "y": 2354, "width": 813, "height": 54},
        )

        locator = anchor.team_photo_container_locator()

        self.assertEqual(locator.text_content(), '[data-id="team-photo"]')

    def test_remove_entire_block_targets_all_visual_elements_in_team_interval(self) -> None:
        preview = DummyPreviewForVisualElements([
            {"dataId": "page-wrapper", "className": "e-con", "widgetType": "", "text": "", "x": 0, "y": 800, "width": 850, "height": 3000, "imgSrc": "", "backgroundImage": "none"},
            {"dataId": "team-1", "className": "elementor-widget-heading", "widgetType": "heading.default", "text": "Infantil Masculino Blanco", "x": 21, "y": 900, "width": 715, "height": 54, "imgSrc": "", "backgroundImage": "none"},
            {"dataId": "score", "className": "elementor-widget-heading", "widgetType": "heading.default", "text": "63 - 52", "x": 330, "y": 1045, "width": 120, "height": 40, "imgSrc": "", "backgroundImage": "none"},
            {"dataId": "photo", "className": "e-con e-child", "widgetType": "", "text": "", "x": 21, "y": 1130, "width": 402, "height": 320, "imgSrc": "", "backgroundImage": 'url("team.jpg")'},
            {"dataId": "chronicle", "className": "elementor-widget-text-editor", "widgetType": "text-editor.default", "text": "Crónica antigua", "x": 21, "y": 1500, "width": 700, "height": 180, "imgSrc": "", "backgroundImage": "none"},
            {"dataId": "sidebar", "className": "e-con", "widgetType": "", "text": "Sidebar", "x": 1040, "y": 920, "width": 260, "height": 500, "imgSrc": "", "backgroundImage": "none"},
        ])
        anchor = TeamSectionAnchor(
            preview=preview,
            heading_text="Infantil Masculino Blanco",
            heading_locator=FakeLocator(),
            root_locator=FakeLocator(),
            field_selectors={},
            widget_catalog=[
                {"dataId": "team-1", "text": "Infantil Masculino Blanco", "type": "heading", "x": 21, "y": 900, "width": 715, "height": 54},
                {"dataId": "team-2", "text": "Infantil Masculino Negro", "type": "heading", "x": 21, "y": 1800, "width": 715, "height": 54},
            ],
            current_widget={"dataId": "team-1", "text": "Infantil Masculino Blanco", "type": "heading", "x": 21, "y": 900, "width": 715, "height": 54},
        )

        removed = anchor.remove_entire_block()

        self.assertTrue(removed)
        self.assertEqual(
            preview.page.evaluate_payloads[-1],
            ["team-1", "score", "photo", "chronicle"],
        )

    def test_sponsor_image_locators_use_elements_after_patrocinador(self) -> None:
        anchor = TeamSectionAnchor(
            preview=DummyPreviewForVisualElements([
                {"dataId": "team-photo", "className": "e-con e-child", "widgetType": "", "text": "", "x": 21, "y": 2555, "width": 402, "height": 320, "imgSrc": "", "backgroundImage": 'url("team.jpeg")'},
                {"dataId": "sponsor", "className": "elementor-widget-thegem-styledimage", "widgetType": "thegem-styledimage.default", "text": "", "x": 21, "y": 3234, "width": 385, "height": 250, "imgSrc": "sponsor.jpg", "backgroundImage": "none"},
            ]),
            heading_text="Alevín Rubiocar",
            heading_locator=FakeLocator(box={"x": 21, "y": 2354, "width": 813, "height": 54}),
            root_locator=FakeLocator(),
            field_selectors={},
            widget_catalog=[
                {"dataId": "team-1", "text": "Alevín Rubiocar", "type": "heading", "x": 21, "y": 2354, "width": 813, "height": 54},
                {"dataId": "patro", "text": "PATROCINADOR", "type": "heading", "x": 21, "y": 3208, "width": 385, "height": 25},
                {"dataId": "team-2", "text": "Alevín Postas Tapería", "type": "heading", "x": 21, "y": 3799, "width": 813, "height": 54},
            ],
            current_widget={"dataId": "team-1", "text": "Alevín Rubiocar", "type": "heading", "x": 21, "y": 2354, "width": 813, "height": 54},
        )

        locators = anchor.sponsor_image_locators()

        self.assertEqual([locator.text_content() for locator in locators], ['[data-id="sponsor"]'])

    def test_image_widget_adapter_tries_second_candidate_when_first_is_not_editable(self) -> None:
        panel = FakePanel()
        panel.image_widget_attempts = [ValueError("Widget seleccionado inesperado: 'Elementos'."), "Edit Imagen"]
        first_locator = FakeLocator(text="first")
        second_locator = FakeLocator(text="second")
        anchor = FakeImageAnchor([first_locator, second_locator])

        ImageWidgetAdapter(panel).update_image(anchor, ROOT / "resultados" / "fotos" / "basket_aranjuez.png")

        self.assertEqual(anchor.clicked, [first_locator, second_locator])
        self.assertEqual(panel.image_path, ROOT / "resultados" / "fotos" / "basket_aranjuez.png")

    def test_background_container_adapter_reuses_existing_media_name_for_fallback(self) -> None:
        panel = FakePanel()
        anchor = FakeBackgroundAnchor([FakeLocator(text="container")])
        image_path = ROOT / "resultados" / "fotos" / "basket_aranjuez.png"

        BackgroundContainerAdapter(panel).update_image(
            anchor,
            image_path,
            reuse_existing_name="basket_aranjuez.png",
        )

        self.assertEqual(anchor.clicked, [anchor._locators[0]])
        self.assertEqual(panel.background_image_path, image_path)
        self.assertEqual(panel.background_reuse_name, "basket_aranjuez.png")
        self.assertFalse(panel.background_require_existing_media)

    def test_media_search_queries_include_humanized_filename(self) -> None:
        page = FakePage()
        side_panel = SidePanelContext(page, {})

        queries = side_panel._media_search_queries("basket_aranjuez.png")

        self.assertEqual(queries[0], "basket_aranjuez")
        self.assertIn("basket aranjuez", queries)

    def test_photo_repository_uses_fallback_for_all_teams_when_week_has_no_zip(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            photos_dir = root / "resultados" / "fotos"
            cache_dir = root / "output" / "photo_assets"
            photos_dir.mkdir(parents=True)
            cache_dir.mkdir(parents=True)
            (photos_dir / "basket_aranjuez.png").write_bytes(b"fake-image")
            (photos_dir / "equipo_real.png").write_bytes(b"fake-team-image")

            repository = PhotoRepository(
                root_dir=root,
                photos_dir=photos_dir,
                cache_dir=cache_dir,
                fallback_name="basket_aranjuez.png",
                photos_zip_available=False,
            )
            team = SimpleNamespace(
                web_block_name="Senior Femenino A",
                heading_aliases=["SENIOR FEMENINO A"],
                photo_aliases=["equipo real"],
            )

            resolved = repository.resolve_for_team(team)

            self.assertTrue(resolved.fallback)
            self.assertEqual(resolved.source_name, "basket_aranjuez.png")
            self.assertEqual(resolved.path.name, "basket_aranjuez.png")

    def test_photo_repository_prefers_configured_zip_path_over_other_archives(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            photos_dir = root / "resultados" / "fotos"
            cache_dir = root / "output" / "photo_assets"
            photos_dir.mkdir(parents=True)
            cache_dir.mkdir(parents=True)
            (photos_dir / "basket_aranjuez.png").write_bytes(b"fallback")

            old_zip = photos_dir / "old_week.zip"
            new_zip = photos_dir / "new_week.zip"
            with zipfile.ZipFile(old_zip, "w") as archive:
                archive.writestr("equipo_blanco_viejo.png", b"old")
            with zipfile.ZipFile(new_zip, "w") as archive:
                archive.writestr("equipo_blanco_actual.png", b"new")

            repository = PhotoRepository(
                root_dir=root,
                photos_dir=photos_dir,
                cache_dir=cache_dir,
                fallback_name="basket_aranjuez.png",
                photos_zip_available=True,
                photos_zip_path=new_zip,
            )
            team = SimpleNamespace(
                web_block_name="Infantil Femenino 1º año Blanco",
                heading_aliases=["INFANTIL FEMENINO PRIMER AÑO"],
                photo_aliases=["equipo blanco actual"],
            )

            resolved = repository.resolve_for_team(team)

            self.assertFalse(resolved.fallback)
            self.assertEqual(resolved.source_name, "equipo_blanco_actual.png")

    def test_header_widget_adapter_updates_title(self) -> None:
        anchor = FakeAnchor()
        panel = FakePanel()

        HeaderWidgetAdapter(panel).update_field(anchor, "team_name", "Senior Femenino A")

        self.assertEqual(anchor.clicked, [("team_name", None)])
        self.assertEqual(panel.title_value, "Senior Femenino A")
        self.assertIsNone(panel.weight_value)

    def test_header_widget_adapter_updates_weight_when_requested(self) -> None:
        anchor = FakeAnchor()
        panel = FakePanel()

        HeaderWidgetAdapter(panel).update_field(anchor, "home_period_1", "11", weight="bold")

        self.assertEqual(panel.title_value, "11")
        self.assertEqual(panel.weight_value, "bold")

    def test_text_editor_widget_adapter_falls_back_to_html(self) -> None:
        anchor = FakeAnchor()
        panel = FakePanel()
        panel.fail_visual = True

        mode = TextEditorWidgetAdapter(panel).update_field(
            anchor,
            "chronicle_text",
            paragraphs=["Párrafo uno.", "Párrafo dos."],
            players_text="Jugadoras: Ana, Bea",
            preferred_mode="visual",
        )

        self.assertEqual(mode, "html")
        self.assertEqual(panel.modes, ["visual", "html", "visual"])
        self.assertIn("<p>Jugadoras: Ana, Bea</p>", panel.html_value)

    def test_period_slot_count_prefers_section_defaults(self) -> None:
        service = ElementorUpdateService(
            settings=build_test_settings(),
            event_logger=FakeLogger(),
            artifacts=RunArtifacts(
                run_id="test",
                output_dir=ROOT / "output",
                screenshot_dir=ROOT / "screenshots",
                human_log_path=ROOT / "logs" / "test.log",
                json_log_path=ROOT / "logs" / "test.jsonl",
            ),
        )

        class AnchorWithoutLabels:
            def field_locator(self, field_name):
                raise LookupError(field_name)

        self.assertEqual(service._period_slot_count("senior", AnchorWithoutLabels(), SimpleNamespace(partials=[])), 4)
        self.assertEqual(service._period_slot_count("alevin", AnchorWithoutLabels(), SimpleNamespace(partials=[])), 6)

    def test_build_period_updates_fills_missing_scores_and_bolds_winner(self) -> None:
        service = ElementorUpdateService(
            settings=build_test_settings(),
            event_logger=FakeLogger(),
            artifacts=RunArtifacts(
                run_id="test",
                output_dir=ROOT / "output",
                screenshot_dir=ROOT / "screenshots",
                human_log_path=ROOT / "logs" / "test.log",
                json_log_path=ROOT / "logs" / "test.jsonl",
            ),
        )
        chronicle = ParsedChronicle(
            raw_heading="SENIOR FEMENINO A",
            normalized_heading="SENIOR FEMENINO A",
            section="senior",
            competition_lines=[],
            result_raw=None,
            home_team="Basket Aranjuez",
            away_team="Rival",
            home_score=0,
            away_score=0,
            basket_is_home=True,
            basket_score=0,
            rival_team="Rival",
            rival_score=0,
            partials=[
                PartialScore(label="1C", home_score=11, away_score=13),
                PartialScore(label="2C", home_score=16, away_score=10),
                PartialScore(label="3C", home_score=None, away_score=None),
            ],
            body_paragraphs=[],
        )

        updates = service._build_period_updates(4, chronicle)

        self.assertEqual(updates[0]["home_weight"], "normal")
        self.assertEqual(updates[0]["away_weight"], "bold")
        self.assertEqual(updates[1]["home_weight"], "bold")
        self.assertEqual(updates[1]["away_weight"], "normal")
        self.assertEqual(updates[2]["home_value"], "-")
        self.assertEqual(updates[2]["away_value"], "-")
        self.assertEqual(updates[3]["label"], "4C")
        self.assertEqual(updates[3]["home_value"], "-")
        self.assertEqual(updates[3]["away_value"], "-")

    def test_build_period_updates_uses_section_label_templates_not_docx_labels(self) -> None:
        service = ElementorUpdateService(
            settings=build_test_settings(),
            event_logger=FakeLogger(),
            artifacts=RunArtifacts(
                run_id="test",
                output_dir=ROOT / "output",
                screenshot_dir=ROOT / "screenshots",
                human_log_path=ROOT / "logs" / "test.log",
                json_log_path=ROOT / "logs" / "test.jsonl",
            ),
        )
        chronicle = ParsedChronicle(
            raw_heading="ALEVÍN FEMENINO PRIMER AÑO",
            normalized_heading="ALEVÍN FEMENINO PRIMER AÑO",
            section="alevin",
            competition_lines=[],
            result_raw=None,
            home_team="Basket Aranjuez",
            away_team="Rival",
            home_score=0,
            away_score=0,
            basket_is_home=True,
            basket_score=0,
            rival_team="Rival",
            rival_score=0,
            partials=[
                PartialScore(label="1C", home_score=15, away_score=2),
                PartialScore(label="2C", home_score=10, away_score=2),
                PartialScore(label="3C", home_score=22, away_score=2),
                PartialScore(label="4C", home_score=13, away_score=4),
            ],
            body_paragraphs=[],
        )

        updates = service._build_period_updates(6, chronicle)

        self.assertEqual([item["label"] for item in updates], ["1P", "2P", "3P", "4P", "5P", "6P"])
        self.assertEqual(updates[4]["home_value"], "-")
        self.assertEqual(updates[5]["away_value"], "-")

    def test_chronicle_paragraphs_include_multiple_matches_for_same_team(self) -> None:
        service = ElementorUpdateService(
            settings=build_test_settings(),
            event_logger=FakeLogger(),
            artifacts=RunArtifacts(
                run_id="test",
                output_dir=ROOT / "output",
                screenshot_dir=ROOT / "screenshots",
                human_log_path=ROOT / "logs" / "test.log",
                json_log_path=ROOT / "logs" / "test.jsonl",
            ),
        )
        first = ParsedChronicle(
            raw_heading="INFANTIL PREFERENTE FEMENINO",
            normalized_heading="INFANTIL PREFERENTE FEMENINO",
            section="infantil",
            competition_lines=["PRIMERA DIVISIÓN", "GRUPO 2"],
            result_raw="BASKET ARANJUEZ ESENCIAL SALUD 89 - 39 VALCUDE ALCOBENDAS",
            home_team="BASKET ARANJUEZ ESENCIAL SALUD",
            away_team="VALCUDE ALCOBENDAS",
            home_score=89,
            away_score=39,
            basket_is_home=True,
            basket_score=89,
            rival_team="VALCUDE ALCOBENDAS",
            rival_score=39,
            partials=[
                PartialScore(label="1C", home_score=25, away_score=18),
                PartialScore(label="2C", home_score=16, away_score=4),
                PartialScore(label="3C", home_score=22, away_score=9),
                PartialScore(label="4C", home_score=26, away_score=8),
            ],
            body_paragraphs=["Primera crónica."],
        )
        second = ParsedChronicle(
            raw_heading="INFANTIL PREFERENTE FEMENINO",
            normalized_heading="INFANTIL PREFERENTE FEMENINO",
            section="infantil",
            competition_lines=["PRIMERA DIVISIÓN", "GRUPO 2"],
            result_raw="SAN JOSÉ DEL PARQUE 4 - 55 BASKET ARANJUEZ ESENCIAL SALUD",
            home_team="SAN JOSÉ DEL PARQUE",
            away_team="BASKET ARANJUEZ ESENCIAL SALUD",
            home_score=4,
            away_score=55,
            basket_is_home=False,
            basket_score=55,
            rival_team="SAN JOSÉ DEL PARQUE",
            rival_score=4,
            partials=[
                PartialScore(label="1C", home_score=23, away_score=2),
                PartialScore(label="2C", home_score=21, away_score=0),
                PartialScore(label="3C", home_score=2, away_score=7),
                PartialScore(label="4C", home_score=4, away_score=0),
            ],
            body_paragraphs=["Segunda crónica."],
        )
        mapped = SimpleNamespace(all_chronicles=[first, second])

        paragraphs = service._chronicle_paragraphs(mapped)

        self.assertEqual(
            paragraphs,
            [
                "BASKET ARANJUEZ ESENCIAL SALUD 89 - 39 VALCUDE ALCOBENDAS",
                "PARCIALES: 25-18, 16-4, 22-9, 26-8",
                "Primera crónica.",
                "",
                "SAN JOSÉ DEL PARQUE 4 - 55 BASKET ARANJUEZ ESENCIAL SALUD",
                "PARCIALES: 23-2, 21-0, 2-7, 4-0",
                "Segunda crónica.",
            ],
        )

    def test_competition_and_group_overrides_prefer_team_config_values(self) -> None:
        service = ElementorUpdateService(
            settings=build_test_settings(),
            event_logger=FakeLogger(),
            artifacts=RunArtifacts(
                run_id="test",
                output_dir=ROOT / "output",
                screenshot_dir=ROOT / "screenshots",
                human_log_path=ROOT / "logs" / "test.log",
                json_log_path=ROOT / "logs" / "test.jsonl",
            ),
        )
        mapped = SimpleNamespace(
            team_config=SimpleNamespace(
                display_competition="CADETE FEMENINO PREFERENTE",
                display_group="PRIMERA DIVISIÓN - GRUPO 4",
            )
        )
        chronicle = SimpleNamespace(
            competition_title="A DIVISIÓN",
            group_title="GRUPO 4",
            raw_heading="CADETE PREFERENTE FEMENINO PRIMER",
        )

        self.assertEqual(service._competition_value(mapped, chronicle), "CADETE FEMENINO PREFERENTE")
        self.assertEqual(service._group_value(mapped, chronicle), "PRIMERA DIVISIÓN - GRUPO 4")

    def test_group_value_keeps_single_line_group_when_category_is_overridden(self) -> None:
        service = ElementorUpdateService(
            settings=build_test_settings(),
            event_logger=FakeLogger(),
            artifacts=RunArtifacts(
                run_id="test",
                output_dir=ROOT / "output",
                screenshot_dir=ROOT / "screenshots",
                human_log_path=ROOT / "logs" / "test.log",
                json_log_path=ROOT / "logs" / "test.jsonl",
            ),
        )
        mapped = SimpleNamespace(
            team_config=SimpleNamespace(
                display_competition="INFANTIL PREFERENTE 1er AÑO",
                display_group=None,
            )
        )
        chronicle = SimpleNamespace(
            competition_title="GRUPO 4",
            group_title="",
            raw_heading="INFANTIL FEMENINO PRIMER AÑO",
        )

        self.assertEqual(service._group_value(mapped, chronicle), "GRUPO 4")

    def test_safe_header_update_logs_warning(self) -> None:
        anchor = FakeAnchor()
        logger = FakeLogger()
        service = ElementorUpdateService(
            settings=build_test_settings(),
            event_logger=logger,
            artifacts=RunArtifacts(
                run_id="test",
                output_dir=ROOT / "output",
                screenshot_dir=ROOT / "screenshots",
                human_log_path=ROOT / "logs" / "test.log",
                json_log_path=ROOT / "logs" / "test.jsonl",
            ),
        )

        class BrokenAdapter:
            def update_field(self, *args, **kwargs):
                raise LookupError("missing selector")

        service._safe_header_update(anchor, BrokenAdapter(), "competition", "Liga")
        self.assertTrue(any(event[1] == "block_not_found" for event in logger.events))

    def test_safe_header_update_skips_missing_layout_fields_without_warning(self) -> None:
        anchor = FakeAnchor()
        anchor.available_fields = {"team_name"}
        logger = FakeLogger()
        service = ElementorUpdateService(
            settings=build_test_settings(),
            event_logger=logger,
            artifacts=RunArtifacts(
                run_id="test",
                output_dir=ROOT / "output",
                screenshot_dir=ROOT / "screenshots",
                human_log_path=ROOT / "logs" / "test.log",
                json_log_path=ROOT / "logs" / "test.jsonl",
            ),
        )

        class BrokenAdapter:
            def update_field(self, *args, **kwargs):
                raise AssertionError("No debería intentar editar un campo ausente")

        updated = service._safe_header_update(anchor, BrokenAdapter(), "competition", "Liga")

        self.assertEqual(updated, 0)
        self.assertFalse(logger.events)

    def test_safe_team_photo_update_skips_when_block_has_no_photo_target(self) -> None:
        anchor = FakeAnchor()
        logger = FakeLogger()
        service = ElementorUpdateService(
            settings=build_test_settings(),
            event_logger=logger,
            artifacts=RunArtifacts(
                run_id="test",
                output_dir=ROOT / "output",
                screenshot_dir=ROOT / "screenshots",
                human_log_path=ROOT / "logs" / "test.log",
                json_log_path=ROOT / "logs" / "test.jsonl",
            ),
        )

        anchor.team_photo_container_locators = lambda: []
        anchor.team_image_locators = lambda: []
        mapped = SimpleNamespace(team_config=SimpleNamespace(web_block_name="Senior Femenino A"))

        class BrokenAdapter:
            def update_image(self, *args, **kwargs):
                raise AssertionError("No debería intentar editar una foto ausente")

        updated = service._safe_team_photo_update(anchor, BrokenAdapter(), BrokenAdapter(), mapped)

        self.assertEqual(updated, 0)
        self.assertFalse(logger.events)

    def test_safe_team_photo_update_reuses_existing_fallback_media(self) -> None:
        anchor = FakeAnchor()
        logger = FakeLogger()
        service = ElementorUpdateService(
            settings=build_test_settings(),
            event_logger=logger,
            artifacts=RunArtifacts(
                run_id="test",
                output_dir=ROOT / "output",
                screenshot_dir=ROOT / "screenshots",
                human_log_path=ROOT / "logs" / "test.log",
                json_log_path=ROOT / "logs" / "test.jsonl",
            ),
        )

        image_path = ROOT / "resultados" / "fotos" / "basket_aranjuez.png"
        service.photo_repository = SimpleNamespace(
            resolve_for_team=lambda _team: ResolvedPhoto(
                path=image_path,
                fallback=True,
                source_name="basket_aranjuez.png",
            )
        )
        mapped = SimpleNamespace(team_config=SimpleNamespace(web_block_name="Senior Femenino B"))

        class CapturingAdapter:
            def __init__(self):
                self.calls = []

            def update_image(self, anchor_arg, path_arg, *, reuse_existing_name=None, require_existing_media=False):
                self.calls.append((anchor_arg, path_arg, reuse_existing_name, require_existing_media))

        adapter = CapturingAdapter()
        anchor.team_photo_container_locators = lambda: [object()]

        updated = service._safe_team_photo_update(anchor, adapter, adapter, mapped)

        self.assertEqual(updated, 1)
        self.assertEqual(adapter.calls, [(anchor, image_path, "basket_aranjuez.png", False)])

    def test_safe_team_photo_update_uploads_fallback_once_then_requires_reuse(self) -> None:
        anchor = FakeAnchor()
        logger = FakeLogger()
        service = ElementorUpdateService(
            settings=build_test_settings(),
            event_logger=logger,
            artifacts=RunArtifacts(
                run_id="test",
                output_dir=ROOT / "output",
                screenshot_dir=ROOT / "screenshots",
                human_log_path=ROOT / "logs" / "test.log",
                json_log_path=ROOT / "logs" / "test.jsonl",
            ),
        )

        image_path = ROOT / "resultados" / "fotos" / "basket_aranjuez.png"
        service.photo_repository = SimpleNamespace(
            resolve_for_team=lambda _team: ResolvedPhoto(
                path=image_path,
                fallback=True,
                source_name="basket_aranjuez.png",
            )
        )
        mapped = SimpleNamespace(team_config=SimpleNamespace(web_block_name="Senior Femenino B"))

        class UploadThenCaptureAdapter:
            def __init__(self):
                self.calls = []

            def update_image(self, anchor_arg, path_arg, *, reuse_existing_name=None, require_existing_media=False):
                self.calls.append((reuse_existing_name, require_existing_media))
                return False

        adapter = UploadThenCaptureAdapter()
        anchor.team_photo_container_locators = lambda: [object()]

        first_updated = service._safe_team_photo_update(anchor, adapter, adapter, mapped)
        second_updated = service._safe_team_photo_update(anchor, adapter, adapter, mapped)

        self.assertEqual(first_updated, 1)
        self.assertEqual(second_updated, 1)
        self.assertEqual(
            adapter.calls,
            [
                ("basket_aranjuez.png", False),
                ("basket_aranjuez.png", True),
            ],
        )

    def test_update_mapped_block_skips_incomplete_team_when_expected_anchor_is_missing(self) -> None:
        service = ElementorUpdateService(
            settings=build_test_settings(),
            event_logger=FakeLogger(),
            artifacts=RunArtifacts(
                run_id="test",
                output_dir=ROOT / "output",
                screenshot_dir=ROOT / "screenshots",
                human_log_path=ROOT / "logs" / "test.log",
                json_log_path=ROOT / "logs" / "test.jsonl",
            ),
        )
        preview = FakePreviewForHeadingMatch([
            {"dataId": "title", "text": "INFANTIL FEMENINO: 21-22 Marzo 2026", "x": 0, "y": 300, "width": 1000, "height": 120, "type": "heading"},
            {"dataId": "team-1", "text": "Infantil Femenino 1º año Blanco", "x": 21, "y": 900, "width": 715, "height": 54, "type": "heading"},
            {"dataId": "team-2", "text": "Infantil Femenino 1º año Negro", "x": 21, "y": 2200, "width": 715, "height": 54, "type": "heading"},
            {"dataId": "team-3", "text": "Infantil Femenino 1º año Amarillo", "x": 21, "y": 3600, "width": 715, "height": 54, "type": "heading"},
            {"dataId": "team-4", "text": "Infantil Femenino 2º año Esencial Salud", "x": 21, "y": 5000, "width": 715, "height": 54, "type": "heading"},
        ])
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
            body_paragraphs=[],
            is_complete=False,
            warnings=["missing_result", "parse_warning:incomplete_block"],
        )
        mapped = SimpleNamespace(
            team_config=SimpleNamespace(
                section="infantil_femenino",
                web_block_name="ZZZ EQUIPO SIN BLOQUE",
                heading_aliases=["ZZZ HEADING SIN BLOQUE"],
                expected_order=999,
                display_competition=None,
                display_group=None,
            ),
            parsed_chronicle=chronicle,
        )
        service.settings.teams = [mapped.team_config]

        with self.assertRaises(SkipBlockUpdate):
            service._update_mapped_block(preview, FakePanel(), mapped)

    def test_recover_missing_ordered_block_duplicates_last_block_when_only_final_slot_is_absent(self) -> None:
        logger = FakeLogger()
        service = ElementorUpdateService(
            settings=build_test_settings(),
            event_logger=logger,
            artifacts=RunArtifacts(
                run_id="test",
                output_dir=ROOT / "output",
                screenshot_dir=ROOT / "screenshots",
                human_log_path=ROOT / "logs" / "test.log",
                json_log_path=ROOT / "logs" / "test.jsonl",
            ),
        )
        preview = SimpleNamespace(page=SimpleNamespace(wait_for_timeout=lambda _ms: None))
        mapped = SimpleNamespace(
            team_config=SimpleNamespace(
                section="alevin",
                web_block_name="Alevín Masculino 2º año Negro",
                expected_order=7,
            )
        )

        with (
            patch("basket_wordpress_sync.wordpress.service.count_team_anchors", return_value=6),
            patch("basket_wordpress_sync.wordpress.service.duplicate_ordered_team_block", return_value=True) as duplicate_mock,
        ):
            recovered = service._recover_missing_ordered_block(preview, mapped)

        self.assertTrue(recovered)
        duplicate_mock.assert_called_once_with(preview, 6)
        self.assertTrue(any(event[1] == "team_block_duplicated" for event in logger.events))


if __name__ == "__main__":
    unittest.main()
