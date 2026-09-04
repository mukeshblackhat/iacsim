import pytest
from conftest import edge

from iacsim.core.models import EdgeKind

WEB_A = 'module.compute.aws_instance.this["us-east-1a"]'
DB = "module.database.aws_db_instance.this"
WEB_VPC = "module.network.aws_vpc.this"
DB_VPC = "module.db_network.aws_vpc.this"


def test_no_warnings(classic_web_bad):
    graph, raw = classic_web_bad
    assert raw.warnings == [] and graph.warnings == []


def test_database_is_in_the_aliased_provider_region(classic_web_bad):
    graph, _ = classic_web_bad
    db = graph.nodes[DB]
    assert db.placement.region == "eu-west-1" and db.placement.az == "eu-west-1a"
    assert db.placement.vpc == DB_VPC
    assert graph.nodes[WEB_A].placement.region == "us-east-1"
    assert graph.nodes[DB_VPC].placement.region == "eu-west-1"


def test_web_still_reads_the_database(classic_web_bad):
    graph, _ = classic_web_bad
    assert edge(graph, WEB_A, DB).kind == EdgeKind.READ


def test_vpcs_are_peered(classic_web_bad):
    graph, _ = classic_web_bad
    e = edge(graph, WEB_VPC, DB_VPC)
    assert e.kind == EdgeKind.PEER and e.rule == "vpc_peering"
    assert "eu-west-1" in e.evidence


def test_same_module_reuse_yields_same_edge_set_plus_peering(classic_web, classic_web_bad):
    good, bad = classic_web[0], classic_web_bad[0]
    pairs = lambda g: {(e.src, e.dst, e.kind) for e in g.edges}
    assert pairs(bad) - pairs(good) == {(WEB_VPC, DB_VPC, EdgeKind.PEER)}


# ---------------------------------------------------------------- M2: run

def test_cross_region_database_is_the_whole_difference(classic_web_run, classic_web_bad_run):
    from conftest import result
    good, bad = result(classic_web_run, "page_load"), result(classic_web_bad_run, "page_load")
    # two DB round-trips × (request + response) × (75 ms cross-region − 0.3 ms same-AZ)
    assert bad.total_ms - good.total_ms == pytest.approx(2 * 2 * (75 - 0.3))
    for hop in bad.hops:
        if hop.dst == DB:
            assert hop.breakdown["distance"] == pytest.approx(150)
        else:
            assert hop.breakdown == next(h for h in good.hops if (h.src, h.dst) == (hop.src, hop.dst)).breakdown


# ---------------------------------------------------------------- M3: analyzers

def test_top_recommendation_is_to_co_locate_the_database(classic_web_run, classic_web_bad_run):
    from conftest import result
    delta = result(classic_web_bad_run, "page_load").total_ms - result(classic_web_run, "page_load").total_ms
    findings = next(f for f in classic_web_bad_run.findings if f.scenario == "page_load")
    recs = findings.by_analyzer()["recommendations"]
    assert recs[0].subject.startswith("co-locate module.database.aws_db_instance.this") or \
        recs[0].subject.startswith("co-locate database.db_instance")
    assert recs[0].latency_ms == pytest.approx(delta, rel=0.05)
    assert recs[0].refs == [f"{WEB_A} → {DB}"] * 2
    categories = {f.subject: f for f in findings.by_analyzer()["per_category"]}
    assert categories["distance"].share > 0.9 and categories["distance"].layer == "A1"
