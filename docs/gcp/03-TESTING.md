# M11 GCP — testing checklist

Every testable item on this page is a checkbox. Tick them as they go green; the page is the
status truth for M11 testing the way `TIMELINE.md` is for milestones.

Scope: what proves GCP support works, and what proves adding it did not move AWS. Nothing here
changes `iacsim/`, `tests/` or `examples/` — this is the plan those changes are written against.

**Start with §3.0 guard-rails.** They are the three tests that catch silent wrongness — a hop
priced at nothing, a region that resolved to `None`, an AWS number that moved. Everything else
catches loud wrongness, which the existing 290 tests already model well.

Repo rules that apply throughout (`CONTRIBUTING.md` → Style):

- Tests assert formulas over profile values, never a magic number.
- Unresolvable input is a warning, never a crash — and every warning must be a *named* one.
- British spelling: normaliser, behaviour.

---

## 1. The vendored fixture corpus

Seven fixtures under `examples/real-world/`, vendored unmodified with an `ATTRIBUTION.md` each,
exactly as the AWS corpus in `examples/real-world/README.md`.

**Why one source dominates:** `terraform-google-modules/terraform-docs-samples` (Apache-2.0,
pinned at commit `74058fe5e54b`) is the primary source because **16 of 16 sample directories
checked contain zero `module` blocks**. They are self-contained `main.tf` files that graph fully
with no `terraform init`.

The obvious alternatives fail that test. The official `terraform-google-modules/*` and
`GoogleCloudPlatform/terraform-google-*` module repos keep their realistic stacks in `examples/`,
and those `examples/` pull registry modules — which reproduces the dead-graph problem of the
existing `eks-cluster` fixture, where the graph is empty until `terraform init` has populated
`.terraform/modules/modules.json`. One such fixture is useful as a documented limitation; seven
would be a corpus that tests nothing.

### 1.1 Vendored corpus

| fixture | source | shape covered | size | modules | what it exercises | expected warnings |
|---|---|---|---|---|---|---|
| `gcp-ntier-serverless-web` | `google/skills` @ `d56d145e512d`, path `skills/cloud/google-cloud-solution-n-tier-serverless-web-app/assets` | 1 + 4 + 6 | 1 file, 824 lines, 29 types | 0 | two Cloud Run tiers → Cloud SQL + Memorystore Redis behind the full HTTPS LB chain; Cloud Armor, DNS, VPC/PSA, firewall policy, monitoring alert | none |
| `gcp-glb-mig-backend` | terraform-docs-samples `lb/external_http_lb_mig_backend` | 4 | 157 lines, 9 resources | 0 | the canonical minimal global HTTP LB chain onto a MIG, literal regions, no provider block, no variables | none |
| `gcp-cloudrun-multiregion-glb` | terraform-docs-samples `run/multiple_regions` | 4 + 6 | 216 lines, 14 resources | 0 | `count`-fanned Cloud Run + serverless NEGs across two regions, one global LB, HTTP→HTTPS redirect; every resource on `google-beta` | none |
| `gcp-functions-firestore-pubsub` | `GoogleCloudPlatform/cloud-release-chat-bot` @ `d0463c4e43fe`, path `release/` | 2 | 2 files, 16 KB | 0 | Cloud Functions + Firestore + Pub/Sub; five `data.archive_file` blocks pointing outside the vendored directory | `data.* sources are not evaluated` |
| `gcp-gke-multitenant` | terraform-docs-samples `gke/quickstart/multitenant` | 3 | 206 lines, 14 resources | 0 | GKE with node pools, a second `kubernetes` provider configured from `data.google_client_config`, workload identity, Cloud SQL beside the cluster | `data.* sources are not evaluated` |
| `gcp-eventarc-workflows-run` | terraform-docs-samples `workflows/cloud_run_job` | 5 | 226 lines, 17 resources | 0 | Eventarc trigger → Workflows → Cloud Run job; service-account IAM bindings as the only edge evidence | none |
| `gcp-lb-regional` | terraform-docs-samples `lb/regional_external_http_load_balancer` | 4 (regional) | 13 resources | 0 | the regional half of every global/regional twin type | none |

The six architecture shapes the corpus must cover: **1** Cloud Run + Cloud SQL behind an LB ·
**2** Cloud Functions + Firestore + Pub/Sub · **3** GKE with node pools · **4** the full HTTP LB
chain · **5** Eventarc/Workflows → Cloud Run · **6** multi-region.

