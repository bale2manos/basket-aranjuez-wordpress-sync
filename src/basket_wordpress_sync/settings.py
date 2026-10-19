from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from config import default_config

from .models import TeamConfig


@dataclass(slots=True)
class SiteSettings:
    site_key: str
    login_url: str
    admin_posts_url: str | None
    storage_state_path: Path


@dataclass(slots=True)
class RuntimeSettings:
    logs_dir: Path
    screenshots_dir: Path
    output_dir: Path
    state_dir: Path
    photos_dir: Path
    results_overrides_path: Path | None
    browser: str
    headless: bool
    delete_mode: str
    rich_text_mode: str
    missing_result_behavior: str
    prefer_visual_text_editor: bool
    empty_chronicle_placeholder: str
    photo_fallback_name: str
    photos_zip_available: bool
    photos_zip_path: Path | None
    default_docx_path: Path | None
    default_site_key: str
    backup_dir: Path
    abort_on_team_error: bool
    preflight_required: bool
    section_entry_urls: dict[str, str] = field(default_factory=dict)
    section_order: list[str] = field(default_factory=list)
    section_period_counts: dict[str, int] = field(default_factory=dict)
    period_label_templates: dict[int, str] = field(default_factory=dict)
    overtime_label: str = "PR"
    timeouts: dict[str, int] = field(default_factory=dict)
    retries: dict[str, int] = field(default_factory=dict)


@dataclass(slots=True)
class LoadedSettings:
    root_dir: Path
    runtime: RuntimeSettings
    selectors: dict[str, Any]
    teams: list[TeamConfig]
    sites: dict[str, SiteSettings]

    def site(self, site_key: str) -> SiteSettings:
        try:
            return self.sites[site_key]
        except KeyError as exc:
            raise KeyError(f"Site key desconocido: {site_key}") from exc

    def configured_sections(self) -> list[str]:
        discovered = [team.section for team in self.teams]
        if self.runtime.section_order:
            ordered = [section for section in self.runtime.section_order if section in discovered]
        else:
            ordered = []
        for section in discovered:
            if section not in ordered:
                ordered.append(section)
        return ordered


