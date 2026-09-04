"""Differ unit tests on hand-built Findings pairs — no parser, no pipeline."""

import pytest

from iacsim.core.models import (
    Confidence,
    Edge,
    EdgeKind,
    Finding,
    Findings,
    HopResult,
    InfraGraph,
    Node,
    NodeKind,
    Placement,
)
from iacsim.differ import diff_graphs, diff_hops, diff_reports, diff_scenario, parse_threshold


def hop(src, dst, ms, **breakdown):
    return HopResult(src, dst, ms, breakdown or {"distance": ms}, evidence="test")


def cat(subject, ms, share, layer="A1", additive=True):
    return Finding("per_category", subject, ms, share, "detail", layer=layer, additive=additive)


def rec(subject, saving):
    return Finding("recommendations", subject, saving, 0.5, "why")


def findings(name, hops, extra=()):
    return Findings(name, sum(h.latency_ms for h in hops), list(extra), ["defaults"], hops=hops)


def graph(db_region="us-east-1", with_peer=False):
    g = InfraGraph()
    g.add_node(Node("web", NodeKind.COMPUTE, "ec2", Placement("us-east-1", "us-east-1a", "vpc-a")))
    g.add_node(Node("db", NodeKind.DATASTORE, "rds", Placement(db_region, f"{db_region}a", "vpc-a")))
    g.add_edge(Edge("web", "db", EdgeKind.READ, Confidence.HIGH, "test"))
    if with_peer:
        g.add_node(Node("vpc-b", NodeKind.NETWORK, "vpc", Placement(db_region)))
        g.add_edge(Edge("web", "vpc-b", EdgeKind.PEER, Confidence.HIGH, "test"))
    return g


# ------------------------------------------------------------------ hops

def test_hops_align_by_label_and_occurrence():
    before = [hop("web", "db", 5), hop("web", "db", 5)]
    after = [hop("web", "db", 5), hop("web", "db", 150)]
    deltas = diff_hops(before, after)
    assert [(d.occurrence, d.status) for d in deltas] == [(1, "unchanged"), (2, "changed")]
    assert deltas[1].delta_ms == 145 and deltas[1].breakdown_deltas() == {"distance": (5.0, 150.0)}


def test_hops_added_and_removed():
    deltas = diff_hops([hop("a", "b", 1), hop("b", "c", 2)], [hop("a", "b", 1), hop("a", "d", 3)])
    by = {(d.label, d.status) for d in deltas}
    assert ("a → d", "added") in by and ("b → c", "removed") in by
    added = next(d for d in deltas if d.status == "added")
    assert added.index_before is None and added.index_after == 2 and added.delta_ms == 3


def test_tiny_delta_counts_as_unchanged():
    deltas = diff_hops([hop("a", "b", 1.00)], [hop("a", "b", 1.04)])
    assert deltas[0].status == "unchanged"


# ------------------------------------------------------------------ scenarios

def test_one_sided_scenario_is_listed_not_an_error():
    only_after = diff_scenario(None, findings("new", [hop("a", "b", 1)]))
    only_before = diff_scenario(findings("old", [hop("a", "b", 1)]), None)
    assert only_after.status == "only_after" and only_after.before_ms is None and only_after.after_ms == 1
    assert only_before.status == "only_before" and only_before.hops == []


def test_zero_diff():
    f = findings("s", [hop("a", "b", 4)], [cat("distance", 4, 1.0)])
    report = diff_reports([f], [f], graph(), graph())
    assert report.is_empty and report.scenarios[0].delta_ms == 0
    assert report.regressions(("ms", 0)) == []


def test_category_delta_arithmetic():
    b = findings("s", [hop("a", "b", 10)], [cat("distance", 2, 0.2), cat("processing", 8, 0.8, "A2")])
    a = findings("s", [hop("a", "b", 100)], [cat("distance", 92, 0.92), cat("processing", 8, 0.08, "A2")])
    sd = diff_scenario(b, a)
    by = {c.subject: c for c in sd.categories}
    assert by["distance"].delta_ms == 90 and by["distance"].before_share == 0.2 and by["distance"].after_share == 0.92
    assert by["processing"].status == "unchanged" and by["processing"].delta_ms == 0
    assert sd.delta_ms == 90 and sd.delta_pct == 9.0


def test_non_additive_categories_are_left_out_of_the_shift():
    b = findings("s", [hop("a", "b", 1)], [cat("distance", 1, 1.0), cat("hops", 1, 0, "A3", additive=False)])
    assert [c.subject for c in diff_scenario(b, b).categories] == ["distance"]


def test_recommendations_appear_and_disappear():
    b = findings("s", [hop("a", "b", 1)], [rec("batch calls", 5)])
    a = findings("s", [hop("a", "b", 1)], [rec("co-locate db", 300), rec("batch calls", 5)])
    statuses = {r.subject: r.status for r in diff_scenario(b, a).recommendations}
    assert statuses == {"co-locate db": "appeared", "batch calls": "unchanged"}
    assert {r.subject: r.status for r in diff_scenario(a, b).recommendations}["co-locate db"] == "disappeared"


# ------------------------------------------------------------------ graph

def test_graph_diff_reports_moves_adds_and_edges():
    d = diff_graphs(graph(), graph("eu-west-1", with_peer=True))
    assert {(m.node_id, m.field, m.before, m.after) for m in d.nodes_moved} == {
        ("db", "region", "us-east-1", "eu-west-1"), ("db", "az", "us-east-1a", "eu-west-1a")}
    assert d.nodes_added == ["vpc-b"] and d.nodes_removed == []
    assert d.edges_added == ["web → vpc-b (peer)"] and d.edges_removed == []


def test_graph_diff_can_align_by_label_across_formats():
    tf, cfn = InfraGraph(), InfraGraph()
    tf.add_node(Node("aws_lambda_function.api", NodeKind.COMPUTE, "lambda", Placement("us-east-1"), label="api"))
    cfn.add_node(Node("ApiLambdaABC123", NodeKind.COMPUTE, "lambda", Placement("us-east-1"), label="api"))
    assert not diff_graphs(tf, cfn, align_by="id").is_empty
    assert diff_graphs(tf, cfn, align_by="label").is_empty


# ------------------------------------------------------------------ CI threshold

@pytest.mark.parametrize("text,expected", [("50ms", ("ms", 50.0)), ("10%", ("percent", 10.0)),
                                           (" 2.5 ms ", ("ms", 2.5))])
def test_parse_threshold(text, expected):
    assert parse_threshold(text) == expected


def test_parse_threshold_rejects_garbage():
    with pytest.raises(ValueError):
        parse_threshold("fast")


def test_regressions_respect_both_units():
    b = findings("s", [hop("a", "b", 100)])
    a = findings("s", [hop("a", "b", 130)])
    report = diff_reports([b], [a], graph(), graph())
    assert [s.name for s in report.regressions(("ms", 20))] == ["s"]
    assert report.regressions(("ms", 50)) == []
    assert [s.name for s in report.regressions(("percent", 10))] == ["s"]
    assert report.regressions(("percent", 30)) == []
