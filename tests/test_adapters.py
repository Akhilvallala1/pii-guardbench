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

CARD_PROMPT = "charge card 5289 6655 3719 3154 please"
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


def test_mock_passthrough_leaks_and_latency_is_measured_not_hard_coded() -> None:
    adapter = MockAdapter(name="mock-leaky", behavior="passthrough", latency_ms=5.0)
    result = adapter.run(CARD_PROMPT)
    assert result.redacted_text == CARD_PROMPT
    assert result.flags.blocked is False
    # Measured wall time includes the simulated 5 ms delay, so it is >= 5 and
    # (almost surely) not exactly 5.0.
    assert result.latency_ms >= 5.0
    fast = MockAdapter(name="fast", behavior="passthrough").run(CARD_PROMPT)
    assert 0.0 <= fast.latency_ms < 5.0


def test_mock_rejects_negative_latency() -> None:
    with pytest.raises(ValueError):
        MockAdapter(latency_ms=-1)


def test_mock_redact_removes_card_and_over_redacts_control() -> None:
    adapter = MockAdapter(name="mock-redact", behavior="redact", over_redact=True)
    card = adapter.run(CARD_PROMPT)
    control = adapter.run(CONTROL)
    assert "5289 6655 3719 3154" not in card.redacted_text
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
    from pii_guardbench.adapters import presidio

    # The per-process shared AnalyzerEngine would otherwise hide the missing import.
    monkeypatch.setattr(presidio, "_SHARED", {})
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
    result = adapter.run("ssn 790-18-3465 ok")
    assert "790-18-3465" not in result.redacted_text
    assert "ssn" in result.flags.detected_categories
    assert adapter.describe()["piiguard_package"]


@pytest.mark.skipif(
    not (_installed("presidio_analyzer") and _installed("en_core_web_lg")),
    reason="presidio/spaCy model not installed",
)
def test_presidio_default_engine_redacts_card_and_email() -> None:
    result = PresidioAdapter().run("mail jane@example.org card 5289 6655 3719 3154")
    assert "jane@example.org" not in result.redacted_text
    assert "5289 6655 3719 3154" not in result.redacted_text


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


def test_registry_has_matched_config_ablations_and_honest_names() -> None:
    names = set(list_adapters())
    assert {"presidio-nemo6-t0", "presidio-nemo6-t0.4", "presidio-all-t0.4", "nemo-guardrails-all",
            "nemo-guardrails-detect", "piiguard-pgpresidio", "piiguard-union-presidio",
            "gliner-pii", "llm-guard-anonymize", "llm-guard-block"} <= names
    # Old name still resolves, to the honestly named adapter.
    assert get_adapter("piiguard-presidio").name == "piiguard-pgpresidio"


def test_entity_contracts_are_declared_per_config() -> None:
    from pii_guardbench.adapters.presidio import NEMO_EXAMPLE_ENTITIES, contract_for_entities

    assert get_adapter("mock-leaky").entity_contract() is None
    nemo6 = contract_for_entities(NEMO_EXAMPLE_ENTITIES)
    assert nemo6 == {"name", "email", "phone", "credit_card", "ssn", "address"} - ({"address"} - nemo6)
    assert get_adapter("presidio-nemo6-t0").entity_contract() == nemo6
    assert get_adapter("nemo-guardrails").entity_contract() == nemo6
    assert get_adapter("piiguard").entity_contract() == {"ssn", "email", "phone", "credit_card"}
    assert get_adapter("gliner-pii").entity_contract() >= {"name", "email", "api_key", "password", "mrn"}


def test_union_replace_spans_merges_overlaps() -> None:
    from pii_guardbench.adapters.piiguard import _replace_spans

    text = "a 123-45-6789 b jane@example.org c"
    spans = [(2, 13), (5, 9), (16, 32)]
    assert _replace_spans(text, spans, "<PII>") == "a <PII> b <PII> c"
    assert _replace_spans(text, [], "<PII>") == text


