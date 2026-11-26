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
  --out results/gretel-en-v1.json --out-dir /tmp/gretel-v2 > results/gretel-en-v1.leaderboard.txt
cp /tmp/gretel-v2/per_prompt.jsonl results/gretel-en-v1/per_prompt.jsonl
```

- Files: `gretel-en-v1.json` (all numbers incl. McNemar/Holm and rank intervals, per-adapter config, hardware,
  versions), `gretel-en-v1.leaderboard.txt` (the exact CLI output below), and `gretel-en-v1/per_prompt.jsonl`
  (one row per adapter x prompt: exact/normalized/partial flags, blocked, latency, returned text).
- Scorer: **v2** (normalized + exact leak, partial last-4, blocked-rate and residual exposure given block, lenient
  FPR, Wilson + cluster bootstrap, paired McNemar with Holm, bootstrap rank intervals; 2,000 iterations, seed 0).
  Gretel records carry no template id, so each prompt is its own bootstrap cluster here.
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

## Scorer v1 to v2 on the same outputs

v1 = commit `3405368` (whole-token exact match, `blocked=True` never a leak). v2 = this scorer. Same sample, same
adapter configs; only latency differs between the two runs (mocks no longer report hard-coded 1.5 / 8.0 ms).

| Row | v1 prompt leak | v2 exact leak | v2 normalized leak | v1 leaked spans | v2 leaked spans (normalized) | v2 blocked | v2 partial (last-4, card/SSN prompts) |
| --- | --- | --- | --- | --- | --- | --- | --- |
| mock-leaky | 500/500 (100.0%) | 500/500 (100.0%) | 500/500 (100.0%) | 1324/1324 | 1324/1324 | 0/500 (0.0%) | 0/190 (0.0%) |
| mock-redact | 466/500 (93.2%) | 466/500 (93.2%) | 466/500 (93.2%) | 995/1324 | 995/1324 | 0/500 (0.0%) | 0/190 (0.0%) |
| presidio | 209/500 (41.8%) | 209/500 (41.8%) | 209/500 (41.8%) | 244/1324 | 244/1324 | 0/500 (0.0%) | 0/190 (0.0%) |
| nemo-guardrails | 336/500 (67.2%) | 336/500 (67.2%) | 336/500 (67.2%) | 472/1324 | 473/1324 | 0/500 (0.0%) | 0/190 (0.0%) |
| piiguard | 466/500 (93.2%) | 466/500 (93.2%) | 466/500 (93.2%) | 957/1324 | 958/1324 | 0/500 (0.0%) | 0/190 (0.0%) |
| piiguard-presidio | 342/500 (68.4%) | 342/500 (68.4%) | 342/500 (68.4%) | 475/1324 | 476/1324 | 0/500 (0.0%) | 0/190 (0.0%) |

Prompt-level numbers are unchanged: none of these guards reformats values (Presidio and NeMo replace whole
entities with `<ENTITY>`, piiguard masks whole values) and none sets `blocked`, so normalized matching, partial
exposure and residual-exposure-given-block have nothing extra to catch on this sample. The +1 span for
nemo-guardrails, piiguard and piiguard-presidio is one Gretel record whose text also contains the card digits as a
price (`$367,722.81189748`); after the card itself is masked, normalized digit matching still finds the digits in
the price. That is a genuine digit-level exposure but also a matcher edge case (commas and dots are treated as digit
separators). It moves only the span-level count. The new scorer mainly matters for the generated hard layer and for
guards that reformat or partially mask.

## Leaderboard (exact output, scorer v2)

```text
PII-GuardBench results (scorer v2)
  prompts : data/external/gretel-en-v1/sample.jsonl
  counts  : 500 total, 500 PII, 0 control
  bootstrap: 2000 iterations, seed 0, 500 clusters (metadata.cluster > metadata.template_id > id)

