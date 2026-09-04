"""Analyzer: group by breakdown key — how much is *distance* vs *processing*
vs *cold_start*. This is the view that says "you have a geography problem"
vs "you have a cold-start problem"."""

from __future__ import annotations

from collections import defaultdict

from iacsim.core.interfaces import ANALYZERS, Analyzer
from iacsim.core.models import Finding, InfraGraph, Result


@ANALYZERS.register("per_category")
class PerCategoryAnalyzer(Analyzer):
    def analyse(self, result: Result, graph: InfraGraph) -> list[Finding]:
        total = result.total_ms or 1.0
        by_cat: dict[str, float] = defaultdict(float)
        for h in result.hops:
            for cat, ms in h.breakdown.items():
                by_cat[cat] += ms
        findings = [
            Finding(analyzer="per_category", subject=cat, latency_ms=ms, share=ms / total,
                    detail=f"summed across {len(result.hops)} hops")
            for cat, ms in by_cat.items()
        ]
        return sorted(findings, key=lambda f: -f.latency_ms)
