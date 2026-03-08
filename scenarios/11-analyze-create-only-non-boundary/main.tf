# Scenario 11: Create-Only Non-Boundary Plan
#
# All resources are creates with no IAM or network control points (no
# aws_security_group, aws_iam_*). This exercises the blast radius
# GREENFIELD_MULTIPLIER path (0.3x score discount) and verifies that
# find_resource_prior_fixes() surfaces tribal warnings even without the
# Phase 2 BFS (L2 gate does not fire — no boundary resources, no deletes).
#
# Expected: LOW severity score, tribal warnings present if fix DB has records
# for aws_s3_bucket or aws_instance.

locals {
  tags = {
    Project = var.name_prefix
    Owner   = "fixdoc"
  }
}

resource "aws_vpc" "main" {
  cidr_block           = "10.110.0.0/16"
  enable_dns_support   = true
  enable_dns_hostnames = true
  tags                 = merge(local.tags, { Name = "${var.name_prefix}-vpc" })
}

resource "aws_subnet" "public_a" {
  vpc_id            = aws_vpc.main.id
  cidr_block        = "10.110.1.0/24"
  availability_zone = "us-east-1a"
  tags              = merge(local.tags, { Name = "${var.name_prefix}-subnet-a" })
}

resource "aws_subnet" "public_b" {
  vpc_id            = aws_vpc.main.id
  cidr_block        = "10.110.2.0/24"
  availability_zone = "us-east-1b"
  tags              = merge(local.tags, { Name = "${var.name_prefix}-subnet-b" })
}

resource "aws_s3_bucket" "data" {
  bucket = "${var.name_prefix}-data"
  tags   = merge(local.tags, { Name = "${var.name_prefix}-data" })
}

resource "aws_s3_bucket" "logs" {
  bucket = "${var.name_prefix}-logs"
  tags   = merge(local.tags, { Name = "${var.name_prefix}-logs" })
}

# EC2 instance with no IAM profile or security group (uses VPC default SG)
# aws_instance is NOT a control point, so no Phase 2 BFS fires
resource "aws_instance" "app" {
  ami           = "ami-12345678"
  instance_type = "t3.micro"
  subnet_id     = aws_subnet.public_a.id
  tags          = merge(local.tags, { Name = "${var.name_prefix}-app" })
}
