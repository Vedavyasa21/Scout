"""Bounded agent orchestration loop."""

from __future__ import annotations

import time
import uuid
from collections.abc import Sequence

from scout.agent.models import AgentHooks, AgentResult, StepRecord
from scout.agent.prompt import build_system_prompt
from scout.config import Settings
from scout.logging import get_logger
from scout.providers import (
    AssistantTurn,
    ModelProvider,
    ProviderReply,
    ToolCall,
    ToolTurn,
    Turn,
    UserTurn,
)
from scout.tools import Approver, ToolContext, ToolRegistry, ToolResult

logger = get_logger("agent")

# Safety valve: a single model reply may not request more than this many calls.
MAX_CALLS_PER_ROUND = 6


class Agent:
    """Runs a single user request through the model/tool loop.

    The loop is strictly bounded: at most `max_steps` model calls and
    `max_steps * MAX_CALLS_PER_ROUND` tool executions. The agent never asserts
    an action succeeded unless the corresponding tool returned `ok=True`.
    """

    def __init__(
        self,
        *,
        provider: ModelProvider,
        registry: ToolRegistry,
        settings: Settings,
        hooks: AgentHooks | None = None,
        system_prompt: str | None = None,
        approver: Approver | None = None,
    ) -> None:
        self.provider = provider
        self.registry = registry
        self.settings = settings
        self.context = ToolContext.from_settings(settings, approver)
        self.hooks = hooks or AgentHooks()
        self.system_prompt = system_prompt or build_system_prompt(settings, registry.all())
        self.max_steps = settings.max_tool_steps
        self._max_calls = self.max_steps * MAX_CALLS_PER_ROUND

    def run(self, message: str, history: Sequence[Turn] | None = None) -> AgentResult:
        """Add `message` to the conversation and drive the loop to completion."""
        task_id = uuid.uuid4().hex[:12]
        turns: list[Turn] = list(history or [])
        turns.append(UserTurn(text=message))
        steps: list[StepRecord] = []
        calls_made = 0
        started_at = time.perf_counter()
        model_ms = 0

        logger.info("task started", extra={"task_id": task_id, "event": "task_start"})

        for step in range(1, self.max_steps + 1):
            if self.hooks.on_model_call:
                self.hooks.on_model_call(step)

            model_started = time.perf_counter()
            reply: ProviderReply = self.provider.complete(
                system=self.system_prompt,
                turns=turns,
                tools=self.registry.specs(),
            )
            model_ms += int((time.perf_counter() - model_started) * 1000)
            turns.append(AssistantTurn(text=reply.text, tool_calls=reply.tool_calls, raw=reply.raw))

            if not reply.tool_calls:
                text = reply.text or "I do not have a final answer for that request."
                if self.hooks.on_assistant_text:
                    self.hooks.on_assistant_text(text)
                logger.info(
                    "task completed",
                    extra={
                        "task_id": task_id,
                        "event": "task_complete",
                        "status": "ok",
                        "duration_ms": sum(s.duration_ms for s in steps),
                    },
                )
                return AgentResult(
                    text=text,
                    completed=True,
                    steps=steps,
                    turns=turns,
                    task_id=task_id,
                    duration_ms=int((time.perf_counter() - started_at) * 1000),
                    model_ms=model_ms,
                )

            for call in reply.tool_calls[:MAX_CALLS_PER_ROUND]:
                calls_made += 1
                if calls_made > self._max_calls:
                    payload = {
                        "ok": False,
                        "error": "Tool-call budget exhausted for this request.",
                    }
                    turns.append(
                        ToolTurn(call_id=call.call_id, name=call.name, payload=payload, ok=False)
                    )
                    steps.append(
                        StepRecord(
                            step=step,
                            tool=call.name,
                            arguments=call.arguments,
                            ok=False,
                            duration_ms=0,
                            error=payload["error"],
                        )
                    )
                    continue

                if self.hooks.on_tool_start:
                    self.hooks.on_tool_start(call)

                result = self._execute(task_id, step, call)
                steps.append(
                    StepRecord(
                        step=step,
                        tool=call.name,
                        arguments=call.arguments,
                        ok=result.ok,
                        duration_ms=result.duration_ms,
                        error=result.error,
                    )
                )
                turns.append(
                    ToolTurn(
                        call_id=call.call_id,
                        name=call.name,
                        payload=result.to_payload(),
                        ok=result.ok,
                    )
                )
                if self.hooks.on_tool_result:
                    self.hooks.on_tool_result(call, result)

        text = (
            f"Stopped after {self.max_steps} steps without a final answer; the request "
            "needs more steps than the configured limit. Completed "
            f"{sum(1 for s in steps if s.ok)} tool call(s) successfully. "
            "Raise SCOUT_MAX_TOOL_STEPS or narrow the request."
        )
        logger.warning(
            "task hit step limit",
            extra={"task_id": task_id, "event": "task_limit", "status": "limit"},
        )
        if self.hooks.on_assistant_text:
            self.hooks.on_assistant_text(text)
        return AgentResult(
            text=text,
            completed=False,
            limit_reached=True,
            steps=steps,
            turns=turns,
            task_id=task_id,
            duration_ms=int((time.perf_counter() - started_at) * 1000),
            model_ms=model_ms,
        )

    def _execute(self, task_id: str, step: int, call: ToolCall) -> ToolResult:
        permission = None
        if call.name in self.registry:
            permission = self.registry.get(call.name).permission.value
        result = self.registry.execute(call.name, call.arguments, self.context)
        logger.info(
            "tool call",
            extra={
                "task_id": task_id,
                "tool": call.name,
                "permission": permission,
                "status": "ok" if result.ok else "error",
                "duration_ms": result.duration_ms,
                "event": f"tool_step_{step}",
            },
        )
        if not result.ok:
            logger.warning(
                "tool failed: %s",
                result.error,
                extra={"task_id": task_id, "tool": call.name, "status": "error"},
            )
        return result
