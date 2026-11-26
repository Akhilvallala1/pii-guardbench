"""GLiNER PII adapter (standalone zero-shot NER, local CPU inference).

Configuration, fixed before any benchmark run and not tuned on benchmark data:

* model ``urchade/gliner_multi_pii-v1`` (Apache-2.0) via the ``gliner`` package;
* one plain-language label per benchmark category (``GLINER_LABELS``);
* GLiNER's default threshold 0.5;
* long texts are split into chunks of at most 256 GLiNER tokens (32-token overlap)
  (GLiNER truncates long inputs); an entity spanning a chunk boundary can be
  missed;
* every predicted span is replaced with ``<LABEL>`` (overlaps merged).

It only redacts; it never blocks.
"""

from __future__ import annotations

import re
from importlib import metadata
from typing import Any, Optional

from pii_guardbench.adapters.base import (
    AdapterFlags,
    AdapterResult,
    AdapterUnavailableError,
    GuardrailAdapter,
)

DEFAULT_MODEL = "urchade/gliner_multi_pii-v1"
# label -> benchmark category
GLINER_LABELS: dict[str, str] = {
    "person": "name",
    "email": "email",
    "phone number": "phone",
    "credit card number": "credit_card",
    "social security number": "ssn",
    "address": "address",
    "bank account number": "account_number",
    "medical record number": "mrn",
    "medical diagnosis": "diagnosis",
    "api key": "api_key",
    "access token": "token",
    "password": "password",
}
_SHARED: dict[str, Any] = {}
# GLiNER's own word splitter (gliner.data_processing WhitespaceTokenSplitter):
# punctuation counts as a token, so chunk on this, not on whitespace words.
_WORD_RE = re.compile(r"\w+(?:[-_]\w+)*|\S")


def _version(package: str) -> str | None:
    try:
        return metadata.version(package)
    except metadata.PackageNotFoundError:
        return None


class GlinerPiiAdapter(GuardrailAdapter):
    name = "gliner-pii"

    def __init__(
        self,
        model_name: str = DEFAULT_MODEL,
        *,
        threshold: float = 0.5,
        max_words: int = 256,
        overlap_words: int = 32,
        name: Optional[str] = None,
    ) -> None:
        self.model_name = model_name
        self.threshold = threshold
        self.max_words = max_words  # GLiNER truncates beyond 384 tokens; stay well under
        self.overlap_words = overlap_words
        if name:
            self.name = name

    def _model(self) -> Any:
        if self.model_name not in _SHARED:
            try:
                from gliner import GLiNER
            except ImportError as exc:
                raise AdapterUnavailableError(
                    "GlinerPiiAdapter needs the 'gliner' package and torch (CPU is fine): "
                    "pip install -e \".[gliner]\"."
                ) from exc
            try:
                _SHARED[self.model_name] = GLiNER.from_pretrained(self.model_name)
            except Exception as exc:  # network / hub errors
                raise AdapterUnavailableError(
                    f"Could not load GLiNER model {self.model_name}: {exc}"
                ) from exc
        return _SHARED[self.model_name]

    def _chunks(self, text: str) -> list[tuple[int, int]]:
        words = [m.span() for m in _WORD_RE.finditer(text)]
        if not words:
            return [(0, len(text))]
        out = []
        step = max(1, self.max_words - self.overlap_words)
        for i in range(0, len(words), step):
            group = words[i:i + self.max_words]
            out.append((group[0][0], group[-1][1]))
            if i + self.max_words >= len(words):
                break
        return out

    def normalize(self, prompt: str) -> dict[str, str]:
        return {"text": prompt}

    def call(self, request: dict[str, str]) -> dict[str, Any]:
        model = self._model()
        text = request["text"]
        spans: list[tuple[int, int, str]] = []
        for start, end in self._chunks(text):
            for ent in model.predict_entities(text[start:end], list(GLINER_LABELS), threshold=self.threshold):
                spans.append((start + int(ent["start"]), start + int(ent["end"]), ent["label"]))
        spans.sort()
        merged: list[list[Any]] = []
        for s, e, label in spans:
            if merged and s < merged[-1][1]:
                merged[-1][1] = max(merged[-1][1], e)
            else:
                merged.append([s, e, label])
        out, cursor = [], 0
        for s, e, label in merged:
            out.append(text[cursor:s])
            out.append("<" + label.upper().replace(" ", "_") + ">")
            cursor = e
        out.append(text[cursor:])
        return {"text": "".join(out), "detected_categories": sorted({l for _, _, l in spans})}

    def parse(self, raw: dict[str, Any]) -> AdapterResult:
        return AdapterResult(
            redacted_text=str(raw.get("text", "")),
            flags=AdapterFlags(blocked=False, detected_categories=list(raw.get("detected_categories") or [])),
        )

    def entity_contract(self) -> frozenset[str]:
        return frozenset(GLINER_LABELS.values())

    def describe(self) -> dict[str, Any]:
        return {
            "adapter": self.name,
            "gliner": _version("gliner"),
            "torch": _version("torch"),
            "transformers": _version("transformers"),
            "model": self.model_name,
            "labels": list(GLINER_LABELS),
            "threshold": self.threshold,
            "config": f"GLiNER {self.model_name}, {len(GLINER_LABELS)} labels (one per benchmark "
            f"category, fixed a priori), threshold {self.threshold}, {self.max_words}-token chunks ({self.overlap_words} overlap), "
            "replace with <LABEL>",
            "entity_contract": sorted(self.entity_contract()),
        }
