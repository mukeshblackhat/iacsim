# Timeline

Running log of what is done, what is in progress, and what is next. One entry
per working session; milestone table at the bottom is the single source of
truth for status. Update this file at the end of every session.

Legend: ✅ done · 🔨 in progress · ⏳ planned · 🧊 parked

---

## 2026-09-04 — Day 1: problem → spec → skeleton

### Done
- **Problem statement** written (`problem statment.md`) with a plain-language
  explanation section.
- **Found Foosh's real infra**: `~/Foosh/async-workflows/infrastructure/` is
  AWS CDK (Python), not Terraform. Synthesized CloudFormation lives in
  `cdk.out/*.template.json` — 69 resources: 14 Lambdas, 8 tables, 1 state
  machine, 1 bucket, API Gateway. This became the reason for the CloudFormation
  adapter (M5).
- **SPEC.md** written and all 7 design questions resolved one by one:
  | # | Decision |
  |---|---|
  | Q1 | Python 3.12 (`python-hcl2`, `networkx`, `typer`) |
  | Q2 | CLI + JSON now; static web viewer later (M6) |
  | Q3 | Infer request paths from IaC evidence; `scenarios.yaml` overrides |
  | Q4 | Built-in defaults + `--profile`; schema designed so `calibrate` (M7) slots in |
  | Q5 | Terraform primary + CloudFormation adapter (M5) |
  | Q6 | Deterministic walker (M2), Monte-Carlo (M6) |
  | Q7 | `classic-web` first, then `foosh-serverless` |
  | — | **Everything plug-and-play**: interface + registry + config key per choice |
- **Package skeleton** (`iacsim/`) — one folder per pipeline stage, one file
  per implementation. All 10 extension points have an ABC, a registry and at
  least one built-in. `iacsim plugins` lists them; 6 tests pass.
  - Real now: registry/plugin discovery, IR models, config layering, profile
    merge with `per_resource`, 3 cost rules, 4 analyzers, 2 reporters,
    `scenarios.yaml` loader, CLI (6 commands wired).
  - Stubbed with docstring plan: Terraform parser, AWS normaliser, 7 inference
    rules, walkers, inferred scenarios, diff, CloudFormation parser, calibrate.
- **Terraform examples on shared modules** (`examples/modules/`: network,
  load_balancer, compute, database, lambda_function, dynamodb_table):
  - `classic-web` — ALB → EC2 ×2 → RDS, one region.
  - `classic-web-bad` — same modules, RDS in eu-west-1 over VPC peering.
  - `foosh-serverless` — Terraform twin of the Foosh stack: 16 Lambdas with
    real memory/timeout, 10 tables with real keys/GSIs, API GW `{proxy+}`,
    condensed Step Functions ASL with the real shape, 3 scenarios.
  - All 32 `.tf` files parse with `python-hcl2`. (`terraform validate` not run —
    CLI not installed.)

### M1 done (same day, parallel agent)
- **Terraform parser** (`iacsim/parsers/terraform/`): `hcl_expr.py` (expression
  tokenizer + parser), `evaluator.py` (lazy evaluation with `Ref` placeholders,
  ~45 built-in functions, for-expressions, conditionals, `try`/`can`),
  `loader.py` (module tree, `for_each`/`count` on resources *and* modules,
  `dynamic` blocks, `templatefile()`, `jsonencode()` kept structured, provider
  aliases → `RawResource.region`). All three examples parse with **zero
  warnings**; unresolvable HCL becomes a warning, never a crash.
- **Reference convention** (`iacsim/core/refs.py`): attributes that mention
  other resources hold `"${address.attr}"` placeholders; `references_in()` /
  `addresses_in()` turn them back into addresses for the inference rules.
- **AWS normaliser**: TYPE_MAP → kind/subtype, placement from provider region +
  `availability_zone` / `subnet_id` / `subnets` / RDS subnet group; glue types
  dropped silently; adds the `internet` EXTERNAL node with an edge into every
  gateway / LB / CDN.
