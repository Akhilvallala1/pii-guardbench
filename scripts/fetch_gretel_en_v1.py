#!/usr/bin/env python3
"""Download the Gretel test split, convert it, and write a small seeded sample.

Usage:
    python scripts/fetch_gretel_en_v1.py [--n 500] [--seed 20261126]

The full parquet file is cached under ``data/external/gretel-en-v1/_cache/``
(git-ignored) and is never committed. Only ``sample.jsonl`` and the stats
file are written for commit. Requires ``pyarrow`` (``pip install -e ".[data]"``).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from pii_guardbench.datasets.gretel import (  # noqa: E402
    DATASET_ID,
    convert_rows,
    exclude_secret_like,
    stratified_sample,
    write_jsonl,
)

OUT_DIR = ROOT / "data" / "external" / "gretel-en-v1"
CACHE_DIR = OUT_DIR / "_cache"
SPLIT = "test"
DEFAULT_SEED = 20261126
DEFAULT_N = 500
DEFAULT_MIN_PER_CATEGORY = 25


def hub_revision() -> str:
    url = f"https://huggingface.co/api/datasets/{DATASET_ID}"
    with urllib.request.urlopen(url, timeout=60) as response:
        return json.load(response)["sha"]


def download(revision: str) -> Path:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    target = CACHE_DIR / f"{SPLIT}-{revision[:12]}.parquet"
    if target.exists():
        return target
    url = (
        f"https://huggingface.co/datasets/{DATASET_ID}/resolve/{revision}"
        f"/data/{SPLIT}-00000-of-00001.parquet"
    )
    print(f"Downloading {url}", file=sys.stderr)
    urllib.request.urlretrieve(url, target)  # noqa: S310 (fixed https URL)
    return target


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--n", type=int, default=DEFAULT_N, help="sample size")
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--min-per-category", type=int, default=DEFAULT_MIN_PER_CATEGORY)
    parser.add_argument("--revision", default=None, help="pin a dataset commit SHA")
    args = parser.parse_args()

    import pyarrow.parquet as pq

    revision = args.revision or hub_revision()
    parquet_path = download(revision)
    table = pq.read_table(parquet_path)
    rows = table.to_pylist()
    records, stats = convert_rows(rows)
    eligible, excluded_secret_like = exclude_secret_like(records)
    sample = stratified_sample(
        eligible, args.n, args.seed, min_per_category=args.min_per_category
    )

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    write_jsonl(sample, OUT_DIR / "sample.jsonl")

    sample_categories: dict[str, int] = {}
    sample_records_with: dict[str, int] = {}
    for rec in sample:
        for span in rec["sensitive_spans"]:
            sample_categories[span["category"]] = sample_categories.get(span["category"], 0) + 1
        for category in {s["category"] for s in rec["sensitive_spans"]}:
            sample_records_with[category] = sample_records_with.get(category, 0) + 1
    summary = {
        "dataset": DATASET_ID,
        "split": SPLIT,
        "revision": revision,
        "parquet_sha256": sha256(parquet_path),
        "sample_seed": args.seed,
        "sample_size_requested": args.n,
        "sample_size": len(sample),
        "min_records_per_category_floor": args.min_per_category,
        "conversion": stats.to_dict(),
        "records_excluded_secret_like_before_sampling": excluded_secret_like,
        "records_eligible_for_sampling": len(eligible),
        "sample_spans_by_category": dict(sorted(sample_categories.items())),
        "sample_records_with_category": dict(sorted(sample_records_with.items())),
        "sample_records_without_pii": sum(1 for r in sample if not r["sensitive_spans"]),
    }
    (OUT_DIR / "conversion_stats.json").write_text(
        json.dumps(summary, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
