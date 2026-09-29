"""
Dashboard vocabulary extractor.

Input:
    Sequence[str] corrected OCR texts.

Output:
    tuple[DashboardEntry, ...] sorted A -> Z.

Each input string is processed independently, so a phrase can never match
across the boundary between two OCR correction results.
"""

from __future__ import annotations

import json

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from .preprocessor import DictionaryToken, DashboardTextPreprocessor
from .phrase_trie import DashboardPhraseTrie


SCHEMA_VERSION = 3
# ---------------------------------------------------------------------------
# RESOURCE PATH
# ---------------------------------------------------------------------------
# Edit this path if dictionary.json is stored elsewhere.
#
# Example:
#     backend/dictionary/processor.py
#     dictionary/en_vi_dashboard.json
#
# Then:
#     Path("../../dictionary/en_vi_dashboard.json")
DEFAULT_DICTIONARY_PATH = Path(
    r"/home/haivx/Desktop/vscode/translate_select/dictionary/en_vi_dashboard.json"
)

DashboardEntryType = Literal["word", "phrase"]


class DashboardError(RuntimeError):
    pass


class DashboardInputError(ValueError):
    pass


class DashboardDictionaryLoadError(DashboardError):
    pass


@dataclass(frozen=True, slots=True)
class DashboardEntry:
    """
    One unique dictionary item ready for Dashboard display.

    No primary meaning/pronunciation is invented:
    all distinct dictionary values are returned.
    """
    term: str
    meanings: tuple[str, ...]
    ipas: tuple[str, ...]
    entry_type: DashboardEntryType


