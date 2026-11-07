"""Workspace boundary enforcement for filesystem operations.

All paths are resolved (symlinks and `..` included) before being compared
against the allowlisted workspaces, which prevents traversal and link escapes.
"""

from __future__ import annotations

from pathlib import Path


class PathAccessError(PermissionError):
    """Raised when a path falls outside every approved workspace."""


def resolve_candidate(raw: str | Path, base: Path | None = None) -> Path:
    """Resolve a user/model supplied path without requiring it to exist."""
    path = Path(raw).expanduser()
    if not path.is_absolute():
        path = (base or Path.cwd()) / path
    try:
        return path.resolve(strict=False)
    except OSError:
        return path.absolute()


def _is_within(child: Path, parent: Path) -> bool:
    try:
        child.relative_to(parent)
    except ValueError:
        return False
    return True


def ensure_within_workspaces(path: Path, workspaces: list[Path]) -> Path:
    """Return `path` if it is inside an approved workspace, else raise."""
    resolved = resolve_candidate(path)
    for workspace in workspaces:
        root = resolve_candidate(workspace)
        if _is_within(resolved, root):
            return resolved
    allowed = ", ".join(str(w) for w in workspaces) or "(none configured)"
    raise PathAccessError(f"Path '{resolved}' is outside the approved workspaces: {allowed}")


def ensure_existing_directory(raw: str | Path, workspaces: list[Path]) -> Path:
    path = ensure_within_workspaces(Path(raw), workspaces)
    if not path.exists():
        raise FileNotFoundError(f"Directory does not exist: {path}")
    if not path.is_dir():
        raise NotADirectoryError(f"Not a directory: {path}")
    return path


def ensure_existing_file(raw: str | Path, workspaces: list[Path]) -> Path:
    path = ensure_within_workspaces(Path(raw), workspaces)
    if not path.exists():
        raise FileNotFoundError(f"File does not exist: {path}")
    if not path.is_file():
        raise IsADirectoryError(f"Not a file: {path}")
    return path


REDACTED = "***redacted***"


def redact_secrets(text: str, secrets: list[str | None]) -> str:
    """Replace known secret values in text before logging or display."""
    result = text
    for secret in secrets:
        if secret and len(secret) >= 6:
            result = result.replace(secret, REDACTED)
    return result
