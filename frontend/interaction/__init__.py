"""
frontend.interaction
====================

Application-facing interaction layer.

MAIN RESPONSIBILITY
-------------------

The central public class of this package is:

    InteractionProcessor

Its job is to connect the desktop Selection subsystem to the backend
TranslationPipeline:

    SelectionResult
        ->
    TranslationRequest
        ->
    backend TranslationPipeline
        ->
    DashboardResponse / LLMTranslationResponse

The processor is intentionally a thin orchestration layer.

It does NOT:
    - implement Selection logic;
    - implement Dictionary lookup;
    - implement LLM inference;
    - render UI;
    - update Dashboard / ResultView / SelectionIndicator;
    - own the application state machine.

State / Controller is responsible for deciding what the backend result means
for the application and which UI should react.


==============================================================================
MOST IMPORTANT: HOW TO USE InteractionProcessor
==============================================================================

Typical automatic application flow:

    from frontend.interaction import (
        InteractionProcessor,
        ProcessingMode,
    )

    processor = InteractionProcessor(
        mode=ProcessingMode.DICTIONARY,
    )

    def on_result(response):
        # Backend response received.
        # Forward it to the controller/state layer.
        print(response)

    def on_error(error, selection_result):
        print(
            "Processing failed:",
            error,
            "selection=",
            selection_result.text,
        )

    processor.start(
        callback=on_result,
        error_callback=on_error,
    )

    # ... application continues running ...

    processor.stop()


The intended flow is:

    user selects text
            |
            v
        Selection
            |
            v
    InteractionProcessor
            |
            v
       Backend pipeline
            |
            v
         callback
            |
            v
    Controller / State / UI


==============================================================================
1. CONSTRUCTION
==============================================================================

    processor = InteractionProcessor(
        mode=ProcessingMode.LLM,
        max_workers=2,
    )

Arguments
---------

mode
----
Selects which backend workflow the interaction layer exposes.

    ProcessingMode.LLM
        -> only LLM jobs

    ProcessingMode.DICTIONARY
        -> only Dictionary jobs

    ProcessingMode.BOTH
        -> Dictionary and LLM are submitted as two independent jobs

Default:

    ProcessingMode.LLM


max_workers
-----------
Number of worker threads used by the backend executor.

Default:

    2

For BOTH mode:

    max_workers >= 2

is required because Dictionary and LLM are intentionally independent jobs.


Optional Selection injection
----------------------------

The processor normally creates its own Selection:

    processor = InteractionProcessor(
        mode=ProcessingMode.DICTIONARY,
    )

A custom Selection can be injected when the controller/application needs to
own the Selection object:

    processor = InteractionProcessor(
        selection=selection,
        mode=ProcessingMode.DICTIONARY,
    )

This keeps Selection ownership explicit without coupling the processor to a
specific Selection implementation.


==============================================================================
2. AUTOMATIC MODE: start(...)
==============================================================================

Use:

    processor.start(
        callback=on_result,
        error_callback=on_error,
    )

`callback` is required.

`error_callback` is optional.

After start():

    Selection
        ->
    finalized SelectionResult
        ->
    processor
        ->
    worker thread
        ->
    backend
        ->
    callback(response)


IMPORTANT THREAD RULE
---------------------

The processing callback is not a Qt GUI-thread callback.

The Selection subsystem invokes its processor callback from its worker
thread, and InteractionProcessor also performs backend work in executor
workers.

Therefore:

    DO NOT

        callback(response):
            dashboard.set_entries(...)
            result_view.display(...)

directly from the processing callback.

Instead use a thread-safe bridge:

    worker callback
        ->
    Qt signal
        ->
    queued GUI-thread slot
        ->
    Dashboard / ResultView / SelectionIndicator


Example conceptual structure:

    processor.start(
        callback=controller.on_backend_result,
        error_callback=controller.on_backend_error,
    )

Then the controller owns the Qt-thread boundary.


==============================================================================
3. stop()
==============================================================================

Stop the automatic workflow with:

    processor.stop()

This:

    1. marks the processor as not running;
    2. stops Selection;
    3. cancels pending futures where possible;
    4. shuts down the worker executor.

The controller should normally call stop() during application shutdown or when
the user presses Stop.


==============================================================================
4. is_running AND mode
==============================================================================

Read the current processor state:

    processor.is_running

Read the configured mode:

    processor.mode

Example:

    if processor.is_running:
        print("processor is active")

    if processor.mode is ProcessingMode.DICTIONARY:
        ...


These properties describe processor-local state. They are not a replacement
for the application's global State machine.


==============================================================================
5. process(...)
==============================================================================

The processor also exposes synchronous processing for one already-finalized
SelectionResult:

    response = processor.process(
        selection_result
    )

This bypasses the automatic Selection -> Backend loop.

Return value:

    ProcessingMode.LLM
        -> LLMTranslationResponse

    ProcessingMode.DICTIONARY
        -> DashboardResponse

    ProcessingMode.BOTH
        -> tuple[
               BackendResponse,
               BackendResponse,
           ]

In BOTH mode, synchronous `process(...)` executes the Dictionary path and LLM
path sequentially.

Automatic mode is different: BOTH submits them as independent worker jobs.


==============================================================================
6. BACKEND RESPONSE TYPES
==============================================================================

The processor does not invent a new response schema.

Public backend response types are:

    BackendResponse
        = LLMTranslationResponse | DashboardResponse

LLM mode:
    -> LLMTranslationResponse

Dictionary mode:
    -> DashboardResponse

The controller should inspect the concrete response type and route it to the
appropriate view.

Conceptually:

    DashboardResponse
        -> Dashboard

    LLMTranslationResponse
        -> ResultView


==============================================================================
7. ERROR HANDLING
==============================================================================

Use:

    error_callback(error, selection_result)

The callback receives:

    error
        Exception raised during processing.

    selection_result
        SelectionResult that caused the backend operation.

Example:

    def on_error(error, selection_result):
        print(
            f"Failed for {selection_result.text!r}: "
            f"{type(error).__name__}: {error}"
        )

Errors from a backend worker are published through `error_callback` when one
is registered.

Consumer callback failures are isolated from the worker so a UI/consumer
exception does not directly terminate the processing worker.


==============================================================================
8. PROCESSING MODES
==============================================================================

LLM
---

    ProcessingMode.LLM

Flow:

    SelectionResult
        ->
    TranslationRequest(mode=LLM)
        ->
    LLM pipeline
        ->
    LLMTranslationResponse


DICTIONARY
----------

    ProcessingMode.DICTIONARY

Flow:

    SelectionResult
        ->
    TranslationRequest(mode=DICTIONARY)
        ->
    Dictionary pipeline
        ->
    DashboardResponse


BOTH
----

    ProcessingMode.BOTH

Automatic mode:

    SelectionResult
        |
        +----> Dictionary worker
        |
        +----> LLM worker

Each path publishes its own native backend response.

IMPORTANT:
    Do not use the backend PipelineMode.BOTH from this interaction layer.

The interaction layer intentionally creates independent Dictionary and LLM
pipelines because it needs both native responses separately.


==============================================================================
9. UI RESPONSIBILITY
==============================================================================

InteractionProcessor has NO knowledge of:

    Dashboard
    ResultView
    SelectionIndicator
    ControlPanel

The controller/state layer should connect them conceptually:

    InteractionProcessor
            |
            +--> processing starts
            |       |
            |       +--> SelectionIndicator.start()
            |
            +--> backend result
            |       |
            |       +--> Dashboard.set_entries(...)
            |       +--> ResultView.display(...)
            |
            +--> processing finishes
                    |
                    +--> SelectionIndicator.stop()


This is intentionally outside the processor.


==============================================================================
10. VALIDATION BOUNDARY
==============================================================================

`process(...)` and the internal automatic path require a real:

    SelectionResult

The processor rejects an unexpected object type and rejects empty / whitespace
only selection text before sending a backend request.

The processor also verifies that each pipeline returns the response type
expected for that mode.


==============================================================================
11. OWNERSHIP MODEL
==============================================================================

Recommended architecture:

    State / Controller
            |
            +--> owns InteractionProcessor
            |
            +--> owns / coordinates UI views
            |
            +--> reacts to processor callbacks
            |
            +--> performs Qt queued dispatch when necessary

    InteractionProcessor
            |
            +--> owns processing executor
            +--> owns or receives Selection
            +--> owns backend pipeline instances
            +--> publishes backend results

    Selection
            |
            +--> detects / stabilizes desktop selection


The processor should remain unaware of the higher-level controller and UI
architecture.


==============================================================================
12. WHAT NOT TO PUT INTO InteractionProcessor
==============================================================================

Do not add:

    - Dashboard references;
    - ResultView references;
    - SelectionIndicator references;
    - ControlPanel logic;
    - Qt widgets;
    - application State machine;
    - UI positioning;
    - UI animation;
    - backend implementation details.

For example, this is architecturally wrong:

    class InteractionProcessor:
        ...
        self.dashboard.set_entries(...)

Instead:

    processor callback
        ->
    Controller / State
        ->
    Qt GUI-thread dispatch
        ->
    Dashboard


==============================================================================
PUBLIC API
==============================================================================

Preferred imports:

    from frontend.interaction import (
        BackendResponse,
        ResultCallback,
        ErrorCallback,
        ProcessingMode,
        InteractionProcessor,
    )


The package also keeps the existing Selection / Permission exports available
for compatibility with the current frontend.interaction package layout.

Those subsystems are not documented in detail here; their own package
contracts remain the source of truth.


==============================================================================
REFACTORING RULE
==============================================================================

When modifying InteractionProcessor:

    1. Keep the processor thin.
    2. Keep Selection independent from backend implementation.
    3. Keep UI completely outside the processor.
    4. Preserve the callback thread contract.
    5. Preserve the distinction between synchronous `process(...)` and
       automatic `start(...)`.
    6. Preserve independent Dictionary / LLM execution in BOTH mode.
    7. Update this __init__.py whenever the public processor API or lifecycle
       changes.
"""


