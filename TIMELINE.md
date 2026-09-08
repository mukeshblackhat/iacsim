# Timeline

Running log of what is done, what is in progress, and what is next. One entry
per working session; milestone table at the bottom is the single source of
truth for status. Update this file at the end of every session.

Legend: ✅ done · 🔨 in progress · ⏳ planned · 🧊 parked

---

## 2026-09-04 — Day 1: problem → spec → skeleton

### Done
- **Problem statement** written (`problem-statement.md`) with a plain-language
  explanation section.
- **Found Foosh's real infra**: `~/Foosh/async-workflows/infrastructure/` is
  AWS CDK (Python), not Terraform. Synthesized CloudFormation lives in
  `cdk.out/*.template.json` — 69 resources: 14 Lambdas, 8 tables, 1 state
  machine, 1 bucket, API Gateway. This became the reason for the CloudFormation
  adapter (M5).
- **SPEC.md** written and all 7 design questions resolved one by one:
  | # | Decision |
  |---|---|
  | Q1 | Python 3.12 (`python-hcl2`, `typer`, `pyyaml`, `rich`; `networkx` planned, later dropped as unused) |
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

### M6 done (same day, parallel agent)
- **Shared traversal** (`simulator/traversal.py`): the walk is now planned
  once (edge selection, ×2 on sync distance, `op` overrides, parallel /
  fan-out / wait, group labels, shape) and *evaluated* by a backend —
  `ExpectedBackend` (the M2 numbers, unchanged) or Monte-Carlo. The two
  walkers cannot drift apart; `expected_value.py` is now 25 lines.
- **Monte-Carlo walker** (`--walker monte_carlo --samples N --seed S`):
  distance and processing drawn from a lognormal whose mean is the expected
  value (`variance.distance_sigma` 0.2 / `variance.processing_sigma` 0.3 in
  `defaults.yaml`, per-subtype `sigma` wins), cold starts as a coin flip
  (`cold_prob` × full `cold`), waits exact. Parallel groups take the
  element-wise max. Reports mean + p50/p90/p95/p99 per scenario and p50/p99
  per hop. numpy optional (not installed here — the pure-Python `Vec`
  sampler did 10 000 samples × 6 scenarios in ~4 s). Seeded runs are
  byte-reproducible.
- **Actual numbers** (10 000 samples, seed 1):
  | scenario | mean | p50 | p95 | p99 |
  |---|---|---|---|---|
  | classic-web `page_load` | 57.1 | 56.4 | 72.1 | 79.8 |
  | classic-web-bad `page_load` | 355.4 | 352.7 | 432.1 | 468.5 |
  | foosh `start_workflow` | 151.2 | 111.6 | 509.7 | 529.3 |
  | foosh `run_workflow_3_nodes` | 227.3 | 94.3 | 506.0 | 892.6 |
  | foosh `poll_status` | 107.3 | 66.5 | 465.9 | 479.9 |
  The Lambda-heavy paths are bimodal exactly as intended: foosh
  `run_workflow_3_nodes` p99 is 9.5× its p50, and the Tail-risk section names
  the two 400 ms cold starts (`output_updater`, `parser`) as the drivers.
  classic-web-bad's tail is only 1.3× — a geography problem, not a variance
  problem.
- **`tail_risk` analyzer** (on by default, silent unless sampled): the
  scenario's p99 − p50, then the top hops by their own p99 − p50 with the
  likeliest cause (cold start / cross-region variance / service variance),
  citing the hop. Text/markdown briefs gained a `tail` header line, a `p99`
  hop column and a "Tail risk" section; `report.json` gained
  `percentiles`, `samples` and per-hop `percentiles` (schema 2, additive).
- **Graph viewer** (`iacsim/viewer/index.html`, one self-contained file, no
  CDN): region / AZ swimlanes, nodes left-to-right in request order, edge
  width ∝ expected latency and colour = dominant category, click for
  evidence + breakdown, scenario dropdown overlays the path with hop badges
  and shows total / p50 / p99, network nodes behind a toggle, dark mode via
  `prefers-color-scheme`, file picker fallback for `file://`.
  `iacsim view <target>` runs the pipeline if `report.json` is missing,
  serves `.iacsim/` on a free port and opens the browser (`--no-open`,
  `--port`, `--duration` for tests).