### 1.2 Per-fixture notes

**`gcp-ntier-serverless-web` — the flagship.** One 824-line file covering shapes 1, 4 and 6 at
once. Two Cloud Run tiers reach Cloud SQL and Memorystore Redis, behind the complete verified
chain:

```
google_compute_region_network_endpoint_group.frontend_neg
  → google_compute_backend_service.frontend_lb_backend
  → google_compute_url_map.default
  → google_compute_target_https_proxy.default
  → google_compute_global_forwarding_rule
```

No placeholders, no `module` blocks, no `data` sources — so it should graph with zero warnings,
which makes it the fixture that proves the GCP path end to end. It also declares `tls_*`
resources, already covered by the normaliser's `IGNORED_PREFIXES`.

**`gcp-glb-mig-backend` — the control.** Exactly the five chain resources and nothing in the way:
`google_compute_global_forwarding_rule`, `target_http_proxy`, `url_map`, `backend_service`,
`health_check`, plus `global_address`, `instance_group_manager`, `instance_template`, `firewall`.
No provider block and no variables — regions are literals. When the `gcp_lb_chain` rule breaks,
this is the fixture whose failure is readable.

**`gcp-cloudrun-multiregion-glb` — the multi-region test.**
`locals.run_regions = ["us-central1", "europe-west1"]` with `count = length(...)` fans Cloud Run
services *and* serverless NEGs across both regions, then one global LB fronts them. It builds two
chains — HTTPS, plus an HTTP→HTTPS redirect through a `url_map` with `default_url_redirect` —
sharing one `global_address`. It exercises `count`-indexed cross-resource references such as
`google_cloud_run_v2_service.run_default[count.index].name` inside a NEG's `cloud_run {}` block.
**Every resource sets `provider = google-beta`**, which is the single hardest parser case in the
corpus (see §3.1).

**`gcp-functions-firestore-pubsub` — the `data.*` case.** Its five `data.archive_file` blocks
point outside the vendored directory. That reproduces the existing
`data.* sources are not evaluated` warning rather than inventing a new one, matching the AWS
corpus convention (`serverless-apigw-lambda-dynamodb` does the same).

**`gcp-gke-multitenant` — the cross-provider case.** It earns its place beyond GKE: it configures
a *second* provider (`kubernetes`) from a GCP data source (`data.google_client_config`), a
cross-provider dependency nothing in the AWS corpus tests. It also has workload identity
(`workload_pool = "….svc.id.goog"`) and a `google_project_iam_member` binding
`serviceAccount:….svc.id.goog[<ns>-team/default]`, plus Cloud SQL alongside the cluster. It is
the only terraform-docs-samples directory found that contains a `provider "…"` block at all.

**`gcp-lb-regional` — the twin-type trap.** It exists specifically to catch the global-vs-regional
type gap: `google_compute_region_url_map`, `google_compute_region_target_http_proxy`,
`google_compute_region_backend_service`, `google_compute_region_health_check`, and a plain
`google_compute_forwarding_rule`. A `TYPE_MAP` that only lists the global spellings passes every
other fixture and fails this one.

### 1.3 Fetching them

Five of the seven come from one repo, so one sparse checkout covers them:

```
git clone --depth 1 --filter=blob:none --sparse \
  https://github.com/terraform-google-modules/terraform-docs-samples /tmp/tds
cd /tmp/tds && git sparse-checkout set \
  lb/external_http_lb_mig_backend lb/regional_external_http_load_balancer \
  run/multiple_regions gke/quickstart/multitenant workflows/cloud_run_job
```

The two standalone ones:

```
git clone --depth 1 --filter=blob:none --sparse https://github.com/google/skills /tmp/skills
cd /tmp/skills && git sparse-checkout set \
  skills/cloud/google-cloud-solution-n-tier-serverless-web-app/assets

git clone --depth 1 https://github.com/GoogleCloudPlatform/cloud-release-chat-bot /tmp/crcb
# fixture is /tmp/crcb/release/
```

Pin each clone to the commit in the table before vendoring (`git checkout <commit>`).

### 1.4 Vendoring checklist

- [ ] Copy only the `.tf` files and the data files they reference — no `.git`, no `README`, no
      generated `.terraform/`.
