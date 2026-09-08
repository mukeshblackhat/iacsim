# iacsim — IaC → latency simulation

Reads Terraform (or CloudFormation), builds a graph of your infrastructure,
simulates a request through it, and tells you where the milliseconds go —
before you deploy. See `SPEC.md` for the design, `DECISIONS.md` for every choice and why,
`CODE_FLOW.md` for what calls what, `TIMELINE.md` for status and
milestones, `problem-statement.md` for the why, and `CONTRIBUTING.md` before sending a change.
MIT licensed (`LICENSE`).

**Status:** M0–M8, M11 (GCP) and M15 (the dashboard) built; a deep-look hardening pass is in progress (see `TIMELINE.md`, 2026-09-05).
CI (`.github/workflows/ci.yml`) runs `make check` + `make examples` on Python 3.12 and 3.13.

```
pip install -e ".[dev]"
iacsim plugins                       # what is registered
iacsim graph examples/classic-web    # M1
iacsim run   examples/classic-web    # M2+
iacsim run   examples/foosh-serverless --walker load --profile examples/foosh-serverless/calibrated.yaml   # M8: users until it breaks
iacsim diff  examples/classic-web examples/classic-web-bad   # M4
iacsim calibrate examples/foosh-serverless                     # M7 — measured numbers → calibrated.yaml (then --profile calibrated.yaml)
iacsim diff  ./main ./pr --fail-on-regression 50ms           # CI: exit 2 if any scenario grows > 50 ms (or 10%)
iacsim diff  ./before ./after --scenario checkout -o markdown  # one scenario, PR-comment markdown → .iacsim/diff.md
iacsim run   examples/foosh-serverless --walker monte_carlo --samples 10000 --seed 1   # M6: p50/p95/p99 + tail risk
iacsim run   examples/gcp-web -o html                          # M15: the dashboard → .iacsim/report.html (one file, opens from file://)
iacsim view  examples/classic-web-bad                         # M15: the same dashboard, rendered from report.json and served
iacsim run   examples/foosh-serverless --scenario poll_status  # only the named scenario(s)
iacsim validate ./infra --strict                             # exit 1 on any parser warning, not only unwired steps
iacsim --version
```

## One edge per pair, every operation remembered

Rules often find more than one thing about the same pair — an env var says a Lambda *knows* a
table, an IAM statement says it may *read and write* it. iacsim keeps **one edge per
`src → dst`** with `ops` = every operation any rule found evidence for, prices the
highest-priority one (`invoke > route > consume > publish > read > write`; a request path reads
by default), and says so in the hop evidence: *"(also may write: use op: write)"*. A scenario
step re-prices with `- { node: …, op: write }`. The result is the same graph whichever rule
runs first, and the Terraform twin of the Foosh stack now matches the real CDK template exactly.

## Real-world Terraform

iacsim is tested against unmodified public Terraform projects vendored under
`examples/real-world/` (each with an `ATTRIBUTION.md`): an AWS serverless pattern, an
EventBridge-Pipes-to-Step-Functions pattern, the provider's `ecs-alb` and `two-tier`
examples, and an EKS cluster built from registry modules. `tests/test_real_world.py` asserts
that every one of them parses, builds a graph and runs, and that every warning is a *named*
one. See `examples/real-world/README.md` for the larger manual-run corpus.

What the Terraform parser reads:

- `.tf`, `.tofu` and `.tf.json` files; `terraform.tfvars`, `*.auto.tfvars` (+ `.json`) — values win over defaults
- local modules, `count` / `for_each` on resources and modules, nested `dynamic` blocks, `[*]` splats
- `terraform.workspace` (`--workspace NAME`, default `default`)
- `file()`, `fileexists()`, `templatefile()` relative to the module, then the root
- registry / git modules **after `terraform init`** — iacsim follows `.terraform/modules/modules.json`
  to the downloaded copies; without it, one warning per module

