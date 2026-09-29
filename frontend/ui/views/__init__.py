"""
frontend.ui.views
=================

UI VIEW PACKAGE CONTRACT
------------------------

This package contains only presentation-layer floating views.

Current public views
--------------------
1. Dashboard
       Dictionary result view.

2. ResultView
       LLM translation result view.

3. SelectionIndicator
       Small visual processing indicator.

These views are intentionally independent from:
    - Selection
    - InteractionProcessor
    - backend Dictionary
    - backend LLM
    - application state
    - controllers
    - ControlPanel

The controller/application layer owns orchestration:
    backend state -> which view to use -> when to show/hide -> position.

The views own only:
    - rendering;
    - local interaction;
    - local visual state;
    - their own window lifecycle.

DO NOT MOVE CONTROLLER LOGIC INTO THIS PACKAGE
----------------------------------------------
A view must not decide:
    - when a backend starts;
    - when a backend finishes;
    - which view should appear;
    - where another view should appear;
    - whether Selection is active;
    - application-wide state transitions.

The controller/state layer is responsible for these decisions.


==============================================================================
IMPORTANT: LINUX / WAYLAND / XCB
==============================================================================

The project is developed on a Linux Wayland desktop with XWayland available.

For the floating ResultView, Qt Wayland was not reliable enough for the required
always-on-top behavior. The project therefore deliberately uses XCB/XWayland.

CRITICAL:
    QT_QPA_PLATFORM=xcb

The ResultView contains the same platform safeguard, but this package also
enforces it BEFORE importing any PyQt6 view module.

Do not move the platform block below the PyQt6 imports.

Do not remove it during refactoring unless the window-system architecture has
been intentionally changed and re-tested.

Typical launch:
    QT_QPA_PLATFORM=xcb python -m frontend.test

or:
    python -m frontend.test

when this package is imported on Linux + Wayland + XWayland (DISPLAY set).


==============================================================================
PUBLIC IMPORTS
==============================================================================

Preferred:

    from frontend.ui.views import (
        Dashboard,
        DashboardEntryView,
        ResultView,
        Language,
        ViewMode,
        TranslationResponseProtocol,
        SelectionIndicator,
    )

Do not import private implementation classes such as:
    _DashboardDragHeader


==============================================================================
1. DASHBOARD
==============================================================================

Public classes:
    Dashboard
    DashboardEntryView

DashboardEntryView
------------------
UI-neutral dictionary view model:

    DashboardEntryView(
        term="machine learning",
        meanings=(
            "học máy",
            "máy học",
        ),
        ipas=(
            "/məˈʃiːn ˈlɜːnɪŋ/",
        ),
        entry_type="phrase",
    )

Important:
    - Dashboard does not import backend models.
    - Controller converts backend Dictionary output into DashboardEntryView.
    - ipas may be empty: ().
    - entry_type defaults to "word".

Dashboard
---------
One persistent Dashboard instance should normally be created by the controller.

Typical lifecycle:

    dashboard = Dashboard()

    # new dictionary result:
    dashboard.set_entries(entries)
    dashboard.show_dashboard()

    # hide without destroying:
    dashboard.hide_dashboard()

The Dashboard replaces the current content snapshot through set_entries(...).
Do NOT create a new Dashboard for every selection.

Interaction:
    - passive mode: transparent background, white text, hidden header,
      hidden scrollbar;
    - interactive mode: visible background, dark text, header and scrollbar;
    - leaving the Dashboard does NOT hide it;
    - the Dashboard handles its own move/resize interaction.

The caller decides:
    - when Dashboard is shown;
    - when Dashboard is hidden;
    - where Dashboard is positioned.

The Dashboard does NOT:
    - access backend data;
    - perform lookup;
    - communicate with ResultView;
    - communicate with SelectionIndicator;
    - manage application state.


==============================================================================
2. RESULT VIEW
==============================================================================

Public classes:
    ResultView
    Language
    ViewMode
    TranslationResponseProtocol

ResultView is the floating LLM translation result window.

Backend contract
----------------
The view does not depend on the concrete backend class.
It only requires an object satisfying:

    response.text: str
    response.translation_lines: tuple[str, ...]

Typical lifecycle:

    result_view = ResultView()

    # new LLM response:
    result_view.display(response)

    # or explicitly:
    result_view.set_result(response)
    result_view.show_result()

    # hide without destroying:
    result_view.hide_result()

ResultView maintains one persistent content widget and replaces only the
rendered translation rows.

Interaction:
    - passive mode remains visible;
    - passive mode is click-through so selection underneath remains possible;
    - passive mode uses transparent background + white text;
    - interactive mode restores visible background + dark text;
    - header controls become available in interactive mode;
    - window supports move/resize interaction;
    - window does not deliberately steal keyboard focus.

Public interaction state:
    Language:
        ENGLISH / VIETNAMESE

    ViewMode:
        SINGLE / BILINGUAL

Public methods commonly used by controller:
    display(response)
    set_result(response)
    show_result()
    hide_result()
    clear()

Local controls:
    toggle_language()
    toggle_view_mode()

IMPORTANT:
    ResultView has a Linux/XWayland/XCB requirement documented above.
    Keep that requirement intact.


==============================================================================
3. SELECTION INDICATOR
==============================================================================

Public class:
    SelectionIndicator

This is a PURE VISUAL MODULE.

It knows nothing about:
    - Selection;
    - processing;
    - Dictionary;
    - LLM;
    - Dashboard;
    - ResultView;
    - controller timing;
    - placement policy.

It only renders:
    - a small rounded floating capsule;
    - animated spinner;
    - status text such as "running…".

The controller decides:
    - when it starts;
    - when it stops;
    - when it is visible;
    - where it is positioned;
    - what status text it displays.

Typical lifecycle:

    indicator = SelectionIndicator()

    indicator.move_to(global_position)
    indicator.show_indicator()
    indicator.start()

    # later:
    indicator.stop()
    indicator.hide_indicator()

Equivalent lower-level Qt calls are intentionally not required by the
controller.

Important:
    - show/hide timing does NOT belong here;
    - minimum display duration does NOT belong here;
    - backend progress logic does NOT belong here;
    - placement logic does NOT belong here;
    - it is mouse-transparent and must not block the application underneath;
    - it does not take keyboard focus.

Optional text:

    indicator.set_text("running…")

The default text is:
    "running…"

The spinner is drawn with QPainter and animated with QTimer; it has no GIF or
external image-resource dependency.


==============================================================================
LIFECYCLE / OWNERSHIP RULE
==============================================================================

Recommended application ownership:

    Controller / Application State
            │
            ├── one persistent Dashboard
            │
            ├── one persistent ResultView
            │
            └── one persistent SelectionIndicator

The controller owns:
    - the instances;
    - visibility decisions;
    - placement decisions;
    - backend-to-view conversion;
    - application state transitions.

Each view owns:
    - its own widgets;
    - its own local interaction state;
    - its own rendering.


==============================================================================
DO NOT CONFUSE THESE THREE
==============================================================================

Dashboard
    = dictionary content

ResultView
    = LLM translation content

SelectionIndicator
    = processing status only

SelectionIndicator is NOT a progress-bar for backend percentage.
It is only a visual "the application is currently processing" signal.


==============================================================================
REFactoring RULE
==============================================================================

When modifying this package:

1. Keep each view independent.
2. Do not import backend / processor / controller classes into views.
3. Keep public APIs stable unless the controller contract is intentionally
   changed.
4. Update this file whenever a public view, public method, or architectural
   contract changes.
5. Preserve the Linux Wayland/XCB requirement until the floating-window
   architecture is deliberately migrated and re-tested.
"""

