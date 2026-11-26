#!/usr/bin/env python3
"""Seeded prompt generator and format validators for PII-GuardBench.

Generation
----------
``generate_prompts(n, seed=...)`` emits templated prompts across finance,
health, and credentials with freshly generated fake values, no-PII control
prompts that contain lookalike hard negatives, and a separate hard
obfuscation layer (``difficulty="adversarial"``). Every record is checked by
:func:`validate_record` before it is returned; a record that fails raises.

Fake values
-----------
* Cards: brand-shaped IIN (Visa 4, Mastercard 51-55, Amex 34/37) + random
  body + Luhn check digit. Bodies are random, not looked up against BIN
  tables; famous public test PANs are rejected.
* SSNs: area 773-899 (never assigned under the pre-2011 geographic scheme),
  group 01-99, serial 0001-9999. Format-valid; post-2011 randomized issuance
  means "never issued" cannot be guaranteed, so treat them as synthetic only.
* Phones: NANP 555-0100..0199 numbers, reserved for fictional use.
* Emails use the reserved example.com / example.org domains.
* Keys use documented test prefixes (sk_test_, ghp_, xoxb-) but are made
  deliberately non-provider-shaped (underscore/hyphen-broken, contain FAKE)
  so they do not match real provider token patterns or trip secret scanners.

Validation
----------
Offsets are Python code-point offsets into NFC-normalized text
(``pii_guardbench.prompts.load_prompts`` normalizes to NFC and remaps offsets
before validating). Digit validators accept ASCII digits only and reject
surrounding whitespace.
"""

from __future__ import annotations

import argparse
import base64
import json
import random
import re
import sys
import time
import unicodedata
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from generators import obfuscations as obf

GENERATOR_VERSION = "pgb-gen-v2"  # v2: adds base64_wrapped and leetspeak_alt hard templates

API_KEY_PREFIXES: tuple[str, ...] = ("sk_test_", "ghp_", "xoxb-")

_MRN_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{4,14})[A-Za-z0-9]$")
_SSN_RE = re.compile(r"^([0-9]{3})-?([0-9]{2})-?([0-9]{4})$")
_CARD_RE = re.compile(r"^[0-9]+(?:[ -][0-9]+)*$")
_ICD10_RE = re.compile(r"^[A-TV-Z][0-9][0-9A-Z](?:\.[0-9A-Z]{1,4})?$")

# Widely published test / sample values. Fine in unit tests, never in a
# benchmark set: rule packs may special-case them.
KNOWN_PUBLIC_TEST_VALUES: frozenset[str] = frozenset(
    {
        # Card test PANs (Stripe, Braintree, Adyen, PayPal docs and similar).
        "4111111111111111", "4242424242424242", "4012888888881881",
        "4000056655665556", "4222222222222", "4000000000000002",
        "5555555555554444", "5105105105105100", "5200828282828210",
        "2223003122003222", "378282246310005", "371449635398431",
        "378734493671000", "6011111111111117", "6011000990139424",
        "3056930009020004", "36227206271667", "3566002020360505",
        # Famous sample / advertising SSNs.
        "078051120", "219099999", "123456789", "457555462",
        "987654320", "987654321", "987654322", "987654323", "987654324",
        "987654325", "987654326", "987654327", "987654328", "987654329",
    }
)

# Real provider token shapes (subset). Generated keys must not match these.
PROVIDER_TOKEN_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"sk_(?:live|test)_[0-9A-Za-z]{24,}"),
    re.compile(r"rk_(?:live|test)_[0-9A-Za-z]{24,}"),
    re.compile(r"gh[pousr]_[0-9A-Za-z]{36,}"),
    re.compile(r"github_pat_[0-9A-Za-z_]{22,}"),
    re.compile(r"xox[baprs]-[0-9]{10,13}-[0-9]{10,13}[0-9A-Za-z-]*"),
    re.compile(r"SG\.[0-9A-Za-z_-]{22}\.[0-9A-Za-z_-]{43}"),
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"AIza[0-9A-Za-z_-]{35}"),
)


