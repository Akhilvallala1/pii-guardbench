# results/piiguard-v2 (uncommitted, author-written reference rows)

- `pgb-v1.0-*.json`: FIRST one-shot. Frozen v2 rules (`frozen_v2_rules.yaml`, f540775e, `FROZEN.json`).
  **Historical only**: these rules contained name cues (Customer, Cardholder, ask, cc) that appear in no dev prompt
  but do appear in pgb-v1.0 templates.
- `ablated/`: SECOND, disclosed run with v2-ablated (`frozen_v2_ablated_rules.yaml`, 21d97fe2, `FROZEN_ABLATED.json`),
  done after a review that inspected test templates. Clean 108/600 (0.180), hard 33/200 (0.165), control FP 0/200.
  The union-filtered row is identical. This is the recommended row.
- `frozen_v2_1_rules.yaml` (9e8b2abc): merge-type fix. Validated on dev only (0 scoring diffs). Never run on pgb-v1.0.
- `frozen_rules.sha256`: one SHA-256 line per archived rules file.
- Caveats:
  - The dev split shares pgb's generator, encoders and conventions (ICD regex, SSN area exclusion, key prefixes, MRN-,
    control semantics). All pgb-v1.0 numbers here are optimistic in-family
    estimates (upper bound on protection, likely underestimate / lower bound on real-world leak; e.g. Gretel 0.792 vs
    ablated 0.180).
  - Gretel (main 0.932, v2-ablated 0.792 prompt leak) is the out-of-family estimate.
  - In-scope equals overall by construction (the 11-category contract covers every clean and hard category).
  - The worktree rules.yaml was stashed and restored after the first one-shot. Its hash was unchanged.
- Full account: `/workspace/o1/piiguard_v2_report.md`, section 10.
