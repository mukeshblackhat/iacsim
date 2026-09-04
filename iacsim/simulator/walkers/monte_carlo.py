"""Monte-Carlo walker.                                                       [M6 ✅]

Same traversal as expected_value (simulator/traversal.py); the difference is
the backend: every hop is drawn `samples` times, so a "number" is a vector and
parallel groups take the element-wise max. Reports the mean as `total_ms` plus
p50 / p90 / p95 / p99.

What is sampled, per breakdown category of a hop
  distance     lognormal with the expected value as its mean, σ = variance.distance_sigma
  processing   lognormal, σ = processing.<subtype>.{per_resource|defaults}.sigma
               or variance.processing_sigma
  cold_start   bimodal: with probability `cold_prob` the full `cold` cost, else 0
               (both from the destination's processing block — the same numbers
               the cold_start rule blended into the expected value)
  wait         exact
  anything else (a plugin rule's category) exact
The lognormal is parameterised so its mean equals the expected value, so the
Monte-Carlo mean agrees with the deterministic walker up to sampling noise.

numpy is optional (`pip install iacsim[montecarlo]`) and makes this ~50× faster;
without it a small pure-Python vector type is used. `seed` (config
`simulation.seed`, CLI `--seed`) makes a run reproducible.

Profile keys this walker reads — `iacsim calibrate` (M7) should write them:
  variance.distance_sigma, variance.processing_sigma,
  processing.<subtype>.defaults|per_resource.<id>.{cold, cold_prob, sigma}
"""

from __future__ import annotations

import math
import random
from typing import Any

from iacsim.core.interfaces import WALKERS, Walker
from iacsim.core.models import InfraGraph, Profile, Result, Scenario
from iacsim.simulator.traversal import HopCost, PlannedHop, Planner, build_result, evaluate

DEFAULT_SAMPLES = 10_000
DEFAULT_DISTANCE_SIGMA = 0.2
DEFAULT_PROCESSING_SIGMA = 0.3
PERCENTILES = (50, 90, 95, 99)


@WALKERS.register("monte_carlo")
class MonteCarloWalker(Walker):
    def run(self, graph: InfraGraph, scenario: Scenario, **options: Any) -> Result:
        plan = Planner(graph, options.get("price")).plan(scenario)
        samples = int(options.get("samples") or DEFAULT_SAMPLES)
        sampler = make_sampler(samples, options.get("seed"))
        backend = MonteCarloBackend(graph, options.get("profile"), sampler)
        ev = evaluate(plan, backend)
        percentiles = {f"p{q}": round(v, 3) for q, v in zip(PERCENTILES, sampler.percentiles(ev.total, PERCENTILES))}
        return build_result(plan, ev, backend, walker="monte_carlo", percentiles=percentiles, samples=samples)


class MonteCarloBackend:
    """Draws one vector of samples per hop from the profile's distributions."""

    def __init__(self, graph: InfraGraph, profile: Profile | None, sampler: Sampler) -> None:
        self.graph, self.profile, self.s = graph, profile, sampler
        variance = profile.variance if profile else {}
        self.distance_sigma = float(variance.get("distance_sigma", DEFAULT_DISTANCE_SIGMA))
        self.processing_sigma = float(variance.get("processing_sigma", DEFAULT_PROCESSING_SIGMA))

    def zero(self):
        return self.s.constant(0.0)

    def maximum(self, values):
        return self.s.maximum(values)

    def mean(self, value) -> float:
        return self.s.mean(value)

    def cost(self, hop: PlannedHop) -> HopCost:
        parts = {key: self._draw(hop, key, ms) for key, ms in hop.breakdown.items()}
        total = self.zero()
        for v in parts.values():
            total = total + v
        p50, p99 = self.s.percentiles(total, (50, 99))
        return HopCost(total=total,
                       breakdown={k: round(self.s.mean(v), 3) for k, v in parts.items()},
                       percentiles={"p50": round(p50, 3), "p99": round(p99, 3)})

    def _draw(self, hop: PlannedHop, key: str, ms: float):
        if ms <= 0 or hop.is_wait:
            return self.s.constant(ms)
        if key == "distance":
            return self.s.lognormal(ms, self.distance_sigma)
        if key == "processing":
            return self.s.lognormal(ms, self._processing_sigma(hop.dst))
        if key == "cold_start":
            cold, prob = self._cold_start(hop.dst)
            if cold is not None:
                return self.s.bernoulli(prob) * cold
        return self.s.constant(ms)

    def _processing_block(self, node_id: str) -> dict[str, Any]:
        node = self.graph.nodes.get(node_id)
        return self.profile.processing_for(node) if (node and self.profile) else {}

    def _processing_sigma(self, node_id: str) -> float:
        return float(self._processing_block(node_id).get("sigma", self.processing_sigma))

    def _cold_start(self, node_id: str) -> tuple[float | None, float]:
        block = self._processing_block(node_id)
        cold, prob = block.get("cold"), block.get("cold_prob")
        if cold is None or prob is None:
            return None, 0.0
        return float(cold), float(prob)


