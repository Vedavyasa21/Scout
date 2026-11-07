"""Typer application: commands, interactive session and progress rendering."""

from __future__ import annotations

import platform
import sys

import typer
from rich.table import Table

from scout import __version__
from scout.agent import Agent, AgentHooks
from scout.cli.doctor import run_doctor
from scout.cli.render import (
    console,
    print_banner,
    render_answer,
    render_error,
    render_model_call,
    render_tool_result,
    render_tool_start,
    render_tools_table,
)
from scout.config import ConfigurationError, Settings, load_settings
from scout.logging import configure_logging
from scout.providers import ModelProvider, ProviderError, Turn, UserTurn, create_provider
from scout.tools import build_registry

app = typer.Typer(
    name="scout",
    help="Scout - a secure CLI AI agent for your computer.",
    add_completion=False,
    no_args_is_help=False,
    invoke_without_command=True,
    rich_markup_mode="rich",
)

MAX_HISTORY_TURNS = 40


def _fail(message: str) -> None:
    render_error(message)
    raise typer.Exit(1)


def _load() -> Settings:
    try:
        settings = load_settings()
    except ConfigurationError as exc:
        _fail(str(exc))
    configure_logging(settings)
    return settings


def _build_agent(settings: Settings, hooks: AgentHooks) -> tuple[Agent, ModelProvider]:
    try:
        provider = create_provider(settings)
    except ConfigurationError as exc:
        _fail(str(exc))
    except ProviderError as exc:
        _fail(str(exc))
    registry = build_registry()
    agent = Agent(provider=provider, registry=registry, settings=settings, hooks=hooks)
    return agent, provider


def _trim_history(turns: list[Turn]) -> list[Turn]:
    trimmed = turns[-MAX_HISTORY_TURNS:]
    while trimmed and not isinstance(trimmed[0], UserTurn):
        trimmed.pop(0)
    return trimmed


@app.callback(invoke_without_command=True)
def root(
    ctx: typer.Context,
    version: bool = typer.Option(
        False, "--version", "-V", is_eager=True, help="Show the Scout version and exit."
    ),
) -> None:
    """Start an interactive session when no subcommand is given."""
    if version:
        console.print(f"scout v{__version__}")
        raise typer.Exit()
    if ctx.invoked_subcommand is None:
        interactive()


@app.command()
def ask(
    request: str = typer.Argument(..., help="Natural-language request for Scout."),
) -> None:
    """Run one request and print the result."""
    if not request.strip():
        _fail("The request is empty.")

    settings = _load()
    hooks = AgentHooks(
        on_model_call=render_model_call,
        on_tool_start=render_tool_start,
        on_tool_result=render_tool_result,
    )
    agent, _provider = _build_agent(settings, hooks)

    try:
        result = agent.run(request)
    except ProviderError as exc:
        _fail(str(exc))
    except KeyboardInterrupt:
        console.print("\n[dim]interrupted[/dim]")
        raise typer.Exit(130) from None

    render_answer(result.text)
    if not result.completed:
        raise typer.Exit(1)


@app.command()
def status() -> None:
    """Show provider, model and workspace configuration."""
    settings = _load()
    registry = build_registry()

    table = Table(title=f"scout v{__version__}", header_style="bold cyan", show_header=False)
    table.add_column("key", style="bold")
    table.add_column("value")
    table.add_row("provider", settings.provider)
    table.add_row("model", settings.model)
    table.add_row("api key", "configured" if settings.api_key.strip() else "[red]missing[/red]")
    table.add_row("tools", str(len(registry)))
    table.add_row("step limit", str(settings.max_tool_steps))
    table.add_row("workspaces", "\n".join(str(w) for w in settings.workspaces))
    table.add_row("log file", str(settings.resolved_log_file))
    table.add_row("python", platform.python_version())
    table.add_row("platform", f"{platform.system()} {platform.release()}")
    console.print(table)


@app.command()
def tools() -> None:
    """List every registered tool and its permission level."""
    _load()
    render_tools_table(build_registry().all())


@app.command(name="config")
def show_config() -> None:
    """Show the resolved configuration with secrets masked."""
    settings = _load()

    table = Table(title="scout configuration", header_style="bold cyan", show_header=False)
    table.add_column("key", style="bold")
    table.add_column("value")
    rows = {
        "provider": settings.provider,
        "model": settings.model,
        "api key": "configured" if settings.api_key.strip() else "[red]missing[/red]",
        "max tool steps": str(settings.max_tool_steps),
        "tool timeout (s)": str(settings.tool_timeout),
        "request timeout (s)": str(settings.request_timeout),
        "log level": settings.log_level,
        "log file": str(settings.resolved_log_file),
        "workspaces": "\n".join(str(w) for w in settings.workspaces),
        "allowed apps": ", ".join(sorted(settings.allowed_apps)),
    }
    for key, value in rows.items():
        table.add_row(key, value)
    console.print(table)


@app.command()
def doctor(
    check_api: bool = typer.Option(
        False, "--check-api", help="Also make a minimal request to the configured provider."
    ),
) -> None:
    """Diagnose installation, configuration and connectivity."""
    settings = _load()
    raise typer.Exit(run_doctor(settings, check_api=check_api))


@app.command()
def interactive() -> None:
    """Chat with Scout in a persistent session (default command)."""
    settings = _load()
    hooks = AgentHooks(
        on_model_call=render_model_call,
        on_tool_start=render_tool_start,
        on_tool_result=render_tool_result,
    )
    agent, _provider = _build_agent(settings, hooks)
    registry = agent.registry

    console.print()
    print_banner(settings, len(registry))
    console.print("[dim]Type /help for commands, /exit to quit.[/dim]\n")

    history: list[Turn] = []
    while True:
        try:
            message = console.input("[bold cyan]you[/bold cyan] [dim]›[/dim] ").strip()
        except (EOFError, KeyboardInterrupt):
            console.print()
            break

        if not message:
            continue
        if message.startswith("/"):
            command = message.lower()
            if command in {"/exit", "/quit"}:
                break
            if command == "/help":
                console.print(
                    "[dim]/tools  list tools · /clear  clear conversation · /exit  quit[/dim]"
                )
                continue
            if command == "/tools":
                render_tools_table(registry.all())
                continue
            if command == "/clear":
                history = []
                console.print("[dim]conversation cleared[/dim]")
                continue
            console.print(f"[yellow]unknown command:[/yellow] {message} (try /help)")
            continue

        try:
            result = agent.run(message, history=history)
        except ProviderError as exc:
            render_error(str(exc))
            continue
        except KeyboardInterrupt:
            console.print("\n[dim]interrupted[/dim]")
            continue

        history = _trim_history(list(result.turns))
        render_answer(result.text)


def _configure_stdio() -> None:
    """Use UTF-8 on Windows consoles so Rich glyphs render instead of crashing."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (OSError, ValueError):  # pragma: no cover - best effort only
            continue


def main() -> None:
    """Console-script entry point."""
    _configure_stdio()
    try:
        app()
    except ConfigurationError as exc:  # defensive: surfaces instead of a traceback
        render_error(str(exc))
        sys.exit(1)
