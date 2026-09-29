"""
Build canonical Dashboard dictionary from data/anhviet109K.txt.

DashboardTextPreprocessor is the only lookup normalization/tokenization contract.
This file only parses raw dictionary data and writes canonical JSON.
Examples are preserved nowhere in the runtime index because Dashboard scans
lexical words/phrases, not example sentences.
"""

from __future__ import annotations

import argparse
import json
import re

from dataclasses import dataclass, field
from pathlib import Path

from .preprocessor import DashboardTextPreprocessor


SCHEMA_VERSION = 3

# ---------------------------------------------------------------------------
# RESOURCE PATHS
# ---------------------------------------------------------------------------
# Edit these two paths to match your project.
#
# Example project:
#
#   translate_select/
#   ├── backend/
#   │   └── dictionary/
#   │       └── create_json.py
#   └── dictionary/
#       └── anhviet109K.txt
#
# In that layout:
#
#   SOURCE_PATH = Path("../../dictionary/anhviet109K.txt")
#   OUTPUT_PATH = Path("../../dictionary/en_vi_dashboard.json")
#
# Paths are resolved from the current working directory when the script runs.
SOURCE_PATH = Path("dictionary/anhviet109K.txt")
OUTPUT_PATH = Path("dictionary/en_vi_dashboard.json")

DEFAULT_SOURCE_PATH = SOURCE_PATH
DEFAULT_OUTPUT_PATH = OUTPUT_PATH

HEADER_PATTERN = re.compile(
    r"^@(?P<headword>.*?)(?:\s+/(?P<ipa>[^/]*)/)?\s*$"
)


@dataclass(slots=True)
class RawExpression:
    source: str
    meanings: list[str] = field(default_factory=list)


@dataclass(slots=True)
class RawEntry:
    headword: str
    ipa: str | None
    meanings: list[str] = field(default_factory=list)
    expressions: list[RawExpression] = field(default_factory=list)


def clean_text(value: str) -> str:
    """Payload cleaning only. Not lookup normalization."""
    return " ".join(value.strip().split())


def append_unique(values: list[str], value: str | None) -> None:
    value = clean_text(value or "")
    if value and value not in values:
        values.append(value)


def looks_like_flat_collocation(
    value: str,
    headword: str,
) -> bool:
    """
    Detect collocation rows in flat technical dictionary blocks.

    Examples from the real source:
        data        -> "input d. ..."
        information -> "processed i. ..."
        machine     -> "digital m. ..."
        method      -> "m. of approximation ..."

    Conservative rule:
    - only the headword's own initial abbreviation is accepted;
    - the abbreviation must occur before Vietnamese/non-ASCII letters;
    - caller only applies this to @headword blocks without "* section".
    """
    letters = [
        char.casefold()
        for char in headword
        if char.isascii() and char.isalpha()
    ]

    if len(letters) < 2:
        return False

    initial = re.escape(letters[0])

    match = re.search(
        rf"(?<![A-Za-z]){initial}\.(?=\s|[,(])",
        value,
        flags=re.IGNORECASE,
    )

    if match is None:
        return False

    prefix = value[:match.start()]

    return not any(
        char.isalpha() and not char.isascii()
        for char in prefix
    )


