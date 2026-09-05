"""WP2: one edge per (src, dst) regardless of rule order; every operation with
evidence is kept in `ops`, the priced kind follows KIND_PRIORITY, and a scenario
step's `op:` re-prices it."""

import pytest
from conftest import EXAMPLES, edge

from iacsim.core.config import load_config
from iacsim.core.models import Confidence, Edge, EdgeKind, InfraGraph, Node, NodeKind
from iacsim.core.pipeline import _merge_edge, build_graph


def _edge_set(graph):
    return {(e.src, e.dst, e.kind, tuple(e.ops)) for e in graph.edges}


def test_rule_order_does_not_change_the_graph():
    target = EXAMPLES / "foosh-serverless"
    forward = load_config(target)
    backward = load_config(target, {"inference.rules": list(reversed(forward.get("inference.rules")))})
    g1, _ = build_graph(target, forward)
    g2, _ = build_graph(target, backward)
    assert _edge_set(g1) == _edge_set(g2)
    assert len(g1.edges) == len(g2.edges)
    api_table = next(e for e in g1.edges if e.src.startswith("module.api.") and 'table["workflows"]' in e.dst)
    assert api_table.rule == "env_var+iam_policy"
    api_table_rev = next(e for e in g2.edges if e.src.startswith("module.api.") and 'table["workflows"]' in e.dst)
    assert api_table_rev.rule == "iam_policy+env_var"          # names in the order they ran; same edge


def test_merge_keeps_every_op_prices_the_read_and_lifts_confidence():
    g = InfraGraph()
    for nid, kind, sub in [("fn", NodeKind.COMPUTE, "lambda"), ("t", NodeKind.DATASTORE, "dynamodb")]:
        g.add_node(Node(nid, kind, sub))
    index = {}
    _merge_edge(g, index, Edge("fn", "t", EdgeKind.WRITE, Confidence.MEDIUM, "iam grants PutItem", rule="iam_policy"))
    _merge_edge(g, index, Edge("fn", "t", EdgeKind.READ, Confidence.HIGH, "env TABLE_NAME", rule="env_var"))
    # same rule twice: no-op
    _merge_edge(g, index, Edge("fn", "t", EdgeKind.READ, Confidence.LOW, "again", rule="env_var"))
    assert len(g.edges) == 1
    e = g.edges[0]
    assert e.kind == EdgeKind.READ and e.ops == [EdgeKind.READ, EdgeKind.WRITE]
    assert e.confidence == Confidence.HIGH
    assert e.rule == "iam_policy+env_var" and e.evidence == "iam grants PutItem; env_var: env TABLE_NAME"


def test_op_write_reprices_a_read_edge_and_the_hop_says_so(foosh_run, default_profile):
    from conftest import result
    r = result(foosh_run, "start_workflow")
    dyn = default_profile.processing["dynamodb"]["defaults"]
    write = next(h for h in r.hops if 'table["executions"]' in h.dst)
    read = next(h for h in r.hops if 'table["workflows"]' in h.dst)
    assert write.breakdown["processing"] == pytest.approx(dyn["write"])
    assert read.breakdown["processing"] == pytest.approx(dyn["read"])
    assert "also may write: use op: write" in read.evidence
    assert "also may" not in write.evidence


def test_every_edge_has_ops_and_kind_is_the_first(foosh):
    graph, _ = foosh
    for e in graph.edges:
        assert e.ops and e.kind == e.ops[0], (e.src, e.dst)
    assert edge(graph, "internet", "aws_api_gateway_rest_api.main").ops == [EdgeKind.INVOKE]
