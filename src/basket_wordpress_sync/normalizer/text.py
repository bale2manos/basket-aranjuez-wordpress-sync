from __future__ import annotations

import re
import unicodedata


DISPLAY_REPLACEMENTS = {
    "Alevin": "Alevín",
    "Ano": "Año",
    "Autonomica": "Autonómica",
    "Basquet": "Básquet",
    "Clinica": "Clínica",
    "Division": "División",
    "Femenino": "Femenino",
    "Femenina": "Femenina",
    "Masculino": "Masculino",
    "Mister": "Mister",
    "Odon": "Odón",
    "Taperia": "Tapería",
}
COMPETITION_PREFIXES = (
    ("CUARTA", "CUARTA"),
    ("TERCERA", "TERCERA"),
    ("SEGUNDA", "SEGUNDA"),
    ("PRIMERA", "PRIMERA"),
    ("PRIMER", "PRIMERA"),
)
UPPERCASE_WORDS = {"A", "B", "C.B.", "C.D.", "A.B.", "ADC", "CB", "CD", "CAS"}
TEAM_NAME_REPLACEMENTS = {
    "BASKET ARANJUEZ": "Basket Aranjuez",
}
STRUCTURED_KEYWORDS = (
    "DIVISION",
    "DIVISIÓN",
    "AUTONOMICA",
    "AUTONÓMICA",
    "LIGA",
    "FASE",
    "GRUPO",
    "ORO",
    "PLATA",
    "BRONCE",
    "PREFERENTE",
)
HEADING_PATTERNS = (
    re.compile(
        r"^(?P<heading>SENIOR\s+(?:FEMENINO|FEMENINA|FEM|MASCULINO|MASC|MAS)(?:\s+[AB])?)(?P<rest>.*)$",
        re.IGNORECASE,
    ),
    re.compile(
        r"^(?P<heading>(?:ALEV[IÍ]N|INFANTIL|CADETE|JUNIOR)\s+"
        r"(?:PREFERENTE\s+)?"
        r"(?:FEMENINO|FEMENINA|FEM|MASCULINO|MASC|MAS)"
        r"(?:\s+(?:PRIMERA|PRIMER|SEGUNDA|SEGUNDO|1(?:º|O)?|2(?:º|O)?)(?:\s+A[ÑN]O)?)?"
        r"(?:\s+[AB])?)(?P<rest>.*)$",
        re.IGNORECASE,
    ),
    re.compile(
        r"^(?P<heading>SUB\s*22(?:\s+(?:FEMENINO|FEMENINA|FEM|MASCULINO|MASC|MAS))?(?:\s+[AB])?)(?P<rest>.*)$",
        re.IGNORECASE,
    ),
)


def strip_accents(value: str) -> str:
    return "".join(
        char
        for char in unicodedata.normalize("NFKD", value or "")
        if not unicodedata.combining(char)
    )


def compact_whitespace(value: str) -> str:
    return re.sub(r"\s+", " ", (value or "").replace("\xa0", " ")).strip()


def normalize_lookup_key(value: str) -> str:
    normalized = strip_accents(value or "").upper()
    normalized = re.sub(r"[\"'`´]", " ", normalized)
    normalized = re.sub(r"[^A-Z0-9]+", " ", normalized)
    return compact_whitespace(normalized)


def _is_acronym_token(value: str) -> bool:
    return bool(re.fullmatch(r"(?:[A-Za-z]\.){2,}[A-Za-z]?\.?", value.strip("()")))


def _format_wrapped_single_letter(value: str) -> str | None:
    match = re.fullmatch(r"(?P<prefix>[^A-Za-z0-9]*)(?P<core>[A-Za-z])(?P<suffix>[^A-Za-z0-9]*)", value)
    if match is None:
        return None
    return f"{match.group('prefix')}{match.group('core').upper()}{match.group('suffix')}"


def smart_title(value: str) -> str:
    words = compact_whitespace(value).split(" ")
    if not words or words == [""]:
        return ""

    rendered: list[str] = []
    for word in words:
        if not word:
            continue
        wrapped_single_letter = _format_wrapped_single_letter(word)
        if wrapped_single_letter is not None:
            rendered.append(wrapped_single_letter)
            continue
        upper_word = word.upper()
        if re.fullmatch(r"[A-Za-z]", word):
            rendered.append(upper_word)
            continue
        if upper_word in UPPERCASE_WORDS or _is_acronym_token(word):
            rendered.append(upper_word)
            continue
        if "-" in word:
            rendered.append("-".join(smart_title(part) for part in word.split("-")))
            continue
        capitalized = word.lower().capitalize()
        rendered.append(DISPLAY_REPLACEMENTS.get(capitalized, capitalized))
    return " ".join(rendered)


