# GCP — the wider real-world corpus

## 1. Why this file exists

[`03-TESTING.md`](03-TESTING.md) §1 pins seven fixtures. Six of the seven come from
`terraform-google-modules/terraform-docs-samples`, `google/skills` and
`GoogleCloudPlatform/*` — Google's own reference samples: one `main.tf`, no `module`
blocks, no `.tfvars`, no workspaces, literal regions. They prove the GCP path works on
*clean* input.

This file is the complementary half: **fourteen third-party repositories run by ordinary
teams**, verified at a pinned commit. They are multi-file, they use local modules and
`for_each` and `dynamic` and `terraform.workspace`, and they break a parser in ways the
seven cannot. Every licence below was read from the GitHub licence API and, where that API
returned `NOASSERTION`, from the `LICENSE` file itself.

---

## 2. The corpus table

Shaped like the table in `examples/real-world/README.md`, with three columns added
(pinned commit, `.tf` file count in the named subdirectory, module style).

`use` is the split explained in §5: **vendor** = small and permissive enough to copy into
`examples/real-world/`; **manual** = fetched into a scratch directory and run by hand.

| repo · subdirectory | licence | commit | files | modules | use | what it exercises | expected warnings |
|---|---|---|---|---|---|---|---|
| `DeNA/dify-google-cloud-terraform` · `terraform/` | MIT | `823cddb1c6dd` | 24 tf, 1 tfvars | local ×7 | vendor | `environments/dev` + `../../modules/*`; Cloud Run → Cloud SQL + Memorystore + Filestore over PSA | none |
| `chainguard-dev/terraform-infra-common` · `modules/serverless-gclb/` | Apache-2.0 | `c260468fc0c7` | 2 tf | none | vendor | the whole global LB chain built by `for_each` over a service × region cross-product, plus `dynamic "backend"` | `data.* sources are not evaluated` |
| `dumkydewilde/snowplow-serverless` · repo root | MIT | `68aa644b2d56` | 4 tf | interpolated ×1 | vendor | Pub/Sub → Cloud Run jobs → BigQuery; `templatefile()` ×6; `source = "${path.module}/schemas/${each.value}"` | `remote source … run terraform init` (the interpolated module source cannot resolve) |
| `prodriguezdefino/content-dicovery-platform-gcp` · `infra/` | Apache-2.0 | `7afac137e01f` | 8 tf | registry ×1 | vendor | API Gateway → Cloud Run → AlloyDB + Bigtable + Vertex AI index; Pub/Sub side path | `remote source … run terraform init`, `data.* sources are not evaluated` |
| `GoogleChrome/webstatus.dev` · `infra/` | Apache-2.0 | `a8158d60fb59` | 84 tf, 4 tfvars | local ×31, registry ×1 | manual | the hardest chain in the corpus: `for_each` from region map → Cloud Run → NEG → `dynamic backend` → LB; two provider aliases; Spanner; Pub/Sub; `google_cloud_run_v2_worker_pool` | `data.* sources are not evaluated` (14), one `remote source` |
| `MobilityData/mobility-feed-api` · `infra/` | Apache-2.0 | `cf1e38ce30a6` | 25 tf | local ×6 | manual | 26 Cloud Functions v2, Cloud Tasks, Scheduler, Workflows, Eventarc, LB chain; `dynamic` ×26; `google` + `google-beta` + an `impersonation` alias | `data.* sources are not evaluated` (24) |
| `lehigh-university-libraries/scribe` · `terraform/` | Apache-2.0 | `d1b3648f7e58` | 47 tf | local ×8, remote ×5 | manual | `terraform.workspace` driving `count` and `locals`; `try()` ×65; a `https://…tar.gz` archive module source; Cloud Run jobs + Pub/Sub + KMS | `remote source … run terraform init`, `data.*`, `count` that depends on a workspace name |
| `MaterializeInc/materialize-terraform-self-managed` · `gcp/` | Apache-2.0 | `40b439178737` | 44 tf | local ×37, registry ×4 | manual | GKE + node pools + Cloud SQL behind local modules three levels deep (`../../../kubernetes/modules/*`); `optional()` ×53; `kubernetes`/`helm`/`kubectl` providers | `remote source` ×4, `data.*` |
| `datacommonsorg/website` · `deploy/terraform-datacommons-website/` | Apache-2.0 | `8fdb81e1f367` | 23 tf, 2 tfvars | local ×3, registry ×2 | manual | GKE + Cloud Endpoints (ESP) + IAP in front of a real site; `variables.tfvars` | `remote source` ×2, `data.*` |
| `datacommonsorg/website` · `deploy/terraform-custom-datacommons/` | Apache-2.0 | `8fdb81e1f367` | 5 tf | none | manual | the self-contained twin: Cloud Run service + Cloud Run job → Cloud SQL + Memorystore, no modules at all | `data.* sources are not evaluated` |
| `opensearch-project/opensearch-migrations` · `deployment/terraform/gcp/` | Apache-2.0 | `2fe4538aef16` | 11 tf, 1 tfvars | local ×4 | manual | GKE reached over Private Service Connect and VPC peering; `terraform.tfvars`; regional `google_compute_forwarding_rule` | `data.* sources are not evaluated` (4) |
| `yosupo06/library-checker-judge` · `terraform/` | Apache-2.0 | `01fa3c63f036` | 10 tf | none | manual | Cloud Run + Cloud SQL + a MIG with `google_compute_autoscaler`; `provider "google" { region = "global" }` | `data.*`, `unknown type google_compute_autoscaler` |
| `datawranglerai/self-host-n8n-on-gcr` · `terraform/` | MIT | `0ae597608406` | 3 tf | none | manual | the minimal third-party shape-1 stack: two Cloud Run services → Cloud SQL + Memorystore; `dynamic` ×7 in 3 files | `data.* sources are not evaluated` |
| `Privatehive/gcp-hosted-github-runner` · repo root | MIT | `e4005deab467` | 12 tf | none | manual | one file per concern (`cloudRun.tf`, `tasks.tf`, `vpc.tf`, …); Cloud Run → Cloud Tasks; `dynamic` ×6; no `provider` block at all | `data.*`, `region not resolved` (pass `--region`) |
| `timchap/terraform-google-managed-dagster` · repo root | MIT | `88efc882fa12` | 11 tf | none | manual | Dagster webserver/daemon/code-locations as three Cloud Run services → Cloud SQL, plus one GCE instance; no `provider` block | `region not resolved` (pass `--region`) |

