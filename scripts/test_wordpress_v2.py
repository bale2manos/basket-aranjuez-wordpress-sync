"""Exercise visual widget editing on an isolated, unpublished historical copy."""
from __future__ import annotations

import argparse
from dataclasses import replace
from datetime import date
from html import unescape
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

from playwright.sync_api import sync_playwright
from basket_wordpress_sync.artifacts import create_run_artifacts
from basket_wordpress_sync.logging_utils import build_logger
from basket_wordpress_sync.normalizer.text import normalize_lookup_key, format_team_display
from basket_wordpress_sync.parser import ChronicleParser
from basket_wordpress_sync.planner import DryRunPlanner
from basket_wordpress_sync.models import DeletionPlan
from basket_wordpress_sync.settings import load_settings
from basket_wordpress_sync.wordpress.api import WordPressApi
from basket_wordpress_sync.wordpress.service import ElementorUpdateService


def widgets(post):
    data = post["meta"]["_elementor_data"]
    if isinstance(data, str):
        data = json.loads(data)
    def walk(nodes):
        for node in nodes:
            if node.get("widgetType"):
                yield node
            yield from walk(node.get("elements") or [])
    return list(walk(data))


def plain(value):
    return re.sub(r"\s+", " ", unescape(re.sub(r"<[^>]+>", " ", str(value)))).strip()


