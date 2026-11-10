"""Process-wide emergency stop for cooperative agent cancellation.

Every active run registers its cancellation token here. A UI button, tray action,
dialog button or signal handler can call `EMERGENCY_STOP.stop_all()` without
holding a reference to the running agent. Cancellation is still cooperative: a
tool that is already executing cannot be killed, but no further model or tool
work starts.
"""

from __future__ import annotations

import threading
from typing import Protocol


class Stoppable(Protocol):
    """Minimal interface a registered token must provide."""

    def cancel(self) -> None: ...


class StopController:
    """Tracks active cancellation tokens and stops them together."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._tokens: set[Stoppable] = set()

    @property
    def active(self) -> int:
        with self._lock:
            return len(self._tokens)

    def register(self, token: Stoppable) -> None:
        with self._lock:
            self._tokens.add(token)

    def unregister(self, token: Stoppable) -> None:
        with self._lock:
            self._tokens.discard(token)

    def stop_all(self) -> int:
        """Cancel every active run. Returns how many tokens were stopped."""
        with self._lock:
            tokens = list(self._tokens)
        for token in tokens:
            token.cancel()
        return len(tokens)


EMERGENCY_STOP = StopController()
