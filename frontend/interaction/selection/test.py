from __future__ import annotations

import sys
import time
import threading
from pathlib import Path
from functools import wraps


# ============================================================================
# PROJECT ROOT
# ============================================================================

# test_selection_timing.py
#   -> frontend/
#       -> selection/
#
# Project root = parents[2]
PROJECT_ROOT = Path(__file__).resolve().parents[2]

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


# ============================================================================
# GLOBAL TIMING
# ============================================================================

PROGRAM_START = time.perf_counter()

_print_lock = threading.Lock()


def now() -> float:
    return time.perf_counter()


def elapsed() -> float:
    return now() - PROGRAM_START


def log(message: str) -> None:
    """
    Thread-safe console output.
    """
    with _print_lock:
        print(f"[{elapsed():9.3f}s] {message}", flush=True)


def ms(seconds: float) -> str:
    return f"{seconds * 1000:10.2f} ms"


# ============================================================================
# IMPORT
# ============================================================================

import_start = now()

from frontend.interaction.selection import Selection, SelectionResult
from frontend.interaction.selection.resolver import SelectionResolver

import_end = now()

log(f"IMPORT frontend.selection      : {ms(import_end - import_start)}")


# ============================================================================
# PROFILING STATE
# ============================================================================

class Profiler:
    def __init__(self) -> None:
        self.lock = threading.RLock()

        # Number of resolver calls.
        self.resolve_count = 0

        # Current identity according to the REAL Selection._build_identity().
        self.last_identity: str | None = None

        # When the current identity was first observed.
        self.identity_first_seen: float | None = None

        # Keep last text separately.
        self.last_text: str | None = None

        # Statistics.
        self.resolve_times: list[float] = []
        self.atspi_extract_times: list[float] = []
        self.fragment_times: list[float] = []

        self.callback_count = 0

        # Run timing.
        self.selection_start_time: float | None = None

    def reset_identity(self) -> None:
        with self.lock:
            self.last_identity = None
            self.identity_first_seen = None
            self.last_text = None

    def record_result(self, result: SelectionResult | None) -> None:
        """
        Track the exact identity logic currently used by Selection.
        """

        if result is None:
            return

        try:
            identity = Selection._build_identity(result)
        except Exception as exc:
            log(f"[IDENTITY ERROR] {exc}")
            return

        with self.lock:
            self.last_text = result.text

            if identity != self.last_identity:
                previous_identity = self.last_identity

                self.last_identity = identity
                self.identity_first_seen = now()

                if previous_identity is None:
                    log(
                        "[IDENTITY] first observed | "
                        f"source={result.source!r} "
                        f"text={result.text!r}"
                    )
                else:
                    log(
                        "[IDENTITY] CHANGED        | "
                        f"source={result.source!r} "
                        f"text={result.text!r}"
                    )

                    # Show geometry because geometry is part of
                    # Selection._build_identity().
                    if result.bounds is not None:
                        b = result.bounds
                        log(
                            "[IDENTITY] bounds         | "
                            f"x={b.x:.2f}, y={b.y:.2f}, "
                            f"w={b.width:.2f}, h={b.height:.2f}"
                        )

    def print_summary(self) -> None:
        with self.lock:
            log("")
            log("=" * 78)
            log("FINAL PROFILING SUMMARY")
            log("=" * 78)

            log(f"Resolver calls       : {self.resolve_count}")
            log(f"Callbacks            : {self.callback_count}")

            if self.resolve_times:
                total = sum(self.resolve_times)
                avg = total / len(self.resolve_times)

                log(
                    f"resolve() avg        : {ms(avg)}"
                )
                log(
                    f"resolve() max        : {ms(max(self.resolve_times))}"
                )
                log(
                    f"resolve() total      : {ms(total)}"
                )

            if self.atspi_extract_times:
                total = sum(self.atspi_extract_times)
                avg = total / len(self.atspi_extract_times)

                log(
                    f"AT-SPI extract avg   : {ms(avg)}"
                )
                log(
                    f"AT-SPI extract max   : {ms(max(self.atspi_extract_times))}"
                )
                log(
                    f"AT-SPI extract total : {ms(total)}"
                )

            if self.fragment_times:
                total = sum(self.fragment_times)
                avg = total / len(self.fragment_times)

                log(
                    f"fragments avg        : {ms(avg)}"
                )
                log(
                    f"fragments max        : {ms(max(self.fragment_times))}"
                )
                log(
                    f"fragments total      : {ms(total)}"
                )

            log("=" * 78)


profiler = Profiler()


# ============================================================================
# GENERIC INSTANCE METHOD WRAPPER
# ============================================================================

def timed_method(
    obj,
    method_name: str,
    label: str,
    *,
    only_if_positive: bool = False,
):
    """
    Wrap ONE INSTANCE METHOD only.

    This does NOT modify the source module or class definition.
    """

    original = getattr(obj, method_name)

    @wraps(original)
    def wrapper(*args, **kwargs):
        start = now()

        try:
            return original(*args, **kwargs)

        finally:
            duration = now() - start

            if only_if_positive and duration <= 0:
                return

            log(f"[TIMING] {label:<32} {ms(duration)}")

    setattr(obj, method_name, wrapper)


