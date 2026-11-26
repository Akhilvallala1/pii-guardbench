"""Validate the scaffold example against the schema and format validators."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from generators.generate_prompts import (
    api_key_prefix_valid,
    generate_prompts,
    luhn_valid,
    mrn_format_valid,
    span_offsets_valid,
    ssn_valid,
    validate_record,
)

ROOT = Path(__file__).resolve().parents[1]
SCHEMA_PATH = ROOT / "schema" / "prompt.schema.json"
EXAMPLE_PATH = ROOT / "schema" / "example.jsonl"


def load_schema() -> dict:
    with SCHEMA_PATH.open(encoding="utf-8") as handle:
        return json.load(handle)


def load_examples() -> list[dict]:
    records = []
    with EXAMPLE_PATH.open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                records.append(json.loads(line))
    return records


@pytest.fixture(scope="module")
def schema() -> dict:
    return load_schema()


@pytest.fixture(scope="module")
def validator(schema: dict) -> Draft202012Validator:
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


@pytest.fixture(scope="module")
def examples() -> list[dict]:
    return load_examples()


def test_example_file_has_exactly_one_record(examples: list[dict]) -> None:
    assert len(examples) == 1


def test_example_matches_schema(validator: Draft202012Validator, examples: list[dict]) -> None:
    validator.validate(examples[0])


def test_example_offsets_and_formats(examples: list[dict]) -> None:
    record = examples[0]
    errors = validate_record(record)
    assert errors == []
    text = record["text"]
    categories = {span["category"] for span in record["sensitive_spans"]}
    assert "credit_card" in categories
    assert "ssn" in categories
    for span in record["sensitive_spans"]:
        assert text[span["start"] : span["end"]] == span["value"]
        if span["category"] == "credit_card":
            assert luhn_valid(span["value"])
        if span["category"] == "ssn":
            assert ssn_valid(span["value"])


def test_luhn_accepts_visa_test_pan() -> None:
    assert luhn_valid("4111 1111 1111 1111")
    assert luhn_valid("4111111111111111")


def test_luhn_rejects_invalid_checksum_and_junk() -> None:
    assert not luhn_valid("4111 1111 1111 1112")
    assert not luhn_valid("")
    assert not luhn_valid("4111-ABCD-1111-1111")


def test_ssn_accepts_well_known_sample_and_rejects_invalid_areas() -> None:
    assert ssn_valid("078-05-1120")
    assert ssn_valid("078051120")
    assert not ssn_valid("000-12-3456")
    assert not ssn_valid("666-12-3456")
    assert not ssn_valid("900-12-3456")
    assert not ssn_valid("999-12-3456")
    assert not ssn_valid("078-00-1120")
    assert not ssn_valid("078-05-0000")
    assert not ssn_valid("78-05-1120")


def test_mrn_stub_and_api_key_prefix() -> None:
    assert mrn_format_valid("MRN-123456")
    assert mrn_format_valid("A1234567")
    assert not mrn_format_valid("12")
    assert not mrn_format_valid("------")
    assert not mrn_format_valid("ABCDEF")
    assert api_key_prefix_valid("sk_test_abc123")
    assert api_key_prefix_valid("ghp_exampletoken")
    assert api_key_prefix_valid("xoxb-example-token")
    assert not api_key_prefix_valid("sk_live_abc123")
    assert not api_key_prefix_valid("AKIAEXAMPLE")


def test_span_offset_check() -> None:
    text = "card 4111 1111 1111 1111 end"
    start = text.index("4111 1111 1111 1111")
    end = start + len("4111 1111 1111 1111")
    assert span_offsets_valid(text, start, end, "4111 1111 1111 1111")
    assert not span_offsets_valid(text, start, end, "4111111111111111")
    assert not span_offsets_valid(text, -1, end, "4111 1111 1111 1111")


def test_generate_prompts_is_not_implemented() -> None:
    with pytest.raises(NotImplementedError):
        generate_prompts(1000)
