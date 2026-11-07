"""Rich-based terminal rendering helpers."""

from __future__ import annotations

import json
from typing import Any

from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from scout import __version__
from scout.config import Settings
from scout.providers import ToolCall
from scout.tools import ToolResult


def create_console() -> Console:
    return Console(highlight=False, soft_wrap=True)


console = create_console()


def print_banner(settings: Settings, tool_count: int) -> None:
    text = Text()
    text.append("Scout", style="bold cyan")
    text.append(f" v{__version__}", style="dim")
    text.append("\n")
    text.append(f"{settings.provider} · {settings.model} · {tool_count} tools", style="dim")
    text.append("\n")
    text.append(f"workspace: {', '.join(str(w) for w in settings.workspaces)}", style="dim")
    console.print(Panel(text, border_style="cyan", padding=(0, 1)))


def _short_args(arguments: dict[str, Any], limit: int = 100) -> str:
    try:
        rendered = json.dumps(arguments, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        rendered = str(arguments)
    if len(rendered) > limit:
        rendered = rendered[: limit - 1] + "…"
    return rendered


def render_model_call(step: int) -> None:
    console.print(f"[dim]step {step} · thinking…[/dim]")


def render_tool_start(call: ToolCall) -> None:
    console.print(
        f"  [cyan]→[/cyan] [bold]{call.name}[/bold][dim]({_short_args(call.arguments)})[/dim]"
    )


def summarise_result(tool: str, result: ToolResult, limit: int = 120) -> str:
    if not result.ok:
        return result.error or "failed"
    data = result.data
    if isinstance(data, dict):
        if "cpu" in data and "memory" in data:
            parts = [
                f"CPU {data['cpu'].get('percent_total', '?')}%",
                f"RAM {data['memory'].get('percent_used', '?')}%",
            ]
            disks = data.get("disks") or []
            if disks:
                parts.append(f"{disks[0]['path']} {disks[0]['percent_used']}%")
            battery = data.get("battery") or {}
            if battery.get("present"):
                parts.append(f"battery {battery.get('percent', '?')}%")
            return " · ".join(parts)
        if "system" in data and "release" in data:
            return (
                f"{data['system']} {data['release']} · {data.get('cpu_count_logical', '?')} cores"
            )
        for key, label in (
            ("matches", "match(es)"),
            ("entries", "entr(ies)"),
            ("processes", "process(es)"),
            ("applications", "application(s)"),
            ("windows", "window(s)"),
        ):
            if isinstance(data.get(key), list):
                suffix = " (truncated)" if data.get("truncated") else ""
                return f"{len(data[key])} {label}{suffix}"
        for key in ("path", "resolved_path", "application", "opened"):
            if key in data and len(data) <= 4:
                return f"{key}={data[key]}"
    try:
        rendered = json.dumps(data, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        rendered = str(data)
    if len(rendered) > limit:
        rendered = rendered[: limit - 1] + "…"
    return rendered


def render_tool_result(call: ToolCall, result: ToolResult) -> None:
    summary = summarise_result(call.name, result)
    if result.ok:
        console.print(f"  [green]✓[/green] [dim]{summary} · {result.duration_ms} ms[/dim]")
    else:
        console.print(f"  [red]✗[/red] [red]{summary}[/red] [dim]· {result.duration_ms} ms[/dim]")


def render_answer(
    text: str,
    *,
    duration_ms: int | None = None,
    model_ms: int | None = None,
    tools: int = 0,
) -> None:
    body = text.strip() or "(empty response)"
    try:
        content: Any = Markdown(body, hyperlinks=False)
    except Exception:  # pragma: no cover - malformed markdown should never crash a run
        content = body
    subtitle = None
    if duration_ms is not None:
        parts = [f"{duration_ms / 1000:.1f}s"]
        if model_ms is not None:
            parts.append(f"model {model_ms / 1000:.1f}s")
        if tools:
            parts.append(f"{tools} tool(s)")
        subtitle = " · ".join(parts)
    console.print()
    console.print(Panel(content, title="scout", subtitle=subtitle, border_style="green"))


def render_error(message: str) -> None:
    console.print(f"[red]error:[/red] {message}")


def render_tools_table(tools: list[Any]) -> None:
    table = Table(title="Registered tools", header_style="bold cyan", expand=False)
    table.add_column("tool", style="bold", no_wrap=True)
    table.add_column("permission", no_wrap=True)
    table.add_column("confirm", justify="center")
    table.add_column("description", max_width=60)
    for tool in tools:
        color = "green" if tool.permission.value == "read" else "yellow"
        table.add_row(
            tool.name,
            f"[{color}]{tool.permission.value}[/{color}]",
            "yes" if tool.requires_confirmation else "no",
            tool.description,
        )
    console.print(table)
