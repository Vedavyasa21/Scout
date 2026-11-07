"""Tool registry: registration, validation, permissions and execution."""

from __future__ import annotations

import time

import pytest

from scout.tools import (
    PermissionDeniedError,
    PermissionLevel,
    Tool,
    ToolArgumentError,
    ToolContext,
    ToolNotFoundError,
    ToolRegistry,
    ToolResult,
    build_registry,
    validate_arguments,
)

SCHEMA = {
    "type": "object",
    "properties": {
        "path": {"type": "string", "minLength": 1},
        "limit": {"type": "integer", "minimum": 1, "maximum": 10},
        "mode": {"type": "string", "enum": ["fast", "safe"]},
        "flags": {"type": "array", "items": {"type": "boolean"}, "maxItems": 3},
    },
    "required": ["path"],
    "additionalProperties": False,
}


def _ok_handler(args: dict, _ctx: ToolContext) -> ToolResult:
    return ToolResult(ok=True, data=args)


def _tool(name: str = "echo", permission: PermissionLevel = PermissionLevel.READ) -> Tool:
    return Tool(
        name=name,
        description="echo",
        parameters=SCHEMA,
        permission=permission,
        handler=_ok_handler,
    )


class TestValidation:
    def test_valid_arguments_pass_through(self) -> None:
        result = validate_arguments(SCHEMA, {"path": "a", "limit": 3, "mode": "safe"})
        assert result == {"path": "a", "limit": 3, "mode": "safe"}

    def test_missing_required(self) -> None:
        with pytest.raises(ToolArgumentError, match="Missing required"):
            validate_arguments(SCHEMA, {})

    def test_unknown_argument_rejected(self) -> None:
        with pytest.raises(ToolArgumentError, match="Unknown argument"):
            validate_arguments(SCHEMA, {"path": "a", "hack": True})

    def test_type_mismatch(self) -> None:
        with pytest.raises(ToolArgumentError, match="must be integer"):
            validate_arguments(SCHEMA, {"path": "a", "limit": "5"})

    def test_bool_is_not_integer(self) -> None:
        with pytest.raises(ToolArgumentError, match="boolean"):
            validate_arguments(SCHEMA, {"path": "a", "limit": True})

    def test_enum_enforced(self) -> None:
        with pytest.raises(ToolArgumentError, match="one of"):
            validate_arguments(SCHEMA, {"path": "a", "mode": "turbo"})

    def test_numeric_bounds(self) -> None:
        with pytest.raises(ToolArgumentError, match=">="):
            validate_arguments(SCHEMA, {"path": "a", "limit": 0})
        with pytest.raises(ToolArgumentError, match="<="):
            validate_arguments(SCHEMA, {"path": "a", "limit": 11})

    def test_nested_item_types(self) -> None:
        with pytest.raises(ToolArgumentError, match="flags"):
            validate_arguments(SCHEMA, {"path": "a", "flags": [1, 2]})

    def test_arguments_must_be_object(self) -> None:
        with pytest.raises(ToolArgumentError):
            validate_arguments(SCHEMA, ["not", "a", "dict"])  # type: ignore[arg-type]


class TestRegistry:
    def test_register_and_lookup(self) -> None:
        registry = ToolRegistry()
        registry.register(_tool())
        assert "echo" in registry
        assert registry.get("echo").name == "echo"
        assert len(registry) == 1

    def test_duplicate_registration_rejected(self) -> None:
        registry = ToolRegistry()
        registry.register(_tool())
        with pytest.raises(ValueError, match="already registered"):
            registry.register(_tool())

    def test_invalid_name_rejected(self) -> None:
        registry = ToolRegistry()
        with pytest.raises(ValueError, match="Invalid tool name"):
            registry.register(_tool("bad name!"))

    def test_unknown_tool(self) -> None:
        with pytest.raises(ToolNotFoundError):
            ToolRegistry().get("ghost")

    def test_execute_unknown_tool_returns_error(self, settings) -> None:
        result = ToolRegistry().execute("ghost", {}, ToolContext.from_settings(settings))
        assert not result.ok
        assert "Unknown tool" in (result.error or "")

    def test_execute_validates_before_running(self, settings) -> None:
        registry = ToolRegistry()
        registry.register(_tool())
        result = registry.execute("echo", {}, ToolContext.from_settings(settings))
        assert not result.ok
        assert "Missing required" in (result.error or "")

    def test_read_tool_executes(self, settings) -> None:
        registry = ToolRegistry()
        registry.register(_tool())
        result = registry.execute("echo", {"path": "x"}, ToolContext.from_settings(settings))
        assert result.ok
        assert result.data == {"path": "x"}
        assert result.duration_ms >= 0

    @pytest.mark.parametrize(
        "level",
        [PermissionLevel.WRITE, PermissionLevel.EXECUTE, PermissionLevel.HIGH_RISK],
    )
    def test_privileged_tools_are_denied(self, settings, level: PermissionLevel) -> None:
        registry = ToolRegistry()
        registry.register(_tool(permission=level))
        result = registry.execute("echo", {"path": "x"}, ToolContext.from_settings(settings))
        assert not result.ok
        assert "permission" in (result.error or "").lower()

    def test_low_risk_tool_is_allowed(self, settings) -> None:
        registry = ToolRegistry()
        registry.register(_tool(permission=PermissionLevel.LOW_RISK))
        assert registry.execute("echo", {"path": "x"}, ToolContext.from_settings(settings)).ok

    def test_timeout_is_reported(self, settings) -> None:
        def slow(_args: dict, _ctx: ToolContext) -> ToolResult:
            time.sleep(0.5)
            return ToolResult(ok=True)

        registry = ToolRegistry()
        registry.register(
            Tool(
                name="slow",
                description="slow",
                parameters={"type": "object", "properties": {}},
                permission=PermissionLevel.READ,
                handler=slow,
                timeout=0.05,
            )
        )
        result = registry.execute("slow", {}, ToolContext.from_settings(settings))
        assert not result.ok
        assert "timed out" in (result.error or "")

    def test_handler_exception_becomes_result(self, settings) -> None:
        def boom(_args: dict, _ctx: ToolContext) -> ToolResult:
            raise RuntimeError("kaput")

        registry = ToolRegistry()
        registry.register(
            Tool(
                name="boom",
                description="boom",
                parameters={"type": "object", "properties": {}},
                permission=PermissionLevel.READ,
                handler=boom,
            )
        )
        result = registry.execute("boom", {}, ToolContext.from_settings(settings))
        assert not result.ok
        assert "kaput" in (result.error or "")

    def test_default_registry_contents(self) -> None:
        registry = build_registry()
        names = set(registry.names())
        assert {"get_os_info", "search_files", "launch_application"} <= names
        assert all(
            tool.permission in {PermissionLevel.READ, PermissionLevel.LOW_RISK}
            for tool in registry.all()
        )
        assert len(registry.specs()) == len(names)


def test_permission_gate_message(settings) -> None:
    gate = ToolRegistry().gate
    with pytest.raises(PermissionDeniedError):
        gate.check(_tool(permission=PermissionLevel.WRITE))
