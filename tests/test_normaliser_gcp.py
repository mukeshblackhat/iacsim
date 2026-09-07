"""The GCP normaliser on a hand-written RawResources — no parser, the way
conftest.tiny_graph hand-builds a graph. Covers docs/gcp/02-TYPE-MAP.md's
closing checklist: kinds and subtypes, resource-first placement (G10), the
`internet` node and its entry edges (G13), both halves of every global/regional
twin, the three NEG types, IAM grants dropped by suffix, unknown types kept
with a warning, and Cloud CDN as an attribute.
"""

import pytest

from iacsim.core.models import EdgeKind, NodeKind, RawResource, RawResources
from iacsim.core.registry import load_builtin_plugins
from iacsim.graph.normalisers import aws, gcp
from iacsim.graph.normalisers.gcp import CHAIN, TYPE_MAP, GcpNormaliser
from iacsim.scenarios.inferred import NOT_A_HOP

VPC, SUBNET = "google_compute_network.vpc", "google_compute_subnetwork.sub"
RUN, SQL = "google_cloud_run_v2_service.api", "google_sql_database_instance.db"
RULE, PROXY, URLMAP = ("google_compute_global_forwarding_rule.lb", "google_compute_target_https_proxy.lb",
                       "google_compute_url_map.lb")
BACKEND, NEG = "google_compute_backend_service.api", "google_compute_region_network_endpoint_group.api"
TOPIC, SUB = "google_pubsub_topic.events", "google_pubsub_subscription.events"


def setup_module():
    load_builtin_plugins()


def _r(address: str, attrs: dict | None = None, region: str | None = "us-central1") -> RawResource:
    rtype = address.split(".")[-2] if address.startswith("module.") else address.split(".")[0]
    return RawResource(address=address, type=rtype, attrs=attrs or {}, region=region)


def _stack() -> RawResources:
    """Cloud Run behind a global HTTPS LB, reading Cloud SQL, publishing to Pub/Sub."""
    return RawResources(format="terraform", resources=[
        _r(VPC, {"name": "vpc"}),
        _r(SUBNET, {"name": "sub", "region": "us-central1", "network": "${" + VPC + ".id}"}),
        _r(RUN, {"name": "api", "location": "europe-west1", "template": [{
            "scaling": [{"min_instance_count": 1, "max_instance_count": 10}],
            "max_instance_request_concurrency": 80,
            "containers": [{"image": "x", "resources": [{"limits": {"cpu": "1", "memory": "512Mi"}}]}],
            "vpc_access": [{"network_interfaces": [{"network": "${" + VPC + ".id}",
                                                    "subnetwork": "${" + SUBNET + ".id}"}]}]}]}),
        _r(SQL, {"name": "db", "region": "us-central1", "settings": [{"tier": "db-f1-micro"}]}),
        _r(RULE, {"name": "lb", "target": "${" + PROXY + ".id}"}),
        _r(PROXY, {"name": "lb", "url_map": "${" + URLMAP + ".self_link}"}),
        _r(URLMAP, {"name": "lb", "default_service": "${" + BACKEND + ".name}"}),
        _r(BACKEND, {"name": "api", "enable_cdn": True, "backend": [{"group": "${" + NEG + ".id}"}]}),
        _r(NEG, {"name": "api", "region": "europe-west1", "cloud_run": [{"service": "${" + RUN + ".name}"}]}),
        _r(TOPIC, {"name": "events"}),
        _r(SUB, {"name": "events", "topic": "${" + TOPIC + ".id}"}),
        _r("google_project_iam_member.run_sql", {"role": "roles/cloudsql.client"}),
        _r("google_cloud_run_v2_service_iam_member.public", {"role": "roles/run.invoker"}),
        _r("google_nonexistent_thing_iam_binding.made_up", {"role": "roles/x"}),
        _r("google_project_service.run", {"service": "run.googleapis.com"}),
        _r("google_compute_health_check.hc", {"name": "hc"}),
        _r("google_compute_autoscaler.mystery", {"name": "mystery"}),
    ])


@pytest.fixture(scope="module")
def graph():
    return GcpNormaliser().normalise(_stack())


# ------------------------------------------------------------- kinds, subtypes, placement

