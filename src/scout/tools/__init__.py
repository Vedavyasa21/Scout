"""Tool package: expose the registry contract and approval types."""

from scout.security.policy import (
    AllowAllApprover,
    ApprovalDecision,
    ApprovalRequest,
    Approver,
    DenyAllApprover,
    PolicyEngine,
)
from scout.tools.base import (
    PermissionDeniedError,
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
    "AllowAllApprover",
    "ApprovalDecision",
    "ApprovalRequest",
    "Approver",
    "DenyAllApprover",
    "PermissionDeniedError",
    "PermissionLevel",
    "PolicyEngine",
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
