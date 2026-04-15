"""Structured logging helpers."""

import json
import logging
import sys
from typing import Any


class JsonFormatter(logging.Formatter):
    """Format log records as compact JSON."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        event = getattr(record, "event", None)
        if isinstance(event, dict):
            payload.update(event)
        return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def configure_logging(level: int = logging.INFO) -> None:
    """Configure root logging for CLI runs."""
    root = logging.getLogger()
    if any(isinstance(handler.formatter, JsonFormatter) for handler in root.handlers):
        root.setLevel(level)
        return
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(JsonFormatter())
    root.handlers[:] = [handler]
    root.setLevel(level)


def log_stage(
    stage: str,
    *,
    rows_in: int | None,
    rows_out: int | None,
    duration_ms: int | float | None,
    warnings: list[str] | None,
    logger_name: str = "tw_scanner",
) -> None:
    """Log the standard stage telemetry payload."""
    logging.getLogger(logger_name).info(
        "stage_complete",
        extra={
            "event": {
                "stage": stage,
                "rows_in": rows_in,
                "rows_out": rows_out,
                "duration_ms": duration_ms,
                "warnings": warnings or [],
            }
        },
    )
