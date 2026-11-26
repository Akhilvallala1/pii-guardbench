"""Adapter tests. No network and no paid APIs.

Real-engine smoke tests are skipped when the optional dependency (or its
spaCy model) is not installed; the availability tests simulate a missing
dependency so they pass either way.
"""

from __future__ import annotations

import importlib.util
import sys

import pytest

from pii_guardbench.adapters import AdapterUnavailableError, get_adapter, list_adapters
from pii_guardbench.adapters.mock import MockAdapter
from pii_guardbench.adapters.nemo import NemoGuardrailsAdapter
from pii_guardbench.adapters.piiguard import PiiguardAdapter
from pii_guardbench.adapters.presidio import PresidioAdapter

CARD_PROMPT = "charge card 4111 1111 1111 1111 please"
CONTROL = "Please confirm invoice 123456 is paid."


def _installed(module: str) -> bool:
    return importlib.util.find_spec(module) is not None


def test_registry_contains_mocks_and_real_adapters() -> None:
    names = list_adapters()
    for name in (
        "mock", "mock-leaky", "mock-redact",
        "piiguard", "piiguard-presidio", "presidio", "nemo-guardrails",
    ):
        assert name in names
    leaky = get_adapter("mock-leaky")
    assert isinstance(leaky, MockAdapter)
    assert leaky.name == "mock-leaky"


def test_mock_passthrough_leaks_and_reports_configured_latency() -> None:
    adapter = MockAdapter(name="mock-leaky", behavior="passthrough", latency_ms=1.5)
    result = adapter.run(CARD_PROMPT)
    assert result.redacted_text == CARD_PROMPT
    assert result.flags.blocked is False
    assert result.latency_ms == 1.5


def test_mock_redact_removes_card_and_over_redacts_control() -> None:
    adapter = MockAdapter(name="mock-redact", behavior="redact", over_redact=True)
    card = adapter.run(CARD_PROMPT)
    control = adapter.run(CONTROL)
    assert "4111 1111 1111 1111" not in card.redacted_text
    assert "[REDACTED]" in card.redacted_text
    assert card.flags.blocked is False
    assert control.redacted_text != CONTROL
    assert "123456" not in control.redacted_text


def test_mock_block_marks_prompt_blocked() -> None:
    adapter = MockAdapter(name="mock-block", behavior="block")
    result = adapter.run(CARD_PROMPT)
    assert result.flags.blocked is True
    assert result.redacted_text == ""


def test_presidio_reports_unavailable_without_dependency(monkeypatch) -> None:
    monkeypatch.setitem(sys.modules, "presidio_analyzer", None)
    with pytest.raises(AdapterUnavailableError, match="presidio-analyzer"):
        PresidioAdapter().warmup()


def test_nemo_reports_unavailable_without_dependency(monkeypatch) -> None:
    monkeypatch.setitem(sys.modules, "nemoguardrails", None)
    with pytest.raises(AdapterUnavailableError, match="nemoguardrails"):
        NemoGuardrailsAdapter().warmup()


def test_piiguard_reports_unavailable_without_package_or_guard_dir(monkeypatch, tmp_path) -> None:
    monkeypatch.setitem(sys.modules, "piiguard", None)
    monkeypatch.setitem(sys.modules, "piiguard.pii", None)
    monkeypatch.setitem(sys.modules, "piiguard.pii.guard", None)
    with pytest.raises(AdapterUnavailableError, match="piiguard"):
        PiiguardAdapter(guard_dir=tmp_path).warmup()


@pytest.mark.skipif(not _installed("piiguard"), reason="piiguard not installed")
def test_piiguard_missing_guard_dir_message(monkeypatch, tmp_path) -> None:
    monkeypatch.delenv("PIIGUARD_GUARD_DIR", raising=False)
    with pytest.raises(AdapterUnavailableError, match="guard directory"):
        PiiguardAdapter(guard_dir=tmp_path).warmup()


@pytest.mark.skipif(not _installed("piiguard"), reason="piiguard not installed")
def test_piiguard_rules_only_with_inline_rules(tmp_path) -> None:
    (tmp_path / "rules.yaml").write_text(
        "version: 1\nrules:\n- name: ssn\n  pattern: '\\b\\d{3}-\\d{2}-\\d{4}\\b'\n",
        encoding="utf-8",
    )
    adapter = PiiguardAdapter(guard_dir=tmp_path)
    result = adapter.run("ssn 078-05-1120 ok")
    assert "078-05-1120" not in result.redacted_text
    assert "ssn" in result.flags.detected_categories
    assert adapter.describe()["piiguard_package"]


@pytest.mark.skipif(
    not (_installed("presidio_analyzer") and _installed("en_core_web_lg")),
    reason="presidio/spaCy model not installed",
)
def test_presidio_default_engine_redacts_card_and_email() -> None:
    result = PresidioAdapter().run("mail jane@example.org card 4111 1111 1111 1111")
    assert "jane@example.org" not in result.redacted_text
    assert "4111 1111 1111 1111" not in result.redacted_text


@pytest.mark.skipif(
    not (_installed("nemoguardrails") and _installed("en_core_web_lg")),
    reason="nemoguardrails/spaCy model not installed",
)
def test_nemo_pii_rail_masks_email_offline() -> None:
    result = NemoGuardrailsAdapter().run("mail jane@example.org please")
    assert "jane@example.org" not in result.redacted_text


def test_unknown_adapter_name_lists_known_options() -> None:
    with pytest.raises(KeyError, match="Unknown adapter"):
        get_adapter("definitely-not-a-real-adapter")
