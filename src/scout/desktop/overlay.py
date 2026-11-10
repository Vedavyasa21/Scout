"""A sleek, professional always-on-top Scout overlay built with PyQt6.

Features:
- True 32-bit ARGB per-pixel alpha transparency with hardware anti-aliasing and Gaussian drop-shadows.
- Reference Image 2: Compact floating capsule pill (320x56) with semicircular ends,
  compass badge, live status typography, animated purple waveform bars, and close button.
- Reference Image 3: Expanded Assistant HUD with 4 framing reticles, holographic 3D glowing sphere,
  animated white flowing sine wave, and bottom dock (Keyboard, Glowing Mic, Capabilities).
- Reference Image 1: Capabilities Orbit view with concentric orbital rings, central compass disc,
  and 5 capability squircle tiles (Files 📁, Code </>, Web 🌐, Apps ⊞, Terminal >_).
- Executive Summary Card: Properly shaped floating card with anti-aliased rounded corners,
  tool chips, formatted typography, copy-to-clipboard, and quick-action chips.
- Mobile-Assistant Wake: Saying "Hey Scout" or pressing Ctrl+Alt+S immediately brings the HUD
  to the foreground on top of all windows and begins voice interaction.
- Smooth asynchronous QThread agent execution with zero UI lag or freezing.
"""

from __future__ import annotations

import contextlib
import ctypes
import json
import math
import os
import sys
import threading
from pathlib import Path

from PyQt6.QtCore import (
    QObject,
    QPoint,
    QRect,
    QRectF,
    QSize,
    Qt,
    QThread,
    QTimer,
    pyqtSignal,
)
from PyQt6.QtGui import (
    QBrush,
    QColor,
    QFont,
    QIcon,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
)
from PyQt6.QtWidgets import (
    QApplication,
    QDialog,
    QFrame,
    QGraphicsDropShadowEffect,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMenu,
    QMessageBox,
    QPushButton,
    QSystemTrayIcon,
    QVBoxLayout,
    QWidget,
)

from scout.agent import Agent, AgentCancelled, AgentHooks
from scout.config import Settings
from scout.control import EMERGENCY_STOP
from scout.desktop.character import ScoutCompanionWindow
from scout.providers import (
    AssistantTurn,
    ProviderError,
    Turn,
    UserTurn,
    create_provider,
)
from scout.security.policy import ApprovalDecision, ApprovalRequest
from scout.tools import build_registry
from scout.voice import (
    GlobalHotkeyListener,
    VoiceError,
    WakeWordListener,
    record_wav,
    transcribe_wav,
)

# Visual Constants
CARD_BG = "#ffffff"
CARD_BORDER = "#e2e8f0"
CARD_BORDER_ACTIVE = "#c4b5fd"
TEXT_DARK = "#0f172a"
TEXT_MUTED = "#64748b"
WAVE_PURPLE = "#8b5cf6"
ACCENT_BLUE = "#3b82f6"
RETICLE_COLOR = "#a5b4fc"
ORBIT_RING_COLOR = "#e0e7ff"

ASSETS_DIR = Path(__file__).resolve().parent
ICON_PNG_PATH = ASSETS_DIR / "scout_icon.png"
ICO_PATH = ASSETS_DIR / "scout_icon.ico"
ORB_PNG_PATH = ASSETS_DIR / "scout_orb.png"


def force_foreground_window(win_id: int) -> None:
    """Force window to the absolute front on Windows regardless of focus lock."""
    if os.name != "nt":
        return
    with contextlib.suppress(Exception):
        user32 = ctypes.windll.user32
        user32.ShowWindow(win_id, 9)  # SW_RESTORE
        user32.SetWindowPos(
            win_id, -1, 0, 0, 0, 0, 0x0002 | 0x0001 | 0x0040
        )  # HWND_TOPMOST | SWP_SHOWWINDOW
        user32.BringWindowToTop(win_id)
        user32.SetForegroundWindow(win_id)


# ----------------------------------------------------------------------
# 1. Custom High-Clarity Paint Widgets
# ----------------------------------------------------------------------


