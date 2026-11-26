"""Gretel converter tests on a tiny hand-written fixture. No network."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from pii_guardbench.datasets.gretel import (
    LABEL_TO_CATEGORY,
    convert_record,
    contains_secret_like,
    convert_rows,
    exclude_secret_like,
    find_occurrences,
    map_domain,
    map_label,
    parse_entities,
    stratified_sample,
)
from pii_guardbench.prompts import load_prompts, load_schema

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "tests" / "fixtures" / "gretel_tiny_raw.jsonl"
SAMPLE = ROOT / "data" / "external" / "gretel-en-v1" / "sample.jsonl"


@pytest.fixture(scope="module")
def raw_rows() -> list[dict]:
    return [json.loads(line) for line in RAW.read_text(encoding="utf-8").splitlines() if line]


@pytest.fixture(scope="module")
def converted(raw_rows):
    return convert_rows(raw_rows)


def test_every_emitted_span_matches_its_text_and_schema(converted) -> None:
    records, _ = converted
    validator = Draft202012Validator(load_schema())
    assert records
    for record in records:
        validator.validate(record)
        assert record["source"] == "adapted"
        for span in record["sensitive_spans"]:
            assert record["text"][span["start"] : span["end"]] == span["value"]


def test_category_mapping() -> None:
    assert map_label("credit_card_number") == "credit_card"
    assert map_label("medical_record_number") == "mrn"
    assert map_label("phone_number") == "phone"
    assert map_label("first_name") == "name"
    assert map_label("street_address") == "address"
    assert map_label("api_key") == "api_key"
    # Not mapped: dropped, never silently turned into "other".
    assert map_label("date_of_birth") is None
    assert map_label("ipv4") is None
    assert "other" not in LABEL_TO_CATEGORY.values()


def test_domain_mapping() -> None:
    assert map_domain("banking") == "finance"
    assert map_domain("healthcare") == "health"
    assert map_domain("cybersecurity") == "credentials"
    assert map_domain("aviation") == "none"


def test_repeated_values_get_every_occurrence_with_exact_offsets(converted) -> None:
    records, _ = converted
    first = next(r for r in records if r["id"].endswith("t1"))
    janes = [s for s in first["sensitive_spans"] if s["value"] == "Jane"]
    assert [(s["start"], s["end"]) for s in janes] == [(8, 12), (98, 102)]
    categories = {s["category"] for s in first["sensitive_spans"]}
    assert categories == {"name", "email", "credit_card", "ssn"}


def test_whole_token_matching_skips_substrings_and_short_values(converted) -> None:
    records, _ = converted
    record = next(r for r in records if r["id"].endswith("t3"))
    ann = [s for s in record["sensitive_spans"] if s["value"] == "Ann"]
    # "Annual" at the start must not be matched; only the standalone "Ann".
    assert [(s["start"], s["end"]) for s in ann] == [(15, 18)]
    assert all(s["value"] != "Al" for s in record["sensitive_spans"])
    assert find_occurrences("Annual Ann", "Ann") == [(7, 10)]


def test_conversion_stats(converted) -> None:
    records, stats = converted
    assert stats.records_in == 5
    assert stats.records_kept == len(records) == 3
    assert stats.records_dropped_no_mapped_entities == 1  # only date/ipv4 labels
    assert stats.records_dropped_unparseable == 1
    assert stats.entities_too_short == 1
    assert stats.entities_not_found_in_text == 1
    assert stats.entities_unmapped_label["date"] == 2
    assert stats.entities_unmapped_label["ipv4"] == 1
    assert stats.spans_failed_offset_check == 0
    assert stats.spans_emitted == sum(len(r["sensitive_spans"]) for r in records)
    assert stats.spans_by_category["ssn"] == 1


def test_records_without_mapped_labels_are_dropped_not_made_controls(raw_rows) -> None:
    only_unmapped = next(r for r in raw_rows if r["uid"] == "t2")
    assert convert_record(only_unmapped) is None


def test_parse_entities_handles_python_literal_and_bad_input() -> None:
    assert parse_entities("[{'entity': 'x', 'types': ['ssn']}]")[0]["entity"] == "x"
    with pytest.raises((ValueError, SyntaxError)):
        parse_entities("not a literal [")


def test_sampling_is_seeded_and_applies_category_floor(converted) -> None:
    records, _ = converted
    big = []
    for index in range(60):
        base = json.loads(json.dumps(records[index % len(records)]))
        base["id"] = f"{base['id']}-{index}"
        big.append(base)
    a = stratified_sample(big, 20, seed=7, min_per_category=2)
    b = stratified_sample(big, 20, seed=7, min_per_category=2)
    c = stratified_sample(big, 20, seed=8, min_per_category=2)
    assert [r["id"] for r in a] == [r["id"] for r in b]
    assert [r["id"] for r in a] != [r["id"] for r in c]
    assert len(a) == 20
    for category in ("ssn", "api_key", "mrn"):
        assert sum(any(s["category"] == category for s in r["sensitive_spans"]) for r in a) >= 2


def test_external_records_load_without_generated_set_format_rules() -> None:
    # Real data has API keys without sk_test_ prefixes; strict checks would reject it.
    with pytest.raises(ValueError, match="format checks"):
        load_prompts(_write_real_style_key(), check_formats=True)
    assert load_prompts(_write_real_style_key(), check_formats=False)


def _write_real_style_key(_cache: dict = {}) -> Path:
    import tempfile

    if "path" not in _cache:
        text = "key AKIAEXAMPLE1234567 here"
        record = {
            "id": "x",
            "text": text,
            "sensitive_spans": [
                {"category": "api_key", "start": 4, "end": 22, "value": "AKIAEXAMPLE1234567"}
            ],
            "difficulty": "medium",
            "source": "adapted",
            "domain": "none",
        }
        handle = tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False, encoding="utf-8")
        handle.write(json.dumps(record) + "\n")
        handle.close()
        _cache["path"] = Path(handle.name)
    return _cache["path"]


def test_committed_sample_is_valid_and_documented() -> None:
    records = load_prompts(SAMPLE, check_formats=False)
    assert 300 <= len(records) <= 500
    for record in records:
        for span in record["sensitive_spans"]:
            assert record["text"][span["start"] : span["end"]] == span["value"]
    folder = SAMPLE.parent
    assert (folder / "NOTICE").exists() and (folder / "README.md").exists()
    stats = json.loads((folder / "conversion_stats.json").read_text(encoding="utf-8"))
    assert stats["dataset"] == "gretelai/gretel-pii-masking-en-v1"
    assert stats["sample_size"] == len(records)


def test_provider_token_shaped_values_are_excluded_from_the_committed_sample() -> None:
    fake_sendgrid = "SG." + "a" * 22 + "." + "b" * 43
    assert contains_secret_like(f"key {fake_sendgrid} here")
    assert contains_secret_like("AKIA" + "A" * 16)
    assert not contains_secret_like("password hunter2hunter2 and sk_test_example123")
    records = [
        {"id": "a", "text": f"use {fake_sendgrid}"},
        {"id": "b", "text": "nothing secret"},
    ]
    kept, excluded = exclude_secret_like(records)
    assert [r["id"] for r in kept] == ["b"] and excluded == 1
