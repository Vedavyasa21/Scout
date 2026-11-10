"""Typer application: commands, interactive session and progress rendering."""

from __future__ import annotations

import platform
import sys

import typer
from rich.table import Table

from scout import __version__
from scout.agent import Agent, AgentCancelled, AgentHooks
from scout.cli.approval import RichApprover
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
from scout.cli.streaming import StreamRenderer, render_streamed_footer, run_streaming
from scout.config import (
    ConfigurationError,
    Settings,
    global_env_file,
    load_settings,
    save_api_key,
)
from scout.logging import configure_logging
from scout.providers import ModelProvider, ProviderError, Turn, UserTurn, create_provider
from scout.security.audit import AuditLedger
from scout.security.policy import AllowAllApprover
from scout.tools import build_registry
from scout.voice import VoiceError, record_wav, transcribe_wav

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


def _prompt_for_key(settings: Settings) -> Settings:
    """First-run onboarding: ask for the user's own key and store it per user."""
    if not sys.stdin.isatty():
        _fail(
            "No API key configured. Run `scout init` first, or set SCOUT_API_KEY. "
            "Get a key at https://aistudio.google.com/apikey"
        )
    console.print(
        "[bold cyan]Scout needs your own Gemini API key.[/bold cyan]\n"
        "[dim]Create one at https://aistudio.google.com/apikey — it is stored only "
        "on this computer and is never sent anywhere except Google.[/dim]"
    )
    key = typer.prompt("Paste your Gemini API key", hide_input=True)
    if not key.strip():
        _fail("No API key entered. Run `scout init` when you have one.")
    try:
        path = save_api_key(key)
    except ConfigurationError as exc:
        _fail(str(exc))
    where = "Windows Credential Manager" if path is None else str(path)
    console.print(f"[green]Saved[/green] your key to [bold]{where}[/bold]\n")
    return settings.model_copy(update={"api_key": key.strip()})


def _load(*, prompt_for_key: bool = True) -> Settings:
    try:
        settings = load_settings()
    except ConfigurationError as exc:
        _fail(str(exc))
    configure_logging(settings)
    if prompt_for_key and not settings.api_key.strip():
        settings = _prompt_for_key(settings)
    return settings


def _build_agent(settings: Settings, hooks: AgentHooks) -> tuple[Agent, ModelProvider]:
    try:
        provider = create_provider(settings)
    except ConfigurationError as exc:
        _fail(str(exc))
    except ProviderError as exc:
        _fail(str(exc))
    registry = build_registry()
    if settings.full_access:
        console.print(
            "[bold yellow]Warning:[/bold yellow] full access is enabled by configuration; "
            "WRITE/EXECUTE/HIGH_RISK tools run without confirmation. "
            "Set SCOUT_FULL_ACCESS=false to restore approvals.\n"
        )
    approver = (
        AllowAllApprover() if getattr(settings, "full_access", False) else RichApprover(console)
    )
    agent = Agent(
        provider=provider,
        registry=registry,
        settings=settings,
        hooks=hooks,
        approver=approver,
    )
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

    streamer = StreamRenderer(console)
    try:
        result = run_streaming(agent, request, streamer=streamer)
    except ProviderError as exc:
        _fail(str(exc))
    except AgentCancelled:
        console.print("[yellow]stopped[/yellow]")
        raise typer.Exit(130) from None
    except KeyboardInterrupt:
        console.print("\n[dim]interrupted[/dim]")
        raise typer.Exit(130) from None

    if streamer.streamed:
        render_streamed_footer(console, result)
    else:
        render_answer(
            result.text,
            duration_ms=result.duration_ms,
            model_ms=result.model_ms,
            tools=result.succeeded_tools,
        )
    if not result.completed:
        raise typer.Exit(1)


