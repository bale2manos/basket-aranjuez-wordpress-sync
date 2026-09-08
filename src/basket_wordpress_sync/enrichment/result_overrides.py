from __future__ import annotations

from datetime import date
from pathlib import Path
import re

from docx import Document

from generador_horarios.normalization import normalize_lookup_key as legacy_normalize_lookup_key
from generador_horarios.normalization import parse_category as legacy_parse_category
from generador_horarios.results import ResultOverride, load_result_overrides

from ..mapper import TeamMapper
from ..models import ParsedChronicle, TeamConfig
from ..normalizer.text import compact_whitespace, matches_target_section, normalize_lookup_key


MONTHS = {
    "enero": 1,
    "febrero": 2,
    "marzo": 3,
    "abril": 4,
    "mayo": 5,
    "junio": 6,
    "julio": 7,
    "agosto": 8,
    "septiembre": 9,
    "setiembre": 9,
    "octubre": 10,
    "noviembre": 11,
    "diciembre": 12,
}
DATE_RANGE_RE = re.compile(
    r"(?P<day_start>\d{1,2})\s*-\s*(?P<day_end>\d{1,2})\s+de\s+(?P<month>[a-záéíóú]+)(?:\s+de\s+(?P<year>\d{4}))?",
    re.IGNORECASE,
)
SCORE_RE = re.compile(r"(?P<home>\d+)\s*-\s*(?P<away>\d+)")
TEAM_FILLER_TOKENS = {"CLUB", "BALONCESTO", "CB", "CD", "CDB", "APA"}
TEAM_KEYWORDS = {
    "A",
    "B",
    "BLANCO",
    "NEGRO",
    "AMARILLO",
    "CAS",
    "RUBIOCAR",
    "MISTER",
    "PIXEL",
    "POSTAS",
    "TAPERIA",
    "CLINICA",
    "RICHER",
    "ESENCIAL",
    "SALUD",
}


class _NullLogger:
    def warning(self, *_args, **_kwargs) -> None:
        return None


def enrich_chronicles_with_result_overrides(
    *,
    docx_path: Path,
    chronicles: list[ParsedChronicle],
    teams: list[TeamConfig],
    overrides_path: Path | None,
) -> int:
    if overrides_path is None or not overrides_path.exists():
        return 0

    weekend_dates = _extract_weekend_dates(docx_path)
    if not weekend_dates:
        return 0

    overrides = load_result_overrides(overrides_path, _NullLogger())
    if not overrides:
        return 0

    available = [
        override
        for override in overrides.values()
        if override.result
        and override.fecha
        and override.fecha in weekend_dates
    ]
    if not available:
        return 0

    mapper = TeamMapper(teams)
    assigned_override_keys: set[str] = set()
    applied = 0

    for section in _configured_sections(teams):
        relevant = [
            chronicle
            for chronicle in chronicles
            if matches_target_section(section, chronicle_section=chronicle.section, heading=chronicle.raw_heading)
        ]
        if not relevant:
            continue
        mapped, _errors = mapper.map_section(section, relevant)
        for mapped_chronicle in mapped:
            chronicle = mapped_chronicle.parsed_chronicle
            if chronicle is None or not _needs_override_result(chronicle):
                continue
            override = _select_override_for_team(
                team_config=mapped_chronicle.team_config,
                chronicle=chronicle,
                available_overrides=available,
                assigned_override_keys=assigned_override_keys,
            )
            if override is None:
                continue
            _apply_override(chronicle, override)
            assigned_override_keys.add(override.match_key)
            applied += 1

    return applied


def _configured_sections(teams: list[TeamConfig]) -> list[str]:
    ordered: list[str] = []
    for team in teams:
        if team.section not in ordered:
            ordered.append(team.section)
    return ordered


