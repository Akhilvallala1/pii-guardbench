"""piiguard-v2 adapters (rules developed on the pgb-dev-v1 split only).

* ``piiguard-v2``: piiguard's ``Guard`` with the v2 ``guard/rules.yaml`` from the
  ``piiguard-v2`` worktree (``$PIIGUARD_V2_GUARD_DIR``). v2 changes only the rules file
  (every validator it uses already exists in piiguard 0.1.0), so it runs on the
  *installed* piiguard package; the ``piiguard`` row is unaffected because it reads
  ``$PIIGUARD_GUARD_DIR`` (main's rules) and the package code is identical.
* ``piiguard-v2-union-filtered``: union of the v2 rule findings with a default
  ``AnalyzerEngine()`` (same shared engine as the ``presidio`` row, threshold 0)
  restricted to a dev-chosen whitelist of entity types (``UNION_FILTERED_WHITELIST``).

Entity contract (WIDENED vs ``piiguard``): the contract is derived from the rule
names present in the loaded rules file via ``V2_RULE_TO_CATEGORY``; with the frozen
v2 rules it adds api_key, token, password, mrn, diagnosis, account_number and name
(see the report). In-scope leak and contract-aware FPR are therefore not
comparable one-to-one with the ``piiguard`` row's.

Disclosure: piiguard and these rules are written by the benchmark author.
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
from typing import Any

from pii_guardbench.adapters.base import AdapterUnavailableError
from pii_guardbench.adapters.piiguard import (
    ANY_IN_CONTRACT,
    PiiguardAdapter,
    _RULE_TYPES,
    _replace_spans,
)
from pii_guardbench.adapters.presidio import (
    PRESIDIO_TO_CATEGORY,
    resolved_presidio_types,
    shared_default_analyzer,
)

ENV_GUARD_DIR = "PIIGUARD_V2_GUARD_DIR"

# New v2 rule names -> benchmark category (main's rule names keep _RULE_TYPES).
V2_NEW_RULE_TYPES: dict[str, str] = {
    "ssn_keyword_fragment": "ssn",
    "zero_width_laced": ANY_IN_CONTRACT,
    "api_key_prefixed": "api_key",
    "secret_assignment": "api_key",
    "token_prefixed": "token",
    "password_context": "password",
    "mrn_prefixed": "mrn",
    "mrn_context": "mrn",
    "icd10_dotted": "diagnosis",
    "icd10_context": "diagnosis",
    "account_context": "account_number",
    "name_context": "name",
    "name_after_preposition": "name",
    "name_sentence_subject": "name",
}
V2_RULE_TO_CATEGORY: dict[str, str] = {**_RULE_TYPES, **V2_NEW_RULE_TYPES}

# Presidio types allowed into the filtered union. Chosen on pgb-dev-v1 ONLY (see
# scripts/piiguard_v2_union_whitelist.py and the report). Frozen before the one-shot.
UNION_FILTERED_WHITELIST: tuple[str, ...] = ("CREDIT_CARD", "EMAIL_ADDRESS", "IBAN_CODE")


def _resolve_guard_dir(explicit: Path | None) -> Path:
    guard_dir = explicit or (Path(os.environ[ENV_GUARD_DIR]) if os.environ.get(ENV_GUARD_DIR) else None)
    if guard_dir is None or not (Path(guard_dir) / "rules.yaml").exists():
        raise AdapterUnavailableError(
            "piiguard-v2 needs the v2 guard directory (the piiguard-v2 worktree's 'guard/' "
            f"folder with rules.yaml). Set {ENV_GUARD_DIR} or pass guard_dir."
        )
    return Path(guard_dir)


class PiiguardV2Adapter(PiiguardAdapter):
    name = "piiguard-v2"

    def __init__(self, guard_dir: str | os.PathLike[str] | None = None, *, name: str | None = None) -> None:
        super().__init__(guard_dir, with_presidio=False, name=name or "piiguard-v2")

    def _load(self) -> Any:
        if self._guard is not None:
            return self._guard
        try:
            from piiguard.pii.guard import Guard
        except ImportError as exc:
            raise AdapterUnavailableError("piiguard-v2 needs the 'piiguard' package.") from exc
        self.guard_dir = _resolve_guard_dir(self.guard_dir)
        self._guard = Guard.from_dir(self.guard_dir)
        return self._guard

    def rule_names(self) -> list[str]:
        try:
            guard = self._load()
        except AdapterUnavailableError:
            return []
        return [r.name for r in guard.rules.rules]

    def category_map(self) -> dict[str, str]:
        return dict(V2_RULE_TO_CATEGORY)

    def entity_contract(self) -> frozenset[str]:
        cats = {V2_RULE_TO_CATEGORY.get(n) for n in self.rule_names()}
        return frozenset(c for c in cats if c and c != ANY_IN_CONTRACT)

    def describe(self) -> dict[str, Any]:
        info = super().describe()
        info["config"] = "Guard.from_dir($PIIGUARD_V2_GUARD_DIR) v2 rules only; no classifier, no LLM"
        info["entity_contract"] = sorted(self.entity_contract())
        info["contract_note"] = "WIDENED vs piiguard: contract derived from v2 rule names"
        return info


class PiiguardV2UnionFilteredAdapter(PiiguardV2Adapter):
    name = "piiguard-v2-union-filtered"

    def __init__(
        self,
        guard_dir: str | os.PathLike[str] | None = None,
        *,
        whitelist: tuple[str, ...] | None = None,
        analyzer: Any = None,
        anonymizer: Any = None,
        name: str | None = None,
    ) -> None:
        super().__init__(guard_dir, name=name or "piiguard-v2-union-filtered")
        self.whitelist = tuple(whitelist) if whitelist is not None else UNION_FILTERED_WHITELIST
        self._analyzer = analyzer
        self._anonymizer = anonymizer

    def call(self, request: dict[str, str]) -> dict[str, Any]:
        text = request["text"]
        guard = self._load()
        if self._analyzer is None:
            self._analyzer = shared_default_analyzer()
        spans: list[tuple[int, int]] = []
        types: set[str] = set()
        for f in guard.redact(text).findings:
            types.add(f"piiguard:{f.type}")
            spans.append((f.start, f.end))
        found = list(self._analyzer.analyze(
            text=text, language="en", entities=list(self.whitelist), score_threshold=0.0))
        for r in found:
            spans.append((r.start, r.end))
        for t in resolved_presidio_types(text, found, self._anonymizer) if found else []:
            types.add(f"presidio:{t}")
        return {"text": _replace_spans(text, spans, "<PII>"), "detected_categories": sorted(types)}

    def category_map(self) -> dict[str, str]:
        return {
            **{f"piiguard:{k}": v for k, v in V2_RULE_TO_CATEGORY.items()},
            **{f"presidio:{k}": v for k, v in PRESIDIO_TO_CATEGORY.items() if k in self.whitelist},
        }

    def entity_contract(self) -> frozenset[str]:
        return super().entity_contract() | frozenset(
            PRESIDIO_TO_CATEGORY[t] for t in self.whitelist if t in PRESIDIO_TO_CATEGORY)

    def describe(self) -> dict[str, Any]:
        info = super().describe()
        info["config"] = (
            "Union of v2 rules and presidio AnalyzerEngine() defaults (threshold 0) restricted to "
            f"entities={','.join(self.whitelist)}; merged spans replaced with <PII>"
        )
        info["presidio_whitelist"] = list(self.whitelist)
        info["entity_contract"] = sorted(self.entity_contract())
        return info


def rules_sha256(guard_dir: Path) -> str:
    return hashlib.sha256((guard_dir / "rules.yaml").read_bytes()).hexdigest()