def looks_like_heading_line(value: str) -> bool:
    text = compact_whitespace(value)
    if not text:
        return False
    normalized = normalize_lookup_key(text)
    if not normalized.startswith(("SENIOR", "JUNIOR", "CADETE", "INFANTIL", "ALEVIN", "SUB22", "SUB 22")):
        return False

    letters = [char for char in text if char.isalpha()]
    uppercase_ratio = 1.0
    if letters:
        uppercase_ratio = sum(char.isupper() for char in letters) / len(letters)

    if uppercase_ratio >= 0.60:
        return True
    return any(keyword in normalized for keyword in STRUCTURED_KEYWORDS)


def section_from_heading(heading: str) -> str:
    normalized = normalize_lookup_key(heading)
    if normalized.startswith("SENIOR"):
        return "senior"
    if normalized.startswith("ALEVIN"):
        return "alevin"
    if normalized.startswith("INFANTIL"):
        return "infantil"
    if normalized.startswith("CADETE"):
        return "cadete"
    if normalized.startswith("JUNIOR"):
        return "junior"
    if normalized.startswith("SUB22") or normalized.startswith("SUB 22"):
        return "sub22"
    return "other"


def heading_gender(heading: str) -> str | None:
    tokens = set(normalize_lookup_key(heading).split())
    if {"FEMENINO", "FEMENINA", "FEM"} & tokens:
        return "femenino"
    if {"MASCULINO", "MASC", "MAS"} & tokens:
        return "masculino"
    return None


def matches_target_section(target_section: str, *, chronicle_section: str, heading: str) -> bool:
    normalized_target = normalize_lookup_key(target_section).replace(" ", "_").lower()
    if normalized_target in {"infantil_femenino", "infantil_fem"}:
        return chronicle_section == "infantil" and heading_gender(heading) == "femenino"
    if normalized_target in {"infantil_masculino", "infantil_masc"}:
        return chronicle_section == "infantil" and heading_gender(heading) == "masculino"
    return chronicle_section == normalized_target


def split_competition_lines(value: str) -> list[str]:
    text = compact_whitespace(value).strip(" -,:;")
    if not text:
        return []

    fragments: list[str] = []
    comma_split = re.split(r"\s*,\s*", text, maxsplit=1)
    if len(comma_split) == 2 and all(comma_split):
        fragments.extend(comma_split)
    else:
        normalized = normalize_lookup_key(text)
        match = re.search(r"\bGRUPO\s+[A-Z0-9- ]+$", normalized)
        if match:
            start = match.start()
            left = text[:start].strip(" -,:;")
            right = text[start:].strip(" -,:;")
            if left:
                fragments.append(left)
            if right:
                fragments.append(right)
        if not fragments:
            fragments.append(text)

    unique: list[str] = []
    seen: set[str] = set()
    for fragment in fragments:
        key = normalize_lookup_key(fragment)
        if key and key not in seen:
            unique.append(fragment)
            seen.add(key)
    return unique


def extract_heading_and_competition(value: str) -> tuple[str | None, list[str]]:
    text = compact_whitespace(value)
    for pattern in HEADING_PATTERNS:
        match = pattern.match(text)
        if match is None:
            continue
        heading = compact_whitespace(match.group("heading"))
        rest = compact_whitespace(match.group("rest"))
        return heading, split_competition_lines(rest)
    return None, []


def format_team_display(value: str | None) -> str:
    if not value:
        return ""
    normalized = normalize_lookup_key(value)
    for source, target in TEAM_NAME_REPLACEMENTS.items():
        if normalized == source or normalized.startswith(f"{source} "):
            return target
    text = smart_title(value)
    for source, target in TEAM_NAME_REPLACEMENTS.items():
        text = re.sub(re.escape(source.title()), target, text, flags=re.IGNORECASE)
    return text


def format_competition_display(value: str | None, *, heading: str | None = None) -> str:
    text = compact_whitespace(value or "")
    if not text:
        return ""

    normalized_value = normalize_lookup_key(text)
    if normalized_value in {"A DIVISION", "DIVISION"}:
        normalized_heading = normalize_lookup_key(heading or "")
        for token, replacement in COMPETITION_PREFIXES:
            if re.search(rf"(?:^| ){re.escape(token)}(?: |$)", normalized_heading):
                return f"{replacement} DIVISIÓN"
        if normalized_value == "A DIVISION":
            return "PRIMERA DIVISIÓN"
    return text


def format_score(home_score: int | None, away_score: int | None) -> str:
    if home_score is None or away_score is None:
        return ""
    return f"{home_score} - {away_score}"


def build_period_labels(count: int, templates: dict[int, str], overtime_label: str) -> list[str]:
    if count <= 0:
        return []
    if count == 5:
        base_template = templates.get(4, "{index}C")
        labels = [base_template.format(index=index) for index in range(1, 5)]
        labels.append(overtime_label)
        return labels

    template = templates.get(count, "{index}P")
    return [template.format(index=index) for index in range(1, count + 1)]
