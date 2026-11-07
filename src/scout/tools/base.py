"""Tool contract, argument validation and the central registry."""

from __future__ import annotations

import concurrent.futures
import json
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any

from scout.config import Settings


class PermissionLevel(StrEnum):
    """Central permission categories used by the policy engine."""

    READ = "read"
    LOW_RISK = "low_risk"
    WRITE = "write"
    EXECUTE = "execute"
    HIGH_RISK = "high_risk"


class ToolError(Exception):
    """Base class for tool failures safe to show to the user."""


class ToolArgumentError(ToolError):
    """Raised when model-supplied arguments fail local validation."""


class ToolNotFoundError(ToolError):
    """Raised when a tool name is not registered."""


class PermissionDeniedError(ToolError):
    """Raised when the policy engine denies a tool call."""


@dataclass(frozen=True)
class ToolContext:
    """Runtime dependencies handed to every tool handler."""

    settings: Settings
    workspaces: tuple[Path, ...] = field(default_factory=tuple)

    @classmethod
    def from_settings(cls, settings: Settings) -> ToolContext:
        return cls(settings=settings, workspaces=tuple(settings.workspaces))


@dataclass
class ToolResult:
    """Outcome of a tool execution."""

    ok: bool
    data: Any = None
    error: str | None = None
    duration_ms: int = 0

    def to_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"ok": self.ok}
        if self.ok:
            payload["result"] = self.data
        else:
            payload["error"] = self.error or "unknown error"
        return payload

    def to_json(self) -> str:
        return json.dumps(self.to_payload(), ensure_ascii=False, default=str)


Handler = Callable[[dict[str, Any], ToolContext], ToolResult]

_TYPE_CHECKS: dict[str, tuple[type, ...]] = {
    "string": (str,),
    "integer": (int,),
    "number": (int, float),
    "boolean": (bool,),
    "array": (list,),
    "object": (dict,),
}


def _validate_value(name: str, value: Any, spec: dict[str, Any]) -> Any:
    expected = spec.get("type")
    if expected in _TYPE_CHECKS:
        # bool is a subclass of int; reject it for numeric types.
        if isinstance(value, bool) and expected in ("integer", "number"):
            raise ToolArgumentError(f"Argument '{name}' must be {expected}, got boolean")
        if not isinstance(value, _TYPE_CHECKS[expected]):
            raise ToolArgumentError(
                f"Argument '{name}' must be {expected}, got {type(value).__name__}"
            )
    if "enum" in spec and value not in spec["enum"]:
        raise ToolArgumentError(f"Argument '{name}' must be one of {spec['enum']}")
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if "minimum" in spec and value < spec["minimum"]:
            raise ToolArgumentError(f"Argument '{name}' must be >= {spec['minimum']}")
        if "maximum" in spec and value > spec["maximum"]:
            raise ToolArgumentError(f"Argument '{name}' must be <= {spec['maximum']}")
    if isinstance(value, str):
        if "minLength" in spec and len(value) < spec["minLength"]:
            raise ToolArgumentError(f"Argument '{name}' is too short")
        if "maxLength" in spec and len(value) > spec["maxLength"]:
            raise ToolArgumentError(f"Argument '{name}' is too long")
    if isinstance(value, list):
        if "minItems" in spec and len(value) < spec["minItems"]:
            raise ToolArgumentError(f"Argument '{name}' needs at least {spec['minItems']} items")
        if "maxItems" in spec and len(value) > spec["maxItems"]:
            raise ToolArgumentError(f"Argument '{name}' allows at most {spec['maxItems']} items")
        item_spec = spec.get("items")
        if isinstance(item_spec, dict):
            return [_validate_value(f"{name}[]", item, item_spec) for item in value]
    return value


def validate_arguments(schema: dict[str, Any], arguments: dict[str, Any]) -> dict[str, Any]:
    """Validate model-generated arguments against a JSON-schema subset."""
    if not isinstance(arguments, dict):
        raise ToolArgumentError("Tool arguments must be a JSON object")

    properties: dict[str, Any] = schema.get("properties", {})
    required: list[str] = schema.get("required", [])

    missing = [key for key in required if key not in arguments]
    if missing:
        raise ToolArgumentError(f"Missing required argument(s): {', '.join(missing)}")

    if schema.get("additionalProperties") is False:
        unknown = [key for key in arguments if key not in properties]
        if unknown:
            raise ToolArgumentError(f"Unknown argument(s): {', '.join(unknown)}")

    validated: dict[str, Any] = {}
    for key, value in arguments.items():
        spec = properties.get(key)
        if spec is None:
            validated[key] = value
            continue
        validated[key] = _validate_value(key, value, spec)
    return validated


