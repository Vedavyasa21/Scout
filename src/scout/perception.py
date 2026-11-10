"""Screen perception helpers: active window, region capture and privacy masks.

Imports for GUI libraries are lazy so a headless or missing-dependency system
can still import Scout. Masking happens on the captured image before it is
written to disk; a sensitive focused window is refused by the caller.
"""

from __future__ import annotations

import contextlib
import ctypes
import os
import shutil
import subprocess
import time
import uuid
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw


class PerceptionError(Exception):
    """Raised when screen perception is unavailable or fails."""


def _pyautogui() -> Any:
    try:
        import pyautogui
    except Exception as exc:  # pragma: no cover - depends on host install
        raise PerceptionError(
            "Screen capture is not installed. Run: pip install pyautogui"
        ) from exc
    pyautogui.FAILSAFE = True
    pyautogui.PAUSE = 0.05
    return pyautogui


def _windows() -> Any:
    try:
        import pygetwindow as gw
    except Exception as exc:  # pragma: no cover - depends on host install
        raise PerceptionError(
            "Window inspection is not installed. Run: pip install pygetwindow"
        ) from exc
    return gw


def _title(window: Any) -> str:
    return (getattr(window, "title", "") or "").strip()


def _rect(window: Any) -> dict[str, int]:
    try:
        return {
            "x": int(window.left),
            "y": int(window.top),
            "width": int(window.width),
            "height": int(window.height),
        }
    except Exception:
        return {}


def _foreground_pid() -> int | None:
    if os.name != "nt":
        return None
    try:
        from ctypes import wintypes

        user32 = ctypes.windll.user32  # type: ignore[attr-defined]
        hwnd = user32.GetForegroundWindow()
        if not hwnd:
            return None
        process_id = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(process_id))
        return int(process_id.value) or None
    except Exception:
        return None


def _process_name(pid: int | None) -> str:
    if pid is None:
        return ""
    try:
        import psutil

        return psutil.Process(pid).name()
    except Exception:
        return ""


def active_window_info() -> dict[str, Any]:
    """Return title, process, pid, rectangle and state for the focused window."""
    info: dict[str, Any] = {
        "title": "",
        "process": "",
        "pid": None,
        "rect": {},
        "minimized": False,
    }
    try:
        active = _windows().getActiveWindow()
    except Exception:
        active = None
    if active is not None:
        info["title"] = _title(active)
        info["rect"] = _rect(active)
        with contextlib.suppress(Exception):
            info["minimized"] = bool(active.isMinimized)
    pid = _foreground_pid()
    info["pid"] = pid
    info["process"] = _process_name(pid)
    return info


def sensitive_title(title: str, patterns: list[str] | tuple[str, ...]) -> str | None:
    """Return the matching privacy pattern for a window title, if any."""
    lowered = title.lower()
    for pattern in patterns:
        needle = str(pattern).strip().lower()
        if needle and needle in lowered:
            return pattern
    return None


def apply_privacy_masks(image: Image.Image, patterns: list[str] | tuple[str, ...]) -> int:
    """Paint black boxes over visible windows with sensitive titles.

    Returns the number of masked windows. The focused window is handled by the
    caller, which refuses the capture instead of masking the user's active work.
    """
    if not patterns:
        return 0
    try:
        windows = _windows().getAllWindows()
    except Exception:
        return 0
    draw = ImageDraw.Draw(image)
    width, height = image.size
    masked = 0
    for window in windows:
        title = _title(window)
        if not title or sensitive_title(title, patterns) is None:
            continue
        try:
            if not bool(window.visible):
                continue
        except Exception:
            pass
        rect = _rect(window)
        if not rect:
            continue
        left = max(0, rect["x"])
        top = max(0, rect["y"])
        right = min(width, rect["x"] + rect["width"])
        bottom = min(height, rect["y"] + rect["height"])
        if right <= left or bottom <= top:
            continue
        draw.rectangle((left, top, right, bottom), fill=(0, 0, 0))
        masked += 1
    return masked


def capture_screen(patterns: list[str] | tuple[str, ...]) -> tuple[Image.Image, int]:
    """Capture the primary screen with sensitive inactive windows masked."""
    gui = _pyautogui()
    try:
        image = gui.screenshot()
    except Exception as exc:
        raise PerceptionError(f"Screenshot failed: {exc}") from exc
    return image, apply_privacy_masks(image, patterns)


def crop_region(image: Image.Image, region: dict[str, int]) -> Image.Image:
    """Crop a validated, in-bounds rectangle from a screenshot."""
    x, y = region["x"], region["y"]
    width, height = region["width"], region["height"]
    if x < 0 or y < 0 or width <= 0 or height <= 0:
        raise PerceptionError("The screen region must have positive coordinates and dimensions.")
    if x + width > image.width or y + height > image.height:
        raise PerceptionError(f"The region is outside the {image.width}x{image.height} screen.")
    return image.crop((x, y, x + width, y + height))


