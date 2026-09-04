# Dynamic workflow state machine. The real definition is ~200 KB of generated
# ASL (one Execute/UpdateOutputs/Delay block per node type); the template in
# step_functions/workflow.asl.json keeps the same shape with the Lambda ARNs
# injected — this is what iacsim's step_functions rule reads for call order.

resource "aws_iam_role" "sfn" {
  name = "${local.prefix}-sfn-${local.suffix}"
  assume_role_policy = jsonencode({
    Version   = "2012-10-17"
    Statement = [{ Effect = "Allow", Action = "sts:AssumeRole", Principal = { Service = "states.amazonaws.com" } }]
  })
}

resource "aws_iam_role_policy" "sfn_invoke" {
  role = aws_iam_role.sfn.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = ["lambda:InvokeFunction"]
      Resource = [for w in module.worker : w.arn]
    }]
  })
}

resource "aws_cloudwatch_log_group" "sfn" {
  name              = "/aws/vendedlogs/states/AsyncWorkflow${title(local.suffix)}StateMachine"
  retention_in_days = 30
}

resource "aws_sfn_state_machine" "workflow" {
  name     = "AsyncWorkflow${title(local.suffix)}StateMachine"
  role_arn = aws_iam_role.sfn.arn
  type     = "STANDARD"

  definition = templatefile("${path.module}/step_functions/workflow.asl.json", {
    parser_arn           = module.worker["parser"].arn
    input_preparer_arn   = module.worker["input_preparer"].arn
    output_updater_arn   = module.worker["output_updater"].arn
    finalizer_arn        = module.worker["finalizer"].arn
    text_enhancement_arn = module.worker["text_enhancement"].arn
    image_generation_arn = module.worker["image_generation"].arn
    image_to_image_arn   = module.worker["image_to_image"].arn
    video_creation_arn   = module.worker["video_creation"].arn
    lipsync_arn          = module.worker["lipsync_processing"].arn
    router_arn           = module.worker["router"].arn
    text_iterator_arn    = module.worker["text_iterator"].arn
    html_template_arn    = module.worker["html_template"].arn
    max_concurrency      = 5
  })

  logging_configuration {
    log_destination        = "${aws_cloudwatch_log_group.sfn.arn}:*"
    include_execution_data = true
    level                  = "ALL"
  }
}
