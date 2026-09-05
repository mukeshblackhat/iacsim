# api: accepts the order and publishes it. worker: consumes and persists it.

module "api" {
  source = "../modules/lambda_function"

  function_name = "${local.name}-api"
  handler       = "app.api.handler"
  memory_mb     = 512
  environment = {
    QUEUE_URL = aws_sqs_queue.orders.url        # env_var rule: api → queue (publish)
  }
  policy_statements = [{
    Effect   = "Allow"
    Action   = ["sqs:SendMessage"]              # iam_policy rule: the same edge, PUBLISH
    Resource = [aws_sqs_queue.orders.arn]
  }]
}

module "worker" {
  source = "../modules/lambda_function"

  function_name = "${local.name}-worker"
  handler       = "app.worker.handler"
  memory_mb     = 1024
  environment = {
    TABLE_NAME = module.orders.name             # env_var rule: worker → table (read by default)
  }
  policy_statements = [
    {
      Effect   = "Allow"
      Action   = ["sqs:ReceiveMessage", "sqs:DeleteMessage", "sqs:GetQueueAttributes"]
      Resource = [aws_sqs_queue.orders.arn]
    },
    {
      Effect   = "Allow"
      Action   = ["dynamodb:PutItem", "dynamodb:GetItem"]   # iam_policy rule: ops [read, write]
      Resource = [module.orders.arn]
    },
  ]
}
