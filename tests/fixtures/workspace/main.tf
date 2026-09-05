locals {
  accounts = { default = "111", prod = "999" }
}
provider "aws" {
  region = "us-east-1"
}
resource "aws_s3_bucket" "b" {
  bucket = "logs-${terraform.workspace}-${local.accounts[terraform.workspace]}"
}
