#!/usr/bin/env python3
"""Comparison table: committed pgb-v1.0 rows, first one-shot (frozen v2, cue-contaminated, historical),
and the SECOND disclosed run (v2-ablated, results/piiguard-v2/ablated/). Reads JSON only, runs nothing.

Writes results/piiguard-v2/ablated/comparison_table.{md,json}.
"""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = [  # (label, file pattern, adapters)
    ("committed", "results/pgb-v1.0-{L}.json", ("piiguard", "presidio", "piiguard-union-presidio")),
    ("first one-shot v2 (f540775e, test-only cues, historical)", "results/piiguard-v2/pgb-v1.0-{L}.json",
     ("piiguard-v2", "piiguard-v2-union-filtered")),
    ("SECOND run v2-ablated (21d97fe2, recommended)", "results/piiguard-v2/ablated/pgb-v1.0-{L}.json",
     ("piiguard-v2", "piiguard-v2-union-filtered")),
]
SCORING = ("blocked", "exact_leak", "normalized_leak", "partial_exposure", "false_positive",
           "contract_false_positive", "span_matches")


def _fmt(kn):
    if not kn or not kn.get("n"):
        return "n/a"
    s = f"{kn['k']}/{kn['n']} ({kn['k'] / kn['n']:.3f})"
    if kn.get("low") is not None:
        s += f" [{kn['low']:.3f}, {kn['high']:.3f}]"
    return s


def _ci(r, key):
    kn = dict(r.get(key) or {})
    kn.pop("low", None), kn.pop("high", None)  # Wilson; report the cluster bootstrap instead
    ci = r.get(f"{key}_cluster_bootstrap") or {}
    if ci.get("low") is not None:
        kn["low"], kn["high"] = ci["low"], ci["high"]
    return kn


def _per_prompt(path: Path, adapter: str):
    out = {}
    for line in path.read_text().splitlines():
        if line.strip():
            r = json.loads(line)
            if r["adapter"] == adapter:
                out[r["prompt_id"]] = tuple(json.dumps(r.get(f), sort_keys=True) for f in SCORING)
    return out


def main() -> int:
    rows, out = [], []
    hdr = "| run | adapter | clean leak | hard leak | control raw FP | control contract FP | blocked clean/hard |"
    rows += [hdr, "|" + "---|" * 7]
    for label, pat, adapters in SRC:
        d = {L: {r["adapter"]: r for r in json.loads((ROOT / pat.format(L=L)).read_text())["results"]}
             for L in ("clean", "hard", "control")}
        for a in adapters:
            c, h, k = d["clean"][a], d["hard"][a], d["control"][a]
            rec = {"run": label, "adapter": a, "clean_leak": _ci(c, "leak"), "hard_leak": _ci(h, "leak"),
                   "control_fp": _ci(k, "false_positive"), "control_contract_fp": _ci(k, "contract_false_positive"),
                   "blocked": [c.get("blocked_rate", {}).get("k"), h.get("blocked_rate", {}).get("k")],
                   "by_category_clean": c.get("by_category"), "by_category_hard": h.get("by_category")}
            out.append(rec)
            rows.append(f"| {label} | {a} | {_fmt(rec['clean_leak'])} | {_fmt(rec['hard_leak'])} | "
                        f"{_fmt(rec['control_fp'])} | {_fmt(rec['control_contract_fp'])} | {rec['blocked'][0]}/{rec['blocked'][1]} |")
    # per-prompt scoring identity checks
    checks = {}
    for L in ("clean", "hard", "control"):
        first = ROOT / f"results/piiguard-v2/pgb-v1.0-{L}/per_prompt.jsonl"
        abl = ROOT / f"results/piiguard-v2/ablated/pgb-v1.0-{L}/per_prompt.jsonl"
        v2_first, v2_abl = _per_prompt(first, "piiguard-v2"), _per_prompt(abl, "piiguard-v2")
        u_abl = _per_prompt(abl, "piiguard-v2-union-filtered")
        checks[L] = {
            "n": len(v2_abl),
            "first_vs_ablated_v2_scoring_diff_prompts": sorted(p for p in v2_abl if v2_abl[p] != v2_first.get(p)),
            "ablated_v2_vs_union_scoring_diff_prompts": sorted(p for p in v2_abl if v2_abl[p] != u_abl.get(p)),
        }
    rows.append("")
    for L, c in checks.items():
        rows.append(f"- {L}: n={c['n']}; prompts whose scoring fields differ first-vs-ablated v2: "
                    f"{len(c['first_vs_ablated_v2_scoring_diff_prompts'])}; ablated v2 vs union-filtered: "
                    f"{len(c['ablated_v2_vs_union_scoring_diff_prompts'])}")
    dst = ROOT / "results/piiguard-v2/ablated"
    (dst / "comparison_table.md").write_text("\n".join(rows) + "\n")
    (dst / "comparison_table.json").write_text(json.dumps({"rows": out, "per_prompt_checks": checks}, indent=1) + "\n")
    print("\n".join(rows))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
