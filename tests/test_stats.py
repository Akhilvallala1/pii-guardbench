"""Cluster bootstrap, McNemar exact, Holm, rank intervals. No network."""

from __future__ import annotations

import pytest

from pii_guardbench.adapters.base import AdapterFlags, AdapterResult
from pii_guardbench.evaluator import evaluate
from pii_guardbench.stats import (
    bootstrap_rank_intervals,
    cluster_bootstrap_ci,
    cluster_key,
    holm_adjust,
    mcnemar_exact,
    paired_bootstrap_diff_ci,
)


def test_mcnemar_exact_reference_values() -> None:
    # b=5, c=0: p = 2 * 0.5**5 = 0.0625
    r = mcnemar_exact([True] * 5 + [False] * 3, [False] * 5 + [False] * 3)
    assert (r["b"], r["c"]) == (5, 0)
    assert r["p_value"] == pytest.approx(0.0625)
    # b=3, c=1: p = 2 * (1 + 4) / 16 = 0.625
    r = mcnemar_exact([True, True, True, False], [False, False, False, True])
    assert r["p_value"] == pytest.approx(0.625)
    # No discordant pairs -> p = 1
    assert mcnemar_exact([True, False], [True, False])["p_value"] == 1.0
    # Symmetric b=c is capped at 1
    assert mcnemar_exact([True, False], [False, True])["p_value"] == 1.0


def test_holm_reference_values() -> None:
    assert holm_adjust([0.01, 0.04, 0.03]) == pytest.approx([0.03, 0.06, 0.06])
    assert holm_adjust([0.5, 0.9]) == pytest.approx([1.0, 1.0])
    assert holm_adjust([]) == []


def test_bootstrap_is_seeded_and_configurable() -> None:
    flags = [True, False] * 20
    clusters = [str(i) for i in range(40)]
    a = cluster_bootstrap_ci(flags, clusters, iterations=300, seed=1)
    b = cluster_bootstrap_ci(flags, clusters, iterations=300, seed=1)
    c = cluster_bootstrap_ci(flags, clusters, iterations=300, seed=2)
    assert a == b
    assert (a.low, a.high) != (c.low, c.high)
    assert a.iterations == 300 and a.n_clusters == 40
    assert a.low < 0.5 < a.high
    with pytest.raises(ValueError):
        cluster_bootstrap_ci(flags, clusters, iterations=0)


def test_clustering_widens_interval() -> None:
    # 10 clusters of 4 identical outcomes vs the same 40 outcomes unclustered.
    flags = []
    for i in range(10):
        flags += [i % 2 == 0] * 4
    iid = cluster_bootstrap_ci(flags, [str(i) for i in range(40)], iterations=2000, seed=0)
    clustered = cluster_bootstrap_ci(flags, [str(i // 4) for i in range(40)], iterations=2000, seed=0)
    assert (clustered.high - clustered.low) > (iid.high - iid.low)


def test_degenerate_and_empty_inputs() -> None:
    all_true = cluster_bootstrap_ci([True] * 5, list("abcde"), iterations=50)
    assert all_true.low == all_true.high == 1.0
    empty = cluster_bootstrap_ci([], [], iterations=50)
    assert empty.estimate is None and empty.format_ci() == "n/a"


def test_paired_diff_and_rank_intervals() -> None:
    a = [True] * 30 + [False] * 10
    b = [False] * 40
    clusters = [str(i) for i in range(40)]
    diff = paired_bootstrap_diff_ci(a, b, clusters, iterations=500, seed=0)
    assert diff.estimate == pytest.approx(0.75)
    assert diff.low > 0
    ranks = bootstrap_rank_intervals({"leaky": a, "tight": b}, clusters, iterations=500, seed=0)
    assert ranks["tight"]["rank"] == 1.0 and ranks["leaky"]["rank"] == 2.0
    assert ranks["tight"]["high"] == 1.0
    tied = bootstrap_rank_intervals({"x": b, "y": b}, clusters, iterations=50)
    assert tied["x"]["rank"] == tied["y"]["rank"] == 1.5


def test_cluster_key_precedence() -> None:
    assert cluster_key({"id": "a", "metadata": {"cluster": "c1", "template_id": "t"}}) == "c1"
    assert cluster_key({"id": "a", "metadata": {"template_id": "t"}}) == "t"
    assert cluster_key({"id": "a"}) == "a"


def test_evaluate_adds_holm_adjusted_pairwise_and_ranks() -> None:
    prompts = [
        {"id": f"p{i}", "text": f"ssn 790-18-34{i:02d}", "sensitive_spans": [
            {"category": "ssn", "start": 4, "end": 15, "value": f"790-18-34{i:02d}"}],
         "metadata": {"template_id": f"t{i % 3}"}}
        for i in range(10, 22)
    ]
    leak = [AdapterResult(p["text"], AdapterFlags()) for p in prompts]
    safe = [AdapterResult("ssn [X]", AdapterFlags()) for _ in prompts]
    report = evaluate(prompts, {"a": leak, "b": safe, "c": safe}, bootstrap_iterations=200, seed=0)
    assert len(report.pairwise) == 3
    ab = report.pairwise[0]
    assert (ab["adapter_a"], ab["adapter_b"], ab["b"], ab["c"]) == ("a", "b", 12, 0)
    assert ab["p_holm"] >= ab["p_value"]
    assert report.bootstrap["n_clusters"] == 3
    assert report.ranks["a"]["rank"] == 3.0
    assert report.to_dict()["pairwise_mcnemar_holm"][0]["p_holm"] == ab["p_holm"]
