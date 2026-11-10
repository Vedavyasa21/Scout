"""Desktop control tools: windows, keyboard, mouse, apps, volume, screenshots.

These tools can act outside the filesystem sandbox, so they are gated by the
approval policy: anything that injects input or changes the machine requires an
explicit user confirmation. Imports are lazy so a missing GUI dependency only
disables this module instead of breaking the whole CLI.
"""

from __future__ import annotations

import ctypes
import os
import time
import webbrowser
from pathlib import Path
from typing import Any
from urllib.parse import quote_plus, urlparse

from scout.perception import (
    PerceptionError,
    active_window_info,
    capture_screen,
    crop_region,
    ocr_image,
    prune_screenshots,
    save_screenshot,
    sensitive_title,
)
from scout.security.egress import EgressDeniedError, EgressPolicy
from scout.tools.base import (
    PermissionLevel,
    Tool,
    ToolContext,
    ToolError,
    ToolRegistry,
    ToolResult,
)


def _gui() -> Any:
    try:
        import pyautogui
    except Exception as exc:  # pragma: no cover - depends on host install
        raise ToolError("Desktop control is not installed. Run: pip install pyautogui") from exc
    pyautogui.FAILSAFE = True  # slam the pointer into a corner to abort
    pyautogui.PAUSE = 0.05
    return pyautogui


def _windows() -> Any:
    try:
        import pygetwindow as gw
    except Exception as exc:  # pragma: no cover - depends on host install
        raise ToolError("Window control is not installed. Run: pip install pygetwindow") from exc
    return gw


def _ok(data: Any) -> ToolResult:
    return ToolResult(ok=True, data=data)


def _fail(message: str) -> ToolResult:
    return ToolResult(ok=False, error=message)


def _title(window: Any) -> str:
    return (getattr(window, "title", "") or "").strip()


def _find_window(gw: Any, title: str | None) -> Any:
    if not title:
        active = gw.getActiveWindow()
        if active is None:
            raise ToolError("No active window found.")
        return active
    needle = title.strip().lower()
    matches = [w for w in gw.getAllWindows() if needle in _title(w).lower()]
    if not matches:
        raise ToolError(f"No visible window title contains '{title}'.")
    exact = [w for w in matches if _title(w).lower() == needle]
    if exact:
        return exact[0]
    if len(matches) > 1:
        titles = ", ".join(_title(w) for w in matches[:6])
        raise ToolError(
            f"'{title}' matches {len(matches)} windows: {titles}. Use a more specific title."
        )
    return matches[0]


# --------------------------------------------------------------------------- windows


def list_windows(_args: dict[str, Any], _ctx: ToolContext) -> ToolResult:
    gw = _windows()
    try:
        active = gw.getActiveWindow()
        active_title = _title(active) if active is not None else ""
    except Exception:
        active_title = ""

    windows: list[dict[str, Any]] = []
    for window in gw.getAllWindows():
        title = _title(window)
        if not title:
            continue
        try:
            visible = bool(window.visible)
            rect = {
                "x": int(window.left),
                "y": int(window.top),
                "width": int(window.width),
                "height": int(window.height),
            }
            minimized = bool(window.isMinimized)
        except Exception:
            visible, rect, minimized = True, {}, False
        if not visible:
            continue
        windows.append(
            {
                "title": title,
                "active": title == active_title,
                "minimized": minimized,
                "rect": rect,
            }
        )

    windows.sort(key=lambda item: (not item["active"], item["title"].lower()))
    return _ok({"windows": windows[:40], "count": len(windows), "truncated": len(windows) > 40})


def window_control(args: dict[str, Any], _ctx: ToolContext) -> ToolResult:
    gw = _windows()
    action = args["action"]
    try:
        window = _find_window(gw, args.get("title"))
    except ToolError as exc:
        return _fail(str(exc))
    try:
        if action == "focus":
            if getattr(window, "isMinimized", False):
                window.restore()
            window.activate()
        elif action == "minimize":
            window.minimize()
        elif action == "maximize":
            window.maximize()
        elif action == "restore":
            window.restore()
        else:  # pragma: no cover - schema restricts the enum
            return _fail(f"Unsupported action: {action}")
    except Exception as exc:
        return _fail(f"Could not {action} '{_title(window)}': {exc}")
    time.sleep(0.15)
    return _ok({"action": action, "title": _title(window)})


# --------------------------------------------------------------------------- input