What stays a warning (never a crash): `data.*` sources, `terraform_remote_state`, provider-computed
functions (`cidrsubnet`, `filemd5`, …), a provider region that does not resolve (pass `--region`
as the fallback), resource types iacsim does not model yet (kept as network nodes so nothing vanishes).

## Exit codes and paths

| code | meaning |
|---|---|
| 0 | ok |
| 1 | problems found — `validate`: a scenario step no edge touches (or any warning with `--strict`); `calibrate`: nothing could be measured |
| 2 | input error — unknown node / implementation / file, a bad flag value — or `diff --fail-on-regression` tripped |
| 3 | metric source unusable (`calibrate`: missing SDK, no credentials, access denied) |

Every error is one line on stderr prefixed with the command name — no tracebacks.
Relative paths given to `--profile`, `--load` and `--out` are resolved against the
target directory first, then your current directory, so `iacsim calibrate ./infra`
followed by `iacsim run ./infra --profile calibrated.yaml` just works.

## Tail latency — `--walker monte_carlo`

The default walker adds expected values. `--walker monte_carlo` draws every hop
`--samples` times instead — distance and processing from a lognormal around the
expected value, Lambda cold starts as a coin flip (`cold_prob` × the full
`cold` cost) — and reports p50 / p90 / p95 / p99 next to the mean, a `p99`
column in the hop table, and a **Tail risk** section naming the hops that
drive p99 − p50 (cold starts, cross-region variance). `--seed` makes a run
reproducible. numpy is optional (`pip install -e ".[montecarlo]"`) and makes it
much faster; without it a pure-Python sampler is used. Spread is configurable in
the profile (`variance.distance_sigma`, `variance.processing_sigma`, and a
per-subtype `sigma`).

## Dashboard — `-o html` and `iacsim view`

Three commands produce the same page:

```
iacsim run  ./infra -o html                       # .iacsim/report.html
iacsim diff ./before ./after -o html              # <after>/.iacsim/diff.html
iacsim view ./infra                               # renders .iacsim/index.html from report.json, serves it, opens the browser
                                                  #   (--no-open to skip, --port to pin one; the pipeline runs only when report.json is stale)
```

`report.html` is one self-contained file — the report inlined as a JSON blob, no server,
no library, no build step — so it opens from `file://` and is the file to send someone.
Every number on it comes from the same schema-2 `report.json` the `json` reporter writes;
nothing is computed in the browser that the engine did not already compute.

What the page shows, in the order you meet it:

- **Header** — source format, node / edge counts, when it was generated, and the profile rung
  (`defaults → calibrated.yaml (cloudwatch, 24h)`, the same line the terminal prints); scenario
  tabs; toggles for network nodes, every inferred edge, all hops, fit to width.
- **KPI strip** — total (mean when sampled), p50 / p95 / p99 when present, samples,
  declared | inferred, the shape line; with `--walker load`, a "breaks at ~N users · resource"
  tile that jumps to the capacity band.
- **The map, first** — nodes in region / AZ swimlanes, request order left to right. The selected
  scenario's path carries numbered hop badges; arrow width is that hop's milliseconds, colour is
  its dominant category (distance / processing / cold start), the slowest hop is marked, and
  each node on the path shows its share as a bar. Hover an arrow or node for the breakdown;
  click one for the **drawer** — its hops with evidence, the finding that names it, the
  recommendations that touch it, its capacity row.
- **Right rail** — *Where the time goes* (one 100 % stacked bar, A1 / A2 / A3 rows), *Top
  bottlenecks* (click to select on the map), *Recommendations* (saves ~N ms, with the "based on"
  reasoning folded under each).
- **Hop table** — path order, critical-path marker, group, ms, p50 / p99 when sampled, a mini
  breakdown bar, and the evidence **unclipped**; *Critical path* and *Tail risk* when those
  findings exist; warnings last.
