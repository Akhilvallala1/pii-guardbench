"""Convert gretelai/gretel-pii-masking-en-v1 records into PII-GuardBench prompts.

Dataset: https://huggingface.co/datasets/gretelai/gretel-pii-masking-en-v1
License: Apache-2.0. The documents are synthetic (LLM-generated), not real
user text. Labels come from the generator, not from human annotators.

The published data has no character offsets: each record lists entity strings
and their types. Offsets are therefore *derived* here by locating every
whole-token occurrence of each entity string in the text, and every derived
span is verified with ``text[start:end] == value`` before it is kept.
"""

from __future__ import annotations

import ast
import json
import random
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Sequence

DATASET_ID = "gretelai/gretel-pii-masking-en-v1"
MIN_VALUE_LENGTH = 3

# Gretel entity type -> PII-GuardBench category. Labels not listed here are
# *dropped* (counted as mapping loss), never silently turned into "other".
LABEL_TO_CATEGORY: dict[str, str] = {
    "credit_card_number": "credit_card",
    "ssn": "ssn",
    "medical_record_number": "mrn",
    "api_key": "api_key",
    "password": "password",
    "email": "email",
    "phone_number": "phone",
    "name": "name",
    "first_name": "name",
    "last_name": "name",
    "street_address": "address",
    "address": "address",
    "account_number": "account_number",
}

# Gretel domains that map onto this benchmark's three planned domains.
# Everything else is recorded as domain "none" with the original in metadata.
DOMAIN_MAP: dict[str, str] = {
    "banking": "finance",
    "finance": "finance",
    "financial-services": "finance",
    "insurance": "finance",
    "healthcare": "health",
    "pharmaceuticals-biotechnology": "health",
    "medical": "health",
    "authentication-services": "credentials",
    "cryptography": "credentials",
    "cybersecurity": "credentials",
    "digital-certificates": "credentials",
    "security": "credentials",
    "biometrics": "credentials",
    "data-privacy": "credentials",
}


# Strictly-structured provider token formats. Synthetic values that happen to
# match these make secret scanners (including GitHub push protection) flag the
# committed sample, so records containing them are kept out of the *committed*
# sample. The choice depends only on the text, never on any guardrail output.
SECRET_LIKE_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(pattern)
    for pattern in (
        r"SG\.[A-Za-z0-9_-]{22}\.[A-Za-z0-9_-]{43}",  # SendGrid
        r"xox[abprs]-[A-Za-z0-9-]{10,}",  # Slack
        r"(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{36}",  # GitHub tokens
        r"github_pat_[A-Za-z0-9_]{22,}",
        r"AIza[0-9A-Za-z_-]{35}",  # Google API key
        r"(?:AKIA|ASIA)[A-Z0-9]{16}",  # AWS access key id
        r"[sr]k_live_[0-9A-Za-z]{16,}",  # Stripe live keys
        r"sq0(?:atp|csp|idp)-[A-Za-z0-9_-]{22,}",  # Square
        r"sl\.[A-Za-z0-9_-]{40,}",  # Dropbox
        r"glpat-[A-Za-z0-9_-]{20}",  # GitLab
        r"npm_[A-Za-z0-9]{36}",  # npm
        r"hf_[A-Za-z0-9]{34}",  # Hugging Face
    )
)


def contains_secret_like(text: str) -> bool:
    return any(pattern.search(text) for pattern in SECRET_LIKE_PATTERNS)


def exclude_secret_like(
    records: Sequence[dict[str, Any]],
) -> tuple[list[dict[str, Any]], int]:
    """Drop records whose text contains a provider-token-shaped string."""
    kept = [rec for rec in records if not contains_secret_like(rec["text"])]
    return kept, len(records) - len(kept)


@dataclass
class ConversionStats:
    """Counters describing what the converter kept, dropped, and why."""

    records_in: int = 0
    records_kept: int = 0
    records_dropped_no_mapped_entities: int = 0
    records_dropped_unparseable: int = 0
    records_dropped_no_verified_span: int = 0
    entities_in: int = 0
    entities_unmapped_label: Counter = field(default_factory=Counter)
    entities_too_short: int = 0
    entities_not_found_in_text: int = 0
    entities_multi_type: int = 0
    entities_kept: int = 0
    spans_emitted: int = 0
    spans_failed_offset_check: int = 0
    spans_by_category: Counter = field(default_factory=Counter)

    def merge(self, other: "ConversionStats") -> None:
        for name, value in other.__dict__.items():
            current = getattr(self, name)
            if isinstance(value, Counter):
                current.update(value)
            else:
                setattr(self, name, current + value)

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for name, value in self.__dict__.items():
            out[name] = dict(sorted(value.items())) if isinstance(value, Counter) else value
        return out


def parse_entities(raw: Any) -> list[dict[str, Any]]:
    """Parse the ``entities`` field, a Python-literal string of dicts."""
    if isinstance(raw, str):
        parsed = ast.literal_eval(raw)
    else:
        parsed = raw
    if not isinstance(parsed, (list, tuple)):
        raise ValueError("entities must be a list")
    return [dict(item) for item in parsed]


def map_label(label: str) -> str | None:
    """Return the benchmark category for a Gretel entity type, or None if unmapped."""
    return LABEL_TO_CATEGORY.get(label)


def map_domain(gretel_domain: str) -> str:
    return DOMAIN_MAP.get(gretel_domain, "none")


