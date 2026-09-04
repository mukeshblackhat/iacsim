"""iacsim.yaml — picks an implementation for every extension point.

Every key is optional; DEFAULTS below is the whole file with nothing overridden.
Precedence: CLI flags > iacsim.yaml in the target directory > DEFAULTS.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

DEFAULTS: dict[str, Any] = {
    "provider": "aws",
    "parsers": {
        "cloudformation": {"region": None},   # None → first region literal in the template → us-east-1
    },
    "inference": {
        "rules": [
            "step_functions",
            "event_source_mapping",
            "lambda_permission",
            "api_gateway_integration",
            "target_group",
            "env_var",
            "iam_policy",
            "vpc_peering",
        ],
    },
    "scenarios": {
        "sources": ["yaml_file", "inferred_from_entrypoints"],
        "file": "scenarios.yaml",
    },
    "latency": {
        "profiles": ["defaults"],
        "rules": ["distance", "processing", "cold_start"],
    },
    "simulation": {
        "walker": "expected_value",
        "samples": 10_000,           # monte_carlo only
        "seed": None,                # monte_carlo only; set for reproducible runs
        "load": "load.yaml",         # load walker only: arrival rates, next to scenarios.yaml
        "tail_factor": 1.3,          # load walker only: p99 ≈ tail_factor × expected before queueing
    },
    "analysis": {
        "analyzers": ["per_hop", "per_node", "per_category", "critical_path", "recommendations", "tail_risk",
                      "saturation"],
        "top_n": 10,
    },
    "report": {
        "outputs": ["text", "json"],
        "out_dir": ".iacsim",
    },
    "calibrate": {
        "source": "cloudwatch",      # any registered metric source; plugins/ can add more
        "window": "7d",              # 7d | 24h | 30m
        "out": "calibrated.yaml",    # relative to the target dir
        # Per-source options, passed as ctor kwargs (None → the source's own default).
        # Credentials never go here: the AWS credential chain / env vars supply them.
        "sources": {
            "cloudwatch": {"region": None, "aws_profile": None},
            "fake": {"fixture": None},
        },
    },
}

CONFIG_FILENAME = "iacsim.yaml"


@dataclass
class Config:
    data: dict[str, Any] = field(default_factory=lambda: _deep_copy(DEFAULTS))
    path: Path | None = None

    # dotted access: cfg.get("simulation.walker")
    def get(self, dotted: str, default: Any = None) -> Any:
        node: Any = self.data
        for part in dotted.split("."):
            if not isinstance(node, dict) or part not in node:
                return default
            node = node[part]
        return node

    def set(self, dotted: str, value: Any) -> None:
        *parents, leaf = dotted.split(".")
        node = self.data
        for part in parents:
            node = node.setdefault(part, {})
        node[leaf] = value


def load_config(target_dir: Path, overrides: dict[str, Any] | None = None) -> Config:
    """DEFAULTS ← iacsim.yaml (if present) ← CLI overrides (dotted keys, None skipped)."""
    cfg = Config()
    candidate = target_dir / CONFIG_FILENAME
    if candidate.is_file():
        user = yaml.safe_load(candidate.read_text()) or {}
        cfg.data = _deep_merge(cfg.data, user)
        cfg.path = candidate
    for key, value in (overrides or {}).items():
        if value is not None:
            cfg.set(key, value)
    return cfg


def _deep_merge(base: dict, override: dict) -> dict:
    out = dict(base)
    for k, v in override.items():
        out[k] = _deep_merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else v
    return out


def _deep_copy(d: dict) -> dict:
    # A real deep copy: `_deep_merge({}, d)` shared the first-level dicts with
    # DEFAULTS, so `cfg.set("latency.profiles", …)` leaked into every later Config.
    return copy.deepcopy(d)
