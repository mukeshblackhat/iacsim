"""GCP resource types → neutral (kind, subtype) + placement.

The GCP twin of `aws.py`: the same three tables (`TYPE_MAP`, `IGNORED_PREFIXES`,
`KEEP_ATTRS`), the same three buckets for a type — mapped, silently ignored, or
kept as a NETWORK node with a warning so nothing vanishes — and the same
`internet` EXTERNAL node that scenarios start from. The table itself is
`docs/gcp/02-TYPE-MAP.md`; every row here is a row there.

Where this file deliberately differs from `aws.py`:

  IAM grants      matched by suffix (`google_*_iam_{member,binding,policy}`) rather
                  than enumerated — the provider has one triple per resource family,
                  so a list can never be complete.
  placement       the resource is read FIRST (`region` / `location` / `zone` /
                  `network` / `subnetwork`) and the provider block is the fallback;
                  a GCP resource usually states its own region, an AWS one inherits
                  it. `zone` (`us-central1-a`) is `Placement.az`.
  entry points    only the head of the load-balancer chain (a forwarding rule) is a
                  public entry point; the proxy / URL map / backend service / NEG
                  behind it are LB nodes but the internet cannot enter there.
  subtype names   GCP-native throughout (`cloud_run`, `gce`, `memorystore`, ...):
                  `Profile.processing_for` is keyed by the bare subtype, so reusing
                  an AWS name would silently inherit AWS numbers. NETWORK subtypes
                  (`vpc`, `subnet`, ...) are shared on purpose — they are never a hop.

The five links of an HTTP load balancer stay separate nodes (G3). The chain
head (`forwarding_rule`) charges the GFE's routing time, ALB parity; every other
link is `route: 0`, and `DistanceRule` charges no distance between two chain
nodes (`CHAIN`, declared below and merged by `behaviour_tables()`), because they
are one device.
"""

from __future__ import annotations

import re
from typing import Any, ClassVar

from iacsim.core.interfaces import NORMALISERS, Normaliser
from iacsim.core.models import (
    PHYSICAL_NAME_ATTRS,
    Confidence,
    Edge,
    EdgeKind,
    InfraGraph,
    Node,
    NodeKind,
    Placement,
    RawResource,
    RawResources,
)
from iacsim.core.refs import addresses_in

