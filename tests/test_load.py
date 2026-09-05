"""M8: load profile parsing, Erlang-C, capacity derivation, fan-out waves, and
a hand-built graph whose saturation point is computable by hand."""

import json

import pytest

from iacsim.core.interfaces import WALKERS
from iacsim.core.models import (
    Confidence,
    Edge,
    EdgeKind,
    InfraGraph,
    Latency,
    Node,
    NodeKind,
    Placement,
    Scenario,
    Step,
)
from iacsim.core.registry import load_builtin_plugins
from iacsim.latency.profile import load_default_profile
from iacsim.simulator import capacity as cap
from iacsim.simulator.load import LoadProfile, LoadProfileError, PerUser, parse_interval, parse_load
from iacsim.simulator.traversal import ExpectedBackend, Planner, build_result, evaluate


def setup_module():
    load_builtin_plugins()


# ------------------------------------------------------------------ load.yaml

def test_intervals():
    assert parse_interval("2s") == 2.0
    assert parse_interval("10m") == 600.0
    assert parse_interval("1h") == 3600.0
    assert parse_interval("500ms") == 0.5
    assert parse_interval(3) == 3.0
    with pytest.raises(LoadProfileError):
        parse_interval("soon")
    with pytest.raises(LoadProfileError):
        parse_interval("0s")


def test_parse_load_rates_flags_and_mix():
    p = parse_load({"users": 500, "per_user": {"a": {"every": "2s", "while": "running"}, "b": {"every": "1m"}},
                    "workflow_mix": [{"scenario": "w1", "share": 0.75}, {"scenario": "w2", "share": 0.25}],
                    "thresholds": {"p99_ms": 500}})
    assert p.users == [500]
    a, b = p.per_user
    assert a.rps == 0.5 and a.while_running and b.rps == pytest.approx(1 / 60) and not b.while_running
    assert p.workflow_mix == [("w1", 0.75), ("w2", 0.25)]
    assert p.thresholds == {"p99_ms": 500.0, "utilisation": 0.8}
    assert set(p.scenario_names()) == {"a", "b", "w1", "w2"}


def test_parse_load_rejects_bad_input():
    with pytest.raises(LoadProfileError, match="sum to"):
        parse_load({"per_user": {"a": {"every": "1s"}}, "workflow_mix": [{"scenario": "w", "share": 0.5}]})
    with pytest.raises(LoadProfileError, match="per_user"):
        parse_load({"users": [10]})
    with pytest.raises(LoadProfileError, match="every"):
        parse_load({"per_user": {"a": {}}})
    with pytest.raises(LoadProfileError, match="users"):
        parse_load({"users": [0], "per_user": {"a": {"every": "1s"}}})


# ------------------------------------------------------------------ Erlang C

def test_erlang_c_known_values():
    assert cap.erlang_c(1, 0.5) == pytest.approx(0.5)          # M/M/1: P(wait) = ρ
    assert cap.erlang_c(2, 1.6) == pytest.approx(0.7111, abs=1e-4)
    assert cap.erlang_c(3, 3.0) == 1.0 and cap.erlang_c(3, 5.0) == 1.0
    assert cap.erlang_c(10, 0.0) == 0.0


def test_queue_wait_mm1_at_half_load_equals_service_time():
    r = cap.Resource("x", "x", "rds", slots=1)
    r.erlangs, r.arrivals_per_s = 0.5, 5.0          # S = 0.1 s, λ = 5/s, ρ = 0.5
    w = cap.queue_wait(r)
    assert w.mean_ms == pytest.approx(100.0) and not w.saturated and w.utilisation == pytest.approx(0.5)
    assert w.p99_ms > w.mean_ms


def test_queue_wait_saturated_is_none_not_inf():
    r = cap.Resource("x", "x", "rds", slots=2)
    r.erlangs, r.arrivals_per_s = 2.5, 25.0
    w = cap.queue_wait(r)
    assert w.saturated and w.mean_ms is None and w.p99_ms is None
    assert "Infinity" not in json.dumps(w.__dict__)


def test_huge_server_count_is_mm_infinity():
    r = cap.Resource("x", "x", "alb", rps=1_000_000)
    r.erlangs, r.arrivals_per_s = 100.0, 100_000.0   # c = rps × S = 1e6 × 0.001 = 1000 → still finite
    assert cap.queue_wait(r).mean_ms == pytest.approx(0.0, abs=1e-6)


# ------------------------------------------------------------------ capacity from attrs

def _profile():
    return load_default_profile()


