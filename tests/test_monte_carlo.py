"""Monte-Carlo walker on a hand-built graph — agreement with the deterministic
walker, tail behaviour, reproducibility, and the pure-Python sampler."""

import pytest

from iacsim.core.config import Config
from iacsim.core.interfaces import WALKERS
from iacsim.core.models import (
    Confidence,
    Edge,
    EdgeKind,
    InfraGraph,
    Node,
    NodeKind,
    Placement,
    Scenario,
    Step,
)
from iacsim.core.pipeline import cost_graph, load_profile, make_pricer
from iacsim.simulator.traversal import Planner, build_result, evaluate
from iacsim.simulator.walkers.monte_carlo import (
    MonteCarloBackend,
    NumpySampler,
    PythonSampler,
    Vec,
    make_sampler,
)

US, EU = Placement(region="us-east-1"), Placement(region="eu-west-1")


def _graph() -> InfraGraph:
    g = InfraGraph()
    g.add_node(Node("internet", NodeKind.EXTERNAL, "internet"))
    g.add_node(Node("gw", NodeKind.GATEWAY, "api_gateway", US))
    g.add_node(Node("fn", NodeKind.COMPUTE, "lambda", US))
    g.add_node(Node("fn2", NodeKind.COMPUTE, "lambda", US))
    g.add_node(Node("table", NodeKind.DATASTORE, "dynamodb", US))
    g.add_node(Node("far_db", NodeKind.DATASTORE, "rds", EU))
    for src, dst, kind in [("internet", "gw", EdgeKind.INVOKE), ("gw", "fn", EdgeKind.INVOKE),
                           ("fn", "table", EdgeKind.READ), ("fn", "far_db", EdgeKind.READ),
                           ("fn", "fn2", EdgeKind.INVOKE)]:
        g.add_edge(Edge(src, dst, kind, Confidence.HIGH, f"{src} calls {dst}"))
    return g


@pytest.fixture
def costed():
    cfg, g = Config(), _graph()
    profile = load_profile(cfg)
    cost_graph(g, profile, cfg)
    return g, profile, make_pricer(g, profile, cfg)


def _run(costed, steps, walker="monte_carlo", **opts):
    g, profile, price = costed
    return WALKERS.get(walker)().run(g, Scenario("t", "gw", steps), price=price, profile=profile, **opts)


def test_mean_agrees_with_the_deterministic_walker(costed):
    steps = [Step(node="fn"), Step(node="table"), Step(node="far_db")]
    exact = _run(costed, steps, walker="expected_value")
    mc = _run(costed, steps, samples=4000, seed=7)
    assert mc.walker == "monte_carlo" and mc.samples == 4000
    assert mc.total_ms == pytest.approx(exact.total_ms, rel=0.08)
    assert set(mc.percentiles) == {"p50", "p90", "p95", "p99"}
    assert mc.percentiles["p50"] <= mc.percentiles["p95"] <= mc.percentiles["p99"]
    assert [(h.src, h.dst) for h in mc.hops] == [(h.src, h.dst) for h in exact.hops]


def test_cold_start_makes_p99_much_larger_than_p50(costed):
    mc = _run(costed, [Step(node="fn"), Step(node="fn2")], samples=4000, seed=1)
    fn = next(h for h in mc.hops if h.dst == "fn")
    assert fn.percentiles["p99"] > 300 > fn.percentiles["p50"]          # cold 400 ms hits ~5 % of the time
    assert mc.percentiles["p99"] > 2 * mc.percentiles["p50"]
    assert fn.breakdown["cold_start"] == pytest.approx(20.0, abs=6)     # mean of the bimodal draw ≈ 0.05 × 400


def test_seed_makes_runs_reproducible(costed):
    a = _run(costed, [Step(node="fn"), Step(node="far_db")], samples=500, seed=42)
    b = _run(costed, [Step(node="fn"), Step(node="far_db")], samples=500, seed=42)
    c = _run(costed, [Step(node="fn"), Step(node="far_db")], samples=500, seed=43)
    assert a.total_ms == b.total_ms and a.percentiles == b.percentiles
    assert a.total_ms != c.total_ms


