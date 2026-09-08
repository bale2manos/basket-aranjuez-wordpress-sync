from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from ..artifacts import create_run_artifacts
from ..enrichment import enrich_chronicles_with_result_overrides
from ..json_utils import to_primitive
from ..logging_utils import build_logger
from ..models import EntryTarget
from ..normalizer.text import matches_target_section, normalize_lookup_key
from ..parser import ChronicleParser
from ..planner import DryRunPlanner
from ..settings import load_settings


ROOT_DIR = Path(__file__).resolve().parents[3]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Automatizador local de WordPress/Elementor para Basket Aranjuez.")
    parser.add_argument("--config-dir", type=Path)
    subparsers = parser.add_subparsers(dest="command", required=True)

    auth_parser = subparsers.add_parser("auth-login", help="Abre el navegador para iniciar sesión y guardar storage_state.")
    auth_parser.add_argument("--site-key")

    dry_run_parser = subparsers.add_parser("dry-run", help="Parsea el docx, mapea equipos y genera el plan sin tocar WordPress.")
    dry_run_parser.add_argument("--docx", type=Path)
    dry_run_parser.add_argument("--section", required=True)

    dry_run_all_parser = subparsers.add_parser("dry-run-all", help="Genera el dry-run de todas las secciones configuradas.")
    dry_run_all_parser.add_argument("--docx", type=Path)

    update_parser = subparsers.add_parser("update-block", help="Actualiza un único bloque en Elementor.")
    update_parser.add_argument("--docx", type=Path)
    update_parser.add_argument("--section", required=True)
    update_parser.add_argument("--team", required=True)
    update_parser.add_argument("--entry-url")
    update_parser.add_argument("--site-key")

    update_section_parser = subparsers.add_parser("update-section", help="Actualiza automáticamente todos los equipos mapeados de una sección.")
    update_section_parser.add_argument("--docx", type=Path)
    update_section_parser.add_argument("--section", required=True)
    update_section_parser.add_argument("--entry-url")
    update_section_parser.add_argument("--site-key")

    update_all_parser = subparsers.add_parser("update-all", help="Actualiza automáticamente todas las secciones configuradas.")
    update_all_parser.add_argument("--docx", type=Path)
    update_all_parser.add_argument("--site-key")

    update_weekend_parser = subparsers.add_parser("update-weekend", help="Compatibilidad: actualiza Alevín y Senior en una sola ejecución.")
    update_weekend_parser.add_argument("--docx", type=Path)
    update_weekend_parser.add_argument("--senior-entry-url")
    update_weekend_parser.add_argument("--alevin-entry-url")
    update_weekend_parser.add_argument("--site-key")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    settings = load_settings(ROOT_DIR, args.config_dir)

    if args.command == "auth-login":
        return _run_auth_login(args, settings)
    if args.command == "dry-run":
        return _run_dry_run(args, settings)
    if args.command == "dry-run-all":
        return _run_dry_run_all(args, settings)
    if args.command == "update-block":
        return _run_update_block(args, settings)
    if args.command == "update-section":
        return _run_update_section(args, settings)
    if args.command == "update-all":
        return _run_update_all(args, settings)
    if args.command == "update-weekend":
        return _run_update_weekend(args, settings)
    parser.error(f"Comando no soportado: {args.command}")
    return 2


def _run_auth_login(args: argparse.Namespace, settings) -> int:
    artifacts = create_run_artifacts(ROOT_DIR, settings.runtime, "auth_login")
    logger = build_logger("basket_wordpress_sync.auth", artifacts.human_log_path, artifacts.json_log_path)
    site_key = _resolve_site_key(args.site_key, settings)
    logger.record("info", "auth_login", f"Preparando login manual para '{site_key}'.")

    from ..wordpress.service import WordPressSessionService

    service = WordPressSessionService(settings=settings, event_logger=logger)
    service.auth_login(site_key)
    print(f"Sesión guardada en: {settings.site(site_key).storage_state_path}")
    return 0


