"""
Dashboard dictionary subsystem.

Boundary
--------
The dictionary package is self-contained. It owns:
    - text preprocessing;
    - tokenization;
    - Unicode/case/apostrophe/hyphen normalization;
    - English lemmatization;
    - dictionary resource construction;
    - phrase matching;
    - word/phrase lookup;
    - Dashboard entry construction.

No dictionary module imports backend/tokenizer.py.

External API boundary
---------------------
The application layer receives TranslationRequest from backend/schema.py.
It passes request.text into DashboardExtractor and then maps the returned
DashboardEntry objects into the application-level TranslationResponse.

The dictionary package deliberately does not import schema.py. This keeps
the dependency direction one-way:

    schema -> pipeline -> dictionary

rather than:

    dictionary -> schema -> dictionary
"""

from .processor import (
    DashboardDictionaryLoadError,
    DashboardEntry,
    DashboardError,
    DashboardExtractor,
    DashboardInputError,
)
from .preprocessor import (
    DEFAULT_PREPROCESSOR_CONFIG,
    DEFAULT_STOPWORDS,
    DashboardPreprocessorError,
    DashboardTextPreprocessor,
    DictionaryToken,
    PreprocessorConfig,
)

__all__ = [
    "DashboardExtractor",
    "DashboardEntry",
    "DashboardError",
    "DashboardInputError",
    "DashboardDictionaryLoadError",
    "DashboardTextPreprocessor",
    "DictionaryToken",
    "PreprocessorConfig",
    "DEFAULT_PREPROCESSOR_CONFIG",
    "DEFAULT_STOPWORDS",
    "DashboardPreprocessorError",
]
