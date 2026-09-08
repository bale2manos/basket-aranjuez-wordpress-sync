from __future__ import annotations

import argparse
import site
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent
SRC_DIR = ROOT_DIR / "src"
USER_SITE = Path(site.getusersitepackages())
for candidate in (SRC_DIR, USER_SITE):
    candidate_str = str(candidate)
    if candidate_str not in sys.path:
        sys.path.insert(0, candidate_str)

from config import AppConfig, RenderMode, apply_overrides, default_config
from generador_horarios.pipeline import run_pipeline


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Genera imágenes de jornada o resultados de Basket Aranjuez.")
    parser.add_argument("--mode", choices=["horarios", "resultados"])
    parser.add_argument("--excel-path", type=Path)
    parser.add_argument("--background-image-path", type=Path)
    parser.add_argument("--header-overlay-path", type=Path)
    parser.add_argument("--logos-dir", type=Path)
    parser.add_argument("--font-path", type=Path)
    parser.add_argument("--output-root-dir", type=Path)
    parser.add_argument("--basket-logo-path", type=Path)
    parser.add_argument("--club-logo-map-path", type=Path)
    parser.add_argument("--venue-map-path", type=Path)
    parser.add_argument("--results-doc-path", type=Path)
    parser.add_argument("--results-overrides-path", type=Path)
    parser.add_argument("--max-per-page", type=int)
    return parser


def resolve_path(value: Path | None) -> Path | None:
    if value is None:
        return None
    return value.expanduser().resolve()


def build_runtime_config(args: argparse.Namespace) -> AppConfig:
    config = default_config()
    mode = args.mode if args.mode is not None else config.mode
    return apply_overrides(
        config,
        mode=mode,
        excel_path=resolve_path(args.excel_path),
        background_image_path=resolve_path(args.background_image_path),
        header_overlay_path=resolve_path(args.header_overlay_path),
        logos_dir=resolve_path(args.logos_dir),
        font_path=resolve_path(args.font_path),
        output_root_dir=resolve_path(args.output_root_dir),
        basket_logo_path=resolve_path(args.basket_logo_path),
        club_logo_map_path=resolve_path(args.club_logo_map_path),
        venue_map_path=resolve_path(args.venue_map_path),
        results_doc_path=resolve_path(args.results_doc_path),
        results_overrides_path=resolve_path(args.results_overrides_path),
        max_per_page=args.max_per_page,
    )


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    result = run_pipeline(build_runtime_config(args))
    print(f"Modo: {result.render_mode}")
    print(f"Carpeta generada: {result.output_dir}")
    print(f"Imagenes creadas: {len(result.images)}")
    print(f"Partidos procesados: {result.rendered_count}")
    print(f"Partidos omitidos: {result.skipped_count}")
    print(f"Summary: {result.summary_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
