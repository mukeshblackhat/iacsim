# Sixteen Lambdas, one module call each via for_each. Memory / timeout come
# from Foosh's config/base.json. The api Lambda additionally gets the state
# machine ARN and permission to start executions (see step_functions.tf).

locals {
  common_env = merge(local.table_env, local.bucket_env, {
    ENVIRONMENT               = local.suffix
    CREDITS_ENABLED           = "true"
    CREDIT_BUFFER_PERCENTAGE  = "0.2"
    STATE_MACHINE_NAME        = "AsyncWorkflow${title(local.suffix)}StateMachine"
  })

  # name => { handler, memory, timeout }   — from LambdaFactory.create_all_lambdas + base.json.
  # `function_name` overrides the "<prefix>-<key>-<suffix>" default where the
  # factory's function_type differs from our key (html_template → html_template_processor).
  workers = {
    text_enhancement   = { handler = "lambdas.enhanced_text_models.lambda_handler",          memory = 512,  timeout = 300 }
    image_generation   = { handler = "lambdas.enhanced_image_models.lambda_handler",         memory = 1024, timeout = 900 }
    image_to_image     = { handler = "lambdas.image_to_image_models.lambda_handler",         memory = 1024, timeout = 300 }
    video_creation     = { handler = "lambdas.enhanced_video_models.lambda_handler",         memory = 3008, timeout = 900 }
    lipsync_processing = { handler = "lambdas.enhanced_lipsync_models.lambda_handler",       memory = 2048, timeout = 900 }
    router             = { handler = "lambdas.router_processor.lambda_handler",              memory = 512,  timeout = 120 }
    text_input         = { handler = "lambdas.input_processors.text_input_handler",          memory = 512,  timeout = 120 }
    text_iterator      = { handler = "lambdas.input_processors.text_iterator_handler",       memory = 512,  timeout = 120 }
    image_input        = { handler = "lambdas.input_processors.image_input_handler",         memory = 512,  timeout = 120 }
    video_input        = { handler = "lambdas.input_processors.video_input_handler",         memory = 512,  timeout = 120 }
    html_template      = { handler = "lambdas.html_template_processor.lambda_handler",       memory = 512,  timeout = 120,
                           function_name = "${local.prefix}-html-template-processor-${local.suffix}" }
    parser             = { handler = "lambdas.step_functions.workflow_parser.lambda_handler", memory = 512,  timeout = 120 }
    input_preparer     = { handler = "lambdas.step_functions.input_preparer.lambda_handler",  memory = 512,  timeout = 120 }
    output_updater     = { handler = "lambdas.step_functions.output_updater.lambda_handler",  memory = 512,  timeout = 120 }
    finalizer          = { handler = "lambdas.step_functions.workflow_finalizer.lambda_handler", memory = 512, timeout = 120 }
  }
}

module "worker" {
  source   = "../modules/lambda_function"
  for_each = local.workers

  function_name     = try(each.value.function_name, "${local.prefix}-${replace(each.key, "_", "-")}-${local.suffix}")
  handler           = each.value.handler
  memory_mb         = each.value.memory
  timeout_seconds   = each.value.timeout
  environment       = local.common_env
  policy_statements = [local.dynamodb_policy, local.s3_policy]
}

module "api" {
  source = "../modules/lambda_function"

  function_name        = "${local.prefix}-api-${local.suffix}"
  description          = "API Gateway handler for workflow orchestration"
  handler              = "lambdas.api_handler.lambda_handler"
  memory_mb            = 1024
  timeout_seconds      = 300
  reserved_concurrency = 100
  environment = merge(local.common_env, {
    STATE_MACHINE_ARN = aws_sfn_state_machine.workflow.arn
  })
  policy_statements = [
    local.dynamodb_policy,
    local.s3_policy,
    {
      Effect   = "Allow"
      Action   = ["states:StartExecution", "states:DescribeExecution", "states:StopExecution", "states:ListExecutions"]
      Resource = [aws_sfn_state_machine.workflow.arn, "${aws_sfn_state_machine.workflow.arn}:*"]
    },
  ]
}
