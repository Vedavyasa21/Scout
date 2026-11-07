"""Example: registering a read-only tool without changing the agent.

Usage:
    python examples/custom_tool.py <directory-inside-a-workspace>
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

from scout.config import load_settings
from scout.security import PathAccessError, ensure_existing_directory
from scout.tools import (
    PermissionLevel,
    Tool,
    ToolContext,
    ToolResult,
    build_registry,
)


def count_files(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
    try:
        root = ensure_existing_directory(args["path"], list(ctx.workspaces))
    except (PathAccessError, FileNotFoundError, NotADirectoryError) as exc:
        return ToolResult(ok=False, error=str(exc))

    files = [path for path in root.rglob("*") if path.is_file()]
    by_suffix: dict[str, int] = {}
    for path in files:
        key = path.suffix.lower() or "(none)"
        by_suffix[key] = by_suffix.get(key, 0) + 1

    top = sorted(by_suffix.items(), key=lambda item: item[1], reverse=True)[:10]
    return ToolResult(
        ok=True,
        data={
            "root": str(root),
            "total_files": len(files),
            "by_extension": [{"extension": ext, "count": count} for ext, count in top],
        },
    )


def main() -> int:
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else Path.cwd()
    settings = load_settings()
    registry = build_registry()

    registry.register(
        Tool(
            name="count_files",
            description="Count files under a directory and group them by extension.",
            parameters={
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "required": ["path"],
                "additionalProperties": False,
            },
            permission=PermissionLevel.READ,
            handler=count_files,
            tags=("example",),
        )
    )

    result = registry.execute(
        "count_files",
        {"path": str(target)},
        ToolContext.from_settings(settings),
    )
    print(result.to_json())
    return 0 if result.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