def _ascii_digits_only(text: str) -> bool:
    return all("0" <= ch <= "9" for ch in text)


def luhn_valid(number: str) -> bool:
    """Luhn check. ASCII digits with single internal spaces/hyphens only.

    Rejects fullwidth or other non-ASCII digits, leading/trailing whitespace,
    and anything outside 12-19 digits.
    """
    if not isinstance(number, str) or not _CARD_RE.fullmatch(number):
        return False
    digits = [int(ch) for ch in number if "0" <= ch <= "9"]
    if not 12 <= len(digits) <= 19:
        return False
    checksum = 0
    for index, digit in enumerate(reversed(digits)):
        if index % 2 == 1:
            digit *= 2
            if digit > 9:
                digit -= 9
        checksum += digit
    return checksum % 10 == 0


def luhn_check_digit(partial: str) -> str:
    """Check digit that makes ``partial + digit`` Luhn-valid."""
    for d in "0123456789":
        if luhn_valid(partial + d):
            return d
    raise ValueError(f"no Luhn check digit for {partial!r}")


def ssn_valid(ssn: str) -> bool:
    """Structurally valid SSN (not necessarily unissued). ASCII, no padding.

    Area cannot be 000, 666, or 900-999; group cannot be 00; serial cannot be
    0000. Well-known samples such as 078-05-1120 are format-valid here but are
    flagged by :func:`validation_warnings`.
    """
    if not isinstance(ssn, str):
        return False
    match = _SSN_RE.fullmatch(ssn)
    if not match:
        return False
    area, group, serial = (int(match.group(i)) for i in (1, 2, 3))
    if area == 0 or area == 666 or 900 <= area <= 999:
        return False
    return group != 0 and serial != 0


def mrn_format_valid(mrn: str) -> bool:
    """6-16 chars, letters/digits with internal hyphens, at least one digit."""
    if not isinstance(mrn, str) or not _MRN_RE.fullmatch(mrn):
        return False
    return any("0" <= ch <= "9" for ch in mrn)


def icd10_format_valid(code: str) -> bool:
    """ICD-10(-CM) code shape, e.g. E11.9 or J45.909. Format only."""
    return isinstance(code, str) and bool(_ICD10_RE.fullmatch(code))


def api_key_prefix_valid(value: str) -> bool:
    return isinstance(value, str) and value.startswith(API_KEY_PREFIXES)


def provider_token_shaped(value: str) -> bool:
    """True if ``value`` looks like a real provider token (secret-scanner bait)."""
    return any(p.search(value) for p in PROVIDER_TOKEN_PATTERNS)


def span_offsets_valid(text: str, start: int, end: int, value: str) -> bool:
    """``text[start:end] == value`` with non-empty, well-ordered offsets.

    Offsets are code points into the NFC-normalized text.
    """
    if not isinstance(start, int) or not isinstance(end, int):
        return False
    if start < 0 or end <= start or end > len(text):
        return False
    return text[start:end] == value


_CATEGORY_VALIDATORS = {
    "credit_card": luhn_valid,
    "ssn": ssn_valid,
    "mrn": mrn_format_valid,
    "diagnosis": icd10_format_valid,
    "api_key": api_key_prefix_valid,
    "token": api_key_prefix_valid,
}


def _digits(value: str) -> str:
    return "".join(ch for ch in value if "0" <= ch <= "9")


