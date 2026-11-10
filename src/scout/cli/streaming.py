"""Live token streaming for the terminal.

The agent emits model deltas on an `EventBus`; this renderer writes them as they
arrive when stdout is a real terminal. Piped output and tests keep the existing
single-panel rendering, so scripts and assertions stay stable.
"""

from __future__ import annotations

import asyncio
import signal
import threading
from collections.abc import Sequence

from rich.console import Console

from scout.agent import Agent, AgentResult
from scout.cli.render import console as default_console
from scout.control import EMERGENCY_STOP
from scout.events import Event, EventBus, EventType
from scout.providers import Turn


class StreamRenderer:
    """Render MODEL_CHUNK deltas and keep tool lines from colliding."""

    def __init__(self, console: Console | None = None, *, enabled: bool | None = None) -> None:
        self.console = console or default_console
        self.enabled = self.console.is_terminal if enabled is None else enabled
        self.streamed = False
        self._line_open = False

    def on_event(self, event: Event) -> None:
        if event.type is EventType.MODEL_CHUNK:
            if not self.enabled:
                return
            text = str(event.get("text", ""))
            if not text:
                return
            if not self.streamed:
                self.console.print()
                self.console.print("[bold cyan]scout[/bold cyan] ", end="")
                self.streamed = True
                self._line_open = True
            self.console.print(text, end="", markup=False, highlight=False)
            return

        if event.type in {
            EventType.MODEL_COMPLETED,
            EventType.TOOL_STARTED,
            EventType.AGENT_ERROR,
            EventType.AGENT_LIMIT,
        }:
            self._close_line()

    def _close_line(self) -> None:
        if self._line_open:
            self.console.print()
            self._line_open = False


def run_streaming(
    agent: Agent,
    message: str,
    *,
    history: Sequence[Turn] | None = None,
    streamer: StreamRenderer | None = None,
) -> AgentResult:
    """Run one request with token streaming. Synchronous CLI entry point."""
    renderer = streamer or StreamRenderer()
    bus = EventBus()
    unsubscribe = bus.subscribe(renderer.on_event)

    async def _run() -> AgentResult:
        try:
            return await agent.run_async(message, history=history, bus=bus)
        finally:
            renderer._close_line()
            unsubscribe()

    # Ctrl+C is the terminal emergency stop: cancel every active run, then let
    # the normal KeyboardInterrupt path unwind. Installed only on the main thread.
    previous = None
    if threading.current_thread() is threading.main_thread():
        previous = signal.getsignal(signal.SIGINT)

        def _on_interrupt(_signum, _frame):
            EMERGENCY_STOP.stop_all()
            raise KeyboardInterrupt

        signal.signal(signal.SIGINT, _on_interrupt)
    try:
        return asyncio.run(_run())
    except KeyboardInterrupt:
        # asyncio.run replaces SIGINT handling on 3.11+, so stop all registered
        # runs again here after its task cancellation has unwound.
        EMERGENCY_STOP.stop_all()
        raise
    finally:
        if previous is not None:
            signal.signal(signal.SIGINT, previous)


def render_streamed_footer(console: Console, result: AgentResult) -> None:
    """Close a streamed answer with timing instead of repeating the text."""
    parts = [f"{result.duration_ms / 1000:.1f}s"]
    if result.model_ms:
        parts.append(f"model {result.model_ms / 1000:.1f}s")
    successful = result.succeeded_tools
    if successful:
        parts.append(f"{successful} tool(s)")
    if result.failed_tools:
        parts.append(f"{result.failed_tools} failed")
    console.print(f"[green]✓[/green] [dim]{' · '.join(parts)}[/dim]")
