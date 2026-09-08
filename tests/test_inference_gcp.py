"""WP5: the five GCP inference rules plus the generalised `env_var` and
`vpc_peering`, each on a hand-built RawResources pushed through
`GcpNormaliser().normalise` and then the rule — the way test_normaliser_gcp.py
hand-builds its stack. Every test asserts kind / ops / rule / an evidence
substring, and at least one edge that must NOT exist (docs/gcp/03-TESTING.md §3.3).
"""

from pathlib import Path

from iacsim.core.config import DEFAULTS, load_config
from iacsim.core.interfaces import INFERENCE_RULES
from iacsim.core.models import Confidence, EdgeKind, NodeKind, RawResource, RawResources
from iacsim.core.pipeline import _merge_edge, build_graph
from iacsim.core.registry import load_builtin_plugins
from iacsim.graph.normalisers.gcp import GcpNormaliser

ROOT = Path(__file__).resolve().parent.parent
GCP_RULES = ("gcp_lb_chain", "gcp_iam_binding", "gcp_eventarc", "gcp_pubsub_push", "gcp_workflows")

RUN, RUN2 = "google_cloud_run_v2_service.api", "google_cloud_run_v2_service.worker"
FN1, FN2 = "google_cloudfunctions_function.legacy", "google_cloudfunctions2_function.handler"
SQL, FIRESTORE, BUCKET, BQ = ("google_sql_database_instance.db", "google_firestore_database.docs",
                              "google_storage_bucket.assets", "google_bigquery_table.events")
TOPIC, SUB, DLQ = "google_pubsub_topic.orders", "google_pubsub_subscription.orders", "google_pubsub_topic.dead"
SA, WF = "google_service_account.api", "google_workflows_workflow.pipeline"
JOB_SCHED = "google_cloud_scheduler_job.tick"
VM, MIG, VPC_A, VPC_B = ("google_compute_instance.vm", "google_compute_instance_group_manager.web",
                         "google_compute_network.a", "google_compute_network.b")


def setup_module():
    load_builtin_plugins()


def _r(address: str, attrs: dict | None = None) -> RawResource:
    rtype = address.split(".")[-2] if address.startswith("module.") else address.split(".")[0]
    return RawResource(address=address, type=rtype, attrs=attrs or {}, region="us-central1")


def ref(address: str, attr: str = "id") -> str:
    return "${" + address + "." + attr + "}"


def _infer(resources: list[RawResource], *rules: str):
    """Normalise, run the named rules and merge the way the pipeline does."""
    raw = RawResources(format="terraform", resources=resources)
    graph = GcpNormaliser().normalise(raw)
    index = {(e.src, e.dst): e for e in graph.edges}
    for name in rules:
        for edge in INFERENCE_RULES.get(name)().apply(graph, raw):
            edge.rule = name
            _merge_edge(graph, index, edge)
    return graph


def rules(e) -> set[str]:
    return set((e.rule or "").split("+"))


# ------------------------------------------------------------------ registration

def test_every_gcp_rule_is_registered_and_in_the_default_list():
    for name in GCP_RULES:
        assert name in INFERENCE_RULES
        assert name in DEFAULTS["inference"]["rules"]
    order = DEFAULTS["inference"]["rules"]
    assert order.index("gcp_workflows") < order.index("env_var")          # early, like step_functions
    assert order.index("gcp_lb_chain") < order.index("env_var")


# ------------------------------------------------------------------ gcp_lb_chain

RULE_G, PROXY_G, MAP_G = ("google_compute_global_forwarding_rule.lb", "google_compute_target_https_proxy.lb",
                          "google_compute_url_map.lb")
BACKEND_G, NEG_G = "google_compute_backend_service.api", "google_compute_region_network_endpoint_group.api"
BUCKET_BACKEND, UNUSED = "google_compute_backend_bucket.static", "google_compute_backend_service.unused"
REDIRECT_MAP = "google_compute_url_map.redirect"