def _run_dry_run(args: argparse.Namespace, settings) -> int:
    section = _require_known_section(args.section, settings)
    artifacts = create_run_artifacts(ROOT_DIR, settings.runtime, f"dry_run_{section}")
    logger = build_logger("basket_wordpress_sync.dry_run", artifacts.human_log_path, artifacts.json_log_path)
    docx_path = _resolve_docx_path(args.docx, settings)
    logger.record("info", "dry_run_start", f"Analizando {docx_path} para la sección {section}.")

    chronicles = _parse_chronicles(docx_path, settings)
    plan = _build_section_plan(section, chronicles, settings)

    _write_json(artifacts.output_dir / "parsed_chronicles.json", chronicles)
    _write_json(artifacts.output_dir / "run_plan.json", plan)
    print(_format_plan_summary(section, settings, plan, chronicles))
    print(f"Artefactos: {artifacts.output_dir}")
    return 0


def _run_dry_run_all(args: argparse.Namespace, settings) -> int:
    docx_path = _resolve_docx_path(args.docx, settings)
    chronicles = _parse_chronicles(docx_path, settings)
    has_errors = False
    for section in settings.configured_sections():
        artifacts = create_run_artifacts(ROOT_DIR, settings.runtime, f"dry_run_{section}")
        plan = _build_section_plan(section, chronicles, settings)
        _write_json(artifacts.output_dir / "parsed_chronicles.json", chronicles)
        _write_json(artifacts.output_dir / "run_plan.json", plan)
        print(_format_plan_summary(section, settings, plan, chronicles))
        print(f"Artefactos: {artifacts.output_dir}")
        print("")
        if any(event.level == "error" for event in plan.errors):
            has_errors = True
    return 1 if has_errors else 0


def _run_update_block(args: argparse.Namespace, settings) -> int:
    section = _require_known_section(args.section, settings)
    artifacts = create_run_artifacts(ROOT_DIR, settings.runtime, "update_block")
    logger = build_logger("basket_wordpress_sync.update_block", artifacts.human_log_path, artifacts.json_log_path)
    docx_path = _resolve_docx_path(args.docx, settings)
    entry_url = _resolve_entry_url(section, args.entry_url, settings)
    site_key = _resolve_site_key(args.site_key, settings)
    logger.record("info", "update_block_start", f"Preparando actualización de '{args.team}' en {entry_url}.")

    chronicles = _parse_chronicles(docx_path, settings)
    entry_target = EntryTarget(mode="url", value=entry_url)
    plan = _build_section_plan(section, chronicles, settings, entry_target=entry_target)
    _write_json(artifacts.output_dir / "parsed_chronicles.json", chronicles)
    _write_json(artifacts.output_dir / "run_plan.json", plan)

    mapped = _find_mapped_team(plan, args.team)
    if mapped is None:
        logger.record("error", "mapping_error", f"No se encontró un bloque mapeado para '{args.team}'.")
        print(_format_plan_summary(section, settings, plan, chronicles))
        return 1

    from ..wordpress.service import ElementorUpdateService

    service = ElementorUpdateService(settings=settings, event_logger=logger, artifacts=artifacts)
    service.update_single_block(site_key=site_key, entry_url=entry_url, mapped_chronicle=mapped)
    print(_format_plan_summary(section, settings, plan, chronicles))
    print(f"Artefactos: {artifacts.output_dir}")
    return 0


def _run_update_section(args: argparse.Namespace, settings) -> int:
    section = _require_known_section(args.section, settings)
    docx_path = _resolve_docx_path(args.docx, settings)
    entry_url = _resolve_entry_url(section, args.entry_url, settings)
    site_key = _resolve_site_key(args.site_key, settings)
    chronicles = _parse_chronicles(docx_path, settings)
    status = _execute_section_update(
        section=section,
        entry_url=entry_url,
        site_key=site_key,
        settings=settings,
        chronicles=chronicles,
        command_name=f"update_section_{section}",
    )
    return 0 if not status["failed"] else 1


