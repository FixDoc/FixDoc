# Minimal resource — errors fire on missing vars before this is evaluated
resource "aws_instance" "app" {
  ami           = "ami-12345678"
  instance_type = "t3.micro"
  tags          = { Name = var.name_prefix, SubnetCidr = var.subnet_cidr }
}
