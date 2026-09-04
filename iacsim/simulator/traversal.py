"""Shared path traversal — the *structure* of a walk, independent of how hops are priced.

Both walkers share one traversal so they cannot drift apart:

  1. `Planner(graph, price).plan(scenario)` follows the scenario once and returns a
     Plan: the ordered hop structure (sequential hops, parallel groups, fan-outs,
     waits) with each hop's *expected* breakdown attached. Everything that needs
     the graph happens here — edge selection, ×2 on synchronous distance, `op`
     overrides, group labels, the static parts of Result.shape.
  2. `evaluate(plan, backend)` turns the plan into numbers: sequential hops add,
     parallel branches take the max. The Backend decides what one hop costs —
     the expected breakdown (`ExpectedBackend`, deterministic) or one draw per
     sample (the Monte-Carlo backend, where a "number" is a vector of samples).
  3. `build_result(...)` packs an Evaluation into the Result the analyzers read.

Edge selection for one hop  (src → dst), in order:
  a. a real edge current → dst                                     ("direct")
  b. dst is a node the request has already been at: the response leg back to a
     caller. Network was already charged by the forward hop's ×2, so only the
     destination's processing counts                                 ("response")
  c. a real edge caller → dst for the most recent earlier node on the path that
     has one — the request *returned* to that caller first. Covers "api Lambda
     reads table A, then table B" and Step Functions, where the state machine
     invokes every worker                                          ("via caller")
  d. nothing: a synthetic INVOKE edge priced by the same cost rules, plus a
     warning                                                        ("estimated")
In case (c) the hop is recorded from the caller, so attribution is right.

`distance` is one-way (latency/rules/distance.py), so it is doubled for
synchronous kinds (INVOKE / READ / WRITE / ROUTE) and counted once for one-way
kinds (PUBLISH / CONSUME / PEER). Processing and cold start count once. A step's
`op` re-prices the hop with that operation. If the graph has an
`internet → entry` edge, that hop is charged first. A step naming the node the
request is already at repeats the previous hop from the same caller.

Structure: parallel branches are walked from `current`, cost = max(branches),
every hop recorded, `on_critical_path` only on the slowest branch (decided on
the backend's scalar view — the mean, for samples); fan-out prices one copy;
`wait_ms` is a hop-less cost in category "wait".
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field, replace
from typing import Any, Protocol

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


# ------------------------------------------------------------------ the plan

@dataclass
class PlannedHop:
    src: str
    dst: str
    breakdown: dict[str, float]          # expected ms per category, already ×2 for sync distance
    evidence: str
    group: str | None = None             # "parallel1/branch2" inside a parallel group
    is_wait: bool = False

    @property
    def label(self) -> str:
        return f"{self.src} → {self.dst}"


@dataclass
class PlannedGroup:
    label: str                           # "parallel1"
    branches: list[list[PlanItem]]


PlanItem = PlannedHop | PlannedGroup


@dataclass
class Plan:
    scenario: str
    items: list[PlanItem]
    shape: dict[str, float]              # sequential_hops, parallel_groups, fanout_copies, wait_ms
    warnings: list[str] = field(default_factory=list)


@dataclass
class _Walk:
    """Mutable state for one traversal (or one parallel branch)."""
    current: str
    visited: list[str]                   # every node the request has been at, in order
    items: list[PlanItem]


class Planner:
    def __init__(self, graph: InfraGraph, price: Pricer | None) -> None:
        self.graph, self.price = graph, price

    def plan(self, scenario: Scenario) -> Plan:
        self.warnings: list[str] = []
        self.shape = {"sequential_hops": 0, "parallel_groups": 0, "fanout_copies": 0, "wait_ms": 0.0}
        self._parallel_seq = 0

        walk = _Walk(current=scenario.entry, visited=[scenario.entry], items=[])
        if self.graph.find_edge("internet", scenario.entry) is not None:
            walk.current = "internet"
            self._hop(scenario.entry, Step(node=scenario.entry), walk)
        self._walk_steps(scenario.steps, walk)
        return Plan(scenario.name, walk.items, self.shape, self.warnings)

    # ------------------------------------------------------------ structure

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
        self._parallel_seq += 1
        group = PlannedGroup(label=f"parallel{self._parallel_seq}", branches=[])
        for i, branch in enumerate(branches, 1):
            sub = _Walk(current=walk.current, visited=list(walk.visited), items=[])
            self._walk_steps(branch, sub)
            _tag_group(sub.items, f"{group.label}/branch{i}")
            group.branches.append(sub.items)
        walk.items.append(group)
        if len(branches) > 1:
            self.shape["parallel_groups"] += 1      # a single-branch group (Map replay) is not parallelism
        # branches rejoin: `current` stays where the fork happened

    def _wait(self, step: Step, walk: _Walk) -> None:
        ms = float(step.wait_ms or 0)
        walk.items.append(PlannedHop(src=walk.current, dst=walk.current, breakdown={"wait": ms},
                                     evidence=step.note or "deliberate wait", is_wait=True))
        self.shape["wait_ms"] += ms

    # ------------------------------------------------------------ one hop

    def _hop(self, dst: str, step: Step, walk: _Walk, suffix: str | None = None) -> None:
        src = walk.current
        if dst == src and walk.items:                # "call the same thing again"
            last = _last_hop(walk.items)
            if last is not None:
                src = last.src
            suffix = "repeat call" if not suffix else f"repeat call; {suffix}"
        edge, mode = self._edge_for(src, dst, walk.visited)
        came_from = src
        if mode == "via caller":
            src = edge.src
        if step.op:
            edge = self._reprice(edge, OP_TO_KIND.get(step.op, edge.kind))

        evidence = self._evidence(edge, mode, came_from)
        if suffix:
            evidence = f"{evidence}; {suffix}"
        if step.note:
            evidence = f"{evidence}; {step.note}"

        walk.items.append(PlannedHop(src=src, dst=dst, breakdown=self._charge(edge, mode), evidence=evidence))
        walk.current = dst
        walk.visited.append(dst)

    def _edge_for(self, src: str, dst: str, visited: list[str]) -> tuple[Edge, str]:
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
        name = self.graph.display_name
        if mode == "via caller":
            return f"after returning from {name(src)} — {edge.evidence}"
        if mode == "response":
            return f"response leg (network counted on the forward hop); {name(edge.dst)} handles the reply"
        return edge.evidence


def _tag_group(items: list[PlanItem], label: str) -> None:
    """Label every hop in a branch; nested groups keep their inner label."""
    for item in items:
        if isinstance(item, PlannedHop):
            if item.group is None:
                item.group = label
        else:
            for branch in item.branches:
                _tag_group(branch, label)


def _last_hop(items: list[PlanItem]) -> PlannedHop | None:
    for item in reversed(items):
        if isinstance(item, PlannedHop):
            return item
        for branch in reversed(item.branches):
            if (hop := _last_hop(branch)) is not None:
                return hop
    return None


# ------------------------------------------------------------------ evaluation

@dataclass
class HopCost:
    """What one hop costs in the backend's number type — a float for the
    deterministic walker, a vector of samples for Monte-Carlo — plus the
    per-category means for the hop table."""
    total: Any
    breakdown: dict[str, float]
    percentiles: dict[str, float] = field(default_factory=dict)


class Backend(Protocol):
    def zero(self) -> Any: ...
    def cost(self, hop: PlannedHop) -> HopCost: ...
    def maximum(self, values: list[Any]) -> Any: ...
    def mean(self, value: Any) -> float: ...


class ExpectedBackend:
    """Every hop costs exactly its expected breakdown."""

    def zero(self) -> float:
        return 0.0

    def cost(self, hop: PlannedHop) -> HopCost:
        return HopCost(total=sum(hop.breakdown.values()), breakdown=hop.breakdown)

    def maximum(self, values: list[float]) -> float:
        return max(values)

    def mean(self, value: float) -> float:
        return value


@dataclass
class EvaluatedHop:
    hop: PlannedHop
    cost: HopCost
    on_critical_path: bool = True


@dataclass
class Evaluation:
    hops: list[EvaluatedHop]             # in path order, branches flattened
    total: Any
    parallel_savings: Any


def evaluate(plan: Plan, backend: Backend) -> Evaluation:
    ev = _Evaluator(backend)
    total, hops = ev.items(plan.items)
    return Evaluation(hops, total, ev.savings)


class _Evaluator:
    def __init__(self, backend: Backend) -> None:
        self.b = backend
        self.savings = backend.zero()

    def items(self, items: list[PlanItem]) -> tuple[Any, list[EvaluatedHop]]:
        total, hops = self.b.zero(), []
        for item in items:
            if isinstance(item, PlannedHop):
                cost = self.b.cost(item)
                hops.append(EvaluatedHop(item, cost))
                total = total + cost.total
            else:
                total = total + self._group(item, hops)
        return total, hops

    def _group(self, group: PlannedGroup, hops: list[EvaluatedHop]) -> Any:
        results = [self.items(branch) for branch in group.branches]
        if not results:
            return self.b.zero()
        slowest = max(range(len(results)), key=lambda i: self.b.mean(results[i][0]))
        for i, (_, branch_hops) in enumerate(results):
            for h in branch_hops:
                h.on_critical_path = h.on_critical_path and i == slowest
            hops.extend(branch_hops)
        branch_max = self.b.maximum([t for t, _ in results])
        summed = self.b.zero()
        for t, _ in results:
            summed = summed + t
        self.savings = self.savings + (summed - branch_max)
        return branch_max


def build_result(plan: Plan, ev: Evaluation, backend: Backend, walker: str,
                 percentiles: dict[str, float] | None = None, samples: int | None = None) -> Result:
    hops = [
        HopResult(src=e.hop.src, dst=e.hop.dst, latency_ms=round(backend.mean(e.cost.total), 3),
                  breakdown=e.cost.breakdown, evidence=e.hop.evidence,
                  on_critical_path=e.on_critical_path, group=e.hop.group, percentiles=e.cost.percentiles)
        for e in ev.hops
    ]
    shape = dict(plan.shape)
    shape["hop_count"] = len(hops)
    shape["parallel_savings_ms"] = backend.mean(ev.parallel_savings)
    return Result(scenario=plan.scenario, total_ms=round(backend.mean(ev.total), 3), hops=hops,
                  walker=walker, percentiles=percentiles or {}, samples=samples,
                  shape=shape, warnings=list(plan.warnings))
