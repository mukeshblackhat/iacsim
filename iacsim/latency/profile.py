"""Profile sources and merging.

Layers: defaults.yaml → --profile a.yaml → --profile b.yaml. Later wins per
key; dicts merge recursively so an override file can be tiny.
"""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import yaml

from iacsim.core.interfaces import PROFILE_SOURCES, ProfileSource
from iacsim.core.models import Profile
from iacsim.core.util import deep_merge

DEFAULTS_PATH = Path(__file__).with_name("defaults.yaml")
_DEFAULTS_CACHE: tuple[int, dict[str, Any]] | None = None     # (mtime_ns, parsed document)


@PROFILE_SOURCES.register("defaults")
class DefaultsProfileSource(ProfileSource):
    def load(self, spec: str) -> dict[str, Any]:
        """defaults.yaml parsed once per process (re-read if the file changes);
        every caller gets its own deep copy, so a profile can be edited in place
        without touching the cache."""
        global _DEFAULTS_CACHE
        mtime = DEFAULTS_PATH.stat().st_mtime_ns
        if _DEFAULTS_CACHE is None or _DEFAULTS_CACHE[0] != mtime:
            _DEFAULTS_CACHE = (mtime, yaml.safe_load(DEFAULTS_PATH.read_text()))
        return copy.deepcopy(_DEFAULTS_CACHE[1])


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
        merged = deep_merge(merged, layer)
    return Profile(
        meta=merged.get("meta", {}),
        distance=merged.get("distance", {}),
        processing=merged.get("processing", {}),
        sources=[describe_layer(name, layer) for name, layer in layers],
        variance=merged.get("variance", {}),
        capacity=merged.get("capacity", {}),
    )


def describe_layer(spec: str, layer: dict[str, Any]) -> str:
    """How a layer shows up in report headers. A calibrated file carries
    `meta.source` / `meta.window`, so the rung is visible:
    `defaults → measured.yaml (cloudwatch, 7d)`. A plain override stays `team.yaml`."""
    meta = layer.get("meta") or {}
    source = meta.get("source")
    if not source or source in ("defaults", "manual") or source == spec:
        return spec
    window = meta.get("window")
    return f"{spec} ({source}, {window})" if window else f"{spec} ({source})"


def load_defaults_document() -> dict[str, Any]:
    """The parsed defaults.yaml (rung 0) as a plain dict — a fresh copy."""
    return DefaultsProfileSource().load("defaults")


def load_default_profile() -> Profile:
    """The built-in defaults as a Profile — what every test and the calibrator
    start from; the pipeline layers --profile files on top via merge_profiles."""
    return merge_profiles([("defaults", load_defaults_document())])
