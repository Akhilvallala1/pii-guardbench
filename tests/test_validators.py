"""Robustness: ASCII-only digit validators, NFC offsets, unique ids, warnings."""

from __future__ import annotations

import json
import unicodedata
from pathlib import Path

import pytest

from generators.generate_prompts import (
    icd10_format_valid,
    luhn_valid,
    provider_token_shaped,
    span_offsets_valid,
    ssn_valid,
    validate_record,
    validation_warnings,
)
from pii_guardbench.prompts import PublicTestValueWarning, load_prompts


def _write(tmp_path: Path, records: list[dict]) -> Path:
    path = tmp_path / "p.jsonl"
    path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in records), encoding="utf-8")
    return path


def _rec(rid: str, text: str, spans: list[tuple[str, str]], **kw) -> dict:
    out = []
    for cat, v in spans:
        s = text.index(v)
        out.append({"category": cat, "start": s, "end": s + len(v), "value": v})
    return {"id": rid, "text": text, "sensitive_spans": out, "difficulty": "easy",
            "source": "generated", "domain": "finance", **kw}


def test_luhn_rejects_fullwidth_and_surrounding_whitespace() -> None:
    assert luhn_valid("5289 6655 3719 3154")
    assert luhn_valid("5289-6655-3719-3154")
    assert not luhn_valid("５２８９６６５５３７１９３１５４")
    assert not luhn_valid("5289 6655 3719 3154 ")
    assert not luhn_valid(" 5289665537193154")
    assert not luhn_valid("5289  6655 3719 3154")  # double separator
    assert not luhn_valid("٥٢٨٩٦٦٥٥٣٧١٩٣١٥٤")  # Arabic-Indic digits


def test_ssn_rejects_fullwidth_and_whitespace() -> None:
    assert ssn_valid("790-18-3465")
    assert not ssn_valid("７９０-１８-３４６５")
    assert not ssn_valid(" 790-18-3465")
    assert not ssn_valid("790-18-3465\n")


def test_icd10_and_provider_token_checks() -> None:
    assert icd10_format_valid("E11.9") and icd10_format_valid("I10") and icd10_format_valid("J45.909")
    assert not icd10_format_valid("U07.1") and not icd10_format_valid("E1.9") and not icd10_format_valid("diabetes")
    assert provider_token_shaped("sk_live_" + "a" * 24)
    assert provider_token_shaped("ghp_" + "A1" * 18)
    assert not provider_token_shaped("sk_test_FAKE_4753rw_3mpw3v_pgb")
    assert not provider_token_shaped("ghp_FAKE_abc123_def456_pgb")


def test_known_public_test_values_warn() -> None:
    rec = _rec("x", "card 4111 1111 1111 1111 ssn 078-05-1120",
               [("credit_card", "4111 1111 1111 1111"), ("ssn", "078-05-1120")])
    assert validate_record(rec) == []
    warns = validation_warnings(rec)
    assert len(warns) == 2 and all("public test value" in w for w in warns)
    fresh = _rec("y", "card 5289 6655 3719 3154", [("credit_card", "5289 6655 3719 3154")])
    assert validation_warnings(fresh) == []


def test_loader_warns_on_public_values(tmp_path: Path) -> None:
    rec = _rec("x", "card 4111 1111 1111 1111", [("credit_card", "4111 1111 1111 1111")])
    with pytest.warns(PublicTestValueWarning):
        load_prompts(_write(tmp_path, [rec]))


def test_duplicate_ids_are_rejected(tmp_path: Path) -> None:
    a = _rec("dup", "ssn 790-18-3465", [("ssn", "790-18-3465")])
    b = _rec("dup", "ssn 893-80-5601", [("ssn", "893-80-5601")])
    with pytest.raises(ValueError, match="duplicate id 'dup'"):
        load_prompts(_write(tmp_path, [a, b]))


def test_nfd_text_is_normalized_and_offsets_remapped(tmp_path: Path) -> None:
    nfd_text = unicodedata.normalize("NFD", "Café patient José, ssn 790-18-3465.")
    value = "790-18-3465"
    start = nfd_text.index(value)  # NFD offset: two extra combining marks earlier
    rec = {"id": "u", "text": nfd_text, "difficulty": "easy", "source": "generated", "domain": "health",
           "sensitive_spans": [{"category": "ssn", "start": start, "end": start + len(value), "value": value}]}
    assert "not NFC" in " ".join(validate_record(rec))
    [loaded] = load_prompts(_write(tmp_path, [rec]))
    assert loaded["text"] == unicodedata.normalize("NFC", nfd_text)
    span = loaded["sensitive_spans"][0]
    assert span["start"] == start - 2
    assert loaded["text"][span["start"]:span["end"]] == value


def test_empty_and_overlapping_spans_rejected() -> None:
    assert not span_offsets_valid("abc", 1, 1, "")
    rec = _rec("o", "ssn 790-18-3465", [("ssn", "790-18-3465")])
    rec["sensitive_spans"].append({"category": "other", "start": 6, "end": 9, "value": "0-1"})
    assert any("overlap" in e for e in validate_record(rec))


def test_obfuscation_must_match_canonical() -> None:
    text = "ssn 7 9 0 1 8 3 4 6 5"
    surface = "7 9 0 1 8 3 4 6 5"
    s = text.index(surface)
    good = {"id": "h", "text": text, "sensitive_spans": [{"category": "ssn", "start": s, "end": s + len(surface),
            "value": surface, "canonical_value": "790-18-3465", "obfuscation": "spaced_digits"}]}
    assert validate_record(good) == []
    bad = json.loads(json.dumps(good))
    bad["sensitive_spans"][0]["canonical_value"] = "893-80-5601"
    assert any("spaced_digits" in e for e in validate_record(bad))