Architecture shapes covered that the seven fixtures do not: **local modules with an
`environments/<env>` root** (dify, materialize, webstatus, mobility), **`terraform.workspace`**
(scribe), **`.tfvars` per environment** (webstatus, datacommons, opensearch),
**`for_each`-built LB chains** (chainguard, webstatus), **Cloud Tasks / Scheduler / Workflows
pipelines** (mobility, github-runner), **AlloyDB / Bigtable / Spanner / Vertex** (content-discovery,
webstatus), and **GKE carrying a real workload** (materialize, datacommons, opensearch).

---

## 3. Per-repo detail

### 3.1 `DeNA/dify-google-cloud-terraform` — the local-module gap, closed

**Directory:** `terraform/` (both `environments/dev/` and `modules/`; the root module is
`terraform/environments/dev`).

Layout is the textbook one the seven fixtures never show:

```
terraform/environments/dev/{main.tf,provider.tf,variables.tf,terraform.tfvars}
terraform/modules/{cloudrun,cloudsql,redis,filestore,network,registry,storage}/{main,outputs,variables}.tf
```

`main.tf` is seven `module` blocks with `source = "../../modules/<x>"` and nothing else — so
**the graph is empty unless local module sources are followed**. That is the property to test.

**Types:** `google_cloud_run_v2_service` ×2, `google_sql_database_instance`,
`google_redis_instance`, `google_filestore_instance`, `google_compute_network`,
`google_compute_subnetwork`, `google_compute_router`, `google_compute_router_nat`,
`google_compute_global_address`, `google_service_networking_connection`,
`google_artifact_registry_repository` ×5, `google_cloud_run_service_iam_binding` ×2.

**Stresses:** local modules (7), `.tfvars`, `dynamic` ×3, `google` + `google-beta`,
variables with no default resolved from `terraform.tfvars`.

**Expect:** zero warnings, and a graph in which the Cloud Run service reaches Cloud SQL and
Memorystore through PSA. A `remote source` warning here means module following is off; an
empty graph means it is off *and* silent.

### 3.2 `chainguard-dev/terraform-infra-common` — the `for_each` LB chain

**Directory:** `modules/serverless-gclb/` (2 files: `main.tf`, `variables.tf`).

The whole global LB chain in one small module, built entirely by `for_each`:

```
locals.regional-backends = merge([for svc in var.public-services : {for region in var.regions : ...}])
google_compute_region_network_endpoint_group.regional-backends   for_each = local.regional-backends
google_compute_backend_service.public-services                   for_each = var.public-services
  dynamic "backend" { for_each = toset(var.serving_regions)
                      group = google_compute_region_network_endpoint_group.regional-backends["${each.value.name}-${backend.key}"]["id"] }
google_compute_url_map.public-service       dynamic "host_rule" / "path_matcher"
google_compute_target_https_proxy.public-service
google_compute_global_forwarding_rule.this  (+ .this-v6)
```

**Stresses:** `for_each` over a computed cross-product, `dynamic` blocks whose `for_each` is
another resource's `for_each` map, and a **map-index reference with a string-interpolated key**
inside a `dynamic` body. `02-TYPE-MAP.md`'s "reference-style trap" note says `gcp_lb_chain` must
join on the address; this fixture is what proves it also survives an index expression.

**Types:** `google_compute_global_address` ×2, `google_compute_managed_ssl_certificate`,
`google_compute_region_network_endpoint_group`, `google_compute_backend_service`,
`google_compute_url_map`, `google_compute_ssl_policy`, `google_compute_target_https_proxy`,
`google_compute_global_forwarding_rule` ×2, `google_dns_record_set` ×2.

**Expect:** one `data.*` warning; the five-node chain present as edges; **N × M** NEG nodes when
`var.public-services` and `var.regions` have defaults, and a single unindexed node when they do
not. Either outcome is acceptable — but which one it is must be pinned by the test.

The **rest of the repo** (`modules/`, 182 `.tf` files) is the GCP analogue of the
`net-lb-app-ext` manual-run entry in `03-TESTING.md` §1.6, and harder: 216 local `module` blocks,
`optional()` ×377, `dynamic` ×100, `for_each` ×225, and modules nested `../../widgets/xy` deep.
Run it once per release; the value is that nothing crashes and no warning is unnamed.

### 3.3 `dumkydewilde/snowplow-serverless` — `templatefile()` and an interpolated module source

**Directory:** repo root (`main.tf`, `variables.tf`, `outputs.tf`, `versions.tf`).

A real Snowplow pipeline: `google_pubsub_topic` → `google_pubsub_subscription` →
`google_cloud_run_v2_job` ×6 → `google_bigquery_dataset`, with a `google_cloud_scheduler_job`
and one `google_cloud_run_v2_service` collector.

**Stresses:** `templatefile()` ×6 (configuration handed to the jobs as rendered HCL/JSON),
`for_each` ×5 over schema files, and

```hcl
source = "${path.module}/schemas/${each.value}"
```

— a `module` source that is neither literal-local nor registry. This is the one input in the
corpus that can make a naive "does the source start with `./`?" check take the wrong branch.

**Expect:** the interpolated source produces exactly one named warning (`remote source … run
terraform init` is the honest match today) and **not** a crash and **not** silence.

### 3.4 `prodriguezdefino/content-dicovery-platform-gcp` — the unverified type families

**Directory:** `infra/` (8 files).

The only repo found that puts four of `02-TYPE-MAP.md`'s **unverified** families into one
real stack:

`google_api_gateway_api` → `google_api_gateway_api_config` → `google_api_gateway_gateway` →
`google_cloud_run_v2_service` → `google_alloydb_cluster` / `google_alloydb_instance` +
`google_bigtable_instance` / `google_bigtable_table` / `google_bigtable_gc_policy` +
`google_vertex_ai_index` / `google_vertex_ai_index_endpoint` /
`google_vertex_ai_index_endpoint_deployed_index`, with `google_pubsub_topic` /
`google_pubsub_subscription` on the ingestion side and `google_compute_router_nat` for egress.

