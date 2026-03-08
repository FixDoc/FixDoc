# Scenario 13: Replace + Delete Heavy Plan
#
# Baseline: VPC (10.130.0.0/16) + subnets + SG + EC2 + extra S3 bucket.
# Changed (policy_mode=changed):
#   - VPC CIDR changes to 10.131.0.0/16 (immutable field → forces VPC replace)
#   - VPC replace cascades to subnet, SG, EC2 (all replace)
#   - Extra S3 bucket is removed (delete action)
#
# Expected blast radius behavior:
#   - has_destructive=True → L2 BFS fires, history_matches can populate
#   - Multiple replace + delete actions → HIGH severity score
#   - max_warnings cap tested if fix DB has relevant records
#
# Run with: run_apply_then_plan_update_boundary
# Toggle: policy_mode (baseline → changed)

locals {
  tags = {
    Project = var.name_prefix
    Owner   = "fixdoc"
  }

  vpc_cidr    = var.policy_mode == "baseline" ? "10.130.0.0/16" : "10.131.0.0/16"
  subnet_cidr = var.policy_mode == "baseline" ? "10.130.1.0/24" : "10.131.1.0/24"
}

resource "aws_vpc" "main" {
  cidr_block           = local.vpc_cidr
  enable_dns_support   = true
  enable_dns_hostnames = true
  tags                 = merge(local.tags, { Name = "${var.name_prefix}-vpc" })
}

resource "aws_subnet" "public" {
  vpc_id                  = aws_vpc.main.id
  cidr_block              = local.subnet_cidr
  availability_zone       = "us-east-1a"
  map_public_ip_on_launch = true
  tags                    = merge(local.tags, { Name = "${var.name_prefix}-public" })
}

resource "aws_security_group" "app" {
  name   = "${var.name_prefix}-sg"
  vpc_id = aws_vpc.main.id

  ingress {
    from_port   = 22
    to_port     = 22
    protocol    = "tcp"
    cidr_blocks = [local.vpc_cidr]
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = merge(local.tags, { Name = "${var.name_prefix}-sg" })
}

resource "aws_instance" "app" {
  ami                    = "ami-12345678"
  instance_type          = "t3.micro"
  subnet_id              = aws_subnet.public.id
  vpc_security_group_ids = [aws_security_group.app.id]
  tags                   = merge(local.tags, { Name = "${var.name_prefix}-app" })
}

# Extra S3 bucket present in baseline, removed in changed → delete action in plan
resource "aws_s3_bucket" "extra" {
  count  = var.policy_mode == "baseline" ? 1 : 0
  bucket = "${var.name_prefix}-extra"
  tags   = merge(local.tags, { Name = "${var.name_prefix}-extra" })
}
