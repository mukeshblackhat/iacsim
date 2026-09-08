# Real-world Terraform fixtures

Public Terraform projects, vendored **unmodified** (only the `.tf` files and the data files
they reference) so `tests/test_real_world.py` can prove that unfamiliar, real Terraform
parses, builds a graph and runs without crashing — and that every warning it produces is a
*named* one. Each directory has an `ATTRIBUTION.md` with the source URL, commit and licence.

| fixture | what it exercises | expected warnings |
|---|---|---|
| `serverless-apigw-lambda-dynamodb` | API Gateway v2 → Lambda → DynamoDB, `data.archive_file`, inline IAM JSON | `data.* sources are not evaluated` |
| `serverless-pipes-sqs-stepfunctions` | SQS → EventBridge Pipes → Step Functions (`templatefile` ASL) | `unknown type aws_pipes_pipe` (Pipes edges are on the roadmap) |
| `ecs-alb` | ALB → ECS service on an EC2 Auto Scaling Group, `count` subnets, `[*]` splat, `templatefile` task definition, provider region from a variable with no default | `region not resolved` (pass `--region`), `data.*` |
| `two-tier` | classic ELB → EC2 with `provisioner`/`connection` blocks, `terraform.template.tfvars` | none |
| `eks-cluster` | registry modules only (`terraform-aws-modules/vpc`, `eks`, `iam`) — the graph is empty until `terraform init` has populated `.terraform/modules/modules.json` | `remote source … run terraform init` |
| `gcp-ntier-serverless-web` | two Cloud Run tiers → Cloud SQL (PSC) + Memorystore Redis behind the full HTTPS LB chain (serverless NEG → backend service → URL map → HTTPS proxy → global forwarding rule), Cloud Armor, DNS, VPC/PSA, `tls_*` resources; one 824-line file, no `module`/`data` blocks | none |
| `gcp-glb-mig-backend` | the canonical minimal global HTTP LB chain onto a MIG — literal regions, no provider block, no variables; the readable "control" when the LB-chain rule breaks | none |
| `gcp-cloudrun-multiregion-glb` | `count`-fanned Cloud Run services + serverless NEGs across two regions behind one global LB, HTTP→HTTPS redirect `url_map`, `[count.index]` cross-references; every resource sets `provider = google-beta` | none |
| `gcp-functions-firestore-pubsub` | five Cloud Functions v2 + Firestore + Pub/Sub + Cloud Scheduler, GCS bucket objects named from `data.archive_file` outputs that point outside the fixture, a `gcs` backend | `data.* sources are not evaluated`, `not evaluated (unknown function basename())`, `state machine definition could not be read` (Scheduler jobs) |
| `gcp-gke-multitenant` | GKE with node pools and workload identity, a second `kubernetes` provider configured from `data.google_client_config`, Cloud SQL beside the cluster | `data.* sources are not evaluated` |
| `gcp-eventarc-workflows-run` | Eventarc trigger → Workflows → Cloud Run job with service-account IAM bindings as the only edge evidence; inline Workflows YAML with `sys.get_env(...)` | `data.* sources are not evaluated`, `not evaluated (trailing tokens …)`, `state machine definition could not be read` (the Workflows body) |
| `gcp-lb-regional` | the regional twin of every global LB type — `region_url_map`, `region_target_http_proxy`, `region_backend_service`, `region_health_check`, a plain `forwarding_rule` — plus a proxy-only subnet; catches a `TYPE_MAP` that only knows the global spellings | none |

Run any of them:

```
iacsim graph examples/real-world/ecs-alb --region us-east-1
iacsim run   examples/real-world/two-tier
iacsim graph examples/real-world/gcp-glb-mig-backend --region us-central1
```

The `gcp-*` fixtures come from `terraform-google-modules/terraform-docs-samples` (five), `google/skills`
and `GoogleCloudPlatform/cloud-release-chat-bot` (one each), all Apache-2.0 — each `ATTRIBUTION.md` names
the pinned commit. Until GCP auto-detection lands, they need `provider: gcp` in an `iacsim.yaml` (or the
equivalent config override) to graph as anything other than `unknown type` network nodes.

## Manual-run corpus (not vendored — size or licence)

Fetch read-only into a scratch directory, then run `graph`, `validate`, `run`:

| repo | why | what to expect |
|---|---|---|
| `terraform-aws-modules/terraform-aws-lambda/examples/complete` (Apache-2.0) | registry `module` sources, `for_each` with `zipmap`, `data.*` | 18 nodes; one `remote source` warning per registry module; `unknown type aws_lambda_layer_version` |
| `ministryofjustice/modernisation-platform-environments/terraform/environments/apex` (MIT) | 26 files, `terraform.workspace`, provider aliases, `dynamic` blocks, `*.auto.tfvars.json`, local `modules/` | ~60 nodes; `--workspace development` changes the account lookups; warnings for `trimprefix`/`substr` on unresolved values and `count` that depends on `data.*` |
| `hetznercloud/terraform-provider-hcloud/examples/resources/hcloud_server` (MPL-2.0) | a non-AWS provider | every resource is an `unknown type … kept as a network node` warning; `run` exits 0 with no scenarios — the generic/server normaliser is milestone M9 |
| `terraform-google-modules/terraform-google-lb-http/examples/cloudrun` (Apache-2.0) | the GCP analogue of the `eks-cluster` registry-module fixture: proves the `remote source … run terraform init` path is provider-neutral | empty graph until `terraform init`; one `remote source` warning per registry module |
| `GoogleCloudPlatform/cloud-foundation-fabric/modules/net-lb-app-ext` (Apache-2.0) | 13 files, 15 LB resource types; `urlmap.tf` alone is 38 KB of `dynamic` / `for_each` / `optional()` — the hardest HCL-evaluation test in the GCP ecosystem | `dynamic` and `not evaluated` warnings; the value is that nothing crashes and no warning is unnamed |

```
git clone --depth 1 --filter=blob:none --sparse https://github.com/terraform-aws-modules/terraform-aws-lambda /tmp/tal
cd /tmp/tal && git sparse-checkout set examples/complete
iacsim validate examples/complete
```

```
git clone --depth 1 --filter=blob:none --sparse https://github.com/terraform-google-modules/terraform-google-lb-http /tmp/tglh
cd /tmp/tglh && git sparse-checkout set examples/cloudrun
iacsim validate examples/cloudrun --region us-central1

git clone --depth 1 --filter=blob:none --sparse https://github.com/GoogleCloudPlatform/cloud-foundation-fabric /tmp/cff
cd /tmp/cff && git sparse-checkout set modules/net-lb-app-ext
iacsim graph modules/net-lb-app-ext --region us-central1
```
