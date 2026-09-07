"""Cost rules against the shared tiny graph (conftest.tiny_graph) — no parser needed.

  web (ec2, us-east-1a) → db (rds, `db_region`a)   READ
  gw (api_gateway)      → fn (lambda)              INVOKE

Plus the guard-rail (G8): every subtype a normaliser's type map can produce must
be priced. A subtype with no `processing` block, or whose provider names an
invoke key the block does not contain, makes every hop into it cost **nothing** —
silently, with no warning. `kinesis` was in exactly that state until M11.
"""

import pytest
from conftest import tiny_graph

from iacsim.core.interfaces import COST_RULES, behaviour_tables
from iacsim.core.models import NodeKind
from iacsim.core.registry import load_builtin_plugins
from iacsim.graph.normalisers.aws import TYPE_MAP, AwsNormaliser
from iacsim.graph.normalisers.gcp import CHAIN, GcpNormaliser
from iacsim.graph.normalisers.gcp import TYPE_MAP as GCP_TYPE_MAP
from iacsim.latency.profile import load_default_profile, load_defaults_document, merge_profiles
from iacsim.scenarios.inferred import NOT_A_HOP


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


# --------------------------------------------------------------- guard-rails (G8)

def _hop_subtypes(type_map: dict[str, tuple[NodeKind, str]]) -> list[str]:
    """Every subtype a type map can produce that a request can actually land on.
    NETWORK / EXTERNAL nodes are never a hop (scenarios/inferred.py) and so carry
    no processing block."""
    return sorted({subtype for kind, subtype in type_map.values() if kind not in NOT_A_HOP})


NORMALISERS_UNDER_TEST = [(AwsNormaliser, TYPE_MAP), (GcpNormaliser, GCP_TYPE_MAP)]


def _subtype_cases():
    """(normaliser class, subtype) for every hop subtype of every built-in normaliser."""
    return [pytest.param(cls, subtype, id=f"{cls.__name__}-{subtype}")
            for cls, type_map in NORMALISERS_UNDER_TEST for subtype in _hop_subtypes(type_map)]


@pytest.mark.parametrize("cls, subtype", _subtype_cases())
def test_every_subtype_has_a_priced_invoke_key(cls, subtype):
    """The whole class of silent-zero bugs, in one assertion per subtype."""
    load_builtin_plugins()
    block = load_defaults_document()["processing"].get(subtype, {}).get("defaults", {})
    assert block, f"latency/defaults.yaml has no processing.{subtype} block: every hop into it is free"
    key = cls.INVOKE_KEYS.get(subtype)
    assert key, f"{cls.__name__}.INVOKE_KEYS has no entry for '{subtype}': every INVOKE hop into it is free"
    assert key in block, f"processing.{subtype} has no '{key}' key, which is what INVOKE_KEYS charges"
    assert float(block[key]) >= 0


@pytest.mark.parametrize("cls", [cls for cls, _ in NORMALISERS_UNDER_TEST], ids=lambda c: c.__name__)
def test_every_cold_start_subtype_has_cold_and_cold_prob(cls):
    """The other door into the same trap: a subtype in COLD_START whose block
    lacks `cold` / `cold_prob` is charged no cold start, silently."""
    processing = load_defaults_document()["processing"]
    for subtype in cls.COLD_START:
        block = processing.get(subtype, {}).get("defaults", {})
        assert {"cold", "cold_prob"} <= set(block), f"processing.{subtype} needs cold and cold_prob"
        assert float(block["cold"]) > 0 and 0 <= float(block["cold_prob"]) <= 1


def test_invoke_keys_name_only_subtypes_the_type_map_can_produce():
    """A stale INVOKE_KEYS entry is harmless but a typo in one is not: the table
    must be exactly the hop subtypes, no more."""
    for cls, type_map in NORMALISERS_UNDER_TEST:
        assert set(cls.INVOKE_KEYS) == set(_hop_subtypes(type_map)), cls.__name__


