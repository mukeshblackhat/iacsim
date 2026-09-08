"""Real-world Terraform must not crash, and every warning must be a named one.

Fixtures under examples/real-world/ are unmodified public projects (see each
ATTRIBUTION.md). The table below is the contract per fixture: the minimum node
count the parser must find, and the warning phrases that are allowed.
"""

from __future__ import annotations

import json
from itertools import pairwise
from pathlib import Path

import pytest
from conftest import EXAMPLES, example_copy
from typer.testing import CliRunner

from iacsim.cli import app
from iacsim.core.config import load_config
from iacsim.core.models import EdgeKind
from iacsim.core.pipeline import build_graph

REAL_WORLD = EXAMPLES / "real-world"

KNOWN_WARNINGS = (
    "data.* sources are not evaluated",
    "remote source",
    "unknown type",
    "region not resolved",
    "region unknown",
    "duplicate resource",
    "count not resolved",
    "for_each not resolved",
    "dynamic",
    "not evaluated",
)

# fixture → (minimum non-network nodes, region flag needed)
MIN_NODES = {
    "serverless-apigw-lambda-dynamodb": 4,
    "serverless-pipes-sqs-stepfunctions": 3,
    "ecs-alb": 3,
    "two-tier": 2,
    "eks-cluster": 0,
    # GCP (M11): the exact non-network count `iacsim graph` reports today, so a
    # type silently dropping out of TYPE_MAP shows up here first
    "gcp-ntier-serverless-web": 10,
    "gcp-glb-mig-backend": 5,
    "gcp-cloudrun-multiregion-glb": 11,
    "gcp-functions-firestore-pubsub": 13,
    "gcp-gke-multitenant": 2,
    "gcp-eventarc-workflows-run": 3,
    "gcp-lb-regional": 5,
}


def _fixtures() -> list[Path]:
    return sorted(p for p in REAL_WORLD.iterdir() if p.is_dir())


@pytest.mark.parametrize("fixture", _fixtures(), ids=lambda p: p.name)
def test_graph_builds_and_every_warning_is_named(fixture: Path):
    cfg = load_config(fixture, {"parsers.terraform.region": "us-east-1"})
    graph, _ = build_graph(fixture, cfg)
    real_nodes = [n for n in graph.nodes.values() if n.kind not in ("network", "external")]
    assert len(real_nodes) >= MIN_NODES[fixture.name], [n.id for n in real_nodes]
    unknown = [w for w in graph.warnings if not any(phrase in w for phrase in KNOWN_WARNINGS)]
    assert unknown == [], unknown


@pytest.mark.parametrize("fixture", _fixtures(), ids=lambda p: p.name)
def test_run_exits_zero(fixture: Path, tmp_path):
    copy = example_copy(f"real-world/{fixture.name}", tmp_path)
    result = CliRunner().invoke(app, ["run", str(copy), "--region", "us-east-1", "-o", "json"])
    assert result.exit_code == 0, result.output
    report = json.loads((copy / ".iacsim" / "report.json").read_text())
    # no scenarios.yaml in the corpus: every fixture the internet can enter gets an inferred path
    enters = any(e["src"] == "internet" for e in report["graph"]["edges"])
    assert bool(report["scenarios"]) == enters, [s["name"] for s in report["scenarios"]]
    assert all(s["source"] == "inferred" for s in report["scenarios"])


def test_ecs_alb_routes_to_the_service_and_sees_the_asg():
    fixture = REAL_WORLD / "ecs-alb"
    graph, _ = build_graph(fixture, load_config(fixture, {"parsers.terraform.region": "us-east-1"}))
    alb = next(n.id for n in graph.nodes.values() if n.subtype == "alb")
    service = next(n.id for n in graph.nodes.values() if n.subtype == "fargate")
    asg = graph.nodes["aws_autoscaling_group.app"]
    edge = graph.find_edge(alb, service)
    assert edge is not None and edge.kind == EdgeKind.ROUTE and edge.rule == "target_group"
    assert asg.subtype == "ec2" and asg.attrs.get("instances")  # desired_capacity from the ASG


def test_two_tier_classic_elb_routes_to_the_instance():
    fixture = REAL_WORLD / "two-tier"
    graph, _ = build_graph(fixture, load_config(fixture))
    edge = graph.find_edge("aws_elb.web", "aws_instance.web")
    assert edge is not None and edge.kind == EdgeKind.ROUTE
    assert graph.warnings == []          # provisioner/connection blocks are skipped, tfvars template ignored


def test_eks_cluster_names_every_registry_module_once():
    fixture = REAL_WORLD / "eks-cluster"
    graph, _ = build_graph(fixture, load_config(fixture))
    remote = [w for w in graph.warnings if "remote source" in w]
    assert len(remote) == 3 and all("terraform init" in w for w in remote)


# ------------------------------------------------------------------ GCP corpus (M11)

GCP_CHAIN = ["forwarding_rule", "target_proxy", "url_map", "backend_service"]


