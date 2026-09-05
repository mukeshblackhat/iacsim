"""Analyzer: turn what the graph knows into 1–5 plain-English suggestions,
each with an estimated saving and the hops it was derived from.

Rules (all on counted hops only):
  cross_region     a datastore in a different region from its caller
                   → co-locate; saving = the cross-region distance minus a
                     cross-AZ distance (~1 ms per leg)
  batch_calls      the same caller hits the same datastore K ≥ 2 times in a row
                   → batch; saving up to (K − 1) × one call
  cold_start       Lambda cold-start expectation > 10 % of the total
                   → provisioned concurrency on the worst offenders
  parallelise      consecutive sequential hops from the same caller to
                   *different* datastores (no data dependency visible)
                   → run concurrently; saving = sum − max
  waits            Step Functions Wait states
                   → check whether the polling can be event-driven

`latency_ms` on these findings is the *estimated saving*; `share` is the
saving as a fraction of the total. Every finding cites its hops in `refs`.
"""

from __future__ import annotations

from collections import defaultdict
from itertools import groupby

from iacsim.analyzer._common import counted_hops, distance_class, is_wait, region_of
from iacsim.core.interfaces import ANALYZERS, Analyzer
from iacsim.core.models import Finding, HopResult, InfraGraph, NodeKind, Result

CROSS_AZ_LEG_MS = 1.0          # what a co-located hop would still pay, per leg
MAX_CITED_HOPS = 4             # "based on:" lists this many, then "+N more"
COLD_START_SHARE_THRESHOLD = 0.10
MAX_RECOMMENDATIONS = 5


