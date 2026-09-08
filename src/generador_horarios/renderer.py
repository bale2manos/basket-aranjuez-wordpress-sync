from __future__ import annotations

import logging
import re
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

from config import AppConfig

from .layout import (
    BLACK,
    CANVAS_SIZE,
    TEXT_BLACK,
    WHITE,
    YELLOW,
    MatchSlot,
    fit_text_block,
    get_match_slots,
    measure_text,
)
from .models import MatchRecord, PageSpec
from .normalization import format_schedule_line


DEFAULT_LOGO_SCALE = 0.74
BASKET_LOGO_SCALE = 0.94
RESULTS_TEXT_VERTICAL_OFFSET = -6
TEAM_TEXT_LINE_SPACING = 2
LOSER_BOX_ALPHA = 89
TRANSLUCENT_YELLOW = (247, 207, 35, LOSER_BOX_ALPHA)
RENDER_SCALE = 2
BACKGROUND_BLUR_RADIUS = 0.8


def _scale_box(box: tuple[int, int, int, int], scale: int) -> tuple[int, int, int, int]:
    return tuple(value * scale for value in box)


def _scale_slot(slot: MatchSlot, scale: int) -> MatchSlot:
    return MatchSlot(
        left_logo_box=_scale_box(slot.left_logo_box, scale),
        right_logo_box=_scale_box(slot.right_logo_box, scale),
        left_team_box=_scale_box(slot.left_team_box, scale),
        vs_box=_scale_box(slot.vs_box, scale),
        right_team_box=_scale_box(slot.right_team_box, scale),
        info_box=_scale_box(slot.info_box, scale),
    )