- **CLI tests** (`tests/test_cli.py`, `CliRunner`): every command including
  `--fail-on-regression` exit 2, `view --no-open --duration`, the M7 stub.
  `cli.py` coverage 55 % → ~95 %. Found and fixed a real bug on the way:
  `Config()` shared its first-level dicts with `DEFAULTS`, so a `--profile`
  set in-process leaked into every later `Config` (`core/config.py` now
  uses `copy.deepcopy`).
- **Terraform twin names** now match `config/environments/staging.json`
  (`name_override` for `AsyncWorkflowsStaging` / `WorkflowExecutionsStagingSF`,
  `function_name` for `…-html-template-processor-…`, bucket `…-new`), so the
  label diff against the real CDK template is **empty** — asserted.
- **`validate`** now prints the profile rungs and warns on scenario steps
  naming a node no edge touches; `calibrate` exits 1 with an honest message
  instead of a malformed `typer.Exit`.
- Tests 115 → 141. Limitations: the Monte-Carlo *mean* of a parallel scenario
  is higher than the deterministic total (227 vs 184 ms for foosh
  `run_workflow_3_nodes`) — that is correct (E[max] ≥ max(E)), but the
  recommendations still work on means; Choice/`Default` handling unchanged
  from M2; the viewer's layout is a simple layered one (no force-directed
  physics), fine up to ~50 nodes.

### M7 done (same day, parallel agent) — option (b): built and tested against a fake source

Decision: the metric source is company-specific (account, region, credentials,
even which monitoring system), so it is chosen purely in `iacsim.yaml` and
`cloudwatch` is one implementation among any a team can plug in. Everything is
tested through the `fake` source; the CloudWatch source is complete but was
**not** run against a live account.

- **`MetricSource` ABC** (`core/interfaces.py`): `supports(kind)`,
  `measure(kind, name, window, region) -> dict | None`, plus `prepare(root)`
  and `describe()` hooks; `MetricSourceError` → exit 3 with a one-line hint.
  Constructed from `calibrate.sources.<name>` options exactly like parsers
  (`make_metric_source`), so a Datadog/X-Ray source is a file in `plugins/`.
- **Calibrator** (`latency/calibrate/calibrator.py`): every Lambda / table /
  RDS / ALB / API / state-machine node → complete block (`defaults ←
  measured`, so a partial measurement still has `cold`/`cold_prob`), written
  `by_label` (physical name, portable across Terraform/CloudFormation) or
  `per_resource` (node id) when the name is ambiguous. Skips carry a reason.
  `variance.processing_sigma` = median of measured σ when ≥ 3.
- **Writer** (`writer.py`): overlay YAML in the defaults.yaml schema with a
  provenance header; round-trips through `--profile`. `Profile.processing_for`
  now **merges** `defaults ← by_label ← per_resource` (was replace).
- **Rung in every header**: `profile  defaults → measured.yaml (fake, 7d)`.
- **CloudWatch** (`cloudwatch_queries.py` pure + tested with canned
  GetMetricData results; `cloudwatch.py` thin boto3 wrapper): Duration p50/p99
  → warm/σ, InitDuration → cold, inits/invocations → cold_prob, DynamoDB
  per-operation latency, RDS s→ms, ALB dimension via ListMetrics, API GW
  `Latency − IntegrationLatency`; Step Functions unsupported (not a metric).
- **CLI**: `iacsim calibrate <target> [--source] [--window] [--out] [--region]
  [--dry-run]`; exit 0 with skips, 1 if nothing measured, 2 unknown source,
  3 source not set up. `examples/foosh-serverless/iacsim.yaml` and
  `examples/foosh-cfn/iacsim.yaml` carry only a `calibrate:` block pointing at
  `calibrate-fixture.yaml` (keyed by physical name; two Lambdas left out, one
  deliberately partial), so `iacsim calibrate examples/foosh-serverless` needs
  no flags.

  ```
  calibrated 26 node(s) — source=fake, window=7d
  async-workflow-image-generation-staging  lambda    warm=18000, cold=19000, cold_prob=0.05, sigma=0.3
  async-workflow-parser-staging            lambda    warm=40, cold=900, cold_prob=0.3, sigma=0.4
  async-workflow-text-input-staging        lambda    warm=35  (cold, cold_prob from defaults)
  AsyncWorkflowsStaging                    dynamodb  read=3.2, write=6.1
  …
  skipped 2 node(s) — defaults kept
  async-workflow-router-staging            lambda    no data in window
  async-workflow-video-input-staging       lambda    no data in window
  2 of 28 measurable node(s) stay on defaults.yaml
  ```