- **8 inference rules** (7 planned + `vpc_peering`): step_functions (walks the
  ASL incl. Map / Parallel / Choice / Wait and stores the structure on the
  orchestrator node as `attrs["workflow"]`), event_source_mapping,
  lambda_permission, api_gateway_integration, target_group, env_var
  (Lambda env, EC2 user_data / templatefile vars), iam_policy (structured or
  JSON policies → READ/WRITE/PUBLISH/INVOKE), vpc_peering.
- **Results**: `iacsim graph` → classic-web 10 nodes / 5 edges;
  classic-web-bad 16 / 6 (RDS in eu-west-1 + PEER edge); foosh-serverless
  30 / 194 (176 env_var reads, 12 Step Functions invokes, 3 IAM invokes,
  API GW → api Lambda, api Lambda → state machine).
- **Tests**: 29 passing — per-example edge assertions + parser unit tests
  (for_each / count / module addresses / provider alias / jsonencode / warnings).
- **Known limitations**: remote module sources, `data` sources, `%{ }` template
  directives, splat on unresolved values, and provider-computed functions
  (`cidrsubnet`, `file`) are left as `${...}` text with a warning. One edge per
  (src, dst) pair — env_var reports datastore access as READ; a WRITE-vs-READ
  distinction per scenario step is an M2 concern.

### Flagged
- `~/Foosh/async-workflows/infrastructure/config/environments/staging.json`
  contains plaintext Razorpay + FAL API keys. Not copied anywhere. Should be
  rotated and moved to Secrets Manager.

### Next session
- Start **M2**: expected-value walker + inferred scenarios →
  `iacsim run examples/classic-web` prints total ms + per-hop table.

---

## Milestones

| # | Goal | Deliverable / acceptance | Status | Date |
|---|---|---|---|---|
| M0 | Agree the plan, lay the foundation | SPEC.md agreed; skeleton with every extension point registered; 3 example stacks | ✅ | 2026-09-04 |
| M1 | Terraform → graph | `iacsim graph examples/classic-web` → nodes + edges with evidence in `graph.json`; modules, `for_each`, `templatefile` refs resolved; foosh-serverless parses with Step Functions edges in call order | ✅ | 2026-09-04 |
| M2 | Simulate one request | `iacsim run examples/classic-web` prints total ms + per-hop table; expected-value walker handles sequential / parallel / fanout; inferred scenarios for entry points with no `scenarios.yaml` | ⏳ | |
| M3 | Say *why* it's slow | All 4 analyzers on real output; text report ranks hops, nodes, categories; `report.json` versioned | ⏳ | |
| M4 | Compare designs | `iacsim diff classic-web classic-web-bad` shows the cross-region DB as the delta; per-hop before/after | ⏳ | |
| M5 | Read CloudFormation / CDK output | `iacsim graph ~/Foosh/.../cdk.out` produces the same graph as `foosh-serverless` (correctness check) | ⏳ | |
| M6 | Tail latency + polish | `--walker monte_carlo --samples N` → p50/p95/p99; static graph viewer reading `graph.json`; validation, docs, tests | ⏳ | |
| M7 | Real numbers | `iacsim calibrate` pulls Lambda Duration / InitDuration, DynamoDB latency from CloudWatch into a profile YAML; run against Foosh staging | ⏳ | |

Estimated: M1–M3 ≈ 1 week (demo-able), M0–M6 ≈ 2 weeks, M7 additive.

## Open items / ideas parked

- 🧊 Reading CDK Python source directly (instead of `cdk.out`) — deferred, `cdk synth` output is enough.
- 🧊 Non-AWS providers — graph is neutral, only the normaliser is AWS-specific.
- 🧊 Throughput / queueing under load — out of scope for v1, single-request latency only.
- 🧊 X-Ray traces as an inference *validator* (confirm/deny inferred edges) — nice M7 extension.
