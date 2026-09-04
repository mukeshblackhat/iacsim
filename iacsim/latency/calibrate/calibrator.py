"""Graph + MetricSource → a calibration result in the defaults.yaml schema.

For every node whose subtype is a MetricSource kind:
  no physical name          → skipped "no physical name"
  source.supports() False   → skipped "source does not support <kind>"
  measure() is None         → skipped "no data in window"      (defaults kept)
  else                      → complete block = defaults[subtype] ← measured, written
                              under by_label[label] when the label is unique in the
                              graph (portable across Terraform / CloudFormation),
                              else under per_resource[node.id]

Complete blocks matter: `Profile.processing_for` merges, but a file that only
says `warm: 12` reads badly and would break under older/simpler readers. Keys
the measurement did not supply are listed in `filled` so the coverage table
can say "(cold, cold_prob from defaults)". The result is a plain dict so the
writer and tests need no knowledge of sources.
"""

from __future__ import annotations

import statistics
from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from iacsim import __version__
from iacsim.core.interfaces import PROFILE_SOURCES, MetricSource
from iacsim.core.models import InfraGraph, Node

# Profile subtypes in defaults.yaml order — the writer keeps this order too.
SUBTYPE_ORDER = ("lambda", "ec2", "fargate", "dynamodb", "rds", "elasticache", "s3", "alb",
                 "api_gateway", "api_gateway_v2", "cloudfront", "sqs", "sns", "step_functions")


@dataclass
class CalibrationResult:
    profile: dict[str, Any]
    covered: list[str] = field(default_factory=list)              # node ids
    skipped: list[tuple[str, str]] = field(default_factory=list)  # (node id, reason)
    meta: dict[str, Any] = field(default_factory=dict)
    measured: dict[str, dict[str, float]] = field(default_factory=dict)  # node id → keys measured
    filled: dict[str, list[str]] = field(default_factory=dict)           # node id → keys taken from defaults


def calibrate(graph: InfraGraph, source: MetricSource, window: str, *, fmt: str) -> CalibrationResult:
    defaults = PROFILE_SOURCES.get("defaults")().load("defaults")["processing"]
    result = CalibrationResult(profile={"meta": {}, "processing": {}})
    label_counts = Counter(n.label for n in graph.nodes.values() if n.label)

    for node in sorted(graph.nodes.values(), key=lambda n: n.id):
        if node.subtype not in MetricSource.KINDS:
            continue
        reason = _skip_reason(node, source)
        if reason:
            result.skipped.append((node.id, reason))
            continue
        measured = source.measure(node.subtype, node.label, window, node.placement.region)
        if not measured:
            result.skipped.append((node.id, "no data in window"))
            continue
        subtype_defaults = defaults.get(node.subtype, {}).get("defaults", {})
        block = {**subtype_defaults, **measured}
        _write_block(result.profile["processing"], node, block, unique_label=label_counts[node.label] == 1)
        result.covered.append(node.id)
        result.measured[node.id] = dict(measured)
        result.filled[node.id] = [k for k in subtype_defaults if k not in measured]

    _set_variance(result)
    result.meta = _meta(graph, source, window, fmt, result)
    result.profile["meta"] = result.meta
    result.profile = _ordered(result.profile)
    return result


def _skip_reason(node: Node, source: MetricSource) -> str | None:
    if not node.label:
        return "no physical name"
    if not source.supports(node.subtype):
        return f"source does not support {node.subtype}"
    return None


def _write_block(processing: dict[str, Any], node: Node, block: dict[str, float], *, unique_label: bool) -> None:
    """One entry per node: by physical name when that name is unambiguous in the
    graph (so the file also serves the other IaC format), else by node id."""
    section = processing.setdefault(node.subtype, {})
    if unique_label:
        section.setdefault("by_label", {})[node.label] = dict(block)
    else:
        section.setdefault("per_resource", {})[node.id] = dict(block)


def _set_variance(result: CalibrationResult) -> None:
    """Global processing σ = median of the measured per-resource σs, when there are
    enough of them to mean anything (≥ 3). Otherwise leave defaults.yaml's value."""
    sigmas = [m["sigma"] for m in result.measured.values() if "sigma" in m]
    if len(sigmas) >= 3:
        result.profile["variance"] = {"processing_sigma": round(statistics.median(sigmas), 3)}


def _meta(graph: InfraGraph, source: MetricSource, window: str, fmt: str,
          result: CalibrationResult) -> dict[str, Any]:
    regions = Counter(n.placement.region for n in graph.nodes.values() if n.placement.region)
    return {
        "source": getattr(type(source), "registry_name", type(source).__name__),
        "window": window,
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "format": fmt,
        "region": regions.most_common(1)[0][0] if regions else None,
        "iacsim_version": __version__,
        "nodes_seen": len(result.covered) + len(result.skipped),
        "covered": len(result.covered),
        **{k: v for k, v in source.describe().items() if k not in ("source", "window")},
    }


def _ordered(profile: dict[str, Any]) -> dict[str, Any]:
    """meta → variance → processing (subtypes in defaults.yaml order)."""
    out: dict[str, Any] = {"meta": profile["meta"]}
    if "variance" in profile:
        out["variance"] = profile["variance"]
    processing = profile["processing"]
    out["processing"] = {k: processing[k] for k in SUBTYPE_ORDER if k in processing}
    out["processing"].update({k: v for k, v in processing.items() if k not in SUBTYPE_ORDER})
    return out
