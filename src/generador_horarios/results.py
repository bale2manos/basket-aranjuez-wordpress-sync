from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date
import json
import logging
from pathlib import Path
import re
import xml.etree.ElementTree as ET
import zipfile

from .models import MatchRecord
from .normalization import normalize_lookup_key, parse_category


SCORE_PATTERN = re.compile(r"\b(\d+)\s*[-\u2010\u2011\u2012\u2013\u2014\u2212]\s*(\d+)\b")
CATEGORY_KEYWORDS = ("SENIOR", "JUNIOR", "CADETE", "INFANTIL", "SUB", "ALEVIN", "ALV")
TEAM_FILLER_TOKENS = {"CLUB", "BALONCESTO", "CB", "CD", "CDB", "APA"}
TEAM_ALIAS_MAP = {
    "CABRINI VERDE": "CABRINI APA VERDE",
    "COSLADA SERRACINES": "COSLADA CB SERRACINES",
    "DAGANZO": "BALONCESTO DAGANZO ISUZU REVEIN",
    "VILLALBA": "VILLALBA CRISREPARA",
    "MENESIANOS AZUL": "MENESIANO AZUL",
    "ZONA PRESS": "CLUB BALONCESTO ZONA PRESS",
    "A D C BOADILLA": "ADC BOADILLA",
    "C B COSLADA": "CB COSLADA",
    "EL VALLE": "EL VALLE CD",
    "HALCONES ARROYOFRESNO": "HALCONES ARROYOFRESNO B",
    "PARLA BASQUET": "PARLA BASQUET 2014",
    "DISTRITO CARABANCHEL": "DISTRITO CARABANCHEL A",
    "COLEGIO LAS TABLAS": "COLEGIO ESTUDIANTES LAS TABLAS",
}
PHRASE_REPLACEMENTS = (
    ("A P A", "APA"),
    ("C D B", "CDB"),
    ("A D C", "ADC"),
    ("C B", "CB"),
    ("C D", "CD"),
)


@dataclass(slots=True)
class ParsedResultEntry:
    category_context: str
    score_line: str
    local_team_raw: str
    visitante_team_raw: str
    local_score: int
    visitante_score: int


@dataclass(slots=True)
class ResultOverride:
    match_key: str
    fecha: str | None
    categoria: str
    local_original: str
    visitante_original: str
    result: str | None


def build_match_key(match_date: date | None, category: str, local_name: str, visitor_name: str) -> str:
    date_key = match_date.isoformat() if match_date else "sin-fecha"
    parts = [
        date_key,
        normalize_lookup_key(category),
        normalize_lookup_key(local_name),
        normalize_lookup_key(visitor_name),
    ]
    return "__".join(parts)


def _normalize_override_result(value: str | None) -> str | None:
    if value is None:
        return None
    match = SCORE_PATTERN.search(str(value))
    if match is None:
        return None
    return f"{int(match.group(1))}-{int(match.group(2))}"


def _looks_like_category_heading(text: str) -> bool:
    normalized = normalize_lookup_key(text)
    return "GRUPO" in normalized and any(keyword in normalized for keyword in CATEGORY_KEYWORDS)


def _extract_docx_paragraphs(docx_path: Path) -> list[str]:
    with zipfile.ZipFile(docx_path) as archive:
        root = ET.fromstring(archive.read("word/document.xml"))

    namespace = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
    paragraphs: list[str] = []
    for paragraph in root.findall(".//w:p", namespace):
        texts = [node.text or "" for node in paragraph.findall(".//w:t", namespace)]
        text = "".join(texts).strip()
        if text:
            paragraphs.append(text)
    return paragraphs


def _clean_team_fragment(value: str) -> str:
    return value.strip(" .,:;!?-–—−")


def _parse_score_line(text: str) -> tuple[str, str, int, int] | None:
    normalized = normalize_lookup_key(text)
    if "BASKET ARANJUEZ" not in normalized:
        return None
    if normalized.startswith("PARCIALES"):
        return None

    match = SCORE_PATTERN.search(text)
    if match is None:
        return None

    local_team = _clean_team_fragment(text[: match.start()])
    visitante_team = _clean_team_fragment(text[match.end() :])
    if not local_team or not visitante_team:
        return None

    return local_team, visitante_team, int(match.group(1)), int(match.group(2))


def parse_results_doc(docx_path: Path) -> list[ParsedResultEntry]:
    current_category = ""
    entries: list[ParsedResultEntry] = []
    for paragraph in _extract_docx_paragraphs(docx_path):
        if _looks_like_category_heading(paragraph):
            current_category = paragraph
            continue

        parsed = _parse_score_line(paragraph)
        if parsed is None:
            continue

        local_team, visitante_team, local_score, visitante_score = parsed
        entries.append(
            ParsedResultEntry(
                category_context=current_category,
                score_line=paragraph,
                local_team_raw=local_team,
                visitante_team_raw=visitante_team,
                local_score=local_score,
                visitante_score=visitante_score,
            )
        )
    return entries