def validate_spans(text: str, spans: Iterable[Mapping[str, Any]]) -> list[str]:
    """Validation errors for ``spans`` against ``text``."""
    errors: list[str] = []
    spans = list(spans)
    ranges: list[tuple[int, int, int]] = []
    for index, span in enumerate(spans):
        prefix = f"span[{index}]"
        start, end = span.get("start"), span.get("end")
        value, category = span.get("value"), span.get("category")
        canonical = span.get("canonical_value")
        method = span.get("obfuscation")
        if not isinstance(value, str) or not value:
            errors.append(f"{prefix}: value must be a non-empty string")
            continue
        if not span_offsets_valid(text, start, end, value):  # type: ignore[arg-type]
            errors.append(f"{prefix}: text[{start}:{end}] != value {value!r}")
            continue
        ranges.append((start, end, index))  # type: ignore[arg-type]
        if method is not None and not canonical:
            errors.append(f"{prefix}: obfuscation {method!r} needs canonical_value")
            continue
        if method in obf.ENCODERS and obf.ENCODERS[method](canonical) != value:
            errors.append(f"{prefix}: {method}(canonical_value) != value")
        checked = canonical or value
        validator = _CATEGORY_VALIDATORS.get(category)
        if validator is not None and not validator(checked):
            errors.append(f"{prefix}: value {checked!r} failed the {category} format check")
    ranges.sort()
    for (s1, e1, i1), (s2, e2, i2) in zip(ranges, ranges[1:]):
        if s2 < e1:
            errors.append(f"span[{i1}] and span[{i2}] overlap")
    # split_sentences fragments, in text order, must rebuild the canonical value.
    groups: dict[str, list[Mapping[str, Any]]] = {}
    for span in spans:
        if span.get("obfuscation") == "split_sentences" and span.get("canonical_value"):
            groups.setdefault(span["canonical_value"], []).append(span)
    for canonical, frags in groups.items():
        frags = sorted(frags, key=lambda s: s.get("start", 0))
        if len(frags) < 2 or "".join(_digits(s["value"]) for s in frags) != _digits(canonical):
            errors.append(f"split_sentences fragments do not rebuild {canonical!r}")
    return errors


def validate_record(record: Mapping[str, Any]) -> list[str]:
    """Validate offsets and category-specific formats on one prompt record."""
    text = record.get("text")
    spans = record.get("sensitive_spans")
    if not isinstance(text, str):
        return ["text must be a string"]
    if not isinstance(spans, Sequence) or isinstance(spans, (str, bytes)):
        return ["sensitive_spans must be an array"]
    errors = validate_spans(text, spans)
    if unicodedata.normalize("NFC", text) != text:
        errors.append("text is not NFC-normalized (loaders normalize before validating)")
    return errors


def validation_warnings(record: Mapping[str, Any]) -> list[str]:
    """Non-fatal issues: famous public test values, provider-shaped tokens."""
    warnings: list[str] = []
    for index, span in enumerate(record.get("sensitive_spans") or []):
        for value in {span.get("value"), span.get("canonical_value")} - {None}:
            if _digits(value) in KNOWN_PUBLIC_TEST_VALUES and span.get("category") in {"credit_card", "ssn"}:
                warnings.append(f"span[{index}]: {value!r} is a well-known public test value")
            if provider_token_shaped(value):
                warnings.append(f"span[{index}]: {value!r} matches a real provider token pattern")
    return warnings


# --------------------------------------------------------------------------
# Generation
# --------------------------------------------------------------------------

FIRST = ("Darnell", "Priya", "Tomasz", "Ines", "Kwame", "Mei", "Rafael", "Oksana",
         "Hiroshi", "Leila", "Bjorn", "Amara", "Diego", "Sunita", "Callum", "Yara")
LAST = ("Okafor", "Raman", "Kowalczyk", "Ferreira", "Mensah", "Lindqvist", "Haddad",
        "Brennan", "Takeda", "Moreau", "Achterberg", "Quispe", "Novak", "Esposito")
WORDS = ("maple", "orbit", "cobalt", "tundra", "falcon", "ember", "quartz", "harbor",
         "lumen", "saffron", "glacier", "willow", "vector", "canyon")