def test_parallel_hops_are_grouped_and_max_is_taken_per_sample(costed):
    mc = _run(costed, [Step(node="fn"), Step(parallel=[[Step(node="table")], [Step(node="far_db")]])],
              samples=1000, seed=3)
    groups = {h.group for h in mc.hops if h.group}
    assert groups == {"parallel1/branch1", "parallel1/branch2"}
    far = next(h for h in mc.hops if h.dst == "far_db")
    assert far.on_critical_path and not next(h for h in mc.hops if h.dst == "table").on_critical_path
    assert mc.shape["parallel_groups"] == 1 and mc.shape["parallel_savings_ms"] > 0


def test_pure_python_sampler_works_without_numpy(costed):
    g, profile, price = costed
    plan = Planner(g, price).plan(Scenario("t", "gw", [Step(node="fn"), Step(node="table")]))
    backend = MonteCarloBackend(g, profile, PythonSampler(300, seed=5))
    ev = evaluate(plan, backend)
    result = build_result(plan, ev, backend, walker="monte_carlo", samples=300)
    exact = _run(costed, [Step(node="fn"), Step(node="table")], walker="expected_value")
    assert result.total_ms == pytest.approx(exact.total_ms, rel=0.15)
    assert isinstance(ev.total, Vec)


def test_vec_arithmetic_and_percentiles():
    s = PythonSampler(4, seed=0)
    a, b = Vec([1.0, 2.0, 3.0, 4.0]), Vec([4.0, 3.0, 2.0, 1.0])
    assert (a + b).v == [5.0] * 4 and (a - b).v == [-3.0, -1.0, 1.0, 3.0] and (a * 2).v == [2.0, 4.0, 6.0, 8.0]
    assert s.maximum([a, b]).v == [4.0, 3.0, 3.0, 4.0]
    assert s.mean(a) == 2.5 and s.percentiles(a, (50, 99)) == [3.0, 4.0]
    assert s.constant(1.5).v == [1.5] * 4
    assert s.bernoulli(1.0).v == [1.0] * 4 and s.bernoulli(0.0).v == [0.0] * 4


def test_make_sampler_picks_numpy():
    """numpy is a dev dependency, so the fast path is exercised in the suite, not skipped."""
    assert isinstance(make_sampler(10, seed=1), NumpySampler)


def test_python_and_numpy_samplers_agree(costed):
    """Same graph, same path: the pure-Python fallback and the numpy sampler must
    tell the same story (means within 5 %, p99 within 10 %) — they draw from the
    same distributions with different generators, so only the statistics match."""
    g, profile, price = costed
    plan = Planner(g, price).plan(Scenario("t", "gw", [Step(node="fn"), Step(node="table"), Step(node="far_db")]))
    results = []
    for sampler in (PythonSampler(6000, seed=11), NumpySampler(6000, seed=11)):
        backend = MonteCarloBackend(g, profile, sampler)
        ev = evaluate(plan, backend)
        total_p99 = sampler.percentiles(ev.total, (99,))[0]
        results.append((build_result(plan, ev, backend, walker="monte_carlo", samples=6000).total_ms, total_p99))
    (py_mean, py_p99), (np_mean, np_p99) = results
    assert np_mean == pytest.approx(py_mean, rel=0.05)
    assert np_p99 == pytest.approx(py_p99, rel=0.10)


def test_missing_numpy_prints_one_tip_for_big_runs(monkeypatch, capsys):
    import sys as _sys

    from iacsim.simulator.walkers import monte_carlo as mc
    monkeypatch.setitem(_sys.modules, "numpy", None)      # import numpy → ImportError
    monkeypatch.setattr(mc, "_tip_shown", False)
    assert isinstance(mc.make_sampler(2000, seed=0), PythonSampler)
    assert isinstance(mc.make_sampler(2000, seed=0), PythonSampler)
    err = capsys.readouterr().err
    assert err.count(mc.NUMPY_TIP) == 1
