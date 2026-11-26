"""Layers, control clustering, contract-aware FPR, detected types, pairwise
cluster summary, template families, and figures. Mocks / fake raw outputs only."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

from pii_guardbench.adapters.base import AdapterFlags, AdapterResult
from pii_guardbench.evaluator import (
    contract_false_positive,
    evaluate,
    family_of,
    format_leaderboard,
    layer_of,
    score_adapter,
)


def _r(text: str, blocked: bool = False, types=()) -> AdapterResult:
    return AdapterResult(text, AdapterFlags(blocked=blocked, detected_categories=list(types)))


def _pii(i: int, tpl: str, domain: str = "finance") -> dict:
    v = f"790-18-{3400 + i:04d}"
    return {"id": f"p{i}", "text": f"ssn {v} ok", "domain": domain, "difficulty": "easy",
            "sensitive_spans": [{"category": "ssn", "start": 4, "end": 15, "value": v}],
            "metadata": {"layer": "clean", "template_id": tpl}}


def _ctl(i: int, tpl: str) -> dict:
    return {"id": f"c{i}", "text": f"Ship order {1000 + i} on Tuesday please.", "domain": "none",
            "difficulty": "hard", "sensitive_spans": [], "metadata": {"layer": "control", "template_id": tpl}}


# ---- layer_of -------------------------------------------------------------

def test_layer_of_honors_metadata_layer() -> None:
    assert layer_of({"metadata": {"layer": "control"}, "difficulty": "hard", "sensitive_spans": []}) == "control"
    assert layer_of({"metadata": {"layer": "hard"}, "sensitive_spans": [{}]}) == "hard"
    assert layer_of({"metadata": {"layer": "clean"}, "difficulty": "adversarial", "sensitive_spans": [{}]}) == "clean"


def test_layer_of_fallbacks() -> None:
    assert layer_of({"difficulty": "adversarial", "sensitive_spans": [{}]}) == "hard"
    assert layer_of({"difficulty": "hard", "sensitive_spans": []}) == "control"
    assert layer_of({"difficulty": "easy", "sensitive_spans": [{}]}) == "clean"
    assert layer_of({"metadata": {"layer": "weird"}, "sensitive_spans": [{}]}) == "clean"


def test_outcome_layer_for_controls_is_control() -> None:
    p = [_ctl(i, "ctl-01") for i in range(3)]
    s = score_adapter("x", p, [_r(q["text"]) for q in p], bootstrap_iterations=20)
    assert {o.layer for o in s.outcomes} == {"control"}
    assert {o.cluster for o in s.outcomes} == {"ctl-01"}


# ---- control clusters and blocked rate ------------------------------------

def test_control_cluster_bootstrap_and_control_blocked_rate() -> None:
    prompts = [_ctl(i, f"ctl-0{i % 4}") for i in range(20)]
    # block every control of template ctl-00 (5 of 20), leave the rest intact
    runs = [_r("I'm sorry", blocked=True) if p["metadata"]["template_id"] == "ctl-00" else _r(p["text"]) for p in prompts]
    s = score_adapter("x", prompts, runs, bootstrap_iterations=300, seed=1)
    assert s.false_positive.k == 5 and s.false_positive.n == 20
    assert s.control_blocked.k == 5 and s.control_blocked.n == 20
    assert s.blocked.n == 0  # blocked_rate is over PII prompts only
    fb = s.false_positive_bootstrap
    assert fb is not None and fb.n_clusters == 4 and fb.estimate == 0.25
    assert fb.low <= 0.25 <= fb.high and fb.high > fb.low
    assert s.fp_blocks == 5
    d = s.to_dict()
    assert d["control_blocked_rate"]["k"] == 5 and d["false_positive_cluster_bootstrap"]["n_clusters"] == 4
    rep = evaluate(prompts, {"x": runs}, bootstrap_iterations=50)
    assert rep.bootstrap["n_control_clusters"] == 4 and rep.bootstrap["n_pii_clusters"] == 0
    text = format_leaderboard(rep)
    assert "PII clusters 0, control clusters 4" in text and "Ctl blocked" in text


# ---- contract-aware FPR ----------------------------------------------------

CMAP = {"US_SSN": "ssn", "DATE_TIME": None, "LOCATION": "address"}
CMAP = {k: v for k, v in CMAP.items() if v}


def test_contract_fp_helper() -> None:
    assert contract_false_positive(True, False, ["US_SSN"], {"ssn"}, CMAP) is True
    assert contract_false_positive(True, False, ["DATE_TIME"], {"ssn"}, CMAP) is False
    assert contract_false_positive(True, False, ["LOCATION"], {"ssn"}, CMAP) is False  # address not in contract
    assert contract_false_positive(True, True, [], {"ssn"}, CMAP) is True  # blocks count
    assert contract_false_positive(False, False, ["US_SSN"], {"ssn"}, CMAP) is False
    assert contract_false_positive(True, False, ["US_SSN"], None, None) is None
    assert contract_false_positive(True, False, ["x"], {"ssn"}, {"x": "*"}) is True


def test_contract_aware_fpr_in_score_and_mock_na() -> None:
    prompts = [_ctl(i, f"ctl-0{i % 2}") for i in range(4)]
    runs = [
        _r("Ship order <US_SSN> on Tuesday please.", types=["US_SSN"]),
        _r("Ship order 1001 on <DATE_TIME> please.", types=["DATE_TIME"]),
        _r("I'm sorry", blocked=True),
        _r(prompts[3]["text"]),
    ]
    s = score_adapter("presidio-like", prompts, runs, bootstrap_iterations=50,
                      contract={"ssn", "name"}, category_map=CMAP)
    assert s.false_positive.k == 3
    assert s.contract_false_positive.k == 2 and s.contract_false_positive.n == 4
    assert s.contract_false_positive_bootstrap.n_clusters == 2
    assert s.fp_blocks == 1
    assert s.fp_by_type == {"DATE_TIME": 1, "US_SSN": 1}
    assert s.fp_by_template["ctl-00"]["raw"].k == 2 and s.fp_by_template["ctl-00"]["contract"].k == 2
    assert s.fp_by_template["ctl-01"]["raw"].k == 1 and s.fp_by_template["ctl-01"]["contract"].k == 0
    assert s.fp_by_template["ctl-00"]["blocked"] == 1
    rows = [o.to_dict() for o in s.outcomes]
    assert rows[0]["detected_types"] == ["US_SSN"] and rows[1]["contract_false_positive"] is False
    mock = score_adapter("mock-redact", prompts, runs, bootstrap_iterations=50)
    assert mock.contract_false_positive is None and mock.to_dict()["contract_false_positive"] is None
    rep = evaluate(prompts, {"presidio-like": runs, "mock-redact": runs}, bootstrap_iterations=50,
                   contracts={"presidio-like": {"ssn"}, "mock-redact": None},
                   category_maps={"presidio-like": CMAP})
    text = format_leaderboard(rep)
    assert "Control false positives by redacted entity type" in text
    assert "Control false positives by template" in text and "Contract FPR" in text


# ---- detected types from real adapter parse paths (fake raw outputs) -------

def test_presidio_records_anonymizer_item_types() -> None:
    from pii_guardbench.adapters.presidio import PRESIDIO_TO_CATEGORY, PresidioAdapter

    class F:  # analyzer result
        def __init__(self, t): self.entity_type = t

    class Analyzer:
        def analyze(self, **kw): return [F("DATE_TIME"), F("US_SSN"), F("PERSON")]

    class Anon:
        def anonymize(self, text, analyzer_results):
            class R: pass
            r = R(); r.text = "<US_SSN> on <DATE_TIME>"; r.items = [F("US_SSN"), F("DATE_TIME")]
            return r

    a = PresidioAdapter(analyzer=Analyzer(), anonymizer=Anon())
    res = a.parse(a.call(a.normalize("x")))
    assert res.flags.detected_categories == ["DATE_TIME", "US_SSN"]
    assert a.category_map() == PRESIDIO_TO_CATEGORY and "DATE_TIME" not in a.category_map()


def test_nemo_parses_placeholders() -> None:
    from pii_guardbench.adapters.nemo import NemoGuardrailsAdapter

    a = NemoGuardrailsAdapter()
    res = a.parse({"text": "room <US_DRIVER_LICENSE>, ssn <US_SSN>", "original": "room E11, ssn 1", "blocked": False})
    assert res.flags.detected_categories == ["US_DRIVER_LICENSE", "US_SSN"]
    assert a.category_map()["US_SSN"] == "ssn" and "US_DRIVER_LICENSE" not in a.category_map()


def test_piiguard_and_union_record_types() -> None:
    from pii_guardbench.adapters.piiguard import PiiguardAdapter, PiiguardUnionPresidioAdapter

    class Fd:
        def __init__(self, t, v, s): self.type, self.value, self.start, self.end = t, v, s, s + len(v)

    class Red:
        def __init__(self, text, f): self.text, self.findings = text, f

    class Guard:
        def redact(self, text):
            return Red(text.replace("931-48-3943", "[PII_SSN_1]"), [Fd("ssn", "931-48-3943", text.find("931-48-3943"))])

    g = PiiguardAdapter()
    g._guard = Guard()
    res = g.parse(g.call({"text": "id 931-48-3943 today"}))
    assert res.flags.detected_categories == ["ssn"] and g.category_map()["ssn"] == "ssn"
    assert "dob_spaced" not in g.category_map() and g.category_map()["us_ssn"] == "ssn"

    class AR:
        def __init__(self, t, s, e): self.entity_type, self.start, self.end = t, s, e

    class An:
        def analyze(self, text, language, score_threshold): return [AR("DATE_TIME", 15, 20)]

    u = PiiguardUnionPresidioAdapter(analyzer=An())
    u._guard = Guard()
    res = u.parse(u.call({"text": "id 931-48-3943 today"}))
    assert res.redacted_text == "id <PII> <PII>"
    assert res.flags.detected_categories == ["piiguard:ssn", "presidio:DATE_TIME"]
    cm = u.category_map()
    assert cm["piiguard:ssn"] == "ssn" and "presidio:DATE_TIME" not in cm


def test_gliner_records_labels() -> None:
    from pii_guardbench.adapters import gliner_pii

    class Model:
        def predict_entities(self, text, labels, threshold):
            i = text.find("Ann")
            return [{"start": i, "end": i + 3, "label": "person"}]

    a = gliner_pii.GlinerPiiAdapter(model_name="fake")
    gliner_pii._SHARED["fake"] = Model()
    try:
        res = a.parse(a.call({"text": "hi Ann ok"}))
    finally:
        gliner_pii._SHARED.pop("fake")
    assert res.redacted_text == "hi <PERSON> ok" and res.flags.detected_categories == ["person"]
    assert a.category_map()["person"] == "name"


def test_llm_guard_parses_redacted_placeholders() -> None:
    from pii_guardbench.adapters.llm_guard import LlmGuardAdapter

    raw = {"text": "call [REDACTED_PHONE_NUMBER_1] or [REDACTED_US_SSN_RE_2] [REDACTED_PHONE_NUMBER_3]",
           "is_valid": False, "risk_score": 1.0, "original": "call x"}
    for a in (LlmGuardAdapter(), LlmGuardAdapter(block_on_invalid=True)):
        res = a.parse(raw)
        assert res.flags.detected_categories == ["PHONE_NUMBER", "US_SSN_RE"]
        assert a.category_map()["US_SSN_RE"] == "ssn"


# ---- pairwise cluster summary ----------------------------------------------

def test_pairwise_cluster_ci_summary_counts() -> None:
    prompts = [_pii(i, f"t{i % 6}") for i in range(60)] + [_ctl(i, f"ctl-0{i % 3}") for i in range(9)]
    leak = [_r(p["text"]) for p in prompts]
    safe = [_r("ssn [X] ok") if p["sensitive_spans"] else _r(p["text"]) for p in prompts]
    # "half": leaks only in templates t0..t2 -> concentrated in clusters
    half = [_r(p["text"]) if p["metadata"]["template_id"] in ("t0",) else
            (_r("ssn [X] ok") if p["sensitive_spans"] else _r(p["text"])) for p in prompts]
    rep = evaluate(prompts, {"a": leak, "b": safe, "c": half, "mock-leaky": leak}, bootstrap_iterations=300)
    ps = rep.pairwise_summary
    assert ps["n_pairs"] == 3
    by = {(r["adapter_a"], r["adapter_b"]): r for r in rep.pairwise}
    assert by[("a", "b")]["cluster_ci_excludes_0"] is True
    assert by[("b", "c")]["p_holm"] < 0.05  # 10 discordant pairs, all one way
    assert by[("b", "c")]["cluster_ci_excludes_0"] is False  # one template drives it
    assert ps["holm_significant"] == 3
    assert ps["holm_significant_and_cluster_ci_excludes_0"] == 2
    assert ps["cluster_ci_excludes_0"] == 2
    assert ps["fpr_cluster_ci_excludes_0"] == 0
    assert by[("a", "mock-leaky")]["p_holm"] is None
    assert "cluster CI excl. 0" in format_leaderboard(rep)
    assert rep.to_dict()["pairwise_summary"] == ps


# ---- by_family / by_template -----------------------------------------------

def test_family_of() -> None:
    assert family_of({"metadata": {"template_id": "cred-02"}, "domain": "finance"}) == "credentials"
    assert family_of({"metadata": {"template_id": "hea-01"}}) == "health"
    assert family_of({"metadata": {"template_id": "hard-05"}, "domain": "health"}) == "health"
    assert family_of({"domain": "none"}) is None


def test_by_family_and_by_template() -> None:
    prompts = [_pii(i, t, d) for i, (t, d) in enumerate(
        [("cred-01", "credentials")] * 4 + [("fin-01", "finance")] * 3 + [("hea-01", "health")] * 2)]
    runs = [_r(p["text"]) if p["metadata"]["template_id"] == "cred-01" else _r("ssn [X] ok") for p in prompts]
    s = score_adapter("x", prompts, runs, bootstrap_iterations=20)
    assert s.by_family["credentials"].k == 4 and s.by_family["credentials"].n == 4
    assert s.by_family["finance"].k == 0 and s.by_family["health"].n == 2
    assert s.by_template["fin-01"].n == 3 and s.by_template["cred-01"].low is not None
    d = s.to_dict()
    assert set(d["by_family"]) == {"credentials", "finance", "health"} and "cred-01" in d["by_template"]
    text = format_leaderboard(evaluate(prompts, {"x": runs}, bootstrap_iterations=20))
    assert "Leak by template family" in text and "Leak by template (" in text


# ---- figures -----------------------------------------------------------------

def _result_row(name, leak, fpr, block, cfpr=None):
    ci = lambda r: {"rate": r, "low": max(0, r - 0.1), "high": min(1, r + 0.1), "k": 1, "n": 10,
                    "estimate": r}
    return {"adapter": name, "leak": ci(leak), "leak_cluster_bootstrap": ci(leak),
            "false_positive": ci(fpr), "false_positive_cluster_bootstrap": ci(fpr),
            "contract_false_positive": ci(cfpr) if cfpr is not None else None,
            "contract_false_positive_cluster_bootstrap": ci(cfpr) if cfpr is not None else None,
            "blocked_rate": ci(block), "in_scope": {"prompt_leak": ci(leak / 2)}}


@pytest.mark.skipif(importlib.util.find_spec("matplotlib") is None, reason="matplotlib not installed (figures extra)")
def test_make_figures_on_synthetic_results(tmp_path: Path) -> None:
    import importlib.util as iu

    spec = iu.spec_from_file_location("make_figures", Path(__file__).resolve().parent.parent / "scripts" / "make_figures.py")
    mf = iu.module_from_spec(spec)
    spec.loader.exec_module(mf)
    rows = [_result_row("sys-a", 0.2, 0.7, 0.0, 0.1), _result_row("sys-b", 0.5, 0.1, 0.8, 0.1),
            _result_row("sys-c", 0.6, 0.8, 0.0, 0.5), _result_row("mock-redact", 0.0, 0.0, 0.0)]
    for layer in ("clean", "hard", "control"):
        (tmp_path / f"pgb-v1.0-{layer}.json").write_text(json.dumps({"results": rows}), encoding="utf-8")
    series = mf.build_series(tmp_path)
    assert {p["name"] for p in series["a"]} == {"sys-a", "sys-b", "sys-c"}  # mocks excluded
    assert mf.pareto_front([(0.7, 0.2), (0.1, 0.5), (0.8, 0.6)]) == [(0.1, 0.5), (0.7, 0.2)]
    out = tmp_path / "figs"
    paths = mf.make_figures(tmp_path, out)
    assert len(paths) == 4 and all(p.exists() and p.stat().st_size > 1000 for p in paths)
    first = [p.read_bytes() for p in paths]
    mf.make_figures(tmp_path, out)
    assert [p.read_bytes() for p in paths] == first  # deterministic
