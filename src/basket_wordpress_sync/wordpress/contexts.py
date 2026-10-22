from __future__ import annotations

from dataclasses import dataclass, field, replace
from pathlib import Path
import re
import time
from typing import Any
from urllib.parse import parse_qs, urlsplit

from ..normalizer.text import normalize_lookup_key


SCORE_TEXT_RE = re.compile(r"\d+\s*[-‐‑‒–—−]\s*\d+")
PERIOD_LABEL_RE = re.compile(r"^(?:\d+[CP]|PR\d*|TE\d*)$", re.IGNORECASE)
NUMERIC_TEXT_RE = re.compile(r"^\d+$")


class PreviewContext:
    def __init__(self, page: Any, selectors: dict[str, Any]) -> None:
        self.page = page
        self.selectors = selectors

    def root(self) -> Any:
        selector = str((self.selectors.get("editor") or {}).get("preview_frame") or "").strip()
        if selector:
            locator = self.page.locator(selector).first
            if locator.count() > 0:
                try:
                    handle = locator.element_handle(timeout=2000)
                except Exception:
                    handle = None
                if handle is not None:
                    try:
                        frame = handle.content_frame()
                    except Exception:
                        frame = None
                    if frame is not None:
                        return frame

        for frame in getattr(self.page, "frames", []):
            frame_url = getattr(frame, "url", "") or ""
            if frame_url and not frame_url.startswith("about:") and frame is not self.page.main_frame:
                return frame
        return self.page

    def locator(self, selector: str) -> Any:
        return self.root().locator(_normalize_selector(selector))

    def click_visible_text_in_preview(self, text: str, scope: Any | None = None) -> None:
        root = scope or self.root()
        locator = self._locate_text(root, text)
        if locator.count() == 0:
            raise LookupError(f"No se encontr? el texto visible '{text}' en la preview.")
        locator.scroll_into_view_if_needed()
        locator.click()

    def screenshot(self, path: Path) -> None:
        self.page.screenshot(path=str(path), full_page=True)

    def _locate_text(self, root: Any, text: str) -> Any:
        locator = root.get_by_text(text, exact=True).first
        if locator.count() > 0:
            return locator
        locator = root.get_by_text(re.compile(rf"^{re.escape(text)}$", re.IGNORECASE)).first
        if locator.count() > 0:
            return locator
        upper = text.upper()
        if upper != text:
            locator = root.get_by_text(upper, exact=True).first
            if locator.count() > 0:
                return locator
        return root.get_by_text(text).first