def _graph():
    g = InfraGraph()
    us = Placement(region="us-east-1")
    g.add_node(Node("internet", NodeKind.EXTERNAL, "internet"))
    g.add_node(Node("gw", NodeKind.GATEWAY, "api_gateway", us, label="gw"))
    g.add_node(Node("fn", NodeKind.COMPUTE, "lambda", us, attrs={"concurrency": 10}, label="fn"))
    g.add_node(Node("bg", NodeKind.COMPUTE, "lambda", us, label="bg"))                 # unreserved
    g.add_node(Node("db", NodeKind.DATASTORE, "rds", us, attrs={"instance_class": "db.t3.medium"}, label="db"))
    g.add_node(Node("t", NodeKind.DATASTORE, "dynamodb", us, attrs={"billing_mode": "PAY_PER_REQUEST"}, label="t"))
    g.add_node(Node("tp", NodeKind.DATASTORE, "dynamodb", us,
                    attrs={"read_capacity": 100, "write_capacity": 50}, label="tp"))
    g.add_node(Node("web", NodeKind.COMPUTE, "ec2", us,
                    attrs={"instance_type": "t3.medium", "instances": 1}, label="web"))
    g.add_node(Node("svc", NodeKind.COMPUTE, "fargate", us, attrs={"instances": 3}, label="svc"))
    for src, dst, kind, ms in [("internet", "gw", EdgeKind.INVOKE, 30.0), ("gw", "fn", EdgeKind.INVOKE, 20.0),
                               ("fn", "db", EdgeKind.READ, 5.0), ("fn", "t", EdgeKind.READ, 4.0),
                               ("gw", "bg", EdgeKind.INVOKE, 20.0)]:
        e = Edge(src, dst, kind, Confidence.DECLARED, "test")
        e.latency = Latency(expected=ms, breakdown={"processing": ms})
        g.add_edge(e)
    return g


def test_resources_from_attributes_and_profile():
    res = cap.resources_for(_graph(), _profile())
    assert res["fn"].slots == 10 and "reserved_concurrent_executions=10" in res["fn"].source
    pool = res[cap.UNRESERVED_POOL]
    assert pool.slots == 1000 - 10 and pool.members == ["bg"]
    assert res["db"].slots == 400                          # db.t3.medium max_connections
    assert res["t"].rps == 40000                           # on-demand
    assert res["tp"].rps == 150                            # provisioned read + write
    assert res["web"].rps == 200 and res["svc"].rps == 900
    assert cap.resource_of("bg", res, _graph()) is pool and cap.resource_of("internet", res, _graph()) is None


# ------------------------------------------------------------------ fan-out waves

def _orchestrated_graph(concurrency):
    g = InfraGraph()
    g.add_node(Node("sm", NodeKind.ORCHESTRATOR, "step_functions",
                    attrs={"workflow": [{"state": "M", "type": "Map", "concurrency": concurrency, "body": []}]}))
    g.add_node(Node("w", NodeKind.COMPUTE, "lambda"))
    e = Edge("sm", "w", EdgeKind.INVOKE, Confidence.DECLARED, "test")
    e.latency = Latency(expected=100.0, breakdown={"processing": 100.0})
    g.add_edge(e)
    return g


@pytest.mark.parametrize("count,concurrency,waves", [(3, 5, 1), (12, 5, 3), (10, 5, 2), (7, None, 1)])
def test_fanout_costs_one_copy_per_wave(count, concurrency, waves):
    g = _orchestrated_graph(concurrency)
    plan = Planner(g, None).plan(Scenario("s", "sm", [Step(fanout=("w", count))]))
    r = build_result(plan, evaluate(plan, ExpectedBackend()), ExpectedBackend(), "expected_value")
    assert r.shape["fanout_waves"] == waves and r.shape["fanout_copies"] == count
    assert r.total_ms == pytest.approx(100.0 * waves)
    assert r.hops[0].breakdown["processing"] == pytest.approx(100.0 * waves)
    if waves > 1:
        assert f"{waves} waves" in r.hops[0].evidence


def _nested_graph(outer, inner, other_branch=None):
    """The real CDK shape: Choice → Map(outer) → Map(inner) → Task w. With
    `other_branch`, a second Choice branch encloses w in a single Map(other_branch)."""
    chain = [{"state": "Levels", "type": "Map", "concurrency": outer,
              "body": [{"state": "Nodes", "type": "Map", "concurrency": inner,
                        "body": [{"state": "Prep", "type": "Task", "target": "w", "kind": "invoke"}]}]}]
    branches = {"parallel": chain}
    if other_branch is not None:
        branches["sequential"] = [{"state": "Seq", "type": "Map", "concurrency": other_branch,
                                   "body": [{"state": "Prep2", "type": "Task", "target": "w", "kind": "invoke"}]}]
    g = InfraGraph()
    g.add_node(Node("sm", NodeKind.ORCHESTRATOR, "step_functions",
                    attrs={"workflow": [{"state": "Mode", "type": "Choice", "branches": branches}]}))
    g.add_node(Node("w", NodeKind.COMPUTE, "lambda"))
    g.add_node(Node("other", NodeKind.COMPUTE, "lambda"))
    for dst in ("w", "other"):
        e = Edge("sm", dst, EdgeKind.INVOKE, Confidence.DECLARED, "test")
        e.latency = Latency(expected=100.0, breakdown={"processing": 100.0})
        g.add_edge(e)
    return g


