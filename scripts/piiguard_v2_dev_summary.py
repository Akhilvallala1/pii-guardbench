#!/usr/bin/env python3
"""Summarize dev-split runs (results/piiguard-v2/dev/<tag>-<split>-<layer>.json).

    python scripts/piiguard_v2_dev_summary.py TAG [TAG...] [--out FILE]

Prints, per tag/split/adapter: clean leak k/n, hard leak k/n, control raw FP k/n,
contract FP k/n, plus per-category span leak (clean+hard) and control FP by
template and detected type. All numbers are read from evaluator JSON output.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

DEV = Path(__file__).resolve().parent.parent / "results" / "piiguard-v2" / "dev"


def _load(tag: str, split: str, layer: str) -> dict:
    p = DEV / f"{tag}-{split}-{layer}.json"
    return json.loads(p.read_text()) if p.exists() else {}


def _kn(d: dict) -> str:
    return f"{d.get('k')}/{d.get('n')}" if d else "-"


def summarize(tags: list[str], detail: bool) -> str:
    lines = []
    for tag in tags:
        for split in ("train", "holdout", "holdout2"):
            runs = {L: _load(tag, split, L) for L in ("clean", "hard", "control")}
            if not any(runs.values()):
                continue
            adapters = [r["adapter"] for r in (runs["clean"] or runs["control"]).get("results", [])]
            for a in adapters:
                get = {L: next((x for x in runs[L].get("results", []) if x["adapter"] == a), {}) for L in runs}
                c, h, k = get["clean"], get["hard"], get["control"]
                lines.append(
                    f"{tag:<22} {split:<8} {a:<34} clean {_kn(c.get('leak', {})):>8}  hard {_kn(h.get('leak', {})):>8}  "
                    f"ctlFP {_kn(k.get('false_positive', {})):>8}  ctlCFP {_kn(k.get('contract_false_positive', {})):>8}  "
                    f"inscope clean {_kn((c.get('in_scope') or {}).get('prompt_leak', {})):>8} hard {_kn((h.get('in_scope') or {}).get('prompt_leak', {})):>8}"
                )
                if detail:
                    for L in ("clean", "hard"):
                        for cat, v in sorted((get[L].get("by_category") or {}).items()):
                            lines.append(f"    {L:<6} cat {cat:<15} {_kn(v)}")
                        for t, v in sorted((get[L].get("by_template") or {}).items()):
                            lines.append(f"    {L:<6} tpl {t:<15} {_kn(v)}")
                    for t, v in sorted((k.get("control_fp_by_template") or {}).items()):
                        lines.append(f"    control tpl {t:<10} raw {_kn(v['raw'])} contract {_kn(v['contract'])}")
                    lines.append(f"    control fp by type {json.dumps(k.get('control_fp_by_detected_type'))}")
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("tags", nargs="+")
    ap.add_argument("--detail", action="store_true")
    ap.add_argument("--out", type=Path)
    a = ap.parse_args()
    text = summarize(a.tags, a.detail)
    print(text)
    if a.out:
        a.out.write_text(text + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
