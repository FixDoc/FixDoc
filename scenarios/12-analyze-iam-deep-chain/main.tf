# Scenario 12: IAM Deep Chain — Multi-hop BFS Propagation
#
# A 4-resource IAM hierarchy:
#   aws_iam_role.app (changed: policy_mode=changed updates assume_role_policy)
#     ├── aws_iam_role_policy_attachment.app (depends on role + policy) ← depth 1
#     ├── aws_iam_instance_profile.app (depends on role)                ← depth 1
#     │   └── aws_instance.app (depends on profile)                     ← depth 2
#     └── aws_iam_policy.app (not changed in baseline→changed)
#
# When plan runs with policy_mode=changed:
#   - aws_iam_role.app gets "update" action (control point, criticality=0.9)
#   - BFS via reverse adjacency finds: profile, attachment (depth 1), instance (depth 2)
#
# Run with: run_apply_then_plan_update_boundary
# Toggle variable: policy_mode (baseline → changed updates the role trust policy)

locals {
  tags = {
    Project = var.name_prefix
    Owner   = "fixdoc"
  }

  # Baseline: only EC2 can assume the role
  # Changed: EC2 + Lambda can assume the role (role update in plan)
  principals = var.policy_mode == "baseline" ? ["ec2.amazonaws.com"] : ["ec2.amazonaws.com", "lambda.amazonaws.com"]
}

resource "aws_iam_policy" "app" {
  name = "${var.name_prefix}-policy"

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = ["s3:GetObject"]
      Resource = "arn:aws:s3:::${var.name_prefix}-*"
    }]
  })

  tags = local.tags
}

resource "aws_iam_role" "app" {
  name = "${var.name_prefix}-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = local.principals }
      Action    = "sts:AssumeRole"
    }]
  })

  tags = local.tags
}

resource "aws_iam_role_policy_attachment" "app" {
  role       = aws_iam_role.app.name
  policy_arn = aws_iam_policy.app.arn
}

resource "aws_iam_instance_profile" "app" {
  name = "${var.name_prefix}-profile"
  role = aws_iam_role.app.name
}

resource "aws_vpc" "main" {
  cidr_block           = "10.120.0.0/16"
  enable_dns_support   = true
  enable_dns_hostnames = true
  tags                 = merge(local.tags, { Name = "${var.name_prefix}-vpc" })
}

resource "aws_subnet" "public" {
  vpc_id                  = aws_vpc.main.id
  cidr_block              = "10.120.1.0/24"
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
    cidr_blocks = ["10.120.0.0/16"]
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
  iam_instance_profile   = aws_iam_instance_profile.app.name
  tags                   = merge(local.tags, { Name = "${var.name_prefix}-app" })
}
