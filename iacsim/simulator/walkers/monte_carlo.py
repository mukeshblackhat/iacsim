"""Monte-Carlo walker.                                                       [M6]

Same traversal as expected_value, but each hop draws from a distribution:
  network  → lognormal around distance (σ from profile or 0.2)
  lambda   → bimodal: cold with cold_prob, else warm
  datastore→ lognormal around read/write
Runs `samples` times (default 10 000) and reports p50 / p95 / p99. Requires
numpy (pip install iacsim[montecarlo]).
"""

from __future__ import annotations

from typing import Any

from iacsim.core.interfaces import WALKERS, Walker
from iacsim.core.models import InfraGraph, Result, Scenario


@WALKERS.register("monte_carlo")
class MonteCarloWalker(Walker):
    def run(self, graph: InfraGraph, scenario: Scenario, **options: Any) -> Result:
        raise NotImplementedError("M6: Monte-Carlo walker")