Adapter            Exact leak       Norm leak        Wilson 95%      Cluster-boot 95%  Partial (last-4)  Blocked        Resid|block  FPR        FPR Wilson 95%  Latency ms (mean/p50/p95)
-----------------  ---------------  ---------------  --------------  ----------------  ----------------  -------------  -----------  ---------  --------------  -------------------------
mock-leaky         1.000 (500/500)  1.000 (500/500)  [0.992, 1.000]  [1.000, 1.000]    0.000 (0/190)     0.000 (0/500)  n/a (0/0)    n/a (0/0)  n/a             0.00 / 0.00 / 0.00
mock-redact        0.932 (466/500)  0.932 (466/500)  [0.906, 0.951]  [0.910, 0.952]    0.000 (0/190)     0.000 (0/500)  n/a (0/0)    n/a (0/0)  n/a             0.02 / 0.02 / 0.04
presidio           0.418 (209/500)  0.418 (209/500)  [0.376, 0.462]  [0.374, 0.460]    0.000 (0/190)     0.000 (0/500)  n/a (0/0)    n/a (0/0)  n/a             9.13 / 8.34 / 15.95
nemo-guardrails    0.672 (336/500)  0.672 (336/500)  [0.630, 0.712]  [0.632, 0.714]    0.000 (0/190)     0.000 (0/500)  n/a (0/0)    n/a (0/0)  n/a             9.10 / 8.46 / 15.42
piiguard           0.932 (466/500)  0.932 (466/500)  [0.906, 0.951]  [0.910, 0.954]    0.000 (0/190)     0.000 (0/500)  n/a (0/0)    n/a (0/0)  n/a             0.28 / 0.24 / 0.57
piiguard-presidio  0.684 (342/500)  0.684 (342/500)  [0.642, 0.723]  [0.642, 0.724]    0.000 (0/190)     0.000 (0/500)  n/a (0/0)    n/a (0/0)  n/a             8.95 / 8.08 / 15.55

Layer breakdown (clean vs adversarial obfuscation)
Adapter            Layer  Exact leak       Norm leak        Wilson 95%
-----------------  -----  ---------------  ---------------  --------------
mock-leaky         clean  1.000 (500/500)  1.000 (500/500)  [0.992, 1.000]
mock-redact        clean  0.932 (466/500)  0.932 (466/500)  [0.906, 0.951]
presidio           clean  0.418 (209/500)  0.418 (209/500)  [0.376, 0.462]
nemo-guardrails    clean  0.672 (336/500)  0.672 (336/500)  [0.630, 0.712]
piiguard           clean  0.932 (466/500)  0.932 (466/500)  [0.906, 0.951]
piiguard-presidio  clean  0.684 (342/500)  0.684 (342/500)  [0.642, 0.723]

Category breakdown (normalized leak)
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
nemo-guardrails    credit_card     0.353 (24/68)    [0.250, 0.472]
nemo-guardrails    email           0.000 (0/120)    [0.000, 0.031]
nemo-guardrails    mrn             0.941 (191/203)  [0.900, 0.966]
nemo-guardrails    name            0.121 (24/198)   [0.083, 0.174]
nemo-guardrails    password        0.963 (26/27)    [0.817, 0.993]
nemo-guardrails    phone           0.227 (20/88)    [0.152, 0.325]
nemo-guardrails    ssn             0.397 (56/141)   [0.320, 0.480]
piiguard           account_number  0.576 (19/33)    [0.408, 0.728]
piiguard           address         1.000 (147/147)  [0.975, 1.000]
piiguard           api_key         1.000 (24/24)    [0.862, 1.000]
piiguard           credit_card     0.279 (19/68)    [0.187, 0.396]
piiguard           email           0.000 (0/120)    [0.000, 0.031]
piiguard           mrn             1.000 (203/203)  [0.981, 1.000]
piiguard           name            1.000 (198/198)  [0.981, 1.000]
piiguard           password        1.000 (27/27)    [0.875, 1.000]
piiguard           phone           0.625 (55/88)    [0.521, 0.719]
piiguard           ssn             0.397 (56/141)   [0.320, 0.480]
piiguard-presidio  account_number  0.576 (19/33)    [0.408, 0.728]
piiguard-presidio  address         0.218 (32/147)   [0.159, 0.291]
piiguard-presidio  api_key         1.000 (24/24)    [0.862, 1.000]
piiguard-presidio  credit_card     0.279 (19/68)    [0.187, 0.396]
piiguard-presidio  email           0.000 (0/120)    [0.000, 0.031]
piiguard-presidio  mrn             0.916 (186/203)  [0.870, 0.947]
piiguard-presidio  name            0.121 (24/198)   [0.083, 0.174]
piiguard-presidio  password        0.963 (26/27)    [0.817, 0.993]
piiguard-presidio  phone           0.352 (31/88)    [0.261, 0.456]
piiguard-presidio  ssn             0.397 (56/141)   [0.320, 0.480]

