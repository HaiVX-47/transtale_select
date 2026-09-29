from __future__ import annotations

from enum import Enum
from typing import Optional

from .schema import (
    DashboardEntryResponse,
    DashboardResponse,
    LLMTranslationResponse,
    TranslationMode,
    TranslationRequest,
)
from .dictionary import DashboardExtractor
from .llm import LanguageTranslator


# ============================================================
# ERRORS
# ============================================================


class PipelineError(Exception):
    """Base exception for all pipeline-level errors."""


class PipelineConfigurationError(PipelineError):
    """Raised when the pipeline configuration is invalid."""


class PipelineInputError(PipelineError):
    """Raised when the pipeline receives invalid input."""


class PipelineExecutionError(PipelineError):
    """Raised when one of the underlying modules fails."""


# ============================================================
# PIPELINE MODE
# ============================================================


class PipelineMode(str, Enum):
    """
    Processing modes supported by the backend pipeline.

    LLM:
        Use only the LLM module.

    DICTIONARY:
        Use only the Dictionary module.

    BOTH:
        Use Dictionary first, then LLM.

    BOTH is an internal pipeline mode.
    It is not required to have a frontend button.
    """

    LLM = "llm"
    DICTIONARY = "dictionary"
    BOTH = "both"


# ============================================================
# PIPELINE
# ============================================================


