from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import Mock
import sys
import unittest
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from basket_wordpress_sync.mapper.team_mapper import TeamMapper
from basket_wordpress_sync.parser import ChronicleParser
from basket_wordpress_sync.settings import load_settings
from basket_wordpress_sync.wordpress.api import WordPressApi
from basket_wordpress_sync.wordpress.target_guard import require_unpublished_target
from basket_wordpress_sync.photo_repository import PhotoRepository
from basket_wordpress_sync.models import TeamConfig


class SeasonTests(unittest.TestCase):
    def test_every_current_team_has_a_unique_rule_without_order_inference(self):
        settings = load_settings(ROOT, season="2026-27")
        parser = ChronicleParser()
        chronicles = []
        for index, team in enumerate(settings.teams):
            rule = team.match_rules[0]
            lines = [rule["heading"], *rule.get("competition", "").split("|"), "BASKET ARANJUEZ 40 - 30 CB RIVAL", "Cronica de prueba."]
            chronicles.extend(parser._parse_block(lines, index + 1))
        self.assertEqual(len(settings.teams), 34)
        # Scramble order to prove the identity comes from the rule, not the template slot.
        for section in settings.configured_sections():
            relevant = [c for c in reversed(chronicles) if c.section == ("infantil" if section.startswith("infantil") else section)]
            if section.startswith("infantil"):
                gender = "FEMENINO" if section.endswith("femenino") else "MASCULINO"
                relevant = [c for c in relevant if gender in c.raw_heading]
            mapped, errors = TeamMapper(settings.teams).map_section(section, relevant)
            self.assertFalse(errors, (section, errors))
            self.assertEqual(len(mapped), sum(t.section == section for t in settings.teams))

    def test_two_real_matches_group_and_unknown_group_fails_closed(self):
        settings = load_settings(ROOT, season="2026-27")
        parser = ChronicleParser()
        lines = ["ALEV\u00cdN FEMENINO PRIMER A\u00d1O", "GRUPO 8", "BASKET ARANJUEZ RUBIOCAR 40 - 30 CB RIVAL", "Cronica de prueba."]
        first = parser._parse_block(lines, 1)[0]
        second = parser._parse_block(lines, 2)[0]
        mapped, errors = TeamMapper(settings.teams).map_section("alevin", [first, second])
        self.assertFalse(errors)
        self.assertEqual(len(mapped), 1)
        self.assertEqual(len(mapped[0].all_chronicles), 2)
        unknown = parser._parse_block([lines[0], "GRUPO 99", *lines[2:]], 3)[0]
        self.assertTrue(TeamMapper(settings.teams).map_section("alevin", [unknown])[1])
        group_eighty = parser._parse_block([lines[0], "GRUPO 80", *lines[2:]], 4)[0]
        self.assertTrue(TeamMapper(settings.teams).map_section("alevin", [group_eighty])[1])

    def test_guard_uses_rest_nonce_not_heartbeat_nonce(self):
        request = Mock()
        request.get.side_effect = [
            SimpleNamespace(status=401),
            SimpleNamespace(url="https://example.test/wp-admin/edit.php", text=lambda: 'var heartbeatSettings={"nonce":"wrong"}; var wpApiSettings={"root":"https://example.test/wp-json/","nonce":"correct"};'),
            SimpleNamespace(ok=True, json=lambda: {"id": 12, "status": "draft"}),
        ]
        require_unpublished_target(request, "https://example.test/?post=12", {"https://example.test"})
        self.assertEqual(request.get.call_args.kwargs["headers"], {"X-WP-Nonce": "correct"})

    def test_existing_logo_with_wordpress_suffix_is_reused_without_upload(self):
        api = object.__new__(WordPressApi)
        api._json = Mock(return_value=[{"id": 25, "source_url": "https://example.test/uploads/basket_aranjuez-12.png"}])
        media = api.resolve_media(Path("basket_aranjuez.png"), reuse=True)
        self.assertEqual(media["id"], 25)
        self.assertEqual(api._json.call_count, 1)
        self.assertEqual(api._json.call_args.args[0], "get")

    def test_selected_zip_outside_photos_folder_and_no_stale_loose_photos(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            photos = root / "photos"
            photos.mkdir()
            (photos / "basket_aranjuez.png").write_bytes(b"logo")
            (photos / "AlevMasc2AnoNegro.jpg").write_bytes(b"old-photo")
            selected = root / "downloads" / "current.zip"
            selected.parent.mkdir()
            with zipfile.ZipFile(selected, "w") as archive:
                archive.writestr("AlevFem2AnoBlanco.jpg", b"new-photo")
            repository = PhotoRepository(root_dir=root, photos_dir=photos, cache_dir=root / "cache", fallback_name="basket_aranjuez.png", photos_zip_available=True, photos_zip_path=selected)
            found = repository.resolve_for_team(TeamConfig("alevin", "Alev\u00edn Femenino 2\u00ba a\u00f1o Blanco"))
            self.assertFalse(found.fallback)
            self.assertEqual(found.path.read_bytes(), b"new-photo")
            missing = repository.resolve_for_team(TeamConfig("alevin", "Alev\u00edn Masculino 2\u00ba a\u00f1o Negro"))
            self.assertTrue(missing.fallback)
            self.assertEqual(missing.path.read_bytes(), b"logo")

    def test_missing_selected_zip_never_uses_old_archive(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "basket_aranjuez.png").write_bytes(b"logo")
            with zipfile.ZipFile(root / "last-week.zip", "w") as archive:
                archive.writestr("AlevFem2AnoBlanco.jpg", b"old-photo")
            repository = PhotoRepository(root_dir=root, photos_dir=root, cache_dir=root / "cache", fallback_name="basket_aranjuez.png", photos_zip_available=True, photos_zip_path=root / "missing.zip")
            self.assertTrue(repository.resolve_for_team(TeamConfig("alevin", "Alev\u00edn Femenino 2\u00ba a\u00f1o Blanco")).fallback)


if __name__ == "__main__":
    unittest.main()