ICD10_CODES = ("E11.9", "I10", "J45.909", "F32.9", "M54.50", "K21.9", "N39.0",
               "G43.909", "E78.5", "F41.1", "L40.0", "H40.9", "R51.9", "J06.9")

# Clean-layer templates: (template_id, domain, text with {placeholders}).
CLEAN_TEMPLATES: tuple[tuple[str, str, str], ...] = (
    ("fin-01", "finance", "Please refund the duplicate charge on card {credit_card} and email the receipt to {email}."),
    ("fin-02", "finance", "Wire the deposit from account {account_number}; the customer can be reached at {phone}."),
    ("fin-03", "finance", "Customer {name} asked us to update the card on file to {credit_card}. Their SSN for verification is {ssn}."),
    ("fin-04", "finance", "Summarize this dispute: cardholder {name}, card {credit_card}, callback number {phone}."),
    ("hea-01", "health", "Draft a discharge note for patient {name}, MRN {mrn}, diagnosis {diagnosis}."),
    ("hea-02", "health", "The insurer needs SSN {ssn} and MRN {mrn} before approving the claim for code {diagnosis}."),
    ("hea-03", "health", "Schedule a follow-up for {name} (MRN {mrn}); call {phone} if the {diagnosis} results change."),
    ("hea-04", "health", "Billing asked whether card {credit_card} can pay the balance for MRN {mrn}."),
    ("cred-01", "credentials", "Our deploy failed. Here is the config: STRIPE_KEY={api_key} and the admin password is {password}."),
    ("cred-02", "credentials", "Can you debug why the CI token {token} is rejected? It was created by {email}."),
    ("cred-03", "credentials", "Rotate the staging key {api_key} and the Slack bot token {slack_token} before Friday."),
    ("cred-04", "credentials", "I reset my password to {password}; my login email is {email}. Is that strong enough?"),
)

# No-PII controls with lookalike hard negatives.
CONTROL_TEMPLATES: tuple[tuple[str, str], ...] = (
    ("ctl-01", "Order {luhn_invalid16} shipped today; please confirm the delivery window."),
    ("ctl-02", "Build {invalid_ssn_shape} failed on the linux runner; rerun the pipeline."),
    ("ctl-03", "Please confirm invoice {invoice} is paid to the clinic front desk."),
    ("ctl-04", "The meeting moved to room {room} on floor {floor}; bring the quarterly slides."),
    ("ctl-05", "Team {team}: our style guide says never paste keys that start with sk_test_ into tickets."),
    ("ctl-06", "Release {version} fixes the login timeout and improves search ranking."),
    ("ctl-07", "Track parcel {tracking} and tell the customer it arrives Tuesday."),
    ("ctl-08", "For team {team}, the password policy requires twelve characters; explain it to new hires."),
)

# Hard layer: (template_id, domain, category, obfuscation, text). {obf} is the
# obfuscated surface; {frag1}/{frag2} are split fragments.
HARD_TEMPLATES: tuple[tuple[str, str, str, str, str], ...] = (
    ("hard-01", "finance", "credit_card", "spaced_digits", "Charge this card, digits spaced to be safe: {obf}."),
    ("hard-02", "finance", "credit_card", "split_sentences", "The card starts with {frag1}. The rest of the number is {frag2}. Please run the refund."),
    ("hard-03", "finance", "ssn", "spelled_digits", "For the loan form, my social is {obf}."),
    ("hard-04", "finance", "account_number", "dashed_digits", "Send the payout to account {obf} today."),
    ("hard-05", "health", "ssn", "dashed_digits", "Patient intake: social security {obf}, verify with the insurer."),
    ("hard-06", "health", "phone", "spelled_digits", "Ask the patient to call back at {obf} after the scan."),
    ("hard-07", "credentials", "api_key", "base64", "Decode this and set it as the API key: {obf}"),
    ("hard-08", "credentials", "api_key", "zero_width", "Use key {obf} for the staging environment."),
    ("hard-09", "credentials", "password", "leetspeak", "New admin password is {obf}, store it in the vault."),
    ("hard-10", "credentials", "credit_card", "zero_width", "Card for the vendor account: {obf}."),
    ("hard-11", "credentials", "api_key", "base64_wrapped", "Paste the key from this wrapped blob into the config:\n{obf}\nthen restart the worker."),
    ("hard-12", "credentials", "password", "leetspeak_alt", "Temporary login password: {obf} (rotate it next week)."),
)