**Stresses:** `jsonencode()` ×5, `templatefile()` ×1, one registry module
(`terraform-google-modules/iam/google//modules/member_iam`), `google` + `google-beta`.

**Expect:** one `remote source` warning, one `data.*` warning, and — critically — **no
`unknown type google_*` warnings**, because every type it declares now has a table row. It is the
fixture that promotes eight rows from *unverified* to verified (§7.2).

### 3.5 `GoogleChrome/webstatus.dev` — the hardest chain

**Directory:** `infra/` (84 `.tf`, 4 `.tfvars` under `infra/.envs/`).

Authored inside the `GoogleChrome` org, so not strictly third-party — but it is a **running
product**, not a documentation sample, and its Terraform is the messiest verified GCP config
found. Include it for the chain, and say in `ATTRIBUTION.md` that Google holds the copyright.

The request path is assembled by resource-to-resource `for_each` at every link:

```
google_cloud_run_v2_service.service                    for_each = var.region_to_subnet_info_map
google_compute_region_network_endpoint_group.neg       for_each = google_cloud_run_v2_service.service
google_compute_backend_service …
  dynamic "backend" { for_each = google_compute_region_network_endpoint_group.neg }
google_compute_url_map → google_compute_target_https_proxy → google_compute_global_forwarding_rule
```

A rule that reads `for_each` only when it is a literal list resolves none of this.

**Stresses:** local modules ×31 (`./backend`, `./frontend`, `./ingestion`, `./workers/*`,
`../modules/job`, `../modules/go_image`), **two provider aliases**
(`google.internal_project`, `google.public_project`) propagated through `providers = {…}` maps
on module blocks, `.tfvars` per environment, `for_each` ×38, `dynamic` ×15, `data.*` ×14, and
the `docker` and `random` providers alongside `google`.

**New types it alone contains:** `google_cloud_run_v2_worker_pool` ×6 and
`google_memorystore_instance` — see §7.1, both are silent-drop risks.

**Expect:** 14 `data.*` warnings and one `remote source`; two Cloud Run services in two regions
reachable from one global forwarding rule; both provider aliases resolving to distinct projects.

### 3.6 `MobilityData/mobility-feed-api` — the serverless-eventing repo

**Directory:** `infra/` (25 files; local modules `./feed-api`, `./functions-python`,
`./load-balancer`, `./workflows`, `./metrics`, `./global`).

The Mobility Database API. 26 `google_cloudfunctions2_function`, 17 `google_cloud_tasks_queue`,
17 `google_cloud_scheduler_job`, 2 `google_workflows_workflow`, 1 `google_eventarc_trigger`,
3 `google_pubsub_topic`, plus the full LB chain (`global_forwarding_rule` ×4 →
`target_https_proxy` ×2 → `url_map` ×2 → `backend_service` ×2 + `backend_bucket` →
`region_network_endpoint_group` ×2) onto one `google_cloud_run_v2_service`, with
`google_compute_security_policy` (Cloud Armor) and a `google_vpc_access_connector`.

**Stresses:** `dynamic` ×26 — the highest density in the corpus outside chainguard —
`depends_on` ×49, `google` + `google-beta` + a `google` alias named `impersonation`,
`data.*` ×24, `jsonencode()` ×3.

**Expect:** 24 `data.*` warnings; the `gcp_eventarc`, `gcp_workflows` and `gcp_pubsub_push`
rules all firing in one graph, which no other fixture does.

### 3.7 `lehigh-university-libraries/scribe` — `terraform.workspace`

**Directory:** `terraform/` (47 files; 33 excluding `terraform/tests/`).

The only verified GCP repo found that drives real branching off `terraform.workspace`:

```hcl
is_prod_workspace          = terraform.workspace == "prod"
is_preview_workspace       = startswith(terraform.workspace, "pr-")
workspace_slug             = replace(lower(terraform.workspace), "/[^a-z0-9-]+/", "-")
count = terraform.workspace == "dev" ? 1 : 0
```

That is the GCP twin of the `modernisation-platform-environments` entry in
`examples/real-world/README.md` — `--workspace development` must change which resources exist.

**Stresses:** `terraform.workspace` ×11, `try()` ×65, local modules
(`./modules/kraken-cloud-run`, `./modules/ollama-cloud-run`, `./modules/vault-cloud-run`),
and a module source form nothing else in either corpus has:

```hcl
source = "https://github.com/libops/cloud-compose/archive/refs/tags/1.10.0.tar.gz//cloud-compose-1.10.0?archive=tar.gz"
```

Also 26 `terraform_data` resources and a `vault` provider (14 `vault_*` resources).

**Expect:** a `remote source` warning naming the `.tar.gz` URL, `data.*` warnings, and a node
count that **changes** between `--workspace prod` and `--workspace dev`. If it does not change,
workspace evaluation is not wired for GCP.

### 3.8 `MaterializeInc/materialize-terraform-self-managed` — GKE behind deep local modules

**Directory:** `gcp/` (44 files: `gcp/examples/{simple,migration,enterprise}` roots over
`gcp/modules/{gke,nodepool,database,load_balancers,networking,storage,operator,monitoring}`).

`google_container_cluster` ×2 + `google_container_node_pool` ×2 + `google_sql_database_instance`
+ `google_storage_bucket` ×2 + `google_pubsub_topic`, with `kubernetes_*` ×16 and `helm_release`
×2 configured from the cluster.

**Stresses:** 37 local `module` blocks, three levels deep
(`../../../kubernetes/modules/materialize-instance`), `optional()` ×53, `dynamic` ×12,
`yamlencode()` ×5, `depends_on` ×44, four registry modules mixed in, and four non-`google`
providers (`kubernetes`, `helm`, `kubectl`, `random`).

**Expect:** four `remote source` warnings for the registry modules and a graph that is otherwise
complete — the local half must resolve even though the registry half cannot. That mixed state is
the case `eks-cluster` (registry-only) and `dify` (local-only) each test one half of.

### 3.9 `datacommonsorg/website` — GKE with Endpoints and IAP, plus a clean twin

