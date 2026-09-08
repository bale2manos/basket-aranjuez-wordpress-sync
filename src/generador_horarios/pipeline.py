from __future__ import annotations

from collections.abc import Iterable
from datetime import date
import logging
from pathlib import Path

from config import AppConfig

from .assets import (
    bootstrap_club_logo_map,
    bootstrap_venue_map,
    ensure_directory,
    load_lookup_map,
    resolve_club_display_name,
    resolve_logo_path,
    resolve_venue_display_name,
)
from .cover_renderer import render_cover
from .excel_reader import load_matches
from .models import MatchRecord, PageSpec, PipelineResult
from .normalization import detect_match_type, derive_basket_display_name
from .renderer import render_page
from .results import (
    build_match_key,
    load_result_overrides,
    match_results_to_records,
    parse_results_doc,
    update_result_overrides,
)
from .summary_writer import write_summary


def _build_logger() -> logging.Logger:
    logger = logging.getLogger("generador_horarios")
    logger.setLevel(logging.INFO)
    logger.handlers.clear()
    logger.propagate = False

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(logging.Formatter("%(levelname)s - %(message)s"))
    logger.addHandler(console_handler)
    return logger


def _attach_file_logger(logger: logging.Logger, output_dir: Path) -> None:
    file_handler = logging.FileHandler(output_dir / "run.log", encoding="utf-8")
    file_handler.setFormatter(logging.Formatter("%(asctime)s | %(levelname)s | %(message)s"))
    logger.addHandler(file_handler)


def _close_logger(logger: logging.Logger) -> None:
    handlers = list(logger.handlers)
    for handler in handlers:
        handler.flush()
        handler.close()
        logger.removeHandler(handler)


def _chunked(items: list[MatchRecord], chunk_size: int) -> Iterable[list[MatchRecord]]:
    for start in range(0, len(items), chunk_size):
        yield items[start : start + chunk_size]


def _compute_jornada_dir(config: AppConfig, records: list[MatchRecord]) -> Path:
    available_dates = [record.fecha for record in records if record.fecha is not None]
    jornada_date = min(available_dates) if available_dates else date.today()
    return config.output_root_dir / f"jornada_{jornada_date.isoformat()}"


def _prepare_record(
    record: MatchRecord,
    config: AppConfig,
    club_map: dict[str, dict[str, str]],
    venue_map: dict[str, dict[str, str]],
    logger: logging.Logger,
) -> None:
    page_type, skip_reason = detect_match_type(record.local_original, record.visitante_original)
    if page_type is None:
        record.status = "skipped"
        record.skip_reason = skip_reason
        logger.warning("Fila %s omitida: %s", record.row_index, skip_reason)
        return

    record.page_type = page_type
    record.render_mode = config.mode
    record.middle_text = "PLAYOFFS" if config.mode == "horarios" and record.is_playoff else "VS"
    record.result_text = None
    record.result_source = "none"
    record.match_key = build_match_key(record.fecha, record.categoria, record.local_original, record.visitante_original)

    basket_original = record.local_original if page_type == "local" else record.visitante_original
    rival_original = record.visitante_original if page_type == "local" else record.local_original

    basket_display = derive_basket_display_name(record.categoria, basket_original)
    rival_display = resolve_club_display_name(rival_original, club_map)
    venue_display = resolve_venue_display_name(record.campo_original, venue_map)

    basket_logo_path = config.basket_logo_path if config.basket_logo_path.exists() else None
    rival_logo_path = resolve_logo_path(rival_original, club_map, config.logos_dir)

    if basket_logo_path is None:
        logger.warning("No se encontró el logo fijo de Basket Aranjuez en %s", config.basket_logo_path)
    if rival_logo_path is None:
        logger.warning("Fila %s: falta logo rival para '%s'", record.row_index, rival_original)

    if page_type == "local":
        record.local_display = basket_display
        record.visitante_display = rival_display
        record.local_logo_path = basket_logo_path
        record.visitante_logo_path = rival_logo_path
    else:
        record.local_display = rival_display
        record.visitante_display = basket_display
        record.local_logo_path = rival_logo_path
        record.visitante_logo_path = basket_logo_path

    record.venue_display = venue_display
    record.status = "ready"