def _global_chain() -> list[RawResource]:
    """Three reference styles on purpose: .id, .self_link and .name must all join (02-TYPE-MAP.md)."""
    return [
        _r(RULE_G, {"name": "lb", "target": ref(PROXY_G, "id")}),
        _r(PROXY_G, {"name": "lb", "url_map": ref(MAP_G, "self_link")}),
        _r(MAP_G, {"name": "lb", "default_service": ref(BACKEND_G, "name"),
                   "path_matcher": [{"name": "static", "default_service": ref(BACKEND_G, "id"),
                                     "path_rule": [{"paths": ["/static/*"], "service": ref(BUCKET_BACKEND, "id")}]}]}),
        _r(REDIRECT_MAP, {"name": "redirect", "default_url_redirect": [{"https_redirect": True}]}),
        _r(BACKEND_G, {"name": "api", "health_checks": [ref("google_compute_health_check.hc", "id")],
                       "backend": [{"group": ref(NEG_G, "id")}]}),
        _r(UNUSED, {"name": "unused", "backend": [{"group": ref(NEG_G, "id")}]}),
        _r("google_compute_health_check.hc", {"name": "hc"}),
        _r(BUCKET_BACKEND, {"name": "static", "bucket_name": ref(BUCKET, "name")}),
        _r(BUCKET, {"name": "assets"}),
        _r(NEG_G, {"name": "api", "region": "us-central1", "cloud_run": [{"service": ref(RUN, "name")}]}),
        _r(RUN, {"name": "api"}),
    ]


def test_lb_chain_global_serverless_neg_every_hop_is_a_route():
    graph = _infer(_global_chain(), "gcp_lb_chain")
    for src, dst, attr in [(RULE_G, PROXY_G, "target"), (PROXY_G, MAP_G, "url_map"),
                           (MAP_G, BACKEND_G, "default_service"), (BACKEND_G, NEG_G, "backend.group"),
                           (NEG_G, RUN, "cloud_run.service"), (BUCKET_BACKEND, BUCKET, "bucket_name")]:
        e = graph.find_edge(src, dst)
        assert e is not None, (src, dst)
        assert e.kind == EdgeKind.ROUTE and e.ops == [EdgeKind.ROUTE] and e.confidence == Confidence.HIGH
        assert e.rule == "gcp_lb_chain" and attr in e.evidence and "routes to" in e.evidence
    static = graph.find_edge(MAP_G, BUCKET_BACKEND)
    assert static.kind == EdgeKind.ROUTE and "path_matcher.path_rule.service" in static.evidence
    # negatives: the health check is not a node, the unused backend is never routed to, a redirect map is a terminal
    assert "google_compute_health_check.hc" not in graph.nodes
    assert not [e for e in graph.edges if e.dst == UNUSED]
    assert not [e for e in graph.edges if e.src == REDIRECT_MAP]
    assert graph.warnings == []
    assert graph.find_edge("internet", RULE_G) is not None and graph.find_edge("internet", MAP_G) is None


RULE_R, PROXY_R, MAP_R = ("google_compute_forwarding_rule.lb", "google_compute_region_target_http_proxy.lb",
                          "google_compute_region_url_map.lb")
BACKEND_R, NEG_Z, ENDPOINT = ("google_compute_region_backend_service.web",
                              "google_compute_network_endpoint_group.vms", "google_compute_network_endpoint.vm")


def test_lb_chain_regional_twins_mig_and_zonal_neg():
    graph = _infer([
        _r(RULE_R, {"name": "lb", "region": "us-central1", "target": ref(PROXY_R, "id")}),
        _r(PROXY_R, {"name": "lb", "url_map": ref(MAP_R, "id")}),
        _r(MAP_R, {"name": "lb", "default_service": ref(BACKEND_R, "id")}),
        _r(BACKEND_R, {"name": "web", "backend": [{"group": ref(MIG, "instance_group")}, {"group": ref(NEG_Z, "id")}]}),
        _r(MIG, {"name": "web", "target_size": 3,
                 "version": [{"instance_template": ref("google_compute_instance_template.t", "id")}]}),
        _r("google_compute_instance_template.t", {"name": "t"}),
        _r(NEG_Z, {"name": "vms", "zone": "us-central1-a"}),
        _r(ENDPOINT, {"network_endpoint_group": ref(NEG_Z, "name"), "instance": ref(VM, "name"), "port": 8080}),
        _r(VM, {"name": "vm", "zone": "us-central1-a"}),
    ], "gcp_lb_chain")
    chain = [(RULE_R, PROXY_R), (PROXY_R, MAP_R), (MAP_R, BACKEND_R), (BACKEND_R, MIG), (BACKEND_R, NEG_Z), (NEG_Z, VM)]
    for src, dst in chain:
        e = graph.find_edge(src, dst)
        assert e is not None and e.kind == EdgeKind.ROUTE and e.rule == "gcp_lb_chain", (src, dst)
    assert graph.nodes[MIG].kind == NodeKind.COMPUTE and graph.nodes[MIG].attrs["instances"] == 3
    assert "network_endpoint.vm registers" in graph.find_edge(NEG_Z, VM).evidence
    assert graph.find_edge(MIG, VM) is None                           # a MIG is the node; its VMs are not expanded
    assert "google_compute_instance_template.t" not in graph.nodes


