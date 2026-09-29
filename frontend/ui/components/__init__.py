"""
frontend.ui.components
======================

COMPONENT PACKAGE CONTRACT
--------------------------

This package contains small reusable UI components used by higher-level
views / ControlPanel.

Current public components
-------------------------
    1. LLMButton
           Select / toggle the LLM processing mode.

    2. DictionaryButton
           Select / toggle the Dictionary processing mode.

    3. StartButton
           Request that the application starts its main operation.

    4. StopButton
           Request that the application stops its main operation.

The components are deliberately THIN UI components.

They do NOT:
    - access Selection;
    - access InteractionProcessor;
    - call backend Dictionary / LLM code;
    - modify application state directly;
    - decide what another UI component should do;
    - create Dashboard / ResultView / SelectionIndicator;
    - own application-wide controller logic.

The higher-level ControlPanel / controller / state layer owns those decisions.


==============================================================================
1. PUBLIC IMPORTS
==============================================================================

Preferred import style:

    from frontend.ui.components import (
        LLMButton,
        DictionaryButton,
        StartButton,
        StopButton,
    )

The package __init__.py intentionally exports only public component classes.

Do not import implementation details through this package.


==============================================================================
2. LLMButton
==============================================================================

Purpose
-------
Represents the user's LLM mode selection.

States
------
    LLMButton.State.INACTIVE
    LLMButton.State.ACTIVE

Initial state:
    INACTIVE

Signal
------
    clicked_requested

The component emits clicked_requested when the user clicks it.

Important:
    The button does NOT decide what selecting LLM means for the application.

Typical ControlPanel usage:

    llm_button = LLMButton()

    llm_button.clicked_requested.connect(
        controller.on_llm_requested
    )

    llm_button.set_state(
        LLMButton.State.ACTIVE
    )

Read current state with:

    llm_button.state()


==============================================================================
3. DictionaryButton
==============================================================================

Purpose
-------
Represents the user's Dictionary mode selection.

States
------
    DictionaryButton.State.INACTIVE
    DictionaryButton.State.ACTIVE

Initial state:
    INACTIVE

Signal
------
    clicked_requested

Typical usage:

    dictionary_button = DictionaryButton()

    dictionary_button.clicked_requested.connect(
        controller.on_dictionary_requested
    )

    dictionary_button.set_state(
        DictionaryButton.State.ACTIVE
    )

Read current state with:

    dictionary_button.state()


==============================================================================
4. StartButton
==============================================================================

Purpose
-------
Represents the request to start the application's main operation.

States
------
    StartButton.State.READY
    StartButton.State.DISABLED

Initial state:
    READY

Signal
------
    start_requested

Important:
    The signal means:
        "the user requested Start"

    It does NOT mean:
        "the backend has started"

    It does NOT start Selection by itself.

The controller/state layer decides what happens after the signal.

Typical usage:

    start_button = StartButton()

    start_button.start_requested.connect(
        controller.on_start_requested
    )

    start_button.set_state(
        StartButton.State.DISABLED
    )

The button itself prevents start_requested from being emitted while it is in
the DISABLED state.


==============================================================================
5. StopButton
==============================================================================

Purpose
-------
Represents the request to stop the application's main operation.

States
------
    StopButton.State.READY
    StopButton.State.DISABLED

Initial state:
    READY

Signal
------
    stop_requested

Important:
    The signal means:
        "the user requested Stop"

    It does NOT directly stop Selection or InteractionProcessor.

The controller/state layer owns the actual stop operation.

Typical usage:

    stop_button = StopButton()

    stop_button.stop_requested.connect(
        controller.on_stop_requested
    )


==============================================================================
6. STATE OWNERSHIP
==============================================================================

The components contain only LOCAL UI state.

Example:

    llm_button.set_state(
        LLMButton.State.ACTIVE
    )

This means only:
    "render this button as active."

It should NOT be interpreted as:
    "the whole application is now in LLM mode."

The application-wide meaning belongs to the state/controller layer.


Recommended ownership:

    State / Controller
          │
          ├── decides current application mode
          │
          ├── calls button.set_state(...)
          │
          └── reacts to button signals

    Component
          │
          ├── stores its local visual state
          ├── renders itself
          └── emits user-intent signals


==============================================================================
7. EXPECTED CONTROL FLOW
==============================================================================

Mode selection
--------------

    User clicks LLMButton
            ↓
    LLMButton.clicked_requested
            ↓
    ControlPanel / Controller
            ↓
    application state changes
            ↓
    controller updates:
        llm_button.set_state(ACTIVE)
        dictionary_button.set_state(INACTIVE)


Start
-----

    User clicks StartButton
            ↓
    StartButton.start_requested
            ↓
    controller
            ↓
    state transition / Selection.start()
            ↓
    backend workflow


Stop
----

    User clicks StopButton
            ↓
    StopButton.stop_requested
            ↓
    controller
            ↓
    state transition / Selection.stop()
            ↓
    backend workflow stops


==============================================================================
8. COMPONENT VS. CONTROL PANEL
==============================================================================

Component
---------
A single button with:
    - local visual state;
    - local rendering;
    - user-intent signal.

ControlPanel
------------
Owns composition of multiple components.

For example:

    ┌────────────────────────────────────┐
    │ LLM | Dictionary | Start | Stop   │
    └────────────────────────────────────┘

ControlPanel decides:
    - which components are visible;
    - layout;
    - how their signals are connected;
    - which state should be rendered.

Controller / State decides:
    - what those user actions mean for the application.


==============================================================================
9. WHAT NOT TO ADD HERE
==============================================================================

Do not put the following into individual button components:

    - Selection.start()
    - Selection.stop()
    - InteractionProcessor.start()
    - InteractionProcessor.stop()
    - backend translation calls
    - Dashboard / ResultView updates
    - SelectionIndicator updates
    - application state machines
    - cross-component coordination

For example, this is architecturally wrong:

    class StartButton(QPushButton):
        ...
        self.processor.start()

Instead:

    class StartButton(QPushButton):
        ...
        self.start_requested.emit()

and let the controller decide what that request means.


==============================================================================
10. REFACTORING RULE
==============================================================================

When modifying this package:

    1. Keep components independent.
    2. Keep backend/controller imports out of component modules.
    3. Preserve signal names unless the ControlPanel contract is intentionally
       changed.
    4. Keep set_state(...) responsible only for local visual state.
    5. Update this file whenever public components or their contracts change.
    6. Do not reintroduce LoadingIndicator here.

LoadingIndicator
----------------
The previous LoadingIndicator component is intentionally removed.

Processing status is now represented by:

    frontend.ui.views.SelectionIndicator

That view is a dedicated floating visual module and must remain separate from
these button components.
"""


from __future__ import annotations


# ============================================================================
# PUBLIC COMPONENT EXPORTS
# ============================================================================
#
# Keep this package-level API small and explicit.
#
# This makes:
#
#     from frontend.ui.components import LLMButton
#
# the preferred usage while keeping the implementation modules independent.
# ============================================================================

from .dictionary_button import DictionaryButton
from .llm_button import LLMButton
from .start_button import StartButton
from .stop_button import StopButton


__all__ = [
    "DictionaryButton",
    "LLMButton",
    "StartButton",
    "StopButton",
]