def _gcp(name: str, region: str | None = "us-central1"):
    fixture = REAL_WORLD / name
    overrides = {"parsers.terraform.region": region} if region else None
    graph, _ = build_graph(fixture, load_config(fixture, overrides))
    return graph


def _route_path(graph, start: str) -> list[str]:
    """Follow the single ROUTE edge out of each node from `start`: the LB chain in order."""
    path = [start]
    while True:
        out = [e for e in graph.edges if e.src == path[-1] and e.kind == EdgeKind.ROUTE]
        if len(out) != 1 or out[0].dst in path:
            return path
        path.append(out[0].dst)


def _shape(graph) -> set[tuple]:
    """Edges as (src subtype, dst subtype, kind, rule): the same for a global and a regional LB."""
    return {(graph.nodes[e.src].subtype, graph.nodes[e.dst].subtype, e.kind, e.rule) for e in graph.edges}


def _reachable(graph, start: str) -> set[str]:
    seen, todo = set(), [start]
    while todo:
        node = todo.pop()
        if node in seen:
            continue
        seen.add(node)
        todo += [e.dst for e in graph.edges if e.src == node]
    return seen


def test_gcp_glb_mig_backend_walks_the_minimal_chain_onto_the_mig():
    graph = _gcp("gcp-glb-mig-backend")
    assert graph.warnings == []
    path = _route_path(graph, "google_compute_global_forwarding_rule.default")
    assert [graph.nodes[n].subtype for n in path] == GCP_CHAIN + ["gce"]
    for src, dst in pairwise(path):
        edge = graph.find_edge(src, dst)
        assert edge.kind == EdgeKind.ROUTE and edge.rule == "gcp_lb_chain" and " routes to " in edge.evidence
    assert [e.dst for e in graph.edges if e.src == "internet"] == [path[0]]
    assert graph.nodes[path[-1]].attrs["instances"] == 2                   # target_size of the MIG
    assert not [n for n in graph.nodes if "health_check" in n]              # glue, never a hop
    assert len(graph.edges) == 1 + len(GCP_CHAIN)


def test_gcp_lb_regional_has_the_same_shape_from_the_region_twin_types():
    regional, glob = _gcp("gcp-lb-regional"), _gcp("gcp-glb-mig-backend")
    assert regional.warnings == []
    assert {"google_compute_region_url_map.default", "google_compute_region_target_http_proxy.default",
            "google_compute_region_backend_service.default", "google_compute_forwarding_rule.default"} <= set(
        regional.nodes)
    path = _route_path(regional, "google_compute_forwarding_rule.default")
    assert [regional.nodes[n].subtype for n in path] == GCP_CHAIN + ["gce"]
    assert _shape(regional) == _shape(glob)
    assert all(regional.nodes[n].placement.region == "us-west1" for n in path)


def test_gcp_cloudrun_multiregion_serves_two_regions_from_one_forwarding_rule(default_profile):
    graph = _gcp("gcp-cloudrun-multiregion-glb")
    assert graph.warnings == []
    runs = {n.id: n.placement.region for n in graph.nodes.values() if n.subtype == "cloud_run"}
    assert runs == {"google_cloud_run_v2_service.run_default[0]": "us-central1",
                    "google_cloud_run_v2_service.run_default[1]": "europe-west1"}
    assert set(runs) <= _reachable(graph, "google_compute_global_forwarding_rule.lb_default")
    negs = [e.dst for e in graph.edges if e.src == "google_compute_backend_service.lb_default"]
    assert sorted(negs) == ["google_compute_region_network_endpoint_group.lb_default[0]",
                            "google_compute_region_network_endpoint_group.lb_default[1]"]
    # the pair is a known distance, so a hop between the two regions would never fall back to the default
    assert "europe-west1/us-central1" in default_profile.distance["cross_region"]
    # the HTTP → HTTPS redirect url_map is a terminal, not a broken chain
    assert not [e for e in graph.edges if e.src == "google_compute_url_map.https_default"]
    assert sorted(e.dst for e in graph.edges if e.src == "internet") == [
        "google_compute_global_forwarding_rule.https_default", "google_compute_global_forwarding_rule.lb_default"]


def test_gcp_cloudrun_multiregion_prices_the_chain_at_zero_into_both_regions(default_profile):
    """G9: the global backend service fans out to a NEG in each region; neither
    hop is a cross-region wire, and the hop out of each NEG is priced in-region."""
    from iacsim.core.pipeline import run
    fixture = REAL_WORLD / "gcp-cloudrun-multiregion-glb"
    output = run(fixture, load_config(fixture, {"parsers.terraform.region": "us-central1"}))
    d = default_profile.distance
    hops = [h for r in output.results for h in r.hops if h.src != "internet"]
    assert hops
    for hop in hops:
        src, dst = output.graph.nodes[hop.src], output.graph.nodes[hop.dst]
        if dst.subtype == "cloud_run":
            assert src.placement.region == dst.placement.region
            assert hop.breakdown["distance"] == pytest.approx(2 * d["same_region_unknown_az"])
        else:
            assert hop.breakdown.get("distance", 0) == 0, (hop.src, hop.dst)


