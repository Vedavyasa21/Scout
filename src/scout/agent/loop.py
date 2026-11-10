"""Bounded agent orchestration loop with streaming, events and cancellation.

The model still proposes and Scout still disposes: tool arguments are validated
by the registry and the policy gate is unchanged. What is new is that one run is
an asyncio task that emits structured events, streams text deltas and can be
stopped through a thread-safe cancellation token.

`Agent.run()` remains a synchronous facade for existing callers (CLI legacy,
PyQt worker thread, tests); `Agent.run_async()` is the primary implementation.
"""

from __future__ import annotations

import asyncio
import threading
import time
import uuid
from collections.abc import Sequence
from dataclasses import replace

from scout.agent.models import AgentHooks, AgentResult, StepRecord
from scout.agent.plan import PlanTracker
from scout.agent.prompt import build_system_prompt
from scout.config import Settings
from scout.control import EMERGENCY_STOP
from scout.events import EventBus, EventType
from scout.logging import get_logger
from scout.providers import (
    AssistantTurn,
    ModelProvider,
    ProviderError,
    ProviderReply,
    ToolCall,
    ToolTurn,
    Turn,
    UserTurn,
)
from scout.security.audit import AuditLedger
from scout.tools import Approver, ToolContext, ToolRegistry

logger = get_logger("agent")

# Safety valve: a single model reply may not request more than this many calls.
MAX_CALLS_PER_ROUND = 6


class AgentCancelled(Exception):
    """Raised when a cancellation token stops a run before it finishes."""


