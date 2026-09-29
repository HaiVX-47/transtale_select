from __future__ import annotations

import argparse
from backend.schema import DashboardResponse, LLMTranslationResponse

from .processor import InteractionProcessor, ProcessingMode
from .selection.result import SelectionResult


def _print_llm_response(response: LLMTranslationResponse) -> None:
    print("\n" + "=" * 72)
    print("LLM RESPONSE")
    print("=" * 72)
    print(f"Source: {response.text}")
    print("Translation:")

    for index, line in enumerate(response.translation_lines):
        print(f"  [{index}] {line}")

    print("=" * 72)


def _print_dictionary_response(response: DashboardResponse) -> None:
    print("\n" + "=" * 72)
    print("DICTIONARY RESPONSE")
    print("=" * 72)

    for index, entry in enumerate(response.entries):
        print(f"[{index}] {entry.term} ({entry.entry_type})")

        if entry.ipas:
            print("  IPA:")
            for ipa in entry.ipas:
                print(f"    {ipa}")

        print("  Meanings:")
        for meaning in entry.meanings:
            print(f"    {meaning}")

    print("=" * 72)


def _on_result(response: LLMTranslationResponse | DashboardResponse) -> None:
    """Print exactly what the backend returns."""
    if isinstance(response, LLMTranslationResponse):
        _print_llm_response(response)
        return

    if isinstance(response, DashboardResponse):
        _print_dictionary_response(response)
        return

    raise TypeError(
        "Unexpected backend response type: "
        f"{type(response).__name__}"
    )


def _on_error(error: Exception, selection_result: SelectionResult) -> None:
    print("\n" + "!" * 72)
    print("PROCESSING ERROR")
    print("!" * 72)
    print(f"Selection: {selection_result.text!r}")
    print(f"Error: {type(error).__name__}: {error}")
    print("!" * 72)


def _parse_mode() -> ProcessingMode:
    parser = argparse.ArgumentParser(
        description="Live integration test: Selection -> Backend."
    )
    parser.add_argument(
        "--mode",
        choices=tuple(mode.value for mode in ProcessingMode),
        default=ProcessingMode.BOTH.value,
        help="Backend processing mode. Default: both.",
    )
    args = parser.parse_args()
    return ProcessingMode(args.mode)


def main() -> None:
    mode = _parse_mode()
    processor = InteractionProcessor(mode=mode)
    print("=" * 72)
    print("INTERACTION INTEGRATION TEST")
    print("=" * 72)
    print(f"Mode: {mode.value}")
    print("The processor is now waiting for Selection results.")
    print("Select text on the desktop and keep the selection stable.")
    print("Press Enter in this terminal to stop the test.")
    print("=" * 72)

    try:
        processor.start(
            callback=_on_result,
            error_callback=_on_error,
        )
        input()
    except KeyboardInterrupt:
        print("\nStopping test...")
    finally:
        processor.stop()
        print("Integration test stopped.")


if __name__ == "__main__":
    main()