def _extract_weekend_dates(docx_path: Path) -> set[str]:
    texts = [docx_path.stem]
    try:
        document = Document(str(docx_path))
        for paragraph in document.paragraphs[:8]:
            text = compact_whitespace(paragraph.text)
            if text:
                texts.append(text)
    except Exception:
        return set()

    for text in texts:
        match = DATE_RANGE_RE.search(text)
        if match is None:
            continue
        month_key = _normalize_month(match.group("month"))
        month = MONTHS.get(month_key)
        year = int(match.group("year")) if match.group("year") else None
        if month is None or year is None:
            continue
        day_start = int(match.group("day_start"))
        day_end = int(match.group("day_end"))
        return {
            date(year, month, day_start).isoformat(),
            date(year, month, day_end).isoformat(),
        }
    return set()


def _normalize_month(value: str) -> str:
    text = value.strip().lower()
    replacements = str.maketrans("áéíóú", "aeiou")
    return text.translate(replacements)


def _needs_override_result(chronicle: ParsedChronicle) -> bool:
    return not (
        chronicle.home_team
        and chronicle.away_team
        and chronicle.home_score is not None
        and chronicle.away_score is not None
    )


def _select_override_for_team(
    *,
    team_config: TeamConfig,
    chronicle: ParsedChronicle,
    available_overrides: list[ResultOverride],
    assigned_override_keys: set[str],
) -> ResultOverride | None:
    candidates: list[tuple[int, ResultOverride]] = []
    for override in available_overrides:
        if override.match_key in assigned_override_keys:
            continue
        score = _override_score(team_config, chronicle, override)
        if score > 0:
            candidates.append((score, override))

    if not candidates:
        return None

    candidates.sort(key=lambda item: item[0], reverse=True)
    best_score, best_override = candidates[0]
    second_score = candidates[1][0] if len(candidates) > 1 else 0
    if best_score < 55:
        return None
    if second_score and best_score - second_score <= 8:
        return None
    return best_override


def _override_score(team_config: TeamConfig, chronicle: ParsedChronicle, override: ResultOverride) -> int:
    category_score = max(
        (
            _category_match_score(expected, override.categoria)
            for expected in _category_candidates(team_config, chronicle)
            if expected
        ),
        default=0,
    )
    team_score = max(
        (
            max(
                _team_match_score(expected, override.local_original),
                _team_match_score(expected, override.visitante_original),
            )
            for expected in _team_candidates(team_config, chronicle)
            if expected
        ),
        default=0,
    )
    return category_score + team_score


def _category_candidates(team_config: TeamConfig, chronicle: ParsedChronicle) -> list[str]:
    values: list[str] = []
    if team_config.display_competition:
        values.append(team_config.display_competition)
    values.extend(team_config.competition_aliases)
    values.extend(chronicle.competition_lines)
    values.extend(team_config.heading_aliases)
    values.append(team_config.web_block_name)
    return _unique(values)


def _team_candidates(team_config: TeamConfig, chronicle: ParsedChronicle) -> list[str]:
    values = [
        *team_config.result_aliases,
        chronicle.home_team or "",
        chronicle.away_team or "",
        chronicle.result_raw or "",
        team_config.web_block_name,
    ]
    return _unique(values)


def _unique(values: list[str]) -> list[str]:
    unique: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = compact_whitespace(value)
        key = normalize_lookup_key(text)
        if text and key and key not in seen:
            unique.append(text)
            seen.add(key)
    return unique


def _category_match_score(expected: str, candidate: str) -> int:
    expected_info = legacy_parse_category(expected or "")
    candidate_info = legacy_parse_category(candidate or "")
    expected_base = legacy_normalize_lookup_key(expected_info.base)
    candidate_base = legacy_normalize_lookup_key(candidate_info.base)
    if expected_base and candidate_base and expected_base != candidate_base:
        return 0

    expected_gender = legacy_normalize_lookup_key(expected_info.gender or "")
    candidate_gender = legacy_normalize_lookup_key(candidate_info.gender or "")
    if expected_gender and candidate_gender and expected_gender != candidate_gender:
        return 0

    expected_year = legacy_normalize_lookup_key(expected_info.year or "")
    candidate_year = legacy_normalize_lookup_key(candidate_info.year or "")
    if expected_year and candidate_year and expected_year != candidate_year:
        return 0

    expected_tokens = _category_tokens(expected)
    candidate_tokens = _category_tokens(candidate)
    if not expected_tokens or not candidate_tokens:
        return 0
    overlap = len(expected_tokens & candidate_tokens)
    if overlap <= 0:
        return 0
    return 12 * overlap


