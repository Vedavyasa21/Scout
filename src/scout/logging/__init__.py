"""Structured file logging with secret redaction.

Logs go to a rotating file, never to stdout: the CLI owns the terminal. Every
record carries optional structured fields (`task_id`, `tool`, `event`,
`status`, `duration_ms`, `permission`) when the caller supplies them.
"""

from __future__ import annotations

import json
import logging
import logging.handlers
from pathlib import Path
from typing import Any

from scout.config import Settings
from scout.security import redact_secrets

_STRUCTURED_FIELDS = ("task_id", "tool", "event", "status", "duration_ms", "permission")
_CONFIGURED = False


class RedactingFilter(logging.Filter):
    """Remove configured secrets from log messages and tracebacks."""

    def __init__(self, secrets: list[str]) -> None:
        super().__init__()
        self._secrets = secrets

    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = redact_secrets(record.getMessage(), self._secrets)
        record.args = ()
        if record.exc_text:
            record.exc_text = redact_secrets(record.exc_text, self._secrets)
        return True


class JsonFormatter(logging.Formatter):
    """One JSON object per line, including registered structured fields."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for key in _STRUCTURED_FIELDS:
            value = getattr(record, key, None)
            if value is not None:
                payload[key] = value
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)


def configure_logging(settings: Settings) -> Path:
    """Configure the `scout` logger tree once. Returns the log file path."""
    global _CONFIGURED
    path = settings.resolved_log_file
    if _CONFIGURED:
        return path

    logger = logging.getLogger("scout")
    logger.setLevel(settings.log_level)
    logger.propagate = False
    for handler in list(logger.handlers):
        logger.removeHandler(handler)

    file_handler = logging.handlers.RotatingFileHandler(
        path, maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8"
    )
    file_handler.setFormatter(JsonFormatter())
    secrets = [settings.api_key] if settings.api_key else []
    file_handler.addFilter(RedactingFilter(secrets))
    logger.addHandler(file_handler)

    _CONFIGURED = True
    return path


def get_logger(name: str) -> logging.Logger:
    """Return a namespaced Scout logger."""
    return logging.getLogger(f"scout.{name}")
