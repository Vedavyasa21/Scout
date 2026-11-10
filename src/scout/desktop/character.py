"""Scout's character: a small, expressive desktop companion.

The renderer is a pure consumer of agent events: it draws a procedural 2.5D
version of Scout (fluffy dark fur, cyan eyes, goggles and an orange scarf) and
plays a different animation for each assistant state. It never drives the agent,
so a rendering problem can never stop an actual task.

The character is painted with QPainter rather than loaded from a 3D file, so it
stays crisp at any DPI, needs no GPU or web engine, and reacts instantly.
"""

from __future__ import annotations

import contextlib
import json
import math
from dataclasses import dataclass
from pathlib import Path

from PyQt6.QtCore import (
    QElapsedTimer,
    QPoint,
    QPointF,
    QRectF,
    Qt,
    QTimer,
    pyqtSignal,
)
from PyQt6.QtGui import (
    QBrush,
    QColor,
    QCursor,
    QFont,
    QFontMetrics,
    QGuiApplication,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
    QRadialGradient,
)
from PyQt6.QtWidgets import QMenu, QWidget

# Design canvas: the character is drawn in a 220x240 box and placed below the
# status bubble (which occupies the top of the widget).
DESIGN_W = 220.0
DESIGN_H = 240.0
CHAR_X = 12.0
CHAR_Y = 46.0
WIDGET_W = 244
WIDGET_H = 292

FUR_TOP = QColor("#2c2d38")
FUR_MID = QColor("#191a23")
FUR_LOW = QColor("#0b0c11")
FUR_EDGE = QColor("#08090d")
FUR_LIGHT = QColor("#3d3f4d")
ORANGE_TOP = QColor("#ffa84f")
ORANGE_MID = QColor("#f97c22")
ORANGE_DEEP = QColor("#cf5413")
CYAN = QColor("#4fe3f7")
PUPIL = QColor("#05090c")

STATE_ALIASES = {
    "idle": "idle",
    "ready": "idle",
    "online": "idle",
    "listening": "listening",
    "listen": "listening",
    "hearing": "listening",
    "thinking": "thinking",
    "planning": "thinking",
    "processing": "thinking",
    "acting": "working",
    "working": "working",
    "running": "working",
    "tool": "working",
    "executing": "working",
    "speaking": "speaking",
    "talking": "speaking",
    "success": "success",
    "done": "success",
    "complete": "success",
    "error": "error",
    "failed": "error",
    "failure": "error",
    "cancelled": "cancelled",
    "canceled": "cancelled",
    "stopped": "cancelled",
    "stop": "cancelled",
    "sleeping": "sleeping",
    "sleep": "sleeping",
    "resting": "sleeping",
}

# States that play once and then settle back to idle on their own.
TRANSIENT_STATES = {"success": 2.0, "error": 2.6, "cancelled": 1.8}

BUBBLES = {
    "idle": "Ready",
    "listening": "Listening…",
    "thinking": "Thinking…",
    "speaking": "Speaking…",
    "success": "Done!",
    "error": "Something went wrong",
    "cancelled": "Stopped",
}


def normalize_state(state: str) -> str:
    """Map any agent state name onto one of the nine animation states."""
    key = (state or "").strip().lower().replace("_", "").replace("-", "")
    aliases = {name.replace("_", ""): value for name, value in STATE_ALIASES.items()}
    return aliases.get(key, "idle")


def _noise(index: int, seed: int = 0) -> float:
    """Deterministic pseudo-random value in [0, 1) so fur never flickers."""
    value = math.sin(index * 12.9898 + seed * 78.233 + 0.5) * 43758.5453
    return value - math.floor(value)


def _fur_path(
    cx: float,
    cy: float,
    rx: float,
    ry: float,
    points: int,
    seed: int,
    t: float,
    amplitude: float = 0.08,
) -> QPainterPath:
    """A ring of soft fur spikes around an ellipse."""
    path = QPainterPath()
    for index in range(points):
        angle = math.tau * index / points
        if index % 2:
            scale = 1.0 + amplitude * 0.18
        else:
            scale = 1.0 + amplitude * (0.35 + 0.65 * _noise(index, seed))
        sway = 1.0 + 0.035 * math.sin(t * 1.4 + index * 0.47)
        x = cx + math.cos(angle) * rx * scale * sway
        y = cy + math.sin(angle) * ry * scale * sway
        if index == 0:
            path.moveTo(x, y)
        else:
            path.lineTo(x, y)
    path.closeSubpath()
    return path


@dataclass
class _Pose:
    """Everything that changes from frame to frame, resolved per state."""

    bob: float = 0.0
    breathe: float = 1.0
    tilt: float = 0.0
    eye_open: float = 1.0
    happy: bool = False
    mouth: float = 0.1
    look_x: float = 0.0
    look_y: float = 0.0
    right_paw: tuple[float, float] = (176.0, 104.0)
    right_elbow: tuple[float, float] = (172.0, 144.0)
    left_paw: tuple[float, float] = (50.0, 182.0)
    left_elbow: tuple[float, float] = (56.0, 166.0)
    cape: float = 0.0
    antenna: float = 0.0
    jump: float = 0.0
    sparkle: float = 0.0
    thinking: float = 0.0
    sleeping: bool = False