@pytest.mark.parametrize("count,waves", [(1, 1), (5, 1), (6, 2), (10, 2), (11, 3)])
def test_fanout_uses_the_innermost_map_under_a_choice(count, waves):
    plan = Planner(_nested_graph(1, 5), None).plan(Scenario("s", "sm", [Step(fanout=("w", count))]))
    assert plan.shape["fanout_waves"] == waves and not plan.warnings          # inner Map(5), not outer Map(1)


def test_fanout_under_choice_picks_the_innermost_map_and_warns():
    g = _nested_graph(1, 5, other_branch=1)                                   # {5} vs {1} disagree
    plan = Planner(g, None).plan(Scenario("s", "sm", [Step(fanout=("w", 10))]))
    assert plan.shape["fanout_waves"] == 2                                     # largest wins
    assert any("Map states with concurrency {1, 5}" in w and "using 5" in w for w in plan.warnings)
    pinned = Planner(g, None).plan(Scenario("s", "sm", [Step(fanout=("w", 10, 2))]))
    assert pinned.shape["fanout_waves"] == 5 and not pinned.warnings          # fanout.concurrency pins it
    assert "fanout.concurrency" in pinned.items[0].evidence
    unmapped = Planner(g, None).plan(Scenario("s", "sm", [Step(fanout=("other", 10))]))
    assert unmapped.shape["fanout_waves"] == 1                                 # no Map targets it: one wave


def test_fanout_erlangs_scale_with_copies_not_waves():
    """Capacity is per copy: 10 invocations of 100 ms each, whatever the wave count."""
    g = _orchestrated_graph(5)
    g.add_node(Node("t", NodeKind.DATASTORE, "dynamodb", attrs={"billing_mode": "PAY_PER_REQUEST"}))
    e = Edge("sm", "t", EdgeKind.WRITE, Confidence.DECLARED, "test")
    e.latency = Latency(expected=8.0, breakdown={"processing": 8.0})
    g.add_edge(e)
    walker = WALKERS.get("load")()
    scenarios = [Scenario("fan", "sm", [Step(fanout=("w", 10)), Step(fanout=("t", 10))])]
    load = LoadProfile(users=[1], per_user=[PerUser("fan", every_s=1.0)])
    r = walker.run(g, scenarios[0], price=None, profile=_profile(), scenarios=scenarios, load=load)
    assert r.shape["fanout_waves"] == 4                                         # 2 + 2: latency pays waves
    util = r.load.utilisation[1]
    pool = r.load.resources[cap.UNRESERVED_POOL]
    # λ·copies·hold/slots
    assert util[cap.UNRESERVED_POOL] == pytest.approx(1.0 * 10 * 0.100 / pool["slots"], rel=1e-3)
    # rps-capped: λ·copies/rps (stored to 4 dp)
    assert util["t"] == pytest.approx(1.0 * 10 / 40000, abs=5e-5)


def test_reserved_concurrency_zero_is_throttled_off():
    g = _graph()
    g.nodes["fn"].attrs["concurrency"] = 0
    r = _run_load(g, _load([100]))[0]
    assert r.load.utilisation[100]["fn"] is None                             # no servers: SAT, not 0 %
    assert r.load.latency[100]["saturated"] and r.load.latency[100]["saturated_by"] == ["fn"]
    assert "throttled off" in r.load.resources["fn"]["source"]
    # a saturated sweep is valid JSON: null, not inf
    assert "Infinity" not in json.dumps(r.load.to_dict(), allow_nan=False)
    from iacsim.core.interfaces import ANALYZERS
    findings = ANALYZERS.get("saturation")().analyse(r, g)
    first = next(f for f in findings if f.subject == "first_to_break")
    assert first.refs == ["fn"] and first.latency_ms == 0


def test_while_running_without_a_mix_is_a_stated_assumption():
    g = _graph()
    load = LoadProfile(users=[10], per_user=[PerUser("hit", every_s=2.0, while_running=True)])
    r = _run_load(g, load)[0]
    assert any("'while: running' with no workflow_mix" in a for a in r.load.assumptions)
    assert r.load.tail_factor == 1.3
    plain = _run_load(g, _load([10]))[0]
    assert not any("while: running" in a for a in plain.load.assumptions)


# ------------------------------------------------------------------ the load walker on a hand-built graph

def _load(users):
    return LoadProfile(users=users, per_user=[PerUser("hit", every_s=1.0)])


