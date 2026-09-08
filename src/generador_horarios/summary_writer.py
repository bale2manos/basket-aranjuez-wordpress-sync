from __future__ import annotations

from datetime import datetime
import json
from pathlib import Path

from .models import MatchRecord, RenderMode


def _serialize_match(record: MatchRecord) -> dict[str, object]:
    return {
        "match_key": record.match_key,
        "output_image": record.output_image,
        "page_type": record.page_type,
        "page_index": record.page_index,
        "fecha": record.fecha.isoformat() if record.fecha else None,
        "hora": record.hora.strftime("%H:%M") if record.hora else None,
        "categoria": record.categoria,
        "is_playoff": record.is_playoff,
        "local_original": record.local_original,
        "visitante_original": record.visitante_original,
        "local_display": record.local_display,
        "visitante_display": record.visitante_display,
        "venue_display": record.venue_display,
        "render_mode": record.render_mode,
        "middle_text": record.middle_text,
        "result_text": record.result_text,
        "result_source": record.result_source,
        "status": record.status,
        "skip_reason": record.skip_reason,
    }


def write_summary(
    output_dir: Path,
    source_excel: Path,
    images: list[dict[str, object]],
    records: list[MatchRecord],
    render_mode: RenderMode,
    results_doc_path: Path | None,
    results_overrides_path: Path | None,
) -> Path:
    payload = {
        "source_excel": str(source_excel),
        "output_dir": str(output_dir),
        "generated_at": datetime.now().astimezone().isoformat(),
        "render_mode": render_mode,
        "results_doc": str(results_doc_path) if results_doc_path else None,
        "results_overrides_path": str(results_overrides_path) if results_overrides_path else None,
        "images": images,
        "items": [_serialize_match(record) for record in sorted(records, key=lambda item: item.row_index)],
    }
    summary_path = output_dir / "summary.json"
    summary_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return summary_path
