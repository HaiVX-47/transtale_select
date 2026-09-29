from __future__ import annotations

from math import cos, pi, sin
from typing import Final

from PyQt6.QtCore import QPoint, QRectF, QSize, Qt, QTimer
from PyQt6.QtGui import QColor, QFont, QPainter, QPen
from PyQt6.QtWidgets import QApplication, QSizePolicy, QWidget


# ============================================================================
# CONFIGURATION
# ============================================================================

# The indicator is intentionally small. It should communicate state without
# competing visually with Dashboard / ResultView.
INDICATOR_WIDTH: Final[int] = 132
INDICATOR_HEIGHT: Final[int] = 36

SPINNER_SIZE: Final[int] = 18
SPINNER_STROKE: Final[float] = 2.2

ANIMATION_INTERVAL_MS: Final[int] = 45
ANIMATION_STEP_DEGREES: Final[float] = 18.0

# Horizontal padding around the whole capsule.
HORIZONTAL_PADDING: Final[int] = 10

# Gap between spinner and text.
CONTENT_GAP: Final[int] = 7

# Text displayed by the indicator.
DEFAULT_TEXT: Final[str] = "running…"


# ============================================================================
# SELECTION INDICATOR
# ============================================================================


class SelectionIndicator(QWidget):
    """
    Small, independent floating processing indicator.

    Responsibility
    --------------
    This widget owns only its visual presentation:

        spinner animation + "running…" label.

    It does NOT know anything about:

        - Selection
        - InteractionProcessor
        - Dictionary
        - LLM
        - Dashboard
        - ResultView
        - application controllers
        - when it should appear
        - where it should appear

    The controller decides:

        indicator.move(...)
        indicator.show_indicator()
        indicator.hide_indicator()
        indicator.start()
        indicator.stop()

    Design
    ------
    - Frameless floating widget.
    - Always-on-top.
    - No keyboard focus.
    - Transparent to mouse / keyboard input.
    - Rounded translucent capsule.
    - Custom QPainter spinner; no GIF/resource dependency.
    - Smooth 360-degree looping animation.
    """

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        text: str = DEFAULT_TEXT,
    ) -> None:
        super().__init__(parent)

        self._angle: float = 0.0
        self._text: str = text.strip() or DEFAULT_TEXT

        self._timer = QTimer(self)
        self._timer.setInterval(ANIMATION_INTERVAL_MS)
        self._timer.timeout.connect(self._advance_animation)

        self._build_window()
        self._build_metrics()

    # ========================================================================
    # WINDOW SETUP
    # ========================================================================

    def _build_window(self) -> None:
        """
        Configure the indicator as a small non-interactive floating window.
        """

        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )

        self.setAttribute(
            Qt.WidgetAttribute.WA_TranslucentBackground,
            True,
        )

        # The indicator must never steal keyboard focus.
        self.setFocusPolicy(
            Qt.FocusPolicy.NoFocus,
        )

        # Critical:
        # the widget is visual-only. Mouse/keyboard events must continue to
        # reach the application underneath it.
        self.setAttribute(
            Qt.WidgetAttribute.WA_TransparentForMouseEvents,
            True,
        )

        self.setSizePolicy(
            QSizePolicy.Policy.Fixed,
            QSizePolicy.Policy.Fixed,
        )

        self.setFixedSize(
            INDICATOR_WIDTH,
            INDICATOR_HEIGHT,
        )

        self.setMouseTracking(False)

    def _build_metrics(self) -> None:
        """Precompute the visual geometry used by the painter."""

        self._spinner_rect = QRectF(
            float(HORIZONTAL_PADDING),
            (INDICATOR_HEIGHT - SPINNER_SIZE) / 2.0,
            float(SPINNER_SIZE),
            float(SPINNER_SIZE),
        )

        text_x = (
            HORIZONTAL_PADDING
            + SPINNER_SIZE
            + CONTENT_GAP
        )

        self._text_rect = QRectF(
            float(text_x),
            0.0,
            float(
                INDICATOR_WIDTH
                - text_x
                - HORIZONTAL_PADDING
            ),
            float(INDICATOR_HEIGHT),
        )

    # ========================================================================
    # PUBLIC API — VISIBILITY
    # ========================================================================

    def show_indicator(self) -> None:
        """
        Show the indicator.

        Visibility is controlled exclusively by the caller.
        No timing policy is implemented here.
        """

        self.show()
        self.raise_()

    def hide_indicator(self) -> None:
        """Hide the indicator without changing animation state."""

        self.hide()

    # ========================================================================
    # PUBLIC API — ANIMATION
    # ========================================================================

    def start(self) -> None:
        """
        Start / resume the spinner animation.

        This method does not decide when the indicator should be shown.
        """

        if not self._timer.isActive():
            self._timer.start()

        self.update()

    def stop(self) -> None:
        """
        Stop the spinner animation.

        The current angle is preserved so that restarting does not require
        rebuilding the widget.
        """

        self._timer.stop()
        self.update()

    @property
    def is_animating(self) -> bool:
        """Whether the spinner timer is currently active."""

        return self._timer.isActive()

    # ========================================================================
    # PUBLIC API — TEXT
    # ========================================================================

    def set_text(self, text: str) -> None:
        """
        Change the displayed status text.

        The indicator itself does not interpret the meaning of the text.
        """

        cleaned = text.strip()

        if not cleaned:
            cleaned = DEFAULT_TEXT

        if cleaned == self._text:
            return

        self._text = cleaned
        self.update()

    @property
    def text(self) -> str:
        """Current status text."""

        return self._text

    # ========================================================================
    # PUBLIC API — POSITION
    # ========================================================================

    def move_to(self, position: QPoint) -> None:
        """
        Move the indicator to a global desktop position.

        The caller owns all placement policy.
        """

        self.move(position)

    # ========================================================================
    # INTERNAL — ANIMATION
    # ========================================================================

    def _advance_animation(self) -> None:
        self._angle = (
            self._angle + ANIMATION_STEP_DEGREES
        ) % 360.0

        self.update()

    # ========================================================================
    # PAINTING
    # ========================================================================

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)

        painter.setRenderHint(
            QPainter.RenderHint.Antialiasing,
            True,
        )

        # ------------------------------------------------------------
        # Capsule
        # ------------------------------------------------------------

        capsule = QRectF(
            0.5,
            0.5,
            self.width() - 1.0,
            self.height() - 1.0,
        )

        # Soft, neutral translucent background. The widget remains subtle
        # over both light and dark desktop content.
        painter.setBrush(
            QColor(24, 24, 27, 218)
        )

        painter.setPen(
            QPen(
                QColor(255, 255, 255, 38),
                1.0,
            )
        )

        painter.drawRoundedRect(
            capsule,
            18.0,
            18.0,
        )

        # ------------------------------------------------------------
        # Spinner
        # ------------------------------------------------------------

        self._paint_spinner(
            painter,
            self._spinner_rect,
        )

        # ------------------------------------------------------------
        # Text
        # ------------------------------------------------------------

        font = QFont(
            self.font(),
        )

        font.setPixelSize(13)
        font.setWeight(QFont.Weight.Medium)

        painter.setFont(font)
        painter.setPen(
            QColor(255, 255, 255, 205)
        )

        painter.drawText(
            self._text_rect,
            int(
                Qt.AlignmentFlag.AlignVCenter
                | Qt.AlignmentFlag.AlignLeft
            ),
            self._text,
        )

        painter.end()

    def _paint_spinner(
        self,
        painter: QPainter,
        rect: QRectF,
    ) -> None:
        """
        Draw a polished arc spinner.

        The arc has a slightly heavier leading end and a softer trailing
        appearance so the motion is readable without being visually loud.
        """

        center = rect.center()

        # Base ring: extremely subtle, only visible enough to make the shape
        # readable when the animated arc is between positions.
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(
            QPen(
                QColor(255, 255, 255, 38),
                SPINNER_STROKE,
                Qt.PenStyle.SolidLine,
                Qt.PenCapStyle.RoundCap,
            )
        )

        painter.drawEllipse(
            rect.adjusted(
                SPINNER_STROKE / 2.0,
                SPINNER_STROKE / 2.0,
                -SPINNER_STROKE / 2.0,
                -SPINNER_STROKE / 2.0,
            )
        )

        # Draw several arcs with decreasing opacity to create a lightweight
        # "comet-tail" effect without an image asset.
        tail_count = 5
        total_span = 105.0

        for index in range(tail_count):
            progress = index / max(1, tail_count - 1)

            span = total_span * (
                1.0 - 0.62 * progress
            )

            alpha = int(
                230.0
                * (1.0 - 0.70 * progress)
            )

            pen = QPen(
                QColor(255, 255, 255, alpha),
                SPINNER_STROKE,
                Qt.PenStyle.SolidLine,
                Qt.PenCapStyle.RoundCap,
            )

            painter.setPen(pen)

            start_angle = (
                self._angle
                + 25.0
                + index * 8.0
            )

            painter.drawArc(
                QRectF(
                    center.x() - rect.width() / 2.0 + 1.0,
                    center.y() - rect.height() / 2.0 + 1.0,
                    rect.width() - 2.0,
                    rect.height() - 2.0,
                ),
                int(-start_angle * 16.0),
                int(-span * 16.0),
            )

        # Small bright leading point gives the rotation direction a clear
        # visual anchor.
        painter.setPen(
            QPen(
                QColor(255, 255, 255, 240),
                SPINNER_STROKE + 0.2,
                Qt.PenStyle.SolidLine,
                Qt.PenCapStyle.RoundCap,
            )
        )

        leading_start = (
            -self._angle
            * pi
            / 180.0
        )

        # Keep the point aligned with the leading edge of the animated arc.
        point_angle = leading_start - 25.0 * pi / 180.0

        point_radius = (
            rect.width() / 2.0
            - SPINNER_STROKE / 2.0
            - 0.5
        )

        point = QPoint(
            int(
                center.x()
                + point_radius
                * cos(point_angle)
            ),
            int(
                center.y()
                + point_radius
                * sin(point_angle)
            ),
        )

        painter.drawPoint(point)

    # ========================================================================
    # SIZE HINT
    # ========================================================================

    def sizeHint(self) -> QSize:
        return QSize(
            INDICATOR_WIDTH,
            INDICATOR_HEIGHT,
        )


__all__ = [
    "SelectionIndicator",
]


# ============================================================================
# OPTIONAL VISUAL SELF-TEST
# ============================================================================
#
# This section is intentionally independent from the application. Running:
#
#     python -m frontend.ui.views.selection_indicator
#
# opens the indicator by itself so the visual design can be inspected.
#
# The real application does not execute this block when the module is
# imported.
#

if __name__ == "__main__":
    import os
    import sys

    # This self-test does not alter the application's platform choice.
    # The main application is responsible for selecting XCB/XWayland when
    # required by the desktop environment.

    app = QApplication.instance()

    owns_app = app is None

    if app is None:
        app = QApplication(sys.argv)

    indicator = SelectionIndicator()
    indicator.move_to(QPoint(700, 300))
    indicator.show_indicator()
    indicator.start()

    if owns_app:
        sys.exit(app.exec())
