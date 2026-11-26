"""Lenient FPR and blocked-handling metrics. No network."""

from __future__ import annotations

from pii_guardbench.adapters.base import AdapterFlags, AdapterResult
from pii_guardbench.evaluator import is_false_positive, score_adapter

CONTROL = {"id": "c", "text": "Please confirm invoice 123456 is paid.", "sensitive_spans": []}
SSN = "790-18-3465"
PII = {"id": "p", "text": f"ssn {SSN}", "sensitive_spans": [
    {"category": "ssn", "start": 4, "end": 15, "value": SSN}]}


def _r(text: str, blocked: bool = False) -> AdapterResult:
    return AdapterResult(redacted_text=text, flags=AdapterFlags(blocked=blocked))


def test_whitespace_and_trailing_newline_are_not_false_positives() -> None:
    assert not is_false_positive(CONTROL, _r(CONTROL["text"] + "\n"))
    assert not is_false_positive(CONTROL, _r("  Please   confirm invoice 123456\tis paid.  "))


def test_edge_punctuation_and_insertions_are_not_false_positives() -> None:
    assert not is_false_positive(CONTROL, _r("Please confirm invoice 123456 is paid"))
    assert not is_false_positive(CONTROL, _r(CONTROL["text"] + " [Note: no PII found]"))


def test_replaced_or_removed_token_and_block_are_false_positives() -> None:
    assert is_false_positive(CONTROL, _r("Please confirm invoice [REDACTED] is paid."))
    assert is_false_positive(CONTROL, _r("Please confirm invoice is paid."))
    assert is_false_positive(CONTROL, _r(CONTROL["text"], blocked=True))


def test_fp_only_applies_to_controls() -> None:
    assert not is_false_positive(PII, _r("anything"))


def test_blocked_rate_and_residual_exposure_given_block() -> None:
    results = [_r(PII["text"], blocked=True)]  # blocked but echoed
    s = score_adapter("echo-block", [PII], results, bootstrap_iterations=10)
    assert s.blocked.k == 1 and s.blocked.n == 1
    assert s.residual_given_block.k == 1 and s.residual_given_block.n == 1
    assert s.leak.k == 1
    clean_block = score_adapter("clean-block", [PII], [_r("", blocked=True)], bootstrap_iterations=10)
    assert clean_block.residual_given_block.k == 0 and clean_block.residual_given_block.n == 1
    assert clean_block.leak.k == 0
