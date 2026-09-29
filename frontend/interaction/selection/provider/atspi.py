from __future__ import annotations

import logging
from typing import List, Optional

import gi

gi.require_version("Atspi", "2.0")

from gi.repository import Atspi

from .base import BaseSelectionProvider
from ..result import (
    SelectionLine,
    SelectionRect,
    SelectionResult,
    SelectionWord,
)
from ...permission.accessibility import AccessibilityPermission


logger = logging.getLogger(__name__)


class AtspiProvider(BaseSelectionProvider):
    """
    Extracts the current text selection through AT-SPI.

    Backend:
        GObject Introspection -> libatspi

    Public output:
        SelectionResult | None

    Selection policy:
        - Only the first selection is used when multiple disjoint selections
          are reported.
        - No artificial text or newline is created.
        - Geometry is best-effort.
        - Failure to obtain geometry does not invalidate the selected text.

    Coordinate system:
        - Atspi.CoordType.SCREEN
    """

    def __init__(self) -> None:
        self.capability = AccessibilityPermission.check_capability()
        self.atspi = Atspi
        self.diagnostic_error: Optional[str] = None

        if not (
            self.capability.module_available
            and self.capability.bus_reachable
        ):
            self.diagnostic_error = self.capability.error_message

    # ============================================================
    # PUBLIC API
    # ============================================================

    def is_available(self) -> bool:
        return (
            self.capability.module_available
            and self.capability.bus_reachable
        )

    def extract(self, *, include_geometry: bool = True) -> SelectionResult | None:
        """
        Find the currently focused text object with an active selection
        and convert it into the project's SelectionResult contract.
        """

        if not self.is_available():
            return None

        self.diagnostic_error = None

        try:
            desktop = self.atspi.get_desktop(0)

            if desktop is None:
                self.diagnostic_error = "AT-SPI desktop is unavailable."
                return None

            # ----------------------------------------------------
            # First preference:
            # active applications
            # ----------------------------------------------------

            active_apps = self._find_active_applications(desktop)

            for app in active_apps:
                result = self._extract_from_application(
                    app,
                    include_geometry=include_geometry,
                )

                if result is not None:
                    return result

            self.diagnostic_error = (
                "No active application exposed a text selection."
            )

            return None

        except Exception as exc:
            self.diagnostic_error = (
                f"AT-SPI extraction failed: {exc}"
            )
            logger.exception("AT-SPI extraction failed")

            return None

    # ============================================================
    # APPLICATION / FOCUS SEARCH
    # ============================================================

    def _find_active_applications(self, desktop) -> List:
        """
        Return applications that are marked ACTIVE.
        """

        applications = []

        try:
            child_count = desktop.get_child_count()
        except Exception:
            return applications

        for i in range(child_count):
            try:
                app = desktop.get_child_at_index(i)

                if app is None:
                    continue

                state_set = app.get_state_set()

                if state_set.contains(
                    self.atspi.StateType.ACTIVE
                ):
                    applications.append(app)

            except Exception:
                continue

        return applications

    def _extract_from_application(
        self,
        app,
        *,
        include_geometry: bool = True,
    ) -> SelectionResult | None:
        """
        Search an application for its focused node and extract selection.
        """

        focused_node = self._find_focused_node(app)

        if focused_node is not None:
            # Most applications expose the selection directly on the
            # focused object.
            result = self._extract_from_node(
                focused_node,
                include_geometry=include_geometry,
            )

            if result is not None:
                return result

            # Some applications focus a container while the actual
            # selection is exposed by a descendant.
            result = self._find_selection_in_subtree(
                focused_node,
                include_geometry=include_geometry,
            )

            if result is not None:
                return result

        # The focused object and its descendants are the useful search area.
        # Walking the rest of a large application tree on every polling cycle
        # can issue thousands of synchronous AT-SPI calls. The resolver will
        # try the clipboard provider if this focused subtree has no selection.
        return None

    def _find_focused_node(self, node):
        """
        Depth-first search for STATE_FOCUSED.
        """

        try:
            state_set = node.get_state_set()

            if state_set.contains(
                self.atspi.StateType.FOCUSED
            ):
                return node

        except Exception:
            pass

        try:
            child_count = node.get_child_count()
        except Exception:
            return None

        for i in range(child_count):
            try:
                child = node.get_child_at_index(i)

                if child is None:
                    continue

                result = self._find_focused_node(child)

                if result is not None:
                    return result

            except Exception:
                continue

        return None

    def _find_selection_in_subtree(
        self,
        node,
        *,
        include_geometry: bool = True,
    ) -> SelectionResult | None:
        """
        Search recursively for a text object containing an active
        selection.
        """

        result = self._extract_from_node(
            node,
            include_geometry=include_geometry,
        )

        if result is not None:
            return result

        try:
            child_count = node.get_child_count()
        except Exception:
            return None

        for i in range(child_count):
            try:
                child = node.get_child_at_index(i)

                if child is None:
                    continue

                result = self._find_selection_in_subtree(
                    child,
                    include_geometry=include_geometry,
                )

                if result is not None:
                    return result

            except Exception:
                continue

        return None

    # ============================================================
    # NODE EXTRACTION
    # ============================================================

    def _extract_from_node(
        self,
        node,
        *,
        include_geometry: bool = True,
    ) -> SelectionResult | None:
        """
        Try extracting a selection directly from one accessible node.
        """

        try:
            text_iface = node.get_text_iface()

            if text_iface is None:
                return None

            n_selections = text_iface.get_n_selections()

            if n_selections <= 0:
                return None

            # ----------------------------------------------------
            # Current SelectionResult contract supports one
            # contiguous text string. Preserve only the first
            # AT-SPI selection.
            # ----------------------------------------------------

            start_offset, end_offset = (
                text_iface.get_selection(0)
            )

            if end_offset <= start_offset:
                return None

            selected_text = text_iface.get_text(
                start_offset,
                end_offset,
            )

            if not selected_text:
                return None

            return self._build_result(
                node=node,
                text_iface=text_iface,
                selected_text=selected_text,
                start_offset=start_offset,
                end_offset=end_offset,
                include_geometry=include_geometry,
            )

        except Exception:
            return None

    # ============================================================
    # RESULT BUILDING
    # ============================================================

    def _build_result(
        self,
        node,
        text_iface,
        selected_text: str,
        start_offset: int,
        end_offset: int,
        *,
        include_geometry: bool = True,
    ) -> SelectionResult | None:

        try:
            lines: List[SelectionLine] = []

            overall_bounds: Optional[SelectionRect] = None

            raw_lines = selected_text.split("\n")

            relative_offset = 0

            for line_index, line_text in enumerate(raw_lines):

                line_start = relative_offset
                line_end = line_start + len(line_text)

                node_line_start = (
                    start_offset + relative_offset
                )

                node_line_end = (
                    node_line_start + len(line_text)
                )

                line_rect: Optional[SelectionRect] = None
                fragments: List[SelectionRect] = []

                # ------------------------------------------------
                # Geometry is best-effort.
                # Text remains valid even if geometry fails.
                # ------------------------------------------------

                if line_text and include_geometry:

                    try:
                        ext = text_iface.get_range_extents(
                            node_line_start,
                            node_line_end,
                            self.atspi.CoordType.SCREEN,
                        )

                        line_rect = self._rect_from_atspi(ext)

                    except Exception as exc:
                        self.diagnostic_error = (
                            "Partial geometry failure: "
                            f"{exc}"
                        )

                    if line_rect is not None:
                        overall_bounds = (
                            self._merge_rects(
                                overall_bounds,
                                line_rect,
                            )
                        )

                    fragments = (
                        self._extract_visual_fragments(
                            text_iface,
                            node_line_start,
                            node_line_end,
                        )
                    )

                # ------------------------------------------------
                # Words are derived from text offsets.
                #
                # Geometry for individual words is deliberately
                # not fabricated.
                # ------------------------------------------------

                words = self._extract_words(
                    line_text=line_text,
                    line_start=line_start,
                )

                lines.append(
                    SelectionLine(
                        index=line_index,
                        text=line_text,
                        start_offset=line_start,
                        end_offset=line_end,
                        rect=line_rect,
                        words=tuple(words),
                        fragments=tuple(fragments),
                    )
                )

                # +1 for newline in selected_text.
                relative_offset += len(line_text) + 1

            return SelectionResult(
                text=selected_text,
                lines=tuple(lines),
                bounds=overall_bounds,
                source="accessibility",
            )

        except Exception as exc:
            self.diagnostic_error = (
                f"Result construction failed: {exc}"
            )

            logger.exception(
                "Failed to build SelectionResult"
            )

            return None

    # ============================================================
    # WORDS
    # ============================================================

    def _extract_words(
        self,
        line_text: str,
        line_start: int,
    ) -> List[SelectionWord]:
        """
        Extract whitespace-delimited words.

        No geometry is guessed here.
        """

        words: List[SelectionWord] = []

        length = len(line_text)
        i = 0

        while i < length:

            while i < length and line_text[i].isspace():
                i += 1

            if i >= length:
                break

            start = i

            while i < length and not line_text[i].isspace():
                i += 1

            end = i

            words.append(
                SelectionWord(
                    text=line_text[start:end],
                    start_offset=line_start + start,
                    end_offset=line_start + end,
                    rect=None,
                )
            )

        return words

    # ============================================================
    # GEOMETRY
    # ============================================================

    @staticmethod
    def _rect_from_atspi(ext) -> Optional[SelectionRect]:
        """
        Convert an AtspiRect-like object into SelectionRect.
        """

        if ext is None:
            return None

        try:
            x = float(ext.x)
            y = float(ext.y)
            width = float(ext.width)
            height = float(ext.height)

        except AttributeError:
            # Compatibility fallback in case GI returns a tuple-like
            # object in a particular environment.
            try:
                x, y, width, height = ext
                x = float(x)
                y = float(y)
                width = float(width)
                height = float(height)

            except Exception:
                return None

        if width <= 0 or height <= 0:
            return None

        return SelectionRect(
            x=x,
            y=y,
            width=width,
            height=height,
        )

    @staticmethod
    def _merge_rects(
        first: Optional[SelectionRect],
        second: SelectionRect,
    ) -> SelectionRect:

        if first is None:
            return second

        min_x = min(first.x, second.x)
        min_y = min(first.y, second.y)

        max_right = max(
            first.right,
            second.right,
        )

        max_bottom = max(
            first.bottom,
            second.bottom,
        )

        return SelectionRect(
            x=min_x,
            y=min_y,
            width=max_right - min_x,
            height=max_bottom - min_y,
        )

    def _extract_visual_fragments(
        self,
        text_iface,
        start_offset: int,
        end_offset: int,
    ) -> List[SelectionRect]:
        """
        Build visual fragments by grouping character extents
        belonging to the same visual row.

        Geometry failure is non-fatal.
        """

        fragments: List[SelectionRect] = []

        current_rect = None
        baseline_y: Optional[float] = None
        max_height = 0.0

        for offset in range(
            start_offset,
            end_offset,
        ):

            try:
                ext = text_iface.get_character_extents(
                    offset,
                    self.atspi.CoordType.SCREEN,
                )

                rect = self._rect_from_atspi(ext)

                if rect is None:
                    continue

                if current_rect is None:
                    current_rect = rect
                    baseline_y = rect.y
                    max_height = rect.height
                    continue

                assert baseline_y is not None

                same_visual_row = (
                    abs(rect.y - baseline_y)
                    < max(max_height, rect.height) * 0.7
                )

                if same_visual_row:

                    current_rect = self._merge_rects(
                        current_rect,
                        rect,
                    )

                    max_height = max(
                        max_height,
                        rect.height,
                    )

                else:

                    fragments.append(current_rect)

                    current_rect = rect
                    baseline_y = rect.y
                    max_height = rect.height

            except Exception:
                # One character failing must not invalidate
                # the whole selection.
                continue

        if current_rect is not None:
            fragments.append(current_rect)

        return fragments