# terraform type → (kind, subtype). Both halves of every global/regional twin are
# listed (02-TYPE-MAP.md Finding 1) and all three NEG types (Finding 2).
TYPE_MAP: dict[str, tuple[NodeKind, str]] = {
    # serverless compute
    "google_cloud_run_v2_service":                  (NodeKind.COMPUTE, "cloud_run"),
    "google_cloud_run_service":                     (NodeKind.COMPUTE, "cloud_run"),          # v1 API, same product
    "google_cloud_run_v2_job":                      (NodeKind.COMPUTE, "cloud_run_job"),
    "google_cloud_run_v2_worker_pool":              (NodeKind.COMPUTE, "cloud_run_worker_pool"),
    "google_cloudfunctions2_function":              (NodeKind.COMPUTE, "cloud_functions_v2"),
    "google_cloudfunctions_function":               (NodeKind.COMPUTE, "cloud_functions"),
    "google_app_engine_standard_app_version":       (NodeKind.COMPUTE, "app_engine"),         # unverified
    "google_app_engine_flexible_app_version":       (NodeKind.COMPUTE, "app_engine"),         # unverified
    "google_dataflow_job":                          (NodeKind.COMPUTE, "dataflow"),           # unverified
    "google_document_ai_processor":                 (NodeKind.COMPUTE, "document_ai"),
    "google_vertex_ai_index_endpoint":              (NodeKind.COMPUTE, "vertex_index_endpoint"),
    # GCE and GKE
    "google_compute_instance":                      (NodeKind.COMPUTE, "gce"),
    "google_compute_instance_group_manager":        (NodeKind.COMPUTE, "gce"),     # N VMs, see _capacity_attrs
    "google_compute_region_instance_group_manager": (NodeKind.COMPUTE, "gce"),
    "google_compute_instance_group":                (NodeKind.COMPUTE, "gce"),
    "google_container_cluster":                     (NodeKind.COMPUTE, "gke_cluster"),
    "google_container_node_pool":                   (NodeKind.COMPUTE, "gke_node_pool"),
    # load-balancer chain (G3: separate nodes, priced at 0)
    "google_compute_forwarding_rule":               (NodeKind.LB, "forwarding_rule"),
    "google_compute_global_forwarding_rule":        (NodeKind.LB, "forwarding_rule"),
    "google_compute_target_http_proxy":             (NodeKind.LB, "target_proxy"),
    "google_compute_target_https_proxy":            (NodeKind.LB, "target_proxy"),
    "google_compute_target_tcp_proxy":              (NodeKind.LB, "target_proxy"),
    "google_compute_target_ssl_proxy":              (NodeKind.LB, "target_proxy"),
    "google_compute_region_target_http_proxy":      (NodeKind.LB, "target_proxy"),
    "google_compute_region_target_https_proxy":     (NodeKind.LB, "target_proxy"),
    "google_compute_region_target_tcp_proxy":       (NodeKind.LB, "target_proxy"),
    "google_compute_url_map":                       (NodeKind.LB, "url_map"),
    "google_compute_region_url_map":                (NodeKind.LB, "url_map"),
    "google_compute_backend_service":               (NodeKind.LB, "backend_service"),   # enable_cdn = Cloud CDN
    "google_compute_region_backend_service":        (NodeKind.LB, "backend_service"),
    "google_compute_backend_bucket":                (NodeKind.LB, "backend_bucket"),
    "google_compute_network_endpoint_group":        (NodeKind.LB, "neg"),   # zonal: MIG / GCE backends
    "google_compute_region_network_endpoint_group": (NodeKind.LB, "neg"),   # serverless: the Cloud Run path
    "google_compute_global_network_endpoint_group": (NodeKind.LB, "neg"),   # internet endpoints
    # gateways and CDN
    "google_firebase_hosting_site":                 (NodeKind.CDN, "firebase_hosting"),
    "google_api_gateway_api":                       (NodeKind.GATEWAY, "gcp_api_gateway"),   # AWS owns `api_gateway`
    "google_api_gateway_gateway":                   (NodeKind.GATEWAY, "gcp_api_gateway"),
    "google_endpoints_service":                     (NodeKind.GATEWAY, "cloud_endpoints"),
    # datastores
    "google_sql_database_instance":                 (NodeKind.DATASTORE, "cloud_sql"),
    "google_firestore_database":                    (NodeKind.DATASTORE, "firestore"),
    "google_redis_instance":                        (NodeKind.DATASTORE, "memorystore"),
    "google_memorystore_instance":                  (NodeKind.DATASTORE, "memorystore"),
    "google_storage_bucket":                        (NodeKind.DATASTORE, "gcs"),
    "google_vertex_ai_index":                       (NodeKind.DATASTORE, "vertex_index"),
    "google_spanner_instance":                      (NodeKind.DATASTORE, "spanner"),
    "google_spanner_database":                      (NodeKind.DATASTORE, "spanner"),
    "google_bigquery_dataset":                      (NodeKind.DATASTORE, "bigquery"),
    "google_bigquery_table":                        (NodeKind.DATASTORE, "bigquery"),
    "google_bigtable_instance":                     (NodeKind.DATASTORE, "bigtable"),
    "google_bigtable_table":                        (NodeKind.DATASTORE, "bigtable"),
    "google_memcache_instance":                     (NodeKind.DATASTORE, "memcache"),          # unverified
    "google_filestore_instance":                    (NodeKind.DATASTORE, "filestore"),
    "google_alloydb_cluster":                       (NodeKind.DATASTORE, "alloydb"),
    "google_alloydb_instance":                      (NodeKind.DATASTORE, "alloydb"),
    # messaging and eventing
    "google_pubsub_topic":                          (NodeKind.QUEUE, "pubsub"),
    "google_pubsub_subscription":                   (NodeKind.QUEUE, "pubsub_subscription"),
    "google_eventarc_trigger":                      (NodeKind.QUEUE, "eventarc"),
    "google_eventarc_message_bus":                  (NodeKind.QUEUE, "eventarc_bus"),
    "google_eventarc_pipeline":                     (NodeKind.QUEUE, "eventarc_pipeline"),
    "google_cloud_tasks_queue":                     (NodeKind.QUEUE, "cloud_tasks"),
    # orchestration
    "google_workflows_workflow":                    (NodeKind.ORCHESTRATOR, "workflows"),
    "google_composer_environment":                  (NodeKind.ORCHESTRATOR, "composer"),
    "google_cloud_scheduler_job":                   (NodeKind.ORCHESTRATOR, "cloud_scheduler"),
    # placement / plumbing — subtypes shared with AWS on purpose (never a hop)
    "google_compute_network":                       (NodeKind.NETWORK, "vpc"),
    "google_compute_subnetwork":                    (NodeKind.NETWORK, "subnet"),
    "google_compute_network_peering":               (NodeKind.NETWORK, "vpc_peering"),
    "google_compute_router_nat":                    (NodeKind.NETWORK, "nat"),
    "google_vpc_access_connector":                  (NodeKind.NETWORK, "vpc_connector"),
}

