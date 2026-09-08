# IaC Latency Simulator — Spec

> Status: **v1 AGREED** (2026-09-04); M0–M8 built; deep-look pass in progress (2026-09-05). Every decision, including the ones made after this spec, is logged in `DECISIONS.md`. Future changes: add a row there, then update the section here it affects.

---

## 1. What it does (one paragraph)

Reads Infrastructure-as-Code (Terraform first), builds a graph of the infrastructure — services, databases, load balancers, queues, regions — assigns a latency cost to every hop, then simulates a user request walking through that graph and reports (a) the end-to-end latency, (b) which components/hops contribute most, and (c) how a proposed infra change shifts those numbers compared to the current state. It runs *before* deploy, on the IaC files alone — no AWS account needed.

## 1a. Where latency comes from — the model behind the tool

A request's latency has two layers. iacsim models the first; the second is
absorbed through calibration and can be modelled directly later.

```
LAYER A — INFRASTRUCTURE  (modelled now)
  A1  Distance      where things are placed: internet→edge, same AZ, cross AZ, cross region
  A2  Service cost  which AWS service you chose: API GW route, DynamoDB read, Lambda cold start, SFN transition…
  A3  Shape         how things are wired: hop count, sequential vs parallel, fan-out, DB round-trips per request

LAYER B — APPLICATION CODE  (not modelled yet)
      what the Lambda does inside: business logic, third-party API calls, N+1 queries
```