# ------------------------------------------------------------------ gcp_iam_binding

WORKER_EMAIL = "worker-sa@demo.iam.gserviceaccount.com"


def _iam_stack() -> list[RawResource]:
    return [
        _r(SA, {"account_id": "api-sa"}),
        _r(RUN, {"name": "api", "template": [{"service_account": ref(SA, "email")}]}),
        _r(RUN2, {"name": "worker", "template": [{"service_account": WORKER_EMAIL}]}),
        _r("google_service_account.worker", {"account_id": "worker-sa"}),
        _r(FIRESTORE, {"name": "(default)"}),
        _r(BUCKET, {"name": "assets"}),
        _r(TOPIC, {"name": "orders"}),
        _r(SUB, {"name": "orders", "topic": ref(TOPIC)}),
        _r("google_project_iam_member.datastore", {"project": "demo", "role": "roles/datastore.user",
                                                   "member": "serviceAccount:" + ref(SA, "email")}),
        _r("google_storage_bucket_iam_member.viewer", {"bucket": ref(BUCKET, "name"),
                                                       "role": "roles/storage.objectViewer",
                                                       "member": "serviceAccount:" + ref(SA, "email")}),
        _r("google_pubsub_topic_iam_binding.publisher", {"topic": ref(TOPIC, "name"), "role": "roles/pubsub.publisher",
                                                         "members": ["serviceAccount:" + ref(SA, "email")]}),
        _r("google_pubsub_topic_iam_member.subscriber", {"topic": ref(TOPIC, "name"), "role": "roles/pubsub.subscriber",
                                                         "member": "serviceAccount:" + WORKER_EMAIL}),
        _r("google_cloud_run_v2_service_iam_member.invoker", {"name": ref(RUN2, "name"), "role": "roles/run.invoker",
                                                              "member": "serviceAccount:" + ref(SA, "email")}),
        _r("google_project_iam_member.no_meaning", {"project": "demo", "role": "roles/iam.serviceAccountUser",
                                                    "member": "serviceAccount:" + ref(SA, "email")}),
        _r("google_project_iam_member.log", {"project": "demo", "role": "roles/logging.logWriter",
                                             "member": "serviceAccount:" + ref(SA, "email")}),
        _r("google_project_iam_member.primitive", {"project": "demo", "role": "roles/editor",
                                                   "member": "serviceAccount:" + ref(SA, "email")}),
        _r("google_project_iam_member.public", {"project": "demo", "role": "roles/run.invoker", "member": "allUsers"}),
    ]


def test_iam_binding_roles_become_ops_by_target_kind():
    graph = _infer(_iam_stack(), "gcp_iam_binding")
    docs = graph.find_edge(RUN, FIRESTORE)                                 # project-wide grant → every firestore
    assert docs.kind == EdgeKind.READ and docs.ops == [EdgeKind.READ, EdgeKind.WRITE]
    assert docs.confidence == Confidence.MEDIUM and docs.rule == "gcp_iam_binding"
    assert "roles/datastore.user" in docs.evidence and "google_service_account.api" in docs.evidence
    assert "service account of google_cloud_run_v2_service.api" in docs.evidence
    assets = graph.find_edge(RUN, BUCKET)                                  # resource-scoped viewer → READ only
    assert assets.kind == EdgeKind.READ and assets.ops == [EdgeKind.READ] and "objectViewer" in assets.evidence
    pub = graph.find_edge(RUN, TOPIC)
    assert pub.kind == EdgeKind.PUBLISH and pub.ops == [EdgeKind.PUBLISH] and "roles/pubsub.publisher" in pub.evidence
    inv = graph.find_edge(RUN, RUN2)
    assert inv.kind == EdgeKind.INVOKE and "roles/run.invoker" in inv.evidence


