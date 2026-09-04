# classic-web — the "good" baseline.
#
#   internet → ALB (2 AZs) → EC2 web (one per AZ) → RDS Postgres
#
# Everything in one region. Compare with ../classic-web-bad, which is the
# same modules with the database moved to another region.

terraform {
  required_version = ">= 1.5"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
}

provider "aws" {
  region = var.region
}

locals {
  name = "classic-web"
  azs  = ["${var.region}a", "${var.region}b"]
}

module "network" {
  source             = "../modules/network"
  name               = local.name
  availability_zones = local.azs
}

module "load_balancer" {
  source          = "../modules/load_balancer"
  name            = local.name
  vpc_id          = module.network.vpc_id
  subnet_ids      = module.network.public_subnet_ids
  certificate_arn = var.certificate_arn
}

module "compute" {
  source                = "../modules/compute"
  name                  = local.name
  vpc_id                = module.network.vpc_id
  subnets_by_az         = module.network.public_subnets_by_az
  ami_id                = var.ami_id
  alb_security_group_id = module.load_balancer.security_group_id
  target_group_arn      = module.load_balancer.target_group_arn
  db_host               = module.database.address
  db_name               = module.database.db_name
}

module "database" {
  source = "../modules/database"
  providers = { aws = aws }

  name                       = local.name
  vpc_id                     = module.network.vpc_id
  subnet_ids                 = module.network.private_subnet_ids
  availability_zone          = local.azs[0]
  password                   = var.db_password
  allowed_security_group_ids = [module.compute.security_group_id]
}
