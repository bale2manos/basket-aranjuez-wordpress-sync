from __future__ import annotations

from dataclasses import dataclass
from datetime import date, time
import re
import unicodedata


SPANISH_WEEKDAYS = [
    "Lunes",
    "Martes",
    "Mi\u00e9rcoles",
    "Jueves",
    "Viernes",
    "S\u00e1bado",
    "Domingo",
]

SPONSOR_DISPLAY_NAMES = {
    "CAS": "CAS",
    "RUBIOCAR": "Rubiocar",
    "MISTER PIXEL": "Mister Pixel",
    "POSTAS TAPERIA": "Postas Taper\u00eda",
    "CLINICA RICHER": "Cl\u00ednica Richer",
    "ESENCIAL SALUD": "Esencial Salud",
}

DISPLAY_REPLACEMENTS = {
    "Alevin": "Alev\u00edn",
    "Ano": "A\u00f1o",
    "Frances": "Franc\u00e9s",
    "Jose": "Jos\u00e9",
    "Maria": "Mar\u00eda",
    "Pio": "P\u00edo",
    "Basquet": "B\u00e1squet",
    "Fdez.": "Fdez.",
    "Mpal.": "Mpal.",
}

LOWERCASE_WORDS = {"De", "Del", "La", "Las", "Los", "Y"}
UPPERCASE_WORDS = {"A", "B", "C.D.", "C.B.", "C.P.", "CDM", "BT", "ADC", "FGS", "TSN"}


@dataclass(slots=True)
class CategoryInfo:
    base: str
    gender: str | None
    year: str | None
    normalized: str


def strip_accents(value: str) -> str:
    return "".join(
        char
        for char in unicodedata.normalize("NFKD", value)
        if not unicodedata.combining(char)
    )


def normalize_lookup_key(value: str) -> str:
    normalized = strip_accents(value or "").upper()
    normalized = re.sub(r"[\"'`\u00b4]", " ", normalized)
    normalized = re.sub(r"[^A-Z0-9]+", " ", normalized)
    return re.sub(r"\s+", " ", normalized).strip()


def _is_acronym_token(value: str) -> bool:
    plain = value.strip("()")
    if not plain:
        return False
    return bool(re.fullmatch(r"(?:[A-Za-z]\.){2,}[A-Za-z]?\.?", plain))


def smart_title(value: str) -> str:
    words = re.sub(r"\s+", " ", value.strip()).split(" ")
    if not words or words == [""]:
        return ""

    rendered: list[str] = []
    for word in words:
        if not word:
            continue
        upper_word = word.upper()
        if upper_word in UPPERCASE_WORDS or _is_acronym_token(word):
            rendered.append(upper_word)
            continue
        if word.startswith("(") and word.endswith(")"):
            rendered.append(f"({smart_title(word[1:-1])})")
            continue
        if "-" in word:
            rendered.append("-".join(smart_title(part) for part in word.split("-")))
            continue
        if "/" in word:
            rendered.append("/".join(smart_title(part) for part in word.split("/")))
            continue

        capitalized = word.lower().capitalize()
        if rendered and capitalized in LOWERCASE_WORDS:
            rendered.append(capitalized.lower())
        else:
            rendered.append(DISPLAY_REPLACEMENTS.get(capitalized, capitalized))

    return " ".join(rendered)


def detect_match_type(local_name: str, visitor_name: str) -> tuple[str | None, str | None]:
    local_has_basket = "BASKET ARANJUEZ" in normalize_lookup_key(local_name)
    visitor_has_basket = "BASKET ARANJUEZ" in normalize_lookup_key(visitor_name)

    if local_has_basket and visitor_has_basket:
        return None, "Basket Aranjuez aparece en ambos equipos"
    if not local_has_basket and not visitor_has_basket:
        return None, "Basket Aranjuez no aparece en la fila"
    return ("local", None) if local_has_basket else ("visitante", None)


def _extract_year(tokens: set[str]) -> str | None:
    if "1" in tokens and "ANO" in tokens:
        return "1\u00ba A\u00f1o"
    if "2" in tokens and "ANO" in tokens:
        return "2\u00ba A\u00f1o"

    for token in tokens:
        if token.startswith("1") and token.endswith("ANO"):
            return "1\u00ba A\u00f1o"
        if token.startswith("2") and token.endswith("ANO"):
            return "2\u00ba A\u00f1o"
    return None