def test_kind_subtype_and_placement(graph):
    run, sql, rule, topic = graph.nodes[RUN], graph.nodes[SQL], graph.nodes[RULE], graph.nodes[TOPIC]
    assert (run.kind, run.subtype, run.placement.region, run.placement.az) == (
        NodeKind.COMPUTE, "cloud_run", "europe-west1", None)          # location beats the provider's us-central1
    assert (run.placement.vpc, run.placement.subnet) == (VPC, SUBNET)  # template.vpc_access
    assert (sql.kind, sql.subtype, sql.placement.region, sql.placement.az) == (
        NodeKind.DATASTORE, "cloud_sql", "us-central1", None)
    assert (rule.kind, rule.subtype, rule.placement.region) == (NodeKind.LB, "forwarding_rule", "us-central1")
    assert (topic.kind, topic.subtype) == (NodeKind.QUEUE, "pubsub")
    assert graph.nodes[SUB].subtype == "pubsub_subscription"
    assert graph.nodes[SUBNET].placement.vpc == VPC and graph.nodes[SUBNET].kind == NodeKind.NETWORK


def test_zone_is_the_az_and_names_the_region():
    vm = _r("google_compute_instance.vm", {"name": "vm", "zone": "us-west1-b",
                                           "network_interface": [{"network": "${" + VPC + ".id}"}]})
    cluster = _r("google_container_cluster.gke", {"name": "gke", "location": "europe-west1-c"})
    node = GcpNormaliser().normalise(RawResources(format="terraform", resources=[vm, cluster])).nodes
    p = node["google_compute_instance.vm"].placement
    assert (p.region, p.az, p.vpc) == ("us-west1", "us-west1-b", VPC)   # the resource's zone wins over the provider
    p = node["google_container_cluster.gke"].placement
    assert (p.region, p.az) == ("europe-west1", "europe-west1-c")      # a zonal GKE `location` is a zone


def test_provider_zone_is_a_fallback_for_zonal_types_only():
    vm = _r("google_compute_instance.vm", {"name": "vm", "_provider_zone": "us-central1-a"})
    sql = _r(SQL, {"name": "db", "_provider_zone": "us-central1-a"})
    nodes = GcpNormaliser().normalise(RawResources(format="terraform", resources=[vm, sql])).nodes
    assert nodes["google_compute_instance.vm"].placement.az == "us-central1-a"
    assert nodes[SQL].placement.az is None                              # regional: a zone would be a lie


def test_multi_region_location_falls_back_to_the_provider_region():
    bucket = _r("google_storage_bucket.assets", {"name": "assets", "location": "US"})
    node = GcpNormaliser().normalise(RawResources(format="terraform", resources=[bucket])).nodes[bucket.address]
    assert (node.placement.region, node.attrs["location"]) == ("us-central1", "US")


def test_capacity_attrs_are_flat_scalars(graph):
    run, sql = graph.nodes[RUN], graph.nodes[SQL]
    assert {k: run.attrs[k] for k in ("max_instances", "min_instances", "concurrency", "cpu", "memory")} == {
        "max_instances": 10, "min_instances": 1, "concurrency": 80, "cpu": "1", "memory": "512Mi"}
    assert "template" not in run.attrs                                  # the container spec never reaches graph.json
    assert sql.attrs["tier"] == "db-f1-micro"


def test_mig_carries_its_instance_count():
    mig = _r("google_compute_instance_group_manager.web", {
        "name": "web", "zone": "us-central1-a", "target_size": 3,
        "version": [{"instance_template": "${google_compute_instance_template.web.id}"}]})
    node = GcpNormaliser().normalise(RawResources(format="terraform", resources=[mig])).nodes[mig.address]
    assert (node.subtype, node.attrs["instances"], node.attrs["instance_template"]) == (
        "gce", 3, "google_compute_instance_template.web")


def test_cloud_run_v1_annotations_are_read():
    v1 = _r("google_cloud_run_service.legacy", {"name": "legacy", "location": "us-central1", "template": [{
        "metadata": [{"annotations": {"autoscaling.knative.dev/maxScale": "5"}}],
        "spec": [{"container_concurrency": 40}]}]})
    node = GcpNormaliser().normalise(RawResources(format="terraform", resources=[v1])).nodes[v1.address]
    assert (node.subtype, node.attrs["max_instances"], node.attrs["concurrency"]) == ("cloud_run", 5, 40)


# ------------------------------------------------------------- internet + entry points (G13)

