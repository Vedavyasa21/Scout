"""Approval policy: auto-allow reads, confirm everything that changes state."""

from __future__ import annotations

import pytest

from scout.security.policy import ApprovalRequest, PolicyEngine
from scout.tools import PermissionLevel


class FakeTool:
    def __init__(self, name: str, permission: PermissionLevel) -> None:
        self.name = name
        self.permission = permission


@pytest.mark.parametrize("level", [PermissionLevel.READ, PermissionLevel.LOW_RISK])
def test_read_and_low_risk_are_automatic(level: PermissionLevel) -> None:
    assert PolicyEngine().requires_approval(FakeTool("x", level)) is False


@pytest.mark.parametrize(
    "level", [PermissionLevel.WRITE, PermissionLevel.EXECUTE, PermissionLevel.HIGH_RISK]
)
def test_state_changing_tools_need_approval(level: PermissionLevel) -> None:
    assert PolicyEngine().requires_approval(FakeTool("x", level)) is True


def test_session_allow_is_remembered() -> None:
    engine = PolicyEngine()
    tool = FakeTool("type_text", PermissionLevel.EXECUTE)
    assert engine.requires_approval(tool)
    engine.remember("type_text")
    assert engine.requires_approval(tool) is False
    assert engine.session_allowed() == frozenset({"type_text"})


def test_request_carries_context() -> None:
    request = ApprovalRequest(
        tool="type_text",
        permission=PermissionLevel.EXECUTE,
        arguments={"text": "hello"},
        description="type",
    )
    assert request.is_high_risk is False
    assert request.arguments["text"] == "hello"