def parse_category(category: str) -> CategoryInfo:
    normalized = normalize_lookup_key(category)
    tokens = set(normalized.split())

    if "SUB" in tokens and "22" in tokens or "SUB22" in tokens:
        base = "Sub22"
    elif "2A" in tokens and "DIV" in tokens and "AUT" in tokens or "SENIOR" in tokens:
        base = "Senior"
    elif "ALV" in tokens or "ALEVIN" in tokens:
        base = "Alev\u00edn"
    elif "INFANTIL" in tokens:
        base = "Infantil"
    elif "CADETE" in tokens:
        base = "Cadete"
    elif "JUNIOR" in tokens:
        base = "Junior"
    else:
        base = smart_title(category) or "Equipo"

    if "FEM" in tokens or "FEMENINA" in tokens:
        gender = "Fem"
    elif "MASC" in tokens or "MASCULINO" in tokens or "MAS" in tokens:
        gender = "Masc"
    else:
        gender = None

    year = _extract_year(tokens)

    return CategoryInfo(base=base, gender=gender, year=year, normalized=normalized)


def _find_sponsor(team_name: str) -> str | None:
    normalized = normalize_lookup_key(team_name)
    for sponsor_key, sponsor_display in SPONSOR_DISPLAY_NAMES.items():
        if sponsor_key in normalized:
            return sponsor_display
    return None


def _find_color(team_name: str) -> str | None:
    tokens = normalize_lookup_key(team_name).split()
    if "NEGRO" in tokens:
        return "Negro"
    if "BLANCO" in tokens:
        return "Blanco"
    if "AMARILLO" in tokens:
        return "Amarillo"
    return None


def _find_series_letter(category_info: CategoryInfo, team_name: str) -> str | None:
    tokens = normalize_lookup_key(team_name).split()
    if "A" in tokens:
        return "A"
    if "B" in tokens:
        return "B"

    if category_info.base == "Senior":
        if "ORO" in category_info.normalized:
            return "A"
        if "PLATA" in category_info.normalized or "BRONCE" in category_info.normalized:
            return "B"
    return None


def _unique_parts(parts: list[str]) -> list[str]:
    seen: set[str] = set()
    rendered: list[str] = []
    for part in parts:
        if not part:
            continue
        key = normalize_lookup_key(part)
        if key and key not in seen:
            rendered.append(part)
            seen.add(key)
    return rendered


def derive_basket_display_name(category: str, team_name: str) -> str:
    category_info = parse_category(category)
    sponsor = _find_sponsor(team_name)

    parts: list[str] = [category_info.base]
    if category_info.base != "Sub22" and category_info.gender:
        parts.append(category_info.gender)

    if sponsor:
        parts.append(sponsor)
        return " ".join(_unique_parts(parts))

    if category_info.base in {"Alev\u00edn", "Infantil", "Cadete"} and category_info.year:
        parts.append(category_info.year)

    qualifier = _find_color(team_name)
    if qualifier is None:
        qualifier = _find_series_letter(category_info, team_name)

    if qualifier:
        parts.append(qualifier)

    return " ".join(_unique_parts(parts))


def clean_club_name(value: str) -> str:
    cleaned = value.replace('"', " ").replace("'", " ")
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return smart_title(cleaned)


VENUE_SUFFIX_MAP = {
    "CIUDAD DEPORTIVA": "",
    "PABELLON COLEGIO": "Pabell\u00f3n",
    "PABELLON MPAL": "Pabell\u00f3n",
    "PABELLON": "Pabell\u00f3n",
    "COLEGIO": "Colegio",
    "C P": "C.P.",
    "PABELLON CIRCULAR": "Pabell\u00f3n Circular",
    "PABELLON MANUEL BEAUMONT": "Pabell\u00f3n Manuel Beaumont",
    "CDM PABELLON": "CDM (Pabell\u00f3n)",
}


def clean_venue_name(value: str) -> str:
    text = value.strip()
    if not text:
        return ""
    if "," not in text:
        return smart_title(text)

    left, right = [segment.strip() for segment in text.split(",", 1)]
    normalized_right = normalize_lookup_key(right)
    suffix = VENUE_SUFFIX_MAP.get(normalized_right)
    left_display = smart_title(left)
    if suffix is None:
        suffix = smart_title(right)
    if not suffix:
        return left_display
    return f"{left_display}, {suffix}"


def format_schedule_line(match_date: date, match_time: time, venue_display: str) -> str:
    weekday = SPANISH_WEEKDAYS[match_date.weekday()]
    return f"{weekday} {match_date.day}, {match_time.strftime('%H:%M')} - {venue_display}"