- [ ] Write `ATTRIBUTION.md` in each fixture directory with: the source URL **including the
      pinned commit**, the commit hash on its own line, and the licence (Apache-2.0).
- [ ] State that the licence is the repo-root `LICENSE`, and that every terraform-docs-samples
      `main.tf` also carries the Apache header inline — so the licence travels with the vendored
      file even read on its own.
- [ ] Add a row per fixture to the table in `examples/real-world/README.md`, in that file's
      existing `fixture | what it exercises | expected warnings` shape.
- [ ] Add each fixture to `MIN_NODES` in `tests/test_real_world.py` — see §3.6, this is the one
      that fails with a `KeyError` rather than a readable message.

### 1.5 Ruled out — recorded so nobody re-researches it

| candidate | why not |
|---|---|
| `GoogleCloudPlatform/cloud-foundation-fabric` `blueprints/` | Deprecated as of v44.0.0; exists only at tag `v43.0.0`. Blueprints are near-pure module wrappers — `glb-and-armor/main.tf` declares exactly **one** direct resource. Keep it in the manual-run corpus as a local-module (`../../../modules/`) stress test only. |
| `hashicorp/terraform-provider-google` | 264 MB, and its fixtures live embedded in Go test strings, not as `.tf` files. |
| `GoogleCloudPlatform/terraform-google-examples` | Archived in 2020. |
| Most GitHub code-search hits for "Cloud Run + SQL + LB" | Unlicensed. One was GPL-3.0 — incompatible with vendoring into this repo. |

### 1.6 Manual-run corpus (not vendored — size or licence)

Mirrors the manual-run table in `examples/real-world/README.md`. Fetch read-only into a scratch
directory, then run `graph`, `validate`, `run`.

| repo | why | what to expect |
|---|---|---|
| `terraform-google-modules/terraform-google-lb-http/examples/cloudrun/` (Apache-2.0) | the GCP analogue of the existing `eks-cluster` registry-module fixture: proves the `remote source … run terraform init` path is provider-neutral | empty graph until `terraform init`; one `remote source` warning per registry module |
| `GoogleCloudPlatform/cloud-foundation-fabric/modules/net-lb-app-ext/` (Apache-2.0) | 13 files, 15 LB resource types; `urlmap.tf` alone is 38 KB of `dynamic` / `for_each` / `optional()` — the hardest HCL-evaluation test in the GCP ecosystem | `dynamic` and `not evaluated` warnings; the value is that nothing crashes and no warning is unnamed |

- [ ] Both manual-run entries added to `examples/real-world/README.md`'s manual-run table.
- [ ] `net-lb-app-ext` run by hand once per release; record the node count and warning set in the
      M11 notes so a regression in HCL evaluation is visible.

---

## 2. Hand-written examples

The repo convention is a controlled pair whose only difference is the thing being measured —
`classic-web` / `classic-web-bad` differ solely in which region the database sits in, so
`iacsim diff` has a known expected delta. GCP needs the same pair.

### `examples/gcp-web/` — the good baseline

```
internet → global HTTP(S) LB → Cloud Run (us-central1) → Cloud SQL (us-central1)
                                                       → Memorystore Redis (us-central1)
```

- [ ] `main.tf` — provider block with a literal region, the five-resource LB chain, one
      `google_cloud_run_v2_service`, one `google_sql_database_instance`, one
      `google_redis_instance`, and the `google_compute_region_network_endpoint_group` that binds
      the backend service to Cloud Run.
- [ ] `variables.tf` — `region`, `project`, matching `classic-web`'s shape.
- [ ] `outputs.tf`.
- [ ] `scenarios.yaml` — one `page_load` scenario entering at the forwarding rule, with two
      sequential Cloud SQL queries, so the expected total is hand-checkable the way
      `classic-web/scenarios.yaml` is.
- [ ] `iacsim.yaml` setting `provider: google` (or rely on auto-detection, if §1 of
      `01-DESIGN` chooses that) — whichever, the file must document the choice.

### `examples/gcp-web-bad/` — the twin

Identical to `gcp-web` except the database moves to a second region.

- [ ] `main.tf` — same resources, with a second provider block
      (`provider "google" { alias = "db", region = var.db_region }`) and the Cloud SQL instance on
      it, reached over VPC peering / PSA.
- [ ] Same `variables.tf` plus `db_region`, same `outputs.tf`, same `scenarios.yaml`.

