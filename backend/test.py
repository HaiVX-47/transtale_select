"""
backend/test.py

End-to-end test for the backend translation pipeline.

Flow
----
TranslationRequest
        ↓
TranslationPipeline
        ↓
LLM module
        ↓
LLMTranslationResponse

This test does not:
- create or modify the dictionary;
- create fake translation results;
- mock the LLM;
- call internal preprocessing functions directly.

It tests the backend through its public interface.
"""

from __future__ import annotations

from .pipeline import (
    PipelineError,
    PipelineMode,
    TranslationPipeline,
    create_pipeline,
)
from .schema import (
    LLMTranslationResponse,
    TranslationMode,
    TranslationRequest,
)


def main() -> None:
    # --------------------------------------------------------------
    # 1. Create input according to schema.py
    # --------------------------------------------------------------

    request = TranslationRequest(
        text=(
            "This is a sophisticated language translation system.\n"
            "It is designed to process multiple lines independently."
        ),
        mode=TranslationMode.LLM,
    )

    print("=" * 60)
    print("INPUT")
    print("=" * 60)
    print(request.model_dump())

    print("\n" + "=" * 60)
    print("INPUT LINES")
    print("=" * 60)

    for index, line in enumerate(
        request.text.splitlines(),
        start=1,
    ):
        print(f"[{index}] {line}")

    # --------------------------------------------------------------
    # 2. Create the backend pipeline
    # --------------------------------------------------------------

    # LLM only.
    pipeline: TranslationPipeline = create_pipeline(
        mode=PipelineMode.LLM,
    )

    # --------------------------------------------------------------
    # 3. Run the complete backend
    # --------------------------------------------------------------

    try:
        response = pipeline(request)

    except PipelineError as exc:
        print("\n" + "=" * 60)
        print("PIPELINE ERROR")
        print("=" * 60)
        print(f"{type(exc).__name__}: {exc}")
        raise

    # --------------------------------------------------------------
    # 4. Validate response type
    # --------------------------------------------------------------

    if not isinstance(response, LLMTranslationResponse):
        raise TypeError(
            "Expected LLMTranslationResponse, "
            f"received {type(response).__name__}."
        )

    # --------------------------------------------------------------
    # 5. Print complete backend output
    # --------------------------------------------------------------

    print("\n" + "=" * 60)
    print("OUTPUT")
    print("=" * 60)
    print(response.model_dump())

    # --------------------------------------------------------------
    # 6. Print translations line by line
    # --------------------------------------------------------------

    print("\n" + "=" * 60)
    print("TRANSLATION LINES")
    print("=" * 60)

    for index, line in enumerate(
        response.translation_lines,
        start=1,
    ):
        print(f"[{index}] {line}")

    # --------------------------------------------------------------
    # 7. Verify source/output line mapping
    # --------------------------------------------------------------

    source_lines = request.text.split("\n")
    translated_lines = response.translation_lines

    if len(source_lines) != len(translated_lines):
        raise AssertionError(
            "Source and translated line counts do not match: "
            f"{len(source_lines)} != {len(translated_lines)}"
        )

    print("\n" + "=" * 60)
    print("LINE MAPPING")
    print("=" * 60)

    for index, (source, translated) in enumerate(
        zip(source_lines, translated_lines),
        start=1,
    ):
        print(f"[{index}]")
        print(f"  SOURCE     : {source}")
        print(f"  TRANSLATION: {translated}")

    print("\n" + "=" * 60)
    print("TEST PASSED")
    print("=" * 60)


if __name__ == "__main__":
    main()
