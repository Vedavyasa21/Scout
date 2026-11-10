"""Structured agent events and an in-process event bus.

Consumers (CLI, overlay, voice, future dashboards) observe a run through these
events instead of reaching into the agent. Publishing is synchronous and
non-blocking: callbacks must be fast, and async consumers pull from a queue.

The bus is deliberately dependency-free so the agent kernel does not import the
UI, the provider SDK or any framework.
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from scout.logging import get_logger

logger = get_logger("events")


class EventType(StrEnum):
    """Lifecycle events emitted by the agent for one user request."""

    AGENT_STARTED = "agent_started"
    AGENT_THINKING = "agent_thinking"
    MODEL_STARTED = "model_started"
    MODEL_CHUNK = "model_chunk"
    MODEL_COMPLETED = "model_completed"
    TOOL_STARTED = "tool_started"
    TOOL_COMPLETED = "tool_completed"
    APPROVAL_REQUIRED = "approval_required"
    PLAN_UPDATED = "plan_updated"
    VERIFICATION_STARTED = "verification_started"
    VERIFICATION_COMPLETED = "verification_completed"
    AGENT_SUCCESS = "agent_success"
    AGENT_ERROR = "agent_error"
    AGENT_CANCELLED = "agent_cancelled"
    AGENT_LIMIT = "agent_limit"


@dataclass(frozen=True)
class Event:
    """One immutable event. `data` stays in-process and is never logged wholesale."""

    type: EventType
    task_id: str = ""
    data: Mapping[str, Any] = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)

    def get(self, key: str, default: Any = None) -> Any:
        return self.data.get(key, default)


Listener = Callable[[Event], None]


class EventBus:
    """Fan-out bus with synchronous listeners and optional async queues."""

    def __init__(self) -> None:
        self._listeners: list[Listener] = []
        self._queues: list[asyncio.Queue[Event]] = []

    @property
    def listener_count(self) -> int:
        return len(self._listeners)

    def subscribe(self, listener: Listener) -> Callable[[], None]:
        """Register `listener`; the returned callable unsubscribes it."""
        self._listeners.append(listener)

        def unsubscribe() -> None:
            with contextlib.suppress(ValueError):
                self._listeners.remove(listener)

        return unsubscribe

    def open_queue(self) -> asyncio.Queue[Event]:
        """Create a queue for an async consumer (UI task, dashboard, tests)."""
        queue: asyncio.Queue[Event] = asyncio.Queue()
        self._queues.append(queue)
        return queue

    def close_queue(self, queue: asyncio.Queue[Event]) -> None:
        with contextlib.suppress(ValueError):
            self._queues.remove(queue)

    def publish(self, event: Event) -> None:
        """Deliver to every consumer. A broken listener cannot break a run."""
        for listener in list(self._listeners):
            try:
                listener(event)
            except Exception:
                logger.exception("event listener failed for %s", event.type)
        for queue in list(self._queues):
            with contextlib.suppress(asyncio.QueueFull):
                queue.put_nowait(event)

    def emit(self, event_type: EventType, task_id: str = "", **data: Any) -> Event:
        """Build and publish an event in one call."""
        event = Event(event_type, task_id=task_id, data=data)
        self.publish(event)
        return event
