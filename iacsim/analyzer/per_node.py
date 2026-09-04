"""Analyzer: sum every hop *into* a node — "the database costs 60% overall,
across 3 queries"."""

from __future__ import annotations

from collections import defaultdict

from iacsim.core.interfaces import ANALYZERS, Analyzer
from iacsim.core.models import Finding, InfraGraph, Result


@ANALYZERS.register("per_node")
class PerNodeAnalyzer(Analyzer):
    def analyse(self, result: Result, graph: InfraGraph) -> list[Finding]:
        total = result.total_ms or 1.0
        by_node: dict[str, float] = defaultdict(float)
        visits: dict[str, int] = defaultdict(int)
        for h in result.hops:
            by_node[h.dst] += h.latency_ms
            visits[h.dst] += 1
        findings = [
            Finding(analyzer="per_node", subject=node, latency_ms=ms, share=ms / total,
                    detail=f"{visits[node]} hop(s) into {graph.nodes[node].subtype}")
            for node, ms in by_node.items()
        ]
        return sorted(findings, key=lambda f: -f.latency_ms)
