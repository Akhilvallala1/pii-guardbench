# PII-GuardBench

PII-GuardBench is an open benchmark for PII guardrails, with a planned public leaderboard that scores products and libraries on **residual PII exposure** in guardrail output (normalized and exact leak, partial exposure) with **95% confidence intervals and paired significance tests**.

The goal is to compare guardrails such as NVIDIA NeMo Guardrails, Microsoft Presidio, LLM Guard, piiguard, and others behind one adapter interface, on a shared set of prompts that contain planted, obviously synthetic sensitive values.

**Current status: scoring machinery (scorer v2), a seeded prompt generator with a hard obfuscation layer, and a real-data evaluation mode; no frozen ~1,000-prompt set has been committed yet.** Real adapters (Presidio, NeMo Guardrails' PII rail, piiguard) run offline, and a seeded 500-record sample of a public synthetic dataset (Gretel, Apache-2.0) is included for an exploratory run. No official benchmark results are reported; see [`results/`](results/README.md) for the exploratory run and its caveats.

## Scoring (scorer v2)

Leak is judged on the **text the guardrail returned**, over prompts with at least one planted span.

- **Normalized leak** (primary column): both sides are NFKC-normalized (fullwidth digits become ASCII), casefolded, and stripped of zero-width/format characters; spelled-out digit words are mapped to digits. For digit-type categories (cards, SSNs, phones, account numbers) separators between digits are removed, so `5289-6655-3719-3154` and `5289665537193154` both expose a planted `5289 6655 3719 3154`; the digit string must not be glued to other digits. Other categories must match on word boundaries, so the name `Ann` does not match inside `Annual`. For hard-layer spans the underlying `canonical_value` also counts.
- **Exact leak** (kept as its own column): the planted surface string appears verbatim, case-sensitive, not inside a longer word.
- **Partial exposure (last-4)**: for cards and SSNs, the last four digits are visible as a standalone group (for example `xxx-xx-3465`) while the full value is not. Reported separately, never counted as a full leak.
- **Blocking**: `blocked_rate` is the share of PII prompts the guard blocked. The `blocked` flag does **not** excuse residual text: if a blocked response still contains the value it is a leak, and `residual_given_block` reports that share among blocked PII prompts.
- **False positives (lenient)**: on a no-PII control, a false positive means the prompt was blocked or a token was removed or replaced. Whitespace, trailing newlines, edge punctuation and pure insertions (for example an appended notice) are ignored.
- **Statistics**: Wilson score 95% intervals (`z = 1.96`) on every rate; a seeded **cluster bootstrap** CI on normalized leak (clusters are `metadata.cluster`, else `metadata.template_id`, else the prompt id; `--bootstrap-iters`, `--seed`); **paired exact McNemar** tests between every pair of adapters on the same prompts with **Holm** correction and a paired cluster-bootstrap CI on the rate difference; and **bootstrap rank intervals**. Breakdowns by layer (clean vs adversarial) and category, plus latency mean/p50/p95 (always measured wall time).
- **Per-prompt dump**: `--out-dir DIR` writes `summary.json`, `leaderboard.txt`, and `per_prompt.jsonl` (one row per adapter x prompt) for paired analysis, error analysis and human review.

A system cannot look strong simply by blocking everything: that drives leak rate down and false-positive rate up, and blocked-but-echoed output still counts as leaking.

## Prompt generator

`python -m generators.generate_prompts --n 1000 --seed 0 --out data/generated/v0.jsonl` emits seeded, validated prompts (about 0.03 s for 1,000 records on the dev VM):

- **Clean layer** (default 60%): templated finance, health and credentials prompts with fresh fake values: Luhn-valid brand-shaped cards with random bodies, format-valid SSNs from area numbers 773-899 (never assigned under the pre-2011 geographic scheme; post-2011 randomized issuance means they are synthetic, not guaranteed unissued), MRNs, ICD-10-format diagnosis codes, NANP 555-01xx phones, `example.com` emails, passwords, and test-prefixed keys (`sk_test_`, `ghp_`, `xoxb-`) that are deliberately **not** provider-token-shaped.
- **No-PII controls** (default 20%) with lookalike hard negatives: Luhn-invalid 16-digit order numbers, SSN-shaped build ids with invalid area numbers, invoice and tracking numbers, ICD-like room numbers, prefix mentions without keys.
- **Hard layer** (default 20%, `difficulty: "adversarial"`): spaced digits, dashed digits, spelled-out digits, values split across sentences, leetspeak, base64, and zero-width characters. Each span stores the surface `value`, the `canonical_value` and the `obfuscation`; the validator recomputes the encoding.
- Every record passes the validators; values are unique within a set; famous public test values and provider-shaped tokens are rejected. A 30-record demo (`--n 30 --seed 7`) lives in `tests/fixtures/demo_prompts_seed7.jsonl`. **No frozen benchmark set is committed yet.**

## Validation and Unicode

Loaders normalize `text` and span values to **NFC** and remap offsets (`start' = len(NFC(text[:start]))`) before checking `text[start:end] == value`; offsets are Python code points into the NFC text. Duplicate `id`s are rejected, as are empty or overlapping spans. Luhn and SSN checks accept ASCII digits only and reject surrounding whitespace. Well-known public test values (for example `4111 1111 1111 1111`, `078-05-1120`) and provider-shaped tokens raise a `PublicTestValueWarning`.

## Hybrid dataset approach

PII-GuardBench uses a hybrid *process*, but the shipped set will be freshly generated:

- Public labeled sets are used only to shape categories and writing style. Reference sources include [NVIDIA Nemotron-PII](https://huggingface.co/datasets/nvidia/Nemotron-PII) (CC-BY-4.0), [Gretel PII masking](https://huggingface.co/datasets/gretelai/gretel-pii-masking-en-v1) (Apache-2.0), and patterns from the [Microsoft Presidio research generator](https://github.com/microsoft/presidio-research) (MIT). Those datasets are **not** redistributed in this repository.
- Other popular PII corpora, including some ai4privacy releases, have licenses that restrict redistribution or derivatives. They are **not** included here and should not be treated as redistributable building blocks for this project.
- The shipped set will be newly generated prompts from the seeded generator above (planted fake values that pass format checks). The planned size is about 1,000 prompts, split across finance, health, and credentials, plus no-PII controls and a hard obfuscation layer reported separately.
- Exact planted strings are stored in the schema so scoring is deterministic. The programmatic hard layer is generated now; a smaller LLM- or human-written hard set is still planned, kept separate from any prior evaluation set used for piiguard.
- A public practice split will be released; the official evaluation split will stay hidden to reduce gaming.

## Adapter layer

Every guardrail is wrapped behind one interface: `normalize(prompt) -> request`, `call(request) -> raw`, `parse(raw) -> AdapterResult`, plus `run(prompt)` which times the three steps. `AdapterResult` holds `redacted_text`, flags (`blocked` and `detected_categories`), and `latency_ms`.

Built-in adapters:

- `mock`, `mock-leaky`, `mock-redact`, and `mock-block`: in-memory references for tests and demos (no network). Latency is measured; `--mock-latency-ms` adds a simulated delay
- `presidio`: real Microsoft Presidio, default `AnalyzerEngine()` + `AnonymizerEngine()` (needs `pip install -e ".[presidio]"` and `python -m spacy download en_core_web_lg`)
- `nemo-guardrails`: NeMo Guardrails' own Presidio-backed `mask_sensitive_data` PII rail, run offline with no LLM and no API key, using the entity list from NeMo's shipped example config (`pip install -e ".[nemo]"`). NeMo's GLiNER PII rails need a NIM server and are not wired up
- `piiguard` and `piiguard-presidio`: piiguard's committed learned rules, alone or with its local Presidio detector; no LLM. Needs the `piiguard` package and `PIIGUARD_GUARD_DIR` pointing at a checkout's `guard/` folder

Results are keyed by a unique adapter id (the registry name, with aliases such as `nemo` resolved); naming the same adapter twice is an error. An adapter whose dependency is missing is skipped with a warning instead of failing the run. Each adapter's exact configuration and package versions are written to the `--out` JSON next to the hardware description.

## Evaluator and CLI

The evaluator scores one or more adapters on a JSONL file of schema-validated prompts. The CLI prints a leaderboard table and can write the same numbers as JSON.

```bash
python -m pip install -e ".[dev]"

# Smoke the default mock on the single scaffold example
python -m pii_guardbench.cli --prompts schema/example.jsonl --adapters mock

# Mocks on the 30-record generated demo (not a real corpus), with per-prompt dump
python -m pii_guardbench.cli \
  --prompts tests/fixtures/demo_prompts_seed7.jsonl \
  --adapters mock-leaky,mock-redact,mock-block \
  --out-dir /tmp/pgb-demo --bootstrap-iters 2000 --seed 0
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
generators/                   Seeded prompt generator, obfuscation encoders, validators
pii_guardbench/adapters/      Adapter interface, mocks, real adapters, and registry
pii_guardbench/datasets/      Converters from public labeled datasets (Gretel)
data/external/gretel-en-v1/   Seeded 500-record derived sample, NOTICE, conversion stats
results/                      Exploratory run output and caveats (not official results)
scripts/fetch_gretel_en_v1.py Download, convert, and sample the Gretel test split
pii_guardbench/evaluator.py   Leak metrics, FPR, latency, layer/category breakdowns, tables
pii_guardbench/matching.py    Exact / normalized / partial matching
pii_guardbench/stats.py       Cluster bootstrap, McNemar exact, Holm, rank intervals
pii_guardbench/cli.py         `python -m pii_guardbench.cli`
data/raw/                     Notes used only to shape categories and style
data/generated/               Future generator output, before validation
data/validated/               Future prompts that passed schema and format checks
tests/                        Schema, evaluator, adapter, and CLI tests
tests/fixtures/               Tiny prompts and the 30-record seeded generator demo
```

A prompt record includes `id`, `text`, `sensitive_spans` (category, character offsets, exact `value`, and for hard-layer spans `canonical_value` and `obfuscation`), `difficulty`, `source`, `domain`, and optional `metadata`.

## Status and roadmap

Current status: **adapters, scorer v2, generator with hard layer, and an exploratory real-data run; no frozen prompt set yet.**

1. Schema, validators, and repository layout
2. Adapter interface, mock adapters, evaluator with Wilson intervals, and CLI
3. Real adapters (Presidio, NeMo PII rail, piiguard) and an exploratory real-data run on a public synthetic dataset
4. Generator with no-PII controls and a hard obfuscation layer (done); freeze and hash-commit a ~1,000-prompt set (next)
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

`requirements.txt` remains available for a bare pytest install. The generator uses only the standard library (`random.Random(seed)`); `faker` is not required.

## License

Apache License 2.0. See [LICENSE](LICENSE).
