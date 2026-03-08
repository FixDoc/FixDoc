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
  default = "fixdoc-13"
}

# baseline: VPC CIDR 10.130.0.0/16, extra S3 bucket exists
# changed:  VPC CIDR 10.131.0.0/16 (immutable → forces replace + cascade),
#           extra S3 bucket removed (delete action)
variable "policy_mode" {
  type    = string
  default = "baseline"
}
