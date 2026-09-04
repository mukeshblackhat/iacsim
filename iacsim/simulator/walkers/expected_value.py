"""Deterministic walker.                                                     [M2 ✅]

Walks `scenario.steps` from `scenario.entry`, keeping `current` = the node the
request is "at". Every hop becomes a HopResult; the scenario total is the plain
sum of critical-path hops.

Pricing one hop  (src → dst)
  1. Find the edge to charge, in this order:
       a. a real edge current → dst                       (the normal case)
       b. dst is a node the request has already been at: this is the response
          leg back to a caller (`… → table → api Lambda`). Network was already
          charged by the forward hop's ×2, so only the destination's
          processing counts                                   ("response leg")
       c. a real edge caller → dst for the most recent earlier node on the
          path that has one — the request *returned* to that caller first.
          This covers "api Lambda reads table A, then table B" (the tables
          never call each other) and Step Functions, where the state machine
          invokes every worker and the previous worker does not
                                                              ("via caller")
       d. nothing: a synthetic INVOKE edge priced by the same cost rules, plus
          a warning on the Result                              ("estimated")
     In case (c) the hop is recorded from the caller, not from `current`, so
     attribution ("api → table B") is right.
  2. `distance` is one-way (see latency/rules/distance.py), so it is doubled
     for synchronous kinds (INVOKE / READ / WRITE / ROUTE) and counted once for
     one-way kinds (PUBLISH / CONSUME / PEER). Processing and cold start are
     counted once.
  3. A step's `op` ("write", "publish", …) re-prices the hop with that
     operation, so a scenario can say "this is a write" on an edge that
     inference tagged as a read.

Two conveniences
  * If the graph has an `internet → entry` edge, that hop is charged first
    (the user reaching the entry point), so totals include internet_to_edge.
  * A step that names the node the request is already at ("query the table
    again") repeats the previous hop from the same caller.

Structure
  parallel:  each branch is walked from `current`; cost = max(branch totals);
             every hop is recorded, `on_critical_path` only for the slowest
             branch; `current` is unchanged afterwards (branches rejoin).
  fanout:    one copy is priced; the copy count goes to Result.shape.
  wait_ms:   a hop-less cost (Step Functions Wait), category "wait".

Result.shape records Layer A3 — the shape of the path independent of any
number: hop count, parallel groups, ms saved by parallelism, fanout copies,
total wait — so the analyzers can attribute time to "shape".
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace
from typing import Any

from iacsim.core.interfaces import WALKERS, Walker
from iacsim.core.models import (
    Confidence,
    Edge,
    EdgeKind,
    HopResult,
    InfraGraph,
    Latency,
    Result,
    Scenario,
    Step,
)

SYNCHRONOUS = {EdgeKind.INVOKE, EdgeKind.READ, EdgeKind.WRITE, EdgeKind.ROUTE}
OP_TO_KIND = {k.value: k for k in EdgeKind}
Pricer = Callable[[Edge], Latency]


@dataclass
class _Walk:
    """Mutable state for one traversal (or one parallel branch)."""
    current: str
    visited: list[str]                     # every node the request has been at, in order
    hops: list[HopResult]
    total_ms: float = 0.0


@WALKERS.register("expected_value")
class ExpectedValueWalker(Walker):
    def run(self, graph: InfraGraph, scenario: Scenario, **options: Any) -> Result:
        self.graph = graph
        self.price: Pricer | None = options.get("price")
        self.warnings: list[str] = []
        self.shape = {"hop_count": 0, "sequential_hops": 0, "parallel_groups": 0,
                      "parallel_savings_ms": 0.0, "fanout_copies": 0, "wait_ms": 0.0}

        walk = _Walk(current=scenario.entry, visited=[scenario.entry], hops=[])
        if graph.find_edge("internet", scenario.entry) is not None:
            walk.current = "internet"
            self._hop(scenario.entry, Step(node=scenario.entry), walk)
        self._walk_steps(scenario.steps, walk)

        self.shape["hop_count"] = len(walk.hops)
        return Result(scenario=scenario.name, total_ms=round(walk.total_ms, 3), hops=walk.hops,
                      walker="expected_value", shape=self.shape, warnings=self.warnings)

    # ------------------------------------------------------------------ traversal

    def _walk_steps(self, steps: list[Step], walk: _Walk) -> None:
        for step in steps:
            if step.node is not None:
                self._hop(step.node, step, walk)
                self.shape["sequential_hops"] += 1
            elif step.fanout is not None:
                node, count = step.fanout
                self._hop(node, step, walk, suffix=f"×{count} concurrent copies, costed once")
                self.shape["fanout_copies"] += count
            elif step.parallel is not None:
                self._parallel(step.parallel, walk)
            elif step.wait_ms is not None:
                self._wait(step, walk)

    def _parallel(self, branches: list[list[Step]], walk: _Walk) -> None:
        results = []
        for branch in branches:
            sub = _Walk(current=walk.current, visited=list(walk.visited), hops=[])
            self._walk_steps(branch, sub)
            results.append(sub)

        slowest = max(range(len(results)), key=lambda i: results[i].total_ms) if results else None
        for i, sub in enumerate(results):
            for hop in sub.hops:
                hop.on_critical_path = hop.on_critical_path and i == slowest
            walk.hops.extend(sub.hops)
        if slowest is not None:
            walk.total_ms += results[slowest].total_ms
            self.shape["parallel_savings_ms"] += sum(r.total_ms for r in results) - results[slowest].total_ms
        if len(results) > 1:
            self.shape["parallel_groups"] += 1      # a single-branch group (Map replay) is not parallelism
        # branches rejoin: `current` stays where the fork happened

    def _wait(self, step: Step, walk: _Walk) -> None:
        ms = float(step.wait_ms or 0)
        walk.hops.append(HopResult(src=walk.current, dst=walk.current, latency_ms=ms,
                                   breakdown={"wait": ms}, evidence=step.note or "deliberate wait"))
        walk.total_ms += ms
        self.shape["wait_ms"] += ms

    # ------------------------------------------------------------------ one hop

    def _hop(self, dst: str, step: Step, walk: _Walk, suffix: str | None = None) -> None:
        src = walk.current
        if dst == src and walk.hops:                 # "call the same thing again"
            src = walk.hops[-1].src
            suffix = "repeat call" if not suffix else f"repeat call; {suffix}"
        edge, mode = self._edge_for(src, dst, walk.visited)
        came_from = src
        if mode == "via caller":
            src = edge.src
        if step.op:
            edge = self._reprice(edge, OP_TO_KIND.get(step.op, edge.kind))

        breakdown = self._charge(edge, mode)
        evidence = self._evidence(edge, mode, came_from)
        if suffix:
            evidence = f"{evidence}; {suffix}"
        if step.note:
            evidence = f"{evidence}; {step.note}"

        ms = sum(breakdown.values())
        walk.hops.append(HopResult(src=src, dst=dst, latency_ms=round(ms, 3),
                                   breakdown=breakdown, evidence=evidence))
        walk.total_ms += ms
        walk.current = dst
        walk.visited.append(dst)

    def _edge_for(self, src: str, dst: str, visited: list[str]) -> tuple[Edge, str]:
        """Pick the edge to charge; see module docstring for the order."""
        g = self.graph
        if (edge := g.find_edge(src, dst)) is not None:
            return edge, "direct"
        if dst in visited:
            return self._synthetic(src, dst, "response leg back to caller"), "response"
        for caller in reversed(visited):
            if caller != src and (edge := g.find_edge(caller, dst)) is not None:
                return edge, "via caller"
        self.warnings.append(f"no inferred edge {src} → {dst}; hop priced as a synthetic invoke")
        return self._synthetic(src, dst, "no inferred edge — estimated"), "estimated"

    def _synthetic(self, src: str, dst: str, why: str) -> Edge:
        edge = Edge(src=src, dst=dst, kind=EdgeKind.INVOKE, confidence=Confidence.LOW, evidence=why)
        edge.latency = self.price(edge) if self.price else Latency(expected=0.0)
        return edge

    def _reprice(self, edge: Edge, kind: EdgeKind) -> Edge:
        """Same endpoints, different operation — re-run the cost rules."""
        if kind == edge.kind:
            return edge
        repriced = replace(edge, kind=kind, evidence=f"{edge.evidence} (as {kind.value})")
        repriced.latency = self.price(repriced) if self.price else edge.latency
        return repriced

    @staticmethod
    def _charge(edge: Edge, mode: str) -> dict[str, float]:
        """Turn an edge's one-way breakdown into what this hop costs."""
        breakdown = dict(edge.latency.breakdown if edge.latency else {})
        if mode == "response":
            breakdown.pop("distance", None)          # already paid by the forward hop's ×2
        elif edge.kind in SYNCHRONOUS and "distance" in breakdown:
            breakdown["distance"] *= 2               # request + response
        return {k: round(v, 3) for k, v in breakdown.items()}

    def _evidence(self, edge: Edge, mode: str, src: str) -> str:
        if mode == "via caller":
            return f"after returning from {self._label(src)} — {edge.evidence}"
        if mode == "response":
            return f"response leg (network counted on the forward hop); {self._label(edge.dst)} handles the reply"
        return edge.evidence

    def _label(self, node_id: str) -> str:
        return self.graph.display_name(node_id)
