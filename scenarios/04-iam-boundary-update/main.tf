locals {
  tags = {
    Project = var.name_prefix
    Owner   = "fixdoc"
  }

  # Baseline: allow reading
  allow_s3_read_policy = {
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = ["s3:GetObject"]
      Resource = ["arn:aws:s3:::${var.name_prefix}-data/*"]
    }]
  }

  # Changed: explicit deny (represents a risky IAM change)
  deny_s3_read_policy = {
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Deny"
      Action   = ["s3:GetObject"]
      Resource = ["arn:aws:s3:::${var.name_prefix}-data/*"]
    }]
  }

  inline_policy_doc = var.policy_mode == "baseline" ? local.allow_s3_read_policy : local.deny_s3_read_policy
}

resource "aws_s3_bucket" "data" {
  bucket = "${var.name_prefix}-data"
  tags   = local.tags
}

resource "aws_iam_role" "app_role" {
  name = "${var.name_prefix}-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow"
      Principal = { Service = "ec2.amazonaws.com" }
      Action = "sts:AssumeRole"
    }]
  })

  tags = local.tags
}

# Inline policy we will UPDATE in the plan (most realistic for “oops IAM change”)
resource "aws_iam_role_policy" "inline" {
  name   = "${var.name_prefix}-inline"
  role   = aws_iam_role.app_role.id
  policy = jsonencode(local.inline_policy_doc)
}

resource "aws_iam_instance_profile" "profile" {
  name = "${var.name_prefix}-profile"
  role = aws_iam_role.app_role.name
}

resource "aws_vpc" "main" {
  cidr_block           = "10.40.0.0/16"
  enable_dns_support   = true
  enable_dns_hostnames = true
  tags = merge(local.tags, { Name = "${var.name_prefix}-vpc" })
}

resource "aws_subnet" "public_a" {
  vpc_id                  = aws_vpc.main.id
  cidr_block              = "10.40.1.0/24"
  availability_zone       = "us-east-1a"
  map_public_ip_on_launch = true
  tags = merge(local.tags, { Name = "${var.name_prefix}-public-a" })
}

resource "aws_security_group" "app" {
  name   = "${var.name_prefix}-sg"
  vpc_id = aws_vpc.main.id

  ingress {
    from_port   = 22
    to_port     = 22
    protocol    = "tcp"
    cidr_blocks = ["10.40.0.0/16"]
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
  subnet_id              = aws_subnet.public_a.id
  vpc_security_group_ids = [aws_security_group.app.id]

  # This is the “real” attachment: instance uses instance profile
  iam_instance_profile = aws_iam_instance_profile.profile.name

  tags = merge(local.tags, { Name = "${var.name_prefix}-app" })
}