class PermissionGate:
    """Milestone 1 policy gate.

    Read-only and allowlisted low-risk tools are auto-approved. Every other
    category is refused until the approval workflow lands in Milestone 2, so no
    write or execute tool can run accidentally in the meantime.
    """

    ALLOWED = frozenset({PermissionLevel.READ, PermissionLevel.LOW_RISK})

    def check(self, tool: Tool) -> None:
        if tool.permission not in self.ALLOWED:
            raise PermissionDeniedError(
                f"Tool '{tool.name}' requires '{tool.permission.value}' permission, "
                "which is not available in this build (Milestone 2)."
            )


class Tool:
    """A single registered capability."""

    def __init__(
        self,
        *,
        name: str,
        description: str,
        parameters: dict[str, Any],
        permission: PermissionLevel,
        handler: Handler,
        requires_confirmation: bool = False,
        timeout: float | None = None,
        tags: tuple[str, ...] = (),
    ) -> None:
        self.name = name
        self.description = description
        self.parameters = parameters
        self.permission = permission
        self.handler = handler
        self.requires_confirmation = requires_confirmation
        self.timeout = timeout
        self.tags = tags

    def spec(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": self.parameters,
        }

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"Tool(name={self.name!r}, permission={self.permission.value!r})"


class ToolRegistry:
    """Central registry of tools, keyed by unique name."""

    def __init__(self, gate: PermissionGate | None = None) -> None:
        self._tools: dict[str, Tool] = {}
        self.gate = gate or PermissionGate()

    def register(self, tool: Tool) -> None:
        if not tool.name or not tool.name.replace("_", "").isalnum():
            raise ValueError(f"Invalid tool name: {tool.name!r}")
        if tool.name in self._tools:
            raise ValueError(f"Tool already registered: {tool.name}")
        self._tools[tool.name] = tool

    def get(self, name: str) -> Tool:
        try:
            return self._tools[name]
        except KeyError as exc:
            raise ToolNotFoundError(f"Unknown tool: {name}") from exc

    def __contains__(self, name: object) -> bool:
        return name in self._tools

    def __len__(self) -> int:
        return len(self._tools)

    def names(self) -> list[str]:
        return sorted(self._tools)

    def all(self) -> list[Tool]:
        return [self._tools[name] for name in self.names()]

    def by_tag(self, tag: str) -> list[Tool]:
        return [tool for tool in self.all() if tag in tool.tags]

    def specs(self) -> list[dict[str, Any]]:
        return [tool.spec() for tool in self.all()]

    def execute(
        self,
        name: str,
        arguments: dict[str, Any],
        context: ToolContext,
    ) -> ToolResult:
        """Validate, authorize, run and time a tool call.

        Never raises for ordinary failures: a failed ToolResult is returned so
        the agent can observe the failure and recover.
        """
        started = time.perf_counter()
        try:
            tool = self.get(name)
            validated = validate_arguments(tool.parameters, arguments)
            self.gate.check(tool)
        except ToolError as exc:
            return ToolResult(ok=False, error=str(exc))

        timeout = tool.timeout or context.settings.tool_timeout
        executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
        try:
            future = executor.submit(tool.handler, validated, context)
            result = future.result(timeout=timeout)
        except concurrent.futures.TimeoutError:
            result = ToolResult(ok=False, error=f"Tool timed out after {timeout:.0f}s")
        except Exception as exc:  # defensive: handlers should catch their own errors
            result = ToolResult(ok=False, error=f"{type(exc).__name__}: {exc}")
        finally:
            executor.shutdown(wait=False, cancel_futures=True)

        result.duration_ms = int((time.perf_counter() - started) * 1000)
        return result


def build_registry() -> ToolRegistry:
    """Build the default registry with the Milestone 1 tools."""
    from scout.tools import applications, filesystem, system

    registry = ToolRegistry()
    system.register(registry)
    filesystem.register(registry)
    applications.register(registry)
    return registry
