"""load.yaml → LoadProfile: how much traffic, from how many users.          [M8]

    users: [100, 500, 1000, 2000]          # sweep; or a single int
    per_user:                              # what one active user does
      start_workflow:  { every: 2m }
      poll_status:     { every: 2s, while: running }   # only while that user has a workflow in flight
      save_workflow:   { every: 30s }
    workflow_mix:                          # what a started workflow looks like
      - { scenario: run_workflow_3_nodes, share: 0.6 }
      - { scenario: run_workflow_1_video, share: 0.2 }
      - { scenario: run_workflow_10_text, share: 0.2 }
    thresholds: { p99_ms: 2000, utilisation: 0.8 }

`every` → requests per second per user (1 / seconds). `while: running` scales
that rate by the fraction of time a user has a workflow in flight — Little's
law: (workflow starts per second per user) × (mean workflow duration), capped
at 1. Mix shares must sum to 1; a mix scenario's arrival rate is the start
rate × its share. Scenarios in neither list get no traffic and are reported
as such.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

DEFAULT_THRESHOLDS = {"p99_ms": 2000.0, "utilisation": 0.8}
_INTERVAL = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*(ms|s|m|h)\s*$")
_UNIT_SECONDS = {"ms": 0.001, "s": 1.0, "m": 60.0, "h": 3600.0}


class LoadProfileError(ValueError):
    pass


@dataclass
class PerUser:
    scenario: str
    every_s: float
    while_running: bool = False

    @property
    def rps(self) -> float:
        return 1.0 / self.every_s


@dataclass
class LoadProfile:
    users: list[int]
    per_user: list[PerUser]
    workflow_mix: list[tuple[str, float]] = field(default_factory=list)
    thresholds: dict[str, float] = field(default_factory=lambda: dict(DEFAULT_THRESHOLDS))
    path: str | None = None

    def scenario_names(self) -> list[str]:
        return [p.scenario for p in self.per_user] + [name for name, _ in self.workflow_mix]


def parse_interval(text: Any) -> float:
    """'2s' → 2.0, '10m' → 600.0, '1h' → 3600.0, '500ms' → 0.5; a bare number is seconds."""
    if isinstance(text, (int, float)):
        return float(text)
    m = _INTERVAL.match(str(text))
    if not m:
        raise LoadProfileError(f"bad interval {text!r}: use e.g. 2s, 30s, 10m, 1h")
    value = float(m.group(1)) * _UNIT_SECONDS[m.group(2)]
    if value <= 0:
        raise LoadProfileError(f"interval must be positive: {text!r}")
    return value


def load_profile(path: Path) -> LoadProfile:
    path = Path(path)
    if not path.is_file():
        raise LoadProfileError(f"load profile not found: {path}")
    doc = yaml.safe_load(path.read_text()) or {}
    profile = parse_load(doc)
    profile.path = str(path)
    return profile


def parse_load(doc: dict[str, Any]) -> LoadProfile:
    users = doc.get("users", [100, 1000])
    users = [int(users)] if isinstance(users, (int, float)) else sorted({int(u) for u in users})
    if not users or any(u <= 0 for u in users):
        raise LoadProfileError("users must be a positive int or a list of positive ints")

    per_user: list[PerUser] = []
    for name, spec in (doc.get("per_user") or {}).items():
        if not isinstance(spec, dict) or "every" not in spec:
            raise LoadProfileError(f"per_user.{name}: need {{every: <interval>}}")
        per_user.append(PerUser(name, parse_interval(spec["every"]),
                                while_running=str(spec.get("while", "")).lower() == "running"))
    if not per_user:
        raise LoadProfileError("per_user is empty — nothing generates traffic")

    mix: list[tuple[str, float]] = []
    for item in doc.get("workflow_mix") or []:
        if not isinstance(item, dict) or "scenario" not in item:
            raise LoadProfileError("workflow_mix entries need {scenario, share}")
        mix.append((item["scenario"], float(item.get("share", 1.0))))
    if mix and abs(sum(s for _, s in mix) - 1.0) > 1e-6:
        raise LoadProfileError(f"workflow_mix shares sum to {sum(s for _, s in mix):g}, not 1")

    thresholds = dict(DEFAULT_THRESHOLDS)
    thresholds.update({k: float(v) for k, v in (doc.get("thresholds") or {}).items()})
    return LoadProfile(users=users, per_user=per_user, workflow_mix=mix, thresholds=thresholds)
