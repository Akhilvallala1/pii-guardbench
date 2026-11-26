# PII-GuardBench

PII-GuardBench is an open benchmark for PII guardrails, with a planned public leaderboard that scores products and libraries on **exact-match leak rate** with **95% confidence intervals**.

The goal is to compare guardrails such as NVIDIA NeMo Guardrails, Microsoft Presidio, LLM Guard, piiguard, and others behind one adapter interface, on a shared set of prompts that contain planted, obviously synthetic sensitive values.

**Current status: scoring machinery and a real-data evaluation mode; the shipped ~1,000-prompt set is not generated yet.** Real adapters (Presidio, NeMo Guardrails' PII rail, piiguard) run offline, and a seeded 500-record sample of a public synthetic dataset (Gretel, Apache-2.0) is included for an exploratory run. No official benchmark results are reported; see [`results/`](results/README.md) for the exploratory run and its caveats.

## Scoring

A leak is recorded when any planted span `value` still appears in the guardrail's residual text as an exact, case-sensitive string that is not part of a longer word (so the name `Ann` does not match inside `Annual`). If the guardrail **blocks** the prompt, that prompt is not counted as a leak, even if the raw text still contains a planted value. Leak rate is computed over prompts that have at least one planted span, and is reported with a Wilson score 95% confidence interval (`z = 1.96`). Zero-count cases (for example, no control prompts) are reported as `n/a` rather than dividing by zero.

The evaluator also reports:

- False-positive rate on no-PII control prompts (the guard blocked the prompt or changed its text)
- Per-category leak rates with their own Wilson intervals
- Latency mean, p50, and p95 in milliseconds

A system cannot look strong simply by blocking everything: that drives leak rate down and false-positive rate up.

## Hybrid dataset approach

PII-GuardBench uses a hybrid *process*, but the shipped set will be freshly generated:

- Public labeled sets are used only to shape categories and writing style. Reference sources include [NVIDIA Nemotron-PII](https://huggingface.co/datasets/nvidia/Nemotron-PII) (CC-BY-4.0), [Gretel PII masking](https://huggingface.co/datasets/gretelai/gretel-pii-masking-en-v1) (Apache-2.0), and patterns from the [Microsoft Presidio research generator](https://github.com/microsoft/presidio-research) (MIT). Those datasets are **not** redistributed in this repository.
- Other popular PII corpora, including some ai4privacy releases, have licenses that restrict redistribution or derivatives. They are **not** included here and should not be treated as redistributable building blocks for this project.
- The shipped set will be newly generated prompts with planted fake values that still pass format checks (for example, Luhn-valid test card numbers, SSNs in reserved or well-known sample ranges, synthetic MRNs, made-up diagnoses, and test-style keys such as `sk_test_`). The planned size is about 1,000 prompts, split across finance, health, and credentials, plus no-PII controls.
- Exact planted strings are stored in the schema so scoring is deterministic. A harder split may later include adversarial prompts written for this benchmark, kept separate from any prior evaluation set used for piiguard.
- A public practice split will be released; the official evaluation split will stay hidden to reduce gaming.

## Adapter layer

Every guardrail is wrapped behind one interface: `normalize(prompt) -> request`, `call(request) -> raw`, `parse(raw) -> AdapterResult`, plus `run(prompt)` which times the three steps. `AdapterResult` holds `redacted_text`, flags (`blocked` and `detected_categories`), and `latency_ms`.

Built-in adapters:

- `mock`, `mock-leaky`, and `mock-redact`: in-memory references for tests and demos (no network)
- `presidio`: real Microsoft Presidio, default `AnalyzerEngine()` + `AnonymizerEngine()` (needs `pip install -e ".[presidio]"` and `python -m spacy download en_core_web_lg`)
- `nemo-guardrails`: NeMo Guardrails' own Presidio-backed `mask_sensitive_data` PII rail, run offline with no LLM and no API key, using the entity list from NeMo's shipped example config (`pip install -e ".[nemo]"`). NeMo's GLiNER PII rails need a NIM server and are not wired up
- `piiguard` and `piiguard-presidio`: piiguard's committed learned rules, alone or with its local Presidio detector; no LLM. Needs the `piiguard` package and `PIIGUARD_GUARD_DIR` pointing at a checkout's `guard/` folder

An adapter whose dependency is missing is skipped with a warning instead of failing the run. Each adapter's exact configuration and package versions are written to the `--out` JSON next to the hardware description.

## Evaluator and CLI

The evaluator scores one or more adapters on a JSONL file of schema-validated prompts. The CLI prints a leaderboard table and can write the same numbers as JSON.

```bash
python -m pip install -e ".[dev]"

# Smoke the default mock on the single scaffold example
python -m pii_guardbench.cli --prompts schema/example.jsonl --adapters mock

# Two mocks with different leak behavior, on a tiny test fixture (not a real corpus)
python -m pii_guardbench.cli \
  --prompts tests/fixtures/tiny_prompts.jsonl \
  --adapters mock-leaky,mock-redact \
  --out /tmp/pii-guardbench-results.json
```

### Real-data mode (exploratory)

```bash
python -m pip install -e ".[dev,data,presidio,nemo]" piiguard   # piiguard from a checkout of github.com/Akhilvallala1/piiguard
python scripts/fetch_gretel_en_v1.py        # optional: rebuild the sample from the Hugging Face parquet (seeded)
PIIGUARD_GUARD_DIR=/path/to/piiguard/guard \
python -m pii_guardbench.cli --dataset gretel-en-v1 \
  --adapters presidio,nemo-guardrails,piiguard,piiguard-presidio,mock-leaky --out results/gretel-en-v1.json
```

`--dataset gretel-en-v1` runs the committed sample in `data/external/gretel-en-v1/` (schema and offset checks only; the generated-set format rules do not apply to real data). The data is **synthetic**, its labels are generator labels, it has **no no-PII controls** (so FPR prints `n/a`), and unmapped label types are dropped. Read [`data/external/gretel-en-v1/README.md`](data/external/gretel-en-v1/README.md) and [`results/README.md`](results/README.md) before quoting any number. The sample is derived from `gretelai/gretel-pii-masking-en-v1` (Apache-2.0); see its [`NOTICE`](data/external/gretel-en-v1/NOTICE).

`pii-guardbench` is also installed as a console script, and `scripts/run_benchmark.py` is a thin wrapper around the same CLI.

`mock-leaky` passes the prompt through unchanged. `mock-redact` redacts test-style card, SSN, and API-key patterns and also over-redacts long digit runs, so a no-PII control that mentions an invoice number can show a false positive. These runs demonstrate the scoring machinery only; they are not product rankings.

## Repository layout

```text
schema/prompt.schema.json     JSON Schema (Draft 2020-12) for one prompt record
schema/example.jsonl          One scaffold example (not part of a scored set)
generators/                   Placeholder generator CLI and format validators
pii_guardbench/adapters/      Adapter interface, mocks, real adapters, and registry
pii_guardbench/datasets/      Converters from public labeled datasets (Gretel)
data/external/gretel-en-v1/   Seeded 500-record derived sample, NOTICE, conversion stats
results/                      Exploratory run output and caveats (not official results)
scripts/fetch_gretel_en_v1.py Download, convert, and sample the Gretel test split
pii_guardbench/evaluator.py   Leak rate, Wilson CIs, FPR, latency, table output
pii_guardbench/cli.py         `python -m pii_guardbench.cli`
data/raw/                     Notes used only to shape categories and style
data/generated/               Future generator output, before validation
data/validated/               Future prompts that passed schema and format checks
tests/                        Schema, evaluator, adapter, and CLI tests
tests/fixtures/               Tiny hand-written prompts for tests and CLI demos
```

A prompt record includes `id`, `text`, `sensitive_spans` (category, character offsets, and exact `value`), `difficulty`, `source`, `domain`, and optional `metadata`.

## Status and roadmap

Current status: **adapters, evaluator, CLI, and an exploratory real-data run; the shipped prompt set is not generated yet.**

1. Schema, validators, and repository layout
2. Adapter interface, mock adapters, evaluator with Wilson intervals, and CLI
3. Real adapters (Presidio, NeMo PII rail, piiguard) and an exploratory real-data run on a public synthetic dataset
4. Generator that emits about 1,000 validated prompts, including no-PII controls
5. More adapters (for example LLM Guard) and a GLiNER-based NeMo configuration
6. Public practice set, hidden official set, and public leaderboard
7. Paper submission (see venues below)

## Target venues

- TrustNLP at COLING 2027 — deadline January 5, 2027
- ACL 2027 via ACL ARR — deadline January 4, 2027

These dates are submission targets, not acceptances.

## Disclosure

The author also builds piiguard, one of the systems this benchmark is intended to score. To reduce the risk that the official ranking is gamed, including by the author, the official evaluation split will be held out and not published with the practice set. Leaderboard claims should be read with that conflict of interest in mind. This repository does not report any official benchmark results.

## Development

```bash
python -m pip install -e ".[dev]"
python -m pytest
```

`requirements.txt` remains available for a bare pytest install. The `faker` dependency is reserved for the future generator. Running `python -m generators.generate_prompts --n 1000` currently exits with `NotImplementedError`, by design.

## License

Apache License 2.0. See [LICENSE](LICENSE).