@dataclass(slots=True)
class TeamSectionAnchor:
    preview: PreviewContext
    heading_text: str
    heading_locator: Any
    root_locator: Any
    field_selectors: dict[str, str]
    boundary_keys: list[str] = field(default_factory=list)
    widget_catalog: list[dict[str, Any]] = field(default_factory=list)
    current_widget: dict[str, Any] | None = None
    structured_fields_cache: dict[str, dict[str, Any]] = field(default_factory=dict)
    strict_fields: bool = False

    def match_anchors(self) -> list["TeamSectionAnchor"]:
        """Freeze widget IDs for each scoreboard inside this team only."""
        widgets = self._block_widgets()
        rows = self._cluster_rows([w for w in widgets if w["type"] == "heading"], threshold=35.0)
        scores = [row for row in rows if len(row) >= 3 and any(
            SCORE_TEXT_RE.fullmatch(str(w.get("text") or "").strip()) for w in row
        )]
        if not scores:
            raise LookupError(f"No hay marcadores inequivocos en '{self.heading_text}'.")
        metadata = [w for w in widgets if w["y"] < scores[0][0]["y"] - 5]
        matches = []
        for index, row in enumerate(scores):
            top = min(w["y"] for w in row) - 5
            bottom = min(w["y"] for w in scores[index + 1]) - 5 if index + 1 < len(scores) else float("inf")
            region = [w for w in widgets if top <= w["y"] < bottom]
            editors = [w for w in region if w["type"] == "text_editor"]
            if len(editors) != 2:
                raise LookupError(f"Partido {index + 1}: se esperaban roster y cronica, hay {len(editors)} editores.")
            # Do not let a following sponsor/photo enter the removable match region.
            end = max(w["y"] for w in editors)
            region = [w for w in region if w["y"] <= end or w["type"] == "match_end"]
            view = replace(self, widget_catalog=metadata + region, structured_fields_cache={}, strict_fields=True)
            fields = view._structured_field_widgets()
            required = {"home_team", "score", "away_team", "table_home_name", "table_away_name", "chronicle_text", "players_text"}
            if not required <= fields.keys():
                raise LookupError(f"Partido {index + 1}: faltan campos {sorted(required - fields.keys())}.")
            matches.append(view)
        ids = [str(v["dataId"]) for m in matches for name, v in m._structured_field_widgets().items()
               if name not in {"team_name", "competition", "group"}]
        if len(ids) != len(set(ids)):
            raise LookupError("Dos partidos comparten widgets; no se edita el equipo.")
        return matches

    def prepare_match_layout(self, period_counts: list[int]) -> list["TeamSectionAnchor"]:
        from .match_layout import reconcile_match_layout

        matches = self.match_anchors()
        changed = reconcile_match_layout(self, matches, period_counts)
        if changed:
            self.preview.page.wait_for_timeout(750)
            heading_id = str(self.current_widget["dataId"])
            self.widget_catalog.clear()
            self.structured_fields_cache.clear()
            self._prime_widget_catalog()
            self._set_current_widget_by_data_id(heading_id)
            matches = self.match_anchors()
        if len(matches) != len(period_counts):
            scores = [(w.get("dataId"), w.get("text"), round(w["x"]), round(w["y"]), round(w.get("width", 0)))
                      for w in self.widget_catalog if w["type"] == "heading" and SCORE_TEXT_RE.fullmatch(str(w.get("text") or "").strip())]
            boundaries = [(w.get("dataId"), w.get("text")) for w in self._team_anchor_widgets()]
            raise LookupError(f"El numero de bloques de partido no coincide con el Word: {len(matches)}/{len(period_counts)}; marcadores={scores}; limites={boundaries}.")
        for match, count in zip(matches, period_counts):
            for i in range(1, count + 1):
                for prefix in ("period_label", "home_period", "away_period"):
                    match.field_locator(f"{prefix}_{i}")
        return matches

    @classmethod
    def locate(
        cls,
        preview: PreviewContext,
        heading_texts: str | list[str],
        selectors: dict[str, Any],
        *,
        boundary_texts: list[str] | None = None,
        expected_order: int | None = None,
        strict_expected_order: bool = False,
        allow_order_without_heading_match: bool = False,
    ) -> "TeamSectionAnchor":
        root = preview.root()
        preview_section = selectors.get("preview") or {}
        candidate_headings = _candidate_texts(heading_texts)
        boundary_keys = [normalize_lookup_key(text) for text in _candidate_texts(boundary_texts or candidate_headings)]

        def build_anchor(data_id: str, heading_text: str) -> "TeamSectionAnchor":
            heading_locator = preview.locator(f'[data-id="{data_id}"]').first
            container = heading_locator
            container_data_id = ""
            try:
                container_data_id = str(
                    heading_locator.evaluate(
                        r"""
                        (node) => (
                          node.closest(
                            ".elementor-top-section, .e-con, .elementor-section, .elementor-widget-wrap, [data-element_type='container']"
                          )?.getAttribute("data-id") || ""
                        )
                        """
                    )
                ).strip()
            except Exception:
                container_data_id = ""
            if container_data_id:
                candidate = preview.locator(f'[data-id="{container_data_id}"]').first
                if candidate.count() > 0:
                    container = candidate
            for ancestor in preview_section.get("team_block_ancestors") or []:
                candidate = heading_locator.locator(_normalize_selector(str(ancestor))).first
                if candidate.count() > 0:
                    container = candidate
                    break
            anchor = cls(
                preview=preview,
                heading_text=heading_text,
                heading_locator=heading_locator,
                root_locator=container,
                field_selectors={str(key): str(value) for key, value in (preview_section.get("field_selectors") or {}).items()},
                boundary_keys=boundary_keys,
            )
            anchor._prime_widget_catalog()
            anchor._set_current_widget_by_data_id(data_id)
            return anchor

        if expected_order is not None:
            ordered_heading = _find_ordered_team_anchor(preview, expected_order)
            if ordered_heading is not None:
                ordered_text, data_id = ordered_heading
                if allow_order_without_heading_match or _matches_candidate_heading(ordered_text, candidate_headings):
                    anchor = build_anchor(data_id, candidate_headings[0] or ordered_text)
                    if anchor.current_widget is not None:
                        return anchor
            if strict_expected_order:
                raise LookupError(
                    f"No se encontró el bloque ancla en la posición esperada {expected_order} para '{candidate_headings[0]}'."
                )

        heading_widget = _find_heading_widget(preview, candidate_headings)
        if heading_widget is not None:
            heading_text, data_id = heading_widget
            anchor = build_anchor(data_id, heading_text)
            if anchor.current_widget is not None:
                return anchor

        fallback_anchor: TeamSectionAnchor | None = None
        for heading_text in candidate_headings:
            heading_locator = preview._locate_text(root, heading_text)
            if heading_locator.count() == 0:
                continue

            container = heading_locator
            for ancestor in preview_section.get("team_block_ancestors") or []:
                candidate = heading_locator.locator(_normalize_selector(str(ancestor))).first
                if candidate.count() > 0:
                    container = candidate
                    break

            anchor = cls(
                preview=preview,
                heading_text=heading_text,
                heading_locator=heading_locator,
                root_locator=container,
                field_selectors={str(key): str(value) for key, value in (preview_section.get("field_selectors") or {}).items()},
                boundary_keys=boundary_keys,
            )
            anchor._prime_widget_catalog()
            if anchor.current_widget is not None:
                return anchor
            if fallback_anchor is None:
                fallback_anchor = anchor

        if fallback_anchor is not None:
            return fallback_anchor
        raise LookupError(f"No se encontr? el bloque ancla '{candidate_headings[0]}'.")

    def field_locator(self, field_name: str) -> Any:
        structured = self._structured_field_widgets() if self.current_widget is not None else {}
        locator = self._fallback_field_locator(field_name)
        if locator is not None and locator.count() > 0:
            return locator
        if structured and _uses_structured_lookup(field_name):
            raise LookupError(f"No se encontr? el campo '{field_name}' dentro del bloque '{self.heading_text}'.")

        selector = self.field_selectors.get(field_name)
        if selector:
            locator = self.root_locator.locator(_normalize_selector(selector)).first
            if locator.count() > 0:
                return locator
        raise LookupError(f"No se encontr? el campo '{field_name}' dentro del bloque '{self.heading_text}'.")

    def click_field(self, field_name: str, fallback_text: str | None = None) -> None:
        locator = self.field_locator(field_name)
        if locator.count() > 0:
            self._click_locator(locator)
            return
        if fallback_text:
            self.preview.click_visible_text_in_preview(fallback_text, scope=self.root_locator)
            return
        raise LookupError(f"No se puede localizar el campo '{field_name}'.")

    def available_field_names(self) -> set[str]:
        structured = self._structured_field_widgets() if self.current_widget is not None else {}
        if structured:
            return set(structured.keys())
        available: set[str] = set()
        for field_name, selector in self.field_selectors.items():
            if not selector:
                continue
            try:
                locator = self.root_locator.locator(_normalize_selector(selector)).first
            except Exception:
                continue
            if locator.count() > 0:
                available.add(field_name)
        return available

    def has_field(self, field_name: str) -> bool:
        return field_name in self.available_field_names()

    def remove_entire_block(self) -> bool:
        if getattr(self, "strict_fields", False):
            anchor_ids = [str(item.get("dataId") or "") for item in self._team_anchor_widgets()]
            heading_id = self._data_id_for_locator(self.heading_locator)
            removed = self.preview.page.evaluate(
                """({headingId, anchorIds}) => {
                  const operation = async () => {
                    const root = window.elementor?.documents?.getCurrent?.()?.container;
                    if (!root || !window.$e) throw new Error('Elementor no esta preparado');
                    const contains = (node, id) => node.id === id || (node.children || []).some(child => contains(child, id));
                    const children = [...root.children];
                    const start = children.findIndex(node => contains(node, headingId));
                    if (start < 0) throw new Error('No se encuentra el inicio estructural del equipo');
                    const anchorsInStart = anchorIds.filter(id => contains(children[start], id));
                    if (anchorsInStart.length !== 1) throw new Error('Varios equipos comparten un contenedor; no se elimina');
                    let end = children.findIndex((node, index) => index > start && anchorIds.some(id => contains(node, id)));
                    if (end < 0) end = children.length;
                    const targets = children.slice(start, end);
                    for (const container of targets.reverse()) {
                      await window.$e.run('document/elements/delete', {container});
                    }
                    const remains = root.children.some(node => targets.some(target => target.id === node.id));
                    if (remains) throw new Error('Elementor conserva contenedores del equipo');
                    return targets.length > 0;
                  };
                  return Promise.race([operation(), new Promise((_, reject) => setTimeout(() => reject(new Error('No se confirma la eliminacion en 15 segundos')), 15000))]);
                }""",
                {"headingId": heading_id, "anchorIds": anchor_ids},
            )
            if removed:
                self.preview.page.wait_for_timeout(500)
            return bool(removed)
        data_ids = self._block_removal_data_ids()
        for locator in (self.root_locator, self.heading_locator):
            data_id = self._data_id_for_locator(locator)
            if data_id and data_id not in data_ids:
                data_ids.append(data_id)
        current_data_id = str((self.current_widget or {}).get("dataId") or "").strip()
        if current_data_id and current_data_id not in data_ids:
            data_ids.append(current_data_id)
        if not data_ids:
            return False

        removed = self.preview.page.evaluate(
            r"""
            (targetIds) => {
              const rootContainer = window.elementor?.documents?.getCurrent?.()?.container;
              if (!rootContainer || !window.$e || !Array.isArray(targetIds) || targetIds.length === 0) {
                return false;
              }
              const uniqueIds = Array.from(new Set(targetIds.filter(Boolean)));
              const walk = (container, id) => {
                if (!container) {
                  return null;
                }
                if (container.id === id) {
                  return container;
                }
                for (const child of container.children || []) {
                  const found = walk(child, id);
                  if (found) {
                    return found;
                  }
                }
                return null;
              };
              const promote = (container) => {
                let current = container;
                while (current && current.type === 'widget' && current.parent) {
                  current = current.parent;
                }
                return current;
              };
              const containersById = new Map();
              for (const id of uniqueIds) {
                const container = promote(walk(rootContainer, id));
                if (container && container.id) {
                  containersById.set(container.id, container);
                }
              }
              const selectedIds = new Set(containersById.keys());
              const topLevelContainers = Array.from(containersById.values()).filter((container) => {
                let parent = container.parent;
                while (parent) {
                  if (selectedIds.has(parent.id)) {
                    return false;
                  }
                  parent = parent.parent;
                }
                return true;
              });
              if (!topLevelContainers.length) {
                return false;
              }
              const domY = (container) => {
                const node = document.querySelector(`[data-id="${container.id}"]`);
                if (!node) {
                  return 0;
                }
                return node.getBoundingClientRect().top + window.scrollY;
              };
              topLevelContainers.sort((left, right) => domY(right) - domY(left));
              let removedCount = 0;
              for (const container of topLevelContainers) {
                try {
                  window.$e.run('document/elements/delete', { container });
                  removedCount += 1;
                  continue;
                } catch (error) {}
                try {
                  if (typeof container.remove === 'function') {
                    container.remove();
                    removedCount += 1;
                  }
                } catch (error) {}
              }
              return removedCount > 0;
            }
            """,
            data_ids,
        )
        if removed:
            self.preview.page.wait_for_timeout(500)
            return True
        return False

    def _block_removal_data_ids(self) -> list[str]:
        ids: list[str] = []
        top, bottom = self._block_bounds()
        block_height = bottom - top if bottom != float("inf") else None
        try:
            visual_elements = self._block_visual_elements()
        except Exception:
            visual_elements = []
        for item in visual_elements:
            data_id = str(item.get("dataId") or "").strip()
            if not data_id or data_id in ids:
                continue
            x = float(item.get("x") or 0.0)
            width = float(item.get("width") or 0.0)
            height = float(item.get("height") or 0.0)
            if x > 980.0:
                continue
            if width < 50.0 or height < 25.0:
                continue
            if block_height is not None and block_height > 0 and height > block_height + 120.0:
                continue
            ids.append(data_id)
        return ids

    def _data_id_for_locator(self, locator: Any) -> str:
        try:
            handle = locator.element_handle()
        except Exception:
            handle = None
        if handle is None:
            return ""
        try:
            return str(handle.evaluate("(node) => node.closest('[data-id]')?.getAttribute('data-id') || ''")).strip()
        except Exception:
            return ""

    def _click_locator(self, locator: Any) -> None:
        candidates: list[Any] = []
        for candidate in (
            locator,
            locator.locator("xpath=ancestor::*[@data-id][1]").first,
            locator.locator("xpath=ancestor::*[contains(@class,'elementor-widget')][1]").first,
        ):
            try:
                if candidate.count() > 0:
                    candidates.append(candidate)
            except Exception:
                continue

        seen: set[int] = set()
        unique_candidates: list[Any] = []
        for candidate in candidates:
            identifier = id(candidate)
            if identifier in seen:
                continue
            unique_candidates.append(candidate)
            seen.add(identifier)

        last_error: Exception | None = None
        for candidate in unique_candidates:
            try:
                handle = candidate.element_handle()
            except Exception:
                handle = None
            if handle is not None:
                try:
                    data_id = handle.evaluate("(node) => node.closest('[data-id]')?.getAttribute('data-id') || ''")
                    if data_id:
                        selected = self.preview.page.evaluate(
                            r"""
                            (targetId) => {
                              const rootContainer = window.elementor?.documents?.getCurrent?.()?.container;
                              if (!rootContainer || !window.$e) {
                                return false;
                              }
                              const walk = (container) => {
                                if (!container) {
                                  return null;
                                }
                                if (container.id === targetId) {
                                  return container;
                                }
                                if (!container.children || !container.children.length) {
                                  return null;
                                }
                                for (const child of container.children) {
                                  const found = walk(child);
                                  if (found) {
                                    return found;
                                  }
                                }
                                return null;
                              };
                              const container = walk(rootContainer);
                              if (!container) {
                                return false;
                              }
                              window.$e.run('document/elements/select', { container });
                              return true;
                            }
                            """
                            ,
                            data_id,
                        )
                        if selected:
                            self.preview.page.wait_for_timeout(200)
                            return
                except Exception as exc:
                    last_error = exc

            try:
                candidate.scroll_into_view_if_needed()
            except Exception:
                pass
            try:
                candidate.click(timeout=1500, force=True)
                self.preview.page.wait_for_timeout(200)
                return
            except TypeError:
                candidate.click()
                return
            except Exception as exc:
                last_error = exc

        for candidate in unique_candidates:
            try:
                handle = candidate.element_handle()
            except Exception:
                handle = None
            if handle is None:
                continue
            try:
                handle.evaluate(
                    r"""
                    (node) => {
                      const target = node.closest('[data-id]') || node.closest('.elementor-widget') || node;
                      target.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true, view: window }));
                      if (typeof target.click === 'function') {
                        target.click();
                      }
                    }
                    """
                )
                self.preview.page.wait_for_timeout(200)
                return
            except Exception as exc:
                last_error = exc

        if last_error is not None:
            raise last_error
        raise LookupError("No se pudo activar el widget solicitado en Elementor.")

    def _fallback_field_locator(self, field_name: str) -> Any | None:
        self._prime_widget_catalog()
        if self.current_widget is None:
            return None

        structured = self._structured_field_widgets()
        widget = structured.get(field_name)
        if widget is not None:
            locator = self._locator_for_widget(widget)
            if locator is not None and locator.count() > 0:
                return locator

        if field_name == "team_name":
            return self._locator_for_widget(self.current_widget)
        return None

    def _prime_widget_catalog(self) -> None:
        if self.widget_catalog:
            return
        root = self.preview.root()
        post_ids = parse_qs(urlsplit(str(getattr(self.preview.page, "url", ""))).query).get("post", []) if hasattr(self.preview, "page") else []
        post_id = post_ids[0] if post_ids and post_ids[0].isdigit() else ""
        widgets = root.evaluate(
            r"""
            (postId) => {
              const view = window;
              const documentRoot = postId ? document.querySelector(`.elementor[data-elementor-id="${postId}"]`) : document;
              if (!documentRoot) throw new Error('No se encuentra el documento Elementor actual');
              const nodes = Array.from(documentRoot.querySelectorAll('.elementor-widget'));
              return nodes.map((node, index) => {
                const rect = node.getBoundingClientRect();
                const text = (node.textContent || '').trim().replace(/\s+/g, ' ');
                const className = node.className || '';
                let type = 'other';
                if (className.includes('elementor-widget-heading')) type = 'heading';
                if (className.includes('elementor-widget-text-editor')) type = 'text_editor';
                if (className.includes('elementor-widget-thegem-animated-heading')) type = 'match_end';
                if (
                  className.includes('elementor-widget-image') ||
                  className.includes('elementor-widget-thegem-styledimage')
                ) type = 'image';
                return {
                  index,
                  dataId: node.getAttribute('data-id') || '',
                  className,
                  text,
                  y: rect.top + view.scrollY,
                  x: rect.left + view.scrollX,
                  width: rect.width,
                  height: rect.height,
                  type,
                };
              }).filter(item => ['heading', 'text_editor', 'image', 'match_end'].includes(item.type));
            }
            """,
            post_id,
        )
        self.widget_catalog = [dict(item) for item in widgets]
        heading_box = self.heading_locator.bounding_box() or {"x": 0.0, "y": 0.0}
        scored_candidates: list[tuple[int, dict[str, Any]]] = []
        for item in self.widget_catalog:
            if item["type"] != "heading":
                continue
            score = _heading_match_score(self.heading_text, str(item.get("text") or ""))
            if score > 0:
                scored_candidates.append((score, item))
        if scored_candidates:
            best_score = max(score for score, _ in scored_candidates)
            candidates = [item for score, item in scored_candidates if score == best_score]
            self.current_widget = min(candidates, key=lambda item: abs(item["y"] - heading_box.get("y", 0.0)))

    def _set_current_widget_by_data_id(self, data_id: str) -> None:
        lookup = data_id.strip()
        if not lookup:
            return
        for item in self.widget_catalog:
            if str(item.get("dataId") or "").strip() == lookup:
                self.current_widget = item
                return

    def _block_widgets(self) -> list[dict[str, Any]]:
        if self.current_widget is None:
            return self.widget_catalog
        current_y = self.current_widget["y"]
        bottom = float('inf')
        for item in self._team_anchor_widgets():
            if item.get("dataId") == self.current_widget.get("dataId"):
                continue
            if item["y"] > current_y + 50:
                bottom = item["y"] - 5
                break
        return [
            item for item in self.widget_catalog
            if item["y"] >= current_y - 5 and item["y"] < bottom
        ]

    def _team_anchor_widgets(self) -> list[dict[str, Any]]:
        self._prime_widget_catalog()
        anchors = [item for item in self.widget_catalog if _is_team_anchor_candidate(item)]
        anchors.sort(key=lambda entry: (entry["y"], entry["x"]))
        return anchors

    def _structured_field_widgets(self) -> dict[str, dict[str, Any]]:
        if self.structured_fields_cache:
            return self.structured_fields_cache
        if self.current_widget is None:
            return {}

        fields: dict[str, dict[str, Any]] = {"team_name": self.current_widget}
        block_widgets = self._block_widgets()
        heading_rows = self._cluster_rows([item for item in block_widgets if item["type"] == "heading"], threshold=35.0)
        heading_rows = [row for row in heading_rows if row]
        if not heading_rows:
            return fields

        team_row_index = self._find_row_containing_widget(heading_rows, self.current_widget)
        label_row_index = next(
            (
                index
                for index, row in enumerate(heading_rows[team_row_index + 1 :], start=team_row_index + 1)
                if self._is_period_label_row(row)
            ),
            None,
        )
        score_search_end = label_row_index if label_row_index is not None else len(heading_rows)
        score_row_index = next(
            (
                index
                for index, row in enumerate(heading_rows[team_row_index + 1 : score_search_end], start=team_row_index + 1)
                if any(SCORE_TEXT_RE.search((item.get("text") or "").strip()) for item in row)
            ),
            None,
        )
        if score_row_index is None:
            score_candidates = [
                (index, row)
                for index, row in enumerate(heading_rows[team_row_index + 1 : score_search_end], start=team_row_index + 1)
                if self._looks_like_score_row(row)
            ]
            if score_candidates:
                score_row_index = score_candidates[-1][0]
        if score_row_index is not None:
            meta_candidates = [
                row
                for row in heading_rows[team_row_index + 1 : score_row_index]
                if not any(SCORE_TEXT_RE.search((item.get("text") or "").strip()) for item in row)
                and not self._is_period_label_row(row)
            ]
            if meta_candidates:
                meta_row = meta_candidates[0]
                fields["competition"] = meta_row[0]
                if len(meta_row) > 1:
                    fields["group"] = meta_row[1]

            score_row = heading_rows[score_row_index]
            score_item = next(
                (item for item in score_row if SCORE_TEXT_RE.search((item.get("text") or "").strip())),
                None,
            )
            if score_item is not None:
                fields["score"] = score_item
                left_items = [item for item in score_row if item["x"] < score_item["x"]]
                right_items = [item for item in score_row if item["x"] > score_item["x"]]
                if left_items:
                    fields["home_team"] = left_items[0]
                if right_items:
                    fields["away_team"] = right_items[-1]
            elif len(score_row) >= 3:
                midpoint = len(score_row) // 2
                fields["home_team"] = score_row[0]
                fields["score"] = score_row[midpoint]
                fields["away_team"] = score_row[-1]

            if label_row_index is None:
                label_row_index = next(
                    (
                        index
                        for index, row in enumerate(heading_rows[score_row_index + 1 :], start=score_row_index + 1)
                        if self._is_period_label_row(row)
                    ),
                    None,
                )
            if label_row_index is not None:
                label_row = heading_rows[label_row_index]
                label_row = [
                    item
                    for item in label_row
                    if PERIOD_LABEL_RE.match((item.get("text") or "").strip())
                ] or list(label_row)
                label_row.sort(key=lambda item: item["x"])
                for index, item in enumerate(label_row, start=1):
                    fields[f"period_label_{index}"] = item

                minimum_columns = len(label_row) + 1
                data_rows: list[list[dict[str, Any]]] = []
                for row in heading_rows[label_row_index + 1 :]:
                    if len(row) >= minimum_columns:
                        data_rows.append(row)
                    if len(data_rows) == 2:
                        break
                if data_rows:
                    home_row = data_rows[0]
                    fields["table_home_name"] = home_row[0]
                    for index, item in enumerate(home_row[1 : len(label_row) + 1], start=1):
                        fields[f"home_period_{index}"] = item
                if len(data_rows) > 1:
                    away_row = data_rows[1]
                    fields["table_away_name"] = away_row[0]
                    for index, item in enumerate(away_row[1 : len(label_row) + 1], start=1):
                        fields[f"away_period_{index}"] = item

        text_editors = sorted(
            [item for item in block_widgets if item["type"] == "text_editor"],
            key=lambda item: (item["y"], item["x"]),
        )[:2]
        if text_editors:
            fields["players_text"] = text_editors[0]
            fields["chronicle_text"] = text_editors[-1]

        self.structured_fields_cache = fields
        return fields

    def _is_period_label_row(self, row: list[dict[str, Any]]) -> bool:
        if len(row) < 4:
            return False
        label_count = sum(
            1
            for item in row
            if PERIOD_LABEL_RE.match((item.get("text") or "").strip())
        )
        if label_count >= 4:
            return True
        if float(row[0].get("x") or 0.0) < 420.0:
            return False
        compact_count = sum(
            1
            for item in row
            if len((item.get("text") or "").strip()) <= 4 and float(item.get("width") or 0.0) <= 70.0
        )
        return compact_count >= max(4, len(row) - 1)

    def _looks_like_score_row(self, row: list[dict[str, Any]]) -> bool:
        if len(row) < 3:
            return False
        if self._is_period_label_row(row):
            return False
        ordered = sorted(row, key=lambda item: item["x"])
        if float(ordered[0].get("x") or 0.0) > 120.0:
            return False
        return float(ordered[-1].get("x") or 0.0) >= 350.0

    def _find_row_containing_widget(self, rows: list[list[dict[str, Any]]], widget: dict[str, Any]) -> int:
        target_id = widget.get("dataId")
        for index, row in enumerate(rows):
            if any(item.get("dataId") == target_id for item in row):
                return index
        return 0

    def _row_for_widget(self, widgets: list[dict[str, Any]], target: dict[str, Any]) -> list[dict[str, Any]]:
        for row in self._cluster_rows(widgets):
            if any(item.get("dataId") == target.get("dataId") for item in row):
                return row
        return []

    def _cluster_rows(self, widgets: list[dict[str, Any]], *, threshold: float = 35.0) -> list[list[dict[str, Any]]]:
        rows: list[list[dict[str, Any]]] = []
        for item in sorted(widgets, key=lambda entry: (entry["y"], entry["x"])):
            if not rows or abs(rows[-1][0]["y"] - item["y"]) > threshold:
                rows.append([item])
            else:
                rows[-1].append(item)
        for row in rows:
            row.sort(key=lambda entry: entry["x"])
        return rows

    def _locator_for_widget(self, widget: dict[str, Any] | None) -> Any | None:
        if widget is None:
            return None
        data_id = str(widget.get("dataId") or "").strip()
        if not data_id:
            return None
        locator = self.preview.locator(f'[data-id="{data_id}"]').first
        return locator if locator.count() > 0 else None

    def _block_bounds(self) -> tuple[float, float]:
        if self.current_widget is None:
            return 0.0, float("inf")
        current_y = float(self.current_widget["y"])
        bottom = float("inf")
        for item in self._team_anchor_widgets():
            if item.get("dataId") == self.current_widget.get("dataId"):
                continue
            if item["y"] > current_y + 50:
                bottom = float(item["y"]) - 5.0
                break
        return current_y - 5.0, bottom

    def _block_visual_elements(self) -> list[dict[str, Any]]:
        top, bottom = self._block_bounds()
        root = self.preview.root()
        elements = root.evaluate(
            r"""
            ([top, bottom]) => Array.from(document.querySelectorAll('.elementor-element, .e-con')).map((node) => {
              const rect = node.getBoundingClientRect();
              const y = rect.top + window.scrollY;
              const img = node.querySelector(':scope > img, img');
              return {
                dataId: node.getAttribute('data-id') || '',
                className: node.className || '',
                widgetType: node.getAttribute('data-widget_type') || '',
                text: (node.textContent || '').trim().replace(/\s+/g, ' '),
                x: rect.left + window.scrollX,
                y,
                width: rect.width,
                height: rect.height,
                imgSrc: img ? (img.getAttribute('src') || '') : '',
                backgroundImage: getComputedStyle(node).backgroundImage || '',
              };
            }).filter(item => item.dataId && item.y >= top && item.y < bottom && item.width > 50 && item.height > 50);
            """,
            [top, bottom],
        )
        post_ids = parse_qs(urlsplit(str(getattr(self.preview.page, "url", ""))).query).get("post", [])
        if post_ids and post_ids[0].isdigit():
            allowed_ids = set(root.locator(f'.elementor[data-elementor-id="{post_ids[0]}"] [data-id]').evaluate_all("nodes => nodes.map(n => n.getAttribute('data-id'))"))
            elements = [item for item in elements if item.get("dataId") in allowed_ids]
        return [dict(item) for item in elements]

    def set_team_photo_media(self, media: dict) -> str:
        dedicated = self.preview.locator(".basket-sync-team-photo")
        top, bottom = self._block_bounds()
        target_id = ""
        for index in range(dedicated.count()):
            candidate = dedicated.nth(index)
            position = candidate.evaluate("node => node.getBoundingClientRect().top + window.scrollY")
            if top <= position < bottom:
                target_id = candidate.get_attribute("data-id") or ""
                break
        chronicle = self.field_locator("chronicle_text")
        chronicle_id = chronicle.get_attribute("data-id")
        if not chronicle_id:
            chronicle_id = chronicle.evaluate("node => node.closest('[data-id]')?.getAttribute('data-id')")
        result = self.preview.page.evaluate(
            """({targetId, chronicleId, media}) => {
              const operation = async () => {
              const root = window.elementor?.documents?.getCurrent?.()?.container;
              const find = (node, id) => {
                if (!node) return null;
                if (node.id === id) return node;
                for (const child of node.children || []) { const found = find(child, id); if (found) return found; }
                return null;
              };
              if (!root || !window.$e) throw new Error('Elementor no esta preparado');
              let target = targetId ? find(root, targetId) : null;
              if (target) {
                await window.$e.run('document/elements/settings', {container: target, settings: {image: media}});
              } else {
                const chronicle = find(root, chronicleId);
                if (!chronicle?.parent) throw new Error('No hay un contenedor de cronica inequivoco');
                const index = chronicle.parent.children.findIndex(child => child.id === chronicle.id);
                target = await window.$e.run('document/elements/create', {
                  container: chronicle.parent,
                  model: {elType: 'widget', widgetType: 'image', settings: {
                    image: media, image_size: 'medium', align: 'center',
                    width: {unit: 'px', size: 320}, _css_classes: 'basket-sync-team-photo'
                  }, elements: []}, options: {at: index}
                });
              }
              return target?.id || 'created';
              };
              return Promise.race([operation(), new Promise((_, reject) => setTimeout(() => reject(new Error('Elementor no confirma la foto en 15 segundos')), 15000))]);
            }""",
            {"targetId": target_id, "chronicleId": chronicle_id, "media": media},
        )
        self.structured_fields_cache.clear()
        return str(result)

    def team_photo_container_locators(self) -> list[Any]:
        candidates: list[Any] = []
        for item in self._block_visual_elements():
            class_name = normalize_lookup_key(str(item.get("className") or "")).lower()
            widget_type = normalize_lookup_key(str(item.get("widgetType") or "")).lower()
            if "top section" in class_name or "top-section" in class_name:
                continue
            if "e empty" in class_name or "e-empty" in class_name:
                continue
            if "widget-image" in class_name or "styledimage" in class_name or "image" in widget_type:
                continue
            if float(item.get("x") or 0.0) > 80.0:
                continue
            width = float(item.get("width") or 0.0)
            height = float(item.get("height") or 0.0)
            if width < 250.0 or width > 450.0 or height < 180.0 or height > 420.0:
                continue
            locator = self.preview.locator(f'[data-id="{item["dataId"]}"]').first
            if locator.count() > 0:
                candidates.append(locator)
        return candidates

    def team_photo_container_locator(self) -> Any:
        locators = self.team_photo_container_locators()
        if not locators:
            raise LookupError(f"No se encontró un contenedor de foto de equipo dentro del bloque '{self.heading_text}'.")
        return locators[0]

    def has_team_photo_container(self) -> bool:
        return bool(self.team_photo_container_locators())

    def team_photo_uses_source(self, source_name: str) -> bool:
        target_key = normalize_lookup_key(Path(source_name).stem or source_name).replace(" ", "")
        if not target_key:
            return False
        for item in self._block_visual_elements():
            class_name = normalize_lookup_key(str(item.get("className") or "")).lower()
            if "top section" in class_name or "top-section" in class_name:
                continue
            if "e empty" in class_name or "e-empty" in class_name:
                continue
            if float(item.get("x") or 0.0) > 80.0:
                continue
            width = float(item.get("width") or 0.0)
            height = float(item.get("height") or 0.0)
            if width < 250.0 or width > 450.0 or height < 180.0 or height > 420.0:
                continue
            descriptor = f"{item.get('backgroundImage', '')} {item.get('imgSrc', '')}"
            descriptor_key = normalize_lookup_key(descriptor).replace(" ", "")
            if descriptor_key and target_key in descriptor_key:
                return True
        return False

    def sponsor_image_locators(self) -> list[Any]:
        sponsor_heading = next(
            (
                item for item in self._block_widgets()
                if str(item.get("type") or "") == "heading"
                and normalize_lookup_key(str(item.get("text") or "")) == "PATROCINADOR"
            ),
            None,
        )
        sponsor_y = float(sponsor_heading["y"]) if sponsor_heading is not None else None
        locators: list[Any] = []
        for item in self._block_visual_elements():
            if not str(item.get("imgSrc") or "").strip():
                continue
            if sponsor_y is not None and float(item.get("y") or 0.0) < sponsor_y - 5.0:
                continue
            class_name = normalize_lookup_key(str(item.get("className") or "")).lower()
            if "gallery grid" in class_name or "gallery-grid" in class_name:
                continue
            if float(item.get("x") or 0.0) > 80.0:
                continue
            width = float(item.get("width") or 0.0)
            height = float(item.get("height") or 0.0)
            if width < 150.0 or width > 450.0 or height < 100.0:
                continue
            locator = self.preview.locator(f'[data-id="{item["dataId"]}"]').first
            if locator.count() > 0:
                locators.append(locator)
        locators.sort(key=lambda loc: (loc.bounding_box() or {"y": 0.0}).get("y", 0.0))
        return locators

    def team_image_locators(self) -> list[Any]:
        sponsor_heading = next(
            (
                item for item in self._block_widgets()
                if str(item.get("type") or "") == "heading"
                and normalize_lookup_key(str(item.get("text") or "")) == "PATROCINADOR"
            ),
            None,
        )
        sponsor_y = float(sponsor_heading["y"]) if sponsor_heading is not None else None

        locators: list[Any] = []
        for item in self._block_visual_elements():
            img_src = str(item.get("imgSrc") or "").strip()
            if not img_src:
                continue
            class_name = normalize_lookup_key(str(item.get("className") or "")).lower()
            if "gallery grid" in class_name or "gallery-grid" in class_name:
                continue
            if sponsor_y is not None and float(item.get("y") or 0.0) >= sponsor_y - 5.0:
                continue
            if float(item.get("x") or 0.0) > 80.0:
                continue
            width = float(item.get("width") or 0.0)
            height = float(item.get("height") or 0.0)
            if width < 180.0 or width > 450.0 or height < 120.0 or height > 420.0:
                continue
            locator = self.preview.locator(f'[data-id="{item["dataId"]}"]').first
            if locator.count() > 0:
                locators.append(locator)
        locators.sort(key=lambda loc: (loc.bounding_box() or {"y": 0.0}).get("y", 0.0))
        return locators

    def image_locators(self) -> list[Any]:
        return self.team_image_locators()

    def image_locator(self) -> Any:
        widgets = self.image_locators()
        if not widgets:
            raise LookupError(f"No se encontró un widget de imagen dentro del bloque '{self.heading_text}'.")
        return widgets[0]

    def _image_widgets(self) -> list[dict[str, Any]]:
        images = []
        for item in self._block_widgets():
            if str(item.get("type") or "") != "image":
                continue
            class_name = normalize_lookup_key(str(item.get("className") or "")).lower()
            if "gallery grid" in class_name or "gallery-grid" in class_name:
                continue
            images.append(item)
        images.sort(key=lambda item: (item["y"], item["x"]))
        return images

    def _image_widget(self) -> dict[str, Any] | None:
        widgets = self._image_widgets()
        return widgets[0] if widgets else None