A1 and A2 need numbers. **A3 needs none** — it is read straight from the
Terraform + scenario and is often the biggest lever (`classic-web-bad` is an
A1 story; Foosh's Map concurrency is an A3 story). The report attributes time
to A1 / A2 / A3 side by side (`per_category` analyzer: `distance`,
`processing`/`cold_start`, `shape`).

### Number sources — a ladder, same schema at every rung

| Rung | Source | Granularity | Status |
|---|---|---|---|
| 0 | Built-in defaults (public AWS figures) — `latency/defaults.yaml` | per service type | now |
| 1 | Team overrides — `--profile team.yaml` | per type or per resource | now |
| 2 | Measured — `iacsim calibrate` via a pluggable `MetricSource` (`cloudwatch` built in, `fake` for tests, plugins for anything else) | per resource | built (M7); live CloudWatch run pending an account |
| 3 | Traces (X-Ray) | per hop; also confirms/denies inferred edges | parked |

The engine never knows which rung it is on; the report prints it.

**Bridge to Layer B:** CloudWatch `Lambda.Duration` measures A2 + B together
(cold start + your code + the 20 s FAL call). So rung 2 pulls Layer B into
the model per Lambda with no code analysis. Roadmap: Layer A comparative
answers now → calibrated absolute answers (M7) → true Layer B (code /
trace-driven) only if needed.

## 2. Who uses it, how

An engineer with a Terraform repo runs:

```
iacsim graph ./infra                      # parse + map only → .iacsim/graph.json
iacsim validate ./infra [--strict]        # parse, map, check scenarios.yaml; exit 1 on problems
iacsim run ./infra                        # simulate current infra
iacsim run ./infra --scenario checkout    # simulate a named request path
iacsim diff ./infra-before ./infra-after  # compare two versions (--fail-on-regression 50ms → exit 2)
iacsim plugins                            # every registered implementation, per extension point
iacsim run ./infra --profile measured.yaml  # use measured latency numbers
iacsim calibrate ./infra [--source cloudwatch|fake] [--window 7d] [--out measured.yaml] [--dry-run]   # measured numbers → profile (rung 2)
iacsim run ./infra --walker monte_carlo --samples 10000 --seed 1   # (M6) p50/p95/p99 + tail risk
iacsim view ./infra                         # (M6) graph viewer in the browser
```

and gets a report: total latency, ranked bottleneck list, and per-hop breakdown.

## 3. Scope

### In scope (v1)
- Terraform `.tf` parsing (HCL) — AWS provider.
- CloudFormation JSON/YAML as a second input adapter (Q5, M5) — covers CDK / SAM / Serverless Framework output; makes the Foosh project a real test case. Input format is auto-detected: `.tf` files → Terraform parser; `*.template.json` / `template.yaml` with `AWSTemplateFormatVersion` or `Resources` → CloudFormation parser.
- Resource types: API Gateway, ALB/NLB, EC2, ECS/Fargate, Lambda, Step Functions, SQS, SNS, DynamoDB, RDS, ElastiCache, S3, CloudFront, VPC/subnet/AZ/region, VPC peering, NAT gateway.
- Latency model: per-hop base costs + distance costs (same AZ / cross AZ / cross region) + component processing costs.
- Simulation of one or more named request paths.
- Bottleneck ranking (per-hop and per-component contribution).
- Diff between two IaC snapshots.
- CLI + text/JSON report.

### Out of scope (v1)
- Azure (M10) — the graph is provider-neutral; this is a normaliser plus rules, not a new parser.
- GCP — **no longer out of scope: in progress (M11)**, see `docs/gcp/`. The correction that milestone forced: the blocker was never the *parsers*. `google_*` Terraform parses today with the same loader, evaluator and `${address.attr}` reference convention as `aws_*`. What is missing is a `google` normaliser, GCP inference rules (forwarding rule → URL map → backend service → NEG → Cloud Run, Eventarc, Pub/Sub push, Workflows, IAM via service accounts), GCP pairs in `cross_region`, and the AWS-subtype behaviour tables becoming provider-owned (`DECISIONS.md` §10, G4). One AWS-only filter on provider blocks in the Terraform loader is what drops the GCP region today.
- Discrete-event load simulation (burstiness, warm-up transients). M8's `load` walker models contention analytically (M/M/c per resource) and names what breaks first; a full event-driven simulator is parked.
- Live *tracing* (X-Ray per-hop timings). Aggregate metrics via `iacsim calibrate` are in (M7).
- Application code analysis (we don't read Lambda source to figure out what it calls).
- Reading CDK / Pulumi source directly (Q5) — run `cdk synth` yourself and point the tool at `cdk.out/`.
- A web *console* (server, accounts, a history of runs). The visual output shipped as **M15** instead: `iacsim run … -o html` / `iacsim diff … -o html` write one self-contained page per run (the `html` reporter), and `iacsim view` serves the same page — see `DECISIONS.md` D54.

## 4. Inputs and outputs

### Inputs
1. **IaC directory** — folder of `.tf` files (or a CloudFormation template).
2. **Scenario file (optional)** — `scenarios.yaml` describing request paths (Q3). With no file, the tool infers paths: it finds entry points (API Gateway, ALB, CloudFront) and walks inferred edges. The file is only needed to pin an exact path, express parallel steps, or override a wrong guess.

   ```yaml
   checkout:
     entry: aws_api_gateway_rest_api.main
     steps:
       - aws_lambda_function.create_order
       - parallel:
           - aws_dynamodb_table.orders        # write
           - aws_sqs_queue.notifications      # publish
       - aws_lambda_function.confirm
   ```
3. **Latency profile (optional)** — `latency.yaml` overriding the built-in cost table. See Q4.

### Outputs
1. **Report** — per scenario: total latency, per-hop table, top-N bottlenecks. Terminal text + `report.json`.
2. **Graph dump** — `graph.json` (nodes + edges), so it can be visualised later.
3. **Diff report** — before/after totals, and per-hop deltas.

## 5. Architecture — the pipeline

```
 .tf files ──► [Terraform parser] ──┐
                                    ├──► [Normaliser] ──► Infra Graph (IR) ──► [Latency model] ──► Weighted graph
 CFN json  ──► [CloudFormation      │                                                                     │
                parser]  ───────────┘                                                                     ▼
                                                                                              [Simulator] ◄── scenarios.yaml
                                                                                                          │
                                                                                                          ▼
                                                                                        [Analyzer] ──► [Reporter] ──► CLI / JSON
                                                                                                          ▲
                                                                                             [Differ] ─────┘   (runs pipeline twice)
```

Every stage talks to the next one **only through the Infra Graph (IR)**. That is the contract that makes the thing modular: a new parser (Pulumi, GCP) or a new latency model plugs in without touching the simulator.

## 5a. Design principle — everything is plug-and-play

**Rule:** every place this spec makes a choice is an *extension point*, not a hard-coded decision. Each extension point is (1) an abstract interface, (2) a registry that maps a name → implementation, and (3) a key in `iacsim.yaml` (or a CLI flag) that picks the implementation by name. Adding an option means adding one file and one registry entry — never editing the engine.

| Extension point | Interface | Built-in implementations | Selected by |
|---|---|---|---|
| Input parser | `Parser.parse(path) -> RawResources` | `terraform`, `cloudformation` | auto-detect, or `--format` |
| Resource normaliser (provider → neutral node kind) | `Normaliser.normalise(RawResources) -> InfraGraph` | `aws` | `provider:` in config |
| Edge inference rules | `InferenceRule.apply(graph) -> [Edge]` | one class per row of the inference table (`step_functions`, `event_source_mapping`, `lambda_permission`, `target_group`, `env_var`, `iam_policy`, …) | `inference.rules: [list]` — enable/disable/reorder |
| Scenario source | `ScenarioSource.load() -> [Scenario]` | `yaml_file`, `inferred_from_entrypoints` | `scenarios.sources: [list]` |
| Latency profile source | `ProfileSource.load() -> dict` | `defaults`, `yaml_file` (a calibrated file is just a `yaml_file`) | `--profile` (stackable) |
| Latency cost rules | `CostRule.cost(edge, profile) -> Latency` | `distance`, `processing`, `cold_start`, `serialisation` | `latency.rules: [list]` |
| Simulation walker | `Walker.run(graph, scenario) -> Result` | `expected_value` (M2), `monte_carlo` (M6), `load` (M8: capacity + arrival rates → contention) | `--walker` / `simulation.walker:` |
| Analyzer | `Analyzer.analyse(Result) -> Findings` | `per_hop`, `per_node`, `per_category`, `critical_path`, `recommendations`, `tail_risk` (M6, only speaks when sampled) | `analysis.analyzers: [list]` |
| Reporter | `Reporter.render(Findings) -> output` | `text`, `json`, `markdown`, `html` (M6) | `--output` |
| Metric source (calibration) | `MetricSource.supports(kind)` / `.measure(kind, name, window, region) -> dict | None`, plus `prepare(root)` / `describe()` hooks | `cloudwatch`, `fake`; company plugins (Datadog, X-Ray…) in `plugins/` | `calibrate.source:` + `calibrate.sources.<name>:` options |

Mechanics:

- One `Registry[T]` helper; each package exposes `register(name, cls)` and a `get(name)`.
- Implementations are discovered from the built-in packages **and** from a `plugins/` directory / `iacsim.plugins` entry-point group, so a team can drop in a custom rule without forking.
- A single `iacsim.yaml` at the repo root holds all selections; every key has a sane default so the file is optional. CLI flags override the file.

```yaml
# iacsim.yaml — every key optional
provider: aws
inference:
  rules: [step_functions, event_source_mapping, lambda_permission, target_group, api_gateway_integration, env_var, iam_policy]
scenarios:
  sources: [yaml_file, inferred_from_entrypoints]
latency:
  profiles: [defaults, ./profiles/team.yaml]
  rules: [distance, processing, cold_start]
simulation:
  walker: expected_value      # or monte_carlo
  samples: 10000              # monte_carlo only
analysis:
  analyzers: [per_hop, per_node, per_category, critical_path, recommendations, tail_risk]
report:
  outputs: [text, json]
```

The IR (`InfraGraph`, `Scenario`, `Profile`, `Result`, `Findings`) is the fixed contract between extension points. It is versioned (`schema_version` field in every dumped JSON) so old `graph.json` files stay readable after upgrades.

## 6. Modules

| Module | Responsibility | Depends on |
|---|---|---|
| `parsers/terraform` | Read HCL, resolve `resource`/`module`/`variable`/`locals` refs, emit raw resources + references | HCL library |
| `parsers/cloudformation` | Read template JSON/YAML, resolve `Ref`/`Fn::GetAtt`/`DependsOn`, emit raw resources + references | — |
| `graph/` | The IR: `Node`, `Edge`, `InfraGraph`. Normaliser maps provider-specific resource types to neutral node kinds (`compute`, `datastore`, `lb`, `queue`, `gateway`, `cdn`, `network`) and attaches `region` / `az` / `vpc` placement. | parsers |
| `latency/` | Cost table (built-in YAML) + rules that turn an edge into a latency distribution: `base(kind_a → kind_b) + distance(placement_a, placement_b) + processing(kind_b)` | graph |
| `latency/calibrate/` | `calibrator.py` (graph + MetricSource → complete per-resource blocks), `writer.py` (overlay YAML), `fake.py`, `cloudwatch_queries.py` (pure maths, tested), `cloudwatch.py` (thin boto3 wrapper, the only boto3 import) | latency, core.interfaces |
| `simulator/` | Walks a scenario path through the weighted graph. Sequential hops add; parallel branches take the max; fan-out (Step Functions Map / SQS) modelled explicitly. Two walkers behind one interface (Q6): `ExpectedValueWalker` (M2) and `MonteCarloWalker` (M6, `--samples N`, reports p50/p95/p99) | latency, scenarios |
| `analyzer/` | Attribution: which hops / nodes / *categories* (cross-region, cold start, DB) contribute what %. Ranks them. | simulator |
| `differ/` | Runs the pipeline on two inputs, aligns nodes by logical name, reports deltas | everything above |
| `reporter/` | Text tables + JSON | analyzer, differ |
| `cli/` | `run`, `diff`, `graph`, `validate` commands | reporter |

## 7. Data model (IR)

```
Node
  id          "aws_lambda_function.create_order"
  kind        compute | datastore | lb | queue | gateway | cdn | network | external
  subtype     lambda | ec2 | fargate | dynamodb | rds | alb | sqs | ...
  placement   { region, az?, vpc?, subnet? }
  attrs       { memory, runtime, engine, ... }   # only what the latency model needs

Edge
  src, dst    node ids
  kind        invoke | read | write | route | publish | consume | peer   # the operation that is priced
  ops         [read, write]                     # every operation any rule found evidence for (one edge per src→dst;
                                                # a scenario step's `op:` re-prices the hop with another of these)
  evidence    "env var TABLE_NAME → aws_dynamodb_table.orders"   # why we think this edge exists
  latency     { expected, p50, p99, dist }       # filled in by latency model; `expected` used by
                                                 # the deterministic walker, the rest by Monte-Carlo (Q6)

Scenario
  name        "checkout"
  entry       node id
  steps       ordered list of hops; supports parallel: [[a,b],[c]] and fanout: {node, count}
```

Edges come from two places (Q3): **inferred** from IaC references and **declared** in `scenarios.yaml`. Declared always wins over inferred. Inferred edges get an `evidence` string so the report can say *why* it thinks a hop exists.

Inference rules, in order of confidence:

| Evidence in IaC | Edge produced | Confidence |
|---|---|---|
| Step Functions state machine definition (`Task` states, `Map`, `Parallel`) | state machine → lambda/service, with real ordering + parallelism | high — this *is* the call graph |
| `aws_lambda_event_source_mapping` (SQS/DynamoDB stream/Kinesis → Lambda) | queue/stream → lambda (`consume`) | high |
| `aws_lambda_permission` from API Gateway / S3 / SNS | gateway/bucket/topic → lambda (`invoke`) | high |
| ALB/NLB `target_group` + `listener` | lb → ec2/ecs (`route`) | high |
| API Gateway `integration` | gateway → lambda/http (`invoke`) | high |
| Lambda/ECS env var referencing a table/bucket/queue | compute → datastore/queue (`read`/`write`) | medium |
| IAM policy granting `dynamodb:*`, `s3:*`, `sqs:SendMessage`, `lambda:InvokeFunction` on a specific ARN | compute → target | medium |
| `aws_vpc_peering_connection` | vpc ↔ vpc (`peer`) — placement hint for the distance rule | high |
| Security group ingress from another SG | network reachability only; not a call edge | low — used to *validate* declared paths |

## 8. Latency model — where the numbers come from

Built-in `latency/defaults.yaml`, editable:

```yaml
distance:
  same_az: 0.3        # ms, one-way network
  cross_az: 1.0
  cross_region:       # matrix, ms — populated for common pairs, fallback by geo distance
    us-east-1/us-west-2: 65
    us-east-1/ap-south-1: 190
processing:
  lambda:      { warm: 5,  cold: 400, cold_prob: 0.05 }
  dynamodb:    { read: 4,  write: 8 }
  rds_postgres:{ query: 5 }
  alb:         { route: 2 }
  api_gateway: { route: 10 }
  step_functions: { transition: 25 }
  sqs:         { enqueue: 10, poll_delay: 100 }
  s3:          { get: 20, put: 30 }
```

Numbers are *approximate public figures*, not truth — the point is relative comparison ("moving the DB saves ~180 ms"), not absolute prediction. Every number is overridable and the report states which profile was used.

**One flat distance table, all clouds.** There is no `distance.<cloud>` block — `distance` is
global and `latency/rules/distance.py` is its only consumer. GCP region names (`us-central1`,
`europe-west1`) do not collide with AWS ones, so GCP inter-region pairs merge straight into the
same `cross_region` map; `same_az` / `cross_az` / `internet_to_edge` are shared.

**Subtype names must be unique across clouds.** `Profile.processing_for` is keyed by the bare
`Node.subtype` and `Node` carries no provider field, so a GCP subtype that reuses an AWS name
would silently inherit AWS numbers. GCP subtypes are therefore named distinctly (`cloud_run`,
`cloud_sql`, `pubsub`, `gcs`, …), never `lambda` or `s3`.

### Profile layering (Q4)

Profiles stack, later wins per key:

```
defaults.yaml  →  --profile team.yaml  →  --profile calibrated-prod.yaml
```

The **same YAML schema** is used at every layer, so a hand-written profile and a machine-generated one are interchangeable. Two extra fields make the schema calibration-ready from day one:

```yaml
meta:
  source: "defaults" | "cloudwatch" | "fake" | <plugin name>     # shown in every report header as the rung
  window: "7d"
  generated_at: 2026-09-04T10:00:00+00:00
  format: terraform                # which IaC the per_resource ids belong to
  region: us-east-1
variance:
  processing_sigma: 0.3            # median of measured per-resource σ, when ≥ 3 were measured
processing:
  lambda:
    defaults: { warm: 5, cold: 400, cold_prob: 0.05 }          # defaults.yaml only
    by_label:                      # calibrate writes these, keyed by physical AWS name
      async-workflow-parser-staging: { warm: 40, cold: 900, cold_prob: 0.3, sigma: 0.4 }
    per_resource:                  # only when a name is ambiguous in the graph; keyed by node id
      module.worker["x"].aws_lambda_function.this: { warm: 12, cold: 850, cold_prob: 0.02 }
```

Lookup for any node: `defaults[subtype]` ← `by_label[node.label]` ← `per_resource[node.id]`, **merged** (later wins per key), so an override can set one key and inherit the rest. A calibrated file is an *overlay* — `meta`, `variance`, `processing.<subtype>.{by_label, per_resource}` — and never changes the shape of the schema. `by_label` is what lets one calibrated file serve both the Terraform and the CloudFormation graph of a stack.

### Calibration — built (M7), pluggable by design

`iacsim calibrate ./infra [--source NAME] [--window 7d] [--out measured.yaml] [--region R] [--dry-run]`

Graph → for every node whose subtype is a metric kind → `MetricSource.measure(kind, physical name, window, region)` → complete block (`defaults ← measured`) → overlay YAML + coverage table (covered with the measured keys and any filled from defaults; skipped with reason: no data in window / source does not support kind / no physical name).

| Kind | CloudWatch source reads | Fills |
|---|---|---|
| lambda | `Duration` p50, p99; `InitDuration` p50 + SampleCount; `Invocations` Sum | `warm`, `cold = warm + init`, `cold_prob = inits / invocations`, `sigma = ln(p99/p50)/2.326` |
| dynamodb | `SuccessfulRequestLatency` p50 per GetItem / Query / PutItem / UpdateItem | `read`, `write` |
| rds | `ReadLatency`, `WriteLatency` (s → ms) | `read`, `write` |
| alb | `TargetResponseTime` p50 (dimension `app/<name>/<id>` resolved via ListMetrics) | `route` |
| api_gateway | `Latency` p50 − `IntegrationLatency` p50 | `route` |
| step_functions | — not a metric; per-state timings need execution history — unsupported by the CloudWatch source (a plugin may add it) | |

The source is company-specific: `calibrate.source` picks a registered `MetricSource`, `calibrate.sources.<name>` are its constructor options (region, named AWS profile, fixture path, env-var *names* — never credentials). `prepare(root)` lets a source resolve paths relative to the config; `describe()` adds provenance to `meta`. `cloudwatch_queries.py` holds all the maths and is unit-tested with canned responses; `cloudwatch.py` is the only file that imports boto3 (lazily — missing SDK or credentials → one-line hint, exit 3). The `fake` source reads a YAML fixture and backs every test. A live CloudWatch run is not exercised in this repo (no account); see TIMELINE.md "Option (a)".

## 9. Repository layout

```
IAC/
  SPEC.md                    # this file
  problem-statement.md
  iacsim/                    # python package — one folder per pipeline stage, one file per implementation
    core/        registry.py  models.py (IR)  interfaces.py (ABCs + registries)  config.py  pipeline.py
    parsers/     detect.py  terraform/{hcl_expr,evaluator,loader,parser}.py  cloudformation/parser.py
    graph/       normalisers/aws.py  normalisers/gcp.py (M11)  inference/<one file per rule>.py  (+ vpc_peering, added in M1)
                 inference/ GCP rules (M11): backend_service.py  eventarc.py  pubsub_push.py  workflows.py  service_account.py  psc.py
    scenarios/   yaml_file.py  inferred.py
    latency/     defaults.yaml  profile.py  rules/{distance,processing,cold_start}.py  calibrate/{calibrator,writer,fake,cloudwatch,cloudwatch_queries}.py
    simulator/   walkers/{expected_value,monte_carlo}.py
    analyzer/    per_hop.py  per_node.py  per_category.py  critical_path.py
    reporter/    text.py  json_.py
    cli.py  differ.py
  plugins/                   # drop-in extensions, auto-imported (see plugins/README.md)
  pyproject.toml
    parsers/{terraform,cloudformation}/
    graph/
    latency/  defaults.yaml  calibrate/
    simulator/
    analyzer/
    differ/
    reporter/
    cli.py
  examples/
    modules/                 # shared Terraform modules, one folder each
      network/  load_balancer/  compute/  database/  lambda_function/  dynamodb_table/
    classic-web/             # ALB → EC2 (2 AZs) → RDS, single region            (root stack = module calls only)
    classic-web-bad/         # same modules, RDS in eu-west-1 over VPC peering — obvious bottleneck for `diff`
    foosh-serverless/        # Terraform twin of ~/Foosh/async-workflows: API GW → api Lambda → 10 tables / S3 / Step Functions → 15 workers
                             #   split by function: main.tf lambdas.tf dynamodb.tf s3.tf api_gateway.tf step_functions.tf + step_functions/workflow.asl.json
    foosh-cfn/               # M5: the real cdk.out template — must produce the same graph as foosh-serverless
    iacsim.yaml              # sample config (all keys optional)
    <each example>/scenarios.yaml
  tests/
```

## 10. Sizing — how much work

| Milestone | Deliverable | Rough effort |
|---|---|---|
| M0 ✅ | Spec agreed; package skeleton with every extension point registered (`iacsim plugins`); `classic-web`, `classic-web-bad`, `foosh-serverless` Terraform written on shared modules | done 2026-09-04 |
| M1 | Terraform parser → Infra Graph + `graph` command that dumps JSON | 2–3 days |
| M2 | Latency model + simulator + `run` on one scenario | 2–3 days |
| M3 | Analyzer (bottleneck ranking) + readable report | 1–2 days |
| M4 | `diff` command | 1 day |
| M5 | CloudFormation adapter, Foosh as real test case | 2 days |
| M6 | Polish: validation, docs, tests + Monte-Carlo walker (`--samples`) + static graph viewer | 3 days |
| M7 | `calibrate`: pluggable MetricSource → profile YAML; fake source tested end-to-end, CloudWatch source shipped untested against a live account | 1 day |

~2 weeks of focused work for a reviewable prototype (M0–M6); M1–M3 alone (≈1 week) is already a demo. M7 is additive and can run against the Foosh AWS account when ready.

## 11. Risks / known hard parts

- **Terraform is not a call graph.** It says what exists and what *may* talk to what (IAM), not the order of calls. Scenarios fill the gap — but that means the engineer has to write them. Mitigation: infer as much as possible (Step Functions definitions are gold — they *are* the call graph for serverless).
- **Modules and variables.** Real Terraform uses `module` blocks, `for_each`, `count`, remote modules. v1 handles local modules and simple interpolation; anything unresolvable becomes a node with a warning, not a crash.
- **Numbers will be wrong in absolute terms.** Position the tool as comparative. Report always shows the profile used.

---

## 11a. Cross-cutting decision (from user)

> "Everywhere we have a choice, make it plug-and-play. Every config should be plug-and-play so it can be updated / changed / swapped later."

Applied as section 5a. Checklist for every future PR: *if this adds a choice, did it add an interface + registry entry + config key?*

## 12. Q&A — decisions log

Questions are asked one at a time. Each answer gets recorded here and the sections above get updated.

| # | Question | Decision | Rationale |
|---|---|---|---|
| Q1 | Language / stack | **Python 3.12** — `python-hcl2` (Terraform parsing), `typer` (CLI), `pyyaml`, `rich`, `pytest`; `numpy` optional (Monte-Carlo) | Fastest to prototype; strongest HCL library outside Go (TS options are thin or wrap a Go binary); the graph is plain dataclasses (`networkx` was planned and dropped as unused); Foosh infra is Python CDK. TypeScript was considered and rejected for v1 — it only wins if the engine must run in-browser, which the JSON-viewer plan avoids |
| Q2 | Interface | **CLI + JSON first; web graph viewer later (M6)** — became the dashboard in M15: `-o html` and `iacsim view` render one page from `report.json` | `graph.json` / `report.json` are the contract, so a viewer can be bolted on without touching the engine — M15 did exactly that: the dashboard is a reporter, no engine file changed |
| Q3 | Request paths | **Infer from IaC evidence + optional `scenarios.yaml` overrides** | Works out of the box on a fresh repo; engineer only writes scenarios to pin exact paths or fix wrong guesses. Every inferred hop carries an `evidence` string so wrong guesses are visible and correctable |
| Q4 | Latency numbers | **Build defaults + `--profile` override now (v1); design the profile format and module layout so `iacsim calibrate` (pull real numbers from CloudWatch) slots in later (M7)** | Comparative results are fine with public defaults; measured numbers make it trustworthy. Calibration needs AWS creds so it stays optional and separate |
| Q5 | Input formats | **Terraform (primary, M1) + CloudFormation JSON/YAML adapter (M5)** | CloudFormation is what CDK / SAM / Serverless Framework emit, so Foosh's `cdk.out/*.template.json` is a real test case with no rewriting. Reading CDK source directly is deferred — `cdk synth` output already covers it |
| Q6 | Simulation method | **Deterministic expected-value in M2; Monte-Carlo `--samples N` in M6 for p50/p95/p99** | Deterministic is explainable and testable first; profile schema already stores `p50/p99/cold_prob`, so Monte-Carlo is additive — same graph, same profile, different walker |
| Q7 | First examples | **`classic-web` first (ALB → EC2 ×2 AZ → RDS, plus `classic-web-bad` with RDS cross-region), then `foosh-serverless`** | Trivial fixture gets the pipeline green end-to-end in M1; the Foosh rewrite exercises Step Functions / event-driven inference and later doubles as a correctness check for the CloudFormation adapter (both inputs must yield the same graph) |

## 13. M8 — capacity (added 2026-09-04)

Same graph, same scenarios, one more walker. `load.yaml` gives arrivals per
user and a users sweep; `Result.load` carries utilisation per resource and
latency per user count; the `saturation` analyzer names what breaks first and
what attribute raises the ceiling. Capacity attributes come from the IaC
(`reserved_concurrent_executions`, `desired_count`, `instance_class`,
provisioned throughput, Map `MaxConcurrency`) and from a new `capacity:` block
in `defaults.yaml` for account-level limits. Layer A3 gains *waves*: fan-out
beyond a Map's concurrency costs `ceil(count / c)` rounds. Analytic M/M/c; no
new dependencies; no upstream stage changed.
