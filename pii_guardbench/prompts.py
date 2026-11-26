"""Load and validate JSONL prompt records against the schema and format checks."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

from generators.generate_prompts import validate_record

SCHEMA_PATH = Path(__file__).resolve().parents[1] / "schema" / "prompt.schema.json"


def load_schema(path: Path | None = None) -> dict[str, Any]:
    schema_path = path or SCHEMA_PATH
    with schema_path.open(encoding="utf-8") as handle:
        schema = json.load(handle)
    Draft202012Validator.check_schema(schema)
    return schema


def load_prompts(
    path: str | Path,
    *,
    schema_path: Path | None = None,
) -> list[dict[str, Any]]:
    """Load a JSONL file of prompt records, validating schema and span offsets."""
    validator = Draft202012Validator(load_schema(schema_path))
    records: list[dict[str, Any]] = []
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
            format_errors = validate_record(record)
            if format_errors:
                raise ValueError(
                    f"{prompt_path}:{line_number} failed format checks: "
                    + "; ".join(format_errors)
                )
            records.append(record)
    if not records:
        raise ValueError(f"{prompt_path} contains no prompt records.")
    return records
