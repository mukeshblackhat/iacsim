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

### M2 done (same day, parallel agent)
- **Expected-value walker** (`simulator/walkers/expected_value.py`): walks
  declared steps, charging one-way `distance` ×2 for synchronous hops and
  processing / cold start once. Picks the edge to charge as: direct → response
  leg (destination already visited; network not re-charged) → *via caller*
  (most recent earlier node with an edge to the target — this is how "api
  reads table A then table B" and every Step Functions invocation get
  attributed to the real caller) → synthetic estimate + warning. `parallel` =
  max(branches) with off-path hops marked `·`; `fanout` costed once; `wait_ms`
  steps; `internet → entry` charged automatically; a repeated node = repeat
  call. `Result.shape` records Layer A3 (hop count, parallel savings, fan-out
  copies, waits).
- **Scenario steps** gained `op:` (re-price a hop as write / publish …) and
  `wait_ms:`; the pipeline hands the walker the same pricing function the
  graph was costed with (`make_pricer`), so synthetic hops use identical rules.
- **Inferred scenarios** (`scenarios/inferred.py`): from every `internet →`
  entry point, DFS that stops at datastores / queues and *replays* a state
  machine's `attrs["workflow"]` (Task, Map, Parallel, Choice → branch with the
  most tasks, Wait). One path per (entry, leaf subtype), max 5 — Foosh yields
  3, not 176.
- **Distance rule** fix: an unknown AZ on either side (ALB, DynamoDB, Lambda)
  is `same_region_unknown_az` (0.5 ms), not cross-AZ.
- **Text report** now prints the hop table in path order with breakdown and
  evidence, the shape line, then analyzer sections; `validate` works.
- **Numbers** (`iacsim run`, defaults profile):
  | Example / scenario | total |
  |---|---|
  | classic-web `page_load` | 57.2 ms |
  | classic-web-bad `page_load` | 356.0 ms — **+298.8 ms = 2 DB round-trips × 2 legs × (75 − 0.3)** |
  | foosh `start_workflow` | 151.0 ms |
  | foosh `run_workflow_3_nodes` | 184.0 ms (parallel group saves 92 ms; fan-out ×3 costed once) |
  | foosh `poll_status` | 106.0 ms |
  | foosh inferred `…/StateMachine` | 232.0 ms |
- **Tests**: 45 passing (walker unit tests on a hand-built graph, inferred
  scenario tests, per-example `run` assertions incl. the exact cross-region delta).
- **Known limitations**: the orchestrator's own transition cost is charged only
  on the hop *into* the state machine, not per Task; Choice branch selection
  is "most tasks", not the ASL `Default`; declared steps that name a node with
  no inferred edge from any earlier node are estimated as a synthetic invoke
  (warned, never fails).

### Next session
- Start **M3**: analyzers on real output — add a `shape` category to
  `per_category` from `Result.shape`, make `critical_path` report slack for
  off-path hops, tighten the text report (top-N per analyzer, hide duplicates
  of the hop table), version `report.json`.

---

## Milestones

| # | Goal | Deliverable / acceptance | Status | Date |
|---|---|---|---|---|
| M0 | Agree the plan, lay the foundation | SPEC.md agreed; skeleton with every extension point registered; 3 example stacks | ✅ | 2026-09-04 |
| M1 | Terraform → graph | `iacsim graph examples/classic-web` → nodes + edges with evidence in `graph.json`; modules, `for_each`, `templatefile` refs resolved; foosh-serverless parses with Step Functions edges in call order | ✅ | 2026-09-04 |
| M2 | Simulate one request | `iacsim run examples/classic-web` prints total ms + per-hop table; expected-value walker handles sequential / parallel / fanout; inferred scenarios for entry points with no `scenarios.yaml` | ✅ | 2026-09-04 |
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
