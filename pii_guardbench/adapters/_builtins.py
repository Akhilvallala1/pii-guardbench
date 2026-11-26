"""Register the built-in adapters. Imported from the adapters package init."""

from __future__ import annotations

from pii_guardbench.adapters.mock import MockAdapter
from pii_guardbench.adapters.nemo import NemoGuardrailsAdapter
from pii_guardbench.adapters.piiguard import PiiguardAdapter
from pii_guardbench.adapters.presidio import PresidioAdapter
from pii_guardbench.adapters.registry import register_adapter


def _mock() -> MockAdapter:
    return MockAdapter(name="mock", behavior="passthrough")


def _mock_leaky() -> MockAdapter:
    return MockAdapter(name="mock-leaky", behavior="passthrough", latency_ms=1.5)


def _mock_redact() -> MockAdapter:
    return MockAdapter(
        name="mock-redact",
        behavior="redact",
        over_redact=True,
        latency_ms=8.0,
    )


register_adapter("mock", _mock)
register_adapter("mock-leaky", _mock_leaky)
register_adapter("mock-redact", _mock_redact)
register_adapter("piiguard", PiiguardAdapter)
register_adapter("presidio", PresidioAdapter)
register_adapter("nemo-guardrails", NemoGuardrailsAdapter)
register_adapter("nemo", NemoGuardrailsAdapter)
