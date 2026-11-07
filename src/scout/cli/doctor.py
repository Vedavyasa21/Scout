"""`scout doctor`: environment and configuration diagnostics."""

from __future__ import annotations

import importlib.util
import os
import sys
from dataclasses import dataclass

from rich.table import Table

from scout.cli.render import console, render_error
from scout.config import Settings
from scout.providers import SUPPORTED_PROVIDERS, ProviderError, create_provider
from scout.tools.applications import AppNotFoundError, resolve_application

OK = "ok"
WARN = "warn"
FAIL = "fail"

_ICONS = {OK: "[green]✓[/green]", WARN: "[yellow]![/yellow]", FAIL: "[red]✗[/red]"}


@dataclass
class Check:
    name: str
    status: str
    detail: str


def _check_python() -> Check:
    version = sys.version_info
    if version >= (3, 11):
        return Check("Python version", OK, f"{version.major}.{version.minor}.{version.micro}")
    return Check(
        "Python version",
        FAIL,
        f"{version.major}.{version.minor} found; Scout requires 3.11 or newer",
    )


def _check_provider(settings: Settings) -> Check:
    if settings.provider.lower() in SUPPORTED_PROVIDERS:
        return Check("Provider", OK, settings.provider)
    return Check(
        "Provider",
        FAIL,
        f"'{settings.provider}' is not supported (supported: {', '.join(SUPPORTED_PROVIDERS)})",
    )


def _check_api_key(settings: Settings) -> Check:
    if settings.api_key.strip():
        return Check("API key", OK, "configured (value not displayed)")
    return Check(
        "API key",
        FAIL,
        "missing; run `scout init` (or set SCOUT_API_KEY) with your own Gemini key",
    )


def _check_sdk() -> Check:
    if importlib.util.find_spec("google.genai") is not None:
        return Check("google-genai SDK", OK, "installed")
    return Check("google-genai SDK", FAIL, "not installed; run pip install -e '.[dev]'")


def _check_workspaces(settings: Settings) -> list[Check]:
    checks: list[Check] = []
    writable = 0
    for workspace in settings.workspaces:
        if not workspace.is_dir():
            checks.append(Check("Workspace", FAIL, f"{workspace} is not a directory"))
            continue
        readable = os.access(workspace, os.R_OK)
        can_write = os.access(workspace, os.W_OK)
        writable += int(can_write)
        checks.append(
            Check(
                "Workspace",
                OK if readable else WARN,
                f"{workspace} (readable={readable}, writable={can_write})",
            )
        )
    if not writable:
        checks.append(
            Check("Workspace writes", WARN, "no workspace is writable; future write tools need one")
        )
    return checks


def _check_log(settings: Settings) -> Check:
    path = settings.resolved_log_file
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8"):
            pass
    except OSError as exc:
        return Check("Log file", WARN, f"cannot write {path}: {exc}")
    return Check("Log file", OK, str(path))


def _check_apps(settings: Settings) -> Check:
    found: list[str] = []
    missing: list[str] = []
    for app in settings.allowed_apps:
        try:
            resolve_application(app, settings.allowed_apps)
        except AppNotFoundError:
            missing.append(app)
        else:
            found.append(app)
    if not found:
        return Check("Allowlisted apps", WARN, "none found; launching is unavailable")
    detail = f"{len(found)} found"
    if missing:
        detail += f" · not on this system: {', '.join(missing)}"
    return Check("Allowlisted apps", OK, detail)


def _check_api(settings: Settings) -> Check:
    try:
        provider = create_provider(settings)
        text = provider.verify()
    except ProviderError as exc:
        return Check("API round trip", FAIL, str(exc))
    except Exception as exc:
        return Check("API round trip", FAIL, str(exc))
    return Check("API round trip", OK, f"model replied: {text[:40] or '(empty)'}")


def run_doctor(settings: Settings, check_api: bool = False) -> int:
    """Run diagnostics and return a process exit code."""
    checks = [
        _check_python(),
        _check_provider(settings),
        _check_api_key(settings),
        _check_sdk(),
        *_check_workspaces(settings),
        _check_log(settings),
        _check_apps(settings),
    ]
    if check_api:
        if not settings.api_key.strip():
            checks.append(Check("API round trip", FAIL, "skipped: no API key configured"))
        else:
            with console.status("[dim]contacting provider…[/dim]", spinner="dots"):
                checks.append(_check_api(settings))

    table = Table(title="scout doctor", header_style="bold cyan", expand=False)
    table.add_column("status", justify="center")
    table.add_column("check", style="bold")
    table.add_column("detail")
    for check in checks:
        table.add_row(_ICONS[check.status], check.name, check.detail)
    console.print(table)

    failures = [check for check in checks if check.status == FAIL]
    warnings = [check for check in checks if check.status == WARN]
    if failures:
        render_error(f"{len(failures)} check(s) failed, {len(warnings)} warning(s)")
        return 1
    console.print(
        "[green]All checks passed.[/green]"
        + (f" [yellow]{len(warnings)} warning(s).[/yellow]" if warnings else "")
    )
    return 0
