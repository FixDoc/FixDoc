locals {
  tags = { Project = var.name_prefix, Owner = "fixdoc" }
}

resource "aws_vpc" "main" {
  cidr_block = "10.60.0.0/16"
  tags       = merge(local.tags, { Name = "${var.name_prefix}-vpc" })
}

# Invalid CIDR — AWS provider validates format during plan
resource "aws_security_group" "bad_cidr" {
  name        = "${var.name_prefix}-bad-cidr"
  description = "SG with intentionally invalid CIDR"
  vpc_id      = aws_vpc.main.id

  ingress {
    description = "Bad CIDR"
    from_port   = 80
    to_port     = 80
    protocol    = "tcp"
    cidr_blocks = ["10.0.0.0/32"]  # /33 is invalid
  }
  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
  tags = merge(local.tags, { Name = "${var.name_prefix}-bad-cidr" })
}

# Invalid JSON — AWS provider validates during plan
resource "aws_iam_role" "bad_policy" {
  name               = "${var.name_prefix}-bad-policy"
  assume_role_policy = jsonencode({
  Version = "2012-10-17"
  Statement = [
    {
      Action = "sts:AssumeRole"
      Effect = "Allow"
      Sid    = ""
      Principal = {
        Service = "ec2.amazonaws.com"
      }
    },
  ]
})
  tags               = local.tags
}