- **Capacity band** ("users until it breaks", only after `--walker load`) — the utilisation
  heatmap per resource and user count, p99 by users per scenario with the threshold rule and
  saturation markers, and *What breaks first* with the IaC attribute that raises the ceiling.

`diff.html` draws the *after* graph with added, moved and removed nodes ringed (a moved node
carries "region: a → b"), before / after bars per scenario, the hops that changed, and the
recommendations that appeared or disappeared.

Keyboard: the scenario tabs are a tablist (arrow keys), nodes and edges are focusable, Escape
closes the drawer, a skip link leads to the hop table; dark mode and reduced motion follow the
system. `html` is not in the default `report.outputs` — the page is ~400 KB per run — so ask
for it with `-o html` or put it in `iacsim.yaml`.

Real samples live in `examples/dashboard/` (`gcp-web`, `foosh-load` with the capacity band,
`classic-web-diff`) — regenerated tool output, held equal by a test, not mock data.
`make dashboard` opens one.

## CloudFormation / CDK / SAM input

Anything that ends up as a CloudFormation template works the same way —
the format is auto-detected (or force it with `--format cloudformation`):

```
cd my-cdk-app && cdk synth                          # writes cdk.out/<Stack>.template.json
iacsim graph cdk.out/MyStack.template.json          # a file, or the cdk.out directory
iacsim run   cdk.out --region ap-south-1            # region: --region, iacsim.yaml parsers.cloudformation.region,
                                                    #         else the first region literal in the template, else us-east-1
sam build && iacsim run .aws-sam/build/template.yaml
iacsim diff examples/foosh-serverless examples/foosh-cfn --align-by label   # Terraform twin vs the real CDK output
```

Inside, CloudFormation resources are made Terraform-shaped in one place
(`parsers/cloudformation/canonical.py`: `AWS::Lambda::Function` →
`aws_lambda_function`, `MemorySize` → `memory_size`, `Ref`/`Fn::GetAtt` →
the same `${address.attr}` placeholders) so the normaliser and every
inference rule run unchanged on both formats.

## GCP input

Point iacsim at a directory of `google_*` Terraform. There is no config file and no flag:
the provider is auto-detected from the resource-type prefixes, and an explicit
`provider: gcp` in `iacsim.yaml` still wins.

```
iacsim graph ./gcp-infra                     # google_* types → the gcp normaliser
iacsim run   ./gcp-infra --region us-central1
```

`google` and `google-beta` are the same provider here — both declare `google_*` resource
types, so a `google-beta` block (and a resource that only exists in beta) is read like any
other. `.tf`, `.tofu` and `.tf.json`, modules, `for_each` / `count` and `templatefile()` all
work exactly as they do for AWS: the parser layer was never AWS-specific.

**What it covers (M11):** the `gcp` normaliser (97 `google_*` types), the load-balancer
chain drawn as its real resources (forwarding rule → proxy → URL map → backend service → NEG,
priced as one device), Cloud Run / Functions cold starts, and five inference rules — the LB
chain, IAM bindings via service accounts, Eventarc, Pub/Sub push, and Workflows YAML. Proven
on seven unmodified public fixtures under `examples/real-world/gcp-*` and the controlled
`examples/gcp-web` / `gcp-web-bad` pair (`iacsim diff` → +398 ms for a Cloud SQL one region
away). Not yet: capacity modelling (`--walker load`) and `calibrate` for GCP. Design notes:
`docs/gcp/`, `DECISIONS.md` §10, `TIMELINE.md`.

## Calibrate — replace guesses with measurements

Every number in a report starts as a public average from `latency/defaults.yaml`
(rung 0) or your own `--profile` (rung 1). `iacsim calibrate` builds **rung 2**:
it asks a metric source how long each Lambda, table, load balancer and API in
your graph *actually* took, and writes a profile you pass straight back in.

