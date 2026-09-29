from enum import Enum

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import QPushButton


class DictionaryButton(QPushButton):
    class State(str, Enum):
        INACTIVE = "inactive"
        ACTIVE = "active"

    clicked_requested = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)

        self._state = self.State.INACTIVE

        self._build_ui()
        self._connect_signals()
        self._render()

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        self.setText("Dictionary")
        self.setMinimumSize(100, 32)
        self.setCursor(self.cursor())

    # ------------------------------------------------------------------
    # Interaction
    # ------------------------------------------------------------------

    def _connect_signals(self) -> None:
        self.clicked.connect(self._on_clicked)

    def _on_clicked(self) -> None:
        self.clicked_requested.emit()

    # ------------------------------------------------------------------
    # State
    # ------------------------------------------------------------------

    def set_state(self, state: State) -> None:
        if self._state == state:
            return

        self._state = state
        self._render()

    def state(self) -> State:
        return self._state

    # ------------------------------------------------------------------
    # Rendering
    # ------------------------------------------------------------------

    def _render(self) -> None:
        if self._state == self.State.INACTIVE:
            self._render_inactive()
            return

        if self._state == self.State.ACTIVE:
            self._render_active()
            return

        raise ValueError(
            f"Unsupported DictionaryButton state: {self._state}"
        )

    def _render_inactive(self) -> None:
        self.setStyleSheet(
            """
            QPushButton {
                background-color: #252525;
                color: #d0d0d0;
                border: 1px solid #444444;
                border-radius: 6px;
                padding: 6px 12px;
            }

            QPushButton:hover {
                background-color: #303030;
            }

            QPushButton:pressed {
                background-color: #1f1f1f;
            }
            """
        )

    def _render_active(self) -> None:
        self.setStyleSheet(
            """
            QPushButton {
                background-color: #3b82f6;
                color: white;
                border: 1px solid #2563eb;
                border-radius: 6px;
                padding: 6px 12px;
                font-weight: 600;
            }

            QPushButton:hover {
                background-color: #2563eb;
            }

            QPushButton:pressed {
                background-color: #1d4ed8;
            }
            """
        )