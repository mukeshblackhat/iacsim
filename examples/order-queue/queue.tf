# Orders queue with a dead-letter queue after three failed receives.

resource "aws_sqs_queue" "dlq" {
  name                      = "${local.name}-orders-dlq"
  message_retention_seconds = 1209600
}

resource "aws_sqs_queue" "orders" {
  name                       = "${local.name}-orders"
  visibility_timeout_seconds = 60
  redrive_policy = jsonencode({
    deadLetterTargetArn = aws_sqs_queue.dlq.arn
    maxReceiveCount     = 3
  })
}

# The worker polls the queue: this is the CONSUME edge.
resource "aws_lambda_event_source_mapping" "orders_to_worker" {
  event_source_arn = aws_sqs_queue.orders.arn
  function_name    = module.worker.name
  batch_size       = 10
}
