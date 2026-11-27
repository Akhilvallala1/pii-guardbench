#!/usr/bin/env python3
"""Check that the dev split pgb-dev-v1 is disjoint from the frozen pgb-v1.0 test set.

Checks (both directions):
1. No dev template string equals a pgb-v1.0 template string (clean, hard, control).
2. No literal template fragment (text between placeholders, >= MIN_FRAGMENT chars,
   whitespace-stripped) of one set occurs in any prompt text of the other.
3. No planted PII value string (span ``value`` and ``canonical_value``) of one set
   occurs as a substring of any prompt text of the other.

Exit 0 and print OK if disjoint; exit 1 and list the collisions otherwise.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from generators import generate_dev as dev  # noqa: E402
from generators import generate_prompts as gp  # noqa: E402

OFFICIAL = ROOT / "data" / "official" / "pgb-v1.0" / "prompts.jsonl"
DEV = ROOT / "data" / "dev" / "pgb-dev-v1" / "prompts.jsonl"
DEV_H2 = ROOT / "data" / "dev" / "pgb-dev-v1" / "holdout2.jsonl"
MIN_FRAGMENT = 16
_PH = re.compile(r"\{\w+\}")


def _load(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def official_templates() -> list[str]:
    return [t[-1] for t in gp.CLEAN_TEMPLATES] + [t[-1] for t in gp.HARD_TEMPLATES] + [t[-1] for t in gp.CONTROL_TEMPLATES]


def dev_templates() -> list[str]:
    return [t[-1] for group in (dev.DEV_CLEAN_TEMPLATES, dev.DEV_HARD_TEMPLATES, dev.DEV_CONTROL_TEMPLATES,
                                dev.H2_CLEAN_TEMPLATES, dev.H2_HARD_TEMPLATES, dev.H2_CONTROL_TEMPLATES)
            for t in group]


def fragments(template: str) -> list[str]:
    return [f.strip() for f in _PH.split(template) if len(f.strip()) >= MIN_FRAGMENT]


def values(records: list[dict[str, Any]]) -> set[str]:
    out: set[str] = set()
    for r in records:
        for s in r["sensitive_spans"]:
            out.add(s["value"])
            if s.get("canonical_value"):
                out.add(s["canonical_value"])
    return out


def check(dev_records: list[dict[str, Any]], off_records: list[dict[str, Any]],
          dev_tpls: list[str], off_tpls: list[str]) -> list[str]:
    problems: list[str] = []
    for t in set(dev_tpls) & set(off_tpls):
        problems.append(f"identical template: {t!r}")
    off_text = "\n\x00".join(r["text"] for r in off_records)
    dev_text = "\n\x00".join(r["text"] for r in dev_records)
    for t in dev_tpls:
        for f in fragments(t):
            if f in off_text:
                problems.append(f"dev template fragment in pgb-v1.0: {f!r}")
    for t in off_tpls:
        for f in fragments(t):
            if f in dev_text:
                problems.append(f"pgb-v1.0 template fragment in dev: {f!r}")
    for v in sorted(values(dev_records)):
        if v in off_text:
            problems.append(f"dev value in pgb-v1.0: {v!r}")
    for v in sorted(values(off_records)):
        if v in dev_text:
            problems.append(f"pgb-v1.0 value in dev: {v!r}")
    return problems


def main() -> int:
    problems = check(_load(DEV) + _load(DEV_H2), _load(OFFICIAL), dev_templates(), official_templates())
    for p in problems:
        print(p)
    print("OK" if not problems else f"{len(problems)} collisions")
    return 0 if not problems else 1


if __name__ == "__main__":
    raise SystemExit(main())
