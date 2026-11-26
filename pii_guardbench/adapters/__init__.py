"""Guardrail adapters and the registry used by the evaluator and CLI."""

from pii_guardbench.adapters.base import (
    AdapterFlags,
    AdapterResult,
    AdapterUnavailableError,
    GuardrailAdapter,
)
from pii_guardbench.adapters.mock import MockAdapter
from pii_guardbench.adapters.nemo import NemoGuardrailsAdapter
from pii_guardbench.adapters.piiguard import PiiguardAdapter
from pii_guardbench.adapters.presidio import PresidioAdapter
from pii_guardbench.adapters.registry import get_adapter, list_adapters, register_adapter

# Register built-in adapters on import.
from pii_guardbench.adapters import _builtins  # noqa: F401

__all__ = [
    "AdapterFlags",
    "AdapterResult",
    "AdapterUnavailableError",
    "GuardrailAdapter",
    "MockAdapter",
    "NemoGuardrailsAdapter",
    "PiiguardAdapter",
    "PresidioAdapter",
    "get_adapter",
    "list_adapters",
    "register_adapter",
]