# Glue with no latency meaning; dropped silently (inference rules read them from raw).
# TYPE_MAP is consulted first, so a prefix here never shadows a mapped type that
# happens to start with it (`google_compute_network_endpoint` vs `..._group`).
IGNORED_PREFIXES = (
    # not google at all
    "random_", "null_resource", "terraform_data", "time_sleep", "time_static", "local_file",
    "archive_file", "tls_", "kubernetes_", "helm_release", "docker_", "vault_", "ko_build", "cosign_sign",
    # identity and project bookkeeping
    "google_project", "google_service_account", "google_iam_", "google_iap_", "google_apikeys_",
    "google_identity_platform_", "google_storage_hmac_key", "google_org", "google_folder",
    "google_tags_", "google_os_config_", "google_essential_contacts_", "google_billing_",
    # secrets, keys, certificates, addresses
    "google_secret_manager_", "google_kms_", "google_compute_ssl_", "google_compute_region_ssl_",
    "google_compute_managed_ssl_", "google_compute_address", "google_compute_global_address",
    # security and firewalling
    "google_compute_firewall", "google_compute_network_firewall_policy",
    "google_compute_region_network_firewall_policy", "google_compute_security_policy",
    "google_compute_region_security_policy",
    # load-balancer glue: the NEG / MIG is the node, not each endpoint or template
    "google_compute_network_endpoint", "google_compute_global_network_endpoint",
    "google_compute_region_network_endpoint", "google_compute_instance_template",
    "google_compute_region_instance_template", "google_compute_health_check",
    "google_compute_region_health_check", "google_compute_http", "google_compute_autoscaler",
    "google_compute_region_autoscaler", "google_api_gateway_api_config", "google_cloud_run_domain_mapping",
    # disks and images
    "google_compute_disk", "google_compute_region_disk", "google_compute_attached_disk",
    "google_compute_snapshot", "google_compute_image",
    # datastore children
    "google_sql_database", "google_sql_user", "google_sql_ssl_cert", "google_storage_bucket_",
    "google_storage_notification", "google_storage_default_", "google_storage_object_",
    "google_storage_transfer_", "google_storage_managed_folder", "google_datastore_index",
    "google_bigquery_routine", "google_bigquery_job", "google_bigquery_data_transfer_config",
    "google_bigtable_gc_policy", "google_vertex_ai_index_endpoint_deployed_index",
    "google_pubsub_schema", "google_eventarc_enrollment", "google_eventarc_google_api_source",
    # network glue: routing, peering config, VPNs, DNS, private services access
    "google_compute_router", "google_compute_route", "google_compute_network_peering_routes_config",
    "google_compute_shared_vpc_", "google_compute_project_", "google_compute_vpn_", "google_compute_ha_vpn_",
    "google_compute_external_vpn_gateway", "google_compute_interconnect", "google_dns_",
    "google_service_networking_connection",
    # observability, build, fleet, containers-as-storage
    "google_monitoring_", "google_logging_", "google_cloudbuild", "google_artifact_registry_",
    "google_container_registry", "google_gke_hub_", "google_firebase_project", "google_firebase_web_app",
    "google_firebase_hosting_",
)