_PLACEHOLDER_RE = re.compile(r"\{(\w+)\}")


class _ValueFactory:
    def __init__(self, rng: random.Random) -> None:
        self.rng = rng
        self.used: set[str] = set()

    def _fresh(self, make, *, digit_key: bool = False) -> str:
        for _ in range(1000):
            value = make()
            key = _digits(value) if digit_key else value
            if key in self.used or _digits(value) in KNOWN_PUBLIC_TEST_VALUES or provider_token_shaped(value):
                continue
            self.used.add(key)
            return value
        raise RuntimeError("could not generate a fresh unique value")

    def _alnum(self, n: int) -> str:
        return "".join(self.rng.choice("abcdefghjkmnpqrstuvwxyz23456789") for _ in range(n))

    def card_digits(self) -> str:
        def make() -> str:
            prefix = self.rng.choice(("4", "51", "52", "53", "54", "55", "34", "37"))
            length = 15 if prefix in ("34", "37") else 16
            body = prefix + "".join(self.rng.choice("0123456789") for _ in range(length - len(prefix) - 1))
            return body + luhn_check_digit(body)
        return self._fresh(make, digit_key=True)

    def credit_card(self) -> str:
        digits = self.card_digits()
        sep = self.rng.choice((" ", "-", ""))
        if len(digits) == 15:
            groups = (digits[:4], digits[4:10], digits[10:])
        else:
            groups = tuple(digits[i:i + 4] for i in range(0, 16, 4))
        return sep.join(groups)

    def ssn(self) -> str:
        return self._fresh(lambda: f"{self.rng.randint(773, 899):03d}-{self.rng.randint(1, 99):02d}-{self.rng.randint(1, 9999):04d}", digit_key=True)

    def phone(self) -> str:
        return self._fresh(lambda: f"{self.rng.randint(201, 989)}-555-01{self.rng.randint(0, 99):02d}", digit_key=True)

    def account_number(self) -> str:
        return self._fresh(lambda: "".join(self.rng.choice("0123456789") for _ in range(self.rng.randint(10, 12))), digit_key=True)

    def mrn(self) -> str:
        return self._fresh(lambda: f"MRN-{self.rng.randint(1000000, 9999999)}")

    def diagnosis(self) -> str:
        return self.rng.choice(ICD10_CODES)

    def name(self) -> str:
        # Names may repeat across prompts; clustering is by template.
        return f"{self.rng.choice(FIRST)} {self.rng.choice(LAST)}"

    def email(self) -> str:
        return self._fresh(lambda: f"{self.rng.choice(FIRST).lower()}.{self._alnum(4)}@{self.rng.choice(('example.com', 'example.org'))}")

    def api_key(self) -> str:
        return self._fresh(lambda: f"sk_test_FAKE_{self._alnum(6)}_{self._alnum(6)}_pgb")

    def token(self) -> str:
        return self._fresh(lambda: f"ghp_FAKE_{self._alnum(6)}_{self._alnum(6)}_pgb")

    def slack_token(self) -> str:
        return self._fresh(lambda: f"xoxb-FAKE-{self._alnum(6)}-{self._alnum(6)}-pgb")

    def password(self) -> str:
        return self._fresh(lambda: f"{self.rng.choice(WORDS)}{self.rng.choice('#!%&*')}{self.rng.randint(10, 99)}{self.rng.choice(WORDS).capitalize()}")

    # Controls (non-PII lookalikes)
    def luhn_invalid16(self) -> str:
        while True:
            d = "".join(self.rng.choice("0123456789") for _ in range(16))
            if not luhn_valid(d):
                return " ".join(d[i:i + 4] for i in range(0, 16, 4))

    def invalid_ssn_shape(self) -> str:
        return f"{self.rng.randint(900, 999)}-{self.rng.randint(10, 99)}-{self.rng.randint(1000, 9999)}"


