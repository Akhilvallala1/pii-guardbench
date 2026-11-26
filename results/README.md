# results

One exploratory run of the scoring pipeline on the committed Gretel sample. **These are not
official benchmark results and not a product ranking.** Read the caveats before quoting a number.

- Data: `data/external/gretel-en-v1/sample.jsonl` (500 records, seed 20261126, from the test split of
  `gretelai/gretel-pii-masking-en-v1`, Apache-2.0). The text is **synthetic**, labels are generator labels (no human
  review), and there are **0 no-PII controls**, so false-positive rate is `n/a` for every row.
- Command (from the repo root, with the optional dependencies installed):

```bash
PIIGUARD_GUARD_DIR=/path/to/piiguard/guard \
python -m pii_guardbench.cli --dataset gretel-en-v1 \
  --adapters mock-leaky,mock-redact,presidio,nemo-guardrails,piiguard,piiguard-presidio \
  --out results/gretel-en-v1.json
```

- Files: `gretel-en-v1.json` (all numbers, per-adapter config, hardware, versions) and
  `gretel-en-v1.leaderboard.txt` (the exact CLI output below).
- Hardware: 8 vCPU Intel Xeon (cloud VM), 15.6 GB RAM, no GPU, Linux 6.12, Python 3.13.5. Latency is
  wall-clock per prompt on CPU, measured sequentially after a warm-up call, so it is only comparable across
  rows of this one run.
- Versions: presidio-analyzer/anonymizer 2.2.366, spaCy 3.8.18 with `en_core_web_lg` 3.8.0,
  nemoguardrails 0.25.0, piiguard 0.1.0 with `guard/rules.yaml` `version: 23`
  (piiguard commit `0602e62`, rules SHA-256 `4369f4e8...a4c0`).

## Adapter configurations (defaults, not tuned on this sample)

| Row | What ran |
| --- | --- |
| `mock-leaky` | Pass-through. Reference: leak rate must be 100%. |
| `mock-redact` | Demo regexes for card/SSN/API key plus 6+ digit runs. Reference only. |
| `presidio` | `AnalyzerEngine()` defaults (`en_core_web_lg`, all default recognizers, no entity filter, score threshold 0) and `AnonymizerEngine()` default replace-with-`<ENTITY>`. |
| `nemo-guardrails` | NeMo Guardrails' own Presidio-backed `mask_sensitive_data` action (offline, no LLM, no API key) with the entity list from NeMo's shipped example config (`PERSON, EMAIL_ADDRESS, PHONE_NUMBER, CREDIT_CARD, US_SSN, LOCATION`); action default threshold 0.4. NeMo's GLiNER PII rails need a NIM server and were not run. |
| `piiguard` | `Guard.from_dir(guard/)`: the committed learned regex rules only; no classifier, no LLM. |
| `piiguard-presidio` | The same rules plus piiguard's own `PresidioDetector` (its `--detector presidio` defaults: threshold 0.5, 15 entities). |

Disclosure: piiguard is written by this benchmark's author. No adapter was changed after seeing these numbers.

## Leaderboard (exact output)

