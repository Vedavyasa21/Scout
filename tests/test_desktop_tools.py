"""Desktop tools: input guardrails, window selection and app resolution."""

from __future__ import annotations

from pathlib import Path

from scout.tools import PermissionLevel, ToolContext, build_registry, desktop


class FakeGui:
    KEYBOARD_KEYS = [
        "enter",
        "tab",
        "ctrl",
        "alt",
        "s",
        "a",
        "volumeup",
        "volumedown",
        "volumemute",
    ]

    def __init__(self) -> None:
        self.typed: list[str] = []
        self.pressed: list[str] = []
        self.hotkeys: list[tuple[str, ...]] = []
        self.clicks: list[dict] = []
        self.scrolled: list[int] = []

    def write(self, char: str) -> None:
        self.typed.append(char)

    def press(self, key: str) -> None:
        self.pressed.append(key)

    def hotkey(self, *keys: str) -> None:
        self.hotkeys.append(keys)

    def size(self) -> tuple[int, int]:
        return (1920, 1080)

    def moveTo(self, x: int, y: int, duration: float = 0.0) -> None:
        return None

    def click(self, **kwargs) -> None:
        self.clicks.append(kwargs)

    def scroll(self, amount: int) -> None:
        self.scrolled.append(amount)


class FakeWindow:
    def __init__(
        self,
        title: str,
        *,
        active: bool = False,
        minimized: bool = False,
        visible: bool = True,
    ) -> None:
        self.title = title
        self.isActive = active
        self.isMinimized = minimized
        self.visible = visible
        self.left, self.top, self.width, self.height = 0, 0, 800, 600
        self.actions: list[str] = []

    def activate(self) -> None:
        self.actions.append("activate")

    def restore(self) -> None:
        self.actions.append("restore")

    def minimize(self) -> None:
        self.actions.append("minimize")

    def maximize(self) -> None:
        self.actions.append("maximize")


class FakeGw:
    def __init__(self, windows: list[FakeWindow], active: FakeWindow | None = None) -> None:
        self.windows = windows
        self.active = active or windows[0]

    def getAllWindows(self) -> list[FakeWindow]:
        return self.windows

    def getActiveWindow(self) -> FakeWindow:
        return self.active


def _context(settings) -> ToolContext:
    return ToolContext.from_settings(settings)


def test_list_windows_puts_active_first(settings, monkeypatch) -> None:
    editor = FakeWindow("Editor", active=True)
    browser = FakeWindow("Browser")
    hidden = FakeWindow("Hidden", visible=False)
    monkeypatch.setattr(
        desktop, "_windows", lambda: FakeGw([browser, hidden, editor], active=editor)
    )

    result = desktop.list_windows({}, _context(settings))
    assert result.ok
    assert result.data["windows"][0]["title"] == "Editor"
    assert result.data["windows"][0]["active"] is True
    assert all(item["title"] != "Hidden" for item in result.data["windows"])


def test_window_control_focuses_match(settings, monkeypatch) -> None:
    editor = FakeWindow("Notes - Editor", minimized=True)
    monkeypatch.setattr(desktop, "_windows", lambda: FakeGw([editor]))

    result = desktop.window_control({"action": "focus", "title": "editor"}, _context(settings))
    assert result.ok
    assert editor.actions == ["restore", "activate"]


def test_window_control_reports_ambiguity(settings, monkeypatch) -> None:
    windows = [FakeWindow("Chrome - A"), FakeWindow("Chrome - B")]
    monkeypatch.setattr(desktop, "_windows", lambda: FakeGw(windows))

    result = desktop.window_control({"action": "focus", "title": "chrome"}, _context(settings))
    assert not result.ok
    assert "matches 2 windows" in (result.error or "")


def test_type_text_rejects_non_ascii(settings, monkeypatch) -> None:
    gui = FakeGui()
    monkeypatch.setattr(desktop, "_gui", lambda: gui)
    result = desktop.type_text({"text": "hello ☃"}, _context(settings))
    assert not result.ok
    assert "ASCII" in (result.error or "")
    assert gui.typed == []


def test_type_text_handles_newlines(settings, monkeypatch) -> None:
    gui = FakeGui()
    monkeypatch.setattr(desktop, "_gui", lambda: gui)
    result = desktop.type_text({"text": "hi\nyo"}, _context(settings))
    assert result.ok
    assert gui.typed == ["h", "i", "y", "o"]
    assert gui.pressed == ["enter"]


def test_press_keys_rejects_unknown_key(settings, monkeypatch) -> None:
    gui = FakeGui()
    monkeypatch.setattr(desktop, "_gui", lambda: gui)
    result = desktop.press_keys({"keys": ["ctrl", "not-a-key"]}, _context(settings))
    assert not result.ok
    assert "not-a-key" in (result.error or "")
    assert gui.hotkeys == []


def test_click_outside_screen_is_rejected(settings, monkeypatch) -> None:
    gui = FakeGui()
    monkeypatch.setattr(desktop, "_gui", lambda: gui)
    result = desktop.click({"x": 5000, "y": 10}, _context(settings))
    assert not result.ok
    assert "outside" in (result.error or "")
    assert gui.clicks == []


def test_find_shortcuts_recurses_and_filters(tmp_path: Path) -> None:
    root = tmp_path / "Programs"
    (root / "Music").mkdir(parents=True)
    (root / "Spotify.lnk").write_text("")
    (root / "Music" / "Spotify Helper.lnk").write_text("")
    (root / "Notes.txt").write_text("")
    matches = desktop.find_shortcuts("spotify", [root])
    assert {path.name for path in matches} == {"Spotify.lnk", "Spotify Helper.lnk"}


def test_launch_app_prefers_exact_match(settings, monkeypatch, tmp_path: Path) -> None:
    shortcut = tmp_path / "Spotify.lnk"
    shortcut.write_text("")
    monkeypatch.setattr(desktop, "find_shortcuts", lambda _query: [shortcut, tmp_path / "X.lnk"])
    launched: list[Path] = []
    monkeypatch.setattr(desktop.os, "startfile", launched.append, raising=False)

    result = desktop.launch_app({"name": "spotify"}, _context(settings))
    assert result.ok
    assert result.data["application"] == "Spotify"
    assert launched == [shortcut]


def test_desktop_tools_are_registered_with_risk_levels() -> None:
    tools = {tool.name: tool for tool in build_registry().all()}
    assert tools["list_windows"].permission is PermissionLevel.READ
    assert tools["search_web"].permission is PermissionLevel.LOW_RISK
    assert tools["type_text"].permission is PermissionLevel.EXECUTE
    assert tools["type_text"].requires_confirmation is True
    assert tools["lock_screen"].permission is PermissionLevel.HIGH_RISK


def test_search_web_opens_browser(settings, monkeypatch) -> None:
    opened_urls: list[str] = []
    monkeypatch.setattr(
        desktop.webbrowser, "open", lambda url, **_kw: opened_urls.append(url) or True
    )
    result = desktop.search_web({"query": "python tutorial"}, _context(settings))
    assert result.ok
    assert len(opened_urls) == 1
    assert "google.com/search?q=python+tutorial" in opened_urls[0]
