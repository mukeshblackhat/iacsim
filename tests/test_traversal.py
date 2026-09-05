"""The pricing contract, hop mode by hop mode, on the shared tiny_graph:

    forward sync   2×distance + processing (op key, else the subtype's invoke key) + cold start
    response leg   only `respond` (default 0) — never distance, never cold start
    out of an orchestrator   + one `transition`
    unknown region           priced as same-region, but warned once per node
"""

from conftest import tiny_graph

from iacsim.core.config import Config
from iacsim.core.interfaces import WALKERS
from iacsim.core.models import Placement, Scenario, Step
from iacsim.core.pipeline import cost_graph, load_profile, make_pricer
from iacsim.simulator.traversal import Planner
from iacsim.simulator.walkers.monte_carlo import MonteCarloBackend, make_sampler


def _costed(profile=None, **graph_overrides):
    cfg, g = Config(), tiny_graph(**graph_overrides)
    profile = profile or load_profile(cfg)
    cost_graph(g, profile, cfg)
    return g, profile, make_pricer(g, profile, cfg)


def _run(costed, entry, steps):
    g, profile, price = costed
    return WALKERS.get("expected_value")().run(g, Scenario("t", entry, steps), price=price, profile=profile)


def _hop(r, src, dst, nth=0):
    return [h for h in r.hops if h.src == src and h.dst == dst][nth]


def test_response_leg_charges_only_respond():
    g, p, price = c = _costed()
    r = _run(c, "gw", [Step(node="fn"), Step(node="table"), Step(node="fn")])
    back = _hop(r, "table", "fn")
    assert back.breakdown == {}
    assert "already counted on the forward hop" in back.evidence

    p.processing["lambda"]["defaults"]["respond"] = 2.0
    r = _run((g, p, price), "gw", [Step(node="fn"), Step(node="table"), Step(node="fn")])
    assert _hop(r, "table", "fn").breakdown == {"processing": 2.0}


def test_transition_is_charged_on_every_hop_out_of_an_orchestrator():
    c = _costed()
    T = c[1].processing["step_functions"]["defaults"]["transition"]
    r = _run(c, "fn", [Step(node="sfn"), Step(node="w1"), Step(node="table"), Step(node="w2")])
    into_sfn, w1, table, w2 = r.hops[-4:]
    assert (into_sfn.src, into_sfn.dst, w1.dst, table.dst, w2.dst) == ("fn", "sfn", "w1", "table", "w2")
    assert into_sfn.breakdown["processing"] == T                      # StartExecution, as before
    assert [h.breakdown.get("transition") for h in (w1, table, w2)] == [T, None, T]
    assert w2.src == "sfn"                                            # via caller: the state machine invokes w2


def test_route_into_compute_charges_handle():
    c = _costed()
    handle = c[1].processing["ec2"]["defaults"]["handle"]
    r = _run(c, "lb", [Step(node="web")])
    assert _hop(r, "lb", "web").breakdown["processing"] == handle     # ec2 has no `route` key → invoke key


def test_synthetic_hop_into_a_table_is_a_read():
    c = _costed()
    read = c[1].processing["dynamodb"]["defaults"]["read"]
    r = _run(c, "gw", [Step(node="table")])                            # gw has no edge to table
    assert _hop(r, "gw", "table").breakdown["processing"] == read
    assert len(r.warnings) == 1 and "synthetic read" in r.warnings[0]


def test_unknown_region_warns_once_per_node():
    c = _costed(placements={"table": Placement()})
    g, p, _ = c
    unknown = p.distance["same_region_unknown_az"]
    r = _run(c, "gw", [Step(node="fn"), Step(node="table"), Step(node="table"), Step(node="table")])
    assert all(h.breakdown["distance"] == 2 * unknown for h in r.hops if h.dst == "table")
    warnings = [w for w in g.warnings if "region unknown" in w]
    assert len(warnings) == 1 and warnings[0].startswith("table:")


def test_bad_op_is_an_error():
    import pytest
    c = _costed()
    with pytest.raises(ValueError, match="op 'wirte' is not one of"):
        _run(c, "gw", [Step(node="fn"), Step(node="table", op="wirte")])


def test_bad_op_in_scenarios_yaml_is_an_error_with_a_hint(tmp_path):
    import pytest

    from iacsim.scenarios.yaml_file import YamlScenarioSource
    g = tiny_graph()
    (tmp_path / "scenarios.yaml").write_text("t:\n  entry: gw\n  steps:\n    - node: table\n      op: wirte\n")
    with pytest.raises(ValueError, match="op 'wirte' is not one of .*did you mean 'write'"):
        YamlScenarioSource().load(g, tmp_path)


def test_parallel_branch_visits_are_merged():
    c = _costed()
    r = _run(c, "gw", [Step(node="fn"), Step(parallel=[[Step(node="table")], [Step(node="sfn"), Step(node="w1")]]),
                       Step(node="w1")])
    back = _hop(r, "fn", "w1", nth=-1)
    assert "response leg" in back.evidence and back.breakdown == {}          # w1 was visited inside the branch
    assert r.warnings == []


def test_monte_carlo_draws_no_cold_start_on_response_legs():
    g, p, price = _costed()
    plan = Planner(g, price, p).plan(Scenario("t", "gw", [Step(node="fn"), Step(node="table"), Step(node="fn")]))
    backend = MonteCarloBackend(g, p, make_sampler(200, 1))
    forward, back = plan.items[1], plan.items[3]
    assert "cold_start" in backend.cost(forward).breakdown
    assert backend.cost(back).breakdown == {}
