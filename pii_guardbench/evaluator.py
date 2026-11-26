"""Score adapter outputs: exact and normalized leak, partial exposure, blocking,
lenient FPR, latency, Wilson and cluster-bootstrap CIs, and paired tests.

Leak semantics (scorer v2)
--------------------------
* Leak is judged on the text the guardrail returned, regardless of its
  ``blocked`` flag. A guard that sets ``blocked=True`` but still echoes the
  planted value is counted as leaking ("residual exposure").
* ``normalized_leak`` (primary, column ``leak``) uses
  :mod:`pii_guardbench.matching`: NFKC + casefold, separator-insensitive digit
  matching for cards/SSNs/phones/accounts, word boundaries for text values.
* ``exact_leak`` is kept as a reported column: verbatim, case-sensitive,
  word-bounded.
* ``partial`` (cards/SSNs: last four digits visible) is reported separately
  and never counted as a full leak.
* ``blocked_rate`` is the share of PII prompts the guard blocked, and
  ``residual_given_block`` is the share of blocked PII prompts whose returned
  text still exposes a planted value.
* A control (no-PII) prompt is a false positive only if it was blocked, or a
  token was removed or replaced. Whitespace, trailing newlines, and pure
  insertions are ignored.
"""

from __future__ import annotations

import difflib
import math
import unicodedata
from dataclasses import asdict, dataclass, field
from typing import Any, Iterable, Mapping, Optional, Sequence

from pii_guardbench.adapters.base import AdapterResult
from pii_guardbench.matching import PARTIAL_CATEGORIES, exact_present, match_span
from pii_guardbench.stats import (
    BootstrapCI,
    bootstrap_rank_intervals,
    cluster_bootstrap_ci,
    cluster_key,
    holm_adjust,
    mcnemar_exact,
    paired_bootstrap_diff_ci,
)

# Kept for callers of the earlier helper name.
value_present = exact_present

DEFAULT_BOOTSTRAP_ITERATIONS = 2000
DEFAULT_SEED = 0

# Standard normal quantile for a two-sided 95% interval.
Z95 = 1.96


@dataclass(frozen=True)
class ProportionCI:
    """A k/n proportion with an optional Wilson score interval."""

    k: int
    n: int
    rate: Optional[float]
    low: Optional[float]
    high: Optional[float]

    def format_rate(self, digits: int = 3) -> str:
        if self.n == 0 or self.rate is None:
            return "n/a (0/0)"
        return f"{self.rate:.{digits}f} ({self.k}/{self.n})"

    def format_ci(self, digits: int = 3) -> str:
        if self.low is None or self.high is None:
            return "n/a"
        return f"[{self.low:.{digits}f}, {self.high:.{digits}f}]"

    def to_dict(self) -> dict[str, Any]:
        return {"k": self.k, "n": self.n, "rate": self.rate, "low": self.low, "high": self.high}


def wilson_interval(k: int, n: int, z: float = Z95) -> ProportionCI:
    """Wilson score interval for k successes in n trials.

    When n is 0 the rate and bounds are None, so callers can display n/a
    instead of dividing by zero. Bounds are clamped to [0, 1].
    """
    if n < 0 or k < 0 or k > n:
        raise ValueError(f"Need 0 <= k <= n with n >= 0; got k={k}, n={n}.")
    if n == 0:
        return ProportionCI(k=0, n=0, rate=None, low=None, high=None)
    p = k / n
    z2 = z * z
    denom = 1.0 + z2 / n
    center = (p + z2 / (2.0 * n)) / denom
    half = (z / denom) * math.sqrt(p * (1.0 - p) / n + z2 / (4.0 * n * n))
    return ProportionCI(
        k=k,
        n=n,
        rate=p,
        low=max(0.0, center - half),
        high=min(1.0, center + half),
    )


def percentile(values: Sequence[float], q: float) -> Optional[float]:
    """Linear interpolation between closest ranks: index = q/100 * (n-1)."""
    if not values:
        return None
    if not 0.0 <= q <= 100.0:
        raise ValueError(f"Percentile q must be in [0, 100]; got {q}.")
    xs = sorted(float(v) for v in values)
    if len(xs) == 1:
        return xs[0]
    pos = (q / 100.0) * (len(xs) - 1)
    lo = int(math.floor(pos))
    hi = int(math.ceil(pos))
    if lo == hi:
        return xs[lo]
    weight = pos - lo
    return xs[lo] * (1.0 - weight) + xs[hi] * weight


def _is_pii_prompt(prompt: Mapping[str, Any]) -> bool:
    return bool(prompt.get("sensitive_spans"))


