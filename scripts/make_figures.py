"""Leak vs cost (FPR, block rate) Pareto figures from the pgb-v1.0 result JSONs.

    python scripts/make_figures.py [--results results] [--out results/figures]

Reads ``pgb-v1.0-{clean,hard,control}.json`` and writes PNG (300 dpi) + PDF:

* ``b_clean_inscope_leak_vs_contract_fpr``  main text, single column (3.3 x 2.6 in, 8 pt)
* ``d_clean_leak_vs_clean_block_rate``      main text, single column (3.3 x 2.6 in, 8 pt)
* ``a_clean_leak_vs_raw_fpr``               appendix (6.3 x 3.6 in, 9 pt)
* ``c_hard_leak_vs_raw_fpr``                appendix (6.3 x 3.6 in, 9 pt)

One point per non-mock adapter. Error bars are cluster-bootstrap 95% CIs on
both axes (in-scope leak, blocked rate and FPRs all have stored cluster CIs).
Color in (a), (b) = clean ``blocked_rate``; in (c) = hard ``blocked_rate``.
The Pareto frontier (lower-left is better) is a step line. Labels use a fixed
short-name map; systems within 0.01 on both axes share one merged label; labels
are placed greedily over candidate offsets so their bounding boxes (measured
with the renderer) never overlap each other, the markers or the axes edge.
Needs the ``figures`` extra (matplotlib).
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import math
from typing import Any, Optional

ROOT = Path(__file__).resolve().parent.parent

SHORT = {
    "presidio": "presidio",
    "presidio-all-t0.4": "pres-all@.4",
    "presidio-nemo6-t0": "pres-n6@0",
    "presidio-nemo6-t0.4": "pres-n6@.4",
    "nemo-guardrails": "nemo-mask",
    "nemo-guardrails-all": "nemo-all",
    "nemo-guardrails-detect": "nemo-det",
    "piiguard": "piiguard",
    "piiguard-pgpresidio": "pg-pres",
    "piiguard-union-presidio": "union",
    "gliner-pii": "gliner",
    "llm-guard-anonymize": "llm-anon",
    "llm-guard-block": "llm-blk",
}
MERGE_TOL = 0.01

# Filled by make_figures: figure key -> list of label bboxes (display coords,
# (x0, y0, x1, y1)), used by the no-overlap test.
LABEL_BOXES: dict[str, list[tuple[float, float, float, float]]] = {}


def short_name(name: str) -> str:
    return SHORT.get(name, name)


def merge_label(names: list[str]) -> str:
    """'pres-n6@0', 'pres-n6@.4', 'nemo-mask' -> 'pres-n6@0/@.4, nemo-mask'."""
    parts: list[tuple[str, list[str]]] = []
    for n in names:
        s = short_name(n)
        stem, at, suf = s.partition("@")
        for p in parts:
            if at and p[0] == stem and p[1] and p[1][0].startswith("@"):
                p[1].append("@" + suf)
                break
        else:
            parts.append((stem, ["@" + suf] if at else []))
    return ", ".join(stem + "/".join(sufs) if sufs else stem for stem, sufs in parts)


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


def _req(d: dict[str, Any], key: str, name: str, where: str) -> Any:
    """Required key: no silent Wilson/0 fallback (figures promise cluster-bootstrap CIs)."""
    v = d.get(key)
    if v is None or (isinstance(v, dict) and v.get("rate") is None and key == "blocked_rate"):
        raise KeyError(f"{where} results for {name!r} lack required key {key!r}; regenerate the results JSON "
                       "with the current evaluator (no fallback is used)")
    return v


def build_series(results_dir: Path) -> dict[str, list[dict[str, Any]]]:
    clean = _load(results_dir / "pgb-v1.0-clean.json")
    hard = _load(results_dir / "pgb-v1.0-hard.json")
    control = _load(results_dir / "pgb-v1.0-control.json")
    names = sorted(set(clean) & set(control) & set(hard))
    series: dict[str, list[dict[str, Any]]] = {k: [] for k in "abcd"}
    for n in names:
        c, h, k = clean[n], hard[n], control[n]
        cblock = _req(c, "blocked_rate", n, "clean")["rate"]
        hblock = _req(h, "blocked_rate", n, "hard")["rate"]
        raw_fpr = _point(k["false_positive"]["rate"], _req(k, "false_positive_cluster_bootstrap", n, "control"))
        cfp = k.get("contract_false_positive")
        cfpr = (_point(cfp["rate"], _req(k, "contract_false_positive_cluster_bootstrap", n, "control"))
                if cfp else None)
        cleak = _point(c["leak"]["rate"], _req(c, "leak_cluster_bootstrap", n, "clean"))
        hleak = _point(h["leak"]["rate"], _req(h, "leak_cluster_bootstrap", n, "hard"))
        ins = (c.get("in_scope") or {})
        pl = ins.get("prompt_leak")
        inleak = (_point(pl["rate"], _req(ins, "prompt_leak_cluster_bootstrap", n, "clean in_scope"))
                  if pl else None)
        bpt = _point(cblock, _req(c, "blocked_rate_cluster_bootstrap", n, "clean"))
        for key, x, y, color in (("a", raw_fpr, cleak, cblock), ("b", cfpr, inleak, cblock),
                                 ("c", raw_fpr, hleak, hblock), ("d", bpt, cleak, None)):
            if x is not None and y is not None:
                series[key].append({"name": n, "x": x, "y": y, "block": color})
    return series


MAIN = {"size": (3.3, 2.6), "font": 8}
APPX = {"size": (6.3, 3.6), "font": 9}
FIGS = {
    "a": ("a_clean_leak_vs_raw_fpr", APPX, "Raw control FPR", "Clean leak", "Clean block rate"),
    "b": ("b_clean_inscope_leak_vs_contract_fpr", {**MAIN, "zoom": True}, "Contract-aware control FPR",
          "Clean in-scope leak", "Clean block rate"),
    "c": ("c_hard_leak_vs_raw_fpr", APPX, "Raw control FPR", "Hard leak", "Hard-layer block rate"),
    "d": ("d_clean_leak_vs_clean_block_rate", MAIN, "Clean block rate (PII prompts)", "Clean leak", None),
}


def _groups(pts: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    groups: list[list[dict[str, Any]]] = []
    order = [n for n in SHORT]
    for p in sorted(pts, key=lambda p: (order.index(p["name"]) if p["name"] in order else 99, p["name"])):
        for g in groups:
            if abs(g[0]["x"][0] - p["x"][0]) <= MERGE_TOL and abs(g[0]["y"][0] - p["y"][0]) <= MERGE_TOL:
                g.append(p)
                break
        else:
            groups.append([p])
    return groups


def _seg_hits_box(p0, p1, box, pad: float = 1.0) -> bool:
    """Liang-Barsky clip: does segment p0-p1 pass through ``box`` (padded)?"""
    x0, y0 = p0
    dx, dy = p1[0] - x0, p1[1] - y0
    lo, hi = 0.0, 1.0
    for p, q in ((-dx, x0 - (box[0] - pad)), (dx, (box[2] + pad) - x0),
                 (-dy, y0 - (box[1] - pad)), (dy, (box[3] + pad) - y0)):
        if p == 0:
            if q < 0:
                return False
            continue
        t = q / p
        if p < 0:
            lo = max(lo, t)
        else:
            hi = min(hi, t)
        if lo > hi:
            return False
    return True


def _box_dist(box, pt) -> float:
    dx = max(box[0] - pt[0], 0.0, pt[0] - box[2])
    dy = max(box[1] - pt[1], 0.0, pt[1] - box[3])
    return math.hypot(dx, dy)


def _anchor(box, pt):
    """Leader end used for crossing checks: the label center. Matplotlib draws the
    leader from the point toward the text and clips it at the text bbox, so the
    drawn line is a sub-segment of point -> center (conservative)."""
    return ((box[0] + box[2]) / 2, (box[1] + box[3]) / 2)


def _ok(box, own, placed, markers, leaders, others, leadered) -> bool:
    """Label box acceptable: no label/marker overlap, its leader (if any) crosses
    no label, no existing leader crosses it, and an un-leadered label is
    unambiguous (nearer its own point than any other point)."""
    if any(_overlap(box, o) for o in placed) or any(_overlap(box, m, 0) for m in markers):
        return False
    if any(_seg_hits_box(a, b, box, 0.5) for a, b in leaders):
        return False
    if leadered:
        seg = (own, _anchor(box, own))
        if any(_seg_hits_box(seg[0], seg[1], o, 0.5) for o in placed):
            return False
        if any(_seg_hits_box(seg[0], seg[1], (q[0] - 3, q[1] - 3, q[0] + 3, q[1] + 3), 2.0)
               for q in others if math.dist(q, own) > 8):
            return False
    else:
        d = _box_dist(box, own)
        if any(_box_dist(box, o) < 1.5 * d + 6.0 for o in others):
            return False
    return True


def _overlap(a, b, pad: float = 1.0) -> bool:
    return not (a[2] + pad <= b[0] or b[2] + pad <= a[0] or a[3] + pad <= b[1] or b[3] + pad <= a[1])


def _column(ax, renderer, axbox, groups, pos, crowded, fontsize, px, leader, markers, placed, artists, leaders) -> bool:
    """Callout column beside one crowded component, labels in the points' vertical order."""
    for wrap in (False, True):
        for off in (30, 60, 90, 120):
            for side in ("right", "left"):
                if _column_side(ax, renderer, axbox, groups, pos, crowded, fontsize, px, leader, markers,
                                placed, artists, leaders, side, off, wrap):
                    return True
    return False


