from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from .settings import RuntimeSettings


@dataclass(slots=True)
class RunArtifacts:
    run_id: str
    output_dir: Path
    screenshot_dir: Path
    human_log_path: Path
    json_log_path: Path


def create_run_artifacts(root_dir: Path, runtime: RuntimeSettings, command_name: str) -> RunArtifacts:
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    run_id = f"{command_name}_{timestamp}"
    output_dir = (root_dir / runtime.output_dir / run_id).resolve()
    screenshot_dir = (root_dir / runtime.screenshots_dir / run_id).resolve()
    logs_dir = (root_dir / runtime.logs_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    screenshot_dir.mkdir(parents=True, exist_ok=True)
    logs_dir.mkdir(parents=True, exist_ok=True)
    return RunArtifacts(
        run_id=run_id,
        output_dir=output_dir,
        screenshot_dir=screenshot_dir,
        human_log_path=logs_dir / f"{run_id}.log",
        json_log_path=logs_dir / f"{run_id}.jsonl",
    )