class DashboardExtractor:
    """
    Black-box vocabulary extraction.

    corrected texts
        -> full internal preprocessing + tokenization
        -> surface + lemma phrase matching
        -> word surface lookup
        -> lemma fallback
        -> remove unknown
        -> deduplicate
        -> alphabetic sort
        -> DashboardEntry[]
    """

    def __init__(
        self,
        dictionary_path: str | Path | None = None,
    ) -> None:
        self.dictionary_path = Path(
            dictionary_path or DEFAULT_DICTIONARY_PATH
        ).expanduser().resolve()

        data = self._load_dictionary()
        self._words: dict[str, dict] = data["words"]
        self._phrases: dict[str, dict] = data["phrases"]

        self._preprocessor = DashboardTextPreprocessor()
        self._preprocessor = DashboardTextPreprocessor()

        self._phrase_trie = DashboardPhraseTrie(
            self._phrases.keys()
        )

    def extract(
        self,
        texts: Sequence[str],
    ) -> tuple[DashboardEntry, ...]:
        """
        Process all corrected OCR texts as one Dashboard snapshot.

        Sequence[str] is required. A bare str is rejected.
        """
        self._validate_texts(texts)

        found: dict[
            tuple[DashboardEntryType, str],
            DashboardEntry,
        ] = {}

        for text in texts:
            text = self._preprocessor.preprocess(text)

            # Sau khi loại toàn bộ token rác, text có thể trở thành ""
            if not text:
                continue

            lemma_tokens = self._preprocessor.tokenize(text)
            surface_tokens = self._surface_tokens(lemma_tokens)
            token_index = 0

            while token_index < len(surface_tokens):
                phrase_match = self._best_phrase_match(
                    text,
                    surface_tokens,
                    lemma_tokens,
                    token_index,
                )

                if phrase_match is not None:
                    key = ("phrase", phrase_match.key)

                    if key not in found:
                        found[key] = self._make_entry(
                            self._phrases[phrase_match.key],
                            "phrase",
                        )

                    # Longest phrase owns the complete matched span.
                    token_index = phrase_match.end_token_index
                    continue

                surface_token = surface_tokens[token_index]
                lemma_token = lemma_tokens[token_index]

                if surface_token.is_word:
                    lookup_key = surface_token.normalized
                    resource = self._words.get(lookup_key)

                    # Exact surface form luôn thắng.
                    # Chỉ khi không có mới fallback sang lemma.
                    if resource is None:
                        lemma_key = lemma_token.normalized

                        if lemma_key != lookup_key:
                            resource = self._words.get(lemma_key)

                            if resource is not None:
                                lookup_key = lemma_key

                    if resource is not None:
                        key = ("word", lookup_key)

                        if key not in found:
                            found[key] = self._make_entry(
                                resource,
                                "word",
                            )

                token_index += 1

        # Sort theo chính term mà UI sẽ nhìn thấy, không sort theo
        # internal lookup key.
        return tuple(
            sorted(
                found.values(),
                key=lambda entry: (
                    entry.term.casefold(),
                    entry.entry_type,
                ),
            )
        )

    def _surface_tokens(
        self,
        tokens: Sequence[DictionaryToken],
    ) -> tuple[DictionaryToken, ...]:
        """
        Tạo token lookup không lemma nhưng giữ nguyên span/type.

        DashboardTextPreprocessor vẫn là normalization source of truth.
        """
        return tuple(
            DictionaryToken(
                index=token.index,
                text=token.text,
                normalized=self._preprocessor.normalize_token(
                    token.text,
                    apply_lemma=False,
                ),
                start=token.start,
                end=token.end,
                token_type=token.token_type,
            )
            for token in tokens
        )

    def _best_phrase_match(
        self,
        text: str,
        surface_tokens: Sequence[DictionaryToken],
        lemma_tokens: Sequence[DictionaryToken],
        start_index: int,
    ):
        """
        Exact surface phrase và lemma-fallback cùng được xét.

        - phrase dài hơn thắng;
        - cùng độ dài thì exact surface thắng.
        """
        surface_match = self._phrase_trie.longest_match(
            text,
            surface_tokens,
            start_index,
        )

        lemma_match = self._phrase_trie.longest_match(
            text,
            lemma_tokens,
            start_index,
        )

        if surface_match is None:
            return lemma_match

        if lemma_match is None:
            return surface_match

        if lemma_match.token_count > surface_match.token_count:
            return lemma_match

        return surface_match

    @staticmethod
    def _make_entry(
        resource: dict,
        entry_type: DashboardEntryType,
    ) -> DashboardEntry:
        return DashboardEntry(
            term=resource["term"],
            meanings=tuple(resource["meanings"]),
            ipas=tuple(resource["ipa"]),
            entry_type=entry_type,
        )

    @staticmethod
    def _validate_texts(texts: Sequence[str]) -> None:
        if isinstance(texts, (str, bytes)) or not isinstance(
            texts,
            Sequence,
        ):
            raise DashboardInputError(
                "texts must be a sequence of strings, not one string."
            )

        for index, text in enumerate(texts):
            if not isinstance(text, str):
                raise DashboardInputError(
                    f"texts[{index}] must be a string."
                )
            if "\x00" in text:
                raise DashboardInputError(
                    f"texts[{index}] must not contain null characters."
                )

    def _load_dictionary(self) -> dict:
        if not self.dictionary_path.is_file():
            raise DashboardDictionaryLoadError(
                f"Dashboard dictionary not found: {self.dictionary_path}"
            )

        try:
            with self.dictionary_path.open(
                "r",
                encoding="utf-8",
            ) as file:
                data = json.load(file)
        except json.JSONDecodeError as exc:
            raise DashboardDictionaryLoadError(
                "Invalid Dashboard JSON."
            ) from exc
        except OSError as exc:
            raise DashboardDictionaryLoadError(
                "Could not read Dashboard dictionary."
            ) from exc

        self._validate_dictionary(data)
        return data

    @staticmethod
    def _validate_dictionary(data: object) -> None:
        if not isinstance(data, dict):
            raise DashboardDictionaryLoadError(
                "Dashboard JSON root must be an object."
            )

        if set(data) != {"schema_version", "words", "phrases"}:
            raise DashboardDictionaryLoadError(
                "Dashboard JSON has an invalid root schema."
            )

        if data["schema_version"] != SCHEMA_VERSION:
            raise DashboardDictionaryLoadError(
                "Unsupported Dashboard schema version."
            )

        for group_name in ("words", "phrases"):
            group = data[group_name]

            if not isinstance(group, dict):
                raise DashboardDictionaryLoadError(
                    f"{group_name!r} must be an object."
                )

            for key, resource in group.items():
                if not isinstance(key, str) or not key:
                    raise DashboardDictionaryLoadError(
                        f"Invalid {group_name} key."
                    )

                if not isinstance(resource, dict) or set(resource) != {
                    "term",
                    "ipa",
                    "meanings",
                }:
                    raise DashboardDictionaryLoadError(
                        f"Invalid resource for {key!r}."
                    )

                term = resource["term"]
                ipas = resource["ipa"]
                meanings = resource["meanings"]

                if not isinstance(term, str) or not term:
                    raise DashboardDictionaryLoadError(
                        f"Invalid term for {key!r}."
                    )
                if not isinstance(ipas, list) or not all(
                    isinstance(value, str) and value
                    for value in ipas
                ):
                    raise DashboardDictionaryLoadError(
                        f"Invalid IPA list for {key!r}."
                    )
                if not isinstance(meanings, list) or not meanings or not all(
                    isinstance(value, str) and value
                    for value in meanings
                ):
                    raise DashboardDictionaryLoadError(
                        f"Invalid meanings for {key!r}."
                    )
