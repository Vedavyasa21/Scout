"""Emergency stop plumbing and secure approval defaults."""

from __future__ import annotations

import asyncio
import os
import threading
import time
from collections.abc import AsyncIterator, Sequence
from types import SimpleNamespace
from typing import Any

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import QApplication

from scout.agent import Agent, AgentCancelled
from scout.cli import streaming
from scout.config import Settings
from scout.control import EMERGENCY_STOP, StopController
from scout.desktop import overlay as overlay_module
from scout.desktop.overlay import OverlayApprover, ScoutOverlayWindow
from scout.providers import ModelProvider, ProviderChunk, ProviderReply, Turn
from scout.security.permissions import PermissionLevel
from scout.security.policy import ApprovalDecision, ApprovalRequest
from scout.tools import build_registry


class Token:
    def __init__(self) -> None:
        self.cancelled = False

    def cancel(self) -> None:
        self.cancelled = True


class EndlessProvider(ModelProvider):
    """Streams empty chunks forever so the agent keeps checking its token."""

    name = "endless"

    def __init__(self) -> None:
        super().__init__(model="fake-model")

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
        while True:
            await asyncio.sleep(0.01)
            yield ProviderChunk(text="")


@pytest.fixture(scope="module")
def qt_app():
    app = QApplication.instance() or QApplication([])
    yield app


def _request() -> ApprovalRequest:
    return ApprovalRequest(
        tool="desktop_type_text",
        permission=PermissionLevel.HIGH_RISK,
        arguments={"text": "hello"},
        description="Type into the focused window.",
    )


def test_full_access_is_off_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SCOUT_FULL_ACCESS", raising=False)
    assert Settings(api_key="k").full_access is False


def test_stop_controller_cancels_registered_tokens() -> None:
    controller = StopController()
    first, second = Token(), Token()
    controller.register(first)
    controller.register(second)

    assert controller.active == 2
    assert controller.stop_all() == 2
    assert first.cancelled and second.cancelled

    controller.unregister(first)
    controller.unregister(second)
    assert controller.active == 0
    assert controller.stop_all() == 0


def test_cli_keyboard_interrupt_stops_all(monkeypatch: pytest.MonkeyPatch) -> None:
    stops: list[bool] = []

    class AgentStub:
        async def run_async(self, *_args, **_kwargs):
            raise AssertionError("the fake asyncio.run must not execute it")

    def fake_run(coro):
        coro.close()
        raise KeyboardInterrupt

    monkeypatch.setattr(
        streaming, "EMERGENCY_STOP", SimpleNamespace(stop_all=lambda: stops.append(True))
    )
    monkeypatch.setattr(streaming.asyncio, "run", fake_run)

    with pytest.raises(KeyboardInterrupt):
        streaming.run_streaming(AgentStub(), "hello")

    assert stops == [True]


def test_global_stop_cancels_a_running_agent(settings: Settings) -> None:
    agent = Agent(
        provider=EndlessProvider(),
        registry=build_registry(),
        settings=settings,
    )

    async def scenario() -> None:
        task = asyncio.create_task(agent.run_async("loop forever"))
        await asyncio.sleep(0.1)
        assert EMERGENCY_STOP.active == 1

        assert EMERGENCY_STOP.stop_all() == 1
        with pytest.raises(AgentCancelled):
            await task
        assert EMERGENCY_STOP.active == 0

    asyncio.run(scenario())


def test_overlay_stop_cancels_tokens_and_pending_approval(
    qt_app, monkeypatch: pytest.MonkeyPatch
) -> None:
    stops: list[bool] = []
    pending: list[bool] = []
    monkeypatch.setattr(
        overlay_module,
        "EMERGENCY_STOP",
        SimpleNamespace(stop_all=lambda: stops.append(True)),
    )
    stub = SimpleNamespace(
        _stop_epoch=0,
        approver=SimpleNamespace(cancel_pending=lambda: pending.append(True)),
        worker=SimpleNamespace(isRunning=lambda: False),
        _set_state=lambda *_args: None,
        _settle_timer=SimpleNamespace(start=lambda *_args: None),
        wake_listener=None,
    )

    ScoutOverlayWindow.emergency_stop(stub)

    assert stops == [True]
    assert pending == [True]
    assert stub._stop_epoch == 1


def test_stop_discards_voice_started_before_it(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(overlay_module, "record_wav", lambda **_kwargs: object())
    stub = SimpleNamespace(
        _stop_epoch=2,
        settings=SimpleNamespace(voice_max_seconds=15, voice_silence_seconds=1.0),
    )

    # Epoch 1 was captured before Stop; the clip must not be transcribed or run.
    ScoutOverlayWindow._voice_record_worker(stub, 1)


def test_overlay_approver_denies_without_an_answer() -> None:
    approver = OverlayApprover(timeout=0.02)
    assert approver.approve(_request()) is ApprovalDecision.DENY


def test_cancel_pending_marks_the_request_answered() -> None:
    approver = OverlayApprover(timeout=0.02)
    assert approver.answered is False
    approver.cancel_pending()
    assert approver.answered is True


def test_overlay_approver_resolves_from_a_gui_decision(qt_app) -> None:
    approver = OverlayApprover(timeout=2.0)
    prompts: list[ApprovalRequest] = []

    def show(request: ApprovalRequest) -> None:
        prompts.append(request)
        QTimer.singleShot(10, lambda: approver.resolve(ApprovalDecision.ALLOW_ONCE))

    approver.requested.connect(show, Qt.ConnectionType.QueuedConnection)

    result: dict[str, ApprovalDecision] = {}
    worker = threading.Thread(target=lambda: result.update(decision=approver.approve(_request())))
    worker.start()
    deadline = time.monotonic() + 2.0
    while worker.is_alive() and time.monotonic() < deadline:
        qt_app.processEvents()
        time.sleep(0.005)
    worker.join(timeout=1.0)

    assert not worker.is_alive()
    assert result["decision"] is ApprovalDecision.ALLOW_ONCE
    assert len(prompts) == 1


def test_cancel_pending_denies_a_waiting_call(qt_app) -> None:
    approver = OverlayApprover(timeout=2.0)
    approver.requested.connect(
        lambda _request: QTimer.singleShot(10, approver.cancel_pending),
        Qt.ConnectionType.QueuedConnection,
    )

    result: dict[str, ApprovalDecision] = {}
    worker = threading.Thread(target=lambda: result.update(decision=approver.approve(_request())))
    worker.start()
    deadline = time.monotonic() + 2.0
    while worker.is_alive() and time.monotonic() < deadline:
        qt_app.processEvents()
        time.sleep(0.005)
    worker.join(timeout=1.0)

    assert result["decision"] is ApprovalDecision.DENY
