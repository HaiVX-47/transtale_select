from __future__ import annotations

# ============================================================================
# IMPORTANT: LINUX WINDOW-SYSTEM REQUIREMENT
# ============================================================================
#
# This ResultView is a floating top-most Qt window. On the user's Linux
# desktop the session runs on Wayland (WAYLAND_DISPLAY=wayland-0), while
# XWayland is also available (DISPLAY=:0).
#
# During testing, the same Qt code behaved differently under the two Qt
# platform backends:
#
#     Wayland / Qt Wayland
#         -> ResultView could lose its top-most stacking when another
#            application (for example VSCode) was activated.
#
#     XWayland / Qt XCB
#         -> WindowStaysOnTopHint + raise_() behaved as required, and the
#            ResultView remained above other application windows.
#
# Therefore this project MUST be launched with:
#
#     QT_QPA_PLATFORM=xcb python -m frontend.test
#
# or with QT_QPA_PLATFORM=xcb exported before QApplication is created.
# This is a window-system/compositor constraint, NOT a sudo/permission
# problem. Do not remove this note when refactoring the ResultView.
# ============================================================================

import ctypes
import ctypes.util
import os
import re
import shutil
import sys
import time
from enum import Enum, auto
from typing import Protocol

# On the target Linux setup, the desktop session is Wayland, but this project
# deliberately runs Qt through XWayland/XCB. This is required for the floating
# ResultView to keep the same reliable always-on-top behavior as the Dashboard.
#
# IMPORTANT:
#     Wayland / Qt Wayland -> compositor controls top-level stacking, so
#                         raise_() is not sufficient for this use case.
#     XWayland / Qt XCB   -> WindowStaysOnTopHint + raise_() behaves as
#                         required, and the ResultView stays above VSCode,
#                         browsers, terminals, etc.
#
# Therefore: on Linux+Wayland, when XWayland is available (DISPLAY is set),
# force Qt to use the XCB backend BEFORE importing any PyQt6 module. This keeps
# the application from silently falling back to the Wayland backend later.
# This is a window-system/compositor requirement, NOT a sudo/permission issue.
#
# Manual launch remains valid and should be documented in project scripts as:
#     QT_QPA_PLATFORM=xcb python -m frontend.test
if (
    sys.platform.startswith("linux")
    and os.environ.get("XDG_SESSION_TYPE", "").lower() == "wayland"
    and os.environ.get("DISPLAY")
):
    os.environ["QT_QPA_PLATFORM"] = "xcb"

from PyQt6.QtCore import (
    QEvent,
    QPoint,
    QRect,
    Qt,
    QProcess,
    QProcessEnvironment,
    QTimer,
)
from PyQt6.QtGui import QCloseEvent, QCursor, QEnterEvent, QMouseEvent, QShowEvent, QGuiApplication
from PyQt6.QtWidgets import (
    QAbstractButton,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QScrollArea,
    QScrollBar,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
    QPushButton,
)


# ============================================================================
# INPUT CONTRACT
# ============================================================================


class TranslationResponseProtocol(Protocol):
    """
    Minimal contract required from the LLM backend response.

    The backend response is expected to provide:
        - text
        - translation_lines
    """

    text: str
    translation_lines: tuple[str, ...]


class Language(Enum):
    ENGLISH = auto()
    VIETNAMESE = auto()


class ViewMode(Enum):
    SINGLE = auto()
    BILINGUAL = auto()


# ============================================================================
# CONFIG
# ============================================================================


MIN_WIDTH = 300
MIN_HEIGHT = 180

DEFAULT_WIDTH = 420
DEFAULT_HEIGHT = 300

# Width of the invisible resize region around the four edges.
RESIZE_MARGIN = 9

SOURCE_FONT_SIZE = 15
TRANSLATION_FONT_SIZE = 14

# Long uninterrupted strings such as:
#
#     aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa
#
# receive invisible break opportunities.
LONG_TOKEN_THRESHOLD = 16
SOFT_BREAK_INTERVAL = 8


# ============================================================================
# RESULT VIEW
# ============================================================================


