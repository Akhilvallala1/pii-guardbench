"""Stub adapter for NVIDIA NeMo Guardrails. Does not call a live service."""

from __future__ import annotations

from typing import Any

from pii_guardbench.adapters.base import AdapterFlags, AdapterResult, GuardrailAdapter


class NemoGuardrailsAdapter(GuardrailAdapter):
    """Wrapper shape for NVIDIA NeMo Guardrails."""

    name = "nemo-guardrails"

    def normalize(self, prompt: str) -> dict[str, str]:
        return {"prompt": prompt}

    def call(self, request: dict[str, str]) -> Any:
        try:
            import nemoguardrails  # noqa: F401
        except ImportError as exc:
            raise NotImplementedError(
                "NemoGuardrailsAdapter requires the 'nemoguardrails' package, "
                "which is not installed. This stub does not call a live service."
            ) from exc
        raise NotImplementedError(
            "NemoGuardrailsAdapter is a stub: nemoguardrails is installed, "
            "but the live rails call is not wired up yet."
        )

    def parse(self, raw: Any) -> AdapterResult:
        if not isinstance(raw, dict):
            raise TypeError(
                "NemoGuardrailsAdapter.parse expects a dict-like raw response."
            )
        return AdapterResult(
            redacted_text=str(raw.get("response", raw.get("text", ""))),
            flags=AdapterFlags(
                blocked=bool(raw.get("blocked", False)),
                detected_categories=list(raw.get("detected_categories") or []),
            ),
        )