@app.command()
def init(
    check: bool = typer.Option(
        True, "--check/--no-check", help="Verify the key with one small provider request."
    ),
) -> None:
    """Save your own Gemini API key in a per-user config file (safe for open source)."""
    settings = _load(prompt_for_key=False)
    console.print(
        "[bold cyan]Set up Scout[/bold cyan]\n"
        "[dim]Scout is open source and never ships a shared key. Use your own from "
        "https://aistudio.google.com/apikey[/dim]"
    )
    existing = bool(settings.api_key.strip())
    if existing:
        console.print("[dim]A key is already configured. Press Enter to keep it.[/dim]")
    key = typer.prompt(
        "Paste your Gemini API key", hide_input=True, default="", show_default=False
    ).strip()
    if key:
        try:
            path = save_api_key(key)
        except ConfigurationError as exc:
            _fail(str(exc))
        settings = settings.model_copy(update={"api_key": key})
        where = "Windows Credential Manager" if path is None else str(path)
        console.print(f"[green]Saved[/green] your key to [bold]{where}[/bold]")
    elif existing:
        console.print("[dim]Keeping the existing key.[/dim]")
    else:
        _fail("No API key entered. Run `scout init` again when you have one.")

    if not check:
        return
    try:
        provider = create_provider(settings)
        text = provider.verify()
    except (ConfigurationError, ProviderError) as exc:
        _fail(f"The key was saved, but the check failed: {exc}")
    console.print(
        f"[green]Your key works.[/green] [dim]model replied: {text[:40] or '(empty)'}[/dim]\n"
        "[dim]Next: `scout` to chat, `scout voice` to talk, `scout overlay` for the icon.[/dim]"
    )


@app.command()
def status() -> None:
    """Show provider, model and workspace configuration."""
    settings = _load(prompt_for_key=False)
    registry = build_registry()

    table = Table(title=f"scout v{__version__}", header_style="bold cyan", show_header=False)
    table.add_column("key", style="bold")
    table.add_column("value")
    table.add_row("provider", settings.provider)
    table.add_row("model", settings.model)
    table.add_row("api key", "configured" if settings.api_key.strip() else "[red]missing[/red]")
    table.add_row("tools", str(len(registry)))
    table.add_row("step limit", str(settings.max_tool_steps))
    table.add_row(
        "full access",
        "[red]enabled (approvals bypassed)[/red]"
        if settings.full_access
        else "[green]prompted[/green]",
    )
    table.add_row("workspaces", "\n".join(str(w) for w in settings.workspaces))
    table.add_row("log file", str(settings.resolved_log_file))
    table.add_row("audit file", str(settings.resolved_audit_file))
    table.add_row("python", platform.python_version())
    table.add_row("platform", f"{platform.system()} {platform.release()}")
    console.print(table)


@app.command()
def tools() -> None:
    """List every registered tool and its permission level."""
    _load(prompt_for_key=False)
    render_tools_table(build_registry().all())


@app.command()
def audit(
    limit: int = typer.Option(20, "--limit", min=1, max=500, help="Number of recent records."),
) -> None:
    """Show recent approval and tool-execution audit records."""
    settings = _load(prompt_for_key=False)
    records = AuditLedger(settings.resolved_audit_file, enabled=settings.audit_enabled).read_recent(
        limit
    )
    if not records:
        console.print(f"[dim]No audit records yet at {settings.resolved_audit_file}[/dim]")
        return
    table = Table(title="scout audit", header_style="bold cyan")
    for column in ("time", "event", "tool", "decision", "ok", "detail"):
        table.add_column(column)
    for record in records:
        table.add_row(
            str(record.get("ts", ""))[11:19],
            str(record.get("event", "")),
            str(record.get("tool", "")),
            str(record.get("decision", "")),
            "" if "ok" not in record else ("yes" if record.get("ok") else "no"),
            str(record.get("error", record.get("reason", "")))[:70],
        )
    console.print(table)


