"""Analyzer: for scenarios with parallel branches, which branch decided the
total. Hops not on the critical path are reported with their slack."""

from __future__ import annotations

from iacsim.core.interfaces import ANALYZERS, Analyzer
from iacsim.core.models import Finding, InfraGraph, Result


@ANALYZERS.register("critical_path")
class CriticalPathAnalyzer(Analyzer):
    def analyse(self, result: Result, graph: InfraGraph) -> list[Finding]:
        total = result.total_ms or 1.0
        on_path = [h for h in result.hops if h.on_critical_path]
        return [Finding(analyzer="critical_path", subject=f"{h.src} → {h.dst}", latency_ms=h.latency_ms,
                        share=h.latency_ms / total, detail="on critical path") for h in on_path]
