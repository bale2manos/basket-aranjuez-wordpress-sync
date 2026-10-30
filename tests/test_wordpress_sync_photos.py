from pathlib import Path
import site
import sys
from tempfile import TemporaryDirectory
import unittest
import zipfile

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
USER_SITE = Path(site.getusersitepackages())
for candidate in (ROOT, SRC, USER_SITE):
    candidate_str = str(candidate)
    if candidate_str not in sys.path:
        sys.path.insert(0, candidate_str)

from basket_wordpress_sync.models import TeamConfig
from basket_wordpress_sync.photo_repository import PhotoRepository


class PhotoRepositoryTests(unittest.TestCase):
    def test_resolve_uses_zip_photo_when_alias_matches(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            photos_dir = root / "resultados" / "fotos"
            photos_dir.mkdir(parents=True)
            (photos_dir / "basket_aranjuez.png").write_bytes(b"png")
            with zipfile.ZipFile(photos_dir / "fotos.zip", "w") as archive:
                archive.writestr("AlevFem1AñoBlanco.jpeg", b"jpeg")

            repository = PhotoRepository(
                root_dir=root,
                photos_dir=photos_dir,
                cache_dir=root / "cache",
                fallback_name="basket_aranjuez.png",
                photos_zip_path=photos_dir / "fotos.zip",
            )
            team = TeamConfig(
                section="alevin",
                web_block_name="Alevín femenino blanco primer año",
                photo_aliases=["AlevFem1AñoBlanco"],
            )

            resolved = repository.resolve_for_team(team)

            self.assertFalse(resolved.fallback)
            self.assertTrue(resolved.path.exists())
            self.assertIn("alevfem1anoblanco", resolved.path.name)

    def test_resolve_falls_back_to_shield_when_no_match(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            photos_dir = root / "resultados" / "fotos"
            photos_dir.mkdir(parents=True)
            fallback = photos_dir / "basket_aranjuez.png"
            fallback.write_bytes(b"png")

            repository = PhotoRepository(
                root_dir=root,
                photos_dir=photos_dir,
                cache_dir=root / "cache",
                fallback_name="basket_aranjuez.png",
            )
            team = TeamConfig(section="senior", web_block_name="Senior Femenino B")

            resolved = repository.resolve_for_team(team)

            self.assertTrue(resolved.fallback)
            self.assertTrue(resolved.path.exists())
            self.assertIn("basket_aranjuez", resolved.path.name)

    def test_resolve_uses_web_name_and_aliases_together(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            photos_dir = root / "photos"
            photos_dir.mkdir()
            archive_path = photos_dir / "week.zip"
            with zipfile.ZipFile(archive_path, "w") as archive:
                archive.writestr("InfFemNegro.jpeg", b"jpeg")
            repository = PhotoRepository(
                root_dir=root, photos_dir=photos_dir, cache_dir=root / "cache",
                fallback_name="basket_aranjuez.png", photos_zip_path=archive_path,
            )
            team = TeamConfig(
                section="infantil_femenino", web_block_name="Infantil Femenino 2º año Negro",
                photo_aliases=["InfFem2AñoNegro", "InfFemNegro"],
            )
            resolved = repository.resolve_for_team(team)
            self.assertFalse(resolved.fallback)
            self.assertEqual(resolved.source_name, "InfFemNegro.jpeg")

    def test_ambiguous_photo_matches_are_rejected(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            photos_dir = root / "photos"
            photos_dir.mkdir()
            archive_path = photos_dir / "week.zip"
            with zipfile.ZipFile(archive_path, "w") as archive:
                archive.writestr("InfFemNegro.jpeg", b"one")
                archive.writestr("InfFemNegro.jpg", b"two")
            repository = PhotoRepository(
                root_dir=root, photos_dir=photos_dir, cache_dir=root / "cache",
                fallback_name="basket_aranjuez.png", photos_zip_path=archive_path,
            )
            team = TeamConfig(section="infantil_femenino", web_block_name="Infantil Femenino 2º año Negro",
                              photo_aliases=["InfFemNegro"])
            with self.assertRaisesRegex(ValueError, "ambigua"):
                repository.resolve_for_team(team)


if __name__ == "__main__":
    unittest.main()
