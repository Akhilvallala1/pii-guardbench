#!/usr/bin/env python3
"""Choose the Presidio entity whitelist for piiguard-v2-union-filtered on pgb-dev-v1 ONLY.

For every default AnalyzerEngine() entity type (threshold 0, same engine as the
``presidio`` row), count on the full dev control layer (train+holdout, 200 prompts)
the controls where that type produced at least one finding, and on dev clean+hard
the planted spans it overlaps. Selection rule (fixed before looking at the counts):

  keep type T iff (a) T maps to a benchmark category (PRESIDIO_TO_CATEGORY), so it is
  inside the declared contract, and (b) T fires on <= 2 of 200 dev controls (1%).

Writes results/piiguard-v2/dev/union_whitelist.json.
"""

from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from pii_guardbench.adapters.presidio import PRESIDIO_TO_CATEGORY, shared_default_analyzer  # noqa: E402
from pii_guardbench.prompts import load_prompts  # noqa: E402

MAX_CONTROL_HITS = 2


def main() -> int:
    prompts = load_prompts(ROOT / "data" / "dev" / "pgb-dev-v1" / "prompts.jsonl")
    analyzer = shared_default_analyzer()
    supported = sorted(analyzer.get_supported_entities(language="en"))
    ctl_hits: Counter[str] = Counter()
    span_hits: Counter[str] = Counter()
    span_hits_by_cat: dict[str, Counter[str]] = {}
    n_ctl = n_spans = 0
    for p in prompts:
        found = analyzer.analyze(text=p["text"], language="en", score_threshold=0.0)
        if not p["sensitive_spans"]:
            n_ctl += 1
            for t in {r.entity_type for r in found}:
                ctl_hits[t] += 1
            continue
        for s in p["sensitive_spans"]:
            n_spans += 1
            for t in {r.entity_type for r in found if r.start < s["end"] and r.end > s["start"]}:
                span_hits[t] += 1
                span_hits_by_cat.setdefault(t, Counter())[s["category"]] += 1
    rows = []
    for t in supported:
        in_contract = t in PRESIDIO_TO_CATEGORY
        keep = in_contract and ctl_hits[t] <= MAX_CONTROL_HITS
        rows.append({"type": t, "category": PRESIDIO_TO_CATEGORY.get(t), "dev_control_hits": ctl_hits[t],
                     "dev_span_overlaps": span_hits[t], "span_overlaps_by_category": dict(span_hits_by_cat.get(t, {})),
                     "keep": keep})
    out = {"rule": f"in PRESIDIO_TO_CATEGORY and dev_control_hits <= {MAX_CONTROL_HITS}/{n_ctl}",
           "n_controls": n_ctl, "n_spans": n_spans, "types": rows,
           "whitelist": [r["type"] for r in rows if r["keep"]]}
    path = ROOT / "results" / "piiguard-v2" / "dev" / "union_whitelist.json"
    path.write_text(json.dumps(out, indent=2) + "\n")
    for r in rows:
        print(f"{r['type']:<22} cat={str(r['category']):<15} ctl={r['dev_control_hits']:>3}/{n_ctl} spans={r['dev_span_overlaps']:>4} keep={r['keep']}")
    print("whitelist:", out["whitelist"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