def _category_tokens(value: str) -> set[str]:
    info = legacy_parse_category(value or "")
    normalized = legacy_normalize_lookup_key(value or "")
    tokens: set[str] = set()
    for item in (info.base, info.gender, info.year):
        if item:
            tokens.add(legacy_normalize_lookup_key(item))
    if "PREF" in normalized or "PREFERENTE" in normalized:
        tokens.add("PREF")
    for token in ("ORO", "PLATA", "BRONCE", "PRIMERA", "SEGUNDA", "CUARTA"):
        if token in normalized:
            tokens.add(token)
    return {token for token in tokens if token}


def _team_match_score(expected: str, candidate: str) -> int:
    expected_key = normalize_lookup_key(expected)
    candidate_key = normalize_lookup_key(candidate)
    if not expected_key or not candidate_key:
        return 0
    if expected_key == candidate_key:
        return 120
    if expected_key in candidate_key:
        return 95 + min(len(expected_key.split()), 8)

    expected_tokens = _team_tokens(expected)
    candidate_tokens = _team_tokens(candidate)
    if not expected_tokens or not candidate_tokens:
        return 0

    shared = expected_tokens & candidate_tokens
    if not shared:
        return 0

    score = int(70 * len(shared) / max(len(expected_tokens), len(candidate_tokens)))
    qualifier_overlap = TEAM_KEYWORDS & shared
    if qualifier_overlap:
        score += 15 * len(qualifier_overlap)
    return score


def _team_tokens(value: str) -> set[str]:
    tokens = set(normalize_lookup_key(value).split())
    return {token for token in tokens if token and token not in TEAM_FILLER_TOKENS}


def _apply_override(chronicle: ParsedChronicle, override: ResultOverride) -> None:
    score_match = SCORE_RE.search(override.result or "")
    if score_match is None:
        return

    home_score = int(score_match.group("home"))
    away_score = int(score_match.group("away"))
    home_team = compact_whitespace(override.local_original)
    away_team = compact_whitespace(override.visitante_original)
    home_has_basket = "BASKET ARANJUEZ" in normalize_lookup_key(home_team)
    away_has_basket = "BASKET ARANJUEZ" in normalize_lookup_key(away_team)

    chronicle.result_raw = f"{home_team} {home_score} - {away_score} {away_team}"
    chronicle.home_team = home_team
    chronicle.away_team = away_team
    chronicle.home_score = home_score
    chronicle.away_score = away_score
    if home_has_basket and not away_has_basket:
        chronicle.basket_is_home = True
        chronicle.basket_score = home_score
        chronicle.rival_team = away_team
        chronicle.rival_score = away_score
    elif away_has_basket and not home_has_basket:
        chronicle.basket_is_home = False
        chronicle.basket_score = away_score
        chronicle.rival_team = home_team
        chronicle.rival_score = home_score

    chronicle.warnings = [
        warning
        for warning in chronicle.warnings
        if warning not in {"missing_result", "parse_warning:incomplete_block"}
    ]
    chronicle.warnings.append("result_enriched_from_overrides")
    chronicle.warnings = _unique(chronicle.warnings)
    chronicle.is_complete = bool(
        chronicle.raw_heading
        and chronicle.home_team
        and chronicle.away_team
        and chronicle.home_score is not None
        and chronicle.away_score is not None
        and (chronicle.players_text or chronicle.body_paragraphs)
    )
