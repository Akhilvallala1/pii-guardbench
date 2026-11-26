"""In-scope contracts, span-level leak, Holm family, split grouping. Mocks only."""

from __future__ import annotations

from pii_guardbench.adapters.base import AdapterFlags, AdapterResult
from pii_guardbench.evaluator import evaluate, format_leaderboard, prompt_leaked, score_adapter, score_prompt

EMAIL = "jane.q7@example.org"
KEY = "sk_test_FAKE_abc123_def456_pgb"
PROMPTS = [
    {"id": "p1", "text": f"mail {EMAIL} key {KEY}",
     "sensitive_spans": [{"category": "email", "start": 5, "end": 5 + len(EMAIL), "value": EMAIL},
                         {"category": "api_key", "start": 10 + len(EMAIL), "end": 10 + len(EMAIL) + len(KEY), "value": KEY}],
     "metadata": {"template_id": "t1"}},
    {"id": "p2", "text": f"key {KEY[:-3]}zzz only",
     "sensitive_spans": [{"category": "api_key", "start": 4, "end": 4 + len(KEY), "value": KEY[:-3] + "zzz"}],
     "metadata": {"template_id": "t2"}},
    {"id": "c1", "text": "Please confirm invoice 123456 is paid.", "sensitive_spans": [], "metadata": {"template_id": "t3"}},
]


def _r(text: str, blocked: bool = False) -> AdapterResult:
    return AdapterResult(redacted_text=text, flags=AdapterFlags(blocked=blocked, detected_categories=[]), latency_ms=1.0)


def _email_only(p: dict) -> AdapterResult:
    return _r(p["text"].replace(EMAIL, "<EMAIL>"))


def test_in_scope_metrics_ignore_out_of_contract_categories() -> None:
    runs = [_email_only(p) for p in PROMPTS]
    score = score_adapter("sys", PROMPTS, runs, bootstrap_iterations=50, contract={"email"}, shared_categories={"email"})
    assert score.leak.k == 2  # all-entity prompt leak: both PII prompts still leak the key
    assert score.in_scope["prompt_leak"].k == 0 and score.in_scope["prompt_leak"].n == 1
    assert score.in_scope["span_leak"].n == 1
    assert score.span_leak_ci.k == 2 and score.span_leak_ci.n == 3
    assert score.span_leak_bootstrap is not None


def test_shared_scope_is_intersection_of_non_mock_contracts_and_holm_skips_mocks() -> None:
    runs = {
        "sys-a": [_email_only(p) for p in PROMPTS],
        "sys-b": [_r(p["text"]) for p in PROMPTS],
        "mock-leaky": [_r(p["text"]) for p in PROMPTS],
    }
    contracts = {"sys-a": {"email", "api_key"}, "sys-b": {"email", "phone"}, "mock-leaky": None}
    report = evaluate(PROMPTS, runs, bootstrap_iterations=50, contracts=contracts)
    assert report.shared_categories == ["email"]
    by_pair = {(r["adapter_a"], r["adapter_b"]): r for r in report.pairwise}
    assert by_pair[("sys-a", "sys-b")]["p_holm"] is not None
    assert by_pair[("sys-a", "sys-b")]["holm_family"] == "systems"
    assert by_pair[("sys-a", "mock-leaky")]["p_holm"] is None
    text = format_leaderboard(report)
    assert "In-scope entity contract" in text and "shared scope = email" in text and "ref" in text


def test_split_fragments_score_as_one_unit() -> None:
    card = "4929123456781234"
    text = f"The card starts with {card[:8]}. The rest of the number is {card[8:]}. Thanks."
    s1 = text.index(card[:8])
    s2 = text.index(card[8:])
    prompt = {"id": "s", "text": text, "sensitive_spans": [
        {"category": "credit_card", "start": s1, "end": s1 + 8, "value": card[:8], "canonical_value": card, "obfuscation": "split_sentences"},
        {"category": "credit_card", "start": s2, "end": s2 + 8, "value": card[8:], "canonical_value": card, "obfuscation": "split_sentences"},
    ]}
    full = score_prompt("x", prompt, _r(text))
    assert full.normalized_leak and len(full.span_matches) == 1
    half = score_prompt("x", prompt, _r(text.replace(card[8:], "[X]")))
    assert half.normalized_leak is False and half.partial_exposure is True
    assert not prompt_leaked(prompt, _r(text.replace(card[8:], "[X]")))
    none = score_prompt("x", prompt, _r("The card starts with [X]. The rest is [X]."))
    assert none.normalized_leak is False and none.partial_exposure is False


