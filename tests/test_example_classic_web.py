import pytest
from conftest import edge

from iacsim.core.models import EdgeKind, NodeKind

LB = "module.load_balancer.aws_lb.this"
WEB_A = 'module.compute.aws_instance.this["us-east-1a"]'
WEB_B = 'module.compute.aws_instance.this["us-east-1b"]'
DB = "module.database.aws_db_instance.this"


def test_no_warnings(classic_web):
    graph, raw = classic_web
    assert raw.warnings == [] and graph.warnings == []


def test_nodes_have_kind_subtype_and_placement(classic_web):
    graph, _ = classic_web
    lb, web_a, db = graph.nodes[LB], graph.nodes[WEB_A], graph.nodes[DB]
    assert (lb.kind, lb.subtype, lb.placement.region, lb.placement.az) == (NodeKind.LB, "alb", "us-east-1", None)
    assert (web_a.kind, web_a.subtype, web_a.placement.az) == (NodeKind.COMPUTE, "ec2", "us-east-1a")
    assert graph.nodes[WEB_B].placement.az == "us-east-1b"
    assert (db.kind, db.subtype, db.placement.region, db.placement.az) == (NodeKind.DATASTORE, "rds", "us-east-1", "us-east-1a")
    assert web_a.placement.vpc == "module.network.aws_vpc.this"
    assert db.attrs["engine"] == "postgres" and web_a.attrs["instance_type"] == "t3.medium"


def test_internet_enters_at_the_load_balancer(classic_web):
    graph, _ = classic_web
    e = edge(graph, "internet", LB)
    assert e.kind == EdgeKind.INVOKE and e.rule == "normaliser"


def test_lb_routes_to_both_instances(classic_web):
    graph, _ = classic_web
    for web in (WEB_A, WEB_B):
        e = edge(graph, LB, web)
        assert e.kind == EdgeKind.ROUTE and e.rule == "target_group"
        assert "listener" in e.evidence and "registers" in e.evidence


def test_instances_read_the_database_via_user_data(classic_web):
    graph, _ = classic_web
    for web in (WEB_A, WEB_B):
        e = edge(graph, web, DB)
        assert e.kind == EdgeKind.READ and e.rule == "env_var"
        assert "user_data db_host" in e.evidence


def test_edge_count_is_exact(classic_web):
    graph, _ = classic_web
    assert len(graph.edges) == 5


# ---------------------------------------------------------------- M2: run

def test_page_load_walks_lb_web_db_db_and_back(classic_web_run):
    from conftest import result
    r = result(classic_web_run, "page_load")
    assert [(h.src, h.dst) for h in r.hops] == [
        ("internet", LB), (LB, WEB_A), (WEB_A, DB), (WEB_A, DB), (DB, WEB_A)]
    assert r.warnings == []
    assert r.total_ms == pytest.approx(57.2)
