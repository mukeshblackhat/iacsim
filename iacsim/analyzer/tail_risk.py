"""Analyzer: tail risk — which hops drive p99 − p50.

Only speaks when the walker sampled (Result.samples is set); on a
deterministic run it returns nothing, so it is safe to leave on by default.

Lines
  "p99 − p50"      the scenario's own spread (share 1.0), with p50/p95/p99 in the detail
  one per hop      that hop's p99 − p50, its share of the scenario spread, and the
                   likeliest cause: a Lambda cold start (bimodal), cross-region
                   network variance, or plain service-time variance
Shares of per-hop spreads do not sum to exactly 100 % (percentiles are not
additive), so every line is `additive=False`.
"""

from __future__ import annotations

from iacsim.analyzer._common import counted_hops, distance_class
from iacsim.core.interfaces import ANALYZERS, Analyzer
from iacsim.core.models import Finding, HopResult, InfraGraph, Result

TOP_N = 5


@ANALYZERS.register("tail_risk")
class TailRiskAnalyzer(Analyzer):
    def analyse(self, result: Result, graph: InfraGraph) -> list[Finding]:
        if not result.samples or not result.percentiles:
            return []
        p = result.percentiles
        spread = p.get("p99", 0.0) - p.get("p50", 0.0)
        summary = Finding(
            "tail_risk", "p99 − p50", round(spread, 3), 1.0,
            f"p50 {p.get('p50', 0):,.1f} · p95 {p.get('p95', 0):,.1f} · p99 {p.get('p99', 0):,.1f} ms "
            f"over {result.samples:,} samples", layer="A2", additive=False)

        per_hop = []
        for h in counted_hops(result):
            hop_spread = h.percentiles.get("p99", 0.0) - h.percentiles.get("p50", 0.0)
            if hop_spread <= 0:
                continue
            per_hop.append(Finding("tail_risk", h.label, round(hop_spread, 3),
                                   hop_spread / spread if spread else 0.0, self._why(h, graph),
                                   refs=[h.label], layer=self._layer(h, graph), additive=False))
        per_hop.sort(key=lambda f: -f.latency_ms)
        return [summary, *per_hop[:TOP_N]]

    @staticmethod
    def _why(h: HopResult, graph: InfraGraph) -> str:
        p50, p99 = h.percentiles.get("p50", 0.0), h.percentiles.get("p99", 0.0)
        numbers = f"p50 {p50:,.1f} → p99 {p99:,.1f} ms"
        if h.breakdown.get("cold_start"):
            return f"Lambda cold start — bimodal: usually warm, occasionally the full init cost ({numbers})"
        if distance_class(h, graph) == "cross_region":
            return f"cross-region network variance ({numbers})"
        return f"service-time variance ({numbers})"

    @staticmethod
    def _layer(h: HopResult, graph: InfraGraph) -> str:
        if h.breakdown.get("cold_start"):
            return "A2"
        return "A1" if distance_class(h, graph) == "cross_region" else "A2"
