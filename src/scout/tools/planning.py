"""The `update_plan` tool: model-visible task planning and progress tracking."""

from __future__ import annotations

from typing import Any

from scout.tools.base import (
    PermissionLevel,
    Tool,
    ToolContext,
    ToolRegistry,
    ToolResult,
)

STATUSES = ("pending", "active", "done", "failed", "skipped")


def update_plan(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
    tracker = ctx.plan
    if tracker is None:
        return ToolResult(ok=False, error="Task planning is not available in this run.")
    steps = args.get("steps")
    if not isinstance(steps, list) or not steps:
        return ToolResult(ok=False, error="Provide at least one plan step.")
    cleaned: list[dict[str, Any]] = []
    for index, step in enumerate(steps, start=1):
        if not isinstance(step, dict):
            return ToolResult(ok=False, error=f"Plan step {index} must be an object.")
        description = str(step.get("description", "")).strip()
        if not description:
            return ToolResult(ok=False, error=f"Plan step {index} needs a description.")
        status = str(step.get("status", "pending")).strip().lower()
        if status not in STATUSES:
            return ToolResult(
                ok=False,
                error=f"Plan step {index} status must be one of {', '.join(STATUSES)}.",
            )
        cleaned.append({"description": description[:300], "status": status})

    plan = tracker.update(str(args.get("goal", "")).strip(), cleaned)
    snapshot = plan.snapshot()
    return ToolResult(
        ok=True,
        data={
            "goal": snapshot["goal"],
            "steps": snapshot["steps"],
            "outstanding": plan.outstanding,
        },
    )


def register(registry: ToolRegistry) -> None:
    registry.register(
        Tool(
            name="update_plan",
            description=(
                "Declare or update a short task plan (2-6 concrete steps) and keep each "
                "step's status current. Use this for multi-step work before acting."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "goal": {"type": "string", "maxLength": 500},
                    "steps": {
                        "type": "array",
                        "minItems": 1,
                        "maxItems": 8,
                        "items": {
                            "type": "object",
                            "properties": {
                                "description": {
                                    "type": "string",
                                    "minLength": 1,
                                    "maxLength": 300,
                                },
                                "status": {
                                    "type": "string",
                                    "enum": list(STATUSES),
                                },
                            },
                            "required": ["description"],
                            "additionalProperties": False,
                        },
                    },
                },
                "required": ["steps"],
                "additionalProperties": False,
            },
            permission=PermissionLevel.READ,
            handler=update_plan,
            tags=("planning",),
        )
    )
