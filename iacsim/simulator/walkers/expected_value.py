"""Deterministic walker.                                                     [M2 ✅]

Every hop costs exactly its expected breakdown; sequential hops add, parallel
branches take the max. The traversal itself — which edge a hop charges, the ×2
on synchronous distance, `op` overrides, parallel / fan-out / wait handling and
Result.shape — lives in simulator/traversal.py and is shared with the
Monte-Carlo walker; this class only supplies the "use the expected value"
backend.
"""

from __future__ import annotations

from typing import Any

from iacsim.core.interfaces import WALKERS, Walker
from iacsim.core.models import InfraGraph, Result, Scenario
from iacsim.simulator.traversal import ExpectedBackend, Planner, build_result, evaluate


@WALKERS.register("expected_value")
class ExpectedValueWalker(Walker):
    def run(self, graph: InfraGraph, scenario: Scenario, **options: Any) -> Result:
        plan = Planner(graph, options.get("price")).plan(scenario)
        backend = ExpectedBackend()
        return build_result(plan, evaluate(plan, backend), backend, walker="expected_value")