def test_iam_binding_subscriber_is_a_consumer_from_the_queue_and_literal_emails_resolve():
    graph = _infer(_iam_stack(), "gcp_iam_binding")
    con = graph.find_edge(TOPIC, RUN2)                                     # worker's SA is a literal email
    assert con is not None and con.kind == EdgeKind.CONSUME and con.ops == [EdgeKind.CONSUME]
    assert "roles/pubsub.subscriber" in con.evidence and WORKER_EMAIL in con.evidence
    assert graph.find_edge(RUN2, TOPIC) is None                            # never a call into the topic
    # negatives: control-plane / primitive roles and allUsers draw nothing; a principal never targets itself
    assert not [e for e in graph.edges if e.rule == "gcp_iam_binding" and e.src == "internet"]
    assert not [e for e in graph.edges if "serviceAccountUser" in e.evidence or "logWriter" in e.evidence]
    assert not [e for e in graph.edges if "roles/editor" in e.evidence]
    assert graph.find_edge(RUN2, RUN2) is None and graph.find_edge(RUN, SUB) is None


def test_iam_binding_workload_identity_binds_to_the_gke_cluster():
    cluster = "google_container_cluster.main"
    graph = _infer([
        _r(cluster, {"name": "main", "location": "us-central1",
                     "workload_identity_config": [{"workload_pool": "demo.svc.id.goog"}]}),
        _r(BUCKET, {"name": "assets"}),
        _r("google_storage_bucket_iam_member.ksa", {"bucket": ref(BUCKET, "name"), "role": "roles/storage.objectAdmin",
                                                    "member": "serviceAccount:demo.svc.id.goog[shop/api]"}),
    ], "gcp_iam_binding")
    e = graph.find_edge(cluster, BUCKET)
    assert e.kind == EdgeKind.READ and e.ops == [EdgeKind.READ, EdgeKind.WRITE] and "workload identity" in e.evidence
    assert "demo.svc.id.goog[shop/api]" not in graph.nodes


# ------------------------------------------------------------------ gcp_eventarc

PUBLISHED = "google.cloud.pubsub.topic.v1.messagePublished"
FINALIZED = "google.cloud.storage.object.v1.finalized"
TRIGGER, TRIGGER_GCS, TRIGGER_AUDIT = ("google_eventarc_trigger.orders", "google_eventarc_trigger.uploads",
                                        "google_eventarc_trigger.audit")