def _column_side(ax, renderer, axbox, groups, pos, crowded, fontsize, px, leader, markers, placed, artists, leaders,
                 side, off=30, wrap=False) -> bool:
    """Labels stacked bottom-to-top in the points' vertical order at a fixed x, each
    stepped upward past markers and earlier labels. All-or-nothing."""
    col_x = (max(pos[i][0] for i in crowded) + off * px if side == "right"
             else min(pos[i][0] for i in crowded) - off * px)
    order = sorted(crowded, key=lambda i: (pos[i][1], pos[i][0]))
    col: list[tuple[Any, tuple[float, float, float, float]]] = []
    floor = axbox.y0 + 1
    ok = True
    n_leaders = len(leaders)
    for i in order:
        g = groups[i]
        text = merge_label([p["name"] for p in g])
        if wrap:
            text = text.replace(", ", ",\n")
        y = pos[i][1]
        t = ax.annotate(text, (g[0]["x"][0], g[0]["y"][0]), xytext=(0, 0), textcoords="data",
                        fontsize=fontsize, ha="left" if side == "right" else "right", va="center",
                        zorder=5, arrowprops=leader)
        while True:
            t.xyann = tuple(ax.transData.inverted().transform((col_x, y)))
            bb = _text_extent(t, renderer)
            box = (bb.x0, bb.y0, bb.x1, bb.y1)
            if box[1] < floor:
                y += floor - box[1]
                continue
            if box[3] > axbox.y1 - 1 or box[2] > axbox.x1 - 1 or box[0] < axbox.x0 + 1:
                ok = False
                break
            hit = [o for o in [c[1] for c in col] + placed + markers if _overlap(box, o)]
            if not hit:
                own = pos[i]
                seg = (own, _anchor(box, own))
                if any(_seg_hits_box(a, b, box, 0.5) for a, b in leaders) or any(
                        _seg_hits_box(seg[0], seg[1], o, 0.5) for o in placed + [c[1] for c in col]):
                    ok = False
                break
            y += max(o[3] for o in hit) - box[1] + 1.5
        t._pgb_group = g
        col.append((t, box))
        if ok:
            leaders.append((pos[i], _anchor(box, pos[i])))
        if not ok:
            break
        floor = box[3] + 1.5
    if ok:
        for t, box in col:
            placed.append(box)
            artists.append(t)
        return True
    for t, _ in col:
        t.remove()
    del leaders[n_leaders:]
    return False