class TranslationPipeline:
    """
    Application-level translation orchestration.

    Responsibilities
    ----------------
    - Receive a validated TranslationRequest.
    - Dispatch to Dictionary / LLM.
    - Preserve line structure for LLM translation.
    - Convert Dictionary domain entries into Dashboard responses.
    - Return structured backend output.

    This module does NOT:
    - implement dictionary lookup;
    - implement model inference;
    - implement model preprocessing;
    - render UI;
    - know about frontend components.
    """

    def __init__(
        self,
        *,
        mode: PipelineMode = PipelineMode.LLM,
        dictionary: Optional[DashboardExtractor] = None,
        llm: Optional[LanguageTranslator] = None,
    ) -> None:

        self._mode = self._validate_mode(mode)

        self._dictionary = dictionary
        self._llm = llm

        self._validate_configuration()

    # ==================================================================
    # PUBLIC API
    # ==================================================================

    def translate(
        self,
        request: TranslationRequest,
    ) -> (
        LLMTranslationResponse
        | DashboardResponse
    ):
        """
        Process one TranslationRequest.

        Output type depends on the selected processing mode:

            LLM
                -> LLMTranslationResponse

            DICTIONARY
                -> DashboardResponse
        """

        if not isinstance(request, TranslationRequest):
            raise PipelineInputError(
                "Pipeline expects a TranslationRequest."
            )

        try:

            if self._mode is PipelineMode.LLM:
                return self._run_llm(request)

            if self._mode is PipelineMode.DICTIONARY:
                return self._run_dictionary(request)

            if self._mode is PipelineMode.BOTH:
                return self._run_both(request)

        except PipelineError:
            raise

        except Exception as exc:
            raise PipelineExecutionError(
                "Translation pipeline failed: "
                f"{type(exc).__name__}: {exc}"
            ) from exc

        raise PipelineConfigurationError(
            f"Unsupported pipeline mode: {self._mode!r}"
        )

    def __call__(
        self,
        request: TranslationRequest,
    ):
        """Allow pipeline(...) as shorthand for pipeline.translate(...)."""
        return self.translate(request)

    # ==================================================================
    # LLM
    # ==================================================================

    def _run_llm(
        self,
        request: TranslationRequest,
    ) -> LLMTranslationResponse:
        """
        Translate each input line independently.

        IMPORTANT
        ---------
        No translated lines are ever joined.

        Mapping:

            source line i
                ->
            translated line i

        Blank lines are preserved as blank output lines.
        """

        if self._llm is None:
            raise PipelineConfigurationError(
                "LLM mode requires a LanguageTranslator instance."
            )

        source_lines = self._split_lines(
            request.text
        )

        translated_lines: list[str] = []

        for line_index, source_line in enumerate(
            source_lines
        ):

            # ----------------------------------------------------------
            # Preserve blank lines.
            # ----------------------------------------------------------

            if not source_line.strip():
                translated_lines.append("")
                continue

            # ----------------------------------------------------------
            # Each line becomes one independent model request.
            # ----------------------------------------------------------

            line_request = TranslationRequest(
                text=source_line,
                mode=TranslationMode.LLM,
            )

            try:
                translated_line = self._llm.translate_text(
                    line_request
                )

            except Exception as exc:
                raise PipelineExecutionError(
                    "LLM failed on line "
                    f"{line_index + 1}: "
                    f"{type(exc).__name__}: {exc}"
                ) from exc

            if not isinstance(
                translated_line,
                str,
            ):
                raise PipelineExecutionError(
                    "LLM returned a non-string translation "
                    f"for line {line_index + 1}."
                )

            translated_lines.append(
                translated_line.strip()
            )

        # --------------------------------------------------------------
        # IMPORTANT:
        #
        # Do NOT:
        #
        #     "\n".join(translated_lines)
        #
        # The list itself is the structured output.
        # --------------------------------------------------------------

        return LLMTranslationResponse(
            text=request.text,
            translation_lines=tuple(
                translated_lines
            ),
            mode=TranslationMode.LLM,
        )

    # ==================================================================
    # DICTIONARY / DASHBOARD
    # ==================================================================

    def _run_dictionary(
        self,
        request: TranslationRequest,
    ) -> DashboardResponse:
        """
        Run the Dictionary module and preserve the complete
        DashboardEntry information.
        """

        if self._dictionary is None:
            raise PipelineConfigurationError(
                "Dictionary mode requires a DashboardExtractor instance."
            )

        try:

            entries = self._dictionary.extract(
                [request.text]
            )

            if not entries:
                raise PipelineExecutionError(
                    "Dictionary module returned no entries."
                )

            dashboard_entries = tuple(
                self._convert_dashboard_entry(entry)
                for entry in entries
            )

            if not dashboard_entries:
                raise PipelineExecutionError(
                    "Dictionary module produced no dashboard entries."
                )

            return DashboardResponse(
                entries=dashboard_entries,
                mode=TranslationMode.DICTIONARY,
            )

        except PipelineError:
            raise

        except Exception as exc:
            raise PipelineExecutionError(
                "Dictionary module failed: "
                f"{type(exc).__name__}: {exc}"
            ) from exc

    # ==================================================================
    # BOTH
    # ==================================================================

    def _run_both(
        self,
        request: TranslationRequest,
    ) -> LLMTranslationResponse:
        """
        Run Dictionary first, then LLM.

        Dictionary output is treated as auxiliary information.
        The public result of BOTH remains the LLM response.

        BOTH is an internal pipeline mode and does not imply
        a frontend 'Both' button.
        """

        if self._dictionary is None:
            raise PipelineConfigurationError(
                "Both mode requires a DashboardExtractor instance."
            )

        if self._llm is None:
            raise PipelineConfigurationError(
                "Both mode requires a LanguageTranslator instance."
            )

        try:

            # ----------------------------------------------------------
            # Dictionary stage
            # ----------------------------------------------------------

            dictionary_entries = self._dictionary.extract(
                [request.text]
            )

            # The Dictionary stage is intentionally executed here
            # but is not discarded conceptually by the pipeline design.
            #
            # If a future workflow needs the dashboard output during
            # BOTH processing, this is the point where that information
            # is available.
            _ = dictionary_entries

            # ----------------------------------------------------------
            # LLM stage
            # ----------------------------------------------------------

            return self._run_llm(request)

        except PipelineError:
            raise

        except Exception as exc:
            raise PipelineExecutionError(
                "Combined translation pipeline failed: "
                f"{type(exc).__name__}: {exc}"
            ) from exc

    # ==================================================================
    # LINE SPLITTING
    # ==================================================================

    @staticmethod
    def _split_lines(
        text: str,
    ) -> tuple[str, ...]:
        """
        Split text into lines while preserving line structure.

        Supported newline formats:
            \\n
            \\r\\n
            \\r

        Important:
        - Internal empty lines are preserved.
        - Trailing empty lines are preserved.
        - No line is joined with another line.
        """

        normalized = (
            text
            .replace("\r\n", "\n")
            .replace("\r", "\n")
        )

        return tuple(
            normalized.split("\n")
        )

    # ==================================================================
    # DASHBOARD CONVERSION
    # ==================================================================

    @staticmethod
    def _convert_dashboard_entry(
        entry,
    ) -> DashboardEntryResponse:
        """
        Convert a processor DashboardEntry into
        the public DashboardEntryResponse schema.

        All dictionary information is preserved.
        """

        return DashboardEntryResponse(
            term=entry.term,
            meanings=tuple(entry.meanings),
            ipas=tuple(entry.ipas),
            entry_type=entry.entry_type,
        )

    # ==================================================================
    # VALIDATION
    # ==================================================================

    @staticmethod
    def _validate_mode(
        mode: PipelineMode,
    ) -> PipelineMode:

        if isinstance(mode, PipelineMode):
            return mode

        try:
            return PipelineMode(mode)

        except (TypeError, ValueError) as exc:
            raise PipelineConfigurationError(
                f"Invalid pipeline mode: {mode!r}"
            ) from exc

    def _validate_configuration(self) -> None:

        if self._mode is PipelineMode.LLM:

            if self._llm is None:
                raise PipelineConfigurationError(
                    "LLM mode cannot run without a LanguageTranslator."
                )

            return

        if self._mode is PipelineMode.DICTIONARY:

            if self._dictionary is None:
                raise PipelineConfigurationError(
                    "Dictionary mode cannot run without a DashboardExtractor."
                )

            return

        if self._mode is PipelineMode.BOTH:

            if (
                self._dictionary is None
                or self._llm is None
            ):
                raise PipelineConfigurationError(
                    "Both mode requires both "
                    "Dictionary and LLM modules."
                )

            return

        raise PipelineConfigurationError(
            f"Unsupported pipeline mode: {self._mode!r}"
        )


# ============================================================
# FACTORY
# ============================================================


def create_pipeline(
    *,
    mode: PipelineMode = PipelineMode.LLM,
) -> TranslationPipeline:
    """
    Create a fully configured translation pipeline.

    Default:
        LLM only.
    """

    dictionary = None
    llm = None

    if mode in (
        PipelineMode.DICTIONARY,
        PipelineMode.BOTH,
    ):
        dictionary = DashboardExtractor()

    if mode in (
        PipelineMode.LLM,
        PipelineMode.BOTH,
    ):
        llm = LanguageTranslator()

    return TranslationPipeline(
        mode=mode,
        dictionary=dictionary,
        llm=llm,
    )


__all__ = [
    "PipelineError",
    "PipelineConfigurationError",
    "PipelineInputError",
    "PipelineExecutionError",
    "PipelineMode",
    "TranslationPipeline",
    "create_pipeline",
]