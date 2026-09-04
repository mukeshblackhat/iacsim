# classic-web-bad — same modules as ../classic-web, but the database lives in
# a second region (eu-west-1) reached over VPC peering. Every DB query now
# pays a transatlantic round-trip. This is the fixture `iacsim diff` should
# catch: the only change is where the database sits.

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

provider "aws" {
  alias  = "db"
  region = var.db_region
}

locals {
  name   = "classic-web"
  azs    = ["${var.region}a", "${var.region}b"]
  db_azs = ["${var.db_region}a", "${var.db_region}b"]
}

module "network" {
  source             = "../modules/network"
  name               = local.name
  availability_zones = local.azs
}

# A second VPC in the database region, peered with the web VPC.
module "db_network" {
  source             = "../modules/network"
  providers          = { aws = aws.db }
  name               = "${local.name}-db"
  cidr_block         = "10.1.0.0/16"
  availability_zones = local.db_azs
}

resource "aws_vpc_peering_connection" "web_to_db" {
  vpc_id      = module.network.vpc_id
  peer_vpc_id = module.db_network.vpc_id
  peer_region = var.db_region
}

resource "aws_vpc_peering_connection_accepter" "db" {
  provider                  = aws.db
  vpc_peering_connection_id = aws_vpc_peering_connection.web_to_db.id
  auto_accept               = true
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
  source    = "../modules/database"
  providers = { aws = aws.db }

  name              = local.name
  vpc_id            = module.db_network.vpc_id
  subnet_ids        = module.db_network.private_subnet_ids
  availability_zone = local.db_azs[0]
  password          = var.db_password
  # Security groups cannot be referenced across regions; allow the web CIDR instead.
  allowed_security_group_ids = []
}
