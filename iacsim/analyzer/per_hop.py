"""Analyzer: rank individual counted hops by latency share. The simplest and
most useful view: "API GW → create_order: 412 ms (61 %)"."""

from __future__ import annotations

from iacsim.analyzer._common import counted_hops
from iacsim.core.interfaces import ANALYZERS, Analyzer
from iacsim.core.models import Finding, InfraGraph, Result


@ANALYZERS.register("per_hop")
class PerHopAnalyzer(Analyzer):
    def analyse(self, result: Result, graph: InfraGraph) -> list[Finding]:
        total = result.total_ms or 1.0
        findings = [
            Finding("per_hop", h.label, h.latency_ms, h.latency_ms / total, h.evidence, refs=[h.label])
            for h in counted_hops(result)
        ]
        return sorted(findings, key=lambda f: -f.latency_ms)