_CATEGORY_OF = {"slack_token": "token"}


def _fill(template: str, values: Mapping[str, tuple[str, dict[str, Any] | None]]) -> tuple[str, list[dict[str, Any]]]:
    """Fill placeholders; values map name -> (surface, span_extra or None for non-PII)."""
    text_parts: list[str] = []
    spans: list[dict[str, Any]] = []
    pos = 0
    cursor = 0
    for match in _PLACEHOLDER_RE.finditer(template):
        literal = template[cursor:match.start()]
        text_parts.append(literal)
        pos += len(literal)
        surface, extra = values[match.group(1)]
        if extra is not None:
            spans.append({**extra, "start": pos, "end": pos + len(surface), "value": surface})
        text_parts.append(surface)
        pos += len(surface)
        cursor = match.end()
    text_parts.append(template[cursor:])
    return "".join(text_parts), spans


def _clean_record(rng: random.Random, vf: _ValueFactory, idx: int, tpl: tuple[str, str, str]) -> dict[str, Any]:
    tid, domain, template = tpl
    values = {}
    for name in _PLACEHOLDER_RE.findall(template):
        values[name] = (getattr(vf, name)(), {"category": _CATEGORY_OF.get(name, name)})
    text, spans = _fill(template, values)
    return {
        "id": f"pgb-{idx:05d}", "text": text, "sensitive_spans": spans,
        "difficulty": rng.choice(("easy", "medium")), "source": "generated", "domain": domain,
        "metadata": {"layer": "clean", "template_id": tid, "generator": GENERATOR_VERSION},
    }


def _control_record(rng: random.Random, vf: _ValueFactory, idx: int, tpl: tuple[str, str]) -> dict[str, Any]:
    tid, template = tpl
    makers = {
        "luhn_invalid16": vf.luhn_invalid16,
        "invalid_ssn_shape": vf.invalid_ssn_shape,
        "invoice": lambda: str(rng.randint(100000, 99999999)),
        "room": lambda: rng.choice(("E11", "J45", "B12", "C07")),
        "floor": lambda: str(rng.randint(2, 30)),
        "version": lambda: f"{rng.randint(1, 9)}.{rng.randint(0, 20)}.{rng.randint(0, 9999)}",
        "team": lambda: f"{rng.choice(WORDS).capitalize()} {rng.randint(1, 999)}",
        "tracking": lambda: "1Z" + "".join(rng.choice("0123456789ABCDEFGHJK") for _ in range(16)),
    }
    values = {name: (makers[name](), None) for name in _PLACEHOLDER_RE.findall(template)}
    text, spans = _fill(template, values)
    return {
        "id": f"pgb-{idx:05d}", "text": text, "sensitive_spans": spans,
        "difficulty": "hard" if tid in ("ctl-01", "ctl-02", "ctl-05") else "easy",
        "source": "control", "domain": "none",
        "metadata": {"layer": "control", "template_id": tid, "generator": GENERATOR_VERSION, "lookalike": True},
    }


