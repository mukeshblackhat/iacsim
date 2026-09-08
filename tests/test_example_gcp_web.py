"""gcp-web — the GCP twin of classic-web (M11).

    internet → global forwarding rule → HTTPS proxy → URL map → backend service
             → serverless NEG → Cloud Run (us-central1) → Cloud SQL Postgres (us-central1)
                                                        → Memorystore Redis (us-central1)

The five load-balancer resources are one Google Front End drawn as five nodes:
the internet enters at the forwarding rule and nowhere else, every hop between
chain nodes is 0 ms, and the hop into Cloud Run is the first one priced again.
Every timing below is recomputed from the profile — no magic numbers.
"""

import pytest
from conftest import edge, result

from iacsim.core.models import EdgeKind, NodeKind

RULE = "google_compute_global_forwarding_rule.web"
PROXY = "google_compute_target_https_proxy.web"
URL_MAP = "google_compute_url_map.web"
BACKEND = "google_compute_backend_service.web"
NEG = "google_compute_region_network_endpoint_group.web"
RUN = "google_cloud_run_v2_service.web"
DB = "google_sql_database_instance.db"
CACHE = "google_redis_instance.cache"
VPC = "google_compute_network.vpc"
SUBNET = "google_compute_subnetwork.app"

CHAIN = [RULE, PROXY, URL_MAP, BACKEND, NEG]
CHAIN_SUBTYPES = ["forwarding_rule", "target_proxy", "url_map", "backend_service", "neg"]
CHAIN_ATTRS = ["target", "url_map", "default_service", "backend.group", "cloud_run.service"]   # what each link follows
GLUE_PREFIXES = ("google_service_account.", "google_project_iam_member.", "google_compute_managed_ssl_certificate.",
                 "google_compute_global_address.", "google_service_networking_connection.", "google_sql_database.")


def rules(e) -> set[str]:
    return set((e.rule or "").split("+"))


def test_no_warnings(gcp_web):
    graph, raw = gcp_web
    assert raw.warnings == [] and graph.warnings == []


def test_nodes_have_kind_subtype_and_placement(gcp_web):
    graph, _ = gcp_web
    run, db, cache = graph.nodes[RUN], graph.nodes[DB], graph.nodes[CACHE]
    assert (run.kind, run.subtype, run.placement.region, run.placement.az) == (
        NodeKind.COMPUTE, "cloud_run", "us-central1", None)
    assert (run.placement.vpc, run.placement.subnet) == (VPC, SUBNET)          # template.vpc_access.network_interfaces
    # a REGIONAL (HA) instance spans zones, so it has a region and no single zone
    assert (db.kind, db.subtype, db.placement.region, db.placement.az) == (
        NodeKind.DATASTORE, "cloud_sql", "us-central1", None)
    assert db.placement.vpc == VPC                                   # settings.ip_configuration.private_network
    assert (cache.kind, cache.subtype, cache.placement.region) == (NodeKind.DATASTORE, "memorystore", "us-central1")
    assert [(graph.nodes[n].kind, graph.nodes[n].subtype) for n in CHAIN] == [(NodeKind.LB, s) for s in CHAIN_SUBTYPES]
    assert all(graph.nodes[n].placement.region == "us-central1" for n in CHAIN)
    assert db.attrs["database_version"] == "POSTGRES_16" and db.attrs["tier"] == "db-custom-2-7680"
    assert run.attrs["max_instances"] == 10 and cache.attrs["memory_size_gb"] == 1


def test_glue_is_not_a_node(gcp_web):
    graph, _ = gcp_web
    assert not [n for n in graph.nodes if n.startswith(GLUE_PREFIXES)]
    assert {n.kind for n in graph.nodes.values()} == {
        NodeKind.EXTERNAL, NodeKind.LB, NodeKind.COMPUTE, NodeKind.DATASTORE, NodeKind.NETWORK}


def test_internet_enters_at_the_forwarding_rule_only(gcp_web):
    graph, _ = gcp_web
    e = edge(graph, "internet", RULE)
    assert e.kind == EdgeKind.INVOKE and e.rule == "normaliser" and "public entry point" in e.evidence
    for node in CHAIN[1:] + [RUN, DB, CACHE]:
        assert graph.find_edge("internet", node) is None, node
    assert [e.dst for e in graph.edges if e.src == "internet"] == [RULE]


def test_lb_chain_routes_link_by_link(gcp_web):
    graph, _ = gcp_web
    for src, dst, attr in zip(CHAIN, CHAIN[1:] + [RUN], CHAIN_ATTRS, strict=True):
        e = edge(graph, src, dst)
        assert (e.kind, e.rule, e.ops) == (EdgeKind.ROUTE, "gcp_lb_chain", [EdgeKind.ROUTE])
        assert f" {attr} routes to " in e.evidence, e.evidence
    # the chain is a straight line: no link skips ahead, nothing routes backwards
    assert graph.find_edge(RULE, URL_MAP) is None and graph.find_edge(BACKEND, RUN) is None
    assert graph.find_edge(NEG, BACKEND) is None