class SidePanelContext:
    def __init__(self, page: Any, selectors: dict[str, Any]) -> None:
        self.page = page
        self.selectors = selectors.get("editor") or {}

    def get_selected_widget_type(self) -> str:
        locator = self.page.locator(str(self.selectors.get("widget_type_label", ""))).first
        if locator.count() == 0:
            return ""
        return (locator.text_content() or "").strip()

    def ensure_widget_type(self, *expected_keywords: str) -> str:
        widget_type = self.get_selected_widget_type()
        normalized = normalize_lookup_key(widget_type)
        if expected_keywords and not any(normalize_lookup_key(keyword) in normalized for keyword in expected_keywords):
            raise ValueError(f"Widget seleccionado inesperado: '{widget_type}'.")
        return widget_type

    def set_header_title(self, value: str) -> None:
        locator = self.page.locator(str(self.selectors.get("header_title_input", ""))).first
        if locator.count() == 0:
            raise LookupError("No se encontr? el campo 'T?tulo' del widget Encabezado.")
        locator.fill(value)

    def set_header_weight(self, value: str) -> None:
        normalized = normalize_lookup_key(value)
        target_value = "bold" if normalized == "BOLD" else "thin"
        locator = self._first_visible_locator(
            str(self.selectors.get("header_weight_select", "")).strip(),
            "#elementor-controls select[data-setting='thegem_heading_weight']",
            "#elementor-controls select[data-setting='title_weight']",
            "#elementor-panel-content-wrapper select[data-setting='thegem_heading_weight']",
            "#elementor-panel-content-wrapper select[data-setting='title_weight']",
        )
        if locator is None:
            raise LookupError("No se encontr? el selector de peso del widget Encabezado.")
        try:
            locator.select_option(value=target_value)
        except Exception:
            locator.select_option(label="Bold" if target_value == "bold" else "Thin")

    def select_text_editor_mode(self, mode: str) -> None:
        selector_key = "text_editor_visual_tab" if mode == "visual" else "text_editor_html_tab"
        selectors = [str(self.selectors.get(selector_key, "")).strip()]
        if mode == "visual":
            selectors.extend([
                "#elementor-panel-content-wrapper .wp-switch-editor.switch-tmce",
                "#elementor-panel-content-wrapper .wp-switch-editor:has-text('Visual')",
                "button:has-text('Visual')",
            ])
            labels = ["Visual"]
        else:
            selectors.extend([
                "#elementor-panel-content-wrapper .wp-switch-editor.switch-html",
                "#elementor-panel-content-wrapper .wp-switch-editor:has-text('C?digo')",
                "#elementor-panel-content-wrapper .wp-switch-editor:has-text('Code')",
                "button:has-text('C?digo')",
                "button:has-text('Code')",
            ])
            labels = ["C?digo", "Code", "HTML"]

        locator = self._first_visible_locator(*selectors)
        if locator is None:
            locator = self._first_text_match(labels, roles=("button", "tab"))
        if locator is None:
            if mode == "html" and self._text_editor_textarea() is not None:
                return
            raise LookupError(f"No se encontr? la pesta?a '{mode}' del Editor de texto.")
        locator.click()

    def set_text_editor_visual(self, value: str) -> None:
        locator = self._first_visible_locator(
            str(self.selectors.get("text_editor_visual_editable", "")).strip(),
            "#elementor-controls .mce-content-body",
            "#elementor-controls [contenteditable='true']",
        )
        if locator is not None:
            locator.evaluate(
                r"""
                (node, payload) => {
                  node.focus();
                  const parts = payload.split(/
{2,}|
+/).map(item => item.trim()).filter(Boolean);
                  if ('value' in node) {
                    node.value = payload;
                  } else {
                    node.innerHTML = '';
                    for (const part of parts) {
                      const paragraph = node.ownerDocument.createElement('p');
                      paragraph.textContent = part;
                      node.appendChild(paragraph);
                    }
                  }
                  node.dispatchEvent(new Event('input', { bubbles: true }));
                  node.dispatchEvent(new Event('change', { bubbles: true }));
                }
                """,
                value,
            )
            return

        iframe_locator = self._first_visible_locator(
            str(self.selectors.get("text_editor_visual_iframe", "")).strip(),
            "#elementor-controls iframe",
            "#elementor-panel-content-wrapper .elementor-control-editor iframe",
        )
        if iframe_locator is None:
            raise LookupError("No se encontr? el ?rea visual del Editor de texto.")
        handle = iframe_locator.element_handle()
        if handle is None:
            raise LookupError("No se pudo acceder al iframe visual del Editor de texto.")
        frame = handle.content_frame()
        if frame is None:
            raise LookupError("No se pudo resolver el iframe visual del Editor de texto.")
        body = frame.locator("body#tinymce, body.mce-content-body, body").first
        if body.count() == 0:
            raise LookupError("No se encontr? el cuerpo visual del Editor de texto.")
        body.evaluate(
            r"""
            (node, payload) => {
              const doc = node.ownerDocument;
              const parts = payload.split(/
{2,}|
+/).map(item => item.trim()).filter(Boolean);
              node.focus();
              node.innerHTML = '';
              for (const part of parts) {
                const paragraph = doc.createElement('p');
                paragraph.textContent = part;
                node.appendChild(paragraph);
              }
              const editor = doc.defaultView?.tinymce?.activeEditor;
              if (editor) {
                editor.setContent(node.innerHTML);
                editor.setDirty(true);
                editor.save();
              }
              node.dispatchEvent(new Event('input', { bubbles: true }));
              node.dispatchEvent(new Event('change', { bubbles: true }));
            }
            """,
            value,
        )

    def set_text_editor_html(self, value: str) -> None:
        locator = self._text_editor_textarea()
        if locator is None:
            raise LookupError("No se encontr? el ?rea HTML del Editor de texto.")
        if locator.is_visible():
            locator.fill(value)
            locator.evaluate(
                r"""
                (node) => {
                  node.dispatchEvent(new Event('input', { bubbles: true }));
                  node.dispatchEvent(new Event('change', { bubbles: true }));
                  node.blur();
                }
                """
            )
            return
        locator.evaluate(
            r"""
            (node, payload) => {
              node.value = payload;
              node.dispatchEvent(new Event('input', { bubbles: true }));
              node.dispatchEvent(new Event('change', { bubbles: true }));
            }
            """,
            value,
        )

    def get_text_editor_plain_value(self) -> str:
        locator = self._text_editor_textarea()
        if locator is None:
            raise LookupError("No se encontr? el valor HTML del Editor de texto.")
        return locator.input_value()

    def set_image_file(
        self,
        image_path: Path,
        *,
        reuse_existing_name: str | None = None,
        require_existing_media: bool = False,
    ) -> bool:
        self.ensure_panel_section_open("Content", "Contenido")
        return self._set_media_file(
            image_path,
            str(self.selectors.get("image_replace_button", "")).strip(),
            "#elementor-controls .elementor-control-media__replace",
            "#elementor-controls .elementor-control-media-upload-button",
            reuse_existing_name=reuse_existing_name,
            require_existing_media=require_existing_media,
        )

    def select_style_tab(self) -> None:
        tab = self._first_visible_locator(
            str(self.selectors.get("style_tab_button", "")).strip(),
            "#elementor-panel-content-wrapper .elementor-tab-control-style",
            "#elementor-panel .elementor-tab-control-style",
            "button:has-text('Estilo')",
            "button:has-text('Style')",
        )
        if tab is None:
            raise LookupError("No se encontró la pestaña de estilo del contenedor.")
        tab.click()
        self.page.wait_for_timeout(300)

    def set_background_image_file(
        self,
        image_path: Path,
        *,
        reuse_existing_name: str | None = None,
        require_existing_media: bool = False,
    ) -> bool:
        self.select_style_tab()
        self.ensure_panel_section_open("Fondo", "Background")
        classic_option = self._first_visible_locator(
            str(self.selectors.get("background_classic_option", "")).strip(),
            "#elementor-controls .elementor-control-background_background input[value='classic']",
            "#elementor-panel-content-wrapper .elementor-control-background_background input[value='classic']",
            "#elementor-controls .elementor-control-background_background [data-tooltip='Clásico']",
            "#elementor-panel-content-wrapper .elementor-control-background_background [data-tooltip='Clásico']",
        )
        if classic_option is not None:
            try:
                classic_option.check(force=True)
            except Exception:
                classic_option.click(force=True)
            self.page.wait_for_timeout(200)
        return self._set_media_file(
            image_path,
            str(self.selectors.get("background_image_replace_button", "")).strip(),
            "#elementor-controls .elementor-control-background_image .elementor-control-media__replace",
            "#elementor-panel-content-wrapper .elementor-control-background_image .elementor-control-media__replace",
            reuse_existing_name=reuse_existing_name,
            require_existing_media=require_existing_media,
        )

    def click_save(self) -> None:
        self.dismiss_media_modal()
        dropdown = self._first_visible_locator(
            str(self.selectors.get("save_dropdown_toggle", "")).strip(),
            "[aria-label='Guardar opciones']",
            "button:has-text('Guardar opciones')",
        )
        if dropdown is not None:
            dropdown.click()
            self.page.wait_for_timeout(300)
            draft_action = self._first_visible_locator(
                str(self.selectors.get("save_draft_action", "")).strip(),
                "[role='menuitem']:has-text('Guardar borrador')",
                "button:has-text('Guardar borrador')",
            )
            if draft_action is not None and draft_action.is_enabled():
                draft_action.click()
                return

        for label in ("Actualizar", "Guardar borrador"):
            button = self._first_visible_locator(
                f"button:has-text('{label}')",
                f"[role='button']:has-text('{label}')",
            )
            if button is not None and button.is_enabled():
                button.click()
                return

        publish_button = self._first_visible_locator(
            "button:has-text('Publicar')",
            "[role='button']:has-text('Publicar')",
            str(self.selectors.get("save_button", "")).strip(),
        )
        if publish_button is not None:
            raise RuntimeError("Solo se ha encontrado la acci?n 'Publicar'. Por seguridad la herramienta no publica autom?ticamente.")

        raise LookupError("No se encontr? una acci?n segura de guardar/actualizar en Elementor.")

    def _text_editor_textarea(self) -> Any | None:
        selectors = [
            str(self.selectors.get("text_editor_html_textarea", "")).strip(),
            "#elementor-panel-content-wrapper .wp-editor-area",
            "#elementor-controls textarea[data-setting='editor']",
            "#elementor-controls textarea",
        ]
        for selector in selectors:
            if not selector:
                continue
            locator = self.page.locator(selector)
            if locator.count() > 0:
                return locator.first
        return None

    def _first_visible_locator(self, *selectors: str) -> Any | None:
        for selector in selectors:
            if not selector:
                continue
            locator = self.page.locator(selector)
            count = locator.count()
            for index in range(count):
                candidate = locator.nth(index)
                try:
                    if candidate.is_visible():
                        return candidate
                except Exception:
                    continue
        return None

    def _first_text_match(self, labels: list[str], *, roles: tuple[str, ...]) -> Any | None:
        for label in labels:
            for role in roles:
                locator = self.page.get_by_role(role, name=label)
                count = locator.count()
                for index in range(count):
                    candidate = locator.nth(index)
                    try:
                        if candidate.is_visible():
                            return candidate
                    except Exception:
                        continue
        return None

    def _wait_for_visible_locator(self, *selectors: str, timeout_ms: int = 10000) -> Any | None:
        deadline = time.monotonic() + (timeout_ms / 1000)
        while time.monotonic() < deadline:
            locator = self._first_visible_locator(*selectors)
            if locator is not None:
                return locator
            self.page.wait_for_timeout(200)
        return None

    def ensure_panel_section_open(self, *labels: str) -> None:
        for label in labels:
            button = self._first_visible_locator(
                f"#elementor-controls .elementor-panel-heading:has-text('{label}')",
                f"#elementor-panel-content-wrapper .elementor-panel-heading:has-text('{label}')",
                f"button:has-text('{label}')",
            )
            if button is None:
                continue
            parent = button.locator("xpath=ancestor::*[contains(@class,'elementor-control-type-section')][1]").first
            try:
                class_name = parent.get_attribute("class") or ""
            except Exception:
                class_name = ""
            if "e-open" not in class_name:
                button.click()
                self.page.wait_for_timeout(300)
            return
        raise LookupError(f"No se encontró ninguna sección del panel entre: {', '.join(labels)}")

    def dismiss_media_modal(self) -> None:
        close_button = self._first_visible_locator(
            ".media-modal .media-modal-close",
            "#wp-media-modal .media-modal-close",
        )
        if close_button is not None:
            try:
                close_button.click()
                self.page.wait_for_timeout(300)
            except Exception:
                pass
        backdrop = self._first_visible_locator(".media-modal-backdrop")
        if backdrop is not None:
            try:
                self.page.keyboard.press("Escape")
                self.page.wait_for_timeout(300)
            except Exception:
                pass

    def _set_media_file(
        self,
        image_path: Path,
        *replace_selectors: str,
        reuse_existing_name: str | None = None,
        require_existing_media: bool = False,
    ) -> bool:
        self.dismiss_media_modal()
        replace = self._first_visible_locator(*replace_selectors)
        if replace is None:
            raise LookupError("No se encontró la acción para reemplazar la imagen seleccionada.")
        replace.click()

        modal_selector = str(self.selectors.get("media_modal", "")).strip() or ".media-modal"
        modal = self._wait_for_visible_locator(
            modal_selector,
            "#wp-media-modal",
            timeout_ms=10000,
        )
        if modal is None:
            raise LookupError("No se abrió la biblioteca multimedia de WordPress.")
        if reuse_existing_name:
            if self._select_existing_media(modal_selector, reuse_existing_name):
                return True
            if require_existing_media:
                self.dismiss_media_modal()
                raise LookupError(
                    f"No se encontró '{reuse_existing_name}' en la biblioteca multimedia; no se sube de nuevo para evitar duplicados."
                )
        file_input = self._first_visible_locator(
            str(self.selectors.get("media_file_input", "")).strip(),
            f"{modal_selector} input[type='file']",
            "#wp-media-modal input[type='file']",
            "input[type='file']",
        )
        if file_input is None:
            raise LookupError("No se encontró el input de subida de imágenes en la biblioteca multimedia.")
        file_input.set_input_files(str(image_path))
        self.page.wait_for_timeout(3000)

        select_button = self._first_visible_locator(
            str(self.selectors.get("media_select_button", "")).strip(),
            ".media-modal .media-button-select",
            ".media-modal button:has-text('Seleccionar')",
        )
        if select_button is None:
            raise LookupError("No se encontró el botón para confirmar la imagen seleccionada.")
        select_button.click()
        try:
            modal.wait_for(state="hidden", timeout=30000)
        except Exception:
            self.dismiss_media_modal()
        return False

    def _select_existing_media(self, modal_selector: str, media_name: str) -> bool:
        self._ensure_media_library_tab(modal_selector)
        search_input = self._first_visible_locator(
            str(self.selectors.get("media_search_input", "")).strip(),
            f"{modal_selector} .media-toolbar-primary.search-form input[type='search']",
            "#wp-media-modal .media-toolbar-primary.search-form input[type='search']",
            f"{modal_selector} input[type='search']",
        )
        if search_input is None:
            attachment = self._find_matching_media_attachment(modal_selector, media_name, Path(media_name).stem)
            return self._choose_media_attachment(modal_selector, attachment) if attachment is not None else False

        for query in self._media_search_queries(media_name):
            try:
                search_input.fill(query)
            except Exception:
                continue
            self.page.wait_for_timeout(1200)
            attachment = self._find_matching_media_attachment(modal_selector, media_name, query)
            if attachment is None:
                continue
            return self._choose_media_attachment(modal_selector, attachment)
        return False

    def _choose_media_attachment(self, modal_selector: str, attachment: Any) -> bool:
        try:
            attachment.click(force=True)
        except Exception:
            attachment.click()
        self.page.wait_for_timeout(400)
        select_button = self._first_visible_locator(
            str(self.selectors.get("media_select_button", "")).strip(),
            ".media-modal .media-button-select",
            ".media-modal button:has-text('Seleccionar')",
        )
        if select_button is None:
            self.dismiss_media_modal()
            return True
        try:
            if not select_button.is_enabled():
                self.dismiss_media_modal()
                return True
        except Exception:
            pass
        select_button.click()
        modal = self.page.locator(modal_selector).first
        try:
            modal.wait_for(state="hidden", timeout=30000)
        except Exception:
            self.dismiss_media_modal()
        return True

    def _first_visible_media_attachment(self, modal_selector: str) -> Any | None:
        selector = str(self.selectors.get("media_attachment_items", "")).strip() or f"{modal_selector} .attachments .attachment"
        attachment_locator = self.page.locator(selector)
        count = min(attachment_locator.count(), 50)
        for index in range(count):
            candidate = attachment_locator.nth(index)
            try:
                if candidate.is_visible():
                    return candidate
            except Exception:
                continue
        return None

    def _ensure_media_library_tab(self, modal_selector: str) -> None:
        library_tab = self._first_visible_locator(
            str(self.selectors.get("media_library_tab", "")).strip(),
            f"{modal_selector} .media-menu-item:has-text('Biblioteca multimedia')",
            "#wp-media-modal .media-menu-item:has-text('Biblioteca multimedia')",
            f"{modal_selector} .media-menu-item:has-text('Media Library')",
            "#wp-media-modal .media-menu-item:has-text('Media Library')",
        )
        if library_tab is None:
            return
        try:
            library_tab.click()
            self.page.wait_for_timeout(700)
        except Exception:
            return

    def _find_matching_media_attachment(self, modal_selector: str, media_name: str, query: str) -> Any | None:
        selector = str(self.selectors.get("media_attachment_items", "")).strip() or f"{modal_selector} .attachments .attachment"
        attachment_locator = self.page.locator(selector)
        count = min(attachment_locator.count(), 50)
        if count <= 0:
            return None

        target_key = normalize_lookup_key(media_name).replace(" ", "")
        stem_key = normalize_lookup_key(Path(media_name).stem).replace(" ", "")
        query_key = normalize_lookup_key(query).replace(" ", "")
        best_locator: Any | None = None
        best_score = 0

        for index in range(count):
            candidate = attachment_locator.nth(index)
            try:
                if not candidate.is_visible():
                    continue
            except Exception:
                continue
            try:
                descriptor = candidate.evaluate(
                    r"""
                    (node) => {
                      const parts = [
                        node.getAttribute('aria-label') || '',
                        node.getAttribute('data-id') || '',
                        node.dataset?.title || '',
                        node.querySelector('.filename')?.textContent || '',
                        node.querySelector('.attachment-preview')?.getAttribute('aria-label') || '',
                        node.querySelector('img')?.getAttribute('alt') || '',
                        node.querySelector('img')?.getAttribute('src') || '',
                        node.textContent || '',
                      ];
                      return parts.join(' ');
                    }
                    """
                )
            except Exception:
                descriptor = ""
            score = self._media_match_score(str(descriptor), target_key=target_key, stem_key=stem_key, query_key=query_key)
            if score > best_score:
                best_score = score
                best_locator = candidate

        return best_locator if best_score > 0 else None

    def _media_search_queries(self, media_name: str) -> list[str]:
        stem = Path(media_name).stem
        candidates = [
            stem,
            media_name,
            stem.replace("_", " "),
            stem.replace("-", " "),
        ]
        unique: list[str] = []
        seen: set[str] = set()
        for candidate in candidates:
            text = candidate.strip()
            key = text.casefold()
            if text and key not in seen:
                unique.append(text)
                seen.add(key)
        return unique

    def _media_match_score(self, descriptor: str, *, target_key: str, stem_key: str, query_key: str) -> int:
        descriptor_key = normalize_lookup_key(descriptor).replace(" ", "")
        if not descriptor_key:
            return 0
        if target_key and target_key in descriptor_key:
            return 300 + len(target_key)
        if stem_key and stem_key in descriptor_key:
            return 250 + len(stem_key)
        if query_key and query_key in descriptor_key:
            return 200 + len(query_key)
        return 0