def _run_update_all(args: argparse.Namespace, settings) -> int:
    docx_path = _resolve_docx_path(args.docx, settings)
    site_key = _resolve_site_key(args.site_key, settings)
    chronicles = _parse_chronicles(docx_path, settings)
    statuses: dict[str, dict[str, object]] = {}

    for section in settings.configured_sections():
        entry_url = _resolve_entry_url(section, None, settings)
        statuses[section] = _execute_section_update(
            section=section,
            entry_url=entry_url,
            site_key=site_key,
            settings=settings,
            chronicles=chronicles,
            command_name=f"update_section_{section}",
        )

    print("")
    print("Resumen global:")
    for section in settings.configured_sections():
        status = statuses[section]
        print(f"- {section}: {len(status['successful'])} OK, {len(status['failed'])} fallidos")
    return 0 if all(not status["failed"] for status in statuses.values()) else 1


def _run_update_weekend(args: argparse.Namespace, settings) -> int:
    docx_path = _resolve_docx_path(args.docx, settings)
    site_key = _resolve_site_key(args.site_key, settings)
    chronicles = _parse_chronicles(docx_path, settings)
    senior_status = _execute_section_update(
        section="senior",
        entry_url=_resolve_entry_url("senior", args.senior_entry_url, settings),
        site_key=site_key,
        settings=settings,
        chronicles=chronicles,
        command_name="update_section_senior",
    )
    alevin_status = _execute_section_update(
        section="alevin",
        entry_url=_resolve_entry_url("alevin", args.alevin_entry_url, settings),
        site_key=site_key,
        settings=settings,
        chronicles=chronicles,
        command_name="update_section_alevin",
    )

    print("")
    print("Resumen global:")
    print(f"- senior: {len(senior_status['successful'])} OK, {len(senior_status['failed'])} fallidos")
    print(f"- alevin: {len(alevin_status['successful'])} OK, {len(alevin_status['failed'])} fallidos")
    return 0 if not senior_status["failed"] and not alevin_status["failed"] else 1


def _execute_section_update(
    *,
    section: str,
    entry_url: str,
    site_key: str,
    settings,
    chronicles: list,
    command_name: str,
) -> dict[str, object]:
    artifacts = create_run_artifacts(ROOT_DIR, settings.runtime, command_name)
    logger = build_logger(f"basket_wordpress_sync.{command_name}", artifacts.human_log_path, artifacts.json_log_path)
    logger.record("info", "update_section_start", f"Preparando actualización automática de la sección '{section}' en {entry_url}.")

    entry_target = EntryTarget(mode="url", value=entry_url)
    plan = _build_section_plan(section, chronicles, settings, entry_target=entry_target)
    _write_json(artifacts.output_dir / "parsed_chronicles.json", chronicles)
    _write_json(artifacts.output_dir / "run_plan.json", plan)

    from ..wordpress.service import ElementorUpdateService

    service = ElementorUpdateService(settings=settings, event_logger=logger, artifacts=artifacts)
    ordered_updates = sorted(plan.updates, key=lambda item: item.team_config.expected_order)
    status = service.update_section(
        site_key=site_key,
        entry_url=entry_url,
        mapped_chronicles=ordered_updates,
        deletions=plan.deletions,
    )

    print(_format_plan_summary(section, settings, plan, chronicles))
    print("Procesados correctamente:")
    if status.successful:
        for item in status.successful:
            print(f"- {item}")
    else:
        print("- ninguno")
    print("")
    print("Equipos con cambios guardados:")
    if status.changed_teams:
        for item in status.changed_teams:
            print(f"- {item}")
    else:
        print("- ninguno")
    print("")
    print("Fallidos:")
    if status.failed:
        for item in status.failed:
            print(f"- {item}")
    else:
        print("- ninguno")
    print("")
    print("Equipos limpiados por ausencia en el Word:")
    if status.cleared_teams:
        for item in status.cleared_teams:
            print(f"- {item}")
    else:
        print("- ninguno")
    print("")
    print("Omitidos por seguridad:")
    if status.skipped:
        for item in status.skipped:
            print(f"- {item}")
    else:
        print("- ninguno")
    print("")
    print(f"Guardado final: {'si' if status.saved else 'no'}")
    print(f"Artefactos: {artifacts.output_dir}")

    return {
        "successful": status.successful,
        "failed": status.failed,
        "skipped": status.skipped,
        "changed_teams": status.changed_teams,
        "cleared_teams": status.cleared_teams,
        "saved": status.saved,
        "artifacts": artifacts.output_dir,
    }