def test_cloud_run_reads_the_database_with_two_kinds_of_evidence(gcp_web):
    graph, _ = gcp_web
    e = edge(graph, RUN, DB)
    assert e.kind == EdgeKind.READ and e.ops == [EdgeKind.READ]
    assert rules(e) == {"env_var", "gcp_iam_binding"}
    assert "env DB_INSTANCE references" in e.evidence and "connection_name" in e.evidence
    assert "roles/cloudsql.client" in e.evidence and "service account of" in e.evidence


def test_cloud_run_reads_the_cache_from_its_env(gcp_web):
    graph, _ = gcp_web
    e = edge(graph, RUN, CACHE)
    assert (e.kind, e.rule) == (EdgeKind.READ, "env_var")
    assert "env REDIS_HOST references" in e.evidence and e.evidence.endswith(f"{CACHE}.host")
    assert graph.find_edge(DB, RUN) is None and graph.find_edge(DB, CACHE) is None


def test_edge_count_is_exact(gcp_web):
    graph, _ = gcp_web
    assert len(graph.edges) == 1 + len(CHAIN) + 2          # internet, five chain links, two datastore reads


# ---------------------------------------------------------------- run

def test_page_load_walks_the_chain_then_two_db_reads_and_back(gcp_web_run):
    r = result(gcp_web_run, "page_load")
    assert [(h.src, h.dst) for h in r.hops] == [
        ("internet", RULE), (RULE, PROXY), (PROXY, URL_MAP), (URL_MAP, BACKEND), (BACKEND, NEG),
        (NEG, RUN), (RUN, DB), (RUN, DB), (DB, RUN)]
    assert r.warnings == []


def test_page_load_is_recomputed_from_the_profile(gcp_web_run, default_profile):
    p = default_profile
    d, proc = p.distance, p.processing
    head, run, sql = proc["forwarding_rule"]["defaults"], proc["cloud_run"]["defaults"], proc["cloud_sql"]["defaults"]
    r = result(gcp_web_run, "page_load")
    entry, *chain, into_run, read1, read2, back = r.hops

    # internet → forwarding rule: internet_to_edge both ways plus the GFE's own routing, charged once
    assert entry.breakdown == {"distance": 2 * d["internet_to_edge"], "processing": head["route"]}
    # the four hops inside the chain: route 0 in the profile, no wire between them (G9)
    for hop, subtype in zip(chain, CHAIN_SUBTYPES[1:], strict=True):
        assert proc[subtype]["defaults"]["route"] == 0
        assert hop.latency_ms == 0 and not any(hop.breakdown.values()), (subtype, hop.breakdown)
    # NEG → Cloud Run: regional both sides → same_region_unknown_az, doubled; warm + blended cold start
    assert into_run.breakdown == {"distance": 2 * d["same_region_unknown_az"], "processing": run["warm"],
                                  "cold_start": pytest.approx(run["cold"] * run["cold_prob"])}
    for read in (read1, read2):
        assert read.breakdown == {"distance": 2 * d["same_region_unknown_az"], "processing": sql["read"]}
    assert back.breakdown == ({"processing": run["respond"]} if run["respond"] else {})

    expected = (2 * d["internet_to_edge"] + head["route"]
                + 2 * d["same_region_unknown_az"] + run["warm"] + run["cold"] * run["cold_prob"]
                + 2 * (2 * d["same_region_unknown_az"] + sql["read"])
                + run["respond"])
    assert r.total_ms == pytest.approx(expected)
    assert r.total_ms == pytest.approx(sum(h.latency_ms for h in r.hops))


def test_cached_page_reads_memorystore_once(gcp_web_run, default_profile):
    p = default_profile
    r = result(gcp_web_run, "cached_page")
    assert [(h.src, h.dst) for h in r.hops][-2:] == [(RUN, CACHE), (CACHE, RUN)]
    cache_read = r.hops[-2]
    assert cache_read.breakdown == {"distance": 2 * p.distance["same_region_unknown_az"],
                                    "processing": p.processing["memorystore"]["defaults"]["read"]}
    assert r.total_ms == pytest.approx(sum(h.latency_ms for h in r.hops))


def test_every_scenario_total_is_the_sum_of_its_hops(gcp_web_run):
    assert {r.scenario for r in gcp_web_run.results} >= {"page_load", "cached_page"}
    for r in gcp_web_run.results:
        assert r.total_ms == pytest.approx(sum(h.latency_ms for h in r.hops)), r.scenario
        assert r.hops[0].src == "internet" and r.hops[0].dst == RULE       # every path starts at the chain head
