"""Tiny helpers shared across stages (no domain knowledge lives here)."""

from __future__ import annotations

from typing import Any


def deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """`base` updated by `override`, recursing into dicts (later wins per key).
    Returns a new dict; the inputs are not mutated. Used for config layering
    (DEFAULTS ← iacsim.yaml) and profile layering (defaults ← --profile …)."""
    out = dict(base)
    for k, v in override.items():
        out[k] = deep_merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else v
    return out
