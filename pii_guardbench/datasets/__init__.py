"""Converters from public span-labeled PII datasets into the PII-GuardBench schema.

Full datasets are never committed. Only small, seeded samples live under
``data/external/<name>/sample.jsonl``.
"""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

# CLI dataset name -> committed sample path.
DATASET_SAMPLES: dict[str, Path] = {
    "gretel-en-v1": REPO_ROOT / "data" / "external" / "gretel-en-v1" / "sample.jsonl",
}


def dataset_sample_path(name: str) -> Path:
    try:
        return DATASET_SAMPLES[name]
    except KeyError as exc:
        known = ", ".join(sorted(DATASET_SAMPLES)) or "(none)"
        raise KeyError(f"Unknown dataset {name!r}. Known datasets: {known}") from exc
