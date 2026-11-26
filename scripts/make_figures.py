"""Leak vs cost (FPR, block rate) Pareto figures from the pgb-v1.0 result JSONs.

    python scripts/make_figures.py [--results results] [--out results/figures]

Reads ``pgb-v1.0-{clean,hard,control}.json`` and writes PNGs:

* ``a_clean_leak_vs_raw_fpr.png``
* ``b_clean_inscope_leak_vs_contract_fpr.png``
* ``c_hard_leak_vs_raw_fpr.png``
* ``d_clean_leak_vs_clean_block_rate.png``

One point per non-mock adapter (mocks excluded), labeled; error bars are the
cluster-bootstrap 95% CIs on both axes (in-scope leak in (b) uses its Wilson
interval: the JSON has no cluster CI for it, noted on the axis). In (a)-(c)
marker color encodes the clean ``blocked_rate``. The Pareto frontier
(lower-left is better) is a step line. Needs the ``figures`` extra (matplotlib).
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Optional

ROOT = Path(__file__).resolve().parent.parent


def _load(path: Path) -> dict[str, dict[str, Any]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    return {r["adapter"]: r for r in data["results"] if not r["adapter"].startswith("mock")}


def _point(rate: Any, ci: Optional[dict]) -> Optional[tuple[float, float, float]]:
    """(value, lower err, upper err) or None."""
    if rate is None:
        return None
    lo = ci.get("low") if ci else None
    hi = ci.get("high") if ci else None
    lo = rate if lo is None else lo
    hi = rate if hi is None else hi
    return float(rate), max(0.0, rate - lo), max(0.0, hi - rate)


def pareto_front(points: list[tuple[float, float]]) -> list[tuple[float, float]]:
    """Non-dominated points (minimize x and y), sorted by x."""
    front: list[tuple[float, float]] = []
    best_y = float("inf")
    for x, y in sorted(points):
        if y < best_y:
            front.append((x, y))
            best_y = y
    return front


def build_series(results_dir: Path) -> dict[str, list[dict[str, Any]]]:
    clean = _load(results_dir / "pgb-v1.0-clean.json")
    hard = _load(results_dir / "pgb-v1.0-hard.json")
    control = _load(results_dir / "pgb-v1.0-control.json")
    names = sorted(set(clean) & set(control) & set(hard))
    series: dict[str, list[dict[str, Any]]] = {k: [] for k in "abcd"}
    for n in names:
        c, h, k = clean[n], hard[n], control[n]
        block = (c.get("blocked_rate") or {}).get("rate") or 0.0
        raw_fpr = _point(k["false_positive"]["rate"], k.get("false_positive_cluster_bootstrap"))
        cfp = k.get("contract_false_positive")
        cfpr = _point(cfp["rate"], k.get("contract_false_positive_cluster_bootstrap")) if cfp else None
        cleak = _point(c["leak"]["rate"], c.get("leak_cluster_bootstrap"))
        hleak = _point(h["leak"]["rate"], h.get("leak_cluster_bootstrap"))
        ins = (c.get("in_scope") or {}).get("prompt_leak")
        inleak = _point(ins["rate"], ins) if ins else None
        bl = c.get("blocked_rate") or {}
        bpt = _point(bl.get("rate"), bl)
        for key, x, y in (("a", raw_fpr, cleak), ("b", cfpr, inleak), ("c", raw_fpr, hleak), ("d", bpt, cleak)):
            if x is not None and y is not None:
                series[key].append({"name": n, "x": x, "y": y, "block": block})
    return series


FIGS = {
    "a": ("a_clean_leak_vs_raw_fpr.png", "Clean leak vs raw control FPR",
          "Raw control FPR (cluster-boot 95%)", "Clean normalized leak (cluster-boot 95%)", True),
    "b": ("b_clean_inscope_leak_vs_contract_fpr.png", "Clean in-scope leak vs contract-aware FPR",
          "Contract-aware control FPR (cluster-boot 95%)", "Clean in-scope prompt leak (Wilson 95%)", True),
    "c": ("c_hard_leak_vs_raw_fpr.png", "Hard leak vs raw control FPR",
          "Raw control FPR (cluster-boot 95%)", "Hard normalized leak (cluster-boot 95%)", True),
    "d": ("d_clean_leak_vs_clean_block_rate.png", "Clean leak vs clean block rate",
          "Clean blocked rate (share of PII prompts blocked, Wilson 95%)",
          "Clean normalized leak (cluster-boot 95%)", False),
}


def make_figures(results_dir: Path, out_dir: Path) -> list[Path]:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    matplotlib.rcParams["svg.hashsalt"] = "pgb"
    series = build_series(results_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    written = []
    for key, (fname, title, xl, yl, color_block) in FIGS.items():
        pts = series[key]
        fig, ax = plt.subplots(figsize=(7.5, 6), dpi=120)
        sc = None
        for p in pts:
            ax.errorbar(p["x"][0], p["y"][0], xerr=[[p["x"][1]], [p["x"][2]]],
                        yerr=[[p["y"][1]], [p["y"][2]]], fmt="none", ecolor="0.6", lw=0.8, zorder=1)
        if pts:
            xs = [p["x"][0] for p in pts]
            ys = [p["y"][0] for p in pts]
            if color_block:
                sc = ax.scatter(xs, ys, c=[p["block"] for p in pts], cmap="viridis", vmin=0, vmax=1,
                                s=50, zorder=3, edgecolors="k", linewidths=0.5)
                cb = fig.colorbar(sc, ax=ax)
                cb.set_label("Clean blocked rate (legend)")
            else:
                ax.scatter(xs, ys, s=50, zorder=3, color="tab:blue", edgecolors="k", linewidths=0.5)
            for p in pts:
                ax.annotate(p["name"], (p["x"][0], p["y"][0]), textcoords="offset points",
                            xytext=(4, 4), fontsize=7)
            front = pareto_front(list(zip(xs, ys)))
            fx = [f[0] for f in front]
            fy = [f[1] for f in front]
            ax.step(fx, fy, where="post", color="tab:red", lw=1.2, label="Pareto frontier (lower-left better)", zorder=2)
            ax.legend(loc="upper right", fontsize=8)
        ax.set_xlim(-0.03, 1.03)
        ax.set_ylim(-0.03, 1.03)
        ax.set_xlabel(xl)
        ax.set_ylabel(yl)
        ax.set_title(title + " (non-mock adapters)")
        ax.grid(True, alpha=0.3)
        fig.tight_layout()
        path = out_dir / fname
        fig.savefig(path, metadata={"Software": None})
        plt.close(fig)
        written.append(path)
    return written


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--results", type=Path, default=ROOT / "results")
    ap.add_argument("--out", type=Path, default=ROOT / "results" / "figures")
    args = ap.parse_args()
    for p in make_figures(args.results, args.out):
        print(p)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
