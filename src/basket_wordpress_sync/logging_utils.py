from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .json_utils import to_primitive
from .models import RunEvent


@dataclass(slots=True)
class EventLogger:
    logger: logging.Logger
    json_log_path: Path

    def record(self, level: str, code: str, message: str, **details: Any) -> RunEvent:
        event = RunEvent(level=level, code=code, message=message, details=to_primitive(details))
        getattr(self.logger, level.lower(), self.logger.info)(f"[{code}] {message}")
        with self.json_log_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(to_primitive(event), ensure_ascii=False) + "\n")
        return event


def build_logger(name: str, human_log_path: Path, json_log_path: Path) -> EventLogger:
    logger = logging.getLogger(name)
    logger.setLevel(logging.INFO)
    logger.handlers.clear()
    logger.propagate = False

    formatter = logging.Formatter("%(asctime)s %(levelname)s %(message)s")

    file_handler = logging.FileHandler(human_log_path, encoding="utf-8")
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(formatter)
    logger.addHandler(stream_handler)

    return EventLogger(logger=logger, json_log_path=json_log_path)
