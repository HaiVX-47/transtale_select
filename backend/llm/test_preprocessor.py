"""
Small preprocessing test for the LLM subsystem.

Run from project root:

    python -m backend.llm.test_preprocessor

This test does not load MarianMT.
"""

from .preprocessor import LanguageTextPreprocessor


def main() -> None:
    preprocessor = LanguageTextPreprocessor()

    text = "state-of-the-art_model, is it ready?"
    processed = preprocessor.preprocess(text)

    print("INPUT :")
    print(text)
    print()
    print("OUTPUT:")
    print(processed)


if __name__ == "__main__":
    main()
