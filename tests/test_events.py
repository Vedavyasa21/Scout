"""Event bus: listeners, failure isolation and async queue delivery."""

from __future__ import annotations

import asyncio

from scout.events import Event, EventBus, EventType


def test_emit_delivers_in_order_and_carries_task_id() -> None:
    bus = EventBus()
    seen: list[tuple[str, Event]] = []
    bus.subscribe(lambda event: seen.append(("a", event)))
    bus.subscribe(lambda event: seen.append(("b", event)))

    event = bus.emit(EventType.MODEL_CHUNK, "task-1", text="hi")

    assert [name for name, _ in seen] == ["a", "b"]
    assert event.task_id == "task-1"
    assert event.type is EventType.MODEL_CHUNK
    assert event.get("text") == "hi"


def test_unsubscribe_stops_delivery() -> None:
    bus = EventBus()
    seen: list[Event] = []
    unsubscribe = bus.subscribe(seen.append)

    unsubscribe()
    bus.emit(EventType.AGENT_SUCCESS)

    assert seen == []
    assert bus.listener_count == 0


def test_broken_listener_does_not_block_the_rest() -> None:
    bus = EventBus()
    seen: list[Event] = []

    def broken(_event: Event) -> None:
        raise RuntimeError("listener failed")

    bus.subscribe(broken)
    bus.subscribe(seen.append)

    bus.emit(EventType.AGENT_STARTED)

    assert len(seen) == 1


def test_async_queue_receives_events() -> None:
    bus = EventBus()

    async def scenario() -> Event:
        queue = bus.open_queue()
        bus.emit(EventType.AGENT_THINKING, "t", step=2)
        event = await queue.get()
        bus.close_queue(queue)
        return event

    event = asyncio.run(scenario())

    assert event.type is EventType.AGENT_THINKING
    assert event.get("step") == 2
