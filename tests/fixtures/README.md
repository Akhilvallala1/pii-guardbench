# tests/fixtures

Small prompt files used by unit tests and CLI demos. Not part of any scored official set.

- `tiny_prompts.jsonl`: four hand-written records. Card and SSN values are freshly generated fakes (Luhn-valid card with random body, SSN from area 773-899), not famous public test values; the key is test-prefixed and deliberately non-provider-shaped.
- `demo_prompts_seed7.jsonl`: 30 records from `python -m generators.generate_prompts --n 30 --seed 7` (clean, control and hard layers). A test checks that it regenerates byte-for-byte.
- `gretel_tiny_raw.jsonl`: raw Gretel-format rows for the converter tests.