def parse_raw_dictionary(path: Path) -> list[RawEntry]:
    """
    Parse the merged raw dictionary conservatively.

    Source rules confirmed from the real dictionary:
    - "- ..." normally means a headword definition.
    - after "! expression", following "- ..." lines are meanings of that
      expression, not meanings of the headword.
    - flat technical blocks may use abbreviated headwords such as
      m., d., i. for collocations; those rows are not headword meanings.
    - examples and standalone '+' explanatory notes are not Dashboard
      word meanings.
    """
    entries: list[RawEntry] = []

    current: RawEntry | None = None
    pending_expression: RawExpression | None = None

    current_has_section = False
    flat_collocation_mode = False

    with path.open("r", encoding="utf-8", errors="replace") as file:
        for raw_line in file:
            line = raw_line.strip()

            if not line:
                continue

            if line.startswith("@"):
                if line == "@":
                    current = None
                    pending_expression = None
                    current_has_section = False
                    flat_collocation_mode = False
                    continue

                match = HEADER_PATTERN.match(line)

                if match is None:
                    raise ValueError(
                        f"Invalid dictionary header: {line!r}"
                    )

                headword = clean_text(
                    match.group("headword")
                )

                if not headword:
                    raise ValueError(
                        f"Empty dictionary headword: {line!r}"
                    )

                ipa = (
                    clean_text(match.group("ipa") or "")
                    or None
                )

                current = RawEntry(
                    headword=headword,
                    ipa=ipa,
                )

                entries.append(current)

                pending_expression = None
                current_has_section = False
                flat_collocation_mode = False
                continue

            if current is None:
                continue

            if line.startswith("*"):
                current_has_section = True
                pending_expression = None
                flat_collocation_mode = False
                continue

            if line.startswith("!"):
                value = clean_text(line[1:])

                if not value:
                    pending_expression = None
                    continue

                if "+" in value:
                    source, meaning = value.split("+", 1)

                    expression = RawExpression(
                        source=clean_text(source),
                    )

                    append_unique(
                        expression.meanings,
                        meaning,
                    )

                    current.expressions.append(
                        expression
                    )

                    pending_expression = None

                else:
                    expression = RawExpression(
                        source=value
                    )

                    current.expressions.append(
                        expression
                    )

                    pending_expression = expression

                continue

            if line.startswith("-"):
                value = clean_text(line[1:])

                if not value:
                    continue

                # Example:
                #   !to learn by heart
                #   - học thuộc lòng
                #
                # Multiple consecutive '-' lines may belong to one expression.
                if pending_expression is not None:
                    append_unique(
                        pending_expression.meanings,
                        value,
                    )
                    continue

                # Flat technical block:
                #   @machine
                #   - máy; cơ cấu; thiết bị...
                #   - accounting m. máy kế toán
                #   - digital m. máy tính chữ số
                #
                # Keep the first real definition. Once the block switches to
                # abbreviated collocations, exclude all remaining '-' rows from
                # the headword meanings.
                if (
                    not current_has_section
                    and not flat_collocation_mode
                    and looks_like_flat_collocation(
                        value,
                        current.headword,
                    )
                ):
                    flat_collocation_mode = True
                    continue

                if flat_collocation_mode:
                    continue

                append_unique(
                    current.meanings,
                    value,
                )
                continue

            if line.startswith("+"):
                value = clean_text(line[1:])

                if (
                    pending_expression is not None
                    and value
                ):
                    append_unique(
                        pending_expression.meanings,
                        value,
                    )
                    pending_expression = None

                # Standalone '+' lines in economics entries are explanatory
                # notes, not compact Dashboard meanings.
                continue

            if line.startswith("="):
                pending_expression = None
                continue

            pending_expression = None

    return entries


def canonical_tokens(
    text: str,
    tokenizer: DashboardTextPreprocessor,
) -> tuple[str, ...]:
    """
    Convert one lexical source into canonical word tokens.

    Only DashboardTextPreprocessor decides token boundaries and normalization.
    Punctuation between separate tokens is rejected, so a phrase key cannot
    silently discard punctuation from the raw dictionary.
    """
    source = clean_text(text)
    if not source:
        return ()

    tokens = tokenizer.tokenize(source)
    if not tokens or any(not token.is_word for token in tokens):
        return ()

    cursor = 0
    for token in tokens:
        if source[cursor:token.start].strip():
            return ()
        cursor = token.end

    if source[cursor:].strip():
        return ()

    # Dictionary identity phải giữ lexical surface form.
    # Tokenizer vẫn là source of truth cho normalization,
    # nhưng tuyệt đối không lemmatize key khi build resource.
    #
    # Ví dụ:
    #     am -> am
    #     is -> is
    #     machines -> machines
    #
    # Không được collapse:
    #     am / is / be -> cùng một key
    return tuple(
        tokenizer.normalize_token(
            token.text,
            apply_lemma=False,
        )
        for token in tokens
    )


def add_entry(
    table: dict[str, dict],
    *,
    key: str,
    term: str,
    ipa: str | None = None,
    meanings: list[str] | tuple[str, ...] = (),
) -> None:
    entry = table.setdefault(
        key,
        {
            "term": clean_text(term),
            "ipa": [],
            "meanings": [],
        },
    )

    append_unique(entry["ipa"], ipa)
    for meaning in meanings:
        append_unique(entry["meanings"], meaning)


