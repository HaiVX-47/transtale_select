from enum import Enum

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import QPushButton


class StartButton(QPushButton):
    class State(str, Enum):
        READY = "ready"
        DISABLED = "disabled"

    start_requested = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)

        self._state = self.State.READY

        self._build_ui()
        self._connect_signals()
        self._render()

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        self.setMinimumSize(90, 32)
        self.setCursor(self.cursor())

    # ------------------------------------------------------------------
    # Interaction
    # ------------------------------------------------------------------

    def _connect_signals(self) -> None:
        self.clicked.connect(self._on_clicked)

    def _on_clicked(self) -> None:
        if self._state != self.State.READY:
            return

        self.start_requested.emit()

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
        if self._state == self.State.READY:
            self._render_ready()
            return

        if self._state == self.State.DISABLED:
            self._render_disabled()
            return

        raise ValueError(
            f"Unsupported StartButton state: {self._state}"
        )

    def _render_ready(self) -> None:
        self.setText("▶ Start")

        self.setEnabled(True)

        self.setStyleSheet(
            """
            QPushButton {
                background-color: #252525;
                color: #d0d0d0;
                border: 1px solid #444444;
                border-radius: 6px;
                padding: 6px 12px;
                font-weight: 600;
            }

            QPushButton:hover {
                background-color: #303030;
            }

            QPushButton:pressed {
                background-color: #1f1f1f;
            }
            """
        )

    def _render_disabled(self) -> None:
        self.setText("▶ Start")

        self.setEnabled(False)

        self.setStyleSheet(
            """
            QPushButton {
                background-color: #1c1c1c;
                color: #666666;
                border: 1px solid #2d2d2d;
                border-radius: 6px;
                padding: 6px 12px;
                font-weight: 600;
            }
            """
        )