"""Concrete-but-stub adapter for piiguard. Does not call a live service."""

from __future__ import annotations

from typing import Any

from pii_guardbench.adapters.base import AdapterFlags, AdapterResult, GuardrailAdapter


class PiiguardAdapter(GuardrailAdapter):
    """Wrapper shape for piiguard. The live call is not wired up yet."""

    name = "piiguard"

    def normalize(self, prompt: str) -> dict[str, str]:
        return {"text": prompt}

    def call(self, request: dict[str, str]) -> Any:
        try:
            import piiguard  # noqa: F401
        except ImportError as exc:
            raise NotImplementedError(
                "PiiguardAdapter requires the 'piiguard' package, which is "
                "not installed. This stub does not call a live service."
            ) from exc
        raise NotImplementedError(
            "PiiguardAdapter is a stub: the package is present, but the "
            "live piiguard call is not wired up yet."
        )

    def parse(self, raw: Any) -> AdapterResult:
        # Expected future mapping: a dict with redacted text and optional flags.
        if not isinstance(raw, dict):
            raise TypeError("PiiguardAdapter.parse expects a dict-like raw response.")
        return AdapterResult(
            redacted_text=str(raw.get("redacted_text", raw.get("text", ""))),
            flags=AdapterFlags(
                blocked=bool(raw.get("blocked", False)),
                detected_categories=list(raw.get("detected_categories") or []),
            ),
        )
