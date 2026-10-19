from datetime import date
from types import SimpleNamespace
import unittest
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from basket_wordpress_sync.discovery import AdminInventory, discovery_report, entry_from_post, select_weekend_drafts, site_origin, title_matches_weekend, title_section
from basket_wordpress_sync.wordpress.target_guard import require_unpublished_target


class DiscoveryTests(unittest.TestCase):
    def test_current_season_thegem_widgets_and_nested_title(self):
        post = {
            "id": 7997, "date": "2026-10-11T19:10:08", "status": "publish",
            "title": {"rendered": "CADETE: 10-11 Octubre 2026"}, "link": "https://example.test/cadete/",
            "content": {"rendered": '<div data-id="a" data-widget_type="heading.default"><h2>Cadete Femenino <span>1er a&ntilde;o</span> D12 Sport Clinic</h2></div><div data-id="b" data-widget_type="thegem-animated-heading.default"><div>PRIMERA FASE <strong>GRUPO 2</strong></div></div>'},
        }
        entry = entry_from_post(post, "https://example.test/wp-admin/")
        self.assertEqual(entry.team_headings, ["Cadete Femenino 1er a\u00f1o D12 Sport Clinic"])
        self.assertEqual(entry.widgets[1].text, "PRIMERA FASE GRUPO 2")
        self.assertFalse(entry.editable)
        report = discovery_report([entry], date(2026, 8, 1))
        self.assertTrue(report["read_only"])
        self.assertIn("thegem-animated-heading.default", report["sections"]["cadete"]["widget_types"])

    def test_sections_and_non_chronicle_posts(self):
        self.assertEqual(title_section("INFANTIL FEMENINO: 10-11 Octubre"), "infantil_femenino")
        self.assertEqual(title_section("SUB 22: 17-18 Octubre"), "sub22")
        self.assertIsNone(title_section("Campus de verano"))
        self.assertIsNone(title_section("SENIORIZAR"))
        self.assertEqual(site_origin("https://example.test/wp-admin/edit.php"), "https://example.test")


class TargetGuardTests(unittest.TestCase):
    def request(self, status="draft", post_id=12):
        response = SimpleNamespace(status=200, ok=True, json=lambda: {"id": post_id, "status": status})
        return SimpleNamespace(get=lambda *args, **kwargs: response)

    def test_published_scheduled_private_and_unknown_are_protected(self):
        for status in ("publish", "future", "private", "pending", "trash", "unknown"):
            with self.subTest(status=status), self.assertRaises(RuntimeError):
                require_unpublished_target(self.request(status), "https://example.test/wp-admin/post.php?post=12&action=elementor", {"https://example.test"})

    def test_only_verified_draft_allowed(self):
        payload = require_unpublished_target(self.request(), "https://example.test/wp-admin/post.php?post=12&action=elementor", {"https://example.test"})
        self.assertEqual(payload["status"], "draft")
        with self.assertRaises(RuntimeError):
            require_unpublished_target(self.request(post_id=13), "https://example.test/?post=12", {"https://example.test"})

    def test_foreign_origin_and_duplicate_post_ids_are_rejected_before_network(self):
        request = SimpleNamespace(get=lambda *args, **kwargs: self.fail("No debe consultar otro destino"))
        for url in ("https://foreign.test/?post=12", "https://example.test/?post=12&post=13", "https://example.test/?post=-1"):
            with self.subTest(url=url), self.assertRaises(ValueError):
                require_unpublished_target(request, url, {"https://example.test"})

    def test_editor_is_never_opened_for_published_entry(self):
        from basket_wordpress_sync.wordpress.service import ElementorUpdateService

        browser = Mock()
        context = browser.new_context.return_value
        context.request = self.request("publish")
        service = object.__new__(ElementorUpdateService)
        service.settings = SimpleNamespace(
            runtime=SimpleNamespace(browser="chromium", headless=True),
            sites={"test": SimpleNamespace(login_url="https://example.test/wp-login.php")},
        )
        with patch("basket_wordpress_sync.wordpress.service._launch_browser", return_value=browser):
            with self.assertRaises(RuntimeError):
                service._open_editor(SimpleNamespace(chromium=Mock()), Path("state.json"), "https://example.test/?post=12&action=elementor")
        context.new_page.assert_not_called()
        browser.close.assert_called_once()

    def test_expired_session_is_reported_without_loading_editor(self):
        request = Mock()
        request.get.side_effect = [SimpleNamespace(status=401), SimpleNamespace(url="https://example.test/wp-login.php")]
        with self.assertRaisesRegex(RuntimeError, "caducado"):
            require_unpublished_target(request, "https://example.test/?post=12", {"https://example.test"})


