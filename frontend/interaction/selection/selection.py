from __future__ import annotations

import hashlib
import logging
import threading
import time
from collections import deque
from collections.abc import Callable
from typing import Deque

from .resolver import SelectionResolver
from .result import SelectionResult


logger = logging.getLogger(__name__)


class Selection:
    """
    Public facade for the entire selection subsystem.

    Responsibilities
    ----------------
    1. Continuously observe the current selection.
    2. Detect when the selection changes.
    3. Wait until the selection remains unchanged for `stable_delay`.
    4. Extract the finalized selection through SelectionResolver.
    5. Store each finalized SelectionResult in one internal LIFO stack.
    6. Deliver finalized results to an optional callback.

    External modules do not need to know:
        - which provider is used,
        - how selection is detected,
        - how long stabilization takes,
        - how the result is extracted,
        - or how the internal stack is maintained.

    Public lifecycle
    ----------------
        selection = Selection()
        selection.start()

        # Later:
        result = selection.pop()

        # Or:
        selection.start(callback=my_callback)

        selection.stop()

    Selection semantics
    -------------------
    A selection is finalized when its observed identity remains unchanged
    for `stable_delay` seconds.

    Each finalized selection is emitted at most once until the observed
    selection changes again.
    """

    def __init__(
        self,
        stable_delay: float = 1.0,
        poll_interval: float = 0.2,
        max_stack_size: int | None = None,
        resolver: SelectionResolver | None = None,
    ) -> None:
        if stable_delay < 0:
            raise ValueError("stable_delay must be >= 0.")

        if poll_interval <= 0:
            raise ValueError("poll_interval must be > 0.")

        if max_stack_size is not None and max_stack_size <= 0:
            raise ValueError("max_stack_size must be > 0 or None.")

        self.stable_delay = stable_delay
        self.poll_interval = poll_interval
        self.max_stack_size = max_stack_size

        self._resolver = resolver or SelectionResolver()

        self._stack: Deque[SelectionResult] = deque()

        self._callback: Callable[[SelectionResult], None] | None = None

        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._lock = threading.RLock()

        # Current observed selection identity.
        self._current_identity: str | None = None

        # Time at which the current identity was first observed.
        self._stable_since: float | None = None

        # Identity of the selection that has already been emitted.
        self._emitted_identity: str | None = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def start(
        self,
        callback: Callable[[SelectionResult], None] | None = None,
    ) -> None:
        """
        Start the selection service.

        The service immediately begins monitoring the current selection.

        `callback`, when provided, is called whenever a new SelectionResult
        has been finalized.

        The callback runs on the selection worker thread. UI frameworks such
        as Qt must marshal UI updates back to the UI thread themselves.
        """
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                if callback is not None:
                    self._callback = callback
                return

            self._callback = callback
            self._stop_event.clear()

            self._current_identity = None
            self._stable_since = None
            self._emitted_identity = None

            self._thread = threading.Thread(
                target=self._run,
                name="selection-service",
                daemon=True,
            )
            self._thread.start()

    def stop(self) -> None:
        """
        Stop the selection service.

        The internal stack is preserved.
        """
        with self._lock:
            self._stop_event.set()
            thread = self._thread

        if thread is not None and thread.is_alive():
            thread.join(timeout=2.0)

        with self._lock:
            self._thread = None
            self._stable_since = None

    def pop(self) -> SelectionResult | None:
        """
        Pop the most recently finalized selection.

        Returns:
            SelectionResult if the stack is non-empty, otherwise None.
        """
        with self._lock:
            if not self._stack:
                return None

            return self._stack.pop()

    def peek(self) -> SelectionResult | None:
        """
        Return the most recently finalized selection without removing it.
        """
        with self._lock:
            if not self._stack:
                return None

            return self._stack[-1]

    def clear(self) -> None:
        """Remove all finalized results from the internal stack."""
        with self._lock:
            self._stack.clear()

    @property
    def stack_size(self) -> int:
        """Number of finalized selections currently stored."""
        with self._lock:
            return len(self._stack)

    @property
    def is_running(self) -> bool:
        """Whether the selection worker is currently running."""
        with self._lock:
            return self._thread is not None and self._thread.is_alive()

    # ------------------------------------------------------------------
    # Internal lifecycle
    # ------------------------------------------------------------------

    def _run(self) -> None:
        """
        Main worker loop.

        This is intentionally the only place that manages:
            - selection observation,
            - stabilization timing,
            - finalization,
            - and result emission.
        """
        while not self._stop_event.is_set():
            try:
                self._check_selection()
            except Exception:
                logger.exception("Unexpected error in selection worker.")

            self._stop_event.wait(self.poll_interval)

    def _check_selection(self) -> None:
        """
        Observe the current selection and manage its stabilization state.
        """
        # Text-only reads avoid per-character geometry calls on every poll.
        # Geometry is fetched once, only after the candidate has stabilized.
        result = self._resolve(include_geometry=False)

        # A provider may return a SelectionResult whose text contains only
        # whitespace (for example during a transient selection transition).
        # Such a result is not a valid user selection and must never reach
        # _finalize() / InteractionProcessor.
        if result is None or not result.text.strip():
            self._reset_pending_selection()
            return

        identity = self._build_identity(result)
        now = time.monotonic()

        with self._lock:
            # First observation of a selection.
            if identity != self._current_identity:
                self._current_identity = identity
                self._stable_since = now
                self._emitted_identity = None
                return

            # Same selection is still present.
            if self._stable_since is None:
                self._stable_since = now
                return

            # Already emitted. Do not emit the same selection again.
            if self._emitted_identity == identity:
                return

            # Has the selection remained unchanged long enough?
            if now - self._stable_since < self.stable_delay:
                return

        # Re-read once with full geometry after the selection is stable. The
        # selection could change between the text snapshot and this read.
        final_result = self._resolve(include_geometry=True)
        if final_result is None or not final_result.text.strip():
            self._reset_pending_selection()
            return

        final_identity = self._build_identity(final_result)
        if final_identity != identity:
            with self._lock:
                self._current_identity = final_identity
                self._stable_since = time.monotonic()
                self._emitted_identity = None
            return

        # Finalize outside the lock.
        self._finalize(final_result, identity)

    def _resolve(self, *, include_geometry: bool) -> SelectionResult | None:
        """Use the enhanced resolver API while keeping injected test resolvers compatible."""
        if isinstance(self._resolver, SelectionResolver):
            return self._resolver.resolve(include_geometry=include_geometry)
        return self._resolver.resolve()

    def _finalize(
        self,
        result: SelectionResult,
        identity: str,
    ) -> None:
        """
        Store and publish one finalized selection.
        """
        with self._lock:
            # Protect against a race where the selection changed while
            # finalization was happening.
            if identity != self._current_identity:
                return

            if self._emitted_identity == identity:
                return

            if not result.text.strip():
                self._reset_pending_selection()
                return

            self._push(result)
            self._emitted_identity = identity

            callback = self._callback

        if callback is not None:
            try:
                callback(result)
            except Exception:
                logger.exception(
                    "Selection callback failed."
                )

    # ------------------------------------------------------------------
    # Stack
    # ------------------------------------------------------------------

    def _push(self, result: SelectionResult) -> None:
        """
        Push one finalized result onto the internal LIFO stack.
        """
        if (
            self.max_stack_size is not None
            and len(self._stack) >= self.max_stack_size
        ):
            # Keep the newest results and discard the oldest one.
            self._stack.popleft()

        self._stack.append(result)

    # ------------------------------------------------------------------
    # Selection state
    # ------------------------------------------------------------------

    def _reset_pending_selection(self) -> None:
        """
        Reset the pending selection state.

        A new selection after this point can be emitted even if its text
        is identical to a previously emitted selection.
        """
        with self._lock:
            self._current_identity = None
            self._stable_since = None
            self._emitted_identity = None

    @staticmethod
    def _build_identity(result: SelectionResult) -> str:
        """
        Build a deterministic identity for the currently observed selection.

        Identity uses the selected text and source, not screen geometry.
        AT-SPI geometry can vary slightly between snapshots; including it
        caused a stable selection to restart its debounce period repeatedly.
        """
        parts: list[str] = [
            result.source,
            result.text,
        ]

        for line in result.lines:
            parts.extend(
                [
                    str(line.index),
                    str(line.start_offset),
                    str(line.end_offset),
                    line.text,
                ]
            )

        payload = "\x1f".join(parts).encode("utf-8")

        return hashlib.sha256(payload).hexdigest()


__all__ = ["Selection"]