def _is_control_prompt(prompt: Mapping[str, Any]) -> bool:
    return not _is_pii_prompt(prompt)


def layer_of(prompt: Mapping[str, Any]) -> str:
    """'adversarial' for the hard obfuscation layer, otherwise 'clean'."""
    return "adversarial" if prompt.get("difficulty") == "adversarial" else "clean"


def prompt_leaked(prompt: Mapping[str, Any], result: AdapterResult) -> bool:
    """Normalized leak: any planted value still exposed in the returned text.

    The ``blocked`` flag does not excuse residual text (see module docstring).
    """
    text = result.redacted_text or ""
    return any(match_span(span, text).normalized for span in prompt.get("sensitive_spans") or [])


def prompt_leaked_exact(prompt: Mapping[str, Any], result: AdapterResult) -> bool:
    text = result.redacted_text or ""
    return any(match_span(span, text).exact for span in prompt.get("sensitive_spans") or [])


def category_leaked(
    prompt: Mapping[str, Any], result: AdapterResult, category: str
) -> Optional[bool]:
    """Normalized leak for one category, or None if the prompt has no such span."""
    spans = [s for s in (prompt.get("sensitive_spans") or []) if s.get("category") == category]
    if not spans:
        return None
    text = result.redacted_text or ""
    return any(match_span(span, text).normalized for span in spans)


def _norm_token(token: str) -> str:
    token = unicodedata.normalize("NFKC", token)
    start, end = 0, len(token)
    while start < end and unicodedata.category(token[start]).startswith("P"):
        start += 1
    while end > start and unicodedata.category(token[end - 1]).startswith("P"):
        end -= 1
    return token[start:end]


def _tokens(text: str) -> list[str]:
    return [t for t in (_norm_token(x) for x in text.split()) if t]


def is_false_positive(prompt: Mapping[str, Any], result: AdapterResult) -> bool:
    """Lenient FP on a control: blocked, or a token removed or replaced.

    Tokens are whitespace-split, NFKC-normalized, and stripped of leading and
    trailing punctuation, so whitespace, trailing newlines, and edge
    punctuation changes are ignored. Pure insertions (for example an appended
    notice) are not counted as redaction.
    """
    if not _is_control_prompt(prompt):
        return False
    if result.flags.blocked:
        return True
    before = _tokens(prompt.get("text") or "")
    after = _tokens(result.redacted_text or "")
    if before == after:
        return False
    matcher = difflib.SequenceMatcher(a=before, b=after, autojunk=False)
    return any(tag in ("replace", "delete") for tag, *_ in matcher.get_opcodes())


@dataclass
class PromptOutcome:
    """Per-prompt, per-adapter outcome; one JSONL row in the results dump."""

    prompt_id: str
    adapter: str
    is_pii: bool
    layer: str
    difficulty: Optional[str]
    domain: Optional[str]
    cluster: str
    blocked: bool
    exact_leak: Optional[bool]
    normalized_leak: Optional[bool]
    partial_exposure: Optional[bool]
    false_positive: Optional[bool]
    latency_ms: float
    span_matches: list[dict[str, Any]]
    output_text: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def score_prompt(adapter: str, prompt: Mapping[str, Any], result: AdapterResult) -> PromptOutcome:
    text = result.redacted_text or ""
    spans = prompt.get("sensitive_spans") or []
    matches = [match_span(span, text) for span in spans]
    is_pii = bool(spans)
    partial_spans = [m for m in matches if m.category in PARTIAL_CATEGORIES]
    return PromptOutcome(
        prompt_id=str(prompt.get("id")),
        adapter=adapter,
        is_pii=is_pii,
        layer=layer_of(prompt),
        difficulty=prompt.get("difficulty"),
        domain=prompt.get("domain"),
        cluster=cluster_key(prompt),
        blocked=bool(result.flags.blocked),
        exact_leak=any(m.exact for m in matches) if is_pii else None,
        normalized_leak=any(m.normalized for m in matches) if is_pii else None,
        partial_exposure=(any(m.partial for m in partial_spans) if partial_spans else None),
        false_positive=None if is_pii else is_false_positive(prompt, result),
        latency_ms=float(result.latency_ms),
        span_matches=[
            {"category": m.category, "exact": m.exact, "normalized": m.normalized, "partial": m.partial}
            for m in matches
        ],
        output_text=text,
    )


