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
  default = "fixdoc-12"
}

# baseline: EC2 only can assume the role
# changed:  EC2 + Lambda can assume the role (triggers role update in plan)
variable "policy_mode" {
  type    = string
  default = "baseline"
}