def _parse_chronicles(docx_path: Path, settings) -> list:
    parser = ChronicleParser(
        period_label_templates=settings.runtime.period_label_templates,
        overtime_label=settings.runtime.overtime_label,
    )
    chronicles = parser.parse_docx(docx_path.resolve())
    enrich_chronicles_with_result_overrides(
        docx_path=docx_path.resolve(),
        chronicles=chronicles,
        teams=settings.teams,
        overrides_path=settings.runtime.results_overrides_path,
    )
    return chronicles


def _build_section_plan(section: str, chronicles: list, settings, entry_target: EntryTarget | None = None):
    planner = DryRunPlanner(settings.teams, delete_mode=settings.runtime.delete_mode)
    return planner.build_plan(section, chronicles, entry_target=entry_target)


def _find_mapped_team(plan, team_name: str):
    team_key = normalize_lookup_key(team_name)
    for mapped in plan.updates:
        if normalize_lookup_key(mapped.team_config.web_block_name) == team_key:
            return mapped
    return None


def _resolve_docx_path(value: Path | None, settings) -> Path:
    if value is not None:
        return value.resolve()
    if settings.runtime.default_docx_path is not None:
        return settings.runtime.default_docx_path.resolve()
    raise FileNotFoundError("No hay DOCX configurado. Añádelo en config.py o pásalo con --docx.")


def _resolve_entry_url(section: str, override: str | None, settings) -> str:
    if override:
        return override
    configured = settings.runtime.section_entry_urls.get(section, "").strip()
    if configured:
        return configured
    raise ValueError(f"No hay entry_url configurada para la sección '{section}'. Añádela en config.py o pásala con --entry-url.")


def _resolve_site_key(value: str | None, settings) -> str:
    return (value or settings.runtime.default_site_key or "basket-aranjuez").strip()


def _require_known_section(section: str, settings) -> str:
    normalized = normalize_lookup_key(section).replace(" ", "_").lower()
    available = {normalize_lookup_key(item).replace(" ", "_").lower(): item for item in settings.configured_sections()}
    if normalized not in available:
        known = ", ".join(settings.configured_sections())
        raise ValueError(f"Sección desconocida: {section}. Secciones configuradas: {known}")
    return available[normalized]


def _write_json(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(to_primitive(payload), ensure_ascii=False, indent=2), encoding="utf-8")


def _format_plan_summary(section: str, settings, plan, chronicles: list) -> str:
    section_teams = [team for team in settings.teams if team.section == section]
    relevant = [
        chronicle
        for chronicle in chronicles
        if matches_target_section(section, chronicle_section=chronicle.section, heading=chronicle.raw_heading)
    ]
    ignored = [chronicle.raw_heading for chronicle in plan.ignored]
    errors = [event.message for event in plan.errors if event.level == "error"]
    warnings = [event.message for event in plan.errors if event.level != "error"]

    lines = [
        f"Entrada objetivo: {section}",
        f"Equipos detectados en Word: {len(relevant)}",
        f"Equipos esperados en config: {len(section_teams)}",
        "Equipos a actualizar:",
    ]
    if plan.updates:
        lines.extend(f"- {item.team_config.web_block_name}" for item in plan.updates)
    else:
        lines.append("- ninguno")
    lines.append("")
    lines.append("Equipos ausentes y a eliminar:")
    if plan.deletions:
        lines.extend(f"- {item.team_config.web_block_name}" for item in plan.deletions)
    else:
        lines.append("- ninguno")
    lines.append("")
    lines.append("Bloques ignorados por no pertenecer a la sección:")
    if ignored:
        lines.extend(f"- {item}" for item in ignored)
    else:
        lines.append("- ninguno")
    lines.append("")
    lines.append("Errores:")
    if errors:
        lines.extend(f"- {item}" for item in errors)
    else:
        lines.append("- ninguno")
    if warnings:
        lines.append("")
        lines.append("Warnings:")
        lines.extend(f"- {item}" for item in warnings)
    return "\n".join(lines)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
