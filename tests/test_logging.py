"""Structured logging and secret redaction."""

from __future__ import annotations

import json
import logging
from pathlib import Path

from scout import logging as scout_logging


def test_log_records_are_json_and_redacted(settings, tmp_path: Path, monkeypatch) -> None:
    log_path = tmp_path / "scout.log"
    settings.api_key = "super-secret-value-42"
    settings.log_file = log_path
    monkeypatch.setattr(scout_logging, "_CONFIGURED", False)

    path = scout_logging.configure_logging(settings)
    assert path == log_path

    logger = scout_logging.get_logger("test")
    logger.info("using key super-secret-value-42", extra={"tool": "get_os_info", "status": "ok"})
    for handler in logging.getLogger("scout").handlers:
        handler.flush()

    lines = log_path.read_text(encoding="utf-8").strip().splitlines()
    assert lines
    assert "super-secret-value-42" not in log_path.read_text(encoding="utf-8")
    payload = json.loads(lines[-1])
    assert payload["level"] == "INFO"
    assert payload["tool"] == "get_os_info"
    assert payload["status"] == "ok"
    assert "redacted" in payload["message"]