def test_eventarc_trigger_draws_source_to_destination_and_skips_the_trigger_node():
    graph = _infer([
        _r(TOPIC, {"name": "orders"}), _r(BUCKET, {"name": "assets"}), _r(RUN, {"name": "api"}),
        _r(WF, {"name": "pipeline", "source_contents": "main:\n  steps: []\n"}),
        _r(FN2, {"name": "handler", "event_trigger": [{"event_type": PUBLISHED,
                                                       "pubsub_topic": ref(TOPIC, "id")}]}),
        _r(FN1, {"name": "legacy", "event_trigger": [{"event_type": "google.storage.object.finalize",
                                                      "resource": "assets"}]}),
        _r(TRIGGER, {"name": "orders", "location": "us-central1",
                     "matching_criteria": [{"attribute": "type", "value": PUBLISHED}],
                     "transport": [{"pubsub": [{"topic": ref(TOPIC, "id")}]}],
                     "destination": [{"cloud_run_service": [{"service": ref(RUN, "name"), "region": "us-central1"}]}]}),
        _r(TRIGGER_GCS, {"name": "uploads",
                         "matching_criteria": [{"attribute": "type", "value": FINALIZED},
                                               {"attribute": "bucket", "value": ref(BUCKET, "name")}],
                         "destination": [{"workflow": ref(WF, "id")}]}),
        _r(TRIGGER_AUDIT, {"name": "audit",
                           "matching_criteria": [{"attribute": "type", "value": "google.cloud.audit.log.v1.written"},
                                                 {"attribute": "serviceName", "value": "storage.googleapis.com"}],
                           "destination": [{"cloud_run_service": [{"service": ref(RUN, "name")}]}]}),
        _r("google_storage_notification.uploads", {"bucket": ref(BUCKET, "name"), "topic": ref(TOPIC, "id"),
                                                   "event_types": ["OBJECT_FINALIZE"]}),
    ], "gcp_eventarc")
    e = graph.find_edge(TOPIC, RUN)
    assert e.kind == EdgeKind.CONSUME and e.confidence == Confidence.HIGH and e.rule == "gcp_eventarc"
    assert "messagePublished" in e.evidence and "eventarc_trigger.orders" in e.evidence
    up = graph.find_edge(BUCKET, WF)
    assert up.kind == EdgeKind.CONSUME and "object.v1.finalized" in up.evidence
    assert graph.find_edge(TOPIC, FN2).kind == EdgeKind.CONSUME              # a function's own v2 trigger
    legacy = graph.find_edge(BUCKET, FN1)                                   # v1: a literal resource name
    assert legacy.kind == EdgeKind.CONSUME and "object.finalize" in legacy.evidence
    note = graph.find_edge(BUCKET, TOPIC)
    assert note.kind == EdgeKind.PUBLISH and "OBJECT_FINALIZE" in note.evidence
    # negatives: the trigger nodes carry no edges; an audit-log trigger has no source node
    triggers = (TRIGGER, TRIGGER_GCS, TRIGGER_AUDIT)
    assert not [x for x in graph.edges if x.src in triggers or x.dst in triggers]
    assert [x for x in graph.edges if x.dst == RUN] == [e]


# ------------------------------------------------------------------ gcp_pubsub_push

def test_pubsub_push_charges_delivery_once_and_grades_the_endpoint():
    sub_literal = "google_pubsub_subscription.literal"
    graph = _infer([
        _r(TOPIC, {"name": "orders"}), _r(DLQ, {"name": "dead"}),
        _r(RUN, {"name": "api"}), _r(RUN2, {"name": "worker"}),
        _r(BQ, {"table_id": "events", "dataset_id": "d"}),
        _r(SUB, {"name": "orders", "topic": ref(TOPIC, "id"),
                 "push_config": [{"push_endpoint": ref(RUN, "uri") + "/push"}],
                 "dead_letter_policy": [{"dead_letter_topic": ref(DLQ, "id")}]}),
        _r(sub_literal, {"name": "literal", "topic": ref(TOPIC, "id"),
                         "push_config": [{"push_endpoint": "https://worker-abc123xyz-uc.a.run.app/events"}],
                         "bigquery_config": [{"table": ref(BQ, "id")}]}),
    ], "gcp_pubsub_push")
    fan = graph.find_edge(TOPIC, SUB)
    assert fan.kind == EdgeKind.PUBLISH and fan.rule == "gcp_pubsub_push" and "subscribes to" in fan.evidence
    push = graph.find_edge(SUB, RUN)
    assert push.kind == EdgeKind.CONSUME and push.confidence == Confidence.HIGH and "placeholder" in push.evidence
    literal = graph.find_edge(sub_literal, RUN2)
    assert literal.kind == EdgeKind.CONSUME and literal.confidence == Confidence.LOW
    assert "literal run.app URL" in literal.evidence and "'worker'" in literal.evidence
    assert graph.find_edge(sub_literal, BQ).kind == EdgeKind.WRITE
    # negatives: never topic —CONSUME→ subscription, never topic → consumer directly, no dead-letter hop
    assert EdgeKind.CONSUME not in fan.ops and graph.find_edge(TOPIC, RUN) is None
    assert graph.find_edge(SUB, DLQ) is None and graph.find_edge(TOPIC, DLQ) is None


# ------------------------------------------------------------------ gcp_workflows

