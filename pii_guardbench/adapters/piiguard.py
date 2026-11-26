"""piiguard adapter (https://github.com/Akhilvallala1/piiguard), offline configs only.

Two offline configurations are provided, neither of which sends text anywhere:

* ``piiguard``: the committed learned regex rules (``guard/rules.yaml``) and no
  classifier. This is the guard's rule layer as shipped.
* ``piiguard-pgpresidio`` (formerly ``piiguard-presidio``, kept as an alias):
  the same rules plus piiguard's own ``PresidioDetector`` (its
  ``--detector presidio`` mode). That detector is a *different Presidio
  configuration* from the ``presidio`` row: 15 entities
  (``PRESIDIO_DEFAULT_ENTITIES`` in piiguard) at score threshold 0.5.
* ``piiguard-union-presidio``: a true union of the rules and the *same*
  ``AnalyzerEngine()`` defaults used by the ``presidio`` row (all recognizers,
  threshold 0). Both run on the original text; their character spans are
  merged and every merged span is replaced with ``<PII>``.

The remote LLM classifier path needs an API key and is not used.

Disclosure: piiguard is written by the benchmark author.

The guard directory is ``$PIIGUARD_GUARD_DIR`` or the ``guard_dir`` argument
(a checkout's ``guard/`` folder containing ``rules.yaml``). The ``piiguard``
package must be importable (``pip install`` a checkout).
"""

from __future__ import annotations

import hashlib
import os
from importlib import metadata
from pathlib import Path
from typing import Any

from pii_guardbench.adapters.base import (
    AdapterFlags,
    AdapterResult,
    AdapterUnavailableError,
    GuardrailAdapter,
)
from pii_guardbench.adapters.presidio import (
    PRESIDIO_TO_CATEGORY,
    contract_for_entities,
    resolved_presidio_types,
    shared_default_analyzer,
)

# Category value meaning "any category in this adapter's contract": piiguard's
# generic obfuscation rules (spelled/fullwidth/split/encoded digits, generic
# base64/hex/reversed PII) target the contract's identifiers without saying
# which one, so a hit on a control counts as an in-contract redaction.
ANY_IN_CONTRACT = "*"