@ANALYZERS.register("recommendations")
class RecommendationsAnalyzer(Analyzer):
    def analyse(self, result: Result, graph: InfraGraph) -> list[Finding]:
        self.graph, self.total = graph, result.total_ms or 1.0
        hops = [h for h in counted_hops(result) if not is_wait(h)]
        findings = (self._cross_region(hops) + self._batch_calls(hops) + self._cold_start(hops)
                    + self._parallelise(hops) + self._waits(result))
        findings.sort(key=lambda f: -f.latency_ms)
        return findings[:MAX_RECOMMENDATIONS]

    # ------------------------------------------------------------ rules

    def _cross_region(self, hops: list[HopResult]) -> list[Finding]:
        far: dict[tuple[str, str], list[HopResult]] = defaultdict(list)
        for h in hops:
            if distance_class(h, self.graph) == "cross_region" and self._kind(h.dst) == NodeKind.DATASTORE:
                far[(h.src, h.dst)].append(h)
        out = []
        for (src, dst), group in far.items():
            saving = sum(h.breakdown.get("distance", 0.0) - 2 * CROSS_AZ_LEG_MS for h in group)
            out.append(self._finding(
                f"co-locate {self._name(dst)} with {self._name(src)}",
                saving,
                f"{self._name(dst)} is in {region_of(self.graph, dst)} but its caller {self._name(src)} is in "
                f"{region_of(self.graph, src)}; {len(group)} call(s) each pay the cross-region round-trip. "
                f"Moving it to {region_of(self.graph, src)} leaves ~{2 * CROSS_AZ_LEG_MS:g} ms "
                "of cross-AZ distance per call",
                group))
        return out

    def _batch_calls(self, hops: list[HopResult]) -> list[Finding]:
        out = []
        for (src, dst), run in groupby(hops, key=lambda h: (h.src, h.dst)):
            run = list(run)
            if len(run) < 2 or self._kind(dst) != NodeKind.DATASTORE:
                continue
            per_call = sum(h.latency_ms for h in run) / len(run)
            out.append(self._finding(
                f"batch the {len(run)} calls from {self._name(src)} to {self._name(dst)}",
                per_call * (len(run) - 1),
                f"{len(run)} consecutive round-trips to the same {self._subtype(dst)} at ~{per_call:,.0f} ms each; "
                f"one batched query (BatchGetItem / a JOIN / a single transaction) pays the trip once",
                run))
        return out

    def _cold_start(self, hops: list[HopResult]) -> list[Finding]:
        cold = [h for h in hops if h.breakdown.get("cold_start")]
        cold_ms = sum(h.breakdown["cold_start"] for h in cold)
        if cold_ms / self.total <= COLD_START_SHARE_THRESHOLD:
            return []
        by_fn: dict[str, float] = defaultdict(float)
        for h in cold:
            by_fn[h.dst] += h.breakdown["cold_start"]
        worst = sorted(by_fn.items(), key=lambda kv: -kv[1])[:3]
        names = ", ".join(self._name(n) for n, _ in worst)
        return [self._finding(
            f"provisioned concurrency on {names}",
            cold_ms,
            f"expected cold-start cost is {cold_ms / self.total:.0%} of the total across "
            f"{len(cold)} Lambda invocation(s); provisioned concurrency (or a smaller package / SnapStart) removes it",
            cold)]

    def _parallelise(self, hops: list[HopResult]) -> list[Finding]:
        """Runs of ≥ 2 consecutive sequential hops with one caller and distinct datastore targets."""
        out = []
        run: list[HopResult] = []

        def flush() -> None:
            targets = {h.dst for h in run}
            if len(run) >= 2 and len(targets) >= 2:
                ms = [h.latency_ms for h in run]
                out.append(self._finding(
                    f"run the {len(run)} reads from {self._name(run[0].src)} in parallel",
                    sum(ms) - max(ms),
                    f"{self._name(run[0].src)} calls "
                    f"{', '.join(self._name(t) for t in dict.fromkeys(h.dst for h in run))} "
                    f"one after another and nothing in the scenario says one depends on the other; "
                    f"issuing them concurrently costs only the slowest ({max(ms):,.0f} ms)",
                    list(run)))
            run.clear()

        for h in hops:
            # writes usually depend on what was read before them; only reads are candidates
            eligible = (h.group is None and self._kind(h.dst) == NodeKind.DATASTORE
                        and "(as write)" not in h.evidence)
            if eligible and (not run or run[-1].src == h.src):
                run.append(h)
            else:
                flush()
                if eligible:
                    run.append(h)
        flush()
        return out

    def _waits(self, result: Result) -> list[Finding]:
        wait_ms = (result.shape or {}).get("wait_ms", 0.0)
        if not wait_ms:
            return []
        waits = [h for h in result.hops if is_wait(h) and h.on_critical_path]
        return [self._finding(
            "replace Step Functions Wait states with event-driven completion",
            wait_ms,
            f"{wait_ms:,.0f} ms of the total is deliberate waiting ({len(waits)} Wait state(s)); "
            f"if these poll a third party, a callback / waitForTaskToken removes the fixed delay",
            waits)]

    # ------------------------------------------------------------ helpers

    def _finding(self, title: str, saving: float, why: str, hops: list[HopResult]) -> Finding:
        cited = "; ".join(self._hop_label(h) for h in hops[:MAX_CITED_HOPS])
        if len(hops) > MAX_CITED_HOPS:
            cited += f"; +{len(hops) - MAX_CITED_HOPS} more"
        return Finding("recommendations", title, round(saving, 3), saving / self.total,
                       f"{why}. Based on: {cited}", refs=[h.label for h in hops])

    def _hop_label(self, h: HopResult) -> str:
        return f"{self._name(h.src)} → {self._name(h.dst)} ({h.latency_ms:,.0f} ms)"

    def _name(self, node_id: str) -> str:
        return self.graph.display_name(node_id)

    def _kind(self, node_id: str):
        node = self.graph.nodes.get(node_id)
        return node.kind if node else None

    def _subtype(self, node_id: str) -> str:
        node = self.graph.nodes.get(node_id)
        return node.subtype if node else "service"
