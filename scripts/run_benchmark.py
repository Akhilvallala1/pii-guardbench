#!/usr/bin/env python3
"""Thin wrapper around `python -m pii_guardbench.cli`."""

from pii_guardbench.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
