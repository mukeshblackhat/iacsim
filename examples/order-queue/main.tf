# order-queue — the smallest event-driven shape.
#
#   internet → API Gateway {proxy+} → api Lambda ──publish──► SQS orders queue
#                                                                  │ event source mapping
#                                                                  ▼
#                                                         worker Lambda ──write──► DynamoDB orders
#
# Exercises PUBLISH / CONSUME edges (env var + IAM for the publish side,
# aws_lambda_event_source_mapping for the consume side) and a dead-letter queue.

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
  name = "order-queue"
}