def _cover_image(image: Image.Image, size: tuple[int, int]) -> Image.Image:
    src_width, src_height = image.size
    dst_width, dst_height = size
    scale = max(dst_width / src_width, dst_height / src_height)
    resized = image.resize(
        (int(src_width * scale), int(src_height * scale)),
        Image.Resampling.LANCZOS,
    )
    left = max((resized.width - dst_width) // 2, 0)
    top = max((resized.height - dst_height) // 2, 0)
    return resized.crop((left, top, left + dst_width, top + dst_height))


def _load_background(background_path: Path, logger: logging.Logger, size: tuple[int, int]) -> Image.Image:
    if background_path.exists():
        with Image.open(background_path) as image:
            grayscale = image.convert("L").filter(ImageFilter.GaussianBlur(BACKGROUND_BLUR_RADIUS)).convert("RGBA")
            return _cover_image(grayscale, size)

    logger.warning("No se encontro el fondo en %s. Se usa un fondo gris.", background_path)
    return Image.new("RGBA", size, "#d9d9d9")


def _draw_logo_circle(draw: ImageDraw.ImageDraw, box: tuple[int, int, int, int]) -> None:
    draw.ellipse(box, fill=WHITE)


def _same_path(left: Path | None, right: Path | None) -> bool:
    if left is None or right is None:
        return False
    return left.resolve(strict=False) == right.resolve(strict=False)


def _draw_rounded_fill(
    canvas: Image.Image,
    draw: ImageDraw.ImageDraw,
    box: tuple[int, int, int, int],
    radius: int,
    fill: str | tuple[int, int, int, int],
) -> None:
    if isinstance(fill, tuple) and len(fill) == 4 and fill[3] < 255:
        overlay = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
        overlay_draw = ImageDraw.Draw(overlay)
        overlay_draw.rounded_rectangle(box, radius=radius, fill=fill)
        canvas.alpha_composite(overlay)
        return
    draw.rounded_rectangle(box, radius=radius, fill=fill)


def _paste_logo(
    canvas: Image.Image,
    logo_path: Path | None,
    circle_box: tuple[int, int, int, int],
    logger: logging.Logger,
    warning_label: str,
    *,
    draw_circle: bool = True,
    scale: float = DEFAULT_LOGO_SCALE,
) -> bool:
    if draw_circle:
        _draw_logo_circle(ImageDraw.Draw(canvas), circle_box)
    if logo_path is None or not logo_path.exists():
        logger.warning("%s: falta logo en %s", warning_label, logo_path or "<vacio>")
        return False

    with Image.open(logo_path) as raw_logo:
        logo = raw_logo.convert("RGBA")
        max_width = int((circle_box[2] - circle_box[0]) * scale)
        max_height = int((circle_box[3] - circle_box[1]) * scale)
        logo.thumbnail((max_width, max_height), Image.Resampling.LANCZOS)
        offset_x = circle_box[0] + (circle_box[2] - circle_box[0] - logo.width) // 2
        offset_y = circle_box[1] + (circle_box[3] - circle_box[1] - logo.height) // 2
        canvas.alpha_composite(logo, (offset_x, offset_y))
    return True


def _draw_multiline_centered(
    draw: ImageDraw.ImageDraw,
    box: tuple[int, int, int, int],
    text: str,
    font_path: Path,
    fill: str,
    stroke_fill: str | None = None,
    stroke_width: int = 0,
    base_size: int = 28,
    vertical_offset: int = 0,
    line_spacing: int = 0,
) -> None:
    max_width = box[2] - box[0] - 20
    max_height = box[3] - box[1] - 12
    fitted = fit_text_block(
        draw,
        text,
        font_path=font_path,
        max_width=max_width,
        max_height=max_height,
        base_size=base_size,
        min_size=18,
        max_lines=3,
    )

    total_height = fitted.line_height * len(fitted.lines) + max(0, len(fitted.lines) - 1) * line_spacing
    current_y = box[1] + (box[3] - box[1] - total_height) // 2 + vertical_offset
    for line in fitted.lines:
        line_width, line_height = measure_text(draw, line, fitted.font)
        current_x = box[0] + (box[2] - box[0] - line_width) // 2
        draw.text(
            (current_x, current_y),
            line,
            font=fitted.font,
            fill=fill,
            stroke_width=stroke_width,
            stroke_fill=stroke_fill,
        )
        current_y += line_height + line_spacing


def _parse_result_scores(result_text: str | None) -> tuple[int, int] | None:
    if not result_text:
        return None
    match = re.search(r"(\d+)\s*[-\u2013\u2014\u2212]\s*(\d+)", result_text)
    if match is None:
        return None
    return int(match.group(1)), int(match.group(2))


def _resolve_team_box_fills(match: MatchRecord) -> tuple[str | tuple[int, int, int, int], str | tuple[int, int, int, int]]:
    left_fill: str | tuple[int, int, int, int] = YELLOW
    right_fill: str | tuple[int, int, int, int] = YELLOW
    if match.render_mode != "resultados":
        return left_fill, right_fill

    scores = _parse_result_scores(match.result_text or match.middle_text)
    if scores is None:
        return left_fill, right_fill

    local_score, visitante_score = scores
    if local_score < visitante_score:
        left_fill = TRANSLUCENT_YELLOW
    elif visitante_score < local_score:
        right_fill = TRANSLUCENT_YELLOW
    return left_fill, right_fill


def _draw_match(
    canvas: Image.Image,
    draw: ImageDraw.ImageDraw,
    slot: MatchSlot,
    match: MatchRecord,
    config: AppConfig,
    logger: logging.Logger,
    scale: int,
) -> bool:
    left_fill, right_fill = _resolve_team_box_fills(match)
    _draw_rounded_fill(canvas, draw, slot.left_team_box, radius=10 * scale, fill=left_fill)
    _draw_rounded_fill(canvas, draw, slot.vs_box, radius=10 * scale, fill=BLACK)
    _draw_rounded_fill(canvas, draw, slot.right_team_box, radius=10 * scale, fill=right_fill)
    if match.render_mode != "resultados":
        _draw_rounded_fill(canvas, draw, slot.info_box, radius=18 * scale, fill=BLACK)

    left_is_basket = _same_path(match.local_logo_path, config.basket_logo_path)
    right_is_basket = _same_path(match.visitante_logo_path, config.basket_logo_path)

    left_logo_ok = _paste_logo(
        canvas,
        match.local_logo_path,
        slot.left_logo_box,
        logger,
        f"Fila {match.row_index} logo local",
        draw_circle=not left_is_basket,
        scale=BASKET_LOGO_SCALE if left_is_basket else DEFAULT_LOGO_SCALE,
    )
    right_logo_ok = _paste_logo(
        canvas,
        match.visitante_logo_path,
        slot.right_logo_box,
        logger,
        f"Fila {match.row_index} logo visitante",
        draw_circle=not right_is_basket,
        scale=BASKET_LOGO_SCALE if right_is_basket else DEFAULT_LOGO_SCALE,
    )

    _draw_multiline_centered(
        draw,
        slot.left_team_box,
        match.local_display,
        config.font_path,
        fill=TEXT_BLACK,
        stroke_fill=WHITE,
        stroke_width=2 * scale,
        base_size=28 * scale,
        vertical_offset=-4 * scale,
        line_spacing=TEAM_TEXT_LINE_SPACING * scale,
    )
    _draw_multiline_centered(
        draw,
        slot.right_team_box,
        match.visitante_display,
        config.font_path,
        fill=TEXT_BLACK,
        stroke_fill=WHITE,
        stroke_width=2 * scale,
        base_size=28 * scale,
        vertical_offset=-4 * scale,
        line_spacing=TEAM_TEXT_LINE_SPACING * scale,
    )
    _draw_multiline_centered(
        draw,
        slot.vs_box,
        match.middle_text,
        config.font_path,
        fill=WHITE,
        base_size=(34 if match.render_mode == "resultados" else 30) * scale,
        vertical_offset=(RESULTS_TEXT_VERTICAL_OFFSET if match.render_mode == "resultados" else 0) * scale,
    )

    if match.render_mode != "resultados":
        schedule_line = format_schedule_line(match.fecha, match.hora, match.venue_display)
        _draw_multiline_centered(
            draw,
            slot.info_box,
            schedule_line,
            config.font_path,
            fill=WHITE,
            base_size=21 * scale,
        )

    return left_logo_ok and right_logo_ok


def _apply_header_overlay(canvas: Image.Image, overlay_path: Path, logger: logging.Logger) -> None:
    if not overlay_path.exists():
        logger.warning("No se encontro el header overlay en %s", overlay_path)
        return

    with Image.open(overlay_path) as overlay_image:
        overlay = overlay_image.convert("RGBA")
        if overlay.size != canvas.size:
            overlay = overlay.resize(canvas.size, Image.Resampling.LANCZOS)
        canvas.alpha_composite(overlay)


def render_page(page: PageSpec, config: AppConfig, logger: logging.Logger) -> None:
    render_size = (CANVAS_SIZE[0] * RENDER_SCALE, CANVAS_SIZE[1] * RENDER_SCALE)
    canvas = _load_background(config.background_image_path, logger, render_size)
    draw = ImageDraw.Draw(canvas)

    for slot, match in zip(get_match_slots(len(page.matches)), page.matches):
        scaled_slot = _scale_slot(slot, RENDER_SCALE)
        logos_ok = _draw_match(canvas, draw, scaled_slot, match, config, logger, RENDER_SCALE)
        if match.status != "skipped":
            match.status = "rendered" if logos_ok else "rendered_missing_logo"

    _apply_header_overlay(canvas, config.header_overlay_path, logger)
    final_canvas = canvas.resize(CANVAS_SIZE, Image.Resampling.LANCZOS)
    page.output_path.parent.mkdir(parents=True, exist_ok=True)
    final_canvas.save(page.output_path, format="PNG", optimize=True, compress_level=6)