class ScoutCharacterWidget(QWidget):
    """Draws Scout and animates him for the current assistant state."""

    activated = pyqtSignal()
    quit_requested = pyqtSignal()
    stop_requested = pyqtSignal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedSize(WIDGET_W, WIDGET_H)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

        self._time = 0.0
        self._state = "idle"
        self._detail = ""
        self._since = 0.0
        self._bubble_since = 0.0
        self._bubble_until = 0.0
        self._next_blink = 2.6
        self._blink_left = 0.0
        self._bubble_text = ""

        self._press_global: QPoint | None = None
        self._window_origin: QPoint | None = None
        self._dragging = False

        self._clock = QElapsedTimer()
        self._clock.start()
        self._last_tick = 0.0

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._on_tick)
        self._timer.start(33)  # ~30 fps: smooth without waking the CPU

    # ------------------------------------------------------------------ State

    def state(self) -> str:
        return self._state

    def set_state(self, state: str, detail: str = "") -> None:
        """React to an assistant event. Safe to call from the GUI thread."""
        target = normalize_state(state)
        if target == self._state and detail == self._detail:
            return
        self._state = target
        self._detail = detail.strip()
        self._since = self._time
        self._bubble_since = self._time
        self._bubble_until = self._time + (4.5 if target in TRANSIENT_STATES else 3.2)
        if target == "sleeping":
            self._bubble_until = 0.0
        self.update()

    def advance(self, dt: float) -> None:
        """Advance the animation clock (also used by tests and previews)."""
        dt = max(0.0, min(dt, 0.25))
        self._time += dt

        self._next_blink -= dt
        if self._next_blink <= 0.0 and self._state not in {"sleeping", "error"}:
            self._blink_left = 0.16
            self._next_blink = 2.2 + _noise(int(self._time * 37.0), 11) * 2.8
        if self._blink_left > 0.0:
            self._blink_left = max(0.0, self._blink_left - dt)

        duration = TRANSIENT_STATES.get(self._state)
        if duration is not None and self._time - self._since >= duration:
            self._state = "idle"
            self._detail = ""
            self._since = self._time

        if self._state == "idle" and self._time - self._since > 150.0:
            self._state = "sleeping"
            self._since = self._time
            self._bubble_until = 0.0

        self.update()

    def _on_tick(self) -> None:
        now = self._clock.elapsed() / 1000.0
        dt = now - self._last_tick
        self._last_tick = now
        self.advance(dt)

    def _blink_factor(self) -> float:
        if self._blink_left <= 0.0:
            return 1.0
        progress = 1.0 - self._blink_left / 0.16
        return max(0.05, 1.0 - math.sin(math.pi * progress))

    def _cursor_look(self) -> tuple[float, float]:
        if not self.isVisible():
            return (0.0, 0.0)
        local = self.mapFromGlobal(QCursor.pos())
        center_x = CHAR_X + 112.0
        center_y = CHAR_Y + 96.0
        dx = (local.x() - center_x) / 170.0
        dy = (local.y() - center_y) / 170.0
        dx = max(-1.0, min(1.0, dx))
        dy = max(-1.0, min(1.0, dy))
        return (dx * 3.6, dy * 3.2)

    # ------------------------------------------------------------------ Poses

    def _pose(self) -> _Pose:
        t = self._time
        since = t - self._since
        look_x, look_y = self._cursor_look()
        pose = _Pose(
            bob=math.sin(t * 1.7) * 1.1,
            breathe=1.0 + math.sin(t * 1.9) * 0.007,
            tilt=math.sin(t * 0.8) * 1.2,
            mouth=0.10 + 0.04 * (0.5 + 0.5 * math.sin(t * 2.2)),
            look_x=look_x,
            look_y=look_y,
            cape=math.sin(t * 1.25) * 3.0,
            antenna=math.sin(t * 1.9) * 2.4,
        )

        if self._state == "listening":
            pose.tilt = 6.0 + math.sin(t * 1.8) * 1.2
            pose.eye_open = 1.08
            pose.bob = math.sin(t * 1.4) * 0.7
            pose.breathe = 1.0 + math.sin(t * 2.6) * 0.004
            pose.look_x = look_x * 1.35
            pose.look_y = look_y * 1.35
            pose.right_paw = (181.0, 96.0)
            pose.right_elbow = (175.0, 140.0)
            pose.left_paw = (44.0, 190.0)
            pose.left_elbow = (52.0, 172.0)
            pose.antenna = math.sin(t * 3.4) * 1.2
            pose.cape = math.sin(t * 1.6) * 2.0
            pose.mouth = 0.06

        elif self._state == "thinking":
            pose.tilt = -5.0 + math.sin(t * 1.1) * 1.0
            pose.bob = -2.0 + math.sin(t * 2.4) * 1.5
            pose.eye_open = 0.92
            pose.look_x = math.sin(t * 2.6) * 3.0
            pose.look_y = -3.0 + math.sin(t * 1.7) * 0.8
            pose.right_paw = (150.0, 132.0)
            pose.right_elbow = (168.0, 150.0)
            pose.thinking = 1.0
            pose.mouth = 0.05
            pose.antenna = math.sin(t * 2.6) * 3.4

        elif self._state == "working":
            swing = math.sin(t * 9.0)
            pose.tilt = 3.0
            pose.bob = math.sin(t * 6.0) * 1.3
            pose.eye_open = 0.82
            pose.mouth = 0.08
            pose.look_y = 1.6
            pose.look_x = 2.0
            pose.right_paw = (148.0 + swing * 5.0, 198.0)
            pose.right_elbow = (171.0, 172.0)
            pose.left_paw = (88.0 - swing * 5.0, 200.0)
            pose.left_elbow = (60.0, 174.0)
            pose.cape = math.sin(t * 3.0) * 4.0
            pose.antenna = math.sin(t * 5.0) * 2.0

        elif self._state == "speaking":
            pose.mouth = 0.5 + 0.5 * math.sin(t * 15.0)
            pose.bob = math.sin(t * 8.0) * 1.6
            pose.tilt = math.sin(t * 3.0) * 3.0
            pose.eye_open = 1.0
            pose.right_paw = (186.0, 112.0 + math.sin(t * 6.0) * 8.0)
            pose.right_elbow = (176.0, 150.0)
            pose.cape = math.sin(t * 2.4) * 3.0
            pose.antenna = math.sin(t * 4.2) * 2.8

        elif self._state == "success":
            if since < 1.25:
                pose.jump = abs(math.sin(since * math.pi / 0.62)) * 20.0
            pose.happy = True
            pose.mouth = 0.8
            pose.sparkle = 1.0
            pose.tilt = math.sin(t * 10.0) * 2.6
            pose.right_paw = (201.0, 60.0)
            pose.right_elbow = (191.0, 130.0)
            pose.left_paw = (23.0, 64.0)
            pose.left_elbow = (33.0, 132.0)
            pose.cape = math.sin(t * 8.0) * 5.0
            pose.antenna = math.sin(t * 9.0) * 5.0

        elif self._state == "error":
            pose.tilt = -7.0 + math.sin(t * 1.2) * 0.8
            pose.bob = 1.5
            pose.eye_open = 0.45
            pose.mouth = 0.0
            pose.look_y = 2.6
            pose.right_paw = (172.0, 178.0)
            pose.right_elbow = (170.0, 158.0)
            pose.left_paw = (54.0, 190.0)
            pose.left_elbow = (58.0, 172.0)
            pose.antenna = -7.0
            pose.cape = math.sin(t * 0.9) * 1.4

        elif self._state == "cancelled":
            pose.tilt = math.sin(t * 2.0) * 1.6
            pose.bob = math.sin(t * 2.4) * 1.2
            pose.eye_open = 1.12
            pose.mouth = 0.18
            pose.right_paw = (180.0, 104.0)
            pose.right_elbow = (176.0, 148.0)
            pose.left_paw = (46.0, 186.0)
            pose.left_elbow = (54.0, 172.0)
            pose.cape = math.sin(t * 1.4) * 2.0
            pose.antenna = -3.0 + math.sin(t * 3.0) * 1.5

        elif self._state == "sleeping":
            pose.eye_open = 0.0
            pose.tilt = 8.0
            pose.bob = math.sin(t * 0.8) * 1.8
            pose.breathe = 1.0 + math.sin(t * 1.1) * 0.014
            pose.mouth = 0.0
            pose.sleeping = True
            pose.look_x = 0.0
            pose.look_y = 0.0
            pose.right_paw = (174.0, 186.0)
            pose.right_elbow = (172.0, 166.0)
            pose.left_paw = (52.0, 192.0)
            pose.left_elbow = (58.0, 174.0)
            pose.antenna = -4.0
            pose.cape = math.sin(t * 0.6) * 1.0

        pose.eye_open *= self._blink_factor()
        return pose

    # ------------------------------------------------------------------ Bubble

    def _bubble_alpha(self) -> float:
        if not self._bubble_text:
            return 0.0
        remaining = self._bubble_until - self._time
        if remaining <= 0.0:
            return 0.0
        fade_in = min(1.0, (self._time - self._bubble_since) / 0.18)
        fade_out = min(1.0, remaining / 0.45)
        return max(0.0, min(fade_in, fade_out))

    def _bubble_label(self) -> str:
        if self._state == "working":
            return f"Working · {self._detail}" if self._detail else "Working…"
        if self._state == "error" and self._detail:
            text = self._detail.strip().splitlines()[0]
            return f"Error · {text}"
        return BUBBLES.get(self._state, "")

    def _draw_bubble(self, painter: QPainter) -> None:
        self._bubble_text = self._bubble_label()
        alpha = self._bubble_alpha()
        if alpha <= 0.0 or not self._bubble_text:
            return

        font = QFont("Segoe UI", 8)
        font.setWeight(QFont.Weight.DemiBold)
        metrics = QFontMetrics(font)
        max_width = WIDGET_W - 34
        text = metrics.elidedText(self._bubble_text, Qt.TextElideMode.ElideRight, max_width)
        width = min(max_width, metrics.horizontalAdvance(text) + 28)
        height = 27.0
        x = (self.width() - width) / 2.0
        y = 5.0

        painter.save()
        painter.setOpacity(alpha)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        path = QPainterPath()
        path.addRoundedRect(QRectF(x, y, width, height), 13.5, 13.5)
        painter.setBrush(QBrush(QColor(255, 255, 255, 242)))
        painter.setPen(QPen(QColor(206, 218, 234), 1.0))
        painter.drawPath(path)

        tail = QPainterPath()
        tail.moveTo(x + width / 2.0 - 5.0, y + height - 1.0)
        tail.lineTo(x + width / 2.0, y + height + 6.0)
        tail.lineTo(x + width / 2.0 + 5.0, y + height - 1.0)
        tail.closeSubpath()
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(QColor(255, 255, 255, 242)))
        painter.drawPath(tail)

        painter.setFont(font)
        painter.setPen(QColor(71, 85, 105))
        painter.drawText(
            QRectF(x, y, width, height),
            int(Qt.AlignmentFlag.AlignCenter),
            text,
        )
        painter.restore()

    # ------------------------------------------------------------------ Drawing

    def paintEvent(self, _event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)

        pose = self._pose()
        painter.save()
        painter.translate(CHAR_X, CHAR_Y + pose.bob - pose.jump)

        self._draw_halo(painter)
        self._draw_shadow(painter, pose)

        painter.save()
        painter.translate(110.0, 226.0)
        painter.scale(1.0, pose.breathe)
        painter.translate(-110.0, -226.0)

        self._draw_cape(painter, pose)
        self._draw_legs(painter)
        self._draw_body(painter)
        self._draw_straps(painter)
        self._draw_arms(painter, pose)
        self._draw_scarf(painter, pose)
        self._draw_head(painter, pose)
        painter.restore()

        if pose.sparkle:
            self._draw_sparkles(painter)
        if pose.thinking:
            self._draw_thoughts(painter)
        if pose.sleeping:
            self._draw_sleep(painter)

        painter.restore()
        self._draw_bubble(painter)

    def _draw_halo(self, painter: QPainter) -> None:
        gradient = QRadialGradient(QPointF(110.0, 108.0), 120.0)
        gradient.setColorAt(0.0, QColor(120, 226, 245, 26))
        gradient.setColorAt(0.55, QColor(120, 226, 245, 10))
        gradient.setColorAt(1.0, QColor(120, 226, 245, 0))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(gradient))
        painter.drawEllipse(QPointF(110.0, 108.0), 120.0, 120.0)

    def _draw_shadow(self, painter: QPainter, pose: _Pose) -> None:
        squeeze = max(0.45, 1.0 - pose.jump / 55.0)
        gradient = QRadialGradient(QPointF(110.0, 228.0), 66.0)
        gradient.setColorAt(0.0, QColor(4, 6, 10, 78))
        gradient.setColorAt(0.7, QColor(4, 6, 10, 26))
        gradient.setColorAt(1.0, QColor(4, 6, 10, 0))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(gradient))
        painter.drawEllipse(QPointF(110.0, 228.0), 66.0 * squeeze, 12.0 * squeeze)

    def _draw_cape(self, painter: QPainter, pose: _Pose) -> None:
        sway = pose.cape
        path = QPainterPath(QPointF(98.0, 126.0))
        path.cubicTo(
            QPointF(58.0, 128.0),
            QPointF(20.0, 142.0 + sway),
            QPointF(6.0, 177.0 + sway * 0.5),
        )
        path.cubicTo(
            QPointF(16.0, 188.0 + sway),
            QPointF(34.0, 180.0),
            QPointF(52.0, 191.0 + sway),
        )
        path.cubicTo(QPointF(68.0, 174.0), QPointF(82.0, 162.0), QPointF(103.0, 158.0))
        path.closeSubpath()

        gradient = QLinearGradient(0.0, 126.0, 60.0, 196.0)
        gradient.setColorAt(0.0, ORANGE_TOP)
        gradient.setColorAt(0.55, ORANGE_MID)
        gradient.setColorAt(1.0, ORANGE_DEEP)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(gradient))
        painter.drawPath(path)

        fold = QPainterPath(QPointF(82.0, 140.0))
        fold.cubicTo(
            QPointF(50.0, 146.0),
            QPointF(30.0, 158.0 + sway * 0.4),
            QPointF(22.0, 178.0 + sway * 0.4),
        )
        fold.cubicTo(
            QPointF(38.0, 170.0),
            QPointF(54.0, 170.0),
            QPointF(68.0, 174.0),
        )
        fold.closeSubpath()
        painter.setBrush(QBrush(QColor(197, 78, 14, 120)))
        painter.drawPath(fold)

        highlight = QPainterPath(QPointF(96.0, 129.0))
        highlight.cubicTo(
            QPointF(62.0, 133.0),
            QPointF(28.0, 146.0 + sway),
            QPointF(12.0, 172.0 + sway * 0.5),
        )
        pen = QPen(QColor(255, 214, 160, 160), 1.8)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawPath(highlight)

    def _draw_legs(self, painter: QPainter) -> None:
        for cx in (84.0, 136.0):
            painter.setPen(QPen(QColor("#08090d"), 1.2))
            painter.setBrush(QColor("#15161d"))
            painter.drawEllipse(QPointF(cx, 208.0), 18.0, 15.0)
            painter.setPen(QPen(QColor("#343644"), 1.5))
            for dx in (-5.0, 0.0, 5.0):
                painter.drawArc(
                    QRectF(cx + dx - 4.0, 205.0, 8.0, 10.0),
                    205 * 16,
                    130 * 16,
                )

    def _draw_body(self, painter: QPainter) -> None:
        body = _fur_path(110.0, 172.0, 47.0, 47.0, 56, 5, self._time, 0.06)
        gradient = QLinearGradient(0.0, 126.0, 0.0, 220.0)
        gradient.setColorAt(0.0, FUR_TOP)
        gradient.setColorAt(0.5, FUR_MID)
        gradient.setColorAt(1.0, FUR_LOW)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(gradient))
        painter.drawPath(body)

        # Faint rim light so the silhouette reads on dark wallpapers.
        rim = _fur_path(110.0, 172.0, 47.0, 47.0, 56, 5, self._time, 0.06)
        pen = QPen(QColor(120, 210, 232, 30), 2.4)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawPath(rim)

    def _draw_straps(self, painter: QPainter) -> None:
        pen = QPen(QColor("#4a3324"), 6.0)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawLine(QPointF(93.0, 150.0), QPointF(106.0, 196.0))
        painter.drawLine(QPointF(130.0, 150.0), QPointF(118.0, 196.0))

        painter.setPen(QPen(QColor("#2a1d13"), 1.0))
        painter.setBrush(QColor("#d9a441"))
        for x, y in ((102.0, 173.0), (121.0, 173.0)):
            painter.drawRoundedRect(QRectF(x - 5.0, y - 4.0, 10.0, 8.0), 2.0, 2.0)

    def _draw_arm(
        self,
        painter: QPainter,
        shoulder: tuple[float, float],
        elbow: tuple[float, float],
        paw: tuple[float, float],
    ) -> None:
        path = QPainterPath(QPointF(*shoulder))
        path.quadTo(QPointF(*elbow), QPointF(*paw))

        pen = QPen(QColor("#191a22"), 18.5)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawPath(path)

        painter.setPen(QPen(QColor(126, 212, 232, 26), 4.0))
        painter.save()
        painter.translate(-1.2, -1.6)
        painter.drawPath(path)
        painter.restore()

        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor("#1b1c24"))
        painter.drawEllipse(QPointF(*paw), 11.3, 11.3)
        painter.setPen(QPen(QColor("#3a3c48"), 1.3))
        for dx in (-3.5, 0.0, 3.5):
            painter.drawLine(
                QPointF(paw[0] + dx, paw[1] + 1.5),
                QPointF(paw[0] + dx, paw[1] + 6.0),
            )

    def _draw_arms(self, painter: QPainter, pose: _Pose) -> None:
        self._draw_arm(painter, (64.0, 154.0), pose.left_elbow, pose.left_paw)
        self._draw_arm(painter, (152.0, 152.0), pose.right_elbow, pose.right_paw)

    def _draw_scarf(self, painter: QPainter, pose: _Pose) -> None:
        band = QPainterPath(QPointF(56.0, 132.0))
        band.cubicTo(QPointF(78.0, 158.0), QPointF(142.0, 158.0), QPointF(164.0, 128.0))
        band.lineTo(QPointF(174.0, 146.0))
        band.cubicTo(QPointF(148.0, 180.0), QPointF(76.0, 176.0), QPointF(46.0, 150.0))
        band.closeSubpath()

        gradient = QLinearGradient(0.0, 126.0, 0.0, 176.0)
        gradient.setColorAt(0.0, ORANGE_TOP)
        gradient.setColorAt(0.5, ORANGE_MID)
        gradient.setColorAt(1.0, ORANGE_DEEP)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(gradient))
        painter.drawPath(band)

        highlight = QPainterPath(QPointF(62.0, 134.0))
        highlight.cubicTo(QPointF(84.0, 152.0), QPointF(138.0, 154.0), QPointF(162.0, 132.0))
        pen = QPen(QColor(255, 214, 160, 150), 2.0)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawPath(highlight)

        # Knot between the two scarf ends.
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor("#ff8c33"))
        painter.drawRoundedRect(QRectF(114.0, 136.0, 30.0, 24.0), 9.0, 9.0)
        painter.setBrush(QColor(207, 84, 19, 110))
        painter.drawRoundedRect(QRectF(120.0, 148.0, 18.0, 8.0), 4.0, 4.0)

        self._draw_medallion(painter)

    def _draw_medallion(self, painter: QPainter) -> None:
        center = QPointF(129.0, 155.0)

        glow = QRadialGradient(center, 17.0)
        glow.setColorAt(0.0, QColor(120, 250, 255, 120))
        glow.setColorAt(1.0, QColor(120, 250, 255, 0))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(glow))
        painter.drawEllipse(center, 17.0, 17.0)

        silver = QRadialGradient(QPointF(125.0, 150.0), 16.0)
        silver.setColorAt(0.0, QColor("#f4f8fb"))
        silver.setColorAt(0.6, QColor("#b8c3ce"))
        silver.setColorAt(1.0, QColor("#7b8794"))
        painter.setBrush(QBrush(silver))
        painter.setPen(QPen(QColor("#e9eef3"), 1.6))
        painter.drawEllipse(center, 12.0, 12.0)

        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor("#0b3140"))
        painter.drawEllipse(center, 8.6, 8.6)

        star = QPainterPath()
        for index in range(8):
            angle = -math.pi / 2.0 + index * math.pi / 4.0
            radius = 7.6 if index % 2 == 0 else 2.9
            point = QPointF(
                center.x() + math.cos(angle) * radius,
                center.y() + math.sin(angle) * radius,
            )
            if index == 0:
                star.moveTo(point)
            else:
                star.lineTo(point)
        star.closeSubpath()
        painter.setBrush(QColor("#b7fbff"))
        painter.drawPath(star)

    def _draw_head(self, painter: QPainter, pose: _Pose) -> None:
        painter.save()
        painter.translate(112.0, 138.0)
        painter.rotate(pose.tilt)
        painter.translate(-112.0, -138.0)

        head = _fur_path(112.0, 91.0, 59.0, 54.0, 84, 3, self._time, 0.10)
        gradient = QLinearGradient(0.0, 34.0, 0.0, 148.0)
        gradient.setColorAt(0.0, FUR_TOP)
        gradient.setColorAt(0.45, FUR_MID)
        gradient.setColorAt(1.0, FUR_LOW)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(gradient))
        painter.drawPath(head)

        # Soft top-left sheen.
        sheen = QRadialGradient(QPointF(88.0, 66.0), 62.0)
        sheen.setColorAt(0.0, QColor(160, 210, 235, 20))
        sheen.setColorAt(1.0, QColor(160, 210, 235, 0))
        painter.setBrush(QBrush(sheen))
        painter.drawEllipse(QPointF(88.0, 66.0), 62.0, 62.0)

        rim = QPainterPath(QPointF(60.0, 80.0))
        rim.quadTo(QPointF(70.0, 38.0), QPointF(122.0, 40.0))
        pen = QPen(QColor(140, 224, 242, 46), 2.6)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawPath(rim)

        # Fur tufts around the face break up the silhouette.
        painter.setPen(QPen(QColor(58, 60, 74, 150), 1.4))
        for index in range(9):
            angle = math.pi * (1.08 + index * 0.105)
            x = 112.0 + math.cos(angle) * 52.0
            y = 91.0 + math.sin(angle) * 46.0
            painter.drawLine(
                QPointF(x, y),
                QPointF(x + math.cos(angle) * 9.0, y + math.sin(angle) * 9.0),
            )

        self._draw_face(painter, pose)
        self._draw_goggles(painter)
        self._draw_antenna(painter, pose)
        painter.restore()

    def _draw_eye(
        self,
        painter: QPainter,
        center: tuple[float, float],
        look_x: float,
        look_y: float,
        open_amount: float,
    ) -> None:
        painter.save()
        painter.translate(center[0], center[1])
        painter.scale(1.0, max(0.05, open_amount))

        painter.setBrush(QBrush(QColor("#f8fdff")))
        painter.setPen(QPen(QColor("#1d1e27"), 2.0))
        painter.drawEllipse(QPointF(0.0, 0.0), 19.0, 22.0)

        iris = QRadialGradient(QPointF(-2.0 + look_x, -5.0 + look_y), 25.0)
        iris.setColorAt(0.0, QColor("#c6fcff"))
        iris.setColorAt(0.32, QColor("#5fe6f8"))
        iris.setColorAt(0.72, QColor("#11a7c4"))
        iris.setColorAt(1.0, QColor("#086b83"))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(iris))
        painter.drawEllipse(QPointF(look_x, look_y + 2.0), 12.0, 14.5)

        painter.setBrush(PUPIL)
        painter.drawEllipse(QPointF(look_x, look_y + 3.0), 6.2, 9.2)

        painter.setBrush(QColor(255, 255, 255, 245))
        painter.drawEllipse(QPointF(look_x - 5.0, look_y - 6.0), 4.6, 4.2)
        painter.setBrush(QColor(255, 255, 255, 160))
        painter.drawEllipse(QPointF(look_x + 4.0, look_y + 8.0), 2.1, 2.0)

        painter.restore()

    def _draw_face(self, painter: QPainter, pose: _Pose) -> None:
        if pose.happy:
            pen = QPen(QColor("#eafcff"), 4.4)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            painter.setPen(pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            for cx in (88.0, 136.0):
                arc = QPainterPath(QPointF(cx - 13.0, 98.0))
                arc.quadTo(QPointF(cx, 87.0), QPointF(cx + 13.0, 98.0))
                painter.drawPath(arc)
        elif pose.eye_open < 0.14:
            pen = QPen(QColor(198, 244, 252, 220), 3.4)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            painter.setPen(pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            for cx in (88.0, 136.0):
                arc = QPainterPath(QPointF(cx - 13.0, 94.0))
                arc.quadTo(QPointF(cx, 102.0), QPointF(cx + 13.0, 94.0))
                painter.drawPath(arc)
        else:
            self._draw_eye(painter, (88.0, 96.0), pose.look_x, pose.look_y, pose.eye_open)
            self._draw_eye(painter, (136.0, 96.0), pose.look_x, pose.look_y, pose.eye_open)

        # Tiny nose and mouth.
        nose = QPainterPath(QPointF(108.5, 112.0))
        nose.lineTo(QPointF(115.5, 112.0))
        nose.lineTo(QPointF(112.0, 116.0))
        nose.closeSubpath()
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor("#f0a3b8"))
        painter.drawPath(nose)

        mouth_open = max(0.0, min(1.0, pose.mouth))
        if mouth_open > 0.16:
            painter.setBrush(QColor("#40131d"))
            painter.drawEllipse(
                QPointF(112.0, 125.0),
                7.5,
                1.8 + 6.5 * mouth_open,
            )
            painter.setBrush(QColor("#f0788f"))
            painter.drawEllipse(
                QPointF(112.0, 127.0 + 2.4 * mouth_open),
                4.6,
                1.5 + 2.6 * mouth_open,
            )

        pen = QPen(QColor("#0a0b10"), 2.2)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        if self._state == "error":
            frown = QPainterPath(QPointF(102.0, 128.0))
            frown.quadTo(QPointF(112.0, 121.0), QPointF(122.0, 128.0))
            painter.drawPath(frown)
        else:
            smile = QPainterPath(QPointF(102.0, 121.0))
            smile.quadTo(QPointF(112.0, 129.0 + 4.0 * mouth_open), QPointF(122.0, 121.0))
            painter.drawPath(smile)

    def _draw_goggles(self, painter: QPainter) -> None:
        painter.save()
        painter.translate(118.0, 54.0)
        painter.rotate(-7.0)
        painter.translate(-118.0, -54.0)

        strap = QPainterPath(QPointF(50.0, 76.0))
        strap.quadTo(QPointF(112.0, 38.0), QPointF(168.0, 73.0))
        pen = QPen(QColor("#5a3f2a"), 9.0)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawPath(strap)
        pen = QPen(QColor("#7d5a3d"), 3.6)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(pen)
        painter.drawPath(strap)

        # Orange strap keepers.
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor("#ff8a2b"))
        painter.drawRoundedRect(QRectF(52.0, 62.0, 11.0, 10.0), 2.5, 2.5)
        painter.setBrush(QColor("#d96b12"))
        painter.drawRoundedRect(QRectF(157.0, 55.0, 9.0, 9.0), 2.0, 2.0)

        for cx, cy in ((90.0, 50.0), (141.0, 46.0)):
            painter.setPen(QPen(QColor("#3b2a1c"), 2.0))
            painter.setBrush(QColor("#6b4a2f"))
            painter.drawEllipse(QPointF(cx, cy), 17.0, 16.0)
            painter.setBrush(QColor("#2a1f16"))
            painter.drawEllipse(QPointF(cx, cy), 14.0, 13.0)

            glass = QRadialGradient(QPointF(cx - 5.0, cy - 5.0), 17.0)
            glass.setColorAt(0.0, QColor("#dcfbff"))
            glass.setColorAt(0.35, QColor("#72e7f8"))
            glass.setColorAt(0.78, QColor("#1095b5"))
            glass.setColorAt(1.0, QColor("#0a5c74"))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QBrush(glass))
            painter.drawEllipse(QPointF(cx, cy), 12.2, 11.2)

            painter.setPen(QPen(QColor(255, 255, 255, 140), 1.8))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawArc(
                QRectF(cx - 9.0, cy - 9.0, 18.0, 18.0),
                40 * 16,
                70 * 16,
            )

        pen = QPen(QColor("#6b4a2f"), 5.0)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(pen)
        painter.drawLine(QPointF(108.0, 48.0), QPointF(123.0, 46.5))
        painter.restore()

    def _draw_antenna(self, painter: QPainter, pose: _Pose) -> None:
        sway = pose.antenna
        tip = QPointF(100.0 + sway * 1.1, 15.0 - abs(sway) * 0.35)
        antenna = QPainterPath(QPointF(124.0, 44.0))
        antenna.cubicTo(
            QPointF(141.0, 16.0),
            QPointF(112.0, 1.0),
            tip,
        )

        pen = QPen(QColor("#111218"), 5.4)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawPath(antenna)

        pen = QPen(QColor("#2f3240"), 2.2)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(pen)
        painter.drawPath(antenna)

        for radius, alpha in ((16.0, 34), (11.0, 70), (7.0, 130)):
            glow = QRadialGradient(tip, radius)
            glow.setColorAt(0.0, QColor(90, 240, 255, alpha))
            glow.setColorAt(1.0, QColor(90, 240, 255, 0))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QBrush(glow))
            painter.drawEllipse(tip, radius, radius)

        painter.setBrush(QColor("#c9fdff"))
        painter.drawEllipse(tip, 4.2, 4.2)

    # ------------------------------------------------------------------ Effects

    def _draw_sparkles(self, painter: QPainter) -> None:
        painter.save()
        painter.setPen(Qt.PenStyle.NoPen)
        for index in range(6):
            angle = self._time * 1.6 + index * math.tau / 6.0
            radius = 88.0 + math.sin(self._time * 3.0 + index) * 6.0
            x = 112.0 + math.cos(angle) * radius
            y = 92.0 + math.sin(angle) * radius * 0.72
            pulse = 0.55 + 0.45 * math.sin(self._time * 7.0 + index * 1.7)
            size = 4.0 + 3.2 * pulse
            painter.setBrush(QColor(255, 228, 150, int(130 + 110 * pulse)))
            star = QPainterPath()
            star.moveTo(x, y - size)
            star.quadTo(x, y, x + size, y)
            star.quadTo(x, y, x, y + size)
            star.quadTo(x, y, x - size, y)
            star.quadTo(x, y, x, y - size)
            painter.drawPath(star)
        painter.restore()

    def _draw_thoughts(self, painter: QPainter) -> None:
        painter.save()
        painter.setPen(Qt.PenStyle.NoPen)
        for index in range(3):
            phase = (self._time * 1.4 + index * 0.35) % 1.0
            x = 166.0 + index * 11.0
            y = 36.0 - phase * 8.0
            alpha = int(150 * math.sin(math.pi * phase))
            painter.setBrush(QColor(214, 236, 246, max(0, alpha)))
            painter.drawEllipse(QPointF(x, y), 3.0 + index * 0.7, 3.0 + index * 0.7)
        painter.restore()

    def _draw_sleep(self, painter: QPainter) -> None:
        painter.save()
        font = QFont("Segoe UI", 13)
        font.setWeight(QFont.Weight.Bold)
        painter.setFont(font)
        for index in range(3):
            phase = (self._time * 0.45 + index * 0.33) % 1.0
            x = 170.0 + index * 7.0 + phase * 16.0
            y = 60.0 - phase * 34.0
            painter.setPen(QColor(190, 230, 245, int(180 * math.sin(math.pi * phase))))
            painter.drawText(QPointF(x, y), "z")
        painter.restore()

    # ------------------------------------------------------------------ Mouse

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self._press_global = event.globalPosition().toPoint()
            self._window_origin = self.window().pos()
            self._dragging = False
            event.accept()

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        if self._press_global is None or self._window_origin is None:
            return
        if not (event.buttons() & Qt.MouseButton.LeftButton):
            return
        delta = event.globalPosition().toPoint() - self._press_global
        if not self._dragging and delta.manhattanLength() > 4:
            self._dragging = True
        if self._dragging:
            self.window().move(self._window_origin + delta)
            event.accept()

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if event.button() != Qt.MouseButton.LeftButton:
            return
        was_dragging = self._dragging
        self._press_global = None
        self._window_origin = None
        self._dragging = False
        if was_dragging:
            saver = getattr(self.window(), "save_position", None)
            if callable(saver):
                saver()
        else:
            self.activated.emit()
        event.accept()

    def mouseDoubleClickEvent(self, event) -> None:  # noqa: N802
        self.activated.emit()
        event.accept()

    def contextMenuEvent(self, event) -> None:  # noqa: N802
        menu = QMenu(self)
        menu.setStyleSheet(
            """
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
            """
        )
        talk = menu.addAction("🎙️ Talk to Scout")
        talk.triggered.connect(self.activated.emit)
        stop = menu.addAction("■ Stop Scout")
        stop.triggered.connect(self.stop_requested.emit)
        hide = menu.addAction("🙈 Hide Scout")
        hide.triggered.connect(lambda: self.window().hide())
        menu.addSeparator()
        quit_action = menu.addAction("✕ Quit Scout")
        quit_action.triggered.connect(self.quit_requested.emit)
        menu.exec(event.globalPos())


