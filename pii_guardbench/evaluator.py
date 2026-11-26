"""Score adapter outputs: exact-match leak rate, Wilson CIs, FPR, and latency."""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from typing import Any, Iterable, Mapping, Optional, Sequence

from pii_guardbench.adapters.base import AdapterResult

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


def value_present(value: str, text: str) -> bool:
    """True if ``value`` occurs in ``text`` as an exact string, not inside a longer token.

    The match is case-sensitive and exact. A hit is ignored only when the value
    begins or ends with a word character and the neighbouring character in
    ``text`` is also a word character (for example the name ``Ann`` inside
    ``Annual``). Values such as ``078-05-1120`` or ``sk_test_x`` behave exactly
    like a plain substring test whenever they stand alone.
    """
    if not value:
        return False
    start = text.find(value)
    while start != -1:
        end = start + len(value)
        before_ok = not (value[0].isalnum() and start > 0 and text[start - 1].isalnum())
        after_ok = not (value[-1].isalnum() and end < len(text) and text[end].isalnum())
        if before_ok and after_ok:
            return True
        start = text.find(value, start + 1)
    return False


def _is_pii_prompt(prompt: Mapping[str, Any]) -> bool:
    return bool(prompt.get("sensitive_spans"))


def _is_control_prompt(prompt: Mapping[str, Any]) -> bool:
    return not _is_pii_prompt(prompt)


def prompt_leaked(prompt: Mapping[str, Any], result: AdapterResult) -> bool:
    """A leak is any planted value still present, unless the guard blocked."""
    spans = prompt.get("sensitive_spans") or []
    if not spans:
        return False
    if result.flags.blocked:
        return False
    text = result.redacted_text or ""
    return any(value_present(span["value"], text) for span in spans)


def category_leaked(
    prompt: Mapping[str, Any], result: AdapterResult, category: str
) -> Optional[bool]:
    """Return leak status for one category, or None if the prompt has no such span."""
    spans = [s for s in (prompt.get("sensitive_spans") or []) if s.get("category") == category]
    if not spans:
        return None
    if result.flags.blocked:
        return False
    text = result.redacted_text or ""
    return any(value_present(span["value"], text) for span in spans)


def is_false_positive(prompt: Mapping[str, Any], result: AdapterResult) -> bool:
    """A control is a false positive if the guard blocked it or changed the text."""
    if not _is_control_prompt(prompt):
        return False
    if result.flags.blocked:
        return True
    return (result.redacted_text or "") != (prompt.get("text") or "")


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
    leak: ProportionCI
    false_positive: ProportionCI
    latency: LatencyStats
    by_category: dict[str, ProportionCI] = field(default_factory=dict)
    # Supplementary, per planted span instead of per prompt. Spans cluster inside
    # prompts, so no confidence interval is attached.
    span_leak: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "adapter": self.adapter,
            "leak": self.leak.to_dict(),
            "false_positive": self.false_positive.to_dict(),
            "latency_ms": self.latency.to_dict(),
            "by_category": {key: value.to_dict() for key, value in self.by_category.items()},
            "span_leak": self.span_leak,
        }


@dataclass
class BenchmarkReport:
    prompts_path: Optional[str]
    n_prompts: int
    n_pii: int
    n_controls: int
    scores: list[AdapterScore]

    def to_dict(self) -> dict[str, Any]:
        return {
            "prompts_path": self.prompts_path,
            "n_prompts": self.n_prompts,
            "n_pii": self.n_pii,
            "n_controls": self.n_controls,
            "results": [score.to_dict() for score in self.scores],
        }


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


