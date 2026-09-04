"""Analyzer: for scenarios with parallel groups, which branch decided the
total and how much *slack* the other branches have (how much slower they
could get before they matter). Emits nothing when there is no parallel group —
the hop table already covers a purely sequential path.

Branches are identified by HopResult.group ("parallel1/branch2"), set by the
walker.
"""

from __future__ import annotations

from collections import defaultdict

from iacsim.core.interfaces import ANALYZERS, Analyzer
from iacsim.core.models import Finding, HopResult, InfraGraph, Result


@ANALYZERS.register("critical_path")
class CriticalPathAnalyzer(Analyzer):
    def analyse(self, result: Result, graph: InfraGraph) -> list[Finding]:
        if not (result.shape or {}).get("parallel_groups"):
            return []
        total = result.total_ms or 1.0
        branches: dict[str, list[HopResult]] = defaultdict(list)
        for h in result.hops:
            if h.group:
                branches[h.group].append(h)

        by_group: dict[str, dict[str, list[HopResult]]] = defaultdict(dict)
        for label, hops in branches.items():
            group, branch = label.split("/", 1)
            by_group[group][branch] = hops

        findings = []
        for group, its_branches in by_group.items():
            totals = {b: sum(h.latency_ms for h in hops) for b, hops in its_branches.items()}
            critical_ms = max(totals.values())
            tied = sum(1 for t in totals.values() if t == critical_ms)
            for branch, hops in its_branches.items():
                path = " → ".join([graph.display_name(hops[0].src)] + [graph.display_name(h.dst) for h in hops])
                slack = critical_ms - totals[branch]
                if slack == 0:
                    detail = f"{group}: critical branch — it sets the group's cost"
                    if tied > 1:
                        detail += f" (tied with {tied - 1} other branch(es); speeding up one alone changes nothing)"
                else:
                    detail = f"{group}: {slack:,.1f} ms of slack — could be that much slower before the total moves"
                findings.append(Finding("critical_path", path, round(totals[branch], 3),
                                        totals[branch] / total if slack == 0 else 0.0, detail,
                                        refs=[h.label for h in hops], layer="A3", additive=(slack == 0)))
        return findings
