variable "function_name" { type = string }
variable "handler" { type = string }
variable "description" {
  type    = string
  default = null
}
variable "package_path" {
  type    = string
  default = "build/lambda.zip"
}
variable "memory_mb" {
  type    = number
  default = 512
}
variable "timeout_seconds" {
  type    = number
  default = 120
}
variable "reserved_concurrency" {
  type    = number
  default = -1
}
variable "environment" {
  type    = map(string)
  default = {}
}
variable "policy_statements" {
  type        = list(any)
  default     = []
  description = "Extra IAM statements (DynamoDB / S3 / states:*) — these are what iacsim's iam_policy rule reads"
}
