# generators

This folder will produce the shipped PII-GuardBench prompt set: about 1,000 freshly generated prompts with planted, obviously synthetic sensitive values, plus no-PII controls.

`generate_prompts.py` is a placeholder CLI (`--n`, default 1,000) and already includes working validators:

- Luhn checksum for credit card numbers (spaces and hyphens ignored)
- SSN format check (area not 000, 666, or 900–999; group not 00; serial not 0000)
- A stub MRN format check
- API key / token prefix check (`sk_test_`, `ghp_`, `xoxb-`)
- Span offset check (`text[start:end] == value`)

Generation itself is intentionally not implemented. Do not generate the 1,000 prompts until the generator design is agreed. Public labeled sets such as Nemotron-PII and Gretel PII masking may inform category coverage and style only; they are not inputs to copy into the shipped set.