### What the diff must prove

- [ ] `iacsim diff examples/gcp-web examples/gcp-web-bad` reports a total increase equal to
      `2 × queries × (cross_region[us-central1/europe-west1] − same_region_unknown_az)` computed
      from the profile — never a hard-coded millisecond figure.
- [ ] The node and edge sets are otherwise identical: the diff attributes the whole delta to
      placement, not to a shape change.
- [ ] A `conftest.py` fixture pair exists for each: `gcp_web` / `gcp_web_run` and
      `gcp_web_bad` / `gcp_web_bad_run`, session-scoped, following the existing pattern.
- [ ] A `tests/test_example_gcp_web.py` written on the `tests/test_example_order_queue.py`
      template: docstring naming the topology, node-id constants, a
      `raw.warnings == [] and graph.warnings == []` assertion, per-edge kind/ops/rule/evidence
      assertions, negative assertions (`graph.find_edge(...) is None`), an exact edge count, and
      timing recomputed from profile values.
- [ ] A `tests/test_example_gcp_diff.py` on the `tests/test_example_diff.py` template.

---

## 3. The test plan

### 3.0 Guard-rails — do these first

These three catch failures that produce a plausible-looking number instead of an error.

- [ ] **No GCP subtype in `TYPE_MAP` without both a `processing` entry and an `INVOKE_KEYS`
      entry.** A table-driven test over the GCP type table asserting, for every subtype it can
      produce, that `latency/defaults.yaml` has a `processing.<subtype>` block and that
      `INVOKE_KEY_FOR_SUBTYPE` names a key that block actually contains.

      *What it proves:* a missing entry charges the hop **nothing**, silently — no warning, no
      error, just a total that is too low. `iacsim/latency/rules/processing.py` returns `{}` when
      the key is absent. This is precedent, not theory: `aws_kinesis_stream` maps to subtype
      `kinesis`, `INVOKE_KEY_FOR_SUBTYPE["kinesis"] == "publish"`, and
      `latency/defaults.yaml` has **no `processing.kinesis` block at all** — every hop into a
      Kinesis stream is priced at zero today. GCP adds roughly a dozen subtypes at once; without
      this test at least one of them lands in the same state.
- [ ] Extend the same test to AWS (it will fail on `kinesis`) and either add the missing
      `processing.kinesis` block or record an explicit allowlist entry with a reason. Do not
      ship a GCP guard-rail that exempts the bug it was modelled on.
- [ ] **No GCP node with `placement.region is None`** in any of the seven fixtures or either
      hand-written example, when a region is resolvable from the config.

      *What it proves:* the parser fix landed. `iacsim/parsers/terraform/loader.py` currently
      hard-codes the provider name — `_resolve_regions` skips every block whose
      `block["name"] != "aws"`, `_provider_alias` returns `"aws"` for any unrecognised
      `provider =` expression, and the per-resource fallback is `self.regions.get("aws")`. Until
      those three are provider-neutral, every `google_*` resource gets `region=None`, every hop
      prices as `same_region_unknown_az` (0.5 ms), and the entire cross-region cost that
      `gcp-web-bad` exists to demonstrate silently vanishes into a warning nobody reads.
- [ ] **AWS behaviour byte-identical after the provider-owned-tables refactor.** Run the existing
      suite unchanged before and after; the 290 existing tests are the regression proof. Assert
      no AWS test file needed an edit — if one did, the refactor changed behaviour, not structure.
- [ ] Pin the AWS report bytes: `tests/fixtures/foosh_report_sha256.txt` already hashes a full
      report. It must not move.

### 3.1 Parser layer

New small fixtures belong in `tests/fixtures/`, following the existing `tfvars/`, `workspace/`,
`nested-dynamic/` pattern — one directory, one `main.tf`, driven from a test that calls
`build_graph` directly.

- [ ] `provider "google" { region = … }` resolves, and resources with no explicit `provider`
      attribute inherit it. *Proves the default-provider key is no longer the literal `"aws"`.*
- [ ] `provider "google-beta"` resolves, and `provider = google-beta` on a resource binds to it.
      *Proves `_provider_alias` handles a non-aliased, hyphenated provider reference —
      `"${google-beta}"` does not match the current `^\$\{(\w+)\.(\w+)\}$` alias regex and falls
      through to `"aws"`.* This is what `gcp-cloudrun-multiregion-glb` needs.
