"""Agent-facing result models and UI hooks."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from scout.providers import ToolCall, Turn
from scout.tools import ToolResult


@dataclass
class StepRecord:
    """A single executed tool call, kept for the final report and audit trace."""

    step: int
    tool: str
    arguments: dict[str, Any]
    ok: bool
    duration_ms: int
    error: str | None = None


@dataclass
class AgentResult:
    """Outcome of one user request."""

    text: str
    completed: bool
    limit_reached: bool = False
    steps: list[StepRecord] = field(default_factory=list)
    turns: list[Turn] = field(default_factory=list)
    task_id: str = ""

    @property
    def succeeded_tools(self) -> int:
        return sum(1 for step in self.steps if step.ok)

    @property
    def failed_tools(self) -> int:
        return sum(1 for step in self.steps if not step.ok)


@dataclass
class AgentHooks:
    """Optional callbacks the CLI uses to render progress."""

    on_model_call: Callable[[int], None] | None = None
    on_tool_start: Callable[[ToolCall], None] | None = None
    on_tool_result: Callable[[ToolCall, ToolResult], None] | None = None
    on_assistant_text: Callable[[str], None] | None = None