def save_screenshot(image: Image.Image, folder: Path, prefix: str) -> Path:
    """Save a PNG and return its path."""
    folder.mkdir(parents=True, exist_ok=True)
    millis = int(time.time() * 1000) % 1000
    stamp = time.strftime("%Y%m%d-%H%M%S")
    path = folder / f"{prefix}-{stamp}-{millis:03d}.png"
    try:
        image.save(path)
    except Exception as exc:
        raise PerceptionError(f"Could not save the screenshot: {exc}") from exc
    return path


OCR_SCRIPT = r"""
$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [Text.Encoding]::UTF8
Add-Type -AssemblyName System.Runtime.WindowsRuntime
$null = [Windows.Storage.StorageFile, Windows.Storage, ContentType = WindowsRuntime]
$null = [Windows.Media.Ocr.OcrEngine, Windows.Foundation, ContentType = WindowsRuntime]
$null = [Windows.Graphics.Imaging.BitmapDecoder, Windows.Graphics.Imaging, ContentType = WindowsRuntime]
$asTask = ([System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {
    $_.Name -eq "AsTask" -and $_.IsGenericMethod -and $_.GetParameters().Count -eq 1 -and
    $_.GetParameters()[0].ParameterType.Name -like "IAsyncOperation*"
})[0]
function Await($task, $resultType) {
    $generic = $asTask.MakeGenericMethod($resultType)
    $net = $generic.Invoke($null, @($task))
    $net.Wait(-1) | Out-Null
    return $net.Result
}
$path = $env:SCOUT_OCR_PATH
$file = Await ([Windows.Storage.StorageFile]::GetFileFromPathAsync($path)) ([Windows.Storage.StorageFile])
$stream = Await ($file.OpenAsync([Windows.Storage.FileAccessMode]::Read)) ([Windows.Storage.Streams.IRandomAccessStream])
$decoder = Await ([Windows.Graphics.Imaging.BitmapDecoder]::CreateAsync($stream)) ([Windows.Graphics.Imaging.BitmapDecoder])
$bitmap = Await ($decoder.GetSoftwareBitmapAsync()) ([Windows.Graphics.Imaging.SoftwareBitmap])
$engine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromUserProfileLanguages()
if ($null -eq $engine) { throw "No Windows OCR language is installed." }
$result = Await ($engine.RecognizeAsync($bitmap)) ([Windows.Media.Ocr.OcrResult])
[Console]::Out.Write($result.Text)
"""


def ocr_image(path: Path) -> str:
    """Read text from an image with the local Windows OCR engine."""
    if os.name != "nt":
        raise PerceptionError("Screen OCR is currently implemented on Windows only.")
    powershell = shutil.which("powershell.exe") or shutil.which("pwsh")
    if powershell is None:
        raise PerceptionError("PowerShell is needed for local Windows OCR.")

    source = path
    temp: Path | None = None
    try:
        # Small regions are upscaled; the Windows engine needs enough pixels
        # around each glyph to recognize it.
        try:
            with Image.open(path) as original:
                image = original.convert("RGB")
                largest = max(image.size)
                if largest < 1200:
                    scale = min(6.0, 1200 / max(largest, 1))
                    image = image.resize(
                        (int(image.width * scale), int(image.height * scale)),
                        Image.Resampling.LANCZOS,
                    )
                    temp = path.with_name(f".ocr-{uuid.uuid4().hex}.png")
                    image.save(temp)
                    source = temp
        except OSError as exc:
            raise PerceptionError(f"Could not read the screenshot for OCR: {exc}") from exc

        environment = os.environ.copy()
        environment["SCOUT_OCR_PATH"] = str(source.expanduser().resolve())
        try:
            completed = subprocess.run(
                [
                    powershell,
                    "-NoProfile",
                    "-NonInteractive",
                    "-Command",
                    OCR_SCRIPT,
                ],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=25,
                env=environment,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise PerceptionError(f"Windows OCR did not finish: {exc}") from exc
        if completed.returncode != 0:
            detail = (completed.stderr or "").strip().splitlines()
            message = detail[-1] if detail else "unknown error"
            raise PerceptionError(f"Windows OCR failed: {message[:300]}")
        text = completed.stdout.replace("\r\n", "\n").strip()
        if not text:
            raise PerceptionError("Windows OCR found no readable text on the screen.")
        return text
    finally:
        if temp is not None:
            with contextlib.suppress(OSError):
                temp.unlink(missing_ok=True)


def prune_screenshots(folder: Path, keep_days: int, max_files: int) -> None:
    """Delete old or excess Scout captures; individual failures are ignored."""
    try:
        shots = sorted(
            [
                path
                for path in folder.glob("*.png")
                if path.name.startswith(("shot-", "region-", "ocr-"))
            ],
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )
    except OSError:
        return
    cutoff = time.time() - keep_days * 86400 if keep_days > 0 else 0.0
    for index, path in enumerate(shots):
        try:
            too_old = bool(cutoff and path.stat().st_mtime < cutoff)
            too_many = bool(max_files and index >= max_files)
            if too_old or too_many:
                path.unlink(missing_ok=True)
        except OSError:
            continue
