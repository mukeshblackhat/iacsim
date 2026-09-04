"""Terminal report: one block per scenario — total, then top-N per analyzer."""

from __future__ import annotations

from iacsim.core.interfaces import REPORTERS, Reporter
from iacsim.core.models import Findings, InfraGraph

TOP_N = 10


@REPORTERS.register("text")
class TextReporter(Reporter):
    def render(self, findings: list[Findings], graph: InfraGraph) -> str:
        lines: list[str] = []
        for f in findings:
            lines += [f"scenario: {f.scenario}", f"total:    {f.total_ms:,.1f} ms",
                      f"profile:  {' → '.join(f.profile_sources)}", ""]
            for analyzer in dict.fromkeys(x.analyzer for x in f.findings):
                lines.append(f"  [{analyzer}]")
                rows = [x for x in f.findings if x.analyzer == analyzer][:TOP_N]
                for x in rows:
                    lines.append(f"    {x.latency_ms:8.1f} ms  {x.share:5.0%}  {x.subject}   ({x.detail})")
                lines.append("")
        if graph.warnings:
            lines += ["warnings:"] + [f"  - {w}" for w in graph.warnings]
        return "\n".join(lines)
