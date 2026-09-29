"""
frontend.ui.control_panel
=========================

Lightweight floating control panel for Translate Select.

Design goals
------------
- visually bounded, opaque frame with a clear accent color;
- real top-level window that can be moved by dragging its header;
- minimize to the desktop taskbar while runtime is active;
- never owns Selection/backend/application logic;
- emits only user-intent signals.
"""

from __future__ import annotations

import os
import sys

if (
    sys.platform.startswith("linux")
    and os.environ.get("XDG_SESSION_TYPE", "").lower() == "wayland"
    and os.environ.get("DISPLAY")
):
    os.environ["QT_QPA_PLATFORM"] = "xcb"

from PyQt6.QtCore import QEvent, QPoint, Qt, pyqtSignal
from PyQt6.QtGui import QMouseEvent
from PyQt6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from .ui.components import (
    DictionaryButton,
    LLMButton,
    StartButton,
    StopButton,
)


class ControlPanel(QWidget):
    """
    Persistent application control surface.

    Window behavior
    ---------------
    NORMAL:
        The complete panel is visible and can be dragged by its header.

    RUNNING:
        The panel is changed to Stop mode and minimized to the taskbar.
        It is not destroyed and its position is preserved.

    STOPPED:
        The panel is restored to Normal mode with LLM selected and Start
        visible.

    The header is the only drag surface. The close/minimize buttons remain
    ordinary clickable controls.
    """

    llm_requested = pyqtSignal()
    dictionary_requested = pyqtSignal()
    start_requested = pyqtSignal()
    stop_requested = pyqtSignal()
    close_requested = pyqtSignal()

    def __init__(
        self,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)

        self._dragging = False
        self._drag_offset = QPoint()

        self._build_window()
        self._build_ui()
        self._connect_signals()
        self.reset()

    # ======================================================================
    # WINDOW
    # ======================================================================

    def _build_window(self) -> None:
        # Use Window rather than Tool so that minimizing this panel has a
        # normal taskbar/dock representation on desktop environments.
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Window
        )

        self.setAttribute(
            Qt.WidgetAttribute.WA_TranslucentBackground,
            False,
        )

        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setMinimumWidth(390)
        self.setMaximumWidth(460)
        self.setObjectName("TranslateSelectControlPanel")
        self.setWindowTitle("Translate Select")

        self.setStyleSheet(
            """
            QWidget#TranslateSelectControlPanel {
                background-color: #111827;
                border: 2px solid #3B82F6;
                border-radius: 16px;
            }

            QFrame#controlHeader {
                background-color: #1E293B;
                border: 1px solid #334155;
                border-radius: 11px;
            }

            QFrame#headerAccent {
                background-color: #60A5FA;
                border: none;
                border-radius: 3px;
            }

            QLabel#titleLabel {
                color: #F8FAFC;
                font-size: 16px;
                font-weight: 700;
            }

            QLabel#subtitleLabel {
                color: #94A3B8;
                font-size: 10px;
            }

            QToolButton#windowButton {
                color: #CBD5E1;
                background: transparent;
                border: none;
                border-radius: 7px;
                font-size: 16px;
                font-weight: 700;
            }

            QToolButton#windowButton:hover {
                color: #FFFFFF;
                background-color: #334155;
            }

            QToolButton#closeButton:hover {
                background-color: #DC2626;
            }

            QToolButton#closeButton:pressed {
                background-color: #B91C1C;
            }

            QFrame#modeFrame {
                background-color: #172033;
                border: 1px solid #334155;
                border-radius: 11px;
            }

            QFrame#actionFrame {
                background-color: #172033;
                border: 1px solid #334155;
                border-radius: 11px;
            }
            """
        )

    # ======================================================================
    # UI
    # ======================================================================

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(11, 11, 11, 11)
        root.setSpacing(9)

        # ------------------------------------------------------------------
        # Header / drag surface
        # ------------------------------------------------------------------
        self._drag_handle = QFrame(self)
        self._drag_handle.setObjectName("controlHeader")
        self._drag_handle.setCursor(Qt.CursorShape.OpenHandCursor)
        self._drag_handle.installEventFilter(self)
        self._drag_handle.setMinimumHeight(58)

        header_layout = QHBoxLayout(self._drag_handle)
        header_layout.setContentsMargins(10, 8, 8, 8)
        header_layout.setSpacing(9)

        accent = QFrame(self._drag_handle)
        accent.setObjectName("headerAccent")
        accent.setFixedSize(4, 34)
        accent.setAttribute(
            Qt.WidgetAttribute.WA_TransparentForMouseEvents,
            True,
        )
        header_layout.addWidget(accent, 0, Qt.AlignmentFlag.AlignVCenter)

        title_column = QVBoxLayout()
        title_column.setContentsMargins(0, 0, 0, 0)
        title_column.setSpacing(1)

        self.title_label = QLabel("Translate Select", self._drag_handle)
        self.title_label.setObjectName("titleLabel")
        self.title_label.setAttribute(
            Qt.WidgetAttribute.WA_TransparentForMouseEvents,
            True,
        )

        self.subtitle_label = QLabel(
            "Choose mode  •  drag this header to move",
            self._drag_handle,
        )
        self.subtitle_label.setObjectName("subtitleLabel")
        self.subtitle_label.setAttribute(
            Qt.WidgetAttribute.WA_TransparentForMouseEvents,
            True,
        )

        title_column.addWidget(self.title_label)
        title_column.addWidget(self.subtitle_label)
        header_layout.addLayout(title_column, 1)

        self.minimize_button = self._create_window_button(
            "—",
            "Minimize to taskbar",
        )
        self.minimize_button.setObjectName("windowButton")
        header_layout.addWidget(self.minimize_button, 0)

        self.close_button = self._create_window_button(
            "×",
            "Exit application",
        )
        self.close_button.setObjectName("closeButton")
        header_layout.addWidget(self.close_button, 0)

        root.addWidget(self._drag_handle)

        # ------------------------------------------------------------------
        # Mode frame
        # ------------------------------------------------------------------
        mode_frame = QFrame(self)
        mode_frame.setObjectName("modeFrame")
        mode_layout = QHBoxLayout(mode_frame)
        mode_layout.setContentsMargins(7, 7, 7, 7)
        mode_layout.setSpacing(7)

        self.llm_button = LLMButton(mode_frame)
        self.dictionary_button = DictionaryButton(mode_frame)

        mode_layout.addWidget(self.llm_button, 1)
        mode_layout.addWidget(self.dictionary_button, 1)
        root.addWidget(mode_frame)

        # ------------------------------------------------------------------
        # Action frame
        # ------------------------------------------------------------------
        action_frame = QFrame(self)
        action_frame.setObjectName("actionFrame")
        action_layout = QHBoxLayout(action_frame)
        action_layout.setContentsMargins(7, 7, 7, 7)
        action_layout.setSpacing(7)

        self.start_button = StartButton(action_frame)
        self.stop_button = StopButton(action_frame)

        action_layout.addWidget(self.start_button, 1)
        action_layout.addWidget(self.stop_button, 1)
        root.addWidget(action_frame)

        self.adjustSize()

    @staticmethod
    def _create_window_button(text: str, tooltip: str) -> QToolButton:
        button = QToolButton()
        button.setText(text)
        button.setToolTip(tooltip)
        button.setFixedSize(30, 30)
        button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        button.setObjectName("windowButton")
        return button

    # ======================================================================
    # DRAGGING
    # ======================================================================

    def eventFilter(self, watched: object, event: object) -> bool:
        if watched is not self._drag_handle:
            return super().eventFilter(watched, event)  # type: ignore[arg-type]

        if not isinstance(event, QMouseEvent):
            return super().eventFilter(watched, event)  # type: ignore[arg-type]

        if event.type() == QEvent.Type.MouseButtonPress:
            if event.button() == Qt.MouseButton.LeftButton:
                self._dragging = True
                self._drag_offset = (
                    event.globalPosition().toPoint()
                    - self.frameGeometry().topLeft()
                )
                self._drag_handle.setCursor(Qt.CursorShape.ClosedHandCursor)
                return True

        elif event.type() == QEvent.Type.MouseMove:
            if self._dragging and event.buttons() & Qt.MouseButton.LeftButton:
                self.move(
                    event.globalPosition().toPoint() - self._drag_offset,
                )
                return True

        elif event.type() == QEvent.Type.MouseButtonRelease:
            if event.button() == Qt.MouseButton.LeftButton:
                self._dragging = False
                self._drag_handle.setCursor(Qt.CursorShape.OpenHandCursor)
                return True

        return super().eventFilter(watched, event)  # type: ignore[arg-type]

    # ======================================================================
    # SIGNALS
    # ======================================================================

    def _connect_signals(self) -> None:
        self.llm_button.clicked_requested.connect(self.llm_requested)
        self.dictionary_button.clicked_requested.connect(self.dictionary_requested)
        self.start_button.start_requested.connect(self.start_requested)
        self.stop_button.stop_requested.connect(self.stop_requested)
        self.minimize_button.clicked.connect(self.minimize_to_taskbar)
        self.close_button.clicked.connect(self.close_requested)

    # ======================================================================
    # MODE PRESENTATION
    # ======================================================================

    def set_mode_selection(
        self,
        *,
        llm_selected: bool,
        dictionary_selected: bool,
    ) -> None:
        if not llm_selected and not dictionary_selected:
            raise ValueError("At least one processing mode must remain selected.")

        self.llm_button.set_state(
            LLMButton.State.ACTIVE
            if llm_selected
            else LLMButton.State.INACTIVE
        )
        self.dictionary_button.set_state(
            DictionaryButton.State.ACTIVE
            if dictionary_selected
            else DictionaryButton.State.INACTIVE
        )

    # ======================================================================
    # RUNTIME PRESENTATION
    # ======================================================================

    def show_start_mode(self) -> None:
        """Show Start and remove Stop."""
        self.start_button.set_state(StartButton.State.READY)
        self.stop_button.set_state(StopButton.State.DISABLED)

        self.start_button.show()
        self.stop_button.hide()
        self.adjustSize()

    def show_stop_mode(self) -> None:
        """Show Stop and remove Start."""
        self.start_button.set_state(StartButton.State.DISABLED)
        self.stop_button.set_state(StopButton.State.READY)

        self.start_button.hide()
        self.stop_button.show()
        self.adjustSize()

    # ======================================================================
    # RESET / VISIBILITY
    # ======================================================================

    def reset(self) -> None:
        self.set_mode_selection(
            llm_selected=True,
            dictionary_selected=False,
        )
        self.show_start_mode()

    def show_panel(self) -> None:
        """Restore the panel from the taskbar and show it normally."""
        self.showNormal()
        self.show()
        self.raise_()

    def hide_panel(self) -> None:
        """Legacy hide API: completely hide the panel."""
        self.hide()

    def minimize_to_taskbar(self) -> None:
        """Temporarily remove the panel from the desktop while preserving it."""
        self.showMinimized()


__all__ = [
    "ControlPanel",
]