# Every resource family has `*_iam_member` / `_binding` / `_policy` siblings; matched
# by suffix so a grant type nobody listed never becomes an unknown-type warning.
IAM_GRANT = re.compile(r"^google_.*_iam_(member|binding|policy|audit_config)$")

# Attributes worth keeping on the node — scalars only; nested facts are flattened
# by _capacity_attrs so a Cloud Run container spec never lands in graph.json.
KEEP_ATTRS = ("region", "zone", "location", "network", "subnetwork",
              "machine_type", "database_version", "tier", "memory_size_gb", "size_gb", "storage_class",
              "node_count", "initial_node_count", "target_size", "max_instances", "min_instances",
              "enable_cdn", "load_balancing_scheme", "protocol", "port_range", "ip_protocol",
              "runtime", "entry_point", "ingress", "timeout", "type")

# Types whose `zone` defaults from the provider block when unset. Only these read
# the loader's `_provider_zone` stash — a regional Cloud SQL instance has no zone.
ZONAL_TYPES = frozenset({
    "google_compute_instance", "google_compute_instance_group_manager",
    "google_compute_instance_group", "google_compute_network_endpoint_group",
})

# The links of an HTTP(S) load balancer, in order. Every one prices `route: 0`
# (G9); the walker still charges *distance* between them because
# `latency/rules/distance.py` knows nothing about chains — that residual is
# same_region_unknown_az × 2 per internal hop until the distance rule learns this set.
CHAIN = frozenset({"forwarding_rule", "target_proxy", "url_map", "backend_service", "backend_bucket", "neg"})

# Public entry points: gateways, CDNs, and the *head* of an LB chain. A URL map
# or backend service is an LB node the internet cannot reach directly.
ENTRY_KINDS = (NodeKind.GATEWAY, NodeKind.CDN)
ENTRY_LB_SUBTYPES = frozenset({"forwarding_rule"})
INTERNET = "internet"

REGION_RE = re.compile(r"^[a-z]+-[a-z]+\d+$")          # us-central1, europe-west1
ZONE_RE = re.compile(r"^[a-z]+-[a-z]+\d+-[a-z]$")      # us-central1-a


