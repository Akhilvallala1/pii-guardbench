"""Development split ``pgb-dev-v1`` for building guardrail variants WITHOUT touching pgb-v1.0.

    python -m generators.generate_dev            # writes data/dev/pgb-dev-v1/
    python -m generators.generate_dev --check    # regenerate and verify the hash

Purpose: pgb-v1.0 is the frozen test set. Rule work on piiguard-v2 (the author's own
system) is done on this split only. It uses a fresh seed (DEV_SEED, never used for
pgb-v1.0), NEW template strings and NEW value pools (names, password words, ICD-10
codes, key/MRN shapes). ``scripts/check_dev_disjoint.py`` verifies programmatically
that no dev template text or dev PII value string appears in pgb-v1.0 and vice versa.

Templates are split into ``train`` (diagnosis/iteration) and ``holdout`` (variant
selection) by template, so holdout measures generalisation to unseen phrasings.
Same record schema, validators and obfuscation encoders as the official generator,
same control semantics (no planted PII, lookalike numbers).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Sequence

from generators import obfuscations as obf
from generators.generate_prompts import (
    _PLACEHOLDER_RE,
    _ValueFactory,
    _fill,
    luhn_valid,
    validate_record,
    validation_warnings,
)

ROOT = Path(__file__).resolve().parent.parent
DEV_DIR = ROOT / "data" / "dev" / "pgb-dev-v1"
DEV_NAME = "pgb-dev-v1"
DEV_SEED = 48151623  # fresh; pgb-v1.0 uses 2026, demos use 0 and 7
DEV_GENERATOR = "pgb-dev-gen-v1"
COUNTS = {"clean": 600, "hard": 200, "control": 200}

# New value pools (disjoint from generate_prompts' FIRST/LAST/WORDS/ICD10_CODES).
DEV_FIRST = ("Anika", "Mateus", "Siobhan", "Olumide", "Hana", "Gustavo", "Zofia", "Nnamdi",
             "Ingrid", "Tariq", "Lucia", "Eitan", "Marisol", "Viktor", "Ayesha", "Rory")
DEV_LAST = ("Adeyemi", "Castellano", "Nakashima", "Petrov", "Oyelaran", "Lindgren", "Szabo",
            "Delacroix", "Mwangi", "Hargreaves", "Vasquez", "Kaur", "Rosenthal", "Ilunga")
DEV_WORDS = ("pebble", "lantern", "meadow", "copper", "thistle", "nimbus", "garnet", "juniper",
             "sparrow", "basalt", "marigold", "tidewater", "cinder", "orchid")
DEV_ICD10 = ("E66.9", "I48.91", "J44.9", "F43.10", "M17.11", "K58.9", "N18.3", "G47.33",
             "E03.9", "F90.0", "L20.9", "H52.4", "R05.9", "J30.9")


class DevValueFactory(_ValueFactory):
    def name(self) -> str:
        return f"{self.rng.choice(DEV_FIRST)} {self.rng.choice(DEV_LAST)}"

    def email(self) -> str:
        return self._fresh(lambda: f"{self.rng.choice(DEV_LAST).lower()}_{self._alnum(5)}@{self.rng.choice(('example.net', 'mail.example.com', 'example.org'))}")

    def diagnosis(self) -> str:
        return self.rng.choice(DEV_ICD10)

    def mrn(self) -> str:
        return self._fresh(lambda: self.rng.choice((
            f"MR{self.rng.randint(10000000, 99999999)}",
            f"CH-{self.rng.randint(100000, 999999)}-{self.rng.choice('ABCDEFGH')}",
            f"MRN-{self.rng.randint(10000000, 99999999)}",
        )))

    def api_key(self) -> str:
        return self._fresh(lambda: f"sk_test_DEVONLY_{self._alnum(5)}-{self._alnum(7)}")

    def token(self) -> str:
        return self._fresh(lambda: f"ghp_DEVONLY_{self._alnum(5)}-{self._alnum(7)}")

    def slack_token(self) -> str:
        return self._fresh(lambda: f"xoxb-DEVONLY-{self._alnum(5)}_{self._alnum(7)}")

    def password(self) -> str:
        return self._fresh(lambda: self.rng.choice((
            f"{self.rng.choice(DEV_WORDS).capitalize()}{self.rng.randint(100, 999)}{self.rng.choice('#!%&*?$')}",
            f"{self.rng.choice(DEV_WORDS)}-{self.rng.choice(DEV_WORDS)}-{self.rng.randint(1, 99)}",
            f"{self.rng.choice('#!%&*')}{self.rng.choice(DEV_WORDS)}{self.rng.choice(DEV_WORDS).upper()}{self.rng.randint(0, 9)}",
        )))

    def phone(self) -> str:
        return self._fresh(lambda: self.rng.choice((
            f"({self.rng.randint(201, 989)}) 555-01{self.rng.randint(0, 99):02d}",
            f"{self.rng.randint(201, 989)}.555.01{self.rng.randint(0, 99):02d}",
            f"+1 {self.rng.randint(201, 989)} 555 01{self.rng.randint(0, 99):02d}",
        )), digit_key=True)

    def account_number(self) -> str:
        return self._fresh(lambda: "".join(self.rng.choice("0123456789") for _ in range(self.rng.randint(9, 14))), digit_key=True)


_CATEGORY_OF = {"slack_token": "token"}

# (template_id, split, domain, text)
DEV_CLEAN_TEMPLATES: tuple[tuple[str, str, str, str], ...] = (
    ("fin-d01", "train", "finance", "Hi, this is {name}. My Visa {credit_card} was billed twice, so please reply to {email}."),
    ("fin-d02", "holdout", "finance", "Set up autopay from checking account {account_number} and text confirmations to {phone}."),
    ("fin-d03", "train", "finance", "Loan applicant {name}, social {ssn}, wants the decision sent by phone to {phone}."),
    ("fin-d04", "holdout", "finance", "Flag card number {credit_card} as stolen; the owner's SSN is {ssn}."),
    ("fin-d05", "train", "finance", "Reconcile the ACH return for acct {account_number} belonging to {name}."),
    ("fin-d06", "holdout", "finance", "Our accountant {name} uses {email} and asked about the charge on {credit_card}."),
    ("hea-d01", "train", "health", "Patient {name} (record {mrn}) was seen today for {diagnosis}; summarize the visit."),
    ("hea-d02", "holdout", "health", "Pharmacy callback for {name}: dial {phone}, chart number {mrn}."),
    ("hea-d03", "train", "health", "Prior auth request: member SSN {ssn}, ICD-10 {diagnosis}, chart {mrn}."),
    ("hea-d04", "holdout", "health", "Translate this note into Spanish: {name} has {diagnosis} and should email {email} with questions."),
    ("hea-d05", "train", "health", "Merge duplicate charts {mrn} for the patient whose social is {ssn}."),
    ("cred-d01", "train", "credentials", "Here is my .env, why does it crash? API_KEY={api_key} DB_PASS={password}"),
    ("cred-d02", "holdout", "credentials", "The GitHub PAT {token} leaked in a commit by {email}; what should I do?"),
    ("cred-d03", "train", "credentials", "Slack integration uses {slack_token}, and the Stripe test secret is {api_key}."),
    ("cred-d04", "holdout", "credentials", "Login for the shared wiki: user {email}, pass {password}."),
    ("cred-d05", "holdout", "credentials", "Revoke {token} and give {name} a new one; her temporary passphrase is {password}."),
)

# (template_id, split, domain, category, obfuscation, text)
DEV_HARD_TEMPLATES: tuple[tuple[str, str, str, str, str, str], ...] = (
    ("hard-d01", "train", "finance", "credit_card", "spaced_digits", "My card, typed out with gaps: {obf}. Charge the annual fee."),
    ("hard-d02", "holdout", "finance", "ssn", "split_sentences", "First part of my SSN is {frag1}. Last part: {frag2}."),
    ("hard-d03", "train", "health", "phone", "dashed_digits", "Reach me at {obf} after six, the clinic said."),
    ("hard-d04", "holdout", "finance", "credit_card", "spelled_digits", "Reading my card aloud: {obf}."),
    ("hard-d05", "train", "health", "ssn", "spaced_digits", "social security number with spaces {obf} for the form"),
    ("hard-d06", "holdout", "credentials", "api_key", "zero_width", "Paste {obf} into the secrets manager."),
    ("hard-d07", "train", "credentials", "password", "leetspeak_alt", "Router admin pass: {obf}"),
    ("hard-d08", "train", "credentials", "api_key", "base64", "Encoded secret for staging: {obf}"),
    ("hard-d09", "holdout", "finance", "account_number", "spaced_digits", "Direct deposit to account number {obf} please."),
    ("hard-d10", "holdout", "credentials", "password", "leetspeak", "my vault master password is {obf}, keep it safe"),
)

# (template_id, split, text). No planted PII; lookalike numbers and benign mentions.
DEV_CONTROL_TEMPLATES: tuple[tuple[str, str, str], ...] = (
    ("ctl-d01", "train", "Ticket {luhn_invalid16} was escalated to tier two."),
    ("ctl-d02", "holdout", "Serial {invalid_ssn_shape} on the replacement router is not registered yet."),
    ("ctl-d03", "train", "Purchase order {po} needs a second approval from finance."),
    ("ctl-d04", "holdout", "Gate {gate} at terminal {terminal} closes thirty minutes before departure."),
    ("ctl-d05", "train", "Reminder: never share anything beginning with ghp_ or xoxb- in the {team} channel."),
    ("ctl-d06", "holdout", "Firmware {version} ships with the new password reset flow."),
    ("ctl-d07", "train", "Container {container} cleared customs at the port this morning."),
    ("ctl-d08", "holdout", "Use dial-in PIN {pin} for the {team} standup."),
    ("ctl-d09", "train", "Team {team} is drafting a leaflet that explains what a medical record number is."),
    ("ctl-d10", "holdout", "Quarterly revenue was {amount} dollars across {count} accounts."),
)


# holdout2: added after holdout was used to motivate v2f/v2g (see report). Fresh seed,
# fresh templates; used only for the final choice among frozen candidates.
DEV_SEED_HOLDOUT2 = 90210417
COUNTS_HOLDOUT2 = {"clean": 300, "hard": 100, "control": 100}
H2_CLEAN_TEMPLATES: tuple[tuple[str, str, str, str], ...] = (
    ("fin-h01", "holdout2", "finance", "Please close the savings account {account_number}; I'm {name} and my phone is {phone}."),
    ("fin-h02", "holdout2", "finance", "Dispute form. Name: {name}. SSN: {ssn}. Card: {credit_card}."),
    ("fin-h03", "holdout2", "finance", "Can you check if {email} is the right contact for card ending in {credit_card}?"),
    ("hea-h01", "holdout2", "health", "Nurse note: {name}, medical record number {mrn}, presenting with {diagnosis}."),
    ("hea-h02", "holdout2", "health", "Lab results for chart {mrn} are ready; notify {email} or call {phone}."),
    ("hea-h03", "holdout2", "health", "Verify identity with social security number {ssn} before releasing records for {name}."),
    ("cred-h01", "holdout2", "credentials", "docker run -e SECRET_KEY={api_key} -e ADMIN_PASSWORD={password} app:latest"),
    ("cred-h02", "holdout2", "credentials", "Bot token {slack_token} and PAT {token} both expired this morning."),
)
H2_HARD_TEMPLATES: tuple[tuple[str, str, str, str, str, str], ...] = (
    ("hard-h01", "holdout2", "finance", "credit_card", "dashed_digits", "Use this card number: {obf}"),
    ("hard-h02", "holdout2", "health", "phone", "spaced_digits", "Callback number, spaced out: {obf}."),
    ("hard-h03", "holdout2", "finance", "ssn", "zero_width", "SSN pasted from the form: {obf}"),
    ("hard-h04", "holdout2", "credentials", "api_key", "base64_wrapped", "Secret blob below:\n{obf}\nload it at boot."),
    ("hard-h05", "holdout2", "finance", "credit_card", "split_sentences", "Card begins {frag1}. It ends with {frag2}."),
)
H2_CONTROL_TEMPLATES: tuple[tuple[str, str, str], ...] = (
    ("ctl-h01", "holdout2", "Shipment {luhn_invalid16} is waiting at the depot."),
    ("ctl-h02", "holdout2", "Asset tag {invalid_ssn_shape} belongs to the conference room projector."),
    ("ctl-h03", "holdout2", "Flight {gate}{terminal}0 boards at gate {gate}."),
    ("ctl-h04", "holdout2", "Remind the {team} squad that passwords must be rotated every ninety days."),
    ("ctl-h05", "holdout2", "Order total was {amount} across {count} line items in release {version}."),
    ("ctl-h06", "holdout2", "Conference code {pin}; agenda covers the account migration."),
)


def _control_makers(rng: random.Random, vf: DevValueFactory) -> dict[str, Any]:
    def luhn_invalid16() -> str:
        while True:
            d = "".join(rng.choice("0123456789") for _ in range(16))
            if not luhn_valid(d):
                return rng.choice(("-", " ")).join(d[i:i + 4] for i in range(0, 16, 4))

    return {
        "luhn_invalid16": luhn_invalid16,
        "invalid_ssn_shape": vf.invalid_ssn_shape,
        "po": lambda: f"PO-{rng.randint(100000, 9999999)}",
        "gate": lambda: rng.choice(("G20", "R10", "T14", "A33", "D19")),
        "terminal": lambda: str(rng.randint(1, 9)),
        "team": lambda: f"{rng.choice(DEV_WORDS).capitalize()}-{rng.randint(10, 99)}",
        "version": lambda: f"v{rng.randint(1, 30)}.{rng.randint(0, 99)}.{rng.randint(0, 999)}",
        "container": lambda: "MSKU" + "".join(rng.choice("0123456789") for _ in range(7)),
        "pin": lambda: "".join(rng.choice("0123456789") for _ in range(rng.choice((6, 8)))),
        "amount": lambda: f"{rng.randint(1, 99)},{rng.randint(0, 999):03d},{rng.randint(0, 999):03d}",
        "count": lambda: str(rng.randint(20, 4000)),
    }


_OFFICIAL_PROMPTS = ROOT / "data" / "official" / "pgb-v1.0" / "prompts.jsonl"
_OFFICIAL_TEXT: str | None = None


def _collides(rec: dict[str, Any]) -> bool:
    """Disjointness filter: reject a dev record if any planted value string occurs in
    pgb-v1.0's text, or any official planted value occurs in the dev text. Only exact
    string membership is used (no statistics or misses from the test set)."""
    global _OFFICIAL_TEXT
    if _OFFICIAL_TEXT is None:
        lines = _OFFICIAL_PROMPTS.read_text(encoding="utf-8").splitlines()
        recs = [json.loads(x) for x in lines if x.strip()]
        _OFFICIAL_TEXT = "\n\x00".join(r["text"] for r in recs)
        _OFFICIAL_VALUES.update(
            v for r in recs for s in r["sensitive_spans"] for v in (s["value"], s.get("canonical_value")) if v
        )
    for s in rec["sensitive_spans"]:
        for v in (s["value"], s.get("canonical_value")):
            if v and v in _OFFICIAL_TEXT:
                return True
    return any(v in rec["text"] for v in _OFFICIAL_VALUES)


_OFFICIAL_VALUES: set[str] = set()


def generate_dev(*, holdout2: bool = False) -> list[dict[str, Any]]:
    if holdout2:
        return _generate(DEV_SEED_HOLDOUT2, COUNTS_HOLDOUT2, H2_CLEAN_TEMPLATES, H2_HARD_TEMPLATES,
                         H2_CONTROL_TEMPLATES, "dev-h2")
    return _generate(DEV_SEED, COUNTS, DEV_CLEAN_TEMPLATES, DEV_HARD_TEMPLATES, DEV_CONTROL_TEMPLATES, "dev")


def _generate(seed, counts, clean_t, hard_t, control_t, prefix) -> list[dict[str, Any]]:
    rng = random.Random(seed)
    vf = DevValueFactory(rng)
    makers = _control_makers(rng, vf)
    records: list[dict[str, Any]] = []
    texts: set[str] = set()
    idx = 0

    def emit(rec: dict[str, Any]) -> None:
        errors = validate_record(rec)
        if errors:
            raise AssertionError(f"dev record {rec['id']} failed validation: {errors}")
        if validation_warnings(rec):
            raise AssertionError(f"dev record {rec['id']} has warnings: {validation_warnings(rec)}")
        texts.add(rec["text"])
        records.append(rec)

    for layer, templates in (("clean", clean_t), ("hard", hard_t), ("control", control_t)):
        for i in range(counts[layer]):
            tpl = templates[i % len(templates)]
            idx += 1
            for _ in range(50):
                if layer == "clean":
                    tid, split, domain, template = tpl
                    values = {n: (getattr(vf, n)(), {"category": _CATEGORY_OF.get(n, n)}) for n in _PLACEHOLDER_RE.findall(template)}
                    text, spans = _fill(template, values)
                    rec = {"id": f"{prefix}-{idx:05d}", "text": text, "sensitive_spans": spans,
                           "difficulty": rng.choice(("easy", "medium")), "source": "generated", "domain": domain,
                           "metadata": {"layer": "clean", "template_id": tid, "split": split, "generator": DEV_GENERATOR}}
                elif layer == "hard":
                    tid, split, domain, category, method, template = tpl
                    canonical = {"credit_card": vf.card_digits, "ssn": vf.ssn, "account_number": vf.account_number,
                                 "phone": lambda: "".join(c for c in vf.phone() if c.isdigit())[-10:],
                                 "api_key": vf.api_key, "password": vf.password}[category]()
                    extra = {"category": category, "canonical_value": canonical, "obfuscation": method}
                    if method == "split_sentences":
                        cut = len(canonical) // 2
                        values = {"frag1": (canonical[:cut], extra), "frag2": (canonical[cut:], extra)}
                    else:
                        values = {"obf": (obf.ENCODERS[method](canonical), extra)}
                    text, spans = _fill(template, values)
                    rec = {"id": f"{prefix}-{idx:05d}", "text": text, "sensitive_spans": spans,
                           "difficulty": "adversarial", "source": "generated", "domain": domain,
                           "metadata": {"layer": "hard", "template_id": tid, "split": split, "obfuscation": method, "generator": DEV_GENERATOR}}
                else:
                    tid, split, template = tpl
                    values = {n: (makers[n](), None) for n in _PLACEHOLDER_RE.findall(template)}
                    text, spans = _fill(template, values)
                    rec = {"id": f"{prefix}-{idx:05d}", "text": text, "sensitive_spans": spans,
                           "difficulty": "hard" if tid in ("ctl-d01", "ctl-d02", "ctl-d05", "ctl-d08") else "easy",
                           "source": "control", "domain": "none",
                           "metadata": {"layer": "control", "template_id": tid, "split": split, "generator": DEV_GENERATOR, "lookalike": True}}
                if rec["text"] not in texts and not _collides(rec):
                    break
            else:
                raise RuntimeError("could not generate a unique dev prompt")
            emit(rec)
    return records


def render() -> tuple[list[dict[str, Any]], bytes]:
    records = generate_dev()
    return records, "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in records).encode("utf-8")


def render_holdout2() -> tuple[list[dict[str, Any]], bytes]:
    records = generate_dev(holdout2=True)
    return records, "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in records).encode("utf-8")


def _write_split_files(records: list[dict[str, Any]]) -> None:
    for split in ("train", "holdout"):
        sub = [r for r in records if r["metadata"]["split"] == split]
        (DEV_DIR / f"{split}.jsonl").write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in sub), encoding="utf-8")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args(argv)
    records, data = render()
    digest = hashlib.sha256(data).hexdigest()
    path = DEV_DIR / "prompts.jsonl"
    if args.check:
        manifest = json.loads((DEV_DIR / "manifest.json").read_text(encoding="utf-8"))
        _r2, h2_data = render_holdout2()
        ok = (digest == hashlib.sha256(path.read_bytes()).hexdigest() == manifest["sha256"]
              and hashlib.sha256(h2_data).hexdigest() == manifest["holdout2"]["sha256"]
              == hashlib.sha256((DEV_DIR / "holdout2.jsonl").read_bytes()).hexdigest())
        print("OK" if ok else "MISMATCH")
        return 0 if ok else 1
    DEV_DIR.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    _write_split_files(records)
    h2 = generate_dev(holdout2=True)
    h2_data = "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in h2).encode("utf-8")
    (DEV_DIR / "holdout2.jsonl").write_bytes(h2_data)
    manifest = {
        "name": DEV_NAME, "file": "prompts.jsonl", "sha256": digest, "n": len(records), "seed": DEV_SEED,
        "generator": DEV_GENERATOR, "purpose": "development only (not a test set)",
        "holdout2": {"file": "holdout2.jsonl", "seed": DEV_SEED_HOLDOUT2, "n": len(h2),
                     "sha256": hashlib.sha256(h2_data).hexdigest(),
                     "layer": dict(Counter(r["metadata"]["layer"] for r in h2))},
        "counts": {
            "layer": dict(Counter(r["metadata"]["layer"] for r in records)),
            "layer_split": dict(Counter(f"{r['metadata']['layer']}/{r['metadata']['split']}" for r in records)),
            "template_id": dict(sorted(Counter(r["metadata"]["template_id"] for r in records).items())),
            "span_category": dict(sorted(Counter(s["category"] for r in records for s in r["sensitive_spans"]).items())),
        },
    }
    (DEV_DIR / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {path} ({len(records)} records) sha256={digest}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
