"""
Internal longest-phrase matcher for the Dashboard dictionary module.

The trie receives canonical phrase keys already produced by create_json.py.
It never normalizes dictionary resources and never tokenizes raw text.
Runtime text must already be tokenized by the internal DashboardTextPreprocessor.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field

from .preprocessor import DictionaryToken


class DashboardPhraseTrieError(RuntimeError):
    pass


class DashboardPhraseTrieInputError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class DashboardPhraseMatch:
    key: str
    start_token_index: int
    end_token_index: int

    @property
    def token_count(self) -> int:
        return self.end_token_index - self.start_token_index


@dataclass(slots=True)
class _TrieNode:
    children: dict[str, "_TrieNode"] = field(default_factory=dict)
    key: str | None = None


class DashboardPhraseTrie:
    """
    Trie over canonical phrase keys.

    A canonical key is exactly:
        " ".join(DashboardTextPreprocessor normalized tokens)

    Longest match wins at each start position.
    """

    def __init__(self, phrase_keys: Iterable[str]) -> None:
        self._root = _TrieNode()
        self._size = 0
        self._max_tokens = 0

        for key in phrase_keys:
            self._insert(key)

    def __len__(self) -> int:
        return self._size

    @property
    def max_phrase_tokens(self) -> int:
        return self._max_tokens

    def _insert(self, key: str) -> None:
        if not isinstance(key, str) or not key:
            raise DashboardPhraseTrieInputError(
                "phrase key must be a non-empty string."
            )

        parts = tuple(key.split(" "))
        if (
            len(parts) < 2
            or any(not part for part in parts)
            or " ".join(parts) != key
        ):
            raise DashboardPhraseTrieInputError(
                f"invalid canonical phrase key: {key!r}."
            )

        node = self._root
        for part in parts:
            node = node.children.setdefault(part, _TrieNode())

        if node.key is None:
            node.key = key
            self._size += 1

        self._max_tokens = max(self._max_tokens, len(parts))

    @staticmethod
    def _separator_is_valid(
        text: str,
        previous: DictionaryToken,
        current: DictionaryToken,
    ) -> bool:
        """
        Phrase tokens may be separated by whitespace only.
        Therefore "machine learning" can match, but
        "machine, learning" cannot.
        """
        gap = text[previous.end:current.start]
        return bool(gap) and gap.isspace()

    def longest_match(
        self,
        text: str,
        tokens: Sequence[DictionaryToken],
        start_index: int,
    ) -> DashboardPhraseMatch | None:
        if not isinstance(text, str):
            raise DashboardPhraseTrieInputError(
                "text must be a string."
            )
        if not isinstance(start_index, int):
            raise DashboardPhraseTrieInputError(
                "start_index must be int."
            )
        if start_index < 0 or start_index > len(tokens):
            raise DashboardPhraseTrieInputError(
                "invalid start_index."
            )
        if start_index == len(tokens):
            return None

        node = self._root
        best_key: str | None = None
        best_end: int | None = None
        max_end = min(
            len(tokens),
            start_index + self._max_tokens,
        )

        for index in range(start_index, max_end):
            token = tokens[index]

            if not isinstance(token, DictionaryToken):
                raise DashboardPhraseTrieInputError(
                    "tokens must contain DictionaryToken."
                )

            if index > start_index and not self._separator_is_valid(
                text,
                tokens[index - 1],
                token,
            ):
                break

            node = node.children.get(token.normalized)
            if node is None:
                break

            if node.key is not None:
                best_key = node.key
                best_end = index + 1

        if best_key is None or best_end is None:
            return None

        return DashboardPhraseMatch(
            key=best_key,
            start_token_index=start_index,
            end_token_index=best_end,
        )
