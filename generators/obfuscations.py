"""Deterministic obfuscation encoders for the 'hard' (adversarial) layer.

Every encoder maps a canonical planted value to the surface form written into
the prompt. They are deterministic so the validator can recompute
``encode(canonical) == surface`` for every adversarial span. ``split_sentences``
is checked at record level instead (fragments, in order, rebuild the value).
"""

from __future__ import annotations

import base64

DIGIT_WORDS = ("zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine")
ZWSP = "\u200b"
_LEET = str.maketrans({"a": "4", "e": "3", "i": "1", "o": "0", "s": "5", "t": "7",
                       "A": "4", "E": "3", "I": "1", "O": "0", "S": "5", "T": "7"})


def spaced_digits(value: str) -> str:
    return " ".join(ch for ch in value if ch.isascii() and ch.isdigit())


def dashed_digits(value: str) -> str:
    return "-".join(ch for ch in value if ch.isascii() and ch.isdigit())


def spelled_digits(value: str) -> str:
    return " ".join(DIGIT_WORDS[int(ch)] for ch in value if ch.isascii() and ch.isdigit())


def leetspeak(value: str) -> str:
    return value.translate(_LEET)


_LEET_ALT = str.maketrans({"a": "@", "i": "!", "s": "$", "t": "+", "e": "3", "o": "0",
                           "A": "@", "I": "!", "S": "$", "T": "+", "E": "3", "O": "0"})


def leetspeak_alt(value: str) -> str:
    """Second leet alphabet (symbols instead of digits for a/i/s/t)."""
    return value.translate(_LEET_ALT)


def base64_wrapped(value: str, width: int = 16) -> str:
    """Base64 hard-wrapped with a newline every ``width`` characters (MIME-style)."""
    raw = base64_encode(value)
    return "\n".join(raw[i : i + width] for i in range(0, len(raw), width))


def base64_encode(value: str) -> str:
    return base64.b64encode(value.encode("utf-8")).decode("ascii")


def zero_width(value: str) -> str:
    """Insert a zero-width space after every second character."""
    out = []
    for index, ch in enumerate(value):
        out.append(ch)
        if index % 2 == 1 and index != len(value) - 1:
            out.append(ZWSP)
    return "".join(out)


ENCODERS = {
    "spaced_digits": spaced_digits,
    "dashed_digits": dashed_digits,
    "spelled_digits": spelled_digits,
    "leetspeak": leetspeak,
    "base64": base64_encode,
    "zero_width": zero_width,
    "leetspeak_alt": leetspeak_alt,
    "base64_wrapped": base64_wrapped,
}
OBFUSCATIONS = tuple(ENCODERS) + ("split_sentences",)
