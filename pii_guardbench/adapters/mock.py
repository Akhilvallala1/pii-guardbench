"""Configurable mock adapter for tests and CLI demos. No network calls."""

from __future__ import annotations

import re
from typing import Any, Iterable, Sequence

from pii_guardbench.adapters.base import AdapterFlags, AdapterResult, GuardrailAdapter

# Patterns used by the "redact" behavior. These are demo heuristics, not a
# production detector. The 6+ digit run is intentionally aggressive so a
# no-PII control that mentions an invoice number can show a false positive.
_CARD_RE = re.compile(r"\b(?:\d[ -]?){13,19}\b")
_SSN_RE = re.compile(r"\b\d{3}-\d{2}-\d{4}\b")
_API_KEY_RE = re.compile(r"\b(?:sk_test_|ghp_|xoxb-)\S+\b")
_LONG_DIGITS_RE = re.compile(r"\b\d{6,}\b")

_DEFAULT_REDACT_PATTERNS: tuple[re.Pattern[str], ...] = (
    _CARD_RE,
    _SSN_RE,
    _API_KEY_RE,
)


class MockAdapter(GuardrailAdapter):
    """In-memory adapter with configurable leak, redact, or block behavior.

    Parameters
    ----------
    behavior:
        ``passthrough`` returns the prompt unchanged (leaks planted values).
        ``redact`` replaces configured values and regex matches with
        ``replacement``. ``block`` marks the prompt as blocked.
    over_redact:
        When True and behavior is ``redact``, also redact 6+ digit runs.
        Useful for demonstrating a non-zero false-positive rate on controls.
    latency_ms:
        If set, ``run()`` reports this value instead of measured wall time,
        so demos and tests have stable latency numbers.
    """

    def __init__(
        self,
        name: str = "mock",
        *,
        behavior: str = "passthrough",
        redact_values: Iterable[str] | None = None,
        redact_patterns: Sequence[re.Pattern[str]] | None = None,
        replacement: str = "[REDACTED]",
        over_redact: bool = False,
        detected_categories: Sequence[str] | None = None,
        latency_ms: float | None = None,
    ) -> None:
        if behavior not in {"passthrough", "redact", "block"}:
            raise ValueError(
                f"Unknown mock behavior {behavior!r}; "
                "use 'passthrough', 'redact', or 'block'."
            )
        self.name = name
        self.behavior = behavior
        self.redact_values = tuple(redact_values or ())
        self.redact_patterns = (
            list(redact_patterns)
            if redact_patterns is not None
            else list(_DEFAULT_REDACT_PATTERNS)
        )
        if over_redact:
            self.redact_patterns.append(_LONG_DIGITS_RE)
        self.replacement = replacement
        self.over_redact = over_redact
        self._detected_categories = list(detected_categories or [])
        self.fixed_latency_ms = latency_ms

    def normalize(self, prompt: str) -> dict[str, str]:
        return {"text": prompt}

    def call(self, request: dict[str, str]) -> dict[str, Any]:
        text = request["text"]
        if self.behavior == "block":
            return {
                "text": "",
                "blocked": True,
                "detected_categories": list(self._detected_categories),
            }
        if self.behavior == "redact":
            redacted, detected = self._redact(text)
            categories = list(self._detected_categories or detected)
            return {
                "text": redacted,
                "blocked": False,
                "detected_categories": categories,
            }
        return {
            "text": text,
            "blocked": False,
            "detected_categories": list(self._detected_categories),
        }

    def parse(self, raw: dict[str, Any]) -> AdapterResult:
        return AdapterResult(
            redacted_text=str(raw.get("text", "")),
            flags=AdapterFlags(
                blocked=bool(raw.get("blocked", False)),
                detected_categories=list(raw.get("detected_categories") or []),
            ),
        )

    def run(self, prompt: str) -> AdapterResult:
        result = super().run(prompt)
        if self.fixed_latency_ms is not None:
            result.latency_ms = float(self.fixed_latency_ms)
        return result

    def _redact(self, text: str) -> tuple[str, list[str]]:
        detected: list[str] = []
        redacted = text
        for value in self.redact_values:
            if value and value in redacted:
                redacted = redacted.replace(value, self.replacement)
                detected.append("other")
        pattern_categories = (
            (_CARD_RE, "credit_card"),
            (_SSN_RE, "ssn"),
            (_API_KEY_RE, "api_key"),
            (_LONG_DIGITS_RE, "other"),
        )
        for pattern, category in pattern_categories:
            if pattern not in self.redact_patterns:
                continue
            if pattern.search(redacted):
                redacted = pattern.sub(self.replacement, redacted)
                if category not in detected:
                    detected.append(category)
        return redacted, detected