WORKFLOW_YAML = '''<<-EOT
    main:
      params: [input]
      steps:
        - init:
            assign:
              - project: $${sys.get_env("GOOGLE_CLOUD_PROJECT_ID")}
        - fan_out:
            parallel:
              branches:
                - callApi:
                    steps:
                      - api:
                          call: http.post
                          args:
                            url: ${google_cloud_run_v2_service.api.uri}/orders
                            body: $${input}
                          result: r
                - callWorker:
                    steps:
                      - worker:
                          call: http.get
                          args:
                            url: https://worker-abc123xyz-uc.a.run.app/health
        - pause:
            call: sys.sleep
            args:
              seconds: 2
        - decide:
            switch:
              - condition: $${r.code == 200}
                next: notify
              - condition: true
                next: end
        - notify:
            call: googleapis.pubsub.v1.projects.topics.publish
            args:
              topic: ${google_pubsub_topic.orders.id}
              body: {}
        - external:
            call: http.get
            args:
              url: https://api.stripe.com/v1/charges
        - done:
            return: $${r}
  EOT'''


def test_workflows_yaml_becomes_edges_and_a_replayable_workflow():
    graph = _infer([
        _r(RUN, {"name": "api"}), _r(RUN2, {"name": "worker"}), _r(TOPIC, {"name": "orders"}),
        _r(WF, {"name": "pipeline", "region": "us-central1", "source_contents": WORKFLOW_YAML}),
    ], "gcp_workflows")
    api = graph.find_edge(WF, RUN)
    assert api.kind == EdgeKind.INVOKE and api.confidence == Confidence.HIGH and api.rule == "gcp_workflows"
    assert "step 'api' (call http.post)" in api.evidence and "placeholder" in api.evidence
    worker = graph.find_edge(WF, RUN2)
    assert worker.kind == EdgeKind.INVOKE and worker.confidence == Confidence.LOW
    assert "literal run.app" in worker.evidence
    pub = graph.find_edge(WF, TOPIC)
    assert pub.kind == EdgeKind.PUBLISH and "googleapis.pubsub.v1.projects.topics.publish" in pub.evidence
    assert len([e for e in graph.edges if e.src == WF]) == 3                # the Stripe URL matches nothing
    assert graph.warnings == []
    flow = graph.nodes[WF].attrs["workflow"]
    assert [s["type"] for s in flow] == ["Parallel", "Wait", "Choice"]
    branches = flow[0]["branches"]
    assert [[t["target"] for t in b] for b in branches] == [[RUN], [RUN2]]
    assert flow[1]["seconds"] == 2.0
    notify = flow[2]["branches"]["notify"]
    assert notify[0] == {"state": "notify", "type": "Task", "target": TOPIC, "kind": "publish",
                         "resource": "googleapis.pubsub.v1.projects.topics.publish"}
    assert notify[1]["target"] is None and notify[1]["resource"] == "https://api.stripe.com/v1/charges"
    assert flow[2]["branches"]["end"] == []


def test_workflows_unreadable_sources_warn_and_scheduler_jobs_wire_their_targets():
    other = "google_workflows_workflow.templated"
    broken = "google_workflows_workflow.broken"
    graph = _infer([
        _r(RUN, {"name": "api"}), _r(TOPIC, {"name": "orders"}),
        _r(other, {"name": "templated", "source_contents": {"__templatefile__": {"path": "wf.yaml", "vars": {}}}}),
        _r(broken, {"name": "broken", "source_contents": "main:\n  steps: [\n"}),
        _r(JOB_SCHED, {"name": "tick", "schedule": "* * * * *", "http_target": [{"uri": ref(RUN, "uri") + "/tick"}]}),
        _r("google_cloud_scheduler_job.pub", {"name": "pub", "pubsub_target": [{"topic_name": ref(TOPIC, "id")}]}),
    ], "gcp_workflows")
    assert any(w.startswith(other) and "templatefile" in w for w in graph.warnings)
    assert any(w.startswith(broken) and "invalid YAML" in w for w in graph.warnings)
    assert "workflow" not in graph.nodes[broken].attrs
    tick = graph.find_edge(JOB_SCHED, RUN)
    assert tick.kind == EdgeKind.INVOKE and tick.confidence == Confidence.HIGH and "http_target.uri" in tick.evidence
    assert graph.find_edge("google_cloud_scheduler_job.pub", TOPIC).kind == EdgeKind.PUBLISH