def test_nemo_detect_mode_blocks_with_refusal() -> None:
    from pii_guardbench.adapters.base import BLOCK_MESSAGE

    # Parse the raw shape call() returns in detect mode (no NeMo needed).
    adapter = NemoGuardrailsAdapter(mode="detect")
    result = adapter.parse({"text": BLOCK_MESSAGE, "original": "mail jane@example.org", "blocked": True})
    assert result.flags.blocked and result.redacted_text == BLOCK_MESSAGE
    clean = adapter.parse({"text": "hello", "original": "hello", "blocked": False})
    assert not clean.flags.blocked and clean.redacted_text == "hello"
    assert adapter.name == "nemo-guardrails-detect" or adapter.mode == "detect"


def test_llm_guard_parse_blocks_only_in_block_variant() -> None:
    from pii_guardbench.adapters.base import BLOCK_MESSAGE
    from pii_guardbench.adapters.llm_guard import LlmGuardAdapter

    raw = {"text": "call [REDACTED_PHONE_NUMBER_1]", "is_valid": False, "risk_score": 1.0}
    anon = LlmGuardAdapter().parse(raw)
    assert not anon.flags.blocked and anon.redacted_text == raw["text"]
    block = LlmGuardAdapter(block_on_invalid=True).parse(raw)
    assert block.flags.blocked and block.redacted_text == BLOCK_MESSAGE
    ok = LlmGuardAdapter(block_on_invalid=True).parse({"text": "hi", "is_valid": True, "risk_score": 0.0})
    assert not ok.flags.blocked


def test_llm_guard_unavailable_without_interpreter(monkeypatch) -> None:
    from pii_guardbench.adapters.llm_guard import LlmGuardAdapter

    monkeypatch.delenv("PGB_LLM_GUARD_PYTHON", raising=False)
    with pytest.raises(AdapterUnavailableError, match="PGB_LLM_GUARD_PYTHON"):
        LlmGuardAdapter().warmup()


def test_gliner_unavailable_without_dependency(monkeypatch) -> None:
    from pii_guardbench.adapters import gliner_pii
    from pii_guardbench.adapters.gliner_pii import GlinerPiiAdapter

    monkeypatch.setattr(gliner_pii, "_SHARED", {})
    monkeypatch.setitem(sys.modules, "gliner", None)
    with pytest.raises(AdapterUnavailableError):
        GlinerPiiAdapter().warmup()


def _hf_cached(repo: str) -> bool:
    try:
        from huggingface_hub import try_to_load_from_cache

        return isinstance(try_to_load_from_cache(repo, "gliner_config.json"), str)
    except Exception:
        return False


@pytest.mark.skipif(
    not (_installed("gliner") and _hf_cached("urchade/gliner_multi_pii-v1")),
    reason="gliner or its cached model not available (no download in tests)",
)
def test_gliner_live_redacts_email_offline(monkeypatch) -> None:
    monkeypatch.setenv("HF_HUB_OFFLINE", "1")
    from pii_guardbench.adapters.gliner_pii import GlinerPiiAdapter

    result = GlinerPiiAdapter().run("Please email jane.doe@example.org about the invoice.")
    assert "jane.doe@example.org" not in result.redacted_text
    assert not result.flags.blocked


@pytest.mark.skipif(
    not __import__("os").environ.get("PGB_LLM_GUARD_PYTHON"),
    reason="PGB_LLM_GUARD_PYTHON not set (LLM Guard runs in its own venv)",
)
def test_llm_guard_live_block_variant_blocks_pii() -> None:
    from pii_guardbench.adapters.base import BLOCK_MESSAGE
    from pii_guardbench.adapters.llm_guard import LlmGuardAdapter

    anon = LlmGuardAdapter().run("Call me at 212-555-0147, my name is John Smith.")
    assert "212-555-0147" not in anon.redacted_text and not anon.flags.blocked
    block = LlmGuardAdapter(block_on_invalid=True).run("Call me at 212-555-0147, my name is John Smith.")
    assert block.flags.blocked and block.redacted_text == BLOCK_MESSAGE
