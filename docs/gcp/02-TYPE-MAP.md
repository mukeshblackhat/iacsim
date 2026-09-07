# GCP — type map

The evidence-backed table that becomes `iacsim/graph/normalisers/gcp.py`, the
GCP twin of `iacsim/graph/normalisers/aws.py`. It has the same three parts —
`TYPE_MAP`, `IGNORED_PREFIXES`, `KEEP_ATTRS` — and falls into the same three
buckets for a type it does not know (`aws.py:111-118`): mapped, silently
ignored, or kept as a `NETWORK` node with a warning so nothing vanishes (D20).

Every row was checked against the enums at `iacsim/core/models.py:48-58`
(`NodeKind`) and `iacsim/core/models.py:61-68` (`EdgeKind`). Subtype names are
GCP-native and never reuse an AWS name — see `01-DECISIONS.md` G11 for why that
is a correctness rule, not a style one.

**Before reading the table, read the two findings below.** A map that gets them
wrong loses half of a real fixture without printing anything.

---

## Finding 1 — global and regional twins are different type names

Five families have separate Terraform types for the global and the regional
variant of the same thing:

| global | regional |
|---|---|
| `google_compute_url_map` | `google_compute_region_url_map` |
| `google_compute_target_http_proxy` / `_target_https_proxy` | `google_compute_region_target_http_proxy` / `_region_target_https_proxy` |
| `google_compute_backend_service` | `google_compute_region_backend_service` |
| `google_compute_health_check` | `google_compute_region_health_check` |
| `google_compute_ssl_certificate` | `google_compute_region_ssl_certificate` |

A map that knows only the global half **silently drops half of every regional
load-balancer fixture** — the nodes fall through to the `NETWORK` + warning
bucket at best, and `gcp_lb_chain` then joins nothing, so the request path ends
at the forwarding rule. Both spellings map to the same subtype; only
`Placement` differs (a regional resource carries `region`, a global one does
not).

Seen in: `docs:lb-regional` uses the `region_*` spellings throughout;
`docs:lb-mig` uses the global ones.

## Finding 2 — there are three NEG types, not one

| type | what it fronts | why it matters |
|---|---|---|
| `google_compute_network_endpoint_group` | zonal, MIG / GCE backends | the classic path |
| `google_compute_region_network_endpoint_group` | **serverless: Cloud Run, Cloud Functions, App Engine** | **the important one** — this is the only link between an HTTP load balancer and a Cloud Run service |
| `google_compute_global_network_endpoint_group` | internet endpoints (external backends) | rare; the backend is off-platform |

Miss `region_network_endpoint_group` and every "load balancer in front of Cloud
Run" stack — the most common serverless GCP shape there is — has no edge from
the LB to the service. Seen in: `docs:run-multi`, `docs:lb-regional`.

---

## The table

Columns: `google_* type | NodeKind | subtype | seen in | notes`.

"seen in" convention:
- a **named fixture** (`skills`, `docs:run-multi`, …) — attribution is certain, see Sources at the bottom
- **corpus** — verified present in the 10-fixture corpus; the exact fixture is not recorded here
- **unverified** — added from knowledge under G2 (broad coverage), not seen in the corpus. Never silently invented: every such row says so.

### Serverless compute

| google_* type | NodeKind | subtype | seen in | notes |
|---|---|---|---|---|
| `google_cloud_run_v2_service` | COMPUTE | `cloud_run` | skills, docs:run-multi, docs:run-sql | the flagship GCP compute node; cold start applies (G20) |
| `google_cloud_run_v2_job` | COMPUTE | `cloud_run_job` | docs:wf-run-job | not on a request path unless a Workflow or Scheduler invokes it |
| `google_cloud_run_v2_worker_pool` | COMPUTE | `cloud_run_worker_pool` | `05-EXAMPLES.md` (webstatus.dev) | **silent-drop risk** — carries a whole ingestion tier in real code; omit it and that tier vanishes from the graph |
| `google_cloudfunctions2_function` | COMPUTE | `cloud_functions_v2` | docs:fn-pubsub, corpus | env vars at `service_config.environment_variables` (G14) |
| `google_cloudfunctions_function` | COMPUTE | `cloud_functions` | corpus | v1; env vars at `environment_variables` |
| `google_app_engine_standard_app_version` | COMPUTE | `app_engine` | **unverified** | |
| `google_app_engine_flexible_app_version` | COMPUTE | `app_engine` | **unverified** | |
| `google_dataflow_job` | COMPUTE | `dataflow` | **unverified** | batch/stream; rarely on a synchronous path |
| `google_document_ai_processor` | COMPUTE | `document_ai` | corpus | a called managed API; COMPUTE is the closest kind — INVOKE by default (`iacsim/core/models.py:73-78`) |
| `google_vertex_ai_index_endpoint` | COMPUTE | `vertex_index_endpoint` | corpus | the queryable endpoint |

