"""pgb-dev-v1: regenerates, is disjoint from pgb-v1.0, and the leakage checker catches collisions."""
from __future__ import annotations

import hashlib
import importlib.util
import json
from collections import Counter
from pathlib import Path

from generators import generate_dev as dev
from pii_guardbench.prompts import load_prompts

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("check_dev_disjoint", ROOT / "scripts" / "check_dev_disjoint.py")
cdd = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cdd)


def test_dev_split_regenerates_and_matches_manifest() -> None:
    manifest = json.loads((dev.DEV_DIR / "manifest.json").read_text())
    _r, data = dev.render()
    assert hashlib.sha256(data).hexdigest() == manifest["sha256"] == hashlib.sha256((dev.DEV_DIR / "prompts.jsonl").read_bytes()).hexdigest()
    _r2, d2 = dev.render_holdout2()
    assert hashlib.sha256(d2).hexdigest() == manifest["holdout2"]["sha256"]
    assert dev.DEV_SEED != 2026 and dev.DEV_SEED_HOLDOUT2 != 2026


def test_dev_split_loads_with_layers_and_splits() -> None:
    p = load_prompts(dev.DEV_DIR / "prompts.jsonl")
    assert Counter(r["metadata"]["layer"] for r in p) == {"clean": 600, "hard": 200, "control": 200}
    assert {r["metadata"]["split"] for r in p} == {"train", "holdout"}
    assert all(not r["sensitive_spans"] for r in p if r["metadata"]["layer"] == "control")
    assert len(load_prompts(dev.DEV_DIR / "holdout2.jsonl")) == 500


def test_dev_split_is_disjoint_from_official() -> None:
    problems = cdd.check(cdd._load(cdd.DEV) + cdd._load(cdd.DEV_H2), cdd._load(cdd.OFFICIAL),
                         cdd.dev_templates(), cdd.official_templates())
    assert problems == []


def test_disjoint_checker_detects_template_and_value_collisions() -> None:
    off = [{"text": "Please refund the duplicate charge on card 4000 1234 now.", "sensitive_spans": [{"value": "4000 1234"}]}]
    dv = [{"text": "Ticket 4000 1234 escalated.", "sensitive_spans": [{"value": "Zed"}]}]
    tpl = "Please refund the duplicate charge on card {credit_card} now."
    problems = cdd.check(dv, off, [tpl], [tpl])
    assert any("identical template" in p for p in problems)
    assert any("pgb-v1.0 value in dev" in p for p in problems)
