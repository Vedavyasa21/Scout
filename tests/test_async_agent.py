"""Async agent loop: streaming assembly, structured events and cancellation."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Sequence
from typing import Any

import pytest

from scout.agent import Agent, AgentCancelled, CancellationToken
from scout.events import Event, EventBus, EventType
from scout.providers import (
    ModelProvider,
    ProviderChunk,
    ProviderError,
    ProviderReply,
    ToolCall,
    Turn,
)
from scout.tools import build_registry


class StreamProvider(ModelProvider):
    """Replays scripted streams; never falls back to `complete()`."""

    name = "stream-fake"

    def __init__(self, scripts: list[list[ProviderChunk]]) -> None:
        super().__init__(model="fake-model")
        self.scripts = scripts

    def complete(
        self,
        *,
        system: str,
        turns: Sequence[Turn],
        tools: Sequence[dict[str, Any]],
    ) -> ProviderReply:
        raise AssertionError("the agent should consume the stream")

    async def stream(
        self,
        *,
        system: str,
        turns: Sequence[Turn],
        tools: Sequence[dict[str, Any]],
    ) -> AsyncIterator[ProviderChunk]:
        if not self.scripts:
            raise ProviderError("no scripted stream left")
        for chunk in self.scripts.pop(0):
            yield chunk


def _agent(settings, scripts: list[list[ProviderChunk]]) -> Agent:
    return Agent(
        provider=StreamProvider(scripts),
        registry=build_registry(),
        settings=settings,
    )


def _bus() -> tuple[EventBus, list[Event]]:
    events: list[Event] = []
    bus = EventBus()
    bus.subscribe(events.append)
    return bus, events


def test_text_stream_is_assembled_and_emitted(settings) -> None:
    bus, events = _bus()
    agent = _agent(settings, [[ProviderChunk(text="Hello "), ProviderChunk(text="there")]])

    result = asyncio.run(agent.run_async("hi", bus=bus))

    assert result.completed
    assert result.text == "Hello there"
    assert [event.type for event in events] == [
        EventType.AGENT_STARTED,
        EventType.AGENT_THINKING,
        EventType.MODEL_STARTED,
        EventType.MODEL_CHUNK,
        EventType.MODEL_CHUNK,
        EventType.MODEL_COMPLETED,
        EventType.AGENT_SUCCESS,
    ]
    deltas = [e.get("text") for e in events if e.type is EventType.MODEL_CHUNK]
    assert deltas == ["Hello ", "there"]
    assert all(event.task_id == result.task_id for event in events)


def test_streamed_tool_call_runs_and_is_reported(settings) -> None:
    bus, events = _bus()
    agent = _agent(
        settings,
        [
            [
                ProviderChunk(
                    text="Checking. ",
                    tool_calls=[ToolCall(name="get_os_info", arguments={}, call_id="c1")],
                )
            ],
            [ProviderChunk(text="You are on this machine.")],
        ],
    )

    result = asyncio.run(agent.run_async("which os?", bus=bus))

    assert result.completed
    assert len(result.steps) == 1
    assert result.steps[0].ok
    types = [event.type for event in events]
    assert types.index(EventType.TOOL_STARTED) < types.index(EventType.TOOL_COMPLETED)
    completed = next(e for e in events if e.type is EventType.TOOL_COMPLETED)
    assert completed.get("tool") == "get_os_info"
    assert completed.get("ok") is True


def test_privileged_tool_emits_approval_event(settings) -> None:
    bus, events = _bus()
    agent = _agent(
        settings,
        [
            [ProviderChunk(tool_calls=[ToolCall(name="type_text", arguments={"text": "hi"})])],
            [ProviderChunk(text="I need permission for that.")],
        ],
    )

    result = asyncio.run(agent.run_async("type hi", bus=bus))

    approval = next(e for e in events if e.type is EventType.APPROVAL_REQUIRED)
    assert approval.get("tool") == "type_text"
    assert approval.get("permission") == "execute"
    assert not result.steps[0].ok
    assert "confirmation" in (result.steps[0].error or "").lower()


def test_provider_error_emits_error_event(settings) -> None:
    bus, events = _bus()
    agent = _agent(settings, [])

    with pytest.raises(ProviderError):
        asyncio.run(agent.run_async("hi", bus=bus))

    assert events[-1].type is EventType.AGENT_ERROR
    assert "no scripted stream" in (events[-1].get("error") or "")


def test_cancelled_token_stops_before_the_model_call(settings) -> None:
    bus, events = _bus()
    token = CancellationToken()
    token.cancel()
    agent = _agent(settings, [[ProviderChunk(text="never")]])

    with pytest.raises(AgentCancelled):
        asyncio.run(agent.run_async("hi", cancel=token, bus=bus))

    assert [event.type for event in events] == [
        EventType.AGENT_STARTED,
        EventType.AGENT_CANCELLED,
    ]


class CancellingProvider(StreamProvider):
    def __init__(self, token: CancellationToken) -> None:
        super().__init__([])
        self.token = token

    async def stream(
        self,
        *,
        system: str,
        turns: Sequence[Turn],
        tools: Sequence[dict[str, Any]],
    ) -> AsyncIterator[ProviderChunk]:
        yield ProviderChunk(text="partial ")
        self.token.cancel()
        yield ProviderChunk(text="never")


def test_cancellation_between_chunks_stops_the_stream(settings) -> None:
    bus, events = _bus()
    token = CancellationToken()
    agent = Agent(
        provider=CancellingProvider(token),
        registry=build_registry(),
        settings=settings,
    )

    with pytest.raises(AgentCancelled):
        asyncio.run(agent.run_async("hi", cancel=token, bus=bus))

    deltas = [e.get("text") for e in events if e.type is EventType.MODEL_CHUNK]
    assert deltas == ["partial "]
    assert events[-1].type is EventType.AGENT_CANCELLED


def test_step_limit_emits_limit_event(settings) -> None:
    settings.max_tool_steps = 1
    bus, events = _bus()
    agent = _agent(
        settings,
        [[ProviderChunk(tool_calls=[ToolCall(name="get_os_info", arguments={})])]],
    )

    result = asyncio.run(agent.run_async("loop", bus=bus))

    assert not result.completed
    assert result.limit_reached
    assert events[-1].type is EventType.AGENT_LIMIT


def test_sync_run_facade_still_works(settings) -> None:
    agent = _agent(settings, [[ProviderChunk(text="ok")]])

    assert agent.run("hi").text == "ok"


def test_sync_run_rejects_a_running_event_loop(settings) -> None:
    agent = _agent(settings, [[ProviderChunk(text="ok")]])

    async def scenario() -> None:
        with pytest.raises(RuntimeError, match="running event loop"):
            agent.run("hi")

    asyncio.run(scenario())