### GCE and GKE

| google_* type | NodeKind | subtype | seen in | notes |
|---|---|---|---|---|
| `google_compute_instance` | COMPUTE | `gce` | docs:lb-mig, corpus | one VM = one node, mirroring `aws_instance` (`aws.py:44`) |
| `google_compute_instance_group_manager` | COMPUTE | `gce` | docs:lb-mig | a MIG is N VMs; `target_size` is the capacity fact (G6 defers using it) |
| `google_compute_region_instance_group_manager` | COMPUTE | `gce` | corpus | regional twin of the above |
| `google_compute_instance_group` | COMPUTE | `gce` | corpus | unmanaged group; still a backend target |
| `google_container_cluster` | COMPUTE | `gke_cluster` | docs:gke-mt | |
| `google_container_node_pool` | COMPUTE | `gke_node_pool` | docs:gke-mt | carries `node_count` / `machine_type`; two nodes per cluster is noisy but honest — the pool is where capacity lives |

### Load balancer chain

All five links stay separate nodes (G3). Every internal hop is priced at 0 ms —
see "The 0 ms chain" below.

| google_* type | NodeKind | subtype | seen in | notes |
|---|---|---|---|---|
| `google_compute_forwarding_rule` | LB | `forwarding_rule` | docs:lb-regional | the chain's first link; regional |
| `google_compute_global_forwarding_rule` | LB | `forwarding_rule` | docs:lb-mig, docs:run-multi | global twin |
| `google_compute_target_http_proxy` | LB | `target_proxy` | docs:lb-mig | |
| `google_compute_target_https_proxy` | LB | `target_proxy` | corpus | |
| `google_compute_region_target_http_proxy` | LB | `target_proxy` | docs:lb-regional | Finding 1 |
| `google_compute_region_target_https_proxy` | LB | `target_proxy` | corpus | Finding 1 |
| `google_compute_url_map` | LB | `url_map` | docs:lb-mig, docs:run-multi | |
| `google_compute_region_url_map` | LB | `url_map` | docs:lb-regional | Finding 1 |
| `google_compute_backend_service` | LB | `backend_service` | docs:lb-mig, docs:run-multi | `enable_cdn = true` here is Cloud CDN — an attribute, not a resource (see below) |
| `google_compute_region_backend_service` | LB | `backend_service` | docs:lb-regional | Finding 1 |
| `google_compute_backend_bucket` | LB | `backend_bucket` | corpus | routes a URL path straight to a GCS bucket |
| `google_compute_network_endpoint_group` | LB | `neg` | docs:lb-mig | zonal, MIG backends — Finding 2 |
| `google_compute_region_network_endpoint_group` | LB | `neg` | docs:run-multi, docs:lb-regional | **serverless NEG — the Cloud Run path**, Finding 2 |
| `google_compute_global_network_endpoint_group` | LB | `neg` | corpus | internet endpoints — Finding 2 |

### Gateways and CDN

| google_* type | NodeKind | subtype | seen in | notes |
|---|---|---|---|---|
| `google_firebase_hosting_site` | CDN | `firebase_hosting` | corpus | a real edge cache and a public entry point |
| `google_api_gateway_api` | GATEWAY | `gcp_api_gateway` | **unverified** | prefixed on purpose: GCP's product is also called API Gateway and the bare name collides with the AWS `api_gateway` profile block (G11) |
| `google_api_gateway_gateway` | GATEWAY | `gcp_api_gateway` | **unverified** | the deployed instance |

### Datastores