# ------------------------------------------------------------------ samplers

class Sampler:
    """Vector arithmetic + distributions over `n` samples. Two implementations:
    numpy arrays, or `Vec` (a thin list wrapper) when numpy is not installed."""

    def __init__(self, n: int, seed: int | None) -> None:
        self.n, self.seed = n, seed

    def constant(self, value: float): ...
    def lognormal(self, mean: float, sigma: float): ...
    def bernoulli(self, p: float): ...
    def maximum(self, values: list): ...
    def mean(self, value) -> float: ...
    def percentiles(self, value, qs) -> list[float]: ...


def make_sampler(n: int, seed: int | None) -> Sampler:
    try:
        import numpy  # noqa: F401
        return NumpySampler(n, seed)
    except ImportError:
        return PythonSampler(n, seed)


def _lognormal_mu(mean: float, sigma: float) -> float:
    """log-space mean so that E[X] == mean."""
    return math.log(mean) - sigma * sigma / 2


class NumpySampler(Sampler):
    def __init__(self, n: int, seed: int | None) -> None:
        super().__init__(n, seed)
        import numpy as np
        self.np = np
        self.rng = np.random.default_rng(seed)

    def constant(self, value: float):
        return self.np.full(self.n, float(value))

    def lognormal(self, mean: float, sigma: float):
        return self.rng.lognormal(_lognormal_mu(mean, sigma), sigma, self.n)

    def bernoulli(self, p: float):
        return (self.rng.random(self.n) < p).astype(float)

    def maximum(self, values: list):
        return self.np.maximum.reduce(values)

    def mean(self, value) -> float:
        return float(self.np.mean(value))

    def percentiles(self, value, qs) -> list[float]:
        return [float(x) for x in self.np.percentile(value, list(qs))]


class Vec:
    """Pure-Python sample vector: just enough arithmetic for the evaluator."""
    __slots__ = ("v",)

    def __init__(self, values: list[float]) -> None:
        self.v = values

    def __add__(self, other: Vec) -> Vec:
        return Vec([a + b for a, b in zip(self.v, other.v)])

    def __sub__(self, other: Vec) -> Vec:
        return Vec([a - b for a, b in zip(self.v, other.v)])

    def __mul__(self, scalar: float) -> Vec:
        return Vec([a * scalar for a in self.v])


class PythonSampler(Sampler):
    def __init__(self, n: int, seed: int | None) -> None:
        super().__init__(n, seed)
        self.rng = random.Random(seed)

    def constant(self, value: float) -> Vec:
        return Vec([float(value)] * self.n)

    def lognormal(self, mean: float, sigma: float) -> Vec:
        mu = _lognormal_mu(mean, sigma)
        return Vec([self.rng.lognormvariate(mu, sigma) for _ in range(self.n)])

    def bernoulli(self, p: float) -> Vec:
        return Vec([1.0 if self.rng.random() < p else 0.0 for _ in range(self.n)])

    def maximum(self, values: list[Vec]) -> Vec:
        return Vec([max(xs) for xs in zip(*(v.v for v in values))])

    def mean(self, value: Vec) -> float:
        return sum(value.v) / len(value.v)

    def percentiles(self, value: Vec, qs) -> list[float]:
        ordered = sorted(value.v)
        last = len(ordered) - 1
        return [ordered[round(q / 100 * last)] for q in qs]
