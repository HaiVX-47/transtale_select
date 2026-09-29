from __future__ import annotations

import ctypes
import ctypes.util
import time
from collections.abc import Iterable
from dataclasses import dataclass

from PyQt6.QtCore import (
    QEvent,
    QPoint,
    QRect,
    Qt,
    QTimer,
)
from PyQt6.QtGui import (
    QCloseEvent,
    QHideEvent,
    QEnterEvent,
    QCursor,
    QGuiApplication,
    QMouseEvent,
    QColor,
    QPainter,
    QPen,
    QShowEvent,
)
from PyQt6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QLayout,
    QScrollArea,
    QScrollBar,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)


# ==============================================================
# CONFIG
# ==============================================================

MIN_DASHBOARD_WIDTH = 130
MIN_DASHBOARD_HEIGHT = 90

DEFAULT_DASHBOARD_WIDTH = 360
DEFAULT_DASHBOARD_HEIGHT = 420


# Left column:
#     Word
#     IPA
#
# This width remains fixed while the Dashboard is resized.
# Only the meanings column receives additional horizontal space.

LEFT_COLUMN_WIDTH = 96


# Invisible resize zone around all four edges.

RESIZE_MARGIN = 9


# ==============================================================
# VIEW MODEL
# ==============================================================

@dataclass(
    frozen=True,
    slots=True,
)
class DashboardEntryView:
    """
    UI-neutral representation of one dictionary entry.

    This class belongs entirely to the Dashboard view layer.

    Dashboard does not import backend models.

    The interaction/controller layer is responsible for converting
    backend Dictionary output into this structure.
    """

    term: str

    meanings: tuple[
        str,
        ...,
    ]

    ipas: tuple[
        str,
        ...,
    ] = ()

    entry_type: str = "word"


# ==============================================================
# DASHBOARD HEADER
# ==============================================================

class _DashboardDragHeader(QFrame):
    """
    Visual header of the Dashboard.

    The header itself does not implement window movement.
    The parent Dashboard handles movement for the whole surface.
    """

    def __init__(
        self,
        parent=None,
    ) -> None:

        super().__init__(parent)

        self._build_ui()

    # ==========================================================
    # BUILD
    # ==========================================================

    def _build_ui(
        self,
    ) -> None:

        self.setObjectName(
            "dashboardHeader"
        )

        self.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Fixed,
        )

        layout = QHBoxLayout(
            self
        )

        layout.setContentsMargins(
            8,
            5,
            8,
            5,
        )

        layout.setSpacing(0)

        self.title_label = QLabel(
            "Dictionary"
        )

        self.title_label.setObjectName(
            "dashboardTitle"
        )

        # Parent Dashboard handles pointer interaction.
        self.title_label.setAttribute(
            Qt.WidgetAttribute.WA_TransparentForMouseEvents,
            True,
        )

        layout.addWidget(
            self.title_label
        )

        layout.addStretch()


# ==============================================================
# DASHBOARD
# ==============================================================

