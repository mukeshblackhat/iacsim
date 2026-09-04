"""Analyzer: where the time goes by *layer* — the A1 / A2 / A3 view of SPEC §1a.

Additive lines (shares sum to 100 % of the scenario total):
    distance      A1  network between placements
    processing    A2  time inside the destination service
    cold_start    A2  Lambda cold-start expectation
    wait          A3  deliberate Step Functions Wait states
    service       A2  roll-up of processing + cold_start   (additive=False so it is
                      not double-counted; share is still meaningful)

Informational shape lines (additive=False, share=0):
    parallel_savings   ms the parallel groups saved versus running sequentially
    hops               how many hops the request makes (and how many are sequential)
    fanout             concurrent copies that were costed once

Every `detail` says what would change the number: which hops are cross-region,
which node to move, how many calls are sequential.
"""

from __future__ import annotations

from collections import Counter, defaultdict

from iacsim.analyzer._common import LAYER_OF, counted_hops, distance_class, region_of
from iacsim.core.interfaces import ANALYZERS, Analyzer
from iacsim.core.models import Finding, InfraGraph, Result


@ANALYZERS.register("per_category")
class PerCategoryAnalyzer(Analyzer):
    def analyse(self, result: Result, graph: InfraGraph) -> list[Finding]:
        self.graph, self.total = graph, result.total_ms or 1.0
        hops = counted_hops(result)

        by_cat: dict[str, float] = defaultdict(float)
        refs: dict[str, list[str]] = defaultdict(list)
        for h in hops:
            for cat, ms in h.breakdown.items():
                by_cat[cat] += ms
                refs[cat].append(h.label)

        findings = [self._line(cat, ms, refs[cat], hops) for cat, ms in by_cat.items()]
        findings.sort(key=lambda f: -f.latency_ms)

        service = by_cat.get("processing", 0.0) + by_cat.get("cold_start", 0.0)
        if service:
            findings.append(Finding("per_category", "service", round(service, 3), service / self.total,
                                    "processing + cold_start (roll-up of the A2 lines above)",
                                    layer="A2", additive=False))
        findings += self._shape_lines(result)
        return findings

    # ------------------------------------------------------------ one additive line

    def _line(self, cat: str, ms: float, hop_refs: list[str], hops) -> Finding:
        detail = {
            "distance": self._distance_detail,
            "processing": self._processing_detail,
            "cold_start": self._cold_start_detail,
            "wait": lambda hs: f"{len(hs)} Wait state(s) — deliberate pauses in the state machine",
        }.get(cat, lambda hs: f"summed across {len(hs)} hops")
        relevant = [h for h in hops if cat in h.breakdown]
        return Finding("per_category", cat, round(ms, 3), ms / self.total, detail(relevant),
                       refs=hop_refs, layer=LAYER_OF.get(cat, "other"))

    def _distance_detail(self, hops) -> str:
        classes = Counter(distance_class(h, self.graph) for h in hops)
        parts = []
        cross = [h for h in hops if distance_class(h, self.graph) == "cross_region"]
        if cross:
            far = Counter(h.dst for h in cross)
            targets = ", ".join(
                f"{self.graph.display_name(n)} ({region_of(self.graph, n)}) ← {self.graph.display_name(cross[0].src)} "
                f"({region_of(self.graph, cross[0].src)})" for n in far)
            parts.append(f"{len(cross)} cross-region hop(s): {targets} — co-locate to remove this")
        if classes.get("internet"):
            parts.append("1 internet → edge hop (fixed cost of being reachable)")
        if classes.get("cross_az"):
            parts.append(f"{classes['cross_az']} cross-AZ hop(s)")
        if classes.get("same_region"):
            parts.append(f"{classes['same_region']} same-region hop(s) (AZ spans)")
        if classes.get("same_az"):
            parts.append(f"{classes['same_az']} same-AZ hop(s)")
        return "; ".join(parts) or f"{len(hops)} hop(s)"

    def _processing_detail(self, hops) -> str:
        per_subtype: dict[str, float] = defaultdict(float)
        for h in hops:
            per_subtype[self._subtype(h.dst)] += h.breakdown["processing"]
        top = sorted(per_subtype.items(), key=lambda kv: -kv[1])[:3]
        return "time inside services: " + ", ".join(f"{k} {v:,.0f} ms" for k, v in top)

    def _cold_start_detail(self, hops) -> str:
        fns = {self.graph.display_name(h.dst) for h in hops}
        return f"expected cold-start cost across {len(hops)} Lambda invocation(s) ({', '.join(sorted(fns))})"

    def _subtype(self, node_id: str) -> str:
        node = self.graph.nodes.get(node_id)
        return node.subtype if node else "unknown"

    # ------------------------------------------------------------ A3 shape lines

    def _shape_lines(self, result: Result) -> list[Finding]:
        shape = result.shape or {}
        out = []
        if shape.get("parallel_groups"):
            out.append(Finding("per_category", "parallel_savings", round(shape["parallel_savings_ms"], 3), 0.0,
                               f"{int(shape['parallel_groups'])} parallel group(s) saved this much versus running "
                               f"the branches one after another — not part of the total",
                               layer="A3", additive=False))
        out.append(Finding("per_category", "hops", float(shape.get("hop_count", len(result.hops))), 0.0,
                           f"{int(shape.get('sequential_hops', 0))} sequential hop(s); each is a round-trip "
                           f"that adds distance and processing", layer="A3", additive=False))
        if shape.get("fanout_copies"):
            out.append(Finding("per_category", "fanout", float(shape["fanout_copies"]), 0.0,
                               "concurrent copies costed once (Map / batch)", layer="A3", additive=False))
        return out
