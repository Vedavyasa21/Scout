"""Phase 2: permission rules, audit ledger, planning/verification and privacy."""

from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator, Sequence
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from PIL import Image

from scout import perception
from scout.agent import Agent
from scout.agent.plan import PlanStepStatus, PlanTracker
from scout.config import Settings
from scout.events import EventBus, EventType
from scout.providers import (
    ModelProvider,
    ProviderChunk,
    ProviderReply,
    ToolCall,
    Turn,
)
from scout.security.audit import AuditLedger
from scout.security.egress import EgressDeniedError, EgressPolicy
from scout.security.policy import ApprovalDecision, PermissionDecision, PolicyEngine
from scout.tools import PermissionLevel, Tool, ToolContext, ToolRegistry, build_registry, desktop
from scout.voice.wake import WakeWordListener


class FakeTool:
    def __init__(self, name: str, permission: PermissionLevel) -> None:
        self.name = name
        self.permission = permission


def test_policy_hard_deny_beats_full_access() -> None:
    engine = PolicyEngine(full_access=True, deny=["lock_screen"])
    tool = FakeTool("lock_screen", PermissionLevel.HIGH_RISK)
    assert engine.evaluate(tool) is PermissionDecision.DENY
    assert (
        engine.evaluate(FakeTool("type_text", PermissionLevel.EXECUTE)) is PermissionDecision.AUTO
    )


def test_policy_auto_approve_skips_the_prompt() -> None:
    engine = PolicyEngine(auto_approve=["type_text"])
    assert (
        engine.evaluate(FakeTool("type_text", PermissionLevel.EXECUTE)) is PermissionDecision.AUTO
    )
    assert engine.evaluate(FakeTool("click", PermissionLevel.EXECUTE)) is PermissionDecision.ASK


def test_registry_blocked_tool_never_executes(settings: Settings) -> None:
    called: list[bool] = []
    registry = ToolRegistry(PolicyEngine(deny=["echo"]))
    registry.register(
        Tool(
            name="echo",
            description="echo",
            parameters={"type": "object", "properties": {}, "additionalProperties": False},
            permission=PermissionLevel.READ,
            handler=lambda _args, _ctx: called.append(True),  # type: ignore[arg-type,return-value]
        )
    )
    context = ToolContext.from_settings(settings.model_copy(update={"full_access": True}))
    result = registry.execute("echo", {}, context)
    assert not result.ok
    assert "blocked" in (result.error or "").lower()
    assert called == []


def test_auto_approved_tool_runs_without_an_approver(settings: Settings) -> None:
    registry = ToolRegistry(PolicyEngine(auto_approve=["echo"]))
    registry.register(
        Tool(
            name="echo",
            description="echo",
            parameters={"type": "object", "properties": {}, "additionalProperties": False},
            permission=PermissionLevel.EXECUTE,
            handler=lambda _args, _ctx: SimpleNamespace(
                ok=True, data="ran", error=None, duration_ms=0
            ),
        )
    )
    context = ToolContext.from_settings(settings)
    unsaved = settings.model_copy(update={"full_access": False})
    context = ToolContext.from_settings(unsaved)
    result = registry.execute("echo", {}, context)
    assert result.ok
    assert result.data == "ran"


def test_audit_ledger_redacts_and_truncates(tmp_path: Path) -> None:
    ledger = AuditLedger(tmp_path / "audit.jsonl")
    ledger.record(
        "approval_requested",
        task_id="t1",
        tool="type_text",
        arguments={"text": "hello", "password": "hunter2", "api_key": "secret"},
        error="x" * 2000,
    )
    record = ledger.read_recent(1)[0]
    assert record["arguments"]["text"] == "hello"
    assert record["arguments"]["password"] == "***"
    assert record["arguments"]["api_key"] == "***"
    assert len(record["error"]) < 500


def test_audit_records_tool_execution(settings: Settings) -> None:
    registry = build_registry()
    context = ToolContext.from_settings(settings, audit=AuditLedger(settings.resolved_audit_file))
    result = registry.execute("get_os_info", {}, context)
    assert result.ok
    events = {
        record["event"] for record in AuditLedger(settings.resolved_audit_file).read_recent(10)
    }
    assert "tool_result" in events


def test_plan_tracker_tracks_status_and_verification() -> None:
    tracker = PlanTracker()
    tracker.update(
        "test",
        [
            {"description": "read", "status": "done"},
            {"description": "write", "status": "pending"},
        ],
    )
    assert tracker.plan is not None
    assert tracker.plan.steps[0].status is PlanStepStatus.DONE
    assert tracker.needs_verification()
    assert tracker.begin_verification()
    assert not tracker.begin_verification()
    assert not tracker.needs_verification()