def _run_load(graph, load, tail_factor=1.3):
    walker = WALKERS.get("load")()
    scenarios = [Scenario("hit", "gw", [Step(node="fn"), Step(node="db"), Step(node="t")])]
    return [walker.run(graph, s, price=None, profile=_profile(), scenarios=scenarios, load=load,
                       tail_factor=tail_factor) for s in scenarios]


def test_reserved_lambda_saturates_where_the_arithmetic_says():
    # fn holds its slot for its own 20 ms + 5 ms (db) + 4 ms (t) = 29 ms per request;
    # one request/s/user → erlangs = 0.029 × U; 10 slots → ρ = 1 at U ≈ 345.
    g = _graph()
    r = _run_load(g, _load([100, 200, 400]))[0]
    util = r.load.utilisation
    assert util[100]["fn"] == pytest.approx(0.29, abs=0.01)
    assert util[200]["fn"] == pytest.approx(0.58, abs=0.01)
    assert util[400]["fn"] >= 1.0 and r.load.latency[400]["saturated"]
    assert r.load.latency[400]["saturated_by"] == ["fn"]
    assert r.load.latency[100]["expected_ms"] >= r.total_ms          # queueing only adds
    assert r.load.latency[100]["p99_ms"] >= 1.3 * r.total_ms
    assert all(util[u]["fn"] <= util[v]["fn"] for u, v in [(100, 200), (200, 400)])   # monotone
    assert r.total_ms == pytest.approx(59.0)                            # base numbers untouched


def test_load_result_is_json_clean():
    r = _run_load(_graph(), _load([100, 1000]))[0]
    text = json.dumps(r.load, default=str)
    assert "Infinity" not in text and "NaN" not in text


def test_saturation_analyzer_names_the_first_to_break():
    from iacsim.core.interfaces import ANALYZERS
    g = _graph()
    r = _run_load(g, _load([100, 200]))[0]
    findings = ANALYZERS.get("saturation")().analyse(r, g)
    first = next(f for f in findings if f.subject == "first_to_break")
    assert first.refs == ["fn"] and first.latency_ms == pytest.approx(100 / 0.29, rel=0.05)
    assert "reserved_concurrent_executions=10" in first.detail
    ceilings = [f for f in findings if f.subject == "ceiling"]
    assert not ceilings                       # fn breaks past the sweep's last U; nothing to fix yet
    r2 = _run_load(g, _load([100, 1000]))[0]
    ceilings = [f.detail for f in ANALYZERS.get("saturation")().analyse(r2, g) if f.subject == "ceiling"]
    assert any("reserved_concurrent_executions on fn from 10" in d for d in ceilings)
    assert any("provisioned concurrency" in d for d in ceilings)


def test_load_walker_needs_a_profile_and_known_scenarios(tmp_path):
    g = _graph()
    walker = WALKERS.get("load")()
    s = Scenario("hit", "gw", [Step(node="fn")])
    with pytest.raises(LoadProfileError, match="--walker load needs"):
        walker.run(g, s, price=None, profile=_profile(), scenarios=[s], load=None, root=tmp_path)
    bad = LoadProfile(users=[10], per_user=[PerUser("nope", 1.0)])
    with pytest.raises(LoadProfileError, match="unknown scenario"):
        WALKERS.get("load")().run(g, s, price=None, profile=_profile(), scenarios=[s], load=bad)


def test_while_running_uses_littles_law():
    """poll every 1 s only while a workflow (started every 100 s, lasting 20 s) runs → 20 % of the time."""
    from iacsim.simulator.walkers.load import _rates_per_user
    g = _orchestrated_graph(5)
    g.add_node(Node("gw", NodeKind.GATEWAY, "api_gateway"))
    e = Edge("gw", "sm", EdgeKind.INVOKE, Confidence.DECLARED, "t")
    e.latency = Latency(1.0, breakdown={"processing": 1.0})
    g.add_edge(e)
    planner = Planner(g, None)
    walker = WALKERS.get("load")()
    planned = {
        "start": walker._plan(Scenario("start", "gw", [Step(node="sm")]), planner, g),
        "poll": walker._plan(Scenario("poll", "gw", []), planner, g),
        # 200 waves × 100 ms = 20 s
        "run": walker._plan(Scenario("run", "sm", [Step(fanout=("w", 1000))]), planner, g),
    }
    load = LoadProfile(users=[1], per_user=[PerUser("start", 100.0), PerUser("poll", 1.0, while_running=True)],
                       workflow_mix=[("run", 1.0)])
    rates = _rates_per_user(load, planned)
    assert rates["start"] == pytest.approx(0.01) and rates["run"] == pytest.approx(0.01)
    assert rates["poll"] == pytest.approx(0.2)
