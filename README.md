# PII-GuardBench

PII-GuardBench is an open benchmark for PII guardrails, with a planned public leaderboard that scores products and libraries on **exact-match leak rate** with **95% confidence intervals**.

The goal is to compare guardrails such as NVIDIA NeMo Guardrails, Microsoft Presidio, LLM Guard, piiguard, and others behind one adapter interface, on a shared set of prompts that contain planted, obviously synthetic sensitive values.

**Current status: scaffold only.** This repository does not yet include the planned dataset, adapters, evaluator, or any benchmark results. No prompts have been generated.

## Scoring

A leak is recorded when a planted value appears in a system's residual output (for example, after redaction or blocking) as an exact string match on the recorded `value` for that span. The primary metric is leak rate, reported with 95% confidence intervals (Wilson score and/or bootstrap). The evaluator will also report a false-positive rate on no-PII control prompts, so a system cannot look strong simply by blocking everything.

## Hybrid dataset approach

PII-GuardBench uses a hybrid *process*, but the shipped set will be freshly generated:

- Public labeled sets are used only to shape categories and writing style. Reference sources include [NVIDIA Nemotron-PII](https://huggingface.co/datasets/nvidia/Nemotron-PII) (CC-BY-4.0), [Gretel PII masking](https://huggingface.co/datasets/gretelai/gretel-pii-masking-en-v1) (Apache-2.0), and patterns from the [Microsoft Presidio research generator](https://github.com/microsoft/presidio-research) (MIT). Those datasets are **not** redistributed in this repository.
- Other popular PII corpora, including some ai4privacy releases, have licenses that restrict redistribution or derivatives. They are **not** included here and should not be treated as redistributable building blocks for this project.
- The shipped set will be newly generated prompts with planted fake values that still pass format checks (for example, Luhn-valid test card numbers, SSNs in reserved or well-known sample ranges, synthetic MRNs, made-up diagnoses, and test-style keys such as `sk_test_`). The planned size is about 1,000 prompts, split across finance, health, and credentials, plus no-PII controls.
- Exact planted strings are stored in the schema so scoring is deterministic. A harder split may later include adversarial prompts written for this benchmark, kept separate from any prior evaluation set used for piiguard.
- A public practice split will be released; the official evaluation split will stay hidden to reduce gaming.

## Adapter layer

Each guardrail will be wrapped behind one interface so the evaluator can send the same prompt to NVIDIA NeMo Guardrails, Microsoft Presidio, LLM Guard, piiguard, and additional systems, then score the residual output.

## Evaluator

Planned outputs:

- Exact-match leak rate per system, domain, category, and difficulty
- 95% confidence intervals (Wilson and/or bootstrap)
- False-positive rate on control prompts
- Optional per-span breakdowns

Nothing in this repository reports those numbers yet.

## Repository layout

```text
schema/prompt.schema.json   JSON Schema (Draft 2020-12) for one prompt record
schema/example.jsonl        One scaffold example (not part of a scored set)
generators/                 Placeholder CLI and working format validators
data/raw/                   Notes used only to shape categories and style
data/generated/             Future generator output, before validation
data/validated/             Future prompts that passed schema and format checks
tests/                      Schema and validator tests
```

A prompt record includes `id`, `text`, `sensitive_spans` (category, character offsets, and exact `value`), `difficulty`, `source`, `domain`, and optional `metadata`.

## Status and roadmap

Current status: **scaffold, no prompts yet.**

1. Schema, validators, and repository layout (this commit)
2. Generator that emits about 1,000 validated prompts
3. Adapter interface and first guardrail wrappers
4. Evaluator with confidence intervals
5. Public practice set, hidden official set, and leaderboard
6. Paper submission (see venues below)

## Target venues

- TrustNLP at COLING 2027 — deadline January 5, 2027
- ACL 2027 via ACL ARR — deadline January 4, 2027

These dates are submission targets, not acceptances.

## Disclosure

The author also builds piiguard, one of the systems this benchmark is intended to score. To reduce the risk that the official ranking is gamed, including by the author, the official evaluation split will be held out and not published with the practice set. Leaderboard claims should be read with that conflict of interest in mind. This repository does not report any benchmark results.

## Development

```bash
python -m pip install -r requirements.txt
python -m pytest
```

The `faker` dependency is reserved for the future generator. Running `python -m generators.generate_prompts --n 1000` currently exits with `NotImplementedError`, by design.

## License

Apache License 2.0. See [LICENSE](LICENSE).