class ScriptProvider(ModelProvider):
    name = "script-fake"

    def __init__(self, scripts: list[list[ProviderChunk]]) -> None:
        super().__init__(model="fake")
        self.scripts = scripts

    def complete(
        self, *, system: str, turns: Sequence[Turn], tools: Sequence[dict[str, Any]]
    ) -> ProviderReply:
        raise AssertionError("the stream should be used")

    async def stream(
        self, *, system: str, turns: Sequence[Turn], tools: Sequence[dict[str, Any]]
    ) -> AsyncIterator[ProviderChunk]:
        for chunk in self.scripts.pop(0):
            yield chunk


def _run(settings: Settings, scripts: list[list[ProviderChunk]]) -> tuple[Any, list]:
    bus = EventBus()
    events: list = []
    bus.subscribe(events.append)
    agent = Agent(provider=ScriptProvider(scripts), registry=build_registry(), settings=settings)
    result = asyncio.run(agent.run_async("do two things", bus=bus))
    return result, events


def test_agent_verifies_an_unfinished_plan(settings: Settings) -> None:
    result, events = _run(
        settings,
        [
            [
                ProviderChunk(
                    tool_calls=[
                        ToolCall(
                            name="update_plan",
                            arguments={
                                "goal": "two things",
                                "steps": [
                                    {"description": "first", "status": "done"},
                                    {"description": "second", "status": "pending"},
                                ],
                            },
                            call_id="p1",
                        )
                    ]
                )
            ],
            [ProviderChunk(text="I am done.")],
            [ProviderChunk(text="Verified: second is still not possible.")],
        ],
    )
    types = [event.type for event in events]
    assert EventType.PLAN_UPDATED in types
    assert EventType.VERIFICATION_STARTED in types
    assert EventType.VERIFICATION_COMPLETED in types
    assert result.completed
    assert result.plan["steps"][1]["status"] == "pending"
    assert len(result.steps) == 1


def test_agent_skips_verification_for_a_finished_plan(settings: Settings) -> None:
    result, events = _run(
        settings,
        [
            [
                ProviderChunk(
                    tool_calls=[
                        ToolCall(
                            name="update_plan",
                            arguments={
                                "steps": [{"description": "only", "status": "done"}],
                            },
                            call_id="p1",
                        )
                    ]
                )
            ],
            [ProviderChunk(text="All complete.")],
        ],
    )
    assert result.completed
    assert EventType.VERIFICATION_STARTED not in [event.type for event in events]


def test_egress_allowlist_blocks_unknown_hosts() -> None:
    policy = EgressPolicy(["example.com"])
    assert policy.allows("https://example.com/page")
    assert policy.allows("https://docs.example.com/page")
    assert not policy.allows("https://evil.example.net/")
    with pytest.raises(EgressDeniedError):
        policy.check("https://evil.example.net/")


def test_screenshot_refuses_sensitive_active_window(
    settings: Settings, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        desktop,
        "active_window_info",
        lambda: {"title": "Bitwarden — Vault"},
    )
    monkeypatch.setattr(Settings, "data_dir", property(lambda _self: tmp_path))
    result = desktop.screenshot({}, ToolContext.from_settings(settings))
    assert not result.ok
    assert "blocked" in (result.error or "").lower()


