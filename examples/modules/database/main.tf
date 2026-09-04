# database — single RDS Postgres instance. Takes its own provider so the
# caller can place it in a different region (see classic-web-bad).

terraform {
  required_providers {
    aws = {
      source                = "hashicorp/aws"
      configuration_aliases = [aws]
    }
  }
}

resource "aws_security_group" "this" {
  name   = "${var.name}-db"
  vpc_id = var.vpc_id

  ingress {
    from_port       = 5432
    to_port         = 5432
    protocol        = "tcp"
    security_groups = var.allowed_security_group_ids
  }
}

resource "aws_db_subnet_group" "this" {
  name       = var.name
  subnet_ids = var.subnet_ids
}

resource "aws_db_instance" "this" {
  identifier             = var.name
  engine                 = "postgres"
  engine_version         = "16"
  instance_class         = var.instance_class
  allocated_storage      = 20
  db_name                = var.db_name
  username               = "app"
  password               = var.password
  db_subnet_group_name   = aws_db_subnet_group.this.name
  vpc_security_group_ids = [aws_security_group.this.id]
  availability_zone      = var.availability_zone
  multi_az               = false
  skip_final_snapshot    = true
}