def test_gcp_functions_pubsub_topic_fans_out_to_the_push_subscriber():
    graph = _gcp("gcp-functions-firestore-pubsub")
    topic, sub = "google_pubsub_topic.topic", "google_pubsub_subscription.subscription"
    client, check = "google_cloudfunctions2_function.client_function", "google_cloudfunctions2_function.check_function"
    fan_out = graph.find_edge(topic, sub)
    assert fan_out.kind == EdgeKind.PUBLISH and fan_out.rule == "gcp_pubsub_push"
    assert "subscribes to" in fan_out.evidence
    push = graph.find_edge(sub, client)
    assert push.kind == EdgeKind.CONSUME and push.rule == "gcp_pubsub_push" and "push_config" in push.evidence
    assert graph.find_edge(topic, client) is None                               # delivery goes through the subscription
    scheduled = graph.find_edge("google_cloud_scheduler_job.job", check)
    assert scheduled.kind == EdgeKind.INVOKE and set(scheduled.rule.split("+")) == {"gcp_workflows", "gcp_iam_binding"}
    assert graph.nodes["google_firestore_database.database"].subtype == "firestore"
    unexpected = [w for w in graph.warnings
                  if "data.* sources are not evaluated" not in w and "unknown function basename()" not in w]
    assert unexpected == []


def test_gcp_eventarc_trigger_hands_the_bucket_event_to_the_workflow_that_runs_the_job():
    graph = _gcp("gcp-eventarc-workflows-run")
    bucket, wf = "google_storage_bucket.default", "google_workflows_workflow.default"
    job = "google_cloud_run_v2_job.default"
    assert graph.nodes[wf].subtype == "workflows" and "google_eventarc_trigger.default" not in graph.nodes
    steps = graph.nodes[wf].attrs["workflow"]
    assert steps and steps[0]["type"] == "Choice"
    assert any(t.get("target") == job for branch in steps[0]["branches"].values() for t in branch)
    delivered = graph.find_edge(bucket, wf)
    assert delivered.kind == EdgeKind.CONSUME and delivered.rule == "gcp_eventarc"
    assert "google.cloud.storage.object.v1.finalized" in delivered.evidence
    ran = graph.find_edge(wf, job)
    assert ran.kind == EdgeKind.INVOKE and ran.rule == "gcp_workflows" and "step 'run_job'" in ran.evidence
    assert graph.find_edge(job, bucket).rule == "env_var"                       # INPUT_BUCKET
    # the workflow body's `$${sys.get_env(...)}` escapes are Workflows expressions, not HCL to evaluate
    assert graph.warnings == ["root module: data.* sources are not evaluated; references to data.X stay unresolved"]


def test_gcp_gke_multitenant_keeps_the_cluster_and_ignores_the_kubernetes_provider_silently():
    with_region, without = _gcp("gcp-gke-multitenant"), _gcp("gcp-gke-multitenant", region=None)
    for graph in (with_region, without):
        assert set(graph.nodes) == {"google_container_cluster.default", "google_sql_database_instance.default",
                                    "internet"}
        assert graph.nodes["google_container_cluster.default"].subtype == "gke_cluster"
        assert graph.nodes["google_sql_database_instance.default"].placement.region == "us-central1"
        # kubernetes_config_map is dropped without a word; provider "kubernetes" has no region to resolve
        assert graph.warnings == ["root module: data.* sources are not evaluated; references to data.X stay unresolved"]


def test_gcp_ntier_serverless_web_reaches_both_datastores_behind_the_full_chain():
    graph = _gcp("gcp-ntier-serverless-web")
    assert graph.warnings == []
    frontend, backend = "google_cloud_run_v2_service.frontend", "google_cloud_run_v2_service.backend_application"
    db, cache = "google_sql_database_instance.private_db", "google_redis_instance.private_cache[0]"
    path = _route_path(graph, "google_compute_global_forwarding_rule.https")
    assert [graph.nodes[n].subtype for n in path] == GCP_CHAIN + ["neg", "cloud_run"] and path[-1] == frontend
    tier = graph.find_edge(frontend, backend)
    assert tier.kind == EdgeKind.INVOKE and set(tier.rule.split("+")) == {"env_var", "gcp_iam_binding"}
    read_db = graph.find_edge(backend, db)
    assert read_db.kind == EdgeKind.READ and set(read_db.rule.split("+")) == {"env_var", "gcp_iam_binding"}
    assert "connection_name" in read_db.evidence and "roles/cloudsql.client" in read_db.evidence
    read_cache = graph.find_edge(backend, cache)                              # a `dynamic "env"` inside containers {}
    assert read_cache.kind == EdgeKind.READ and read_cache.rule == "env_var" and "REDIS_HOST" in read_cache.evidence
    # the PSC endpoint (load_balancing_scheme = "") is the consumer side of Cloud SQL, not a public entry
    assert "google_compute_forwarding_rule.db_psc_endpoint" in graph.nodes
    assert [e.dst for e in graph.edges if e.src == "internet"] == [path[0]]
