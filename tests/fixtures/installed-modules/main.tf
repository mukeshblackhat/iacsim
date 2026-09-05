provider "aws" {
  region = "us-east-1"
}
module "vpc" {
  source  = "terraform-aws-modules/vpc/aws"
  version = "5.0.0"
  name    = "net"
}
module "missing" {
  source = "terraform-aws-modules/eks/aws"
}
