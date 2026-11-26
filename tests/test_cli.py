"""CLI smoke tests with the mock adapter. No network or real guardrail APIs."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ROOT / "schema" / "example.jsonl"
FIXTURE = ROOT / "tests" / "fixtures" / "tiny_prompts.jsonl"


def _run(args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "pii_guardbench.cli", *args],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
        timeout=15,
    )


def test_cli_mock_on_example_jsonl(tmp_path: Path) -> None:
    out = tmp_path / "results.json"
    completed = _run(
        ["--prompts", str(EXAMPLE), "--adapters", "mock", "--out", str(out)]
    )
    assert completed.returncode == 0, completed.stderr
    assert "PII-GuardBench results" in completed.stdout
    assert "Leak rate" in completed.stdout
    assert "mock" in completed.stdout
    assert "Category breakdown" in completed.stdout
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["n_prompts"] == 1
    assert payload["n_pii"] == 1
    assert payload["n_controls"] == 0
    assert payload["results"][0]["adapter"] == "mock"
    # Default mock is passthrough, so the example card and SSN leak.
    assert payload["results"][0]["leak"]["k"] == 1
    assert payload["results"][0]["leak"]["n"] == 1
    assert payload["results"][0]["false_positive"]["n"] == 0
    assert payload["results"][0]["false_positive"]["rate"] is None


def test_cli_two_mocks_on_tiny_fixture(tmp_path: Path) -> None:
    out = tmp_path / "results.json"
    completed = _run(
        [
            "--prompts",
            str(FIXTURE),
            "--adapters",
            "mock-leaky,mock-redact",
            "--out",
            str(out),
        ]
    )
    assert completed.returncode == 0, completed.stderr
    stdout = completed.stdout
    assert "mock-leaky" in stdout
    assert "mock-redact" in stdout
    assert "credit_card" in stdout
    assert "ssn" in stdout
    assert "api_key" in stdout
    payload = json.loads(out.read_text(encoding="utf-8"))
    by_name = {row["adapter"]: row for row in payload["results"]}
    leaky = by_name["mock-leaky"]
    redacting = by_name["mock-redact"]
    assert leaky["leak"]["rate"] == 1.0
    assert leaky["false_positive"]["rate"] == 0.0
    assert redacting["leak"]["rate"] == 0.0
    assert redacting["false_positive"]["rate"] == 1.0
    # Latency is measured, not hard-coded.
    assert leaky["latency_ms"]["mean"] >= 0.0
    assert redacting["latency_ms"]["mean"] >= 0.0


def test_cli_rejects_unknown_adapter() -> None:
    completed = _run(["--prompts", str(EXAMPLE), "--adapters", "not-a-guard"])
    assert completed.returncode == 1
    assert "Unknown adapter" in completed.stderr


def test_cli_dataset_mode_runs_on_committed_gretel_sample(tmp_path: Path) -> None:
    out = tmp_path / "results.json"
    completed = _run(["--dataset", "gretel-en-v1", "--adapters", "mock-leaky,mock-redact", "--out", str(out)])
    assert completed.returncode == 0, completed.stderr
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["n_controls"] == 0
    by_name = {row["adapter"]: row for row in payload["results"]}
    assert by_name["mock-leaky"]["leak"]["rate"] == 1.0
    assert by_name["mock-leaky"]["false_positive"]["rate"] is None
    assert "environment" in payload and "python" in payload["environment"]


def test_cli_requires_exactly_one_prompt_source() -> None:
    assert _run(["--adapters", "mock"]).returncode == 2
    both = _run(["--prompts", str(EXAMPLE), "--dataset", "gretel-en-v1"])
    assert both.returncode == 2


def test_cli_skips_unavailable_adapter_with_warning(tmp_path: Path) -> None:
    # piiguard without PIIGUARD_GUARD_DIR is unavailable; the mock must still run.
    import os

    env = {k: v for k, v in os.environ.items() if k != "PIIGUARD_GUARD_DIR"}
    completed = subprocess.run(
        [sys.executable, "-m", "pii_guardbench.cli", "--prompts", str(EXAMPLE),
         "--adapters", "mock,piiguard", "--out", str(tmp_path / "r.json")],
        cwd=ROOT, check=False, capture_output=True, text=True, timeout=60, env=env,
    )
    assert completed.returncode == 0, completed.stderr
    assert "skipped adapter piiguard" in completed.stderr
    payload = json.loads((tmp_path / "r.json").read_text(encoding="utf-8"))
    assert "piiguard" in payload["skipped_adapters"]
    assert [r["adapter"] for r in payload["results"]] == ["mock"]


DEMO = ROOT / "tests" / "fixtures" / "demo_prompts_seed7.jsonl"


def test_cli_rejects_duplicate_adapter_ids() -> None:
    for spec in ("mock,mock", "nemo,nemo-guardrails", "mock-leaky,MOCK-LEAKY"):
        completed = _run(["--prompts", str(EXAMPLE), "--adapters", spec])
        assert completed.returncode == 1, spec
        assert "Duplicate adapter id" in completed.stderr


def test_cli_out_dir_writes_per_prompt_dump_and_stats_sections(tmp_path: Path) -> None:
    out_dir = tmp_path / "run"
    completed = _run([
        "--prompts", str(DEMO), "--adapters", "mock-leaky,mock-redact,mock-block",
        "--out-dir", str(out_dir), "--bootstrap-iters", "200", "--seed", "3",
        "--mock-latency-ms", "1",
    ])
    assert completed.returncode == 0, completed.stderr
    out = completed.stdout
    for section in ("Exact leak", "Norm leak", "Cluster 95%", "Wilson (iid, secondary)", "Ctl blocked", "cluster CI excl. 0", "Partial", "Span leak", "In-scope entity contract", "Resid|block",
                    "Layer breakdown", "Paired McNemar", "p Holm", "Bootstrap rank intervals"):
        assert section in out, section
    rows = [json.loads(line) for line in (out_dir / "per_prompt.jsonl").read_text(encoding="utf-8").splitlines()]
    assert len(rows) == 3 * 30
    assert {r["adapter"] for r in rows} == {"mock-leaky", "mock-redact", "mock-block"}
    assert {"prompt_id", "exact_leak", "normalized_leak", "partial_exposure", "blocked",
            "false_positive", "latency_ms", "layer", "cluster", "detected_types", "contract_false_positive", "template_id"} <= set(rows[0])
    summary = json.loads((out_dir / "summary.json").read_text(encoding="utf-8"))
    assert summary["bootstrap"]["iterations"] == 200 and summary["bootstrap"]["seed"] == 3
    assert len(summary["pairwise_mcnemar_holm"]) == 3
    # Simulated 1 ms delay is measured, so mean latency is at least 1 ms.
    assert all(r["latency_ms"]["mean"] >= 1.0 for r in summary["results"])
    assert (out_dir / "leaderboard.txt").read_text(encoding="utf-8") == out


def test_cli_rejects_bad_bootstrap_iterations() -> None:
    completed = _run(["--prompts", str(EXAMPLE), "--adapters", "mock", "--bootstrap-iters", "0"])
    assert completed.returncode == 1


def test_cli_layer_filter_scores_one_layer(tmp_path: Path) -> None:
    out = tmp_path / "hard.json"
    completed = _run(["--prompts", str(DEMO), "--adapters", "mock-leaky", "--layer", "hard",
                      "--bootstrap-iters", "50", "--out", str(out)])
    assert completed.returncode == 0, completed.stderr
    payload = json.loads(out.read_text(encoding="utf-8"))
    demo = [json.loads(line) for line in DEMO.read_text(encoding="utf-8").splitlines()]
    n_hard = sum(1 for r in demo if r["metadata"]["layer"] == "hard")
    assert payload["layer"] == "hard"
    assert payload["results"][0]["leak"]["n"] + payload["results"][0]["false_positive"]["n"] == n_hard
