"""Streaming terminal renderer and overlay worker regressions."""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from types import SimpleNamespace

import pytest
from PyQt6.QtWidgets import QApplication
from rich.console import Console

from scout.agent import Agent, AgentCancelled
from scout.cli.streaming import StreamRenderer
from scout.desktop.overlay import AgentWorker, ScoutOverlayWindow
from scout.events import Event, EventType
from scout.providers import AssistantTurn, ModelProvider, ProviderReply, ToolCall, UserTurn
from scout.tools import build_registry


def test_stream_renderer_writes_deltas_live() -> None:
    console = Console(record=True, force_terminal=True, width=80)
    renderer = StreamRenderer(console, enabled=True)

    renderer.on_event(Event(EventType.MODEL_CHUNK, data={"text": "Hel"}))
    renderer.on_event(Event(EventType.MODEL_CHUNK, data={"text": "lo"}))
    renderer.on_event(Event(EventType.MODEL_COMPLETED, data={"text": "Hello"}))
    output = console.export_text()

    assert "scout" in output
    assert "Hello" in output
    assert renderer.streamed


def test_stream_renderer_is_silent_when_disabled() -> None:
    console = Console(record=True, force_terminal=False, width=80)
    renderer = StreamRenderer(console, enabled=False)

    renderer.on_event(Event(EventType.MODEL_CHUNK, data={"text": "hidden"}))
    renderer.on_event(Event(EventType.MODEL_COMPLETED, data={"text": "hidden"}))

    assert console.export_text() == ""
    assert not renderer.streamed


class FakeProvider(ModelProvider):
    name = "fake"

    def __init__(self, replies: list[ProviderReply]) -> None:
        super().__init__(model="fake-model", api_key="unused")
        self._replies = list(replies)

    def complete(self, *, system, turns, tools) -> ProviderReply:
        return self._replies.pop(0)


@pytest.fixture(scope="module")
def qt_app():
    app = QApplication.instance() or QApplication([])
    yield app


def test_agent_worker_reports_tool_names_not_a_count(qt_app, settings) -> None:
    provider = FakeProvider(
        [
            ProviderReply(
                text=None,
                tool_calls=[ToolCall(name="get_os_info", arguments={}, call_id="c1")],
            ),
            ProviderReply(text="done"),
        ]
    )
    agent = Agent(provider=provider, registry=build_registry(), settings=settings)
    worker = AgentWorker(agent)
    worker.set_request("check os", [])

    finished: list[tuple] = []
    errors: list[tuple] = []
    worker.sig_finished.connect(lambda *args: finished.append(args))
    worker.sig_error.connect(lambda *args: errors.append(args))
    worker.run()

    assert errors == []
    assert len(finished) == 1
    title, _text, _duration, tools = finished[0]
    assert "get_os_info" in title
    assert tools == ["get_os_info"]


def test_agent_worker_reports_cancellation_separately(qt_app) -> None:
    class CancelledAgent:
        def run(self, *_args, **_kwargs):
            raise AgentCancelled("The task was cancelled.")

    worker = AgentWorker(CancelledAgent())
    cancelled: list[str] = []
    errors: list[tuple] = []
    worker.sig_cancelled.connect(cancelled.append)
    worker.sig_error.connect(lambda *args: errors.append(args))

    worker.run()

    assert cancelled and "stopped" in cancelled[0].lower()
    assert errors == []


def test_overlay_forwards_model_chunks_to_the_hud(qt_app, settings) -> None:
    window = ScoutOverlayWindow(
        settings, enable_wake_word=False, enable_hotkey=False, enable_companion=False
    )
    window._stream_buffer = ""

    window.sig_agent_chunk.emit("Hello ")
    window.sig_agent_chunk.emit("world")
    qt_app.processEvents()

    assert window._stream_buffer == "Hello world"
    assert "Hello world" in window.hud_subtitle.text()
    window.close()


def test_completion_handler_records_a_real_assistant_turn(qt_app) -> None:
    summary_calls: list[tuple] = []
    stub = SimpleNamespace(
        history=[],
        worker=SimpleNamespace(request_text="hello"),
        wake_listener=None,
        _set_state=lambda *_args: None,
        _settle_timer=SimpleNamespace(start=lambda *_args: None),
        geometry=lambda: None,
        summary_win=SimpleNamespace(
            show_summary=lambda *args, **kwargs: summary_calls.append((args, kwargs))
        ),
    )

    ScoutOverlayWindow._on_worker_finished(stub, "title", "answer", 0.2, [])

    assert len(stub.history) == 2
    assert isinstance(stub.history[0], UserTurn)
    assert isinstance(stub.history[1], AssistantTurn)
    assert stub.history[1].text == "answer"
    assert summary_calls