@NORMALISERS.register("gcp")
class GcpNormaliser(Normaliser):
    # What a call *into* each subtype costs — the key inside its `processing`
    # block charged on an INVOKE hop and the fallback for any unpriced edge kind.
    # Every non-NETWORK subtype TYPE_MAP can produce is here (tests/test_latency_rules.py).
    INVOKE_KEYS: ClassVar[dict[str, str]] = {
        "cloud_run": "warm", "cloud_run_job": "start", "cloud_run_worker_pool": "handle",
        "cloud_functions_v2": "warm", "cloud_functions": "warm", "app_engine": "warm",
        "dataflow": "handle", "document_ai": "process", "vertex_index_endpoint": "query",
        "gce": "handle", "gke_cluster": "handle", "gke_node_pool": "handle",
        "forwarding_rule": "route", "target_proxy": "route", "url_map": "route",
        "backend_service": "route", "backend_bucket": "route", "neg": "route",
        "firebase_hosting": "miss", "gcp_api_gateway": "route", "cloud_endpoints": "route",
        "cloud_sql": "read", "firestore": "read", "memorystore": "read", "gcs": "read",
        "vertex_index": "read", "spanner": "read", "bigquery": "read", "bigtable": "read",
        "memcache": "read", "filestore": "read", "alloydb": "read",
        "pubsub": "publish", "pubsub_subscription": "publish", "eventarc": "publish",
        "eventarc_bus": "publish", "eventarc_pipeline": "publish", "cloud_tasks": "publish",
        "workflows": "transition", "composer": "transition", "cloud_scheduler": "transition",
    }
    # Scale-to-zero compute that pays a cold start; their blocks carry `cold` / `cold_prob`.
    COLD_START: ClassVar[frozenset[str]] = frozenset({
        "cloud_run", "cloud_functions", "cloud_functions_v2", "app_engine",
    })
    # The load-balancer chain: no wire between two of these, so no distance (G9).
    CHAIN: ClassVar[frozenset[str]] = CHAIN

    def normalise(self, raw: RawResources) -> InfraGraph:
        graph = InfraGraph()
        by_address = {r.address: r for r in raw.resources}

        for r in raw.resources:
            if r.type in TYPE_MAP:
                kind, subtype = TYPE_MAP[r.type]
            elif r.type.startswith(IGNORED_PREFIXES) or IAM_GRANT.match(r.type):
                continue
            else:
                kind, subtype = NodeKind.NETWORK, r.type
                graph.warnings.append(f"{r.address}: unknown type {r.type}; kept as a network node")
            attrs = {k: r.attrs[k] for k in KEEP_ATTRS if k in r.attrs and r.attrs[k] is not None}
            attrs.update(_capacity_attrs(r.type, subtype, r.attrs))
            graph.add_node(Node(
                id=r.address, kind=kind, subtype=subtype,
                placement=_placement(r, by_address),
                attrs=attrs,
                label=_label(r),
            ))

        self._add_internet(graph)
        return graph

    @staticmethod
    def _add_internet(graph: InfraGraph) -> None:
        graph.add_node(Node(id=INTERNET, kind=NodeKind.EXTERNAL, subtype="internet", label="internet"))
        for node in list(graph.nodes.values()):
            if node.kind in ENTRY_KINDS or (node.kind == NodeKind.LB and node.subtype in ENTRY_LB_SUBTYPES):
                graph.add_edge(Edge(INTERNET, node.id, EdgeKind.INVOKE, Confidence.HIGH,
                                    f"{node.subtype} {node.label or node.id} is a public entry point",
                                    rule="normaliser"))


# ------------------------------------------------------------------ capacity (G6, deferred)

def _capacity_attrs(rtype: str, subtype: str, attrs: dict[str, Any]) -> dict[str, Any]:
    """Flat capacity facts lifted out of nested blocks, in the neutral keys the
    `load` walker reads for AWS (`instances`, `concurrency`) plus what a later
    GCP capacity model will need (`max_instances`, `cpu`, `memory`, `tier`).
    Nothing reads them this round; they are kept so stored graphs need no re-run."""
    out: dict[str, Any] = {}
    if subtype == "cloud_run":
        template = _block(attrs.get("template"))
        if rtype == "google_cloud_run_service":                     # v1: knative annotations
            notes = _block(_block(template.get("metadata")).get("annotations"))
            _put_int(out, "max_instances", notes.get("autoscaling.knative.dev/maxScale"))
            _put_int(out, "min_instances", notes.get("autoscaling.knative.dev/minScale"))
            _put_int(out, "concurrency", _block(template.get("spec")).get("container_concurrency"))
        else:
            scaling = _block(template.get("scaling"))
            _put_int(out, "max_instances", scaling.get("max_instance_count"))
            _put_int(out, "min_instances", scaling.get("min_instance_count"))
            _put_int(out, "concurrency", template.get("max_instance_request_concurrency"))
            limits = _block(_block(_block(template.get("containers")).get("resources")).get("limits"))
            for key in ("cpu", "memory"):
                if isinstance(limits.get(key), str):
                    out[key] = limits[key]
    elif subtype == "cloud_functions_v2":
        service, build = _block(attrs.get("service_config")), _block(attrs.get("build_config"))
        if isinstance(service.get("available_memory"), str):
            out["memory"] = service["available_memory"]
        _put_int(out, "timeout", service.get("timeout_seconds"))
        _put_int(out, "max_instances", service.get("max_instance_count"))
        _put_int(out, "min_instances", service.get("min_instance_count"))
        if isinstance(build.get("runtime"), str):
            out["runtime"] = build["runtime"]
    elif subtype == "cloud_functions":
        _put_int(out, "memory", attrs.get("available_memory_mb"))
    elif subtype == "cloud_sql":
        tier = _block(attrs.get("settings")).get("tier")
        if isinstance(tier, str):
            out["tier"] = tier
    elif subtype == "gce":
        # a single VM is one server; a (regional) MIG is target_size of them
        target = attrs.get("target_size")
        out["instances"] = target if isinstance(target, int) and target > 0 else 1
        template = _first_address(_block(attrs.get("version")).get("instance_template"))
        if template:
            out["instance_template"] = template
    elif subtype == "gke_node_pool":
        autoscaling = _block(attrs.get("autoscaling"))
        _put_int(out, "min_nodes", autoscaling.get("min_node_count"))
        _put_int(out, "max_nodes", autoscaling.get("max_node_count"))
        machine = _block(attrs.get("node_config")).get("machine_type")
        if isinstance(machine, str):
            out["machine_type"] = machine
    return out


