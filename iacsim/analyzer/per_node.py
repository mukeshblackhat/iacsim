"""Analyzer: sum every counted hop *into* a node, merging repeat calls —
"database.db_instance: 310 ms, 87 %, 2 calls — dominated by distance"."""

from __future__ import annotations

from collections import defaultdict

from iacsim.analyzer._common import LAYER_NAMES, LAYER_OF, counted_hops, is_wait
from iacsim.core.interfaces import ANALYZERS, Analyzer
from iacsim.core.models import Finding, InfraGraph, Result


@ANALYZERS.register("per_node")
class PerNodeAnalyzer(Analyzer):
    def analyse(self, result: Result, graph: InfraGraph) -> list[Finding]:
        total = result.total_ms or 1.0
        ms_by_node: dict[str, float] = defaultdict(float)
        calls: dict[str, int] = defaultdict(int)
        breakdown_by_node: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
        refs: dict[str, list[str]] = defaultdict(list)

        for h in counted_hops(result):
            if is_wait(h):
                continue
            ms_by_node[h.dst] += h.latency_ms
            calls[h.dst] += 1
            refs[h.dst].append(h.label)
            for cat, ms in h.breakdown.items():
                breakdown_by_node[h.dst][cat] += ms

        findings = []
        for node_id, ms in ms_by_node.items():
            node = graph.nodes.get(node_id)
            subtype = node.subtype if node else "unknown"
            dominant = max(breakdown_by_node[node_id].items(), key=lambda kv: kv[1])[0]
            layer = LAYER_OF.get(dominant, "other")
            n = calls[node_id]
            detail = (f"{n} call(s) into {subtype} — dominated by {dominant} "
                      f"({LAYER_NAMES.get(layer, layer)}, {breakdown_by_node[node_id][dominant]:,.0f} ms)")
            findings.append(Finding("per_node", node_id, round(ms, 3), ms / total, detail,
                                    refs=refs[node_id], layer=layer))
        return sorted(findings, key=lambda f: -f.latency_ms)