- **Effect on foosh `run_workflow_3_nodes`** (defaults → calibrated):
  deterministic total 184.0 → 19,731.8 ms; Monte-Carlo (10 000 samples, seed 1)
  p50 94.0 → 18,257.7 · p95 503.9 → 34,019.0 · p99 892.6 → 42,436.7 ms.
  The 18 s FAL call inside `image_generation` — Layer B, invisible to the IaC —
  now dominates, which is exactly the point of rung 2. The same file
  calibrates `examples/foosh-cfn` through `by_label` (asserted).
- Tests 141 → 161, coverage ≥ 85 % with `cloudwatch.py` omitted (boto3 wrapper).

**Option (a) — live run, not done.** To calibrate a real account, someone
with read-only access runs, on their machine:

```
pip install 'iacsim[calibrate]'                       # boto3
aws configure --profile readonly                       # cloudwatch:GetMetricData + ListMetrics is enough
iacsim calibrate ./infra --source cloudwatch --region us-east-1 --window 7d --out measured.yaml
iacsim run ./infra --profile measured.yaml --walker monte_carlo
```

(or put `source: cloudwatch` and `aws_profile: readonly` in `iacsim.yaml`).
Cost: cents. Nothing is written to AWS. Expect a few "no data in window"
skips for idle functions and possibly an ALB dimension the ListMetrics prefix
match cannot resolve — both keep defaults and say so.

### All milestones complete — M0–M7 in one day, 8 commits, 161 tests.

### Next
- Option (a): a live CloudWatch calibration when an account is available.
- Open items / parked list below.

---

