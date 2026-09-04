"""`iacsim calibrate` — replace guessed latency numbers with measured ones.

    graph  ──► for every Lambda / table / LB / API node ──► MetricSource.measure(kind, physical name)
           ──► complete per-resource block (defaults ← measured) ──► profile YAML (defaults.yaml schema)

The metric source is the company-specific part and is chosen purely by config:
`calibrate.source: cloudwatch` plus `calibrate.sources.cloudwatch: {region, aws_profile}`.
Any monitoring system can be plugged in from ./plugins by registering a
MetricSource — the calibrator and writer never import a concrete source.
Only cloudwatch.py imports boto3, lazily.
"""

from __future__ import annotations

from pathlib import Path

from iacsim.core.config import Config
from iacsim.core.interfaces import (  # noqa: F401  (re-export)
    METRIC_SOURCES,
    MetricSource,
    MetricSourceError,
)


def make_metric_source(cfg: Config, root: Path | None = None) -> MetricSource:
    """`calibrate.source` → registered class, constructed with the
    `calibrate.sources.<name>` options (None values dropped so class defaults win),
    then `prepare(root)` so the source can resolve paths relative to the config.
    Same idiom as per-parser options in core/pipeline.py."""
    name = cfg.get("calibrate.source")
    cls = METRIC_SOURCES.get(name)
    options = {k: v for k, v in (cfg.get(f"calibrate.sources.{name}") or {}).items() if v is not None}
    source = cls(**options)
    source.prepare(root or (cfg.path.parent if cfg.path else Path.cwd()))
    return source


from iacsim.latency.calibrate import cloudwatch, fake  # noqa: F401  (register built-ins)