def _block(value: Any) -> dict[str, Any]:
    """A nested HCL block as one dict: the loader emits `template { }` as a
    one-element list; a plain map (annotations, limits) is returned as-is."""
    if isinstance(value, list) and value and isinstance(value[0], dict):
        return value[0]
    return value if isinstance(value, dict) else {}


def _put_int(out: dict[str, Any], key: str, value: Any) -> None:
    """Keep an integer fact, tolerating the string form knative annotations use."""
    if isinstance(value, bool):
        return
    if isinstance(value, int) or (isinstance(value, str) and value.isdigit()):
        out[key] = int(value)


# ------------------------------------------------------------------ placement

def _placement(r: RawResource, by_address: dict[str, RawResource]) -> Placement:
    """Resource first, provider second (G10). `location` counts only when it is
    region- or zone-shaped: a multi-region (`US`, `nam5`) is left to the provider
    fallback rather than written into a field the distance rule compares."""
    location = _string(r.attrs.get("location"))
    az = _string(r.attrs.get("zone"))
    if az is None and location and ZONE_RE.match(location):
        az = location
    if az is None and r.type in ZONAL_TYPES:
        az = _string(r.attrs.get("_provider_zone"))

    region = _string(r.attrs.get("region"))
    if region is None and location and REGION_RE.match(location):
        region = location
    if region is None and az:
        region = az.rsplit("-", 1)[0]                          # us-central1-a → us-central1
    if region is None:
        region = r.region

    vpc, subnet = _network_refs(r.attrs)
    if subnet and subnet in by_address:
        vpc = vpc or _first_address(by_address[subnet].attrs.get("network"))
    return Placement(region=region, az=az, vpc=vpc, subnet=subnet)


def _network_refs(attrs: dict[str, Any]) -> tuple[str | None, str | None]:
    """(vpc, subnet) addresses from wherever the resource names its network:
    top-level (`subnetwork`, NEG, connector), `network_interface` (GCE),
    `template.vpc_access.network_interfaces` (Cloud Run v2), or
    `settings.ip_configuration.private_network` (Cloud SQL)."""
    candidates = [
        attrs,
        _block(attrs.get("network_interface")),
        _block(_block(_block(attrs.get("template")).get("vpc_access")).get("network_interfaces")),
    ]
    vpc = subnet = None
    for block in candidates:
        vpc = vpc or _first_address(block.get("network"))
        subnet = subnet or _first_address(block.get("subnetwork"))
    vpc = vpc or _first_address(_block(_block(attrs.get("settings")).get("ip_configuration")).get("private_network"))
    return vpc, subnet


def _first_address(value: Any) -> str | None:
    found = addresses_in(value)
    return found[0] if found else None


def _string(value: Any) -> str | None:
    return value if isinstance(value, str) and "${" not in value else None


def _label(r: RawResource) -> str | None:
    for key in PHYSICAL_NAME_ATTRS:
        if isinstance(r.attrs.get(key), str) and "${" not in r.attrs[key]:
            return r.attrs[key]
    return None
