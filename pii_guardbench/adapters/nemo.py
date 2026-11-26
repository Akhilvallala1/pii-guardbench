"""NVIDIA NeMo Guardrails adapter: the built-in Presidio-backed PII masking rail.

NeMo Guardrails' ``sensitive_data_detection`` rail runs Presidio locally and
needs no LLM and no API key. This adapter calls the rail's own
``mask_sensitive_data`` action (``nemoguardrails.library.sensitive_data_detection``)
with a ``RailsConfig`` built from the entity list in NeMo Guardrails' shipped
example ``examples/configs/sensitive_data_detection_v2/config.yml``:
PERSON, EMAIL_ADDRESS, PHONE_NUMBER, CREDIT_CARD, US_SSN, LOCATION.

That list is NeMo's documented example, not a full-coverage configuration, and
it was not tuned for this benchmark. NeMo's GLiNER PII rails need a NIM server
(a hosted endpoint with an NVIDIA API key, or local containers) and are not used.
"""

from __future__ import annotations

import asyncio
import re
from importlib import metadata
from typing import Any

from pii_guardbench.adapters.base import (
    AdapterFlags,
    AdapterResult,
    AdapterUnavailableError,
    GuardrailAdapter,
)

NEMO_EXAMPLE_ENTITIES: tuple[str, ...] = (
    "PERSON",
    "EMAIL_ADDRESS",
    "PHONE_NUMBER",
    "CREDIT_CARD",
    "US_SSN",
    "LOCATION",
)

_PLACEHOLDER_RE = re.compile(r"<([A-Z_]+)>")


def _rails_yaml(entities: tuple[str, ...]) -> str:
    lines = [
        "rails:",
        "  config:",
        "    sensitive_data_detection:",
        "      input:",
        "        score_threshold: 0.4",
        "        entities:",
    ]
    lines.extend(f"          - {entity}" for entity in entities)
    return "\n".join(lines) + "\n"


class NemoGuardrailsAdapter(GuardrailAdapter):
    """Wrapper around NeMo Guardrails' Presidio-backed input masking rail."""

    name = "nemo-guardrails"

    def __init__(self, entities: tuple[str, ...] = NEMO_EXAMPLE_ENTITIES) -> None:
        self.entities = tuple(entities)
        self._config: Any = None
        self._action: Any = None
        self._loop: asyncio.AbstractEventLoop | None = None

    def _load(self) -> None:
        if self._action is not None:
            return
        try:
            from nemoguardrails import RailsConfig
            from nemoguardrails.library.sensitive_data_detection.actions import (
                mask_sensitive_data,
            )
        except ImportError as exc:
            raise AdapterUnavailableError(
                "NemoGuardrailsAdapter needs 'nemoguardrails', 'presidio-analyzer', "
                "'presidio-anonymizer' and the spaCy model en_core_web_lg "
                "(pip install -e \".[nemo]\")."
            ) from exc
        self._config = RailsConfig.from_content(yaml_content=_rails_yaml(self.entities))
        self._action = mask_sensitive_data
        self._loop = asyncio.new_event_loop()

    def normalize(self, prompt: str) -> dict[str, str]:
        return {"source": "input", "text": prompt}

    def call(self, request: dict[str, str]) -> dict[str, Any]:
        self._load()
        assert self._loop is not None
        try:
            outcome = self._loop.run_until_complete(
                self._action(
                    source=request["source"], text=request["text"], config=self._config
                )
            )
        except RuntimeError as exc:
            # nemoguardrails raises RuntimeError when the spaCy model is missing.
            raise AdapterUnavailableError(f"NeMo PII rail could not start: {exc}") from exc
        masked = outcome.metadata.get("masked_text", request["text"])
        return {"text": masked, "original": request["text"]}

    def parse(self, raw: dict[str, Any]) -> AdapterResult:
        detected = sorted(set(_PLACEHOLDER_RE.findall(raw["text"])) - set(_PLACEHOLDER_RE.findall(raw["original"])))
        return AdapterResult(
            redacted_text=str(raw["text"]),
            flags=AdapterFlags(blocked=False, detected_categories=detected),
        )

    def describe(self) -> dict[str, Any]:
        def version(package: str) -> str | None:
            try:
                return metadata.version(package)
            except metadata.PackageNotFoundError:
                return None

        return {
            "adapter": self.name,
            "nemoguardrails": version("nemoguardrails"),
            "presidio_analyzer": version("presidio-analyzer"),
            "spacy_model": "en_core_web_lg " + (version("en-core-web-lg") or "unknown"),
            "config": (
                "mask_sensitive_data action (Presidio-backed, local, no LLM) with the "
                "shipped example entities " + ", ".join(self.entities) + "; action default "
                "score threshold 0.4; GLiNER NIM rails not used (need a NIM server/API key)"
            ),
        }