class ScoutCompanionWindow(QWidget):
    """Transparent, always-on-top window that hosts Scout on the desktop."""

    summon_requested = pyqtSignal()
    quit_requested = pyqtSignal()
    stop_requested = pyqtSignal()

    def __init__(self, data_dir: Path | None = None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
            | Qt.WindowType.WindowDoesNotAcceptFocus
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setWindowTitle("Scout")
        self.setFixedSize(WIDGET_W, WIDGET_H)

        self.character = ScoutCharacterWidget(self)
        self.character.move(0, 0)
        self.character.activated.connect(self.summon_requested.emit)
        self.character.quit_requested.connect(self.quit_requested.emit)
        self.character.stop_requested.connect(self.stop_requested.emit)

        self._data_dir = data_dir
        self._place()

    def set_state(self, state: str, detail: str = "") -> None:
        self.character.set_state(state, detail)

    def _position_file(self) -> Path | None:
        if self._data_dir is None:
            return None
        return self._data_dir / "companion_position.json"

    def _place(self) -> None:
        screen = QGuiApplication.primaryScreen()
        if screen is None:
            self.move(80, 80)
            return
        area = screen.availableGeometry()
        x = area.right() - self.width() - 16
        y = area.bottom() - self.height() - 4

        path = self._position_file()
        if path is not None:
            with contextlib.suppress(OSError, ValueError, TypeError):
                saved = json.loads(path.read_text(encoding="utf-8"))
                sx, sy = int(saved["x"]), int(saved["y"])
                if area.contains(sx + 40, sy + 40) and area.contains(
                    sx + self.width() - 40, sy + self.height() - 40
                ):
                    x, y = sx, sy
        self.move(x, y)

    def save_position(self) -> None:
        path = self._position_file()
        if path is None:
            return
        with contextlib.suppress(OSError):
            path.write_text(
                json.dumps({"x": self.x(), "y": self.y()}),
                encoding="utf-8",
            )

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        self.character.raise_()
