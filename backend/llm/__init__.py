"""
LLM translation subsystem.

Public boundary
---------------

    TranslationRequest
        ->
    LanguageTranslator
        ->
    TranslationResponse

The subsystem contains:
    - config.py: model/device/generation configuration;
    - preprocessor.py: minimal `_` / `-` preprocessing;
    - model.py: MarianMT runtime.

The module does not depend on the dictionary subsystem.
It does not use DictionaryTokenizer, PhraseTrie, dictionary resources,
stopword removal, lemmatization, or word-level lookup.
"""

from .config import (
    DEFAULT_LANGUAGE_TRANSLATION_CONFIG,
    LanguageTranslationConfig,
)
from .model import (
    LanguageModelLoadError,
    LanguageTranslationError,
    LanguageTranslationInferenceError,
    LanguageTranslationInputError,
    LanguageTranslator,
)
from .preprocessor import (
    LanguagePreprocessorError,
    LanguageTextPreprocessor,
)

__all__ = [
    "LanguageTranslator",
    "LanguageTranslationConfig",
    "DEFAULT_LANGUAGE_TRANSLATION_CONFIG",
    "LanguageTranslationError",
    "LanguageModelLoadError",
    "LanguageTranslationInputError",
    "LanguageTranslationInferenceError",
    "LanguageTextPreprocessor",
    "LanguagePreprocessorError",
]
