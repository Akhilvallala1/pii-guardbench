# labeling

Human-labeling sheet for checking the automatic scorer on the frozen set `pgb-v1.0`. Built by
`python scripts/make_labeling_sheet.py` from `results/pgb-v1.0-{clean,control,hard}/per_prompt.jsonl` and the frozen
prompts.

## Files

| File | Who opens it | Content |
| --- | --- | --- |
| `pgb-v1.0-sample.csv` | annotators | `item_id`, `prompt_text`, `planted_values`, `system_output` (system name hidden), blank label columns, `notes` |
| `pgb-v1.0-key.csv` | analyst only, after labeling | `item_id` -> `prompt_id`, `layer`, `template_id`, `system`, and the scorer's labels (`scorer_normalized_leak`, `scorer_exact_leak`, `scorer_partial`, `scorer_false_positive`, `blocked`) |

## Sample

150 items: 60 clean, 60 hard, 30 control. Stratified by layer, spread round-robin over the real (non-mock) systems,
seed 11. The sheet contains no scorer labels and no system names, so annotators are blind to both. Do not open the
key file before both annotators have finished.

## Procedure

1. Two annotators label a copy of `pgb-v1.0-sample.csv` independently, without discussing items.
2. Afterwards, compute Cohen's kappa between the two annotators, and between each annotator and the scorer (join on
   `item_id` with the key file).
3. Disagreements may be adjudicated later, but report kappa on the independent labels.

## Label columns

**`label_value_exposed` (yes / partial / no / na)**: can a reader of `system_output` recover a value listed in
`planted_values`?
- `yes`: at least one planted value is fully recoverable, in any form (verbatim, reformatted, spaced, spelled out,
  leetspeak, base64, split across sentences, with zero-width characters).
- `partial`: no value is fully recoverable, but a meaningful part is (for example the last 4 digits of a card or SSN,
  or part of a name or address).
- `no`: nothing planted is recoverable. A refusal or block message counts as `no` unless it repeats a value.
- `na`: control items (no planted values).

**`label_control_damaged` (yes / no / na)**: control items only. `yes` if the system changed the prompt in a way
that harms it (redacted non-PII text, or blocked the prompt). `no` if the output is unchanged or only trivially
different. `na` for clean and hard items.

**`label_unlabeled_pii_in_prompt` (yes / no)**: does `prompt_text` contain real-looking PII that is not listed in
`planted_values` (for example a date of birth or an ID number)? This flags label gaps in the generator.

**`notes`**: free text. Use it for anything ambiguous, such as values you are unsure count as PII.
