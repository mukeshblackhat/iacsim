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

Run any of them:

```
iacsim graph examples/real-world/ecs-alb --region us-east-1
iacsim run   examples/real-world/two-tier
```

## Manual-run corpus (not vendored — size or licence)

Fetch read-only into a scratch directory, then run `graph`, `validate`, `run`:

| repo | why | what to expect |
|---|---|---|
| `terraform-aws-modules/terraform-aws-lambda/examples/complete` (Apache-2.0) | registry `module` sources, `for_each` with `zipmap`, `data.*` | 18 nodes; one `remote source` warning per registry module; `unknown type aws_lambda_layer_version` |
| `ministryofjustice/modernisation-platform-environments/terraform/environments/apex` (MIT) | 26 files, `terraform.workspace`, provider aliases, `dynamic` blocks, `*.auto.tfvars.json`, local `modules/` | ~60 nodes; `--workspace development` changes the account lookups; warnings for `trimprefix`/`substr` on unresolved values and `count` that depends on `data.*` |
| `hetznercloud/terraform-provider-hcloud/examples/resources/hcloud_server` (MPL-2.0) | a non-AWS provider | every resource is an `unknown type … kept as a network node` warning; `run` exits 0 with no scenarios — the generic/server normaliser is milestone M9 |

```
git clone --depth 1 --filter=blob:none --sparse https://github.com/terraform-aws-modules/terraform-aws-lambda /tmp/tal
cd /tmp/tal && git sparse-checkout set examples/complete
iacsim validate examples/complete
```