def type_text(args: dict[str, Any], _ctx: ToolContext) -> ToolResult:
    gui = _gui()
    text = args["text"]
    unsupported = sorted({ch for ch in text if ord(ch) > 127})
    if unsupported:
        return _fail(
            "Typing currently supports ASCII only; unsupported characters: "
            + " ".join(repr(ch) for ch in unsupported[:8])
        )
    try:
        for char in text:
            if char == "\n":
                gui.press("enter")
            elif char == "\t":
                gui.press("tab")
            else:
                gui.write(char)
    except Exception as exc:
        return _fail(f"Typing failed: {exc}")
    return _ok({"typed_chars": len(text)})


def press_keys(args: dict[str, Any], _ctx: ToolContext) -> ToolResult:
    gui = _gui()
    keys = [str(key).strip().lower() for key in args["keys"] if str(key).strip()]
    if not keys:
        return _fail("At least one key is required.")
    invalid = [key for key in keys if key not in gui.KEYBOARD_KEYS]
    if invalid:
        return _fail(f"Unknown key name(s): {', '.join(invalid)}")
    try:
        gui.hotkey(*keys)
    except Exception as exc:
        return _fail(f"Key press failed: {exc}")
    return _ok({"keys": keys})


def click(args: dict[str, Any], _ctx: ToolContext) -> ToolResult:
    gui = _gui()
    width, height = gui.size()
    x, y = args["x"], args["y"]
    if not (0 <= x < width and 0 <= y < height):
        return _fail(f"Coordinates ({x}, {y}) are outside the {width}x{height} screen.")
    try:
        gui.moveTo(x, y, duration=0.1)
        gui.click(
            x=x,
            y=y,
            clicks=args.get("clicks", 1),
            interval=0.08,
            button=args.get("button", "left"),
        )
    except Exception as exc:
        return _fail(f"Click failed: {exc}")
    return _ok(
        {"x": x, "y": y, "button": args.get("button", "left"), "clicks": args.get("clicks", 1)}
    )


def scroll(args: dict[str, Any], _ctx: ToolContext) -> ToolResult:
    gui = _gui()
    if "x" in args and "y" in args:
        gui.moveTo(args["x"], args["y"], duration=0.1)
    try:
        gui.scroll(args["amount"])
    except Exception as exc:
        return _fail(f"Scroll failed: {exc}")
    return _ok({"amount": args["amount"]})


# --------------------------------------------------------------------------- screen & media


def screen_size(_args: dict[str, Any], _ctx: ToolContext) -> ToolResult:
    gui = _gui()
    width, height = gui.size()
    return _ok({"width": width, "height": height})


def screenshot(_args: dict[str, Any], ctx: ToolContext) -> ToolResult:
    patterns = list(ctx.settings.screenshot_blocked_titles)
    title = str(active_window_info().get("title", ""))
    blocked = sensitive_title(title, patterns)
    if blocked:
        return _fail(
            f"Screenshot blocked: the focused window matches the sensitive pattern "
            f"'{blocked}'. Switch to a non-sensitive window and try again."
        )

    try:
        image, masked = capture_screen(patterns)
        path = save_screenshot(image, ctx.settings.data_dir / "screenshots", "shot")
    except PerceptionError as exc:
        return _fail(str(exc))
    prune_screenshots(
        ctx.settings.data_dir / "screenshots",
        ctx.settings.screenshot_retention_days,
        ctx.settings.screenshot_max_files,
    )
    return _ok(
        {
            "path": str(path),
            "width": image.width,
            "height": image.height,
            "masked_windows": masked,
        }
    )


