"""Name-to-factory registry for guardrail adapters."""

from __future__ import annotations

from collections.abc import Callable

from pii_guardbench.adapters.base import GuardrailAdapter

AdapterFactory = Callable[[], GuardrailAdapter]

_REGISTRY: dict[str, AdapterFactory] = {}


def register_adapter(name: str, factory: AdapterFactory, *, overwrite: bool = False) -> None:
    """Register an adapter factory under a lowercase CLI name."""
    key = name.strip().lower()
    if not key:
        raise ValueError("Adapter name must be non-empty.")
    if key in _REGISTRY and not overwrite:
        raise ValueError(f"Adapter {name!r} is already registered.")
    _REGISTRY[key] = factory


def get_adapter(name: str) -> GuardrailAdapter:
    """Build a new adapter instance by registry name."""
    key = name.strip().lower()
    try:
        factory = _REGISTRY[key]
    except KeyError as exc:
        known = ", ".join(list_adapters()) or "(none)"
        raise KeyError(f"Unknown adapter {name!r}. Known adapters: {known}") from exc
    adapter = factory()
    if not getattr(adapter, "name", None):
        adapter.name = key
    return adapter


def list_adapters() -> list[str]:
    """Return registered adapter names in sorted order."""
    return sorted(_REGISTRY)
