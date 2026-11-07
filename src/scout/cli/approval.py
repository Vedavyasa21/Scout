"""Interactive approval prompt for tools that change the machine."""

from __future__ import annotations

import json
import sys

from rich.console import Console
from rich.panel import Panel
from rich.prompt import Prompt
from rich.text import Text

from scout.security.policy import ApprovalDecision, ApprovalRequest
from scout.security.policy import Approver as ApproverProtocol


class RichApprover(ApproverProtocol):
    """Ask the user before running a write, execute or high-risk tool.

    In a non-interactive process (piped stdin, CI) every request is denied so
    an unattended run can never approve itself.
    """

    def __init__(self, console: Console, interactive: bool | None = None) -> None:
        self.console = console
        self.interactive = sys.stdin.isatty() if interactive is None else interactive

    def approve(self, request: ApprovalRequest) -> ApprovalDecision:
        if not self.interactive:
            self.console.print(
                f"[yellow]confirmation required:[/yellow] {request.tool} "
                "[dim](no interactive terminal; denied)[/dim]"
            )
            return ApprovalDecision.DENY

        preview = json.dumps(request.arguments, ensure_ascii=False, default=str)
        if len(preview) > 700:
            preview = preview[:699] + "…"

        body = Text()
        body.append("permission: ", style="dim")
        body.append(request.permission.value, style="bold yellow")
        body.append("\n")
        body.append("action: ", style="dim")
        body.append(request.description)
        body.append("\n\narguments\n", style="dim")
        body.append(preview)
        title = (
            "Scout wants to control your computer" if request.is_high_risk else "Approval needed"
        )
        self.console.print(Panel(body, title=title, border_style="yellow", padding=(0, 1)))

        if request.is_high_risk:
            answer = Prompt.ask(
                "[bold]Type [cyan]allow[/cyan] to permit this high-risk action[/bold]",
                default="deny",
                console=self.console,
            )
            if answer.strip().lower() in {"allow", "yes", "y"}:
                return ApprovalDecision.ALLOW_ONCE
            self.console.print("[dim]denied[/dim]")
            return ApprovalDecision.DENY

        answer = Prompt.ask(
            "Allow? [bold]y[/bold]es / [bold]n[/bold]o / [bold]a[/bold]lways this session",
            choices=["y", "n", "a"],
            default="n",
            console=self.console,
        )
        return {
            "y": ApprovalDecision.ALLOW_ONCE,
            "a": ApprovalDecision.ALLOW_SESSION,
            "n": ApprovalDecision.DENY,
        }[answer]
