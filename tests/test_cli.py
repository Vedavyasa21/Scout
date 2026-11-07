"""CLI wiring with a fake provider (no network)."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

import scout.cli.app as cli
from scout.cli.approval import RichApprover
from scout.providers import ModelProvider, ProviderReply, ToolCall, Turn
from scout.security.policy import ApprovalDecision, ApprovalRequest
from scout.tools import PermissionLevel

runner = CliRunner()


class FakeProvider(ModelProvider):
    name = "fake"

    def __init__(self, replies: list[ProviderReply]) -> None:
        super().__init__(model="fake-model", api_key="unused")
        self._replies = list(replies)

    def complete(
        self, *, system: str, turns: Sequence[Turn], tools: Sequence[dict[str, Any]]
    ) -> ProviderReply:
        return self._replies.pop(0) if self._replies else ProviderReply(text="done")


@pytest.fixture
def patched(monkeypatch, settings):
    monkeypatch.setattr(cli, "load_settings", lambda: settings)
    monkeypatch.setattr(cli, "configure_logging", lambda _settings: Path("unused.log"))
    return settings


def test_version() -> None:
    result = runner.invoke(cli.app, ["--version"])
    assert result.exit_code == 0
    assert "scout v" in result.output


def test_tools_lists_registry(patched) -> None:
    result = runner.invoke(cli.app, ["tools"])
    assert result.exit_code == 0
    assert "get_os_info" in result.output
    assert "launch_application" in result.output


def test_status_masks_key(patched) -> None:
    result = runner.invoke(cli.app, ["status"])
    assert result.exit_code == 0
    assert "configured" in result.output
    assert patched.api_key not in result.output


def test_config_masks_key(patched) -> None:
    result = runner.invoke(cli.app, ["config"])
    assert result.exit_code == 0
    assert "test-key-not-real" not in result.output


def test_ask_renders_answer(patched, monkeypatch) -> None:
    monkeypatch.setattr(
        cli, "create_provider", lambda _s: FakeProvider([ProviderReply(text="Hello from Scout")])
    )
    result = runner.invoke(cli.app, ["ask", "hi"])
    assert result.exit_code == 0
    assert "Hello from Scout" in result.output


def test_non_interactive_approver_denies() -> None:
    approver = RichApprover(cli.console, interactive=False)
    request = ApprovalRequest(
        tool="type_text",
        permission=PermissionLevel.EXECUTE,
        arguments={"text": "hello"},
        description="type text",
    )
    assert approver.approve(request) is ApprovalDecision.DENY


def test_desktop_action_is_denied_without_a_terminal(patched, monkeypatch) -> None:
    replies = [
        ProviderReply(text=None, tool_calls=[ToolCall(name="lock_screen", arguments={})]),
        ProviderReply(text="I could not lock the screen."),
    ]
    monkeypatch.setattr(cli, "create_provider", lambda _s: FakeProvider(replies))
    result = runner.invoke(cli.app, ["ask", "lock my screen"])
    assert result.exit_code == 0
    assert "no interactive terminal" in result.output
    assert "denied" in result.output.lower()


def test_ask_runs_tools(patched, monkeypatch) -> None:
    replies = [
        ProviderReply(text=None, tool_calls=[ToolCall(name="get_os_info", arguments={})]),
        ProviderReply(text="System inspected"),
    ]
    monkeypatch.setattr(cli, "create_provider", lambda _s: FakeProvider(replies))
    result = runner.invoke(cli.app, ["ask", "check system"])
    assert result.exit_code == 0
    assert "get_os_info" in result.output
    assert "System inspected" in result.output


def test_ask_missing_api_key_exits_1(patched, monkeypatch) -> None:
    patched.api_key = ""
    result = runner.invoke(cli.app, ["ask", "hi"])
    assert result.exit_code == 1
    assert "API key" in result.output


def test_ask_limit_exits_1(patched, monkeypatch) -> None:
    patched.max_tool_steps = 1
    replies = [ProviderReply(text=None, tool_calls=[ToolCall(name="get_os_info", arguments={})])]
    monkeypatch.setattr(cli, "create_provider", lambda _s: FakeProvider(replies))
    result = runner.invoke(cli.app, ["ask", "loop"])
    assert result.exit_code == 1
    assert "Stopped after 1 steps" in result.output


def test_interactive_exit(patched, monkeypatch) -> None:
    monkeypatch.setattr(cli, "create_provider", lambda _s: FakeProvider([]))
    result = runner.invoke(cli.app, [], input="/exit\n")
    assert result.exit_code == 0
    assert "Scout" in result.output


def test_interactive_conversation(patched, monkeypatch) -> None:
    monkeypatch.setattr(
        cli, "create_provider", lambda _s: FakeProvider([ProviderReply(text="hi there")])
    )
    result = runner.invoke(cli.app, [], input="hello\n/exit\n")
    assert result.exit_code == 0
    assert "hi there" in result.output


def test_interactive_help_and_unknown(patched, monkeypatch) -> None:
    monkeypatch.setattr(cli, "create_provider", lambda _s: FakeProvider([]))
    result = runner.invoke(cli.app, [], input="/help\n/nope\n/exit\n")
    assert result.exit_code == 0
    assert "unknown command" in result.output


def test_doctor_missing_key_exits_1(patched) -> None:
    patched.api_key = ""
    result = runner.invoke(cli.app, ["doctor"])
    assert result.exit_code == 1
    assert "failed" in result.output


def test_doctor_with_key_passes(patched) -> None:
    result = runner.invoke(cli.app, ["doctor"])
    assert "scout doctor" in result.output
