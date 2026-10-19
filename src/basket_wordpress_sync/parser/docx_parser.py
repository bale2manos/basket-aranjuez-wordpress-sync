from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re

from docx import Document

from ..models import ParsedChronicle, PartialScore
from ..normalizer.text import (
    build_period_labels,
    compact_whitespace,
    extract_heading_and_competition,
    looks_like_heading_line,
    normalize_lookup_key,
    section_from_heading,
    split_competition_lines,
)


SCORE_PATTERN = re.compile(r"(?P<home>\d+)\s*[-\u2010\u2011\u2012\u2013\u2014\u2212]\s*(?P<away>\d+)")
RESULT_LINE_PATTERN = re.compile(
    r"^\s*(?P<home_team>.+?)\s+(?P<home_score>\d+)\s*[-\u2010\u2011\u2012\u2013\u2014\u2212]\s*(?P<away_score>\d+)\s+(?P<away_team>.+?)\s*$"
)
TEAM_SCORE_LINE_PATTERN = re.compile(
    r"^\s*(?P<home_team>.+?)\s+(?P<home_score>\d+)\s*[-\u2010\u2011\u2012\u2013\u2014\u2212]\s*(?P<away_team>.+?)\s+(?P<away_score>\d+)\s*$"
)
TEAM_CONNECTOR_TOKENS = {"DE", "DEL", "LA", "LAS", "LOS", "EL", "AL", "Y"}
PLAYERS_PREFIX_RE = re.compile(r"^(JUGADOR(?:ES|AS)?|1º\s*ENTRENADOR|2º\s*ENTRENADOR|ENTRENADOR(?:ES)?):", re.IGNORECASE)


@dataclass(slots=True)
class ParsedResult:
    raw_line: str
    home_team: str
    away_team: str
    home_score: int | None
    away_score: int | None
    basket_is_home: bool | None
    basket_score: int | None
    rival_team: str | None
    rival_score: int | None
    warnings: list[str]


