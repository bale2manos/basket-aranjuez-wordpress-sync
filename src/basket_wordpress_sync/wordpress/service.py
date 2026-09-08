from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import time
from typing import Any

from playwright.sync_api import Browser, BrowserContext, Page, sync_playwright

from ..artifacts import RunArtifacts
from ..logging_utils import EventLogger
from ..models import DeletionPlan, MappedChronicle
from ..normalizer.text import build_period_labels, format_competition_display, format_score, format_team_display
from ..photo_repository import PhotoRepository
from .adapters import BackgroundContainerAdapter, HeaderWidgetAdapter, ImageWidgetAdapter, TextEditorWidgetAdapter
from .contexts import PreviewContext, SidePanelContext, TeamSectionAnchor, count_team_anchors, duplicate_ordered_team_block


@dataclass(slots=True)
class BlockUpdateResult:
    screenshot_before: Path
    screenshot_after: Path
    screenshot_final: Path
    changed: bool = False


@dataclass(slots=True)
class SectionUpdateResult:
    successful: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    changed_teams: list[str] = field(default_factory=list)
    cleared_teams: list[str] = field(default_factory=list)
    final_screenshot: Path | None = None
    saved: bool = False
    save_blocked: bool = False


class SkipBlockUpdate(RuntimeError):
    pass


class WordPressSessionService:
    def __init__(self, *, settings, event_logger: EventLogger) -> None:
        self.settings = settings
        self.event_logger = event_logger

    def auth_login(self, site_key: str) -> Path:
        site = self.settings.site(site_key)
        storage_state_path = (self.settings.root_dir / site.storage_state_path).resolve()
        storage_state_path.parent.mkdir(parents=True, exist_ok=True)

        with sync_playwright() as playwright:
            browser_factory = getattr(playwright, self.settings.runtime.browser)
            browser = _launch_browser(browser_factory, headless=False)
            context = browser.new_context()
            page = context.new_page()
            self.event_logger.record("info", "login_open", f"Abriendo {site.login_url} para login manual.")
            page.goto(site.login_url, wait_until="load", timeout=self.settings.runtime.timeouts.get("navigation_ms", 30000))
            if self._stdin_available():
                try:
                    input("Completa el login en la ventana del navegador y pulsa Enter aqu? para guardar la sesi?n...")
                    self._ensure_authenticated_session(context, page, site)
                except EOFError:
                    self.event_logger.record(
                        "warning",
                        "login_stdin_unavailable",
                        "La entrada est?ndar no est? disponible; se esperar? autom?ticamente a que completes el login en el navegador.",
                    )
                    self._wait_for_manual_login(context, page, site)
            else:
                self.event_logger.record(
                    "info",
                    "login_wait",
                    "Esperando autom?ticamente a que completes el login en el navegador abierto.",
                )
                self._wait_for_manual_login(context, page, site)
            context.storage_state(path=str(storage_state_path))
            browser.close()

        self.event_logger.record("info", "login_saved", f"Sesi?n guardada en {storage_state_path}.")
        return storage_state_path

    def _ensure_authenticated_session(self, context: BrowserContext, page: Page, site) -> None:
        admin_url = site.admin_posts_url or site.login_url.replace("wp-login.php", "wp-admin/")
        deadline = time.monotonic() + 30
        last_url = page.url
        while time.monotonic() < deadline:
            if self._has_wordpress_login_cookie(context) and not self._is_login_url(page.url):
                return
            try:
                page.goto(admin_url, wait_until="load", timeout=self.settings.runtime.timeouts.get("navigation_ms", 30000))
                page.wait_for_timeout(1000)
            except Exception:
                pass
            last_url = page.url
            if self._has_wordpress_login_cookie(context) and not self._is_login_url(page.url):
                return
        raise RuntimeError(
            "No se detectó una sesión válida de WordPress. Haz login completo y espera a ver el panel de administración antes de pulsar Enter."
            f" URL actual: {last_url}"
        )

    def _has_wordpress_login_cookie(self, context: BrowserContext) -> bool:
        try:
            cookies = context.cookies()
        except Exception:
            return False
        for cookie in cookies:
            name = str(cookie.get("name") or "")
            if name.startswith("wordpress_logged_in_") or name.startswith("wordpress_sec_"):
                return True
        return False

    def _is_login_url(self, url: str) -> bool:
        return "wp-login.php" in (url or "")

    def _stdin_available(self) -> bool:
        try:
            import sys

            return bool(sys.stdin and sys.stdin.isatty())
        except Exception:
            return False

    def _wait_for_manual_login(self, context: BrowserContext, page: Page, site) -> None:
        deadline = time.monotonic() + 600
        while time.monotonic() < deadline:
            if self._has_wordpress_login_cookie(context) and not self._is_login_url(page.url):
                self._ensure_authenticated_session(context, page, site)
                return
            page.wait_for_timeout(1000)
        raise RuntimeError(
            "No se detectó una sesión válida de WordPress dentro del tiempo de espera. Completa el login y vuelve a intentarlo."
        )


