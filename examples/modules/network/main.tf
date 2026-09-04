# network — one VPC, two AZs, a public subnet pair for the web tier and a
# private subnet pair for the database tier.

resource "aws_vpc" "this" {
  cidr_block = var.cidr_block
  tags       = { Name = var.name }
}

resource "aws_subnet" "public" {
  for_each = toset(var.availability_zones)

  vpc_id            = aws_vpc.this.id
  availability_zone = each.value
  cidr_block        = cidrsubnet(var.cidr_block, 8, index(var.availability_zones, each.value))
  tags              = { Name = "${var.name}-public-${each.value}", Tier = "public" }
}

resource "aws_subnet" "private" {
  for_each = toset(var.availability_zones)

  vpc_id            = aws_vpc.this.id
  availability_zone = each.value
  cidr_block        = cidrsubnet(var.cidr_block, 8, 10 + index(var.availability_zones, each.value))
  tags              = { Name = "${var.name}-private-${each.value}", Tier = "private" }
}