- [ ] `provider "google" { alias = "db", region = … }` plus `provider = google.db` on a resource
      resolves to the second region. *Proves the `gcp-web-bad` diff is real.*
- [ ] A `providers = { google = google.db }` map on a module block propagates. *Same mechanism
      `classic-web-bad` uses for AWS.*
- [ ] Both `google` and `google-beta` blocks present with different regions resolve
      independently.
- [ ] `count`-indexed cross-resource references —
      `google_cloud_run_v2_service.run_default[count.index].name` read from inside another
      resource's nested block — produce one reference per index, not one unresolved string.
- [ ] `zone = "us-central1-a"` sets both region (`us-central1`, derived) and zone.
- [ ] `region = "us-central1"` sets region with no zone.
- [ ] `location` resolves for both spellings: a region (`us-central1`, Cloud Run, GKE regional) and
      a multi-region (`US`, `EU`, for GCS and Firestore) — the multi-region case must not be
      mistaken for a region key in the `cross_region` distance table.
- [ ] Precedence when more than one is present: explicit `zone` > explicit `region` /
      `location` > provider region. Assert the order, not just that something resolved.
- [ ] `--region` / `parsers.terraform.region` still acts as the fallback and still emits
      `region not resolved` when nothing else provides one.
- [ ] A GCP project with no provider block at all (`gcp-glb-mig-backend`) parses, taking regions
      from resource-level `region` attributes.

### 3.2 Normaliser layer

- [ ] Every GCP type in the table maps to the expected `(kind, subtype)` — one parametrised test
      over the table, in the shape of `tests/test_normaliser_types.py`.
- [ ] Placement per node: region, zone, and network/VPC, asserted together as a tuple the way
      `tests/test_example_classic_web.py` does
      (`(node.kind, node.subtype, node.placement.region, node.placement.az)`).
- [ ] The `internet` EXTERNAL node exists with **exactly** the id `internet` — the same constant
      the AWS normaliser uses, because `scenarios.yaml` files and the traversal both name it
      literally.
- [ ] `internet` has an INVOKE edge with `rule == "normaliser"` into **every** GATEWAY, LB and
      CDN node — mirroring `test_internet_enters_at_the_load_balancer`. Assert the count, so a
      new entry-kind subtype cannot be added without a matching edge.
- [ ] **Both halves of every global/regional twin** map, and map to the same `(kind, subtype)`:
      `url_map` / `region_url_map`, `backend_service` / `region_backend_service`,
      `target_http_proxy` / `region_target_http_proxy`, `target_https_proxy` /
      `region_target_https_proxy`, `health_check` / `region_health_check`,
      `global_forwarding_rule` / `forwarding_rule`, `global_address` / `address`,
      `network_endpoint_group` / `region_network_endpoint_group`. *`gcp-lb-regional` is the
      fixture that fails if a twin is missing; this test is the one that says which.*
- [ ] The ignore list drops GCP glue silently and is asserted explicitly: `google_project_service`,
      `google_service_account`, `google_project_iam_*` (read by inference from raw, not shown as
      nodes), `google_compute_firewall`, `google_compute_router*`, `google_dns_*`,
      `google_storage_bucket_object`, `google_secret_manager_*`, `google_kms_*`,
      `google_compute_ssl_certificate`, `google_compute_instance_template`, `tls_*`,
      `random_*`, `archive_file`.
- [ ] An unmapped `google_*` type becomes a NETWORK node with an `unknown type` warning — nothing
      ever vanishes.
- [ ] `google_compute_instance_group_manager` becomes a COMPUTE node carrying its
      `target_size`/`base_instance_name` as an instance count, the way
      `aws_autoscaling_group` carries `desired_capacity`.
- [ ] GKE node pools do not become separate nodes; the cluster is the node, with its node count
      as an attribute (mirrors `aws_ecs_task_definition` being followed but not shown).

### 3.3 Inference layer

One test file per rule, on the `tests/test_inference_ecs_asg.py` template: a tiny inline
Terraform string, `build_graph` on a `tmp_path`, then assertions. Each rule test asserts edge
**kind**, **rule name**, an **evidence substring**, and at least one **negative edge**.

