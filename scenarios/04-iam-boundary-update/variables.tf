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
  default = "fixdoc-iam"
}

# baseline: allow s3:GetObject
# changed:  deny s3:GetObject (simulate breaking change)
variable "policy_mode" {
  type    = string
  default = "baseline"
}