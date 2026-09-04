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
          "total_ms",
          "shape":    {hop_count, sequential_hops, parallel_groups, parallel_savings_ms, fanout_copies, wait_ms},
          "profile":  {"sources": [...]},
          "hops":     [{src, dst, label, latency_ms, breakdown, evidence, on_critical_path, group}],
          "findings": {
            "per_hop":         [Finding...],   # subject = hop label
            "per_node":        [Finding...],   # subject = node id
            "per_category":    [Finding...],   # subject = distance|processing|cold_start|wait|service|parallel_savings|hops|fanout
            "critical_path":   [Finding...],   # only when parallel_groups > 0
            "recommendations": [Finding...]    # latency_ms = estimated saving
          },
          "warnings": [...]
        }
      ]
    }

    Finding = {analyzer, subject, latency_ms, share, detail, refs: [hop labels], layer: A1|A2|A3|null, additive}

Stable keys for `diff` to align on: scenario `name`; hop `label` (+ path index);
per_node `subject`; per_category `subject`; recommendation `subject`.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

from iacsim.core.interfaces import REPORTERS, Reporter
from iacsim.core.models import SCHEMA_VERSION, Findings, InfraGraph


@REPORTERS.register("json")
class JsonReporter(Reporter):
    def __init__(self, **_ignored) -> None:
        pass

    def render(self, findings: list[Findings], graph: InfraGraph) -> str:
        sources = findings[0].profile_sources if findings else []
        return json.dumps(
            {
                "schema_version": SCHEMA_VERSION,
                "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
                "profile": {"sources": sources},
                "graph": graph.to_dict(),
                "scenarios": [f.to_dict() for f in findings],
            },
            indent=2, default=str,
        )
