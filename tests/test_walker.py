"""Expected-value walker on a hand-built graph — no parser involved.

  internet → gw → fn → {table, bucket, fn2 → table2}
"""

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

REGION = Placement(region="us-east-1")


def _graph() -> InfraGraph:
    g = InfraGraph()
    g.add_node(Node("internet", NodeKind.EXTERNAL, "internet"))
    g.add_node(Node("gw", NodeKind.GATEWAY, "api_gateway", REGION))
    g.add_node(Node("fn", NodeKind.COMPUTE, "lambda", REGION))
    g.add_node(Node("fn2", NodeKind.COMPUTE, "lambda", REGION))
    g.add_node(Node("table", NodeKind.DATASTORE, "dynamodb", REGION))
    g.add_node(Node("table2", NodeKind.DATASTORE, "dynamodb", REGION))
    g.add_node(Node("bucket", NodeKind.DATASTORE, "s3", REGION))
    for src, dst, kind in [("internet", "gw", EdgeKind.INVOKE), ("gw", "fn", EdgeKind.INVOKE),
                           ("fn", "table", EdgeKind.READ), ("fn", "bucket", EdgeKind.READ),
                           ("fn", "fn2", EdgeKind.INVOKE), ("fn2", "table2", EdgeKind.READ)]:
        g.add_edge(Edge(src, dst, kind, Confidence.HIGH, f"{src} calls {dst}"))
    return g


@pytest.fixture
def costed():
    cfg, g = Config(), _graph()
    profile = load_profile(cfg)
    cost_graph(g, profile, cfg)
    return g, make_pricer(g, profile, cfg)


def _run(costed, steps, entry="gw", name="t"):
    g, price = costed
    return WALKERS.get("expected_value")().run(g, Scenario(name, entry, steps), price=price)


def _hop(result, src, dst):
    return next(h for h in result.hops if h.src == src and h.dst == dst)


def test_entry_hop_from_internet_is_charged_and_distance_is_doubled(costed):
    r = _run(costed, [Step(node="fn")])
    entry = _hop(r, "internet", "gw")
    assert entry.breakdown["distance"] == 40.0            # internet_to_edge 20 × 2
    assert entry.breakdown["processing"] == 10.0          # api_gateway route
    fn = _hop(r, "gw", "fn")
    assert fn.breakdown == {"distance": 1.0, "processing": 5.0, "cold_start": 20.0}
    assert r.total_ms == pytest.approx(50 + 26)


def test_sequential_hops_add_up(costed):
    r = _run(costed, [Step(node="fn"), Step(node="table"), Step(node="bucket")])
    assert r.total_ms == pytest.approx(sum(h.latency_ms for h in r.hops))
    assert [ (h.src, h.dst) for h in r.hops ][1:] == [("gw", "fn"), ("fn", "table"), ("fn", "bucket")]
    assert "after returning from table" in _hop(r, "fn", "bucket").evidence


def test_parallel_costs_the_slowest_branch_and_marks_the_rest(costed):
    r = _run(costed, [Step(node="fn"),
                      Step(parallel=[[Step(node="table")], [Step(node="fn2"), Step(node="table2")]])])
    slow = _hop(r, "fn", "fn2").latency_ms + _hop(r, "fn2", "table2").latency_ms
    fast = _hop(r, "fn", "table").latency_ms
    assert slow > fast
    assert r.total_ms == pytest.approx(50 + 26 + slow)
    assert _hop(r, "fn", "table").on_critical_path is False
    assert _hop(r, "fn", "fn2").on_critical_path is True
    assert r.shape["parallel_groups"] == 1
    assert r.shape["parallel_savings_ms"] == pytest.approx(fast)


def test_fanout_is_costed_once(costed):
    once = _run(costed, [Step(node="fn"), Step(node="table")]).total_ms
    fan = _run(costed, [Step(node="fn"), Step(fanout=("table", 20))])
    assert fan.total_ms == pytest.approx(once)
    assert fan.shape["fanout_copies"] == 20
    assert "×20" in _hop(fan, "fn", "table").evidence


def test_op_override_uses_write_cost(costed):
    read = _run(costed, [Step(node="fn"), Step(node="table")])
    write = _run(costed, [Step(node="fn"), Step(node="table", op="write")])
    assert _hop(read, "fn", "table").breakdown["processing"] == 4.0
    assert _hop(write, "fn", "table").breakdown["processing"] == 8.0


def test_response_hop_back_to_caller_costs_nothing_by_default(costed):
    r = _run(costed, [Step(node="fn"), Step(node="table"), Step(node="fn")])
    back = _hop(r, "table", "fn")
    assert back.breakdown == {} and back.latency_ms == 0      # forward hop already paid network + processing
    assert "response leg" in back.evidence and "already counted on the forward hop" in back.evidence
    assert r.warnings == []


def test_repeat_call_to_same_node_repeats_the_hop(costed):
    r = _run(costed, [Step(node="fn"), Step(node="table"), Step(node="table")])
    hops = [(h.src, h.dst) for h in r.hops]
    assert hops.count(("fn", "table")) == 2
    assert "repeat call" in r.hops[-1].evidence


def test_wait_step_is_pure_cost(costed):
    r = _run(costed, [Step(node="fn"), Step(wait_ms=1500)])
    assert r.total_ms == pytest.approx(50 + 26 + 1500)
    assert r.shape["wait_ms"] == 1500


def test_unknown_hop_is_estimated_with_a_warning(costed):
    r = _run(costed, [Step(node="table2")])            # gw has no edge to table2
    assert len(r.warnings) == 1 and "no inferred edge" in r.warnings[0] and "synthetic read" in r.warnings[0]
    assert _hop(r, "gw", "table2").breakdown["processing"] == 4.0     # a datastore is read, not "invoked"