class Dashboard(QWidget):
    """
    Independent floating Dictionary Dashboard.

    RESPONSIBILITY
    --------------

    This class only owns Dashboard UI behavior.

    It does not:

    - access the backend;
    - perform dictionary lookup;
    - translate text;
    - own global application state;
    - decide when the Dashboard should appear;
    - decide when the Dashboard should disappear;
    - communicate with ResultView;
    - communicate with ProcessingIndicator;
    - communicate with ControlPanel.

    External layers only provide DashboardEntryView data
    and decide when to show/hide this view.


    The Dashboard remains mouse-interactive while visible. Ctrl+D hides it
    before the user selects text, so hover-based input transparency is not
    needed and would prevent reliable raising and dragging.


    MOVE
    ----

    Hold left mouse button anywhere in the Dashboard body:

        -> move Dashboard

    Exception:

        -> vertical scrollbar keeps its normal behavior.


    RESIZE
    ------

    Edges:
        resize one dimension.

    Corners:
        resize both dimensions.


    COLUMN BEHAVIOR
    ---------------

        ┌──────────────┬───────────────────────────┐
        │ Word + IPA   │ Meaning                   │
        │ fixed 96 px  │ expanding                 │
        └──────────────┴───────────────────────────┘

    Increasing Dashboard width expands only the right column.
    """

    # ==========================================================
    # INIT
    # ==========================================================

    def __init__(
        self,
        parent=None,
    ) -> None:

        super().__init__(parent)

        # ------------------------------------------------------
        # Local UI state
        # ------------------------------------------------------

        self._interactive = False
        self._last_mouse_button_seen_at = 0.0
        self._hover_started_at: float | None = None
        self._x11_library = None
        self._x11_display = None
        self._x11_root = 0

        self._entry_widgets: list[QWidget] = []

        # ------------------------------------------------------
        # Manual move / resize fallback state
        # ------------------------------------------------------

        self._drag_mode: str | None = None

        self._resize_edges = Qt.Edge(0)

        self._press_global_position: QPoint | None = None

        self._press_geometry: QRect | None = None

        # ------------------------------------------------------
        # Build
        # ------------------------------------------------------

        self._build_ui()

        self._initialize_x11_pointer_query()

        # Poll pointer state to update the visual hover style and raise the
        # Dashboard when the pointer enters it.
        self._pointer_state_timer = QTimer(self)
        self._pointer_state_timer.setInterval(40)
        self._pointer_state_timer.timeout.connect(
            self._sync_pointer_interaction
        )

        self._install_pointer_tracking(
            self
        )

        self._apply_passive_style()
        self._set_mouse_transparent(False)

    # ==========================================================
    # BUILD ROOT UI
    # ==========================================================

    def _build_ui(
        self,
    ) -> None:

        # ------------------------------------------------------
        # WINDOW
        # ------------------------------------------------------

        self.setWindowTitle(
            "Dictionary"
        )

        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )

        self.setAttribute(
            Qt.WidgetAttribute.WA_TranslucentBackground,
            True,
        )

        # The Dashboard is a read-only floating view. Mouse input is used for
        # moving, resizing and scrolling, but it must not take keyboard focus
        # away from the editor underneath it.
        self.setFocusPolicy(
            Qt.FocusPolicy.NoFocus,
        )

        # Do not explicitly activate this window.
        #
        # It should remain usable without deliberately stealing
        # keyboard focus from the application where the user
        # selected the text.

        self.setMouseTracking(
            True
        )

        # ------------------------------------------------------
        # WINDOW SIZE
        # ------------------------------------------------------

        self.setMinimumSize(
            MIN_DASHBOARD_WIDTH,
            MIN_DASHBOARD_HEIGHT,
        )

        self.resize(
            DEFAULT_DASHBOARD_WIDTH,
            DEFAULT_DASHBOARD_HEIGHT,
        )

        self.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Expanding,
        )

        # ------------------------------------------------------
        # ROOT LAYOUT
        # ------------------------------------------------------

        root_layout = QVBoxLayout(
            self
        )

        root_layout.setContentsMargins(
            0,
            0,
            0,
            0,
        )

        root_layout.setSpacing(
            0
        )

        root_layout.setSizeConstraint(
            QLayout.SizeConstraint.SetNoConstraint
        )

        # ------------------------------------------------------
        # MAIN CONTAINER
        # ------------------------------------------------------

        self.container = QFrame(
            self
        )

        self.container.setObjectName(
            "dictionaryDashboardContainer"
        )

        self.container.setMinimumSize(
            0,
            0,
        )

        self.container.setMouseTracking(
            True
        )

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

        container_layout.setSpacing(
            4
        )

        container_layout.setSizeConstraint(
            QLayout.SizeConstraint.SetNoConstraint
        )

        root_layout.addWidget(
            self.container
        )

        # ------------------------------------------------------
        # HEADER
        # ------------------------------------------------------

        self.header = _DashboardDragHeader(
            self.container
        )

        container_layout.addWidget(
            self.header
        )

        # ------------------------------------------------------
        # SCROLL AREA
        # ------------------------------------------------------

        self.scroll_area = QScrollArea(
            self.container
        )

        # This view has no keyboard-editable fields. Keep clicks on its
        # content from moving keyboard focus away from the active editor.
        self.scroll_area.setFocusPolicy(
            Qt.FocusPolicy.NoFocus,
        )
        self.scroll_area.viewport().setFocusPolicy(
            Qt.FocusPolicy.NoFocus,
        )
        self.scroll_area.verticalScrollBar().setFocusPolicy(
            Qt.FocusPolicy.NoFocus,
        )

        self.scroll_area.setObjectName(
            "dictionaryScrollArea"
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

        # Horizontal scrolling is unnecessary because text wraps.

        self.scroll_area.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )

        self.scroll_area.setVerticalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )

        # ------------------------------------------------------
        # SCROLL CONTENT
        # ------------------------------------------------------

        self.content = QWidget()

        self.content.setObjectName(
            "dictionaryDashboardContent"
        )

        self.content.setMinimumSize(
            0,
            0,
        )

        self.content.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Preferred,
        )

        self.content_layout = QVBoxLayout(
            self.content
        )

        self.content_layout.setContentsMargins(
            4,
            4,
            4,
            4,
        )

        self.content_layout.setSpacing(
            8
        )

        self.content_layout.setAlignment(
            Qt.AlignmentFlag.AlignTop
        )

        self.content_layout.setSizeConstraint(
            QLayout.SizeConstraint.SetNoConstraint
        )

        self.scroll_area.setWidget(
            self.content
        )

        container_layout.addWidget(
            self.scroll_area,
            1,
        )

    # ==========================================================
    # PUBLIC — ENTRIES
    # ==========================================================

    def set_entries(
        self,
        entries: Iterable[DashboardEntryView],
    ) -> None:
        """
        Replace the complete Dashboard snapshot.

        Example:

            DashboardEntryView(
                term="machine learning",
                meanings=(
                    "học máy",
                    "máy học",
                ),
                ipas=(
                    "/məˈʃiːn ˈlɜːnɪŋ/",
                ),
                entry_type="phrase",
            )
        """

        self.clear_entries()

        for item in entries:

            entry_widget = self._create_entry_widget(
                item
            )

            self.content_layout.addWidget(
                entry_widget
            )

            self._entry_widgets.append(
                entry_widget
            )

            # Register only the newly-created subtree.

            self._install_pointer_tracking(
                entry_widget
            )

        self.content.adjustSize()

    def clear_entries(
        self,
    ) -> None:

        for widget in self._entry_widgets:

            self.content_layout.removeWidget(
                widget
            )

            widget.deleteLater()

        self._entry_widgets.clear()

    # ==========================================================
    # ENTRY UI
    # ==========================================================

    def _create_entry_widget(
        self,
        item: DashboardEntryView,
    ) -> QWidget:
        """
        Render one dictionary entry.

        Layout:

            ┌────────────┬─────────────────────────────┐
            │ term       │ • meaning 1                 │
            │ IPA        │ • meaning 2                 │
            └────────────┴─────────────────────────────┘

        Left:
            fixed width.

        Right:
            expands/contracts with Dashboard.
        """

        frame = QFrame()

        frame.setObjectName(
            "dictionaryEntry"
        )

        frame.setMinimumWidth(
            0
        )

        frame.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Preferred,
        )

        root = QHBoxLayout(
            frame
        )

        root.setContentsMargins(
            4,
            6,
            4,
            6,
        )

        root.setSpacing(
            8
        )

        root.setSizeConstraint(
            QLayout.SizeConstraint.SetNoConstraint
        )

        # ------------------------------------------------------
        # LEFT WIDGET
        # ------------------------------------------------------

        left_widget = QWidget(
            frame
        )

        left_widget.setObjectName(
            "dictionaryLeftColumn"
        )

        # Critical:
        #
        # The left column never receives additional width.

        left_widget.setFixedWidth(
            LEFT_COLUMN_WIDTH
        )

        left_widget.setSizePolicy(
            QSizePolicy.Policy.Fixed,
            QSizePolicy.Policy.Preferred,
        )

        left_column = QVBoxLayout(
            left_widget
        )

        left_column.setContentsMargins(
            0,
            0,
            0,
            0,
        )

        left_column.setSpacing(
            3
        )

        # ------------------------------------------------------
        # TERM
        # ------------------------------------------------------

        term_label = QLabel(
            item.term
        )

        term_label.setObjectName(
            "dictionarySource"
        )

        term_label.setMinimumWidth(
            0
        )

        term_label.setWordWrap(
            True
        )

        # Horizontal Ignored lets QLabel wrap rather than force
        # the outer Dashboard to remain wide.

        term_label.setSizePolicy(
            QSizePolicy.Policy.Ignored,
            QSizePolicy.Policy.Preferred,
        )

        # Dashboard movement takes priority over text selection.

        term_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.NoTextInteraction
        )

        left_column.addWidget(
            term_label
        )

        # ------------------------------------------------------
        # IPA
        # ------------------------------------------------------

        ipa_values = tuple(
            ipa.strip()
            for ipa in item.ipas
            if ipa
            and ipa.strip()
        )

        if ipa_values:

            ipa_label = QLabel(
                "  •  ".join(
                    ipa_values
                )
            )

            ipa_label.setObjectName(
                "dictionaryIPA"
            )

            ipa_label.setMinimumWidth(
                0
            )

            ipa_label.setWordWrap(
                True
            )

            ipa_label.setSizePolicy(
                QSizePolicy.Policy.Ignored,
                QSizePolicy.Policy.Preferred,
            )

            ipa_label.setTextInteractionFlags(
                Qt.TextInteractionFlag.NoTextInteraction
            )

            left_column.addWidget(
                ipa_label
            )

        left_column.addStretch()

        # ------------------------------------------------------
        # RIGHT WIDGET
        # ------------------------------------------------------

        right_widget = QWidget(
            frame
        )

        right_widget.setObjectName(
            "dictionaryRightColumn"
        )

        right_widget.setMinimumWidth(
            0
        )

        # Critical:
        #
        # This side consumes the available extra horizontal space.

        right_widget.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Preferred,
        )

        right_column = QVBoxLayout(
            right_widget
        )

        right_column.setContentsMargins(
            0,
            0,
            0,
            0,
        )

        right_column.setSpacing(
            3
        )

        # ------------------------------------------------------
        # MEANINGS
        # ------------------------------------------------------

        meaning_values = tuple(
            meaning.strip()
            for meaning in item.meanings
            if meaning
            and meaning.strip()
        )

        if not meaning_values:

            meaning_values = (
                "—",
            )

        for meaning in meaning_values:

            meaning_label = QLabel(
                f"• {meaning}"
            )

            meaning_label.setObjectName(
                "dictionaryMeaning"
            )

            meaning_label.setMinimumWidth(
                0
            )

            meaning_label.setWordWrap(
                True
            )

            meaning_label.setSizePolicy(
                QSizePolicy.Policy.Ignored,
                QSizePolicy.Policy.Preferred,
            )

            meaning_label.setTextInteractionFlags(
                Qt.TextInteractionFlag.NoTextInteraction
            )

            right_column.addWidget(
                meaning_label
            )

        right_column.addStretch()

        # ------------------------------------------------------
        # COMPOSE COLUMNS
        # ------------------------------------------------------

        root.addWidget(
            left_widget,
            0,
        )

        root.addWidget(
            right_widget,
            1,
        )

        # Left:
        #     fixed
        #
        # Right:
        #     expanding

        root.setStretch(
            0,
            0,
        )

        root.setStretch(
            1,
            1,
        )

        return frame

    # ==========================================================
    # PUBLIC VISIBILITY
    # ==========================================================

    def show_dashboard(
        self,
    ) -> None:
        """
        Show the Dashboard.

        The caller decides when this method should be called.
        """

        self.show()

        self._bring_to_front()

    def hide_dashboard(
        self,
    ) -> None:
        """
        Hide the Dashboard.

        The caller decides when this method should be called.
        """

        self.hide()

    def showEvent(self, event: QShowEvent) -> None:
        super().showEvent(event)
        if not self._x11_display:
            self._initialize_x11_pointer_query()
        self._pointer_state_timer.start()
        self._sync_pointer_interaction()

    def hideEvent(self, event: QHideEvent) -> None:
        self._pointer_state_timer.stop()
        self._interactive = False
        self._hover_started_at = None
        self._apply_passive_style()
        self._set_mouse_transparent(False)
        super().hideEvent(event)

    def closeEvent(self, event: QCloseEvent) -> None:
        self._pointer_state_timer.stop()
        if self._x11_library and self._x11_display:
            try:
                self._x11_library.XCloseDisplay(self._x11_display)
            except (OSError, AttributeError, TypeError):
                pass
        self._x11_display = None
        self._x11_root = 0
        super().closeEvent(event)

    def _set_mouse_transparent(self, transparent: bool) -> None:
        # The visible overlay must receive mouse events for hover, dragging,
        # and scrolling. Selection mode hides the overlay instead of toggling
        # this Qt-only attribute, which does not pass input to another process.
        del transparent
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, False)

    def _cursor_over_dashboard_controls(self, point: QPoint) -> bool:
        if self._edges_at(point) != Qt.Edge(0):
            return True

        header_origin = self.header.mapTo(self, QPoint(0, 0))
        header_rect = self.header.rect().translated(header_origin)
        if header_rect.contains(point):
            return True

        scrollbar = self.scroll_area.verticalScrollBar()
        if scrollbar.isVisible():
            scrollbar_origin = scrollbar.mapTo(self, QPoint(0, 0))
            scrollbar_rect = scrollbar.rect().translated(scrollbar_origin)
            if scrollbar_rect.contains(point):
                return True

        return False

    def _sync_pointer_interaction(self) -> None:
        if not self.isVisible() or self._drag_mode is not None:
            return

        if self._mouse_button_is_down():
            self._last_mouse_button_seen_at = time.monotonic()
            self._hover_started_at = None
            return

        now = time.monotonic()
        quiet_period = self._mouse_gesture_quiet_period()
        if now - self._last_mouse_button_seen_at < quiet_period:
            return

        point = self.mapFromGlobal(QCursor.pos())
        if self.rect().contains(point):
            if self._interactive:
                self._hover_started_at = now
                self._set_mouse_transparent(
                    not self._cursor_over_dashboard_controls(point)
                )
                return

            if self._hover_started_at is None:
                self._hover_started_at = now
                return

            if now - self._hover_started_at < quiet_period:
                return

            self._interactive = True
            self._bring_to_front()
            self._set_mouse_transparent(
                not self._cursor_over_dashboard_controls(point)
            )
            self._apply_interactive_style()
            return

        self._hover_started_at = None
        if self._interactive:
            self._interactive = False
            self._reset_pointer_cursors()
            self._apply_passive_style()
        self._set_mouse_transparent(True)

    @staticmethod
    def _mouse_gesture_quiet_period() -> float:
        app = QGuiApplication.instance()
        if app is None:
            return 0.5
        return app.styleHints().mouseDoubleClickInterval() / 1000.0 + 0.12

    def _initialize_x11_pointer_query(self) -> None:
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
            library.XCloseDisplay.argtypes = [ctypes.c_void_p]
            library.XCloseDisplay.restype = ctypes.c_int
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

    def _mouse_button_is_down(self) -> bool:
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
                return bool(button_mask.value & 0x1F00)
        return QGuiApplication.mouseButtons() != Qt.MouseButton.NoButton

    # ==========================================================
    # BRING TO FRONT
    # ==========================================================

    def _bring_to_front(
        self,
    ) -> None:
        """
        Raise Dashboard without deliberately requesting
        keyboard focus.
        """

        if not self.isVisible():
            return

        self.raise_()

    # ==========================================================
    # HOVER
    # ==========================================================

    def enterEvent(
        self,
        event: QEnterEvent,
    ) -> None:
        self._bring_to_front()
        self._interactive = True
        self._apply_interactive_style()
        super().enterEvent(
            event
        )

    def leaveEvent(
        self,
        event: QEvent,
    ) -> None:

        self._interactive = False
        self._hover_started_at = None
        self._apply_passive_style()

        super().leaveEvent(
            event
        )

    # ==========================================================
    # POINTER TRACKING REGISTRATION
    # ==========================================================

    def _install_pointer_tracking(
        self,
        widget: QWidget,
    ) -> None:
        """
        Install this Dashboard as an event filter only on:

            - the supplied widget;
            - its descendants.

        No application-global event filter is used.
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

    # ==========================================================
    # EDGE HIT TEST
    # ==========================================================

    def _edges_at(
        self,
        position: QPoint,
    ):
        """
        Determine which resize edges are under position.

        Possible results:

            0
            LeftEdge
            RightEdge
            TopEdge
            BottomEdge
            LeftEdge | TopEdge
            ...
        """

        edges = Qt.Edge(0)

        x = position.x()
        y = position.y()

        width = self.width()
        height = self.height()

        # ------------------------------------------------------
        # HORIZONTAL EDGES
        # ------------------------------------------------------

        if x <= RESIZE_MARGIN:

            edges |= Qt.Edge.LeftEdge

        elif x >= (
            width
            - RESIZE_MARGIN
        ):

            edges |= Qt.Edge.RightEdge

        # ------------------------------------------------------
        # VERTICAL EDGES
        # ------------------------------------------------------

        if y <= RESIZE_MARGIN:

            edges |= Qt.Edge.TopEdge

        elif y >= (
            height
            - RESIZE_MARGIN
        ):

            edges |= Qt.Edge.BottomEdge

        return edges

    # ==========================================================
    # RESIZE CURSOR
    # ==========================================================

    def _cursor_for_edges(
        self,
        edges,
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

        # ------------------------------------------------------
        # DIAGONAL: \
        # ------------------------------------------------------

        if (
            left and top
        ) or (
            right and bottom
        ):

            return Qt.CursorShape.SizeFDiagCursor

        # ------------------------------------------------------
        # DIAGONAL: /
        # ------------------------------------------------------

        if (
            right and top
        ) or (
            left and bottom
        ):

            return Qt.CursorShape.SizeBDiagCursor

        # ------------------------------------------------------
        # HORIZONTAL
        # ------------------------------------------------------

        if left or right:

            return Qt.CursorShape.SizeHorCursor

        # ------------------------------------------------------
        # VERTICAL
        # ------------------------------------------------------

        if top or bottom:

            return Qt.CursorShape.SizeVerCursor

        return Qt.CursorShape.ArrowCursor

    def _update_pointer_cursor(
        self,
        watched: QWidget,
        global_position: QPoint,
    ) -> None:

        local_position = (
            self.mapFromGlobal(
                global_position
            )
        )

        edges = self._edges_at(
            local_position
        )

        watched.setCursor(
            self._cursor_for_edges(
                edges
            )
        )

    def _reset_pointer_cursors(
        self,
    ) -> None:

        self.unsetCursor()

        for child in self.findChildren(
            QWidget
        ):

            child.unsetCursor()

    # ==========================================================
    # SCROLLBAR TEST
    # ==========================================================

    @staticmethod
    def _is_scrollbar_widget(
        watched: QWidget,
    ) -> bool:
        """
        Scrollbars retain their normal mouse-drag behavior.
        """

        current = watched

        while isinstance(
            current,
            QWidget,
        ):

            if isinstance(
                current,
                QScrollBar,
            ):

                return True

            current = current.parentWidget()

        return False

    # ==========================================================
    # EVENT FILTER
    # ==========================================================

    def eventFilter(
        self,
        watched,
        event,
    ) -> bool:

        if not isinstance(
            watched,
            QWidget,
        ):

            return super().eventFilter(
                watched,
                event,
            )

        event_type = event.type()

        # ------------------------------------------------------
        # ENTER ANY DASHBOARD CHILD
        # ------------------------------------------------------

        if (
            event_type
            == QEvent.Type.Enter
        ):
            return False

        # ------------------------------------------------------
        # MOUSE MOVE
        # ------------------------------------------------------

        if (
            event_type
            == QEvent.Type.MouseMove
        ):

            global_position = (
                event.globalPosition()
                .toPoint()
            )

            # Manual fallback operation currently active.

            if self._drag_mode is not None:

                self._continue_manual_drag(
                    global_position
                )

                return True

            if not self._mouse_button_is_down():
                local_position = self.mapFromGlobal(global_position)
                self._set_mouse_transparent(
                    not self._cursor_over_dashboard_controls(local_position)
                )

            self._update_pointer_cursor(
                watched,
                global_position,
            )

            return False

        # ------------------------------------------------------
        # LEFT PRESS
        # ------------------------------------------------------

        if (
            event_type
            == QEvent.Type.MouseButtonPress
            and event.button()
            == Qt.MouseButton.LeftButton
        ):

            global_position = (
                event.globalPosition()
                .toPoint()
            )

            local_position = (
                self.mapFromGlobal(
                    global_position
                )
            )

            edges = self._edges_at(
                local_position
            )

            # --------------------------------------------------
            # PRIORITY 1 — RESIZE
            # --------------------------------------------------

            if edges != Qt.Edge(0):

                handle = (
                    self.windowHandle()
                )

                # Native window-system resize is preferred.

                if (
                    handle is not None
                    and handle.startSystemResize(
                        edges
                    )
                ):

                    event.accept()

                    return True

                # Manual fallback.

                self._begin_manual_resize(
                    global_position,
                    edges,
                )

                event.accept()

                return True

            # --------------------------------------------------
            # PRIORITY 2 — SCROLLBAR
            # --------------------------------------------------

            if self._is_scrollbar_widget(
                watched
            ):

                return False

            # --------------------------------------------------
            # PRIORITY 3 — MOVE FROM ANY NON-CONTROL SURFACE
            # --------------------------------------------------

            # The header has no dedicated buttons; moving from the body makes
            # the window usable from its center as well as its top edge.

            handle = (
                self.windowHandle()
            )

            if (
                handle is not None
                and handle.startSystemMove()
            ):

                event.accept()

                return True

            # Manual fallback.

            self._begin_manual_move(
                global_position
            )

            event.accept()

            return True

        # ------------------------------------------------------
        # LEFT RELEASE
        # ------------------------------------------------------

        if (
            event_type
            == QEvent.Type.MouseButtonRelease
            and event.button()
            == Qt.MouseButton.LeftButton
        ):

            if self._drag_mode is not None:

                self._end_manual_drag()

                event.accept()

                return True

            return False

        return super().eventFilter(
            watched,
            event,
        )

    # ==========================================================
    # MANUAL MOVE FALLBACK
    # ==========================================================

    def _begin_manual_move(
        self,
        global_position: QPoint,
    ) -> None:

        self._drag_mode = "move"

        self._press_global_position = (
            global_position
        )

        self._press_geometry = QRect(
            self.geometry()
        )

        # Continue receiving mouse events even when cursor moves
        # outside the original child widget.

        self.grabMouse()

    # ==========================================================
    # MANUAL RESIZE FALLBACK
    # ==========================================================

    def _begin_manual_resize(
        self,
        global_position: QPoint,
        edges,
    ) -> None:

        self._drag_mode = "resize"

        self._resize_edges = edges

        self._press_global_position = (
            global_position
        )

        self._press_geometry = QRect(
            self.geometry()
        )

        self.grabMouse()

    # ==========================================================
    # CONTINUE MANUAL OPERATION
    # ==========================================================

    def _continue_manual_drag(
        self,
        global_position: QPoint,
    ) -> None:

        if (
            self._press_global_position
            is None
        ):
            return

        if (
            self._press_geometry
            is None
        ):
            return

        delta = (
            global_position
            - self._press_global_position
        )

        # ------------------------------------------------------
        # MOVE
        # ------------------------------------------------------

        if self._drag_mode == "move":

            new_position = (
                self._press_geometry.topLeft()
                + delta
            )

            self.move(
                new_position
            )

            return

        # ------------------------------------------------------
        # RESIZE
        # ------------------------------------------------------

        if self._drag_mode != "resize":
            return

        original = QRect(
            self._press_geometry
        )

        left = original.left()
        right = original.right()
        top = original.top()
        bottom = original.bottom()

        minimum_width = (
            self.minimumWidth()
        )

        minimum_height = (
            self.minimumHeight()
        )

        # ------------------------------------------------------
        # LEFT
        # ------------------------------------------------------

        if (
            self._resize_edges
            & Qt.Edge.LeftEdge
        ):

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

        # ------------------------------------------------------
        # RIGHT
        # ------------------------------------------------------

        if (
            self._resize_edges
            & Qt.Edge.RightEdge
        ):

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

        # ------------------------------------------------------
        # TOP
        # ------------------------------------------------------

        if (
            self._resize_edges
            & Qt.Edge.TopEdge
        ):

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

        # ------------------------------------------------------
        # BOTTOM
        # ------------------------------------------------------

        if (
            self._resize_edges
            & Qt.Edge.BottomEdge
        ):

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

        new_geometry = QRect(
            QPoint(
                left,
                top,
            ),
            QPoint(
                right,
                bottom,
            ),
        )

        self.setGeometry(
            new_geometry
        )

    # ==========================================================
    # END MANUAL OPERATION
    # ==========================================================

    def _end_manual_drag(
        self,
    ) -> None:

        self._drag_mode = None

        self._resize_edges = Qt.Edge(0)

        self._press_global_position = None

        self._press_geometry = None

        if (
            QWidget.mouseGrabber()
            is self
        ):

            self.releaseMouse()

    # ==========================================================
    # PASSIVE STYLE
    # ==========================================================

    def _apply_passive_style(
        self,
    ) -> None:
        """
        Normal viewing mode.
        """

        self.header.hide()

        self.scroll_area.setVerticalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )

        self.setStyleSheet(
            """
            QFrame#dictionaryDashboardContainer {
                background: transparent;
                border: none;
            }

            QWidget#dictionaryDashboardContent,
            QWidget#dictionaryLeftColumn,
            QWidget#dictionaryRightColumn {
                background: transparent;
            }

            QFrame#dictionaryEntry {
                background: transparent;
                border: none;
            }

            QLabel#dictionarySource {
                color: white;

                font-weight: 700;
                font-size: 15px;

                background: transparent;
            }

            QLabel#dictionaryIPA {
                color: rgba(
                    255,
                    255,
                    255,
                    205
                );

                font-size: 13px;
                font-style: italic;

                background: transparent;
            }

            QLabel#dictionaryMeaning {
                color: white;

                font-size: 14px;

                background: transparent;
            }

            QScrollArea#dictionaryScrollArea {
                background: transparent;
                border: none;
            }
            """
        )

    # ==========================================================
    # INTERACTIVE STYLE
    # ==========================================================

    def _apply_interactive_style(
        self,
    ) -> None:
        """
        Mouse-hover / interactive mode.
        """

        self.header.show()

        self.scroll_area.setVerticalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAsNeeded
        )

        self.setStyleSheet(
            """
            QFrame#dictionaryDashboardContainer {
                background-color: rgba(
                    250,
                    250,
                    250,
                    235
                );

                border: 1px solid #b8b8b8;
                border-radius: 8px;
            }

            QFrame#dashboardHeader {
                background-color: rgba(
                    235,
                    235,
                    235,
                    240
                );

                border-radius: 5px;
            }

            QLabel#dashboardTitle {
                color: #111111;

                font-size: 14px;
                font-weight: 700;

                background: transparent;
            }

            QWidget#dictionaryDashboardContent,
            QWidget#dictionaryLeftColumn,
            QWidget#dictionaryRightColumn {
                background: transparent;
            }

            QFrame#dictionaryEntry {
                background-color: rgba(
                    255,
                    255,
                    255,
                    180
                );

                border-bottom: 1px solid #dddddd;
            }

            QLabel#dictionarySource {
                color: #111111;

                font-weight: 700;
                font-size: 15px;

                background: transparent;
            }

            QLabel#dictionaryIPA {
                color: #666666;

                font-size: 13px;
                font-style: italic;

                background: transparent;
            }

            QLabel#dictionaryMeaning {
                color: #333333;

                font-size: 14px;

                background: transparent;
            }

            QScrollArea#dictionaryScrollArea {
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