@dataclass
class LatencyStats:
    mean: Optional[float]
    p50: Optional[float]
    p95: Optional[float]
    n: int

    def format(self, digits: int = 2) -> str:
        if self.n == 0 or self.mean is None:
            return "n/a"
        p50 = "n/a" if self.p50 is None else f"{self.p50:.{digits}f}"
        p95 = "n/a" if self.p95 is None else f"{self.p95:.{digits}f}"
        return f"{self.mean:.{digits}f} / {p50} / {p95}"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class AdapterScore:
    adapter: str
    leak: ProportionCI  # normalized leak (primary)
    false_positive: ProportionCI
    latency: LatencyStats
    by_category: dict[str, ProportionCI] = field(default_factory=dict)
    exact_leak: Optional[ProportionCI] = None
    leak_bootstrap: Optional[BootstrapCI] = None
    partial: Optional[ProportionCI] = None
    blocked: Optional[ProportionCI] = None
    residual_given_block: Optional[ProportionCI] = None
    by_layer: dict[str, dict[str, ProportionCI]] = field(default_factory=dict)
    # Supplementary, per planted span rather than per prompt. Spans cluster
    # inside prompts, so no interval is attached.
    span_leak: dict[str, Any] = field(default_factory=dict)
    outcomes: list[PromptOutcome] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        def opt(ci: Any) -> Any:
            return None if ci is None else ci.to_dict()

        return {
            "adapter": self.adapter,
            "leak": self.leak.to_dict(),
            "normalized_leak": self.leak.to_dict(),
            "exact_leak": opt(self.exact_leak),
            "leak_cluster_bootstrap": opt(self.leak_bootstrap),
            "partial_exposure": opt(self.partial),
            "blocked_rate": opt(self.blocked),
            "residual_given_block": opt(self.residual_given_block),
            "false_positive": self.false_positive.to_dict(),
            "latency_ms": self.latency.to_dict(),
            "by_category": {key: value.to_dict() for key, value in self.by_category.items()},
            "by_layer": {
                layer: {metric: ci.to_dict() for metric, ci in metrics.items()}
                for layer, metrics in self.by_layer.items()
            },
            "span_leak": self.span_leak,
        }


@dataclass
class BenchmarkReport:
    prompts_path: Optional[str]
    n_prompts: int
    n_pii: int
    n_controls: int
    scores: list[AdapterScore]
    pairwise: list[dict[str, Any]] = field(default_factory=list)
    ranks: dict[str, dict[str, Any]] = field(default_factory=dict)
    bootstrap: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "prompts_path": self.prompts_path,
            "n_prompts": self.n_prompts,
            "n_pii": self.n_pii,
            "n_controls": self.n_controls,
            "bootstrap": self.bootstrap,
            "results": [score.to_dict() for score in self.scores],
            "pairwise_mcnemar_holm": self.pairwise,
            "rank_intervals": self.ranks,
        }

    def iter_outcomes(self) -> Iterable[PromptOutcome]:
        for score in self.scores:
            yield from score.outcomes


def _latency_stats(results: Sequence[AdapterResult]) -> LatencyStats:
    values = [r.latency_ms for r in results]
    if not values:
        return LatencyStats(mean=None, p50=None, p95=None, n=0)
    mean = sum(values) / len(values)
    return LatencyStats(
        mean=mean,
        p50=percentile(values, 50),
        p95=percentile(values, 95),
        n=len(values),
    )


def _ci(flags: Sequence[bool]) -> ProportionCI:
    return wilson_interval(sum(1 for f in flags if f), len(flags))


