"""Provider-agnostic conversation turns and the model provider interface."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any


class ProviderError(Exception):
    """Raised when a provider request fails. Messages never contain secrets."""


@dataclass
class ToolCall:
    """A tool invocation requested by the model."""

    name: str
    arguments: dict[str, Any] = field(default_factory=dict)
    call_id: str = ""


@dataclass
class UserTurn:
    text: str


@dataclass
class AssistantTurn:
    text: str | None = None
    tool_calls: list[ToolCall] = field(default_factory=list)
    raw: Any = None
    """Provider-native payload, replayed verbatim so signatures/ids survive."""


@dataclass
class ToolTurn:
    call_id: str
    name: str
    payload: dict[str, Any]
    ok: bool = True


Turn = UserTurn | AssistantTurn | ToolTurn


@dataclass
class ProviderReply:
    text: str | None
    tool_calls: list[ToolCall] = field(default_factory=list)
    raw: Any = None
    finish_reason: str | None = None


class ModelProvider(ABC):
    """Minimal interface every model backend must implement."""

    name: str = "unknown"

    def __init__(
        self,
        *,
        model: str,
        api_key: str = "",
        request_timeout: float = 120.0,
    ) -> None:
        self.model = model
        self._api_key = api_key
        self.request_timeout = request_timeout

    @abstractmethod
    def complete(
        self,
        *,
        system: str,
        turns: Sequence[Turn],
        tools: Sequence[dict[str, Any]],
    ) -> ProviderReply:
        """Send the conversation and available tools, returning the next reply."""

    def verify(self) -> str:
        """Perform a minimal round trip to verify credentials. Returns model text."""
        reply = self.complete(
            system="You are a connectivity probe. Reply with exactly: OK",
            turns=[UserTurn("Reply with exactly: OK")],
            tools=[],
        )
        return (reply.text or "").strip()

    def close(self) -> None:
        """Release provider resources. Optional."""
        return None
