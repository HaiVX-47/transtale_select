from __future__ import annotations

import os
import sys
from concurrent.futures import Future, ThreadPoolExecutor
from threading import RLock
from typing import Any

if (
    sys.platform.startswith("linux")
    and os.environ.get("XDG_SESSION_TYPE", "").lower() == "wayland"
    and os.environ.get("DISPLAY")
):
    os.environ["QT_QPA_PLATFORM"] = "xcb"

from PyQt6.QtCore import QEvent, QObject, QPoint, QTimer, pyqtSignal, pyqtSlot
from PyQt6.QtGui import QScreen
from PyQt6.QtWidgets import QApplication, QWidget

from backend.schema import (
    DashboardResponse,
    LLMTranslationResponse,
    TranslationMode,
    TranslationRequest,
)

from .control_panel import ControlPanel
from .state import ApplicationState, ProcessingMode
from .interaction.global_hotkey import GlobalHotkey
from .interaction.selection import SelectionResult
from .ui.views import Dashboard, DashboardEntryView, ResultView, SelectionIndicator


class Controller(QObject):
    """
    Single application orchestration center.

    Ownership:
        Controller
            ├── one ControlPanel
            ├── one Dashboard
            ├── one ResultView
            ├── one SelectionIndicator
            └── one ApplicationState

    Runtime invariant:
        STOPPED
            -> ControlPanel shows Start
            -> Dashboard hidden
            -> ResultView hidden
            -> Indicator hidden
            -> Selection stopped

        RUNNING
            -> ControlPanel shows Stop
            -> Selection is active
            -> at most one backend batch is active
            -> Dashboard/ResultView are persistent and reused

    Closing the final result window while RUNNING is equivalent to Stop/reset.
    """

    _selection_signal = pyqtSignal(int, object)
    _manual_selection_signal = pyqtSignal(int, object, object)
    _backend_response_signal = pyqtSignal(int, object)
    _backend_error_signal = pyqtSignal(int, object, object)
    _processing_started_signal = pyqtSignal(int, object)
    _processing_finished_signal = pyqtSignal(int, object)

    def __init__(
        self,
        *,
        state: ApplicationState | None = None,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)

        self.state = state or ApplicationState()

        # These are created exactly once for the whole Controller lifetime.
        self.control_panel = ControlPanel()
        self.dashboard = Dashboard()
        self.result_view = ResultView()
        self.selection_indicator = SelectionIndicator()

        self._started = False
        self._shutting_down = False
        self._selection_mode = False
        self._hotkey = GlobalHotkey(self)

        # Every Start/Stop lifecycle gets a new generation.
        # Old worker callbacks can therefore never affect a new run.
        self._generation = 0

        # Backend execution belongs to Controller.
        self._executor: ThreadPoolExecutor | None = None
        self._futures: set[Future[Any]] = set()

        self._processing_lock = RLock()
        self._processing_active = False
        self._processing_remaining = 0
        self._pending_selection: SelectionResult | None = None
        self._active_selection: SelectionResult | None = None

        self._ui_mutation_depth = 0

        self._connect_ui()
        self._connect_runtime_signals()
        self._hotkey.activated.connect(self._toggle_selection_translate)
        self._install_view_filters()

        self._reset_ui()

    # ------------------------------------------------------------------
    # APPLICATION LIFECYCLE
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Start only the UI shell. Runtime remains STOPPED."""
        if self._shutting_down:
            return

        self._started = True
        self._reset_ui()
        self._show_control_panel()
        self._hotkey.start()

    def shutdown(self) -> None:
        """Idempotently stop runtime and close the persistent UI objects."""
        if self._shutting_down:
            return

        self._shutting_down = True
        self._generation += 1
        self._hotkey.stop()

        self._stop_runtime_resources()
        self._hide_all_runtime_views()

        self.control_panel.hide_panel()
        self.dashboard.close()
        self.result_view.close()
        self.selection_indicator.close()
        self.control_panel.close()

        self._started = False

    # ------------------------------------------------------------------
    # CONTROL PANEL
    # ------------------------------------------------------------------

    @pyqtSlot()
    def on_llm_requested(self) -> None:
        if self.state.is_running:
            return

        current = self.state.mode

        if current is ProcessingMode.LLM:
            next_mode = ProcessingMode.LLM
        elif current is ProcessingMode.DICTIONARY:
            next_mode = ProcessingMode.BOTH
        else:
            next_mode = ProcessingMode.DICTIONARY

        self.state.set_mode(next_mode)
        self._render_mode()

    @pyqtSlot()
    def on_dictionary_requested(self) -> None:
        if self.state.is_running:
            return

        current = self.state.mode

        if current is ProcessingMode.LLM:
            next_mode = ProcessingMode.BOTH
        elif current is ProcessingMode.DICTIONARY:
            next_mode = ProcessingMode.DICTIONARY
        else:
            next_mode = ProcessingMode.LLM

        self.state.set_mode(next_mode)
        self._render_mode()

    @pyqtSlot()
    def on_start_requested(self) -> None:
        """
        Start the actual runtime.

        Important:
            State.start() happens BEFORE the panel is minimized.
            Therefore "Stop" means the runtime really is RUNNING.
        """
        if self._shutting_down or self.state.is_running:
            return

        self._generation += 1
        generation = self._generation

        self._clear_processing_bookkeeping()

        started = self.state.start(monitor_selection=False)

        if not started:
            self._reset_ui()
            return

        # The authoritative lifecycle has now changed.
        self.control_panel.show_stop_mode()
        self._selection_mode = False
        self._show_runtime_views_for_mode()

        # Minimize only after Selection is actually running.
        self.control_panel.minimize_to_taskbar()

    @pyqtSlot()
    def on_stop_requested(self) -> None:
        """
        Stop is deliberately idempotent.

        Repeated Stop clicks cannot create another reset or another worker.
        """
        if self._shutting_down:
            return

        if not self.state.is_running:
            self._render_stopped_ui()
            return

        self._generation += 1
        self._selection_mode = False
        self._stop_runtime_resources()
        self._render_stopped_ui()

    @pyqtSlot()
    def on_close_requested(self) -> None:
        self.shutdown()
        QApplication.quit()

    # ------------------------------------------------------------------
    # SELECTION -> BACKEND
    # ------------------------------------------------------------------

    @pyqtSlot()
    def _toggle_selection_translate(self) -> None:
        """Ctrl+D alternates between a clear selection surface and one-shot translation."""
        if self._shutting_down:
            return
        if not self.state.is_running:
            self.on_start_requested()
            if not self.state.is_running:
                return

        if not self._selection_mode:
            # Enter selection mode: dismiss every overlay and invalidate any
            # stale selection snapshot or callback from an earlier cycle.
            self._selection_mode = True
            self._generation += 1
            self._clear_processing_bookkeeping()
            self.selection_indicator.stop()
            self.selection_indicator.hide_indicator()
            self._with_ui_mutation(self._hide_all_runtime_views)
            return

        # Leave selection mode and resolve the current selection exactly once.
        self._selection_mode = False
        generation = self._generation
        executor = self._ensure_executor()
        future = executor.submit(GlobalHotkey.resolve_current_selection)
        with self._processing_lock:
            self._futures.add(future)
        future.add_done_callback(
            lambda completed, token=generation: self._manual_selection_done(token, completed)
        )

    def _manual_selection_done(self, generation: int, future: Future[Any]) -> None:
        try:
            selection = future.result()
            error = None
        except Exception as exc:
            selection = None
            error = exc
        with self._processing_lock:
            self._futures.discard(future)
        self._manual_selection_signal.emit(generation, selection, error)

    @pyqtSlot(int, object, object)
    def _handle_manual_selection(self, generation: int, selection: object, error: object) -> None:
        if self._shutting_down or generation != self._generation or not self.state.is_running:
            return
        if error is not None:
            self.state.record_error(error if isinstance(error, Exception) else RuntimeError(str(error)))
            return
        if not isinstance(selection, SelectionResult) or not selection.text.strip():
            self.state.record_error(RuntimeError("No selected text found."))
            return
        self._start_backend_batch(generation, selection)

    @pyqtSlot(int, object)
    def _handle_selection(self, generation: int, selection: SelectionResult) -> None:
        """
        Selection callback crosses into Qt through _selection_signal.

        No Qt widget is touched by the Selection worker thread.
        """
        if self._shutting_down:
            return

        if generation != self._generation:
            return

        if not self.state.is_running:
            return

        if not isinstance(selection, SelectionResult):
            self.state.record_error(
                TypeError(
                    "Selection callback returned "
                    f"{type(selection).__name__}, expected SelectionResult."
                )
            )
            return

        if not selection.text.strip():
            return

        with self._processing_lock:
            if self._processing_active:
                # Keep only the newest selection. This prevents executor
                # flooding when the user repeatedly selects text.
                self._pending_selection = selection
                return

        self._start_backend_batch(generation, selection)

    def _start_backend_batch(
        self,
        generation: int,
        selection: SelectionResult,
    ) -> None:
        if self._shutting_down:
            return
        if generation != self._generation or not self.state.is_running:
            return

        mode = self.state.mode
        job_count = 2 if mode is ProcessingMode.BOTH else 1

        with self._processing_lock:
            if self._processing_active:
                self._pending_selection = selection
                return

            self._processing_active = True
            self._processing_remaining = job_count
            self._active_selection = selection

        executor = self._ensure_executor()

        # Indicator means "backend work is now being dispatched", not merely
        # "the application is RUNNING".
        self._processing_started_signal.emit(generation, selection)

        try:
            if mode is ProcessingMode.BOTH:
                self._submit(
                    executor,
                    generation,
                    selection,
                    TranslationMode.DICTIONARY,
                )
                self._submit(
                    executor,
                    generation,
                    selection,
                    TranslationMode.LLM,
                )
            else:
                translation_mode = (
                    TranslationMode.LLM
                    if mode is ProcessingMode.LLM
                    else TranslationMode.DICTIONARY
                )
                self._submit(
                    executor,
                    generation,
                    selection,
                    translation_mode,
                )
        except Exception as exc:
            self._backend_error_signal.emit(
                generation,
                exc,
                selection,
            )
            self._finish_backend_job(generation, selection)

    def _submit(
        self,
        executor: ThreadPoolExecutor,
        generation: int,
        selection: SelectionResult,
        translation_mode: TranslationMode,
    ) -> None:
        future = executor.submit(
            self._run_backend,
            generation,
            selection,
            translation_mode,
        )

        with self._processing_lock:
            self._futures.add(future)

        future.add_done_callback(self._forget_future)

    def _run_backend(
        self,
        generation: int,
        selection: SelectionResult,
        translation_mode: TranslationMode,
    ) -> None:
        """
        Worker-thread backend execution.

        The worker never touches Qt widgets.
        """
        try:
            request = TranslationRequest(
                text=selection.text.strip(),
                mode=translation_mode,
            )

            pipeline = self.state.get_pipeline(
                ProcessingMode.LLM
                if translation_mode is TranslationMode.LLM
                else ProcessingMode.DICTIONARY
            )

            response = pipeline.translate(request)

            self._backend_response_signal.emit(
                generation,
                response,
            )

        except Exception as exc:
            self._backend_error_signal.emit(
                generation,
                exc,
                selection,
            )

        finally:
            self._processing_finished_signal.emit(
                generation,
                selection,
            )

    def _forget_future(self, future: Future[Any]) -> None:
        with self._processing_lock:
            self._futures.discard(future)

    @pyqtSlot(int, object)
    def _handle_backend_response(
        self,
        generation: int,
        response: Any,
    ) -> None:
        if self._shutting_down:
            return
        if generation != self._generation:
            return
        if not self.state.is_running:
            return

        if isinstance(response, DashboardResponse):
            self._display_dictionary_response(response)
            return

        if isinstance(response, LLMTranslationResponse):
            self._display_llm_response(response)
            return

        self.state.record_error(
            TypeError(
                "Unexpected backend response type: "
                f"{type(response).__name__}"
            )
        )

    @pyqtSlot(int, object, object)
    def _handle_backend_error(
        self,
        generation: int,
        error: Exception,
        selection: SelectionResult,
    ) -> None:
        if self._shutting_down:
            return
        if generation != self._generation:
            return
        if not self.state.is_running:
            return

        self.state.record_error(error)

    @pyqtSlot(int, object)
    def _handle_processing_started(
        self,
        generation: int,
        selection: SelectionResult,
    ) -> None:
        if self._shutting_down:
            return
        if generation != self._generation:
            return
        if not self.state.is_running:
            return

        self._position_selection_indicator(selection)
        self.selection_indicator.set_text("running…")
        self.selection_indicator.show_indicator()
        self.selection_indicator.start()

    @pyqtSlot(int, object)
    def _handle_processing_finished(
        self,
        generation: int,
        selection: SelectionResult,
    ) -> None:
        if self._shutting_down:
            return
        if generation != self._generation:
            return

        with self._processing_lock:
            if not self._processing_active:
                return

            self._processing_remaining -= 1

            if self._processing_remaining > 0:
                return

            pending = self._pending_selection
            self._pending_selection = None
            self._processing_active = False
            self._active_selection = None

        self.selection_indicator.stop()
        self.selection_indicator.hide_indicator()

        if (
            pending is not None
            and self.state.is_running
            and generation == self._generation
        ):
            self._start_backend_batch(generation, pending)

    # ------------------------------------------------------------------
    # PERSISTENT VIEW LIFECYCLE
    # ------------------------------------------------------------------

    def _display_dictionary_response(self, response: DashboardResponse) -> None:
        entries = tuple(
            DashboardEntryView(
                term=entry.term,
                meanings=tuple(entry.meanings),
                ipas=tuple(entry.ipas),
                entry_type=entry.entry_type,
            )
            for entry in response.entries
        )

        self.dashboard.set_entries(entries)
        if self.state.mode is ProcessingMode.BOTH:
            self._position_both_views_once()
        else:
            self._position_view_once(self.dashboard)
        self.dashboard.show_dashboard()

        if self.state.mode is not ProcessingMode.BOTH:
            self.result_view.hide_result()

    def _display_llm_response(self, response: LLMTranslationResponse) -> None:
        if self.state.mode is ProcessingMode.BOTH:
            self._position_both_views_once()
        else:
            self._position_view_once(self.result_view)
        self.result_view.display(response)

        if self.state.mode is not ProcessingMode.BOTH:
            self.dashboard.hide_dashboard()

    def _show_runtime_views_for_mode(self) -> None:
        """
        Keep the work area clear until the first translation is ready.

        Showing an empty, always-on-top ResultView immediately on Start puts
        a large transparent window over the editor. That overlay can disrupt
        selection and typing even before it contains anything useful.
        """
        # Actual response handlers show each persistent view when its data
        # arrives, after the user has selected text and the backend finishes.
        return

    # ------------------------------------------------------------------
    # VIEW CLOSE / RESET
    # ------------------------------------------------------------------

    def _install_view_filters(self) -> None:
        self.dashboard.installEventFilter(self)
        self.result_view.installEventFilter(self)

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if (
            not self._shutting_down
            and self._ui_mutation_depth == 0
            and watched in (self.dashboard, self.result_view)
            # Hiding/minimizing a result window is not equivalent to the user
            # closing it. Only an explicit close should reconcile the runtime.
            and event.type() == QEvent.Type.Close
        ):
            QTimer.singleShot(0, self._reconcile_views)

        return super().eventFilter(watched, event)

    def _reconcile_views(self) -> None:
        if self._shutting_down:
            return
        if not self.state.is_running:
            return

        # In BOTH mode, closing one window is allowed.
        # Closing the final visible result window ends the runtime.
        if (
            not self.dashboard.isVisible()
            and not self.result_view.isVisible()
        ):
            self._generation += 1
            self._stop_runtime_resources()
            self._render_stopped_ui()

    # ------------------------------------------------------------------
    # RUNTIME STOP / RESET
    # ------------------------------------------------------------------

    def _stop_runtime_resources(self) -> None:
        """
        Stop every activity owned by the runtime.

        Selection is stopped synchronously. Backend futures are cancelled
        where possible; an already-running backend call cannot be force-killed
        safely, so its callback is invalidated by generation.
        """
        self.selection_indicator.stop()
        self.selection_indicator.hide_indicator()

        self._clear_processing_bookkeeping()

        self.state.stop()

        with self._processing_lock:
            executor = self._executor
            self._executor = None
            futures = tuple(self._futures)
            self._futures.clear()

        for future in futures:
            future.cancel()

        if executor is not None:
            executor.shutdown(wait=False, cancel_futures=True)

    def _clear_processing_bookkeeping(self) -> None:
        with self._processing_lock:
            self._processing_active = False
            self._processing_remaining = 0
            self._pending_selection = None
            self._active_selection = None

    def _ensure_executor(self) -> ThreadPoolExecutor:
        with self._processing_lock:
            if self._executor is None:
                self._executor = ThreadPoolExecutor(
                    max_workers=2,
                    thread_name_prefix="translation-backend",
                )
            return self._executor

    # ------------------------------------------------------------------
    # UI RESET / MODE
    # ------------------------------------------------------------------

    def _reset_ui(self) -> None:
        self._generation += 1

        self._stop_runtime_resources()

        self._with_ui_mutation(self._hide_all_runtime_views)

        self.dashboard.clear_entries()
        self.result_view.clear()

        self.control_panel.reset()

        self.selection_indicator.stop()
        self.selection_indicator.hide_indicator()

    def _render_stopped_ui(self) -> None:
        self._with_ui_mutation(self._hide_all_runtime_views)

        self.dashboard.clear_entries()
        self.result_view.clear()

        self.selection_indicator.stop()
        self.selection_indicator.hide_indicator()

        self.state.reset()
        self.control_panel.reset()
        self._show_control_panel()

    def _render_mode(self) -> None:
        if self.state.mode is ProcessingMode.LLM:
            self.control_panel.set_mode_selection(
                llm_selected=True,
                dictionary_selected=False,
            )
        elif self.state.mode is ProcessingMode.DICTIONARY:
            self.control_panel.set_mode_selection(
                llm_selected=False,
                dictionary_selected=True,
            )
        else:
            self.control_panel.set_mode_selection(
                llm_selected=True,
                dictionary_selected=True,
            )

    def _hide_all_runtime_views(self) -> None:
        self.dashboard.hide_dashboard()
        self.result_view.hide_result()

    def _show_control_panel(self) -> None:
        self._position_control_panel()
        self.control_panel.show_panel()

    # ------------------------------------------------------------------
    # POSITIONING
    # ------------------------------------------------------------------

    def _position_control_panel(self) -> None:
        if self.control_panel.property("_controller_positioned"):
            return

        screen = self._primary_screen()
        if screen is None:
            return

        geometry = screen.availableGeometry()
        self.control_panel.adjustSize()

        self.control_panel.move(
            geometry.center().x() - self.control_panel.width() // 2,
            geometry.top() + 32,
        )
        self.control_panel.setProperty("_controller_positioned", True)

    def _position_view_once(self, view: QWidget) -> None:
        marker = "_controller_initial_positioned"
        if view.property(marker):
            return

        screen = self._primary_screen()
        if screen is None:
            return

        geometry = screen.availableGeometry()
        view.adjustSize()

        view.move(
            geometry.center().x() - view.width() // 2,
            geometry.top() + max(80, geometry.height() // 5),
        )
        view.setProperty(marker, True)

    def _position_both_views_once(self) -> None:
        screen = self._primary_screen()
        if screen is None:
            return

        geometry = screen.availableGeometry()
        margin = 32
        gap = 24

        if (
            self.dashboard.property("_controller_both_positioned")
            and self.result_view.property("_controller_both_positioned")
        ):
            if self.dashboard.geometry().intersects(
                self.result_view.geometry()
            ):
                self.dashboard.adjustSize()
                self.result_view.adjustSize()

                self.dashboard.move(
                    geometry.right() - self.dashboard.width() - margin,
                    geometry.center().y() - self.dashboard.height() // 2,
                )
                self.result_view.move(
                    geometry.left() + margin,
                    geometry.center().y() - self.result_view.height() // 2,
                )
            return

        self.dashboard.adjustSize()
        self.result_view.adjustSize()

        dashboard_x = (
            geometry.right()
            - self.dashboard.width()
            - margin
        )
        dashboard_y = (
            geometry.center().y()
            - self.dashboard.height() // 2
        )

        result_x = geometry.left() + margin
        result_y = (
            geometry.center().y()
            - self.result_view.height() // 2
        )

        if (
            result_x
            + self.result_view.width()
            + gap
            > geometry.center().x()
        ):
            result_x = max(
                geometry.left() + margin,
                geometry.center().x()
                - self.result_view.width()
                - gap,
            )

        self.dashboard.move(dashboard_x, dashboard_y)
        self.result_view.move(result_x, result_y)

        self.dashboard.setProperty(
            "_controller_both_positioned",
            True,
        )
        self.result_view.setProperty(
            "_controller_both_positioned",
            True,
        )

    def _position_selection_indicator(
        self,
        selection: SelectionResult,
    ) -> None:
        if selection.bounds is not None:
            self.selection_indicator.move_to(
                QPoint(
                    selection.bounds.x,
                    selection.bounds.y,
                )
            )
            return

        screen = self._primary_screen()
        if screen is None:
            return

        geometry = screen.availableGeometry()
        self.selection_indicator.adjustSize()

        self.selection_indicator.move_to(
            QPoint(
                geometry.center().x()
                - self.selection_indicator.width() // 2,
                geometry.top()
                + 64,
            )
        )

    @staticmethod
    def _primary_screen() -> QScreen | None:
        app = QApplication.instance()
        return app.primaryScreen() if app is not None else None

    # ------------------------------------------------------------------
    # SIGNALS / UI MUTATION GUARD
    # ------------------------------------------------------------------

    def _connect_ui(self) -> None:
        self.control_panel.llm_requested.connect(
            self.on_llm_requested
        )
        self.control_panel.dictionary_requested.connect(
            self.on_dictionary_requested
        )
        self.control_panel.start_requested.connect(
            self.on_start_requested
        )
        self.control_panel.stop_requested.connect(
            self.on_stop_requested
        )
        self.control_panel.close_requested.connect(
            self.on_close_requested
        )

    def _connect_runtime_signals(self) -> None:
        self._selection_signal.connect(self._handle_selection)
        self._manual_selection_signal.connect(self._handle_manual_selection)
        self._backend_response_signal.connect(
            self._handle_backend_response
        )
        self._backend_error_signal.connect(
            self._handle_backend_error
        )
        self._processing_started_signal.connect(
            self._handle_processing_started
        )
        self._processing_finished_signal.connect(
            self._handle_processing_finished
        )

    class _UIMutation:
        def __init__(self, controller: "Controller") -> None:
            self.controller = controller

        def __enter__(self):
            self.controller._ui_mutation_depth += 1
            return self

        def __exit__(self, exc_type, exc, tb):
            self.controller._ui_mutation_depth -= 1
            return False

    def _with_ui_mutation(self, fn):
        with self._UIMutation(self):
            return fn()


__all__ = ["Controller"]