```
iacsim calibrate ./infra                     # source + window from iacsim.yaml, writes ./infra/calibrated.yaml
iacsim calibrate ./infra --source cloudwatch --window 24h --region ap-south-1 --out measured.yaml
iacsim calibrate ./infra --dry-run           # just the coverage table
iacsim run ./infra --profile measured.yaml   # header now says:  profile  defaults → measured.yaml (cloudwatch, 24h)
```

What it prints: a coverage table — every node measured (with the keys it got,
and which keys were filled from defaults), every node skipped and why
(`no data in window`, `source does not support rds`, `no physical name`), and
how many measurable nodes stay on defaults. Nothing is guessed: a resource
that did not run in the window keeps its default and the report says so.

What CloudWatch is asked (read-only; `cloudwatch:GetMetricData` and
`cloudwatch:ListMetrics` — AWS's `ReadOnlyAccess` policy covers it; a full
run over 30 resources costs a fraction of a cent):

| resource | metrics | profile keys |
|---|---|---|
| Lambda | `Duration` p50/p99, `InitDuration` p50 + count, `Invocations` | `warm`, `cold`, `cold_prob`, `sigma` |
| DynamoDB | `SuccessfulRequestLatency` p50 per operation | `read`, `write` |
| RDS | `ReadLatency`, `WriteLatency` | `read`, `write` |
| ALB | `TargetResponseTime` p50 | `route` |
| API Gateway | `Latency` − `IntegrationLatency` | `route` |

Lambda `Duration` includes your code and every third-party call it makes, so
calibration is also how the application layer enters the model — per function,
with no code analysis.

The source is **company-specific and pluggable**. Pick and configure it in
`iacsim.yaml`; credentials come from the normal AWS chain (`aws configure`,
`AWS_PROFILE`, an instance role), never from the file:

```yaml
calibrate:
  source: cloudwatch            # or fake, or any plugin below
  window: 7d
  out: calibrated.yaml
  sources:
    cloudwatch: { region: us-east-1, aws_profile: readonly }
    fake:       { fixture: calibrate-fixture.yaml }   # canned numbers — demos, tests, dry runs
```

Another monitoring system is a 15-line file in `plugins/`:

```python
# plugins/datadog_source.py
import os, requests
from iacsim.core.interfaces import METRIC_SOURCES, MetricSource

@METRIC_SOURCES.register("datadog")
class DatadogSource(MetricSource):
    def supports(self, kind):
        return kind in ("lambda", "dynamodb")

    def measure(self, kind, name, window, region=None):
        key = os.environ[self.options["api_key_env"]]           # iacsim.yaml names the env var, never the key
        p50 = requests.get(f"https://api.{self.options['site']}/…", headers={"DD-API-KEY": key}).json()
        return {"warm": p50["duration"]} if kind == "lambda" else {"read": p50["latency"]}
```

```yaml
calibrate:
  source: datadog
  sources:
    datadog: { site: datadoghq.eu, api_key_env: DD_API_KEY }
```

The written file is an overlay in the `defaults.yaml` schema: `by_label`
entries keyed by physical AWS name (so one file serves the Terraform and the
CloudFormation graph of the same stack), `per_resource` by node id only when a
name is ambiguous, and `meta` recording source, window and time so every
report header shows which rung it is on. Try it without an account:

```
iacsim calibrate examples/foosh-serverless      # fake source, examples/foosh-serverless/calibrate-fixture.yaml
iacsim run examples/foosh-serverless --profile examples/foosh-serverless/calibrated.yaml --walker monte_carlo
```

A live CloudWatch run against your own account is the same command with
`source: cloudwatch` after `aws configure` with a read-only key — it has not
been exercised in this repo (no account here), see `TIMELINE.md`.

## Layout — one folder per stage, one file per implementation