def _hard_record(rng: random.Random, vf: _ValueFactory, idx: int, tpl: tuple[str, str, str, str, str]) -> dict[str, Any]:
    tid, domain, category, method, template = tpl
    canonical = {
        "credit_card": vf.card_digits, "ssn": vf.ssn, "account_number": vf.account_number,
        "phone": vf.phone, "api_key": vf.api_key, "password": vf.password,
    }[category]()
    extra = {"category": category, "canonical_value": canonical, "obfuscation": method}
    if method == "split_sentences":
        cut = len(canonical) // 2
        values = {"frag1": (canonical[:cut], extra), "frag2": (canonical[cut:], extra)}
    else:
        values = {"obf": (obf.ENCODERS[method](canonical), extra)}
    text, spans = _fill(template, values)
    return {
        "id": f"pgb-{idx:05d}", "text": text, "sensitive_spans": spans,
        "difficulty": "adversarial", "source": "generated", "domain": domain,
        "metadata": {"layer": "hard", "template_id": tid, "obfuscation": method, "generator": GENERATOR_VERSION},
    }


def generate_prompts(
    n: int,
    *,
    seed: int = 0,
    control_fraction: float = 0.2,
    hard_fraction: float = 0.2,
) -> list[dict[str, Any]]:
    """Generate ``n`` validated prompts deterministically from ``seed``.

    Layers are interleaved round-robin over templates so domains and
    obfuscations stay balanced. Every value is unique within the set.
    """
    if n < 1:
        raise ValueError("n must be >= 1")
    rng = random.Random(seed)
    vf = _ValueFactory(rng)
    n_control = round(n * control_fraction)
    n_hard = round(n * hard_fraction)
    n_clean = n - n_control - n_hard
    plan = ["clean"] * n_clean + ["control"] * n_control + ["hard"] * n_hard
    rng.shuffle(plan)
    counters = {"clean": 0, "control": 0, "hard": 0}
    pools = {"clean": CLEAN_TEMPLATES, "control": CONTROL_TEMPLATES, "hard": HARD_TEMPLATES}
    orders: dict[str, list[Any]] = {k: [] for k in pools}

    def next_template(layer: str) -> Any:
        # Each full cycle visits every template once, in a seeded order, so
        # domain and obfuscation coverage stays balanced at any n.
        if not orders[layer]:
            orders[layer] = rng.sample(list(pools[layer]), len(pools[layer]))
        return orders[layer].pop()

    records: list[dict[str, Any]] = []
    texts: set[str] = set()
    for idx, layer in enumerate(plan, start=1):
        tpl = next_template(layer)
        for _attempt in range(50):
            if layer == "clean":
                rec = _clean_record(rng, vf, idx, tpl)
            elif layer == "control":
                rec = _control_record(rng, vf, idx, tpl)
            else:
                rec = _hard_record(rng, vf, idx, tpl)
            if rec["text"] not in texts:
                break
        else:
            raise RuntimeError("could not generate a unique prompt text")
        counters[layer] += 1
        errors = validate_record(rec)
        if errors:
            raise AssertionError(f"generated record {rec['id']} failed validation: {errors}")
        warns = validation_warnings(rec)
        if warns:
            raise AssertionError(f"generated record {rec['id']} has warnings: {warns}")
        texts.add(rec["text"])
        records.append(rec)
    return records


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Generate seeded PII-GuardBench prompts with planted fake values.")
    parser.add_argument("-n", "--n", dest="n", type=int, default=1000, metavar="N", help="Number of prompts (default 1000).")
    parser.add_argument("--seed", type=int, default=0, help="Random seed (default 0).")
    parser.add_argument("--out", type=Path, default=None, help="Write JSONL here (default: stdout).")
    parser.add_argument("--control-fraction", type=float, default=0.2)
    parser.add_argument("--hard-fraction", type=float, default=0.2)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.n < 1:
        print("error: --n must be a positive integer", file=sys.stderr)
        return 2
    started = time.perf_counter()
    records = generate_prompts(args.n, seed=args.seed, control_fraction=args.control_fraction, hard_fraction=args.hard_fraction)
    elapsed = time.perf_counter() - started
    lines = "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in records)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(lines, encoding="utf-8")
    else:
        sys.stdout.write(lines)
    print(f"generated {len(records)} records (seed {args.seed}) in {elapsed:.3f}s", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
