"""Microsoft Presidio adapter: default AnalyzerEngine + default AnonymizerEngine.

Default configuration, deliberately not tuned for any dataset:
``AnalyzerEngine()`` (spaCy ``en_core_web_lg``, all default recognizers,
``language="en"``, no entity filter, score threshold 0) followed by
``AnonymizerEngine().anonymize`` with default operators, which replace each
detected span with ``<ENTITY_TYPE>``.
"""

from __future__ import annotations

from importlib import metadata
from typing import Any

from pii_guardbench.adapters.base import (
    AdapterFlags,
    AdapterResult,
    AdapterUnavailableError,
    GuardrailAdapter,
)


def _version(package: str) -> str | None:
    try:
        return metadata.version(package)
    except metadata.PackageNotFoundError:
        return None


class PresidioAdapter(GuardrailAdapter):
    """Wrapper around Presidio's analyzer and anonymizer."""

    name = "presidio"

    def __init__(self, analyzer: Any = None, anonymizer: Any = None) -> None:
        self._analyzer = analyzer
        self._anonymizer = anonymizer

    def _engines(self) -> tuple[Any, Any]:
        if self._analyzer is None or self._anonymizer is None:
            try:
                from presidio_analyzer import AnalyzerEngine
                from presidio_anonymizer import AnonymizerEngine
            except ImportError as exc:
                raise AdapterUnavailableError(
                    "PresidioAdapter needs 'presidio-analyzer' and "
                    "'presidio-anonymizer' (pip install -e \".[presidio]\") plus a "
                    "spaCy model (python -m spacy download en_core_web_lg)."
                ) from exc
            try:
                self._analyzer = self._analyzer or AnalyzerEngine()
            except (OSError, SystemExit) as exc:
                raise AdapterUnavailableError(
                    "Presidio could not load its default spaCy model "
                    f"(en_core_web_lg): {exc}"
                ) from exc
            self._anonymizer = self._anonymizer or AnonymizerEngine()
        return self._analyzer, self._anonymizer

    @property
    def analyzer(self) -> Any:
        return self._engines()[0]

    def normalize(self, prompt: str) -> dict[str, str]:
        return {"text": prompt, "language": "en"}

    def call(self, request: dict[str, str]) -> dict[str, Any]:
        analyzer, anonymizer = self._engines()
        found = analyzer.analyze(text=request["text"], language=request["language"])
        anonymized = anonymizer.anonymize(text=request["text"], analyzer_results=found)
        return {
            "text": anonymized.text,
            "detected_categories": sorted({r.entity_type for r in found}),
        }

    def parse(self, raw: dict[str, Any]) -> AdapterResult:
        return AdapterResult(
            redacted_text=str(raw.get("text", "")),
            flags=AdapterFlags(
                blocked=False,
                detected_categories=list(raw.get("detected_categories") or []),
            ),
        )

    def describe(self) -> dict[str, Any]:
        return {
            "adapter": self.name,
            "presidio_analyzer": _version("presidio-analyzer"),
            "presidio_anonymizer": _version("presidio-anonymizer"),
            "spacy": _version("spacy"),
            "spacy_model": "en_core_web_lg " + (_version("en-core-web-lg") or "unknown"),
            "config": "AnalyzerEngine() defaults, language=en, no entity filter, "
            "score_threshold=0; AnonymizerEngine() default replace-with-<ENTITY>",
        }
