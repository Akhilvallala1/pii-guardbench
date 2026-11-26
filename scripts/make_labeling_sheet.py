"""Build a stratified human-labeling sample from a frozen-set run (no scorer labels in the sheet).

    python scripts/make_labeling_sheet.py

Reads ``results/pgb-v1.0-{clean,control,hard}/per_prompt.jsonl`` and the frozen
prompts, samples (prompt, system output) items stratified by layer and spread
round-robin over the real (non-mock) systems, and writes:

* ``labeling/pgb-v1.0-sample.csv``: what an annotator sees (prompt, planted
  values, the system output with the system name hidden) and blank label columns;
* ``labeling/pgb-v1.0-key.csv``: item id -> system and the automatic scorer's
  labels, kept separate so annotators are blind to them.

Two annotators label the same sheet independently; agreement (Cohen's kappa)
between them and with the scorer is computed afterwards.
"""

from __future__ import annotations

import csv
import json
import random
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PROMPTS = ROOT / "data" / "official" / "pgb-v1.0" / "prompts.jsonl"
OUT = ROOT / "labeling"
SEED = 11
PER_LAYER = {"clean": 60, "hard": 60, "control": 30}


def main() -> int:
    prompts = {}
    for line in PROMPTS.read_text(encoding="utf-8").splitlines():
        rec = json.loads(line)
        prompts[rec["id"]] = rec
    rng = random.Random(SEED)
    sheet, key = [], []
    for layer, k in PER_LAYER.items():
        rows = [json.loads(l) for l in (ROOT / "results" / f"pgb-v1.0-{layer}" / "per_prompt.jsonl").read_text(encoding="utf-8").splitlines()]
        rows = [r for r in rows if not r["adapter"].startswith("mock")]
        by_prompt = defaultdict(dict)
        for r in rows:
            by_prompt[r["prompt_id"]][r["adapter"]] = r
        systems = sorted({r["adapter"] for r in rows})
        # Stratify by template: shuffle prompts, then take them template by template.
        by_template = defaultdict(list)
        for pid in sorted(by_prompt):
            by_template[prompts[pid]["metadata"]["template_id"]].append(pid)
        for pids in by_template.values():
            rng.shuffle(pids)
        order = []
        while any(by_template.values()):
            for t in sorted(by_template):
                if by_template[t]:
                    order.append(by_template[t].pop())
        for i, pid in enumerate(order[:k]):
            system = systems[(i + rng.randrange(len(systems))) % len(systems)]
            r = by_prompt[pid][system]
            p = prompts[pid]
            item = f"{layer}-{len([s for s in sheet if s['item_id'].startswith(layer)]) + 1:03d}"
            planted = "; ".join(
                f"{s['category']}={s.get('canonical_value', s['value'])!r}" for s in p["sensitive_spans"]
            ) or "(none: no-PII control)"
            sheet.append({
                "item_id": item,
                "prompt_text": p["text"],
                "planted_values": planted,
                "system_output": r["output_text"],
                "label_value_exposed (yes/partial/no/na)": "",
                "label_control_damaged (yes/no/na)": "",
                "label_unlabeled_pii_in_prompt (yes/no)": "",
                "notes": "",
            })
            key.append({
                "item_id": item, "prompt_id": pid, "layer": layer, "template_id": p["metadata"]["template_id"],
                "system": system, "scorer_normalized_leak": r["normalized_leak"], "scorer_exact_leak": r["exact_leak"],
                "scorer_partial": r["partial_exposure"], "scorer_false_positive": r["false_positive"], "blocked": r["blocked"],
            })
    OUT.mkdir(exist_ok=True)
    for name, rows in (("pgb-v1.0-sample.csv", sheet), ("pgb-v1.0-key.csv", key)):
        with (OUT / name).open("w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=list(rows[0]))
            w.writeheader()
            w.writerows(rows)
    print(f"wrote {len(sheet)} items to {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
