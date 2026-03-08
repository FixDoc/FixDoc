variable "aws_region" {
  type    = string
  default = "us-east-1"
}

variable "localstack_endpoint" {
  type    = string
  default = "http://localhost:4566"
}

variable "name_prefix" {
  type    = string
  default = "fixdoc-sg"
}

# baseline: internal-only CIDR
# changed:  0.0.0.0/0
variable "sg_exposure" {
  type    = string
  default = "baseline"
}