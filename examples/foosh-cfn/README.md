# foosh-cfn

The **real** CloudFormation template for the Foosh async-workflows staging
stack, produced by `cdk synth` from `~/Foosh/async-workflows/infrastructure`
on 2026-09-04 (CDK source as of Jul 2026: 16 Lambdas, 10 DynamoDB tables,
1 state machine, 1 bucket, API Gateway).

Secrets in Lambda environment variables (`*KEY*`, `*SECRET*`, `*TOKEN*`,
`*PASSWORD*`) are replaced with `REDACTED`; the AWS account id is replaced
with `123456789012`. Nothing else is edited.

It is the correctness check for the CloudFormation adapter: the graph built
from this file must match the graph built from the hand-written Terraform
twin in `../foosh-serverless`:

```
iacsim graph examples/foosh-cfn
iacsim diff  examples/foosh-serverless examples/foosh-cfn --align-by label
```