def score_adapter(
    name: str,
    prompts: Sequence[Mapping[str, Any]],
    results: Sequence[AdapterResult],
) -> AdapterScore:
    """Aggregate leak rate, FPR, category breakdown, and latency for one adapter."""
    if len(prompts) != len(results):
        raise ValueError(
            f"Prompt/result length mismatch for {name}: "
            f"{len(prompts)} prompts, {len(results)} results."
        )

    pii_flags = [
        prompt_leaked(prompt, result)
        for prompt, result in zip(prompts, results)
        if _is_pii_prompt(prompt)
    ]
    fp_flags = [
        is_false_positive(prompt, result)
        for prompt, result in zip(prompts, results)
        if _is_control_prompt(prompt)
    ]

    categories: dict[str, list[bool]] = {}
    for prompt, result in zip(prompts, results):
        seen: set[str] = set()
        for span in prompt.get("sensitive_spans") or []:
            category = span["category"]
            if category in seen:
                continue
            seen.add(category)
            leaked = category_leaked(prompt, result, category)
            if leaked is None:
                continue
            categories.setdefault(category, []).append(leaked)

    by_category = {
        category: wilson_interval(sum(flags), len(flags))
        for category, flags in sorted(categories.items())
    }
    span_total = 0
    span_leaked = 0
    for prompt, result in zip(prompts, results):
        for span in prompt.get("sensitive_spans") or []:
            span_total += 1
            if not result.flags.blocked and value_present(span["value"], result.redacted_text or ""):
                span_leaked += 1
    span_leak = {
        "k": span_leaked,
        "n": span_total,
        "rate": (span_leaked / span_total) if span_total else None,
    }
    return AdapterScore(
        adapter=name,
        span_leak=span_leak,
        leak=wilson_interval(sum(pii_flags), len(pii_flags)),
        false_positive=wilson_interval(sum(fp_flags), len(fp_flags)),
        latency=_latency_stats(results),
        by_category=by_category,
    )


def evaluate(
    prompts: Sequence[Mapping[str, Any]],
    runs: Mapping[str, Sequence[AdapterResult]],
    *,
    prompts_path: Optional[str] = None,
) -> BenchmarkReport:
    """Score every adapter in `runs` against the same prompt list."""
    scores = [score_adapter(name, prompts, results) for name, results in runs.items()]
    return BenchmarkReport(
        prompts_path=prompts_path,
        n_prompts=len(prompts),
        n_pii=sum(1 for prompt in prompts if _is_pii_prompt(prompt)),
        n_controls=sum(1 for prompt in prompts if _is_control_prompt(prompt)),
        scores=scores,
    )


def format_leaderboard(report: BenchmarkReport) -> str:
    """Render a plain-text leaderboard plus a per-category breakdown."""
    lines: list[str] = []
    source = report.prompts_path or "(in-memory prompts)"
    lines.append("PII-GuardBench results")
    lines.append(f"  prompts : {source}")
    lines.append(
        f"  counts  : {report.n_prompts} total, {report.n_pii} PII, {report.n_controls} control"
    )
    lines.append("")

    headers = (
        "Adapter",
        "Leak rate",
        "95% CI",
        "FPR",
        "FPR 95% CI",
        "Latency ms (mean/p50/p95)",
    )
    rows = [
        (
            score.adapter,
            score.leak.format_rate(),
            score.leak.format_ci(),
            score.false_positive.format_rate(),
            score.false_positive.format_ci(),
            score.latency.format(),
        )
        for score in report.scores
    ]
    lines.extend(_align_table(headers, rows))

    lines.append("")
    lines.append("Category breakdown")
    cat_headers = ("Adapter", "Category", "Leak rate", "95% CI")
    cat_rows: list[tuple[str, ...]] = []
    for score in report.scores:
        if not score.by_category:
            cat_rows.append((score.adapter, "n/a", "n/a (0/0)", "n/a"))
            continue
        for category, ci in score.by_category.items():
            cat_rows.append(
                (score.adapter, category, ci.format_rate(), ci.format_ci())
            )
    lines.extend(_align_table(cat_headers, cat_rows))
    return "\n".join(lines) + "\n"


def _align_table(
    headers: Sequence[str], rows: Iterable[Sequence[str]]
) -> list[str]:
    materialized = [tuple(row) for row in rows]
    columns = list(zip(*([headers] + materialized))) if materialized else [
        (header,) for header in headers
    ]
    widths = [max(len(cell) for cell in column) for column in columns]

    def fmt(row: Sequence[str]) -> str:
        return "  ".join(cell.ljust(width) for cell, width in zip(row, widths))

    rule = "  ".join("-" * width for width in widths)
    return [fmt(headers), rule] + [fmt(row) for row in materialized]