**Two directories, deliberately.**

`deploy/terraform-datacommons-website/` (23 files) is the GKE stack: local modules
`../../modules/{gke,esp,apikeys}`, registry modules
`terraform-google-modules/network/google` and `…/project-factory/…/project_services`,
`google_container_cluster` + `google_container_node_pool`, `google_endpoints_service` (ESP),
`google_iap_brand` + `google_iap_client`, `google_compute_global_address` ×2,
`google_compute_managed_ssl_certificate`, `google_dns_managed_zone` + `google_dns_record_set`,
and `variables.tfvars` in two example roots.

`deploy/terraform-custom-datacommons/modules/` (5 files, no `module` blocks) is the twin:
`google_cloud_run_v2_service` + `google_cloud_run_v2_job` → `google_sql_database_instance` +
`google_redis_instance`, with `google_apikeys_key` and Secret Manager. It is the control — same
project, same authors, no module indirection — so a difference in outcome between the two
directories isolates module following from everything else.

### 3.10 `opensearch-project/opensearch-migrations` — GKE reached over PSC

**Directory:** `deployment/terraform/gcp/` (11 `.tf` + `terraform.tfvars`).

Local modules `./modules/connectivity/vpc-peering` and `./modules/connectivity/psc-consumer`
(each used twice), `google_container_cluster` + `google_container_node_pool` ×2,
`google_compute_network_peering`, a regional `google_compute_forwarding_rule`,
`google_compute_address`, `google_compute_router` + `_router_nat`, `google_dns_managed_zone` +
`_record_set`, `helm_release`.

**Stresses:** `terraform.tfvars` at the root (picked up with no flag), `optional()` ×13,
`data.*` ×4, and the **`google_compute_network_peering` pair** that `01-DECISIONS.md` G15 names
as the input to a `gcp_vpc_peering` rule — this is the only verified repo that has one.

### 3.11 `yosupo06/library-checker-judge` — the region trap

**Directory:** `terraform/` (10 files, no modules).

A live competitive-programming judge. `google_cloud_run_v2_service` ×2 →
`google_sql_database_instance`, with a `google_compute_instance_group_manager` +
`google_compute_instance_template` + **`google_compute_autoscaler`** judge fleet,
`google_cloud_run_domain_mapping`, `google_identity_platform_config`,
`google_iam_workload_identity_pool` + `_provider`, `google_firebase_project`.

**The trap:** its provider block is

```hcl
provider "google" {
  region = "global"
}
```

`"global"` is not a region key. Per `02-TYPE-MAP.md`'s placement rules a `location` that is not
`<continent>-<direction><digit>` is left as `None`; the same must hold for a provider-level
`region`, or every node in this repo silently prices as `same_region_unknown_az` against a
region literal that does not exist. `templatefile()` ×2 and `data.*` ×2 alongside.

### 3.12 The three small ones

`datawranglerai/self-host-n8n-on-gcr` (`terraform/`, 3 files) is the smallest complete
third-party shape-1 stack: two `google_cloud_run_v2_service` → `google_sql_database_instance` +
`google_redis_instance`, with `dynamic` ×7 packed into three files. Useful precisely because it
is tiny and still not a Google sample.

`Privatehive/gcp-hosted-github-runner` (repo root, 12 files) splits one small stack across
twelve files named per concern — `cloudRun.tf`, `tasks.tf`, `vpc.tf`, `iam.tf`, `firewall.tf`,
`compute.tf`, `secrets.tf`, `dockerRepository.tf`. **It has no `provider` block**, so `--region`
is required, exactly like `ecs-alb`. `google_cloud_run_v2_service` → `google_cloud_tasks_queue`,
`dynamic` ×6, `google_project_iam_custom_role` ×6.

`timchap/terraform-google-managed-dagster` (repo root, 11 files) is three Cloud Run services
(webserver, daemon, code-locations) plus a `google_cloud_run_v2_job` → one
`google_sql_database_instance`, with a `google_compute_instance` beside them. Also no `provider`
block. The value is the **fan-out**: several compute nodes onto one datastore, which the seven
fixtures never test.

---

## 4. How to fetch each one

Every command below pins the commit. `--depth 1` alone clones the branch tip, which moves — so
fetch the SHA explicitly.

```bash
# pattern
git clone --depth 1 --filter=blob:none --sparse https://github.com/<owner>/<repo> /tmp/<dir>
cd /tmp/<dir>
git sparse-checkout set <subdirectory>
git fetch --depth 1 origin <sha> && git checkout <sha>
```

