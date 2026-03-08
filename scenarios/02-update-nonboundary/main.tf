locals {
  base_tags = {
    Project = var.name_prefix
    Owner   = "fixdoc"
  }

  instance_tags = merge(local.base_tags, {
    Variant = var.variant
  })

  instance_type = var.variant == "baseline" ? "t3.micro" : "t3.small"
}

resource "aws_vpc" "main" {
  cidr_block           = "10.20.0.0/16"
  enable_dns_support   = true
  enable_dns_hostnames = true
  tags = merge(local.base_tags, { Name = "${var.name_prefix}-vpc" })
}

resource "aws_subnet" "public_a" {
  vpc_id                  = aws_vpc.main.id
  cidr_block              = "10.20.1.0/24"
  availability_zone       = "us-east-1a"
  map_public_ip_on_launch = true
  tags = merge(local.base_tags, { Name = "${var.name_prefix}-public-a" })
}

resource "aws_security_group" "app" {
  name   = "${var.name_prefix}-sg"
  vpc_id = aws_vpc.main.id

  ingress {
    from_port   = 22
    to_port     = 22
    protocol    = "tcp"
    cidr_blocks = ["10.20.0.0/16"] # internal only
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = merge(local.base_tags, { Name = "${var.name_prefix}-sg" })
}

resource "aws_instance" "app" {
  ami                    = "ami-12345678"
  instance_type          = local.instance_type
  subnet_id              = aws_subnet.public_a.id
  vpc_security_group_ids = [aws_security_group.app.id]

  tags = merge(local.instance_tags, { Name = "${var.name_prefix}-app" })
}