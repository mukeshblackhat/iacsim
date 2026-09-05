provider "aws" {
  region = "us-east-1"
}
locals {
  fns = { alpha = { mem = 128 }, beta = { mem = 512 } }
}
module "worker" {
  source = "./child"
  count  = 2
  index  = count.index
}
resource "aws_lambda_function" "fn" {
  for_each      = local.fns
  function_name = each.key
  memory_size   = each.value.mem
  handler       = "h"
  runtime       = "python3.12"
}