- [ ] `gcp_lb_chain` — walks `forwarding_rule → target_*_proxy → url_map → backend_service →
      NEG/instance group → Cloud Run | GCE | GKE`, emitting ROUTE edges. Evidence names the
      intermediate resource, e.g. `url_map default routes to backend_service frontend_lb_backend`.
- [ ] `gcp_lb_chain` negative: the health check is not a request hop — no edge into
      `google_compute_health_check`. *Mirrors `assert graph.find_edge(QUEUE, dlq) is None`.*
- [ ] `gcp_lb_chain` negative: an unreferenced backend service produces no edge.
- [ ] `gcp_lb_chain` regional: the same edges are produced from the `region_*` types, with the
      same rule name.
- [ ] `gcp_lb_chain` multi-region: two `count`-indexed Cloud Run services behind one backend
      service produce **two** ROUTE edges, one per index.
- [ ] `gcp_lb_chain` redirect: a `url_map` with `default_url_redirect` and no backend produces no
      edge and no warning — it is a terminal, not a broken chain.
- [ ] `gcp_iam_binding` — a `google_project_iam_member` /
      `google_cloud_run_service_iam_member` / `google_pubsub_topic_iam_member` granting a service
      account a role produces the edge from the resource that *uses* that service account.
      Evidence names the role and the member.
- [ ] `gcp_iam_binding` READ vs WRITE: a viewer/reader role produces READ ops; an editor/writer
      role produces `[READ, WRITE]`. *Mirrors the AWS `iam_policy` rule's action split.*
- [ ] `gcp_iam_binding` negative: a role with no data-plane meaning (`roles/viewer` on logging,
      `roles/iam.serviceAccountUser`) produces no edge.
- [ ] `gcp_iam_binding` workload identity: the
      `serviceAccount:….svc.id.goog[<ns>/<ksa>]` member form parses and binds to the GKE
      cluster, not to a literal node named after the string. *`gcp-gke-multitenant` is the
      fixture.*
- [ ] `gcp_eventarc` — `google_eventarc_trigger` produces an INVOKE edge from its
      `matching_criteria` source to its `destination` (Cloud Run service, Cloud Run job, or
      Workflows). Evidence names the event type.
- [ ] `gcp_eventarc` negative: the trigger resource itself is not a node in the graph.
- [ ] `gcp_pubsub_push` — a `google_pubsub_subscription` with a `push_config.push_endpoint`
      pointing at a Cloud Run service produces a CONSUME edge topic → service. Evidence names the
      subscription.
- [ ] `gcp_pubsub_push` pull half: a publisher's IAM grant on a topic produces a PUBLISH edge
      into the topic, and the subscriber produces CONSUME out of it — never a
      publisher→topic→publisher loop. *Mirrors the order-queue assertion that a
      `sqs:ReceiveMessage` grant is a consumer, not a publisher.*
- [ ] `gcp_pubsub_push` negative: a dead-letter topic reference is not a request hop.
- [ ] `gcp_workflows` — a `google_workflows_workflow` whose `source_contents` YAML calls a Cloud
      Run URL or a Cloud Functions URL produces INVOKE edges workflow → target. Evidence names
      the step.
- [ ] `gcp_workflows` negative: a workflow step calling an external HTTPS URL that matches no
      node in the graph produces no edge and no crash — an unresolvable target is at most a
      warning.
- [ ] `gcp_workflows` on unparseable `source_contents` (a `templatefile()` that did not resolve)
      emits a named warning, never an exception.
- [ ] Each new rule name is added to `inference.rules` in `DEFAULTS`
      (`iacsim/core/config.py`) — and therefore to `examples/iacsim.yaml`, see §3.6.
- [ ] `iacsim plugins` lists every new rule. *The plug-and-play rule in `CONTRIBUTING.md`: new
      behaviour is a registration, and a registration that does not appear in `plugins` is not
      one.*
- [ ] Rule merge: where two rules produce the same edge (an IAM binding *and* an LB chain), the
      merged edge's `rule` is `a+b`. *Mirrors `tests/test_inference_merge.py` and the
      `rules(pub) == {"env_var", "iam_policy"}` assertion in the order-queue test.*

### 3.4 Latency layer

Every timing assertion recomputes the expected total from `default_profile` values. No magic
numbers.