def _place_labels(fig, ax, groups, fontsize, lines=()) -> list[Any]:
    """Greedy deterministic placement; returns the label annotation artists."""
    renderer = fig.canvas.get_renderer()
    axbox = ax.get_window_extent(renderer)
    px = fig.dpi / 72.0
    marker_r = 3.2 * px
    markers = []
    for g in groups:
        for p in g:
            mx, my = ax.transData.transform((p["x"][0], p["y"][0]))
            markers.append((mx - marker_r, my - marker_r, mx + marker_r, my + marker_r))
    for (x0, y0), (x1, y1) in lines:  # Pareto step segments as thin obstacles
        a0 = ax.transData.transform((x0, y0))
        a1 = ax.transData.transform((x1, y1))
        markers.append((min(a0[0], a1[0]) - px, min(a0[1], a1[1]) - px,
                        max(a0[0], a1[0]) + px, max(a0[1], a1[1]) + px))
    leg = ax.get_legend()
    if leg is not None:
        lb = leg.get_window_extent(renderer)
        markers.append((lb.x0, lb.y0, lb.x1, lb.y1))
    rings = [(r, a) for r in (6, 11, 17, 24, 32, 42, 54, 68, 84)
             for a in (45, 0, 315, 90, 270, 135, 180, 225, 22, 338, 158, 202, 68, 112, 248, 292)]
    placed: list[tuple[float, float, float, float]] = []
    artists: list[Any] = []
    leaders: list[tuple[tuple[float, float], tuple[float, float]]] = []
    leader = {"arrowstyle": "-", "color": "0.45", "lw": 0.5, "shrinkA": 0, "shrinkB": 3}

    # 1) Crowded groups (another group within CROWD_PX) get a callout column to
    #    the right of the crowd, in the same vertical order as their points, so
    #    leader lines never cross. Overlaps are resolved by pushing labels apart.
    pos = [tuple(ax.transData.transform((g[0]["x"][0], g[0]["y"][0]))) for g in groups]
    crowd_px = 12 * px
    near = {i: [j for j, b in enumerate(pos) if j != i and math.dist(a, b) < crowd_px] for i, a in enumerate(pos)}
    components: list[list[int]] = []
    seen: set[int] = set()
    for i in range(len(pos)):  # connected components of the "near" graph
        if i in seen or not near[i]:
            continue
        comp, stack = [], [i]
        while stack:
            k = stack.pop()
            if k in seen:
                continue
            seen.add(k)
            comp.append(k)
            stack.extend(near[k])
        components.append(sorted(comp))
    remaining = list(range(len(groups)))
    for crowded in components:
        if len(crowded) < 3:
            continue
        _column(ax, renderer, axbox, groups, pos, crowded, fontsize, px, leader, markers, placed, artists, leaders)
    done = {id(a._pgb_group) for a in artists}
    remaining = [i for i in remaining if id(groups[i]) not in done]
    # 2) Everything else: greedy over rings of candidate offsets.
    for g in (groups[i] for i in remaining):
        x, y = g[0]["x"][0], g[0]["y"][0]
        label = merge_label([p["name"] for p in g])
        best = None
        for r, ang in rings:
            dx, dy = r * math.cos(math.radians(ang)), r * math.sin(math.radians(ang))
            ha = "left" if dx > 1 else ("right" if dx < -1 else "center")
            va = "bottom" if dy > 1 else ("top" if dy < -1 else "center")
            t = ax.annotate(label, (x, y), xytext=(dx, dy), textcoords="offset points",
                            fontsize=fontsize, ha=ha, va=va, zorder=5)
            bb = _text_extent(t, renderer)
            box = (bb.x0, bb.y0, bb.x1, bb.y1)
            inside = box[0] >= axbox.x0 + 1 and box[2] <= axbox.x1 - 1 and box[1] >= axbox.y0 + 1 and box[3] <= axbox.y1 - 1
            own = tuple(ax.transData.transform((x, y)))
            others = [q for q in pos if math.dist(q, own) > 0.5]
            if inside and _ok(box, own, placed, markers, leaders, others, r > 11):
                best = (t, box, r)
                break
            t.remove()
        if best is None:  # fall back to a grid over the whole axes, nearest first
            mx, my = ax.transData.transform((x, y))
            grid = [(gx, gy) for gx in range(int(axbox.x0) + 4, int(axbox.x1) - 4, int(6 * px))
                    for gy in range(int(axbox.y0) + 4, int(axbox.y1) - 4, int(4 * px))]
            grid.sort(key=lambda q: (math.dist(q, (mx, my)), q))
            for gx, gy in grid:
                ha = "left" if gx >= mx else "right"
                t = ax.annotate(label, (x, y), xytext=ax.transData.inverted().transform((gx, gy)),
                                textcoords="data", fontsize=fontsize, ha=ha, va="center", zorder=5)
                bb = _text_extent(t, renderer)
                box = (bb.x0, bb.y0, bb.x1, bb.y1)
                inside = box[0] >= axbox.x0 + 1 and box[2] <= axbox.x1 - 1 and box[1] >= axbox.y0 + 1 and box[3] <= axbox.y1 - 1
                if inside and _ok(box, (mx, my), placed, markers, leaders, [], True):
                    best = (t, box, 99)
                    break
                t.remove()
        if best is None:
            raise RuntimeError(f"could not place label {label!r}")
        t, box, r = best
        if r > 11:
            t.arrow_patch = None
            t.remove()
            t = ax.annotate(label, (x, y), xytext=t.xyann, textcoords=t.anncoords,
                            fontsize=fontsize, ha=t.get_ha(), va=t.get_va(), zorder=5,
                            arrowprops=leader)
            own = tuple(ax.transData.transform((x, y)))
            leaders.append((own, _anchor(box, own)))
        placed.append(box)
        artists.append(t)
    return artists


