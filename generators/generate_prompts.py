#!/usr/bin/env python3
"""Placeholder CLI and format validators for PII-GuardBench prompts.

Generation of the planned ~1,000-prompt set is not implemented yet.
The validators in this module are implemented and are used by the tests.
"""

from __future__ import annotations

import argparse
import re
import sys
from typing import Any, Iterable, Mapping, Sequence

API_KEY_PREFIXES: tuple[str, ...] = ("sk_test_", "ghp_", "xoxb-")

# Stub: 6–16 characters, alphanumeric plus optional internal hyphens, at least
# one digit. Tighten this once the generator's MRN policy is decided.
_MRN_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{4,14})[A-Za-z0-9]$")
_SSN_RE = re.compile(r"^(\d{3})-?(\d{2})-?(\d{4})$")


def luhn_valid(number: str) -> bool:
    """Return True if `number` (spaces and hyphens ignored) passes a Luhn check."""
    digits = [int(ch) for ch in number if ch.isdigit()]
    if not digits or any(not ch.isdigit() and ch not in " -" for ch in number):
        return False
    checksum = 0
    for index, digit in enumerate(reversed(digits)):
        if index % 2 == 1:
            digit *= 2
            if digit > 9:
                digit -= 9
        checksum += digit
    return checksum % 10 == 0


def ssn_valid(ssn: str) -> bool:
    """Return True if `ssn` has a structurally valid (not necessarily issued) SSN.

    Area cannot be 000, 666, or 900–999; group cannot be 00; serial cannot be
    0000. This is a format check only. Well-known invalid samples such as
    078-05-1120 are treated as format-valid.
    """
    match = _SSN_RE.fullmatch(ssn.strip())
    if not match:
        return False
    area = int(match.group(1))
    group = int(match.group(2))
    serial = int(match.group(3))
    if area == 0 or area == 666 or 900 <= area <= 999:
        return False
    if group == 0 or serial == 0:
        return False
    return True


def mrn_format_valid(mrn: str) -> bool:
    """Stub medical-record-number format check.

    Accepts 6–16 character identifiers made of letters, digits, and optional
    internal hyphens, with at least one digit. This is a placeholder and will
    be replaced by a tighter policy when generation is implemented.
    """
    value = mrn.strip()
    if not _MRN_RE.fullmatch(value):
        return False
    return any(ch.isdigit() for ch in value)


def api_key_prefix_valid(value: str) -> bool:
    """Return True if `value` starts with a documented test/key prefix."""
    return value.startswith(API_KEY_PREFIXES)


def span_offsets_valid(text: str, start: int, end: int, value: str) -> bool:
    """Return True if `text[start:end] == value` with well-ordered offsets."""
    if not isinstance(start, int) or not isinstance(end, int):
        return False
    if start < 0 or end < start or end > len(text):
        return False
    return text[start:end] == value


_CATEGORY_VALIDATORS = {
    "credit_card": luhn_valid,
    "ssn": ssn_valid,
    "mrn": mrn_format_valid,
    "api_key": api_key_prefix_valid,
    "token": api_key_prefix_valid,
}


def validate_spans(text: str, spans: Iterable[Mapping[str, Any]]) -> list[str]:
    """Return a list of validation error messages for `spans` against `text`."""
    errors: list[str] = []
    for index, span in enumerate(spans):
        prefix = f"span[{index}]"
        start = span.get("start")
        end = span.get("end")
        value = span.get("value")
        category = span.get("category")
        if not isinstance(value, str):
            errors.append(f"{prefix}: value must be a string")
            continue
        if not span_offsets_valid(text, start, end, value):  # type: ignore[arg-type]
            errors.append(
                f"{prefix}: text[{start}:{end}] != value {value!r}"
            )
            continue
        validator = _CATEGORY_VALIDATORS.get(category)
        if validator is not None and not validator(value):
            errors.append(
                f"{prefix}: value {value!r} failed the {category} format check"
            )
    return errors


def validate_record(record: Mapping[str, Any]) -> list[str]:
    """Validate offsets and category-specific formats on one prompt record."""
    text = record.get("text")
    spans = record.get("sensitive_spans")
    if not isinstance(text, str):
        return ["text must be a string"]
    if not isinstance(spans, Sequence) or isinstance(spans, (str, bytes)):
        return ["sensitive_spans must be an array"]
    return validate_spans(text, spans)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Generate PII-GuardBench prompts with planted synthetic values. "
            "Generation is not implemented in this scaffold."
        )
    )
    parser.add_argument(
        "-n",
        "--n",
        dest="n",
        type=int,
        default=1000,
        metavar="N",
        help="Number of prompts to generate (default: 1000). Not implemented.",
    )
    return parser


def generate_prompts(n: int) -> list[dict[str, Any]]:
    """Reserved entry point for prompt generation.

    TODO: Implement generation of about 1,000 prompts with planted fake
    values, split across finance, health, and credentials, plus no-PII
    controls. Do not copy third-party datasets into the shipped set.
    """
    raise NotImplementedError(
        "Prompt generation is not implemented in this scaffold. "
        f"Asked to generate {n} prompts; the planned set is about 1,000. "
        "Use the validators in this module, and do not generate the dataset yet."
    )


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.n < 1:
        print("error: --n must be a positive integer", file=sys.stderr)
        return 2
    generate_prompts(args.n)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except NotImplementedError as exc:
        print(f"Not implemented: {exc}", file=sys.stderr)
        raise SystemExit(1)
