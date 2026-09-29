from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor
from collections.abc import Callable
from enum import Enum
from threading import RLock

from backend.pipeline import PipelineMode, TranslationPipeline, create_pipeline
from backend.schema import (
    DashboardResponse,
    LLMTranslationResponse,
    TranslationMode,
    TranslationRequest,
)

from .selection import Selection
from .selection.result import SelectionResult


BackendResponse = LLMTranslationResponse | DashboardResponse
ResultCallback = Callable[[BackendResponse], None]
ErrorCallback = Callable[[Exception, SelectionResult], None]


class ProcessingMode(str, Enum):
    """Mode exposed by the interaction layer."""

    LLM = "llm"
    DICTIONARY = "dictionary"
    BOTH = "both"


class InteractionProcessor:
    """
    Connect Selection to the backend.

    This module is deliberately thin:

        SelectionResult
            -> TranslationRequest
            -> backend TranslationPipeline
            -> native backend response

    The processor does not:
    - inspect or modify Selection internals;
    - implement translation logic;
    - define a new result schema;
    - know anything about the UI.

    In BOTH mode, Dictionary and LLM are submitted as two independent
    backend jobs. Each job publishes its own native backend response.
    """

    def __init__(
        self,
        *,
        selection: Selection | None = None,
        mode: ProcessingMode = ProcessingMode.LLM,
        max_workers: int = 2,
    ) -> None:
        self._mode = self._validate_mode(mode)

        if max_workers <= 0:
            raise ValueError("max_workers must be > 0.")
        if self._mode is ProcessingMode.BOTH and max_workers < 2:
            raise ValueError("BOTH mode requires at least 2 workers.")

        self._selection = selection or Selection()
        self._max_workers = max_workers
        self._executor: ThreadPoolExecutor | None = None
        self._futures: set[Future[None]] = set()

        self._lock = RLock()
        self._running = False
        self._callback: ResultCallback | None = None
        self._error_callback: ErrorCallback | None = None

        self._pipelines = self._create_pipelines(self._mode)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @property
    def mode(self) -> ProcessingMode:
        return self._mode

    @property
    def is_running(self) -> bool:
        with self._lock:
            return self._running

    def start(
        self,
        callback: ResultCallback,
        *,
        error_callback: ErrorCallback | None = None,
    ) -> None:
        """Start the automatic Selection -> Backend flow."""
        if callback is None:
            raise ValueError("callback is required.")

        with self._lock:
            self._callback = callback
            self._error_callback = error_callback

            if self._running:
                return

            self._executor = ThreadPoolExecutor(
                max_workers=self._max_workers,
                thread_name_prefix="interaction-processor",
            )
            self._running = True

        # Selection owns selection detection/stabilization. The processor only
        # receives finalized SelectionResult objects through this callback.
        self._selection.start(callback=self._on_selection)

    def stop(self) -> None:
        """Stop the automatic flow and shut down processing workers."""
        with self._lock:
            if not self._running:
                return
            self._running = False
            executor = self._executor
            self._executor = None
            futures = tuple(self._futures)
            self._futures.clear()

        self._selection.stop()

        for future in futures:
            future.cancel()

        if executor is not None:
            executor.shutdown(wait=True, cancel_futures=True)

    def process(
        self,
        selection_result: SelectionResult,
    ) -> BackendResponse | tuple[BackendResponse, ...]:
        """
        Process one SelectionResult synchronously.

        LLM/DICTIONARY mode returns exactly one native backend response.
        BOTH mode returns a tuple containing the two native backend responses.
        """
        self._validate_selection_result(selection_result)

        if self._mode is ProcessingMode.BOTH:
            # Both paths remain independent. This method is synchronous, so
            # they are executed one after another here; automatic mode uses
            # separate worker jobs and can run them concurrently.
            return (
                self._run_dictionary(selection_result),
                self._run_llm(selection_result),
            )

        if self._mode is ProcessingMode.LLM:
            return self._run_llm(selection_result)

        return self._run_dictionary(selection_result)

    # ------------------------------------------------------------------
    # Selection -> Backend dispatch
    # ------------------------------------------------------------------

    def _on_selection(self, selection_result: SelectionResult) -> None:
        """Receive a finalized SelectionResult from Selection."""
        with self._lock:
            if not self._running or self._executor is None:
                return
            executor = self._executor

        self._validate_selection_result(selection_result)

        if self._mode is ProcessingMode.BOTH:
            # Two genuinely independent jobs.
            self._submit(executor, self._run_and_publish, selection_result, TranslationMode.DICTIONARY)
            self._submit(executor, self._run_and_publish, selection_result, TranslationMode.LLM)
            return

        mode = (
            TranslationMode.LLM
            if self._mode is ProcessingMode.LLM
            else TranslationMode.DICTIONARY
        )
        self._submit(executor, self._run_and_publish, selection_result, mode)

    def _submit(
        self,
        executor: ThreadPoolExecutor,
        fn: Callable[..., None],
        *args: object,
    ) -> None:
        future = executor.submit(fn, *args)
        with self._lock:
            self._futures.add(future)
        future.add_done_callback(self._forget_future)

    def _forget_future(self, future: Future[None]) -> None:
        with self._lock:
            self._futures.discard(future)

    def _run_and_publish(
        self,
        selection_result: SelectionResult,
        mode: TranslationMode,
    ) -> None:
        try:
            if mode is TranslationMode.LLM:
                response = self._run_llm(selection_result)
            else:
                response = self._run_dictionary(selection_result)

            self._publish(response)
        except Exception as exc:
            self._publish_error(exc, selection_result)

    # ------------------------------------------------------------------
    # Backend calls
    # ------------------------------------------------------------------

    def _run_llm(self, selection_result: SelectionResult) -> LLMTranslationResponse:
        request = TranslationRequest(
            text=selection_result.text.strip(),
            mode=TranslationMode.LLM,
        )
        response = self._pipelines[TranslationMode.LLM].translate(request)

        if not isinstance(response, LLMTranslationResponse):
            raise TypeError(
                "LLM pipeline returned an unexpected response type: "
                f"{type(response).__name__}."
            )

        return response

    def _run_dictionary(self, selection_result: SelectionResult) -> DashboardResponse:
        request = TranslationRequest(
            text=selection_result.text.strip(),
            mode=TranslationMode.DICTIONARY,
        )
        response = self._pipelines[TranslationMode.DICTIONARY].translate(request)

        if not isinstance(response, DashboardResponse):
            raise TypeError(
                "Dictionary pipeline returned an unexpected response type: "
                f"{type(response).__name__}."
            )

        return response

    # ------------------------------------------------------------------
    # Output callbacks
    # ------------------------------------------------------------------

    def _publish(self, response: BackendResponse) -> None:
        with self._lock:
            callback = self._callback

        if callback is None:
            return

        try:
            callback(response)
        except Exception:
            # Consumer/UI callback failures must not terminate the processing
            # worker or Selection service.
            return

    def _publish_error(
        self,
        error: Exception,
        selection_result: SelectionResult,
    ) -> None:
        with self._lock:
            callback = self._error_callback

        if callback is None:
            return

        try:
            callback(error, selection_result)
        except Exception:
            return

    # ------------------------------------------------------------------
    # Configuration
    # ------------------------------------------------------------------

    @staticmethod
    def _create_pipelines(
        mode: ProcessingMode,
    ) -> dict[TranslationMode, TranslationPipeline]:
        """Create only the backend flows required by the selected mode."""
        if mode is ProcessingMode.LLM:
            return {
                TranslationMode.LLM: create_pipeline(mode=PipelineMode.LLM),
            }

        if mode is ProcessingMode.DICTIONARY:
            return {
                TranslationMode.DICTIONARY: create_pipeline(
                    mode=PipelineMode.DICTIONARY,
                ),
            }

        # Do not use PipelineMode.BOTH here. The backend's current BOTH mode
        # executes Dictionary as auxiliary work and publicly returns only LLM.
        # This interaction layer needs both native responses independently.
        return {
            TranslationMode.DICTIONARY: create_pipeline(
                mode=PipelineMode.DICTIONARY,
            ),
            TranslationMode.LLM: create_pipeline(mode=PipelineMode.LLM),
        }

    @staticmethod
    def _validate_mode(mode: ProcessingMode) -> ProcessingMode:
        if isinstance(mode, ProcessingMode):
            return mode

        try:
            return ProcessingMode(mode)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"Invalid processing mode: {mode!r}") from exc

    @staticmethod
    def _validate_selection_result(selection_result: SelectionResult) -> None:
        if not isinstance(selection_result, SelectionResult):
            raise TypeError(
                "InteractionProcessor expects SelectionResult, got "
                f"{type(selection_result).__name__}."
            )

        if not selection_result.text.strip():
            raise ValueError("SelectionResult contains empty text.")


__all__ = [
    "BackendResponse",
    "ResultCallback",
    "ErrorCallback",
    "ProcessingMode",
    "InteractionProcessor",
]
