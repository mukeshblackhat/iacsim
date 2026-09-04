# lambda_function — one Python Lambda with its own role. Mirrors Foosh's
# LambdaFactory: every function gets the same table/bucket env vars, and the
# caller attaches extra policies.

resource "aws_iam_role" "this" {
  name = "${var.function_name}-role"

  assume_role_policy = jsonencode({
    Version   = "2012-10-17"
    Statement = [{ Effect = "Allow", Action = "sts:AssumeRole", Principal = { Service = "lambda.amazonaws.com" } }]
  })
}

resource "aws_iam_role_policy_attachment" "basic" {
  role       = aws_iam_role.this.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

resource "aws_iam_role_policy" "extra" {
  count  = length(var.policy_statements) > 0 ? 1 : 0
  name   = "${var.function_name}-extra"
  role   = aws_iam_role.this.id
  policy = jsonencode({ Version = "2012-10-17", Statement = var.policy_statements })
}

resource "aws_lambda_function" "this" {
  function_name = var.function_name
  description   = var.description
  role          = aws_iam_role.this.arn
  runtime       = "python3.10"
  handler       = var.handler
  filename      = var.package_path
  memory_size   = var.memory_mb
  timeout       = var.timeout_seconds

  reserved_concurrent_executions = var.reserved_concurrency

  environment {
    variables = var.environment
  }
}
