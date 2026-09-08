from __future__ import annotations

from dataclasses import dataclass
from datetime import date
import logging
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from config import AppConfig

from .models import RenderMode


CANVAS_SIZE = (1080, 1350)
RENDER_SCALE = 1
DEFAULT_BG_COLOR = (253, 207, 33, 255)
DEFAULT_BAND_COLOR = (6, 5, 3, 255)
DEFAULT_BAND_TOP_RATIO = 513 / 1350
DEFAULT_BAND_BOTTOM_RATIO = 837 / 1350
TITLE_TOP_OFFSET = 6
TITLE_HEIGHT = 116
DATE_TOP_OFFSET = 176
DATE_HEIGHT = 104
RIGHT_LOGO_BOX = (706, 1164, 1038, 1286)
TITLE_MAX_WIDTH = 780
DATE_MAX_WIDTH = 900
TITLE_FONT_SIZE = 112
DATE_OUTLINE_FONT_SIZE = 108
DATE_MONTH_FONT_SIZE = 84
MONTH_GAP = 34
SEPARATOR_BASELINE_SHIFT = -6
MONTH_VERTICAL_SHIFT = -10
MONTH_NAMES = {
    1: "ENERO",
    2: "FEBRERO",
    3: "MARZO",
    4: "ABRIL",
    5: "MAYO",
    6: "JUNIO",
    7: "JULIO",
    8: "AGOSTO",
    9: "SEPTIEMBRE",
    10: "OCTUBRE",
    11: "NOVIEMBRE",
    12: "DICIEMBRE",
}


@dataclass(frozen=True, slots=True)
class CoverSegment:
    text: str
    style: str
    gap_after: int = 0
    baseline_shift: int = 0


@dataclass(frozen=True, slots=True)
class CoverStyle:
    background_color: tuple[int, int, int, int]
    band_color: tuple[int, int, int, int]
    band_top: int
    band_bottom: int


@dataclass(frozen=True, slots=True)
class MeasuredCoverSegment:
    segment: CoverSegment
    font: ImageFont.ImageFont
    width: int
    top_extent: int
    bottom_extent: int
    stroke_width: int


def _scale_box(box: tuple[int, int, int, int], scale: int) -> tuple[int, int, int, int]:
    return tuple(value * scale for value in box)


