"""Analyzer: rank individual hops by latency share. The simplest and most
useful view: "API GW → create_order: 412 ms (61%)"."""

from __future__ import annotations

from iacsim.core.interfaces import ANALYZERS, Analyzer
from iacsim.core.models import Finding, InfraGraph, Result


@ANALYZERS.register("per_hop")
class PerHopAnalyzer(Analyzer):
    def analyse(self, result: Result, graph: InfraGraph) -> list[Finding]:
        total = result.total_ms or 1.0
        findings = [
            Finding(analyzer="per_hop", subject=f"{h.src} → {h.dst}", latency_ms=h.latency_ms,
                    share=h.latency_ms / total, detail=h.evidence)
            for h in result.hops
        ]
        return sorted(findings, key=lambda f: -f.latency_ms)