def verify_saved(before, after, mapped, service):
    original = {node["id"]: node for node in widgets(before)}
    current = widgets(after)
    errors = []
    for item in mapped:
        name = item.team_config.web_block_name
        start = next((i for i, node in enumerate(current) if node.get("widgetType") == "heading" and normalize_lookup_key(plain(node.get("settings", {}).get("title", ""))) == normalize_lookup_key(name)), None)
        if start is None:
            errors.append(f"No se guardo el encabezado: {name}")
            continue
        following = current[start:]
        names = {normalize_lookup_key(name) for team in service.settings.teams
                 for name in [team.web_block_name, *team.anchor_aliases]}
        end = next((i for i, node in enumerate(following[1:], 1) if node.get("widgetType") == "heading" and normalize_lookup_key(plain(node.get("settings", {}).get("title", ""))) in names), len(following))
        block = following[:end]
        chronicle = item.parsed_chronicle
        values = [plain(node.get("settings", {}).get("title", "")) for node in block if node.get("widgetType") == "heading"
                  and normalize_lookup_key(plain(node.get("settings", {}).get("title", ""))) != "PATROCINADOR"]
        expected = [name, service._competition_value(item, chronicle), service._group_value(item, chronicle)]
        cursor = 3
        base = service.settings.runtime.section_period_counts[item.team_config.section]
        for match in item.all_chronicles:
            label_start = cursor + 3
            label_end = label_start
            while label_end < len(values) and re.fullmatch(r"(?:\d+[CP]|PR\d*|TE\d*)", values[label_end], re.I):
                label_end += 1
            count = label_end - label_start
            if count < max(base, len(match.partials)):
                errors.append(f"{name}: faltan periodos en un partido guardado")
            periods = service._build_period_updates(count, match, regulation_count=base)
            expected += [format_team_display(match.home_team), f"{match.home_score} - {match.away_score}", format_team_display(match.away_team)]
            expected += [p["label"] for p in periods]
            expected += [format_team_display(match.home_team)] + [p["home_value"] for p in periods]
            expected += [format_team_display(match.away_team)] + [p["away_value"] for p in periods]
            cursor += 3 + count + 2 * (count + 1)
        for index, (wanted, actual) in enumerate(zip(expected, values)):
            if wanted is not None and plain(wanted) != actual:
                errors.append(f"{name}: campo {index}: esperado {wanted!r}, guardado {actual!r}")
        if len(values) != len(expected):
            errors.append(f"{name}: numero inesperado de campos de tanteos")
        editors = [node for node in block if node.get("widgetType") == "text-editor"]
        texts = [plain(node.get("settings", {}).get("editor", "")) for node in editors]
        if len(editors) != 2 * len(item.all_chronicles):
            errors.append(f"{name}: numero inesperado de editores de partido")
        for index, match in enumerate(item.all_chronicles):
            body = texts[2 * index + 1] if 2 * index + 1 < len(texts) else ""
            paragraphs = [p for p in match.body_paragraphs if p.strip()] or [service.settings.runtime.empty_chronicle_placeholder]
            for paragraph in paragraphs:
                if plain(paragraph) not in body:
                    errors.append(f"{name}: falta un parrafo de cronica en el partido {index + 1}")
        for node in editors:
            old = original.get(node["id"])
            if old and re.match(r"^(?:[12]\s*[\u00bao.]?\s*Entrenador|Jugadores\s*:|Jugadoras\s*:)", plain(old.get("settings", {}).get("editor", "")), re.I):
                if old.get("settings", {}).get("editor") != node.get("settings", {}).get("editor"):
                    errors.append(f"{name}: se ha modificado el roster")
        images = [node for node in block if "basket-sync-team-photo" in node.get("settings", {}).get("_css_classes", "")]
        if len(images) != 1 or not images[0].get("settings", {}).get("image", {}).get("id"):
            errors.append(f"{name}: no hay una unica foto de equipo guardada")
    current_by_id = {node["id"]: node for node in current}
    for node_id, old in original.items():
        if old.get("widgetType") == "thegem-styledimage" and old != current_by_id.get(node_id):
            errors.append(f"Se ha modificado una imagen de patrocinador: {node_id}")
    return errors


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-id", type=int, required=True)
    parser.add_argument("--section", required=True)
    parser.add_argument("--docx", type=Path, required=True)
    parser.add_argument("--test-id", type=int, help="Reutiliza exclusivamente una copia PRUEBA V2 en borrador.")
    parser.add_argument("--remove-team", help="Prueba solo la eliminacion completa de este equipo en la copia.")
    parser.add_argument("--team", help="Prueba solo este equipo del perfil en la copia.")
    parser.add_argument("--rename-template-team", nargs=2, metavar=("OLD", "NEW"), help="Prepara un encabezado de la copia historica para probar otro equipo del perfil actual.")
    args = parser.parse_args()
    settings = load_settings(ROOT)
    settings.runtime = replace(settings.runtime, photos_zip_available=False, results_overrides_path=None)
    artifacts = create_run_artifacts(ROOT, settings.runtime, "v2_live_test")
    logger = build_logger("basket_wordpress_sync.v2_test", artifacts.human_log_path, artifacts.json_log_path)
    site = settings.site(settings.runtime.default_site_key)
    chronicles = ChronicleParser().parse_docx(args.docx)
    plan = DryRunPlanner(settings.teams).build_plan(args.section, chronicles)
    if any(event.level == "error" for event in plan.errors):
        raise RuntimeError("El mapeo tiene errores; no se crea ninguna copia.")
    with sync_playwright() as playwright:
        request = playwright.request.new_context(storage_state=str(ROOT / site.storage_state_path))
        try:
            api = WordPressApi(request, site.login_url)
            source = api.get_post(args.source_id)
            if date.fromisoformat(source["date"][:10]) >= date(2026, 10, 17):
                raise ValueError("La referencia de prueba debe ser anterior al 17 de octubre.")
            if args.test_id:
                clone = api.get_post(args.test_id)
                if clone["status"] != "draft" or not clone["title"].get("raw", "").startswith("PRUEBA V2 - "):
                    raise ValueError("El destino no es una copia de prueba en borrador.")
                fingerprint = api.fingerprint(source)
            else:
                clone, fingerprint = api.create_test_copy(args.source_id)
            before = api.get_post(clone["id"])
            if args.rename_template_team:
                old_name, new_name = args.rename_template_team
                if new_name not in {team.web_block_name for team in settings.teams}:
                    raise ValueError("El nombre de prueba no pertenece al perfil de temporada.")
                template = before["meta"]["_elementor_data"]
                template = json.loads(template) if isinstance(template, str) else template
                changed = []
                def prepare(nodes):
                    for node in nodes:
                        if node.get("widgetType") == "heading" and normalize_lookup_key(plain(node.get("settings", {}).get("title", ""))) == normalize_lookup_key(old_name):
                            node["settings"]["title"] = new_name
                            changed.append(node["id"])
                        prepare(node.get("elements") or [])
                prepare(template)
                if len(changed) != 1:
                    raise ValueError("No se encuentra un unico encabezado para preparar la copia.")
                api._json("post", f"posts/{clone['id']}", data={"meta": {"_elementor_data": json.dumps(template, ensure_ascii=False)}})
                before = api.get_post(clone["id"])
            present = {normalize_lookup_key(plain(node.get("settings", {}).get("title", ""))) for node in widgets(before) if node.get("widgetType") == "heading"}
            mapped = [item for item in plan.updates if normalize_lookup_key(item.team_config.web_block_name) in present]
            if args.team:
                mapped = [item for item in mapped if normalize_lookup_key(item.team_config.web_block_name) == normalize_lookup_key(args.team)]
            if not mapped:
                raise RuntimeError("La plantilla historica no contiene equipos del perfil nuevo.")
            deletions = []
            removed_ids = set()
            if args.remove_team:
                removed = next((item for item in mapped if normalize_lookup_key(item.team_config.web_block_name) == normalize_lookup_key(args.remove_team)), None)
                if removed is None:
                    raise ValueError("El equipo que se quiere eliminar no existe en esta copia.")
                before_widgets = widgets(before)
                names = {normalize_lookup_key(item.team_config.web_block_name) for item in plan.updates}
                in_removed_block = False
                for node in before_widgets:
                    title = normalize_lookup_key(plain(node.get("settings", {}).get("title", ""))) if node.get("widgetType") == "heading" else ""
                    if title in names:
                        in_removed_block = title == normalize_lookup_key(args.remove_team)
                    if in_removed_block:
                        removed_ids.add(node["id"])
                deletions = [DeletionPlan(removed.team_config, "remove_widget_or_container")]
                mapped = []
            url = f"{api.origin}/wp-admin/post.php?post={clone['id']}&action=elementor"
            print(f"Copia de prueba: {url}", flush=True)
            print(f"Equipos a probar: {[item.team_config.web_block_name for item in mapped]}", flush=True)
        finally:
            request.dispose()
    service = ElementorUpdateService(settings=settings, event_logger=logger, artifacts=artifacts)
    status = service.update_section(site_key=site.site_key, entry_url=url, mapped_chronicles=mapped, deletions=deletions)
    with sync_playwright() as playwright:
        request = playwright.request.new_context(storage_state=str(ROOT / site.storage_state_path))
        try:
            api = WordPressApi(request, site.login_url)
            after = api.get_post(clone["id"])
            errors = [] if removed_ids else verify_saved(before, after, mapped, service)
            if removed_ids:
                after_ids = {node["id"] for node in widgets(after)}
                if removed_ids & after_ids:
                    errors.append("Quedan widgets del equipo eliminado")
                old_kept = {node["id"]: node for node in widgets(before) if node["id"] not in removed_ids}
                new_kept = {node["id"]: node for node in widgets(after) if node["id"] not in removed_ids}
                if old_kept != new_kept:
                    errors.append("La eliminacion ha alterado widgets de otros equipos")
            if after["status"] != "draft":
                errors.append("La copia no esta en borrador")
            if fingerprint != api.fingerprint(api.get_post(args.source_id)):
                errors.append("La entrada original ha cambiado")
            if not status.saved or status.failed:
                errors.append("La automatizacion no completo el guardado")
            report = {"source_id": source["id"], "test_id": clone["id"], "editor_url": url, "saved_as_draft": after["status"] == "draft", "source_unchanged": fingerprint == api.fingerprint(api.get_post(args.source_id)), "removed_team": args.remove_team, "removed_widget_count": len(removed_ids), "errors": errors}
            (artifacts.output_dir / "validation.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
            print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)
            return 1 if errors else 0
        finally:
            request.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
