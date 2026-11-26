"""Statistics beyond Wilson: cluster bootstrap, paired McNemar + Holm, rank intervals.

All resampling is seeded and uses only the standard library, so results are
reproducible across machines for the same seed, iteration count, and input.

Clusters
    Prompts that share a template (or an underlying value) are not
    independent. The bootstrap resamples whole clusters with replacement; the
    cluster key of a prompt is ``metadata.cluster``, else
    ``metadata.template_id``, else the prompt ``id`` (no clustering).

Paired design
    Every adapter runs on the same prompts, so one set of resampled clusters is
    shared across adapters in each replicate. That gives paired difference
    intervals and rank intervals that respect the correlation between systems.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass
from functools import lru_cache
from typing import Any, Mapping, Optional, Sequence


def cluster_key(prompt: Mapping[str, Any]) -> str:
    meta = prompt.get("metadata") or {}
    for key in ("cluster", "template_id"):
        value = meta.get(key)
        if value not in (None, ""):
            return str(value)
    return str(prompt.get("id"))


def _quantile(sorted_values: Sequence[float], q: float) -> float:
    """Linear-interpolated quantile, q in [0, 1]."""
    if not sorted_values:
        raise ValueError("quantile of empty sequence")
    pos = q * (len(sorted_values) - 1)
    lo = math.floor(pos)
    hi = math.ceil(pos)
    if lo == hi:
        return float(sorted_values[lo])
    w = pos - lo
    return float(sorted_values[lo]) * (1 - w) + float(sorted_values[hi]) * w


@dataclass(frozen=True)
class BootstrapCI:
    estimate: Optional[float]
    low: Optional[float]
    high: Optional[float]
    iterations: int
    n_clusters: int
    seed: int

    def format_ci(self, digits: int = 3) -> str:
        if self.low is None or self.high is None:
            return "n/a"
        return f"[{self.low:.{digits}f}, {self.high:.{digits}f}]"

    def to_dict(self) -> dict[str, Any]:
        return {
            "estimate": self.estimate,
            "low": self.low,
            "high": self.high,
            "iterations": self.iterations,
            "n_clusters": self.n_clusters,
            "seed": self.seed,
        }


def _group(clusters: Sequence[str]) -> list[list[int]]:
    order: dict[str, list[int]] = {}
    for index, key in enumerate(clusters):
        order.setdefault(key, []).append(index)
    return list(order.values())


def _replicate_indices(groups: list[list[int]], iterations: int, seed: int) -> tuple[tuple[int, ...], ...]:
    """Cluster indices chosen per replicate (shared across adapters)."""
    return _picks(len(groups), iterations, seed)


@lru_cache(maxsize=8)
def _picks(g: int, iterations: int, seed: int) -> tuple[tuple[int, ...], ...]:
    rng = random.Random(seed)
    return tuple(tuple(rng.choices(range(g), k=g)) for _ in range(iterations))


def _cluster_sums(groups: list[list[int]], flags: Sequence[bool]) -> list[tuple[int, int]]:
    return [(sum(1 for i in grp if flags[i]), len(grp)) for grp in groups]


def cluster_bootstrap_ci(
    flags: Sequence[bool],
    clusters: Sequence[str],
    *,
    iterations: int = 2000,
    seed: int = 0,
    alpha: float = 0.05,
) -> BootstrapCI:
    """Percentile cluster-bootstrap CI for a proportion (ratio estimator)."""
    if len(flags) != len(clusters):
        raise ValueError("flags and clusters must have the same length")
    if iterations < 1:
        raise ValueError("iterations must be >= 1")
    if not flags:
        return BootstrapCI(None, None, None, iterations, 0, seed)
    groups = _group(clusters)
    sums = _cluster_sums(groups, flags)
    estimate = sum(flags) / len(flags)
    stats = []
    for picks in _replicate_indices(groups, iterations, seed):
        k = sum(sums[i][0] for i in picks)
        n = sum(sums[i][1] for i in picks)
        stats.append(k / n)
    stats.sort()
    return BootstrapCI(
        estimate=estimate,
        low=_quantile(stats, alpha / 2),
        high=_quantile(stats, 1 - alpha / 2),
        iterations=iterations,
        n_clusters=len(groups),
        seed=seed,
    )


def paired_bootstrap_diff_ci(
    flags_a: Sequence[bool],
    flags_b: Sequence[bool],
    clusters: Sequence[str],
    *,
    iterations: int = 2000,
    seed: int = 0,
    alpha: float = 0.05,
) -> BootstrapCI:
    """Cluster-bootstrap CI for rate(a) - rate(b) on the same prompts."""
    if not (len(flags_a) == len(flags_b) == len(clusters)):
        raise ValueError("paired inputs must have equal length")
    if not flags_a:
        return BootstrapCI(None, None, None, iterations, 0, seed)
    groups = _group(clusters)
    sa = _cluster_sums(groups, flags_a)
    sb = _cluster_sums(groups, flags_b)
    estimate = (sum(flags_a) - sum(flags_b)) / len(flags_a)
    stats = []
    for picks in _replicate_indices(groups, iterations, seed):
        n = sum(sa[i][1] for i in picks)
        stats.append((sum(sa[i][0] for i in picks) - sum(sb[i][0] for i in picks)) / n)
    stats.sort()
    return BootstrapCI(
        estimate=estimate,
        low=_quantile(stats, alpha / 2),
        high=_quantile(stats, 1 - alpha / 2),
        iterations=iterations,
        n_clusters=len(groups),
        seed=seed,
    )


def _binom_cdf_half(k: int, n: int) -> float:
    """P(X <= k) for X ~ Binomial(n, 0.5), exact with integers."""
    return sum(math.comb(n, i) for i in range(0, k + 1)) / (2 ** n)


def mcnemar_exact(flags_a: Sequence[bool], flags_b: Sequence[bool]) -> dict[str, Any]:
    """Two-sided exact McNemar test on paired binary outcomes.

    ``b`` counts prompts where only A is positive, ``c`` where only B is.
    p = min(1, 2 * P(X <= min(b, c))) with X ~ Bin(b + c, 0.5); p = 1 when
    there are no discordant pairs.
    """
    if len(flags_a) != len(flags_b):
        raise ValueError("paired inputs must have equal length")
    b = sum(1 for x, y in zip(flags_a, flags_b) if x and not y)
    c = sum(1 for x, y in zip(flags_a, flags_b) if y and not x)
    n = b + c
    p = 1.0 if n == 0 else min(1.0, 2.0 * _binom_cdf_half(min(b, c), n))
    return {"b": b, "c": c, "discordant": n, "p_value": p}


def holm_adjust(p_values: Sequence[float]) -> list[float]:
    """Holm–Bonferroni step-down adjusted p-values (same order as input)."""
    m = len(p_values)
    order = sorted(range(m), key=lambda i: p_values[i])
    adjusted = [0.0] * m
    running = 0.0
    for rank, index in enumerate(order):
        value = min(1.0, (m - rank) * p_values[index])
        running = max(running, value)
        adjusted[index] = running
    return adjusted


def _average_ranks(values: Sequence[float]) -> list[float]:
    """Rank 1 = smallest value; ties get the average rank."""
    order = sorted(range(len(values)), key=lambda i: values[i])
    ranks = [0.0] * len(values)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
            j += 1
        avg = (i + j) / 2 + 1
        for t in range(i, j + 1):
            ranks[order[t]] = avg
        i = j + 1
    return ranks


def bootstrap_rank_intervals(
    flags_by_adapter: Mapping[str, Sequence[bool]],
    clusters: Sequence[str],
    *,
    iterations: int = 2000,
    seed: int = 0,
    alpha: float = 0.05,
) -> dict[str, dict[str, Any]]:
    """Rank (1 = lowest rate, i.e. least leakage) with a bootstrap interval."""
    names = list(flags_by_adapter)
    if not names:
        return {}
    point = _average_ranks([sum(f) / len(f) if f else 0.0 for f in flags_by_adapter.values()])
    if not clusters:
        return {n: {"rank": point[i], "low": None, "high": None} for i, n in enumerate(names)}
    groups = _group(clusters)
    sums = {n: _cluster_sums(groups, flags_by_adapter[n]) for n in names}
    samples: dict[str, list[float]] = {n: [] for n in names}
    for picks in _replicate_indices(groups, iterations, seed):
        total = sum(sums[names[0]][i][1] for i in picks)
        rates = [sum(sums[n][i][0] for i in picks) / total for n in names]
        for n, r in zip(names, _average_ranks(rates)):
            samples[n].append(r)
    out = {}
    for i, n in enumerate(names):
        s = sorted(samples[n])
        out[n] = {
            "rank": point[i],
            "low": _quantile(s, alpha / 2),
            "high": _quantile(s, 1 - alpha / 2),
        }
    return out