| google_* type | NodeKind | subtype | seen in | notes |
|---|---|---|---|---|
| `google_sql_database_instance` | DATASTORE | `cloud_sql` | skills, docs:run-sql | the instance is the node; the database and user are glue |
| `google_firestore_database` | DATASTORE | `firestore` | skills, chat-bot | |
| `google_redis_instance` | DATASTORE | `memorystore` | corpus | **not** `elasticache` — G11 |
| `google_memorystore_instance` | DATASTORE | `memorystore` | `05-EXAMPLES.md` | the successor API to `google_redis_instance`; same subtype, so both price identically. Miss it and new code silently loses its cache hop |
| `google_storage_bucket` | DATASTORE | `gcs` | skills, corpus | |
| `google_vertex_ai_index` | DATASTORE | `vertex_index` | corpus | queried through `vertex_index_endpoint` |
| `google_spanner_instance` | DATASTORE | `spanner` | **unverified** | |
| `google_spanner_database` | DATASTORE | `spanner` | **unverified** | |
| `google_bigquery_dataset` | DATASTORE | `bigquery` | **unverified** | |
| `google_bigquery_table` | DATASTORE | `bigquery` | **unverified** | |
| `google_bigtable_instance` | DATASTORE | `bigtable` | **unverified** | |
| `google_bigtable_table` | DATASTORE | `bigtable` | **unverified** | |
| `google_memcache_instance` | DATASTORE | `memcache` | **unverified** | |
| `google_filestore_instance` | DATASTORE | `filestore` | **unverified** | |
| `google_alloydb_cluster` | DATASTORE | `alloydb` | **unverified** | |
| `google_alloydb_instance` | DATASTORE | `alloydb` | **unverified** | |

### Messaging and eventing

| google_* type | NodeKind | subtype | seen in | notes |
|---|---|---|---|---|
| `google_pubsub_topic` | QUEUE | `pubsub` | skills, docs:fn-pubsub | |
| `google_pubsub_subscription` | QUEUE | `pubsub_subscription` | docs:fn-pubsub, corpus | `push_config.push_endpoint` is what `gcp_pubsub_push` reads |
| `google_eventarc_trigger` | QUEUE | `eventarc` | docs:eventarc-adv, corpus | kept as a node, not collapsed: Eventarc delivery is real latency between source and destination |
| `google_eventarc_message_bus` | QUEUE | `eventarc_bus` | docs:eventarc-adv | |
| `google_eventarc_pipeline` | QUEUE | `eventarc_pipeline` | docs:eventarc-adv | |
| `google_cloud_tasks_queue` | QUEUE | `cloud_tasks` | **unverified** | |

### Orchestration

| google_* type | NodeKind | subtype | seen in | notes |
|---|---|---|---|---|
| `google_workflows_workflow` | ORCHESTRATOR | `workflows` | docs:wf-run-job | `source_contents` is YAML naming services by URL — what `gcp_workflows` parses |
| `google_composer_environment` | ORCHESTRATOR | `composer` | corpus | |
| `google_cloud_scheduler_job` | ORCHESTRATOR | `cloud_scheduler` | corpus | **open question for round 2:** entry points come only from the `internet` node's edges (`aws.py:101`, `inferred.py:69`), and a scheduler is not a public entry point — so a cron-triggered path infers no scenario today and the user must declare it in `scenarios.yaml` |

### Network (placement, never a hop)

`NodeKind.NETWORK` is in `NOT_A_HOP` (`iacsim/scenarios/inferred.py:41`) and has
no `processing` block, so these subtypes are shared with AWS by design — the
one documented exception to G11.

| google_* type | NodeKind | subtype | seen in | notes |
|---|---|---|---|---|
| `google_compute_network` | NETWORK | `vpc` | skills, corpus | `Placement.vpc` |
| `google_compute_subnetwork` | NETWORK | `subnet` | skills, corpus | `Placement.subnet` |
| `google_compute_network_peering` | NETWORK | `vpc_peering` | **unverified** | `{network, peer_network}` — the pair `gcp_vpc_peering` joins (G15) |
| `google_compute_router_nat` | NETWORK | `nat` | **unverified** | |
| `google_vpc_access_connector` | NETWORK | `vpc_connector` | **unverified** | Cloud Run / Functions egress into a VPC |

---

## The ignore list (`IGNORED_PREFIXES` twin)

Dropped from the graph silently. **Dropped from the graph is not dropped from
the run**: inference rules read `RawResources` directly, so an ignored type can
still be the evidence that draws an edge (`aws.py:6-9`). The column below says
which.

