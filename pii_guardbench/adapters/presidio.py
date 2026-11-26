"""Microsoft Presidio adapter: AnalyzerEngine + default AnonymizerEngine.

Configurations (none tuned on any evaluation data):

* ``presidio``: ``AnalyzerEngine()`` defaults (spaCy ``en_core_web_lg``, all
  default recognizers, ``language="en"``, no entity filter, score threshold 0),
  then ``AnonymizerEngine().anonymize`` with default operators, which replace
  each detected span with ``<ENTITY_TYPE>``.
* ``presidio-all-t0.4``: all default recognizers, score threshold 0.4.
* ``presidio-nemo6-t0`` / ``presidio-nemo6-t0.4``: restricted to NeMo
  Guardrails' shipped example entity list (PERSON, EMAIL_ADDRESS,
  PHONE_NUMBER, CREDIT_CARD, US_SSN, LOCATION) at threshold 0 or 0.4. These are
  matched-config ablations against the ``nemo-guardrails`` rows.

All Presidio-based adapters in one process share a single default
``AnalyzerEngine`` instance (same model, same recognizers).
"""

from __future__ import annotations

from importlib import metadata
from typing import Any, Iterable, Optional

from pii_guardbench.adapters.base import (
    AdapterFlags,
    AdapterResult,
    AdapterUnavailableError,
    GuardrailAdapter,
)

# Presidio entity type -> benchmark category (for in-scope contracts).
PRESIDIO_TO_CATEGORY: dict[str, str] = {
    "PERSON": "name",
    "EMAIL_ADDRESS": "email",
    "PHONE_NUMBER": "phone",
    "CREDIT_CARD": "credit_card",
    "US_SSN": "ssn",
    "LOCATION": "address",
    "US_BANK_NUMBER": "account_number",
    "IBAN_CODE": "account_number",
}

NEMO_EXAMPLE_ENTITIES: tuple[str, ...] = (
    "PERSON",
    "EMAIL_ADDRESS",
    "PHONE_NUMBER",
    "CREDIT_CARD",
    "US_SSN",
    "LOCATION",
)

_SHARED: dict[str, Any] = {}


def _version(package: str) -> str | None:
    try:
        return metadata.version(package)
    except metadata.PackageNotFoundError:
        return None


def shared_default_analyzer() -> Any:
    """One default ``AnalyzerEngine()`` per process (loads en_core_web_lg)."""
    if "analyzer" not in _SHARED:
        try:
            from presidio_analyzer import AnalyzerEngine
        except ImportError as exc:
            raise AdapterUnavailableError(
                "Presidio adapters need 'presidio-analyzer' and 'presidio-anonymizer' "
                "(pip install -e \".[presidio]\") plus a spaCy model "
                "(python -m spacy download en_core_web_lg)."
            ) from exc
        try:
            _SHARED["analyzer"] = AnalyzerEngine()
        except (OSError, SystemExit) as exc:
            raise AdapterUnavailableError(
                f"Presidio could not load its default spaCy model (en_core_web_lg): {exc}"
            ) from exc
    return _SHARED["analyzer"]


def contract_for_entities(entities: Iterable[str]) -> frozenset[str]:
    return frozenset(PRESIDIO_TO_CATEGORY[e] for e in entities if e in PRESIDIO_TO_CATEGORY)


class PresidioAdapter(GuardrailAdapter):
    """Wrapper around Presidio's analyzer and anonymizer."""

    name = "presidio"

    def __init__(
        self,
        analyzer: Any = None,
        anonymizer: Any = None,
        *,
        entities: Optional[Iterable[str]] = None,
        score_threshold: float = 0.0,
        name: Optional[str] = None,
    ) -> None:
        self._analyzer = analyzer
        self._anonymizer = anonymizer
        self.entities = tuple(entities) if entities is not None else None
        self.score_threshold = float(score_threshold)
        if name:
            self.name = name

    def _engines(self) -> tuple[Any, Any]:
        if self._analyzer is None:
            self._analyzer = shared_default_analyzer()
        if self._anonymizer is None:
            try:
                from presidio_anonymizer import AnonymizerEngine
            except ImportError as exc:
                raise AdapterUnavailableError("Presidio adapters need 'presidio-anonymizer'.") from exc
            self._anonymizer = AnonymizerEngine()
        return self._analyzer, self._anonymizer

    @property
    def analyzer(self) -> Any:
        return self._engines()[0]

    def normalize(self, prompt: str) -> dict[str, str]:
        return {"text": prompt, "language": "en"}

    def call(self, request: dict[str, str]) -> dict[str, Any]:
        analyzer, anonymizer = self._engines()
        found = analyzer.analyze(
            text=request["text"],
            language=request["language"],
            entities=list(self.entities) if self.entities is not None else None,
            score_threshold=self.score_threshold,
        )
        anonymized = anonymizer.anonymize(text=request["text"], analyzer_results=found)
        return {
            "text": anonymized.text,
            "detected_categories": sorted({r.entity_type for r in found}),
        }

    def parse(self, raw: dict[str, Any]) -> AdapterResult:
        return AdapterResult(
            redacted_text=str(raw.get("text", "")),
            flags=AdapterFlags(
                blocked=False,  # Presidio only redacts; it never blocks.
                detected_categories=list(raw.get("detected_categories") or []),
            ),
        )

    def configured_entities(self) -> list[str]:
        if self.entities is not None:
            return list(self.entities)
        return sorted(self.analyzer.get_supported_entities(language="en"))

    def entity_contract(self) -> frozenset[str]:
        return contract_for_entities(self.configured_entities())

    def describe(self) -> dict[str, Any]:
        return {
            "adapter": self.name,
            "presidio_analyzer": _version("presidio-analyzer"),
            "presidio_anonymizer": _version("presidio-anonymizer"),
            "spacy": _version("spacy"),
            "spacy_model": "en_core_web_lg " + (_version("en-core-web-lg") or "unknown"),
            "entities": self.configured_entities(),
            "score_threshold": self.score_threshold,
            "config": (
                "AnalyzerEngine() defaults, language=en, "
                + ("no entity filter" if self.entities is None else "entities=" + ",".join(self.entities))
                + f", score_threshold={self.score_threshold}; AnonymizerEngine() default replace-with-<ENTITY>"
            ),
            "entity_contract": sorted(self.entity_contract()),
        }
