from __future__ import annotations

from html import escape
from pathlib import Path

from .contexts import SidePanelContext, TeamSectionAnchor


class HeaderWidgetAdapter:
    def __init__(self, side_panel: SidePanelContext) -> None:
        self.side_panel = side_panel

    def update_field(
        self,
        anchor: TeamSectionAnchor,
        field_name: str,
        value: str,
        *,
        fallback_text: str | None = None,
        weight: str | None = None,
    ) -> None:
        anchor.click_field(field_name, fallback_text=fallback_text)
        self.side_panel.ensure_widget_type("Encabezado", "Heading")
        self.side_panel.set_header_title(value)
        if weight is not None:
            self.side_panel.set_header_weight(weight)


class TextEditorWidgetAdapter:
    def __init__(self, side_panel: SidePanelContext) -> None:
        self.side_panel = side_panel

    def update_field(
        self,
        anchor: TeamSectionAnchor,
        field_name: str,
        *,
        paragraphs: list[str],
        players_text: str | None = None,
        preferred_mode: str = "visual",
        fallback_text: str | None = None,
    ) -> str:
        anchor.click_field(field_name, fallback_text=fallback_text)
        self.side_panel.ensure_widget_type("Editor de texto", "Text Editor")
        rich_text = self.compose_rich_text(paragraphs=paragraphs, players_text=players_text)
        html_text = self.compose_html(paragraphs=paragraphs, players_text=players_text)
        modes = [preferred_mode, "html" if preferred_mode == "visual" else "visual"]
        last_error: Exception | None = None
        for mode in modes:
            try:
                self.side_panel.select_text_editor_mode(mode)
                if mode == "visual":
                    self.side_panel.set_text_editor_visual(rich_text)
                    try:
                        self.side_panel.select_text_editor_mode("html")
                        self.side_panel.set_text_editor_html(html_text)
                        try:
                            self.side_panel.select_text_editor_mode("visual")
                        except Exception:
                            pass
                        return "html"
                    except Exception:
                        return "visual"
                else:
                    self.side_panel.set_text_editor_html(html_text)
                    try:
                        self.side_panel.select_text_editor_mode("visual")
                    except Exception:
                        pass
                return mode
            except Exception as exc:
                last_error = exc
        if last_error is not None:
            raise last_error
        return preferred_mode

    def compose_rich_text(self, *, paragraphs: list[str], players_text: str | None = None) -> str:
        chunks = []
        if players_text:
            chunks.append(players_text)
        chunks.extend(paragraphs)
        return "\n\n".join(chunk for chunk in chunks if chunk)

    def compose_html(self, *, paragraphs: list[str], players_text: str | None = None) -> str:
        rendered = []
        if players_text:
            rendered.append(f"<p>{escape(players_text).replace(chr(10), '<br>')}</p>")
        for paragraph in paragraphs:
            rendered.append(f"<p>{escape(paragraph)}</p>")
        return "".join(rendered)


class ImageWidgetAdapter:
    def __init__(self, side_panel: SidePanelContext) -> None:
        self.side_panel = side_panel

    def update_image(
        self,
        anchor: TeamSectionAnchor,
        image_path: Path,
        *,
        reuse_existing_name: str | None = None,
        require_existing_media: bool = False,
    ) -> bool:
        locators = anchor.image_locators()
        if not locators:
            raise LookupError(f"No se encontró un widget de imagen dentro del bloque '{anchor.heading_text}'.")
        last_error: Exception | None = None
        for locator in locators:
            for _ in range(3):
                try:
                    self.side_panel.dismiss_media_modal()
                    anchor._click_locator(locator)
                    anchor.preview.page.wait_for_timeout(500)
                    self.side_panel.ensure_widget_type("Imagen", "Image")
                    return self.side_panel.set_image_file(
                        image_path,
                        reuse_existing_name=reuse_existing_name,
                        require_existing_media=require_existing_media,
                    )
                except Exception as exc:
                    last_error = exc
                    self.side_panel.dismiss_media_modal()
                    if isinstance(exc, ValueError):
                        break
                    anchor.preview.page.wait_for_timeout(400)
        if last_error is not None:
            raise last_error


class BackgroundContainerAdapter:
    def __init__(self, side_panel: SidePanelContext) -> None:
        self.side_panel = side_panel

    def update_image(
        self,
        anchor: TeamSectionAnchor,
        image_path: Path,
        *,
        reuse_existing_name: str | None = None,
        require_existing_media: bool = False,
    ) -> bool:
        locators = anchor.team_photo_container_locators()
        if not locators:
            raise LookupError(f"No se encontró un contenedor de foto de equipo dentro del bloque '{anchor.heading_text}'.")
        last_error: Exception | None = None
        for locator in locators:
            for _ in range(3):
                try:
                    self.side_panel.dismiss_media_modal()
                    anchor._click_locator(locator)
                    anchor.preview.page.wait_for_timeout(500)
                    self.side_panel.ensure_widget_type("Contenedor", "Container")
                    return self.side_panel.set_background_image_file(
                        image_path,
                        reuse_existing_name=reuse_existing_name,
                        require_existing_media=require_existing_media,
                    )
                except Exception as exc:
                    last_error = exc
                    self.side_panel.dismiss_media_modal()
                    if isinstance(exc, ValueError):
                        break
                    anchor.preview.page.wait_for_timeout(400)
        if last_error is not None:
            raise last_error
