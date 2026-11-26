"""Register the built-in adapters. Imported from the adapters package init."""

from __future__ import annotations

from pii_guardbench.adapters.mock import MockAdapter
from pii_guardbench.adapters.nemo import NemoGuardrailsAdapter
from pii_guardbench.adapters.gliner_pii import GlinerPiiAdapter
from pii_guardbench.adapters.llm_guard import LlmGuardAdapter
from pii_guardbench.adapters.piiguard import PiiguardAdapter, PiiguardUnionPresidioAdapter
from pii_guardbench.adapters.presidio import NEMO_EXAMPLE_ENTITIES, PresidioAdapter
from pii_guardbench.adapters.registry import register_adapter


def _mock() -> MockAdapter:
    return MockAdapter(name="mock", behavior="passthrough")


def _mock_leaky() -> MockAdapter:
    return MockAdapter(name="mock-leaky", behavior="passthrough")


def _mock_redact() -> MockAdapter:
    return MockAdapter(
        name="mock-redact",
        behavior="redact",
        over_redact=True,
    )


def _mock_block() -> MockAdapter:
    return MockAdapter(name="mock-block", behavior="block")


def _piiguard_presidio() -> PiiguardAdapter:
    return PiiguardAdapter(with_presidio=True, name="piiguard-pgpresidio")


register_adapter("mock", _mock)
register_adapter("mock-leaky", _mock_leaky)
register_adapter("mock-redact", _mock_redact)
register_adapter("mock-block", _mock_block)
register_adapter("piiguard", PiiguardAdapter)
register_adapter("piiguard-pgpresidio", _piiguard_presidio)
register_adapter("piiguard-presidio", _piiguard_presidio, alias_of="piiguard-pgpresidio")
register_adapter("piiguard-union-presidio", PiiguardUnionPresidioAdapter)

register_adapter("presidio", PresidioAdapter)
register_adapter(
    "presidio-all-t0.4", lambda: PresidioAdapter(score_threshold=0.4, name="presidio-all-t0.4")
)
register_adapter(
    "presidio-nemo6-t0",
    lambda: PresidioAdapter(entities=NEMO_EXAMPLE_ENTITIES, score_threshold=0.0, name="presidio-nemo6-t0"),
)
register_adapter(
    "presidio-nemo6-t0.4",
    lambda: PresidioAdapter(entities=NEMO_EXAMPLE_ENTITIES, score_threshold=0.4, name="presidio-nemo6-t0.4"),
)

register_adapter("nemo-guardrails", NemoGuardrailsAdapter)
register_adapter("nemo", NemoGuardrailsAdapter, alias_of="nemo-guardrails")
register_adapter(
    "nemo-guardrails-all",
    lambda: NemoGuardrailsAdapter(entities="all", name="nemo-guardrails-all"),
)
register_adapter(
    "nemo-guardrails-detect",
    lambda: NemoGuardrailsAdapter(mode="detect", score_threshold=0.4, name="nemo-guardrails-detect"),
)

register_adapter("gliner-pii", GlinerPiiAdapter)
register_adapter("llm-guard-anonymize", LlmGuardAdapter)
register_adapter("llm-guard-block", lambda: LlmGuardAdapter(block_on_invalid=True))