def test_evaluate_fpr_holm_summary_and_in_scope_clusters() -> None:
    """Statistics plumbing: FPR Holm over non-mock pairs only, summary counts,
    and in-scope cluster bootstrap uses only in-scope prompts' clusters."""
    from pii_guardbench.stats import holm_adjust

    pii = [
        {"id": f"e{i}", "text": f"mail {EMAIL}", "metadata": {"template_id": f"te{i % 3}"},
         "sensitive_spans": [{"category": "email", "start": 5, "end": 5 + len(EMAIL), "value": EMAIL}]}
        for i in range(6)
    ] + [
        {"id": f"k{i}", "text": f"key {KEY}", "metadata": {"template_id": f"tk{i % 2}"},
         "sensitive_spans": [{"category": "api_key", "start": 4, "end": 4 + len(KEY), "value": KEY}]}
        for i in range(4)
    ]
    ctl = [{"id": f"c{i}", "text": f"Invoice {i} is paid.", "sensitive_spans": [],
            "metadata": {"template_id": f"tc{i % 4}"}} for i in range(12)]
    prompts = pii + ctl

    def run(redact: bool, fp_every: int) -> list[AdapterResult]:
        out = []
        for j, p in enumerate(prompts):
            if p["sensitive_spans"]:
                t = p["text"].replace(EMAIL, "<EMAIL>").replace(KEY, "<KEY>") if redact else p["text"]
                out.append(_r(t))
            else:
                out.append(_r(p["text"].replace("Invoice", "<X>")) if fp_every and j % fp_every == 0 else _r(p["text"]))
        return out

    runs = {"sys-a": run(True, 1), "sys-b": run(False, 0), "sys-c": run(True, 3), "mock-leaky": run(False, 0)}
    contracts = {"sys-a": {"email"}, "sys-b": {"email"}, "sys-c": {"email", "api_key"}, "mock-leaky": None}
    report = evaluate(prompts, runs, bootstrap_iterations=50, contracts=contracts)

    fam = [r for r in report.pairwise if not (r["adapter_a"].startswith("mock") or r["adapter_b"].startswith("mock"))]
    mock = [r for r in report.pairwise if r not in fam]
    assert len(fam) == 3 and len(mock) == 3
    expected = holm_adjust([r["fpr_mcnemar"]["p_value"] for r in fam])
    assert [r["fpr_p_holm"] for r in fam] == expected
    assert all(r["fpr_p_holm"] is None and r["p_holm"] is None for r in mock)
    # Guard against mock pairs leaking into the family: with all 6 pairs the
    # adjustment differs for this data.
    with_mock = dict(zip(map(id, report.pairwise), holm_adjust([r["fpr_mcnemar"]["p_value"] for r in report.pairwise])))
    assert [with_mock[id(r)] for r in fam] != expected

    s = report.pairwise_summary
    assert s["n_pairs"] == 3
    assert s["holm_significant"] == sum(1 for r in fam if r["p_holm"] < 0.05)
    assert s["fpr_holm_significant"] == sum(1 for p in expected if p < 0.05)
    assert s["fpr_holm_significant"] >= 1  # sys-a (12/12 FP) vs sys-b (0/12)
    assert s["fpr_cluster_ci_excludes_0"] == sum(1 for r in fam if r["fpr_cluster_ci_excludes_0"])
    assert s["fpr_holm_significant_and_cluster_ci_excludes_0"] == sum(
        1 for r, p in zip(fam, expected) if p < 0.05 and r["fpr_cluster_ci_excludes_0"])

    sc = {x.adapter: x.to_dict() for x in report.scores}
    ins = sc["sys-a"]["in_scope"]
    assert ins["prompt_leak"]["n"] == 6
    assert ins["prompt_leak_cluster_bootstrap"]["n_clusters"] == 3  # te0..te2 only, not tk*
    assert sc["sys-c"]["in_scope"]["prompt_leak_cluster_bootstrap"]["n_clusters"] == 5
    assert report.bootstrap["n_pii_clusters"] == 5 and report.bootstrap["n_control_clusters"] == 4