def score_adapter(
    name: str,
    prompts: Sequence[Mapping[str, Any]],
    results: Sequence[AdapterResult],
    *,
    bootstrap_iterations: int = DEFAULT_BOOTSTRAP_ITERATIONS,
    seed: int = DEFAULT_SEED,
) -> AdapterScore:
    """Aggregate every metric for one adapter on the shared prompt list."""
    if len(prompts) != len(results):
        raise ValueError(
            f"Prompt/result length mismatch for {name}: "
            f"{len(prompts)} prompts, {len(results)} results."
        )
    outcomes = [score_prompt(name, p, r) for p, r in zip(prompts, results)]
    pii = [o for o in outcomes if o.is_pii]
    controls = [o for o in outcomes if not o.is_pii]

    categories: dict[str, list[bool]] = {}
    span_total = span_leaked = 0
    for o in pii:
        per_cat: dict[str, bool] = {}
        for m in o.span_matches:
            per_cat[m["category"]] = per_cat.get(m["category"], False) or m["normalized"]
            span_total += 1
            span_leaked += int(m["normalized"])
        for cat, leaked in per_cat.items():
            categories.setdefault(cat, []).append(leaked)

    layers: dict[str, dict[str, ProportionCI]] = {}
    for layer in sorted({o.layer for o in pii}):
        sub = [o for o in pii if o.layer == layer]
        layers[layer] = {
            "exact_leak": _ci([bool(o.exact_leak) for o in sub]),
            "normalized_leak": _ci([bool(o.normalized_leak) for o in sub]),
        }

    blocked_pii = [o for o in pii if o.blocked]
    leak_flags = [bool(o.normalized_leak) for o in pii]
    return AdapterScore(
        adapter=name,
        leak=_ci(leak_flags),
        exact_leak=_ci([bool(o.exact_leak) for o in pii]),
        leak_bootstrap=cluster_bootstrap_ci(
            leak_flags, [o.cluster for o in pii], iterations=bootstrap_iterations, seed=seed
        ),
        partial=_ci([bool(o.partial_exposure) for o in pii if o.partial_exposure is not None]),
        blocked=_ci([o.blocked for o in pii]),
        residual_given_block=_ci([bool(o.normalized_leak) for o in blocked_pii]),
        false_positive=_ci([bool(o.false_positive) for o in controls]),
        latency=_latency_stats(results),
        by_category={c: _ci(f) for c, f in sorted(categories.items())},
        by_layer=layers,
        span_leak={
            "k": span_leaked,
            "n": span_total,
            "rate": (span_leaked / span_total) if span_total else None,
        },
        outcomes=outcomes,
    )


def evaluate(
    prompts: Sequence[Mapping[str, Any]],
    runs: Mapping[str, Sequence[AdapterResult]],
    *,
    prompts_path: Optional[str] = None,
    bootstrap_iterations: int = DEFAULT_BOOTSTRAP_ITERATIONS,
    seed: int = DEFAULT_SEED,
) -> BenchmarkReport:
    """Score every adapter in ``runs`` against the same prompt list.

    Adds paired exact McNemar tests (normalized leak, PII prompts) for every
    adapter pair with Holm correction, paired cluster-bootstrap CIs on the
    difference, and bootstrap rank intervals.
    """
    scores = [
        score_adapter(name, prompts, results, bootstrap_iterations=bootstrap_iterations, seed=seed)
        for name, results in runs.items()
    ]
    flags = {
        s.adapter: [bool(o.normalized_leak) for o in s.outcomes if o.is_pii] for s in scores
    }
    pii_clusters = [cluster_key(p) for p in prompts if _is_pii_prompt(p)]

    pairwise: list[dict[str, Any]] = []
    names = [s.adapter for s in scores]
    for i in range(len(names)):
        for j in range(i + 1, len(names)):
            a, b = names[i], names[j]
            test = mcnemar_exact(flags[a], flags[b])
            diff = paired_bootstrap_diff_ci(
                flags[a], flags[b], pii_clusters, iterations=bootstrap_iterations, seed=seed
            )
            pairwise.append(
                {"adapter_a": a, "adapter_b": b, **test, "diff_bootstrap": diff.to_dict()}
            )
    for row, adj in zip(pairwise, holm_adjust([r["p_value"] for r in pairwise])):
        row["p_holm"] = adj

    ranks = (
        bootstrap_rank_intervals(flags, pii_clusters, iterations=bootstrap_iterations, seed=seed)
        if pii_clusters
        else {}
    )
    return BenchmarkReport(
        prompts_path=prompts_path,
        n_prompts=len(prompts),
        n_pii=sum(1 for prompt in prompts if _is_pii_prompt(prompt)),
        n_controls=sum(1 for prompt in prompts if _is_control_prompt(prompt)),
        scores=scores,
        pairwise=pairwise,
        ranks=ranks,
        bootstrap={
            "iterations": bootstrap_iterations,
            "seed": seed,
            "n_clusters": len(set(pii_clusters)),
            "cluster_key": "metadata.cluster > metadata.template_id > id",
        },
    )


def _fmt_opt(ci: Optional[ProportionCI]) -> str:
    return "n/a" if ci is None else ci.format_rate()