```
iacsim/
  core/          IR models, interfaces + registries, config, pipeline
  parsers/       terraform/{hcl_expr,evaluator,loader,parser}.py  cloudformation/{template,intrinsics,canonical,parser}.py → RawResources
  graph/         normalisers/aws.py  inference/<rule>.py (8 rules) → InfraGraph
  scenarios/     yaml_file.py  inferred.py            → [Scenario]
  latency/       defaults.yaml  profile.py  rules/  calibrate/{calibrator,writer,fake,cloudwatch,cloudwatch_queries}.py
  simulator/     traversal.py (shared planner + evaluator)  walkers/expected_value.py  monte_carlo.py
  analyzer/      per_hop  per_node  per_category  critical_path  recommendations  tail_risk  saturation
  reporter/      text  markdown  json  html_ (the dashboard: report_payload() inlined into viewer/index.html)
  viewer/        index.html (the dashboard template — one placeholder, no CDN, no build) + serve helpers for `iacsim view`
  cli.py  differ.py
examples/
  modules/       shared Terraform modules: network, load_balancer, compute, database
  classic-web/   ALB → EC2 ×2 → RDS, one region
  classic-web-bad/  same, RDS in eu-west-1 over VPC peering
  foosh-serverless/ API GW → Lambdas → Step Functions → DynamoDB / S3 (hand-written Terraform twin)
  foosh-cfn/     the real `cdk synth` template of the same stack (secrets redacted) — M5 correctness check
  dashboard/     real `-o html` output (gcp-web, foosh-load, classic-web-diff) — the shareable sample, held by a test
plugins/         drop-in extensions (see plugins/README.md)
tests/
```

Every choice is an extension point: an ABC in `core/interfaces.py`, a
registry, and a key in `iacsim.yaml`. Milestone markers `[M1]`…`[M7]` in
module docstrings say what is built and what is next.

## Capacity — how many users until it breaks

Everything above gives one request an empty road. `--walker load` adds traffic:

```
iacsim run examples/foosh-serverless --walker load --profile examples/foosh-serverless/calibrated.yaml
```

reads `load.yaml` next to `scenarios.yaml`:

```yaml
users: [100, 500, 1000, 2000, 5000, 10000]     # sweep
per_user:
  start_workflow: { every: 2m }
  poll_status:    { every: 2s, while: running }   # only while that user has a run in flight
  save_workflow:  { every: 30s }
workflow_mix:                                    # what a started run looks like
  - { scenario: run_workflow_3_nodes, share: 0.6 }
  - { scenario: run_workflow_1_video, share: 0.2 }
  - { scenario: run_workflow_10_text, share: 0.2 }
thresholds: { p99_ms: 2000, utilisation: 0.8 }
```

and prints, before the per-scenario briefs, a capacity brief: utilisation of
every resource at each user count, p99 per scenario at each user count, and
**what breaks first** — the resource, the user count (exact, because
utilisation is linear in users), why (which scenario feeds it, how long it
holds a slot), and what raises the ceiling, citing the IaC attribute
(`reserved_concurrent_executions=100 on module.api…`).

Capacity comes from the IaC where it is declared — Lambda reserved
concurrency, ECS `desired_count`, RDS `instance_class`, DynamoDB provisioned
throughput, Step Functions Map `MaxConcurrency` — and from the `capacity:`
block in `latency/defaults.yaml` for account-level limits (Lambda account
concurrency 1000, on-demand DynamoDB 40k rps, …). Override either with
`--profile`.

Assumptions, stated: analytic M/M/c queueing per resource (Poisson arrivals,
exponential service — no discrete-event simulation); a Lambda holds its
concurrency slot for its whole invocation including everything it waits on
downstream; unreserved Lambdas share one pool of `account_concurrency − Σ
reserved`; p99 ≈ 1.3 × the no-contention expected value plus p99 queue waits
(`simulation.tail_factor`). Fan-out through a Map now costs
`ceil(count / MaxConcurrency)` waves, which also sharpens the plain walkers.

