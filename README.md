# iacsim — IaC → latency simulation

Reads Terraform (or CloudFormation), builds a graph of your infrastructure,
simulates a request through it, and tells you where the milliseconds go —
before you deploy. See `SPEC.md` for the design, `TIMELINE.md` for status and
milestones, and `problem statment.md` for the why.

```
pip install -e ".[dev]"
iacsim plugins                       # what is registered
iacsim graph examples/classic-web    # M1
iacsim run   examples/classic-web    # M2+
iacsim diff  examples/classic-web examples/classic-web-bad   # M4: what moved, A1/A2/A3 shift, changed hops
iacsim diff  ./main ./pr --fail-on-regression 50ms           # CI: exit 2 if any scenario grows > 50 ms (or 10%)
iacsim diff  ./before ./after --scenario checkout -o markdown  # one scenario, PR-comment markdown → .iacsim/diff.md
iacsim run   examples/foosh-serverless --walker monte_carlo --samples 10000 --seed 1   # M6: p50/p95/p99 + tail risk
iacsim view  examples/classic-web-bad                         # M6: graph viewer in the browser
```

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

## Graph viewer — `iacsim view`

`iacsim view ./infra` runs the pipeline if needed, drops a single self-contained
`index.html` into `.iacsim/` next to `report.json`, serves the folder on
`127.0.0.1` and opens the browser (`--no-open` to skip, `--port` to pin one).
The page draws every node in region / AZ swimlanes left-to-right in request
order, edges with width ∝ expected latency and colour = dominant cost category
(distance / processing / cold start); click a node or edge for its placement,
attributes, evidence and breakdown; pick a scenario to overlay its path with
hop numbers and see its total (and p50/p99 when sampled). Network nodes are
hidden behind a toggle. No CDN, no build step — it reads only `report.json`
(schema 2) or a bare `graph.json`, and offers a file picker when opened from
`file://`.

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

## Layout — one folder per stage, one file per implementation

```
iacsim/
  core/          IR models, interfaces + registries, config, pipeline
  parsers/       terraform/{hcl_expr,evaluator,loader,parser}.py  cloudformation/{template,intrinsics,canonical,parser}.py → RawResources
  graph/         normalisers/aws.py  inference/<rule>.py (8 rules) → InfraGraph
  scenarios/     yaml_file.py  inferred.py            → [Scenario]
  latency/       defaults.yaml  profile.py  rules/  calibrate/
  simulator/     traversal.py (shared planner + evaluator)  walkers/expected_value.py  monte_carlo.py
  analyzer/      per_hop  per_node  per_category  critical_path  recommendations  tail_risk
  reporter/      text  markdown  json
  viewer/        index.html (self-contained graph viewer) + serve helpers
  cli.py  differ.py
examples/
  modules/       shared Terraform modules: network, load_balancer, compute, database
  classic-web/   ALB → EC2 ×2 → RDS, one region
  classic-web-bad/  same, RDS in eu-west-1 over VPC peering
  foosh-serverless/ API GW → Lambdas → Step Functions → DynamoDB / S3 (hand-written Terraform twin)
  foosh-cfn/     the real `cdk synth` template of the same stack (secrets redacted) — M5 correctness check
plugins/         drop-in extensions (see plugins/README.md)
tests/
```

Every choice is an extension point: an ABC in `core/interfaces.py`, a
registry, and a key in `iacsim.yaml`. Milestone markers `[M1]`…`[M7]` in
module docstrings say what is built and what is next.