def _load_font(font_path: Path, size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    try:
        return ImageFont.truetype(str(font_path), size=size)
    except OSError:
        return ImageFont.load_default()


def _measure_text(
    draw: ImageDraw.ImageDraw,
    text: str,
    font: ImageFont.ImageFont,
    stroke_width: int = 0,
) -> tuple[int, int]:
    left, top, right, bottom = draw.textbbox((0, 0), text or " ", font=font, stroke_width=stroke_width)
    return right - left, bottom - top


def _get_font_metrics(font: ImageFont.ImageFont) -> tuple[int, int]:
    if hasattr(font, "getmetrics"):
        ascent, descent = font.getmetrics()
        return int(ascent), int(descent)

    size = int(getattr(font, "size", 16))
    return size, max(1, round(size * 0.25))


def _sample_cover_style(example_path: Path | None, logger: logging.Logger) -> CoverStyle:
    if example_path is None or not example_path.exists():
        if example_path is not None:
            logger.warning("No se encontro la referencia de portada en %s. Se usan proporciones por defecto.", example_path)
        band_top = round(CANVAS_SIZE[1] * DEFAULT_BAND_TOP_RATIO)
        band_bottom = round(CANVAS_SIZE[1] * DEFAULT_BAND_BOTTOM_RATIO)
        return CoverStyle(DEFAULT_BG_COLOR, DEFAULT_BAND_COLOR, band_top, band_bottom)

    with Image.open(example_path) as raw_image:
        image = raw_image.convert("RGBA")
        width, height = image.size
        center_x = width // 2
        dark_rows = []
        for y in range(height):
            red, green, blue, _ = image.getpixel((center_x, y))
            if red < 24 and green < 24 and blue < 24:
                dark_rows.append(y)

        if dark_rows:
            band_top_ratio = min(dark_rows) / height
            band_bottom_ratio = max(dark_rows) / height
        else:
            band_top_ratio = DEFAULT_BAND_TOP_RATIO
            band_bottom_ratio = DEFAULT_BAND_BOTTOM_RATIO

        background_color = image.getpixel((width // 2, max(height // 8, 1)))
        band_color = image.getpixel((width // 2, min(height // 2, height - 1)))

    return CoverStyle(
        background_color=background_color,
        band_color=band_color,
        band_top=round(CANVAS_SIZE[1] * band_top_ratio),
        band_bottom=round(CANVAS_SIZE[1] * band_bottom_ratio),
    )


def build_cover_date_segments(dates: list[date]) -> list[CoverSegment]:
    unique_dates = sorted({current for current in dates})
    if not unique_dates:
        return []

    if len({(current.year, current.month) for current in unique_dates}) == 1:
        month_label = MONTH_NAMES[unique_dates[0].month]
        segments: list[CoverSegment] = []
        for index, current in enumerate(unique_dates):
            if index > 0:
                separator = " y " if index == len(unique_dates) - 1 else ", "
                segments.append(CoverSegment(separator, "outline", baseline_shift=SEPARATOR_BASELINE_SHIFT))
            gap_after = MONTH_GAP if index == len(unique_dates) - 1 else 0
            segments.append(CoverSegment(str(current.day), "outline", gap_after=gap_after))
        segments.append(CoverSegment(month_label, "solid", baseline_shift=MONTH_VERTICAL_SHIFT))
        return segments

    segments = []
    for index, current in enumerate(unique_dates):
        if index > 0:
            separator = " y " if index == len(unique_dates) - 1 else ", "
            segments.append(CoverSegment(separator, "outline", baseline_shift=SEPARATOR_BASELINE_SHIFT))
        segments.append(CoverSegment(str(current.day), "outline", gap_after=MONTH_GAP))
        segments.append(CoverSegment(MONTH_NAMES[current.month], "solid", baseline_shift=MONTH_VERTICAL_SHIFT))
    return segments


def _fit_font_for_width(
    draw: ImageDraw.ImageDraw,
    text: str,
    font_path: Path,
    max_width: int,
    base_size: int,
    min_size: int,
    stroke_width: int = 0,
) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    for font_size in range(base_size, min_size - 1, -2):
        font = _load_font(font_path, font_size)
        width, _ = _measure_text(draw, text, font, stroke_width=stroke_width)
        if width <= max_width:
            return font
    return _load_font(font_path, min_size)


def _measure_segments(
    draw: ImageDraw.ImageDraw,
    segments: list[CoverSegment],
    bold_font: ImageFont.ImageFont,
    italic_font: ImageFont.ImageFont,
    stroke_width: int,
) -> tuple[int, int, int, list[MeasuredCoverSegment]]:
    measured_segments: list[MeasuredCoverSegment] = []
    total_width = 0
    min_top_extent = 0
    max_bottom_extent = 0

    for segment in segments:
        font = italic_font if segment.style == "outline" else bold_font
        segment_stroke_width = stroke_width if segment.style == "outline" else 0
        width, _ = _measure_text(draw, segment.text, font, stroke_width=segment_stroke_width)
        ascent, descent = _get_font_metrics(font)
        top_extent = -(ascent + segment_stroke_width) + segment.baseline_shift
        bottom_extent = descent + segment_stroke_width + segment.baseline_shift
        measured_segments.append(
            MeasuredCoverSegment(
                segment=segment,
                font=font,
                width=width,
                top_extent=top_extent,
                bottom_extent=bottom_extent,
                stroke_width=segment_stroke_width,
            )
        )
        total_width += width + segment.gap_after
        min_top_extent = min(min_top_extent, top_extent)
        max_bottom_extent = max(max_bottom_extent, bottom_extent)

    return total_width, min_top_extent, max_bottom_extent, measured_segments


def _build_date_fonts(
    bold_font_path: Path,
    italic_font_path: Path,
) -> tuple[ImageFont.ImageFont, ImageFont.ImageFont, int]:
    outline_size = DATE_OUTLINE_FONT_SIZE * RENDER_SCALE
    month_size = DATE_MONTH_FONT_SIZE * RENDER_SCALE
    stroke_width = max(2, round(outline_size * 0.024))
    return _load_font(bold_font_path, month_size), _load_font(italic_font_path, outline_size), stroke_width


def _draw_centered_segments(
    draw: ImageDraw.ImageDraw,
    box: tuple[int, int, int, int],
    segments: list[CoverSegment],
    bold_font: ImageFont.ImageFont,
    italic_font: ImageFont.ImageFont,
    solid_fill: tuple[int, int, int, int],
    outline_fill: tuple[int, int, int, int],
    outline_stroke_fill: tuple[int, int, int, int],
    stroke_width: int,
) -> None:
    total_width, min_top_extent, max_bottom_extent, measured_segments = _measure_segments(
        draw,
        segments,
        bold_font,
        italic_font,
        stroke_width,
    )
    current_x = box[0] + (box[2] - box[0] - total_width) // 2
    line_height = max_bottom_extent - min_top_extent
    baseline_y = box[1] + (box[3] - box[1] - line_height) // 2 - min_top_extent

    for item in measured_segments:
        segment = item.segment
        ascent, _ = _get_font_metrics(item.font)
        text_y = baseline_y - (ascent + item.stroke_width) + segment.baseline_shift
        if segment.style == "outline":
            draw.text(
                (current_x, text_y),
                segment.text,
                font=item.font,
                fill=outline_fill,
                stroke_width=item.stroke_width,
                stroke_fill=outline_stroke_fill,
            )
        else:
            draw.text(
                (current_x, text_y),
                segment.text,
                font=item.font,
                fill=solid_fill,
            )
        current_x += item.width + segment.gap_after


def _paste_logo(canvas: Image.Image, logo_path: Path, box: tuple[int, int, int, int]) -> None:
    with Image.open(logo_path) as raw_logo:
        logo = raw_logo.convert("RGBA")
        max_width = box[2] - box[0]
        max_height = box[3] - box[1]
        logo.thumbnail((max_width, max_height), Image.Resampling.LANCZOS)
        offset_x = box[0] + (max_width - logo.width) // 2
        offset_y = box[1] + (max_height - logo.height) // 2
        canvas.alpha_composite(logo, (offset_x, offset_y))


def _apply_cover_basket_overlay(canvas: Image.Image, overlay_path: Path, logger: logging.Logger) -> None:
    if not overlay_path.exists():
        logger.warning("No se encontro el overlay de portada de Basket Aranjuez en %s", overlay_path)
        return

    with Image.open(overlay_path) as raw_overlay:
        overlay = raw_overlay.convert("RGBA")
        if overlay.size != canvas.size:
            overlay = overlay.resize(canvas.size, Image.Resampling.LANCZOS)
        canvas.alpha_composite(overlay)


def render_cover(
    output_dir: Path,
    config: AppConfig,
    render_mode: RenderMode,
    dates: list[date],
    logger: logging.Logger,
) -> Path | None:
    if not dates:
        logger.warning("No hay fechas validas para generar la portada.")
        return None
    if config.cover_font_bold_path is None or not config.cover_font_bold_path.exists():
        logger.error("No se encontro la fuente bold de portada en %s", config.cover_font_bold_path)
        return None
    if config.cover_font_italic_path is None or not config.cover_font_italic_path.exists():
        logger.error("No se encontro la fuente italic de portada en %s", config.cover_font_italic_path)
        return None

    render_size = (CANVAS_SIZE[0] * RENDER_SCALE, CANVAS_SIZE[1] * RENDER_SCALE)
    style = _sample_cover_style(config.cover_example_path, logger)
    canvas = Image.new("RGBA", render_size, style.background_color)
    draw = ImageDraw.Draw(canvas)
    band_top = style.band_top * RENDER_SCALE
    band_bottom = style.band_bottom * RENDER_SCALE
    draw.rectangle((0, band_top, render_size[0], band_bottom), fill=style.band_color)

    title_text = "HORARIOS" if render_mode == "horarios" else "RESULTADOS"
    title_box = _scale_box((120, style.band_top + TITLE_TOP_OFFSET, 960, style.band_top + TITLE_TOP_OFFSET + TITLE_HEIGHT), RENDER_SCALE)
    title_font = _fit_font_for_width(
        draw,
        title_text,
        config.cover_font_bold_path,
        TITLE_MAX_WIDTH * RENDER_SCALE,
        base_size=TITLE_FONT_SIZE * RENDER_SCALE,
        min_size=68 * RENDER_SCALE,
    )
    title_width, title_height = _measure_text(draw, title_text, title_font)
    title_x = title_box[0] + (title_box[2] - title_box[0] - title_width) // 2
    title_y = title_box[1] + (title_box[3] - title_box[1] - title_height) // 2
    draw.text((title_x, title_y), title_text, font=title_font, fill=style.background_color)

    date_segments = build_cover_date_segments(dates)
    date_box = _scale_box((90, style.band_top + DATE_TOP_OFFSET, 990, style.band_top + DATE_TOP_OFFSET + DATE_HEIGHT), RENDER_SCALE)
    bold_font, italic_font, stroke_width = _build_date_fonts(
        config.cover_font_bold_path,
        config.cover_font_italic_path,
    )
    _draw_centered_segments(
        draw,
        date_box,
        date_segments,
        bold_font,
        italic_font,
        solid_fill=style.background_color,
        outline_fill=style.band_color,
        outline_stroke_fill=style.background_color,
        stroke_width=stroke_width,
    )

    cover_overlay_path = config.cover_basket_logo_path or (config.logos_dir / "cover_ba_logo.png")
    _apply_cover_basket_overlay(canvas, cover_overlay_path, logger)

    if config.clupik_logo_path is not None and config.clupik_logo_path.exists():
        _paste_logo(canvas, config.clupik_logo_path, _scale_box(RIGHT_LOGO_BOX, RENDER_SCALE))
    else:
        logger.warning("No se encontro el logo de Clupik para la portada en %s", config.clupik_logo_path)

    output_path = output_dir / "portada.png"
    canvas.save(output_path, format="PNG", optimize=True, compress_level=6)
    return output_path






