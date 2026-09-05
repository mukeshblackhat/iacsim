"""Load walker — "how many users until it breaks".                        [M8]

Analytic, not discrete-event: fast, deterministic, and every number can be
traced to a formula. For each user count U in the sweep:

  1. arrivals   λ_s = U × rps per user for each scenario in load.yaml
                (`while: running` scaled by Little's law; mix scenarios get the
                workflow start rate × share).
  2. hold time  plan every scenario once with the *expected* backend (no
                contention) and, per hop, decide which resource it occupies and
                for how long:
                  Lambda   the whole invocation — its own hop plus every hop it
                           makes before returning (all hops with src == that
                           Lambda). A Lambda holds its concurrency slot while it
                           waits on DynamoDB / S3 / FAL — that is the point.
                  others   the hop's own service time (processing + cold start).
                Fan-out copies count `copies` times (one per Map item).
  3. offered    per resource: a = Σ_s λ_s × Σ_visits hold_s ; ρ = a / servers
                (simulator/capacity.py).
  4. waits      Erlang-C mean and p99 queue wait per resource; ρ ≥ 1 → saturated.
  5. latency    re-evaluate each plan with a backend that adds the mean wait of
                the destination's resource to every hop — parallel branches still
                take the max — giving expected latency at U. p99 at U =
                tail_factor × the no-contention expected + Σ p99 queue waits on
                the critical path (tail_factor: config simulation.tail_factor).

Utilisation is linear in U, so the users at which ρ reaches 1 is exact:
U* = U / ρ(U). The saturation analyzer reads that from Result.load.

One Result per scenario (not per U): total_ms / hops stay the no-contention
numbers the other analyzers expect; the sweep lives in Result.load. The walker
needs *all* scenarios to share resources, so the pipeline passes them in
`options["scenarios"]` and the sweep is computed once per walker instance.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from iacsim.core.interfaces import WALKERS, Walker
from iacsim.core.models import InfraGraph, NodeKind, Profile, Result, Scenario
from iacsim.simulator.capacity import Resource, Wait, queue_wait, resource_of, resources_for
from iacsim.simulator.load import LoadProfile, LoadProfileError, load_profile
from iacsim.simulator.traversal import (
    Evaluation,
    ExpectedBackend,
    HopCost,
    Plan,
    PlannedHop,
    Planner,
    build_result,
    evaluate,
)

DEFAULT_TAIL_FACTOR = 1.3


@dataclass
class _Visit:
    """One occupancy of a resource by one scenario: hold seconds × copies."""
    resource: str
    hold_s: float
    copies: int = 1


@dataclass
class _Planned:
    scenario: Scenario
    plan: Plan
    base: Evaluation
    visits: list[_Visit] = field(default_factory=list)
    starts_workflow: bool = False

    def base_total_s(self) -> float:
        return float(self.base.total) / 1000.0


@WALKERS.register("load")
class LoadWalker(Walker):
    def __init__(self) -> None:
        self._sweep: dict[str, Any] | None = None

    def run(self, graph: InfraGraph, scenario: Scenario, **options: Any) -> Result:
        if self._sweep is None:
            self._sweep = self._compute(graph, options)
        base = self._sweep["planned"][scenario.name]
        backend = ExpectedBackend()
        result = build_result(base.plan, base.base, backend, walker="load")
        result.load = self._sweep["per_scenario"].get(scenario.name, {"users": self._sweep["users"], "traffic": False})
        result.load["resources"] = self._sweep["resources"]
        result.load["utilisation"] = self._sweep["utilisation"]
        result.load["thresholds"] = self._sweep["thresholds"]
        result.load["assumptions"] = self._sweep["assumptions"]
        return result

    # ------------------------------------------------------------ the sweep

    def _compute(self, graph: InfraGraph, options: dict[str, Any]) -> dict[str, Any]:
        scenarios: list[Scenario] = options.get("scenarios") or []
        profile: Profile | None = options.get("profile")
        load = _load_from_options(options)
        tail_factor = float(options.get("tail_factor") or DEFAULT_TAIL_FACTOR)

        planner = Planner(graph, options.get("price"), profile)
        planned = {s.name: self._plan(s, planner, graph) for s in scenarios}
        missing = [n for n in load.scenario_names() if n not in planned]
        if missing:
            raise LoadProfileError(f"load profile names unknown scenario(s): {', '.join(missing)}")

        rps_per_user = _rates_per_user(load, planned)
        resources = resources_for(graph, profile) if profile else {}
        utilisation: dict[int, dict[str, float]] = {}
        per_scenario: dict[str, dict[str, Any]] = {
            name: {"users": load.users, "rps": {}, "latency": {}, "traffic": name in rps_per_user}
            for name in planned}

        for users in load.users:
            rates = {name: users * rps for name, rps in rps_per_user.items()}
            _offer(resources, planned, rates, graph)
            waits = {key: queue_wait(r) for key, r in resources.items()}
            utilisation[users] = {key: round(w.utilisation, 4) for key, w in waits.items()}
            for name, p in planned.items():
                per_scenario[name]["rps"][users] = round(rates.get(name, 0.0), 4)
                per_scenario[name]["latency"][users] = _latency_at(p, waits, resources, graph, tail_factor)

        return {
            "users": load.users, "planned": planned, "utilisation": utilisation, "per_scenario": per_scenario,
            "thresholds": load.thresholds,
            # by_scenario shares are U-independent (everything is linear in U); taken at the last U
            "resources": {key: r.to_dict() | {"erlangs_per_user": _per_user_erlangs(r, load.users[-1]),
                                              "by_scenario": {n: round(e, 6) for n, e in r.by_scenario.items()}}
                          for key, r in resources.items()},
            "assumptions": [
                "M/M/c queueing per resource (Poisson arrivals, exponential service)",
                "a Lambda holds its concurrency slot for its whole invocation, including downstream calls",
                "account Lambda concurrency from the profile (capacity.lambda.account_concurrency)",
                f"p99 = {tail_factor:g} × no-contention expected + p99 queue waits on the critical path",
            ],
        }

    def _plan(self, scenario: Scenario, planner: Planner, graph: InfraGraph) -> _Planned:
        plan = planner.plan(scenario)
        base = evaluate(plan, ExpectedBackend())
        p = _Planned(scenario, plan, base)
        p.visits = _visits(base, graph)
        p.starts_workflow = any(_kind(graph, e.hop.dst) == NodeKind.ORCHESTRATOR for e in base.hops)
        return p


# ------------------------------------------------------------------ helpers

def _load_from_options(options: dict[str, Any]) -> LoadProfile:
    load = options.get("load")
    if isinstance(load, LoadProfile):
        return load
    root = Path(options.get("root") or ".")
    if not load:
        raise LoadProfileError("--walker load needs a load profile: --load load.yaml (config simulation.load)")
    return load_profile(root / load if not Path(load).is_absolute() else Path(load))


def _kind(graph: InfraGraph, node_id: str):
    node = graph.nodes.get(node_id)
    return node.kind if node else None


def _visits(base: Evaluation, graph: InfraGraph) -> list[_Visit]:
    """Which resource each hop occupies and for how long (seconds)."""
    lambda_span: dict[str, float] = defaultdict(float)     # node id → ms it is busy per scenario run
    lambda_copies: dict[str, int] = defaultdict(int)
    visits: list[_Visit] = []
    for e in base.hops:
        hop, ms = e.hop, e.cost.total
        if hop.is_wait:
            continue
        dst_node = graph.nodes.get(hop.dst)
        if dst_node is not None and dst_node.subtype == "lambda":
            lambda_span[hop.dst] += ms                     # its own invocation
            lambda_copies[hop.dst] += hop.copies
        elif dst_node is not None and dst_node.kind not in (NodeKind.NETWORK, NodeKind.EXTERNAL):
            service = sum(v for k, v in e.cost.breakdown.items() if k not in ("distance", "transition"))
            visits.append(_Visit(hop.dst, service / 1000.0, hop.copies))
        src_node = graph.nodes.get(hop.src)
        if src_node is not None and src_node.subtype == "lambda" and hop.dst != hop.src:
            lambda_span[hop.src] += ms                     # time the Lambda spends waiting downstream
    for node_id, span in lambda_span.items():
        copies = max(1, lambda_copies.get(node_id, 1))
        visits.append(_Visit(node_id, span / 1000.0 / copies, copies))
    return visits


def _rates_per_user(load: LoadProfile, planned: dict[str, _Planned]) -> dict[str, float]:
    """Requests per second per user, per scenario, after `while: running` and the mix."""
    start_rps = sum(p.rps for p in load.per_user if planned[p.scenario].starts_workflow)
    mean_duration_s = sum(share * planned[name].base_total_s() for name, share in load.workflow_mix) \
        if load.workflow_mix else 0.0
    active = min(1.0, start_rps * mean_duration_s) if mean_duration_s else 1.0

    rates: dict[str, float] = {}
    for p in load.per_user:
        rates[p.scenario] = rates.get(p.scenario, 0.0) + p.rps * (active if p.while_running else 1.0)
    for name, share in load.workflow_mix:
        rates[name] = rates.get(name, 0.0) + start_rps * share
    return rates


def _offer(resources: dict[str, Resource], planned: dict[str, _Planned], rates: dict[str, float],
           graph: InfraGraph) -> None:
    for r in resources.values():
        r.erlangs, r.arrivals_per_s, r.by_scenario = 0.0, 0.0, {}
    for name, p in planned.items():
        rate = rates.get(name, 0.0)
        if rate <= 0:
            continue
        for v in p.visits:
            r = resource_of(v.resource, resources, graph)
            if r is None:
                continue
            erl = rate * v.copies * v.hold_s
            r.erlangs += erl
            r.arrivals_per_s += rate * v.copies
            r.by_scenario[name] = r.by_scenario.get(name, 0.0) + erl


def _per_user_erlangs(r: Resource, users: int) -> float:
    return round(r.erlangs / users, 6) if users else 0.0


class _ContendedBackend(ExpectedBackend):
    """Expected cost plus the mean queue wait of the hop's resource."""

    def __init__(self, waits: dict[str, Wait], resources: dict[str, Resource], graph: InfraGraph) -> None:
        self.waits, self.resources, self.graph = waits, resources, graph

    def cost(self, hop: PlannedHop) -> HopCost:
        base = super().cost(hop)
        wait = self.wait_for(hop)
        if wait is None or hop.is_wait:
            return base
        return HopCost(total=base.total + wait, breakdown=base.breakdown | {"queue": round(wait, 3)})

    def wait_for(self, hop: PlannedHop) -> float | None:
        r = resource_of(hop.dst, self.resources, self.graph)
        if r is None:
            return 0.0
        w = self.waits.get(r.key)
        return None if (w is None or w.saturated) else w.mean_ms


