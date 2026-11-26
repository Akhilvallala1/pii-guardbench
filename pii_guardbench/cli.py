"""Command-line entry point: load prompts, run adapters, print a leaderboard."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path

from pii_guardbench.adapters import (
    AdapterUnavailableError,
    MockAdapter,
    adapter_id,
    get_adapter,
    list_adapters,
)
from pii_guardbench.datasets import DATASET_SAMPLES, REPO_ROOT, dataset_sample_path
from pii_guardbench.environment import collect_environment
from pii_guardbench.evaluator import (
    DEFAULT_BOOTSTRAP_ITERATIONS,
    DEFAULT_SEED,
    evaluate,
    format_leaderboard,
)
from pii_guardbench.prompts import load_prompts


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pii-guardbench",
        description=(
            "Run PII-GuardBench adapters on a JSONL prompt file and print a "
            "leaderboard: exact and normalized leak, partial exposure, blocking, "
            "lenient FPR, Wilson and cluster-bootstrap CIs, paired McNemar/Holm "
            "tests, and bootstrap rank intervals."
        ),
    )
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument(
        "--prompts",
        type=Path,
        help="Path to a JSONL file of prompt records (strict category format checks).",
    )
    source.add_argument(
        "--dataset",
        choices=sorted(DATASET_SAMPLES),
        help=(
            "Run on the committed sample of a public span-labeled dataset "
            "(schema and offset checks only; real data does not follow the "
            "generated-set format rules)."
        ),
    )
    parser.add_argument(
        "--adapters",
        default="mock",
        help=(
            "Comma-separated adapter names (default: mock). "
            f"Known names: {', '.join(list_adapters()) or 'none registered'}."
        ),
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=None,
        help="Optional path to write the results (with environment details) as JSON.",
    )
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=None,
        help=(
            "Optional directory: writes summary.json, leaderboard.txt, and "
            "per_prompt.jsonl (one row per adapter x prompt)."
        ),
    )
    parser.add_argument(
        "--bootstrap-iters",
        type=int,
        default=DEFAULT_BOOTSTRAP_ITERATIONS,
        help=f"Cluster-bootstrap iterations (default {DEFAULT_BOOTSTRAP_ITERATIONS}).",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=DEFAULT_SEED,
        help=f"Bootstrap seed (default {DEFAULT_SEED}).",
    )
    parser.add_argument(
        "--layer",
        choices=("all", "clean", "control", "hard"),
        default="all",
        help=(
            "Score only one layer of a generated set (metadata.layer; records without it "
            "count as clean, or control when they have no spans). Default: all."
        ),
    )
    parser.add_argument(
        "--mock-latency-ms",
        type=float,
        default=None,
        help="Simulated delay for mock adapters; reported latency is still measured.",
    )
    return parser


def record_layer(prompt: dict) -> str:
    layer = (prompt.get("metadata") or {}).get("layer")
    if layer in ("clean", "control", "hard"):
        return layer
    if prompt.get("difficulty") == "adversarial":
        return "hard"
    return "clean" if prompt.get("sensitive_spans") else "control"


def parse_adapter_names(spec: str) -> list[str]:
    names = [part.strip() for part in spec.split(",") if part.strip()]
    if not names:
        raise ValueError("Provide at least one adapter name in --adapters.")
    seen: dict[str, str] = {}
    for name in names:
        uid = adapter_id(name)
        if uid in seen:
            raise ValueError(
                f"Duplicate adapter id {uid!r} (from {seen[uid]!r} and {name!r}); "
                "each adapter may appear once per run."
            )
        seen[uid] = name
    return names


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    skipped: dict[str, str] = {}
    descriptions: dict[str, dict] = {}
    try:
        names = parse_adapter_names(args.adapters)
        if args.dataset:
            prompts_path = dataset_sample_path(args.dataset)
            prompts = load_prompts(prompts_path, check_formats=False)
        else:
            prompts_path = args.prompts
            prompts = load_prompts(prompts_path)
        if args.layer != "all":
            prompts = [p for p in prompts if record_layer(p) == args.layer]
            if not prompts:
                raise ValueError(f"No prompts in layer {args.layer!r}.")
        if args.bootstrap_iters < 1:
            raise ValueError("--bootstrap-iters must be >= 1")
        runs = {}
        contracts: dict[str, object] = {}
        for name in names:
            uid = adapter_id(name)
            adapter = get_adapter(name)
            if args.mock_latency_ms is not None and isinstance(adapter, MockAdapter):
                if args.mock_latency_ms < 0:
                    raise ValueError("--mock-latency-ms must be >= 0")
                adapter.latency_ms = args.mock_latency_ms
            try:
                adapter.warmup()
            except AdapterUnavailableError as exc:
                skipped[uid] = str(exc)
                print(f"warning: skipped adapter {uid}: {exc}", file=sys.stderr)
                continue
            runs[uid] = [adapter.run(prompt["text"]) for prompt in prompts]
            descriptions[uid] = adapter.describe()
            contracts[uid] = adapter.entity_contract()
        if not runs:
            raise ValueError("No adapter could run; see warnings above.")
    except (ValueError, KeyError, NotImplementedError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    display_path = prompts_path
    try:
        display_path = Path(prompts_path).resolve().relative_to(REPO_ROOT)
    except ValueError:
        pass
    report = evaluate(
        prompts,
        runs,
        prompts_path=str(display_path),
        bootstrap_iterations=args.bootstrap_iters,
        seed=args.seed,
        contracts=contracts,
    )
    leaderboard = format_leaderboard(report)
    sys.stdout.write(leaderboard)
    if args.out is not None or args.out_dir is not None:
        payload = report.to_dict()
        payload["environment"] = collect_environment()
        payload["adapter_config"] = descriptions
        payload["skipped_adapters"] = skipped
        payload["layer"] = args.layer
        text = json.dumps(payload, indent=2) + "\n"
        if args.out is not None:
            args.out.parent.mkdir(parents=True, exist_ok=True)
            args.out.write_text(text, encoding="utf-8")
            print(f"Wrote {args.out}", file=sys.stderr)
        if args.out_dir is not None:
            args.out_dir.mkdir(parents=True, exist_ok=True)
            (args.out_dir / "summary.json").write_text(text, encoding="utf-8")
            (args.out_dir / "leaderboard.txt").write_text(leaderboard, encoding="utf-8")
            with (args.out_dir / "per_prompt.jsonl").open("w", encoding="utf-8") as handle:
                for outcome in report.iter_outcomes():
                    handle.write(json.dumps(outcome.to_dict(), ensure_ascii=False) + "\n")
            print(f"Wrote {args.out_dir}/{{summary.json,leaderboard.txt,per_prompt.jsonl}}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
