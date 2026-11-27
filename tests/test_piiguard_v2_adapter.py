"""piiguard-v2 adapters: skip cleanly, contract derived from rules, filtered union whitelist."""
from __future__ import annotations

import importlib.util
import os
from pathlib import Path

import pytest

from pii_guardbench.adapters import AdapterUnavailableError, get_adapter
from pii_guardbench.adapters.piiguard_v2 import (
    UNION_FILTERED_WHITELIST,
    PiiguardV2Adapter,
    PiiguardV2UnionFilteredAdapter,
)

HAS_PIIGUARD = importlib.util.find_spec("piiguard") is not None
V2_DIR = os.environ.get("PIIGUARD_V2_GUARD_DIR")

RULES = """version: 1
rules:
- name: ssn
  pattern: '\\b\\d{3}-\\d{2}-\\d{4}\\b'
- name: password_context
  pattern: '(?<=password is )\\S+'
"""


def test_registered() -> None:
    assert get_adapter("piiguard-v2").name == "piiguard-v2"
    assert get_adapter("piiguard-v2-union-filtered").name == "piiguard-v2-union-filtered"


def test_unavailable_without_guard_dir(monkeypatch) -> None:
    monkeypatch.delenv("PIIGUARD_V2_GUARD_DIR", raising=False)
    with pytest.raises(AdapterUnavailableError):
        PiiguardV2Adapter().warmup()


def test_whitelist_excludes_noisy_types() -> None:
    assert "DATE_TIME" not in UNION_FILTERED_WHITELIST and "US_DRIVER_LICENSE" not in UNION_FILTERED_WHITELIST
    assert set(UNION_FILTERED_WHITELIST) == {"CREDIT_CARD", "EMAIL_ADDRESS", "IBAN_CODE"}


@pytest.mark.skipif(not HAS_PIIGUARD, reason="piiguard not installed")
def test_contract_follows_rules(tmp_path: Path) -> None:
    (tmp_path / "rules.yaml").write_text(RULES)
    a = PiiguardV2Adapter(tmp_path)
    assert a.entity_contract() == {"ssn", "password"}
    out = a.run("my password is hunter2! and ssn 801-23-4567")
    assert "hunter2!" not in out.redacted_text and "801-23-4567" not in out.redacted_text
    assert a.category_map()["password_context"] == "password"


@pytest.mark.skipif(not V2_DIR, reason="PIIGUARD_V2_GUARD_DIR not set")
def test_frozen_v2_rules_contract_and_behaviour() -> None:
    a = PiiguardV2Adapter()
    assert a.entity_contract() == {"ssn", "email", "phone", "credit_card", "api_key", "token",
                                   "password", "mrn", "diagnosis", "account_number", "name"}
    red = a.run("Patient Zora Quill, MRN-12345678, dx E11.9, key sk_test_abcdef123").redacted_text
    for v in ("Zora Quill", "MRN-12345678", "E11.9", "sk_test_abcdef123"):
        assert v not in red
    # lookalikes stay untouched
    for ctl in ("Build 912-34-5678 failed.", "Order 1234 5678 9012 3456 shipped.", "Room E11 on floor 3."):
        assert a.run(ctl).redacted_text == ctl


def _has_presidio() -> bool:
    return importlib.util.find_spec("presidio_analyzer") is not None


@pytest.mark.skipif(not (V2_DIR and _has_presidio()), reason="needs PIIGUARD_V2_GUARD_DIR and presidio")
def test_union_filtered_drops_date_time() -> None:
    a = PiiguardV2UnionFilteredAdapter()
    text = "The meeting is on Tuesday 5 March at noon."
    res = a.run(text)
    assert res.redacted_text == text
    assert "email" in a.entity_contract()
