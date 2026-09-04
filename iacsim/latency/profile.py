"""Profile sources and merging.

Layers: defaults.yaml → --profile a.yaml → --profile b.yaml. Later wins per
key; dicts merge recursively so an override file can be tiny.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from iacsim.core.interfaces import PROFILE_SOURCES, ProfileSource
from iacsim.core.models import Profile

DEFAULTS_PATH = Path(__file__).with_name("defaults.yaml")


@PROFILE_SOURCES.register("defaults")
class DefaultsProfileSource(ProfileSource):
    def load(self, spec: str) -> dict[str, Any]:
        return yaml.safe_load(DEFAULTS_PATH.read_text())


@PROFILE_SOURCES.register("yaml_file")
class YamlProfileSource(ProfileSource):
    def load(self, spec: str) -> dict[str, Any]:
        path = Path(spec)
        if not path.is_file():
            raise FileNotFoundError(f"latency profile not found: {path}")
        return yaml.safe_load(path.read_text()) or {}


def merge_profiles(layers: list[tuple[str, dict[str, Any]]]) -> Profile:
    merged: dict[str, Any] = {}
    for _name, layer in layers:
        merged = _deep_merge(merged, layer)
    return Profile(
        meta=merged.get("meta", {}),
        distance=merged.get("distance", {}),
        processing=merged.get("processing", {}),
        sources=[name for name, _ in layers],
    )


def _deep_merge(base: dict, override: dict) -> dict:
    out = dict(base)
    for k, v in override.items():
        out[k] = _deep_merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else v
    return out