from __future__ import annotations


# ============================================================================
# PROCESSOR PUBLIC API
# ============================================================================

from .processor import (
    BackendResponse,
    ErrorCallback,
    InteractionProcessor,
    ProcessingMode,
    ResultCallback,
)


# ============================================================================
# EXISTING PUBLIC SUBSYSTEM EXPORTS
# ============================================================================
#
# Keep these exports for compatibility with the package structure already used
# by the project. The main contract documented above is InteractionProcessor.
# ============================================================================

from .permission import (
    AccessibilityPermission,
    AccessibilityCapability,
    PrimarySelectionPermission,
    PrimarySelectionCapability,
    PermissionManager,
    Capabilities,
    EnvironmentDetector,
)

from .selection import (
    Selection,
    SelectionResult,
    SelectionRect,
    SelectionLine,
    SelectionWord,
)


__all__ = [
    # Processor
    "BackendResponse",
    "ResultCallback",
    "ErrorCallback",
    "ProcessingMode",
    "InteractionProcessor",

    # Permission
    "AccessibilityPermission",
    "AccessibilityCapability",
    "PrimarySelectionPermission",
    "PrimarySelectionCapability",
    "PermissionManager",
    "Capabilities",
    "EnvironmentDetector",

    # Selection
    "Selection",
    "SelectionResult",
    "SelectionRect",
    "SelectionLine",
    "SelectionWord",
]