Paired McNemar exact tests on normalized leak (Holm-adjusted)
Pair (A vs B)                         only A leaks  only B leaks  p exact    p Holm     rate(A)-rate(B) [cluster-boot 95%]
------------------------------------  ------------  ------------  ---------  ---------  ----------------------------------
mock-leaky vs mock-redact             34            0             1.164e-10  4.657e-10  +0.068 [+0.048, +0.090]
mock-leaky vs presidio                291           0             5.027e-88  7.54e-87   +0.582 [+0.540, +0.626]
mock-leaky vs nemo-guardrails         164           0             8.553e-50  1.026e-48  +0.328 [+0.286, +0.368]
mock-leaky vs piiguard                34            0             1.164e-10  4.657e-10  +0.068 [+0.046, +0.090]
mock-leaky vs piiguard-presidio       158           0             5.474e-48  6.021e-47  +0.316 [+0.276, +0.358]
mock-redact vs presidio               259           2             1.846e-74  2.584e-73  +0.514 [+0.474, +0.560]
mock-redact vs nemo-guardrails        147           17            5.165e-27  3.099e-26  +0.260 [+0.216, +0.304]
mock-redact vs piiguard               18            18            1          1          +0.000 [-0.024, +0.024]
mock-redact vs piiguard-presidio      140           16            6.544e-26  3.272e-25  +0.248 [+0.204, +0.292]
presidio vs nemo-guardrails           0             127           1.175e-38  1.175e-37  -0.254 [-0.294, -0.214]
presidio vs piiguard                  2             259           1.846e-74  2.584e-73  -0.514 [-0.558, -0.472]
presidio vs piiguard-presidio         4             137           1.165e-35  9.324e-35  -0.266 [-0.308, -0.226]
nemo-guardrails vs piiguard           5             135           6.21e-34   4.347e-33  -0.260 [-0.300, -0.220]
nemo-guardrails vs piiguard-presidio  13            19            0.3771     0.7542     -0.012 [-0.034, +0.010]
piiguard vs piiguard-presidio         124           0             9.404e-38  8.464e-37  +0.248 [+0.210, +0.286]

Bootstrap rank intervals (1 = least leakage, normalized leak)
Adapter            Rank  95% interval
-----------------  ----  ------------
mock-leaky         6.0   [6.0, 6.0]
mock-redact        4.5   [4.0, 5.0]
presidio           1.0   [1.0, 1.0]
nemo-guardrails    2.0   [2.0, 3.0]
piiguard           4.5   [4.0, 5.0]
piiguard-presidio  3.0   [2.0, 3.0]
```

## How to read this honestly

- **Wilson and the bootstrap both treat prompts as independent here** (no template ids in Gretel). The sample is stratified with a per-category floor, so
  rare categories are over-represented; do not read the overall rate as a population estimate for Gretel or for real traffic.
- **Normalized match is still lenient on partial redaction of multi-token values.** Separator-insensitive digit
  matching and last-4 partial exposure cover cards, SSNs, phones and account numbers, but if a guard masks part of an
  address or name the remaining fragment is not counted. Leak rates are lower bounds on residual exposure.
- **The sample excludes provider-token-shaped keys** (29 records, to keep secret scanners quiet), so API-key results
  understate how guards handle well-known key formats; `api_key` has only 24 records.
- **Only mapped categories are scored.** Values such as dates of birth, IP addresses, employee/customer IDs and user names
  are in the text but unscored, which mostly flatters guards that do not detect them.
- **Category fit drives the ranking.** piiguard's shipped rules target structured identifiers (SSN, card, phone, email and
  obfuscated variants), so it leaves names, addresses, MRNs, API keys and passwords untouched, while Presidio's defaults
  include NER for names and locations. This run measures fit to Gretel's label set, not overall guard quality.
- **Label noise exists** (for example a UK NI-style number labeled `ssn`), which can penalize a guard for
  declining to treat it as a US SSN.