| prefix / type | still read from raw by | why it is not a node |
|---|---|---|
| `google_project_iam_member`, `google_project_iam_binding` | `gcp_iam_binding` | a grant, not a thing a request enters |
| `google_service_account`, `google_service_account_iam_member` | `gcp_iam_binding` | the principal identity a binding names |
| `google_cloud_run_v2_service_iam_member`, `google_cloud_run_service_iam_member`, `google_cloud_run_service_iam_binding`, `google_cloud_run_service_iam_policy` | `gcp_iam_binding` | resource-scoped grants onto a Cloud Run service |
| `google_storage_bucket_iam_member` | `gcp_iam_binding` | |
| `google_pubsub_topic_iam_binding` | `gcp_iam_binding` | |
| `google_secret_manager_secret_iam_member`, `google_secret_manager_secret_iam_binding` | `gcp_iam_binding` | |
| `google_storage_notification` | `gcp_eventarc` | bucket → topic wiring; the AWS analogue is `aws_s3_bucket_notification` |
| `google_eventarc_enrollment`, `google_eventarc_google_api_source` | `gcp_eventarc` | bus ↔ pipeline routing glue |
| `google_compute_network_endpoint`, `google_compute_global_network_endpoint` | `gcp_lb_chain` | a single endpoint registration; the NEG is the node |
| `google_compute_instance_template` | `gcp_lb_chain` (via the MIG) | glue, like `aws_launch_template` (`aws.py:92`) |
| `google_sql_database`, `google_sql_user` | — | children of the instance |
| `google_storage_bucket_object` | — | mirrors `aws_s3_object` (`aws.py:76`) |
| `google_secret_manager_secret`, `google_secret_manager_secret_version` | — | mirrors `aws_secretsmanager_` (`aws.py:76`) |
| `google_kms_key_ring`, `google_kms_crypto_key`, `google_kms_crypto_key_iam_member` | — | mirrors `aws_kms_` (`aws.py:76`). `03-TESTING.md` §3.2 asserts these are ignored, so the row has to exist here too |
| `google_compute_firewall`, `google_compute_network_firewall_policy`, `_association`, `_rule`, `google_compute_security_policy` | — | mirrors `aws_security_group` / `aws_wafv2_` (`aws.py:77, 82`) |
| `google_compute_health_check`, `google_compute_region_health_check` | — | not on the request path |
| `google_compute_ssl_certificate`, `google_compute_region_ssl_certificate`, `google_compute_managed_ssl_certificate` | — | mirrors `aws_acm_` (`aws.py:77`) |
| `google_compute_address`, `google_compute_global_address` | — | mirrors `aws_eip` (`aws.py:77`) |
| `google_dns_managed_zone`, `google_dns_record_set` | — | name resolution, not a hop |
| `google_monitoring_alert_policy` | — | mirrors `aws_cloudwatch_` (`aws.py:89`) |
| `google_cloudbuild_trigger` | — | CI, not runtime |
| `google_artifact_registry_repository` | — | image storage, not a request hop |
| `google_project_service`, `google_project_service_identity` | — | API enablement; pure bookkeeping and very common — omit it and a real repo warns dozens of times |
| `google_service_networking_connection` | — | private services access; changes reachability, not the request shape (parked, `01-DECISIONS.md` §6) |
| `google_gke_hub_feature`, `google_gke_hub_membership_binding`, `google_gke_hub_namespace`, `google_gke_hub_scope`, `google_gke_hub_scope_rbac_role_binding` | — | fleet management; no data path |
| `google_firebase_project` | — | the project container, not the hosting site |
| `random_`, `tls_`, `time_sleep` | — | already covered by the AWS list (`aws.py:75-76`) — but `IGNORED_PREFIXES` lives per normaliser, so the GCP file needs its own copy |
| `kubernetes_config_map` | — | a different provider entirely; appears in the corpus alongside `google_container_cluster` |

### Match IAM grants by suffix, not by enumeration

Six rows above enumerate individual `*_iam_member` / `*_iam_binding` /
`*_iam_policy` types. That list cannot be kept complete: the wider corpus in
`05-EXAMPLES.md` turns up **15 more** across 134 distinct types, and GCP adds one
per service. Every resource type in the provider has these three siblings.