class WeekendDiscoveryTests(unittest.TestCase):
    def test_admin_rows_do_not_confuse_published_and_drafts(self):
        inventory = AdminInventory()
        inventory.feed('<table><tr id="post-12" class="post status-draft"><td><a class="row-title">SENIOR: 10-11 Octubre 2026</a></td></tr><tr id="post-13" class="post status-publish"><td><a class="row-title">ALEV&Iacute;N: 10-11 Octubre 2026</a></td></tr></table>')
        report = select_weekend_drafts(inventory.entries, date(2026, 10, 10), "https://example.test")
        self.assertEqual(set(report["entry_urls"]), {"senior"})
        self.assertEqual(report["protected"][0]["post_id"], 13)

    def test_no_latest_fallback_or_ambiguous_target(self):
        entries = [{"post_id": 12, "title": "SENIOR: 10-11 Octubre 2026", "status": "draft"}, {"post_id": 13, "title": "SENIOR: 10-11 Octubre 2026", "status": "draft"}]
        report = select_weekend_drafts(entries, date(2026, 10, 10), "https://example.test")
        self.assertFalse(report["entry_urls"])
        self.assertTrue(report["errors"])
        self.assertFalse(select_weekend_drafts(entries, date(2026, 10, 17), "https://example.test")["entry_urls"])

    def test_already_completed_section_is_not_selected_even_with_draft_copy(self):
        entries = [{"post_id": 12, "title": "SENIOR: 10-11 Octubre 2026", "status": status} for status in ("draft", "publish")]
        self.assertFalse(select_weekend_drafts(entries, date(2026, 10, 10), "https://example.test")["entry_urls"])

    def test_weekend_dates_and_month_boundary(self):
        self.assertTrue(title_matches_weekend("SENIOR: 10-11 Octubre 2026", date(2026, 10, 10)))
        self.assertFalse(title_matches_weekend("SENIOR: 17-18 Octubre 2026", date(2026, 10, 10)))
        self.assertFalse(title_matches_weekend("SENIOR: 10-11 Octubre 2025", date(2026, 10, 10)))
        self.assertTrue(title_matches_weekend("SENIOR: 31 Octubre - 1 Noviembre 2026", date(2026, 10, 31)))
        with self.assertRaises(ValueError):
            title_matches_weekend("SENIOR", date(2026, 10, 11))


class GuiValidationTests(unittest.TestCase):
    def test_file_content_changes_invalidate_previous_fingerprint(self):
        from basket_wordpress_sync.gui.app import SyncGui

        with TemporaryDirectory() as directory:
            root = Path(directory)
            document = root / "word.docx"
            document.write_bytes(b"first version")
            variable = lambda value: SimpleNamespace(get=lambda: value)
            gui = object.__new__(SyncGui)
            gui.project_root = root
            gui.config_file = root / "custom.yaml"
            gui.config_dir = None
            gui.docx_var = variable(str(document))
            gui.photos_var = variable("")
            gui.has_photos_var = variable(False)
            gui.url_vars = {"senior": variable("https://example.test/?post=12")}
            gui.section_vars = {"senior": variable(True)}
            first = gui._input_fingerprint()
            document.write_bytes(b"changed input")
            self.assertNotEqual(first, gui._input_fingerprint())
            command = gui._runner_command("dry-run-all", "--sections", "senior")
            self.assertIn(str(gui.config_file), command)
            self.assertLess(command.index("--config-file"), command.index("dry-run-all"))

    def test_section_selection_is_explicit_and_deduplicated(self):
        from basket_wordpress_sync.cli.main import _selected_sections, build_parser

        args = build_parser().parse_args(["update-all", "--sections", "senior", "senior", "alevin"])
        settings = SimpleNamespace(configured_sections=lambda: ["senior", "alevin", "junior"])
        self.assertEqual(_selected_sections(args, settings), ["senior", "alevin"])


if __name__ == "__main__":
    unittest.main()
