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
  default = "fixdoc-update"
}

# Baseline vs changed plan
# baseline: t3.micro + Tag "Variant=baseline"
# changed:  t3.small + Tag "Variant=changed"
variable "variant" {
  type    = string
  default = "baseline"
}