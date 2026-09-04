output "api_endpoint" { value = aws_api_gateway_stage.main.invoke_url }
output "state_machine_arn" { value = aws_sfn_state_machine.workflow.arn }
output "output_bucket" { value = aws_s3_bucket.outputs.bucket }
