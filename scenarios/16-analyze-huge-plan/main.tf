# Scenario 16: Huge Plan — 250 Resources, 5 Resource Types
#
# 50 instances of 5 resource types = 250 create actions in the plan.
# Tests:
#   - find_by_resource_type deduplication (5 unique types → exactly 5 DB calls)
#   - Blast score scaling with large resource counts
#   - Performance: `time fixdoc analyze plan.json --max-warnings 10` < 2s target
#
# Resource types:
#   1. aws_s3_bucket          (count=50)
#   2. aws_security_group     (count=50, needs VPC)
#   3. aws_iam_role           (count=50)
#   4. aws_iam_role_policy    (count=50, attached to roles)
#   5. aws_cloudwatch_log_group (count=50)
#
# Run with: run_plan (plan-only; no apply needed)

locals {
  tags = {
    Project = var.name_prefix
    Owner   = "fixdoc"
  }
}

resource "aws_vpc" "main" {
  cidr_block           = "10.160.0.0/16"
  enable_dns_support   = true
  enable_dns_hostnames = true
  tags                 = merge(local.tags, { Name = "${var.name_prefix}-vpc" })
}

resource "aws_s3_bucket" "bulk" {
  count  = 50
  bucket = "${var.name_prefix}-bulk-${count.index}"
  tags   = merge(local.tags, { Index = tostring(count.index) })
}

resource "aws_security_group" "bulk" {
  count  = 50
  name   = "${var.name_prefix}-sg-${count.index}"
  vpc_id = aws_vpc.main.id

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = merge(local.tags, { Index = tostring(count.index) })
}

resource "aws_iam_role" "bulk" {
  count = 50
  name  = "${var.name_prefix}-role-${count.index}"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "ec2.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })

  tags = merge(local.tags, { Index = tostring(count.index) })
}

resource "aws_iam_role_policy" "bulk" {
  count = 50
  name  = "${var.name_prefix}-policy-${count.index}"
  role  = aws_iam_role.bulk[count.index].id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = ["logs:CreateLogGroup", "logs:CreateLogStream"]
      Resource = "*"
    }]
  })
}

resource "aws_cloudwatch_log_group" "bulk" {
  count             = 50
  name              = "/fixdoc/${var.name_prefix}/bulk-${count.index}"
  retention_in_days = 7
  tags              = merge(local.tags, { Index = tostring(count.index) })
}