So the GCP normaliser should match them with a **pattern**,
`^google_.*_iam_(member|binding|policy)$`, rather than a list — the one place the
GCP file should diverge from `aws.py`'s pure-prefix `IGNORED_PREFIXES` tuple.
`gcp_iam_binding` reads them all from raw regardless. An enumeration here means a
grant type nobody listed becomes an unknown-type warning and, worse, its edge is
never drawn.

### Reconciling with the wider corpus

The **unverified** marks above were written against the seven fixtures in
`03-TESTING.md`. `05-EXAMPLES.md` §7 surveys 14 third-party repos and **134**
distinct `google_*` types, and promotes a number of these rows from unverified to
seen-in-real-code. Treat `05-EXAMPLES.md` §7 as the authority on which, and
update the `seen in` column from it rather than re-researching.

---

## Placement rules

`_placement()` reads the **resource first, the provider second** — the reverse
emphasis from `aws.py:169-188`, and the reason is `01-DECISIONS.md` G10.

| GCP attribute | `Placement` field | example |
|---|---|---|
| `region` | `.region` | `us-central1` |
| `location` | `.region` | Cloud Run v2, Firestore, Composer, GKE all use `location` |
| `zone` | `.az` | `us-central1-a` — the existing `az` field, no IR change |
| `network` | `.vpc` | `${google_compute_network.default.id}` → the address |
| `subnetwork` | `.subnet` | |
| the provider block's region | `.region`, fallback only | requires the G7 loader fix, or every field above is the only source and a global resource has none |

`location` can hold a multi-region (`US`, `EU`) or a dual-region for GCS. Those
are not region names the `cross_region` map knows (`defaults.yaml:16-21`), so a
`location` that is not of the form `<continent>-<direction><digit>` is left as
`None` rather than written into `Placement.region` — an unknown region already
warns once per node (`distance.py:46-52`), which is the honest outcome.

---

## `KEEP_ATTRS`

The AWS list (`aws.py:96-99`) is scalars only, read straight off `r.attrs`.
Keep that property: GCP hides most of the interesting facts in nested blocks,
and copying a Cloud Run `template` wholesale would put the entire container spec
into `graph.json`.

Scalars, direct:

```
region  zone  location  network  subnetwork
machine_type  database_version  tier  memory_size_gb  size_gb  storage_class
node_count  initial_node_count  target_size
enable_cdn  load_balancing_scheme  protocol  port_range  ip_protocol
runtime  entry_point  ingress  timeout  type
```

Nested, derived by a `_capacity_attrs`-style helper (`aws.py:143-164` is the
pattern) so the node keeps a flat scalar:

| source | derived key |
|---|---|
| `template.scaling.max_instance_count` / `min_instance_count` (Cloud Run v2) | `max_instances` / `min_instances` |
| `template.max_instance_request_concurrency` (Cloud Run v2) | `concurrency` |
| `template.containers[].resources.limits.{cpu,memory}` | `cpu`, `memory` |
| `service_config.{available_memory,timeout_seconds,max_instance_count}` (Functions v2) | `memory`, `timeout`, `max_instances` |
| `settings.tier` (Cloud SQL) | `tier` |
| `version[].instance_template` (MIG) | the template address |

Nothing here is read this round — `01-DECISIONS.md` G6 defers capacity — but
the attributes must be on the node before the load walker can ever use them, and
adding them later means re-normalising every stored `graph.json`.

---

## The 0 ms chain

Per G3 the LB chain stays five nodes, and per G9 that costs nothing only if
**both** halves of a hop's price are suppressed.

Subtypes on the chain: `forwarding_rule`, `target_proxy`, `url_map`,
`backend_service`, `backend_bucket`, `neg`.

1. **Processing.** Each gets `processing.<subtype>.defaults = { route: 0 }` in
   the profile, and an `INVOKE_KEYS` entry mapping the subtype to `route`. Both
   are required — a missing `INVOKE_KEYS` entry is the G8 free-hop trap, and a
   missing profile block is the same trap by the other door.
2. **Distance.** The hop out of a chain node is a ROUTE, which is in
   `SYNCHRONOUS` (`traversal.py:75`), so the walker doubles its distance
   (`traversal.py:283`). Every chain node is regional or global, so `az` is
   `None` and the distance rule returns `same_region_unknown_az` — 0.5 ms,
   doubled — for each of the four internal hops. That is ~4 ms of network that
   does not exist.

