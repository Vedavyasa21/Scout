"""A small always-on-top Scout orb for voice and quick typed requests.

The window is frameless, translucent and draggable. Left-click starts a voice
request; right-click offers a typed prompt and quit. Tools that change the
machine raise a modal approval dialog before they run.
"""

from __future__ import annotations

import contextlib
import json
import math
import os
import threading
import time
import tkinter as tk
from tkinter import messagebox, simpledialog

from scout.agent import Agent, AgentHooks
from scout.config import Settings
from scout.providers import ModelProvider, ProviderError, Turn, UserTurn, create_provider
from scout.security.policy import ApprovalDecision, ApprovalRequest
from scout.tools import build_registry
from scout.voice import (
    Recording,
    VoiceError,
    record_wav,
    transcribe_wav,
)

SIZE = 64
TRANSPARENT = "#010203"  # Windows transparent-color key; never drawn visibly
ORB_FILL = "#0b1220"
MIC = "#e2e8f0"
COLORS = {
    "idle": "#22d3ee",
    "listening": "#f87171",
    "thinking": "#fbbf24",
    "acting": "#a78bfa",
    "error": "#ef4444",
}
BUBBLE_BG = "#0f172a"


class TkApprover:
    """Approval source that asks through a modal Tk dialog."""

    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self._closed = False

    def close(self) -> None:
        self._closed = True

    def approve(self, request: ApprovalRequest) -> ApprovalDecision:
        if self._closed:
            return ApprovalDecision.DENY
        done = threading.Event()
        decision = [ApprovalDecision.DENY]

        preview = json.dumps(request.arguments, ensure_ascii=False, default=str)
        if len(preview) > 900:
            preview = preview[:899] + "…"
        detail = (
            f"Scout wants to run:\n{request.tool}  ({request.permission.value})\n\n"
            f"{request.description}\n\nArguments:\n{preview}"
        )

        def ask() -> None:
            try:
                if request.is_high_risk:
                    answer = simpledialog.askstring(
                        "Scout - high-risk action",
                        detail + "\n\nType allow to confirm:",
                        parent=self.root,
                    )
                    allowed = (answer or "").strip().lower() == "allow"
                else:
                    allowed = messagebox.askyesno(
                        "Scout - approval", detail + "\n\nAllow this action?", parent=self.root
                    )
                decision[0] = ApprovalDecision.ALLOW_ONCE if allowed else ApprovalDecision.DENY
            except Exception:
                decision[0] = ApprovalDecision.DENY
            finally:
                done.set()

        try:
            self.root.after(0, ask)
        except Exception:
            return ApprovalDecision.DENY
        done.wait(timeout=180)
        return decision[0]