class ResultView(QWidget):
    """
    Floating translation overlay.

    Interaction behavior follows the existing Dictionary Dashboard:

    PASSIVE
        - window remains visible;
        - background is fully transparent;
        - English text is white;
        - Vietnamese text is white;
        - header is hidden;
        - scrollbar is hidden.

    INTERACTIVE
        - background becomes visible;
        - text becomes dark;
        - header appears;
        - scrollbar appears when needed.

    The transition is controlled by the pointer position. In PASSIVE mode
    the native widget is mouse-transparent so selection underneath the
    overlay remains possible. A lightweight cursor-position timer switches
    the same window into INTERACTIVE mode when the pointer enters it, while
    preserving the current mode during an active mouse-button gesture.

    The window is never hidden merely because the mouse leaves.

    Window stacking follows the Dashboard model:
        - FramelessWindowHint
        - WindowStaysOnTopHint
        - Tool
        - raise() on show / explicit interaction
        - no activateWindow()
        - no z-order changes while the pointer moves or focus changes
        - X11 EWMH ABOVE reinforcement when running on XCB
        - no application-global event filter
        - passive mode is click-through so text underneath remains selectable

    Public API:

        view = ResultView()
        view.display(response)

    No dependency on Selection, Processor, Backend, or application controllers.
    """

    def __init__(
        self,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)

        # ------------------------------------------------------------------
        # UI state
        # ------------------------------------------------------------------

        self._language = Language.ENGLISH
        self._view_mode = ViewMode.SINGLE
        self._current_response: TranslationResponseProtocol | None = None
        self._interactive = False

        # ------------------------------------------------------------------
        # Manual move / resize fallback
        # ------------------------------------------------------------------

        self._drag_mode: str | None = None
        self._resize_edges = Qt.Edge(0)
        self._last_mouse_button_seen_at = 0.0

        self._press_global_position: QPoint | None = None
        self._press_geometry: QRect | None = None

        # ------------------------------------------------------------------
        # Window configuration
        # ------------------------------------------------------------------

        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )

        self.setAttribute(
            Qt.WidgetAttribute.WA_TranslucentBackground,
            True,
        )

        # Keep keyboard focus in the application the user is actually using.
        self.setFocusPolicy(
            Qt.FocusPolicy.NoFocus
        )

        self.setMouseTracking(True)

        self.setMinimumSize(
            MIN_WIDTH,
            MIN_HEIGHT,
        )

        self.resize(
            DEFAULT_WIDTH,
            DEFAULT_HEIGHT,
        )

        self._build_ui()

        self._x11_library = None
        self._x11_display = None
        self._x11_root = 0
        self._initialize_x11_pointer_query()

        # Retain one asynchronous wmctrl process object. Stacking requests
        # must never wait on the window manager from Qt's UI thread.
        self._wmctrl_process = QProcess(self)
        self._wmctrl_process.setStandardOutputFile(os.devnull)
        self._wmctrl_process.setStandardErrorFile(os.devnull)

        # Passive mode must allow the application underneath to receive mouse
        # input. Because a mouse-transparent top-level window cannot reliably
        # receive its own Enter/Leave events, use the global cursor position
        # as the hover source while the overlay is passive.
        self._pointer_state_timer = QTimer(self)
        self._pointer_state_timer.setInterval(40)
        self._pointer_state_timer.timeout.connect(
            self._sync_pointer_interaction
        )

        # Same strategy as DictionaryDashboard:
        # only ResultView and its descendants receive the event filter.
        self._install_pointer_tracking(self)

        self._apply_passive_style()

    # ======================================================================
    # UI BUILD
    # ======================================================================

    def _build_ui(self) -> None:
        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)

        # ------------------------------------------------------------------
        # Main container
        # ------------------------------------------------------------------

        self.container = QFrame(self)
        self.container.setObjectName(
            "resultContainer"
        )
        self.container.setMouseTracking(True)
        self.container.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Expanding,
        )

        container_layout = QVBoxLayout(
            self.container
        )
        container_layout.setContentsMargins(
            6,
            6,
            6,
            6,
        )
        container_layout.setSpacing(4)

        root_layout.addWidget(
            self.container
        )

        # ------------------------------------------------------------------
        # Header
        # ------------------------------------------------------------------

        self.header = QFrame(
            self.container
        )
        self.header.setObjectName(
            "resultHeader"
        )
        self.header.setMouseTracking(True)
        self.header.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Fixed,
        )

        header_layout = QHBoxLayout(
            self.header
        )
        header_layout.setContentsMargins(
            8,
            5,
            8,
            5,
        )
        header_layout.setSpacing(4)

        self.button_language = self._create_button(
            text="EN",
            width=42,
            tooltip="Toggle English / Vietnamese",
        )

        self.button_mode = self._create_button(
            text="Single",
            width=62,
            tooltip="Toggle Single / Bilingual",
        )

        self.button_close = self._create_button(
            text="×",
            width=28,
            tooltip="Close",
        )

        # A — left.
        header_layout.addWidget(
            self.button_language
        )

        # B + X — right.
        header_layout.addStretch(1)
        header_layout.addWidget(
            self.button_mode
        )
        header_layout.addWidget(
            self.button_close
        )

        self.button_language.clicked.connect(
            self.toggle_language
        )

        self.button_mode.clicked.connect(
            self.toggle_view_mode
        )

        self.button_close.clicked.connect(
            self.close
        )

        container_layout.addWidget(
            self.header
        )

        # ------------------------------------------------------------------
        # Scroll area
        # ------------------------------------------------------------------

        self.scroll_area = QScrollArea(
            self.container
        )
        self.scroll_area.setObjectName(
            "resultScrollArea"
        )

        self.scroll_area.setWidgetResizable(
            True
        )

        self.scroll_area.setFrameShape(
            QFrame.Shape.NoFrame
        )

        self.scroll_area.setMinimumSize(
            0,
            0,
        )

        self.scroll_area.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Expanding,
        )

        # Text must wrap instead of creating horizontal scrolling.
        self.scroll_area.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )

        # Hidden in passive mode; enabled on hover.
        self.scroll_area.setVerticalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )

        self.scroll_area.setMouseTracking(True)
        self.scroll_area.viewport().setMouseTracking(True)

        # Keep ONE content QWidget for the lifetime of the ResultView.
        # Only its child rows are replaced.
        self.content = QWidget(
            self.scroll_area
        )
        self.content.setObjectName(
            "resultContent"
        )
        self.content.setMinimumSize(
            0,
            0,
        )
        self.content.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Preferred,
        )
        self.content.setMouseTracking(True)

        # One logical translation line = one QGrid row.
        #
        # In bilingual mode:
        #
        #     English | Vietnamese
        #
        # The row height becomes the larger required height of the two
        # labels. This prevents vertical drift when one side wraps more.
        self.content_grid = QGridLayout(
            self.content
        )

        self.content_grid.setContentsMargins(
            4,
            4,
            4,
            4,
        )

        self.content_grid.setHorizontalSpacing(
            8
        )

        self.content_grid.setVerticalSpacing(
            4
        )

        self.content_grid.setColumnStretch(
            0,
            1,
        )

        self.content_grid.setColumnStretch(
            1,
            1,
        )

        self.content_grid.setAlignment(
            Qt.AlignmentFlag.AlignTop
        )

        self.scroll_area.setWidget(
            self.content
        )

        container_layout.addWidget(
            self.scroll_area,
            1,
        )

    @staticmethod
    def _create_button(
        *,
        text: str,
        width: int,
        tooltip: str,
    ) -> QPushButton:
        button = QPushButton(text)

        button.setFixedSize(
            width,
            25,
        )

        button.setToolTip(
            tooltip
        )

        button.setFocusPolicy(
            Qt.FocusPolicy.NoFocus
        )

        button.setCursor(
            Qt.CursorShape.PointingHandCursor
        )

        button.setMouseTracking(True)

        return button

    # ======================================================================
    # PUBLIC API
    # ======================================================================

    def display(
        self,
        response: TranslationResponseProtocol,
    ) -> None:
        """
        Replace the current response and show the ResultView.

        One-call API:

            view.display(response)
        """
        self.set_result(response)
        self.show_result()

    def show_result(
        self,
    ) -> None:
        """Show the floating result without taking keyboard focus."""
        self.show()
        self._bring_to_front()
        self._pointer_state_timer.start()
        self._sync_pointer_interaction()

    def hide_result(
        self,
    ) -> None:
        """Hide the result without destroying or clearing its content."""
        self.hide()

    def set_result(
        self,
        response: TranslationResponseProtocol,
    ) -> None:
        """
        Validate and render a new response.

        The QScrollArea content widget itself is never replaced.
        """
        source_lines, translated_lines = (
            self._validate_response(
                response
            )
        )

        # Rendering must succeed before the response reference is committed.
        self._render_content(
            source_lines,
            translated_lines,
        )

        self._current_response = response

        self._update_button_state()

        # Every new result starts from the beginning.
        self.scroll_area.verticalScrollBar().setValue(
            0
        )

    def clear(self) -> None:
        self._clear_content()
        self._current_response = None

    def toggle_language(self) -> None:
        self._language = (
            Language.VIETNAMESE
            if self._language is Language.ENGLISH
            else Language.ENGLISH
        )

        self._refresh()

    def toggle_view_mode(self) -> None:
        self._view_mode = (
            ViewMode.BILINGUAL
            if self._view_mode is ViewMode.SINGLE
            else ViewMode.SINGLE
        )

        self._refresh()

    # ======================================================================
    # RESPONSE VALIDATION
    # ======================================================================

    def _validate_response(
        self,
        response: TranslationResponseProtocol,
    ) -> tuple[
        tuple[str, ...],
        tuple[str, ...],
    ]:
        if response is None:
            raise ValueError(
                "ResultView requires a translation response."
            )

        if not hasattr(
            response,
            "text",
        ):
            raise ValueError(
                "Translation response is missing 'text'."
            )

        if not hasattr(
            response,
            "translation_lines",
        ):
            raise ValueError(
                "Translation response is missing "
                "'translation_lines'."
            )

        text = getattr(
            response,
            "text",
        )

        translation_lines = getattr(
            response,
            "translation_lines",
        )

        if not isinstance(
            text,
            str,
        ):
            raise ValueError(
                "Translation response 'text' must be str."
            )

        try:
            translated_lines = tuple(
                translation_lines
            )
        except TypeError as exc:
            raise ValueError(
                "'translation_lines' must be iterable."
            ) from exc

        if not all(
            isinstance(
                line,
                str,
            )
            for line in translated_lines
        ):
            raise ValueError(
                "Every translation line must be str."
            )

        normalized_text = (
            text.replace(
                "\r\n",
                "\n",
            )
            .replace(
                "\r",
                "\n",
            )
        )

        source_lines = tuple(
            normalized_text.split(
                "\n"
            )
        )

        if len(source_lines) != len(
            translated_lines
        ):
            raise ValueError(
                "Line count mismatch: "
                f"{len(source_lines)} source lines vs "
                f"{len(translated_lines)} translated lines."
            )

        return (
            source_lines,
            translated_lines,
        )

    # ======================================================================
    # CONTENT
    # ======================================================================

    def _clear_content(self) -> None:
        while self.content_grid.count():
            item = self.content_grid.takeAt(0)

            widget = item.widget()

            if widget is not None:
                widget.deleteLater()

    def _render_content(
        self,
        source_lines: tuple[str, ...],
        translated_lines: tuple[str, ...],
    ) -> None:
        self._clear_content()

        for index, (
            source_line,
            translated_line,
        ) in enumerate(
            zip(
                source_lines,
                translated_lines,
            )
        ):
            if self._view_mode is ViewMode.SINGLE:
                text = (
                    source_line
                    if self._language is Language.ENGLISH
                    else translated_line
                )

                label = self._create_label(
                    text,
                    self._language,
                )

                self.content_grid.addWidget(
                    label,
                    index,
                    0,
                    1,
                    2,
                )

                continue

            source_label = self._create_label(
                source_line,
                Language.ENGLISH,
            )

            translated_label = self._create_label(
                translated_line,
                Language.VIETNAMESE,
            )

            # ONE logical row for the two languages.
            self.content_grid.addWidget(
                source_label,
                index,
                0,
            )

            self.content_grid.addWidget(
                translated_label,
                index,
                1,
            )

        self.content_grid.invalidate()
        self.content_grid.activate()

        self.content.adjustSize()

        # Dynamically-created descendants need the same pointer tracking.
        self._install_pointer_tracking(
            self.content
        )

    @staticmethod
    def _create_label(
        text: str,
        language: Language,
    ) -> QLabel:
        label = QLabel(
            ResultView._prepare_text(
                text
            )
        )

        label.setMinimumWidth(
            0
        )

        label.setWordWrap(
            True
        )

        # IMPORTANT:
        #
        # Align TOP means that if Vietnamese wraps to 3 visual lines while
        # English wraps to 1, both start at the same logical row origin.
        label.setAlignment(
            Qt.AlignmentFlag.AlignLeft
            | Qt.AlignmentFlag.AlignTop
        )

        # Ignored horizontal policy gives QLabel permission to wrap instead
        # of forcing the outer window to remain wide.
        label.setSizePolicy(
            QSizePolicy.Policy.Ignored,
            QSizePolicy.Policy.Preferred,
        )

        label.setTextInteractionFlags(
            Qt.TextInteractionFlag.NoTextInteraction
        )

        label.setMouseTracking(True)

        label.setProperty(
            "language",
            (
                "english"
                if language is Language.ENGLISH
                else "vietnamese"
            ),
        )

        return label

    @staticmethod
    def _prepare_text(
        text: str,
    ) -> str:
        """
        Insert invisible break opportunities into long uninterrupted tokens.

        Example:

            aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa

        becomes visually breakable without changing the visible text.
        """
        pattern = re.compile(
            rf"\S{{{LONG_TOKEN_THRESHOLD + 1},}}"
        )

        def replace_token(
            match: re.Match[str],
        ) -> str:
            token = match.group(0)

            chunks = [
                token[
                    start : start + SOFT_BREAK_INTERVAL
                ]
                for start in range(
                    0,
                    len(token),
                    SOFT_BREAK_INTERVAL,
                )
            ]

            return "\u200b".join(
                chunks
            )

        return pattern.sub(
            replace_token,
            text,
        )

    def _refresh(self) -> None:
        if self._current_response is None:
            self._clear_content()
            self._update_button_state()
            return

        source_lines, translated_lines = (
            self._validate_response(
                self._current_response
            )
        )

        self._render_content(
            source_lines,
            translated_lines,
        )

        self._update_button_state()

    # ======================================================================
    # BUTTON STATE
    # ======================================================================

    def _update_button_state(self) -> None:
        self.button_language.setText(
            "EN"
            if self._language is Language.ENGLISH
            else "VI"
        )

        self.button_mode.setText(
            "Single"
            if self._view_mode is ViewMode.SINGLE
            else "Split"
        )

    # ======================================================================
    # PASSIVE / INTERACTIVE STYLE
    # ======================================================================

    def _apply_passive_style(self) -> None:
        """
        Same visual behavior as DictionaryDashboard PASSIVE mode.

        The ResultView itself is NOT hidden.
        """
        self.header.hide()

        self.scroll_area.setVerticalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )

        self.setStyleSheet(
            """
            QFrame#resultContainer {
                background: transparent;
                border: none;
                border-radius: 8px;
            }

            QFrame#resultHeader {
                background: transparent;
                border: none;
            }

            QWidget#resultContent {
                background: transparent;
            }

            QFrame[objectName^="translationRow_"] {
                background: transparent;
                border: none;
            }

            QLabel[language="english"] {
                color: white;
                font-weight: 700;
                font-size: 15px;
                background: transparent;
            }

            QLabel[language="vietnamese"] {
                color: white;
                font-size: 14px;
                background: transparent;
            }

            QScrollArea#resultScrollArea {
                background: transparent;
                border: none;
            }
            """
        )

    def _apply_interactive_style(self) -> None:
        """
        Same visual language as DictionaryDashboard INTERACTIVE mode.
        """
        self.header.show()

        self.scroll_area.setVerticalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAsNeeded
        )

        self.setStyleSheet(
            """
            QFrame#resultContainer {
                background-color: rgba(
                    250,
                    250,
                    250,
                    235
                );
                border: 1px solid #b8b8b8;
                border-radius: 8px;
            }

            QFrame#resultHeader {
                background-color: rgba(
                    235,
                    235,
                    235,
                    240
                );
                border-radius: 5px;
            }

            QFrame#resultHeader QPushButton {
                background-color: rgba(
                    250,
                    250,
                    250,
                    230
                );
                color: #222222;
                border: 1px solid #d0d0d0;
                border-radius: 5px;
                font-size: 12px;
                font-weight: 600;
            }

            QFrame#resultHeader QPushButton:hover {
                background-color: #ffffff;
                border-color: #b0b0b0;
            }

            QFrame#resultHeader QPushButton:pressed {
                background-color: #e7e7e7;
            }

            QWidget#resultContent {
                background: transparent;
            }

            QFrame[objectName^="translationRow_"] {
                background-color: rgba(
                    255,
                    255,
                    255,
                    180
                );
                border-bottom: 1px solid #dddddd;
            }

            QLabel[language="english"] {
                color: #111111;
                font-weight: 700;
                font-size: 15px;
                background: transparent;
            }

            QLabel[language="vietnamese"] {
                color: #333333;
                font-size: 14px;
                background: transparent;
            }

            QScrollArea#resultScrollArea {
                background: transparent;
                border: none;
            }

            QScrollBar:vertical {
                width: 10px;
                background: transparent;
            }

            QScrollBar::handle:vertical {
                background: #a0a0a0;
                border-radius: 5px;
                min-height: 24px;
            }

            QScrollBar::handle:vertical:hover {
                background: #888888;
            }

            QScrollBar::add-line:vertical,
            QScrollBar::sub-line:vertical {
                height: 0px;
            }

            QScrollBar::add-page:vertical,
            QScrollBar::sub-page:vertical {
                background: transparent;
            }
            """
        )

    # ======================================================================
    # VISIBILITY / HOVER
    # ======================================================================

    def _set_mouse_transparent(
        self,
        transparent: bool,
    ) -> None:
        """Enable/disable click-through for passive/interactive mode."""
        self.setAttribute(
            Qt.WidgetAttribute.WA_TransparentForMouseEvents,
            transparent,
        )

    def _sync_pointer_interaction(self) -> None:
        """
        Keep passive mode click-through while still supporting hover UI.

        PASSIVE  -> mouse-transparent, so the user can select text underneath.
        INTERACTIVE -> mouse input is captured by ResultView for move/resize,
                       buttons, scrolling, and other normal interaction.
        """
        if not self.isVisible() or self._drag_mode is not None:
            return

        # Keep the current input mode while the user is dragging. Otherwise
        # crossing this floating window can toggle mouse transparency and
        # steal an in-progress text-selection gesture from the app below.
        if self._mouse_button_is_down():
            self._last_mouse_button_seen_at = time.monotonic()
            return

        # A double-click or a click followed immediately by a drag contains
        # a short gap with no button held. Keep click-through stable across
        # that gap so hover polling cannot capture the next press.
        quiet_period = self._mouse_gesture_quiet_period()
        if time.monotonic() - self._last_mouse_button_seen_at < quiet_period:
            return

        inside = self._cursor_inside()

        if inside:
            if not self._interactive:
                self._interactive = True
                self._set_mouse_transparent(False)
                self._apply_interactive_style()
            return

        if self._interactive:
            self._interactive = False
            self._reset_pointer_cursors()
            self._apply_passive_style()

        self._set_mouse_transparent(True)

    def showEvent(
        self,
        event: QShowEvent,
    ) -> None:
        """
        Show the window, then apply the visual state from the real cursor
        position. Stacking remains Dashboard-style.
        """
        super().showEvent(
            event
        )

        # Re-assert top-most state after the native window has been created.
        if not self._x11_display:
            self._initialize_x11_pointer_query()
        self._bring_to_front()
        self._pointer_state_timer.start()
        self._sync_pointer_interaction()

    def enterEvent(
        self,
        event: QEnterEvent,
    ) -> None:
        """
        Pointer entered the overlay; update its interaction style without
        changing stacking or keyboard focus.
        """
        if self._mouse_button_is_down():
            self._last_mouse_button_seen_at = time.monotonic()
            super().enterEvent(event)
            return

        self._interactive = True
        self._set_mouse_transparent(False)

        self._apply_interactive_style()

        super().enterEvent(
            event
        )

    def leaveEvent(
        self,
        event: QEvent,
    ) -> None:
        """
        Leaving the window changes appearance only.

        The window itself remains visible and remains a top-level window.
        """
        if (
            self._drag_mode is not None
            or self._mouse_button_is_down()
        ):
            if self._mouse_button_is_down():
                self._last_mouse_button_seen_at = time.monotonic()
            super().leaveEvent(
                event
            )
            return

        self._interactive = False

        self._reset_pointer_cursors()
        self._apply_passive_style()

        # CRITICAL: leaving the ResultView changes appearance only.
        # It never calls hide(), close(), or setVisible(False).
        # Passive mode is mouse-transparent so a new selection can be made
        # directly in the application underneath the floating result.
        self._set_mouse_transparent(True)

        super().leaveEvent(
            event
        )

    def _reset_pointer_cursors(self) -> None:
        """Reset the ResultView and all child cursors after leaving the view."""
        self.unsetCursor()

        for child in self.findChildren(QWidget):
            child.unsetCursor()

    def _initialize_x11_pointer_query(self) -> None:
        """Prepare a lightweight X11 query for buttons held over other apps."""
        if QGuiApplication.platformName().lower() != "xcb":
            return

        try:
            library_name = ctypes.util.find_library("X11")
            if not library_name:
                return

            library = ctypes.CDLL(library_name)
            library.XOpenDisplay.argtypes = [ctypes.c_char_p]
            library.XOpenDisplay.restype = ctypes.c_void_p
            library.XDefaultRootWindow.argtypes = [ctypes.c_void_p]
            library.XDefaultRootWindow.restype = ctypes.c_ulong
            library.XQueryPointer.argtypes = [
                ctypes.c_void_p,
                ctypes.c_ulong,
                ctypes.POINTER(ctypes.c_ulong),
                ctypes.POINTER(ctypes.c_ulong),
                ctypes.POINTER(ctypes.c_int),
                ctypes.POINTER(ctypes.c_int),
                ctypes.POINTER(ctypes.c_int),
                ctypes.POINTER(ctypes.c_int),
                ctypes.POINTER(ctypes.c_uint),
            ]
            library.XQueryPointer.restype = ctypes.c_int

            display = library.XOpenDisplay(None)
            if not display:
                return

            self._x11_library = library
            self._x11_display = display
            self._x11_root = library.XDefaultRootWindow(display)
        except (OSError, AttributeError, TypeError):
            self._x11_library = None
            self._x11_display = None
            self._x11_root = 0

    def _close_x11_pointer_query(self) -> None:
        if self._x11_library and self._x11_display:
            try:
                self._x11_library.XCloseDisplay.argtypes = [ctypes.c_void_p]
                self._x11_library.XCloseDisplay.restype = ctypes.c_int
                self._x11_library.XCloseDisplay(self._x11_display)
            except (OSError, AttributeError, TypeError):
                pass

        self._x11_display = None
        self._x11_root = 0

    def _mouse_button_is_down(self) -> bool:
        """Read held buttons globally; Qt may not see presses passed to another app."""
        if self._x11_library and self._x11_display and self._x11_root:
            root_return = ctypes.c_ulong()
            child_return = ctypes.c_ulong()
            root_x = ctypes.c_int()
            root_y = ctypes.c_int()
            window_x = ctypes.c_int()
            window_y = ctypes.c_int()
            button_mask = ctypes.c_uint()

            try:
                queried = self._x11_library.XQueryPointer(
                    self._x11_display,
                    self._x11_root,
                    ctypes.byref(root_return),
                    ctypes.byref(child_return),
                    ctypes.byref(root_x),
                    ctypes.byref(root_y),
                    ctypes.byref(window_x),
                    ctypes.byref(window_y),
                    ctypes.byref(button_mask),
                )
            except (OSError, ValueError):
                queried = 0

            if queried:
                # X11 Button1Mask through Button5Mask cover the physical buttons.
                return bool(button_mask.value & 0x1F00)

        # Fallback for non-X11 backends or when the native pointer query fails.
        return QGuiApplication.mouseButtons() != Qt.MouseButton.NoButton


    def _cursor_inside(self) -> bool:
        local_position = self.mapFromGlobal(
            self.cursor().pos()
        )

        return self.rect().contains(
            local_position
        )

    # ======================================================================
    # STACKING
    # ======================================================================

    def _bring_to_front(self) -> None:
        """
        Put ResultView above normal application windows without activating it.

        Qt handles the normal case through WindowStaysOnTopHint. On X11 we
        additionally ask the window manager for EWMH _NET_WM_STATE_ABOVE.
        This is a stacking request, not a focus/activation request.
        """
        if not self.isVisible():
            return

        self.raise_()
        self._set_x11_above()

    @staticmethod
    def _mouse_gesture_quiet_period() -> float:
        """Keep hover mode stable for one platform double-click interval."""
        app = QGuiApplication.instance()
        if app is None:
            return 0.5
        return app.styleHints().mouseDoubleClickInterval() / 1000.0 + 0.12

    def _set_x11_above(self) -> None:
        """Ask an X11 window manager to keep this native window ABOVE."""
        try:
            if QGuiApplication.platformName().lower() != "xcb":
                return

            wmctrl = shutil.which("wmctrl")
            if wmctrl is None:
                return

            process = self._wmctrl_process
            if process.state() != QProcess.ProcessState.NotRunning:
                return

            window_id = int(self.winId())
            process.setProgram(wmctrl)
            process.setArguments(
                ["-i", "-r", hex(window_id), "-b", "add,above"]
            )
            environment = QProcessEnvironment.systemEnvironment()
            environment.insert("LC_ALL", "C")
            process.setProcessEnvironment(environment)
            process.start()
        except (
            OSError,
            ValueError,
            RuntimeError,
        ):
            # Qt's WindowStaysOnTopHint remains active even when native
            # X11 reinforcement is unavailable.
            return

    # ======================================================================
    # POINTER TRACKING
    # ======================================================================

    def _install_pointer_tracking(
        self,
        widget: QWidget,
    ) -> None:
        """
        Install ResultView as an event filter only on itself and descendants.

        No QApplication-global event filter is used.
        """
        widget.setMouseTracking(
            True
        )

        widget.installEventFilter(
            self
        )

        for child in widget.findChildren(
            QWidget
        ):
            child.setMouseTracking(
                True
            )

            child.installEventFilter(
                self
            )

    def _belongs_to_view(
        self,
        watched: object,
    ) -> bool:
        if watched is self:
            return True

        if not isinstance(
            watched,
            QWidget,
        ):
            return False

        return self.isAncestorOf(
            watched
        )

    def _is_control(
        self,
        watched: object,
    ) -> bool:
        """
        A/B/X buttons and scrollbars retain normal Qt behavior.
        """
        if not isinstance(
            watched,
            QWidget,
        ):
            return False

        current: QWidget | None = watched

        while (
            current is not None
            and current is not self
        ):
            if isinstance(
                current,
                (
                    QAbstractButton,
                    QScrollBar,
                ),
            ):
                return True

            current = current.parentWidget()

        return False

    def _local_position(
        self,
        watched: QWidget,
        event: QMouseEvent,
    ) -> QPoint:
        if watched is self:
            return event.position().toPoint()

        return watched.mapTo(
            self,
            event.position().toPoint(),
        )

    # ======================================================================
    # EVENT FILTER
    # ======================================================================

    def eventFilter(
        self,
        watched: object,
        event: QEvent,
    ) -> bool:
        if not self._belongs_to_view(
            watched
        ):
            return False

        if not isinstance(
            watched,
            QWidget,
        ):
            return super().eventFilter(
                watched,
                event,
            )

        event_type = event.type()

        # Passive mode intentionally does not consume input. The native
        # attribute should already make the window click-through, but this
        # guard also protects against transient events during the state
        # transition.
        if not self._interactive:
            return False

        # --------------------------------------------------------------
        # ENTER ANY RESULTVIEW CHILD
        # --------------------------------------------------------------
        #
        # This mirrors DictionaryDashboard:
        #
        # entering an inner child is also an intentional interaction with
        # the floating window, so bring it forward.

        if event_type == QEvent.Type.Enter:
            self._interactive = True
            self._apply_interactive_style()
            return False

        # --------------------------------------------------------------
        # MOUSE MOVE
        # --------------------------------------------------------------

        if event_type == QEvent.Type.MouseMove:
            if not isinstance(
                event,
                QMouseEvent,
            ):
                return False

            global_position = (
                event.globalPosition()
                .toPoint()
            )

            # Continue active drag/resize even when the pointer moves across
            # child widgets.
            if self._drag_mode is not None:
                self._continue_manual_operation(
                    global_position
                )
                return True

            # Controls own their pointer behavior.
            if self._is_control(
                watched
            ):
                return False

            local_position = self._local_position(
                watched,
                event,
            )

            self.setCursor(
                self._cursor_for_edges(
                    self._edges_at(
                        local_position
                    )
                )
            )

            return False

        # --------------------------------------------------------------
        # LEFT PRESS
        # --------------------------------------------------------------

        if (
            event_type
            == QEvent.Type.MouseButtonPress
            and isinstance(
                event,
                QMouseEvent,
            )
            and event.button()
            == Qt.MouseButton.LeftButton
        ):
            # Raise first, exactly as DictionaryDashboard03 does.
            self._bring_to_front()

            global_position = (
                event.globalPosition()
                .toPoint()
            )

            local_position = self._local_position(
                watched,
                event,
            )

            edges = self._edges_at(
                local_position
            )

            # ----------------------------------------------------------
            # PRIORITY 1 — RESIZE
            # ----------------------------------------------------------

            if edges != Qt.Edge(0):
                handle = self.windowHandle()

                if (
                    handle is not None
                    and handle.startSystemResize(
                        edges
                    )
                ):
                    event.accept()
                    return True

                self._begin_manual_operation(
                    mode="resize",
                    global_position=global_position,
                    edges=edges,
                )

                event.accept()
                return True

            # ----------------------------------------------------------
            # PRIORITY 2 — CONTROLS / SCROLLBAR
            # ----------------------------------------------------------

            # Controls retain normal Qt behavior and therefore are not
            # interpreted as window-move gestures.
            if self._is_control(
                watched
            ):
                return False

            # ----------------------------------------------------------
            # PRIORITY 3 — MOVE FROM ANY CONTENT AREA
            # ----------------------------------------------------------

            handle = self.windowHandle()

            if (
                handle is not None
                and handle.startSystemMove()
            ):
                event.accept()
                return True

            self._begin_manual_operation(
                mode="move",
                global_position=global_position,
                edges=Qt.Edge(0),
            )

            event.accept()
            return True

        # --------------------------------------------------------------
        # LEFT RELEASE
        # --------------------------------------------------------------

        if (
            event_type
            == QEvent.Type.MouseButtonRelease
            and isinstance(
                event,
                QMouseEvent,
            )
            and event.button()
            == Qt.MouseButton.LeftButton
        ):
            if self._drag_mode is not None:
                self._end_manual_operation()

                event.accept()
                return True

            return False

        return super().eventFilter(
            watched,
            event,
        )

    # ======================================================================
    # RESIZE EDGE DETECTION
    # ======================================================================

    def _edges_at(
        self,
        position: QPoint,
    ) -> Qt.Edge:
        edges = Qt.Edge(0)

        x = position.x()
        y = position.y()

        if x <= RESIZE_MARGIN:
            edges |= Qt.Edge.LeftEdge
        elif x >= (
            self.width()
            - RESIZE_MARGIN
        ):
            edges |= Qt.Edge.RightEdge

        if y <= RESIZE_MARGIN:
            edges |= Qt.Edge.TopEdge
        elif y >= (
            self.height()
            - RESIZE_MARGIN
        ):
            edges |= Qt.Edge.BottomEdge

        return edges

    @staticmethod
    def _cursor_for_edges(
        edges: Qt.Edge,
    ) -> Qt.CursorShape:
        left = bool(
            edges
            & Qt.Edge.LeftEdge
        )
        right = bool(
            edges
            & Qt.Edge.RightEdge
        )
        top = bool(
            edges
            & Qt.Edge.TopEdge
        )
        bottom = bool(
            edges
            & Qt.Edge.BottomEdge
        )

        if (left and top) or (
            right and bottom
        ):
            return Qt.CursorShape.SizeFDiagCursor

        if (right and top) or (
            left and bottom
        ):
            return Qt.CursorShape.SizeBDiagCursor

        if left or right:
            return Qt.CursorShape.SizeHorCursor

        if top or bottom:
            return Qt.CursorShape.SizeVerCursor

        return Qt.CursorShape.ArrowCursor

    # ======================================================================
    # MANUAL MOVE / RESIZE FALLBACK
    # ======================================================================

    def _begin_manual_operation(
        self,
        *,
        mode: str,
        global_position: QPoint,
        edges: Qt.Edge,
    ) -> None:
        self._drag_mode = mode
        self._resize_edges = edges

        self._press_global_position = (
            global_position
        )

        self._press_geometry = QRect(
            self.geometry()
        )

        self.grabMouse()

    def _continue_manual_operation(
        self,
        global_position: QPoint,
    ) -> None:
        if (
            self._drag_mode is None
            or self._press_global_position is None
            or self._press_geometry is None
        ):
            return

        delta = (
            global_position
            - self._press_global_position
        )

        # --------------------------------------------------------------
        # MOVE
        # --------------------------------------------------------------

        if self._drag_mode == "move":
            self.move(
                self._press_geometry.topLeft()
                + delta
            )
            return

        # --------------------------------------------------------------
        # RESIZE
        # --------------------------------------------------------------

        if self._drag_mode != "resize":
            return

        original = self._press_geometry

        left = original.left()
        right = original.right()

        top = original.top()
        bottom = original.bottom()

        minimum_width = self.minimumWidth()
        minimum_height = self.minimumHeight()

        if self._resize_edges & Qt.Edge.LeftEdge:
            proposed_left = (
                original.left()
                + delta.x()
            )

            maximum_left = (
                right
                - minimum_width
                + 1
            )

            left = min(
                proposed_left,
                maximum_left,
            )

        if self._resize_edges & Qt.Edge.RightEdge:
            proposed_right = (
                original.right()
                + delta.x()
            )

            minimum_right = (
                left
                + minimum_width
                - 1
            )

            right = max(
                proposed_right,
                minimum_right,
            )

        if self._resize_edges & Qt.Edge.TopEdge:
            proposed_top = (
                original.top()
                + delta.y()
            )

            maximum_top = (
                bottom
                - minimum_height
                + 1
            )

            top = min(
                proposed_top,
                maximum_top,
            )

        if self._resize_edges & Qt.Edge.BottomEdge:
            proposed_bottom = (
                original.bottom()
                + delta.y()
            )

            minimum_bottom = (
                top
                + minimum_height
                - 1
            )

            bottom = max(
                proposed_bottom,
                minimum_bottom,
            )

        self.setGeometry(
            QRect(
                QPoint(
                    left,
                    top,
                ),
                QPoint(
                    right,
                    bottom,
                ),
            )
        )

    def _end_manual_operation(self) -> None:
        self._drag_mode = None
        self._resize_edges = Qt.Edge(0)

        self._press_global_position = None
        self._press_geometry = None

        if QWidget.mouseGrabber() is self:
            self.releaseMouse()

    # ======================================================================
    # CLOSE
    # ======================================================================

    def closeEvent(
        self,
        event: QCloseEvent,
    ) -> None:
        self._end_manual_operation()

        self._pointer_state_timer.stop()
        self._interactive = False
        self._set_mouse_transparent(True)
        self._close_x11_pointer_query()

        super().closeEvent(
            event
        )


__all__ = [
    "Language",
    "ViewMode",
    "TranslationResponseProtocol",
    "ResultView",
]
