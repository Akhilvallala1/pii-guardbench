#!/usr/bin/env python3
"""One-shot comparison table: committed pgb-v1.0 rows (read only) + results/piiguard-v2/pgb-v1.0-*.json.

Writes results/piiguard-v2/oneshot_table.md and .json. No evaluation is run here.
"""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
COMMITTED = ("piiguard", "presidio", "piiguard-union-presidio")


def _rows(path: Path, names) -> dict:
    d = json.loads(path.read_text())
    return {r["adapter"]: r for r in d["results"] if r["adapter"] in names}


def _fmt(kn: dict, ci: dict | None) -> str:
    if not kn or not kn.get("n"):
        return "n/a"
    s = f"{kn['k']}/{kn['n']} ({kn['k'] / kn['n']:.3f})"
    if ci and ci.get("low") is not None:
        s += f" [{ci['low']:.3f}, {ci['high']:.3f}]"
    return s


def main() -> int:
    table, out = [], {}
    names = COMMITTED + ("piiguard-v2", "piiguard-v2-union-filtered")
    src = {}
    for L in ("clean", "hard", "control"):
        src[L] = {**_rows(ROOT / f"results/pgb-v1.0-{L}.json", COMMITTED),
                  **_rows(ROOT / f"results/piiguard-v2/pgb-v1.0-{L}.json", names)}
    hdr = "| adapter | clean leak | hard leak | control raw FP | control contract FP | in-scope clean | in-scope hard | blocked (clean/hard) | contract |"
    table += [hdr, "|" + "---|" * 9]
    for a in names:
        c, h, k = src["clean"][a], src["hard"][a], src["control"][a]
        row = {
            "clean_leak": _fmt(c["leak"], c["leak_cluster_bootstrap"]),
            "hard_leak": _fmt(h["leak"], h["leak_cluster_bootstrap"]),
            "control_fp": _fmt(k["false_positive"], k["false_positive_cluster_bootstrap"]),
            "control_contract_fp": _fmt(k["contract_false_positive"], k["contract_false_positive_cluster_bootstrap"]),
            "inscope_clean": _fmt(c["in_scope"]["prompt_leak"], c["in_scope"].get("prompt_leak_cluster_bootstrap")),
            "inscope_hard": _fmt(h["in_scope"]["prompt_leak"], h["in_scope"].get("prompt_leak_cluster_bootstrap")),
            "blocked": f"{c['blocked_rate']['k']}/{c['blocked_rate']['n']} / {h['blocked_rate']['k']}/{h['blocked_rate']['n']}",
            "contract": ",".join(c["in_scope"]["categories"]),
            "source": "committed" if a in COMMITTED else "results/piiguard-v2 one-shot",
        }
        out[a] = row
        table.append(f"| {a} | " + " | ".join(row[x] for x in ("clean_leak", "hard_leak", "control_fp", "control_contract_fp",
                                                               "inscope_clean", "inscope_hard", "blocked", "contract")) + " |")
    (ROOT / "results/piiguard-v2/oneshot_table.md").write_text("\n".join(table) + "\n")
    (ROOT / "results/piiguard-v2/oneshot_table.json").write_text(json.dumps(out, indent=2) + "\n")
    print("\n".join(table))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