- [ ] `gcp-web`'s `page_load` total equals a formula built from
      `p.distance["internet_to_edge"]`, `p.processing["gcp_lb"]["defaults"]["route"]`,
      `p.processing["cloud_run"]["defaults"]`, and `p.processing["cloud_sql"]["defaults"]["read"]`
      — assembled the way `test_place_order_is_the_synchronous_half` assembles the order-queue
      total.
- [ ] `r.total_ms == pytest.approx(sum(h.latency_ms for h in r.hops))` for every GCP scenario.
- [ ] **The LB chain's internal hops are 0 ms.** `forwarding_rule → proxy → url_map →
      backend_service` are all one Google front-end; only the entry hop and the hop into the
      backend cost anything. Assert the intermediate hops' `breakdown` is empty, the way
      `test_page_load_hops_are_priced_by_the_contract` asserts `back.breakdown == {}`.
- [ ] The hop that *does* cost is charged exactly once: `internet → forwarding_rule` charges
      `internet_to_edge` plus the LB's `route`, and no other chain hop adds either.
- [ ] Cold start fires for `cloud_run`: the `cold_start` rule must not be hard-coded to
      `dst.subtype != "lambda"`. Assert `breakdown["cold_start"] == cold * cold_prob` from the
      profile.
- [ ] Cold start fires for `cloud_functions` on the same terms.
- [ ] Cold start does **not** fire for `gce`, `gke`, `cloud_sql`, `memorystore`, `firestore`.
- [ ] A one-way PUBLISH into Pub/Sub charges distance once, not doubled. *Mirrors the
      `publish.breakdown["distance"] == same_region_unknown_az` assertion in the order-queue
      test.*
- [ ] A CONSUME hop out of Pub/Sub charges the source's `consume` plus the destination's own
      invoke cost — the source-side pricing case in `processing.py`.
- [ ] `gcp-web` vs `gcp-web-bad`: the delta equals
      `2 × queries × (cross_region[<sorted pair>] − same_region_unknown_az)`, read from the
      profile.
- [ ] GCP inter-region pairs exist in `latency/defaults.yaml`'s `distance.cross_region` map, with
      keys in the same sorted `a/b` form. At minimum the pairs the fixtures use:
      `europe-west1/us-central1`, `asia-northeast1/us-central1`, `europe-west1/us-east1`.
- [ ] An AWS/GCP mixed pair (`us-east-1/us-central1`) falls back to `cross_region_default` with a
      named warning rather than a `KeyError`.
- [ ] `capacity` blocks exist for every GCP subtype the `load` walker can reach, or the walker
      degrades with a named warning.

### 3.5 End-to-end, per fixture

Driven by the existing auto-discovery in `tests/test_real_world.py` — `_fixtures()` globs
`examples/real-world/*/`, so all seven are picked up the moment they land on disk.

- [ ] `test_graph_builds_and_every_warning_is_named` passes for all seven (this is automatic once
      `MIN_NODES` has entries).
- [ ] `test_run_exits_zero` passes for all seven, writing `.iacsim/report.json`.
- [ ] `gcp-ntier-serverless-web` has **zero** warnings and the full five-node chain is present as
      edges — the flagship's own named test, in the style of
      `test_ecs_alb_routes_to_the_service_and_sees_the_asg`.
- [ ] `gcp-glb-mig-backend` produces the minimal chain with zero warnings.
- [ ] `gcp-cloudrun-multiregion-glb` produces two Cloud Run nodes in two different regions, and
      both are reachable from the single global forwarding rule.
- [ ] `gcp-lb-regional` produces the same edge shape as `gcp-glb-mig-backend` from the `region_*`
      types.
- [ ] `gcp-gke-multitenant` names the `kubernetes` provider in a warning or handles it silently —
      whichever, the behaviour is pinned by a test, not left to chance.
- [ ] Proposed `MIN_NODES` values, each confirmed against real `iacsim graph` output before the
      test is committed (these are lower bounds to be tightened, not guesses to be trusted):
      `gcp-ntier-serverless-web: 8`, `gcp-glb-mig-backend: 3`,
      `gcp-cloudrun-multiregion-glb: 5`, `gcp-functions-firestore-pubsub: 2`,
      `gcp-gke-multitenant: 3`, `gcp-eventarc-workflows-run: 4`, `gcp-lb-regional: 3`.

### 3.6 Repo hygiene — these break loudly if forgotten

- [ ] **Every new fixture directory is added to `MIN_NODES` in `tests/test_real_world.py`.**
      `_fixtures()` auto-discovers `examples/real-world/*/`, and the test body does
      `MIN_NODES[fixture.name]` — a fixture that is on disk but not in the dict fails with a bare
      `KeyError`, in a test that never mentions the new fixture by name. This is the single most
      confusing failure mode in the repo.
- [ ] A `conftest.py` fixture **pair** per hand-written example — `gcp_web` (graph) and
      `gcp_web_run` (run), both `scope="session"`, plus the same for `gcp_web_bad`. Without the
      run half, timing tests re-parse per test and the suite slows measurably.
- [ ] New GCP lines added to the `examples` target in the `Makefile`: at minimum
      `graph examples/gcp-web`, `graph examples/gcp-web-bad`, `run examples/gcp-web -o json`,
      `diff examples/gcp-web examples/gcp-web-bad -o json`, and one
      `graph examples/real-world/gcp-*` smoke.
- [ ] `examples/iacsim.yaml` still equals `DEFAULTS` after the new `inference.rules` entries are
      added — `tests/test_config_docs.py` asserts exact equality of the parsed YAML and the dict.
      Adding a rule to `DEFAULTS` without editing the YAML fails that test.
- [ ] Every `path:line` citation added to `DECISIONS.md` or `CODE_FLOW.md` for M11 resolves —
      `tests/test_docs_refs.py` checks that the file exists and the line number is within its
      length. Line numbers move; re-check after the last refactor commit, not before.
- [ ] `DECISIONS.md` gains a row for the GCP decisions (provider-owned tables, `google-beta`
      handling, multi-region location keys) — `CONTRIBUTING.md` requires it.
- [ ] `TIMELINE.md` M11 row updated.
- [ ] Coverage stays at or above **85 %** (`make check` → `--cov-fail-under=85`). New table
      entries are cheap to cover; new rules are not — each rule needs its own test file or
      coverage drops.
- [ ] `make ci` (= `make check` + `make examples`) green on Python 3.12 and 3.13.
- [ ] `ruff check iacsim tests` clean at line length 120.

---

## 4. Expected-warnings contract

The repo's rule: unfamiliar Terraform must not crash, and **every warning must be a named one**.
`tests/test_real_world.py` enforces it by asserting that every warning contains one of the
`KNOWN_WARNINGS` phrases.

The GCP corpus is designed to need **no new allowlist phrase**. Each fixture emits either nothing
or a warning already in the list.

| fixture | warnings allowed | matching `KNOWN_WARNINGS` phrase | why |
|---|---|---|---|
| `gcp-ntier-serverless-web` | none | — | self-contained, no modules, no data sources; the fixture that proves the clean path |
| `gcp-glb-mig-backend` | none | — | literal regions, no provider block, no variables |
| `gcp-cloudrun-multiregion-glb` | none | — | `count` and `google-beta` must both resolve; a warning here is a parser bug, not a fixture property |
| `gcp-functions-firestore-pubsub` | `data.* sources are not evaluated` | `data.* sources are not evaluated` | five `data.archive_file` blocks point outside the vendored directory |
| `gcp-gke-multitenant` | `data.* sources are not evaluated` | same | `data.google_client_config` feeds the second provider |
| `gcp-eventarc-workflows-run` | none | — | if the `gcp_workflows` rule cannot parse `source_contents`, that is a `not evaluated` warning and a rule bug to fix, not a fixture to allowlist |
| `gcp-lb-regional` | none | — | |

- [ ] `KNOWN_WARNINGS` is **unchanged** after M11. If a new phrase is genuinely needed, it is a
      design decision: record it in `DECISIONS.md` with the reason, do not slip it into the tuple.
- [ ] No fixture emits `region unknown` — that phrase is allowlisted for AWS fixtures that
      legitimately need `--region`, and a GCP fixture hitting it means §3.0's region guard-rail
      has failed.
- [ ] No fixture emits `unknown type google_*` — every type the corpus contains is either mapped
      or deliberately ignored. A hit here names a `TYPE_MAP` gap; fix the table, do not accept
      the warning.
- [ ] Manual-run corpus only: `net-lb-app-ext` may emit `dynamic` and `not evaluated` warnings.
      It is not in `examples/real-world/`, so it is not subject to the contract — but the warnings
      it produces are recorded in the M11 notes so a change in count is visible.