def _find_heading_widget(preview: PreviewContext, candidate_headings: list[str]) -> tuple[str, str] | None:
    widgets = preview.root().evaluate(
        r"""
        () => Array.from(document.querySelectorAll('.elementor-widget-heading')).map((node) => ({
          dataId: node.getAttribute('data-id') || '',
          text: (node.textContent || '').trim().replace(/\s+/g, ' '),
          x: node.getBoundingClientRect().left + window.scrollX,
          y: node.getBoundingClientRect().top + window.scrollY,
          width: node.getBoundingClientRect().width,
          height: node.getBoundingClientRect().height,
          type: 'heading',
        })).filter(item => item.dataId)
        """
    )
    headings = [dict(item) for item in widgets]
    best: tuple[int, str, str] | None = None
    for candidate in candidate_headings:
        for item in headings:
            score = _heading_match_score(candidate, str(item.get("text") or ""))
            if score <= 0:
                continue
            if _is_team_anchor_candidate(item):
                score += 5
            if best is None or score > best[0]:
                best = (score, candidate, str(item["dataId"]))
    if best is None:
        return None
    return best[1], best[2]


def _find_ordered_team_anchor(preview: PreviewContext, expected_order: int) -> tuple[str, str] | None:
    widgets = preview.root().evaluate(
        r"""
        () => Array.from(document.querySelectorAll('.elementor-widget-heading')).map((node) => {
          const rect = node.getBoundingClientRect();
          return {
            dataId: node.getAttribute('data-id') || '',
            text: (node.textContent || '').trim().replace(/\s+/g, ' '),
            x: rect.left + window.scrollX,
            y: rect.top + window.scrollY,
            width: rect.width,
            height: rect.height,
            type: 'heading',
          };
        }).filter(item => item.dataId)
        """
    )
    anchors = [dict(item) for item in widgets if _is_team_anchor_candidate(dict(item))]
    anchors.sort(key=lambda entry: (entry["y"], entry["x"]))
    index = expected_order - 1
    if 0 <= index < len(anchors):
        item = anchors[index]
        return str(item.get("text") or ""), str(item.get("dataId") or "")
    return None


