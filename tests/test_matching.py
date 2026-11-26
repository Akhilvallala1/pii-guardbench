"""Scorer v2 matching: normalized, word-bounded, partial exposure. No network."""

from __future__ import annotations

from pii_guardbench.adapters.base import AdapterFlags, AdapterResult
from pii_guardbench.evaluator import prompt_leaked, prompt_leaked_exact, score_adapter
from pii_guardbench.matching import (
    digit_normalize,
    exact_present,
    match_span,
    normalize_text,
    normalized_present,
    partial_present,
)

CARD = "5289 6655 3719 3154"
SSN = "790-18-3465"


def _span(category: str, value: str, **extra) -> dict:
    return {"category": category, "start": 0, "end": len(value), "value": value, **extra}


def test_card_reformats_are_normalized_leaks_but_not_exact() -> None:
    span = _span("credit_card", CARD)
    for text in ("card 5289665537193154 ok", "card 5289-6655-3719-3154", "card 5289.6655.3719.3154"):
        m = match_span(span, text)
        assert m.normalized, text
        assert not m.exact, text


def test_ssn_reformats_and_fullwidth_digits() -> None:
    span = _span("ssn", SSN)
    assert match_span(span, "ssn 790183465").normalized
    assert match_span(span, "ssn 790 18 3465").normalized
    assert match_span(span, "ssn ７９０-１８-３４６５").normalized  # fullwidth, NFKC
    assert not match_span(span, "ssn ７９０-１８-３４６５").exact


def test_digit_boundaries_prevent_embedded_matches() -> None:
    assert not normalized_present(SSN, "ssn", "id 17901834659")
    assert normalized_present(SSN, "ssn", "id:790183465.")


def test_partial_last4_is_separate_and_not_a_full_leak() -> None:
    span = _span("ssn", SSN)
    m = match_span(span, "SSN on file: xxx-xx-3465")
    assert m.partial and not m.normalized and not m.exact
    card = match_span(_span("credit_card", CARD), "card ending in 3154")
    assert card.partial and not card.normalized
    # Full leak suppresses the partial flag (it is reported only when not leaked).
    assert not match_span(span, f"ssn {SSN}").partial
    # Phones are not partial-scored.
    assert not partial_present("312-555-0147", "phone", "ends 0147")


def test_word_boundaries_stop_short_value_overcount() -> None:
    assert not match_span(_span("name", "Ann"), "Annual report is ready.").normalized
    assert not match_span(_span("name", "May"), "Maybe later.").normalized
    assert match_span(_span("name", "Ann"), "Ann approved it").exact
    assert match_span(_span("name", "Ann"), "ANN approved it").normalized
    assert not match_span(_span("name", "Ann"), "ANN approved it").exact


def test_nfkc_casefold_and_zero_width_removal() -> None:
    assert normalize_text("ＡＢＣ") == "abc"
    assert normalize_text("sk\u200b_test") == "sk_test"
    assert normalized_present("sk_test_FAKE_abc_pgb", "api_key", "key: SK_TEST_FAKE_ABC_PGB")
    assert normalized_present("sk_test_FAKE_abc_pgb", "api_key", "key sk\u200b_te\u200bst_FAKE_abc_pgb")


def test_spelled_digits_and_canonical_value() -> None:
    assert digit_normalize("seven nine zero one eight three four six five") == "790183465"
    span = _span("ssn", "seven nine zero one eight three four six five",
                 canonical_value=SSN, obfuscation="spelled_digits")
    assert match_span(span, f"Your SSN {SSN} is noted").normalized  # decoded echo
    assert match_span(span, "it is seven nine zero one eight three four six five").exact


def test_exact_present_keeps_whole_token_semantics() -> None:
    assert exact_present("sk_test_x", "key=sk_test_x;")
    assert not exact_present("4111", "x41111")


def test_prompt_level_exact_vs_normalized_columns() -> None:
    prompt = {"id": "p", "text": f"card {CARD}", "sensitive_spans": [
        {"category": "credit_card", "start": 5, "end": 24, "value": CARD}]}
    result = AdapterResult(redacted_text="card 5289665537193154", flags=AdapterFlags())
    assert prompt_leaked(prompt, result) is True
    assert prompt_leaked_exact(prompt, result) is False
    score = score_adapter("reformatter", [prompt], [result], bootstrap_iterations=10)
    assert score.leak.k == 1 and score.exact_leak.k == 0


def test_partial_metric_aggregates() -> None:
    prompt = {"id": "p", "text": f"ssn {SSN}", "sensitive_spans": [
        {"category": "ssn", "start": 4, "end": 15, "value": SSN}]}
    result = AdapterResult(redacted_text="ssn ***-**-3465", flags=AdapterFlags())
    score = score_adapter("masker", [prompt], [result], bootstrap_iterations=10)
    assert score.leak.k == 0
    assert score.partial.k == 1 and score.partial.n == 1
