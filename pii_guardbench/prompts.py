"""Load and validate JSONL prompt records against the schema and format checks.

Unicode handling: every record's ``text`` (and each span ``value`` /
``canonical_value``) is normalized to NFC on load. If the stored text was not
NFC, span offsets are remapped as ``start' = len(NFC(text[:start]))`` and
``end' = start' + len(NFC(value))``, and the offsets are then validated against
the normalized text (``text[start:end] == value``). Offsets are therefore
Python code-point offsets into NFC text, not bytes, UTF-16 units, or graphemes.
"""

from __future__ import annotations

import json
import unicodedata
import warnings
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

from generators.generate_prompts import span_offsets_valid, validate_record, validation_warnings

SCHEMA_PATH = Path(__file__).resolve().parents[1] / "schema" / "prompt.schema.json"


class PublicTestValueWarning(UserWarning):
    """A record uses a famous public test value or a provider-shaped token."""


def load_schema(path: Path | None = None) -> dict[str, Any]:
    schema_path = path or SCHEMA_PATH
    with schema_path.open(encoding="utf-8") as handle:
        schema = json.load(handle)
    Draft202012Validator.check_schema(schema)
    return schema


def nfc_normalize_record(record: dict[str, Any]) -> dict[str, Any]:
    """Return a copy with NFC text/values and offsets remapped to the NFC text."""
    text = record.get("text")
    if not isinstance(text, str):
        return record
    nfc = unicodedata.normalize
    out = dict(record)
    out["text"] = nfc("NFC", text)
    spans = []
    for span in record.get("sensitive_spans") or []:
        new = dict(span)
        for key in ("value", "canonical_value"):
            if isinstance(new.get(key), str):
                new[key] = nfc("NFC", new[key])
        start, end = span.get("start"), span.get("end")
        if out["text"] != text and isinstance(start, int) and isinstance(end, int) and 0 <= start <= end <= len(text):
            new["start"] = len(nfc("NFC", text[:start]))
            new["end"] = new["start"] + len(nfc("NFC", text[start:end]))
        spans.append(new)
    out["sensitive_spans"] = spans
    return out


def load_prompts(
    path: str | Path,
    *,
    schema_path: Path | None = None,
    check_formats: bool = True,
) -> list[dict[str, Any]]:
    """Load a JSONL file of prompt records, validating schema and span offsets.

    With ``check_formats=True`` (default) category formats are enforced as well
    (Luhn cards, SSN area rules, MRN, ICD-10 shape, key prefixes, obfuscation
    consistency), which suits the generated set. External real-data samples use
    ``check_formats=False``: only the schema and ``text[start:end] == value``
    are enforced. Duplicate ``id`` values are always an error. Famous public
    test values and provider-shaped tokens raise a :class:`PublicTestValueWarning`.
    """
    validator = Draft202012Validator(load_schema(schema_path))
    records: list[dict[str, Any]] = []
    seen_ids: dict[str, int] = {}
    prompt_path = Path(path)
    with prompt_path.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            stripped = line.strip()
            if not stripped:
                continue
            try:
                record = json.loads(stripped)
            except json.JSONDecodeError as exc:
                raise ValueError(
                    f"{prompt_path}:{line_number} is not valid JSON: {exc}"
                ) from exc
            errors = sorted(validator.iter_errors(record), key=lambda err: list(err.path))
            if errors:
                first = errors[0]
                location = ".".join(str(part) for part in first.path) or "(root)"
                raise ValueError(
                    f"{prompt_path}:{line_number} failed schema validation at "
                    f"{location}: {first.message}"
                )
            record_id = record["id"]
            if record_id in seen_ids:
                raise ValueError(
                    f"{prompt_path}:{line_number} duplicate id {record_id!r} "
                    f"(first seen on line {seen_ids[record_id]})"
                )
            seen_ids[record_id] = line_number
            record = nfc_normalize_record(record)
            format_errors = (
                validate_record(record)
                if check_formats
                else _offset_errors(record)
            )
            if format_errors:
                raise ValueError(
                    f"{prompt_path}:{line_number} failed format checks: "
                    + "; ".join(format_errors)
                )
            for message in validation_warnings(record):
                warnings.warn(
                    f"{prompt_path}:{line_number} {message}", PublicTestValueWarning, stacklevel=2
                )
            records.append(record)
    if not records:
        raise ValueError(f"{prompt_path} contains no prompt records.")
    return records


def _offset_errors(record: dict[str, Any]) -> list[str]:
    text = record["text"]
    errors = []
    for index, span in enumerate(record["sensitive_spans"]):
        if not span_offsets_valid(text, span["start"], span["end"], span["value"]):
            errors.append(f"span[{index}]: text[{span['start']}:{span['end']}] != value")
    return errors
