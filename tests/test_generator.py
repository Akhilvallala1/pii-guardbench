"""Seeded generator: determinism, layers, validity, and the committed demo."""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

import pytest

from generators.generate_prompts import (
    generate_prompts,
    main,
    provider_token_shaped,
    validate_record,
    validation_warnings,
)
from pii_guardbench.prompts import load_prompts

ROOT = Path(__file__).resolve().parents[1]
DEMO = ROOT / "tests" / "fixtures" / "demo_prompts_seed7.jsonl"


def test_deterministic_for_seed() -> None:
    assert generate_prompts(60, seed=11) == generate_prompts(60, seed=11)
    assert generate_prompts(60, seed=11) != generate_prompts(60, seed=12)


@pytest.fixture(scope="module")
def big() -> list[dict]:
    return generate_prompts(500, seed=5)


def test_layers_domains_and_obfuscations_are_covered(big: list[dict]) -> None:
    layers = Counter(r["metadata"]["layer"] for r in big)
    assert layers == {"clean": 300, "control": 100, "hard": 100}
    assert {r["domain"] for r in big if r["metadata"]["layer"] == "clean"} == {"finance", "health", "credentials"}
    obfs = {r["metadata"]["obfuscation"] for r in big if r["metadata"]["layer"] == "hard"}
    assert obfs == {"spaced_digits", "dashed_digits", "spelled_digits", "split_sentences",
                    "leetspeak", "base64", "zero_width", "leetspeak_alt", "base64_wrapped"}
    cats = {s["category"] for r in big for s in r["sensitive_spans"]}
    assert {"credit_card", "ssn", "mrn", "diagnosis", "api_key", "token", "password"} <= cats


def test_every_record_valid_unique_and_clean(big: list[dict]) -> None:
    assert len({r["id"] for r in big}) == len(big)
    assert len({r["text"] for r in big}) == len(big)
    for r in big:
        assert validate_record(r) == [], r["id"]
        assert validation_warnings(r) == [], r["id"]
        assert not provider_token_shaped(r["text"]), r["id"]
        if r["metadata"]["layer"] == "hard":
            assert r["difficulty"] == "adversarial"
            assert all("canonical_value" in s and "obfuscation" in s for s in r["sensitive_spans"])
        if r["metadata"]["layer"] == "control":
            assert r["sensitive_spans"] == [] and r["source"] == "control"


def test_unique_identifier_values(big: list[dict]) -> None:
    seen = Counter()
    for r in big:
        for s in r["sensitive_spans"]:
            if s["category"] in {"credit_card", "ssn", "account_number", "api_key", "token", "mrn"}:
                seen[s.get("canonical_value") or s["value"]] += 1
    dupes = {v: n for v, n in seen.items() if n > 1}
    # split_sentences plants one value as two fragments of the same canonical value.
    assert all(n == 2 for n in dupes.values())


def test_demo_fixture_is_regenerable_and_loads(tmp_path: Path) -> None:
    records = load_prompts(DEMO)
    assert len(records) == 30
    assert records == generate_prompts(30, seed=7)
    out = tmp_path / "g.jsonl"
    assert main(["--n", "30", "--seed", "7", "--out", str(out)]) == 0
    assert out.read_text(encoding="utf-8") == DEMO.read_text(encoding="utf-8")


def test_cli_rejects_bad_n() -> None:
    assert main(["--n", "0"]) == 2