def _read_yaml(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        payload = yaml.safe_load(handle) or {}
    if not isinstance(payload, dict):
        raise ValueError(f"El archivo {path} no contiene un mapa YAML.")
    return payload


def _merge_maps(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """Merge user-facing unified config over the legacy YAML structure."""
    merged = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _merge_maps(merged[key], value)
        else:
            merged[key] = value
    return merged


def _resolve_config_path(root_dir: Path, value: object) -> Path:
    path = Path(str(value))
    if not path.is_absolute():
        path = root_dir / path
    return path.resolve()


def _apply_unified_config(
    root_dir: Path,
    runtime_payload: dict[str, Any],
    unified_config_path: Path | None,
) -> dict[str, Any]:
    path = unified_config_path or (root_dir / "configuracion.yaml")
    if not path.exists():
        return runtime_payload

    unified = _read_yaml(path)
    defaults = dict(runtime_payload.get("defaults") or {})
    unified_defaults = unified.get("defaults") or {}
    if isinstance(unified_defaults, dict):
        defaults = _merge_maps(defaults, unified_defaults)

    aliases = {
        "docx_path": "default_docx_path",
        "photos_zip_path": "photos_zip_path",
        "photos_zip_available": "photos_zip_available",
        "site_key": "default_site_key",
        "entry_urls": "section_entry_urls",
        "section_order": "section_order",
        "season": "season",
    }
    for source_key, target_key in aliases.items():
        value = unified.get(source_key)
        if value not in (None, "", {}):
            defaults[target_key] = value

    merged = dict(runtime_payload)
    merged["defaults"] = defaults
    return merged


def load_settings(
    root_dir: Path,
    config_dir: Path | None = None,
    unified_config_path: Path | None = None,
    *, season: str | None = None,
) -> LoadedSettings:
    config_root = (config_dir or (root_dir / "config")).resolve()
    app_config = default_config()
    runtime_payload = _read_yaml(config_root / "runtime.yaml")
    unified_path = unified_config_path
    if unified_path is not None and not unified_path.is_absolute():
        unified_path = root_dir / unified_path
    unified_path = unified_path or (root_dir / "configuracion.yaml")
    runtime_payload = _apply_unified_config(root_dir, runtime_payload, unified_path)
    selectors_payload = _read_yaml(config_root / "selectors.yaml")

    defaults = runtime_payload.get("defaults") or {}
    if not isinstance(defaults, dict):
        raise ValueError("runtime.yaml > defaults debe ser un mapa.")
    season = season or str(defaults.get("season", "2026-27"))
    season_teams = config_root / "seasons" / season / "teams.yaml"
    if season not in {"2026-27", "legacy"}:
        raise ValueError(f"Temporada desconocida: {season}")
    teams_payload = _read_yaml(season_teams if season != "legacy" and season_teams.exists() else config_root / "teams.yaml")

    runtime = RuntimeSettings(
        logs_dir=Path(str(defaults.get("logs_dir", "logs"))),
        screenshots_dir=Path(str(defaults.get("screenshots_dir", "screenshots"))),
        output_dir=Path(str(defaults.get("output_dir", "output/wordpress_sync"))),
        state_dir=Path(str(defaults.get("state_dir", "output/wordpress_sync/state"))),
        photos_dir=Path(str(defaults.get("photos_dir", "resultados/fotos"))),
        results_overrides_path=_resolve_config_path(root_dir, defaults.get("results_overrides_path", app_config.results_overrides_path))
        if defaults.get("results_overrides_path", app_config.results_overrides_path)
        else None,
        browser=str(defaults.get("browser", "chromium")),
        headless=bool(defaults.get("headless", False)),
        delete_mode=str(defaults.get("delete_mode", "clear_texts_only")),
        rich_text_mode=str(defaults.get("rich_text_mode", "visual")),
        missing_result_behavior=str(defaults.get("missing_result_behavior", "warn_and_skip")),
        prefer_visual_text_editor=bool(defaults.get("prefer_visual_text_editor", True)),
        empty_chronicle_placeholder=str(defaults.get("empty_chronicle_placeholder", "-")),
        photo_fallback_name=str(defaults.get("photo_fallback_name", "basket_aranjuez.png")),
        photos_zip_available=bool(
            defaults.get("photos_zip_available", app_config.wordpress_sync_has_photos_zip)
        ),
        photos_zip_path=_resolve_config_path(
            root_dir, defaults.get("photos_zip_path", app_config.wordpress_sync_photos_zip_path)
        )
        if defaults.get("photos_zip_path", app_config.wordpress_sync_photos_zip_path)
        else None,
        default_docx_path=_resolve_config_path(
            root_dir,
            defaults.get("default_docx_path", app_config.wordpress_sync_docx_path or app_config.results_doc_path),
        )
        if defaults.get("default_docx_path", app_config.wordpress_sync_docx_path or app_config.results_doc_path)
        else None,
        default_site_key=str(defaults.get("default_site_key", app_config.wordpress_sync_site_key or "basket-aranjuez")),
        backup_dir=Path(str(defaults.get("backup_dir", "output/wordpress_sync/backups"))),
        abort_on_team_error=bool(defaults.get("abort_on_team_error", True)),
        preflight_required=bool(defaults.get("preflight_required", True)),
        section_entry_urls={
            str(key): str(value)
            for key, value in (
                defaults.get("section_entry_urls")
                or app_config.wordpress_sync_entry_urls
                or {}
            ).items()
            if str(value).strip()
        },
        section_order=[str(item) for item in (defaults.get("section_order") or app_config.wordpress_sync_section_order or []) if str(item).strip()],
        section_period_counts={str(key): int(value) for key, value in (defaults.get("section_period_counts") or {}).items()},
        period_label_templates={
            int(key): str(value)
            for key, value in (defaults.get("period_label_templates") or {}).items()
        },
        overtime_label=str(defaults.get("overtime_label", "PR")),
        timeouts={str(key): int(value) for key, value in (defaults.get("timeouts") or {}).items()},
        retries={str(key): int(value) for key, value in (defaults.get("retries") or {}).items()},
    )

    sites: dict[str, SiteSettings] = {}
    for site_key, site_payload in (runtime_payload.get("sites") or {}).items():
        if not isinstance(site_payload, dict):
            continue
        sites[str(site_key)] = SiteSettings(
            site_key=str(site_key),
            login_url=str(site_payload.get("login_url", "")),
            admin_posts_url=str(site_payload.get("admin_posts_url") or "") or None,
            storage_state_path=Path(str(site_payload.get("storage_state_path", runtime.state_dir / f"{site_key}.json"))),
        )

    teams: list[TeamConfig] = []
    for section, section_payload in (teams_payload.get("sections") or {}).items():
        if not isinstance(section_payload, dict):
            continue
        for item in section_payload.get("teams") or []:
            if not isinstance(item, dict):
                continue
            teams.append(
                TeamConfig(
                    section=str(section),
                    web_block_name=str(item.get("web_block_name", "")).strip(),
                    heading_aliases=[str(alias).strip() for alias in item.get("heading_aliases") or [] if str(alias).strip()],
                    competition_aliases=[
                        str(alias).strip()
                        for alias in item.get("competition_aliases") or []
                        if str(alias).strip()
                    ],
                    result_aliases=[str(alias).strip() for alias in item.get("result_aliases") or [] if str(alias).strip()],
                    photo_aliases=[str(alias).strip() for alias in item.get("photo_aliases") or [] if str(alias).strip()],
                    display_competition=str(item.get("display_competition", "")).strip() or None,
                    display_group=str(item.get("display_group", "")).strip() or None,
                    expected_order=int(item.get("expected_order", 0) or 0),
                    match_rules=list(item.get("match_rules") or []),
                    anchor_aliases=list(item.get("anchor_aliases") or []),
                )
            )

    return LoadedSettings(
        root_dir=root_dir.resolve(),
        runtime=runtime,
        selectors=selectors_payload,
        teams=teams,
        sites=sites,
    )
