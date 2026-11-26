# generators

`generate_prompts.py` produces PII-GuardBench prompts with planted, obviously synthetic sensitive values. It is seeded and uses only the standard library.

```bash
python -m generators.generate_prompts --n 1000 --seed 0 --out data/generated/v0.jsonl
python -m generators.generate_prompts --n 30 --seed 7 --out tests/fixtures/demo_prompts_seed7.jsonl  # the committed demo
```

Layers (fractions configurable with `--control-fraction` and `--hard-fraction`):

- **clean** (60%): finance, health and credentials templates with fresh fake values
- **control** (20%): no-PII prompts with lookalike hard negatives (Luhn-invalid 16-digit order numbers, invalid-area SSN shapes, invoice/tracking numbers, ICD-like room numbers, key-prefix mentions)
- **hard** (20%, `difficulty: "adversarial"`): spaced, dashed or spelled-out digits, values split across sentences, leetspeak, base64, zero-width characters (`obfuscations.py`). Spans keep the surface `value`, the `canonical_value` and the `obfuscation`.

Every generated record must pass `validate_record` with no `validation_warnings`, or generation raises. Generating 1,000 records takes about 0.03 s on the dev VM.

Validators:

- Luhn for cards: ASCII digits with single internal spaces or hyphens only, 12-19 digits, no surrounding whitespace
- SSN format: ASCII only, no padding; area not 000, 666 or 900-999; group not 00; serial not 0000
- MRN format, ICD-10 code shape, test-key prefix (`sk_test_`, `ghp_`, `xoxb-`)
- Span offsets (`text[start:end] == value`, non-empty), no overlapping spans, NFC text
- Hard-layer consistency: `encode(canonical_value) == value`; split fragments rebuild the canonical value
- Warnings: famous public test values (e.g. `4111 1111 1111 1111`, `078-05-1120`) and real provider token shapes

No frozen benchmark set is committed yet. Public labeled sets such as Nemotron-PII and Gretel PII masking may inform category coverage and style only; they are not inputs to copy into the shipped set.
