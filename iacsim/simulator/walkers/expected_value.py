"""Deterministic walker.                                                     [M2]

Walks scenario.steps from scenario.entry, keeping `current` = the node the
request is "at". For each step:

  node:      cost = edge(current → node).latency.expected × 2   (request + response)
             — except CONSUME / PUBLISH edges which are one-way.
  parallel:  cost = max(cost of each branch)
  fanout:    cost = single hop cost (all copies run concurrently); noted in breakdown

Total is the plain sum. Every hop is recorded as a HopResult with its
breakdown so the analyzers can attribute time.
"""

from __future__ import annotations

from typing import Any

from iacsim.core.interfaces import WALKERS, Walker
from iacsim.core.models import InfraGraph, Result, Scenario


@WALKERS.register("expected_value")
class ExpectedValueWalker(Walker):
    def run(self, graph: InfraGraph, scenario: Scenario, **options: Any) -> Result:
        raise NotImplementedError("M2: expected-value walker")
