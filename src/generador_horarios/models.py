from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, time
from pathlib import Path
from typing import Literal


PageType = Literal["local", "visitante"]
RenderMode = Literal["horarios", "resultados"]
ResultSource = Literal["none", "docx", "override", "missing"]


@dataclass(slots=True)
class MatchRecord:
    row_index: int
    categoria: str
    local_original: str
    visitante_original: str
    fecha: date | None
    hora: time | None
    campo_original: str
    is_playoff: bool = False
    page_type: PageType | None = None
    page_index: int | None = None
    output_image: str | None = None
    local_display: str = ""
    visitante_display: str = ""
    venue_display: str = ""
    local_logo_path: Path | None = None
    visitante_logo_path: Path | None = None
    status: str = "pending"
    skip_reason: str | None = None
    warnings: list[str] = field(default_factory=list)
    render_mode: RenderMode = "horarios"
    middle_text: str = "VS"
    match_key: str = ""
    result_text: str | None = None
    result_source: ResultSource = "none"

    @property
    def sort_key(self) -> tuple[object, ...]:
        return (
            self.fecha or date.min,
            self.hora or time.min,
            self.categoria,
            self.local_original,
            self.visitante_original,
            self.row_index,
        )


@dataclass(slots=True)
class PageSpec:
    page_type: PageType
    page_index: int
    output_path: Path
    matches: list[MatchRecord]
    render_mode: RenderMode = "horarios"


@dataclass(slots=True)
class PipelineResult:
    output_dir: Path
    summary_path: Path
    images: list[dict[str, object]]
    rendered_count: int
    skipped_count: int
    render_mode: RenderMode
