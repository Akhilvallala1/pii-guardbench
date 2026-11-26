# data/external/gretel-en-v1

A small, seeded, **derived sample** of the public dataset
[`gretelai/gretel-pii-masking-en-v1`](https://huggingface.co/datasets/gretelai/gretel-pii-masking-en-v1)
(Apache-2.0), converted into the PII-GuardBench prompt schema for a real-data
evaluation mode. See [`NOTICE`](NOTICE) for attribution and the statement of changes.

| File | What it is |
| --- | --- |
| `sample.jsonl` | 500 converted records (the committed sample) |
| `conversion_stats.json` | Revision, parquet SHA-256, seed, conversion counters, sample composition |
| `NOTICE` | Source, license, attribution, and changes |
| `_cache/` | Full download. **Git-ignored; never committed.** |

## What this data is, and is not

- **Synthetic, not real user text.** Every document was LLM-generated. It is
  labeled and publicly available, but it is not a sample of what real users
  type, and its values are not real people's data.
- **Labels are generator labels, not human annotations.** Nobody verified each
  label. Some are wrong for their name: the sample contains a UK National
  Insurance-style number labeled `ssn`. There are no human leak labels either:
  "leak" here means an exact string match on a generator-listed value.
- **No character offsets in the source.** Gretel publishes entity strings and
  types only. Offsets here are derived by finding every whole-token,
  case-sensitive occurrence of each entity string, and each span is verified
  with `text[start:end] == value` (0 failures; failures would be dropped and counted).
- **No no-PII controls.** Every sampled record contains at least one planted
  value, so false-positive rate is `n/a` on this data.

## Reproduce

```bash
python -m pip install -e ".[data]"
python scripts/fetch_gretel_en_v1.py          # seed 20261126, n=500, category floor 25
```

The script pins the dataset revision it downloaded (see `conversion_stats.json`),
caches the parquet under `_cache/`, converts every test record, then takes a seeded
sample: first a floor of 25 records per mapped category (so rare categories such as
`api_key` and `password` have usable sizes), then the remainder proportionally by
Gretel domain. The floor means **the sample over-represents rare categories relative
to the full split**; it is a benchmark sample, not a population estimate.

## Conversion (test split, 5,000 records)

- 4,420 records kept; 578 dropped because none of their entities map to a benchmark
  category (they are *not* treated as no-PII controls, since they do contain PII of
  unmapped types); 2 dropped because no mapped value could be located in the text.
- Mapped categories: `credit_card`, `ssn`, `mrn`, `api_key`, `password`, `email`,
  `phone`, `name` (name + first_name + last_name), `address` (address + street_address),
  `account_number`.
- Dropped (unmapped) labels include `date_of_birth`, `ipv4`/`ipv6`, `customer_id`,
  `employee_id`, `user_name`, `bank_routing_number`, `tax_id`, `national_id`, `cvv`, `pin`,
  `license_plate`, `date`, `company_name`, `city`, `url` and others. Their values are still
  in the text but are **not scored**, so a guardrail that redacts them gets no credit and
  one that leaves them gets no penalty.
- **Secret-scanner exclusion.** Before sampling, 29 of the 4,420 converted records were excluded because their text
  contains a string shaped like a real provider token (SendGrid, Slack, GitHub, Google, AWS key IDs, and similar; see
  `SECRET_LIKE_PATTERNS` in `pii_guardbench/datasets/gretel.py`). The values are synthetic, but GitHub push protection
  rejected a push containing one. This depends only on the text, not on any guardrail output, but it does remove many
  of the most realistic-looking API keys, so **`api_key` coverage in the sample is thinner (24 records, below the 25
  floor) and skews toward less recognizable key formats.**
- Values shorter than 3 characters are skipped; 15 entities not found in their text were
  dropped and counted.
- `difficulty` is a constant `medium` (the dataset has no difficulty signal). `domain` is
  mapped from Gretel's domain where it clearly fits finance, health, or credentials,
  otherwise `none` (the original is kept in `metadata.gretel_domain`).
- Source is `adapted`. Strict category format checks (Luhn, SSN area rules, key prefixes)
  are **not** applied to this data; only the schema and offsets are.