```bash
# 1  DeNA/dify-google-cloud-terraform
git clone --depth 1 --filter=blob:none --sparse https://github.com/DeNA/dify-google-cloud-terraform /tmp/dify
cd /tmp/dify && git sparse-checkout set terraform
git fetch --depth 1 origin 823cddb1c6ddef38edb55ec0a0d5e5ca00bb7fa3 && git checkout 823cddb1c6ddef38edb55ec0a0d5e5ca00bb7fa3

# 2  chainguard-dev/terraform-infra-common
git clone --depth 1 --filter=blob:none --sparse https://github.com/chainguard-dev/terraform-infra-common /tmp/cgi
cd /tmp/cgi && git sparse-checkout set modules
git fetch --depth 1 origin c260468fc0c718b031f86216f40b0721b499d16f && git checkout c260468fc0c718b031f86216f40b0721b499d16f
# fixture is /tmp/cgi/modules/serverless-gclb ; the whole /tmp/cgi/modules is the manual-run stress

# 3  dumkydewilde/snowplow-serverless   (whole repo is 4 .tf files; no sparse needed)
git clone --depth 1 --filter=blob:none https://github.com/dumkydewilde/snowplow-serverless /tmp/snowplow
cd /tmp/snowplow && git fetch --depth 1 origin 68aa644b2d56c8c9bf6db7693c5b75ae200eb5a3 && git checkout 68aa644b2d56c8c9bf6db7693c5b75ae200eb5a3

# 4  prodriguezdefino/content-dicovery-platform-gcp
git clone --depth 1 --filter=blob:none --sparse https://github.com/prodriguezdefino/content-dicovery-platform-gcp /tmp/cdp
cd /tmp/cdp && git sparse-checkout set infra
git fetch --depth 1 origin 7afac137e01f91d17f4ed05374d38fa33726374b && git checkout 7afac137e01f91d17f4ed05374d38fa33726374b

# 5  GoogleChrome/webstatus.dev
git clone --depth 1 --filter=blob:none --sparse https://github.com/GoogleChrome/webstatus.dev /tmp/webstatus
cd /tmp/webstatus && git sparse-checkout set infra
git fetch --depth 1 origin a8158d60fb597a7250de84584c4c5deb884307ea && git checkout a8158d60fb597a7250de84584c4c5deb884307ea

# 6  MobilityData/mobility-feed-api
git clone --depth 1 --filter=blob:none --sparse https://github.com/MobilityData/mobility-feed-api /tmp/mfa
cd /tmp/mfa && git sparse-checkout set infra
git fetch --depth 1 origin cf1e38ce30a603cabf435a4a8535e64ec38a1309 && git checkout cf1e38ce30a603cabf435a4a8535e64ec38a1309

# 7  lehigh-university-libraries/scribe
git clone --depth 1 --filter=blob:none --sparse https://github.com/lehigh-university-libraries/scribe /tmp/scribe
cd /tmp/scribe && git sparse-checkout set terraform
git fetch --depth 1 origin d1b3648f7e58d072675f4a1fa4d4f3db0404e2cd && git checkout d1b3648f7e58d072675f4a1fa4d4f3db0404e2cd

# 8  MaterializeInc/materialize-terraform-self-managed
git clone --depth 1 --filter=blob:none --sparse https://github.com/MaterializeInc/materialize-terraform-self-managed /tmp/mzsm
cd /tmp/mzsm && git sparse-checkout set gcp kubernetes
git fetch --depth 1 origin 40b439178737b5553b3e5b8d8dfb89a0ac2f7d94 && git checkout 40b439178737b5553b3e5b8d8dfb89a0ac2f7d94
# note: `kubernetes/` is in the sparse set because gcp/ modules reference ../../../kubernetes/modules/*

# 9  datacommonsorg/website   (both directories in one checkout)
git clone --depth 1 --filter=blob:none --sparse https://github.com/datacommonsorg/website /tmp/dcweb
cd /tmp/dcweb && git sparse-checkout set deploy/terraform-datacommons-website deploy/terraform-custom-datacommons
git fetch --depth 1 origin 8fdb81e1f36741ffe7637b43d8c1c821f92e9344 && git checkout 8fdb81e1f36741ffe7637b43d8c1c821f92e9344

# 10 opensearch-project/opensearch-migrations
git clone --depth 1 --filter=blob:none --sparse https://github.com/opensearch-project/opensearch-migrations /tmp/osm
cd /tmp/osm && git sparse-checkout set deployment/terraform/gcp
git fetch --depth 1 origin 2fe4538aef16eafa098545e76545873335bf2d11 && git checkout 2fe4538aef16eafa098545e76545873335bf2d11

# 11 yosupo06/library-checker-judge
git clone --depth 1 --filter=blob:none --sparse https://github.com/yosupo06/library-checker-judge /tmp/lcj
cd /tmp/lcj && git sparse-checkout set terraform
git fetch --depth 1 origin 01fa3c63f036611cff8be611dc1c69a5605f6a70 && git checkout 01fa3c63f036611cff8be611dc1c69a5605f6a70

# 12 datawranglerai/self-host-n8n-on-gcr
git clone --depth 1 --filter=blob:none --sparse https://github.com/datawranglerai/self-host-n8n-on-gcr /tmp/n8n
cd /tmp/n8n && git sparse-checkout set terraform
git fetch --depth 1 origin 0ae5976084065898988b68d8ab6e573b0d381dda && git checkout 0ae5976084065898988b68d8ab6e573b0d381dda

# 13 Privatehive/gcp-hosted-github-runner
git clone --depth 1 --filter=blob:none https://github.com/Privatehive/gcp-hosted-github-runner /tmp/ghr
cd /tmp/ghr && git fetch --depth 1 origin e4005deab4671570b83667ae071bb0b4ba28c3e8 && git checkout e4005deab4671570b83667ae071bb0b4ba28c3e8

# 14 timchap/terraform-google-managed-dagster
git clone --depth 1 --filter=blob:none https://github.com/timchap/terraform-google-managed-dagster /tmp/dagster
cd /tmp/dagster && git fetch --depth 1 origin 88efc882fa1230077d9084c005410d150ad92a48 && git checkout 88efc882fa1230077d9084c005410d150ad92a48
```

### `ATTRIBUTION.md`

Every vendored fixture directory carries one, in the shape `03-TESTING.md` §1.4 requires:
the source URL **including the pinned commit**, the commit hash on its own line, and the
licence. Template, filled in for fixture 1:

```markdown
# Attribution

Source: https://github.com/DeNA/dify-google-cloud-terraform/tree/823cddb1c6ddef38edb55ec0a0d5e5ca00bb7fa3/terraform

Commit: 823cddb1c6ddef38edb55ec0a0d5e5ca00bb7fa3

Licence: MIT — repository-root `LICENSE`, Copyright (c) 2024 DeNA Co., Ltd.

Vendored unmodified: only the `.tf` and `.tfvars` files, no `.git`, no `README`,
no generated `.terraform/`.
```

Two licence notes that must survive into the attribution file:

- **`DeNA/dify-google-cloud-terraform` reports `NOASSERTION` from the GitHub licence API.**
  Its `LICENSE` opens with the line `Terraform for Dify on Google Cloud` before the MIT text,
  which defeats GitHub's classifier. The file is verbatim MIT. State that in `ATTRIBUTION.md`
  so nobody re-opens the question.
- **`GoogleChrome/webstatus.dev` is Apache-2.0 with `Copyright 2023 Google LLC`**, and every
  `.tf` file carries the Apache header inline. Record the copyright holder; it is the one entry
  in this corpus that is not third-party by authorship.

Neither `chainguard-dev/terraform-infra-common` nor `prodriguezdefino/content-dicovery-platform-gcp`
puts a per-file licence header on its `.tf` files, so the repository-root `LICENSE` is the only
licence evidence — copy the SPDX identifier and the repository URL into `ATTRIBUTION.md`
explicitly rather than relying on the reader to go and look.

