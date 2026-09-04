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
```

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
  simulator/     walkers/expected_value.py  monte_carlo.py
  analyzer/      per_hop  per_node  per_category  critical_path
  reporter/      text  json
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
