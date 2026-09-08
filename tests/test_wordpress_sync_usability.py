from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

import yaml

from basket_wordpress_sync.backups import backup_configuration
from basket_wordpress_sync.preflight import run_preflight
from basket_wordpress_sync.settings import load_settings


ROOT = Path(__file__).resolve().parents[1]


class WordPressSyncUsabilityTests(unittest.TestCase):
    def test_unified_config_overrides_weekly_values(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config_dir = root / "config"
            config_dir.mkdir()
            for name in ("runtime.yaml", "teams.yaml", "selectors.yaml"):
                shutil.copy2(ROOT / "config" / name, config_dir / name)
            (root / "configuracion.yaml").write_text(
                yaml.safe_dump(
                    {
                        "docx_path": "resultados/esta-semana.docx",
                        "photos_zip_available": False,
                        "entry_urls": {"senior": "https://example.test/elementor"},
                    },
                    sort_keys=False,
                ),
                encoding="utf-8",
            )

            settings = load_settings(root, config_dir)

            self.assertEqual(settings.runtime.default_docx_path, (root / "resultados/esta-semana.docx").resolve())
            self.assertFalse(settings.runtime.photos_zip_available)
            self.assertEqual(settings.runtime.section_entry_urls["senior"], "https://example.test/elementor")

    def test_preflight_reports_missing_session_and_url(self) -> None:
        site = SimpleNamespace(storage_state_path=Path("state.json"))
        runtime = SimpleNamespace(
            section_entry_urls={"senior": ""},
            photos_zip_available=False,
            photos_zip_path=None,
            photos_dir=Path("photos"),
            photo_fallback_name="basket.png",
        )
        settings = SimpleNamespace(
            root_dir=Path(tempfile.gettempdir()) / "missing-wordpress-sync-test",
            runtime=runtime,
            selectors={"login": {}, "editor": {}, "preview": {}},
            site=lambda _site_key: site,
        )

        report = run_preflight(
            settings=settings,
            site_key="basket-aranjuez",
            docx_path=Path("missing.docx"),
            sections=["senior"],
        )

        self.assertFalse(report.ok)
        self.assertTrue(any("Word" in error for error in report.errors))
        self.assertTrue(any("sesion" in error for error in report.errors))
        self.assertTrue(any("URL" in error for error in report.errors))

    def test_backup_copies_only_configuration_files(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "config").mkdir()
            (root / "configuracion.yaml").write_text("site_key: test\n", encoding="utf-8")
            (root / "config" / "runtime.yaml").write_text("defaults: {}\n", encoding="utf-8")
            runtime = SimpleNamespace(backup_dir=Path("backups"))

            backup = backup_configuration(root_dir=root, runtime=runtime)

            self.assertTrue((backup / "configuracion.yaml").exists())
            self.assertTrue((backup / "runtime.yaml").exists())
            self.assertFalse((backup / "state.json").exists())


if __name__ == "__main__":
    unittest.main()
