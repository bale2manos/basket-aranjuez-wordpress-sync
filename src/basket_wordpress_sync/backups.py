from __future__ import annotations

import json
import shutil
from datetime import datetime
from pathlib import Path


def backup_configuration(*, root_dir: Path, runtime, config_dir: Path | None = None) -> Path:
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_dir = (root_dir / runtime.backup_dir / timestamp).resolve()
    backup_dir.mkdir(parents=True, exist_ok=False)

    config_root = config_dir or (root_dir / "config")
    candidates = [
        root_dir / "configuracion.yaml",
        root_dir / "config.py",
        config_root / "runtime.yaml",
        config_root / "teams.yaml",
        config_root / "selectors.yaml",
    ]
    copied: list[str] = []
    for source in candidates:
        if not source.exists() or not source.is_file():
            continue
        shutil.copy2(source, backup_dir / source.name)
        copied.append(source.name)

    (backup_dir / "backup_manifest.json").write_text(
        json.dumps({"created_at": timestamp, "files": copied}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return backup_dir
