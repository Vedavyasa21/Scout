"""Durable audit ledger for runs, approvals and tool execution.

The ledger is intentionally append-only JSON Lines so it can be inspected with
any text tool or consumed by the CLI. Records never include raw credentials and
argument values are truncated; audit failures never break an agent run.
"""

from __future__ import annotations

import json
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

MAX_FIELD_CHARS = 400
MAX_ITEMS = 30
ROTATE_BYTES = 5_000_000
ROTATE_KEEP = 2

_SENSITIVE_PARTS = (
    "password",
    "passwd",
    "secret",
    "token",
    "api_key",
    "apikey",
    "authorization",
    "cookie",
    "credential",
)


def _sensitive(key: str) -> bool:
    lowered = key.lower()
    return any(part in lowered for part in _SENSITIVE_PARTS)


def _clean(value: Any, *, key: str = "") -> Any:
    """Bound and redact a value before it is written to disk."""
    if key and _sensitive(key):
        return "***"
    if isinstance(value, str):
        return value if len(value) <= MAX_FIELD_CHARS else value[: MAX_FIELD_CHARS - 1] + "…"
    if isinstance(value, dict):
        cleaned: dict[str, Any] = {}
        for index, (child_key, child_value) in enumerate(value.items()):
            if index >= MAX_ITEMS:
                cleaned["…"] = f"{len(value) - MAX_ITEMS} more"
                break
            cleaned[str(child_key)] = _clean(child_value, key=str(child_key))
        return cleaned
    if isinstance(value, (list, tuple, set)):
        items = list(value)
        cleaned_items = [_clean(item) for item in items[:MAX_ITEMS]]
        if len(items) > MAX_ITEMS:
            cleaned_items.append(f"… {len(items) - MAX_ITEMS} more")
        return cleaned_items
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    return _clean(str(value), key=key)


class AuditLedger:
    """Thread-safe append-only JSONL ledger with size rotation."""

    def __init__(
        self,
        path: Path | str | None,
        *,
        enabled: bool = True,
        rotate_bytes: int = ROTATE_BYTES,
    ) -> None:
        self.path = Path(path) if path is not None else None
        self.enabled = bool(enabled and self.path is not None)
        self.rotate_bytes = rotate_bytes
        self._lock = threading.Lock()

    def record(self, event: str, **fields: Any) -> None:
        """Append one record. Never raises for I/O or serialization problems."""
        if not self.enabled or self.path is None:
            return
        payload: dict[str, Any] = {
            "ts": datetime.now(UTC).isoformat(timespec="milliseconds"),
            "event": event,
        }
        for key, value in fields.items():
            payload[key] = _clean(value, key=key)
        try:
            line = json.dumps(payload, ensure_ascii=False, default=str)
        except (TypeError, ValueError):
            return
        try:
            with self._lock:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                self._rotate_if_needed()
                with self.path.open("a", encoding="utf-8") as handle:
                    handle.write(line + "\n")
        except OSError:
            # Auditing must never take down a task. The rotating log still has
            # the structured event for diagnostics.
            return

    def read_recent(self, limit: int = 20) -> list[dict[str, Any]]:
        """Return the newest `limit` parseable records, oldest first."""
        if not self.enabled or self.path is None or not self.path.exists():
            return []
        try:
            lines = self.path.read_text(encoding="utf-8").splitlines()
        except OSError:
            return []
        records: list[dict[str, Any]] = []
        for line in lines[-max(limit, 0) :]:
            try:
                record = json.loads(line)
            except (TypeError, ValueError):
                continue
            if isinstance(record, dict):
                records.append(record)
        return records

    def _rotate_if_needed(self) -> None:
        assert self.path is not None
        try:
            if self.path.stat().st_size < self.rotate_bytes:
                return
        except OSError:
            return
        for index in range(ROTATE_KEEP, 0, -1):
            source = (
                self.path if index == 1 else self.path.with_name(f"{self.path.name}.{index - 1}")
            )
            target = self.path.with_name(f"{self.path.name}.{index}")
            if source.exists():
                try:
                    source.replace(target)
                except OSError:
                    return
