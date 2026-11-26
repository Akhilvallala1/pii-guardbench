"""Command-line entry point: load prompts, run adapters, print a leaderboard."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path

from pii_guardbench.adapters import get_adapter, list_adapters
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
    parser.add_argument(
        "--prompts",
        required=True,
        type=Path,
        help="Path to a JSONL file of validated prompt records.",
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
        help="Optional path to write the results as JSON.",
    )
    return parser


def parse_adapter_names(spec: str) -> list[str]:
    names = [part.strip() for part in spec.split(",") if part.strip()]
    if not names:
        raise ValueError("Provide at least one adapter name in --adapters.")
    return names


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        names = parse_adapter_names(args.adapters)
        prompts = load_prompts(args.prompts)
        runs = {}
        for name in names:
            adapter = get_adapter(name)
            runs[adapter.name] = [adapter.run(prompt["text"]) for prompt in prompts]
    except (ValueError, KeyError, NotImplementedError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    report = evaluate(prompts, runs, prompts_path=str(args.prompts))
    sys.stdout.write(format_leaderboard(report))
    if args.out is not None:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(report.to_dict(), indent=2) + "\n", encoding="utf-8")
        print(f"Wrote {args.out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
