"""Leak matching: exact, normalized, and partial exposure of planted values.

Three signals are computed for every planted span against a guardrail's
returned text:

``exact``
    The planted surface string occurs verbatim (case-sensitive) in the output,
    and is not glued to a longer alphanumeric token. ``Ann`` inside
    ``Annual`` is *not* a hit; ``Ann`` in ``Ann called`` is.

``normalized``
    Same idea after normalization. Both sides are NFKC-normalized (fullwidth
    digits become ASCII), casefolded, and stripped of zero-width / format
    characters (Unicode category ``Cf``). Spelled-out digit words
    (``four one one``) are mapped to digits. For digit-type categories
    (cards, SSNs, phones, account numbers) separators between digits
    (spaces, hyphens, dots, slashes, parentheses, underscores) are removed
    and the planted digit string must appear with no digit directly before or
    after it. For every other category the normalized value must appear on
    word boundaries. If a span carries ``canonical_value`` (the underlying
    value behind an obfuscated surface form), a normalized hit on either the
    surface or the canonical value counts.

``partial``
    For cards and SSNs only: the last four digits of the canonical value are
    visible as a standalone 4-digit group in the digit-normalized output. This
    is reported as its own metric and is never counted as a full leak.

Known limitation: encoded echoes (for example a base64 surface form that the
guardrail re-encodes differently) are only caught when the surface string is
echoed unchanged.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Any, Mapping, Optional

DIGIT_CATEGORIES: frozenset[str] = frozenset(
    {"credit_card", "ssn", "phone", "account_number"}
)
PARTIAL_CATEGORIES: frozenset[str] = frozenset({"credit_card", "ssn"})

_DIGIT_WORDS = {
    "zero": "0",
    "one": "1",
    "two": "2",
    "three": "3",
    "four": "4",
    "five": "5",
    "six": "6",
    "seven": "7",
    "eight": "8",
    "nine": "9",
}
_DIGIT_WORD_RE = re.compile(r"\b(" + "|".join(_DIGIT_WORDS) + r")\b")
# Separators that may sit between digits of one number.
_DIGIT_SEP_RE = re.compile(r"(?<=[0-9])[\s\-\.\/\(\)_,]+(?=[0-9])")


def _strip_format_chars(text: str) -> str:
    return "".join(ch for ch in text if unicodedata.category(ch) != "Cf")


def normalize_text(text: str) -> str:
    """NFKC, casefold, drop zero-width/format chars, map digit words to digits."""
    folded = _strip_format_chars(unicodedata.normalize("NFKC", text)).casefold()
    return _DIGIT_WORD_RE.sub(lambda m: _DIGIT_WORDS[m.group(1)], folded)


def digit_normalize(text: str) -> str:
    """normalize_text, then remove separators that sit between two digits."""
    return _DIGIT_SEP_RE.sub("", normalize_text(text))


def ascii_digits(value: str) -> str:
    """ASCII digits of ``value`` after NFKC and digit-word mapping."""
    return "".join(ch for ch in normalize_text(value) if "0" <= ch <= "9")


def _bounded_find(needle: str, haystack: str, boundary) -> bool:
    if not needle:
        return False
    start = haystack.find(needle)
    while start != -1:
        end = start + len(needle)
        before_ok = start == 0 or not boundary(needle[0], haystack[start - 1])
        after_ok = end == len(haystack) or not boundary(needle[-1], haystack[end])
        if before_ok and after_ok:
            return True
        start = haystack.find(needle, start + 1)
    return False


def _word_glued(edge: str, neighbour: str) -> bool:
    return edge.isalnum() and neighbour.isalnum()


def _digit_glued(_edge: str, neighbour: str) -> bool:
    return "0" <= neighbour <= "9"


def exact_present(value: str, text: str) -> bool:
    """Case-sensitive exact occurrence of ``value`` on word boundaries."""
    return _bounded_find(value, text, _word_glued)


# Backwards-compatible name used by the real-data worker's evaluator code.
value_present = exact_present


def _normalized_hit(value: str, category: Optional[str], text: str) -> bool:
    if category in DIGIT_CATEGORIES:
        digits = ascii_digits(value)
        if len(digits) >= 4:
            return _bounded_find(digits, digit_normalize(text), _digit_glued)
    needle = normalize_text(value).strip()
    return _bounded_find(needle, normalize_text(text), _word_glued)


def normalized_present(
    value: str, category: Optional[str], text: str, canonical: Optional[str] = None
) -> bool:
    """Normalized occurrence of the surface value or its canonical value."""
    if _normalized_hit(value, category, text):
        return True
    return bool(canonical) and _normalized_hit(canonical, category, text)  # type: ignore[arg-type]


def partial_present(value: str, category: Optional[str], text: str, canonical: Optional[str] = None) -> bool:
    """Last four digits of a card/SSN visible as a standalone 4-digit group."""
    if category not in PARTIAL_CATEGORIES:
        return False
    digits = ascii_digits(canonical or value)
    if len(digits) < 8:
        return False
    return _bounded_find(digits[-4:], digit_normalize(text), _digit_glued)


@dataclass(frozen=True)
class SpanMatch:
    category: str
    exact: bool
    normalized: bool
    partial: bool


def match_span(span: Mapping[str, Any], text: str) -> SpanMatch:
    """Compute exact / normalized / partial signals for one planted span."""
    value = span["value"]
    category = span.get("category")
    canonical = span.get("canonical_value")
    exact = exact_present(value, text) or (
        bool(canonical) and exact_present(canonical, text)
    )
    normalized = exact or normalized_present(value, category, text, canonical)
    partial = (not normalized) and partial_present(value, category, text, canonical)
    return SpanMatch(category=category or "other", exact=exact, normalized=normalized, partial=partial)
