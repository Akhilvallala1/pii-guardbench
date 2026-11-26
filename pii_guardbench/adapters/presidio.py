"""Stub adapter for Microsoft Presidio. Does not call a live service."""

from __future__ import annotations

from typing import Any

from pii_guardbench.adapters.base import AdapterFlags, AdapterResult, GuardrailAdapter


class PresidioAdapter(GuardrailAdapter):
    """Wrapper shape for Microsoft Presidio Analyzer/Anonymizer."""

    name = "presidio"

    def normalize(self, prompt: str) -> dict[str, str]:
        return {"text": prompt, "language": "en"}

    def call(self, request: dict[str, str]) -> Any:
        try:
            import presidio_analyzer  # noqa: F401
            import presidio_anonymizer  # noqa: F401
        except ImportError as exc:
            raise NotImplementedError(
                "PresidioAdapter requires the 'presidio-analyzer' and "
                "'presidio-anonymizer' packages, which are not installed. "
                "This stub does not call a live service."
            ) from exc
        raise NotImplementedError(
            "PresidioAdapter is a stub: Presidio is installed, but the live "
            "analyze/anonymize call is not wired up yet."
        )

    def parse(self, raw: Any) -> AdapterResult:
        if not isinstance(raw, dict):
            raise TypeError("PresidioAdapter.parse expects a dict-like raw response.")
        return AdapterResult(
            redacted_text=str(raw.get("text", "")),
            flags=AdapterFlags(
                blocked=bool(raw.get("blocked", False)),
                detected_categories=list(raw.get("detected_categories") or []),
            ),
        )