@app.command(name="config")
def show_config() -> None:
    """Show the resolved configuration with secrets masked."""
    settings = _load(prompt_for_key=False)

    table = Table(title="scout configuration", header_style="bold cyan", show_header=False)
    table.add_column("key", style="bold")
    table.add_column("value")
    rows = {
        "provider": settings.provider,
        "model": settings.model,
        "api key": "configured" if settings.api_key.strip() else "[red]missing[/red]",
        "user config": str(global_env_file()),
        "full access": "enabled" if settings.full_access else "disabled",
        "max tool steps": str(settings.max_tool_steps),
        "thinking budget": "default"
        if settings.thinking_budget < 0
        else str(settings.thinking_budget),
        "tool timeout (s)": str(settings.tool_timeout),
        "request timeout (s)": str(settings.request_timeout),
        "log level": settings.log_level,
        "log file": str(settings.resolved_log_file),
        "audit file": str(settings.resolved_audit_file),
        "denied tools": ", ".join(settings.denied_tools) or "none",
        "auto-approved tools": ", ".join(settings.auto_approve_tools) or "none",
        "egress allowlist": ", ".join(settings.egress_allowlist) or "any http(s) host",
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
    settings = _load(prompt_for_key=False)
    raise typer.Exit(run_doctor(settings, check_api=check_api))


@app.command()
def voice() -> None:
    """Talk to Scout: record a spoken request, transcribe it, then run it."""
    settings = _load()
    hooks = AgentHooks(
        on_model_call=render_model_call,
        on_tool_start=render_tool_start,
        on_tool_result=render_tool_result,
    )
    agent, provider = _build_agent(settings, hooks)

    console.print(
        "[bold cyan]Voice mode[/bold cyan] [dim]· speak after the prompt · say 'exit' to stop[/dim]"
    )
    history: list[Turn] = []
    while True:
        console.print("[dim]listening…[/dim]")
        try:
            recording = record_wav(
                max_seconds=settings.voice_max_seconds,
                silence_seconds=settings.voice_silence_seconds,
            )
        except VoiceError as exc:
            _fail(str(exc))
        except KeyboardInterrupt:
            console.print("\n[dim]stopped[/dim]")
            break
        if recording is None:
            console.print("[yellow]heard nothing[/yellow] [dim](try again)[/dim]")
            continue

        try:
            text = transcribe_wav(provider, recording)
        except ProviderError as exc:
            render_error(str(exc))
            continue
        console.print(f"[bold cyan]you[/bold cyan] [dim]›[/dim] {text}")
        if text.strip().lower().rstrip(".!?") in {"exit", "quit", "stop listening", "goodbye"}:
            break

        streamer = StreamRenderer(console)
        try:
            result = run_streaming(agent, text, history=history, streamer=streamer)
        except ProviderError as exc:
            render_error(str(exc))
            continue
        except AgentCancelled:
            console.print("[yellow]stopped[/yellow]")
            continue
        except KeyboardInterrupt:
            console.print("\n[dim]interrupted[/dim]")
            continue
        history = _trim_history(list(result.turns))
        if streamer.streamed:
            render_streamed_footer(console, result)
        else:
            render_answer(
                result.text,
                duration_ms=result.duration_ms,
                model_ms=result.model_ms,
                tools=result.succeeded_tools,
            )


@app.command()
def overlay(
    opacity: float | None = typer.Option(
        None, "--opacity", min=0.3, max=1.0, help="Icon opacity (default from config)."
    ),
    companion: bool = typer.Option(
        True, "--companion/--no-companion", help="Show the Scout character on the desktop."
    ),
) -> None:
    """Show the floating Scout icon (click to talk, right-click for menu)."""
    settings = _load()
    from scout.desktop.overlay import run_overlay

    try:
        run_overlay(settings, opacity=opacity, enable_companion=companion)
    except KeyboardInterrupt:
        console.print("[dim]overlay closed[/dim]")
    except ProviderError as exc:
        _fail(str(exc))
    except ConfigurationError as exc:
        _fail(str(exc))
    except Exception as exc:
        _fail(f"Could not open the overlay: {exc}")


@app.command()
def enable(
    wake_word: bool = typer.Option(
        True,
        "--wake-word/--no-wake-word",
        help="Listen continuously for 'Hey Scout' or custom code.",
    ),
    hotkey: bool = typer.Option(
        True, "--hotkey/--no-hotkey", help="Enable Windows global hotkey Ctrl+Alt+S."
    ),
    opacity: float | None = typer.Option(
        None, "--opacity", min=0.3, max=1.0, help="Overlay opacity."
    ),
    companion: bool = typer.Option(
        True, "--companion/--no-companion", help="Show the Scout character on the desktop."
    ),
) -> None:
    """Enable hands-free Scout assistant mode and launch overlay immediately listening."""
    settings = _load()
    from scout.desktop.overlay import run_overlay
    from scout.voice import trigger_custom_enable_hook

    trigger_custom_enable_hook()

    console.print(
        "[bold cyan]Scout Assistant Enabled[/bold cyan] [dim]· Voice & wake detection active[/dim]\n"
        "[dim]• Press [bold]Ctrl+Alt+S[/bold] anytime to talk (always local)\n"
        "• Wake-word audio stays local unless SCOUT_WAKE_CLOUD_FALLBACK=true\n"
        "• Overlay launched in active listening mode right now[/dim]\n"
    )
    try:
        run_overlay(
            settings,
            opacity=opacity,
            auto_listen=True,
            enable_wake_word=wake_word,
            enable_hotkey=hotkey,
            enable_companion=companion,
        )
    except KeyboardInterrupt:
        console.print("[dim]assistant mode stopped[/dim]")
    except ProviderError as exc:
        _fail(str(exc))
    except ConfigurationError as exc:
        _fail(str(exc))
    except Exception as exc:
        _fail(f"Could not open assistant mode: {exc}")


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
        if message.lower() in {"enable", "/enable"}:
            import subprocess

            from scout.voice import trigger_custom_enable_hook

            trigger_custom_enable_hook()
            cmd = [sys.executable, "-m", "scout", "enable"]
            subprocess.Popen(
                cmd,
                creationflags=subprocess.DETACHED_PROCESS if sys.platform == "win32" else 0,
                close_fds=True,
            )
            access_line = (
                "Full access active (tools run without confirmation prompts)"
                if settings.full_access
                else "Approvals enabled (WRITE/EXECUTE actions ask first)"
            )
            console.print(
                "[green]✓ Scout Assistant Mode enabled![/green]\n"
                "[dim]• Overlay started immediately in active listening mode\n"
                "• Press Ctrl+Alt+S anytime to interact; wake audio stays local\n"
                f"• {access_line}[/dim]"
            )
            continue
        if message.startswith("/"):
            command = message.lower()
            if command in {"/exit", "/quit"}:
                break
            if command == "/help":
                console.print(
                    "[dim]/enable  start hands-free assistant mode · /overlay  activate floating icon · /tools  list tools · /clear  clear conversation · /exit  quit[/dim]"
                )
                continue
            if command == "/overlay":
                import subprocess

                subprocess.Popen(
                    [sys.executable, "-m", "scout", "overlay"],
                    creationflags=subprocess.DETACHED_PROCESS if sys.platform == "win32" else 0,
                    close_fds=True,
                )
                console.print("[green]✓ Scout floating icon activated on your screen.[/green]")
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

        streamer = StreamRenderer(console)
        try:
            result = run_streaming(agent, message, history=history, streamer=streamer)
        except ProviderError as exc:
            render_error(str(exc))
            continue
        except AgentCancelled:
            console.print("[yellow]stopped[/yellow]")
            continue
        except KeyboardInterrupt:
            console.print("\n[dim]interrupted[/dim]")
            continue

        history = _trim_history(list(result.turns))
        if streamer.streamed:
            render_streamed_footer(console, result)
        else:
            render_answer(
                result.text,
                duration_ms=result.duration_ms,
                model_ms=result.model_ms,
                tools=result.succeeded_tools,
            )


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
