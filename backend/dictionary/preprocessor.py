"""
Self-contained preprocessing layer for the Dashboard dictionary.

This module is the lexical boundary of backend/dictionary.

Responsibilities
----------------
1. Validate incoming dictionary text.
2. Remove noise:
   - email addresses;
   - numeric values;
   - isolated one-character alphabetic tokens;
   - English stopwords;
   - repeated/empty whitespace.
3. Tokenize words, numbers and emails.
4. Normalize Unicode, apostrophes, hyphens and case.
5. Lemmatize English words.
6. Preserve token spans in the original processed text.
7. Provide the DictionaryToken contract used by create_json,
   phrase_trie and processor.

The other dictionary modules depend only on this module.
They do not import backend/tokenizer.py or any module outside
backend/dictionary for lexical processing.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Literal

from simplemma import lemmatize


TokenType = Literal["word", "number", "email"]
UnicodeNormalization = Literal["NFC", "NFKC"]


@dataclass(frozen=True, slots=True)
class PreprocessorConfig:
    case_sensitive: bool = False
    unicode_normalization: UnicodeNormalization = "NFKC"
    normalize_apostrophes: bool = True
    normalize_hyphens: bool = True

    include_numbers: bool = True
    include_single_character_words: bool = True

    max_input_characters: int = 10_000
    max_token_characters: int = 128

    lemmatize_words: bool = True
    lemmatizer_language: str = "en"

    def __post_init__(self) -> None:
        if self.unicode_normalization not in {"NFC", "NFKC"}:
            raise ValueError(
                "unicode_normalization must be 'NFC' or 'NFKC'."
            )

        if self.max_input_characters < 1:
            raise ValueError(
                "max_input_characters must be >= 1."
            )

        if self.max_token_characters < 1:
            raise ValueError(
                "max_token_characters must be >= 1."
            )


DEFAULT_PREPROCESSOR_CONFIG = PreprocessorConfig()


DEFAULT_STOPWORDS: frozenset[str] = frozenset(
    {
        # articles / determiners
        "a", "an", "the",
        "this", "that", "these", "those",
        "some", "any", "each", "every",
        "either", "neither", "another", "other", "such",

        # conjunctions
        "and", "or", "but", "nor", "yet", "so",
        "although", "though", "because", "since",
        "unless", "while", "whereas", "whether",

        # common prepositions
        "about", "above", "across", "after", "against", "along",
        "among", "around", "at", "before", "behind", "below",
        "beneath", "beside", "besides", "between", "beyond",
        "by", "despite", "down", "during", "except", "for",
        "from", "in", "inside", "into", "near", "of", "off",
        "on", "onto", "over", "past", "per", "regarding",
        "since", "through", "throughout", "to", "toward",
        "towards", "under", "underneath", "until", "up",
        "upon", "via", "with", "within", "without",

        # pronouns
        "i", "me", "my", "mine", "myself",
        "we", "us", "our", "ours", "ourselves",
        "you", "your", "yours", "yourself", "yourselves",
        "he", "him", "his", "himself",
        "she", "her", "hers", "herself",
        "it", "its", "itself",
        "they", "them", "their", "theirs", "themselves",
        "who", "whom", "whose", "which", "what",

        # auxiliary / modal verbs
        "am", "is", "are", "was", "were", "be", "been", "being",
        "do", "does", "did",
        "have", "has", "had", "having",
        "can", "could", "may", "might", "must",
        "shall", "should", "will", "would",

        # common adverbs / function words
        "again", "also", "already", "always", "ever", "never",
        "just", "more", "most", "much", "very", "often",
        "only", "once", "rather", "really", "quite",
        "still", "too", "well", "almost", "even",
        "here", "there", "then", "than", "when", "where",
        "why", "how", "not", "no", "yes",

        # common discourse/function words
        "both", "few", "many", "several", "all", "enough",
        "own", "same", "different",
    }
)


# Cleaning patterns.
EMAIL_PATTERN = re.compile(
    r"(?<![\w.+-])"
    r"[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+"
    r"@"
    r"[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+"
    r"(?![\w.-])",
    re.IGNORECASE,
)

NUMBER_PATTERN = re.compile(
    r"(?<![\w])[-+]?"
    r"(?:\d+(?:[.,]\d+)*|\d+[.,])"
    r"(?:%|[A-Za-z]+)?"
    r"(?![\w])"
)

SINGLE_LETTER_PATTERN = re.compile(
    r"(?<![A-Za-z])[A-Za-z](?![A-Za-z])"
)

WHITESPACE_PATTERN = re.compile(r"\s+")


class DashboardPreprocessorError(ValueError):
    """Invalid Dashboard preprocessor input."""


@dataclass(frozen=True, slots=True)
class DictionaryToken:
    """
    Token produced by DashboardTextPreprocessor.tokenize().

    start/end refer to the text passed to tokenize(), using [start, end).
    """

    index: int
    text: str
    normalized: str
    start: int
    end: int
    token_type: TokenType

    @property
    def length(self) -> int:
        return len(self.text)

    @property
    def is_word(self) -> bool:
        return self.token_type == "word"

    @property
    def is_number(self) -> bool:
        return self.token_type == "number"

    @property
    def is_email(self) -> bool:
        return self.token_type == "email"


class DashboardTextPreprocessor:
    """
    Single preprocessing and lexical engine for the dictionary module.

    The public workflow is:

        raw corrected OCR text
            -> preprocess()
            -> tokenize()
            -> dictionary lookup

    `preprocess()` is the full cleaning stage.

    `tokenize()` is the lexical stage and is also used by create_json
    when constructing canonical dictionary keys.
    """

    # Tokenization patterns.
    EMAIL_TOKEN_PATTERN = (
        r"[A-Za-z0-9._%+\-]+"
        r"@"
        r"[A-Za-z0-9.\-]+"
        r"\."
        r"[A-Za-z]{2,}"
    )

    WORD_PATTERN = (
        r"[^\W\d_]+"
        r"(?:['’\-‐-‒–—−][^\W\d_]+)*"
    )

    NUMBER_TOKEN_PATTERN = (
        r"\d+"
        r"(?:[.,:/\-]\d+)*"
    )

    TOKEN_PATTERN = re.compile(
        rf"(?P<email>{EMAIL_TOKEN_PATTERN})"
        rf"|(?P<word>{WORD_PATTERN})"
        rf"|(?P<number>{NUMBER_TOKEN_PATTERN})",
        flags=re.UNICODE,
    )

    APOSTROPHE_TRANSLATION = str.maketrans(
        {
            "’": "'",
            "‘": "'",
            "‛": "'",
            "ʼ": "'",
        }
    )

    HYPHEN_TRANSLATION = str.maketrans(
        {
            "‐": "-",
            "-": "-",
            "‒": "-",
            "–": "-",
            "—": "-",
            "−": "-",
        }
    )

    def __init__(
        self,
        config: PreprocessorConfig | None = None,
        stopwords: Iterable[str] | None = None,
    ) -> None:
        self.config = config or DEFAULT_PREPROCESSOR_CONFIG

        words = (
            DEFAULT_STOPWORDS
            if stopwords is None
            else frozenset(
                word.casefold().strip()
                for word in stopwords
                if isinstance(word, str) and word.strip()
            )
        )

        self._stopwords = frozenset(
            word
            for word in words
            if len(word) > 1
        )

    @property
    def stopwords(self) -> frozenset[str]:
        return self._stopwords

    # ==========================================================
    # FULL PREPROCESSING
    # ==========================================================

    def preprocess(self, text: str) -> str:
        """
        Fully clean one corrected OCR text.

        Order:
            1. validate;
            2. remove emails;
            3. remove numbers;
            4. remove isolated single letters;
            5. remove stopwords;
            6. normalize whitespace.
        """
        self._validate_text(text)

        if not text:
            return ""

        value = EMAIL_PATTERN.sub(" ", text)
        value = NUMBER_PATTERN.sub(" ", value)
        value = SINGLE_LETTER_PATTERN.sub(" ", value)

        if self._stopwords:
            value = self._build_stopword_pattern().sub(" ", value)

        return WHITESPACE_PATTERN.sub(" ", value).strip()

    def preprocess_many(
        self,
        texts: Sequence[str],
    ) -> tuple[str, ...]:
        if isinstance(texts, (str, bytes)) or not isinstance(
            texts,
            Sequence,
        ):
            raise DashboardPreprocessorError(
                "texts must be a sequence of strings, not one string."
            )

        return tuple(
            self.preprocess(text)
            for text in texts
        )

    # ==========================================================
    # TOKENIZATION
    # ==========================================================

    def tokenize(
        self,
        text: str,
    ) -> tuple[DictionaryToken, ...]:
        """
        Tokenize one string.

        No semantic lookup happens here.
        The method only creates lexical tokens and normalized forms.
        """
        validated_text = self._validate_text(text)

        if not validated_text:
            return ()

        tokens: list[DictionaryToken] = []

        for match in self.TOKEN_PATTERN.finditer(
            validated_text
        ):
            token_type = self._get_token_type(match)

            if (
                token_type == "number"
                and not self.config.include_numbers
            ):
                continue

            original = match.group(0)

            if len(original) > self.config.max_token_characters:
                continue

            if (
                token_type == "word"
                and len(original) == 1
                and not self.config.include_single_character_words
            ):
                continue

            normalized = self.normalize_token(
                original,
                apply_lemma=(token_type == "word"),
            )

            tokens.append(
                DictionaryToken(
                    index=len(tokens),
                    text=original,
                    normalized=normalized,
                    start=match.start(),
                    end=match.end(),
                    token_type=token_type,
                )
            )

        return tuple(tokens)

    def normalize_token(
        self,
        token: str,
        *,
        apply_lemma: bool = True,
    ) -> str:
        """
        Normalize one lexical token.

        Examples:
            Decided  -> decide
            STUDIES  -> study
            Machines -> machine

        `apply_lemma=False` is required when creating dictionary
        surface keys.
        """
        validated_token = self._validate_token(token)

        normalized = unicodedata.normalize(
            self.config.unicode_normalization,
            validated_token,
        )

        if self.config.normalize_apostrophes:
            normalized = normalized.translate(
                self.APOSTROPHE_TRANSLATION
            )

        if self.config.normalize_hyphens:
            normalized = normalized.translate(
                self.HYPHEN_TRANSLATION
            )

        if not self.config.case_sensitive:
            normalized = normalized.casefold()

        if (
            apply_lemma
            and self.config.lemmatize_words
        ):
            normalized = lemmatize(
                normalized,
                lang=self.config.lemmatizer_language,
            )

        return normalized

    def normalize_phrase(
        self,
        phrase: str,
    ) -> tuple[str, ...]:
        return tuple(
            token.normalized
            for token in self.tokenize(phrase)
        )

    def extract_normalized_tokens(
        self,
        text: str,
    ) -> tuple[str, ...]:
        return tuple(
            token.normalized
            for token in self.tokenize(text)
        )

    def __call__(
        self,
        text: str,
    ) -> tuple[DictionaryToken, ...]:
        return self.tokenize(text)

    # ==========================================================
    # VALIDATION
    # ==========================================================

    def _validate_text(self, text: str) -> str:
        if not isinstance(text, str):
            raise DashboardPreprocessorError(
                "text must be a string, "
                f"received {type(text).__name__}."
            )

        if "\x00" in text:
            raise DashboardPreprocessorError(
                "text must not contain null characters."
            )

        if len(text) > self.config.max_input_characters:
            raise DashboardPreprocessorError(
                "text exceeds the configured maximum length of "
                f"{self.config.max_input_characters} characters."
            )

        return text

    def _validate_token(self, token: str) -> str:
        if not isinstance(token, str):
            raise DashboardPreprocessorError(
                "token must be a string, "
                f"received {type(token).__name__}."
            )

        if not token:
            raise DashboardPreprocessorError(
                "token must not be empty."
            )

        if any(character.isspace() for character in token):
            raise DashboardPreprocessorError(
                "normalize_token() only accepts one token."
            )

        if len(token) > self.config.max_token_characters:
            raise DashboardPreprocessorError(
                "token exceeds the configured maximum length of "
                f"{self.config.max_token_characters} characters."
            )

        return token

    @staticmethod
    def _get_token_type(
        match: re.Match[str],
    ) -> TokenType:
        if match.lastgroup == "email":
            return "email"

        if match.lastgroup == "number":
            return "number"

        return "word"

    def _build_stopword_pattern(self) -> re.Pattern[str]:
        escaped = sorted(
            (
                re.escape(word)
                for word in self._stopwords
            ),
            key=len,
            reverse=True,
        )

        return re.compile(
            rf"(?<![A-Za-z])(?:{'|'.join(escaped)})(?![A-Za-z])",
            re.IGNORECASE,
        )


__all__ = [
    "DEFAULT_STOPWORDS",
    "DEFAULT_PREPROCESSOR_CONFIG",
    "DashboardPreprocessorError",
    "DictionaryToken",
    "PreprocessorConfig",
    "DashboardTextPreprocessor",
]