class CancellationToken:
    """Thread-safe stop flag usable from a UI thread while the agent runs."""

    def __init__(self) -> None:
        self._event = threading.Event()

    def cancel(self) -> None:
        self._event.set()

    @property
    def is_cancelled(self) -> bool:
        return self._event.is_set()

    def raise_if_cancelled(self) -> None:
        if self.is_cancelled:
            raise AgentCancelled("The task was cancelled.")


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
        bus: EventBus | None = None,
    ) -> None:
        self.provider = provider
        self.registry = registry
        self.settings = settings
        self.context = ToolContext.from_settings(settings, approver)
        self.hooks = hooks or AgentHooks()
        self.system_prompt = system_prompt or build_system_prompt(settings, registry.all())
        self.max_steps = settings.max_tool_steps
        self._max_calls = self.max_steps * MAX_CALLS_PER_ROUND
        self.bus = bus
        # The security engine is independent of the model and re-applies the
        # configured hard denies/auto-approvals for every call. Empty lists do
        # not erase rules a caller already installed on the registry policy.
        policy_rules: dict[str, object] = {"full_access": settings.full_access}
        if settings.auto_approve_tools:
            policy_rules["auto_approve"] = settings.auto_approve_tools
        if settings.denied_tools:
            policy_rules["deny"] = settings.denied_tools
        self.registry.policy.configure(**policy_rules)  # type: ignore[arg-type]
        self.audit = AuditLedger(
            settings.resolved_audit_file if settings.audit_enabled else None,
            enabled=settings.audit_enabled,
        )

    def run(
        self,
        message: str,
        history: Sequence[Turn] | None = None,
        *,
        cancel: CancellationToken | None = None,
        bus: EventBus | None = None,
    ) -> AgentResult:
        """Synchronous facade. Not callable from inside a running event loop."""
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(self.run_async(message, history=history, cancel=cancel, bus=bus))
        raise RuntimeError(
            "Agent.run() cannot be called from a running event loop; "
            "use `await agent.run_async(...)` instead."
        )

    async def run_async(
        self,
        message: str,
        history: Sequence[Turn] | None = None,
        *,
        cancel: CancellationToken | None = None,
        bus: EventBus | None = None,
    ) -> AgentResult:
        """Add `message` to the conversation and drive the loop to completion."""
        task_id = uuid.uuid4().hex[:12]
        turns: list[Turn] = list(history or [])
        turns.append(UserTurn(text=message))
        steps: list[StepRecord] = []
        calls_made = 0
        started_at = time.perf_counter()
        model_ms = 0
        events = bus or self.bus
        token = cancel or CancellationToken()
        EMERGENCY_STOP.register(token)
        plan = PlanTracker()
        context = replace(self.context, audit=self.audit, task_id=task_id, plan=plan)
        last_plan_revision = 0

        def emit(event_type: EventType, **data: object) -> None:
            if events is not None:
                events.emit(event_type, task_id, **data)

        logger.info("task started", extra={"task_id": task_id, "event": "task_start"})
        self.audit.record(
            "run_started",
            task_id=task_id,
            step_limit=self.max_steps,
            request_chars=len(message),
        )
        emit(EventType.AGENT_STARTED, max_steps=self.max_steps)

        try:
            for step in range(1, self.max_steps + 1):
                token.raise_if_cancelled()

                emit(EventType.AGENT_THINKING, step=step)
                if self.hooks.on_model_call:
                    self.hooks.on_model_call(step)
                emit(EventType.MODEL_STARTED, step=step)

                reply, step_ms = await self._stream_reply(
                    turns=turns,
                    step=step,
                    cancel=token,
                    emit=emit,
                )
                model_ms += step_ms
                turns.append(
                    AssistantTurn(
                        text=reply.text,
                        tool_calls=reply.tool_calls,
                        raw=reply.raw,
                    )
                )

                if not reply.tool_calls:
                    # One verification pass: if the model declared a plan and left
                    # steps pending or failed, make it reconcile before finishing.
                    if plan.needs_verification() and plan.begin_verification():
                        emit(EventType.VERIFICATION_STARTED, plan=plan.snapshot())
                        turns.append(UserTurn(text=plan.verification_note()))
                        continue
                    if plan.verification_used:
                        snapshot = plan.snapshot() or {}
                        emit(
                            EventType.VERIFICATION_COMPLETED,
                            outstanding=plan.plan.outstanding if plan.plan else 0,
                            failed=plan.plan.failed if plan.plan else 0,
                            plan=snapshot,
                        )
                    text = reply.text or "I do not have a final answer for that request."
                    if self.hooks.on_assistant_text:
                        self.hooks.on_assistant_text(text)
                    duration_ms = int((time.perf_counter() - started_at) * 1000)
                    logger.info(
                        "task completed",
                        extra={
                            "task_id": task_id,
                            "event": "task_complete",
                            "status": "ok",
                            "duration_ms": sum(s.duration_ms for s in steps),
                        },
                    )
                    emit(
                        EventType.AGENT_SUCCESS,
                        text=text,
                        duration_ms=duration_ms,
                        model_ms=model_ms,
                        tools=sum(1 for s in steps if s.ok),
                    )
                    self.audit.record(
                        "run_finished",
                        task_id=task_id,
                        status="ok",
                        duration_ms=duration_ms,
                        tools_ok=sum(1 for s in steps if s.ok),
                        tools_failed=sum(1 for s in steps if not s.ok),
                    )
                    return AgentResult(
                        text=text,
                        completed=True,
                        steps=steps,
                        turns=turns,
                        task_id=task_id,
                        duration_ms=duration_ms,
                        model_ms=model_ms,
                        plan=plan.snapshot(),
                    )

                # Never drop requested calls silently: the model must learn that
                # the rest of a large batch was not executed.
                for skipped in reply.tool_calls[MAX_CALLS_PER_ROUND:]:
                    error = (
                        f"Skipped: a single reply may request at most "
                        f"{MAX_CALLS_PER_ROUND} tool calls. Request the remaining "
                        "work in another step."
                    )
                    turns.append(
                        ToolTurn(
                            call_id=skipped.call_id,
                            name=skipped.name,
                            payload={"ok": False, "error": error},
                            ok=False,
                        )
                    )
                    emit(
                        EventType.TOOL_COMPLETED,
                        step=step,
                        tool=skipped.name,
                        ok=False,
                        error=error,
                    )
                if len(reply.tool_calls) > MAX_CALLS_PER_ROUND:
                    logger.warning(
                        "model requested %d tool calls; capped at %d",
                        len(reply.tool_calls),
                        MAX_CALLS_PER_ROUND,
                    )

                for call in reply.tool_calls[:MAX_CALLS_PER_ROUND]:
                    token.raise_if_cancelled()
                    calls_made += 1
                    if calls_made > self._max_calls:
                        payload = {
                            "ok": False,
                            "error": "Tool-call budget exhausted for this request.",
                        }
                        turns.append(
                            ToolTurn(
                                call_id=call.call_id,
                                name=call.name,
                                payload=payload,
                                ok=False,
                            )
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
                        emit(
                            EventType.TOOL_COMPLETED,
                            step=step,
                            tool=call.name,
                            ok=False,
                            error=payload["error"],
                        )
                        continue

                    approval_needed, permission = self._approval_info(call.name)
                    if approval_needed:
                        emit(
                            EventType.APPROVAL_REQUIRED,
                            step=step,
                            tool=call.name,
                            permission=permission,
                            arguments=call.arguments,
                        )

                    if self.hooks.on_tool_start:
                        self.hooks.on_tool_start(call)
                    emit(
                        EventType.TOOL_STARTED,
                        step=step,
                        tool=call.name,
                        arguments=call.arguments,
                    )

                    result = await self.registry.execute_async(call.name, call.arguments, context)
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
                            extra={
                                "task_id": task_id,
                                "tool": call.name,
                                "status": "error",
                            },
                        )
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
                    emit(
                        EventType.TOOL_COMPLETED,
                        step=step,
                        tool=call.name,
                        ok=result.ok,
                        duration_ms=result.duration_ms,
                        error=result.error,
                    )
                    if self.hooks.on_tool_result:
                        self.hooks.on_tool_result(call, result)
                    if plan.revision != last_plan_revision:
                        last_plan_revision = plan.revision
                        emit(EventType.PLAN_UPDATED, plan=plan.snapshot())

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
            emit(
                EventType.AGENT_LIMIT,
                max_steps=self.max_steps,
                tools=sum(1 for s in steps if s.ok),
            )
            self.audit.record(
                "run_finished",
                task_id=task_id,
                status="limit",
                tools_ok=sum(1 for s in steps if s.ok),
                tools_failed=sum(1 for s in steps if not s.ok),
            )
            return AgentResult(
                text=text,
                completed=False,
                limit_reached=True,
                steps=steps,
                turns=turns,
                task_id=task_id,
                duration_ms=int((time.perf_counter() - started_at) * 1000),
                model_ms=model_ms,
                plan=plan.snapshot(),
            )

        except (asyncio.CancelledError, AgentCancelled) as exc:
            self.audit.record("run_finished", task_id=task_id, status="cancelled")
            emit(EventType.AGENT_CANCELLED, reason=str(exc) or "cancelled")
            logger.info(
                "task cancelled",
                extra={"task_id": task_id, "event": "task_cancel", "status": "cancelled"},
            )
            raise
        except ProviderError as exc:
            self.audit.record("run_finished", task_id=task_id, status="provider_error")
            emit(EventType.AGENT_ERROR, error=str(exc), kind="provider")
            logger.warning(
                "task failed: %s",
                exc,
                extra={"task_id": task_id, "event": "task_error", "status": "error"},
            )
            raise
        except Exception as exc:  # defensive: surface a structured event first
            self.audit.record("run_finished", task_id=task_id, status="internal_error")
            emit(
                EventType.AGENT_ERROR,
                error=f"{type(exc).__name__}: {exc}",
                kind="internal",
            )
            logger.exception(
                "task crashed",
                extra={"task_id": task_id, "event": "task_error", "status": "error"},
            )
            raise
        finally:
            EMERGENCY_STOP.unregister(token)

    async def _stream_reply(
        self,
        *,
        turns: Sequence[Turn],
        step: int,
        cancel: CancellationToken | None,
        emit,
    ) -> tuple[ProviderReply, int]:
        """Consume a provider stream, emit deltas and assemble one reply."""
        text_parts: list[str] = []
        tool_calls: list[ToolCall] = []
        raw = None
        finish_reason: str | None = None
        started = time.perf_counter()

        async for chunk in self.provider.stream(
            system=self.system_prompt,
            turns=turns,
            tools=self.registry.specs(),
        ):
            if cancel is not None:
                cancel.raise_if_cancelled()
            if chunk.text:
                text_parts.append(chunk.text)
                if self.hooks.on_model_chunk:
                    self.hooks.on_model_chunk(chunk.text)
                emit(EventType.MODEL_CHUNK, step=step, text=chunk.text)
            if chunk.tool_calls:
                tool_calls.extend(chunk.tool_calls)
            if chunk.raw is not None:
                raw = chunk.raw
            if chunk.finish_reason:
                finish_reason = chunk.finish_reason

        elapsed_ms = int((time.perf_counter() - started) * 1000)
        text = "".join(text_parts).strip() or None
        emit(
            EventType.MODEL_COMPLETED,
            step=step,
            text=text,
            text_chars=len(text or ""),
            tool_calls=len(tool_calls),
            duration_ms=elapsed_ms,
        )
        return (
            ProviderReply(
                text=text,
                tool_calls=tool_calls,
                raw=raw,
                finish_reason=finish_reason,
            ),
            elapsed_ms,
        )

    def _approval_info(self, name: str) -> tuple[bool, str]:
        """Report whether a tool will need the human gate (notification only).

        The registry still makes the real decision; this only lets a consumer
        show an approval event before execution.
        """
        if name not in self.registry:
            return False, ""
        tool = self.registry.get(name)
        return self.registry.policy.requires_approval(tool), tool.permission.value