class ScoutOverlay:
    """The floating icon and its voice/text loop."""

    def __init__(self, settings: Settings, *, opacity: float | None = None) -> None:
        self.settings = settings
        self.opacity = settings.overlay_opacity if opacity is None else opacity
        self.root = tk.Tk()
        self.root.title("Scout")
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", True)
        self.root.attributes("-alpha", self.opacity)
        if os.name == "nt":
            with contextlib.suppress(tk.TclError):
                self.root.wm_attributes("-transparentcolor", TRANSPARENT)
        self.root.configure(bg=TRANSPARENT)

        self.canvas = tk.Canvas(
            self.root,
            width=SIZE,
            height=SIZE,
            bg=TRANSPARENT,
            highlightthickness=0,
            bd=0,
            cursor="hand2",
        )
        self.canvas.pack()
        self._draw_orb()

        self.approver = TkApprover(self.root)
        self.provider: ModelProvider
        try:
            self.provider = create_provider(settings)
            self.agent = Agent(
                provider=self.provider,
                registry=build_registry(),
                settings=settings,
                hooks=AgentHooks(
                    on_model_call=lambda _step: self._set_state("thinking"),
                    on_tool_start=lambda _call: self._set_state("acting"),
                ),
                approver=self.approver,
            )
        except Exception:
            self.root.destroy()
            raise

        self.history: list[Turn] = []
        self.state = "idle"
        self._busy = threading.Event()
        self._drag_origin: tuple[int, int, int, int] | None = None
        self._dragged = False
        self._bubble: tk.Toplevel | None = None
        self._bubble_after: str | None = None
        self._closed = False

        self._place_window()
        self._bind_events()
        self._animate()

    # ------------------------------------------------------------------ drawing

    def _draw_orb(self) -> None:
        self.ring = self.canvas.create_oval(
            3, 3, SIZE - 3, SIZE - 3, outline=COLORS["idle"], width=2
        )
        self.canvas.create_oval(9, 9, SIZE - 9, SIZE - 9, fill=ORB_FILL, outline="")
        # A tiny microphone drawn from primitives, so no font is required.
        self.canvas.create_oval(27, 18, 37, 38, fill=MIC, outline="")
        self.canvas.create_line(32, 38, 32, 45, fill=MIC, width=2)
        self.canvas.create_line(26, 45, 38, 45, fill=MIC, width=2)
        self.canvas.create_arc(
            23, 27, 41, 45, start=200, extent=140, style="arc", outline=MIC, width=2
        )

    def _apply_state(self, state: str) -> None:
        self.state = state
        self.canvas.itemconfigure(self.ring, outline=COLORS.get(state, COLORS["idle"]))
        if state == "idle":
            self.canvas.itemconfigure(self.ring, width=2)
            self.canvas.coords(self.ring, 3, 3, SIZE - 3, SIZE - 3)

    def _set_state(self, state: str) -> None:
        with contextlib.suppress(RuntimeError):  # window already destroyed
            self.root.after(0, self._apply_state, state)

    def _animate(self) -> None:
        if self._closed:
            return
        if self.state == "listening":
            pulse = 2 + 2.5 * (0.5 + 0.5 * math.sin(time.perf_counter() * 6))
            inset = 3 + 1.5 * math.sin(time.perf_counter() * 6)
            self.canvas.itemconfigure(self.ring, width=round(pulse, 1))
            self.canvas.coords(self.ring, inset, inset, SIZE - inset, SIZE - inset)
        self.root.after(100, self._animate)

    # ------------------------------------------------------------------ layout

    def _position_file(self):
        return self.settings.data_dir / "overlay_position.json"

    def _place_window(self) -> None:
        x = self.root.winfo_screenwidth() - SIZE - 42
        y = self.root.winfo_screenheight() - SIZE - 130
        try:
            saved = json.loads(self._position_file().read_text(encoding="utf-8"))
            sx, sy = int(saved["x"]), int(saved["y"])
            if 0 <= sx <= self.root.winfo_screenwidth() - SIZE and 0 <= sy <= (
                self.root.winfo_screenheight() - SIZE
            ):
                x, y = sx, sy
        except Exception:
            pass
        self.root.geometry(f"{SIZE}x{SIZE}+{x}+{y}")

    def _save_position(self) -> None:
        with contextlib.suppress(OSError):
            self._position_file().write_text(
                json.dumps({"x": self.root.winfo_x(), "y": self.root.winfo_y()}),
                encoding="utf-8",
            )

    def _bind_events(self) -> None:
        self.canvas.bind("<ButtonPress-1>", self._on_press)
        self.canvas.bind("<B1-Motion>", self._on_drag)
        self.canvas.bind("<ButtonRelease-1>", self._on_release)
        self.canvas.bind("<Button-3>", self._on_menu)
        self.root.bind("<Escape>", lambda _event: self.close())

    # ------------------------------------------------------------------ mouse

    def _on_press(self, event: tk.Event) -> None:
        self._drag_origin = (
            event.x_root,
            event.y_root,
            self.root.winfo_x(),
            self.root.winfo_y(),
        )
        self._dragged = False

    def _on_drag(self, event: tk.Event) -> None:
        if self._drag_origin is None:
            return
        start_x, start_y, win_x, win_y = self._drag_origin
        dx, dy = event.x_root - start_x, event.y_root - start_y
        if abs(dx) + abs(dy) > 3:
            self._dragged = True
        self.root.geometry(f"{SIZE}x{SIZE}+{win_x + dx}+{win_y + dy}")

    def _on_release(self, _event: tk.Event) -> None:
        self._drag_origin = None
        if self._dragged:
            self._save_position()
            return
        self._start_voice()

    def _on_menu(self, event: tk.Event) -> None:
        menu = tk.Menu(self.root, tearoff=0)
        menu.add_command(label="Ask Scout by typing...", command=self._ask_typed)
        menu.add_separator()
        menu.add_command(label="Quit Scout", command=self.close)
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()

    # ------------------------------------------------------------------ requests

    def _ask_typed(self) -> None:
        if self._busy.is_set():
            self._show_bubble("Scout", "Still working on the previous request.")
            return
        prompt = simpledialog.askstring("Scout", "What should Scout do?", parent=self.root)
        if prompt and prompt.strip():
            self._start_request(prompt.strip())

    def _start_voice(self) -> None:
        if self._busy.is_set():
            self._show_bubble("Scout", "Still working on the previous request.")
            return
        self._busy.set()
        self._apply_state("listening")
        threading.Thread(target=self._voice_worker, daemon=True).start()

    def _voice_worker(self) -> None:
        try:
            recording: Recording | None = record_wav(
                max_seconds=self.settings.voice_max_seconds,
                silence_seconds=self.settings.voice_silence_seconds,
            )
            if recording is None:
                self._show_bubble("Scout", "I did not hear anything. Click the orb and speak.")
                return
            self._set_state("thinking")
            text = transcribe_wav(self.provider, recording)
            self._show_bubble("You", text)
            self._run_agent(text)
        except VoiceError as exc:
            self._show_bubble("Voice unavailable", str(exc), error=True)
        except ProviderError as exc:
            self._show_bubble("Transcription failed", str(exc), error=True)
        except Exception as exc:
            self._show_bubble("Voice error", str(exc), error=True)
        finally:
            self._busy.clear()
            self._set_state("idle")

    def _start_request(self, text: str) -> None:
        self._busy.set()
        self._set_state("thinking")
        threading.Thread(target=self._request_worker, args=(text,), daemon=True).start()

    def _request_worker(self, text: str) -> None:
        try:
            self._run_agent(text)
        except ProviderError as exc:
            self._show_bubble("Scout error", str(exc), error=True)
        except Exception as exc:
            self._show_bubble("Scout error", str(exc), error=True)
        finally:
            self._busy.clear()
            self._set_state("idle")

    def _run_agent(self, text: str) -> None:
        result = self.agent.run(text, history=self.history)
        self.history = list(result.turns)[-40:]
        while self.history and not isinstance(self.history[0], UserTurn):
            self.history.pop(0)
        self._show_bubble(
            f"Scout · {result.duration_ms / 1000:.1f}s",
            result.text,
            error=not result.completed,
        )

    # ------------------------------------------------------------------ bubble

    def _show_bubble(self, title: str, text: str, *, error: bool = False) -> None:
        with contextlib.suppress(RuntimeError):
            self.root.after(0, self._render_bubble, title, text, error)

    def _render_bubble(self, title: str, text: str, error: bool) -> None:
        if self._closed:
            return
        self._close_bubble()
        bubble = tk.Toplevel(self.root)
        bubble.overrideredirect(True)
        bubble.attributes("-topmost", True)
        bubble.attributes("-alpha", 0.94)
        bubble.configure(bg=BUBBLE_BG)

        header = tk.Label(
            bubble,
            text=title,
            bg=BUBBLE_BG,
            fg=COLORS["error"] if error else COLORS["idle"],
            font=("Segoe UI", 9, "bold"),
            anchor="w",
            padx=12,
            pady=(8, 0),
        )
        header.pack(fill="x")
        body = tk.Label(
            bubble,
            text=text.strip() or "(no response)",
            bg=BUBBLE_BG,
            fg="#e2e8f0",
            font=("Segoe UI", 9),
            justify="left",
            anchor="w",
            wraplength=330,
            padx=12,
            pady=8,
        )
        body.pack(fill="both")
        bubble.bind("<Button-1>", lambda _event: self._close_bubble())
        for widget in (header, body):
            widget.bind("<Button-1>", lambda _event: self._close_bubble())

        bubble.update_idletasks()
        width = max(bubble.winfo_reqwidth(), 180)
        height = bubble.winfo_reqheight()
        x = self.root.winfo_x() + SIZE // 2 - width // 2
        y = self.root.winfo_y() - height - 8
        x = max(8, min(x, self.root.winfo_screenwidth() - width - 8))
        if y < 8:
            y = self.root.winfo_y() + SIZE + 8
        bubble.geometry(f"+{x}+{y}")

        self._bubble = bubble
        self._bubble_after = self.root.after(15000, self._close_bubble)

    def _close_bubble(self) -> None:
        if self._bubble_after is not None:
            with contextlib.suppress(Exception):
                self.root.after_cancel(self._bubble_after)
            self._bubble_after = None
        if self._bubble is not None:
            with contextlib.suppress(Exception):
                self._bubble.destroy()
            self._bubble = None

    # ------------------------------------------------------------------ lifecycle

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._save_position()
        self.approver.close()
        self._close_bubble()
        with contextlib.suppress(Exception):
            self.root.destroy()

    def run(self, *, autoclose_ms: int | None = None) -> None:
        if autoclose_ms:
            self.root.after(autoclose_ms, self.close)
        self.root.mainloop()


def run_overlay(settings: Settings, *, opacity: float | None = None) -> None:
    """Open the floating icon and block until it is closed."""
    autoclose = os.environ.get("SCOUT_OVERLAY_AUTOCLOSE_MS", "").strip()
    overlay = ScoutOverlay(settings, opacity=opacity)
    overlay.run(autoclose_ms=int(autoclose) if autoclose.isdigit() else None)
