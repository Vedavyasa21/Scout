"""Google Gemini provider adapter."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import AsyncIterator, Sequence
from typing import Any

from scout.providers.base import (
    AssistantTurn,
    ModelProvider,
    ProviderCapabilities,
    ProviderChunk,
    ProviderError,
    ProviderReply,
    ToolCall,
    ToolTurn,
    Turn,
    UserTurn,
)
from scout.security import redact_secrets

logger = logging.getLogger("scout.provider.gemini")

_TEMPERATURE = 0.2

# Substrings that indicate a transient failure worth retrying.
_RETRYABLE_MARKERS = (
    "429",
    "500",
    "502",
    "503",
    "504",
    "rate limit",
    "quota",
    "temporarily",
    "unavailable",
    "timeout",
    "timed out",
    "connection",
)


def _is_retryable(exc: Exception) -> bool:
    message = str(exc).lower()
    return any(marker in message for marker in _RETRYABLE_MARKERS)


def _to_gemini_contents(turns: Sequence[Turn]) -> list[Any]:
    """Translate provider-agnostic turns into Gemini Content objects."""
    from google.genai import types

    contents: list[Any] = []
    for turn in turns:
        if isinstance(turn, UserTurn):
            contents.append(types.Content(role="user", parts=[types.Part(text=turn.text)]))
        elif isinstance(turn, AssistantTurn):
            if turn.raw is not None:
                # Replay the exact model content: preserves thought signatures
                # and function-call ids required by Gemini thinking models.
                contents.append(turn.raw)
                continue
            parts: list[Any] = []
            if turn.text:
                parts.append(types.Part(text=turn.text))
            for call in turn.tool_calls:
                parts.append(
                    types.Part(
                        function_call=types.FunctionCall(
                            name=call.name, args=call.arguments, id=call.call_id or None
                        )
                    )
                )
            contents.append(types.Content(role="model", parts=parts))
        elif isinstance(turn, ToolTurn):
            contents.append(
                types.Content(
                    role="user",
                    parts=[
                        types.Part(
                            function_response=types.FunctionResponse(
                                name=turn.name,
                                response=turn.payload,
                                id=turn.call_id or None,
                            )
                        )
                    ],
                )
            )
    return contents


def _tool_declarations(tools: Sequence[dict[str, Any]]) -> list[Any]:
    from google.genai import types

    declarations = [
        types.FunctionDeclaration(
            name=spec["name"],
            description=spec.get("description", ""),
            parameters_json_schema=spec.get("parameters", {"type": "object", "properties": {}}),
        )
        for spec in tools
    ]
    return [types.Tool(function_declarations=declarations)] if declarations else []


def _extract_reply(response: Any) -> ProviderReply:
    candidates = getattr(response, "candidates", None) or []
    if not candidates:
        feedback = getattr(response, "prompt_feedback", None)
        reason = getattr(feedback, "block_reason", None) if feedback else None
        raise ProviderError(
            "The provider returned no candidates"
            + (f" (blocked: {reason})" if reason else "")
            + ". The request may have been filtered by safety settings."
        )

    candidate = candidates[0]
    finish = getattr(candidate, "finish_reason", None)
    finish_name = getattr(finish, "name", None) or (str(finish) if finish else None)

    text_chunks: list[str] = []
    calls: list[ToolCall] = []
    content = getattr(candidate, "content", None)
    for part in getattr(content, "parts", None) or []:
        function_call = getattr(part, "function_call", None)
        if function_call is not None:
            calls.append(
                ToolCall(
                    name=function_call.name or "",
                    arguments=dict(function_call.args or {}),
                    call_id=function_call.id or "",
                )
            )
            continue
        text = getattr(part, "text", None)
        if text and not getattr(part, "thought", False):
            text_chunks.append(text)

    text = "".join(text_chunks).strip() or None

    if not calls and not text:
        if finish_name in {"SAFETY", "RECITATION", "BLOCKLIST", "PROHIBITED_CONTENT"}:
            raise ProviderError(f"The provider blocked the response (reason: {finish_name}).")
        raise ProviderError(f"The provider returned an empty response (finish: {finish_name}).")

    return ProviderReply(
        text=text,
        tool_calls=calls,
        raw=content,
        finish_reason=finish_name,
    )


def _extract_stream_chunk(
    response: Any,
) -> tuple[str, list[ToolCall], list[Any], str | None]:
    """Pull the text delta, tool calls and raw parts out of one stream chunk."""
    candidates = getattr(response, "candidates", None) or []
    if not candidates:
        return "", [], [], None

    candidate = candidates[0]
    finish = getattr(candidate, "finish_reason", None)
    finish_name = getattr(finish, "name", None) or (str(finish) if finish else None)
    parts = list(getattr(getattr(candidate, "content", None), "parts", None) or [])

    text_chunks: list[str] = []
    calls: list[ToolCall] = []
    for part in parts:
        function_call = getattr(part, "function_call", None)
        if function_call is not None:
            calls.append(
                ToolCall(
                    name=function_call.name or "",
                    arguments=dict(function_call.args or {}),
                    call_id=function_call.id or "",
                )
            )
            continue
        text = getattr(part, "text", None)
        if text and not getattr(part, "thought", False):
            text_chunks.append(text)

    return "".join(text_chunks), calls, parts, finish_name


def _friendly_error(exc: Exception, api_key: str) -> str:
    message = redact_secrets(str(exc), [api_key])
    lowered = message.lower()
    if "api key" in lowered or "unauthenticated" in lowered or "401" in lowered:
        return "The provider rejected the API key. Check SCOUT_API_KEY in your .env file."
    if "permission" in lowered or "403" in lowered:
        return "The provider denied access. Verify the key has Gemini API access enabled."
    if "not found" in lowered or "404" in lowered:
        return "The configured model was not found. Check SCOUT_MODEL."
    if "429" in lowered or "quota" in lowered or "rate" in lowered:
        return "The provider rate limit or quota was reached. Wait and retry."
    if "timeout" in lowered or "timed out" in lowered:
        return (
            "The provider request timed out. Check your connection or raise SCOUT_REQUEST_TIMEOUT."
        )
    return f"Provider request failed: {message}"


class GeminiProvider(ModelProvider):
    """Gemini adapter using the official google-genai SDK."""

    name = "gemini"

    def __init__(
        self,
        *,
        model: str,
        api_key: str,
        request_timeout: float = 120.0,
        max_retries: int = 2,
        thinking_budget: int = -1,
        thinking_level: str = "",
    ) -> None:
        super().__init__(model=model, api_key=api_key, request_timeout=request_timeout)
        self.max_retries = max_retries
        self.thinking_budget = thinking_budget
        self.thinking_level = thinking_level
        try:
            from google import genai
            from google.genai import types
        except ImportError as exc:  # pragma: no cover - dependency is declared
            raise ProviderError(
                "The google-genai package is not installed. Run: pip install google-genai"
            ) from exc

        self._genai = genai
        self._types = types
        self._client = genai.Client(
            api_key=api_key,
            http_options=types.HttpOptions(timeout=int(request_timeout * 1000)),
        )

    @property
    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            streaming=True,
            tool_calling=True,
            audio_input=True,
        )

    def _build_request(
        self,
        system: str,
        turns: Sequence[Turn],
        tools: Sequence[dict[str, Any]],
    ) -> tuple[list[Any], dict[str, Any]]:
        """Build provider contents and config arguments shared by both paths."""
        types = self._types
        config_args: dict[str, Any] = {
            "system_instruction": system,
            "temperature": _TEMPERATURE,
            "tools": _tool_declarations(tools),
            "automatic_function_calling": types.AutomaticFunctionCallingConfig(disable=True),
        }
        if self.thinking_budget >= 0:
            config_args["thinking_config"] = types.ThinkingConfig(
                thinking_budget=self.thinking_budget
            )
        elif self.thinking_level:
            config_args["thinking_config"] = types.ThinkingConfig(
                thinking_level=self.thinking_level
            )
        logger.debug(
            "gemini request model=%s turns=%d tools=%d", self.model, len(turns), len(tools)
        )
        return _to_gemini_contents(turns), config_args

    def complete(
        self,
        *,
        system: str,
        turns: Sequence[Turn],
        tools: Sequence[dict[str, Any]],
    ) -> ProviderReply:
        types = self._types
        contents, config_args = self._build_request(system, turns, tools)
        try:
            response = self._generate(contents, types.GenerateContentConfig(**config_args))
        except ProviderError as exc:
            # Not every model accepts thinking settings. Retry once without them
            # rather than failing the whole request after a model swap.
            if "thinking_config" not in config_args or "invalid argument" not in str(exc).lower():
                raise
            logger.warning("model rejected thinking settings; retrying without them")
            config_args.pop("thinking_config")
            response = self._generate(contents, types.GenerateContentConfig(**config_args))
        reply = _extract_reply(response)
        logger.debug(
            "gemini reply finish=%s tool_calls=%d text_chars=%d",
            reply.finish_reason,
            len(reply.tool_calls),
            len(reply.text or ""),
        )
        return reply

    def _open_stream(self, contents: Any, config: Any) -> Any:
        """Open a native async stream. Isolated so tests can replace the SDK."""
        return self._client.aio.models.generate_content_stream(
            model=self.model,
            contents=contents,
            config=config,
        )

    async def stream(
        self,
        *,
        system: str,
        turns: Sequence[Turn],
        tools: Sequence[dict[str, Any]],
    ) -> AsyncIterator[ProviderChunk]:
        """Stream text deltas and return the replayable turn in the final chunk."""
        types = self._types
        contents, config_args = self._build_request(system, turns, tools)
        attempts = self.max_retries + 1
        attempt = 0
        thinking_retry_available = True

        while attempt < attempts:
            attempt += 1
            text_chunks: list[str] = []
            calls: list[ToolCall] = []
            raw_parts: list[Any] = []
            finish_name: str | None = None
            emitted = False

            try:
                iterator = self._open_stream(contents, types.GenerateContentConfig(**config_args))
                async for chunk in iterator:
                    delta, chunk_calls, chunk_parts, finish = _extract_stream_chunk(chunk)
                    if finish:
                        finish_name = finish
                    raw_parts.extend(chunk_parts)
                    calls.extend(chunk_calls)
                    if delta:
                        text_chunks.append(delta)
                        emitted = True
                        yield ProviderChunk(text=delta)
                    elif chunk_calls or chunk_parts:
                        emitted = True
            except ProviderError:
                raise
            except Exception as exc:  # SDK raises several unrelated error types
                message = str(exc).lower()
                if (
                    not emitted
                    and "thinking_config" in config_args
                    and "invalid argument" in message
                    and thinking_retry_available
                ):
                    logger.warning("model rejected thinking settings; retrying without them")
                    config_args.pop("thinking_config", None)
                    thinking_retry_available = False
                    attempt -= 1
                    continue
                if not emitted and attempt < attempts and _is_retryable(exc):
                    delay = min(2 ** (attempt - 1), 4)
                    logger.warning(
                        "transient stream failure (attempt %d/%d), retrying in %ss",
                        attempt,
                        attempts,
                        delay,
                    )
                    await asyncio.sleep(delay)
                    continue
                friendly = _friendly_error(exc, self._api_key)
                logger.warning("gemini stream failed: %s", friendly)
                raise ProviderError(friendly) from exc

            text = "".join(text_chunks).strip() or None
            if not text and not calls:
                blocked = {"SAFETY", "RECITATION", "BLOCKLIST", "PROHIBITED_CONTENT"}
                if finish_name in blocked:
                    raise ProviderError(
                        f"The provider blocked the response (reason: {finish_name})."
                    )
                raise ProviderError(
                    f"The provider returned an empty response (finish: {finish_name})."
                )
            logger.debug(
                "gemini stream finish=%s tool_calls=%d text_chars=%d",
                finish_name,
                len(calls),
                len(text or ""),
            )
            yield ProviderChunk(
                tool_calls=calls,
                raw=types.Content(role="model", parts=raw_parts) if raw_parts else None,
                finish_reason=finish_name,
            )
            return

        raise ProviderError("The provider stream failed without a response.")  # pragma: no cover

    def transcribe_audio(self, data: bytes, *, mime_type: str = "audio/wav") -> str:
        """Transcribe a recorded voice command with Gemini's audio input."""
        types = self._types
        config = types.GenerateContentConfig(
            system_instruction=(
                "You transcribe spoken computer commands. Output only the transcript, "
                "without commentary, quotes or speaker labels."
            ),
            temperature=0.0,
            max_output_tokens=1024,
        )
        contents = [
            types.Content(
                role="user",
                parts=[
                    types.Part.from_bytes(data=data, mime_type=mime_type),
                    types.Part(text="Transcribe the speech in this audio exactly."),
                ],
            )
        ]
        response = self._generate(contents, config)
        text = (response.text or "").strip()
        if not text:
            raise ProviderError("No speech was detected in the recording.")
        return text

    def _generate(self, contents: Any, config: Any) -> Any:
        """Make one request with bounded exponential backoff for transient errors."""
        attempts = self.max_retries + 1
        for attempt in range(1, attempts + 1):
            try:
                return self._client.models.generate_content(
                    model=self.model,
                    contents=contents,
                    config=config,
                )
            except Exception as exc:  # SDK raises several unrelated error types
                if attempt < attempts and _is_retryable(exc):
                    delay = min(2 ** (attempt - 1), 4)
                    logger.warning(
                        "transient provider failure (attempt %d/%d), retrying in %ss",
                        attempt,
                        attempts,
                        delay,
                    )
                    time.sleep(delay)
                    continue
                message = _friendly_error(exc, self._api_key)
                logger.warning("gemini request failed: %s", message)
                raise ProviderError(message) from exc
        raise ProviderError("The provider request failed without a response.")  # pragma: no cover
