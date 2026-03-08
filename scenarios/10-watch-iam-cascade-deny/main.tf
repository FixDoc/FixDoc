variable "aws_region" {
  type    = string
  default = "us-east-1"
}

variable "name_prefix" {
  type    = string
  default = "fixdoc-watch10"
}

# Provisioner role used by Terraform
resource "aws_iam_role" "provisioner" {
  name = "${var.name_prefix}-provisioner"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "ec2.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
  tags = { Project = var.name_prefix, Owner = "fixdoc" }
}

# After initial apply, attaching this policy creates a cascade deny on re-apply
resource "aws_iam_policy" "deny_all" {
  name        = "${var.name_prefix}-deny-all"
  description = "Explicit deny all — triggers cascade AccessDenied on re-apply"
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Deny"
      Action   = "*"
      Resource = "*"
    }]
  })
  tags = { Project = var.name_prefix, Owner = "fixdoc" }
}

resource "aws_iam_role_policy_attachment" "deny" {
  role       = aws_iam_role.provisioner.name
  policy_arn = aws_iam_policy.deny_all.arn
}

# These resources cascade-fail with AccessDenied / UnauthorizedOperation on re-apply
resource "aws_instance" "app" {
  ami           = "ami-0c02fb55956c7d316"  # Amazon Linux 2 us-east-1
  instance_type = "t3.micro"
  tags          = { Name = "${var.name_prefix}-app", Project = var.name_prefix }
}

resource "aws_s3_bucket" "data" {
  bucket = "${var.name_prefix}-data"
  tags   = { Name = "${var.name_prefix}-data", Project = var.name_prefix }
}