class PiiguardAdapter(GuardrailAdapter):
    """Wrapper around ``piiguard.pii.guard.Guard`` (rules only, or rules + Presidio)."""

    name = "piiguard"

    def __init__(
        self,
        guard_dir: str | os.PathLike[str] | None = None,
        *,
        with_presidio: bool = False,
        name: str | None = None,
        presidio_analyzer: Any = None,
    ) -> None:
        self.guard_dir = Path(guard_dir) if guard_dir else None
        self.with_presidio = with_presidio
        self._presidio_analyzer = presidio_analyzer
        if name:
            self.name = name
        elif with_presidio:
            self.name = "piiguard-pgpresidio"
        self._guard: Any = None

    def _load(self) -> Any:
        if self._guard is not None:
            return self._guard
        try:
            from piiguard.pii.guard import Guard
        except ImportError as exc:
            raise AdapterUnavailableError(
                "PiiguardAdapter needs the 'piiguard' package "
                "(pip install a checkout of github.com/Akhilvallala1/piiguard)."
            ) from exc
        guard_dir = self.guard_dir or (
            Path(os.environ["PIIGUARD_GUARD_DIR"]) if os.environ.get("PIIGUARD_GUARD_DIR") else None
        )
        if guard_dir is None or not (Path(guard_dir) / "rules.yaml").exists():
            raise AdapterUnavailableError(
                "PiiguardAdapter needs the guard directory (a piiguard checkout's "
                "'guard/' folder with rules.yaml). Set PIIGUARD_GUARD_DIR or pass guard_dir."
            )
        self.guard_dir = Path(guard_dir)
        classifier = None
        if self.with_presidio:
            try:
                from piiguard.pii.local import PresidioDetector
            except ImportError as exc:
                raise AdapterUnavailableError(
                    "piiguard-presidio needs presidio-analyzer and a spaCy model."
                ) from exc
            # Leave analyzer=None so piiguard builds its own engine exactly as its
            # --detector presidio mode does (explicit spaCy NlpEngineProvider config).
            classifier = PresidioDetector(analyzer=self._presidio_analyzer)
        self._guard = Guard.from_dir(self.guard_dir, classifier=classifier)
        return self._guard

    def normalize(self, prompt: str) -> dict[str, str]:
        return {"text": prompt}

    def call(self, request: dict[str, str]) -> dict[str, Any]:
        guard = self._load()
        redaction = guard.redact(request["text"])
        return {
            "text": redaction.text,
            "detected_categories": sorted({f.type for f in redaction.findings}),
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
        rules_version = None
        rules_sha256 = None
        rules = (self.guard_dir / "rules.yaml") if self.guard_dir else None
        if rules and rules.exists():
            first = rules.read_text(encoding="utf-8").splitlines()[:1]
            rules_version = first[0] if first else None
            rules_sha256 = hashlib.sha256(rules.read_bytes()).hexdigest()
        try:
            package_version = metadata.version("piiguard")
        except metadata.PackageNotFoundError:
            package_version = None
        return {
            "adapter": self.name,
            "piiguard_package": package_version,
            "guard_dir": str(self.guard_dir) if self.guard_dir else None,
            "rules_yaml_first_line": rules_version,
            "rules_yaml_sha256": rules_sha256,
            "config": (
                "Guard.from_dir(guard/), learned rules + piiguard PresidioDetector "
                "(its defaults: score_threshold=0.5, 15 default entities); no LLM classifier"
                if self.with_presidio
                else "Guard.from_dir(guard/), learned rules only; no classifier, no LLM"
            ),
            "entity_contract": sorted(self.entity_contract()),
            "disclosure": "piiguard is written by the benchmark author (reference row).",
        }

    def category_map(self) -> dict[str, str]:
        return dict(PIIGUARD_TYPE_TO_CATEGORY)

    def entity_contract(self) -> frozenset[str]:
        contract = set(PIIGUARD_RULE_CONTRACT)
        if self.with_presidio:
            contract |= contract_for_entities(PIIGUARD_PRESIDIO_ENTITIES)
        return frozenset(contract)


# piiguard's shipped rules (guard/rules.yaml v23) target SSNs, emails, US phones
# and cards, including obfuscated variants; the remaining rules are dates of
# birth and insurance identifiers that the benchmark does not score.
PIIGUARD_RULE_CONTRACT: frozenset[str] = frozenset({"ssn", "email", "phone", "credit_card"})
# piiguard.pii.local.PRESIDIO_DEFAULT_ENTITIES (piiguard 0.1.0).
PIIGUARD_PRESIDIO_ENTITIES: tuple[str, ...] = (
    "PERSON", "LOCATION", "EMAIL_ADDRESS", "PHONE_NUMBER", "US_SSN", "CREDIT_CARD", "IBAN_CODE",
    "IP_ADDRESS", "US_BANK_NUMBER", "US_DRIVER_LICENSE", "US_PASSPORT", "US_ITIN",
    "MEDICAL_LICENSE", "CRYPTO", "MAC_ADDRESS",
)


_RULE_TYPES: dict[str, str] = {
    "ssn": "ssn", "ssn_spaced": "ssn", "base64_ssn": "ssn", "hex_ssn": "ssn",
    "email": "email", "base64_email": "email",
    "phone_us": "phone", "phone_spaced": "phone", "base64_phone": "phone", "hex_phone": "phone",
    "credit_card": "credit_card", "credit_card_spaced": "credit_card",
    "base64_credit_card": "credit_card", "hex_credit_card": "credit_card",
    "fullwidth_digits": ANY_IN_CONTRACT, "spelled_digits": ANY_IN_CONTRACT,
    "split_line_digits": ANY_IN_CONTRACT, "unicode_separator_digits": ANY_IN_CONTRACT,
    "url_encoded": ANY_IN_CONTRACT, "generic_base64_pii": ANY_IN_CONTRACT,
    "generic_hex_pii": ANY_IN_CONTRACT, "generic_reversed_pii": ANY_IN_CONTRACT,
    # scan_timeout is a scanner timeout, not a redaction of an identifier:
    # deliberately unmapped (out of contract), so it never makes a contract FP.
    # dob_*, adjuster_badge, claim_number, policy_number, split_line_identifier,
    # reversed_* insurance ids: not benchmark categories (out of contract).
}
# piiguard's PresidioDetector lower-cases Presidio entity types (_norm_type).
PIIGUARD_TYPE_TO_CATEGORY: dict[str, str] = {
    **_RULE_TYPES,
    **{k.lower(): v for k, v in PRESIDIO_TO_CATEGORY.items()},
}
# Union adapter records prefixed types so its generic <PII> token stays attributable.
UNION_TYPE_TO_CATEGORY: dict[str, str] = {
    **{f"piiguard:{k}": v for k, v in _RULE_TYPES.items()},
    **{f"presidio:{k}": v for k, v in PRESIDIO_TO_CATEGORY.items()},
}


class PiiguardUnionPresidioAdapter(PiiguardAdapter):
    """True union: piiguard rules OR default ``AnalyzerEngine()`` (threshold 0)."""

    name = "piiguard-union-presidio"

    def __init__(
        self, guard_dir: str | os.PathLike[str] | None = None, *, analyzer: Any = None, anonymizer: Any = None
    ) -> None:
        super().__init__(guard_dir, with_presidio=False, name="piiguard-union-presidio")
        self._analyzer = analyzer
        self._anonymizer = anonymizer

    def call(self, request: dict[str, str]) -> dict[str, Any]:
        text = request["text"]
        guard = self._load()
        if self._analyzer is None:
            self._analyzer = shared_default_analyzer()
        spans: list[tuple[int, int]] = []
        types: set[str] = set()
        for finding in guard.redact(text).findings:
            types.add(f"piiguard:{finding.type}")
            if 0 <= finding.start < finding.end <= len(text) and text[finding.start:finding.end] == finding.value:
                spans.append((finding.start, finding.end))
            else:  # offsets not on the raw text: fall back to locating the value
                start = text.find(finding.value) if finding.value else -1
                while start != -1:
                    spans.append((start, start + len(finding.value)))
                    start = text.find(finding.value, start + 1)
        found = list(self._analyzer.analyze(text=text, language="en", score_threshold=0.0))
        for result in found:
            spans.append((result.start, result.end))
        # Attribute Presidio types exactly as the ``presidio`` row does: the
        # entity types surviving AnonymizerEngine conflict resolution.
        for t in resolved_presidio_types(text, found, self._anonymizer):
            types.add(f"presidio:{t}")
        return {"text": _replace_spans(text, spans, "<PII>"), "detected_categories": sorted(types)}

    def category_map(self) -> dict[str, str]:
        return dict(UNION_TYPE_TO_CATEGORY)

    def entity_contract(self) -> frozenset[str]:
        if self._analyzer is None:
            self._analyzer = shared_default_analyzer()
        return frozenset(PIIGUARD_RULE_CONTRACT) | contract_for_entities(
            self._analyzer.get_supported_entities(language="en")
        )

    def describe(self) -> dict[str, Any]:
        info = super().describe()
        info["config"] = (
            "Union of Guard.from_dir(guard/) learned rules and presidio AnalyzerEngine() defaults "
            "(all recognizers, threshold 0) on the original text; merged spans replaced with <PII>"
        )
        info["entity_contract"] = sorted(self.entity_contract())
        return info


def _replace_spans(text: str, spans: list[tuple[int, int]], token: str) -> str:
    merged: list[list[int]] = []
    for start, end in sorted(spans):
        if merged and start <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    out, cursor = [], 0
    for start, end in merged:
        out.append(text[cursor:start])
        out.append(token)
        cursor = end
    out.append(text[cursor:])
    return "".join(out)
