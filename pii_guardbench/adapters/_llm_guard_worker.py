"""Stand-alone worker for the LLM Guard adapter (run in LLM Guard's own venv).

llm-guard 0.3.16 needs Python < 3.13 and pins presidio 2.2.358, so it cannot
share the benchmark's environment. The adapter starts this script with the
interpreter in ``$PGB_LLM_GUARD_PYTHON`` and talks JSON lines over stdin/stdout:
request ``{"text": ...}`` -> response ``{"text", "is_valid", "risk_score"}``.
This file must not import pii_guardbench.
"""

import json
import logging
import os
import sys

# Running this file puts its own directory (pii_guardbench/adapters/) first on
# sys.path, where llm_guard.py (the adapter) would shadow the real llm_guard package.
_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path[:] = [p for p in sys.path if os.path.abspath(p or ".") != _HERE]


def main() -> int:
    logging.disable(logging.CRITICAL)
    try:
        import structlog

        structlog.configure(wrapper_class=structlog.make_filtering_bound_logger(logging.CRITICAL))
    except Exception:
        pass
    from llm_guard.input_scanners import Anonymize
    from llm_guard.input_scanners.anonymize import DEFAULT_ENTITY_TYPES
    from llm_guard.vault import Vault

    scanner = Anonymize(Vault())  # library defaults
    out = sys.stdout
    out.write(json.dumps({"ready": True, "entity_types": list(DEFAULT_ENTITY_TYPES)}) + "\n")
    out.flush()
    for line in sys.stdin:
        if not line.strip():
            continue
        text = json.loads(line)["text"]
        sanitized, is_valid, risk = scanner.scan(text)
        out.write(json.dumps({"text": sanitized, "is_valid": bool(is_valid), "risk_score": float(risk)}) + "\n")
        out.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