def find_occurrences(text: str, value: str) -> list[tuple[int, int]]:
    """All case-sensitive occurrences of ``value`` that are not inside a longer token."""
    pattern = re.compile(r"(?<!\w)" + re.escape(value) + r"(?!\w)")
    return [(m.start(), m.end()) for m in pattern.finditer(text)]


def convert_record(
    row: Mapping[str, Any], stats: ConversionStats | None = None
) -> dict[str, Any] | None:
    """Convert one Gretel row. Returns None if the record is dropped."""
    stats = stats if stats is not None else ConversionStats()
    stats.records_in += 1
    text = row.get("text")
    try:
        entities = parse_entities(row.get("entities"))
    except (ValueError, SyntaxError):
        stats.records_dropped_unparseable += 1
        return None
    if not isinstance(text, str) or not text:
        stats.records_dropped_unparseable += 1
        return None

    stats.entities_in += len(entities)
    spans: dict[tuple[int, int, str], dict[str, Any]] = {}
    original_labels: set[str] = set()
    mapped_entity_count = 0
    for entity in entities:
        value = entity.get("entity")
        types = [t for t in (entity.get("types") or []) if isinstance(t, str)]
        if not isinstance(value, str) or not types:
            continue
        if len(types) > 1:
            stats.entities_multi_type += 1
        category = None
        label_used = None
        for label in types:
            category = map_label(label)
            if category is not None:
                label_used = label
                break
        if category is None:
            stats.entities_unmapped_label[types[0]] += 1
            continue
        mapped_entity_count += 1
        if len(value) < MIN_VALUE_LENGTH:
            stats.entities_too_short += 1
            continue
        occurrences = find_occurrences(text, value)
        if not occurrences:
            stats.entities_not_found_in_text += 1
            continue
        original_labels.add(label_used)
        stats.entities_kept += 1
        for start, end in occurrences:
            if text[start:end] != value:
                stats.spans_failed_offset_check += 1
                continue
            spans[(start, end, category)] = {
                "category": category,
                "start": start,
                "end": end,
                "value": value,
            }

    if mapped_entity_count == 0:
        stats.records_dropped_no_mapped_entities += 1
        return None
    if not spans:
        stats.records_dropped_no_verified_span += 1
        return None

    ordered = sorted(spans.values(), key=lambda s: (s["start"], s["end"], s["category"]))
    for span in ordered:
        stats.spans_emitted += 1
        stats.spans_by_category[span["category"]] += 1
    stats.records_kept += 1
    gretel_domain = str(row.get("domain", ""))
    return {
        "id": f"gretel-en-v1-{row.get('uid', stats.records_in)}",
        "text": text,
        "sensitive_spans": ordered,
        "difficulty": "medium",
        "source": "adapted",
        "domain": map_domain(gretel_domain),
        "metadata": {
            "dataset": DATASET_ID,
            "gretel_domain": gretel_domain,
            "gretel_document_type": str(row.get("document_type", "")),
            "original_labels": sorted(original_labels),
            "difficulty_note": "constant 'medium'; the dataset provides no difficulty signal",
        },
    }


def convert_rows(rows: Iterable[Mapping[str, Any]]) -> tuple[list[dict[str, Any]], ConversionStats]:
    stats = ConversionStats()
    records: list[dict[str, Any]] = []
    for row in rows:
        record = convert_record(row, stats)
        if record is not None:
            records.append(record)
    return records, stats


def stratified_sample(
    records: Sequence[dict[str, Any]],
    n: int,
    seed: int,
    *,
    min_per_category: int = 0,
) -> list[dict[str, Any]]:
    """Seeded sample: a per-category floor first, then proportional by Gretel domain.

    The floor guarantees rare categories (api_key, password, ...) have enough
    examples to give a usable confidence interval. The remainder is filled
    proportionally across Gretel domains (largest-remainder allocation). The
    result is deterministic for a given input order, ``n`` and ``seed``.
    """
    if n >= len(records):
        return list(records)
    rng = random.Random(seed)
    by_id = {rec["id"]: rec for rec in records}
    chosen: dict[str, dict[str, Any]] = {}

    if min_per_category > 0:
        by_category: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for rec in records:
            for category in {s["category"] for s in rec["sensitive_spans"]}:
                by_category[category].append(rec)
        for category in sorted(by_category):
            have = sum(
                1 for rec in chosen.values()
                if any(s["category"] == category for s in rec["sensitive_spans"])
            )
            pool = [r for r in by_category[category] if r["id"] not in chosen]
            rng.shuffle(pool)
            for rec in pool:
                if have >= min_per_category or len(chosen) >= n:
                    break
                chosen[rec["id"]] = rec
                have += 1

    remaining = n - len(chosen)
    if remaining > 0:
        groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for rec in records:
            if rec["id"] not in chosen:
                groups[rec["metadata"]["gretel_domain"]].append(rec)
        total = sum(len(g) for g in groups.values())
        quotas: dict[str, int] = {}
        remainders: list[tuple[float, str]] = []
        for name in sorted(groups):
            exact = remaining * len(groups[name]) / total
            quotas[name] = int(exact)
            remainders.append((exact - int(exact), name))
        leftover = remaining - sum(quotas.values())
        for _, name in sorted(remainders, key=lambda item: (-item[0], item[1]))[:leftover]:
            quotas[name] += 1
        for name in sorted(groups):
            pool = list(groups[name])
            rng.shuffle(pool)
            for rec in pool[: quotas[name]]:
                chosen[rec["id"]] = rec

    return [by_id[rec_id] for rec_id in sorted(chosen)]


def write_jsonl(records: Iterable[Mapping[str, Any]], path: Any) -> None:
    with open(path, "w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