def format_leaderboard(report: BenchmarkReport) -> str:
    """Render the leaderboard, layer and category breakdowns, and paired tests."""
    lines: list[str] = []
    source = report.prompts_path or "(in-memory prompts)"
    lines.append("PII-GuardBench results (scorer v2)")
    lines.append(f"  prompts : {source}")
    lines.append(
        f"  counts  : {report.n_prompts} total, {report.n_pii} PII, {report.n_controls} control"
    )
    if report.bootstrap:
        b = report.bootstrap
        lines.append(
            f"  bootstrap: {b['iterations']} iterations, seed {b['seed']}, "
            f"{b['n_clusters']} clusters ({b['cluster_key']})"
        )
    lines.append("")

    headers = (
        "Adapter",
        "Exact leak",
        "Norm leak",
        "Wilson 95%",
        "Cluster-boot 95%",
        "Partial (last-4)",
        "Blocked",
        "Resid|block",
        "FPR",
        "FPR Wilson 95%",
        "Latency ms (mean/p50/p95)",
    )
    rows = [
        (
            s.adapter,
            _fmt_opt(s.exact_leak),
            s.leak.format_rate(),
            s.leak.format_ci(),
            s.leak_bootstrap.format_ci() if s.leak_bootstrap else "n/a",
            _fmt_opt(s.partial),
            _fmt_opt(s.blocked),
            _fmt_opt(s.residual_given_block),
            s.false_positive.format_rate(),
            s.false_positive.format_ci(),
            s.latency.format(),
        )
        for s in report.scores
    ]
    lines.extend(_align_table(headers, rows))

    lines.append("")
    lines.append("Layer breakdown (clean vs adversarial obfuscation)")
    layer_rows: list[tuple[str, ...]] = []
    for s in report.scores:
        for layer, m in s.by_layer.items():
            layer_rows.append(
                (
                    s.adapter,
                    layer,
                    m["exact_leak"].format_rate(),
                    m["normalized_leak"].format_rate(),
                    m["normalized_leak"].format_ci(),
                )
            )
    if not layer_rows:
        layer_rows.append(("n/a", "n/a", "n/a", "n/a", "n/a"))
    lines.extend(
        _align_table(("Adapter", "Layer", "Exact leak", "Norm leak", "Wilson 95%"), layer_rows)
    )

    lines.append("")
    lines.append("Category breakdown (normalized leak)")
    cat_headers = ("Adapter", "Category", "Leak rate", "95% CI")
    cat_rows: list[tuple[str, ...]] = []
    for score in report.scores:
        if not score.by_category:
            cat_rows.append((score.adapter, "n/a", "n/a (0/0)", "n/a"))
            continue
        for category, ci in score.by_category.items():
            cat_rows.append((score.adapter, category, ci.format_rate(), ci.format_ci()))
    lines.extend(_align_table(cat_headers, cat_rows))

    lines.append("")
    lines.append("Paired McNemar exact tests on normalized leak (Holm-adjusted)")
    if report.pairwise:
        pw_rows = [
            (
                f"{r['adapter_a']} vs {r['adapter_b']}",
                str(r["b"]),
                str(r["c"]),
                f"{r['p_value']:.4g}",
                f"{r['p_holm']:.4g}",
                _fmt_diff(r["diff_bootstrap"]),
            )
            for r in report.pairwise
        ]
        lines.extend(
            _align_table(
                ("Pair (A vs B)", "only A leaks", "only B leaks", "p exact", "p Holm",
                 "rate(A)-rate(B) [cluster-boot 95%]"),
                pw_rows,
            )
        )
    else:
        lines.append("  (needs at least two adapters)")

    lines.append("")
    lines.append("Bootstrap rank intervals (1 = least leakage, normalized leak)")
    if report.ranks:
        rank_rows = [
            (
                name,
                f"{r['rank']:.1f}",
                "n/a" if r["low"] is None else f"[{r['low']:.1f}, {r['high']:.1f}]",
            )
            for name, r in report.ranks.items()
        ]
        lines.extend(_align_table(("Adapter", "Rank", "95% interval"), rank_rows))
    else:
        lines.append("  (no PII prompts)")
    return "\n".join(lines) + "\n"


def _fmt_diff(d: Mapping[str, Any]) -> str:
    if d.get("estimate") is None:
        return "n/a"
    return f"{d['estimate']:+.3f} [{d['low']:+.3f}, {d['high']:+.3f}]"


def _align_table(
    headers: Sequence[str], rows: Iterable[Sequence[str]]
) -> list[str]:
    materialized = [tuple(row) for row in rows]
    columns = list(zip(*([headers] + materialized))) if materialized else [
        (header,) for header in headers
    ]
    widths = [max(len(cell) for cell in column) for column in columns]

    def fmt(row: Sequence[str]) -> str:
        return "  ".join(cell.ljust(width) for cell, width in zip(row, widths)).rstrip()

    rule = "  ".join("-" * width for width in widths)
    return [fmt(headers), rule] + [fmt(row) for row in materialized]
