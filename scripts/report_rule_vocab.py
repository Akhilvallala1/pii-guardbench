#!/usr/bin/env python3
"""Rule-vocabulary and shared-component report for piiguard-v2 vs pgb-dev-v1 vs pgb-v1.0 (P2 after review).

Complements scripts/check_dev_disjoint.py (which only proves template- and value-disjointness and is unchanged).
For every literal word in each rule pattern of a rules.yaml (cue words such as "Customer", "ask", "MRN"),
count prompts containing it (word-boundary, case-insensitive) in dev (prompts.jsonl + holdout2.jsonl) and in
the official pgb-v1.0 prompts. A word with official > 0 and dev == 0 is FLAGGED: dev could not have motivated it.
Also writes a declared list of generator components that dev shares with pgb-v1.0.
Never fails (exit 0); it is a report, not a gate. Uses string membership only, never scores.

    python scripts/report_rule_vocab.py RULES_YAML [--out results/piiguard-v2/rule_vocab_report.json]
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
OFFICIAL = ROOT / "data" / "official" / "pgb-v1.0" / "prompts.jsonl"
DEV = [ROOT / "data" / "dev" / "pgb-dev-v1" / "prompts.jsonl", ROOT / "data" / "dev" / "pgb-dev-v1" / "holdout2.jsonl"]

SHARED_COMPONENTS = [
    "generators.generate_prompts._ValueFactory (value pools/shapes reused by generate_dev.py)",
    "generators.generate_prompts._fill and validate_record",
    "obfuscation encoders (zero-width spacing, base64_wrapped, leet maps, spelled digits, split_sentences)",
    "SSN convention: invalid areas 000/666/9xx used for lookalike controls (invalid_ssn_shape)",
    "ICD-10 regex _ICD10_RE == v2 rule icd10_dotted",
    "API_KEY_PREFIXES (sk_test_, ghp_, xoxb-) == v2 rules api_key_prefixed/token_prefixed",
    "MRN- digits shape (pgb MRN shape; one of dev's three MRN shapes)",
    "Luhn-valid cards / Luhn-invalid lookalikes, 555-01xx phones",
    "parallel control semantics (ctl-d02 <-> ctl-02, ctl-d05 <-> ctl-05, ...)",
]


def _texts(paths) -> list[str]:
    out = []
    for p in paths:
        for line in p.read_text(encoding="utf-8").splitlines():
            if line.strip():
                out.append(json.loads(line)["text"])
    return out


def words(pattern: str) -> set[str]:
    p = re.sub(r"\[([A-Z])([a-z])\]", lambda m: m.group(2) if m.group(1).lower() == m.group(2) else m.group(0), pattern)
    p = re.sub(r"\(\?[a-zA-Z]*[:)]|\(\?<[=!]|\(\?[=!]", " ", p)
    p = re.sub(r"\\[A-Za-z]", " ", p)
    p = re.sub(r"\[[^\]]*\]", " ", p)
    return {w for w in re.findall(r"[A-Za-z][A-Za-z_]+", p)}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("rules", type=Path)
    ap.add_argument("--out", type=Path, default=ROOT / "results" / "piiguard-v2" / "rule_vocab_report.json")
    a = ap.parse_args()
    rules = yaml.safe_load(a.rules.read_text())["rules"]
    dev, off = _texts(DEV), _texts([OFFICIAL])
    rows, flagged = [], []
    for r in rules:
        for w in sorted(words(r["pattern"])):
            rx = re.compile(r"(?<![A-Za-z])" + re.escape(w) + r"(?![A-Za-z])", re.I)
            d = sum(1 for t in dev if rx.search(t))
            o = sum(1 for t in off if rx.search(t))
            row = {"rule": r["name"], "word": w, "dev_prompts": d, "official_prompts": o, "flag": o > 0 and d == 0}
            rows.append(row)
            if row["flag"]:
                flagged.append(row)
    rep = {"rules_file": str(a.rules), "n_dev_prompts": len(dev), "n_official_prompts": len(off),
           "note": "case-insensitive word-boundary prompt counts; flag = in official but not in dev",
           "flagged_official_only": flagged, "all": rows, "declared_shared_generator_components": SHARED_COMPONENTS}
    a.out.write_text(json.dumps(rep, indent=1) + "\n")
    print(f"{len(flagged)} rule words seen in official but not dev:")
    for f in flagged:
        print(f"  {f['rule']}: {f['word']} (official {f['official_prompts']})")
    print(f"wrote {a.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