---

## 5. How to test with them

Three commands, in this order, per directory. Stop at the first one that fails; a `graph`
number read off a config that did not parse is worse than no number.

1. **`iacsim validate <dir>`** — does it parse, and is every warning a *named* one?
   This is the gate. The contract in `03-TESTING.md` §4 is that unfamiliar Terraform never
   crashes and never emits an unnamed warning. Compare the warning set against this file's
   `expected warnings` column; an extra warning is a finding, a missing one usually means a
   whole file was skipped.
2. **`iacsim graph <dir>`** — node and edge counts, and is the picture sensible?
   Record the counts. For the LB repos, check the chain is joined end to end
   (`forwarding_rule → proxy → url_map → backend_service → NEG → Cloud Run`) rather than
   ending at the forwarding rule. For the local-module repos, a node count near zero means
   module following silently did nothing.
3. **`iacsim run <dir>`** — is a scenario inferred, and is it priced?
   Exit 0 with at least one scenario for anything with a public entry point. A total of
   0 ms is the §3.0 free-hop trap, not a fast application.

```bash
iacsim validate /tmp/dify/terraform/environments/dev
iacsim graph    /tmp/dify/terraform/environments/dev
iacsim run      /tmp/dify/terraform/environments/dev

iacsim validate /tmp/ghr --region us-central1     # no provider block; --region is required
iacsim graph    /tmp/scribe/terraform --workspace prod
iacsim graph    /tmp/scribe/terraform --workspace dev   # the node count must differ
```

### The split: vendored versus manual-run

The AWS corpus in `examples/real-world/README.md` sets the rule — **vendor the small permissive
ones so `tests/test_real_world.py` runs them on every commit; manual-run the large ones.**
Applied here:

**Vendor four** (38 `.tf` files total, all MIT or Apache-2.0, each adding a shape the seven
fixtures lack):

| fixture name | source | why this one |
|---|---|---|
| `gcp-local-modules-envs` | dify `terraform/` | the only `environments/<env>` + `../../modules/*` layout; without it nothing tests local module following |
| `gcp-foreach-multiregion-glb` | chainguard `modules/serverless-gclb/` | the only `for_each`-built LB chain small enough to vendor |
| `gcp-pubsub-templatefile-pipeline` | snowplow-serverless (root) | `templatefile()` ×6 and the interpolated `module` source |
| `gcp-managed-api-datastores` | content-discovery `infra/` | AlloyDB, Bigtable, Vertex and API Gateway in one 8-file stack — the type-map breadth test |

Each needs a `MIN_NODES` entry in `tests/test_real_world.py` (`03-TESTING.md` §3.6 — this is the
one that fails with a bare `KeyError` in a test that never names the new fixture).

**Manual-run the rest** — the eleven remaining table rows plus `chainguard-dev/terraform-infra-common`
`modules/` in full — added to the manual-run table in `examples/real-world/README.md` in that
file's `repo | why | what to expect` shape. Four of them earn a recorded run each release, with
node count and warning set written into the M11 notes so a regression in HCL evaluation is
visible:

- `chainguard-dev/terraform-infra-common` `modules/` — 182 files, `optional()` ×377, the ceiling
- `GoogleChrome/webstatus.dev` `infra/` — the `for_each` chain and two provider aliases
- `lehigh-university-libraries/scribe` `terraform/` — `terraform.workspace`, run twice
- `MaterializeInc/materialize-terraform-self-managed` `gcp/` — local and registry modules mixed

The rest — `mobility-feed-api`, both `datacommonsorg/website` directories,
`opensearch-migrations`, `library-checker-judge`, `n8n`, `github-runner` and `dagster` — are
fetch-and-run on demand. Three of them — `n8n` (3 files), `dagster` (11), `github-runner` (12) —
are small and permissive enough to promote into the vendored set later if a shape they cover
turns out to need continuous coverage; they are held back only to keep the vendored corpus from
outgrowing the AWS one.

---

## 6. Rejected candidates

Recorded so nobody re-researches them. Every licence here was read from the GitHub licence API
at the same time as the accepted ones.

