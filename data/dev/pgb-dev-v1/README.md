# pgb-dev-v1: development split (NOT a test set)

Purpose: build and select piiguard-v2 (the benchmark author's own system) without touching the frozen
test set `data/official/pgb-v1.0`. Nothing here is a benchmark result.

- `prompts.jsonl`: 1,000 records, seed 48151623 (pgb-v1.0 uses 2026): 600 clean, 200 hard, 200 control.
  `metadata.split` is `train` (iteration/diagnosis) or `holdout` (variant selection), assigned by template.
- `train.jsonl`, `holdout.jsonl`: the same records split by `metadata.split`.
- `holdout2.jsonl`: 500 records (300/100/100), seed 90210417, its own templates. Added after the first holdout had
  been used to motivate fixes, and used only for the final choice among already-built candidates.
- `manifest.json`: SHA-256 and counts. Regenerate or verify with `python -m generators.generate_dev [--check]`.

Templates, value pools (names, password words, ICD-10 codes, MRN/key/phone shapes) and control templates are new
(`generators/generate_dev.py`). Control semantics match pgb-v1.0: no planted PII, lookalike numbers and benign
mentions. `python scripts/check_dev_disjoint.py` (and `tests/test_dev_split.py`) checks that no dev template, template
fragment (>= 16 chars) or planted value occurs in pgb-v1.0 text, and the reverse. The generator rejects a dev record
whose planted value occurs in pgb-v1.0 text (exact string membership only).
