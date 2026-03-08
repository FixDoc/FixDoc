locals {
  base_tags = {
    Project = var.name_prefix
    Owner   = "fixdoc"
  }

  ingress_cidr = var.sg_exposure == "baseline" ? "10.30.0.0/16" : "0.0.0.0/0"
}

resource "aws_vpc" "main" {
  cidr_block           = "10.30.0.0/16"
  enable_dns_support   = true
  enable_dns_hostnames = true
  tags = merge(local.base_tags, { Name = "${var.name_prefix}-vpc" })
}

resource "aws_subnet" "public_a" {
  vpc_id                  = aws_vpc.main.id
  cidr_block              = "10.30.1.0/24"
  availability_zone       = "us-east-1a"
  map_public_ip_on_launch = true
  tags = merge(local.base_tags, { Name = "${var.name_prefix}-public-a" })
}

resource "aws_security_group" "web" {
  name   = "${var.name_prefix}-sg-web"
  vpc_id = aws_vpc.main.id

  ingress {
    description = "SSH (test exposure)"
    from_port   = 22
    to_port     = 22
    protocol    = "tcp"
    cidr_blocks = [local.ingress_cidr]
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = merge(local.base_tags, { Name = "${var.name_prefix}-sg-web" })
}

resource "aws_instance" "app_a" {
  ami                    = "ami-12345678"
  instance_type          = "t3.micro"
  subnet_id              = aws_subnet.public_a.id
  vpc_security_group_ids = [aws_security_group.web.id]
  tags                   = merge(local.base_tags, { Name = "${var.name_prefix}-app-a" })
}

resource "aws_instance" "app_b" {
  ami                    = "ami-12345678"
  instance_type          = "t3.micro"
  subnet_id              = aws_subnet.public_a.id
  vpc_security_group_ids = [aws_security_group.web.id]
  tags                   = merge(local.base_tags, { Name = "${var.name_prefix}-app-b" })
}