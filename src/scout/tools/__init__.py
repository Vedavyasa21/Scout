"""Tool package: expose the registry contract."""

from scout.tools.base import (
    PermissionDeniedError,
    PermissionGate,
    PermissionLevel,
    Tool,
    ToolArgumentError,
    ToolContext,
    ToolError,
    ToolNotFoundError,
    ToolRegistry,
    ToolResult,
    build_registry,
    validate_arguments,
)

__all__ = [
    "PermissionDeniedError",
    "PermissionGate",
    "PermissionLevel",
    "Tool",
    "ToolArgumentError",
    "ToolContext",
    "ToolError",
    "ToolNotFoundError",
    "ToolRegistry",
    "ToolResult",
    "build_registry",
    "validate_arguments",
]
