#!/usr/bin/env python3
"""Diff per-prompt scoring fields between two dev-run tags (results/piiguard-v2/dev/<tag>-<split>-<layer>/).

    python scripts/piiguard_v2_scoring_diff.py TAG_A TAG_B [--splits train holdout holdout2] [--out FILE]

Scoring fields: blocked, exact_leak, normalized_leak, partial_exposure, false_positive,
contract_false_positive, span_matches. output_text and detected_types are reported separately
(not scoring). Exit 1 if any scoring field differs.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

DEV = Path(__file__).resolve().parent.parent / "results" / "piiguard-v2" / "dev"
SCORING = ("blocked", "exact_leak", "normalized_leak", "partial_exposure", "false_positive",
           "contract_false_positive", "span_matches")


def _rows(tag: str, split: str, layer: str) -> dict:
    p = DEV / f"{tag}-{split}-{layer}" / "per_prompt.jsonl"
    out = {}
    for line in p.read_text().splitlines():
        if line.strip():
            r = json.loads(line)
            out[(r["adapter"], r["prompt_id"])] = r
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("a")
    ap.add_argument("b")
    ap.add_argument("--splits", nargs="+", default=["train", "holdout", "holdout2"])
    ap.add_argument("--out", type=Path)
    a = ap.parse_args()
    report = {"a": a.a, "b": a.b, "scoring_fields": list(SCORING), "scoring_diffs": [], "compared": 0,
              "output_text_diffs": 0, "detected_types_diffs": 0}
    for split in a.splits:
        for layer in ("clean", "hard", "control"):
            ra, rb = _rows(a.a, split, layer), _rows(a.b, split, layer)
            if set(ra) != set(rb):
                report["scoring_diffs"].append({"split": split, "layer": layer, "key_mismatch": True})
                continue
            for k in ra:
                report["compared"] += 1
                for f in SCORING:
                    if ra[k].get(f) != rb[k].get(f):
                        report["scoring_diffs"].append({"split": split, "layer": layer, "adapter": k[0],
                                                        "prompt_id": k[1], "field": f})
                report["output_text_diffs"] += ra[k].get("output_text") != rb[k].get("output_text")
                report["detected_types_diffs"] += ra[k].get("detected_types") != rb[k].get("detected_types")
    text = json.dumps(report, indent=1)
    print(text)
    if a.out:
        a.out.write_text(text + "\n")
    return 1 if report["scoring_diffs"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