def build_dashboard_dictionary(
    entries: list[RawEntry],
    tokenizer: DashboardTextPreprocessor,
) -> dict:
    words: dict[str, dict] = {}
    phrases: dict[str, dict] = {}

    for entry in entries:
        tokens = canonical_tokens(entry.headword, tokenizer)

        if len(tokens) == 1:
            add_entry(
                words,
                key=tokens[0],
                term=entry.headword,
                ipa=entry.ipa,
                meanings=entry.meanings,
            )

        elif len(tokens) >= 2:
            add_entry(
                phrases,
                key=" ".join(tokens),
                term=entry.headword,
                ipa=entry.ipa,
                meanings=entry.meanings,
            )

        for expression in entry.expressions:
            if not expression.meanings:
                continue

            phrase_tokens = canonical_tokens(
                expression.source,
                tokenizer,
            )

            if len(phrase_tokens) < 2:
                continue

            add_entry(
                phrases,
                key=" ".join(phrase_tokens),
                term=expression.source,
                meanings=expression.meanings,
            )

    # Dashboard only returns terms with a meaning.
    words = {
        key: value
        for key, value in words.items()
        if value["meanings"]
    }
    phrases = {
        key: value
        for key, value in phrases.items()
        if value["meanings"]
    }

    return {
        "schema_version": SCHEMA_VERSION,
        "words": dict(sorted(words.items())),
        "phrases": dict(sorted(phrases.items())),
    }


def validate_dashboard_dictionary(data: object) -> None:
    if not isinstance(data, dict):
        raise TypeError("Dashboard dictionary root must be an object.")

    if set(data) != {"schema_version", "words", "phrases"}:
        raise ValueError(
            "Dashboard dictionary must contain exactly "
            "'schema_version', 'words', and 'phrases'."
        )

    if data["schema_version"] != SCHEMA_VERSION:
        raise ValueError("Unexpected Dashboard schema version.")

    for group_name in ("words", "phrases"):
        group = data[group_name]
        if not isinstance(group, dict):
            raise TypeError(f"{group_name!r} must be an object.")

        for key, entry in group.items():
            if not isinstance(key, str) or not key:
                raise ValueError(f"Invalid {group_name} key: {key!r}.")
            if not isinstance(entry, dict):
                raise TypeError(f"{group_name}[{key!r}] must be an object.")
            if set(entry) != {"term", "ipa", "meanings"}:
                raise ValueError(f"Invalid entry schema for {key!r}.")
            if not isinstance(entry["term"], str) or not entry["term"]:
                raise ValueError(f"Invalid term for {key!r}.")
            if not isinstance(entry["ipa"], list) or not all(
                isinstance(value, str) and value
                for value in entry["ipa"]
            ):
                raise TypeError(f"Invalid IPA list for {key!r}.")
            if not isinstance(entry["meanings"], list) or not entry["meanings"]:
                raise ValueError(f"{key!r} must contain at least one meaning.")
            if not all(
                isinstance(value, str) and value
                for value in entry["meanings"]
            ):
                raise TypeError(f"Invalid meanings for {key!r}.")


def build(source_path: Path, output_path: Path) -> None:
    if not source_path.is_file():
        raise FileNotFoundError(f"Raw dictionary not found: {source_path}")

    tokenizer = DashboardTextPreprocessor()
    entries = parse_raw_dictionary(source_path)
    if not entries:
        raise RuntimeError("No dictionary entries were parsed.")

    data = build_dashboard_dictionary(entries, tokenizer)
    validate_dashboard_dictionary(data)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as file:
        json.dump(
            data,
            file,
            ensure_ascii=False,
            separators=(",", ":"),
        )
        file.write("\n")

    print(f"Raw entries: {len(entries)}")
    print(f"Dashboard words: {len(data['words'])}")
    print(f"Dashboard phrases: {len(data['phrases'])}")
    print(f"Written: {output_path}")
    print("Dashboard dictionary build: PASS")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build canonical Dashboard dictionary."
    )
    parser.add_argument(
        "--source",
        type=Path,
        default=DEFAULT_SOURCE_PATH,
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT_PATH,
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    build(
        args.source.expanduser().resolve(),
        args.output.expanduser().resolve(),
    )


if __name__ == "__main__":
    main()
