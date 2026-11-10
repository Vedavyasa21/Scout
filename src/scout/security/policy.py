"""Approval policy for tool execution.

Read-only tools run automatically. Low-risk tools (allowlisted app launches,
volume keys) also run automatically. Anything that changes the machine or
injects input requires an explicit approval from the active approver; without
an approver the call is denied. This is the safety boundary the desktop tools
depend on.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Protocol, runtime_checkable

from scout.security.permissions import PermissionLevel


class ApprovalDecision(StrEnum):
    """What the approver decided for a single tool call."""

    ALLOW_ONCE = "allow_once"
    ALLOW_SESSION = "allow_session"
    DENY = "deny"


class PermissionDecision(StrEnum):
    """The central permission manager's answer before execution."""

    AUTO = "auto"
    ASK = "ask"
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

    def __init__(
        self,
        full_access: bool = False,
        *,
        auto_approve: list[str] | tuple[str, ...] = (),
        deny: list[str] | tuple[str, ...] = (),
    ) -> None:
        self._lock = threading.Lock()
        self.full_access = full_access
        self._auto_approve: set[str] = set(auto_approve)
        self._denied: set[str] = set(deny)
        self._session_allowed: set[str] = set()

    def configure(
        self,
        *,
        full_access: bool | None = None,
        auto_approve: list[str] | tuple[str, ...] | None = None,
        deny: list[str] | tuple[str, ...] | None = None,
    ) -> None:
        """Apply settings to the central policy (thread-safe, idempotent)."""
        with self._lock:
            if full_access is not None:
                self.full_access = full_access
            if auto_approve is not None:
                self._auto_approve = set(auto_approve)
            if deny is not None:
                self._denied = set(deny)

    def evaluate(self, tool: Any, full_access: bool | None = None) -> PermissionDecision:
        """Return AUTO, ASK or a hard DENY. Hard deny beats full access."""
        with self._lock:
            if tool.name in self._denied:
                return PermissionDecision.DENY
            if full_access or (full_access is None and self.full_access):
                return PermissionDecision.AUTO
            if tool.permission in self.AUTO_ALLOWED:
                return PermissionDecision.AUTO
            if tool.name in self._auto_approve or tool.name in self._session_allowed:
                return PermissionDecision.AUTO
            return PermissionDecision.ASK

    def requires_approval(self, tool: Any, full_access: bool | None = None) -> bool:
        return self.evaluate(tool, full_access) is PermissionDecision.ASK

    def remember(self, tool_name: str) -> None:
        with self._lock:
            self._session_allowed.add(tool_name)

    def forget_all(self) -> None:
        with self._lock:
            self._session_allowed.clear()

    def session_allowed(self) -> frozenset[str]:
        with self._lock:
            return frozenset(self._session_allowed)

    def denied_tools(self) -> frozenset[str]:
        with self._lock:
            return frozenset(self._denied)