# ============================================================================
# SPECIAL WRAPPERS
# ============================================================================

def wrap_atspi_provider(provider) -> None:
    """
    Instrument the existing AtspiProvider instance.
    """

    # ------------------------------------------------------------------------
    # Top-level extract()
    # ------------------------------------------------------------------------

    original_extract = provider.extract

    @wraps(original_extract)
    def extract_wrapper(*args, **kwargs):
        start = now()

        try:
            return original_extract(*args, **kwargs)

        finally:
            duration = now() - start

            profiler.atspi_extract_times.append(duration)

            log(
                f"[AT-SPI] extract()             {ms(duration)}"
            )

    provider.extract = extract_wrapper

    # ------------------------------------------------------------------------
    # _build_result()
    # ------------------------------------------------------------------------

    original_build_result = provider._build_result

    @wraps(original_build_result)
    def build_result_wrapper(*args, **kwargs):
        start = now()

        try:
            return original_build_result(*args, **kwargs)

        finally:
            duration = now() - start

            log(
                f"[AT-SPI] _build_result()        {ms(duration)}"
            )

    provider._build_result = build_result_wrapper

    # ------------------------------------------------------------------------
    # _extract_visual_fragments()
    # ------------------------------------------------------------------------

    original_fragments = provider._extract_visual_fragments

    @wraps(original_fragments)
    def fragments_wrapper(*args, **kwargs):
        start = now()

        try:
            return original_fragments(*args, **kwargs)

        finally:
            duration = now() - start

            profiler.fragment_times.append(duration)

            log(
                f"[AT-SPI] visual_fragments()     {ms(duration)}"
            )

    provider._extract_visual_fragments = fragments_wrapper

    # ------------------------------------------------------------------------
    # _find_focused_node()
    #
    # This function is recursive.
    # We measure the whole outer invocation instead of printing every node.
    # ------------------------------------------------------------------------

    original_focused = provider._find_focused_node

    focused_depth = threading.local()
    focused_depth.value = 0

    @wraps(original_focused)
    def focused_wrapper(*args, **kwargs):
        depth = getattr(focused_depth, "value", 0)

        focused_depth.value = depth + 1

        if depth == 0:
            start = now()
        else:
            start = None

        try:
            return original_focused(*args, **kwargs)

        finally:
            focused_depth.value = depth

            if depth == 0 and start is not None:
                duration = now() - start

                log(
                    f"[AT-SPI] _find_focused_node()    {ms(duration)}"
                )

    provider._find_focused_node = focused_wrapper

    # ------------------------------------------------------------------------
    # _find_selection_in_subtree()
    #
    # Also recursive.
    # ------------------------------------------------------------------------

    original_subtree = provider._find_selection_in_subtree

    subtree_depth = threading.local()
    subtree_depth.value = 0

    @wraps(original_subtree)
    def subtree_wrapper(*args, **kwargs):
        depth = getattr(subtree_depth, "value", 0)

        subtree_depth.value = depth + 1

        if depth == 0:
            start = now()
        else:
            start = None

        try:
            return original_subtree(*args, **kwargs)

        finally:
            subtree_depth.value = depth

            if depth == 0 and start is not None:
                duration = now() - start

                log(
                    f"[AT-SPI] _find_selection_tree() {ms(duration)}"
                )

    provider._find_selection_in_subtree = subtree_wrapper


def wrap_primary_provider(provider) -> None:
    """
    Instrument PrimarySelectionProvider.extract().
    """

    original_extract = provider.extract

    @wraps(original_extract)
    def extract_wrapper(*args, **kwargs):
        start = now()

        try:
            return original_extract(*args, **kwargs)

        finally:
            duration = now() - start

            log(
                f"[PRIMARY] extract()             {ms(duration)}"
            )

    provider.extract = extract_wrapper


# ============================================================================
# CREATE RESOLVER
# ============================================================================

log("")
log("=" * 78)
log("INITIALIZATION")
log("=" * 78)

resolver_start = now()

resolver = SelectionResolver()

resolver_end = now()

log(
    f"SelectionResolver()               : "
    f"{ms(resolver_end - resolver_start)}"
)

log("")
log("Providers:")

for index, provider in enumerate(resolver.providers, start=1):
    try:
        available = provider.is_available()
    except Exception as exc:
        available = False
        log(
            f"  {index}. {type(provider).__name__:<28} "
            f"ERROR: {exc}"
        )
        continue

    log(
        f"  {index}. {type(provider).__name__:<28} "
        f"available={available}"
    )

# Instrument the ACTUAL provider instances created by resolver.
for provider in resolver.providers:
    provider_name = type(provider).__name__

    if provider_name == "AtspiProvider":
        wrap_atspi_provider(provider)

    elif provider_name == "PrimarySelectionProvider":
        wrap_primary_provider(provider)