def text_boxes(fig, artists) -> list[tuple[float, float, float, float]]:
    """Rendered bounding boxes (display px) of label texts, measured with the renderer."""
    fig.canvas.draw()
    r = fig.canvas.get_renderer()
    return [tuple(_text_extent(a, r).extents) for a in artists]


def _text_extent(t, renderer):
    """Bounding box of the label text only (Annotation's own extent includes the leader line)."""
    from matplotlib.text import Annotation, Text

    if isinstance(t, Annotation):
        t.update_positions(renderer)  # place text for its textcoords before measuring
    return Text.get_window_extent(t, renderer)


def _limits(vals: list[tuple[float, float, float]], zoom: bool = False) -> tuple[float, float]:
    """Axis range from 0 to the largest upper CI bound (or, with ``zoom``, the
    largest point), rounded up to 0.1 (max 1)."""
    hi = max(((v if zoom else v + e) for v, _, e in vals), default=1.0)
    top = min(1.0, (int(hi * 10 + 0.999) / 10) + 0.1)
    pad = 0.04 * top
    return -pad, top + pad


def make_figures(results_dir: Path, out_dir: Path) -> list[Path]:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    matplotlib.rcParams["svg.hashsalt"] = "pgb"
    matplotlib.rcParams["pdf.fonttype"] = 42
    series = build_series(results_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    written = []
    for key, (stem, spec, xl, yl, clabel) in FIGS.items():
        fs = spec["font"]
        with matplotlib.rc_context({"font.size": fs, "axes.labelsize": fs, "xtick.labelsize": fs - 1,
                                    "ytick.labelsize": fs - 1, "legend.fontsize": fs - 1}):
            pts = series[key]
            fig, ax = plt.subplots(figsize=spec["size"], dpi=300)
            for p in pts:
                ax.errorbar(p["x"][0], p["y"][0], xerr=[[p["x"][1]], [p["x"][2]]],
                            yerr=[[p["y"][1]], [p["y"][2]]], fmt="none", ecolor="0.7", lw=0.6,
                            capsize=0, zorder=1)
            zoom = spec.get("zoom", False)
            ax.set_xlim(*_limits([p["x"] for p in pts], zoom))
            ax.set_ylim(*_limits([p["y"] for p in pts], zoom))
            if zoom:  # mark error bars that run past the zoomed axes
                x0, x1 = ax.get_xlim()
                y0, y1 = ax.get_ylim()
                for p in pts:
                    for v, lo, hi, axis in ((p["x"][0], p["x"][1], p["x"][2], "x"),
                                            (p["y"][0], p["y"][1], p["y"][2], "y")):
                        top = x1 if axis == "x" else y1
                        if v + hi > top:
                            at = (top, p["y"][0]) if axis == "x" else (p["x"][0], top)
                            ax.plot(*at, marker=">" if axis == "x" else "^", ms=2.5, color="0.55",
                                    clip_on=False, zorder=1)
            ax.set_xlabel(xl)
            ax.set_ylabel(yl)
            ax.grid(True, alpha=0.25, lw=0.4)
            if pts:
                xs = [p["x"][0] for p in pts]
                ys = [p["y"][0] for p in pts]
                front = pareto_front(list(zip(xs, ys)))
                if len(front) >= 2:
                    ax.step([f[0] for f in front], [f[1] for f in front], where="post", color="tab:red",
                            lw=0.9, label="Pareto front", zorder=2)
                else:  # single-point front: no line to draw, so ring the point instead
                    ax.scatter([f[0] for f in front], [f[1] for f in front], s=60, facecolors="none",
                               edgecolors="tab:red", linewidths=1.0, zorder=4,
                               label="Pareto front (single point)")
                segs = []
                for (x0, y0), (x1, y1) in zip(front, front[1:]):
                    segs += [((x0, y0), (x1, y0)), ((x1, y0), (x1, y1))]
                if clabel:
                    sc = ax.scatter(xs, ys, c=[p["block"] for p in pts], cmap="viridis", vmin=0, vmax=1,
                                    s=16, zorder=3, edgecolors="k", linewidths=0.4)
                    cb = fig.colorbar(sc, ax=ax, pad=0.02, fraction=0.05)
                    cb.set_label(clabel)
                    cb.ax.tick_params(labelsize=fs - 1)
                else:
                    ax.scatter(xs, ys, s=16, zorder=3, color="tab:blue", edgecolors="k", linewidths=0.4)
                # A single-point front sits lower-left, where its legend entry would cover data.
                ax.legend(loc="lower right" if len(front) >= 2 else "upper left", frameon=False, handlelength=1.2, borderaxespad=0.2)
            fig.tight_layout(pad=0.3)
            fig.canvas.draw()
            labels = _place_labels(fig, ax, _groups(pts), fs - 1, segs) if pts else []
            LABEL_BOXES[key] = text_boxes(fig, labels)
            for ext, kw in (("png", {"metadata": {"Software": None}}),
                            ("pdf", {"metadata": {"CreationDate": None, "Producer": None, "Creator": None}})):
                path = out_dir / f"{stem}.{ext}"
                fig.savefig(path, dpi=300, **kw)
                written.append(path)
            plt.close(fig)
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
