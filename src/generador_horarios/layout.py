from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from PIL import ImageDraw, ImageFont


CANVAS_SIZE = (1080, 1350)
MAX_MATCHES_PER_PAGE = 8
TOP_MARGIN = 182
BOTTOM_MARGIN = 40
ROW_GAP = 10
ROW_HEIGHT = 131
LEFT_LOGO_BOX = (18, 2, 106, 90)
RIGHT_LOGO_BOX = (974, 2, 1062, 90)
LEFT_TEAM_BOX = (96, 10, 456, 76)
VS_BOX = (456, 10, 624, 76)
RIGHT_TEAM_BOX = (624, 10, 984, 76)
INFO_BOX = (96, 72, 984, 118)

YELLOW = "#f7cf23"
BLACK = "#1e1e1e"
WHITE = "#ffffff"
TEXT_BLACK = "#111111"


@dataclass(slots=True)
class MatchSlot:
    left_logo_box: tuple[int, int, int, int]
    right_logo_box: tuple[int, int, int, int]
    left_team_box: tuple[int, int, int, int]
    vs_box: tuple[int, int, int, int]
    right_team_box: tuple[int, int, int, int]
    info_box: tuple[int, int, int, int]


@dataclass(slots=True)
class FittedText:
    lines: list[str]
    font: ImageFont.FreeTypeFont | ImageFont.ImageFont
    line_height: int


def _shift(box: tuple[int, int, int, int], offset_y: int) -> tuple[int, int, int, int]:
    left, top, right, bottom = box
    return left, top + offset_y, right, bottom + offset_y


def get_match_slots(count: int) -> list[MatchSlot]:
    count = min(count, MAX_MATCHES_PER_PAGE)
    slots: list[MatchSlot] = []
    for index in range(count):
        offset_y = TOP_MARGIN + index * (ROW_HEIGHT + ROW_GAP)
        slots.append(
            MatchSlot(
                left_logo_box=_shift(LEFT_LOGO_BOX, offset_y),
                right_logo_box=_shift(RIGHT_LOGO_BOX, offset_y),
                left_team_box=_shift(LEFT_TEAM_BOX, offset_y),
                vs_box=_shift(VS_BOX, offset_y),
                right_team_box=_shift(RIGHT_TEAM_BOX, offset_y),
                info_box=_shift(INFO_BOX, offset_y),
            )
        )
    return slots


def _load_font(font_path: Path, size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    try:
        return ImageFont.truetype(str(font_path), size=size)
    except OSError:
        return ImageFont.load_default()


def measure_text(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.ImageFont) -> tuple[int, int]:
    left, top, right, bottom = draw.textbbox((0, 0), text or " ", font=font, stroke_width=0)
    return right - left, bottom - top


def _split_long_word(
    draw: ImageDraw.ImageDraw,
    word: str,
    font: ImageFont.ImageFont,
    max_width: int,
) -> tuple[str, str]:
    if not word:
        return "", ""
    current = ""
    for character in word:
        candidate = f"{current}{character}"
        width, _ = measure_text(draw, candidate, font)
        if width <= max_width or not current:
            current = candidate
        else:
            break

    if len(current) == len(word):
        return current, ""
    if not current:
        return word[0], word[1:]
    return current, word[len(current):]


def wrap_text(
    draw: ImageDraw.ImageDraw,
    text: str,
    font: ImageFont.ImageFont,
    max_width: int,
    max_lines: int | None = None,
) -> list[str]:
    words = text.split()
    if not words:
        return [""]

    lines: list[str] = []
    current = ""
    queue = words[:]
    while queue:
        word = queue.pop(0)
        candidate = word if not current else f"{current} {word}"
        width, _ = measure_text(draw, candidate, font)
        if width <= max_width:
            current = candidate
            continue

        if current:
            lines.append(current)
            if max_lines is not None and len(lines) > max_lines:
                return lines
            current = ""
            queue.insert(0, word)
            continue

        head, tail = _split_long_word(draw, word, font, max_width)
        lines.append(head)
        if max_lines is not None and len(lines) > max_lines:
            return lines
        if tail:
            queue.insert(0, tail)

    if current:
        lines.append(current)
    return lines


def truncate_to_lines(
    draw: ImageDraw.ImageDraw,
    text: str,
    font: ImageFont.ImageFont,
    max_width: int,
    max_lines: int,
) -> list[str]:
    wrapped = wrap_text(draw, text, font, max_width, max_lines=None)
    if len(wrapped) <= max_lines:
        return wrapped

    lines = wrapped[:max_lines]
    last_line = lines[-1].rstrip()
    while last_line:
        candidate = f"{last_line}…"
        width, _ = measure_text(draw, candidate, font)
        if width <= max_width:
            lines[-1] = candidate
            return lines
        last_line = last_line[:-1].rstrip()
    lines[-1] = "…"
    return lines


def fit_text_block(
    draw: ImageDraw.ImageDraw,
    text: str,
    font_path: Path,
    max_width: int,
    max_height: int,
    base_size: int = 28,
    min_size: int = 18,
    max_lines: int = 3,
) -> FittedText:
    for font_size in range(base_size, min_size - 1, -2):
        font = _load_font(font_path, font_size)
        lines = wrap_text(draw, text, font, max_width, max_lines=max_lines)
        if len(lines) > max_lines:
            continue

        _, line_height = measure_text(draw, "Ag", font)
        total_height = line_height * len(lines)
        if total_height <= max_height:
            return FittedText(lines=lines, font=font, line_height=line_height)

    font = _load_font(font_path, min_size)
    lines = truncate_to_lines(draw, text, font, max_width, max_lines=max_lines)
    _, line_height = measure_text(draw, "Ag", font)
    return FittedText(lines=lines, font=font, line_height=line_height)


def centered_text_origin(
    box: tuple[int, int, int, int],
    text_width: int,
    text_height: int,
) -> tuple[int, int]:
    left, top, right, bottom = box
    x = left + (right - left - text_width) // 2
    y = top + (bottom - top - text_height) // 2
    return x, y




