from __future__ import annotations

from enum import Enum
from threading import RLock

from backend.pipeline import PipelineMode, TranslationPipeline, create_pipeline
from backend.schema import TranslationMode
from .interaction.selection import Selection


class ApplicationLifecycle(str, Enum):
    STOPPED = "stopped"
    RUNNING = "running"


class ProcessingMode(str, Enum):
    LLM = "llm"
    DICTIONARY = "dictionary"
    BOTH = "both"


class ApplicationState:
    """
    Pure application lifecycle/resource state.

    No Qt. No widgets. No UI decisions.

    Owns:
        - application lifecycle;
        - selected processing mode;
        - one persistent Selection service;
        - lazy backend pipeline resources;
        - last error.

    Controller decides when start/stop/reset happen.
    """

    DEFAULT_MODE = ProcessingMode.LLM

    def __init__(self, *, mode: ProcessingMode = DEFAULT_MODE) -> None:
        self._lock = RLock()
        self._lifecycle = ApplicationLifecycle.STOPPED
        self._mode = self._validate_mode(mode)
        self._selection = Selection(max_stack_size=1)
        self._pipelines: dict[TranslationMode, TranslationPipeline] = {}
        self._last_error: Exception | None = None

    @property
    def lifecycle(self) -> ApplicationLifecycle:
        with self._lock:
            return self._lifecycle

    @property
    def is_running(self) -> bool:
        with self._lock:
            return self._lifecycle is ApplicationLifecycle.RUNNING

    @property
    def mode(self) -> ProcessingMode:
        with self._lock:
            return self._mode

    @property
    def selection(self) -> Selection:
        return self._selection

    @property
    def last_error(self) -> Exception | None:
        with self._lock:
            return self._last_error

    def set_mode(self, mode: ProcessingMode) -> bool:
        mode = self._validate_mode(mode)

        with self._lock:
            if self._lifecycle is ApplicationLifecycle.RUNNING:
                return False
            if mode is self._mode:
                return False
            self._mode = mode
            self._last_error = None
            return True

    def start(self, callback=None, *, monitor_selection: bool = True) -> bool:
        """
        Start the single persistent Selection service.

        No backend model is loaded here.
        """
        with self._lock:
            if self._lifecycle is ApplicationLifecycle.RUNNING:
                return False
            self._last_error = None

        try:
            if monitor_selection:
                self._selection.start(callback=callback)
            elif self._selection.is_running:
                self._selection.stop()
        except Exception as exc:
            self.record_error(exc)
            return False

        with self._lock:
            self._lifecycle = ApplicationLifecycle.RUNNING
        return True

    def stop(self) -> bool:
        """
        Stop Selection and return application lifecycle to STOPPED.

        The persistent Selection object is reused on the next Start.
        """
        with self._lock:
            was_running = self._lifecycle is ApplicationLifecycle.RUNNING
            self._lifecycle = ApplicationLifecycle.STOPPED

        if was_running:
            try:
                self._selection.stop()
            except Exception as exc:
                self.record_error(exc)

        return was_running

    def reset(self) -> None:
        """
        Return application state to its initial state.

        Controller calls this after stopping the runtime.
        """
        self.stop()

        with self._lock:
            self._mode = self.DEFAULT_MODE
            self._last_error = None

    def get_pipeline(self, mode: ProcessingMode) -> TranslationPipeline:
        """
        Lazily create/cache exactly one backend pipeline per translation mode.

        This is a resource accessor, not a lifecycle transition.
        """
        translation_mode = self._translation_mode(mode)

        with self._lock:
            pipeline = self._pipelines.get(translation_mode)
            if pipeline is not None:
                return pipeline

        pipeline_mode = (
            PipelineMode.LLM
            if translation_mode is TranslationMode.LLM
            else PipelineMode.DICTIONARY
        )
        pipeline = create_pipeline(mode=pipeline_mode)

        with self._lock:
            existing = self._pipelines.get(translation_mode)
            if existing is not None:
                return existing
            self._pipelines[translation_mode] = pipeline
            return pipeline

    def record_error(self, error: Exception) -> None:
        with self._lock:
            self._last_error = error

    @staticmethod
    def _validate_mode(mode: ProcessingMode) -> ProcessingMode:
        if isinstance(mode, ProcessingMode):
            return mode
        return ProcessingMode(mode)

    @staticmethod
    def _translation_mode(mode: ProcessingMode) -> TranslationMode:
        if mode is ProcessingMode.LLM:
            return TranslationMode.LLM
        if mode is ProcessingMode.DICTIONARY:
            return TranslationMode.DICTIONARY
        raise ValueError("BOTH is not a concrete backend pipeline mode.")


__all__ = [
    "ApplicationLifecycle",
    "ApplicationState",
    "ProcessingMode",
]
