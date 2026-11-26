"""NVIDIA NeMo Guardrails adapter: the built-in Presidio-backed PII rails.

NeMo Guardrails' ``sensitive_data_detection`` rails run Presidio locally and
need no LLM and no API key. Two of NeMo's own actions are wrapped
(``nemoguardrails.library.sensitive_data_detection.actions``):

``mode="mask"`` (``mask_sensitive_data``)
    Replaces detected entities and passes the text on. In nemoguardrails
    0.25.0 this action calls ``_get_analyzer()`` with no argument, so its score
    threshold is **fixed at 0.4** regardless of the configured
    ``score_threshold`` (verified in the installed source). A "NeMo at
    threshold 0" configuration is therefore not reachable through NeMo's mask
    action; the matched comparison at threshold 0 is done on the Presidio side
    (``presidio-nemo6-t0``).

``mode="detect"`` (``detect_sensitive_data``)
    The blocking input rail: if any configured entity is found (at the
    configured ``score_threshold``), the rail blocks. A blocked prompt returns
    NeMo's default refusal text, as an application would show the user.

Entity lists: ``NEMO_EXAMPLE_ENTITIES`` is the list from NeMo's shipped example
``examples/configs/sensitive_data_detection_v2/config.yml``; ``"all"`` passes
every entity type supported by Presidio's default recognizers. Neither was
tuned on benchmark data. NeMo's GLiNER PII rails need a NIM server and are
not used.
"""

from __future__ import annotations

import asyncio
import re
from importlib import metadata
from typing import Any, Iterable, Optional, Union

from pii_guardbench.adapters.base import (
    BLOCK_MESSAGE,
    AdapterFlags,
    AdapterResult,
    AdapterUnavailableError,
    GuardrailAdapter,
)
from pii_guardbench.adapters.presidio import (
    NEMO_EXAMPLE_ENTITIES,
    PRESIDIO_TO_CATEGORY,
    contract_for_entities,
    shared_default_analyzer,
)

__all__ = ["NEMO_EXAMPLE_ENTITIES", "NemoGuardrailsAdapter"]

_PLACEHOLDER_RE = re.compile(r"<([A-Z_]+)>")
MASK_ACTION_FIXED_THRESHOLD = 0.4


def _rails_yaml(entities: tuple[str, ...], score_threshold: float) -> str:
    lines = [
        "rails:",
        "  config:",
        "    sensitive_data_detection:",
        "      input:",
        f"        score_threshold: {score_threshold}",
        "        entities:",
    ]
    lines.extend(f"          - {entity}" for entity in entities)
    return "\n".join(lines) + "\n"


class NemoGuardrailsAdapter(GuardrailAdapter):
    """Wrapper around NeMo Guardrails' Presidio-backed input rails."""

    name = "nemo-guardrails"

    def __init__(
        self,
        entities: Union[Iterable[str], str] = NEMO_EXAMPLE_ENTITIES,
        *,
        mode: str = "mask",
        score_threshold: float = 0.4,
        name: Optional[str] = None,
    ) -> None:
        if mode not in ("mask", "detect"):
            raise ValueError("mode must be 'mask' or 'detect'")
        self._entities_spec = entities
        self.mode = mode
        self.score_threshold = float(score_threshold)
        if name:
            self.name = name
        self.entities: tuple[str, ...] | None = (
            None if entities == "all" else tuple(entities)  # type: ignore[arg-type]
        )
        self._config: Any = None
        self._action: Any = None
        self._loop: asyncio.AbstractEventLoop | None = None

    def _resolve_entities(self) -> tuple[str, ...]:
        if self.entities is None:
            self.entities = tuple(sorted(shared_default_analyzer().get_supported_entities(language="en")))
        return self.entities

    def _load(self) -> None:
        if self._action is not None:
            return
        try:
            from nemoguardrails import RailsConfig
            from nemoguardrails.library.sensitive_data_detection.actions import (
                detect_sensitive_data,
                mask_sensitive_data,
            )
        except ImportError as exc:
            raise AdapterUnavailableError(
                "NemoGuardrailsAdapter needs 'nemoguardrails', 'presidio-analyzer', "
                "'presidio-anonymizer' and the spaCy model en_core_web_lg "
                "(pip install -e \".[nemo]\")."
            ) from exc
        entities = self._resolve_entities()
        self._config = RailsConfig.from_content(
            yaml_content=_rails_yaml(entities, self.score_threshold)
        )
        self._action = mask_sensitive_data if self.mode == "mask" else detect_sensitive_data
        self._loop = asyncio.new_event_loop()

    def normalize(self, prompt: str) -> dict[str, str]:
        return {"source": "input", "text": prompt}

    def call(self, request: dict[str, str]) -> dict[str, Any]:
        self._load()
        assert self._loop is not None
        try:
            outcome = self._loop.run_until_complete(
                self._action(source=request["source"], text=request["text"], config=self._config)
            )
        except RuntimeError as exc:
            # nemoguardrails raises RuntimeError when the spaCy model is missing.
            raise AdapterUnavailableError(f"NeMo PII rail could not start: {exc}") from exc
        if self.mode == "detect":
            blocked = bool(outcome.metadata.get("has_sensitive_data")) or _is_block(outcome)
            return {
                "text": BLOCK_MESSAGE if blocked else request["text"],
                "original": request["text"],
                "blocked": blocked,
            }
        masked = outcome.metadata.get("masked_text", request["text"])
        return {"text": masked, "original": request["text"], "blocked": False}

    def parse(self, raw: dict[str, Any]) -> AdapterResult:
        detected = sorted(
            set(_PLACEHOLDER_RE.findall(raw["text"])) - set(_PLACEHOLDER_RE.findall(raw["original"]))
        )
        return AdapterResult(
            redacted_text=str(raw["text"]),
            flags=AdapterFlags(blocked=bool(raw.get("blocked")), detected_categories=detected),
        )

    def entity_contract(self) -> frozenset[str]:
        return contract_for_entities(self._resolve_entities())

    def category_map(self) -> dict[str, str]:
        # NeMo's actions do not expose Presidio findings; detected types are
        # parsed from the <ENTITY> placeholders in the masked text (mask mode).
        return dict(PRESIDIO_TO_CATEGORY)

    def describe(self) -> dict[str, Any]:
        def version(package: str) -> str | None:
            try:
                return metadata.version(package)
            except metadata.PackageNotFoundError:
                return None

        effective = MASK_ACTION_FIXED_THRESHOLD if self.mode == "mask" else self.score_threshold
        return {
            "adapter": self.name,
            "nemoguardrails": version("nemoguardrails"),
            "presidio_analyzer": version("presidio-analyzer"),
            "spacy_model": "en_core_web_lg " + (version("en-core-web-lg") or "unknown"),
            "mode": self.mode,
            "entities": list(self._resolve_entities()),
            "effective_score_threshold": effective,
            "config": (
                f"{'mask_sensitive_data' if self.mode == 'mask' else 'detect_sensitive_data (blocking)'} "
                f"action (Presidio-backed, local, no LLM); {len(self._resolve_entities())} entities; "
                f"effective threshold {effective}"
                + (" (fixed by NeMo's mask action)" if self.mode == "mask" else "")
                + "; GLiNER NIM rails not used"
            ),
            "entity_contract": sorted(self.entity_contract()),
        }


def _is_block(outcome: Any) -> bool:
    for attr in ("status", "action", "kind", "decision"):
        value = getattr(outcome, attr, None)
        if value is not None and "block" in str(value).lower():
            return True
    return False