class ElementorUpdateService:
    def __init__(self, *, settings, event_logger: EventLogger, artifacts: RunArtifacts) -> None:
        self.settings = settings
        self.event_logger = event_logger
        self.artifacts = artifacts
        self.photo_repository = PhotoRepository(
            root_dir=settings.root_dir,
            photos_dir=(settings.root_dir / settings.runtime.photos_dir).resolve(),
            cache_dir=artifacts.output_dir / "photo_assets",
            fallback_name=settings.runtime.photo_fallback_name,
            photos_zip_available=settings.runtime.photos_zip_available,
            photos_zip_path=getattr(settings.runtime, "photos_zip_path", None),
        )
        self._uploaded_fallback_media_names: set[str] = set()

    def update_single_block(self, *, site_key: str, entry_url: str, mapped_chronicle: MappedChronicle) -> BlockUpdateResult:
        if mapped_chronicle.parsed_chronicle is None:
            raise ValueError("El bloque solicitado no tiene cr?nica asociada para actualizar.")

        storage_state_path = self._storage_state_path(site_key)
        screenshot_before = self.artifacts.screenshot_dir / f"{mapped_chronicle.team_config.web_block_name}_before.png"
        screenshot_after = self.artifacts.screenshot_dir / f"{mapped_chronicle.team_config.web_block_name}_after.png"
        screenshot_final = self.artifacts.screenshot_dir / "final.png"

        with sync_playwright() as playwright:
            browser, context, page = self._open_editor(playwright, storage_state_path, entry_url)
            try:
                preview = PreviewContext(page, self.settings.selectors)
                side_panel = SidePanelContext(page, self.settings.selectors)
                preview.screenshot(screenshot_before)
                try:
                    changed_count = self._update_mapped_block(preview, side_panel, mapped_chronicle)
                except SkipBlockUpdate as exc:
                    changed_count = 0
                    self.event_logger.record(
                        "warning",
                        "team_update_skipped",
                        str(exc),
                        team=mapped_chronicle.team_config.web_block_name,
                        url=entry_url,
                    )
                preview.screenshot(screenshot_after)
                changed = changed_count > 0
                if changed:
                    side_panel.click_save()
                    page.wait_for_timeout(self.settings.runtime.timeouts.get("save_ms", 15000))
                page.screenshot(path=str(screenshot_final), full_page=True)
                if changed:
                    self.event_logger.record(
                        "info",
                        "update_completed",
                        f"Bloque '{mapped_chronicle.team_config.web_block_name}' actualizado y guardado para revisi?n.",
                        team=mapped_chronicle.team_config.web_block_name,
                        url=entry_url,
                    )
                else:
                    self.event_logger.record(
                        "warning",
                        "block_not_found",
                        f"El bloque '{mapped_chronicle.team_config.web_block_name}' no produjo cambios editables.",
                        team=mapped_chronicle.team_config.web_block_name,
                        url=entry_url,
                    )
            except Exception as exc:
                error_shot = self.artifacts.screenshot_dir / "error.png"
                page.screenshot(path=str(error_shot), full_page=True)
                self.event_logger.record(
                    "error",
                    "widget_edit_error",
                    f"Fall? la actualizaci?n del bloque '{mapped_chronicle.team_config.web_block_name}': {exc}",
                    team=mapped_chronicle.team_config.web_block_name,
                    screenshot=str(error_shot),
                )
                raise
            finally:
                browser.close()

        return BlockUpdateResult(
            screenshot_before=screenshot_before,
            screenshot_after=screenshot_after,
            screenshot_final=screenshot_final,
            changed=changed,
        )

    def update_section(
        self,
        *,
        site_key: str,
        entry_url: str,
        mapped_chronicles: list[MappedChronicle],
        deletions: list[DeletionPlan] | None = None,
    ) -> SectionUpdateResult:
        if not mapped_chronicles and not deletions:
            return SectionUpdateResult()

        storage_state_path = self._storage_state_path(site_key)
        final_screenshot = self.artifacts.screenshot_dir / "final.png"
        result = SectionUpdateResult(final_screenshot=final_screenshot)

        with sync_playwright() as playwright:
            browser, context, page = self._open_editor(playwright, storage_state_path, entry_url)
            try:
                preview = PreviewContext(page, self.settings.selectors)
                side_panel = SidePanelContext(page, self.settings.selectors)
                any_changes = False
                had_team_error = False

                for mapped in mapped_chronicles:
                    team_name = mapped.team_config.web_block_name
                    screenshot_before = self.artifacts.screenshot_dir / f"{team_name}_before.png"
                    screenshot_after = self.artifacts.screenshot_dir / f"{team_name}_after.png"
                    try:
                        preview.screenshot(screenshot_before)
                        changed_count = self._update_mapped_block(preview, side_panel, mapped)
                        preview.screenshot(screenshot_after)
                        if changed_count <= 0:
                            raise LookupError(f"No se encontr? ning?n widget editable para '{team_name}'.")
                        result.successful.append(team_name)
                        result.changed_teams.append(team_name)
                        any_changes = True
                        page.wait_for_timeout(self.settings.runtime.timeouts.get("ui_ms", 10000) // 4)
                    except SkipBlockUpdate as exc:
                        result.skipped.append(team_name)
                        self.event_logger.record(
                            "warning",
                            "team_update_skipped",
                            str(exc),
                            section=mapped.team_config.section,
                            team=team_name,
                            url=entry_url,
                        )
                    except Exception as exc:
                        result.failed.append(team_name)
                        had_team_error = True
                        self.event_logger.record(
                            "error",
                            "team_update_failed",
                            f"Fall? la actualizaci?n autom?tica de '{team_name}': {exc}",
                            section=mapped.team_config.section,
                            team=team_name,
                            url=entry_url,
                        )

                deletion_items = list(deletions or [])
                deletion_items.sort(key=lambda item: int(item.team_config.expected_order or 0), reverse=True)
                for deletion in deletion_items:
                    team_name = deletion.team_config.web_block_name
                    try:
                        cleared = self._clear_deleted_block(preview, side_panel, deletion)
                        if cleared:
                            result.cleared_teams.append(team_name)
                            result.changed_teams.append(team_name)
                            any_changes = True
                    except SkipBlockUpdate as exc:
                        result.skipped.append(team_name)
                        self.event_logger.record(
                            "warning",
                            "team_delete_skipped",
                            str(exc),
                            section=deletion.team_config.section,
                            team=team_name,
                            url=entry_url,
                        )
                        if self.settings.runtime.abort_on_team_error:
                            break
                    except Exception as exc:
                        result.failed.append(team_name)
                        had_team_error = True
                        self.event_logger.record(
                            "error",
                            "team_delete_failed",
                            f"Falló la limpieza automática de '{team_name}': {exc}",
                            section=deletion.team_config.section,
                            team=team_name,
                            url=entry_url,
                        )

                if any_changes and not had_team_error:
                    side_panel.click_save()
                    page.wait_for_timeout(self.settings.runtime.timeouts.get("save_ms", 15000))
                    result.saved = True
                elif any_changes and had_team_error:
                    result.save_blocked = True
                    self.event_logger.record(
                        "error",
                        "save_blocked",
                        "Se descartaron los cambios del navegador porque hubo un error durante la seccion.",
                        section=entry_url,
                    )
                page.screenshot(path=str(final_screenshot), full_page=True)
            finally:
                browser.close()

        return result

    def _storage_state_path(self, site_key: str) -> Path:
        site = self.settings.site(site_key)
        storage_state_path = (self.settings.root_dir / site.storage_state_path).resolve()
        if not storage_state_path.exists():
            raise FileNotFoundError(
                f"No existe la sesi?n guardada para '{site_key}'. Ejecuta primero 'auth-login --site-key {site_key}'."
            )
        return storage_state_path

    def _open_editor(self, playwright, storage_state_path: Path, entry_url: str) -> tuple[Browser, BrowserContext, Page]:
        browser_factory = getattr(playwright, self.settings.runtime.browser)
        browser = _launch_browser(browser_factory, headless=self.settings.runtime.headless)
        context = browser.new_context(storage_state=str(storage_state_path))
        page = context.new_page()
        page.goto(entry_url, wait_until="load", timeout=self.settings.runtime.timeouts.get("navigation_ms", 30000))
        page.wait_for_timeout(self.settings.runtime.timeouts.get("ui_ms", 10000))
        if self._is_login_page(page):
            browser.close()
            raise RuntimeError(
                "La sesión guardada no es válida o ha caducado. Ejecuta de nuevo 'python wordpress_sync.py auth-login --site-key basket-aranjuez' y completa el login hasta llegar al panel de WordPress."
            )
        return browser, context, page

    def _is_login_page(self, page: Page) -> bool:
        if "wp-login.php" in (page.url or ""):
            return True
        try:
            return page.locator("#loginform, form[name='loginform'], #wp-submit").count() > 0
        except Exception:
            return False

    def _update_mapped_block(self, preview: PreviewContext, side_panel: SidePanelContext, mapped_chronicle: MappedChronicle) -> int:
        chronicle = mapped_chronicle.parsed_chronicle
        if chronicle is None:
            raise ValueError("El bloque solicitado no tiene cr?nica asociada para actualizar.")
        all_chronicles = self._mapped_chronicles(mapped_chronicle)
        if len(all_chronicles) > 1:
            self.event_logger.record(
                "info",
                "multiple_chronicles_team",
                f"Se combinarán {len(all_chronicles)} crónicas para '{mapped_chronicle.team_config.web_block_name}' en el texto largo.",
                team=mapped_chronicle.team_config.web_block_name,
                matches=len(all_chronicles),
            )

        section_boundary_texts = self._section_boundary_texts(mapped_chronicle.team_config.section)
        anchor_candidates = [
            chronicle.raw_heading,
            mapped_chronicle.team_config.web_block_name,
            mapped_chronicle.team_config.web_block_name.upper(),
            *mapped_chronicle.team_config.heading_aliases,
        ]
        try:
            anchor = TeamSectionAnchor.locate(
                preview,
                anchor_candidates,
                self.settings.selectors,
                boundary_texts=section_boundary_texts,
                expected_order=mapped_chronicle.team_config.expected_order,
                strict_expected_order=False,
                allow_order_without_heading_match=True,
            )
        except LookupError as exc:
            recovered = self._recover_missing_ordered_block(preview, mapped_chronicle)
            if recovered:
                anchor = TeamSectionAnchor.locate(
                    preview,
                    anchor_candidates,
                    self.settings.selectors,
                    boundary_texts=section_boundary_texts,
                    expected_order=mapped_chronicle.team_config.expected_order,
                    strict_expected_order=False,
                    allow_order_without_heading_match=True,
                )
                return self._update_block(anchor=anchor, side_panel=side_panel, mapped_chronicle=mapped_chronicle)
            if self._can_recover_update_by_order(preview, mapped_chronicle):
                anchor = TeamSectionAnchor.locate(
                    preview,
                    anchor_candidates,
                    self.settings.selectors,
                    boundary_texts=section_boundary_texts,
                    expected_order=mapped_chronicle.team_config.expected_order,
                    strict_expected_order=False,
                    allow_order_without_heading_match=True,
                )
                self.event_logger.record(
                    "warning",
                    "team_anchor_recovered_by_order",
                    f"Se recupera '{mapped_chronicle.team_config.web_block_name}' por posición de bloque porque no se encontró su encabezado.",
                    section=mapped_chronicle.team_config.section,
                    team=mapped_chronicle.team_config.web_block_name,
                    expected_order=mapped_chronicle.team_config.expected_order,
                )
                return self._update_block(anchor=anchor, side_panel=side_panel, mapped_chronicle=mapped_chronicle)
            if not chronicle.is_complete:
                raise SkipBlockUpdate(
                    f"Se omite '{mapped_chronicle.team_config.web_block_name}' porque no existe un bloque ancla inequívoco y la crónica está incompleta."
                ) from exc
            raise
        return self._update_block(anchor=anchor, side_panel=side_panel, mapped_chronicle=mapped_chronicle)

    def _can_recover_update_by_order(self, preview: PreviewContext, mapped_chronicle: MappedChronicle) -> bool:
        expected_order = int(mapped_chronicle.team_config.expected_order or 0)
        if expected_order <= 0:
            return False
        if mapped_chronicle.parsed_chronicle is None or not mapped_chronicle.parsed_chronicle.is_complete:
            return False
        try:
            return count_team_anchors(preview) >= expected_order
        except Exception:
            return False

    def _recover_missing_ordered_block(self, preview: PreviewContext, mapped_chronicle: MappedChronicle) -> bool:
        expected_order = int(mapped_chronicle.team_config.expected_order or 0)
        if expected_order <= 1:
            return False
        current_count = count_team_anchors(preview)
        if current_count != expected_order - 1:
            return False
        if not duplicate_ordered_team_block(preview, current_count):
            return False
        self.event_logger.record(
            "info",
            "team_block_duplicated",
            f"Se duplicó el bloque {current_count} para crear el hueco de '{mapped_chronicle.team_config.web_block_name}'.",
            section=mapped_chronicle.team_config.section,
            team=mapped_chronicle.team_config.web_block_name,
            source_order=current_count,
            target_order=expected_order,
        )
        timeouts = getattr(self.settings.runtime, "timeouts", {}) or {}
        preview.page.wait_for_timeout(timeouts.get("ui_ms", 10000) // 2)
        return True

    def _update_block(self, *, anchor: TeamSectionAnchor, side_panel: SidePanelContext, mapped_chronicle: MappedChronicle) -> int:
        chronicle = mapped_chronicle.parsed_chronicle
        assert chronicle is not None

        header_adapter = HeaderWidgetAdapter(side_panel)
        text_adapter = TextEditorWidgetAdapter(side_panel)
        team_photo_background_adapter = BackgroundContainerAdapter(side_panel)
        team_photo_image_adapter = ImageWidgetAdapter(side_panel)
        changes = 0

        changes += self._safe_header_update(anchor, header_adapter, "team_name", mapped_chronicle.team_config.web_block_name)
        changes += self._safe_header_update(
            anchor,
            header_adapter,
            "competition",
            self._competition_value(mapped_chronicle, chronicle),
        )
        changes += self._safe_header_update(
            anchor,
            header_adapter,
            "group",
            self._group_value(mapped_chronicle, chronicle),
        )
        changes += self._safe_header_update(anchor, header_adapter, "home_team", format_team_display(chronicle.home_team))
        if chronicle.home_score is not None and chronicle.away_score is not None:
            changes += self._safe_header_update(anchor, header_adapter, "score", format_score(chronicle.home_score, chronicle.away_score))
        changes += self._safe_header_update(anchor, header_adapter, "away_team", format_team_display(chronicle.away_team))
        changes += self._safe_header_update(anchor, header_adapter, "table_home_name", format_team_display(chronicle.home_team))
        changes += self._safe_header_update(anchor, header_adapter, "table_away_name", format_team_display(chronicle.away_team))

        period_count = self._period_slot_count(mapped_chronicle.team_config.section, anchor, chronicle)
        for item in self._build_period_updates(period_count, chronicle):
            index = item["index"]
            changes += self._safe_header_update(anchor, header_adapter, f"period_label_{index}", item["label"])
            changes += self._safe_header_update(anchor, header_adapter, f"home_period_{index}", item["home_value"], weight=item["home_weight"])
            changes += self._safe_header_update(anchor, header_adapter, f"away_period_{index}", item["away_value"], weight=item["away_weight"])

        changes += self._safe_text_update(
            anchor,
            text_adapter,
            "chronicle_text",
            paragraphs=self._chronicle_paragraphs(mapped_chronicle),
            players_text=None,
        )
        changes += self._safe_team_photo_update(
            anchor,
            team_photo_background_adapter,
            team_photo_image_adapter,
            mapped_chronicle,
        )
        return changes

    def _clear_deleted_block(
        self,
        preview: PreviewContext,
        side_panel: SidePanelContext,
        deletion: DeletionPlan,
    ) -> int:
        team_config = deletion.team_config
        anchor_candidates = [
            team_config.web_block_name,
            team_config.web_block_name.upper(),
            *team_config.heading_aliases,
        ]
        section_boundary_texts = self._section_boundary_texts(team_config.section)
        try:
            anchor = TeamSectionAnchor.locate(
                preview,
                anchor_candidates,
                self.settings.selectors,
                boundary_texts=section_boundary_texts,
                expected_order=team_config.expected_order,
                strict_expected_order=False,
                allow_order_without_heading_match=True,
            )
        except LookupError as exc:
            raise SkipBlockUpdate(
                f"Se omite la limpieza de '{team_config.web_block_name}' porque no existe un bloque ancla inequívoco."
            ) from exc

        if deletion.delete_mode == "skip_and_warn":
            raise SkipBlockUpdate(
                f"Se omite '{team_config.web_block_name}' porque la estrategia configurada es skip_and_warn."
            )
        if deletion.delete_mode == "remove_widget_or_container":
            try:
                side_panel.dismiss_media_modal()
            except Exception:
                pass
            if anchor.remove_entire_block():
                self.event_logger.record(
                    "info",
                    "team_block_removed",
                    f"Se eliminó el bloque completo de '{team_config.web_block_name}'.",
                    section=team_config.section,
                    team=team_config.web_block_name,
                )
                return 1

        header_adapter = HeaderWidgetAdapter(side_panel)
        text_adapter = TextEditorWidgetAdapter(side_panel)
        changes = 0
        for field_name in (
            "team_name",
            "competition",
            "group",
            "home_team",
            "score",
            "away_team",
            "table_home_name",
            "table_away_name",
        ):
            changes += self._safe_header_update(anchor, header_adapter, field_name, "")

        period_count = self._period_slot_count(team_config.section, anchor, type("EmptyChronicle", (), {"partials": []})())
        for index in range(1, period_count + 1):
            changes += self._safe_header_update(anchor, header_adapter, f"period_label_{index}", "")
            changes += self._safe_header_update(anchor, header_adapter, f"home_period_{index}", "")
            changes += self._safe_header_update(anchor, header_adapter, f"away_period_{index}", "")

        changes += self._safe_text_update(anchor, text_adapter, "players_text", paragraphs=[], players_text=None)
        changes += self._safe_text_update(anchor, text_adapter, "chronicle_text", paragraphs=[], players_text=None)
        return changes

    def _safe_header_update(
        self,
        anchor: TeamSectionAnchor,
        adapter: HeaderWidgetAdapter,
        field_name: str,
        value: str,
        *,
        weight: str | None = None,
    ) -> int:
        if value is None:
            return 0
        if not anchor.has_field(field_name):
            return 0
        try:
            adapter.update_field(anchor, field_name, value, weight=weight)
            return 1
        except Exception as exc:
            self.event_logger.record(
                "warning",
                "block_not_found",
                f"No se pudo actualizar el campo '{field_name}': {exc}",
                field=field_name,
                team=anchor.heading_text,
            )
            return 0

    def _period_slot_count(self, section: str, anchor: TeamSectionAnchor, chronicle) -> int:
        configured = self.settings.runtime.section_period_counts.get(section)
        if configured:
            return configured
        detected = 0
        for index in range(1, 7):
            try:
                anchor.field_locator(f"period_label_{index}")
            except Exception:
                break
            detected += 1
        if detected:
            return detected
        if chronicle.partials:
            return len(chronicle.partials)
        return 4

    def _build_period_updates(self, period_count: int, chronicle) -> list[dict[str, str | int]]:
        labels = build_period_labels(
            period_count,
            self.settings.runtime.period_label_templates,
            self.settings.runtime.overtime_label,
        )
        updates: list[dict[str, str | int]] = []
        for index in range(1, period_count + 1):
            partial = chronicle.partials[index - 1] if index <= len(chronicle.partials) else None
            label = labels[index - 1]
            home_value, away_value, winner = self._period_display_values(partial)
            updates.append(
                {
                    "index": index,
                    "label": label,
                    "home_value": home_value,
                    "away_value": away_value,
                    "home_weight": "bold" if winner == "home" else "normal",
                    "away_weight": "bold" if winner == "away" else "normal",
                }
            )
        return updates

    def _period_display_values(self, partial) -> tuple[str, str, str | None]:
        if partial is None:
            return "-", "-", None
        if partial.home_score is None or partial.away_score is None:
            return "-", "-", None
        if partial.home_score > partial.away_score:
            winner = "home"
        elif partial.away_score > partial.home_score:
            winner = "away"
        else:
            winner = None
        return str(partial.home_score), str(partial.away_score), winner

    def _chronicle_paragraphs(self, mapped_chronicle: MappedChronicle) -> list[str]:
        chronicles = self._mapped_chronicles(mapped_chronicle)
        if len(chronicles) <= 1:
            chronicle = chronicles[0] if chronicles else None
            if chronicle is None:
                return [self.settings.runtime.empty_chronicle_placeholder]
            paragraphs = [paragraph for paragraph in chronicle.body_paragraphs if paragraph and paragraph.strip()]
            if paragraphs:
                return paragraphs
            return [self.settings.runtime.empty_chronicle_placeholder]

        combined: list[str] = []
        for index, chronicle in enumerate(chronicles):
            if index > 0:
                combined.append("")
            result_line = self._chronicle_result_line(chronicle)
            if result_line:
                combined.append(result_line)
            partials_line = self._chronicle_partials_line(chronicle)
            if partials_line:
                combined.append(partials_line)
            body_paragraphs = [paragraph for paragraph in chronicle.body_paragraphs if paragraph and paragraph.strip()]
            if body_paragraphs:
                combined.extend(body_paragraphs)
            else:
                combined.append(self.settings.runtime.empty_chronicle_placeholder)
        return combined

    def _mapped_chronicles(self, mapped_chronicle: MappedChronicle) -> list:
        chronicles = getattr(mapped_chronicle, "all_chronicles", None)
        if chronicles is not None:
            return list(chronicles)
        chronicles = getattr(mapped_chronicle, "chronicles", None)
        if chronicles:
            return list(chronicles)
        chronicle = getattr(mapped_chronicle, "parsed_chronicle", None)
        if chronicle is not None:
            return [chronicle]
        return []

    def _chronicle_result_line(self, chronicle) -> str | None:
        if chronicle.result_raw:
            return chronicle.result_raw
        if chronicle.home_team and chronicle.away_team and chronicle.home_score is not None and chronicle.away_score is not None:
            return f"{chronicle.home_team} {format_score(chronicle.home_score, chronicle.away_score)} {chronicle.away_team}"
        return None

    def _chronicle_partials_line(self, chronicle) -> str | None:
        if not chronicle.partials:
            return None
        values = []
        for partial in chronicle.partials:
            if partial.home_score is None or partial.away_score is None:
                values.append("- -")
                continue
            values.append(f"{partial.home_score}-{partial.away_score}")
        return f"PARCIALES: {', '.join(values)}"

    def _competition_value(self, mapped_chronicle: MappedChronicle, chronicle) -> str:
        configured = mapped_chronicle.team_config.display_competition
        if configured:
            return configured
        return format_competition_display(chronicle.competition_title, heading=chronicle.raw_heading)

    def _group_value(self, mapped_chronicle: MappedChronicle, chronicle) -> str:
        configured = mapped_chronicle.team_config.display_group
        if configured:
            return configured
        if mapped_chronicle.team_config.display_competition and not chronicle.group_title:
            return chronicle.competition_title
        return chronicle.group_title

    def _safe_text_update(
        self,
        anchor: TeamSectionAnchor,
        adapter: TextEditorWidgetAdapter,
        field_name: str,
        *,
        paragraphs: list[str],
        players_text: str | None,
    ) -> int:
        if not anchor.has_field(field_name):
            return 0
        try:
            adapter.update_field(
                anchor,
                field_name,
                paragraphs=paragraphs,
                players_text=players_text,
                preferred_mode=self.settings.runtime.rich_text_mode,
            )
            return 1
        except Exception as exc:
            self.event_logger.record(
                "warning",
                "block_not_found",
                f"No se pudo actualizar el campo '{field_name}': {exc}",
                field=field_name,
                team=anchor.heading_text,
            )
            return 0

    def _safe_team_photo_update(
        self,
        anchor: TeamSectionAnchor,
        background_adapter: BackgroundContainerAdapter,
        image_adapter: ImageWidgetAdapter,
        mapped_chronicle: MappedChronicle,
    ) -> int:
        if not (anchor.team_photo_container_locators() or anchor.team_image_locators()):
            return 0
        try:
            resolved = self.photo_repository.resolve_for_team(mapped_chronicle.team_config)
            reuse_existing_name = resolved.source_name if resolved.fallback else None
            team_photo_uses_source = getattr(anchor, "team_photo_uses_source", None)
            already_set = False
            if resolved.fallback and callable(team_photo_uses_source):
                try:
                    already_set = bool(team_photo_uses_source(resolved.source_name))
                except Exception:
                    already_set = False
            if resolved.fallback and already_set:
                self.event_logger.record(
                    "info",
                    "team_photo_already_set",
                    f"La foto fallback ya estaba puesta para '{mapped_chronicle.team_config.web_block_name}'.",
                    team=mapped_chronicle.team_config.web_block_name,
                    image=str(resolved.path),
                    fallback=True,
                    reused_library_asset=True,
                    reuse_requested=True,
                    source_name=resolved.source_name,
                )
                return 0
            update_error: Exception | None = None
            reused_library_asset = False

            if anchor.team_photo_container_locators():
                try:
                    reused_library_asset = bool(
                        background_adapter.update_image(
                            anchor,
                            resolved.path,
                            reuse_existing_name=reuse_existing_name,
                            require_existing_media=self._fallback_must_reuse_existing_media(resolved.source_name),
                        )
                    )
                    update_error = None
                except Exception as exc:
                    update_error = exc

            if update_error is not None and anchor.team_image_locators():
                try:
                    reused_library_asset = bool(
                        image_adapter.update_image(
                            anchor,
                            resolved.path,
                            reuse_existing_name=reuse_existing_name,
                            require_existing_media=self._fallback_must_reuse_existing_media(resolved.source_name),
                        )
                    )
                    update_error = None
                except Exception as exc:
                    update_error = exc

            if update_error is not None:
                raise update_error
            if resolved.fallback and not reused_library_asset:
                self._uploaded_fallback_media_names.add(resolved.source_name)

            self.event_logger.record(
                "info",
                "team_photo_updated",
                f"Foto de equipo actualizada para '{mapped_chronicle.team_config.web_block_name}'.",
                team=mapped_chronicle.team_config.web_block_name,
                image=str(resolved.path),
                fallback=resolved.fallback,
                reused_library_asset=bool(reused_library_asset),
                reuse_requested=bool(reuse_existing_name),
                source_name=resolved.source_name,
            )
            return 1
        except Exception as exc:
            self.event_logger.record(
                "warning",
                "team_photo_skipped",
                f"No se pudo actualizar la foto de equipo de '{mapped_chronicle.team_config.web_block_name}': {exc}",
                team=mapped_chronicle.team_config.web_block_name,
            )
            return 0

    def _fallback_must_reuse_existing_media(self, source_name: str) -> bool:
        return source_name in self._uploaded_fallback_media_names

    def _section_boundary_texts(self, section: str) -> list[str]:
        texts: list[str] = []
        for team in self.settings.teams:
            if team.section != section:
                continue
            texts.append(team.web_block_name)
            texts.extend(team.heading_aliases)
        return texts


def _launch_browser(browser_factory: Any, *, headless: bool) -> Any:
    try:
        return browser_factory.launch(headless=headless)
    except Exception as exc:
        raise RuntimeError(
            "No se pudo abrir el navegador de Playwright. Ejecuta primero 'python -m playwright install chromium'."
        ) from exc