```text
PII-GuardBench results
  prompts : data/external/gretel-en-v1/sample.jsonl
  counts  : 500 total, 500 PII, 0 control

Adapter            Leak rate        95% CI          FPR        FPR 95% CI  Latency ms (mean/p50/p95)
-----------------  ---------------  --------------  ---------  ----------  -------------------------
mock-leaky         1.000 (500/500)  [0.992, 1.000]  n/a (0/0)  n/a         1.50 / 1.50 / 1.50       
mock-redact        0.932 (466/500)  [0.906, 0.951]  n/a (0/0)  n/a         8.00 / 8.00 / 8.00       
presidio           0.418 (209/500)  [0.376, 0.462]  n/a (0/0)  n/a         8.99 / 8.19 / 15.44      
nemo-guardrails    0.672 (336/500)  [0.630, 0.712]  n/a (0/0)  n/a         8.69 / 7.82 / 15.38      
piiguard           0.932 (466/500)  [0.906, 0.951]  n/a (0/0)  n/a         0.29 / 0.25 / 0.59       
piiguard-presidio  0.684 (342/500)  [0.642, 0.723]  n/a (0/0)  n/a         8.75 / 7.95 / 14.87      

Category breakdown
Adapter            Category        Leak rate        95% CI        
-----------------  --------------  ---------------  --------------
mock-leaky         account_number  1.000 (33/33)    [0.896, 1.000]
mock-leaky         address         1.000 (147/147)  [0.975, 1.000]
mock-leaky         api_key         1.000 (24/24)    [0.862, 1.000]
mock-leaky         credit_card     1.000 (68/68)    [0.947, 1.000]
mock-leaky         email           1.000 (120/120)  [0.969, 1.000]
mock-leaky         mrn             1.000 (203/203)  [0.981, 1.000]
mock-leaky         name            1.000 (198/198)  [0.981, 1.000]
mock-leaky         password        1.000 (27/27)    [0.875, 1.000]
mock-leaky         phone           1.000 (88/88)    [0.958, 1.000]
mock-leaky         ssn             1.000 (141/141)  [0.973, 1.000]
mock-redact        account_number  0.576 (19/33)    [0.408, 0.728]
mock-redact        address         0.966 (142/147)  [0.923, 0.985]
mock-redact        api_key         1.000 (24/24)    [0.862, 1.000]
mock-redact        credit_card     0.044 (3/68)     [0.015, 0.122]
mock-redact        email           1.000 (120/120)  [0.969, 1.000]
mock-redact        mrn             0.759 (154/203)  [0.695, 0.812]
mock-redact        name            1.000 (198/198)  [0.981, 1.000]
mock-redact        password        1.000 (27/27)    [0.875, 1.000]
mock-redact        phone           0.625 (55/88)    [0.521, 0.719]
mock-redact        ssn             0.369 (52/141)   [0.294, 0.451]
presidio           account_number  0.000 (0/33)     [0.000, 0.104]
presidio           address         0.129 (19/147)   [0.084, 0.193]
presidio           api_key         0.833 (20/24)    [0.641, 0.933]
presidio           credit_card     0.206 (14/68)    [0.127, 0.316]
presidio           email           0.000 (0/120)    [0.000, 0.031]
presidio           mrn             0.374 (76/203)   [0.311, 0.443]
presidio           name            0.121 (24/198)   [0.083, 0.174]
presidio           password        0.926 (25/27)    [0.766, 0.979]
presidio           phone           0.125 (11/88)    [0.071, 0.210]
presidio           ssn             0.291 (41/141)   [0.222, 0.370]
nemo-guardrails    account_number  0.667 (22/33)    [0.496, 0.802]
nemo-guardrails    address         0.218 (32/147)   [0.159, 0.291]
nemo-guardrails    api_key         1.000 (24/24)    [0.862, 1.000]
nemo-guardrails    credit_card     0.338 (23/68)    [0.237, 0.457]
nemo-guardrails    email           0.000 (0/120)    [0.000, 0.031]
nemo-guardrails    mrn             0.941 (191/203)  [0.900, 0.966]
nemo-guardrails    name            0.121 (24/198)   [0.083, 0.174]
nemo-guardrails    password        0.963 (26/27)    [0.817, 0.993]
nemo-guardrails    phone           0.227 (20/88)    [0.152, 0.325]
nemo-guardrails    ssn             0.397 (56/141)   [0.320, 0.480]
piiguard           account_number  0.576 (19/33)    [0.408, 0.728]
piiguard           address         1.000 (147/147)  [0.975, 1.000]
piiguard           api_key         1.000 (24/24)    [0.862, 1.000]
piiguard           credit_card     0.265 (18/68)    [0.174, 0.380]
piiguard           email           0.000 (0/120)    [0.000, 0.031]
piiguard           mrn             1.000 (203/203)  [0.981, 1.000]
piiguard           name            1.000 (198/198)  [0.981, 1.000]
piiguard           password        1.000 (27/27)    [0.875, 1.000]
piiguard           phone           0.625 (55/88)    [0.521, 0.719]
piiguard           ssn             0.397 (56/141)   [0.320, 0.480]
piiguard-presidio  account_number  0.576 (19/33)    [0.408, 0.728]
piiguard-presidio  address         0.218 (32/147)   [0.159, 0.291]
piiguard-presidio  api_key         1.000 (24/24)    [0.862, 1.000]
piiguard-presidio  credit_card     0.265 (18/68)    [0.174, 0.380]
piiguard-presidio  email           0.000 (0/120)    [0.000, 0.031]
piiguard-presidio  mrn             0.916 (186/203)  [0.870, 0.947]
piiguard-presidio  name            0.121 (24/198)   [0.083, 0.174]
piiguard-presidio  password        0.963 (26/27)    [0.817, 0.993]
piiguard-presidio  phone           0.352 (31/88)    [0.261, 0.456]
piiguard-presidio  ssn             0.397 (56/141)   [0.320, 0.480]
```

Per-prompt leak rate means **at least one** planted value of that prompt survived. The same run counted per
planted span instead (supplementary, no CI because spans cluster inside prompts):

| Row | Leaked spans |
| --- | --- |
| mock-leaky | 1324 / 1324 (100.0%) |
| mock-redact | 995 / 1324 (75.2%) |
| presidio | 244 / 1324 (18.4%) |
| nemo-guardrails | 472 / 1324 (35.6%) |
| piiguard | 957 / 1324 (72.3%) |
| piiguard-presidio | 475 / 1324 (35.9%) |

## How to read this honestly

- **The Wilson intervals assume independent prompts.** The sample is stratified with a per-category floor, so
  rare categories are over-represented; do not read the overall rate as a population estimate for Gretel or for real traffic.
- **Exact match is lenient on partial redaction.** If a guard masks part of a multi-token value (an address, say), the
  exact value is gone and it is not counted as a leak even though a fragment remains. Leak rates are therefore lower
  bounds on residual exposure.
- **The sample excludes provider-token-shaped keys** (29 records, to keep secret scanners quiet), so API-key results
  understate how guards handle well-known key formats; `api_key` has only 24 records.
- **Only mapped categories are scored.** Values such as dates of birth, IP addresses, employee/customer IDs and user names
  are in the text but unscored, which mostly flatters guards that do not detect them.
- **Category fit drives the ranking.** piiguard's shipped rules target structured identifiers (SSN, card, phone, email and
  obfuscated variants), so it leaves names, addresses, MRNs, API keys and passwords untouched, while Presidio's defaults
  include NER for names and locations. This run measures fit to Gretel's label set, not overall guard quality.
- **Label noise exists** (for example a UK NI-style number labeled `ssn`), which can penalize a guard for
  declining to treat it as a US SSN.