def _canonical_team_tokens(value: str) -> set[str]:
    normalized = normalize_lookup_key(value)
    for source, target in PHRASE_REPLACEMENTS:
        normalized = normalized.replace(source, target)
    normalized = TEAM_ALIAS_MAP.get(normalized, normalized)
    tokens = [token for token in normalized.split() if token and token not in TEAM_FILLER_TOKENS]
    return set(tokens)


def _team_match_score(expected: str, candidate: str) -> int:
    expected_tokens = _canonical_team_tokens(expected)
    candidate_tokens = _canonical_team_tokens(candidate)
    if not expected_tokens or not candidate_tokens:
        return 0
    if expected_tokens == candidate_tokens:
        return 100
    if expected_tokens.issubset(candidate_tokens) or candidate_tokens.issubset(expected_tokens):
        return 80 + 2 * len(expected_tokens & candidate_tokens)

    intersection = len(expected_tokens & candidate_tokens)
    if intersection == 0:
        return 0

    union = len(expected_tokens | candidate_tokens)
    return int(60 * intersection / union)


def _category_tokens(value: str) -> set[str]:
    parsed = parse_category(value)
    normalized = normalize_lookup_key(value)
    tokens = {normalize_lookup_key(parsed.base)}
    if parsed.gender:
        tokens.add(normalize_lookup_key(parsed.gender))
    if parsed.year:
        tokens.add(normalize_lookup_key(parsed.year))
    if "PREF" in normalized or "PREFERENTE" in normalized:
        tokens.add("PREF")
    if "ORO" in normalized:
        tokens.add("ORO")
    if "PLATA" in normalized:
        tokens.add("PLATA")
    if "BRONCE" in normalized:
        tokens.add("BRONCE")
    if "PRIMERA" in normalized:
        tokens.add("PRIMERA")
    if "SEGUNDA" in normalized:
        tokens.add("SEGUNDA")
    if "CUARTA" in normalized:
        tokens.add("CUARTA")
    return {token for token in tokens if token}


def _category_match_score(expected: str, candidate: str) -> int:
    expected_tokens = _category_tokens(expected)
    candidate_tokens = _category_tokens(candidate)
    if not expected_tokens or not candidate_tokens:
        return 0
    return 5 * len(expected_tokens & candidate_tokens)


def _candidate_score(record: MatchRecord, entry: ParsedResultEntry) -> int:
    local_score = _team_match_score(record.local_original, entry.local_team_raw)
    visitor_score = _team_match_score(record.visitante_original, entry.visitante_team_raw)
    if local_score == 0 or visitor_score == 0:
        return 0
    return local_score + visitor_score + _category_match_score(record.categoria, entry.category_context)


def match_results_to_records(records: list[MatchRecord], entries: list[ParsedResultEntry]) -> dict[str, ParsedResultEntry]:
    candidates: list[tuple[int, str, int]] = []
    for record in records:
        for entry_index, entry in enumerate(entries):
            score = _candidate_score(record, entry)
            if score >= 120:
                candidates.append((score, record.match_key, entry_index))

    candidates.sort(reverse=True)
    assigned_records: set[str] = set()
    assigned_entries: set[int] = set()
    matches: dict[str, ParsedResultEntry] = {}
    for _, match_key, entry_index in candidates:
        if match_key in assigned_records or entry_index in assigned_entries:
            continue
        matches[match_key] = entries[entry_index]
        assigned_records.add(match_key)
        assigned_entries.add(entry_index)
    return matches


def load_result_overrides(path: Path | None, logger: logging.Logger) -> dict[str, ResultOverride]:
    if path is None or not path.exists():
        return {}

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("No se pudo leer %s: %s", path, exc)
        return {}

    if not isinstance(payload, list):
        logger.warning("%s no contiene una lista de overrides.", path)
        return {}

    overrides: dict[str, ResultOverride] = {}
    for item in payload:
        if not isinstance(item, dict):
            continue
        match_key = str(item.get("match_key") or "").strip()
        if not match_key:
            continue
        overrides[match_key] = ResultOverride(
            match_key=match_key,
            fecha=item.get("fecha"),
            categoria=str(item.get("categoria") or "").strip(),
            local_original=str(item.get("local_original") or "").strip(),
            visitante_original=str(item.get("visitante_original") or "").strip(),
            result=_normalize_override_result(item.get("result")),
        )
    return overrides


def update_result_overrides(
    path: Path | None,
    missing_records: list[MatchRecord],
    existing_overrides: dict[str, ResultOverride],
) -> None:
    if path is None:
        return

    path.parent.mkdir(parents=True, exist_ok=True)
    merged = dict(existing_overrides)
    for record in missing_records:
        merged[record.match_key] = ResultOverride(
            match_key=record.match_key,
            fecha=record.fecha.isoformat() if record.fecha else None,
            categoria=record.categoria,
            local_original=record.local_original,
            visitante_original=record.visitante_original,
            result=existing_overrides.get(record.match_key, ResultOverride(record.match_key, None, "", "", "", None)).result,
        )

    payload = [asdict(item) for item in sorted(merged.values(), key=lambda item: (item.fecha or "", item.categoria, item.local_original, item.visitante_original))]
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