def count_team_anchors(preview: PreviewContext) -> int:
    widgets = preview.root().evaluate(
        r"""
        () => Array.from(document.querySelectorAll('.elementor-widget-heading')).map((node) => {
          const rect = node.getBoundingClientRect();
          return {
            dataId: node.getAttribute('data-id') || '',
            text: (node.textContent || '').trim().replace(/\s+/g, ' '),
            x: rect.left + window.scrollX,
            y: rect.top + window.scrollY,
            width: rect.width,
            height: rect.height,
            type: 'heading',
          };
        }).filter(item => item.dataId)
        """
    )
    anchors = [dict(item) for item in widgets if _is_team_anchor_candidate(dict(item))]
    return len(anchors)


def duplicate_ordered_team_block(preview: PreviewContext, source_order: int) -> bool:
    if not hasattr(preview, "page"):
        return False
    ordered = _find_ordered_team_anchor(preview, source_order)
    if ordered is None:
        return False

    _, data_id = ordered
    before = count_team_anchors(preview)
    try:
        preview.page.evaluate(
            r"""
            (targetId) => {
              const rootContainer = window.elementor?.documents?.getCurrent?.()?.container;
              if (!rootContainer || !window.$e) {
                return false;
              }
              const walk = (container) => {
                if (!container) {
                  return null;
                }
                if (container.id === targetId) {
                  return container;
                }
                for (const child of container.children || []) {
                  const found = walk(child);
                  if (found) {
                    return found;
                  }
                }
                return null;
              };
              let container = walk(rootContainer);
              if (!container) {
                return false;
              }
              if (container.type === 'widget' && container.parent) {
                container = container.parent;
              }
              window.$e.run('document/elements/duplicate', { container });
              return true;
            }
            """,
            data_id,
        )
        preview.page.wait_for_timeout(4000)
    except Exception:
        preview.page.wait_for_timeout(8000)
    try:
        return count_team_anchors(preview) >= before + 1
    except Exception:
        return False


