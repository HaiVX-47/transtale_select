from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


# ============================================================
# GEOMETRY
# ============================================================


@dataclass(frozen=True, slots=True)
class SelectionRect:
    """
    Rectangle in global screen coordinates.

    Coordinates:
        x      -> left
        y      -> top
        width  -> rectangle width
        height -> rectangle height
    """

    x: float
    y: float
    width: float
    height: float

    @property
    def right(self) -> float:
        return self.x + self.width

    @property
    def bottom(self) -> float:
        return self.y + self.height

    @property
    def is_valid(self) -> bool:
        return (
            self.width > 0
            and self.height > 0
        )


# ============================================================
# WORD
# ============================================================


@dataclass(frozen=True, slots=True)
class SelectionWord:
    """
    One selected word/token with optional screen geometry.

    Geometry may be unavailable for selection providers that can
    extract text but cannot expose text coordinates.
    """

    text: str

    start_offset: int
    end_offset: int

    rect: SelectionRect | None = None


# ============================================================
# LINE
# ============================================================


@dataclass(frozen=True, slots=True)
class SelectionLine:
    """
    One logical selected line.

    The line text corresponds to exactly one line in the
    selected text.

    start_offset / end_offset use offsets relative to the complete
    SelectionResult.text.

    rect:
        Bounding rectangle of the selected portion of this line.

    words:
        Optional word-level information available from the
        selection provider.

    fragments:
        A line may visually consist of multiple rectangles.

        This can happen when a selection is represented as
        several visual fragments by the source application.
    """

    index: int

    text: str

    start_offset: int
    end_offset: int

    rect: SelectionRect | None = None

    words: tuple[SelectionWord, ...] = ()

    fragments: tuple[SelectionRect, ...] = ()


# ============================================================
# SELECTION RESULT
# ============================================================


@dataclass(frozen=True, slots=True)
class SelectionResult:
    """
    The ONLY public output type of the entire selection subsystem.

    Every selection provider must convert its native result into
    this structure before returning it.

    The rest of the application must not need to know whether the
    data came from:

        - AT-SPI
        - primary selection
        - screenshot + OCR
        - application-specific integration
        - another future provider

    Coordinate system:
        global screen coordinates.
    """

    # --------------------------------------------------------
    # Complete selected text
    # --------------------------------------------------------

    text: str

    # --------------------------------------------------------
    # Selected visual/logical lines
    # --------------------------------------------------------

    lines: tuple[SelectionLine, ...]

    # --------------------------------------------------------
    # Overall selection geometry
    # --------------------------------------------------------

    bounds: SelectionRect | None = None

    # --------------------------------------------------------
    # Provider metadata
    # --------------------------------------------------------

    source: Literal[
        "accessibility",
        "primary_selection",
        "screenshot_ocr",
        "application_specific",
        "unknown",
    ] = "unknown"

    # ========================================================
    # Validation
    # ========================================================

    def __post_init__(self) -> None:
        if not isinstance(
            self.text,
            str,
        ):
            raise TypeError(
                "SelectionResult.text must be a string."
            )

        if not isinstance(
            self.lines,
            tuple,
        ):
            raise TypeError(
                "SelectionResult.lines must be a tuple."
            )

        if not self.text and self.lines:
            raise ValueError(
                "SelectionResult cannot contain lines "
                "when text is empty."
            )
            
        if self.bounds is not None and not self.bounds.is_valid:
            raise ValueError("SelectionResult.bounds must be valid if present.")

        previous_end = 0
        text_length = len(self.text)

        for expected_index, line in enumerate(
            self.lines
        ):
            if line.index != expected_index:
                raise ValueError(
                    "SelectionLine.index must be continuous "
                    "starting from 0."
                )

            if line.start_offset < 0:
                raise ValueError(
                    "SelectionLine.start_offset cannot be negative."
                )

            if line.end_offset < line.start_offset:
                raise ValueError(
                    "SelectionLine.end_offset cannot be "
                    "smaller than start_offset."
                )

            if line.end_offset > text_length:
                raise ValueError(
                    "SelectionLine.end_offset cannot exceed complete text length."
                )

            if line.start_offset < previous_end:
                raise ValueError(
                    "SelectionLine offsets must be ordered and not overlap."
                )

            # Check slicing semantic
            sliced_text = self.text[line.start_offset:line.end_offset]
            if sliced_text != line.text:
                raise ValueError(
                    f"SelectionLine slice mismatch at index {line.index}: "
                    f"Expected {repr(line.text)}, got {repr(sliced_text)}"
                )
                
            if line.rect is not None and not line.rect.is_valid:
                raise ValueError(f"SelectionLine.rect must be valid at index {line.index}.")
                
            for frag in line.fragments:
                if not frag.is_valid:
                    raise ValueError(f"SelectionLine.fragments must be valid at index {line.index}.")
                    
            for word in line.words:
                if word.start_offset < line.start_offset or word.end_offset > line.end_offset:
                    raise ValueError(f"Word offsets must be within its line at index {line.index}.")
                if word.start_offset > word.end_offset:
                    raise ValueError(f"Word start_offset must be <= end_offset at index {line.index}.")
                if word.rect is not None and not word.rect.is_valid:
                    raise ValueError(f"Word.rect must be valid at index {line.index}.")
                word_sliced = self.text[word.start_offset:word.end_offset]
                if word_sliced != word.text:
                    raise ValueError(
                        f"SelectionWord slice mismatch: Expected {repr(word.text)}, got {repr(word_sliced)}"
                    )

            previous_end = line.end_offset


    # ========================================================
    # Convenience
    # ========================================================

    @property
    def line_count(self) -> int:
        return len(self.lines)

    @property
    def has_geometry(self) -> bool:
        """
        Return True when at least one usable geometry is available (bounds, line rect, fragment, or word rect).
        """

        if (
            self.bounds is not None
            and self.bounds.is_valid
        ):
            return True

        for line in self.lines:
            if line.rect is not None and line.rect.is_valid:
                return True
            for frag in line.fragments:
                if frag is not None and frag.is_valid:
                    return True
            for word in line.words:
                if word.rect is not None and word.rect.is_valid:
                    return True
                    
        return False


    @property
    def has_word_geometry(self) -> bool:
        return any(
            word.rect is not None
            and word.rect.is_valid
            for line in self.lines
            for word in line.words
        )

    @property
    def source_lines(self) -> tuple[str, ...]:
        """
        Return selected text split according to the normalized
        SelectionLine representation.
        """

        return tuple(
            line.text
            for line in self.lines
        )


__all__ = [
    "SelectionRect",
    "SelectionWord",
    "SelectionLine",
    "SelectionResult",
]