"""
Preprocessing for LLM translation.

Only two substitutions are performed:

    "-" -> " "
    "_" -> " "

All other punctuation is preserved.

Examples
--------
    "state-of-the-art"
        -> "state of the art"

    "machine_learning"
        -> "machine learning"

    "Hello, world!"
        -> "Hello, world!"

    "What_is_this?"
        -> "What is this?"

The module does not:
    - lowercase;
    - lemmatize;
    - remove punctuation;
    - remove stopwords;
    - tokenize into words;
    - modify commas, periods, question marks, exclamation marks, etc.
"""

from __future__ import annotations

import re


class LanguagePreprocessorError(ValueError):
    """Invalid LLM preprocessing input."""


class LanguageTextPreprocessor:
    """Minimal text normalization before MarianMT inference."""

    WHITESPACE_PATTERN = re.compile(r"\s+")

    def preprocess(self, text: str) -> str:
        if not isinstance(text, str):
            raise LanguagePreprocessorError(
                "text must be a string, "
                f"received {type(text).__name__}."
            )

        if "\x00" in text:
            raise LanguagePreprocessorError(
                "text must not contain null characters."
            )

        # Only these two characters are structurally replaced.
        value = text.replace("-", " ")
        value = value.replace("_", " ")

        # Collapse whitespace created by the replacements and by
        # the original input. Punctuation itself is untouched.
        value = self.WHITESPACE_PATTERN.sub(" ", value)

        return value.strip()


__all__ = [
    "LanguagePreprocessorError",
    "LanguageTextPreprocessor",
]
