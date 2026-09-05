"""Cost rules against the shared tiny graph (conftest.tiny_graph) — no parser needed.

  web (ec2, us-east-1a) → db (rds, `db_region`a)   READ
  gw (api_gateway)      → fn (lambda)              INVOKE
"""

from conftest import tiny_graph

from iacsim.core.interfaces import COST_RULES
from iacsim.core.registry import load_builtin_plugins
from iacsim.latency.profile import load_default_profile, load_defaults_document, merge_profiles


def setup_module():
    load_builtin_plugins()


def _profile():
    return load_default_profile()


def test_same_az_is_cheap_and_cross_region_is_not():
    p = _profile()
    rule = COST_RULES.get("distance")()
    near_g, far_g = tiny_graph(db_region="us-east-1"), tiny_graph(db_region="eu-west-1")
    near = rule.cost(near_g.find_edge("web", "db"), near_g, p)["distance"]
    far = rule.cost(far_g.find_edge("web", "db"), far_g, p)["distance"]
    assert near < 1 and far >= 50


def test_processing_picks_read_for_read_edge():
    g, p = tiny_graph(), _profile()
    assert COST_RULES.get("processing")().cost(g.find_edge("web", "db"), g, p) == {"processing": 5.0}


def test_cold_start_only_for_lambda():
    g, p = tiny_graph(), _profile()
    rule = COST_RULES.get("cold_start")()
    assert rule.cost(g.find_edge("web", "db"), g, p) == {}
    assert rule.cost(g.find_edge("gw", "fn"), g, p) == {"cold_start": 400 * 0.05}


def test_per_resource_overrides_defaults():
    g = tiny_graph()
    p = merge_profiles([
        ("defaults", load_defaults_document()),
        ("override", {"processing": {"rds": {"per_resource": {"db": {"read": 42}}}}}),
    ])
    assert COST_RULES.get("processing")().cost(g.find_edge("web", "db"), g, p) == {"processing": 42.0}
