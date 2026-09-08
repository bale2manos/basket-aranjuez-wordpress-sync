"""Safe starting configuration for the public WordPress sync distribution."""

from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Literal


BASE_DIR = Path(__file__).resolve().parent
RenderMode = Literal["horarios", "resultados"]


@dataclass(slots=True)
class AppConfig:
    excel_path: Path = BASE_DIR / "input" / "Partidos.xlsx"
    background_image_path: Path = BASE_DIR / "foto.png"
    header_overlay_path: Path = BASE_DIR / "header.png"
    logos_dir: Path = BASE_DIR / "logos"
    font_path: Path = BASE_DIR / "fonts" / "Poppins-Bold.ttf"
    output_root_dir: Path = BASE_DIR / "output"
    basket_logo_path: Path = BASE_DIR / "resultados" / "fotos" / "basket_aranjuez.png"
    club_logo_map_path: Path = BASE_DIR / "data" / "club_logo_map.csv"
    venue_map_path: Path = BASE_DIR / "data" / "venue_map.csv"
    cover_example_path: Path | None = None
    cover_font_bold_path: Path | None = None
    cover_font_italic_path: Path | None = None
    clupik_logo_path: Path | None = None
    cover_basket_logo_path: Path | None = None
    mode: RenderMode = "horarios"
    results_doc_path: Path | None = None
    results_overrides_path: Path | None = None
    max_per_page: int = 8
    wordpress_sync_docx_path: Path | None = None
    wordpress_sync_site_key: str = "basket-aranjuez"
    wordpress_sync_section_order: tuple[str, ...] = ()
    wordpress_sync_entry_urls: dict[str, str] = field(default_factory=dict)
    wordpress_sync_has_photos_zip: bool = False
    wordpress_sync_photos_zip_path: Path | None = None


def default_config() -> AppConfig:
    return AppConfig()


def apply_overrides(config: AppConfig, **overrides: object) -> AppConfig:
    return replace(config, **{key: value for key, value in overrides.items() if value is not None})