### M8 done — capacity (committed `7b7fdaa`)
- **Fan-out waves**: a Map with `MaxConcurrency 5` and 10 items now costs 2 rounds, not 1 — `run_workflow_10_text` = 6.4 s (calibrated) and `Result.shape.fanout_waves`; `run_workflow_3_nodes` unchanged at 184.0 ms.
- **Capacity attributes** on nodes (`concurrency`, `instances`, `read/write_capacity`) from Terraform and CloudFormation; a `capacity:` block in `defaults.yaml` for account-level limits, overridable with `--profile`.
- **`load` walker** (`simulator/walkers/load.py`, `load.py`, `capacity.py`): arrivals from `load.yaml` (`every`, `while: running` via Little's law, workflow mix), hold time per resource (a Lambda holds its slot for its whole invocation), M/M/c Erlang-C waits, expected and p99 per scenario per user count, `saturated` when ρ ≥ 1. One Result per scenario; the sweep lives in `Result.load`; `report.json` gains a top-level `capacity`.
- **`saturation` analyzer**: first to break (exact, utilisation is linear in users), per-resource threshold crossings, per-scenario p99 crossings, ceilings citing the attribute.
- **Foosh** with the fixture-derived `calibrated.yaml` and `load.yaml` (start every 2 min, poll every 2 s while running, save every 30 s):

  | resource | 100 | 500 | 1,000 | 2,000 | 5,000 | 10,000 | capacity |
  |---|---|---|---|---|---|---|---|
  | async-workflow-api-staging | 4% | 22% | 44% | 88% ▲ | SAT | SAT | 100 slots (reserved_concurrent_executions) |
  | Lambda unreserved pool | 2% | 11% | 21% | 42% | SAT | SAT | 900 slots (account 1000 − 100 reserved) |
  | API Gateway / state machine / tables / S3 | <1% | <1% | ~1% | ~3% | ~7% | ~14% | rps-capped, never the problem |

  **First to break: the `api` Lambda at ~2,270 users** — `poll_status` is 68% of its load; ceiling: raise `reserved_concurrent_executions` on `module.api.aws_lambda_function.this` 100 → 551 for 10,000 users, or poll less often. The unreserved worker pool follows at ~4,713 users (image/video Lambdas hold slots for 18–48 s). p99 at 2,000 users: start_workflow 617 ms, poll 555 ms; every user-facing route saturates by 5,000.
- Tests: `tests/test_load.py` (intervals, mix validation, Erlang-C known values, capacity from attrs, waves, a hand-built graph saturating at the computable U, JSON cleanliness, Little's law), Foosh + CLI load tests. 184 tests, 92.4% coverage.
- Not done: discrete-event simulation (burstiness, the Map's per-workflow cap as a queue), calibration of capacity numbers from CloudWatch (`ConcurrentExecutions`, `Throttles`).

---

## 2026-09-05 — Day 2: the deep-look pass

A teammate (infra/DevOps lead who will run it on real stacks) asked for a deep look so it is
"super efficient". Three read-only review passes (runtime, correctness/UX, code quality), a
sequenced plan of eight work packages, one local commit per package through `make check`,
nothing pushed until reviewed. Weighting: trustworthy numbers first, robustness on unfamiliar
Terraform second, contributor-ready repo third, speed only where visible.

### Done
- **Phase 1 — docs** (`5f108c5`): `DECISIONS.md` (53 decisions + 11 for this pass: question · options · choice · why · file:line) and `CODE_FLOW.md` (all 7 commands traced, two deep traces, glossary, "how to add …" cookbook); 273 file:line references verified by `tests/test_docs_refs.py`.
- **WP1 — pricing correctness** (`3fe71c3`): response legs charge only `respond` (default 0) — no duplicate processing, no impossible cold start; new registered `transition` cost rule charges every hop out of a state machine; op-key fallback (ROUTE→ec2 = `handle`, INVOKE→dynamodb = `read`); unknown region warns once per node. Foosh defaults: `poll_status` 106 → **81**, `start_workflow` 151 → 126, `run_workflow_3_nodes` 184 → **309**; classic-web 57.2 / 356.0 unchanged.
- **WP3 — fan-out and orchestrator** (`f0eb8a9`): innermost Map for fan-out waves (the real CDK template's Maps sit under a Choice — waves were always 1); erlangs per copy (Lambdas were 5× low, others 2× high); `op:` typos error with a hint; parallel visits merged; `reserved_concurrent_executions = 0` = throttled off. Foosh calibrated first-to-break: api Lambda ≈ 2,270 → **Lambda unreserved pool ≈ 3,838 users**.
- **WP2 — edge inference** (`a4941e4`): one edge per (src, dst) with `ops` = everything any rule found, kind by priority (READ over WRITE; `op: write` re-prices) — order-independent; ECS task definitions and Auto Scaling Groups followed; `data.*` and duplicate resources warn; Terraform twin aligned with the real CDK template: graph diff **48 → 0 edges**; new `examples/order-queue` (SQS → Lambda rule 0 % → 100 % executed).
- **WP4 — CLI hardening** (`b28c6e4`): one error boundary, exit codes 0/1/2/3 documented and tested (no tracebacks); relative `--profile`/`--load`/`--out` resolve against the target dir first; `view` prints its URL before blocking; `--version`; `run --scenario`; `validate --strict`.
- **WP5 — real-world Terraform** (`890135d`): hardened against six public repos (serverless-patterns, provider-aws examples, terraform-aws-lambda, learn-eks, MoJ modernisation-platform, hcloud); `*.tf.json`/`.tofu`, tfvars, `--workspace`, `--region` fallback, splat, `file()`/`templatefile()` paths, `.terraform/modules/modules.json`; five vendored fixtures with attribution; **252 tests**.

### In progress
- **WP6 — repo / onboarding**: LICENSE (MIT), CONTRIBUTING, GitHub Actions CI (3.12 + 3.13, `make ci` + the diff exit-2 self-test), pyproject metadata + package-data, ruff E501/bugbear at 120 columns, `examples/iacsim.yaml` regenerated from `DEFAULTS` (asserted by a test), docs drift fixed, `problem-statement.md` rename.
- **WP7 — runtime**: Terraform file/expression parse cache (137 → ~8 ms), static built-in module list instead of walking every module, one pricer per run, edge index, numpy as a dev dependency so the fast Monte-Carlo path is tested.
- **WP8 — cleanups**: diff models out of the IR, `WorkflowStep`/`LoadSummary` dataclasses, dead code out, hermetic tests.

### Next
- Phase 3 roadmap (M9–M14 below) once the pass is reviewed and pushed.

## 2026-09-08 — Day 3: GCP planning (M11)

The first non-AWS cloud. Planning and documentation only — **no file under `iacsim/`
changed this round**; the suite stayed at 290 passing, which is the proof.

### Decided (G1–G6 — full form, with options and evidence, in `docs/gcp/01-DECISIONS.md`)

| id | Decision |
|---|---|
| G1 | Auto-detect the provider from resource-type prefixes (`google_*` → `gcp`); an explicit `provider:` in `iacsim.yaml` still wins |
| G2 | One broad first slice — 40+ `google_*` types — instead of a thin vertical through Cloud Run only |
| G3 | GCP's HTTP load-balancer chain stays 4–5 separate nodes (forwarding rule → target proxy → URL map → backend service → NEG), not collapsed into one; the internal chain hops are priced at 0 ms so the shape is visible without inventing latency |
| G4 | The AWS-subtype behaviour tables inside the engine become **provider-owned** — every `Normaliser` declares its own; the AWS tables move into `AwsNormaliser` unchanged |
| G5 | GCP documentation lives in `docs/gcp/` (overview, decisions, type map, testing, changes) |
| G6 | GCP capacity modelling (`--walker load`) is deferred to a later work package — latency first |

### Found — three ways GCP gets a confident wrong answer today

- **Regions vanish.** `parsers/terraform/loader.py:167` skips every provider block whose
  name is not `aws`, so a `google` provider's region never reaches `RawResource.region`.
  Every GCP node lands with `region=None`, and `latency/rules/distance.py` then prices
  every hop as `same_region_unknown_az` — 0.5 ms where a cross-region hop is 120.
- **INVOKE hops are free.** `latency/rules/processing.py:37` maps a subtype to the key an
  incoming call costs. A subtype missing from that table falls through `:58` with
  `key = None` and the rule returns `{}` — no processing at all, silently.
- **Cold starts never fire.** `latency/rules/cold_start.py` (then line 16) returned early unless
  `dst.subtype == "lambda"`, so Cloud Run and Cloud Functions would show a cold-start
  cost of zero even with `cold` / `cold_prob` in the profile.

All three fail *quietly*: a plausible number, no warning. They are the reason G4 exists —
the tables that decide these three answers have to belong to the provider, not the engine.

### Also found

- The `distance.<cloud>` block that `CODE_FLOW.md` §5 promised a new cloud must add
  **does not exist**. `latency/defaults.yaml:11` is one flat global `distance` block and
  `latency/rules/distance.py` is its only consumer. GCP region names do not collide with
  AWS ones, so GCP pairs merge straight into the flat `cross_region` map. The row is
  corrected in this round's `CODE_FLOW.md` edit.
- Phase 3's "only new registrations" rule does not survive contact with GCP:
  `core/pipeline.py:51` picks the normaliser from config with no detection, `core/models.py:252`
  strips `.aws_` only (GCP node ids would render unshortened), and `core/interfaces.py:156`
  enumerates AWS subtypes inside the `MetricSource` ABC. Each is a real engine edit, and each
  now has a row in `DECISIONS.md` §10 before any code moves.

### Delivered

- **Doc set** under `docs/gcp/`: `00-OVERVIEW.md` (index), `01-DECISIONS.md` (G1–G6 in
  `DECISIONS.md`'s shape), `02-TYPE-MAP.md` (the evidence-backed `google_*` → kind/subtype
  table), `03-TESTING.md` (what to test, with which fixture), `04-CHANGES.md` (the execution
  record, one entry per work package).
- **Seven public GCP Terraform repositories licence-checked as vendorable fixtures** — all
  Apache-2.0, so they can be vendored under `examples/real-world/` with an `ATTRIBUTION.md`
  the way the AWS corpus already is. Listed in `docs/gcp/03-TESTING.md`.
- **Existing docs updated**: `DECISIONS.md` §10 + the D8 amendment, `SPEC.md` §3 / §8 / §9,
  `CODE_FLOW.md` §5, `README.md` "GCP input", `CONTRIBUTING.md` "A cloud provider".

### Round 2 — implemented the same day (six commits, not pushed)

`docs/gcp/04-CHANGES.md` is the record; the short form: loader reads every provider
block (WP1) → behaviour tables move onto each `Normaliser`, fixing a real AWS bug on
the way (`aws_kinesis_stream` hops cost 0 ms) (WP2) → the `gcp` normaliser, its profile,
and a zero-distance LB chain (WP3) → provider auto-detection and `--provider` (WP4) →
five GCP inference rules (WP5) → seven public fixtures vendored unmodified (WP7a) →
`gcp-web` / `gcp-web-bad`, fixture tests, four parser bugs the fixtures exposed (WP7b).

Trust: `make check` 463 passed, 93.5 % coverage; every AWS example's `graph.json`
and `report.json` **byte-identical** to the pre-M11 tree; an independent audit found
two placement gaps (Firestore `location_id`, Spanner `config`), fixed with tests.
Left open on purpose: GCP capacity modelling (G6), a Cloud Monitoring metric source.

### Day 3, continued — the visual dashboard (M15, six work packages, one commit each)

The answer was terminal text, JSON, Markdown, and a bare graph viewer that drew boxes and
arrows and nothing else. `report.json` already carried three times what that viewer showed —
the "where the time goes" shares, the ranked bottlenecks, recommendations with reasoning,
per-hop breakdowns and p50/p99, tail risk, and the whole capacity sweep, which nothing
rendered. So this was a rendering job: **no engine file changed**; the AWS text / markdown /
json bytes did not move (`tests/fixtures/foosh_report_sha256.txt`).

Decided (V1–V5): **V1** one run, one page — `iacsim run … -o html` → a self-contained
`report.html` (`diff.html` for `iacsim diff`), opens from `file://`, no server, no accounts,
not a console with history. **V2** the map is the first screen with time drawn on it — arrow
width = hop ms, colour = dominant category, the selected scenario's path emphasised, the
slowest hop marked; numbers beside it. **V3** explore a run, never edit it — tabs, drawer,
tooltips, toggles; what-if stays `iacsim diff`. **V4** the shareable sample in the repo is
*real* output (`examples/dashboard/`), regenerated by `make examples` and held equal by
`tests/test_dashboard_sample.py`, so it cannot drift. **V5** `html` stays out of the default
`report.outputs` (~400 KB per run); `-o html` is in the quick-start and `iacsim view` renders
the page on demand. Full form in `DECISIONS.md` D54.

- **WP1 — the reporter and the map.** `iacsim/reporter/html_.py` (`HtmlReporter`, registered
  `html`; `render_page` substitutes one placeholder in `iacsim/viewer/index.html` by
  `str.replace`; `_embed` escapes `<` so `</script>` in evidence text cannot end the data block;
  `SECTION_TITLES` mirrors `BriefBuilder` and a drift test holds them equal). The payload
  builder `report_payload()` was extracted from the `json` reporter and is shared, so the page
  and `report.json` are the same document. The template was rewritten: header with source
  format, counts, `generated_at` and the profile rung; scenario tabs; KPI strip; the map in
  region / AZ swimlanes with numbered hop badges, width from hop ms, colour by category, the
  slowest hop marked, per-node share bars, hover tooltips; `displayName()` made
  provider-neutral. `viewer.prepare()` renders via `render_page` instead of copying a static
  file — the `file://` picker and the `fetch()` loader are gone, one loading path. A minimal
  diff page so `iacsim diff -o html` never tracebacks. `examples/dashboard/` skeleton.
- **WP2 — the rail and the tables.** *Where the time goes* as one 100 % stacked bar from the
  additive `per_category` findings with layer chips; *Top bottlenecks* (click selects on the
  map, hover highlights its hops); *Recommendations* as cards with "saves ~N ms" and the
  "based on" reasoning folded under each; the hop table in path order (top-N or all) with the
  critical-path marker, mini breakdown bars, p50/p99 when sampled and **evidence unclipped**;
  critical path and tail risk when present; scenario warnings, then `graph.warnings` once.
- **WP3 — the drawer and the keyboard.** Click a node or edge → a `role="dialog"` drawer:
  name, kind, placement, attrs; every hop touching it in the selected scenario with breakdown
  and evidence; its `per_node` finding, the recommendations whose refs touch it, its capacity
  row, in / out edges. Tabs are a `role="tablist"` with arrow keys; nodes and edges are
  focusable (Enter / Space open the drawer, focus shows the tooltip); Escape closes and
  returns focus; a skip link to the hop table first; `:focus-visible` rings;
  `prefers-reduced-motion` removes transitions; fit-to-width.
- **WP4 — the capacity band** ("users until it breaks", only when `capacity.users` is
  non-empty): the utilisation heatmap (rows = resources by ρ at the last user count, cols =
  the sweep, outlined past `thresholds.utilisation`, hatched SAT at ≥ 1), the p99-by-users
  line chart per scenario with the threshold rule and a SAT marker where the line stops,
  and *What breaks first* from `capacity.first_to_break` plus the ceiling findings; a KPI
  tile "breaks at ~N users · resource" that scrolls to it. The foosh sample regenerated with
  `--walker load`.
- **WP5 — the diff page.** The *after* graph as the map with added (green ring), moved
  (amber ring + "region: a → b" chip) and removed (dashed ghost) nodes; the path from the
  diff's hop labels with a delta chip on each badge; before / after stacked bars and delta rows
  per scenario; the bottleneck shift; recommendations that appeared / disappeared; the table of
  hops that changed with two mini breakdown bars. `classic-web → classic-web-bad` shows RDS
  ringed amber, `us-east-1 → eu-west-1`, +298 ms.
- **WP6 — documentation.** `README.md` "Dashboard" section + quick-start line + layout tree;
  `DECISIONS.md` D54 and the Q2 amendment; this entry and the M15 row; `CONTRIBUTING.md`
  reporter recipe + the browser-smoke checklist; `SPEC.md` out-of-scope and Q2 lines;
  `CODE_FLOW.md` §2.5 for the new `prepare()`.

Trust: `make check` 484 passed, coverage gate ≥ 85 % (the page's JS is outside it by
design — every decision that can be Python is Python and tested: payload, titles, escaping,
substitution, `prepare`, the sample); browser smoke through the Playwright MCP after WP1,
WP3, WP4 and WP5 (node count, drawer evidence, Escape, tab badge counts, no console errors,
dark mode) — checklist in `CONTRIBUTING.md`, not in pytest. No push.

## Milestones

| # | Goal | Deliverable / acceptance | Status | Date |
|---|---|---|---|---|
| M0 | Agree the plan, lay the foundation | SPEC.md agreed; skeleton with every extension point registered; 3 example stacks | ✅ | 2026-09-04 |
| M1 | Terraform → graph | `iacsim graph examples/classic-web` → nodes + edges with evidence in `graph.json`; modules, `for_each`, `templatefile` refs resolved; foosh-serverless parses with Step Functions edges in call order | ✅ | 2026-09-04 |
| M2 | Simulate one request | `iacsim run examples/classic-web` prints total ms + per-hop table; expected-value walker handles sequential / parallel / fanout; inferred scenarios for entry points with no `scenarios.yaml` | ✅ | 2026-09-04 |
| M3 | Say *why* it's slow | All 4 analyzers on real output + `recommendations`; text/markdown report is a bottleneck brief (A1/A2/A3 bar, top nodes, suggestions with savings, hop table); `report.json` schema 2 | ✅ | 2026-09-04 |
| M4 | Compare designs | `iacsim diff classic-web classic-web-bad` shows the cross-region DB as the delta (graph-level move, A1 shift, the two RDS hops, co-locate recommendation); `--fail-on-regression` for CI | ✅ | 2026-09-04 |
| M5 | Read CloudFormation / CDK output | `iacsim graph examples/foosh-cfn` (real `cdk synth` output) produces the same graph as `foosh-serverless` — 30/30 nodes, every difference enumerated and explained in the test | ✅ | 2026-09-04 |
| M6 | Tail latency + polish | `--walker monte_carlo --samples N --seed S` → p50/p90/p95/p99 + `tail_risk`; `iacsim view` graph viewer; CLI tests (141 total); Terraform twin labels == real CDK labels | ✅ | 2026-09-04 |
| M8 | Capacity — users until it breaks | `iacsim run --walker load` sweeps a users count, reports utilisation per resource and p99 per route, names what breaks first with the IaC attribute that raises the ceiling; fan-out waves | ✅ | 2026-09-04 |
| M7 | Real numbers | `iacsim calibrate` — pluggable `MetricSource`; fake source end-to-end (coverage table, overlay YAML, `by_label`, rung in headers, Monte-Carlo picks up measured cold starts); CloudWatch source shipped, **not run live** (option a) | ✅ | 2026-09-04 |

Estimated: M1–M3 ≈ 1 week (demo-able), M0–M6 ≈ 2 weeks, M7 additive. Actual: all of M0–M7 on 2026-09-04.

### Roadmap — Phase 3 (every infra source, every major cloud; live-account reader last, as a plug)

| # | Goal | Deliverable / acceptance | Status | Date |
|---|---|---|---|---|
| M9 | Servers + Kubernetes | `generic` normaliser with `sites.yaml` / `distance.custom`; hcloud / DigitalOcean / Proxmox / vSphere / libvirt type maps; `remote-exec` as "provisioned on host" evidence; Kubernetes manifests / Helm-template adapter (Deployment env, Service→Deployment, Ingress/HTTPRoute, replicas/HPA → capacity) | ⏳ | |
| M10 | Azure | `azurerm` normaliser + rules (private_endpoint, app_settings, role_assignment, APIM backend/policy, Logic App actions, Event Grid, backend pools, VNet peering/vWAN); region-pair defaults; ARM/Bicep parser; `azapi_resource` bodies; one example | ⏳ | |
| M11 | GCP | `google` normaliser + rules (forwarding_rule→url_map→backend_service→NEG→Cloud Run, Eventarc, Pub/Sub push, Workflows YAML, IAM via service accounts, PSC); inter-region defaults; `.tofu` | ✅ | 2026-09-08 |
| M12 | More IaC formats | Pulumi `stack export`, CDKTF `cdk.tf.json`, Config Connector KRM, OpenTofu extras | ⏳ | |
| M13 | Servers without Terraform | Docker Compose, Ansible inventory + roles, Nomad, PaaS config files, Cloudflare Workers / Vercel | ⏳ | |
| M14 | Live-account readers (the plug) | `INVENTORY_SOURCES` registry configured like `calibrate.sources`; AWS Config / Resource Explorer, Azure Resource Graph, GCP Cloud Asset Inventory; read-only; tested with a fake inventory | ⏳ | |
| M15 | Visual dashboard | `iacsim run … -o html` → one self-contained `report.html` (data inlined, opens from `file://`, no server); `iacsim diff … -o html` → `diff.html`; `iacsim view` serves the same page. The map first with time drawn on it (hop width, category colour, slowest hop, share bars), rail (where the time goes · bottlenecks · recommendations), hop table with unclipped evidence, drawer + keyboard, the capacity band (heatmap, p99 by users, what breaks first), the diff page; `examples/dashboard/` real output held by a test; section titles held to `BriefBuilder`; no engine change, AWS bytes unchanged | ✅ | 2026-09-08 |

Rule for every Phase 3 milestone: only new registrations — `iacsim plugins` lists the new names and `core/`, `simulator/`, `analyzer/`, `reporter/` stay untouched except additive registrations.

## Open items / ideas parked

- 🧊 Reading CDK Python source directly (instead of `cdk.out`) — deferred, `cdk synth` output is enough.
- 🧊 X-Ray traces as an inference *validator* (confirm/deny inferred edges) — a natural `MetricSource` plugin plus a rule.
- 🧊 Option (a): live CloudWatch calibration against a real account (read-only creds; one command, see M7 notes).
- 🧊 Discrete-event load simulation (burstiness, warm-up, the Map's per-workflow cap as a queue) — M8 is analytic M/M/c.
- 🧊 Calibrating capacity numbers (`ConcurrentExecutions`, `Throttles`) from CloudWatch.
