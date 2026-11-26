"""Command-line entry point: load prompts, run adapters, print a leaderboard."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path

from pii_guardbench.adapters import (
    AdapterUnavailableError,
    get_adapter,
    list_adapters,
)
from pii_guardbench.datasets import DATASET_SAMPLES, REPO_ROOT, dataset_sample_path
from pii_guardbench.environment import collect_environment
from pii_guardbench.evaluator import evaluate, format_leaderboard
from pii_guardbench.prompts import load_prompts


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pii-guardbench",
        description=(
            "Run PII-GuardBench adapters on a JSONL prompt file and print a "
            "leaderboard of exact-match leak rates with Wilson 95% CIs."
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
    return parser


def parse_adapter_names(spec: str) -> list[str]:
    names = [part.strip() for part in spec.split(",") if part.strip()]
    if not names:
        raise ValueError("Provide at least one adapter name in --adapters.")
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
        runs = {}
        for name in names:
            adapter = get_adapter(name)
            try:
                adapter.warmup()
            except AdapterUnavailableError as exc:
                skipped[adapter.name] = str(exc)
                print(f"warning: skipped adapter {adapter.name}: {exc}", file=sys.stderr)
                continue
            runs[adapter.name] = [adapter.run(prompt["text"]) for prompt in prompts]
            descriptions[adapter.name] = adapter.describe()
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
    report = evaluate(prompts, runs, prompts_path=str(display_path))
    sys.stdout.write(format_leaderboard(report))
    if args.out is not None:
        payload = report.to_dict()
        payload["environment"] = collect_environment()
        payload["adapter_config"] = descriptions
        payload["skipped_adapters"] = skipped
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        print(f"Wrote {args.out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