# ------------------------------------------------------------------ env_var (G14) and vpc_peering (G15)

def test_env_var_reads_cloud_run_containers_functions_and_gce_metadata():
    template = "google_compute_instance_template.web"
    v1 = "google_cloud_run_service.v1"
    graph = _infer([
        _r(SQL, {"name": "db"}), _r(TOPIC, {"name": "orders"}), _r(SA, {"account_id": "api-sa"}),
        _r(RUN, {"name": "api", "template": [{
            "service_account": ref(SA, "email"),
            "containers": [{"image": "x", "env": [{"name": "DB_HOST", "value": ref(SQL, "connection_name")},
                                                  {"name": "TOPIC", "value": ref(TOPIC, "name")}]}]}]}),
        _r(v1, {"name": "v1",
                "template": [{"spec": [{"containers": [{"env": [{"name": "DB", "value": ref(SQL, "name")}]}]}]}]}),
        _r(FN2, {"name": "handler", "service_config": [{"environment_variables": {"TOPIC": ref(TOPIC, "name")}}]}),
        _r(FN1, {"name": "legacy", "environment_variables": {"DB": ref(SQL, "connection_name")}}),
        _r(VM, {"name": "vm", "metadata_startup_script": "export DB=" + ref(SQL, "private_ip_address")}),
        _r(MIG, {"name": "web", "version": [{"instance_template": ref(template, "id")}]}),
        _r(template, {"name": "web", "metadata": {"startup-script": "curl " + ref(TOPIC, "name")}}),
    ], "env_var")
    db = graph.find_edge(RUN, SQL)
    assert db.kind == EdgeKind.READ and db.rule == "env_var" and db.confidence == Confidence.MEDIUM
    assert "template.containers env DB_HOST references google_sql_database_instance.db.connection_name" in db.evidence
    assert graph.find_edge(RUN, TOPIC).kind == EdgeKind.PUBLISH
    assert graph.find_edge(v1, SQL).kind == EdgeKind.READ
    fn = graph.find_edge(FN2, TOPIC)
    assert fn.kind == EdgeKind.PUBLISH and "service_config.environment_variables TOPIC" in fn.evidence
    assert "environment_variables DB" in graph.find_edge(FN1, SQL).evidence
    assert "metadata_startup_script" in graph.find_edge(VM, SQL).evidence
    mig = graph.find_edge(MIG, TOPIC)
    assert mig.kind == EdgeKind.PUBLISH and "instance_template.web metadata startup-script" in mig.evidence
    # negatives: the service account is not a node, nothing edges to it; no self edge
    assert SA not in graph.nodes and not [e for e in graph.edges if e.dst == SA]
    assert graph.find_edge(RUN, RUN) is None


def test_vpc_peering_generalises_to_google_compute_network_peering():
    graph = _infer([
        _r(VPC_A, {"name": "a"}), _r(VPC_B, {"name": "b"}), _r(RUN, {"name": "api"}),
        _r("google_compute_network_peering.ab", {"name": "ab", "network": ref(VPC_A, "self_link"),
                                                 "peer_network": ref(VPC_B, "self_link")}),
    ], "vpc_peering")
    e = graph.find_edge(VPC_A, VPC_B)
    assert e.kind == EdgeKind.PEER and e.confidence == Confidence.HIGH and e.rule == "vpc_peering"
    assert e.evidence == f"google_compute_network_peering.ab peers {VPC_A} with {VPC_B}"
    assert graph.find_edge(VPC_B, VPC_A) is None                          # one declaration, one direction
    assert not [x for x in graph.edges if x.src == RUN or x.dst == RUN]


# ------------------------------------------------------------------ end to end

def test_build_graph_on_the_gcp_fixture_runs_every_rule_without_crashing():
    target = ROOT / "tests" / "fixtures" / "gcp"
    graph, _raw = build_graph(target, load_config(target, {"provider": "gcp"}))
    assert "google_cloud_run_v2_service.api" in graph.nodes and "internet" in graph.nodes
    assert all(e.rule for e in graph.edges)
