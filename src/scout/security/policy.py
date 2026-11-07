"""Approval policy for tool execution.

Read-only tools run automatically. Low-risk tools (allowlisted app launches,
volume keys) also run automatically. Anything that changes the machine or
injects input requires an explicit approval from the active approver; without
an approver the call is denied. This is the safety boundary the desktop tools
depend on.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Protocol, runtime_checkable

from scout.security.permissions import PermissionLevel


class ApprovalDecision(StrEnum):
    """What the approver decided for a single tool call."""

    ALLOW_ONCE = "allow_once"
    ALLOW_SESSION = "allow_session"
    DENY = "deny"


@dataclass(frozen=True)
class ApprovalRequest:
    """Everything the user needs to judge one tool call."""

    tool: str
    permission: PermissionLevel
    arguments: dict[str, Any]
    description: str

    @property
    def is_high_risk(self) -> bool:
        return self.permission is PermissionLevel.HIGH_RISK


@runtime_checkable
class Approver(Protocol):
    """Interactive confirmation source (CLI prompt, desktop dialog, ...)."""

    def approve(self, request: ApprovalRequest) -> ApprovalDecision:
        """Return the decision for `request`."""
        ...


class DenyAllApprover:
    """Non-interactive default: refuse anything needing confirmation."""

    def approve(self, request: ApprovalRequest) -> ApprovalDecision:
        return ApprovalDecision.DENY


class AllowAllApprover:
    """Explicit opt-in for tests and trusted automation. Never a CLI default."""

    def approve(self, request: ApprovalRequest) -> ApprovalDecision:
        return ApprovalDecision.ALLOW_ONCE


class PolicyEngine:
    """Decides which tool calls need human approval.

    The engine also remembers tools the user approved for the rest of the
    session, so a repeated desktop action does not nag on every step.
    """

    AUTO_ALLOWED = frozenset({PermissionLevel.READ, PermissionLevel.LOW_RISK})

    def __init__(self) -> None:
        self._session_allowed: set[str] = set()

    def requires_approval(self, tool: Any) -> bool:
        permission: PermissionLevel = tool.permission
        if permission in self.AUTO_ALLOWED:
            return False
        return tool.name not in self._session_allowed

    def remember(self, tool_name: str) -> None:
        self._session_allowed.add(tool_name)

    def forget_all(self) -> None:
        self._session_allowed.clear()

    def session_allowed(self) -> frozenset[str]:
        return frozenset(self._session_allowed)
