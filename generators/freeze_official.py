"""Freeze the official generated set (public split) with a SHA-256 manifest.

    python -m generators.freeze_official            # writes data/official/pgb-v1.0/
    python -m generators.freeze_official --check    # regenerate and verify the hash

The seed and size were fixed before any system was run on the set. The set is
public, so a system can be tuned to it; a hidden split is future work.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Sequence

from generators.generate_prompts import GENERATOR_VERSION, generate_prompts

ROOT = Path(__file__).resolve().parent.parent
OFFICIAL_DIR = ROOT / "data" / "official" / "pgb-v1.0"
OFFICIAL_NAME = "pgb-v1.0"
OFFICIAL_SEED = 2026
OFFICIAL_N = 1000
CONTROL_FRACTION = 0.2
HARD_FRACTION = 0.2


def render() -> tuple[list[dict[str, Any]], bytes]:
    records = generate_prompts(
        OFFICIAL_N, seed=OFFICIAL_SEED, control_fraction=CONTROL_FRACTION, hard_fraction=HARD_FRACTION
    )
    data = "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in records).encode("utf-8")
    return records, data


def build_manifest(records: list[dict[str, Any]], data: bytes) -> dict[str, Any]:
    try:
        commit = subprocess.run(
            ["git", "-C", str(ROOT), "rev-parse", "HEAD"], capture_output=True, text=True, check=True
        ).stdout.strip()
    except Exception:  # pragma: no cover - not a git checkout
        commit = None
    spans = [s for r in records for s in r["sensitive_spans"]]
    return {
        "name": OFFICIAL_NAME,
        "file": "prompts.jsonl",
        "sha256": hashlib.sha256(data).hexdigest(),
        "bytes": len(data),
        "n": len(records),
        "seed": OFFICIAL_SEED,
        "control_fraction": CONTROL_FRACTION,
        "hard_fraction": HARD_FRACTION,
        "generator": GENERATOR_VERSION,
        "repo_head_at_freeze": commit,  # parent of the commit that adds this set
        "scorer": "pii-guardbench scorer v2 (normalized leak primary)",
        "split": "public",
        "regenerate": f"python -m generators.generate_prompts --n {OFFICIAL_N} --seed {OFFICIAL_SEED}",
        "counts": {
            "layer": dict(sorted(Counter(r["metadata"]["layer"] for r in records).items())),
            "domain": dict(sorted(Counter(r["domain"] for r in records).items())),
            "template_id": dict(sorted(Counter(r["metadata"]["template_id"] for r in records).items())),
            "span_category": dict(sorted(Counter(s["category"] for s in spans).items())),
            "obfuscation": dict(
                sorted(Counter(r["metadata"]["obfuscation"] for r in records if "obfuscation" in r["metadata"]).items())
            ),
            "spans": len(spans),
        },
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="Verify the committed file and manifest.")
    args = parser.parse_args(argv)
    records, data = render()
    digest = hashlib.sha256(data).hexdigest()
    prompts_path = OFFICIAL_DIR / "prompts.jsonl"
    manifest_path = OFFICIAL_DIR / "manifest.json"
    if args.check:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        on_disk = hashlib.sha256(prompts_path.read_bytes()).hexdigest()
        ok = digest == on_disk == manifest["sha256"]
        print(f"regenerated={digest}\non_disk={on_disk}\nmanifest={manifest['sha256']}\n{'OK' if ok else 'MISMATCH'}")
        return 0 if ok else 1
    OFFICIAL_DIR.mkdir(parents=True, exist_ok=True)
    prompts_path.write_bytes(data)
    manifest_path.write_text(json.dumps(build_manifest(records, data), indent=2) + "\n", encoding="utf-8")
    print(f"wrote {prompts_path} ({len(records)} records) sha256={digest}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
