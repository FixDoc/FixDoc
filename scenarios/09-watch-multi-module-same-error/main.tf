locals {
  tags = { Project = var.name_prefix, Owner = "fixdoc" }
}

resource "aws_vpc" "main" {
  cidr_block = "10.90.0.0/16"
  tags       = merge(local.tags, { Name = "${var.name_prefix}-vpc" })
}

module "app_a" {
  source      = "./modules/app"
  name_prefix = "${var.name_prefix}-a"
  vpc_id      = aws_vpc.main.id
  cidr_block  = "10.0.0.0/32"  # invalid CIDR
}

module "app_b" {
  source      = "./modules/app"
  name_prefix = "${var.name_prefix}-b"
  vpc_id      = aws_vpc.main.id
  cidr_block  = "10.0.0.0/32"  # same invalid CIDR, different module
}