def _heading_match_score(candidate_text: str, widget_text: str) -> int:
    candidate_key = normalize_lookup_key(candidate_text)
    widget_key = normalize_lookup_key(widget_text)
    if not candidate_key or not widget_key:
        return 0

    candidate_compact = candidate_key.replace(" ", "")
    widget_compact = widget_key.replace(" ", "")
    if widget_key == candidate_key:
        return 100
    if candidate_key in widget_key:
        return 85
    if candidate_compact and candidate_compact in widget_compact:
        return 75

    candidate_tokens = [token for token in candidate_key.split(" ") if token]
    widget_tokens = [token for token in widget_key.split(" ") if token]
    widget_token_set = set(widget_tokens)
    for token in _distinctive_heading_tokens(candidate_tokens):
        if token not in widget_token_set and token not in widget_compact:
            return 0
    overlap = sum(1 for token in candidate_tokens if token in widget_tokens or token in widget_compact)
    if candidate_tokens and overlap >= max(2, len(candidate_tokens) - 1):
        return 40 + overlap
    return 0


def _matches_candidate_heading(observed_text: str, candidates: list[str]) -> bool:
    for candidate in candidates:
        if _heading_match_score(candidate, observed_text) >= 55:
            return True
    return False


def _distinctive_heading_tokens(tokens: list[str]) -> list[str]:
    generic = {
        "SENIOR",
        "SUB",
        "22",
        "ALEVIN",
        "INFANTIL",
        "CADETE",
        "JUNIOR",
        "FEMENINO",
        "FEMENINA",
        "MASCULINO",
        "MASC",
        "MAS",
        "PREFERENTE",
        "ANO",
        "O",
        "PRIMER",
        "PRIMERA",
        "SEGUNDO",
        "SEGUNDA",
    }
    distinctive: list[str] = []
    for token in tokens:
        if token in generic:
            continue
        if token.isdigit():
            distinctive.append(token)
            continue
        if len(token) >= 4 or token in {"A", "B", "C", "CAS", "CB", "CD", "AB"}:
            distinctive.append(token)
    return distinctive


def _is_team_anchor_candidate(item: dict[str, Any]) -> bool:
    if str(item.get("type") or "") != "heading":
        return False
    return float(item.get("x") or 0.0) <= 60.0 and float(item.get("width") or 0.0) >= 600.0 and float(item.get("y") or 0.0) >= 500.0


def _uses_structured_lookup(field_name: str) -> bool:
    if field_name in {"team_name", "competition", "group", "home_team", "score", "away_team", "table_home_name", "table_away_name", "players_text", "chronicle_text"}:
        return True
    return field_name.startswith(("period_label_", "home_period_", "away_period_"))


def _candidate_texts(value: str | list[str]) -> list[str]:
    if isinstance(value, str):
        values = [value]
    else:
        values = list(value)
    seen: set[str] = set()
    unique: list[str] = []
    for item in values:
        text = item.strip()
        if text and text not in seen:
            unique.append(text)
            seen.add(text)
    return unique


def _normalize_selector(selector: str) -> str:
    text = selector.strip()
    if text.startswith("xpath="):
        return text
    if text.startswith(".//") or text.startswith("//") or text.startswith("("):
        return f"xpath={text}"
    return text