from __future__ import annotations

# ============================================================================
# IMPORTANT: SET THE QT PLATFORM BEFORE IMPORTING ANY VIEW MODULE
# ============================================================================
#
# Python executes this package's __init__.py before importing:
#
#     frontend.ui.views.dashboard
#     frontend.ui.views.result_view
#     frontend.ui.views.selection_indicator
#
# Therefore the XCB safeguard must live here BEFORE those modules import
# PyQt6. This prevents a package-level import from accidentally initializing
# Qt with the Wayland backend first.
#
# This mirrors the platform requirement documented in ResultView.
# ============================================================================

import os
import sys


if (
    sys.platform.startswith("linux")
    and os.environ.get("XDG_SESSION_TYPE", "").lower() == "wayland"
    and os.environ.get("DISPLAY")
):
    os.environ["QT_QPA_PLATFORM"] = "xcb"


# ============================================================================
# PUBLIC VIEW EXPORTS
# ============================================================================
#
# Import only public classes here.
# Private implementation classes remain private to their own modules.
# ============================================================================

from .dashboard import Dashboard, DashboardEntryView
from .result_view import (
    Language,
    ResultView,
    TranslationResponseProtocol,
    ViewMode,
)
from .selection_indicator import SelectionIndicator


__all__ = [
    "Dashboard",
    "DashboardEntryView",
    "Language",
    "ResultView",
    "TranslationResponseProtocol",
    "ViewMode",
    "SelectionIndicator",
]
