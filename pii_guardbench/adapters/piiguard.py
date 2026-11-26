"""piiguard adapter (https://github.com/Akhilvallala1/piiguard), offline configs only.

Two offline configurations are provided, neither of which sends text anywhere:

* ``piiguard``: the committed learned regex rules (``guard/rules.yaml``) and no
  classifier. This is the guard's rule layer as shipped.
* ``piiguard-presidio``: the same rules plus piiguard's own
  ``PresidioDetector`` (its ``--detector presidio`` mode) as the base detector.

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
            self.name = "piiguard-presidio"
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
        }
