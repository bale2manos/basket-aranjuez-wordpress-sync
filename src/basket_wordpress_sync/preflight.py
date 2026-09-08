from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass(slots=True)
class PreflightReport:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    checks: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors

    def format(self) -> str:
        lines = ["Comprobacion previa:"]
        lines.extend(f"- OK: {item}" for item in self.checks)
        lines.extend(f"- AVISO: {item}" for item in self.warnings)
        lines.extend(f"- ERROR: {item}" for item in self.errors)
        return "\n".join(lines)


def run_preflight(
    *,
    settings,
    site_key: str,
    docx_path: Path,
    sections: list[str],
    entry_url_overrides: dict[str, str] | None = None,
) -> PreflightReport:
    report = PreflightReport()
    if docx_path.exists() and docx_path.is_file():
        report.checks.append(f"Word encontrado: {docx_path}")
    else:
        report.errors.append(f"No se encuentra el Word: {docx_path}")

    try:
        site = settings.site(site_key)
        report.checks.append(f"Sitio configurado: {site_key}")
    except KeyError as exc:
        report.errors.append(str(exc))
        return report

    storage_state = (settings.root_dir / site.storage_state_path).resolve()
    if storage_state.exists():
        report.checks.append("Sesion de WordPress encontrada")
    else:
        report.errors.append(
            f"No existe la sesion de WordPress. Ejecuta auth-login --site-key {site_key}."
        )

    for section in sections:
        entry_url = (entry_url_overrides or {}).get(section, "").strip()
        if not entry_url:
            entry_url = settings.runtime.section_entry_urls.get(section, "").strip()
        if entry_url.startswith(("http://", "https://")):
            report.checks.append(f"URL configurada para {section}")
        else:
            report.errors.append(f"No hay una URL valida configurada para la seccion '{section}'.")

    if settings.runtime.photos_zip_available:
        photos_zip = settings.runtime.photos_zip_path
        if photos_zip and photos_zip.exists():
            report.checks.append(f"ZIP de fotos encontrado: {photos_zip}")
        else:
            report.warnings.append("El ZIP de fotos esta activado pero no se encuentra; se usara el fallback configurado.")
    else:
        report.checks.append("Fotos ZIP desactivadas; se conserva el fallback configurado")

    fallback = (settings.root_dir / settings.runtime.photos_dir / settings.runtime.photo_fallback_name).resolve()
    if fallback.exists():
        report.checks.append(f"Fallback de foto encontrado: {fallback.name}")
    else:
        report.warnings.append(f"No se encuentra el fallback de foto: {fallback}")

    for group in ("login", "editor", "preview"):
        if isinstance(settings.selectors.get(group), dict):
            report.checks.append(f"Selectores cargados: {group}")
        else:
            report.errors.append(f"Falta el grupo de selectores '{group}'.")
    return report
