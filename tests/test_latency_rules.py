"""Cost rules against a hand-built 3-node graph — no parser needed."""

from iacsim.core.interfaces import COST_RULES, PROFILE_SOURCES
from iacsim.core.models import Confidence, Edge, EdgeKind, InfraGraph, Node, NodeKind, Placement
from iacsim.core.registry import load_builtin_plugins
from iacsim.latency.profile import merge_profiles


def setup_module():
    load_builtin_plugins()


def _graph(db_region: str) -> InfraGraph:
    g = InfraGraph()
    g.add_node(Node("web", NodeKind.COMPUTE, "ec2", Placement(region="us-east-1", az="us-east-1a")))
    g.add_node(Node("db", NodeKind.DATASTORE, "rds", Placement(region=db_region, az=f"{db_region}a")))
    g.add_node(Node("fn", NodeKind.COMPUTE, "lambda", Placement(region="us-east-1")))
    g.add_edge(Edge("web", "db", EdgeKind.READ, Confidence.DECLARED, "test"))
    g.add_edge(Edge("web", "fn", EdgeKind.INVOKE, Confidence.DECLARED, "test"))
    return g


def _profile():
    return merge_profiles([("defaults", PROFILE_SOURCES.get("defaults")().load("defaults"))])


def test_same_az_is_cheap_and_cross_region_is_not():
    p = _profile()
    rule = COST_RULES.get("distance")()
    near = rule.cost(_graph("us-east-1").edges[0], _graph("us-east-1"), p)["distance"]
    far = rule.cost(_graph("eu-west-1").edges[0], _graph("eu-west-1"), p)["distance"]
    assert near < 1 and far >= 50


def test_processing_picks_read_for_read_edge():
    g, p = _graph("us-east-1"), _profile()
    assert COST_RULES.get("processing")().cost(g.edges[0], g, p) == {"processing": 5.0}


def test_cold_start_only_for_lambda():
    g, p = _graph("us-east-1"), _profile()
    rule = COST_RULES.get("cold_start")()
    assert rule.cost(g.edges[0], g, p) == {}
    assert rule.cost(g.edges[1], g, p) == {"cold_start": 400 * 0.05}


def test_per_resource_overrides_defaults():
    g = _graph("us-east-1")
    p = merge_profiles([
        ("defaults", PROFILE_SOURCES.get("defaults")().load("defaults")),
        ("override", {"processing": {"rds": {"per_resource": {"db": {"read": 42}}}}}),
    ])
    assert COST_RULES.get("processing")().cost(g.edges[0], g, p) == {"processing": 42.0}
