"""Mock and stub adapter tests. No network or real guardrail APIs."""

from __future__ import annotations

import pytest

from pii_guardbench.adapters import get_adapter, list_adapters
from pii_guardbench.adapters.mock import MockAdapter
from pii_guardbench.adapters.nemo import NemoGuardrailsAdapter
from pii_guardbench.adapters.piiguard import PiiguardAdapter
from pii_guardbench.adapters.presidio import PresidioAdapter

CARD_PROMPT = "charge card 4111 1111 1111 1111 please"
CONTROL = "Please confirm invoice 123456 is paid."


def test_registry_contains_mocks_and_stubs() -> None:
    names = list_adapters()
    for name in ("mock", "mock-leaky", "mock-redact", "piiguard", "presidio", "nemo-guardrails"):
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


@pytest.mark.parametrize(
    "adapter_cls,request_payload",
    [
        (PiiguardAdapter, {"text": "hello"}),
        (PresidioAdapter, {"text": "hello", "language": "en"}),
        (NemoGuardrailsAdapter, {"prompt": "hello"}),
    ],
)
def test_stub_adapters_raise_without_calling_a_service(
    adapter_cls: type, request_payload: dict
) -> None:
    adapter = adapter_cls()
    with pytest.raises(NotImplementedError, match="not installed|stub"):
        adapter.call(request_payload)


def test_unknown_adapter_name_lists_known_options() -> None:
    with pytest.raises(KeyError, match="Unknown adapter"):
        get_adapter("definitely-not-a-real-adapter")
