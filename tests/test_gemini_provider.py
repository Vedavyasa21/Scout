"""Gemini adapter: translation and response parsing without network calls."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from scout.providers import AssistantTurn, ProviderError, ToolCall, ToolTurn, UserTurn
from scout.providers.gemini import (
    GeminiProvider,
    _extract_reply,
    _friendly_error,
    _is_retryable,
    _to_gemini_contents,
    _tool_declarations,
)


def _part(**kwargs):
    defaults = {"text": None, "thought": False, "function_call": None}
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


def _response(parts: list, finish: str = "STOP"):
    candidate = SimpleNamespace(
        finish_reason=SimpleNamespace(name=finish),
        content=SimpleNamespace(parts=parts, role="model"),
    )
    return SimpleNamespace(candidates=[candidate], prompt_feedback=None)


def test_contents_translation() -> None:
    contents = _to_gemini_contents(
        [
            UserTurn("hello"),
            AssistantTurn(
                text=None,
                tool_calls=[ToolCall(name="get_os_info", arguments={}, call_id="c1")],
            ),
            ToolTurn(call_id="c1", name="get_os_info", payload={"ok": True, "result": {}}),
        ]
    )
    assert contents[0].role == "user"
    assert contents[0].parts[0].text == "hello"
    assert contents[1].role == "model"
    assert contents[1].parts[0].function_call.name == "get_os_info"
    assert contents[2].role == "user"
    response = contents[2].parts[0].function_response
    assert response.name == "get_os_info"
    assert response.response == {"ok": True, "result": {}}


def test_raw_content_is_replayed_verbatim() -> None:
    raw = object()
    contents = _to_gemini_contents([AssistantTurn(text="x", raw=raw)])
    assert contents == [raw]


def test_text_reply_parsing() -> None:
    reply = _extract_reply(_response([_part(text="All good")]))
    assert reply.text == "All good"
    assert reply.tool_calls == []
    assert reply.finish_reason == "STOP"


def test_thought_parts_are_ignored() -> None:
    reply = _extract_reply(_response([_part(text="internal", thought=True), _part(text="final")]))
    assert reply.text == "final"


def test_function_call_parsing() -> None:
    part = _part(
        function_call=SimpleNamespace(name="search_files", args={"query": "gem5"}, id="abc")
    )
    reply = _extract_reply(_response([part]))
    assert reply.tool_calls == [
        ToolCall(name="search_files", arguments={"query": "gem5"}, call_id="abc")
    ]


def test_missing_id_is_tolerated() -> None:
    part = _part(function_call=SimpleNamespace(name="get_os_info", args=None, id=None))
    reply = _extract_reply(_response([part]))
    assert reply.tool_calls[0].call_id == ""
    assert reply.tool_calls[0].arguments == {}


def test_no_candidates_raises() -> None:
    response = SimpleNamespace(candidates=[], prompt_feedback=None)
    with pytest.raises(ProviderError, match="no candidates"):
        _extract_reply(response)


def test_blocked_response_raises() -> None:
    with pytest.raises(ProviderError, match="blocked"):
        _extract_reply(_response([], finish="SAFETY"))
    with pytest.raises(ProviderError, match="empty response"):
        _extract_reply(_response([], finish="STOP"))


def test_tool_declarations() -> None:
    declarations = _tool_declarations(
        [{"name": "t", "description": "d", "parameters": {"type": "object", "properties": {}}}]
    )
    assert len(declarations) == 1
    assert declarations[0].function_declarations[0].name == "t"
    assert _tool_declarations([]) == []


def test_friendly_error_redacts_key() -> None:
    key = "AQ.abcdef123456789"
    message = _friendly_error(RuntimeError(f"401 invalid api key {key}"), key)
    assert key not in message
    assert "rejected the API key" in message


def test_friendly_error_classifies_problems() -> None:
    assert "rate limit" in _friendly_error(RuntimeError("429 quota"), "")
    assert "timed out" in _friendly_error(RuntimeError("request timeout"), "")
    assert "not found" in _friendly_error(RuntimeError("404 model not found"), "")


def test_retryable_classification() -> None:
    assert _is_retryable(RuntimeError("503 UNAVAILABLE"))
    assert _is_retryable(RuntimeError("429 quota exceeded"))
    assert not _is_retryable(RuntimeError("400 invalid argument"))


def test_transient_failure_is_retried(monkeypatch) -> None:
    provider = GeminiProvider(model="m", api_key="k", max_retries=2)
    calls = {"count": 0}

    class Models:
        def generate_content(self, **_kwargs):
            calls["count"] += 1
            if calls["count"] < 3:
                raise RuntimeError("503 UNAVAILABLE")
            return _response([_part(text="recovered")])

    provider._client = SimpleNamespace(models=Models())
    monkeypatch.setattr("scout.providers.gemini.time.sleep", lambda _seconds: None)

    reply = provider.complete(system="s", turns=[UserTurn("hi")], tools=[])
    assert reply.text == "recovered"
    assert calls["count"] == 3


def test_non_transient_failure_is_not_retried(monkeypatch) -> None:
    provider = GeminiProvider(model="m", api_key="k", max_retries=3)
    calls = {"count": 0}

    class Models:
        def generate_content(self, **_kwargs):
            calls["count"] += 1
            raise RuntimeError("400 invalid argument")

    provider._client = SimpleNamespace(models=Models())
    monkeypatch.setattr("scout.providers.gemini.time.sleep", lambda _seconds: None)

    with pytest.raises(ProviderError):
        provider.complete(system="s", turns=[UserTurn("hi")], tools=[])
    assert calls["count"] == 1
