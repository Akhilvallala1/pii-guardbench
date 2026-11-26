"""Evaluator aggregation on hand-built mock outputs. No network calls."""

from __future__ import annotations

import pytest

from pii_guardbench.adapters.base import AdapterFlags, AdapterResult
from pii_guardbench.evaluator import (
    evaluate,
    percentile,
    prompt_leaked,
    score_adapter,
    wilson_interval,
)

CARD = "4111 1111 1111 1111"
SSN = "078-05-1120"
KEY = "sk_test_example123"

PROMPTS = [
    {
        "id": "pii-card-ssn",
        "text": f"charge {CARD} ssn {SSN}",
        "sensitive_spans": [
            {"category": "credit_card", "start": 7, "end": 26, "value": CARD},
            {"category": "ssn", "start": 31, "end": 42, "value": SSN},
        ],
    },
    {
        "id": "pii-key",
        "text": f"key {KEY}",
        "sensitive_spans": [
            {"category": "api_key", "start": 4, "end": 22, "value": KEY},
        ],
    },
    {
        "id": "control",
        "text": "Please confirm invoice 123456 is paid.",
        "sensitive_spans": [],
    },
]


def _result(text: str, *, blocked: bool = False, latency_ms: float = 1.0) -> AdapterResult:
    return AdapterResult(
        redacted_text=text,
        flags=AdapterFlags(blocked=blocked, detected_categories=[]),
        latency_ms=latency_ms,
    )


def test_blocked_output_is_not_a_leak_even_if_text_still_contains_pii() -> None:
    prompt = PROMPTS[0]
    leaked_text = _result(prompt["text"], blocked=False)
    blocked = _result(prompt["text"], blocked=True)
    assert prompt_leaked(prompt, leaked_text) is True
    assert prompt_leaked(prompt, blocked) is False


def test_score_leaky_redacting_and_blocking_adapters() -> None:
    leaky = [
        _result(PROMPTS[0]["text"], latency_ms=1.5),
        _result(PROMPTS[1]["text"], latency_ms=1.5),
        _result(PROMPTS[2]["text"], latency_ms=1.5),
    ]
    redacting = [
        _result("charge [REDACTED] ssn [REDACTED]", latency_ms=8.0),
        _result("key [REDACTED]", latency_ms=8.0),
        _result("Please confirm invoice [REDACTED] is paid.", latency_ms=8.0),
    ]
    # Blocked despite residual PII in the raw text; must not count as a leak.
    blocking = [
        _result(PROMPTS[0]["text"], blocked=True, latency_ms=4.0),
        _result(PROMPTS[1]["text"], blocked=True, latency_ms=4.0),
        _result(PROMPTS[2]["text"], blocked=True, latency_ms=4.0),
    ]

    leaky_score = score_adapter("mock-leaky", PROMPTS, leaky)
    redact_score = score_adapter("mock-redact", PROMPTS, redacting)
    block_score = score_adapter("mock-block", PROMPTS, blocking)

    assert leaky_score.leak.k == 2
    assert leaky_score.leak.n == 2
    assert leaky_score.leak.rate == 1.0
    assert leaky_score.false_positive.k == 0
    assert leaky_score.false_positive.n == 1
    assert leaky_score.false_positive.rate == 0.0
    assert leaky_score.by_category["credit_card"].rate == 1.0
    assert leaky_score.by_category["ssn"].rate == 1.0
    assert leaky_score.by_category["api_key"].rate == 1.0
    assert leaky_score.latency.mean == pytest.approx(1.5)
    assert leaky_score.latency.p50 == pytest.approx(1.5)
    assert leaky_score.latency.p95 == pytest.approx(1.5)

    assert redact_score.leak.k == 0
    assert redact_score.leak.n == 2
    assert redact_score.leak.rate == 0.0
    assert redact_score.false_positive.k == 1
    assert redact_score.false_positive.n == 1
    assert redact_score.false_positive.rate == 1.0
    assert redact_score.by_category["credit_card"].rate == 0.0
    assert redact_score.by_category["ssn"].rate == 0.0
    assert redact_score.by_category["api_key"].rate == 0.0

    assert block_score.leak.rate == 0.0
    assert block_score.false_positive.rate == 1.0
    assert block_score.by_category["credit_card"].rate == 0.0

    expected_leak_ci = wilson_interval(2, 2)
    assert leaky_score.leak.low == pytest.approx(expected_leak_ci.low)
    assert leaky_score.leak.high == pytest.approx(expected_leak_ci.high)


def test_category_breakdown_partial_redaction() -> None:
    # Card is redacted, SSN leaks; overall prompt still counts as a leak.
    results = [
        _result(f"charge [REDACTED] ssn {SSN}", latency_ms=2.0),
        _result("key [REDACTED]", latency_ms=2.0),
        _result(PROMPTS[2]["text"], latency_ms=2.0),
    ]
    score = score_adapter("partial", PROMPTS, results)
    assert score.leak.k == 1
    assert score.leak.n == 2
    assert score.by_category["credit_card"].k == 0
    assert score.by_category["ssn"].k == 1
    assert score.by_category["api_key"].k == 0
    assert score.false_positive.k == 0


def test_zero_prompt_counts_are_safe() -> None:
    score = score_adapter("empty", [], [])
    assert score.leak.n == 0
    assert score.leak.rate is None
    assert score.false_positive.n == 0
    assert score.false_positive.rate is None
    assert score.latency.mean is None
    assert score.by_category == {}


def test_evaluate_keeps_adapter_order_and_counts() -> None:
    leaky = [
        _result(PROMPTS[0]["text"], latency_ms=1.0),
        _result(PROMPTS[1]["text"], latency_ms=1.0),
        _result(PROMPTS[2]["text"], latency_ms=1.0),
    ]
    report = evaluate(
        PROMPTS,
        {"first": leaky, "second": leaky},
        prompts_path="in-memory",
    )
    assert report.n_prompts == 3
    assert report.n_pii == 2
    assert report.n_controls == 1
    assert [score.adapter for score in report.scores] == ["first", "second"]


def test_percentile_edges() -> None:
    assert percentile([], 50) is None
    assert percentile([4.0], 95) == 4.0
    assert percentile([0.0, 10.0], 50) == pytest.approx(5.0)
    assert percentile([0.0, 10.0], 95) == pytest.approx(9.5)
    with pytest.raises(ValueError):
        percentile([1.0], 120)


def test_length_mismatch_raises() -> None:
    with pytest.raises(ValueError, match="length mismatch"):
        score_adapter("x", PROMPTS, [_result("only one")])
