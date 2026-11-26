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
* ``control_blocked_rate`` is the share of no-PII control prompts the guard
  blocked (``blocked_rate`` is over PII prompts only).
* A control (no-PII) prompt is a raw false positive if it was blocked, or a
  whitespace token was deleted or replaced (tokens are NFKC-normalized and
  stripped of leading/trailing punctuation first). Whitespace changes,
  trailing newlines, edge punctuation changes and pure insertions are ignored.
* A contract-aware false positive is a raw false positive that was blocked or
  redacted at least one native entity type (``AdapterFlags.detected_categories``)
  that the adapter's ``category_map`` sends to a category in its
  ``entity_contract``. Adapters without a contract or map (mocks) get n/a.
* Layers: ``metadata.layer`` when it is clean/control/hard; otherwise
  difficulty ``adversarial`` -> hard, no spans -> control, else clean.
"""

from __future__ import annotations

import difflib
import math
import unicodedata
from dataclasses import asdict, dataclass, field
from typing import Any, Iterable, Mapping, Optional, Sequence

from pii_guardbench.adapters.base import AdapterResult
from pii_guardbench.matching import PARTIAL_CATEGORIES, SpanMatch, exact_present, match_span
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


LAYERS = ("clean", "control", "hard")
FAMILY_PREFIXES = {"cred": "credentials", "fin": "finance", "hea": "health"}
# Category value in a category_map meaning "any category in the contract".
ANY_IN_CONTRACT = "*"


def layer_of(prompt: Mapping[str, Any]) -> str:
    """``metadata.layer`` if it is clean/control/hard; else difficulty
    ``adversarial`` -> hard, no sensitive spans -> control, otherwise clean."""
    layer = (prompt.get("metadata") or {}).get("layer")
    if layer in LAYERS:
        return str(layer)
    if prompt.get("difficulty") == "adversarial":
        return "hard"
    if not prompt.get("sensitive_spans"):
        return "control"
    return "clean"


def template_of(prompt: Mapping[str, Any]) -> Optional[str]:
    tid = (prompt.get("metadata") or {}).get("template_id")
    return None if tid in (None, "") else str(tid)


def family_of(prompt: Mapping[str, Any]) -> Optional[str]:
    """credentials/finance/health: clean templates by prefix, else the domain."""
    tid = template_of(prompt)
    if tid:
        prefix = tid.split("-", 1)[0]
        if prefix in FAMILY_PREFIXES:
            return FAMILY_PREFIXES[prefix]
    dom = prompt.get("domain")
    return None if dom in (None, "", "none") else str(dom)


def contract_false_positive(
    raw_fp: bool,
    blocked: bool,
    detected_types: Sequence[str],
    contract: Optional[Iterable[str]],
    category_map: Optional[Mapping[str, str]],
) -> Optional[bool]:
    """Contract-aware FP: a raw FP that was blocked or redacted >= 1 native
    type mapping to an in-contract category. None without contract/map."""
    if contract is None or category_map is None:
        return None
    if not raw_fp:
        return False
    if blocked:
        return True
    cats = set(contract)
    for t in detected_types:
        cat = category_map.get(t)
        if cat == ANY_IN_CONTRACT or (cat is not None and cat in cats):
            return True
    return False


def prompt_leaked(prompt: Mapping[str, Any], result: AdapterResult) -> bool:
    """Normalized leak: any planted value still exposed in the returned text.

    The ``blocked`` flag does not excuse residual text (see module docstring).
    """
    text = result.redacted_text or ""
    return any(m.normalized for m in _span_units(prompt.get("sensitive_spans") or [], text))


def prompt_leaked_exact(prompt: Mapping[str, Any], result: AdapterResult) -> bool:
    text = result.redacted_text or ""
    return any(m.exact for m in _span_units(prompt.get("sensitive_spans") or [], text))


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
    template_id: Optional[str]
    family: Optional[str]
    blocked: bool
    exact_leak: Optional[bool]
    normalized_leak: Optional[bool]
    partial_exposure: Optional[bool]
    false_positive: Optional[bool]
    contract_false_positive: Optional[bool]
    detected_types: list[str]
    latency_ms: float
    span_matches: list[dict[str, Any]]
    output_text: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _span_units(spans: Sequence[Mapping[str, Any]], text: str) -> list[SpanMatch]:
    """One match per planted value.

    Fragments of a value split across sentences (``obfuscation ==
    "split_sentences"``, same ``canonical_value``) form one unit: it is a full
    (normalized/exact) leak only if every fragment survives or the whole
    canonical value appears; if only some fragments survive it is a partial
    exposure, not a full leak.
    """
    units: list[SpanMatch] = []
    groups: dict[str, list[SpanMatch]] = {}
    order: list[Any] = []
    for span in spans:
        m = match_span(span, text)
        if span.get("obfuscation") == "split_sentences" and span.get("canonical_value"):
            key = span["canonical_value"]
            if key not in groups:
                groups[key] = []
                order.append(("g", key))
            groups[key].append(m)
        else:
            order.append(("s", m))
    for kind, item in order:
        if kind == "s":
            units.append(item)
            continue
        frags = groups[item]
        full_norm = all(f.normalized for f in frags)
        units.append(
            SpanMatch(
                category=frags[0].category,
                exact=all(f.exact for f in frags),
                normalized=full_norm,
                partial=(not full_norm) and any(f.normalized or f.partial for f in frags),
            )
        )
    return units


def score_prompt(
    adapter: str,
    prompt: Mapping[str, Any],
    result: AdapterResult,
    *,
    contract: Optional[Iterable[str]] = None,
    category_map: Optional[Mapping[str, str]] = None,
) -> PromptOutcome:
    text = result.redacted_text or ""
    spans = prompt.get("sensitive_spans") or []
    matches = _span_units(spans, text)
    is_pii = bool(spans)
    partial_spans = [m for m in matches if m.category in PARTIAL_CATEGORIES or m.partial]
    detected = sorted({str(t) for t in (result.flags.detected_categories or [])})
    raw_fp = None if is_pii else is_false_positive(prompt, result)
    return PromptOutcome(
        prompt_id=str(prompt.get("id")),
        adapter=adapter,
        is_pii=is_pii,
        layer=layer_of(prompt),
        difficulty=prompt.get("difficulty"),
        domain=prompt.get("domain"),
        cluster=cluster_key(prompt),
        template_id=template_of(prompt),
        family=family_of(prompt),
        blocked=bool(result.flags.blocked),
        exact_leak=any(m.exact for m in matches) if is_pii else None,
        normalized_leak=any(m.normalized for m in matches) if is_pii else None,
        partial_exposure=(any(m.partial for m in partial_spans) if partial_spans else None),
        false_positive=raw_fp,
        contract_false_positive=(
            None if is_pii else contract_false_positive(
                bool(raw_fp), bool(result.flags.blocked), detected, contract, category_map
            )
        ),
        detected_types=detected,
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
    by_obfuscation: dict[str, ProportionCI] = field(default_factory=dict)
    # Per planted value (split fragments count once). Spans cluster inside
    # prompts and templates, so the interval is a cluster bootstrap.
    span_leak: dict[str, Any] = field(default_factory=dict)
    span_leak_ci: Optional[ProportionCI] = None
    span_leak_bootstrap: Optional[BootstrapCI] = None
    contract: Optional[list[str]] = None
    # Controls (no-PII prompts). FPR intervals: cluster bootstrap over control
    # templates (reported) and Wilson (iid, secondary).
    false_positive_bootstrap: Optional[BootstrapCI] = None
    control_blocked: Optional[ProportionCI] = None
    contract_false_positive: Optional[ProportionCI] = None  # None = n/a (no contract)
    contract_false_positive_bootstrap: Optional[BootstrapCI] = None
    fp_blocks: int = 0
    fp_by_type: dict[str, int] = field(default_factory=dict)
    fp_by_template: dict[str, dict[str, Any]] = field(default_factory=dict)
    by_template: dict[str, ProportionCI] = field(default_factory=dict)
    by_family: dict[str, ProportionCI] = field(default_factory=dict)
    in_scope: dict[str, Any] = field(default_factory=dict)
    blocked_bootstrap: Optional[BootstrapCI] = None
    control_blocked_bootstrap: Optional[BootstrapCI] = None
    shared_scope: dict[str, Any] = field(default_factory=dict)
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
            "false_positive_cluster_bootstrap": opt(self.false_positive_bootstrap),
            "control_blocked_rate": opt(self.control_blocked),
            "blocked_rate_cluster_bootstrap": opt(self.blocked_bootstrap),
            "control_blocked_rate_cluster_bootstrap": opt(self.control_blocked_bootstrap),
            "contract_false_positive": opt(self.contract_false_positive),
            "contract_false_positive_cluster_bootstrap": opt(self.contract_false_positive_bootstrap),
            "false_positive_blocks": self.fp_blocks,
            "control_fp_by_detected_type": dict(self.fp_by_type),
            "control_fp_by_template": {
                t: {k: (v.to_dict() if hasattr(v, "to_dict") else v) for k, v in d.items()}
                for t, d in self.fp_by_template.items()
            },
            "by_template": {k: v.to_dict() for k, v in self.by_template.items()},
            "by_family": {k: v.to_dict() for k, v in self.by_family.items()},
            "latency_ms": self.latency.to_dict(),
            "by_category": {key: value.to_dict() for key, value in self.by_category.items()},
            "by_layer": {
                layer: {metric: ci.to_dict() for metric, ci in metrics.items()}
                for layer, metrics in self.by_layer.items()
            },
            "span_leak": self.span_leak,
            "by_obfuscation": {k: v.to_dict() for k, v in self.by_obfuscation.items()},
            "span_leak_wilson_unadjusted": opt(self.span_leak_ci),
            "span_leak_cluster_bootstrap": opt(self.span_leak_bootstrap),
            "entity_contract": self.contract,
            "in_scope": _scope_dict(self.in_scope),
            "shared_scope": _scope_dict(self.shared_scope),
        }


def _scope_dict(scope: Mapping[str, Any]) -> dict[str, Any]:
    return {k: (v.to_dict() if hasattr(v, "to_dict") else v) for k, v in scope.items()}


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
    shared_categories: Optional[list[str]] = None
    pairwise_summary: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "prompts_path": self.prompts_path,
            "n_prompts": self.n_prompts,
            "n_pii": self.n_pii,
            "n_controls": self.n_controls,
            "bootstrap": self.bootstrap,
            "results": [score.to_dict() for score in self.scores],
            "pairwise_mcnemar_holm": self.pairwise,
            "holm_family": "all pairs of non-mock adapters; mock pairs reported unadjusted",
            "pairwise_summary": self.pairwise_summary,
            "rank_intervals": self.ranks,
            "shared_scope_categories": self.shared_categories,
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
    contract: Optional[Iterable[str]] = None,
    shared_categories: Optional[Iterable[str]] = None,
    category_map: Optional[Mapping[str, str]] = None,
) -> AdapterScore:
    """Aggregate every metric for one adapter on the shared prompt list."""
    if len(prompts) != len(results):
        raise ValueError(
            f"Prompt/result length mismatch for {name}: "
            f"{len(prompts)} prompts, {len(results)} results."
        )
    contract_set = set(contract) if contract is not None else None
    outcomes = [
        score_prompt(name, p, r, contract=contract_set, category_map=category_map)
        for p, r in zip(prompts, results)
    ]
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

    by_obf: dict[str, list[bool]] = {}
    for prompt, o in zip(prompts, outcomes):
        obf = (prompt.get("metadata") or {}).get("obfuscation")
        if obf and o.is_pii:
            by_obf.setdefault(str(obf), []).append(bool(o.normalized_leak))

    blocked_pii = [o for o in pii if o.blocked]
    leak_flags = [bool(o.normalized_leak) for o in pii]
    span_flags = [bool(m["normalized"]) for o in pii for m in o.span_matches]
    span_clusters = [o.cluster for o in pii for _ in o.span_matches]
    contract_list = sorted(contract_set) if contract_set is not None else None

    by_tpl: dict[str, list[bool]] = {}
    by_fam: dict[str, list[bool]] = {}
    for o in pii:
        if o.template_id:
            by_tpl.setdefault(o.template_id, []).append(bool(o.normalized_leak))
        if o.family:
            by_fam.setdefault(o.family, []).append(bool(o.normalized_leak))

    fp_flags = [bool(o.false_positive) for o in controls]
    ctl_clusters = [o.cluster for o in controls]
    has_contract = contract_set is not None and category_map is not None
    cfp_flags = [bool(o.contract_false_positive) for o in controls]
    fp_by_type: dict[str, int] = {}
    for o in controls:
        if o.false_positive:
            for t in o.detected_types:
                fp_by_type[t] = fp_by_type.get(t, 0) + 1
    fp_by_tpl: dict[str, dict[str, Any]] = {}
    for key in sorted({o.cluster for o in controls}):
        sub_c = [o for o in controls if o.cluster == key]
        fp_by_tpl[key] = {
            "raw": _ci([bool(o.false_positive) for o in sub_c]),
            "contract": _ci([bool(o.contract_false_positive) for o in sub_c]) if has_contract else None,
            "blocked": sum(1 for o in sub_c if o.blocked),
        }
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
        false_positive=_ci(fp_flags),
        false_positive_bootstrap=cluster_bootstrap_ci(
            fp_flags, ctl_clusters, iterations=bootstrap_iterations, seed=seed
        ),
        control_blocked=_ci([o.blocked for o in controls]),
        blocked_bootstrap=cluster_bootstrap_ci(
            [o.blocked for o in pii], [o.cluster for o in pii], iterations=bootstrap_iterations, seed=seed
        ),
        control_blocked_bootstrap=cluster_bootstrap_ci(
            [o.blocked for o in controls], ctl_clusters, iterations=bootstrap_iterations, seed=seed
        ),
        contract_false_positive=_ci(cfp_flags) if has_contract else None,
        contract_false_positive_bootstrap=(
            cluster_bootstrap_ci(cfp_flags, ctl_clusters, iterations=bootstrap_iterations, seed=seed)
            if has_contract else None
        ),
        fp_blocks=sum(1 for o in controls if o.false_positive and o.blocked),
        fp_by_type=dict(sorted(fp_by_type.items())),
        fp_by_template=fp_by_tpl,
        by_template={k: _ci(v) for k, v in sorted(by_tpl.items())},
        by_family={k: _ci(v) for k, v in sorted(by_fam.items())},
        latency=_latency_stats(results),
        by_category={c: _ci(f) for c, f in sorted(categories.items())},
        by_layer=layers,
        by_obfuscation={k: _ci(v) for k, v in sorted(by_obf.items())},
        span_leak={
            "k": span_leaked,
            "n": span_total,
            "rate": (span_leaked / span_total) if span_total else None,
        },
        span_leak_ci=_ci(span_flags),
        span_leak_bootstrap=cluster_bootstrap_ci(
            span_flags, span_clusters, iterations=bootstrap_iterations, seed=seed
        ),
        contract=contract_list,
        in_scope=_scope_metrics(pii, contract_list, bootstrap_iterations, seed),
        shared_scope=_scope_metrics(
            pii, sorted(shared_categories) if shared_categories is not None else None, bootstrap_iterations, seed
        ),
        outcomes=outcomes,
    )


def _scope_metrics(
    pii: Sequence[PromptOutcome],
    categories: Optional[Sequence[str]],
    bootstrap_iterations: int = DEFAULT_BOOTSTRAP_ITERATIONS,
    seed: int = DEFAULT_SEED,
) -> dict[str, Any]:
    """Prompt- and span-level normalized leak restricted to ``categories``.

    ``prompt_leak_cluster_bootstrap`` is the prompt-level cluster-bootstrap CI
    over the same prompt cluster keys as overall leak (the reported interval);
    ``prompt_leak`` keeps the Wilson (iid) interval."""
    if categories is None:
        return {}
    cats = set(categories)
    prompt_flags: list[bool] = []
    prompt_clusters: list[str] = []
    span_flags: list[bool] = []
    for o in pii:
        inside = [m for m in o.span_matches if m["category"] in cats]
        if not inside:
            continue
        prompt_flags.append(any(m["normalized"] for m in inside))
        prompt_clusters.append(o.cluster)
        span_flags.extend(bool(m["normalized"]) for m in inside)
    return {
        "categories": sorted(cats),
        "prompt_leak": _ci(prompt_flags),
        "prompt_leak_cluster_bootstrap": cluster_bootstrap_ci(
            prompt_flags, prompt_clusters, iterations=bootstrap_iterations, seed=seed
        ),
        "span_leak": _ci(span_flags),
    }


def evaluate(
    prompts: Sequence[Mapping[str, Any]],
    runs: Mapping[str, Sequence[AdapterResult]],
    *,
    prompts_path: Optional[str] = None,
    bootstrap_iterations: int = DEFAULT_BOOTSTRAP_ITERATIONS,
    seed: int = DEFAULT_SEED,
    contracts: Optional[Mapping[str, Optional[Iterable[str]]]] = None,
    category_maps: Optional[Mapping[str, Optional[Mapping[str, str]]]] = None,
) -> BenchmarkReport:
    """Score every adapter in ``runs`` against the same prompt list.

    ``contracts`` maps adapter id -> benchmark categories it is configured to
    cover (``None`` = no declared contract). The shared scope is the
    intersection of all declared contracts of non-mock adapters.

    Adds paired exact McNemar tests (normalized leak, PII prompts) for every
    adapter pair with Holm correction, paired cluster-bootstrap CIs on the
    difference, and bootstrap rank intervals.
    """
    contracts = dict(contracts or {})
    category_maps = dict(category_maps or {})
    declared = [set(c) for n, c in contracts.items() if c is not None and not is_reference_adapter(n)]
    shared = sorted(set.intersection(*declared)) if declared else None
    scores = [
        score_adapter(
            name,
            prompts,
            results,
            bootstrap_iterations=bootstrap_iterations,
            seed=seed,
            contract=contracts.get(name),
            shared_categories=shared,
            category_map=category_maps.get(name),
        )
        for name, results in runs.items()
    ]
    flags = {
        s.adapter: [bool(o.normalized_leak) for o in s.outcomes if o.is_pii] for s in scores
    }
    pii_clusters = [cluster_key(p) for p in prompts if _is_pii_prompt(p)]
    ctl_clusters = [cluster_key(p) for p in prompts if _is_control_prompt(p)]
    fp = {s.adapter: [bool(o.false_positive) for o in s.outcomes if not o.is_pii] for s in scores}

    pairwise: list[dict[str, Any]] = []
    names = [s.adapter for s in scores]
    for i in range(len(names)):
        for j in range(i + 1, len(names)):
            a, b = names[i], names[j]
            test = mcnemar_exact(flags[a], flags[b])
            diff = paired_bootstrap_diff_ci(
                flags[a], flags[b], pii_clusters, iterations=bootstrap_iterations, seed=seed
            )
            fdiff = paired_bootstrap_diff_ci(
                fp[a], fp[b], ctl_clusters, iterations=bootstrap_iterations, seed=seed
            )
            pairwise.append(
                {
                    "adapter_a": a,
                    "adapter_b": b,
                    **test,
                    "diff_bootstrap": diff.to_dict(),
                    "cluster_ci_excludes_0": _excludes_zero(diff),
                    "fpr_mcnemar": mcnemar_exact(fp[a], fp[b]) if ctl_clusters else None,
                    "fpr_diff_bootstrap": fdiff.to_dict(),
                    "fpr_cluster_ci_excludes_0": _excludes_zero(fdiff),
                }
            )
    family = [r for r in pairwise if not (is_reference_adapter(r["adapter_a"]) or is_reference_adapter(r["adapter_b"]))]
    for row, adj in zip(family, holm_adjust([r["p_value"] for r in family])):
        row["p_holm"] = adj
        row["holm_family"] = "systems"
    # FPR McNemar: Holm-adjusted separately (own family, same non-mock pairs).
    fpr_family = [r for r in family if r.get("fpr_mcnemar") is not None]
    for row, adj in zip(fpr_family, holm_adjust([r["fpr_mcnemar"]["p_value"] for r in fpr_family])):
        row["fpr_p_holm"] = adj
    for row in pairwise:
        if "p_holm" not in row:
            row["p_holm"] = None
            row["holm_family"] = "reference (mock; not adjusted)"
        row.setdefault("fpr_p_holm", None)

    summary = pairwise_summary(pairwise)

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
        shared_categories=shared,
        pairwise_summary=summary,
        bootstrap={
            "iterations": bootstrap_iterations,
            "seed": seed,
            # Legacy alias of n_pii_clusters (kept for old readers; 0 on a
            # controls-only run). Use n_pii_clusters / n_control_clusters.
            "n_clusters": len(set(pii_clusters)),
            "n_clusters_note": "legacy alias of n_pii_clusters",
            "n_pii_clusters": len(set(pii_clusters)),
            "n_control_clusters": len(set(ctl_clusters)),
            "cluster_key": "metadata.cluster > metadata.template_id > id",
        },
    )


def _excludes_zero(ci: BootstrapCI) -> Optional[bool]:
    if ci.low is None or ci.high is None:
        return None
    return ci.low > 0 or ci.high < 0


def pairwise_summary(pairwise: Sequence[Mapping[str, Any]], alpha: float = 0.05) -> dict[str, Any]:
    """Counts over non-mock pairs: Holm-significant (leak McNemar), how many of
    those also have a cluster-bootstrap difference CI excluding 0, and total
    pairs whose cluster CI excludes 0 (leak and FPR)."""
    fam = [r for r in pairwise if r.get("p_holm") is not None]
    holm = [r for r in fam if r["p_holm"] < alpha]
    return {
        "alpha": alpha,
        "n_pairs": len(fam),
        "holm_significant": len(holm),
        "holm_significant_and_cluster_ci_excludes_0": sum(1 for r in holm if r.get("cluster_ci_excludes_0")),
        "cluster_ci_excludes_0": sum(1 for r in fam if r.get("cluster_ci_excludes_0")),
        "fpr_cluster_ci_excludes_0": sum(1 for r in fam if r.get("fpr_cluster_ci_excludes_0")),
        "fpr_holm_significant": sum(1 for r in fam if r.get("fpr_p_holm") is not None and r["fpr_p_holm"] < alpha),
        "fpr_holm_significant_and_cluster_ci_excludes_0": sum(
            1 for r in fam
            if r.get("fpr_p_holm") is not None and r["fpr_p_holm"] < alpha and r.get("fpr_cluster_ci_excludes_0")
        ),
    }


def is_reference_adapter(name: str) -> bool:
    """Mock/reference adapters are excluded from the Holm family and shared scope."""
    return name.startswith("mock")


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
            f"  bootstrap: {b['iterations']} iterations, seed {b['seed']}; "
            f"PII clusters {b.get('n_pii_clusters', b.get('n_clusters', 0))}, "
            f"control clusters {b.get('n_control_clusters', 0)} ({b['cluster_key']})"
        )
    lines.append(
        "  intervals: cluster-bootstrap 95% (reported); Wilson columns at the end are iid, secondary"
    )
    lines.append(
        "  blocked = share of PII prompts blocked; ctl blocked = share of no-PII controls blocked"
    )
    lines.append("")

    def boot(ci: Optional[BootstrapCI]) -> str:
        return ci.format_ci() if ci else "n/a"

    headers = (
        "Adapter",
        "Norm leak",
        "Cluster 95%",
        "Span leak",
        "Cluster 95%",
        "FPR",
        "Cluster 95%",
        "Exact leak",
        "Partial",
        "Blocked",
        "Cluster 95%",
        "Resid|block",
        "Ctl blocked",
        "Cluster 95%",
        "Latency ms (mean/p50/p95)",
        "Wilson (iid, secondary): leak",
        "span",
        "FPR",
    )
    rows = [
        (
            s.adapter,
            s.leak.format_rate(),
            boot(s.leak_bootstrap),
            _fmt_opt(s.span_leak_ci),
            boot(s.span_leak_bootstrap),
            s.false_positive.format_rate(),
            boot(s.false_positive_bootstrap),
            _fmt_opt(s.exact_leak),
            _fmt_opt(s.partial),
            _fmt_opt(s.blocked),
            boot(s.blocked_bootstrap),
            _fmt_opt(s.residual_given_block),
            _fmt_opt(s.control_blocked),
            boot(s.control_blocked_bootstrap),
            s.latency.format(),
            s.leak.format_ci(),
            s.span_leak_ci.format_ci() if s.span_leak_ci else "n/a",
            s.false_positive.format_ci(),
        )
        for s in report.scores
    ]
    lines.extend(_align_table(headers, rows))

    if report.n_controls:
        lines.append("")
        lines.append(
            "Control false positives: raw vs contract-aware (contract-aware = blocked, or redacted a "
            "native type mapping to a category in the adapter's entity contract)"
        )
        fp_rows = [
            (
                s.adapter,
                s.false_positive.format_rate(),
                boot(s.false_positive_bootstrap),
                s.false_positive.format_ci(),
                _fmt_opt(s.contract_false_positive) if s.contract_false_positive else "n/a",
                boot(s.contract_false_positive_bootstrap) if s.contract_false_positive else "n/a",
                s.contract_false_positive.format_ci() if s.contract_false_positive else "n/a",
                str(s.fp_blocks),
            )
            for s in report.scores
        ]
        lines.extend(
            _align_table(
                ("Adapter", "Raw FPR", "Cluster 95%", "Wilson (iid)", "Contract FPR", "Cluster 95%",
                 "Wilson (iid)", "FPs that are blocks"),
                fp_rows,
            )
        )
        lines.append("")
        lines.append("Control false positives by redacted entity type (controls with that native type redacted)")
        type_rows = [
            (s.adapter, t, str(n)) for s in report.scores for t, n in s.fp_by_type.items()
        ] or [("n/a", "n/a", "0")]
        lines.extend(_align_table(("Adapter", "Native type", "Controls"), type_rows))
        lines.append("")
        lines.append("Control false positives by template")
        tpl_rows = [
            (
                s.adapter,
                t,
                d["raw"].format_rate(),
                d["contract"].format_rate() if d["contract"] is not None else "n/a",
                str(d["blocked"]),
            )
            for s in report.scores
            for t, d in s.fp_by_template.items()
        ]
        lines.extend(_align_table(("Adapter", "Template", "Raw FP", "Contract FP", "Blocked"), tpl_rows))

    fam_rows = [
        (s.adapter, fam, ci.format_rate(), ci.format_ci())
        for s in report.scores
        for fam, ci in s.by_family.items()
    ]
    if fam_rows:
        lines.append("")
        lines.append("Leak by template family (normalized leak; Wilson, no clustering)")
        lines.extend(_align_table(("Adapter", "Family", "Norm leak", "Wilson 95%"), fam_rows))
    tpl_leak_rows = [
        (s.adapter, t, ci.format_rate(), ci.format_ci())
        for s in report.scores
        for t, ci in s.by_template.items()
    ]
    if tpl_leak_rows:
        lines.append("")
        lines.append("Leak by template (normalized leak; Wilson within one template)")
        lines.extend(_align_table(("Adapter", "Template", "Norm leak", "Wilson 95%"), tpl_leak_rows))
    lines.append("")
    lines.append("Layer breakdown (clean / hard obfuscation; controls have no leak)")
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

    obf_rows = [
        (s.adapter, obf, ci.format_rate(), ci.format_ci())
        for s in report.scores
        for obf, ci in s.by_obfuscation.items()
    ]
    if obf_rows:
        lines.append("")
        lines.append("Hard-layer breakdown by obfuscation (normalized leak)")
        lines.extend(_align_table(("Adapter", "Obfuscation", "Norm leak", "Wilson 95%"), obf_rows))

    lines.append("")
    lines.append("In-scope entity contract (normalized leak on categories each system is configured for)")
    scope_rows: list[tuple[str, ...]] = []
    for s in report.scores:
        if s.contract is None:
            scope_rows.append((s.adapter, "(none declared: reference)", "n/a", "n/a", "n/a", "n/a", "n/a", "n/a"))
            continue
        ins, sh = s.in_scope, s.shared_scope
        scope_rows.append(
            (
                s.adapter,
                ",".join(s.contract) or "(empty)",
                ins["prompt_leak"].format_rate() if ins else "n/a",
                boot(ins.get("prompt_leak_cluster_bootstrap")) if ins else "n/a",
                ins["span_leak"].format_rate() if ins else "n/a",
                sh["prompt_leak"].format_rate() if sh else "n/a",
                boot(sh.get("prompt_leak_cluster_bootstrap")) if sh else "n/a",
                sh["span_leak"].format_rate() if sh else "n/a",
            )
        )
    lines.extend(
        _align_table(
            ("Adapter", "Configured categories", "In-scope prompt leak", "Cluster 95%", "In-scope span leak",
             "Shared-scope prompt leak", "Cluster 95%", "Shared-scope span leak"),
            scope_rows,
        )
    )
    lines.append(
        "  shared scope = "
        + (",".join(report.shared_categories) if report.shared_categories else "n/a")
        + " (intersection of declared contracts of non-mock adapters)"
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
    lines.append("Paired McNemar exact tests on normalized leak (Holm over non-mock pairs; 'ref' = mock pair, unadjusted)")
    if report.pairwise:
        pw_rows = [
            (
                f"{r['adapter_a']} vs {r['adapter_b']}",
                str(r["b"]),
                str(r["c"]),
                f"{r['p_value']:.4g}",
                "ref" if r["p_holm"] is None else f"{r['p_holm']:.4g}",
                _fmt_diff(r["diff_bootstrap"]),
                _yn(r.get("cluster_ci_excludes_0")),
                _fmt_diff(r.get("fpr_diff_bootstrap") or {}),
                "n/a" if not r.get("fpr_mcnemar") else f"{r['fpr_mcnemar']['p_value']:.4g}",
                "ref" if r.get("fpr_p_holm") is None else f"{r['fpr_p_holm']:.4g}",
            )
            for r in report.pairwise
        ]
        lines.extend(
            _align_table(
                ("Pair (A vs B)", "only A leaks", "only B leaks", "p exact", "p Holm",
                 "rate(A)-rate(B) [cluster-boot 95%]", "cluster CI excl. 0",
                 "FPR(A)-FPR(B) [cluster-boot 95%]", "FPR p exact", "FPR p Holm"),
                pw_rows,
            )
        )
        ps = report.pairwise_summary
        if ps:
            lines.append(
                f"  summary (non-mock pairs, n={ps['n_pairs']}): Holm-significant at {ps['alpha']}: "
                f"{ps['holm_significant']}; of those, cluster CI excludes 0: "
                f"{ps['holm_significant_and_cluster_ci_excludes_0']}; total pairs with leak cluster CI "
                f"excluding 0: {ps['cluster_ci_excludes_0']}; FPR cluster CI excluding 0: "
                f"{ps['fpr_cluster_ci_excludes_0']}; FPR Holm-significant (separate Holm family): "
                f"{ps.get('fpr_holm_significant', 0)}; of those, FPR cluster CI excludes 0: "
                f"{ps.get('fpr_holm_significant_and_cluster_ci_excludes_0', 0)}"
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


def _yn(v: Optional[bool]) -> str:
    return "n/a" if v is None else ("yes" if v else "no")


def _fmt_diff(d: Mapping[str, Any]) -> str:
    if d.get("estimate") is None or d.get("low") is None:
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
