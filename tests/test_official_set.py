"""The frozen official set regenerates byte-for-byte and matches its manifest hash."""

from __future__ import annotations

import hashlib
import json
from collections import Counter

from generators.freeze_official import OFFICIAL_DIR, render
from pii_guardbench.prompts import load_prompts


def test_official_set_hash_matches_manifest_and_regenerates() -> None:
    manifest = json.loads((OFFICIAL_DIR / "manifest.json").read_text(encoding="utf-8"))
    on_disk = (OFFICIAL_DIR / "prompts.jsonl").read_bytes()
    assert hashlib.sha256(on_disk).hexdigest() == manifest["sha256"]
    _records, data = render()
    assert data == on_disk
    assert manifest["n"] == 1000 and manifest["seed"] == 2026


def test_official_set_loads_and_has_three_layers() -> None:
    prompts = load_prompts(OFFICIAL_DIR / "prompts.jsonl")
    layers = Counter(p["metadata"]["layer"] for p in prompts)
    assert layers == {"clean": 600, "control": 200, "hard": 200}