class WaveformWidget(QWidget):
    """Smooth, anti-aliased vertical audio waveform bars matching Reference Image 2."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedSize(65, 30)
        self.state = "idle"  # idle, listening, thinking, acting
        self.phase = 0.0

    def set_state(self, state: str) -> None:
        self.state = state
        self.update()

    def advance(self, step: float = 0.22) -> None:
        self.phase += step
        self.update()

    def paintEvent(self, _event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        envelope = [0.25, 0.45, 0.75, 1.0, 1.25, 1.4, 1.25, 1.0, 0.75, 0.45, 0.25]
        h = self.height()
        cy = h / 2.0
        start_x = 6.0
        spacing = 5.0

        pen = QPen(QColor(WAVE_PURPLE))
        pen.setWidthF(2.6)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(pen)

        now = self.phase
        for i, env in enumerate(envelope):
            if self.state == "listening":
                wave = 0.5 + 0.5 * math.sin(now * 3.5 + i * 0.7)
                flutter = 0.2 * math.cos(now * 5.0 + i * 1.3)
                bh = max(4.0, min(24.0, 22.0 * env * (wave + flutter)))
            elif self.state == "thinking":
                wave = 0.5 + 0.5 * math.sin(now * 2.2 + i * 0.5)
                bh = max(4.0, min(18.0, 16.0 * wave * env))
            elif self.state == "acting":
                wave = 0.5 + 0.5 * math.sin(now * 2.8 - i * 0.6)
                bh = max(4.0, min(20.0, 18.0 * wave))
            else:
                wave = 0.5 + 0.5 * math.sin(now * 1.0 + i * 0.4)
                bh = max(3.0, min(10.0, 7.0 * env * (0.8 + 0.2 * wave)))

            bx = start_x + i * spacing
            painter.drawLine(
                QPoint(int(bx), int(cy - bh / 2.0)), QPoint(int(bx), int(cy + bh / 2.0))
            )


class OrbWidget(QWidget):
    """Holographic 3D glowing sphere with animated sine wave matching Reference Image 3."""

    def __init__(self, orb_pixmap: QPixmap | None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedSize(100, 100)
        self.orb_pixmap = orb_pixmap
        self.phase = 0.0

    def advance(self, step: float = 0.18) -> None:
        self.phase += step
        self.update()

    def paintEvent(self, _event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        cx, cy = self.width() / 2.0, self.height() / 2.0

        # Draw 3D Orb Pixmap
        if self.orb_pixmap and not self.orb_pixmap.isNull():
            target_rect = QRect(int(cx - 45), int(cy - 45), 90, 90)
            painter.drawPixmap(target_rect, self.orb_pixmap)
        else:
            # Fallback procedural gradient
            grad = QLinearGradient(cx - 45, cy - 45, cx + 45, cy + 45)
            grad.setColorAt(0.0, QColor("#06b6d4"))
            grad.setColorAt(0.5, QColor("#3b82f6"))
            grad.setColorAt(1.0, QColor("#8b5cf6"))
            painter.setBrush(QBrush(grad))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawEllipse(QPoint(int(cx), int(cy)), 44, 44)

        # Draw glowing white sine wave across orb equator
        path = QPainterPath()
        first = True
        for dx in range(-40, 41, 2):
            px = cx + dx
            # Gaussian bell curve envelope keeps wave gracefully bounded
            amp = 13.0 * math.exp(-((dx / 26.0) ** 2))
            py = cy + amp * math.sin(dx * 0.12 - self.phase)
            if first:
                path.moveTo(px, py)
                first = False
            else:
                path.lineTo(px, py)

        pen = QPen(QColor(255, 255, 255, 235))
        pen.setWidthF(2.6)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(pen)
        painter.drawPath(path)


class OrbitWidget(QWidget):
    """Reference Image 1: Concentric orbital rings with 5 capability nodes."""

    node_selected = pyqtSignal(str, str)

    def __init__(self, logo_pixmap: QPixmap | None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedSize(308, 220)
        self.logo_pixmap = logo_pixmap
        self.selected_node = ""

        # 5 Capabilities
        self.nodes = [
            ("Files", -math.pi / 2, "📁", "Read, write & search local files"),
            ("Code", -math.pi / 2 + 2 * math.pi / 5, "</>", "Run commands, test scripts & debug"),
            ("Web", -math.pi / 2 + 4 * math.pi / 5, "🌐", "Google search & web browser automation"),
            ("Apps", -math.pi / 2 + 6 * math.pi / 5, "⊞", "Launch & control Windows applications"),
            ("Terminal", -math.pi / 2 + 8 * math.pi / 5, ">_", "PowerShell & CLI tool automation"),
        ]

    def mousePressEvent(self, event) -> None:  # noqa: N802
        pos = event.position()
        cx, cy = self.width() / 2.0, self.height() / 2.0 - 10
        orbit_r = 75.0

        for label, angle, _icon, desc in self.nodes:
            nx = cx + orbit_r * math.cos(angle)
            ny = cy + orbit_r * math.sin(angle)
            if math.hypot(pos.x() - nx, pos.y() - ny) <= 20:
                self.selected_node = label
                self.node_selected.emit(label, desc)
                self.update()
                return

    def paintEvent(self, _event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        cx, cy = self.width() / 2.0, self.height() / 2.0 - 10

        # Concentric orbital rings
        for r, color, w in [
            (40, "#f1f5f9", 1.0),
            (75, ORBIT_RING_COLOR, 1.4),
            (110, "#f8fafc", 1.0),
        ]:
            pen = QPen(QColor(color))
            pen.setWidthF(w)
            painter.setPen(pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawEllipse(QPoint(int(cx), int(cy)), r, r)

        # Center compass badge
        if self.logo_pixmap and not self.logo_pixmap.isNull():
            target_rect = QRect(int(cx - 24), int(cy - 24), 48, 48)
            painter.drawPixmap(target_rect, self.logo_pixmap)
        else:
            painter.setBrush(QBrush(QColor("#ffffff")))
            painter.setPen(QPen(QColor("#c7d2fe"), 2))
            painter.drawEllipse(QPoint(int(cx), int(cy)), 22, 22)

        # 5 Capability Nodes
        orbit_r = 75.0
        for label, angle, icon, _desc in self.nodes:
            nx = cx + orbit_r * math.cos(angle)
            ny = cy + orbit_r * math.sin(angle)
            is_sel = self.selected_node == label

            # Squircle badge
            badge_rect = QRectF(nx - 18, ny - 18, 36, 36)
            path = QPainterPath()
            path.addRoundedRect(badge_rect, 10, 10)

            painter.setBrush(QBrush(QColor("#ffffff")))
            pen_color = "#8b5cf6" if is_sel else "#e2e8f0"
            painter.setPen(QPen(QColor(pen_color), 2.0 if is_sel else 1.0))
            painter.drawPath(path)

            # Icon text
            painter.setPen(QColor("#3b82f6" if label in ("Files", "Apps", "Web") else "#8b5cf6"))
            painter.setFont(QFont("Segoe UI Emoji", 10, QFont.Weight.Bold))
            painter.drawText(badge_rect, Qt.AlignmentFlag.AlignCenter, icon)

            # Tiny label below squircle
            painter.setPen(QColor(TEXT_MUTED))
            painter.setFont(QFont("Segoe UI", 7, QFont.Weight.Bold))
            lbl_rect = QRectF(nx - 30, ny + 20, 60, 14)
            painter.drawText(lbl_rect, Qt.AlignmentFlag.AlignCenter, label)


# ----------------------------------------------------------------------
# 2. Executive Floating Summary Card Window
# ----------------------------------------------------------------------


class SummaryCard(QWidget):
    """Floating executive summary card matching modern mobile/desktop HUDs."""

    talk_again_requested = pyqtSignal()
    reply_requested = pyqtSignal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(None)  # Top-level window
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setFixedWidth(380)

        # Outer layout for drop shadow padding
        outer = QVBoxLayout(self)
        outer.setContentsMargins(14, 14, 14, 14)

        # Card container
        self.card = QFrame(self)
        self.card.setObjectName("summaryCard")
        self.card.setStyleSheet("""
            QFrame#summaryCard {
                background-color: #ffffff;
                border: 1px solid #e2e8f0;
                border-radius: 20px;
            }
        """)

        shadow = QGraphicsDropShadowEffect(self.card)
        shadow.setBlurRadius(24)
        shadow.setOffset(0, 8)
        shadow.setColor(QColor(15, 23, 42, 45))
        self.card.setGraphicsEffect(shadow)

        card_layout = QVBoxLayout(self.card)
        card_layout.setContentsMargins(14, 12, 14, 12)
        card_layout.setSpacing(10)

        # Header Row
        header = QHBoxLayout()
        header.setSpacing(8)

        self.icon_badge = QLabel("➤")
        self.icon_badge.setStyleSheet("""
            background-color: #ede9fe;
            color: #7c3aed;
            font-size: 11px;
            font-weight: bold;
            border-radius: 11px;
            padding: 3px 6px;
        """)
        header.addWidget(self.icon_badge)

        self.title_lbl = QLabel("Scout Summary")
        self.title_lbl.setStyleSheet(
            "font-family: 'Segoe UI'; font-size: 11px; font-weight: bold; color: #4c1d95;"
        )
        header.addWidget(self.title_lbl)

        header.addStretch(1)

        self.btn_copy = QPushButton("Copy")
        self.btn_copy.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_copy.setStyleSheet("""
            QPushButton {
                background: #f1f5f9;
                color: #475569;
                border: none;
                border-radius: 10px;
                padding: 4px 8px;
                font-size: 10px;
            }
            QPushButton:hover { background: #e2e8f0; }
        """)
        self.btn_copy.clicked.connect(self._copy_text)
        header.addWidget(self.btn_copy)

        btn_close = QPushButton("✕")
        btn_close.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_close.setStyleSheet("""
            QPushButton {
                background: transparent;
                color: #94a3b8;
                border: none;
                font-size: 11px;
                font-weight: bold;
                padding: 2px 4px;
            }
            QPushButton:hover { color: #0f172a; }
        """)
        btn_close.clicked.connect(self.hide)
        header.addWidget(btn_close)
        card_layout.addLayout(header)

        # Divider
        divider = QFrame()
        divider.setFrameShape(QFrame.Shape.HLine)
        divider.setStyleSheet("background-color: #f1f5f9; max-height: 1px;")
        card_layout.addWidget(divider)

        # Summary Body
        self.body_lbl = QLabel()
        self.body_lbl.setWordWrap(True)
        self.body_lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.body_lbl.setStyleSheet("""
            font-family: 'Segoe UI';
            font-size: 11px;
            color: #1e293b;
            line-height: 1.4;
            padding: 2px 0px;
        """)
        card_layout.addWidget(self.body_lbl)

        # Footer Actions
        footer = QHBoxLayout()
        footer.setSpacing(6)

        btn_talk = QPushButton("🎙 Talk Again")
        btn_talk.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_talk.setStyleSheet("""
            QPushButton {
                background-color: #ede9fe;
                color: #6d28d9;
                font-family: 'Segoe UI';
                font-size: 10px;
                font-weight: bold;
                border: none;
                border-radius: 12px;
                padding: 5px 10px;
            }
            QPushButton:hover { background-color: #ddd6fe; }
        """)
        btn_talk.clicked.connect(self._on_talk_again)
        footer.addWidget(btn_talk)

        btn_reply = QPushButton("⌨ Type Reply")
        btn_reply.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_reply.setStyleSheet("""
            QPushButton {
                background-color: #f8fafc;
                color: #475569;
                font-family: 'Segoe UI';
                font-size: 10px;
                border: 1px solid #e2e8f0;
                border-radius: 12px;
                padding: 4px 10px;
            }
            QPushButton:hover { background-color: #f1f5f9; }
        """)
        btn_reply.clicked.connect(self._on_reply)
        footer.addWidget(btn_reply)

        footer.addStretch(1)
        card_layout.addLayout(footer)

        outer.addWidget(self.card)

        # Auto-dismiss timer (25s)
        self.dismiss_timer = QTimer(self)
        self.dismiss_timer.setSingleShot(True)
        self.dismiss_timer.timeout.connect(self.hide)

    def show_summary(
        self, title: str, text: str, *, error: bool = False, anchor_rect: QRect | None = None
    ) -> None:
        self.title_lbl.setText(title)
        self.body_lbl.setText(text.strip() or "(no response)")
        if error:
            self.icon_badge.setText("⚠️")
            self.icon_badge.setStyleSheet(
                "background-color: #fee2e2; color: #dc2626; border-radius: 11px; padding: 3px 6px;"
            )
            self.title_lbl.setStyleSheet(
                "font-family: 'Segoe UI'; font-size: 11px; font-weight: bold; color: #dc2626;"
            )
        else:
            self.icon_badge.setText("➤")
            self.icon_badge.setStyleSheet(
                "background-color: #ede9fe; color: #7c3aed; border-radius: 11px; padding: 3px 6px;"
            )
            self.title_lbl.setStyleSheet(
                "font-family: 'Segoe UI'; font-size: 11px; font-weight: bold; color: #4c1d95;"
            )

        self.btn_copy.setText("Copy")
        self.adjustSize()

        if anchor_rect:
            screen = QApplication.primaryScreen().geometry()
            w = self.width()
            h = self.height()
            x = anchor_rect.x() + (anchor_rect.width() - w) // 2
            x = max(10, min(x, screen.width() - w - 10))
            y = anchor_rect.y() - h - 6
            if y < 20:
                y = anchor_rect.y() + anchor_rect.height() + 6
            y = max(10, min(y, screen.height() - h - 10))
            self.move(x, y)

        self.show()
        force_foreground_window(int(self.winId()))
        self.dismiss_timer.start(25000)

    def _copy_text(self) -> None:
        clipboard = QApplication.clipboard()
        clipboard.setText(self.body_lbl.text())
        self.btn_copy.setText("✓ Copied")

    def _on_talk_again(self) -> None:
        self.hide()
        self.talk_again_requested.emit()

    def _on_reply(self) -> None:
        self.hide()
        self.reply_requested.emit()


# ----------------------------------------------------------------------
# 3. Asynchronous Agent Worker Thread
# ----------------------------------------------------------------------


class AgentWorker(QThread):
    """Executes agent runs in a dedicated background thread with zero UI blocking."""

    sig_status = pyqtSignal(str, str)  # (state, detail)
    sig_finished = pyqtSignal(str, str, float, list)  # (title, text, duration_s, tools)
    sig_error = pyqtSignal(str, str)  # (title, error_message)
    sig_cancelled = pyqtSignal(str)  # (message)

    def __init__(self, agent: Agent, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.agent = agent
        self.request_text = ""
        self.history: list[Turn] = []

    def set_request(self, text: str, history: list[Turn]) -> None:
        self.request_text = text
        self.history = history

    def run(self) -> None:
        self.sig_status.emit("thinking", "")
        try:
            result = self.agent.run(self.request_text, history=self.history)
            tool_names = [step.tool for step in result.steps if step.ok]
            title = f"Scout · {result.duration_ms / 1000:.1f}s"
            if tool_names:
                title += f" · {', '.join(tool_names[:2])}"
            self.sig_finished.emit(title, result.text, result.duration_ms / 1000.0, tool_names)
        except AgentCancelled:
            self.sig_cancelled.emit("Stopped before the task finished.")
        except ProviderError as exc:
            self.sig_error.emit("Scout Provider Error", str(exc))
        except Exception as exc:
            self.sig_error.emit("Execution Error", str(exc))


class OverlayApprover(QObject):
    """Approval dialog bridge between the agent worker and the GUI thread.

    `approve()` runs on the agent's worker thread. It emits a request that is
    delivered to the overlay window on the GUI thread and waits for the answer.
    Without an answer (window closed, timeout, stop) the call is denied.
    """

    requested = pyqtSignal(object)

    def __init__(self, parent: QObject | None = None, *, timeout: float = 120.0) -> None:
        super().__init__(parent)
        self.timeout = timeout
        self._lock = threading.Lock()
        self._answered = threading.Event()
        self._decision = ApprovalDecision.DENY

    def approve(self, request: ApprovalRequest) -> ApprovalDecision:
        with self._lock:
            self._decision = ApprovalDecision.DENY
        self._answered.clear()
        self.requested.emit(request)
        if not self._answered.wait(self.timeout):
            return ApprovalDecision.DENY
        with self._lock:
            return self._decision

    def resolve(self, decision: ApprovalDecision) -> None:
        with self._lock:
            self._decision = decision
        self._answered.set()

    @property
    def answered(self) -> bool:
        return self._answered.is_set()

    def cancel_pending(self) -> None:
        self.resolve(ApprovalDecision.DENY)


# ----------------------------------------------------------------------
# 4. Main Scout Overlay Application Window
# ----------------------------------------------------------------------


class ScoutOverlayWindow(QWidget):
    """Master Scout Overlay with 4 reference views, wake-word pop-up, and flawless edges."""

    sig_agent_chunk = pyqtSignal(str)

    def __init__(
        self,
        settings: Settings,
        *,
        opacity: float | None = None,
        auto_listen: bool = False,
        enable_wake_word: bool = True,
        enable_hotkey: bool = True,
        enable_companion: bool = True,
    ) -> None:
        super().__init__()
        self.settings = settings
        self.label = settings.overlay_label.capitalize() if settings.overlay_label else "Scout"
        self.opacity = 0.98 if opacity is None else opacity

        # Translucent Frameless Window setup
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setWindowOpacity(self.opacity)

        # Asset pixmaps
        self.logo_pixmap: QPixmap | None = None
        self.orb_pixmap: QPixmap | None = None
        self._load_assets()

        # State
        self.view_mode = "compact"  # compact, expanded, orbit, drawer
        self.state = "idle"  # idle, listening, thinking, acting, success, error, cancelled
        self.active_tool = ""
        self.history: list[Turn] = []
        self._drag_pos = QPoint()
        self._stream_buffer = ""
        # Incremented by Stop so voice work started before the stop is discarded.
        self._stop_epoch = 0

        # Approver & Agent: confirmations are shown in the overlay by default.
        self.approver = OverlayApprover(self)
        self.approver.requested.connect(
            self._on_approval_requested, Qt.ConnectionType.QueuedConnection
        )
        self.provider = create_provider(settings)
        self.agent = Agent(
            provider=self.provider,
            registry=build_registry(),
            settings=settings,
            hooks=AgentHooks(
                on_model_call=lambda _step: self._on_agent_model_call(),
                on_tool_start=lambda call: self._on_agent_tool_start(call.name),
                on_model_chunk=self.sig_agent_chunk.emit,
            ),
            approver=self.approver,
        )
        self.sig_agent_chunk.connect(self._on_agent_chunk, Qt.ConnectionType.QueuedConnection)

        # Agent background worker thread
        self.worker = AgentWorker(self.agent, self)
        self.worker.sig_status.connect(self._set_state)
        self.worker.sig_finished.connect(self._on_worker_finished)
        self.worker.sig_error.connect(self._on_worker_error)
        self.worker.sig_cancelled.connect(self._on_worker_cancelled)

        # Summary window
        self.summary_win = SummaryCard()
        self.summary_win.talk_again_requested.connect(self.start_voice)
        self.summary_win.reply_requested.connect(lambda: self.set_view_mode("drawer"))

        # Desktop character. It only listens to state changes; if it fails the
        # assistant keeps working.
        self.companion: ScoutCompanionWindow | None = None
        if enable_companion:
            with contextlib.suppress(Exception):
                self.companion = ScoutCompanionWindow(settings.data_dir)
                self.companion.summon_requested.connect(self.wake_and_listen)
                self.companion.quit_requested.connect(self.close)
                self.companion.stop_requested.connect(self.emergency_stop)

        # Returns transient success/error states to rest without disturbing a new run.
        self._settle_timer = QTimer(self)
        self._settle_timer.setSingleShot(True)
        self._settle_timer.timeout.connect(lambda: self._set_state("idle", ""))

        # Build UI Structure
        self._build_ui()

        # Animation timer (60 FPS)
        self.anim_timer = QTimer(self)
        self.anim_timer.timeout.connect(self._on_anim_frame)
        self.anim_timer.start(16)

        # System Tray setup
        self._setup_tray()

        # Position window
        self._place_window()

        # Background Listeners (Wake Word & Hotkey)
        self.hotkey_listener: GlobalHotkeyListener | None = None
        self.wake_listener: WakeWordListener | None = None
        if enable_hotkey:
            with contextlib.suppress(Exception):
                self.hotkey_listener = GlobalHotkeyListener(self.wake_and_listen)
                self.hotkey_listener.start()
        if enable_wake_word:
            with contextlib.suppress(Exception):
                self.wake_listener = WakeWordListener(
                    self.wake_and_listen,
                    provider=self.provider,
                    local_only=not self.settings.wake_cloud_fallback,
                )
                self.wake_listener.start()

        if auto_listen:
            QTimer.singleShot(400, self.wake_and_listen)

    # ------------------------------------------------------------------ Assets

    def _load_assets(self) -> None:
        if ICON_PNG_PATH.exists():
            self.logo_pixmap = QPixmap(str(ICON_PNG_PATH)).scaled(
                32,
                32,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            self.logo_mid_pixmap = QPixmap(str(ICON_PNG_PATH)).scaled(
                48,
                48,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
        else:
            self.logo_pixmap = None
            self.logo_mid_pixmap = None

        if ORB_PNG_PATH.exists():
            self.orb_pixmap = QPixmap(str(ORB_PNG_PATH)).scaled(
                90,
                90,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
        else:
            self.orb_pixmap = None

    # ------------------------------------------------------------------ UI Layout

    def _build_ui(self) -> None:
        # Master outer layout has padding for the Gaussian drop-shadow
        self.master_layout = QVBoxLayout(self)
        self.master_layout.setContentsMargins(16, 16, 16, 16)

        # Central Card
        self.card = QFrame(self)
        self.card.setObjectName("scoutCard")

        self.shadow = QGraphicsDropShadowEffect(self.card)
        self.shadow.setBlurRadius(24)
        self.shadow.setOffset(0, 6)
        self.shadow.setColor(QColor(15, 23, 42, 45))
        self.card.setGraphicsEffect(self.shadow)

        self.card_layout = QVBoxLayout(self.card)
        self.card_layout.setContentsMargins(0, 0, 0, 0)
        self.card_layout.setSpacing(0)

        # 1. Compact View Container (Reference Image 2)
        self.compact_widget = QWidget(self.card)
        comp_layout = QHBoxLayout(self.compact_widget)
        comp_layout.setContentsMargins(14, 8, 14, 8)
        comp_layout.setSpacing(10)

        # Left circular logo badge
        self.btn_logo = QPushButton()
        self.btn_logo.setFixedSize(34, 34)
        self.btn_logo.setCursor(Qt.CursorShape.PointingHandCursor)
        if self.logo_pixmap:
            self.btn_logo.setIcon(QIcon(self.logo_pixmap))
            self.btn_logo.setIconSize(QSize(30, 30))
        self.btn_logo.setStyleSheet("QPushButton { background: transparent; border: none; }")
        self.btn_logo.clicked.connect(self._toggle_expand)
        comp_layout.addWidget(self.btn_logo)

        # Status text
        self.status_lbl = QLabel(f"{self.label} · Ready")
        self.status_lbl.setStyleSheet("""
            QLabel {
                font-family: 'Segoe UI';
                font-size: 11px;
                font-weight: 600;
                color: #0f172a;
            }
        """)
        comp_layout.addWidget(self.status_lbl)

        comp_layout.addStretch(1)

        # Animated purple waveform bars
        self.wave_widget = WaveformWidget(self.compact_widget)
        comp_layout.addWidget(self.wave_widget)

        # Emergency stop: always available, even in the compact pill.
        btn_stop = QPushButton("■")
        btn_stop.setFixedSize(22, 22)
        btn_stop.setToolTip("Stop Scout (Esc in approval dialogs)")
        btn_stop.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_stop.setStyleSheet("""
            QPushButton {
                background-color: #fef2f2;
                color: #dc2626;
                border: 1px solid #fecaca;
                border-radius: 11px;
                font-size: 8px;
                font-weight: bold;
            }
            QPushButton:hover { background-color: #fee2e2; color: #b91c1c; }
        """)
        btn_stop.clicked.connect(self.emergency_stop)
        comp_layout.addWidget(btn_stop)

        # Right circular dismiss button (✕)
        btn_close = QPushButton("✕")
        btn_close.setFixedSize(22, 22)
        btn_close.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_close.setStyleSheet("""
            QPushButton {
                background-color: #f1f5f9;
                color: #64748b;
                border: 1px solid #e2e8f0;
                border-radius: 11px;
                font-size: 9px;
                font-weight: bold;
            }
            QPushButton:hover { background-color: #e2e8f0; color: #0f172a; }
        """)
        btn_close.clicked.connect(self.close)
        comp_layout.addWidget(btn_close)

        self.card_layout.addWidget(self.compact_widget)

        # 2. Drawer View Container (Inline Text Input)
        self.drawer_widget = QWidget(self.card)
        drawer_layout = QHBoxLayout(self.drawer_widget)
        drawer_layout.setContentsMargins(14, 8, 14, 12)
        drawer_layout.setSpacing(8)

        self.input_entry = QLineEdit()
        self.input_entry.setPlaceholderText("Ask Scout anything...")
        self.input_entry.setStyleSheet("""
            QLineEdit {
                background-color: #f8fafc;
                border: 1px solid #cbd5e1;
                border-radius: 14px;
                padding: 6px 12px;
                font-family: 'Segoe UI';
                font-size: 10px;
                color: #0f172a;
            }
            QLineEdit:focus { border: 1px solid #8b5cf6; }
        """)
        self.input_entry.returnPressed.connect(self._on_entry_submit)
        drawer_layout.addWidget(self.input_entry)

        btn_send = QPushButton("➤")
        btn_send.setFixedSize(28, 28)
        btn_send.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_send.setStyleSheet("""
            QPushButton {
                background-color: #8b5cf6;
                color: #ffffff;
                border: none;
                border-radius: 14px;
                font-size: 10px;
                font-weight: bold;
            }
            QPushButton:hover { background-color: #7c3aed; }
        """)
        btn_send.clicked.connect(self._on_entry_submit)
        drawer_layout.addWidget(btn_send)

        self.drawer_widget.hide()
        self.card_layout.addWidget(self.drawer_widget)

        # 3. Expanded HUD Container (Reference Image 3)
        self.expanded_widget = QWidget(self.card)
        exp_layout = QVBoxLayout(self.expanded_widget)
        exp_layout.setContentsMargins(14, 14, 14, 14)
        exp_layout.setSpacing(6)

        # Header with close
        exp_header = QHBoxLayout()
        exp_header.addStretch(1)
        btn_hud_close = QPushButton("✕")
        btn_hud_close.setFixedSize(22, 22)
        btn_hud_close.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_hud_close.setStyleSheet("""
            QPushButton {
                background-color: #f1f5f9;
                color: #64748b;
                border: 1px solid #e2e8f0;
                border-radius: 11px;
                font-size: 9px;
                font-weight: bold;
            }
            QPushButton:hover { background-color: #e2e8f0; color: #0f172a; }
        """)
        btn_hud_close.clicked.connect(lambda: self.set_view_mode("compact"))
        exp_header.addWidget(btn_hud_close)
        exp_layout.addLayout(exp_header)

        # Center Holographic Orb
        orb_box = QHBoxLayout()
        orb_box.addStretch(1)
        self.orb_widget = OrbWidget(self.orb_pixmap, self.expanded_widget)
        orb_box.addWidget(self.orb_widget)
        orb_box.addStretch(1)
        exp_layout.addLayout(orb_box)

        # Title: Scout
        self.hud_title = QLabel(self.label)
        self.hud_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.hud_title.setStyleSheet(
            "font-family: 'Segoe UI'; font-size: 13px; font-weight: bold; color: #0f172a;"
        )
        exp_layout.addWidget(self.hud_title)

        # Subtitle: Listening...
        self.hud_subtitle = QLabel("Ready")
        self.hud_subtitle.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.hud_subtitle.setStyleSheet("font-family: 'Segoe UI'; font-size: 10px; color: #64748b;")
        exp_layout.addWidget(self.hud_subtitle)

        exp_layout.addSpacing(8)

        # Bottom Dock (Capsule Container)
        dock_box = QHBoxLayout()
        dock_box.addStretch(1)

        dock_frame = QFrame()
        dock_frame.setStyleSheet("""
            QFrame {
                background-color: #f8fafc;
                border: 1px solid #e2e8f0;
                border-radius: 20px;
            }
        """)
        dock_layout = QHBoxLayout(dock_frame)
        dock_layout.setContentsMargins(12, 4, 12, 4)
        dock_layout.setSpacing(16)

        # Button 1: Keyboard (Type)
        btn_kb = QPushButton("⌨")
        btn_kb.setFixedSize(32, 32)
        btn_kb.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_kb.setStyleSheet("""
            QPushButton {
                background-color: #ffffff;
                color: #475569;
                border: 1px solid #e2e8f0;
                border-radius: 16px;
                font-family: 'Segoe UI Emoji';
                font-size: 11px;
            }
            QPushButton:hover { background-color: #f1f5f9; }
        """)
        btn_kb.clicked.connect(lambda: self.set_view_mode("drawer"))
        dock_layout.addWidget(btn_kb)

        # Button 2: Glowing Purple Mic (Talk)
        btn_mic = QPushButton("🎙")
        btn_mic.setFixedSize(40, 40)
        btn_mic.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_mic.setStyleSheet("""
            QPushButton {
                background-color: #8b5cf6;
                color: #ffffff;
                border: 2px solid #c4b5fd;
                border-radius: 20px;
                font-family: 'Segoe UI Emoji';
                font-size: 13px;
            }
            QPushButton:hover { background-color: #7c3aed; }
        """)
        btn_mic.clicked.connect(self.start_voice)
        dock_layout.addWidget(btn_mic)

        # Button 3: Emergency Stop
        btn_stop = QPushButton("■")
        btn_stop.setFixedSize(32, 32)
        btn_stop.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_stop.setToolTip("Stop Scout")
        btn_stop.setStyleSheet("""
            QPushButton {
                background-color: #fef2f2;
                color: #dc2626;
                border: 1px solid #fecaca;
                border-radius: 16px;
                font-size: 9px;
                font-weight: bold;
            }
            QPushButton:hover { background-color: #fee2e2; color: #b91c1c; }
        """)
        btn_stop.clicked.connect(self.emergency_stop)
        dock_layout.addWidget(btn_stop)

        # Button 4: Settings / Capabilities (Reference Image 1)
        btn_caps = QPushButton("⚙")
        btn_caps.setFixedSize(32, 32)
        btn_caps.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_caps.setStyleSheet("""
            QPushButton {
                background-color: #ffffff;
                color: #475569;
                border: 1px solid #e2e8f0;
                border-radius: 16px;
                font-family: 'Segoe UI Emoji';
                font-size: 11px;
            }
            QPushButton:hover { background-color: #f1f5f9; }
        """)
        btn_caps.clicked.connect(lambda: self.set_view_mode("orbit"))
        dock_layout.addWidget(btn_caps)

        dock_box.addWidget(dock_frame)
        dock_box.addStretch(1)
        exp_layout.addLayout(dock_box)

        self.expanded_widget.hide()
        self.card_layout.addWidget(self.expanded_widget)

        # 4. Capabilities Orbit Container (Reference Image 1)
        self.orbit_widget = QWidget(self.card)
        orb_layout = QVBoxLayout(self.orbit_widget)
        orb_layout.setContentsMargins(14, 12, 14, 12)
        orb_layout.setSpacing(6)

        # Header: Back button & Title
        orb_header = QHBoxLayout()
        btn_back = QPushButton("← Back")
        btn_back.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_back.setStyleSheet("""
            QPushButton {
                background: transparent;
                color: #6366f1;
                font-family: 'Segoe UI';
                font-size: 10px;
                font-weight: bold;
                border: none;
            }
            QPushButton:hover { color: #4338ca; }
        """)
        btn_back.clicked.connect(lambda: self.set_view_mode("expanded"))
        orb_header.addWidget(btn_back)

        orb_header.addStretch(1)

        orb_title = QLabel("Scout Capabilities")
        orb_title.setStyleSheet(
            "font-family: 'Segoe UI'; font-size: 11px; font-weight: bold; color: #0f172a;"
        )
        orb_header.addWidget(orb_title)

        orb_header.addStretch(1)

        btn_orb_close = QPushButton("✕")
        btn_orb_close.setFixedSize(20, 20)
        btn_orb_close.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_orb_close.setStyleSheet("""
            QPushButton {
                background-color: #f1f5f9;
                color: #64748b;
                border: 1px solid #e2e8f0;
                border-radius: 10px;
                font-size: 9px;
            }
            QPushButton:hover { background-color: #e2e8f0; color: #0f172a; }
        """)
        btn_orb_close.clicked.connect(lambda: self.set_view_mode("compact"))
        orb_header.addWidget(btn_orb_close)
        orb_layout.addLayout(orb_header)

        # Orbit canvas
        self.orbit_canvas = OrbitWidget(self.logo_mid_pixmap, self.orbit_widget)
        self.orbit_canvas.node_selected.connect(self._on_orbit_node_selected)
        orb_layout.addWidget(self.orbit_canvas, alignment=Qt.AlignmentFlag.AlignCenter)

        # Description banner
        self.orbit_desc_lbl = QLabel("5 Capabilities Active · Full Desktop Access")
        self.orbit_desc_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.orbit_desc_lbl.setStyleSheet("""
            background-color: #f8fafc;
            border: 1px solid #e2e8f0;
            border-radius: 12px;
            padding: 6px 12px;
            font-family: 'Segoe UI';
            font-size: 9px;
            color: #475569;
        """)
        orb_layout.addWidget(self.orbit_desc_lbl)

        self.orbit_widget.hide()
        self.card_layout.addWidget(self.orbit_widget)

        self.master_layout.addWidget(self.card)
        self._update_card_style()

    def _update_card_style(self) -> None:
        """Apply CSS styling matching mode and state."""
        border_color = CARD_BORDER_ACTIVE if self.state == "listening" else CARD_BORDER
        radius = 28 if self.view_mode == "compact" else 26
        self.card.setStyleSheet(f"""
            QFrame#scoutCard {{
                background-color: {CARD_BG};
                border: 1px solid {border_color};
                border-radius: {radius}px;
            }}
        """)

    # ------------------------------------------------------------------ View Mode Transitions

    def set_view_mode(self, mode: str) -> None:
        """Switch between Compact Pill, Expanded HUD, Orbit View, and Input Drawer."""
        self.view_mode = mode

        # Toggle containers
        self.compact_widget.setVisible(mode in ("compact", "drawer"))
        self.drawer_widget.setVisible(mode == "drawer")
        self.expanded_widget.setVisible(mode == "expanded")
        self.orbit_widget.setVisible(mode == "orbit")

        if mode == "compact":
            self.setFixedSize(352, 88)
        elif mode == "drawer":
            self.setFixedSize(352, 136)
            self.input_entry.setFocus()
        elif mode == "expanded" or mode == "orbit":
            self.setFixedSize(372, 362)

        self._update_card_style()
        self._save_position()

    def _toggle_expand(self) -> None:
        if self.view_mode == "compact":
            self.set_view_mode("expanded")
        else:
            self.set_view_mode("compact")

    def _toggle_companion(self) -> None:
        if self.companion is None:
            return
        if self.companion.isVisible():
            self.companion.hide()
        else:
            self.companion.show()
            self.companion.raise_()

    def _on_orbit_node_selected(self, label: str, desc: str) -> None:
        self.orbit_desc_lbl.setText(f"{label}: {desc}")

    # ------------------------------------------------------------------ Pop-Up On Wake & Hotkey

    def wake_and_listen(self) -> None:
        """Summon Scout prominently to the front and begin active voice listening."""
        QTimer.singleShot(0, self._do_wake_and_listen)

    def _do_wake_and_listen(self) -> None:
        self.show()
        force_foreground_window(int(self.winId()))
        # Expand prominently into the assistant HUD!
        self.set_view_mode("expanded")
        self.start_voice()

    # ------------------------------------------------------------------ Voice & Execution

    def start_voice(self) -> None:
        """Start microphone recording and pass to agent."""
        if self.worker.isRunning():
            return
        self._settle_timer.stop()
        if self.wake_listener:
            self.wake_listener.pause()
        self._set_state("listening", "")
        epoch = self._stop_epoch
        threading.Thread(target=self._voice_record_worker, args=(epoch,), daemon=True).start()

    def _voice_record_worker(self, epoch: int) -> None:
        try:
            recording = record_wav(
                max_seconds=self.settings.voice_max_seconds,
                silence_seconds=self.settings.voice_silence_seconds,
            )
            if epoch != self._stop_epoch:
                return
            if recording is None:
                self._set_state("idle", "")
                if self.wake_listener:
                    self.wake_listener.resume()
                return

            self._set_state("thinking", "")
            text = transcribe_wav(self.provider, recording)
            if epoch != self._stop_epoch:
                return
            self._start_agent_request(text)
        except VoiceError as exc:
            if epoch == self._stop_epoch:
                self._on_worker_error("Voice Error", str(exc))
        except ProviderError as exc:
            if epoch == self._stop_epoch:
                self._on_worker_error("Transcription Error", str(exc))
        except Exception as exc:
            if epoch == self._stop_epoch:
                self._on_worker_error("Error", str(exc))

    def _on_entry_submit(self) -> None:
        query = self.input_entry.text().strip()
        if not query:
            return
        self.input_entry.clear()
        self.set_view_mode("compact")
        self._start_agent_request(query)

    def _start_agent_request(self, text: str) -> None:
        if self.worker.isRunning():
            return
        self._settle_timer.stop()
        self._stream_buffer = ""
        if self.wake_listener:
            self.wake_listener.pause()
        self._set_state("thinking", "")
        self.worker.set_request(text, self.history)
        self.worker.start()

    def _on_agent_model_call(self) -> None:
        self._set_state("thinking", "")

    def _on_agent_tool_start(self, tool_name: str) -> None:
        self._set_state("acting", tool_name)

    def _on_agent_chunk(self, text: str) -> None:
        """Render streamed model text in the HUD without stealing focus."""
        if not text:
            return
        self._stream_buffer = (self._stream_buffer + text)[-1200:]
        compact = " ".join(self._stream_buffer.split())
        if compact:
            self.hud_subtitle.setText(compact[-110:])
            self.status_lbl.setText(f"{self.label} · responding…")

    # ------------------------------------------------------------------ Approvals & Emergency Stop

    def _on_approval_requested(self, request: ApprovalRequest) -> None:
        # Stop may have dismissed the request before the queued signal arrived.
        if self.approver.answered:
            return
        decision = ApprovalDecision.DENY
        try:
            self.show()
            self.raise_()
            force_foreground_window(int(self.winId()))
            decision = self._ask_for_approval(request)
        except Exception:
            decision = ApprovalDecision.DENY
        finally:
            self.approver.resolve(decision)

    def _ask_for_approval(self, request: ApprovalRequest) -> ApprovalDecision:
        arguments = json.dumps(request.arguments, ensure_ascii=False, default=str)
        if len(arguments) > 900:
            arguments = arguments[:899] + "…"
        details = (
            f"Permission: {request.permission.value.upper()}\n\n"
            f"{request.description}\n\n"
            f"Arguments:\n{arguments}"
        )

        if request.is_high_risk:
            dialog = QInputDialog(self)
            dialog.setWindowTitle("Scout · high-risk action")
            dialog.setLabelText(
                f'Allow Scout to run "{request.tool}"?\n\n'
                f"{details}\n\nType 'allow' to confirm. Cancel stops the task."
            )
            dialog.setOkButtonText("Allow once")
            dialog.setCancelButtonText("Stop Scout")
            dialog.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)
            accepted = dialog.exec() == QDialog.DialogCode.Accepted
            if not accepted:
                EMERGENCY_STOP.stop_all()
                return ApprovalDecision.DENY
            if dialog.textValue().strip().lower() == "allow":
                return ApprovalDecision.ALLOW_ONCE
            return ApprovalDecision.DENY

        box = QMessageBox(self)
        box.setWindowTitle("Scout needs approval")
        box.setIcon(QMessageBox.Icon.Warning)
        box.setText(f'Allow Scout to run "{request.tool}"?')
        box.setInformativeText(details)
        box.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)
        allow_once = box.addButton("Allow once", QMessageBox.ButtonRole.AcceptRole)
        allow_session = box.addButton("Allow for session", QMessageBox.ButtonRole.ActionRole)
        deny = box.addButton("Deny", QMessageBox.ButtonRole.RejectRole)
        stop = box.addButton("Stop Scout", QMessageBox.ButtonRole.DestructiveRole)
        box.setDefaultButton(deny)
        box.exec()

        clicked = box.clickedButton()
        if clicked is allow_once:
            return ApprovalDecision.ALLOW_ONCE
        if clicked is allow_session:
            return ApprovalDecision.ALLOW_SESSION
        if clicked is stop:
            EMERGENCY_STOP.stop_all()
        return ApprovalDecision.DENY

    def emergency_stop(self) -> None:
        """Cancel the active run, dismiss approvals and discard pending voice."""
        self._stop_epoch += 1
        EMERGENCY_STOP.stop_all()
        self.approver.cancel_pending()
        running = self.worker.isRunning()
        self._set_state("cancelled", "")
        if running:
            self.status_lbl.setText("Stopping…")
            self.hud_subtitle.setText("Stopping…")
        else:
            self._settle_timer.start(1800)
            if self.wake_listener:
                self.wake_listener.resume()

    def _on_worker_cancelled(self, message: str) -> None:
        self._stream_buffer = ""
        self._set_state("cancelled", "")
        self._settle_timer.start(2200)
        if self.wake_listener:
            self.wake_listener.resume()
        self.summary_win.show_summary(
            "Scout · stopped",
            message or "The task was stopped before it finished.",
            error=False,
            anchor_rect=self.geometry(),
        )

    def _on_worker_finished(
        self, title: str, text: str, duration_s: float, tools: list[str]
    ) -> None:
        self._stream_buffer = ""
        self._set_state("success", "")
        self._settle_timer.start(2200)
        if self.wake_listener:
            self.wake_listener.resume()

        # Update history
        self.history.append(UserTurn(text=self.worker.request_text))
        self.history.append(AssistantTurn(text=text))
        self.history = self.history[-40:]

        # Pop up properly shaped executive summary card!
        anchor = self.geometry()
        self.summary_win.show_summary(title, text, error=False, anchor_rect=anchor)

    def _on_worker_error(self, title: str, message: str) -> None:
        self._stream_buffer = ""
        self._set_state("error", message)
        self._settle_timer.start(2800)
        if self.wake_listener:
            self.wake_listener.resume()
        anchor = self.geometry()
        self.summary_win.show_summary(title, message, error=True, anchor_rect=anchor)

    def _set_state(self, state: str, detail: str = "") -> None:
        self.state = state
        self.active_tool = detail

        # Update status labels
        msg = f"{self.label} · Ready"
        if state == "listening":
            msg = "Listening…"
        elif state == "thinking":
            msg = "Thinking…"
        elif state == "acting":
            msg = f"Running {detail}…" if detail else "Working…"
        elif state == "success":
            msg = "Done"
        elif state == "error":
            msg = "Error occurred"
        elif state == "cancelled":
            msg = "Stopped"

        self.status_lbl.setText(msg)
        self.hud_subtitle.setText(msg)
        self.wave_widget.set_state(state)
        self._update_card_style()
        if self.companion is not None:
            self.companion.set_state(state, detail)

    def _on_anim_frame(self) -> None:
        self.wave_widget.advance()
        if self.view_mode == "expanded":
            self.orb_widget.advance()

    # ------------------------------------------------------------------ Window Positioning & Mouse Dragging

    def _position_file(self) -> Path:
        return self.settings.data_dir / "overlay_position_qt.json"

    def _place_window(self) -> None:
        screen = QApplication.primaryScreen().geometry()
        x = max(20, screen.width() - self.width() - 40)
        y = max(20, screen.height() - self.height() - 100)
        try:
            saved = json.loads(self._position_file().read_text(encoding="utf-8"))
            sx, sy = int(saved["x"]), int(saved["y"])
            if 0 <= sx <= screen.width - self.width() and 0 <= sy <= screen.height - self.height():
                x, y = sx, sy
        except Exception:
            pass
        self.move(x, y)
        self.set_view_mode("compact")
        self.show()
        if self.companion is not None:
            self.companion.show()
            self.companion.raise_()

    def _save_position(self) -> None:
        with contextlib.suppress(OSError):
            self._position_file().write_text(
                json.dumps({"x": self.x(), "y": self.y()}),
                encoding="utf-8",
            )

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_pos = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            event.accept()

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        if event.buttons() == Qt.MouseButton.LeftButton and not self._drag_pos.isNull():
            self.move(event.globalPosition().toPoint() - self._drag_pos)
            event.accept()

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        self._save_position()
        self._drag_pos = QPoint()

    def mouseDoubleClickEvent(self, _event) -> None:  # noqa: N802
        self._toggle_expand()

    def contextMenuEvent(self, event) -> None:  # noqa: N802
        menu = QMenu(self)
        menu.setStyleSheet("""
            QMenu {
                background-color: #ffffff;
                color: #0f172a;
                border: 1px solid #e2e8f0;
                border-radius: 8px;
                padding: 4px;
                font-family: 'Segoe UI';
                font-size: 11px;
            }
            QMenu::item:selected {
                background-color: #ede9fe;
                color: #6d28d9;
                border-radius: 4px;
            }
        """)

        act_talk = menu.addAction("🎙️ Talk to Scout")
        act_talk.triggered.connect(self.start_voice)

        act_stop = menu.addAction("■ Stop Scout")
        act_stop.triggered.connect(self.emergency_stop)

        act_type = menu.addAction("💬 Type a Prompt...")
        act_type.triggered.connect(lambda: self.set_view_mode("drawer"))

        act_orbit = menu.addAction("🪐 Capabilities Orbit (Ref 1)")
        act_orbit.triggered.connect(lambda: self.set_view_mode("orbit"))

        act_hud = menu.addAction("✨ Toggle HUD View")
        act_hud.triggered.connect(self._toggle_expand)

        act_companion = menu.addAction("🐈 Show/Hide Scout")
        act_companion.triggered.connect(self._toggle_companion)

        menu.addSeparator()
        access = "Enabled" if self.settings.full_access else "Off · approvals required"
        act_access = menu.addAction(f"⚡ Full Access: {access}")
        act_access.setEnabled(False)

        act_key = menu.addAction("🔑 Hotkey: Ctrl+Alt+S")
        act_key.setEnabled(False)

        menu.addSeparator()
        act_quit = menu.addAction("✕ Quit Scout")
        act_quit.triggered.connect(self.close)

        menu.exec(event.globalPos())

    # ------------------------------------------------------------------ System Tray

    def _setup_tray(self) -> None:
        if not QSystemTrayIcon.isSystemTrayAvailable():
            return
        icon = QIcon(str(ICO_PATH)) if ICO_PATH.exists() else self.windowIcon()
        self.tray = QSystemTrayIcon(icon, self)
        self.tray.setToolTip("Scout Assistant")

        tray_menu = QMenu()
        tray_menu.setStyleSheet("""
            QMenu {
                background-color: #ffffff;
                color: #0f172a;
                border: 1px solid #e2e8f0;
                border-radius: 8px;
                padding: 4px;
                font-family: 'Segoe UI';
                font-size: 11px;
            }
            QMenu::item:selected {
                background-color: #ede9fe;
                color: #6d28d9;
                border-radius: 4px;
            }
        """)

        t_talk = tray_menu.addAction("🎙️ Talk to Scout")
        t_talk.triggered.connect(self.start_voice)

        t_stop = tray_menu.addAction("■ Stop Scout")
        t_stop.triggered.connect(self.emergency_stop)

        t_hud = tray_menu.addAction("✨ Show/Hide Assistant")
        t_hud.triggered.connect(self.wake_and_listen)

        t_companion = tray_menu.addAction("🐈 Show/Hide Scout")
        t_companion.triggered.connect(self._toggle_companion)

        tray_menu.addSeparator()
        t_quit = tray_menu.addAction("✕ Quit Scout")
        t_quit.triggered.connect(self.close)

        self.tray.setContextMenu(tray_menu)
        self.tray.activated.connect(
            lambda r: (
                self.wake_and_listen() if r == QSystemTrayIcon.ActivationReason.Trigger else None
            )
        )
        self.tray.show()

    # ------------------------------------------------------------------ Lifecycle

    def close(self) -> None:
        self.closeEvent(None)
        app = QApplication.instance()
        if app:
            app.quit()

    def closeEvent(self, event) -> None:  # noqa: N802
        EMERGENCY_STOP.stop_all()
        with contextlib.suppress(Exception):
            self.approver.cancel_pending()
        with contextlib.suppress(Exception):
            if self.worker.isRunning():
                self.worker.wait(2000)
        if self.hotkey_listener:
            with contextlib.suppress(Exception):
                self.hotkey_listener.stop()
        if self.wake_listener:
            with contextlib.suppress(Exception):
                self.wake_listener.stop()
        if hasattr(self, "tray") and self.tray:
            self.tray.hide()
        if hasattr(self, "summary_win"):
            self.summary_win.close()
        if self.companion is not None:
            self.companion.close()
        self._save_position()
        if event:
            event.accept()


# ----------------------------------------------------------------------
# 5. Entry Point Function
# ----------------------------------------------------------------------


def run_overlay(
    settings: Settings,
    *,
    opacity: float | None = None,
    auto_listen: bool = False,
    enable_wake_word: bool = True,
    enable_hotkey: bool = True,
    enable_companion: bool = True,
) -> None:
    """Launch the professional PyQt6 Scout Overlay."""
    # Ensure Windows taskbar grouping uses AppUserModelID
    if os.name == "nt":
        with contextlib.suppress(Exception):
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("Scout.Agent.Overlay.v1")

    app = QApplication.instance()
    owns_app = False
    if app is None:
        app = QApplication(sys.argv)
        owns_app = True

    if ICO_PATH.exists():
        app.setWindowIcon(QIcon(str(ICO_PATH)))

    window = ScoutOverlayWindow(
        settings,
        opacity=opacity,
        auto_listen=auto_listen,
        enable_wake_word=enable_wake_word,
        enable_hotkey=enable_hotkey,
        enable_companion=enable_companion,
    )
    # Keep the Python wrapper alive for the application's lifetime, even when
    # run_overlay does not own the QApplication (tests, embedded callers).
    app._scout_window = window  # type: ignore[attr-defined]

    autoclose = os.environ.get("SCOUT_OVERLAY_AUTOCLOSE_MS", "").strip()
    if autoclose.isdigit():
        QTimer.singleShot(int(autoclose), app.quit)

    if owns_app:
        app.exec()
