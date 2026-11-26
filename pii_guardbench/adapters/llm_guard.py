"""LLM Guard ``Anonymize`` input scanner (protectai/llm-guard), local CPU.

``llm-guard`` 0.3.16 requires Python < 3.13 and pins presidio 2.2.358, so it
runs in its own virtualenv. Set ``PGB_LLM_GUARD_PYTHON`` to that interpreter;
the adapter starts ``_llm_guard_worker.py`` once and exchanges JSON lines. The
measured latency therefore includes a small local IPC overhead.

Configurations (library defaults, not tuned):

* ``llm-guard-anonymize``: ``Anonymize(Vault())`` defaults (its
  ``DEFAULT_ENTITY_TYPES``, the default DeBERTa NER model plus regex patterns,
  spaCy ``en_core_web_sm``); returns the sanitized prompt. Never blocks.
* ``llm-guard-block``: same scan, but uses the scanner's own verdict: when
  ``is_valid`` is False (it found PII) the prompt is blocked and the user sees
  the refusal text, as an application that honours the verdict would do.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path
from typing import Any, Optional

from pii_guardbench.adapters.base import (
    BLOCK_MESSAGE,
    AdapterFlags,
    AdapterResult,
    AdapterUnavailableError,
    GuardrailAdapter,
)

WORKER = Path(__file__).with_name("_llm_guard_worker.py")
LLM_GUARD_TO_CATEGORY = {
    "CREDIT_CARD": "credit_card",
    "CREDIT_CARD_RE": "credit_card",
    "EMAIL_ADDRESS": "email",
    "EMAIL_ADDRESS_RE": "email",
    "PERSON": "name",
    "PHONE_NUMBER": "phone",
    "US_SSN": "ssn",
    "US_SSN_RE": "ssn",
    "US_BANK_NUMBER": "account_number",
    "IBAN_CODE": "account_number",
}
_WORKERS: dict[str, Any] = {}
# The worker returns only the sanitized text, so the redacted native types are
# parsed from LLM Guard's placeholders ``[REDACTED_<TYPE>_<n>]``.
_PLACEHOLDER_RE = re.compile(r"\[REDACTED_([A-Z_]+?)_\d+\]")


def placeholder_types(text: str) -> list[str]:
    return sorted(set(_PLACEHOLDER_RE.findall(text or "")))


def _start_worker(python: str) -> dict[str, Any]:
    if python in _WORKERS:
        return _WORKERS[python]
    proc = subprocess.Popen(
        [python, str(WORKER)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        bufsize=1,
    )
    assert proc.stdout is not None
    first = proc.stdout.readline()
    if not first:
        raise AdapterUnavailableError(
            f"LLM Guard worker exited during start-up (interpreter {python}); "
            "check that llm-guard and its spaCy models are installed there."
        )
    info = json.loads(first)
    worker = {"proc": proc, "entity_types": info.get("entity_types", [])}
    _WORKERS[python] = worker
    return worker


class LlmGuardAdapter(GuardrailAdapter):
    name = "llm-guard-anonymize"

    def __init__(self, *, block_on_invalid: bool = False, python: Optional[str] = None, name: Optional[str] = None) -> None:
        self.block_on_invalid = block_on_invalid
        self.python = python
        if name:
            self.name = name
        elif block_on_invalid:
            self.name = "llm-guard-block"

    def _worker(self) -> dict[str, Any]:
        python = self.python or os.environ.get("PGB_LLM_GUARD_PYTHON")
        if not python or not Path(python).exists():
            raise AdapterUnavailableError(
                "LLM Guard runs in its own venv (llm-guard 0.3.16 needs Python < 3.13). "
                "Set PGB_LLM_GUARD_PYTHON to that interpreter."
            )
        return _start_worker(python)

    def normalize(self, prompt: str) -> dict[str, str]:
        return {"text": prompt}

    def call(self, request: dict[str, str]) -> dict[str, Any]:
        proc = self._worker()["proc"]
        proc.stdin.write(json.dumps({"text": request["text"]}) + "\n")
        proc.stdin.flush()
        line = proc.stdout.readline()
        if not line:
            raise RuntimeError("LLM Guard worker died")
        raw = json.loads(line)
        raw["original"] = request["text"]
        return raw

    def parse(self, raw: dict[str, Any]) -> AdapterResult:
        blocked = self.block_on_invalid and not raw["is_valid"]
        detected = sorted(set(placeholder_types(str(raw["text"]))) - set(placeholder_types(str(raw.get("original") or ""))))
        return AdapterResult(
            redacted_text=BLOCK_MESSAGE if blocked else str(raw["text"]),
            flags=AdapterFlags(blocked=blocked, detected_categories=detected),
        )

    def category_map(self) -> dict[str, str]:
        return dict(LLM_GUARD_TO_CATEGORY)

    def entity_contract(self) -> frozenset[str]:
        types = self._worker()["entity_types"]
        return frozenset(LLM_GUARD_TO_CATEGORY[t] for t in types if t in LLM_GUARD_TO_CATEGORY)

    def describe(self) -> dict[str, Any]:
        python = self.python or os.environ.get("PGB_LLM_GUARD_PYTHON")
        versions = {}
        if python and Path(python).exists():
            code = (
                "import json,importlib.metadata as m;print(json.dumps({p:m.version(p) for p in "
                "['llm-guard','presidio-analyzer','transformers','torch','spacy','en-core-web-sm']}))"
            )
            try:
                versions = json.loads(subprocess.run([python, "-c", code], capture_output=True, text=True, timeout=60).stdout)
            except Exception:
                versions = {}
        return {
            "adapter": self.name,
            "versions": versions,
            "entity_types": self._worker()["entity_types"],
            "config": "llm_guard.input_scanners.Anonymize(Vault()) library defaults"
            + ("; blocked when the scanner returns is_valid=False" if self.block_on_invalid else "; sanitized text, never blocks"),
            "entity_contract": sorted(self.entity_contract()),
        }