# ============================================================================
# WRAP RESOLVER
# ============================================================================

original_resolve = resolver.resolve


@wraps(original_resolve)
def profiled_resolve(*args, **kwargs):
    call_start = now()

    profiler.resolve_count += 1

    try:
        result = original_resolve(*args, **kwargs)

        return result

    finally:
        duration = now() - call_start

        profiler.resolve_times.append(duration)

        # Re-run resolve is NOT done here.
        # We use the result already returned by original_resolve().

        try:
            result
        except UnboundLocalError:
            result = None

        if result is None:
            log(
                f"[RESOLVE #{profiler.resolve_count:04d}] "
                f"{ms(duration)} | result=None"
            )
        else:
            text_preview = result.text.replace("\n", "\\n")

            if len(text_preview) > 100:
                text_preview = text_preview[:100] + "..."

            log(
                f"[RESOLVE #{profiler.resolve_count:04d}] "
                f"{ms(duration)} | "
                f"source={result.source} | "
                f"text={text_preview!r}"
            )

            profiler.record_result(result)


resolver.resolve = profiled_resolve


# ============================================================================
# CREATE SELECTION
# ============================================================================

selection_create_start = now()

selection = Selection(
    stable_delay=1.0,
    poll_interval=0.2,
    max_stack_size=None,
    resolver=resolver,
)

selection_create_end = now()

log(
    f"Selection()                       : "
    f"{ms(selection_create_end - selection_create_start)}"
)


# ============================================================================
# PROFILE _check_selection()
#
# This measures the entire Selection cycle:
#
#     _check_selection()
#         -> resolver.resolve()
#         -> identity
#         -> stabilization
#         -> finalize
#
# ============================================================================
original_check_selection = selection._check_selection


@wraps(original_check_selection)
def profiled_check_selection(*args, **kwargs):
    start = now()

    try:
        return original_check_selection(*args, **kwargs)

    finally:
        duration = now() - start

        log(
            f"[SELECTION] _check_selection()  {ms(duration)}"
        )


selection._check_selection = profiled_check_selection


# ============================================================================
# CALLBACK
# ============================================================================

def on_selection(result: SelectionResult) -> None:
    callback_time = now()

    profiler.callback_count += 1

    text = result.text

    with profiler.lock:
        first_seen = profiler.identity_first_seen

    log("")
    log("=" * 78)
    log(">>> SELECTION RECEIVED")
    log("=" * 78)

    log(
        f"Callback total from program start : "
        f"{ms(callback_time - PROGRAM_START)}"
    )

    if first_seen is not None:
        log(
            f"Current identity -> callback     : "
            f"{ms(callback_time - first_seen)}"
        )

    log(
        f"source                            : "
        f"{result.source}"
    )

    log(
        f"text length                       : "
        f"{len(text)}"
    )

    log(
        f"text                              : "
        f"{text!r}"
    )

    log(
        f"line count                        : "
        f"{len(result.lines)}"
    )

    log(
        f"has geometry                      : "
        f"{result.has_geometry}"
    )

    if result.bounds is not None:
        b = result.bounds

        log(
            "bounds                            : "
            f"x={b.x:.2f}, "
            f"y={b.y:.2f}, "
            f"w={b.width:.2f}, "
            f"h={b.height:.2f}"
        )
    else:
        log("bounds                            : None")

    log("=" * 78)
    log("")


# ============================================================================
# START
# ============================================================================

log("")
log("=" * 78)
log("START SELECTION MONITOR")
log("=" * 78)

start_call_start = now()

selection.start(callback=on_selection)

start_call_end = now()

log(
    f"selection.start()                 : "
    f"{ms(start_call_end - start_call_start)}"
)

log("")
log("Now highlight text normally.")
log("Do NOT press Ctrl+C.")
log("Do NOT paste.")
log("Just highlight text and wait.")
log("")
log("Keep this terminal open and watch:")
log("")
log("  [AT-SPI] visual_fragments()")
log("  [AT-SPI] _find_focused_node()")
log("  [AT-SPI] _find_selection_tree()")
log("  [AT-SPI] extract()")
log("  [RESOLVE]")
log("  [IDENTITY]")
log("  [SELECTION] _check_selection()")
log("  >>> SELECTION RECEIVED")
log("")
log("Press Ctrl+C to stop.")
log("")


# ============================================================================
# KEEP PROCESS ALIVE
# ============================================================================

try:
    while True:
        time.sleep(1.0)

except KeyboardInterrupt:
    log("")
    log("Ctrl+C received. Stopping selection...")

finally:
    stop_start = now()

    try:
        selection.stop()
    except Exception as exc:
        log(f"selection.stop() ERROR: {exc}")

    stop_end = now()

    log(
        f"selection.stop()                  : "
        f"{ms(stop_end - stop_start)}"
    )

    profiler.print_summary()

    log("")
    log("Done.")