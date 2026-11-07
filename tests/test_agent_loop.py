"""Bounded agent loop: tool execution, failures, limits and history."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import pytest

from scout.agent import MAX_CALLS_PER_ROUND, Agent, AgentHooks
from scout.providers import (
    ModelProvider,
    ProviderError,
    ProviderReply,
    ToolCall,
    Turn,
    UserTurn,
)
from scout.tools import (
    PermissionLevel,
    Tool,
    ToolContext,
    ToolRegistry,
    ToolResult,
    build_registry,
)


class FakeProvider(ModelProvider):
    """Replays scripted replies and records the turns it was given."""

    name = "fake"

    def __init__(self, replies: list[ProviderReply]) -> None:
        super().__init__(model="fake-model", api_key="unused")
        self._replies = list(replies)
        self.calls: list[list[Turn]] = []

    def complete(
        self,
        *,
        system: str,
        turns: Sequence[Turn],
        tools: Sequence[dict[str, Any]],
    ) -> ProviderReply:
        self.calls.append(list(turns))
        if not self._replies:
            raise ProviderError("no scripted replies left")
        return self._replies.pop(0)


def _registry() -> ToolRegistry:
    registry = build_registry()
    registry.register(
        Tool(
            name="fake_write",
            description="pretend write",
            parameters={"type": "object", "properties": {}},
            permission=PermissionLevel.WRITE,
            handler=lambda _a, _c: ToolResult(ok=True, data={"written": True}),
        )
    )
    return registry


def _agent(settings, replies: list[ProviderReply], hooks: AgentHooks | None = None) -> Agent:
    return Agent(
        provider=FakeProvider(replies),
        registry=_registry(),
        settings=settings,
        hooks=hooks,
    )


def test_plain_answer_has_no_tool_calls(settings) -> None:
    agent = _agent(settings, [ProviderReply(text="Hello there")])
    result = agent.run("hi")
    assert result.completed
    assert result.text == "Hello there"
    assert result.steps == []
    assert isinstance(result.turns[-1], UserTurn) is False


def test_tool_call_is_executed_and_result_returns_to_model(settings) -> None:
    replies = [
        ProviderReply(
            text=None,
            tool_calls=[ToolCall(name="get_os_info", arguments={}, call_id="c1")],
        ),
        ProviderReply(text="You are on Windows."),
    ]
    provider = FakeProvider(replies)
    agent = Agent(provider=provider, registry=_registry(), settings=settings)
    result = agent.run("what OS?")

    assert result.completed
    assert len(result.steps) == 1
    assert result.steps[0].ok
    assert result.steps[0].tool == "get_os_info"
    # The second model call must include the tool result turn.
    assert any(type(turn).__name__ == "ToolTurn" for turn in provider.calls[1])
    tool_turn = next(t for t in provider.calls[1] if type(t).__name__ == "ToolTurn")
    assert tool_turn.ok is True
    assert tool_turn.payload["ok"] is True


def test_unknown_tool_failure_is_reported_not_claimed(settings) -> None:
    replies = [
        ProviderReply(text=None, tool_calls=[ToolCall(name="ghost", arguments={})]),
        ProviderReply(text="That tool does not exist."),
    ]
    agent = _agent(settings, replies)
    result = agent.run("do something")
    assert result.completed
    assert result.steps[0].ok is False
    assert "Unknown tool" in (result.steps[0].error or "")
    assert result.failed_tools == 1


def test_write_tool_is_denied_by_gate(settings) -> None:
    replies = [
        ProviderReply(text=None, tool_calls=[ToolCall(name="fake_write", arguments={})]),
        ProviderReply(text="I cannot write files yet."),
    ]
    agent = _agent(settings, replies)
    result = agent.run("write a file")
    assert result.steps[0].ok is False
    assert "permission" in (result.steps[0].error or "").lower()


def test_step_limit_is_enforced(settings) -> None:
    settings.max_tool_steps = 2
    replies = [
        ProviderReply(text=None, tool_calls=[ToolCall(name="get_os_info", arguments={})]),
        ProviderReply(text=None, tool_calls=[ToolCall(name="get_os_info", arguments={})]),
        ProviderReply(text="never reached"),
    ]
    agent = _agent(settings, replies)
    result = agent.run("loop forever")
    assert not result.completed
    assert result.limit_reached
    assert len(result.steps) == 2
    assert "Stopped after 2 steps" in result.text


def test_tool_calls_per_round_are_capped(settings) -> None:
    calls = [ToolCall(name="get_os_info", arguments={}) for _ in range(MAX_CALLS_PER_ROUND + 4)]
    replies = [ProviderReply(text=None, tool_calls=calls), ProviderReply(text="done")]
    agent = _agent(settings, replies)
    result = agent.run("many calls")
    assert len(result.steps) == MAX_CALLS_PER_ROUND


def test_hooks_fire_in_order(settings) -> None:
    events: list[str] = []
    hooks = AgentHooks(
        on_model_call=lambda step: events.append(f"model:{step}"),
        on_tool_start=lambda call: events.append(f"start:{call.name}"),
        on_tool_result=lambda call, result: events.append(f"result:{result.ok}"),
        on_assistant_text=lambda text: events.append("answer"),
    )
    replies = [
        ProviderReply(text=None, tool_calls=[ToolCall(name="get_os_info", arguments={})]),
        ProviderReply(text="done"),
    ]
    agent = _agent(settings, replies, hooks=hooks)
    agent.run("go")
    assert events == ["model:1", "start:get_os_info", "result:True", "model:2", "answer"]


def test_history_is_preserved_across_runs(settings) -> None:
    provider = FakeProvider([ProviderReply(text="first"), ProviderReply(text="second")])
    agent = Agent(provider=provider, registry=_registry(), settings=settings)
    first = agent.run("one")
    second = agent.run("two", history=first.turns)
    assert second.completed
    # Second request includes the first exchange: user, assistant, user.
    assert len(provider.calls[1]) == 3
    assert provider.calls[1][0].text == "one"


def test_provider_error_propagates(settings) -> None:
    agent = _agent(settings, [])
    with pytest.raises(ProviderError):
        agent.run("hi")


def test_tool_context_uses_settings(settings) -> None:
    agent = _agent(settings, [ProviderReply(text="ok")])
    context = ToolContext.from_settings(settings)
    assert agent.context.workspaces == context.workspaces