class ChronicleParser:
    def __init__(
        self,
        *,
        period_label_templates: dict[int, str] | None = None,
        overtime_label: str = "PR",
    ) -> None:
        self.period_label_templates = period_label_templates or {4: "{index}C", 5: "{index}C", 6: "{index}P"}
        self.overtime_label = overtime_label

    def parse_docx(self, docx_path: Path) -> list[ParsedChronicle]:
        paragraphs = self._read_paragraphs(docx_path)
        blocks = self._split_blocks(paragraphs)
        chronicles: list[ParsedChronicle] = []
        next_order_index = 1
        for block in blocks:
            parsed_items = self._parse_block(block, next_order_index)
            chronicles.extend(parsed_items)
            next_order_index += len(parsed_items)
        return chronicles

    def _read_paragraphs(self, docx_path: Path) -> list[str]:
        document = Document(str(docx_path))
        paragraphs: list[str] = []
        for paragraph in document.paragraphs:
            for line in paragraph.text.splitlines():
                text = compact_whitespace(line)
                if text:
                    paragraphs.append(text)
        return paragraphs

    def _split_blocks(self, paragraphs: list[str]) -> list[list[str]]:
        blocks: list[list[str]] = []
        current: list[str] = []
        for paragraph in paragraphs:
            if looks_like_heading_line(paragraph):
                if current:
                    blocks.append(current)
                current = [paragraph]
                continue
            if current:
                current.append(paragraph)
        if current:
            blocks.append(current)
        return blocks

    def _parse_block(self, lines: list[str], order_index: int) -> list[ParsedChronicle]:
        heading, inline_competition = extract_heading_and_competition(lines[0])
        base_warnings: list[str] = []
        if heading is None:
            heading = lines[0]
            base_warnings.append("parse_error:heading_not_detected")

        pre_result_lines: list[str] = []
        content_lines = list(lines[1:])
        first_result_index: int | None = None
        for index, line in enumerate(content_lines):
            if self._parse_result_line(line) is not None:
                first_result_index = index
                break
            pre_result_lines.append(line)

        competition_lines = list(inline_competition)
        for line in pre_result_lines:
            if self._looks_like_competition_line(line):
                competition_lines.extend(split_competition_lines(line))
        competition_lines = self._unique_lines(competition_lines)

        if first_result_index is None:
            return [
                self._build_chronicle(
                    heading=heading,
                    competition_lines=competition_lines,
                    result=None,
                    post_result_lines=[],
                    source_paragraphs=list(lines),
                    order_index=order_index,
                    base_warnings=[*base_warnings, "missing_result", "parse_warning:incomplete_block"],
                )
            ]

        entry_groups: list[list[str]] = []
        current_group: list[str] = []
        for line in content_lines[first_result_index:]:
            if self._parse_result_line(line) is not None:
                if current_group:
                    entry_groups.append(current_group)
                current_group = [line]
                continue
            if current_group:
                current_group.append(line)
        if current_group:
            entry_groups.append(current_group)

        parsed_block: list[ParsedChronicle] = []
        for offset, group in enumerate(entry_groups):
            result = self._parse_result_line(group[0])
            parsed_block.append(
                self._build_chronicle(
                    heading=heading,
                    competition_lines=competition_lines,
                    result=result,
                    post_result_lines=group[1:],
                    source_paragraphs=[lines[0], *pre_result_lines, *group],
                    order_index=order_index + offset,
                    base_warnings=base_warnings,
                )
            )
        return parsed_block

    def _build_chronicle(
        self,
        *,
        heading: str,
        competition_lines: list[str],
        result: ParsedResult | None,
        post_result_lines: list[str],
        source_paragraphs: list[str],
        order_index: int,
        base_warnings: list[str],
    ) -> ParsedChronicle:
        warnings = list(base_warnings)

        partial_lines = [line for line in post_result_lines if normalize_lookup_key(line).startswith("PARCIALES")]
        partials = self._parse_partials(partial_lines[0]) if partial_lines else []

        player_lines: list[str] = []
        body_paragraphs: list[str] = []
        roster_open = False
        for line in post_result_lines:
            if normalize_lookup_key(line).startswith("PARCIALES"):
                continue
            if PLAYERS_PREFIX_RE.match(line):
                player_lines.append(line)
                roster_open = True
                continue
            if roster_open and re.match(r"^\d+º\s*ENTRENADOR:", line, re.IGNORECASE):
                player_lines.append(line)
                continue
            body_paragraphs.append(line)

        players_text = "\n".join(player_lines) if player_lines else None
        if result is None:
            if "missing_result" not in warnings:
                warnings.append("missing_result")
        else:
            warnings.extend(result.warnings)

        if partials and len(partials) not in {4, 5, 6}:
            warnings.append(f"partials_mismatch:{len(partials)}")

        is_complete = bool(heading and result is not None and (players_text or body_paragraphs))
        if not is_complete:
            if "parse_warning:incomplete_block" not in warnings:
                warnings.append("parse_warning:incomplete_block")

        return ParsedChronicle(
            raw_heading=heading,
            normalized_heading=normalize_lookup_key(heading),
            section=section_from_heading(heading),
            competition_lines=competition_lines,
            result_raw=result.raw_line if result is not None else None,
            home_team=result.home_team if result is not None else None,
            away_team=result.away_team if result is not None else None,
            home_score=result.home_score if result is not None else None,
            away_score=result.away_score if result is not None else None,
            basket_is_home=result.basket_is_home if result is not None else None,
            basket_score=result.basket_score if result is not None else None,
            rival_team=result.rival_team if result is not None else None,
            rival_score=result.rival_score if result is not None else None,
            partials=partials,
            players_text=players_text,
            body_paragraphs=body_paragraphs,
            is_complete=is_complete,
            warnings=self._unique_lines(warnings),
            source_paragraphs=list(source_paragraphs),
            order_index=order_index,
        )

    def _looks_like_competition_line(self, value: str) -> bool:
        normalized = normalize_lookup_key(value)
        if not normalized:
            return False
        if normalized.startswith("PARCIALES") or PLAYERS_PREFIX_RE.match(value):
            return False
        if SCORE_PATTERN.search(value):
            return False
        if any(keyword in normalized for keyword in ("GRUPO", "LIGA", "FASE", "DIVISION", "AUTONOMICA", "PREFERENTE", "ORO", "PLATA", "BRONCE")):
            return True
        letters = [char for char in value if char.isalpha()]
        uppercase_ratio = 1.0
        if letters:
            uppercase_ratio = sum(char.isupper() for char in letters) / len(letters)
        return uppercase_ratio >= 0.60

    def _parse_result_line(self, value: str) -> ParsedResult | None:
        normalized = normalize_lookup_key(value)
        if normalized.startswith("PARCIALES") or PLAYERS_PREFIX_RE.match(value):
            return None
        result_match = RESULT_LINE_PATTERN.match(compact_whitespace(value))
        if result_match is None:
            result_match = TEAM_SCORE_LINE_PATTERN.match(compact_whitespace(value))
        if result_match is None:
            return None

        home_team = result_match.group("home_team").strip(" .,:;!?-–—−")
        away_team = result_match.group("away_team").strip(" .,:;!?-–—−")
        if not self._looks_like_team_fragment(home_team) or not self._looks_like_team_fragment(away_team):
            return None
        home_tokens = re.findall(r"[A-Za-zÁÉÍÓÚÜÑáéíóúüñ0-9]+", home_team)
        away_tokens = re.findall(r"[A-Za-zÁÉÍÓÚÜÑáéíóúüñ0-9]+", away_team)
        if len(home_tokens) > 10 or len(away_tokens) > 10:
            return None

        home_score = int(result_match.group("home_score"))
        away_score = int(result_match.group("away_score"))
        home_has_basket = "BASKET ARANJUEZ" in normalize_lookup_key(home_team)
        away_has_basket = "BASKET ARANJUEZ" in normalize_lookup_key(away_team)
        warnings: list[str] = []
        basket_is_home: bool | None = None
        basket_score: int | None = None
        rival_team: str | None = None
        rival_score: int | None = None
        if home_has_basket and not away_has_basket:
            basket_is_home = True
            basket_score = home_score
            rival_team = away_team
            rival_score = away_score
        elif away_has_basket and not home_has_basket:
            basket_is_home = False
            basket_score = away_score
            rival_team = home_team
            rival_score = home_score
        else:
            warnings.append("parse_warning:basket_side_ambiguous")

        return ParsedResult(
            raw_line=value,
            home_team=home_team,
            away_team=away_team,
            home_score=home_score,
            away_score=away_score,
            basket_is_home=basket_is_home,
            basket_score=basket_score,
            rival_team=rival_team,
            rival_score=rival_score,
            warnings=warnings,
        )

    def _looks_like_team_fragment(self, value: str) -> bool:
        text = compact_whitespace(value).strip(" .,:;!?-–—−")
        if not text:
            return False
        if any(mark in text for mark in (";", "!", "?", "(", ")")):
            return False
        tokens = re.findall(r"[A-Za-zÁÉÍÓÚÜÑáéíóúüñ0-9]+", text)
        if not tokens or len(tokens) > 8:
            return False
        alpha_words = re.findall(r"[A-Za-zÁÉÍÓÚÜÑáéíóúüñ]+", text)
        lowercase_words = [
            normalize_lookup_key(word)
            for word in alpha_words
            if word.islower()
        ]
        if any(word not in TEAM_CONNECTOR_TOKENS for word in lowercase_words):
            return False
        return True

    def _parse_partials(self, value: str) -> list[PartialScore]:
        body = value.partition(":")[2] or value
        normalized = re.sub(r"[;/]+", ",", body)
        normalized = re.sub(
            r"(?<=\d)\s+(?=\d+\s*[-\u2010\u2011\u2012\u2013\u2014\u2212]\s*(?:\d+|-))",
            ", ",
            normalized,
        )
        segments = [segment.strip() for segment in normalized.split(",") if segment.strip()]
        parsed_segments: list[tuple[int | None, int | None, str]] = []
        for segment in segments:
            cleaned_segment = segment.strip(" .;:")
            if re.fullmatch(r"[-\u2010\u2011\u2012\u2013\u2014\u2212]\s*[-\u2010\u2011\u2012\u2013\u2014\u2212]", cleaned_segment):
                parsed_segments.append((None, None, cleaned_segment))
                continue
            match = re.fullmatch(r"(?P<home>\d+)?\s*[-\u2010\u2011\u2012\u2013\u2014\u2212]\s*(?P<away>\d+)?", cleaned_segment)
            if match is None:
                continue
            home_raw = match.group("home")
            away_raw = match.group("away")
            parsed_segments.append(
                (
                    int(home_raw) if home_raw is not None else None,
                    int(away_raw) if away_raw is not None else None,
                    match.group(0),
                )
            )

        labels = build_period_labels(len(parsed_segments), self.period_label_templates, self.overtime_label)
        partials: list[PartialScore] = []
        for index, (home_score, away_score, raw_value) in enumerate(parsed_segments):
            label = labels[index] if index < len(labels) else str(index + 1)
            partials.append(
                PartialScore(
                    label=label,
                    home_score=home_score,
                    away_score=away_score,
                    raw_value=raw_value,
                )
            )
        return partials

    def _unique_lines(self, values: list[str]) -> list[str]:
        unique: list[str] = []
        seen: set[str] = set()
        for value in values:
            key = normalize_lookup_key(value)
            if key and key not in seen:
                unique.append(value)
                seen.add(key)
        return unique