def _latency_at(p: _Planned, waits: dict[str, Wait], resources: dict[str, Resource], graph: InfraGraph,
                tail_factor: float) -> dict[str, Any]:
    backend = _ContendedBackend(waits, resources, graph)
    touched = {key for key in _resources_on_path(p, resources, graph)}
    saturated = sorted(k for k in touched if waits[k].saturated)
    if saturated:
        labels = [resources[k].label for k in saturated]
        return {"expected_ms": None, "p99_ms": None, "saturated": True, "saturated_by": labels}
    ev = evaluate(p.plan, backend)
    p99_queue = sum((waits[resource_of(e.hop.dst, resources, graph).key].p99_ms or 0.0)
                    for e in ev.hops if e.on_critical_path and not e.hop.is_wait
                    and resource_of(e.hop.dst, resources, graph) is not None)
    expected = round(backend.mean(ev.total), 3)
    return {"expected_ms": expected, "p99_ms": round(tail_factor * p.base.total + p99_queue, 3),
            "saturated": False, "saturated_by": []}


def _resources_on_path(p: _Planned, resources: dict[str, Resource], graph: InfraGraph) -> set[str]:
    keys = set()
    for v in p.visits:
        r = resource_of(v.resource, resources, graph)
        if r is not None:
            keys.add(r.key)
    return keys