def test_gcp_lb_chain_hops_cost_no_processing():
    """G3/G9: the chain is one Google Front End drawn as several nodes. Its head
    (`forwarding_rule`) charges the GFE's routing time — ALB parity — and every
    other link routes for 0 ms."""
    processing = load_defaults_document()["processing"]
    for subtype in CHAIN:
        assert GcpNormaliser.INVOKE_KEYS[subtype] == "route"
        if subtype != "forwarding_rule":
            assert processing[subtype]["defaults"] == {"route": 0}, subtype
    assert processing["forwarding_rule"]["defaults"]["route"] == processing["alb"]["defaults"]["route"]


def _gcp_chain_graph():
    """internet → forwarding_rule (global, us-central1) → backend_service (global)
    → neg (europe-west1) → cloud_run (europe-west1). The two chain-internal hops
    span a region boundary on paper; there is no wire between them."""
    from iacsim.core.models import Confidence, Edge, EdgeKind, InfraGraph, Node, Placement
    g = InfraGraph()
    for nid, kind, subtype, region in [
        ("internet", NodeKind.EXTERNAL, "internet", None),
        ("fr", NodeKind.LB, "forwarding_rule", "us-central1"),
        ("bs", NodeKind.LB, "backend_service", "us-central1"),
        ("neg", NodeKind.LB, "neg", "europe-west1"),
        ("run", NodeKind.COMPUTE, "cloud_run", "europe-west1"),
    ]:
        g.add_node(Node(nid, kind, subtype, Placement(region=region), label=nid))
    for src, dst in [("internet", "fr"), ("fr", "bs"), ("bs", "neg"), ("neg", "run")]:
        g.add_edge(Edge(src, dst, EdgeKind.ROUTE if src != "internet" else EdgeKind.INVOKE,
                        Confidence.HIGH, f"{src} → {dst}"))
    return g


def test_gcp_lb_chain_hops_cost_no_distance():
    """G9: DistanceRule charges nothing between two chain nodes even when their
    placements differ — without this a global backend_service → regional NEG hop
    priced as cross-region (~200 ms of phantom network)."""
    g, p = _gcp_chain_graph(), _profile()
    rule = COST_RULES.get("distance")()
    assert "chain" in behaviour_tables().__dataclass_fields__ and CHAIN <= behaviour_tables().chain
    assert rule.cost(g.find_edge("fr", "bs"), g, p) == {"distance": 0.0}
    assert rule.cost(g.find_edge("bs", "neg"), g, p) == {"distance": 0.0}      # crosses regions on paper
    into = rule.cost(g.find_edge("internet", "fr"), g, p)["distance"]
    out = rule.cost(g.find_edge("neg", "run"), g, p)["distance"]
    assert into == p.distance["internet_to_edge"] and out > 0                  # ends of the chain still priced
    assert not g.warnings                                                       # no "region unknown" noise


def test_cost_rules_read_the_normaliser_tables():
    """The seam itself: the rules see every provider's tables, not a copy."""
    load_builtin_plugins()
    tables = behaviour_tables()
    for cls, _ in NORMALISERS_UNDER_TEST:
        assert tables.invoke_keys.items() >= cls.INVOKE_KEYS.items()
        assert tables.cold_start >= cls.COLD_START


def test_cloud_run_cold_start_is_charged():
    """G20 end to end: the rule reads the GCP normaliser's COLD_START set."""
    load_builtin_plugins()
    g, p = tiny_graph(), _profile()
    g.nodes["fn"].subtype = "cloud_run"
    block = p.processing["cloud_run"]["defaults"]
    assert COST_RULES.get("cold_start")().cost(g.find_edge("gw", "fn"), g, p) == {
        "cold_start": block["cold"] * block["cold_prob"]}


def test_kinesis_hop_is_not_free():
    """The precedent G8 was written from: a publish into a stream used to cost 0."""
    load_builtin_plugins()
    g, p = tiny_graph(), _profile()
    g.nodes["db"].subtype = "kinesis"
    cost = COST_RULES.get("processing")().cost(g.find_edge("web", "db"), g, p)
    assert cost["processing"] == p.processing["kinesis"]["defaults"]["publish"]
