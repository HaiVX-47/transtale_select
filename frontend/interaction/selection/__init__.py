"""
frontend.selection
==================

Selection subsystem public API.

IMPORTANT ARCHITECTURE
----------------------

This package is responsible ONLY for obtaining and stabilizing desktop text
selection.

It does not:
    - translate text;
    - call Dictionary;
    - call LLM;
    - update Dashboard;
    - update ResultView;
    - control SelectionIndicator;
    - own application state;
    - decide what a selected text should do.

The controller / processing layer consumes SelectionResult and decides what
happens next.


==============================================================================
PUBLIC OUTPUT
==============================================================================

The canonical output of the whole selection subsystem is:

    SelectionResult

Providers are hidden behind the SelectionResolver. The rest of the
application should not need to know whether the text came from:

    - AT-SPI;
    - PRIMARY selection;
    - screenshot/OCR in a future implementation;
    - an application-specific provider.

SelectionResult already contains:
    - complete selected text;
    - logical selected lines;
    - optional overall bounds;
    - provider source;
    - optional line/word geometry.


==============================================================================
MOST IMPORTANT: HOW TO USE Selection
==============================================================================

Recommended controller usage:

    from frontend.selection import Selection, SelectionResult

    selection = Selection()

    def on_selection(result: SelectionResult) -> None:
        print(result.text)

    selection.start(
        callback=on_selection,
    )

    # ... application keeps running ...

    selection.stop()

The callback is called whenever a NEW selection has remained stable long
enough to be finalized.

The callback runs on the Selection worker thread.

IMPORTANT:
    Do NOT update Qt widgets directly from this callback.

Instead:

    Selection worker thread
            |
            v
        callback(...)
            |
            v
        Qt signal / queued call
            |
            v
        GUI thread
            |
            v
        Dashboard / ResultView / SelectionIndicator


==============================================================================
Selection PARAMETERS
==============================================================================

    Selection(
        stable_delay=1.0,
        poll_interval=0.2,
        max_stack_size=None,
    )

stable_delay
------------
How long the observed selection must remain unchanged before it is emitted.

Default:
    1.0 second

Example:

    Selection(stable_delay=0.8)

poll_interval
-------------
How often the desktop selection is checked.

Default:
    0.2 second

Smaller values increase polling frequency.

Example:

    Selection(
        stable_delay=1.0,
        poll_interval=0.1,
    )

max_stack_size
--------------
Maximum number of finalized selections stored internally.

Default:
    None  -> unlimited

Example:

    Selection(max_stack_size=20)


==============================================================================
SELECTION LIFECYCLE
==============================================================================

The normal lifecycle is:

    Selection()
        |
        v
    start(callback=...)
        |
        v
    observe desktop selection
        |
        v
    selection changes?
        |
       yes
        |
        v
    wait for stable_delay
        |
        v
    SelectionResult finalized
        |
        +----------------------+
        |                      |
        v                      v
    internal stack          callback(...)
                               |
                               v
                         controller / Qt bridge

Stopping:

    selection.stop()


==============================================================================
IMPORTANT SELECTION SEMANTICS
==============================================================================

A selection is emitted only after its identity remains stable for
`stable_delay`.

The same observed selection is emitted at most once.

When the observed selection changes, a new selection may be emitted after
stabilization.

The implementation also treats empty / whitespace-only selection as an
invalid transient state and resets the pending selection instead of
publishing it.


==============================================================================
CALLBACK VS STACK
==============================================================================

Selection provides TWO ways to consume finalized selections.

1. Callback mode
----------------

    selection.start(callback=on_selection)

This is the recommended pattern for the application's automatic workflow.

Every finalized selection is pushed into the internal stack AND delivered to
the callback.

2. Stack mode
-------------

    selection.start()

    result = selection.pop()

`pop()` returns the newest finalized SelectionResult.

`peek()` returns the newest result without removing it.

`clear()` removes all stored finalized results.

Example:

    result = selection.peek()

    result = selection.pop()

    print(selection.stack_size)


==============================================================================
IMPORTANT: DO NOT MIX UP pop() AND callback
==============================================================================

The callback and internal stack are independent outputs of the same finalized
selection.

A callback does not remove the item from the stack.

Therefore:

    callback(result)
        +
    selection.pop()

can observe the same finalized selection through two different mechanisms.

The controller should choose one consumption strategy intentionally.


==============================================================================
SELECTIONRESULT
==============================================================================

SelectionResult is the canonical data object returned by this package.

Basic usage:

    result.text

Selected logical lines:

    result.lines

Convenience:

    result.source_lines

Geometry:

    result.bounds
    result.has_geometry
    result.has_word_geometry

The coordinate system for geometry is global screen coordinates.

Do not construct application-specific replacement result objects. Use
SelectionResult as the boundary type between this subsystem and the rest of the
application.


==============================================================================
GEOMETRY
==============================================================================

Supporting public types:

    SelectionRect
    SelectionWord
    SelectionLine

SelectionRect
-------------
Global screen rectangle:

    x
    y
    width
    height

SelectionWord
-------------
One selected token with:
    text
    start_offset
    end_offset
    optional rect

SelectionLine
-------------
One logical selected line with:
    index
    text
    start_offset
    end_offset
    optional rect
    optional words
    optional fragments

Geometry is optional.

A provider may successfully return textual selection without coordinates.


==============================================================================
PROVIDER / RESOLVER
==============================================================================

The application should normally NOT instantiate providers directly.

Use:

    Selection()

The current resolver tries providers in priority order:

    1. AtspiProvider
    2. PrimarySelectionProvider

A provider result is accepted only when its text contains at least one
non-whitespace character.

Missing geometry does NOT invalidate a textual selection.

The resolver therefore hides provider-specific behavior from the rest of the
application.


==============================================================================
SelectionTransformer
==============================================================================

SelectionTransformer is a small final boundary validator.

It accepts:

    SelectionResult | None

and returns:

    SelectionResult | None

It is not normally required by application code when Selection already
returns the canonical type.

It exists as an explicit boundary for future pipelines or integrations.


==============================================================================
WHAT THE CONTROLLER SHOULD DO
==============================================================================

The controller is responsible for:

    SelectionResult
        |
        +--> build TranslationRequest
        |
        +--> start backend processing
        |
        +--> show SelectionIndicator
        |
        +--> update Dashboard / ResultView
        |
        +--> decide application state


The Selection module itself must remain unaware of all of those decisions.


==============================================================================
SHUTDOWN
==============================================================================

When the application exits:

    selection.stop()

`stop()` signals the worker and waits briefly for the selection thread to
finish.

The Selection stack itself is preserved when stopping.


==============================================================================
PUBLIC IMPORTS
==============================================================================

Preferred:

    from frontend.selection import (
        Selection,
        SelectionResult,
        SelectionRect,
        SelectionWord,
        SelectionLine,
    )

The resolver, providers, and transformer remain implementation/boundary
details unless a future architecture explicitly promotes them to public API.


==============================================================================
REFACTORING RULE
==============================================================================

When modifying this module:

    1. Keep Selection independent from UI and backend.
    2. Keep SelectionResult as the canonical output type.
    3. Do not put Qt logic inside Selection.
    4. Do not call backend code from Selection.
    5. Preserve callback thread semantics.
    6. Preserve stabilization semantics unless intentionally redesigned.
    7. Update this __init__.py when the public contract changes.
"""


from __future__ import annotations


# ============================================================================
# PUBLIC API
# ============================================================================

from .result import (
    SelectionRect,
    SelectionWord,
    SelectionLine,
    SelectionResult,
)
from .selection import Selection


__all__ = [
    "SelectionRect",
    "SelectionWord",
    "SelectionLine",
    "SelectionResult",
    "Selection",
]
