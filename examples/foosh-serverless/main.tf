# foosh-serverless — Terraform twin of ~/Foosh/async-workflows (CDK, staging).
#
#   internet → API Gateway {proxy+} → api Lambda
#                                        ├─ reads/writes 10 DynamoDB tables
#                                        ├─ starts Step Functions execution
#                                        └─ signs S3 URLs
#   Step Functions: Parse → Map( PrepareInput → Execute<node type> → UpdateOutputs ) → Finalize
#   Every worker Lambda reads/writes DynamoDB + the outputs bucket.
#
# Single region. Nothing here is "wrong" on purpose — it is the real shape,
# so the interesting question is which of the many hops dominate.
#
# Files: lambdas.tf, dynamodb.tf, s3.tf, api_gateway.tf, step_functions.tf.

terraform {
  required_version = ">= 1.5"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
}

provider "aws" {
  region = var.region
}

locals {
  prefix = "async-workflow"
  suffix = var.environment
}
