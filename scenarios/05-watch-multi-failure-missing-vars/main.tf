locals {
  tags = { Project = var.name_prefix, Owner = "fixdoc" }
}

module "app_a" {
  source      = "./modules/app"
  name_prefix = "${var.name_prefix}-a"
  instance_type = "t3.micro"
  subnet_cidr = "default"
  # Intentionally omitting required vars: instance_type, subnet_cidr
}

module "app_b" {
  source      = "./modules/app"
  name_prefix = "${var.name_prefix}-b"
  instance_type = "t3.micro"
  subnet_cidr = "default"
  # Intentionally omitting required vars: instance_type, subnet_cidr
}
