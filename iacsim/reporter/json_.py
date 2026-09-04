"""report.json — Findings + graph, versioned, for the future web viewer and for `diff`."""

from __future__ import annotations

import json
from dataclasses import asdict

from iacsim.core.interfaces import REPORTERS, Reporter
from iacsim.core.models import SCHEMA_VERSION, Findings, InfraGraph


@REPORTERS.register("json")
class JsonReporter(Reporter):
    def render(self, findings: list[Findings], graph: InfraGraph) -> str:
        return json.dumps(
            {"schema_version": SCHEMA_VERSION,
             "scenarios": [asdict(f) for f in findings],
             "graph": graph.to_dict()},
            indent=2, default=str,
        )
