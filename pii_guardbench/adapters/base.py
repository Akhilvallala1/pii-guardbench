"""Abstract adapter interface shared by every guardrail wrapper."""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass
class AdapterFlags:
    """Outcome flags from a single guardrail call."""

    blocked: bool = False
    detected_categories: list[str] = field(default_factory=list)


@dataclass
class AdapterResult:
    """Normalized guardrail output scored by the evaluator."""

    redacted_text: str
    flags: AdapterFlags
    latency_ms: float = 0.0


class GuardrailAdapter(ABC):
    """One interface per guardrail: normalize, call, parse, and a timed run."""

    name: str = "adapter"

    @abstractmethod
    def normalize(self, prompt: str) -> Any:
        """Turn a prompt string into the request object this guardrail expects."""

    @abstractmethod
    def call(self, request: Any) -> Any:
        """Send the request to the guardrail and return the raw response."""

    @abstractmethod
    def parse(self, raw: Any) -> AdapterResult:
        """Map a raw response onto redacted text and flags. Latency is set by run()."""

    def run(self, prompt: str) -> AdapterResult:
        """Normalize, call, and parse, recording wall-clock latency in milliseconds."""
        started = time.perf_counter()
        request = self.normalize(prompt)
        raw = self.call(request)
        result = self.parse(raw)
        result.latency_ms = (time.perf_counter() - started) * 1000.0
        return result
