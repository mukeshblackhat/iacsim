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
     caller. Network was charged by the forward hop's ×2 and the forward hop's
     processing (warm / handle) already includes composing the reply, so a
     response leg costs only the profile's optional `respond` key, default 0
                                                                    ("response")
  c. a real edge caller → dst for the most recent earlier node on the path that
     has one — the request *returned* to that caller first. Covers "api Lambda
     reads table A, then table B" and Step Functions, where the state machine
     invokes every worker                                          ("via caller")
  d. nothing: a synthetic edge (READ into a datastore, PUBLISH into a queue,
     INVOKE otherwise) priced by the same cost rules, plus a warning ("estimated")
In case (c) the hop is recorded from the caller, so attribution is right.

`distance` is one-way (latency/rules/distance.py), so it is doubled for
synchronous kinds (INVOKE / READ / WRITE / ROUTE) and counted once for one-way
kinds (PUBLISH / CONSUME / PEER). Processing and cold start count once. A step's
`op` re-prices the hop with that operation. If the graph has an
`internet → entry` edge, that hop is charged first. A step naming the node the
request is already at repeats the previous hop from the same caller.

Structure: parallel branches are walked from `current`, cost = max(branches),
every hop recorded, `on_critical_path` only on the slowest branch (decided on
the backend's scalar view — the mean, for samples); nodes visited inside the
branches stay "visited" after the join, so a later step back to one of them is
a response leg. Fan-out prices one copy per *wave*: `count` copies through an
orchestrator whose Map allows `c` at a time cost ceil(count / c) sequential
waves. `c` is the concurrency of the **innermost** Map that encloses a Task
targeting the fan-out node (the real CDK workflow nests `Choice → Map(1) →
Map(5)`); when different branches enclose it with different concurrencies the
largest is used and a warning says so; `fanout.concurrency` in scenarios.yaml
pins it. `EvaluatedHop.unit_cost` keeps the one-copy cost so the load walker
can charge capacity per copy. An unknown `op` is an error, never a silent
fallback. `wait_ms` is a hop-less cost in category "wait".
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from typing import Any, Protocol

from iacsim.core.models import (
    DEFAULT_KIND_FOR_TARGET,
    Confidence,
    Edge,
    EdgeKind,
    HopResult,
    InfraGraph,
    Latency,
    Profile,
    Result,
    Scenario,
    Step,
    WorkflowStep,
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
    copies: int = 1                      # fan-out: how many concurrent invocations this hop stands for
    waves: int = 1                       # fan-out: ceil(copies / Map concurrency) — sequential rounds

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
    def __init__(self, graph: InfraGraph, price: Pricer | None, profile: Profile | None = None) -> None:
        self.graph, self.price, self.profile = graph, price, profile

    def plan(self, scenario: Scenario) -> Plan:
        self.warnings: list[str] = []
        self.shape = {"sequential_hops": 0, "parallel_groups": 0, "fanout_copies": 0, "fanout_waves": 0,
                      "wait_ms": 0.0}
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
                node, count, *rest = step.fanout
                explicit = rest[0] if rest else None
                concurrency = int(explicit) if explicit else self._map_concurrency(walk.visited, node)
                waves = max(1, math.ceil(count / concurrency)) if concurrency else 1
                why = "fanout.concurrency" if explicit else "Map MaxConcurrency"
                suffix = (f"×{count} concurrent copies, costed once" if waves == 1 else
                          f"×{count} copies in {waves} waves of {concurrency} ({why})")
                self._hop(node, step, walk, suffix=suffix)
                last = _last_hop(walk.items)
                if last is not None:
                    last.copies, last.waves = count, waves
                self.shape["fanout_copies"] += count
                self.shape["fanout_waves"] += waves
            elif step.parallel is not None:
                self._parallel(step.parallel, walk)
            elif step.wait_ms is not None:
                self._wait(step, walk)

    def _parallel(self, branches: list[list[Step]], walk: _Walk) -> None:
        self._parallel_seq += 1
        group = PlannedGroup(label=f"parallel{self._parallel_seq}", branches=[])
        fork_len, subs = len(walk.visited), []
        for i, branch in enumerate(branches, 1):
            sub = _Walk(current=walk.current, visited=list(walk.visited), items=[])   # branches don't see each other
            self._walk_steps(branch, sub)
            _tag_group(sub.items, f"{group.label}/branch{i}")
            group.branches.append(sub.items)
            subs.append(sub)
        for sub in subs:                             # after the join the request *has* been everywhere the
            for node_id in sub.visited[fork_len:]:   # branches went: a later step back there is a response leg
                if node_id not in walk.visited:
                    walk.visited.append(node_id)
        walk.items.append(group)
        if len(branches) > 1:
            self.shape["parallel_groups"] += 1      # a single-branch group (Map replay) is not parallelism
        # branches rejoin: `current` stays where the fork happened

    def _map_concurrency(self, visited: list[str], dst: str) -> int | None:
        """MaxConcurrency that caps how many fan-out copies of `dst` run at once:
        the innermost Map, in the most recent orchestrator the request went
        through, that encloses a Task targeting `dst`. Branches that enclose it
        differently → the largest, with a warning. No enclosing Map → the first
        top-level Map (a fan-out the definition does not name), else None."""
        for node_id in reversed(visited):
            node = self.graph.nodes.get(node_id)
            if node is None or node.kind != "orchestrator":
                continue
            workflow = WorkflowStep.from_dicts(node.attrs.get("workflow") or [])
            found = sorted({c for c in _enclosing_map_concurrencies(workflow, dst, None) if c})
            if len(found) > 1:
                self.warnings.append(
                    f"fan-out {self.graph.display_name(dst)}: Map states with concurrency "
                    f"{{{', '.join(str(c) for c in found)}}} enclose it; using {found[-1]} — "
                    f"set fanout.concurrency in scenarios.yaml to pin")
            if found:
                return found[-1]
            for item in workflow:
                if item.type == "Map" and item.concurrency:
                    return int(item.concurrency)
            return None
        return None

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
            if step.op not in OP_TO_KIND:
                raise ValueError(f"scenario step {dst}: op '{step.op}' is not one of {sorted(OP_TO_KIND)}")
            edge = self._reprice(edge, OP_TO_KIND[step.op])

        evidence = self._evidence(edge, mode, came_from)
        if not step.op and len(edge.ops) > 1:          # more than one operation had evidence
            others = ", ".join(o.value for o in edge.ops if o != edge.kind)
            evidence = f"{evidence} (also may {others}: use op: {others})"
        if suffix:
            evidence = f"{evidence}; {suffix}"
        if step.note:
            evidence = f"{evidence}; {step.note}"

        walk.items.append(PlannedHop(src=src, dst=dst, breakdown=self._charge(edge, mode, dst), evidence=evidence))
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
        kind = self._synthetic_kind(dst)
        self.warnings.append(f"no inferred edge {src} → {dst}; hop priced as a synthetic {kind.value}")
        return self._synthetic(src, dst, "no inferred edge — estimated", kind), "estimated"

    def _synthetic_kind(self, dst: str) -> EdgeKind:
        node = self.graph.nodes.get(dst)
        return DEFAULT_KIND_FOR_TARGET.get(node.kind, EdgeKind.INVOKE) if node else EdgeKind.INVOKE

    def _synthetic(self, src: str, dst: str, why: str, kind: EdgeKind = EdgeKind.INVOKE) -> Edge:
        edge = Edge(src=src, dst=dst, kind=kind, confidence=Confidence.LOW, evidence=why)
        edge.latency = self.price(edge) if self.price else Latency(expected=0.0)
        return edge

    def _reprice(self, edge: Edge, kind: EdgeKind) -> Edge:
        """Same endpoints, different operation — re-run the cost rules."""
        if kind == edge.kind:
            return edge
        repriced = replace(edge, kind=kind, evidence=f"{edge.evidence} (as {kind.value})")
        repriced.latency = self.price(repriced) if self.price else edge.latency
        return repriced

    def _charge(self, edge: Edge, mode: str, dst: str) -> dict[str, float]:
        """Turn an edge's one-way breakdown into what this hop costs."""
        if mode == "response":
            respond = self._respond(dst)             # network and processing were paid on the forward hop
            return {"processing": round(respond, 3)} if respond else {}
        breakdown = dict(edge.latency.breakdown if edge.latency else {})
        if edge.kind in SYNCHRONOUS and "distance" in breakdown:
            breakdown["distance"] *= 2               # request + response
        return {k: round(v, 3) for k, v in breakdown.items()}

    def _respond(self, dst: str) -> float:
        """processing.<subtype>.respond for the node the request returns to (default 0)."""
        node = self.graph.nodes.get(dst)
        if node is None or self.profile is None:
            return 0.0
        return float(self.profile.processing_for(node).get("respond") or 0.0)

    def _evidence(self, edge: Edge, mode: str, src: str) -> str:
        name = self.graph.display_name
        if mode == "via caller":
            return f"after returning from {name(src)} — {edge.evidence}"
        if mode == "response":
            node = self.graph.nodes.get(edge.dst)
            subtype = node.subtype if node else "?"
            return (f"response leg — network and processing already counted on the forward hop into "
                    f"{name(edge.dst)}; respond={self._respond(edge.dst):g} (processing.{subtype}.respond)")
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


def _enclosing_map_concurrencies(items: list[WorkflowStep], dst: str, innermost: int | None) -> list[int | None]:
    """For every Task targeting `dst`, the concurrency of the innermost Map that
    encloses it (None when no Map does). Recurses through Map bodies, Parallel
    branches and Choice branches."""
    out: list[int | None] = []
    for item in items:
        if item.type == "Task" and item.target == dst:
            out.append(innermost)
        elif item.type == "Map":
            inner = int(item.concurrency) if item.concurrency else innermost
            out += _enclosing_map_concurrencies(item.body, dst, inner)
        else:
            for branch in item.sub_flows():
                out += _enclosing_map_concurrencies(branch, dst, innermost)
    return out


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
    cost: HopCost                        # what the request pays: one copy × waves
    on_critical_path: bool = True
    unit_cost: HopCost | None = None     # one copy, unscaled — what one fan-out invocation costs


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
                unit = self.b.cost(item)
                cost = _scaled(unit, item.waves)
                hops.append(EvaluatedHop(item, cost, unit_cost=unit))
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


def _scaled(cost: HopCost, waves: int) -> HopCost:
    """A fan-out hop that needs `waves` sequential rounds costs `waves` × one copy."""
    if waves <= 1:
        return cost
    return HopCost(total=cost.total * waves,
                   breakdown={k: round(v * waves, 3) for k, v in cost.breakdown.items()},
                   percentiles={k: round(v * waves, 3) for k, v in cost.percentiles.items()})


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