def test_internet_node_enters_at_the_forwarding_rule_only(graph):
    internet = graph.nodes["internet"]
    assert (internet.kind, internet.subtype) == (NodeKind.EXTERNAL, "internet")
    entries = [e for e in graph.edges if e.src == "internet"]
    assert [e.dst for e in entries] == [RULE]
    assert entries[0].kind == EdgeKind.INVOKE and entries[0].rule == "normaliser"
    assert "public entry point" in entries[0].evidence


def test_gateways_and_cdns_are_entry_points():
    raws = [_r("google_api_gateway_gateway.gw", {"name": "gw"}),
            _r("google_firebase_hosting_site.web", {"site_id": "w"}),
            _r("google_compute_forwarding_rule.internal", {"name": "i", "region": "us-central1"})]
    g = GcpNormaliser().normalise(RawResources(format="terraform", resources=raws))
    assert sorted(e.dst for e in g.edges if e.src == "internet") == sorted(r.address for r in raws)
    assert len(g.edges) == 3


# ------------------------------------------------------------- twins, NEGs, CDN

@pytest.mark.parametrize("global_type, regional_type", [
    ("google_compute_url_map", "google_compute_region_url_map"),
    ("google_compute_target_http_proxy", "google_compute_region_target_http_proxy"),
    ("google_compute_target_https_proxy", "google_compute_region_target_https_proxy"),
    ("google_compute_backend_service", "google_compute_region_backend_service"),
    ("google_compute_global_forwarding_rule", "google_compute_forwarding_rule"),
    ("google_compute_instance_group_manager", "google_compute_region_instance_group_manager"),
])
def test_global_and_regional_twins_map_to_the_same_subtype(global_type, regional_type):
    assert TYPE_MAP[global_type] == TYPE_MAP[regional_type]
    assert TYPE_MAP[global_type][0] in (NodeKind.LB, NodeKind.COMPUTE)


def test_all_three_neg_types_are_the_neg_subtype():
    for t in ("google_compute_network_endpoint_group", "google_compute_region_network_endpoint_group",
              "google_compute_global_network_endpoint_group"):
        assert TYPE_MAP[t] == (NodeKind.LB, "neg"), t
    # ...and the single-endpoint glue that shares their prefix is not a node
    g = GcpNormaliser().normalise(RawResources(format="terraform", resources=[
        _r("google_compute_network_endpoint.one", {}), _r("google_compute_global_network_endpoint.two", {})]))
    assert list(g.nodes) == ["internet"]


def test_cloud_cdn_is_an_attribute_on_the_backend_service(graph):
    backend = graph.nodes[BACKEND]
    assert (backend.kind, backend.subtype, backend.attrs["enable_cdn"]) == (NodeKind.LB, "backend_service", True)


def test_chain_nodes_are_all_lb_kind(graph):
    for node_id in (RULE, PROXY, URLMAP, BACKEND, NEG):
        assert graph.nodes[node_id].kind == NodeKind.LB and graph.nodes[node_id].subtype in CHAIN


# ------------------------------------------------------------- ignore list and the unknown bucket

def test_iam_grants_match_by_suffix_and_never_become_nodes(graph):
    for dropped in ("google_project_iam_member.run_sql", "google_cloud_run_v2_service_iam_member.public",
                    "google_nonexistent_thing_iam_binding.made_up", "google_project_service.run",
                    "google_compute_health_check.hc", "google_compute_autoscaler.mystery"):
        assert dropped not in graph.nodes
    assert not any("iam" in w for w in graph.warnings)


def test_unknown_google_type_is_kept_as_a_network_node_with_a_warning():
    odd = _r("google_new_thing.x", {"name": "x"})
    g = GcpNormaliser().normalise(RawResources(format="terraform", resources=[odd]))
    assert (g.nodes[odd.address].kind, g.nodes[odd.address].subtype) == (NodeKind.NETWORK, "google_new_thing")
    assert g.warnings == ["google_new_thing.x: unknown type google_new_thing; kept as a network node"]


def test_no_warnings_on_the_clean_stack(graph):
    assert graph.warnings == []


# ------------------------------------------------------------- the G11 naming rule

def test_no_gcp_subtype_reuses_an_aws_name_outside_network():
    aws_names = {s for k, s in aws.TYPE_MAP.values() if k not in NOT_A_HOP}
    gcp_names = {s for k, s in gcp.TYPE_MAP.values() if k not in NOT_A_HOP}
    assert not aws_names & gcp_names