def screenshot_region(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
    region = {
        "x": args["x"],
        "y": args["y"],
        "width": args["width"],
        "height": args["height"],
    }
    patterns = list(ctx.settings.screenshot_blocked_titles)
    title = str(active_window_info().get("title", ""))
    blocked = sensitive_title(title, patterns)
    if blocked:
        return _fail(
            f"Screenshot blocked: the focused window matches the sensitive pattern "
            f"'{blocked}'. Switch to a non-sensitive window and try again."
        )

    try:
        image, masked = capture_screen(patterns)
        cropped = crop_region(image, region)
        path = save_screenshot(cropped, ctx.settings.data_dir / "screenshots", "region")
    except PerceptionError as exc:
        return _fail(str(exc))
    prune_screenshots(
        ctx.settings.data_dir / "screenshots",
        ctx.settings.screenshot_retention_days,
        ctx.settings.screenshot_max_files,
    )
    return _ok(
        {
            "path": str(path),
            "width": cropped.width,
            "height": cropped.height,
            "region": region,
            "masked_windows": masked,
        }
    )


def active_window(_args: dict[str, Any], _ctx: ToolContext) -> ToolResult:
    return _ok(active_window_info())


def read_screen_text(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
    """Capture (optionally a region), mask sensitive windows, then OCR locally."""
    region: dict[str, int] | None = None
    keys = ("x", "y", "width", "height")
    if any(key in args for key in keys):
        if not all(key in args for key in keys):
            return _fail("Provide x, y, width and height together for a region.")
        region = {key: args[key] for key in keys}

    patterns = list(ctx.settings.screenshot_blocked_titles)
    blocked = sensitive_title(str(active_window_info().get("title", "")), patterns)
    if blocked:
        return _fail(
            f"Screen reading blocked: the focused window matches the sensitive pattern '{blocked}'."
        )

    try:
        image, masked = capture_screen(patterns)
        target = crop_region(image, region) if region is not None else image
        path = save_screenshot(target, ctx.settings.data_dir / "screenshots", "ocr")
    except PerceptionError as exc:
        return _fail(str(exc))
    prune_screenshots(
        ctx.settings.data_dir / "screenshots",
        ctx.settings.screenshot_retention_days,
        ctx.settings.screenshot_max_files,
    )
    try:
        text = ocr_image(path)
    except PerceptionError as exc:
        return _fail(str(exc))
    return _ok(
        {
            "text": text[:20000],
            "truncated": len(text) > 20000,
            "path": str(path),
            "region": region,
            "masked_windows": masked,
        }
    )


def set_volume(args: dict[str, Any], _ctx: ToolContext) -> ToolResult:
    gui = _gui()
    action = args["action"]
    key = {"up": "volumeup", "down": "volumedown", "mute": "volumemute"}[action]
    steps = args.get("steps", 1) if action != "mute" else 1
    try:
        for _ in range(steps):
            gui.press(key)
    except Exception as exc:
        return _fail(f"Volume control failed: {exc}")
    return _ok({"action": action, "steps": steps})


# --------------------------------------------------------------------------- launching


def open_url(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
    url = args["url"].strip()
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return _fail("Only absolute http:// or https:// URLs are allowed.")
    try:
        EgressPolicy(ctx.settings.egress_allowlist).check(url)
    except EgressDeniedError as exc:
        return _fail(str(exc))
    try:
        opened = webbrowser.open(url, new=2)
    except Exception as exc:
        return _fail(f"Could not open the browser: {exc}")
    if not opened:
        return _fail("No browser accepted the URL.")
    return _ok({"opened": url})


def search_web(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
    query = args["query"].strip()
    if not query:
        return _fail("Search query cannot be empty.")
    url = f"https://www.google.com/search?q={quote_plus(query)}"
    try:
        EgressPolicy(ctx.settings.egress_allowlist).check(url)
    except EgressDeniedError as exc:
        return _fail(str(exc))
    try:
        opened = webbrowser.open(url, new=2)
    except Exception as exc:
        return _fail(f"Could not open browser for search: {exc}")
    if not opened:
        return _fail("No browser accepted the search URL.")
    return _ok({"opened": url, "query": query})


def start_menu_roots() -> list[Path]:
    """Locations that hold Start Menu shortcuts on Windows."""
    roots: list[Path] = []
    for env in ("ProgramData", "APPDATA", "LOCALAPPDATA"):
        base = os.environ.get(env)
        if not base:
            continue
        candidate = Path(base) / "Microsoft" / "Windows" / "Start Menu" / "Programs"
        if candidate.is_dir():
            roots.append(candidate)
    return roots


def find_shortcuts(query: str, roots: list[Path] | None = None) -> list[Path]:
    """Find .lnk/.url shortcuts whose name contains `query` (case-insensitive)."""
    needle = query.strip().lower()
    if len(needle) < 2:
        return []
    matches: list[Path] = []
    for root in roots if roots is not None else start_menu_roots():
        if not root.is_dir():
            continue
        for path in root.rglob("*"):
            if path.suffix.lower() not in {".lnk", ".url"}:
                continue
            if needle in path.stem.lower():
                matches.append(path)
                if len(matches) >= 40:
                    return matches
    return matches


def launch_app(args: dict[str, Any], _ctx: ToolContext) -> ToolResult:
    query = args["name"].strip()
    matches = find_shortcuts(query)
    if not matches:
        return _fail(
            f"No Start Menu shortcut matches '{query}'. Ask the user for the exact app name."
        )
    exact = [path for path in matches if path.stem.lower() == query.lower()]
    if len(exact) == 1:
        chosen = exact[0]
    elif len(matches) == 1:
        chosen = matches[0]
    else:
        names = ", ".join(sorted({path.stem for path in matches})[:8])
        return _fail(f"'{query}' matches several apps: {names}. Use a more specific name.")
    try:
        os.startfile(chosen)  # type: ignore[attr-defined]
    except Exception as exc:
        return _fail(f"Could not launch {chosen.stem}: {exc}")
    return _ok({"application": chosen.stem, "shortcut": str(chosen)})


def lock_screen(_args: dict[str, Any], _ctx: ToolContext) -> ToolResult:
    if os.name != "nt":
        return _fail("Screen locking is only implemented on Windows.")
    try:
        ctypes.windll.user32.LockWorkStation()  # type: ignore[attr-defined]
    except Exception as exc:
        return _fail(f"Could not lock the screen: {exc}")
    return _ok({"locked": True})


# --------------------------------------------------------------------------- registration


def _schema(properties: dict[str, Any], required: list[str] | None = None) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": properties,
        "required": required or [],
        "additionalProperties": False,
    }


TITLE = {"type": "string", "maxLength": 200, "minLength": 1}


def register(registry: ToolRegistry) -> None:
    """Register desktop tools on `registry`."""
    registry.register(
        Tool(
            name="list_windows",
            description="List visible windows with their titles, active state and position.",
            parameters=_schema({}),
            permission=PermissionLevel.READ,
            handler=list_windows,
            tags=("desktop",),
        )
    )
    registry.register(
        Tool(
            name="active_window",
            description=(
                "Inspect the focused window: title, process name and pid, position, "
                "size and minimized state."
            ),
            parameters=_schema({}),
            permission=PermissionLevel.READ,
            handler=active_window,
            tags=("desktop", "perception"),
        )
    )
    registry.register(
        Tool(
            name="window_control",
            description=(
                "Focus, minimize, maximize or restore a window. Omit title to use the "
                "currently active window."
            ),
            parameters=_schema(
                {
                    "action": {
                        "type": "string",
                        "enum": ["focus", "minimize", "maximize", "restore"],
                    },
                    "title": TITLE,
                },
                ["action"],
            ),
            permission=PermissionLevel.EXECUTE,
            handler=window_control,
            requires_confirmation=True,
            tags=("desktop",),
        )
    )
    registry.register(
        Tool(
            name="type_text",
            description=(
                "Type text into the focused window as if using the keyboard. ASCII only; "
                "use this to fill search boxes or forms."
            ),
            parameters=_schema(
                {"text": {"type": "string", "minLength": 1, "maxLength": 2000}},
                ["text"],
            ),
            permission=PermissionLevel.EXECUTE,
            handler=type_text,
            requires_confirmation=True,
            tags=("desktop", "input"),
        )
    )
    registry.register(
        Tool(
            name="press_keys",
            description=(
                "Press a key or key combination in the focused window, e.g. "
                "['ctrl','s'], ['alt','tab'], ['enter']."
            ),
            parameters=_schema(
                {
                    "keys": {
                        "type": "array",
                        "items": {"type": "string", "minLength": 1, "maxLength": 20},
                        "minItems": 1,
                        "maxItems": 4,
                    }
                },
                ["keys"],
            ),
            permission=PermissionLevel.EXECUTE,
            handler=press_keys,
            requires_confirmation=True,
            tags=("desktop", "input"),
        )
    )
    registry.register(
        Tool(
            name="click",
            description="Move the mouse and click at absolute screen coordinates.",
            parameters=_schema(
                {
                    "x": {"type": "integer", "minimum": 0, "maximum": 20000},
                    "y": {"type": "integer", "minimum": 0, "maximum": 20000},
                    "button": {"type": "string", "enum": ["left", "right", "middle"]},
                    "clicks": {"type": "integer", "minimum": 1, "maximum": 3},
                },
                ["x", "y"],
            ),
            permission=PermissionLevel.EXECUTE,
            handler=click,
            requires_confirmation=True,
            tags=("desktop", "input"),
        )
    )
    registry.register(
        Tool(
            name="scroll",
            description="Scroll the focused window. Positive is up, negative is down.",
            parameters=_schema(
                {
                    "amount": {"type": "integer", "minimum": -20, "maximum": 20},
                    "x": {"type": "integer", "minimum": 0, "maximum": 20000},
                    "y": {"type": "integer", "minimum": 0, "maximum": 20000},
                },
                ["amount"],
            ),
            permission=PermissionLevel.EXECUTE,
            handler=scroll,
            requires_confirmation=True,
            tags=("desktop", "input"),
        )
    )
    registry.register(
        Tool(
            name="screen_size",
            description="Get the primary screen resolution.",
            parameters=_schema({}),
            permission=PermissionLevel.READ,
            handler=screen_size,
            tags=("desktop",),
        )
    )
    registry.register(
        Tool(
            name="screenshot",
            description="Capture the screen to a PNG file and return its path and dimensions.",
            parameters=_schema({}),
            permission=PermissionLevel.LOW_RISK,
            handler=screenshot,
            tags=("desktop",),
        )
    )
    registry.register(
        Tool(
            name="screenshot_region",
            description=(
                "Capture a rectangular screen region to PNG. Sensitive inactive "
                "windows are masked; the focused window is refused if sensitive."
            ),
            parameters=_schema(
                {
                    "x": {"type": "integer", "minimum": 0, "maximum": 20000},
                    "y": {"type": "integer", "minimum": 0, "maximum": 20000},
                    "width": {"type": "integer", "minimum": 1, "maximum": 20000},
                    "height": {"type": "integer", "minimum": 1, "maximum": 20000},
                },
                ["x", "y", "width", "height"],
            ),
            permission=PermissionLevel.LOW_RISK,
            handler=screenshot_region,
            tags=("desktop", "perception"),
        )
    )
    registry.register(
        Tool(
            name="read_screen_text",
            description=(
                "Read visible text from the screen (or a rectangular region) using "
                "the local Windows OCR engine. Sensitive windows are masked/refused."
            ),
            parameters=_schema(
                {
                    "x": {"type": "integer", "minimum": 0, "maximum": 20000},
                    "y": {"type": "integer", "minimum": 0, "maximum": 20000},
                    "width": {"type": "integer", "minimum": 1, "maximum": 20000},
                    "height": {"type": "integer", "minimum": 1, "maximum": 20000},
                }
            ),
            permission=PermissionLevel.LOW_RISK,
            handler=read_screen_text,
            tags=("desktop", "perception", "ocr"),
        )
    )
    registry.register(
        Tool(
            name="set_volume",
            description="Turn the system volume up/down by steps, or mute it.",
            parameters=_schema(
                {
                    "action": {"type": "string", "enum": ["up", "down", "mute"]},
                    "steps": {"type": "integer", "minimum": 1, "maximum": 10},
                },
                ["action"],
            ),
            permission=PermissionLevel.LOW_RISK,
            handler=set_volume,
            tags=("desktop",),
        )
    )
    registry.register(
        Tool(
            name="open_url",
            description="Open an http/https URL in the default browser.",
            parameters=_schema(
                {"url": {"type": "string", "minLength": 8, "maxLength": 2000}},
                ["url"],
            ),
            permission=PermissionLevel.LOW_RISK,
            handler=open_url,
            tags=("desktop",),
        )
    )
    registry.register(
        Tool(
            name="search_web",
            description="Search the web for a query using Google in the default browser.",
            parameters=_schema(
                {"query": {"type": "string", "minLength": 1, "maxLength": 500}},
                ["query"],
            ),
            permission=PermissionLevel.LOW_RISK,
            handler=search_web,
            tags=("desktop",),
        )
    )
    registry.register(
        Tool(
            name="launch_app",
            description=(
                "Launch any installed application by name, resolved from Start Menu "
                "shortcuts (e.g. 'spotify', 'word', 'steam')."
            ),
            parameters=_schema(
                {"name": {"type": "string", "minLength": 2, "maxLength": 100}},
                ["name"],
            ),
            permission=PermissionLevel.EXECUTE,
            handler=launch_app,
            requires_confirmation=True,
            tags=("desktop", "launch"),
        )
    )
    registry.register(
        Tool(
            name="lock_screen",
            description="Lock the Windows session immediately.",
            parameters=_schema({}),
            permission=PermissionLevel.HIGH_RISK,
            handler=lock_screen,
            requires_confirmation=True,
            tags=("desktop",),
        )
    )