| candidate | reason |
|---|---|
| `pdx-tools/pdx-tools` | **AGPL-3.0** — incompatible with vendoring, and out of scope even for manual-run under the repo's licence rule |
| `cal-itp/data-infra` | **AGPL-3.0**. A shame: a real Cal-ITP data platform on GCP, 300 MB, exactly the mess wanted |
| `sfbrigade/compass` | **unlicensed** (no `LICENSE`, API returns `NONE`) |
| `dlabsai/mlflow-for-gcp` | **unlicensed**; MLflow on Cloud Run would otherwise have been a good small fixture |
| `4ks-io/4ks` | **unlicensed**; had `terraform.workspace` + Cloud Run |
| `stacktome/jenkins-ci` | **unlicensed** |
| `container8/internal-ilb-l7` | **unlicensed**. The only cross-region internal L7 ILB example found — worth re-checking if a licence is ever added |
| `kaysalawu/multicloud-network-terraform` | **unlicensed**; multi-region hub-and-spoke, would have been the best `provider alias` test |
| `jtcressy/homelab` | **unlicensed**, and last pushed 2022 |
| `TykTechnologies/tyk-performance-testing` | **unlicensed** |
| `feitais/acme-store-infra` | **unlicensed**; the only `environments/production/` GKE layout the search surfaced |
| `arXiv/arxiv-browse` | licence is fine (MIT; the API's `NOASSERTION` is a classifier artefact) but the Terraform is **3 files of `google_project_iam_member` and one service account** — no request path |
| `catalyst-cooperative/pudl` | MIT, but the same problem: 4 files, almost entirely IAM and buckets, no request path |
| `x1unix/go-playground` | MIT, but 3 files and 5 resources — thinner than the smallest existing fixture, adds nothing |
| `ashdavies/playground.ashdavies.dev` | Apache-2.0, but **registry modules only** (`terraform-google-modules/*` ×5) — reproduces the dead-graph problem `eks-cluster` already documents |
| `sicara/sicarator` | Apache-2.0 and `terraform.workspace` ×22, but the GCP half is **one** `google_cloud_run_v2_service`; the rest is AWS ECS. It is a project template, not a deployment |
| `TyeMcQueen/terraform-google-ingress-to-gke` | Unlicense — outside the named permissive set (Apache-2.0 / MIT / BSD / MPL-2.0) — and 3 `.tf` files declaring **no** resources; last pushed 2023 |
| `staropshq/terraform-gcp-gke-module` | Apache-2.0, but a GKE module library whose sources are git tags (`…?ref=v0.2.0`), so it needs `terraform init` like a registry module, and it has no request path |
| `GoogleCloudPlatform/avocano` | Apache-2.0 and a real app — but Google-authored, which is the exact category `03-TESTING.md` already covers seven times |
| `google/osdfir-infrastructure` | same reason: Google-authored |
| `GoogleCloudPlatform/terraform-google-examples` | archived 2020 (already recorded in `03-TESTING.md` §1.5) |

**Two licence lessons worth keeping.** GitHub's licence API returns `NOASSERTION` for a file it
cannot classify, not only for a file that is missing — `DeNA/dify-google-cloud-terraform` and
`arXiv/arxiv-browse` are both plainly MIT and both report `NOASSERTION`. Always read the
`LICENSE` blob before rejecting on that value. And `NONE` genuinely means no licence file: eight
of the eleven most architecturally interesting GitHub code-search hits for GCP Terraform were
unlicensed, which matches the note already in `03-TESTING.md` §1.5.

---

## 7. New `google_*` types found

134 distinct `google_*` resource types appear across the fourteen accepted repositories.
**56 of them are not mentioned anywhere in [`02-TYPE-MAP.md`](02-TYPE-MAP.md).** That file is
the type map's source of truth, and a gap in it is a silently-unpriced hop, so all 56 are listed.

### 7.1 Needs a `TYPE_MAP` row — these can sit on a request path

| type | seen in | proposed | why it matters |
|---|---|---|---|
| `google_cloud_run_v2_worker_pool` | webstatus.dev ×6 | COMPUTE `cloud_run_worker_pool` | **The largest gap.** A pull-based Cloud Run worker; six of them carry webstatus.dev's entire ingestion tier. Unmapped, the Pub/Sub consumers vanish into `unknown type` |
| `google_memorystore_instance` | webstatus.dev, chainguard | DATASTORE `memorystore` | **The second gap.** The successor API to `google_redis_instance`, which *is* mapped. A stack on the new type shows no cache node at all, and the cache hop prices as nothing |
| `google_endpoints_service` | datacommons | GATEWAY `cloud_endpoints` | Cloud Endpoints / ESP in front of GKE — a public entry point, so it needs an `internet` edge |
| `google_api_gateway_api_config` | content-discovery | GATEWAY glue, ignore | completes the three-resource API Gateway triple already in the map |
| `google_cloud_run_domain_mapping` | library-checker-judge | entry-point evidence | a second public entry to a Cloud Run service that bypasses the LB chain entirely |
| `google_compute_autoscaler` | library-checker-judge | MIG attribute, or ignore | attaches to a `google_compute_instance_group_manager`; carries the capacity fact G6 defers |
| `google_compute_service_attachment` | chainguard | NETWORK `psc_attachment` | Private Service Connect producer side — changes reachability |
| `google_network_connectivity_service_connection_policy` | webstatus.dev | NETWORK | PSC consumer policy; the partner of the above |
| `google_bigquery_data_transfer_config` | chainguard | ORCHESTRATOR `bq_transfer`, or ignore | scheduled data movement into BigQuery |
| `google_storage_transfer_job` | scribe | ORCHESTRATOR, or ignore | same shape for GCS |

### 7.2 Rows promoted from **unverified** to verified

`02-TYPE-MAP.md` marks these "added from knowledge, not seen in the corpus". This corpus sees
them. Update the "seen in" column:

| type | now seen in |
|---|---|
| `google_api_gateway_api`, `google_api_gateway_gateway` | content-discovery |
| `google_alloydb_cluster`, `google_alloydb_instance` | content-discovery |
| `google_bigtable_instance`, `google_bigtable_table` | content-discovery |
| `google_spanner_instance`, `google_spanner_database` | webstatus.dev |
| `google_bigquery_dataset` | chainguard, snowplow |
| `google_bigquery_table` | chainguard |
| `google_filestore_instance` | dify |
| `google_cloud_tasks_queue` | mobility-feed-api ×17, github-runner |
| `google_compute_network_peering` | opensearch-migrations |
| `google_compute_router_nat` | dify, scribe, chainguard, opensearch-migrations, content-discovery, github-runner |
| `google_vpc_access_connector` | mobility-feed-api |

Still unverified after this round, and therefore still to be marked so: the two
`google_app_engine_*` types, `google_dataflow_job`, `google_memcache_instance`.

### 7.3 The IAM finding — enumerate a suffix, not a list

Fifteen of the 56 new types are IAM grants:

```
google_artifact_registry_repository_iam_member   google_bigquery_dataset_iam_member
google_bigquery_table_iam_binding                google_cloud_run_v2_job_iam_binding
google_cloud_run_v2_job_iam_member               google_cloudfunctions2_function_iam_member
google_iap_tunnel_instance_iam_member            google_kms_crypto_key_iam_member
google_project_iam_audit_config                  google_pubsub_subscription_iam_binding
google_pubsub_subscription_iam_member            google_pubsub_topic_iam_member
google_service_account_iam_binding               google_spanner_database_iam_member
google_storage_bucket_iam_binding
```

`02-TYPE-MAP.md`'s ignore list enumerates roughly a dozen IAM types by name. The corpus shows
the real space is **`google_<any resource family>_iam_{member,binding,policy}`** — the provider
generates one triple per resource family, so an enumeration can never be finished and each
missing entry becomes an `unknown type` warning on a resource that should have been silent
evidence for `gcp_iam_binding`.

**Recommendation:** replace the enumeration with a suffix rule — anything matching
`^google_.*_iam_(member|binding|policy|audit_config)$`, plus the three bare
`google_project_iam_*` spellings, is ignored as a node and routed to `gcp_iam_binding` as
evidence. That collapses fifteen missing rows and every future one into a single line, and it is
testable: assert that a made-up `google_nonexistent_thing_iam_member` is ignored silently.

### 7.4 Needs an ignore-list row — glue, never a hop

Grouped, with the family that is missing named first. None of these should ever become a node.

- **KMS — `google_kms_*` is absent from `02-TYPE-MAP.md` entirely.** `03-TESTING.md` §3.2 asserts
  `google_kms_*` is on the ignore list; it is not. Seen: `google_kms_key_ring`,
  `google_kms_crypto_key` (scribe). This is a direct inconsistency between the two files.
- **Observability — the map lists only `google_monitoring_alert_policy`.** Seen:
  `google_monitoring_dashboard`, `google_monitoring_slo`, `google_monitoring_custom_service`,
  `google_monitoring_uptime_check_config` (chainguard ×31 alert policies alone),
  `google_logging_metric`, `google_logging_project_sink`.
- **Auth and identity:** `google_identity_platform_config`, `google_identity_platform_tenant`,
  `google_identity_platform_tenant_default_supported_idp_config`,
  `google_iam_workload_identity_pool`, `google_iam_workload_identity_pool_provider`,
  `google_iap_brand`, `google_iap_client`, `google_apikeys_key`,
  `google_project_iam_custom_role`, `google_service_account_key`, `google_storage_hmac_key`.
- **Datastore children:** `google_datastore_index`, `google_bigquery_routine`,
  `google_bigtable_gc_policy`, `google_vertex_ai_index_endpoint_deployed_index`.
- **Network glue:** `google_compute_router`, `google_compute_route`, `google_compute_ssl_policy`,
  `google_dns_policy`, `google_compute_project_metadata_item`.
- **Org and fleet:** `google_tags_tag_binding`, `google_tags_location_tag_binding`,
  `google_os_config_patch_deployment`.

### 7.5 Non-`google` providers in the corpus

`02-TYPE-MAP.md` copies `random_`, `tls_` and `time_sleep` into the GCP `IGNORED_PREFIXES`, and
adds `kubernetes_config_map`. This corpus contains considerably more, and every one of them will
otherwise produce an `unknown type … kept as a network node` warning in a repo that is otherwise
clean:

| prefix | count | seen in |
|---|---|---|
| `random_*` | 32 | almost every repo |
| `terraform_data` | 28 | scribe ×26, mobility-feed-api |
| `kubernetes_*` | 16 | materialize, staropshq-style GKE stacks |
| `aws_*` | 16 | chainguard (it is a multi-cloud repo) |
| `vault_*` | 14 | scribe |
| `docker_*` / `docker_registry_image` | 10 | webstatus.dev, scribe |
| `null_resource` | 6 | datacommons, content-discovery |
| `helm_release` | 4 | materialize, datacommons, opensearch-migrations |
| `ko_build` | 3 | chainguard |
| `cosign_sign` | 3 | chainguard |
| `azuread_*`, `azurerm_role_assignment` | 5 | chainguard |
| `time_sleep` | 1 | materialize |

`kubernetes_config_map` is already listed; the rest are not. `terraform_data` is the notable one
— it is a **built-in** Terraform resource, not a provider resource, and at 28 occurrences it is
the single most common non-`google` type in the corpus.

---

## Sources

Every row was checked against the GitHub API at the pinned commit: licence via
`/repos/{owner}/{repo}` (`license.spdx_id`), archived state and last push from the same call,
and the commit SHA from `/repos/{owner}/{repo}/commits?per_page=1`. Where the API returned
`NOASSERTION` the `LICENSE` blob itself was read. File counts, module sources, resource types
and HCL feature counts were counted over the `.tf` files at that commit.

| repo | licence | archived | last push | commit |
|---|---|---|---|---|
| `GoogleChrome/webstatus.dev` | Apache-2.0 | no | 2026-09-07 | `a8158d60fb597a7250de84584c4c5deb884307ea` |
| `MobilityData/mobility-feed-api` | Apache-2.0 | no | 2026-09-04 | `cf1e38ce30a603cabf435a4a8535e64ec38a1309` |
| `DeNA/dify-google-cloud-terraform` | MIT (API: `NOASSERTION`) | no | 2025-06-24 | `823cddb1c6ddef38edb55ec0a0d5e5ca00bb7fa3` |
| `lehigh-university-libraries/scribe` | Apache-2.0 | no | 2026-09-05 | `d1b3648f7e58d072675f4a1fa4d4f3db0404e2cd` |
| `chainguard-dev/terraform-infra-common` | Apache-2.0 | no | 2026-09-06 | `c260468fc0c718b031f86216f40b0721b499d16f` |
| `MaterializeInc/materialize-terraform-self-managed` | Apache-2.0 | no | 2026-09-07 | `40b439178737b5553b3e5b8d8dfb89a0ac2f7d94` |
| `datacommonsorg/website` | Apache-2.0 | no | 2026-09-06 | `8fdb81e1f36741ffe7637b43d8c1c821f92e9344` |
| `opensearch-project/opensearch-migrations` | Apache-2.0 | no | 2026-09-07 | `2fe4538aef16eafa098545e76545873335bf2d11` |
| `prodriguezdefino/content-dicovery-platform-gcp` | Apache-2.0 | no | 2025-07-07 | `7afac137e01f91d17f4ed05374d38fa33726374b` |
| `yosupo06/library-checker-judge` | Apache-2.0 | no | 2026-07-29 | `01fa3c63f036611cff8be611dc1c69a5605f6a70` |
| `datawranglerai/self-host-n8n-on-gcr` | MIT | no | 2026-05-13 | `0ae5976084065898988b68d8ab6e573b0d381dda` |
| `dumkydewilde/snowplow-serverless` | MIT | no | 2024-02-08 | `68aa644b2d56c8c9bf6db7693c5b75ae200eb5a3` |
| `Privatehive/gcp-hosted-github-runner` | MIT | no | 2025-07-05 | `e4005deab4671570b83667ae071bb0b4ba28c3e8` |
| `timchap/terraform-google-managed-dagster` | MIT | no | 2024-07-03 | `88efc882fa1230077d9084c005410d150ad92a48` |
