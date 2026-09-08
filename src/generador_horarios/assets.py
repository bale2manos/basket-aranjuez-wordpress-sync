from __future__ import annotations

import csv
import logging
from pathlib import Path

from .models import MatchRecord
from .normalization import clean_club_name, clean_venue_name, normalize_lookup_key


def ensure_directory(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def _compact_lookup_key(value: str) -> str:
    return normalize_lookup_key(value).replace(" ", "")


def _token_set(value: str) -> set[str]:
    return {token for token in normalize_lookup_key(value).split() if token}


def infer_logo_path(team_name: str, logos_dir: Path) -> Path | None:
    if not logos_dir.exists():
        return None

    team_compact = _compact_lookup_key(team_name)
    team_tokens = _token_set(team_name)
    if not team_compact:
        return None

    best_match: Path | None = None
    best_score = 0
    for candidate in sorted(logos_dir.glob("*.png")):
        candidate_compact = _compact_lookup_key(candidate.stem)
        candidate_tokens = _token_set(candidate.stem)
        if not candidate_compact:
            continue

        score = 0
        if candidate_compact in team_compact or team_compact in candidate_compact:
            score = len(candidate_compact)
        elif len(candidate_tokens) >= 2 and candidate_tokens.issubset(team_tokens):
            score = len("".join(sorted(candidate_tokens)))

        if score > best_score:
            best_match = candidate
            best_score = score
    return best_match


def bootstrap_club_logo_map(csv_path: Path, records: list[MatchRecord], logger: logging.Logger, logos_dir: Path | None = None) -> None:
    if csv_path.exists() and csv_path.stat().st_size > 0:
        return

    ensure_directory(csv_path.parent)
    seen: set[str] = set()
    rows: list[dict[str, str]] = []
    for record in records:
        for team_name in (record.local_original, record.visitante_original):
            if not team_name or "BASKET ARANJUEZ" in normalize_lookup_key(team_name):
                continue
            key = normalize_lookup_key(team_name)
            if key in seen:
                continue
            seen.add(key)
            inferred_logo = infer_logo_path(team_name, logos_dir) if logos_dir else None
            rows.append(
                {
                    "source_name": team_name,
                    "display_name": clean_club_name(team_name),
                    "logo_filename": inferred_logo.name if inferred_logo else "",
                    "notes": "Autoasignado por nombre de archivo." if inferred_logo else "Completar logo_filename cuando el PNG esté disponible.",
                }
            )

    with csv_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=["source_name", "display_name", "logo_filename", "notes"],
        )
        writer.writeheader()
        writer.writerows(sorted(rows, key=lambda item: normalize_lookup_key(item["source_name"])))

    logger.info("Creado %s con %s clubes detectados.", csv_path, len(rows))


def bootstrap_venue_map(csv_path: Path, records: list[MatchRecord], logger: logging.Logger) -> None:
    if csv_path.exists() and csv_path.stat().st_size > 0:
        return

    ensure_directory(csv_path.parent)
    seen: set[str] = set()
    rows: list[dict[str, str]] = []
    for record in records:
        venue = record.campo_original
        key = normalize_lookup_key(venue)
        if not venue or key in seen:
            continue
        seen.add(key)
        rows.append(
            {
                "source_name": venue,
                "display_name": clean_venue_name(venue),
            }
        )

    with csv_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["source_name", "display_name"])
        writer.writeheader()
        writer.writerows(sorted(rows, key=lambda item: normalize_lookup_key(item["source_name"])))

    logger.info("Creado %s con %s sedes detectadas.", csv_path, len(rows))


def load_lookup_map(csv_path: Path) -> dict[str, dict[str, str]]:
    if not csv_path.exists() or csv_path.stat().st_size == 0:
        return {}

    with csv_path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        mapping: dict[str, dict[str, str]] = {}
        for row in reader:
            source_name = (row.get("source_name") or "").strip()
            if not source_name:
                continue
            mapping[normalize_lookup_key(source_name)] = {key: (value or "").strip() for key, value in row.items()}
        return mapping


def resolve_club_display_name(team_name: str, club_map: dict[str, dict[str, str]]) -> str:
    entry = club_map.get(normalize_lookup_key(team_name))
    if entry and entry.get("display_name"):
        return entry["display_name"]
    return clean_club_name(team_name)


def resolve_venue_display_name(venue_name: str, venue_map: dict[str, dict[str, str]]) -> str:
    entry = venue_map.get(normalize_lookup_key(venue_name))
    if entry and entry.get("display_name"):
        return entry["display_name"]
    return clean_venue_name(venue_name)


def resolve_logo_path(
    team_name: str,
    club_map: dict[str, dict[str, str]],
    logos_dir: Path,
) -> Path | None:
    entry = club_map.get(normalize_lookup_key(team_name))
    if entry:
        logo_filename = entry.get("logo_filename", "").strip()
        if logo_filename:
            candidate = logos_dir / logo_filename
            if candidate.exists():
                return candidate
    return infer_logo_path(team_name, logos_dir)