def _apply_results_mode(records: list[MatchRecord], config: AppConfig, logger: logging.Logger) -> list[MatchRecord]:
    if config.results_doc_path is None:
        raise ValueError("El modo resultados requiere results_doc_path en config.py o CLI.")
    if not config.results_doc_path.exists():
        raise ValueError(f"No se encontró el documento de resultados: {config.results_doc_path}")

    entries = parse_results_doc(config.results_doc_path)
    logger.info("Resultados detectados en DOCX: %s", len(entries))

    overrides = load_result_overrides(config.results_overrides_path, logger)
    matched_entries = match_results_to_records(records, entries)

    missing_records: list[MatchRecord] = []
    matched_docx = 0
    matched_overrides = 0
    ready_records: list[MatchRecord] = []

    for record in records:
        override = overrides.get(record.match_key)
        if override is not None and override.result:
            record.result_text = override.result
            record.middle_text = override.result
            record.result_source = "override"
            matched_overrides += 1
            ready_records.append(record)
            continue

        entry = matched_entries.get(record.match_key)
        if entry is not None:
            record.result_text = f"{entry.local_score}-{entry.visitante_score}"
            record.middle_text = record.result_text
            record.result_source = "docx"
            matched_docx += 1
            ready_records.append(record)
            continue

        record.status = "skipped"
        record.skip_reason = "Resultado no encontrado"
        record.result_source = "missing"
        missing_records.append(record)
        logger.warning("Fila %s omitida en resultados: %s vs %s", record.row_index, record.local_original, record.visitante_original)

    update_result_overrides(config.results_overrides_path, missing_records, overrides)
    logger.info(
        "Resultados aplicados: %s desde DOCX, %s desde overrides, %s pendientes.",
        matched_docx,
        matched_overrides,
        len(missing_records),
    )
    return ready_records


def _build_pages(output_dir: Path, records: list[MatchRecord], max_per_page: int, render_mode: str) -> list[PageSpec]:
    pages: list[PageSpec] = []
    for page_type in ("local", "visitante"):
        page_records = sorted(
            [record for record in records if record.page_type == page_type],
            key=lambda item: item.sort_key,
        )
        for page_index, chunk in enumerate(_chunked(page_records, max_per_page), start=1):
            filename = f"{page_type}{page_index}.png"
            pages.append(
                PageSpec(
                    page_type=page_type,
                    page_index=page_index,
                    output_path=output_dir / filename,
                    matches=chunk,
                    render_mode=render_mode,
                )
            )
    return pages


def run_pipeline(config: AppConfig) -> PipelineResult:
    ensure_directory(config.output_root_dir)
    logger = _build_logger()
    try:
        logger.info("Modo activo: %s", config.mode)
        logger.info("Leyendo Excel desde %s", config.excel_path)

        records = load_matches(config.excel_path, logger)
        bootstrap_club_logo_map(config.club_logo_map_path, records, logger, config.logos_dir)
        bootstrap_venue_map(config.venue_map_path, records, logger)

        jornada_dir = _compute_jornada_dir(config, records)
        output_dir = jornada_dir / config.mode
        ensure_directory(output_dir)
        _attach_file_logger(logger, output_dir)

        club_map = load_lookup_map(config.club_logo_map_path)
        venue_map = load_lookup_map(config.venue_map_path)

        valid_records: list[MatchRecord] = []
        for record in records:
            if record.status == "skipped":
                continue
            _prepare_record(record, config, club_map, venue_map, logger)
            if record.status != "skipped":
                valid_records.append(record)

        if config.mode == "resultados":
            valid_records = _apply_results_mode(valid_records, config, logger)

        pages = _build_pages(output_dir, valid_records, config.max_per_page, config.mode)
        images: list[dict[str, object]] = []

        cover_dates = sorted({record.fecha for record in records if record.fecha is not None})
        cover_path = render_cover(output_dir, config, config.mode, cover_dates, logger)
        if cover_path is not None:
            images.append(
                {
                    "output_image": cover_path.name,
                    "page_type": "cover",
                    "page_index": None,
                    "match_count": 0,
                    "render_mode": config.mode,
                }
            )
            logger.info("Generada imagen %s", cover_path.name)
        for page in pages:
            try:
                render_page(page, config, logger)
                for match in page.matches:
                    match.output_image = page.output_path.name
                    match.page_index = page.page_index
                images.append(
                    {
                        "output_image": page.output_path.name,
                        "page_type": page.page_type,
                        "page_index": page.page_index,
                        "match_count": len(page.matches),
                        "render_mode": config.mode,
                    }
                )
                logger.info("Generada imagen %s", page.output_path.name)
            except Exception as exc:
                logger.exception("Error al renderizar %s: %s", page.output_path.name, exc)
                for match in page.matches:
                    match.status = "skipped"
                    match.skip_reason = f"Error de render: {exc}"
                    match.output_image = None
                    match.page_index = None

        summary_path = write_summary(
            output_dir=output_dir,
            source_excel=config.excel_path,
            images=images,
            records=records,
            render_mode=config.mode,
            results_doc_path=config.results_doc_path,
            results_overrides_path=config.results_overrides_path,
        )
        logger.info("Resumen generado en %s", summary_path)

        rendered_count = sum(record.status.startswith("rendered") for record in records)
        skipped_count = sum(record.status == "skipped" for record in records)
        return PipelineResult(
            output_dir=output_dir,
            summary_path=summary_path,
            images=images,
            rendered_count=rendered_count,
            skipped_count=skipped_count,
            render_mode=config.mode,
        )
    finally:
        _close_logger(logger)