def test_screenshot_region_crops_and_saves(
    settings: Settings, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    image = Image.new("RGB", (100, 100), "white")
    monkeypatch.setattr(desktop, "active_window_info", lambda: {"title": "Editor"})
    monkeypatch.setattr(desktop, "capture_screen", lambda _patterns: (image, 1))
    monkeypatch.setattr(Settings, "data_dir", property(lambda _self: tmp_path))

    result = desktop.screenshot_region(
        {"x": 10, "y": 20, "width": 30, "height": 40},
        ToolContext.from_settings(settings),
    )

    assert result.ok
    assert result.data["width"] == 30
    assert result.data["height"] == 40
    assert result.data["masked_windows"] == 1
    assert Path(result.data["path"]).exists()
    assert Path(result.data["path"]).name.startswith("region-")


def test_read_screen_text_uses_masked_capture(
    settings: Settings, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    image = Image.new("RGB", (100, 100), "white")
    monkeypatch.setattr(desktop, "active_window_info", lambda: {"title": "Editor"})
    monkeypatch.setattr(desktop, "capture_screen", lambda _patterns: (image, 2))
    monkeypatch.setattr(desktop, "ocr_image", lambda _path: "Hello\nworld")
    monkeypatch.setattr(Settings, "data_dir", property(lambda _self: tmp_path))

    result = desktop.read_screen_text(
        {"x": 0, "y": 0, "width": 50, "height": 50},
        ToolContext.from_settings(settings),
    )

    assert result.ok
    assert result.data["text"] == "Hello\nworld"
    assert result.data["masked_windows"] == 2
    assert Path(result.data["path"]).name.startswith("ocr-")


def test_read_screen_text_rejects_a_partial_region(settings: Settings) -> None:
    result = desktop.read_screen_text({"x": 1}, ToolContext.from_settings(settings))
    assert not result.ok
    assert "together" in (result.error or "").lower()


@pytest.mark.skipif(os.name != "nt", reason="Windows OCR uses PowerShell")
def test_ocr_image_invokes_the_local_engine(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "capture.png"
    Image.new("RGB", (100, 40), "white").save(path)
    seen: dict[str, object] = {}

    def fake_run(command, **kwargs):
        seen["command"] = command
        seen["env"] = kwargs["env"]
        return SimpleNamespace(returncode=0, stdout="Hello\r\n", stderr="")

    monkeypatch.setattr(perception.subprocess, "run", fake_run)

    assert perception.ocr_image(path) == "Hello"
    command = seen["command"]
    assert isinstance(command, list) and "-Command" in command
    assert str(seen["env"]["SCOUT_OCR_PATH"]).endswith(".png")


def test_privacy_masks_cover_sensitive_windows(monkeypatch: pytest.MonkeyPatch) -> None:
    window = SimpleNamespace(
        title="Bitwarden — Vault",
        left=10,
        top=10,
        width=20,
        height=20,
        visible=True,
    )
    monkeypatch.setattr(
        perception, "_windows", lambda: SimpleNamespace(getAllWindows=lambda: [window])
    )
    image = Image.new("RGB", (50, 50), "white")

    count = perception.apply_privacy_masks(image, ["bitwarden"])

    assert count == 1
    assert image.getpixel((15, 15)) == (0, 0, 0)
    assert image.getpixel((45, 45)) == (255, 255, 255)


def test_active_window_reports_process_and_rect(monkeypatch: pytest.MonkeyPatch) -> None:
    window = SimpleNamespace(
        title="Editor",
        left=1,
        top=2,
        width=300,
        height=200,
        isMinimized=False,
    )
    monkeypatch.setattr(
        perception, "_windows", lambda: SimpleNamespace(getActiveWindow=lambda: window)
    )
    monkeypatch.setattr(perception, "_foreground_pid", lambda: 42)
    monkeypatch.setattr(perception, "_process_name", lambda pid: "editor.exe" if pid == 42 else "")

    info = perception.active_window_info()

    assert info["title"] == "Editor"
    assert info["process"] == "editor.exe"
    assert info["rect"] == {"x": 1, "y": 2, "width": 300, "height": 200}


def test_screenshot_retention_prunes_old_and_excess(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    folder = tmp_path / "screenshots"
    folder.mkdir()
    old = folder / "shot-old.png"
    old.write_bytes(b"x")
    old_time = 1_600_000_000
    os.utime(old, (old_time, old_time))
    for index in range(3):
        (folder / f"shot-new-{index}.png").write_bytes(b"x")
    perception.prune_screenshots(folder, keep_days=1, max_files=2)
    remaining = sorted(path.name for path in folder.glob("shot-*.png"))
    assert "shot-old.png" not in remaining
    assert len(remaining) == 2


def test_wake_listener_is_local_only_by_default() -> None:
    calls: list[bool] = []

    class Provider(ModelProvider):
        name = "fake"

        def complete(self, **_kwargs) -> ProviderReply:
            raise AssertionError("cloud transcription must not run")

        def transcribe_audio(self, _data: bytes, *, mime_type: str = "audio/wav") -> str:
            calls.append(True)
            return "hey scout"

    listener = WakeWordListener(lambda: None, provider=Provider(model="fake"), local_only=True)
    assert listener._matches_wake(b"\x00\x00" * 100) is False
    assert calls == []


def test_wake_custom_detector_works_offline() -> None:
    listener = WakeWordListener(
        lambda: None,
        custom_detector=lambda pcm: pcm == b"wake",
        local_only=True,
    )
    assert listener._matches_wake(b"wake") is True
    assert listener._matches_wake(b"nope") is False


def test_save_key_to_explicit_path(tmp_path: Path) -> None:
    from scout.config import save_api_key

    target = tmp_path / ".env"
    result = save_api_key("abc123", path=target)
    assert result == target
    assert "SCOUT_API_KEY=abc123" in target.read_text(encoding="utf-8")


def test_approval_decision_values_are_stable() -> None:
    assert ApprovalDecision.ALLOW_ONCE.value == "allow_once"
