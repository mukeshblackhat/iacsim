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

### M3 done (same day, parallel agent)
- **`per_category`** is now the A1 / A2 / A3 view from SPEC §1a: additive
  lines `distance` (A1), `processing` + `cold_start` (A2, with a `service`
  roll-up), `wait` (A3) — shares sum to 100 % over counted (critical-path)
  hops — plus informational A3 lines `parallel_savings`, `hops`, `fanout`.
  Every detail says what would change it ("2 cross-region hop(s):
  database.db_instance (eu-west-1) ← classic-web-us-east-1a (us-east-1) —
  co-locate to remove this").
- **`per_node`** merges repeat calls ("2 call(s) into rds — dominated by
  distance"); **`critical_path`** emits nothing without a parallel group and
  otherwise lists each branch with its slack (ties are called out);
  **`per_hop`** unchanged but counted hops only.
- **`recommendations`** (new analyzer, on by default): rule-based, 1–5 lines,
  each with an estimated saving and the hops it was derived from — co-locate a
  cross-region datastore, batch repeated calls, provisioned concurrency when
  cold start > 10 %, run independent reads in parallel, replace Wait states.
  What it prints:
  - classic-web-bad `page_load`: **"co-locate database.db_instance with
    classic-web-us-east-1a — saves ~296 ms (83 %)"** (within 1 % of the
    298.8 ms diff) and "batch the 2 calls … saves ~155 ms".
  - foosh `run_workflow_3_nodes`: **"provisioned concurrency on
    async-workflow-parser-staging, …-input-preparer-staging,
    …-image-generation-staging — saves ~100 ms (54 %)"**; foosh
    `start_workflow` also gets "run the 4 reads from async-workflow-api-staging
    in parallel — saves ~15 ms".
- **Reports** share one `Brief` structure (`reporter/_brief.py`): header →
  *Where the time goes* (layer bar) → *Top bottlenecks* → *Recommendations* →
  *Hops* (top-N by ms in path order; `--all-hops` for everything) → *Critical
  path* (parallel only) → warnings. `text` draws it with `rich` (colour on a
  TTY, plain otherwise); new **`markdown`** reporter for PR comments;
  `-o/--output` picks reporters.
- **`report.json` schema 2** (`reporter/json_.py` docstring is the contract):
  `generated_at`, `profile.sources`, `graph`, and per scenario `hops` (with
  `label`, `group`) and `findings` grouped by analyzer. `Finding` gained
  `refs`, `layer`, `additive`; `HopResult` gained `group`
  ("parallel1/branch2", set by the walker).
- **Tests**: 69 passing (analyzers on a hand-built Result, every reporter on
  every example, json contract, classic-web-bad's top recommendation ≈ the
  measured delta).
- **Known limitations**: recommendation savings are first-order estimates
  (co-locate assumes ~1 ms cross-AZ per leg; batching assumes one call's cost
  per extra call); the parallelise rule only sees adjacent reads from one
  caller and cannot know about data dependencies the scenario doesn't state;
  `critical_path` needs the walker's `group` labels, so the Monte-Carlo walker
  (M6) must set them too.

### M4 done (same day, parallel agent)
- **`iacsim diff before after`** runs both pipelines with the *same* profile
  (before-side config wins) and aligns: graph nodes by id (`--align-by label`
  for Terraform-vs-CloudFormation), edges by (src, dst, kind); scenarios by
  name; `per_category` / `per_node` / `recommendations` by subject; hops by
  label + occurrence index (2nd call to a table lines up with the 2nd call).
  |Δ| < 0.05 ms is "unchanged". One-sided scenarios are listed, not errors.
- **Report** (text via `rich`, `markdown`, `json` — all through
  `Reporter.render_diff`): *What changed in the infrastructure* (moved /
  added / removed nodes, edges) → per scenario: before / after / delta (red
  up, green down on a TTY) → *Where the time goes — shift* (A1/A2/A3 before →
  after, share shift) → *Hops that changed* (with breakdown `distance
  0.6→150`) → *Bottleneck shift* → *Recommendations* appeared / disappeared.
  Writes `<after>/.iacsim/diff.json` (`DiffReport.to_dict()`, documented in
  `reporter/json_.py`) and `diff.md`.
- **CI**: `--fail-on-regression 50ms|10%` exits 2 when any scenario total
  grows past the threshold; `--scenario NAME` restricts; exit 0 otherwise.
- What it prints for `classic-web → classic-web-bad` (condensed):
  ```
  What changed in the infrastructure
    moved   database.db_instance (region)   us-east-1 → eu-west-1
    moved   database.db_instance (az)       us-east-1a → eu-west-1a
    added   classic-web-db (vpc) + 4 subnets + aws_vpc_peering_connection.web_to_db
    edge added   network.vpc → classic-web-db (peer)

  scenario: page_load      before 57.2 ms   after 356.0 ms   delta +298.8 ms (+522%)
    A1 distance     42.2 → 341.0   +298.8   74% → 96%
    A2 processing   15.0 →  15.0      0.0   26% →  4%
    Hops that changed (2 of 5):
      3→3  classic-web-us-east-1a → database.db_instance   5.6 → 155.0  +149.4  distance 0.6→150
      4→4  classic-web-us-east-1a → database.db_instance   5.6 → 155.0  +149.4  distance 0.6→150
    Bottleneck shift: database.db_instance 11.2 → 310.0 (+298.8)
    Recommendations: appeared  co-locate database.db_instance with classic-web-us-east-1a  saves ~296 ms
  page_load: 57.2 → 356.0 ms (+298.8)
  ```
  `foosh-serverless` vs itself → "no latency change", exit 0.
- **Tests**: 96 passing (differ unit tests on hand-built Findings: label +
  occurrence alignment, added/removed, one-sided, zero diff, category
  arithmetic, threshold parsing, label-alignment across formats; example
  tests: exact 298.8 ms delta, exactly the two RDS hops, graph-level move +
  peer edge, co-locate recommendation appears, CLI exit codes 2 / 0).
- **Known limitations**: node alignment is by id, so renaming a resource
  shows as removed + added (use `--align-by label` when labels are stable);
  only `region` / `az` / `vpc` count as a move (subnet changes are not
  reported); hop alignment is by label, so a re-ordered path with the same
  hops shows as unchanged.

### M5 done (same day, parallel agent)
- **Fresh template**: `cdk synth` of `~/Foosh/async-workflows/infrastructure`
  worked (aws-cdk-lib in a scratch venv; the CDK CLI complained about a
  manifest-schema mismatch *after* the app had written the template). Copied
  to `examples/foosh-cfn/template.json` — 77 resources, 16 Lambdas, 10 tables,
  matching the Jul-2026 CDK source — with 11 secret env values → `REDACTED`
  and the account id → `123456789012` (asserted by a test). Nothing under
  `~/Foosh` was modified.
- **Adapter** (`iacsim/parsers/cloudformation/`): `template.py` (JSON, YAML
  with `!Ref`/`!GetAtt`/`!Sub`/… short tags, region guess from ARNs),
  `intrinsics.py` (Ref / GetAtt / Join / Sub / Select / If / Split / GetAZs /
  pseudo-params → the Terraform placeholder convention `${type.LogicalId.attr}`),
  `canonical.py` (one table: `AWS::X::Y` → `aws_x_y`, PascalCase → snake_case,
  per-type aliases like `TableName` → `name`, synthetic
  `aws_api_gateway_integration` / `aws_lb_target_group_attachment`, literal
  env-var names → placeholders). Addresses are `<terraform type>.<LogicalId>`,
  so the AWS normaliser and all 8 inference rules run **unchanged**.
  `Parser` now takes `**options` (`parsers.cloudformation.region` in
  `iacsim.yaml`, `--region` on `run`/`graph`); a template *file* is a valid
  target (config / scenarios / outputs live beside it).
- **Correctness check** (`iacsim diff examples/foosh-serverless examples/foosh-cfn
  --align-by label`): 30 nodes on both sides, same kind/subtype multiset,
  no warnings, zero moves. Every difference is enumerated in
  `tests/test_example_foosh_cfn.py`:
  | Difference | Count | Why |
  |---|---|---|
  | node labels | 4 | the twin picked its own names for `workflows` / `executions` tables, the outputs bucket and the html-template Lambda (real: `AsyncWorkflowsStaging`, `WorkflowExecutionsStagingSF`, `async-workflow-outputs-staging-new`, `async-workflow-html-template-processor-staging`) |
  | edge kind, 16 Lambdas × 3 tables | 48 | the twin gives every Lambda env vars for all 10 tables; the real stack passes 7 and reaches `PaymentIdempotency` / `PublishedApps` / `AppExecutions` through IAM only → env_var READ vs iam_policy WRITE |
  | Step Functions Task targets | 2 + 2 | the twin's condensed ASL has Tasks for lipsync / image-to-image and Pass states for inputs; the real one is the reverse — all 15 workers are reached either way |
  | state machine → tables | 2 | CDK grants the SFN role `dynamodb:*` on workflows / executions; the twin's role only invokes Lambdas |
  Everything else — 146 edges incl. internet → API, API GW → api Lambda,
  api → state machine, the 12 real Step Functions Task edges, all worker →
  table / bucket reads — is identical. `iacsim run examples/foosh-cfn` works
  with inferred scenarios (232 / 81 / 97 ms).
- **Tests**: 115 passing (+19: intrinsics → placeholders, YAML short tags,
  canonical types/attrs, synthetic resources, physical-name resolution,
  DefinitionString reassembly, region guess, redaction, and the
  enumerated-differences check above).
- **Known limitations**: Conditions are not evaluated (`Fn::If` takes the
  true branch, warned); `Fn::FindInMap` / `Fn::ImportValue` / `Fn::Cidr` are
  kept as `<name>` markers; literal-name resolution only looks at Lambda env
  vars (not ECS container definitions) and only at datastore / queue names;
  no AZ placement for CFN Lambdas (same as Terraform). Follow-up worth doing
  outside M5: rename the four resources in the Terraform twin to the real
  names so the label diff is empty.

### Next session
- Start **M6**: Monte-Carlo walker (`--walker monte_carlo --samples N` →
  p50/p95/p99, must set `HopResult.group` like the deterministic walker),
  static graph viewer reading `graph.json` / `report.json`, validation,
  docs, tests.

---

## Milestones

| # | Goal | Deliverable / acceptance | Status | Date |
|---|---|---|---|---|
| M0 | Agree the plan, lay the foundation | SPEC.md agreed; skeleton with every extension point registered; 3 example stacks | ✅ | 2026-09-04 |
| M1 | Terraform → graph | `iacsim graph examples/classic-web` → nodes + edges with evidence in `graph.json`; modules, `for_each`, `templatefile` refs resolved; foosh-serverless parses with Step Functions edges in call order | ✅ | 2026-09-04 |
| M2 | Simulate one request | `iacsim run examples/classic-web` prints total ms + per-hop table; expected-value walker handles sequential / parallel / fanout; inferred scenarios for entry points with no `scenarios.yaml` | ✅ | 2026-09-04 |
| M3 | Say *why* it's slow | All 4 analyzers on real output + `recommendations`; text/markdown report is a bottleneck brief (A1/A2/A3 bar, top nodes, suggestions with savings, hop table); `report.json` schema 2 | ✅ | 2026-09-04 |
| M4 | Compare designs | `iacsim diff classic-web classic-web-bad` shows the cross-region DB as the delta (graph-level move, A1 shift, the two RDS hops, co-locate recommendation); `--fail-on-regression` for CI | ✅ | 2026-09-04 |
| M5 | Read CloudFormation / CDK output | `iacsim graph examples/foosh-cfn` (real `cdk synth` output) produces the same graph as `foosh-serverless` — 30/30 nodes, every difference enumerated and explained in the test | ✅ | 2026-09-04 |
| M6 | Tail latency + polish | `--walker monte_carlo --samples N` → p50/p95/p99; static graph viewer reading `graph.json`; validation, docs, tests | ⏳ | |
| M7 | Real numbers | `iacsim calibrate` pulls Lambda Duration / InitDuration, DynamoDB latency from CloudWatch into a profile YAML; run against Foosh staging | ⏳ | |

Estimated: M1–M3 ≈ 1 week (demo-able), M0–M6 ≈ 2 weeks, M7 additive.

## Open items / ideas parked

- 🧊 Reading CDK Python source directly (instead of `cdk.out`) — deferred, `cdk synth` output is enough.
- 🧊 Non-AWS providers — graph is neutral, only the normaliser is AWS-specific.
- 🧊 Throughput / queueing under load — out of scope for v1, single-request latency only.
- 🧊 X-Ray traces as an inference *validator* (confirm/deny inferred edges) — nice M7 extension.
