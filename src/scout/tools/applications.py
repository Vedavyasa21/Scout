"""Launching allowlisted applications and folders."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import psutil

from scout.security import PathAccessError, ensure_existing_directory
from scout.tools.base import PermissionLevel, Tool, ToolContext, ToolRegistry, ToolResult

# Common install locations used when an executable is not on PATH.
_WINDOWS_FALLBACKS: dict[str, tuple[str, ...]] = {
    "notepad": (r"{system32}\notepad.exe",),
    "calc": (r"{system32}\calc.exe",),
    "mspaint": (r"{system32}\mspaint.exe",),
    "explorer": (r"{windows}\explorer.exe",),
    "taskmgr": (r"{system32}\Taskmgr.exe",),
    "control": (r"{system32}\control.exe",),
    "cmd": (r"{system32}\cmd.exe",),
    "powershell": (r"{system32}\WindowsPowerShell\v1.0\powershell.exe",),
}


class AppNotAllowedError(PermissionError):
    """Raised when an application is not on the allowlist."""


class AppNotFoundError(FileNotFoundError):
    """Raised when an allowlisted application cannot be located."""


def _normalise(name: str) -> str:
    base = Path(name).name.lower()
    return base[:-4] if base.endswith(".exe") else base


def _allowlist_entry(app: str, allowlist: list[str]) -> str:
    key = _normalise(app)
    for entry in allowlist:
        if _normalise(entry) == key:
            return entry
    raise AppNotAllowedError(
        f"'{app}' is not on the application allowlist. "
        f"Allowed: {', '.join(sorted(_normalise(e) for e in allowlist))}"
    )


def resolve_application(app: str, allowlist: list[str]) -> Path:
    """Resolve an allowlisted app name to an existing executable path."""
    entry = _allowlist_entry(app, allowlist)

    candidate = Path(entry).expanduser()
    if candidate.is_absolute():
        if candidate.exists():
            return candidate
        raise AppNotFoundError(f"Allowlisted application path does not exist: {candidate}")

    found = shutil.which(entry) or shutil.which(f"{entry}.exe")
    if found:
        return Path(found)

    if sys.platform == "win32":
        windows_root = Path(os.environ.get("SYSTEMROOT", r"C:\Windows"))
        variables = {
            "system32": str(windows_root / "System32"),
            "windows": str(windows_root),
        }
        for template in _WINDOWS_FALLBACKS.get(_normalise(entry), ()):
            path = Path(template.format(**variables))
            if path.exists():
                return path

    raise AppNotFoundError(f"Could not locate allowlisted application '{entry}' on this system")


def _start_detached(path: Path) -> None:
    """Launch an app without a shell and without blocking Scout."""
    if sys.platform == "win32":
        os.startfile(str(path))  # noqa: S606 - resolved allowlisted path only
        return
    subprocess.Popen(  # noqa: S603 - argument array, resolved allowlisted path, no shell
        [str(path)],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
        shell=False,
    )


def launch_application(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
    app = args["application"]
    try:
        path = resolve_application(app, ctx.settings.allowed_apps)
        _start_detached(path)
    except AppNotAllowedError as exc:
        return ToolResult(ok=False, error=str(exc))
    except AppNotFoundError as exc:
        return ToolResult(ok=False, error=str(exc))
    except OSError as exc:
        return ToolResult(ok=False, error=f"Launch failed: {exc}")

    return ToolResult(
        ok=True,
        data={
            "application": app,
            "resolved_path": str(path),
            "launched": True,
            "note": "The OS accepted the launch request; Scout cannot confirm window state.",
        },
    )


def is_application_installed(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
    app = args["application"]
    allowlist = ctx.settings.allowed_apps
    if _normalise(app) not in {_normalise(entry) for entry in allowlist}:
        return ToolResult(
            ok=True,
            data={
                "application": app,
                "allowlisted": False,
                "installed": None,
                "note": "Not on the allowlist; installation was not probed.",
            },
        )
    try:
        path = resolve_application(app, allowlist)
    except (AppNotAllowedError, AppNotFoundError):
        return ToolResult(
            ok=True, data={"application": app, "allowlisted": True, "installed": False}
        )
    return ToolResult(
        ok=True,
        data={
            "application": app,
            "allowlisted": True,
            "installed": True,
            "resolved_path": str(path),
        },
    )


def list_running_applications(_args: dict[str, Any], _ctx: ToolContext) -> ToolResult:
    try:
        counts: dict[str, int] = {}
        total = 0
        for proc in psutil.process_iter(["name"]):
            name = proc.info.get("name") or "unknown"
            counts[name] = counts.get(name, 0) + 1
            total += 1
    except Exception as exc:
        return ToolResult(ok=False, error=f"Could not list running applications: {exc}")

    top = sorted(counts.items(), key=lambda item: item[1], reverse=True)[:60]
    return ToolResult(
        ok=True,
        data={
            "total_processes": total,
            "applications": [{"name": name, "processes": count} for name, count in top],
        },
    )


def open_folder(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
    try:
        folder = ensure_existing_directory(args["path"], list(ctx.workspaces))
        _start_detached(folder)
    except PathAccessError as exc:
        return ToolResult(ok=False, error=str(exc))
    except (FileNotFoundError, NotADirectoryError) as exc:
        return ToolResult(ok=False, error=str(exc))
    except OSError as exc:
        return ToolResult(ok=False, error=f"Could not open folder: {exc}")

    return ToolResult(
        ok=True,
        data={"path": str(folder), "opened": True},
    )


def register(registry: ToolRegistry) -> None:
    registry.register(
        Tool(
            name="launch_application",
            description=(
                "Launch an application that appears on Scout's allowlist, "
                "for example 'notepad', 'calc' or 'code'."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "application": {
                        "type": "string",
                        "description": "Allowlisted application name or executable path.",
                        "minLength": 1,
                        "maxLength": 260,
                    },
                },
                "required": ["application"],
                "additionalProperties": False,
            },
            permission=PermissionLevel.LOW_RISK,
            handler=launch_application,
            tags=("applications",),
        )
    )
    registry.register(
        Tool(
            name="is_application_installed",
            description="Check whether an allowlisted application is installed and where it lives.",
            parameters={
                "type": "object",
                "properties": {
                    "application": {"type": "string", "minLength": 1, "maxLength": 260},
                },
                "required": ["application"],
                "additionalProperties": False,
            },
            permission=PermissionLevel.READ,
            handler=is_application_installed,
            tags=("applications",),
        )
    )
    registry.register(
        Tool(
            name="list_running_applications",
            description="List running application process names with process counts.",
            parameters={"type": "object", "properties": {}, "additionalProperties": False},
            permission=PermissionLevel.READ,
            handler=list_running_applications,
            tags=("applications",),
        )
    )
    registry.register(
        Tool(
            name="open_folder",
            description="Open an approved workspace folder in the system file manager.",
            parameters={
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Directory to open. Must be inside an approved workspace.",
                    },
                },
                "required": ["path"],
                "additionalProperties": False,
            },
            permission=PermissionLevel.LOW_RISK,
            handler=open_folder,
            tags=("applications",),
        )
    )
