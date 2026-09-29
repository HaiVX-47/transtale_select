from __future__ import annotations

from .result import SelectionResult


class SelectionTransformer:
    """
    Final boundary for the selection subsystem.

    All providers must already return SelectionResult.
    This class validates and returns the canonical result type
    used by the rest of the application.
    """

    def transform(
        self,
        result: SelectionResult | None,
    ) -> SelectionResult | None:
        if result is None:
            return None

        if not isinstance(result, SelectionResult):
            raise TypeError(
                "SelectionTransformer expects SelectionResult "
                "or None."
            )

        # SelectionResult.__post_init__ already enforces the
        # structural contract.
        return result


__all__ = [
    "SelectionTransformer",
]