Only the **internal** chain hops are free. The hop *into* the chain
(`internet` → forwarding rule) keeps `internet_to_edge`, and the hop *out* of
the chain (NEG → Cloud Run) is a real network hop and keeps its distance and
the destination's processing.

---

## Reference-style trap

Within one LB wiring, GCP fixtures mix three reference styles — visible in
`docs:run-multi`:

```
google_compute_url_map.default.id
google_compute_backend_service.default.self_link
google_compute_target_http_proxy.p.name
```

`addresses_in()` strips the attribute and returns the bare address, so all
three already resolve to the same node id — verified against
`iacsim/core/refs.py:36-50` (`split_address`) and `refs.py:57-61`. The trap is
therefore not in the parser but in the rule: `gcp_lb_chain` must join on the
**address**, the way `target_group` does (`target_group.py:33`, `:39`), and
must never match on the attribute name. A rule that looks for `.id` drops the
`self_link` and `name` links and the chain breaks in the middle with no warning.

---

## Cloud CDN is an attribute, not a resource

There is no `google_cloud_cdn` type. Cloud CDN is `enable_cdn = true` on a
`google_compute_backend_service`, which is why it cannot be a `TYPE_MAP` row.
`enable_cdn` is in `KEEP_ATTRS` so the fact reaches the node; deciding what to
do with it (a second subtype chosen at normalise time, or a profile key the
`backend_service` block reads) is parked — `01-DECISIONS.md` §6.

---

## Closing checklist

Run this before the map is considered done. Items 1 and 2 are the G8 invariant
and belong in a test, not in a reviewer's head.

- [ ] Every subtype in `TYPE_MAP` has a `processing.<subtype>` block in the profile.
- [ ] Every subtype in `TYPE_MAP` has an `INVOKE_KEYS` entry, and that key exists in its processing block. *(Missing either one means every INVOKE hop into that node is charged 0 ms and nothing is printed — `processing.py:57-60` returns `{}`. `kinesis` is in this state today.)*
- [ ] No GCP subtype name collides with an AWS one, except the `NodeKind.NETWORK` set (`vpc`, `subnet`, `vpc_peering`, `nat`).
- [ ] Both spellings of all five global/regional twins are present (Finding 1).
- [ ] All three NEG types are present (Finding 2).
- [ ] Every compute subtype that cold-starts is in the normaliser's `COLD_START` set (G20).
- [ ] The normaliser adds one `EXTERNAL` node with the literal id `internet`, edged to every GATEWAY / LB / CDN node (G13).
- [ ] Every chain subtype has `route: 0` **and** is exempt from the distance charge (G9).
- [ ] Every **unverified** row is still marked unverified, or has been promoted with a fixture named.

---

## Sources

All fixtures are Apache-2.0 and were read at the pinned commit.

| short name | repository | commit | path |
|---|---|---|---|
| `skills` | `google/skills` — n-tier serverless web app, 29 distinct types, the flagship | `d56d145e512d` | — |
| `docs:lb-mig` | `terraform-google-modules/terraform-docs-samples` | `74058fe5e54b` | `lb/external_http_lb_mig_backend` |
| `docs:lb-regional` | same | `74058fe5e54b` | `lb/regional_external_http_load_balancer` |
| `docs:run-multi` | same | `74058fe5e54b` | `run/multiple_regions` |
| `docs:run-sql` | same | `74058fe5e54b` | `run/connect_cloud_sql` |
| `docs:gke-mt` | same | `74058fe5e54b` | `gke/quickstart/multitenant` |
| `docs:wf-run-job` | same | `74058fe5e54b` | `workflows/cloud_run_job` |
| `docs:eventarc-adv` | same | `74058fe5e54b` | `eventarc/advanced` |
| `docs:fn-pubsub` | same | `74058fe5e54b` | `functions/pubsub` |
| `chat-bot` | `GoogleCloudPlatform/cloud-release-chat-bot` | `d0463c4e43fe` | — |

91 distinct `google_*` types were verified across these fixtures, plus six
non-`google` types (`random_id`, `random_password`, `time_sleep`,
`tls_private_key`, `tls_self_signed_cert`, `kubernetes_config_map`) that appear
in the same files and are all handled by the ignore list.
