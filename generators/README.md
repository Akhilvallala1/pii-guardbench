# generators

`generate_prompts.py` produces PII-GuardBench prompts with planted, obviously synthetic sensitive values. It is seeded and uses only the standard library.

```bash
python -m generators.generate_prompts --n 1000 --seed 0 --out data/generated/v0.jsonl
python -m generators.generate_prompts --n 30 --seed 7 --out tests/fixtures/demo_prompts_seed7.jsonl  # the committed demo
python -m generators.freeze_official --check   # regenerate the frozen pgb-v1.0 set and verify its SHA-256
```

Layers (fractions configurable with `--control-fraction` and `--hard-fraction`):

- **clean** (60%): finance, health and credentials templates with fresh fake values
- **control** (20%): no-PII prompts with lookalike hard negatives (Luhn-invalid 16-digit order numbers, invalid-area SSN shapes, invoice/tracking numbers, ICD-like room numbers, key-prefix mentions)
- **hard** (20%, `difficulty: "adversarial"`): spaced, dashed or spelled-out digits, values split across sentences, leetspeak (digit alphabet `a->4` and symbol alphabet `a->@, i->!, s->$, t->+`), base64 (single line, and wrapped with a newline every 16 characters), zero-width characters (`obfuscations.py`). Spans keep the surface `value`, the `canonical_value` and the `obfuscation`.

Every generated record must pass `validate_record` with no `validation_warnings`, or generation raises. Generating 1,000 records takes about 0.03 s on the dev VM.

Validators:

- Luhn for cards: ASCII digits with single internal spaces or hyphens only, 12-19 digits, no surrounding whitespace
- SSN format: ASCII only, no padding; area not 000, 666 or 900-999; group not 00; serial not 0000
- MRN format, ICD-10 code shape, test-key prefix (`sk_test_`, `ghp_`, `xoxb-`)
- Span offsets (`text[start:end] == value`, non-empty), no overlapping spans, NFC text
- Hard-layer consistency: `encode(canonical_value) == value`; split fragments rebuild the canonical value
- Warnings: famous public test values (e.g. `4111 1111 1111 1111`, `078-05-1120`) and real provider token shapes

**Frozen set.** `freeze_official.py` writes `data/official/pgb-v1.0/prompts.jsonl` (n=1000, seed 2026, generator `pgb-gen-v2`) and `manifest.json` with its SHA-256 (`d489a2953aad4484bd4f6efb7812ebf88b81d68372820a7eea829fdb54e2b447`) and per-layer/domain/template/category counts. Seed and size were fixed before any system was run on it. The set is public, so it can be overfit; a hidden split is future work. Changing any template or encoder changes the bytes, so the frozen set must be re-versioned (`pgb-v1.1`, ...), never silently regenerated.

**Scorer/generator coupling.** The hard-layer scorer decodes base64 and folds leetspeak on the output side, and those decoders were written by the same author as these encoders. The scorer does not import the encoders and accepts forms the generator never emits (other wrap widths, mixed leet alphabets), but an obfuscation that neither side anticipated is still missed. Public labeled sets such as Nemotron-PII and Gretel PII masking may inform category coverage and style only; they are not inputs to copy into the shipped set.
