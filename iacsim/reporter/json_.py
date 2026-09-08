"""report.json — the machine contract consumed by `iacsim diff` (M4) and the
graph viewer (M6). Schema version 2:

    {
      "schema_version": "2",
      "generated_at":   ISO-8601 UTC,
      "profile":        {"sources": ["defaults", "team.yaml", ...]},
      "graph":          InfraGraph.to_dict()  — nodes, edges (with latency), warnings,
      "scenarios": [
        {
          "name", "description", "source" ("declared" | "inferred"),
          "total_ms",                        # the mean when sampled
          "percentiles": {p50, p90, p95, p99}, # {} unless --walker monte_carlo
          "samples":  N | null,
          "shape":    {hop_count, sequential_hops, parallel_groups, parallel_savings_ms, fanout_copies, wait_ms},
          "profile":  {"sources": [...]},
          "hops":     [{src, dst, label, latency_ms, breakdown, evidence, on_critical_path, group,
                        percentiles: {p50, p99} | {}}],
          "findings": {
            "per_hop":         [Finding...],   # subject = hop label
            "per_node":        [Finding...],   # subject = node id
            "per_category":    [Finding...],   # subject = distance|processing|cold_start|wait|service|
                                               #           parallel_savings|hops|fanout
            "critical_path":   [Finding...],   # only when parallel_groups > 0
            "recommendations": [Finding...],   # latency_ms = estimated saving
            "tail_risk":       [Finding...]    # only when sampled; latency_ms = p99 − p50
          },
          "warnings": [...],
          "load": {}                          # `--walker load` (M8): {users, rps, latency: {U: {expected_ms, p99_ms,
                                              #   saturated, saturated_by}}, utilisation: {U: {resource: ρ}},
                                              #   resources: {key: {label, subtype, slots, rps, members, source,
                                              #   erlangs_per_user, by_scenario}}, thresholds, assumptions, traffic}
        }
      ],
      "capacity": {}                          # `--walker load` only: the sweep once (users, resources, utilisation,
                                              # thresholds, assumptions, first_to_break) — the viewer / diff read this
    }

    Finding = {analyzer, subject, latency_ms, share, detail, refs: [hop labels],
               layer: A1|A2|A3|capacity|null, additive}

Stable keys for `diff` to align on: scenario `name`; hop `label` (+ path index);
per_node `subject`; per_category `subject`; recommendation `subject`.

diff.json (`render_diff`) is `DiffReport.to_dict()`:

    {
      "schema_version", "generated_at", "before", "after", "profile_sources",
      "graph":     {nodes_added, nodes_removed, nodes_moved: [{node_id, field, before, after}],
                    edges_added, edges_removed},
      "scenarios": [{name, status ("both" | "only_before" | "only_after"), description,
                     before_ms, after_ms, delta_ms, delta_pct,
                     categories: [ValueDelta], hops: [HopDelta], nodes: [ValueDelta],
                     recommendations: [{subject, status, saving_ms, detail}],
                     shape_before, shape_after}]
    }
    ValueDelta = {subject, before_ms, after_ms, before_share, after_share, layer, detail, status, delta_ms}
    HopDelta   = {label, occurrence, index_before, index_after, before_ms, after_ms,
                  breakdown_before, breakdown_after, status, delta_ms}
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

from iacsim.core.interfaces import REPORTERS, Reporter
from iacsim.core.models import SCHEMA_VERSION, Findings, InfraGraph
from iacsim.diff.models import DiffReport


@REPORTERS.register("json")
class JsonReporter(Reporter):
    def render(self, findings: list[Findings], graph: InfraGraph) -> str:
        return json.dumps(report_payload(findings, graph), indent=2, default=str, allow_nan=False)

    def render_diff(self, diff: DiffReport, before: InfraGraph, after: InfraGraph) -> str:
        payload = diff.to_dict()
        payload["generated_at"] = datetime.now(UTC).isoformat(timespec="seconds")
        return json.dumps(payload, indent=2, default=str, allow_nan=False)


def _capacity(findings: list[Findings]) -> dict:
    """The load sweep once, at the top level (it is identical on every scenario)."""
    first = next((f for f in findings if f.load and f.load.resources), None)
    if first is None:
        return {}
    load = first.load
    breaks = [f for f in first.by_analyzer().get("saturation", []) if f.subject == "first_to_break"]
    return {
        "users": load.users,
        "thresholds": load.thresholds,
        "assumptions": load.assumptions,
        "resources": load.resources,
        "utilisation": load.utilisation,
        "first_to_break": ({"resource": breaks[0].refs[0], "users": breaks[0].latency_ms, "detail": breaks[0].detail}
                           if breaks else None),
    }


def report_payload(findings: list[Findings], graph: InfraGraph) -> dict:
    """The schema-2 document as a dict — `JsonReporter` serialises it, the html
    reporter inlines it into the dashboard. Keys and their order are the contract."""
    sources = findings[0].profile_sources if findings else []
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "profile": {"sources": sources},
        "graph": graph.to_dict(),
        "scenarios": [f.to_dict() for f in findings],
        "capacity": _capacity(findings),
    }
