"""Terminal report. One block per scenario:

    scenario, description, total, profile
    the hop table in path order (src → dst, ms, breakdown, evidence)
    shape line (hops, parallel savings, fanout, waits)
    then the top-N rows of each analyzer
"""

from __future__ import annotations

from iacsim.core.interfaces import REPORTERS, Reporter
from iacsim.core.models import Findings, HopResult, InfraGraph

TOP_N = 10
EVIDENCE_WIDTH = 60
NAME_WIDTH = 34


@REPORTERS.register("text")
class TextReporter(Reporter):
    def render(self, findings: list[Findings], graph: InfraGraph) -> str:
        self.graph = graph
        blocks = [self._scenario(f) for f in findings]
        if graph.warnings:
            blocks.append("warnings:\n" + "\n".join(f"  - {w}" for w in graph.warnings))
        return "\n".join(blocks)

    def _scenario(self, f: Findings) -> str:
        lines = [f"scenario: {f.scenario}  ({f.source})"]
        if f.description:
            lines.append(f"  {f.description}")
        lines += [f"total:    {f.total_ms:,.1f} ms", f"profile:  {' → '.join(f.profile_sources)}", ""]

        if f.hops:
            lines.append(f"  {'#':>2}  {'hop':<{NAME_WIDTH * 2 + 3}} {'ms':>9}  breakdown")
            for i, hop in enumerate(f.hops, 1):
                lines.append(self._hop_row(i, hop))
            lines.append("")
        if f.shape:
            lines.append("  shape: " + self._shape(f.shape))
            lines.append("")

        for analyzer in dict.fromkeys(x.analyzer for x in f.findings):
            lines.append(f"  [{analyzer}]")
            for x in [x for x in f.findings if x.analyzer == analyzer][:TOP_N]:
                lines.append(f"    {x.latency_ms:9.1f} ms  {x.share:5.0%}  {self._name(x.subject)}   ({x.detail})")
            lines.append("")
        if f.warnings:
            lines += ["  warnings:"] + [f"    - {w}" for w in f.warnings] + [""]
        return "\n".join(lines)

    def _hop_row(self, i: int, hop: HopResult) -> str:
        marker = " " if hop.on_critical_path else "·"
        if hop.src == hop.dst:                      # a wait
            name = f"{self._name(hop.src)}  (wait)"
        else:
            name = f"{self._name(hop.src)} → {self._name(hop.dst)}"
        breakdown = " ".join(f"{k}={v:g}" for k, v in hop.breakdown.items())
        evidence = hop.evidence if len(hop.evidence) <= EVIDENCE_WIDTH else hop.evidence[:EVIDENCE_WIDTH - 1] + "…"
        return f"{marker} {i:>2}  {name:<{NAME_WIDTH * 2 + 3}} {hop.latency_ms:9.1f}  {breakdown}\n" \
               f"       {evidence}"

    @staticmethod
    def _shape(shape: dict[str, float]) -> str:
        parts = [f"{int(shape.get('hop_count', 0))} hops"]
        if shape.get("parallel_groups"):
            parts.append(f"{int(shape['parallel_groups'])} parallel group(s) saving {shape['parallel_savings_ms']:,.1f} ms")
        if shape.get("fanout_copies"):
            parts.append(f"fan-out of {int(shape['fanout_copies'])} copies costed once")
        if shape.get("wait_ms"):
            parts.append(f"{shape['wait_ms']:,.0f} ms of deliberate waits")
        return "; ".join(parts)

    def _name(self, node_id: str) -> str:
        """InfraGraph.display_name, truncated; ' → ' subjects (analyzer hop
        labels) are handled per side."""
        if " → " in node_id:
            a, b = node_id.split(" → ", 1)
            return f"{self._name(a)} → {self._name(b)}"
        name = self.graph.display_name(node_id)
        return name if len(name) <= NAME_WIDTH else name[:NAME_WIDTH - 1] + "…"
