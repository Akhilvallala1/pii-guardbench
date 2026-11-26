"""Record hardware and package versions next to benchmark results."""

from __future__ import annotations

import os
import platform
import sys
from importlib import metadata
from typing import Any

TRACKED_PACKAGES = (
    "presidio-analyzer",
    "presidio-anonymizer",
    "spacy",
    "en-core-web-lg",
    "piiguard",
    "nemoguardrails",
    "jsonschema",
    "pyarrow",
)


def _package_versions() -> dict[str, str]:
    versions: dict[str, str] = {}
    for package in TRACKED_PACKAGES:
        try:
            versions[package] = metadata.version(package)
        except metadata.PackageNotFoundError:
            continue
    return versions


def _cpu_model() -> str | None:
    try:
        with open("/proc/cpuinfo", encoding="utf-8") as handle:
            for line in handle:
                if line.lower().startswith("model name"):
                    return line.split(":", 1)[1].strip()
    except OSError:
        pass
    return platform.processor() or None


def _memory_gb() -> float | None:
    try:
        with open("/proc/meminfo", encoding="utf-8") as handle:
            for line in handle:
                if line.startswith("MemTotal:"):
                    return round(int(line.split()[1]) / 1024 / 1024, 1)
    except (OSError, ValueError):
        pass
    return None


def collect_environment() -> dict[str, Any]:
    return {
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "cpu_model": _cpu_model(),
        "cpu_count": os.cpu_count(),
        "memory_gb": _memory_gb(),
        "gpu": "none used (CPU only)",
        "packages": _package_versions(),
    }
