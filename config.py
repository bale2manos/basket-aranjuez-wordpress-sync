from __future__ import annotations

from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Literal


BASE_DIR = Path(__file__).resolve().parent
RenderMode = Literal["horarios", "resultados"]


@dataclass(slots=True)
class AppConfig:
    excel_path: Path
    background_image_path: Path
    header_overlay_path: Path
    logos_dir: Path
    font_path: Path
    output_root_dir: Path
    basket_logo_path: Path
    club_logo_map_path: Path
    venue_map_path: Path
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
    wordpress_sync_has_photos_zip: bool = True
    wordpress_sync_photos_zip_path: Path | None = None


def default_config() -> AppConfig:
    return AppConfig(
        excel_path=BASE_DIR / "input" / "Partidos CW23.xlsx",
        background_image_path=BASE_DIR / "foto.png",
        header_overlay_path=BASE_DIR / "header.png",
        logos_dir=BASE_DIR / "logos",
        font_path=BASE_DIR / "fonts" / "Poppins-Bold.ttf",
        output_root_dir=BASE_DIR / "output",
        basket_logo_path=BASE_DIR / "logos" / "basket_aranjuez.png",
        club_logo_map_path=BASE_DIR / "data" / "club_logo_map.csv",
        venue_map_path=BASE_DIR / "data" / "venue_map.csv",
        cover_example_path=BASE_DIR / "cover.png",
        cover_font_bold_path=BASE_DIR / "fonts" / "Poppins-Bold.ttf",
        cover_font_italic_path=BASE_DIR / "fonts" / "Poppins-BoldItalic.ttf",
        clupik_logo_path=BASE_DIR / "logos" / "Clupik-logo.png",
        cover_basket_logo_path=BASE_DIR / "logos" / "cover_ba_logo.png",
        mode="horarios",
        results_doc_path=BASE_DIR / "resultados" / "Crónicas Basket Aranjuez 6-7 de junio.docx",
        results_overrides_path=BASE_DIR / "data" / "result_overrides.json",
        max_per_page=8,
        wordpress_sync_docx_path=BASE_DIR / "resultados" / "Crónicas Basket Aranjuez 6-7 de junio.docx",
        wordpress_sync_site_key="basket-aranjuez",
        wordpress_sync_section_order=(
            "alevin",
            "infantil_femenino",
            #"infantil_masculino",
            "cadete",
            #"junior",
            #"sub22",
            "senior",
        ),
        wordpress_sync_entry_urls={
            "alevin": "https://basketaranjuez.com/wp-admin/post.php?post=7870&action=elementor",
            "infantil_femenino": "https://basketaranjuez.com/wp-admin/post.php?post=7864&action=elementor",
            #"infantil_masculino": "https://basketaranjuez.com/wp-admin/post.php?post=7779&action=elementor",
            "cadete": "https://basketaranjuez.com/wp-admin/post.php?post=7867&action=elementor",
            #"junior": "https://basketaranjuez.com/wp-admin/post.php?post=7723&action=elementor",
            #"sub22": "https://basketaranjuez.com/wp-admin/post.php?post=7721&action=elementor",
            "senior": "https://basketaranjuez.com/wp-admin/post.php?post=7868&action=elementor",
        },
        wordpress_sync_has_photos_zip=True,
        wordpress_sync_photos_zip_path=BASE_DIR / "resultados" / "fotos" / "crnicasyfotografasbasketaranjuez3031demayo.zip",
    )


def apply_overrides(config: AppConfig, **overrides: object) -> AppConfig:
    clean_overrides = {key: value for key, value in overrides.items() if value is not None}
    return replace(config, **clean_overrides)
